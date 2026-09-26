"""Box-by-hour: one value's distribution for each hour of the day (e.g. duct static, fan-on only).

A carpet shows *when*; a box-by-hour shows the *spread* at each hour -- whether duct static is reset
down overnight and at light load, or rides one setpoint all day. Samples outside ``mask`` (e.g. fan
off) are dropped before binning, so an idle fan's zero reading never drags a box down.
numpy/pandas; matplotlib lazy-imported.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["hourly_groups", "box_by_hour"]


def hourly_groups(series: pd.Series, *, mask=None) -> dict:
    """``{hour: values}`` for hours 0-23 that have samples (after ``mask`` and NaN removal)."""
    s = pd.to_numeric(pd.Series(series), errors="coerce")
    if mask is not None:
        s = s[pd.Series(mask).reindex(s.index).fillna(False).astype(bool)]
    s = s.dropna()
    idx = pd.DatetimeIndex(s.index)
    return {
        int(h): s.to_numpy(dtype=float)[idx.hour == h]
        for h in sorted(set(idx.hour))
        if (idx.hour == h).any()
    }


def box_by_hour(
    series: pd.Series,
    *,
    mask=None,
    ax=None,
    title: str | None = None,
    ylabel: str = "value",
    showfliers: bool = False,
):
    """Draw one box per hour of day for ``series`` (only where ``mask`` is True). Returns the Axes.

    Hours with no samples are left empty (not drawn as zero). Each box is labelled with its sample
    count (red under 10), so a handful of night-cycle samples is not read as a daily pattern.
    Fliers are hidden by default so a few transients do not dominate the scale.
    """
    import matplotlib.pyplot as plt

    groups = hourly_groups(series, mask=mask)
    if ax is None:
        _, ax = plt.subplots(figsize=(10, 3.5))
    if groups:
        ax.boxplot(
            [groups[h] for h in groups],
            positions=list(groups),
            widths=0.6,
            showfliers=showfliers,
            manage_ticks=False,  # keep the x axis in hours, not box ordinals
        )
        # sample count per hour: a box of 2 samples must not read like a box of 300. Drawn inside
        # a headroom band at the top of the axes, clear of the title and the boxes.
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + 0.18 * (hi - lo))
        for h, v in groups.items():
            ax.text(
                h,
                0.985,
                f"n={len(v)}",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=6,
                rotation=90,
                color="#555" if len(v) >= 10 else "#b3261e",
            )
    ax.set_xlim(-0.8, 23.8)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([str(h) for h in range(0, 24, 2)])
    ax.set_xlabel("Hour of day")
    ax.set_ylabel(ylabel)
    n = sum(len(v) for v in groups.values())
    ax.set_title(title or f"Box by hour — {n} samples")
    return ax
