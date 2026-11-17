"""SimCast's engine room: reads the sim (or the demo race), keeps the race tracker and the director going, and
holds what the window shows. Settings live in %APPDATA%\\SimCast\\config.json, the log in simcast.log next to it."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import deque

from .audio import BroadcastAudio, SimAudio
from .link import DUCK_LEVELS, SimCoachLink
from .demo import DemoRace
from .director import Director
from .memory import Memory
from .race import RaceTracker
from .solo import RallyTracker, SoloTracker, StageStore
from . import ai as ai_mod
from . import persona, providers, sims

APP_NAME = "SimCast"
APP_VERSION = "1.4"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SimCast")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "simcast.log")
_LOG_LOCK = threading.Lock()


def file_log(msg):
    """One line in %APPDATA%\\SimCast\\simcast.log (kept under 2 MB: the old one goes to simcast.log.old)."""
    try:
        with _LOG_LOCK:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + str(msg).rstrip() + "\n")
                big = fh.tell() > 2_000_000
            if big:
                os.replace(LOG_PATH, LOG_PATH + ".old")
    except Exception:
        pass

LOOP_HZ = 30
DEFAULTS = {
    "lead_name": "Ben", "analyst_name": "Kate", "pit_name": "Mia",
    "lead_voice": "en-GB-RyanNeural", "analyst_voice": "en-GB-SoniaNeural", "pit_voice": "en-AU-NatashaNeural",
    "tts": "on", "speech_rate": 0,
    "volume": 0.9, "audio_out": "", "simcoach_duck": "under",
    "chatter": "normal", "focus": "balanced", "pit_reporter": True, "ai_colour": True,
    "driver_name": "", "ai_provider": "openai", "ai_fallback": True,
    "openai_api_key": "", "gemini_api_key": "", "deepseek_api_key": "", "elevenlabs_api_key": "",
    "openai_model": "auto", "gemini_model": "auto", "deepseek_model": "auto", "openai_tts_model": "auto",
    "f1_port": 20777, "dr2_port": 20779, "udp_share_simhub": True, "game_setup": True, "guide_seen": False,
    "demo_laps": 8, "captions": False, "captions_pos": None, "window": None,
    "check_updates": True, "update_skip": "",
    "booth_tone": "broadcast", "swearing": False, "banter": "normal", "driver_pronouns": "auto",
    "ai_convo": "auto", "drive_mode": "stream", "race_sponsor": "",
    "memory": True, "roster_file": "", "obs_captions": False, "obs_port": 8686, "twitch_channel": "",
    "twitch_chat": True, "twitch_only_booth": False,
    "recap": True, "recap_offset": 0, "hotkey_skip": "", "hotkey_mute": "", "hotkey_me": "", "hotkey_mode": "", "my_sponsors": "", "driver_sponsors": "", "invent_sponsors": True,
}
STYLES = {   # the booth in one click; Custom is anything else
    "tv": {"chatter": "normal", "focus": "balanced", "booth_tone": "broadcast", "swearing": False, "banter": "normal"},
    "late": {"chatter": "chatty", "focus": "balanced", "booth_tone": "loose", "swearing": True, "banter": "lots"},
    "pub": {"chatter": "chatty", "focus": "me", "booth_tone": "unhinged", "swearing": True, "banter": "lots"},
}


def style_of(cfg):
    """The style the settings match, or 'custom'."""
    return next((k for k, v in STYLES.items() if all(cfg.get(x) == y for x, y in v.items())), "custom")


CHOICES = {"tts": ("on", "off"), "chatter": ("quiet", "normal", "chatty"),
           "focus": ("field", "balanced", "me"), "ai_provider": ("openai", "gemini", "deepseek"),
           "simcoach_duck": tuple(DUCK_LEVELS), "booth_tone": persona.TONES, "banter": persona.BANTER,
           "driver_pronouns": persona.PRONOUN_CHOICES, "ai_convo": providers.CONVO_CHOICES,
           "drive_mode": ("stream", "driver")}
SECRET_KEYS = ("openai_api_key", "gemini_api_key", "deepseek_api_key", "elevenlabs_api_key")
ROLE_LABEL = {"lead": "lead", "analyst": "analyst", "pit": "pit lane"}


def load_config(path=None):
    path = path or CONFIG_PATH
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            cfg.update({k: v for k, v in d.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Settings unreadable ({e}); using defaults.")
    return clean_config(cfg)


def clean_config(cfg):
    if cfg.get("tts") in ("edge", "kokoro", "google", "openai"):      # settings from SimCast 1.0
        cfg["tts"] = "on"
    for k, opts in CHOICES.items():
        if cfg.get(k) not in opts:
            cfg[k] = DEFAULTS[k]
    try:
        cfg["volume"] = max(0.0, min(1.0, float(cfg.get("volume", 0.9))))
    except (TypeError, ValueError):
        cfg["volume"] = 0.9
    try:
        cfg["speech_rate"] = max(-30, min(30, int(cfg.get("speech_rate", 0))))
    except (TypeError, ValueError):
        cfg["speech_rate"] = 0
    try:
        cfg["demo_laps"] = max(2, min(30, int(cfg.get("demo_laps", 8))))
    except (TypeError, ValueError):
        cfg["demo_laps"] = 8
    for k in ("lead_name", "analyst_name", "pit_name", "driver_name"):
        cfg[k] = " ".join(str(cfg.get(k) or "").split())[:40]
    for k in ("race_sponsor", "my_sponsors"):
        cfg[k] = " ".join(str(cfg.get(k) or "").split())[:120]
    cfg["driver_sponsors"] = str(cfg.get("driver_sponsors") or "").strip()[:2000]
    for k in ("swearing", "invent_sponsors"):
        cfg[k] = bool(cfg.get(k, DEFAULTS[k]))
    try:
        cfg["obs_port"] = max(1024, min(65535, int(cfg.get("obs_port") or 8686)))
    except (TypeError, ValueError):
        cfg["obs_port"] = 8686
    cfg["twitch_channel"] = str(cfg.get("twitch_channel") or "").strip()[:80]
    try:
        cfg["recap_offset"] = max(0, min(36000, int(cfg.get("recap_offset") or 0)))
    except (TypeError, ValueError):
        cfg["recap_offset"] = 0
    cfg["check_updates"] = bool(cfg.get("check_updates", True))
    cfg["update_skip"] = str(cfg.get("update_skip") or "")[:40]
    return cfg


def save_config(cfg, path=None):
    path = path or CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: cfg.get(k) for k in DEFAULTS}, fh, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Couldn't save settings: {e}")


class SimCast:
    def __init__(self, cfg=None, audio=None, log=print, booth=True, save=True):
        self.cfg = clean_config(dict(DEFAULTS, **(cfg or load_config())))
        self.save_enabled = save
        self.log = log
        sims._LOG[0] = log
        self.lines: deque = deque(maxlen=200)
        self.line_seq = 0                                  # numbers the lines, for the OBS captions page
        self.captions_server = None
        self.chat = None
        self.events_seen = 0
        self.stages = StageStore(os.path.join(APP_DIR, "stages.json") if save else None)
        self.mode = "field"
        self._mode_cand, self._mode_n = "field", 0
        self.tracker = RaceTracker(log=log)
        if audio is None:
            audio = BroadcastAudio(self.config, on_line=self._on_line, log=log, openai_client=self._openai_client,
                                   link=SimCoachLink(log=log))
        else:
            audio.on_line = self._on_line
        self.audio = audio
        self.booth = ai_mod.AIBooth(self.config, providers.ai_chat, providers._json_reply,
                                    label_fn=providers.ai_label, log=log,
                                    stream_fn=providers.ai_chat_stream) if booth else None
        # a second booth for the conversations, on its own provider (DeepSeek, say) and its own thread
        self.convo_booth = ai_mod.AIBooth(self.config, providers.convo_chat, providers._json_reply,
                                          label_fn=providers.convo_label, log=log) if booth else None
        if booth:
            providers.detect_in_background(self.cfg, log=log)
            from . import eleven
            eleven.fetch_in_background(self.cfg, log=log)
        self.director = Director(self.tracker, self.audio, self.config, booth=self.booth, log=log,
                                 convo_booth=self.convo_booth)
        self.memory = Memory(os.path.join(APP_DIR, "memory.json"), log=log) if save else None
        self.director.set_memory(self.memory)
        from .recap import Recap
        self.director.recap = Recap(os.path.join(APP_DIR, "Recaps"), log=log) if save else None
        self.status = "Waiting for a sim..."
        self.source = None
        self.demo = None
        self.readers = None
        self.last_frame_t = None
        self.lock = threading.RLock()
        self.sims = []                     # detect.scan(): the supported sims installed on this PC
        self.errors = deque(maxlen=5)

    # -------------------------------------------------------------- settings
    def config(self):
        return dict(self.cfg)

    def update_settings(self, changes):
        """From the Settings window. Unknown keys are ignored; key fields are only replaced when something's typed."""
        with self.lock:
            for k, v in (changes or {}).items():
                if k not in DEFAULTS:
                    continue
                if k in SECRET_KEYS and not str(v or "").strip():
                    continue
                if isinstance(DEFAULTS[k], bool):
                    v = bool(v)
                self.cfg[k] = v.strip() if isinstance(v, str) else v
            self.cfg = clean_config(self.cfg)
            if self.save_enabled:
                save_config(self.cfg)
        if self.captions_server is not None or self.chat is not None or any(
                k in (changes or {}) for k in ("obs_captions", "obs_port", "twitch_channel", "twitch_chat")):
            try:
                self.sync_stream()
            except Exception as e:
                self.log(f"Streaming: {e}")
        if self.booth is not None and (changes or {}).get("elevenlabs_api_key"):
            from . import eleven
            eleven.fetch_in_background(self.cfg, log=self.log)
        if self.booth is not None and any(k in SECRET_KEYS for k in (changes or {})):
            providers.detect_in_background(self.cfg, log=self.log)

    @staticmethod
    def _openai_client(cfg):
        return providers.openai_client(providers.ai_key(cfg, "openai"))

    # -------------------------------------------------------------- what's said
    def _on_line(self, line, t):
        cfg = self.config()
        name = cfg.get(f"{line.who}_name") or line.who
        self.line_seq += 1
        self.lines.append({"seq": self.line_seq, "who": line.who, "name": name, "text": line.text, "energy": line.energy,
                           "mood": getattr(line, "mood", ""),
                           "t": time.time()})
        self.director.heard(line, t)
        self.log(f"{name.upper():>6} | {line.text}")

    # -------------------------------------------------------------- frames in
    MODE_LABEL = {"field": "Whole field", "solo": "Your car only", "rally": "Rally stage"}

    def _make_tracker(self, mode):
        name = lambda: self.cfg.get("driver_name") or ""
        if mode == "rally":
            return RallyTracker(log=self.log, name_fn=name, store=self.stages)
        if mode == "solo":
            return SoloTracker(log=self.log, name_fn=name)
        return RaceTracker(log=self.log)

    def _pick_mode(self, frame):
        """Whole field when the sim shares every car, rally for stages, else the car being driven. A change has
        to hold for about a second (ACC's grid arrives a moment after the car, for one)."""
        mode = "rally" if frame.game in sims.RALLY_GAMES else ("field" if frame.field else "solo")
        if mode == self.mode:
            self._mode_n = 0
            return
        if mode != self._mode_cand:
            self._mode_cand, self._mode_n = mode, 0
        self._mode_n += 1
        if self._mode_n >= 30 or self.last_frame_t is None:
            self.mode = mode
            self.tracker = self._make_tracker(mode)
            self.director.set_tracker(self.tracker)
            self.log(f"Commentary mode: {self.MODE_LABEL[mode]}")

    def feed(self, frame, now):
        if frame is None:
            return
        with self.lock:
            self._pick_mode(frame)
        self.last_frame_t = now
        with self.lock:
            try:
                ev = self.tracker.update(frame, now)
            except Exception as e:
                self._error("Race tracker", e)
                return
            if ev:
                self.events_seen += len(ev)
                self.director.add(ev, now)
            self.director.recording = self.demo is None       # demo races aren't remembered
            try:
                self.director.driver_input(frame, now)
            except Exception as e:
                self._error("Driver mode", e)

    # -------------------------------------------------------------- streaming: OBS captions, Twitch chat
    def sync_stream(self):
        """Start or stop the OBS captions page and the Twitch chat reader to match the settings."""
        from . import stream
        cfg = self.config()
        want = bool(cfg.get("obs_captions"))
        srv = self.captions_server
        if srv is not None and (not want or srv.port != int(cfg.get("obs_port") or 8686)):
            srv.stop()
            self.captions_server = srv = None
        if want and srv is None:
            srv = stream.CaptionServer(lambda: list(self.lines), cfg.get("obs_port") or 8686, log=self.log)
            if srv.start():
                self.captions_server = srv
        if stream.clean_channel(cfg.get("twitch_channel")) and self.chat is None:
            self.chat = stream.TwitchChat(lambda: self.config().get("twitch_channel"),
                                          lambda: "!booth" if self.config().get("twitch_only_booth") else "",
                                          log=self.log)
            self.chat.start()
        self.director.chat = self.chat if cfg.get("twitch_chat", True) else None

    # -------------------------------------------------------------- hotkeys
    def toggle_air(self):
        d = self.director
        d.on_air = not d.on_air
        if not d.on_air:
            self.audio.interrupt(None)
        self.log("Booth " + ("on air" if d.on_air else "off air"))

    def hotkey(self, action, now=None):
        """A key or wheel button was pressed (hotkeys.py)."""
        now = time.monotonic() if now is None else now
        if action == "skip":
            self.audio.interrupt(None)
        elif action == "mute":
            self.toggle_air()
        elif action == "mode":
            new = "stream" if self.cfg.get("drive_mode") == "driver" else "driver"
            self.update_settings({"drive_mode": new})
            self.log("Mode: " + ("Driving" if new == "driver" else "Stream"))
        elif action == "me":
            with self.lock:
                self.director.talk_about_me(now)

    def tick(self, now):
        with self.lock:
            try:
                self.director.tick(now)
            except Exception as e:
                self._error("Director", e)

    def _error(self, where, e):
        msg = f"{where}: {type(e).__name__}: {e}"
        if not self.errors or self.errors[-1] != msg:
            self.errors.append(msg)
            self.log(msg)
            try:
                file_log(traceback.format_exc())
            except Exception:
                pass

    # -------------------------------------------------------------- demo
    def start_demo(self, laps=None):
        with self.lock:
            cfg = self.config()
            self.demo = DemoRace(laps=laps or cfg.get("demo_laps", 8), seed=int(time.time()) % 1000,
                                 player_name=cfg.get("driver_name") or "")
            self.mode = "field"
            self.tracker = self._make_tracker("field")
            self.director.set_tracker(self.tracker)
            self.director.reset()
            self.audio.interrupt(time.monotonic())
            self.status = "Demo race"
            self.source = "Demo"

    def stop_demo(self):
        with self.lock:
            if self.demo is not None:
                self.demo = None
                self.audio.interrupt(time.monotonic())
                self.tracker.reset()
                self.director.reset()
                self.status = "Waiting for a sim..."
                self.source = None

    # -------------------------------------------------------------- live loop
    def run(self, stop):
        """Read the demo or the first sim that's running, LOOP_HZ times a second, until stop is set."""
        active = None
        next_check = 0.0
        dt = 1.0 / LOOP_HZ
        last = time.monotonic()
        self.scan_sims(setup=True)
        while not stop.is_set():
            now = time.monotonic()
            step, last = now - last, now
            try:
                if self.demo is not None:
                    if active is not None:
                        active, self.readers = None, None
                    fr = self.demo.read(min(step, 0.25))
                    self.feed(fr, now)
                    if self.demo is not None and self.demo.finished and self.director.wrapped and \
                            not self.audio.speaking(now):
                        self.status = "Demo finished"
                else:
                    if active is None and now >= next_check:
                        next_check = now + 2.0
                        if self.readers is None:
                            self.readers = sims.available_readers(self.cfg, log=self.log)
                        active = next((r for r in self.readers if self._safe(r.connected)), None)
                        if active is None:
                            other = self._safe(sims.unsupported_sim)
                            self.status = f"{other[0]} isn't supported" if other else "Waiting for a sim..."
                        if active is not None:
                            self.log(f"Connected to {active.name}")
                            self.source = active.name
                            self.status = f"Live: {active.name}"
                            self.last_frame_t = None
                            self.tracker.reset()
                            self.director.reset()
                    if active is not None:
                        if not self._safe(active.connected):
                            self.log(f"{active.name} closed")
                            active, self.source, self.status = None, None, "Waiting for a sim..."
                        else:
                            fr = active.read()
                            if fr is not None:
                                self.status = f"Live: {fr.game}  ·  {self.MODE_LABEL[self.mode]}"
                            self.feed(fr, now)
                self.tick(now)
            except Exception as e:
                self._error("Loop", e)
                active = None
                stop.wait(1.0)
            rem = dt - (time.monotonic() - now)
            if rem > 0:
                stop.wait(rem)

    def scan_sims(self, setup=False):
        """Find the supported sims installed here; with setup, switch on what they need (only for those)."""
        from . import detect
        try:
            sims = detect.scan()
            self.log(detect.summary(sims))
            if setup and self.cfg.get("game_setup", True) and any(s.found for s in sims):
                from .gamesetup import setup_all
                if setup_all(log=self.log, sims=sims):
                    sims = detect.scan()                   # statuses after the changes
            self.sims = sims
        except Exception as e:
            self._error("Finding sims", e)
        return self.sims

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return False

    # -------------------------------------------------------------- what the window shows
    def state(self):
        with self.lock:
            return self._state()

    def _state(self):
        tr = self.tracker
        cfg = self.config()
        ld = tr.leader()
        lap = None
        if ld is not None and tr.session == "race":
            lap = min(ld.laps + 1, tr.race_laps) if tr.race_laps else ld.laps + 1
        standings = []
        for s in tr.standings():
            s = dict(s)
            s["call"] = self.director.writer.n(s["id"])
            standings.append(s)
        public = {k: v for k, v in cfg.items() if k in DEFAULTS and k not in SECRET_KEYS}
        public["keys"] = {k: bool(providers.ai_key(cfg, k.split("_")[0])) for k in SECRET_KEYS
                          if k.split("_")[0] in providers.AI_PROVIDERS}
        public["keys"]["elevenlabs_api_key"] = bool(str(cfg.get("elevenlabs_api_key") or "").strip())
        return {
            "app": APP_NAME, "version": APP_VERSION, "status": self.status, "source": self.source, "mode": self.mode,
            "session": tr.session, "track": tr.track, "lap": lap, "laps": tr.race_laps or None,
            "time_left": tr.time_left, "flag": tr.flag, "started": tr.started, "finished": tr.finished,
            "standings": standings, "lines": list(self.lines)[-40:], "on_air": self.director.on_air,
            "speaking": self.audio.speaking(time.monotonic()),
            "ai": self.booth.label() if self.booth else "",
            "ai_convo": (lambda c: c if c and c != (self.booth.label() if self.booth else "") else "")(
                self.convo_booth.label() if self.convo_booth else ""), "ai_error": self.booth.last_error if self.booth else "",
            "demo": self.demo is not None, "settings": public, "errors": list(self.errors),
            "voice_note": getattr(self.audio, "engine_note", ""),
            "simcoach": bool(getattr(self.audio, "link", None) and self.audio.link.connected()),
            "sims": [{"key": s.key, "name": s.name, "status": s.status, "ok": s.ok, "where": s.where}
                     for s in self.sims if s.found],
        }


def headless_run(laps=6, seed=3, cfg=None, booth=None, max_t=3600.0, log=print, events=True, convo_booth=None,
                 memory=None, recap=None):
    """A whole demo race on a simulated clock with silent audio: returns the SimCast (lines in .lines and
    .audio.spoken). Used by --test and the tests."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    audio = SimAudio()
    cast = SimCast(cfg, audio=audio, log=log, booth=False, save=False)
    if booth is not None:
        cast.booth = booth
        cast.director.booth = booth
        booth.clock = lambda: clock[0]
    if convo_booth is not None:
        cast.convo_booth = cast.director.convo_booth = convo_booth
        convo_booth.clock = lambda: clock[0]
    if memory is not None:
        cast.memory = memory
        cast.director.set_memory(memory)                  # the test race counts as a real one
    if recap is not None:
        cast.director.recap = recap
    import random
    cast.director.writer.rng = random.Random(seed)        # the same race, said the same way: tests repeat exactly
    demo = DemoRace(laps=laps, seed=seed, player_name=cfg.get("driver_name", ""), events=events)
    dt = 1.0 / LOOP_HZ
    clock = [0.0]
    while clock[0] < max_t:
        t = clock[0]
        cast.feed(demo.read(dt), t)
        cast.tick(t)
        audio.tick(t)
        if demo.finished and cast.director.wrapped and not audio.speaking(t):
            break
        clock[0] += dt
    cast.director.finish_recap(demo=True)
    return cast
