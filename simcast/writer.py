"""What the commentators say: lines for every kind of race moment, written the way a TV booth talks, in three
voices: the lead (play-by-play), the analyst (colour) and the pit-lane reporter.

These lines are instant (no network), so the big moments are called the moment they happen; the AI booth
(ai.py) adds colour and analysis on top when it's switched on. Drivers are called by name; where the booth needs a
pronoun it reads the first name (persona.py), and a name that isn't clearly a man's or a woman's stays 'they'.
The booth's personality, random chat and sponsor mentions are in banter.py.
"""
from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass

from . import persona
from .banter import BanterMixin
from .race import CAUTION_FLAGS, call_name

LEAD, ANALYST, PIT = "lead", "analyst", "pit"
THREAD_GAP = 6.0         # seconds before a running conversation is moved on a beat
THREAD_TTL = 75.0        # a story left alone this long is dropped rather than half-finished
THREAD_MAX = 5           # conversations running at once
THREAD_BEATS = 4         # beats before a story is paid off
FLOOR_MAX = 3            # lines one voice may hold the floor before the other breaks in
PAIR_TTL = 60.0          # a fight just called out is left that long before it is worked again
BREAKS = {               # the line that breaks a run: the other voice gets a word in, the way people do
    ANALYST: ["Busy, isn't it?", "Yes, and look at this.", "Hard to keep up with.", "Right, and there's more."],
    LEAD: ["Go on.", "Yes, keep going.", "Right.", "Good point, and look at this."],
}
CALL_TTL = 300.0         # a prediction made on air is remembered this long, so the booth can come back to it


@dataclass
class Story:
    """One running conversation in the booth: a battle, a driver on the move, a pit phase. It is opened, moved
    on a beat at a time while it lasts, and paid off when it is done, so the booth follows a thread like a TV
    booth instead of firing off remarks."""
    kind: str
    key: tuple
    since: float
    last: float
    beat: int = 0
    mark: float = 0.0            # the figure the next beat compares against (a gap, a place)
    teased: bool = False         # somebody has already had a go about a call made on this one


@dataclass
class Line:
    who: str
    text: str
    energy: int = 1          # 0 calm, 1 engaged, 2 excited, 3 the big moments
    mood: str = ""           # how it's said: excited, angry, gutted, ... (persona.MOODS); "" lets the energy decide

    def as_dict(self):
        return {"who": self.who, "text": self.text, "energy": self.energy, "mood": self.mood}


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
        "twelve": "twelfth"}


_PLAIN_STARTS = {"maybe", "really", "see", "can", "is", "how", "who's", "what", "where", "still", "nothing",
                 "and", "so", "well", "right", "look", "not", "close", "yes", "no", "go", "it's", "there's"}


def _sentences(text: str) -> str:
    """Every sentence starts with a capital, however the line was put together ('... today. six cars ...')."""
    text = text[:1].upper() + text[1:]
    return re.sub(r"([.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


def num_words(n: int) -> str:
    n = int(n)
    if n < 0:
        return "minus " + num_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[o] if o else "")
    return str(n)


def ordinal_words(n: int) -> str:
    """5 -> 'fifth', 21 -> 'twenty-first'."""
    w = num_words(n)
    head, _, last = w.rpartition("-")
    if last in _ORD:
        last = _ORD[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return (head + "-" if head else "") + last


def pos_words(p: int) -> str:
    """How a position is said: 'the lead', 'second place', 'P twenty-three' for long grids."""
    if p <= 0:
        return "the pack"
    if p == 1:
        return "the lead"
    if p <= 30:
        return f"{ordinal_words(p)} place"
    return f"P{p}"


def place(p: int) -> str:
    """'second', 'fifth' (no 'place'), for 'running fifth'."""
    return ordinal_words(p) if 0 < p <= 30 else f"P{p}"


def gap_words(s) -> str:
    """Seconds as a commentator says them: 'three tenths', 'half a second', 'one point four seconds'."""
    if s is None:
        return "a gap"
    if s < 0.08:
        return "nothing at all"
    if s < 0.95:
        t = max(1, int(round(s * 10)))
        if t == 5:
            return "half a second"
        return "a tenth" if t == 1 else f"{num_words(t)} tenths"
    if s < 1.05:
        return "a second"
    if s < 10:
        whole, tenth = int(s), int(round((s - int(s)) * 10))
        if tenth == 10:
            whole, tenth = whole + 1, 0
        if tenth == 0:
            return f"{num_words(whole)} seconds"
        if tenth == 5 and whole == 1:
            return "a second and a half"
        return f"{num_words(whole)} point {num_words(tenth)} seconds"
    return f"{num_words(int(round(s)))} seconds" if s < 100 else f"over {int(s // 60)} minutes"


def lap_time_words(t) -> str:
    """69.23 -> 'one nine point two', 103.4 -> 'one forty-three point four', 58.71 -> 'fifty-eight point seven'."""
    if t is None:
        return "a quick one"
    m, sec = divmod(float(t), 60.0)
    tenth = int(round((sec - int(sec)) * 10))
    whole = int(sec)
    if tenth == 10:
        whole, tenth = whole + 1, 0
    if whole == 60:
        m, whole = m + 1, 0
    secw = num_words(whole) if whole >= 10 or m == 0 else f"oh {num_words(whole)}"
    if m == 0:
        return f"{secw} point {num_words(tenth)}"
    return f"{num_words(int(m))} {secw} point {num_words(tenth)}"


def plural(n, word):
    return f"{num_words(n)} {word}" + ("" if n == 1 else "s")


def mins_words(s) -> str:
    m = int(round(s / 60.0))
    if m <= 1:
        return "over a minute" if s >= 60 else "a few laps"
    return f"{num_words(m)} minutes"


class Writer(BanterMixin):
    """Turns Events (race.py) into Lines. Keeps track of which phrasings were used lately so the booth doesn't
    sound like a loop, and which drivers were mentioned (the director uses that to spread the airtime)."""

    def __init__(self, tracker, cfg=None, seed=None):
        self.tr = tracker
        self.cfg = cfg or {}
        self.rng = random.Random(seed)
        self._recent: dict = {}
        self._pick_i = 0
        self.stories: dict = {}
        self._last_prune = -1e9
        self.last_open = ""
        self.last_kind = ""
        self.last_ended = -1e9
        self.told_player = -1e9
        self.floor_who, self.floor_run = "", 0        # who spoke last, and for how many lines running
        self._said: deque = deque(maxlen=60)          # the last lines out, so a quiet spell doesn't repeat itself
        self.recent_pairs: dict = {}                 # battle pairs lately covered, so they aren't reopened
        # the booth's memory: who said what would happen, how long the stops took, moments worth coming back to
        self.calls: dict = {}                        # frozenset(pair) -> {LEAD|ANALYST: car picked, "t": when}
        self.lane_times: dict = {}                   # car -> seconds in the pit lane, last stop
        self.moments: dict = {}                      # car -> (what happened, when), for callbacks later on
        self._told_pace: dict = {}                   # lap-time comparisons lately given, so they aren't repeated
        self.passes = 0                              # overtakes called this race, and lead changes
        self.lead_changes = 0
        self.asides_done: set = set()                # side conversations already had this race
        self.last_aside = -1e9

    # ------------------------------------------------------------------ helpers
    @property
    def lead_name(self):
        return (self.cfg.get("lead_name") or "Ben").split()[0]

    @property
    def analyst_name(self):
        return (self.cfg.get("analyst_name") or "Kate").split()[0]

    @property
    def pit_name(self):
        return (self.cfg.get("pit_name") or "Mia").split()[0]

    def pit_voice(self):
        return PIT if self.cfg.get("pit_reporter", True) else LEAD

    def n(self, cid, now=None, full=False):
        """A driver's name as said on air (and marks them as mentioned)."""
        c = self.tr.car(cid)
        if c is None:
            return "a car"
        if now is not None:
            c.mentioned = now
        if c.player and (self.cfg.get("driver_name") or "").strip():
            nm = self.cfg["driver_name"].strip()
            return nm if full else nm.split()[-1] if len(nm.split()) > 1 else nm
        ent = self.roster.find(c) if getattr(self.cfg, "get", None) and self.cfg.get("roster_file") else None
        if ent and ent["say"]:
            return ent["say"]                               # the roster says how to say it
        if full:
            return c.name
        same = [o for o in self.tr.cars.values() if o.call == c.call and o.id != c.id and not o.gone]
        return c.name if same else c.call

    def pick(self, kind, options):
        """A random option not used in the last few times for this kind."""
        if not options:
            return None
        recent = self._recent.setdefault(kind, deque(maxlen=max(1, min(4, len(options) - 1))))
        idx = [i for i in range(len(options)) if i not in recent] or list(range(len(options)))
        i = self.rng.choice(idx)
        recent.append(i)
        self._pick_i += 1
        return options[i]

    def chance(self, p):
        return self.rng.random() < p

    def book(self, lines):
        """These lines are going out: remember them, so a quiet spell doesn't say the same thing again."""
        for ln in lines or ():
            self._said.append(ln.text)

    def heard(self, line):
        """A line has actually gone on air. The floor follows what was really said, so an interrupt (which drops
        whatever had not been reached) can't leave the booth thinking a voice has had more of it than it has."""
        if line.who in (LEAD, ANALYST):
            self.floor_run = self.floor_run + 1 if line.who == self.floor_who else 1
            self.floor_who = line.who

    def floor(self, lines):
        """No voice holds the floor for long: where one would run on past FLOOR_MAX lines, the other breaks in on
        what has been said, so a stack of calls still lands as two people at a microphone. Booking is left to
        book(), called once the lines are actually queued."""
        out, who, run = [], self.floor_who, self.floor_run
        vocab = None
        if getattr(self.tr, "nascar", False):
            from .nascar import vocab
        for ln in lines or []:
            if not ln:
                continue
            ln = Line(ln.who, _sentences(vocab(ln.text) if vocab else ln.text), ln.energy, ln.mood)
            if ln.who in (LEAD, ANALYST) and ln.who == who and run >= FLOOR_MAX:
                other = ANALYST if ln.who == LEAD else LEAD
                out.append(Line(other, self.pick("brk_" + other, BREAKS[other]), 1))
                who, run = other, 1
            out.append(ln)
            if ln.who not in (LEAD, ANALYST):
                continue
            if ln.who == who:
                run += 1
            else:
                who, run = ln.who, 1
        return out

    def _talk(self, kind, calls, answers=None, energy=1):
        """A lead's call and the answer to that exact call: the second voice picks up what the first said, so
        the pair lands as two people talking to each other, not two separate remarks."""
        call = self.pick(kind, calls)
        if call is None:
            return []
        out = [Line(LEAD, call, energy)]
        answer = self.pick(kind + "_a", answers)
        if answer:
            out.append(Line(ANALYST, answer, max(1, energy - 1)))
        return out

    def _race_length(self):
        tr = self.tr
        if tr.race_laps:
            return f"{num_words(tr.race_laps)} laps"
        if tr.time_left:
            return mins_words(tr.time_left)
        return "this race"

    def _lap_status(self):
        tr = self.tr
        ld = tr.leader()
        if ld is None:
            return ""
        lap = ld.laps + 1
        if tr.race_laps:
            return f"lap {num_words(min(lap, tr.race_laps))} of {num_words(tr.race_laps)}"
        return f"lap {num_words(lap)}"

    def _behind(self, cid):
        """(car behind, interval) for cid in the running order."""
        tr = self.tr
        p = tr.pos_of(cid)
        b = tr.at(p + 1)
        return (b, b.interval) if b is not None else (None, None)

    # ------------------------------------------------------------------ conversation helpers
    @staticmethod
    def _other(who):
        return ANALYST if who == LEAD else LEAD

    def _name(self, who):
        return {LEAD: self.lead_name, ANALYST: self.analyst_name, PIT: self.pit_name}.get(who, "")

    def _next_voice(self):
        """Whoever didn't speak last opens the next exchange, so a conversation is picked up, not restarted."""
        return ANALYST if self.floor_who == LEAD else LEAD

    @property
    def _mia(self):
        return bool(self.cfg.get("pit_reporter", True))

    def _addr(self, who, line, p=0.35):
        """Now and then, say the other person's name first, the way people at one microphone do."""
        if not self.chance(p):
            return line
        first = line.split(" ", 1)[0].strip(",.?!").lower()
        if first in _PLAIN_STARTS:                            # "Maybe, ..." -> "Kate, maybe, ..."; names keep caps
            line = line[:1].lower() + line[1:]
        return f"{self._name(who)}, {line}"

    def _pace(self, a, b):
        """(quicker car, margin) on the last lap between two cars; (None, 0) if level; None if unknown."""
        ca, cb = self.tr.car(a), self.tr.car(b)
        if not (ca and cb and ca.last and cb.last) or ca.in_pit or cb.in_pit or ca.pit_lap or cb.pit_lap:
            return None
        d = cb.last - ca.last
        if abs(d) > 1.0:
            return None                                       # not a like-for-like lap (traffic, a moment, a stop)
        if abs(d) < 0.1:
            return None, 0.0
        return (a, d) if d > 0 else (b, -d)

    def _laps_left(self):
        tr = self.tr
        ld = tr.leader()
        if not tr.race_laps or ld is None:
            return None
        return max(0, tr.race_laps - ld.laps)

    def _laps_left_words(self):
        n = self._laps_left()
        if n is None:
            return mins_words(self.tr.time_left) + " left" if self.tr.time_left else "plenty of race left"
        return "one lap left" if n <= 1 else f"{num_words(n)} laps left"

    def _read_fight(self, a, b, now):
        """A read on a fight from the lap times rather than a stock phrase: (answer, who's favoured, reason).
        The answer replies to a question; the reason stands on its own (for a call or a remark)."""
        an, bn = self.n(a, now), self.n(b, now)
        pc = self._pace(a, b)
        if pc is not None:                                    # the same lap-time fact isn't given twice running
            fact = (frozenset((a, b)), pc[0], round(pc[1], 1))
            if now - self._told_pace.get(fact, -1e9) < 45:
                pc = None
            else:
                self._told_pace[fact] = now
        if pc is None:
            return self.pick("read_none", [
                f"It's hard to call. {an} has the position, {bn} has to make something happen.",
                f"Position is everything here. {bn} has to find a way, {an} just has to not make a mistake.",
            ]), a, self.pick("reason_none", [
                f"{an} has the position, and {bn} hasn't shown me a way past yet.",
                f"{bn} has been there a while without a real look. {an} is placing the car well.",
                f"The car in front sets the terms in a fight like this, and {an} is doing it well.",
            ])
        fast, m = pc
        if fast is None:
            return self.pick("read_level", [
                "They're level on lap time, near enough to the hundredth. It comes down to who blinks first.",
                f"Nothing between them on pace. That suits {an}, who only has to keep the door shut.",
            ]), a, self.pick("reason_level", [
                "They're dead level on lap time, and level suits the car in front.",
                f"There's nothing between them on the clock, so {bn} needs {an} to make a mistake.",
                f"Same lap times, near enough. {bn} can't just drive past on pace.",
            ])
        if fast == b:
            return self.pick("read_chaser", [
                f"On pace, yes. {bn} was {gap_words(m)} quicker on that last lap. It's about finding the place to do it.",
                f"I think so. {bn} has the speed, {gap_words(m)} a lap quicker. It's whether {an} gives an opening.",
                f"The stopwatch says {bn}. That last lap was {gap_words(m)} better than {an}'s.",
            ]), b, self.pick("reason_chaser", [
                f"{bn} was {gap_words(m)} quicker on that last lap.",
                f"The stopwatch says so, {gap_words(m)} a lap quicker.",
            ])
        return self.pick("read_leader", [
            f"I'm not convinced. {an} actually had the better of that last lap, by {gap_words(m)}. {bn} is close, not quicker.",
            f"Close isn't the same as quicker. {an} was {gap_words(m)} faster last time round.",
            f"Not on that evidence. {an} found {gap_words(m)} on that lap. That's a driver in control.",
        ]), a, self.pick("reason_leader", [
            f"{an} actually had the better of that last lap, by {gap_words(m)}.",
            f"{bn} is close, but not quicker. {an} was {gap_words(m)} faster last time round.",
        ])

    def _call(self, pair, who, pick, now):
        """Put a prediction on record, so the booth can come back to it when the fight is settled."""
        rec = self.calls.setdefault(frozenset(pair), {"t": now})
        rec[who] = pick
        rec["t"] = now

    def _settle(self, winner, loser, now):
        """A fight has been settled: whoever called it gets the credit, whoever didn't takes some stick."""
        rec = self.calls.pop(frozenset((winner, loser)), None)
        if not rec or now - rec.get("t", -1e9) > CALL_TTL:
            return []
        lc = self.tr.car(loser)
        wc = self.tr.car(winner)
        if any(c is None or c.in_pit or (c.trouble_t is not None and c.trouble_t >= rec["t"]) or
               (c.pit_in_t is not None and c.pit_in_t >= rec["t"]) for c in (lc, wc)):
            return []                                         # settled by a stop or a spin, not by the racing
        right = [w for w in (LEAD, ANALYST) if w in rec and rec[w] == winner]
        wrong = [w for w in (LEAD, ANALYST) if w in rec and rec[w] != winner]
        hook = getattr(self, "on_call_settled", None)
        if hook:
            for w in right + wrong:
                hook(self._name(w), w in right)                # into the booth's memory across races
        wn = self.n(winner, now)
        if right and wrong:
            r, w = right[0], wrong[0]
            return [Line(w, self.pick("settle_w", [
                f"All right, {self._name(r)}, you called that one.",
                f"Go on then, {self._name(r)}. Say it.",
                f"I'll admit it, {self._name(r)}. You were right about {wn}.",
            ]), 1), Line(r, self.pick("settle_r", [
                "I'm only going to mention it once.",
                "I did say. The lap times were telling us all along.",
                f"Told you. {wn} had it in hand the whole time.",
            ]), 1)]
        if right:
            r = right[0]
            return [Line(self._other(r), self.pick("settle_one_r", [
                f"And {self._name(r)} called that, to be fair.",
                f"You said {wn} would do it, {self._name(r)}.",
            ]), 1), Line(r, self.pick("settle_one_r_a", [
                "Somebody had to.",
                "It was always coming. The pace was there.",
            ]), 1)]
        if wrong:
            w = wrong[0]
            return [Line(self._other(w), self.pick("settle_one_w", [
                f"So much for that prediction, {self._name(w)}.",
                f"Didn't you say the opposite a few laps ago, {self._name(w)}?",
            ]), 1), Line(w, self.pick("settle_one_w_a", [
                "Yes, all right. I'll take that one on the chin.",
                f"I did. Credit to {wn}, that's a better drive than I gave it.",
            ]), 1)]
        return []

    def _due_call(self, now):
        """Go back to a fight somebody made a call on, once it has settled itself without a pass: the booth
        remembers what it said, and somebody gets the credit."""
        tr = self.tr
        for pair, rec in list(self.calls.items()):
            if now - rec.get("t", now) < 40 or len(pair) != 2:
                continue
            if now - rec.get("t", now) > CALL_TTL:
                del self.calls[pair]
                continue
            cars = [tr.car(c) for c in pair]
            if any(c is None or c.gone or (c.trouble_t is not None and c.trouble_t >= rec["t"]) or
                   (c.pit_in_t is not None and c.pit_in_t >= rec["t"]) for c in cars):
                del self.calls[pair]                          # a stop or a spin decided it, not the racing
                continue
            ca, cb = sorted(cars, key=lambda c: tr.pos_of(c.id))
            if ca.laps_down != cb.laps_down:
                continue
            gap = (cb.gap or 0) - (ca.gap or 0) if cb.gap is not None else None
            if gap is None or gap < 1.8:
                continue
            an, bn = self.n(ca.id, now), self.n(cb.id, now)
            intro = Line(self._next_voice(), self.pick("call_back", [
                f"Remember {an} and {bn}? That one has settled itself: {an} is {gap_words(gap)} clear now.",
                f"Let's go back to {an} and {bn}. {an} has come out on top, {gap_words(gap)} up the road.",
                f"Quick word on {an} and {bn}, because we argued about that one. {an}, {gap_words(gap)} ahead.",
            ]), 1)
            settle = self._settle(ca.id, cb.id, now)
            if settle:
                return [intro] + settle
        return []

    def _remember(self, cid, what, now):
        self.moments[cid] = (what, now)

    def _callback(self, cid, now):
        """Something this driver did earlier, to tie the story together ('the same car that ...')."""
        m = self.moments.get(cid)
        if not m or now - m[1] < 45:
            return ""
        return m[0]

    # ------------------------------------------------------------------ events
    def event(self, e, now, interrupting=False) -> list:
        self._pick_i = 0
        lines = None
        if getattr(self.tr, "nascar", False):
            from . import nascar
            lines = nascar.event_lines(self, e, now)
        if lines is None:
            f = getattr(self, "ev_" + e.kind, None)
            lines = f(e, now) if f else []
        lines = [Line(ln.who, ln.text[:1].upper() + ln.text[1:], ln.energy, ln.mood) for ln in (lines or []) if ln and ln.text]
        if interrupting and lines and lines[0].who == LEAD and lines[0].energy >= 2 and \
                e.kind in ("overtake", "stopped", "moment", "contact", "retired"):
            cut = self.pick("cut", ["Oh, hang on!", "Wait a moment!", "Oh, look at this!", "Hold on!", ""])
            if cut:
                lines[0] = Line(LEAD, f"{cut} {lines[0].text}", lines[0].energy, lines[0].mood)
        return self.floor(lines)

    def ev_intro(self, e, now):
        tr = self.tr
        if getattr(tr, "solo", False):
            return self._solo_intro(now)
        track = tr.track or "the circuit"
        event = persona.the_race(self.cfg)
        if tr.session != "race":
            kind = "qualifying" if tr.session == "qualifying" else "practice"
            if event:
                return [Line(LEAD, self.pick("intro_qn", [
                    f"Hello and welcome to {kind} for {event} here at {track}. {num_words(len(tr.cars)).capitalize()} cars are out to set a time.",
                    f"Welcome along to {kind} for {event} at {track}. {num_words(len(tr.cars)).capitalize()} drivers, one clock.",
                ]), 1)]
            return [Line(LEAD, self.pick("intro_q", [
                f"Hello and welcome to {track}, it's {kind}, and {num_words(len(tr.cars))} cars are out to set a time.",
                f"Welcome along to {kind} here at {track}. {num_words(len(tr.cars)).capitalize()} drivers, one clock.",
            ]), 1)]
        if e.data.get("joined"):
            ld = tr.leader()
            p2 = tr.at(2)
            out = [Line(LEAD, self.pick("intro_jn", [
                f"And we're joining the action in {event} here at {track}, {self._lap_status()}.",
                f"Welcome back to {event} at {track}, where we're on {self._lap_status()}.",
            ]) if event else self.pick("intro_j", [
                f"And we're joining the action here at {track}, {self._lap_status()}.",
                f"Welcome back to {track}, where we're on {self._lap_status()}.",
            ]), 1)]
            if ld:
                gap = f", {gap_words(p2.interval)} up the road from {self.n(p2.id, now)}" if p2 and p2.interval else ""
                out.append(Line(LEAD, f"{self.n(ld.id, now)} leads{gap}.", 1))
            me = tr.player()
            if me and tr.pos_of(me.id) > 1:
                out.append(Line(ANALYST, f"And {self.n(me.id, now)} is running {place(tr.pos_of(me.id))}.", 1))
            return out
        pole = tr.at(1)
        out = [Line(LEAD, self.pick("intro_n", [
            f"Hello and welcome to {event}, here at {track}, where {num_words(len(tr.cars))} cars are lining up for {self._race_length()} of racing.",
            f"Good afternoon, and welcome to {event} at {track}. {num_words(len(tr.cars)).capitalize()} cars, {self._race_length()}, and it's almost time to go.",
            f"Welcome to {event}. The grid is formed here at {track}, {num_words(len(tr.cars))} cars, {self._race_length()} ahead of us.",
        ]) if event else self.pick("intro", [
            f"Hello and welcome to {track}, where {num_words(len(tr.cars))} cars are lining up for {self._race_length()} of racing.",
            f"Good afternoon, and welcome to {track}. {num_words(len(tr.cars)).capitalize()} cars, {self._race_length()}, and it's almost time to go.",
            f"Welcome to {track}. The grid is formed, {num_words(len(tr.cars))} cars, {self._race_length()} ahead of us.",
        ]), 1)]
        bits = []
        if pole:
            bits.append(f"Thanks {self.lead_name}. {self.n(pole.id, now, full=True)} starts from pole")
        me = tr.player()
        if me and pole and me.id != pole.id and tr.pos_of(me.id):
            bits.append(f"and we'll be keeping a close eye on {self.n(me.id, now, full=True)}, "
                        f"lining up {place(tr.pos_of(me.id))} on the grid")
        if bits:
            out.append(Line(ANALYST, ", ".join(bits) + ".", 1))
        out.append(Line(LEAD, self.pick("intro_l", [
            "Not long now. The cars are rolling.",
            "The crowd is up, the cars are forming up. This is the part I love.",
        ]), 1))
        return out

    def ev_start(self, e, now):
        if e.data.get("standing", True):
            return self._talk("start", [
                "Lights out, and away we go!",
                "And it's lights out, and the race is on!",
                "Five red lights... and they're away!",
                "Lights out! And we are racing!",
            ], [
                "And they're away! Look at the run down to the first corner!",
                "Keep it clean now, and let's see who wants it most.",
                "Here we go! Hold onto your hats, everybody!",
            ], energy=3)
        return self._talk("start_r", [
            "Green flag, green flag, and we are racing!",
            "And the green flag is out, here we go!",
        ], [
            "Green, green, green! Off they go!",
            "Racing again, and it's elbows out already!",
        ], energy=3)

    def ev_start_review(self, e, now):
        d = e.data
        if getattr(self.tr, "solo", False):
            return self._solo_where(now, ANALYST, "Through the opening corners")
        out = []
        if d.get("leader") is not None:
            out.append(Line(LEAD, self.pick("sr_lead", [
                f"Through the opening corners, it's {self.n(d['leader'], now)} who leads.",
                f"{self.n(d['leader'], now)} has the lead as they settle down.",
                f"And {self.n(d['leader'], now)} is out in front.",
            ]), 2))
        if d.get("gainer") is not None:
            self._remember(d["gainer"], f"the car that gained {plural(d['gained'], 'place')} at the start", now)
        if d.get("loser") is not None:
            self._remember(d["loser"], f"the car that lost {plural(d['lost'], 'place')} at the start", now)
        if d.get("gainer") is not None:
            out.append(Line(ANALYST, self.pick("sr_gain", [
                f"Great start from {self.n(d['gainer'], now)}, up {plural(d['gained'], 'place')} already.",
                f"What a getaway from {self.n(d['gainer'], now)}, {plural(d['gained'], 'place')} gained.",
            ]), 2))
        if d.get("loser") is not None:
            out.append(Line(ANALYST, self.pick("sr_lose", [
                f"Not such a good one for {self.n(d['loser'], now)}, down {num_words(d['lost'])}.",
                f"{self.n(d['loser'], now)} has gone backwards, {plural(d['lost'], 'place')} lost.",
            ]), 1))
        me = d.get("me")
        if me is not None and me not in (d.get("gainer"), d.get("loser"), d.get("leader")):
            ch = d.get("me_change", 0)
            pos = place(self.tr.pos_of(me))
            txt = (f"{self.n(me, now)} up {num_words(ch)}, into {pos}." if ch > 0 else
                   f"{self.n(me, now)} has dropped {num_words(-ch)}, down to {pos}." if ch < 0 else
                   f"{self.n(me, now)} holds station in {pos}.")
            out.append(Line(LEAD if not out else ANALYST, txt, 1))
        return out

    def ev_overtake(self, e, now):
        """The call, then either a prediction settled ('you called that') or a reason from the lap times."""
        lines = self._overtake_lines(e, now)
        a, b = e.cars[0], (e.cars[1] if len(e.cars) > 1 else None)
        self.passes += max(1, len(e.cars) - 1)
        if e.data.get("lead"):
            self.lead_changes += 1
        if e.data.get("multi") and len(e.cars) > 2:
            self._remember(a, f"the car that went past {plural(len(e.cars) - 1, 'car')} in one go earlier", now)
        if b is None or not lines:
            return lines
        settle = self._settle(a, b, now)
        if settle:
            return lines[:1] + settle
        pc = self._pace(a, b)
        if pc and pc[0] == a and pc[1] >= 0.15 and len(lines) > 1 and lines[1].who == ANALYST and self.chance(0.6):
            an, bn = self.n(a, now), self.n(b, now)
            lines[1] = Line(ANALYST, self.pick("ovt_why", [
                f"No surprise, really. {an} was {gap_words(pc[1])} a lap quicker than {bn} coming into that.",
                f"The lap times had been saying it. {gap_words(pc[1]).capitalize()} a lap faster, {bn} couldn't hold that off.",
                f"{an} had the pace, {gap_words(pc[1])} a lap. It was a question of when, not if.",
            ]), lines[1].energy)
        return lines

    def _overtake_lines(self, e, now):
        d = e.data
        a, victims = e.cars[0], list(e.cars[1:])
        b = victims[0] if victims else None
        an, bn = self.n(a, now), self.n(b, now) if b is not None else "the car ahead"
        pos = d.get("pos", 0)
        ca, cb = self.tr.car(a), self.tr.car(b) if b is not None else None
        if d.get("lead"):
            out = [Line(LEAD, self.pick("ovt_lead", [
                f"And {an} takes the lead! Past {bn}, and into first place!",
                f"There it is! {an} is through on {bn}, and {an} leads the race!",
                f"{an} goes for it... and {an} has the lead!",
                f"It's a new leader! {an} gets the job done on {bn}!",
            ]), 3),
                Line(ANALYST, self.pick("ovt_lead_a", [
                    f"That's been coming. {an} has looked the quicker of the two.",
                    f"Big moment in this race. Now can {bn} hang on and fight back?",
                    f"Well, {bn} couldn't hold on to that one. Now {an} has to make it count.",
                    f"Lead change! And listen to this crowd. {bn} will want that one back.",
                ]), 2)]
            return out
        if d.get("multi") and len(victims) >= 2:
            names = ", ".join(self.n(v, now) for v in victims[:-1]) + f" and {self.n(victims[-1], now)}"
            return [Line(LEAD, self.pick("ovt_multi", [
                f"{an} is on a charge! Past {names}, all in one go, and up to {place(pos)}!",
                f"Look at {an}! That's {names} dispatched, {an} into {place(pos)}!",
            ]), 3),
                Line(ANALYST, self.pick("ovt_multi_a", [
                    "Three in one go! That's a driver on a mission.",
                    "This is a real charge. Nobody had an answer to that.",
                ]), 2)]
        if ca and ca.player:
            return [Line(LEAD, self.pick("ovt_me", [
                f"{an} makes the move on {bn}! That's {place(pos)} for {an}!",
                f"And {an} is through! Past {bn}, and up into {place(pos)}!",
                f"Lovely move from {an}, {bn} can't do anything about it. {place(pos).capitalize()}!",
            ]), 3),
                Line(ANALYST, self.pick("ovt_me_a", [
                    "Textbook. Out late, on the brakes, and it stuck.",
                    f"That's the move of a driver who wants this. {place(pos).capitalize()}!",
                ]), 2)]
        if cb and cb.player:
            return [Line(LEAD, self.pick("ovt_vs_me", [
                f"{an} gets by {bn}, and {bn} drops to {place(pos + 1)}.",
                f"And {bn} has lost a place, {an} is through into {place(pos)}.",
                f"{an} makes it stick on {bn}. That's {place(pos)} for {an}.",
            ]), 2),
            Line(ANALYST, self.pick("ovt_vs_me_a", [
                f"{bn} had the place and lost it. That's going to hurt.",
                f"Straight back past. {bn} will want another go at that.",
            ]), 1)]
        long_fight = d.get("battle_len", 0) >= 60
        if long_fight:
            return [Line(LEAD, self.pick("ovt_long", [
                f"Finally! After {mins_words(d['battle_len'])} of trying, {an} is past {bn} for {place(pos)}!",
                f"At last, {an} finds a way by {bn}! That's {pos_words(pos)}.",
            ]), 3),
                Line(ANALYST, self.pick("ovt_long_a", [
                    f"{mins_words(d['battle_len'])} of pressure, and it finally told. Great, hard racing.",
                    "Patience paid off there. That was a proper fight.",
                ]), 2)]
        energy = 3 if pos <= 3 else 2
        return [Line(LEAD, self.pick("ovt", [
            f"{an} gets past {bn} for {pos_words(pos)}.",
            f"And {an} is through! {pos_words(pos).capitalize()} for {an}, {bn} has no answer.",
            f"Move made by {an} on {bn}, up into {place(pos)}.",
            f"{an} makes it stick on {bn}, and that's {pos_words(pos)}.",
            f"Into {place(pos)} goes {an}, past {bn}.",
            f"Through goes {an}! {bn} loses {pos_words(pos)}.",
        ]), energy), Line(ANALYST, self.pick("ovt_a", [
            f"That's {place(pos)} gone from {bn}. A clean, firm move.",
            f"{bn} will be furious with that. {an} just had too much.",
            f"No contact, all fair, and the place is {an}'s.",
            f"Decisive. No messing about, and {bn} has it all to do again.",
        ]), 1)]

    def ev_battle(self, e, now):
        b, a = e.cars
        bn, an = self.n(b, now), self.n(a, now)
        pos, gap = e.data.get("pos", 0), gap_words(e.data.get("gap"))
        what = "the lead" if pos == 1 else place(pos)
        if self._threaded("battle", (a, b)):
            return []
        self.covered(("battle", (a, b)), now, window=PAIR_TTL)
        if self.chance(0.5):
            read = self._read_fight(a, b, now)[2]
            return [Line(LEAD, self.pick("battle", [
                f"Great scrap developing for {what}: {bn} is right on the back of {an}, just {gap} between them.",
                f"{bn} has closed right up on {an}, {gap} apart, and that's for {what}.",
                f"Now here's a battle: {an} with {bn} in the mirrors, for {what}.",
            ]), 2), Line(ANALYST, read, 1)]
        return self._talk("battle", [
            f"Great scrap developing for {what}: {bn} is right on the back of {an}, just {gap} between them.",
            f"Watch this one closely. {bn} is all over {an} in the fight for {what}, {gap} the gap.",
            f"{bn} has closed right up on {an}, {gap} apart, and that's for {what}.",
            f"Now here's a battle: {an} with {bn} in the mirrors, for {what}.",
        ], [
            f"{an} has to be careful now, any small mistake and {bn} is through.",
            f"This is where the pressure tells. {an} can't afford to put a wheel wrong.",
            f"{bn} will be looking for the tow down the straight. That's where it'll come from.",
            f"Question is whether {bn} has the pace to actually make a move stick.",
            f"{an} needs to defend, but without doing anything silly.",
        ], energy=2)

    def ev_closing(self, e, now):
        b, a = e.cars
        d = e.data
        return self._talk("closing", [
            f"{self.n(b, now)} is reeling in {self.n(a, now)}, about {gap_words(d['per_lap'])} a lap. The gap's down to {gap_words(d['gap'])}.",
            f"The gap between {self.n(a, now)} and {self.n(b, now)} is coming down, {gap_words(d['gap'])} now.",
            f"{self.n(b, now)} is the quicker car at the moment, closing on {self.n(a, now)}, {gap_words(d['gap'])} the gap.",
        ], [
            f"At that rate it won't be long before {self.n(b, now)} is right with {self.n(a, now)}.",
            f"{self.n(a, now)} needs to find something, and quickly.",
            f"This is becoming a real problem for {self.n(a, now)}.",
        ])

    def ev_stretch(self, e, now):
        a, b = e.cars
        return [Line(LEAD, self.pick("stretch", [
            f"{self.n(a, now)} is pulling clear out front, {gap_words(e.data['gap'])} the lead over {self.n(b, now)}.",
            f"The lead is growing. {self.n(a, now)} is {gap_words(e.data['gap'])} up the road now.",
        ]), 1)]

    def ev_battle_over(self, e, now):
        a, b = e.cars
        settle = self._settle(a, b, now)
        if settle:
            return [Line(LEAD, self.pick("bover_l", [
                f"{self.n(a, now)} has broken away from {self.n(b, now)}, that one looks done.",
                f"And that fight has fizzled out, {self.n(b, now)} dropping back from {self.n(a, now)}.",
            ]), 1)] + settle
        return [Line(LEAD, self.pick("bover_l", [
            f"{self.n(a, now)} has broken away from {self.n(b, now)}, that one looks done.",
            f"And that fight has fizzled out, {self.n(b, now)} dropping back from {self.n(a, now)}.",
        ]), 1), Line(ANALYST, self.pick("bover", [
            f"{self.n(b, now)} just couldn't stay with it in the end. The pace wasn't there.",
            "Shame, that had the makings of something. On to the next one.",
        ]), 1)]

    def ev_fastest_lap(self, e, now):
        a = e.cars[0]
        t = lap_time_words(e.data.get("time"))
        c = self.tr.car(a)
        if c and c.player:
            return [Line(LEAD, self.pick("fl_me", [
                f"Purple for {self.n(a, now)}! Fastest lap of the race, a {t}.",
                f"{self.n(a, now)} goes quickest of anyone! A {t}, fastest lap of the race.",
            ]), 2)]
        if e.data.get("prev_car") == a:
            return [Line(LEAD, self.pick("fl_again", [
                f"{self.n(a, now)} goes even quicker, a {t}, and lowers the fastest lap again.",
                f"And another fastest lap for {self.n(a, now)}, now a {t}.",
            ]), 1)]
        out = [Line(LEAD, self.pick("fl", [
            f"Fastest lap of the race goes to {self.n(a, now)}, a {t}.",
            f"{self.n(a, now)} sets the fastest lap, a {t}.",
            f"A {t} for {self.n(a, now)}, quickest lap so far.",
        ]), 1)]
        prev = e.data.get("prev_car")
        if prev is not None and prev != a and self.chance(0.4):
            out.append(Line(ANALYST, f"That beats the {lap_time_words(e.data.get('prev'))} from {self.n(prev, now)}.", 1))
        return out

    def ev_personal_best(self, e, now):
        a = e.cars[0]
        return [Line(ANALYST, self.pick("pb", [
            f"{self.n(a, now)} finding more time there, a {lap_time_words(e.data['time'])}, best lap so far.",
            f"Personal best for {self.n(a, now)}, a {lap_time_words(e.data['time'])}. Getting quicker.",
        ]), 1)]

    def ev_pit_in(self, e, now):
        """A big stop is thrown to the pit lane ('Mia?') and she takes it; the rest she calls herself."""
        a = e.cars[0]
        pos = e.data.get("pos", 0)
        c = self.tr.car(a)
        who = self.pit_voice()
        self.covered(("pits", "cycle"), now, window=8.0)
        nm = self.n(a, now)
        stopped = [x for x in self.tr.cars.values() if x.pits and x.id != a and not x.gone]
        first = "the first of the front-runners to stop" if pos <= 3 and not any(
            (self.tr.pos_of(x.id) or 99) <= 4 for x in stopped) else ""
        where = "the lead" if pos == 1 else place(pos)
        if self._mia and ((c and c.player) or pos <= 3):
            throw = (f"{nm} is coming in! {self.pit_name}, over to you." if c and c.player else
                     self.pick("pin_throw", [
                         f"And the leader is coming in! {self.pit_name}?" if pos == 1 else
                         f"{nm} is coming in from {place(pos)}! {self.pit_name}?",
                         f"Pit lane, {self.pit_name}. {nm} is in." if pos > 1 else
                         f"The leader peels off into the pit lane! Let's go down to {self.pit_name}.",
                     ]))
            report = self.pick("pin_report", [
                f"Yes, {self.lead_name}, {nm} is in from {where}" + (f", {first}." if first else ".") +
                (" Everyone up and down the lane is watching this one." if pos == 1 else ""),
                f"{nm} into the box from {where}. {plural(len(stopped), 'car')} through here already, "
                f"so we know what a good stop looks like today." if stopped else
                f"{nm} into the box from {where}, and nobody has stopped before this, so there's no "
                "benchmark yet.",
            ])
            return [Line(LEAD, throw, 2), Line(PIT, report, 2)]
        if c and c.player:
            return [Line(who, self.pick("pin_me", [
                f"{nm} is in, here we go. A crucial stop this.",
                f"And {nm} comes into the pit lane from {place(pos)}.",
            ]), 2)]
        if pos == 1:
            return [Line(who, self.pick("pin_lead", [
                f"The leader is in! {nm} peels into the pit lane.",
                f"{nm} pits from the lead.",
            ]), 2)]
        return [Line(who, self.pick("pin", [
            f"{nm} pits from {place(pos)}.",
            f"In comes {nm}, who was running {place(pos)}.",
            f"{nm} is in the pit lane.",
        ]), 1)]

    def _stop_compare(self, a, lane, now):
        """Mia's comparison: this stop against the quickest so far, or the car it was racing."""
        others = {cid: t for cid, t in self.lane_times.items() if cid != a and self.tr.car(cid)}
        if not others or not lane:
            return ""
        best_id = min(others, key=others.get)
        best = others[best_id]
        d = lane - best
        if d <= -0.3:
            return self.pick("pc_best", [
                f"That's the quickest stop of the day so far, {gap_words(-d)} better than {self.n(best_id, now)}'s.",
                f"Fastest through the lane yet. {self.n(best_id, now)} had the benchmark, and that beats it.",
            ])
        if d < 0.3:
            return f"Almost identical to {self.n(best_id, now)}'s, the quickest so far."
        return self.pick("pc_slow", [
            f"That's {gap_words(d)} slower than {self.n(best_id, now)}'s stop, which is still the one to beat.",
            f"{gap_words(d).capitalize()} off the best stop so far, {self.n(best_id, now)}'s.",
        ])

    def ev_pit_out(self, e, now):
        """Mia reports the stop with its time and how it compares; the booth asks her what it means or picks it up,
        and she hands back."""
        a = e.cars[0]
        d = e.data
        pos, was = d.get("pos", 0), d.get("was")
        lane = d.get("lane_time")
        lane = lane if lane and lane < 100 else None
        lane_s = f", {num_words(int(round(lane)))} seconds in the pit lane" if lane else ""
        nm = self.n(a, now)
        who = self.pit_voice()
        cmp_ = self._stop_compare(a, lane, now) if lane else ""
        if lane:
            self.lane_times[a] = lane
            self._remember(a, f"the car that was {num_words(int(round(lane)))} seconds in the pit lane", now)
        report = self.pick("pout", [
            f"{nm} is back out, in {place(pos)}{lane_s}.",
            f"Stop done for {nm}, rejoining in {place(pos)}{lane_s}.",
            f"And {nm} is away, back out in {place(pos)}{lane_s}.",
        ])
        ty, was_ty = d.get("tyre"), d.get("tyre_was")
        if ty and was_ty and ty != was_ty:
            report += f" Off the {was_ty} tyres and on to the {ty}."
        elif ty and self._mia and self.chance(0.4):
            report += f" On {ty} tyres."
        if cmp_ and self._mia:
            report += " " + cmp_
        out = [Line(who, report, 1)]
        if not self._mia:
            if was and pos and was < pos:
                out.append(Line(ANALYST, f"Down from {place(was)} to {place(pos)}, but the others still have to stop.", 1))
            return out
        ahead = self.tr.at(pos - 1) if pos and pos > 1 else None
        still = [x for x in self.tr.cars.values() if not x.gone and not x.pits and x.status not in ("dnf", "dsq")]
        roll = self.rng.random()
        if roll < 0.45:                                       # the booth asks her a question, she answers it
            kind, ask = self.pick("pout_ask", [
                ("where", f"{self.pit_name}, where does that leave {nm} once everyone has stopped?"),
                ("how", f"And {self.pit_name}, was that a good one?"),
                ("how", f"How did that look from where you are, {self.pit_name}?"),
            ])
            if kind == "how":
                ans = (cmp_ and "On the clock, " + cmp_[:1].lower() + cmp_[1:] + " So yes, a decent job.") or \
                    self.pick("pout_ans_look", [
                        "Tidy. In, done, away. No drama at all.",
                        "No problems that I could see. The crew will be happy with that.",
                    ])
            elif still:
                car = self.tr.car(a)
                lead_still = min(still, key=lambda x: self.tr.pos_of(x.id))
                ans = (f"Hard to say yet, {plural(len(still), 'car')} still to come in, including "
                       f"{self.n(lead_still.id, now)}. Right now it's {place(pos)}" +
                       (f", {gap_words(car.interval)} behind {self.n(ahead.id, now)}."
                        if ahead and car and car.interval is not None else "."))
            else:
                ans = f"Everyone has stopped now, so what you see is what you get. {place(pos).capitalize()}."
            out += [Line(self.rng.choice((LEAD, ANALYST)), ask, 1), Line(PIT, ans, 1)]
            if self.chance(0.5):
                out.append(Line(PIT, self.pick("handback", [f"Back to you, {self.lead_name}.",
                                                            f"{self.lead_name}."]), 1))
        elif was and pos and was < pos:
            out.append(Line(ANALYST, self.pick("pout_a", [
                f"Thanks, {self.pit_name}. Down from {place(was)} to {place(pos)}, but the others still have to stop.",
                f"That stop costs {nm} {plural(pos - was, 'place')} for now. Let's see how it shakes out.",
            ]), 1))
            if still and self.chance(0.6):
                out.append(Line(PIT, self.pick("pout_push", [
                    f"I'd be careful with that, {self.analyst_name}. {plural(len(still), 'car')} still to stop, but "
                    f"they're the ones {nm} now has to beat on the road.",
                    f"We'll know in a lap or two, {self.analyst_name}. {plural(len(still), 'car')} still to come "
                    "through here, and every one of them reshuffles it.",
                ]), 1))
        else:
            out.append(Line(LEAD, self.pick("pout_l", [
                f"Thanks, {self.pit_name}. And {nm} goes back into the fight in {place(pos)}.",
                f"Thank you, {self.pit_name}. Meanwhile, the race has kept moving.",
            ]), 1))
        return out

    def ev_stopped(self, e, now):
        a = e.cars[0]
        pos = e.data.get("pos", 0)
        self._remember(a, f"the car that stopped on track from {place(pos)}", now)
        c = self.tr.car(a)
        if c and c.player:
            return [Line(LEAD, self.pick("stop_me", [
                f"Oh no! {self.n(a, now)} has stopped on track!",
                f"{self.n(a, now)} is stationary! Trouble for {self.n(a, now)}!",
            ]), 3), Line(ANALYST, self.pick("stop_me_a", [
                "What a moment for that to happen. Let's see if they can get it going.",
                "That's come at the worst possible time. Fingers crossed it's a simple fix.",
            ]), 2)]
        return [Line(LEAD, self.pick("stop", [
            f"Oh! {self.n(a, now)} has stopped on track! Trouble for {self.n(a, now)}, who was running {place(pos)}.",
            f"Hang on, {self.n(a, now)} is slow... {self.n(a, now)} has come to a halt!",
            f"{self.n(a, now)} is stationary! That's {self.n(a, now)} from {place(pos)}, stopped out on the circuit.",
        ]), 3), Line(ANALYST, self.pick("stop_a", [
            "Someone is going to benefit from that, you can be sure of it.",
            "That could be a safety car. Watch the flag.",
            f"We all know what that does to the race, {self.lead_name}.",
        ]), 2)]

    def ev_recovered(self, e, now):
        a = e.cars[0]
        return [Line(LEAD, self.pick("rec", [
            f"{self.n(a, now)} is moving again, but that's cost a lot. Down to {place(e.data.get('pos', 0))}.",
            f"And {self.n(a, now)} gets going again, now {place(e.data.get('pos', 0))}.",
        ]), 1), Line(ANALYST, self.pick("rec_a", [
            "At least they're going. Now comes the hard part, getting that time back.",
            f"Right, back after it. {place(e.data.get('pos', 0)).capitalize()} is a long way from where {self.n(a, now)} was.",
        ]), 1)]

    def ev_moment(self, e, now):
        a = e.cars[0]
        loss = gap_words(e.data.get("loss"))
        self._remember(a, f"the car that lost {loss} in that moment earlier", now)
        c = self.tr.car(a)
        if c and c.player:
            return [Line(LEAD, self.pick("mom_me", [
                f"Big moment for {self.n(a, now)}! That's cost {loss} or so.",
                f"Oh, {self.n(a, now)} has lost time there! {loss.capitalize()} gone.",
            ]), 2), Line(ANALYST, self.pick("mom_me_a", [
                "Stayed out of the wall, at least. Now the recovery has to start.",
                "That's a big one. Everything still pointing the right way, hopefully.",
            ]), 1)]
        return [Line(LEAD, self.pick("mom", [
            f"Big moment for {self.n(a, now)}! That's cost {loss}.",
            f"{self.n(a, now)} has lost time there, about {loss}. Something's gone wrong.",
            f"Oh! A moment for {self.n(a, now)}, {loss} lost.",
        ]), 2), Line(ANALYST, self.pick("mom_a", [
            f"That's {loss} gone just like that. Racing can be cruel.",
            "Out wide, and it's cost them dear. They'll be lucky if that's all it cost.",
            f"Have a look at the replays. That's a very expensive few corners for {self.n(a, now)}.",
        ]), 1)]

    def ev_retired(self, e, now):
        a = e.cars[0]
        pos = e.data.get("pos", 0)
        if e.data.get("why") == "gone":
            return [Line(LEAD, f"We've lost {self.n(a, now)} from the timing screens. It looks like {self.n(a, now)} has retired.", 1),
                    Line(ANALYST, "A shame for everyone who worked on that car. Nothing to do about it now but carry on.", 1)]
        if e.data.get("why") == "dsq":
            return [Line(LEAD, f"{self.n(a, now)} has been disqualified.", 2),
                    Line(ANALYST, "That changes the shape of the race for everyone still in it.", 2)]
        return self._talk("ret", [
            f"And that's the end of the race for {self.n(a, now)}, out from {place(pos)}.",
            f"{self.n(a, now)} is out of the race. A miserable afternoon.",
            f"Retirement for {self.n(a, now)}. That's a real shame.",
        ], [
            f"From {place(pos)} as well, and probably through no fault of their own.",
            "Heartbreaking. They'd have been looking at a much better afternoon than this.",
            f"That's a lost opportunity for {self.n(a, now)}, no question.",
        ], energy=2)

    def ev_contact(self, e, now):
        a, b = e.cars
        return self._talk("contact", [
            f"Contact! {self.n(a, now)} and {self.n(b, now)} have touched!",
            f"Oh, they've made contact! {self.n(a, now)} and {self.n(b, now)}!",
        ], [
            "Let's see who comes out of that worse. Both still going, at least.",
            f"Have a look at {self.n(a, now)} again. Did anything come off there?",
            "Hard racing, and they've both paid a little for it.",
        ], energy=3)

    def ev_incident(self, e, now):
        """iRacing incident points: a jump of four or more is contact or a big moment the sim has scored."""
        a = e.cars[0]
        pts, tot = e.data.get("points", 4), e.data.get("total", 0)
        nm = self.n(a, now)
        out = [Line(LEAD, self.pick("inc", [
            f"That's {num_words(pts)} incident points for {nm}. Something's happened there.",
            f"{nm} has just picked up {num_words(pts)} incident points.",
        ]), 1)]
        if tot >= 12:
            out.append(Line(ANALYST, f"That's {num_words(tot)} for the race now. {nm} needs to keep it clean from "
                                     "here.", 1))
        return out

    def ev_penalty(self, e, now):
        a = e.cars[0]
        sec = e.data.get("seconds") or 0
        what = f"a {num_words(sec)} second penalty" if 0 < sec < 60 else "a penalty"
        return [Line(LEAD, self.pick("pen", [
            f"News from the stewards: {what} for {self.n(a, now)}.",
            f"And {self.n(a, now)} has been given {what}.",
        ]), 1)]

    def ev_caution(self, e, now):
        k = e.data.get("kind")
        txt = {"sc": "Safety car! The safety car has been deployed!",
               "vsc": "Virtual safety car! Everyone has to slow down.",
               "fcy": "Full course yellow! The race is neutralised."}.get(k, "Yellow flags out on the circuit.")
        self.forget()                           # the running conversations are all on hold now
        return [Line(LEAD, txt, 2)]

    def ev_sc_in(self, e, now):
        return [Line(LEAD, "The safety car is coming in at the end of this lap. Get ready for the restart.", 2)]

    def ev_green(self, e, now):
        return [Line(LEAD, self.pick("green", [
            "And we're green again! Racing resumes!",
            "Green flag! Back to racing!",
        ]), 2)]

    def ev_lap_one(self, e, now):
        tr = self.tr
        if getattr(tr, "solo", False):
            return self._solo_where(now, LEAD, "One lap down")
        p1, p2, p3 = tr.at(1), tr.at(2), tr.at(3)
        if not (p1 and p2):
            return []
        rest = f" and {self.n(p3.id, now)}" if p3 else ""
        out = [Line(LEAD, self.pick("lap1", [
            f"End of the opening lap: {self.n(p1.id, now)} leads {self.n(p2.id, now)}{rest}.",
            f"One lap down. {self.n(p1.id, now)} leads, {self.n(p2.id, now)} second{', ' + self.n(p3.id, now) + ' third' if p3 else ''}.",
        ]), 1), Line(ANALYST, self.pick("lap1_a", [
            f"{self.n(p2.id, now)} is going to have a real go at this, you'd think.",
            f"Early days, and {self.n(p1.id, now)} has the lead. Now to keep it.",
            "Plenty of time yet, but that's the first blow struck.",
        ]), 1)]
        return out

    def ev_laps_to_go(self, e, now):
        tr = self.tr
        if getattr(tr, "solo", False):
            out = self._solo_where(now, LEAD, f"{num_words(e.data['to_go']).capitalize()} laps to go")
            return out + self._solo_answer() if out else out
        p1, p2 = tr.at(1), tr.at(2)
        n = num_words(e.data["to_go"]).capitalize()
        out = [Line(LEAD, f"{n} laps to go, and {self.n(p1.id, now) if p1 else 'the leader'} still leads.", 1)]
        if p2 and p2.interval:
            out.append(Line(ANALYST, f"{self.n(p2.id, now)} is {gap_words(p2.interval)} behind.", 1))
        else:
            out.append(Line(ANALYST, "No mistakes now. This is the part where races are won and lost.", 1))
        return out

    def ev_halfway(self, e, now):
        if getattr(self.tr, "solo", False):
            out = self._solo_where(now, LEAD, "Half distance")
            return out + self._solo_answer() if out else out
        p1, p2 = self.tr.at(1), self.tr.at(2)
        lead = f", {self.n(p1.id, now)} leads by {gap_words(p2.interval)}" if p1 and p2 and p2.interval else ""
        return [Line(LEAD, f"We're at half distance{lead}.", 1),
                Line(ANALYST, self.pick("half_a", [
                    f"Halfway, and {self.n(p1.id, now)} has earned every inch of that." if p1 else "Halfway.",
                    "Everything to race for from here. Nobody's backing off.",
                ]), 1)]

    def ev_time_to_go(self, e, now):
        if getattr(self.tr, "solo", False):
            out = self._solo_where(now, LEAD, f"{e.data['label'].capitalize()} left on the clock")
            return out + self._solo_answer() if out else out
        p1 = self.tr.at(1)
        return [Line(LEAD, f"{e.data['label'].capitalize()} left on the clock, and {self.n(p1.id, now) if p1 else 'the leader'} leads.", 1)]

    def ev_final_lap(self, e, now):
        tr = self.tr
        if getattr(tr, "solo", False):
            return [Line(LEAD, self.pick("s_final", ["White flag! This is the final lap.",
                                                    "Here comes the last lap!"]), 2)] \
                + (self._solo_where(now, LEAD, "") or []) + self._solo_answer()
        p1, p2 = tr.at(1), tr.at(2)
        out = [Line(LEAD, self.pick("final", [
            f"White flag! This is the final lap, and {self.n(p1.id, now)} leads!",
            f"Here comes the last lap! {self.n(p1.id, now)} is one lap away from victory!",
            "Final lap! One more time round!",
        ]), 2)]
        if p2:
            out.append(Line(ANALYST, self.pick("final_a", [
                f"{self.n(p2.id, now)} has one lap to do something about this. All or nothing now.",
                f"Everything {self.n(p2.id, now)} has left, right here. No time for thinking.",
                f"This is {self.n(p1.id, now)}'s to lose, and {self.n(p2.id, now)}'s to steal.",
            ]), 2))
        if p2 and p2.interval is not None:
            if p2.interval < 1.0:
                out.append(Line(ANALYST, f"And {self.n(p2.id, now)} is right there, {gap_words(p2.interval)} behind! This isn't over!", 3))
            else:
                out.append(Line(ANALYST, f"{self.n(p2.id, now)} is {gap_words(p2.interval)} back. {self.n(p1.id, now)} just has to bring it home.", 1))
        return out

    def ev_winner(self, e, now):
        a = e.cars[0]
        self.forget()                           # nothing running survives the flag
        c = self.tr.car(a)
        track = self.tr.track or "here"
        win = self.n(a, now, full=True)
        event = persona.the_race(self.cfg)
        if event:
            at = f" at {self.tr.track}" if self.tr.track else ""
            out = [Line(LEAD, self.pick("win_me_n" if c and c.player else "win_n", [
                f"{win} wins! {win} takes the chequered flag{at} and wins {event}!",
                f"Chequered flag{at}! {win} is the winner of {event}!",
            ] + ([f"And it's victory for {win}! What a drive!"] if c and c.player else [])), 3)]
        elif c and c.player:
            out = [Line(LEAD, self.pick("win_me", [
                f"{win} wins! {win} takes the chequered flag and wins at {track}!",
                f"And it's victory for {win}! What a drive!",
            ]), 3)]
        else:
            out = [Line(LEAD, self.pick("win", [
                f"{win} wins! {win} takes the chequered flag at {track}!",
                f"And it's {win} who takes the win here at {track}!",
                f"Chequered flag! {win} is the winner!",
            ]), 3)]
        if c and c.grid:
            g = c.grid
            line = (f"From pole to the flag. Not a foot wrong from {self.n(a, now)}." if g == 1 else
                    f"From {place(g)} on the grid. What a drive from {self.n(a, now)}." if g >= 4 else
                    f"Started {place(g)}, and took the win. Thoroughly deserved.")
            out.append(Line(ANALYST, line, 2))
        else:
            out.append(Line(ANALYST, self.pick("win_a", [
                f"Nobody could live with {self.n(a, now)} today.",
                f"A measured, faultless afternoon from {self.n(a, now)}.",
                "That's the drive of somebody who simply wasn't going to be beaten.",
            ]), 2))
        return out

    def ev_podium(self, e, now):
        a = e.cars[0]
        pos = e.data["pos"]
        return [Line(LEAD, self.pick("pod", [
            f"{self.n(a, now)} brings it home in {place(pos)}.",
            f"Across the line in {place(pos)}, {self.n(a, now)}.",
        ]), 1), Line(ANALYST, self.pick("pod_a", [
            f"A podium for {self.n(a, now)}, and a big haul of points with it.",
            f"That's {place(pos)} on the day for {self.n(a, now)}. A strong afternoon.",
        ]), 1)]

    def ev_player_finish(self, e, now):
        a = e.cars[0]
        pos, grid = e.data.get("pos", 0), e.data.get("grid")
        out = [Line(LEAD, self.pick("pfin", [
            f"And here comes {self.n(a, now, full=True)}, crossing the line in {place(pos)}.",
            f"{self.n(a, now, full=True)} takes the chequered flag in {place(pos)}.",
        ]), 2)]
        if grid and pos:
            if pos < grid:
                out.append(Line(ANALYST, f"Up {plural(grid - pos, 'place')} from {place(grid)} on the grid. A really good afternoon's work.", 2))
            elif pos > grid:
                out.append(Line(ANALYST, f"Down from {place(grid)} on the grid, so a bit of a frustrating one. Plenty to learn from.", 1))
            else:
                out.append(Line(ANALYST, f"Started {place(grid)}, finished {place(pos)}. Solid, and no mistakes when it mattered.", 1))
        else:
            out.append(Line(ANALYST, f"{place(pos).capitalize()} on the road. A result to build on.", 1))
        return out

    def ev_quali_lap(self, e, now):
        a = e.cars[0]
        d = e.data
        t = lap_time_words(d.get("time"))
        c = self.tr.car(a)
        quali = self.tr.session == "qualifying"
        if d.get("top"):
            return [Line(LEAD, self.pick("q_top", [
                f"{'Provisional pole' if quali else 'Top of the times'} for {self.n(a, now)}! A {t}.",
                f"{self.n(a, now)} goes quickest! A {t}.",
            ]), 2 if quali else 1)]
        if c and c.player:
            return [Line(LEAD, f"{self.n(a, now)} improves, a {t}, that's {place(d.get('pos', 0))}.", 2)]
        return [Line(LEAD, self.pick("q", [
            f"{self.n(a, now)} improves to {place(d.get('pos', 0))}, a {t}.",
            f"A {t} for {self.n(a, now)}, up to {place(d.get('pos', 0))}.",
        ]), 1)]

    def ev_accident(self, e, now):
        a = e.cars[0]
        return self._talk("accident", [
            f"Oh, there's been an accident! {self.n(a, now)} is involved!",
            f"Trouble! An incident, and {self.n(a, now)} is caught up in it!",
        ], [
            "Cars everywhere. Let's hope everyone is alright.",
            "Look at the state of that. The race changes in an instant.",
            "That's going to take some sorting out.",
        ], energy=3)

    # ------------------------------------------------------------------ one car: solo mode
    def _solo_frame(self):
        return getattr(self.tr, "frame", None)

    def _solo_intro(self, now):
        tr, f = self.tr, self._solo_frame()
        me = tr.player()
        who = self.n(me.id, now, full=True) if me else "our driver"
        track = tr.track or "the circuit"
        if tr.session == "race":
            pos = f"from {place(f.position)}" if f and f.position else ""
            field = f" in a field of {num_words(f.cars)}" if f and f.cars and f.cars < 100 else ""
            length = f", {self._race_length()}" if tr.race_laps or tr.time_left else ""
            event = persona.the_race(self.cfg)
            where = f"{event} at {track}" if event else track
            return [Line(LEAD, f"Welcome to {where}, where we're following {who}{field}{length}.", 1),
                    Line(ANALYST, f"Starting {pos}. Let's see what the afternoon brings." if pos else
                         "Let's see what the afternoon brings.", 1)]
        kind = "qualifying" if tr.session == "qualifying" else "practice"
        event = persona.the_race(self.cfg)
        if event:
            return [Line(LEAD, f"Welcome to {kind} for {event} at {track}, and we're with {who}.", 1)]
        return [Line(LEAD, f"Welcome to {track}, it's {kind}, and we're with {who}.", 1)]

    def _solo_where(self, now, who, lead_in):
        f = self._solo_frame()
        me = self.tr.player()
        if f is None or me is None or not f.position:
            return []
        state = (f.position, round(f.gap_ahead or 0, 0), round(f.gap_behind or 0, 0))
        if state == getattr(self, "_solo_state", None):           # nothing new to say about where the car is
            if not lead_in:
                return []
            return [Line(who, f"{lead_in}, {self.n(me.id, now)} still {place(f.position) if f.position > 1 else 'leading'}.", 1)]
        self._solo_state = state
        bits = [f"{lead_in}{': ' if lead_in else ''}{self.n(me.id, now)} is {place(f.position) if f.position > 1 else 'leading'}"]
        if f.car_ahead and f.gap_ahead and f.position > 1:
            bits.append(f"{gap_words(f.gap_ahead)} behind {call_name(f.car_ahead.get('name', ''))}")
        if f.car_behind and f.gap_behind and f.gap_behind < 3:
            bits.append(f"{call_name(f.car_behind.get('name', ''))} {gap_words(f.gap_behind)} back")
        return [Line(who, ", ".join(bits) + ".", 1)]

    def ev_solo_gain(self, e, now):
        d, me = e.data, e.cars[0]
        other = f" past {call_name(d['other'])}" if d.get("other") else ""
        return self._talk("sgain", [
            f"{self.n(me, now)} makes the move{other}! Up to {place(d['pos'])}!",
            f"And {self.n(me, now)} is through{other}, that's {place(d['pos'])}!",
            f"Lovely move from {self.n(me, now)}{other}, into {place(d['pos'])}!",
        ], [
            "That's the way to do it. Clean, committed, and through.",
            "Great work on the brakes. Now pull out a gap.",
            f"Up to {place(d['pos'])}, and the pace is clearly there.",
        ], energy=3 if d["pos"] <= 3 else 2)

    def ev_solo_loss(self, e, now):
        d, me = e.data, e.cars[0]
        by = f"{call_name(d['other'])} gets by" if d.get("other") else "A place lost"
        return self._talk("sloss", [
            f"{by}, and {self.n(me, now)} drops to {place(d['pos'])}.",
            f"{self.n(me, now)} loses a place there, down to {place(d['pos'])}.",
        ], [
            "Have another go. There's still time to get that back.",
            "Nothing lost yet, but the gap is the wrong way round now.",
        ], energy=2)

    def ev_solo_closing(self, e, now):
        d, me = e.data, e.cars[0]
        return self._talk("sclose", [
            f"{self.n(me, now)} is reeling in {call_name(d['other'])}, about {gap_words(d['per_lap'])} a lap. "
            f"It's {gap_words(d['gap'])} now.",
            f"The gap to {call_name(d['other'])} is coming down, {gap_words(d['gap'])}. {self.n(me, now)} is on a charge.",
        ], [
            "At that rate it's only a matter of time. Stay patient.",
            f"{gap_words(d['gap'])} and closing. This is where the pressure builds.",
        ], energy=1)

    def ev_solo_pressure(self, e, now):
        d, me = e.data, e.cars[0]
        return [Line(ANALYST, self.pick("spress", [
            f"{call_name(d['other'])} is closing on {self.n(me, now)}, {gap_words(d['gap'])} behind now.",
            f"Pressure from behind for {self.n(me, now)}: {call_name(d['other'])} is {gap_words(d['gap'])} back and closing.",
        ]), 1), Line(LEAD, self.pick("spress_l", [
            "It's getting tight. Keep it neat and let them make the mistake.",
            f"{gap_words(d['gap'])}. No room for error now.",
        ]), 1)]

    def ev_solo_off(self, e, now):
        me = e.cars[0]
        return self._talk("soff", [
            f"Oh, {self.n(me, now)} is off the track!",
            f"Wide! {self.n(me, now)} runs out of road there!",
            f"A trip across the grass for {self.n(me, now)}!",
        ], [
            "Get it back on the black stuff, quickly now.",
            "Run wide and it's cost momentum as well as time.",
            "No harm done, hopefully. Keep the car pointing the right way.",
        ], energy=2)

    def _solo_exchange(self, now) -> list:
        """One car, two voices: the lead says where it is (only when that has changed) or reads the last lap,
        and the analyst answers with an angle on it. Nothing said twice."""
        f = self._solo_frame()
        me = self.tr.player()
        if f is None or me is None or now - me.mentioned < 20:
            return []
        state = (f.position, round(f.gap_ahead or 0, 0), round(f.gap_behind or 0, 0))
        self._pick_i = 0
        if self.tr.session == "race" and state != getattr(self, "_solo_state", None):
            self._solo_state = state
            return self._solo_where(now, LEAD, "") + self._solo_answer()
        if me.last and me.best and me.laps != getattr(self, "_solo_lap_talked", -1):
            self._solo_lap_talked = me.laps
            d = me.last - me.best
            lead = (f"A {lap_time_words(me.last)} that time, right on the pace." if d < 0.15 else
                    f"That's a {lap_time_words(me.last)}, {gap_words(d)} off the best.")
            answer = self.pick("s_lap_ans", [
                "The car is in a good window. Keep chipping away.",
                "Time still to find, but the trend is the right way.",
                "That's a tidy enough lap. Nothing to panic about.",
            ])
            return [Line(LEAD, lead, 1), Line(ANALYST, answer, 1)]

    def _solo_answer(self) -> list:
        f = self._solo_frame()
        if f is None:
            return []
        pos = f.position or 0
        if f.car_ahead and f.gap_ahead is not None and f.gap_ahead < 2 and pos > 1:
            txt = self.pick("s_ans_press", [
                f"{gap_words(f.gap_ahead)} to the car in front. The move is on if it stays this close.",
                f"Right in the tow now. {call_name(f.car_ahead.get('name', ''))} has company.",
            ])
        elif f.car_behind and f.gap_behind is not None and f.gap_behind < 2:
            txt = self.pick("s_ans_back", [
                f"{gap_words(f.gap_behind)} back to {call_name(f.car_behind.get('name', ''))}. Mirror full.",
                "Someone is filling the mirrors. The next few corners matter.",
            ])
        elif pos <= 3:
            txt = self.pick("s_ans_front", [
                "That's podium pace. Keep it clean and this stays a very good afternoon.",
                f"{place(pos).capitalize()} and looking calm about it. Exactly where you want to be.",
            ])
        else:
            g = f"{gap_words(f.gap_ahead)} to the car ahead. " if f.gap_ahead else ""
            txt = self.pick("s_ans_mid", [
                f"{g}Time to chip away at it, one car at a time.",
                f"{g}Plenty to gain from here, and nothing to lose.",
            ])
        return [Line(ANALYST, txt, 1)]

    def _filler_solo(self, now):
        """One car: say where it is only when that has changed, otherwise the last lap or the laps left, so the
        booth always has another angle (a booth that repeats itself sounds like a loop)."""
        f = self._solo_frame()
        me = self.tr.player()
        if f is None or me is None or now - me.mentioned < 25:
            return []
        state = (f.position, round(f.gap_ahead or 0, 0), round(f.gap_behind or 0, 0))
        told = getattr(self, "_solo_state", None)
        topics = []
        if self.tr.session == "race" and state != told:
            topics.append("where")
        if me.last and me.best and me.laps != getattr(self, "_solo_lap_told", -1):
            topics.append("lap")
        if self.tr.race_laps and me.laps and me.laps != getattr(self, "_solo_togo_told", -1):
            topics.append("togo")
        self._solo_lap_told = me.laps
        if not topics:
            if not (me.last and me.best):
                return []
            topics.append("lap")
        k = topics[0] if "where" in topics and self.chance(0.6) else topics[-1]
        if k == "where":
            self._solo_state = state
            return self._solo_where(now, LEAD, "")
        if k == "lap":
            self._solo_lap_told = me.laps
            d = me.last - me.best
            if d < 0.15:
                txt = self.pick("s_lap_good", [
                    f"Another strong lap from {self.n(me.id, now)}, a {lap_time_words(me.last)}.",
                    f"A {lap_time_words(me.last)} that time, right on the pace.",
                ])
            else:
                txt = self.pick("s_lap_off", [
                    f"That last one was a {lap_time_words(me.last)}, {gap_words(d)} off the best.",
                    f"A {lap_time_words(me.last)}. The best today is a {lap_time_words(me.best)}.",
                ])
            return [Line(ANALYST, txt, 1)]
        self._solo_togo_told = me.laps
        left = max(0, self.tr.race_laps - me.laps)
        if left <= 0:
            return []
        return [Line(LEAD, f"{plural(left, 'lap').capitalize()} to go for {self.n(me.id, now)}, running {place(f.position) if f.position else 'on'}.", 1)]

    # ------------------------------------------------------------------ rally
    def _km(self, km):
        if km < 1:
            return f"{num_words(int(round(km * 1000 / 100)) * 100)} metres" if km > 0.1 else "a few metres"
        whole, tenth = int(km), int(round((km - int(km)) * 10))
        if tenth == 10:
            whole, tenth = whole + 1, 0
        if not tenth:
            return "one kilometre" if whole == 1 else f"{num_words(whole)} kilometres"
        return f"{num_words(whole)} point {num_words(tenth)} kilometres"

    def ev_stage_intro(self, e, now):
        d = e.data
        stage = self.tr.track or "this stage"
        car = f" in the {d['car']}" if d.get("car") and "car " not in d["car"] else ""
        me = self.tr.player()
        out = [Line(LEAD, self.pick("st_intro", [
            f"Welcome to {stage}. {self._km(d['km']).capitalize()} against the clock.",
            f"We're at {stage}, {self._km(d['km'])} of it, and {self.n(me.id, now) if me else 'our driver'} is on the start line{car}.",
        ]), 1)]
        if d.get("best"):
            out.append(Line(ANALYST, f"The time to beat on this stage is a {lap_time_words(d['best'])}.", 1))
        else:
            out.append(Line(ANALYST, "First time through here with this car, so this run sets the benchmark.", 1))
        return out

    def ev_stage_start(self, e, now):
        return [Line(LEAD, self.pick("st_go", [
            "Three, two, one... and we're away!", "And off the line! Here we go!", "Go! And we're on the stage!",
        ]), 3)]

    def ev_split(self, e, now):
        d = e.data
        where = {0.25: "the first split", 0.5: "the halfway split", 0.75: "the final split"}.get(d["at"], "the split")
        delta = d.get("delta")
        if delta is None:
            return [Line(LEAD, f"Through {where}, {lap_time_words(d['time'])} on the clock.", 1)]
        if delta <= -0.05:
            return [Line(LEAD, self.pick("split_up", [
                f"Through {where}, and that's {gap_words(-delta)} up on the best!",
                f"Green at {where}! {gap_words(-delta).capitalize()} up!",
            ]), 2)]
        if delta < 0.05:
            return [Line(LEAD, f"{where.capitalize()}, and it's dead level with the best run.", 2)]
        return [Line(LEAD, self.pick("split_down", [
            f"Through {where}, {gap_words(delta)} down on the best.",
            f"{where.capitalize()}: {gap_words(delta)} off the pace of the best run.",
        ]), 1), Line(ANALYST, self.pick("split_down_a", [
            "That'll have to be made up somewhere later on the stage.",
            "Work to do from here. No panicking, just get it back a bit at a time.",
        ]), 1)]

    def ev_rally_moment(self, e, now):
        out = [Line(LEAD, self.pick("r_mom", [
            "Oh, big moment there!", "Whoa! That was close!", "Oh, sideways! Hold on to it!",
            "That's a big hit of speed lost! Something happened there!",
        ]), 3)]
        out.append(Line(ANALYST, self.pick("r_mom_a", [
            "That's the margins we're dealing with here. Inches.",
            "One corner, and it can all change.",
            "Anywhere else that's a gentle run wide. Here, it's time in the bank.",
        ]), 2))
        return out

    def ev_rally_stopped(self, e, now):
        return [Line(LEAD, self.pick("r_stop", [
            "Oh no, we've stopped on the stage!", "The car has come to a halt! This is costing a lot of time!",
        ]), 3), Line(ANALYST, "The clock doesn't stop for anybody. Every second in there is a second lost.", 2)]

    def ev_rally_going(self, e, now):
        return [Line(ANALYST, "Going again, but that's taken a big chunk out of the time.", 1),
                Line(LEAD, "At least it's moving. Now it's about salvaging whatever is left of this stage.", 1)]

    def ev_rally_damage(self, e, now):
        return [Line(ANALYST, self.pick("r_dmg", [
            "That's done some damage. It'll need nursing to the end now.",
            "There's damage on the car after that. Careful from here.",
        ]), 2), Line(LEAD, "Every corner is a gamble now. The stage is suddenly a lot longer.", 1)]

    def ev_stage_restart(self, e, now):
        return [Line(LEAD, "And we're going again from the start.", 1)]

    def ev_stage_finish(self, e, now):
        d = e.data
        t = lap_time_words(d["time"])
        if d.get("new_best") and d.get("best"):
            out = [Line(LEAD, f"Stop the clock! A {t}, and that's a new best on this stage by {gap_words(-d['delta'])}!", 3)]
        elif d.get("new_best"):
            out = [Line(LEAD, f"Stop the clock! A {t}. That's the time to beat now.", 2)]
        else:
            out = [Line(LEAD, f"Across the line, a {t}, {gap_words(d['delta'])} off the best.", 2)]
        if d.get("top") and d["top"] > 100:
            out.append(Line(ANALYST, f"Top speed on that run, {num_words(int(round(d['top'])))} kilometres an hour.", 1))
        return out

    def _filler_rally(self, now):
        tr = self.tr
        f = getattr(tr, "frame", None)
        if f is None or not getattr(tr, "running", False) or now - getattr(self, "_rally_told", -1e9) < 26:
            return []
        self._rally_told = now
        left = max(0.0, (1 - f.pct) * (f.track_len or 0) / 1000.0)
        best = tr.store.get(tr.key) if getattr(tr, "store", None) else None
        out = [Line(LEAD, self.pick("r_left", [
            f"{self._km(left).capitalize()} to go.", f"Still {self._km(left)} left on this one.",
        ]), 1)]
        if best and best.get("profile"):
            i = int(f.pct * 100)
            ref = best["profile"][i] if i < len(best["profile"]) else None
            if ref:
                d = f.cur_time - ref
                side = "up on" if d < 0 else "down on"
                out.append(Line(ANALYST, self.pick("r_now", [
                    f"Right now that's {gap_words(abs(d))} {side} the best run.",
                    f"On the live timing, {gap_words(abs(d))} {side} the reference.",
                    f"{gap_words(abs(d)).capitalize()} {'in hand' if d < 0 else 'to find'} compared with the best run.",
                ]), 1))
        elif f.speed > 120:
            out.append(Line(ANALYST, f"{num_words(int(f.speed))} kilometres an hour through here. Fully committed.", 1))
        return out

    # ------------------------------------------------------------------ quiet moments
    def filler(self, now, focus="balanced") -> list:
        """Something to say when nothing's happening: the next beat of a conversation the booth is already having
        or a new one about a battle, a charge or the pit phase; failing that the running order, the player's race
        or a driver who hasn't had a mention for a while."""
        tr = self.tr
        if getattr(tr, "rally", False):
            return self._filler_rally(now)
        if getattr(tr, "solo", False):
            return self._filler_solo(now)
        if tr.session != "race":
            return self._filler_quali(now)
        if not tr.started:
            return self._filler_grid(now)
        if tr.finished:
            return []
        raw = self._due_call(now) or self._beat(now, focus) or self._aside(now, focus) or self._open(now, focus)
        lines = self._fresh(raw) or self._fresh(raw, 8) or raw
        if lines:
            return self.floor(lines)
        weights = {"order": 2, "battle": 3, "player": 3 if focus != "field" else 1, "pace": 1.5,
                   "pits": 1, "field": 2.5 if focus != "me" else 0.5, "lead": 1.5}
        if focus == "me":
            weights["player"] = 6
        opts = list(weights)
        self.rng.shuffle(opts)
        opts.sort(key=lambda k: -weights[k] * self.rng.random())
        for k in opts:
            raw = getattr(self, "_f_" + k)(now)
            lines = self._fresh(raw) or self._fresh(raw, 8) or raw
            if lines:
                return self.floor(lines)
        return []

    def _fresh(self, lines, depth=60):
        """Drop anything in the last few lines: a booth that repeats itself sounds like a loop."""
        recent = set(list(self._said)[-depth:])
        return [ln for ln in (lines or []) if ln and ln.text not in recent]

    def _f_order(self, now):
        tr = self.tr
        top = [tr.at(i) for i in (1, 2, 3)]
        if not all(top):
            return []
        order = (top[0].id, top[1].id, top[2].id)
        if order == getattr(self, "_order_told", None) and now - getattr(self, "_order_t", -1e9) < 60:
            return []
        self._order_told, self._order_t = order, now
        out = [Line(LEAD, self.pick("f_order", [
            f"Let's have a look at the order. {self.n(top[0].id, now)} leads, {self.n(top[1].id, now)} is second, and {self.n(top[2].id, now)} third.",
            f"So, {self._lap_status()}: {self.n(top[0].id, now)}, {self.n(top[1].id, now)}, {self.n(top[2].id, now)}, that's your top three.",
            f"As it stands: {self.n(top[0].id, now)} out in front, then {self.n(top[1].id, now)}, then {self.n(top[2].id, now)}.",
        ]), 1)]
        me = tr.player()
        if me and tr.pos_of(me.id) > 3 and now - me.mentioned > 25:
            out += self._player_line(me, now, ANALYST)
        return out

    def _f_lead(self, now):
        p1, p2 = self.tr.at(1), self.tr.at(2)
        if not (p1 and p2) or p2.interval is None or self.stories:
            return []
        g = gap_words(p2.interval)
        if p2.interval < 1.0:
            return [Line(LEAD, f"Still nothing in it at the front: {self.n(p2.id, now)} is {g} behind {self.n(p1.id, now)}.", 1),
                    Line(ANALYST, f"{self.n(p1.id, now)} can't relax for a second here.", 1)]
        return [Line(LEAD, self.pick("f_lead", [
            f"{self.n(p1.id, now)}'s lead is {g} now.",
            f"Out front, {self.n(p1.id, now)} has {g} in hand over {self.n(p2.id, now)}.",
        ]), 1)]

    # ------------------------------------------------------------------ conversations
    def exchange(self, now, focus="balanced") -> list:
        """What the booth says in a quiet spell: the next beat of a conversation already running, or a fresh one.
        A thread is opened, worked beat by beat and paid off, so the booth follows the racing like a TV booth
        instead of firing off unconnected remarks."""
        tr = self.tr
        if tr.session != "race" or tr.finished or getattr(tr, "rally", False):
            return []
        if getattr(tr, "solo", False):
            return self._solo_exchange(now)
        self._pick_i = 0
        parts = self._due_call(now) or self._beat(now, focus) or self._aside(now, focus)
        if parts:
            return parts
        return self._open(now, focus)

    @property
    def active(self):
        return bool(self.stories)

    def _open(self, now, focus="balanced") -> list:
        """Start a new conversation about the best story going, if there is one worth opening."""
        if len(self.stories) >= THREAD_MAX:
            return []
        kind = self._choose(now, focus)
        if kind is None:
            return []
        key, mark, parts = getattr(self, "_open_" + kind)(now)
        if not parts or not key:
            return []
        self.stories[key] = Story(kind, key, now, now, 1, mark)
        self.covered(key, now, window=PAIR_TTL if kind in ("battle", "charge") else 10.0)
        self.last_kind = kind
        if kind in ("battle", "charge"):
            self.told_player = now
        self._prune(now)
        return parts

    def _prune(self, now):
        """Forget conversations that have gone nowhere for a while, so old stories don't block new ones."""
        if now - self._last_prune < 10:
            return
        self._last_prune = now
        for key, st in list(self.stories.items()):
            if now - st.last > THREAD_TTL or now - st.since > 240:
                del self.stories[key]

    def forget(self, now=None):
        """The race moved on (a restart, a new session): drop the running conversations."""
        self.stories.clear()
        self.recent_pairs.clear()
        self._said.clear()
        self.last_open, self.last_kind = "", ""
        self.last_ended = self.told_player = -1e9

    def clear_memory(self):
        """A new session: the calls made, the stop times and the moments belong to the old one. (A caution or the
        flag only puts the conversations on hold: what was said is still remembered for the sign-off.)"""
        self.calls.clear()
        self.lane_times.clear()
        self.moments.clear()
        self.passes = self.lead_changes = 0
        self.asides_done.clear()
        self.__dict__.pop("_ob_used", None)
        self.__dict__.pop("_hist_told", None)
        self.last_aside = -1e9

    def covered(self, key, now, window=PAIR_TTL):
        """This one has just gone out on air, so it isn't said again straight after (a call is not repeated by an
        event line, and a conversation doesn't open on a fight that has only just been called)."""
        self.recent_pairs[key] = now
        for k in [k for k, w in self.recent_pairs.items() if now - w > PAIR_TTL]:
            del self.recent_pairs[k]

    def thread_hint(self):
        """What the booth is in the middle of, for the AI to carry on rather than start something else."""
        if not self.stories:
            return ""
        st = max(self.stories.values(), key=lambda s: s.last)
        tr = self.tr
        k = st.key[1] if len(st.key) > 1 else None
        if st.kind == "battle" and isinstance(k, tuple) and len(k) == 2:
            a, b = tr.car(k[0]), tr.car(k[1])
            if a and b and b.interval is not None:
                return (f"You are in the middle of the fight for {place(tr.pos_of(a.id))}: {self.n(a.id)} ahead, "
                        f"{self.n(b.id)} {gap_words(b.interval)} behind.")
        if st.kind == "charge" and isinstance(k, int):
            c = tr.car(k)
            if c:
                return f"You are following {self.n(c.id)}, on a charge up to {place(tr.pos_of(c.id))}."
        if st.kind == "pits":
            n = len([x for x in tr.cars.values() if not x.gone and not x.pits])
            return f"The pit cycle is still running, {plural(n, 'car')} still to come in."
        if st.kind == "player":
            me = tr.player()
            if me:
                return f"You are talking through {self.n(me.id)}'s afternoon, running {place(tr.pos_of(me.id))}."
        return ""

    def _threaded(self, kind, key, now=None, window=PAIR_TTL):
        """Is this already a conversation, or has it just been on air?"""
        if (kind, key) in self.stories:
            return True
        w = self.recent_pairs.get((kind, key))
        return w is not None and (now is None or now - w < window)

    def _beat(self, now, focus) -> list:
        """Move the oldest live conversation on a beat. Empty if none is due."""
        for key, st in sorted(self.stories.items(), key=lambda kv: kv[1].last):
            if now - st.last < THREAD_GAP:
                continue
            maker = getattr(self, "_beat_" + st.kind, None)
            parts, done = (maker(st, now) if maker else ([], True))
            if not parts:
                if now - st.last > THREAD_TTL or now - st.since > 240:
                    del self.stories[key]
                continue
            st.last, st.beat = now, st.beat + 1
            if done:
                del self.stories[key]
                self.last_ended = now
            return parts
        return []

    def _choose(self, now, focus):
        """Which story to open: what the race is actually doing, weighted by the focus setting."""
        tr = self.tr
        me = tr.player()
        opts = []
        for (a, b), bt in tr.battles.items():
            ca, cb = tr.car(a), tr.car(b)
            if not (ca and cb and bt.get("told")) or cb.interval is None:
                continue
            if self._threaded("battle", (a, b), now):
                continue
            if now - max(ca.mentioned, cb.mentioned) < 20:
                continue
            pos = tr.pos_of(a)
            score = 7.0 - cb.interval - pos * 0.25
            if pos == 1:
                score += 3
            if me and (ca.player or cb.player):
                score += {"me": 9, "balanced": 4, "field": 1}.get(focus, 4)
            if focus == "field" and pos <= 2:
                score -= 4
            if focus == "me" and not (me and (ca.player or cb.player)):
                score -= 3
            opts.append((score - (2.0 if self.last_kind == "battle" and now - self.last_ended < 20 else 0.0),
                         "battle", (a, b)))
        movers = self._movers(now)
        if movers:
            c, gained, lost = movers[0]
            score = 5.0 + abs(gained - lost) * 1.5
            if c.player:
                score += {"me": 9, "balanced": 3, "field": 0}.get(focus, 3)
            elif focus == "field":
                score += 3
            if ("charge", c.id) not in self.stories:
                opts.append((score, "charge", c.id))
        if me and ("player", me.id) not in self.stories and now - self.told_player > 40:
            sp = {"me": 7.0, "balanced": 3.5, "field": 1.5}.get(focus, 3.5)
            if now - self.last_ended < 20:
                sp += 2.0                                     # a change of scene after any battle
            opts.append((sp, "player", me.id))
        if ("trend", "x") not in self.stories and not any(s.kind in ("trend", "stretch") for s in self.stories.values()):
            opts.append(((3.5 if self.last_kind == "battle" else 2.5) if focus == "field" else
                         (2.5 if self.last_kind == "battle" else 1.2), "trend", "x"))
        if self._pitting(now) and not self._threaded("pits", "cycle", now, 12.0) and self.chance(0.5):
            opts.append((4.5 + (9.0 if focus == "me" else 0.0), "pits", "cycle"))
        if not opts:
            return None
        opts.sort(key=lambda o: -o[0] * (0.75 + self.rng.random() * 0.5))
        best = opts[0]
        if len(opts) > 1 and best[2] == self.last_open and self.chance(0.5):
            best = opts[1]
        self.last_open = best[2]
        return best[1]

    def _movers(self, now):
        """Cars that have gained or lost a lot of places since the grid, so the booth can follow the charge."""
        tr = self.tr
        out = []
        for c in tr.cars.values():
            if c.player or c.gone or c.grid is None or c.in_pit or now - c.mentioned < 25:
                continue
            if c.status in ("dnf", "dsq") or getattr(c, "laps", 0) < 1:
                continue
            p = tr.pos_of(c.id)
            if p and p < c.grid - 3:
                out.append((c, c.grid - p, 0))
        out.sort(key=lambda x: -x[1])
        return out

    def _pitting(self, now):
        """Is anyone actually in the lane right now (or just arrived), so a pit story is worth opening?"""
        return any(c.in_pit and not c.gone for c in self.tr.cars.values() if now - (c.pit_in_t or 0) < 40)

    # --- asides: the side conversations a real booth drifts into between stories, always about this race

    def _aside(self, now, focus="balanced"):
        """Now and then, a side conversation: what it's like in the car in a fight like this one, the race in
        numbers, who wins it, what the pit lane looks like, the viewer's driver's afternoon. Random, but always
        tied to what is on the screen, and never the same one twice in a race."""
        tr = self.tr
        gap, odds = self.aside_pace()
        if now - self.last_aside < gap or not tr.started or tr.finished or not self.chance(odds):
            return []
        ld = tr.leader()
        left = self._laps_left()
        if ld is None or ld.laps < 1 or (left is not None and left < 2) or getattr(tr, "flag", None) in CAUTION_FLAGS or any(c.in_pit and now - (c.pit_in_t or 0) < 6 for c in tr.cars.values()):
            return []                         # not in the opening scramble, the last lap, a caution or a stop
        topics = ["numbers", "defend", "chase", "stops", "final", "start", "viewer", "swap", "winner", "pitview",
                  "pressure"]
        self.rng.shuffle(topics)
        if self.chance({"rare": 0.15, "normal": 0.35, "lots": 0.7}.get(self.cfg.get("banter"), 0.35)):
            lines = self._banter(now)                 # a random conversation: a sponsor read or a tangent
            if lines:
                self.last_aside = now
                return lines
        if focus == "me" and "viewer" in topics:
            topics.remove("viewer")
            topics.insert(0, "viewer")
        for t in topics:
            if t in self.asides_done:
                continue
            lines = getattr(self, "_aside_" + t)(now)
            if lines:
                self.asides_done.add(t)
                self.last_aside = now
                return lines
        return []

    def _close_fight(self):
        """The closest fight going: (car ahead, car behind, gap), or None."""
        tr = self.tr
        best = None
        for (a, b), bt in tr.battles.items():
            ca, cb = tr.car(a), tr.car(b)
            if ca and cb and not ca.gone and not cb.gone and cb.interval is not None and cb.interval < 1.5 and \
                    tr.pos_of(b) == tr.pos_of(a) + 1 and (best is None or cb.interval < best[2]):
                best = (a, b, cb.interval)
        return best

    def _aside_numbers(self, now):
        laps = self.tr.leader().laps if self.tr.leader() else 0
        if self.passes < 3 or laps < 1:
            return []
        mov = max((c for c in self.tr.cars.values() if c.grid and not c.gone and c.status not in ("dnf", "dsq")),
                  key=lambda c: c.grid - self.tr.pos_of(c.id), default=None)
        gained = mov.grid - self.tr.pos_of(mov.id) if mov else 0
        leaders = (f" and {plural(self.lead_changes, 'change')} of lead" if self.lead_changes else
                   ", and not a single change of lead")
        out = [Line(LEAD, self.pick("as_num", [
            f"{self.analyst_name}, a few numbers while it's calm. {plural(self.passes, 'pass')} so far{leaders}.",
            f"While we've got a moment: that's {plural(self.passes, 'overtake')} in this race already{leaders}.",
        ]).replace("passs", "passes"), 1)]
        if mov and gained >= 2:
            out.append(Line(ANALYST, self.pick("as_num_a", [
                f"And the biggest mover is {self.n(mov.id, now)}, {plural(gained, 'place')} up from the grid. "
                "That's where the story of this race is, for me.",
                f"Most of them involving {self.n(mov.id, now)}, it feels like. {plural(gained, 'place')} gained "
                "since the start.",
            ]), 1))
        else:
            out.append(Line(ANALYST, "Which tells you how evenly matched these cars are. Nobody is getting away "
                                     "with anything.", 1))
        out.append(Line(LEAD, self.pick("as_num_l", [
            "And plenty of race left to add to it.",
            "Keep counting. I don't think it's finished.",
        ]), 1))
        return out

    def _aside_defend(self, now):
        f = self._close_fight()
        if not f:
            return []
        an, bn = self.n(f[0], now), self.n(f[1], now)
        return [Line(LEAD, self.pick("as_def", [
            f"{self.analyst_name}, you've been where {an} is now, with a car filling the mirrors. "
            "What's going through your head?",
            f"What's it actually like in {an}'s seat right now, {self.analyst_name}?",
        ]), 1), Line(ANALYST, self.pick("as_def_a", [
            "Mostly the mirrors, and that's the problem. You stop driving your own race and start driving the car "
            "behind, and that's exactly when you miss a braking point.",
            "Honestly? You're counting corners. Every one you get through is one fewer for them to have a go at.",
            "You hear them before you see them. And you tell yourself not to look, and you look anyway.",
        ]), 1), Line(LEAD, self.pick("as_def_l", [
            f"So that's what {bn} is waiting for. One look in the mirror too many.",
            f"And {bn} knows all of that, of course. {gap_words(f[2]).capitalize()} and waiting.",
        ]), 1)]

    def _aside_chase(self, now):
        f = self._close_fight()
        if not f:
            return []
        an, bn = self.n(f[0], now), self.n(f[1], now)
        return [Line(LEAD, self.pick("as_ch", [
            f"Is it easier being {bn}, though? Chasing rather than being chased?",
            f"Would you rather be {an} or {bn} right now, {self.analyst_name}?",
        ]), 1), Line(ANALYST, self.pick("as_ch_a", [
            f"{bn}, every time. Nothing to lose. You can try something, and if it doesn't work you just try again "
            "a lap later.",
            f"Chasing is fun for about three laps. After that, if you haven't got by, you start wondering if you "
            f"ever will. {bn} is getting near that point.",
        ]), 1), Line(LEAD, self.pick("as_ch_l", [
            "Spoken like someone who did a lot of chasing.",
            "I think you've just told us more about your career than you meant to.",
        ]), 1), Line(ANALYST, self.pick("as_ch_b", [
            "No comment.",
            "Thank you, Ben. Lovely.".replace("Ben", self.lead_name),
            "We're not here to talk about me.",
        ]), 1)]

    def _aside_stops(self, now):
        if not self._mia or not self.lane_times:
            return []
        best = min(self.lane_times, key=self.lane_times.get)
        if not self.tr.car(best):
            return []
        bt = num_words(int(round(self.lane_times[best])))
        return [Line(LEAD, f"{self.pit_name}, what's it like down there when a car comes in?"),
                Line(PIT, self.pick("as_st", [
                    f"It goes very quiet, {self.lead_name}. Everyone stops talking and watches the clock. "
                    f"{plural(len(self.lane_times), 'stop')} so far, and {self.n(best, now)}'s {bt} seconds is the "
                    "one the crews are measuring themselves against.",
                    f"Tense. You can tell a good stop without looking at the clock, it just flows. "
                    f"{self.n(best, now)}'s was like that, {bt} seconds, the best so far.",
                ]), 1),
                Line(ANALYST, self.pick("as_st_a", [
                    "I always hated stops. Sitting there, completely still, while everyone else is racing.",
                    "From the car it feels like an hour. You just stare at the lollipop and wait.",
                ]), 1),
                Line(PIT, self.pick("as_st_b", [
                    f"And yet you always got away cleanly, {self.analyst_name}. I've looked it up.",
                    "The crews would say the same about waiting for you to arrive.",
                ]), 1)]

    def _aside_final(self, now):
        left = self._laps_left()
        ld = self.tr.leader()
        p2 = self.tr.at(2)
        if left is None or left > 3 or left < 2 or not ld or not p2 or p2.interval is None:
            return []
        return [Line(LEAD, self.pick("as_fi", [
            f"{self.analyst_name}, {self._laps_left_words()}. What changes in the car now?",
            f"Last few laps. Does it feel different from the inside, {self.analyst_name}?",
        ]), 1), Line(ANALYST, self.pick("as_fi_a", [
            "Everything gets louder. Every noise from the car, you're sure it's about to break.",
            "You start doing sums. How big is the gap, how many corners left, how many mistakes can I afford. "
            "Usually none.",
        ]), 1), Line(LEAD, f"And {self.n(ld.id, now)} will be doing those sums now. "
                           f"{gap_words(p2.interval).capitalize()} to {self.n(p2.id, now)}.", 1)]

    def _aside_start(self, now):
        ld = self.tr.leader()
        g = next(((cid, m) for cid, m in self.moments.items() if "at the start" in m[0]), None)
        if not ld or ld.laps > 2 or not g:
            return []
        nm = self.n(g[0], now)
        ask = self.chance(0.5)
        return [Line(LEAD, f"Still thinking about that start, {self.analyst_name}. {nm} {g[1][0].replace('the car that ', '')}.", 1),
                Line(ANALYST, self.pick("as_sa", [
                    "Starts are half nerve, half luck. You commit to a gap before it's there and hope it opens.",
                    "You've got about two seconds to make a decision you'll be living with for the whole race.",
                ]), 1),
                Line(LEAD, "Did you ever get one badly wrong?" if ask else
                     f"And {nm} is living with it, one way or another.", 1)] + \
            ([Line(ANALYST, "Once. We don't talk about it.", 1)] if ask else [])

    def _aside_viewer(self, now):
        me = self.tr.player()
        if me is None or me.gone or me.in_pit:
            return []
        p = self.tr.pos_of(me.id)
        ahead = self.tr.at(p - 1)
        behind, gb = self._behind(me.id)
        nm = self.n(me.id, now)
        if ahead and me.interval is not None and me.interval < 2.0:
            think = (f"Get after {self.n(ahead.id, now)}. {gap_words(me.interval).capitalize()} is close enough "
                     "that one good lap does it.")
        elif behind and gb is not None and gb < 1.5:
            think = (f"Honestly, the mirrors. {self.n(behind.id, now)} is {gap_words(gb)} back, and that's the "
                     "real fight.")
        else:
            think = (f"Rhythm. Nobody close either way, so it's about clean laps and being ready when the race "
                     f"comes back to {nm}.")
        return [Line(LEAD, f"If you're {nm} right now, {self.analyst_name}, {place(p)}, what are you thinking?", 1),
                Line(ANALYST, think, 1),
                Line(LEAD, self.pick("as_vw_l", [
                    "Simple as that.",
                    f"Over to you, {nm}.",
                ]), 1)]

    def _aside_swap(self, now):
        laps = [(c.last, c) for c in self.tr.cars.values() if c.last and not c.gone and not c.in_pit and not c.pit_lap]
        ld = self.tr.leader()
        if len(laps) < 3 or not ld:
            return []
        t, fast = min(laps, key=lambda x: x[0])
        if fast.id == ld.id:
            return [Line(LEAD, f"{self.analyst_name}, if you could swap into any car out there right now?", 1),
                    Line(ANALYST, f"{self.n(ld.id, now)}'s. Leading and quickest on the last lap. Easy question.", 1),
                    Line(LEAD, "Fair. I was hoping for something more interesting.", 1),
                    Line(ANALYST, "Sorry to disappoint.", 1)]
        return [Line(LEAD, f"{self.analyst_name}, if you could swap into any car out there right now?", 1),
                Line(ANALYST, f"{self.n(fast.id, now)}'s. Quickest on the last lap, a {lap_time_words(t)}. "
                              f"{place(self.tr.pos_of(fast.id)).capitalize()} on the road, but that's the car.", 1),
                Line(LEAD, f"Not {self.n(ld.id, now)}, who's leading the race?", 1),
                Line(ANALYST, f"{self.n(ld.id, now)} has the result. I'd want the speed.", 1)]

    def _aside_winner(self, now):
        left = self._laps_left()
        ld, p2 = self.tr.leader(), self.tr.at(2)
        if not ld or not p2 or p2.interval is None or (left is not None and left < 2) or ld.laps < 1:
            return []
        if frozenset((ld.id, p2.id)) in self.calls:
            return []
        if p2.interval > 5.0:
            return []
        _, fav, reason = self._read_fight(ld.id, p2.id, now)
        first = reason.split(" ", 1)[0].strip(",.")
        if first not in (self.n(ld.id), self.n(p2.id)):
            reason = reason[:1].lower() + reason[1:]
        other = p2.id if fav == ld.id else ld.id
        self._call((ld.id, p2.id), ANALYST, fav, now)
        self._call((ld.id, p2.id), LEAD, other, now)
        return [Line(LEAD, self.pick("as_wn", [
            f"Go on then, {self.analyst_name}. Who wins this?",
            f"Put your neck on the line, {self.analyst_name}. Who takes it?",
        ]), 1), Line(ANALYST, f"I'll say {self.n(fav, now)}, because {reason}", 1),
            Line(LEAD, (f"I'll take {self.n(other, now)}. Track position, and {self._laps_left_words()}."
                        if other == ld.id else
                        f"I'll go the other way. {self.n(other, now)} is {gap_words(p2.interval)} back, and "
                        + ("that's nothing." if p2.interval < 1.5 else f"there's {self._laps_left_words()}."))),
            Line(ANALYST, "Done. Loser buys dinner.", 1)]

    def _aside_pitview(self, now):
        if not self._mia:
            return []
        cars = [c for c in self.tr.cars.values() if not c.gone and c.status not in ("dnf", "dsq")]
        still = sorted((c for c in cars if not c.pits), key=lambda c: self.tr.pos_of(c.id))
        done = [c for c in cars if c.pits]
        if not still or not done:
            return []
        s0 = still[0]
        return [Line(PIT, f"{self.lead_name}, a word from down here. Every crew in this lane is watching the timing "
                          f"screens for {self.n(s0.id, now)}, still to stop and running {place(self.tr.pos_of(s0.id))}.", 1),
                Line(LEAD, f"Why {self.n(s0.id, now)} in particular, {self.pit_name}?", 1),
                Line(PIT, self.pick("as_pv", [
                    "Because where that car comes out decides half their afternoons. Everyone's doing the maths.",
                    f"Because until {self.n(s0.id, now)} stops, nobody really knows where they stand.",
                ]), 1)]

    def _aside_pressure(self, now):
        me = self.tr.player()
        ld = self.tr.leader()
        if not ld or ld.laps < 2:
            return []
        if me and not me.gone and self.tr.pos_of(me.id) == 1:
            return []
        p2 = self.tr.at(2)
        if not p2 or p2.interval is None:
            return []
        return [Line(ANALYST, f"Can I say something about {self.n(ld.id, now)}? We've barely mentioned the leader.", 1),
                Line(LEAD, "That's usually a compliment.", 1),
                Line(ANALYST, self.pick("as_pr", [
                    f"It is. {gap_words(p2.interval).capitalize()} in hand over {self.n(p2.id, now)}, no drama, "
                    "no mistakes. That's the hardest kind of driving to notice.",
                    "It is. Nobody's talking about them because there's been nothing to talk about. That's the job done "
                    "properly.",
                ]), 1)]

    # --- battle: followed while it lasts, paid off when it breaks
    def _open_battle(self, now, key=None):
        tr = self.tr
        key = key or ("battle", tuple())
        cands = []
        for (a, b), bt in tr.battles.items():
            ca, cb = tr.car(a), tr.car(b)
            if ca and cb and bt.get("told") and cb.interval is not None and \
                    not self._threaded("battle", (a, b), now):
                cands.append((cb.interval + tr.pos_of(a) * 0.1, a, b))
        cands.sort()
        if not cands:
            return None, 0, []
        _, a, b = cands[0]
        ca = tr.car(a)
        what = "the lead" if tr.pos_of(a) == 1 else place(tr.pos_of(a))
        stakes = ("Everything riding on this one, too." if what == "the lead" else
                  "That's a big fight in the race for the podium." if tr.pos_of(a) <= 3 else
                  "Plenty at stake in the middle of that pack.")
        parts = [
            Line(LEAD, self.pick("th_b_open", [
                f"Right, eyes on {what}. {self.n(b, now)} is right on the back of {self.n(a, now)}, "
                f"{gap_words(tr.car(b).interval)} between them. {stakes}",
                f"Have a look at this fight for {what}: {self.n(a, now)} ahead, {self.n(b, now)} all over it.",
                f"{self.n(b, now)} has got right up behind {self.n(a, now)} for {what}. Stay with this one.",
            ]), 2),
            Line(ANALYST, self.pick("th_b_open_a", [
                f"This is where {self.n(a, now)} needs a clean exit. One wheel off line and it's gone.",
                f"Watch the exits. {self.n(a, now)} can't be late on a single one now.",
                f"{self.n(b, now)} is close enough to force a mistake. That's the game now.",
            ]), 1),
        ]
        self.covered(("battle", (a, b)), now)
        return ("battle", (a, b)), tr.car(b).interval, parts

    def _beat_battle(self, st, now):
        """A fight, followed a beat at a time. Each beat has a shape a real booth uses: a question answered with
        the lap times, a prediction that the other one disputes, a bit of stick when a call is slow to come off,
        or the gap read out and picked up. The voice that didn't speak last opens it."""
        tr = self.tr
        a, b = st.key[1]
        ca, cb = tr.car(a), tr.car(b)
        if not (ca and cb) or ca.gone or cb.gone or cb.interval is None:
            return [], True
        if any((c.trouble_t is not None and c.trouble_t >= st.since) or c.in_pit or
               (c.pit_in_t is not None and c.pit_in_t >= st.since) for c in (ca, cb)):
            self.calls.pop(frozenset((a, b)), None)           # a stop or a spin ended it: nobody called that
            return [], True
        if tr.pos_of(b) != tr.pos_of(a) + 1:
            return [], True                                   # not nose to tail any more: the events said it
        if st.beat >= THREAD_BEATS or cb.interval > 2.2:
            return self._payoff_battle(st, now, ca, cb), True
        moved = abs((cb.interval or 0) - st.mark) >= 0.15
        grew = (cb.interval or 0) - st.mark
        st.mark = cb.interval or 0
        me = self._next_voice()
        you = self._other(me)
        an, bn = self.n(a, now), self.n(b, now)
        gap = cb.interval or 0
        rec = self.calls.get(frozenset((a, b)), {})
        shapes = ["ask", "watch"]
        if not any(w in rec for w in (LEAD, ANALYST)) and st.beat <= 2:
            shapes += ["debate", "debate"]
        if any(w in rec for w in (LEAD, ANALYST)) and st.beat >= 2 and not st.teased and \
                now - rec.get("t", now) > 45:
            shapes += ["stick", "stick"]
        shape = self.rng.choice(shapes)

        if shape == "debate":                                 # a call, disputed, and the call defended
            _, fav, read = self._read_fight(a, b, now)
            if read in list(self._said)[-6:]:
                read = self._read_fight(a, b, now)[2]
            if fav == b:
                claim = self.pick("db_b", [
                    f"I think {bn} gets this done. {read}",
                    f"I'll say it now: {bn} is going to be through before long. {read}",
                ])
                laps = self._laps_left_words()
                counter = self.pick("db_b_no", [
                    f"I'm not so sure. Quicker isn't past, and {an} has the line. {laps.capitalize()}, too.",
                    f"See, I'd back {an}. {bn} has been sat there and nothing has happened yet.",
                    f"Maybe, but {an} only has to get it right one corner at a time, and {laps}.",
                ])
            else:
                claim = self.pick("db_a", [
                    f"I think {an} keeps this. {read}",
                    f"For me {an} holds on here. {read}",
                ])
                counter = self.pick("db_a_no", [
                    f"I'd disagree. {bn} is {gap_words(gap)} away and has been all lap. That pressure tells eventually.",
                    f"Really? I think {bn} has one big lunge in there somewhere, and {gap_words(gap)} is close enough.",
                    f"I don't know. {an} has had a mirror full of {bn} for a while now. Mistakes come from that.",
                ])
            self._call((a, b), me, fav, now)
            self._call((a, b), you, b if fav == a else a, now)
            back = self.pick("db_back", [
                "We'll see. Mark it down, we'll come back to it.",
                "Give it a couple of laps. I'm happy with my call.",
                "Well, one of us is going to look clever.",
                "Fair enough. Let's let them settle it.",
            ])
            return [Line(me, claim, 1), Line(you, self._addr(me, counter, 0.5), 1), Line(me, back, 1)], False

        if shape == "stick" and rec:                          # a call that hasn't come off yet
            st.teased = True
            picker = next(w for w in (LEAD, ANALYST) if w in rec)
            other = self._other(picker)
            pk = self.n(rec[picker], now)
            if rec[picker] == b:                              # backed the chaser, still nothing
                return [Line(other, self.pick("stick_b", [
                    f"{self._name(picker)}, you had {pk} getting through. Still waiting.",
                    f"Still {an} in front, {self._name(picker)}. Just saying.",
                    f"How's that prediction looking, {self._name(picker)}?",
                ]), 1), Line(picker, self.pick("stick_b_a", [
                    f"Still saying it. {gap_words(gap).capitalize()}, and {pk} isn't going away." if grew <= 0.1 else
                    f"All right, it's taking longer than I thought. {gap_words(gap).capitalize()} now.",
                    f"Ask me again in two laps. {pk} is right there.",
                ]), 1)], False
            return [Line(picker, self.pick("stick_a", [
                f"Still {pk} in front, {self._name(other)}. I'm feeling quite good about my call.",
                f"Nothing yet from {bn}. Told you {pk} had this covered.",
            ]), 1), Line(other, self.pick("stick_a_a", [
                f"It's not over, there's {self._laps_left_words()}." if self._laps_left() != 1 else
                "One lap. That's all it takes.",
                f"{gap_words(gap).capitalize()}, though. I wouldn't be celebrating yet.",
            ]), 1)], False

        if shape == "ask":                                    # a real question, and an answer with a number in it
            q = self.pick("th_b_question", [
                f"Can {bn} actually make this stick, do you think?",
                f"Is there a way through for {bn}?",
                f"Is {bn} quicker, or just close?",
                f"Does {bn} have enough to get by, though?",
            ])
            read, _, _ = self._read_fight(a, b, now)
            return [Line(me, self._addr(you, q), 1), Line(you, read, 1)], False

        if gap < 0.4:
            txt = self.pick("th_b_tight", [
                f"Look at that, {gap_words(gap)} in it, {bn} is virtually alongside.",
                f"Nothing at all between them now! {bn} is right there.",
                f"{bn} on the back of {an}, {gap_words(gap)}. Barely a car's width in it.",
                f"Right on {an}'s gearbox, {gap_words(gap)}. {an} must know.",
            ])
            reply = self.pick("th_b_tight_a", [
                f"And that's where it goes wrong for the car in front. One lock-up and {bn} is through.",
                f"{an} can't cover both sides for ever. Something's got to give.",
                "Watch the next braking zone. That's the one.",
            ])
        elif moved and grew < 0:
            txt = self.pick("th_b_move", [
                f"Closer still, {gap_words(gap)} now. {bn} is edging in.",
                f"Down to {gap_words(gap)}. {bn} is picking the moment.",
                f"{bn} keeps chipping away, {gap_words(gap)} the difference now.",
            ])
            reply = self.pick("th_b_move_a", [
                f"That's not a gap that's going to hold. {an} is going to have to start defending.",
                f"{an} will have seen that on the dash. Nothing to be done about it but keep it tidy.",
            ])
        elif moved:
            txt = self.pick("th_b_out", [
                f"And {an} has pulled a little out of that, {gap_words(gap)} now.",
                f"{an} has answered. {gap_words(gap).capitalize()}, out of reach for a moment.",
            ])
            reply = self.pick("th_b_out_a", [
                f"That's a response. {bn} pushed and {an} found a bit more.",
                f"Interesting. Maybe {bn} has used the best of it already.",
            ])
        else:
            txt = self.pick("th_b_hold", [
                f"Still {gap_words(gap)}. {an} is holding on, just about.",
                f"No way through yet. {bn} is there, {gap_words(gap)} back.",
                f"Held at {gap_words(gap)}. {bn} has to find something else.",
                f"{an} is driving to a plan here, {gap_words(gap)} the margin.",
            ])
            reply = self._read_fight(a, b, now)[2]
        return [Line(me, txt, 1), Line(you, reply, 1)], False

    def _payoff_battle(self, st, now, ca, cb):
        a, b = ca.id, cb.id
        self.covered(("battle", (a, b)), now)                # told and paid off: not reopened straight away
        if ca.gone or cb.gone:
            self.calls.pop(frozenset((a, b)), None)
            return [Line(LEAD, f"And that's the end of that one between {self.n(a, now)} and "
                                f"{self.n(b, now)}.", 1)]
        if cb.interval is not None and cb.interval > 2.0:
            settle = self._settle(a, b, now)
            return [
                Line(LEAD, self.pick("th_b_over_l", [
                    f"And {self.n(b, now)} has had to think again, that gap is stretching now.",
                    f"{self.n(a, now)} has seen that off, {self.n(b, now)} is dropping back.",
                ]), 1),
            ] + (settle or [
                Line(ANALYST, self.pick("th_b_over_a", [
                    f"That's a big answer from {self.n(a, now)}. {self.n(b, now)} needs to find it again.",
                    f"The move never came. {self.n(b, now)} is going to have to think of something else.",
                ]), 1),
            ])
        rec = self.calls.get(frozenset((a, b)), {})
        if not any(w in rec for w in (LEAD, ANALYST)):        # nobody has called it yet: a call to close on
            me = self._next_voice()
            fav = self._read_fight(a, b, now)[1]
            self._call((a, b), me, fav, now)
            return [Line(me, self.pick("th_b_close", [
                f"We'll leave it there for now, but I'll put it on record: {self.n(fav, now)} comes out on top.",
                f"Let's park that one. My money's on {self.n(fav, now)}, for what it's worth.",
            ]), 1), Line(self._other(me), self.pick("th_b_close_a", [
                "Noted. I'll remind you.",
                "Brave. We'll see.",
                "I'll hold you to that.",
            ]), 1)]
        return [Line(self._next_voice(), self.pick("th_b_done", [
            f"{self.n(a, now)} has soaked up that pressure so far, and {self.n(b, now)} has it all to do again.",
            f"{self.n(b, now)} showed the nose, {self.n(a, now)} didn't blink. We'll come back to this one.",
        ]), 1)]

    # --- charge: a driver on the move, followed up the field
    def _open_charge(self, now, key=None):
        movers = self._movers(now)
        if isinstance(key, int):
            movers = [m for m in movers if m[0].id == key] or movers
        if not movers:
            return None, 0, []
        c, gained, _ = movers[0]
        p = self.tr.pos_of(c.id)
        nm = self.n(c.id, now)
        parts = [
            Line(LEAD, self.pick("th_c_open", [
                f"Have a look at {nm}, up to {place(p)} now and this car is flying.",
                f"{nm} is on a charge here, {plural(c.grid - p, 'place')} gained and now {place(p)}.",
                f"From {place(c.grid)} on the grid, {nm} is up to {place(p)}. Some drive, this.",
            ]), 2),
            Line(ANALYST, self.pick("th_c_open_a", [
                f"The car looks hooked up, too, and that makes {nm} dangerous.",
                "Great drive so far. The question is how much further this goes.",
                f"Brave, clean moves, one after another. {nm} believes in this.",
            ]), 1),
        ]
        cb = self._callback(c.id, now)
        if cb:
            parts[1] = Line(ANALYST, self.pick("th_c_cb", [
                f"And remember, this is {cb}. Quite a turnaround." if "lost" in cb or "stopped" in cb else
                f"And this is {cb}. It hasn't stopped coming since.",
                f"That's {cb}, {self.lead_name}. " + ("Quite a recovery." if "lost" in cb or "stopped" in cb
                                                          else "Consistent, isn't it?"),
            ]), 1)
            parts.append(Line(LEAD, self.pick("th_c_cb_l", [
                "Good memory. I'd forgotten that.",
                "So it has. Where does this end, then?",
                "Right, and that makes this even better.",
            ]), 1))
        return ("charge", c.id), float(p), parts

    def _beat_charge(self, st, now):
        tr = self.tr
        c = tr.car(st.key[1])
        if c is None or c.gone:
            return [], True
        p = tr.pos_of(c.id)
        if p <= 2 or st.beat >= THREAD_BEATS:
            return self._payoff_charge(st, now, c), True
        if p >= st.mark:
            return [], False                                  # no new place yet: nothing to move the story on
        st.mark = float(p)
        who = LEAD if st.beat % 2 else ANALYST
        other = ANALYST if who == LEAD else LEAD
        ahead = tr.at(p - 1)
        gap = gap_words(c.interval) if c.interval is not None else "a gap"
        what = "second" if p - 1 == 2 else place(p - 1)
        return [
            Line(who, self.pick("th_c_beat", [
                f"Now {place(p)}. {self.n(c.id, now)} has {self.n(ahead.id, now)} next, {gap} up the road.",
                f"Up another place, {place(p)}. {self.n(ahead.id, now)} is {gap} ahead.",
                f"This is relentless. {place(p)}, and {self.n(ahead.id, now)} is {gap} up the road.",
            ]), 2),
            Line(other, self.pick("th_c_beat_a", [
                f"{self.n(ahead.id, now)} is going to have to defend there. {self.n(c.id, now)} is coming.",
                f"One more and that's {what}. Where does this end?",
                f"{self.n(ahead.id, now)} has a mirror full of {self.n(c.id, now)} now.",
            ]), 1),
        ], False

    def _payoff_charge(self, st, now, c):
        p = self.tr.pos_of(c.id)
        if p <= 2:
            return [Line(ANALYST, f"And now {place(p)} for {self.n(c.id, now)}. From the back of the grid, "
                                   "this is a remarkable drive."), Line(LEAD, "Remarkable is the word. And it's not "
                                                                            "finished yet.")]
        if c.grid and p < c.grid:
            return [Line(LEAD, f"{self.n(c.id, now)} has taken {plural(c.grid - p, 'place')} in this race, from "
                                f"{place(c.grid)} to {place(p)}. Drive of the day so far."),
                    Line(ANALYST, "And a long way left to go. Keep an eye on that car.")]
        return [Line(LEAD, f"{self.n(c.id, now)} is up to {place(p)} and still coming. Drive of the day so far."),
                Line(ANALYST, "There's more in this yet. Keep an eye on that car.")]

    # --- trend: where the race is heading, over the last few laps
    def _open_trend(self, now, key=None):
        tr = self.tr
        if tr.race_laps < 3:
            return None, 0, []
        ld = tr.leader()
        if ld is None:
            return None, 0, []
        sooner = [c for c in tr.cars.values()
                  if c.id != ld.id and not c.gone and not c.in_pit and c.last and c.laps_timed
                  and len(c.laps_timed) >= 2 and c.last < c.laps_timed[-2] - 0.35 and c.interval is not None]
        if sooner:
            c = min(sooner, key=lambda x: x.interval)
            p = tr.pos_of(c.id)
            mine, theirs = c.laps_timed[-2], c.last
            what = "the lead" if p == 1 else place(p)
            return ("trend", (c.id, ld.id)), float(p), [
                Line(ANALYST, self.pick("th_t_open", [
                    f"Look at the timing screens. {self.n(c.id, now)} has found {gap_words(theirs and mine - theirs)} "
                    f"on the last lap alone, and it's for {what}.",
                    f"{self.n(c.id, now)} is getting quicker lap by lap, a {lap_time_words(theirs)} last time round. "
                    f"That gap to {self.n(ld.id, now)} won't last at this rate.",
                ]), 1),
                Line(LEAD, self.pick("th_t_open_l", [
                    f"And that's {gap_words(c.interval)} between them as it stands. This is becoming serious.",
                    f"Watch that gap come down. {self.n(c.id, now)} is coming for this one.",
                ]), 1),
            ]
        if self.chance(0.5):
            return None, 0, []
        # nobody is closing: the leader is easing away, which is a story in itself
        p2 = tr.at(2)
        if p2 is None or p2.interval is None:
            return None, 0, []
        return ("stretch", (ld.id, p2.id)), float(p2.interval), [
            Line(LEAD, f"At the front, {self.n(ld.id, now)} is easing clear of {self.n(p2.id, now)}, "
                       f"{gap_words(p2.interval)} now."),
            Line(ANALYST, self.pick("th_s_open", [
                f"That's a gap built on pure pace. {self.n(p2.id, now)} has no answer to it.",
                f"Nothing {self.n(p2.id, now)} can do about that at the moment. It's a long afternoon from here.",
            ]), 1),
        ]

    def _beat_trend(self, st, now):
        c, ld = (self.tr.car(i) for i in st.key[1])
        if c is None or ld is None or c.gone:
            return [], True
        if c.interval is None or c.interval > 3.0 or st.beat >= 2:
            return [], True
        st.mark = c.interval
        return [Line(ANALYST, self.pick("th_t_beat", [
            f"Down to {gap_words(c.interval)} now. {self.n(c.id, now)} is right in this.",
            f"And still it comes, {gap_words(c.interval)}. {self.n(ld.id, now)} is hanging on.",
        ]), 1),
            Line(LEAD, "Into the last few laps. This could get very interesting.")], False

    def _beat_stretch(self, st, now):
        ld, p2 = (self.tr.car(i) for i in st.key[1])
        if ld is None or p2 is None or p2.interval is None:
            return [], True
        if st.beat >= 1:
            return [], True
        return [Line(ANALYST, self.pick("th_s_beat", [
            f"{gap_words(p2.interval)} and growing. {self.n(p2.id, now)} needs something soon.",
            f"{self.n(ld.id, now)} is managing this race from the front. That's a lead built properly.",
        ]), 1)], False

    # --- the viewer's driver: their afternoon, not just this second
    def _open_player(self, now, key=None):
        tr = self.tr
        me = tr.player()
        if me is None or me.gone:
            return None, 0, []
        p = tr.pos_of(me.id)
        if not p:
            return None, 0, []
        nm = self.n(me.id, now)
        if me.grid and me.grid > p:
            parts = [
                Line(LEAD, f"An afternoon's work from {nm}, this: up to {place(p)} from {place(me.grid)} on the grid."),
                Line(ANALYST, self.pick("th_pl_gain", [
                    f"Brave, clean progress, one car at a time. {nm} is driving beautifully.",
                    f"From {place(me.grid)} as well. That's a proper drive going on here.",
                ]), 1),
            ]
        elif me.grid and me.grid < p:
            parts = [
                Line(LEAD, f"Tough afternoon for {nm}, down to {place(p)} now from {place(me.grid)}."),
                Line(ANALYST, self.pick("th_pl_loss", [
                    f"Nothing falling for {nm} today. It's about salvaging something from here.",
                    f"A long way back from {place(p)}. They'll keep at it, though.",
                ]), 1),
            ]
        elif p <= 3:
            parts = [
                Line(LEAD, f"And {nm} is {place(p)}, right in the fight at the front now."),
                Line(ANALYST, self.pick("th_pl_front", [
                    f"Hold this together and that's a big afternoon for {nm}.",
                    f"{place(p)} and looking settled. Just keep it clean.",
                ]), 1),
            ]
        else:
            parts = [
                Line(LEAD, f"{nm} is running {place(p)}."),
                Line(ANALYST, self.pick("th_pl_mid", [
                    f"Nothing wrong with that, {self.lead_name}. It's a long race and the stops haven't shaken out.",
                    f"A quiet afternoon so far. {nm} will want more than this.",
                ]), 1),
            ]
        return ("player", me.id), float(p), parts

    def _beat_player(self, st, now):
        me = self.tr.car(st.key[1])
        if me is None or me.gone:
            return [], True
        p = self.tr.pos_of(me.id)
        if st.beat >= 2 or p == st.mark:
            return [], True
        st.mark = float(p)
        return [Line(LEAD, f"And now {place(p)} for {self.n(me.id, now)}."),
                Line(ANALYST, self.pick("th_pl_beat", [
                    f"That's the progress you want. {self.lead_name}, I'd say there's more in it.",
                    "One more place. And the car ahead isn't far up the road either.",
                ]), 1)], False

    # --- pits: the stops happening now
    def _open_pits(self, now, key=None):
        """The pit phase as a conversation with the pit lane: the booth throws to the reporter, who brings the
        stop and what it means, and the analyst takes it on."""
        if now - self.recent_pairs.get(("pits", "cycle"), -1e9) < 8.0:
            return None, 0, []
        innow = [c for c in self.tr.cars.values() if c.in_pit and not c.gone]
        if not innow:
            return None, 0, []
        c = innow[0]
        still = [x for x in self.tr.cars.values() if not x.gone and not x.pits and x.status not in ("dnf", "dsq")]
        n_still = len(still)
        if not self._mia:
            return ("pits", "cycle"), float(n_still), [
                Line(LEAD, f"In the pit lane, {self.n(c.id, now)} is on the stops."),
                Line(ANALYST, f"And {plural(n_still, 'car')} still to come in. A stop can make or break an "
                              "afternoon from here."),
            ]
        others = [x for x in innow[1:4]]
        busy = (f" {self.n(others[0].id, now)} is in as well" + (f", and {self.n(others[1].id, now)}" if len(others) > 1
                                                                 else "") + ", so it's busy down here.") if others else ""
        return ("pits", "cycle"), float(n_still), [
            Line(LEAD, self.pick("pits_throw", [
                f"Let's go down to the pit lane. {self.pit_name}, what's happening?",
                f"{self.pit_name}, it's getting busy where you are.",
                f"The stops are starting to shape this. {self.pit_name}?",
            ])),
            Line(PIT, f"{self.n(c.id, now)} is on the stop right now.{busy} "
                      f"{plural(n_still, 'car')} still to come in."),
            Line(ANALYST, self.pick("pits_open_a", [
                f"And that's where it gets interesting, {self.pit_name}. Stay out longer and you might jump people, "
                "or you might fall into traffic.",
                "This is the bit of the race where you win or lose it without anyone overtaking.",
            ]), 1),
        ]

    def _beat_pits(self, st, now):
        tr = self.tr
        innow = [c for c in tr.cars.values() if c.in_pit and not c.gone]
        still = [x for x in tr.cars.values() if not x.gone and not x.pits and x.status not in ("dnf", "dsq")]
        st.mark = float(len(still))
        pv = self.pit_voice()
        if not still:
            best = min(self.lane_times, key=self.lane_times.get) if self.lane_times else None
            tail = (f" Quickest stop of the day went to {self.n(best, now)}, "
                    f"{num_words(int(round(self.lane_times[best])))} seconds." if best is not None and tr.car(best)
                    else "")
            return [Line(pv, f"That's the whole field through the lane now.{tail}"),
                    Line(ANALYST, self.pick("pits_done_a", [
                        f"Thanks, {self.pit_name}. So no more hiding: what you see on track now is the real order.",
                        "And now it's a straight fight, nothing held back.",
                    ]) if self._mia else "And now it's a straight fight, nothing held back.", 1)], True
        if not innow and st.beat >= 2:
            return [], True
        if not self._mia:
            if not innow:
                return [], False
            c = innow[0]
            return [Line(pv, f"Now {self.n(c.id, now)} on the stops. {plural(len(still), 'car')} left to come in.", 1),
                    Line(ANALYST, "This needs to be tidy. The lane is busy.")], False
        long_one = min(still, key=lambda x: tr.pos_of(x.id))
        ln = self.n(long_one.id, now)
        lp = place(tr.pos_of(long_one.id))
        roll = self.rng.random()
        if roll < 0.4:                       # the analyst asks the pit lane, the pit lane answers with the data
            return [Line(ANALYST, self.pick("pits_ask", [
                f"{self.pit_name}, who's the one still to stop that we should be watching?",
                f"{self.pit_name}, who's still out there without a stop?",
            ]), 1), Line(PIT, f"{ln}, {self.analyst_name}. Running {lp} and still hasn't been in. "
                              f"{plural(len(still) - 1, 'other car')} as well." if len(still) > 1 else
                     f"Just {ln} now, running {lp}. Everyone else has been through here.", 1)], False
        if roll < 0.75:                      # the pit lane and the analyst don't see it the same way
            return [Line(PIT, self.pick("pits_view", [
                f"{self.analyst_name}, I think {ln} is running out of time to make staying out work. "
                f"{lp.capitalize()} on the road, but that stop is still to come.",
                f"I'd say {ln} has left it late. When that stop comes, {lp} isn't going to survive it.",
            ]), 1), Line(ANALYST, self.pick("pits_view_a", [
                f"I'm not sure I agree, {self.pit_name}. Clear track in front, {ln} can push every lap.",
                "Maybe. But it's not done until the stop is done. A quick one and that changes everything.",
            ]), 1), Line(PIT, self.pick("pits_view_b", [
                "We'll know soon enough. The crew will be out when it's time.",
                "Fair, but I've seen enough stops today to know how long they take.",
            ]), 1)], False
        if not innow:
            return [], False
        c = innow[0]
        return [Line(PIT, self.pick("th_p_beat", [
            f"{self.n(c.id, now)} is in and the crew are over the wall. {plural(len(still), 'car')} still to come.",
            f"Now {self.n(c.id, now)} on the stops. {plural(len(still), 'car')} left to come in.",
        ]), 1), Line(LEAD, self.pick("th_p_beat_l", [
            f"Thanks, {self.pit_name}. Every one of those stops reshuffles this order.",
            f"Keep us posted, {self.pit_name}.",
        ]), 1)], False

    # ------------------------------------------------------------------ the old single-voice fillers
    def _f_battle(self, now):
        tr = self.tr
        best = None
        for (a, b), bt in tr.battles.items():
            ca, cb = tr.car(a), tr.car(b)
            if not (ca and cb and bt.get("told")) or cb.interval is None or cb.interval > 1.2:
                continue
            score = now - max(ca.mentioned, cb.mentioned) - tr.pos_of(a) * 3
            if best is None or score > best[0]:
                best = (score, ca, cb)
        if best is None or now - max(best[1].mentioned, best[2].mentioned) < 20:
            return []
        _, ca, cb = best
        if self._threaded("battle", (ca.id, cb.id), now):
            return []
        pos = tr.pos_of(ca.id)
        what = "the lead" if pos == 1 else place(pos)
        return [Line(LEAD, self.pick("f_battle", [
            f"Still a tight one between {self.n(ca.id, now)} and {self.n(cb.id, now)} for {what}, {gap_words(cb.interval)} in it.",
            f"{self.n(cb.id, now)} is still hounding {self.n(ca.id, now)}, {gap_words(cb.interval)} apart, the fight for {what}.",
            f"Let's go back to the battle for {what}. {self.n(ca.id, now)} ahead, {self.n(cb.id, now)} right there, {gap_words(cb.interval)}.",
        ]), 1)]

    def _player_line(self, me, now, who=LEAD):
        tr = self.tr
        p = tr.pos_of(me.id)
        ahead = tr.at(p - 1)
        behind, gb = self._behind(me.id)
        bits = [f"{self.n(me.id, now)} is running {place(p)}"]
        if ahead and me.interval is not None:
            bits.append(f"{gap_words(me.interval)} behind {self.n(ahead.id, now)}")
        if behind and gb is not None and gb < 3:
            bits.append(f"with {self.n(behind.id, now)} {gap_words(gb)} back")
        return [Line(who, ", ".join(bits) + ".", 1)]

    def _f_player(self, now):
        tr = self.tr
        me = tr.player()
        if me is None or me.in_pit or now - me.mentioned < 25:
            return []
        out = self._player_line(me, now)
        if me.last and me.best and len(me.laps_timed) >= 2:
            rank = sorted(c.last for c in tr.cars.values() if c.last and not c.gone and not c.in_pit)
            if rank and me.last <= rank[min(2, len(rank) - 1)]:
                out.append(Line(ANALYST, self.pick("f_me_fast", [
                    f"And {self.n(me.id)} is one of the quickest cars out there at the moment, a {lap_time_words(me.last)} last time round.",
                    f"The pace is there for {self.n(me.id)}. That last lap was a {lap_time_words(me.last)}.",
                ]), 1))
            elif me.interval is not None and me.interval < 1.5:
                out.append(Line(ANALYST, f"Within striking distance. {self.n(me.id)} just needs the right moment.", 1))
        return out

    def _f_pace(self, now):
        tr = self.tr
        laps = [(c.last, c) for c in tr.cars.values() if c.last and not c.gone and not c.in_pit and len(c.laps_timed) >= 1]
        if len(laps) < 3:
            return []
        laps.sort(key=lambda x: x[0])
        t, c = laps[0]
        if now - c.mentioned < 25:
            t, c = laps[1]
        out, told = [], getattr(self, "_pace_told", set())
        for t, c in laps:
            txt = self.pick("f_pace", [
                f"Quickest out there on the last lap was {self.n(c.id, now)}, a {lap_time_words(t)}.",
                f"Interesting, {self.n(c.id, now)} is putting in some quick laps, a {lap_time_words(t)} that last one.",
            ])
            if txt not in told:
                out.append(Line(ANALYST, txt, 1))
                break
        if not out:
            t, c = laps[0]
            out.append(Line(ANALYST, f"Quickest out there on the last lap was {self.n(c.id, now)}, a {lap_time_words(t)}.", 1))
        told.add(out[0].text)
        self._pace_told = told
        if tr.fastest and tr.fastest[1] != c.id:
            line = f"Fastest lap still belongs to {self.n(tr.fastest[1], now)}, a {lap_time_words(tr.fastest[0])}."
            if line not in told:
                out.append(Line(LEAD, line, 1))
                told.add(line)
        return out

    def _f_pits(self, now):
        tr = self.tr
        cars = [c for c in tr.cars.values() if not c.gone and c.status not in ("dnf", "dsq")]
        stopped = [c for c in cars if c.pits]
        if not stopped or len(stopped) == len(cars):
            return []
        still = sorted((c for c in cars if not c.pits), key=lambda c: tr.pos_of(c.id))[:3]
        names = ", ".join(self.n(c.id, now) for c in still)
        return [Line(self.pit_voice(), self.pick("f_pits", [
            f"{num_words(len(stopped)).capitalize()} cars have made their stop so far. Still to come in: {names}.",
            f"In the pit lane, we're waiting on {names}. They're the ones still to stop.",
        ]), 1)]

    def _f_field(self, now):
        """A driver further down who hasn't been mentioned in a while."""
        tr = self.tr
        busy = {cid for pair in (s.key[1] for s in self.stories.values() if s.kind == "battle")
                for cid in pair}                       # drivers the booth is already following
        busy |= {s.key[1] for s in self.stories.values() if s.kind in ("charge", "player")}
        cands = [c for c in tr.cars.values() if not c.gone and not c.in_pit and tr.pos_of(c.id) > 3 and not c.player
                 and c.id not in busy and now - c.mentioned > 60 and c.status not in ("dnf", "dsq")]
        if not cands:
            return []
        c = min(cands, key=lambda c: c.mentioned)
        p = tr.pos_of(c.id)
        ahead = tr.at(p - 1)
        gap = f", {gap_words(c.interval)} behind {self.n(ahead.id, now)}" if ahead and c.interval is not None else ""
        extra = ""
        if c.grid and c.grid != p:
            extra = f" Started {place(c.grid)}."
        return [Line(LEAD, self.pick("f_field", [
            f"Further back, {self.n(c.id, now)} is running {place(p)}{gap}.{extra}",
            f"Let's check in on {self.n(c.id, now)}: {place(p)}{gap}.{extra}",
            f"Down in {place(p)}, it's {self.n(c.id, now)}{gap}.{extra}",
        ]), 1)]

    def _filler_grid(self, now):
        tr = self.tr
        if now - getattr(self, "_grid_told", -1e9) < 25:
            return []
        self._grid_told = now
        p1, p2 = tr.at(1), tr.at(2)
        if not (p1 and p2):
            return []
        return [Line(ANALYST, self.pick("grid", [
            f"{self.n(p1.id, now)} and {self.n(p2.id, now)} on the front row. The run to the first corner will be fascinating.",
            "The cars are on the grid. Not long now, everyone's just waiting for the lights.",
        ]), 1)]

    def _filler_quali(self, now):
        tr = self.tr
        p1, p2 = tr.at(1), tr.at(2)
        if not p1 or not p1.best:
            return []
        out = [Line(LEAD, f"{self.n(p1.id, now)} still sets the pace, a {lap_time_words(p1.best)}.", 1)]
        if p2 and p2.gap is not None:
            out.append(Line(ANALYST, f"{self.n(p2.id, now)} is {gap_words(p2.gap)} off that.", 1))
        me = tr.player()
        if me and me.best and tr.pos_of(me.id) > 2 and now - me.mentioned > 30:
            out.append(Line(ANALYST, f"{self.n(me.id, now)} is {place(tr.pos_of(me.id))} for now, {gap_words(me.gap)} off the top.", 1))
        return out

    def final_calls(self, now):
        """At the flag, the arguments are settled by the result: one of them is brought back for the sign-off."""
        tr = self.tr
        for pair, rec in list(self.calls.items()):
            del self.calls[pair]
            cars = [tr.car(c) for c in pair]
            if len(pair) != 2 or any(c is None or c.gone or c.status in ("dnf", "dsq") for c in cars):
                continue
            ca, cb = sorted(cars, key=lambda c: tr.pos_of(c.id))
            intro = Line(LEAD, self.pick("final_call", [
                f"And {self.n(ca.id, now)} against {self.n(cb.id, now)}, which we argued about: "
                f"{self.n(ca.id, now)} came out ahead, {place(tr.pos_of(ca.id))} to {place(tr.pos_of(cb.id))}.",
                f"One more thing. {self.n(ca.id, now)} and {self.n(cb.id, now)}: {self.n(ca.id, now)} finished "
                "in front.",
            ]), 1)
            rec["t"] = now                                    # judged on the result, whatever happened on the way
            self.calls[pair] = rec
            settle = self._settle(ca.id, cb.id, now)
            if settle:
                return [intro] + settle
        return []

    def wrap_up(self, now):
        tr = self.tr
        w = tr.car(tr.winner)
        if not w:
            return []
        calls = self.final_calls(now)
        p2, p3 = tr.at(2), tr.at(3)
        pod = f", ahead of {self.n(p2.id, now)}" + (f" and {self.n(p3.id, now)}" if p3 else "") if p2 else ""
        event = persona.the_race(self.cfg)
        of = f" of {event}" if event else ""
        out = [Line(LEAD, f"So, that's the result{of} here at {tr.track or 'the circuit'}: victory for {self.n(w.id, now, full=True)}{pod}.", 1)]
        out += calls
        me = tr.player()
        if me and me.finished:
            out.append(Line(ANALYST, f"And {place(tr.pos_of(me.id))} for {self.n(me.id, now)}. Thanks for watching, everybody.", 1))
            out.append(Line(LEAD, "Thank you for having us. Goodnight.", 1))
        else:
            out.append(Line(ANALYST, f"Great race. Thanks for having me, {self.lead_name}.", 1))
            out.append(Line(LEAD, "Our thanks to you too. Goodnight, everybody.", 1))
        return out
