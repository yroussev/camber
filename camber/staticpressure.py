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

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import effective_occupied_mask, occupied_mask

__all__ = [
    "DamperCensusResult",
    "damper_census",
    "StaticResetResult",
    "analyze_static_reset",
]


@dataclass
class DamperCensusResult:
    """Fleet VAV damper-position census with a static-pressure verdict."""

    n_boxes: int
    n_intervals: int
    median_damper_pct: float  # fleet median damper position (occupied)
    pct_boxes_low: float  # share of boxes whose median is below low band
    pct_boxes_high: float  # share whose median is at/above high band
    pct_boxes_in_band: float  # share in the healthy mid band
    verdict: str
    coverage_start: str
    coverage_end: str
    #: which samples were judged: ``"trended occupancy"`` (every box read its own trended
    #: occupancy point), ``"assumed schedule (weekdays 07-18)"`` (none did), ``"mixed"`` (some
    #: boxes each way) or ``"off"`` (``occupied_only=False``: every sample). Added 0.98 (#84).
    occupancy_gate: str = "assumed schedule (weekdays 07-18)"

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


#: the occupancy-gate labels (shared with the rules that report which samples they judged)
_OCC_TRENDED = "trended occupancy"
_OCC_SCHEDULE = "assumed schedule (weekdays 07-18)"


def _flag(df: pd.DataFrame, col: str):
    return df[col] if col in df.columns else None


def damper_census(
    box_frames: dict,
    *,
    low_band: float = 50.0,
    high_band: float = 90.0,
    occupied_only: bool = True,
    use_trended_occupancy: bool = True,
) -> DamperCensusResult | None:
    """Aggregate VAV damper positions across many boxes.

    ``box_frames``: {equip -> DataFrame with a 'Damper' column}. Bands are OUR
    judgment (PNNL guidance ~50-75% healthy): a box whose *median occupied* damper
    is below ``low_band`` is "throttling" (suggests static too high); at/above
    ``high_band`` is "starved" (static too low).

    Occupied samples (``occupied_only``): a box's own trended ``'Occupancy'`` column, when it has
    any non-null value and ``use_trended_occupancy`` is on, replaces the assumed weekday 07-18
    schedule (so a trended weekend or evening occupancy is judged, 0.98 #84); otherwise the
    schedule applies. ``'WarmUp'`` / ``'CoolDown'`` flags, when present, drop prep-mode samples
    either way. The result's ``occupancy_gate`` says which applied.
    """
    box_medians = []
    n_int = 0
    start = end = None
    gates: set = set()
    for _equip, df in box_frames.items():
        if "Damper" not in df.columns:
            continue
        s = df["Damper"]
        if occupied_only:
            occ = _flag(df, "Occupancy") if use_trended_occupancy else None
            trended = occ is not None and occ.notna().any()
            gates.add(_OCC_TRENDED if trended else _OCC_SCHEDULE)
            mask = effective_occupied_mask(
                s.index,
                occ=occ if trended else None,
                warmup=_flag(df, "WarmUp"),
                cooldown=_flag(df, "CoolDown"),
            )
            s = s[mask.to_numpy(dtype=bool)]
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
        occupancy_gate=(
            "off" if not occupied_only else gates.pop() if len(gates) == 1 else "mixed"
        ),
    )


@dataclass
class StaticResetResult:
    """Duct static-pressure setpoint reset diagnostics for one AHU."""

    equip: str
    n_considered: int
    sp_median: float
    sp_std: float
    sp_reset_present: bool  # setpoint varies materially => some reset
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_static_reset(
    df: pd.DataFrame,
    equip: str,
    *,
    sp_flat_std: float = 0.05,  # SP std (in. w.c.) below this == flat (no reset)
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
