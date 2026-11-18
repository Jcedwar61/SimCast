"""The post-race recap: the result, YouTube chapters, the highlights and the whole transcript, with timestamps.

Written when a race ends to Recaps\\<date> <time> <track>.md next to the settings. Times count from lights out.
A stream or recording that started earlier needs that offset added: the recap says where the race started on
the clock, and Settings > Stream can add a fixed offset to every timestamp.
"""
from __future__ import annotations

import os
import re
import time

CHAPTER_KINDS = {"start": "Lights out", "final_lap": "Final lap", "winner": "The finish"}
HIGHLIGHT_MIN = 70          # events at least this big are highlights
CHAPTER_GAP = 30.0          # YouTube wants chapters at least 10 s apart; a race reads better with fewer


def stamp(s):
    """Seconds -> 'm:ss' or 'h:mm:ss', the way YouTube reads chapters."""
    s = max(0, int(round(s)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class Recap:
    def __init__(self, folder, log=print):
        self.folder, self.log = folder, log
        self.reset()

    def reset(self):
        self.lines = []              # (t, name, text)
        self.events = []             # (t, kind, priority, text)
        self.start_t = None          # the race clock's zero: lights out
        self.start_wall = None
        self.first_t = None

    def line(self, t, name, text):
        if self.first_t is None:
            self.first_t = t
        self.lines.append((t, name, text))

    def event(self, t, e, text):
        if self.first_t is None:
            self.first_t = t
        if e.kind == "start" and self.start_t is None:
            self.start_t, self.start_wall = t, time.time()
        self.events.append((t, e.kind, e.priority, text, dict(e.data or {})))

    def _rel(self, t, offset=0.0):
        zero = self.start_t if self.start_t is not None else (self.first_t or 0.0)
        return t - zero + offset

    def chapters(self, offset=0.0):
        """[(seconds, title)]: lights out, then the big moments, the final lap and the finish."""
        out = [(0.0, "Lights out" if self.start_t is not None else "On air")]
        for t, kind, prio, text, data in self.events:
            rel = self._rel(t)
            if rel <= 0:
                continue
            if kind in CHAPTER_KINDS and kind != "start":
                title = CHAPTER_KINDS[kind]
            elif kind == "overtake" and data.get("lead"):
                title = text[:1].upper() + text[1:]
            elif kind in ("contact", "stopped", "retired", "penalty") and prio >= 70:
                title = text[:1].upper() + text[1:]
            elif kind == "pit_in" and prio >= 70:
                title = text[:1].upper() + text[1:]
            else:
                continue
            if rel - out[-1][0] < CHAPTER_GAP:
                if kind in CHAPTER_KINDS:
                    out[-1] = (out[-1][0], title)          # the finish wins over whatever came just before
                continue
            out.append((rel, title))
        return [(t + offset if i else 0.0, title) for i, (t, title) in enumerate(out)]

    def highlights(self, offset=0.0):
        return [(self._rel(t, offset), text) for t, kind, prio, text, _ in self.events
                if prio >= HIGHLIGHT_MIN and kind not in ("intro", "start_review", "podium") and self._rel(t) > -5]

    def write(self, tr, writer, offset=0.0, demo=False, cost=""):
        """The recap file for the race just run. Returns its path, or None if there's nothing to write."""
        if len(self.lines) < 3:
            return None
        event = " ".join(str(getattr(writer, "cfg", {}).get("race_name") or "").split())
        track = (tr.track or "race").strip()
        title = f"{event} at {track}" if event and tr.track else (event or track)
        when = time.strftime("%Y-%m-%d %H%M")
        safe = re.sub(r'[\\\\/:*?"<>|]+', " ", event or track).strip() or "race"
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, f"{when} {safe}{' (demo)' if demo else ''}.md")
        out = [f"# {title}{' (demo race)' if demo else ''}, {time.strftime('%d %B %Y')}", ""]
        st = tr.standings() if hasattr(tr, "standings") else []
        if st:
            out += ["## Result", "", "| Pos | Driver | Grid | Best lap |", "|---|---|---|---|"]
            for s in st:
                c = tr.car(s["id"])
                best = _laptime(c.best) if c is not None and c.best else "-"
                status = f" ({s['status']})" if s.get("status") in ("dnf", "dsq") else ""
                out.append(f"| {s['pos']} | {s['name']}{status} | {s.get('grid') or '-'} | {best} |")
            out.append("")
        lead = ("" if self.start_wall is None else
                f"Lights out was at {time.strftime('%H:%M:%S', time.localtime(self.start_wall))}. ")
        out += ["## YouTube chapters", "",
                f"{lead}Times count from lights out" + (f", plus {stamp(offset)} for the stream's start" if offset
                                                        else "") + ". Paste these into the video description:", "",
                "```"]
        out += [f"{stamp(t)} {title}" for t, title in self.chapters(offset)]
        out += ["```", "", "## Highlights", ""]
        out += [f"- {stamp(t)} {text[:1].upper() + text[1:]}" for t, text in self.highlights(offset)] or ["- (none)"]
        if cost:
            out += ["", "## AI and voice cost (estimate)", "", cost]
        out += ["", "## Transcript", ""]
        out += [f"[{stamp(self._rel(t, offset))}] **{name}:** {text}" for t, name, text in self.lines]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + "\n")
        except OSError as e:
            self.log(f"Couldn't write the recap: {e}")
            return None
        self.log(f"Recap written: {path}")
        return path


def _laptime(x):
    m, s = divmod(x, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"
