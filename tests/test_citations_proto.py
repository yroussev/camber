"""The prototype of camber.agent.verify still behind a switch (PLAN-CITE-EVAL section 5):
CAMBER_CITE_A (default-deny on parse coverage), judged with the switch off and on. B (every
region judged) is the default since 0112-cite10 (tests/test_citations_regions.py) and C (one
clause per unit, formerly ``CAMBER_CITE_C=split``) since round 11: its cases are below, judged
by the default checker. The measurements are tools/cite_eval/run.py (--flags A).
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

FACTS = facttable.facts_index(facttable.load_or_collect(run.FACTS))


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-proto")))


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ("A", "C"):
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


# (draft, cites, kept with the switch off, switch, kept with it on)
CASES = [
    # A: a word the checker does not read and no cited fact uses fails closed
    (f"AHU-1 has 100.0% coverage {_c(COV)}.", COV, True, "A", True),
    (f"AHU-1 has a pristine 100.0% coverage {_c(COV)}.", COV, True, "A", False),
]


@pytest.mark.parametrize("draft,cites,off,flag,on", CASES)
def test_switch(judge, monkeypatch, draft, cites, off, flag, on):
    assert _kept(judge, draft, cites) is off
    monkeypatch.setenv(f"CAMBER_CITE_{flag}", "1")
    assert _kept(judge, draft, cites) is on


def test_c_switch_is_gone(judge, monkeypatch):
    """C is the default: the old switch changes nothing, whatever its value."""
    draft = f"AHU-1: simultaneous_heat_cool fault; AHU-2: simultaneous_heat_cool ok {_c(RR)}."
    for v in ("", "0", "1", "split"):
        monkeypatch.setenv("CAMBER_CITE_C", v)
        assert _kept(judge, draft, RR) is False  # the swapped status list fails
        ok = f"AHU-2 fails simultaneous_heat_cool and AHU-1 passes simultaneous_heat_cool {_c(RR)}."
        assert _kept(judge, ok, RR) is True


def test_c_says_why(judge):
    r = judge(
        {
            "key": "k",
            "draft": f"AHU-1 or AHU-2 maybe fails simultaneous_heat_cool {_c(RR)}.",
            "facts": [FACTS[c] for c in RR],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert not r["grounded"]
    assert "give each unit its own sentence" in r["sentences"][0][2]


# C: sets, "respectively", "unlike" and "X, not Y" are written as one clause per unit and each
# (unit, claim) pair is read against that unit's own facts
OA = ["F4@sensor_trust-c81f17", "F3@sensor_trust-c81f17"]  # AHU-2 0.73, AHU-3 0.72 (suspect)
REF = ["R1@sensor_trust-c81f17", "R2@sensor_trust-c81f17"]  # AHU-1 disagrees, AHU-2 agrees
SPLIT = [
    # (draft, cites, kept)
    (f"AHU-2's oa_damper and AHU-3's oa_damper are both suspect {_c(OA)}.", OA, True),
    (f"AHU-2 and AHU-3 have oa_damper trusts of 0.73 and 0.72, respectively {_c(OA)}.", OA, True),
    (f"AHU-2 and AHU-3 have oa_damper trusts of 0.72 and 0.73, respectively {_c(OA)}.", OA, False),
    (f"Unlike AHU-2, AHU-1's supply_air_temp disagrees with its reference {_c(REF)}.", REF, True),
    (f"Unlike AHU-1, AHU-2's supply_air_temp disagrees with its reference {_c(REF)}.", REF, False),
    (f"AHU-1's supply_air_temp, not AHU-2's, disagrees with its reference {_c(REF)}.", REF, True),
    (f"AHU-2's supply_air_temp, not AHU-1's, disagrees with its reference {_c(REF)}.", REF, False),
    (f"AHU-1 fails simultaneous_heat_cool, not AHU-2 {_c(RR)}.", RR, False),
]


@pytest.mark.parametrize("draft,cites,on", SPLIT)
def test_c_split(judge, draft, cites, on):
    assert _kept(judge, draft, cites) is on
