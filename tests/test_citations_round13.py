"""Round 13 of the citation checker (PLAN-CITE-EVAL, measured regime).

1. Fact texts state what hosts quote from the result's JSON: a trust score that the 2-place
   rounding hides (``1.00 (0.9977)``), a finding's true/false flags and null metrics
   (``fan_gated true``, ``null: oa_cost_excess_cool_kbtu``), an M&V saving's two totals.
2. A caveat given for some of a rule's results is about the units it names, not the rule.
3. Fact ids are never shortened: a bare id stays an unknown citation, and its why names the
   full id; the tool description and the prompt rules say so.
4. A pro-form "one" in a negation stands for the fact's noun ("not one tuned for this site").
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities import default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import _unknown_why  # noqa: E402
from camber.capabilities._cap_prompts import _CITE_RULES  # noqa: E402
from camber.capabilities._cap_sensor_trust import _score  # noqa: E402
from camber.capabilities._common import _metric_flags, caveat_facts, finding_fact  # noqa: E402
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite13-"))


def _verdicts(judge, draft, *facts):
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": list(facts),
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return [(s[1], s[2]) for s in r["sentences"]]


# --------------------------------------------------------------------------- 1. fact texts


def test_trust_score_shows_the_value_the_rounding_hides():
    assert _score(0.9977) == "1.00 (0.9977)"
    assert _score(0.73) == "0.73"
    assert _score(1.0) == "1.00"


def test_finding_fact_states_flags_and_null_metrics():
    row = {
        "equip": "AHU-1",
        "rule": "leaking_valve",
        "severity": "fault",
        "finding_id": "leaking_valve@AHU-1",
        "summary": "AHU-1: HW-leak 45% of both-valves-shut samples",
        "metrics": {"hw_leak_pct": 45.0, "fan_gated": True, "x_kbtu": None, "_hidden": None},
    }
    text = finding_fact("F2@rule_run", row)["text"]
    assert text.endswith("; fan_gated true; null: x_kbtu")
    assert _metric_flags({}) == ""
    many = {f"m{i}": None for i in range(9)}
    assert _metric_flags(many).endswith("m5 and 3 more")


def test_mv_saving_states_its_projected_and_actual_totals(tmp_path):
    facts = facttable.collect(str(tmp_path))
    savings = [
        f for f in facts["mv_summary"] if f.get("rule") == "mv_savings" and f["kind"] == "finding"
    ]
    assert savings and all(
        "; baseline projected " in f["text"] and ", reporting actual " in f["text"] for f in savings
    )


def test_trust_and_flag_sentences_are_supported(judge):
    st = {
        "id": "F4@sensor_trust-3741e0",
        "kind": "point_trust",
        "equip": "AHU-1",
        "role": "supply_air_temp",
        "value": 0.9977,
        "text": '"AHU-1" "supply_air_temp": trust 1.00 (0.9977) (suspect; reference_warn); '
        "coverage 100% of its own span",
    }
    for fig, want in (("0.9977", "supported"), ("1.00", "supported"), ("0.9978", "fail")):
        got = _verdicts(judge, f"AHU-1 supply_air_temp scores {fig} [F4@sensor_trust-3741e0].", st)
        assert (got[0][0] == "supported") == (want == "supported"), (fig, got)


# --------------------------------------------------------------------------- 2. caveat scope


def _rows(n):
    return [
        {
            "rule": "reheat_penalty",
            "equip": f"VAV-{i}",
            "finding_id": f"reheat_penalty@VAV-{i}",
            "caveats": ["not evaluated: no OAT or supply air temperature"],
        }
        for i in range(1, n + 1)
    ]


def test_caveat_of_some_results_says_so():
    part = caveat_facts("rule_run", _rows(3), "site", {"reheat_penalty": 145})[0]
    assert part["of"] == 145 and " caveat (given for 3 of its 145 results): " in part["text"]
    whole = caveat_facts("rule_run", _rows(3), "site", {"reheat_penalty": 3})[0]
    assert "of" not in whole and " caveat: " in whole["text"]
    assert "of" not in caveat_facts("rule_run", _rows(3), "site")[0]


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("- Reheat penalty: not evaluated, no OAT or supply air temperature", False),
        ("- Reheat penalty on VAV-2: not evaluated, no OAT or supply air temperature", True),
        ("- Reheat penalty: not evaluated for these boxes, no OAT or supply air temperature", True),
        ("- Reheat penalty: not evaluated for 5 boxes, no OAT or supply air temperature", False),
    ],
)
def test_partial_caveat_is_read_of_its_units(judge, draft, ok):
    v = caveat_facts("rule_run", _rows(3), "site", {"reheat_penalty": 145})[0]
    v["id"] = "V1@rule_run-b84b96"
    got = _verdicts(judge, f"{draft} [V1@rule_run-b84b96].", v)
    assert (got[0][0] == "supported") == ok, got


def test_whole_rule_caveat_needs_no_units(judge):
    v = caveat_facts("rule_run", _rows(3), "site", {"reheat_penalty": 3})[0]
    v["id"] = "V1@rule_run-b84b96"
    got = _verdicts(
        judge,
        "- Reheat penalty: not evaluated, no OAT or supply air temperature [V1@rule_run-b84b96].",
        v,
    )
    assert got[0][0] == "supported", got


# --------------------------------------------------------------------------- 3. short ids


def test_bare_id_why_names_the_full_id():
    why = _unknown_why("F5", ["F5@rule_run-aaaaaa", "F5@rule_run-bbbbbb", "F6@x-cccccc"])
    assert why.endswith("cite the full id, e.g. F5@rule_run-bbbbbb")
    assert "e.g. F5@rule_run-…" in _unknown_why("F5", [])
    assert (
        _unknown_why("F5@rule_run-zzzzzz", []) == "F5@rule_run-zzzzzz is not a fact of this session"
    )


def test_bare_id_is_still_an_unknown_citation(judge):
    f = {"id": "F2@rule_run-54307c", "kind": "finding", "value": "fault", "text": "x"}
    got = _verdicts(judge, "AHU-1 failed economizer_high_limit [F2].", f)
    assert got[0][0] == "unknown_citation" and "F2@rule_run-54307c" in got[0][1], got


def test_never_shorten_is_in_the_description_and_the_rules():
    desc = default_registry().get("check_citations").description
    assert "Never shorten fact ids: cite each id exactly as issued." in desc
    assert len(desc) < 600
    assert "Never shorten a fact id" in _CITE_RULES and "exactly as issued" in _CITE_RULES


# --------------------------------------------------------------------------- 4. pro-form one

N1 = {
    "id": "N1@rule_run-a19ce0",
    "kind": "notice",
    "text": "No run config names this facility, so this run uses the config template packaged "
    'for catalog dataset "synthetic-ahu-sensor-bias": its equipment classes, rules and '
    "parameters, not a config tuned for your own use of the data.",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The run used a packaged config template, not one tuned for this site", True),
        ("The run used a packaged config template, not one written for this site", False),
        ("The run used a tuned config, not one packaged for this site", False),
    ],
)
def test_pro_form_one_stands_for_the_facts_noun(judge, draft, ok):
    got = _verdicts(judge, f"{draft} [N1@rule_run-a19ce0].", N1)
    assert (got[0][0] == "supported") == ok, got
