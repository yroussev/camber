"""Energy units: parse, convert and report energy, demand and EUI in IP or SI (provisional, 0.92).

CAMBER's rules and detectors keep their own internal units (IP, as before). This module is about
the energy a user *reads*: M&V savings and their bands, billing totals, SEP primary energy, EUI,
and per-unit prices and emission factors. A config ``"units": {"system": "ip" | "si"}`` chooses
how those are reported (:class:`UnitSystem`):

=========  ============  =============  ================
system     energy        demand/power   EUI
=========  ============  =============  ================
``ip``     kBtu          kBtu/h         kBtu/ft2/yr
``si``     kWh           kW             kWh/m2/yr
=========  ============  =============  ================

Without a ``units`` block nothing changes: every output stays in the meter's own unit, as before
0.92. Temperatures, pressures and flows are not touched.

**The canonical internal unit is the kWh.** Every energy unit is stored as kWh per unit
(:data:`ENERGY_UNITS`) and every power unit as kW per unit (:data:`POWER_UNITS`); a conversion goes
through kWh, so any pair round-trips to float precision.

**Factors** (all exact by definition; the rounded forms are the usual printed ones):

* 1 Btu (International Table) = 1055.05585262 J, exact (NIST SP 811, Appendix B.8). Hence
  1 kWh = 3600 kJ / 1.05505585262 kJ = 3.41214163 kBtu (printed **3.412142**) and
  1 kBtu = **1.055056** MJ.
* 1 therm = 100,000 Btu (IT); 1 dekatherm = 1 MMBtu = 1,000 kBtu; 1 kBtu = 1,000 Btu.
  (The US therm, defined on the 59 F Btu, is 105.4804 MJ, 0.024 % smaller; CAMBER uses the IT
  therm throughout.)
* 1 ton-hour of refrigeration = 12,000 Btu = 12 kBtu; 1 ton = 12,000 Btu/h.
* 1 kWh = 3.6 MJ exactly; 1 GJ = 1,000 MJ.
* 1 ft = 0.3048 m exactly, so 1 m2 = 10.7639104 ft2 and 1 m3 = 35.3146667 ft3.

:data:`camber.bps.EUI_FACTORS_KBTU` keeps its historical **3.412** kBtu/kWh, so that
:func:`camber.bps.site_eui` outputs do not move; it is 0.004 % below the exact 3.412142. The
unit-aware :func:`camber.bps.site_eui_units` uses the exact factor. See docs/UNITS.md.

**Gas by volume and steam by mass** have no fixed energy content. A volume (``Mcf``, ``CCF``,
``cf``/``ft3``, ``m3``) converts only with an explicit ``heat_content`` (for example
``"10.37 therm/Mcf"`` or ``"1037 Btu/ft3"``), and a steam mass (``lb``, ``klb``, ``kg``) only
with an explicit ``enthalpy`` (``"1000 Btu/lb"``). There is no silent default.

**Parsing** (:func:`parse_unit`) ignores case, spaces, ``-``/``_``/``·``, and accepts common
spellings (``kilowatt-hours``, ``therms``, ``MMBtu``, ``ton-hr``, ``MBH``, ``m³``). Unknown units
raise ``ValueError``, and so do **ambiguous** ones: ``MBtu`` and ``Mlb`` (M is a thousand in US
utility usage and a million in SI), a bare ``ton`` where energy is expected, and ``therm`` where
power is expected. A bare ``Mcf`` is a *thousand* cubic feet (the US gas-utility reading);
ENERGY STAR Portfolio Manager writes Mcf for a *million*.

**Published factor sets** (:mod:`camber.energy_factors`, opt-in) supply heat contents for fuels
billed by volume or mass, e.g. ENERGY STAR's 1,026 Btu/cf for US natural gas. They carry the
publisher's own, often rounded, multipliers (3.412 kBtu/kWh); the exact factors here are unchanged.

Provisional (0.92): names and signatures may change in a minor release.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "BTU_J",
    "KBTU_PER_KWH",
    "ENERGY_UNITS",
    "POWER_UNITS",
    "VOLUME_UNITS",
    "MASS_UNITS",
    "SYSTEMS",
    "Unit",
    "UnitSystem",
    "parse_unit",
    "parse_heat_content",
    "to_kwh",
    "convert",
    "energy_factor",
    "convert_power",
    "power_factor",
    "energy_unit_of_rate",
    "convert_eui",
    "eui_factor",
    "convert_rate",
    "format_energy",
]

#: The International Table Btu in joules, exact (NIST SP 811, Appendix B.8).
BTU_J = 1055.05585262
_KWH_J = 3.6e6
#: kBtu per kWh, exact: 3.41214163... (printed 3.412142).
KBTU_PER_KWH = _KWH_J / (1000.0 * BTU_J)
_KWH_PER_BTU = BTU_J / _KWH_J

# joules per one unit: conversions divide these, so Btu-based pairs (therm/kBtu) stay exact
_ENERGY_J: dict = {
    "kWh": _KWH_J,
    "MWh": 1.0e3 * _KWH_J,
    "GWh": 1.0e6 * _KWH_J,
    "Wh": _KWH_J / 1.0e3,
    "Btu": BTU_J,
    "kBtu": 1.0e3 * BTU_J,
    "MMBtu": 1.0e6 * BTU_J,
    "therm": 1.0e5 * BTU_J,
    "ton-hour": 1.2e4 * BTU_J,
    "kJ": 1.0e3,
    "MJ": 1.0e6,
    "GJ": 1.0e9,
}
#: kWh per one unit, keyed by canonical unit name.
ENERGY_UNITS: dict = {k: j / _KWH_J for k, j in _ENERGY_J.items()}

#: kW per one unit, keyed by canonical unit name.
POWER_UNITS: dict = {
    "kW": 1.0,
    "MW": 1000.0,
    "W": 1.0e-3,
    "Btu/h": _KWH_PER_BTU,
    "kBtu/h": 1.0e3 * _KWH_PER_BTU,
    "MMBtu/h": 1.0e6 * _KWH_PER_BTU,
    "ton": 1.2e4 * _KWH_PER_BTU,
}

# a rate integrated over one hour, as energy
_RATE_ENERGY = {
    "kW": "kWh",
    "MW": "MWh",
    "W": "Wh",
    "Btu/h": "Btu",
    "kBtu/h": "kBtu",
    "MMBtu/h": "MMBtu",
    "ton": "ton-hour",
}

_FT3_PER_M3 = 1.0 / 0.3048**3
#: cubic feet per one unit.
VOLUME_UNITS: dict = {"ft3": 1.0, "CCF": 100.0, "Mcf": 1000.0, "m3": _FT3_PER_M3}
_LB_KG = 0.45359237  # exact
#: pounds (mass) per one unit.
MASS_UNITS: dict = {"lb": 1.0, "klb": 1000.0, "kg": 1.0 / _LB_KG}

_ALIASES = {
    "energy": {
        "kWh": ("kwh", "kilowatthour", "kilowatthours", "kwhr", "kwhs"),
        "MWh": ("mwh", "megawatthour", "megawatthours", "mwhr"),
        "GWh": ("gwh", "gigawatthour", "gigawatthours"),
        "Wh": ("wh", "watthour", "watthours"),
        "Btu": ("btu", "btus", "britishthermalunit", "britishthermalunits"),
        "kBtu": ("kbtu", "kbtus", "thousandbtu", "kilobtu"),
        "MMBtu": ("mmbtu", "mmbtus", "millionbtu", "dth", "dekatherm", "dekatherms", "decatherm"),
        "therm": ("therm", "therms", "thm", "thms"),
        "ton-hour": ("tonhour", "tonhours", "tonhr", "tonhrs", "tonh", "tonhs", "trh"),
        "kJ": ("kj", "kilojoule", "kilojoules"),
        "MJ": ("mj", "megajoule", "megajoules"),
        "GJ": ("gj", "gigajoule", "gigajoules"),
    },
    "power": {
        "kW": ("kw", "kilowatt", "kilowatts"),
        "MW": ("mw", "megawatt", "megawatts"),
        "W": ("w", "watt", "watts"),
        "Btu/h": ("btu/h", "btuh", "btu/hr", "btuhr", "btus/h"),
        "kBtu/h": ("kbtu/h", "kbtuh", "kbtu/hr", "kbtuhr", "mbh", "mbtuh"),
        "MMBtu/h": ("mmbtu/h", "mmbtuh", "mmbtu/hr", "mmbtuhr"),
        "ton": ("ton", "tons", "tr", "tonsrefrigeration", "refrigerationton", "refrigerationtons"),
    },
    "volume": {
        "ft3": ("ft3", "cf", "cuft", "cubicfeet", "cubicfoot", "scf"),
        "CCF": ("ccf", "hcf"),
        "Mcf": ("mcf", "mscf"),
        "m3": ("m3", "cubicmeter", "cubicmeters", "cubicmetre", "cubicmetres", "sm3"),
    },
    "mass": {
        "lb": ("lb", "lbs", "pound", "pounds", "lbm"),
        "klb": ("klb", "klbs", "kpound", "kpounds", "thousandpounds"),
        "kg": ("kg", "kgs", "kilogram", "kilograms"),
    },
}
_LOOKUP: dict = {}
for _kind, _names in _ALIASES.items():
    for _canon, _spellings in _names.items():
        for _s in _spellings:
            _LOOKUP.setdefault(_s, []).append((_kind, _canon))

_AMBIGUOUS = {
    "mbtu": "'MBtu' is ambiguous (M is a thousand in US utility usage, a million in SI): "
    "write kBtu or MMBtu",
    "mlb": "'Mlb' is ambiguous (a thousand or a million pounds): write klb",
    "mlbs": "'Mlb' is ambiguous (a thousand or a million pounds): write klb",
    "mmcf": "'MMcf' (a million cubic feet) is read only through a factor set "
    "(camber.energy_factors, units.factor_set); otherwise write the volume in Mcf",
    "mm3": "'Mm3' is ambiguous (million cubic metres or cubic millimetres): write m3",
}


@dataclass(frozen=True)
class Unit:
    """A parsed unit: its ``kind`` (``energy``, ``power``, ``volume`` or ``mass``), canonical
    ``name``, and ``scale`` (kWh, kW, ft3 or lb per one unit)."""

    kind: str
    name: str
    scale: float


def _norm(text: str) -> str:
    t = str(text).strip().lower()
    t = t.replace("³", "3").replace("²", "2").replace("·", "").replace("•", "")
    t = re.sub(r"\bper\b", "/", t)
    t = re.sub(r"[\s_\-.]+", "", t)
    t = t.replace("/hour", "/h").replace("/hr", "/h")
    return t


def parse_unit(text: str, *, kind: str | tuple | None = None) -> Unit:
    """Parse a unit string into a :class:`Unit`; ``kind`` restricts what is acceptable.

    Raises ``ValueError`` on an empty, unknown or ambiguous unit, or one of the wrong kind (a
    bare ``"ton"`` is a power unit, so ``kind="energy"`` refuses it and suggests ``ton-hour``).
    """
    if text is None or not str(text).strip():
        raise ValueError("no unit given")
    key = _norm(text)
    if key in _AMBIGUOUS:
        raise ValueError(_AMBIGUOUS[key])
    hits = _LOOKUP.get(key, [])
    if kind is not None:
        want = {kind} if isinstance(kind, str) else set(kind)
        ok = [h for h in hits if h[0] in want]
        if not ok:
            if hits:
                have = hits[0][0]
                hint = " (did you mean ton-hour?)" if key in ("ton", "tons", "tr") else ""
                raise ValueError(
                    f"{text!r} is a {have} unit, not {' or '.join(sorted(want))}{hint}"
                )
            raise ValueError(f"unknown {'/'.join(sorted(want))} unit {text!r}{_known(want)}")
        hits = ok
    if not hits:
        raise ValueError(f"unknown unit {text!r}{_known(None)}")
    if len(hits) > 1:  # pragma: no cover - the alias table has no cross-kind duplicates
        raise ValueError(f"{text!r} is ambiguous: {hits}")
    k, name = hits[0]
    table = {"energy": ENERGY_UNITS, "power": POWER_UNITS, "volume": VOLUME_UNITS}
    scale = table.get(k, MASS_UNITS)[name]
    return Unit(kind=k, name=name, scale=scale)


def _known(kinds) -> str:
    tables = {
        "energy": ENERGY_UNITS,
        "power": POWER_UNITS,
        "volume": VOLUME_UNITS,
        "mass": MASS_UNITS,
    }
    names = [n for k, t in tables.items() if kinds is None or k in kinds for n in t]
    return f"; known: {', '.join(names)}"


_HC_RE = re.compile(r"^\s*([-+0-9.eE,]+)\s*(.+)$")


def parse_heat_content(spec) -> tuple:
    """A heat content (gas, per volume) or enthalpy (steam, per mass): ``(kWh per unit, kind)``
    with ``kind`` ``"volume"`` (kWh per ft3) or ``"mass"`` (kWh per lb).

    ``spec`` is ``"10.37 therm/Mcf"``, ``{"value": 10.37, "unit": "therm/Mcf"}`` or a
    ``(value, "therm/Mcf")`` pair. The unit is an energy unit over a volume or a mass unit.
    """
    v, unit = _hc_value_unit(spec)
    per_j, kind = _hc_j(v, unit)
    return per_j / _KWH_J, kind


def _hc_value_unit(spec) -> tuple:
    """``(value, unit)`` of a heat-content spec, validated (see :func:`parse_heat_content`)."""
    if isinstance(spec, Mapping):
        value, unit = spec.get("value"), spec.get("unit")
    elif isinstance(spec, (tuple, list)) and len(spec) == 2:
        value, unit = spec
    elif isinstance(spec, str):
        m = _HC_RE.match(spec)
        if not m:
            raise ValueError(f"unreadable heat content {spec!r}; write e.g. '10.37 therm/Mcf'")
        value, unit = m.group(1).replace(",", ""), m.group(2)
    else:
        raise ValueError(f"heat content must be '<value> <energy>/<volume or mass>', got {spec!r}")
    try:
        v = float(value)  # type: ignore[arg-type]  # None / junk raise and are reported below
    except (TypeError, ValueError):
        raise ValueError(f"heat content value {value!r} is not a number") from None
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"heat content must be a positive number, got {value!r}")
    if not isinstance(unit, str) or "/" not in unit.replace(" per ", "/"):
        raise ValueError(f"heat content unit must be '<energy>/<volume or mass>', got {unit!r}")
    return v, unit


def _hc_j(v: float, unit: str) -> tuple:
    """``(joules per ft3 or per lb, "volume" | "mass")`` of ``v`` in ``unit``."""
    num, den = unit.replace(" per ", "/").split("/", 1)
    e = parse_unit(num, kind="energy")
    q = parse_unit(den, kind=("volume", "mass"))
    return v * _ENERGY_J[e.name] / q.scale, q.kind


def to_kwh(value, unit, *, heat_content=None, enthalpy=None):
    """``value`` (a number or array) in ``unit`` as kWh.

    ``unit`` is an energy unit, or a gas volume (needs ``heat_content``) or a steam mass (needs
    ``enthalpy``); see :func:`parse_heat_content`. A missing heat content or enthalpy is an error,
    never a default.
    """
    return value * (_j_per(unit, heat_content=heat_content, enthalpy=enthalpy) / _KWH_J)


def _j_per(unit, *, heat_content=None, enthalpy=None) -> float:
    """Joules in one ``unit`` (an energy unit, or a volume / mass with its heat content)."""
    u = parse_unit(unit, kind=("energy", "volume", "mass"))
    if u.kind == "energy":
        return _ENERGY_J[u.name]
    if u.kind == "volume":
        if heat_content is None:
            raise ValueError(
                f"{unit!r} is a gas volume: give its heat_content (e.g. '10.37 therm/Mcf' or "
                "'1037 Btu/ft3'); there is no default"
            )
        per, k = _hc_j(*_hc_value_unit(heat_content))
        if k != "volume":
            raise ValueError(f"heat_content {heat_content!r} is per mass; {unit!r} is a volume")
        return per * u.scale
    if enthalpy is None:
        raise ValueError(
            f"{unit!r} is a steam mass: give its enthalpy (e.g. '1000 Btu/lb'); there is no default"
        )
    per, k = _hc_j(*_hc_value_unit(enthalpy))
    if k != "mass":
        raise ValueError(f"enthalpy {enthalpy!r} is per volume; {unit!r} is a mass")
    return per * u.scale


def energy_factor(from_unit, to_unit, *, heat_content=None, enthalpy=None) -> float:
    """The factor that turns an amount in ``from_unit`` into ``to_unit`` (an energy unit).

    ``from_unit`` may be a gas volume or steam mass (with ``heat_content`` / ``enthalpy``)."""
    to = parse_unit(to_unit, kind="energy")
    return _j_per(from_unit, heat_content=heat_content, enthalpy=enthalpy) / _ENERGY_J[to.name]


def convert(value, from_unit, to_unit, *, heat_content=None, enthalpy=None):
    """``value`` (a number or array) converted from ``from_unit`` to the energy unit ``to_unit``."""
    return value * energy_factor(from_unit, to_unit, heat_content=heat_content, enthalpy=enthalpy)


def power_factor(from_unit, to_unit) -> float:
    """The factor that turns a power in ``from_unit`` into ``to_unit`` (kW, kBtu/h, MBH, tons)."""
    return parse_unit(from_unit, kind="power").scale / parse_unit(to_unit, kind="power").scale


def convert_power(value, from_unit, to_unit):
    """``value`` (a number or array) converted between power units."""
    return value * power_factor(from_unit, to_unit)


def energy_unit_of_rate(unit) -> str:
    """The energy unit a power ``unit`` integrates to over an hour (``kW`` -> ``kWh``, ``Btu/h``
    -> ``Btu``, ``MBH`` -> ``kBtu``, ``tons`` -> ``ton-hour``)."""
    return _RATE_ENERGY[parse_unit(unit, kind="power").name]


_AREA = {"ft2": 1.0, "m2": 1.0 / 0.3048**2}  # ft2 per unit (1 ft = 0.3048 m, exact)
_AREA_ALIASES = {
    "ft2": "ft2",
    "sqft": "ft2",
    "sf": "ft2",
    "squarefeet": "ft2",
    "squarefoot": "ft2",
    "m2": "m2",
    "sqm": "m2",
    "squaremeter": "m2",
    "squaremeters": "m2",
    "squaremetre": "m2",
    "squaremetres": "m2",
}


def _area(text: str) -> str:
    key = _norm(text)
    if key not in _AREA_ALIASES:
        raise ValueError(f"unknown area unit {text!r}; use ft2 or m2")
    return _AREA_ALIASES[key]


def _eui(text: str) -> tuple:
    """``'kBtu/ft2/yr'`` -> ``(energy Unit, area name)``; the time base is always a year."""
    t = str(text).replace("·", "/").replace("²", "2")
    parts = [p for p in re.split(r"/", t) if p.strip()]
    if len(parts) == 3 and _norm(parts[2]) in ("yr", "y", "year", "a", "annum"):
        parts = parts[:2]
    if len(parts) != 2:
        raise ValueError(f"unreadable EUI unit {text!r}; write e.g. 'kBtu/ft2/yr' or 'kWh/m2/yr'")
    area = parts[1].strip()
    for suffix in ("yr", "year"):  # "ft2yr" (from a "ft2·yr" written without a separator)
        if _norm(area).endswith(suffix) and _norm(area)[: -len(suffix)] in _AREA_ALIASES:
            area = _norm(area)[: -len(suffix)]
    return parse_unit(parts[0], kind="energy"), _area(area)


def eui_factor(from_unit: str, to_unit: str) -> float:
    """The factor between two annual EUI units (``kBtu/ft2/yr``, ``kWh/m2/yr``, ``MJ/m2/yr``)."""
    e1, a1 = _eui(from_unit)
    e2, a2 = _eui(to_unit)
    return (_ENERGY_J[e1.name] / _ENERGY_J[e2.name]) * (_AREA[a2] / _AREA[a1])


def convert_eui(value, from_unit: str, to_unit: str):
    """``value`` converted between EUI units (``kBtu/ft2/yr`` <-> ``kWh/m2/yr``)."""
    return value * eui_factor(from_unit, to_unit)


def convert_rate(rate, per_unit, to_per_unit, *, heat_content=None, enthalpy=None):
    """A per-unit rate (``$/therm``, ``kg CO2e/MMBtu``, ``$/Mcf`` ...) re-expressed per
    ``to_per_unit``: ``rate x (amount of per_unit in one to_per_unit)``.

    ``per_unit`` may be a gas volume or steam mass (then ``heat_content`` / ``enthalpy``);
    ``to_per_unit`` is an energy unit. For example ``convert_rate(1.20, "therm", "kWh")`` is the
    price per kWh of gas sold at $1.20 a therm.
    """
    one_to_in_from = _ENERGY_J[parse_unit(to_per_unit, kind="energy").name] / _j_per(
        per_unit, heat_content=heat_content, enthalpy=enthalpy
    )
    return rate * one_to_in_from


def format_energy(value, unit: str | None = None, fmt: str = "{:,.0f}") -> str:
    """``value`` formatted, followed by ``unit`` when one is given (``"1,234 kBtu"``)."""
    s = fmt.format(value)
    return f"{s} {unit}" if unit else s


# --------------------------------------------------------------------------- the config system

#: The reporting systems: energy, demand and EUI units, and the default area unit.
SYSTEMS: dict = {
    "ip": {"energy": "kBtu", "power": "kBtu/h", "eui": "kBtu/ft2/yr", "area": "ft2"},
    "si": {"energy": "kWh", "power": "kW", "eui": "kWh/m2/yr", "area": "m2"},
}
_CONFIG_KEYS = {"system", "area"}


@dataclass(frozen=True)
class UnitSystem:
    """The reporting unit system a config's ``units`` block chooses (provisional, 0.92).

    ``energy``, ``power`` and ``eui`` are the reported unit labels; ``area`` is the unit a
    config's floor areas are stated in (``ft2`` for IP and ``m2`` for SI unless ``units.area``
    says otherwise).
    """

    system: str
    energy: str
    power: str
    eui: str
    area: str

    @classmethod
    def of(cls, system: str, *, area: str | None = None) -> UnitSystem:
        """The :class:`UnitSystem` named ``"ip"`` or ``"si"``."""
        key = str(system).strip().lower()
        if key not in SYSTEMS:
            raise ValueError(f'units.system must be "ip" or "si", got {system!r}')
        s = SYSTEMS[key]
        return cls(
            system=key,
            energy=s["energy"],
            power=s["power"],
            eui=s["eui"],
            area=_area(area) if area is not None else s["area"],
        )

    @classmethod
    def from_config(cls, config: Mapping | None) -> UnitSystem | None:
        """The config's ``units`` block as a :class:`UnitSystem`, or ``None`` when it has none
        (every output then stays in the meter's own unit, exactly as before 0.92)."""
        spec = (config or {}).get("units")
        if spec is None:
            return None
        if isinstance(spec, str):
            spec = {"system": spec}
        if not isinstance(spec, Mapping) or "system" not in spec:
            raise ValueError('units must be {"system": "ip" | "si"} (optionally "area")')
        extra = set(spec) - _CONFIG_KEYS
        if extra:
            raise ValueError(f"units: unknown key(s) {sorted(extra)}")
        return cls.of(spec["system"], area=spec.get("area"))

    def energy_factor(self, from_unit, *, heat_content=None, enthalpy=None) -> float:
        """The factor from ``from_unit`` to this system's energy unit."""
        return energy_factor(from_unit, self.energy, heat_content=heat_content, enthalpy=enthalpy)

    def power_factor(self, from_unit) -> float:
        """The factor from the power ``from_unit`` to this system's demand unit."""
        return power_factor(from_unit, self.power)

    def eui_factor(self, from_unit: str) -> float:
        """The factor from the EUI ``from_unit`` to this system's EUI unit."""
        return eui_factor(from_unit, self.eui)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return {
            "system": self.system,
            "energy": self.energy,
            "power": self.power,
            "eui": self.eui,
            "area": self.area,
        }
