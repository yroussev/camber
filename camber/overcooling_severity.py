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
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask

# Default severity tiers: degF below the reference setpoint.
DEFAULT_TIERS: dict = {"info": 1.0, "warn": 2.0, "fault": 3.0}
# Tier ordering, mildest -> worst (info is informational only).
_TIER_ORDER = ("info", "warn", "fault")


@dataclass
class OvercoolSeverityResult:
    """Depth x duration overcooling-severity summary for one zone."""

    equip: str
    n_considered: int
    mode: str                  # "relative_deadband" | "absolute"
    interval_min: float        # inferred/declared sampling interval (minutes)
    window_min: float          # persistence window (minutes)
    median_depth_f: float      # median depth below the reference among overcooled samples
    max_depth_f: float         # deepest excursion below the reference (degF)
    tier_pct: dict             # tier -> % of considered samples in a SUSTAINED run
    tier_minutes: dict         # tier -> total sustained minutes at that tier
    tier_sustained: dict       # tier -> bool (any sustained run at all)
    severity: str              # worst SUSTAINED tier: "ok" | "info" | "warn" | "fault"
    coverage_start: str
    coverage_end: str

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


def _sustained_mask(qualifies: np.ndarray, times: np.ndarray,
                    window: pd.Timedelta, interval: pd.Timedelta,
                    gap_factor: float = 1.5) -> np.ndarray:
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
        while (j + 1 < n and qualifies[j + 1]
               and (times[j + 1] - times[j]) <= gap_limit):
            j += 1
        # run is samples [i .. j]; contiguous coverage = span + one interval
        if (times[j] - times[i]) + iv >= w:
            out[i:j + 1] = True
        i = j + 1
    return out


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
) -> OvercoolSeverityResult | None:
    """Score overcooling severity (depth x duration) for one zone.

    ``df`` columns (legacy token names): ``SpaceTemp``, ``ActCoolSP`` (required),
    ``ActHeatSP`` (enables relative-to-deadband mode), and optionally ``WarmUp`` /
    ``CoolDown`` for occupancy. Returns ``None`` if the required columns or any
    usable samples are missing. See the module docstring for tier, persistence, and
    reference-setpoint semantics.
    """
    tiers = dict(tiers) if tiers else dict(DEFAULT_TIERS)
    if "SpaceTemp" not in df.columns or "ActCoolSP" not in df.columns:
        return None

    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(
            work.index,
            warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )]

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

    overcooled = depth > 0
    median_depth = float(np.median(depth[overcooled])) if overcooled.any() else 0.0
    max_depth = float(depth.max()) if n else 0.0

    tier_pct, tier_minutes, tier_sustained = {}, {}, {}
    severity = "ok"
    iv_min = iv.total_seconds() / 60.0
    for tier in _TIER_ORDER:
        thr = tiers[tier]
        qualifies = depth >= thr
        sustained = _sustained_mask(qualifies, times, window, iv, gap_factor)
        cnt = int(sustained.sum())
        tier_sustained[tier] = bool(cnt)
        tier_pct[tier] = round(100.0 * cnt / n, 2) if n else 0.0
        tier_minutes[tier] = round(cnt * iv_min, 1)
        if cnt:
            severity = tier        # tiers iterate mild -> worst; worst sustained wins

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
    )
