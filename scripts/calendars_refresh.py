"""Rebuild the bundled public-holiday calendars in camber/calendars/ (maintainer tool).

    python scripts/calendars_refresh.py --src DIR          # check: rebuild and diff, no writes
    python scripts/calendars_refresh.py --src DIR --write  # rewrite the JSON files

``DIR`` holds the downloaded sources (nothing is fetched here):

* ``opm.html`` -- the U.S. Office of Personnel Management's federal holidays page
  (https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/). The US calendar is
  built from 5 U.S.C. 6103 and Executive Order 11582 (a Saturday holiday is observed on the
  Friday before, a Sunday one on the Monday after) and must agree with every year OPM tabulates.
* ``boe_<year>.html`` -- the BOE resolution "por la que se publica la relación de fiestas laborales
  para el año <year>" (``https://www.boe.es/diario_boe/txt.php?id=<BOE id>``, ids in ``ES_SOURCES``)
  for the years whose annex is an HTML table; ``boe_<year>.txt`` -- ``pdftotext -layout`` of the
  annex PDF for the years published as a PDF table only (2016, 2017).

Norway needs no source file: its dates follow the statutes (Lov om helligdager og helligdagsfred
§ 2, LOV-1995-02-24-12; Lov om 1. og 17. mai som høgtidsdager § 1, LOV-1947-04-26-1).

Checklist: adding a year or a country
-------------------------------------
1. Download the publisher's document for the year; add its id, URL and any correction
   ("corrección de errores") to ``ES_SOURCES`` / ``ES_CORRECTIONS``.
2. Run without ``--write`` and read the diff: an existing year must not change unless the
   publisher corrected it.
3. ``--write``, then add the hand-checked dates to ``tests/test_calendars.py`` and note the change
   in the CHANGELOG. A new country is a new ``<cc>_*.json`` file with the same schema.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "camber", "calendars")
SCHEMA = "camber.calendars/1"
RETRIEVED = "2026-09-28"

# --------------------------------------------------------------------------- dates by rule


def easter(y: int) -> dt.date:
    """Gregorian Easter Sunday (the anonymous Gregorian computus, "Meeus/Jones/Butcher")."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(y, month, day + 1)


def _nth(y, m, wd, n):
    d = dt.date(y, m, 1)
    d += dt.timedelta((wd - d.weekday()) % 7)
    return d + dt.timedelta(7 * (n - 1))


def _last(y, m, wd):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(1)
    return d - dt.timedelta((d.weekday() - wd) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - dt.timedelta(1)
    if d.weekday() == 6:
        return d + dt.timedelta(1)
    return d


def us_federal(y: int) -> list:
    """The observed US federal holidays of ``y`` (5 U.S.C. 6103(a), (b); E.O. 11582)."""
    h = [
        (_observed(dt.date(y, 1, 1)), "New Year's Day"),
        (_nth(y, 1, 0, 3), "Birthday of Martin Luther King, Jr."),
        (_nth(y, 2, 0, 3), "Washington's Birthday"),
        (_last(y, 5, 0), "Memorial Day"),
    ]
    if y >= 2021:
        h.append((_observed(dt.date(y, 6, 19)), "Juneteenth National Independence Day"))
    h += [
        (_observed(dt.date(y, 7, 4)), "Independence Day"),
        (_nth(y, 9, 0, 1), "Labor Day"),
        (_nth(y, 10, 0, 2), "Columbus Day"),
        (_observed(dt.date(y, 11, 11)), "Veterans Day"),
        (_nth(y, 11, 3, 4), "Thanksgiving Day"),
        (_observed(dt.date(y, 12, 25)), "Christmas Day"),
    ]
    return h


def norway(y: int) -> list:
    """Norway's helligdager (LOV-1995-02-24-12 § 2, Sundays aside) and 1 and 17 May."""
    e = easter(y)
    days = [
        (dt.date(y, 1, 1), "Nyttårsdag"),
        (e - dt.timedelta(3), "Skjærtorsdag"),
        (e - dt.timedelta(2), "Langfredag"),
        (e, "Første påskedag"),
        (e + dt.timedelta(1), "Andre påskedag"),
        (dt.date(y, 5, 1), "Offentlig høytidsdag (1. mai)"),
        (dt.date(y, 5, 17), "Grunnlovsdag (17. mai)"),
        (e + dt.timedelta(39), "Kristi himmelfartsdag"),
        (e + dt.timedelta(49), "Første pinsedag"),
        (e + dt.timedelta(50), "Andre pinsedag"),
        (dt.date(y, 12, 25), "Første juledag"),
        (dt.date(y, 12, 26), "Andre juledag"),
    ]
    return sorted(days)


# --------------------------------------------------------------------------- OPM check


def opm_years(path: str) -> dict:
    """``{year: {iso date, ...}}`` of OPM's tables (Inauguration Day, DC area only, left out)."""
    s = open(path, encoding="utf-8").read()
    t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).replace("’", "'")
    out: dict = {}
    wd = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    table = r"(\d{4}) Holiday Schedule Date Holiday (.*?)(?=\d{4} Holiday Schedule|$)"
    row = (
        rf"(?:{wd}), (\w+) (\d{{1,2}})(, \d{{4}})? \**\s*([A-Z][A-Za-z'., ]+?)"
        rf"(?= \*|(?: (?:{wd}),)| Note| This|$)"
    )
    for m in re.finditer(table, t):
        year = int(m.group(1))
        for mon, day, prev, name in re.findall(row, m.group(2)):
            if "Inauguration" in name:
                continue
            new_year_eve = mon == "December" and int(day) == 31 and "New Year" in name
            y = year - 1 if (prev or new_year_eve) else year
            d = dt.datetime.strptime(f"{y} {mon} {day}", "%Y %B %d").date()
            out.setdefault(year, set()).add(d)
    return out


# --------------------------------------------------------------------------- BOE (Spain)

ES_CODES = [
    "AN",
    "AR",
    "AS",
    "IB",
    "CN",
    "CB",
    "CM",
    "CL",
    "CT",
    "VC",
    "EX",
    "GA",
    "MD",
    "MC",
    "NC",
    "PV",
    "RI",
    "CE",
    "ML",
]  # fmt: skip  (the BOE annex's column order)
ES_NAMES = ["Andalucía", "Aragón", "Asturias", "Illes Balears", "Canarias", "Cantabria",
            "Castilla-La Mancha", "Castilla y León", "Cataluña", "Comunitat Valenciana",
            "Extremadura", "Galicia", "Madrid", "Murcia", "Navarra", "País Vasco", "La Rioja",
            "Ciudad de Ceuta", "Ciudad de Melilla"]  # fmt: skip
ES_SOURCES = {
    2016: "BOE-A-2015-11348",
    2017: "BOE-A-2016-9244",
    2018: "BOE-A-2017-11639",
    2019: "BOE-A-2018-14369",
    2020: "BOE-A-2019-14552",
    2021: "BOE-A-2020-13343",
    2022: "BOE-A-2021-17113",
    2023: "BOE-A-2022-16755",
    2024: "BOE-A-2023-22014",
    2025: "BOE-A-2024-21316",
    2026: "BOE-A-2025-21667",
}
# corrections of the annex table (a correction of the resolution's text only is not listed)
ES_CORRECTIONS = {2018: [("BOE-A-2017-12209", "CN", "2018-12-08", "La Inmaculada Concepción")]}
_MONTH_NAMES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"
)
_MONTHS = {m: i + 1 for i, m in enumerate(_MONTH_NAMES.split())}


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
        if not self.depth:
            return
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = {"text": "", "colspan": int(dict(attrs).get("colspan") or 1)}

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        if self.depth and tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        if self.depth and tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().rstrip(".").replace(" – ", " - ")


def boe_html(path: str, year: int) -> dict:
    p = _Rows()
    p.feed(open(path, encoding="utf-8").read())
    out: dict = {c: {} for c in ES_CODES}
    month = None
    for r in p.rows:
        first = re.sub(r"\s+", " ", r[0]["text"]).strip() if r else ""
        if first.lower() in _MONTHS:
            month = _MONTHS[first.lower()]
            continue
        m = re.match(r"^(\d{1,2})\s+(.*)$", first)
        if not m or month is None:
            continue
        cells = r[1:]
        if len(cells) != len(ES_CODES):
            raise SystemExit(f"BOE {year}: row {first!r} has {len(cells)} community cells")
        day = dt.date(year, month, int(m.group(1))).isoformat()
        for code, c in zip(ES_CODES, cells):
            if c["text"].strip():
                out[code][day] = _clean(m.group(2))
    return out


def boe_pdf_text(path: str, year: int) -> dict:
    """The annex of a PDF-only year from ``pdftotext -layout``: a mark belongs to the column
    whose centre (from the first row marked in all 19 columns) is nearest, within 4 characters."""
    lines = open(path, encoding="utf-8").read().splitlines()
    marks_of = lambda ln: [(m.start() + len(m.group(0)) / 2) for m in re.finditer(r"\*+", ln)]  # noqa: E731
    centres = next(
        marks_of(ln) for ln in lines if len(marks_of(ln)) == 19 and re.match(r"^\s*\d", ln)
    )
    out: dict = {c: {} for c in ES_CODES}
    month, pending = None, None

    def put(mo, d, name, xs):
        day = dt.date(year, mo, d).isoformat()
        for x in xs:
            k = min(range(19), key=lambda i: abs(centres[i] - x))
            if abs(centres[k] - x) >= 4:
                raise SystemExit(f"BOE {year}: a mark on {day} sits between columns")
            out[ES_CODES[k]][day] = _clean(name)

    for ln in lines:
        s = ln.strip()
        if s.lower() in _MONTHS:
            month = _MONTHS[s.lower()]
            continue
        m = re.match(r"^\s*(\d{1,2})\s+(\S.*?)(\s{2,}|$)", ln)
        xs = marks_of(ln)
        if m and month:
            if not xs:
                pending = (month, int(m.group(1)), m.group(2))
                continue
            if len(xs) == 19 and abs(xs[0] - centres[0]) > 3:
                centres = xs  # a page break moved the table
            put(month, int(m.group(1)), m.group(2), xs)
            pending = None
        elif pending and xs and not re.match(r"^\s*\d", ln):
            put(*pending, xs)
            pending = None
    return out


# --------------------------------------------------------------------------- build


def build(src: str) -> dict:
    files = {}
    us_years = range(2011, 2031)
    opm = opm_years(os.path.join(src, "opm.html"))
    us = {}
    for y in us_years:
        got = {d for d, _ in us_federal(y)}
        if y in opm and opm[y] - got:  # OPM rows the page parse found must all be in the rules
            raise SystemExit(f"US {y}: OPM lists {sorted(opm[y] - got)} the rules do not give")
        us.update({d.isoformat(): n for d, n in us_federal(y) if d.year in us_years})
    files["us_federal.json"] = {
        "schema": SCHEMA,
        "country": "US",
        "title": "US federal holidays (observed dates)",
        "kind": "public",
        "coverage": [us_years[0], us_years[-1]],
        "note": "The day federal offices close: a holiday on a Saturday is observed on the Friday "
        "before, one on a Sunday on the Monday after (so New Year's Day 2022 is 2021-12-31). "
        "Juneteenth from 2021. Inauguration Day (Washington, DC area only) is not included. State "
        "and local holidays differ; add them with a CSV file.",
        "sources": [
            {
                "title": "5 U.S.C. 6103 (holidays); Executive Order 11582 (observance)",
                "url": "https://www.law.cornell.edu/uscode/text/5/6103",
            },
            {
                "title": "U.S. Office of Personnel Management, Federal Holidays (every year "
                "2011-2030 tabulated there agrees with these dates)",
                "url": "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/",
                "retrieved": RETRIEVED,
            },
        ],
        "terms": "Works of the U.S. Government, not subject to copyright (17 U.S.C. 105).",
        "dates": {"": us},
    }
    no_years = range(2000, 2041)
    files["no_public.json"] = {
        "schema": SCHEMA,
        "country": "NO",
        "title": "Norway: public holidays (helligdager) and 1 and 17 May",
        "kind": "public",
        "coverage": [no_years[0], no_years[-1]],
        "note": "Computed from the statutes (Easter by the Gregorian computus). Sundays other than "
        "Easter and Whit Sunday are not listed. School holidays are set by each municipality "
        "(skolerute) and are not included; add them with a CSV file.",
        "sources": [
            {
                "title": "Lov om helligdager og helligdagsfred (LOV-1995-02-24-12) § 2",
                "url": "https://lovdata.no/dokument/NL/lov/1995-02-24-12",
                "retrieved": RETRIEVED,
            },
            {
                "title": "Lov om 1. og 17. mai som høgtidsdager (LOV-1947-04-26-1) § 1",
                "url": "https://lovdata.no/dokument/NL/lov/1947-04-26-1",
                "retrieved": RETRIEVED,
            },
        ],
        "terms": "Norwegian statutes are not protected by copyright (åndsverkloven § 14).",
        "dates": {"": {d.isoformat(): n for y in no_years for d, n in norway(y)}},
    }
    es: dict = {c: {} for c in ES_CODES}
    sources = []
    for y, boe_id in sorted(ES_SOURCES.items()):
        h, t = os.path.join(src, f"boe_{y}.html"), os.path.join(src, f"boe_{y}.txt")
        got = boe_pdf_text(t, y) if os.path.exists(t) else boe_html(h, y)
        for _cid, code, day, name in ES_CORRECTIONS.get(y, []):
            got[code][day] = name
        for c in ES_CODES:
            es[c].update(got[c])
        row = {
            "year": y,
            "title": f"Resolución de la Dirección General (de Empleo / de Trabajo) por la que se "
            f"publica la relación de fiestas laborales para el año {y}",
            "id": boe_id,
            "url": f"https://www.boe.es/buscar/doc.php?id={boe_id}",
            "retrieved": RETRIEVED,
        }
        if os.path.exists(t):
            row["note"] = "annex published as a PDF table; read with pdftotext -layout"
        if y in ES_CORRECTIONS:
            row["corrections"] = sorted({c[0] for c in ES_CORRECTIONS[y]})
        sources.append(row)
    files["es_public.json"] = {
        "schema": SCHEMA,
        "country": "ES",
        "title": "Spain: labour holidays (fiestas laborales) by autonomous community",
        "kind": "public",
        "coverage": [min(ES_SOURCES), max(ES_SOURCES)],
        "requires_subdivision": True,
        "subdivisions": dict(zip(ES_CODES, ES_NAMES)),
        "note": "The national, substituted and community holidays each community observes, as "
        "the BOE publishes them every year (codes are ISO 3166-2:ES). The two local holidays of "
        "each municipality are not included; add them with a CSV file.",
        "sources": sources,
        "terms": "BOE content may be reused with attribution to the Agencia Estatal Boletín "
        "Oficial del Estado (https://www.boe.es/informacion/aviso_legal/).",
        "dates": {c: dict(sorted(v.items())) for c, v in es.items()},
    }
    for doc in files.values():
        doc["retrieved"] = RETRIEVED
        doc["dates"] = {k: dict(sorted(v.items())) for k, v in doc["dates"].items()}
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", required=True, help="folder with the downloaded sources")
    ap.add_argument("--write", action="store_true", help="rewrite camber/calendars/*.json")
    a = ap.parse_args(argv)
    changed = 0
    for name, doc in build(a.src).items():
        path = os.path.join(OUT, name)
        text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != text:
            changed += 1
            print(f"{name}: {'new' if old is None else 'changed'}")
            if a.write:
                open(path, "w", encoding="utf-8").write(text)
    print("up to date" if not changed else ("written" if a.write else "not written (--write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
