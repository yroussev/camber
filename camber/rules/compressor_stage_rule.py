"""Rule: DX compressor staging instability (stage changes per day).

Multi-stage DX that ramps stages up and down excessively (rather than holding a stage) wastes
efficiency and stresses compressors — the DX analogue of chiller-staging hunting. Counts stage
*changes* per day from the compressor-stage trend. ``max_changes_per_day`` is
staging-hysteresis dependent, so it is a constructor parameter.

**Only changes while running count (0.106, #185).** A change from stage 0 to a stage or back is
the compressor starting or stopping, which ``compressor_short_cycle`` judges; counting it here made
a healthy unit cycling two or three times an hour read as hunting. A change counts when the
compressor runs (stage above 0) on both sides of it.

**Trend resolution (0.106, #185).** A resampled stage is the bin's mean, so a stage held for less
than the interval averages away (stage 1 then 2 for half a 15-minute bin reads 1.5, which rounds
to one level). On a trend coarser than ``max_interval_min`` the count is a floor: a count at or
above the limit stands with a caveat; below it, samples between two stages (a mean that is not a
whole number) show the interval hid changes, so the verdict declines to ``info`` instead of
``ok``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..boilercycle import _interval_min
from ..model.roles import Role
from .base import Finding

#: A value within this of a whole number counts as one level (float noise from a resampled mean).
_LEVEL_EPS = 1e-6


def _span_days(s: pd.Series) -> float:
    return max((s.index[-1] - s.index[0]).total_seconds() / 86400.0, 1e-9)


def _changes_per_day(series: pd.Series) -> tuple:
    """(#level changes, changes/day, span_days) for a stepwise series over its datetime index."""
    s = series.dropna()
    if len(s) < 2:
        return 0, 0.0, 0.0
    changes = int((s.round().diff().fillna(0) != 0).sum())
    span_days = _span_days(s)
    return changes, changes / span_days, span_days


def _n_partial(s: pd.Series) -> int:
    """Samples between two levels (a resampled mean that is not a whole number)."""
    return int(((s - s.round()).abs() > _LEVEL_EPS).sum())


def _running_stage_changes(series: pd.Series) -> tuple:
    """(#stage changes while running, changes/day, span_days, #partial samples) (0.106, #185).

    A change counts when the rounded stage differs from the previous sample's and both are above
    0, so a start (0 -> n) or a stop (n -> 0) is not a stage change.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return 0, 0.0, 0.0, 0
    lvl = s.round()
    prev = lvl.shift()
    changed = (lvl != prev) & (lvl > 0) & (prev > 0)
    n = int(changed.sum())
    return n, n / _span_days(s), _span_days(s), _n_partial(s)


def _binary_changes(series: pd.Series) -> tuple:
    """(#changes, changes/day, span_days, #partial samples) of a 0/1 command (0.106, #185).

    On a 0/1 trend this is the plain level-change count. A resampled command holds each bin's
    mean, the share of the bin at 1, and a bin strictly between 0 and 1 changed inside it. The
    count is then the **fewest changes the means allow**: a run of part-way bins between two whole
    bins in the same state holds at least two changes (out and back), between different states
    at least one, and at either end of the trend at least one. A series with values outside
    [0, 1] is not a 0/1 command and falls back to the rounded level-change count.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return 0, 0.0, 0.0, 0
    v = s.to_numpy(dtype=float)
    if v.min() < -_LEVEL_EPS or v.max() > 1.0 + _LEVEL_EPS:
        n, per_day, span = _changes_per_day(s)
        return n, per_day, span, _n_partial(s)
    whole = (v <= _LEVEL_EPS) | (v >= 1.0 - _LEVEL_EPS)
    state = v >= 0.5
    n = 0
    last = None  # the state of the last whole bin
    pending = False  # part-way bins since that whole bin
    for i in range(len(v)):
        if not whole[i]:
            pending = True
            continue
        if pending:
            n += 1 if last is None else (2 if state[i] == last else 1)
        elif last is not None and state[i] != last:
            n += 1
        last, pending = state[i], False
    if pending:
        n += 1
    return n, n / _span_days(s), _span_days(s), int((~whole).sum())


def _coarse_trend_check(
    severity: str,
    *,
    interval_min: float | None,
    max_interval_min: float,
    n_partial: int,
    counted: str,
    hidden: str,
    limit_name: str,
    limit: float,
) -> tuple:
    """``(severity, caveats, declined)`` after the trend-resolution check (0.106, #185).

    The counterpart of :func:`camber.boilercycle._resolution_check` for a change count. On a trend
    coarser than ``max_interval_min`` the count is a floor: a verdict at or above the limit stands
    with a caveat; below it, part-way samples (``n_partial``) mean the interval hid changes, so
    the verdict declines to ``info``; with none, ``ok`` stands with the caveat. At or below
    ``max_interval_min`` nothing changes.
    """
    if interval_min is None or interval_min <= max_interval_min:
        return severity, [], False
    caveat = (
        f"{counted} counted on a {interval_min:g}-minute trend, coarser than the "
        f"{max_interval_min:g} minutes the count needs (max_interval_min): {hidden} averages "
        "away in a resampled mean, so the count is a floor; run at the stored interval for the "
        "true count"
    )
    if severity != "ok" or n_partial == 0:
        return severity, [caveat], False
    caveat += (
        f". {n_partial} samples sit part-way between two states, so the interval hid changes: "
        f"below {limit:g} a day ({limit_name}) cannot be confirmed"
    )
    return "info", [caveat], True


class CompressorStaging:
    """Detects unstable DX compressor staging (excess stage changes per day while running)."""

    name = "compressor_staging"
    roles_required = (Role.COMPRESSOR_STAGE,)
    roles_optional = ()

    def __init__(self, max_changes_per_day: float = 24.0, max_interval_min: float = 5.0):
        self.max_changes_per_day = max_changes_per_day
        # 0.106 (#185): the coarsest trend whose count can vouch for "ok"; a stage held for less
        # than the interval averages away in a resampled mean
        self.max_interval_min = max_interval_min

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.COMPRESSOR_STAGE not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need compressor stage)",
            )
        stage = frame[Role.COMPRESSOR_STAGE]
        n_changes, per_day, span, n_partial = _running_stage_changes(stage)
        if span <= 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need compressor stage)",
            )
        max_stage = float(np.nanmax(pd.to_numeric(stage, errors="coerce").to_numpy(dtype=float)))
        if per_day >= 2 * self.max_changes_per_day:
            severity = "fault"
        elif per_day >= self.max_changes_per_day:
            severity = "warn"
        else:
            severity = "ok"
        interval = _interval_min(stage.dropna().index)
        severity, caveats, declined = _coarse_trend_check(
            severity,
            interval_min=interval,
            max_interval_min=self.max_interval_min,
            n_partial=n_partial,
            counted="stage changes",
            hidden="a stage held for less than the interval",
            limit_name="max_changes_per_day",
            limit=self.max_changes_per_day,
        )
        summary = (
            f"{equip}: {per_day:.1f} compressor-stage changes/day while running "
            f"(threshold {self.max_changes_per_day:.0f}), top stage {max_stage:.0f}"
        )
        if declined:
            summary += f"; cannot confirm on a {interval:g}-minute trend"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "stage_changes_per_day": round(per_day, 2),
                "max_changes_per_day": self.max_changes_per_day,
                "n_changes": n_changes,
                "max_stage": max_stage,
                "n_days": round(span, 2),
                "interval_min": interval,  # 0.106 (#185)
                "n_partial": n_partial,  # 0.106 (#185)
            },
            summary=summary,
            caveats=caveats,
        )
