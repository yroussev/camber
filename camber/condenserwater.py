"""Condenser-water reset diagnostic: does CW supply temp float down with wet-bulb?

A tower can make colder condenser water whenever the ambient wet-bulb drops, and
colder condenser water lowers chiller lift -- roughly 1-2% chiller kW per °F of
condenser-water temperature. A good plant therefore *resets* the condenser-water
(tower) setpoint to track wet-bulb plus the design approach, instead of holding a
fixed high setpoint year-round. The trade is tower-fan energy, but on most chillers
the compressor savings dominate (ASHRAE 90.1 / chiller-plant optimization guidance).

We test for the reset by regressing CW supply temp on wet-bulb over operating hours:
a working reset has a clearly positive slope (CW supply falls as wet-bulb falls); a
flat slope means the setpoint is held constant -- a missed-efficiency opportunity, not
a hard fault. Wet-bulb is measured if mapped, else derived from OAT + RH (Stull).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .coolingtower import stull_wetbulb_f


@dataclass
class CondenserWaterResetResult:
    """Condenser-water supply-temp reset (slope vs wet-bulb) over operating hours."""

    equip: str
    n_operating: int
    cws_median_f: float
    cws_slope_per_wetbulb: float   # d(CW supply)/d(wet-bulb); ~1 = full reset, ~0 = none
    reset_present: bool
    wetbulb_source: str            # "measured" | "derived"
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _ols_slope(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 10 or np.std(x) == 0:
        return float("nan")
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def analyze_cw_reset(
    df: pd.DataFrame,
    equip: str,
    *,
    reset_slope_flat: float = 0.3,   # CWS/wet-bulb slope below this = effectively no reset
    min_range_f: float = 2.0,        # CW range below this == not rejecting heat
    min_fan_pct: float = 5.0,        # tower fan above this == operating (if available)
) -> CondenserWaterResetResult | None:
    """Regress CW supply temp on wet-bulb over operating hours to detect a reset.

    Expects legacy column ``CWS_Temp`` and either ``WetBulb`` or ``OAT`` + ``RH``.
    Optional ``CWR_Temp``/``TowerFanSpeed`` gate "operating". ``reset_slope_flat`` is
    the judgment knob (an ideal reset tracks wet-bulb ~1:1).
    """
    if "CWS_Temp" not in df.columns:
        return None
    work = df.copy()
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
    w = w[(w.CWS_Temp.between(40, 120)) & (w._wb.between(10, 95))]
    if "TowerFanSpeed" in work.columns:
        w = w[work["TowerFanSpeed"].reindex(w.index) > min_fan_pct]
    elif "CWR_Temp" in w.columns:
        w = w[(w.CWR_Temp - w.CWS_Temp) >= min_range_f]
    if len(w) < 10:
        return None

    slope = _ols_slope(w._wb.values, w.CWS_Temp.values)
    reset_present = (not np.isnan(slope)) and slope >= reset_slope_flat

    return CondenserWaterResetResult(
        equip=equip,
        n_operating=int(len(w)),
        cws_median_f=round(float(w.CWS_Temp.median()), 1),
        cws_slope_per_wetbulb=round(slope, 3) if not np.isnan(slope) else float("nan"),
        reset_present=bool(reset_present),
        wetbulb_source=wb_source,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
