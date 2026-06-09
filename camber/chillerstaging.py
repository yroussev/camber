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
