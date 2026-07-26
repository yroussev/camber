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
from camber.scorecard import build_scorecard  # noqa: E402
from camber.model.entities import completeness, TEMPLATES  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.mapping_confidence import review  # noqa: E402
from camber.agent import (  # noqa: E402
    build_context, Fact, Context, facts_from_scorecard, facts_from_completeness,
    facts_from_history, facts_from_mapping,
)
from camber.agent.context import facts_from_findings  # noqa: E402
from camber.agent.verify import check  # noqa: E402


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


# --------------------------------------------------------------------------- other builders (step 5)

class _FakeReadAPI:
    """Duck-typed ReadAPI for facts_from_history — returns long-form rows."""

    def history(self, **kw):
        return {"history": [
            {"ts": "2024-07-01T00:00:00", "equip": "AHU-1", "role": "oat", "value": 60.0},
            {"ts": "2024-07-01T01:00:00", "equip": "AHU-1", "role": "oat", "value": 90.0},
            {"ts": "2024-07-01T02:00:00", "equip": "AHU-1", "role": "oat", "value": None}],
            "count": 3}


def test_scorecard_facts_overall_plus_weak_categories():
    sc = build_scorecard(_findings())
    facts = facts_from_scorecard(sc)
    assert facts[0].kind == "scorecard" and "grade" in facts[0].text.lower()
    assert facts[0].data["overall_grade"] == sc.overall_grade


def test_completeness_fact_only_when_required_role_missing():
    tmpl = TEMPLATES["AHU"]
    ready = completeness("AHU", list(tmpl.required))          # fully instrumented -> no fact
    missing = completeness("AHU", list(tmpl.required)[:1])    # missing required -> a fact
    assert facts_from_completeness([ready]) == []
    facts = facts_from_completeness([missing])
    assert len(facts) == 1 and facts[0].kind == "completeness"
    assert "missing required" in facts[0].text


def test_history_facts_are_bounded_stats_not_raw():
    facts = facts_from_history(_FakeReadAPI())
    assert len(facts) == 1
    f = facts[0]
    assert f.kind == "history" and f.data["count"] == 2       # the None sample is dropped
    assert f.data["min"] == 60.0 and f.data["max"] == 90.0 and f.data["mean"] == 75.0
    assert "history" not in f.data and "values" not in f.data  # never the raw series


def test_mapping_facts_from_review():
    mp = MappingProvider.from_dict({"aliases": {"OAT": "oat"}, "patterns": []})
    facts = facts_from_mapping(review(["OAT", "Mystery"], mp))
    assert any("Mystery" in f.text and "unmapped" in f.text for f in facts)
    assert all(f.kind == "mapping" for f in facts)


def test_unified_context_has_unique_ids_and_self_grounds():
    sc = build_scorecard(_findings())
    comp = completeness("AHU", list(TEMPLATES["AHU"].required)[:1])
    mp = MappingProvider.from_dict({"aliases": {}, "patterns": []})
    ctx = build_context(_findings(), scorecard=sc, completeness=[comp],
                        read_api=_FakeReadAPI(), mapping_review=review(["Mystery"], mp))
    assert len(set(ctx.ids())) == len(ctx.ids())             # ids unique across all builders
    # every fact, cited on its own, verifies grounded (numbers traceable to itself)
    assert all(check(f"[{f.id}] {f.text}", ctx).grounded for f in ctx.facts)


def test_run_context_summary_and_findings():
    class _Run:
        site = "Demo"
        equipment = 3
        rules_run = ["a", "b"]
        findings = _findings()
    ctx = build_context(run=_Run(), price=EnergyPrice())
    run_facts = ctx.by_kind("run")
    assert len(run_facts) == 1 and "3 equipment" in run_facts[0].text
    assert ctx.site == "Demo" and len(ctx.by_kind("finding")) == len(_findings())


# --- portfolio / fleet facts (0.5) ------------------------------------------ #

def _fleet():
    from camber.report.fleet import build_fleet_report
    b1 = {"site": "Building A", "eui": 120.0, "findings": _findings()}
    b2 = {"site": "Building B", "eui": 65.0,
          "findings": [Finding(rule="economizer_high_limit", equip="AHU-2", severity="warn",
                               summary="econ")]}
    return build_fleet_report([b1, b2], peer_median_eui=90.0)


def test_facts_from_fleet_summary_and_per_building():
    from camber.agent import facts_from_fleet
    facts = facts_from_fleet(_fleet())
    assert facts[0].kind == "fleet" and facts[0].equip == ""       # summary fact
    per = [f for f in facts if f.equip]
    assert {f.equip for f in per} == {"Building A", "Building B"}
    assert all(f.kind == "fleet" for f in facts)


def test_build_context_fleet_sets_multisite_and_grounds():
    ctx = build_context(fleet=_fleet())
    assert ctx.site == ["Building A", "Building B"]                # multi-site
    for f in ctx.by_kind("fleet"):
        assert check(f"[{f.id}] {f.text}", ctx).grounded          # every figure traceable
    assert len(set(ctx.ids())) == len(ctx.ids())


def test_build_context_runs_list_multisite():
    class _Run:
        def __init__(self, site): self.site = site; self.equipment = 1; self.rules_run = ["r"]; \
            self.findings = _findings()
    ctx = build_context(runs=[_Run("S1"), _Run("S2")], price=EnergyPrice())
    assert ctx.site == ["S1", "S2"] and len(ctx.by_kind("run")) == 2
