"""Tests for operating-schedule inference (camber.schedule)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.schedule import WeeklySchedule, compare_schedule, detect_schedule  # noqa: E402


def _load(weeks=4, seed=0):
    idx = pd.date_range("2024-06-03", periods=weeks * 7 * 24, freq="1h")  # starts on a Monday
    occ = ((idx.hour >= 8) & (idx.hour < 18) & (idx.dayofweek < 5)).astype(float)
    return pd.Series(40 + 60 * occ + np.random.default_rng(seed).normal(0, 3, len(idx)), index=idx)


def test_detects_weekday_daytime_and_weekend_off():
    sch = detect_schedule(_load())
    assert isinstance(sch, WeeklySchedule)
    mon = sch.days[0]
    assert 7 <= mon.start_hour <= 9 and 16 <= mon.end_hour <= 18  # ~8–18 occupancy
    assert sch.days[5].on_hours == 0 and sch.days[6].on_hours == 0  # weekend off
    assert sch.is_on(0, 12) and not sch.is_on(5, 12)


def test_occupied_fraction_reasonable():
    sch = detect_schedule(_load())
    # ~10 h × 5 weekdays / 168 ≈ 0.30
    assert 0.25 < sch.occupied_fraction < 0.35


def test_compare_to_stated_schedule():
    sch = detect_schedule(_load())
    stated = [(d, h) for d in range(5) for h in range(9, 17)]  # weekday 9–5
    cmp = compare_schedule(sch, stated)
    assert cmp["agreement"] > 0.9
    assert cmp["n_extra"] > 0 and cmp["n_missing"] == 0  # runs a bit beyond 9–5
    # detected 8:00 is extra runtime vs a 9–5 schedule
    assert (0, 8) in cmp["extra_runtime_slots"]


def test_custom_threshold_changes_detection():
    load = _load()
    high = detect_schedule(load, threshold=200.0)  # nothing exceeds it
    assert high.occupied_fraction == 0.0


def test_empty_series():
    sch = detect_schedule(pd.Series(dtype=float))
    assert sch.on_slots == [] and sch.occupied_fraction != sch.occupied_fraction  # NaN
