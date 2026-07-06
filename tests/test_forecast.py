"""Tests for the load forecaster + learned-normal anomaly detection (camber.forecast)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.forecast import (  # noqa: E402
    backtest, forecast_anomalies, seasonal_forecast,
)


def _weekly_load(weeks=4, seed=0):
    """Hourly load with a clear daily/weekly occupancy shape."""
    idx = pd.date_range("2026-05-01", periods=weeks * 7 * 24, freq="1h")
    rng = np.random.default_rng(seed)
    occ = ((idx.hour >= 7) & (idx.hour <= 18) & (idx.dayofweek < 5)).astype(float)
    return pd.Series(40 + 60 * occ + rng.normal(0, 1.5, len(idx)), index=idx)


def test_seasonal_forecast_recovers_occupancy_shape():
    h = _weekly_load(weeks=4)
    horizon = pd.date_range(h.index[-1] + pd.Timedelta(hours=1), periods=48, freq="1h")
    fc = seasonal_forecast(h, horizon)
    assert fc.notna().all() and len(fc) == 48
    # occupied-hour forecast should be well above an unoccupied-hour forecast
    occ = fc[(fc.index.hour >= 9) & (fc.index.hour <= 16) & (fc.index.dayofweek < 5)]
    uno = fc[(fc.index.hour >= 0) & (fc.index.hour <= 4)]
    assert occ.mean() > uno.mean() + 30


def test_seasonal_forecast_empty_history():
    fc = seasonal_forecast(pd.Series(dtype=float),
                           pd.date_range("2026-06-01", periods=5, freq="1h"))
    assert fc.empty or fc.isna().all()               # no history -> no usable forecast


def test_backtest_reports_reasonable_accuracy():
    h = _weekly_load(weeks=4)
    r = backtest(h, test_frac=0.25)
    assert "cv_rmse" in r and r["n_test"] > 0
    assert r["cv_rmse"] < 0.5                        # a clean seasonal signal fits well


def test_forecast_anomalies_flags_injected_spikes():
    h = _weekly_load(weeks=4, seed=1)
    # forecast the last week from the first three, then inject spikes into the actual
    horizon = h.index[-7 * 24:]
    fc = seasonal_forecast(h.iloc[:-7 * 24], horizon)
    actual = h.loc[horizon].copy()
    actual.iloc[20] += 200.0                          # two clear anomalies
    actual.iloc[100] -= 200.0
    rep = forecast_anomalies(actual, fc, k=4.0)
    assert rep.n_anomalies >= 2 and rep.anomaly_frac > 0
    assert len(rep.timestamps) == rep.n_anomalies


def test_forecast_anomalies_clean_series_few_flags():
    h = _weekly_load(weeks=4, seed=2)
    horizon = h.index[-7 * 24:]
    fc = seasonal_forecast(h.iloc[:-7 * 24], horizon)
    rep = forecast_anomalies(h.loc[horizon], fc, k=4.0)
    assert rep.anomaly_frac < 0.05                    # a matching forecast -> few anomalies
    assert rep.mae >= 0
