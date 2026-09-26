"""Tests for IPMVP Option-B retrofit isolation (camber.mandv.retrofit_isolation)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.coverage import ExtrapolationPolicy  # noqa: E402
from camber.mandv.retrofit_isolation import (  # noqa: E402
    IsolationSavings,
    fit_driver_model,
    isolation_normalized_savings,
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
    assert np.allclose(m.predict(len(y)), y.mean())  # predict by length


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
    yr = 10 + 0.6 * tons_r  # ~25% less per ton
    r = isolation_savings(
        yb, yr, baseline_driver=tons_b, reporting_driver=tons_r, boundary="CH-1 sub-meter"
    )
    assert isinstance(r, IsolationSavings) and r.option == "B"
    assert r.boundary == "CH-1 sub-meter"
    assert r.savings > 0 and r.savings_pct > 0.1  # real savings, adjusted for load
    # adjusted baseline = baseline model applied to REPORTING tons (load-corrected)
    assert abs(r.adjusted_baseline - float((10 + 0.8 * tons_r).sum())) < 1.0
    assert r.accept  # clean synthetic -> model accepted


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
    tons_r = np.linspace(150, 400, 40)  # heavier reporting load
    yb = 0.8 * tons_b
    yr = 0.6 * tons_r  # more efficient, but more load
    # the reporting load runs 100 tons (40% of the fitted range) past the baseline's: the coverage
    # guard (issue #20) declines that by default, so opt in to compute it
    assert isolation_savings(yb, yr, baseline_driver=tons_b, reporting_driver=tons_r).declined
    r = isolation_savings(
        yb,
        yr,
        baseline_driver=tons_b,
        reporting_driver=tons_r,
        extrapolation=ExtrapolationPolicy(decline=False),
    )
    naive = float(yb.sum() - yr.sum())
    assert r.savings > naive  # adjustment credits the extra load
    assert r.savings > 0


def test_isolation_normalized_savings_removes_driver_shift():
    # same efficiency both periods but reporting ran at lower load -> normalized savings ~0
    normal = np.linspace(50, 400, 12)
    tons_b = np.linspace(50, 400, 40)
    tons_r = np.linspace(40, 300, 40)  # lighter reporting load
    yb = 5 + 0.7 * tons_b
    yr = 5 + 0.7 * tons_r  # identical model
    # the normal load profile runs 100 tons past the reporting fit: declined by default (#20)
    flagged = isolation_normalized_savings(
        yb, yr, normal, baseline_driver=tons_b, reporting_driver=tons_r
    )
    assert flagged.declined and flagged.coverage_reporting["tier"] == "severe"
    ns = isolation_normalized_savings(
        yb,
        yr,
        normal,
        baseline_driver=tons_b,
        reporting_driver=tons_r,
        extrapolation=ExtrapolationPolicy(decline=False),
    )
    assert abs(ns.savings_pct) < 0.02  # no real change once normalized


def test_as_dict_includes_model():
    r = isolation_savings(np.full(6, 100.0), np.full(6, 90.0), boundary="b")
    d = r.as_dict()
    assert d["option"] == "B" and d["boundary"] == "b"
    assert d["model"]["p"] == 1


# --------------------------------------------------------------------------- #21 (21a) fixes


def test_isolation_normalized_passes_each_models_p():
    """Two drivers are p = 3; the band used to be computed as if p = 2."""
    from camber.mandv.normalized import normalized_savings
    from camber.mandv.stats import fit_stats

    rng = np.random.default_rng(7)
    Xb, Xr = rng.uniform(50, 400, (60, 2)), rng.uniform(60, 390, (60, 2))
    yb = 10 + Xb @ [0.8, 0.2] + rng.normal(0, 5, 60)
    yr = 10 + Xr @ [0.6, 0.2] + rng.normal(0, 5, 60)
    normal = np.column_stack([np.linspace(60, 390, 12), np.linspace(390, 60, 12)])
    got = isolation_normalized_savings(yb, yr, normal, baseline_driver=Xb, reporting_driver=Xr)
    mb, mr = fit_driver_model(Xb, yb), fit_driver_model(Xr, yr)
    assert mb.p == mr.p == 3
    cv = [fit_stats(y, m.predict(X), 3).cv_rmse for y, m, X in ((yb, mb, Xb), (yr, mr, Xr))]
    kw = dict(baseline_cv_rmse=cv[0], n_baseline=60, reporting_cv_rmse=cv[1], n_reporting=60)
    right = normalized_savings(mb, mr, normal, p_baseline=3, p_reporting=3, **kw)
    wrong = normalized_savings(mb, mr, normal, **kw)  # the old p = 2 default
    assert got.abs_uncertainty == right.abs_uncertainty > wrong.abs_uncertainty


def test_isolation_time_index_keeps_rho():
    import pandas as pd

    rng = np.random.default_rng(3)
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    tons = 200 + 100 * np.sin(np.arange(n) / 20.0)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.7 * e[i - 1] + rng.normal(0, 5)
    yb = 10 + 0.8 * tons + e
    yr = 10 + 0.6 * tons + rng.normal(0, 5, n)
    plain = isolation_savings(yb, yr, baseline_driver=tons, reporting_driver=tons)
    rho = isolation_savings(yb, yr, baseline_driver=tons, reporting_driver=tons,
                            baseline_index=idx)  # fmt: skip
    assert rho.savings == plain.savings
    assert rho.abs_uncertainty > 1.5 * plain.abs_uncertainty  # rho ~0.7 widens the band
    assert rho.model._fit_record.rho is not None
    nrm = isolation_normalized_savings(
        yb, yr, np.linspace(120, 280, 12), baseline_driver=tons, reporting_driver=tons,
        baseline_index=idx, reporting_index=idx,
    )  # fmt: skip
    nrm0 = isolation_normalized_savings(
        yb, yr, np.linspace(120, 280, 12), baseline_driver=tons, reporting_driver=tons
    )
    assert nrm.abs_uncertainty > nrm0.abs_uncertainty
