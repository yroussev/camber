"""Tests for demand & peak analytics (camber.demand)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.demand import (  # noqa: E402
    analyze_demand, baseload_anomaly, peak_shave_savings,
)


def _peaky_load(days=60, base=20.0, bump=80.0):
    """Baseload + weekday-afternoon (12-18h) bump -> a peaky load."""
    idx = pd.date_range("2025-06-01", periods=days * 24, freq="1h")
    hour = idx.hour
    weekday = idx.dayofweek < 5
    day = np.where(weekday & (hour >= 12) & (hour < 18), bump, 0.0)
    return pd.Series(base + day, index=idx)


# --- analyze_demand ----------------------------------------------------------- #

def test_peak_baseload_and_load_factor():
    r = analyze_demand(_peaky_load())
    assert abs(r.peak_kw - 100.0) < 0.5            # 20 base + 80 bump
    assert abs(r.baseload_kw - 20.0) < 1.0
    assert 0.2 < r.load_factor < 0.6               # spiky load
    assert 12 <= r.coincident_peak_hour < 18       # peaks in the afternoon
    assert r.pct_intervals_near_peak < 30          # few intervals at peak
    assert len(r.monthly_peak_kw) >= 2


def test_analyze_demand_insufficient():
    assert analyze_demand(pd.Series([1.0, 2.0], index=pd.date_range("2025-01-01", periods=2, freq="1h"))) is None


# --- baseload anomaly --------------------------------------------------------- #

def test_baseload_ok_with_setback():
    r = baseload_anomaly(_peaky_load())            # nights/weekends drop to baseload
    assert r.severity == "ok"
    assert r.baseload_ratio < 0.6


def test_baseload_fault_when_always_on():
    idx = pd.date_range("2025-06-01", periods=60 * 24, freq="1h")
    flat = pd.Series(np.full(len(idx), 50.0), index=idx)   # runs 24/7, no setback
    r = baseload_anomaly(flat)
    assert r.severity == "fault"
    assert abs(r.baseload_ratio - 1.0) < 0.05


# --- peak shave value --------------------------------------------------------- #

def test_peak_shave_savings():
    load = _peaky_load(days=60)                     # peak 100 kW, 2 months (Jun, Jul)
    r = peak_shave_savings(load, target_kw=80.0, demand_rate=13.0)
    assert r.n_months >= 2
    # each month shaves 100 -> 80 = 20 kW @ $13 = $260
    for m in r.monthly.values():
        assert abs(m["shaved_kw"] - 20.0) < 0.5
        assert abs(m["savings"] - 260.0) < 1.0
    assert r.annual_savings > 500


def test_peak_shave_no_savings_above_peak():
    load = _peaky_load()
    r = peak_shave_savings(load, target_kw=200.0, demand_rate=13.0)   # target above peak
    assert r.annual_savings == 0.0
