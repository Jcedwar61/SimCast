"""Who the booth is talking about, and how the booth talks.

- Pronouns from first names: a driver called 'Lewis Hamilton' is 'he', 'Jamie Chadwick' is 'she'. A name that
  isn't clearly one or the other (Alex, Sam, Jordan, a gamer tag, a surname on its own) stays 'they': a wrong
  guess is worse than a neutral word.
- The booth's personality: professional, loose or unhinged, with or without swearing.
- Sponsors: the race's title sponsor and each car's backers, from the settings, or made-up brands when asked.
"""
from __future__ import annotations

import re
import zlib

# ------------------------------------------------------------------------------------------------- names
# Common first names that are clearly one or the other across the countries sim racers and sim AI fields come
# from. Names used for both (Alex, Sam, Jordan, Andrea, Nicola, Jean, ...) are left out on purpose.
_MALE = set("""
aaron abdul adam adrian ahmed aidan ainsley alain alan albert alberto aleksander alessandro alessio alexander
alexandre alexei alfie alfonso alfred ali alonso alvaro anders andre andreas andrei andres andrew andy angelo anthony
anton antonio antoine arjun armando arnaud arne arthur arturo axel barry bart ben benjamin benoit bernd bernard bernie
bill billy bjorn blake bob bobby boris brad bradley brandon brendon brett brian bruce bruno bryan callum calvin carl
carlos cesar charles charlie chase chen christian christoph christopher clark colin colton connor conor craig cristian
cristiano curtis cyril damian damon dan daniel daniil danny dario darren dave david dean denis dennis derek diego
dmitri dmitry dominic dominik don donald douglas duncan dylan eddie edgar edoardo eduardo edward elias elliot emanuele
emil emilio emmanuel enrico enrique enzo eric erik ernesto esteban ethan eugene evan fabian fabio felipe felix
fernando filip filippo finn francesco franco frank franz fred freddie frederic frederik gabriel gareth gary gavin
geoff george georg gerald gerhard gianni gil giorgio giovanni giuseppe glenn gordon graham greg gregor guido
guillaume gunnar gustavo guy hans harald harrison harry harvey hector heinz helio helmut henri henrik henry herbert
hugo ian igor isaac ivan jack jackson jacob jacques jaime jake james jamal jan jared jason javier jeff jeffrey
jenson jeremy jerome jerry jim jimmy joachim joao joe joel johan johann john johnny jonas jonathan jordi jorge jose
josef joseph josh joshua juan julian julien juri justin kai kamil karl karim keith kelvin ken kenneth kevin kieran
kimi klaus konstantin kurt kyle lance lando lars laurent lawrence lee leo leon leonardo lewis liam logan lorenzo
louis luca lucas luciano ludovic luis luiz lukas luke magnus malcolm manuel marc marcel marco marcos marcus mario
mark marko martin mateo mathias matteo matthew matthias mattia maurice mauricio max maxim maximilian mehdi michael
michel miguel mika mike mikhail milan mitch mohamed mohammed nathan nathaniel neil nelson nicholas nick nico
nicolas niels nigel niki nikita nikolai nils noah norbert oliver olivier omar oscar oskar otto owen pablo paolo
pascal patrick paul paulo pedro per peter petr phil philip philipp philippe pierre pietro piotr rafael rafa ralf ralph
ramon randy raul ray raymond reza ricardo riccardo richard rick ricky rob robert roberto rod roger roland
roman romain ron ronald ronnie ross roy rubens ruben rudolf rui russell ryan ryo salvador samuel santiago scott
sean sebastian sebastien sergei sergey sergio seth shane simon stefan stefano stephane stephen steve steven stuart
sven takuma taro ted teo terry thiago thierry thomas tim timo timothy toby todd tom tommy tony torsten travis
tristan troy tyler ugo ulrich umberto valentino valtteri victor vincent vitaly vladimir walter warren wayne werner
will william wolfgang xavier yannick yuki yuri zach zachary zak
""".split())

_FEMALE = set("""
abbie abby abigail ada adele adriana agnes aida aimee alana alessandra alexandra alice alicia alina alison
amanda amber amelia amy ana anastasia angela angelica angelina anita ann anna annabel anne annie antonia april ariana
ashley astrid audrey aurora ava barbara beatrice beatriz bella beth bethany bianca brenda bridget brittany camila
camille carla carmen carol carolina caroline cassandra catherine cecilia celine charlotte chelsea chloe christina
christine claire clara claudia colette cristina daisy dana daniela danielle daria debbie deborah denise diana
donna dora dorothy eleanor elena eliza elizabeth ella ellen ellie eloise elsa emilia emily emma erica
erika esther eva eve evelyn fatima felicity fiona florence francesca gabriela gabriella gemma georgia gina giulia
grace hailey hanna hannah harriet hazel heather heidi helen helena holly ida ines ingrid irene iris isabel isabella
isabelle ivana jacqueline jade jana jane janet jasmine jennifer jenny jessica jill joan joanna jodie
johanna josephine joy judith julia juliana julie juliette justine karen karin kate katarina katherine kathryn katie
katrin kayla kelly kira kirsten lara laura lauren layla lea leah lena lily linda lisa liv lola lorena louise
lucia lucy luisa lydia madeleine madison maggie maja manuela margaret margot maria mariana marie marina marion
marta martina mary maya megan melanie melissa mia michelle mila milena miriam molly monica monika nadia naomi
natalia natalie natasha nicole nina nora olga olivia paige pamela paola patricia paula pauline penelope petra
phoebe pippa polly priya rachel rebecca renee rita roberta rosa rose rosie ruby ruth sabine sabrina sally samantha
sandra sara sarah scarlett selena serena sienna silvia simona sofia sonia sophia sophie stella
stephanie susan susanne svetlana sylvia tamara tanya tatiana teresa tess tessa tina tracy valentina valeria
vanessa vera veronica victoria viktoria violet virginia wendy yasmin yvonne zara zoe
""".split())

def first_name(name: str) -> str:
    """The first name in 'Lewis Hamilton', 'L. Hamilton' (none), 'HAMILTON Lewis' (none: can't tell), 'lewis_h'."""
    n = re.sub(r"[_\-.]+", " ", str(name or "")).strip()
    n = re.sub(r"\d+", " ", n).strip()
    parts = n.split()
    if not parts:
        return ""
    f = parts[0]
    if len(f) < 2:
        return ""
    return f.lower()


def name_gender(name: str):
    """'m', 'f' or None from a person's first name."""
    f = first_name(name)
    if not f:
        return None
    if f in _MALE:
        return "m"
    if f in _FEMALE:
        return "f"
    return None


PRONOUNS = {"m": ("he", "him", "his"), "f": ("she", "her", "her"), None: ("they", "them", "their")}
PRONOUN_CHOICES = ("auto", "he", "she", "they")


def pronoun(g) -> str:
    return PRONOUNS.get(g, PRONOUNS[None])[0]


def from_choice(choice, name=""):
    """The gender a pronoun setting stands for ('auto' reads the name)."""
    if choice == "he":
        return "m"
    if choice == "she":
        return "f"
    if choice == "they":
        return None
    return name_gender(name)


def voice_gender(voice: str):
    """'Ryan (British) - Microsoft' -> 'm'; 'en-GB-SoniaNeural' -> 'f'; 'kokoro:bf_emma' -> 'f'."""
    s = str(voice or "").split(":")[-1].strip()
    s = re.sub(r"^[a-z]{2}-[A-Z]{2}-", "", s)              # Microsoft ids: en-GB-SoniaNeural
    s = re.sub(r"Neural$", "", s.split()[0] if s.split() else "")
    s = s.split("_")[-1]                                   # Kokoro ids: bf_emma
    return name_gender(s)


def booth_gender(cfg, who, voices=()):
    """A commentator's gender: from their name, or failing that from the voice they speak with."""
    g = name_gender(cfg.get(f"{who}_name") or "")
    if g:
        return g
    vid = cfg.get(f"{who}_voice") or ""
    label = next((lab for lab, v in voices if v == vid), vid)
    return voice_gender(label)


# ------------------------------------------------------------------------------------------------- emotion
# How a line is said. (rate %, pitch Hz, volume %) on top of the energy, for voices that only take prosody, and a
# direction for voices that take one (OpenAI). Scaled by the booth's personality: a TV booth shows it, an
# unhinged one lets it all out.
MOODS = {
    "calm": (-4, -2, -3, "calm, measured, relaxed"),
    "excited": (6, 4, 5, "excited, voice rising, quickening"),
    "thrilled": (10, 8, 10, "ecstatic, shouting with joy over the crowd"),
    "angry": (5, -3, 10, "angry and exasperated, sharp, biting the words off"),
    "frustrated": (2, -2, 5, "frustrated, exasperated, a groan in the voice"),
    "shocked": (7, 7, 8, "shocked, gasping, voice jumping up"),
    "gutted": (-10, -6, -5, "gutted and deflated, sympathetic, slower"),
    "amused": (2, 3, 0, "amused, laughing through the words, playful"),
    "tense": (3, 1, -3, "tense, hushed, on the edge of the seat"),
    "sarcastic": (-3, -1, 0, "dry and sarcastic, deadpan"),
}
MOOD_SCALE = {"broadcast": 0.6, "loose": 1.0, "unhinged": 1.5}


def mood_prosody(mood, tone="broadcast"):
    """(rate %, pitch Hz, volume %) to add for a mood."""
    m = MOODS.get(mood)
    if not m:
        return 0, 0, 0
    k = MOOD_SCALE.get(tone, 1.0)
    return int(round(m[0] * k)), int(round(m[1] * k)), int(round(m[2] * k))


def mood_direction(mood, tone="broadcast", swearing=False):
    """The spoken direction for a voice that takes one."""
    m = MOODS.get(mood)
    out = f"Emotion: {m[3]}." if m else ""
    if tone == "loose":
        out += " Relaxed and expressive, with real feeling: laugh, groan, raise your voice when it's earned."
    elif tone == "unhinged":
        out += (" Over the top: really shout, really groan, laugh out loud, sound like you've lost it. Big swings "
                "of emotion from one line to the next.")
    if swearing:
        out += " Say any swear words naturally and with feeling, never bleeped."
    return out.strip()


# ------------------------------------------------------------------------------------------------- tone
TONES = ("broadcast", "loose", "unhinged")
BANTER = ("rare", "normal", "lots")

TONE_TEXT = {
    "broadcast": "Tone: a proper television booth. Warm, a bit of teasing, never rude.",
    "loose": ("Tone: LOOSE. This booth has let its hair down. More banter, ribbing each other, running jokes, "
              "slang, big opinions said with a grin, proper overreactions to the big moments ('Oh, come ON!'), "
              "laughing at their own mistakes. Still a race call: the jokes come out of what's happening on track "
              "or what the other one just said."),
    "unhinged": ("Tone: UNHINGED. The booth has completely lost it, in the best way. Wild overreactions, mock "
                 "outrage at a bad move, ranting tangents that spiral and then snap back to the race, conspiracy "
                 "theories about a team's strategy, petty feuds between the two of you over an old prediction, "
                 "dramatic despair when a call goes wrong, gallows humour, shouting at the screen. Every bit of "
                 "it is set off by what's happening in the race or by what was just said: unhinged, but always "
                 "about this race and this conversation."),
}

SWEAR_ON = ("Swearing is allowed and sounds natural for this booth: curse when a moment earns it (a dive-bomb, a "
            "spin, a botched stop, a ridiculous take from your colleague). Real words, not bleeped or starred, "
            "but not in every line. Never slurs, nothing hateful about anyone's identity, nothing sexual.")
SWEAR_OFF = "Keep the language clean: no swearing."


def tone_rules(cfg) -> str:
    tone = cfg.get("booth_tone", "broadcast")
    return TONE_TEXT.get(tone, TONE_TEXT["broadcast"]) + " " + (SWEAR_ON if cfg.get("swearing") else SWEAR_OFF)


# ------------------------------------------------------------------------------------------------- sponsors
# Made-up brands only: two halves put together, so every driver gets a believable backer that isn't a real company.
_BRAND_A = ("Vantor", "Kestrel", "Halcyon", "Novalith", "Brightwater", "Corvane", "Redline", "Ashgrove", "Zentrix",
            "Polaris", "Ironbark", "Silverpeak", "Quillon", "Tarrant", "Velocis", "Northstar", "Bluefen", "Orbis",
            "Cobaltis", "Starling", "Granford", "Lumora", "Hexley", "Thornfield", "Apexa", "Marlowe", "Ravelin",
            "Solent", "Drummond", "Ventura")
_BRAND_B = ("Lubricants", "Energy Drinks", "Logistics", "Watches", "Tyres", "Insurance", "Telecom", "Coffee",
            "Software", "Bank", "Motor Oil", "Freight", "Brakes", "Beverages", "Airlines", "Cloud", "Tools",
            "Fuels", "Electronics", "Apparel", "Racing Gear", "Batteries", "Holdings", "Crypto Exchange",
            "Pizza", "Mattresses", "Sunglasses", "Hardware")


def _h(s: str) -> int:
    return zlib.crc32(str(s).lower().encode("utf-8"))


def made_up_sponsors(name: str, n=2):
    """One or two fictional backers for a driver, the same every time for the same name."""
    h = _h(name)
    out = []
    for k in range(n if h % 3 else 1):
        a = _BRAND_A[(h >> (k * 7)) % len(_BRAND_A)]
        b = _BRAND_B[(h >> (k * 5 + 3)) % len(_BRAND_B)]
        s = f"{a} {b}"
        if s not in out and not any(x.split()[0] == a for x in out):
            out.append(s)
    return out


def made_up_title(track: str) -> str:
    h = _h("title:" + (track or "race"))
    return f"{_BRAND_A[h % len(_BRAND_A)]} {_BRAND_B[(h >> 5) % len(_BRAND_B)]}"


def parse_sponsor_list(text: str):
    """'Brand A, Brand B' -> ['Brand A', 'Brand B']."""
    return [s.strip() for s in re.split(r"[,/|]", str(text or "")) if s.strip()][:4]


def parse_driver_sponsors(text: str):
    """'Okafor: Brand A, Brand B; Lewis Hamilton = Brand C' (or one per line) -> {'okafor': [...], ...}."""
    out = {}
    for chunk in re.split(r"[;\n]", str(text or "")):
        if ":" in chunk:
            who, what = chunk.split(":", 1)
        elif "=" in chunk:
            who, what = chunk.split("=", 1)
        else:
            continue
        who = " ".join(who.split()).lower()
        lst = parse_sponsor_list(what)
        if who and lst:
            out[who] = lst
    return out


class Sponsors:
    """Who is backed by whom, worked out from the settings each time they're asked for (they can change mid-race)."""

    def __init__(self, cfg_fn):
        self.cfg_fn = cfg_fn

    def title(self, track=""):
        cfg = self.cfg_fn()
        t = " ".join(str(cfg.get("race_sponsor") or "").split())
        if t:
            return t
        return made_up_title(track) if cfg.get("invent_sponsors", True) else ""

    def of(self, car):
        """The sponsors on a car (a race.Car), [] if none."""
        if car is None:
            return []
        cfg = self.cfg_fn()
        if car.player:
            mine = parse_sponsor_list(cfg.get("my_sponsors"))
            if mine:
                return mine
        table = parse_driver_sponsors(cfg.get("driver_sponsors"))
        name = " ".join(str(car.name or "").split()).lower()
        for key in (name, (car.call or "").lower(), (cfg.get("driver_name") or "").strip().lower() if car.player else None):
            if key and key in table:
                return table[key]
        if cfg.get("invent_sponsors", True):
            return made_up_sponsors(car.name)
        return []


def car_make(car_name: str) -> str:
    """'Porsche 911 GT3 R (992)' -> 'Porsche', for 'the Porsche of Okafor'."""
    w = str(car_name or "").split()
    if not w or w[0].lower() in ("car", "iracing", "f1", "formula", "class", "hypercar") or \
            re.fullmatch(r"(?i)(gt|lmp|lmh|lmdh|gte|tcr|f|fr)\d*\w?", w[0]):
        return ""                                     # a class, not a make: 'GT3', 'LMP2', 'Formula 3'
    if len(w) > 1 and w[0].lower() in ("aston", "alfa", "land", "mercedes-amg", "rolls"):
        return " ".join(w[:2]) if w[0].lower() != "mercedes-amg" else "Mercedes"
    return w[0]
