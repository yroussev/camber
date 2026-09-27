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


def cusum_plot(
    baseline_projected: pd.Series,
    actual: pd.Series,
    *,
    limit: float | None = None,
    ax=None,
    title: str | None = None,
    units: str = "kWh",
):
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
    ax.fill_between(
        s.index,
        0,
        s.to_numpy(),
        where=(s.to_numpy() >= 0),
        color="#2ca02c",
        alpha=0.15,
        label="net savings",
    )
    ax.fill_between(
        s.index,
        0,
        s.to_numpy(),
        where=(s.to_numpy() < 0),
        color="#d62728",
        alpha=0.15,
        label="net waste",
    )
    if limit is not None:
        ax.axhline(abs(limit), color="grey", lw=0.8, ls="--")
        ax.axhline(
            -abs(limit), color="grey", lw=0.8, ls="--", label=f"±control limit ({abs(limit):g})"
        )

    total = float(s.iloc[-1])
    ax.set_ylabel(f"Cumulative {units}")
    ax.set_xlabel("Time")
    verdict = "savings" if total > 0 else "waste"
    ax.set_title(title or f"CUSUM — net {verdict} {total:,.0f} {units} over {len(s)} intervals")
    ax.legend(loc="upper left", fontsize=8)
    return ax


_VERSION_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#17becf")


def chained_cusum_plot(
    frame: pd.DataFrame,
    *,
    markers=(),
    gaps=(),
    ax=None,
    title: str | None = None,
    units: str = "kWh",
):
    """A CUSUM chained across baseline versions: one segment per version, rebaseline markers.

    ``frame`` is indexed by date with columns ``version`` (``"v1"``, ``"v2"``, ...), ``projected``
    (that version's baseline projection) and ``actual`` -- only the days each version reports
    (:func:`camber.mvrun.chained_report`). The cumulative sum runs on across versions: a segment
    starts where the previous one ended (the "previous interval" convention of the DOE EnPI V5
    tool, whose chained model years continue from the last point before them), so the curve is
    the cumulative saving of the whole chain while each segment's slope is its own version's.
    ``markers`` are ``{"date", "label"}`` rebaseline dates (a dashed vertical line each) and
    ``gaps`` ``[start, end]`` spans nothing was reported over (the trigger to the end of the new
    baseline window), shaded. Returns the Axes.
    """
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))
    if frame is None or frame.empty:
        ax.set_title(title or "Chained CUSUM -- no reported days")
        return ax
    f = frame.sort_index()
    carry = 0.0
    order = list(dict.fromkeys(f["version"]))
    for k, ver in enumerate(order):
        seg = f[f["version"] == ver]
        d = (seg["projected"] - seg["actual"]).astype(float)
        s = carry + d.cumsum()
        color = _VERSION_COLORS[k % len(_VERSION_COLORS)]
        ax.plot(s.index, s.to_numpy(), color=color, lw=1.4, label=f"{ver} (Σ projected − actual)")
        if k > 0:  # the carried-forward level the segment continues from
            ax.plot([s.index[0], s.index[0]], [carry, s.iloc[0]], color=color, lw=0.8, ls=":")
        carry = float(s.iloc[-1])
    for g in gaps or ():
        ax.axvspan(
            pd.Timestamp(g[0]),
            pd.Timestamp(g[1]),
            color="#999999",
            alpha=0.15,
            lw=0,
            label="not reported (rebaseline window)",
        )
    for m in markers or ():
        ax.axvline(pd.Timestamp(m["date"]), color="#444444", lw=1.0, ls="--")
        ax.annotate(
            str(m.get("label", "")),
            (pd.Timestamp(m["date"]), 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(3, -12),
            textcoords="offset points",
            fontsize=8,
            color="#444444",
        )
    ax.axhline(0, color="#444444", lw=0.8)
    ax.set_ylabel(f"Cumulative {units}")
    ax.set_xlabel("Time")
    verdict = "savings" if carry > 0 else "waste"
    ax.set_title(
        title
        or f"Chained CUSUM -- net {verdict} {carry:,.0f} {units} across {len(order)} version(s)"
    )
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), loc="upper left", fontsize=8)
    return ax
