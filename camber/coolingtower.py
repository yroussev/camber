"""Cooling-tower approach diagnostic: condenser-water supply vs ambient wet-bulb.

A tower can only cool the condenser water *toward* the ambient wet-bulb, never below
it. The gap it actually achieves is the **approach**:

    approach = CW_supply_temp - wet_bulb_temp

(condenser water leaving the tower, to the chiller condenser). A low approach (~3-7 °F
for a well-sized, clean tower) means good heat rejection; a persistently high approach
at load means fouled/scaled fill, plugged nozzles, reduced airflow (failed/under-
driven fans), or an undersized/over-loaded tower. A high approach raises condenser
water temperature, which raises chiller lift and kW/ton -- so this pairs directly with
the chiller-efficiency rule (cite CTI/ASHRAE cooling-tower performance guidance).

Wet-bulb is rarely a BAS point, so if it isn't mapped we derive it from outdoor
dry-bulb + relative humidity using Stull's closed-form approximation (Stull 2011,
*J. Appl. Meteor. Climatol.*) -- no psychrometric dependency. The design approach is
tower/climate-specific, so ``design_approach_f`` is an injected parameter.

**Stull's fit is for sea-level pressure.** At lower barometric pressure the same dry-bulb and RH
give a *lower* wet-bulb, so a sea-level wet-bulb reads high and the tower approach reads low.
Against the full psychrometric solution, the pressure effect alone is ~0.3-0.8 °F at 500 m and
~1.8-2.5 °F at 1600 m in hot, dry air, on top of Stull's own ~±1 °F fit error (total ≈ +1.4 °F and
+2.6 °F in those conditions). Every function that derives wet-bulb therefore takes an optional
``elevation_ft`` (standard atmosphere) or a measured ``pressure_psia``; given either, wet-bulb is
solved from the ASHRAE psychrometric equations at that pressure (:func:`psychrometric_wetbulb_f`,
within ~0.1 °F of a reference psychrometric library). Omitting both keeps the sea-level Stull
default -- unchanged from before, including its bias at altitude.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "stull_wetbulb_f",
    "psychrometric_wetbulb_f",
    "pressure_psia_at_elevation",
    "SEA_LEVEL_PSIA",
    "cw_range_f",
    "tower_approach_f",
    "CoolingTowerResult",
    "analyze_cooling_tower_approach",
]


def tower_approach_f(
    frame: pd.DataFrame,
    *,
    supply_col: str = "CWS_Temp",
    wetbulb_col: str = "WetBulb",
    oat_col: str = "OAT",
    rh_col: str = "RH",
    elevation_ft: float | None = None,
    pressure_psia: float | None = None,
) -> pd.Series:
    """Cooling-tower **approach** (°F): ``CW_supply - wet_bulb`` (CW leaving the tower).

    The gap the tower actually achieves toward ambient wet-bulb — the same subtraction
    :func:`analyze_cooling_tower_approach` scores as a level, exposed here as a series so
    :mod:`camber.rules.coolingtower_drift_rule` can fit a load-normalized baseline and track its
    **drift** (a widening approach at matched load is the fouling/scale/reduced-airflow signal).

    Wet-bulb is taken from ``wetbulb_col`` when present, else derived from ``oat_col`` + ``rh_col``
    via :func:`stull_wetbulb_f` (no psychrometric dependency; sea level unless ``elevation_ft`` or
    ``pressure_psia`` is given). Raises :class:`KeyError` if it has
    neither a supply temperature nor any wet-bulb source — an approach needs both ends.
    """
    if supply_col not in frame.columns:
        raise KeyError(f"tower_approach_f needs column {supply_col!r}")
    if wetbulb_col in frame.columns:
        wb = frame[wetbulb_col]
    elif oat_col in frame.columns and rh_col in frame.columns:
        wb = pd.Series(
            stull_wetbulb_f(
                frame[oat_col],
                frame[rh_col],
                elevation_ft=elevation_ft,
                pressure_psia=pressure_psia,
            ),
            index=frame.index,
        )
    else:
        raise KeyError(
            f"tower_approach_f needs {wetbulb_col!r}, or both {oat_col!r} and {rh_col!r} "
            "to derive it"
        )
    return frame[supply_col] - wb


def cw_range_f(
    frame: pd.DataFrame,
    *,
    supply_col: str = "CWS_Temp",
    return_col: str = "CWR_Temp",
) -> pd.Series:
    """Condenser-water **range** (°F): ``CW_return - CW_supply``, the rise across the condenser.

    One subtraction, but the sign convention matters and more than one diagnostic depends on it, so
    it lives in one place. This module uses it as an "is the tower actually rejecting heat?" gate;
    :mod:`camber.rules.chiller_cw_range_rule` fits a load-normalized baseline to it and scores its
    drift, which is the condenser-side *hydraulic* signal (range is inversely proportional to
    condenser-water flow at matched load).

    Raises :class:`KeyError` if either column is absent -- range cannot be derived from one end.
    """
    missing = [c for c in (supply_col, return_col) if c not in frame.columns]
    if missing:
        raise KeyError(f"cw_range_f needs column(s) {missing}")
    return frame[return_col] - frame[supply_col]


SEA_LEVEL_PSIA = 14.696  # standard atmosphere, 101.325 kPa
_KPA_PER_PSI = 6.894757


def pressure_psia_at_elevation(elevation_ft) -> float:
    """Standard-atmosphere barometric pressure (psia) at ``elevation_ft`` (ASHRAE Fundamentals)."""
    z_m = float(elevation_ft) * 0.3048
    return SEA_LEVEL_PSIA * (1.0 - 2.25577e-5 * z_m) ** 5.2559


def _pws_kpa(t_c):
    """Saturation vapour pressure over liquid water, kPa (Alduchov-Eskridge Magnus form)."""
    return 0.61094 * np.exp(17.625 * t_c / (t_c + 243.04))


def psychrometric_wetbulb_f(oat_f, rh_pct, pressure_psia=SEA_LEVEL_PSIA):
    """Thermodynamic wet-bulb (°F) at a given barometric pressure (ASHRAE Fundamentals ch. 1).

    Solves the psychrometric wet-bulb equation for the humidity ratio implied by dry-bulb, RH and
    pressure, by vectorized bisection -- no external dependency. Used here to carry Stull's
    sea-level approximation to altitude; it is also a usable wet-bulb in its own right.
    """
    t = (np.asarray(oat_f, dtype=float) - 32.0) / 1.8
    rh = np.clip(np.asarray(rh_pct, dtype=float), 0.0, 100.0) / 100.0
    p = float(pressure_psia) * _KPA_PER_PSI
    t, rh = np.broadcast_arrays(t, rh)
    pw = rh * _pws_kpa(t)
    w = 0.621945 * pw / (p - pw)

    def w_from_tw(tw):
        pws = _pws_kpa(tw)
        ws = 0.621945 * pws / (p - pws)
        return ((2501.0 - 2.326 * tw) * ws - 1.006 * (t - tw)) / (2501.0 + 1.86 * t - 4.186 * tw)

    lo = t - 60.0
    hi = t.copy()
    for _ in range(50):  # bisection to well under 0.001 °C
        mid = 0.5 * (lo + hi)
        above = w_from_tw(mid) > w
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    tw = 0.5 * (lo + hi)
    out = tw * 1.8 + 32.0
    return out.item() if out.ndim == 0 else out


def _pressure_psia(elevation_ft, pressure_psia):
    if pressure_psia is not None:
        return float(pressure_psia)
    if elevation_ft is not None:
        return pressure_psia_at_elevation(elevation_ft)
    return None


def stull_wetbulb_f(oat_f, rh_pct, *, elevation_ft=None, pressure_psia=None):
    """Wet-bulb (°F) from dry-bulb (°F) and RH (%) via Stull's 2011 approximation.

    Valid for roughly 5-99% RH **at sea level**; accurate to ~±1 °F across typical HVAC
    conditions there. Vectorized over pandas Series / numpy arrays.

    At altitude a sea-level wet-bulb reads high (in hot, dry air ≈+1.4 °F at 500 m and +2.6 °F at
    1600 m in total, of which ~0.3-0.8 / ~1.8-2.5 °F is the pressure effect). Pass the site
    ``elevation_ft`` (standard atmosphere) or a measured barometric ``pressure_psia`` and the
    wet-bulb is instead solved psychrometrically at that pressure (:func:`psychrometric_wetbulb_f`),
    which removes both the pressure bias and Stull's fit error. Neither given = the sea-level
    Stull default, exactly as before.
    """
    p = _pressure_psia(elevation_ft, pressure_psia)
    if p is None:
        return _stull_sea_level_f(oat_f, rh_pct)
    out = psychrometric_wetbulb_f(oat_f, rh_pct, p)
    return out


def _stull_sea_level_f(oat_f, rh_pct):
    t = (np.asarray(oat_f, dtype=float) - 32.0) / 1.8  # -> °C
    rh = np.asarray(rh_pct, dtype=float)
    tw_c = (
        t * np.arctan(0.151977 * np.sqrt(rh + 8.313659))
        + np.arctan(t + rh)
        - np.arctan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh)
        - 4.686035
    )
    return tw_c * 1.8 + 32.0  # -> °F


@dataclass
class CoolingTowerResult:
    """Cooling-tower approach over operating hours vs the design approach."""

    equip: str
    n_operating: int  # intervals the tower is rejecting heat
    approach_median_f: float  # median (CW supply - wet-bulb) over operating hours
    range_median_f: float  # median (CW return - CW supply), NaN if no return temp
    wetbulb_source: str  # "measured" | "derived" (from OAT + RH)
    pct_hours_high_approach: float  # % operating hrs above design + margin
    design_approach_f: float
    coverage_start: str
    coverage_end: str
    effort_gated: bool | None = None  # True = judged only at high fan effort; None = no fan trend
    n_low_effort_excluded: int | None = None  # fan-on hours below the effort gate (not judged)

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_cooling_tower_approach(
    df: pd.DataFrame,
    equip: str,
    *,
    design_approach_f: float = 7.0,  # tower/climate-specific -- SET to the schedule
    high_margin_f: float = 3.0,  # approach above design+margin == high
    min_range_f: float = 2.0,  # CW range below this == not really rejecting heat
    min_fan_pct: float = 5.0,  # tower fan above this == operating (if available)
    min_effort_pct: float | None = 90.0,  # judge approach only at/above this fan speed
    elevation_ft: float | None = None,  # site elevation for a derived wet-bulb (None = sea level)
    pressure_psia: float | None = None,  # or a measured barometric pressure
) -> CoolingTowerResult | None:
    """Compute tower approach from CW supply temp and wet-bulb (measured or derived).

    Expects legacy column ``CWS_Temp`` and either ``WetBulb`` or both ``OAT`` and
    ``RH`` (to derive wet-bulb). Optional ``CWR_Temp`` gives the range and gates
    "operating"; ``TowerFanSpeed`` gates operating when present. ``design_approach_f``
    is the equipment-specific judgment; the floors are stability guards.

    **Approach is judged at high fan effort** (``TowerFanSpeed >= min_effort_pct``) when the fan
    is trended. A tower's capability is its approach at full fan (CTI rating practice); at part
    fan the approach is whatever the controller chose. That matters in cold weather: plants hold
    a *minimum* condenser-water temperature (commonly ~60 F), so the tower deliberately leaves
    water well above wet-bulb + design with its fan at minimum -- a high approach that is correct
    control, not a defect. Judging those hours fired the rule on healthy towers. Returns ``None``
    when the tower never reached the effort gate (nothing to judge). ``min_effort_pct=None``
    restores the old "fan running" gate.

    A derived wet-bulb assumes sea level unless ``elevation_ft`` / ``pressure_psia`` is given (see
    :func:`stull_wetbulb_f`); at altitude the uncorrected approach reads low.
    """
    if "CWS_Temp" not in df.columns:
        return None
    work = df.copy()
    work = work[~work.index.duplicated(keep="first")]  # defensive: reindex needs a unique index
    # wet-bulb: prefer a measured point, else derive from dry-bulb + RH
    if "WetBulb" in work.columns:
        wb = work["WetBulb"]
        wb_source = "measured"
    elif "OAT" in work.columns and "RH" in work.columns:
        wb = pd.Series(
            stull_wetbulb_f(
                work["OAT"], work["RH"], elevation_ft=elevation_ft, pressure_psia=pressure_psia
            ),
            index=work.index,
        )
        wb_source = "derived"
    else:
        return None
    work = work.assign(_wb=wb)

    cols = ["CWS_Temp", "_wb"] + [c for c in ("CWR_Temp",) if c in work.columns]
    w = work[cols].dropna()
    # plausibility guards
    w = w[(w.CWS_Temp.between(40, 120)) & (w._wb.between(10, 95))]
    if "CWR_Temp" in w.columns:
        w = w[w.CWR_Temp.between(40, 130)]

    # "operating": fan running if we have it, else real heat rejection (CW range)
    effort_gated = None
    n_low_effort = None
    if "TowerFanSpeed" in work.columns:
        fan = work["TowerFanSpeed"].reindex(w.index)
        w = w[fan > min_fan_pct]
        if min_effort_pct is not None:
            hard = fan.reindex(w.index) >= min_effort_pct
            n_low_effort = int((~hard).sum())
            w = w[hard]
            effort_gated = True
    elif "CWR_Temp" in w.columns:
        w = w[cw_range_f(w) >= min_range_f]
    if len(w) < 10:
        return None

    approach = (w.CWS_Temp - w._wb).clip(lower=-2)  # can't beat wet-bulb (allow noise)
    rng = cw_range_f(w) if "CWR_Temp" in w.columns else None
    high = float((approach > design_approach_f + high_margin_f).mean())

    return CoolingTowerResult(
        equip=equip,
        n_operating=int(len(approach)),
        approach_median_f=round(float(approach.median()), 1),
        range_median_f=round(float(rng.median()), 1) if rng is not None else float("nan"),
        wetbulb_source=wb_source,
        pct_hours_high_approach=round(100.0 * high, 1),
        design_approach_f=float(design_approach_f),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        effort_gated=effort_gated,
        n_low_effort_excluded=n_low_effort,
    )
