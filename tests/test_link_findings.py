"""link_findings: issues with sensor precedence, union hours, max-not-sum cost, stable rank."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import EnergyPrice, EquipmentLoad, FaultCost  # noqa: E402
from camber.integrate.tickets import fingerprint  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.triage import (  # noqa: E402
    Issue,
    finding_confidence,
    issue_totals,
    link_findings,
    sensor_causes,
)
from camber.sensorhealth import ConsistencyResult, SensorTrust  # noqa: E402

REG = builtin_registry()
IDX = pd.date_range("2026-03-02", periods=48, freq="h")


def _f(rule, equip="DemoAHU", severity="warn", **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics, summary=rule)


def _cost(f, usd):
    costed = usd is not None
    return FaultCost(
        rule=f.rule,
        equip=f.equip,
        severity=f.severity,
        electricity_kwh=0.0,
        gas_therms=0.0,
        annual_cost_usd=float(usd or 0.0),
        basis="test-model" if costed else "needs EquipmentLoad.heating_capacity_kbtuh",
        costed=costed,
    )


def _mask(hours):
    return pd.Series(IDX.hour.isin(hours), index=IDX)


def test_oat_drift_makes_every_oat_consumer_conditional_never_deleted():
    drift = _f("sensor_drift:oat", equip="weather", severity="fault")
    econ = _f("economizer_high_limit")  # OAT is required
    oaf_other = _f("outdoor_air_fraction", equip="OtherAHU")  # OAT optional, another unit
    leak = _f("leaking_valve")  # no OAT
    issues = link_findings([drift, econ, oaf_other, leak], rules=REG)
    by_rule = {i.root.rule: i for i in issues}
    assert len(issues) == 4  # nothing deleted
    assert by_rule["economizer_high_limit"].conditional
    assert by_rule["outdoor_air_fraction"].conditional  # shared role -> every equipment
    assert not by_rule["leaking_valve"].conditional
    assert not by_rule["sensor_drift:oat"].conditional  # not conditional on itself
    assert {f.rule for f in by_rule["sensor_drift:oat"].dependents} == {
        "economizer_high_limit",
        "outdoor_air_fraction",
    }
    cond = by_rule["economizer_high_limit"]
    assert cond.confidence == "L"
    assert any("conditional on sensor_drift:oat" in w for w in cond.why)


def test_conditional_cost_is_at_risk_not_in_the_firm_total():
    drift = _f("sensor_drift:oat", equip="weather", severity="fault")
    econ, leak = _f("economizer_high_limit"), _f("leaking_valve")
    fs = [drift, econ, leak]
    costs = [_cost(drift, None), _cost(econ, 500.0), _cost(leak, 200.0)]
    t = issue_totals(link_findings(fs, rules=REG, costs=costs))
    assert t["annual_cost_usd"] == 200.0 and t["at_risk_usd"] == 500.0
    assert t["n_conditional"] == 1 and t["n_uncosted"] == 1


def test_non_shared_sensor_cause_stays_on_its_equipment():
    trust = {"DemoAHU": {"supply_air_temp": SensorTrust("supply_air_temp", 100, 1.0, 0.9, 0, 0,
                                                        0.3, "untrusted", ["stuck"])}}  # fmt: skip
    a = _f("leaking_valve", equip="DemoAHU")
    b = _f("leaking_valve", equip="OtherAHU")
    issues = link_findings([a, b], rules=REG, trust=trust)
    cond = {i.equip: i.conditional for i in issues}
    assert cond == {"DemoAHU": True, "OtherAHU": False}
    causes = sensor_causes([], trust=trust)
    assert causes[0].kind == "trust" and "stuck" in causes[0].detail


def test_mixing_violation_taints_mat_rat_oat_on_that_unit_only():
    mixing = {"DemoAHU": ConsistencyResult("mixing_temperature_order", 100, 0.3, "fault", "x")}
    a, b = _f("outdoor_air_fraction", equip="DemoAHU"), _f("outdoor_air_fraction", equip="Other")
    cond = {i.equip: i.conditional for i in link_findings([a, b], rules=REG, mixing=mixing)}
    assert cond == {"DemoAHU": True, "Other": False}


def test_reheat_minimization_g36_joins_its_sat_chain():
    fs = [_f("reheat_minimization_g36", severity="fault"), _f("supply_air_reset")]
    (issue,) = link_findings(fs, rules=REG)
    assert issue.rules == ["supply_air_reset", "reheat_minimization_g36"]
    assert issue.chain == "sat" and issue.severity == "fault"
    assert issue.key == fingerprint("", "DemoAHU", "supply_air_reset")


def test_chain_cost_is_the_max_member_never_the_sum():
    fs = [_f("supply_air_reset"), _f("reheat_penalty"), _f("simultaneous_heat_cool")]
    costs = [_cost(fs[0], None), _cost(fs[1], 300.0), _cost(fs[2], 100.0)]
    (issue,) = link_findings(fs, rules=REG, costs=costs)
    assert issue.cost == 300.0
    assert "largest shown" in issue.cost_basis_note
    assert issue_totals([issue])["annual_cost_usd"] == 300.0


def test_hours_are_a_union_of_member_masks_and_runtime_uses_fan_on_hours():
    fs = [_f("supply_air_reset"), _f("reheat_penalty")]
    masks = {id(fs[0]): _mask(range(8, 12)), id(fs[1]): _mask(range(10, 14))}  # overlap 10-11
    fan = _mask(range(6, 20))
    (issue,) = link_findings(
        fs, rules=REG, mask_for=lambda f: masks.get(id(f)), runtime=lambda e: (fan, "fan status")
    )
    assert issue.hours_union == 2 * 6  # 08-13 on two days, never 2 * (4 + 4)
    assert issue.fan_on_hours == 2 * 14 and issue.fan_gate == "fan status"
    assert issue.pct_runtime == round(100 * 12 / 28, 1)
    # a violation while the fan is off is not counted
    (gated,) = link_findings(
        fs, rules=REG, mask_for=lambda f: _mask([2, 3]), runtime=lambda e: (fan, "fan status")
    )
    assert gated.hours_union == 0.0
    (no_mask,) = link_findings(fs, rules=REG)
    assert no_mask.hours_union is None and no_mask.pct_runtime is None


def test_rank_is_severity_then_dollars_with_uncosted_last_and_deterministic():
    fs = [
        _f("leaking_valve", equip="A", severity="warn"),
        _f("leaking_valve", equip="B", severity="warn"),
        _f("leaking_valve", equip="C", severity="warn"),
        _f("filter_fouling", equip="D", severity="fault"),
    ]
    costs = [_cost(fs[0], None), _cost(fs[1], 50.0), _cost(fs[2], 900.0), _cost(fs[3], None)]
    issues = link_findings(fs, rules=REG, costs=costs)
    assert [i.equip for i in issues] == ["D", "C", "B", "A"]
    assert [i.rank for i in issues] == [1, 2, 3, 4]
    assert issues[-1].cost_basis_note == "uncosted — needs heating_capacity_kbtuh"
    again = link_findings(list(reversed(fs)), rules=REG, costs=list(reversed(costs)))
    assert [i.key for i in again] == [i.key for i in issues]


def test_exclude_cost_and_costs_computed_from_loads():
    f = _f("supply_air_reset_compliance", pct_below_g36_target=80.0, mean_gap_f=4.0)
    loads = {"DemoAHU": EquipmentLoad(heating_capacity_kbtuh=500.0)}
    (costed,) = link_findings([f], rules=REG, loads=loads, price=EnergyPrice())
    assert costed.cost and costed.cost > 0
    (excl,) = link_findings(
        [f], rules=REG, loads=loads, exclude_cost=lambda _f: "no site sequence known"
    )
    assert excl.cost is None and "excluded from $" in excl.cost_basis_note
    assert "no site sequence known" in excl.cost_basis_note


def test_finding_confidence_is_the_minimum_component_with_a_why_line_each():
    f = _f("economizer_high_limit", n_above_limit=500)
    trusted = SensorTrust("oat", 500, 1.0, 0.0, 0.0, 0.0, 0.95, "trusted", [])
    suspect = SensorTrust("oa_damper", 500, 1.0, 0.0, 0.1, 0.0, 0.6, "suspect", [])
    c = finding_confidence(
        f,
        trust={"oat": trusted, "oa_damper": suspect},
        mapping=("H", "roles recorded at ingest"),
        assumptions=("H", "site-configured parameters"),
        corroboration={"tpr": 1.0, "fpr": 0.0, "track": "synthetic"},
    )
    assert c.level == "M" and c.components["input_trust"] == "M"
    assert c.components == {
        "input_trust": "M",
        "mapping": "H",
        "assumptions": "H",
        "sample": "H",
        "corroboration": "H",
    }
    assert len(c.why) == 5
    small = finding_confidence(_f("x", n=10))
    assert small.level == "L" and small.components == {"sample": "L"}
    assert any("not assessed" in w for w in small.why)
    low_cov = finding_confidence(_f("x", n=500), coverage=0.4)
    assert low_cov.components["sample"] == "L"
    mid = finding_confidence(_f("x"), sample_n=500, coverage=0.7,
                             corroboration={"tpr": 0.5, "fpr": 0.2})  # fmt: skip
    assert mid.components == {"sample": "M", "corroboration": "M"}


def test_uncosted_issues_of_one_tier_rank_by_confidence_then_key():
    """0.102 (#112): within a tier, uncosted issues rank H, M, L, then ungraded, then by key;
    costed issues keep their dollar order whatever their confidence."""
    equips = ["A", "B", "C", "D", "E", "F", "G"]
    fs = [_f("leaking_valve", equip=e) for e in equips]
    usd = {"A": None, "B": None, "C": None, "D": None, "E": 10.0, "F": 900.0, "G": None}
    grade = {"A": "L", "B": "M", "C": "H", "D": "M", "E": "H", "F": "L", "G": "?"}
    costs = [_cost(f, usd[f.equip]) for f in fs]

    def conf(i):
        c = finding_confidence(i.root)
        c.level = grade[i.equip]
        return c

    issues = link_findings(fs, rules=REG, costs=costs, confidence_for=conf)
    m = sorted(["B", "D"], key=lambda e: next(i.key for i in issues if i.equip == e))
    assert [i.equip for i in issues] == ["F", "E", "C", *m, "A", "G"]
    again = link_findings(
        list(reversed(fs)), rules=REG, costs=list(reversed(costs)), confidence_for=conf
    )
    assert [i.key for i in again] == [i.key for i in issues]


def test_confidence_for_hook_and_issue_properties():
    f = _f("leaking_valve")
    (issue,) = link_findings(
        [f],
        rules=REG,
        confidence_for=lambda i: finding_confidence(i.root, assumptions=("L", "reference only")),
        facility_id="demo-fac",
    )
    assert isinstance(issue, Issue) and issue.confidence == "L"
    assert issue.key == fingerprint("demo-fac", "DemoAHU", "leaking_valve")
    assert not issue.costed and issue.confidence_components["assumptions"] == "L"
    # non-actionable findings are left out unless asked for
    assert link_findings([_f("leaking_valve", severity="ok")]) == []
    assert len(link_findings([_f("leaking_valve", severity="ok")], actionable_only=False)) == 1


def test_rules_may_be_a_map_or_an_iterable():
    drift = _f("sensor_drift:oat", equip="weather", severity="warn")
    econ = _f("economizer_high_limit")
    rule = REG.get("economizer_high_limit")
    for rules in ({"economizer_high_limit": rule}, [rule]):
        by = {i.root.rule: i for i in link_findings([drift, econ], rules=rules)}
        assert by["economizer_high_limit"].conditional
