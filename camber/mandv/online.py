"""Online / streaming M&V monitors — incremental, no full recompute.

The batch M&V engine (`mandv.stats`, `mandv.cusum`) scores a finished reporting period. For
*live* monitoring you want the same signals updated one sample at a time: an **online CUSUM** of
savings/waste against a baseline model (so savings erosion is caught as it happens — itself an FDD
signal), and a **rolling-window residual anomaly** flag (online "learned-normal" deviation). Both
are O(1) per update and keep only bounded state, so they suit a streaming ingest buffer.

Dependency-light: collections + a baseline model exposing ``predict``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class CusumState:
    """Snapshot of the online savings CUSUM after an update."""

    n: int
    last_residual: float  # predicted − actual (positive = savings)
    cusum: float  # running Σ residual (net savings to date)
    high: float  # one-sided tabular accumulator (sustained savings)
    low: float  # one-sided tabular accumulator (sustained waste)
    alarm: str | None  # "savings" | "waste" | None, when a tabular limit is crossed


class OnlineCusum:
    """Incremental CUSUM of (baseline-predicted − actual) against a baseline model.

    ``predict`` maps a driver value to expected consumption (e.g. ``model.predict`` or any
    ``f(driver) -> float``). Each :meth:`update` folds one (driver, actual) sample. ``limit`` +
    ``slack`` enable a two-sided **tabular** CUSUM: the high/low accumulators ignore drift within
    ``slack`` and raise an ``alarm`` when either exceeds ``limit`` — the standard sustained-shift
    detector, here for sustained savings or waste.
    """

    def __init__(self, predict, *, limit: float | None = None, slack: float = 0.0):
        self._predict = predict
        self.limit = limit
        self.slack = slack
        self.n = 0
        self.cusum = 0.0
        self.high = 0.0
        self.low = 0.0

    def reset(self) -> None:
        self.n = self.cusum = self.high = self.low = 0
        self.cusum = self.high = self.low = 0.0

    def update(self, driver, actual) -> CusumState:
        raw = self._predict(driver)
        pred = float(np.asarray(raw).reshape(-1)[0])  # accept scalar or length-1 array
        resid = pred - float(actual)
        self.n += 1
        self.cusum += resid
        # tabular one-sided accumulators with a slack/reference value
        self.high = max(0.0, self.high + resid - self.slack)
        self.low = max(0.0, self.low - resid - self.slack)
        alarm = None
        if self.limit is not None:
            if self.high >= self.limit:
                alarm = "savings"
            elif self.low >= self.limit:
                alarm = "waste"
        return CusumState(
            n=self.n,
            last_residual=round(resid, 4),
            cusum=round(self.cusum, 4),
            high=round(self.high, 4),
            low=round(self.low, 4),
            alarm=alarm,
        )


@dataclass
class AnomalyState:
    """Snapshot of the rolling-residual anomaly monitor after an update."""

    n: int
    value: float
    z: float  # robust z-score of the latest value vs the window (NaN until warm)
    is_anomaly: bool
    warm: bool  # whether the window has enough history to judge


class RollingAnomaly:
    """Online anomaly flag from a rolling window's robust z-score (median / MAD).

    Maintains the last ``window`` values; once ``min_samples`` are seen, the latest value's robust
    z-score (using median + MAD, so a single spike doesn't mask the next) is computed and flagged
    when ``|z| >= k``. Use on a residual stream (actual − forecast) for online "learned-normal"
    deviation detection.
    """

    def __init__(self, *, window: int = 48, k: float = 3.5, min_samples: int | None = None):
        self.window = window
        self.k = k
        self.min_samples = min_samples or max(8, window // 4)
        self._buf: deque = deque(maxlen=window)

    def update(self, value) -> AnomalyState:
        v = float(value)
        warm = len(self._buf) >= self.min_samples
        z, is_anom = float("nan"), False
        if warm:
            arr = np.asarray(self._buf, dtype=float)
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            scale = 1.4826 * mad  # MAD → σ for a normal distribution
            if scale > 0:
                z = (v - med) / scale
                is_anom = abs(z) >= self.k
        self._buf.append(v)  # the latest joins the window after judging
        return AnomalyState(
            n=len(self._buf),
            value=v,
            z=round(z, 3) if z == z else float("nan"),
            is_anomaly=is_anom,
            warm=warm,
        )
