"""Round 16 of the citation checker (PLAN-CITE-EVAL, measured regime).

1. Fact texts state what hosts quoted from the result's JSON or inferred with no fact stating
   it: why a sensor reference measured no drift rate (``drift not evaluated because the overlap
   spans 4.0 days, under the 28 days a drift rate needs``), a finding's gates (``gates:
   occupancy_gate "trended occupancy"``), which parameters set a rule's warn and fault levels
   (``verdict thresholds: warn_pct (warn); no fault threshold among its parameters``), and that a
   disagreeing reference makes a point suspect whatever its score.
2. A reason joined to what it explains ("so", "because", "since", "which is why") needs a cited
   fact that gives it; a check said not to have been judged must be named by a cited fact that
   says so, with the count where it declined only some; a count bound to the list or table next
   to it ("These are the 8 faults") must be its number of items.
3. A comparison is said of its clause's subject, not the figure nearest the comparison word; a
   share's whole is read past the participles before it, and a failing share's why says what it
   is of; a parameter said to set a warn or fault level must be that level's.
4. dataset_entry by a facility id names the catalog id.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.capabilities._cap_sensor_trust import (  # noqa: E402
    _drift_note,
    _drift_why,
    _reference_fact,
)
from camber.capabilities._common import (  # noqa: E402
    _metric_gates,
    finding_fact,
    verdict_levels,
)
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite16-"))


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

_CAVEAT = (
    "drift not evaluated: overlap spans 4.0 days (< 28); a per-month rate from a shorter "
    "window is an extrapolation"
)


def test_drift_note_says_why_no_rate_was_measured():
    assert _drift_note([_CAVEAT], None) == "overlap spans 4.0 days (< 28)"
    assert _drift_note([_CAVEAT], 0.4) is None  # a rate was measured
    assert _drift_note(["bias is the median of ..."], None) is None
    assert _drift_why("overlap spans 4.0 days (< 28)") == (
        "the overlap spans 4.0 days, under the 28 days a drift rate needs"
    )
    assert _drift_why("only 9 daily baselines (< 14)") == (
        "there are only 9 daily baselines, under the 14 a drift rate needs"
    )


_REF = {
    "equip": "AHU-1",
    "role": "supply_air_temp",
    "reference": "handheld reference logger",
    "unit": "°F",
    "verdict": "warn",
    "bias": 3.0,
    "rmse": 3.0,
    "correlation": 1.0,
    "drift_per_month": None,
    "drift_note": "overlap spans 4.0 days (< 28)",
    "n_samples": 384,
    "overlap_start": "2025-05-19 00:00:00",
    "overlap_end": "2025-05-22 23:45:00",
    "summary": "AHU-1 supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over "
    "384 samples / 4 days -- biased +3.0",
}
REF_NEW = _reference_fact("R1@sensor_trust-aaaaaa", _REF, "site-a")
REF_OLD = _reference_fact("R1@sensor_trust-aaaaaa", {**_REF, "drift_note": None}, "site-a")


def test_reference_fact_gives_the_reason():
    assert (
        "; drift not evaluated because the overlap spans 4.0 days, under the 28 days a "
        in (REF_NEW["text"])
    )
    assert "because" not in REF_OLD["text"]


ROW = {
    "equip": "AHU-1",
    "rule": "supply_air_control",
    "severity": "ok",
    "finding_id": "supply_air_control@AHU-1",
    "summary": "AHU-1: SAT off setpoint 0% of occupied running hours",
    "metrics": {"occupancy_gate": "trended occupancy", "off_setpoint_pct": 0.0, "tol_F": 2.0},
}
GATED = {**finding_fact("F5@rule_run-aaaaaa", ROW), "facility_id": "site-a"}
UNGATED = {
    **finding_fact("F5@rule_run-aaaaaa", {**ROW, "metrics": {"off_setpoint_pct": 0.0}}),
    "facility_id": "site-a",
}


def test_finding_fact_names_its_gates():
    shown = GATED["text"].replace("\ue000", "").replace("\ue001", "")  # the name marks
    assert shown.endswith('; gates: occupancy_gate "trended occupancy"')
    gates = _metric_gates({"fan_gate": "fan status", "fan_gated": True, "source": "x"})
    assert gates.replace("\ue000", "").replace("\ue001", "") == 'gates: fan_gate "fan status"'
    assert _metric_gates({"n": 3}) == ""


def test_verdict_levels():
    assert verdict_levels(["tol_F", "warn_pct", "fault_pct"]) == (
        "verdict thresholds: warn_pct (warn), fault_pct (fault)"
    )
    assert verdict_levels(["warn_pct", "warn_gap_f", "fan_gate"]) == (
        "verdict thresholds: warn_pct (warn), warn_gap_f (warn); no fault threshold among its "
        "parameters"
    )
    assert verdict_levels(["bias_warn", "bias_fault"]) == (
        "verdict thresholds: bias_warn (warn), bias_fault (fault)"
    )
    assert verdict_levels(["fan_heat_f", "delta_thr_f"]) == (
        "no warn or fault threshold among its parameters"
    )


def _params(rule: str) -> dict:
    ctx = CallContext(store=tempfile.mkdtemp(prefix="cite16-"), allowed_facilities=frozenset())
    facts = default_registry().call("rule_params", {"rules": [rule]}, ctx).facts
    return next(f for f in facts if f.get("kind") == "rule_params")


def test_rule_params_fact_names_the_verdict_levels():
    assert _params("supply_air_control")["text"].endswith(
        "; verdict thresholds: warn_pct (warn), fault_pct (fault)"
    )
    assert _params("leaking_valve")["text"].endswith(
        "; no warn or fault threshold among its parameters"
    )


def test_dataset_entry_by_facility_id_names_the_catalog_id():
    ctx = CallContext(store=tempfile.mkdtemp(prefix="cite16-"), allowed_facilities=frozenset())
    with pytest.raises(CapabilityError) as e:
        default_registry().call(
            "dataset_entry", {"dataset_id": "ds-synthetic-ahu-sensor-bias"}, ctx
        )
    assert "pass dataset_id 'synthetic-ahu-sensor-bias'" in str(e.value)
    res = default_registry().call("dataset_search", {"query": "ds-synthetic-ahu-sensor-bias"}, ctx)
    assert [r["id"] for r in res.data["datasets"]] == ["synthetic-ahu-sensor-bias"]


# --------------------------------------------------------------------------- 2. reasons


@pytest.mark.parametrize(
    "draft,new,old",
    [
        ("The comparison covers only 4 days, so drift over time was not evaluated", True, False),
        ("Drift is not evaluated for AHU-1, because the overlap is only 4 days", True, False),
        ("Since the overlap is only 4 days, drift was not evaluated", True, False),
        ("The overlap is only 4 days, which is why drift was not evaluated", True, False),
        ("Drift was not evaluated because the bias is +3.0", False, False),
        ("The point reads high, so drift was not evaluated", False, False),
        ("The comparison covers only 4 days, so the point is suspect", False, False),
        ("The bias is +3.0, so the point reads high against its reference", True, True),
        ("Drift was not evaluated", True, True),
    ],
)
def test_a_reason_needs_a_fact_that_gives_it(judge, draft, new, old):
    assert _ok(judge, f"{draft} [R1@sensor_trust-aaaaaa].", REF_NEW) == new
    assert _ok(judge, f"{draft} [R1@sensor_trust-aaaaaa].", REF_OLD) == old


TOWER = {
    "id": "F5@rule_run-bbbbbb",
    "kind": "finding",
    "rule": "cooling_tower_approach",
    "equip": "PLANT-1",
    "text": '"PLANT-1" cooling_tower_approach (info): "PLANT-1": approach not judged -- the '
    "tower fan never reached 90% (peak 88%; at part fan the approach is the controller's "
    "choice, not the tower's limit); declined true",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        (
            "Tower approach was not judged on PLANT-1, because the fan peaked at 88% and never "
            "reached the 90% gate",
            True,
        ),
        (
            "Tower approach was not judged on PLANT-1, because the condenser water was too warm",
            False,
        ),
        ("Tower approach was not judged on PLANT-1, so the fan should be checked", True),
    ],
)
def test_a_reason_read_against_a_dash(judge, draft, ok):
    assert _ok(judge, f"{draft} [F5@rule_run-bbbbbb].", TOWER) == ok


# --------------------------------------------------------------------------- 2. not judged

RUN = {
    "id": "F1@rule_run-bbbbbb",
    "kind": "rule_run",
    "text": "5 rules were run on 5 equipment of site-a and 4 of them returned a result, 20 "
    "rule-equipment result(s) in all: 2 fault, 1 warn, 5 info and 12 ok; 5 rule-equipment "
    "pair(s) (1 rule(s)) skipped for missing inputs: those pairs were not judged",
}
PART = {
    "id": "V2@rule_run-bbbbbb",
    "kind": "caveat",
    "rule": "cooling_tower_approach",
    "of": 5,
    "text": '"PLANT-1", "PLANT-2", "PLANT-3", "PLANT-4" cooling_tower_approach caveat (given for '
    "4 of its 5 results): could not evaluate tower approach: the fan never reached the "
    "high-effort gate",
}
SKIP = {
    "id": "K1@rule_run-bbbbbb",
    "kind": "skipped_rules",
    "text": "1 rule(s) skipped for missing inputs on site-a, so not judged: condenser_bypass_leak "
    'on "PLANT-1", "PLANT-2" (missing cw_bypass_valve)',
}


@pytest.mark.parametrize(
    "draft,facts,ok",
    [
        ("Tower approach and bypass-leak checks weren't judged", [RUN], False),
        ("Tower approach and bypass-leak checks weren't judged", [PART, SKIP], False),
        (
            "Tower approach wasn't judged on 4 of the 5 plants, and the bypass-leak check was "
            "skipped",
            [PART, SKIP],
            True,
        ),
        ("The bypass-leak check was skipped for missing inputs", [SKIP], True),
        ("condenser_bypass_leak was not judged", [SKIP], True),
        ("5 rule-equipment pairs were skipped for missing inputs", [RUN], True),
        ("The run reports no declined result", [RUN], False),
        ("AHU-1's supply_air_temp drift was not evaluated", [REF_NEW], True),
        ("AHU-1's bias was not evaluated", [REF_NEW], False),
        ("The reference comparison was not evaluated", [REF_NEW], False),
    ],
)
def test_a_not_judged_claim_is_named_and_scoped(judge, draft, facts, ok):
    cites = "".join(f"[{f['id']}]" for f in facts)
    assert _ok(judge, f"{draft} {cites}.", *facts) == ok


# --------------------------------------------------------------------------- 2. deictic counts

RUN8 = {
    "id": "F1@rule_run-cccccc",
    "kind": "rule_run",
    "text": "5 rules were run on 56 equipment of site-a and 5 of them returned a result, 145 "
    "rule-equipment result(s) in all: 8 fault, 0 warn, 51 info and 86 ok; 0 rule-equipment "
    "pair(s) (0 rule(s)) skipped for missing inputs",
}
ROWS7 = "| Zone | Rule |\n|---|---|\n" + "".join(f"| VAV{i} | reheat |\n" for i in range(1, 8))
LIST7 = "".join(f"- VAV{i} reheat\n" for i in range(1, 8))


def _verdict(judge, draft, sentence) -> str:
    r = judge({"key": "k", "draft": draft, "facts": [RUN8], "allowed": ["site-a"], "names": {}})
    return next(v for t, v, _w in r["sentences"] if t.startswith(sentence))


@pytest.mark.parametrize(
    "draft,sentence,ok",
    [
        (ROWS7 + "\nThese are the 8 faults in the run [F1@rule_run-cccccc].", "These", False),
        (
            ROWS7 + "| fleet | census |\n\nThese are the 8 faults in the run [F1@rule_run-cccccc].",
            "These",
            True,
        ),
        (ROWS7 + "\nThe run has 8 faults [F1@rule_run-cccccc].", "The run", True),
        ("The following 8 are the faults [F1@rule_run-cccccc]:\n" + LIST7, "The following", False),
        (
            LIST7 + "\nThe 8 faults listed above are in the run [F1@rule_run-cccccc].",
            "The 8",
            False,
        ),
        (
            LIST7 + "\nThe run has 145 results; 8 of those 145 results are faults "
            "[F1@rule_run-cccccc].",
            "The run",
            True,
        ),
    ],
)
def test_a_count_bound_to_the_list_next_to_it(judge, draft, sentence, ok):
    assert (_verdict(judge, draft, sentence) == "supported") == ok


# --------------------------------------------------------------------------- 3. comparisons

LEAK = {
    "id": "F1@finding_detail-dddddd",
    "kind": "finding",
    "rule": "leaking_valve",
    "equip": "AHU-1",
    "text": '"AHU-1" leaking_valve (fault): "AHU-1": with both valves shut, air shifts (median '
    "+3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples",
}
LEAK_PARAMS = {
    "id": "F4@finding_detail-dddddd",
    "kind": "parameters",
    "text": "leaking_valve ran with fan_heat_f = 2.0, delta_thr_f = 3.0, valve_closed_thr = 5.0; "
    "no warn or fault threshold among its parameters",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        (
            "AHU-1's air shifted +3.4F beyond the 2F fan heat, which is above the delta_thr_f of "
            "3.0",
            True,
        ),
        (
            "AHU-1's air shifted +3.4F beyond the 2F fan heat, which is below the delta_thr_f of "
            "3.0",
            False,
        ),  # the nearest figure (2F) is below 3.0: binding to it kept this
        (
            "AHU-1's air shifted +3.4F beyond the 2F fan heat, which is not above the delta_thr_f "
            "of 3.0",
            False,
        ),
        ("The delta_thr_f of 3.0 is above the 2F fan heat", True),
    ],
)
def test_a_comparison_is_said_of_its_subject(judge, draft, ok):
    ids = "[F1@finding_detail-dddddd] [F4@finding_detail-dddddd]"
    assert _ok(judge, f"{draft} {ids}.", LEAK, LEAK_PARAMS) == ok


SHARE = {
    "id": "F2@finding_detail-eeeeee",
    "kind": "evidence_hours",
    "subject": {"facility_id": "site-a", "equip": "AHU-1"},
    "text": "The rule flagged 106 hours (424 of 780 judged occupied fan-on samples, 54.4%) in "
    "16 contiguous span(s), the longest 13 hours",
}


def test_a_share_is_of_the_samples_past_its_participles(judge):
    draft = "AHU-1 was below target on {} of judged occupied fan-on {} [F2@finding_detail-eeeeee]."
    assert _ok(judge, draft.format("54.4%", "samples"), SHARE)
    assert not _ok(judge, draft.format("54.4%", "hours"), SHARE)
    r = judge(
        {
            "key": "k",
            "draft": draft.format("54.4%", "hours"),
            "facts": [SHARE],
            "allowed": ["site-a"],
            "names": {},
        }
    )
    why = r["sentences"][0][2]
    assert "only as 54.4 %" not in why  # (the round-15 why named the figure it failed on)
    assert why == (
        "54.4 % of hours: a cited fact gives this figure as a share of samples, not as a share of "
        "hours"
    )


LEVELS = {
    "id": "F3@rule_params-ffffff",
    "kind": "rule_params",
    "rule": "supply_air_control",
    "text": "supply_air_control (rule, run with rule_run) defaults: tol_F = 2.0 °F, warn_pct = "
    "10.0 % of fan-on running samples, fault_pct = 25.0 % of fan-on running samples; verdict "
    "thresholds: warn_pct (warn), fault_pct (fault)",
}
RESET_LEVELS = {
    "id": "F4@rule_params-ffffff",
    "kind": "rule_params",
    "rule": "supply_air_reset_compliance",
    "text": "supply_air_reset_compliance (rule, run with rule_run) defaults: warn_pct = 40.0 % "
    "of gated samples, warn_gap_f = 2.0 °F; verdict thresholds: warn_pct (warn), warn_gap_f "
    "(warn); no fault threshold among its parameters",
}
NO_LEVELS = {**LEAK_PARAMS, "id": "F2@rule_params-ffffff", "kind": "rule_params"}


@pytest.mark.parametrize(
    "draft,fact,ok",
    [
        ("supply_air_control's warn threshold is warn_pct", LEVELS, True),
        ("supply_air_control's fault threshold is warn_pct", LEVELS, False),
        ("fault_pct is the warn threshold of supply_air_control", LEVELS, False),
        ("supply_air_control has no fault threshold", LEVELS, False),
        ("supply_air_reset_compliance has no fault threshold", RESET_LEVELS, True),
        ("supply_air_reset_compliance has no warn threshold", RESET_LEVELS, False),
        (
            "The leaking_valve parameter list does not include a separate warn or fault threshold",
            NO_LEVELS,
            True,
        ),
        ("leaking_valve has no threshold", NO_LEVELS, False),
    ],
)
def test_verdict_levels_are_read_as_listed(judge, draft, fact, ok):
    assert _ok(judge, f"{draft} [{fact['id']}].", fact) == ok


@pytest.mark.parametrize(
    "draft,fact,ok",
    [
        ("- supply_air_control gated on trended occupancy", GATED, True),
        ("- supply_air_control gated on trended occupancy", UNGATED, False),
        ("supply_air_control on AHU-1 is gated on trended humidity", GATED, False),
        ("The supply_air_control rule uses trended occupancy", GATED, True),
    ],
)
def test_a_gate_is_said_of_the_rule_whose_fact_names_it(judge, draft, fact, ok):
    assert _ok(judge, f"{draft} [F5@rule_run-aaaaaa].", fact) == ok


# --------------------------------------------------------------------------- 16b. dev strips
# (round 16b) The 19 true dev sentences round 16 stripped, with their cited facts as issued, and
# the 2 dev escapes it closed, which must stay closed (R16-DEV-STRIPS.md, dev units only).

_MV = (
    '"Fox_assembly_Adrianne__chilledwater" mv_savings (info): '
    '"Fox_assembly_Adrianne__chilledwater": avoided energy -1,615,675 (-15.8%) ± 1,165,919 at 90% '
    "over 340 reporting days; baseline coverage in_range; declined false; method_declared false; "
    "sep_range_valid true; null: baseline_version, fsu_extrapolation_factor; baseline projected "
    "10,249,298, reporting actual 11,864,973; energy in the meter's own units (its trended rate "
    "summed over hours; the run config names no energy unit); the baseline does not meet G14 "
    "acceptance, so this saving is for information only; baseline period 2016-01-01 to "
    "2016-12-31 (the year 2016), reporting period 2017-01-01 to 2017-12-31 (the year 2017)"
)
_K1 = (
    "3 rule(s) skipped for missing inputs on ds-synthetic-g36-vav-fleet, so not judged: "
    "airflow_tracking (missing airflow, airflow_sp); reheat_minimization_g36 (missing heat_valve, "
    "airflow, airflow_min_sp or airflow_sp); unmet_setpoint_hours (missing space_temp)"
)
_R1_BIAS = (
    '"{eq}" "supply_air_temp" against its configured reference "{ref}" disagrees (warn): the '
    "point is suspect, and a finding that rests on it is conditional on this sensor: {eq} "
    "supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 days "
    "-- biased +3.0; bias +3.0 °F, RMSE 3.0 °F: the bias is the median of the point minus the "
    "reference, so the point reads high against it; drift not evaluated because the overlap "
    "spans 4.0 days, under the 28 days a drift rate needs; 384 paired samples from 2025-05-19 "
    "00:00:00 to 2025-05-22 23:45:00"
)
_COND = (
    "conditional on sensor_drift:supply_air_temp warn (AHU-1 supply_air_temp: bias +3.0, drift "
    "not evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 days -- biased +3.0) on AHU-1"
)
_LEAK_F2 = (
    '"AHU-1" leaking_valve (fault): "AHU-1": with both valves shut, air shifts (median +3.4F '
    "beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples (worst: HW "
    f"(heating) coil); {_COND}; fan_gated true"
)
_N1 = (
    "5 saving(s) rest on a baseline that does not meet G14 acceptance: for information only, not "
    "a verified saving."
)


def _f(fid, kind, text, **kw):
    return {"id": fid, "kind": kind, "text": text, **kw}


_STRIPS = [
    # not-judged naming
    (
        "1",
        "The savings result reports declined false and baseline coverage in_range "
        "[F3@mv_summary-f151e8].",
        [_f("F3@mv_summary-f151e8", "finding", _MV, rule="mv_savings")],
    ),
    (
        "2",
        "- The result reports declined false [F3@mv_summary-f00b5b].",
        [_f("F3@mv_summary-f00b5b", "finding", _MV, rule="mv_savings")],
    ),
    (
        "3",
        "- The rule run covered 56 equipment and skipped 0 rule-equipment pairs for missing "
        "inputs [F1@rule_run-69a9a1].",
        [
            _f(
                "F1@rule_run-69a9a1",
                "rule_run",
                "12 rules were run on 56 equipment of "
                "ds-synthetic-g36-vav-fleet and 12 of them returned a result, 167 rule-equipment "
                "result(s) in all: 34 fault, 10 warn, 3 info and 120 ok; 0 "
                "rule-equipment pair(s) (0 "
                "rule(s)) skipped for missing inputs",
            )
        ],
    ),
    (
        "4",
        "airflow_tracking, reheat_minimization_g36 and unmet_setpoint_hours were skipped for "
        "missing inputs, so they were not judged. [K1@rule_run-1a9192]",
        [_f("K1@rule_run-1a9192", "skipped_rules", _K1)],
    ),
    (
        "5",
        "- Several rules were skipped for missing inputs: airflow tracking, reheat minimization "
        "and unmet-setpoint hours [K1@rule_run-ffb985].",
        [_f("K1@rule_run-ffb985", "skipped_rules", _K1)],
    ),
    (
        "6",
        "- Several other rules were skipped for missing inputs, such as airflow and space "
        "temperature [K1@rule_run-093faa].",
        [_f("K1@rule_run-093faa", "skipped_rules", _K1)],
    ),
    (
        "7",
        "- The rule's caveat says hunting was not evaluated because the ~15-min sampling is too "
        "coarse, so a hunting loop is aliased away [V1@rule_run-3ac45d].",
        [
            _f(
                "V1@rule_run-3ac45d",
                "caveat",
                '"AHU-1", "AHU-2" control_hunting caveat: hunting not '
                "evaluated: the trend's sampling (~15-min) can show at most "
                "4.0 reversals/hr, below "
                "the 6/hr warn threshold -- a hunting loop is aliased away; re-trend at 1-5 min to "
                "judge (observed reversals on 0% of the resolvable maximum)",
                rule="control_hunting",
            )
        ],
    ),
    (
        "8",
        'The drift of the "AHU__sat_bias" supply_air_temp against its reference was not '
        "evaluated. [R1@sensor_trust-f6d8f7]",
        [
            _f(
                "R1@sensor_trust-f6d8f7",
                "reference_comparison",
                _R1_BIAS.format(eq="AHU__sat_bias", ref="duct logger"),
                equip="AHU__sat_bias",
                role="supply_air_temp",
            )
        ],
    ),
    # reason
    (
        "9",
        "Five savings rest on a baseline that does not meet acceptance, so they are for "
        "information only and not verified savings. [N1@mv_summary-c5b9f8]",
        [_f("N1@mv_summary-c5b9f8", "notice", _N1)],
    ),
    (
        "10",
        "- Five savings rest on baselines that fail G14 acceptance, so they are not verified "
        "savings [N1@mv_summary-84f0bf].",
        [_f("N1@mv_summary-84f0bf", "notice", _N1)],
    ),
    (
        "11",
        'The "chw_flow" point is suspect at trust 0.57 because it is clipped and has outliers. '
        "[F2@sensor_trust-99f248]",
        [
            _f(
                "F2@sensor_trust-99f248",
                "point_trust",
                '"PLANT__short_cycling" "chw_flow": trust 0.57 '
                "(0.5723) (suspect; clipped, outliers); coverage 100% of its own span",
                equip="PLANT__short_cycling",
                role="chw_flow",
            )
        ],
    ),
    (
        "12",
        "It is rated warn because its condenser-water supply holds a flat setpoint, with a "
        "median of 85.0F and no wet-bulb reset [F2@rule_run-e77cab].",
        [
            _f(
                "F2@rule_run-e77cab",
                "finding",
                '"PLANT__fixed_cw" condenser_water_reset (warn): '
                '"PLANT__fixed_cw": CW supply median 85.0F, slope 0.07 F/F '
                "vs wet-bulb; flat setpoint "
                "(no wet-bulb reset); reset_present false",
                rule="condenser_water_reset",
                equip="PLANT__fixed_cw",
            )
        ],
    ),
    (
        "13",
        "The leaking_valve rule allows 2F of fan heat, and on AHU-1 it found a median shift of "
        "3.4F beyond that allowance with both valves shut, so it "
        "raised a fault with 86% of samples "
        "flagging a heating-coil leak [F2@rule_run-5f3653].",
        [_f("F2@rule_run-5f3653", "finding", _LEAK_F2, rule="leaking_valve", equip="AHU-1")],
    ),
    (
        "14",
        "The tool judged 24 points and found 21 trusted, 3 suspect and 0 untrusted, so none "
        "fall below the 0.5 trust floor [F1@sensor_trust-7e3116].",
        [
            _f(
                "F1@sensor_trust-7e3116",
                "trust_summary",
                "site-north: 24 points judged (rules view: as "
                "the rule runner reads its inputs): 21 trusted, 3 suspect, 0 "
                "untrusted; 0 below the "
                "trust floor 0.5",
                facility_id="site-north",
            )
        ],
    ),
    (
        "15",
        "It reads +3.0 °F high over 384 samples across 4 days, so any finding that depends on "
        "it is conditional [R1@sensor_trust-c497d2].",
        [
            _f(
                "R1@sensor_trust-c497d2",
                "reference_comparison",
                _R1_BIAS.format(eq="AHU-1", ref="handheld reference logger"),
                equip="AHU-1",
                role="supply_air_temp",
            )
        ],
    ),
    (
        "16",
        "AHU-1's sensor reads about 3 °F high against a reference logger, so its result is "
        "only conditional. [F2@rule_run-de9ea9] [F3@rule_run-de9ea9] [R1@sensor_trust-252277]",
        [
            _f(
                "F2@rule_run-de9ea9",
                "finding",
                '"AHU-1" supply_air_control (ok): "AHU-1": SAT off '
                "setpoint 0% of occupied running hours (mean |Δ| 0.1°F; warm 0%, cold 0%); "
                f'{_COND}; gates: occupancy_gate "trended occupancy"',
                rule="supply_air_control",
                equip="AHU-1",
            ),
            _f(
                "F3@rule_run-de9ea9",
                "finding",
                '"AHU-2" supply_air_control (ok): "AHU-2": SAT off '
                "setpoint 0% of occupied running hours (mean |Δ| 0.1°F; warm 0%, cold 0%); gates: "
                'occupancy_gate "trended occupancy"',
                rule="supply_air_control",
                equip="AHU-2",
            ),
            _f(
                "R1@sensor_trust-252277",
                "reference_comparison",
                _R1_BIAS.format(eq="AHU-1", ref="handheld reference logger"),
                equip="AHU-1",
                role="supply_air_temp",
            ),
        ],
    ),
    (
        "17",
        "9. The AHU-1 supply_air_control result was ok, but it also depends on the biased "
        "sensor, so it is conditional [C1@rule_run-7d3644].",
        [
            _f(
                "C1@rule_run-7d3644",
                "conditional_not_listed",
                "1 result(s) not listed here also rest "
                "on an input whose configured sensor reference disagrees with it, so each is "
                'conditional on that sensor (a pass too): "AHU-1" supply_air_control (ok)',
            )
        ],
    ),
    # (round 16c) a check not judged for the inputs its skip fact says it misses
    (
        "16c",
        "- the simultaneous heat/cool rule was not judged because heat valve and cool valve "
        "points are missing [K1@rule_run-2a2f27].",
        [
            _f(
                "K1@rule_run-2a2f27",
                "skipped_rules",
                "1 rule(s) skipped for missing inputs on ds-synthetic-g36-vav-fleet, so not "
                'judged: simultaneous_heat_cool on "AHU__healthy" (missing heat_valve, '
                "cool_valve)",
            )
        ],
    ),
    # deictic: the count of its own list, as the last bullet of a longer list
    (
        "18",
        "".join(f'- "AHU__healthy" rule_{i} is ok [K1@rule_run-b8a5d5].\n' for i in range(8))
        + "- These three rules were skipped for missing inputs, so they were not judged: "
        "airflow_tracking, reheat_minimization_g36 and unmet_setpoint_hours [K1@rule_run-b8a5d5].",
        [_f("K1@rule_run-b8a5d5", "skipped_rules", _K1)],
    ),
    # rule-id method words
    (
        "19",
        "- leaking_valve used a 2F fan-heat allowance and was fan-gated [F2@rule_run-8e277f].",
        [_f("F2@rule_run-8e277f", "finding", _LEAK_F2, rule="leaking_valve", equip="AHU-1")],
    ),
]

_ESCAPES = [
    (
        "A",
        "| Zone | Rule |\n|---|---|\n"
        + "".join(f"| VAV{i} | reheat |\n" for i in range(1, 8))
        + "\nThese are the 8 faults in the run. [F1@rule_run-2217b5]",
        [
            _f(
                "F1@rule_run-2217b5",
                "rule_run",
                "5 rules were run on 56 equipment of "
                "ds-synthetic-g36-vav-fleet and 4 of them returned a result, 145 rule-equipment "
                "result(s) in all: 8 fault, 0 warn, 51 info and 86 ok; 8 rule-equipment pair(s) (1 "
                "rule(s)) skipped for missing inputs: those pairs were not judged",
            )
        ],
    ),
    (
        "B",
        "Tower approach and bypass-leak checks weren't judged. [F1@rule_run-d73c9c]",
        [
            _f(
                "F1@rule_run-d73c9c",
                "rule_run",
                "5 rules were run on 5 equipment of "
                "ds-synthetic-chw-plant-staging and 4 of them returned a result, 16 rule-equipment "
                "result(s) in all: 2 fault, 2 warn, 4 info and 8 ok; 5 rule-equipment pair(s) (1 "
                "rule(s)) skipped for missing inputs: those pairs were not judged",
            )
        ],
    ),
]


def _last(judge, draft, facts):
    r = judge({"key": "k", "draft": draft, "facts": facts, "allowed": [], "names": {}})
    assert "error" not in r, r
    return r["sentences"][-1]


@pytest.mark.parametrize("item,draft,facts", _STRIPS, ids=[s[0] for s in _STRIPS])
def test_round16_dev_strip_is_supported_again(judge, item, draft, facts):
    _text, verdict, why = _last(judge, draft, facts)
    assert verdict == "supported", (item, why)


@pytest.mark.parametrize("item,draft,facts", _ESCAPES, ids=[s[0] for s in _ESCAPES])
def test_round16_closed_escape_stays_closed(judge, item, draft, facts):
    _text, verdict, why = _last(judge, draft, facts)
    assert verdict != "supported", item


def test_round16c_a_skip_reason_must_be_the_missing_inputs(judge):
    fact = _f(
        "K1@rule_run-2a2f27",
        "skipped_rules",
        "1 rule(s) skipped for missing inputs on ds-synthetic-g36-vav-fleet, so not judged: "
        'simultaneous_heat_cool on "AHU__healthy" (missing heat_valve, cool_valve)',
    )
    draft = "The simultaneous heat/cool rule was not judged because {} [K1@rule_run-2a2f27]."
    assert _ok(judge, draft.format("the heat valve and cool valve points are missing"), fact)
    assert not _ok(judge, draft.format("the supply fan was off"), fact)
    assert not _ok(judge, draft.format("the zone temperature is untrusted"), fact)
