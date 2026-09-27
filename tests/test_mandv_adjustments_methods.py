"""Adjustments applied to every SEP method (#21 phases 21b + 21c).

The flow is method, then adjustments, then result. These tests check, for each method, that an
empty ledger reproduces the method's own numbers, that a planted non-routine event is recovered,
that a chain restates only the link an entry is dated in (Eq 6 re-multiplied, Eq 11 re-summed,
the shared-model covariance carried through) and the config order.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.mandv.adjustments import (  # noqa: E402
    DEFAULT_SETTLE_DAYS,
    EcmSchedule,
    NonRoutineAdjustment,
    StaticFactorAdjustment,
    apply_adjustments,
    check_validity,
    estimate_nre_indicator,
)
from camber.mandv.methods import (  # noqa: E402
    backcast_savings,
    chained_savings,
    forecast_savings,
    sequential_chain,
    standard_conditions_savings,
)
from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.stats import _t_value, avoided_energy_savings, fit_stats  # noqa: E402

YEARS = {
    "baseline": ["2018-01-01", "2018-12-31"],
    "intermediate": ["2019-01-01", "2019-12-31"],
    "reporting": ["2020-01-01", "2020-12-30"],
}


def _truth(T):
    return 40 + 1.5 * np.maximum(0, 50 - T) + 2.0 * np.maximum(0, T - 70)


def _year(rng, start, level=1.0, sd=2.0, n=365):
    idx = pd.date_range(start, periods=n, freq="D")
    d = np.arange(n)
    T = 57 - 28 * np.cos((d - 15) / 365 * 2 * np.pi) + rng.normal(0, 3, n)
    return idx, T, level * _truth(T) + rng.normal(0, sd, n)


def _site(seed=0, event=None):
    """Three years, 10% saved from year 2 and 20% from year 3; ``event`` = (start day, per-day
    effect) in the reporting year (a new load, not a saving)."""
    rng = np.random.default_rng(seed)
    ib, Tb, yb = _year(rng, "2018-01-01")
    ii, Ti, yi = _year(rng, "2019-01-01", 0.9)
    ir, Tr, yr = _year(rng, "2020-01-01", 0.8)
    if event is not None:
        yr = yr.copy()
        yr[event[0] :] += event[1]
    return {"b": (ib, Tb, yb), "i": (ii, Ti, yi), "r": (ir, Tr, yr)}


def _fit(p):
    idx, T, y = p
    m = fit_model(T, y, "5P", time_index=idx)
    st = fit_stats(y, m.predict(T), 5, time_index=idx)
    return m, st


def _forecast(s, kernel="g14"):
    mb, st = _fit(s["b"])
    _, Tr, yr = s["r"]
    kw = dict(cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=5, rho=st.rho_lag1)
    return (
        mb,
        forecast_savings(mb, Tr, yr, kernel=kernel, **kw),
        avoided_energy_savings(mb, Tr, yr, kernel=kernel, **kw),
    )


def _backcast(s):
    mr, st = _fit(s["r"])
    _, Tb, yb = s["b"]
    return mr, backcast_savings(
        mr, Tb, yb, cv_rmse=st.cv_rmse, n_reporting=st.n, p_reporting=5, rho=st.rho_lag1
    )


def _chain(s):
    mi, st = _fit(s["i"])
    (_, Tb, yb), (_, Tr, yr) = s["b"], s["r"]
    return mi, chained_savings(mi, Tb, yb, Tr, yr, periods=YEARS, rho=st.rho_lag1)


def _chain_links(s, mi):
    (ib, Tb, yb), (ii, _, _), (ir, Tr, yr) = s["b"], s["i"], s["r"]
    return [
        {"index": ib, "drivers": Tb, "measured": yb, "model": mi, "reporting_index": ii},
        {"index": ir, "drivers": Tr, "measured": yr, "model": mi},
    ]


def _std(s, kernel="exact"):
    mb, stb = _fit(s["b"])
    mr, str_ = _fit(s["r"])
    normal = 57 - 28 * np.cos((np.arange(365) - 15) / 365 * 2 * np.pi)
    kw = {}
    if kernel == "g14":
        kw = dict(baseline_cv_rmse=stb.cv_rmse, n_baseline=stb.n, p_baseline=5,
                  reporting_cv_rmse=str_.cv_rmse, n_reporting=str_.n, p_reporting=5)  # fmt: skip
    return mb, mr, normal, standard_conditions_savings(mb, mr, normal, kernel=kernel, **kw)


_ENG = dict(method="engineering", start="2020-06-01", reason="rental", amount=500.0, se=50.0,
            evidence="invoice")  # fmt: skip


# --------------------------------------------------------------------------- empty ledger


def test_an_empty_ledger_reproduces_every_method():
    s = _site(1)
    _, fc, sav = _forecast(s)
    _, bc = _backcast(s)
    _, ch = _chain(s)
    _, _, _, sc = _std(s)
    _, _, _, sg = _std(s, "g14")
    links = []
    for a, b in (("b", "i"), ("i", "r")):
        m, st = _fit(s[a])
        links.append(forecast_savings(m, s[b][1], s[b][2], cv_rmse=st.cv_rmse, n_baseline=st.n,
                                      p_baseline=5, rho=st.rho_lag1))  # fmt: skip
    sq = sequential_chain(links)
    for res in (fc, bc, ch, sc, sg, sq):
        adj = apply_adjustments(res, [])
        assert adj.method == res.method
        assert adj.savings == pytest.approx(res.savings, abs=0.02), res.method
        assert adj.enpi == pytest.approx(res.enpi, abs=2e-6), res.method
        assert adj.savings_pct == pytest.approx(res.savings_pct, abs=1e-4), res.method
        assert adj.abs_uncertainty == pytest.approx(res.abs_uncertainty, rel=1e-6), res.method
        if res.enpi_uncertainty is None:
            assert adj.enpi_uncertainty is None, res.method
        else:
            assert adj.enpi_uncertainty == pytest.approx(res.enpi_uncertainty, rel=1e-4)
        json.dumps(adj.as_dict(), allow_nan=False)
    # the SavingsResult and the forecast MethodResult adjust identically
    ir, Tr, yr = s["r"]
    led = [NonRoutineAdjustment(**_ENG)]
    a1 = apply_adjustments(sav, led, index=ir)
    a2 = apply_adjustments(fc, led, index=ir)
    assert (a1.savings, a1.abs_uncertainty) == (a2.savings, a2.abs_uncertainty)


# --------------------------------------------------------------------------- forecast / backcast


def test_forecast_method_result_recovers_a_reporting_period_event():
    s = _site(2, event=(200, 30.0))  # a new 30/day load from day 200 of the reporting year
    mb, fc, _ = _forecast(s)
    ir, Tr, yr = s["r"]
    nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting", model=mb)
    adj = apply_adjustments(fc, [nra], index=ir, drivers=Tr, measured=yr, model=mb)
    true = (_truth(Tr) - 0.8 * _truth(Tr)).sum()  # the 20% saving, without the new load
    assert abs(fc.savings - true) > 3 * fc.abs_uncertainty / 1.645
    assert abs(adj.savings - true) <= 2 * adj.abs_uncertainty  # ~3.3 sigma, one run
    assert adj.enpi == pytest.approx(adj.adjusted_reporting / adj.adjusted_baseline, abs=1e-6)
    assert adj.ledger[0]["rows_affected"] == 165


def test_backcast_dates_a_reporting_event_by_its_share_of_the_reporting_model():
    s = _site(3, event=(200, 30.0))
    mr, bc = _backcast(s)
    (ib, Tb, yb), (ir, Tr, yr) = s["b"], s["r"]
    nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting", model=mr)
    adj = apply_adjustments(bc, [nra], index=ib, drivers=Tb, measured=yb, model=mr,
                            reporting_index=ir)  # fmt: skip
    share = 165 / 365
    assert adj.ledger[0]["share_of_later_period"] == pytest.approx(share)
    assert adj.ledger[0]["rows_affected"] == pytest.approx(share * 365)
    true = 0.2 * _truth(Tb).sum()
    assert abs(adj.savings - true) < abs(bc.savings - true)
    assert abs(adj.savings - true) <= 2 * adj.abs_uncertainty  # ~3.3 sigma, one run
    # without reporting_index the indicator falls back on its own fit window, with a caveat
    alt = apply_adjustments(bc, [nra], index=ib)
    assert alt.savings == pytest.approx(adj.savings, abs=1.0)
    assert any("its own fit window" in c for c in alt.caveats)


# --------------------------------------------------------------------------- standard conditions


def test_standard_conditions_restates_the_baseline_model_at_standard_conditions():
    s = _site(4)
    mb, mr, normal, sc = _std(s)
    eng = NonRoutineAdjustment(**_ENG)
    adj = apply_adjustments(sc, [eng])
    assert adj.method == "standard_conditions" and sc.measured is None
    assert adj.baseline == pytest.approx(sc.projected, abs=0.01)
    assert adj.savings == pytest.approx(sc.savings + 500.0, abs=0.02)
    # the band: the exact baseline and reporting terms plus the entry in quadrature
    t = _t_value(sc.confidence, sc.df)
    u = sc.uncertainty_terms
    want = t * np.sqrt(u["v_param_baseline"] + u["v_param_reporting"] + 50.0**2)
    assert adj.abs_uncertainty == pytest.approx(want, rel=1e-4)
    # a proportional factor scales only the baseline model's share of the variance
    sf = StaticFactorAdjustment(factor="area", method="proportional", start="2019-01-01",
                                reason="wing", baseline_value=1.0, reporting_value=1.2,
                                affected_share=1.0)  # fmt: skip
    a2 = apply_adjustments(sc, [sf])
    want = t * np.sqrt(1.2**2 * u["v_param_baseline"] + u["v_param_reporting"])
    assert a2.adjusted_baseline == pytest.approx(1.2 * sc.projected, abs=0.02)
    assert a2.abs_uncertainty == pytest.approx(want, rel=1e-4)
    with pytest.raises(ValueError, match="standard-conditions rows carry no dates"):
        apply_adjustments(sc, [NonRoutineAdjustment(method="exclude", start="2020-03-01",
                                                    end="2020-04-01", reason="x")])  # fmt: skip


def test_standard_conditions_indicators():
    # a reporting-period load: the reporting model absorbs it on its share of the year
    s = _site(5, event=(200, 30.0))
    mb, mr, normal, sc = _std(s)
    ir, Tr, yr = s["r"]
    nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting", model=mr)
    adj = apply_adjustments(sc, [nra], drivers=normal, model=mb, reporting_index=ir)
    assert adj.ledger[0]["rows_affected"] == pytest.approx(165 / 365 * 365)
    true = 0.2 * _truth(normal).sum()
    assert abs(adj.savings - true) < abs(sc.savings - true)
    assert abs(adj.savings - true) <= 2 * adj.abs_uncertainty  # ~3.3 sigma, one run
    # a baseline-period closure: the refit replaces the baseline model with the event absent
    rng = np.random.default_rng(6)
    ib, Tb, yb = _year(rng, "2018-01-01")
    yb = yb.copy()
    yb[100:160] -= 60.0
    mb2 = fit_model(Tb, yb, "5P", time_index=ib)
    sc2 = standard_conditions_savings(mb2, mr, normal)
    base = estimate_nre_indicator(Tb, yb, ib, start=ib[100], end=ib[160], fit_period="baseline",
                                  model=mb2)  # fmt: skip
    a2 = apply_adjustments(sc2, [base], drivers=normal, model=mb2, reporting_index=ir)
    assert a2.kernel == "exact"
    assert abs(a2.adjusted_baseline - _truth(normal).sum()) < abs(
        sc2.projected - _truth(normal).sum()
    )
    assert a2.ledger[0]["rows_affected"] == 0.0


# --------------------------------------------------------------------------- SEP chain


def test_sep_chain_restates_only_the_link_an_entry_is_dated_in():
    s = _site(7, event=(200, 30.0))
    mi, ch = _chain(s)
    links = _chain_links(s, mi)
    ir, Tr, yr = s["r"]
    assert ch.links[0].period == YEARS["baseline"] and ch.links[1].period == YEARS["reporting"]
    assert ch.links[0].model_window == YEARS["intermediate"]
    nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting", model=mi)
    adj = apply_adjustments(ch, [nra], links=links)
    l1, l2 = adj.links
    assert l1["adjusted_baseline"] == l1["baseline"] and l1["entries"] == []
    assert l2["entries"] == [0] and adj.ledger[0]["link"] == "link 2"
    amount = adj.ledger[0]["resolved_amount"]
    assert l2["adjusted_baseline"] == pytest.approx(l2["baseline"] + amount, abs=0.01)
    # Eq 11 re-summed, Eq 6 re-multiplied
    assert adj.savings == pytest.approx(ch.savings + amount, abs=0.05)
    assert adj.savings == pytest.approx(l1["savings"] + l2["savings"], abs=0.05)
    assert adj.enpi == pytest.approx(l1["enpi"] * l2["enpi"], abs=1e-5)
    assert adj.savings_pct == pytest.approx(1 - adj.enpi, abs=1e-4)
    # the true two-step saving without the new load: 10% then a further ~11%
    Tb = s["b"][1]
    true = 0.1 * _truth(Tb).sum() + (0.9 - 0.8) * _truth(Tr).sum()
    assert abs(adj.savings - true) < abs(ch.savings - true)
    assert abs(adj.savings - true) <= 2 * adj.abs_uncertainty  # ~3.3 sigma, one run
    # the band: the chain covariance plus the indicator in quadrature, at the smaller df
    u = ch.uncertainty_terms
    t = _t_value(ch.confidence, adj.df)
    want = t * np.sqrt(u["var_savings"] + adj.ledger[0]["resolved_se"] ** 2)
    assert adj.abs_uncertainty == pytest.approx(want, rel=1e-4)


def test_sep_chain_scales_the_covariance_with_a_forecast_link_factor():
    s = _site(8)
    mi, ch = _chain(s)
    u = ch.uncertainty_terms
    sf = StaticFactorAdjustment(factor="area", method="proportional", start="2020-01-01",
                                reason="wing", baseline_value=1.0, reporting_value=1.1,
                                affected_share=1.0)  # fmt: skip
    adj = apply_adjustments(ch, [sf], links=_chain_links(s, mi))
    c = 1.1
    var = (u["v_noise_baseline"] + u["v_param_baseline"] + c**2 * u["v_param_reporting"]
           + u["v_noise_reporting"] - 2 * c * u["covariance"])  # fmt: skip
    assert adj.abs_uncertainty == pytest.approx(_t_value(0.9, ch.df) * np.sqrt(var), rel=1e-4)
    assert adj.links[1]["adjusted_baseline"] == pytest.approx(
        1.1 * adj.links[1]["baseline"], abs=0.02
    )
    # a static change between the baseline and intermediate years restates the measured baseline
    early = StaticFactorAdjustment(factor="area", method="proportional", start="2018-12-31",
                                   reason="wing", baseline_value=1.0, reporting_value=1.1,
                                   affected_share=1.0)  # fmt: skip
    a1 = apply_adjustments(ch, [early], links=_chain_links(s, mi))
    assert a1.ledger[0]["link"] == "link 1"
    assert a1.links[0]["adjusted_baseline"] == pytest.approx(
        1.1 * a1.links[0]["baseline"], abs=0.02
    )


def test_sep_chain_refusals():
    s = _site(9)
    mi, ch = _chain(s)
    links = _chain_links(s, mi)
    mid = NonRoutineAdjustment(**{**_ENG, "start": "2019-06-01"})
    with pytest.raises(ValueError, match="intermediate period"):
        apply_adjustments(ch, [mid], links=links)
    ex = NonRoutineAdjustment(method="exclude", start="2020-03-01", end="2020-04-01", reason="x")
    with pytest.raises(ValueError, match="drop them from the chain's input rows"):
        apply_adjustments(ch, [ex], links=links)
    across = NonRoutineAdjustment(**{**_ENG, "start": "2018-06-01", "end": "2020-06-01"})
    with pytest.raises(ValueError, match="intermediate period"):
        apply_adjustments(ch, [across], links=links)
    # an engineering entry needs no rows: the links are dated from the chain's own periods
    ok = apply_adjustments(ch, [NonRoutineAdjustment(**_ENG)])
    assert ok.ledger[0]["link"] == "link 2"


# --------------------------------------------------------------------------- sequential chain


def test_sequential_chain_adjusts_each_link_independently():
    s = _site(10, event=(200, 30.0))
    res, rows = [], []
    for a, b in (("b", "i"), ("i", "r")):
        m, st = _fit(s[a])
        idx, T, y = s[b]
        res.append(forecast_savings(m, T, y, cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=5,
                                    rho=st.rho_lag1))  # fmt: skip
        rows.append({"index": idx, "drivers": T, "measured": y, "model": m})
    sq = sequential_chain(res)
    ir, Tr, yr = s["r"]
    nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting",
                                 model=rows[1]["model"])  # fmt: skip
    adj = apply_adjustments(sq, [nra], links=rows)
    assert adj.links[0]["entries"] == [] and adj.links[1]["entries"] == [0]
    assert adj.savings == pytest.approx(sq.savings + adj.ledger[0]["resolved_amount"], abs=0.05)
    assert adj.enpi == pytest.approx(adj.links[0]["enpi"] * adj.links[1]["enpi"], abs=1e-5)
    # B-19: the links' variances add, plus the entry's
    t = _t_value(0.9, adj.df)
    v = sum((r.abs_uncertainty / _t_value(0.9, r.df)) ** 2 for r in res)
    want = t * np.sqrt(v + adj.ledger[0]["resolved_se"] ** 2)
    assert adj.abs_uncertainty == pytest.approx(want, rel=1e-4)
    with pytest.raises(ValueError, match="each link's dates"):
        apply_adjustments(sq, [nra])
    nested = sequential_chain([sq, res[1]])
    with pytest.raises(ValueError, match="nested chain"):
        apply_adjustments(nested, [NonRoutineAdjustment(**_ENG)],
                          links=[{"window": ["2019-01-01", "2020-12-31"]}, rows[1]])  # fmt: skip


# --------------------------------------------------------------------------- one schedule


def test_one_ecm_schedule_and_one_validity():
    sch = EcmSchedule(("2020-05-01", "2019-03-01"))
    assert sch.settle_days == DEFAULT_SETTLE_DAYS == 14
    assert sch.ecm_dates == ("2019-03-01", "2020-05-01")
    assert sch.near("2020-05-10")[1] == 9 and sch.near("2020-06-10") is None
    assert EcmSchedule.from_dict(sch.as_dict()) == sch
    for bad in ({"settle_days": -1}, {"settle_days": 1.5}, {"ecm_dates": ["nope"]}, {"x": 1}):
        with pytest.raises(ValueError):
            EcmSchedule.from_dict(bad)
    assert check_validity("both") == "both"
    with pytest.raises(ValueError, match="validity must be one of"):
        check_validity("ashrae")
    # apply_adjustments takes the schedule directly, with the same guard as ecm_dates/settle_days
    s = _site(11)
    mb, fc, _ = _forecast(s)
    ir = s["r"][0]
    ind = NonRoutineAdjustment(method="indicator", start="2020-05-10", reason="r", rate=5.0,
                               rate_se=1.0)  # fmt: skip
    with pytest.raises(ValueError, match="settle window"):
        apply_adjustments(fc, [ind], index=ir, schedule=EcmSchedule(("2020-05-01",)))
    ok = apply_adjustments(fc, [ind], index=ir, schedule=EcmSchedule(("2020-05-01",), 5))
    assert ok.ledger[0]["rows_affected"] > 0


# --------------------------------------------------------------------------- Monte Carlo


def test_monte_carlo_coverage_of_the_adjusted_sep_chain():
    """Nominal 90%: the adjusted SEP chain band (shared-model covariance plus a reporting-period
    indicator) covers the true saving 85-95% of the time -- 200 seeded runs, about 6 s."""
    rng = np.random.default_rng(90)
    hits, reps = 0, 200
    for _ in range(reps):
        ib, Tb, yb = _year(rng, "2018-01-01")
        ii, Ti, yi = _year(rng, "2019-01-01", 0.9)
        ir, Tr, yr = _year(rng, "2020-01-01", 0.8)
        yr = yr.copy()
        yr[200:] += 30.0
        mi = fit_model(Ti, yi, "5P", time_index=ii)
        ch = chained_savings(mi, Tb, yb, Tr, yr, periods=YEARS)
        nra = estimate_nre_indicator(Tr, yr, ir, start=ir[200], fit_period="reporting", model=mi)
        links = [{"index": ib}, {"index": ir, "drivers": Tr, "measured": yr, "model": mi}]
        adj = apply_adjustments(ch, [nra], links=links)
        true = 0.1 * _truth(Tb).sum() + 0.1 * _truth(Tr).sum()
        hits += abs(adj.savings - true) <= adj.abs_uncertainty
    assert 0.85 <= hits / reps <= 0.95, hits / reps


# --------------------------------------------------------------------------- config order


def _cfg3(tmp_path, **extra):
    from test_mandv_methods import _mv_three_year_store

    cfg = _mv_three_year_store(tmp_path)
    cfg["mv"][0].update(extra)
    return cfg


def _mv(cfg):
    from camber.config import run_config

    (f,) = [f for f in run_config(cfg).findings if f.rule in ("mv_savings", "mv_method_proposal")]
    return f


_CFG_ENG = {"kind": "nra", "method": "engineering", "start": "2018-06-01", "reason": "rental",
            "amount": 5000.0, "se": 500.0, "evidence": "invoice"}  # fmt: skip
_INTER = ["2017-01-01", "2017-12-31"]


def test_config_applies_the_ledger_after_every_declared_method(tmp_path):
    for method, extra in (
        ("forecast", {}),
        ("backcast", {}),
        ("chaining", {"intermediate_period": _INTER}),
        ("standard_conditions", {"normal_year": list(np.linspace(30, 85, 365))}),
    ):
        f = _mv(_cfg3(tmp_path / method, method=method, adjustments=[_CFG_ENG], **extra))
        m = f.metrics
        unadj = m.get("savings", m.get("avoided_energy"))
        assert m["method"] == method and m["adjusted"] is True, (
            method,
            m.get("adjustments_refused"),
        )
        assert m["adjusted_savings"] == pytest.approx(unadj + 5000.0, abs=0.05), method
        assert m["adjusted_enpi"] is not None and m["validity"] == "g14"
        assert "adjusted for 1 non-routine/static entry" in f.summary
        if method == "chaining":
            assert [ln["entries"] for ln in m["adjusted_links"]] == [[], [0]]
        json.dumps(m, allow_nan=False)


def test_config_chain_indicator_and_refusals(tmp_path):
    ind = {"kind": "nra", "method": "indicator", "start": "2018-07-01", "reason": "new load"}
    f = _mv(_cfg3(tmp_path / "a", method="chaining", intermediate_period=_INTER,
                  adjustments=[ind]))  # fmt: skip
    m = f.metrics
    assert m["adjusted"] is True and m["adjustments"][0]["fit_period"] == "reporting"
    assert m["adjustments"][0]["link"] == "link 2"
    mid = {**_CFG_ENG, "start": "2017-06-01"}
    f = _mv(_cfg3(tmp_path / "b", method="chaining", intermediate_period=_INTER,
                  adjustments=[mid]))  # fmt: skip
    assert (
        f.metrics["adjusted"] is None and "intermediate period" in f.metrics["adjustments_refused"]
    )
    assert f.metrics["savings"] is not None  # the unadjusted saving stands


def test_config_auto_shows_adjusted_and_unadjusted_sensitivity(tmp_path):
    plain = _mv(_cfg3(tmp_path / "a", method="auto"))
    f = _mv(_cfg3(tmp_path / "b", method="auto", adjustments=[_CFG_ENG]))
    assert f.rule == "mv_method_proposal" and f.metrics["proposed"] == plain.metrics["proposed"]
    assert "adjusted_savings" not in f.metrics and "savings" not in f.metrics
    rows = f.metrics["sensitivity"]
    assert rows and len(rows) == len(plain.metrics["sensitivity"])
    for row, base in zip(rows, plain.metrics["sensitivity"]):
        assert row["savings"] == base["savings"]
        assert row["adjusted_savings"] == pytest.approx(row["savings"] + 5000.0, abs=0.05)
        assert row["adjusted_enpi"] is not None
    assert any("before and after the declared adjustments" in c for c in f.caveats)
    json.dumps(f.metrics, allow_nan=False, default=str)


def test_config_one_validity_key(tmp_path):
    f = _mv(_cfg3(tmp_path / "a", method="chaining", intermediate_period=_INTER, validity="both"))
    m = f.metrics
    assert m["validity"] == "both" and set(m["sep_validity"]) == {"intermediate"}
    assert isinstance(m["sep_valid"], bool)
    # the same key gates the adjustments' SEP evidence rule
    eng = {k: v for k, v in _CFG_ENG.items()}
    f = _mv(_cfg3(tmp_path / "b", validity="sep", adjustments=[eng]))
    assert "approved_by" in f.metrics["adjustments_refused"]
    f = _mv(_cfg3(tmp_path / "c", validity="sep", adjustments=[{**eng, "approved_by": "VB"}]))
    assert f.metrics["adjusted"] is True and "sep_valid" in f.metrics
    from camber.config import run_config

    for bad, match in (
        ({"validity": "ashrae"}, "validity must be one of"),
        ({"ecm_dates": "2018-01-01"}, "ecm_dates must be a list"),
        ({"ecm_dates": ["soon"]}, "ecm_dates must be dates"),
        ({"settle_days": -3}, "settle_days must be >= 0"),
    ):
        with pytest.raises(ValueError, match=match):
            run_config(_cfg3(tmp_path / "d", **bad))
