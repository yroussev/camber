"""What the 0.112 acceptance test, round 5, found check_citations still judging wrong (#339), as
generated families rather than single sentences.

The set-of-items rule is one rule now: a sentence that says something of two or more items (by
a quantifier, a number, names joined in any position -- a colon lead-in, a list item, a table
cell, after the verb --, a negated set, a possessive "each X's" or "the pair of") needs, for
every item, a cited fact that states that same claim of it: its figures, verdicts and
negations. Each family is said in many wordings and both ways (the false form fails, its true
twin passes): per-item figures; negated sets; joined names outside plain prose; bare trust-class
and verdict labels after a colon or in a table cell; whole-set verdicts; and direction words
that follow the sign of a saving. Then the minors and the papercut. The facts are cut down from
the testers' and use made-up facilities and equipment.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.capabilities._cap_check_citations import check_citations  # noqa: E402

FAC, OTHER, CLEANFAC, FAULTFAC = "fac-a", "fac-b", "fac-c", "fac-d"
CTX = SimpleNamespace(allowed_facilities=frozenset({FAC, OTHER, CLEANFAC, FAULTFAC}), store=None)


def _run(fid, rules, equip, fault, warn, info, ok, tag):
    n = fault + warn + info + ok
    return {
        "id": f"F1@rule_run-{tag}",
        "kind": "rule_run",
        "facility_id": fid,
        "value": n,
        "counts": {"fault": fault, "warn": warn, "info": info, "ok": ok},
        "text": f"{rules} rules were run on {equip} equipment of {fid} and {rules} of them "
        f"returned a result, {n} rule-equipment result(s) in all: {fault} fault, {warn} warn, "
        f"{info} info and {ok} ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing "
        "inputs",
    }


RUN1 = _run(FAC, 3, 1, 1, 1, 0, 1, "aaaaaa")  # one equipment: a result is a rule's
RUN2 = _run(FAC, 3, 2, 1, 2, 0, 3, "bbbbbb")  # two: six results of three rules
RUN14 = _run(OTHER, 5, 7, 14, 0, 2, 19, "cccccc")
CLEAN = _run(CLEANFAC, 2, 1, 0, 0, 0, 2, "dddddd")
FAULTY = _run(FAULTFAC, 2, 1, 2, 0, 0, 0, "eeeeee")
TRUST = {
    "id": "F1@sensor_trust-aaaaaa",
    "kind": "trust_summary",
    "facility_id": FAC,
    "value": 0,
    "text": f"{FAC}: 11 matching points judged (rules view): 9 trusted, 2 suspect, 0 untrusted; "
    "0 below the trust floor 0.5",
}


def _leak(n, ok, tag):
    if ok:
        body = (
            "no valve leak -- with both valves shut, air holds near the 2F fan-heat allowance "
            "(median +0.5F beyond it); HW-leak 0% / CHW-leak 0% of both-valves-shut samples"
        )
    else:
        body = (
            "with both valves shut, air shifts (median +3.4F beyond 2F fan heat); HW-leak 86% / "
            "CHW-leak 0% of both-valves-shut samples (worst: HW (heating) coil)"
        )
    v = "ok" if ok else "fault"
    return {
        "id": f"F{n}@rule_run-{tag}",
        "kind": "finding",
        "value": v,
        "rule": "leaking_valve",
        "equip": f"AHU-{n}",
        "facility_id": FAC,
        "text": f'"AHU-{n}" leaking_valve ({v}): "AHU-{n}": {body}',
    }


LEAK1, LEAK2, LEAK3, LEAK4 = (_leak(n, n in (2, 4), "ffffff") for n in (1, 2, 3, 4))


def _ctrl(n):
    return {
        "id": f"F{n + 10}@rule_run-ffffff",
        "kind": "finding",
        "value": "ok",
        "rule": "supply_air_control",
        "equip": f"AHU-{n}",
        "facility_id": FAC,
        "text": f'"AHU-{n}" supply_air_control (ok): "AHU-{n}": SAT off setpoint 0% of occupied '
        "running hours (mean |Δ| 0.1°F; warm 0%, cold 0%)",
    }


CTRL1, CTRL2 = _ctrl(1), _ctrl(2)


def _oa(n, trust):
    return {
        "id": f"F{n + 1}@sensor_trust-aaaaaa",
        "kind": "point_trust",
        "equip": f"AHU-{n}",
        "role": "oa_damper",
        "value": trust,
        "text": f'"AHU-{n}" "oa_damper": trust {trust:.2f} (suspect; bimodal, outliers); coverage '
        "100% of its own span",
    }


OA1, OA2, OA3 = _oa(1, 0.66), _oa(2, 0.68), _oa(3, 0.66)


def _flat(n, role, reads, tag):
    return {
        "id": f"L{n}@sensor_trust-{tag}",
        "kind": "flat_point",
        "equip": f"AHU-{n}",
        "role": role,
        "verdict": "flat",
        "subject": {"facility_id": FAC, "equip": f"AHU-{n}", "role": role},
        "text": f'"AHU-{n}" "{role}" never moves over the window: all 2,016 valid samples from '
        f"2025-05-12 00:00 to 2025-06-01 23:45 read {reads}, so it is flat for the whole window",
    }


FLAT1 = _flat(1, "heat_valve", "0 %", "aaaaaa")
FLAT2 = _flat(2, "heat_valve", "0 %", "bbbbbb")
SP1 = _flat(1, "supply_air_temp_sp", "55 °F", "cccccc")


def _cov(equip, pct, n):
    return {
        "id": f"F{n}@coverage-aaaaaa",
        "kind": "equip_coverage",
        "equip": equip,
        "facility_id": FAC,
        "value": pct,
        "text": f'"{equip}": mean coverage {pct:.1f}% of the window over 11 points; worst '
        f'"supply_air_temp" at {pct:.1f}%',
    }


COV_A1, COV_A2, COV_L1 = _cov("AHU-1", 100.0, 2), _cov("AHU-2", 100.0, 3), _cov("LOGGER-1", 19.1, 4)


def _ref(n, bias, ok):
    verdict = "ok" if ok else "warn"
    said = (
        "agrees within the comparison's thresholds (passed)"
        if ok
        else "disagrees (warn): the point is suspect"
    )
    return {
        "id": f"R{n}@sensor_trust-aaaaaa",
        "kind": "reference_comparison",
        "equip": f"AHU-{n}",
        "role": "supply_air_temp",
        "value": verdict,
        "verdict": verdict,
        "subject": {"facility_id": FAC, "equip": f"AHU-{n}", "role": "supply_air_temp"},
        "text": f'"AHU-{n}" "supply_air_temp" against its configured reference "handheld '
        f'reference logger" {said}: bias {bias:+.1f}, RMSE 0.1 over 384 samples',
    }


REF1, REF2, REF3 = _ref(1, 3.0, False), _ref(2, 0.0, True), _ref(3, 3.0, False)


def _saving(meter, energy, pct, n):
    return {
        "id": f"F{n}@mv_summary-aaaaaa",
        "kind": "finding",
        "value": "info",
        "rule": "mv_savings",
        "equip": meter,
        "facility_id": OTHER,
        "text": f'"{meter}" mv_savings (info): "{meter}": avoided energy {energy} ({pct}) ± 4,971 '
        "at 90% over 365 reporting days; the baseline does not meet G14 acceptance, so this "
        "saving is for information only",
    }


SAV_A = _saving("Meter-A", "2,915", "2.5%", 2)
SAV_B = _saving("Meter-B", "-293,693", "-15.6%", 3)
SAV_C = _saving("Meter-C", "2,915", "2.5%", 4)


def _baseline(meter, meets, n):
    said = "meets daily acceptance" if meets else "does not meet daily acceptance"
    v = "ok" if meets else "info"
    return {
        "id": f"F{n}@mv_summary-bbbbbb",
        "kind": "finding",
        "value": v,
        "rule": "mv_baseline",
        "equip": meter,
        "facility_id": OTHER,
        "text": f'"{meter}" mv_baseline ({v}): "{meter}": 4P baseline, R2 0.81, CV(RMSE) 25.2% '
        f"over 345 days -- {said} (G14 criteria)",
    }


BASE_A, BASE_B, BASE_C, BASE_D = (
    _baseline("Meter-A", False, 2),
    _baseline("Meter-B", False, 3),
    _baseline("Meter-C", True, 4),
    _baseline("Meter-D", True, 5),
)
SAMPLES0 = {
    "id": "F2@finding_detail-aaaaaa",
    "kind": "evidence_hours",
    "value": 0.0,
    "unit": "h",
    "subject": {"facility_id": FAC, "equip": "AHU-2", "finding": "leaking_valve@AHU-2"},
    "text": "The rule flagged 0 hours (0 of 59 judged both-valves-shut samples, 0.0%)",
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
FACILITY = {
    "id": "F2@facilities-aaaaaa",
    "kind": "facility",
    "facility_id": FAC,
    "value": 24,
    "text": f'{FAC} ("{FAC}"): active, 24 points on 4 equipment, data from 2025-05-12T00:00:00 '
    "to 2025-06-01T23:45:00",
}
FACTS = [
    RUN1,
    RUN2,
    RUN14,
    CLEAN,
    FAULTY,
    TRUST,
    LEAK1,
    LEAK2,
    LEAK3,
    LEAK4,
    CTRL1,
    CTRL2,
    OA1,
    OA2,
    OA3,
    FLAT1,
    FLAT2,
    SP1,
    COV_A1,
    COV_A2,
    COV_L1,
    REF1,
    REF2,
    REF3,
    SAV_A,
    SAV_B,
    SAV_C,
    BASE_A,
    BASE_B,
    BASE_C,
    BASE_D,
    SAMPLES0,
    PART,
    FACILITY,
]
_BY_ID = {f["id"]: f for f in FACTS}
assert len(_BY_ID) == len(FACTS)


def _check(text: str) -> dict:
    """check_citations on ``text``, with the facts it cites (in the order cited)."""
    import re

    ids = list(dict.fromkeys(re.findall(r"[A-Z]+\d+@[a-z_]+-[a-z]{6}", text)))
    cited = [_BY_ID[i] for i in ids if i in _BY_ID] or FACTS[:1]
    return check_citations(CTX, {"text": text, "facts": cited, "detail": True}).data


def _c(*facts) -> str:
    return "[" + ", ".join(f["id"] for f in facts) + "]"


def _params(cases):
    return [
        pytest.param(text, right, id=f"{'ok' if right else 'no'}-{i}")
        for i, (text, right) in enumerate(cases)
    ]


def _assert(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])


def _both_ways(cases):
    assert sum(1 for _t, ok in cases if ok) >= 5, "each family needs true twins"
    assert sum(1 for _t, ok in cases if not ok) >= 5, "each family needs false forms"


# --------------------------------------------------------------------------- 1. per-item figures

#: a figure said of each of two items: (plural claim, singular claim, names, facts where it is
#: true of both, facts where one item's fact gives another figure)
FIGURES = [
    (
        "have a trust of 0.66",
        "has a trust of 0.66",
        ("AHU-1", "AHU-3"),
        (OA1, OA3),
        (OA1, OA2),
        "oa_damper point",
    ),
    (
        "have a mean coverage of 100.0% of the window",
        "has a mean coverage of 100.0% of the window",
        ("AHU-1", "AHU-2"),
        (COV_A1, COV_A2),
        (COV_A1, COV_L1),
        "unit",
    ),
    ("save 2.5%", "saves 2.5%", ("Meter-A", "Meter-C"), (SAV_A, SAV_C), (SAV_A, SAV_B), "meter"),
]


def _figure_matrix():
    out = []
    for plural, single, (a, b), true, false, noun in FIGURES:
        nouns = noun + "s"
        forms = [
            f"Both {nouns} {plural}",
            f"The two {nouns} {plural}",
            f"The 2 {nouns} {plural}",
            f"All 2 {nouns} {plural}",
            f"Each {noun} {single}",
            f"Each of the {nouns} {single}",
            f"The {nouns} each {plural}",
            f'"{a}" and "{b}" both {plural}',
            f'"{a}" and "{b}" {plural}',
            f'Both "{a}" and "{b}" {plural}',
        ]
        for text in forms:
            named = f'"{a}"' in text
            out.append((f"{text} {_c(*true)}.", True))
            out.append((f"{text} {_c(*false)}.", False))
            # a cited fact about a third item: the named set is still its two names
            out.append((f"{text} {_c(*false, *true)}.", named))
    # a figure the sentence gives one named item, or a list of figures one for each item
    out += [
        (
            f"The oa_damper is suspect on both units, at 0.66 on AHU-1 and 0.68 on AHU-2 "
            f"{_c(OA1, OA2)}.",
            True,
        ),
        (
            f"The oa_damper is suspect on both units, at 0.68 on AHU-1 and 0.66 on AHU-2 "
            f"{_c(OA1, OA2)}.",
            False,
        ),
        (f'"AHU-1" and "AHU-2" have a trust of 0.66 and 0.68 respectively {_c(OA1, OA2)}.', True),
        (f'"AHU-1" and "AHU-2" have a trust of 0.66 and 0.66 respectively {_c(OA1, OA2)}.', False),
        (f"Both oa_damper points are suspect (0.66 and 0.68) {_c(OA1, OA2)}.", True),
        (f"Both oa_damper points are suspect (0.66 and 0.66) {_c(OA1, OA2)}.", False),
        (f"On both, the trust is 0.66 {_c(OA1, OA3)}.", True),
        (f"On both, the trust is 0.66 {_c(OA1, OA2)}.", False),
        (f'Each AHU\'s "supply_air_temp" reads 3.0 high {_c(REF1, REF3)}.', True),
        (f'Each AHU\'s "supply_air_temp" reads 3.0 high {_c(REF1, REF2)}.', False),
        (f'Both "heat_valve" points read 0 % {_c(FLAT1, FLAT2)}.', True),
        (f"Both points read 0 % {_c(FLAT1, SP1)}.", False),
        # a facility named in the sentence is no item's: the figure is still each item's
        (f"Both units on {FAC} have a mean coverage of 100.0% {_c(COV_A1, COV_A2)}.", True),
        (f"Both units on {FAC} have a mean coverage of 100.0% {_c(COV_A1, COV_L1)}.", False),
    ]
    return out


FIGURE_CASES = _figure_matrix()


@pytest.mark.parametrize("text, right", _params(FIGURE_CASES))
def test_each_item_of_a_set_needs_its_own_figure(text, right):
    _assert(text, right)


def test_the_figure_matrix_covers_both_ways():
    _both_ways(FIGURE_CASES)
    assert len(FIGURE_CASES) >= 100


# --------------------------------------------------------------------------- 2. negated sets

#: what a negated set says of each item: (predicate, facts where it is true of both items,
#: facts where one item's fact says otherwise, the two items' names)
NEGATED = [
    ("fails leaking_valve", (LEAK2, LEAK4), (LEAK1, LEAK2), ("AHU-2", "AHU-4"), ("AHU-1", "AHU-2")),
    ("has a valve leak", (LEAK2, LEAK4), (LEAK1, LEAK2), ("AHU-2", "AHU-4"), ("AHU-1", "AHU-2")),
    (
        "fails supply_air_control",
        (CTRL1, CTRL2),
        (LEAK1, CTRL2),
        ("AHU-1", "AHU-2"),
        ("AHU-1", "AHU-2"),
    ),
]


def _negated_matrix():
    out = []
    for pred, true, false, (ta, tb), (fa, fb) in NEGATED:
        nouns = [
            "Neither AHU",
            "Neither of the AHUs",
            "Neither of the two AHUs",
            "No AHU",
            "None of the AHUs",
            "Not one of the AHUs",
        ]
        for lead in nouns:
            out.append((f"{lead} {pred} {_c(*true)}.", True))
            out.append((f"{lead} {pred} {_c(*false)}.", False))
            out.append((f"{lead} {pred} {_c(true[0])}.", False))  # one item is not a set
        out.append((f"Neither {ta} nor {tb} {pred} {_c(*true)}.", True))
        out.append((f"Neither {fa} nor {fb} {pred} {_c(*false)}.", False))
        out.append((f'Neither "{ta}" nor "{tb}" {pred} {_c(*true)}.', True))
        out.append((f'Neither "{fa}" nor "{fb}" {pred} {_c(*false)}.', False))
    # "A has no valve leak, and B doesn't either"
    out += [
        (f"AHU-2 has no valve leak, and AHU-4 doesn't either {_c(LEAK2, LEAK4)}.", True),
        (f"AHU-2 has no valve leak, and AHU-1 doesn't either {_c(LEAK2, LEAK1)}.", False),
        (f"AHU-2 has no valve leak, and neither does AHU-4 {_c(LEAK2, LEAK4)}.", True),
        (f"AHU-2 has no valve leak, and neither does AHU-1 {_c(LEAK2, LEAK1)}.", False),
        (f"AHU-1 fails leaking_valve, and so does AHU-3 {_c(LEAK1, LEAK3)}.", True),
        (f"AHU-1 fails leaking_valve, and so does AHU-2 {_c(LEAK1, LEAK2)}.", False),
        # the negation of each item's verb, and of a trust class
        (f'Neither "heat_valve" point moves over the window {_c(FLAT1, FLAT2)}.', True),
        (f'Neither "heat_valve" nor "oa_damper" moves over the window {_c(FLAT1, OA1)}.', False),
        (f'Neither "AHU-1" nor "AHU-2" oa_damper is trusted {_c(OA1, OA2)}.', True),
        (f"Neither oa_damper point is trusted {_c(OA1, OA2)}.", True),
        (f"Neither oa_damper point is suspect {_c(OA1, OA2)}.", False),
        (f'Neither "Meter-C" nor "Meter-D" meets daily acceptance {_c(BASE_C, BASE_D)}.', False),
        (f'Neither "Meter-A" nor "Meter-B" meets daily acceptance {_c(BASE_A, BASE_B)}.', True),
        (f'Neither "Meter-A" nor "Meter-C" meets daily acceptance {_c(BASE_A, BASE_C)}.', False),
        # a negated set the checker cannot read fails closed
        (f"leaking_valve fails on neither AHU {_c(LEAK2, LEAK4)}.", False),
    ]
    return out


NEGATED_CASES = _negated_matrix()


@pytest.mark.parametrize("text, right", _params(NEGATED_CASES))
def test_a_negated_set_needs_the_negation_stated_of_each_item(text, right):
    _assert(text, right)


def test_the_negated_matrix_covers_both_ways():
    _both_ways(NEGATED_CASES)
    assert len(NEGATED_CASES) >= 70


# --------------------------------------------------------------------------- 3. joined names


def _joined_matrix():
    out = []
    for verdict, (a, b), true, false in (
        ("fault", ("AHU-1", "AHU-3"), (LEAK1, LEAK3), (LEAK1, LEAK2)),
        ("ok", ("AHU-2", "AHU-4"), (LEAK2, LEAK4), (LEAK2, LEAK1)),
    ):
        fa, fb = a, ("AHU-2" if verdict == "fault" else "AHU-1")
        for form in (
            "{a} and {b}: leaking_valve {v}",
            "- {a}, {b}: {v} on leaking_valve",
            "| {a}, {b} | leaking_valve | {v} |",
            "| {a} and {b} | {v} on leaking_valve |",
            "{a} and {b} — leaking_valve {v}",
            '"{a}" and "{b}": leaking_valve ({v})',
        ):
            out.append((form.format(a=a, b=b, v=verdict) + f" {_c(*true)}", True))
            out.append((form.format(a=fa, b=fb, v=verdict) + f" {_c(*false)}", False))
    out += [
        (f"Leaking valves were found on AHU-1 and AHU-3 {_c(LEAK1, LEAK3)}.", True),
        (f"Leaking valves were found on AHU-1 and AHU-2 {_c(LEAK1, LEAK2)}.", False),
        (f"The pair of AHUs fail leaking_valve (fault) {_c(LEAK1, LEAK3)}.", True),
        (f"The pair of AHUs fail leaking_valve (fault) {_c(LEAK1, LEAK2)}.", False),
        (f"The pair of AHUs fail leaking_valve (fault) {_c(LEAK1)}.", False),
        (f"Each AHU's supply_air_temp disagrees with its reference {_c(REF1, REF3)}.", True),
        (f"Each AHU's supply_air_temp disagrees with its reference {_c(REF1, REF2)}.", False),
        (f"Each AHU's supply_air_temp disagrees with its reference {_c(REF1)}.", False),
        (f'"heat_valve" and "supply_air_temp_sp" both never move {_c(FLAT1, SP1)}.', True),
        (f'"heat_valve" and "oa_damper" both never move over the window {_c(FLAT1, OA1)}.', False),
        (f"The heating valve and the OA damper both never move {_c(FLAT1, OA1)}.", False),
        (
            f'The three meters "Meter-A", "Meter-B" and "Meter-C" save energy '
            f"{_c(SAV_A, SAV_B, SAV_C)}.",
            False,
        ),
        (f'The two meters "Meter-A" and "Meter-C" save 2.5% {_c(SAV_A, SAV_C)}.', True),
    ]
    return out


def _clause_matrix():
    """A set read with its own clause only: what another clause says is not said of it."""
    return [
        (
            f"Both AHUs pass supply_air_control, but only AHU-1 fails leaking_valve "
            f"{_c(CTRL1, CTRL2, LEAK1)}.",
            True,
        ),
        (
            f"AHU-1 and AHU-2 pass supply_air_control, but only AHU-1 fails leaking_valve "
            f"{_c(CTRL1, CTRL2, LEAK1)}.",
            True,
        ),
        (
            f"Both AHUs pass supply_air_control; both also fail leaking_valve "
            f"{_c(CTRL1, CTRL2, LEAK1)}.",
            False,
        ),
        (
            f"Both AHUs pass supply_air_control, and both fail leaking_valve "
            f"{_c(CTRL1, CTRL2, LEAK1, LEAK2)}.",
            False,
        ),
        (
            f"AHU-1 fails leaking_valve; both AHUs pass supply_air_control "
            f"{_c(CTRL1, CTRL2, LEAK1)}.",
            True,
        ),
        (
            f"AHU-1 and AHU-2 differ: AHU-1 fails leaking_valve and AHU-2 passes "
            f"{_c(LEAK1, LEAK2)}.",
            True,
        ),
        (f"Neither of AHU-1 and AHU-2 fails supply_air_control {_c(CTRL1, CTRL2)}.", True),
        (f"Neither of AHU-1 and AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
        # a set whose cited facts are about none of its items: its figure is not read
        (f"Both AHUs have 24 points {_c(FACILITY)}.", False),
        (f"{FAC} has 24 points {_c(FACILITY)}.", True),
    ]


JOINED_CASES = _joined_matrix() + _clause_matrix()


@pytest.mark.parametrize("text, right", _params(JOINED_CASES))
def test_names_joined_anywhere_are_read_as_a_set(text, right):
    _assert(text, right)


def test_the_joined_matrix_covers_both_ways():
    _both_ways(JOINED_CASES)


# --------------------------------------------------------------------------- 4. bare labels


def _label_matrix():
    out = []
    for label, true, false, fact in (
        ("Suspect", 2, 0, TRUST),
        ("Trusted", 9, 11, TRUST),
        ("Untrusted", 0, 2, TRUST),
        ("suspect", 2, 3, TRUST),
        ("Passed", 19, 5, RUN14),
        ("Failed", 14, 0, RUN14),
        ("Faulted", 14, 5, RUN14),
        ("Healthy", 19, 35, RUN14),
    ):
        for form in ("{l}: {n} {c}.", "- {l}: {n} {c}", "| {l} | {n} | {c}", "{l} — {n} {c}."):
            out.append((form.format(l=label, n=true, c=_c(fact)), True))
            out.append((form.format(l=label, n=false, c=_c(fact)), False))
    out += [
        (f"{FAC} suspect: 2 {_c(TRUST)}.", True),
        (f"{FAC} suspect: 0 {_c(TRUST)}.", False),
        (f"Passed: 2 rules {_c(CLEAN)}.", True),
        (f"Passed: 5 rules {_c(RUN14)}.", False),
        (f"Rules passed: 2 of 2 {_c(CLEAN)}.", True),
        (f"Rules passed: 5 of 5 {_c(RUN14)}.", False),
        (f"Rules passed on {OTHER}: 5 of 5 {_c(RUN14)}.", False),
        (f"| 2 of 2 | passed {_c(CLEAN)} |", True),
        (f"| 5 of 5 | passed {_c(RUN14)} |", False),
    ]
    return out


LABEL_CASES = _label_matrix()


@pytest.mark.parametrize("text, right", _params(LABEL_CASES))
def test_a_bare_class_or_verdict_label_counts_its_figure(text, right):
    _assert(text, right)


def test_the_label_matrix_covers_both_ways():
    _both_ways(LABEL_CASES)


# --------------------------------------------------------------------------- 5. whole sets


def _whole_matrix():
    out = []
    for form, verdict in (
        ("The VAV boxes on {f} are {v}", "healthy"),
        ("The boxes on {f} are {v}", "healthy"),
        ("{f}'s {n} AHUs are {v}", "healthy"),
        ("{f}'s {n} AHUs {v}", "passed"),
        ("The {r} rules {v} on {f}", "passed"),
        ("The {r} rules on {f} {v}", "passed"),
    ):
        out.append((form.format(f=CLEANFAC, n=1, r=2, v=verdict) + f" {_c(CLEAN)}.", True))
        out.append((form.format(f=OTHER, n=7, r=5, v=verdict) + f" {_c(RUN14)}.", False))
    for form in ("The {r} rules faulted on {f}", "The {r} rules on {f} faulted"):
        out.append((form.format(f=FAULTFAC, r=2) + f" {_c(FAULTY)}.", True))
        out.append((form.format(f=OTHER, r=5) + f" {_c(RUN14)}.", False))
        out.append((form.format(f=CLEANFAC, r=2) + f" {_c(CLEAN)}.", False))
    out += [
        (f"Each AHU faulted on leaking_valve {_c(LEAK1, LEAK3)}.", True),
        (f"Each AHU faulted on leaking_valve {_c(LEAK1, LEAK2)}.", False),
        (f"Each air handler faulted on leaking_valve {_c(LEAK1, LEAK2)}.", False),
    ]
    return out


WHOLE_CASES = _whole_matrix()


@pytest.mark.parametrize("text, right", _params(WHOLE_CASES))
def test_a_verdict_on_a_whole_set_in_more_wordings(text, right):
    _assert(text, right)


def test_the_whole_set_matrix_covers_both_ways():
    _both_ways(WHOLE_CASES)


# --------------------------------------------------------------------------- 6. directions


def _direction_matrix():
    out = []
    for words, up in (
        ("saved energy", False),
        ("saves energy", False),
        ("reduced its energy use", False),
        ("decreased its energy use", False),
        ("increased its energy use", True),
        ("did not save energy", True),
        ("shows a negative avoided energy", True),
        ("used more energy than its baseline", True),
        ("used less energy than its baseline", False),
    ):
        for meter, fact, is_up in (("Meter-A", SAV_A, False), ("Meter-B", SAV_B, True)):
            right = up == is_up
            out.append((f'"{meter}" {words} {_c(fact)}.', right))
    out += [
        (f'"Meter-B" avoided energy -293,693 (-15.6%) {_c(SAV_B)}.', True),
        (f'"Meter-A" avoided energy 2,915 (2.5%) {_c(SAV_A)}.', True),
        (f'"Meter-B" avoided energy 293,693 (15.6%) {_c(SAV_B)}.', False),
        (f"Both meters save energy {_c(SAV_A, SAV_C)}.", True),
        (f"Both meters save energy {_c(SAV_A, SAV_B)}.", False),
        (f'Neither "Meter-B" nor "Meter-A" saved energy {_c(SAV_B, SAV_A)}.', False),
        (f"This saving is for information only {_c(SAV_B)}.", True),
    ]
    return out


DIRECTION_CASES = _direction_matrix()


@pytest.mark.parametrize("text, right", _params(DIRECTION_CASES))
def test_a_direction_word_follows_the_sign_of_a_saving(text, right):
    _assert(text, right)


def test_the_direction_matrix_covers_both_ways():
    _both_ways(DIRECTION_CASES)


# --------------------------------------------------------------------------- 7. minors

MINORS = [
    # an elliptical "none" or "the rest" after a count
    (f"2 points on {FAC} are suspect and none is trusted {_c(TRUST)}.", False),
    (f"2 points on {FAC} are suspect, and none are trusted {_c(TRUST)}.", False),
    (f"2 points on {FAC} are suspect and none is untrusted {_c(TRUST)}.", True),
    (f"9 points on {FAC} are trusted and the rest are untrusted {_c(TRUST)}.", False),
    (f"9 points on {FAC} are trusted and the rest are suspect {_c(TRUST)}.", True),
    (
        f"The two units ran leaking_valve {_c(LEAK1, LEAK2)}. Each fails it {_c(LEAK1, LEAK2)}.",
        False,
    ),
    (f"Each fails it {_c(LEAK1, LEAK3)}.", True),
    # "well over", "just over"
    (f"Well over 2 points on {FAC} are suspect {_c(TRUST)}.", False),
    (f"Just over 2 points on {FAC} are suspect {_c(TRUST)}.", False),
    # a bound is a threshold, not a count: its true form fails closed too
    (f"Well over 8 points on {FAC} are trusted {_c(TRUST)}.", False),
    # true sentences no longer stripped
    (
        f"Fewer than a quarter of the 106 flagged hours fall in the reference window {_c(PART)}.",
        True,
    ),
    (
        f"More than a quarter of the 106 flagged hours fall in the reference window {_c(PART)}.",
        False,
    ),
    (f"No point falls below the trust floor 0.5 {_c(TRUST)}.", True),
    (f"No suspect point is below the trust floor 0.5 {_c(TRUST)}.", True),
    (f"No suspect point was found on {FAC} {_c(TRUST)}.", False),
    (f"None of the 59 judged samples on AHU-2 were flagged {_c(SAMPLES0)}.", True),
    (f"0 of the 59 judged samples on AHU-2 were flagged {_c(SAMPLES0)}.", True),
    (f"The rule flagged none of the 59 judged samples on AHU-2 {_c(SAMPLES0)}.", True),
    (f"None of the 58 judged samples on AHU-2 were flagged {_c(SAMPLES0)}.", False),
    (
        f'The facilities list shows "{FAC}" as active, with 24 points on 4 equipment '
        f"{_c(FACILITY)}.",
        True,
    ),
    (f'The facilities listing shows "{FAC}" as active {_c(FACILITY)}.', True),
    (f"The facilities have 24 points {_c(FACILITY)}.", False),
    (f"One rule returned a fault {_c(RUN1)}.", True),
    (f"1 rule returned a fault {_c(RUN1)}.", True),
    (f"2 rules returned a fault {_c(RUN1)}.", False),
    (f"One rule returned a fault {_c(RUN2)}.", False),
    # the papercut: rule_run's verdicts count results, not rules
    (f"Of the 3 rules, 1 returned a fault, 2 a warn, 3 ok {_c(RUN2)}.", False),
    (
        f"Of the 3 rules that ran, 1 returned a fault, 2 returned a warn, and 3 returned ok "
        f"{_c(RUN2)}.",
        False,
    ),
    (f"The rules returned 1 fault, 2 warn and 3 ok {_c(RUN2)}.", True),
    (f"6 rule-equipment results: 1 fault, 2 warn, 0 info and 3 ok {_c(RUN2)}.", True),
]


@pytest.mark.parametrize("text, right", _params(MINORS))
def test_the_minors_and_the_papercut(text, right):
    _assert(text, right)


def test_a_rule_count_said_of_results_gets_a_why_that_says_what_to_write():
    data = _check(f"1 rule returned a fault {_c(RUN2)}.")
    why = data["sentences"][0]["why"]
    assert "rule-equipment results, not rules" in why and "'1 fault'" in why


def test_a_negated_set_it_cannot_read_says_so():
    data = _check(f"leaking_valve fails on neither AHU {_c(LEAK2, LEAK4)}.")
    assert "negated set is read only where it opens its clause" in data["sentences"][0]["why"]


def test_a_per_item_figure_names_the_item_it_is_false_for():
    data = _check(f"Both meters save 2.5% {_c(SAV_A, SAV_B)}.")
    assert "'Meter-B'" in data["sentences"][0]["why"]


# --------------------------------------------------------------------------- the rule_run text


def test_rule_run_counts_rule_equipment_results(tmp_path):
    import _rcx_fixture as fx

    from camber.capabilities import CallContext, default_registry

    st = fx.make_store(tmp_path, meta=None)
    ctx = CallContext(store=st.root, allowed_facilities=frozenset({fx.FID}))
    res = default_registry().call(
        "rule_run", {"facility_id": fx.FID, "min_severity": "ok", "limit": 500}, ctx
    )
    head = res.facts[0]["text"]
    n = res.facts[0]["value"]
    assert f"{n} rule-equipment result(s) in all: " in head
    counts = res.facts[0]["counts"]
    assert sum(counts.values()) == n
