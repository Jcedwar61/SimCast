"""ElevenLabs voices: the most emotional of the lot.

ElevenLabs' v4 Turbo, v4 and v3 models read emotion cues written into the text ('[excited] Into the lead!',
'[sighs] That's their race over.'), so each line's mood (persona.MOODS) becomes a cue, and the louder the booth's
personality, the looser the voice is allowed to get. v4 Turbo comes first: v4's expressiveness, tuned for speed
(about 0.1 s), at half v4's price ($0.04 against $0.08 per 1,000 characters). A model the account can't use is
skipped for the rest of the session: v4, then v3, then multilingual v2, which is used without the cues. The voices offered are the ones on your ElevenLabs account (fetched with your key).
Voice ids are stored as 'eleven:<voice id>'.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1"
MODELS = ("eleven_v4_turbo", "eleven_v4", "eleven_v3", "eleven_multilingual_v2")   # best value first
CUE_MODELS = {"eleven_v4_turbo", "eleven_v4", "eleven_v3"}                      # the ones that take emotion cues
CUES = {"excited": "[excited]", "thrilled": "[excited]", "angry": "[angry]", "frustrated": "[sighs]",
        "shocked": "[excited]", "gutted": "[sad]", "amused": "[laughs]", "sarcastic": "[sarcastic]"}
LOOSENESS = {"broadcast": (0.5, 0.35), "loose": (0.4, 0.55), "unhinged": (0.25, 0.8)}   # (stability, style)

_voices: dict = {}            # key fingerprint -> [(label, 'eleven:<id>')]
_refused: set = set()         # models this key can't use
_lock = threading.Lock()


def key_of(cfg):
    return str((cfg or {}).get("elevenlabs_api_key") or "").strip()


def cue(mood):
    return CUES.get(mood or "", "")


def _req(url, key, body=None, timeout=20.0):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                               headers={"xi-api-key": key, "Content-Type": "application/json",
                                        "Accept": "audio/mpeg" if body is not None else "application/json",
                                        "User-Agent": "SimCast"})
    return urllib.request.urlopen(r, timeout=timeout)


def fetch_voices(cfg, log=None):
    """The voices on this ElevenLabs account, as (label, voice id) for Settings > Voices. Blocking."""
    key = key_of(cfg)
    if not key:
        return []
    try:
        with _req(f"{API}/voices", key, timeout=10.0) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        if log:
            log(f"ElevenLabs: couldn't list the voices ({type(e).__name__})")
        return []
    out = []
    for v in d.get("voices") or []:
        vid, name = v.get("voice_id"), (v.get("name") or "").strip()
        if not vid or not name:
            continue
        lab = v.get("labels") or {}
        accent = (lab.get("accent") or "").title()
        out.append((f"{name}{f' ({accent})' if accent else ''} - ElevenLabs (your key)", f"eleven:{vid}"))
    out.sort()
    with _lock:
        _voices[key[-8:]] = out
    if log:
        log(f"ElevenLabs: {len(out)} voices on the account")
    return out


def voices(cfg):
    with _lock:
        return list(_voices.get(key_of(cfg)[-8:], []))


def fetch_in_background(cfg, log=None):
    if key_of(cfg) and not voices(cfg):
        threading.Thread(target=fetch_voices, args=(cfg, log), daemon=True, name="eleven-voices").start()


def synth(cfg, voice_id, text, mood, path, timeout=20.0):
    """Speak one line to an mp3 file, with the mood as an emotion cue where the model takes one."""
    key = key_of(cfg)
    if not key:
        raise RuntimeError("no ElevenLabs key")
    stab, style = LOOSENESS.get(cfg.get("booth_tone", "broadcast"), LOOSENESS["broadcast"])
    last = None
    for model in MODELS:
        if (key[-8:], model) in _refused:
            continue
        c = cue(mood) if model in CUE_MODELS else ""
        body = {"text": f"{c} {text}".strip(), "model_id": model,
                "voice_settings": {"stability": stab, "similarity_boost": 0.75, "style": style,
                                   "use_speaker_boost": True}}
        try:
            with _req(f"{API}/text-to-speech/{voice_id}?output_format=mp3_44100_128", key, body, timeout) as resp, \
                    open(path, "wb") as fh:
                while True:
                    chunk = resp.read(16384)
                    if not chunk:
                        break
                    fh.write(chunk)
            return
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 403, 404, 422) and model != MODELS[-1]:
                _refused.add((key[-8:], model))           # this account can't use it: don't ask again
                continue
            raise
    raise last or RuntimeError("ElevenLabs refused every model")

