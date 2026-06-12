"""Tests for IPMVP Option-B retrofit isolation (camber.mandv.retrofit_isolation)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.retrofit_isolation import (  # noqa: E402
    DriverModel, IsolationSavings, fit_driver_model, isolation_normalized_savings,
    isolation_savings,
)


def test_fit_driver_model_affine():
    # chiller kWh = 5 + 0.7 * tons (a load-driven sub-meter)
    tons = np.linspace(50, 400, 40)
    kwh = 5 + 0.7 * tons
    m = fit_driver_model(tons, kwh)
    assert m.p == 2 and len(m.coef) == 1
    assert abs(m.intercept - 5) < 1e-6 and abs(m.coef[0] - 0.7) < 1e-6
    assert np.allclose(m.predict(tons), kwh)


def test_fit_driver_model_constant():
    y = np.array([100.0, 102.0, 98.0, 101.0])
    m = fit_driver_model(None, y)
    assert m.coef == () and m.p == 1
    assert abs(m.intercept - y.mean()) < 1e-9
    assert np.allclose(m.predict(len(y)), y.mean())          # predict by length


def test_fit_driver_model_multivariate():
    rng = np.random.default_rng(0)
    x1 = rng.uniform(0, 100, 60)
    x2 = rng.uniform(0, 20, 60)
    y = 3 + 0.5 * x1 + 2.0 * x2
    m = fit_driver_model(np.column_stack([x1, x2]), y)
    assert len(m.coef) == 2
    assert abs(m.coef[0] - 0.5) < 1e-6 and abs(m.coef[1] - 2.0) < 1e-6


def test_isolation_savings_load_driven():
    # baseline: kWh = 10 + 0.8*tons; reporting: same tons but a more efficient plant (0.6 slope)
    tons_b = np.linspace(50, 400, 50)
    tons_r = np.linspace(60, 380, 50)
    yb = 10 + 0.8 * tons_b
    yr = 10 + 0.6 * tons_r                                    # ~25% less per ton
    r = isolation_savings(yb, yr, baseline_driver=tons_b, reporting_driver=tons_r,
                          boundary="CH-1 sub-meter")
    assert isinstance(r, IsolationSavings) and r.option == "B"
    assert r.boundary == "CH-1 sub-meter"
    assert r.savings > 0 and r.savings_pct > 0.1             # real savings, adjusted for load
    # adjusted baseline = baseline model applied to REPORTING tons (load-corrected)
    assert abs(r.adjusted_baseline - float((10 + 0.8 * tons_r).sum())) < 1.0
    assert r.accept                                          # clean synthetic -> model accepted


def test_isolation_savings_constant_load():
    # a lighting retrofit: ~constant power, same operating hours -> raw before/after difference
    yb = np.full(12, 1000.0)
    yr = np.full(12, 700.0)
    r = isolation_savings(yb, yr, boundary="lighting panel L2")
    assert abs(r.adjusted_baseline - 12000.0) < 1e-6
    assert abs(r.reporting_actual - 8400.0) < 1e-6
    assert abs(r.savings - 3600.0) < 1e-6
    assert abs(r.savings_pct - 0.30) < 1e-6


def test_load_adjustment_beats_naive_difference():
    # reporting period ran at HIGHER load; a naive sub-meter difference would understate
    # savings, but the load-adjusted Option-B baseline corrects for it.
    tons_b = np.linspace(50, 300, 40)
    tons_r = np.linspace(150, 400, 40)                       # heavier reporting load
    yb = 0.8 * tons_b
    yr = 0.6 * tons_r                                        # more efficient, but more load
    r = isolation_savings(yb, yr, baseline_driver=tons_b, reporting_driver=tons_r)
    naive = float(yb.sum() - yr.sum())
    assert r.savings > naive                                 # adjustment credits the extra load
    assert r.savings > 0


def test_isolation_normalized_savings_removes_driver_shift():
    # same efficiency both periods but reporting ran at lower load -> normalized savings ~0
    normal = np.linspace(50, 400, 12)
    tons_b = np.linspace(50, 400, 40)
    tons_r = np.linspace(40, 300, 40)                        # lighter reporting load
    yb = 5 + 0.7 * tons_b
    yr = 5 + 0.7 * tons_r                                    # identical model
    ns = isolation_normalized_savings(yb, yr, normal, baseline_driver=tons_b,
                                      reporting_driver=tons_r)
    assert abs(ns.savings_pct) < 0.02                        # no real change once normalized


def test_as_dict_includes_model():
    r = isolation_savings(np.full(6, 100.0), np.full(6, 90.0), boundary="b")
    d = r.as_dict()
    assert d["option"] == "B" and d["boundary"] == "b"
    assert d["model"]["p"] == 1
