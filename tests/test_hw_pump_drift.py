"""Tests for the hot-water pump / loop drift family (0.105, #152).

The ``hw_pump`` family is the ``pump`` family's five detectors on the hot-water roles. The physics
is shared, so the pumpsim cases -- remapped onto the hot-water roles -- must score exactly as the
chilled-water suite scores them; the hot-water detectors carry their own rule names and store kinds
so one equipment can carry both loops; and the ``pump`` family itself is untouched.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftrun import DRIFT_FAMILIES, build_drift_suite, run_drift  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.pumpdrift import diagnose_pump_drift  # noqa: E402
from camber.pumpsim import FAULTS, build_pump_suite, simulate_case  # noqa: E402
from camber.rules import param_docs  # noqa: E402
from camber.scorecard import RULE_CATEGORY  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A chilled-water loop's warm side is its return, a hot-water loop's its supply: remapping return
# -> HW supply and supply -> HW return keeps the warm - cool ΔT, so the physics is unchanged.
_TO_HW = {
    Role.CHW_FLOW: Role.HW_FLOW,
    Role.CHW_PUMP_SPEED: Role.HW_PUMP_SPEED,
    Role.CHW_RETURN_TEMP: Role.HW_SUPPLY_TEMP,
    Role.CHW_SUPPLY_TEMP: Role.HW_RETURN_TEMP,
    Role.CHW_DIFF_PRESS: Role.HW_DIFF_PRESS,
    Role.CHW_DIFF_PRESS_SP: Role.HW_DIFF_PRESS_SP,
}
_HW_NAMES = [
    "hw_pump_flow_drift",
    "hw_pump_head_drift",
    "hw_loop_deltat_drift",
    "hw_loop_dp_drift",
    "hw_pump_power_drift",
]


def _hw(frame):
    return frame.rename(columns=_TO_HW)


# --------------------------------------------------------------------------- suite membership


def test_the_family_builds_the_five_detectors_on_the_hot_water_roles():
    suite = build_drift_suite("hw_pump", BaselineStore())
    assert [r.name for r in suite] == _HW_NAMES
    assert [r.kind for r in suite] == [n[: -len("_drift")] for n in _HW_NAMES]
    flow, head, dt, dp, power = suite
    assert (flow.flow_role, flow.speed_role, flow.dp_role, flow.dp_sp_role) == (
        Role.HW_FLOW,
        Role.HW_PUMP_SPEED,
        Role.HW_DIFF_PRESS,
        Role.HW_DIFF_PRESS_SP,
    )
    assert (head.speed_role, head.flow_role) == (Role.HW_PUMP_SPEED, Role.HW_FLOW)
    assert dt.roles_required == (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP, Role.HW_FLOW)
    assert (dp.dp_role, dp.flow_role, dp.sp_role) == (
        Role.HW_DIFF_PRESS,
        Role.HW_FLOW,
        Role.HW_DIFF_PRESS_SP,
    )
    assert power.roles_required == (Role.POWER, Role.HW_FLOW)
    for rule in suite:
        roles = set(rule.roles_required) | set(rule.roles_optional)
        assert not any(r.value.startswith("chw_") for r in roles), rule.name
    assert DRIFT_FAMILIES["hw_pump"].diagnose is diagnose_pump_drift


def test_the_pump_family_is_unchanged():
    suite = build_drift_suite("pump", BaselineStore())
    assert [r.name for r in suite] == [n[len("hw_") :] for n in _HW_NAMES]
    assert [r.kind for r in suite] == [
        "pump_flow",
        "pump_head",
        "loop_deltat",
        "loop_dp",
        "pump_power",
    ]
    assert suite[2].roles_required == (Role.CHW_RETURN_TEMP, Role.CHW_SUPPLY_TEMP, Role.CHW_FLOW)
    assert [type(r) for r in build_pump_suite(BaselineStore())] == [type(r) for r in suite]


def test_role_overrides_still_win():
    """A hot-water loop with no flow meter can normalize its ΔT on the pump speed."""
    from camber.rules.hw_pump_rules import HwLoopDeltaTDrift

    rule = HwLoopDeltaTDrift(BaselineStore(), load_role=Role.HW_PUMP_SPEED)
    assert rule.roles_required == (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP, Role.HW_PUMP_SPEED)


def test_the_hot_water_detectors_are_documented_and_categorized():
    for hw in _HW_NAMES:
        chw = param_docs.HW_PUMP_DRIFT_TWINS[hw]
        assert param_docs.DRIFT_PARAM_DOCS[hw] is param_docs.DRIFT_PARAM_DOCS[chw]
        hw_params = [(p.name, p.default) for p in param_docs.drift_rule_params(hw)]
        assert hw_params == [(p.name, p.default) for p in param_docs.drift_rule_params(chw)]
        assert RULE_CATEGORY[hw] == RULE_CATEGORY[chw] == "maintenance"


# --------------------------------------------------------------------------- the physics


@pytest.mark.parametrize("fault", [None, *FAULTS])
def test_a_hot_water_pumpsim_case_scores_as_the_chilled_water_one(fault):
    """Same data on the hot-water roles -> the same severities, metrics and loop verdict."""
    case = simulate_case(fault, 0 if fault is None else 4, seed=7)
    chw = [
        r.analyze_periods(case.equip, case.baseline, case.current)
        for r in build_pump_suite(BaselineStore())
    ]
    hw = [
        r.analyze_periods(case.equip, _hw(case.baseline), _hw(case.current))
        for r in build_drift_suite("hw_pump", BaselineStore(), site="SIM", run_id="SIM")
    ]
    assert [f.rule for f in hw] == _HW_NAMES
    for c, h in zip(chw, hw):
        assert h.severity == c.severity, (fault, h.rule)
        assert h.metrics == pytest.approx(c.metrics, nan_ok=True), (fault, h.rule)
    d_chw = diagnose_pump_drift(chw, equip=case.equip)
    d_hw = diagnose_pump_drift(hw, equip=case.equip)
    assert (d_hw.severity, d_hw.locus, d_hw.causes) == (d_chw.severity, d_chw.locus, d_chw.causes)
    assert d_hw.locus == case.expected_locus or fault is None
    # signals are keyed by the rule each Finding carries, so a hot-water verdict names its rules
    assert all(k.startswith("hw_") for k in d_hw.signals)


def test_hot_water_faults_are_detected():
    for fault in ("impeller_wear", "clogged_strainer", "overpumping", "bearing_drag"):
        case = simulate_case(fault, 4, seed=11)
        suite = build_drift_suite("hw_pump", BaselineStore())
        fs = [r.analyze_periods("HWP_1", _hw(case.baseline), _hw(case.current)) for r in suite]
        d = diagnose_pump_drift(fs, equip="HWP_1")
        assert d.severity in ("warn", "fault"), fault
        assert d.locus == FAULTS[fault].expected_locus, fault
    steady = simulate_case(None, 0, seed=11)
    suite = build_drift_suite("hw_pump", BaselineStore())
    fs = [r.analyze_periods("HWP_1", _hw(steady.baseline), _hw(steady.current)) for r in suite]
    assert diagnose_pump_drift(fs).locus == "steady"


# --------------------------------------------------------------------------- one equip, two loops


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _token(role) -> str:
    return "".join(p.capitalize() for p in role.value.split("_"))


def _two_loop_site(tmp_path):
    """PLANT_1 carries a healthy chilled-water loop and an overpumped hot-water loop."""
    trends = os.path.join(str(tmp_path), "trends")
    os.makedirs(trends, exist_ok=True)
    chw = simulate_case(None, 0, seed=3)
    hw = simulate_case("overpumping", 4, seed=5)
    loop_roles = list(_TO_HW)  # the loop-specific roles; status / head / power are shared points
    aliases = {}
    for case, rename in ((chw, {}), (hw, _TO_HW)):
        frame = pd.concat([case.baseline, case.current])[loop_roles].rename(columns=rename)
        for col in frame.columns:
            aliases[_token(col)] = col.value
            _write_point(trends, "PLANT_1", _token(col), frame[col])
    return trends, MappingProvider.from_dict({"aliases": aliases})


def _run_both(trends, mapping, store, **kw):
    from camber.resolve import discover

    refs = {"PLANT": discover([trends], "PLANT", marker_measure=_token(Role.HW_FLOW))}
    return run_drift(
        refs,
        mapping,
        store=store,
        families=[{"class": "PLANT", "family": "pump"}, {"class": "PLANT", "family": "hw_pump"}],
        baseline=("2025-05-01", "2025-05-30"),
        current=("2025-06-01", "2025-07-01"),
        site="T",
        run_id="R",
        **kw,
    )


def test_one_equipment_keeps_a_baseline_per_loop(tmp_path):
    trends, mapping = _two_loop_site(tmp_path)
    store = BaselineStore()
    _run_both(trends, mapping, store, freeze_if_missing=True)
    kinds = {rec.kind for rec in store.records() if rec.equip == "PLANT_1"}
    assert {"pump_flow", "loop_deltat", "loop_dp"} <= kinds
    assert {"hw_pump_flow", "hw_loop_deltat", "hw_loop_dp"} <= kinds
    assert (
        store.model_for("T", "PLANT_1", "loop_deltat").as_dict()
        != store.model_for("T", "PLANT_1", "hw_loop_deltat").as_dict()
    )

    res = _run_both(trends, mapping, store)
    chw, hw = res.families
    assert (chw.family, hw.family) == ("pump", "hw_pump")
    assert hw.label == "Hot-water pump / loop drift"
    assert {f.rule for f in chw.findings}.isdisjoint({f.rule for f in hw.findings})
    assert [d.locus for d in chw.diagnoses] == ["steady"]
    assert [d.locus for d in hw.diagnoses] == ["distribution"]
    assert "hw_loop_deltat_drift" in hw.diagnoses[0].signals
    dt = next(f for f in hw.findings if f.rule == "hw_loop_deltat_drift")
    assert dt.severity in ("warn", "fault") and "low-ΔT syndrome" in dt.summary


def test_the_plant_roll_up_reaches_the_hot_water_family(tmp_path):
    trends, mapping = _two_loop_site(tmp_path)
    from camber.resolve import discover

    refs = {"PLANT": discover([trends], "PLANT", marker_measure=_token(Role.HW_FLOW))}
    store = BaselineStore()
    kw = {
        "store": store,
        "families": [{"class": "PLANT", "family": "hw_pump", "plant": "HW plant"}],
        "baseline": ("2025-05-01", "2025-05-30"),
        "current": ("2025-06-01", "2025-07-01"),
    }
    run_drift(refs, mapping, freeze_if_missing=True, **kw)
    plant = run_drift(refs, mapping, **kw).families[0].plant
    assert plant is not None and plant.plant == "HW plant" and plant.n_degrading == 1
