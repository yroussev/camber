"""Refrigerant saturation properties and the pressure-to-temperature transforms (provisional, 0.93).

A BAS or lab logger usually trends refrigerant **pressures** and **line temperatures**, not the
saturation-referenced differences a technician reads off a gauge set. This module turns the first
into the second:

* ``subcooling = T_sat,bubble(P_liquid) - T_liquid_line``
* ``superheat = T_suction_line - T_sat,dew(P_suction)``
* ``discharge superheat = T_discharge_line - T_sat,dew(P_discharge)``
* condenser approach ``= T_sat(P_discharge) - T_condenser_leaving_water`` and evaporator approach
  ``= T_chw_leaving - T_sat(P_suction)``

so a unit that logs pressures and line temperatures feeds the charge and heat-transfer detectors
that key off :attr:`~camber.model.roles.Role.SUBCOOLING_TEMP`,
:attr:`~camber.model.roles.Role.SUPERHEAT_TEMP` and the approach roles (see
:func:`derive_refrigerant_roles`; an equipment's ``"refrigerant"`` in a run config switches the
derivation on).

**No property library is needed.** Each fluid's saturation curve is a published
vapour-pressure correlation of the Wagner form

    ln(p / p_c) = (T_c / T) * sum_i n_i * (1 - T / T_c) ** t_i

evaluated directly for ``p(T)`` and inverted numerically for ``T(p)``. The coefficients and their
sources:

========  ======================================================================================
R-410A    Bubble and dew equations of Lemmon (2003), Int. J. Thermophys. 24(4):991-1006,
          doi:10.1023/A:1025048800563 (the pseudo-pure R-410A model's own saturation lines).
R-744     Vapour-pressure equation (eq. 3.13) of Span & Wagner (1996), J. Phys. Chem. Ref. Data
          25(6):1509-1596, doi:10.1063/1.555991.
R-134a    Ancillary vapour-pressure fits to the reference equations of state (R-134a: Tillner-Roth
R-22      & Baehr 1994, doi:10.1063/1.555958; R-22: Kamei, Beyerlein & Jacobsen 1995,
R-32      doi:10.1007/BF01441910; R-32: Tillner-Roth & Yokozeki 1997, doi:10.1063/1.556002) as
          published in CoolProp 8.0.0 (Bell et al. 2014, doi:10.1021/ie4033999; MIT licence; see
          NOTICE).
========  ======================================================================================

**Accuracy.** Checked against NIST REFPROP saturation temperatures published beside the raw
pressures in the NIST residential heat-pump FDD data (R-410A, doi:10.18434/M32132; 7,374 test
points, 78 to 625 psia): bubble within 0.011 degF, dew within 0.007 degF. The pure-fluid
correlations agree with their full reference equations of state (via CoolProp) to 0.014 degF or
better from -40 to 150 degF. Both are far inside the +/-0.5 degF the transforms need, and far inside
the error of a field pressure transducer (1 psi at 120 psig is about 0.6 degF of R-410A saturation).

**Gauge vs absolute.** CAMBER's pressure roles are **gauge** (psig) -- a dataset published in psia
is converted at ingest -- so ``basis="gauge"`` is the default and the absolute pressure is the
reading plus :data:`STANDARD_ATM_PSIA`. A site well above sea level can pass its own ``atm_psia``
(at 5,000 ft it is about 12.2 psia: taking 14.7 there reads R-410A saturation ~1.3 degF high at
suction, ~0.5 degF at discharge).

**Blends and glide.** R-410A is a near-azeotropic blend: at one pressure its bubble (saturated
liquid) and dew (saturated vapour) temperatures differ by about 0.2 degF. Subcooling is referenced
to the bubble point and every superheat to the dew point (the convention of manufacturer charging
tables and of NIST's own subcooling and superheat columns); the approaches use the dew point, the
compressor-rating convention for a blend's saturated temperature. For a pure fluid the two are one
curve.

**CO2 (R-744) is transcritical.** Above its critical point (87.8 degF, 1070 psia) there is no
saturation: a gas cooler's pressure has no condensing temperature and "subcooling" is undefined.
:func:`saturation_temp` returns NaN there (as it does for any fluid above its critical pressure or
below its lowest tabulated temperature), so every transform **declines** row by row instead of
extrapolating a curve that does not exist. A transcritical rack's medium-temperature discharge
crosses the critical pressure routinely in warm weather (in the ORNL CO2 booster data it is
supercritical in 7-12% of summer rows), so the high-side transforms of an R-744 system are
available only while it runs subcritical; the suction side is always subcritical.

The pseudo-pure R-410A critical point (Lemmon 2003: 161.4 degF, 710.9 psia) caps that fluid the
same way; no HVAC operating point comes near it.

Provisional public API (0.93): names and signatures may still change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .model.roles import Role

__all__ = [
    "STANDARD_ATM_PSIA",
    "Refrigerant",
    "REFRIGERANTS",
    "get_refrigerant",
    "normalize_refrigerant",
    "saturation_pressure",
    "saturation_temp",
    "is_supercritical",
    "subcooling",
    "superheat",
    "discharge_superheat",
    "condenser_approach",
    "evaporator_approach",
    "DERIVED_ROLES",
    "derivation_inputs",
    "derive_refrigerant_roles",
]

#: Standard atmosphere, psia -- the default gauge-to-absolute offset.
STANDARD_ATM_PSIA = 14.695949

_PSI_PER_KPA = 0.14503773773
_PSI_PER = {
    "psi": 1.0,
    "kpa": _PSI_PER_KPA,
    "bar": 100.0 * _PSI_PER_KPA,
    "mpa": 1000.0 * _PSI_PER_KPA,
}


def _k_to_f(k):
    return (k - 273.15) * 1.8 + 32.0


def _f_to_k(f):
    return (f - 32.0) / 1.8 + 273.15


@dataclass(frozen=True)
class Refrigerant:
    """One fluid's saturation correlation (Wagner form; see the module docstring).

    ``bubble`` and ``dew`` are ``(n, t)`` coefficient tuples; a pure fluid has one curve, stored
    in both. ``tc_k`` / ``pc_kpa`` are the correlation's reducing (critical) point and
    ``tmin_k`` the lowest temperature it is valid at.
    """

    name: str
    tc_k: float
    pc_kpa: float
    tmin_k: float
    bubble: tuple
    dew: tuple
    blend: bool
    source: str

    @property
    def critical_temp_f(self) -> float:
        """The critical (pseudo-critical, for a blend) temperature, degF."""
        return float(_k_to_f(self.tc_k))

    @property
    def critical_pressure_psia(self) -> float:
        """The critical (pseudo-critical, for a blend) pressure, psia."""
        return self.pc_kpa * _PSI_PER_KPA

    def _p_kpa(self, t_k, point: str):
        n, t = self.bubble if point == "bubble" else self.dew
        t_k = np.asarray(t_k, dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            theta = 1.0 - t_k / self.tc_k
            s = sum(a * np.power(theta, b) for a, b in zip(n, t))
            return self.pc_kpa * np.exp(self.tc_k / t_k * s)


_R410A = Refrigerant(
    name="R-410A",
    tc_k=344.494,
    pc_kpa=4901.2,
    tmin_k=200.0,
    bubble=((-7.2818, 2.5093, -3.2695, -2.8022), (1.0, 1.8, 2.4, 4.9)),
    dew=((-7.4411, 1.9883, -2.4925, -3.2633), (1.0, 1.6, 2.4, 5.0)),
    blend=True,
    source="Lemmon (2003), Int. J. Thermophys. 24(4):991, doi:10.1023/A:1025048800563",
)

_CO2_CURVE = ((-7.0602087, 1.9391218, -1.6463597, -3.2995634), (1.0, 1.5, 2.0, 4.0))
_R744 = Refrigerant(
    name="R-744",
    tc_k=304.1282,
    pc_kpa=7377.3,
    tmin_k=216.592,  # the triple point: below it CO2 is solid, not a saturated liquid
    bubble=_CO2_CURVE,
    dew=_CO2_CURVE,
    blend=False,
    source="Span & Wagner (1996), J. Phys. Chem. Ref. Data 25(6):1509, eq. 3.13",
)

_R134A_CURVE = (
    (
        0.4331478287291047,
        -9.090302559074352,
        2.1476074125217703,
        -1.557687007603464,
        -3.5020328972698604,
        14.958442337201044,
    ),
    (0.845, 0.99, 1.14, 2.651, 4.507, 17.235),
)
_R22_CURVE = (
    (
        -7.2670371701641265,
        1.2860929616388512,
        -0.10790119794275199,
        -0.989473291657183,
        -2.9393096353041623,
        0.488431917326884,
    ),
    (0.999, 1.272, 1.692, 2.74, 4.539, 15.876),
)
_R32_CURVE = (
    (
        0.019194334673139708,
        -6.794802163795212,
        9.276391392786632,
        -12.327153852761946,
        28.46579606476374,
        -28.570870718190093,
    ),
    (0.531, 0.979, 2.203, 2.382, 3.421, 3.558),
)
_COOLPROP = "ancillary fit to the reference EOS ({}), CoolProp 8.0.0 (MIT)"

#: canonical name -> :class:`Refrigerant`.
REFRIGERANTS: dict = {
    "R-410A": _R410A,
    "R-744": _R744,
    "R-134a": Refrigerant(
        "R-134a",
        374.21,
        4059.28,
        169.85,
        _R134A_CURVE,
        _R134A_CURVE,
        False,
        _COOLPROP.format("Tillner-Roth & Baehr 1994"),
    ),
    "R-22": Refrigerant(
        "R-22",
        369.295,
        4990.0,
        115.73,
        _R22_CURVE,
        _R22_CURVE,
        False,
        _COOLPROP.format("Kamei, Beyerlein & Jacobsen 1995"),
    ),
    "R-32": Refrigerant(
        "R-32",
        351.255,
        5782.0,
        136.34,
        _R32_CURVE,
        _R32_CURVE,
        False,
        _COOLPROP.format("Tillner-Roth & Yokozeki 1997"),
    ),
}

_ALIASES = {"CO2": "R-744", "CARBONDIOXIDE": "R-744"}


def normalize_refrigerant(name) -> str:
    """The canonical name for a refrigerant spelling (``"r410a"``, ``"410A"``, ``"CO2"`` ...).

    Raises ``ValueError`` naming the supported fluids for an unknown one.
    """
    if isinstance(name, Refrigerant):
        return name.name
    raw = re.sub(r"[^A-Z0-9]", "", str(name).upper())
    if raw in _ALIASES:
        return _ALIASES[raw]
    if raw and not raw.startswith("R"):
        raw = "R" + raw
    for canon in REFRIGERANTS:
        if re.sub(r"[^A-Z0-9]", "", canon.upper()) == raw:
            return canon
    raise ValueError(f"unknown refrigerant {name!r}; supported: {', '.join(sorted(REFRIGERANTS))}")


def get_refrigerant(name) -> Refrigerant:
    """The :class:`Refrigerant` for a name in any accepted spelling (see
    :func:`normalize_refrigerant`)."""
    if isinstance(name, Refrigerant):
        return name
    return REFRIGERANTS[normalize_refrigerant(name)]


def _point(point: str) -> str:
    p = str(point).lower()
    if p not in ("bubble", "dew"):
        raise ValueError(f"point must be 'bubble' or 'dew', not {point!r}")
    return p


def _as_array(x):
    """(float ndarray, rebuild) -- rebuild(values) restores a Series / scalar shape."""
    if isinstance(x, pd.Series):
        idx, name = x.index, x.name
        arr = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
        return arr, lambda v: pd.Series(v, index=idx, name=name)
    if np.ndim(x) == 0:
        try:
            val = float(x)
        except (TypeError, ValueError):
            val = math.nan
        return np.array([val]), lambda v: float(v[0])
    return np.asarray(x, dtype=float), lambda v: v


def _psia(pressure, basis: str, atm_psia: float, unit: str):
    b = str(basis).lower()
    if b not in ("gauge", "absolute"):
        raise ValueError(f"basis must be 'gauge' or 'absolute', not {basis!r}")
    u = str(unit).lower()
    if u not in _PSI_PER:
        raise ValueError(f"pressure unit must be one of {sorted(_PSI_PER)}, not {unit!r}")
    p = pressure * _PSI_PER[u]
    return p + atm_psia if b == "gauge" else p


def _temp_out(t_f, unit: str):
    u = str(unit).upper().lstrip("°").replace("DEG", "")
    if u == "F":
        return t_f
    if u == "C":
        return (t_f - 32.0) / 1.8
    if u == "K":
        return _f_to_k(t_f)
    raise ValueError(f"temperature unit must be 'F', 'C' or 'K', not {unit!r}")


def _temp_in_f(t, unit: str):
    u = str(unit).upper().lstrip("°").replace("DEG", "")
    if u == "F":
        return t
    if u == "C":
        return t * 1.8 + 32.0
    if u == "K":
        return _k_to_f(t)
    raise ValueError(f"temperature unit must be 'F', 'C' or 'K', not {unit!r}")


def saturation_pressure(
    temp,
    fluid,
    *,
    point: str = "bubble",
    basis: str = "gauge",
    atm_psia: float = STANDARD_ATM_PSIA,
    temp_unit: str = "F",
    pressure_unit: str = "psi",
):
    """Saturation pressure at ``temp`` (degF by default), gauge psi by default.

    ``point`` picks the bubble (saturated liquid) or dew (saturated vapour) line of a blend; a pure
    fluid has one line. NaN outside ``[tmin, T_c]``. Scalars, arrays and Series are accepted and
    returned in kind.
    """
    ref = get_refrigerant(fluid)
    arr, rebuild = _as_array(temp)
    t_k = _f_to_k(_temp_in_f(arr, temp_unit))
    ok = np.isfinite(t_k) & (t_k >= ref.tmin_k) & (t_k <= ref.tc_k)
    out = np.full(arr.shape, np.nan)
    out[ok] = ref._p_kpa(t_k[ok], _point(point)) * _PSI_PER_KPA
    if str(basis).lower() == "gauge":
        out = out - atm_psia
    elif str(basis).lower() != "absolute":
        raise ValueError(f"basis must be 'gauge' or 'absolute', not {basis!r}")
    return rebuild(out / _PSI_PER[str(pressure_unit).lower()])


def saturation_temp(
    pressure,
    fluid,
    *,
    point: str = "bubble",
    basis: str = "gauge",
    atm_psia: float = STANDARD_ATM_PSIA,
    pressure_unit: str = "psi",
    temp_unit: str = "F",
):
    """Saturation temperature at ``pressure`` (gauge psi by default), degF by default.

    Inverts the fluid's vapour-pressure correlation by bisection (vectorised; resolution far
    below 0.001 degF). Returns NaN -- a decline, never an extrapolation -- where there is no
    saturation state: at or above the critical pressure (a transcritical CO2 gas cooler), below
    the correlation's lowest temperature, or for a missing / non-numeric reading.
    """
    ref = get_refrigerant(fluid)
    pt = _point(point)
    arr, rebuild = _as_array(pressure)
    p_kpa = _psia(arr, basis, atm_psia, pressure_unit) / _PSI_PER_KPA
    p_lo = float(ref._p_kpa(ref.tmin_k, pt))
    ok = np.isfinite(p_kpa) & (p_kpa >= p_lo) & (p_kpa < ref.pc_kpa)
    out = np.full(arr.shape, np.nan)
    if ok.any():
        target = np.log(p_kpa[ok])
        lo = np.full(target.shape, ref.tmin_k)
        hi = np.full(target.shape, ref.tc_k)
        for _ in range(48):  # (T_c - T_min) / 2**48 is ~1e-12 K
            mid = 0.5 * (lo + hi)
            above = np.log(ref._p_kpa(mid, pt)) > target
            hi = np.where(above, mid, hi)
            lo = np.where(above, lo, mid)
        out[ok] = _k_to_f(0.5 * (lo + hi))
    return rebuild(_temp_out(out, temp_unit))


def is_supercritical(
    pressure,
    fluid,
    *,
    basis: str = "gauge",
    atm_psia: float = STANDARD_ATM_PSIA,
    pressure_unit: str = "psi",
):
    """True where ``pressure`` is at or above the fluid's critical pressure (no saturation state).

    For CO2 this is a transcritical gas cooler; NaN readings are False.
    """
    ref = get_refrigerant(fluid)
    arr, rebuild = _as_array(pressure)
    p = _psia(arr, basis, atm_psia, pressure_unit)
    with np.errstate(invalid="ignore"):
        out = np.isfinite(p) & (p >= ref.critical_pressure_psia)
    return rebuild(out)


def _sat(pressure, fluid, point, kw):
    return saturation_temp(pressure, fluid, point=point, **kw)


def subcooling(liquid_pressure, liquid_temp, fluid, **kw):
    """Liquid subcooling, degF: ``T_sat,bubble(P_liquid) - T_liquid_line``.

    ``liquid_pressure`` is ideally read at the liquid line; the discharge pressure is the usual
    stand-in (it sits a few psi above, reading subcooling slightly high -- a constant offset a
    drift detector absorbs). NaN where the pressure has no saturation state. ``kw`` passes
    ``basis`` / ``atm_psia`` / ``pressure_unit`` to :func:`saturation_temp`.
    """
    return _sat(liquid_pressure, fluid, "bubble", kw) - liquid_temp


def superheat(suction_pressure, suction_temp, fluid, **kw):
    """Suction superheat, degF: ``T_suction_line - T_sat,dew(P_suction)``."""
    return suction_temp - _sat(suction_pressure, fluid, "dew", kw)


def discharge_superheat(discharge_pressure, discharge_temp, fluid, **kw):
    """Compressor discharge superheat, degF: ``T_discharge_line - T_sat,dew(P_discharge)``."""
    return discharge_temp - _sat(discharge_pressure, fluid, "dew", kw)


def condenser_approach(discharge_pressure, condenser_leaving_temp, fluid, **kw):
    """Condenser approach, degF: ``T_sat,dew(P_discharge) - T_leaving`` (the leaving condenser
    water of a water-cooled chiller). Discharge pressure stands in for condenser pressure."""
    return _sat(discharge_pressure, fluid, "dew", kw) - condenser_leaving_temp


def evaporator_approach(suction_pressure, evaporator_leaving_temp, fluid, **kw):
    """Evaporator approach, degF: ``T_leaving - T_sat,dew(P_suction)`` (the leaving chilled
    water). Suction pressure stands in for evaporator pressure (reading the approach slightly
    wide by the suction-line pressure drop)."""
    return evaporator_leaving_temp - _sat(suction_pressure, fluid, "dew", kw)


# derived role -> (the transform, [pressure roles in preference order], the temperature role)
_DERIVATIONS: dict = {
    Role.SUBCOOLING_TEMP: (
        subcooling,
        (Role.LIQUID_LINE_PRESSURE, Role.DISCHARGE_PRESSURE),
        Role.LIQUID_LINE_TEMP,
    ),
    Role.SUPERHEAT_TEMP: (superheat, (Role.SUCTION_PRESSURE,), Role.SUCTION_LINE_TEMP),
    Role.DISCHARGE_SUPERHEAT_TEMP: (
        discharge_superheat,
        (Role.DISCHARGE_PRESSURE,),
        Role.DISCHARGE_LINE_TEMP,
    ),
    Role.COND_APPROACH_TEMP: (condenser_approach, (Role.DISCHARGE_PRESSURE,), Role.CW_RETURN_TEMP),
    Role.EVAP_APPROACH_TEMP: (evaporator_approach, (Role.SUCTION_PRESSURE,), Role.CHW_SUPPLY_TEMP),
}

#: Roles :func:`derive_refrigerant_roles` can compute from pressures and temperatures.
DERIVED_ROLES: tuple = tuple(_DERIVATIONS)


def derivation_inputs(roles) -> tuple:
    """The input roles needed to derive whichever of ``roles`` are derivable (possibly empty)."""
    need: list = []
    for r in roles:
        spec = _DERIVATIONS.get(r)
        if spec is None:
            continue
        for x in (*spec[1], spec[2]):
            if x not in need:
                need.append(x)
    return tuple(need)


def derive_refrigerant_roles(
    frame: pd.DataFrame,
    fluid,
    *,
    roles=None,
    overwrite: bool = False,
    basis: str = "gauge",
    atm_psia: float = STANDARD_ATM_PSIA,
) -> pd.DataFrame:
    """Add saturation-referenced roles computed from a role frame's pressures and temperatures.

    For each of ``roles`` (default :data:`DERIVED_ROLES`) whose inputs are present -- the first
    available pressure role and the temperature role listed in :data:`DERIVED_ROLES`' table -- the
    difference is computed row by row and added as a column. A role the frame already carries (a
    controller-reported subcooling, say) is kept unless ``overwrite``. The approaches are derived
    only against water temperatures (``cw_return_temp`` / ``chw_supply_temp``), i.e. for a
    water-cooled chiller. Rows with no saturation state (a transcritical CO2 discharge) come out
    NaN. The result's ``attrs["refrigerant_derived"]`` records ``{role: {"refrigerant", "from",
    "declined_rows"}}`` for every role it added. Returns a new frame; the input is not modified.
    """
    ref = get_refrigerant(fluid)
    want = DERIVED_ROLES if roles is None else tuple(r for r in roles if r in _DERIVATIONS)
    out = frame.copy()
    notes: dict = dict(frame.attrs.get("refrigerant_derived") or {})
    for role in want:
        if role in out.columns and not overwrite:
            continue
        fn, pressures, temp_role = _DERIVATIONS[role]
        p_role = next((p for p in pressures if p in frame.columns), None)
        if p_role is None or temp_role not in frame.columns:
            continue
        pressure = pd.to_numeric(frame[p_role], errors="coerce")
        temp = pd.to_numeric(frame[temp_role], errors="coerce")
        vals = fn(pressure, temp, ref, basis=basis, atm_psia=atm_psia)
        out[role] = vals
        declined = int((pressure.notna() & temp.notna() & vals.isna()).sum())
        notes[role.value] = {
            "refrigerant": ref.name,
            "from": [p_role.value, temp_role.value],
            "declined_rows": declined,
        }
    if notes:
        out.attrs["refrigerant_derived"] = notes
    return out
