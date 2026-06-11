"""Tests for RCx/MBCx workflow + functional-test automation (camber.rcx)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rcx import (  # noqa: E402
    functional_test, before_after,
)


def _frame(n=200, start="2025-06-01"):
    """Synthetic role-frame: a supply-air-temp setpoint and the measured value."""
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame({"sat_sp": np.full(n, 55.0), "sat": np.full(n, 55.0)}, index=idx)


# --- functional_test ---------------------------------------------------------- #

def test_functional_test_pass():
    # SAT tracks setpoint within 2 degF on every interval -> 100% pass
    f = _frame()
    r = functional_test(f, "SAT tracks SP",
                        lambda fr: (fr["sat"] - fr["sat_sp"]).abs() <= 2.0)
    assert r.severity == "pass"
    assert abs(r.pass_rate_pct - 100.0) < 1e-6
    assert r.n == len(f)


def test_functional_test_fail():
    # Drive SAT 6 degF off setpoint for 60% of intervals -> well below 80% pass
    f = _frame()
    f.loc[f.index[:120], "sat"] = 61.0
    r = functional_test(f, "SAT tracks SP",
                        lambda fr: (fr["sat"] - fr["sat_sp"]).abs() <= 2.0)
    assert r.severity == "fail"
    assert r.pass_rate_pct < 80.0
    assert abs(r.pass_rate_pct - 40.0) < 1.0


def test_functional_test_nulls_are_excluded():
    # Half the intervals are not evaluable (NaN); valid count drops, scoring uses valid only
    f = _frame()
    cond = pd.Series(True, index=f.index, dtype="object")
    cond.iloc[100:] = np.nan
    r = functional_test(f, "with nulls", lambda fr: cond)
    assert r.n == 100
    assert abs(r.pass_rate_pct - 100.0) < 1e-6


def test_functional_test_insufficient():
    f = _frame(n=5)
    r = functional_test(f, "tiny",
                        lambda fr: (fr["sat"] - fr["sat_sp"]).abs() <= 2.0)
    assert r is None


# --- before_after ------------------------------------------------------------- #

def _step_series(before_level, after_level, noise=0.0, n=100, seed=0):
    """Metric series: constant level before/after a midpoint change, optional noise."""
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    half = n // 2
    rng = np.random.default_rng(seed)
    vals = np.concatenate([np.full(half, before_level), np.full(n - half, after_level)])
    if noise:
        vals = vals + rng.normal(0.0, noise, size=n)
    return pd.Series(vals, index=idx), idx[half]


def test_before_after_significant_improvement():
    s, ct = _step_series(100.0, 60.0, noise=2.0)
    r = before_after(s, ct)
    assert r.improved is True
    assert r.significant is True
    assert r.delta < 0                          # lower-is-better: a drop
    assert r.metric_after < r.metric_before


def test_before_after_regression():
    s, ct = _step_series(60.0, 100.0, noise=2.0)
    r = before_after(s, ct)
    assert r.improved is False
    assert r.significant is True
    assert r.delta > 0                          # metric went up = worse


def test_before_after_no_change_insignificant():
    # Same level both sides, only noise -> change is well under 0.5 * pooled std
    s, ct = _step_series(100.0, 100.0, noise=5.0)
    r = before_after(s, ct)
    assert r.significant is False
    assert abs(r.delta) < 0.5 * 5.0


def test_before_after_insufficient():
    idx = pd.date_range("2025-06-01", periods=12, freq="1h")
    s = pd.Series(np.arange(12, dtype=float), index=idx)
    # change_time leaves only a few points after it -> below min_samples
    assert before_after(s, idx[10]) is None
