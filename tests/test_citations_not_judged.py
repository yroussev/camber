"""check_citations' not-judged units (0112-cite12): text the checker does not read is reported
apart, never counted as a pass. A heading that says something of the lines under it and a short
label or remark that cites nothing are ``not_judged`` (counted in ``n_not_judged``, ``grounded``
false, left out of ``verified_text``); pure structure is no unit at all. The facts are made up.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.agent.verify import _NOT_CHECKED, check  # noqa: E402
from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import (  # noqa: E402
    CAPABILITIES,
    REMOVED,
    _context,
    check_citations,
)
from camber.capabilities._cap_prompts import _CITE_RULES  # noqa: E402

REG = default_registry()
CTX = SimpleNamespace(allowed_facilities=frozenset({"site-a"}), store=None)
LEAK = {
    "id": "F1@rule_run-aaaaaa",
    "kind": "finding",
    "value": "fault",
    "rule": "leaking_valve",
    "equip": "AHU-1",
    "facility_id": "site-a",
    "text": '"AHU-1" leaking_valve (fault): the heating valve leaks on 60% of hours',
}
OK2 = {
    "id": "F2@rule_run-aaaaaa",
    "kind": "finding",
    "value": "ok",
    "rule": "leaking_valve",
    "equip": "AHU-2",
    "facility_id": "site-a",
    "text": '"AHU-2" leaking_valve (ok): no valve leak',
}
FACTS = [LEAK, OK2]
GOOD = "AHU-1's heating valve leaks on 60% of hours [F1@rule_run-aaaaaa]."


def _check(text: str, **kw) -> dict:
    return check_citations(CTX, {"text": text, "facts": FACTS, "detail": True, **kw}).data


def _nj(data: dict) -> list:
    return [s["text"] for s in data["sentences"] if s["verdict"] == "not_judged"]


# --------------------------------------------------------------------------- not judged


@pytest.mark.parametrize(
    "line",
    [
        "## Faulted units",  # a verdict word
        "## Points that never move",  # a negation, in a relative clause too
        "**What is not fine**",
        "**Other suspect points:**",
        "## All units",  # a quantifier
        "Both units also share these quirks:",
        "**Meters with a baseline that fails G14 (information only):**",
        "### Worst offenders",  # a rank word
    ],
)
def test_a_heading_that_says_something_of_its_lines_is_not_judged(line):
    data = _check(f"{line}\n\n- {GOOD}\n")
    assert _nj(data) == [line]
    assert data["grounded"] is False and data["n_not_judged"] == 1
    assert data["verdicts"]["not_judged"] == 1 and data["verdicts"]["supported"] == 1
    row = next(s for s in data["sentences"] if s["verdict"] == "not_judged")
    assert row["why"].startswith("a heading that says something of the lines under it")
    # its place is marked, so the item under it keeps its section; checked again, the mark is
    # structure and the rest passes
    vt = data["verified_text"]
    assert line not in vt and _NOT_CHECKED in vt and f"- {GOOD}" in vt
    again = _check(vt)
    assert again["grounded"] and again["n_not_judged"] == 0 and again["n_sentences"] == 1


def test_a_heading_mark_keeps_its_markup():
    vt = _check(f"## Faulted units\n{GOOD}\n**Worst units**\n{GOOD}\n")["verified_text"]
    # a removed heading gave a verdict: the NOTICE line opens the text
    assert vt.startswith(REMOVED + "\n\n")
    assert vt[len(REMOVED) + 2 :].splitlines() == [
        f"## {_NOT_CHECKED}",
        GOOD,
        f"**{_NOT_CHECKED}**",
        GOOD,
    ]


@pytest.mark.parametrize(
    "draft, unit",
    [
        (f"{GOOD} That matches the claim.", "That matches the claim."),
        (f"{GOOD}\n- **Coverage:** They're identical.\n", "- **Coverage:** They're identical."),
        (f"{GOOD}\n- AHU-1 leaking_valve: FAULT.\n", "- AHU-1 leaking_valve: FAULT."),
        (f"{GOOD}\n- **Healthy:** it sees very little demand.\n", None),
        (f"{GOOD}\nThe inputs came through as trusted.\n", None),
        (f"{GOOD}\n- AHU-2: OK.\n", None),
        (f"{GOOD}\n2. Stop 2: the member boxes.\n", None),  # a figure in a title
    ],
)
def test_a_label_or_remark_citing_nothing_is_not_judged(draft, unit):
    data = _check(draft)
    got = _nj(data)
    assert len(got) == 1 and (unit is None or got == [unit])
    assert data["grounded"] is False and data["n_not_judged"] == 1
    row = next(s for s in data["sentences"] if s["verdict"] == "not_judged")
    assert row["why"].startswith("a short label or remark that cites nothing")
    assert row["cited"] == [] and row["unknown"] == []
    assert got[0] not in data["verified_text"] and GOOD in data["verified_text"]


def test_a_label_that_cites_a_fact_is_judged_like_any_sentence():
    data = _check("- AHU-1 leaking_valve: FAULT [F1@rule_run-aaaaaa].\n")
    assert data["n_not_judged"] == 0 and data["grounded"]
    data = _check("- AHU-2 leaking_valve: FAULT [F2@rule_run-aaaaaa].\n")
    assert data["n_not_judged"] == 0 and data["verdicts"]["unsupported_claim"] == 1
    data = _check("## Faulted units [F1@rule_run-aaaaaa]\n")
    assert data["n_not_judged"] == 0


def test_an_uncited_table_row_is_not_judged_and_its_line_goes():
    draft = f"| Unit | Result |\n|---|---|\n| AHU-2 | OK |\n| AHU-1 | {GOOD} |\n"
    data = _check(draft)
    assert _nj(data) == ["| AHU-2 | OK |"]
    vt = f"| Unit | Result |\n|---|---|\n| AHU-1 | {GOOD} |\n"
    assert data["verified_text"] == f"{REMOVED}\n\n{vt}"  # the row gave a verdict


def test_a_remark_inside_a_table_cell_goes_and_the_row_keeps_its_cells():
    draft = f"| Unit | Result |\n|---|---|\n| AHU-1 | {GOOD} Treat it as a sensor problem. |\n"
    data = _check(draft)
    assert _nj(data) == ["Treat it as a sensor problem."]
    assert data["verified_text"].splitlines()[2] == f"| AHU-1 | {GOOD} |"


def test_not_judged_also_with_per_sentence_off():
    data = _check(f"{GOOD} That matches the claim.", per_sentence=False)
    assert data["n_not_judged"] == 1 and data["grounded"] is False


def test_the_result_says_so_in_its_fact_and_banner():
    res = check_citations(CTX, {"text": f"{GOOD} That matches the claim.", "facts": FACTS})
    assert "1 heading(s) or label(s) were not judged" in res.facts[0]["text"]
    assert any("were not judged" in b for b in res.banners)
    # without detail, only the failing units are listed: the not-judged one among them
    assert [s["verdict"] for s in res.data["sentences"]] == ["not_judged"]


def test_a_not_judged_verdict_or_caveat_label_opens_verified_text_with_the_notice():
    data = _check(f"{GOOD}\n- AHU-2: OK.\n")
    assert data["verified_text"].startswith("NOTICE: check_citations removed")


def test_the_registry_validates_the_new_fields(tmp_path):
    from camber.capabilities._ledger import FactLedger

    ledger = FactLedger()
    ledger.record(FACTS)
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset({"site-a"}), ledger=ledger)
    res = REG.call("check_citations", {"text": f"{GOOD} That matches.", "detail": True}, ctx)
    assert res.data["n_not_judged"] == 1 and res.data["verdicts"]["not_judged"] == 1


def test_the_tool_description_and_the_prompts_say_what_not_judged_is():
    assert "not_judged" in CAPABILITIES[0].description
    assert "is not judged and is left out" in _CITE_RULES


# --------------------------------------------------------------------------- structure


@pytest.mark.parametrize(
    "before",
    [
        "## Sensor trust",
        "**AHU-1 details**",
        "# AHU-1 triage",
        "## Savings claim check",
        "How far the inputs can be trusted:",
        "Here's the Slack message:",
        "- **Chiller staging:**",
        '- **Stop 1, "AHU-1":**',
        "---",
        "Hello,",
        "Hi team,",
        "Dear [Owner],",
        "**Q1.",
    ],
)
def test_structure_is_no_unit(before):
    text = f"{before}\n\n{GOOD}\n\nBest regards\n"
    data = _check(text)
    assert data["grounded"] and data["n_not_judged"] == 0 and data["n_sentences"] == 1
    assert data["verified_text"] == text


def test_a_tables_rule_and_column_header_are_no_unit():
    text = f"| Unit | Finding |\n|---|---|\n| AHU-1 | {GOOD} |\n"
    data = _check(text)
    assert data["grounded"] and data["n_not_judged"] == 0 and data["verified_text"] == text


def test_a_list_items_title_that_names_a_topic_is_no_unit():
    text = f"1. Sensor offset. {GOOD}\n"
    data = _check(text)
    assert data["grounded"] and data["n_not_judged"] == 0 and data["verified_text"] == text
    # a title with a verdict word, a colon, a figure or a pronoun is not a title
    for title in ("Faulty valve.", "AHU-1: Fully covered.", "Issue 2.", "Fine valve."):
        assert _check(f"1. {title} {GOOD}\n")["n_not_judged"] == 1, title
    # a sentence with a verb the checker reads is judged as before: uncited keeps its verdict
    data = _check(f"1. It leaks. {GOOD}\n")
    assert data["n_not_judged"] == 0 and data["verdicts"]["uncited"] == 1


def test_python_check_keeps_them_unread():
    # only check_citations reports not-judged units; check() (templates, RCx prose) as before
    g = check(f"{GOOD} That matches the claim.", _context(FACTS), require_cite_each_sentence=True)
    assert g.grounded and "That matches the claim." in g.text
