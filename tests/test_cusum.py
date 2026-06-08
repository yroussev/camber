"""Tests for CUSUM savings tracking (mandv.cusum)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.cusum import control_exceedances, cusum, cusum_savings  # noqa: E402


def _pair():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    proj = pd.Series([10.0, 10.0, 10.0], index=idx)
    act = pd.Series([8.0, 8.0, 8.0], index=idx)
    return proj, act


def test_cusum_accumulates_difference():
    proj, act = _pair()
    s = cusum(proj, act)
    assert list(s) == [2.0, 4.0, 6.0]


def test_cusum_savings_summary():
    proj, act = _pair()
    r = cusum_savings(proj, act)
    assert r.total == 6.0
    assert r.mean_rate == 2.0
    assert r.n == 3
    assert r.accumulating_savings


def test_cusum_waste_is_negative():
    proj, act = _pair()
    r = cusum_savings(proj, act[::-1] * 0 + 12.0)  # actual 12 > projected 10
    assert r.total < 0
    assert not r.accumulating_savings


def test_control_exceedances_flags_crossing():
    proj, act = _pair()
    ex = control_exceedances(proj, act, limit=5.0)
    # cumulative hits 6 only at the 3rd point
    assert len(ex) == 1
    assert ex.iloc[0] == 6.0
