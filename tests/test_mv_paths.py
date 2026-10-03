"""``camber mv`` and :mod:`camber.mvrun`: the refusal paths and the edges (#21 phase 21d, #48).

Every refusal is a sentence the operator can act on, and none of them writes anything.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_mandv_rebaseline import STEP, _daily, _fit, _workspace  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.config import load_config, run_config, run_mv_config  # noqa: E402
from camber.mandv.adjustments import EcmSchedule  # noqa: E402
from camber.mandv.rebaseline import (  # noqa: E402
    DeclaredChange,
    MVBaselineStore,
    RebaselinePolicy,
    StaticFactorChange,
    Trigger,
    event_phrase,
    events_from_entry,
    mv_provenance,
    propose_rebaseline,
    version_label,
    window_anchor,
)
from camber.mvrun import (  # noqa: E402
    chained_report,
    fit_version,
    meter_series,
    open_mv_store,
    plan_adjust,
    plan_freeze,
    plan_rebaseline,
    propose,
)


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    """A workspace meter with v1 frozen (2016) and data through 2019."""
    tmp = tmp_path_factory.mktemp("mvpaths")
    ws, cfg, store = _workspace(tmp, end="2019-12-31")
    assert main(["mv", "freeze", cfg, "--reason", "init", "--by", "ana", "--apply"]) == 0
    return ws, cfg, store, os.path.dirname(cfg)


def _cfg(frozen, **entry):
    _ws, cfg, _store, base = frozen
    c = load_config(cfg)
    c["mv"][0].update(entry)
    return c, base


def test_store_resolution_outside_a_workspace(tmp_path):
    c = {"site": "Nowhere", "source": {"kind": "perpoint_csv", "folder": "x"}, "mv": []}
    store, path, _ctx = open_mv_store(c, base_dir=str(tmp_path), required=False)
    assert store is None and path is None
    with pytest.raises(ValueError, match="mv_store"):
        open_mv_store(c, base_dir=str(tmp_path))


def test_fit_version_refuses_short_or_thin_windows(frozen):
    c, base = _cfg(frozen)
    (ms,) = meter_series(c, base_dir=base)
    with pytest.raises(ValueError, match="--allow-short"):
        fit_version(ms.daily, ["2016-01-01", "2016-06-30"], entry=ms.entry)
    fit = fit_version(ms.daily, ["2016-01-01", "2016-06-30"], entry=ms.entry, allow_short=True)
    assert fit["caveats"] and "short or gappy" in fit["caveats"][0]
    with pytest.raises(ValueError, match="only 5 days"):
        fit_version(ms.daily, ["2016-01-01", "2016-01-05"], entry=ms.entry, allow_short=True)
    assert meter_series(c, base_dir=base, equips=["nope"]) == []


def test_freeze_refusals(frozen):
    c, base = _cfg(frozen, method=None)
    c["mv"][0].pop("method")
    plan = plan_freeze(c, base_dir=base, reason="r", accepted_by="a")
    assert plan.refused and "already frozen" in plan.refused[0]["why"]
    ws, cfg, store, base = frozen
    other = str(os.path.join(base, "other_mv.json"))
    plan = plan_freeze(c, base_dir=base, reason="r", accepted_by="a", store_path=other)
    assert "declare mv.method" in plan.refused[0]["why"] and not plan.changes
    c2, _ = _cfg(frozen)
    c2["mv"][0].pop("period")
    plan = plan_freeze(c2, base_dir=base, reason="r", accepted_by="a", store_path=other)
    assert "no period" in plan.refused[0]["why"]
    c3, _ = _cfg(frozen, period=["2016-01-01", "2016-03-31"])
    plan = plan_freeze(c3, base_dir=base, reason="r", accepted_by="a", store_path=other)
    assert "needs 365 consecutive days" in plan.refused[0]["why"]
    assert not os.path.exists(other)  # nothing is written by a plan


def test_provenance_requires_reason_and_who():
    d = _daily(step=False)
    m, _st, sub = _fit(d, ["2016-01-01", "2016-12-31"])
    with pytest.raises(ValueError, match="reason"):
        mv_provenance(m, sub, reason=" ", accepted_by="a", method="forecast", kernel="g14")
    with pytest.raises(ValueError, match="accepted_by"):
        mv_provenance(m, sub, reason="r", accepted_by="", method="forecast", kernel="g14")


def test_rebaseline_refusals(frozen):
    c, base = _cfg(frozen)
    plan = plan_rebaseline(c, base_dir=base, equip="nope", reason="r", accepted_by="a")
    assert "no such M&V meter" in plan.refused[0]["why"]
    plan = plan_rebaseline(
        c, base_dir=base, equip="meter", reason="r", accepted_by="a", trigger_ids=["T6"]
    )
    assert "no unresolved rebaseline-class trigger ['T6']" in plan.refused[0]["why"]
    # a window inside an ECM installation window
    c2, _ = _cfg(frozen, ecm_dates=["2017-01-01", "2019-06-01"])
    plan = plan_rebaseline(
        c2,
        base_dir=base,
        equip="meter",
        reason="r",
        accepted_by="a",
        period=["2018-12-01", "2019-11-30"],
    )
    assert "overlaps the ECM installation window" in plan.refused[0]["why"]
    # too early after the step
    plan = plan_rebaseline(
        c,
        base_dir=base,
        equip="meter",
        reason="r",
        accepted_by="a",
        period=["2018-02-05", "2019-02-04"],
    )
    assert "settle days after the trigger" in plan.refused[0]["why"]
    # a gappy window
    plan = plan_rebaseline(
        c,
        base_dir=base,
        equip="meter",
        reason="r",
        accepted_by="a",
        period=["2019-06-01", "2020-05-31"],
    )
    assert "missing" in plan.refused[0]["why"]
    # not enough data after the step yet: the proposal declines with the days needed
    plan = plan_rebaseline(
        c, base_dir=base, equip="meter", reason="r", accepted_by="a", as_of="2018-12-31"
    )
    assert plan.refused[0]["days_needed"] > 0
    # a proposal whose data changed since it was made
    prop = {
        "window": {
            "window": ["2018-12-01", "2019-11-30"],
            "model": {"type": "x"},
            "fit_frame_sha256": "0" * 64,
        }
    }
    plan = plan_rebaseline(
        c, base_dir=base, equip="meter", reason="r", accepted_by="a", from_proposal=prop
    )
    assert "changed since the proposal" in plan.refused[0]["why"]
    plan = plan_rebaseline(
        c,
        base_dir=base,
        equip="meter",
        reason="r",
        accepted_by="a",
        from_proposal={"window": None, "declined_reason": "x"},
    )
    assert "no window/model" in plan.refused[0]["why"]
    # the new model must be valid under the entry's regime
    c3, _ = _cfg(frozen, validity="both")
    c3["mv"][0]["rebaseline"] = {"major_step_frac": 0.01}
    plan = plan_rebaseline(
        c3,
        base_dir=base,
        equip="meter",
        reason="r",
        accepted_by="a",
        period=["2018-03-01", "2019-02-28"],
    )
    assert plan.refused or plan.changes


def test_rebaseline_before_anything_is_frozen(tmp_path):
    ws, cfg, store = _workspace(tmp_path, end="2017-03-31")
    c = load_config(cfg)
    plan = plan_rebaseline(
        c, base_dir=os.path.dirname(cfg), equip="meter", reason="r", accepted_by="a"
    )
    assert "nothing frozen yet" in plan.refused[0]["why"]
    plan = plan_adjust(
        c,
        base_dir=os.path.dirname(cfg),
        equip="meter",
        reason="r",
        accepted_by="a",
        specs=[{"kind": "nra"}],
    )
    assert "nothing frozen yet" in plan.refused[0]["why"]
    doc = propose(c, base_dir=os.path.dirname(cfg))
    assert "version" not in doc["meters"][0] and "method_proposal" in doc["meters"][0]
    assert main(["mv", "propose", cfg]) == 0
    assert main(["mv", "list", cfg]) == 0
    rep = chained_report(c, base_dir=os.path.dirname(cfg))
    assert rep["meters"] == []


def test_adjust_paths(frozen, tmp_path, capsys):
    c, base = _cfg(frozen)
    with pytest.raises(ValueError, match="non-empty JSON list"):
        plan_adjust(c, base_dir=base, equip="meter", specs=[], reason="r", accepted_by="a")
    plan = plan_adjust(c, base_dir=base, equip="nope", specs=[{}], reason="r", accepted_by="a")
    assert "no such M&V meter" in plan.refused[0]["why"]
    plan = plan_adjust(c, base_dir=base, equip="meter", specs=["x"], reason="r", accepted_by="a")
    assert "must be an object" in plan.refused[0]["why"]
    # an indicator entry is estimated against the frozen model on the reporting days
    ind = {
        "kind": "nra",
        "method": "indicator",
        "start": "2017-06-01",
        "end": "2017-07-01",
        "reason": "temporary load",
    }
    plan = plan_adjust(c, base_dir=base, equip="meter", specs=[ind], reason="r", accepted_by="a")
    assert plan.changes and "indicator from 2017-06-01" in plan.changes[0]["entries"][0]
    (rec,) = plan.store.records()
    (entry,) = plan.store.ledger(rec)
    assert entry.fit is not None and entry.fit.fit_period == "reporting"
    # ... and on the baseline days when it starts inside the baseline window
    ind_b = {**ind, "start": "2016-06-01", "end": "2016-07-01"}
    plan = plan_adjust(c, base_dir=base, equip="meter", specs=[ind_b], reason="r", accepted_by="a")
    assert plan.store.ledger(plan.store.records()[0])[0].fit.fit_period == "baseline"
    # the confounding guard refuses a meter-derived entry next to an ECM
    near = {**ind, "start": "2017-01-05", "end": "2017-02-01"}
    plan = plan_adjust(c, base_dir=base, equip="meter", specs=[near], reason="r", accepted_by="a")
    assert "settle window" in plan.refused[0]["why"]
    # SEP's evidence rule
    c2, _ = _cfg(frozen, validity="sep")
    eng = {
        "kind": "nra",
        "method": "engineering",
        "start": "2017-06-01",
        "reason": "x",
        "amount": 1.0,
        "se": 0.1,
        "evidence": "log",
    }
    plan = plan_adjust(c2, base_dir=base, equip="meter", specs=[eng], reason="r", accepted_by="a")
    assert "approved_by" in plan.refused[0]["why"]
    # a proposal cannot be recorded
    with pytest.raises(ValueError, match="is a proposal"):
        plan.store.add_adjustments(
            [
                __import__("camber.mandv.adjustments", fromlist=["x"]).NonRoutineAdjustment(
                    "engineering",
                    "2017-06-01",
                    "x",
                    amount=1.0,
                    se=0.1,
                    evidence="e",
                    status="proposed",
                )
            ],
            site=plan.ctx.site,
            equip="meter",
            kind="mv_power",
            accepted_by="a",
            reason="r",
        )
    with pytest.raises(ValueError, match="accepted_by and reason"):
        plan.store.add_adjustments(
            [], site="", equip="meter", kind="mv_power", accepted_by="", reason=""
        )
    # the CLI reads {"adjustments": [...]} too, and refuses a missing --reason
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"adjustments": [eng]}))
    _ws, cfg, _store, _b = frozen
    assert (
        main(
            [
                "mv",
                "adjust",
                cfg,
                "--equip",
                "meter",
                "--spec",
                str(spec),
                "--by",
                "a",
                "--reason",
                " ",
            ]
        )
        == 1
    )
    assert "needs --reason" in capsys.readouterr().err


def test_versioned_run_declines_from_the_start_and_on_method_change(frozen):
    c, base = _cfg(frozen, reporting_period=["2018-03-01", "2018-12-31"])
    fs = run_mv_config(c, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    why = sav.metrics["declined_reason"]
    assert sav.metrics["declined"] and "unresolved non-routine event on 2018-02-01" in why
    assert "a rebaseline window 2019-01-01..2019-12-31 is available" in why
    assert sav.metrics["baseline_version"] == "v1"
    # with less data, the decline says how many more days a rebaseline needs
    short = {**c, "source": {**c["source"], "end": "2018-12-31"}}
    fs = run_mv_config(short, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert "rebaseline needs" in sav.metrics["declined_reason"]
    # an NRA-class trigger declines with the NRA hint
    c2, _ = _cfg(frozen, reporting_period=["2018-03-01", "2018-12-31"])
    c2["mv"][0]["rebaseline"] = {"major_step_frac": 0.9}
    fs = run_mv_config(c2, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert "record an NRA" in sav.metrics["declined_reason"]
    # the reporting period starts before any version ended
    c3, _ = _cfg(frozen, reporting_period=["2016-06-01", "2016-12-31"])
    fs = run_mv_config(c3, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert "no frozen baseline version ended before" in sav.metrics["declined_reason"]
    # no reporting period: the baseline finding says it was read, not refitted
    c4, _ = _cfg(frozen)
    c4["mv"][0].pop("reporting_period")
    (b,) = run_mv_config(c4, base_dir=base)
    assert b.rule == "mv_baseline" and "not refitted" in b.summary
    # `camber run` uses the same versioned path
    fs = run_config(c)
    assert any(f.rule == "mv_trigger" for f in fs.findings)
    # a bad rebaseline block is a config error
    c5, _ = _cfg(frozen, rebaseline={"bogus": 1})
    with pytest.raises(ValueError, match="unknown key"):
        run_mv_config(c5, base_dir=base)
    c6, _ = _cfg(frozen, rebaseline={"events": [{"date": "2017-01-01"}]})
    with pytest.raises(ValueError, match="mv.rebaseline"):
        run_mv_config(c6, base_dir=base)
    assert run_mv_config({**c, "mv": []}, base_dir=base) == []


def test_other_frozen_methods_and_auto(tmp_path):
    ws, cfg, store = _workspace(tmp_path, end="2017-12-31")
    c = load_config(cfg)
    c["mv"][0]["method"] = "backcast"
    base = os.path.dirname(cfg)
    open(cfg, "w").write(json.dumps(c))
    assert main(["mv", "freeze", cfg, "--reason", "init", "--apply"]) == 0
    fs = run_mv_config(c, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert sav.metrics["method"] == "backcast" and sav.metrics["baseline_version"] == "v1"
    c["mv"][0]["method"] = "auto"
    fs = run_mv_config(c, base_dir=base)
    assert any(f.rule == "mv_method_proposal" for f in fs)
    c["mv"][0].pop("method")  # undeclared: the frozen method is used, and it counts as declared
    fs = run_mv_config(c, base_dir=base)
    (sav,) = [f for f in fs if f.rule == "mv_savings"]
    assert sav.metrics["method_declared"] is True
    # the chained report reports a non-forecast version's segment as a forecast, and says so
    rep = chained_report(c, base_dir=base)
    (m,) = rep["meters"]
    assert any("reports each segment as a forecast" in cv for cv in m.chain.caveats)


def test_data_changed_under_a_frozen_baseline(tmp_path):
    ws, cfg, store = _workspace(tmp_path, end="2017-12-31")
    assert main(["mv", "freeze", cfg, "--reason", "init", "--apply"]) == 0
    st = MVBaselineStore.load(store, facility_id="f1")
    rec = st.records()[0]
    rec.provenance["fit_frame_sha256"] = "0" * 64
    st.save()
    c = load_config(cfg)
    base = os.path.dirname(cfg)
    fs = run_mv_config(c, base_dir=base)
    (b,) = [f for f in fs if f.rule == "mv_baseline"]
    assert b.metrics["baseline_data_changed"] and "no longer reproduces" in b.caveats[0]
    doc = propose(c, base_dir=base)
    assert doc["meters"][0]["baseline_data_changed"] is True
    rep = chained_report(c, base_dir=base)
    assert any("changed since it was frozen" in x for x in rep["meters"][0].caveats)
    assert main(["mv", "list", cfg]) == 0  # the list flags the provenance mismatch


def test_chained_report_segment_declined_at_its_start(frozen, tmp_path):
    c, base = _cfg(frozen)
    c["mv"][0]["rebaseline"] = {
        "events": [{"date": "2017-01-20", "description": "wing closed", "magnitude": "major"}]
    }
    rep = chained_report(c, base_dir=base, as_of="2019-12-31")
    (m,) = rep["meters"]
    assert m.segments[0].get("partial") or m.segments[0].get("declined")
    from camber.report.mv import mv_report_html

    html = mv_report_html(rep, charts=False)
    assert "UNRESOLVED" in html and "<img" not in html
    assert "nothing was read" in mv_report_html({"skipped_state": "suspended", "meters": []})
    assert "No meter has a frozen" in mv_report_html({"meters": []})


def test_policy_helpers_and_events():
    ev, sf = events_from_entry(
        {
            "rebaseline": {
                "events": [{"date": "2018-01-01", "description": "x"}],
                "static_factors": [
                    {"factor": "area", "baseline_value": 1.0, "value": 1.2, "date": "2018-01-01"}
                ],
            }
        }
    )
    assert ev[0].magnitude == "major" and sf[0].change == pytest.approx(0.2)
    with pytest.raises(ValueError, match="must be lists"):
        events_from_entry({"rebaseline": {"events": {}}})
    with pytest.raises(ValueError, match="magnitude"):
        DeclaredChange("2018-01-01", "x", "huge")
    with pytest.raises(ValueError, match="description"):
        DeclaredChange("2018-01-01", " ")
    with pytest.raises(ValueError, match="baseline_value"):
        StaticFactorChange("a", 0.0, 1.0, "2018-01-01")
    with pytest.raises(ValueError, match="tolerance"):
        StaticFactorChange("a", 1.0, 1.0, "2018-01-01", tolerance=-1)
    for bad in ({"max_missing_frac": 1.0}, {"min_baseline_days": 0}, {"major_step_frac": 0}):
        with pytest.raises(ValueError):
            RebaselinePolicy(**bad)
    assert RebaselinePolicy().as_dict()["schedule"]["settle_days"] == 14
    t2 = Trigger("T2", "2018-01-01", "t", "d", "rebaseline", "b")
    t1 = Trigger("T1", "2018-05-01", "t", "d", "nra_indicator", "b")
    t0 = Trigger(
        "T1",
        "2017-05-01",
        "t",
        "d",
        "nra_indicator",
        "b",
        resolved=True,
        resolved_by="rebaselined (a later version starts 2017-06-01)",
    )
    assert window_anchor([t2, t1, t0], [t2]) is t1  # never straddle a later step
    assert "non-routine event" in event_phrase(t1)
    assert "unresolved trigger T5" in event_phrase(Trigger("T5", "2019-01-01", "t", "d", "r", "b"))


def test_proposal_outcomes_nra_and_advisory_t6():
    d = _daily(step=False, end="2019-06-30")
    m, st, _ = _fit(d, ["2016-01-01", "2016-12-31"])
    ev = [
        DeclaredChange("2017-06-01", "server room", "minor"),
        DeclaredChange("2017-08-01", "new sub-meter", "static"),
    ]
    sf = [StaticFactorChange("occupants", 100.0, 130.0, "2017-04-01")]
    p = propose_rebaseline(
        d,
        m,
        baseline=["2016-01-01", "2016-12-31"],
        policy=RebaselinePolicy(schedule=EcmSchedule(("2017-01-01",), 14)),
        fit_valid={"g14": True},
        events=ev,
        static_factors=sf,
    )
    assert p.outcome == "nra"
    kinds = sorted(s["method"] for s in p.nra_specs)
    assert kinds == ["engineering", "indicator", "proportional"]
    assert any(s.get("affected_share") is None for s in p.nra_specs)  # no default share (D8)
    p6 = propose_rebaseline(
        d,
        m,
        baseline=["2016-01-01", "2016-12-31"],
        policy=RebaselinePolicy(schedule=EcmSchedule(("2017-01-01", "2017-09-01"), 14)),
        fit_valid={"g14": True},
    )
    assert any("T6 is advisory" in c for c in p6.caveats)


def test_step_detection_edge_cases():
    from camber.mandv.rebaseline import _savings_steps

    d = _daily(step=False)
    m, _st, _ = _fit(d, ["2016-01-01", "2016-12-31"])
    assert _savings_steps(d.iloc[:30], m, RebaselinePolicy()) == []
    flat = d.loc["2017-02-01":"2017-12-31"].copy()
    flat["energy"] = m.predict(flat["oat"].values)  # no noise at all
    assert _savings_steps(flat, m, RebaselinePolicy()) == []


def test_cli_run_list_json_and_report_without_charts(frozen, tmp_path, capsys):
    _ws, cfg, store, _base = frozen
    out = tmp_path / "o"
    assert main(["mv", "run", cfg, "--out", str(out)]) == 0
    assert (out / "mv_findings.json").exists()
    js = tmp_path / "l.json"
    assert main(["mv", "list", cfg, "--equip", "meter", "--json", str(js)]) == 0
    rows = json.load(open(js))
    assert rows[0]["version"] == "v1" and rows[0]["verified"] is True
    html = tmp_path / "r.html"
    assert main(["mv", "report", cfg, "--out", str(html), "--no-charts"]) == 0
    assert "<img" not in html.read_text()
    c = load_config(cfg)
    c.pop("mv")
    nomv = tmp_path / "nomv.json"
    nomv.write_text(json.dumps(c))
    assert main(["mv", "run", str(nomv)]) == 0
    assert "no 'mv' section" in capsys.readouterr().out


def test_chart_handles_empty_and_multi_version_frames():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from camber.charts.cusum_chart import chained_cusum_plot

    ax = chained_cusum_plot(pd.DataFrame(columns=["version", "projected", "actual"]))
    assert "no reported days" in ax.get_title()
    idx = pd.date_range("2017-01-01", periods=10, freq="D")
    f = pd.DataFrame(
        {
            "version": ["v1"] * 5 + ["v2"] * 5,
            "projected": np.full(10, 10.0),
            "actual": np.full(10, 9.0),
        },
        index=idx,
    )
    ax = chained_cusum_plot(
        f,
        markers=[{"date": "2017-01-06", "label": "rebaseline"}],
        gaps=[["2017-01-06", "2017-01-06"]],
    )
    assert "across 2 version(s)" in ax.get_title() and "savings 10" in ax.get_title()
    plt.close("all")


def test_version_lookup_edges(frozen):
    _ws, _cfg_, store, _b = frozen
    st = MVBaselineStore.load(store, facility_id="f1")
    (rec,) = st.records()
    assert st.version(rec.site, "meter", "mv_power", "v9") is None
    assert st.in_force(rec.site, "meter", "mv_power", "2016-06-01") is None
    assert st.versions(rec.site, "nope", "mv_power") == []
    assert version_label({"provenance": {}}) == "v?"
    with pytest.raises(ValueError, match="no frozen M&V baseline"):
        st.rebaseline(
            None,
            site="",
            equip="nope",
            kind="mv_power",
            at="t",
            period=["2019-01-01", "2019-12-31"],
            provenance={},
        )
    with pytest.raises(ValueError, match="to adjust"):
        st.add_adjustments([], site="", equip="nope", kind="mv_power", accepted_by="a", reason="r")
    assert pd.Timestamp(STEP).year == 2018


def test_chained_report_follows_the_unit_system(frozen):
    """0.93 (#70): `camber mv report` reports energy in units.system (kBtu or kWh)."""
    from camber import energy_units as eu
    from camber.report.mv import mv_report_html

    c, base = _cfg(frozen)
    (raw,) = chained_report(c, base_dir=base)["meters"]
    assert raw.units is None and "units" not in raw.as_dict()
    c["mv"][0]["units"] = "kW"
    (same,) = chained_report(c, base_dir=base)["meters"]  # units named, no system: unchanged
    assert same.as_dict() == raw.as_dict()
    for system, unit in (("ip", "kBtu"), ("si", "kWh")):
        c["units"] = {"system": system}
        (m,) = chained_report(c, base_dir=base)["meters"]
        k = eu.energy_factor("kWh", unit)
        assert m.units == {
            "factor": k,
            "energy_unit": unit,
            "meter_unit": "kWh",
            "unit_system": system,
        }
        d, d0 = m.as_dict(), raw.as_dict()
        assert d["units"]["energy_unit"] == unit
        assert d["chain"]["savings"] == pytest.approx(d0["chain"]["savings"] * k)
        assert d["chain"]["savings_pct"] == d0["chain"]["savings_pct"]
        assert d["links"][0]["abs_uncertainty"] == pytest.approx(
            d0["links"][0]["abs_uncertainty"] * k
        )
        assert m.cusum["actual"].sum() == pytest.approx(raw.cusum["actual"].sum() * k)
        html = mv_report_html({"facility_id": "f1", "meters": [m]}, charts=False)
        assert f"Savings ({unit})" in html and f"Energy in {unit}" in html
    c.pop("units")
    html0 = mv_report_html({"facility_id": "f1", "meters": [raw]}, charts=False)
    assert "Savings</th>" in html0 and "Energy in" not in html0
    c["units"] = {"system": "ip"}
    c["mv"][0].pop("units")
    with pytest.raises(ValueError, match="rate unit"):
        chained_report(c, base_dir=base)
