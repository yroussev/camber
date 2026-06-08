"""Static-pressure reset + VAV damper-distribution census (PNNL Ch.5/Ch.7).

Two related supply-air-side faults:

1. **Damper distribution.** In a well-set-up VAV system most box dampers ride in a
   mid band (~50-75% open). If most dampers sit near-closed, duct static is too
   high (boxes throttle hard to shed it -- wasted fan energy, cube-law). If several
   are pinned ~100%, static is too low (starved boxes). This census aggregates all
   box damper positions and reports where the fleet sits.

2. **Static-pressure reset.** A flat duct-static setpoint means no reset; a good
   sequence trims the setpoint down at low demand. Reported per AHU from the
   duct-static setpoint's variation.

Damper census is a *fleet* diagnostic (all boxes at once); static-SP flatness is
per-AHU.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask


@dataclass
class DamperCensusResult:
    """Fleet VAV damper-position census with a static-pressure verdict."""

    n_boxes: int
    n_intervals: int
    median_damper_pct: float       # fleet median damper position (occupied)
    pct_boxes_low: float           # share of boxes whose median is below low band
    pct_boxes_high: float          # share whose median is at/above high band
    pct_boxes_in_band: float       # share in the healthy mid band
    verdict: str
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def damper_census(
    box_frames: dict,
    *,
    low_band: float = 50.0,
    high_band: float = 90.0,
    occupied_only: bool = True,
) -> DamperCensusResult | None:
    """Aggregate VAV damper positions across many boxes.

    ``box_frames``: {equip -> DataFrame with a 'Damper' column}. Bands are OUR
    judgment (PNNL guidance ~50-75% healthy): a box whose *median occupied* damper
    is below ``low_band`` is "throttling" (suggests static too high); at/above
    ``high_band`` is "starved" (static too low).
    """
    box_medians = []
    n_int = 0
    start = end = None
    for equip, df in box_frames.items():
        if "Damper" not in df.columns:
            continue
        s = df["Damper"]
        if occupied_only:
            s = s[occupied_mask(s.index)]
        s = s.dropna()
        if s.empty:
            continue
        box_medians.append(float(s.median()))
        n_int = max(n_int, len(s))
        start = s.index.min() if start is None else min(start, s.index.min())
        end = s.index.max() if end is None else max(end, s.index.max())
    if not box_medians:
        return None

    n = len(box_medians)
    ser = pd.Series(box_medians)
    pct_low = round(100.0 * float((ser < low_band).mean()), 1)
    pct_high = round(100.0 * float((ser >= high_band).mean()), 1)
    pct_band = round(100.0 - pct_low - pct_high, 1)
    fleet_median = round(float(ser.median()), 1)

    if pct_low >= 60.0:
        verdict = "static likely TOO HIGH (most dampers throttling low)"
    elif pct_high >= 25.0:
        verdict = "static likely TOO LOW (boxes pinned open / starved)"
    else:
        verdict = "damper distribution healthy"

    return DamperCensusResult(
        n_boxes=n,
        n_intervals=n_int,
        median_damper_pct=fleet_median,
        pct_boxes_low=pct_low,
        pct_boxes_high=pct_high,
        pct_boxes_in_band=pct_band,
        verdict=verdict,
        coverage_start=str(start),
        coverage_end=str(end),
    )


@dataclass
class StaticResetResult:
    """Duct static-pressure setpoint reset diagnostics for one AHU."""

    equip: str
    n_considered: int
    sp_median: float
    sp_std: float
    sp_reset_present: bool         # setpoint varies materially => some reset
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_static_reset(
    df: pd.DataFrame,
    equip: str,
    *,
    sp_flat_std: float = 0.05,     # SP std (in. w.c.) below this == flat (no reset)
    occupied_only: bool = True,
) -> StaticResetResult | None:
    """Is the duct-static setpoint reset, or held flat? ``df`` has 'DuctStaticSP'."""
    if "DuctStaticSP" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    sp = work["DuctStaticSP"].dropna()
    sp = sp[sp > 0]  # drop off-hours zeros
    if len(sp) < 10:
        return None
    std = float(sp.std())
    return StaticResetResult(
        equip=equip,
        n_considered=int(len(sp)),
        sp_median=round(float(sp.median()), 2),
        sp_std=round(std, 3),
        sp_reset_present=bool(std >= sp_flat_std),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
