"""Tests for load disaggregation (camber.disaggregate)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.disaggregate import LoadComponents, disaggregate_load  # noqa: E402


def _weather_load(days=30, base=40.0, seed=0):
    idx = pd.date_range("2025-06-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    oat = pd.Series(
        70 + 15 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24) + rng.normal(0, 2, len(idx)),
        index=idx,
    )
    cooling = 1.5 * np.clip(oat.to_numpy() - 65, 0, None)
    occ = np.where((idx.hour >= 8) & (idx.hour <= 18), 30.0, 0.0)
    load = pd.Series(base + cooling + occ + rng.normal(0, 1, len(idx)), index=idx)
    return load, oat


def test_components_sum_to_total():
    load, oat = _weather_load()
    c = disaggregate_load(load, oat)
    assert isinstance(c, LoadComponents)
    assert abs((c.baseload_kwh + c.weather_kwh + c.other_kwh) - c.total_kwh) < 1.0
    assert abs((c.baseload_frac + c.weather_frac + c.other_frac) - 1.0) < 1e-3


def test_baseload_detected_near_floor():
    load, oat = _weather_load(base=40.0)
    c = disaggregate_load(load, oat)
    assert 36 < c.baseload_kw < 44 and c.baseload_frac > 0.4  # ~40 kW always-on floor


def test_weather_component_present_when_weather_driven():
    load, oat = _weather_load()
    c = disaggregate_load(load, oat)
    assert c.weather_kwh > 0 and c.weather_frac > 0.1


def test_flat_load_is_all_baseload():
    idx = pd.date_range("2025-06-01", periods=200, freq="1h")
    load = pd.Series(50.0, index=idx)
    oat = pd.Series(np.linspace(40, 90, 200), index=idx)
    c = disaggregate_load(load, oat)
    assert c.baseload_frac > 0.98 and c.weather_kwh < 1.0 and c.other_kwh < 1.0


def test_fixed_balance_point_used():
    load, oat = _weather_load()
    c = disaggregate_load(load, oat, balance_point=65.0)
    assert c.balance_point_f == 65.0


def test_empty_input():
    c = disaggregate_load(pd.Series(dtype=float), pd.Series(dtype=float))
    assert c.total_kwh == 0 and c.baseload_frac != c.baseload_frac  # NaN
