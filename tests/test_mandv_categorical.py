"""Tests for multi-variable categorical (per-category) change-point models."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.categorical import fit_categorical  # noqa: E402


def _two_regime():
    rng = np.random.default_rng(0)
    T = np.linspace(40, 100, 600)
    cat = np.where(np.arange(600) % 7 < 5, "weekday", "weekend")
    # weekday: cooling change point 70, slope 1.0; weekend: change point 80, slope 0.4
    y = np.empty(600)
    wd = cat == "weekday"
    y[wd] = 30 + 1.0 * np.maximum(0.0, T[wd] - 70)
    y[~wd] = 10 + 0.4 * np.maximum(0.0, T[~wd] - 80)
    y += rng.normal(0, 0.3, 600)
    return T, y, cat


def test_fits_per_category():
    T, y, cat = _two_regime()
    m = fit_categorical(T, y, cat)
    assert set(m.categories) == {"weekday", "weekend"}
    # each sub-model recovered its own change point
    wd_tc = m.models["weekday"].change_points[0]
    we_tc = m.models["weekend"].change_points[0]
    assert abs(wd_tc - 70) < 5
    assert abs(we_tc - 80) < 6


def test_pooled_fit_beats_single_model():
    from camber.mandv.models import best_model
    from camber.mandv.stats import fit_stats
    T, y, cat = _two_regime()
    cm = fit_categorical(T, y, cat)
    # single pooled model ignoring category
    single = best_model(T, y)
    from camber.mandv.models import N_PARAMS
    s_single = fit_stats(y, single.predict(T), p=N_PARAMS[single.kind])
    # the categorical model explains more variance than one lumped model
    assert cm.pooled_stats.r2 > s_single.r2


def test_predict_routes_by_category():
    T, y, cat = _two_regime()
    m = fit_categorical(T, y, cat)
    # a hot weekday should predict higher cooling energy than a hot weekend
    wd = m.predict(np.array([95.0]), np.array(["weekday"]))[0]
    we = m.predict(np.array([95.0]), np.array(["weekend"]))[0]
    assert wd > we


def test_skips_sparse_category():
    T, y, cat = _two_regime()
    cat = cat.copy()
    cat[:3] = "holiday"            # only 3 holiday points -> below min_per_cat
    m = fit_categorical(T, y, cat, min_per_cat=6)
    assert "holiday" not in m.categories


def test_fixed_kind_per_category():
    T, y, cat = _two_regime()
    m = fit_categorical(T, y, cat, kind="3PC")
    assert all(sub.kind == "3PC" for sub in m.models.values())
