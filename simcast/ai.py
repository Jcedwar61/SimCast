"""The AI half of the booth: colour, analysis and conversation written from the live race data.

The instant lines (writer.py) call what just happened; this adds what a real booth adds on top: why it
happened, what it means, the stories through the field, and the back-and-forth between two people. It runs
on SimCoach's AI providers (OpenAI, Gemini or DeepSeek, with fallback) in a background thread, and a reply
that arrives too late for the moment it was about is thrown away rather than spoken out of date.
"""
from __future__ import annotations

import threading
import time

from .writer import ANALYST, LEAD, PIT, Line, lap_time_words, place

MIN_INTERVAL = 5.0          # seconds between requests
TIMEOUT = 8.0
MAX_LINE_CHARS = 240

SYSTEM = """You are the commentary team on a live television broadcast of a motor race (a sim race, but you \
commentate it exactly like real TV). Voices:
- LEAD: {lead}, the play-by-play commentator. Calls the action, keeps the story moving.
- ANALYST: {analyst}, a former racing driver. Explains why things happen and what to watch for.
{pit_line}
Style: British TV motorsport commentary. Natural, warm, spoken English, short sentences, some energy. This is a \
two-man booth, not two monologues: the voices bounce off each other. Things that make it sound like a real booth: \
- Answer what the other one just said. Pick up their exact point (a driver, a gap, a place) and add to it.
- Ask each other the obvious question ("can they hold that?"), hand a thought over, correct a detail, disagree \
  mildly, finish each other's idea. Use the other's first name now and then, never every line.
- The lead opens a thought; the analyst answers it; then they trade the next line back.
- Never both say the same thing in different words. No "yes, exactly" every time.

Hard rules:
- Only state facts that are in the RACE DATA. Never invent contact, damage, penalties, team radio, weather, tyre \
compounds, strategy calls, lap times, gaps or history. If you don't know, talk about what the data does show \
(gaps, lap times, positions, who has pitted, who is closing).
- Call drivers by the name given in square brackets.
- Never use he, she, his or her for a driver: say their name or "they".
- No lists, no emojis, no stage directions, no sound effects, no quotation marks around lines.
- Each line at most 30 words. Don't repeat anything in RECENT COMMENTARY.
- {player} is the viewer's driver. Give them fair airtime, but this is a broadcast of the whole race.

Reply with JSON only: {{"lines": [{{"who": "lead" or "analyst"{pit_who}, "text": "...", "energy": 0-3}}]}}
energy: 0 calm, 1 engaged, 2 excited, 3 huge moment."""


def _t(x):
    if x is None:
        return "-"
    m, s = divmod(x, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def race_brief(tr, writer, events, transcript, max_cars=14):
    """The race as text for the model: session, standings with gaps and lap times, battles, recent events and
    what the booth has just said."""
    lines = []
    ld = tr.leader()
    if tr.session == "race":
        lap = f"lap {min(ld.laps + 1, tr.race_laps) if tr.race_laps else ld.laps + 1}" if ld else ""
        of = f" of {tr.race_laps}" if tr.race_laps else (f", {int(tr.time_left // 60)} min left" if tr.time_left else "")
        state = "finished" if tr.finished else ("racing" if tr.started else "on the grid, before the start")
        lines.append(f"SESSION: Race at {tr.track}, {lap}{of}, {state}. Flag: {tr.flag or 'green'}.")
    else:
        lines.append(f"SESSION: {tr.session} at {tr.track}. Order is by best lap.")
    st = tr.standings()
    me = next((s for s in st if s["player"]), None)
    keep = set(range(min(max_cars, len(st))))
    if me:
        i = st.index(me)
        keep |= {k for k in range(i - 2, i + 3) if 0 <= k < len(st)}
    lines.append("STANDINGS (pos, name [call name], interval to car ahead, gap to leader, last lap, best lap, "
                 "pit stops, notes):")
    for k, s in enumerate(st):
        if k not in keep:
            continue
        name = s["name"] if not s["player"] or not (writer.cfg.get("driver_name") or "").strip() else writer.cfg["driver_name"]
        call = writer.n(s["id"])
        iv = "leader" if k == 0 else (f"+{s['interval']:.1f}s" if s["interval"] is not None else
                                      (f"+{s['laps_down']} lap(s)" if s["laps_down"] else "-"))
        gap = "" if k == 0 else (f"{s['gap']:.1f}s" if s["gap"] is not None else "-")
        notes = []
        if s["player"]:
            notes.append("VIEWER'S DRIVER")
        if s["in_pit"]:
            notes.append("in the pits")
        if s["status"] in ("stopped", "dnf", "dsq", "finished"):
            notes.append(s["status"])
        if s["battle"]:
            notes.append("in a battle")
        if s["fastest"]:
            notes.append("fastest lap")
        if s["grid"]:
            notes.append(f"started P{s['grid']}")
        lines.append(f"{s['pos']}. {name} [{call}] {iv} {gap} last {_t(s['last'])} best {_t(s['best'])} "
                     f"stops {s['pits']} {', '.join(notes)}".rstrip())
    if len(st) > len(keep):
        lines.append(f"(... {len(st) - len(keep)} more cars)")
    if tr.fastest:
        lines.append(f"FASTEST LAP: {writer.n(tr.fastest[1])}, {_t(tr.fastest[0])} on lap {tr.fastest[2]}.")
    bats = []
    for (a, b), bt in tr.battles.items():
        ca, cb = tr.car(a), tr.car(b)
        if ca and cb and bt.get("told") and cb.interval is not None:
            bats.append(f"{writer.n(b)} chasing {writer.n(a)} for P{tr.pos_of(a)}, {cb.interval:.1f}s, for "
                        f"{int(bt['last_close'] - bt['since'])}s")
    if bats:
        lines.append("BATTLES: " + "; ".join(bats))
    if events:
        lines.append("RECENT EVENTS (oldest first): " + "; ".join(events[-8:]))
    if transcript:
        lines.append("RECENT COMMENTARY (last line is the most recent; the booth talks to itself, so pick up "
                     "whatever the other one has just said and answer it):")
        lines += [f"{writer.lead_name if w == LEAD else writer.analyst_name if w == ANALYST else 'pit'}: {t}"
                  for w, t in transcript[-8:]]
    return "\n".join(lines)


def describe(e, writer):
    """An Event as one plain sentence for the model (and the recent-events list)."""
    n = writer.n
    d = e.data
    c = e.cars
    k = e.kind
    if k == "overtake":
        return f"{n(c[0])} passed {', '.join(n(x) for x in c[1:])} for P{d.get('pos')}" + (" (THE LEAD)" if d.get("lead") else "")
    if k == "battle":
        return f"{n(c[0])} is within {d.get('gap', 0):.1f}s of {n(c[1])} for P{d.get('pos')}"
    if k == "closing":
        return f"{n(c[0])} is closing on {n(c[1])} by {d['per_lap']:.1f}s a lap, gap {d['gap']:.1f}s"
    if k == "fastest_lap":
        return f"{n(c[0])} set the fastest lap of the race, {_t(d.get('time'))}"
    if k == "pit_in":
        return f"{n(c[0])} pitted from P{d.get('pos')}"
    if k == "pit_out":
        return f"{n(c[0])} rejoined from the pits in P{d.get('pos')} (was P{d.get('was')})"
    if k == "stopped":
        return f"{n(c[0])} stopped on track while running P{d.get('pos')}"
    if k == "moment":
        return f"{n(c[0])} lost {d.get('loss', 0):.1f}s in a few corners (a spin or a trip off, cause unknown)"
    if k == "retired":
        return f"{n(c[0])} retired from P{d.get('pos')}"
    if k == "winner":
        return f"{n(c[0])} won the race"
    if k == "player_finish":
        return f"{n(c[0])} (viewer's driver) finished P{d.get('pos')} from P{d.get('grid')} on the grid"
    if k == "contact":
        return f"{n(c[0])} and {n(c[1])} made contact"
    if k == "start":
        return "the race started"
    if k == "final_lap":
        return "the leader started the final lap"
    if k == "quali_lap":
        return f"{n(c[0])} set a {_t(d.get('time'))}, now P{d.get('pos')}"
    if k == "caution":
        return f"caution: {d.get('kind')}"
    if k == "green":
        return "green flag, racing resumed"
    return k.replace("_", " ") + (f": {', '.join(n(x) for x in c)}" if c else "")


def parse_lines(raw, json_reply, pit=True, opener=LEAD):
    d = json_reply(raw) if raw else None
    if not d or not isinstance(d.get("lines"), list):
        return []
    out = []
    for item in d["lines"][:5]:
        if not isinstance(item, dict):
            continue
        who = str(item.get("who", "")).lower().strip()
        who = {"lead": LEAD, "analyst": ANALYST, "pit": PIT, "reporter": PIT}.get(who)
        if who is None or (who == PIT and not pit):
            who = ANALYST if who is None else LEAD
        text = " ".join(str(item.get("text", "")).split()).strip().strip('"')
        if not text or len(text) > MAX_LINE_CHARS:
            continue
        try:
            energy = max(0, min(3, int(item.get("energy", 1))))
        except (TypeError, ValueError):
            energy = 1
        out.append(Line(who, text, energy))
    _alternate(out, opener)
    return out


def _alternate(lines, opener=LEAD):
    """Two voices hand over to each other: the booth's talkers are put in strict alternation from the opener, so
    the booth answers itself instead of delivering two statements in a row. Pit-lane lines keep their place, and a
    lone line keeps the voice that wrote it."""
    other = ANALYST if opener == LEAD else LEAD
    talkers = [ln for ln in lines if ln.who != PIT]
    if len(talkers) < 2:
        return                                            # a lone line keeps the voice that wrote it
    for k, ln in enumerate(talkers):
        ln.who = opener if k % 2 == 0 else other


class AIBooth:
    """One request at a time, in a background thread. request() returns False if busy, rate limited or off;
    the reply is handed to on_lines(lines, request_info) from the worker thread."""

    def __init__(self, cfg_fn, chat_fn, json_reply, label_fn=None, log=print, clock=time.monotonic):
        self.cfg_fn, self.chat_fn, self.json_reply = cfg_fn, chat_fn, json_reply
        self.label_fn = label_fn
        self.log = log
        self.clock = clock
        self.busy = False
        self.last_req = -1e9
        self.failures = 0
        self.disabled_until = 0.0
        self.last_error = ""
        self.sync = False                     # tests: run in the calling thread

    def available(self):
        cfg = self.cfg_fn()
        if not cfg.get("ai_colour", True):
            return False
        from .providers import AI_PROVIDERS, ai_key
        return any(ai_key(cfg, p) for p in AI_PROVIDERS)

    def label(self):
        if not self.available():
            return ""
        try:
            return self.label_fn(self.cfg_fn()) if self.label_fn else "AI"
        except Exception:
            return "AI"

    def ready(self, now=None):
        now = self.clock() if now is None else now
        return (not self.busy and now - self.last_req >= MIN_INTERVAL and now >= self.disabled_until
                and self.available())

    def request(self, task, brief, on_lines, info=None, max_tokens=350, opener=LEAD):
        if not self.ready():
            return False
        self.busy, self.last_req = True, self.clock()
        cfg = self.cfg_fn()
        pit = cfg.get("pit_reporter", True)
        me = (cfg.get("driver_name") or "").strip() or "The driver marked VIEWER'S DRIVER"
        system = SYSTEM.format(
            lead=cfg.get("lead_name") or "Ben", analyst=cfg.get("analyst_name") or "Kate", player=me,
            pit_line=(f"- PIT: {cfg.get('pit_name') or 'Mia'}, the pit-lane reporter, only for pit stops.\n"
                      if pit else ""),
            pit_who=' or "pit"' if pit else "")
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": f"RACE DATA\n{brief}\n\nTASK\n{task}"}]

        def work():
            t0 = self.clock()
            lines = []
            try:
                raw = self.chat_fn(cfg, msgs, max_tokens=max_tokens, timeout=TIMEOUT)
                lines = parse_lines(raw, self.json_reply, pit, opener)
                self.failures = 0
                self.last_error = ""
            except Exception as e:
                self.failures += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self.log(f"AI booth: {self.last_error}")
                if self.failures >= 3:
                    self.disabled_until = self.clock() + 120.0       # stop hammering a broken key or network
            finally:
                self.busy = False
            if lines:
                try:
                    on_lines(lines, dict(info or {}, took=self.clock() - t0))
                except Exception as e:
                    self.log(f"AI booth: {e}")

        if self.sync:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="ai-booth").start()
        return True


# Task wording for each kind of request -------------------------------------------------------------------

def react_task(event_text, said, speaker="Ben", other="Kate"):
    """The other voice answers what was just said on air, and the first one comes back to that: a short exchange,
    not a remark. Returns (task, who opens the reply)."""
    return (f"This just happened: {event_text}. {speaker} has just said on air: \"{said}\". "
            f"Carry the conversation on from there: {other} answers that line directly, using the data "
            f"(what it means, who gains, who has it all to do now), then {speaker} comes back to the answer in a few "
            f"words. Two lines, {other} first, then {speaker}. Don't repeat the line itself.")


def filler_task(writer, now, focus):
    tr = writer.tr
    quiet = sorted((c for c in tr.cars.values() if not c.gone), key=lambda c: c.mentioned)[:4]
    names = ", ".join(writer.n(c.id) for c in quiet)
    me = tr.player()
    focus_txt = {"me": f"Focus on {writer.n(me.id)}'s race." if me else "",
                 "field": "Pick a story from anywhere in the field."}.get(focus, "Pick an interesting story from the race.")
    lead = writer.lead_name
    analyst = writer.analyst_name
    seen = getattr(writer, "thread_hint", lambda: "")()
    carry = f" You are already on this, so carry it on rather than starting elsewhere: {seen}" if seen else ""
    return (f"It's a quiet moment in the race. Fill it with a short, natural back-and-forth between {lead} (LEAD) "
            f"and {analyst} (ANALYST), 3 lines, strictly alternating: {lead} raises a story, {analyst} answers it, "
            f"{lead} comes back to that answer.{carry} {focus_txt} Good topics: a close battle, who is quickest "
            "right now, gaps growing or shrinking, pit stops made and still to come, someone who has gained or lost "
            f"a lot since the start. Drivers who haven't had a mention for a while: {names}.")


def wrap_task(lead_name="Ben", analyst_name="Kate"):
    return (f"The race is over. Give a closing exchange (4 lines, alternating) between {lead_name} (LEAD) and "
            f"{analyst_name} (ANALYST): the winner, the podium, the viewer's driver's result compared to their grid "
            "slot, and one standout moment from RECENT EVENTS. Then sign off to each other and to the viewers.")


def intro_task(lead_name="Ben", analyst_name="Kate"):
    return (f"We're coming on air. Give the opening (3 lines, alternating): {lead_name} (LEAD) welcomes viewers to "
            f"the circuit and sets the scene, {analyst_name} (ANALYST) answers with what to watch, including the "
            f"viewer's driver, and {lead_name} hands over to the start.")


__all__ = ["AIBooth", "race_brief", "describe", "parse_lines", "react_task", "filler_task", "wrap_task",
           "intro_task", "lap_time_words", "place"]
