"""Energy conversion factor sets: published multipliers from billed units to energy (provisional).

A *factor set* is one JSON file in this package, transcribed from a published source and pinned
to it (URL, edition, retrieval date and the source file's sha256). The first set is
``energy_star_thermal_2015``: the ENERGY STAR Portfolio Manager technical reference "Thermal Energy
Conversions" (EPA, August 2015), Figures 2 and 3, with U.S. and Canadian columns. See
docs/UNITS.md ("Energy conversion factors") and docs/ENERGY-FACTORS.md for the full table.

Every ``*.json`` file here is loaded and validated (:func:`validate_factor_set`): the schema, the
unit keys, and, where an entry states a heat content, that its multiplier equals heat content x
unit size to the printed precision. An entry the source itself prints inconsistently must say so
(``"consistency": {"status": "source_discrepancy", ...}``) and is transcribed as printed; using it
raises an :class:`EnergyFactorWarning`. A new set, or a new edition, is a new file: no code
changes (see ``scripts/energy_factors_refresh.py``).

**The "M" problem.** ENERGY STAR writes M for *million* (``Mcf`` = million cubic feet, ``MBtu`` =
million Btu) and k/K for thousand (``Kcf``); many US gas utilities write ``Mcf`` for a *thousand*
cubic feet. :mod:`camber.energy_units` reads a bare ``Mcf`` as a thousand cubic feet and refuses
``MBtu`` and ``Mlb``; this module keeps that reading for bare strings, so a bare ``Mcf`` here is the
set's ``kcf`` entry, **with a caveat** naming the conflict. The unambiguous labels ``kcf``,
``MMcf`` / ``million cf``, ``MMBtu``, ``klb`` and ``MMlb`` / ``million lb`` are always accepted.

``to_kbtu(120, "kcf", "natural_gas", factor_set="energy_star_thermal_2015", region="US")`` is
123,120 kBtu. These are the set's own (often rounded) multipliers: ENERGY STAR converts a kWh at
3.412 kBtu, where :mod:`camber.energy_units` uses the exact 3.412142.

Provisional (0.92): names and signatures may change in a minor release.
"""

from __future__ import annotations

import json
import math
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

__all__ = [
    "SCHEMA",
    "UNIT_KEYS",
    "EnergyFactorWarning",
    "FactorEntry",
    "FactorSet",
    "Conversion",
    "factor_sets",
    "get_factor_set",
    "load_factor_set",
    "validate_factor_set",
    "resolve_unit",
    "resolve_meter_type",
    "factor_for",
    "to_kbtu",
    "reference_markdown",
]

#: The schema tag every factor-set file carries.
SCHEMA = "camber.energy_factors/1"

_LB_KG = 0.45359237  # exact
_FT3_M3 = 0.3048**3  # exact
_GAL_US_FT3 = 231.0 / 1728.0  # 231 in3, exact
_L_FT3 = 1.0e-3 / _FT3_M3
_GAL_UK_FT3 = 4.54609 * _L_FT3  # 4.54609 L, exact

#: The unit vocabulary factor sets may use: key -> (kind, size in the kind's base unit). Energy
#: units carry no size (their multiplier is the set's own); volumes are in ft3, masses in lb.
UNIT_KEYS: dict = {
    "kBtu": ("energy", None),
    "MMBtu": ("energy", None),
    "kWh": ("energy", None),
    "MWh": ("energy", None),
    "GJ": ("energy", None),
    "therm": ("energy", None),
    "ton-hour": ("energy", None),
    "ft3": ("volume", 1.0),
    "CCF": ("volume", 100.0),
    "kcf": ("volume", 1.0e3),
    "MMcf": ("volume", 1.0e6),
    "m3": ("volume", 1.0 / _FT3_M3),
    "gal_US": ("volume", _GAL_US_FT3),
    "gal_UK": ("volume", _GAL_UK_FT3),
    "L": ("volume", _L_FT3),
    "lb": ("mass", 1.0),
    "klb": ("mass", 1.0e3),
    "MMlb": ("mass", 1.0e6),
    "kg": ("mass", 1.0 / _LB_KG),
    "short_ton": ("mass", 2000.0),
    "tonne": ("mass", 1000.0 / _LB_KG),
}
# energy per heat-content numerator, in kBtu
_HC_ENERGY_KBTU = {"Btu": 1.0e-3, "kBtu": 1.0, "MMBtu": 1.0e3}

# Unambiguous spellings a factor set accepts beyond camber.energy_units' parser (normalised).
_SET_ALIASES = {
    "kcf": "kcf",
    "thousandcf": "kcf",
    "thousandcubicfeet": "kcf",
    "thousandcubicfoot": "kcf",
    "mmcf": "MMcf",
    "millioncf": "MMcf",
    "millioncubicfeet": "MMcf",
    "millioncubicfoot": "MMcf",
    "mmlb": "MMlb",
    "mmlbs": "MMlb",
    "millionlb": "MMlb",
    "millionlbs": "MMlb",
    "millionpounds": "MMlb",
    "gal": "gal_US",
    "gals": "gal_US",
    "gallon": "gal_US",
    "gallons": "gal_US",
    "galus": "gal_US",
    "usgal": "gal_US",
    "usgallon": "gal_US",
    "usgallons": "gal_US",
    "gallonsus": "gal_US",
    "galuk": "gal_UK",
    "ukgal": "gal_UK",
    "ukgallon": "gal_UK",
    "ukgallons": "gal_UK",
    "gallonsuk": "gal_UK",
    "imperialgallon": "gal_UK",
    "imperialgallons": "gal_UK",
    "l": "L",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
    "ton": "short_ton",
    "tons": "short_ton",
    "shortton": "short_ton",
    "shorttons": "short_ton",
    "tonne": "tonne",
    "tonnes": "tonne",
    "metricton": "tonne",
    "metrictons": "tonne",
    "tonnesmetric": "tonne",
}
# camber.energy_units canonical names -> factor-set unit keys (its "Mcf" is a thousand cf)
_EU_TO_KEY = {
    "kWh": "kWh",
    "MWh": "MWh",
    "kBtu": "kBtu",
    "MMBtu": "MMBtu",
    "therm": "therm",
    "ton-hour": "ton-hour",
    "GJ": "GJ",
    "ft3": "ft3",
    "CCF": "CCF",
    "Mcf": "kcf",
    "m3": "m3",
    "lb": "lb",
    "klb": "klb",
    "kg": "kg",
}
_BARE_M = {"mcf", "mscf"}  # a bare M-volume: thousand here (camber.energy_units), million in ES


class EnergyFactorWarning(UserWarning):
    """A conversion rests on a caveat: a bare ``Mcf`` (thousand or million?), or a multiplier the
    source prints inconsistently with its own heat content."""


@dataclass(frozen=True)
class FactorEntry:
    """One row of a factor set: ``multiplier`` output units (kBtu) per one ``unit_key``."""

    meter_type: str
    region: str
    input_unit: str
    unit_key: str
    multiplier: float
    multiplier_text: str
    heat_content: dict | None = None
    footnotes: tuple = ()
    note: str | None = None
    consistency: dict | None = None

    @property
    def discrepancy(self) -> str | None:
        """The source-discrepancy note, when the source prints this row inconsistently."""
        c = self.consistency or {}
        return c.get("note") if c.get("status") == "source_discrepancy" else None


@dataclass(frozen=True)
class FactorSet:
    """A validated factor set (see :func:`get_factor_set`)."""

    name: str
    kind: str
    output_unit: str
    source: dict
    conventions: dict
    regions: dict
    quick_reference: tuple
    meter_types: dict
    entries: tuple
    _index: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def title(self) -> str:
        """The source's title."""
        return self.source["title"]

    def citation(self) -> str:
        """``title (publisher, edition); url; retrieved <date>, sha256 <first 12>``."""
        s = self.source
        return (
            f"{s['title']} ({s['publisher']}, {s['edition']}); {s['url']}; retrieved "
            f"{s['retrieved']}, sha256 {s['sha256'][:12]}"
        )

    def entry(self, meter_type: str, region: str, unit_key: str) -> FactorEntry:
        """The entry for ``(meter_type, region, unit_key)``; ``KeyError`` when there is none."""
        return self._index[(meter_type, region, unit_key)]

    def units(self, meter_type: str, region: str) -> list:
        """The unit keys this set lists for a meter type in a region, in source order."""
        return [
            e.unit_key for e in self.entries if e.meter_type == meter_type and e.region == region
        ]


@dataclass(frozen=True)
class Conversion:
    """A resolved factor: ``multiplier`` (``output_unit`` per one input unit) with where it came
    from and any caveats (see :func:`factor_for`)."""

    factor_set: str
    region: str
    meter_type: str
    unit: str
    entry: FactorEntry
    caveats: tuple = ()

    @property
    def multiplier(self) -> float:
        """The set's multiplier to its output unit (kBtu)."""
        return float(self.entry.multiplier)

    def describe(self) -> str:
        """One line of provenance, e.g. ``ENERGY STAR ... (US): 1 Kcf ... = 1,026 kBtu``."""
        fs = get_factor_set(self.factor_set)
        hc = self.entry.heat_content
        hc_txt = f", heat content {hc['text']}" if hc else ""
        return (
            f"{self.factor_set} ({fs.source['publisher']}, {fs.source['edition']}), "
            f"{self.region}, {fs.meter_types[self.meter_type]['name']}: 1 "
            f"{self.entry.input_unit} = {self.entry.multiplier_text} {fs.output_unit}{hc_txt}"
        )

    def as_dict(self) -> dict:
        """A plain-dict record of the factor used (for findings and reports)."""
        e = self.entry
        return {
            "factor_set": self.factor_set,
            "region": self.region,
            "meter_type": self.meter_type,
            "input_unit": e.input_unit,
            "unit_key": e.unit_key,
            "multiplier": self.multiplier,
            "heat_content": e.heat_content["text"] if e.heat_content else None,
            "footnotes": list(e.footnotes),
        }


# --------------------------------------------------------------------------- validation


def _decimals(text: str) -> int:
    t = text.replace(",", "")
    return len(t.split(".", 1)[1]) if "." in t else 0


def _half_ulp(text: str) -> float:
    return 0.5 * 10.0 ** (-_decimals(text))


def _num(text) -> float:
    return float(str(text).replace(",", ""))


_TEXT_NUM = re.compile(r"^[0-9]{1,3}(,[0-9]{3})*(\.[0-9]+)?$|^[0-9]+(\.[0-9]+)?$")


def _expected(e: Mapping) -> tuple | None:
    """``(expected multiplier, tolerance)`` from an entry's heat content, or ``None``."""
    hc = e.get("heat_content")
    if not hc:
        return None
    num, den = hc["unit_key"].split("/", 1)
    kind_in, size_in = UNIT_KEYS[e["unit_key"]]
    kind_hc, size_hc = UNIT_KEYS[den]
    per = size_in / size_hc  # heat-content denominators per one input unit
    k = _HC_ENERGY_KBTU[num]
    exp = float(hc["value"]) * k * per
    # the multiplier and the heat content are each rounded to their printed digits
    tol = _half_ulp(e["multiplier_text"]) + _half_ulp(hc["value_text"]) * k * per
    return exp, tol + 1e-9 * abs(exp)


def validate_factor_set(doc: Mapping) -> list:
    """Every problem with a factor-set document (an empty list when it is valid).

    Checks the schema tag, the source block (URL, edition, retrieval date, a 64-hex sha256,
    terms, footnotes), the regions and meter types, each entry's keys, unit key, printed
    multiplier text (it must equal the number), footnote references and uniqueness, the
    quick-reference rows against the entries, and -- where an entry states a heat content --
    that the multiplier equals heat content x unit size within the printed precision. A row the
    source prints inconsistently must carry ``consistency.status == "source_discrepancy"``; a row
    marked so that is in fact consistent is also a problem (a stale flag).
    """
    p: list = []
    if not isinstance(doc, Mapping):
        return ["a factor set must be a JSON object"]
    if doc.get("schema") != SCHEMA:
        p.append(f"schema must be {SCHEMA!r}, got {doc.get('schema')!r}")
    for k in ("name", "kind", "output_unit"):
        if not isinstance(doc.get(k), str) or not doc.get(k):
            p.append(f"{k!r} must be a non-empty string")
    if doc.get("kind") == "energy_conversion" and doc.get("output_unit") != "kBtu":
        p.append("an energy_conversion set converts to kBtu (output_unit)")
    src = doc.get("source")
    if not isinstance(src, Mapping):
        return p + ["'source' must be an object"]
    for k in ("title", "publisher", "url", "edition", "retrieved", "sha256", "terms"):
        if not isinstance(src.get(k), str) or not src.get(k):
            p.append(f"source.{k} must be a non-empty string")
    if not re.fullmatch(r"[0-9a-f]{64}", str(src.get("sha256", ""))):
        p.append("source.sha256 must be 64 lowercase hex digits")
    if not str(src.get("url", "")).startswith("https://"):
        p.append("source.url must be https")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(src.get("retrieved", ""))):
        p.append("source.retrieved must be YYYY-MM-DD")
    if not re.fullmatch(r"\d{4}(-\d{2}){0,2}", str(src.get("edition", ""))):
        p.append("source.edition must be YYYY, YYYY-MM or YYYY-MM-DD")
    notes = src.get("footnotes") or {}
    if not isinstance(notes, Mapping):
        p.append("source.footnotes must be an object")
        notes = {}
    conv = doc.get("conventions")
    if not isinstance(conv, Mapping) or conv.get("M") not in ("thousand", "million"):
        p.append("conventions.M must be 'thousand' or 'million'")
    regions = doc.get("regions")
    if not isinstance(regions, Mapping) or not regions:
        return p + ["'regions' must be a non-empty object"]
    mts = doc.get("meter_types")
    if not isinstance(mts, list) or not mts:
        return p + ["'meter_types' must be a non-empty list"]
    names: dict = {}
    mkeys = set()
    for mt in mts:
        key = mt.get("key") if isinstance(mt, Mapping) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]+", key):
            p.append(f"meter type key {key!r} must be lower_snake_case")
            continue
        mkeys.add(key)
        for alias in [key, _mt_norm(mt.get("name", "")), *(mt.get("aliases") or [])]:
            a = _mt_norm(alias)
            if a in names and names[a] != key:
                p.append(f"meter type alias {alias!r} names both {names[a]} and {key}")
            names[a] = key
        for fn in mt.get("footnotes") or []:
            if fn not in notes:
                p.append(f"{key}: unknown footnote {fn!r}")
        hcs = mt.get("heat_content") or {}
        for region in regions:
            if region not in hcs:
                p.append(f"{key}: no heat_content entry for region {region}")
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        return p + ["'entries' must be a non-empty list"]
    seen = set()
    need = {"meter_type", "region", "input_unit", "unit_key", "multiplier", "multiplier_text"}
    for i, e in enumerate(entries):
        where = f"entries[{i}]"
        if not isinstance(e, Mapping) or need - set(e):
            p.append(f"{where}: needs {sorted(need)}")
            continue
        where = f"{e['meter_type']}/{e['region']}/{e['unit_key']}"
        if e["meter_type"] not in mkeys:
            p.append(f"{where}: unknown meter type")
        if e["region"] not in regions:
            p.append(f"{where}: unknown region")
        if e["unit_key"] not in UNIT_KEYS:
            p.append(f"{where}: unknown unit key (see UNIT_KEYS)")
            continue
        ident = (e["meter_type"], e["region"], e["unit_key"])
        if ident in seen:
            p.append(f"{where}: duplicate entry")
        seen.add(ident)
        mt_text = str(e["multiplier_text"])
        m = e["multiplier"]
        if not _TEXT_NUM.match(mt_text):
            p.append(f"{where}: multiplier_text {mt_text!r} is not a printed number")
        elif isinstance(m, bool) or not isinstance(m, (int, float)) or _num(mt_text) != m:
            p.append(f"{where}: multiplier {m!r} does not equal its text {mt_text!r}")
        elif not (math.isfinite(m) and m > 0):
            p.append(f"{where}: multiplier must be positive")
        for fn in e.get("footnotes") or []:
            if fn not in notes:
                p.append(f"{where}: unknown footnote {fn!r}")
        hc = e.get("heat_content")
        kind = UNIT_KEYS[e["unit_key"]][0]
        if hc is not None:
            if kind == "energy":
                p.append(f"{where}: an energy unit takes no heat content")
                continue
            ok = isinstance(hc, Mapping) and {"value", "value_text", "unit_key"} <= set(hc)
            num_den = str(hc.get("unit_key", "")).split("/", 1) if ok else []
            if (
                not ok
                or len(num_den) != 2
                or num_den[0] not in _HC_ENERGY_KBTU
                or num_den[1] not in UNIT_KEYS
                or UNIT_KEYS[num_den[1]][0] != kind
                or _num(hc["value_text"]) != hc["value"]
            ):
                p.append(
                    f"{where}: heat_content must be {{value, value_text, unit_key}} with "
                    f"unit_key '<Btu|kBtu|MMBtu>/<{kind} unit key>' matching the value"
                )
                continue
        c = e.get("consistency")
        flagged = isinstance(c, Mapping) and c.get("status") == "source_discrepancy"
        if c is not None and not (flagged and isinstance(c.get("note"), str) and c["note"]):
            p.append(f"{where}: consistency must be {{status: source_discrepancy, note}}")
        ex = _expected(e) if isinstance(hc, Mapping) else None
        if ex is not None and isinstance(hc, Mapping):
            exp, tol = ex
            off = abs(float(m) - exp) > tol
            if off and not flagged:
                p.append(
                    f"{where}: multiplier {mt_text} disagrees with heat content "
                    f"{hc['value_text']} {hc['unit_key']} (expected {exp:.6g} +/- {tol:.3g}); "
                    "fix the transcription or mark it a source_discrepancy"
                )
            if flagged and not off:
                p.append(f"{where}: marked a source_discrepancy but is consistent")
    qr = doc.get("quick_reference") or []
    for q in qr:
        key = q.get("unit_key") if isinstance(q, Mapping) else None
        if key not in UNIT_KEYS or UNIT_KEYS[key][0] != "energy":
            p.append(f"quick_reference: {key!r} is not an energy unit key")
            continue
        if _num(q.get("kbtu_text", "nan")) != q.get("kbtu"):
            p.append(f"quick_reference {key}: kbtu does not equal its text")
        for e in entries:
            if (
                isinstance(e, Mapping)
                and e.get("unit_key") == key
                and e.get("multiplier") != q["kbtu"]
            ):
                p.append(
                    f"{e['meter_type']}/{e['region']}/{key}: {e['multiplier']} differs from "
                    f"the quick reference {q['kbtu']}"
                )
    return p


def _mt_norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def load_factor_set(doc: Mapping) -> FactorSet:
    """A :class:`FactorSet` from a parsed JSON document; ``ValueError`` listing every problem."""
    problems = validate_factor_set(doc)
    if problems:
        raise ValueError(
            f"factor set {doc.get('name') if isinstance(doc, Mapping) else '?'!r} is invalid:\n  "
            + "\n  ".join(problems)
        )
    entries = tuple(
        FactorEntry(
            meter_type=e["meter_type"],
            region=e["region"],
            input_unit=e["input_unit"],
            unit_key=e["unit_key"],
            multiplier=e["multiplier"],
            multiplier_text=e["multiplier_text"],
            heat_content=dict(e["heat_content"]) if e.get("heat_content") else None,
            footnotes=tuple(e.get("footnotes") or ()),
            note=e.get("note"),
            consistency=dict(e["consistency"]) if e.get("consistency") else None,
        )
        for e in doc["entries"]
    )
    fs = FactorSet(
        name=doc["name"],
        kind=doc["kind"],
        output_unit=doc["output_unit"],
        source=dict(doc["source"]),
        conventions=dict(doc["conventions"]),
        regions=dict(doc["regions"]),
        quick_reference=tuple(dict(q) for q in doc.get("quick_reference") or ()),
        meter_types={mt["key"]: dict(mt) for mt in doc["meter_types"]},
        entries=entries,
    )
    fs._index.update({(e.meter_type, e.region, e.unit_key): e for e in entries})
    return fs


@lru_cache(maxsize=1)
def _registry() -> dict:
    out: dict = {}
    for res in sorted(resources.files(__name__).iterdir(), key=lambda r: r.name):
        if not res.name.endswith(".json"):
            continue
        doc = json.loads(res.read_text(encoding="utf-8"))
        fs = load_factor_set(doc)
        if fs.name != res.name[: -len(".json")]:
            raise ValueError(f"{res.name}: name {fs.name!r} must match the file name")
        out[fs.name] = fs
    return out


def factor_sets() -> list:
    """The names of the bundled factor sets, sorted."""
    return sorted(_registry())


def get_factor_set(name: str) -> FactorSet:
    """The bundled factor set ``name``; ``ValueError`` naming the known sets otherwise."""
    reg = _registry()
    if name not in reg:
        raise ValueError(f"unknown factor set {name!r}; known: {', '.join(sorted(reg))}")
    return reg[name]


# --------------------------------------------------------------------------- lookups


def resolve_meter_type(meter_type: str, *, factor_set: str) -> str:
    """The set's meter-type key for ``meter_type`` (its key, printed name or an alias)."""
    fs = get_factor_set(factor_set)
    want = _mt_norm(meter_type)
    for key, mt in fs.meter_types.items():
        if want in {_mt_norm(a) for a in [key, mt.get("name", ""), *(mt.get("aliases") or [])]}:
            return key
    raise ValueError(
        f"unknown meter type {meter_type!r} in {factor_set}; known: {', '.join(fs.meter_types)}"
    )


def resolve_unit(text: str, *, factor_set: str | None = None) -> tuple:
    """``(unit key, caveats)`` for a unit string.

    Reads the unambiguous factor-set labels (``kcf``, ``MMcf`` / ``million cf``, ``klb``,
    ``MMlb``, ``gallons`` (US), ``UK gallons``, ``liters``, ``tons`` (short), ``tonnes``) and every
    unit :func:`camber.energy_units.parse_unit` reads. A bare ``Mcf`` keeps
    :mod:`camber.energy_units`' reading (a thousand cubic feet, ``kcf``) and returns a caveat
    naming the conflict with a set that writes M for million; ``MBtu`` and ``Mlb`` stay refused as
    ambiguous (``ValueError``).
    """
    from ..energy_units import _norm, parse_unit

    if text is None or not str(text).strip():
        raise ValueError("no unit given")
    key = _norm(text)
    if key in _SET_ALIASES:
        return _SET_ALIASES[key], ()
    u = parse_unit(text, kind=("energy", "volume", "mass"))  # refuses MBtu / Mlb (ambiguous)
    if u.name not in _EU_TO_KEY:
        raise ValueError(f"{text!r} ({u.name}) is not a unit factor sets list; convert it first")
    caveats: tuple = ()
    if key in _BARE_M:
        caveats = (_m_caveat(text, factor_set),)
    return _EU_TO_KEY[u.name], caveats


def _m_caveat(text: str, factor_set: str | None) -> str:
    base = (
        f"'{text}' read as a THOUSAND cubic feet (the US gas-utility convention, CAMBER's reading)"
    )
    if factor_set is not None:
        fs = get_factor_set(factor_set)
        if fs.conventions.get("M") == "million":
            return (
                f"{base}, but {factor_set} writes Mcf for a MILLION cubic feet (its thousand is "
                f"Kcf; note {fs.conventions.get('footnote', '?')} of the source): if the bill "
                "follows that convention this energy is 1,000x too low. Write 'kcf' or 'MMcf' to "
                "make the unit unambiguous."
            )
    return (
        f"{base}. Some references (ENERGY STAR Portfolio Manager among them) write Mcf for a "
        "MILLION cubic feet; if the bill does, this energy is 1,000x too low. Write 'kcf' or "
        "'MMcf' to make the unit unambiguous."
    )


def factor_for(unit: str, meter_type: str, *, factor_set: str, region: str) -> Conversion:
    """The :class:`Conversion` for one billed ``unit`` of ``meter_type`` in ``region``.

    ``ValueError`` when the set lists no such unit for that meter type (the message lists the
    units it does), for an unknown region, meter type or set, and for ambiguous units.
    """
    fs = get_factor_set(factor_set)
    if region not in fs.regions:
        raise ValueError(f"{factor_set} has no region {region!r}; known: {', '.join(fs.regions)}")
    mt = resolve_meter_type(meter_type, factor_set=factor_set)
    key, caveats = resolve_unit(unit, factor_set=factor_set)
    try:
        e = fs.entry(mt, region, key)
    except KeyError:
        listed = ", ".join(fs.units(mt, region))
        hint = " (did you mean ton-hour?)" if key == "short_ton" and "ton-hour" in listed else ""
        raise ValueError(
            f"{factor_set} lists no {key!r} for {mt} ({region}){hint}; it lists: {listed}"
        ) from None
    if e.discrepancy:
        caveats = caveats + (
            f"{factor_set} {mt}/{region}/{key}: the source's multiplier {e.multiplier_text} is not "
            f"consistent with its own heat content; used as printed. {e.discrepancy}",
        )
    return Conversion(
        factor_set=factor_set, region=region, meter_type=mt, unit=key, entry=e, caveats=caveats
    )


def to_kbtu(value, unit: str, meter_type: str, *, factor_set: str, region: str):
    """``value`` (a number or array) billed in ``unit`` of ``meter_type``, as kBtu, with the
    set's multiplier for ``region``. Each caveat (a bare ``Mcf``; a source discrepancy) is issued
    as an :class:`EnergyFactorWarning`; :func:`factor_for` returns them instead."""
    c = factor_for(unit, meter_type, factor_set=factor_set, region=region)
    fs = get_factor_set(factor_set)
    if fs.output_unit != "kBtu":
        raise ValueError(f"{factor_set} converts to {fs.output_unit}, not kBtu")
    for cav in c.caveats:
        warnings.warn(cav, EnergyFactorWarning, stacklevel=2)
    return value * c.multiplier


# --------------------------------------------------------------------------- docs


def reference_markdown(name: str) -> str:
    """The generated Markdown reference table of every entry in the set ``name`` (the body of
    docs/ENERGY-FACTORS.md between its markers; a test keeps the two in sync)."""
    fs = get_factor_set(name)
    regions = list(fs.regions)
    out = [
        f"### {fs.title}",
        "",
        f"`{fs.name}`: {fs.source['publisher']}, edition {fs.source['edition']}, retrieved "
        f"{fs.source['retrieved']}. Source: <{fs.source['url']}> (sha256 "
        f"`{fs.source['sha256']}`). Multipliers to {fs.output_unit}, as printed.",
        "",
    ]
    cols = " | ".join(f"{r} {fs.output_unit} per unit | {r} heat content" for r in regions)
    for key, mt in fs.meter_types.items():
        out += [f"#### {mt['name']} (`{key}`)", ""]
        out += [f"| Input unit (as printed) | Key | {cols} | Notes |"]
        out += ["|---|---|" + "---|---|" * len(regions) + "---|"]
        for ukey in fs.units(key, regions[0]):
            cells = []
            notes: list = []
            for r in regions:
                e = fs.entry(key, r, ukey)
                hc = e.heat_content["text"] if e.heat_content else ""
                flag = " (!)" if e.discrepancy else ""
                cells += [f"{e.multiplier_text}{flag}", hc]
                notes += [n for n in e.footnotes if n not in notes]
            first = fs.entry(key, regions[0], ukey)
            out.append(
                f"| {first.input_unit} | `{ukey}` | "
                + " | ".join(cells)
                + f" | {', '.join(sorted(notes))} |"
            )
        texts = {r: mt["heat_content"][r].get("text", "") for r in regions}
        out += ["", "Heat content: " + "; ".join(f"{r} {t}" for r, t in texts.items()) + "."]
        extra = sorted(
            {
                f"{e.region} `{e.unit_key}`: {e.discrepancy or e.note}"
                for e in fs.entries
                if e.meter_type == key and (e.discrepancy or e.note)
            }
        )
        if extra:
            out.append("")
            out += [f"- {x}" for x in extra]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
