"""NASCAR mode: stock car races called the way an American oval broadcast calls them.

The race itself is followed exactly as in the whole-field mode (race.py); NascarTracker adds what a NASCAR booth
talks about on top: stages and their winners, laps led, the caution count, the free pass for the first car a lap
down, and the restart. The booth's own lines for those moments live here too (event_lines), and vocab() turns
the rest of the booth's words into the broadcast's own: pit road, the checkered flag, the pace car, tires.

It comes on by itself for stock cars (iRacing's NASCAR, ARCA, Late Models, Legends, Modifieds, and Assetto Corsa's
stock car mods) and on oval tracks (iRacing's track type; Assetto Corsa's track names and layouts), or on/off from Settings > Booth >
NASCAR mode, or for one race from the NASCAR button. Assetto Corsa without the CrewChiefEx app shows only your car: that gets the
broadcast's words and voices, but stages and laps led need the whole field.
"""
from __future__ import annotations

import re
import statistics

from . import persona
from .race import CAUTION_FLAGS, Event, RaceTracker
from .writer import ANALYST, LEAD, PIT, Line, lap_time_words, num_words, ordinal_words, place, plural
from .writer import gap_words as _gap_words

CHOICES = ("auto", "on", "off")
STAGE_POINTS = 10                    # the top ten at the end of a stage score points: ten for the winner down to one
AUTO_STAGES_MIN_LAPS = 20            # shorter races aren't split into stages unless the laps are given
AUTO_STAGE_SPLIT = (0.3, 0.6)        # where the stages end in an 'auto' race, as a share of the distance

# Cars that make a race a stock car race: as iRacing names them, and as Assetto Corsa's mod folders do
# ('nascar_2023_chevy', 'gen6_cup_ford', 'acrl_stock_car_toyota'...).
_BRAND = r"(chevy|chevrolet|ford|toyota|dodge|pontiac|camry|mustang|camaro|impala|monte carlo|fusion|silverado|tundra)"
_STOCK = re.compile(r"nascar|\bcup series|\bnext ?gen\b|xfinity|nationwide|\bbusch\b|truck series|craftsman truck|"
                    r"\barca\b|\bnxs\b|late model|\blegends\b|\bmodified\b|street stock|mini stock|super stock|"
                    r"\bsk\b|stock ?car|camaro zl1|\bgen ?[4-7]\b|"
                    rf"\b{_BRAND}\b.*\b(cup|truck)\b|\b(cup|truck)\b.*\b{_BRAND}\b", re.I)
_NOT_STOCK = re.compile(r"stock ?car (pro|brasil|brazil|v8)|\bgt[34]\b|\bgte\b|lmp|prototype|formula|indy|"
                        r"dallara|porsche|clio|mini cooper|supercup", re.I)
# Oval tracks: iRacing's TrackType ('short oval', 'super speedway', 'dirt oval'), or the track's own name as
# Assetto Corsa gives it (the mod's folder and its layout: 'rt_daytona (oval)', 'bristol_motor_speedway').
_OVAL = re.compile(r"oval|speedway", re.I)
_OVAL_NAMES = re.compile(
    r"\boval|speedway|superspeedway|short ?track|daytona|talladega|bristol|martinsville|richmond|darlington|dover|"
    r"phoenix|charlotte|atlanta|texas motor|kansas|las ?vegas motor|\blvms\b|homestead|michigan|pocono|"
    r"nashville|iowa|new hampshire|loudon|gateway|world wide technology|kentucky|rockingham|north wilkesboro|"
    r"wilkesboro|chicagoland|auto club|fontana|california speedway|south boston|hickory|langley|irwindale|"
    r"five flags|lucas oil|bowman gray|myrtle beach|eldora|knoxville|milwaukee mile|thompson|stafford|"
    r"oxford plains|lanier|concord|madison|slinger|winchester|berlin raceway|kern county|"
    r"evergreen|toledo|mobile intl|nazareth|\bisms?\b", re.I)
# ... but not their road courses
_ROAD = re.compile(r"road|roval|\bgp\b|grand ?prix|infield|rallycross|\brx\b|drift|24 ?h|sports ?car|rolex|"
                   r"\bcircuit\b|\bglen\b|chicago street|\bstreet\b", re.I)


def _words(name):
    """'rt_daytona-2020' -> 'rt daytona 2020': mod folder names read like the rest."""
    return re.sub(r"[_\-.]+", " ", str(name or ""))


def is_stock_car(name: str) -> bool:
    n = _words(name)
    return bool(_STOCK.search(n)) and not _NOT_STOCK.search(n)


def is_oval(track: str, track_type: str = "") -> bool:
    """An oval from iRacing's track type, else from the track's name (Assetto Corsa's folder and layout)."""
    if track_type:
        return bool(_OVAL.search(track_type))
    t = _words(track)
    m = re.match(r"(.*?)\s*\(([^)]*)\)\s*$", t)        # 'daytona (oval)': the layout decides when there is one
    name, layout = (m.group(1), m.group(2)) if m else (t, "")
    if layout:
        if re.search(r"\boval|speedway|short ?track|nascar|\bcup\b", layout, re.I):
            return True
        if _ROAD.search(layout):
            return False
    return bool(_OVAL_NAMES.search(name)) and not _ROAD.search(name)


def detect(frame, setting="auto") -> bool:
    """Whether this race gets the NASCAR broadcast: 'on' and 'off' decide; 'auto' looks for stock cars (the
    viewer's or most of the field's) and oval tracks."""
    if setting == "on":
        return True
    if setting == "off" or frame is None:
        return False
    if is_oval(getattr(frame, "track", ""), getattr(frame, "track_type", None) or ""):
        return True
    if is_stock_car(getattr(frame, "car", "")):
        return True
    cars = [e.get("car") or "" for e in (getattr(frame, "field", None) or [])]
    cars = [c for c in cars if c]
    return bool(cars) and sum(is_stock_car(c) for c in cars) * 2 > len(cars)


def stage_laps(race_laps, setting="") -> list:
    """The laps the stages end on (not the race's own last lap). Empty or 'auto': two stage breaks, at about 30%
    and 60% of a race of 20 laps or more. 'off': none. Otherwise the laps as typed ('60, 185')."""
    s = str(setting or "").strip().lower()
    if not race_laps or s in ("off", "none", "no", "0"):
        return []
    if s in ("", "auto"):
        if race_laps < AUTO_STAGES_MIN_LAPS:
            return []
        return sorted({max(1, round(race_laps * x)) for x in AUTO_STAGE_SPLIT})
    laps = []
    for part in re.split(r"[,;/\s]+", s):
        if part.isdigit() and 0 < int(part) < race_laps:
            laps.append(int(part))
    return sorted(set(laps))


def clean_stages(v) -> str:
    """The stages setting as stored: '', 'off', or the laps as '60, 185'."""
    s = str(v or "").strip().lower()
    if s in ("", "auto"):
        return ""
    if s in ("off", "none", "no", "0"):
        return "off"
    laps = sorted({int(p) for p in re.split(r"[,;/\s]+", s) if p.isdigit() and 0 < int(p) < 10000})
    return ", ".join(str(x) for x in laps[:6])


class NascarTracker(RaceTracker):
    """The whole field as RaceTracker follows it, plus stages, laps led, cautions, the free pass and restarts."""
    nascar = True

    def __init__(self, log=print, stages_fn=None):
        self.stages_fn = stages_fn or (lambda: "")
        super().__init__(log=log)

    def reset(self, session=None, track=None):
        super().reset(session, track)
        self._nascar_state()

    @classmethod
    def adopt(cls, tr, stages_fn=None):
        """Turn a running RaceTracker into a NascarTracker without losing the race so far (the NASCAR button
        mid-race). Stages and laps led are counted from here on."""
        tr.__class__ = cls
        tr.stages_fn = stages_fn or (lambda: "")
        tr._nascar_state()
        tr._stages_set = False
        if tr.leader() is not None:                 # stages already run are behind us: don't call them late
            tr._stage_told = {i + 1 for i, end in enumerate(stage_laps(tr.race_laps, tr.stages_fn()))
                              if end <= tr.leader_laps}
        return tr

    def _nascar_state(self):
        self.laps_led: dict = {}                    # car id -> laps led (at the line)
        self.leaders: list = []                     # car ids that have led, in order
        self.cautions = 0
        self.stage_ends: list = []
        self.stage = 1                              # the stage being run now
        self.stage_winners: list = []               # car id per stage completed
        self._stage_told = set()
        self._caution_lap = None

    def stage_count(self):
        return len(self.stage_ends) + 1 if self.stage_ends else 0

    def most_led(self):
        if not self.laps_led:
            return None, 0
        cid = max(self.laps_led, key=lambda k: self.laps_led[k])
        return cid, self.laps_led[cid]

    def _milestones(self, f, now, prev_tl, ev):
        if self.race_laps and not self.stage_ends and not getattr(self, "_stages_set", False):
            self._stages_set = True
            self.stage_ends = stage_laps(self.race_laps, self.stages_fn())
        leader = self.leader()
        before = self.leader_laps
        super()._milestones(f, now, prev_tl, ev)
        if leader is None or self.finished or self.leader_laps <= before:
            return
        n = self.leader_laps
        self.laps_led[leader.id] = self.laps_led.get(leader.id, 0) + 1
        if leader.id not in self.leaders:
            self.leaders.append(leader.id)
        for i, end in enumerate(self.stage_ends):
            stage = i + 1
            if before < end <= n and stage not in self._stage_told:     # a lap skipped in the data still counts
                self._stage_told.add(stage)
                self.stage = stage + 1
                self.stage_winners.append(leader.id)
                top = [cid for cid in self.order[:STAGE_POINTS] if cid in self.cars]
                last = stage == len(self.stage_ends)
                ev.append(Event("stage_end", now, 92, (leader.id,), {"stage": stage, "top": top, "final_next": last},
                                ttl=30, key=f"stage{stage}"))
            elif before < end - 3 <= n < end and ("togo", stage) not in self._stage_told and end >= 10:
                self._stage_told.add(("togo", stage))
                ev.append(Event("stage_to_go", now, 45, (leader.id,), {"stage": stage, "to_go": 3}, ttl=25,
                                key=f"stagetogo{stage}"))

    def _flags(self, f, now, ev):
        was = self.flag
        super()._flags(f, now, ev)
        if self.flag and not was:
            self.cautions += 1
            self._caution_lap = self.leader_laps
            ld = self.leader()
            lucky = None
            for cid in self.order:
                c = self.cars.get(cid)
                if c and c.laps_down >= 1 and not (c.gone or c.stopped or c.finished or c.status in ("dnf", "dsq")):
                    lucky = c
                    break
            if lucky is not None and ld is not None:
                ev.append(Event("free_pass", now, 50, (lucky.id,), {"down": lucky.laps_down}, ttl=40,
                                key=f"lucky{self.cautions}"))

    def stage_break(self):
        """True when a stage has just ended (the caution that follows is for the break, not a crash)."""
        return any(0 <= self.leader_laps - end <= 1 for end in self.stage_ends[:len(self.stage_winners)])


# ---------------------------------------------------------------- the booth

def number_name(w, cid, now=None, full=False):
    """'the number 24 of Byron' when the car's number is known (the sim or the roster), else just the name."""
    tr = w.tr
    c = tr.car(cid)
    name = w.n(cid, now, full=full)
    if c is None:
        return name
    num = c.number
    if not num and getattr(w, "roster", None) is not None and w.cfg.get("roster_file"):
        ent = w.roster.find(c)
        num = ent["number"] if ent else None
    num = str(num or "").strip().lstrip("#")
    if not num or name.lower().startswith("the number"):
        return name
    return f"{name} in the number {num}"


def _recent_trouble(tr, now, within=20.0):
    """The car that most likely brought the caution out: stopped, or in trouble in the last few seconds."""
    best = None
    for c in tr.cars.values():
        if c.trouble_t is not None and now - c.trouble_t <= within and not c.gone:
            if best is None or c.trouble_t > best.trouble_t:
                best = c
    return best


def ev_intro(w, e, now):
    tr = w.tr
    if tr.session != "race" or e.data.get("joined") or getattr(tr, "solo", False):
        return None
    track = tr.track or "the speedway"
    event = persona.the_race(w.cfg)
    cars = num_words(len(tr.cars))
    out = [Line(LEAD, w.pick("nas_intro", [
        f"Hello everybody, and welcome to {event or 'stock car racing'} at {track}! {cars.capitalize()} cars, "
        f"{w._race_length()}, and it's almost time to go racing.",
        f"Good afternoon, and welcome to {event + ' at ' + track if event else track}. {cars.capitalize()} cars "
        f"are on pit road, {w._race_length()} ahead of us.",
        f"It's a beautiful day for racing here at {track}" + (f", and welcome to {event}. " if event else ". ") +
        f"{cars.capitalize()} cars, {w._race_length()} to go.",
    ]), 1)]
    pole = tr.at(1)
    bits = []
    if pole:
        bits.append(f"Thanks, {w.lead_name}. {number_name(w, pole.id, now, full=True)} rolls off from the pole")
        p2 = tr.at(2)
        if p2:
            bits.append(f"with {w.n(p2.id, now)} alongside on the front row")
    me = tr.player()
    if me and pole and me.id != pole.id and tr.pos_of(me.id):
        bits.append(f"and we'll be keeping a close eye on {w.n(me.id, now, full=True)}, starting "
                    f"{place(tr.pos_of(me.id))}")
    if bits:
        out.append(Line(ANALYST, ", ".join(bits) + ".", 1))
    ends = tr.stage_ends or stage_laps(tr.race_laps, tr.stages_fn())
    stages = ""
    if ends:
        laps = num_words(ends[0]) if len(ends) == 1 else \
            ", ".join(num_words(x) for x in ends[:-1]) + " and " + num_words(ends[-1])
        stages = (f"{num_words(len(ends) + 1).capitalize()} stages today, the breaks coming on "
                  f"lap{'s' if len(ends) > 1 else ''} {laps}. ")
    out.append(Line(LEAD, stages + w.pick("nas_intro_l", [
        "The most famous words in motorsport are about to be said. Drivers, start your engines!",
        "Engines are fired, the pace car is rolling. Let's go racing!",
        "Here comes the field behind the pace car. Buckle up, everybody.",
    ]), 2))
    return out


def ev_start(w, e, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    front = f" {w.n(p1.id, now)} and {w.n(p2.id, now)} lead them to the line," if p1 and p2 else ""
    return [Line(LEAD, w.pick("nas_start", [
        f"Green flag! Green flag!{front} and we are racing at {tr.track or 'the speedway'}!",
        "And the green flag is in the air! Here we go!",
        "Green, green, green! We're racing!",
    ]), 3), Line(ANALYST, w.pick("nas_start_a", [
        "Side by side into turn one, and everybody's looking for a lane!",
        "Watch the outside line here, it's three-wide behind the front row!",
        "Hold your breath through turn one. Nobody wins it here, but plenty can lose it.",
    ]), 2)]


def ev_caution(w, e, now):
    tr = w.tr
    w.forget()
    n = getattr(tr, "cautions", 0)
    which = f"the {ordinal_words(n)} caution" if n >= 2 else "the first caution of the day"
    if getattr(tr, "stage_break", lambda: False)():
        return [Line(LEAD, w.pick("nas_caution_stage", [
            "The caution is out for the stage break. Pit road will be busy.",
            "Yellow flag for the end of the stage. Here come the crew chiefs' big calls.",
        ]), 1)]
    c = _recent_trouble(tr, now)
    why = f" {w.n(c.id, now)} is in trouble." if c is not None else ""
    out = [Line(LEAD, w.pick("nas_caution", [
        f"Caution! The yellow flag is out, and that's {which}.{why}",
        f"Yellow flag, yellow flag! Here comes {which}.{why}",
        f"And there's the caution!{why}",
    ]), 2)]
    if tr.race_laps and tr.leader():
        left = max(0, tr.race_laps - tr.leader().laps)
        if left > 2:
            out.append(Line(ANALYST, w.pick("nas_caution_a", [
                "Pit road will be open shortly. Now it's all about tires and track position.",
                f"{num_words(left).capitalize()} laps left. That's a big call for the crew chiefs: two tires, four, "
                "or stay out?",
                "Everybody bunches up behind the pace car. Whatever gap there was, it's gone.",
            ]), 1))
    return out


def ev_sc_in(w, e, now):
    return [Line(LEAD, w.pick("nas_scin", [
        "The pace car's lights are off. We'll go green next time by!",
        "Pace car is coming to pit road. Get ready for the restart!",
    ]), 2)]


def ev_green(w, e, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    out = [Line(LEAD, w.pick("nas_green", [
        "Green flag! Here's the restart!",
        "And they're back to green! Restart!",
        "The leader picks the lane, and we're green!",
    ]), 3)]
    if p1 and p2:
        out.append(Line(ANALYST, w.pick("nas_green_a", [
            f"{w.n(p1.id, now)} and {w.n(p2.id, now)} on the front row, and it's a drag race into turn one!",
            f"{w.n(p2.id, now)} gets a push on the restart, side by side with {w.n(p1.id, now)}!",
            f"Watch the restart zone. {w.n(p1.id, now)} controls it, {w.n(p2.id, now)} has to react.",
        ]), 2))
    left = w._laps_left()
    if left is not None and 0 < left <= 3:
        out.append(Line(LEAD, f"{plural(left, 'lap').capitalize()} to go after this restart. Overtime territory!", 3))
    return out


def ev_free_pass(w, e, now):
    a = e.cars[0]
    return [Line(LEAD, w.pick("nas_lucky", [
        f"The free pass goes to {w.n(a, now)}. The lucky dog gets a lap back.",
        f"And {w.n(a, now)} is the lucky dog. Back on the lead lap at the restart.",
    ]), 1)]


def ev_stage_to_go(w, e, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    out = [Line(LEAD, f"Three laps to go in stage {num_words(e.data['stage'])}, and "
                      f"{w.n(p1.id, now) if p1 else 'the leader'} leads.", 1)]
    if p2 and p2.interval is not None:
        out.append(Line(ANALYST, f"{w.n(p2.id, now)} is {w.gw(p2.interval)} back. Ten points for the stage "
                                 "win, and a playoff point with it.", 1))
    return out


def ev_stage_end(w, e, now):
    tr = w.tr
    a = e.cars[0]
    k = e.data["stage"]
    st = num_words(k)
    w.forget()
    out = [Line(LEAD, w.pick("nas_stage", [
        f"{number_name(w, a, now)} wins stage {st}!",
        f"And that's the end of stage {st}! {w.n(a, now)} takes it!",
        f"Green and white checkered flag, and stage {st} goes to {w.n(a, now)}!",
    ]), 3)]
    top = [cid for cid in e.data.get("top", []) if tr.car(cid)]
    if len(top) >= 3:
        rest = ", ".join(w.n(cid, now) for cid in top[1:4])
        out.append(Line(ANALYST, f"{rest} round out the top four. The top ten all go home with stage points.", 1))
    me = tr.player()
    if me and me.id != a:
        p = tr.pos_of(me.id)
        if 1 < p <= 10:
            out.append(Line(ANALYST, f"And {w.n(me.id, now)} scores stage points in {place(p)}, "
                                     f"{plural(11 - p, 'point')}.", 1))
        elif p > 10:
            out.append(Line(ANALYST, f"{w.n(me.id, now)} is {place(p)}, just outside the points this time.", 1))
    out.append(Line(LEAD, "The final stage is next. That's where they pay the money." if e.data.get("final_next")
                    else f"On to stage {num_words(k + 1)}.", 1))
    return out


def ev_winner(w, e, now):
    tr = w.tr
    a = e.cars[0]
    w.forget()
    c = tr.car(a)
    win = number_name(w, a, now, full=True)
    full = w.n(a, now, full=True)
    flag = f"Checkered flag for the number {win.rsplit(' ', 1)[-1]}!" if win != full else "Checkered flag!"
    track = tr.track or "the speedway"
    event = persona.the_race(w.cfg)
    out = [Line(LEAD, w.pick("nas_win_me" if c and c.player else "nas_win", [
        f"{full} wins{' ' + event if event else ''} at {track}! {flag}",
        f"Checkered flag! {win} wins at {track}!" + (f" That's {event}!" if event else ""),
        f"{w.n(a, now, full=True)} takes the checkered flag! Headed to Victory Lane at {track}!",
    ]), 3)]
    led = tr.laps_led.get(a, 0)
    most, most_n = tr.most_led()
    bits = []
    if led and tr.race_laps and led >= tr.race_laps:
        bits.append("Led every single lap")
    elif led and most == a and len(tr.laps_led) > 1:
        bits.append(f"Led {plural(led, 'lap')}, the most of anybody")
    elif led:
        bits.append(f"Led {plural(led, 'lap')} today")
    elif c is not None:
        bits.append("Didn't lead a lap until the one that counts")
    if c is not None and c.grid:
        bits.append("from the pole" if c.grid == 1 else f"from {place(c.grid)} on the grid")
    if bits:
        out.append(Line(ANALYST, ", ".join(bits) + ".", 2))
    extra = []
    if len(tr.leaders) > 1:
        extra.append(f"{plural(len(tr.leaders), 'different leader')}")
    if tr.cautions:
        extra.append(plural(tr.cautions, "caution"))
    if len(tr.leaders) >= 3 or tr.cautions:
        wild = len(tr.leaders) >= 5 or tr.cautions >= 4
        out.append(Line(ANALYST, f"{' and '.join(extra).capitalize()} today." + (" Some kind of race." if wild else ""),
                        1))
    if most is not None and most != a and most_n >= 3:
        out.append(Line(LEAD, f"{w.n(most, now)} led the most laps, {num_words(most_n)}, and comes away without "
                              "the trophy.", 1))
    return out


# ---------------------------------------------------------------- how a NASCAR booth measures things

CAR_LENGTH_M = 5.2                   # a Cup car, near enough


def _speed_ms(tr):
    """How fast the field is going (m/s): what turns a gap in seconds into car lengths."""
    sp = [c.speed for c in tr.cars.values() if c.speed and c.speed > 60 and not c.in_pit and not c.gone]
    if sp:
        return statistics.median(sp) / 3.6
    best = [c.best for c in tr.cars.values() if c.best]
    if best and getattr(tr, "track_len", None):
        return tr.track_len / statistics.median(best)
    return 50.0


def gap_say(tr, s):
    """A gap the NASCAR way: inside a second it's car lengths ('a car length', 'four car lengths'), beyond that
    seconds ('a second and a half')."""
    if s is None or s >= 0.95:
        return _gap_words(s)
    lengths = s * _speed_ms(tr) / CAR_LENGTH_M
    if lengths < 0.35:
        return "inches"
    if lengths < 0.75:
        return "half a car length"
    if lengths < 1.5:
        return "a car length"
    n = int(round(lengths))
    return f"{num_words(n)} car lengths"


def _mph_words(n):
    n = int(round(n))
    if n < 100:
        return num_words(n)
    h, r = divmod(n, 100)
    return f"{num_words(h)} " + ("hundred" if r == 0 else f"oh {num_words(r)}" if r < 10 else num_words(r))


def lap_mph(tr, t):
    """A lap time as a speed in mph, when the track's length is known (None if not, or if it makes no sense)."""
    tl = getattr(tr, "track_len", None)
    if not t or not tl or abs(tl - 4000.0) < 1e-6:            # 4000 m is the tracker's stand-in, not a real length
        return None
    mph = tl / t * 2.23694
    return mph if 25 < mph < 260 else None


def lap_say(tr, t):
    """A lap as a NASCAR booth says it: 'one ninety-one mile an hour lap' (speed, from the track length), or the
    time if the length isn't known."""
    mph = lap_mph(tr, t)
    return f"{_mph_words(mph)} mile an hour lap" if mph else lap_time_words(t)


def nn(w, cid, now=None):
    """A driver as a NASCAR booth says them: 'the 24 of Byron' now and then when the number is known, else the
    name."""
    name = w.n(cid, now)
    c = w.tr.car(cid)
    if c is None:
        return name
    num = c.number
    if not num and getattr(w, "roster", None) is not None and w.cfg.get("roster_file"):
        ent = w.roster.find(c)
        num = ent["number"] if ent else None
    num = str(num or "").strip().lstrip("#")
    if num and not name.lower().startswith("the ") and w.chance(0.45):
        return f"the {num} of {name}"
    return name


def _spot(pos):
    """'the lead', 'second', 'the final spot in the top ten'..."""
    return "the lead" if pos == 1 else place(pos)


def _zone(pos, was=None):
    """'into the top five' when a car has just crossed into it."""
    for z in (5, 10, 15, 20):
        if pos <= z and (was is None or was > z):
            return f"into the top {num_words(z)}"
    return ""


# ---------------------------------------------------------------- the race, called like an oval broadcast

def overtake_lines(w, e, now):
    """The pass: a run, side by side, cleared. A lead change is counted, a move into the top five or ten said."""
    d = e.data
    a, victims = e.cars[0], list(e.cars[1:])
    b = victims[0] if victims else None
    an, bn = nn(w, a, now), (w.n(b, now) if b is not None else "the car ahead")
    pos = d.get("pos", 0)
    ca, cb = w.tr.car(a), (w.tr.car(b) if b is not None else None)
    if d.get("lead"):
        n = w.lead_changes + 1
        nth = f"That's the {ordinal_words(n)} lead change of the day." if n >= 2 else "First lead change of the day!"
        return [Line(LEAD, w.pick("nas_ovt_lead", [
            f"We have a new leader! {an} gets by {bn} and takes the top spot!",
            f"{an} has a run on {bn}... side by side... and {an} clears for the lead!",
            f"Here comes {an}! Gets the run, makes the pass, and {an} leads the race!",
            f"There's the pass for the lead! {an} drives by {bn}!",
        ]), 3), Line(ANALYST, w.pick("nas_ovt_lead_a", [
            f"{nth} {bn} just couldn't hold that lane.",
            f"{nth} {an} has been the best car on this run, and now it shows.",
            f"{nth} Now {an} gets the clean air. That's a big deal out front.",
            f"{nth} {bn} got loose off the corner and {an} pounced.",
        ]), 2)]
    if d.get("multi") and len(victims) >= 2:
        names = ", ".join(w.n(v, now) for v in victims[:-1]) + f" and {w.n(victims[-1], now)}"
        zone = _zone(pos)
        return [Line(LEAD, w.pick("nas_ovt_multi", [
            f"Look at {an}! Picks off {names} in one shot, up to {place(pos)}!",
            f"{an} is on the move! Three-wide and {an} comes out of it past {names}!",
        ]), 3), Line(ANALYST, w.pick("nas_ovt_multi_a", [
            f"That car is hooked up right now. {an} is driving up through the field" +
            (f", {zone}." if zone else "."),
            "When the car's that good, the holes just open up for you.",
        ]), 2)]
    zone = _zone(pos, pos + 1)
    if ca and ca.player:
        return [Line(LEAD, w.pick("nas_ovt_me", [
            f"{an} gets the run on {bn}... and clears! {place(pos).capitalize()} for {w.n(a, now)}!",
            f"{an} makes the pass on {bn}" + (f", {zone}!" if zone else f", up to {place(pos)}!"),
            f"Side by side with {bn}, and {an} wins the drag race! {place(pos).capitalize()}!",
        ]), 3), Line(ANALYST, w.pick("nas_ovt_me_a", [
            "Got a great run off the corner, and that's all you need.",
            f"That car is coming to {w.n(a, now)}. Long run speed is showing now.",
            "Patient, set it up a lap early, and made it stick. That's racing.",
        ]), 2)]
    if cb and cb.player:
        return [Line(LEAD, w.pick("nas_ovt_vs_me", [
            f"{an} gets by {bn}. {bn} drops back to {place(pos + 1)}.",
            f"{bn} loses a spot, {an} clears for {place(pos)}.",
        ]), 2), Line(ANALYST, w.pick("nas_ovt_vs_me_a", [
            f"{bn} is going to have to find some speed. The car's just not turning like it was.",
            f"{bn} couldn't hold the bottom there. Need to get that spot back on the restart, or on pit road.",
        ]), 1)]
    if d.get("battle_len", 0) >= 60:
        return [Line(LEAD, w.pick("nas_ovt_long", [
            f"Finally! After all those laps on the bumper, {an} clears {bn} for {place(pos)}!",
            f"{an} gets it done! Took a while, but {an} is by {bn} for {place(pos)}.",
        ]), 3), Line(ANALYST, w.pick("nas_ovt_long_a", [
            f"{bn} had been protecting that bottom lane all run. Couldn't do it forever.",
            "Wore the tires out defending, and that's the price.",
        ]), 2)]
    energy = 3 if pos <= 3 else 2
    return [Line(LEAD, w.pick("nas_ovt", [
        f"{an} gets by {bn} for {place(pos)}.",
        f"{an} clears {bn}" + (f" and moves {zone}." if zone else f", now {place(pos)}."),
        f"{an} with a run on {bn}... and completes the pass for {place(pos)}.",
        f"Side by side for {place(pos)}, and {an} wins that one.",
        f"Nice move by {an}, takes {place(pos)} away from {bn}.",
    ]), energy), Line(ANALYST, w.pick("nas_ovt_a", [
        f"{bn} is starting to fade a little. Tires are going away.",
        f"{an} has the better car on this run. You could see that coming for a few laps.",
        f"{bn} just got freed up getting into the corner, and {an} was right there.",
        f"That's track position {an} will want to keep when pit road opens.",
    ]), 1)]


def ev_battle(w, e, now):
    b, a = e.cars
    bn, an = nn(w, b, now), w.n(a, now)
    pos, gap = e.data.get("pos", 0), w.gw(e.data.get("gap"))
    what = _spot(pos)
    if w._threaded("battle", (a, b)):
        return []
    w.covered(("battle", (a, b)), now, window=60.0)
    return w._talk("nas_battle", [
        f"Here comes {bn}! Right on the back bumper of {an}, and that's for {what}.",
        f"{bn} has run down {an} for {what}. {gap.capitalize()} back and looking for a lane.",
        f"Good battle for {what}: {an} out front, {bn} looking high, looking low.",
        f"{bn} is all over {an} for {what}, {gap} between them." if (e.data.get("gap") or 9) * _speed_ms(w.tr)
        / CAR_LENGTH_M <= 3 else f"{bn} is {gap} back of {an} for {what}, and coming.",
    ], [
        f"{bn} is taking the air off {an}'s spoiler. That makes {an} loose getting in.",
        f"Dirty air is the problem for {bn}. Get that close and the front end just washes out.",
        f"If {bn} gets a good run off the corner, this one's over.",
        f"{an} has to run a perfect line now. One bobble and {bn} is right there.",
        f"Watch {bn} try the other lane. When one line won't work, you go find another.",
    ], energy=2)


def ev_closing(w, e, now):
    b, a = e.cars
    d = e.data
    bn, an = nn(w, b, now), w.n(a, now)
    return w._talk("nas_closing", [
        f"{bn} is reeling in {an}, about {_gap_words(d['per_lap'])} a lap. Down to {w.gw(d['gap'])} now.",
        f"{bn} is the fastest car on the track right now, closing on {an}, {w.gw(d['gap'])} back.",
        f"Keep an eye on {bn}. Chewing into the gap to {an} every lap.",
    ], [
        f"{bn} has the long run speed. {an}'s tires are starting to go away.",
        f"{an} is going to see that car in the mirror real soon.",
        "This is where the setup shows. Some cars are fast for ten laps, some are fast for forty.",
    ])


def ev_stretch(w, e, now):
    a, b = e.cars
    return [Line(LEAD, w.pick("nas_stretch", [
        f"{nn(w, a, now)} is checking out. {w.gw(e.data['gap']).capitalize()} the lead over {w.n(b, now)}.",
        f"Clean air is king, and {w.n(a, now)} has it. {w.gw(e.data['gap']).capitalize()} to the good.",
    ]), 1), Line(ANALYST, w.pick("nas_stretch_a", [
        f"{w.n(b, now)} needs a caution. That's the only thing that closes this up.",
        "Out front by yourself, the car's just so much better. Everybody behind is in dirty air.",
    ]), 1)]


def ev_battle_over(w, e, now):
    a, b = e.cars
    first = Line(LEAD, w.pick("nas_bover", [
        f"{w.n(a, now)} has cleared out from {w.n(b, now)}. That fight's done for now.",
        f"{w.n(b, now)} fades back from {w.n(a, now)}.",
    ]), 1)
    settle = w._settle(a, b, now)
    if settle:
        return [first] + settle
    return [first, Line(ANALYST, w.pick("nas_bover_a", [
        f"{w.n(b, now)} burned the tires up trying. Now it's about saving what's left.",
        "Spent too much running in that dirty air. That's what it does to you.",
    ]), 1)]


def ev_fastest_lap(w, e, now):
    a = e.cars[0]
    t = w.ltw(e.data.get("time"))
    c = w.tr.car(a)
    if c and c.player:
        return [Line(LEAD, w.pick("nas_fl_me", [
            f"{w.n(a, now)} turns the fastest lap of the race! A {t}!",
            f"Nobody's been quicker today. {w.n(a, now)}, a {t}.",
        ]), 2)]
    out = [Line(LEAD, w.pick("nas_fl", [
        f"Fast lap of the race now belongs to {nn(w, a, now)}, a {t}.",
        f"{w.n(a, now)} just ripped off a {t}, fastest of anybody so far.",
        f"Quickest lap of the day for {nn(w, a, now)}, a {t}.",
    ]), 1)]
    if w.chance(0.4):
        out.append(Line(ANALYST, w.pick("nas_fl_a", [
            "That's what fresh tires will do for you.",
            "The car's coming in. Those are the laps that win races late.",
        ]), 1))
    return out


def ev_personal_best(w, e, now):
    a = e.cars[0]
    return [Line(ANALYST, w.pick("nas_pb", [
        f"{w.n(a, now)} picking up speed, a {w.ltw(e.data['time'])}. Best lap of the day for that team.",
        f"Whatever they changed on that last stop is working. {w.n(a, now)}, a {w.ltw(e.data['time'])}.",
    ]), 1)]


def ev_pit_in(w, e, now):
    a = e.cars[0]
    pos = e.data.get("pos", 0)
    c = w.tr.car(a)
    w.covered(("pits", "cycle"), now, window=8.0)
    nm = nn(w, a, now)
    under = w.tr.flag in CAUTION_FLAGS
    when = "under caution" if under else "under green"
    if w._mia and ((c and c.player) or pos <= 3):
        throw = w.pick("nas_pin_throw", [
            f"The leader is coming to pit road! {w.pit_name}?" if pos == 1 else
            f"{nm} brings it down pit road from {place(pos)}. {w.pit_name}?",
            f"Let's go down to {w.pit_name} on pit road. {w.n(a, now)} is in.",
        ])
        report = w.pick("nas_pin_report", [
            f"{w.n(a, now)} on pit road {when}. The crew is over the wall, and this is a big one for track position.",
            f"Here they come, {w.lead_name}. Tires and fuel for {w.n(a, now)}, and the crew chief has been "
            "thinking about this stop for a few laps.",
            f"In the box, {w.n(a, now)}. Jack's up. This crew has been good all day.",
        ])
        return [Line(LEAD, throw, 2), Line(PIT, report, 2)]
    who = w.pit_voice()
    if pos == 1:
        return [Line(who, w.pick("nas_pin_lead", [f"The leader {w.n(a, now)} is down pit road.",
                                                    f"{nm} brings the lead car to pit road."]), 2)]
    return [Line(who, w.pick("nas_pin", [
        f"{nm} is down pit road from {place(pos)}.",
        f"{w.n(a, now)} comes to pit road {when}, was running {place(pos)}.",
        f"Green flag stop for {w.n(a, now)}." if not under else f"{w.n(a, now)} takes the opportunity to pit.",
    ]), 1)]


def ev_pit_out(w, e, now):
    """The standard report (time on pit road, against the others), then what a NASCAR pit reporter leads with:
    spots gained or lost on pit road."""
    out = w.ev_pit_out(e, now)
    d = e.data
    pos, was = d.get("pos", 0), d.get("was")
    if not (pos and was) or not out:
        return out
    a = e.cars[0]
    under = w.tr.flag in CAUTION_FLAGS
    if under and pos < was:
        out.insert(1, Line(PIT if w._mia else ANALYST, w.pick("nas_pout_gain", [
            f"Great work by that crew! {w.n(a, now)} picks up {plural(was - pos, 'spot')} on pit road.",
            f"That pit crew just won {w.n(a, now)} {plural(was - pos, 'spot')}. Off pit road in {place(pos)}.",
        ]), 2))
    elif under and pos > was:
        out.insert(1, Line(PIT if w._mia else ANALYST, w.pick("nas_pout_lose", [
            f"Lost {plural(pos - was, 'spot')} on pit road for {w.n(a, now)}. The crew chief won't be happy.",
            f"A slow one for {w.n(a, now)}, {plural(pos - was, 'spot')} gone on pit road.",
        ]), 1))
    return out


def ev_stopped(w, e, now):
    a = e.cars[0]
    pos = e.data.get("pos", 0)
    w._remember(a, f"the car that stopped on track from {place(pos)}", now)
    c = w.tr.car(a)
    nm = w.n(a, now)
    if c and c.player:
        return [Line(LEAD, w.pick("nas_stop_me", [f"Trouble for {nm}! The car has stopped on the track!",
                                                    f"Oh no! {nm} is stopped!"]), 3),
                Line(ANALYST, "Worst possible time for that. Let's see if they can get it refired.", 2)]
    return [Line(LEAD, w.pick("nas_stop", [
        f"Trouble! {nm} is stopped on the track, was running {place(pos)}!",
        f"{nn(w, a, now)} is in trouble! Stopped on the race track!",
        f"Somebody's stopped... it's {nm}!",
    ]), 3), Line(ANALYST, w.pick("nas_stop_a", [
        "That's going to bring out the caution. Crew chiefs, get ready.",
        "A car stopped on track, they've got to throw the yellow for that.",
        f"Big trouble for {nm}. That's a lot of points gone.",
    ]), 2)]


def ev_recovered(w, e, now):
    a = e.cars[0]
    pos = e.data.get("pos", 0)
    return [Line(LEAD, w.pick("nas_rec", [f"{w.n(a, now)} gets it refired and going again, down to {place(pos)}.",
                                           f"{w.n(a, now)} is rolling again, now {place(pos)}."]), 1),
            Line(ANALYST, w.pick("nas_rec_a", [
                "At least they're going. Get it to pit road and let the crew look it over.",
                "Long way to go. Save what you can and get some laps.",
            ]), 1)]


def ev_moment(w, e, now):
    a = e.cars[0]
    loss = w.gw(e.data.get("loss"))
    w._remember(a, f"the car that lost {loss} in that moment earlier", now)
    nm = w.n(a, now)
    return [Line(LEAD, w.pick("nas_mom", [
        f"{nm} got loose! Saved it, but that cost {loss}.",
        f"Whoa, {nm} is sideways! Caught it, but lost {loss} there.",
        f"Trouble for {nm}, lost a bunch of time, about {loss}.",
    ]), 2), Line(ANALYST, w.pick("nas_mom_a", [
        "Got loose up off the corner and had to gather it up. Great save, honestly.",
        "Got into the marbles, and up the race track it went. Lucky that's all it cost.",
        f"{nm} needs to check that car over. That's the kind of moment that flat-spots a tire.",
    ]), 1)]


def ev_retired(w, e, now):
    a = e.cars[0]
    pos = e.data.get("pos", 0)
    nm = w.n(a, now)
    if e.data.get("why") == "dsq":
        return [Line(LEAD, f"NASCAR has disqualified {nm}.", 2)]
    return w._talk("nas_ret", [
        f"{nm} is headed to the garage. That's the end of the day for that team.",
        f"And {nn(w, a, now)} is out of the race, from {place(pos)}.",
        f"{nm}'s day is done. Out of the race.",
    ], [
        "Tough way to end it. That's a big hit in the points.",
        f"Heartbreaking for {nm}. They had a good car today.",
        "Nothing you can do about it now. Fix it and go to the next one.",
    ], energy=2)


def ev_contact(w, e, now):
    a, b = e.cars
    an, bn = w.n(a, now), w.n(b, now)
    return w._talk("nas_contact", [
        f"Contact! {an} and {bn} make contact!",
        f"They're banging doors! {an} and {bn}!",
        f"{an} gets into {bn}!",
    ], [
        "That's short track racing. Rubbing is part of it.",
        f"{bn} is going to remember that one.",
        "Let's see if anybody's got a tire rub. That's what you worry about after a hit like that.",
    ], energy=3)


def ev_accident(w, e, now):
    a = e.cars[0]
    return [Line(LEAD, w.pick("nas_acc", [f"Wreck! {w.n(a, now)} is in the wall!",
                                           f"Here's trouble! {nn(w, a, now)} has hit the wall!"]), 3),
            Line(ANALYST, "That's a hard hit. Let's make sure everybody's OK.", 2)]


def ev_penalty(w, e, now):
    a = e.cars[0]
    return [Line(LEAD, w.pick("nas_pen", [f"NASCAR has a penalty for {w.n(a, now)}.",
                                           f"Word from race control: a penalty on {w.n(a, now)}."]), 1)]


def ev_start_review(w, e, now):
    d = e.data
    out = []
    if d.get("leader") is not None:
        out.append(Line(LEAD, w.pick("nas_sr", [
            f"Through turns one and two, it's {nn(w, d['leader'], now)} out front.",
            f"{w.n(d['leader'], now)} gets the jump and leads them down the backstretch.",
        ]), 2))
    if d.get("gainer") is not None:
        out.append(Line(ANALYST, w.pick("nas_sr_gain", [
            f"Look at {w.n(d['gainer'], now)}, already up {plural(d['gained'], 'spot')}!",
            f"Great restart lane choice by {w.n(d['gainer'], now)}, {plural(d['gained'], 'spot')} gained.",
        ]), 2))
    if d.get("loser") is not None:
        out.append(Line(ANALYST, f"{w.n(d['loser'], now)} got hung out to dry, {plural(d['lost'], 'spot')} lost.", 1))
    me = d.get("me")
    if me is not None and me not in (d.get("gainer"), d.get("loser"), d.get("leader")):
        out.append(Line(LEAD if not out else ANALYST,
                        f"{w.n(me, now)} settles in {place(w.tr.pos_of(me))}.", 1))
    return out


def ev_lap_one(w, e, now):
    tr = w.tr
    p1, p2, p3 = tr.at(1), tr.at(2), tr.at(3)
    if not (p1 and p2):
        return []
    rest = f", {w.n(p3.id, now)} third" if p3 else ""
    return [Line(LEAD, w.pick("nas_lap1", [
        f"One lap complete. {nn(w, p1.id, now)} leads, {w.n(p2.id, now)} second{rest}.",
        f"They come by to complete lap one, and it's {w.n(p1.id, now)} out front.",
    ]), 1), Line(ANALYST, w.pick("nas_lap1_a", [
        "Long way to go. Right now it's about getting the car under you and not using up the tires.",
        "Everybody's still feeling it out. Ask me again in thirty laps.",
    ]), 1)]


def ev_laps_to_go(w, e, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    n = num_words(e.data["to_go"])
    out = [Line(LEAD, w.pick("nas_togo", [
        f"{n.capitalize()} to go, and {nn(w, p1.id, now) if p1 else 'the leader'} is out front.",
        f"{n.capitalize()} laps remaining here at {tr.track or 'the speedway'}. {w.n(p1.id, now) if p1 else 'The leader'} leads.",
    ]), 1)]
    if p2 and p2.interval is not None:
        out.append(Line(ANALYST, w.pick("nas_togo_a", [
            f"{w.n(p2.id, now)} is {w.gw(p2.interval)} back. Need a caution, or need to find something real quick.",
            f"{w.n(p2.id, now)} running second, {w.gw(p2.interval)} behind. Lapped traffic could be the difference.",
        ]), 1))
    return out


def ev_halfway(w, e, now):
    p1 = w.tr.at(1)
    led = getattr(w.tr, "laps_led", {}).get(p1.id, 0) if p1 else 0
    return [Line(LEAD, "We're halfway home" + (f", and {nn(w, p1.id, now)} leads" if p1 else "") + ".", 1),
            Line(ANALYST, (f"{w.n(p1.id, now)} has led {plural(led, 'lap')} so far. " if led >= 3 else "") +
                 w.pick("nas_half_a", ["The second half is where the crew chiefs earn their money.",
                                       "Now it's about the last two pit stops and having a car for the end."]), 1)]


def ev_final_lap(w, e, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    out = [Line(LEAD, w.pick("nas_final", [
        f"White flag! {nn(w, p1.id, now)} leads, one lap to go!",
        f"There's the white flag, and {w.n(p1.id, now)} is one lap from Victory Lane!",
    ]), 3)]
    if p2 and p2.interval is not None:
        if p2.interval < 1.0:
            out.append(Line(ANALYST, f"{w.n(p2.id, now)} is right there, {w.gw(p2.interval)}! Has to get a run "
                                     "off turn two!", 3))
        else:
            out.append(Line(ANALYST, f"{w.n(p2.id, now)} is {w.gw(p2.interval)} back. Bring it home, "
                                     f"{w.n(p1.id, now)}.", 2))
    return out


def ev_podium(w, e, now):
    a, pos = e.cars[0], e.data["pos"]
    return [Line(LEAD, w.pick("nas_pod", [f"{nn(w, a, now)} comes home {place(pos)}.",
                                           f"{place(pos).capitalize()} for {w.n(a, now)}."]), 1),
            Line(ANALYST, w.pick("nas_pod_a", [f"Solid run for {w.n(a, now)}. A top-five day, and good points.",
                                                f"{w.n(a, now)} will take that. A strong run all day."]), 1)]


def ev_player_finish(w, e, now):
    a = e.cars[0]
    pos, grid = e.data.get("pos", 0), e.data.get("grid")
    nm = w.n(a, now, full=True)
    out = [Line(LEAD, w.pick("nas_pfin", [f"{nm} takes the checkered flag in {place(pos)}.",
                                           f"And here's {nm}, across the line {place(pos)}."]), 2)]
    if pos and pos <= 10:
        out.append(Line(ANALYST, f"A top-{'five' if pos <= 5 else 'ten'} finish for {w.n(a, now)}" +
                        (f", up from {place(grid)} at the start." if grid and grid > pos else ".") +
                        " Good points day.", 2))
    elif grid and pos:
        out.append(Line(ANALYST, f"Started {place(grid)}, finished {place(pos)}. " +
                        ("Gained some spots, and that's something to build on." if pos < grid else
                         "Not the day they wanted. Regroup and go to the next one."), 1))
    return out


def ev_quali_lap(w, e, now):
    a = e.cars[0]
    d = e.data
    t = w.ltw(d.get("time"))
    if d.get("top"):
        return [Line(LEAD, w.pick("nas_q_top", [f"{nn(w, a, now)} goes to the top of the board, a {t}!",
                                                 f"New provisional pole! {w.n(a, now)}, a {t}!"]), 2)]
    if (d.get("pos") or 99) <= 10:
        return [Line(LEAD, f"{w.n(a, now)} with a {t}, that's good for {place(d['pos'])} right now.", 1)]
    return []


# ---------------------------------------------------------------- the quiet spells, the NASCAR way

def _rundown(w, now):
    """The classic: a run through the top ten."""
    tr = w.tr
    top = [tr.at(i) for i in range(1, 11)]
    top = [c for c in top if c is not None]
    if len(top) < 5:
        return []
    names = [w.n(c.id, now) for c in top]
    first = f"Let's take a look at the running order. {nn(w, top[0].id, now)} leads, {names[1]} second, " \
            f"{names[2]} third, {names[3]} fourth, {names[4]} fifth."
    out = [Line(LEAD, first, 1)]
    if len(top) >= 10:
        out.append(Line(LEAD, f"Sixth through tenth: {', '.join(names[5:9])} and {names[9]}.", 1))
    p2 = top[1]
    if p2.interval is not None:
        out.append(Line(ANALYST, w.pick("nas_rd_a", [
            f"And the gap from {names[0]} to {names[1]} is {w.gw(p2.interval)}.",
            f"{names[1]} is {w.gw(p2.interval)} back of the leader. That's the one to watch.",
        ]), 1))
    return out


def _clean_air(w, now):
    tr = w.tr
    p1, p2 = tr.at(1), tr.at(2)
    if not (p1 and p2) or p2.interval is None or p2.interval < 1.5:
        return []
    return [Line(LEAD, f"{nn(w, p1.id, now)} out front in clean air, {w.gw(p2.interval)} up on {w.n(p2.id, now)}.", 1),
            Line(ANALYST, w.pick("nas_ca_a", [
                "Out there by yourself, the car has all the downforce. Behind, everybody's fighting dirty air.",
                f"{w.n(p2.id, now)} can get to the bumper, but then the car goes tight in the turbulence. "
                "That's the problem.",
                "Clean air is worth a couple tenths a lap at least. That's why track position is everything.",
            ]), 1)]


def _falloff(w, now):
    """Whose lap times are dropping off most on this run: tires going away."""
    tr = w.tr
    best = None
    for c in tr.cars.values():
        lt = c.laps_timed[-4:]
        if len(lt) < 4 or c.in_pit or c.gone or not c.best:
            continue
        drop = lt[-1] - min(lt[:2])
        if drop > 0.25 and lt[-1] > lt[-2] > lt[-3] and (best is None or drop > best[0]):
            best = (drop, c)
    if best is None:
        return []
    drop, c = best
    return [Line(LEAD, f"The lap times are falling off for {nn(w, c.id, now)}, running {place(tr.pos_of(c.id))}.", 1),
            Line(ANALYST, w.pick("nas_fall_a", [
                f"That's the tires going away. {w.n(c.id, now)} is starting to slide around.",
                "Long run and the right rear is giving up. The crew chief is watching those numbers.",
                f"Getting tight in the center, would be my guess. {w.n(c.id, now)} is going to want an adjustment "
                "on the next stop.",
            ]), 1)]


def _laps_led(w, now):
    tr = w.tr
    cid, n = tr.most_led() if hasattr(tr, "most_led") else (None, 0)
    if cid is None or n < 5 or not tr.car(cid):
        return []
    leaders = len(getattr(tr, "leaders", []))
    return [Line(LEAD, f"{nn(w, cid, now)} has led {plural(n, 'lap')} today, the most of anybody.", 1),
            Line(ANALYST, (f"{plural(leaders, 'different leader').capitalize()} so far. " if leaders >= 3 else "") +
                 w.pick("nas_ll_a", ["Leading laps is nice. Leading the last one is what pays.",
                                     "That's been the dominant car. Question is whether anybody has anything for it."]),
                 1)]


def _lead_lap(w, now):
    tr = w.tr
    racing = [c for c in tr.cars.values() if not c.gone and c.status not in ("dnf", "dsq")]
    on = [c for c in racing if not c.laps_down]
    if len(racing) < 6 or len(on) == len(racing):
        return []
    return [Line(LEAD, f"Only {num_words(len(on))} cars left on the lead lap.", 1),
            Line(ANALYST, w.pick("nas_ll2_a", [
                "That free pass is going to be big for somebody when the next caution comes.",
                "Lapped traffic is going to start to play a role up front.",
            ]), 1)]


def _stage_talk(w, now):
    tr = w.tr
    ends = getattr(tr, "stage_ends", [])
    ld = tr.leader()
    if not ends or ld is None:
        return []
    nxt = next((x for x in ends if x > tr.leader_laps), None)
    if nxt is None or nxt - tr.leader_laps > 25:
        return []
    stage = ends.index(nxt) + 1
    return [Line(LEAD, f"{plural(nxt - tr.leader_laps, 'lap').capitalize()} to go in stage {num_words(stage)}, "
                       f"and {nn(w, ld.id, now)} leads it.", 1),
            Line(ANALYST, w.pick("nas_stg_a", [
                "Some of these teams will play strategy here. Pit just before the stage ends and you get track "
                "position for the next one.",
                "Every point matters at the end of the year. Nobody's giving up a stage spot cheap.",
            ]), 1)]


def _fuel(w, now):
    """The viewer's fuel against the laps left, when the sim says."""
    tr = w.tr
    me = tr.player()
    fl = (getattr(tr, "cond", {}) or {}).get("fuel_laps")
    left = w._laps_left()
    if me is None or fl is None or left is None or left < 3:
        return []
    short = fl < left
    return [Line(LEAD, f"Let's check in on {nn(w, me.id, now)}, running {place(tr.pos_of(me.id))}.", 1),
            Line(ANALYST, f"By our numbers, fuel for about {num_words(int(fl))} laps, and {num_words(left)} to go. " +
                 ("They're going to need one more stop, or a caution to save some." if short else
                  "They can make it on fuel from here."), 1)]


def _check_in(w, now):
    tr = w.tr
    me = tr.player()
    if me is None or me.in_pit:
        return []
    p = tr.pos_of(me.id)
    ahead = tr.at(p - 1) if p > 1 else None
    out = [Line(LEAD, w.pick("nas_ci", [f"Let's check in on {nn(w, me.id, now)}, running {place(p)}.",
                                         f"{w.n(me.id, now)} is up to {place(p)}." if p <= 10 else
                                         f"{w.n(me.id, now)} running {place(p)} right now."]), 1)]
    if ahead and me.interval is not None:
        out.append(Line(ANALYST, w.pick("nas_ci_a", [
            f"{w.gw(me.interval).capitalize()} back of {w.n(ahead.id, now)}. Car looks pretty good on the long run.",
            f"Chasing {w.n(ahead.id, now)}, {w.gw(me.interval)} up the road. Needs to get through the center a "
            "little better.",
        ]), 1))
    return out


FILLERS = (_rundown, _clean_air, _falloff, _laps_led, _lead_lap, _stage_talk, _fuel, _check_in)
FILLER_GAP = 75.0                    # seconds before the same kind of segment comes round again


def filler(w, now, focus="balanced"):
    """A NASCAR segment for a quiet spell: the running order, clean air, tires falling off, laps led, the lead
    lap, the stage, fuel, a check-in on the viewer. [] when none fits right now."""
    told = w.__dict__.setdefault("_nas_told", {})
    opts = list(FILLERS)
    w.rng.shuffle(opts)
    if focus == "me":
        opts.sort(key=lambda f: f not in (_fuel, _check_in))
    for f in opts:
        if now - told.get(f.__name__, -1e9) < FILLER_GAP:
            continue
        lines = f(w, now)
        if lines:
            told[f.__name__] = now
            return lines
    return []


EVENTS = {"intro": ev_intro, "start": ev_start, "caution": ev_caution, "sc_in": ev_sc_in, "green": ev_green,
          "free_pass": ev_free_pass, "stage_to_go": ev_stage_to_go, "stage_end": ev_stage_end, "winner": ev_winner,
          "battle": ev_battle, "closing": ev_closing, "stretch": ev_stretch, "battle_over": ev_battle_over,
          "fastest_lap": ev_fastest_lap, "personal_best": ev_personal_best, "pit_in": ev_pit_in,
          "pit_out": ev_pit_out, "stopped": ev_stopped, "recovered": ev_recovered, "moment": ev_moment,
          "retired": ev_retired, "contact": ev_contact, "accident": ev_accident, "penalty": ev_penalty,
          "start_review": ev_start_review, "lap_one": ev_lap_one, "laps_to_go": ev_laps_to_go,
          "halfway": ev_halfway, "final_lap": ev_final_lap, "podium": ev_podium,
          "player_finish": ev_player_finish, "quali_lap": ev_quali_lap}


def event_lines(w, e, now):
    """NASCAR's own lines for this event, or None to use the standard ones."""
    f = EVENTS.get(e.kind)
    return f(w, e, now) if f else None


# ---------------------------------------------------------------- the words

_VOCAB = [
    # whole phrases first: how the road-racing booth says it -> how an oval booth does
    (r"Textbook\. Out late, on the brakes, and it stuck\.", "Textbook. Got the run and made it stick."),
    (r"Great work on the brakes\.", "Great run off the corner."),
    (r"\bthe run down to the first corner\b", "the run into turn one"),
    (r"\bthe first corner\b", "turn one"),
    (r"\bthe opening corners\b", "turns one and two"),
    (r"\bcorners\b", "turns"),
    (r"\bbraking zone\b", "corner entry"),
    (r"\ba braking point\b", "your entry"),
    (r"\bon the brakes\b", "into the corner"),
    (r"\b(is |are )?filling the mirrors\b", lambda m: (m.group(1) or "") + "right on the bumper"),
    (r"\bin the mirrors\b", "on the back bumper"),
    (r"\bthe mirrors\b", "the car behind"),
    (r"\bthe tow down the straight\b", "the draft down the backstretch"),
    (r"\bin the tow\b", "in the draft"),
    (r"\bdown the straight\b", "down the backstretch"),
    (r"\bPurple for\b", "Fastest lap for"),
    (r"\binto the box\b", "into the pit stall"),
    (r"\b(up and down|through) the lane\b", lambda m: ("up and down" if m.group(1).lower().startswith("up") else "down")
     + " pit road"),
    (r"\bthe lane\b", "pit road"),
    (r"\bThe cars are on the grid\b", "The field is lined up"),
    (r"\bThe grid is formed\b", "The field is set"),
    (r"\bup from the grid\b", "up from the start"),
    (r"\bon the grid\b", "at the start"),
    (r"\bof the grid\b", "of the field"),
    (r"\bthe grid\b", "the field"),
    (r"\bproperly\b", "the right way"),
    (r"\bproper\b", "real"),
    (r"\blovely\b", "great"),
    (r"\bbrilliant\b", "awesome"),
    (r"\bcheeky\b", "sneaky"),
    (r"\bBloody hell\b", "Holy cow"),
    (r"\bbloody\b", "damn"),
    (r"\bsod all\b", "squat"),
    (r"\bmental\b", "insane"),
    (r"\bin the bin\b", "done"),
    (r"\bbins it\b", "wrecks it"),
    (r"\bin the paddock\b", "in the garage area"),
    (r"\bBarely a car's width in it\.", "That's close."),
    (r"\b(one )?lock-up\b", lambda m: (m.group(1) or "") + "bobble"),
    (r"\bgearbox\b", "back bumper"),
    (r"\ba blanket and a cup of tea\b", "a cold drink and a ride back to the hauler"),
    (r"\bWeetabix\b", "Wheaties"),
    (r"\bbiscuits\b", "donuts"),
    (r"\ban estate car\b", "a pickup truck"),
    (r"\byour estate car\b", "your pickup truck"),
    (r"\bwith a big boot\b", "with a big trunk"),
    (r"\bthe long way round the roundabouts\b", "the long way around everywhere"),
    (r",\s*mind\.", "."),
    (r"lights out,? and away we go", "green flag, here we go"),
    (r"five red lights\W*and", "green flag, and"),
    (r"\blights out\b", "green flag"),
    (r"chequered", "checkered"),
    (r"\bpit[- ]lane reporter\b", "pit reporter"),
    (r"\binto the pit[- ]lane\b", "onto pit road"),
    (r"\b(in|down) the pit[- ]lane\b", "on pit road"),
    (r"\bthe pit[- ]lane\b", "pit road"),
    (r"\bpit[- ]lane\b", "pit road"),
    (r"\bthe tow\b", "the draft"),
    (r"\bslipstream\b", "draft"),
    (r"\bvirtual safety car\b", "caution"),
    (r"\bsafety car\b", "pace car"),
    (r"\bfull course yellow\b", "caution"),
    (r"\btyres\b", "tires"),
    (r"\btyre\b", "tire"),
    (r"\bthe stewards\b", "race control"),
    (r"\bstewards\b", "officials"),
    (r"\bon the podium\b", "in the top three"),
    (r"\ba podium\b", "a top-three finish"),
    (r"\bpodium\b", "top three"),
    (r"\bthe circuit\b", "the track"),
    (r"\bcircuit\b", "track"),
    (r"\bneutralised\b", "under caution"),
    (r"\bcolour\b", "color"),
    (r"\bfavourite\b", "favorite"),
    (r"\bmetres\b", "meters"),
]
_VOCAB = [(re.compile(p, re.I), r) for p, r in _VOCAB]


def _match_case(src, new):
    if src[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def vocab(text: str) -> str:
    """The booth's words as an American oval broadcast says them."""
    for rx, rep in _VOCAB:
        text = rx.sub(lambda m: _match_case(m.group(0), rep(m) if callable(rep) else rep), text)
    return text


# ---------------------------------------------------------------- for the AI

def brief(tr, writer) -> list:
    """Lines for the AI's race brief: how to sound, and the NASCAR story so far."""
    out = ["BROADCAST: this is a NASCAR-style stock car broadcast, so call it like a FOX or NBC NASCAR booth, in "
           "American English and NASCAR's words: caution and green flag, restarts, pit road, the pace car, the "
           "draft, side by side, three-wide, the lead lap, the free pass (the lucky dog), stages and stage points, "
           "laps led, the white flag, the checkered flag, Victory Lane, crew chiefs. Never say safety car, pit "
           "lane, chequered, tyres, podium or lights out. Two or four tires and fuel-only stops are strategy talk, "
           "but never say what a team actually did unless the data says so."]
    ends = getattr(tr, "stage_ends", [])
    if ends:
        done = getattr(tr, "stage_winners", [])
        bits = []
        for i, lap in enumerate(ends):
            won = f", won by {writer.n(done[i])}" if i < len(done) and tr.car(done[i]) else ""
            bits.append(f"stage {i + 1} ends lap {lap}{won}")
        bits.append(f"final stage to lap {tr.race_laps}")
        lines = "; ".join(bits)
        out.append(f"STAGES: {lines}. Now running stage {min(getattr(tr, 'stage', 1), len(ends) + 1)}. The top ten "
                   "at each stage end score points (ten down to one), the stage winner a playoff point too.")
    led = sorted(((n, cid) for cid, n in getattr(tr, "laps_led", {}).items() if tr.car(cid)), reverse=True)
    if led:
        out.append("LAPS LED: " + ", ".join(f"{writer.n(cid)} {n}" for n, cid in led[:6]) +
                   f". {plural(len(getattr(tr, 'leaders', [])), 'different leader')}.")
    if getattr(tr, "cautions", 0):
        out.append(f"CAUTIONS: {tr.cautions} so far" + (" (under caution now)" if tr.flag in CAUTION_FLAGS else "") +
                   ".")
    return out


def describe(e, writer):
    """An event as one sentence for the AI, or None for the standard description."""
    n = writer.n
    if e.kind == "stage_end":
        return f"{n(e.cars[0])} won stage {e.data['stage']}"
    if e.kind == "stage_to_go":
        return f"three laps to go in stage {e.data['stage']}"
    if e.kind == "free_pass":
        return f"{n(e.cars[0])} got the free pass (lucky dog), back on the lead lap at the restart"
    if e.kind == "caution":
        return "caution, the yellow flag is out"
    if e.kind == "green":
        return "green flag, the restart"
    return None
