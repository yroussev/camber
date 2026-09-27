"""Lag-1 autocorrelation on monthly and billing rows, and the caveat when it is unknown (#51)."""

import numpy as np
import pandas as pd
import pytest

from camber.mandv.methods import backcast_savings, forecast_savings
from camber.mandv.models import fit_model
from camber.mandv.stats import _RHO_UNKNOWN_CAVEAT, lag1_autocorrelation


def _ar1(n, rho, seed, sd=40.0):
    rng = np.random.default_rng(seed)
    e = np.zeros(n)
    e[0] = rng.normal(0, sd / np.sqrt(1 - rho**2))
    for i in range(1, n):
        e[i] = rho * e[i - 1] + rng.normal(0, sd)
    return e


def _bill_ends(n, seed):
    rng = np.random.default_rng(seed)
    days = np.cumsum(rng.integers(28, 36, n))  # utility billing cycles, 28-35 days
    return pd.DatetimeIndex(pd.Timestamp("2018-01-15") + pd.to_timedelta(days, unit="D"))


@pytest.mark.parametrize("n", [36, 60])
def test_monthly_and_billing_indices_admit_every_neighbour(n):
    e = _ar1(n, 0.5, seed=n)
    plain = lag1_autocorrelation(e, min_points=10)  # no index: every pair
    ms = pd.date_range("2018-01-01", periods=n, freq="MS")
    assert lag1_autocorrelation(e, index=ms) == pytest.approx(plain)
    assert lag1_autocorrelation(e, index=_bill_ends(n, seed=n)) == pytest.approx(plain)


def test_monthly_ar1_is_recovered():
    # a long series so the estimate is tight: the true rho is 0.5
    est = [
        lag1_autocorrelation(
            _ar1(240, 0.5, s), index=pd.date_range("2000-01-01", periods=240, freq="MS")
        )
        for s in range(20)
    ]
    assert all(r is not None for r in est)
    assert np.mean(est) == pytest.approx(0.5, abs=0.06)


def test_contiguous_billing_periods_are_neighbours_and_a_gap_is_not():
    n = 40
    e = _ar1(n, 0.5, seed=3)
    ends = _bill_ends(n, seed=3)
    starts = ends[:-1].insert(0, ends[0] - pd.Timedelta(days=30))  # next starts where last ended
    got = lag1_autocorrelation(e, index=ends, period_start=starts, period_end=ends)
    assert got == pytest.approx(lag1_autocorrelation(e, min_points=10))
    # the other convention (starts the day after the previous end) is contiguous too
    got2 = lag1_autocorrelation(
        e, index=ends, period_start=starts + pd.Timedelta(days=1), period_end=ends
    )
    assert got2 == pytest.approx(got)
    # a missing bill (a 2-month hole) breaks the pairs around it
    keep = np.r_[0:20, 21:n]
    holed = lag1_autocorrelation(
        e[keep], index=ends[keep], period_start=starts[keep], period_end=ends[keep], min_points=3
    )
    a, b = e[keep][:-1], e[keep][1:]
    mask = np.ones(len(a), bool)
    mask[19] = False
    assert holed == pytest.approx(max(0.0, float(np.corrcoef(a[mask], b[mask])[0, 1])))
    with pytest.raises(ValueError, match="together"):
        lag1_autocorrelation(e, period_start=starts)
    with pytest.raises(ValueError, match="same length"):
        lag1_autocorrelation(e, period_start=starts[:5], period_end=ends[:5])


def test_daily_spacing_is_judged_exactly_as_before():
    n = 60
    e = _ar1(n, 0.4, seed=7)
    days = pd.date_range("2024-01-01", periods=n + 1, freq="D").delete(30)  # one 2-day gap
    a, b = e[:-1], e[1:]
    mask = np.ones(n - 1, bool)
    mask[29] = False  # the pair across the gap is not adjacent
    want = max(0.0, float(np.corrcoef(a[mask], b[mask])[0, 1]))
    assert lag1_autocorrelation(e, index=days) == pytest.approx(want)


def test_monthly_fit_records_rho_and_forecast_uses_it():
    n = 36
    idx = pd.date_range("2018-01-01", periods=n, freq="MS")
    T = 50 + 25 * np.sin(2 * np.pi * (np.arange(n) - 3.5) / 12)
    y = 300 + 25 * np.clip(60 - T, 0, None) + _ar1(n, 0.6, seed=0)
    m = fit_model(T, y, "3PH", time_index=idx)
    assert m._fit_record.rho is not None and m._fit_record.rho > 0.3
    fs = forecast_savings(
        m, T[:12], 0.9 * y[:12], cv_rmse=0.1, n_baseline=n, p_baseline=3, kernel="exact"
    )
    assert fs.fsu_autocorrelation_adjusted and _RHO_UNKNOWN_CAVEAT not in fs.caveats


def test_unknown_rho_is_a_caveat_not_a_silent_zero():
    # a one-year monthly baseline has 11 pairs: too few to estimate rho
    idx = pd.date_range("2018-01-01", periods=12, freq="MS")
    T = 50 + 25 * np.sin(2 * np.pi * (np.arange(12) - 3.5) / 12)
    y = 300 + 25 * np.clip(60 - T, 0, None) + _ar1(12, 0.5, seed=1)
    m = fit_model(T, y, "3PH", time_index=idx)
    assert m._fit_record.rho is None
    for kernel in ("g14", "exact"):
        fs = forecast_savings(
            m, T, 0.9 * y, cv_rmse=0.1, n_baseline=12, p_baseline=3, kernel=kernel
        )
        assert fs.rho is None and not fs.fsu_autocorrelation_adjusted
        assert _RHO_UNKNOWN_CAVEAT in fs.caveats
    bc = backcast_savings(m, T, 0.9 * y, cv_rmse=0.1, n_reporting=12, p_reporting=3, kernel="g14")
    assert _RHO_UNKNOWN_CAVEAT in bc.caveats
