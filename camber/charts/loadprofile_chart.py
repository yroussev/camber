"""Pattern F — load profiles & load-duration curves.

Base load and peaks drive cost but aren't obvious in a raw trend. Two views on the shape of a load:

- **Load profile** — average load by hour-of-day (weekday vs weekend), exposing the occupancy shape
  and schedule gaps.
- **Load-duration curve (LDC)** — every interval sorted high-to-low against the % of time it's
  exceeded; the area is energy, the left edge is the peak, the right shoulder is base load. With a
  ``price`` ($/kWh) it translates to an energy-cost figure.

Both annotate base load / peak / load factor from `camber.loadprofile`. matplotlib lazy-imported.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..loadprofile import (
    daily_profile, load_duration, load_metrics, weekday_weekend_profiles,
)


def _interval_hours(index) -> float:
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return 1.0
    deltas = np.diff(idx.view("int64")) / 3.6e12
    return float(np.median(deltas)) if len(deltas) else 1.0


def load_profile_chart(series: pd.Series, *, ax=None, split: bool = True, annotate: bool = True,
                       title: str | None = None, ylabel: str = "kW"):
    """Average load by hour-of-day. Returns ``(ax, LoadMetrics)``.

    ``split`` draws weekday vs weekend profiles (exposes schedule gaps); otherwise a single daily
    average. ``annotate`` overlays the base-load reference line.
    """
    import matplotlib.pyplot as plt

    m = load_metrics(series)
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 4.5))
    if split:
        wk, we = weekday_weekend_profiles(series)
        ax.plot(wk.index, wk.to_numpy(), color="#3366cc", lw=1.8, marker="o", ms=3, label="weekday")
        ax.plot(we.index, we.to_numpy(), color="#ff7f0e", lw=1.6, ls="--", marker="s", ms=3,
                label="weekend")
    else:
        dp = daily_profile(series)
        ax.plot(dp.index, dp.to_numpy(), color="#3366cc", lw=1.8, marker="o", ms=3, label="daily avg")
    if annotate:
        ax.axhline(m.near_base, color="#2ca02c", ls=":", lw=1.0, label=f"baseload ≈ {m.near_base:g}")
    ax.set_xlabel("hour of day")
    ax.set_ylabel(ylabel)
    ax.set_xticks(range(0, 24, 3))
    ax.set_title(title or f"Load profile — base/peak {m.base_to_peak:.2f}, LF {m.load_factor:.2f}")
    ax.legend(loc="best", fontsize=8)
    return ax, m


def load_duration_chart(series: pd.Series, *, ax=None, price: float | None = None,
                        annotate: bool = True, title: str | None = None, ylabel: str = "kW"):
    """Load-duration curve (values sorted high-to-low vs % of time). Returns ``(ax, LoadMetrics)``.

    ``price`` ($/kWh) adds an energy-cost figure (LDC area × price) to the title.
    """
    import matplotlib.pyplot as plt

    m = load_metrics(series)
    ldc = load_duration(series)
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 4.5))
    x = np.linspace(0.0, 100.0, len(ldc)) if len(ldc) else np.array([])
    ax.fill_between(x, 0, ldc, color="#3366cc", alpha=0.18)
    ax.plot(x, ldc, color="#3366cc", lw=1.8, label="load-duration")
    if annotate:
        ax.axhline(m.near_peak, color="#d62728", ls="--", lw=0.9, label=f"peak ≈ {m.near_peak:g}")
        ax.axhline(m.near_base, color="#2ca02c", ls="--", lw=0.9, label=f"baseload ≈ {m.near_base:g}")
    cost_note = ""
    if price is not None:
        energy = float(np.nansum(series.dropna().to_numpy()) * _interval_hours(series.index))
        cost_note = f" · {energy:,.0f} kWh ≈ ${energy * price:,.0f}"
    ax.set_xlabel("% of time at or above")
    ax.set_ylabel(ylabel)
    ax.set_title(title or f"Load-duration curve — LF {m.load_factor:.2f}{cost_note}")
    ax.legend(loc="best", fontsize=8)
    return ax, m
