"""The booth's memory across races: results, rivalries and who called what, kept between sessions.

A league's best commentary is history: "these two made contact at Spa last time out", "three wins in a row",
"Kate's predictions are two from seven this season". Each real race (not a demo) is written to memory.json next
to the settings when it ends: the results, the incidents between drivers (contact, passes for the lead,
retirements) and the commentators' calls that the racing settled. The AI booth gets the history of the drivers
on track in its race data; the instant lines bring up a shared past when two drivers with one meet again.
Drivers are matched by name, so it works best in a league where names don't change.
"""
from __future__ import annotations

import json
import os
import threading
import time

MAX_RACES = 60
MAX_INCIDENTS = 400


def key(name):
    return " ".join(str(name or "").split()).lower()


def ago(races_back):
    """How long ago, the way a commentator says it: 0 is the last race."""
    if races_back <= 0:
        return "last time out"
    if races_back == 1:
        return "the race before last"
    return f"{races_back + 1} races ago"


class Memory:
    def __init__(self, path, log=print):
        self.path, self.log = path, log
        self.lock = threading.Lock()
        self.data = {"races": [], "incidents": [], "calls": {}, "storylines": []}
        self.current = []                    # incidents in the race being run, written when it ends
        self.calls_now = []                  # calls settled in this race
        self._load()

    # ------------------------------------------------------------------ file
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                for k in ("races", "incidents"):
                    if isinstance(d.get(k), list):
                        self.data[k] = d[k]
                if isinstance(d.get("calls"), dict):
                    self.data["calls"] = d["calls"]
                if isinstance(d.get("storylines"), list):
                    self.data["storylines"] = [str(x) for x in d["storylines"] if isinstance(x, str)][:8]
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log(f"Memory unreadable ({e}); starting a fresh one.")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with self.lock:                      # one save at a time, each from its own temp file
                tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, indent=1)
                os.replace(tmp, self.path)
        except Exception as e:
            self.log(f"Couldn't save the booth's memory: {e}")

    def forget(self):
        with self.lock:
            self.data = {"races": [], "incidents": [], "calls": {}, "storylines": []}
            self.current, self.calls_now = [], []
        self.save()

    # ------------------------------------------------------------------ during a race
    def note(self, kind, names, track):
        """An incident worth remembering: 'contact', 'lead_pass' (a pass for the lead), 'retired', 'moment'."""
        names = [n for n in names if n]
        if not names:
            return
        self.current.append({"kind": kind, "names": names, "track": track or ""})

    def call_settled(self, commentator, right):
        self.calls_now.append((commentator, bool(right)))

    def end_race(self, tr, game=""):
        """The flag has fallen: write the result, the incidents and the calls."""
        st = tr.standings() if hasattr(tr, "standings") else []
        if len(st) < 2:
            self.current, self.calls_now = [], []
            return False
        results = []
        for s in st:
            c = tr.car(s["id"])
            results.append({"name": s["name"], "pos": s["pos"], "grid": s.get("grid"), "status": s.get("status"),
                            "player": bool(s.get("player")), "best": c.best if c is not None else None})
        fl = getattr(tr, "fastest", None)
        fastest = tr.car(fl[1]).name if fl and tr.car(fl[1]) else None
        race = {"date": time.strftime("%Y-%m-%d"), "track": tr.track or "", "game": game or getattr(tr, "game", "") or "",
                "results": results, "fastest": fastest}
        with self.lock:
            n = len(self.data["races"])
            self.data["races"].append(race)
            for inc in self.current:
                inc["race"] = n
                self.data["incidents"].append(inc)
            for who, right in self.calls_now:
                rec = self.data["calls"].setdefault(key(who), {"right": 0, "wrong": 0})
                rec["right" if right else "wrong"] += 1
            self._trim()
            self.current, self.calls_now = [], []
        self.save()
        return True

    def _trim(self):
        races = self.data["races"]
        if len(races) > MAX_RACES:
            drop = len(races) - MAX_RACES
            self.data["races"] = races[drop:]
            self.data["incidents"] = [dict(i, race=i["race"] - drop) for i in self.data["incidents"]
                                      if i.get("race", 0) >= drop]
        self.data["incidents"] = self.data["incidents"][-MAX_INCIDENTS:]

    # ------------------------------------------------------------------ what the booth remembers
    def _back(self, race_no):
        return len(self.data["races"]) - 1 - race_no

    def driver(self, name):
        """{'races', 'wins', 'podiums', 'last': (pos, track, races back), 'streak'} or None."""
        k = key(name)
        races = self.data["races"]
        mine = [(i, r, next((x for x in r["results"] if key(x["name"]) == k), None)) for i, r in enumerate(races)]
        mine = [(i, r, x) for i, r, x in mine if x]
        if not mine:
            return None
        wins = sum(1 for _, _, x in mine if x["pos"] == 1 and x.get("status") not in ("dnf", "dsq"))
        pods = sum(1 for _, _, x in mine if x["pos"] <= 3 and x.get("status") not in ("dnf", "dsq"))
        streak = 0
        for _, _, x in reversed(mine):
            if x["pos"] == 1:
                streak += 1
            else:
                break
        i, r, x = mine[-1]
        return {"races": len(mine), "wins": wins, "podiums": pods, "streak": streak,
                "last": (x["pos"], r["track"], self._back(i), x.get("status"))}

    def pair(self, a, b):
        """The most recent incident between two drivers: {'kind', 'track', 'back'} or None."""
        ka, kb = key(a), key(b)
        for inc in reversed(self.data["incidents"]):
            ks = {key(n) for n in inc["names"]}
            if ka in ks and kb in ks and len(ks) >= 2:
                return {"kind": inc["kind"], "track": inc["track"], "back": self._back(inc.get("race", 0))}
        return None

    def record(self, commentator):
        rec = self.data["calls"].get(key(commentator))
        return (rec["right"], rec["wrong"]) if rec else None

    # ------------------------------------------------------------------ season storylines (assist.py writes them)
    def season_facts(self, races=12):
        """The recent results and incidents as text for the storyline writer, or '' with fewer than two races."""
        with self.lock:
            rs = list(self.data["races"])[-races:]
            first = len(self.data["races"]) - len(rs)
            incs = [i for i in self.data["incidents"] if i.get("race", 0) >= first]
        if len(rs) < 2:
            return ""
        out = [f"RESULTS of the last {len(rs)} races, oldest first (pos. driver, grid in brackets, DNF if retired):"]
        for k, r in enumerate(rs):
            res = sorted(r.get("results") or [], key=lambda x: x.get("pos") or 99)[:15]
            bits = [f"{x['pos']}. {x['name']}" + (f" (grid {x['grid']})" if x.get("grid") else "") +
                    (" DNF" if x.get("status") in ("dnf", "dsq") else "") + (" [viewer]" if x.get("player") else "")
                    for x in res]
            back = len(rs) - 1 - k
            out.append(f"Race {k + 1} ({ago(back)}, {r.get('track') or 'unknown track'}): " + "; ".join(bits)
                       + (f". Fastest lap: {r['fastest']}" if r.get("fastest") else ""))
        if incs:
            what = {"contact": "contact", "lead_pass": "pass for the lead", "retired": "retired", "moment": "moment"}
            out.append("INCIDENTS: " + "; ".join(
                f"race {i.get('race', 0) - first + 1}: {what.get(i['kind'], i['kind'])} ({', '.join(i['names'])})"
                for i in incs[-40:]))
        return "\n".join(out)

    def set_storylines(self, stories):
        with self.lock:
            self.data["storylines"] = [str(s) for s in stories][:8]
        self.save()

    def storylines(self):
        with self.lock:
            return list(self.data.get("storylines") or [])

    @staticmethod
    def pair_words(p):
        what = {"contact": "made contact", "lead_pass": "swapped the lead", "moment": "had a moment together"}
        return f"{what.get(p['kind'], 'tangled')}{' at ' + p['track'] if p['track'] else ''} {ago(p['back'])}"

    def brief(self, names, commentators=()):
        """Lines for the AI's race data: the history of these drivers, between them, and the booth's own record."""
        if not self.data["races"]:
            return []
        out = []
        for nm in names:
            d = self.driver(nm)
            if not d:
                continue
            pos, track, back, status = d["last"]
            last = ("did not finish" if status in ("dnf", "dsq") else f"finished P{pos}") + \
                   (f" at {track}" if track else "") + f" {ago(back)}"
            bits = [f"{d['races']} race(s) remembered", f"{d['wins']} win(s)", f"{d['podiums']} podium(s)", last]
            if d["streak"] >= 2:
                bits.append(f"on a {d['streak']}-race winning streak")
            out.append(f"{nm}: " + ", ".join(bits))
        pairs = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                p = self.pair(a, b)
                if p:
                    pairs.append(f"{a} and {b} {self.pair_words(p)}")
        out += pairs[:8]
        for c in commentators:
            r = self.record(c)
            if r and sum(r):
                out.append(f"{c}'s predictions in earlier races: {r[0]} right, {r[1]} wrong")
        return [f"Season storyline: {s}" for s in self.storylines()[:4]] + out
