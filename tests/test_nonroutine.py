"""Tests for non-routine event detection + exclusion (mandv.nonroutine / caltrack)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.caltrack import caltrack_savings  # noqa: E402
from camber.mandv.nonroutine import (  # noqa: E402
    detect_non_routine, detect_step_change)


def _hourly(days=120, seed=0, shutdown=()):
    idx = pd.date_range("2023-01-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    temp = 60 + 18 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) \
        + 8 * np.sin(np.arange(len(idx)) / (24 * 30))
    energy = 20.0 + np.clip(temp - 65, 0, None) * 1.5 + rng.normal(0, 0.5, len(idx))
    e = pd.Series(energy, index=idx)
    for d in shutdown:
        e.loc[d] = 0.5                      # near-zero whole-day shutdown
    return e, pd.Series(temp, index=idx)


def test_detect_flags_shutdown_days():
    sd = ["2023-02-10", "2023-02-11", "2023-03-05"]
    e, t = _hourly(shutdown=sd)
    res = detect_non_routine(e, t)
    assert res.n_flagged >= 3
    flagged = set(res.mask[res.mask].index.strftime("%Y-%m-%d"))
    assert set(sd) <= flagged              # the shutdown days are caught


def test_clean_period_flags_few():
    e, t = _hourly()
    res = detect_non_routine(e, t)
    assert res.fraction < 0.10             # a clean period is mostly routine


def test_caltrack_excludes_non_routine_and_fits_cleaner():
    sd = ["2023-02-10", "2023-02-11", "2023-02-12", "2023-03-05", "2023-03-06"]
    be, bt = _hourly(days=150, seed=1, shutdown=sd)
    re_, rt = _hourly(days=120, seed=2)               # clean reporting, no retrofit
    without = caltrack_savings(be, bt, re_, rt, exclude_non_routine=False)
    cleaned = caltrack_savings(be, bt, re_, rt, exclude_non_routine=True)
    assert cleaned.n_non_routine_excluded >= 5        # the shutdown days dropped
    assert without.n_non_routine_excluded == 0
    # dropping the shutdowns gives a tighter baseline fit
    assert cleaned.baseline_r2 >= without.baseline_r2
    assert cleaned.baseline_cv_rmse <= without.baseline_cv_rmse


def test_detect_requires_min_days():
    e, t = _hourly(days=5)
    with pytest.raises(ValueError):
        detect_non_routine(e, t)


# ---- sustained step-change (level-shift) detection ----

def _hourly_with_step(days=180, step_day=120, step_kw=10.0, seed=0,
                      weather_light=True):
    """Hourly energy with a sustained level shift added after ``step_day``.

    ``weather_light`` keeps the load weakly weather-coupled (a mild, oscillating
    OAT) so the step does not hide inside the temperature relationship -- the case
    where the break date is sharply resolvable. Set False for a strongly
    cooling-driven load.
    """
    idx = pd.date_range("2023-01-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    if weather_light:
        temp = 60 + 10 * np.sin(np.arange(len(idx)) / (24 * 15))
        energy = 20.0 + 0.2 * np.clip(temp - 65, 0, None) + rng.normal(0, 1.0, len(idx))
    else:
        temp = 60 + 18 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) \
            + 8 * np.sin(np.arange(len(idx)) / (24 * 30))
        energy = 20.0 + np.clip(temp - 65, 0, None) * 1.5 + rng.normal(0, 0.5, len(idx))
    e = pd.Series(energy, index=idx)
    cut = idx[0] + pd.Timedelta(days=step_day)
    e.loc[idx >= cut] += step_kw            # sustained level shift
    return e, pd.Series(temp, index=idx), cut


def test_step_change_detected_and_located():
    e, t, cut = _hourly_with_step(days=180, step_day=120, step_kw=10.0)
    res = detect_step_change(e, t)
    assert res.detected
    # weather-light load -> the break date is resolved sharply
    assert abs((res.date - cut.normalize()).days) <= 3
    assert res.delta > 0                    # upward escalation
    assert res.post_mean > res.pre_mean
    # the post-step segment is flagged as the non-routine portion
    assert res.mask[res.mask.index >= res.date].all()
    assert not res.mask[res.mask.index < res.date].any()


def test_step_change_missed_by_pointwise_screen():
    # A modest sustained escalation: each day only mildly off (point-wise screen
    # flags almost nothing) but the segment mean shifts -> the step detector catches
    # it. This is the September-escalation case the daily/point-wise screen misses.
    e, t, cut = _hourly_with_step(days=180, step_day=120, step_kw=0.6, seed=3,
                                  weather_light=False)
    pointwise = detect_non_routine(e, t)
    step = detect_step_change(e, t)
    assert pointwise.fraction < 0.10        # point-wise barely notices
    assert step.detected                    # sustained shift is caught


def test_step_change_clean_period_not_flagged():
    e, t = _hourly(days=180)                 # no step
    res = detect_step_change(e, t)
    assert not res.detected
    assert res.date is None


def test_step_change_ignores_single_spike():
    # one isolated shutdown day is NOT a sustained step
    e, t = _hourly(days=180, shutdown=["2023-04-15"])
    res = detect_step_change(e, t, min_segment_days=14)
    assert not res.detected


def test_step_change_requires_min_days():
    e, t = _hourly(days=20)
    with pytest.raises(ValueError):
        detect_step_change(e, t, min_segment_days=14)
