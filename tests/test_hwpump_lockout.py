"""hw_pump_summer_lockout (0.103, #132): the hot-water pump's warm-weather lockout, and its
wiring through the config, recommenders, walk-down, triage, SOO library, faultlab and docs."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.aso import RECOMMENDERS, recommend  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.plant import (  # noqa: E402
    LOCKOUT_FAULT_PCT,
    LOCKOUT_WARN_PCT,
    SUMMER_LOCKOUT_OAT_F,
    analyze_hw_pump_lockout,
    lockout_severity,
)
from camber.references import reference_ids_for  # noqa: E402
from camber.rules.applicability import rule_equip_classes  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.boiler_rule import BoilerSummerLockout  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule  # noqa: E402
from camber.rules.hwpump_lockout_rule import HWPumpSummerLockout, pump_run  # noqa: E402
from camber.rules.param_docs import PARAM_DOCS, rule_params  # noqa: E402
from camber.rules.triage import group_findings  # noqa: E402
from camber.scorecard import RULE_CATEGORY  # noqa: E402
from camber.soo import evaluate_soo  # noqa: E402
from camber.soo_library import g36_plant_sequence  # noqa: E402
from camber.walkdown import DESIGN_PARAMS, SITE_CHECKS, cause_key  # noqa: E402

N = 24 * 14


def _idx(n=N):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # a Monday


def _frame(pump, oat, **extra):
    idx = _idx(len(oat))
    cols = {Role.OAT: oat, **extra}
    if pump is not None:
        cols[Role.PUMP_STATUS] = pump
    return pd.DataFrame(cols, index=idx)


def _oat(n=N, center=72.0, amp=15.0):
    hour = _idx(n).hour
    return center + amp * np.sin((hour - 9) / 24 * 2 * np.pi)


# --------------------------------------------------------------------------- the analysis


def test_severity_matches_the_boiler_rule():
    assert (LOCKOUT_WARN_PCT, LOCKOUT_FAULT_PCT, SUMMER_LOCKOUT_OAT_F) == (5.0, 20.0, 65.0)
    assert [lockout_severity(x) for x in (None, 0.0, 4.99, 5.0, 19.99, 20.0)] == [
        "info",
        "ok",
        "ok",
        "warn",
        "warn",
        "fault",
    ]
    assert BoilerSummerLockout().summer_lockout_oat_f == HWPumpSummerLockout().summer_lockout_oat_f


def test_pump_left_on_faults_and_a_locked_out_pump_passes():
    oat = _oat()
    boiler = (oat < 65).astype(float)
    rule = HWPumpSummerLockout()
    bad = rule.analyze("P", _frame(np.ones(N), oat, **{Role.BOILER_STATUS: boiler}))
    assert bad.severity == "fault" and bad.metrics["run_source"] == "pump status"
    assert bad.metrics["pump_running_pct"] == 100.0 and bad.metrics["boiler_off_pct"] == 100.0
    assert bad.metrics["max_oat_running_f"] == pytest.approx(oat.max(), abs=0.1)
    assert "boiler was off in 100%" in bad.summary and not bad.caveats
    good = rule.analyze("P", _frame(boiler, oat, **{Role.BOILER_STATUS: boiler}))
    assert good.severity == "ok" and good.metrics["summer_run_pct"] == 0.0
    assert good.metrics["boiler_off_pct"] is None
    assert good.metrics["max_oat_running_f"] < 65.0


def test_threshold_is_strictly_above_and_moves_the_verdict():
    oat = _oat()
    pump = (oat <= 70).astype(float)  # the pump stops above 70 F
    f = _frame(pump, oat, **{Role.HW_SUPPLY_TEMP: np.full(N, 150.0)})
    assert HWPumpSummerLockout(summer_lockout_oat_f=70.0).analyze("P", f).severity == "ok"
    assert HWPumpSummerLockout(summer_lockout_oat_f=60.0).analyze("P", f).severity == "fault"


def test_run_from_speed_then_flow_with_a_caveat():
    oat = _oat()
    speed = np.where(oat > 65, 0.40, 0.0)  # a 0-1 fraction speed is rescaled to percent
    f = pd.DataFrame({Role.OAT: oat, Role.HW_PUMP_SPEED: speed}, index=_idx())
    got = HWPumpSummerLockout().analyze("P", f)
    assert got.severity == "fault" and got.metrics["run_source"] == "pump speed"
    assert any("speed" in c for c in got.caveats)
    flow = pd.DataFrame({Role.OAT: oat, Role.HW_FLOW: np.where(oat < 65, 120.0, 1.0)}, _idx())
    got = HWPumpSummerLockout().analyze("P", flow)
    assert got.severity == "ok" and got.metrics["run_source"] == "hot-water flow"
    run, role = pump_run(pd.DataFrame({Role.OAT: oat}, index=_idx()))
    assert run is None and role is None


def test_declines_without_oat_or_without_a_hot_water_point():
    oat = _oat()
    no_oat = pd.DataFrame(
        {Role.PUMP_STATUS: np.ones(N), Role.HW_SUPPLY_TEMP: np.full(N, 150.0)}, index=_idx()
    )
    got = HWPumpSummerLockout().analyze("P", no_oat)
    assert got.severity == "info" and got.metrics["summer_run_pct"] is None
    assert any("no OAT" in c for c in got.caveats)
    status_only = _frame(np.ones(N), oat)  # could be a chilled-water pump
    got = HWPumpSummerLockout().analyze("P", status_only)
    assert got.severity == "info" and "another loop" in got.caveats[0]
    never = _frame(np.zeros(N), oat, **{Role.HW_SUPPLY_TEMP: np.full(N, 90.0)})
    got = HWPumpSummerLockout().analyze("P", never)
    assert got.severity == "ok" and got.metrics["summer_run_pct"] == 0.0
    assert got.metrics["max_oat_running_f"] is None
    assert analyze_hw_pump_lockout(pd.DataFrame({"OAT": oat}, index=_idx()), "P") is None


def test_boiler_off_share_needs_a_boiler_signal_and_reads_the_gas():
    oat = _oat()
    gas = np.where(oat < 60, 300.0, 0.0)
    f = _frame(np.ones(N), oat, **{Role.GAS_INPUT_RATE: gas})
    got = HWPumpSummerLockout().analyze("P", f)
    assert got.metrics["boiler_off_pct"] == 100.0
    f = _frame(np.ones(N), oat, **{Role.HW_SUPPLY_TEMP: np.full(N, 150.0)})
    got = HWPumpSummerLockout().analyze("P", f)
    assert got.metrics["boiler_off_pct"] is None
    assert any("boiler firing not compared" in c for c in got.caveats)


def test_evidence_shades_only_the_judged_hours():
    oat = _oat()
    f = _frame(
        np.ones(N), oat, **{Role.BOILER_STATUS: np.zeros(N), Role.HW_PUMP_SPEED: np.full(N, 40.0)}
    )
    rule = HWPumpSummerLockout()
    ev = rule.evidence("P", f)
    assert ev.renderer == "multitrend" and ev.roles == [Role.OAT, Role.HW_PUMP_SPEED]
    mask = rule.violation_mask(f)
    weekend = f.index.dayofweek >= 5
    assert mask.any() and not mask[weekend].any() and not mask[f.index.hour < 7].any()
    assert rule.evidence("P", f.drop(columns=[Role.OAT])) is None


def test_boiler_reports_where_it_stops_firing():
    oat = _oat()
    status = (oat < 65).astype(float)
    f = pd.DataFrame({Role.BOILER_STATUS: status, Role.OAT: oat}, index=_idx())
    got = BoilerSummerLockout().analyze("B", f)
    assert got.severity == "ok" and 64.0 < got.metrics["max_oat_running_f"] < 65.0
    no_oat = BoilerSummerLockout().analyze("B", f.drop(columns=[Role.OAT]))
    assert no_oat.metrics["max_oat_running_f"] is None


# --------------------------------------------------------------------------- the wiring


def test_registered_documented_and_classified():
    rule = builtin_registry().get("hw_pump_summer_lockout")
    assert isinstance(rule, HWPumpSummerLockout)
    assert rule_equip_classes(rule) == ("hw_plant", "pump")
    assert set(PARAM_DOCS["hw_pump_summer_lockout"]) == {"summer_lockout_oat_f"}
    assert [p.default for p in rule_params("hw_pump_summer_lockout")] == [65.0]
    assert RULE_CATEGORY["hw_pump_summer_lockout"] == "energy"
    assert reference_ids_for("hw_pump_summer_lockout") == [
        "pnnl-guide-plant-heating",
        "pnnl-retuning-ch8",
    ]
    assert make_rule("hw_pump_summer_lockout", summer_lockout_oat_f=58.0).summer_lockout_oat_f == 58


def _f(rule, severity="fault", **m):
    return Finding(rule=rule, equip="PLANT", severity=severity, metrics=m, summary="")


def test_recommendations_for_both_lockout_rules():
    assert RECOMMENDERS["boiler_summer_lockout"] is RECOMMENDERS["hw_pump_summer_lockout"]
    pump = recommend(
        _f("hw_pump_summer_lockout", summer_run_pct=70.0, lockout_oat_f=65.0, boiler_off_pct=100.0)
    )
    assert pump.title == "Lock out the hot-water pump in warm weather"
    assert pump.cause.endswith("with the boiler off") and "65°F" in pump.cause
    assert pump.references == ["pnnl-guide-plant-heating", "pnnl-retuning-ch8"]
    boiler = recommend(_f("boiler_summer_lockout", "warn", summer_run_pct=8.0, lockout_oat_f=60.0))
    assert boiler.title == "Lock out the boiler in warm weather" and "60°F" in boiler.suggested
    assert recommend(_f("boiler_summer_lockout", "ok")) is None
    bare = recommend(_f("hw_pump_summer_lockout"))
    assert bare.cause == "Hot-water pump running in warm weather" and bare.suggested == ""


def test_walkdown_items_and_design_values():
    for rule in ("boiler_summer_lockout", "hw_pump_summer_lockout"):
        assert "*" in SITE_CHECKS[rule]
        assert DESIGN_PARAMS[rule][0][0] == "summer_lockout_oat_f"
        assert cause_key(_f(rule)) == "*"


def test_triage_groups_the_boiler_and_its_pump_on_one_plant():
    groups = group_findings([_f("hw_pump_summer_lockout"), _f("boiler_summer_lockout", "warn")])
    assert len(groups) == 1 and groups[0].primary_rule == "boiler_summer_lockout"
    assert [m.rule for m in groups[0].members] == [
        "boiler_summer_lockout",
        "hw_pump_summer_lockout",
    ]


def test_soo_plant_sequence_locks_the_pump_out_too():
    seq = g36_plant_sequence(summer_lockout_oat_f=60.0)
    assert [c.name for c in seq] == ["boiler_summer_lockout", "hw_pump_summer_lockout"]
    oat = np.array([80.0] * 120 + [50.0] * 80)
    f = pd.DataFrame(
        {Role.OAT: oat, Role.BOILER_STATUS: (oat < 60).astype(float), Role.PUMP_STATUS: 1.0},
        index=_idx(200),
    )
    rep = evaluate_soo(f, seq, "HWP")
    sev = {c.name: c.severity for c in rep.clauses}
    assert sev == {"boiler_summer_lockout": "ok", "hw_pump_summer_lockout": "fault"}


def test_faultlab_scores_it_pending_sign_off():
    from camber.eval import benchmark

    assert "hw_pump_summer_lockout" in faultlab.PENDING_SCENARIOS
    assert "hw_pump_summer_lockout" not in faultlab.SCENARIOS  # no gated key without sign-off
    pend = faultlab.PENDING_SCENARIOS
    rep = benchmark(faultlab.labeled_records(scenarios=pend), faultlab.targets(pend))
    c = rep.per_detector["hw_pump_summer_lockout"]
    assert (c.true_positive_rate, c.false_positive_rate) == (1.0, 0.0)
    assert faultlab.cross_fire(scenarios=pend) == {}
    # nor does it fire on any gated scenario's faulty frame
    assert not [k for k, v in faultlab.cross_fire().items() if "hw_pump_summer_lockout" in v]


# --------------------------------------------------------------------------- the shared lockout


def _cfg(tmp_path, rules):
    from camber.store import ParquetStore

    oat = _oat()
    boiler = (oat < 60).astype(float)
    frame = pd.DataFrame(
        {Role.OAT: oat, Role.BOILER_STATUS: boiler, Role.PUMP_STATUS: (oat <= 70).astype(float)},
        index=_idx(),
    )
    st = ParquetStore(str(tmp_path / "s"))
    st.write_role_frame(frame, facility_id="f1", equip="HWP", equip_class="HW_PLANT")
    st.register_facility("f1")
    return {
        "source": {"kind": "store", "store": st.root, "facility_id": "f1"},
        "resample": "1h",
        "equipment": [{"class": "HW_PLANT", "marker_role": "oat"}],
        "rules": rules,
    }


def test_the_pump_inherits_the_boilers_lockout(tmp_path):
    from camber.config import run_config

    cfg = _cfg(
        tmp_path,
        [
            {"name": "boiler_summer_lockout", "params": {"summer_lockout_oat_f": 62.0}},
            "hw_pump_summer_lockout",
        ],
    )
    got = {f.rule: f for f in run_config(cfg, base_dir=str(tmp_path)).findings}
    pump = got["hw_pump_summer_lockout"]
    assert pump.metrics["lockout_oat_f"] == 62.0 and pump.severity == "fault"
    assert pump.metrics["param_basis"]["summer_lockout_oat_f"] == {
        "value": 62.0,
        "basis": "inherited from boiler_summer_lockout in this config",
    }
    assert "param_basis" not in got["boiler_summer_lockout"].metrics
    # the pump's own value wins
    cfg["rules"][1] = {"name": "hw_pump_summer_lockout", "params": {"summer_lockout_oat_f": 75}}
    got = {f.rule: f for f in run_config(cfg, base_dir=str(tmp_path)).findings}
    assert got["hw_pump_summer_lockout"].metrics["lockout_oat_f"] == 75.0
    assert got["hw_pump_summer_lockout"].severity == "ok"
    assert "param_basis" not in got["hw_pump_summer_lockout"].metrics


def test_rcx_overrides_count_an_inherited_lockout_as_site_configured():
    from camber.report.rcx import _rule_param_overrides

    cfg = {"rules": [{"name": "boiler_summer_lockout", "params": {"summer_lockout_oat_f": 58}}]}
    assert _rule_param_overrides(cfg) == {
        "boiler_summer_lockout": {"summer_lockout_oat_f": 58},
        "hw_pump_summer_lockout": {"summer_lockout_oat_f": 58},
    }
    assert _rule_param_overrides({"rules": ["boiler_summer_lockout"]}) == {}
