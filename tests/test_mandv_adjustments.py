"""Non-routine and static-factor adjustments (#21 phase 21c; camber.mandv.adjustments).

Synthetic daily cooling meters with AR(1) residuals and planted events of known size: the
indicator, engineering, exclude and submeter NRAs and the proportional static factor recover them,
the indicator's band covers its true effect at the nominal rate, the confounding guard and the SEP
evidence rule refuse, and detection only ever proposes.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.adjustments import (  # noqa: E402
    AdjustedResult,
    ConfoundedAdjustment,
    NonRoutineAdjustment,
    StaticFactorAdjustment,
    adjustment_from_dict,
    apply_adjustments,
    estimate_nre_indicator,
    is_material,
    nra_from_isolation,
    propose_adjustments,
)
from camber.mandv.methods import backcast_savings  # noqa: E402
from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.nonroutine import detect_step_changes  # noqa: E402
from camber.mandv.retrofit_isolation import isolation_savings  # noqa: E402
from camber.mandv.stats import _t_value, avoided_energy_savings, fit_stats  # noqa: E402


def _series(rng, start, days=365, rho=0.4, sd=8.0):
    idx = pd.date_range(start, periods=days, freq="D")
    d = np.arange(days)
    T = 60 + 20 * np.sin(2 * np.pi * (d - 100) / 365) + rng.normal(0, 4, days)
    e = np.empty(days)
    e[0] = rng.normal(0, sd)
    inn = rng.normal(0, sd * np.sqrt(1 - rho**2), days)
    for i in range(1, days):
        e[i] = rho * e[i - 1] + inn[i]
    return idx, T, e


def _truth(T):
    return 400 + 6 * np.maximum(0, T - 65)


def _forecast(model, Tb, yb, ib, Tr, yr, **kw):
    st = fit_stats(yb, model.predict(Tb), 3, time_index=ib)
    return avoided_energy_savings(
        model, Tr, yr, cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=3, rho=st.rho_lag1, **kw
    )


@pytest.fixture(scope="module")
def site():
    """A baseline year, and a reporting year with a 10% saving; noise shared by construction."""
    rng = np.random.default_rng(7)
    ib, Tb, eb = _series(rng, "2023-01-01")
    ir, Tr, er = _series(rng, "2024-01-01")
    yb = _truth(Tb) + eb
    yr = 0.9 * _truth(Tr) + er
    m = fit_model(Tb, yb, "3PC", time_index=ib)
    return dict(ib=ib, Tb=Tb, yb=yb, ir=ir, Tr=Tr, yr=yr, er=er, m=m)


# --------------------------------------------------------------------------- ledger entries


def test_ledger_entries_are_explicit_and_validated():
    with pytest.raises(ValueError, match="evidence"):
        NonRoutineAdjustment(method="engineering", start="2024-03-01", reason="x", amount=1, se=1)
    with pytest.raises(ValueError, match="amount and se"):
        NonRoutineAdjustment(method="submeter", start="2024-03-01", reason="x")
    with pytest.raises(ValueError, match="rate"):
        NonRoutineAdjustment(method="indicator", start="2024-03-01", reason="x")
    with pytest.raises(ValueError, match="unknown NRA method"):
        NonRoutineAdjustment(method="guess", start="2024-03-01", reason="x")
    with pytest.raises(ValueError, match="not after"):
        NonRoutineAdjustment(method="exclude", start="2024-03-01", end="2024-02-01", reason="x")
    # D8: no default affected share -- it must be stated
    with pytest.raises(ValueError, match="explicit affected_share"):
        StaticFactorAdjustment(
            factor="floor area",
            method="proportional",
            start="2024-01-01",
            reason="new wing",
            baseline_value=100,
            reporting_value=120,
        )
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        StaticFactorAdjustment(
            factor="a", method="proportional", start="2024-01-01", reason="r",
            baseline_value=1, reporting_value=2, affected_share=1.5,
        )  # fmt: skip
    with pytest.raises(ValueError, match="evidence"):
        StaticFactorAdjustment(
            factor="a", method="engineering", start="2024-01-01", reason="r", amount=1, se=0
        )
    sf = StaticFactorAdjustment(
        factor="floor area", method="proportional", start="2024-01-01", reason="new wing",
        baseline_value=100, reporting_value=120, affected_share=0.5,
    )  # fmt: skip
    assert sf.ratio == pytest.approx(1.2) and sf.multiplier == pytest.approx(1.1)
    # round trip through the config / JSON dict form
    assert adjustment_from_dict(sf.as_dict()) == sf
    e = NonRoutineAdjustment(
        method="engineering", start="2024-03-01", reason="kiln", amount=-500, se=50,
        evidence="nameplate",
    )  # fmt: skip
    assert adjustment_from_dict(json.loads(json.dumps(e.as_dict()))) == e
    with pytest.raises(ValueError, match="unknown nra adjustment key"):
        adjustment_from_dict({"kind": "nra", "method": "exclude", "start": "2024-1-1", "x": 1})
    with pytest.raises(ValueError, match="kind"):
        adjustment_from_dict({"kind": "other"})


def test_materiality_rule():
    assert is_material(100, 40)  # 100 >= max(0, 80)
    assert not is_material(100, 60)  # 100 < 120
    assert not is_material(100, 10, threshold=150)
    assert is_material(-200, 10, threshold=150)
    assert is_material(5, None) and not is_material(float("nan"), 1)


# --------------------------------------------------------------------------- recovery


def test_reporting_period_indicator_recovers_a_known_event(site):
    s = site
    yr = s["yr"].copy()
    yr[180:] -= 50.0  # a partial shutdown from day 180: 185 days x -50
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], yr)
    nra = estimate_nre_indicator(
        s["Tr"], yr, s["ir"], start=s["ir"][180], fit_period="reporting", model=s["m"],
        reason="partial shutdown",
    )  # fmt: skip
    assert nra.rate == pytest.approx(-50, abs=3 * nra.rate_se)
    assert nra.fit.p == 3 + 1 and nra.fit.df == 365 - 4  # +1 to p for the indicator
    adj = apply_adjustments(sav, [nra], index=s["ir"], drivers=s["Tr"], measured=yr, model=s["m"])
    assert isinstance(adj, AdjustedResult)
    true_saving = (0.1 * _truth(s["Tr"])).sum()
    assert sav.avoided_energy - true_saving == pytest.approx(185 * 50, rel=0.1)
    assert abs(adj.savings - true_saving) <= adj.abs_uncertainty
    # quadrature: the independent reporting-period estimate widens the band
    t = _t_value(0.9, None)
    se0 = sav.abs_uncertainty / t
    expect = _t_value(0.9, nra.fit.df) * np.hypot(se0, 185 * nra.rate_se)
    assert adj.abs_uncertainty == pytest.approx(expect, rel=1e-3)
    (entry,) = adj.ledger
    assert entry["material"] is True and entry["rows_affected"] == 185
    assert entry["resolved_amount"] == pytest.approx(185 * nra.rate)
    assert any("IPMVP 2012 §8.2" in c for c in adj.caveats)
    # the waterfall closes: baseline + adjustments - savings = reporting
    wf = adj.waterfall
    assert wf[0].label == "baseline projection" and wf[-1].label == "reporting actual"
    assert sum(w.value for w in wf if w.kind == "delta") + wf[0].value == pytest.approx(
        wf[-1].value, abs=0.05
    )
    json.dumps(adj.as_dict(), allow_nan=False)


def test_baseline_period_indicator_uses_the_joint_covariance(site):
    s = site
    yb = s["yb"].copy()
    yb[200:260] -= 80.0  # a summer closure inside the baseline
    m = fit_model(s["Tb"], yb, "3PC", time_index=s["ib"])  # fitted through the closure
    sav = _forecast(m, s["Tb"], yb, s["ib"], s["Tr"], s["yr"])
    nra = estimate_nre_indicator(
        s["Tb"], yb, s["ib"], start=s["ib"][200], end=s["ib"][260], fit_period="baseline",
        model=m, reason="closure",
    )  # fmt: skip
    assert nra.rate == pytest.approx(-80, abs=3 * nra.rate_se)
    adj = apply_adjustments(sav, [nra], index=s["ir"], drivers=s["Tr"], measured=s["yr"], model=m)
    true_saving = (0.1 * _truth(s["Tr"])).sum()
    # the contaminated baseline under-projects; the refit recovers the saving
    assert abs(sav.avoided_energy - true_saving) > 3 * abs(adj.savings - true_saving)
    assert abs(adj.savings - true_saving) <= 2 * adj.abs_uncertainty
    assert adj.kernel == "exact" and adj.df == 365 - 4
    # the event ended inside the baseline: the indicator is 0 on every reporting row
    assert adj.ledger[0]["rows_affected"] == 0
    assert [w.label for w in adj.waterfall][:2] == [
        "baseline projection",
        "baseline refit with indicator",
    ]
    # joint Sigma: exactly g'Sigma g + kappa s2 m of the augmented fit
    from camber.mandv.models import _design_for

    f = nra.fit
    g = np.append(_design_for("3PC", f.design[2])(s["Tr"]).sum(axis=0), 0.0)
    var = g @ np.asarray(f.sigma) @ g + f.kappa * f.s2 * 365
    assert adj.abs_uncertainty == pytest.approx(_t_value(0.9, f.df) * np.sqrt(var), rel=1e-3)
    # a persistent event (no end) sets the indicator on every reporting row
    yb2 = s["yb"].copy()
    yb2[300:] -= 40.0
    nra2 = estimate_nre_indicator(
        s["Tb"], yb2, s["ib"], start=s["ib"][300], fit_period="baseline", reason="tenant left"
    )
    adj2 = apply_adjustments(sav, [nra2], index=s["ir"], drivers=s["Tr"])
    assert adj2.ledger[0]["rows_affected"] == 365
    assert adj2.ledger[0]["resolved_amount"] == pytest.approx(365 * nra2.rate)


def test_engineering_and_submeter_add_in_quadrature(site):
    s = site
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], s["yr"])
    eng = NonRoutineAdjustment(
        method="engineering", start="2024-06-01", reason="server room", amount=3000.0, se=400.0,
        evidence="load study",
    )  # fmt: skip
    sub = NonRoutineAdjustment(
        method="submeter", start="2024-06-01", reason="EV chargers", amount=1200.0, se=30.0,
        evidence="sub-meter",
    )  # fmt: skip
    adj = apply_adjustments(sav, [eng, sub], baseline_actual=float(s["yb"].sum()))
    assert adj.adjusted_baseline == pytest.approx(sav.baseline_projected + 4200.0, abs=0.01)
    assert adj.savings == pytest.approx(sav.avoided_energy + 4200.0, abs=0.01)
    t = _t_value(0.9, None)
    expect = t * np.sqrt((sav.abs_uncertainty / t) ** 2 + 400.0**2 + 30.0**2)
    assert adj.abs_uncertainty == pytest.approx(expect, rel=1e-4)
    assert adj.waterfall[0].label == "baseline period actual"
    assert adj.waterfall[1].label == "routine adjustment"
    # not meter-derived: no IPMVP §8.2 caveat, and the ECM guard does not apply
    assert not any("§8.2" in c for c in adj.caveats)
    apply_adjustments(sav, [eng], ecm_dates=["2024-06-01"])


def test_submeter_nra_from_an_option_b_isolation():
    rng = np.random.default_rng(4)
    hrs_b, hrs_r = rng.uniform(8, 16, 90), rng.uniform(8, 16, 90)
    kwh_b = 5 + 20 * hrs_b + rng.normal(0, 4, 90)
    kwh_r = 5 + 26 * hrs_r + rng.normal(0, 4, 90)  # the system now draws 30% more per hour
    iso = isolation_savings(kwh_b, kwh_r, baseline_driver=hrs_b, reporting_driver=hrs_r,
                            boundary="kitchen hood fans")  # fmt: skip
    nra = nra_from_isolation(iso, start="2024-04-01", reason="hood fans re-balanced")
    assert nra.method == "submeter" and not nra.meter_derived
    assert nra.amount == pytest.approx(-iso.savings) and nra.amount > 0
    assert nra.se == pytest.approx(iso.abs_uncertainty / _t_value(0.9, 88))
    assert "kitchen hood fans" in nra.evidence


def test_exclude_drops_the_span_from_both_sides(site):
    s = site
    yr = s["yr"].copy()
    yr[100:130] = 50.0  # an anomaly: meter reading collapsed for a month
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], yr)
    ex = NonRoutineAdjustment(
        method="exclude", start=s["ir"][100], end=s["ir"][130], reason="meter fault"
    )
    adj = apply_adjustments(sav, [ex], index=s["ir"], drivers=s["Tr"], measured=yr, model=s["m"])
    keep = np.ones(365, bool)
    keep[100:130] = False
    direct = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"][keep], yr[keep])
    assert adj.savings == pytest.approx(direct.avoided_energy, abs=0.05)
    assert adj.adjusted_reporting == pytest.approx(direct.reporting_actual, abs=0.05)
    assert adj.n_rows == 335
    # the G14 band rescales exactly as the kernel does (P^2 / m)
    assert adj.abs_uncertainty == pytest.approx(direct.abs_uncertainty, rel=1e-3)
    assert any("30 of 365 rows excluded" in c for c in adj.caveats)
    assert adj.ledger[0]["rows_excluded"] == 30 and adj.ledger[0]["material"] is None
    # the exact kernel recomputes on the kept rows
    sx = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], yr, kernel="exact")
    ax = apply_adjustments(sx, [ex], index=s["ir"], drivers=s["Tr"], measured=yr, model=s["m"])
    dx = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"][keep], yr[keep], kernel="exact")
    assert ax.abs_uncertainty == pytest.approx(dx.abs_uncertainty, rel=1e-3)
    with pytest.raises(ValueError, match="needs"):
        apply_adjustments(sav, [ex], index=s["ir"])


def test_proportional_static_factor_recovers_a_floor_area_change(site):
    s = site
    # half the load scales with floor area, which grew 20% at the start of the reporting year
    yr = 0.9 * _truth(s["Tr"]) * 1.1 + s["er"]
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], yr)
    sf = StaticFactorAdjustment(
        factor="floor area", method="proportional", start="2024-01-01", reason="new wing",
        baseline_value=10000, reporting_value=12000, affected_share=0.5, ratio_se=0.01,
    )  # fmt: skip
    adj = apply_adjustments(sav, [sf], index=s["ir"], drivers=s["Tr"], measured=yr, model=s["m"])
    true_saving = (0.1 * 1.1 * _truth(s["Tr"])).sum()
    assert sav.avoided_energy < 0.1 * true_saving  # unadjusted, the new wing hides the saving
    assert abs(adj.savings - true_saving) <= adj.abs_uncertainty
    assert adj.adjusted_baseline == pytest.approx(1.1 * sav.baseline_projected, rel=1e-6)
    # correlated with the projection: the SE scales by the multiplier, plus the ratio's SE term
    t = _t_value(0.9, None)
    v = (1.1 * sav.abs_uncertainty / t) ** 2 + (sav.baseline_projected * 0.5 * 0.01) ** 2
    assert adj.abs_uncertainty == pytest.approx(t * np.sqrt(v), rel=1e-4)
    # a change mid-period only scales the rows after it
    mid = StaticFactorAdjustment(
        factor="floor area", method="proportional", start="2024-07-01", reason="new wing",
        baseline_value=10000, reporting_value=12000, affected_share=0.5,
    )  # fmt: skip
    a2 = apply_adjustments(sav, [mid], index=s["ir"], drivers=s["Tr"], model=s["m"])
    after = np.asarray(s["ir"] >= "2024-07-01")
    p_after = s["m"].predict(s["Tr"])[after].sum()
    assert a2.adjusted_baseline - sav.baseline_projected == pytest.approx(0.1 * p_after, rel=1e-4)
    a3 = apply_adjustments(sav, [mid])
    assert any("applied to the whole period" in c for c in a3.caveats)
    eng = StaticFactorAdjustment(
        factor="shifts", method="engineering", start="2024-01-01", reason="second shift",
        amount=5000.0, se=500.0, evidence="production schedule",
    )  # fmt: skip
    a4 = apply_adjustments(sav, [eng])
    assert a4.savings == pytest.approx(sav.avoided_energy + 5000.0, abs=0.01)


def test_backcast_results_restate_the_measured_baseline(site):
    s = site
    mr = fit_model(s["Tr"], s["yr"], "3PC", time_index=s["ir"])
    st = fit_stats(s["yr"], mr.predict(s["Tr"]), 3)
    bc = backcast_savings(mr, s["Tb"], s["yb"], cv_rmse=st.cv_rmse, n_reporting=365,
                          p_reporting=3)  # fmt: skip
    eng = NonRoutineAdjustment(
        method="engineering", start="2024-01-01", reason="r", amount=1000.0, se=100.0,
        evidence="e",
    )  # fmt: skip
    adj = apply_adjustments(bc, [eng])
    assert adj.method == "backcast" and adj.baseline == pytest.approx(bc.measured)
    assert adj.savings == pytest.approx(bc.savings + 1000.0, abs=0.01)
    assert adj.waterfall[0].label == "baseline actual"
    # a proportional factor scales the measured side, which carries no model variance
    sf = StaticFactorAdjustment(
        factor="a", method="proportional", start="2023-01-01", reason="r",
        baseline_value=1, reporting_value=1.1, affected_share=1.0,
    )  # fmt: skip
    a2 = apply_adjustments(bc, [sf])
    assert a2.abs_uncertainty == pytest.approx(bc.abs_uncertainty, rel=1e-3)
    with pytest.raises(ValueError, match="declined"):
        declined = backcast_savings(mr, np.full(50, 150.0), np.full(50, 500.0), cv_rmse=0.1,
                                    n_reporting=365, p_reporting=3)  # fmt: skip
        apply_adjustments(declined, [eng])


# --------------------------------------------------------------------------- guards


def test_confounding_guard_refuses_a_meter_derived_nra_near_an_ecm(site):
    s = site
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], s["yr"])
    nra = NonRoutineAdjustment(
        method="indicator", start="2024-05-10", reason="step", rate=-30.0, rate_se=2.0
    )
    with pytest.raises(ConfoundedAdjustment, match="settle window"):
        apply_adjustments(sav, [nra], index=s["ir"], ecm_dates=["2024-05-01"])
    ex = NonRoutineAdjustment(method="exclude", start="2024-03-01", end="2024-04-20", reason="x")
    with pytest.raises(ConfoundedAdjustment):  # the end is near the ECM too
        apply_adjustments(sav, [ex], ecm_dates=["2024-04-25"])
    # outside the window, or a wider settle window, decides it
    ok = apply_adjustments(sav, [nra], index=s["ir"], ecm_dates=["2024-04-01"])
    assert ok.ledger[0]["rows_affected"] == len(s["ir"][s["ir"] >= "2024-05-10"])
    with pytest.raises(ConfoundedAdjustment):
        apply_adjustments(sav, [nra], index=s["ir"], ecm_dates=["2024-04-01"], settle_days=60)


def test_sep_validity_requires_evidence_and_approval(site):
    s = site
    sav = _forecast(s["m"], s["Tb"], s["yb"], s["ib"], s["Tr"], s["yr"])
    eng = NonRoutineAdjustment(
        method="engineering", start="2024-06-01", reason="r", amount=10.0, se=1.0, evidence="e"
    )
    apply_adjustments(sav, [eng])  # g14: fine
    with pytest.raises(ValueError, match="approved_by"):
        apply_adjustments(sav, [eng], validity="sep")
    ok = apply_adjustments(sav, [eng.accept(approved_by="verifier")], validity="sep")
    assert ok.ledger[0]["approved_by"] == "verifier"
    with pytest.raises(ValueError, match="validity"):
        apply_adjustments(sav, [eng], validity="nope")


def test_detection_only_proposes():
    rng = np.random.default_rng(0)
    idx, T, e = _series(rng, "2023-01-01", days=730, rho=0.5)
    y = _truth(T) + e
    y[400:] -= 60.0
    steps = detect_step_changes(pd.Series(y, index=idx), pd.Series(T, index=idx))
    (p,) = propose_adjustments(steps, ecm_dates=[str(idx[405].date())], threshold=10.0)
    assert p.status == "proposed" and p.method == "indicator" and p.material
    assert p.rate == pytest.approx(-60, abs=5)
    assert any("likely the measure" in c for c in p.caveats)
    rng2 = np.random.default_rng(1)
    ir, Tr, er = _series(rng2, "2024-01-01")
    m = fit_model(T[:365], y[:365], "3PC")
    sav = avoided_energy_savings(m, Tr, _truth(Tr) + er, cv_rmse=0.02, n_baseline=365,
                                 p_baseline=3)  # fmt: skip
    with pytest.raises(ValueError, match="only proposed"):
        apply_adjustments(sav, [p], index=ir)
    acc = p.accept(approved_by="analyst", evidence="site log: tenant moved out")
    assert acc.status == "accepted" and acc.evidence.startswith("site log")
    with pytest.raises(ValueError, match="approved_by"):
        p.accept(approved_by="")
    (q,) = propose_adjustments(steps)
    assert not any("likely the measure" in c for c in q.caveats)


# --------------------------------------------------------------------------- Monte Carlo


def _indicator_coverage(rho, reps, seed, fit_period):
    """Share of seeded runs whose 90% band on the indicator covers the planted effect."""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        idx, T, e = _series(rng, "2023-01-01", rho=rho)
        y = _truth(T) + e
        if fit_period == "reporting":
            y = 0.9 * y
            y[180:] -= 50.0
            true, start, end = -50.0, idx[180], None
        else:
            y[200:260] -= 80.0
            true, start, end = -80.0, idx[200], idx[260]
        m = fit_model(T, y, "3PC")
        a = estimate_nre_indicator(T, y, idx, start=start, end=end, fit_period=fit_period, model=m)
        hits += abs(a.rate - true) <= _t_value(0.9, a.fit.df) * a.rate_se
    return hits / reps


# The rho = 0.8 case alone is gated at 80-95%, not 85-95%: a one-year lag-1 estimate of rho is
# biased low, so kappa under-corrects the band there. Every other coverage gate stays 85-95%.
@pytest.mark.parametrize("rho,seed,lo", [(0.0, 11, 0.85), (0.4, 12, 0.85), (0.8, 13, 0.80)])
@pytest.mark.parametrize("fit_period", ["reporting", "baseline"])
def test_monte_carlo_coverage_of_the_indicator_band(rho, seed, lo, fit_period):
    """Nominal 90%: the indicator's rho-inflated band covers the true effect 85-95% of the time
    at rho 0 and 0.4 (change points re-searched, rho estimated) -- about 1.5 s per case. At
    rho = 0.8 it covers about 85%: the lag-1 estimate is biased low in a one-year fit, so kappa
    under-corrects (documented in MANDV.md); the gate there is 80-95%."""
    rate = _indicator_coverage(rho, reps=600, seed=seed, fit_period=fit_period)
    assert lo <= rate <= 0.95, rate


def test_monte_carlo_coverage_of_the_adjusted_saving():
    """The adjusted saving's band (joint Sigma of a baseline refit) covers the true saving at the
    nominal rate when the event does not remove the data that identify the slope."""
    rng = np.random.default_rng(21)
    hits, reps = 0, 300
    for _ in range(reps):
        ib, Tb, eb = _series(rng, "2023-01-01", rho=0.0)
        ir, Tr, er = _series(rng, "2024-01-01", rho=0.0)
        yb = _truth(Tb) + eb
        yb[20:80] -= 80.0  # a winter closure
        yr = 0.9 * _truth(Tr) + er
        m = fit_model(Tb, yb, "3PC", time_index=ib)
        sav = _forecast(m, Tb, yb, ib, Tr, yr)
        nra = estimate_nre_indicator(
            Tb, yb, ib, start=ib[20], end=ib[80], fit_period="baseline", model=m
        )
        adj = apply_adjustments(sav, [nra], index=ir, drivers=Tr)
        hits += abs(adj.savings - (0.1 * _truth(Tr)).sum()) <= adj.abs_uncertainty
    assert 0.85 <= hits / reps <= 0.95, hits / reps
