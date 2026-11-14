"""SimCoach link: SimCast and SimCoach (the live voice coach) can run side by side without talking over each other.

While SimCoach's coach or crew can be heard, SimCoach sends a tiny "talking" packet on this PC (127.0.0.1, UDP
47317) every 100 ms, and "quiet" when it stops. SimCast listens and lowers the booth under the coach: quickly
when the coach starts, then gently back up once the coach has been quiet for a moment. Both keep talking; the
coach is simply on top, the way a TV broadcast drops the commentary under team radio. Nothing leaves the PC.

DuckGain is the volume curve on its own (no sockets), so it can be tested on a fake clock.
"""
from __future__ import annotations

import socket
import threading
import time

PORT = 47317
TALK, QUIET = b"SIMCOACH-VOICE 1 talk", b"SIMCOACH-VOICE 1 quiet"
HOLD = 0.5            # a "talking" packet counts for this long (they come every 100 ms)
HANG = 0.6            # after the coach stops, wait this long before coming back up (no pumping between lines)
ATTACK = 0.15         # seconds to drop to the lowered level
RELEASE = 0.8         # seconds to come back to full volume
DUCK_LEVELS = {"off": 1.0, "little": 0.6, "under": 0.3, "low": 0.12}   # booth volume while the coach talks
DUCK_LABELS = {"off": "Don't lower it", "little": "A little (60%)", "under": "Well under (30%)",
               "low": "Almost silent (12%)"}


class DuckGain:
    """How loud the booth should be right now (0-1, a multiplier on its volume)."""

    def __init__(self):
        self.talk_until = -1e9
        self.gain = 1.0
        self._t = None

    def talk(self, now):
        self.talk_until = max(self.talk_until, now + HOLD)

    def quiet(self, now):
        self.talk_until = min(self.talk_until, now)

    def coach_talking(self, now):
        return now < self.talk_until

    def value(self, now, level):
        """Moves the gain toward `level` while the coach talks, and back toward 1 afterwards (after HANG)."""
        level = max(0.0, min(1.0, float(level)))
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        if self.coach_talking(now):
            target, secs = level, ATTACK
        elif now - self.talk_until < HANG:
            return self.gain                         # hold: the coach is between lines
        else:
            target, secs = 1.0, RELEASE
        step = dt / secs if secs > 0 else 1.0
        if self.gain < target:
            self.gain = min(target, self.gain + step)
        else:
            self.gain = max(target, self.gain - step)
        return self.gain


class SimCoachLink:
    """Listens for SimCoach on this PC. gain(level) is the multiplier for the booth's volume right now."""

    def __init__(self, log=print, port=PORT, start=True):
        self.log, self.port = log, port
        self.duck = DuckGain()
        self.seen = 0.0                              # when SimCoach was last heard from (0: never)
        self._lock = threading.Lock()
        self.sock = None
        if start:
            threading.Thread(target=self._run, daemon=True, name="simcoach-link").start()

    def connected(self, now=None):
        """SimCoach is running (it says 'quiet' every 2 s even when it has nothing to say)."""
        now = time.monotonic() if now is None else now
        return self.seen > 0 and now - self.seen < 5.0

    def coach_talking(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            return self.duck.coach_talking(now)

    def gain(self, level, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            return self.duck.value(now, level)

    def handle(self, data, now=None):
        now = time.monotonic() if now is None else now
        if data not in (TALK, QUIET):
            return
        with self._lock:
            first = self.seen == 0
            self.seen = now
            (self.duck.talk if data == TALK else self.duck.quiet)(now)
        if first:
            self.log("SimCoach is running: the booth drops under the coach while it talks.")

    def _run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", self.port))
            s.settimeout(1.0)
            self.sock = s
        except OSError as e:
            self.log(f"SimCoach link unavailable (port {self.port}: {e}); the booth won't drop under the coach.")
            return
        while True:
            try:
                data, _ = s.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:                          # Windows reports a closed port on UDP as an error: ignore
                time.sleep(0.2)
                continue
            self.handle(data)
