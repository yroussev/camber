"""Tests for per-fault dollar impact (camber.fault_economics)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import (  # noqa: E402
    EnergyPrice, EquipmentLoad, FaultCost, annotate_costs, cost_findings,
    estimate_cost, rank_by_cost, total_cost,
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
    assert fc.gas_therms > 0 and fc.electricity_kwh > 0      # reheat gas + paired cooling
    assert fc.annual_cost_usd > 0
    assert "paired-cooling" in fc.basis
    assert fc.assumptions["faulted_frac"] == 0.20


def test_uncosted_when_sizing_missing():
    f = _f("simultaneous_heat_cool", "AHU-2", "fault", simultaneous_hc_pct=30.0)
    fc = estimate_cost(f, EquipmentLoad(), PRICE)              # no heating capacity
    assert not fc.costed
    assert fc.annual_cost_usd == 0.0
    assert "heating_capacity_kbtuh" in fc.basis               # says what it needs


def test_chiller_uses_finding_metrics_directly():
    # rule already measured kw/ton, design target, tons -> no EquipmentLoad needed
    f = _f("chiller_efficiency", "CH-1", "warn", kw_per_ton_median=0.85,
           design_kw_per_ton=0.60, tons_median=200.0, pct_hours_inefficient=50.0)
    fc = estimate_cost(f, None, EnergyPrice())
    assert fc.costed and fc.electricity_kwh > 0 and fc.gas_therms == 0
    # excess 0.25 kW/ton * 200 tons * (0.5 * 8760 h) = 219,000 kWh
    assert abs(fc.electricity_kwh - 0.25 * 200 * 0.5 * 8760) < 1.0
    assert fc.assumptions["excess_kw_per_ton"] == 0.25


def test_chiller_no_penalty_when_efficient():
    f = _f("chiller_efficiency", "CH-2", "info", kw_per_ton_median=0.55,
           design_kw_per_ton=0.60, tons_median=200.0, pct_hours_inefficient=10.0)
    fc = estimate_cost(f, None, PRICE)
    assert fc.electricity_kwh == 0.0 and not fc.costed         # below target -> no excess


def test_no_model_falls_back_to_explicit_then_uncosted():
    explicit = _f("some_future_rule", "X", "warn", waste_kwh=1000.0)
    fc = estimate_cost(explicit, None, PRICE)
    assert fc.costed and abs(fc.annual_cost_usd - 150.0) < 1e-6  # 1000 kWh * $0.15
    bare = _f("some_future_rule", "Y", "warn", foo=1)
    fc2 = estimate_cost(bare, None, PRICE)
    assert not fc2.costed and "no cost model" in fc2.basis


def test_boiler_cycle_costs_gas_only():
    f = _f("boiler_short_cycle", "B-1", "warn", starts_per_day=12.0,
           max_starts_per_day=6.0, runtime_pct=40.0)
    fc = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=2000.0), PRICE)
    assert fc.costed and fc.gas_therms > 0 and fc.electricity_kwh == 0
    assert fc.assumptions["extra_starts_per_day"] == 6.0


def test_params_override_changes_estimate():
    f = _f("simultaneous_heat_cool", "AHU-3", "fault", simultaneous_hc_pct=20.0)
    load = EquipmentLoad(heating_capacity_kbtuh=100.0, annual_hours=4000)
    base = estimate_cost(f, load, PRICE)
    doubled = estimate_cost(f, load, PRICE, params={"reheat_diversity": 0.60})
    assert doubled.gas_therms > base.gas_therms * 1.9          # 2x diversity ~ 2x gas


def test_rank_by_cost_orders_by_dollars_across_severity():
    # a cheap "fault" vs an expensive "warn" -> dollar ranking puts the expensive one first
    cheap = _f("simultaneous_heat_cool", "small", "fault", simultaneous_hc_pct=5.0)
    pricey = _f("chiller_efficiency", "big", "warn", kw_per_ton_median=0.9,
                design_kw_per_ton=0.6, tons_median=500.0, pct_hours_inefficient=60.0)
    loads = {"small": EquipmentLoad(heating_capacity_kbtuh=50.0, annual_hours=2000)}
    ranked = rank_by_cost([cheap, pricey], loads, PRICE, costed_only=True)
    assert ranked[0].equip == "big" and ranked[0].annual_cost_usd > ranked[1].annual_cost_usd


def test_annotate_then_triage_ranks_within_severity():
    f1 = _f("chiller_efficiency", "CH-A", "warn", kw_per_ton_median=0.8,
            design_kw_per_ton=0.6, tons_median=100.0, pct_hours_inefficient=40.0)
    f2 = _f("chiller_efficiency", "CH-B", "warn", kw_per_ton_median=0.9,
            design_kw_per_ton=0.6, tons_median=300.0, pct_hours_inefficient=60.0)
    annotate_costs([f1, f2], None, PRICE)
    assert "annual_cost_usd" in f1.metrics and f2.metrics["annual_cost_usd"] > 0
    ranked = rank_findings([f1, f2], magnitude_key="annual_cost_usd")
    assert ranked[0].finding.equip == "CH-B"                    # bigger dollar impact first


def test_total_cost_rollup():
    findings = [
        _f("chiller_efficiency", "CH-1", "warn", kw_per_ton_median=0.8,
           design_kw_per_ton=0.6, tons_median=100.0, pct_hours_inefficient=40.0),
        _f("simultaneous_heat_cool", "AHU-1", "fault", simultaneous_hc_pct=10.0),  # uncosted (no load)
    ]
    costs = cost_findings(findings, None, PRICE)
    roll = total_cost(costs)
    assert roll["n_costed"] == 1 and roll["n_uncosted"] == 1
    assert roll["annual_cost_usd"] > 0
