"""The open-fdd -> CAMBER role crosswalk (``crosswalk.json``) and its unit conversions.

The crosswalk is a versioned data file, not code: one row per open-fdd point name (the Haystack
name a package map uses, with the SQL role open-fdd's historian stores it under) and the CAMBER
role it becomes, or ``null`` with a reason. Lookups accept either spelling. Nothing outside the
file is mapped: a name that is not in it is reported as unmapped, never guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files

import pandas as pd

from ...model.equipclass import equip_family
from ...model.roles import Role

# quantity -> {canonical unit: (scale, offset)} into CAMBER's IP unit for that quantity.
# value_ip = value * scale + offset. The first entry of each table is the IP unit itself.
_CONVERSIONS: dict = {
    "temp": {"degF": (1.0, 0.0), "degC": (9.0 / 5.0, 32.0), "K": (9.0 / 5.0, -459.67)},
    "air_flow": {
        "cfm": (1.0, 0.0),
        "L/s": (2.1188799727597, 0.0),
        "m3/h": (0.58857777021102, 0.0),
        "m3/s": (2118.8799727597, 0.0),
    },
    "water_flow": {
        "gpm": (1.0, 0.0),
        "L/s": (15.850323141489, 0.0),
        "m3/h": (4.4028675393, 0.0),
    },
    "air_pressure": {
        "inH2O": (1.0, 0.0),
        "Pa": (1.0 / 249.08891, 0.0),
        "kPa": (1000.0 / 249.08891, 0.0),
    },
    "water_pressure": {
        "psi": (1.0, 0.0),
        "kPa": (0.14503773773, 0.0),
        "Pa": (0.00014503773773, 0.0),
        "ftH2O": (0.43352750192, 0.0),
    },
    "power": {"kW": (1.0, 0.0), "W": (0.001, 0.0)},
    "percent": {"%": (1.0, 0.0), "fraction": (1.0, 0.0)},
    "binary": {"": (1.0, 0.0)},
    "ppm": {"ppm": (1.0, 0.0)},
    "count": {"": (1.0, 0.0)},
    "energy": {"kWh": (1.0, 0.0)},
    "current": {"A": (1.0, 0.0)},
}
# the unit a column of each quantity is assumed to carry when its package declares none
_DEFAULT_UNIT = {
    "ip": {
        "temp": "degF",
        "air_flow": "cfm",
        "water_flow": "gpm",
        "air_pressure": "inH2O",
        "water_pressure": "psi",
    },
    "si": {
        "temp": "degC",
        "air_flow": "L/s",
        "water_flow": "L/s",
        "air_pressure": "Pa",
        "water_pressure": "kPa",
    },
}
_UNIT_ALIASES = {
    "degf": "degF",
    "°f": "degF",
    "f": "degF",
    "deg f": "degF",
    "fahrenheit": "degF",
    "degc": "degC",
    "°c": "degC",
    "c": "degC",
    "deg c": "degC",
    "celsius": "degC",
    "k": "K",
    "kelvin": "K",
    "cfm": "cfm",
    "ft3/min": "cfm",
    "l/s": "L/s",
    "lps": "L/s",
    "m3/h": "m3/h",
    "m³/h": "m3/h",
    "m3/s": "m3/s",
    "m³/s": "m3/s",
    "gpm": "gpm",
    "gal/min": "gpm",
    "inh2o": "inH2O",
    "in/wc": "inH2O",
    "inwc": "inH2O",
    "in wc": "inH2O",
    "in_h2o": "inH2O",
    '"wc': "inH2O",
    "pa": "Pa",
    "kpa": "kPa",
    "psi": "psi",
    "psid": "psi",
    "ftwc": "ftH2O",
    "fth2o": "ftH2O",
    "ft h2o": "ftH2O",
    "kw": "kW",
    "w": "W",
    "%": "%",
    "percent": "%",
    "pct": "%",
    "fraction": "fraction",
    "ppm": "ppm",
    "kwh": "kWh",
    "a": "A",
    "amps": "A",
}
# declared-unit strings that carry no information (left to the unit system)
_BLANK_UNITS = frozenset({"", "-", "none", "n/a", "na", "unitless", "units", "nan"})

_KEY = re.compile(r"[^a-z0-9]")


def _norm_key(name) -> str:
    """Equipment types compare without case or punctuation (``heatPump`` = ``HEAT_PUMP``)."""
    return _KEY.sub("", str(name).lower())


def _name_key(name) -> str:
    """Point names compare exactly, ignoring only case and surrounding space: ``discharge-air-temp``
    and ``sat`` are open-fdd names, ``discharge_air_temp`` is not."""
    return str(name).strip().lower()


@dataclass(frozen=True)
class CrosswalkRow:
    """One crosswalk row: an open-fdd point name and the CAMBER role it becomes (or why not)."""

    haystack: str
    sql_role: str
    camber_role: str | None
    quantity: str
    equip_types: tuple = ()
    note: str = ""
    reason: str = ""

    @property
    def role(self) -> Role | None:
        """The CAMBER :class:`~camber.model.roles.Role`, or ``None`` for a non-mapping."""
        return Role(self.camber_role) if self.camber_role else None

    def applies_to(self, equip_type: str | None) -> bool:
        """True if the row is not limited to equipment types, or ``equip_type`` is one of them."""
        return not self.equip_types or (equip_type or "") in self.equip_types


@dataclass(frozen=True)
class Crosswalk:
    """The loaded crosswalk file: its version, the pinned docs commit, rows and equipment types."""

    version: int
    docs_commit: str
    rows: tuple
    equip_types: dict = field(default_factory=dict)
    sources: tuple = ()

    def lookup(self, name) -> CrosswalkRow | None:
        """The row for an open-fdd Haystack name or SQL role (exact, ignoring case only)."""
        return _index(self).get(_name_key(name))

    def equip_class(self, stamp) -> str | None:
        """The CAMBER equipment class for an open-fdd ``equipType`` stamp; ``None`` if unknown.

        Looks the stamp up in the crosswalk's ``equip_types`` table (ignoring case and
        punctuation); a stamp CAMBER's own class families recognise (``"AHU"``, ``"HEAT_PUMP"``)
        is kept as written, upper-cased. Anything else is unknown -- never inferred from an id.
        """
        if stamp is None or not str(stamp).strip():
            return None
        key = _norm_key(stamp)
        for k, v in self.equip_types.items():
            if _norm_key(k) == key:
                return v
        if equip_family(str(stamp)) is not None:
            return re.sub(r"[^A-Z0-9]+", "_", str(stamp).upper()).strip("_")
        return None

    def openfdd_type(self, stamp) -> str | None:
        """The canonical open-fdd ``equipType`` spelling of ``stamp`` (``None`` if not listed)."""
        if stamp is None:
            return None
        key = _norm_key(stamp)
        return next((k for k in self.equip_types if _norm_key(k) == key), None)

    def table(self) -> list:
        """The rows as plain dicts (for printing and JSON)."""
        return [
            {
                "haystack": r.haystack,
                "sql_role": r.sql_role,
                "camber_role": r.camber_role,
                "quantity": r.quantity,
                "equip_types": list(r.equip_types),
                "note": r.note or r.reason,
            }
            for r in self.rows
        ]


@lru_cache(maxsize=4)
def _index_cached(rows: tuple) -> dict:
    out: dict = {}
    for r in rows:  # first row wins for a repeated name (elec-power has two SQL spellings)
        out.setdefault(_name_key(r.haystack), r)
        out.setdefault(_name_key(r.sql_role), r)
    return out


def _index(cw: Crosswalk) -> dict:
    return _index_cached(cw.rows)


def _parse(doc: dict) -> Crosswalk:
    rows = []
    for e in doc.get("roles") or []:
        if e.get("camber_role") is not None:
            Role(e["camber_role"])  # ValueError on a CAMBER role that does not exist
        if e["quantity"] not in _CONVERSIONS:
            raise ValueError(f"crosswalk row {e['haystack']!r}: unknown quantity {e['quantity']!r}")
        rows.append(
            CrosswalkRow(
                haystack=e["haystack"],
                sql_role=e["sql_role"],
                camber_role=e.get("camber_role"),
                quantity=e["quantity"],
                equip_types=tuple(e.get("equip_types") or ()),
                note=e.get("note", ""),
                reason=e.get("reason", ""),
            )
        )
    return Crosswalk(
        version=int(doc["crosswalk_version"]),
        docs_commit=str(doc.get("openfdd_docs_commit", "")),
        rows=tuple(rows),
        equip_types=dict(doc.get("equip_types") or {}),
        sources=tuple(doc.get("openfdd_docs") or ()),
    )


@lru_cache(maxsize=1)
def load_crosswalk() -> Crosswalk:
    """The crosswalk shipped with CAMBER (``camber/interop/openfdd/crosswalk.json``)."""
    text = files("camber.interop.openfdd").joinpath("crosswalk.json").read_text("utf-8")
    return _parse(json.loads(text))


def canonical_unit(unit) -> str | None:
    """A declared unit string -> its canonical spelling; ``""`` if blank, ``None`` if unknown."""
    if unit is None:
        return ""
    key = str(unit).strip().lower()
    if key in _BLANK_UNITS:
        return ""
    return _UNIT_ALIASES.get(key)


def resolve_unit(quantity: str, declared, unit_system: str) -> tuple:
    """``(unit, error)`` for one column: the declared unit when it fits the quantity, else the
    unit system's default; ``error`` explains a declared unit that cannot be used."""
    table = _CONVERSIONS[quantity]
    unit = canonical_unit(declared)
    if unit is None:
        return None, f"declared unit {declared!r} is not one CAMBER can convert"
    if unit:
        if unit in table:
            return unit, ""
        if quantity in ("binary", "count") or (quantity == "percent" and unit == ""):
            return "", ""
        return None, f"declared unit {declared!r} does not fit a {quantity} point"
    if quantity in _DEFAULT_UNIT[unit_system]:
        return _DEFAULT_UNIT[unit_system][quantity], ""
    return "", ""  # unit-free, or the same in both systems (%, kW, ppm): left as read


def convert(series: pd.Series, quantity: str, unit: str) -> pd.Series:
    """Convert ``series`` from ``unit`` to CAMBER's IP unit for ``quantity``."""
    scale, offset = _CONVERSIONS[quantity].get(unit or "", (1.0, 0.0))
    if scale == 1.0 and offset == 0.0:
        return series
    return series * scale + offset
