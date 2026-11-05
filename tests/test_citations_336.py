"""What the 0.112 acceptance test, round 3, found check_citations still judging wrong (#336), as
generated matrices rather than single sentences.

A counted thing a cited fact quantifies (none of 8, 20 of 106, all 14, "9 trusted, 2 suspect")
is said in every quantifier form, before and after the noun or left out (all, all of the, every
one of, each of, N of the N, N out of N, the N ... all, the N, none of, no, not a single, not
one of, zero, 0, k of the N), and only the forms that agree with the fact pass. Labels are read
as closed sets: the heating side against the cooling side (every spelling of each), trust
classes and fault conditions, and the zero of a verdict ("no rule returned a fault", "fault-free")
against the fact's count of it. Then the minor cases: a re-judged finding said to stand, the
cause a notice states, a bias taken off, a place in the text, a year, a rank word. The facts are
cut down from the testers' and use made-up facilities.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.capabilities._cap_check_citations import check_citations  # noqa: E402

FAC, OTHER = "fac-a", "fac-b"
CTX = SimpleNamespace(allowed_facilities=frozenset({FAC, OTHER}), store=None)

WINDOW = {
    "id": "R2@finding_detail-aaaaaa",
    "kind": "reference_window",
    "value": 0.0,
    "unit": "h",
    "verdict": "outside",
    "subject": {"facility_id": FAC, "equip": "AHU-1", "role": "supply_air_temp"},
    "text": 'None of the 8 flagged hours of "AHU-1" leaking_valve (fault) falls in the window the '
    '"supply_air_temp" reference comparison on "AHU-1" covers (2025-05-19 00:00 to 2025-05-22 '
    "23:45, warn): the reference measured the sensor on other days than the finding's evidence",
}
PART = {
    "id": "R2@finding_detail-bbbbbb",
    "kind": "reference_window",
    "value": 20.0,
    "unit": "h",
    "verdict": "partly",
    "subject": {"facility_id": FAC, "equip": "AHU-2", "role": "supply_air_temp"},
    "text": '20 of the 106 flagged hours of "AHU-2" supply_air_reset_compliance (warn) fall in '
    'the window the "supply_air_temp" reference comparison on "AHU-2" covers (2025-05-19 00:00 '
    "to 2025-05-22 23:45, warn)",
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
COVER24 = {
    "id": "F1@coverage-bbbbbb",
    "kind": "coverage",
    "facility_id": FAC,
    "value": 93.2,
    "unit": "%",
    "text": f"{FAC}: 24 points on 4 equipment have a mean coverage of 93.2% of the whole window; "
    "2 point(s) are below 90% and 4 gap(s) are listed",
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
    "facility_id": "fac-c",
    "value": 1,
    "text": "1 rules were run on 1 equipment of fac-c and 1 of them returned a result: 0 fault, "
    "0 warn, 0 info and 1 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing inputs",
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
    "samples (worst: HW (heating) coil)",
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
G36 = {
    "id": "F4@rule_run-bbbbbb",
    "kind": "finding",
    "value": "fault",
    "rule": "g36_afdd",
    "equip": "AHU__damper_stuck_010",
    "facility_id": OTHER,
    "text": '"AHU__damper_stuck_010" g36_afdd (fault): "AHU__damper_stuck_010": G36 §5.16.14 AFDD '
    "(8 of 15 FCs evaluated) -- FC10 OAT/MAT mismatch (economizer) 100% of applicable hours "
    "(1628 h); FC11 OAT too low for mechanical cooling 56% of applicable hours (904 h). Fan "
    "gate: fan status; ModeDelay 30 min, AlarmDelay 30 min",
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
PARAMS = {
    "id": "F4@finding_detail-aaaaaa",
    "kind": "parameters",
    "value": 8,
    "subject": {"facility_id": FAC, "equip": "AHU__sat_bias", "finding": "leaking_valve@x"},
    "text": "leaking_valve ran with fan_heat_f = 2.0, delta_thr_f = 3.0, valve_closed_thr = 5.0, "
    "measured_fan_heat_f not set, cool_delta_thr_f not set, occupied_only = False",
}
DECLINED = {
    "id": "F4@rule_run-cccccc",
    "kind": "finding",
    "value": "info",
    "rule": "leaking_valve",
    "equip": "AHU__ahu",
    "facility_id": FAC,
    "text": '"AHU__ahu" leaking_valve (info): "AHU__ahu": declined -- untrusted input(s): '
    "supply_air_temp (trust 0.27)",
}
DECLINED_N = {
    "id": "N2@rule_run-cccccc",
    "kind": "notice",
    "basis": "notice",
    "value": "notice",
    "text": "1 check(s) declined because an input scored below the trust floor 0.5 (leaking_valve "
    "on 1 equipment; untrusted input(s): supply_air_temp (trust 0.27)). The fault and warn "
    "counts do not cover them: a declined check is not a pass, and those checks were not "
    "judged. sensor_trust over the same window reports these scores and the flags behind them.",
}
FOX = {
    "id": "F1@sensor_trust-cccccc",
    "kind": "trust_summary",
    "facility_id": "fac-c",
    "value": 0,
    "text": "fac-c: 17 points judged (rules view): 17 trusted, 0 suspect, 0 untrusted; 0 below the "
    "trust floor 0.5",
}
BASELINE = {
    "id": "F2@mv_summary-aaaaaa",
    "kind": "finding",
    "value": "info",
    "rule": "mv_baseline",
    "equip": "meter_A",
    "text": '"meter_A" mv_baseline (info): "meter_A": 4P baseline, R2 0.22, CV(RMSE) 15.8% over '
    "366 days -- does not meet daily acceptance (G14 criteria; the 30% daily CV(RMSE) tier is "
    "CAMBER's): R2 0.22 < 0.75; baseline period 2016-01-01 to 2016-12-31 (the year 2016)",
}
POINT = {
    "id": "F2@sensor_trust-aaaaaa",
    "kind": "point_trust",
    "equip": "VAV2__healthy",
    "role": "airflow",
    "value": 0.6752,
    "rank": 1,
    "text": '"VAV2__healthy" "airflow": trust 0.68 (suspect; intermittent, outliers); coverage '
    "100% of its own span",
}
FACTS = [
    WINDOW,
    PART,
    COVER,
    COVER24,
    TRUST,
    TRUST92,
    RUN,
    RUN14,
    CLEAN,
    LEAK,
    LEAK_CHW,
    G36,
    REJUDGED,
    STANDS,
    PARAMS,
    DECLINED,
    DECLINED_N,
    FOX,
    BASELINE,
    POINT,
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


# --------------------------------------------------------------------------- counted things

#: each counted thing a fact quantifies: (fact, its noun phrase plural / singular, how many
#: there are, what the fact says of them (plural verb / singular verb / with "all" inside), and
#: how many of them it says it of)
THINGS = [
    (
        WINDOW,
        "flagged hours",
        "flagged hour",
        8,
        (
            "fall in the reference window",
            "falls in the reference window",
            "all fall in the reference window",
        ),
        0,
    ),
    (
        PART,
        "flagged hours",
        "flagged hour",
        106,
        (
            "fall in the reference window",
            "falls in the reference window",
            "all fall in the reference window",
        ),
        20,
    ),
    (COVER, "points", "point", 14, ("are below 90%", "is below 90%", "are all below 90%"), 14),
    (COVER24, "points", "point", 24, ("are below 90%", "is below 90%", "are all below 90%"), 2),
    (TRUST, "points", "point", 11, ("are trusted", "is trusted", "are all trusted"), 9),
    (TRUST, "points", "point", 11, ("are suspect", "is suspect", "are all suspect"), 2),
    (TRUST, "points", "point", 11, ("are untrusted", "is untrusted", "are all untrusted"), 0),
    (TRUST92, "points", "point", 92, ("are trusted", "is trusted", "are all trusted"), 89),
    (TRUST92, "points", "point", 92, ("are untrusted", "is untrusted", "are all untrusted"), 1),
    (FOX, "points", "point", 17, ("are trusted", "is trusted", "are all trusted"), 17),
]


def _forms(plural, single, n, verbs):
    """Every quantifier form of "how many of the ``n`` things" with the count it claims:
    ``(sentence without citation, count)``; the position (before the noun, after it, left
    out) and the form ("all", "N of N", "none" ...) vary, the fact and the claim stay."""
    are, is_, after = verbs
    return [
        # all of them, the quantifier before the noun
        (f"All {n} {plural} {are}", n),
        (f"All of the {n} {plural} {are}", n),
        (f"Every one of the {n} {plural} {is_}", n),
        (f"Each of the {n} {plural} {is_}", n),
        (f"{n} of the {n} {plural} {are}", n),
        (f"{n} out of {n} {plural} {are}", n),
        # all of them, the quantifier after the noun
        (f"The {n} {plural} {after}", n),
        (f"The {n} {plural}, all of them, {are}", n),
        (f"The {plural} {after}", n),
        # all of them, no quantifier
        (f"The {n} {plural} {are}", n),
        (f"The {plural} {are}", n),
        # none of them
        (f"None of the {n} {plural} {is_}", 0),
        (f"No {single} {is_}", 0),
        (f"No {plural} {are}", 0),
        (f"Not a single {single} {is_}", 0),
        (f"Not one of the {n} {plural} {is_}", 0),
        (f"Zero {plural} {are}", 0),
        (f"0 of the {n} {plural} {are}", 0),
    ]


def _matrix():
    out = []
    for fact, plural, single, n, verbs, true in THINGS:
        for text, says in _forms(plural, single, n, verbs):
            out.append((f"{text} {_c(fact)}.", says == true))
        # a part, the true one and others the fact gives for something else
        for k in sorted({true, 2, 9, 20} - {0, n}):
            out.append((f"{k} of the {n} {plural} {verbs[0]} {_c(fact)}.", k == true))
            out.append((f"{k} out of {n} {plural} {verbs[0]} {_c(fact)}.", k == true))
    return out


MATRIX = _matrix()


@pytest.mark.parametrize("text, agrees", MATRIX)
def test_a_counted_thing_passes_only_with_the_quantifier_its_fact_gives(text, agrees):
    data = _check(text)
    assert data["grounded"] is agrees, (text, data["sentences"])


def test_the_matrix_covers_every_form_both_ways():
    assert len(MATRIX) >= 180
    assert sum(1 for _t, ok in MATRIX if ok) >= 40
    assert sum(1 for _t, ok in MATRIX if not ok) >= 120


#: fresh wordings of the same claims, beyond the matrix: other quantifiers ("some",
#: "several", "most", "at least one"), synonyms of the fact's verb, a lead-in ("Of the 11
#: points, all are ..."), "nothing", "neither ... nor", sides and fault conditions said in a list
FRESH = [
    (False, f"Each flagged hour of AHU-1 falls in the reference window {_c(WINDOW)}."),
    (False, f"Every flagged hour of AHU-1 falls in the reference window {_c(WINDOW)}."),
    (False, f"All flagged hours of AHU-1 fall in the reference window {_c(WINDOW)}."),
    (False, f"The flagged hours lie in the reference window {_c(WINDOW)}."),
    (False, f"The flagged hours of AHU-1 are inside the reference window {_c(WINDOW)}."),
    (False, f"The flagged hours of AHU-1 are within the reference window {_c(WINDOW)}."),
    (False, f"Some of the 8 flagged hours fall in the reference window {_c(WINDOW)}."),
    (False, f"Several flagged hours fall in the reference window {_c(WINDOW)}."),
    (True, f"Some of the 106 flagged hours fall in the reference window {_c(PART)}."),
    (False, f"Most of the 106 flagged hours fall in the reference window {_c(PART)}."),
    (False, f"At least one of the 8 flagged hours falls in the reference window {_c(WINDOW)}."),
    (True, f"Only 20 of the 106 flagged hours fall in the reference window {_c(PART)}."),
    (True, f"The 8 flagged hours fall outside the reference window {_c(WINDOW)}."),
    (False, f"Nine points are suspect {_c(TRUST)}."),
    (True, f"Nine points are trusted {_c(TRUST)}."),
    (True, f"Two points are suspect and none is untrusted {_c(TRUST)}."),
    (False, f"Two points are trusted and none is suspect {_c(TRUST)}."),
    (False, f"Both points that are suspect on fac-a are trusted {_c(TRUST)}."),
    (False, f"fac-b has zero faults {_c(RUN14)}."),
    (False, f"No equipment on fac-b returned a fault {_c(RUN14)}."),
    (False, f"Nothing failed on fac-b {_c(RUN14)}."),
    (False, f"None of the rules failed on fac-b {_c(RUN14)}."),
    (False, f"Not a single rule failed on fac-b {_c(RUN14)}."),
    (False, f"The run on fac-b found no warns and no faults {_c(RUN14)}."),
    (True, f"The run on fac-b found no warns {_c(RUN14)}."),
    (True, f"The run on fac-c found no faults and no warns {_c(CLEAN)}."),
    (False, f"fac-a had no failures {_c(RUN)}."),
    (
        False,
        f"The heating-coil valve of AHU__coi_leakage_010 leaked on 60% of both-valves-shut "
        f"samples {_c(LEAK_CHW)}.",
    ),
    (
        False,
        f"On AHU__sat_bias the hydronic heating side was 0% of both-valves-shut samples "
        f"{_c(LEAK)}.",
    ),
    (
        True,
        f"On AHU__sat_bias, heating valve leakage was 86% and cooling 0% of both-valves-shut "
        f"samples {_c(LEAK)}.",
    ),
    (
        False,
        f"On AHU__sat_bias, heating valve leakage was 0% and cooling 86% of both-valves-shut "
        f"samples {_c(LEAK)}.",
    ),
    (
        True,
        f"On AHU__sat_bias, 86% (heating) and 0% (cooling) of both-valves-shut samples showed a "
        f"leak {_c(LEAK)}.",
    ),
    (
        False,
        f"On AHU__sat_bias, 0% (heating) and 86% (cooling) of both-valves-shut samples showed a "
        f"leak {_c(LEAK)}.",
    ),
    (False, f"FC11 on AHU__damper_stuck_010 ran for 1628 h {_c(G36)}."),
    (
        False,
        f"The leaking_valve fault on AHU__sat_bias still applies after the re-judge "
        f"{_c(REJUDGED)}.",
    ),
    (False, f"The leaking_valve fault on AHU__sat_bias is upheld {_c(REJUDGED)}."),
    (
        True,
        f"The leaking_valve finding on AHU__sat_bias is explained by the sensor offset "
        f"{_c(REJUDGED)}.",
    ),
    (False, f"0 of the 8 flagged hours fall outside the reference window {_c(WINDOW)}."),
    (False, f"20 of the 106 flagged hours fall outside the reference window {_c(PART)}."),
    (False, f"None of the 8 flagged hours falls outside the reference window {_c(WINDOW)}."),
    (False, f"The 11 points on fac-a are each trusted {_c(TRUST)}."),
    (False, f"Every point on fac-a is trusted {_c(TRUST)}."),
    (False, f"All points on fac-a are trusted {_c(TRUST)}."),
    (False, f"Of the 11 points on fac-a, all are trusted {_c(TRUST)}."),
    (False, f"Of the 11 points on fac-a, none is suspect {_c(TRUST)}."),
    (True, f"Of the 11 points on fac-a, none is untrusted {_c(TRUST)}."),
    (False, f"Of the 92 points, 1 is suspect {_c(TRUST92)}."),
    (True, f"Of the 92 points, 1 is untrusted {_c(TRUST92)}."),
    (False, f"There is 1 suspect point on fac-b {_c(TRUST92)}."),
    (True, f"There are 2 suspect points on fac-b {_c(TRUST92)}."),
    (False, f"Of the 8 flagged hours, all fall in the reference window {_c(WINDOW)}."),
    (True, f"Of the 8 flagged hours, none falls in the reference window {_c(WINDOW)}."),
    (False, f"The reference window covers all 8 flagged hours {_c(WINDOW)}."),
    (False, f"Every one of the 92 points is trusted {_c(TRUST92)}."),
    (False, f"No point on fac-b is untrusted {_c(TRUST92)}."),
    (False, f"fac-b has no untrusted points {_c(TRUST92)}."),
    (False, f"There are no suspect points on fac-a {_c(TRUST)}."),
    (True, f"fac-a has no untrusted points {_c(TRUST)}."),
    (True, f"The 2 suspect points on fac-a are suspect {_c(TRUST)}."),
    (True, f"The cooling coil of AHU__sat_bias did not leak (0%) {_c(LEAK)}."),
    (
        True,
        f"AHU__sat_bias leaked on 86% of both-valves-shut samples on the heating side {_c(LEAK)}.",
    ),
    (
        False,
        f"The CHW valve, not the HW valve, leaked on 86% of both-valves-shut samples {_c(LEAK)}.",
    ),
    (True, f"On AHU__damper_stuck_010, FC10 was 100% and FC11 56% of applicable hours {_c(G36)}."),
    (False, f"On AHU__damper_stuck_010, FC11 was 100% and FC10 56% of applicable hours {_c(G36)}."),
    (True, f"There were no faults or warns on fac-c {_c(CLEAN)}."),
    (True, f"fac-b reported no warns {_c(RUN14)}."),
    (False, f"fac-b reported no warns or faults {_c(RUN14)}."),
    (False, f"fac-b reported neither faults nor warns {_c(RUN14)}."),
    (
        True,
        f"14 faults, 0 warns, 2 info and 19 ok results came from the 5 rules on fac-b {_c(RUN14)}.",
    ),
    (
        True,
        f"Of the 11 points on fac-a, 9 are trusted, 2 are suspect and 0 are untrusted {_c(TRUST)}.",
    ),
    (
        False,
        f"Of the 11 points on fac-a, 2 are trusted, 9 are suspect and 0 are untrusted {_c(TRUST)}.",
    ),
    (True, f"The 5 rules on fac-b returned 14 faults {_c(RUN14)}."),
    (False, f"There are 2 trusted points on fac-b {_c(TRUST92)}."),
]


@pytest.mark.parametrize("right, text", FRESH)
def test_fresh_wordings_of_a_counted_or_labelled_claim(right, text):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


# --------------------------------------------------------------------------- labels

HEATING = ["heating", "heat", "hot-water", "hot water", "HW", "HHW", "reheat", "preheat", "steam"]
COOLING = ["cooling", "cool", "chilled-water", "chilled water", "CHW", "DX"]
SIDES = [(s, "heat") for s in HEATING] + [(s, "cool") for s in COOLING]
SHARES = [
    (LEAK, "AHU__sat_bias", {"heat": 86, "cool": 0}),
    (LEAK_CHW, "AHU__coi_leakage_010", {"heat": 0, "cool": 60}),
]


def _side_matrix():
    out = []
    for fact, eq, by_side in SHARES:
        for word, side in SIDES:
            for value in sorted(set(by_side.values())):
                right = by_side[side] == value
                for text in (
                    f"The {word} valve of {eq} leaked on {value}% of both-valves-shut samples",
                    f"On {eq}, the {word} coil leaked on {value}% of both-valves-shut samples",
                    f"{value}% of both-valves-shut samples on {eq} showed a {word} leak",
                ):
                    out.append((f"{text} {_c(fact)}.", right))
    return out


@pytest.mark.parametrize("text, right", _side_matrix())
def test_a_share_is_bound_to_its_side_by_every_spelling(text, right):
    assert _supported(text) is right, _check(text)["sentences"]


@pytest.mark.parametrize(
    "text",
    [
        f"The glycol valve of AHU__coi_leakage_010 leaked on 60% of both-valves-shut samples "
        f"{_c(LEAK_CHW)}.",
        f"A valve of AHU__coi_leakage_010 leaked on 60% of both-valves-shut samples "
        f"{_c(LEAK_CHW)}.",
    ],
)
def test_a_share_whose_side_is_not_said_or_not_known_fails_closed(text):
    data = _check(text)
    assert not data["grounded"]
    assert (
        "heating or cooling" in data["sentences"][0]["why"]
        or "not as" in (data["sentences"][0]["why"])
    ), data["sentences"]


def _trust_matrix():
    out = []
    counts = {
        TRUST["id"]: (TRUST, {"trusted": 9, "suspect": 2, "untrusted": 0}),
        TRUST92["id"]: (TRUST92, {"trusted": 89, "suspect": 2, "untrusted": 1}),
    }
    for fact, by_label in counts.values():
        for label in by_label:
            for k in sorted(set(by_label.values()) - {0}):
                verb = "is" if k == 1 else "are"
                noun = "point" if k == 1 else "points"
                out.append((f"{k} {noun} {verb} {label} {_c(fact)}.", by_label[label] == k))
            if by_label[label] == 1 or 1 in by_label.values():
                out.append((f"One point is {label} {_c(fact)}.", by_label[label] == 1))
    return out


@pytest.mark.parametrize("text, right", _trust_matrix())
def test_a_count_is_bound_to_its_trust_class(text, right):
    assert _supported(text) is right, _check(text)["sentences"]


@pytest.mark.parametrize(
    "code, value, right",
    [
        ("FC10", 100, True),
        ("FC10", 56, False),
        ("FC11", 56, True),
        ("FC11", 100, False),
        ("FC12", 100, False),
        ("FC12", 56, False),
    ],
)
def test_a_share_is_bound_to_its_fault_condition(code, value, right):
    for text in (
        f"On AHU__damper_stuck_010, {code} was flagged {value}% of applicable hours {_c(G36)}.",
        f"{code} on AHU__damper_stuck_010 was {value}% of applicable hours {_c(G36)}.",
    ):
        assert _supported(text) is right, (text, _check(text)["sentences"])


def _verdict_zero_matrix():
    out = []
    for fact, fac, counts in (
        (RUN, FAC, {"fault": 1, "warn": 1, "info": 0, "ok": 1}),
        (RUN14, OTHER, {"fault": 14, "warn": 0, "info": 2, "ok": 19}),
        (CLEAN, "fac-c", {"fault": 0, "warn": 0, "info": 0, "ok": 1}),
    ):
        for label in ("fault", "warn"):
            none = counts[label] == 0
            for text in (
                f"No rule returned a {label} on {fac}",
                f"Not a single {label} was found on {fac}",
                f"{fac} is {label}-free",
                f"{fac} is free of {label}s",
            ):
                out.append((f"{text} {_c(fact)}.", none))
        both = counts["fault"] == 0 and counts["warn"] == 0
        out.append((f"No rule returned a fault or a warn on {fac} {_c(fact)}.", both))
        out.append((f"No rule returned a warn or a fault on {fac} {_c(fact)}.", both))
        out.append((f"No rule passed on {fac} {_c(fact)}.", counts["ok"] == 0))
    return out


@pytest.mark.parametrize("text, none", _verdict_zero_matrix())
def test_a_zero_of_a_verdict_never_borrows_another_clause_zero(text, none):
    data = _check(text)
    assert data["grounded"] is none, (text, data["sentences"])


def test_both_suspect_points_are_not_trusted():
    data = _check(f"Both suspect points on {FAC} are trusted {_c(TRUST)}.")
    assert not data["grounded"]
    assert "one trust class each" in data["sentences"][0]["why"], data["sentences"]


def test_the_whys_name_the_count_the_fact_gives():
    why = _check(f"The 11 points on {FAC} are trusted {_c(TRUST)}.")["sentences"][0]["why"]
    assert "not as 'trusted'" in why, why
    why = _check(f"No rule passed on {OTHER} {_c(RUN14)}.")["sentences"][0]["why"]
    assert "counts 19 ok" in why, why
    text = (
        f'The 8 flagged hours of "AHU-1" leaking_valve all fall in the reference window '
        f"{_c(WINDOW)}."
    )
    why = _check(text)["sentences"][0]["why"]
    assert "None of the 8 flagged hours" in why, why
    why = _check(f"1 point on {OTHER} is suspect {_c(TRUST92)}.")["sentences"][0]["why"]
    assert "'untrusted', not as 'suspect'" in why, why
    why = _check(f"FC10 on AHU__damper_stuck_010 was 56% of applicable hours {_c(G36)}.")
    assert "'FC11', not as 'FC10'" in why["sentences"][0]["why"], why


def test_a_setting_the_fact_says_is_not_set_is_named_whole():
    assert not _supported(f"No fan heat was set for leaking_valve {_c(PARAMS)}.")
    assert _supported(f"No measured_fan_heat_f was set for leaking_valve {_c(PARAMS)}.")
    assert _supported(f"The measured fan heat was not set for leaking_valve {_c(PARAMS)}.")


# --------------------------------------------------------------------------- minors


@pytest.mark.parametrize("verb", ["stands", "remains valid", "still holds", "is still valid"])
def test_a_finding_re_judged_ok_does_not_stand(verb):
    text = f"The leaking_valve fault on AHU__sat_bias {verb} after the re-judge {_c(REJUDGED)}."
    assert not _supported(text)
    # the one re-judged as a warn does
    assert _supported(f"The supply_air_reset_compliance warn on AHU-1 {verb} {_c(STANDS)}.")
    # air that holds near an allowance is no verdict
    assert _supported(
        f"With both valves shut, air holds near the 2F fan-heat allowance {_c(REJUDGED)}."
    )


def test_a_cause_the_cited_notice_states_is_supported():
    n2, f4 = _c(DECLINED_N), _c(DECLINED, DECLINED_N)
    for text in (
        f"The leaking_valve check declined because of its untrusted supply_air_temp input {f4}.",
        f"Because of the untrusted supply_air_temp, the leaking_valve check declined {f4}.",
        f"Because of that sensor, the leaking_valve check declined {f4}.",
        f"The leaking_valve check declined due to an untrusted input {n2}.",
    ):
        assert _supported(text), _check(text)["sentences"]
    # another cause, or no fact that states one
    for text in (
        f"The leaking_valve check declined because of a stuck valve {f4}.",
        f"The leaking_valve check declined because of its untrusted input {_c(DECLINED)}.",
        f"The leaking_valve check passed because of its untrusted input {f4}.",
    ):
        data = _check(text)
        assert not data["grounded"], text


def test_a_bias_taken_off_is_the_correction_with_its_sign_changed():
    r1 = _c(REJUDGED)
    assert _supported(
        f"Re-judged with the +3.0 °F bias taken off, AHU__sat_bias leaking_valve is ok {r1}."
    )
    assert _supported(
        f"Re-judged with the sensor corrected by -3.0 °F, AHU__sat_bias leaking_valve is ok {r1}."
    )
    data = _check(
        f"Re-judged with the sensor corrected by +3.0 °F, AHU__sat_bias leaking_valve is ok {r1}."
    )
    why = data["sentences"][0]["why"]
    assert not data["grounded"] and "other sign" in why and "threshold" not in why, why
    # a bias said as -3.0 is not this sensor's (+3.0), taken off or not
    data = _check(
        f"Re-judged with that sensor's bias of -3.0 °F taken off, AHU__sat_bias leaking_valve is "
        f"ok {r1}."
    )
    assert not data["grounded"]
    assert "sign changed" in data["sentences"][0]["why"], data["sentences"]
    # a bias of -3.0 taken off is a correction by +3.0: not this fact's
    assert not _supported(
        f"Re-judged with the -3.0 °F bias taken off, AHU__sat_bias leaking_valve is ok {r1}."
    )


def test_a_place_in_the_text_a_year_and_a_rank_word_are_read_as_such():
    body = FOX["text"]
    assert _supported(
        f"Sensor trust (its coverage can differ from the figures above): {body} {_c(FOX)}"
    )
    assert _supported(f"See below: {body} {_c(FOX)}")
    b = _c(BASELINE)
    assert _supported(f"The 2016 baseline does not meet daily acceptance: R2 0.22 < 0.75 {b}.")
    assert not _supported(f"The 2017 baseline does not meet daily acceptance: R2 0.22 < 0.75 {b}.")
    assert not _supported(f"The 2016 baselines do not meet daily acceptance {b}.")
    p = _c(POINT)
    assert _supported(
        f'The least trusted point is "VAV2__healthy" "airflow", trust 0.68 (suspect) {p}.'
    )
    data = _check(f'The most trusted point is "VAV2__healthy" "airflow", trust 0.68 {p}.')
    assert not data["grounded"] and "ranks it so" in data["sentences"][0]["why"]
    ranked_second = {**POINT, "rank": 2}
    data = check_citations(
        CTX,
        {
            "text": f'The least trusted point is "VAV2__healthy" "airflow" {p}.',
            "facts": [ranked_second],
            "detail": True,
        },
    ).data
    assert not data["grounded"]


def test_the_prompts_ask_to_check_the_whole_revised_draft():
    from camber.capabilities import default_registry

    for prompt in default_registry().prompts():
        args = {a.name: "AHU-1" for a in prompt.arguments}
        args["facility_id"] = FAC
        if "finding_id" in args:
            args["finding_id"] = "leaking_valve@AHU-1"
        if "equips" in args:
            args["equips"] = "AHU-1, AHU-2"
        body = "\n".join(m["text"] for m in prompt.render(args))
        assert "the whole of it every time" in body, prompt.name
        assert "not only the ones you changed" in body, prompt.name


def test_point_trust_facts_carry_their_place_in_the_list():
    import inspect

    from camber.capabilities import _cap_sensor_trust

    src = inspect.getsource(_cap_sensor_trust)
    assert '"rank": (arguments.get("offset") or 0) + i - 1' in src
