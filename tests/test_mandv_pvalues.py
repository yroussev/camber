"""Coefficient p-values and the DOE SEP validity verdict (#21 phase 21a).

The t / F tails are computed in the standard library (a regularized incomplete beta by its
continued fraction), so they are checked against closed forms, against CAMBER's own t table and
against the NIST StRD *Norris* certified regression (a NIST work, published as public information
on NIST's site; credit: J. Norris, NIST, "Calibration of Ozone Monitors", NIST StRD).
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv import stats  # noqa: E402
from camber.mandv.degreeday import fit_degree_day  # noqa: E402
from camber.mandv.models import N_PARAMS, fit_model  # noqa: E402
from camber.mandv.retrofit_isolation import fit_driver_model  # noqa: E402
from camber.mandv.stats import (  # noqa: E402
    _betainc,
    _f_sf,
    _t_sf,
    _t_two_sided_p,
    fit_stats,
    logical_signs,
    model_regression_tests,
    regression_tests,
    sep_validity,
)

# ------------------------------------------------------------------------------ distributions


@pytest.mark.parametrize("t", [0.0, 0.1, 1.0, 3.0, 50.0, 1e4, -2.0])
def test_t_df1_is_cauchy(t):
    assert _t_sf(t, 1) == pytest.approx(0.5 - math.atan(t) / math.pi, rel=1e-12, abs=1e-15)


@pytest.mark.parametrize("t", [0.0, 0.5, 2.0, 10.0, 1e3])
def test_t_df2_closed_form(t):
    assert _t_sf(t, 2) == pytest.approx(0.5 - t / (2 * math.sqrt(2 + t * t)), rel=1e-10)


@pytest.mark.parametrize("d2", [1, 3, 10, 57.5, 400])
@pytest.mark.parametrize("t", [0.3, 1.7, 4.0])
def test_f_1_d_is_t_squared(t, d2):
    assert _f_sf(t * t, 1, d2) == pytest.approx(_t_two_sided_p(t, d2), rel=1e-11)


def test_f_2_d_closed_form():
    # F(2, d) upper tail: (1 + 2f/d)^(-d/2)
    for f, d in [(0.5, 4), (3.0, 10), (9.0, 30)]:
        assert _f_sf(f, 2, d) == pytest.approx((1 + 2 * f / d) ** (-d / 2), rel=1e-11)


def test_consistent_with_the_t_table():
    for conf, row in stats._T_BY_DF.items():
        for df, tval in row.items():
            if df is None:
                continue
            assert _t_two_sided_p(tval, df) == pytest.approx(1 - conf, abs=6e-4), (conf, df)


def test_betainc_edges_and_symmetry():
    assert _betainc(2.0, 3.0, 0.0) == 0.0 and _betainc(2.0, 3.0, 1.0) == 1.0
    assert math.isnan(_betainc(0.0, 1.0, 0.5)) and math.isnan(_betainc(1.0, 1.0, float("nan")))
    assert _betainc(1.0, 1.0, 0.3) == pytest.approx(0.3)
    assert _betainc(2.5, 4.0, 0.7) == pytest.approx(1 - _betainc(4.0, 2.5, 0.3), rel=1e-13)
    assert _t_sf(float("inf"), 5) == 0.0 and _t_sf(float("-inf"), 5) == 1.0
    assert math.isnan(_t_sf(1.0, 0)) and math.isnan(_f_sf(1.0, 0, 3))
    assert _f_sf(0.0, 2, 3) == 1.0 and _f_sf(float("inf"), 2, 3) == 0.0


# ------------------------------------------------------------------------------ regression tests

NORRIS = [  # (y, x), NIST StRD Norris.dat
    (0.1, 0.2), (338.8, 337.4), (118.1, 118.2), (888.0, 884.6), (9.2, 10.1), (228.1, 226.5),
    (668.5, 666.3), (998.5, 996.3), (449.1, 448.6), (778.9, 777.0), (559.2, 558.2), (0.3, 0.4),
    (0.1, 0.6), (778.1, 775.5), (668.8, 666.9), (339.3, 338.0), (448.9, 447.5), (10.8, 11.6),
    (557.7, 556.0), (228.3, 228.1), (998.0, 995.8), (888.8, 887.6), (119.6, 120.2), (0.3, 0.3),
    (0.6, 0.3), (557.6, 556.8), (339.3, 339.1), (888.0, 887.2), (998.5, 999.0), (778.9, 779.0),
    (10.2, 11.1), (117.6, 118.3), (228.9, 229.2), (668.4, 669.1), (449.2, 448.9), (0.2, 0.5),
]  # fmt: skip


def test_norris_certified_values():
    y = np.array([r[0] for r in NORRIS])
    x = np.array([r[1] for r in NORRIS])
    X = np.column_stack([np.ones_like(x), x])
    rt = regression_tests(X, y, names=("B0", "B1"))
    assert rt.coef[0] == pytest.approx(-0.262323073774029, rel=1e-9)
    assert rt.coef[1] == pytest.approx(1.00211681802045, rel=1e-12)
    assert rt.se[0] == pytest.approx(0.232818234301152, rel=1e-9)
    assert rt.se[1] == pytest.approx(0.429796848199937e-03, rel=1e-9)
    assert rt.r2 == pytest.approx(0.999993745883712, rel=1e-12)
    assert rt.f_stat == pytest.approx(5436385.54079785, rel=1e-8)
    assert rt.df == 34 and rt.relevant == ("B1",) and rt.f_p < 1e-12
    # intercept t = -1.1267; its two-sided p on 34 df
    assert rt.p[0] == pytest.approx(_t_two_sided_p(-0.262323073774029 / 0.232818234301152, 34))


def test_against_numpy_ols():
    rng = np.random.default_rng(5)
    n = 60
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    y = X @ np.array([2.0, 0.8, 0.05]) + rng.normal(0, 1, n)
    rt = regression_tests(X, y, names=("c", "a", "b"))
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = resid @ resid / (n - 3)
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * s2)
    np.testing.assert_allclose(rt.coef, beta, rtol=1e-10)
    np.testing.assert_allclose(rt.se, se, rtol=1e-10)
    np.testing.assert_allclose(rt.t, beta / se, rtol=1e-10)
    r2 = 1 - resid @ resid / ((y - y.mean()) ** 2).sum()
    assert rt.adj_r2 == pytest.approx(1 - (1 - r2) * (n - 1) / (n - 3))
    assert rt.f_p == pytest.approx(_f_sf(((r2 / 2) / ((1 - r2) / (n - 3))), 2, n - 3), rel=1e-9)
    assert rt.p[2] > 0.05 and rt.p[1] < 1e-6
    json.dumps(rt.as_dict(), allow_nan=False)


def test_change_points_cost_a_degree_of_freedom_and_are_caveated():
    rng = np.random.default_rng(2)
    T = rng.uniform(20, 95, 120)
    y = 30 + 2 * np.maximum(0, T - 65) + rng.normal(0, 3, 120)
    m = fit_model(T, y, "3PC")
    rt = model_regression_tests(m, T, y)
    assert rt.names == ("base", "cool_slope") and rt.n_params == 3 and rt.df == 117
    assert rt.coef[1] == pytest.approx(m.coeffs["cool_slope"], rel=1e-9)
    assert rt.conditional_on_change_points and "conditional" in rt.caveats[0]
    st = fit_stats(y, m.predict(T), N_PARAMS["3PC"])
    assert st.f_stat == pytest.approx(rt.f_stat, rel=1e-3)
    assert st.f_pvalue == pytest.approx(rt.f_p, rel=1e-3, abs=1e-300)
    assert st.adj_r2 == pytest.approx(rt.adj_r2, abs=1e-4)


def test_fit_stats_new_fields_are_trailing_and_optional():
    fs = stats.FitStats(10, 2, 0.9, 1.0, 0.1, 0.0, 5.0, True, "")
    assert fs.f_pvalue is None and fs.adj_r2 is None
    assert list(stats.FitStats.__dataclass_fields__)[-2:] == ["f_pvalue", "adj_r2"]
    st = fit_stats(np.arange(10.0), np.arange(10.0) + 0.1, 1)
    assert st.f_pvalue is None  # p = 1: no F-test


def test_rho_adjusted_p_values_are_larger():
    rng = np.random.default_rng(9)
    n = 365
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.6 * e[i - 1] + rng.normal()
    x = np.sin(np.arange(n) / 58.0)
    y = 5 + 0.3 * x + e
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rt = regression_tests(np.column_stack([np.ones(n), x]), y, names=("c", "x"), time_index=idx)
    assert rt.rho == pytest.approx(0.6, abs=0.1)
    assert rt.p_rho_adjusted[1] > rt.p[1] and rt.f_p_rho_adjusted > rt.f_p
    few = regression_tests(
        np.column_stack([np.ones(40), x[:40]]), y[:40], names=("c", "x"), time_index=idx[:40]
    )
    assert few.p_rho_adjusted is not None or few.caveats


def test_bad_inputs():
    with pytest.raises(ValueError, match="names"):
        regression_tests(np.ones((5, 2)), np.ones(5), names=("a",))
    with pytest.raises(ValueError, match="rows"):
        regression_tests(np.ones((5, 2)), np.ones(4), names=("a", "b"))
    with pytest.raises(ValueError, match="n >"):
        regression_tests(np.ones((3, 2)), np.ones(3), names=("a", "b"), n_change_points=1)
    x = np.arange(10.0)
    rt = regression_tests(np.column_stack([np.ones(10), x]), 1 + 2 * x, names=("c", "x"))
    assert rt.f_p < 1e-12 and rt.p[1] < 1e-12  # a perfect fit
    rt = regression_tests(x, 2 * x, names=("x",))  # no intercept, one column: no F-test
    assert rt.f_p is None and rt.p[0] == 0.0


# ------------------------------------------------------------------------------ SEP validity


def _cooling(n=200, slope=2.0, noise=3.0, seed=0):
    rng = np.random.default_rng(seed)
    T = rng.uniform(40, 95, n)
    return T, 30 + slope * np.maximum(0, T - 65) + rng.normal(0, noise, n)


def test_sep_valid_model_passes():
    T, y = _cooling()
    m = fit_model(T, y, "3PC")
    v = sep_validity(model_regression_tests(m, T, y), signs=logical_signs(m))
    assert v.sep_valid and v.failures == []
    assert any("conditional" in c for c in v.caveats)
    assert any("rho-adjusted verdict not computed" in c for c in v.caveats)
    json.dumps(v.as_dict())


def test_sep_wrong_sign_fails():
    """SEP requires coefficients consistent with a logical understanding: a cooling slope that
    falls with temperature is rejected even when it is significant."""
    T, y = _cooling()
    X = np.column_stack([np.ones_like(T), np.maximum(0, T - 65)])
    rt = regression_tests(X, 200 - (y - 30), names=("base", "cool_slope"), n_change_points=1)
    v = sep_validity(rt, signs={"cool_slope": 1})
    assert not v.sep_valid and any("not positive" in f for f in v.failures)
    assert sep_validity(rt, signs={"cool_slope": -1}).sep_valid
    with pytest.raises(ValueError, match="unknown"):
        sep_validity(rt, signs={"nope": 1})
    with pytest.raises(ValueError, match=r"\+1 or -1"):
        sep_validity(rt, signs={"cool_slope": 2})


def test_sep_weak_model_fails_each_test():
    rng = np.random.default_rng(1)
    n = 40
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    y = rng.normal(size=n)  # nothing explains y
    v = sep_validity(regression_tests(X, y, names=("c", "a", "b")))
    joined = " | ".join(v.failures)
    assert not v.sep_valid
    assert "F-test p >= 0.10" in joined and "R2" in joined and "no relevant variable with" in joined
    assert "p >= 0.20" in joined
    assert any("signs not checked" in c for c in v.caveats)
    const = regression_tests(np.ones((10, 1)), rng.normal(size=10), names=("c",))
    assert "no relevant variable" in sep_validity(const).failures[1]
    assert const.f_p is None and "F-test not available" in sep_validity(const).failures[0]


def test_sep_rho_adjusted_verdict_can_disagree():
    """SEP's tests assume independent residuals; at rho ~0.8 a weak second variable passes as
    written but not once the variance is inflated -- reported, and caveated."""
    n = 365
    rng = np.random.default_rng(0)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.8 * e[i - 1] + rng.normal()
    x, z = np.sin(np.arange(n) / 40.0), np.cos(np.arange(n) / 23.0)
    y = 10 + 3 * x + 0.25 * z + e
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rt = regression_tests(np.column_stack([np.ones(n), x, z]), y, names=("c", "x", "z"),
                          time_index=idx)  # fmt: skip
    v = sep_validity(rt)
    assert v.sep_valid is True and v.sep_valid_rho_adjusted is False
    assert any("z" in f for f in v.failures_rho_adjusted)
    assert any("changes once residual autocorrelation" in c for c in v.caveats)


def test_logical_signs_and_model_tests_for_other_models():
    T, y = _cooling()
    assert logical_signs(fit_model(T, y, "4P")) is None
    assert logical_signs(fit_model(T, y, "5P")) == {"heat_slope": 1, "cool_slope": 1}
    assert logical_signs(object()) is None
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, 50)
    dm = fit_driver_model(x, 1 + 2 * x + rng.normal(0, 0.5, 50))
    rt = model_regression_tests(dm, x, 1 + 2 * x)
    assert rt.names == ("intercept", "driver") and not rt.conditional_on_change_points
    tm = rng.uniform(25, 85, 36)
    em = 300 + 12 * np.maximum(0, 60 - tm) + rng.normal(0, 10, 36)
    dd = fit_degree_day(tm, em, kind="heating")
    rt = model_regression_tests(dd, tm, em)
    assert rt.names == ("base", "heating_slope") and rt.conditional_on_change_points
    with pytest.raises(TypeError):
        model_regression_tests(object(), x, x)
