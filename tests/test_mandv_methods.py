"""SEP adjustment-model methods (#21 phase 21b; camber.mandv.methods).

Forecast as a MethodResult, SEP chaining exactly per SEP 2019 Ed. 2 §6.2.4 (Eq 6 product, Eq 11
sum), the shared-model chain covariance checked by Monte Carlo, the sequential chain (a CAMBER
extension), standard conditions, method selection in SEP's order and the config keys.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.methods import (  # noqa: E402
    MethodResult,
    backcast_savings,
    chained_savings,
    forecast_savings,
    select_method,
    sequential_chain,
    standard_conditions_savings,
)
from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.sep import aggregate_energy_types, chained_senpi, top_down_savings  # noqa: E402
from camber.mandv.stats import avoided_energy_savings, fit_stats  # noqa: E402

PERIODS = {
    "baseline": ["2018-01-01", "2018-12-31"],
    "intermediate": ["2019-01-01", "2019-12-31"],
    "reporting": ["2020-01-01", "2020-12-31"],
}
IDX = pd.date_range("2019-01-01", periods=365, freq="D")


def _truth(T, saving=0.0):
    return (1 - saving) * (40 + 1.5 * np.maximum(0, 50 - T) + 2.0 * np.maximum(0, T - 70))


def _three_years(seed=0, n=365, b=(20, 95), i=(20, 95), r=(20, 95)):
    rng = np.random.default_rng(seed)
    Tb, Ti, Tr = rng.uniform(*b, n), rng.uniform(*i, n), rng.uniform(*r, n)
    yb = _truth(Tb) + rng.normal(0, 2, n)
    yi = _truth(Ti, 0.1) + rng.normal(0, 2, n)
    yr = _truth(Tr, 0.2) + rng.normal(0, 2, n)
    return Tb, yb, Ti, yi, Tr, yr


# --------------------------------------------------------------------------- forecast


def test_forecast_savings_wraps_avoided_energy_exactly():
    Tb, yb, _, _, Tr, yr = _three_years()
    mb = fit_model(Tb, yb, "5P")
    st = fit_stats(yb, mb.predict(Tb), 5)
    kw = dict(cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=5, rho=0.2)
    ae = avoided_energy_savings(mb, Tr, yr, **kw)
    res = forecast_savings(mb, Tr, yr, **kw, baseline_version="v3")
    assert isinstance(res, MethodResult)
    assert res.method == "forecast" and res.basis == "reporting-period conditions"
    assert res.kernel == "g14" and res.baseline_version == "v3"
    assert res.savings == ae.avoided_energy and res.projected == ae.baseline_projected
    assert res.measured == ae.reporting_actual and res.abs_uncertainty == ae.abs_uncertainty
    assert res.enpi == pytest.approx(ae.reporting_actual / ae.baseline_projected, abs=1e-6)
    assert res.savings_pct == pytest.approx(1 - res.enpi, abs=1e-4)
    assert res.enpi_uncertainty == pytest.approx(
        res.enpi * res.abs_uncertainty / res.projected, abs=1e-6
    )
    assert res.sep_terms == {"adjusted_baseline": res.projected, "observed_reporting": res.measured}
    assert res.sep_range_valid is True and res.df == 360
    json.dumps(res.as_dict(), allow_nan=False)


def test_forecast_declines_like_avoided_energy():
    Tb, yb, _, _, Tr, yr = _three_years(b=(20, 40), r=(70, 95))
    mb = fit_model(Tb, yb, "3PH")
    res = forecast_savings(mb, Tr, yr, cv_rmse=0.05, n_baseline=365, p_baseline=3)
    assert res.declined and res.savings is None and res.enpi is None
    assert res.sep_range_valid is False
    assert res.sep_terms == {"observed_reporting": res.measured}


def test_backcast_carries_the_sep_fields():
    Tb, yb, _, _, Tr, yr = _three_years(seed=1)
    mr = fit_model(Tr, yr, "5P")
    res = backcast_savings(mr, Tb, yb, cv_rmse=0.05, n_reporting=365, p_reporting=5)
    assert res.enpi == pytest.approx(res.projected / res.measured, abs=1e-6)
    assert res.savings == pytest.approx(
        top_down_savings("backcast", **res.sep_terms), abs=0.02
    )  # Eq 9
    assert res.sep_range_valid is True


# --------------------------------------------------------------------------- SEP chaining


def test_chaining_recovers_the_saving_and_obeys_eq6_eq11():
    Tb, yb, Ti, yi, Tr, yr = _three_years(seed=3)
    mi = fit_model(Ti, yi, "5P", time_index=IDX)
    res = chained_savings(mi, Tb, yb, Tr, yr, periods=PERIODS)
    assert res.method == "chaining" and res.kernel == "exact" and not res.declined
    back, fwd = res.links
    assert (back.method, back.applied_to, fwd.method, fwd.applied_to) == (
        "backcast",
        "baseline",
        "forecast",
        "reporting",
    )
    # Eq 11: the saving is the sum of the two links; Eq 6: the SEnPI is their product
    assert res.savings == pytest.approx(back.savings + fwd.savings, abs=0.02)
    assert res.savings == pytest.approx(top_down_savings("chaining", **res.sep_terms), abs=0.02)
    assert res.enpi == pytest.approx(back.enpi * fwd.enpi, abs=1e-5)
    assert res.enpi == pytest.approx(chained_senpi(**_eq6(res.sep_terms)), abs=1e-5)
    assert res.savings_pct == pytest.approx(1 - res.enpi, abs=1e-4)
    # truth: baseline -> reporting is 20% (10% then a further 1 - 0.8/0.9)
    assert res.enpi == pytest.approx(0.8, abs=0.01)
    true_s = (_truth(Tb) - _truth(Tb, 0.1)).sum() + (_truth(Tr, 0.1) - _truth(Tr, 0.2)).sum()
    assert abs(res.savings - true_s) <= res.abs_uncertainty
    u = res.uncertainty_terms
    assert u["covariance"] > 0 and u["var_savings"] < u["var_savings_independent"]
    assert res.coverage["baseline_days"] == res.coverage["intermediate_days"] == 365
    json.dumps(res.as_dict(), allow_nan=False)


def _eq6(t):
    return {
        "observed_baseline": t["observed_baseline"],
        "intermediate_at_baseline": t["intermediate_at_baseline"],
        "intermediate_at_reporting": t["intermediate_at_reporting"],
        "observed_reporting": t["observed_reporting"],
    }


def test_chaining_is_exactly_sep_one_intermediate_period():
    Tb, yb, Ti, yi, Tr, yr = _three_years(seed=4)
    mi = fit_model(Ti, yi, "5P")
    short = dict(PERIODS, intermediate=["2019-01-01", "2019-06-30"])
    with pytest.raises(ValueError, match="same length"):
        chained_savings(mi, Tb, yb, Tr, yr, periods=short)
    after = dict(PERIODS, intermediate=["2021-01-01", "2021-12-31"])
    with pytest.raises(ValueError, match="between"):
        chained_savings(mi, Tb, yb, Tr, yr, periods=after)
    with pytest.raises(ValueError, match="kernel='exact'"):
        chained_savings(mi, Tb, yb, Tr, yr, periods=PERIODS, kernel="g14")
    with pytest.raises(ValueError, match="periods must map"):
        chained_savings(mi, Tb, yb, Tr, yr, periods={"baseline": PERIODS["baseline"]})
    # a leap year is still "the same length"
    leap = {
        "baseline": ["2019-01-01", "2019-12-31"],
        "intermediate": ["2020-01-01", "2020-12-31"],
        "reporting": ["2021-01-01", "2021-12-31"],
    }
    assert not chained_savings(mi, Tb, yb, Tr, yr, periods=leap).declined


def test_chaining_declines_when_the_intermediate_model_misses_a_period():
    Tb, yb, Ti, yi, Tr, yr = _three_years(seed=5, b=(20, 50), i=(55, 95), r=(55, 95))
    mi = fit_model(Ti, yi, "3PC")
    res = chained_savings(mi, Tb, yb, Tr, yr, periods=PERIODS)
    assert res.declined and res.savings is None and res.enpi is None
    assert "backcast to the baseline" in res.declined_reason
    assert res.coverage["baseline"]["tier"] == "severe"
    assert res.coverage["reporting"]["tier"] == "in_range"
    assert res.links[0].enpi is None and res.sep_range_valid is False


def _ar1(rng, n, rho, sd):
    e = np.empty(n)
    innov = rng.normal(0, sd * np.sqrt(1 - rho**2), n)
    e[0] = rng.normal(0, sd)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + innov[i]
    return e


def _chain_mc(rho, reps, seed, n=365):
    """Seeded runs of a 3PC chain whose baseline and reporting climates differ; the intermediate
    period spans both. Returns (savings coverage, SEnPI coverage, empirical var, predicted var,
    independence var)."""
    rng = np.random.default_rng(seed)

    def f(T, a, b):
        return a + b * np.maximum(0, T - 60)

    hits = hits_e = 0
    err, v, vi = [], [], []
    for _ in range(reps):
        Tb, Ti, Tr = rng.uniform(40, 80, n), rng.uniform(40, 95, n), rng.uniform(55, 95, n)
        yb = f(Tb, 100, 3.0) + _ar1(rng, n, rho, 6)
        yi = f(Ti, 90, 2.7) + _ar1(rng, n, rho, 6)
        yr = f(Tr, 80, 2.4) + _ar1(rng, n, rho, 6)
        eb, eib = f(Tb, 100, 3.0).sum(), f(Tb, 90, 2.7).sum()
        eir, er = f(Tr, 90, 2.7).sum(), f(Tr, 80, 2.4).sum()
        true_s = (eb - eib) + (eir - er)
        true_e = (eib / eb) * (er / eir)
        m = fit_model(Ti, yi, "3PC", time_index=IDX)
        r = chained_savings(m, Tb, yb, Tr, yr, periods=PERIODS)
        hits += abs(r.savings - true_s) <= r.abs_uncertainty
        hits_e += abs(r.enpi - true_e) <= r.enpi_uncertainty
        err.append(r.savings - true_s)
        v.append(r.uncertainty_terms["var_savings"])
        vi.append(r.uncertainty_terms["var_savings_independent"])
    return hits / reps, hits_e / reps, float(np.var(err)), float(np.mean(v)), float(np.mean(vi))


@pytest.mark.parametrize("rho,seed", [(0.0, 11), (0.4, 12), (0.8, 13)])
def test_monte_carlo_chain_covariance(rho, seed):
    """The shared-model covariance form covers at the nominal 90%; the independence (B-19) form
    over-states the variance because both projections share the intermediate model's error."""
    cov_s, cov_e, emp, pred, indep = _chain_mc(rho, reps=600, seed=seed)
    assert 0.85 <= cov_s <= 0.95, cov_s
    assert 0.85 <= cov_e <= 0.95, cov_e
    assert indep > 1.3 * pred  # the covariance term matters here
    if rho == 0.0:
        assert emp == pytest.approx(pred, rel=0.15)


# --------------------------------------------------------------------------- sequential chain


def test_sequential_chain_is_labelled_an_extension_and_combines_b19_b20():
    Tb, yb, Ti, yi, Tr, yr = _three_years(seed=6)
    mb = fit_model(Tb, yb, "5P", time_index=IDX)
    mi = fit_model(Ti, yi, "5P", time_index=IDX)
    l1 = forecast_savings(mb, Ti, yi, cv_rmse=0, n_baseline=365, p_baseline=5, kernel="exact")
    l2 = forecast_savings(mi, Tr, yr, cv_rmse=0, n_baseline=365, p_baseline=5, kernel="exact")
    res = sequential_chain([l1, l2])
    assert res.method == "sequential_chain" and res.sep_terms is None
    assert "CAMBER extension, not an SEP method" in res.caveats[0]
    assert res.kernel == "exact" and len(res.links) == 2
    assert res.enpi == pytest.approx(l1.enpi * l2.enpi, abs=1e-5)
    assert res.savings == pytest.approx(l1.savings + l2.savings, abs=0.02)
    assert res.abs_uncertainty == pytest.approx(
        np.hypot(l1.abs_uncertainty, l2.abs_uncertainty), rel=1e-3
    )
    rel = np.hypot(l1.enpi_uncertainty / l1.enpi, l2.enpi_uncertainty / l2.enpi)
    assert res.enpi_uncertainty == pytest.approx(res.enpi * rel, rel=1e-3)
    assert res.enpi == pytest.approx(0.8, abs=0.01)
    with pytest.raises(ValueError, match="not an SEP method"):
        aggregate_energy_types({"natural_gas": res})
    with pytest.raises(ValueError, match="at least two"):
        sequential_chain([l1])
    l1.declined = True
    assert sequential_chain([l1, l2]).declined


# --------------------------------------------------------------------------- standard conditions


def test_standard_conditions_exact_by_default():
    Tb, yb, _, _, Tr, yr = _three_years(seed=7)
    mb = fit_model(Tb, yb, "5P", time_index=IDX)
    mr = fit_model(Tr, yr, "5P", time_index=IDX)
    S = np.linspace(25, 90, 365)
    res = standard_conditions_savings(mb, mr, S)
    assert res.method == "standard_conditions" and res.kernel == "exact"
    assert res.measured is None and res.enpi == pytest.approx(0.8, abs=0.01)
    assert res.savings == pytest.approx(top_down_savings("standard_conditions", **res.sep_terms))
    assert res.enpi_uncertainty > 0 and res.sep_range_valid is True
    g = standard_conditions_savings(
        mb, mr, S, kernel="g14", baseline_cv_rmse=0.05, n_baseline=365, p_baseline=5
    )
    assert g.kernel == "g14" and g.enpi_uncertainty is None
    with pytest.raises(ValueError, match="needs baseline_cv_rmse"):
        standard_conditions_savings(mb, mr, S, kernel="g14")


# --------------------------------------------------------------------------- selection


def _frame(Tb, yb, Ti, yi, Tr, yr):
    idx = pd.date_range("2018-01-01", periods=3 * len(Tb), freq="D")
    return pd.DataFrame({"oat": np.r_[Tb, Ti, Tr], "energy": np.r_[yb, yi, yr]}, index=idx)


def test_select_method_follows_sep_order_and_only_proposes():
    frame = _frame(*_three_years(seed=8))
    rep = [str(frame.index[730].date()), str(frame.index[-1].date())]
    p = select_method(frame, baseline=["2018-01-01", "2018-12-31"], reporting=rep)
    assert p.proposed == "forecast" and not p.declined
    assert [s["method"] for s in p.steps] == [
        "forecast",
        "backcast",
        "chaining",
        "standard_conditions",
    ]
    assert {r["method"] for r in p.sensitivity} == {"forecast", "backcast", "chaining"}
    assert "a proposal, not a reported result" in p.caveats[0]
    assert any("valid methods give SEnPI" in c for c in p.caveats)
    assert not hasattr(p, "savings")  # no headline figure
    assert p.models["baseline"]["sep_valid"] is True
    json.dumps(p.as_dict(), default=str)


def test_select_method_falls_back_to_backcast_then_chaining():
    # a mild baseline cannot forecast onto a full-range year; the reporting model backcasts
    frame = _frame(*_three_years(seed=9, b=(55, 70)))
    rep = [str(frame.index[730].date()), str(frame.index[-1].date())]
    p = select_method(frame, baseline=["2018-01-01", "2018-12-31"], reporting=rep)
    assert p.proposed == "backcast" and not p.steps[0]["valid"]
    # neither end covers the other; only the full-range intermediate year covers both
    frame = _frame(*_three_years(seed=10, b=(20, 50), i=(20, 95), r=(60, 95)))
    p = select_method(frame, baseline=["2018-01-01", "2018-12-31"], reporting=rep)
    assert p.proposed == "chaining" and p.intermediate_period[0] == "2019-01-01"
    assert p.models["intermediate"]["sep_valid"] is True
    assert p.results["chaining"].method == "chaining"


def test_select_method_declines_when_nothing_holds():
    rng = np.random.default_rng(1)
    n = 365
    frame = pd.DataFrame(
        {"oat": rng.uniform(20, 95, 3 * n), "energy": rng.normal(100, 5, 3 * n)},
        index=pd.date_range("2018-01-01", periods=3 * n, freq="D"),
    )  # no weather dependence: no SEP-valid model anywhere
    p = select_method(
        frame, baseline=["2018-01-01", "2018-12-31"], reporting=["2020-01-01", "2020-12-30"]
    )
    assert p.proposed is None and p.declined and "§3.6.6" in p.declined_reason
    assert p.sensitivity == []


# --------------------------------------------------------------------------- config keys


def _mv_three_year_store(tmp_path):
    """One meter over 2016-2018: heating below 50 F and cooling above 65 F, 10% saved from 2017
    and a further 10% from 2018. The middle year is the widest (it covers both others)."""
    from camber.model.roles import Role
    from camber.store import ParquetStore

    st = ParquetStore(str(tmp_path / "mv3"))
    idx = pd.date_range("2016-01-01", "2018-12-31 23:00", freq="1h")
    rng = np.random.default_rng(46)
    doy = idx.dayofyear.to_numpy()
    amp = np.where(idx.year == 2017, 32, 22)
    oat = 57 - amp * np.cos((doy - 15) / 365 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    rate = 20 + 1.0 * np.maximum(0, 50 - oat) + 2.0 * np.maximum(0, oat - 65)
    rate = rate * np.where(idx.year == 2016, 1.0, np.where(idx.year == 2017, 0.9, 0.81))
    rate = rate + rng.normal(0, 1.0, len(idx))
    st.write_role_frame(
        pd.DataFrame({Role.POWER: rate, Role.OAT: oat}, index=idx),
        facility_id="f",
        equip="meter",
        equip_class="M",
    )
    return {
        "source": {"kind": "store", "store": st.root, "facility_id": "f"},
        "equipment": [{"class": "M"}],
        "mv": [
            {
                "class": "M",
                "role": "power",
                "period": ["2016-01-01", "2016-12-31"],
                "reporting_period": ["2018-01-01", "2018-12-31"],
            }
        ],
    }


def _savings(cfg):
    from camber.config import run_config

    return [
        f for f in run_config(cfg).findings if f.rule.startswith("mv_") and f.rule != "mv_baseline"
    ]


def test_config_undeclared_method_is_a_forecast_with_a_caveat(tmp_path):
    cfg = _mv_three_year_store(tmp_path)
    (s,) = _savings(cfg)
    m = s.metrics
    assert m["method"] == "forecast" and m["method_declared"] is False and m["kernel"] == "g14"
    assert any("no mv.method declared" in c for c in s.caveats)
    assert m["enpi"] == pytest.approx(1 - m["savings_pct"], abs=1e-3)
    cfg["mv"][0].update(method="forecast", kernel="exact")
    (s,) = _savings(cfg)
    assert s.metrics["method_declared"] is True and s.metrics["kernel"] == "exact"
    assert not any("no mv.method declared" in c for c in s.caveats)
    json.dumps(s.metrics, allow_nan=False)


def test_config_backcast_and_chaining(tmp_path):
    cfg = _mv_three_year_store(tmp_path)
    cfg["mv"][0]["method"] = "backcast"
    (b,) = _savings(cfg)
    assert b.rule == "mv_savings" and b.metrics["method"] == "backcast"
    assert b.metrics["kernel"] == "g14" and "backcast savings" in b.summary
    cfg["mv"][0].update(method="chaining", intermediate_period=["2017-01-01", "2017-12-31"])
    (c,) = _savings(cfg)
    m = c.metrics
    assert m["method"] == "chaining" and m["kernel"] == "exact" and not m["declined"]
    assert m["enpi"] == pytest.approx(0.81, abs=0.02)
    assert len(m["links"]) == 2 and m["intermediate_period"] == ["2017-01-01", "2017-12-31"]
    assert "SEnPI" in c.summary
    json.dumps(m, allow_nan=False)


def test_config_auto_only_proposes(tmp_path):
    cfg = _mv_three_year_store(tmp_path)
    cfg["mv"][0]["method"] = "auto"
    (p,) = _savings(cfg)
    assert p.rule == "mv_method_proposal"
    assert p.metrics["proposed"] in ("forecast", "backcast", "chaining")
    assert "avoided_energy" not in p.metrics and "savings" not in p.metrics
    assert p.metrics["sensitivity"] and "Declare mv.method" in p.summary
    json.dumps(p.metrics, allow_nan=False, default=str)


def test_config_method_and_kernel_validation(tmp_path):
    from camber.config import run_config

    cfg = _mv_three_year_store(tmp_path)
    e = cfg["mv"][0]
    for bad, match in (
        ({"method": "backwards"}, "mv.method must be"),
        ({"kernel": "fancy"}, "mv.kernel must be"),
        ({"method": "chaining", "kernel": "g14"}, "needs kernel 'exact'"),
        ({"method": "chaining"}, "intermediate_period"),
        ({"method": "standard_conditions"}, "normal_year"),
    ):
        cfg["mv"][0] = {**e, **bad}
        with pytest.raises(ValueError, match=match):
            run_config(cfg)
    cfg["mv"][0] = {k: v for k, v in e.items() if k != "period"} | {"method": "backcast"}
    with pytest.raises(ValueError, match="needs both a period"):
        run_config(cfg)
    cfg["mv"][0] = {**e, "method": "chaining", "intermediate_period": ["2017-01-01", "2017-06-30"]}
    with pytest.raises(ValueError, match="same length"):
        run_config(cfg)


def test_config_standard_conditions(tmp_path):
    cfg = _mv_three_year_store(tmp_path)
    cfg["mv"][0].update(method="standard_conditions", normal_year=list(np.linspace(30, 85, 365)))
    (s,) = _savings(cfg)
    assert s.metrics["method"] == "standard_conditions" and s.metrics["kernel"] == "exact"
    assert s.metrics["measured"] is None and s.metrics["enpi"] == pytest.approx(0.81, abs=0.03)


def test_intermediate_windows_include_the_abutting_ones():
    from camber.mandv.methods import _intermediate_windows

    # periods starting mid-month: the only window that fits is the one abutting both
    w = _intermediate_windows(["2021-03-15", "2022-03-14"], ["2023-03-15", "2024-03-14"], "MS")
    assert [(str(a.date()), str(b.date())) for a, b in w] == [("2022-03-15", "2023-03-14")]
    assert (
        _intermediate_windows(["2018-01-01", "2018-12-31"], ["2019-01-01", "2019-12-31"], "MS")
        == []
    )
    w = _intermediate_windows(["2021-03-15", "2022-03-14"], ["2024-03-15", "2025-03-14"], "MS")
    assert str(w[0][0].date()) == "2022-03-15" and str(w[-1][0].date()) == "2023-03-16"
