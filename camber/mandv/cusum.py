"""Cumulative sum (CUSUM) of measurement-&-verification savings.

Tracks the running total of (baseline-projected - actual) consumption against a
baseline model. A rising CUSUM accumulates savings; a falling CUSUM accumulates
waste; a flat line means performance matches the baseline. A change in slope marks
when savings began, degraded, or reversed -- the standard M&V persistence tool
(IPMVP / ASHRAE Guideline 14).

Pairs with :func:`camber.mandv.stats.avoided_energy_savings`: that gives the
period-total avoided energy, this gives its time path and the optional control
limits for alerting.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def cusum(baseline_projected: pd.Series, actual: pd.Series) -> pd.Series:
    """Running cumulative sum of (projected - actual), aligned on the shared index."""
    df = pd.DataFrame({"p": baseline_projected, "a": actual}).dropna()
    return (df["p"] - df["a"]).cumsum()


@dataclass(frozen=True)
class CusumResult:
    """Summary of a CUSUM trajectory."""

    series: pd.Series          # the cumulative curve
    total: float               # final cumulative savings (projected - actual)
    mean_rate: float           # average per-interval savings (the overall slope)
    n: int

    @property
    def accumulating_savings(self) -> bool:
        """True when net cumulative savings is positive."""
        return self.total > 0


def cusum_savings(baseline_projected: pd.Series, actual: pd.Series) -> CusumResult:
    """CUSUM trajectory plus its total and average slope."""
    s = cusum(baseline_projected, actual)
    n = int(len(s))
    total = float(s.iloc[-1]) if n else 0.0
    mean_rate = float((baseline_projected.reindex(s.index)
                       - actual.reindex(s.index)).mean()) if n else 0.0
    return CusumResult(series=s, total=round(total, 4),
                       mean_rate=round(mean_rate, 6), n=n)


def control_exceedances(baseline_projected: pd.Series, actual: pd.Series, *,
                        limit: float) -> pd.Series:
    """Timestamps where |CUSUM| crosses +/-``limit`` (a performance alert).

    A symmetric control band: the first crossing of the upper limit flags
    sustained savings worth confirming; the lower limit flags sustained waste
    worth investigating.
    """
    s = cusum(baseline_projected, actual)
    return s[np.abs(s) >= abs(limit)]
