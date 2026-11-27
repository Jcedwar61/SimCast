"""The AI off the air: jobs that help around the broadcast rather than speak in it.

The booth (ai.py) talks live and has seconds to answer. These jobs have time, so they go to the strongest writer
for the money: DeepSeek first when it has a key (it writes well and costs a fraction), then OpenAI, then Gemini,
with the usual fallback to another provider if one is down. Settings > AI > Write-ups picks a provider or turns
them off.

- Race report: when a race ends, the recap gets a written report of the race, YouTube title ideas, a video
  description, tags and a short social post, all written from the result, the highlights and what the booth said.
- Season storylines: after each remembered race, the booth's memory gets a few running storylines written from the
  results it holds (a streak, a rivalry, a driver on the up), and the AI booth brings them up in later races.
- Chat safety: Twitch messages are checked with OpenAI's moderation model (free with an OpenAI key) before the
  booth sees them, on top of the booth's own rules about chat.
- Key check: Settings > AI > Test keys asks each provider with a key for a one-word answer and says what's wrong
  with any that fail.

Nothing here runs without a key, and a failure is logged and dropped: the broadcast never waits on these jobs.
"""
from __future__ import annotations

import os
import threading
import time

from . import providers

ASSIST_CHOICES = ("auto", "off", "deepseek", "openai", "gemini")
ASSIST_ORDER = ("deepseek", "openai", "gemini")     # Automatic: the first of these with a key
REPORT_TIMEOUT = 90.0
STORY_TIMEOUT = 60.0
MAX_STORYLINES = 5
MODERATION_MODEL = "omni-moderation-latest"
MODERATION_TIMEOUT = 5.0

SYNC = False                                         # tests: run jobs in the calling thread


# ------------------------------------------------------------------------------------------------- provider
def assist_provider(cfg):
    """The provider for write-ups, or None when they're off or no provider has a key."""
    cfg = cfg or {}
    pick = cfg.get("ai_assist") or "auto"
    if pick == "off":
        return None
    if pick in providers.AI_PROVIDERS and providers.ai_key(cfg, pick):
        return pick
    return next((p for p in ASSIST_ORDER if providers.ai_key(cfg, p)), None)


def assist_cfg(cfg):
    """The settings as a write-up job sees them: its provider first and the best writer that provider has."""
    return dict(cfg, ai_provider=assist_provider(cfg) or providers.ai_provider(cfg), _purpose="convo")


def assist_label(cfg):
    p = assist_provider(cfg)
    return providers.ai_label(assist_cfg(cfg), p) if p else ""


def assist_chat(cfg, messages, max_tokens=1200, timeout=REPORT_TIMEOUT):
    if assist_provider(cfg) is None:
        raise ValueError("no API key for write-ups")
    return providers.ai_chat_ex(assist_cfg(cfg), messages, max_tokens=max_tokens, timeout=timeout)[0]


def background(name, fn, log=print):
    """Run fn() off the main loop; any failure is logged, never raised."""
    def work():
        try:
            fn()
        except Exception as e:
            log(f"{name}: {type(e).__name__}: {e}"[:300])
    if SYNC:
        work()
    else:
        threading.Thread(target=work, daemon=True, name=f"assist-{name}").start()


def _text(x, limit):
    return " ".join(str(x or "").split())[:limit]


# ------------------------------------------------------------------------------------------------- race report
REPORT_SYSTEM = """You write up sim races for the driver who streamed them: a race report like a motorsport \
website's, and what they need to post the video. Write from the RACE FACTS only. Never invent contact, damage, \
penalties, weather, strategy, quotes from drivers or anything else that isn't there. The COMMENTARY is what the \
broadcast booth said on air: use it for colour and the moments it picked out, but trust RESULT and HIGHLIGHTS \
over it where they differ, and never present a commentator's opinion or prediction as fact. Call it a race, not \
a "sim race", except in the tags. British or American spelling to match the commentary. No emojis except, at \
most, one in the social post.

Reply with JSON only:
{"headline": "a news-style headline, at most 12 words",
 "report": "the race report: 3 or 4 short paragraphs separated by blank lines (\\n\\n), the winner and how they \
won first, then the story of the race in order, the viewer's driver's race, and a closing line",
 "youtube_titles": ["3 title ideas, at most 70 characters each, punchy, no clickbait lies"],
 "description": "a YouTube description of 2 or 3 sentences (the chapters are added separately)",
 "tags": ["8 to 12 short tags: the track, the sim if known, the car class if known, sim racing, ..."],
 "social": "one post for social media, at most 240 characters"}"""


def report_messages(facts):
    return [{"role": "system", "content": REPORT_SYSTEM},
            {"role": "user", "content": f"{facts}\n\nWrite the report."}]


def parse_report(raw):
    """The model's reply as a clean dict, or None if it isn't usable."""
    d = providers._json_reply(raw)
    if not isinstance(d, dict):
        return None
    report = str(d.get("report") or "").strip()
    if len(report) < 40:
        return None
    paras = [" ".join(p.split()) for p in report.replace("\r", "").split("\n\n") if p.strip()]

    def strs(v, n, limit):
        return [_text(x, limit) for x in (v if isinstance(v, list) else []) if _text(x, limit)][:n]
    return {"headline": _text(d.get("headline"), 140), "report": "\n\n".join(paras)[:4000],
            "youtube_titles": strs(d.get("youtube_titles"), 5, 100), "description": _text(d.get("description"), 800),
            "tags": strs(d.get("tags"), 15, 40), "social": _text(d.get("social"), 300)}


def report_markdown(rep, label=""):
    """The report as a recap section."""
    out = ["## Race report", ""]
    if label:
        out += [f"*Written by {label} from the result, the highlights and the commentary.*", ""]
    if rep.get("headline"):
        out += [f"### {rep['headline']}", ""]
    out += [rep["report"], ""]
    if rep.get("youtube_titles") or rep.get("description") or rep.get("tags"):
        out += ["## For the video", ""]
        if rep.get("youtube_titles"):
            out += ["Title ideas:", ""] + [f"- {t}" for t in rep["youtube_titles"]] + [""]
        if rep.get("description"):
            out += ["Description (paste the chapters below it):", "", "```", rep["description"], "```", ""]
        if rep.get("tags"):
            out += ["Tags:", "", "```", ", ".join(rep["tags"]), "```", ""]
    if rep.get("social"):
        out += ["Social post:", "", "```", rep["social"], "```", ""]
    return "\n".join(out)


def insert_section(path, section):
    """Put a section into a recap file, after the title and before its first section."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    k = text.find("\n## ")
    text = (text.rstrip("\n") + "\n\n" + section) if k < 0 else (text[:k + 1] + section.rstrip("\n") + "\n\n" +
                                                                  text[k + 1:])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_report(cfg, path, facts, chat_fn=None, log=print):
    """Ask for the race report and add it to the recap at path. Returns the report dict, or None."""
    chat_fn = chat_fn or assist_chat
    raw = chat_fn(cfg, report_messages(facts), max_tokens=1400, timeout=REPORT_TIMEOUT)
    rep = parse_report(raw)
    if rep is None:
        log("Race report: the AI's answer wasn't usable; the recap is left as it was.")
        return None
    insert_section(path, report_markdown(rep, assist_label(cfg)))
    log(f"Race report added to the recap: {rep.get('headline') or path}")
    return rep


# ------------------------------------------------------------------------------------------------- storylines
STORY_SYSTEM = """You are the producer of a sim racing league's broadcast. From the RESULTS of the league's \
recent races, write the running storylines the commentators should know going into the next race, the way a \
TV booth's notes read: a winning streak, a driver on the rise or in a slump, a rivalry between two drivers who \
keep finishing together or have tangled, someone who keeps retiring, the viewer's driver's season. Every \
storyline must follow from the RESULTS and INCIDENTS given: count and compare, never invent. One sentence each, \
at most 30 words, with the drivers' names as given. Skip anything that rests on one race alone unless it's big.

Reply with JSON only: {"storylines": ["...", "..."]} with 2 to 5 storylines, the strongest first, or an empty \
list if there aren't enough races yet."""


def story_messages(facts):
    return [{"role": "system", "content": STORY_SYSTEM}, {"role": "user", "content": facts}]


def parse_storylines(raw):
    d = providers._json_reply(raw)
    if not isinstance(d, dict) or not isinstance(d.get("storylines"), list):
        return None
    return [s for s in (_text(x, 260) for x in d["storylines"]) if len(s) > 10][:MAX_STORYLINES]


def write_storylines(cfg, memory, chat_fn=None, log=print):
    """Refresh the memory's season storylines from its results. Returns them, or None."""
    facts = memory.season_facts()
    if not facts:
        return None
    chat_fn = chat_fn or assist_chat
    stories = parse_storylines(chat_fn(cfg, story_messages(facts), max_tokens=700, timeout=STORY_TIMEOUT))
    if stories is None:
        log("Storylines: the AI's answer wasn't usable.")
        return None
    memory.set_storylines(stories)
    log(f"Storylines updated ({len(stories)}).")
    return stories


# ------------------------------------------------------------------------------------------------- chat safety
class Moderator:
    """Twitch chat through OpenAI's moderation model before the booth sees it. allow(text) -> bool. Free with an
    OpenAI key; without one (or with it switched off) everything passes to the booth's own rules. While the check is
    on, a message it can't vouch for is held back: if the check itself fails, and for a couple of minutes after a
    few failures in a row (it rests rather than hammering a broken key or network). The AI booth's replies to chat
    go through the same check before they're said on air."""

    def __init__(self, cfg_fn, log=print, create_fn=None, clock=time.monotonic):
        self.cfg_fn, self.log, self.clock = cfg_fn, log, clock
        self.create_fn = create_fn            # tests: (key, text) -> flagged bool
        self.failures = 0
        self.rest_until = 0.0
        self.blocked = 0

    def active(self):
        cfg = self.cfg_fn() or {}
        return bool(cfg.get("twitch_moderate", True) and providers.ai_key(cfg, "openai"))

    def _flagged(self, key, text):
        if self.create_fn is not None:
            return bool(self.create_fn(key, text))
        client = providers.openai_client(key).with_options(timeout=MODERATION_TIMEOUT)
        r = client.moderations.create(model=MODERATION_MODEL, input=text)
        return any(getattr(x, "flagged", False) for x in (r.results or []))

    def allow(self, text):
        if not self.active():
            return True
        if self.clock() < self.rest_until:
            return False                       # the check is resting after failures: hold chat back meanwhile
        try:
            flagged = self._flagged(providers.ai_key(self.cfg_fn(), "openai"), text)
            self.failures = 0
        except Exception as e:
            self.failures += 1
            if self.failures >= 3:
                self.rest_until = self.clock() + 120.0
                self.log(f"Chat check: {type(e).__name__}; chat is held back for two minutes")
            return False
        if flagged:
            self.blocked += 1
        return not flagged


# ------------------------------------------------------------------------------------------------- key check
def test_keys(cfg, chat_fn=None):
    """{provider: (ok, message)} for every provider with a key: a tiny real request to each."""
    out = {}
    for p, spec in providers.AI_PROVIDERS.items():
        key = providers.ai_key(cfg, p)
        if not key:
            continue
        model = providers.ai_model(cfg, p)
        try:
            if chat_fn is not None:
                chat_fn(p, key, model)
            else:
                providers.openai_chat(key, model, [{"role": "user", "content": "Reply with the word: ready"}],
                                      max_tokens=20, timeout=20.0, provider=p)
            out[p] = (True, f"{spec['name']}: working ({model})")
        except Exception as e:
            msg = providers.openai_problem(e, p).replace(" Check it in SimCast's Settings.", "")
            out[p] = (False, f"{spec['name']}: {msg}")
    return out


__all__ = ["assist_provider", "assist_label", "assist_chat", "background", "write_report", "write_storylines",
           "Moderator", "test_keys", "parse_report", "parse_storylines", "report_markdown", "insert_section"]
