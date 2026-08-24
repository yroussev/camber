"""Tests for per-fault dollar impact (camber.fault_economics)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import (  # noqa: E402
    EnergyPrice,
    EquipmentLoad,
    FaultCost,
    annotate_costs,
    cost_findings,
    estimate_cost,
    rank_by_cost,
    total_cost,
)
from camber.rules.base import Finding  # noqa: E402
from camber.rules.triage import rank_findings  # noqa: E402

PRICE = EnergyPrice(electricity_per_kwh=0.15, gas_per_therm=1.20)


def _f(rule, equip, sev, **metrics):
    return Finding(rule=rule, equip=equip, severity=sev, metrics=metrics, summary="")


def test_simultaneous_hc_costs_gas_and_cooling():
    f = _f("simultaneous_heat_cool", "AHU-1", "fault", simultaneous_hc_pct=20.0)
    load = EquipmentLoad(heating_capacity_kbtuh=100.0, annual_hours=4000)
    fc = estimate_cost(f, load, PRICE)
    assert isinstance(fc, FaultCost) and fc.costed
    assert fc.gas_therms > 0 and fc.electricity_kwh > 0  # reheat gas + paired cooling
    assert fc.annual_cost_usd > 0
    assert "paired-cooling" in fc.basis
    assert fc.assumptions["faulted_frac"] == 0.20


def test_uncosted_when_sizing_missing():
    f = _f("simultaneous_heat_cool", "AHU-2", "fault", simultaneous_hc_pct=30.0)
    fc = estimate_cost(f, EquipmentLoad(), PRICE)  # no heating capacity
    assert not fc.costed
    assert fc.annual_cost_usd == 0.0
    assert "heating_capacity_kbtuh" in fc.basis  # says what it needs


def test_chiller_uses_finding_metrics_directly():
    # rule already measured kw/ton, design target, tons -> no EquipmentLoad needed
    f = _f(
        "chiller_efficiency",
        "CH-1",
        "warn",
        kw_per_ton_median=0.85,
        design_kw_per_ton=0.60,
        tons_median=200.0,
        pct_hours_inefficient=50.0,
    )
    fc = estimate_cost(f, None, EnergyPrice())
    assert fc.costed and fc.electricity_kwh > 0 and fc.gas_therms == 0
    # excess 0.25 kW/ton * 200 tons * (0.5 * 8760 h) = 219,000 kWh
    assert abs(fc.electricity_kwh - 0.25 * 200 * 0.5 * 8760) < 1.0
    assert fc.assumptions["excess_kw_per_ton"] == 0.25


def test_chiller_no_penalty_when_efficient():
    f = _f(
        "chiller_efficiency",
        "CH-2",
        "info",
        kw_per_ton_median=0.55,
        design_kw_per_ton=0.60,
        tons_median=200.0,
        pct_hours_inefficient=10.0,
    )
    fc = estimate_cost(f, None, PRICE)
    assert fc.electricity_kwh == 0.0 and not fc.costed  # below target -> no excess


def test_no_model_falls_back_to_explicit_then_uncosted():
    explicit = _f("some_future_rule", "X", "warn", waste_kwh=1000.0)
    fc = estimate_cost(explicit, None, PRICE)
    assert fc.costed and abs(fc.annual_cost_usd - 150.0) < 1e-6  # 1000 kWh * $0.15
    bare = _f("some_future_rule", "Y", "warn", foo=1)
    fc2 = estimate_cost(bare, None, PRICE)
    assert not fc2.costed and "no cost model" in fc2.basis


def test_boiler_cycle_costs_gas_only():
    f = _f(
        "boiler_short_cycle",
        "B-1",
        "warn",
        starts_per_day=12.0,
        max_starts_per_day=6.0,
        runtime_pct=40.0,
    )
    fc = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=2000.0), PRICE)
    assert fc.costed and fc.gas_therms > 0 and fc.electricity_kwh == 0
    assert fc.assumptions["extra_starts_per_day"] == 6.0


def test_params_override_changes_estimate():
    f = _f("simultaneous_heat_cool", "AHU-3", "fault", simultaneous_hc_pct=20.0)
    load = EquipmentLoad(heating_capacity_kbtuh=100.0, annual_hours=4000)
    base = estimate_cost(f, load, PRICE)
    doubled = estimate_cost(f, load, PRICE, params={"reheat_diversity": 0.60})
    assert doubled.gas_therms > base.gas_therms * 1.9  # 2x diversity ~ 2x gas


def test_rank_by_cost_orders_by_dollars_across_severity():
    # a cheap "fault" vs an expensive "warn" -> dollar ranking puts the expensive one first
    cheap = _f("simultaneous_heat_cool", "small", "fault", simultaneous_hc_pct=5.0)
    pricey = _f(
        "chiller_efficiency",
        "big",
        "warn",
        kw_per_ton_median=0.9,
        design_kw_per_ton=0.6,
        tons_median=500.0,
        pct_hours_inefficient=60.0,
    )
    loads = {"small": EquipmentLoad(heating_capacity_kbtuh=50.0, annual_hours=2000)}
    ranked = rank_by_cost([cheap, pricey], loads, PRICE, costed_only=True)
    assert ranked[0].equip == "big" and ranked[0].annual_cost_usd > ranked[1].annual_cost_usd


def test_annotate_then_triage_ranks_within_severity():
    f1 = _f(
        "chiller_efficiency",
        "CH-A",
        "warn",
        kw_per_ton_median=0.8,
        design_kw_per_ton=0.6,
        tons_median=100.0,
        pct_hours_inefficient=40.0,
    )
    f2 = _f(
        "chiller_efficiency",
        "CH-B",
        "warn",
        kw_per_ton_median=0.9,
        design_kw_per_ton=0.6,
        tons_median=300.0,
        pct_hours_inefficient=60.0,
    )
    annotate_costs([f1, f2], None, PRICE)
    assert "annual_cost_usd" in f1.metrics and f2.metrics["annual_cost_usd"] > 0
    ranked = rank_findings([f1, f2], magnitude_key="annual_cost_usd")
    assert ranked[0].finding.equip == "CH-B"  # bigger dollar impact first


def test_total_cost_rollup():
    findings = [
        _f(
            "chiller_efficiency",
            "CH-1",
            "warn",
            kw_per_ton_median=0.8,
            design_kw_per_ton=0.6,
            tons_median=100.0,
            pct_hours_inefficient=40.0,
        ),
        _f(
            "simultaneous_heat_cool", "AHU-1", "fault", simultaneous_hc_pct=10.0
        ),  # uncosted (no load)
    ]
    costs = cost_findings(findings, None, PRICE)
    roll = total_cost(costs)
    assert roll["n_costed"] == 1 and roll["n_uncosted"] == 1
    assert roll["annual_cost_usd"] > 0


# ---- Trim-and-Respond reset family cost models (0.62.0) ----


def test_sat_reset_compliance_costs_reheat():
    f = _f(
        "supply_air_reset_compliance", "AHU-1", "warn", pct_below_g36_target=60.0, mean_gap_f=5.0
    )
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=200.0), PRICE)
    assert c.costed
    assert c.gas_therms > 0 and c.electricity_kwh == 0
    assert "reheat" in c.basis
    assert c.assumptions["gap_scale"] == 1.0


def test_sat_reset_compliance_gap_scales_cost():
    load = EquipmentLoad(heating_capacity_kbtuh=200.0)
    full = estimate_cost(
        _f("supply_air_reset_compliance", "A", "warn", pct_below_g36_target=60, mean_gap_f=5.0),
        load,
        PRICE,
    )
    half = estimate_cost(
        _f("supply_air_reset_compliance", "A", "warn", pct_below_g36_target=60, mean_gap_f=2.5),
        load,
        PRICE,
    )
    assert abs(half.gas_therms - full.gas_therms / 2) < 1e-6  # gap halved -> cost halved


def test_sat_reset_compliance_uncosted_without_capacity():
    c = estimate_cost(
        _f("supply_air_reset_compliance", "A", "warn", pct_below_g36_target=60, mean_gap_f=5.0),
        EquipmentLoad(),
        PRICE,
    )
    assert not c.costed
    assert "heating_capacity_kbtuh" in c.basis


def test_static_reset_not_trimming_costs_fan():
    f = _f(
        "static_reset_effectiveness",
        "AHU-1",
        "warn",
        reason="not_trimming",
        reset="static",
        pct_idle_untrimmed=70.0,
    )
    c = estimate_cost(f, EquipmentLoad(fan_kw=20.0), PRICE)
    assert c.costed
    assert c.electricity_kwh > 0 and c.gas_therms == 0
    assert "fan" in c.basis


def test_static_reset_not_trimming_uncosted_without_fan_kw():
    f = _f(
        "static_reset_effectiveness",
        "A",
        "warn",
        reason="not_trimming",
        reset="static",
        pct_idle_untrimmed=70.0,
    )
    c = estimate_cost(f, EquipmentLoad(), PRICE)
    assert not c.costed
    assert "fan_kw" in c.basis


def test_sat_reset_not_trimming_costs_reheat():
    f = _f(
        "sat_reset_effectiveness",
        "A",
        "warn",
        reason="not_trimming",
        reset="sat",
        pct_idle_untrimmed=50.0,
    )
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=150.0), PRICE)
    assert c.costed and c.gas_therms > 0
    assert "reheat" in c.basis


def test_reset_not_responding_is_comfort_uncosted():
    f = _f("sat_reset_effectiveness", "A", "warn", reason="not_responding", reset="sat")
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=200.0), PRICE)
    assert not c.costed
    assert c.annual_cost_usd == 0.0  # no dollar fabricated for a comfort/capacity fault
    assert "comfort" in c.basis


def test_reset_stuck_and_diverges_uncosted():
    for reason, needle in (("stuck", "indeterminate"), ("diverges", "comfort")):
        c = estimate_cost(
            _f("static_reset_effectiveness", "A", "warn", reason=reason, reset="static"),
            EquipmentLoad(fan_kw=20.0),
            PRICE,
        )
        assert not c.costed and needle in c.basis


def test_reset_family_flows_through_rank_by_cost():
    # a new model participates in the money ranker with zero extra wiring
    findings = [
        _f("supply_air_reset_compliance", "AHU-1", "warn", pct_below_g36_target=60, mean_gap_f=5.0),
        _f(
            "static_reset_effectiveness",
            "AHU-2",
            "warn",
            reason="not_trimming",
            reset="static",
            pct_idle_untrimmed=80.0,
        ),
    ]
    loads = {
        "AHU-1": EquipmentLoad(heating_capacity_kbtuh=300.0),
        "AHU-2": EquipmentLoad(fan_kw=25.0),
    }
    ranked = rank_by_cost(findings, loads, PRICE)
    assert all(fc.costed for fc in ranked)
    assert ranked[0].annual_cost_usd >= ranked[1].annual_cost_usd  # sorted worst-dollars-first


def test_drift_family_uncosted_by_design():
    # drift is a leading recommission indicator, not a spend -> no cost model, uncosted
    c = estimate_cost(
        _f("chiller_drift", "CH-1", "warn", drift_f=1.5), EquipmentLoad(cooling_tons=300.0), PRICE
    )
    assert not c.costed
    assert "no cost model" in c.basis


# ---- fleet-rule (rogue / cohort) cost models + <fleet> load attribution (0.63.0) ----


def _fleet(rule, **metrics):
    return Finding(rule=rule, equip="<fleet>", severity="warn", metrics=metrics, summary="")


def test_rogue_static_costs_fan_when_grouped():
    f = _fleet(
        "static_rogue_zone_census",
        grouped=True,
        reset="static",
        worst_zone="Z1",
        worst_zone_share=0.8,
        rogue_by_group={"AHU1": ["Z1"]},
    )
    c = estimate_cost(f, EquipmentLoad(fan_kw=25.0), PRICE)
    assert c.costed and c.electricity_kwh > 0 and "fan" in c.basis
    assert c.assumptions["worst_zone_share"] == 0.8


def test_rogue_sat_costs_reheat_when_grouped():
    f = _fleet(
        "sat_rogue_zone_census",
        grouped=True,
        reset="sat",
        worst_zone="Z1",
        worst_zone_share=0.6,
        rogue_by_group={"AHU1": ["Z1"]},
    )
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=200.0), PRICE)
    assert c.costed and c.gas_therms > 0 and "reheat" in c.basis


def test_rogue_ungrouped_is_uncosted_screening():
    f = _fleet("static_rogue_zone_census", grouped=False, reset="static", worst_zone_share=0.8)
    c = estimate_cost(f, EquipmentLoad(fan_kw=25.0), PRICE)
    assert not c.costed and "ungrouped" in c.basis


def test_cohort_static_costs_fan():
    f = _fleet(
        "static_cohort_starvation",
        grouped=True,
        reset="static",
        worst_group="AHU1",
        worst_group_frac=0.9,
    )
    c = estimate_cost(f, EquipmentLoad(fan_kw=25.0), PRICE)
    assert c.costed and c.electricity_kwh > 0 and "fan" in c.basis


def test_cohort_sat_is_uncosted_comfort():
    f = _fleet(
        "sat_cohort_starvation", grouped=True, reset="sat", worst_group="AHU1", worst_group_frac=0.9
    )
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=200.0), PRICE)
    assert not c.costed and c.annual_cost_usd == 0.0 and "comfort" in c.basis


def test_fleet_load_attributed_by_worst_group():
    # two starved AHUs, only AHU1 sized -> AHU1 costs, AHU2 uncosted (needs its own fan_kw)
    findings = [
        _fleet(
            "static_cohort_starvation",
            grouped=True,
            reset="static",
            worst_group="AHU1",
            worst_group_frac=0.9,
        ),
        _fleet(
            "static_cohort_starvation",
            grouped=True,
            reset="static",
            worst_group="AHU2",
            worst_group_frac=0.9,
        ),
    ]
    fcs = cost_findings(findings, {"AHU1": EquipmentLoad(fan_kw=25.0)}, PRICE)
    assert fcs[0].costed and not fcs[1].costed


def test_rogue_load_attributed_via_rogue_by_group():
    # rogue finding: worst_zone -> its AHU via rogue_by_group -> that AHU's load
    f = _fleet(
        "static_rogue_zone_census",
        grouped=True,
        reset="static",
        worst_zone="Z7",
        worst_zone_share=0.7,
        rogue_by_group={"AHU2": ["Z7"]},
    )
    fcs = cost_findings([f], {"AHU2": EquipmentLoad(fan_kw=30.0)}, PRICE)
    assert fcs[0].costed and fcs[0].electricity_kwh > 0
