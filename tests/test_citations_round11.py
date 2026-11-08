"""Round 11 of the citation checker (PLAN-CITE-EVAL section 6).

1. The two round-10 host escapes closed in the facts: a diagnostic evidence chart's fact names
   its axes, and the sensor_trust summary names the role or equipment filter of the call.
"""

from __future__ import annotations

from types import SimpleNamespace

from camber.capabilities._cap_rule_run import _chart
from camber.capabilities._cap_sensor_trust import _selection_words


def test_diagnostic_chart_fact_names_its_axes():
    ev = SimpleNamespace(
        roles=[],
        template=SimpleNamespace(name="economizer lockout", x="oat", y="oa_fraction_pct"),
        renderer="diagnostic",
        title="",
        label="",
    )
    out = _chart(ev)
    assert out["roles"] == ["oat", "oa_fraction_pct"]
    assert out["description"] == (
        "A diagnostic scatter (economizer lockout) of oa_fraction_pct (outdoor-air fraction, y) "
        "against oat (outdoor-air temperature, x), with the violating samples marked"
    )


def test_diagnostic_chart_axis_without_words_is_its_role():
    ev = SimpleNamespace(
        roles=["heat_valve", "cool_valve"],
        template=SimpleNamespace(name="no_simultaneous_hc", x="heat_valve", y="cool_valve"),
        renderer="diagnostic",
        title="",
        label="",
    )
    assert (
        "of cool_valve (cool valve, y) against heat_valve (heat valve, x),"
        in (_chart(ev)["description"])
    )


def test_trust_summary_says_the_selection():
    assert _selection_words(None, None) == "points"
    assert _selection_words(None, ["supply_air_temp"]) == "supply_air_temp points"
    assert _selection_words(["AHU-1"], None) == "points on AHU-1"
    assert (
        _selection_words(["AHU-1", "AHU-2"], ["supply_air_temp", "mixed_air_temp"])
        == "supply_air_temp or mixed_air_temp points on AHU-1, AHU-2"
    )


# --------------------------------------------------------------------------- 2. C is the default

import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


def _corpus() -> dict:
    out = {}
    for path in glob.glob(os.path.join(ROOT, "tests", "citations_corpus", "*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    out[e["id"]] = e
    return out


CORPUS = _corpus()
GEN = facttable.facts_index(facttable.load_or_collect(run.FACTS))


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-r11")))


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ("A", "C"):
        monkeypatch.delenv(f"CAMBER_CITE_{f}", raising=False)


def _grounded(judge, entry_id: str, draft: str) -> bool:
    e = CORPUS[entry_id]
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": e["facts"],
            "allowed": e.get("allowed", []),
            "names": e.get("names", {}),
        }
    )
    assert "error" not in r, r
    return bool(r["grounded"])


ADR, AUD = '"Fox_assembly_Adrianne__chilledwater"', '"Fox_assembly_Audrey__chilledwater"'
MV = "[F3@mv_summary-da68ea][F6@mv_summary-da68ea]"
VAV = "[F5@rule_run-699500] [F7@rule_run-699500] [F9@rule_run-699500]"
DS = "[F3@rule_run-cb5120] [F5@rule_run-cb5120] [F8@rule_run-cb5120]"
COV = "[P11@coverage-3fe28b] [P1@coverage-3fe28b]"
LEAK = "[F2@rule_run-6c7c46] [F1@finding_detail-8ed48b]"
FLAT = (
    "[L1@sensor_trust-87fb75][L3@sensor_trust-87fb75][R1@sensor_trust-87fb75]"
    "[R2@sensor_trust-87fb75]"
)
REF = "[R1@sensor_trust-87fb75][R2@sensor_trust-87fb75]"
OA = "[F2@sensor_trust-87fb75][F3@sensor_trust-87fb75]"

#: the nine true corpus entries C=split stripped in round 10, each with its pair swapped or made
#: false: (corpus id, the true draft, the false one)
NINE = [
    (
        "85cc606796d0",
        f"{ADR}, not {AUD}, used more energy than its baseline {MV}.",
        f"{AUD}, not {ADR}, used more energy than its baseline {MV}.",
    ),
    (
        "ebd09423e21f",
        f"Most of VAV1__not_responding, VAV1__rogue_sat and VAV1__starved fault on "
        f"unmet_setpoint_hours {VAV}.",
        f"Most of VAV1__not_responding, VAV1__rogue_sat and VAV1__starved pass "
        f"unmet_setpoint_hours {VAV}.",
    ),
    (
        "4cf887042f69",
        "AHU__damper_stuck_010 and AHU__damper_stuck_025 ran mechanical cooling 100% of the "
        f"free-cooling hours, AHU__damper_stuck_075 42% {DS}.",
        "AHU__damper_stuck_010 and AHU__damper_stuck_075 ran mechanical cooling 100% of the "
        f"free-cooling hours, AHU__damper_stuck_025 42% {DS}.",
    ),
    (
        "bad3861255a9",
        f"AHU__healthy supply_air_temp has full coverage, unlike LOGGER__healthy's {COV}.",
        f"LOGGER__healthy supply_air_temp has full coverage, unlike AHU__healthy's {COV}.",
    ),
    (
        "6354575e50cb",
        f"AHU-2 has no valve leak, unlike AHU-1 {LEAK}.",
        f"AHU-1 has no valve leak, unlike AHU-2 {LEAK}.",
    ),
    (
        "6e2c2b7d5c2b",
        f"AHU-1's coil leaks, not AHU-2's {LEAK}.",
        f"AHU-2's coil leaks, not AHU-1's {LEAK}.",
    ),
    (
        "0cbf4e79455a",
        'AHU-1 and AHU-2 have flat heat valves, and their "supply_air_temp" biases are +3.0 and '
        f"+0.0 respectively {FLAT}.",
        'AHU-1 and AHU-2 have flat heat valves, and their "supply_air_temp" biases are +0.0 and '
        f"+3.0 respectively {FLAT}.",
    ),
    (
        "b1744f8a917d",
        "AHU-1's supply_air_temp disagrees with its reference rather than agreeing, as AHU-2's "
        f"does {REF}.",
        "AHU-2's supply_air_temp disagrees with its reference rather than agreeing, as AHU-1's "
        f"does {REF}.",
    ),
    (
        "bb2baf20db1f",
        f"AHU-1's oa_damper (0.66) is more suspect than AHU-2's (0.68) {OA}.",
        f"AHU-1's oa_damper (0.68) is more suspect than AHU-2's (0.66) {OA}.",
    ),
    (
        "bb2baf20db1f",
        f"AHU-1's oa_damper (0.66) is more suspect than AHU-2's (0.68) {OA}.",
        f"AHU-1's oa_damper (0.66) is less suspect than AHU-2's (0.68) {OA}.",
    ),
]


@pytest.mark.parametrize("entry_id,true,false", NINE)
def test_the_nine_split_strips_pass_true_and_fail_swapped(judge, entry_id, true, false):
    assert _grounded(judge, entry_id, true)
    assert not _grounded(judge, entry_id, false)


def _gen(judge, draft: str) -> bool:
    import re

    ids = [x.strip() for g in re.findall(r"\[([^\]]+)\]", draft) for x in g.split(",")]
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": [GEN[i] for i in ids],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return bool(r["grounded"])


@pytest.mark.parametrize(
    "draft,ok",
    [
        # a leak needs a cited fact that states one; another rule's fault is none
        ("AHU-1's coil leaks [F1@finding_detail-4ecd59].", False),
        ("AHU-1's coil leaks [C1@rule_run-54307c].", False),  # its leaking_valve is ok
        ("AHU-1's coil does not leak [C1@rule_run-54307c].", True),
        # "most of" a set of named units: more than half of them
        (
            "Most of AHU-1 and AHU-2 have 100.0% coverage [F2@coverage-cd485b] "
            "[F3@coverage-cd485b].",
            True,
        ),
        # past verbs denied: "B did not fail"
        (
            "AHU-2, not AHU-1, failed simultaneous_heat_cool [F3@rule_run-54307c] "
            "[C1@rule_run-54307c].",
            True,
        ),
        (
            "AHU-1, not AHU-2, failed simultaneous_heat_cool [F3@rule_run-54307c] "
            "[C1@rule_run-54307c].",
            False,
        ),
        # an excluded unit is read against its own facts, not another unit's
        (
            "AHU-2 does not pass simultaneous_heat_cool, unlike AHU-1 [C1@rule_run-54307c] "
            "[F1@finding_detail-95ce3b].",
            True,
        ),
        (
            "AHU-1 does not pass simultaneous_heat_cool, unlike AHU-2 [C1@rule_run-54307c] "
            "[F1@finding_detail-95ce3b].",
            False,
        ),
        # items that each carry their own citation are one clause each
        (
            "The suspect ones are the AHU-2 oa_damper [F4@sensor_trust-c81f17] and the AHU-3 "
            "oa_damper [F3@sensor_trust-c81f17].",
            True,
        ),
        (
            "The trusted ones are the AHU-2 oa_damper [F4@sensor_trust-c81f17] and the AHU-3 "
            "oa_damper [F3@sensor_trust-c81f17].",
            False,
        ),
    ],
)
def test_round11_forms(judge, draft, ok):
    assert _gen(judge, draft) is ok


def test_a_unit_set_that_splits_no_way_says_give_each_unit_its_own_sentence(judge):
    from camber.capabilities._cap_check_citations import CAPABILITIES
    from camber.capabilities._cap_prompts import _CITE_RULES

    assert "Give each unit its own sentence" in CAPABILITIES[0].description
    assert "Give each unit its own sentence" in _CITE_RULES


# --------------------------------------------------------------------------- 4. cited strips

MV2 = "[F3@mv_summary-34b587]"  # Meter-2: avoided 10.0%, baseline period 2024-01-01 ...
MV2_FIT = "[F5@mv_summary-34b587]"  # Meter-2: 4P baseline, R2 1.00 ... meets daily acceptance


@pytest.mark.parametrize(
    "draft,ok",
    [
        # a year before a period's noun is a year, wherever it stands
        (f"| Meter | 2024 baseline saving |\n|---|---|\n| Meter-2 | 10% {MV2} |", True),
        (f"| Meter | 2023 baseline saving |\n|---|---|\n| Meter-2 | 10% {MV2} |", False),
        (f"Meter-2 saved 10% against its 2024 baseline {MV2}.", True),
        (f"Meter-2 saved 10% against its 2023 baseline {MV2}.", False),
        # a header's year is the header row's claim; a row reads its column without it
        (
            f"| Meter | Fit and 2024 savings |\n|---|---|\n"
            f"| Meter-2 | meets daily acceptance {MV2_FIT}; avoided 10% {MV2} |",
            True,
        ),
        # "Yes: ..." / "No: ..." under a column that asks: the answer, then its details
        (
            f"| Meter | Meets daily acceptance? |\n|---|---|\n| Meter-2 | Yes: R2 1.00 {MV2_FIT} |",
            True,
        ),
        (
            f"| Meter | Meets daily acceptance? |\n|---|---|\n| Meter-2 | No: R2 1.00 {MV2_FIT} |",
            False,
        ),
        (
            "| Unit | Passes economizer_high_limit? |\n|---|---|\n"
            "| AHU-1 | No: 43% of those samples [F1@finding_detail-4ecd59] |",
            True,
        ),
        (
            "| Unit | Passes economizer_high_limit? |\n|---|---|\n"
            "| AHU-1 | Yes: 43% of those samples [F1@finding_detail-4ecd59] |",
            False,
        ),
        # a column that asks keeps its year: it is judged in its rows only
        (
            "| Unit | Fails economizer_high_limit throughout 2026? |\n|---|---|\n"
            "| AHU-1 | Yes: 43% of those samples [F1@finding_detail-4ecd59] |",
            False,
        ),
        # a cell of two parts, each citing its own fact
        (
            "| Unit | Trust and coverage |\n|---|---|\n| AHU-2's oa_damper | has a trust of 0.73 "
            "[F4@sensor_trust-c81f17]; has 100.0% coverage [P14@coverage-cd485b] |",
            True,
        ),
        (
            "| Unit | Trust and coverage |\n|---|---|\n| AHU-2's oa_damper | has a trust of 0.72 "
            "[F4@sensor_trust-c81f17]; has 100.0% coverage [P14@coverage-cd485b] |",
            False,
        ),
        # "tracks its reference" is "agrees with" it
        ("AHU-2's supply_air_temp tracks its reference [R2@sensor_trust-c81f17].", True),
        ("AHU-1's supply_air_temp tracks its reference [R1@sensor_trust-c81f17].", False),
    ],
)
def test_round11_cited_strip_forms(judge, draft, ok):
    assert _gen(judge, draft) is ok
