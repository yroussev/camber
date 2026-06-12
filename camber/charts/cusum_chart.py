"""CUSUM chart: the cumulative-savings trajectory against a baseline model.

Plots the running cumulative sum of (baseline-projected − actual) consumption from
:func:`camber.mandv.cusum`. The slope is the story: rising = accumulating savings, falling =
accumulating waste, flat = on baseline; a kink dates when performance changed. Optional
symmetric control limits flag a sustained excursion worth investigating (the M&V persistence
view that pairs with the period-total avoided energy).
"""

from __future__ import annotations

import pandas as pd

from ..mandv.cusum import cusum as _cusum


def cusum_plot(baseline_projected: pd.Series, actual: pd.Series, *, limit: float | None = None,
               ax=None, title: str | None = None, units: str = "kWh"):
    """Draw the CUSUM trajectory (with optional ±``limit`` control band). Returns the Axes."""
    import matplotlib.pyplot as plt

    s = _cusum(baseline_projected, actual)
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))
    if s.empty:
        ax.set_title(title or "CUSUM — no overlapping data")
        return ax

    ax.plot(s.index, s.to_numpy(), color="#1f77b4", lw=1.3, label="CUSUM (Σ projected − actual)")
    ax.axhline(0, color="#444444", lw=0.8, ls="-")
    ax.fill_between(s.index, 0, s.to_numpy(), where=(s.to_numpy() >= 0),
                    color="#2ca02c", alpha=0.15, label="net savings")
    ax.fill_between(s.index, 0, s.to_numpy(), where=(s.to_numpy() < 0),
                    color="#d62728", alpha=0.15, label="net waste")
    if limit is not None:
        ax.axhline(abs(limit), color="grey", lw=0.8, ls="--")
        ax.axhline(-abs(limit), color="grey", lw=0.8, ls="--", label=f"±control limit ({abs(limit):g})")

    total = float(s.iloc[-1])
    ax.set_ylabel(f"Cumulative {units}")
    ax.set_xlabel("Time")
    verdict = "savings" if total > 0 else "waste"
    ax.set_title(title or f"CUSUM — net {verdict} {total:,.0f} {units} over {len(s)} intervals")
    ax.legend(loc="upper left", fontsize=8)
    return ax
