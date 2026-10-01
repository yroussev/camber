"""0.98 (#88 item 2): issue titles follow the finding's cause.

``free_cooling_missed`` records *why* free cooling was missed (the damper did not deliver what it
was told, or the economizer never commanded it open); every recommender names the finding's cause
in ``Recommendation.cause``; the RCx report heads each issue with it. Synthetic fixtures only."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.aso import DEFAULT_PARAMS, RECOMMENDERS, recommend  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.freecoolingmissed_rule import FreeCoolingMissed  # noqa: E402


def _frame(*, cmd, oaf, days=14, damper=True, temps=True):
    """Mild weather (OAT 45-55 °F, RAT 72 °F), cooling valve at 40 % all the time; ``oaf`` is the
    delivered outdoor-air fraction (0-1), ``cmd`` the damper command (%)."""
    idx = pd.date_range("2025-06-02", periods=days * 24, freq="1h")
    oat = pd.Series(50 + 5 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi), index=idx)
    rat = pd.Series(72.0, index=idx)
    cols = {Role.OAT: oat, Role.COOL_VALVE: pd.Series(40.0, index=idx)}
    if temps:
        cols[Role.RETURN_AIR_TEMP] = rat
        cols[Role.MIXED_AIR_TEMP] = oaf * oat + (1 - oaf) * rat
    if damper:
        cols[Role.OA_DAMPER] = pd.Series(float(cmd), index=idx)
    return pd.DataFrame(cols)


# ---------------------------------------------------------------- the rule's cause metrics


def test_damper_commanded_open_but_not_delivering():
    f = FreeCoolingMissed().analyze("AHU", _frame(cmd=100.0, oaf=0.05))
    m = f.metrics
    assert f.severity == "fault"
    assert m["missed_cause"] == "damper_not_delivering"
    assert m["commanded_open_pct"] == 100.0
    assert m["commanded_open_hours"] == 14 * 24
    assert m["commanded_open_oaf_median_pct"] == 5.0
    assert m["missed_damper_cmd_median_pct"] == 100.0
    assert (m["cmd_open_pct"], m["oaf_open_pct"], m["stuck_low_oaf_pct"]) == (90.0, 80.0, 30.0)


def test_economizer_not_commanded():
    m = FreeCoolingMissed().analyze("AHU", _frame(cmd=10.0, oaf=0.05)).metrics
    assert m["missed_cause"] == "economizer_not_commanded"
    assert m["commanded_open_pct"] == 0.0
    assert m["commanded_open_oaf_median_pct"] is None
    assert m["missed_damper_cmd_median_pct"] == 10.0
    # a damper command with no mixed/return air: every missed sample was commanded below open
    m = FreeCoolingMissed().analyze("AHU", _frame(cmd=10.0, oaf=0.05, temps=False)).metrics
    assert m["missed_cause"] == "economizer_not_commanded"
    assert m["commanded_open_pct"] is None


def test_no_damper_is_undetermined_and_too_few_hours_is_undetermined():
    m = FreeCoolingMissed().analyze("AHU", _frame(cmd=0, oaf=0.05, damper=False)).metrics
    assert m["missed_cause"] == "undetermined"
    # one day of the pattern, below the 24 h floor
    m = (
        FreeCoolingMissed(stuck_min_hours=48)
        .analyze("AHU", _frame(cmd=100.0, oaf=0.05, days=1))
        .metrics
    )
    assert m["missed_cause"] == "undetermined"
    assert m["commanded_open_pct"] == 100.0


def test_cause_metrics_leave_severity_and_missed_share_alone():
    fr = _frame(cmd=100.0, oaf=0.05)
    a = FreeCoolingMissed().analyze("AHU", fr)
    b = FreeCoolingMissed(stuck_min_share_pct=99.9, stuck_min_hours=1e9).analyze("AHU", fr)
    assert (a.severity, a.metrics["missed_pct"]) == (b.severity, b.metrics["missed_pct"])
    assert b.metrics["missed_cause"] == "undetermined"
    ok = FreeCoolingMissed().analyze("AHU", _frame(cmd=100.0, oaf=1.0))
    assert ok.severity == "ok" and ok.metrics["missed_cause"] is None


# ---------------------------------------------------------------- recommendations


def _fcm(**m):
    return Finding("free_cooling_missed", "AHU-1", "fault", dict(m))


def test_damper_not_delivering_asks_for_a_repair():
    r = recommend(
        _fcm(
            missed_cause="damper_not_delivering",
            commanded_open_pct=61.5,
            commanded_open_hours=1676.0,
            commanded_open_oaf_median_pct=4.4,
            cmd_open_pct=90.0,
        )
    )
    assert r.title == "Repair the outdoor-air damper or actuator"
    assert r.cause == "Outdoor-air damper not modulating (stuck low)"
    assert "commanded ≥90% open on 62% of the missed free-cooling hours (1,676 h)" in r.action
    assert "near 4%" in r.action
    assert r.references == ["pnnl-guide-economizer", "pnnl-retuning-ch6"]
    part = recommend(_fcm(missed_cause="damper_not_delivering", commanded_open_oaf_median_pct=67.5))
    assert part.cause == "Outdoor-air damper not modulating (stuck part open)"
    # the finding's own recorded threshold wins over the default
    own = recommend(
        _fcm(
            missed_cause="damper_not_delivering",
            commanded_open_oaf_median_pct=67.5,
            stuck_low_oaf_pct=70.0,
        )
    )
    assert own.cause.endswith("(stuck low)")
    assert DEFAULT_PARAMS["econ_stuck_low_oaf_pct"] == 30.0


def test_other_free_cooling_causes_keep_the_enable_advice():
    r = recommend(_fcm(missed_cause="economizer_not_commanded"))
    assert r.title == "Enable economizer free cooling"
    assert r.cause == "Economizer not commanded open in free-cooling weather"
    for m in ({"missed_cause": "undetermined"}, {}):
        r = recommend(_fcm(**m))
        assert r.title == "Enable economizer free cooling"
        assert r.cause == "Mechanical cooling in free-cooling weather"


def test_every_recommender_names_a_cause():
    for rule in RECOMMENDERS:
        r = recommend(Finding(rule, "X-1", "warn", {}))
        assert r is not None and r.cause, rule
        assert r.cause != r.title, rule
        assert r.as_dict()["cause"] == r.cause


def test_causes_follow_the_metrics():
    def cause(rule, **m):
        return recommend(Finding(rule, "X-1", "warn", dict(m))).cause

    assert cause("leaking_valve", hw_leak_pct=46.8, chw_leak_pct=2.0).startswith("Heating")
    assert cause("leaking_valve", hw_leak_pct=0.0, chw_leak_pct=60.5).startswith("Cooling")
    assert cause("hw_pump_dp_reset", pct_running_near_min=80.0) == (
        "Hot-water pump pinned at its minimum speed"
    )
    assert cause("chw_pump_dp_reset", pct_running_near_full=60.0, dp_sp_reset_present=False) == (
        "Chilled-water pump near full speed on a fixed DP setpoint"
    )
    assert cause("chw_plant_reset", low_deltaT_pct=40.0) == (
        "Low chilled-water loop ΔT (40% of running hours)"
    )
    assert cause("chw_plant_reset", chwst_reset_present=False) == (
        "Chilled-water supply temperature held flat"
    )
    # 0.98 wave 2: the branches 098-plant-chw added to the chilled-water plant recommender
    assert cause("chw_plant_reset", chwst_reset_direction="reverse", chwst_slope_per_F=0.76) == (
        "Chilled-water supply warms in hot weather (plant capacity or a reversed reset)"
    )
    # a constant-flow plant gets no low-deltaT advice: the flat-reset cause, not the deltaT one
    assert cause(
        "chw_plant_reset", flow_mode="constant", low_deltaT_pct=80.0, chwst_reset_present=False
    ) == ("Chilled-water supply temperature held flat")
    assert cause(
        "chw_plant_reset",
        flow_mode="constant",
        low_deltaT_pct=80.0,
        chwst_reset_direction="reverse",
    ).startswith("Chilled-water supply warms in hot weather")
    assert cause("supply_air_reset", reset_direction="rising_with_load").startswith(
        "Supply air rises with load"
    )
    assert cause("economizer_high_limit") == "Economizer open above the high limit"
    assert cause("outdoor_air_fraction", failure_mode="under_ventilation") == (
        "Outside air below the ventilation minimum"
    )
    assert cause("night_weekend_setback", fan_run_unoccupied_pct=87.0) == (
        "Fan runs 87% of unoccupied hours"
    )
    assert cause("dcv_verification", status="functioning", excess_at_low_demand_pct=80.0) == (
        "Outdoor air above its floor at low demand"
    )


# ---------------------------------------------------------------- the RCx report


def test_report_heads_issues_with_the_cause(tmp_path):
    from camber.report.rcx import _heading, _issue_dict

    rec = recommend(_fcm(missed_cause="damper_not_delivering", commanded_open_oaf_median_pct=4.4))
    assert _heading(rec, rec.title) == "Outdoor-air damper not modulating (stuck low)"
    assert _heading(None, "Leaking valve") == "Leaking valve"

    class _Iss:
        key, rank, equip, severity, chain = "k", 1, "AHU-1", "fault", "econ"
        root = _fcm(missed_cause="damper_not_delivering", commanded_open_oaf_median_pct=4.4)
        members = [root]
        rules = ["free_cooling_missed"]
        cost = None
        cost_basis_note = ""
        conditional_on: list = []
        dependents: list = []
        hours_union = fan_on_hours = pct_runtime = None
        fan_gate = ""
        confidence = "M"
        confidence_components: dict = {}
        why: list = []

    d = _issue_dict(_Iss())
    assert d["title"] == "Repair the outdoor-air damper or actuator"
    assert d["cause"] == "Outdoor-air damper not modulating (stuck low)"


def test_every_recommender_branch_sets_a_cause():
    """Every ``_rec(...)`` / ``Recommendation(...)`` built in camber.aso passes ``cause=``, so a
    branch added later (0.98 wave 2: the reversed chilled-water reset) cannot ship without one."""
    import ast
    import inspect

    import camber.aso as aso

    tree = ast.parse(inspect.getsource(aso))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in ("_rec", "Recommendation")
    ]
    assert len(calls) > 30
    missing = [
        n.lineno
        for n in calls
        if n.func.id == "_rec" and not any(k.arg in ("cause", None) for k in n.keywords)
    ]
    assert not missing, f"_rec(...) without cause= at camber/aso.py lines {missing}"
