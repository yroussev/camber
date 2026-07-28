"""Tests for the prioritized action plan (camber.actionplan) + audit wiring."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.actionplan import (  # noqa: E402
    ActionItem,
    action_plan_html,
    action_plan_rows,
    build_action_plan,
)
from camber.fault_economics import EnergyPrice, EquipmentLoad  # noqa: E402
from camber.report.audit import AuditReport  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _findings():
    return [
        Finding(
            rule="simultaneous_heat_cool",
            equip="AHU-1",
            severity="fault",
            metrics={"simultaneous_hc_pct": 20.0},
            summary="both coils 20%",
        ),
        Finding(rule="supply_air_reset", equip="AHU-2", severity="warn", summary="no reset"),
        Finding(rule="co2_ventilation", equip="Z-1", severity="ok", summary="fine"),
    ]


_LOADS = {"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200, cooling_tons=40)}


def test_build_action_plan_ranks_by_cost_and_skips_non_actionable():
    plan = build_action_plan(_findings(), loads=_LOADS, price=EnergyPrice())
    assert len(plan) == 2  # ok finding skipped
    assert isinstance(plan[0], ActionItem)
    assert plan[0].rule == "simultaneous_heat_cool" and plan[0].costed
    assert plan[0].annual_cost_usd > 0
    assert plan[1].rule == "supply_air_reset"  # uncosted, ranks after the costed fault


def test_recommendation_attached_to_items():
    plan = build_action_plan(_findings(), loads=_LOADS)
    assert plan[0].recommendation is not None
    assert plan[0].recommendation.advisory is True
    assert plan[0].recommendation.standard  # grounded


def test_min_severity_fault_only():
    plan = build_action_plan(_findings(), loads=_LOADS, min_severity="fault")
    assert [a.severity for a in plan] == ["fault"]


def test_costed_only_drops_uncosted():
    plan = build_action_plan(_findings(), loads=_LOADS, costed_only=True)
    assert all(a.costed for a in plan) and len(plan) == 1


def test_action_plan_rows_and_html():
    plan = build_action_plan(_findings(), loads=_LOADS, price=EnergyPrice())
    rows = action_plan_rows(plan)
    assert rows[0]["rule"] == "simultaneous_heat_cool" and rows[0]["annual_cost_usd"] > 0
    assert rows[1]["annual_cost_usd"] is None  # uncosted -> None, not a fake number
    html = action_plan_html(plan)
    assert "<table" in html and "Recommended action" in html and "$" in html


def test_audit_recommend_section_toggle():
    rep = AuditReport(building="HQ", level=2)
    rep.add_findings(_findings())
    with_plan = rep.to_html(recommend=True, loads=_LOADS, price=EnergyPrice())
    without = rep.to_html()
    assert "Recommended actions" in with_plan
    assert "Recommended actions" not in without
