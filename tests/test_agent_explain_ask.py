"""Tests for grounded explanation & Q&A (camber.agent.explain / camber.agent.ask).

Covers all four paths: no LLM (deterministic template), well-behaved stub (grounded LLM answer),
misbehaving stub (flagged/repaired, grounded=False), and fully-hallucinated stub (falls back to
template).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.base import Finding  # noqa: E402
from camber.fault_economics import EquipmentLoad, EnergyPrice  # noqa: E402
from camber.agent import explain, ask, stub_client, build_context  # noqa: E402

_LOADS = {"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200, cooling_tons=50)}


def _findings():
    return [Finding(rule="simultaneous_heat_cool", equip="AHU-1", severity="fault",
                    metrics={"simultaneous_hc_pct": 20.0}, summary="Both coils open 20% of hours.")]


def _explain(**kw):
    return explain(_findings(), loads=_LOADS, price=EnergyPrice(), **kw)


# --------------------------------------------------------------------------- explain

def test_explain_without_llm_is_grounded_template():
    g = _explain()
    assert g.source == "template" and g.grounded
    assert "[F1]" in g.text and "[C1]" in g.text

def test_explain_accepts_single_finding():
    g = explain(_findings()[0], loads=_LOADS, price=EnergyPrice())
    assert g.grounded and "[F1]" in g.text


def test_explain_with_wellbehaved_stub_is_grounded_llm():
    stub = stub_client("[F1] The coils are open together ~20% of hours; fix the sequencing [R1].")
    g = _explain(client=stub)
    assert g.source == "llm" and g.grounded
    assert set(g.cited) == {"F1", "R1"} and g.flagged == []


def test_explain_with_misbehaving_stub_flags_and_repairs():
    stub = stub_client("[Z9] AHU-1 wastes $88,888. [F1] The coils fight.")
    g = _explain(client=stub, strict=True)
    assert g.source == "llm" and not g.grounded
    reasons = {f["reason"] for f in g.flagged}
    assert "unknown-citation" in reasons and "uncited-number" in reasons
    assert "88,888" not in g.text and "[Z9]" not in g.text     # repaired
    assert "[F1]" in g.text                                     # good part kept


def test_explain_with_fully_hallucinated_stub_falls_back_to_template():
    stub = stub_client("[Z9] Everything costs $99,999 and nothing here is real.")
    g = _explain(client=stub, strict=True)
    assert g.source == "template" and g.grounded           # gutted -> deterministic answer


def test_explain_nonstrict_keeps_llm_text_but_marks_ungrounded():
    stub = stub_client("[Z9] AHU-1 wastes $88,888.")
    g = _explain(client=stub, strict=False)
    assert g.source == "llm" and not g.grounded
    assert "88,888" in g.text                               # non-strict does not repair


def test_explain_empty_findings():
    g = explain([], price=EnergyPrice())
    assert g.grounded and g.source == "template"


# --------------------------------------------------------------------------- ask

def test_ask_without_llm_routes_and_grounds():
    g = ask("what should I do about AHU-1?", findings=_findings(), loads=_LOADS, price=EnergyPrice())
    assert g.source == "template" and g.grounded and "[R1]" in g.text


def test_ask_accepts_prebuilt_context():
    ctx = build_context(_findings(), loads=_LOADS, price=EnergyPrice())
    g = ask("what will this cost?", ctx)
    assert g.grounded and "[C1]" in g.text and "$" in g.text


def test_ask_with_stub_is_grounded_llm():
    ctx = build_context(_findings(), loads=_LOADS, price=EnergyPrice())
    stub = stub_client("[C1] The estimated annual cost is about $2,897.")
    g = ask("cost?", ctx, client=stub)
    assert g.source == "llm" and g.grounded and g.cited == ["C1"]


def test_ask_with_hallucinated_stub_falls_back():
    ctx = build_context(_findings(), loads=_LOADS, price=EnergyPrice())
    stub = stub_client("It costs exactly $12,345,678 [Q1].")
    g = ask("cost?", ctx, client=stub, strict=True)
    assert g.source == "template" and g.grounded


def test_ask_unknown_topic_is_honest_and_grounded():
    ctx = build_context(None)
    g = ask("what is the airspeed of a swallow?", ctx)
    assert g.grounded and "don't know" in g.text.lower()
