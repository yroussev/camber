"""The 0112-cite-proto prototypes of camber.agent.verify (PLAN-CITE-EVAL section 5), each behind
its switch: CAMBER_CITE_B (every region judged), CAMBER_CITE_C (one clause per unit) and
CAMBER_CITE_A (default-deny on parse coverage). With no switch set the checker is the one of
c024906: each case below is judged both ways, so the switches are seen to do what they say and
to change nothing when off. The measurements are tools/cite_eval/run.py --flags.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

FACTS = facttable.facts_index(facttable.load_or_collect(run.FACTS))
NBH = "‑"


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-proto")))


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ("A", "B", "C"):
        monkeypatch.delenv(f"CAMBER_CITE_{f}", raising=False)


def _kept(judge, draft: str, cites, core: str = "") -> bool:
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": [FACTS[c] for c in cites],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    vt = " ".join(r["verified_text"].split())
    return bool(r["grounded"]) and " ".join((core or draft).split()) in vt


RR = ["F3@rule_run-54307c", "C1@rule_run-54307c"]  # AHU-2 fails, AHU-1 passes (shc)
COV = ["F2@coverage-cd485b"]  # AHU-1: 100.0% coverage


def _c(ids) -> str:
    return " ".join(f"[{i}]" for i in ids)


# (draft, cites, kept with every switch off, switch, kept with it on)
CASES = [
    # B: a forged comment is read like prose; a true one stays
    (f"<!-- AHU-1 fails simultaneous_heat_cool {_c(RR[1:])}. -->", RR[1:], True, "B", False),
    (f"<!-- AHU-2 fails simultaneous_heat_cool {_c(RR[:1])}. -->", RR[:1], True, "B", True),
    # B: a non-breaking hyphen in the id is the id
    (f"AHU{NBH}1 has 100.0% coverage {_c(COV)}.", COV, False, "B", True),
    # B: a header column's claim is read with the row's "yes"
    (
        f"| Unit | Fails simultaneous_heat_cool |\n|---|---|\n| AHU-1 | yes {_c(RR[1:])} |",
        RR[1:],
        True,
        "B",
        False,
    ),
    # C: a swapped status list fails; a true two-clause sentence stays
    (
        f"AHU-1: simultaneous_heat_cool fault; AHU-2: simultaneous_heat_cool ok {_c(RR)}.",
        RR,
        True,
        "C",
        False,
    ),
    (
        f"AHU-2 fails simultaneous_heat_cool and AHU-1 passes simultaneous_heat_cool {_c(RR)}.",
        RR,
        True,
        "C",
        True,
    ),
    # A: a word the checker does not read and no cited fact uses fails closed
    (f"AHU-1 has 100.0% coverage {_c(COV)}.", COV, True, "A", True),
    (f"AHU-1 has a pristine 100.0% coverage {_c(COV)}.", COV, True, "A", False),
]


@pytest.mark.parametrize("draft,cites,off,flag,on", CASES)
def test_switch(judge, monkeypatch, draft, cites, off, flag, on):
    assert _kept(judge, draft, cites) is off
    monkeypatch.setenv(f"CAMBER_CITE_{flag}", "1")
    assert _kept(judge, draft, cites) is on


def test_c_says_why(judge, monkeypatch):
    monkeypatch.setenv("CAMBER_CITE_C", "1")
    r = judge(
        {
            "key": "k",
            "draft": f"AHU-1 and AHU-2 both fail simultaneous_heat_cool {_c(RR)}.",
            "facts": [FACTS[c] for c in RR],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert not r["grounded"]
    assert "give each unit its own sentence" in r["sentences"][0][2]
