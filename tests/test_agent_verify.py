"""Tests for citation verification (camber.agent.verify) and the deterministic fallback
(camber.agent.templates).

The template layer is the grounding oracle: whatever it emits must verify clean. verify.check must
catch unknown citations and fabricated numbers, repair in strict mode, and only mark in non-strict.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.agent import (  # noqa: E402
    build_context,  # noqa: E402
    templates,
)
from camber.agent.verify import check, extract_cites  # noqa: E402
from camber.fault_economics import EnergyPrice, EquipmentLoad  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _ctx():
    fs = [
        Finding(
            rule="simultaneous_heat_cool",
            equip="AHU-1",
            severity="fault",
            metrics={"simultaneous_hc_pct": 20.0},
            summary="Both coils open 20% of hours.",
        ),
        Finding(
            rule="unmet_setpoint_hours",
            equip="Z-1",
            severity="warn",
            metrics={"unmet_pct": 12.0},
            summary="Zone unmet 12% of occupied hours.",
        ),
    ]
    return build_context(
        fs,
        loads={"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200, cooling_tons=50)},
        price=EnergyPrice(),
    )


# --------------------------------------------------------------------------- extraction


def test_extract_cites_dedupes_and_orders():
    assert extract_cites("see [F1] and [C2] and [F1] again") == ["F1", "C2"]
    assert extract_cites("no cites here") == []


# --------------------------------------------------------------------------- verify: clean cases


def test_clean_cited_text_is_grounded():
    ctx = _ctx()
    g = check("[F1] Both coils open 20% of hours.", ctx)
    assert g.grounded and g.flagged == [] and g.cited == ["F1"]
    assert len(g.facts) == 1 and g.facts[0].id == "F1"


def test_number_traceable_across_multi_sentence_fact():
    # a recommendation's [id] leads a paragraph; a later sentence's number is still traceable to it
    ctx = _ctx()
    rec = next(f for f in ctx.facts if f.kind == "recommendation")
    g = check(f"[{rec.id}] {rec.text}", ctx)
    assert g.grounded and g.flagged == []


def test_text_with_no_numbers_and_no_cites_is_grounded():
    ctx = _ctx()
    g = check("The air handler shows a coil conflict.", ctx)  # qualitative, no numbers -> fine
    assert g.grounded


# --------------------------------------------------------------------------- verify: bad cases


def test_unknown_citation_flagged_and_stripped_in_strict():
    ctx = _ctx()
    g = check("Both coils fight [Z9].", ctx, strict=True)
    assert not g.grounded
    assert any(f["reason"] == "unknown-citation" for f in g.flagged)
    assert "[Z9]" not in g.text


def test_fabricated_number_dropped_in_strict():
    ctx = _ctx()
    g = check("AHU-1 wastes $9,999 [Z9]. Both coils fight [F1].", ctx, strict=True)
    assert not g.grounded
    assert "9,999" not in g.text and "[Z9]" not in g.text
    assert "[F1]" in g.text  # the good sentence survives


def test_wrong_number_attributed_to_fact_is_caught():
    ctx = _ctx()
    # $50,000 is not in F1 (or any cited fact) -> untraceable even though [F1] is real
    g = check("[F1] The coils fight, wasting $50,000 a year.", ctx, strict=True)
    assert not g.grounded and "50,000" not in g.text


def test_non_strict_marks_but_keeps_text():
    ctx = _ctx()
    bad = "AHU-1 wastes $9,999 [Z9]. Both coils fight [F1]."
    g = check(bad, ctx, strict=False)
    assert g.text == bad and not g.grounded and g.flagged


def test_equipment_and_standard_tokens_are_not_numbers():
    ctx = _ctx()
    # "AHU-1", "Z-2", "G36", "62.1" appear but must not be flagged as untraceable numeric claims...
    g = check("AHU-1 and Z-2 relate to ASHRAE G36 guidance [F1].", ctx)
    assert g.grounded and g.flagged == []


def test_source_label_is_carried():
    ctx = _ctx()
    assert check("[F1] fine.", ctx, source="template").source == "template"
    assert check("[F1] fine.", ctx).source == "llm"


# --------------------------------------------------------------------------- templates (the oracle)


def test_template_explain_is_trivially_grounded():
    ctx = _ctx()
    text = templates.explain_from_facts(ctx)
    g = check(text, ctx, source="template")
    assert g.grounded and g.flagged == []
    assert "[F1]" in text and "[C1]" in text and "[R1]" in text


def test_template_explain_groups_by_equipment():
    ctx = _ctx()
    text = templates.explain_from_facts(ctx)
    assert text.startswith("AHU-1:") and "Z-1:" in text


def test_template_answer_routes_on_cost_keyword():
    ctx = _ctx()
    ans = templates.answer_from_facts("what will this cost per year?", ctx)
    assert "[C1]" in ans and "$" in ans
    assert check(ans, ctx, source="template").grounded


def test_template_answer_routes_on_fix_keyword_and_equipment():
    ctx = _ctx()
    ans = templates.answer_from_facts("what should I do about AHU-1?", ctx)
    assert "[R1]" in ans and "[R2]" not in ans  # scoped to AHU-1
    assert check(ans, ctx, source="template").grounded


def test_template_answer_dont_know_when_scope_empty():
    ctx = _ctx()
    # an equipment mentioned that has no cost fact of its own kind ... use a clearly-absent scope
    ans = templates.answer_from_facts("what should I do about AHU-404?", ctx)
    # AHU-404 isn't in the facts -> falls through to findings (still grounded), never fabricates
    assert check(ans, ctx, source="template").grounded


def test_template_answer_empty_context():
    empty = build_context(None)
    ans = templates.answer_from_facts("anything?", empty)
    assert "don't know" in ans.lower()
    assert check(ans, empty).grounded
