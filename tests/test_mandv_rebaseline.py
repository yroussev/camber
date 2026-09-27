"""Versioned M&V baselines, the rebaseline policy and ``camber mv`` (#21 phase 21d, #48).

The write policy is under test as much as the numbers: ``run`` / ``list`` / ``propose`` /
``report`` never touch the store, the three writers are dry runs by default, need ``--reason``,
take the workspace lock and are audited, and nothing ever rebaselines by itself.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.mandv.adjustments import (  # noqa: E402
    AdjustedResult,
    EcmSchedule,
    IndicatorFit,
    NonRoutineAdjustment,
    StaticFactorAdjustment,
    adjustment_from_dict,
    apply_adjustments,
    estimate_nre_indicator,
)
from camber.mandv.methods import forecast_savings, select_method, sequential_chain  # noqa: E402
from camber.mandv.models import N_PARAMS, best_model  # noqa: E402
from camber.mandv.rebaseline import (  # noqa: E402
    MV_SCHEMA,
    DeclaredChange,
    MVBaselineStore,
    RebaselinePolicy,
    StaticFactorChange,
    assess_triggers,
    first_block,
    fit_frame_sha256,
    mv_kind,
    mv_model_from_dict,
    mv_provenance,
    new_baseline_window,
    propose_rebaseline,
    version_label,
    version_segments,
)
from camber.mandv.stats import fit_stats  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import DEFAULT_POLICY, Portfolio  # noqa: E402
from camber.store.modelstore import BaselineRecord, BaselineStore  # noqa: E402

ECM = "2017-01-01"
STEP = "2018-02-01"


def _daily(end="2019-06-30", *, step=True, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-01", end, freq="D")
    doy = idx.dayofyear.to_numpy()
    oat = 55 - 25 * np.cos((doy - 15) / 365 * 2 * np.pi) + rng.normal(0, 4, len(idx))
    e = 500 + 30 * np.maximum(0, oat - 60) + rng.normal(0, 20, len(idx))
    e = np.where(idx >= ECM, e * 0.9, e)
    if step:
        e = np.where(idx >= STEP, e * 0.7, e)
    return pd.DataFrame({"oat": oat, "energy": e}, index=idx)


def _fit(daily, win):
    sub = daily.loc[win[0] : win[1]]
    m = best_model(sub.oat.values, sub.energy.values, time_index=sub.index)
    st = fit_stats(
        sub.energy.values, m.predict(sub.oat.values), N_PARAMS[m.kind], time_index=sub.index
    )
    return m, st, sub


# --------------------------------------------------------------------------- store


def test_baseline_record_provenance_is_trailing_and_drift_files_unchanged(tmp_path):
    rec = BaselineRecord("fp", "s", "e", "k", {}, "t")
    assert rec.provenance == {} and "provenance" not in rec.as_dict()
    rec2 = BaselineRecord.from_dict({**rec.as_dict(), "provenance": {"reason": "x"}})
    assert rec2.as_dict()["provenance"] == {"reason": "x"}
    st = BaselineStore(str(tmp_path / "b.json"))
    assert st.LIST_KEY == "baselines" and st.SCHEMA is None
    st.save()
    assert json.load(open(tmp_path / "b.json")) == {"baselines": []}  # no schema key


def test_mv_store_keeps_every_version_with_provenance(tmp_path):
    daily = _daily()
    m1, st1, sub1 = _fit(daily, ["2016-01-01", "2016-12-31"])
    path = str(tmp_path / "mv.json")
    store = MVBaselineStore(path, facility_id="f1")
    prov = mv_provenance(
        m1,
        sub1,
        reason="initial",
        accepted_by="ana",
        method="forecast",
        kernel="g14",
        fit_stats=st1,
    )
    for k in ("os_user", "host", "fit_frame_sha256", "camber_version", "data_window", "model"):
        assert prov[k]
    kind = mv_kind(Role.POWER)
    assert kind == "mv_power"
    r1 = store.freeze_version(
        m1,
        site="S",
        equip="meter",
        kind=kind,
        frozen_at="t1",
        period=["2016-01-01", "2016-12-31"],
        provenance=prov,
    )
    assert version_label(r1) == "v1" and MVBaselineStore.verify(r1)
    with pytest.raises(ValueError, match="already frozen"):
        store.freeze_version(
            m1,
            site="S",
            equip="meter",
            kind=kind,
            frozen_at="t2",
            period=["2016-01-01", "2016-12-31"],
            provenance=prov,
        )
    m2, st2, sub2 = _fit(daily, ["2018-03-01", "2019-02-28"])
    with pytest.raises(ValueError, match="must start after"):
        store.rebaseline(
            m2,
            site="S",
            equip="meter",
            kind=kind,
            at="t2",
            period=["2016-06-01", "2017-05-31"],
            provenance=prov,
        )
    prov2 = mv_provenance(
        m2,
        sub2,
        reason="step",
        accepted_by="ana",
        method="forecast",
        kernel="g14",
        trigger_ids=["T1:2018-02-01"],
        trigger_date=STEP,
    )
    r2 = store.rebaseline(
        m2,
        site="S",
        equip="meter",
        kind=kind,
        at="t2",
        period=["2018-03-01", "2019-02-28"],
        provenance=prov2,
    )
    assert version_label(r2) == "v2" and r2.provenance["supersedes_version"] == "v1"
    store.save()
    doc = json.load(open(path))
    assert doc["schema"] == MV_SCHEMA and len(doc["mv_baselines"]) == 1
    back = MVBaselineStore.load(path, facility_id="f1")
    vs = back.versions("S", "meter", kind)
    assert [version_label(v) for v in vs] == ["v1", "v2"]
    assert all(MVBaselineStore.verify(v) for v in vs)
    assert back.in_force("S", "meter", kind, "2017-06-01").provenance["version"] == 1
    assert back.in_force("S", "meter", kind, "2019-06-01").provenance["version"] == 2
    assert back.version("S", "meter", kind, 1).period_end == "2016-12-31"
    # the rebuilt model predicts exactly as the fitted one
    T = daily.oat.values[:50]
    assert np.allclose(back.model_of(vs[0]).predict(T), m1.predict(T))
    # tampering with the provenance is detectable
    vs[1].provenance["reason"] = "edited"
    assert not MVBaselineStore.verify(vs[1])


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "mv.json"
    p.write_text(json.dumps({"schema": MV_SCHEMA + 1, "mv_baselines": []}))
    with pytest.raises(ValueError, match="upgrade CAMBER"):
        MVBaselineStore.load(str(p))


def test_mv_model_dispatch_rejects_unknown_types():
    with pytest.raises(KeyError, match="unknown M&V model type"):
        mv_model_from_dict({"type": "Nope"})


def test_ledger_round_trips_losslessly_with_the_indicator_fit():
    daily = _daily(step=False)
    sub = daily.loc["2016-01-01":"2016-12-31"]
    m, _st, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    ind = estimate_nre_indicator(
        sub.oat.values,
        sub.energy.values,
        sub.index,
        start="2016-07-01",
        end="2016-08-01",
        fit_period="baseline",
        model=m,
        reason="test",
        evidence="log",
        approved_by="ana",
    )
    assert isinstance(ind.fit, IndicatorFit)
    d = json.loads(json.dumps(ind.as_dict()))
    back = adjustment_from_dict(d)
    assert back == ind and back.fit == ind.fit
    assert IndicatorFit.from_dict(ind.fit.as_dict()) == ind.fit
    with pytest.raises(ValueError, match="unknown indicator-fit key"):
        IndicatorFit.from_dict({**ind.fit.as_dict(), "bogus": 1})
    sf = StaticFactorAdjustment(
        "floor_area",
        "proportional",
        "2017-01-01",
        "wing added",
        baseline_value=100.0,
        reporting_value=120.0,
        affected_share=0.5,
    )
    assert adjustment_from_dict(json.loads(json.dumps(sf.as_dict()))) == sf


def test_adjusted_result_and_method_result_carry_the_baseline_version():
    daily = _daily(step=False)
    m, st, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    rep = daily.loc["2017-02-01":"2017-12-31"]
    res = forecast_savings(
        m,
        rep.oat.values,
        rep.energy.values,
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=N_PARAMS[m.kind],
        rho=st.rho_lag1,
        baseline_version="v1",
    )
    assert res.baseline_version == "v1"
    eng = NonRoutineAdjustment(
        "engineering", "2017-06-01", "rental", amount=100.0, se=10.0, evidence="invoice"
    )
    adj = apply_adjustments(res, [eng])
    assert isinstance(adj, AdjustedResult) and adj.baseline_version == "v1"
    assert adj.as_dict()["baseline_version"] == "v1"


def test_sequential_chain_links_are_dated_from_the_store_windows():
    daily = _daily()
    m1, st1, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    m2, st2, _ = _fit(daily, ["2018-02-15", "2019-02-14"])
    r1 = daily.loc["2017-01-15":"2018-01-31"]
    r2 = daily.loc["2019-02-15":"2019-06-30"]
    f1 = forecast_savings(
        m1,
        r1.oat.values,
        r1.energy.values,
        cv_rmse=st1.cv_rmse,
        n_baseline=st1.n,
        p_baseline=N_PARAMS[m1.kind],
        baseline_version="v1",
    )
    f2 = forecast_savings(
        m2,
        r2.oat.values,
        r2.energy.values,
        cv_rmse=st2.cv_rmse,
        n_baseline=st2.n,
        p_baseline=N_PARAMS[m2.kind],
        baseline_version="v2",
    )
    wins = [
        {"period": ["2017-01-15", "2018-01-31"], "model_window": ["2016-01-01", "2016-12-31"]},
        {"period": ["2019-02-15", "2019-06-30"], "model_window": ["2018-02-15", "2019-02-14"]},
    ]
    ch = sequential_chain([f1, f2], windows=wins, baseline_version="v1+v2")
    assert ch.links[1].period == ["2019-02-15", "2019-06-30"]
    assert ch.links[0].model_window == ["2016-01-01", "2016-12-31"]
    # an engineering entry is dated against link 2 with no row index passed
    eng = NonRoutineAdjustment(
        "engineering", "2019-04-01", "rental", amount=50.0, se=5.0, evidence="invoice"
    )
    adj = apply_adjustments(ch, [eng])
    assert adj.ledger[0]["link"] == "link 2" and adj.links[1]["entries"] == [0]
    assert adj.links[1]["adjusted_baseline"] == pytest.approx(adj.links[1]["baseline"] + 50.0)
    assert adj.baseline_version == "v1+v2"
    with pytest.raises(ValueError, match="windows= has 1 items"):
        sequential_chain([f1, f2], windows=wins[:1])


def test_select_method_carries_the_fitted_models():
    daily = _daily(step=False)
    prop = select_method(
        daily, baseline=["2016-01-01", "2016-12-31"], reporting=["2017-01-15", "2018-01-14"]
    )
    assert "baseline" in prop.fitted and prop.fitted["baseline"]["type"] == "ChangePointModel"
    m = mv_model_from_dict(prop.fitted["baseline"])
    assert np.isfinite(m.predict(np.array([70.0]))).all()


# --------------------------------------------------------------------------- policy


def test_policy_reads_settle_days_from_the_entry_and_refuses_a_conflict():
    pol = RebaselinePolicy.from_entry({"settle_days": 21, "ecm_dates": [ECM], "validity": "sep"})
    assert pol.settle_days == 21 and pol.require_validity == "sep"
    assert pol.schedule.ecm_dates == (ECM,)
    same = RebaselinePolicy.from_entry({"settle_days": 21, "rebaseline": {"settle_days": 21}})
    assert same.settle_days == 21
    with pytest.raises(ValueError, match="differs from mv.settle_days"):
        RebaselinePolicy.from_entry({"settle_days": 21, "rebaseline": {"settle_days": 30}})
    with pytest.raises(ValueError, match="differs from mv.settle_days"):
        RebaselinePolicy.from_entry({"rebaseline": {"settle_days": 30}})  # default 14
    with pytest.raises(ValueError, match="unknown key"):
        RebaselinePolicy.from_entry({"rebaseline": {"settel_days": 14}})
    with pytest.raises(ValueError):
        RebaselinePolicy(require_validity="nope")


def _triggers(daily, **kw):
    m, st, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    pol = kw.pop("policy", RebaselinePolicy(schedule=EcmSchedule((ECM,), 14)))
    return (
        assess_triggers(
            daily,
            m,
            baseline=["2016-01-01", "2016-12-31"],
            policy=pol,
            fit_valid={"g14": st.accept, "sep": True},
            **kw,
        ),
        m,
        pol,
    )


def test_t1_fires_on_a_major_unexplained_step_and_not_on_the_ecm():
    trig, _m, _p = _triggers(_daily())
    t1 = [t for t in trig if t.id == "T1"]
    assert len(t1) == 1 and abs((pd.Timestamp(t1[0].date) - pd.Timestamp(STEP)).days) <= 3
    assert t1[0].outcome == "rebaseline" and t1[0].blocks and not t1[0].resolved
    assert first_block(trig).key == t1[0].key
    # the ECM step itself is explained (within settle_days of the declared ECM date)
    assert not any(abs((pd.Timestamp(t.date) - pd.Timestamp(ECM)).days) < 20 for t in t1)
    quiet, _m, _p = _triggers(_daily(step=False))
    assert not [t for t in quiet if t.id == "T1"]


def test_t2_t3_t5_t6_and_resolution():
    daily = _daily(step=False, end="2020-06-30")
    ev = [
        DeclaredChange("2017-06-01", "new server room", "minor", id="W-7"),
        DeclaredChange("2018-01-01", "wing closed", "major"),
    ]
    sf = [
        StaticFactorChange("floor_area", 100.0, 101.0, "2017-03-01"),
        StaticFactorChange("occupants", 100.0, 130.0, "2017-04-01"),
    ]
    pol = RebaselinePolicy(schedule=EcmSchedule((ECM, "2018-09-01"), 14))
    trig, m, _ = _triggers(
        daily, events=ev, static_factors=sf, policy=pol, reporting=["2017-01-15", "2020-06-30"]
    )
    ids = {t.id: t for t in trig}
    assert {"T2", "T3", "T5", "T6"} <= set(ids)
    t2 = [t for t in trig if t.id == "T2"]
    assert [t.outcome for t in t2] == ["nra_indicator", "rebaseline"]
    t3 = [t for t in trig if t.id == "T3"]
    assert len(t3) == 1 and t3[0].evidence["factor"] == "occupants"  # 1% is within tolerance
    assert ids["T5"].date == "2019-12-31" and ids["T5"].outcome == "rebaseline"
    assert ids["T6"].date == "2018-09-01" and not ids["T6"].blocks  # advisory
    # an accepted ledger entry resolves the NRA-class triggers it covers
    led = [
        NonRoutineAdjustment(
            "engineering", "2017-06-05", "server room", amount=1.0, se=0.1, evidence="log"
        ),
        StaticFactorAdjustment(
            "occupants",
            "proportional",
            "2017-04-01",
            "more staff",
            baseline_value=100.0,
            reporting_value=130.0,
            affected_share=0.4,
        ),
    ]
    trig2, _m, _ = _triggers(daily, events=ev, static_factors=sf, policy=pol, ledger=led)
    by = {t.key: t for t in trig2}
    assert by["T2:2017-06-01"].resolved and "ledger" in by["T2:2017-06-01"].resolved_by
    assert by["T3:2017-04-01"].resolved
    assert not by["T2:2018-01-01"].resolved  # a major change needs a rebaseline
    # a later version resolves every trigger dated before its window
    trig3, _m, _ = _triggers(daily, events=ev, policy=pol, next_version_start="2019-01-01")
    assert all(t.resolved for t in trig3 if t.date < "2019-01-01")


def test_t4_invalid_model_and_severe_coverage():
    daily = _daily(step=False)
    m, _st, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    t = assess_triggers(
        daily, m, baseline=["2016-01-01", "2016-12-31"], fit_valid={"g14": False, "sep": None}
    )
    assert any(x.id == "T4" and "G14" in x.detail for x in t)
    # a winter-only baseline against summer: severe
    mw, _s, _ = _fit(daily, ["2016-01-01", "2016-03-15"])
    t = assess_triggers(
        daily,
        mw,
        baseline=["2016-01-01", "2016-03-15"],
        reporting=["2016-06-01", "2016-09-30"],
        fit_valid={"g14": True},
    )
    assert any(x.id == "T4" and "severe" in x.detail for x in t)


def test_trigger_inside_the_sep_intermediate_period_must_rebaseline():
    daily = _daily(step=False)
    ev = [DeclaredChange("2017-06-01", "new load", "minor")]
    trig, _m, _ = _triggers(daily, events=ev, intermediate_period=["2017-03-01", "2018-02-28"])
    (t2,) = [t for t in trig if t.id == "T2"]
    assert t2.outcome == "rebaseline" and "intermediate period" in t2.detail


def test_new_window_declines_with_days_needed_then_proposes():
    daily = _daily()
    m, st, _ = _fit(daily, ["2016-01-01", "2016-12-31"])
    pol = RebaselinePolicy(schedule=EcmSchedule((ECM,), 14))
    early = propose_rebaseline(
        daily.loc[:"2018-12-31"],
        m,
        baseline=["2016-01-01", "2016-12-31"],
        policy=pol,
        fit_valid={"g14": True},
    )
    assert early.outcome == "declined"
    (t1,) = [t for t in early.triggers if t.id == "T1"]
    first = pd.Timestamp(t1.date) + pd.Timedelta(days=14)
    need = (first + pd.Timedelta(days=364) - pd.Timestamp("2018-12-31")).days
    assert early.days_needed == need
    assert f"rebaseline needs {need} more days" in early.declined_reason
    assert "unresolved non-routine event on" in early.declined_reason
    late = propose_rebaseline(
        daily, m, baseline=["2016-01-01", "2016-12-31"], policy=pol, fit_valid={"g14": True}
    )
    assert late.outcome == "rebaseline"
    w = late.window
    assert w.ok and w.window[1] == "2019-06-30"  # the latest window
    assert pd.Timestamp(w.window[0]) >= first and w.missing_frac <= 0.10
    assert w.model["type"] == "ChangePointModel" and w.fit_frame_sha256
    assert late.as_dict()["window"]["window"] == w.window
    json.dumps(late.as_dict(), allow_nan=False)


def test_new_window_skips_ecm_install_windows_and_gaps():
    daily = _daily(step=False)
    pol = RebaselinePolicy(schedule=EcmSchedule(("2018-12-01",), 14))
    w = new_baseline_window(daily, after="2017-06-01", policy=pol)
    assert w.ok
    s, e = pd.Timestamp(w.window[0]), pd.Timestamp(w.window[1])
    assert s > pd.Timestamp("2018-12-15") or e < pd.Timestamp("2018-11-17")
    gappy = daily.drop(daily.loc["2018-07-01":"2019-06-30"].index[::5])  # 20% missing
    w2 = new_baseline_window(gappy, after="2018-06-01", policy=RebaselinePolicy())
    assert not w2.ok and "missing" in w2.declined_reason


def test_version_segments_leave_the_rebaseline_gap_unreported():
    r1 = BaselineRecord(
        "a", "", "m", "k", {}, "t1", "2016-01-01", "2016-12-31", provenance={"version": 1}
    )
    r2 = BaselineRecord(
        "a",
        "",
        "m",
        "k",
        {},
        "t2",
        "2018-02-15",
        "2019-02-14",
        provenance={"version": 2, "trigger_date": "2018-02-01", "trigger_ids": ["T1:2018-02-01"]},
    )
    segs = version_segments([r1, r2], end="2019-06-30")
    assert segs[0]["period"] == ["2017-01-01", "2018-01-31"]
    assert segs[0]["gap_after"] == ["2018-02-01", "2019-02-14"]
    assert segs[0]["next_trigger_ids"] == ["T1:2018-02-01"]
    assert segs[1]["period"] == ["2019-02-15", "2019-06-30"] and segs[1]["gap_after"] is None


def test_retention_class_and_manifest_kind():
    assert DEFAULT_POLICY["mv_baselines"] == {"keep": "indefinite", "keep_versions": "all"}
    from camber.portfolio._state import _kind_of

    assert _kind_of("mv_baselines.json") == "mv_baselines"


def test_fit_frame_sha_changes_with_the_data():
    d = _daily(step=False).iloc[:30]
    a = fit_frame_sha256(d)
    d2 = d.copy()
    d2.iloc[3, 1] += 1e-9
    assert a != fit_frame_sha256(d2) and a == fit_frame_sha256(d.copy())


# --------------------------------------------------------------------------- workspace + CLI


def _workspace(tmp_path, *, end="2019-06-30"):
    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("Meter site", facility_id="f1", reason="test", activate=True)
    daily = _daily(end=end)
    idx = pd.date_range(daily.index[0], daily.index[-1] + pd.Timedelta(hours=23), freq="1h")
    oat = daily["oat"].reindex(idx, method="ffill").to_numpy()
    rate = (daily["energy"] / 24.0).reindex(idx, method="ffill").to_numpy()
    pf.store.write_role_frame(
        pd.DataFrame({Role.POWER: rate, Role.OAT: oat}, index=idx),
        facility_id="f1",
        equip="meter",
        equip_class="M",
    )
    cfg = {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "f1"},
        "equipment": [{"class": "M"}],
        "mv": [
            {
                "class": "M",
                "role": "power",
                "period": ["2016-01-01", "2016-12-31"],
                "reporting_period": ["2017-01-15", end],
                "method": "forecast",
                "ecm_dates": [ECM],
                "settle_days": 14,
            }
        ],
    }
    path = str(tmp_path / "config.json")
    open(path, "w").write(json.dumps(cfg))
    return ws, path, os.path.join(ws, "state", "f1", "mv_baselines.json")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    return _workspace(tmp_path_factory.mktemp("mvws"), end="2019-12-31")


def _audit(ws):
    return [json.loads(ln) for ln in open(os.path.join(ws, "_audit.ndjson"))]


def test_the_whole_flow(site, capsys, tmp_path):
    ws, cfg, store = site
    # freeze: needs --reason, dry run by default
    with pytest.raises(SystemExit):
        main(["mv", "freeze", cfg])
    assert main(["mv", "freeze", cfg, "--reason", "initial"]) == 0
    assert "dry run" in capsys.readouterr().out and not os.path.exists(store)
    assert main(["mv", "freeze", cfg, "--reason", "initial", "--by", "ana", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "froze v1" in out and os.path.exists(store)
    first = open(store).read()
    assert main(["mv", "freeze", cfg, "--reason", "again", "--apply"]) == 1  # never overwrites
    assert "already frozen" in capsys.readouterr().out and open(store).read() == first
    last = _audit(ws)[-1]
    assert last["action"] == "mv.freeze" and last["reason"] == "initial"
    assert last["facility_id"] == "f1" and last["details"]["changes"][0]["version"] == "v1"
    manifest = json.load(open(os.path.join(ws, "state", "f1", "manifest.json")))
    assert manifest["files"]["mv_baselines.json"]["kind"] == "mv_baselines"

    # run / list / propose never write
    assert main(["mv", "run", cfg]) == 0
    out = capsys.readouterr().out
    assert "mv_trigger" in out and "v1" in out
    assert main(["mv", "list", cfg]) == 0
    assert "v1" in capsys.readouterr().out
    pj = str(tmp_path / "prop.json")
    assert main(["mv", "propose", cfg, "--as-of", "2019-06-30", "--json", pj]) == 0
    out = capsys.readouterr().out
    assert "proposal: rebaseline" in out and "T1:" in out
    assert open(store).read() == first
    prop = json.load(open(pj))
    row = prop["meters"][0]
    assert row["rebaseline"]["outcome"] == "rebaseline"
    assert row["method_proposal"]["fitted"]["baseline"]["type"] == "ChangePointModel"

    # the versioned run: the saving stops at the unresolved step (partial, with a caveat)
    from camber.config import load_config, run_mv_config

    fs = run_mv_config(load_config(cfg), base_dir=os.path.dirname(cfg))
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert sav.metrics["baseline_version"] == "v1" and sav.metrics["partial"] is True
    assert any("partial: savings after" in c for c in sav.caveats)
    assert pd.Timestamp(sav.metrics["reporting_period"][1]) < pd.Timestamp(STEP)
    trig = [f for f in fs if f.rule == "mv_trigger"]
    assert any(f.severity == "warn" and f.metrics["id"] == "T1" for f in trig)

    # rebaseline: needs --by and --reason, dry run by default, then applied from the proposal
    with pytest.raises(SystemExit):
        main(["mv", "rebaseline", cfg, "--equip", "meter", "--reason", "x"])
    assert (
        main(
            ["mv", "rebaseline", cfg, "--equip", "meter", "--by", "ana", "--reason", "wing closed"]
        )
        == 0
    )
    assert "dry run" in capsys.readouterr().out and open(store).read() == first
    assert (
        main(
            [
                "mv",
                "rebaseline",
                cfg,
                "--equip",
                "meter",
                "--by",
                "ana",
                "--reason",
                "wing closed",
                "--from-proposal",
                pj,
                "--apply",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "rebaselined v2" in out and "supersedes v1" in out
    last = _audit(ws)[-1]
    assert last["action"] == "mv.rebaseline" and last["details"]["changes"][0]["triggers"]
    st = MVBaselineStore.load(store, facility_id="f1")
    (rec,) = st.records()
    assert version_label(rec) == "v2" and len(rec.history) == 1
    assert rec.provenance["from_proposal"] is True
    assert rec.provenance["model"] == row["rebaseline"]["window"]["model"]  # exactly as proposed

    # after the rebaseline the T1 trigger is resolved for v1
    fs = run_mv_config(load_config(cfg), base_dir=os.path.dirname(cfg))
    t1 = [f for f in fs if f.rule == "mv_trigger" and f.metrics["id"] == "T1"]
    assert t1 and all(f.metrics["resolved"] for f in t1)

    # adjust: record an engineering NRA on v2
    spec = str(tmp_path / "adj.json")
    json.dump(
        [
            {
                "kind": "nra",
                "method": "engineering",
                "start": "2019-09-01",
                "reason": "rental chiller",
                "amount": 500.0,
                "se": 50.0,
                "evidence": "invoice",
            }
        ],
        open(spec, "w"),
    )
    assert (
        main(
            [
                "mv",
                "adjust",
                cfg,
                "--equip",
                "meter",
                "--spec",
                spec,
                "--by",
                "ana",
                "--reason",
                "rental",
                "--apply",
            ]
        )
        == 0
    )
    assert "adjusted v2" in capsys.readouterr().out
    assert _audit(ws)[-1]["action"] == "mv.adjust"
    # an identical entry is not recorded twice
    assert (
        main(
            [
                "mv",
                "adjust",
                cfg,
                "--equip",
                "meter",
                "--spec",
                spec,
                "--by",
                "ana",
                "--reason",
                "rental",
                "--apply",
            ]
        )
        == 0
    )
    st = MVBaselineStore.load(store, facility_id="f1")
    assert len(st.records()[0].provenance["adjustments"]) == 1

    # report: a chain across both versions
    html = str(tmp_path / "mv.html")
    rj = str(tmp_path / "mv.json")
    before = open(store).read()
    assert main(["mv", "report", cfg, "--out", html, "--json", rj]) == 0
    assert open(store).read() == before
    doc = json.load(open(rj))
    (m,) = doc["meters"]
    assert [v["version"] for v in m["versions"]] == ["v1", "v2"]
    assert m["chain"]["method"] == "sequential_chain"
    assert m["segments"][0]["period"][0] == "2017-01-15"  # the declared reporting start
    assert m["chain"]["links"][1]["period"][0] == m["segments"][1]["period"][0]
    assert [ln["baseline_version"] for ln in m["links"]] == ["v1", "v2"]
    assert m["adjusted"][1]["baseline_version"] == "v2"
    page = open(html).read()
    assert "chained CUSUM" in page and "never rebaselines automatically" in page


def test_rebaseline_without_a_trigger_is_refused(tmp_path, capsys):
    ws, cfg, store = _workspace(tmp_path, end="2017-12-31")
    main(["mv", "freeze", cfg, "--reason", "init", "--apply"])
    capsys.readouterr()
    assert (
        main(
            [
                "mv",
                "rebaseline",
                cfg,
                "--equip",
                "meter",
                "--by",
                "a",
                "--reason",
                "r",
                "--apply",
                "--period",
                "2017-01-15",
                "2017-12-31",
            ]
        )
        == 1
    )
    assert "no unresolved trigger calls for a rebaseline" in capsys.readouterr().out


def test_a_suspended_facility_is_skipped(tmp_path, capsys):
    ws, cfg, store = _workspace(tmp_path, end="2017-06-30")
    Portfolio(ws).transition("f1", "suspend", reason="closed for the summer")
    assert main(["mv", "freeze", cfg, "--reason", "init", "--apply"]) == 0
    assert "is suspended: skipped" in capsys.readouterr().out and not os.path.exists(store)
    assert main(["mv", "run", cfg]) == 0
    assert "is suspended: skipped" in capsys.readouterr().out


def test_the_lock_refuses_a_concurrent_writer(tmp_path, capsys, monkeypatch):
    """A second writer is refused at once with the holder (the lock is re-entrant in-process,
    so the held lock is simulated at the CLI's one lock seam)."""
    import camber.cli as cli
    from camber.portfolio import PortfolioLocked

    ws, cfg, store = _workspace(tmp_path, end="2017-06-30")
    seen = []

    def held(ctx, *, write=True):
        seen.append(write)
        raise PortfolioLocked("held by 1@host")

    monkeypatch.setattr(cli, "_state_lock", held)
    assert main(["mv", "freeze", cfg, "--reason", "x", "--apply", "--allow-short"]) == 1
    assert "held by" in capsys.readouterr().err and seen == [True]
    assert not os.path.exists(store)


def test_mv_store_outside_a_workspace_and_migration(tmp_path, capsys):
    ws, cfg, _store = _workspace(tmp_path, end="2017-06-30")
    c = json.load(open(cfg))
    c["mv_store"] = "legacy_mv.json"
    c["mv"][0]["period"] = ["2016-01-01", "2016-06-30"]
    open(cfg, "w").write(json.dumps(c))
    assert main(["mv", "freeze", cfg, "--reason", "init", "--apply", "--allow-short"]) == 0
    legacy = str(tmp_path / "legacy_mv.json")
    rec = MVBaselineStore.load(legacy, facility_id="f1").records()[0]
    assert any("short or gappy" in x for x in rec.provenance["caveats"])
    # `camber portfolio migrate` moves every version with the facility's state
    pf = Portfolio(ws)
    rep = pf.migrate(configs=[cfg], apply=True, reason="consolidate")
    assert rep["applied"] and not rep["blocked"]
    target = os.path.join(ws, "state", "f1", "mv_baselines.json")
    doc = json.load(open(target))
    assert doc["schema"] == MV_SCHEMA and len(doc["mv_baselines"]) == 1
    stub = json.load(open(legacy))
    assert stub["camber_redirect"]["kind"] == "mv_baselines"
    # the config naming the old path still reads it, through the redirect
    back = MVBaselineStore.load(legacy, facility_id="f1")
    assert version_label(back.records()[0]) == "v1"


def test_declared_method_mismatch_declines(site, tmp_path):
    ws, cfg, store = site
    if not os.path.exists(store):
        pytest.skip("runs after the whole-flow test")
    from camber.config import load_config, run_mv_config

    c = load_config(cfg)
    c["mv"][0]["method"] = "backcast"
    fs = run_mv_config(c, base_dir=os.path.dirname(cfg))
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert sav.metrics["declined"] and "rebaseline-class action" in sav.metrics["declined_reason"]
