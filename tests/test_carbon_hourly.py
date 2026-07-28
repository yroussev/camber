"""Tests for hourly/marginal Scope-2 carbon (camber.carbon_hourly)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.carbon_hourly import hourly_emissions, marginal_vs_average  # noqa: E402


def _s(vals, start="2026-07-01"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="1h"))


def test_hourly_emissions_basic():
    load = _s([10.0] * 24)  # 10 kW every hour -> 240 kWh
    ef = _s([0.5] * 24)  # 0.5 kg/kWh flat
    e = hourly_emissions(load, ef)
    assert e.kwh == 240.0 and abs(e.co2e_kg - 120.0) < 1e-6
    assert abs(e.effective_factor - 0.5) < 1e-9
    assert abs(e.timing_premium_pct) < 1e-6  # flat factor -> no timing premium


def test_timing_premium_positive_when_running_dirty_hours():
    # dirty midday (0.8), clean otherwise (0.2); building runs mostly midday -> premium > 0
    ef = _s([0.2] * 8 + [0.8] * 8 + [0.2] * 8)
    load = _s([10.0] * 8 + [90.0] * 8 + [10.0] * 8)
    e = hourly_emissions(load, ef)
    assert e.effective_factor > e.avg_factor and e.timing_premium_pct > 0


def test_timing_premium_negative_when_running_clean_hours():
    ef = _s([0.2] * 8 + [0.8] * 8 + [0.2] * 8)
    load = _s([90.0] * 8 + [10.0] * 8 + [90.0] * 8)  # avoids the dirty block
    e = hourly_emissions(load, ef)
    assert e.timing_premium_pct < 0


def test_grams_per_kwh_unit():
    load = _s([10.0] * 4)
    ef_g = _s([500.0] * 4)  # g/kWh
    e = hourly_emissions(load, ef_g, unit_kg_per_kwh=False)
    assert abs(e.co2e_kg - 20.0) < 1e-6  # 40 kWh × 0.5 kg/kWh
    # avg_factor and effective_factor must be reported in the same (kg/kWh) unit
    assert abs(e.avg_factor - 0.5) < 1e-9  # 500 g/kWh -> 0.5 kg/kWh, not left at 500
    assert abs(e.effective_factor - e.avg_factor) < 1e-9  # flat factor -> equal, comparable


def test_marginal_vs_average():
    load = _s([20.0] * 12)
    avg = _s([0.35] * 12)
    marg = _s([0.6] * 12)  # marginal dirtier than average
    c = marginal_vs_average(load, avg, marg)
    assert c.co2e_marginal_kg > c.co2e_avg_kg
    assert abs(c.marginal_over_avg - (0.6 / 0.35)) < 0.01


def test_empty_load():
    e = hourly_emissions(_s([]), _s([]))
    assert e.co2e_kg == 0.0
