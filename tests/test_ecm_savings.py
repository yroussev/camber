"""Tests for measured-waste ECM savings estimation."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.ecm_savings import (  # noqa: E402
    BTU_PER_THERM, heating_in_cooling_weather, simultaneous_heat_cool_energy,
    unoccupied_cooling_energy,
)


def _rate(n, val, start="2024-06-01", freq="1h"):
    idx = pd.date_range(start, periods=n, freq=freq)
    return pd.Series(float(val), index=idx)


def test_heating_in_cooling_weather_counts_hot_hours_only():
    n = 240
    idx = pd.date_range("2024-06-01", periods=n, freq="1h")
    hhw = pd.Series(10000.0, index=idx)                  # 10k BTU/hr constant heating
    oat = pd.Series(np.where(np.arange(n) % 2 == 0, 90.0, 50.0), index=idx)  # half hot
    r = heating_in_cooling_weather(hhw, oat, cutoff_f=70.0,
                                   window=("2024-06-01", "2024-06-30"))
    # ~half the hours are >70F -> ~half the heating energy flagged
    assert 40.0 < r.waste_fraction_pct < 60.0
    assert "therms" in r.waste_display
    assert "upper-bound" in r.basis


def test_simultaneous_counts_overlap_only():
    n = 200
    idx = pd.date_range("2024-06-01", periods=n, freq="1h")
    hhw = pd.Series(5000.0, index=idx)
    # cooling on only in the first half -> overlap only there
    chw = pd.Series(np.where(np.arange(n) < 100, 8000.0, 0.0), index=idx)
    r = simultaneous_heat_cool_energy(hhw, chw, window=("2024-06-01", "2024-06-30"))
    assert 45.0 < r.waste_fraction_pct < 55.0           # ~half overlaps


def test_no_overlap_zero_waste():
    n = 200
    idx = pd.date_range("2024-06-01", periods=n, freq="1h")
    hhw = pd.Series(np.where(np.arange(n) < 100, 5000.0, 0.0), index=idx)
    chw = pd.Series(np.where(np.arange(n) >= 100, 8000.0, 0.0), index=idx)  # never together
    r = simultaneous_heat_cool_energy(hhw, chw, window=("2024-06-01", "2024-06-30"))
    assert r.waste_btu == 0.0
    assert r.waste_fraction_pct == 0.0


def test_unoccupied_cooling_flags_nights_weekends():
    # a full week of constant cooling -> unoccupied fraction should be the majority
    idx = pd.date_range("2024-06-03", periods=24 * 7, freq="1h")  # Mon..Sun
    chw = pd.Series(10000.0, index=idx)
    r = unoccupied_cooling_energy(chw, window=("2024-06-03", "2024-06-09"))
    # occupied window is weekday 7-18 (55 hrs of 168) -> unoccupied ~67%
    assert r.waste_fraction_pct > 60.0
    assert "ton-hours" in r.waste_display


def test_therm_conversion():
    n = 100
    idx = pd.date_range("2024-06-01", periods=n, freq="1h")
    hhw = pd.Series(BTU_PER_THERM, index=idx)            # 1 therm/hr
    oat = pd.Series(90.0, index=idx)                     # all hot
    r = heating_in_cooling_weather(hhw, oat, cutoff_f=70.0,
                                   window=("2024-06-01", "2024-06-30"))
    # ~100 therms total flagged (1 therm/hr * ~100 hr)
    assert 90 < r.waste_btu / BTU_PER_THERM < 110
