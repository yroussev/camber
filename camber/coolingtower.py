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
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "stull_wetbulb_f",
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
) -> pd.Series:
    """Cooling-tower **approach** (°F): ``CW_supply - wet_bulb`` (CW leaving the tower).

    The gap the tower actually achieves toward ambient wet-bulb — the same subtraction
    :func:`analyze_cooling_tower_approach` scores as a level, exposed here as a series so
    :mod:`camber.rules.coolingtower_drift_rule` can fit a load-normalized baseline and track its
    **drift** (a widening approach at matched load is the fouling/scale/reduced-airflow signal).

    Wet-bulb is taken from ``wetbulb_col`` when present, else derived from ``oat_col`` + ``rh_col``
    via :func:`stull_wetbulb_f` (no psychrometric dependency). Raises :class:`KeyError` if it has
    neither a supply temperature nor any wet-bulb source — an approach needs both ends.
    """
    if supply_col not in frame.columns:
        raise KeyError(f"tower_approach_f needs column {supply_col!r}")
    if wetbulb_col in frame.columns:
        wb = frame[wetbulb_col]
    elif oat_col in frame.columns and rh_col in frame.columns:
        wb = pd.Series(stull_wetbulb_f(frame[oat_col], frame[rh_col]), index=frame.index)
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


def stull_wetbulb_f(oat_f, rh_pct):
    """Wet-bulb (°F) from dry-bulb (°F) and RH (%) via Stull's 2011 approximation.

    Valid for roughly 5-99% RH at sea level; accurate to ~±1 °F across typical HVAC
    conditions. Vectorized over pandas Series / numpy arrays.
    """
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
) -> CoolingTowerResult | None:
    """Compute tower approach from CW supply temp and wet-bulb (measured or derived).

    Expects legacy column ``CWS_Temp`` and either ``WetBulb`` or both ``OAT`` and
    ``RH`` (to derive wet-bulb). Optional ``CWR_Temp`` gives the range and gates
    "operating"; ``TowerFanSpeed`` gates operating when present. ``design_approach_f``
    is the equipment-specific judgment; the floors are stability guards.
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
        wb = pd.Series(stull_wetbulb_f(work["OAT"], work["RH"]), index=work.index)
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
    if "TowerFanSpeed" in work.columns:
        fan = work["TowerFanSpeed"].reindex(w.index)
        w = w[fan > min_fan_pct]
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
    )
