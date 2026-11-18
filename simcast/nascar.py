"""NASCAR mode: stock car races called the way an American oval broadcast calls them.

The race itself is followed exactly as in the whole-field mode (race.py); NascarTracker adds what a NASCAR booth
talks about on top: stages and their winners, laps led, the caution count, the free pass for the first car a lap
down, and the restart. The booth's own lines for those moments live here too (event_lines), and vocab() turns
the rest of the booth's words into the broadcast's own: pit road, the checkered flag, the pace car, tires.

It comes on by itself for stock cars (iRacing's NASCAR, ARCA, Late Models, Legends, Modifieds, and Assetto Corsa's
stock car mods) and on oval tracks (iRacing's track type; Assetto Corsa's track names and layouts), or always/never
from Settings > Booth > NASCAR mode. Assetto Corsa without the CrewChiefEx app shows only your car: that gets the
broadcast's words and voices, but stages and laps led need the whole field.
"""
from __future__ import annotations

import re

from . import persona
from .race import CAUTION_FLAGS, Event, RaceTracker
from .writer import ANALYST, LEAD, Line, gap_words, num_words, ordinal_words, place, plural

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
            if n == end and stage not in self._stage_told:
                self._stage_told.add(stage)
                self.stage = stage + 1
                self.stage_winners.append(leader.id)
                top = [cid for cid in self.order[:STAGE_POINTS] if cid in self.cars]
                last = stage == len(self.stage_ends)
                ev.append(Event("stage_end", now, 92, (leader.id,), {"stage": stage, "top": top, "final_next": last},
                                ttl=30, key=f"stage{stage}"))
            elif end - n == 3 and ("togo", stage) not in self._stage_told and end >= 10:
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
        out.append(Line(ANALYST, f"{w.n(p2.id, now)} is {gap_words(p2.interval)} back. Ten points for the stage "
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


EVENTS = {"intro": ev_intro, "start": ev_start, "caution": ev_caution, "sc_in": ev_sc_in, "green": ev_green,
          "free_pass": ev_free_pass, "stage_to_go": ev_stage_to_go, "stage_end": ev_stage_end, "winner": ev_winner}


def event_lines(w, e, now):
    """NASCAR's own lines for this event, or None to use the standard ones."""
    f = EVENTS.get(e.kind)
    return f(w, e, now) if f else None


# ---------------------------------------------------------------- the words

_VOCAB = [
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
        text = rx.sub(lambda m: _match_case(m.group(0), rep), text)
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
