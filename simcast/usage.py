"""What the AI and the paid voices cost: tokens and characters counted as they're used, priced with rough rates.

The rates are dollars per million tokens (in/out) for the AI providers and per million characters for the
voices. They're estimates: providers change their prices, so the rates can be edited in Settings > AI. Counted
per race (reset when a new session starts) and in total since SimCast started.
"""
from __future__ import annotations

import re
import threading
import time

# $ per 1M tokens (input, output) / per 1M characters. Rough; edit in Settings > AI.
DEFAULT_RATES = "openai 2/8, gemini 0.3/2.5, deepseek 0.3/1.2, openai-voice 15, elevenlabs 200"


def parse_rates(text):
    """'openai 2/8, deepseek 0.3/1.2, elevenlabs 200' -> {'openai': (2.0, 8.0), ..., 'elevenlabs': (200.0,)}."""
    out = {}
    for part in re.split(r"[,;\n]", str(text or "")):
        m = re.match(r"\s*([a-z0-9\-]+)\s+([\d.]+)(?:\s*/\s*([\d.]+))?\s*$", part.strip().lower())
        if m:
            vals = tuple(float(x) for x in m.groups()[1:] if x is not None)
            out[m.group(1)] = vals
    return out


class Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.race = {}
        self.total = {}
        self.race_t0 = time.monotonic()
        self.last_race = None

    def _add(self, key, a, b=0):
        with self.lock:
            for d in (self.race, self.total):
                x = d.get(key, [0, 0])
                d[key] = [x[0] + a, x[1] + b]

    def chat(self, provider, tokens_in, tokens_out):
        self._add(provider, int(tokens_in or 0), int(tokens_out or 0))

    def speech(self, service, chars):
        self._add(service, int(chars or 0))

    def new_race(self, rates_text=DEFAULT_RATES):
        """A new session: what the last one cost is kept for the settings, and the count starts again."""
        with self.lock:
            had = dict(self.race)
        if had:
            self.last_race = self.cost(had, rates_text)
        with self.lock:
            self.race = {}
            self.race_t0 = time.monotonic()

    @staticmethod
    def cost(counts, rates_text=DEFAULT_RATES):
        rates = parse_rates(DEFAULT_RATES)
        rates.update(parse_rates(rates_text))
        total = 0.0
        for k, (a, b) in counts.items():
            r = rates.get(k)
            if not r:
                continue
            total += a / 1e6 * r[0] + (b / 1e6 * r[1] if len(r) > 1 else 0.0)
        return total

    def summary(self, rates_text=DEFAULT_RATES, now=None):
        """'This race so far: about $0.12 (...). At this rate, a 30-minute race is about $0.40.'"""
        with self.lock:
            race = dict(self.race)
            t0 = self.race_t0
        if not race and self.last_race is None:
            return "Nothing paid for has been used yet."
        bits = []
        names = {"openai": "OpenAI", "gemini": "Gemini", "deepseek": "DeepSeek", "openai-voice": "OpenAI voices",
                 "elevenlabs": "ElevenLabs"}
        for k, (a, b) in sorted(race.items()):
            bits.append(f"{names.get(k, k)} {a / 1000:.1f}k chars" if b == 0 and "voice" in k or k == "elevenlabs"
                        else f"{names.get(k, k)} {a / 1000:.1f}k tokens in, {b / 1000:.1f}k out")
        c = self.cost(race, rates_text)
        out = f"This race so far: about ${c:.2f}" + (f" ({'; '.join(bits)})." if bits else ".")
        mins = ((now or time.monotonic()) - t0) / 60.0
        if c > 0 and mins >= 3:
            out += f" At this rate, a 30-minute race is about ${c / mins * 30:.2f}."
        if self.last_race is not None:
            out += f" Last race: about ${self.last_race:.2f}."
        return out


METER = Meter()


def estimate_tokens(text):
    """Roughly four characters a token, for replies whose usage the provider didn't report."""
    return max(1, len(str(text or "")) // 4)
