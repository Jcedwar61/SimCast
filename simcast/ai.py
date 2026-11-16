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

SYSTEM = """You are the commentary team on a live television broadcast of a motor race. It's a sim race, but \
you call it exactly like a real broadcast: think of an IMSA, GT World Challenge or WEC booth. The voices:
- LEAD: {lead}, play-by-play. Calls the action, runs the broadcast, asks the questions, throws to the pit lane.
- ANALYST: {analyst}, a former racing driver. Explains why things happen, has opinions and stands by them.
{pit_line}
These are colleagues who have done a hundred races together, talking to each other with the race in front of \
them. It must sound like a conversation, not lines read out in turn. What makes a real booth sound real:
- Answer what the other one just said. Every line picks up the line before it (a name, a gap, a claim) and moves \
it on. Never two separate remarks in a row.
- Every line adds something new from the RACE DATA: a lap-time comparison between two cars, whether a gap is \
growing or shrinking, grid slot against position now, laps left, who has and hasn't stopped, a stop time. A line \
that only agrees ("absolutely", "exactly", "great racing") adds nothing: don't write it.
- Have opinions and disagree. Make calls: who wins this fight, whether a stop worked, whether staying out pays \
off. The other one pushes back with a reason from the data. Concede gracefully or hold your ground. Warm, a bit \
of teasing, never rude.
- Remember. CALLS ON RECORD lists predictions made on air. When the race settles one, say so: "you called that", \
"so much for my prediction", "I'm only going to mention it once". If a call is still open, someone can needle \
the other about it. Come back to things said earlier in RECENT COMMENTARY instead of repeating them.
- Ask real questions and answer them with something specific: "Is Okafor quicker or just close?" "Quicker. \
Three tenths on that last lap."
- Mix the rhythm: some lines are three words ("Go on." "Look at the gap now." "Here it comes."), some are a full \
thought. Cut in or finish the other's sentence now and then. Use first names when you address each other, \
not every line.
- The lead keeps it moving: when a story is told, move on with a handover ("Meanwhile, further back...").
Never say: "good, clean, hard racing", "lovely racing", "this is worth watching", "it's all happening", \
"what a race this has become", "absolutely", "exactly", "make no mistake", "at the end of the day".

Hard rules:
- Only state facts that are in the RACE DATA. Never invent contact, damage, penalties, team radio, weather, tyre \
compounds, fuel numbers, what a team has decided, lap times, gaps or history. Opinions and predictions are \
welcome; invented facts are not. If you don't know, talk about what the data does show.
- Call drivers by the name given in square brackets.
- Never use he, she, his or her for a driver: say their name or "they".
- No lists, no emojis, no stage directions, no sound effects, no quotation marks around lines.
- Each line at most 30 words, most well under 20. Don't repeat anything in RECENT COMMENTARY.
- {player} is the viewer's driver. Give them fair airtime, but this is a broadcast of the whole race.

Reply with JSON only: {{"lines": [{{"who": "lead" or "analyst"{pit_who}, "text": "...", "energy": 0-3}}]}}
energy: 0 calm, 1 engaged, 2 excited, 3 huge moment."""

PIT_LINE = ("- PIT: {pit}, the pit-lane reporter. Reports stops, stop times against the others and who is still "
            "to stop. Part of the conversation: the lead throws down ('{pit}, what are you seeing?'), {pit} answers, "
            "can ask the booth back or disagree about whether a stop worked, and hands back ('Back to you, {lead}'). "
            "Bring {pit} in when the pits or the stops are the story, not in every exchange.\n")


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
    stops = [(t, cid) for cid, t in getattr(writer, "lane_times", {}).items() if tr.car(cid)]
    if stops:
        stops.sort()
        lines.append("PIT STOPS (time in the pit lane, quickest first): " +
                     ", ".join(f"{writer.n(cid)} {t:.1f}s" for t, cid in stops[:8]))
    calls = []
    for pair, rec in getattr(writer, "calls", {}).items():
        for who in (LEAD, ANALYST):
            if who in rec:
                other = next((c for c in pair if c != rec[who]), None)
                nm = writer.lead_name if who == LEAD else writer.analyst_name
                calls.append(f"{nm} said {writer.n(rec[who])} would come out on top against "
                             f"{writer.n(other) if other is not None else 'the other car'}")
    if calls:
        lines.append("CALLS ON RECORD (not settled yet): " + "; ".join(calls[:6]))
    if events:
        lines.append("RECENT EVENTS (oldest first): " + "; ".join(events[-8:]))
    if transcript:
        lines.append("RECENT COMMENTARY (last line is the most recent; the booth talks to itself, so pick up "
                     "whatever the other one has just said and answer it):")
        lines += [f"{writer.lead_name if w == LEAD else writer.analyst_name if w == ANALYST else writer.pit_name}: {t}"
                  for w, t in transcript[-12:]]
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
            pit_line=(PIT_LINE.format(pit=(cfg.get("pit_name") or "Mia").split()[0],
                                      lead=(cfg.get("lead_name") or "Ben").split()[0]) if pit else ""),
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
            f"Carry the conversation on from there: {other} answers that line directly with something new from the "
            f"data (what it means, who gains, who has it all to do now, or why it happened), and may see it "
            f"differently from {speaker}. Then {speaker} comes back to that answer in a few words. If this settles a "
            f"call on record, say who called it. Two lines, {other} first, then {speaker}. Don't repeat the line itself.")


FILLER_SHAPES = ("story", "question", "debate", "callback", "pit", "aside")


def filler_task(writer, now, focus, shape=None):
    """A quiet spell, as a conversation with a shape real booths use: a story worked between two voices, a real
    question answered with a number, a call that the other disputes, a callback to something said earlier, or a
    throw down to the pit lane. Returns (task, who opens it)."""
    tr = writer.tr
    quiet = sorted((c for c in tr.cars.values() if not c.gone), key=lambda c: c.mentioned)[:4]
    names = ", ".join(writer.n(c.id) for c in quiet)
    me = tr.player()
    focus_txt = {"me": f"Focus on {writer.n(me.id)}'s race." if me else "",
                 "field": "Pick a story from anywhere in the field."}.get(focus, "Pick an interesting story from the race.")
    lead = writer.lead_name
    analyst = writer.analyst_name
    pit = writer.pit_name
    seen = getattr(writer, "thread_hint", lambda: "")()
    carry = f" You are already on this, so carry it on rather than starting elsewhere: {seen}" if seen else ""
    has_pit = writer.cfg.get("pit_reporter", True) and any(c.pits or c.in_pit for c in tr.cars.values())
    rng = getattr(writer, "rng", None)
    if shape is None:
        pool = ["story", "question", "debate", "debate", "aside"]
        if getattr(writer, "calls", None) or len(getattr(writer, "_said", ())) > 20:
            pool.append("callback")
        if has_pit:
            pool += ["pit", "pit"]
        shape = rng.choice(pool) if rng else "story"
    if shape == "pit" and not has_pit:
        shape = "question"
    opener = LEAD
    if shape == "question":
        how = (f"{lead} (LEAD) asks {analyst} (ANALYST) a real question about the race, something a viewer would "
               f"wonder; {analyst} answers it with a specific number from the data and an opinion; {lead} picks up the "
               "answer and takes it somewhere (agrees with a twist, or doubts it). 3 lines.")
    elif shape == "debate":
        opener = ANALYST
        how = (f"{analyst} (ANALYST) makes a call about how something will end (a fight, a charge, the pit cycle, "
               f"the win) and gives the reason from the data; {lead} (LEAD) disagrees with a different reason from "
               f"the data; {analyst} answers that, holding the call or conceding a little. 3 lines.")
    elif shape == "callback":
        how = (f"{lead} (LEAD) and {analyst} (ANALYST) come back to something said earlier on air (RECENT COMMENTARY or "
               "CALLS ON RECORD) and look at how it has turned out since, with the numbers. Light teasing is fine if "
               "someone got it wrong. 3 lines.")
    elif shape == "pit":
        how = (f"{lead} (LEAD) throws down to {pit} (PIT) in the pit lane; {pit} answers with the stops from the "
               f"data (stop times compared, who is still to stop and what that means); {analyst} (ANALYST) takes "
               f"that on with an opinion, or {pit} and {analyst} see it differently; {pit} may hand back to {lead}. "
               "3 or 4 lines.")
    elif shape == "aside":
        how = (f"A side conversation, the kind a booth drifts into between stories, but tied to what's on screen: "
               f"what it's like in the car in a fight like the one going on, the race so far in numbers (passes, "
               f"lead changes, biggest mover), who {analyst} would swap seats with and why, who wins this and a small "
               f"bet on it, or {analyst}'s own racing days (general feelings only, no invented results). {lead} (LEAD) "
               f"opens it, {analyst} (ANALYST) answers, and it comes back to a car on track by the last line. Light "
               f"and human: a bit of teasing is welcome. 3 or 4 lines.")
    else:
        how = (f"{lead} (LEAD) raises a story, {analyst} (ANALYST) answers it with something the lead didn't say, "
               f"{lead} comes back to that answer. 3 lines.{carry}")
    return (f"It's a quiet moment in the race. Fill it with a short, natural conversation. {how} {focus_txt} "
            "Good material: a close battle, who is quickest right now and by how much, gaps growing or shrinking, "
            "stops made and still to come, someone who has gained or lost a lot since the start. Drivers who "
            f"haven't had a mention for a while: {names}."), opener


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
