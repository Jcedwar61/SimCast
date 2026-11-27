"""The SimHub link: SimCast's booth on a SimHub dashboard, and SimHub's buttons on the booth.

A small web server on this PC (127.0.0.1 only, port 8687 by default) that the SimCast plugin for SimHub reads
(simhub/plugin). The plugin turns each value here into a SimHub property (SimCastPlugin.Line.Text,
SimCastPlugin.OnAir, ...) for the SimCast Booth dashboard (simhub/dashboard) or any dashboard, and gives SimHub
five actions to bind to wheel buttons: skip the line, booth on/off air, talk about me, Driving/Stream and NASCAR.

GET  /simhub/state            the snapshot below, as JSON: flat, every value a string, number or true/false, never
                              null, and text already formatted, wrapped and cut to fit, so a dashboard formula is
                              just the property's name
POST /simhub/action/<name>    skip | mute | me | mode | nascar (hotkeys.ACTIONS). Needs the X-SimCast header, which
                              a web page can't send to another site without asking first, so a page open in your
                              browser can't press the buttons. Requests for any host but this PC are refused too.
"""
from __future__ import annotations

import json
import re
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import hotkeys
from .stream import local_host as _local_host

PORT = 8687
API = 1                                    # bumped if a value changes meaning; new values don't need it

ROLE_COLOUR = {"lead": "#FFF5A524", "analyst": "#FF6CB6FF", "pit": "#FFFF8A93"}     # SimCast's own (gui.WHO_COLOUR)
RED, GREEN, AMBER, GREY, WHITE = "#FFE5484D", "#FF3DD68C", "#FFF5A524", "#FF5D6570", "#FFECEEF1"
MUTED, CLEAR, ME_ROW, ROW = "#FF8B939E", "#00000000", "#FF2A1517", "#FF1A1D22"
FLAG = {"sc": ("SAFETY CAR", AMBER), "vsc": ("VIRTUAL SAFETY CAR", AMBER), "fcy": ("FULL COURSE YELLOW", AMBER)}
NASCAR_FLAG = {"sc": ("CAUTION", AMBER), "vsc": ("CAUTION", AMBER), "fcy": ("CAUTION", AMBER)}

ROWS = 4                                   # the line being said: up to four rows of ...
WRAP = 46                                  # ... this many characters
HISTORY = 6                                # earlier lines, one row each
HISTORY_CHARS = 72
TOWER = 10                                 # the timing tower
FRESH_S = 8.0                              # a line stays bright this long after it's said


def _s(v):
    return "" if v is None else str(v)


def _cut(text, n):
    t = " ".join(_s(text).split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def wrap_rows(text, width=WRAP, rows=ROWS):
    """The line in rows that fit the dashboard; the last row ends in ... when it doesn't all fit."""
    parts = textwrap.wrap(" ".join(_s(text).split()), width) or [""]
    if len(parts) > rows:
        parts = parts[:rows - 1] + [_cut(" ".join(parts[rows - 1:]), width)]
    return parts + [""] * (rows - len(parts))


def gap_text(car, leader=False):
    """'+1.2', '+1 LAP', 'PIT', 'LEADER' for the timing tower (the gap to the car ahead)."""
    if car.get("in_pit"):
        return "PIT"
    st = car.get("status")
    if st in ("finished",):
        return "FIN"
    if st in ("stopped", "dnf", "dsq", "out"):
        return "OUT"
    if leader:
        return "LEADER"
    down = car.get("laps_down") or 0
    if down:
        return f"+{down} LAP" + ("S" if down > 1 else "")
    iv = car.get("interval")
    return f"+{iv:.1f}" if isinstance(iv, (int, float)) and iv >= 0 else ""


def key_text(binding):
    """A hotkey, short enough for the dashboard: 'F9', 'Button 7' (a wheel or button box), or a dash when unset."""
    b = _s(binding)
    if b.startswith("joy:") and "#" in b:
        try:
            return f"Button {int(b.rsplit('#', 1)[1]) + 1}"
        except ValueError:
            pass
    return _cut(hotkeys.describe(b), 12) if b else "\u2014"


def lap_text(st):
    if st.get("finished"):
        return "FINISHED"
    tl = st.get("time_left")
    if st.get("lap") and st.get("laps"):
        return f"LAP {st['lap']} / {st['laps']}"
    if isinstance(tl, (int, float)) and tl > 0 and st.get("session") == "race":
        m, s = divmod(int(tl), 60)
        return f"{m}:{s:02d} LEFT"
    if st.get("lap"):
        return f"LAP {st['lap']}"
    return (_s(st.get("session")) or "").upper()


def snapshot(st, cfg, now=None):
    """SimCast.state() (and the settings) -> the flat values the dashboard shows. Every key is always there."""
    now = time.time() if now is None else now
    on_air, speaking = bool(st.get("on_air")), bool(st.get("speaking"))
    mode = _s(st.get("mode"))
    nascar = bool(st.get("nascar"))
    live = bool(st.get("source"))
    out = {
        "Api": API, "Version": _s(st.get("version")),
        "OnAir": on_air, "Speaking": speaking and on_air,
        "AirText": "ON AIR" if on_air else "OFF AIR", "AirColor": RED if on_air else GREY,
        "DriveMode": "Driving" if cfg.get("drive_mode") == "driver" else "Stream",
        "Coverage": {"field": "Whole field", "solo": "Your car only", "rally": "Rally stage", "nascar": "NASCAR",
                     "nascar_solo": "NASCAR, your car"}.get(mode, "") if live else "",
        "Nascar": nascar, "Live": live, "Sim": _s(st.get("source")),
        "Status": _cut(st.get("status"), 60),
        "Track": _cut(st.get("track"), 48),
        "RaceName": _cut(cfg.get("race_name") or st.get("real_race") or st.get("real_race_ready") or "", 48),
        "Title": "", "Subtitle": "",
        "Session": _s(st.get("session")).upper(),
        "Lap": int(st.get("lap") or 0), "Laps": int(st.get("laps") or 0), "LapText": lap_text(st),
        "AI": _cut(st.get("ai") or "Stock lines", 40), "AIError": bool(st.get("ai_error")),
        "SimCoach": bool(st.get("simcoach")),
    }
    # the strip under the tally: the race's name over the track, or the track over the sim, or what SimCast is doing
    if out["RaceName"]:
        out["Title"], out["Subtitle"] = out["RaceName"].upper(), out["Track"]
    elif out["Track"]:
        out["Title"], out["Subtitle"] = out["Track"].upper(), out["Sim"]
    else:
        out["Title"], out["Subtitle"] = "SIMCAST", out["Status"]
    flag = st.get("flag")
    if st.get("finished"):
        ft, fc = "CHEQUERED FLAG" if not nascar else "CHECKERED FLAG", WHITE
    elif flag:
        ft, fc = (NASCAR_FLAG if nascar else FLAG).get(flag, (_s(flag).upper(), AMBER))
    elif st.get("started") and st.get("session") == "race":
        ft, fc = "GREEN FLAG", GREEN
    else:
        ft, fc = "", CLEAR
    out.update({"FlagText": ft, "FlagColor": fc, "Caution": bool(flag)})

    names = {who: _s(cfg.get(f"{who}_name")) or who.title() for who in ROLE_COLOUR}
    for who, key in (("lead", "Lead"), ("analyst", "Analyst"), ("pit", "Pit")):
        out[f"Names.{key}"] = names[who]

    lines = list(st.get("lines") or [])
    cur = lines[-1] if lines else None
    age = max(0.0, now - float(cur.get("t") or now)) if cur else 999.0
    out.update({
        "Line.Seq": int(cur.get("seq") or 0) if cur else 0,
        "Line.Speaker": _s(cur.get("name")).upper() if cur else "",
        "Line.Role": _s(cur.get("who")) if cur else "",
        "Line.Color": ROLE_COLOUR.get(cur.get("who"), WHITE) if cur else MUTED,
        "Line.Text": _s(cur.get("text")) if cur else "",
        "Line.Mood": _s(cur.get("mood")) if cur else "",
        "Line.Age": round(min(age, 999.0), 1), "Line.Fresh": age < FRESH_S,
        "Line.TextColor": WHITE if age < FRESH_S else MUTED,
    })
    for i, row in enumerate(wrap_rows(cur.get("text") if cur else "")):
        out[f"Line.Row{i + 1}"] = row
    older = list(reversed(lines[:-1]))[:HISTORY]
    for i in range(HISTORY):
        ln = older[i] if i < len(older) else None
        out[f"History{i + 1}.Speaker"] = _s(ln.get("name")).upper() if ln else ""
        out[f"History{i + 1}.Text"] = _cut(ln.get("text"), HISTORY_CHARS) if ln else ""
        out[f"History{i + 1}.Color"] = ROLE_COLOUR.get(ln.get("who"), WHITE) if ln else CLEAR

    field = list(st.get("standings") or [])
    me = next((c for c in field if c.get("player")), None)
    out["Field.Count"] = len(field)
    for i in range(TOWER):
        c = field[i] if i < len(field) else None
        p = f"Tower{i + 1:02d}."
        out[p + "Show"] = c is not None
        out[p + "Pos"] = str(c.get("pos")) if c else ""
        out[p + "Name"] = _cut(c.get("call") or c.get("name"), 18).upper() if c else ""
        out[p + "Gap"] = gap_text(c, leader=i == 0) if c else ""
        out[p + "IsMe"] = bool(c and c.get("player"))
        out[p + "Battle"] = bool(c and c.get("battle"))
        out[p + "Fastest"] = bool(c and c.get("fastest"))
        out[p + "RowColor"] = (ME_ROW if c.get("player") else ROW) if c else CLEAR
        out[p + "NameColor"] = (RED if c.get("player") else WHITE) if c else WHITE
    idx = field.index(me) if me is not None else -1
    ahead = field[idx - 1] if idx > 0 else None
    behind = field[idx + 1] if 0 <= idx < len(field) - 1 else None
    out.update({
        "Me.Pos": int(me.get("pos") or 0) if me else 0,
        "Me.PosText": f"P{me.get('pos')}" if me else "",
        "Me.Ahead": _cut((ahead or {}).get("call") or (ahead or {}).get("name"), 18),
        "Me.GapAhead": gap_text(me) if me and ahead else "",
        "Me.Behind": _cut((behind or {}).get("call") or (behind or {}).get("name"), 18),
        "Me.GapBehind": gap_text(behind) if behind else "",
        "Me.Battle": bool(me and me.get("battle")), "Me.InPit": bool(me and me.get("in_pit")),
        "Me.Gained": (int(me["grid"]) - int(me["pos"])) if me and me.get("grid") and me.get("pos") else 0,
    })
    for act, key in (("skip", "Skip"), ("mute", "Air"), ("me", "Me"), ("mode", "Mode"), ("nascar", "Nascar")):
        b = cfg.get(f"hotkey_{act}") or ""
        out[f"Key.{key}"] = key_text(b)
    return out


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"      # on Windows it would let a second program take the port


class SimHubLink:
    """The server. state_fn() -> SimCast.state(), cfg_fn() -> the settings, action_fn(name) -> SimCast.hotkey."""

    def __init__(self, state_fn, cfg_fn, action_fn, port=PORT, log=print):
        self.state_fn, self.cfg_fn, self.action_fn, self.port, self.log = state_fn, cfg_fn, action_fn, int(port), log
        self.httpd = None
        self.last_read = None                   # time.monotonic() of the plugin's last read: is SimHub there?

    def connected(self, within=5.0):
        return self.last_read is not None and time.monotonic() - self.last_read < within

    def start(self):
        if self.httpd is not None:
            return True
        link = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if not _local_host(self.headers.get("Host")):
                    return self._send(403, {"error": "local only"})
                if self.path.split("?")[0] in ("/simhub/state", "/simhub/state/"):
                    link.last_read = time.monotonic()
                    try:
                        return self._send(200, snapshot(link.state_fn(), link.cfg_fn()))
                    except Exception as e:
                        return self._send(500, {"error": str(e)})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(min(n, 4096))
                if not _local_host(self.headers.get("Host")) or not self.headers.get("X-SimCast"):
                    return self._send(403, {"error": "SimCast's buttons only take requests from SimHub"})
                m = re.fullmatch(r"/simhub/action/([a-z]+)/?", self.path.split("?")[0])
                if not m or m.group(1) not in hotkeys.ACTIONS:
                    return self._send(404, {"error": "no such action", "actions": list(hotkeys.ACTIONS)})
                try:
                    link.action_fn(m.group(1))
                except Exception as e:
                    return self._send(500, {"error": str(e)})
                self._send(200, {"ok": True, "action": m.group(1)})
        try:
            self.httpd = _Server(("127.0.0.1", self.port), H)
        except OSError as e:
            self.log(f"SimHub link: port {self.port} is in use ({e}). Pick another in Settings > Stream.")
            self.httpd = None
            return False
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="simhub-link").start()
        self.log(f"SimHub link: http://127.0.0.1:{self.port}/simhub/state")
        return True

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
