"""What the 0.112 acceptance test, round 4, found check_citations still judging wrong (#338), as
generated families rather than single sentences.

Six families, each said in many wordings and both ways (the agreeing form passes, the
disagreeing one fails): "both / each / every / all" said of several items (equipment, points),
where each item needs a cited fact stating the claim of it; a verdict given to a whole set with
no quantifier ("The 5 rules passed"); zeros said after a colon, in a "not" passive, "there
aren't any", "X free", "free of" and "neither ... nor"; a whole count said with the fact's own
verb, a pronoun or a table cell; the existential "there are (N) X in Y"; and trust-class counts
said with "one", "a single" or an elliptical second count. Then the minors: "the majority of",
"over N", "half", and the false strips. The facts are cut down from the testers' and use
made-up facilities.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.capabilities._cap_check_citations import check_citations  # noqa: E402

FAC, OTHER, CLEANFAC = "fac-a", "fac-b", "fac-c"
CTX = SimpleNamespace(allowed_facilities=frozenset({FAC, OTHER, CLEANFAC}), store=None)

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
    "facility_id": CLEANFAC,
    "value": 2,
    "text": f"2 rules were run on 1 equipment of {CLEANFAC} and 2 of them returned a result: 0 "
    "fault, 0 warn, 0 info and 2 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing "
    "inputs",
}
TRUST = {
    "id": "F1@sensor_trust-aaaaaa",
    "kind": "trust_summary",
    "facility_id": FAC,
    "value": 0,
    "text": f"{FAC}: 11 matching points judged (rules view): 9 trusted, 2 suspect, 0 untrusted; "
    "0 below the trust floor 0.5",
}
TRUST92 = {
    "id": "F1@sensor_trust-bbbbbb",
    "kind": "trust_summary",
    "facility_id": OTHER,
    "value": 1,
    "text": f"{OTHER}: 92 points judged (rules view): 89 trusted, 2 suspect, 1 untrusted; 1 "
    "below the trust floor 0.5",
}
FOX = {
    "id": "F1@sensor_trust-cccccc",
    "kind": "trust_summary",
    "facility_id": CLEANFAC,
    "value": 0,
    "text": f"{CLEANFAC}: 17 points judged (rules view): 17 trusted, 0 suspect, 0 untrusted; 0 "
    "below the trust floor 0.5",
}
SAMPLES = {
    "id": "F2@finding_detail-aaaaaa",
    "kind": "evidence_hours",
    "value": 8.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "finding": "leaking_valve@x"},
    "text": "The rule flagged 8 hours (32 of 37 judged both-valves-shut samples, 86.5%) in 3 "
    "contiguous span(s), the longest 3 hours",
}
CONDITION = {
    "id": "F3@finding_detail-aaaaaa",
    "kind": "condition_hours",
    "value": 8.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "condition": "heating leak"},
    "text": "heating leak: the rule flagged 8 hours (32 of 37 judged both-valves-shut samples, "
    "86.5%)",
}
RESET = {
    "id": "F2@finding_detail-bbbbbb",
    "kind": "evidence_hours",
    "value": 106.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU-1", "finding": "supply_air_reset_compliance@x"},
    "text": "The rule flagged 106 hours (424 of 780 judged occupied fan-on samples, 54.4%) in 16 "
    "contiguous span(s), the longest 13 hours",
}
FREECOOL = {
    "id": "F8@rule_run-bbbbbb",
    "kind": "finding",
    "value": "fault",
    "rule": "free_cooling_missed",
    "equip": "AHU__damper_stuck_075",
    "facility_id": OTHER,
    "text": '"AHU__damper_stuck_075" free_cooling_missed (fault): "AHU__damper_stuck_075": '
    "mechanical cooling (valve > 5%) ran 42% of the 1672 free-cooling hours (OAT < 60°F); <1% "
    "more on ~100% outside air (integrated economizer, not counted); 1105 h below the 33.8°F "
    "low-limit lockout not judged",
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
    "00:00 to 2025-05-22 23:45, warn): the reference measured the sensor on other days than the "
    "finding's evidence",
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
# one fact per item: AHU-1 leaks, AHU-2 does not; AHU-3 leaks like AHU-1
LEAK1 = {
    "id": "F2@rule_run-dddddd",
    "kind": "finding",
    "value": "fault",
    "rule": "leaking_valve",
    "equip": "AHU-1",
    "facility_id": FAC,
    "text": '"AHU-1" leaking_valve (fault): "AHU-1": with both valves shut, air shifts (median '
    "+3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples (worst: HW "
    "(heating) coil)",
}
LEAK2 = {
    "id": "F6@rule_run-dddddd",
    "kind": "finding",
    "value": "ok",
    "rule": "leaking_valve",
    "equip": "AHU-2",
    "facility_id": FAC,
    "text": '"AHU-2" leaking_valve (ok): "AHU-2": no valve leak -- with both valves shut, air '
    "holds near the 2F fan-heat allowance (median +0.5F beyond it); HW-leak 0% / CHW-leak 0% of "
    "both-valves-shut samples",
}
LEAK3 = {
    "id": "F7@rule_run-dddddd",
    "kind": "finding",
    "value": "fault",
    "rule": "leaking_valve",
    "equip": "AHU-3",
    "facility_id": FAC,
    "text": '"AHU-3" leaking_valve (fault): "AHU-3": with both valves shut, air shifts (median '
    "+3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples (worst: HW "
    "(heating) coil)",
}
REF1 = {
    "id": "R1@sensor_trust-dddddd",
    "kind": "reference_comparison",
    "equip": "AHU-1",
    "role": "supply_air_temp",
    "value": "warn",
    "verdict": "warn",
    "subject": {"facility_id": FAC, "equip": "AHU-1", "role": "supply_air_temp"},
    "text": '"AHU-1" "supply_air_temp" against its configured reference "handheld reference '
    'logger" disagrees (warn): the point is suspect: bias +3.0, RMSE 3.0 over 384 samples',
}
REF2 = {
    "id": "R2@sensor_trust-dddddd",
    "kind": "reference_comparison",
    "equip": "AHU-2",
    "role": "supply_air_temp",
    "value": "ok",
    "verdict": "ok",
    "subject": {"facility_id": FAC, "equip": "AHU-2", "role": "supply_air_temp"},
    "text": '"AHU-2" "supply_air_temp" against its configured reference "handheld reference '
    "logger\" agrees within the comparison's thresholds (passed): bias +0.0, RMSE 0.1 over 384 "
    "samples -- tracks reference: reads neither high nor low",
}
REF3 = {
    **REF1,
    "id": "R3@sensor_trust-dddddd",
    "equip": "AHU-3",
    "subject": {"facility_id": FAC, "equip": "AHU-3", "role": "supply_air_temp"},
    "text": REF1["text"].replace("AHU-1", "AHU-3"),
}
SPR = {
    "id": "F2@sensor_trust-eeeeee",
    "kind": "point_trust",
    "equip": "AHU__healthy",
    "role": "static_pressure_requests",
    "value": 0.7664,
    "rank": 1,
    "text": '"AHU__healthy" "static_pressure_requests": trust 0.77 (suspect; outliers); coverage '
    "100% of its own span",
}
TRUST27 = {
    "id": "F1@sensor_trust-eeeeee",
    "kind": "trust_summary",
    "facility_id": OTHER,
    "value": 0,
    "text": f"{OTHER}: 27 matching points judged (rules view): 26 trusted, 1 suspect, 0 "
    "untrusted; 0 below the trust floor 0.5",
}
# the minors' facts
INPUTS = {
    "id": "W1@aaaaaaaaaaaa-aaaaaa",
    "kind": "confidence",
    "value": "M",
    "equip": "AHU__not_responding",
    "text": "Confidence M: Input trust H: sat_reset_requests trusted (0.97), supply_air_temp_sp "
    "trusted (1.00).",
}
INPUTS_H = {
    "id": "W1@bbbbbbbbbbbb-aaaaaa",
    "kind": "confidence",
    "value": "H",
    "equip": "AHU-2",
    "text": "Confidence H: Input trust H: supply_air_temp trusted (1.00).",
}
CONDITIONAL = {
    "id": "C1@rule_run-aaaaaa",
    "kind": "conditional",
    "value": 1,
    "facility_id": FAC,
    "text": "1 result(s) not listed here also rest on an input whose configured sensor reference "
    'disagrees with it, so each is conditional on that sensor (a pass too): "AHU-1" '
    "supply_air_control (ok)",
}
CLAIMS = [
    {
        "id": f"A{i}@mv_summary-aaaaaa",
        "kind": "claimed_figure",
        "basis": "claimed",
        "value": v,
        "unit": u,
        "subject": {"facility_id": FAC},
        "text": f"The claim under check states {t}: the claim's own figure, not a measured or "
        "computed result",
    }
    for i, v, u, t in ((1, 2017.0, None, "2017"), (2, 12.0, "%", "12%"), (3, 2016.0, None, "2016"))
]
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
FACTS = [
    RUN,
    RUN14,
    CLEAN,
    TRUST,
    TRUST92,
    FOX,
    SAMPLES,
    CONDITION,
    RESET,
    FREECOOL,
    WINDOW,
    PART,
    LEAK1,
    LEAK2,
    LEAK3,
    REF1,
    REF2,
    REF3,
    SPR,
    TRUST27,
    INPUTS,
    INPUTS_H,
    CONDITIONAL,
    *CLAIMS,
    REJUDGED,
]


def _check(text: str) -> dict:
    """check_citations on ``text``, with the facts it cites."""
    cited = [f for f in FACTS if f["id"] in text] or FACTS[:1]
    return check_citations(CTX, {"text": text, "facts": cited, "detail": True}).data


def _c(*facts) -> str:
    return "[" + ", ".join(f["id"] for f in facts) + "]"


def _supported(text: str) -> bool:
    data = _check(text)
    return data["grounded"] and data["n_sentences"] == 1


def _params(cases):
    return [
        pytest.param(text, right, id=f"{'ok' if right else 'no'}-{i}")
        for i, (text, right) in enumerate(cases)
    ]


# --------------------------------------------------------------------------- 1. items


#: what is said of each item, and the facts that state it of each (and of the item it is false
#: for): AHU-1 and AHU-3 leak, AHU-2 does not; AHU-1's and AHU-3's sensors disagree, AHU-2's
#: agrees
CLAIMS_OF_ITEMS = [
    ("fail leaking_valve (fault)", "fails leaking_valve (fault)", (LEAK1, LEAK3), LEAK2),
    (
        "have a leaking heating valve: HW-leak 86%",
        "has a leaking heating valve: HW-leak 86%",
        (LEAK1, LEAK3),
        LEAK2,
    ),
    ("disagree with their reference", "disagrees with its reference", (REF1, REF3), REF2),
]


def _item_forms(plural_verb, single_verb, noun="AHU", nouns="AHUs"):
    """Every way to say a claim of each of a set of items, with the count it says (None: the
    set's size is not said, and the set is read as the items the cited facts are about)."""
    return [
        (f"Both {nouns} {plural_verb}", 2),
        (f"The {nouns} both {plural_verb}", 2),
        (f"Both of the {nouns} {plural_verb}", 2),
        (f"Each of the 2 {nouns} {single_verb}", 2),
        (f"All 2 {nouns} {plural_verb}", 2),
        (f"The 2 {nouns} {plural_verb}", 2),
        (f"Each {noun} {single_verb}", None),
        (f"Every {noun} {single_verb}", None),
        (f"Each of the {nouns} {single_verb}", None),
        (f"The {nouns} all {plural_verb}", None),
        (f"The {nouns} each {single_verb.replace('has ', 'have ')}", None),
        (f"All {nouns} {plural_verb}", None),
        (f"The {nouns} {plural_verb}", None),
    ]


def _item_matrix():
    out = []
    for plural_verb, single_verb, (a, b), wrong in CLAIMS_OF_ITEMS:
        for text, _k in _item_forms(plural_verb, single_verb):
            # the facts state it of two items, each: the set is those two
            out.append((f"{text} {_c(a, b)}.", True))
            out.append((f"{text} {_c(a, b, LEAK1 if a is not LEAK1 else REF1)}.", True))
            # one fact: one item, never "both"
            out.append((f"{text} {_c(a)}.", False))
            # one of the cited facts says otherwise of its item
            out.append((f"{text} {_c(a, wrong)}.", False))
            out.append((f"{text} {_c(a, b, wrong)}.", False))
    # a quantifier after a verb, and "units" / sensors / points
    for facts, right in (((LEAK1, LEAK3), True), ((LEAK1,), False), ((LEAK1, LEAK2), False)):
        out.append((f"The leaking_valve fault is on both AHUs {_c(*facts)}.", right))
        out.append((f"Both units fail leaking_valve (fault) {_c(*facts)}.", right))
    for facts, right in (((REF1, REF3), True), ((REF1,), False), ((REF1, REF2), False)):
        out.append(
            (f'Both "supply_air_temp" points disagree with their reference {_c(*facts)}.', right)
        )
        out.append((f"Both supply air sensors disagree with their reference {_c(*facts)}.", right))
    return out


ITEMS = _item_matrix()


@pytest.mark.parametrize("text, right", _params(ITEMS))
def test_each_item_of_a_set_needs_a_cited_fact_that_states_it(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


def test_the_item_matrix_covers_both_ways():
    assert len(ITEMS) >= 150
    assert sum(1 for _t, ok in ITEMS if ok) >= 20
    assert sum(1 for _t, ok in ITEMS if not ok) >= 120


@pytest.mark.parametrize(
    "text, right",
    _params(
        [
            # a set a cited fact counts is a counted thing; a fact's own "both valves" its words
            (f"Both suspect points on {FAC} are suspect {_c(TRUST)}.", True),
            (f"With both valves shut, air shifts on AHU-1 {_c(LEAK1)}.", True),
            (
                f'Each AHU has a suspect "static_pressure_requests" point (trust 0.77) {_c(SPR)}.',
                False,
            ),
            (f'Every AHU has a suspect "static_pressure_requests" point {_c(SPR)}.', False),
            (
                f'The AHUs each have a suspect "static_pressure_requests" point '
                f"{_c(SPR, TRUST27)}.",
                False,
            ),
            (f'All three AHUs have a suspect "static_pressure_requests" point {_c(SPR)}.', False),
            (f"AHU-1 and AHU-3 both fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.", True),
        ]
    ),
)
def test_items_counted_named_or_said_by_the_fact(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


@pytest.mark.parametrize(
    "text, right",
    _params(
        [
            # names joined as one subject: each is said the same of
            (f"AHU-1 and AHU-2 both fail leaking_valve (fault) {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 and AHU-2 fail leaking_valve (fault) {_c(LEAK1, LEAK2)}.", False),
            (
                f"AHU-1, AHU-2 and AHU-3 fail leaking_valve (fault) {_c(LEAK1, LEAK2, LEAK3)}.",
                False,
            ),
            (f"Both AHU-1 and AHU-3 fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.", True),
            (f"AHU-1, AHU-3 fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.", True),
            (f"AHU-1 fails leaking_valve and AHU-2 passes it {_c(LEAK1, LEAK2)}.", True),
            # other words for the items and the set
            (f"Both air handlers fail leaking_valve (fault) {_c(LEAK1)}.", False),
            (f"Both air handlers fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.", True),
            (f"Both AHUs pass leaking_valve {_c(LEAK2, LEAK1)}.", False),
            (f"{OTHER}'s equipment is healthy {_c(RUN14)}.", False),
            (f"{CLEANFAC}'s rules passed {_c(CLEAN)}.", True),
            (f"All the rules on {OTHER} passed {_c(RUN14)}.", False),
            (f"Every rule on {OTHER} passed {_c(RUN14)}.", False),
            # a labelled count before the one that names its thing
            (f"On {OTHER}, 1 suspect and 1 untrusted point were found {_c(TRUST92)}.", False),
            (f"On {OTHER}, 2 suspect and 1 untrusted point were found {_c(TRUST92)}.", True),
            (
                f"On {OTHER}, 89 trusted, 2 suspect and 1 untrusted points were found "
                f"{_c(TRUST92)}.",
                True,
            ),
            # zeros said otherwise
            (f"Faults on {OTHER} — 0 {_c(RUN14)}.", False),
            (f"Faults detected on {OTHER}: zero {_c(RUN14)}.", False),
            (f"| Faults | 0 | {_c(RUN14)}", False),
            (f"| Warns | 0 | {_c(RUN14)}", True),
            (f"Faults are absent on {OTHER} {_c(RUN14)}.", False),
            (f"{OTHER} is free from faults {_c(RUN14)}.", False),
            (f"{FAC} is clear of suspect points {_c(TRUST)}.", False),
            (f"{FAC} is clear of untrusted points {_c(TRUST)}.", True),
            # a whole said after the verb, with a quantifier
            (f"The whole of the 37 judged samples was flagged {_c(SAMPLES)}.", False),
            (f"The rule flagged all judged both-valves-shut samples {_c(SAMPLES)}.", False),
            (f"The rule flagged the 37 judged both-valves-shut samples {_c(SAMPLES)}.", False),
            (f"The rule flagged 32 of the 37 judged both-valves-shut samples {_c(SAMPLES)}.", True),
            (
                f"Mechanical cooling ran in every free-cooling hour on AHU__damper_stuck_075 "
                f"{_c(FREECOOL)}.",
                False,
            ),
            (f"All are trusted {_c(TRUST)}.", False),
            (f"Each is trusted {_c(TRUST)}.", False),
            (f"All are trusted {_c(FOX)}.", True),
        ]
    ),
)
def test_fresh_wordings_of_sets_and_counts(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


def test_the_why_names_the_item_the_claim_fails_for():
    why = _check(f"Both AHUs fail leaking_valve (fault) {_c(LEAK1, LEAK2)}.")["sentences"][0]["why"]
    assert "'ahu-2'" in why.lower(), why
    why = _check(f"Both AHUs fail leaking_valve (fault) {_c(LEAK1)}.")["sentences"][0]["why"]
    assert "1 of them" in why, why
    why = _check(f"Each AHU fails leaking_valve (fault) {_c(LEAK1)}.")["sentences"][0]["why"]
    assert "each one" in why, why
    why = _check(f"All 3 AHUs fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.")["sentences"][0]
    assert "2 of them" in why["why"], why
    # cited facts about the facility only: its verdict counts
    why = _check(f"Both AHUs fail leaking_valve (fault) {_c(RUN)}.")["sentences"][0]["why"]
    assert "1 ok" in why and "1 warn" in why, why


# --------------------------------------------------------------------------- 2. whole sets


#: a fact's verdict counts, and what each set is
VERDICT_SETS = [
    (RUN, FAC, {"fault": 1, "warn": 1, "info": 0, "ok": 1}, (3, "rules"), (1, "equipment")),
    (RUN14, OTHER, {"fault": 14, "warn": 0, "info": 2, "ok": 19}, (5, "rules"), (7, "equipment")),
    (CLEAN, CLEANFAC, {"fault": 0, "warn": 0, "info": 0, "ok": 2}, (2, "rules"), (1, "equipment")),
]
VERDICT_PREDICATES = [
    ("ok", "passed", "passed"),
    ("ok", "returned ok", "returned ok"),
    ("ok", "are healthy", "is healthy"),
    ("fault", "returned a fault", "returned a fault"),
    ("fault", "failed", "failed"),
]


def _whole_set_matrix():
    out = []
    for fact, fac, counts, *sets in VERDICT_SETS:
        others = {v for v, n in counts.items() if n}
        for label, plural, single in VERDICT_PREDICATES:
            right = others == {label}
            for n, noun in sets:
                verb = single if n == 1 else plural
                for text in (
                    f"The {n} {noun} on {fac} {verb}",
                    f"{n} {noun} on {fac} {verb}",
                    f"The {noun} on {fac} all {plural}" if n > 1 else f"The {noun} on {fac} {verb}",
                ):
                    out.append((f"{text} {_c(fact)}.", right))
                if not right:  # "all 3" is a whole count: its own check, which fails closed
                    out.append((f"All {n} {noun} on {fac} {verb} {_c(fact)}.", False))
    return out


WHOLE = _whole_set_matrix()


@pytest.mark.parametrize("text, right", _params(WHOLE))
def test_a_verdict_given_to_a_whole_set_agrees_with_every_verdict_count(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


def test_the_whole_set_matrix_covers_both_ways():
    assert sum(1 for _t, ok in WHOLE if ok) >= 10
    assert sum(1 for _t, ok in WHOLE if not ok) >= 80


@pytest.mark.parametrize(
    "text, right",
    _params(
        [
            (f"The 5 rules on {OTHER} returned 14 faults {_c(RUN14)}.", True),
            (f"The 3 rules on {FAC} returned a fault, a warn and an ok {_c(RUN)}.", True),
            (f"All 3 rules on {FAC} returned a result {_c(RUN)}.", True),
            (f"The 2 rules on {CLEANFAC} returned a result {_c(CLEAN)}.", True),
        ]
    ),
)
def test_a_whole_set_with_a_figure_or_no_verdict_is_read_as_before(text, right):
    assert _supported(text) is right, _check(text)["sentences"]


# --------------------------------------------------------------------------- 3. zeros


def _zero_matrix():
    out = []
    for fact, fac, counts in (
        (RUN, FAC, {"fault": 1, "warn": 1}),
        (RUN14, OTHER, {"fault": 14, "warn": 0}),
        (CLEAN, CLEANFAC, {"fault": 0, "warn": 0}),
    ):
        for label, n in counts.items():
            for text in (
                f"{label.capitalize()}s on {fac}: 0",
                f"{label.capitalize()}s found on {fac}: 0",
                f"Number of {label}s on {fac}: 0",
                f"- {label.capitalize()}s on {fac}: 0",
                f"{label.capitalize()}s were not found on {fac}",
                f"{label.capitalize()}s weren't found on {fac}",
                f"There aren't any {label}s on {fac}",
                f"There are not any {label}s on {fac}",
                f"{fac} is {label} free",
                f"{fac} is free of {label}s",
            ):
                out.append((f"{text} {_c(fact)}.", n == 0))
        both = counts["fault"] == 0 and counts["warn"] == 0
        out.append((f"Neither faults nor warns were found on {fac} {_c(fact)}.", both))
    for fact, fac, counts in (
        (TRUST, FAC, {"suspect": 2, "untrusted": 0}),
        (TRUST92, OTHER, {"suspect": 2, "untrusted": 1}),
        (FOX, CLEANFAC, {"suspect": 0, "untrusted": 0}),
    ):
        for label, n in counts.items():
            for text in (
                f"{label.capitalize()} points on {fac}: 0",
                f"Number of {label} points on {fac}: 0",
                f"{fac} is {label}-free",
                f"{fac} is {label} free",
                f"{fac} is free of {label} points",
                f"{label.capitalize()} points were not found on {fac}",
                f"There aren't any {label} points on {fac}",
            ):
                out.append((f"{text} {_c(fact)}.", n == 0))
        both = counts["suspect"] == 0 and counts["untrusted"] == 0
        out.append((f"Neither suspect nor untrusted points were found on {fac} {_c(fact)}.", both))
        out.append((f"Neither untrusted nor suspect points were found on {fac} {_c(fact)}.", both))
    return out


ZEROS = _zero_matrix()


@pytest.mark.parametrize("text, none", _params(ZEROS))
def test_a_zero_in_every_form_agrees_with_the_count(text, none):
    data = _check(text)
    assert data["grounded"] is none, (text, data["sentences"])


def test_the_zero_matrix_covers_both_ways():
    assert sum(1 for _t, ok in ZEROS if ok) >= 40
    assert sum(1 for _t, ok in ZEROS if not ok) >= 50


def test_counted_figures_after_a_colon_agree_with_the_count():
    assert _supported(f"Faults on {OTHER}: 14 {_c(RUN14)}.")
    assert not _supported(f"Faults on {OTHER}: 19 {_c(RUN14)}.")
    assert _supported(f"Trusted points on {FAC}: 9 {_c(TRUST)}.")
    assert not _supported(f"Suspect points on {FAC}: 9 {_c(TRUST)}.")
    assert _supported(f"Points judged on {FAC}: 11 {_c(TRUST)}.")
    assert not _supported(f"Points judged on {FAC}: 12 {_c(TRUST)}.")


# --------------------------------------------------------------------------- 4. whole counts


#: a fact's "k of N" (or a share of N) with its own verb, and how to say the N things
PARTS = [
    (SAMPLES, "judged both-valves-shut samples", 37, "were flagged", 32),
    (CONDITION, "judged both-valves-shut samples", 37, "were flagged as a heating leak", 32),
    (RESET, "judged occupied fan-on samples", 780, "were flagged", 424),
]


def _whole_count_matrix():
    out = []
    for fact, noun, n, verb, k in PARTS:
        c = _c(fact)
        for text in (
            f"The {n} {noun} {verb}",
            f"{n} {noun} {verb}",
            f"The {noun} {verb}",
            f"The {noun} {verb.replace('were ', 'were all ')}",
            f"All {noun} {verb}",
            f"All {n} {noun} {verb}",
            f"Each of the {noun} {verb.replace('were ', 'was ')}",
            f"Of the {n} {noun}, all {verb}",
            f"None of the {n} {noun} {verb.replace('were ', 'was ')}",
        ):
            out.append((f"{text} {c}.", False))
        for text in (
            f"{k} of the {n} {noun} {verb}",
            f"{k} of {n} {noun} {verb}",
            f"Of the {n} {noun}, {k} {verb}",
            f"Most of the {n} {noun} {verb}",
            f"Some of the {n} {noun} {verb}",
        ):
            out.append((f"{text} {c}.", True))
    c = _c(FREECOOL)
    for text, right in (
        ("The 1672 free-cooling hours on AHU__damper_stuck_075 ran mechanical cooling", False),
        ("All 1672 free-cooling hours on AHU__damper_stuck_075 ran mechanical cooling", False),
        (
            "On AHU__damper_stuck_075 mechanical cooling ran 42% of the 1672 free-cooling hours",
            True,
        ),
    ):
        out.append((f"{text} {c}.", right))
    return out


WHOLE_COUNTS = _whole_count_matrix()


@pytest.mark.parametrize("text, right", _params(WHOLE_COUNTS))
def test_a_whole_count_with_the_fact_own_verb_agrees_with_its_part(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


@pytest.mark.parametrize(
    "text, right",
    _params(
        [
            (f"{FAC} has 11 points {_c(TRUST)}. They are all trusted {_c(TRUST)}.", False),
            (f"{FAC} has 11 points {_c(TRUST)}. They are all suspect {_c(TRUST)}.", False),
            (f"{CLEANFAC} has 17 points {_c(FOX)}. They are all trusted {_c(FOX)}.", True),
            (f"| {FAC} | 11 points | all trusted | {_c(TRUST)}", False),
            (f"| {CLEANFAC} | 17 points | all trusted | {_c(FOX)}", True),
            (f"| {FAC} | 3 rules | all ok | {_c(RUN)}", False),
            (f"| {CLEANFAC} | 2 rules | all ok | {_c(CLEAN)}", True),
            (f"On {FAC}, all of them are trusted {_c(TRUST)}.", False),
        ]
    ),
)
def test_all_said_with_a_pronoun_or_in_a_table_cell(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


# --------------------------------------------------------------------------- 5. existential


def _existential_matrix():
    out = []
    for fact, n, k in ((WINDOW, 8, 0), (PART, 106, 20)):
        c = _c(fact)
        place = "in the reference window"
        for text, says in (
            (f"There were {n} flagged hours {place}", n),
            (f"There are {n} flagged hours {place}", n),
            (f"There are flagged hours {place}", "some"),
            (f"There were flagged hours {place}", "some"),
            (f"In the reference window there are {n} flagged hours", n),
            ("In the reference window there were flagged hours", "some"),
            (f"There were no flagged hours {place}", 0),
            (f"There are no flagged hours {place}", 0),
            (f"There were {k} flagged hours {place}", k),
            (f"There were some flagged hours {place}", "some"),
        ):
            right = (k > 0) if says == "some" else says == k
            out.append((f"{text} {c}.", right))
    return out


EXISTENTIAL = _existential_matrix()


@pytest.mark.parametrize("text, right", _params(EXISTENTIAL))
def test_there_are_agrees_with_the_fact_count(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


# --------------------------------------------------------------------------- 6. trust classes


def _trust_class_matrix():
    out = []
    for fact, fac, counts in (
        (TRUST, FAC, {"trusted": 9, "suspect": 2, "untrusted": 0}),
        (TRUST92, OTHER, {"trusted": 89, "suspect": 2, "untrusted": 1}),
    ):
        c = _c(fact)
        for label, n in counts.items():
            for text in (
                f"One point on {fac} is {label}",
                f"A single point on {fac} is {label}",
                f"Only a single point on {fac} is {label}",
                f"Just one point on {fac} is {label}",
                f"Exactly one point on {fac} is {label}",
            ):
                out.append((f"{text} {c}.", n == 1))
            for other, m in counts.items():
                if other == label:
                    continue
                for text in (
                    f"1 point on {fac} is {label} and 1 is {other}",
                    f"One point on {fac} is {label} and one is {other}",
                    f"On {fac}, 1 point is {label}, and 1 is {other}",
                ):
                    out.append((f"{text} {c}.", n == 1 and m == 1))
        # below the trust floor is not a trust class: its count is its own
        below = 0 if fact is TRUST else 1
        for label, n in counts.items():
            out.append(
                (
                    f"1 point on {fac} is below the trust floor and 1 is {label} {c}.",
                    below == 1 and n == 1,
                )
            )
    return out


TRUST_CLASSES = _trust_class_matrix()


@pytest.mark.parametrize("text, right", _params(TRUST_CLASSES))
def test_a_trust_class_count_in_every_form(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


# --------------------------------------------------------------------------- minors


@pytest.mark.parametrize(
    "text, right",
    _params(
        [
            # "the majority of", "the bulk of" are "most of"
            (
                f"The majority of the 8 flagged hours fall in the reference window {_c(WINDOW)}.",
                False,
            ),
            (
                f"The majority of the 106 flagged hours fall in the reference window {_c(PART)}.",
                False,
            ),
            (f"The bulk of the 106 flagged hours fall in the reference window {_c(PART)}.", False),
            (f"A majority of the 11 points are trusted {_c(TRUST)}.", False),
            # "over N" is more than N: the fact gives exactly N
            (f"Over 9 of the 11 points on {FAC} are trusted {_c(TRUST)}.", False),
            (f"Upwards of 9 of the 11 points on {FAC} are trusted {_c(TRUST)}.", False),
            (f"Over 9 points on {FAC} are trusted {_c(TRUST)}.", False),
            (f"Over 14 faults were found on {OTHER} {_c(RUN14)}.", False),
            (f"In excess of 14 faults were found on {OTHER} {_c(RUN14)}.", False),
            (f"The rule flagged 8 hours over 3 contiguous spans {_c(SAMPLES)}.", True),
            # "half" is a share of the count
            (f"At least half of the 11 points on {FAC} are trusted {_c(TRUST)}.", True),
            (f"More than half of the 11 points on {FAC} are trusted {_c(TRUST)}.", True),
            (f"Fewer than half of the 11 points on {FAC} are trusted {_c(TRUST)}.", False),
            (f"Half of the 11 points on {FAC} are trusted {_c(TRUST)}.", False),
            (
                f"Fewer than half of the 106 flagged hours fall in the reference window "
                f"{_c(PART)}.",
                True,
            ),
            (
                f"More than half of the 106 flagged hours fall in the reference window {_c(PART)}.",
                False,
            ),
            (
                f"At least half of the 8 flagged hours fall in the reference window {_c(WINDOW)}.",
                False,
            ),
        ]
    ),
)
def test_majority_over_and_half(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


@pytest.mark.parametrize(
    "text",
    [
        f"86.5% of the judged both-valves-shut samples on AHU__sat_bias were flagged "
        f"{_c(SAMPLES)}.",
        f"The rule flagged 86.5% of the judged both-valves-shut samples on AHU__sat_bias "
        f"{_c(SAMPLES)}.",
        f'Input trust is high for this finding {_c(INPUTS_H)}, and "AHU-2" "supply_air_temp" '
        f"agrees with its reference with bias +0.0 {_c(REF2)}.",
        f"Re-judged with the -3.0 °F correction, the finding is ok, with 0% heating and 0% cooling "
        f"leak of both-valves-shut samples {_c(REJUDGED)}.",
        f'On "AHU__not_responding" the reset-request input was trusted at 0.97 and the '
        f"supply-air temperature setpoint at 1.00 {_c(INPUTS)}.",
        f'One ok result, "supply_air_control", also rests on the disputed sensor and is '
        f"conditional {_c(CONDITIONAL)}.",
        f"The claim names 2017 {_c(CLAIMS[0])}, 12% {_c(CLAIMS[1])} and 2016 {_c(CLAIMS[2])}.",
        f"The claim as stated: it names 2017 {_c(CLAIMS[0])}, 12% {_c(CLAIMS[1])} and 2016 "
        f"{_c(CLAIMS[2])}.",
        f"- **The 12% figure:** It is the claim's own number, not a measured or computed result "
        f"{_c(CLAIMS[1])}.",
    ],
)
def test_true_sentences_that_were_stripped(text):
    data = _check(text)
    assert data["grounded"], (text, data["sentences"])


@pytest.mark.parametrize(
    "text",
    [
        # the same wordings, false
        f"87.5% of the judged both-valves-shut samples on AHU__sat_bias were flagged "
        f"{_c(SAMPLES)}.",
        f'"AHU-2" "supply_air_temp" reads high against its reference {_c(REF2)}.',
        f'Input trust is high for this finding, and "AHU-2" "supply_air_temp" reads high '
        f"{_c(INPUTS_H, REF2)}.",
        f"Re-judged, the finding is ok, with 86% heating and 0% cooling leak of both-valves-shut "
        f"samples {_c(REJUDGED)}.",
        f"Re-judged, the finding is ok and the valve leaks {_c(REJUDGED)}.",
        f'On "AHU__not_responding" the reset-request input was trusted at 0.97 and the '
        f"supply-air temperature setpoint at 0.98 {_c(INPUTS)}.",
        f'Two ok results, "supply_air_control", also rest on the disputed sensor '
        f"{_c(CONDITIONAL)}.",
        f"The retrofit saved 12% in 2017 {_c(CLAIMS[1], CLAIMS[0])}.",
        f"The claim names 2017 {_c(CLAIMS[0])}, 15% {_c(CLAIMS[1])} and 2016 {_c(CLAIMS[2])}.",
    ],
)
def test_the_same_wordings_false_still_fail(text):
    data = _check(text)
    assert not data["grounded"], (text, data["sentences"])


# --------------------------------------------------------------------------- the check's word


def test_the_result_says_only_the_text_checked_is_checked():
    for text in (f"The rule flagged 8 hours {_c(SAMPLES)}.", f"It flagged 9 hours {_c(SAMPLES)}."):
        res = check_citations(CTX, {"text": text, "facts": [SAMPLES]})
        assert "covers only the text passed" in res.text, res.text
        assert "check the whole text again" in res.text, res.text


def test_the_prompts_say_a_reworded_answer_is_not_checked():
    from camber.capabilities import default_registry

    for prompt in default_registry().prompts():
        args = {a.name: "AHU-1" for a in prompt.arguments}
        args["facility_id"] = FAC
        if "finding_id" in args:
            args["finding_id"] = "leaking_valve@AHU-1"
        if "equips" in args:
            args["equips"] = "AHU-1, AHU-2"
        body = "\n".join(m["text"] for m in prompt.render(args))
        assert "reword" in body, prompt.name
