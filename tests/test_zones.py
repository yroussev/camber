"""Tests for schedules + zones (fleet heating-vs-cooling census)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.schedules import occupied_mask, day_type, time_of_week_bin  # noqa: E402
from camber.zones import zone_states, time_of_week_profile  # noqa: E402


def _idx(n=48):
    # start Monday 2025-07-07 00:00
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_occupied_mask_weekday_window():
    idx = _idx(48)
    m = occupied_mask(idx)
    # hour 10 Monday occupied, hour 3 not, hour 10 Saturday not
    assert m.loc[pd.Timestamp("2025-07-07 10:00")]
    assert not m.loc[pd.Timestamp("2025-07-07 03:00")]


def test_day_type():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    dt = day_type(idx)
    assert dt.loc[pd.Timestamp("2025-07-07 12:00")] == "weekday"   # Monday
    assert dt.loc[pd.Timestamp("2025-07-12 12:00")] == "weekend"   # Saturday


def test_time_of_week_bin_range():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    b = time_of_week_bin(idx)
    assert b.min() == 0
    assert b.max() == 6 * 24 + 23


def test_zone_states_counts():
    idx = _idx(24)
    # zone A: heating on (valve 50), no cooling flow
    a = pd.DataFrame({"HWValve": 50.0, "ActFlow": 100.0, "ActFlowSP": 200.0}, index=idx)
    # zone B: cooling (flow above SP), no heating
    b = pd.DataFrame({"HWValve": 0.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    # zone C: BOTH heating and cooling (reheat penalty)
    c = pd.DataFrame({"HWValve": 40.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    st = zone_states({"A": a, "B": b, "C": c})
    assert (st["n_zones"] == 3).all()
    assert (st["n_heating"] == 2).all()   # A and C
    assert (st["n_cooling"] == 2).all()   # B and C
    assert (st["n_both"] == 1).all()      # C only


def test_time_of_week_profile_shape():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    a = pd.DataFrame({"HWValve": 50.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    st = zone_states({"A": a})
    prof = time_of_week_profile(st, occupied_only=True)
    assert not prof.empty
    assert set(["n_zones", "n_heating", "n_cooling", "n_both"]).issubset(prof.columns)
