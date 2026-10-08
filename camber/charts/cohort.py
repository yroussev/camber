"""Pattern C — peer / cohort comparison.

One unit looks fine until you compare it to its siblings. This plots a role across a **cohort** of
like equipment as **small multiples**, ordered by how far each unit deviates from the cohort norm,
with the outliers highlighted — so a VAV that runs unlike its 40 peers jumps out. The same
deviation score powers a cohort-deviation FDD rule (:mod:`camber.rules.cohort`).

Deviation is a **robust z-score** (median / MAD) of a per-unit summary (mean, peak, or load factor)
against the cohort, so a couple of odd units don't move the reference. numpy/pandas; matplotlib
lazy-imported.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .diagnostic import _col

_SUMMARIES = ("mean", "peak", "load_factor", "variability")
_TAILS = ("both", "low", "high")


@dataclass
class CohortResult:
    """Per-unit deviation of a role summary from the cohort norm."""

    role: str
    summary: str
    values: dict  # {equip: summary scalar}
    z: dict  # {equip: robust z-score vs the cohort}
    outliers: list  # equip with |z| >= k
    median: float
    mad: float

    def as_dict(self) -> dict:
        return asdict(self)


def cohort_summary(frames: dict, role, *, summary: str = "mean") -> pd.Series:
    """Reduce each unit's role series to one number: ``mean``, ``peak`` (max), ``load_factor``
    (mean/peak) or ``variability`` (the standard deviation; 0.98, #85: a damper or flow that never
    moves stands out on the low side). Returns a Series ``{equip: value}``."""
    if summary not in _SUMMARIES:
        raise ValueError(f"summary must be one of {_SUMMARIES}, got {summary!r}")
    out = {}
    for equip, frame in frames.items():
        try:
            s = _col(frame, role).dropna()
        except KeyError:
            continue
        if s.empty:
            continue
        if summary == "mean":
            out[equip] = float(s.mean())
        elif summary == "peak":
            out[equip] = float(s.max())
        elif summary == "variability":
            out[equip] = float(s.std(ddof=0))
        else:  # load_factor
            peak = float(s.max())
            out[equip] = float(s.mean()) / peak if peak else float("nan")
    return pd.Series(out, dtype=float)


def cohort_deviation(
    frames: dict,
    role,
    *,
    k: float = 3.5,
    summary: str = "mean",
    min_cohort: int = 3,
    tail: str = "both",
) -> CohortResult:
    """Robust z-score of each unit's role summary vs the cohort; flag ``|z| >= k`` as outliers.

    ``tail`` (0.98, #85) picks which side flags: ``"both"`` (default), ``"low"`` (``z <= -k``) or
    ``"high"`` (``z >= k``).
    """
    vals = cohort_summary(frames, role, summary=summary).dropna()
    return cohort_deviation_from_values(
        vals, role, k=k, summary=summary, min_cohort=min_cohort, tail=tail
    )


def cohort_deviation_from_values(
    vals: pd.Series,
    role,
    *,
    k: float = 3.5,
    summary: str = "mean",
    min_cohort: int = 3,
    tail: str = "both",
) -> CohortResult:
    """:func:`cohort_deviation` on per-unit values already reduced (and perhaps normalised).

    ``vals`` is ``{equip: value}``; non-finite values are dropped. Added in 0.98 (#85) so a caller
    can normalise each unit's summary (by its design airflow, or by its own reference period)
    before the robust z.
    """
    if tail not in _TAILS:
        raise ValueError(f"tail must be one of {_TAILS}, got {tail!r}")
    rname = getattr(role, "name", str(role))
    vals = pd.Series(vals, dtype=float)
    vals = vals[np.isfinite(vals.to_numpy(float))]
    if len(vals) < min_cohort:
        return CohortResult(rname, summary, dict(vals), {}, [], float("nan"), float("nan"))
    arr = vals.to_numpy(float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    # MAD collapses to 0 when >half the cohort shares a value; fall back to mean abs deviation so a
    # lone outlier isn't masked (z=0 for everyone). Standard MAD-blind-spot guard.
    scale = 1.4826 * mad if mad > 0 else 1.2533 * float(np.mean(np.abs(arr - med)))
    z = {e: ((v - med) / scale if scale > 0 else 0.0) for e, v in vals.items()}
    if tail == "low":
        outliers = [e for e, zz in z.items() if zz <= -k]
    elif tail == "high":
        outliers = [e for e, zz in z.items() if zz >= k]
    else:
        outliers = [e for e, zz in z.items() if abs(zz) >= k]
    return CohortResult(
        rname,
        summary,
        {e: float(v) for e, v in vals.items()},
        {e: round(zz, 3) for e, zz in z.items()},
        outliers,
        round(med, 4),
        round(mad, 4),
    )


def cohort_small_multiples(
    frames: dict,
    role,
    *,
    ncols: int = 4,
    rank: str = "deviation",
    k: float = 3.5,
    summary: str = "mean",
    max_units: int = 16,
    figsize=None,
):
    """Small-multiples grid of ``role`` across the cohort, ordered by deviation; outliers in red.

    Returns ``(fig, CohortResult)``. ``rank="deviation"`` orders units by ``|z|`` descending (worst
    first); anything else keeps insertion order. At most ``max_units`` panels are drawn.
    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    res = cohort_deviation(frames, role, k=k, summary=summary)
    order = list(frames)
    if rank == "deviation" and res.z:
        order = sorted(res.z, key=lambda e: -abs(res.z[e]))
    order = order[:max_units]

    n = len(order)
    nrows = max(1, math.ceil(n / ncols))
    fig, axes = plt.subplots(
        nrows, ncols, figsize=figsize or (3.2 * ncols, 2.2 * nrows), squeeze=False
    )
    for i, equip in enumerate(order):
        ax = axes[i // ncols][i % ncols]
        try:
            s = _col(frames[equip], role).dropna()
        except KeyError:
            s = pd.Series(dtype=float)
        is_out = equip in res.outliers
        ax.plot(s.index, s.to_numpy(), lw=0.8, color="#d62728" if is_out else "#3366cc")
        zlbl = f"  z={res.z[equip]:+.1f}" if equip in res.z else ""
        ax.set_title(f"{equip}{zlbl}", fontsize=8, color="#d62728" if is_out else "#222")
        ax.tick_params(labelsize=6)
        if isinstance(s.index, pd.DatetimeIndex) and len(s):
            # 0.102 (#117): a small panel fits a few concise date ticks, not overlapping full dates
            loc = mdates.AutoDateLocator(maxticks=4)
            ax.xaxis.set_major_locator(loc)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    for j in range(n, nrows * ncols):  # hide unused panels
        axes[j // ncols][j % ncols].axis("off")
    fig.suptitle(
        f"Cohort — {res.role} ({summary}): {len(res.outliers)} outlier(s) of {n}", fontsize=11
    )
    fig.tight_layout()
    return fig, res
