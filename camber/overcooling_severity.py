"""Std-55-aligned overcooling *severity* diagnostic (depth x duration).

This is a **different axis** from :mod:`camber.overcooling`. That module scores the
*frequency* of a min-flow root cause ("satisfied on cooling, pinned at minimum
airflow with reheat") -- an airflow/ECM metric. This one scores the *comfort
severity* of overcooling: how far the space sits **below its cooling setpoint** and
for **how long**. A zone can score high on one and low on the other, so the two are
kept as separate, separately-named diagnostics (``overcooling_min_flow`` vs
``overcooling_severity``); merging them would conflate a comfort symptom with an
airflow cause.

Severity tiers (all configurable, default degF below the reference setpoint):
  - ``info``  >= 1 degF  -- INFORMATIONAL ONLY. Reported separately and never
                            counted in fault/headline totals (the rule emits it at
                            Finding severity ``"info"``, which the triage layer
                            treats as non-actionable).
  - ``warn``  >= 2 degF
  - ``fault`` >= 3 degF
Tiers are cumulative thresholds: a sample 3.5 degF below the reference qualifies for
all three; a zone's overall severity is the worst tier it *sustains* (see below).

Persistence (time-based, interval-aware):
  A tier is assigned only if the excursion is *sustained* for at least
  ``window`` (default 60 min). Persistence is evaluated over the series' real
  timestamps against its actual sampling interval (inferred as the median sample
  spacing, or a declared ``interval``), so it behaves correctly on 1/5/15/30/60-min
  data:
    - A run of consecutive qualifying samples counts as sustained when the run's
      contiguous time coverage reaches ``window`` (``span + interval >= window``).
    - Gaps break a run: two qualifying samples separated by more than
      ``gap_factor`` x interval are NOT joined, so a data gap cannot fake
      persistence.
    - When the sampling interval is >= the window (coarse / event-logged data) a
      single qualifying sample counts -- one 1-hour reading already spans an hour.

Reference setpoint (configurable via ``relative_to_deadband``):
  - **relative-to-deadband** (default when BOTH heating and cooling setpoints
    exist): overcooling is judged relative to the comfort deadband. A space sitting
    anywhere inside ``[heat_sp, cool_sp]`` is operating as designed and is not
    flagged; depth is measured below the *heating* setpoint
    (``depth = heat_sp - space_temp``), i.e. how far the space pushes past the
    bottom of the deadband. This is the conservative, deadband-aware reading of
    "pushed below the cooling setpoint toward/past the heating setpoint."
  - **absolute** (fallback when only the cooling setpoint is available, or when
    ``relative_to_deadband=False``): depth is measured below the cooling setpoint
    (``depth = cool_sp - space_temp``).

What is *not* overcooling (both found labelled as overcooling on real buildings):
  - **Morning recovery.** A space still climbing back from its night setback in the
    first hours of occupancy is recovering, not being overcooled. Warm-up samples are
    excluded -- by the ``WarmUp`` flag when it is trended, otherwise the first
    ``recovery_hours`` (default 2 h) of every occupied block that follows an
    unoccupied one.
  - **A heating shortfall.** A space below its heating setpoint while its reheat valve
    (``HWValve``) is at/above ``reheat_saturated_pct`` (default 90 %) is being heated
    as hard as the box can -- it is *under-heated*, not overcooled. Those samples are
    scored separately (``shortfall_*``) and never count toward the overcooling tiers.
    Without a reheat valve the two cannot be told apart (``reheat_evaluated=False``).
  - **HVAC off.** A space drifting cold while its terminal/air-handler fan is off
    (``FanStatus`` <= 0.5 or ``FanSpeed`` <= 5 %, when trended) is free-floating, not
    being overcooled; those samples are excluded (``n_fan_off_excluded``).

Occupied = a trended ``Occupancy`` column when present (it replaces the schedule),
else the ``start_hour``/``end_hour``/``occupied_days`` schedule (default weekday 07-18).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .schedules import effective_occupied_mask

__all__ = [
    "DEFAULT_TIERS",
    "OvercoolSeverityResult",
    "infer_interval",
    "analyze_overcooling_severity",
]

# Default severity tiers: degF below the reference setpoint.
DEFAULT_TIERS: dict = {"info": 1.0, "warn": 2.0, "fault": 3.0}
# Tier ordering, mildest -> worst (info is informational only).
_TIER_ORDER = ("info", "warn", "fault")


@dataclass
class OvercoolSeverityResult:
    """Depth x duration overcooling-severity summary for one zone."""

    equip: str
    n_considered: int
    mode: str  # "relative_deadband" | "absolute"
    interval_min: float  # inferred/declared sampling interval (minutes)
    window_min: float  # persistence window (minutes)
    median_depth_f: float  # median depth below the reference among overcooled samples
    max_depth_f: float  # deepest excursion below the reference (degF)
    tier_pct: dict  # tier -> % of considered samples in a SUSTAINED run
    tier_minutes: dict  # tier -> total sustained minutes at that tier
    tier_sustained: dict  # tier -> bool (any sustained run at all)
    severity: str  # worst SUSTAINED tier: "ok" | "info" | "warn" | "fault"
    coverage_start: str
    coverage_end: str
    n_recovery_excluded: int = 0  # morning-recovery samples dropped (heuristic window only)
    reheat_evaluated: bool = False  # a reheat valve was present to tell shortfall from overcool
    shortfall_tier_pct: dict | None = None  # tier -> % considered samples, sustained, reheat maxed
    shortfall_severity: str = "ok"  # worst sustained heating-shortfall tier
    n_fan_off_excluded: int = 0  # samples dropped because the fan was trended off

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def infer_interval(index: pd.DatetimeIndex) -> pd.Timedelta | None:
    """Median sample spacing of a DatetimeIndex (None if undeterminable)."""
    if len(index) < 2:
        return None
    deltas = pd.Series(index).diff().dropna()
    deltas = deltas[deltas > pd.Timedelta(0)]
    if deltas.empty:
        return None
    med = deltas.median()
    return med if med > pd.Timedelta(0) else None


def _sustained_mask(
    qualifies: np.ndarray,
    times: np.ndarray,
    window: pd.Timedelta,
    interval: pd.Timedelta,
    gap_factor: float = 1.5,
) -> np.ndarray:
    """Mark samples that belong to a qualifying run sustained for >= ``window``.

    ``qualifies`` is a boolean array; ``times`` the matching datetime64 values.
    Runs of consecutive qualifying samples are broken by a non-qualifying sample or
    by a time gap larger than ``gap_factor`` x ``interval`` (so a data gap cannot
    fake persistence). A run is sustained when its contiguous span plus one interval
    reaches the window. When the interval is >= the window, a single qualifying
    sample suffices.
    """
    out = np.zeros(len(qualifies), dtype=bool)
    if len(qualifies) == 0:
        return out
    if interval >= window:
        return qualifies.copy()

    w = np.timedelta64(window)
    iv = np.timedelta64(interval)
    gap_limit = np.timedelta64(int(interval.value * gap_factor), "ns")
    n = len(qualifies)
    i = 0
    while i < n:
        if not qualifies[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and qualifies[j + 1] and (times[j + 1] - times[j]) <= gap_limit:
            j += 1
        # run is samples [i .. j]; contiguous coverage = span + one interval
        if (times[j] - times[i]) + iv >= w:
            out[i : j + 1] = True
        i = j + 1
    return out


def _recovery_window(occ: pd.Series, recovery: pd.Timedelta) -> pd.Series:
    """Occupied samples within ``recovery`` of the start of an occupied block that follows an
    unoccupied sample (morning recovery from setback). The first block of the series counts only
    if it is preceded by an unoccupied sample, so data that begins mid-day is not trimmed."""
    occ = occ.astype(bool)
    prev = occ.shift(1)
    starts = occ & (prev == False)  # noqa: E712 -- NaN (series start) is not a transition
    start_at = pd.Series(occ.index.where(starts), index=occ.index)
    start_at = start_at.where(starts).ffill()
    # a block's start only applies while the block continues
    block = (~occ).cumsum()
    start_at = start_at.where(occ).groupby(block).transform("first")
    since = pd.Series(occ.index, index=occ.index) - start_at
    return occ & since.notna() & (since < recovery)


def analyze_overcooling_severity(
    df: pd.DataFrame,
    equip: str,
    *,
    tiers: dict | None = None,
    window_min: float = 60.0,
    interval: str | pd.Timedelta | None = None,
    relative_to_deadband: bool = True,
    gap_factor: float = 1.5,
    occupied_only: bool = True,
    start_hour: float = 7,
    end_hour: float = 18,
    occupied_days=(0, 1, 2, 3, 4),
    recovery_hours: float = 2.0,
    reheat_saturated_pct: float = 90.0,
) -> OvercoolSeverityResult | None:
    """Score overcooling severity (depth x duration) for one zone.

    ``df`` columns (legacy token names): ``SpaceTemp``, ``ActCoolSP`` (required),
    ``ActHeatSP`` (enables relative-to-deadband mode), and optionally ``WarmUp`` /
    ``CoolDown`` / ``Occupancy`` for occupancy and ``HWValve`` (reheat valve, %) to
    separate a heating shortfall from overcooling. Returns ``None`` if the required
    columns or any usable samples are missing. See the module docstring for tier,
    persistence, recovery/shortfall, and reference-setpoint semantics.
    """
    tiers = dict(tiers) if tiers else dict(DEFAULT_TIERS)
    if "SpaceTemp" not in df.columns or "ActCoolSP" not in df.columns:
        return None

    work = df.copy()
    n_recovery = 0
    if occupied_only:
        has_warmup = "WarmUp" in work.columns and work["WarmUp"].notna().any()
        occ = effective_occupied_mask(
            work.index,
            occ=work["Occupancy"] if "Occupancy" in work.columns else None,
            start_hour=start_hour,
            end_hour=end_hour,
            days=occupied_days,
            warmup=work["WarmUp"] if has_warmup else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )
        if not has_warmup and recovery_hours > 0:
            recovering = _recovery_window(occ, pd.Timedelta(hours=recovery_hours))
            n_recovery = int(recovering.sum())
            occ = occ & ~recovering
        work = work[occ]

    # HVAC off -> the space free-floats; that is not overcooling
    fan_off = pd.Series(False, index=work.index)
    if "FanStatus" in work.columns and work["FanStatus"].notna().any():
        fan_off = work["FanStatus"] <= 0.5
    elif "FanSpeed" in work.columns and work["FanSpeed"].notna().any():
        fan_off = work["FanSpeed"] <= 5.0
    n_fan_off = int(fan_off.sum())
    work = work[~fan_off]

    have_heat = "ActHeatSP" in work.columns
    use_relative = relative_to_deadband and have_heat
    needed = ["SpaceTemp", "ActCoolSP"] + (["ActHeatSP"] if use_relative else [])
    work = work.dropna(subset=needed)
    n = len(work)
    if n == 0:
        return None

    if use_relative:
        # depth past the bottom of the deadband (below the heating setpoint)
        depth = (work["ActHeatSP"] - work["SpaceTemp"]).to_numpy(dtype=float)
        mode = "relative_deadband"
    else:
        depth = (work["ActCoolSP"] - work["SpaceTemp"]).to_numpy(dtype=float)
        mode = "absolute"

    # sampling interval: declared wins, else inferred, else fall back to the window
    if interval is not None:
        iv = pd.Timedelta(interval)
    else:
        iv = infer_interval(work.index) or pd.Timedelta(minutes=window_min)
    window = pd.Timedelta(minutes=window_min)
    times = work.index.to_numpy()

    # reheat at/near full open: the space is being heated as hard as the box can -> a heating
    # shortfall, not overcooling (scored separately, never in the overcooling tiers)
    reheat_evaluated = "HWValve" in work.columns and work["HWValve"].notna().any()
    if reheat_evaluated:
        saturated = (work["HWValve"] >= reheat_saturated_pct).to_numpy(dtype=bool)
    else:
        saturated = np.zeros(n, dtype=bool)

    overcooled = (depth > 0) & ~saturated
    median_depth = float(np.median(depth[overcooled])) if overcooled.any() else 0.0
    max_depth = float(depth[~saturated].max()) if (~saturated).any() else 0.0

    tier_pct, tier_minutes, tier_sustained = {}, {}, {}
    shortfall_pct: dict = {}
    severity = "ok"
    shortfall_severity = "ok"
    iv_min = iv.total_seconds() / 60.0
    for tier in _TIER_ORDER:
        thr = tiers[tier]
        qualifies = (depth >= thr) & ~saturated
        sustained = _sustained_mask(qualifies, times, window, iv, gap_factor)
        cnt = int(sustained.sum())
        tier_sustained[tier] = bool(cnt)
        tier_pct[tier] = round(100.0 * cnt / n, 2) if n else 0.0
        tier_minutes[tier] = round(cnt * iv_min, 1)
        if cnt:
            severity = tier  # tiers iterate mild -> worst; worst sustained wins
        short = _sustained_mask((depth >= thr) & saturated, times, window, iv, gap_factor)
        s_cnt = int(short.sum())
        shortfall_pct[tier] = round(100.0 * s_cnt / n, 2) if n else 0.0
        if s_cnt:
            shortfall_severity = tier

    return OvercoolSeverityResult(
        equip=equip,
        n_considered=n,
        mode=mode,
        interval_min=round(iv_min, 3),
        window_min=float(window_min),
        median_depth_f=round(median_depth, 2),
        max_depth_f=round(max_depth, 2),
        tier_pct=tier_pct,
        tier_minutes=tier_minutes,
        tier_sustained=tier_sustained,
        severity=severity,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        n_recovery_excluded=n_recovery,
        n_fan_off_excluded=n_fan_off,
        reheat_evaluated=bool(reheat_evaluated),
        shortfall_tier_pct=shortfall_pct if reheat_evaluated else None,
        shortfall_severity=shortfall_severity,
    )
