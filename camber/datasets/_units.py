"""Source-unit -> IP conversion for ingested datasets.

CAMBER's rules work in IP units (°F, cfm, inH2O, kW; refrigerant pressures in psig);
:mod:`camber.units` only rescales 0-1 fractions to percent. A catalog entry therefore declares the
unit of every mapped role whose source unit is not already IP (``"units": {"oat": "degC",
"airflow": "L/s"}``), and the ingester converts before writing to the store.

Temperatures come in two flavours: an **absolute** reading (°C -> °F is ``x * 9/5 + 32``) and a
**difference** (a ΔT, an approach, a superheat: ``x * 9/5`` with no offset). The roles that are
differences are listed in :data:`DELTA_ROLES`; a ``degC`` declared on one of them converts as a ΔT.

After conversion, :func:`plausibility_warnings` flags any role whose median sits outside a broad
physical range -- the usual symptom of a wrongly declared (or undeclared) unit.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role

# Roles holding a temperature *difference*, not an absolute temperature.
DELTA_ROLES = frozenset(
    {
        Role.COND_APPROACH_TEMP,
        Role.EVAP_APPROACH_TEMP,
        Role.SUBCOOLING_TEMP,
        Role.SUPERHEAT_TEMP,
        Role.DISCHARGE_SUPERHEAT_TEMP,  # 0.93 (#6)
    }
)

_CFM_PER = {
    "L/s": 2.1188799727597,
    "m3/h": 0.58857777021102,
    "m3/min": 35.314666721489,
    "m3/s": 2118.8799727597,
}
_INH2O_PER = {"Pa": 1.0 / 249.08891, "kPa": 1000.0 / 249.08891}
_TEMP = {"degC", "C", "°C", "K", "degK"}

# Accepted unit spellings -> canonical name. IP units (or unit-free) are accepted as no-ops.
_ALIASES = {
    "degc": "degC",
    "c": "degC",
    "°c": "degC",
    "celsius": "degC",
    "k": "K",
    "degk": "K",
    "kelvin": "K",
    "degf": "degF",
    "f": "degF",
    "°f": "degF",
    "l/s": "L/s",
    "lps": "L/s",
    "m3/h": "m3/h",
    "m³/h": "m3/h",
    "cmh": "m3/h",
    "m3/min": "m3/min",
    "m³/min": "m3/min",
    "cmm": "m3/min",
    "m3/s": "m3/s",
    "m³/s": "m3/s",
    "cfm": "cfm",
    "pa": "Pa",
    "kpa": "kPa",
    "inh2o": "inH2O",
    "in_h2o": "inH2O",
    "w": "W",
    "kw": "kW",
    "psia": "psia",
    "psig": "psig",
    "psi": "psig",
    "percent": "percent",
    "%": "percent",
    "fraction": "fraction",
    "": "",
}
NOOP_UNITS = frozenset({"degF", "cfm", "inH2O", "kW", "psig", "percent", "fraction", ""})
_ATM_PSI = 14.696  # standard atmosphere, psi: absolute -> gauge refrigerant pressure

__all__ = [
    "DELTA_ROLES",
    "NOOP_UNITS",
    "canonical_unit",
    "convert_series",
    "convert_frame",
    "plausibility_warnings",
]


def canonical_unit(unit: str) -> str:
    """The canonical spelling of ``unit``; ``ValueError`` for a unit CAMBER cannot convert."""
    key = str(unit or "").strip().lower()
    if key not in _ALIASES:
        raise ValueError(f"unsupported source unit {unit!r}")
    return _ALIASES[key]


def convert_series(s: pd.Series, unit: str, *, delta: bool = False) -> pd.Series:
    """Convert ``s`` from ``unit`` to its IP equivalent (°F, cfm, inH2O, kW, psig).

    ``delta=True`` treats a temperature as a difference (no +32 / -273.15 offset). IP units and
    unit-free (``percent``/``fraction``) are returned unchanged.
    """
    u = canonical_unit(unit)
    if u in NOOP_UNITS:
        return s
    if u == "degC":
        return s * 9.0 / 5.0 if delta else s * 9.0 / 5.0 + 32.0
    if u == "K":
        return s * 9.0 / 5.0 if delta else (s - 273.15) * 9.0 / 5.0 + 32.0
    if u in _CFM_PER:
        return s * _CFM_PER[u]
    if u in _INH2O_PER:
        return s * _INH2O_PER[u]
    if u == "psia":
        return s - _ATM_PSI
    # the only remaining canonical unit is W
    return s / 1000.0


def convert_frame(frame: pd.DataFrame, units: dict | None) -> pd.DataFrame:
    """Convert each role column of ``frame`` named in ``units`` (``{role slug: unit}``) to IP.

    Roles in :data:`DELTA_ROLES` convert temperatures as differences. Columns not named, and names
    not in the frame, are left alone.
    """
    if not units or frame is None or frame.empty:
        return frame
    out = frame.copy()
    for col in out.columns:
        slug = col.value if isinstance(col, Role) else str(col)
        if slug in units:
            out[col] = convert_series(out[col], units[slug], delta=col in DELTA_ROLES)
    return out


def _temp_range(role) -> tuple:
    if role is Role.DISCHARGE_SUPERHEAT_TEMP:  # 0.93 (#6): tens of degF on a healthy compressor
        return (-10.0, 150.0)
    return (-10.0, 60.0) if role in DELTA_ROLES else (-80.0, 260.0)


# Broad physical ranges (IP) for the median of a converted column. Deliberately generous: this
# catches a missed °C->°F or L/s->cfm, not a faulty sensor.
_PLAUSIBLE = {
    Role.DUCT_STATIC: (-1.0, 10.0),
    Role.DUCT_STATIC_SP: (-1.0, 10.0),
    Role.FILTER_DIFF_PRESS: (-1.0, 10.0),
}
_TEMP_ROLES = frozenset(
    r for r in Role if r.value.endswith("_temp") or r.value.endswith("_temp_sp") or r is Role.OAT
) | frozenset({Role.COOL_SP, Role.HEAT_SP})


def plausibility_warnings(frame: pd.DataFrame, *, label: str = "") -> list:
    """Warnings for role columns whose median is outside a broad IP range (unit mistakes)."""
    out: list = []
    if frame is None or frame.empty:
        return out
    for col in frame.columns:
        role = col if isinstance(col, Role) else None
        if role is None:
            continue
        s = pd.to_numeric(frame[col], errors="coerce").dropna()
        if s.empty:
            continue
        if role in _TEMP_ROLES:
            lo, hi = _temp_range(role)
        elif role in _PLAUSIBLE:
            lo, hi = _PLAUSIBLE[role]
        else:
            continue
        med = float(s.median())
        if not lo <= med <= hi:
            where = f"{label}: " if label else ""
            out.append(
                f"{where}{role.value} median {med:.3g} is outside the plausible IP range "
                f"[{lo:g}, {hi:g}] -- check the declared source unit"
            )
    return out
