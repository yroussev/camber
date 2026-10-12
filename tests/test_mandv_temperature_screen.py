"""Baseline fits screen implausible temperatures before fitting (0.105, #159).

A placeholder such as -237 for a missing outdoor temperature used to enter the fit as the coldest
point -- TOWT's first temperature breakpoint, a change-point model's arm. Every fit now treats
values outside the sensor-health outdoor range (-40 to 140 degF) as missing and counts them. A TOWT
row without a temperature predicts NaN (it used to predict its time-of-week level alone), and the
savings sums exclude and count those rows. Synthetic data only.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv._temperature import oat_bounds, screen_temperature  # noqa: E402
from camber.mandv.degreeday import DegreeDayModel, fit_degree_day  # noqa: E402
from camber.mandv.models import ChangePointModel, best_model, fit_model  # noqa: E402
from camber.mandv.towt import TOWTAtIndex, TOWTModel, fit_towt  # noqa: E402

SENTINEL = -237.0


def test_the_screen_shares_the_sensor_health_range():
    from camber.model.roles import Role
    from camber.sensorhealth import PHYSICAL_BOUNDS, range_violation_frac

    assert oat_bounds() == PHYSICAL_BOUNDS[Role.OAT] == (-40.0, 140.0)
    x = pd.Series([50.0, SENTINEL, np.nan, 140.0, -40.0, 141.0, np.inf])
    out, n = screen_temperature(x)
    assert n == 3 and out.isna().tolist() == [False, True, True, False, False, True, True]
    assert range_violation_frac(x.replace(np.inf, np.nan), Role.OAT) > 0  # sensor health agrees
    arr, n = screen_temperature(np.array([1.0, SENTINEL]))
    assert n == 1 and np.isnan(arr[1]) and arr[0] == 1.0
    same, n = screen_temperature(pd.Series([1.0, 2.0]))
    assert n == 0
    kept, n = screen_temperature([SENTINEL], bounds=(-np.inf, np.inf))
    assert n == 0 and kept[0] == SENTINEL


def _daily(n=200, seed=0):
    rng = np.random.default_rng(seed)
    T = rng.uniform(10, 90, n)
    y = 50 + 2.0 * np.clip(55 - T, 0, None) + 1.5 * np.clip(T - 70, 0, None) + rng.normal(0, 1, n)
    return T, y


def test_change_point_fits_ignore_a_sentinel():
    T, y = _daily()
    clean = best_model(T, y)
    Ts = T.copy()
    Ts[[3, 50]] = SENTINEL  # two placeholders, their energies real
    m = best_model(Ts, y)
    assert m.n_temp_screened == 2 and m.n == clean.n - 2
    assert m.fit_range[0] >= -40 and min(m.change_points) > 40
    one = fit_model(Ts, y, "3PH")
    assert one.n_temp_screened == 2 and one.change_points[0] > 40
    # serialised only when non-zero, and rebuilt
    assert ChangePointModel.from_dict(m.as_dict()).n_temp_screened == 2
    assert "n_temp_screened" not in clean.as_dict()
    # opting out reproduces the old behaviour: the sentinel is the coldest point
    raw = fit_model(Ts, y, "3PH", temp_bounds=(-np.inf, np.inf))
    assert raw.n_temp_screened == 0 and raw.fit_range[0] == SENTINEL


def test_clean_fits_are_bit_for_bit_unchanged():
    T, y = _daily(seed=3)
    a = best_model(T, y)
    b = best_model(T, y, temp_bounds=(-np.inf, np.inf))
    assert a.as_dict() == b.as_dict() and a.n_temp_screened == 0


def test_degree_day_fit_ignores_a_sentinel():
    rng = np.random.default_rng(1)
    tavg = rng.uniform(20, 80, 36)
    e = 100 + 3 * np.clip(60 - tavg, 0, None) + rng.normal(0, 1, 36)
    clean = fit_degree_day(tavg, e, kind="heating")
    t2 = tavg.copy()
    t2[0] = SENTINEL
    m = fit_degree_day(t2, e, kind="heating")
    assert m.n_temp_screened == 1 and m.fit.n == clean.fit.n - 1
    assert m.caveats == []  # a screen is not a decline
    assert DegreeDayModel.from_dict(m.as_dict()).n_temp_screened == 1
    assert "n_temp_screened" not in clean.as_dict()


def _hourly(weeks=10, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-04", periods=24 * 7 * weeks, freq="1h")
    t = pd.Series(45 + 15 * np.sin(np.arange(len(idx)) / 70.0), index=idx)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    e = pd.Series(30 + 25 * occ + 0.8 * np.clip(55 - t.to_numpy(), 0, None), index=idx)
    return e + rng.normal(0, 0.5, len(idx)), t


def test_towt_breakpoints_ignore_a_sentinel():
    e, t = _hourly()
    ts = t.copy()
    ts.iloc[[10, 500, 900]] = SENTINEL
    m = fit_towt(e, ts)
    assert m.n_temp_screened == 3 and m.breakpoints[0] >= -40
    raw = fit_towt(e, ts, temp_bounds=(-np.inf, np.inf))
    assert raw.breakpoints[0] == SENTINEL  # the bug: the placeholder was the first breakpoint
    assert TOWTModel.from_dict(m.as_dict()).n_temp_screened == 3
    clean = fit_towt(e, t)
    assert "n_temp_screened" not in clean.as_dict()
    np.testing.assert_array_equal(clean.beta, fit_towt(e, t, temp_bounds=(-np.inf, np.inf)).beta)


def test_a_missing_temperature_predicts_nan_and_is_excluded_from_savings():
    from camber.mandv.stats import avoided_energy_savings

    e, t = _hourly(weeks=12)
    base, rep = slice(0, 24 * 7 * 8), slice(24 * 7 * 8, None)
    m = fit_towt(e.iloc[base], t.iloc[base])
    rt = t.iloc[rep].copy()
    rt.iloc[[5, 6, 7]] = np.nan
    pred = m.predict(rt.index, rt.to_numpy())
    assert np.isnan(pred[[5, 6, 7]]).all() and np.isfinite(np.delete(pred, [5, 6, 7])).all()
    full = m.predict(rt.index, t.iloc[rep].to_numpy())
    assert np.array_equal(np.delete(pred, [5, 6, 7]), np.delete(full, [5, 6, 7]))
    s = avoided_energy_savings(
        TOWTAtIndex(m, rt.index),
        rt.to_numpy(),
        e.iloc[rep].to_numpy() * 0.9,
        cv_rmse=0.05,
        n_baseline=24 * 7 * 8,
        p_baseline=m.n_params,
    )
    assert s.coverage["n_used"] == s.coverage["n_report"] - 3
    assert any(
        "3 of 672 reporting rows were excluded" in c and "3 of them had no finite baseline" in c
        for c in s.caveats
    )


def test_interval_frames_screen_and_count():
    from camber.mandv.intervalfit import (
        daily_energy_vs_temp,
        energy_vs_degree_days,
        hourly_energy_vs_temp,
    )

    e, t = _hourly(weeks=4)
    ts = t.copy()
    ts.iloc[[30, 31]] = SENTINEL
    d = daily_energy_vs_temp(e, ts, rate_is_energy_rate=False)
    clean = daily_energy_vs_temp(e, t, rate_is_energy_rate=False)
    day = ts.index[30].normalize()
    assert d.attrs["n_temp_screened"] == 2 and clean.attrs["n_temp_screened"] == 0
    assert d.loc[day, "oat"] == pytest.approx(
        t[t.index.normalize() == day].drop(ts.index[[30, 31]]).mean()
    )
    assert d["oat"].min() > 0
    h = hourly_energy_vs_temp(e, ts, rate_is_energy_rate=False)
    assert h.attrs["n_temp_screened"] == 2 and len(h) == len(e) - 2
    dd = energy_vs_degree_days(e, ts, rate_is_energy_rate=False)
    assert dd.attrs["n_temp_screened"] == 2 and dd["dd"].max() < 40


def test_caltrack_reports_screened_temperatures():
    from camber.mandv.caltrack import caltrack_savings_hourly

    e, t = _hourly(weeks=16, seed=5)
    ts = t.copy()
    ts.iloc[[100, 2000]] = SENTINEL
    half = 24 * 7 * 8
    res = caltrack_savings_hourly(
        e.iloc[:half], ts.iloc[:half], e.iloc[half:] * 0.9, ts.iloc[half:], min_hours=24 * 30
    )
    assert any("1 baseline temperature value(s) outside" in c for c in res.savings.caveats)
    assert any("1 reporting temperature value(s) outside" in c for c in res.savings.caveats)
