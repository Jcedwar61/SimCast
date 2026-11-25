"""The real race behind this one, when Historic Roster started it.

Historic Roster (a race launcher for Assetto Corsa that recreates real races: real grids, cars and tracks) writes
%APPDATA%\\SimCast\\race_context.json each time it sets a race up: the event, the real grid and result, the real
race-day conditions and which real driver's seat the player took. With it the booth can talk about history ("in
2006 this one went to Schumacher"). Without it nothing changes.

The file is used only for Assetto Corsa, when its track matches the session's and it was written in the last 12
hours. Cars are matched by AC car index (CrewChiefEx's slot = race.ini CAR_n) first, then by name: the player's car
carries the player's own name, not the real driver's. Spoilers (the real result) are held back until the finish
unless the settings say otherwise (cfg "history_spoilers": "after", "always" or "never").
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from typing import Optional

SCHEMA = 1
MAX_AGE_S = 12 * 3600
FILE_NAME = "race_context.json"
SPOILERS = ("after", "always", "never")


def context_path() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SimCast", FILE_NAME)


def fold(s) -> str:
    """Names compared without accents, case or punctuation ('Räikkönen' == 'raikkonen')."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _int(x) -> Optional[int]:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _parse_utc(s) -> Optional[float]:
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


class RealCar:
    """One car of the real race, as Historic Roster put it on this grid (or left it out)."""

    def __init__(self, d: dict, racing=True):
        self.index = _int(d.get("car_index")) if racing else None
        self.driver = str(d.get("driver") or "").strip()
        self.team = (d.get("team") or "").strip() or None
        self.number = (str(d.get("number")).strip() or None) if d.get("number") is not None else None
        self.car = (d.get("car") or "").strip() or None
        self.cls = (d.get("class") or "").strip() or None
        self.stand_in_for = (d.get("stand_in_for") or "").strip() or None
        self.grid = _int(d.get("real_grid"))
        self.pos = _int(d.get("real_pos"))
        self.status = (d.get("real_status") or "").strip() or None
        self.laps = _int(d.get("real_laps"))
        self.racing = racing

    @property
    def finished(self) -> bool:
        return self.pos is not None and self.pos > 0

    def result_words(self) -> str:
        """'won', 'finished P4', 'retired (Engine)'."""
        if self.pos == 1:
            return "won"
        if self.finished:
            return f"finished P{self.pos}"
        why = self.status if self.status and fold(self.status) not in ("", "finished", "retired", "dnf") else None
        return f"retired ({why})" if why else "did not finish"

    def __repr__(self):
        return f"RealCar({self.index}, {self.driver!r}, grid {self.grid}, pos {self.pos})"


class RaceContext:
    def __init__(self, d: dict):
        ac, ev = d.get("ac") or {}, d.get("event") or {}
        self.track = str(ac.get("track") or "").strip()
        self.layout = str(ac.get("layout") or "").strip()
        self.race_laps = _int(ac.get("race_laps"))
        self.watch = bool(ac.get("watch"))
        self.name = str(ev.get("name") or "").strip()
        self.series = (ev.get("series") or "").strip() or None
        self.year = _int(ev.get("year"))
        self.date = None
        try:
            self.date = date.fromisoformat(str(ev.get("date"))[:10]) if ev.get("date") else None
        except ValueError:
            pass
        self.circuit = (ev.get("circuit") or "").strip() or None
        self.real_laps = _int(ev.get("real_laps"))
        self.multiclass = bool(ev.get("multiclass"))
        rc = d.get("real_conditions") or None
        self.conditions = ((rc.get("summary") or "").strip() or None) if isinstance(rc, dict) else None
        pl = d.get("player") or {}
        self.player_index = _int(pl.get("car_index"))
        self.seat_driver = (pl.get("seat_driver") or "").strip() or None
        self.grid = [RealCar(c) for c in d.get("grid") or [] if isinstance(c, dict) and c.get("driver")]
        self.not_racing = [RealCar(c, racing=False) for c in d.get("not_racing") or []
                           if isinstance(c, dict) and c.get("driver")]
        self._by_index = {c.index: c for c in self.grid if c.index is not None}
        self._by_name = {fold(c.driver): c for c in self.grid}

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, game, track, log=None, path=None, now=None, folder="", layout="") -> Optional["RaceContext"]:
        """The context for this session, or None (not AC, no file, stale, another track, unknown format).
        folder/layout: the AC track folder and layout as the game reports them (Frame.track_folder), the sure match;
        without them the track name said on air is compared instead."""
        if game != "AC" or not (track or folder):
            return None
        ctx = cls.read(path, log, now)
        if ctx is None or not ctx.matches(track, folder, layout):
            return None
        if log:
            log(f"Historic Roster race context: {ctx.name} ({len(ctx.grid)} cars)")
        return ctx

    @classmethod
    def read(cls, path=None, log=None, now=None) -> Optional["RaceContext"]:
        """The file as it stands, when it's fresh and a format SimCast knows, whatever the track (the start
        screen shows the race Historic Roster has set up before the sim is running)."""
        path = path or context_path()
        try:
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8-sig") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as e:
            if log:
                log(f"Historic Roster race context unreadable: {e}")
            return None
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            if log:
                log(f"Historic Roster race context: unknown schema {d.get('schema') if isinstance(d, dict) else '?'}")
            return None
        written = _parse_utc(d.get("written_utc"))
        age = (now if now is not None else time.time()) - written if written is not None else None
        if age is None or age > MAX_AGE_S or age < -600:
            return None                                   # an old race (or a clock far off): not this one
        return cls(d)

    def matches(self, track, folder="", layout="") -> bool:
        """Is this the session Historic Roster set up? By the AC track folder and layout when SimCast has them (both
        sides then hold exactly what race.ini says). Otherwise by the track as said on air, which is the track's own
        name ('Autodromo Nazionale Monza', since SimCast 1.15) or, before that, the folder, with ' (layout)' added
        when the layout isn't in the name already."""
        mine, my_layout = self.track.lower(), self.layout.lower()
        if not mine:
            return False
        if folder:
            # Historic Roster always writes the layout of a multi-layout track: no layout there means none here.
            return folder.strip().lower() == mine and (layout or "").strip().lower() == my_layout
        t = str(track or "").strip().lower()
        base, paren, rest = t.partition(" (")
        shown = rest[:-1] if paren and rest.endswith(")") else ""
        if base == mine:
            return shown == my_layout or (not shown and bool(my_layout) and my_layout in mine)
        # The track's own name: the real circuit's name Historic Roster knows, or the folder said as words.
        # The layout can't be told apart reliably by name, so a name match takes the track alone.
        names = {fold(self.circuit), fold(mine), fold(re.sub(r"^[a-z0-9]{2,6}_", "", mine))} - {""}
        return fold(base) in names

    # ------------------------------------------------------------------ cars
    def entry(self, car) -> Optional[RealCar]:
        """The real car behind a race.Car: by AC index (the player's seat by index alone), else by name."""
        if car is None:
            return None
        cid = car.id if isinstance(car.id, int) else _int(car.id)
        rc = self._by_index.get(cid) if cid is not None else None
        if rc is not None and (fold(rc.driver) == fold(car.name) or (car.player and cid == self.player_index)):
            return rc
        return self._by_name.get(fold(car.name))

    def player_seat(self) -> Optional[RealCar]:
        return self._by_index.get(self.player_index) if self.player_index is not None else None

    def by_real_pos(self, include_absent=True) -> list:
        cars = self.grid + (self.not_racing if include_absent else [])
        return sorted((c for c in cars if c.finished), key=lambda c: c.pos)

    def winner(self) -> Optional[RealCar]:
        order = self.by_real_pos()
        return order[0] if order and order[0].pos == 1 else None

    def podium(self) -> list:
        return [c for c in self.by_real_pos() if c.pos <= 3]

    def pole(self) -> Optional[RealCar]:
        return next((c for c in self.grid + self.not_racing if c.grid == 1), None)

    def retirements(self) -> list:
        return [c for c in self.grid + self.not_racing if not c.finished]

    # ------------------------------------------------------------------ words
    def date_words(self) -> str:
        if not self.date:
            return str(self.year) if self.year else ""
        return f"{self.date.day} {self.date.strftime('%B')} {self.date.year}"

    def event_line(self) -> str:
        bits = [self.name or "the real race"]
        if self.circuit:
            bits.append(self.circuit)
        if self.date_words():
            bits.append(self.date_words())
        if self.real_laps:
            bits.append(f"{self.real_laps} laps")
        return ", ".join(bits)


def spoilers_allowed(cfg, finished) -> bool:
    """May the booth talk about the real result now? 'after' (default): once this race is over."""
    mode = (cfg or {}).get("history_spoilers", "after")
    return mode == "always" or (mode == "after" and bool(finished))


__all__ = ["RaceContext", "RealCar", "context_path", "fold", "spoilers_allowed", "SPOILERS"]
