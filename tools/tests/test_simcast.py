"""SimCast: the race tracker, the commentary booth, the AI booth (with a stand-in model), the web page's API and
the sim readers' whole-field data. Everything runs on a simulated clock with silent audio."""
import json
import os
import re
import struct
import sys

import tempfile

from _common import REPO, check, done

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="simcast-test-")
sys.path.insert(0, REPO)
from simcast import sims as sc                        # noqa: E402  (SimCast's own readers: no SimCoach)
from simcast import providers                         # noqa: E402
from simcast import ai as ai_mod                      # noqa: E402
from simcast.app import DEFAULTS, SimCast, headless_run    # noqa: E402
from simcast.audio import SimAudio                    # noqa: E402
from simcast.race import RaceTracker, call_name       # noqa: E402
from simcast.writer import gap_words, lap_time_words, ordinal_words, place  # noqa: E402

# ---------------------------------------------------------------- words
check(call_name("Max Verstappen") == "Verstappen", "call name: surname")
check(call_name("Sam Driver2") == "Driver", "call name: iRacing's trailing digits dropped")
check(call_name("Car 7") == "Car 7", "call name: a numbered car stays whole")
check(call_name("Carlos Sainz Jr.") == "Sainz", "call name: suffix skipped")
check(gap_words(0.5) == "half a second" and gap_words(0.3) == "three tenths", "gaps in tenths")
check(gap_words(1.4) == "one point four seconds" and gap_words(1.5) == "a second and a half", "gaps in seconds")
check(lap_time_words(69.23) == "one oh nine point two", f"lap time words ({lap_time_words(69.23)})")
check(lap_time_words(103.46) == "one forty-three point five", f"lap time words ({lap_time_words(103.46)})")
check(ordinal_words(21) == "twenty-first" and place(2) == "second" and place(12) == "twelfth", "ordinals")

# ---------------------------------------------------------------- tracker, hand-built field
L = 2000.0


def frame(cars, t, session="race", race_laps=10, flag=None):
    """cars: list of dicts with id, name, dist (laps), in_pit, speed, status."""
    order = sorted(cars, key=lambda c: -c["dist"])
    pos = {c["id"]: i + 1 for i, c in enumerate(order)}
    field = [sc.field_entry(c["id"], c["name"], "GT3", pos[c["id"]], int(c["dist"]) if c["dist"] >= 0 else 0,
                            c["dist"] % 1.0, c.get("in_pit", False), None, None, c.get("status"), None,
                            player=c["id"] == 2, speed=c.get("speed")) for c in cars]
    me = next(c for c in cars if c["id"] == 2)
    f = sc.Frame("TEST", "Test Ring", "GT3", int(me["dist"]), me["dist"] % 1.0, 150.0, 1, 0, 0, 4, 7000, 0, 0,
                 False, False, track_len=L, session=session, position=pos[2], cars=len(cars), race_laps=race_laps)
    f.field, f.flag = field, flag
    return f


def run(scenario, until, dt=1 / 30):
    """scenario(t) -> car list; returns all events."""
    tr = RaceTracker(log=lambda m: None)
    evs, t = [], 0.0
    while t < until:
        for e in tr.update(frame(scenario(t), t), t):
            evs.append((t, e))
        t += dt
    return tr, evs


def lapsf(t, lap_s, start=0.0):
    return max(0.0, t - 5.0) / lap_s + start           # stationary for 5 s on the grid, then go


def pass_scn(t):
    return [{"id": 0, "name": "Alex Front", "dist": lapsf(t, 60.0, 0.010), "speed": 0 if t < 5 else 120},
            {"id": 1, "name": "Blake Quick", "dist": lapsf(t, 57.0, 0.005), "speed": 0 if t < 5 else 126},
            {"id": 2, "name": "Me Myself", "dist": lapsf(t, 61.0, 0.0), "speed": 0 if t < 5 else 118}]


tr, evs = run(pass_scn, 200)
kinds = [e.kind for _, e in evs]
check("intro" in kinds and "start" in kinds, "race intro and start")
ov = [e for _, e in evs if e.kind == "overtake"]
check(len(ov) == 1 and ov[0].cars[:2] == (1, 0) and ov[0].data["lead"], f"the quicker car takes the lead ({[e.cars for e in ov]})")
check(tr.cars[1].interval is None and tr.cars[0].interval is not None and tr.cars[0].interval > 0,
      "intervals measured at the timing loops")
check(any(e.kind == "fastest_lap" and e.cars == (1,) for _, e in evs), "fastest lap to the quickest car")


def pit_scn(t):
    cars = pass_scn(t)
    # The leader dives into the pits on lap 2: slow through the lane, and the others go by.
    c0 = cars[0]
    if 70.0 <= t < 95.0:
        c0["in_pit"] = True
        c0["dist"] = lapsf(70.0, 60.0, 0.010) + (t - 70.0) / 400.0
        c0["speed"] = 60
    elif t >= 95.0:
        c0["dist"] = lapsf(70.0, 60.0, 0.010) + 25.0 / 400.0 + (t - 95.0) / 60.0
    cars[1]["dist"] = lapsf(t, 60.5, 0.005)             # no on-track pass in this one
    cars[1]["speed"] = 119
    return cars


tr, evs = run(pit_scn, 150)
kinds = [e.kind for _, e in evs]
check("pit_in" in kinds and "pit_out" in kinds, "pit stop in and out")
check(not [e for _, e in evs if e.kind == "overtake" and 0 in e.cars], "losing places in the pits is not an overtake")


def stop_scn(t):
    cars = pass_scn(t)
    cars[1]["dist"] = lapsf(t, 60.5, 0.005)
    if t > 80:                                          # car 0 stops on track for good
        cars[0]["dist"] = lapsf(80, 60.0, 0.010)
        cars[0]["speed"] = 0
    return cars


tr, evs = run(stop_scn, 110)
check(any(e.kind == "stopped" and e.cars == (0,) for _, e in evs), "a car stopped on track")
check(not [e for _, e in evs if e.kind == "overtake" and len(e.cars) > 1 and e.cars[1] == 0],
      "passing a stopped car is not an overtake")


tr2 = RaceTracker(log=lambda m: None)
tr2_ev = []
t = 0.0
while t < 5 + 61 * 3 + 10:
    tr2_ev += [e.kind for e in tr2.update(frame(pass_scn(t), t, race_laps=3), t)]
    t += 1 / 20
check("final_lap" in tr2_ev and "winner" in tr2_ev and "player_finish" in tr2_ev, "final lap, winner, player's finish")

# ---------------------------------------------------------------- whole demo race through the booth
cast = headless_run(laps=6, seed=5, log=lambda m: None)
spoken = [ln for _, ln in cast.audio.spoken]
text = " ".join(ln.text for ln in spoken)
check(not cast.errors, f"demo race: no errors ({list(cast.errors)})")
check(len(spoken) > 40, f"demo race: plenty said ({len(spoken)} lines)")
check(re.search(r"lights out|green flag|they.re away", text, re.I) is not None, "demo race: the start is called")
check(re.search(r"wins|winner|takes the win", text, re.I) is not None, "demo race: the winner is called")
check("Sam Carter" in text and re.search(r"crossing the line|chequered flag in", text) is not None, "demo race: the player's finish")
mentioned = {c.call for c in cast.tracker.cars.values() if c.call in text}
check(len(mentioned) >= len(cast.tracker.cars) - 1,
      f"demo race: the whole field gets airtime ({len(mentioned)}/{len(cast.tracker.cars)})")
check(not re.search(r"\b(he|she|his|her|him)\b", text, re.I), "demo race: drivers named, never gendered")
dupes = [t for t in set(ln.text for ln in spoken) if sum(1 for ln in spoken if ln.text == t) > 2]
check(not dupes, f"demo race: no line said over and over ({dupes[:2]})")
whos = {ln.who for ln in spoken}
check({"lead", "analyst", "pit"} <= whos, f"demo race: lead, analyst and pit reporter all speak ({whos})")
starts = [t for t, ln in cast.audio.spoken]
check(all(b >= a for a, b in zip(starts, starts[1:])), "demo race: lines never overlap")

# the booth talks to itself: exchanges alternate, and the quiet spells are a back-and-forth
turns = 0
for (_, one), (_, two) in zip(cast.audio.spoken, cast.audio.spoken[1:]):
    if {one.who, two.who} == {"lead", "analyst"}:
        turns += 1
check(turns >= 20, f"demo race: the two commentators trade lines ({turns} hand-overs)")
floor = [ln.who for ln in spoken if ln.who in ("lead", "analyst")]
longest = run_len = 1
for a, b in zip(floor, floor[1:]):
    run_len = run_len + 1 if a == b else 1
    longest = max(longest, run_len)
check(longest <= 3, f"demo race: no talker holds the floor for long ({longest} in a row)")
check(sum(1 for one, two in zip(floor, floor[1:]) if one == "lead" and two == "analyst") >= 15,
      "demo race: the analyst answers the lead")

# the booth follows stories: a thread is opened, worked over a few exchanges, then paid off and dropped
from simcast.writer import FLOOR_MAX, Story, Writer  # noqa: E402


def quiet_spell(tracker, seed=5):
    """Run a race on the booth's filler alone, with the clock stepped on, as quiet spells do."""
    w = Writer(tracker, {"chatter": "normal"}, seed=seed)
    out, t = [], 0.0
    while t < 260:
        lines = w.filler(t, "balanced")
        for ln in lines:
            out.append((t, ln))
            w.heard(ln)                     # as the audio does, line by line, as each one goes out
        w.book(lines)
        t += 4.0
    return w, out


trq = RaceTracker(log=lambda m: None)
told = None
t = 0.0
while t < 240:
    trq.update(frame(pass_scn(t), t, race_laps=8), t)
    told = told or next((k for k, bt in trq.battles.items() if bt.get("told")), None)
    t += 1 / 20
w, said = quiet_spell(trq)
check(len(said) > 35, f"stories: the booth keeps talking through a quiet race ({len(said)} lines)")
check(any(st.kind == "battle" for st in w.stories.values()) or said, "stories: a battle thread gets opened")
fl = [ln.who for _, ln in said if ln.who in ("lead", "analyst")]
best = run = 1
for a, b in zip(fl, fl[1:]):
    run = run + 1 if a == b else 1
    best = max(best, run)
check(best <= FLOOR_MAX, f"stories: no voice holds the floor ({best} in a row)")
distinct = len(set(ln.text for _, ln in said))
check(distinct >= 18, f"stories: the booth keeps finding new things to say ({distinct} of {len(said)}, "
                      "three cars with a fixed gap are a hard case)")
check(all(w.stories[k].last >= w.stories[k].since for k in w.stories), "stories: beats move a thread forward")

# a thread that has gone stale is dropped rather than left half-finished
w.stories["parked"] = Story("battle", ("battle", tuple()), 0.0, 0.0)
w._last_prune = -1e9
w._prune(400.0)
check("parked" not in w.stories, "stories: a stale thread is pruned")
w.stories["kept"] = Story("battle", ("battle", tuple()), 380.0, 380.0)
w._last_prune = -1e9
w._prune(400.0)
check("kept" in w.stories, "stories: a live thread is kept")
w.forget()
check(not w.stories, "stories: forget() drops everything for a new session")

# a fight already called out on air isn't opened again straight after
w = Writer(trq, {}, seed=1)
pair = told or (0, 1)
w.covered(("battle", pair), 100.0)
check(w._threaded("battle", pair, 101.0), "stories: a fight just called is not reopened at once")
check(not w._threaded("battle", pair, 200.0), "stories: and it is free again later")

# the booth remembers its calls: a prediction settled by the racing gets the credit (or the stick)
from simcast.writer import ANALYST, LEAD  # noqa: E402
w = Writer(trq, {}, seed=2)
w._call((0, 1), ANALYST, 1, 100.0)
w._call((0, 1), LEAD, 0, 100.0)
st = w._settle(1, 0, 150.0)
check(len(st) == 2 and st[0].who == LEAD and st[1].who == ANALYST and "Kate" in st[0].text,
      f"calls: the one who got it wrong hands it over to the one who got it right ({[l.text for l in st]})")
check(not w.calls, "calls: a settled call is off the record")
w._call((0, 1), ANALYST, 1, 100.0)
trq.cars[0].pit_in_t, saved = 160.0, trq.cars[0].pit_in_t
check(w._settle(1, 0, 170.0) == [], "calls: a fight decided by a pit stop gets nobody any credit")
trq.cars[0].pit_in_t = saved
w._call((0, 1), LEAD, 0, 100.0)
w.lane_times[0] = 30.0
w.forget()
check(w.calls and w.lane_times, "calls: the flag puts conversations on hold but the booth remembers what it said")
w.clear_memory()
check(not w.calls and not w.lane_times, "calls: a new session starts with a clean slate")
task, opener = ai_mod.filler_task(w, 0.0, "balanced", shape="debate")
check(opener == ANALYST and "disagrees" in task, "AI booth: a debate is opened by the analyst and disputed")

# the pit reporter is part of the conversation, and never talked to when switched off
cast = headless_run(laps=8, seed=7, log=lambda m: None)
sp = [ln for _, ln in cast.audio.spoken]
check(any(ln.who in ("lead", "analyst") and "Mia" in ln.text for ln in sp),
      "pit lane: the booth talks to the pit reporter by name")
check(any(ln.who == "pit" and re.search(r"\b(Ben|Kate)\b", ln.text) for ln in sp),
      "pit lane: the pit reporter talks back to the booth")
check(any(ln.who == "pit" and re.search(r"slower than|quickest stop|off the best stop|identical", ln.text) for ln in sp),
      "pit lane: stops are timed and compared")
check(not [ln.text for ln in sp if re.search(r"[.!?] [a-z]", ln.text)], "every sentence starts with a capital")
cast = headless_run(laps=8, seed=7, cfg={"pit_reporter": False}, log=lambda m: None)
sp = [ln for _, ln in cast.audio.spoken]
check(not [ln.text for ln in sp if "Mia" in ln.text or ln.who == "pit"],
      "pit lane: with the reporter switched off, nobody throws to them")

# side conversations: the booth drifts into them between stories, never the same one twice in a race
asides = []
for sd in (3, 7, 11):
    cast = headless_run(laps=8, seed=sd, log=lambda m: None)
    asides.append(set(cast.director.writer.asides_done))
check(sum(len(a) for a in asides) >= 3, f"asides: side conversations come up in a race ({asides})")
check(all(len(a) <= 11 for a in asides), "asides: each one at most once per race")

# ---------------------------------------------------------------- the AI booth with a stand-in model
calls = []


def fake_chat(cfg, messages, max_tokens=300, timeout=20.0):
    calls.append(messages)
    task = messages[-1]["content"]
    if "keep talking" in task:
        return json.dumps({"lines": [{"who": "analyst", "text": f"AI-MORE {len(calls)}: oh, come off it.",
                                      "energy": 2, "mood": "frustrated"},
                                     {"who": "lead", "text": f"AI-MORE {len(calls)}: I'm serious!", "mood": "nonsense"}]})
    if "quiet moment" in task:
        return json.dumps({"lines": [{"who": "lead", "text": "AI-FILLER: the gaps are tumbling at the front.", "energy": 1},
                                     {"who": "analyst", "text": "AI-FILLER: and the pace is there.", "energy": 1}]})
    if "race is over" in task:
        return '```json\n{"lines": [{"who": "lead", "text": "AI-WRAP: what a race.", "energy": 1}]}\n```'
    if "carry the conversation" in task.lower() or "has already called it" in task:
        return json.dumps({"lines": [{"who": "analyst", "text": "AI-REACT: that changes everything.", "energy": 2},
                                     {"who": "nobody", "text": "x" * 500}]})
    return "not json at all"


booth = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="test", ai_colour=True), fake_chat, providers._json_reply,
                       log=lambda m: None)
booth.sync = True
cast = headless_run(laps=5, seed=5, booth=booth, log=lambda m: None)
said = [ln.text for _, ln in cast.audio.spoken]
check(any(t.startswith("AI-REACT") for t in said), "AI booth: the analyst reacts to big moments")
check(any(t.startswith("AI-FILLER") for t in said), "AI booth: quiet spells become conversation")
check(any(t.startswith("AI-WRAP") for t in said), "AI booth: the sign-off (from a fenced reply)")
check(not any("xxxx" in t for t in said), "AI booth: an overlong line is dropped")
prompt = calls[0][0]["content"] + calls[0][1]["content"]
check("STANDINGS" in prompt and "Only state facts" in prompt and "never a guess" in prompt,
      "AI booth: the prompt carries the race and the rules")
check(re.search(r"\[Moretti\]", prompt) is not None, "AI booth: drivers listed with their call names")
check("Answer what the other one just said" in prompt and "disagree" in prompt and "CALLS ON RECORD" in prompt,
      "AI booth: the prompt asks for a conversation, not two monologues")
check("throws down" in prompt and "Back to you" in prompt, "AI booth: the pit reporter is part of the conversation")

# the booth talks to itself: an exchange alternates, whoever opens it
ex = ai_mod.parse_lines(json.dumps({"lines": [{"who": "lead", "text": "a"}, {"who": "lead", "text": "b"},
                                               {"who": "lead", "text": "c"}]}), providers._json_reply)
check([ln.who for ln in ex] == ["lead", "analyst", "lead"], f"AI booth: the lead's lines are answered ({[l.who for l in ex]})")
ex = ai_mod.parse_lines(json.dumps({"lines": [{"who": "analyst", "text": "a"}, {"who": "analyst", "text": "b"}]}),
                        providers._json_reply, opener="analyst")
check([ln.who for ln in ex] == ["analyst", "lead"], "AI booth: an analyst-led exchange is answered too")
ex = ai_mod.parse_lines(json.dumps({"lines": [{"who": "pit", "text": "p"}, {"who": "lead", "text": "a"},
                                               {"who": "analyst", "text": "b"}, {"who": "lead", "text": "c"}]}),
                        providers._json_reply, opener="analyst")
check([ln.who for ln in ex] == ["pit", "analyst", "lead", "analyst"],
      f"AI booth: pit-lane lines keep their place, the rest alternate ({[l.who for l in ex]})")

react = [m[-1]["content"] for m in calls if "has just said on air" in m[-1]["content"]]
check(react and "Kate answers" in react[0] and "Two lines" in react[0],
      "AI booth: the reaction task hands over from the lead's line to the analyst")


def broken_chat(cfg, messages, max_tokens=300, timeout=20.0):
    raise RuntimeError("network down")


booth = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="test"), broken_chat, providers._json_reply, log=lambda m: None)
booth.sync = True
cast = headless_run(laps=4, seed=5, booth=booth, log=lambda m: None)
said = " ".join(ln.text for _, ln in cast.audio.spoken)
check("that's the result here" in said and not cast.errors, "AI booth down: the built-in booth carries on, wrap-up included")
check(booth.failures >= 3 and booth.disabled_until > 0, "AI booth down: it backs off instead of retrying every few seconds")

# ---------------------------------------------------------------- readers: the whole field
# rFactor 2 / LMU VehicleScoringInfoV01
v = bytearray(584)
struct.pack_into("<i", v, 0, 7)
v[4:4 + 10] = b"Jo Racer\0\0"
v[36:36 + 9] = b"bmw_m4_gt3"[:9]
struct.pack_into("<h", v, 100, 4)
v[103] = 0
struct.pack_into("<d", v, 104, 1500.0)
struct.pack_into("<d", v, 144, 98.4)
struct.pack_into("<d", v, 168, 99.1)
struct.pack_into("<h", v, 192, 1)
v[198], v[199] = 0, 3
v[200:203] = b"GT3"
fe = sc._RF2Family._field([bytes(v)], 4000.0, 7)
check(fe and fe[0]["name"] == "Jo Racer" and fe[0]["position"] == 3 and fe[0]["laps"] == 4 and
      abs(fe[0]["pct"] - 0.375) < 1e-6 and fe[0]["best"] == 98.4 and fe[0]["last"] == 99.1 and fe[0]["player"]
      and fe[0]["cls"] == "GT3", f"rFactor 2 / LMU field ({fe})")

# AMS2
b = bytearray(20700)
struct.pack_into("<i", b, 24, 2)
for k, (nm, pos, laps, dist) in enumerate((("Ann Lead", 1, 5, 900.0), ("Bo Second", 2, 5, 600.0))):
    ob = 28 + k * 100
    b[ob] = 1
    b[ob + 1:ob + 1 + len(nm)] = nm.encode()
    struct.pack_into("<f", b, ob + 80, dist)
    struct.pack_into("<II", b, ob + 84, pos, laps)
    struct.pack_into("<f", b, 9200 + 4 * k, 101.5)
    struct.pack_into("<f", b, 8944 + 4 * k, 100.2)
    struct.pack_into("<I", b, 9776 + 4 * k, 2 if k == 1 else 0)
fe = sc._ams2_field(bytes(b), 2, 0, 3000.0)
check(len(fe) == 2 and fe[0]["name"] == "Ann Lead" and fe[1]["in_pit"] and not fe[0]["in_pit"] and
      fe[0]["last"] == 101.5 and abs(fe[1]["pct"] - 0.2) < 1e-6, f"AMS2 field ({fe})")

# F1 event packet
f1 = sc.F1Reader.__new__(sc.F1Reader)
f1.events = []
hdr = bytearray(29)
for code, tail in ((b"OVTK", bytes([3, 7])), (b"COLL", bytes([1, 2])), (b"SCAR", bytes([1, 0])),
                   (b"FTLP", bytes([4]) + struct.pack("<f", 88.1)), (b"PENA", bytes([1, 2, 5, 255, 5, 3, 0]))):
    f1._event(bytes(hdr) + code + tail + bytes(8))
kinds = [(e["type"], e.get("car"), e.get("other")) for e in f1.events]
check(("overtake", 3, 7) in kinds and ("collision", 1, 2) in kinds and ("fastest_lap", 4, None) in kinds and
      any(e["type"] == "safety_car" and e["kind"] == "sc" and e["phase"] == "deployed" for e in f1.events) and
      any(e["type"] == "penalty" and e["car"] == 5 and e["seconds"] == 5 for e in f1.events), f"F1 events ({kinds})")

# ---------------------------------------------------------------- settings
cast = SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False)
cast.update_settings({"chatter": "chatty", "openai_api_key": "sk-secret-123456", "bogus": 1})
check(cast.cfg["chatter"] == "chatty" and "bogus" not in cast.cfg, "settings: change, unknown keys ignored")
cast.update_settings({"chatter": "shouty", "volume": 7, "openai_api_key": ""})
check(cast.cfg["chatter"] == "normal" and cast.cfg["volume"] == 1.0, "settings: bad values fall back or clamp")
check(cast.cfg["openai_api_key"] == "sk-secret-123456", "settings: an empty key box keeps the saved key")
st = cast.state()
check("sk-secret" not in json.dumps(st) and st["settings"]["keys"]["openai_api_key"],
      "settings: keys never shown back, only 'set'")
cast.start_demo(3)
check(cast.demo is not None and cast.source == "Demo", "demo race starts")
cast.stop_demo()
check(cast.demo is None and not cast.source, "demo race stops")
for asset in ("icon-256.png", "icon-48.png", "logo-40.png", "hero-car-240.png", "helmet-32.png"):
    check(os.path.getsize(os.path.join(REPO, "simcast", "assets", asset)) > 500, f"asset {asset}")

# ---------------------------------------------------------------- stands alone
import ast  # noqa: E402
for root, _d, files in os.walk(os.path.join(REPO, "simcast")):
    for fn in files:
        if fn.endswith(".py"):
            tree = ast.parse(open(os.path.join(root, fn), encoding="utf-8").read())
            mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
                   {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            check("sim_coach" not in mods, f"stands alone: {fn} doesn't import sim_coach")
check("sim_coach" not in sys.modules, "stands alone: SimCoach never loaded")

# ---------------------------------------------------------------- AI: the best model, picked automatically
class _M:
    def __init__(self, i, c):
        self.id, self.created = i, c


class _FakeClient:
    def __init__(self, ids):
        self.ids = ids
        self.models = self

    def with_options(self, **kw):
        return self

    def list(self):
        return [_M(i, c) for i, c in self.ids]


real_client = providers.openai_client
cfg = dict(DEFAULTS, openai_api_key="k-openai", gemini_api_key="k-gemini")
check(providers.ai_model(cfg, "openai") == providers.PREFERRED["openai"][0], "AI: before asking, the first preferred model")
providers.openai_client = lambda key, base=None: _FakeClient(
    [("gpt-5.6-luna", 1), ("gpt-6.1-terra", 2), ("gpt-6-luna-tts", 3), ("gpt-4o", 0), ("text-embedding-3", 1)]
    if key == "k-openai" else [("models/gemini-3.1-pro-preview", 1), ("models/gemini-3.5-flash-lite", 2)])
providers.detect_models(cfg, "openai")
providers.detect_models(cfg, "gemini")
check(providers.ai_model(cfg, "openai") == "gpt-6.1-terra", "AI: OpenAI picks the best model this key can use")
check(providers.ai_model(cfg, "gemini") == "gemini-3.1-pro-preview", "AI: Gemini too (ids without 'models/')")
check(providers.tts_model(cfg) == "gpt-6-luna-tts", "AI: the newest OpenAI speech model is picked for OpenAI voices")
check("gpt-4o" not in providers.model_choices(cfg, "openai") and "text-embedding-3" not in providers.model_choices(cfg, "openai"),
      "AI: retired and non-chat models aren't offered")
check(providers.ai_model(dict(cfg, openai_model="gpt-5.6-luna"), "openai") == "gpt-5.6-luna", "AI: a model picked by hand wins")
providers.openai_client = real_client
check(providers.ai_model(dict(cfg, deepseek_api_key="k"), "deepseek") == "deepseek-chat", "AI: DeepSeek's default")

# ---------------------------------------------------------------- solo mode (one car) and rally mode
from simcast.solo import StageStore  # noqa: E402


def drive(fn, until, cfg=None):
    a = SimAudio()
    c = SimCast(dict(DEFAULTS, game_setup=False, **(cfg or {})), audio=a, log=lambda m: None, booth=False, save=False)
    c.stages = StageStore(None)
    t = 0.0
    while t < until:
        f = fn(t)
        if f is not None:
            c.feed(f, t)
        c.tick(t)
        a.tick(t)
        t += 1 / 30
    return c, " ".join(ln.text for _, ln in a.spoken)


def solo_frame(t):
    lap = 80.0
    laps = int(max(0, t - 6) // lap)
    pct = (max(0, t - 6) % lap) / lap
    pos = 6 if t < 100 else 5
    f = sc.Frame("AC EVO", "Imola", "Ferrari 296 GT3", laps, pct, 0 if t < 6 else 150, 1, 0, 0, 4, 7000, pct * lap,
                 lap if laps else 0.0, False, False, track_len=4900.0, session="race", position=pos, cars=20,
                 race_laps=4, gap_ahead=2.0, gap_behind=1.5)
    f.car_ahead, f.car_behind = {"name": "Alex Rivera"}, {"name": "Kim Bauer"}
    f.race_over = laps >= 4
    return f


c, said = drive(solo_frame, 340, {"driver_name": "Sam Carter"})
check(c.mode == "solo" and not c.errors, f"solo: one car, its own mode ({c.mode}, {list(c.errors)})")
check("following Sam Carter" in said and re.search(r"past Rivera", said) and
      re.search(r"(chequered flag|crossing the line) in fifth", said),
      "solo: the intro, a place gained past the car ahead, the finish")
check(said.count("two seconds behind Rivera") <= 2, "solo: doesn't keep repeating the same running position")
check("Fastest lap of the race" not in said and " wins" not in said, "solo: never calls a 'race winner' or 'fastest lap' it can't know")
solo_whos = [ln.who for _, ln in c.audio.spoken]
check(sum(1 for a, b in zip(solo_whos, solo_whos[1:]) if a == "lead" and b == "analyst") >= 2,
      "solo: the booth is a conversation, the analyst answers the lead")

L = 5000.0


def stage_frame(t):
    starts, durs = [3, 160], [150, 140]
    runs = sum(1 for st, d in zip(starts, durs) if t >= st + d)
    for st, d in zip(starts, durs):
        if st <= t < st + d:
            el = t - st
            return sc.Frame("EA WRC", "Monte Carlo - Col de Turini", "Hyundai i20 N Rally1", runs, el / d,
                            130.0, 1, 0, 0, 4, 6000, el, durs[runs - 1] if runs else 0.0, False, False, track_len=L)
    return sc.Frame("EA WRC", "Monte Carlo - Col de Turini", "Hyundai i20 N Rally1", runs, 0.99999 if runs else 0.0,
                    0, 0, 0, 0, 0, 900, 0.0, durs[runs - 1] if runs else 0.0, False, False, track_len=L)


c, said = drive(stage_frame, 320, {"driver_name": "Sam Carter"})
check(c.mode == "rally" and not c.errors, f"rally: its own mode ({c.mode})")
check("Col de Turini" in said and re.search(r"away|Go!|off the line", said), "rally: the stage and the launch")
check("halfway split" in said and "up on the best" in said, "rally: splits against the best run on the second run")
check("new best on this stage by ten seconds" in said, "rally: a new stage best, by how much")
check(c.stages.get("EA WRC|Monte Carlo - Col de Turini|Hyundai i20 N Rally1")["best"] == 140.0, "rally: the best run is kept")

# ---------------------------------------------------------------- ACC's broadcasting API
from simcast.acc_broadcast import ACCBroadcast, read_config  # noqa: E402


def s_(x):
    b = x.encode()
    return struct.pack("<H", len(b)) + b


def lap(ms):
    return struct.pack("<iHHB", ms, 0, 0, 0) + bytes(4)


bc = ACCBroadcast(tempfile.mkdtemp(), log=lambda m: None)
bc.handle(bytes([1]) + struct.pack("<iBB", 7, 1, 1) + s_(""))
check(bc.conn_id == 7, "ACC: registered")
for idx, first, lastn, num in ((3, "Ana", "Silva", 88), (5, "Ben", "Okoro", 7)):
    bc.handle(bytes([6]) + struct.pack("<HB", idx, 1) + s_("Team") + struct.pack("<iBBHB", num, 0, 0, 1, 1) +
              s_(first) + s_(lastn) + s_(lastn[:3].upper()) + struct.pack("<BH", 0, 1))
bc.handle(bytes([2]) + struct.pack("<HHBBffi", 0, 0, 10, 5, 600000.0, 3600000.0, 5) + s_("set") + s_("cam") + s_("hud")
          + bytes([0]) + struct.pack("<fBBBBB", 50000.0, 20, 25, 0, 0, 0) + lap(101000))
for idx, pos, spline, loc in ((3, 1, 0.42, 1), (5, 2, 0.40, 2)):
    bc.handle(bytes([3]) + struct.pack("<HHBBfffBHHHHfHi", idx, 0, 1, 4, 0, 0, 0, loc, 180, pos, pos, pos, spline, 3, 0)
              + lap(101500) + lap(102000) + lap(50000))
bc.handle(bytes([7, 4]) + s_("Accident") + struct.pack("<ii", 1000, 5))
fe = sorted(bc.field(sc.field_entry), key=lambda e: e["position"])
check([e["name"] for e in fe] == ["Ana Silva", "Ben Okoro"] and fe[1]["in_pit"] and fe[1]["player"] and fe[0]["laps"] == 3
      and fe[0]["last"] == 102.0 and bc.session["type"] == "race", f"ACC: the grid from the broadcasting API ({fe})")
check(bc.events() == [{"type": "accident", "car": 5}], "ACC: the game's accident event")
check(read_config(tempfile.mkdtemp()) == (9000, "asd"), "ACC: default port and password when there's no file")

# ---------------------------------------------------------------- game setup
from simcast import gamesetup  # noqa: E402
docs = tempfile.mkdtemp()
wrc = os.path.join(docs, "My Games", "WRC", "telemetry")
os.makedirs(os.path.join(wrc, "udp"))
json.dump({"schema": 1, "udp": {"packets": [{"structure": "wrc", "packet": "session_update", "ip": "127.0.0.1",
                                             "port": 20777, "bEnabled": True}]}}, open(os.path.join(wrc, "config.json"), "w"))
dr2 = os.path.join(docs, "My Games", "DiRT Rally 2.0", "hardwaresettings")
os.makedirs(dr2)
open(os.path.join(dr2, "hardware_settings_config.xml"), "w").write(
    '<hardware_settings_config>\n <motion_platform>\n  <udp enabled="false" extradata="0" ip="127.0.0.1" port="20777" '
    'delay="1" />\n </motion_platform>\n</hardware_settings_config>\n')
accdir = os.path.join(docs, "Assetto Corsa Competizione", "Config")
os.makedirs(accdir)
open(os.path.join(accdir, "broadcasting.json"), "wb").write(
    json.dumps({"updListenerPort": 0, "connectionPassword": "pw", "commandPassword": ""}).encode("utf-16"))
done_ = gamesetup.setup_all(log=lambda m: None, documents=docs)
check(set(done_) == {"EA WRC", "DiRT Rally 2.0", "ACC"}, f"game setup: all three set up ({done_})")
c2 = json.load(open(os.path.join(wrc, "config.json")))
check(any(p["port"] == 20790 and p["structure"] == "simcast" and p["bEnabled"] for p in c2["udp"]["packets"]) and
      any(p["structure"] == "wrc" for p in c2["udp"]["packets"]), "game setup: EA WRC sends SimCast's packet, SimHub's kept")
st_ = json.load(open(os.path.join(wrc, "udp", "simcast.json")))
check(st_["packets"][0]["channels"][0] == "packet_uid" and len(st_["packets"][0]["channels"]) == len(sc.WRC_CHANNELS),
      "game setup: EA WRC packet layout matches the reader")
x = open(os.path.join(dr2, "hardware_settings_config.xml")).read()
check('enabled="true"' in x and 'extradata="3"' in x and os.path.exists(os.path.join(dr2, "hardware_settings_config.xml.simcast-backup")),
      "game setup: DiRT Rally 2.0 UDP on with extradata 3, backed up")
check(read_config(docs) == (9000, "pw"), "game setup: ACC broadcasting port on, password kept")
check(gamesetup.setup_all(log=lambda m: None, documents=docs) == [], "game setup: a second run changes nothing")

# ---------------------------------------------------------------- voices, guide, licences
from simcast.audio import VOICES, engine_of  # noqa: E402
check(engine_of("kokoro:bm_george") == ("kokoro", "bm_george") and engine_of("en-GB-RyanNeural")[0] == "edge" and
      engine_of("openai:ash") == ("openai", "ash") and engine_of("google:en-GB") == ("google", "en-GB"), "voices: engines")
fams = {engine_of(v)[0] for _, v in VOICES}
check(fams == {"edge", "kokoro", "google", "openai"}, "voices: Microsoft, Kokoro, Google and OpenAI offered")
from simcast.guide import TOPICS  # noqa: E402
gtext = " ".join(t for _, t in TOPICS)
for word in ("Play on", "Demo race", "Personality", "Random chat", "Sponsors", "Call me", "Captions window", "install-simcast-wine.sh", "Automatic", "CrewChiefEx",
             "EA SPORTS WRC", "DiRT Rally 2.0", "AC Rally", "AC EVO", "ACC", "iRacing", "F1 25", "Le Mans Ultimate",
             "rFactor 2", "Automobilista 2", "RaceRoom", "Assetto Corsa"):
    check(word in gtext, f"guide covers: {word}")
lic = os.path.join(REPO, "simcast", "licenses")
notice = open(os.path.join(lic, "THIRD_PARTY_NOTICES.txt")).read()
check("PolyForm Noncommercial License" in open(os.path.join(lic, "LICENSE.txt")).read() and "The Go Authors" in notice and
      all(n in notice for n in ("kokoro-onnx", "ONNX Runtime", "gTTS", "uv ")), "licences: SimCast PolyForm Noncommercial and the notices")

# ---------------------------------------------------------------- installed sims, found without starting them
from simcast import detect  # noqa: E402
pc = tempfile.mkdtemp()
steam, lib2, docs2 = os.path.join(pc, "Steam"), os.path.join(pc, "Games", "SteamLibrary"), os.path.join(pc, "Docs")
for d in (os.path.join(steam, "steamapps", "common"), os.path.join(lib2, "steamapps", "common"), docs2):
    os.makedirs(d, exist_ok=True)
open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w").write(
    '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n "1"\n {\n  "path"  "%s"\n }\n}\n'
    % (steam.replace("\\", "\\\\"), lib2.replace("\\", "\\\\")))
for lib, appid in ((steam, 805550), (lib2, 2399420)):
    open(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"), "w").write('"AppState" { }')
os.makedirs(os.path.join(lib2, "steamapps", "common", "assettocorsa", "apps", "python", "CrewChiefEx"))
os.makedirs(os.path.join(docs2, "Assetto Corsa", "cfg"))
open(os.path.join(docs2, "Assetto Corsa", "cfg", "python.ini"), "w").write("[HELICORSA]\nACTIVE=1\n\n[CREWCHIEFEX]\nACTIVE=0\n")
f1dir = os.path.join(docs2, "My Games", "F1 25", "hardwaresettings")
os.makedirs(f1dir)
open(os.path.join(f1dir, "hardware_settings_config.xml"), "w").write(
    '<hardware_settings_config>\n <motion>\n  <udp enabled="false" broadcast="false" ip="127.0.0.1" port="20777" '
    'sendRate="20" format="2025" yourTelemetry="restricted" />\n </motion>\n</hardware_settings_config>\n')
libs = detect.steam_libraries([steam])
check(len(libs) == 2, f"sims found: both Steam libraries from libraryfolders.vdf ({libs})")
found = {x.key: x for x in detect.scan(libraries=libs, documents=docs2) if x.found}
check(set(found) == {"acc", "lmu", "ac", "f1"}, f"sims found: by app id, install folder and settings folder ({sorted(found)})")
check(not found["ac"].ok and "CrewChiefEx" in found["ac"].status, "sims found: AC says the CrewChiefEx app is off")
check(not found["f1"].ok and "UDP" in found["f1"].status, "sims found: F1 25 says its UDP telemetry is off")
check(found["lmu"].ok and "whole field" in found["lmu"].status, "sims found: Le Mans Ultimate is ready")
did = gamesetup.setup_all(log=lambda m: None, documents=docs2, sims=list(found.values()))
check(set(did) == {"F1 25", "Assetto Corsa"}, f"sims set up: only what's installed and needs it ({did})")
again = {x.key: x for x in detect.scan(libraries=libs, documents=docs2) if x.found}
check(again["ac"].ok and again["f1"].ok and again["acc"].ok,
      f"sims set up: AC, F1 25 and ACC ready after setup ({[(k, x.status) for k, x in again.items() if not x.ok]})")
ini = open(os.path.join(docs2, "Assetto Corsa", "cfg", "python.ini")).read()
check("[HELICORSA]\nACTIVE=1" in ini and "[CREWCHIEFEX]\nACTIVE=1\n" in ini, "sims set up: CrewChiefEx on, other AC apps untouched")
check('udp enabled="true"' in open(os.path.join(f1dir, "hardware_settings_config.xml")).read() and
      'format="2025"' in open(os.path.join(f1dir, "hardware_settings_config.xml")).read(), "sims set up: F1 25 UDP on, format kept")
check("Found 4" in detect.summary(list(found.values())), "sims found: a summary for the log")
# CrewChief installed, but its CrewChiefEx app not yet put into AC: SimCast copies it over and switches it on
cc_home = tempfile.mkdtemp(prefix="simcast-cc-")
cc_app = os.path.join(cc_home, "Britton IT Ltd", "CrewChiefV4", "plugins", "assettocorsa", "apps", "python", "CrewChiefEx")
os.makedirs(cc_app)
open(os.path.join(cc_app, "CrewChiefEx.py"), "w").write("# CrewChiefEx\n")
os.environ["ProgramFiles(x86)"] = cc_home
check(gamesetup.crewchief_dirs() and gamesetup.crewchiefex_source() == cc_app, "CrewChief: its CrewChiefEx app found")
ac3 = tempfile.mkdtemp(prefix="simcast-ac-")
os.makedirs(os.path.join(ac3, "apps", "python"))
docs3 = tempfile.mkdtemp(prefix="simcast-docs-")
os.makedirs(os.path.join(docs3, "Assetto Corsa", "cfg"))
check(gamesetup.setup_crewchiefex(docs3, ac3, log=lambda m: None) and
      os.path.isfile(os.path.join(ac3, "apps", "python", "CrewChiefEx", "CrewChiefEx.py")) and
      gamesetup.crewchiefex_active(docs3), "CrewChief: SimCast installs CrewChiefEx into AC and switches it on")
check(not gamesetup.setup_crewchiefex(docs3, ac3, log=lambda m: None), "CrewChief: nothing to do the second time")
os.environ.pop("ProgramFiles(x86)", None)
check(os.path.exists(os.path.join(REPO, "SimCast.ico")), "SimCast.ico in the repo")

# ---------------------------------------------------------------- SimCoach link: the booth drops under the coach
import socket  # noqa: E402
import time  # noqa: E402
from simcast import link as lk  # noqa: E402

d = lk.DuckGain()
check(d.value(0.0, 0.3) == 1.0, "SimCoach link: full volume when the coach is quiet")
d.talk(1.0)
g = [d.value(1.0 + i * 0.03, 0.3) for i in range(8)]
check(g[-1] == 0.3 and g[1] < 1.0, f"SimCoach link: drops to the lowered level within {lk.ATTACK}s ({g[-1]:.2f})")
for i in range(10):                                     # the coach keeps talking: packets every 100 ms
    d.talk(1.3 + i * 0.1)
    held = d.value(1.3 + i * 0.1, 0.3)
check(held == 0.3, "SimCoach link: stays down while the coach talks")
d.quiet(2.3)
check(d.value(2.3 + lk.HANG * 0.8, 0.3) == 0.3, "SimCoach link: holds down for a moment between the coach's lines")
check(0.3 < d.value(2.3 + lk.HANG + 0.3, 0.3) < 1.0, "SimCoach link: then comes back up gently")
check(d.value(2.3 + lk.HANG + lk.RELEASE + 0.5, 0.3) == 1.0, "SimCoach link: back to full volume")
d2 = lk.DuckGain()
d2.talk(0.0)
check(d2.value(0.0, 0.3) == 1.0 and d2.value(0.5, 1.0) == 1.0, "SimCoach link: 'don't lower it' leaves the volume alone")

msgs = []
port = None
for p_ in range(47400, 47500):
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", p_))
        probe.close()
        port = p_
        break
    except OSError:
        continue
L = lk.SimCoachLink(log=msgs.append, port=port)
for _ in range(50):
    if L.sock is not None:
        break
    time.sleep(0.02)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
check(not L.connected() and not L.coach_talking(), "SimCoach link: nothing heard before SimCoach starts")
tx.sendto(lk.TALK, ("127.0.0.1", port))
for _ in range(50):
    if L.coach_talking():
        break
    time.sleep(0.02)
check(L.connected() and L.coach_talking(), "SimCoach link: a 'talking' packet from SimCoach is heard over UDP")
check(any("SimCoach is running" in m for m in msgs), "SimCoach link: says once that SimCoach was found")
tx.sendto(b"something else", ("127.0.0.1", port))
tx.sendto(lk.QUIET, ("127.0.0.1", port))
for _ in range(50):
    if not L.coach_talking():
        break
    time.sleep(0.02)
check(not L.coach_talking(), "SimCoach link: 'quiet' ends it; other packets are ignored")
# the wire format (SimCoach's docs/simcast-link.md): read leniently so a newer SimCoach still lowers the booth
check(lk.TALK == b"SIMCOACH-VOICE 1 talk" and lk.QUIET == b"SIMCOACH-VOICE 1 quiet"
      and lk.parse(lk.TALK) == (1, "talk") and lk.parse(lk.QUIET) == (1, "quiet"), "SimCoach link: version 1 packets")
check(lk.parse(b"SIMCOACH-VOICE 2 talk") == (2, "talk") and lk.parse(b"SIMCOACH-VOICE 1 quiet src=crew\n")
      == (1, "quiet"), "SimCoach link: a newer version or extra words still understood")
check(all(lk.parse(b) is None for b in (b"", b"SIMCOACH-VOICE", b"SIMCOACH-VOICE 1", b"SIMCOACH-VOICE x talk",
                                       b"SIMCOACH-VOICE 0 talk", b"SIMCOACH-VOICE -1 talk", b"SIMCOACH-VOICE 1 shout",
                                       b"SIMCOACH-VOICE2 1 talk", b"something else", b"\xff\xfe")),
      "SimCoach link: anything else ignored")
L.handle(b"SIMCOACH-VOICE 3 talk crew", now=time.monotonic())
check(L.coach_talking(), "SimCoach link: a version 3 'talk' lowers the booth")
L.handle(b"SIMCOACH-VOICE 3 quiet", now=time.monotonic())
check(not L.coach_talking(), "SimCoach link: and its 'quiet' brings it back")
newer = []
L2 = lk.SimCoachLink(log=newer.append, start=False)
L2.handle(b"SIMCOACH-VOICE 2 quiet")
check(L2.connected() and any("newer link (version 2" in m for m in newer), "SimCoach link: a newer SimCoach is noted once")
check(DEFAULTS["simcoach_duck"] == "under" and set(lk.DUCK_LEVELS) == set(lk.DUCK_LABELS),
      "SimCoach link: lowered to 30% by default, every level has a label")
check(SimCast(cfg={"simcoach_duck": "nonsense"}, audio=SimAudio(), booth=False, save=False).config()["simcoach_duck"]
      == "under", "SimCoach link: a bad setting falls back to the default")
# ---------------------------------------------------------------- Kokoro model shared with SimCoach
import urllib.request  # noqa: E402
from simcast import audio as audio_mod  # noqa: E402
from simcast.app import APP_DIR  # noqa: E402

ka = audio_mod.BroadcastAudio.__new__(audio_mod.BroadcastAudio)
ka.log, ka._kokoro_dl, ka._kokoro_retry = (lambda m: None), True, 0.0
_kd, kmodel, kvoices = ka._kokoro_paths()
coach_k = os.path.join(os.path.dirname(APP_DIR), "SimCoach", "kokoro")
os.makedirs(coach_k, exist_ok=True)


def sparse(path, size):
    with open(path, "wb") as fh:
        fh.truncate(size)


fetched = []


def no_net(req, timeout=None):
    fetched.append(req.full_url)
    raise OSError("no network in this test")


real_urlopen, urllib.request.urlopen = urllib.request.urlopen, no_net
ka._kokoro_download()
check(len(fetched) == 1 and not os.path.exists(kmodel), "Kokoro: no SimCoach copy, so it downloads as before")
sparse(os.path.join(coach_k, "kokoro-v1.0.int8.onnx"), 60_000_000)
sparse(os.path.join(coach_k, "voices-v1.0.bin"), 27_000_000)
fetched.clear()
ka._kokoro_download()
check(fetched == [] and os.path.getsize(kmodel) == 60_000_000 and os.path.getsize(kvoices) == 27_000_000
      and not os.path.exists(kmodel + ".part"), "Kokoro: SimCoach's model files used, nothing downloaded")
os.remove(kvoices)
sparse(os.path.join(coach_k, "voices-v1.0.bin"), 1000)
fetched.clear()
ka._kokoro_download()
check(not os.path.exists(kvoices) and fetched and fetched[0].endswith("voices-v1.0.bin"),
      "Kokoro: a cut-short SimCoach copy is downloaded instead")
urllib.request.urlopen = real_urlopen
# ---------------------------------------------------------------- updates (simcast/update.py)
import hashlib  # noqa: E402
import http.server  # noqa: E402
import threading as _th  # noqa: E402
from simcast import update as up  # noqa: E402

check(up.ver_tuple("v1.3.1") == (1, 3, 1, 0) and up.ver_tuple("1.10") > up.ver_tuple("1.9") and
      up.ver_tuple("nonsense") == (0, 0, 0, 0), "updates: versions compare by number")
wizard = b"MZ" + bytes(range(256)) * 2400                       # stands in for SimCast-Setup.exe (over 500 kB)
sha = hashlib.sha256(wizard).hexdigest()
rel = {"tag_name": "v9.0", "html_url": "https://example.invalid/r", "body": "notes",
       "assets": [{"name": "release-notes.md", "browser_download_url": "x", "digest": "sha256:" + "0" * 64},
                  {"name": "SimCast-Setup.exe", "browser_download_url": "URL", "digest": "sha256:" + sha}]}
info = up.pick_release(rel, "1.3")
check(info and info["version"] == "9.0" and info["installer_url"] == "URL" and info["sha256"] == sha,
      "updates: a newer release is offered with its wizard and checksum")
check(up.pick_release(rel, "9.0") is None and up.pick_release(rel, "9.1") is None,
      "updates: the same or an older release is not offered")
check(up.pick_release(dict(rel, prerelease=True), "1.3") is None and up.pick_release(dict(rel, draft=True), "1.3") is None
      and up.pick_release({}, "1.3") is None, "updates: drafts and pre-releases are not offered")
bare = up.pick_release(dict(rel, assets=[{"name": "SimCast-Setup.exe", "browser_download_url": "URL"}]), "1.3")
check(bare and bare["sha256"] == "" and not up.can_install(bare, here=REPO) and not up.can_install(info, here=REPO),
      "updates: no checksum, or not the installed copy: a link to the release instead of installing")
check(DEFAULTS["check_updates"] is True and DEFAULTS["update_skip"] == "", "updates: checked by default")


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/ok": wizard, "/page": b"<html>not found</html>" * 40000, "/cut": wizard}[self.path]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[:1000] if self.path == "/cut" else body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
_th.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
dl = os.path.join(tempfile.mkdtemp(prefix="simcast-upd-"), "SimCast-Setup-v9.0.exe")
seen = []
up.download_installer(base + "/ok", dl, sha, progress=lambda g, t: seen.append((g, t)))
check(open(dl, "rb").read() == wizard and seen and seen[-1] == (len(wizard), len(wizard)) and
      not os.path.exists(dl + ".part"), "updates: the wizard downloads, with progress")


def refused(path, digest):
    os.remove(dl) if os.path.exists(dl) else None
    try:
        up.download_installer(base + path, dl, digest, timeout=5)
    except Exception:
        return not os.path.exists(dl) and not os.path.exists(dl + ".part")
    return False


check(refused("/ok", "0" * 64), "updates: a download that doesn't match the published checksum is thrown away")
check(refused("/ok", ""), "updates: no checksum, no install")
check(refused("/page", hashlib.sha256(b"<html>not found</html>" * 40000).hexdigest()),
      "updates: a web page instead of a program is thrown away")
check(refused("/cut", sha), "updates: a download cut short is thrown away")
srv.shutdown()
# ---------------------------------------------------------------- pronouns, personality, random chat, sponsors
from simcast import persona  # noqa: E402
check(persona.name_gender("Lewis Hamilton") == "m" and persona.name_gender("Sophia Floersch") == "f",
      "pronouns: a clear first name gives he or she")
check(all(persona.name_gender(n) is None for n in ("Alex Albon", "Sam C", "L. Hamilton", "xX_Speed_Xx", "Okafor", "")),
      "pronouns: an unclear name, an initial, a tag or a surname stays they")
check(persona.from_choice("she", "Lewis Hamilton") == "f" and persona.from_choice("they", "Lewis") is None and
      persona.from_choice("auto", "Emma Jones") == "f", "pronouns: your own choice wins over your name")
check(persona.voice_gender("en-GB-SoniaNeural") == "f" and persona.voice_gender("Ryan (British) - Microsoft") == "m"
      and persona.voice_gender("kokoro:bf_emma") == "f" and persona.voice_gender("openai:alloy") is None,
      "pronouns: a commentator with an unclear name goes by their voice")
check(persona.made_up_sponsors("Okafor") == persona.made_up_sponsors("Okafor") and persona.made_up_sponsors("Okafor"),
      "sponsors: made-up backers are the same every time for a driver")
check(persona.parse_driver_sponsors("Okafor: Acme Oil, Bolt; Lewis Hamilton = Zed\nnonsense") ==
      {"okafor": ["Acme Oil", "Bolt"], "lewis hamilton": ["Zed"]}, "sponsors: other drivers' list is read")
check(persona.car_make("Porsche 911 GT3 R (992)") == "Porsche" and persona.car_make("Aston Martin Vantage") ==
      "Aston Martin", "sponsors: the car's make for 'the ... Porsche of Okafor'")

calls.clear()
loud = dict(DEFAULTS, openai_api_key="test", ai_colour=True, booth_tone="unhinged", swearing=True, banter="lots",
            race_sponsor="Acme Oil", my_sponsors="Bolt Energy", driver_name="Emma Stone", lead_name="Jordan",
            lead_voice="en-GB-SoniaNeural", analyst_name="Kate")
booth = ai_mod.AIBooth(lambda: loud, fake_chat, providers._json_reply, log=lambda m: None)
booth.sync = True
cast = headless_run(laps=4, seed=3, booth=booth, cfg=loud, log=lambda m: None)
prompt = calls[0][0]["content"] + calls[0][1]["content"]
check("UNHINGED" in prompt and "Swearing is allowed" in prompt, "personality: unhinged and swearing reach the AI booth")
check("Jordan (she/her)" in prompt and "Kate (she/her)" in prompt,
      "pronouns: the booth's own, from the name or the voice")
check("(she) " in prompt and re.search(r"\] \((he|she|they)\)", prompt) is not None,
      "pronouns: every driver in the standings has one, the viewer's from their name")
check("SPONSORS: race title sponsor Acme Oil" in prompt and "Bolt Energy" in prompt,
      "sponsors: the race's and the viewer's reach the AI booth")
more = [ln for _, ln in cast.audio.spoken if ln.text.startswith("AI-MORE")]
check(len({ln.text.split(":")[0] for ln in more}) >= 2, f"conversation: the AI booth keeps a conversation going "
      f"({len(more)} follow-up lines)")
check(any(ln.mood == "frustrated" for ln in more) and all(ln.mood in persona.MOODS or ln.mood == "" for ln in more),
      "emotion: the AI's moods come through, an unknown one is dropped")
fill = [ai_mod.filler_task(cast.director.writer, 0, "balanced")[0] for _ in range(60)]
check(any("random conversation" in t for t in fill) and any("sponsor from SPONSORS" in t for t in fill),
      "random chat: the AI booth gets tangents and sponsor reads in the quiet spells")
calls.clear()
calm = dict(DEFAULTS, openai_api_key="test", ai_colour=True)
booth = ai_mod.AIBooth(lambda: calm, fake_chat, providers._json_reply, log=lambda m: None)
booth.sync = True
headless_run(laps=3, seed=3, booth=booth, log=lambda m: None)
prompt = calls[0][0]["content"]
check("Emotion is everything" in prompt and '"mood"' in prompt, "emotion: the AI booth gives every line a mood")
check("a proper television booth" in prompt and "no swearing" in prompt, "personality: professional and clean by default")

from simcast import banter  # noqa: E402
SWEARS = ("bloody", "hell", "shit", "damn", "sod ", "f...", "jesus")
loud_said, clean_said = [], []
for sd in range(4):
    c = headless_run(laps=8, seed=sd, cfg=dict(booth_tone="unhinged", swearing=True, banter="lots",
                                               race_sponsor="Acme Oil"), log=lambda m: None)
    loud_said += [ln.text for _, ln in c.audio.spoken]
    c = headless_run(laps=8, seed=sd, cfg=dict(booth_tone="unhinged", swearing=False, banter="lots"), log=lambda m: None)
    clean_said += [ln.text for _, ln in c.audio.spoken]
outb = {t.split("{")[0][:18] for _, _, _, t in banter.OUTBURSTS}
check(any(any(t.startswith(o) for o in outb if o) for t in loud_said), "personality: the unhinged booth has outbursts")
check(any(ln.mood for _, ln in c.audio.spoken), "emotion: the instant lines carry a mood")
check(any(ln.mood == "angry" or ln.mood == "gutted" for sd in range(1) for _, ln in c.audio.spoken) or
      any(ln.mood in ("shocked", "thrilled") for _, ln in c.audio.spoken), "emotion: the big moments sound like it")
from simcast import audio as audio_mod  # noqa: E402
from simcast.writer import Line as _L  # noqa: E402
calm_p = audio_mod._prosody(_L("lead", "x", 1, "gutted"), {"booth_tone": "broadcast"})
wild_p = audio_mod._prosody(_L("lead", "x", 1, "gutted"), {"booth_tone": "unhinged"})
check(calm_p[0] < 0 and wild_p[0] < calm_p[0], "emotion: a gutted line is slower, and more so when unhinged")
check("angry" in persona.mood_direction("angry", "unhinged", True) and "never bleeped" in
      persona.mood_direction("angry", "unhinged", True), "emotion: OpenAI voices are told how to say it")
check(any(w in t.lower() for t in loud_said for w in SWEARS), "personality: with swearing on, they swear")
check(not any(w in t.lower() for t in clean_said for w in ("bloody", "shit", "damn", "sod ", "f...", "jesus")),
      "personality: with swearing off, nobody swears, however unhinged")
check(persona.the_race({"race_name": " 2026  Las Vegas Grand Prix "}) == "the 2026 Las Vegas Grand Prix" and
      persona.the_race({"race_name": "The Bathurst 12 Hour"}) == "The Bathurst 12 Hour" and
      persona.the_race({}) == "", "race name: said with 'the', unless it already has one")
named = headless_run(laps=3, seed=3, cfg=dict(race_name="2026 Las Vegas Grand Prix"), log=lambda m: None)
said = [ln.text for _, ln in named.audio.spoken]
check(any("the 2026 Las Vegas Grand Prix" in t for t in said[:3]), "race name: the booth welcomes viewers to it")
check(sum("2026 Las Vegas Grand Prix" in t for t in said) >= 2, "race name: and says it again at the finish")
trk = named.director.tr.track
check(trk and all(trk in t for t in said if "2026 Las Vegas Grand Prix" in t),
      "race name: every line that names the race names the track too")
calls.clear()
nm = dict(DEFAULTS, openai_api_key="test", ai_colour=True, race_name="2026 Las Vegas Grand Prix")
booth = ai_mod.AIBooth(lambda: nm, fake_chat, providers._json_reply, log=lambda m: None)
booth.sync = True
headless_run(laps=3, seed=3, booth=booth, cfg=nm, log=lambda m: None)
check(any('EVENT: this race is called "2026 Las Vegas Grand Prix" and it\'s being run at' in m["content"] for c_ in calls for m in c_),
      "race name: reaches the AI booth")
w = c.director.writer
check(persona.car_make("GT3 #12") == "" and persona.car_make("LMP2") == "", "sponsors: a class isn't a make")
w.cfg = dict(w.cfg, race_sponsor="Acme Oil", banter="lots")
reads = []
for _ in range(30):
    w._sponsor_t = -1e9
    reads += [ln.text for ln in w._tg_sponsor(1000.0)]
check(any("Acme Oil" in t for t in reads), "sponsors: the race sponsor is read out in the quiet spells")
check(any(x in t for t in reads for x in persona._BRAND_A), "sponsors: and a car's backers")
w.cfg = dict(w.cfg, booth_tone="broadcast")
check(all(lvl >= 1 for k, lvl, sw, t in banter.OUTBURSTS) and w._outburst(type("E", (), {"kind": "overtake",
      "cars": ()})(), "analyst", 0) == [], "personality: the professional booth has no outbursts")

# ---------------------------------------------------------------- a second AI for the conversations (DeepSeek)
check(providers.convo_provider(dict(DEFAULTS, openai_api_key="a", deepseek_api_key="b")) == "deepseek",
      "two AIs: Automatic hands the conversations to DeepSeek when it has a key")
check(providers.convo_provider(dict(DEFAULTS, openai_api_key="a")) == "openai",
      "two AIs: with one key, the main provider does both")
check(providers.convo_provider(dict(DEFAULTS, openai_api_key="a", deepseek_api_key="b", ai_convo="same")) == "openai"
      and providers.convo_provider(dict(DEFAULTS, ai_provider="deepseek", deepseek_api_key="b", gemini_api_key="c",
                                        ai_convo="gemini")) == "gemini", "two AIs: picked by hand")
who = {"main": [], "talk": []}


def chat_as(name):
    def f(cfg, messages, max_tokens=300, timeout=20.0):
        who[name].append(messages[-1]["content"])
        return fake_chat(cfg, messages, max_tokens, timeout)
    return f


both = dict(DEFAULTS, openai_api_key="a", deepseek_api_key="b", ai_colour=True, banter="lots")
main_b = ai_mod.AIBooth(lambda: both, chat_as("main"), providers._json_reply, log=lambda m: None)
talk_b = ai_mod.AIBooth(lambda: both, chat_as("talk"), providers._json_reply, log=lambda m: None)
main_b.sync = talk_b.sync = True
cast = headless_run(laps=5, seed=5, booth=main_b, convo_booth=talk_b, cfg=both, log=lambda m: None)
check(who["talk"] and all("quiet" in t for t in who["talk"]), f"two AIs: DeepSeek writes the conversations "
      f"({len(who['talk'])} requests)")
check(any("carry the conversation" in t.lower() or "has already called it" in t for t in who["main"]) and
      not any("quiet moment" in t for t in who["main"]), "two AIs: the main one answers the big moments")
said = [ln.text for _, ln in cast.audio.spoken]
check(any(t.startswith("AI-FILLER") for t in said) and any(t.startswith("AI-REACT") for t in said),
      "two AIs: both are heard on air")

# ---------------------------------------------------------------- the best model for each job
check(providers.best_model(DEFAULTS, "deepseek", "react") == "deepseek-chat" and
      providers.best_model(DEFAULTS, "deepseek", "convo") == "deepseek-v4-pro",
      "models: DeepSeek reacts with its quick model and talks with its strongest")
check(providers.best_model(DEFAULTS, "gemini", "convo") == "gemini-3.1-pro-preview" and
      providers.best_model(DEFAULTS, "gemini", "react") == "gemini-3.5-flash", "models: so does Gemini")
oc = providers.best_model(DEFAULTS, "openai", "convo")
check(oc in providers.OPENAI_MODELS and "mini" not in oc and "reasoner" not in oc,
      f"models: OpenAI talks with a full-size model ({oc})")
check(providers.rank_for(["x-mini", "o3", "gpt-7", "gpt-6.5"], "convo")[:2] == ["gpt-7", "gpt-6.5"],
      "models: a newer model the list doesn't know yet is used for conversations")
check(providers.ai_model(dict(DEFAULTS, ai_provider="deepseek", deepseek_model="deepseek-reasoner",
                              _purpose="convo")) == "deepseek-reasoner", "models: one picked by hand always wins")
seen = []
pb = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="a"),
                    lambda cfg, m, max_tokens=0, timeout=0: seen.append((cfg.get("_purpose"), timeout)) or "{}",
                    providers._json_reply, log=lambda m: None)
pb.sync = True
pb.request("t", "b", lambda *a: None, purpose="convo")
pb.last_req = -1e9
pb.request("t", "b", lambda *a: None)
check(seen == [("convo", ai_mod.CONVO_TIMEOUT), ("react", ai_mod.TIMEOUT)],
      "models: each request says what it's for, and a conversation may take longer")

# ---------------------------------------------------------------- driver mode: quiet while the driver is busy
from types import SimpleNamespace as _NS  # noqa: E402
from simcast import cockpit  # noqa: E402
ld = cockpit.DriverLoad()
ld.update(_NS(brake=0.6, speed=40.0, gap_ahead=3.0, gap_behind=4.0), 10.0)
check(ld.busy(10.0) == "braking" and ld.busy(10.5) == "braking", "driver mode: braking is busy")
ld.update(_NS(brake=0.0, speed=40.0, gap_ahead=3.0, gap_behind=4.0), 11.0)
check(ld.busy(11.0) == "", "driver mode: free again once past the apex")
ld.update(_NS(brake=0.0, speed=40.0, gap_ahead=0.5, gap_behind=4.0), 12.0)
check(ld.busy(12.0) == "close", "driver mode: a car within a second is busy")
ld.update(_NS(brake=0.0, speed=40.0, gap_ahead=0.9, gap_behind=4.0), 12.5)
check(ld.busy(12.5) == "close", "driver mode: and stays busy until there's a real gap (no flicker)")
ld.update(_NS(brake=0.0, speed=40.0, gap_ahead=1.3, gap_behind=4.0), 13.0)
check(ld.busy(13.0) == "", "driver mode: free on a gap")
check(ld.busy(20.0) == "", "driver mode: no telemetry, nothing to hold for")
lf = cockpit.DriverLoad()
for k in range(40):
    lf.update(_NS(brake=0.0, speed=40.0, gap_ahead=0.4, gap_behind=4.0), 100.0 + k * 0.5)
check(lf.busy(119.5) == "fight", "driver mode: a long fight lets race calls out between the corners")
lf.update(_NS(brake=0.5, speed=40.0, gap_ahead=0.4, gap_behind=4.0), 120.0)
check(lf.busy(120.0) == "close", "driver mode: but never in the braking zone")

said_at = []
_orig_say = SimAudio.say


def _rec(self, lines, priority, now):
    said_at.append(now)
    return _orig_say(self, lines, priority, now)


def _busy(self, now):
    return "close" if now > 40 and int(now) % 30 < 12 else ""


SimAudio.say, _orig_busy = _rec, cockpit.DriverLoad.busy
cockpit.DriverLoad.busy = _busy
try:
    c = headless_run(laps=5, seed=4, cfg=dict(drive_mode="driver"), log=lambda m: None)
finally:
    SimAudio.say, cockpit.DriverLoad.busy = _orig_say, _orig_busy
quiet = [t for t in said_at if t > 40 and int(t) % 30 < 12 and not c.tracker.finished]
check(not quiet, f"driver mode: nothing starts while the driver is busy ({quiet[:3]})")
check(sum(1 for t in said_at if t > 40) > 10, "driver mode: the booth catches up on the straights")
fill_at = []
_orig_fill = Writer.filler


def _fill_spy(self, now, focus="balanced"):
    fill_at.append(now)
    return _orig_fill(self, now, focus)


SimAudio.say, Writer.filler = _rec, _fill_spy
cockpit.DriverLoad.busy = lambda self, now: "fight" if now > 40 else ""
said_at.clear()
try:
    fc = headless_run(laps=5, seed=4, cfg=dict(drive_mode="driver"), log=lambda m: None)
finally:
    SimAudio.say, cockpit.DriverLoad.busy, Writer.filler = _orig_say, _orig_busy, _orig_fill
check(sum(1 for t in said_at if 40 < t) > 10, "driver mode: in a long fight the race is still called")
check(not [t for t in fill_at if t > 40 and not fc.tracker.finished],
      "driver mode: but no side conversations until it's over")
said_at.clear()
SimAudio.say = _rec
try:
    headless_run(laps=5, seed=4, cfg=dict(drive_mode="stream"), log=lambda m: None)
finally:
    SimAudio.say = _orig_say
check(any(t > 40 and int(t) % 30 < 12 for t in said_at), "stream mode: the full show")

# ---------------------------------------------------------------- hotkeys: a key or a wheel button
from simcast import hotkeys  # noqa: E402


class _Pad:
    def __init__(self):
        self.down = set()

    def pressed(self):
        return set(self.down)


pad, fired = _Pad(), []
hk = hotkeys.Hotkeys(lambda: {"hotkey_skip": "joy:Wheel#3", "hotkey_me": "key:F9"}, fired.append,
                     log=lambda m: None, source=pad)
pad.down = {"joy:Wheel#3"}
hk.step()
hk.step()                                                     # held down: one press, one action
pad.down = set()
hk.step()
pad.down = {"key:F9", "joy:Wheel#0"}
hk.step()
check(fired == ["skip", "me"], f"hotkeys: a wheel button and a key fire their actions once per press ({fired})")
got = []
hk.learn(got.append)
pad.down = {"key:F9", "joy:Wheel#0", "joy:Wheel#5"}
hk.step()
check(got == ["joy:Wheel#5"] and fired == ["skip", "me"], "hotkeys: Set takes the next button pressed, and does nothing else")
check(hotkeys.describe("joy:Fanatec CSL#7") == "Fanatec CSL, button 8" and hotkeys.describe("key:F9") == "F9" and
      hotkeys.describe("") == "not set", "hotkeys: shown the way a person reads them")
hc = headless_run(laps=3, seed=2, log=lambda m: None)
hc.hotkey("mode")
check(hc.cfg["drive_mode"] == "driver", "hotkeys: Driving/Stream switches the mode")
hc.hotkey("mute")
check(not hc.director.on_air, "hotkeys: the booth goes off air")
hc.hotkey("mute")
n0 = len(hc.audio.spoken)
hc.tracker.finished = False
t_me = hc.audio.spoken[-1][0] + 120.0
check(hc.director.talk_about_me(t_me) and len(hc.audio.spoken) > n0, "hotkeys: Talk about me puts the viewer's race on air now")

# ---------------------------------------------------------------- memory across races
from simcast.memory import Memory  # noqa: E402
mpath = os.path.join(tempfile.mkdtemp(), "memory.json")
m1 = Memory(mpath, log=lambda m: None)
r1 = headless_run(laps=5, seed=7, memory=m1, log=lambda m: None)
check(len(m1.data["races"]) == 1 and len(m1.data["races"][0]["results"]) >= 2, "memory: a race is remembered")
check(os.path.exists(mpath), "memory: written to memory.json")
m2 = Memory(mpath, log=lambda m: None)
win = next(r for r in m2.data["races"][0]["results"] if r["pos"] == 1)["name"]
check(m2.driver(win)["wins"] == 1 and m2.driver(win)["last"][2] == 0, "memory: read back next time, the winner's win too")
m2.data["incidents"].append({"kind": "contact", "names": ["Luca Moretti", "Sam Carter"], "track": "Spa", "race": 0})
check(m2.pair("Sam Carter", "Luca Moretti") == {"kind": "contact", "track": "Spa", "back": 0} and
      "made contact at Spa last time out" in m2.pair_words(m2.pair("Sam Carter", "Luca Moretti")),
      "memory: an incident between two drivers, said the way a commentator would")
m2.data["calls"]["kate"] = {"right": 2, "wrong": 5}
mem_calls = []
_mchat = lambda cfg, msgs, max_tokens=0, timeout=0: mem_calls.append(msgs) or "{}"  # noqa: E731
mb = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="t"), _mchat, providers._json_reply, log=lambda m: None)
mb.sync = True
r2 = headless_run(laps=5, seed=7, memory=m2, booth=mb, log=lambda m: None)
brief = mem_calls[0][1]["content"]
check("HISTORY" in brief and f"{win}: 1 race(s) remembered, 1 win(s)" in brief, "memory: the AI booth gets the history")
check("Luca Moretti and Sam Carter made contact at Spa" in brief or "Sam Carter and Luca Moretti made contact at Spa"
      in brief, "memory: and the rivalries between the drivers on track")
check("Kate's predictions in earlier races: 2 right, 5 wrong" in brief, "memory: and the booth's own record")
said2 = [ln.text for _, ln in r2.audio.spoken]
check(any("last time out" in t or "in a row" in t for t in said2), "memory: the booth brings up last race's winner")
check(len(m2.data["races"]) == 2, "memory: the second race is added")
m3 = Memory(os.path.join(tempfile.mkdtemp(), "memory.json"), log=lambda m: None)
headless_run(laps=4, seed=3, memory=m3, cfg=dict(memory=False), log=lambda m: None)
check(not m3.data["races"], "memory: switched off, nothing is remembered")
m2.forget()
check(not Memory(mpath, log=lambda m: None).data["races"], "memory: Forget clears it")

# ---------------------------------------------------------------- league roster
from simcast import roster as roster_mod  # noqa: E402
rdir = tempfile.mkdtemp()
rpath = os.path.join(rdir, "league.csv")
with open(rpath, "w", encoding="utf-8-sig") as fh:
    fh.write("Name;No.;Team;Pronouns;Sponsors;Say as\n"
             "Luca Moretti;12;Scuderia Rossa;they;Acme Oil / Bolt Energy;\n"
             "Sam Carter;#7;Blue Arrow;she;;\n"
             "Okafor;3;;he;Zed Watches;Oh-KAH-for\n")
ents = roster_mod.parse(open(rpath, encoding="utf-8-sig").read())
check(ents["luca moretti"]["number"] == "12" and ents["luca moretti"]["sponsors"] == ["Acme Oil", "Bolt Energy"] and
      ents["sam carter"]["gender"] == "f" and ents["sam carter"]["number"] == "7",
      "roster: a semicolon spreadsheet with its own headings is read")
rc = headless_run(laps=3, seed=7, cfg=dict(roster_file=rpath, booth_tone="loose"), log=lambda m: None)
rw = rc.director.writer
byname = {c.name: c for c in rc.tracker.cars.values()}
mor = byname["Luca Moretti"]
oka = next(c for c in rc.tracker.cars.values() if c.call == "Okafor")
check(rw.pronoun(mor.id) == "they" and rw.pronoun(byname["Sam Carter"].id) == "she" and rw.pronoun(oka.id) == "he",
      "roster: its pronouns win over the guess from the name")
check(rw.sponsors.of(mor) == ["Acme Oil", "Bolt Energy"], "roster: its sponsors win over made-up ones")
check(rw.car_words(mor.id, "Acme Oil").startswith("the number 12 Acme Oil"), f"roster: 'the number 12 ...' "
      f"({rw.car_words(mor.id, 'Acme Oil')})")
check(rw.n(oka.id) == "Oh-KAH-for", "roster: Say as is how the booth says the name")
rbrief = ai_mod.race_brief(rc.tracker, rw, [], [])
check("#12 Scuderia Rossa," in rbrief and "(they)" in rbrief, "roster: the AI booth gets numbers, teams and pronouns")
tpl = roster_mod.write_template(os.path.join(rdir, "new.csv"), ["Ana Lima", "Bo Chen"])
check(set(roster_mod.parse(open(tpl, encoding="utf-8-sig").read())) == {"ana lima", "bo chen"},
      "roster: Make one writes a roster that reads back")

# ---------------------------------------------------------------- more from the sims: weather, tyres, fuel, incidents
yaml = """WeekendInfo:
 TrackDisplayName: Spa
 TrackLength: 7.00 km
 TrackAirTemp: 21.50 C
 TrackSurfaceTemp: 34.20 C
 TrackSkies: Partly Cloudy
DriverInfo:
 DriverCarIdx: 1
 DriverTires:
 - TireIndex: 0
   TireCompoundType: "Hard"
 - TireIndex: 1
   TireCompoundType: "Wet"
 Drivers:
 - CarIdx: 0
   UserName: Ana Lima
   TeamName: Lima Racing
   CarNumber: "12"
   CarScreenName: Porsche 911 GT3 R
   CurDriverIncidentCount: 4
 - CarIdx: 1
   UserName: Bo Chen
   TeamName: Bo Chen
   CarNumber: "7"
   CarScreenName: BMW M4 GT3
   CurDriverIncidentCount: 0
SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionType: Race
   SessionLaps: 20
"""
inf = sc._ir_session_info(yaml)
check(inf["air_temp"] == 21.5 and inf["track_temp"] == 34.2 and inf["sky"] == "partly cloudy" and
      inf["tyres"] == {0: "hard", 1: "wet"}, "iRacing: air and track temperature, sky and the tyre compounds")
check(inf["drivers"][0]["number"] == "12" and inf["drivers"][0]["team"] == "Lima Racing" and
      inf["drivers"][0]["incidents"] == 4 and inf["drivers"][1]["team"] is None,
      "iRacing: car numbers, teams (not a solo driver's own name) and incident points")
check(sc._car_number("Ferrari AF Corse 2023 #51") == "51" and sc._car_number("Ferrari 499P") is None,
      "LMU/rF2: the car number from the entry name")
fe = sc.field_entry(3, "X Y", "GT3", 1, 2, 0.5, number="#12", incidents=4, tyre="soft", penalties=1)
check(fe["number"] == "12" and fe["incidents"] == 4 and fe["tyre"] == "soft" and fe["penalties"] == 1,
      "field entries carry numbers, incidents, tyres and penalties")

xtr = RaceTracker(log=lambda m: None)
xev, t = [], 0.0
while t < 240:
    f = frame(pass_scn(t), t, race_laps=20)
    for e in f.field:
        e["number"] = str(10 + e["id"])
        e["tyre"] = "medium" if t < 150 else "soft"
        e["incidents"] = 0 if t < 100 or e["id"] != 1 else 4
        e["penalties"] = 0 if t < 120 or e["id"] != 0 else 1
    f.air_temp, f.track_temp, f.sky, f.rain = 19.0, 31.0, "overcast", 0.5
    f.fuel = 60.0 - 1.0 * f.laps_done - f.pct
    f.tyre, f.incidents = "medium", 2
    xev += [e for e in xtr.update(f, t)]
    t += 1 / 30
xk = [e.kind for e in xev]
check("incident" in xk and next(e for e in xev if e.kind == "incident").cars == (1,),
      "incidents: four points in one go is news")
check("penalty" in xk, "penalties: a new one is news")
check(xtr.cond.get("air_temp") == 19.0 and abs((xtr.cond.get("fuel_per_lap") or 0) - 1.0) < 0.2 and
      xtr.cond.get("fuel_laps"), f"conditions and the viewer's fuel per lap ({xtr.cond.get('fuel_per_lap')})")
xw = Writer(xtr, {}, seed=1)
xb = ai_mod.race_brief(xtr, xw, [], [])
check("CONDITIONS: air 19C, track 31C, sky overcast, track wet" in xb and "VIEWER'S CAR: fuel for about" in xb,
      "the AI gets the conditions and the viewer's fuel")
check("#11" in xb and "on soft tyres" in xb and "4x incident points" in xb and "1 penalty" in xb,
      "the AI gets numbers, tyres, incident points and penalties for every car")
from simcast.director import test_event  # noqa: E402
po = xw.ev_pit_out(test_event("pit_out", 300.0, (0,), pos=2, was=1, lane_time=28.0, tyre="wet", tyre_was="soft"), 300.0)
check("Off the soft tyres and on to the wet" in po[0].text, "pit stops: a tyre change is called")
check("Only state facts" in ai_mod.SYSTEM and "argue about them" in ai_mod.SYSTEM,
      "the AI may use (and argue over) the tyres, fuel and weather it's given")

# ---------------------------------------------------------------- streaming: OBS captions page, Twitch chat
import socket as _socket  # noqa: E402
import urllib.request as _ur  # noqa: E402
from simcast import stream  # noqa: E402
_s = _socket.socket()
_s.bind(("127.0.0.1", 0))
free_port = _s.getsockname()[1]
_s.close()
cap_lines = [{"seq": 1, "who": "lead", "name": "Ben", "text": "Lights out!"},
             {"seq": 2, "who": "analyst", "name": "Kate", "text": "Here we go."}]
srv = stream.CaptionServer(lambda: cap_lines, free_port, log=lambda m: None)
check(srv.start(), "OBS captions: the page is served on this PC")
try:
    page = _ur.urlopen(f"http://127.0.0.1:{free_port}/", timeout=5).read().decode()
    first = json.loads(_ur.urlopen(f"http://127.0.0.1:{free_port}/lines?since=-1", timeout=5).read())
    after = json.loads(_ur.urlopen(f"http://127.0.0.1:{free_port}/lines?since=1", timeout=5).read())
finally:
    srv.stop()
check("transparent" in page and "/lines?since=" in page, "OBS captions: a transparent page that follows the lines")
check([x["text"] for x in first["lines"]] == ["Here we go."] and after["lines"][0]["colour"] == stream.COLOURS["analyst"],
      "OBS captions: a new page starts at the latest line; each line comes with the speaker's colour")
check(stream.clean_channel("https://www.twitch.tv/Some_One/") == "some_one" and stream.clean_channel("#ab") == "",
      "Twitch: the channel from a link or a name")
tc = stream.TwitchChat(lambda: "x", log=lambda m: None)
got = tc.feed("@badge=1 :viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #x :Kate is wrong, Okafor has it?", now=10.0)
check(got and got[0] == "viewer1" and got[1] == "Kate is wrong, Okafor has it?", "Twitch: a chat message is read")
for bad in ("check out www.spam.com now", "!commands", "AAAAAAAAAAAAAAAAAAAA HELLO", "hi"):
    check(tc.feed(f":u!u@u PRIVMSG #x :{bad}", now=11.0) is None, f"Twitch: not passed to the booth: {bad!r}")
tc2 = stream.TwitchChat(lambda: "x", lambda: "!booth", log=lambda m: None)
check(tc2.feed(":u!u@u PRIVMSG #x :who wins this?", now=1.0) is None and
      tc2.feed(":u!u@u PRIVMSG #x :!booth who wins this?", now=1.0)[1] == "who wins this?",
      "Twitch: with !booth only, only messages that ask on purpose")
check(tc.take(3, now=12.0) and not tc.fresh(now=12.0), "Twitch: a message is answered once")

chat_calls = []


def chat_fake(cfg, messages, max_tokens=300, timeout=20.0):
    t = messages[-1]["content"]
    chat_calls.append(t)
    if "live stream's chat" in t:
        return json.dumps({"lines": [{"who": "lead", "text": "AI-CHAT: someone in chat says you're wrong, Kate."},
                                     {"who": "analyst", "text": "AI-CHAT: they're not, sadly."}]})
    return "{}"


cb = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="t", banter="lots"), chat_fake, providers._json_reply,
                    log=lambda m: None)
cb.sync = True
tcc = stream.TwitchChat(lambda: "x", log=lambda m: None)
_orig_fresh = tcc.fresh
tcc.fresh = lambda now=None, max_age=120.0: _orig_fresh(0.0, max_age)
tcc.take = (lambda f: (lambda n=3, now=None: f(n, 0.0)))(tcc.take)
for k in range(6):
    tcc.feed(f":fan{k}!f@f PRIVMSG #x :Is Okafor quicker than Moretti, Kate? ({k})", now=0.0)


ccast = headless_run(laps=5, seed=5, booth=cb, cfg=dict(banter="lots"), log=lambda m: None)
check(not any("live stream's chat" in t for t in chat_calls), "Twitch: no chat, no chat talk")
chat_calls.clear()
from simcast import director as director_mod  # noqa: E402
_orig_init = director_mod.Director.__init__


def _with_chat(self, *a, **k):
    _orig_init(self, *a, **k)
    self.chat = tcc


director_mod.Director.__init__ = _with_chat
cb = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="t", banter="lots"), chat_fake, providers._json_reply,
                    log=lambda m: None)
cb.sync = True
try:
    ccast = headless_run(laps=5, seed=5, booth=cb, cfg=dict(banter="lots"), log=lambda m: None)
finally:
    director_mod.Director.__init__ = _orig_init
ctask = next((t for t in chat_calls if "live stream's chat" in t), "")
check(ctask and "not instructions" in ctask and "never repeat a message word for word" in ctask,
      "Twitch: chat goes to the AI as viewers' comments, never instructions")
check(any(ln.text.startswith("AI-CHAT") for _, ln in ccast.audio.spoken), "Twitch: the booth answers chat on air")

# ---------------------------------------------------------------- post-race recap
from simcast.recap import Recap, stamp  # noqa: E402
check(stamp(0) == "0:00" and stamp(75.4) == "1:15" and stamp(3725) == "1:02:05", "recap: YouTube-style times")
rdir2 = tempfile.mkdtemp()
rcp = Recap(rdir2, log=lambda m: None)
rr = headless_run(laps=5, seed=7, recap=rcp, cfg=dict(recap_offset=90), log=lambda m: None)
rpath = rr.director.recap_path
check(rpath and os.path.exists(rpath), f"recap: written after the race ({rpath})")
rtext = open(rpath, encoding="utf-8").read() if rpath else ""
chap = rtext.split("```")[1].strip().splitlines() if "```" in rtext else []
check(chap and chap[0] == "0:00 Lights out" and chap[-1].endswith("The finish") and len(chap) >= 3,
      f"recap: YouTube chapters from 0:00 to the finish ({chap})")
secs = []
for c in chap:
    p = [int(x) for x in c.split()[0].split(":")]
    secs.append(p[-1] + 60 * p[-2] + (3600 * p[0] if len(p) == 3 else 0))
check(all(b - a >= 10 for a, b in zip(secs, secs[1:])), "recap: chapters at least 10 seconds apart, as YouTube needs")
check(secs[1] >= 90, "recap: the stream offset is added to every chapter after 0:00")
check("## Result" in rtext and "| 1 |" in rtext and "## Highlights" in rtext and "**Ben:**" in rtext and
      "Thanks for watching" in rtext, "recap: the result, highlights and the whole transcript, sign-off included")
check(headless_run(laps=3, seed=7, recap=Recap(rdir2, log=lambda m: None), cfg=dict(recap=False),
                   log=lambda m: None).director.recap_path is None, "recap: switched off, nothing is written")

# ---------------------------------------------------------------- ElevenLabs: the most emotional voices
from simcast import eleven  # noqa: E402
import urllib.error as _ue  # noqa: E402
check(engine_of("eleven:abc123") == ("eleven", "abc123"), "ElevenLabs: its voices are an engine of their own")
check(eleven.cue("gutted") == "[sad]" and eleven.cue("amused") == "[laughs]" and eleven.cue("") == "",
      "ElevenLabs: a line's mood becomes an emotion cue")
sent = []


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        d, self.data = self.data, b""
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=0):
    body = json.loads(req.data.decode()) if req.data else None
    sent.append((req.full_url, req.headers.get("Xi-api-key"), body))
    if req.full_url.endswith("/voices"):
        return _Resp(json.dumps({"voices": [{"voice_id": "v1", "name": "Rory", "labels": {"accent": "irish"}}]}).encode())
    if body and body["model_id"] == "eleven_v3" and "refuse" in body["text"]:
        raise _ue.HTTPError(req.full_url, 403, "no v3", {}, None)
    return _Resp(b"ID3fake-mp3")


_orig_open = eleven.urllib.request.urlopen
eleven.urllib.request.urlopen = _fake_urlopen
try:
    ecfg = dict(DEFAULTS, elevenlabs_api_key="sk-test", booth_tone="unhinged")
    vs = eleven.fetch_voices(ecfg)
    out_mp3 = os.path.join(tempfile.mkdtemp(), "x.mp3")
    eleven.synth(ecfg, "v1", "Into the lead!", "thrilled", out_mp3)
    first = sent[-1]
    eleven.synth(dict(ecfg, elevenlabs_api_key="sk-other"), "v1", "refuse this", "angry", out_mp3)
    fell = sent[-1]
finally:
    eleven.urllib.request.urlopen = _orig_open
check(vs == [("Rory (Irish) - ElevenLabs (your key)", "eleven:v1")] and eleven.voices(ecfg) == vs,
      "ElevenLabs: the voices on the account are listed")
check(first[1] == "sk-test" and first[2]["text"] == "[excited] Into the lead!" and first[2]["model_id"] == "eleven_v3"
      and first[2]["voice_settings"]["style"] >= 0.7 and open(out_mp3, "rb").read() == b"ID3fake-mp3",
      "ElevenLabs: v3 with the emotion cue, and an unhinged booth lets the voice go")
check(fell[2]["model_id"] == "eleven_multilingual_v2" and fell[2]["text"] == "refuse this",
      "ElevenLabs: an account without v3 falls back to v2, without the cue")

# ---------------------------------------------------------------- faster reactions: streamed line by line
reply = ('```json\n{"lines": [{"who": "analyst", "text": "That {brace} \\"quote\\" move was huge.", "energy": 2, '
         '"mood": "excited"}, {"who": "lead", "text": "And it is not over.", "mood": "tense"}]}\n```')
lsr = ai_mod.LineStream()
items = []
for k in range(0, len(reply), 7):
    items += [(k, x) for x in lsr.feed(reply[k:k + 7])]
check([x["text"] for _, x in items] == ['That {brace} "quote" move was huge.', "And it is not over."] and
      items[0][0] < len(reply) * 0.7, "streaming: each line is picked out as soon as it's whole, braces and quotes "
      "in the text and all")
order = []


def fake_stream(cfg, msgs, on_text, max_tokens=0, timeout=0):
    for k in range(0, len(reply), 9):
        order.append("piece")
        on_text(reply[k:k + 9])
    order.append("done")
    return reply


sb = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="t"), lambda *a, **k: "{}", providers._json_reply,
                    log=lambda m: None, stream_fn=fake_stream)
sb.sync = True
got = []
sb.request("react", "brief", lambda lines, info: (order.append("line"), got.extend(lines)), opener="analyst")
check([ln.who for ln in got] == ["analyst", "lead"] and got[0].mood == "excited",
      "streaming: the reaction comes through line by line, still answering itself")
check(order.index("line") < order.index("done"), "streaming: the first line is out before the reply has finished")
conv = []
sb.last_req = -1e9
sb.request("chat", "brief", lambda lines, info: conv.append(lines), purpose="convo")
check(conv == [] and order.count("done") == 1, "streaming: conversations (fetched ahead) use the ordinary request")
sb2 = ai_mod.AIBooth(lambda: dict(DEFAULTS, openai_api_key="t"), fake_chat, providers._json_reply,
                     log=lambda m: None, stream_fn=lambda cfg, m, on_text, max_tokens=0, timeout=0:
                     (on_text(fake_chat(cfg, m)), fake_chat(cfg, m))[1])
sb2.sync = True
calls.clear()
scast = headless_run(laps=5, seed=5, booth=sb2, log=lambda m: None)
check(any(ln.text.startswith("AI-REACT") for _, ln in scast.audio.spoken), "streaming: reactions are heard in a race")

try:
    __import__("openai")
    _oc, _ac = providers.openai_client, providers.ai_chat
    providers.openai_client = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no network"))
    providers.ai_chat = lambda cfg, m, max_tokens=0, timeout=0: '{"lines": []}'
    pieces = []
    try:
        whole = providers.ai_chat_stream(dict(DEFAULTS, openai_api_key="t"), [], pieces.append)
    finally:
        providers.openai_client, providers.ai_chat = _oc, _ac
    check(whole == '{"lines": []}' and pieces == [whole], "streaming: if it can't stream, the ordinary request is used")
except ImportError:
    pass

# ---------------------------------------------------------------- NASCAR mode
from simcast import nascar  # noqa: E402
from simcast.app import clean_config  # noqa: E402
from simcast.nascar import NascarTracker  # noqa: E402

check(nascar.is_stock_car("NASCAR Cup Series Next Gen Chevrolet Camaro ZL1") and nascar.is_stock_car("ARCA Menards Chevrolet Impala")
      and nascar.is_stock_car("Super Late Model") and not nascar.is_stock_car("Ford Mustang GT3")
      and not nascar.is_stock_car("Stock Car Pro Series 2023"), "nascar: stock cars told from the rest")
nf = frame(pass_scn(0), 0)
check(not nascar.detect(nf, "auto") and nascar.detect(nf, "on"), "nascar: GT3 on a road course stays off unless asked")
nf.track_type = "short oval"
check(nascar.detect(nf, "auto") and not nascar.detect(nf, "off"), "nascar: an oval turns it on (and Never wins)")
nf.track_type = "road course"
for e in nf.field:
    e["car"] = "NASCAR Xfinity Ford Mustang"
check(nascar.detect(nf, "auto"), "nascar: a field of stock cars turns it on, even on a road course")
ac_ovals = ["Daytona (Oval)", "rt_daytona (tri oval)", "Bristol Motor Speedway", "martinsville", "North Wilkesboro",
            "talladega_superspeedway", "Indianapolis (Oval)"]
ac_roads = ["Daytona (Road Course)", "Charlotte (Roval)", "Indianapolis Motor Speedway (Road)", "Spa", "ks_nordschleife",
            "Watkins Glen", "Brands Hatch (GP)", "Laguna Seca"]
check(all(nascar.is_oval(t) for t in ac_ovals) and not any(nascar.is_oval(t) for t in ac_roads),
      f"nascar: Assetto Corsa ovals told from road courses by name and layout "
      f"({[t for t in ac_ovals if not nascar.is_oval(t)]} {[t for t in ac_roads if nascar.is_oval(t)]})")
check(not nascar.is_oval("Daytona (Oval)", "road course") and nascar.is_oval("Daytona", "super speedway"),
      "nascar: iRacing's track type wins over the name")
check(all(nascar.is_stock_car(c) for c in ("nascar_2023_chevy_camaro", "gen6_cup_ford", "acrl_stock_car_toyota",
                                            "legends_ford34", "arca_ford")) and
      not any(nascar.is_stock_car(c) for c in ("ks_porsche_911_gt3_cup_2017", "ks_mazda_mx5_cup", "ford_mustang_gt3",
                                                "stock_car_brasil_2022")), "nascar: Assetto Corsa's stock car mods")
check(sc.nice_name("rt_daytona") == "Daytona" and sc.nice_name("bristol_motor_speedway") == "Bristol Motor Speedway",
      "Assetto Corsa's folder names said as names")
acast = SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False)
acf = sc.Frame("AC", "Martinsville (Oval)", "Gen6 Cup Chevrolet", 0, 0.1, 0.0, 0, 0, 0, 1, 900, 0, 0, False, False,
               session="race", position=4, cars=20, race_laps=50)
acast.feed(acf, 0.0)
check(acast.mode == "nascar_solo" and acast.tracker.nascar_words and acast.config()["lead_voice"].startswith("en-US"),
      f"nascar: Assetto Corsa on an oval without CrewChiefEx gets NASCAR words and voices ({acast.mode})")
check(acast.director.writer.floor([__import__("simcast.writer", fromlist=["Line"]).Line("lead", "Into the pit lane.")])[0].text
      == "Onto pit road.", "nascar: your-car-only mode talks NASCAR too")
check(nascar.stage_laps(200, "") == [60, 120] and nascar.stage_laps(10, "") == [] and
      nascar.stage_laps(312, "60, 185") == [60, 185] and nascar.stage_laps(100, "off") == [] and
      nascar.stage_laps(100, "50, 300") == [50], "nascar: stage laps, automatic and typed")
check(nascar.clean_stages(" 185;60 ") == "60, 185" and nascar.clean_stages("Auto") == "" and
      nascar.clean_stages("OFF") == "off", "nascar: the stages setting is tidied")
cc = clean_config(dict(DEFAULTS, nascar="sometimes", nascar_stages="x 40"))
check(cc["nascar"] == "auto" and cc["nascar_stages"] == "40", "nascar: settings cleaned")
check(nascar.vocab("Into the pit lane, then lights out under the safety car on new tyres to the chequered flag.") ==
      "Onto pit road, then green flag under the pace car on new tires to the checkered flag.",
      "nascar: the broadcast's words")


def oval_scn(t):
    """Three cars on a 25-lap race: car 0 leads, car 1 runs second, car 3 is slow and a lap down by lap 12.
    A caution comes out at 600 s and goes green at 660 s."""
    go = 0 if t < 5 else 1
    return [{"id": 0, "name": "Alex Front", "dist": lapsf(t, 30.0, 0.010), "speed": 150 * go},
            {"id": 1, "name": "Blake Quick", "dist": lapsf(t, 30.5, 0.005), "speed": 148 * go},
            {"id": 2, "name": "Me Myself", "dist": lapsf(t, 31.0, 0.0), "speed": 146 * go},
            {"id": 3, "name": "Dana Slow", "dist": lapsf(t, 34.0, -0.005), "speed": 130 * go}]


ntr = NascarTracker(log=lambda m: None, stages_fn=lambda: "8, 15")
nev, t = [], 0.0
while t < 5 + 30 * 25 + 20:
    fl = "fcy" if 600 <= t < 660 else None
    nev += [e for e in ntr.update(frame(oval_scn(t), t, race_laps=25, flag=fl), t)]
    t += 1 / 20
nk = [e.kind for e in nev]
stages = [e for e in nev if e.kind == "stage_end"]
check([e.data["stage"] for e in stages] == [1, 2] and all(e.cars == (0,) for e in stages) and
      ntr.stage_winners == [0, 0], f"nascar: stage ends called with their winner ({[e.cars for e in stages]})")
check(stages and stages[0].data["top"][:3] == [0, 1, 2], "nascar: the stage's top ten, in order")
check("stage_to_go" in nk, "nascar: three to go in the stage")
check(ntr.laps_led.get(0) == 25 and ntr.leaders == [0] and ntr.most_led() == (0, 25), f"nascar: laps led ({ntr.laps_led})")
check(ntr.cautions == 1 and "caution" in nk and "green" in nk, "nascar: the caution and the restart")
fp = [e for e in nev if e.kind == "free_pass"]
check(len(fp) == 1 and fp[0].cars == (3,), f"nascar: the free pass to the first car a lap down ({[e.cars for e in fp]})")
check("winner" in nk, "nascar: the winner")

ncast = headless_run(laps=20, seed=5, cfg={"nascar": "on"}, log=lambda m: None)
nspoken = " ".join(ln.text for _, ln in ncast.audio.spoken)
check(ncast.mode == "nascar" and ncast.tracker.stage_ends == [6, 12], "nascar: Always gives the demo race NASCAR mode")
check("stage one" in nspoken and "stage two" in nspoken and "checkered flag" in nspoken.lower(),
      "nascar: the booth calls the stages and the checkered flag")
check(not re.search(r"(?i)pit lane|chequered|safety car|lights out|tyre", nspoken),
      "nascar: no road-racing words on air")
check(ncast.config()["lead_voice"].startswith("en-US") and
      SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False).config()["lead_voice"]
      == DEFAULTS["lead_voice"], "nascar: American voices for NASCAR only")
own = dict(DEFAULTS, nascar="on", lead_voice="en-GB-ThomasNeural")
ocast = headless_run(laps=2, seed=5, cfg=own, log=lambda m: None)
check(ocast.config()["lead_voice"] == "en-GB-ThomasNeural", "nascar: a voice you chose yourself is kept")
# The NASCAR button: on or off for this race, keeping the race so far
tcast = SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False)
tcast.source = "TEST"
t, tt = 0.0, None
while t < 5 + 61 * 2:
    tcast.feed(frame(pass_scn(t), t, race_laps=30), t)
    t += 1 / 20
tt = tcast.tracker
laps_before = tt.leader_laps
check(tcast.mode == "field" and not tcast.nascar_on(), "nascar button: a GT3 race starts as the whole field")
tcast.toggle_nascar()
check(tcast.mode == "nascar" and tcast.tracker is tt and tt.nascar and tt.leader_laps == laps_before and tt.started
      and tcast.nascar_on(), "nascar button: on mid-race, the race so far kept")
while t < 5 + 61 * 4:
    tcast.feed(frame(pass_scn(t), t, race_laps=30), t)
    t += 1 / 20
check(tcast.mode == "nascar" and sum(tt.laps_led.values()) >= 1, "nascar button: stays on, laps led counted from then")
tcast.hotkey("nascar")
check(tcast.mode == "field" and not getattr(tcast.tracker, "nascar", False) and tcast.tracker is tt,
      "nascar button: the hotkey switches it back off")
tcast.toggle_nascar()
other = frame(pass_scn(t), t, race_laps=30)
other.track = "Another Ring"
for k in range(40):
    tcast.feed(other, t + k / 20)
check(tcast.nascar_race is None and tcast.mode == "field", "nascar button: a new track goes back to the setting")
idle = SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False)
idle.toggle_nascar()
check(idle.nascar_on(), "nascar button: switched on before the race")
idle.feed(frame(pass_scn(0), 0, race_laps=30), 0.0)
check(idle.mode == "nascar", "nascar button: ... and the next race is NASCAR")
# The NASCAR way of saying it: car lengths, mph, an oval booth's words
gtr = NascarTracker(log=lambda m: None)
gtr.track_len = 858.0                                   # Martinsville, about half a mile
check(nascar.gap_say(gtr, 0.1) == "a car length" and nascar.gap_say(gtr, 0.4) == "four car lengths" and
      nascar.gap_say(gtr, 0.02) == "inches" and nascar.gap_say(gtr, 2.5) == "two point five seconds",
      f"nascar: close gaps in car lengths, bigger ones in seconds ({nascar.gap_say(gtr, 0.4)})")
check(nascar.lap_say(gtr, 19.8) == "ninety-seven mile an hour lap" and
      nascar.lap_say(NascarTracker(log=lambda m: None), 19.8) == "nineteen point eight",
      f"nascar: laps as mph when the track's length is known ({nascar.lap_say(gtr, 19.8)})")
gtr.track_len = 4023.0
check(nascar.lap_say(gtr, 47.0) == "one ninety-one mile an hour lap", "nascar: 'one ninety-one' at Daytona speed")
nlines = [ln.text for _, ln in ncast.audio.spoken]
check(any("car length" in t for t in nlines) and any("mile an hour lap" in t for t in nlines) and
      any("running order" in t or "clean air" in t or "laps today" in t for t in nlines),
      "nascar: the booth measures in car lengths and mph, and does an oval broadcast's segments")
check(not any(re.search(r"(?i)braking zone|in the mirrors|purple|the grid|stewards|tenths? (behind|back|apart|between)",
                        t) for t in nlines), "nascar: no road-racing phrases left on air")
check("NASCAR booth" in ai_mod.NASCAR_KIND and "car lengths" in ai_mod.NASCAR_STYLE,
      "nascar: the AI booth gets a NASCAR booth's style")
# Lanes from the cars' world positions (Assetto Corsa with CrewChiefEx): a made-up oval, 8 cars on the bottom; car 7
# runs a lane higher (or lower), pulls alongside car 6 and gets by
import math  # noqa: E402
from simcast.writer import Writer  # noqa: E402


def oval_lanes(off7, track="Test Speedway (Oval)"):
    R = 150.0
    L = 2 * math.pi * R

    def scn(t):
        cars = []
        for k in range(8):
            d = max(0.0, t - 3) / (18.0 + k * 0.01) - k * 0.012
            off = 0.0
            if k == 7 and t > 120:
                off = off7
                d += min(0.0264, (t - 120) / 30 * 0.0264)
            cars.append((k, d, off))
        pos = {c[0]: i + 1 for i, c in enumerate(sorted(cars, key=lambda c: -c[1]))}
        fld = [sc.field_entry(k, f"Driver {chr(65 + k)}ski", "NASCAR Cup Chevrolet", pos[k], int(d), d % 1.0,
                              player=k == 2, speed=0 if t < 3 else 150,
                              xyz=((R + off) * math.cos(2 * math.pi * (d % 1.0)), 0.0,
                                   (R + off) * math.sin(2 * math.pi * (d % 1.0)))) for k, d, off in cars]
        f = sc.Frame("AC", track, "NASCAR Cup Chevrolet", 0, 0, 150, 1, 0, 0, 4, 7000, 0, 0, False, False,
                     track_len=L, session="race", position=pos[2], cars=8, race_laps=40)
        f.field = fld
        return f
    ltr = NascarTracker(log=lambda m: None)
    lw = Writer(ltr, dict(DEFAULTS), seed=2)
    t, out, said = 0.0, [], []
    while t < 170:
        for e in ltr.update(scn(t), t):
            out.append(e)
            if e.kind in ("side_by_side", "overtake"):
                said += [ln.text for ln in lw.event(e, t)]
        t += 1 / 30
    return ltr, out, said


ltr, levs, lsaid = oval_lanes(5.0)
sbs = [e for e in levs if e.kind == "side_by_side"]
lov = [e for e in levs if e.kind == "overtake" and e.cars[:2] == (7, 6)]
check(ltr.oval and ltr.cars[7].lat is not None and ltr.cars[7].lat - ltr.cars[6].lat > 3.5,
      f"lanes: the car up high measured a lane above ({ltr.cars[7].lat}, {ltr.cars[6].lat})")
check(len(sbs) == 1 and sbs[0].cars == (6, 7), f"lanes: side by side called, low car first ({[e.cars for e in sbs]})")
check(len(lov) == 1 and lov[0].data.get("lane") == "high", f"lanes: the pass is around the outside ({[e.data for e in lov]})")
check(any("on the bottom" in t and "outside" in t or "low" in t and "high" in t for t in lsaid) and
      any(re.search(r"outside|Up high|high line", t) for t in lsaid), f"lanes: the booth says who's low and high ({lsaid})")
ltr, levs, lsaid = oval_lanes(-5.0)
lov = [e for e in levs if e.kind == "overtake" and e.cars[:2] == (7, 6)]
check(len(lov) == 1 and lov[0].data.get("lane") == "low" and any(re.search(r"bottom|Underneath|inside", t) for t in lsaid),
      f"lanes: a pass underneath is called on the bottom ({lsaid})")
ltr, levs, lsaid = oval_lanes(5.0, track="Spa")
check(not ltr.oval and any(e.kind == "side_by_side" for e in levs) and
      not any(re.search(r"bottom|outside|Up high|high line|low", t) for t in lsaid),
      f"lanes: on a road course it's side by side, but no low and high ({lsaid})")

brief = ai_mod.race_brief(ncast.tracker, ncast.director.writer, [], [])
check("BROADCAST: this is a NASCAR" in brief and "STAGES:" in brief and "LAPS LED:" in brief,
      "nascar: the AI is told it's a NASCAR broadcast, with stages and laps led")
gcast = headless_run(laps=2, seed=5, log=lambda m: None)
check(gcast.mode == "field" and "BROADCAST" not in ai_mod.race_brief(gcast.tracker, gcast.director.writer, [], []),
      "nascar: Automatic leaves the GT3 demo alone")

# ---------------------------------------------------------------- your car only: the race ends properly
from simcast.demo import DemoRace  # noqa: E402
from simcast.app import LOOP_HZ  # noqa: E402
from simcast.recap import Recap  # noqa: E402
import random  # noqa: E402
rdir = tempfile.mkdtemp(prefix="simcast-recap-")
scast = SimCast(dict(DEFAULTS), audio=SimAudio(), log=lambda m: None, booth=False, save=False)
scast.source = "TEST"
scast.director.recap = Recap(rdir, log=lambda m: None)
scast.director.writer.rng = random.Random(4)
sdemo, t = DemoRace(laps=3, seed=4), 0.0
while t < 1500:
    sf = sdemo.read(1.0 / LOOP_HZ)
    if sf is not None:
        sf.game, sf.field, sf.track, sf.car = "AC", None, "Martinsville (Oval)", "Gen6 Cup Chevrolet"
    scast.feed(sf, t)
    scast.tick(t)
    scast.audio.tick(t)
    if sdemo.finished and scast.director.wrapped and not scast.audio.speaking(t):
        break
    t += 1.0 / LOOP_HZ
scast.director.finish_recap(demo=False)
stext = " ".join(ln.text for _, ln in scast.audio.spoken)
check(scast.mode == "nascar_solo" and scast.director.wrapped and "Goodnight" in stext and "victory for" not in stext,
      "your car only: the booth signs off with your result, not a 'winner'")
recaps = os.listdir(rdir)
check(len(recaps) == 1 and "## Result" in open(os.path.join(rdir, recaps[0])).read() and
      "| 1 |" not in open(os.path.join(rdir, recaps[0])).read(), f"your car only: the recap is written ({recaps})")

# ---------------------------------------------------------------- cost estimate
from simcast.usage import Meter, parse_rates  # noqa: E402
check(parse_rates("openai 2/8, elevenlabs 200; junk") == {"openai": (2.0, 8.0), "elevenlabs": (200.0,)},
      "cost: rates read as written")
mt = Meter()
mt.chat("openai", 1_000_000, 100_000)
mt.speech("elevenlabs", 10_000)
check(abs(Meter.cost(mt.race) - (2.0 + 0.8 + 2.0)) < 1e-6, "cost: tokens and characters priced with the default rates")
check(abs(Meter.cost(mt.race, "openai 1/1") - (1.0 + 0.1 + 2.0)) < 1e-6, "cost: edited rates win")
mt.race_t0 -= 600
summ = mt.summary()
check("about $4.80" in summ and "OpenAI 1000.0k tokens in, 100.0k out" in summ and "ElevenLabs 10.0k chars" in summ
      and "30-minute race is about $14.40" in summ, f"cost: the summary for Settings ({summ})")
mt.new_race()
check(not mt.race and "Last race: about $4.80" in mt.summary(), "cost: a new race starts the count again")
check(Meter().summary() == "Nothing paid for has been used yet.", "cost: nothing used, nothing to pay")

done()
