"""SimCast: the SimHub link (simhub.py): the values the plugin publishes, the server and its buttons, and the plugin
and dashboard files generated from it (tools/simhub/build_simhub.py)."""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile

from _common import REPO, check, done

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="simcast-test-")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools", "simhub"))
from simcast import app, hotkeys, simhub              # noqa: E402
import build_simhub                                   # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def request(port, method, path, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=host is not None)
    if host is not None:
        c.putheader("Host", host)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == "POST":
        c.putheader("Content-Length", "0")
    c.endheaders()
    r = c.getresponse()
    body = r.read().decode("utf-8")
    c.close()
    return r.status, body


# ----------------------------------------------------------------------------------------------- the values
empty = simhub.snapshot({}, {}, now=0.0)
check(all(v is not None and isinstance(v, (str, bool, int, float)) for v in empty.values()),
      "nothing running: every value is a plain string, number or true/false, never null")
check(empty["AirText"] == "OFF AIR" and empty["OnAir"] is False and empty["Line.Text"] == "",
      "nothing running: off air, no line")

cast = app.headless_run(laps=4, seed=5, log=lambda *a: None, max_t=120.0)
st = cast._state()
cfg = dict(cast.config(), hotkey_skip="key:F9", hotkey_me="joy:Fanatec Wheel#6", race_name="The 2026 Test 500")
st["source"] = "iRacing"
snap = simhub.snapshot(st, cfg, now=st["lines"][-1]["t"] + 1.0)
check(set(snap) == set(empty), "mid-race: the same values as with nothing running (the plugin's list is fixed)")
check(all(v is not None and isinstance(v, (str, bool, int, float)) for v in snap.values()),
      "mid-race: every value is plain, never null")
json.dumps(snap)
last = st["lines"][-1]
check(snap["Line.Text"] == last["text"] and snap["Line.Speaker"] == last["name"].upper(),
      f"the line being said: {snap['Line.Speaker']}: {snap['Line.Text'][:50]}")
check(snap["Line.Fresh"] and snap["Line.TextColor"] == simhub.WHITE, "a line just said is bright")
old = simhub.snapshot(st, cfg, now=last["t"] + 30.0)
check(not old["Line.Fresh"] and old["Line.TextColor"] == simhub.MUTED, "half a minute later it's dimmed")
rows = [snap[f"Line.Row{i}"] for i in range(1, simhub.ROWS + 1)]
check(all(len(r) <= simhub.WRAP for r in rows) and " ".join(r for r in rows if r).startswith(last["text"][:30]),
      "the line is wrapped into rows that fit")
long = simhub.wrap_rows("word " * 80)
check(len(long) == simhub.ROWS and long[-1].endswith("…"), "a line too long for the rows ends in ...")
check(snap["Line.Color"] == simhub.ROLE_COLOUR[last["who"]], "the speaker's colour")
check(snap["History1.Text"] and snap["History1.Speaker"] == st["lines"][-2]["name"].upper(),
      "the line before it, in the history")
check(snap["Tower01.Pos"] == "1" and snap["Tower01.Gap"] == "LEADER" and snap["Tower01.Show"],
      "the timing tower: the leader")
me = next(c for c in st["standings"] if c["player"])
check(snap["Me.Pos"] == me["pos"] and snap["Me.PosText"] == f"P{me['pos']}", f"your place: P{me['pos']}")
if me["pos"] <= simhub.TOWER:
    k = f"Tower{me['pos']:02d}."
    check(snap[k + "IsMe"] and snap[k + "RowColor"] == simhub.ME_ROW, "your row in the tower is yours")
check(snap["Title"] == "THE 2026 TEST 500" and snap["Subtitle"] == snap["Track"], "the race's name over the track")
check(snap["LapText"].startswith("LAP "), f"the lap: {snap['LapText']}")
check(snap["FlagText"] == "GREEN FLAG", "racing: green flag")
check(snap["Key.Skip"] == "F9" and snap["Key.Me"] == "Button 7" and snap["Key.Air"] == "—",
      "the hotkeys, short: F9, Button 7, a dash when unset")
check(simhub.gap_text({"laps_down": 2}) == "+2 LAPS" and simhub.gap_text({"in_pit": True}) == "PIT" and
      simhub.gap_text({"interval": 1.234}) == "+1.2", "gaps: +1.2, +2 LAPS, PIT")
st2 = dict(st, flag="sc", nascar=True)
check(simhub.snapshot(st2, cfg)["FlagText"] == "CAUTION", "NASCAR: a caution is a caution")
check(simhub.snapshot(dict(st, flag="sc"), cfg)["FlagText"] == "SAFETY CAR", "a road race: safety car")

# ----------------------------------------------------------------------------------------------- the server
pressed = []
port = free_port()
link = simhub.SimHubLink(lambda: st, lambda: cfg, pressed.append, port=port, log=lambda *a: None)
check(link.start(), "the link starts")
check(not link.connected(), "nobody has read it yet")
code, body = request(port, "GET", "/simhub/state")
check(code == 200 and json.loads(body)["Line.Text"] == last["text"], "GET /simhub/state: the snapshot")
check(link.connected(), "and now SimHub counts as reading it")
code, _ = request(port, "GET", "/simhub/state", host="evil.example.com")
check(code == 403, "a request for another host is refused (no DNS rebinding)")
code, _ = request(port, "POST", "/simhub/action/skip")
check(code == 403 and not pressed, "a button press without the plugin's header is refused (a web page can't)")
code, _ = request(port, "POST", "/simhub/action/skip", headers={"X-SimCast": "SimHub"}, host="evil.example.com")
check(code == 403 and not pressed, "a button press for another host is refused")
for act in hotkeys.ACTIONS:
    code, _ = request(port, "POST", f"/simhub/action/{act}", headers={"X-SimCast": "SimHub"})
    check(code == 200, f"POST /simhub/action/{act}")
check(pressed == list(hotkeys.ACTIONS), "each press reached SimCast")
code, _ = request(port, "POST", "/simhub/action/explode", headers={"X-SimCast": "SimHub"})
check(code == 404 and len(pressed) == len(hotkeys.ACTIONS), "an unknown action does nothing")
busy = simhub.SimHubLink(lambda: st, lambda: cfg, pressed.append, port=port, log=lambda *a: None)
check(not busy.start(), "a port in use is reported, not fatal")
link.stop()

# the real thing: SimCast's own engine behind the link
eng = app.SimCast(cfg={"simhub_port": free_port()}, audio=app.SimAudio(), booth=False, save=False)
eng.sync_stream()
check(eng.simhub_link is not None, "SimCast starts the link (on by default)")
before = eng.director.on_air
code, _ = request(eng.simhub_link.port, "POST", "/simhub/action/mute", headers={"X-SimCast": "SimHub"})
check(code == 200 and eng.director.on_air != before, "the plugin's On/off air button takes the booth off air")
code, body = request(eng.simhub_link.port, "GET", "/simhub/state")
check(json.loads(body)["OnAir"] == eng.director.on_air, "and the dashboard says so")
check(eng.state()["simhub"], "SimCast's window can say SimHub is reading the booth")
eng.update_settings({"simhub_link": False})
check(eng.simhub_link is None, "switched off in Settings: the link stops")
eng.update_settings({"simhub_link": True, "simhub_port": free_port()})
check(eng.simhub_link is not None, "switched back on, on a new port")
eng.simhub_link.stop()

# ----------------------------------------------------------------------------------------------- generated files
r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "simhub", "build_simhub.py"), "--check"],
                   capture_output=True, text=True)
check(r.returncode == 0, "the plugin's Keys.g.cs and the dashboard are up to date with simhub.py " + r.stdout.strip())
cs = open(build_simhub.KEYS_CS, encoding="utf-8").read()
check(all(f'"{k}"' in cs for k in empty), "the plugin publishes every value")
with open(build_simhub.DASH, encoding="utf-8") as fh:
    dash = json.load(fh)
check(dash["Version"] == 2 and dash["BaseWidth"] == build_simhub.W and len(dash["Screens"]) == 3,
      "the dashboard: Dash Studio format 2, three screens")
check(dash["Metadata"]["ScreenCount"] == 3.0 and dash["Metadata"]["InGameScreensIndexs"] == [0, 1, 2],
      "the dashboard's metadata matches its screens")
types, names = set(), []


def walk(items):
    for it in items:
        types.add(it["$type"])
        names.append(it["Name"])
        for k, b in it.get("Bindings", {}).items():
            if not (b["TargetPropertyName"] == k and b["Mode"] == 2 and b["Formula"]["Expression"]):
                bad.append(f"{it['Name']}.{k}")
        walk(it.get("Childrens", []))


bad = []
for s in dash["Screens"]:
    walk(s["Items"])
check(not bad, "every binding is a formula on its own property " + ", ".join(bad))
check(types <= {"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.%s, SimHub.Plugins" % t
                for t in ("Layer", "TextItem", "RectangleItem")}, "only Dash Studio's own layers, texts and rectangles")
check(len(names) == len(set(names)), "every item has its own name")
cs_actions = open(os.path.join(REPO, "simhub", "plugin", "SimCastPlugin", "SimCastPlugin.cs"), encoding="utf-8").read()
check(all(f'Send("{a}")' in cs_actions for a in hotkeys.ACTIONS), "the plugin has a SimHub action for every hotkey")
done()
