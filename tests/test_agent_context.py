"""Tests for the agent grounding surface (camber.agent.context).

Covers: deterministic/order-stable ids, cost facts that never fabricate a dollar figure when
uncosted, recommendation facts only for actionable findings, root-cause facts only for real
(multi-member) groups, and full JSON round-trip of the Context.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.base import Finding  # noqa: E402
from camber.fault_economics import EquipmentLoad, EnergyPrice  # noqa: E402
from camber.agent import build_context, Fact, Context  # noqa: E402
from camber.agent.context import facts_from_findings  # noqa: E402


def _findings():
    return [
        Finding(rule="simultaneous_heat_cool", equip="AHU-1", severity="fault",
                metrics={"simultaneous_hc_pct": 20.0}, summary="Both coils open 20% of hours."),
        Finding(rule="unmet_setpoint_hours", equip="Z-1", severity="warn",
                metrics={"unmet_pct": 12.0}, summary="Zone unmet 12% of occupied hours."),
        Finding(rule="sensor_flatline", equip="Z-2", severity="ok", summary="Sensor OK."),
    ]


def test_ids_are_deterministic_and_order_stable():
    a = build_context(_findings(), price=EnergyPrice())
    b = build_context(_findings(), price=EnergyPrice())
    assert a.ids() == b.ids()
    # per-kind prefixes in input order
    assert a.ids()[:2] == ["F1", "C1"]
    assert all(f.id == a.by_id(f.id).id for f in a.facts)


def test_every_finding_gets_a_finding_and_cost_fact():
    ctx = build_context(_findings(), price=EnergyPrice())
    assert len(ctx.by_kind("finding")) == 3
    assert len(ctx.by_kind("cost")) == 3
    # finding text is the deterministic summary, verbatim
    assert ctx.by_kind("finding")[0].text == "Both coils open 20% of hours."


def test_uncosted_fact_states_basis_not_dollars():
    ctx = build_context(_findings(), price=EnergyPrice())
    # unmet_setpoint_hours has no cost model -> uncosted -> no dollar figure
    unmet_cost = next(f for f in ctx.by_kind("cost") if f.equip == "Z-1")
    assert "$" not in unmet_cost.text
    assert unmet_cost.data["costed"] is False
    assert "no cost model" in unmet_cost.text


def test_costed_fact_carries_a_dollar_figure():
    ctx = build_context(_findings(),
                        loads={"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200, cooling_tons=50)},
                        price=EnergyPrice())
    hc_cost = next(f for f in ctx.by_kind("cost") if f.equip == "AHU-1")
    assert hc_cost.data["costed"] is True and "$" in hc_cost.text


def test_recommendation_facts_only_for_actionable():
    ctx = build_context(_findings(), price=EnergyPrice())
    rec_equips = {f.equip for f in ctx.by_kind("recommendation")}
    assert "AHU-1" in rec_equips and "Z-1" in rec_equips   # fault + warn
    assert "Z-2" not in rec_equips                         # ok -> no recommendation


def test_no_rootcause_fact_for_solo_findings():
    ctx = build_context(_findings(), price=EnergyPrice())
    assert ctx.by_kind("rootcause") == []


def test_rootcause_fact_for_multi_member_group():
    # two findings on the same AHU that share a causal chain -> one grouped root-cause fact
    fs = [
        Finding(rule="supply_air_reset", equip="AHU-9", severity="warn", summary="No SAT reset."),
        Finding(rule="simultaneous_heat_cool", equip="AHU-9", severity="fault", summary="Coil fight."),
    ]
    ctx = build_context(fs, price=EnergyPrice())
    groups = ctx.by_kind("rootcause")
    assert len(groups) == 1
    assert groups[0].equip == "AHU-9"
    assert groups[0].data["primary_rule"] == "supply_air_reset"   # most-upstream = presumed cause
    assert len(groups[0].data["members"]) == 2


def test_prompt_block_lists_every_fact_with_its_id():
    ctx = build_context(_findings(), price=EnergyPrice())
    block = ctx.to_prompt_block()
    for f in ctx.facts:
        assert f"[{f.id}]" in block
    # one line per fact
    assert block.count("\n") == len(ctx.facts) - 1


def test_context_json_round_trips():
    ctx = build_context(_findings(),
                        loads={"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200)},
                        price=EnergyPrice(), site="Demo Site")
    d = ctx.as_dict()
    s = json.dumps(d)                       # must not raise
    back = json.loads(s)
    assert back["site"] == "Demo Site"
    assert [f["id"] for f in back["facts"]] == ctx.ids()


def test_by_kind_and_by_equip_filters():
    ctx = build_context(_findings(), price=EnergyPrice())
    assert all(f.kind == "finding" for f in ctx.by_kind("finding"))
    assert all(f.equip == "AHU-1" for f in ctx.by_equip("AHU-1"))
    assert ctx.by_id("nope") is None


def test_empty_context():
    ctx = build_context(None)
    assert isinstance(ctx, Context) and ctx.facts == [] and ctx.to_prompt_block() == ""
    assert ctx.ids() == []


def test_fact_is_frozen_and_serializable():
    f = Fact("F1", "finding", "AHU-1", "text", {"a": 1})
    assert f.as_dict() == {"id": "F1", "kind": "finding", "equip": "AHU-1",
                           "text": "text", "data": {"a": 1}}
