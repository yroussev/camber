"""Round 15 of the citation checker (PLAN-CITE-EVAL, measured regime).

1. Fact texts state what hosts quoted from the result's JSON with no fact stating it: a
   finding's thresholds and limits (``thresholds: warn threshold 40% (warn_pct_threshold)``), a
   dataset entry's "good for" list (``F3@dataset_entry``) and how a trust view reads the points
   (``report view: operating-state-aware, ...``).
2. A figure compared with a bound in one sentence must stand in that relation to it; a position
   in a band (estimate ± uncertainty) must agree with the band; a sign said in a word must be
   the figure's; a reading said over or under its reference is its high or low.
3. What CAMBER itself does, said of a view, a rule, the tool or the config, and where a figure
   came from, must be in a cited fact's words; so must a time zone.
4. Where a set of findings is ("both faults are on AHU-1"), a threshold said with another
   threshold's words, and a part's denial said of all of them ("none of the savings is
   verified" against "5 saving(s) ... not a verified saving").
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities._common import _metric_thresholds, finding_fact  # noqa: E402
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite15-"))


def _ok(judge, draft, *facts) -> bool:
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
    return bool(r["grounded"])


# --------------------------------------------------------------------------- 1. fact texts


def test_finding_fact_states_its_thresholds():
    metrics = {
        "warn_pct_threshold": 40.0,
        "warn_gap_floor_f": 1.0,
        "high_limit_f": 65,
        "n_above_limit": 12,  # a count against a limit: not a threshold
        "pct_over_threshold": 3.0,  # a share: not a threshold
        "below_floor_pct": 2.0,
        "low_limit_cooling_hours": 30.0,  # hours of something: not a threshold
        "fault_pct": 20.0,  # a level: the fault threshold
        "thresholds_provisional": True,
    }
    assert _metric_thresholds(metrics) == (
        "thresholds: fault threshold 20% (fault_pct), warn gap floor 1 °F (warn_gap_floor_f), "
        "warn threshold 40% (warn_pct_threshold), high limit 65 °F (high_limit_f)"
    )
    assert _metric_thresholds({"n_valid": 884, "pct_below_target": 54.0}) == ""
    many = {f"warn_{i}_pct": float(i) for i in range(8)}
    assert _metric_thresholds(many).endswith("and 2 more")


ROW = {
    "equip": "AHU-1",
    "rule": "supply_air_reset_compliance",
    "severity": "warn",
    "finding_id": "supply_air_reset_compliance@AHU-1",
    "summary": "AHU-1: SAT below its target 54% of hours (mean gap 3.1°F) -- reheat/energy "
    "opportunity",
    "metrics": {"pct_below_target": 54.0, "warn_pct_threshold": 40.0, "warn_gap_floor_f": 1.0},
}
RESET = {**finding_fact("F2@rule_run-aaaaaa", ROW), "facility_id": "site-a"}


def test_finding_fact_carries_the_thresholds():
    assert RESET["text"].endswith(
        "; thresholds: warn gap floor 1 °F (warn_gap_floor_f), warn threshold 40% "
        "(warn_pct_threshold)"
    )


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("That is the rule's warn threshold", True),
        ("The rule's warn threshold is 40%", True),
        ("The warn gap floor is 1 °F", True),
        ("AHU-1 runs below its target 54% of hours, above the rule's 40% warn threshold", True),
        ("AHU-1 runs below its target 54% of hours, below the rule's 40% warn threshold", False),
        ("The rule's warn threshold is 50%", False),
        ("The rule's fault threshold is 40%", False),
        ("The rule's warn threshold is 1 °F", False),
        ("The rule's warn gap floor is 40%", False),
        ("AHU-1's supply air ran below target 40% of hours", False),
    ],
)
def test_thresholds_are_read_with_their_words(judge, draft, ok):
    assert _ok(judge, f"{draft} [F2@rule_run-aaaaaa].", RESET) == ok


def test_dataset_entry_states_what_it_is_good_for():
    from camber.capabilities import CallContext, default_registry

    ctx = CallContext(store=tempfile.mkdtemp(prefix="cite15-"), allowed_facilities=frozenset())
    facts = default_registry().call("dataset_entry", {"dataset_id": "at-30bldg-sensors"}, ctx).facts
    uses = [f for f in facts if f["kind"] == "dataset_uses"]
    assert len(uses) == 1
    assert uses[0]["text"].startswith("at-30bldg-sensors is good for: sensor health on a real ")


# --------------------------------------------------------------------------- 2. relations

MV1 = "F2@mv_summary-34b587"  # Meter-1: avoided energy -40 (-0.0%) ± 125
MV2 = "F3@mv_summary-34b587"  # Meter-2: avoided energy 10,399 (10.0%) ± 161
BASE1 = "F4@mv_summary-34b587"  # Meter-1 baseline: CV(RMSE) 0.4%, the 30% tier
TRUST = "F1@sensor_trust-c81f17"  # 23 trusted, 4 suspect; the trust floor 0.5
VALVE = "F2@sensor_trust-c81f17"  # AHU-2 heat_valve: trust 0.70
REF = "R1@sensor_trust-c81f17"  # AHU-1 supply_air_temp reads high (bias +3.0 °F)
FAULT1, FAULT2 = "F2@rule_run-54307c", "F3@rule_run-54307c"


@pytest.fixture(scope="module")
def table():
    import json

    path = os.path.join(ROOT, "tests", "citations_corpus", "generated_facts.json")
    with open(path, encoding="utf-8") as fh:
        return {f["id"]: f for cap in json.load(fh).values() for f in cap}


def _cites(judge, table, draft, *ids) -> bool:
    return _ok(judge, draft + " " + "".join(f"[{i}]" for i in ids) + ".", *(table[i] for i in ids))


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("Meter-1's CV(RMSE) of 0.4% is below the 30% tier", True),
        ("Meter-1's CV(RMSE) of 0.4% is above the 30% tier", False),
        ("Meter-1's CV(RMSE) of 0.4% exceeds the 30% tier", False),
        ("Meter-1's CV(RMSE) is 0.4%, well under the 30% tier", True),
        ("Meter-1's CV(RMSE) is 0.4%, well over the 30% tier", False),
        ("Meter-1's 0.4% CV(RMSE) is less than the 30% tier", True),
        ("Meter-1's 0.4% CV(RMSE) is greater than the 30% tier", False),
        ("Meter-1's CV(RMSE) of 0.4% is not above the 30% tier", True),
        ("Meter-1's CV(RMSE) of 0.4% does not exceed the 30% tier", True),
        ("Meter-1's CV(RMSE) of 0.4% is not below the 30% tier", False),
        ("Meter-1's CV(RMSE) 0.4% > 30%", False),
        ("Meter-1's CV(RMSE) 0.4% < 30%", True),
    ],
)
def test_a_figure_compared_with_a_bound(judge, table, draft, ok):
    assert _cites(judge, table, draft, BASE1) == ok


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The 0.70 trust of AHU-2's heat_valve is above the 0.5 floor", True),
        ("The 0.70 trust of AHU-2's heat_valve is below the 0.5 floor", False),
        ("Its trust of 0.70, under the 0.5 floor, is suspect", False),
        ("The 0.70 trust is not below the 0.5 floor", True),
    ],
)
def test_a_trust_compared_with_the_floor(judge, table, draft, ok):
    assert _cites(judge, table, draft, VALVE, TRUST) == ok


def test_a_count_or_a_share_is_not_compared_with_a_measure(judge, table):
    # "66% of run hours were below 40%": the hours are below; "50 hours above 65 °F": a condition
    assert _cites(judge, table, "AHU-2's CHW valve was open 85% of occupied hours", FAULT2)
    assert not _cites(
        judge,
        table,
        "AHU-2's CHW valve was open 85% of occupied hours, less than the HHW valve's 27%",
        FAULT2,
    )


@pytest.mark.parametrize(
    "draft,ident,ok",
    [
        ("Meter-1's avoided energy of -40 is inside its ± 125 band", MV1, True),
        ("Meter-1's avoided energy of -40 is outside its ± 125 band", MV1, False),
        ("Meter-1's avoided energy lies inside its ± 125 band", MV1, True),
        ("That is outside the stated band", MV1, False),
        ("That is within the stated band", MV1, True),
        ("Meter-2's saving of 10,399 is well outside its ± 161 band", MV2, True),
        ("Meter-2's saving of 10,399 is within its ± 161 band", MV2, False),
        ("Meter-2's saving is inside its uncertainty band", MV2, False),
        ("Meter-1's uncertainty is larger than its avoided energy", MV1, True),
        ("Meter-1's avoided energy of -40 is larger than its ± 125 uncertainty", MV1, False),
        ("Meter-2 avoided 10.0%, within a margin of ± 161", MV2, False),
        ("Meter-1's result is within its uncertainty band", BASE1, False),  # no band given
    ],
)
def test_a_position_in_a_band(judge, table, draft, ident, ok):
    assert _cites(judge, table, draft, ident) == ok


@pytest.mark.parametrize(
    "draft,ident,ok",
    [
        ("Meter-2's saving is positive", MV2, True),
        ("Meter-2's saving is negative", MV2, False),
        ("Meter-1's avoided energy is negative", MV1, True),
        ("Meter-1's avoided energy is positive", MV1, False),
        ("AHU-1 has a positive bias", REF, True),
        ("AHU-1 has a negative bias", REF, False),
        ("A positive bias means the point reads high", REF, True),
        ("AHU-1's supply air reads under its reference by 3.0 °F", REF, False),
        ("AHU-1's supply air reads below its reference", REF, False),
        ("AHU-1's supply air reads 3.0 °F over its reference", REF, True),
    ],
)
def test_a_sign_or_a_side_said_in_a_word(judge, table, draft, ident, ok):
    assert _cites(judge, table, draft, ident) == ok


# --------------------------------------------------------------------------- 3. method


REPORT = {
    "id": "F1@sensor_trust-0a0a0a",
    "kind": "trust_summary",
    "facility_id": "site-a",
    "value": 0,
    "text": "site-a: 27 points judged (report view: operating-state-aware, duct points judged "
    "with the fan on and plant points while their equipment runs): 23 trusted, 4 suspect, 0 "
    "untrusted; 0 below the trust floor 0.5",
}
OLD = {**REPORT, "text": REPORT["text"].split(" (report", 1)[0] + " (report view): 23 trusted"}


@pytest.mark.parametrize(
    "draft,fact,ok",
    [
        (
            "The report view, which judges points in their operating state, gave 23 trusted",
            REPORT,
            True,
        ),
        (
            "The report view, which judges points in their operating state, gave 23 trusted",
            OLD,
            False,
        ),
        ("The report view judges duct points with the fan on", REPORT, True),
        ("The report view judges each point against its own history", REPORT, False),
        ("CAMBER scores each point against its manufacturer's tolerance", REPORT, False),
        (
            "The report view, which judges each point only while its fan runs, gave 23 trusted",
            OLD,
            False,
        ),
    ],
)
def test_what_camber_does_needs_a_fact_that_says_so(judge, draft, fact, ok):
    assert _ok(judge, f"{draft} [{fact['id']}].", fact) == ok


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The comparison uses 504 paired samples", True),
        ("The tool uses the median of the point minus the reference as the bias", True),
        ("The tool uses the mean of the point minus the reference as the bias", False),
        ("The bias came from the median of the point minus the reference", True),
        ("The bias came from a two-week spot check", False),
        ("The check compares AHU-1's supply air temperature with a duct logger", False),
    ],
)
def test_a_method_or_a_source_in_the_facts_words(judge, table, draft, ok):
    assert _cites(judge, table, draft, REF) == ok


def test_a_time_zone_needs_a_fact_that_gives_one(judge, table):
    fac = "F3@facilities-027f1a"
    assert not _cites(judge, table, "site-a's timestamps are on the site wall clock", fac)
    assert not _cites(judge, table, "site-a's data is in UTC", fac)
    assert _cites(judge, table, "site-a has data from 2026-03-02 to 2026-03-22", fac)


# --------------------------------------------------------------------------- 4. scope

NOTICE = {
    "id": "N1@mv_summary-44b2bd",
    "kind": "notice",
    "facility_id": "mv-site",
    "text": "5 saving(s) rest on a baseline that does not meet G14 acceptance: for information "
    "only, not a verified saving.",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The M&V results give no verified saving for the site", False),
        ("None of the savings is verified", False),
        ("These 5 savings are not verified", True),
    ],
)
def test_a_part_s_denial_is_not_said_of_all(judge, draft, ok):
    assert _ok(judge, f"{draft} [N1@mv_summary-44b2bd].", NOTICE) == ok


def test_where_a_set_of_findings_is(judge, table):
    assert not _cites(judge, table, "Both faults are on AHU-1", FAULT1, FAULT2)
    assert not _cites(judge, table, "The only fault is on AHU-2", FAULT1)
    assert _cites(judge, table, "The only fault is on AHU-1", FAULT1)
