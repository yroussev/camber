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

**Resampled status (0.104, #143).** A run status resampled to a coarser grid holds each bin's
*duty*, the share of the bin the boiler was on (0..1; see docs/TIME-HANDLING.md). Thresholding
that at 0.5 made a boiler firing a quarter of every hour read as never firing. The count instead
takes the **fewest starts consistent with the duties**: a bin partly on holds a start or a stop
inside it, so a run of part-on bins separated by off bins is one start each. On a plain 0/1 trend
this is the off->on count, unchanged. ``runtime_pct`` is the mean duty, the share of time firing.
:attr:`BoilerCyclingResult.interval_min` and :attr:`~BoilerCyclingResult.n_partial` (samples on for
only part of their interval) let a rule say when the trend is too coarse to vouch for the count.

**Gaps are not idle days (0.108, #237).** The rate divides by the *observed* days -- the sum of
the sample-to-sample steps no longer than ``gap_factor`` x the median interval, as
``control_hunting`` measures its observed time -- not by the span from the first sample to the
last, so a 28-day logger outage no longer reads as 28 days with no starts. The span stays in
``n_days`` and the observed days are ``n_days_observed``; a rule caveats a trend whose observed
days are under ``_LOW_COVERAGE_FRAC`` of its span. Both are floored at one day.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "BoilerCyclingResult",
    "analyze_boiler_cycling",
]


@dataclass
class BoilerCyclingResult:
    """Boiler firing rate over the trend window."""

    equip: str
    n_days: float  # observed span in days
    starts_per_day: float  # off->on transitions per day (a floor; see module doc)
    runtime_pct: float  # % of the time the boiler is firing (the mean duty)
    n_starts: int
    coverage_start: str
    coverage_end: str
    interval_min: float | None = None  # median sample spacing of the status trend, minutes
    n_partial: int = 0  # samples on for only part of their interval (a resampled duty in (0, 1))
    # 0.108 (#237): days actually observed (contiguous steps, gaps left out; floored at 1), the
    # denominator of starts_per_day; n_days is the first-to-last span
    n_days_observed: float = float("nan")

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


#: Below this share of its first-to-last span observed, a start rate is caveated (0.108, #237).
_LOW_COVERAGE_FRAC = 0.5

#: A sample step longer than this many median intervals is a data gap (0.108, #237).
_GAP_FACTOR = 3.0

#: A duty within this of 0 or 1 counts as fully off or on (float noise from a resampled mean).
_DUTY_EPS = 1e-6


def _duty(status: pd.Series) -> pd.Series:
    """The status as a duty in [0, 1] (a 0/1 trend is its own duty)."""
    return pd.to_numeric(status, errors="coerce").astype(float).clip(0.0, 1.0)


def _partial(duty: pd.Series) -> pd.Series:
    """Samples on for only part of their interval (0 < duty < 1)."""
    return (duty > _DUTY_EPS) & (duty < 1.0 - _DUTY_EPS)


def _fewest_starts(duty: np.ndarray) -> np.ndarray:
    """Where the fewest starts consistent with a duty sequence fall (a boolean per sample).

    Each sample is a bin with an entry and an exit state (off 0 / on 1). A fully-off bin is
    (0, 0), a fully-on one (1, 1); a part-on bin is any pair but holds at least one transition:
    (0, 1) one start, (1, 0) none, (0, 0) and (1, 1) one start each (an on burst, or an off gap
    and a restart). Between bins, an exit of 0 and an entry of 1 is a start. The trend is
    taken to begin off, as the plain off->on count does. A two-state dynamic programme finds the
    minimum, and the backtrack marks the bin each start falls in (or at the boundary into).
    """
    n = len(duty)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out
    full = duty >= 1.0 - _DUTY_EPS
    zero = duty <= _DUTY_EPS
    big = n + 1
    best = [0, big]  # cost of ending the previous bin off / on; the trend starts off
    # back[i][x] = (previous exit, entry) chosen to end bin i in state x; cost[i][x] = starts in
    # bin i on that path (boundary + internal)
    back = np.zeros((n, 2, 2), dtype=np.int8)
    step = np.zeros((n, 2), dtype=np.int8)
    pairs: tuple
    for i in range(n):
        if zero[i]:
            pairs = ((0, 0),)
        elif full[i]:
            pairs = ((1, 1),)
        else:
            pairs = ((0, 0), (0, 1), (1, 0), (1, 1))
        new = [big, big]
        for e, x in pairs:
            internal = 0 if (zero[i] or full[i] or (e, x) == (1, 0)) else 1
            for p in (0, 1):
                c = best[p] + (1 if (p == 0 and e == 1) else 0) + internal
                if c < new[x]:
                    new[x] = c
                    back[i, x] = (p, e)
                    step[i, x] = (1 if (p == 0 and e == 1) else 0) + internal
        best = new
    x = 0 if best[0] <= best[1] else 1
    for i in range(n - 1, -1, -1):
        out[i] = step[i, x] > 0
        x = int(back[i, x, 0])
    return out


def _running_starts(status: pd.Series) -> tuple[pd.Series, pd.Series]:
    """``(running, start)`` on a status trend's samples: firing (> 0.5), and the starts.

    On a 0/1 trend the starts are the off->on transitions. On a resampled duty trend (any sample
    part-on) they are where the fewest starts consistent with the duties fall
    (:func:`_fewest_starts`), so a boiler firing part of every interval is not read as never
    starting."""
    duty = _duty(status)
    running = duty > 0.5
    if not _partial(duty).any():
        return running, running & ~running.shift(1, fill_value=False)
    start = pd.Series(_fewest_starts(duty.to_numpy()), index=status.index)
    return running, start


def _interval_min(index) -> float | None:
    """Median positive sample spacing in minutes (None with fewer than two distinct samples)."""
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return None
    steps = np.diff(idx.asi8) / 6e10
    steps = steps[steps > 0]
    return round(float(np.median(steps)), 3) if steps.size else None


def _observed_days(index, gap_factor: float = _GAP_FACTOR) -> float:
    """Days actually observed: the summed sample steps no longer than ``gap_factor`` x the median
    positive step (0.108, #237; the observed-time rule of ``control_hunting``)."""
    idx = pd.DatetimeIndex(index).sort_values()
    if len(idx) < 2:
        return 0.0
    steps = np.diff(idx.asi8) / 1e9
    pos = steps[steps > 0]
    if not pos.size:
        return 0.0
    ok = (steps > 0) & (steps <= gap_factor * float(np.median(pos)))
    return float(steps[ok].sum()) / 86400.0


def _coverage_caveat(res, what: str = "boiler") -> list:
    """A caveat when most of the trend's span is missing (0.108, #237), else ``[]``."""
    obs, span = res.n_days_observed, res.n_days
    if not (obs == obs and span > 0) or obs >= _LOW_COVERAGE_FRAC * span:
        return []
    return [
        f"the {what} status was observed on only {obs:.1f} of the {span:.1f} days from its first "
        f"sample to its last ({obs / span:.0%}; the rest are data gaps): starts_per_day is read "
        "over the observed days, so it stands for those days alone"
    ]


def _short_cycle_samples(df: pd.DataFrame, *, max_starts_per_day: float) -> pd.Series:
    """The firing starts on short-cycling days (0.102, #119).

    A boolean Series on ``df``'s index: True at each off->on start, counted as
    :func:`analyze_boiler_cycling` counts them, on a calendar day with at least
    ``max_starts_per_day`` of them. What a short-cycling evidence chart shades. All False without
    ``BoilerStatus``.
    """
    out = pd.Series(False, index=df.index)
    if "BoilerStatus" not in df.columns:
        return out
    w = df["BoilerStatus"].dropna()
    if not len(w):
        return out
    _running, start = _running_starts(w)
    day = pd.DatetimeIndex(w.index).normalize()
    per_day = start.groupby(day).sum()
    busy = per_day.index[per_day.to_numpy() >= max_starts_per_day]
    flagged = start & pd.Series(day.isin(busy), index=w.index)
    return pd.Series(df.index.isin(w.index[flagged.to_numpy()]), index=df.index)


def analyze_boiler_cycling(
    df: pd.DataFrame,
    equip: str,
    *,
    gap_factor: float = _GAP_FACTOR,
) -> BoilerCyclingResult | None:
    """Count boiler firing starts per day from the ``BoilerStatus`` trend (0/1, or a resampled
    duty in 0..1), per *observed* day (0.108, #237: steps longer than ``gap_factor`` x the median
    interval are gaps, not idle time)."""
    if "BoilerStatus" not in df.columns:
        return None
    w = df[["BoilerStatus"]].dropna()
    if len(w) < 10:
        return None
    span_days = (w.index.max() - w.index.min()).total_seconds() / 86400.0
    span_days = max(span_days, 1.0)
    observed_days = max(_observed_days(w.index, gap_factor), 1.0)

    _running, start = _running_starts(w["BoilerStatus"])
    starts = int(start.sum())
    duty = _duty(w["BoilerStatus"])

    return BoilerCyclingResult(
        equip=equip,
        n_days=round(span_days, 1),
        starts_per_day=round(starts / observed_days, 2),
        runtime_pct=round(100.0 * float(duty.mean()), 1),
        n_starts=starts,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        interval_min=_interval_min(w.index),
        n_partial=int(_partial(duty).sum()),
        n_days_observed=round(observed_days, 1),
    )


def _resolution_check(
    res: BoilerCyclingResult,
    severity: str,
    *,
    max_starts_per_day: float,
    max_interval_min: float,
    what: str = "boiler",
) -> tuple:
    """``(severity, caveats, declined)`` after the trend-resolution check (0.104, #143).

    A start count on a trend coarser than ``max_interval_min`` is a floor: a cycle shorter than
    the interval is invisible. A count already at the limit stands (the floor is reached) with a
    caveat. Below the limit, when samples show ``what`` on for only part of an interval (a
    resampled duty), the interval hid starts and stops, so "ok" cannot be vouched for: the
    verdict declines to ``info``. Below the limit with no part-on samples, ``ok`` stands with
    the caveat. At or below ``max_interval_min`` nothing changes.
    """
    dt = res.interval_min
    if dt is None or dt <= max_interval_min:
        return severity, [], False
    caveat = (
        f"starts counted on a {dt:g}-minute trend, coarser than the {max_interval_min:g} minutes "
        f"the count needs (max_interval_min): a {what} cycle shorter than the interval is "
        "invisible, so starts_per_day is a floor; run at the stored interval for the true count"
    )
    if severity != "ok" or res.n_partial == 0:
        return severity, [caveat], False
    caveat += (
        f". {res.n_partial} samples show the {what} on for only part of the interval, so the "
        f"interval hid starts: below {max_starts_per_day:g} a day cannot be confirmed"
    )
    return "info", [caveat], True
