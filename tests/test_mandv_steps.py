"""Multi-step non-routine event detection (#21 phase 21a; nonroutine.detect_step_changes).

PELT on the weather-model residuals with a rho-inflated Gaussian cost, refitted with one level
indicator per segment. Synthetic daily sites with AR(1) residuals; the old single-step detector's
default output is frozen from before this change.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.changedetect import detect_level_shifts  # noqa: E402
from camber.mandv.intervalfit import daily_energy_vs_temp  # noqa: E402
from camber.mandv.models import best_model  # noqa: E402
from camber.mandv.nonroutine import (  # noqa: E402
    _pelt,
    detect_step_change,
    detect_step_changes,
)


def _site(seed, steps=(), rho=0.5, days=730, sd=8.0):
    """Daily cooling-meter energy with AR(1) residuals (marginal sd ``sd``) and planted steps."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=days, freq="D")
    d = np.arange(days)
    T = 60 + 20 * np.sin(2 * np.pi * (d - 100) / 365) + rng.normal(0, 4, days)
    e = np.empty(days)
    e[0] = 0.0
    inn = rng.normal(0, sd * np.sqrt(1 - rho**2), days)
    for i in range(1, days):
        e[i] = rho * e[i - 1] + inn[i]
    y = 400 + 6 * np.maximum(0, T - 65) + e
    for day, delta in steps:
        y[day:] += delta
    return pd.Series(y, index=idx), pd.Series(T, index=idx)


def test_two_planted_steps_are_found_within_a_week():
    y, T = _site(0, steps=[(200, -60), (480, 40)])
    r = detect_step_changes(y, T)
    assert r.detected and len(r.steps) == 2 and r.converged
    days = [(s.date - y.index[0]).days for s in r.steps]
    assert abs(days[0] - 200) <= 7 and abs(days[1] - 480) <= 7
    assert r.steps[0].delta == pytest.approx(-60, abs=4) and r.steps[1].delta == pytest.approx(
        40, abs=4
    )
    assert all(abs(s.z) > 10 for s in r.steps)
    # the steps are not absorbed into the slope: the refit's residuals are clean again
    assert r.rho == pytest.approx(0.5, abs=0.1) and r.sigma == pytest.approx(8, rel=0.15)
    assert r.segment.iloc[0] == 0 and r.segment.iloc[-1] == 2
    assert r.levels[0] == 0.0 and r.levels[2] == pytest.approx(-20, abs=5)
    json.dumps(r.as_dict(), allow_nan=False)


def test_moderate_steps_under_strong_autocorrelation():
    hits = 0
    for s in range(10):
        y, T = _site(2000 + s, steps=[(250, -24), (500, 24)], rho=0.5)
        days = [(st.date - y.index[0]).days for st in detect_step_changes(y, T).steps]
        hits += len(days) == 2 and abs(days[0] - 250) <= 7 and abs(days[1] - 500) <= 7
    assert hits >= 9


@pytest.mark.parametrize("rho", [0.0, 0.4, 0.8])
def test_no_steps_on_clean_ar1_data(rho):
    """False positives are rare on step-free data at any rho -- where binary segmentation with
    the independent-residual statistic (changedetect) fires on most runs at rho = 0.8."""
    runs = [_site(1000 + s, rho=rho) for s in range(12)]
    fp = sum(detect_step_changes(y, T).detected for y, T in runs)
    assert fp <= 1  # <= 8%; 0 of 50 per rho when this was written
    if rho == 0.8:
        naive = 0
        for y, T in runs[:8]:
            df = daily_energy_vs_temp(y, T)
            m = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
            resid = pd.Series(df["energy"].to_numpy() - m.predict(df["oat"].to_numpy()), df.index)
            naive += bool(detect_level_shifts(resid, min_segment=28))
        assert naive >= 4


def test_changedetect_agrees_on_clear_steps():
    """On large, clean steps the greedy binary segmentation finds the same dates (plus extras)."""
    y, T = _site(0, steps=[(200, -60), (480, 40)])
    df = daily_energy_vs_temp(y, T)
    m = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
    resid = pd.Series(df["energy"].to_numpy() - m.predict(df["oat"].to_numpy()), df.index)
    bs = {ls.at for ls in detect_level_shifts(resid, min_segment=28)}
    pelt = {s.date for s in detect_step_changes(y, T).steps}
    assert pelt <= bs and len(bs) > len(pelt)


def test_max_steps_raises_the_penalty():
    y, T = _site(3, steps=[(100, -40), (250, 40), (400, -40), (560, 40)])
    r = detect_step_changes(y, T, max_steps=2)
    assert len(r.steps) <= 2 and any("penalty raised" in c for c in r.caveats)
    assert len(detect_step_changes(y, T).steps) == 4


def test_inputs_and_iteration_limit():
    y, T = _site(4, days=40)
    with pytest.raises(ValueError, match="days"):
        detect_step_changes(y, T)
    y, T = _site(4, steps=[(300, -50)])
    with pytest.raises(ValueError, match="max_steps"):
        detect_step_changes(y, T, max_steps=0)
    r = detect_step_changes(y, T, max_iter=1, penalty=5.0)
    assert r.iterations == 2 and r.penalty == 5.0
    assert r.converged or any("still changing" in c for c in r.caveats)


def test_pelt_is_the_optimal_segmentation():
    x = np.r_[np.zeros(50), np.full(50, 5.0), np.zeros(50)] + np.random.default_rng(0).normal(
        0, 1, 150
    )
    assert _pelt(x, scale=1.0, penalty=15.0, min_seg=10) == [50, 100]
    assert _pelt(x, scale=1e6, penalty=15.0, min_seg=10) == []
    many = _pelt(x, scale=1.0, penalty=0.1, min_seg=30)
    assert all(b - a >= 30 for a, b in zip([0, *many], [*many, 150]))


# ------------------------------------------------------------------------------ the old detector

# frozen from detect_step_change before #21 (default arguments)
OLD = {
    "detected": True,
    "date": "2023-10-28",
    "delta": -19.7818,
    "rel_shift": 17.446,
    "pre_mean": 4.9454,
    "post_mean": -14.8363,
    "n_pre": 300,
    "n_post": 100,
    "n_days": 400,
    "model_kind": "4P",
}


def test_old_detector_default_output_is_unchanged():
    y, T = _site(7, steps=[(300, -30)], rho=0.4, days=400)
    assert detect_step_change(y, T).as_dict() == OLD
    assert detect_step_change(y, T, autocorrelation=False).as_dict() == OLD


def test_old_detector_kappa_flag():
    y, T = _site(7, steps=[(300, -30)], rho=0.4, days=400)
    r = detect_step_change(y, T, autocorrelation=True)
    assert r.detected and r.rho is not None
    k = (1 + r.rho) / (1 - r.rho)
    assert r.rel_shift == pytest.approx(OLD["rel_shift"] / np.sqrt(k), abs=2e-3)
    assert r.as_dict()["rho"] == r.rho
    # on clean, strongly autocorrelated data the uncorrected statistic fires; the corrected rarely
    runs = [_site(1000 + s, rho=0.8) for s in range(10)]
    assert sum(detect_step_change(y, T).detected for y, T in runs) >= 8
    assert sum(detect_step_change(y, T, autocorrelation=True).detected for y, T in runs) <= 1


def test_a_constant_or_dead_meter_does_not_hang():
    """A meter reading exactly zero all year (one real BDG2 chilled-water meter in 2017) gave a
    0/0 PELT cost that no penalty could prune, so the max_steps loop never ended."""
    idx = pd.date_range("2017-01-01", periods=365, freq="D")
    T = pd.Series(60 + 20 * np.sin(2 * np.pi * np.arange(365) / 365), idx)
    for level in (0.0, 5.0):
        r = detect_step_changes(pd.Series(level, index=idx), T)
        assert not r.detected and r.converged
        assert any("residual variance is zero" in c for c in r.caveats)


def test_pelt_capped_is_the_one_guard_for_both_step_searches():
    """detect_step_changes and the rebaseline T1 search share one capped PELT: a zero, non-finite
    or rounding-level noise scale stops the search instead of looping on the max_steps cap."""
    from camber.mandv.nonroutine import _pelt_capped

    rng = np.random.default_rng(0)
    x = np.r_[np.zeros(60), np.ones(60)] + rng.normal(0, 0.1, 120)
    cps, pen = _pelt_capped(x, scale=0.01, penalty=3 * np.log(120), min_seg=28, max_steps=5)
    assert cps == [60] and pen == 3 * np.log(120)
    cps, pen = _pelt_capped(x, scale=1e-8, penalty=1.0, min_seg=5, max_steps=1)
    assert len(cps) <= 1 and pen > 1.0  # the penalty was raised until at most one step remained
    for bad in (0.0, np.nan, np.inf, -1.0):
        assert _pelt_capped(x, scale=bad, penalty=1.0, min_seg=5, max_steps=1) is None
    assert (
        _pelt_capped(x * 1e-12, scale=1e-30, penalty=1.0, min_seg=5, max_steps=1, floor=1e-24)
        is None
    )
