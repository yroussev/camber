"""Tests for rate/energy-aware resampling."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.resample import (  # noqa: E402
    cumulative_to_interval,
    is_cumulative,
    resample,
    resample_energy,
)


def _idx(n, freq="15min"):
    return pd.date_range("2025-07-01", periods=n, freq=freq)


def test_time_weighted_sum_preserves_energy():
    # 4x 15-min intervals of 1 kWh each -> 1 hourly bin of 4 kWh (energy preserved)
    s = pd.Series([1.0, 1.0, 1.0, 1.0], index=_idx(4, "15min"))
    out = resample(s, "1h", method="time_weighted_sum")
    assert out.iloc[0] == 4.0


def test_mean_loses_energy_on_upsample():
    # the cautionary case: averaging energy understates the hourly total
    s = pd.Series([1.0, 1.0, 1.0, 1.0], index=_idx(4, "15min"))
    out = resample(s, "1h", method="mean")
    assert out.iloc[0] == 1.0          # mean = 1, NOT the 4 kWh total


def test_mean_correct_for_rate():
    # a steady 10 kW rate over the hour resamples (by mean) to 10 kW -- correct
    s = pd.Series([10.0, 10.0, 10.0, 10.0], index=_idx(4, "15min"))
    out = resample(s, "1h", method="mean")
    assert out.iloc[0] == 10.0


def test_nearest_prior_step():
    s = pd.Series([1.0, np.nan, np.nan, 0.0], index=_idx(4, "15min"))
    out = resample(s, "15min", method="nearest_prior")
    assert out.iloc[1] == 1.0          # carried forward
    assert out.iloc[3] == 0.0


def test_is_cumulative_detects_register():
    cum = pd.Series(np.cumsum(np.full(50, 2.0)), index=_idx(50, "1h"))
    assert is_cumulative(cum)
    rate = pd.Series(np.random.default_rng(0).normal(50, 10, 50), index=_idx(50, "1h"))
    assert not is_cumulative(rate)


def test_cumulative_to_interval():
    cum = pd.Series([100.0, 102, 105, 105, 110], index=_idx(5, "1h"))
    d = cumulative_to_interval(cum)
    assert np.isnan(d.iloc[0])
    assert d.iloc[1] == 2.0 and d.iloc[2] == 3.0 and d.iloc[3] == 0.0


def test_cumulative_rollover_to_nan():
    cum = pd.Series([100.0, 102, 5, 8], index=_idx(4, "1h"))   # reset at idx 2
    d = cumulative_to_interval(cum)
    assert np.isnan(d.iloc[2])         # negative diff -> NaN, not a huge negative


def test_resample_energy_end_to_end():
    # cumulative register climbing 1 kWh per 15 min -> hourly totals of ~4 kWh
    cum = pd.Series(np.cumsum(np.full(8, 1.0)), index=_idx(8, "15min"))
    out = resample_energy(cum, "1h")
    # first hour: diffs of the cumulative (1+1+1) after the NaN first = 3; second hour 4
    assert out.dropna().iloc[-1] == 4.0
