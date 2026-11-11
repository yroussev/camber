"""What a result's JSON says beyond its facts is issued as facts (0112-cite10): a finding's
caveats (``V<n>``, one per rule and caveat text, naming the units it was given for), a fault's
or warn's labels and masked samples (``M<n>``), the rules a run skipped (``K1@rule_run``) and
the basis of a rule's parameters (``B1@finding_detail``). Hosts read the whole result and cited
the nearest fact for these (the real-host baseline of PLAN-CITE-EVAL), so a sentence stating one
now has a fact that states it, and a sentence citing another fact for it fails.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities._common import caveat_facts, finding_labels, skipped_fact  # noqa: E402
from camber.capabilities._sanitize import unmark  # noqa: E402
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

ID = re.compile(r"[A-Z]\d+@[a-z_]+-[0-9a-f]{6}")


@pytest.fixture(scope="module")
def facts(tmp_path_factory):
    return facttable.collect(str(tmp_path_factory.mktemp("notes")))


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("notes-judge")))


def _of(facts, cap, kind):
    return [f for f in facts[cap] if f.get("kind") == kind]


def _kept(judge, draft, *cited, session=()):
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": list(cited) + list(session),
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return bool(r["grounded"]), r


def test_rule_run_issues_its_caveats_and_labels(facts):
    caveats = _of(facts, "rule_run", "caveat")
    labels = _of(facts, "rule_run", "finding_labels")
    assert caveats and labels
    for f in caveats + labels:
        assert ID.fullmatch(f["id"]), f["id"]
        assert f["rule"] and f["finding_id" if "finding_id" in f else "finding_ids"]
    part = re.compile(r" caveat(?: \(given for \d+ of its \d+ results\))?: ")
    assert all(f["id"].startswith("V") and part.search(f["text"]) for f in caveats)
    assert all(f["id"].startswith("M") for f in labels)
    # labels only for faults and warnings, each value quoted
    findings = {f["finding_id"]: f for f in _of(facts, "rule_run", "finding")}
    assert all(findings[f["finding_id"]]["value"] in ("fault", "warn") for f in labels)
    assert any('fan_gate "fan status"' in f["text"] for f in labels)
    assert any("samples masked: n_masked_fan_off" in f["text"] for f in labels)


def test_finding_detail_issues_caveats_labels_and_its_parameters_basis(facts):
    kinds = {f["kind"] for f in facts["finding_detail"]}
    assert {"caveat", "finding_labels", "parameter_basis"} <= kinds
    basis = _of(facts, "finding_detail", "parameter_basis")
    assert all(f["id"].startswith("B1@") and "CAMBER judgment" in f["text"] for f in basis)


def test_mv_summary_issues_each_caveat_once_with_its_meters(facts):
    caveats = _of(facts, "mv_summary", "caveat")
    texts = [f["text"] for f in caveats]
    assert len(texts) == len(set(texts))
    method = [f for f in caveats if "no mv.method declared" in f["text"]]
    assert method and len(method[0]["equips"]) >= 2
    assert all(f'"{m}"' in method[0]["text"] for m in method[0]["equips"])


def test_caveats_of_one_rule_and_text_are_one_fact():
    rows = [
        {"rule": "leaking_valve", "equip": e, "finding_id": f"leaking_valve@{e}", "caveats": [c]}
        for e, c in (
            ("AHU-1", "fan heat can read as a leak"),
            ("AHU-2", "fan heat can read as a leak"),
        )
    ]
    rows.append({"rule": "leaking_valve", "equip": "AHU-3", "finding_id": "x", "caveats": []})
    (fact,) = caveat_facts("rule_run", rows, "site-a")
    assert fact["id"] == "V1@rule_run" and fact["equips"] == ["AHU-1", "AHU-2"]
    assert unmark(fact["text"]).startswith('"AHU-1", "AHU-2" leaking_valve caveat: fan heat')
    assert fact["value"] == "caveat"  # no figure a sentence could match


def test_labels_are_strings_and_masked_counts_only():
    row = {
        "rule": "free_cooling_missed",
        "equip": "AHU-1",
        "finding_id": "free_cooling_missed@AHU-1",
        "metrics": {
            "missed_cause": "economizer_not_commanded",
            "n_masked_fan_off": 824,
            "missed_pct": 46.09,
            "_missing_optional": "x",
        },
    }
    fact = finding_labels("M1@rule_run", row, "site-a")
    fact["text"] = unmark(fact["text"])
    assert 'missed_cause "economizer_not_commanded"' in fact["text"]
    assert "n_masked_fan_off 824" in fact["text"]
    assert "46.09" not in fact["text"] and "_missing_optional" not in fact["text"]
    assert finding_labels("M1@rule_run", {**row, "metrics": {"n": 3}}, "site-a") is None


def test_the_skipped_rules_are_named():
    skips = [
        {"rule": "airflow_tracking", "equip": "", "missing": ["airflow", "airflow_sp"]},
        {"rule": "condenser_bypass_leak", "equip": "CH-1", "missing": ["cw_flow"]},
        {"rule": "condenser_bypass_leak", "equip": "CH-2", "missing": ["cw_flow"]},
    ]
    fact = skipped_fact("K1@rule_run", skips, "site-a")
    fact["text"] = unmark(fact["text"])
    assert fact["rules"] == ["airflow_tracking", "condenser_bypass_leak"]
    assert "airflow_tracking (missing airflow, airflow_sp)" in fact["text"]
    assert 'condenser_bypass_leak on "CH-1", "CH-2" (missing cw_flow)' in fact["text"]
    assert skipped_fact("K1@rule_run", [], "site-a") is None


def test_a_caveat_is_checked_against_the_caveat_fact(judge, facts):
    caveat = next(f for f in _of(facts, "rule_run", "caveat") if "dehumidification" in f["text"])
    finding = next(
        f for f in _of(facts, "rule_run", "finding") if f["finding_id"] == caveat["finding_ids"][0]
    )
    unit, rule = caveat["equip"], caveat["rule"]
    said = f"The {rule} caveat on {unit} says dehumidification with reheat could not be ruled out"
    assert _kept(judge, f"{said} [{caveat['id']}].", caveat)[0]
    # citing the finding for its caveat fails: no cited fact is a caveat
    assert not _kept(judge, f"{said} [{finding['id']}].", finding)[0]
    # a caveat that hedges supports no finding stated without the hedge
    assert not _kept(judge, f"{unit} runs dehumidification with reheat [{caveat['id']}].", caveat)[
        0
    ]


def test_an_mv_note_is_supported_by_its_caveat(judge, facts):
    method = next(f for f in _of(facts, "mv_summary", "caveat") if "mv.method" in f["text"])
    draft = f"No M&V method was declared, so the default forecast method was used [{method['id']}]."
    assert _kept(judge, draft, method)[0]
