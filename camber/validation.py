"""Methods validation & scientific credibility: confidence intervals + determinism.

Detection rates reported without uncertainty over-state what a small test set can
prove. This module attaches credibility to the evaluation numbers from
``camber.eval``:

- **Wilson score interval** -- a binomial-proportion confidence interval that, unlike
  the normal (Wald) approximation, stays inside [0, 1] and behaves sensibly at the
  extremes (0 of n, n of n) and for small n (E. B. Wilson, "Probable Inference, the
  Law of Succession, and Statistical Inference," JASA 22 (1927) 209-212),
- **rates with CI** -- the LBNL FDD performance-evaluation framework rates
  (true-positive, false-positive, accuracy) wrapped with their Wilson intervals on
  the correct binomial denominators,
- **determinism check** -- re-runs an analysis function and confirms reproducible
  output, the minimum bar for a defensible (citable) method.

References: LBNL FDD performance-evaluation framework; Wilson score interval (1927).
numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .eval import Confusion


def wilson_interval(k, n, *, z: float = 1.96):
    """Wilson score confidence interval for a binomial proportion (``k`` of ``n``).

    Returns ``(lo, hi)`` bounded within [0, 1]. ``n == 0`` -> ``(nan, nan)``.
    ``z`` is the standard-normal quantile (1.96 ~ 95%). Unlike the Wald interval,
    the Wilson interval never escapes [0, 1] and is well-behaved at ``k == 0`` and
    ``k == n``.
    """
    n = int(n)
    k = int(k)
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z * np.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (float(lo), float(hi))


@dataclass
class RateCI:
    """A proportion with its Wilson confidence interval."""

    rate: float
    lo: float
    hi: float
    n: int                        # denominator (sample size) behind the rate

    def as_dict(self) -> dict:
        """Return the rate and its interval as a plain dict."""
        return asdict(self)


def rate_ci(k, n, *, z: float = 1.96) -> RateCI:
    """A ``RateCI`` for ``k`` successes of ``n`` trials (Wilson interval)."""
    n = int(n)
    k = int(k)
    rate = round(k / n, 4) if n else float("nan")
    lo, hi = wilson_interval(k, n, z=z)
    return RateCI(rate=rate, lo=round(lo, 4) if n else lo,
                  hi=round(hi, 4) if n else hi, n=n)


def metrics_with_ci(c: Confusion) -> dict:
    """LBNL-framework rates from a ``Confusion``, each with a Wilson CI.

    ``true_positive_rate`` uses denominator ``tp + fn`` (actual positives),
    ``false_positive_rate`` uses ``fp + tn`` (actual negatives), and ``accuracy``
    uses the total. Each value is a :class:`RateCI`.
    """
    return {
        "true_positive_rate": rate_ci(c.tp, c.tp + c.fn),
        "false_positive_rate": rate_ci(c.fp, c.fp + c.tn),
        "accuracy": rate_ci(c.tp + c.tn, c.total),
    }


@dataclass
class DeterminismResult:
    """Whether repeated calls to a function reproduced the same output."""

    deterministic: bool
    n_runs: int
    note: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def check_determinism(fn, *args, runs: int = 3, **kwargs) -> DeterminismResult:
    """Call ``fn(*args, **kwargs)`` ``runs`` times and check the output is stable.

    Outputs are compared for equality; if equality raises or is ambiguous (e.g.
    numpy arrays), the comparison falls back to ``repr``. ``deterministic`` is True
    iff every run matched the first. This is the minimum reproducibility bar for a
    method to be reported as citable.
    """
    runs = int(runs)
    if runs < 2:
        return DeterminismResult(deterministic=True, n_runs=runs,
                                 note="fewer than 2 runs; trivially reproducible")
    first = fn(*args, **kwargs)
    for i in range(1, runs):
        cur = fn(*args, **kwargs)
        if not _same(first, cur):
            return DeterminismResult(
                deterministic=False, n_runs=runs,
                note=f"output diverged on run {i + 1} of {runs}")
    return DeterminismResult(deterministic=True, n_runs=runs,
                             note=f"identical output across {runs} runs")


def _same(a, b) -> bool:
    """Best-effort equality, falling back to repr for non-boolean comparisons."""
    try:
        result = (a == b)
        if isinstance(result, bool):
            return result
        # array-like / ambiguous truth value -> require all-equal
        return bool(np.all(result))
    except Exception:
        return repr(a) == repr(b)
