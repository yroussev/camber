"""Supply-air-temperature (SAT) reset diagnostic.

The AHU export has actual supply-air temp (``SupplyAir``) but no SAT *setpoint*
trend, so we can't compare to a setpoint directly. Instead we test whether SAT
is being *reset against load* the way a good sequence would:

  - In a building with SAT reset, supply-air temp RISES when cooling demand
    falls (cooler OAT / lower CHW valve) and FALLS when demand is high. So SAT
    should show a meaningful negative correlation with OAT-driven load and a
    healthy spread (several °F of range).
  - The failure mode is the opposite: SAT pinned low (~55 °F) at all times, so
    the plant overcools continuously and the boxes reheat. That shows up as a
    NEAR-ZERO slope of SAT vs OAT and a TIGHT, LOW SAT distribution.

Outputs let us say, quantitatively, "SAT is held at X ±Y °F regardless of OAT
(slope = Z °F per °F OAT), i.e. essentially no reset" -- the root-cause lever
behind the reheat penalty.

Method note: we fit an ordinary least-squares line SAT ~ a + b*OAT over occupied
cooling-mode hours (CHW valve open). |b| small AND sat_std small => no reset.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask

SATRESET_MEASURES = ["SupplyAir", "CHW_Valve", "OSA", "Occupancy", "WarmUp", "CoolDown"]


def _populated(df, col):
    """Return df[col] only if present and not entirely null, else None."""
    if col in df.columns and df[col].notna().any():
        return df[col]
    return None


@dataclass
class SATResetResult:
    """Supply-air-temperature reset diagnostics: SAT-vs-OAT slope and low-SAT pinning."""

    equip: str
    n_considered: int
    sat_median: float
    sat_p05: float
    sat_p95: float
    sat_std: float
    slope_per_F: float        # d(SAT)/d(OAT); ~0 means no reset
    r2: float
    pct_sat_below_58: float   # % of cooling hours SAT pinned low (<58 F)
    verdict: str
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _ols(x: np.ndarray, y: np.ndarray):
    """Return (slope, intercept, r2) for y ~ a + b x; (nan,nan,nan) if degenerate."""
    if len(x) < 10 or np.std(x) == 0:
        return float("nan"), float("nan"), float("nan")
    b, a = np.polyfit(x, y, 1)
    yhat = a + b * x
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(b), float(a), float(r2)


def analyze_satreset(df, equip, *, oat=None, valve_thr=5.0, occupied_only=True,
                     low_sat_f=58.0, slope_flat=0.10, spread_tight=3.0):
    """Diagnose SAT reset behavior for one AHU. ``df`` columns are measure names.

    Verdict cutoffs are OUR diagnostic thresholds (judgment, not a standard); the
    regression itself is standard OLS:
      low_sat_f=58 F     -- "cold" supply air; % of cooling hours below this shows how
                            dominant cold supply is (the precondition for reheat).
      slope_flat=0.10    -- |d(SAT)/d(OAT)| below ~0.1 F/F is effectively no reset; a
                            real upward reset is several times steeper.
      spread_tight=3.0 F -- SAT std below ~3F means SAT is held in a narrow band, i.e.
                            not modulating with load. flat slope AND tight band => pinned.
    """
    if "SupplyAir" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(
            work.index,
            occ=_populated(work, "Occupancy"),
            warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )]

    # cooling-mode hours only: SAT reset is about cooling supply temp
    if "CHW_Valve" in work.columns:
        work = work[work["CHW_Valve"] > valve_thr]

    sat = work["SupplyAir"].dropna()
    # plausible SAT range guard (drop sensor dropouts)
    sat = sat[(sat > 40) & (sat < 90)]
    if len(sat) < 10:
        return None

    oat_series = oat if oat is not None else (work["OSA"] if "OSA" in work.columns else None)
    if oat_series is not None:
        oa = oat_series.reindex(sat.index).ffill(limit=4)
        m = oa.notna() & sat.notna()
        slope, _, r2 = _ols(oa[m].values, sat[m].values)
    else:
        slope, r2 = float("nan"), float("nan")

    sat_std = float(sat.std())
    pct_low = round(100.0 * float((sat < low_sat_f).mean()), 1)

    flat = (not np.isnan(slope)) and abs(slope) < slope_flat
    tight = sat_std < spread_tight
    if flat and tight:
        verdict = "NO RESET (SAT pinned low regardless of OAT)"
    elif flat:
        verdict = "WEAK/NO RESET (flat SAT vs OAT)"
    elif slope > 0:
        verdict = "RESET PRESENT (SAT rises with OAT)"
    else:
        verdict = "INVERSE (SAT falls as OAT rises — load tracking)"

    return SATResetResult(
        equip=equip,
        n_considered=int(len(sat)),
        sat_median=round(float(sat.median()), 1),
        sat_p05=round(float(sat.quantile(0.05)), 1),
        sat_p95=round(float(sat.quantile(0.95)), 1),
        sat_std=round(sat_std, 2),
        slope_per_F=round(slope, 3) if not np.isnan(slope) else float("nan"),
        r2=round(r2, 3) if not np.isnan(r2) else float("nan"),
        pct_sat_below_58=pct_low,
        verdict=verdict,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
