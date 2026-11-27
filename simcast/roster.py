"""A league roster: one spreadsheet (CSV) with every driver's number, team, pronouns, sponsors and how to say
their name. What it says wins over every guess (pronouns from first names, made-up sponsors), and it gives the
booth 'the number 12 Porsche' and the team names.

Columns, in any order, any of these headings (case doesn't matter); only Driver is needed:
  Driver (or Name) | Number (No, #, Car) | Team | Pronouns (he / she / they) | Sponsors (separated by ; or /)
  | Say as (how the booth should say the name, e.g. 'Ver-STAP-pen' or a nickname)
Comma or semicolon separated, as Excel, Google Sheets or LibreOffice save it.
"""
from __future__ import annotations

import csv
import io
import os
import re

HEADINGS = {
    "name": ("driver", "name", "driver name", "full name"),
    "number": ("number", "no", "no.", "#", "car", "car number", "car no", "car no.", "car #", "num"),
    "team": ("team", "team name", "entrant"),
    "pronouns": ("pronouns", "pronoun", "gender"),
    "sponsors": ("sponsors", "sponsor", "backers"),
    "say": ("say as", "say", "call name", "call", "nickname", "pronunciation"),
}
TEMPLATE_HEAD = ["Driver", "Number", "Team", "Pronouns", "Sponsors", "Say as"]


def key(name):
    return " ".join(str(name or "").split()).lower()


def _col(heading):
    h = key(heading).strip(":")
    for k, alts in HEADINGS.items():
        if h in alts:
            return k
    return None


def _pronoun(text):
    t = key(text)
    if t.startswith(("he", "m")) and not t.startswith("her"):
        return "m"
    if t.startswith(("she", "her", "f", "w")):
        return "f"
    if t.startswith("they") or t in ("n", "nb", "x"):
        return "x"
    return None


def parse(text):
    """{driver key: entry} from the CSV text. An entry: name, number, team, gender ('m', 'f', 'x' or None),
    sponsors (list), say."""
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return {}
    cols = [_col(h) for h in rows[0]]
    if "name" not in cols:
        return {}
    for i, h in enumerate(rows[0]):
        # 'Car' is the number in some sheets and the car itself ('Porsche 911 GT3 R') in others: a number column
        # holds numbers, so one that's mostly words is the car, and another column can be the number
        if cols[i] == "number" and key(h).strip(":") == "car":
            vals = [r[i].strip() for r in rows[1:] if i < len(r) and r[i].strip()]
            if vals and sum(bool(re.fullmatch(r"#?\s*\d{1,4}[A-Za-z]?", v)) for v in vals) < len(vals) / 2:
                cols[i] = None
    if cols.count("number") > 1:                  # 'Car' and 'Number' both: the number is the one that says so
        cols = [None if c == "number" and key(h).strip(":") == "car" else c for c, h in zip(cols, rows[0])]
    out = {}
    for row in rows[1:]:
        d = {c: (row[i].strip() if i < len(row) else "") for i, c in enumerate(cols) if c}
        name = " ".join(d.get("name", "").split())
        if not name:
            continue
        num = re.sub(r"[^0-9A-Za-z]", "", d.get("number", ""))
        out[key(name)] = {
            "name": name, "number": num, "team": d.get("team", ""),
            "gender": _pronoun(d.get("pronouns", "")),
            "sponsors": [s.strip() for s in re.split(r"[;/|]", d.get("sponsors", "")) if s.strip()][:4],
            "say": d.get("say", ""),
        }
    return out


def read_text(path):
    """A spreadsheet saved as CSV: UTF-8 (with or without Excel's BOM), or Windows' own 'ANSI' (cp1252), which is
    what Excel's plain 'CSV' still saves, so 'Pérez' comes out as Pérez either way."""
    raw = open(path, "rb").read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


class Roster:
    """The roster file named in the settings, read again whenever it changes on disk."""

    def __init__(self, cfg_fn, log=print):
        self.cfg_fn, self.log = cfg_fn, log
        self._path, self._mtime, self._data = None, None, {}

    def entries(self):
        path = (self.cfg_fn().get("roster_file") or "").strip()
        if not path:
            return {}
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return {}
        if path != self._path or mt != self._mtime:
            try:
                self._data = parse(read_text(path))
            except Exception as e:
                self.log(f"Roster unreadable ({e})")
                self._data = {}
            self._path, self._mtime = path, mt
        return self._data

    def find(self, car_or_name):
        """The roster entry for a car (race.Car) or a name: by full name, then by surname."""
        data = self.entries()
        if not data:
            return None
        name = getattr(car_or_name, "name", car_or_name)
        k = key(name)
        if k in data:
            return data[k]
        call = key(getattr(car_or_name, "call", "") or (k.split()[-1] if k else ""))
        hits = [e for kk, e in data.items() if kk.split()[-1:] == [call]] if call else []
        return hits[0] if len(hits) == 1 else None


def write_template(path, names=()):
    """A roster to fill in: the headings, and the drivers on track now if there are any."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TEMPLATE_HEAD)
        for n in names or ["Your Name"]:
            w.writerow([n, "", "", "", "", ""])
    return path
