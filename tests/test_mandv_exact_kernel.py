"""The exact OLS projection-variance kernel (#21 phase 21a; camber.mandv._design).

``V_param = kappa s2 g'Ag`` and ``V_noise = kappa s2 m``. Checked against exact OLS identities
in-sample, against the projected kernel it refines, and by Monte Carlo coverage on synthetic
change-point data with AR(1) residuals at rho in {0, 0.4, 0.8}.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv._design import design_rows, projection_variance  # noqa: E402
from camber.mandv.coverage import ExtrapolationPolicy  # noqa: E402
from camber.mandv.degreeday import fit_degree_day  # noqa: E402
from camber.mandv.models import N_PARAMS, ChangePointModel, fit_model  # noqa: E402
from camber.mandv.normalized import normalized_savings  # noqa: E402
from camber.mandv.retrofit_isolation import (  # noqa: E402
    fit_driver_model,
    isolation_normalized_savings,
    isolation_savings,
)
from camber.mandv.stats import _rel_unc_projected, avoided_energy_savings, fit_stats  # noqa: E402
from camber.mandv.towt import TOWTAtIndex, fit_towt  # noqa: E402


def _ar1(rng, n, rho, sd):
    e = np.empty(n)
    innov = rng.normal(0, sd * np.sqrt(1 - rho**2), n)
    e[0] = rng.normal(0, sd)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + innov[i]
    return e


def _temps(rng, n):
    return 60 + 20 * np.sin(2 * np.pi * (np.arange(n) - 100) / 365) + _ar1(rng, n, 0.7, 5)


def _truth(T):
    return 50 + 2 * np.maximum(0, T - 65)


IDX = pd.date_range("2023-01-01", periods=365, freq="D")


def _fitted(kind="3PC", seed=0):
    rng = np.random.default_rng(seed)
    T = _temps(rng, 365)
    y = _truth(T) + rng.normal(0, 4, 365)
    return fit_model(T, y, kind), T, y


@pytest.mark.parametrize("kind", ["2P", "3PC", "3PH", "4P", "5P"])
def test_in_sample_projection_is_the_hat_matrix_sum(kind):
    """Projected onto its own rows, g'Ag = 1'H1 = n for any model with an intercept: the exact
    parameter variance of the in-sample total is s2 * n, i.e. a relative error of CV/sqrt(n)."""
    m, T, y = _fitted(kind)
    pv = projection_variance(m, T)
    rec = m._fit_record
    assert pv.v_param == pytest.approx(rec.s2 * 365, rel=1e-8)
    assert pv.v_noise == pytest.approx(rec.s2 * 365) and pv.m == 365 and pv.kappa == 1.0
    assert pv.total == pytest.approx(float(m.predict(T).sum()), rel=1e-10)
    assert pv.df == 365 - N_PARAMS[kind] and pv.rho is None
    cv = np.sqrt(rec.s2) / y.mean()
    rel = np.sqrt(pv.v_param) / pv.total
    assert rel == pytest.approx(cv / np.sqrt(365), rel=0.02)
    # ... which the CV*sqrt(p/n) projected kernel bounds from above (it is conservative by sqrt(p))
    assert rel <= _rel_unc_projected(cv, n_fit=365, p_fit=N_PARAMS[kind])
    # the mean design row has leverage 1/n
    pv1 = projection_variance(m, T, rows=np.arange(365) < 1)
    assert pv1.m == 1


def test_in_sample_identity_for_driver_degree_day_and_towt():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 10, (100, 2))
    dm = fit_driver_model(x, 1 + x @ [2.0, -1.0] + rng.normal(0, 1, 100))
    assert projection_variance(dm, x).v_param == pytest.approx(dm._fit_record.s2 * 100, rel=1e-8)
    tm = rng.uniform(25, 85, 36)
    dd = fit_degree_day(tm, 300 + 12 * np.maximum(0, 60 - tm) + rng.normal(0, 10, 36))
    assert projection_variance(dd, tm).v_param == pytest.approx(dd._fit_record.s2 * 36, rel=1e-6)
    idx = pd.date_range("2024-01-01", periods=4 * 168, freq="1h")
    t = 60 + 10 * np.sin(np.arange(len(idx)) / 168 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 40 + 20 * (idx.hour.to_numpy() > 8) + np.clip(t - 65, 0, None) + rng.normal(0, 1, len(idx))
    tw = fit_towt(pd.Series(e, index=idx), pd.Series(t, index=idx))
    pv = projection_variance(TOWTAtIndex(tw, idx), t)
    assert pv.v_param == pytest.approx(pv.kappa * tw._fit_record.s2 * len(idx), rel=1e-6)
    assert pv.rho is not None and pv.kappa > 1  # fit_towt records rho from its own index
    assert projection_variance(tw, t, index=idx).v_param == pytest.approx(pv.v_param)
    with pytest.raises(ValueError, match="timestamps"):
        design_rows(tw, t[:5], index=idx)


def test_leverage_grows_away_from_the_fit_and_rho_inflates():
    m, T, _ = _fitted("2P")
    near = projection_variance(m, np.full(30, T.mean()))
    far = projection_variance(m, np.full(30, T.max() + 40))
    assert far.v_param > 10 * near.v_param
    assert near.v_param == pytest.approx(m._fit_record.s2 * 30**2 / 365, rel=1e-6)
    adj = projection_variance(m, T, rho=0.5)
    assert adj.kappa == pytest.approx(3.0) and adj.v_noise == pytest.approx(
        3 * m._fit_record.s2 * 365
    )
    assert projection_variance(m, T, rho=1.0).kappa == float("inf")


def test_exact_savings_are_never_widened_again():
    m, T, y = _fitted("3PC")
    rng = np.random.default_rng(8)
    Tr = rng.uniform(40, 100, 200)  # some reporting days hotter than the baseline ever was
    yr = 0.9 * _truth(Tr)
    st = fit_stats(y, m.predict(T), 3)
    loose = ExtrapolationPolicy(decline=False)
    g = avoided_energy_savings(
        m, Tr, yr, cv_rmse=st.cv_rmse, n_baseline=365, p_baseline=3, extrapolation=loose
    )
    x = avoided_energy_savings(
        m, Tr, yr, cv_rmse=st.cv_rmse, n_baseline=365, p_baseline=3, extrapolation=loose,
        kernel="exact",
    )  # fmt: skip
    assert g.kernel == "g14" and x.kernel == "exact"
    assert x.fsu_extrapolation_factor == 1.0 and g.fsu_extrapolation_factor > 1.0
    assert x.avoided_energy == g.avoided_energy and x.coverage == g.coverage
    assert x.abs_uncertainty > 0 and x.rho is None and not x.fsu_autocorrelation_adjusted
    x2 = avoided_energy_savings(
        m, Tr, yr, cv_rmse=st.cv_rmse, n_baseline=365, p_baseline=3, rho=0.5, kernel="exact",
        extrapolation=loose,
    )  # fmt: skip
    assert x2.abs_uncertainty == pytest.approx(x.abs_uncertainty * np.sqrt(3), rel=1e-3)
    assert x2.rho == 0.5 and x2.fsu_autocorrelation_adjusted
    # the exact kernel falls back to the rho the fit recorded from a time index
    mi = fit_model(T, y, "3PC", time_index=IDX)
    x3 = avoided_energy_savings(mi, Tr, yr, cv_rmse=0.1, n_baseline=365, p_baseline=3,
                                kernel="exact", extrapolation=loose)  # fmt: skip
    assert x3.rho == pytest.approx(mi._fit_record.rho, abs=1e-4)


def test_exact_kernel_needs_a_camber_fit_and_a_known_kernel():
    bare = ChangePointModel("2P", {"base": 1.0, "slope": 1.0}, (), 0.0, 3, _predict=lambda t: t)
    with pytest.raises(TypeError, match="exact"):
        avoided_energy_savings(bare, [1.0, 2.0], [1.0, 1.0], cv_rmse=0.1, n_baseline=3,
                               p_baseline=2, kernel="exact")  # fmt: skip
    m, _, _ = _fitted()
    with pytest.raises(ValueError, match="kernel"):
        avoided_energy_savings(m, [70.0], [60.0], cv_rmse=0.1, n_baseline=3, p_baseline=2,
                               kernel="nope")  # fmt: skip


def test_exact_normalized_and_isolation():
    rng = np.random.default_rng(3)
    Tb, Tr = rng.uniform(50, 100, 200), rng.uniform(50, 100, 200)
    mb = fit_model(Tb, 50 + 2 * np.maximum(0, Tb - 65) + rng.normal(0, 2, 200), "3PC")
    mr = fit_model(Tr, 45 + 1.6 * np.maximum(0, Tr - 65) + rng.normal(0, 2, 200), "3PC")
    temps = np.array([55, 58, 63, 70, 78, 88, 95, 93, 86, 75, 63, 56], dtype=float)
    kw = dict(baseline_cv_rmse=0.05, n_baseline=200, p_baseline=3, p_reporting=3)
    g = normalized_savings(mb, mr, temps, **kw)
    x = normalized_savings(mb, mr, temps, kernel="exact", **kw)
    pb, pr = projection_variance(mb, temps), projection_variance(mr, temps)
    assert x.abs_uncertainty == pytest.approx(1.653 * np.sqrt(pb.v_param + pr.v_param), rel=0.01)
    assert x.kernel == "exact" and x.fsu_extrapolation_factor == 1.0
    assert x.normalized_savings == g.normalized_savings
    # exact is tighter than the conservative sqrt(p/n) form, never wider
    assert x.abs_uncertainty < g.abs_uncertainty
    tons_b, tons_r = rng.uniform(50, 400, 60), rng.uniform(60, 390, 60)
    yb = 10 + 0.8 * tons_b + rng.normal(0, 5, 60)
    yr = 10 + 0.6 * tons_r + rng.normal(0, 5, 60)
    iso = isolation_savings(yb, yr, baseline_driver=tons_b, reporting_driver=tons_r, kernel="exact")
    assert iso.kernel == "exact" and iso.fsu_extrapolation_factor == 1.0 and iso.abs_uncertainty > 0
    nrm = isolation_normalized_savings(yb, yr, np.linspace(60, 390, 12), baseline_driver=tons_b,
                                       reporting_driver=tons_r, kernel="exact")  # fmt: skip
    assert nrm.kernel == "exact" and nrm.abs_uncertainty > 0


def _coverage_rate(rho, reps, seed):
    """Share of seeded runs whose 90% exact band covers the true saving (model correct)."""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        Tb = _temps(rng, 365)
        yb = _truth(Tb) + _ar1(rng, 365, rho, 4)
        Tr = _temps(rng, 365)
        yr = _truth(Tr) - 10 + _ar1(rng, 365, rho, 4)  # a true saving of 10 per day
        m = fit_model(Tb, yb, "3PC", time_index=IDX)  # rho estimated from the residuals
        s = avoided_energy_savings(
            m, Tr, yr, cv_rmse=0.1, n_baseline=365, p_baseline=3, kernel="exact"
        )
        hits += abs(s.avoided_energy - 3650.0) <= s.abs_uncertainty
    return hits / reps


@pytest.mark.parametrize("rho,seed", [(0.0, 1), (0.4, 5), (0.8, 9)])
def test_monte_carlo_coverage_of_the_exact_band(rho, seed):
    """Nominal 90%: the exact band covers the true saving 85-95% of the time under AR(1)
    residuals, with rho estimated (not given) -- about 2 s per rho."""
    rate = _coverage_rate(rho, reps=1000, seed=seed)
    assert 0.85 <= rate <= 0.95, rate
