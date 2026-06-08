"""Tests for interval-meter -> change-point input (daily/hourly energy vs temp)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.intervalfit import (  # noqa: E402
    daily_energy_vs_temp,
    hourly_energy_vs_temp,
    rate_to_energy,
)


def _rate_15min(days, btu_per_hr):
    idx = pd.date_range("2025-01-01", periods=days * 96, freq="15min")
    return pd.Series(float(btu_per_hr), index=idx)


def test_rate_to_energy_daily():
    # constant 1000 BTU/hr for 1 day -> 24,000 BTU that day
    s = _rate_15min(1, 1000.0)
    e = rate_to_energy(s, "D")
    assert abs(e.iloc[0] - 24000.0) < 1.0


def test_rate_to_energy_hourly():
    s = _rate_15min(1, 2000.0)
    e = rate_to_energy(s, "1h")
    # each hour = 2000 BTU/hr * 1 h = 2000 BTU
    assert abs(e.iloc[0] - 2000.0) < 1.0
    assert len(e) >= 24


def test_daily_energy_vs_temp_pairs_and_aggregates():
    rate = _rate_15min(10, 1000.0)
    oat = pd.Series(np.linspace(40, 60, len(rate)), index=rate.index)
    df = daily_energy_vs_temp(rate, oat)
    assert "energy" in df and "oat" in df
    assert len(df) >= 9                       # ~10 days
    assert abs(df["energy"].iloc[0] - 24000.0) < 100   # ~24k BTU/day


def test_hourly_has_hour_column():
    rate = _rate_15min(3, 1500.0)
    oat = pd.Series(np.full(len(rate), 50.0), index=rate.index)
    df = hourly_energy_vs_temp(rate, oat)
    assert set(df["hour"].unique()) <= set(range(24))
    assert abs(df["energy"].iloc[0] - 1500.0) < 50


def test_energy_series_summed_not_integrated_when_flagged():
    # already-energy-per-interval input: summed, not multiplied by hours
    idx = pd.date_range("2025-01-01", periods=96, freq="15min")
    e_per_interval = pd.Series(10.0, index=idx)   # 10 units per 15-min
    oat = pd.Series(50.0, index=idx)
    df = daily_energy_vs_temp(e_per_interval, oat, rate_is_energy_rate=False)
    assert abs(df["energy"].iloc[0] - 960.0) < 1   # 96 * 10


def test_degree_days_heating():
    from camber.mandv.intervalfit import degree_days
    # constant 50F for 1 day, base 65 -> 15 HDD that day
    idx = pd.date_range("2025-01-01", periods=24, freq="1h")
    oat = pd.Series(50.0, index=idx)
    hdd = degree_days(oat, "D", base_f=65, kind="heating")
    assert abs(hdd.iloc[0] - 15.0) < 1e-6


def test_degree_days_cooling_zero_when_cold():
    from camber.mandv.intervalfit import degree_days
    idx = pd.date_range("2025-01-01", periods=24, freq="1h")
    oat = pd.Series(50.0, index=idx)
    cdd = degree_days(oat, "D", base_f=65, kind="cooling")
    assert cdd.iloc[0] == 0.0          # 50F is below the cooling base -> no CDD


def test_degree_days_from_hourly_not_mean():
    # a day averaging 65F but with cold mornings still has heating degree-days,
    # which a daily-mean-based HDD would miss
    from camber.mandv.intervalfit import degree_days
    idx = pd.date_range("2025-01-01", periods=24, freq="1h")
    half = np.concatenate([np.full(12, 45.0), np.full(12, 85.0)])  # mean 65
    oat = pd.Series(half, index=idx)
    hdd = degree_days(oat, "D", base_f=65, kind="heating")
    assert hdd.iloc[0] > 9            # 12h * 20deg / 24 = 10 HDD, not 0


def test_energy_vs_degree_days_frame():
    from camber.mandv.intervalfit import energy_vs_degree_days
    idx = pd.date_range("2025-01-01", periods=3 * 96, freq="15min")
    rate = pd.Series(1000.0, index=idx)          # 1000 BTU/hr
    oat = pd.Series(np.linspace(40, 60, len(idx)), index=idx)
    df = energy_vs_degree_days(rate, oat, freq="D", base_f=65, kind="heating")
    assert "energy" in df and "dd" in df
    assert (df["dd"] > 0).all()                  # all days below base -> positive HDD
