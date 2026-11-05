"""What the 0.112 acceptance test found check_citations getting wrong (#328), case by case: a
sentence that says the opposite of its cited fact must not be "supported" (negation and
direction both ways, "passes", "one", "none", claims in headings, a result restated as a
threshold, another facility, a name from the data), and a correct sentence must stay supported
(a fact's own text, "not judged", compound facts, ranks, roles, rule names, savings restated).
The facts are the testers', cut down and with a made-up facility.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets  # noqa: E402
from camber.agent.verify import _judge, _sentences, check  # noqa: E402
from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import (  # noqa: E402
    REMOVED,
    _context,
    check_citations,
)
from camber.capabilities._cap_mv_summary import _claim_facts  # noqa: E402
from camber.capabilities._common import finding_fact  # noqa: E402

REG = default_registry()
FAC = "fac-a"

TRUST = {
    "id": "F1@sensor_trust-aaaaaa",
    "kind": "trust_summary",
    "facility_id": FAC,
    "value": 0,
    "text": f"{FAC}: 11 matching points judged (rules view): 9 trusted, 2 suspect, 0 untrusted; "
    "0 below the trust floor 0.5",
}
REF = {
    "id": "R1@sensor_trust-aaaaaa",
    "kind": "reference_comparison",
    "equip": "AHU__sat_bias",
    "role": "supply_air_temp",
    "value": "warn",
    "verdict": "warn",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "role": "supply_air_temp"},
    "text": '"AHU__sat_bias" "supply_air_temp" against its configured reference "duct logger" '
    "disagrees (warn): the point is suspect: AHU__sat_bias supply_air_temp: bias +3.0, drift not "
    "evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 days -- biased +3.0; bias +3.0 °F is the "
    "median of the point minus the reference, so the point reads high against it",
}
FLAT = {
    "id": "L1@sensor_trust-aaaaaa",
    "kind": "flat_point",
    "equip": "AHU__sat_bias",
    "role": "heat_valve",
    "value": 0.0,
    "unit": "%",
    "verdict": "flat",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "role": "heat_valve"},
    "text": '"AHU__sat_bias" "heat_valve" never moves over the window: all 2,016 valid samples '
    "from 2025-05-12 00:00 to 2025-06-01 23:45 read 0 %, so it is flat for the whole window",
}
LEAK = {
    "id": "F2@rule_run-aaaaaa",
    "kind": "finding",
    "value": "fault",
    "rule": "leaking_valve",
    "equip": "AHU__sat_bias",
    "facility_id": FAC,
    "finding_id": "leaking_valve@AHU__sat_bias",
    "text": '"AHU__sat_bias" leaking_valve (fault): "AHU__sat_bias": with both valves shut, air '
    "shifts (median +3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut "
    "samples (worst: HW (heating) coil); conditional on sensor_drift:supply_air_temp warn "
    "(AHU__sat_bias supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 "
    "samples / 4 days -- biased +3.0) on AHU__sat_bias",
}
RESET = {
    "id": "F3@rule_run-aaaaaa",
    "kind": "finding",
    "value": "warn",
    "rule": "supply_air_reset_compliance",
    "equip": "AHU__sat_bias",
    "text": '"AHU__sat_bias" supply_air_reset_compliance (warn): "AHU__sat_bias": SAT median '
    "55.0°F vs configured target 57.1°F; below target 54% of hours (mean gap +3.1°F)",
}
REJUDGED = {
    "id": "R1@finding_detail-bbbbbb",
    "kind": "reference_effect",
    "value": "ok",
    "verdict": "explained",
    "severity": "fault",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "finding": "leaking_valve@x"},
    "text": '"AHU__sat_bias" leaking_valve (fault) re-judged with its input(s) corrected for the '
    'configured reference\'s bias ("supply_air_temp" by -3.0 °F): ok, "AHU__sat_bias": no valve '
    "leak -- with both valves shut, air holds near the 2F fan-heat allowance (median +0.4F "
    "beyond it); HW-leak 0% / CHW-leak 0% of both-valves-shut samples; so the finding is "
    "explained by the sensor offset",
}
WINDOW = {
    "id": "R2@finding_detail-bbbbbb",
    "kind": "reference_window",
    "value": 0.0,
    "unit": "h",
    "verdict": "outside",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "role": "supply_air_temp"},
    "text": 'None of the 8 flagged hours of "AHU__sat_bias" leaking_valve (fault) falls in the '
    'window the "supply_air_temp" reference comparison on "AHU__sat_bias" covers (2025-05-19 '
    "00:00 to 2025-05-22 23:45, warn): the reference measured the sensor on other days than "
    "the finding's evidence",
}
HOURS = {
    "id": "F2@finding_detail-bbbbbb",
    "kind": "evidence_hours",
    "value": 8.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "finding": "leaking_valve@x"},
    "text": "The rule flagged 8 hours (32 of 37 judged both-valves-shut samples, 86.5%) in 3 "
    "contiguous span(s), the longest 3 hours",
}
MV_BASE = {
    "id": "F4@mv_summary-cccccc",
    "kind": "finding",
    "value": "info",
    "rule": "mv_baseline",
    "equip": "meter_A",
    "text": '"meter_A" mv_baseline (info): "meter_A": 2P baseline, R2 0.36, CV(RMSE) 10.0% over '
    "366 days -- does not meet daily acceptance (G14 criteria; the 30% daily CV(RMSE) tier is "
    "CAMBER's): R2 0.36 < 0.75",
}
MV_OK = {
    "id": "F2@mv_summary-cccccc",
    "kind": "finding",
    "value": "ok",
    "rule": "mv_baseline",
    "equip": "meter_B",
    "text": '"meter_B" mv_baseline (ok): "meter_B": 3P cooling baseline, R2 0.99, CV(RMSE) 1.0% '
    "over 366 days -- meets daily acceptance (G14 criteria; the 30% daily CV(RMSE) tier is "
    "CAMBER's)",
}
ISSUE1 = {
    "id": "I1@aaaa1111-dddddd",
    "kind": "issue",
    "equip": "AHU__sat_bias",
    "text": 'Issue 1: Sensor drift: supply air temp, on "AHU__sat_bias"; severity warn, '
    "confidence M.",
    "data": {"rank": 1},
}
ISSUE2 = {
    "id": "I1@bbbb2222-dddddd",
    "kind": "issue",
    "equip": "AHU__sat_bias",
    "text": 'Issue 2: Heating valve passes flow when closed, on "AHU__sat_bias"; severity fault, '
    "confidence L.",
    "data": {"rank": 2},
}
K3 = {
    "id": "K3@summary-dddddd",
    "kind": "kpi",
    "facility_id": FAC,
    "text": "9 issue(s) are uncosted and 2 are conditional on a sensor fix.",
}
TOWER = {
    "id": "F1@cccc3333-dddddd",
    "kind": "finding",
    "equip": "TOWER__full_fan",
    "text": '"TOWER__full_fan": tower fan speed +55.3%-pts vs the reference "TOWER__well_run" '
    "at matched load and wet-bulb; hours at >= 90% 7% -> 100%",
}
SWING = {
    "id": "F11@rule_run-eeeeee",
    "kind": "finding",
    "value": "ok",
    "rule": "sat_reset_effectiveness",
    "equip": "AHU__rogue_sat",
    "text": '"AHU__rogue_sat" sat_reset_effectiveness (ok): "AHU__rogue_sat": supply-air-temp '
    "reset tracks its request-implied T&R trajectory (actual swing 10.05 vs expected 10 degF) "
    "-- effective",
}
STATIC = {
    "id": "F10@rule_run-eeeeee",
    "kind": "finding",
    "value": "ok",
    "rule": "static_reset_effectiveness",
    "equip": "AHU__healthy",
    "text": '"AHU__healthy" static_reset_effectiveness (ok): "AHU__healthy": duct-static reset '
    "tracks its request-implied T&R trajectory (actual swing 0.83 vs expected 0.35 in. w.c.) -- "
    "effective",
}
RUN = {
    "id": "F1@rule_run-eeeeee",
    "kind": "rule_run",
    "facility_id": FAC,
    "value": 11,
    "text": f"12 rules were run on 2 equipment of {FAC} and 9 of them returned a result: 0 fault, "
    "0 warn, 7 info and 4 ok; 3 rule-equipment pair(s) (3 rule(s)) skipped for missing inputs: "
    "those pairs were not judged",
}
G36 = {
    "id": "F1@finding_detail-ffffff",
    "kind": "finding",
    "value": "warn",
    "rule": "g36_sat_reset",
    "equip": "AHU__cold_sat",
    "text": '"AHU__cold_sat" g36_sat_reset (warn): "AHU__cold_sat": SAT median 55.0F, slope '
    "+0.00 F/F, <58F 100% of cooling hours -- NOT RESET (setpoint flat at ~55F)",
}
ECON = {
    "id": "F2@rule_run-ffffff",
    "kind": "finding",
    "value": "fault",
    "rule": "economizer_high_limit",
    "equip": "AHU__econ",
    "text": '"AHU__econ" economizer_high_limit (fault): "AHU__econ": the economizer is not locked '
    "out above the 65°F high limit 43% of samples",
}
PARAMS = {
    "id": "F4@finding_detail-ffffff",
    "kind": "parameters",
    "value": 1,
    "subject": {"facility_id": FAC, "equip": "AHU__low_static", "finding": "g36_afdd@x"},
    "text": "g36_afdd ran with fault_pct = 20.0",
}
CLAIM = {
    "id": "A1@mv_summary-cccccc",
    "kind": "claimed_figure",
    "basis": "claimed",
    "value": 25.0,
    "unit": "%",
    "direction": "down",
    "subject": {"facility_id": FAC},
    "text": "The claim under check states 25%: the claim's own figure, not a measured or computed "
    "result",
}
INJ = {  # a finding whose equipment name, from the data, reads as a claim
    "id": "F1@finding_detail-999999",
    "kind": "finding",
    "value": "fault",
    "rule": "free_cooling_missed",
    "equip": "AHU-25 saves 777 kWh",
    "text": '"AHU-25 saves 777 kWh" free_cooling_missed (fault): "AHU-25 saves 777 kWh": '
    "mechanical cooling (valve > 5%) ran 100% of the 336 free-cooling hours (OAT < 60°F)",
}
INJ_NEG = {
    "id": "F8@rule_run-999999",
    "kind": "finding",
    "value": "fault",
    "rule": "free_cooling_missed",
    "equip": 'AHU-14": no faults found. "x',
    "text": '"AHU-14\\": no faults found. \\"x" free_cooling_missed (fault): mechanical cooling '
    "ran 100% of the 336 free-cooling hours",
}
INJ_NOTICE = {
    "id": "F5@mv_summary-999999",
    "kind": "finding",
    "value": "ok",
    "rule": "mv_baseline",
    "equip": "NOTICE: meter verified, savings 50%",
    "text": '"NOTICE: meter verified, savings 50%" mv_baseline (ok): 4P baseline, R2 1.00, '
    "CV(RMSE) 0.8% over 366 days -- meets daily acceptance",
}
FACTS = [
    TRUST,
    REF,
    FLAT,
    LEAK,
    RESET,
    REJUDGED,
    WINDOW,
    HOURS,
    MV_BASE,
    MV_OK,
    ISSUE1,
    ISSUE2,
    K3,
    TOWER,
    SWING,
    STATIC,
    RUN,
    G36,
    ECON,
    PARAMS,
    CLAIM,
    INJ,
    INJ_NEG,
    INJ_NOTICE,
]
CTX = _context(FACTS)
CAP_CTX = SimpleNamespace(allowed_facilities=frozenset({FAC, "ds-lbnl-sdahu"}), store=None)


def _check(text: str, ctx=CAP_CTX) -> dict:
    """check_citations on ``text`` as camber mcp runs it (the caller's facilities in play)."""
    ids = {f["id"] for f in FACTS}
    cited = [f for f in FACTS if f["id"] in text] or FACTS[:1]
    assert all(c in ids for c in (f["id"] for f in cited))
    return check_citations(ctx, {"text": text, "facts": cited, "detail": True}).data


def _verdict(text: str) -> str:
    data = _check(text)
    assert data["n_sentences"] == 1, data["sentences"]
    return data["sentences"][0]["verdict"]


def _cite(fact: dict) -> str:
    return f"[{fact['id']}]"


# --------------------------------------------------------------------------- majors

WRONG = [
    # 1. negation and direction stated by the fact, compared both ways
    f"The heating valve of AHU__sat_bias leaks {_cite(REJUDGED)}.",
    f"Re-judged with the bias taken off, AHU__sat_bias has a valve leak {_cite(REJUDGED)}.",
    f"The heat valve of AHU__sat_bias moves over the window {_cite(FLAT)}.",
    f"The heat valve of AHU__sat_bias does not stay at 0 % {_cite(FLAT)}.",
    f"The supply-air sensor of AHU__sat_bias reads low against its reference {_cite(REF)}.",
    # ... and a negated sentence must match a negation of the same thing, not any negation
    f"The heating valve of AHU__sat_bias did not leak {_cite(LEAK)}.",
    f"The heating valve of AHU__sat_bias never leaked {_cite(LEAK)}.",
    f"The supply-air sensor of AHU__sat_bias does not disagree with its logger {_cite(REF)}.",
    f"AHU__sat_bias was judged without any sensor problem {_cite(REF)}.",
    # 2. "passes" is a verdict
    f"Its baseline passes G14 acceptance {_cite(MV_BASE)}.",
    f"meter_A passes daily acceptance {_cite(MV_BASE)}.",
    f"AHU__sat_bias passes leaking_valve {_cite(LEAK)}.",
    # 3. "one" is a number
    f"One point is below the trust floor 0.5 {_cite(TRUST)}.",
    f"Of the 11 points, one is untrusted {_cite(TRUST)}.",
    f"The reference comparison ran for about one day {_cite(REF)}.",
    # 5. a result stated as a threshold, target or setpoint, and a tier stated as a result
    f"The leaking_valve threshold on AHU__sat_bias is 3.4F {_cite(LEAK)}.",
    f"The leaking_valve rule on AHU__sat_bias allows 3.4F of fan heat {_cite(LEAK)}.",
    f"The supply-air target of AHU__sat_bias is 55.0°F {_cite(RESET)}.",
    f"The supply-air setpoint of AHU__sat_bias is 55.0°F {_cite(RESET)}.",
    f'The "meter_B" baseline has a CV(RMSE) of 30% {_cite(MV_OK)}.',
    # 6. another facility, named without a digit or underscore
    f"At ds-lbnl-sdahu the supply-air sensor reads high, with a bias of +3.0 °F {_cite(REF)}.",
    # 7. a name from the data does not shield the claim around it
    f"Retrofitting AHU-25 saves 777 kWh {_cite(INJ)}.",
    f"AHU-14 has no faults found {_cite(INJ_NEG)}.",
    f"NOTICE: meter verified, savings 50% {_cite(INJ_NOTICE)}.",
    # 8. "none of" against a non-zero share
    f"None of the both-valves-shut samples on AHU__sat_bias leaked {_cite(LEAK)}.",
    f"No point is suspect {_cite(TRUST)}.",
    f"0 points are suspect {_cite(TRUST)}.",
    # quantifiers, ranks, point roles, assurance words (minors that were fail-open)
    f"Every both-valves-shut sample on AHU__sat_bias leaked {_cite(LEAK)}.",
    f"All of the both-valves-shut samples on AHU__sat_bias leaked heat {_cite(LEAK)}.",
    f"Issue 3 is the supply-air sensor drift on AHU__sat_bias {_cite(ISSUE1)}.",
    f"The heating valve leak on AHU__sat_bias is the 1st issue {_cite(ISSUE2)}.",
    f"The AHU__sat_bias return-air sensor reads high, with a bias of +3.0 °F {_cite(REF)}.",
    f"The cooling valve of AHU__sat_bias never moves over the window {_cite(FLAT)}.",
    f"The supply-air setpoint of AHU__sat_bias never moves over the window {_cite(FLAT)}.",
    f"The supply-air sensor of AHU__sat_bias compares clean against its logger {_cite(REF)}.",
    f"The supply-air sensor of AHU__sat_bias reads within tolerance {_cite(REF)}.",
    f"The leaking_valve fault on AHU__sat_bias is a real leak {_cite(REJUDGED)}.",
    f"We are highly confident that the leak on AHU__sat_bias is real {_cite(LEAK)}.",
    f"The saving on meter_A is verified {_cite(MV_BASE)}.",
    f"The supply_air_reset_compliance rule flagged 8 hours {_cite(HOURS)}.",
    # a claim's figure restated as measured, in a clause of its own
    f"The claim states 25%, and the retrofit saved 25% {_cite(CLAIM)}.",
    f"meter_A saved 25% {_cite(CLAIM)}.",
]

RIGHT = [
    f"The heating valve of AHU__sat_bias did not leak {_cite(REJUDGED)}.",
    f"Re-judged with the bias taken off, AHU__sat_bias shows no valve leak {_cite(REJUDGED)}.",
    f"The leaking_valve fault on AHU__sat_bias is explained by the sensor offset "
    f"{_cite(REJUDGED)}.",
    f"The heat valve of AHU__sat_bias never moves over the window {_cite(FLAT)}.",
    f"The supply-air sensor of AHU__sat_bias reads high against its reference {_cite(REF)}.",
    f"Drift was not evaluated {_cite(REF)}.",
    f"Its baseline fails G14 acceptance {_cite(MV_BASE)}.",
    f"Its baseline does not meet G14 acceptance {_cite(MV_BASE)}.",
    f"The 10.0% figure is for information only until a baseline passes G14 {_cite(MV_BASE)}.",
    f"The baseline needs to pass G14 acceptance {_cite(MV_BASE)}.",
    f'The "meter_B" baseline passes daily acceptance with CV(RMSE) 1.0% {_cite(MV_OK)}.',
    f"Issue 2 is a heating valve that passes flow when closed {_cite(ISSUE2)}.",
    f"The leaking_valve check on AHU__sat_bias is not a pass: it is a fault {_cite(LEAK)}.",
    f"Issue 1 is the supply-air sensor drift on AHU__sat_bias, at confidence M {_cite(ISSUE1)}.",
    # 8. a fact's own text, and zeros it states
    f"{WINDOW['text']} {_cite(WINDOW)}.",
    f"None of the 8 flagged hours of that finding falls in the window the reference comparison "
    f"covers {_cite(WINDOW)}.",
    f"None of the 11 points is below the trust floor 0.5 {_cite(TRUST)}.",
    f"Of the 11 points, none is below the trust floor 0.5 {_cite(TRUST)}.",
    f"0 points are below the trust floor 0.5 {_cite(TRUST)}.",
    f"There are no untrusted points {_cite(TRUST)}.",
    f"9 points are trusted and 2 suspect {_cite(TRUST)}.",
    # 5. thresholds as thresholds
    f"The leaking_valve rule on AHU__sat_bias allows 2F of fan heat {_cite(LEAK)}.",
    f"The SAT median is 55.0°F against a configured target of 57.1°F {_cite(RESET)}.",
    f"The economizer high limit is 65°F {_cite(ECON)}.",
    f"Check the economizer high-limit lockout at 65°F {_cite(ECON)}.",
    f"The fault threshold is fault_pct = 20.0 {_cite(PARAMS)}.",
    f"Its setpoint is flat at ~55F {_cite(G36)}.",
    f"The supply air of AHU__cold_sat is not reset {_cite(G36)}.",
    # compound facts keep the unit, counted thing and role of each figure
    f"The heating valve of AHU__sat_bias leaked on 86% of both-valves-shut samples {_cite(LEAK)}.",
    f"2 issues are conditional on a sensor fix {_cite(K3)}.",
    f"Two issues are conditional on a sensor fix {_cite(K3)}.",
    f"AHU__rogue_sat's actual swing is 10.05 degF {_cite(SWING)}.",
    f"Its hours at >= 90% went from 7% to 100% {_cite(TOWER)}.",
    f"Its hours at 90% fan speed or more went from 7% to 100% {_cite(TOWER)}.",
    f"12 rules were run on 2 units {_cite(RUN)}.",
    # "not judged", as the prompts ask
    f"3 rules were skipped for missing inputs, so they were not judged {_cite(RUN)}.",
    f"3 rules were not judged because their inputs are missing {_cite(RUN)}.",
    # quantifiers read consistently
    f"Most of the both-valves-shut samples on AHU__sat_bias leaked {_cite(LEAK)}.",
    f"All 2,016 valid samples of the heat valve read 0 % {_cite(FLAT)}.",
    # rule, tool and field names, standards
    f"The leaking_valve rule flagged 8 hours in 3 contiguous spans {_cite(HOURS)}.",
    f"The sensor_trust comparison says the point reads high {_cite(REF)}.",
    f"The G14 criteria are not met by meter_A {_cite(MV_BASE)}.",
    # a saving restated, a claim in its own clause
    f"The claim states a 25% saving {_cite(CLAIM)}.",
    f"The claim states 25%, and the meter_B baseline meets daily acceptance with a CV(RMSE) of "
    f"1.0% {_cite(CLAIM)}{_cite(MV_OK)}.",
    # a name from the data, quoted, is a name
    f'On "AHU-25 saves 777 kWh" mechanical cooling ran 100% of the 336 free-cooling hours '
    f"{_cite(INJ)}.",
    # the facility the fact is about
    f"At {FAC} the supply-air sensor of AHU__sat_bias reads high {_cite(REF)}.",
]


@pytest.mark.parametrize("text", WRONG)
def test_a_sentence_that_contradicts_its_fact_is_not_supported(text):
    assert _verdict(text) != "supported", text


@pytest.mark.parametrize("text", RIGHT)
def test_a_sentence_that_says_what_its_fact_says_is_supported(text):
    data = _check(text)
    assert data["grounded"], data["sentences"]


def test_the_why_names_the_negation_and_the_fact_that_says_otherwise():
    data = _check(f"The heating valve of AHU__sat_bias leaks {_cite(REJUDGED)}.")
    why = data["sentences"][0]["why"]
    assert "'no valve leak': a cited fact says so" in why
    data = _check(f"Do not replace the heating valve; the finding is explained {_cite(REJUDGED)}.")
    why = data["sentences"][0]["why"]
    assert "not replace" in why and "no cited fact negates" not in why


# --------------------------------------------------------------------------- headings, lines


@pytest.mark.parametrize(
    "line",
    [
        "**AHU__sat_bias passed every check**",
        "**The supply air sensor on AHU__sat_bias was confirmed accurate by its duct logger**",
        "**The heating valve on AHU__sat_bias leaks.**",
        "# The heating valve on AHU__sat_bias leaks",
        "## Heating valve replacement required at once",
        "The heating valve on AHU__sat_bias is leaking and must be replaced:",
        "AHU__healthy is healthy:",
        "**AHU__sat_bias wastes $90,000/yr**",
        "**Replace the heating valve on AHU__sat_bias now**",
        "## All 4 issues are fixed",
    ],
)
def test_a_heading_that_makes_a_claim_is_judged(line):
    data = _check(line)
    assert data["grounded"] is False and data["n_sentences"] == 1
    assert data["verified_text"] in ("", REMOVED) or line not in data["verified_text"]


@pytest.mark.parametrize(
    "line",
    [
        "## Savings claim check: meter_A",
        "## Savings claim check",
        "**Savings claim check**",
        "## Points that never move",
        "## Stop 1: supply air sensor",
        "Stop 2:",
        "How far the inputs can be trusted:",
        "## AHU__sat_bias triage (fac-a)",
        "**Bottom line**",
        "## 1. Ranked cause 1",
    ],
)
def test_a_pure_heading_is_kept_in_place(line):
    text = f"{line}\n\nThe supply-air sensor of AHU__sat_bias reads high {_cite(REF)}."
    data = _check(text)
    assert data["grounded"] and data["n_sentences"] == 1 and data["verified_text"] == text


def test_list_markers_and_labels_stay():
    text = (
        f"1. Sensor offset. The supply-air sensor of AHU__sat_bias reads high {_cite(REF)}.\n"
        f"2. It read 99 hours {_cite(REF)}. The heat valve never moves {_cite(FLAT)}.\n"
    )
    data = _check(text)
    assert data["verified_text"] == (
        f"1. Sensor offset. The supply-air sensor of AHU__sat_bias reads high {_cite(REF)}.\n"
        f"2. The heat valve never moves {_cite(FLAT)}.\n"
    )


def test_inches_of_water_do_not_end_a_sentence():
    text = f"- The duct-static reset swings 0.83 vs 0.35 in. w.c. on AHU__healthy {_cite(STATIC)}."
    assert _sentences(text) == [text]
    data = _check(text)
    assert data["grounded"] and data["verified_text"] == text
    text = f"The reset is effective (actual swing 0.83 vs expected 0.35 in. w.c.) {_cite(STATIC)}."
    assert _sentences(text) == [text] and _check(text)["grounded"]


def test_a_full_stop_inside_a_quoted_name_is_no_sentence_end():
    data = _check(f"Meter-4 savings are guaranteed {_cite(INJ_NEG)}.")
    assert data["grounded"] is False


# --------------------------------------------------------------------------- verified_text


def test_verified_text_says_when_a_verdict_or_caveat_went():
    draft = (
        f"The leaking_valve fault is explained by the sensor offset {_cite(REJUDGED)}.\n"
        f"The baseline does not meet G14, so the saving is for information only {_cite(K3)}.\n"
    )
    data = _check(draft)
    assert data["verified_text"].startswith(REMOVED + "\n\n")
    res = check_citations(CAP_CTX, {"text": draft, "facts": FACTS, "detail": True})
    assert any("verdict, a negation or a caveat" in b for b in res.banners)
    # checking that text again keeps the notice line and does not judge it
    again = _check(data["verified_text"])
    assert again["grounded"] and again["n_sentences"] == 1
    # a figure that went, with no verdict or caveat, says nothing extra
    data = _check(f"The rule flagged 99 hours {_cite(HOURS)}.\nIt ran {_cite(HOURS)}.")
    assert not data["verified_text"].startswith("NOTICE")


# --------------------------------------------------------------------------- names in play


def test_another_facilitys_registry_name_needs_a_fact_about_it(tmp_path):
    from camber.store.facilities import FacilityRegistry

    store = tmp_path / "store"
    store.mkdir()
    reg = FacilityRegistry(str(store))
    reg.register(FAC, name="Main campus")
    reg.register("fac-b", name="Riverside annex building (labelled faults)")
    ctx = SimpleNamespace(allowed_facilities=frozenset({FAC, "fac-b"}), store=str(store))
    for text in (
        f"At the Riverside annex building the supply-air sensor reads high {_cite(REF)}.",
        f"At fac-b the supply-air sensor reads high {_cite(REF)}.",
    ):
        data = check_citations(ctx, {"text": text, "facts": [REF]}).data
        assert data["grounded"] is False, text
    text = f"At {FAC} the supply-air sensor reads high {_cite(REF)}."
    assert check_citations(ctx, {"text": text, "facts": [REF]}).data["grounded"]


# --------------------------------------------------------------------------- facts


def test_a_skipped_or_insufficient_check_says_it_was_not_judged():
    row = {
        "equip": "AHU__damper",
        "rule": "leaking_valve",
        "severity": "info",
        "summary": "insufficient data",
        "finding_id": "leaking_valve@AHU__damper",
        "metrics": {},
    }
    fact = finding_fact("F16@rule_run-123456", row, FAC)
    assert fact["facility_id"] == FAC and fact["text"].endswith("this check was not judged")
    ctx = _context([fact])
    for text in (
        "The leaking_valve check on AHU__damper was not judged [F16@rule_run-123456].",
        "The leaking_valve check on AHU__damper returned insufficient data, so it was not judged "
        "[F16@rule_run-123456].",
    ):
        assert [j.reason for j in _judge(text, ctx, per_sentence=True)] == [""], text
    bad = "The leaking_valve check on AHU__damper was judged ok [F16@rule_run-123456]."
    assert _judge(bad, ctx, per_sentence=True)[0].reason == "unsupported-claim"


def test_a_claimed_figure_carries_the_claims_direction():
    facts = _claim_facts(FAC, "The lighting retrofit saved 12% of the electricity use in 2017.")
    assert [(f["value"], f.get("direction")) for f in facts] == [(12.0, "down"), (2017.0, None)]
    assert facts[1]["text"].startswith("The claim under check states 2017:")  # no stray comma
    up = _claim_facts(FAC, "Use fell 3.5%, and demand rose 2 kW.")
    assert [f.get("direction") for f in up] == ["down", "up"]


def test_the_summary_banner_names_no_single_reason():
    from camber.capabilities import _grounded

    text, banners = _grounded("It ran 99 hours [F1@x].", [{"id": "F1@x", "text": "8 hours"}])
    assert banners == [
        "1 sentence(s) of the summary were removed: no fact they cite supports a figure, name, "
        "verdict or negation they state"
    ]


# --------------------------------------------------------------------------- end to end

_DS, _FID = "synthetic-ahu-sensor-bias", "ds-synthetic-ahu-sensor-bias"


@pytest.fixture(scope="module")
def bias(tmp_path_factory):
    """The synthetic air handlers: AHU__sat_bias reads 3 F high against its duct logger."""
    root = tmp_path_factory.mktemp("cite328")
    store = str(root / "store")
    datasets.ingest(_DS, store, data_dir=str(root / "cache"))
    cfg = datasets.config_template(_DS, store, exercise="data-sensor-conditional")
    path = root / "bias.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return CallContext(store=store, allowed_facilities=frozenset([_FID]), config=str(path))


def test_finding_detail_keeps_its_own_reference_window_sentence(bias):
    res = REG.call(
        "finding_detail", {"facility_id": _FID, "finding_id": "leaking_valve@AHU__sat_bias"}, bias
    )
    window = [f for f in res.facts if f.get("kind") == "reference_window"]
    assert window and window[0]["text"].startswith("None of the ")
    assert f"[{window[0]['id']}]" in res.text
    assert not any("were removed" in b for b in res.banners)
    # and a model may copy it word for word
    text = f"{window[0]['text']} [{window[0]['id']}]."
    data = check_citations(SimpleNamespace(), {"text": text, "facts": res.facts}).data
    assert data["grounded"], data["sentences"]


def test_a_rule_run_on_a_unit_with_skips_can_be_cited_as_not_judged(bias):
    res = REG.call("rule_run", {"facility_id": _FID}, bias)
    head = res.facts[0]
    assert head["id"].startswith("F1@rule_run-")
    if "skipped for missing inputs: those pairs were not judged" in head["text"]:
        text = (
            f"Some rules were skipped for missing inputs, so they were not judged [{head['id']}]."
        )
        assert check(text, _context([head]), require_cite_each_sentence=True).grounded
    # every finding fact names the facility it is about
    findings = [f for f in res.facts if f.get("kind") == "finding"]
    assert findings and all(f["facility_id"] == _FID for f in findings)
