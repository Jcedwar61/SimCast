"""Hotkeys: a keyboard key or a button on the wheel for the things you want mid-race without reaching for the mouse.

- skip: stop the line being said
- mute: booth off air / back on air
- me:   talk about my race, now
- mode: switch between Driving and Stream mode

They work while the sim has the focus: keys are read with GetAsyncKeyState, wheel and button-box buttons through
SDL (pygame) with background input allowed, or Windows' own joystick API if SDL can't be used. A binding is
'key:F9' or 'joy:<device name>#<button>'. In Settings, Set waits for the next key or button pressed.
"""
from __future__ import annotations

import os
import sys
import threading
import time

ACTIONS = ("skip", "mute", "me", "mode")
ACTION_LABEL = {"skip": "Skip this line", "mute": "Booth on/off air", "me": "Talk about me", "mode": "Driving/Stream"}
POLL_S = 0.03

# Virtual-key codes worth binding: F-keys, letters, digits, the numeric keypad and the navigation block.
VK = {f"F{i}": 0x6F + i for i in range(1, 25)}
VK.update({chr(c): c for c in range(0x41, 0x5B)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"Num{d}": 0x60 + d for d in range(10)})
VK.update({"Num*": 0x6A, "Num+": 0x6B, "Num-": 0x6D, "Num.": 0x6E, "Num/": 0x6F, "Insert": 0x2D, "Delete": 0x2E,
           "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "Pause": 0x13, "ScrollLock": 0x91,
           "Space": 0x20, "Tab": 0x09, "Backspace": 0x08, "Enter": 0x0D, "Up": 0x26, "Down": 0x28, "Left": 0x25,
           "Right": 0x27})


def describe(binding):
    """'key:F9' -> 'F9'; 'joy:Fanatec Wheel#7' -> 'Fanatec Wheel, button 8'."""
    b = str(binding or "")
    if b.startswith("key:"):
        return b[4:]
    if b.startswith("joy:") and "#" in b:
        dev, btn = b[4:].rsplit("#", 1)
        try:
            return f"{dev}, button {int(btn) + 1}"
        except ValueError:
            return b
    return "not set"


# ----------------------------------------------------------------------------------------------- input
class WindowsInput:
    """What's held down right now: a set of bindings. Keyboard and every game controller, in the background."""

    def __init__(self, log=print):
        self.log = log
        self._user32 = None
        self._sdl = None
        self._sticks = []
        self._winmm = None
        self._next_scan = 0.0
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None
        self._start_sdl()
        if self._sdl is None:
            self._start_winmm()

    def _start_sdl(self):
        try:
            os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")   # read the wheel while the sim
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")            # has the focus
            import pygame
            pygame.display.init()
            pygame.joystick.init()
            self._sdl = pygame
            self._scan()
        except Exception as e:
            self._sdl = None
            self.log(f"Hotkeys: wheel buttons through SDL unavailable ({e}); trying Windows' joystick API")

    def _scan(self):
        pg = self._sdl
        pg.event.pump()
        sticks = []
        for i in range(pg.joystick.get_count()):
            try:
                j = pg.joystick.Joystick(i)
                j.init()
                sticks.append(j)
            except Exception:
                continue
        self._sticks = sticks
        self._next_scan = time.monotonic() + 5.0          # a wheel plugged in later is picked up

    def _start_winmm(self):
        try:
            import ctypes
            from ctypes import wintypes

            class JOYINFOEX(ctypes.Structure):
                _fields_ = [(n, wintypes.DWORD) for n in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos",
                                                          "dwRpos", "dwUpos", "dwVpos", "dwButtons",
                                                          "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2")]
            self._winmm = (ctypes.windll.winmm, JOYINFOEX, ctypes)
        except Exception:
            self._winmm = None

    def pressed(self):
        down = set()
        if self._user32 is not None:
            for name, vk in VK.items():
                if self._user32.GetAsyncKeyState(vk) & 0x8000:
                    down.add("key:" + name)
        if self._sdl is not None:
            try:
                if time.monotonic() >= self._next_scan:
                    self._scan()
                else:
                    self._sdl.event.pump()
                for j in self._sticks:
                    name = j.get_name().strip() or f"Controller {j.get_instance_id()}"
                    for b in range(j.get_numbuttons()):
                        if j.get_button(b):
                            down.add(f"joy:{name}#{b}")
            except Exception:
                pass
        elif self._winmm is not None:
            winmm, JOYINFOEX, ctypes = self._winmm
            for dev in range(16):
                info = JOYINFOEX()
                info.dwSize = ctypes.sizeof(JOYINFOEX)
                info.dwFlags = 0x80                       # JOY_RETURNBUTTONS
                if winmm.joyGetPosEx(dev, ctypes.byref(info)) != 0:
                    continue
                for b in range(32):
                    if info.dwButtons & (1 << b):
                        down.add(f"joy:Controller {dev + 1}#{b}")
        return down


# ----------------------------------------------------------------------------------------------- the listener
class Hotkeys:
    """Watches the bindings in the settings ('hotkey_skip', ...) and calls on_action(action) on each press."""

    def __init__(self, cfg_fn, on_action, log=print, source=None):
        self.cfg_fn, self.on_action, self.log = cfg_fn, on_action, log
        self.source = source
        self._down = set()
        self._learn = None                                # (callback, deadline) while Settings waits for a press
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        if self.source is None and sys.platform != "win32":
            return                                        # the input readers are Windows-only
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def learn(self, callback, timeout=10.0):
        """The next key or button pressed is handed to callback(binding) (None after timeout) instead of acting."""
        self._learn = (callback, time.monotonic() + timeout)

    def _run(self):
        if self.source is None:
            try:
                self.source = WindowsInput(log=self.log)
            except Exception as e:
                self.log(f"Hotkeys unavailable: {e}")
                return
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:
                self.log(f"Hotkeys: {e}")
            time.sleep(POLL_S)

    def step(self):
        """One poll: whatever went down since the last one fires its action (or answers a Set in Settings)."""
        now_down = set(self.source.pressed())
        fresh = now_down - self._down
        self._down = now_down
        if self._learn is not None:
            cb, deadline = self._learn
            if fresh:
                self._learn = None
                cb(sorted(fresh)[0])
            elif time.monotonic() > deadline:
                self._learn = None
                cb(None)
            return
        if not fresh:
            return
        cfg = self.cfg_fn()
        for action in ACTIONS:
            b = cfg.get(f"hotkey_{action}") or ""
            if b and b in fresh:
                self.on_action(action)
