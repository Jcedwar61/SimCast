"""SimCast: the real race behind an Assetto Corsa race started by Historic Roster (history.py): loading and
matching race_context.json, the cars by AC index, the AI brief with and without spoilers, and the instant lines."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

from _common import REPO, check, done

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="simcast-test-")
sys.path.insert(0, REPO)
from simcast import sims as sc                          # noqa: E402
from simcast import ai as ai_mod                        # noqa: E402
from simcast.history import RaceContext, context_path, fold, spoilers_allowed   # noqa: E402
from simcast.race import Event, RaceTracker             # noqa: E402
from simcast.writer import Writer                       # noqa: E402


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def context(written=None, track="ks_monza66", layout="", schema=1):
    car = lambda i, d, t, g, p, st="Finished", **k: dict(car_index=i, driver=d, team=t, number=str(g), nation=None,
                                                         car=k.get("car"), ac_model="m", **{"class": None},
                                                         stand_in_for=k.get("stand_in"), real_grid=g, real_pos=p,
                                                         real_status=st, real_laps=53)
    return {
        "schema": schema, "source": "Historic Roster", "app_version": "1.0.0",
        "written_utc": utc(written if written is not None else time.time()),
        "ac": {"track": track, "layout": layout, "race_laps": 3, "sessions": ["race"], "watch": False},
        "event": {"name": "2006 Italian Grand Prix", "series": "Formula 1", "category": "F1", "year": 2006,
                  "date": "2006-09-10", "circuit": "Autodromo Nazionale Monza", "locality": "Monza",
                  "real_laps": 53, "multiclass": False, "classes": []},
        "real_conditions": {"start_local": "14:00", "start_known": True, "ambient_c": 24, "kind": "clear",
                            "night": False, "summary": "clear, 24 °C, start 14:00 local"},
        "player": {"car_index": 0, "seat_driver": "Michael Schumacher"},
        "grid": [car(0, "Michael Schumacher", "Ferrari", 2, 1),
                 car(1, "Kimi Räikkönen", "McLaren", 1, 2),
                 car(2, "Nick Heidfeld", "BMW", 3, 8),
                 car(3, "Fernando Alonso", "Renault", 10, None, "Engine", stand_in="Renault R26")],
        "not_racing": [{"driver": "Christijan Albers", "team": "MF1", "real_grid": 19, "real_pos": 17}],
        "source_note": None,
    }


def write(d, path=None):
    path = path or context_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return path


# ---------------------------------------------------------------- a malformed file never breaks the window
bad = context()
bad["ac"]["layout"] = 5
bad["event"]["series"] = ["F1"]
bad["event"]["date"] = 20060910
bad["player"] = []
bad["grid"][1]["team"] = 7
bad["grid"][2]["car"] = {"x": 1}
bad["grid"][3]["real_grid"] = float("inf")
bad["grid"].append({"driver": 42})
bad["grid"].append("not a car")
bad["not_racing"] = {"driver": "x"}
ctx = RaceContext.read(write(bad))
check(ctx is not None and ctx.grid[1].team == "7" and ctx.grid[2].car is None, "malformed: odd values coerced")
check(ctx is not None and ctx.series is None and ctx.player_index is None and ctx.not_racing == [],
      "malformed: wrong-typed sections ignored")
for key, val in (("ac", []), ("event", "2006"), ("grid", {"a": 1})):
    check(RaceContext.read(write({**context(), key: val})) is not None, f"malformed: a file with a bad {key} still reads")

# ---------------------------------------------------------------- loading and matching
write(context())
check(RaceContext.load("AC", "ks_monza66") is not None, "load: AC on the same track")
check(RaceContext.load("ACC", "ks_monza66") is None, "load: another sim is ignored")
check(RaceContext.load("AC EVO", "ks_monza66") is None, "load: AC EVO is ignored")
check(RaceContext.load("AC", "spa") is None, "load: another track is ignored")
check(RaceContext.load("AC", "ks_monza66 (junior)") is None, "load: another layout is ignored")
write(context(written=time.time() - 13 * 3600))
check(RaceContext.load("AC", "ks_monza66") is None, "load: a context older than 12 hours is ignored")
write(context(track="ks_nordschleife", layout="endurance"))
check(RaceContext.load("AC", "ks_nordschleife (endurance)") is not None, "load: a layout as SimCast shows it")
check(RaceContext.load("AC", "ks_nordschleife") is None, "load: the layout missing from the session")
# since SimCast 1.15 the track said on air is the track's own name; the AC folder and layout match for sure
write(context())
check(RaceContext.load("AC", "Autodromo Nazionale Monza", folder="ks_monza66", layout="") is not None,
      "load: matched by the AC folder whatever the track is called on air")
check(RaceContext.load("AC", "Autodromo Nazionale Monza", folder="ks_monza66", layout="junior") is None,
      "load: the folder with another layout is ignored")
check(RaceContext.load("AC", "Monza", folder="monza", layout="") is None, "load: another folder is ignored")
check(RaceContext.load("AC", "Autodromo Nazionale Monza") is not None,
      "load: without the folder, the real circuit's name matches")
check(RaceContext.load("AC", "Spa-Francorchamps") is None, "load: without the folder, another name doesn't")
write(context(schema=2))
check(RaceContext.load("AC", "ks_monza66") is None, "load: an unknown schema is ignored")
with open(context_path(), "w", encoding="utf-8") as fh:
    fh.write("{ not json")
logged = []
check(RaceContext.load("AC", "ks_monza66", log=logged.append) is None and logged, "load: a broken file is logged")
os.remove(context_path())
check(RaceContext.load("AC", "ks_monza66") is None, "load: no file, no context")
check(fold("Kimi Räikkönen") == "kimiraikkonen", "names: accents and case folded")
check(not spoilers_allowed({}, False) and spoilers_allowed({}, True), "spoilers: after the finish by default")
check(spoilers_allowed({"history_spoilers": "always"}, False), "spoilers: any time")
check(not spoilers_allowed({"history_spoilers": "never"}, True), "spoilers: never")

# ---------------------------------------------------------------- compatibility with Historic Roster
# fixtures/historic_roster_context_v1.json was written by Historic Roster's own writer (Core/SimCastContext.cs).
# Historic Roster and SimCast are updated separately: every SimCast version must keep reading this file as it is.
# Don't edit it to make a test pass; if Historic Roster's format changes, it raises "schema".
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "historic_roster_context_v1.json")
fx_written = datetime(2026, 11, 25, 19, 0, 0, tzinfo=timezone.utc).timestamp()
fx = RaceContext.load("AC", "monza", path=FIXTURE, now=fx_written + 60)
check(fx is not None, "compatibility: Historic Roster's schema 1 file loads")
check(fx is not None and fx.name == "2006 Italian Grand Prix" and fx.year == 2006 and fx.real_laps == 53,
      "compatibility: the event")
check(fx is not None and fx.player_seat() is not None and fx.player_seat().driver == "Michael Schumacher",
      "compatibility: the player's seat")
check(fx is not None and len(fx.grid) == 4 and fx.winner() is not None and fx.winner().driver == "Michael Schumacher",
      "compatibility: the grid and the real winner")
check(fx is not None and [c.driver for c in fx.grid if c.stand_in_for] == ["Fernando Alonso"],
      "compatibility: a stand-in car")
check(fx is not None and fx.conditions and "24" in fx.conditions, "compatibility: the real race day")
check(fx is not None and fx.multiclass and fx.grid[0].cls == "A", "compatibility: classes")
check(RaceContext.load("AC", "monza", path=FIXTURE, now=fx_written + 13 * 3600) is None,
      "compatibility: still only within 12 hours")
with open(FIXTURE, encoding="utf-8") as fh:
    newer = json.load(fh)
newer["a_field_from_a_newer_historic_roster"] = {"x": 1}
newer["event"]["something_new"] = [1, 2]
newer["grid"][0]["new_per_car"] = "y"
check(RaceContext.load("AC", "monza", path=write(newer, os.path.join(tempfile.mkdtemp(), "n.json")),
                       now=fx_written + 60) is not None,
      "compatibility: fields added by a newer Historic Roster are ignored")
bare = {"schema": 1, "written_utc": "2026-11-25T19:00:00Z", "ac": {"track": "monza"}}
bare_ctx = RaceContext.load("AC", "monza", path=write(bare, os.path.join(tempfile.mkdtemp(), "b.json")),
                            now=fx_written + 60)
check(bare_ctx is not None and bare_ctx.grid == [] and bare_ctx.player_seat() is None and bare_ctx.winner() is None,
      "compatibility: a file with only the essentials loads")
bare_tr = RaceTracker(log=lambda m: None, history_loader=lambda g, t, log, **k: bare_ctx)
bare_f = sc.Frame("AC", "monza", "F1", 0, 0.0, 0.0, 1, 0, 0, 4, 7000, 0, 0, False, False, session="race")
bare_f.field = [sc.field_entry(0, "Jane Player", "F1", 1, 0, 0.0, player=True)]
bare_tr.update(bare_f, 0.0)
bare_brief = ai_mod.race_brief(bare_tr, Writer(bare_tr, {}, seed=1), [], [])
check("REAL RACE" in bare_brief, "compatibility: the brief copes with a bare file")

# ---------------------------------------------------------------- a race in AC, started by Historic Roster
write(context())
L = 2000.0
NAMES = {0: "Jane Player", 1: "Kimi Raikkonen", 2: "Nick Heidfeld", 3: "Fernando Alonso"}
PACE = {0: 0.98, 1: 1.0, 2: 0.96, 3: 0.95}            # laps a minute: Raikkonen's car wins


def frame(t, game="AC"):
    dist = {i: max(0.0, t - 10) / 60 * p - i * 0.002 for i, p in PACE.items()}   # 10 s on the grid first
    order = sorted(dist, key=lambda i: -dist[i])
    pos = {cid: k + 1 for k, cid in enumerate(order)}
    field = [sc.field_entry(i, NAMES[i], "F1", pos[i], int(d) if d >= 0 else 0, d % 1.0, False, None, None,
                            None, None, player=i == 0, speed=200.0 if t > 10 else 0.0) for i, d in dist.items()]
    me = dist[0]
    # as SimCast's AC reader has it: the track's own name on air, the folder and layout for matching
    f = sc.Frame(game, "Autodromo Nazionale Monza", "F1", int(me), me % 1.0, 200.0, 1, 0, 0, 4, 7000, 0, 0, False,
                 False, track_len=L, session="race", position=pos[0], cars=4, race_laps=3)
    f.field = field
    if game == "AC":
        f.track_folder, f.track_layout = "ks_monza66", ""
    return f


tr = RaceTracker(log=lambda m: None)
tr.update(frame(0.0), 0.0)
h = tr.history
check(h is not None and h.name == "2006 Italian Grand Prix", "tracker: the context is loaded for the session")
check(h.entry(tr.car(0)).driver == "Michael Schumacher", "cars: the player's car is matched by index, not name")
check(h.entry(tr.car(1)).driver == "Kimi Räikkönen", "cars: an AI car is matched by index (name folded)")
check(h.entry(tr.car(3)).stand_in_for == "Renault R26", "cars: a stand-in car keeps its real car")

# SimCast.config() gives the booth the real event as the race's name ('_real_race', tested below)
w = Writer(tr, {"_real_race": h.name}, seed=3)
intro = [ln.text for ln in w.ev_intro(Event("intro", 0.0, 50), 0.0)]
check(any("Autodromo Nazionale Monza" in x for x in intro), f"intro: the circuit ({intro[0]})")
check(any("2006 Italian Grand Prix" in x for x in intro), "intro: the real event, as the race's name")
check(any("10 September 2006" in x for x in intro), "intro: the real day")
check(any("Michael Schumacher's seat" in x and "second" in x for x in intro), f"intro: the viewer's real seat {intro}")
w_named = Writer(tr, {"_real_race": h.name, "race_name": "My League Round 3"}, seed=3)
check(any("My League Round 3" in ln.text for ln in w_named.ev_intro(Event("intro", 0.0, 50), 0.0)),
      "intro: a race name typed in the settings wins")

brief = ai_mod.race_brief(tr, w, [], [])
check("REAL RACE" in brief and "10 September 2006" in brief, "brief: the real race and its date")
check("Real pole: Kimi Räikkönen" in brief, "brief: the real pole")
check("held back" in brief and "Real result:" not in brief, "brief: no real result before the finish")
check("Engine" not in brief, "brief: real retirements held back too")
check("real race: started P10; a stand-in car for the real Renault R26" in brief, "brief: a stand-in car's row")
check("Christijan Albers" in brief, "brief: real entries not on this grid")
check("REAL RACE" in ai_mod.intro_task(history=True) and "REAL RACE" not in ai_mod.intro_task(),
      "AI tasks: the intro brings the real race in only when there is one")

t, finished_at = 0.0, None
while t < 400 and finished_at is None:
    tr.update(frame(t), t)
    if tr.finished:
        finished_at = t
    t += 0.1
check(finished_at is not None and tr.winner == 1, f"race: Raikkonen's car wins (winner {tr.winner})")
brief = ai_mod.race_brief(tr, w, [], [])
check("Real result: P1 Michael Schumacher (Ferrari) from P2" in brief, "brief: the real result after the finish")
check("Fernando Alonso (Engine)" in brief, "brief: real retirements after the finish")
check("REAL RACE" in ai_mod.wrap_task(history=True, spoil=True) and
      "REAL RACE" not in ai_mod.wrap_task(history=True, spoil=False), "AI tasks: the wrap-up compares when allowed")
wrap = [ln.text for ln in w.wrap_up(t)]
check(any("Michael Schumacher" in x and "2006" in x for x in wrap), f"wrap-up: the real winner ({wrap})")

w_never = Writer(tr, {"history_spoilers": "never"}, seed=3)
check(not any("2006" in ln.text for ln in w_never.wrap_up(t)), "wrap-up: no real result when set to never")
check("Real result:" not in ai_mod.race_brief(tr, w_never, [], []), "brief: never means never")
w_off = Writer(tr, {"history": False}, seed=3)
check("REAL RACE" not in ai_mod.race_brief(tr, w_off, [], []), "settings: real races off, no history at all")

# another sim on the same machine: the file is there, but it isn't this race
tr2 = RaceTracker(log=lambda m: None)
tr2.update(frame(0.0, game="ACC"), 0.0)
check(tr2.history is None, "tracker: no context for another sim")
w2 = Writer(tr2, {}, seed=3)
check("REAL RACE" not in ai_mod.race_brief(tr2, w2, [], []), "brief: unchanged without a context")

# the start screen: the race Historic Roster has set up, before the sim is running
from simcast.app import SimCast                         # noqa: E402
from simcast.audio import SimAudio                      # noqa: E402

cast = SimCast(cfg={}, audio=SimAudio(), booth=False, save=False)
check(cast.state()["real_race_ready"] == "2006 Italian Grand Prix", "start screen: Historic Roster's race shown")
check(cast.state()["real_race"] is None, "start screen: no real race live before a session")
cast.tracker.update(frame(0.0), 0.0)
check(cast.config()["_real_race"] == "2006 Italian Grand Prix", "race name: the real event when none is typed")
check(cast.state()["real_race"] == "2006 Italian Grand Prix", "window: the real event shown for the session")
check("_real_race" not in cast.cfg, "race name: never in the saved settings")
os.remove(context_path())
cast._ready_race = (0.0, None, None)
check(cast.state()["real_race_ready"] is None, "start screen: nothing shown without the file")
write(context())
cast_off = SimCast(cfg={"history": False}, audio=SimAudio(), booth=False, save=False)
check(cast_off.state()["real_race_ready"] is None, "start screen: nothing shown with real races off")
done()
