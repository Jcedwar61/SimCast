"""Real sponsors, looked up by the AI. SimCast never makes a sponsor up: the booth only names a sponsor that the
settings or the league roster give, or that the AI knows for a fact.

- The race: when Settings has a race name for a real race from any point in history ('1994 Indianapolis 500',
  '2026 Las Vegas Grand Prix', 'Coca-Cola 600'), the AI is asked for that edition's title sponsor.
- The drivers and teams: a sim field can be full of real names (an AI grid of real drivers, a league running real
  teams). Each one the AI recognises as a real driver or team gets their real backers; online players, gamer tags
  and made-up AI names get nothing.

The AI is told to answer only what it knows and to leave everything else empty, never to guess. Answers (empty
ones too) are kept in sponsors.json next to the settings, so each name is only asked about once. Lookups run in a
background thread; until an answer is in, the booth just has no sponsor for that car.
"""
from __future__ import annotations

import json
import os
import threading
import time

BATCH = 20                  # drivers per request
TIMEOUT = 25.0              # nothing is waiting on air for this: it can take its time
RETRY_AFTER = 120.0         # after a failed request (no key, network down)

RACE_TASK = """A sim racer has named the race they're broadcasting. Is it a real motor race?

Race name: {race}
Track in the sim: {track}

If it is a real race that has actually been held (at any point in history), give the title sponsor of that \
edition: the company in the race's official name, e.g. "Coca-Cola" for the Coca-Cola 600 or "Crypto.com" for a \
"Crypto.com Miami Grand Prix". Use the year in the name if there is one, otherwise the most recent edition. \
If the name itself already contains the sponsor, give that company.
If it isn't a real race, you aren't sure it is, or that edition had no title sponsor, give an empty string. \
Never guess and never invent a company: an empty answer is always better than a wrong one.

Reply with JSON only: {{"real": true or false, "title_sponsor": "..."}}"""

DRIVER_TASK = """These are the drivers in a sim race{context}. Sim fields are mostly online players, gamer tags \
and made-up AI names, but some are real people: a grid of real drivers, or a league running real teams.

{who}

For each one that is clearly a real professional racing driver, or whose team is clearly a real racing team, give \
their real sponsors: the main backers on that driver's car or that team's cars{when}, up to 3, by company name. \
Leave out anyone you don't recognise for certain, anyone whose sponsors you don't know, and anyone who only shares \
a name with a real driver (a common name with no matching team or series). Never guess and never invent a \
company: leaving someone out is always better than a wrong answer.

Reply with JSON only: {{"drivers": [{{"name": "<exactly as given>", "sponsors": ["...", "..."]}}]}} \
(an empty list when you know none of them)."""


def _norm(s) -> str:
    return " ".join(str(s or "").split()).lower()


def _clean_names(lst, n=3):
    out = []
    for s in lst if isinstance(lst, list) else []:
        s = " ".join(str(s or "").split())[:60]
        if s and s.lower() not in (x.lower() for x in out) and s.lower() not in ("none", "unknown", "n/a"):
            out.append(s)
    return out[:n]


class SponsorLookup:
    """Asks the AI once per race name and once per driver, and remembers the answer."""

    def __init__(self, cfg_fn, chat_fn, json_reply, path=None, log=print, clock=time.monotonic):
        self.cfg_fn, self.chat_fn, self.json_reply = cfg_fn, chat_fn, json_reply
        self.path, self.log, self.clock = path, log, clock
        self.races: dict = {}               # race key -> title sponsor ('' = looked up, none)
        self.drivers: dict = {}             # driver key -> [sponsors] ([] = looked up, none)
        self.want_race = None               # (key, race name, track)
        self.want: dict = {}                # driver key -> (name, team, car)
        self.lock = threading.Lock()
        self.busy = False
        self.retry_at = 0.0
        self.sync = False                   # tests: run in the calling thread
        self._load()

    # -------------------------------------------------------------- cache
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            self.races = {str(k): str(v) for k, v in (d.get("races") or {}).items()}
            self.drivers = {str(k): _clean_names(v) for k, v in (d.get("drivers") or {}).items()}
        except Exception as e:
            self.log(f"Sponsors: couldn't read {self.path}: {e}")

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"races": self.races, "drivers": self.drivers}, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            self.log(f"Sponsors: couldn't save {self.path}: {e}")

    def forget(self):
        with self.lock:
            self.races, self.drivers = {}, {}
        self._save()

    # -------------------------------------------------------------- asking
    def on(self):
        cfg = self.cfg_fn()
        if not cfg.get("lookup_sponsors", True) or not cfg.get("ai_colour", True):
            return False
        try:
            from .providers import AI_PROVIDERS, ai_key
            return any(ai_key(cfg, p) for p in AI_PROVIDERS)
        except Exception:
            return False

    def race(self, race_name, track="") -> str:
        """The real title sponsor of the race named in Settings, '' if it isn't a real race, has none, or the AI
        hasn't answered yet."""
        name = " ".join(str(race_name or "").split())
        if not name or not self.on():
            return ""
        key = _norm(name)
        with self.lock:
            if key in self.races:
                return self.races[key]
            self.want_race = (key, name, " ".join(str(track or "").split()))
        self._kick()
        return ""

    @staticmethod
    def driver_key(name, team, race_name):
        return "|".join((_norm(name), _norm(team), _norm(race_name)))

    def driver(self, name, team=None, car=None) -> list:
        """The real sponsors of a driver (or their team), [] if they aren't real, have none we know, or the AI
        hasn't answered yet."""
        name = " ".join(str(name or "").split())
        if not name or not self.on():
            return []
        key = self.driver_key(name, team, self.cfg_fn().get("race_name"))
        with self.lock:
            if key in self.drivers:
                return list(self.drivers[key])
            self.want.setdefault(key, (name, " ".join(str(team or "").split()), " ".join(str(car or "").split())))
        self._kick()
        return []

    def _kick(self):
        with self.lock:
            if self.busy or self.clock() < self.retry_at or not (self.want_race or self.want):
                return
            self.busy = True
        if self.sync:
            self._work()
        else:
            threading.Thread(target=self._work, daemon=True, name="sponsor-lookup").start()

    def _ask(self, task, max_tokens):
        cfg = dict(self.cfg_fn(), _purpose="convo")        # the strongest model the key has: this is about facts
        msgs = [{"role": "system", "content": "You are a motorsport research assistant. You answer only with facts "
                                              "you are sure of, and reply with JSON only."},
                {"role": "user", "content": task}]
        d = self.json_reply(self.chat_fn(cfg, msgs, max_tokens=max_tokens, timeout=TIMEOUT))
        if not isinstance(d, dict):
            raise ValueError("the reply wasn't JSON")             # asked again later, not remembered as 'none'
        return d

    def _work(self):
        try:
            while True:
                with self.lock:
                    race, self.want_race = self.want_race, None
                    batch = list(self.want.items())[:BATCH]
                if race:
                    key, name, track = race
                    d = self._ask(RACE_TASK.format(race=name, track=track or "not given"), 200)
                    t = " ".join(str(d.get("title_sponsor") or "").split())[:60] if d.get("real") else ""
                    with self.lock:
                        self.races[key] = t
                    self.log(f"Sponsors: {name}: " + (f"title sponsor {t}" if t else "no real title sponsor found"))
                elif batch:
                    self._drivers(batch)
                else:
                    break
                self._save()
        except Exception as e:
            self.retry_at = self.clock() + RETRY_AFTER
            self.log(f"Sponsors: couldn't look them up ({type(e).__name__}: {str(e)[:120]})")
        finally:
            with self.lock:
                self.busy = False

    def _drivers(self, batch):
        race_name = " ".join(str(self.cfg_fn().get("race_name") or "").split())
        who = "\n".join(f"- {name}" + (f" (team: {team})" if team else "") + (f" (car: {car})" if car else "")
                        for _, (name, team, car) in batch)
        d = self._ask(DRIVER_TASK.format(who=who, context=f", the {race_name}" if race_name else "",
                                         when=f" at the time of the {race_name}" if race_name else ", most recently"),
                      120 + 40 * len(batch))
        found = {}
        for item in d.get("drivers") if isinstance(d.get("drivers"), list) else []:
            if isinstance(item, dict):
                found[_norm(item.get("name"))] = _clean_names(item.get("sponsors"))
        with self.lock:
            for key, (name, _, _) in batch:
                self.drivers[key] = found.get(_norm(name), [])
                self.want.pop(key, None)
        got = sum(1 for k, _ in batch if self.drivers.get(k))
        self.log(f"Sponsors: looked up {len(batch)} drivers, {got} with real sponsors")
