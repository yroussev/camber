"""Chiller staging / cycling diagnostic: starts-per-day and low part-load runtime.

Two staging-health symptoms readable from a single chiller's trend:

1. **Short-cycling** -- too many starts per day. Each compressor start is mechanical
   wear and an efficiency hit (unloaded inrush, oil migration); chronic cycling means
   the chiller is oversized for the load, the staging thresholds are too tight, or a
   downstream control is hunting.
2. **Low part-load operation** -- running, but persistently at a small fraction of the
   chiller's observed peak output. A lead chiller that idles at 20-30% load for most
   of the season is a staging/sizing problem: a smaller machine (or a single chiller
   instead of two) would carry it far more efficiently.

Both follow PNNL Re-tuning / ASHRAE chiller-plant guidance. True multi-chiller
staging *optimization* (how many of N machines to run for a given load) is a
fleet-level question; this single-equipment rule catches the cycling and idling that
show up on one machine. Trend resolution bounds the start count -- sub-interval cycles
are invisible -- so starts-per-day is a floor, reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd


@dataclass
class ChillerStagingResult:
    """Chiller cycling rate and part-load distribution over the trend window."""

    equip: str
    n_days: float                  # observed span in days
    starts_per_day: float          # off->on transitions per day (a floor; see module doc)
    runtime_pct: float             # % of intervals the chiller is running
    load_factor_median_pct: float  # median load as % of observed peak (NaN if no load data)
    low_load_pct: float            # % of running hrs below min_load_pct (NaN if no load data)
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_chiller_staging(
    df: pd.DataFrame,
    equip: str,
    *,
    min_power_kw: float = 2.0,      # power above this == running
    min_load_pct: float = 40.0,     # load factor below this == "low part-load"
) -> ChillerStagingResult | None:
    """Compute cycling rate and part-load distribution from metered power (+ optional load).

    Expects legacy column ``Power`` (kW); if ``CHWS_Temp``/``CHWR_Temp``/``CHW_Flow``
    are present, load factor (tons vs observed peak) is added. ``min_load_pct`` is the
    judgment knob; ``min_power_kw`` is the on/off floor.
    """
    if "Power" not in df.columns:
        return None
    w = df[[c for c in ("Power", "CHWS_Temp", "CHWR_Temp", "CHW_Flow")
            if c in df.columns]].dropna(subset=["Power"])
    if len(w) < 10:
        return None
    span_days = (w.index.max() - w.index.min()).total_seconds() / 86400.0
    span_days = max(span_days, 1.0)

    running = w.Power >= min_power_kw
    starts = int((running & ~running.shift(1, fill_value=False)).sum())

    load_factor_median = float("nan")
    low_load = float("nan")
    if {"CHWS_Temp", "CHWR_Temp", "CHW_Flow"} <= set(w.columns):
        run = w[running]
        tons = (run.CHW_Flow * (run.CHWR_Temp - run.CHWS_Temp) / 24.0)
        tons = tons[tons > 0]
        if len(tons) >= 10:
            peak = float(tons.max())
            lf = 100.0 * tons / peak if peak > 0 else tons * 0
            load_factor_median = round(float(lf.median()), 1)
            low_load = round(100.0 * float((lf < min_load_pct).mean()), 1)

    return ChillerStagingResult(
        equip=equip,
        n_days=round(span_days, 1),
        starts_per_day=round(starts / span_days, 2),
        runtime_pct=round(100.0 * float(running.mean()), 1),
        load_factor_median_pct=load_factor_median,
        low_load_pct=low_load,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )


@dataclass
class ChillerFleetStagingResult:
    """Plant-level staging: how often more chillers run than the load requires."""

    n_chillers: int
    n_running_hours: int          # intervals with >= 1 chiller running
    n_multi_hours: int            # intervals with >= 2 chillers running
    pct_overstaged: float         # % of multi-chiller hours that could drop a chiller
    median_running_count: float   # median number of chillers running (running hours)
    rep_capacity_kw: float        # representative per-chiller capacity used

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_chiller_staging_fleet(
    frames: dict,
    *,
    min_power_kw: float = 2.0,         # power above this == chiller running
    redundancy_ceiling: float = 0.9,   # a single chiller can carry up to ceiling*capacity
) -> ChillerFleetStagingResult | None:
    """Detect over-staging across a set of chillers from their power trends.

    ``frames`` is ``{equip: df}`` with a ``Power`` (kW) column each. Power is the
    staging load proxy (a chiller's draw scales with its cooling load); per-chiller
    capacity is its observed peak power. At each interval, the plant is *over-staged*
    when two or more chillers run but the total draw would fit in one fewer machine
    (total <= (n_running - 1) * capacity * ceiling) -- a redundant chiller that should
    be staged off (PNNL Re-tuning Ch.8 / ASHRAE chiller-plant staging).
    """
    running_cols, load_cols, caps = {}, {}, []
    for equip, df in frames.items():
        if df is None or "Power" not in df.columns:
            continue
        p = df["Power"].dropna()
        if len(p) < 10:
            continue
        run = p >= min_power_kw
        if not bool(run.any()):
            continue
        running_cols[equip] = run
        load_cols[equip] = p.where(run, 0.0)
        caps.append(float(p[run].quantile(0.95)))   # robust peak (capacity)
    if len(running_cols) < 2:
        return None

    running = pd.DataFrame(running_cols).fillna(False)
    load = pd.DataFrame(load_cols).reindex(running.index).fillna(0.0)
    n_running = running.sum(axis=1)
    total_load = load.sum(axis=1)
    cap_rep = float(pd.Series(caps).median())

    any_run = n_running >= 1
    multi = n_running >= 2
    overstaged = multi & (total_load <= (n_running - 1) * cap_rep * redundancy_ceiling)
    n_multi = int(multi.sum())

    return ChillerFleetStagingResult(
        n_chillers=len(running_cols),
        n_running_hours=int(any_run.sum()),
        n_multi_hours=n_multi,
        pct_overstaged=round(100.0 * int(overstaged.sum()) / n_multi, 1) if n_multi else 0.0,
        median_running_count=round(float(n_running[any_run].median()), 1)
        if bool(any_run.any()) else 0.0,
        rep_capacity_kw=round(cap_rep, 1),
    )
