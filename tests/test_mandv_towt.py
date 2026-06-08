"""Tests for the Time-of-Week & Temperature (TOWT) baseline (mandv.towt)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.stats import fit_stats  # noqa: E402
from camber.mandv.towt import fit_towt, hour_of_week  # noqa: E402


def _synthetic(weeks=6, seed=0):
    """Hourly series: weekday-occupied schedule + a cooling temperature slope."""
    idx = pd.date_range("2024-01-01", periods=weeks * 7 * 24, freq="1h")
    rng = np.random.default_rng(seed)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    temp = 60 + 15 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, len(idx))
    base = 10.0 + 20.0 * occ                       # weekly schedule
    cooling = np.clip(temp - 68, 0, None) * 0.8    # weather term
    energy = base + cooling + rng.normal(0, 0.5, len(idx))
    return pd.Series(energy, index=idx), pd.Series(temp, index=idx)


def test_hour_of_week_origin_and_range():
    idx = pd.DatetimeIndex(["2024-01-01 00:00",   # Monday 00:00 -> 0
                            "2024-01-01 01:00",   # Monday 01:00 -> 1
                            "2024-01-07 23:00"])  # Sunday 23:00 -> 167
    assert list(hour_of_week(idx)) == [0, 1, 167]


def test_towt_recovers_schedule_and_weather():
    e, t = _synthetic()
    m = fit_towt(e, t, occ_split=True)
    yhat = m.predict(e.index, t.values)
    st = fit_stats(e.values, yhat, m.n_params, cv_rmse_max=0.30)
    assert st.r2 > 0.95                 # captures weekly shape + temperature
    assert st.cv_rmse < 0.15


def test_towt_predict_matches_length_and_is_finite():
    e, t = _synthetic()
    m = fit_towt(e, t)
    yhat = m.predict(e.index, t.values)
    assert len(yhat) == len(e)
    assert np.isfinite(yhat).all()


def test_occ_split_adds_parameters():
    e, t = _synthetic()
    m_split = fit_towt(e, t, occ_split=True)
    m_plain = fit_towt(e, t, occ_split=False)
    assert m_split.split and not m_plain.split
    assert m_split.occ_bins is not None and m_plain.occ_bins is None
    # split fits separate occupied/unoccupied temperature blocks -> more params
    assert m_split.n_params >= m_plain.n_params


def test_towt_needs_minimum_observations():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h")
    with pytest.raises(ValueError):
        fit_towt(pd.Series(range(10), index=idx, dtype=float),
                 pd.Series(range(10), index=idx, dtype=float))


def test_towt_beats_naive_mean_on_structured_load():
    e, t = _synthetic()
    m = fit_towt(e, t)
    yhat = m.predict(e.index, t.values)
    sse = float(((e.values - yhat) ** 2).sum())
    sst = float(((e.values - e.values.mean()) ** 2).sum())
    assert sse < 0.1 * sst              # explains >90% of variance
