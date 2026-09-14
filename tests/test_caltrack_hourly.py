"""Hourly NMEC on a TOWT baseline (camber.mandv.caltrack.caltrack_savings_hourly).

The load-bearing tests here are the two that guard *silent* wrongness: a TOWT model projected onto
hours it never saw, and the autocorrelation correction that stops an hourly band looking falsely
tight. Neither was reachable before 0.81.0 — no code path put a TOWT model through the savings
machinery at all.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.caltrack import caltrack_savings_hourly  # noqa: E402
from camber.mandv.stats import avoided_energy_savings, cv_rmse_max_for, fit_stats  # noqa: E402
from camber.mandv.towt import TOWTAtIndex, fit_towt  # noqa: E402


def _ar1(n, rho, sigma, rng):
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + rng.normal(0, sigma)
    return e


def _site(start, weeks, *, scale=1.0, rho=0.0, seed=0):
    """An office: weekday-occupied, cooling above 65F, AR(1) residuals at `rho`."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=weeks * 168, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    t = 60 + 15 * np.sin(np.arange(len(idx)) / 168 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = (40 + 30 * occ + np.clip(t - 65, 0, None) * 2) * scale + _ar1(len(idx), rho, 2.0, rng)
    return pd.Series(e, index=idx), pd.Series(t, index=idx)


def test_recovers_a_known_saving():
    eb, tb = _site("2024-01-01", 20)
    er, tr = _site("2024-06-01", 8, scale=0.85, seed=1)
    res = caltrack_savings_hourly(eb, tb, er, tr)

    assert res.savings.savings_pct == pytest.approx(0.15, abs=0.05)
    assert res.n_tow_bins == 168  # full weekly coverage
    assert res.baseline_n == 20 * 168
    assert res.baseline_accepted is True
    assert res.cv_rmse_max == cv_rmse_max_for("hourly")  # not the 0.20 default


def test_projecting_onto_unseen_hours_fails_loudly():
    """The silent-wrong-answer guard.

    A weekday-only baseline projected onto a weekend predicts a near-zero (even negative) baseline,
    because the one-hot row for an unseen bin is all zeros — which reads downstream as a huge
    negative saving. It must raise, not guess, and not return NaN either: NaN would make those
    hours vanish from the savings sum without saying so.
    """
    eb, tb = _site("2024-01-01", 20)
    weekday = eb.index.dayofweek < 5
    model = fit_towt(eb[weekday], tb[weekday])
    er, tr = _site("2024-06-01", 8, seed=1)

    assert model.covers(er.index[er.index.dayofweek < 5])
    assert not model.covers(er.index)
    with pytest.raises(ValueError, match="never seen at fit"):
        model.predict(er.index, tr.to_numpy())


def test_the_hourly_entry_point_refuses_a_baseline_that_cannot_cover():
    eb, tb = _site("2024-01-01", 20)
    weekday = eb.index.dayofweek < 5
    er, tr = _site("2024-06-01", 8, seed=1)
    with pytest.raises(ValueError, match="168 hour-of-week bins"):
        caltrack_savings_hourly(eb[weekday], tb[weekday], er, tr)


def test_sufficiency_is_coverage_not_row_count():
    """fit_towt's own >=50-observation guard cannot be the constraint: 50 hours, 168 bins."""
    eb, tb = _site("2024-01-01", 20)
    er, tr = _site("2024-06-01", 8, seed=1)
    with pytest.raises(ValueError, match="baseline hours"):
        caltrack_savings_hourly(eb, tb, er, tr, min_hours=24 * 400)
    # three weeks covers all 168 bins but only 3 observations each
    short_e, short_t = _site("2024-01-01", 3)
    with pytest.raises(ValueError, match="fewer than"):
        caltrack_savings_hourly(short_e, short_t, er, tr, min_hours=24, min_obs_per_bin=4)


def test_autocorrelation_widens_the_hourly_band_substantially():
    """Hourly residuals are strongly correlated, so the band is comparable to daily, not tighter.

    Without this correction an hourly path looks falsely precise simply because it has 24x the rows.
    """
    er, tr = _site("2024-06-01", 8, scale=0.85, seed=1)
    bands = {}
    for rho in (0.0, 0.85):
        eb, tb = _site("2024-01-01", 20, rho=rho, seed=3)
        res = caltrack_savings_hourly(eb, tb, er, tr)
        bands[rho] = res.savings.fractional_uncertainty
        if rho:
            assert res.baseline_rho == pytest.approx(0.85, abs=0.1)
            assert res.savings.fsu_autocorrelation_adjusted is True
            assert res.savings.n_effective < res.baseline_n / 5  # n_eff collapses
    assert bands[0.85] > 3 * bands[0.0], bands


def test_towt_flows_through_the_generic_savings_path_via_the_adapter():
    """What normalized.py's docstring claimed for years without it being true."""
    eb, tb = _site("2024-01-01", 20)
    er, tr = _site("2024-06-01", 8, scale=0.85, seed=1)
    model = fit_towt(eb, tb)
    st = fit_stats(
        eb.to_numpy(),
        model.predict(eb.index, tb.to_numpy()),
        model.n_params,
        cv_rmse_max=cv_rmse_max_for("hourly"),
        time_index=eb.index,
    )
    res = avoided_energy_savings(
        TOWTAtIndex(model, er.index),
        tr.to_numpy(),
        er.to_numpy(),
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=model.n_params,
        rho=st.rho_lag1,
    )
    assert res.savings_pct == pytest.approx(0.15, abs=0.05)
    assert np.isfinite(res.fractional_uncertainty)


def test_adapter_rejects_a_length_mismatch():
    eb, tb = _site("2024-01-01", 20)
    model = fit_towt(eb, tb)
    with pytest.raises(ValueError, match="align positionally"):
        TOWTAtIndex(model, eb.index).predict(tb.to_numpy()[:-5])


def test_non_routine_screening_excludes_whole_days():
    """Hour-level trimming would bias CV(RMSE) down and narrow the band -- the wrong direction."""
    eb, tb = _site("2024-01-01", 20)
    shutdown = (eb.index >= "2024-02-05") & (eb.index < "2024-02-07")
    eb = eb.copy()
    eb[shutdown] = eb[shutdown] * 0.1  # a two-day shutdown
    er, tr = _site("2024-06-01", 8, scale=0.85, seed=1)

    res = caltrack_savings_hourly(eb, tb, er, tr, exclude_non_routine=True)
    assert res.n_non_routine_days_excluded >= 1
    assert res.baseline_n % 1 == 0 and res.baseline_n < 20 * 168  # whole days removed
