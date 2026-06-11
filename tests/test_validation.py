"""Tests for methods validation & credibility (camber.validation)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.eval import Confusion  # noqa: E402
from camber.validation import (  # noqa: E402
    wilson_interval, rate_ci, RateCI, metrics_with_ci,
    check_determinism, DeterminismResult,
)


# --- wilson_interval ---------------------------------------------------------- #

def test_wilson_typical():
    lo, hi = wilson_interval(8, 10)
    assert 0.0 < lo < hi < 1.0
    assert lo > 0.4
    assert hi < 0.98


def test_wilson_zero_successes():
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0
    assert 0.0 < hi < 0.5


def test_wilson_all_successes():
    lo, hi = wilson_interval(10, 10)
    assert hi <= 1.0
    assert 0.5 < lo < 1.0


def test_wilson_zero_n():
    lo, hi = wilson_interval(0, 0)
    assert math.isnan(lo) and math.isnan(hi)


def test_wilson_contains_point_estimate():
    lo, hi = wilson_interval(5, 20)
    assert lo <= 0.25 <= hi


# --- rate_ci ------------------------------------------------------------------ #

def test_rate_ci_basic():
    r = rate_ci(8, 10)
    assert isinstance(r, RateCI)
    assert abs(r.rate - 0.8) < 1e-9
    assert r.n == 10
    assert r.lo < r.rate < r.hi
    d = r.as_dict()
    assert set(d) == {"rate", "lo", "hi", "n"}


def test_rate_ci_zero_n():
    r = rate_ci(0, 0)
    assert r.n == 0
    assert math.isnan(r.rate) and math.isnan(r.lo) and math.isnan(r.hi)


# --- metrics_with_ci ---------------------------------------------------------- #

def test_metrics_with_ci_denominators():
    c = Confusion(tp=8, fp=2, fn=2, tn=18)   # P=10, N=20, total=30
    m = metrics_with_ci(c)
    assert set(m) == {"true_positive_rate", "false_positive_rate", "accuracy"}
    tpr = m["true_positive_rate"]
    fpr = m["false_positive_rate"]
    acc = m["accuracy"]
    assert isinstance(tpr, RateCI)
    assert tpr.n == 10 and abs(tpr.rate - 0.8) < 1e-9
    assert fpr.n == 20 and abs(fpr.rate - 0.1) < 1e-9
    assert acc.n == 30 and abs(acc.rate - (26 / 30)) < 1e-3
    for r in (tpr, fpr, acc):
        assert 0.0 <= r.lo <= r.rate <= r.hi <= 1.0


# --- check_determinism -------------------------------------------------------- #

def _pure(x):
    return x * 2 + 1


def test_check_determinism_pure_true():
    res = check_determinism(_pure, 21)
    assert isinstance(res, DeterminismResult)
    assert res.deterministic is True
    assert res.n_runs == 3
    assert isinstance(res.note, str)


_counter = {"v": 0}


def _impure():
    _counter["v"] += 1
    return _counter["v"]


def test_check_determinism_impure_false():
    _counter["v"] = 0
    res = check_determinism(_impure, runs=3)
    assert res.deterministic is False
    assert res.n_runs == 3


def test_check_determinism_array_output():
    import numpy as np

    def _arr():
        return np.array([1.0, 2.0, 3.0])

    res = check_determinism(_arr)
    assert res.deterministic is True
    assert res.as_dict()["deterministic"] is True
