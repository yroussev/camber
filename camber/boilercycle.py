"""Boiler short-cycling diagnostic: firing starts per day from boiler status.

An oversized boiler (or one with too-tight staging/aquastat hysteresis) fires in
short bursts: on, satisfy the loop, off, repeat. Each start is purge-cycle losses,
thermal stress, and lower seasonal efficiency. Counting off->on transitions in the
boiler-status trend gives a starts-per-day rate that flags the pattern (PNNL Building
Re-tuning Ch.8; boiler minimum-cycle-time guidance).

This counts cycles across the whole window (a boiler can cycle at any hour, not just
occupied ones). Trend resolution bounds the count -- sub-interval cycles are invisible
-- so starts-per-day is a floor, reported as such; the cycling threshold is
manufacturer-dependent and injected by the rule.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd


@dataclass
class BoilerCyclingResult:
    """Boiler firing rate over the trend window."""

    equip: str
    n_days: float                 # observed span in days
    starts_per_day: float         # off->on transitions per day (a floor; see module doc)
    runtime_pct: float            # % of intervals the boiler is firing
    n_starts: int
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_boiler_cycling(
    df: pd.DataFrame,
    equip: str,
) -> BoilerCyclingResult | None:
    """Count boiler firing starts per day from the ``BoilerStatus`` (0/1) trend."""
    if "BoilerStatus" not in df.columns:
        return None
    w = df[["BoilerStatus"]].dropna()
    if len(w) < 10:
        return None
    span_days = (w.index.max() - w.index.min()).total_seconds() / 86400.0
    span_days = max(span_days, 1.0)

    running = w["BoilerStatus"] > 0.5
    starts = int((running & ~running.shift(1, fill_value=False)).sum())

    return BoilerCyclingResult(
        equip=equip,
        n_days=round(span_days, 1),
        starts_per_day=round(starts / span_days, 2),
        runtime_pct=round(100.0 * float(running.mean()), 1),
        n_starts=starts,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
