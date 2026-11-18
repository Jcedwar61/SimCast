"""The booth off the leash: outbursts on the big moments, random conversations in the quiet spells and sponsor
mentions, for the instant lines (the AI booth gets the same from its prompt). Mixed into writer.Writer.

How far they go is up to the settings: 'booth_tone' (broadcast, loose, unhinged), 'swearing' and 'banter' (how
often the booth drifts into a side conversation when there's time). Every line here starts from something on
screen or just said, and comes back to the race.
"""
from __future__ import annotations

from . import persona

LEAD, ANALYST, PIT = "lead", "analyst", "pit"

TONE_RANK = {"broadcast": 0, "loose": 1, "unhinged": 2}
OUTBURST_CHANCE = {"broadcast": 0.0, "loose": 0.35, "unhinged": 0.7}
ASIDE_PACE = {"rare": (90.0, 0.25), "normal": (50.0, 0.45), "lots": (15.0, 0.95)}   # (min gap s, chance per spell)
SPONSOR_GAP = 75.0                                                                # between sponsor mentions

# (event kind, lowest tone it fits, needs swearing on, text). {a} the first car, {b} the second, {lead}/{analyst}
OUTBURSTS = [
    ("overtake", 1, False, "Oh, that's cheeky! {a} just helped themselves to that one."),
    ("overtake", 1, False, "Ha! {b} won't be sending {a} a Christmas card this year."),
    ("overtake", 1, False, "Gone! {b} was asleep at the wheel there."),
    ("overtake", 1, True, "Bloody hell, {a}! Where did that come from?"),
    ("overtake", 1, True, "Oh, that's a hell of a move from {a}."),
    ("overtake", 2, False, "I'm standing up in the booth! {a}, you absolute maniac!"),
    ("overtake", 2, False, "{b} has just been mugged in broad daylight. Someone call the stewards. Actually, don't, it was legal."),
    ("overtake", 2, True, "Holy shit, {a}! I've spilled my coffee all over the notes!"),
    ("overtake", 2, True, "{b} is going to be absolutely fuming. Look at that. Fuming. Bloody brilliant."),
    ("moment", 1, False, "Oh dear. {a} will be wanting that one back."),
    ("moment", 1, False, "That's a bit of a moment! Somebody's heart rate just doubled."),
    ("moment", 1, True, "Oh, that's a big moment for {a}. Hell of a save, or hell of a mess, take your pick."),
    ("moment", 2, False, "No, no, no, NO! {a}, what are you DOING?"),
    ("moment", 2, False, "I can't watch. I'm watching. I can't watch."),
    ("moment", 2, True, "Oh, for f... {a}! What the hell was that?"),
    ("moment", 2, True, "Shit! That's going to look horrible on the replay, {a}."),
    ("contact", 1, False, "Ooh, they've swapped paint there. Somebody's getting a stern look in the paddock."),
    ("contact", 1, True, "That's a bloody big hit. {a} and {b} are not going to be friends after this."),
    ("contact", 2, False, "Oh, it's carnage! It's absolute carnage! I love it! I mean, I'm appalled."),
    ("contact", 2, True, "Jesus! {a} and {b}, what the hell are you two doing?"),
    ("stopped", 1, False, "Oh, that's heartbreaking for {a}."),
    ("stopped", 1, True, "Ah, damn it. That's {a}'s race in the bin."),
    ("stopped", 2, False, "No! Not {a}! I had money on {a}! Not real money. Some money."),
    ("stopped", 2, True, "Oh, that's a disaster. A proper, steaming, shitty disaster for {a}."),
    ("retired", 1, True, "Damn. {a} deserved better than that."),
    ("retired", 2, False, "And {a} is out! Somebody get that car a blanket and a cup of tea."),
    ("fastest_lap", 1, False, "Somebody's had their Weetabix this morning. {a}, flying."),
    ("fastest_lap", 1, True, "That's a hell of a lap from {a}."),
    ("fastest_lap", 2, False, "{a} has just broken physics. I want to see the data. I want to see ALL the data."),
    ("fastest_lap", 2, True, "That lap is bloody ridiculous. Who does {a} think they are?"),
    ("pit_out", 2, False, "Every crew in that lane is now doing sums on the back of a napkin."),
    ("winner", 1, False, "I'm not crying, you're crying."),
    ("winner", 1, True, "What a bloody race that was."),
    ("winner", 2, False, "I need a lie down. I genuinely need a lie down."),
    ("winner", 2, True, "That was absolutely mental. Hell of a job, {a}."),
]

# How the booth sounds on each kind of moment: (the call, the reaction). Feelings follow the racing.
KIND_MOOD = {
    "overtake": ("excited", "excited"), "moment": ("shocked", "tense"), "contact": ("shocked", "angry"),
    "stopped": ("shocked", "gutted"), "retired": ("gutted", "gutted"), "winner": ("thrilled", "excited"),
    "fastest_lap": ("excited", ""), "final_lap": ("tense", "tense"), "start": ("thrilled", "excited"),
    "battle": ("tense", "tense"), "closing": ("tense", ""), "penalty": ("shocked", "frustrated"),
    "caution": ("tense", "frustrated"), "player_finish": ("excited", ""), "podium": ("excited", ""),
    "accident": ("shocked", "angry"), "rally_moment": ("shocked", "tense"), "solo_off": ("shocked", "frustrated"),
}
OUTBURST_MOOD = {"overtake": "thrilled", "moment": "shocked", "contact": "angry", "stopped": "gutted",
                 "retired": "gutted", "fastest_lap": "excited", "pit_out": "amused", "winner": "thrilled"}
TANGENT_MOODS = {   # by line, for each side conversation
    "livery": ["amused", "sarcastic", "amused", "sarcastic"], "names": ["amused", "amused", ""],
    "roadcar": ["amused", "sarcastic", "amused"], "coffee": ["tense", "frustrated", "excited"],
    "biscuits": ["frustrated", "angry", "sarcastic", "excited"], "feud": ["", "sarcastic", "amused", "frustrated"],
    "rant": ["frustrated", "sarcastic", "angry", "sarcastic"], "conspiracy": ["tense", "frustrated", "tense",
                                                                           "sarcastic", "tense"],
    "sponsor": ["excited", "amused", "amused"],
}

# Side conversations the booth drifts into when there's time: (name, lowest tone). Each ties to a car on screen.
TANGENTS = [("livery", 0), ("names", 0), ("roadcar", 0), ("coffee", 0), ("biscuits", 1), ("feud", 1),
            ("rant", 2), ("conspiracy", 2)]


def persona_key(name):
    return " ".join(str(name or "").split()).lower()


class BanterMixin:
    """Needs the Writer's tr, cfg, rng, n(), pick(), chance(), _name(), lead_name/analyst_name/pit_name."""

    # ------------------------------------------------------------------ who's who
    def gender(self, cid):
        c = self.tr.car(cid)
        if c is None:
            return None
        if c.player and self.cfg.get("driver_pronouns", "auto") != "auto":
            return persona.from_choice(self.cfg.get("driver_pronouns"), "")
        ent = self.roster.find(c)
        if ent and ent["gender"]:
            return None if ent["gender"] == "x" else ent["gender"]   # the roster says, the booth doesn't guess
        if c.player:
            nm = (self.cfg.get("driver_name") or "").strip() or c.name
            return persona.from_choice("auto", nm)
        return persona.name_gender(c.name)

    @property
    def roster(self):
        r = self.__dict__.get("_roster")
        if r is None:
            from .roster import Roster
            r = self._roster = Roster(lambda: self.cfg)
        return r

    def car_words(self, cid, sponsor=None):
        """'the number 12 Vantor Lubricants Porsche', as much of it as is known."""
        c = self.tr.car(cid)
        if c is None:
            return "the car"
        ent = self.roster.find(c)
        num = f"number {ent['number']} " if ent and ent["number"] else ""
        make = persona.car_make(c.car)
        sp = f"{sponsor} " if sponsor else ""
        return f"the {num}{sp}{make or 'car'}"

    def pronoun(self, cid):
        return persona.pronoun(self.gender(cid))

    @property
    def sponsors(self):
        sp = getattr(self, "_sponsors", None)
        if sp is None:
            sp = self._sponsors = persona.Sponsors(lambda: self.cfg, self.roster)
        return sp

    # ------------------------------------------------------------------ settings
    @property
    def tone(self):
        return TONE_RANK.get(self.cfg.get("booth_tone", "broadcast"), 0)

    @property
    def swearing(self):
        return bool(self.cfg.get("swearing"))

    def _fill(self, text, a=None, b=None, now=None):
        return text.format(a=self.n(a, now) if a is not None else "they",
                           b=self.n(b, now) if b is not None else "them",
                           lead=self.lead_name, analyst=self.analyst_name)

    # ------------------------------------------------------------------ the big moments
    def feel(self, e, lines):
        """Give an event's lines the feeling of the moment: the call, then the reaction to it. A lead change is
        a thrill, the viewer's driver losing a place is a groan."""
        call, react = KIND_MOOD.get(e.kind, ("", ""))
        d = getattr(e, "data", {}) or {}
        if e.kind == "overtake":
            victim = self.tr.car(e.cars[1]) if len(e.cars) > 1 else None
            mover = self.tr.car(e.cars[0]) if e.cars else None
            if d.get("lead") or (mover is not None and mover.player):
                call = react = "thrilled"
            elif victim is not None and victim.player:
                call, react = "shocked", "frustrated"
        for k, ln in enumerate(lines or ()):
            if not ln.mood:
                ln.mood = (call if k == 0 else react) or ("excited" if ln.energy >= 2 else "")
        return lines

    def extras(self, e, lines, now):
        """One more line after an event call: an outburst when the booth is loose or unhinged, or a sponsor
        mention on a big moment for a backed car. [] most of the time."""
        if not lines:
            return []
        last = lines[-1].who
        who = ANALYST if last in (LEAD, PIT) else LEAD
        out = self._history_tag(e, who, now)
        if not out:
            out = self._outburst(e, who, now)
        if not out and e.kind in ("winner", "overtake", "fastest_lap", "podium") and e.cars:
            out = self._sponsor_tag(e, who, now)
        return out

    def _history_tag(self, e, who, now):
        """The booth remembers: two drivers with a past meet again, or last race's winner lines up."""
        mem = getattr(self, "memory", None)
        if mem is None or not self.cfg.get("memory", True):
            return []
        told = self.__dict__.setdefault("_hist_told", set())
        from .writer import Line
        if e.kind in ("intro", "start") and "last_winner" not in told and mem.data["races"]:
            told.add("last_winner")
            last = mem.data["races"][-1]
            win = next((r for r in last["results"] if r["pos"] == 1), None)
            if not win:
                return []
            here = next((c for c in self.tr.cars.values() if persona_key(c.name) == persona_key(win["name"])), None)
            d = mem.driver(win["name"])
            streak = d["streak"] if d else 0
            where = f" at {last['track']}" if last.get("track") else ""
            if here is None:
                return [Line(who, f"Remember, last time out{where} it was {win['name']} who won.", 1)]
            nm = self.n(here.id, now)
            return [Line(who, (f"And {nm} has won the last {streak} in a row. Can anyone stop that?" if streak >= 2
                               else f"{nm} won last time out{where}. Everyone's chasing that car today."), 2,
                         "excited")]
        if e.kind not in ("battle", "overtake", "contact") or len(e.cars) < 2:
            return []
        a, b = self.tr.car(e.cars[0]), self.tr.car(e.cars[1])
        if a is None or b is None:
            return []
        pk = frozenset((a.id, b.id))
        if pk in told:
            return []
        p = mem.pair(a.name, b.name)
        if not p:
            return []
        told.add(pk)
        an, bn = self.n(a.id, now), self.n(b.id, now)
        past = mem.pair_words(p)
        if p["kind"] == "contact":
            return [Line(who, self.pick("hist_contact", [
                f"And there's history here. {an} and {bn} {past}.",
                f"These two again! {an} and {bn} {past}. Neither of them has forgotten.",
                f"Oh, {bn} will remember {an}. They {past}.",
            ]), 2, "tense" if e.kind == "battle" else "excited")]
        return [Line(who, f"{an} and {bn}, together again. They {past}.", 1, "excited")]

    def _outburst(self, e, who, now):
        tone = self.tone
        if tone == 0 or not self.chance(OUTBURST_CHANCE.get(self.cfg.get("booth_tone"), 0.0)):
            return []
        a = e.cars[0] if e.cars else None
        b = e.cars[1] if len(e.cars) > 1 else None
        used = self.__dict__.setdefault("_ob_used", set())
        opts = [t for k, lvl, sw, t in OUTBURSTS if k == e.kind and lvl <= tone and (self.swearing or not sw)
                and ("{b}" not in t or b is not None) and ("{a}" not in t or a is not None) and t not in used]
        top = [t for t in opts if any(t == x[3] and x[1] == tone for x in OUTBURSTS)]
        if top and self.chance(0.7):
            opts = top                                   # unhinged mostly sounds unhinged, now and then just loose
        if not opts:
            return []
        from .writer import Line
        t = self.pick("ob_" + e.kind, opts)
        used.add(t)                                      # an outburst lands once a race: twice is a catchphrase
        return [Line(who, self._fill(t, a, b, now), 3 if tone == 2 else 2, OUTBURST_MOOD.get(e.kind, "excited"))]

    def _sponsor_tag(self, e, who, now):
        if now - getattr(self, "_sponsor_t", -1e9) < SPONSOR_GAP or not self.chance(0.4):
            return []
        c = self.tr.car(e.cars[0])
        lst = self.sponsors.of(c)
        if not lst:
            return []
        self._sponsor_t = now
        nm = self.n(c.id, now)
        car = self.car_words(c.id, lst[0])
        from .writer import Line
        if e.kind == "winner":
            txt = self.pick("sp_win", [f"Champagne all round at {lst[0]} tonight.",
                                       f"A very good day to have your name on {nm}'s car. {', '.join(lst)}, take a bow."])
        else:
            txt = self.pick("sp_ev", [f"And that's {car} of {nm}, doing the sponsors proud.",
                                      f"{lst[0]} will be loving that. Their name all over the replay.",
                                      f"Somebody at {lst[0]} has just spilt their coffee watching that."])
        return [Line(who, txt, 1, "amused" if self.tone else "excited")]

    # ------------------------------------------------------------------ quiet spells
    def aside_pace(self):
        return ASIDE_PACE.get(self.cfg.get("banter", "normal"), ASIDE_PACE["normal"])

    def _banter(self, now):
        """A random conversation when there's time: a sponsor read, or a tangent that starts from what's on
        screen, drifts, and comes back to a car on track. Sponsors can come round again; tangents don't."""
        pool = []
        if now - getattr(self, "_sponsor_t", -1e9) >= SPONSOR_GAP:
            pool += ["sponsor"] * (2 if self.cfg.get("banter") == "lots" else 1)
        pool += [t for t, lvl in TANGENTS if lvl <= self.tone and ("tg_" + t) not in self.asides_done]
        self.rng.shuffle(pool)
        for t in pool:
            lines = getattr(self, "_tg_" + t)(now)
            if lines:
                for ln, m in zip(lines, TANGENT_MOODS.get(t, ())):
                    ln.mood = ln.mood or m
                if t == "sponsor":
                    self._sponsor_t = now
                else:
                    self.asides_done.add("tg_" + t)
                return lines
        return []

    def _someone(self, now, fight=False):
        """A car to hang the conversation on: one in a close fight, or one that hasn't been talked about lately."""
        f = self._close_fight() if hasattr(self, "_close_fight") else None
        if f:
            return f[0], f[1]
        if fight:
            return None
        cars = [c for c in self.tr.cars.values() if not c.gone and not c.in_pit and c.status not in ("dnf", "dsq")]
        if not cars:
            return None
        c = min(cars, key=lambda c: c.mentioned)
        return c.id, None

    def _swear(self, clean, dirty):
        return dirty if self.swearing else clean

    def _tg_sponsor(self, now):
        from .writer import Line
        title = self.sponsors.title(self.tr.track)
        pick = self._someone(now)
        lst = self.sponsors.of(self.tr.car(pick[0])) if pick else []
        if title and (not lst or self.chance(0.4)):
            ld = self.tr.leader()
            if not ld:
                return []
            event = persona.the_race(self.cfg)
            return [Line(LEAD, self.pick("sp_title_n", [
                f"If you're just joining us, this is {event}, brought to you by {title}, and "
                f"{self.n(ld.id, now)} leads.",
                f"Welcome back to {event}, in association with {title}. {self.analyst_name}, has it lived up to "
                f"the billing?",
            ]) if event else self.pick("sp_title", [
                f"If you're just joining us, this is the {title} race at {self.tr.track or 'this circuit'}, and "
                f"{self.n(ld.id, now)} leads.",
                f"Welcome back to the {title} race. {self.analyst_name}, has it lived up to the billing?",
            ]), 1), Line(ANALYST, self.pick("sp_title_a", [
                f"So far, yes. {self.n(ld.id, now)} has made it look easy, mind.",
                f"Ask me again in ten minutes. {self.n(ld.id, now)} might have something to say about it.",
            ] + ([f"Better than the {title.split()[-1].lower()}, I'll tell you that much."] if self.tone else [])), 1)]
        if not lst:
            return []
        c = self.tr.car(pick[0])
        nm = self.n(c.id, now)
        what = self.car_words(c.id, lst[0])[4:]                  # 'number 12 Vantor Porsche' 
        out = [Line(LEAD, self.pick("sp_car", [
            f"Look at the {what} of {nm}. Lovely-looking thing.",
            f"{nm} in the {what}" + (f", with {lst[1]} on board as well." if len(lst) > 1 else "."),
        ]), 1)]
        if self.tone:
            out.append(Line(ANALYST, self.pick("sp_car_loose", [
                f"Has anyone actually ever bought anything from {lst[0]}? Genuine question.",
                f"I was offered a deal by {lst[0]} once. They wanted me to wear the hat. I wore the hat.",
                self._swear(f"If {nm} bins it, {lst[0]} are going to want their money back.",
                            f"If {nm} bins it, {lst[0]} are going to be bloody livid."),
            ]), 1))
            out.append(Line(LEAD, f"Anyway. {nm}, {place_of(self, c.id)} and going well.", 1))
        else:
            out.append(Line(ANALYST, self.pick("sp_car_a", [
                f"And they'll be pleased with what they're getting, {nm} running {place_of(self, c.id)}.",
                f"Good exposure for {lst[0]} today. {nm} has been on screen a lot.",
            ]), 1))
        return out

    def _tg_livery(self, now):
        from .writer import Line
        p = self._someone(now)
        if not p:
            return []
        c = self.tr.car(p[0])
        make = persona.car_make(c.car)
        if not make:
            return []
        nm = self.n(c.id, now)
        return [Line(ANALYST, f"Can we talk about how good {nm}'s {make} looks out there?", 1),
                Line(LEAD, f"We're here to call a race, {self.analyst_name}.", 1),
                Line(ANALYST, self.pick("tg_liv", [
                    "I can do both. Fast cars should look fast. That one looks fast.",
                    "I'm calling it the best-looking car on the grid. That's analysis.",
                ]), 1),
                Line(LEAD, f"Noted. And {nm} is {place_of(self, c.id)}, for the people who wanted the actual race.", 1)]

    def _tg_names(self, now):
        from .writer import Line
        p = self._someone(now)
        if not p:
            return []
        c = self.tr.car(p[0])
        if len(str(c.name).split()) < 2:
            return []
        nm = self.n(c.id, now, full=True)
        return [Line(LEAD, f"{nm}. Great name for a racing driver, isn't it?", 1),
                Line(ANALYST, self.pick("tg_nm", [
                    "You need a name that sounds good on a trophy. That one does.",
                    "Some names are made for commentary. Mine wasn't. Nobody ever got excited saying it.",
                ]), 1),
                Line(LEAD, f"Well, it's a name we're saying a lot today. {self.n(c.id, now)}, running "
                           f"{place_of(self, c.id)}.", 1)]

    def _tg_roadcar(self, now):
        from .writer import Line
        laps = [(c.last, c) for c in self.tr.cars.values() if c.last and not c.gone and not c.in_pit]
        if len(laps) < 2:
            return []
        _, fast = min(laps, key=lambda x: x[0])
        return [Line(LEAD, f"{self.analyst_name}, what do you actually drive to the track these days?", 1),
                Line(ANALYST, self.pick("tg_rc", [
                    "Something sensible with a big boot. I've earned slow.",
                    "Don't. My kids picked it. It's beige.",
                    "An estate car. I still take the long way round the roundabouts, mind.",
                ]), 1),
                Line(LEAD, f"Meanwhile {self.n(fast.id, now)} just did a lap that would have your estate car in tears.", 1)]

    def _tg_coffee(self, now):
        from .writer import Line
        f = self._someone(now, fight=True)
        if not f:
            return []
        a, b = self.n(f[0], now), self.n(f[1], now)
        return [Line(LEAD, f"I've had three coffees since {b} got up behind {a}.", 1),
                Line(ANALYST, self._swear("And neither of them has blinked. It's like watching a staring contest at a "
                                          "hundred and fifty miles an hour.",
                                          "And neither of them has bloody blinked. It's a staring contest at a "
                                          "hundred and fifty miles an hour."), 1),
                Line(LEAD, f"Fourth coffee, then. Come on, {b}.", 1)]

    def _tg_biscuits(self, now):
        from .writer import Line
        ld = self.tr.leader()
        if not ld:
            return []
        return [Line(ANALYST, f"Who's eaten all the biscuits, {self.lead_name}?", 1),
                Line(LEAD, self._swear("I have no idea what you're talking about. Look at the race.",
                                       "I have no bloody idea what you're talking about. Look at the race."), 1),
                Line(ANALYST, "There are crumbs on your notes.", 1),
                Line(LEAD, f"And {self.n(ld.id, now)} still leads! Moving on.", 1)]

    def _tg_feud(self, now):
        from .writer import Line
        p = self._someone(now)
        if not p:
            return []
        nm = self.n(p[0], now)
        return [Line(ANALYST, f"For the record, I said before the start {nm} would be the one to watch.", 1),
                Line(LEAD, "You did not. You said that about everyone. You said it about the safety car once.", 1),
                Line(ANALYST, "The safety car had a very good race that day.", 1),
                Line(LEAD, f"Fine. {nm}, {place_of(self, p[0])}. Happy?", 1)]

    def _tg_rant(self, now):
        from .writer import Line
        f = self._someone(now, fight=True)
        ld = self.tr.leader()
        if not ld:
            return []
        if f:
            a, b = self.n(f[0], now), self.n(f[1], now)
            body = self._swear(f"Lap after lap, {b} gets right up behind {a}, and then what? Nothing! Do something! "
                               "Anything! Throw it up the inside, I don't care!",
                               f"Lap after lap, {b} gets right up behind {a}, and then what? Sod all! Do something! "
                               "Anything! Throw the damn thing up the inside!")
        else:
            body = self._swear("Everyone's just circulating! It's a procession! I didn't come here to watch a "
                               "procession, I came here for chaos!",
                               "Everyone's just bloody circulating! It's a procession! I didn't drive three hours "
                               "for a procession, I came for chaos, damn it!")
        return [Line(ANALYST, f"Can I have a rant, {self.lead_name}?", 1),
                Line(LEAD, "Could I stop you?", 1),
                Line(ANALYST, body, 2),
                Line(LEAD, f"Feel better? Good. {self.n(ld.id, now)} leads, everybody.", 1)]

    def _tg_conspiracy(self, now):
        from .writer import Line
        cars = [c for c in self.tr.cars.values() if not c.gone and c.status not in ("dnf", "dsq")]
        still = [c for c in cars if not c.pits]
        tgt = min(still, key=lambda c: self.tr.pos_of(c.id)) if still and len(still) < len(cars) else None
        if tgt is None:
            p = self._someone(now)
            tgt = self.tr.car(p[0]) if p else None
        if tgt is None:
            return []
        nm = self.n(tgt.id, now)
        return [Line(ANALYST, f"I've got a theory about {nm}.", 1),
                Line(LEAD, "Oh no. Here we go.", 1),
                Line(ANALYST, self._swear(f"They're sandbagging. The whole thing. {nm} is sitting there, smiling, "
                                          "waiting for everyone else to fall apart. It's a long con.",
                                          f"They're sandbagging. The whole bloody thing. {nm} is sitting there, smiling, "
                                          "waiting for everyone else to fall to pieces. It's a long con."), 2),
                Line(LEAD, f"Or {nm} is just {place_of(self, tgt.id)}, {self.analyst_name}.", 1),
                Line(ANALYST, "That's exactly what they want you to think.", 1)]


def place_of(w, cid):
    from .writer import place
    return place(w.tr.pos_of(cid))
