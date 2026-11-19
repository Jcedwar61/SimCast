"""The director: decides what goes on air and when, like the producer in a TV booth's ear.

Every tick it looks at the moments the race tracker found and what the booth is doing. The biggest fresh
story goes next; a stale one (an overtake from twenty seconds ago) is dropped; a huge one (a new leader, a car
stopped) cuts in over whatever is being said. In quiet spells the booth talks about the race: battles, gaps,
pace, pit stops, the viewer's own race, and drivers who haven't had a mention, so the whole field gets airtime.
With the AI booth on, the analyst reacts to big moments and the quiet spells become proper conversation.
"""
from __future__ import annotations

from collections import deque

from . import ai as ai_mod
from .cockpit import CATCH_UP_MAX, DriverLoad
from .race import Event
from .writer import ANALYST, LEAD, Line, Writer

CHATTER = {   # seconds of quiet before the booth fills it, and gap after any line
    "quiet": (18.0, 0.6),
    "normal": (2.5, 0.1),
    "chatty": (1.2, 0.0),
}
FOCUS_BOOST = {"field": 0, "balanced": 10, "me": 22}
CUT_IN = 85                  # events this big may interrupt ...
CUT_MARGIN = 20              # ... something at least this much smaller
REACT_MIN = 60               # the AI analyst reacts to events at least this big
FOLLOWUP_TTL = 12.0
FILLER_TTL = 45.0
AI_WAIT = 6.0                # a quiet spell waits this long for the AI booth's conversation before a stock line
CONTINUE = {"rare": 0, "normal": 1, "lots": 3}    # follow-up exchanges to a conversation, per 'Random chat'
RETRY_S = 2.5


class Director:
    def __init__(self, tracker, audio, cfg_fn, booth=None, log=print, seed=None, convo_booth=None):
        self.tr, self.audio, self.cfg_fn = tracker, audio, cfg_fn
        self.booth = booth                                   # ai.AIBooth or None
        self.convo_booth = convo_booth                       # a second one for the conversations (its own provider)
        self.log = log
        self.writer = Writer(tracker, cfg_fn(), seed=seed)
        self.pending: list = []
        self._keys: dict = {}
        self.followups: deque = deque()                      # (lines, made, priority)
        self.ai_filler = None                                # (lines, made)
        self.transcript: deque = deque(maxlen=60)            # (who, text) for the AI's memory
        self.recent_events: deque = deque(maxlen=20)
        self.wrapped = False
        self._wrap_asked = None
        self.winner_t = None
        self.last_said_t = -1e9
        self.on_air = True
        self.convo_left, self.convo_t, self.convo_n = 0, -1e9, 0   # an AI conversation still running
        self._filler_asked = -1e9
        self.load = DriverLoad()                             # driver mode: is the driver busy right now?
        self.memory = None                                   # memory.Memory: the booth's history across races
        self.chat = None                                     # stream.TwitchChat, when reading chat is on
        self.recap = None                                    # recap.Recap: the transcript and moments of this race
        self._recap_done = False
        self.recap_path = None
        self.recording = False                               # this race goes into it (a real race, not the demo)
        self._remembered = False
        self.held_since = None                               # the booth is holding its tongue since then
        self.hold_reason = ""

    # -------------------------------------------------------------- input
    AFTER_FLAG = {"winner", "podium", "player_finish", "retired", "contact", "penalty"}

    def add(self, events, now):
        for e in events:
            if e.key and e.key in self._keys:
                continue
            if self.tr.finished and e.kind not in self.AFTER_FLAG:
                continue                                     # the race is run: no more battle or pace talk
            if e.key:
                self._keys[e.key] = now
            self.pending.append(e)
            try:
                self.recent_events.append(ai_mod.describe(e, self.writer))
            except Exception:
                pass
            if e.kind == "winner" or (e.kind == "player_finish" and getattr(self.tr, "solo", False)):
                self.winner_t = now                            # (your car only: your finish is the end of the race)
            self._remember_event(e)
            if self.recap is not None:
                try:
                    self.recap.event(now, e, ai_mod.describe(e, self.writer))
                except Exception:
                    pass
            if e.kind == "pit_out" and e.cars:                 # every stop is timed, called on air or not, so the
                lane = (e.data or {}).get("lane_time")         # pit lane can compare against all of them
                if lane and lane < 100:
                    self.writer.lane_times[e.cars[0]] = lane
        if len(self._keys) > 2000:
            for k in [k for k, t in self._keys.items() if now - t > 600]:
                del self._keys[k]

    def set_memory(self, memory):
        self.memory = memory
        w = self.writer
        w.memory = memory
        w.on_call_settled = self._call_settled

    def _remembering(self):
        return self.memory is not None and self.recording and self.cfg_fn().get("memory", True)

    def _call_settled(self, commentator, right):
        if self._remembering():
            self.memory.call_settled(commentator, right)

    def _remember_event(self, e):
        if not self._remembering() or self.tr.session != "race":
            return
        names = [self.tr.car(c).name for c in e.cars if self.tr.car(c) is not None]
        kind = {"contact": "contact", "retired": "retired"}.get(e.kind)
        if e.kind == "overtake" and (e.data or {}).get("lead"):
            kind = "lead_pass"
        if kind and (len(names) >= 2 or kind == "retired"):
            self.memory.note(kind, names, self.tr.track)

    def _end_of_race(self, now):
        """The flag has fallen and the dust settled: the race goes into the booth's memory, once."""
        if self._remembered or self.winner_t is None or now - self.winner_t < 20:
            return
        self._remembered = True
        if self._remembering():
            try:
                self.memory.end_race(self.tr)
            except Exception as ex:
                self.log(f"Memory: {ex}")

    def heard(self, line, t=None):
        """The audio calls this as each line goes on air."""
        self.transcript.append((line.who, line.text))
        self.writer.heard(line)
        if self.recap is not None and t is not None:
            self.recap.line(t, self.writer._name(line.who) or line.who, line.text)

    def finish_recap(self, demo=False):
        """The race is over and the booth has signed off: write the recap (once)."""
        if self.recap is None or self._recap_done or not self.tr.finished:
            return None
        self._recap_done = True
        cfg = self.cfg_fn()
        if not cfg.get("recap", True):
            return None
        try:
            from .usage import METER
            self.recap_path = self.recap.write(self.tr, self.writer, float(cfg.get("recap_offset") or 0), demo=demo,
                                               cost=METER.summary(cfg.get("ai_rates") or ""))
        except Exception as ex:
            self.log(f"Recap: {ex}")
            self.recap_path = None
        return self.recap_path

    def set_tracker(self, tr):
        self.tr = tr
        self.writer.tr = tr
        self.reset()

    def reset(self):
        try:
            from .usage import METER
            METER.new_race(self.cfg_fn().get("ai_rates") or "")
        except Exception:
            pass
        self.pending.clear()
        self._keys.clear()
        self.followups.clear()
        self.ai_filler = None
        self.convo_left, self.convo_t, self.convo_n = 0, -1e9, 0
        self._filler_asked = -1e9
        self.wrapped = False
        self._wrap_asked = None
        self.winner_t = None
        self._remembered = False
        if self.recap is not None:
            self.recap.reset()
        self._recap_done = False
        if self.memory is not None:
            self.memory.current, self.memory.calls_now = [], []     # an unfinished race isn't remembered
        self.writer.forget()
        self.writer.clear_memory()
        self.writer.floor_who, self.writer.floor_run = "", 0

    # -------------------------------------------------------------- scoring
    def _score(self, e, now):
        cfg = self.cfg_fn()
        boost = FOCUS_BOOST.get(cfg.get("focus", "balanced"), 10)
        involved = any(getattr(self.tr.car(c), "player", False) for c in e.cars)
        return e.priority + (boost if involved else 0) - e.age(now) * 2.5

    def _best(self, now):
        self.pending = [e for e in self.pending if e.fresh(now) and
                        (not self.tr.finished or e.kind in self.AFTER_FLAG)]
        if not self.pending:
            return None
        return max(self.pending, key=lambda e: self._score(e, now))

    # -------------------------------------------------------------- driver mode
    def driver_input(self, frame, now):
        """Each telemetry frame: is the driver braking, racing someone close, or off the track?"""
        self.load.update(frame, now, self.tr)

    def _hold(self, cfg, now):
        """Driver mode: True while the driver is busy. Nothing new goes on air; a side conversation playing when a
        car gets alongside is stopped. When it's quiet again, what happened meanwhile is moved on by the time held
        (up to CATCH_UP_MAX), so it's still called if it's still news."""
        why = ""
        if cfg.get("drive_mode") == "driver" and self.tr.started and not self.tr.finished and \
                self.tr.session == "race":
            why = self.load.busy(now)
        self.hold_reason = why
        if why == "fight":
            why = ""                                         # a long fight: tick() lets race calls through
        if why:
            if self.held_since is None:
                self.held_since = now
                if why == "close" and self.audio.speaking(now) and self.audio.current_priority < 50:
                    self.audio.interrupt(now)                # no chat about biscuits side by side into Turn 1
            return True
        if self.held_since is not None:
            held = min(CATCH_UP_MAX, now - self.held_since)
            for e in self.pending:
                e.t += held
            self.followups = type(self.followups)((ln, made + held, p) for ln, made, p in self.followups)
            if self.ai_filler:
                self.ai_filler = (self.ai_filler[0], self.ai_filler[1] + held)
            self.held_since = None
        return False

    def talk_about_me(self, now):
        """The 'talk about me' button: the viewer's race, now, over any chat (not over a big call)."""
        me = self.tr.player() if hasattr(self.tr, "player") else None
        if me is None or not self.on_air:
            return False
        if self.audio.speaking(now):
            if self.audio.current_priority >= CUT_IN:
                return False
            self.audio.interrupt(now)
        lines = self.writer._player_line(me, now) if self.tr.session == "race" and \
            getattr(self.tr, "started", False) else [Line(LEAD, f"Let's check in on {self.writer.n(me.id, now)}.", 1)]
        self._air(lines, 60, now)
        if self.booth is not None:
            self._ask(f"The viewer has asked the booth to talk about their driver, {self.writer.n(me.id)}, right "
                      "now. ANALYST picks up straight from the last line with a proper look at their race from the "
                      "data: position against the grid slot, the gaps either side and whether they're shrinking, "
                      "pace against the cars around them, the stops, and what they need to do next. LEAD answers. "
                      "2 or 3 lines.", 55, now, answer=ANALYST)
        return True

    # -------------------------------------------------------------- the loop
    def tick(self, now):
        if not self.on_air:
            self.pending.clear()
            return
        cfg = self.cfg_fn()
        self.writer.cfg = cfg
        if self._hold(cfg, now):
            return
        quiet_s, gap_s = CHATTER.get(cfg.get("chatter", "normal"), CHATTER["normal"])
        if getattr(self.tr, "solo", False):
            quiet_s *= 1.6                      # one car: less to talk about, so the quiet spells are longer
        top = self._best(now)
        if self.audio.speaking(now):
            if top is not None and top.priority >= CUT_IN and \
                    self._score(top, now) >= self.audio.current_priority + CUT_MARGIN:
                self.audio.interrupt(now)
                self._air_event(top, now, interrupting=True)
            return
        idle = self.audio.idle_for(now)
        if top is not None:
            need = 0.3 if top.priority >= CUT_IN else gap_s
            if idle >= need:
                self._air_event(top, now)
            return
        if self.followups:
            lines, made, prio = self.followups[0]
            if now - made > FOLLOWUP_TTL:
                self.followups.popleft()
            elif idle >= 0.4:
                self.followups.popleft()
                self._air(lines, prio, now)
                if self._wrap_asked is not None:
                    self.wrapped = True
                return
        if self.hold_reason == "fight":
            return                                           # in a long fight: race calls, but no chat
        if self.tr.finished:
            self._end_of_race(now)
            self._wrap(now, idle)
            if self.wrapped and self.audio.idle_for(now) > 2.0:     # the sign-off has been said, not just queued
                self.finish_recap(demo=not self.recording)
            return
        if idle >= quiet_s * 0.6:
            self._prefetch_filler(now)
        mid_story = self.writer.active and idle < 6.0    # between the beats of a running conversation
        if idle >= quiet_s or mid_story:
            if self.ai_filler and now - self.ai_filler[1] <= FILLER_TTL:
                lines, _ = self.ai_filler
                self.ai_filler = None
                self._air(lines, 20, now)
                if now - self.convo_t > FILLER_TTL:            # a new conversation: it may run on a while
                    self.convo_left = CONTINUE.get(cfg.get("banter", "normal"), 1)
                    self.convo_n = 0
                self.convo_t = now
                return
            talk = self._talk_booth()
            if not mid_story and talk is not None and talk.busy and \
                    now - self._filler_asked < AI_WAIT:
                return                                           # the AI's conversation is on its way
            lines = self.writer.filler(now, cfg.get("focus", "balanced"))
            if lines:
                self._air(lines, 20, now)
            elif not mid_story:
                self.audio.last_end = now - quiet_s + RETRY_S    # nothing yet: try again shortly, not in a
                return                                           # whole quiet spell

    # -------------------------------------------------------------- going on air
    def _air(self, lines, priority, now):
        lines = self.writer.floor(lines)
        if not lines:
            return
        self.last_said_t = now
        self.audio.say(lines, priority, now)
        self.writer.book(lines)                 # the floor is booked as it queues, not as it plays

    def _merge(self, top, now):
        """Several overtakes at once (a train of cars swapping places, a pit cycle): one call, not five. Several cars
        into the pits together are one report from the pit lane, not a string of single lines."""
        if top.kind == "pit_in":
            return self._merge_pits(top, now)
        same =[e for e in self.pending if e is not top and e.kind == "overtake" and e.age(now) < 3.0
                and e.priority < 90]
        if top.kind != "overtake" or top.priority >= 90 or not same:
            return None
        events = [top] + same[:2]
        for e in events[1:]:
            self.pending.remove(e)
        w = self.writer
        parts = [f"{w.n(e.cars[0], now)} past {w.n(e.cars[1], now)}" for e in events if len(e.cars) > 1]
        if len(parts) < 2:
            return None
        txt = "Lots going on! " + ", ".join(parts[:-1]) + f", and {parts[-1]}."
        from .writer import Line
        return [Line(LEAD, txt, 2)]

    def _merge_pits(self, top, now):
        same = [e for e in self.pending if e is not top and e.kind == "pit_in" and e.age(now) < 6.0]
        if not same or any(getattr(self.tr.car(e.cars[0]), "player", False) for e in [top] + same):
            return None                                     # the viewer's own stop always gets its own call
        events = [top] + same[:3]
        for e in events[1:]:
            self.pending.remove(e)
        w = self.writer
        names = [w.n(e.cars[0], now) for e in events]
        who = w.pit_voice()
        from .writer import Line
        lst = ", ".join(names[:-1]) + f" and {names[-1]}"
        txt = w.pick("pit_rush", [
            f"It's busy down here now: {lst}, all in together.",
            f"A rush into the pit lane! {lst} are all in.",
            f"{lst}, all on the same lap. The lane is packed.",
        ])
        out = [Line(who, txt, 2)]
        if who != LEAD and w.chance(0.6):
            out.append(Line(ANALYST, w.pick("pit_rush_a", [
                f"A busy lane, {w.pit_name}. That's where stops go wrong.",
                f"Thanks, {w.pit_name}. Now it's all about who comes out in front of whom.",
            ]), 1))
        return out

    def _air_event(self, e, now, interrupting=False):
        self.pending.remove(e)
        lines = self._merge(e, now) or self.writer.event(e, now, interrupting=interrupting)
        if not lines:
            return
        self.followups.clear()
        try:
            self.writer.feel(e, lines)                          # how it's said: the feeling of the moment
            extra = self.writer.extras(e, lines, now)           # an outburst, history or a sponsor
            if e.kind == "intro" and extra and len(lines) >= 2:
                lines[1].text += " " + extra[0].text            # one line, so the start can't cut it off
            else:
                lines = lines + extra
        except Exception as ex:
            self.log(f"banter: {ex}")
        self._air(lines, e.priority, now)
        if e.priority >= REACT_MIN and e.kind not in ("start", "caution", "green"):
            self._ask_reaction(e, lines[-1], now)
        if e.kind == "intro" and self.booth is not None:
            self._ask(ai_mod.intro_task(self.writer.lead_name, self.writer.analyst_name), e.priority - 5, now)

    # -------------------------------------------------------------- AI
    def _brief(self):
        return ai_mod.race_brief(self.tr, self.writer, list(self.recent_events), list(self.transcript))

    def _talk_booth(self):
        """The booth that writes the conversations: the second provider's when it has one set up, so it works
        alongside the main one instead of queueing behind the reactions."""
        cb = self.convo_booth
        if cb is not None and self.booth is not None:
            try:
                from .providers import ai_provider, convo_provider
                cfg = self.cfg_fn()
                if convo_provider(cfg) != ai_provider(cfg) and cb.available() and \
                        self.clock_ok(cb):
                    return cb
            except Exception:
                pass
        return self.booth

    @staticmethod
    def clock_ok(b):
        return b.clock() >= b.disabled_until                 # a second provider that keeps failing steps aside

    def _ask(self, task, priority, now, filler=False, answer=LEAD, max_tokens=420):
        booth = self._talk_booth() if filler else self.booth
        if booth is None or not booth.ready():
            return False
        made = now

        def done(lines, info):
            if filler:
                self.ai_filler = (lines, made)
            else:
                self.followups.append((lines, made, priority))
        try:
            return booth.request(task, self._brief(), done, opener=answer, max_tokens=max_tokens,
                                 purpose="convo" if filler else "react")
        except Exception as e:
            self.log(f"AI booth: {e}")
            return False

    def _ask_reaction(self, e, last, now):
        """The AI booth carries the call on: whoever did not say the last line reacts to it, so the analyst's
        insight is answered by the lead (or the other way round) rather than stacked on top of itself."""
        cfg = self.cfg_fn()
        if cfg.get("chatter") == "quiet" and e.priority < 80:
            return
        answer = LEAD if last.who == ANALYST else ANALYST
        speaker = self.writer.lead_name if last.who == LEAD else self.writer.analyst_name
        other = self.writer.analyst_name if answer == ANALYST else self.writer.lead_name
        self._ask(ai_mod.react_task(ai_mod.describe(e, self.writer), last.text, speaker, other),
                  max(30, e.priority - 30), now, answer=answer)

    def _prefetch_filler(self, now):
        if self.ai_filler is not None and now - self.ai_filler[1] <= FILLER_TTL:
            return
        if self.tr.session == "race" and not self.tr.started:
            return
        talk = self._talk_booth()
        if talk is None or not talk.ready():
            return                                         # nobody free to write it: try again next tick
        if self.chat is not None:
            odds = {"rare": 0.25, "normal": 0.45, "lots": 0.65}.get(self.cfg_fn().get("banter", "normal"), 0.45)
            msgs = self.chat.fresh() if self.writer.chance(odds) else []
            if msgs:
                task, opener = ai_mod.chat_task(self.writer, self.chat.take(3))
                if self._ask(task, 20, now, filler=True, answer=opener):
                    self._filler_asked = now
                    self.convo_t, self.convo_left, self.convo_n = now, 1, 0
                    return
        if self.convo_left > 0 and now - self.convo_t < 30:
            task, opener = ai_mod.continue_task(self.writer, self.convo_n + 1)
            if self._ask(task, 20, now, filler=True, answer=opener):
                self.convo_left -= 1
                self.convo_n += 1
                self._filler_asked = now
            return
        task, opener = ai_mod.filler_task(self.writer, now, self.cfg_fn().get("focus", "balanced"))
        if self._ask(task, 20, now, filler=True, answer=opener, max_tokens=520):
            self._filler_asked = now

    def _wrap(self, now, idle):
        if self.wrapped or self.winner_t is None or now - self.winner_t < 25 or idle < 3:
            return
        if self._wrap_asked is None and self.booth is not None and \
                self._ask(ai_mod.wrap_task(self.writer.lead_name, self.writer.analyst_name), 40, now):
            self._wrap_asked = now
            return
        if self._wrap_asked is not None and now - self._wrap_asked < 10:
            return                                           # the AI's sign-off is on its way
        self.wrapped = True
        self._air(self.writer.wrap_up(now), 40, now)


def test_event(kind, now, cars=(), **data):
    """Build an Event by hand (tests)."""
    return Event(kind, now, data.pop("priority", 70), tuple(cars), data)
