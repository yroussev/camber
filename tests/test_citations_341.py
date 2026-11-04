"""What the 0.112 acceptance test, round 7, found check_citations still judging wrong (#341), as
generated matrices rather than single sentences, each both ways (the false form fails, its true
twin passes).

Two structural rules first. (1) A sentence that says the text, or any part of it, was checked,
verified, supported, validated or grounded is never supported, whatever it cites: only
check_citations' own result says what it checked. A matrix of subjects, verbs and labels is cited
with every capability's facts. (2) A sentence that names a unit (an equipment, a meter) is read
only against the cited facts about that unit, for its figures, verdicts and negations alike, and
a whole site's fact supports only what is said of the site; "Only X", "X, not Y", "Unlike Y, X"
and "Y passes while X fails" read each unit against its own facts. A matrix of pairs of facts
about two units, over every kind of fact the tests know, says one unit's property of the other.

Then: a stretch of time (all year, year-round, for months, all summer, throughout 2025, the whole
of May, every day in May 2025) must lie within a cited fact's window; "in all hours" and a
quantifier of a bare noun need a share of 100% of the same noun with the fact's qualifier;
coverage words need a coverage of 100%; names set off mid-sentence, the other joiners and the
negated sets; figure lists by slash, "vs", before the names, "rank ... respectively" and
possessive gapping; possessive quantifiers; the saving words; and the minors and the papercut.
The facts are cut down from the testers' and use made-up facilities and equipment.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_citations_339 as t339  # noqa: E402
import test_citations_340 as t340  # noqa: E402
from test_citations_340 import CAPABILITIES, _sample, capability_facts  # noqa: E402, F401

from camber.capabilities._cap_check_citations import check_citations  # noqa: E402

FAC = t339.FAC
CTX = t339.CTX
LEAK1, LEAK2 = t339.LEAK1, t339.LEAK2  # AHU-1 faults, AHU-2 has no valve leak
OA1, OA2 = t339.OA1, t339.OA2  # trust 0.66, 0.68
FLAT1, FLAT2, SP1 = t339.FLAT1, t339.FLAT2, t339.SP1
COV_A1, COV_A2, COV_L1 = t339.COV_A1, t339.COV_A2, t339.COV_L1  # 100.0%, 100.0%, 19.1%
REF1, REF2 = t339.REF1, t339.REF2  # bias +3.0 (warn), +0.0 (ok)
SAV_A, SAV_B = t339.SAV_A, t339.SAV_B  # 2.5%, -15.6%
BASE_A, BASE_C = t339.BASE_A, t339.BASE_C
CTRL1, CTRL2 = t339.CTRL1, t339.CTRL2
RUN2, CLEAN = t339.RUN2, t339.CLEAN
VAV1, VAV2, VAV3 = t340.VAV1, t340.VAV2, t340.VAV3  # unmet 100%, 81%, 82%


def _cov_point(equip, pct, gaps, n):
    return {
        "id": f"P{n}@coverage-aaaaaa",
        "kind": "point_coverage",
        "equip": equip,
        "role": "supply_air_temp",
        "value": pct,
        "unit": "%",
        "subject": {"facility_id": FAC, "equip": equip, "role": "supply_air_temp"},
        "text": f'"{equip}" "supply_air_temp": coverage {pct:.1f}% of the whole window (384 '
        f"samples), {gaps} gap(s), the longest 240 hours",
    }


PCOV_L1 = _cov_point("LOGGER-1", 19.1, 2, 1)
PCOV_A1 = _cov_point("AHU-1", 100.0, 0, 2)
COVERAGE = {
    "id": "F1@coverage-aaaaaa",
    "kind": "coverage",
    "facility_id": FAC,
    "value": 93.2,
    "unit": "%",
    "subject": {"facility_id": FAC},
    "text": f"{FAC}: 24 points on 4 equipment have a mean coverage of 93.2% of the whole window "
    "from 2025-05-12T00:00:00 to 2025-06-01T23:45:00; 2 point(s) are below 90% and 4 gap(s) "
    "are listed",
}
FREE = {
    "id": "F21@rule_run-ffffff",
    "kind": "finding",
    "value": "fault",
    "rule": "free_cooling_missed",
    "equip": "AHU-5",
    "facility_id": FAC,
    "text": '"AHU-5" free_cooling_missed (fault): "AHU-5": mechanical cooling (valve > 5%) ran '
    "100% of the 1671 free-cooling hours",
}
RESET = {
    "id": "F22@rule_run-ffffff",
    "kind": "finding",
    "value": "warn",
    "rule": "supply_air_reset_compliance",
    "equip": "AHU-1",
    "facility_id": FAC,
    "text": '"AHU-1" supply_air_reset_compliance (warn): "AHU-1": supply-air temp below target '
    "54% of hours",
}
YEAR = {
    "id": "L9@sensor_trust-cccccc",
    "kind": "flat_point",
    "equip": "AHU-9",
    "role": "supply_air_temp_sp",
    "verdict": "flat",
    "start": "2018-01-01T01:00:00",
    "end": "2018-12-31T23:00:00",
    "subject": {"facility_id": FAC, "equip": "AHU-9", "role": "supply_air_temp_sp"},
    "text": '"AHU-9" "supply_air_temp_sp" never moves over the window: all 35,038 valid samples '
    "from 2018-01-01 01:00 to 2018-12-31 23:00 read 55.184 °F, so it is flat for the whole "
    "window",
}
CLAIMED = {
    "id": "A2@mv_summary-aaaaaa",
    "kind": "claimed_figure",
    "basis": "claimed",
    "value": 12,
    "unit": "%",
    "subject": {"facility_id": t339.OTHER},
    "text": "The claim under check states 12%: the claim's own figure, not a measured or computed "
    "result",
}


def _span(n, day, hours):
    return {
        "id": f"S{n}@finding_detail-aaaaaa",
        "kind": "evidence_span",
        "finding_id": "leaking_valve@AHU-1",
        "start": f"{day}T06:00:00",
        "end": f"{day}T08:45:00",
        "value": hours,
        "unit": "h",
        "subject": {"facility_id": FAC, "equip": "AHU-1"},
        "text": f'"AHU-1" leaking_valve flagged span {n} of 3 in time order: {day} 06:00 to '
        f"{day} 08:45, {hours} hours",
    }


SPAN1, SPAN2, SPAN3 = (
    _span(1, "2025-05-14", 3),
    _span(2, "2025-05-15", 2.75),
    _span(3, "2025-05-16", 2.25),
)
CHECKED = {
    "id": "F1@check_citations-aaaaaa",
    "kind": "citation_check",
    "value": 1,
    "text": "1 of 1 sentence(s) are supported by the facts they cite; 0 cite an unknown id, 0 cite "
    "nothing, 0 state a figure no cited fact carries and 0 make a claim no cited fact makes. "
    "This check covers only the text passed in this call (with detail, verified_text): give "
    "the user only text a check marked supported",
}

FACTS = [
    *t339.FACTS,
    *(f for f in t340.FACTS if f not in t339.FACTS),
    PCOV_L1,
    PCOV_A1,
    COVERAGE,
    FREE,
    RESET,
    YEAR,
    CLAIMED,
    SPAN1,
    SPAN2,
    SPAN3,
    CHECKED,
]
_BY_ID = {f["id"]: f for f in FACTS}
assert len(_BY_ID) == len(FACTS)


def _check(text: str, facts=None) -> dict:
    """check_citations on ``text``, with the facts it cites (in the order cited)."""
    if facts is None:
        ids = list(dict.fromkeys(re.findall(r"[A-Z]+\d+@[a-z_]+-[a-z]{6}", text)))
        facts = [_BY_ID[i] for i in ids if i in _BY_ID] or FACTS[:1]
    return check_citations(CTX, {"text": text, "facts": facts, "detail": True}).data


def _c(*facts) -> str:
    return "".join(f"[{f['id']}]" for f in facts)


def _params(cases):
    return [
        pytest.param(text, right, id=f"{'ok' if right else 'no'}-{i}")
        for i, (text, right) in enumerate(cases)
    ]


def _both_ways(cases):
    assert sum(1 for _t, ok in cases if ok) >= 3, "each family needs true twins"
    assert sum(1 for _t, ok in cases if not ok) >= 3, "each family needs false forms"
    return _params(cases)


def _judged(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])
    return data


# --------------------------------------------------------------------------- 1. claims of checking

_CHECK_WHY = "it says the text, or a part of it, was checked"
_SUBJECTS = [
    "All of this text",
    "All of this answer",
    "All of it",
    "All of the above",
    "Everything above",
    "Everything in this answer",
    "This text",
    "The text",
    "This answer",
    "The summary above",
    "Each sentence of this text",
    "Every line of this answer",
    "Every figure here",
    "Each claim above",
    "The draft",
    "This report",
    "1 of 1 sentences in this answer",
    "All three sentences below",
    "This",
    "It",
]
_PREDICATES = [
    "was checked",
    "was fully checked",
    "was checked in full",
    "is supported",
    "is supported by the facts",
    "has been verified",
    "was validated",
    "is grounded",
    "has been fact-checked",
    "was confirmed",
    "is backed by the cited facts",
    "has all been double-checked",
]
_LABELS = ["", "NOTICE: ", "**NOTICE:** ", "- "]
CHECKING = [
    f"{label}{subject} {pred}" for subject in _SUBJECTS for pred in _PREDICATES for label in _LABELS
]
CHECKING += [
    "check_citations found every sentence supported",
    "The citation check marked all 5 sentences supported",
    "The check marked every sentence of this answer as supported",
    "This answer passed check_citations",
    "All of this passed the citation check",
    "The whole answer passed check_citations",
    "Line 1: supported",
    "| Sentence 2 | supported |",
    "Every sentence below was verified by check_citations",
]
assert len(CHECKING) >= 900


@pytest.mark.parametrize("name", CAPABILITIES)
def test_a_claim_of_checking_is_never_supported(capability_facts, name):  # noqa: F811
    """Every wording of the matrix, cited with each capability's facts (each alone and a sample
    of them together), fails, and the why says it is a claim of checking."""
    by_cap, ctx = capability_facts
    facts = by_cap[name]
    assert facts, name
    cited_sets = [[f] for f in _sample(facts, 6)] + [_sample(facts, 20)]
    bad = []
    for i, cited in enumerate(cited_sets):
        text = "\n\n".join(f"{s} {_c(*cited)}." for s in CHECKING[i :: len(cited_sets)])
        data = check_citations(ctx, {"text": text, "facts": cited, "detail": True}).data
        for s in data["sentences"]:
            if s["verdict"] == "supported" or _CHECK_WHY not in s.get("why", ""):
                bad.append((s["text"][:120], s["verdict"], s.get("why", "")[:160]))
    assert not bad, bad[:5]


@pytest.mark.parametrize("fact", [RUN2, LEAK1, FLAT1, COV_L1, SAV_A, CHECKED, t339.TRUST])
def test_a_claim_of_checking_fails_citing_any_fact(fact):
    text = "\n\n".join(f"{s} {_c(fact)}." for s in CHECKING)
    data = _check(text, [fact])
    bad = [s["text"] for s in data["sentences"] if _CHECK_WHY not in s.get("why", "")]
    assert not bad, bad[:5]


@pytest.mark.parametrize(
    "text",
    [
        # check words not said of the text: the rule does not read them as a claim of checking
        f"The saving is for information only, not a verified saving {_c(SAV_A)}.",
        f"AHU-1 was checked against the reset target {_c(RESET)}.",
        f"Check the AHU-1 supply_air_temp sensor before acting on the equipment {_c(REF1)}.",
        f"The claimed 12% is not supported by the measured 2.5% {_c(SAV_A, CLAIMED)}.",
        f"The leak should be confirmed on site {_c(LEAK1)}.",
        f"Every equipment on {FAC} was checked by the rules {_c(RUN2)}.",
        f"{CHECKED['text']} {_c(CHECKED)}.",  # check_citations' own words, quoted whole
    ],
)
def test_check_words_not_said_of_the_text_are_not_a_claim_of_checking(text):
    data = _check(text)
    assert all(_CHECK_WHY not in s.get("why", "") for s in data["sentences"]), data["sentences"]


def test_check_citations_own_fact_quoted_whole_is_supported():
    assert _check(f"{CHECKED['text']} {_c(CHECKED)}.")["grounded"]


# --------------------------------------------------------------------------- 2. subject binding

#: pairs of facts about two units, each with what it states that the other does not: a verdict,
#: a negation or a figure, as a sentence naming the unit ({u})
PAIRS = [
    # (fact about A, A, fact about B, B, [sentences true of A])
    (
        LEAK1,
        "AHU-1",
        LEAK2,
        "AHU-2",
        [
            "{u} fails leaking_valve",
            "{u} has a leaking_valve fault",
            "The leaking_valve finding on {u} is a fault",
        ],
    ),
    (
        LEAK2,
        "AHU-2",
        LEAK1,
        "AHU-1",
        [
            "{u} has no valve leak",
            "{u} passes leaking_valve",
            "{u} is ok on leaking_valve",
            "{u} does not leak",
        ],
    ),
    (
        OA1,
        "AHU-1",
        OA2,
        "AHU-2",
        [
            "{u}'s oa_damper trust is 0.66",
            "The {u} oa_damper trust is 0.66",
            "{u} oa_damper scores 0.66",
        ],
    ),
    (
        COV_L1,
        "LOGGER-1",
        COV_A2,
        "AHU-2",
        ["{u} is at 19.1% coverage", "{u} has 19.1% coverage", "{u}'s coverage is 19.1%"],
    ),
    (COV_A2, "AHU-2", COV_L1, "LOGGER-1", ["{u} is at 100.0% coverage", "{u} has 100.0% coverage"]),
    (
        REF1,
        "AHU-1",
        REF2,
        "AHU-2",
        [
            "The {u} supply_air_temp disagrees with its reference",
            "{u}'s supply_air_temp is biased +3.0",
        ],
    ),
    (
        REF2,
        "AHU-2",
        REF1,
        "AHU-1",
        [
            "The {u} supply_air_temp agrees with its reference",
            "{u}'s supply_air_temp is biased +0.0",
        ],
    ),
    (
        SAV_B,
        "Meter-B",
        SAV_A,
        "Meter-A",
        ['"{u}" used more energy than its baseline', '"{u}" has an avoided energy of -15.6%'],
    ),
    (
        SAV_A,
        "Meter-A",
        SAV_B,
        "Meter-B",
        ['"{u}" saved energy', '"{u}" has an avoided energy of 2.5%'],
    ),
    (VAV1, "VAV-1", VAV2, "VAV-2", ["{u} is unmet 100% of occupied hours"]),
    (VAV2, "VAV-2", VAV1, "VAV-1", ["{u} is unmet 81% of occupied hours"]),
    (CTRL1, "AHU-1", LEAK1, "AHU-1", []),  # one unit: nothing to swap
]


def _binding_matrix():
    cases = []
    for fa, a, fb, b, said in PAIRS:
        if a == b:
            continue
        for s in said:
            for cites in (_c(fa, fb), _c(fb, fa)):
                cases.append((f"{s.format(u=a)} {cites}.", True))  # A's own, B's fact cited too
                cases.append((f"{s.format(u=b)} {cites}.", False))  # A's said of B
    return cases


BINDING = _binding_matrix()


@pytest.mark.parametrize("text, right", _both_ways(BINDING))
def test_a_named_unit_is_read_only_against_its_own_facts(text, right):
    _judged(text, right)


def test_the_binding_matrix_covers_every_kind_of_pair():
    assert len(BINDING) >= 80
    assert {k for k in (fa["kind"] for fa, *_ in PAIRS)} >= {
        "finding",
        "point_trust",
        "equip_coverage",
        "reference_comparison",
    }


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # "only", "alone", "just": the others cited must not have it
            (f"Only AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"Only AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 alone fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-2 alone fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"Just AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"Only VAV-1 is unmet 100% of occupied hours {_c(VAV1, VAV2)}.", True),
            (f"Only VAV-2 is unmet 100% of occupied hours {_c(VAV1, VAV2)}.", False),
            (f"Only AHU-1 fails leaking_valve {_c(LEAK1, t339.LEAK3)}.", False),  # AHU-3 too
            # "X, not Y", "X but not Y", "rather than", "instead of", "unlike", "it is X ..."
            (f"AHU-1, not AHU-2, fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-2, not AHU-1, fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 but not AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-2 but not AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 rather than AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-2 instead of AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"It is AHU-1, not AHU-2, that fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"It is AHU-2, not AHU-1, that fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-2 -- not AHU-1 -- has the valve leak {_c(LEAK1, LEAK2)}.", False),
            (f"Unlike AHU-2, AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"Unlike AHU-1, AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-2 unlike AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-2, not AHU-1, has an oa_damper trust of 0.66 {_c(OA1, OA2)}.", False),
            (f"AHU-2, not AHU-1, has an oa_damper trust of 0.68 {_c(OA1, OA2)}.", True),
            # two clauses about two units, each read against its own facts
            (f"AHU-1 fails leaking_valve while AHU-2 passes it {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-2 fails leaking_valve while AHU-1 passes it {_c(LEAK1, LEAK2)}.", False),
            (f"Whereas AHU-2 passes, AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (f"Whereas AHU-1 passes, AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 fails leaking_valve; AHU-2 passes it {_c(LEAK1, LEAK2)}.", True),
            (f"AHU-1 passes leaking_valve, but AHU-2 fails it {_c(LEAK1, LEAK2)}.", False),
            # a whole site's fact gives a unit's claim nothing
            (f"AHU-2 fails leaking_valve {_c(RUN2, LEAK2)}.", False),
            (f"AHU-1 fails leaking_valve {_c(RUN2, LEAK1)}.", True),
            (f"LOGGER-1 fails leaking_valve {_c(LEAK1, COV_L1)}.", False),
        ]
    ),
)
def test_only_not_unlike_and_contrasts_read_each_unit_against_its_own_facts(text, right):
    _judged(text, right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"The site is healthy {_c(CLEAN)}.", True),
            (f"{t339.CLEANFAC} is ok {_c(CLEAN)}.", True),
            (f"Overall, the plant passes {_c(CLEAN)}.", True),
            (f"The site is healthy {_c(RUN2)}.", False),
            (f"All told, the site is healthy {_c(RUN2)}.", False),
            (f"{FAC} is healthy {_c(RUN2)}.", False),
            (f"The plant is ok {_c(RUN2)}.", False),
            (f"Overall, the building is fine {_c(t339.FAULTY)}.", False),
            (f"The site is healthy {_c(LEAK1, LEAK2)}.", False),  # no count of the site's results
            (f"The site is healthy {_c(LEAK2)}.", False),
        ]
    ),
)
def test_a_verdict_on_a_whole_site_needs_every_result_to_have_it(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 3. time spans

_SPANS_FALSE = [
    "all year",
    "year-round",
    "for the whole year",
    "the entire year",
    "every day of the year",
    "for months",
    "all summer",
    "all winter",
    "all month",
    "throughout 2025",
    "all of 2025",
    "the whole of May",
    "all of May",
    "every day in May 2025",
    "the entire month of May",
    "all of last year",
    "all week, every week, for months",
]
_SPANS_TRUE = ["for the whole window", "all the time", "every day of the window"]


def _span_matrix():
    cases = []
    for span in _SPANS_FALSE:
        cases.append((f"The AHU-1 heat_valve read 0 % {span} {_c(FLAT1)}.", False))
    for span in _SPANS_TRUE:
        cases.append((f"The AHU-1 heat_valve read 0 % {span} {_c(FLAT1)}.", True))
    # a fact whose window is a whole year holds the year's stretches
    for span in (
        "all year",
        "for all of 2018",
        "throughout 2018",
        "every day of 2018",
        "all of May",
        "all summer",
        "for months",
    ):
        cases.append((f"The AHU-9 supply_air_temp_sp read 55.184 °F {span} {_c(YEAR)}.", True))
    for span in ("throughout 2019", "all of 2017 and 2018", "every day in May 2019"):
        cases.append((f"The AHU-9 supply_air_temp_sp read 55.184 °F {span} {_c(YEAR)}.", False))
    return cases


@pytest.mark.parametrize("text, right", _both_ways(_span_matrix()))
def test_a_stretch_of_time_must_lie_within_the_facts_window(text, right):
    _judged(text, right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # "in all hours" / "every hour" against a share below 100%, or of a qualified subset
            (f"The AHU-1 supply-air temp was below target in all hours {_c(RESET)}.", False),
            (f"In every hour the AHU-1 supply-air temp was below target {_c(RESET)}.", False),
            (f"The AHU-1 supply-air temp was below target in 54% of hours {_c(RESET)}.", True),
            (f"AHU-5 ran mechanical cooling in all hours {_c(FREE)}.", False),
            (f"Every hour of the window on AHU-5 ran mechanical cooling {_c(FREE)}.", False),
            (f"AHU-5 ran mechanical cooling 100% of hours {_c(FREE)}.", False),
            (f"AHU-5 ran mechanical cooling in every free-cooling hour {_c(FREE)}.", True),
            (f"AHU-5 ran mechanical cooling 100% of the free-cooling hours {_c(FREE)}.", True),
            (f"AHU-5 ran mechanical cooling through all 1671 free-cooling hours {_c(FREE)}.", True),
            (f"VAV-1 was too hot every hour {_c(VAV1)}.", False),
            (f"VAV-1 was unmet in every occupied hour {_c(VAV1)}.", True),
            (f"Most samples on AHU-1 show a heating leak {_c(LEAK1)}.", False),
            (f"The AHU-1 heating coil leaked in all both-valves-shut samples {_c(LEAK1)}.", False),
            (f"Most both-valves-shut samples on AHU-1 show a heating leak {_c(LEAK1)}.", True),
        ]
    ),
)
def test_all_hours_and_a_bare_noun_need_a_full_share_of_the_same_noun(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 4. coverage words


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"LOGGER-1 supply_air_temp has full coverage {_c(PCOV_L1)}.", False),
            (f"LOGGER-1 supply_air_temp has data for the whole window {_c(PCOV_L1)}.", False),
            (f"LOGGER-1 supply_air_temp was logged for the entire window {_c(PCOV_L1)}.", False),
            (f"LOGGER-1 supply_air_temp covers the whole window {_c(PCOV_L1)}.", False),
            (f"LOGGER-1 supply_air_temp was trended all the time {_c(PCOV_L1)}.", False),
            (f"LOGGER-1 supply_air_temp has gap-free data {_c(PCOV_L1)}.", False),
            (f"The 24 points on {FAC} were logged for the whole window {_c(COVERAGE)}.", False),
            (f"AHU-1 supply_air_temp has full coverage {_c(PCOV_A1)}.", True),
            (f"AHU-1 supply_air_temp has data for the whole window {_c(PCOV_A1)}.", True),
            (f"AHU-1 supply_air_temp has complete data {_c(PCOV_A1)}.", True),
            (
                f"LOGGER-1 supply_air_temp has a coverage of 19.1% of the whole window "
                f"{_c(PCOV_L1)}.",
                True,
            ),
            (
                f"The 24 points on {FAC} have a mean coverage of 93.2% of the whole window "
                f"{_c(COVERAGE)}.",
                True,
            ),
        ]
    ),
)
def test_coverage_words_need_a_coverage_of_100_percent(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 5. sets and lists

_JOINS = [
    "{a} and also {b}",
    "{a} + {b}",
    "{a} and/or {b}",
    "{a}; {b}",
    "{a} in addition to {b}",
    "{a}, besides {b},",
    "{a} alongside {b}",
    "{a} and, likewise, {b}",
    "{a} (and {b})",
    "{a}, like {b},",
    "{a} -- like {b} --",
    "{a} -- as well as {b} --",
    "{a} (as well as {b})",
    "{a}, and also {b},",
]


def _join_matrix():
    cases = []
    for join in _JOINS:
        both = join.format(a="AHU-1", b="AHU-3")
        mixed = join.format(a="AHU-1", b="AHU-2")
        cases.append((f"{both} fail leaking_valve {_c(LEAK1, t339.LEAK3)}.", True))
        cases.append((f"{mixed} fail leaking_valve {_c(LEAK1, LEAK2)}.", False))
    return cases


@pytest.mark.parametrize("text, right", _both_ways(_join_matrix()))
def test_names_set_off_or_joined_in_other_ways_are_a_set(text, right):
    _judged(text, right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (
                f"No one of VAV-1, VAV-2 and VAV-3 faults on unmet_setpoint_hours "
                f"{_c(VAV1, VAV2, VAV3)}.",
                False,
            ),
            (
                f"Not a single one of VAV-1, VAV-2 and VAV-3 faults on unmet_setpoint_hours "
                f"{_c(VAV1, VAV2, VAV3)}.",
                False,
            ),
            (f"Nor AHU-1 nor AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"Not AHU-1, not AHU-2: neither fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"Nor AHU-2 nor AHU-4 fails leaking_valve {_c(LEAK2, t339.LEAK4)}.", True),
            (f"No one of AHU-2 and AHU-4 has a valve leak {_c(LEAK2, t339.LEAK4)}.", True),
            (f"Not AHU-2, not AHU-4: neither fails leaking_valve {_c(LEAK2, t339.LEAK4)}.", True),
            (
                f"AHU-1 and AHU-3, which fail leaking_valve, are on {FAC} {_c(LEAK1, t339.LEAK3)}.",
                True,
            ),
            (f"AHU-1 and AHU-2, which pass leaking_valve, are on {FAC} {_c(LEAK1, LEAK2)}.", False),
            (f"AHU-1 passes leaking_valve {_c(LEAK1, CTRL1)}.", False),  # the rule's fact only
            (f"AHU-1 passes supply_air_control {_c(LEAK1, CTRL1)}.", True),
        ]
    ),
)
def test_negated_sets_and_named_sets_with_a_relative_clause(text, right):
    _judged(text, right)


def _list_matrix():
    forms = [
        "AHU-1/AHU-2 oa_damper trust: {x}/{y}",
        "AHU-1 vs AHU-2 oa_damper trust: {x} vs {y}",
        "AHU-1 and AHU-2 rank {x} and {y} on oa_damper trust respectively",
        "AHU-1's oa_damper trust is {x} and AHU-2's {y}",
        "AHU-1's oa_damper trust is {x}; AHU-2's is {y}",
        "AHU-1's oa_damper scores {x}, AHU-2's {y}",
        "AHU-1's oa_damper trust ({x}) is lower than AHU-2's ({y})",
        "With oa_damper trusts of {x} and {y}, AHU-1 and AHU-2 have outliers",
    ]
    cases = []
    for form in forms:
        cases.append((f"{form.format(x='0.66', y='0.68')} {_c(OA1, OA2)}.", True))
        cases.append((f"{form.format(x='0.68', y='0.66')} {_c(OA1, OA2)}.", False))
    cases += [
        (f"VAV-1 and VAV-2 are unmet 100%/81% of occupied hours {_c(VAV1, VAV2)}.", True),
        (f"VAV-1 and VAV-2 are unmet 81%/100% of occupied hours {_c(VAV1, VAV2)}.", False),
        (
            f"The flagged spans on 2025-05-14, 2025-05-15 and 2025-05-16 lasted 3, 2.75 and 2.25 "
            f"hours respectively {_c(SPAN1, SPAN2, SPAN3)}.",
            True,
        ),
        (
            f"The flagged spans on 2025-05-14, 2025-05-15 and 2025-05-16 lasted 2.25, 2.75 and 3 "
            f"hours respectively {_c(SPAN1, SPAN2, SPAN3)}.",
            False,
        ),
        (f"The span on 2025-05-14 lasted 3 hours {_c(SPAN1, SPAN3)}.", True),
        (f"The span on 2025-05-14 lasted 2.25 hours {_c(SPAN1, SPAN3)}.", False),
    ]
    return cases


@pytest.mark.parametrize("text, right", _both_ways(_list_matrix()))
def test_figure_lists_in_other_forms_are_matched_in_order(text, right):
    _judged(text, right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"All of AHU-1's points read 0 % {_c(FLAT1)}.", False),
            (f"All of AHU-1's valves read 0 % {_c(FLAT1)}.", False),
            (f"Every one of AHU-1's points reads 0 % {_c(FLAT1)}.", False),
            (f"All AHU-1's points are flat for the whole window {_c(FLAT1)}.", False),
            (f"The heat_valve and all of AHU-1's other points read 0 % {_c(FLAT1)}.", False),
            (f"All of AHU-1's heat_valve samples read 0 % {_c(FLAT1)}.", True),
            (f"All 2,016 valid samples of the AHU-1 heat_valve read 0 % {_c(FLAT1)}.", True),
            (f"The AHU-1 heat_valve is flat for the whole window {_c(FLAT1)}.", True),
        ]
    ),
)
def test_a_quantifier_of_a_possessive_noun(text, right):
    _judged(text, right)


@pytest.mark.parametrize(
    "form, right",
    _both_ways(
        [
            ('"Meter-B" came in under its baseline', False),
            ('"Meter-B" beat its baseline', False),
            ('"Meter-B" ran below its baseline', False),
            ('"Meter-B"\'s energy use went down', False),
            ('"Meter-B"\'s consumption declined', False),
            ('"Meter-B" is a savings success', False),
            ('"Meter-B" consumed less', False),
            ('"Meter-B" ran above its baseline', True),
            ('"Meter-B"\'s energy use went up', True),
            ('"Meter-B" consumed more', True),
            ('"Meter-B" has a savings result', True),
            ('"Meter-A" came in under its baseline', None),
        ]
    ),
)
def test_saving_words_follow_the_sign(form, right):
    meter = "Meter-A" if "Meter-A" in form else "Meter-B"
    fact = SAV_A if meter == "Meter-A" else SAV_B
    if right is None:
        right = True  # the same words on a meter that saved
    _judged(f"{form} {_c(fact)}.", right)


# --------------------------------------------------------------------------- minors, papercut


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"Most of the 6 results on {FAC} are ok {_c(RUN2)}.", False),  # 3 of 6
            (f"Most of the 6 results on {FAC} are faults {_c(RUN2)}.", False),
            (f"Most of the 35 results on {t339.OTHER} are ok {_c(t339.RUN14)}.", True),  # 19 of 35
            (f"All 11 matching points on {FAC} were judged {_c(t339.TRUST)}.", True),
            (f"All 11 matching points on {FAC} were trusted {_c(t339.TRUST)}.", False),
            (f"AHU-2 does not leak {_c(LEAK2)}.", True),
            (f"AHU-1 does not leak {_c(LEAK1)}.", False),
            (f"The 12% is not a measured result {_c(CLAIMED)}.", True),
            (f"The 12% is a measured result {_c(CLAIMED)}.", False),
            (f"Every meter has a savings result {_c(SAV_A, SAV_B)}.", True),
            (f'"Meter-B" has a savings result {_c(SAV_B)}.', True),
            (
                f"Both loggers are at 19.1% {_c(COV_L1)}, while both AHUs are at 100.0% "
                f"{_c(COV_A1, COV_A2)}.",
                None,
            ),
            (
                f"LOGGER-1 is at 19.1% {_c(COV_L1)}, while AHU-1 and AHU-2 are at 100.0% "
                f"{_c(COV_A1, COV_A2)}.",
                True,
            ),
            (
                f"LOGGER-1 is at 100.0% {_c(COV_L1)}, while AHU-1 and AHU-2 are at 100.0% "
                f"{_c(COV_A1, COV_A2)}.",
                False,
            ),
            (f"The AHU-9 supply_air_temp_sp read 55.184 °F for all of 2018 {_c(YEAR)}.", True),
        ]
    ),
)
def test_minors(text, right):
    if right is None:  # one logger cited for "both": fails, the second part is read alone
        assert not _check(text)["grounded"]
        return
    _judged(text, right)


def test_a_year_after_all_of_is_a_stretch_of_time_in_the_why():
    s = _check(f"The AHU-1 heat_valve read 0 % for all of 2025 {_c(FLAT1)}.")["sentences"][0]
    assert s["verdict"] == "unsupported_claim"
    assert "all 2025 of them" not in s["why"] and "2025-05-12 to 2025-06-01" in s["why"], s
