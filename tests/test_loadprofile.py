"""Tests for load-profile / peak metrics (loadprofile.py)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.loadprofile import (  # noqa: E402
    daily_profile, load_duration, load_metrics, weekday_weekend_profiles,
)


def test_load_metrics_basic():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h")
    s = pd.Series(range(1, 11), index=idx, dtype="float64")   # 1..10
    m = load_metrics(s)
    assert m.peak == 10.0 and m.base == 1.0
    assert m.near_peak == 8.0 and m.near_base == 3.0          # 3rd from each end
    assert m.mean == 5.5
    assert m.load_factor == 0.55                              # 5.5 / 10
    assert abs(m.base_to_peak - 3.0 / 8.0) < 1e-9


def test_load_metrics_needs_three_points():
    s = pd.Series([1.0, 2.0], index=pd.date_range("2024-01-01", periods=2, freq="h"))
    with pytest.raises(ValueError):
        load_metrics(s)


def test_load_duration_descending():
    s = pd.Series([2.0, 9.0, 5.0])
    assert list(load_duration(s)) == [9.0, 5.0, 2.0]


def test_daily_profile_by_hour():
    idx = pd.date_range("2024-01-01", periods=48, freq="1h")
    s = pd.Series([float(h) for h in idx.hour], index=idx)
    prof = daily_profile(s)
    assert len(prof) == 24
    assert prof.loc[0] == 0.0 and prof.loc[23] == 23.0


def test_weekday_weekend_split():
    idx = pd.date_range("2024-01-01", periods=24 * 7, freq="1h")  # Mon..Sun
    s = pd.Series(1.0, index=idx)
    wk, we = weekday_weekend_profiles(s)
    assert len(wk) == 24 and len(we) == 24
