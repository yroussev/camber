"""What the 0.112 acceptance test, round 2, found check_citations still judging wrong (#332), case
by case: a figure, zero, verdict and negation are read with their own clause, not the whole
fact. Whole-set counts ("All 11", "106 of the 106", "0 of the 11") are read against what the
fact counts; a zero or "no" does not borrow another clause's zero; a share is bound to its own
label (HW / CHW); a re-judged fact supports only its new verdict; negating frames, rank words,
causes and effects are read; and the true sentences around them stay supported. The facts are
the testers', cut down and with made-up facilities.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.capabilities import _grounded  # noqa: E402
from camber.capabilities._cap_check_citations import check_citations  # noqa: E402

FAC, OTHER = "fac-a", "fac-b"
CTX = SimpleNamespace(allowed_facilities=frozenset({FAC, OTHER}), store=None)

RUN = {
    "id": "F1@rule_run-aaaaaa",
    "kind": "rule_run",
    "facility_id": FAC,
    "value": 3,
    "text": f"3 rules were run on 1 equipment of {FAC} and 3 of them returned a result: 1 fault, "
    "1 warn, 0 info and 1 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing inputs",
}
RUN14 = {
    "id": "F1@rule_run-bbbbbb",
    "kind": "rule_run",
    "facility_id": OTHER,
    "value": 35,
    "text": f"5 rules were run on 7 equipment of {OTHER} and 5 of them returned a result: 14 "
    "fault, 0 warn, 2 info and 19 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing "
    "inputs",
}
CLEAN = {
    "id": "F1@rule_run-cccccc",
    "kind": "rule_run",
    "facility_id": FAC,
    "value": 1,
    "text": f"1 rules were run on 1 equipment of {FAC} and 1 of them returned a result: 0 fault, "
    "0 warn, 0 info and 1 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing inputs",
}
TRUST = {
    "id": "F1@sensor_trust-aaaaaa",
    "kind": "trust_summary",
    "facility_id": FAC,
    "value": 0,
    "text": f"{FAC}: 11 matching points judged (rules view): 9 trusted, 2 suspect, 0 untrusted; "
    "0 below the trust floor 0.5",
}
ALL_BAD = {
    "id": "F1@sensor_trust-dddddd",
    "kind": "trust_summary",
    "facility_id": OTHER,
    "value": 12,
    "text": f"{OTHER}: 12 points judged (rules view): 0 trusted, 0 suspect, 12 untrusted; 12 "
    "below the trust floor 0.5",
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
REF_OK = {
    "id": "R2@sensor_trust-aaaaaa",
    "kind": "reference_comparison",
    "equip": "AHU-2",
    "role": "supply_air_temp",
    "value": "ok",
    "verdict": "ok",
    "subject": {"facility_id": FAC, "equip": "AHU-2", "role": "supply_air_temp"},
    "text": '"AHU-2" "supply_air_temp" against its configured reference "handheld logger" agrees '
    "within the comparison's thresholds (passed): AHU-2 supply_air_temp: bias +0.0, drift not "
    "evaluated, RMSE 0.1, r=1.00 over 384 samples / 4 days -- tracks reference",
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
    "text": '"AHU__sat_bias" leaking_valve (fault): "AHU__sat_bias": with both valves shut, air '
    "shifts (median +3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut "
    "samples (worst: HW (heating) coil); conditional on sensor_drift:supply_air_temp warn "
    "(AHU__sat_bias supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 "
    "samples / 4 days -- biased +3.0) on AHU__sat_bias",
}
LEAK_CHW = {
    "id": "F2@rule_run-bbbbbb",
    "kind": "finding",
    "value": "fault",
    "rule": "leaking_valve",
    "equip": "AHU__coi_leakage_010",
    "facility_id": OTHER,
    "text": '"AHU__coi_leakage_010" leaking_valve (fault): "AHU__coi_leakage_010": with both '
    "valves shut, air shifts (median -2.1F beyond 2F fan heat); HW-leak 0% / CHW-leak 60% of "
    "both-valves-shut samples (worst: CHW (cooling) coil)",
}
LEAK_OK = {
    "id": "F6@rule_run-aaaaaa",
    "kind": "finding",
    "value": "ok",
    "rule": "leaking_valve",
    "equip": "AHU-2",
    "facility_id": FAC,
    "text": '"AHU-2" leaking_valve (ok): "AHU-2": no valve leak -- with both valves shut, air '
    "holds near the 2F fan-heat allowance (median +0.5F beyond it); HW-leak 0% / CHW-leak 0% of "
    "both-valves-shut samples",
}
RESET = {
    "id": "F3@rule_run-aaaaaa",
    "kind": "finding",
    "value": "warn",
    "rule": "supply_air_reset_compliance",
    "equip": "AHU-1",
    "facility_id": FAC,
    "text": '"AHU-1" supply_air_reset_compliance (warn): "AHU-1": SAT median 55.0°F vs '
    "configured target 57.1°F; below target 54% of hours (mean gap +3.1°F) -- reheat/energy "
    "opportunity; conditional on sensor_drift:supply_air_temp warn (AHU-1 supply_air_temp: bias "
    "+3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 days -- biased +3.0) on "
    "AHU-1",
}
REJUDGED = {
    "id": "R1@finding_detail-aaaaaa",
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
STANDS = {
    "id": "R1@finding_detail-bbbbbb",
    "kind": "reference_effect",
    "value": "warn",
    "verdict": "persists",
    "severity": "warn",
    "subject": {"facility_id": FAC, "equip": "AHU-1", "finding": "supply_air_reset_compliance@x"},
    "text": '"AHU-1" supply_air_reset_compliance (warn) re-judged with its input(s) corrected for '
    'the configured reference\'s bias ("supply_air_temp" by -3.0 °F): warn, "AHU-1": SAT median '
    "52.0°F vs configured target 57.1°F; below target 100% of hours (mean gap +6.1°F); so the "
    "finding stands with the offset taken off: the offset does not explain it",
}
WINDOW = {
    "id": "R2@finding_detail-aaaaaa",
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
PART = {
    "id": "R2@finding_detail-bbbbbb",
    "kind": "reference_window",
    "value": 20.0,
    "unit": "h",
    "verdict": "partly",
    "subject": {"facility_id": FAC, "equip": "AHU-1", "role": "supply_air_temp"},
    "text": '20 of the 106 flagged hours of "AHU-1" supply_air_reset_compliance (warn) fall in '
    'the window the "supply_air_temp" reference comparison on "AHU-1" covers (2025-05-19 00:00 '
    "to 2025-05-22 23:45, warn)",
}
HOURS = {
    "id": "F2@finding_detail-aaaaaa",
    "kind": "evidence_hours",
    "value": 8.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "finding": "leaking_valve@x"},
    "text": "The rule flagged 8 hours (32 of 37 judged both-valves-shut samples, 86.5%) in 3 "
    "contiguous span(s), the longest 3 hours",
}
COVER = {
    "id": "F1@coverage-aaaaaa",
    "kind": "coverage",
    "facility_id": OTHER,
    "value": 0.5,
    "unit": "%",
    "text": f"{OTHER}: 14 points on 3 equipment have a mean coverage of 0.5% of the whole window; "
    "14 point(s) are below 90% and 9 gap(s) are listed",
}
BASE_A = {
    "id": "F4@mv_summary-aaaaaa",
    "kind": "finding",
    "value": "info",
    "rule": "mv_baseline",
    "equip": "meter_A",
    "text": '"meter_A" mv_baseline (info): "meter_A": 4P baseline, R2 0.09, CV(RMSE) 33.6% over '
    "365 days -- does not meet daily acceptance (G14 criteria; the 30% daily CV(RMSE) tier is "
    "CAMBER's)",
}
BASE_B = {
    "id": "F7@mv_summary-aaaaaa",
    "kind": "finding",
    "value": "info",
    "rule": "mv_baseline",
    "equip": "meter_B",
    "text": '"meter_B" mv_baseline (info): "meter_B": 3P cooling baseline, R2 0.20, CV(RMSE) '
    "13.1% over 366 days -- does not meet daily acceptance (G14 criteria; the 30% daily "
    "CV(RMSE) tier is CAMBER's)",
}
SAVE_A = {
    "id": "F5@mv_summary-aaaaaa",
    "kind": "finding",
    "value": "info",
    "rule": "mv_savings",
    "equip": "meter_A",
    "text": '"meter_A" mv_savings (info): "meter_A": avoided 2,915 kWh (2.5%); the baseline does '
    "not meet G14 acceptance, so this saving is for information only",
}
SAVE_B = {**SAVE_A, "id": "F8@mv_summary-aaaaaa", "equip": "meter_B"}
SAVE_B["text"] = SAVE_A["text"].replace("meter_A", "meter_B")
NOTICE = {
    "id": "N1@rule_run-aaaaaa",
    "kind": "notice",
    "basis": "notice",
    "value": "notice",
    "text": "2 listed finding(s) rest on an input whose configured sensor reference disagrees "
    "with it (conditional_on names the comparison): treat each as a possible sensor problem "
    "first and check that sensor before acting on the equipment. sensor_trust shows the "
    "comparison. 1 ok result(s) below min_severity rest on such an input too and are not listed "
    "(a lower min_severity lists them); a pass measured with that input is conditional as well.",
}
PAGED = {
    "id": "N2@rule_run-aaaaaa",
    "kind": "notice",
    "basis": "notice",
    "value": "notice",
    "text": "'findings' shows rows 1-25 of 223 (worst first): call again with offset=25 for the "
    "next page, or a larger limit (up to 200)",
}
NONE_VISIBLE = {
    "id": "F1@facilities-aaaaaa",
    "kind": "facilities",
    "value": 0,
    "text": "0 facilities are visible to this caller",
}
FACTS = [
    RUN,
    RUN14,
    CLEAN,
    TRUST,
    ALL_BAD,
    REF,
    REF_OK,
    FLAT,
    LEAK,
    LEAK_CHW,
    LEAK_OK,
    RESET,
    REJUDGED,
    STANDS,
    WINDOW,
    PART,
    HOURS,
    COVER,
    BASE_A,
    BASE_B,
    SAVE_A,
    SAVE_B,
    NOTICE,
    PAGED,
    NONE_VISIBLE,
]


def _check(text: str) -> dict:
    """check_citations on ``text``, with the facts it cites."""
    cited = [f for f in FACTS if f["id"] in text] or FACTS[:1]
    return check_citations(CTX, {"text": text, "facts": cited, "detail": True}).data


def _c(*facts) -> str:
    return "[" + ", ".join(f["id"] for f in facts) + "]"


# --------------------------------------------------------------------------- majors

WRONG = [
    # 1. whole-set counts against a fact that gives a part, or none
    f"All 11 points are trusted {_c(TRUST)}.",
    f"All 11 matching points are below the trust floor 0.5 {_c(TRUST)}.",
    f"0 of the 11 points are suspect {_c(TRUST)}.",
    f"All 3 rules returned a fault {_c(RUN)}.",
    f"All 8 flagged hours of AHU__sat_bias fall in the reference window {_c(WINDOW)}.",
    f"All 106 flagged hours fall in the window the reference covers {_c(PART)}.",
    f"106 of the 106 flagged hours fall in the window the reference covers {_c(PART)}.",
    f"All 37 judged both-valves-shut samples were flagged {_c(HOURS)}.",
    f"All 14 points are trusted {_c(COVER)}.",
    # 2. a zero or "no" read against another clause's zero
    f"No faults were found on {FAC} {_c(RUN)}.",
    f"No faults were found on {OTHER} {_c(RUN14)}.",
    f"0 rules on {FAC} returned a fault {_c(RUN)}.",
    f"0 rules on {OTHER} returned a fault {_c(RUN14)}.",
    f"No rules on {FAC} returned a fault {_c(RUN)}.",
    f"None of the rules on {FAC} returned a fault {_c(RUN)}.",
    f"0 rules on {FAC} returned a warn {_c(RUN)}.",
    f"The run on {FAC} found no fault {_c(RUN)}.",
    f"0 points on {FAC} are suspect {_c(TRUST)}.",
    f"None of the 8 hours on AHU__sat_bias was flagged by leaking_valve {_c(WINDOW)}.",
    # 3. a share bound to its own label
    f"The cooling valve of AHU__sat_bias leaked on 86% of both-valves-shut samples {_c(LEAK)}.",
    f"The cooling coil of AHU__sat_bias leaked on 86% of both-valves-shut samples {_c(LEAK)}.",
    f"On AHU__sat_bias, CHW-leak was 86% of both-valves-shut samples {_c(LEAK)}.",
    f"0% of both-valves-shut samples on AHU__sat_bias showed a heating leak {_c(LEAK)}.",
    f"The heating valve of AHU__coi_leakage_010 leaked on 60% of both-valves-shut samples "
    f"{_c(LEAK_CHW)}.",
    f"AHU__coi_leakage_010 shows HW-leak 60% / CHW-leak 0% of both-valves-shut samples "
    f"{_c(LEAK_CHW)}.",
    LEAK["text"].replace("worst: HW (heating) coil", "worst: CHW (cooling) coil") + f" {_c(LEAK)}.",
    # 4. a re-judged fact's first verdict
    f"Re-judged with the bias taken off, AHU__sat_bias leaking_valve is still a fault "
    f"{_c(REJUDGED)}.",
    REJUDGED["text"].replace(": ok,", ": fault,") + f" {_c(REJUDGED)}.",
    # negating frames
    f"It is false that the supply-air sensor of AHU__sat_bias reads high against its duct logger "
    f"{_c(REF)}.",
    f"It is untrue that the heating valve of AHU__sat_bias never moves {_c(FLAT)}.",
    f"The claim that AHU__sat_bias leaking_valve is a fault is incorrect {_c(LEAK)}.",
    f"The leaking_valve fault on AHU__sat_bias is wrong {_c(LEAK)}.",
    f'The server says "{WINDOW["text"]}", but that is wrong {_c(WINDOW)}.',
    f"{WINDOW['text']} -- not {_c(WINDOW)}.",
    f"The reference measured the AHU__sat_bias supply-air sensor on the same days as the "
    f"leaking_valve evidence {_c(WINDOW)}.",
    # rank words where no cited fact ranks
    f'The "AHU-1" supply_air_reset_compliance is the top issue, ranked first {_c(RESET)}.',
    f'The "AHU-1" supply_air_reset_compliance is the second issue {_c(RESET)}.',
    f'The "AHU-1" supply_air_reset_compliance is issue #1 {_c(RESET)}.',
    f'The "AHU-1" supply_air_reset_compliance is the most severe finding {_c(RESET)}.',
    f"The AHU__sat_bias leaking_valve is the worst issue on {FAC}, ranked first {_c(LEAK)}.",
    # a cause or an effect no cited fact states (the client's inference)
    f"The AHU-1 supply_air_temp sensor shows a +3.0 bias, which accounts for the gap {_c(RESET)}.",
    f"The AHU-1 supply_air_temp sensor shows a +3.0 bias, which is about the same size as the "
    f"gap {_c(RESET)}.",
    f"Recalibrating the AHU-1 supply_air_temp sensor (bias +3.0) will clear this warn {_c(RESET)}.",
    f"The warn on AHU-1 is due to the sensor offset {_c(STANDS)}.",
]

RIGHT = [
    # whole sets the fact gives
    f"All 2,016 valid samples of the heat valve read 0 % {_c(FLAT)}.",
    f"All 12 points are untrusted, and all 12 are below the trust floor 0.5 {_c(ALL_BAD)}.",
    f"The 14 points have a mean coverage of 0.5%, and all 14 are below 90% {_c(COVER)}.",
    f"20 of the 106 flagged hours fall in the window the reference covers {_c(PART)}.",
    f"2 of the 11 points are suspect {_c(TRUST)}.",
    f"None of the 11 points is below the trust floor 0.5 {_c(TRUST)}.",
    # zeros of their own clause
    f"0 points on {FAC} are untrusted {_c(TRUST)}.",
    f"0 rules on {FAC} were skipped for missing inputs {_c(RUN)}.",
    f"No rule-equipment pair on {FAC} was skipped for missing inputs {_c(RUN)}.",
    f"0 rules on {OTHER} returned a warn {_c(RUN14)}.",
    f"No faults were found on {FAC} {_c(CLEAN)}.",
    f"None of the 8 flagged hours of AHU__sat_bias leaking_valve falls in the reference window "
    f"{_c(WINDOW)}.",
    f"0 facilities are visible {_c(NONE_VISIBLE)}.",
    # shares with their own label
    f"The heating valve of AHU__sat_bias leaked on 86% of both-valves-shut samples {_c(LEAK)}.",
    f"On AHU__sat_bias, HW-leak was 86% of both-valves-shut samples {_c(LEAK)}.",
    f"The cooling valve of AHU__coi_leakage_010 leaked on 60% of both-valves-shut samples "
    f"{_c(LEAK_CHW)}.",
    f"The CHW-leak share is 0% of both-valves-shut samples {_c(LEAK)}.",
    f"On AHU__sat_bias, CHW-leak was 0% of both-valves-shut samples {_c(LEAK)}.",
    f'leaking_valve: "AHU-2" is ok, with a median +0.5F beyond the fan-heat allowance and '
    f"HW-leak 0% / CHW-leak 0% {_c(LEAK_OK)}.",
    f"Re-judged, AHU__sat_bias shows HW-leak 0% / CHW-leak 0% of both-valves-shut samples "
    f"{_c(REJUDGED)}.",
    # a re-judged fact: its new verdict, and the finding named by its first one
    f"The leaking_valve fault on AHU__sat_bias is explained by the sensor offset {_c(REJUDGED)}.",
    f"Re-judged with the bias taken off, AHU__sat_bias leaking_valve is ok {_c(REJUDGED)}.",
    f"The offset does not explain the AHU-1 warn {_c(STANDS)}.",
    # a reading of zero is no zero count
    f'"AHU-2" "supply_air_temp" agrees within the comparison\'s thresholds (passed), with bias '
    f"+0.0 and RMSE 0.1 {_c(REF_OK)}.",
    # each baseline's caveat, said of each
    f"The electricity baselines do not meet G14 acceptance {_c(BASE_A, BASE_B)}.",
    # the paging notice with its counted thing
    f"Only the first 25 of 223 findings were listed {_c(PAGED)}.",
    # the fact's own superlative
    f"On AHU__sat_bias the worst coil is the heating (HW) coil {_c(LEAK)}.",
]


@pytest.mark.parametrize("text", WRONG)
def test_a_sentence_its_fact_does_not_say_is_not_supported(text):
    data = _check(text)
    assert not data["grounded"], text


@pytest.mark.parametrize("text", RIGHT)
def test_a_sentence_its_fact_says_is_supported(text):
    data = _check(text)
    assert data["grounded"], data["sentences"]


# --------------------------------------------------------------------------- minors


def test_a_model_name_is_not_a_count_and_a_whole_set_says_why():
    data = _check(
        f"The four electricity baselines do not meet G14 acceptance {_c(BASE_A, BASE_B)}."
    )
    why = data["sentences"][0]["why"]
    assert "4 baseline: no cited fact states this figure" in why, why
    # each cited fact says its baseline does not meet G14: "none of them meets" says no more
    each = f"None of the electricity baselines meets G14 acceptance {_c(BASE_A, BASE_B)}."
    assert _check(each)["grounded"]
    data = _check(f"None of the electricity baselines meets G14 acceptance {_c(BASE_A, RUN)}.")
    why = data["sentences"][0]["why"]
    assert "'meets'" not in why and "baselin'" not in why, why
    assert "None of the electricity baselines" in why and "say it of each" in why, why
    data = _check(f"Every electricity saving is for information only {_c(SAVE_A, SAVE_B)}.")
    assert "say it of each" in data["sentences"][0]["why"]
    each = f"The meter_A saving is for information only {_c(SAVE_A)}."
    assert _check(each)["grounded"]


@pytest.mark.parametrize(
    "text, words",
    [
        (f'The "AHU-1" warn lists no conditional_on sensor. {_c(RESET)}', "no conditional_on"),
        (f"I have not explained it. {_c(RUN)}", "not explained"),
        (f"The cooling valve leaked on 86% of both-valves-shut samples {_c(LEAK)}.", "'cool'"),
    ],
)
def test_the_why_names_the_words_as_written(text, words):
    why = _check(text)["sentences"][0]["why"]
    assert words in why and "QQ" not in why and "persists" not in why, why


def test_whole_sentences_of_a_notice_quoted_word_for_word_are_supported():
    first_two = NOTICE["text"].split(" 1 ok result(s)")[0]
    data = _check(f"{first_two} {_c(NOTICE)}")
    assert data["grounded"] and data["n_sentences"] == 1, data["sentences"]
    second = "sensor_trust shows the comparison."
    assert _check(f"{second} {_c(NOTICE)}")["grounded"]
    # a sentence with a word changed is judged as before
    changed = first_two.replace("a possible sensor problem", "a confirmed equipment problem")
    assert not _check(f"{changed} {_c(NOTICE)}")["grounded"]


def test_a_server_never_strips_its_own_zero_summary():
    text, banners = _grounded(f"0 facilities are visible {_c(NONE_VISIBLE)}.", [NONE_VISIBLE])
    assert not banners and text.startswith("0 facilities are visible"), banners


def test_the_prompts_page_the_report_and_take_a_whole_claim():
    from camber.capabilities import default_registry

    prompts = {p.name: p for p in default_registry().prompts()}

    def text(name, **args):
        return "\n".join(m["text"] for m in prompts[name].render({"facility_id": FAC, **args}))

    for name in ("plan_site_visit", "rcx_executive_summary"):
        body = text(name)
        assert "again with the offset the notice names" in body, name
        assert "keep the default top_n and limit" in body.lower(), name
    # a host's slash command may pass only the claim's first word
    cut = text("check_savings_claim", claim="The")
    assert "If that looks cut short, use the whole claim" in cut
    assert "pass it whole as claim" in text("check_savings_claim")
    # and every prompt asks for a re-check of the revised draft
    for name in prompts:
        args = {a.name: "AHU-1" for a in prompts[name].arguments if a.name != "facility_id"}
        if "finding_id" in args:
            args["finding_id"] = "leaking_valve@AHU-1"
        if "equips" in args:
            args["equips"] = "AHU-1, AHU-2"
        assert "check_citations again on the revised draft" in text(name, **args), name
