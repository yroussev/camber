"""What the 0.112 acceptance test, round 8, found check_citations still judging wrong (#342), as
generated matrices, each both ways (the false form fails, its true twin passes).

(1) Claims of checking: every sentence, cited or not, is screened for a claim that the text, a
part of it, the work it gives ("this triage", "these findings") or what it states was checked,
verified, supported, validated, grounded, confirmed, reviewed, fact-checked or backed -- short
("All supported", "Checked", "✅ Supported"), as a status or a lead-in ("Status: all supported",
"Checked: ...", "Validated by the data, ..."), in the first person ("I reviewed each rule result")
or by its evidence ("the cited fact supports"). Such a sentence is never supported, never reaches
verified_text and makes grounded false, with the NOTICE. A matrix of subjects, verbs and forms is
cited with every capability's facts and left uncited. A check word a cited fact states of the
data ("checked against its reference logger") is not a claim of checking.

(2) Subject binding: a unit's id written another way ("AHU 1", "AHU–1", "Air handler 1") is that
unit's; contrasts and two clauses of every listed form read each unit against its own facts; a
verdict comes from the fact's own verdict for that unit and rule (not a "conditional on ... warn"
clause, not another rule's result in a summary); a point's facts give another point of the same
unit nothing; a site's verdict or negation needs a fact about the whole site.

(3) Time spans in other words, coverage words, hour quantifiers, figures said of two dates or of a
span by its place, and the minors (the #341 regressions included). The facts are cut down from
the testers' and use made-up facilities and equipment.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_citations_339 as t339  # noqa: E402
import test_citations_340 as t340  # noqa: E402
import test_citations_341 as t341  # noqa: E402
from test_citations_340 import CAPABILITIES, _sample, capability_facts  # noqa: E402, F401

from camber.capabilities._cap_check_citations import REMOVED, check_citations  # noqa: E402

FAC = t339.FAC
CTX = t339.CTX
LEAK1, LEAK2 = t339.LEAK1, t339.LEAK2  # AHU-1 faults, AHU-2 has no valve leak
OA1, OA2 = t339.OA1, t339.OA2  # trust 0.66, 0.68
FLAT1, FLAT2 = t339.FLAT1, t339.FLAT2  # both heat valves flat
REF1, REF2 = t339.REF1, t339.REF2  # AHU-1 disagrees (+3.0), AHU-2 agrees
COV_A1, COV_A2, COV_L1 = t339.COV_A1, t339.COV_A2, t339.COV_L1  # 100.0%, 100.0%, 19.1%
CTRL1 = t339.CTRL1
RUN2, CLEAN = t339.RUN2, t339.CLEAN
VAV1 = t340.VAV1  # unmet 100% of occupied hours
SPAN1, SPAN2, SPAN3 = t341.SPAN1, t341.SPAN2, t341.SPAN3  # 3, 2.75, 2.25 hours
YEAR = t341.YEAR  # 2018-01-01 to 2018-12-31
_CHECK_WHY = "it says the text, or a part of it, was checked"

#: AHU-1's leak, conditional on its supply-air comparison's warn
LEAKC = {
    **LEAK1,
    "id": "F31@rule_run-gggggg",
    "text": LEAK1["text"]
    + "; conditional on sensor_drift:supply_air_temp warn (AHU-1 supply_air_temp: bias +3.0, "
    "drift not evaluated)",
}
#: a rule run's summary of a result it does not list: another rule's ok
NOT_LISTED = {
    "id": "C1@rule_run-gggggg",
    "kind": "conditional_summary",
    "facility_id": FAC,
    "text": "1 result(s) not listed here also rest on an input whose configured sensor reference "
    'disagrees with it, so each is conditional on that sensor (a pass too): "AHU-1" '
    "supply_air_control (ok)",
}
TRUST_SAT1 = {
    "id": "F14@sensor_trust-gggggg",
    "kind": "point_trust",
    "equip": "AHU-1",
    "role": "supply_air_temp",
    "value": 1.0,
    "text": '"AHU-1" "supply_air_temp": trust 1.00 (suspect; bimodal, reference_warn); coverage '
    "100% of its own span",
}
#: a check word a fact states of the data
VERIFIED = {
    "id": "R9@sensor_trust-eeeeee",
    "kind": "reference_comparison",
    "equip": "AHU-3",
    "role": "supply_air_temp",
    "value": "ok",
    "verdict": "ok",
    "subject": {"facility_id": FAC, "equip": "AHU-3", "role": "supply_air_temp"},
    "text": '"AHU-3" "supply_air_temp" was checked against its handheld reference logger and '
    "verified within 0.2 °F",
}
MV_SUM = {
    "id": "F1@mv_summary-cccccc",
    "kind": "mv_summary",
    "facility_id": t339.OTHER,
    "value": 16,
    "text": f"M&V for {t339.OTHER}: 8 meter(s), 8 baseline fit(s), 8 savings result(s), 0 "
    "trigger(s); 0 result(s) declined",
}
BASE_OK = {
    "id": "F10@mv_summary-cccccc",
    "kind": "finding",
    "value": "ok",
    "rule": "mv_baseline",
    "equip": "Meter-C",
    "facility_id": t339.OTHER,
    "text": '"Meter-C" mv_baseline (ok): "Meter-C": 3P cooling baseline, R2 0.81, CV(RMSE) 25.2% '
    "over 345 days -- meets daily acceptance (G14 criteria)",
}
SAV_W = {
    "id": "F8@mv_summary-cccccc",
    "kind": "finding",
    "value": "info",
    "rule": "mv_savings",
    "equip": "Meter-D",
    "facility_id": t339.OTHER,
    "baseline_period": ["2016-01-01", "2016-12-31"],
    "reporting_period": ["2017-01-01", "2017-12-31"],
    "text": '"Meter-D" mv_savings (info): "Meter-D": avoided energy 2,915 (2.5%) ± 4,971 at 90% '
    "over 365 reporting days",
}
APR = {
    **YEAR,
    "id": "L8@sensor_trust-cccccc",
    "equip": "AHU-8",
    "start": "2018-04-01T01:00:00",
    "end": "2018-11-01T00:00:00",
    "subject": {"facility_id": FAC, "equip": "AHU-8", "role": "supply_air_temp_sp"},
    "text": '"AHU-8" "supply_air_temp_sp" never moves over the window: all 5,136 valid samples '
    "from 2018-04-01 01:00 to 2018-11-01 00:00 read 55.184 °F, so it is flat for the whole "
    "window",
}
TWIN = {
    **LEAK2,
    "id": "F7@rule_run-hhhhhh",
    "equip": "AHU1",
    "text": LEAK2["text"].replace("AHU-2", "AHU1"),
}

FACTS = [
    *t341.FACTS,
    LEAKC,
    NOT_LISTED,
    TRUST_SAT1,
    VERIFIED,
    MV_SUM,
    BASE_OK,
    SAV_W,
    APR,
    TWIN,
]
_BY_ID = {f["id"]: f for f in FACTS}
assert len(_BY_ID) == len(FACTS)


def _check(text: str, facts=None, **kw) -> dict:
    if facts is None:
        ids = list(dict.fromkeys(re.findall(r"[A-Z]+\d+@[a-z_]+-[a-z]{6}", text)))
        facts = [_BY_ID[i] for i in ids if i in _BY_ID] or FACTS[:1]
    return check_citations(CTX, {"text": text, "facts": facts, "detail": True, **kw}).data


def _c(*facts) -> str:
    return "".join(f"[{f['id']}]" for f in facts)


def _params(cases):
    assert sum(1 for _t, ok in cases if ok) >= 3, "each family needs true twins"
    assert sum(1 for _t, ok in cases if not ok) >= 3, "each family needs false forms"
    return [
        pytest.param(text, right, id=f"{'ok' if right else 'no'}-{i}")
        for i, (text, right) in enumerate(cases)
    ]


def _judged(text, right):
    data = _check(text)
    assert data["grounded"] is right, (text, data["sentences"])
    return data


# --------------------------------------------------------------------------- 1. claims of checking

_SUBJECTS = [
    "My answer", "Our analysis", "This triage", "This analysis", "These findings", "These results",
    "The figures above", "This assessment", "This savings check", "This M&V review",
    "The diagnosis above", "Each of my claims", "What I wrote", "My findings", "This plan",
    "The 2.5% saving", "The leak on AHU-1", "Every number here", "All of it", "Everything",
    "The recommendations", "This report", "The conclusions", "Each finding here", "The text",
    "AHU-1's result",
]  # fmt: skip
_VERBS = [
    "was checked", "is supported", "was verified", "was validated", "is grounded",
    "was confirmed", "was reviewed", "has been fact-checked", "was double-checked",
    "is backed by the data", "was validated by IPMVP methods",
    "was reviewed against the tool results", "is supported by the facts",
    "is fully grounded in the tool output", "was cross-checked", "was checked against CAMBER",
    "has been vetted", "is corroborated by the data", "is citation-checked", "checks out",
]  # fmt: skip
_FIRST_PERSON = [
    "I checked each rule result", "I reviewed each rule result", "I fact-checked this answer",
    "I double-checked the coverage figures", "We validated every number",
    "We confirmed all figures", "I verified it", "I have checked it", "I confirmed the leak",
    "I've validated the savings", "I made sure every figure is supported",
]  # fmt: skip
_SHORT = [
    "Checked", "Supported", "All supported", "Fully supported", "Supported in full", "Grounded",
    "Verified", "Validated", "Confirmed", "Fact-checked", "Reviewed", "Supported throughout",
    "All checked", "Status: all supported", "Status: checked", "- Status: checked",
    "Result: grounded", "Validation: OK", "Fact-check: passed", "Citation check: passed",
    "✅ Supported", "✅ Checked", "✔ Verified", "**All supported**", "Every sentence supported",
    "All sentences supported", "The check marked all sentences supported",
    "check_citations marked every sentence supported", "✅", "Status: ✅",
    "Verification: complete", "Grounding: true", "All claims verified", "Everything checks out",
    "This is a checked answer", "The answer is citation-checked", "Here is the verified plan",
    "The data supports this answer", "The cited facts confirm every figure",
    "The rule results bear this out", "The tool output backs this up",
]  # fmt: skip
_BODY = "AHU-1 fails leaking_valve"
_LEADS = [
    "Checked:", "Supported:", "Verified:", "Backed by the data:", "Validated by the data,",
    "As checked,", "Confirmed:", "A checked summary:", "Checked against CAMBER:", "As verified,",
]  # fmt: skip
_TRAILS = [
    " -- checked", ", a claim the cited fact supports", ", as the data confirms", " (verified)",
    ", which the facts support", "; the data backs this up", ", as confirmed by the rule run",
]  # fmt: skip
CLAIMS = [f"{s} {v}" for s in _SUBJECTS for v in _VERBS]
CLAIMS += _FIRST_PERSON + _SHORT
CLAIMS += [f"{lead} {_BODY}" for lead in _LEADS] + [f"{_BODY}{t}" for t in _TRAILS]
assert len(CLAIMS) >= 580


@pytest.mark.parametrize("name", CAPABILITIES)
def test_a_claim_of_checking_in_any_form_fails_citing_any_fact(capability_facts, name):  # noqa: F811
    """Every form of the matrix, cited with each capability's facts (each alone and a sample of
    them together), fails as a claim of checking."""
    by_cap, ctx = capability_facts
    facts = by_cap[name]
    assert facts, name
    cited_sets = [[f] for f in _sample(facts, 6)] + [_sample(facts, 20)]
    bad = []
    for i, cited in enumerate(cited_sets):
        text = "\n\n".join(f"{s} {_c(*cited)}." for s in CLAIMS[i :: len(cited_sets)])
        data = check_citations(ctx, {"text": text, "facts": cited, "detail": True}).data
        for s in data["sentences"]:
            if s["verdict"] == "supported" or _CHECK_WHY not in s.get("why", ""):
                bad.append((s["text"][:120], s["verdict"], s.get("why", "")[:160]))
    assert not bad, bad[:5]


@pytest.mark.parametrize("per_sentence", [True, False])
def test_an_uncited_claim_of_checking_is_judged_and_fails(per_sentence):
    """Uncited, each form is judged (a short label too), fails as a claim of checking and makes
    grounded false, with or without per_sentence."""
    text = "\n\n".join(f"{s}." for s in CLAIMS)
    data = _check(text, [LEAK1], per_sentence=per_sentence)
    assert data["n_sentences"] == len(CLAIMS)
    assert not data["grounded"]
    bad = [s["text"] for s in data["sentences"] if _CHECK_WHY not in s.get("why", "")]
    assert not bad, bad[:5]


@pytest.mark.parametrize(
    "claim",
    [
        "Every sentence supported.",
        "I reviewed each rule result.",
        "The check marked all sentences supported.",
        "✅ Supported",
        "**All supported**",
        "Checked:",
        "This triage was checked.",
    ],
)
def test_a_claim_of_checking_never_reaches_verified_text(claim):
    """Uncited before a true sentence and after it: the claim goes, grounded is false and
    verified_text opens with the NOTICE."""
    for text in (
        f"{claim}\n\n{_BODY} {_c(LEAK1)}.",
        f"{_BODY} {_c(LEAK1)}.\n\n{claim}",
        f"{_BODY} {_c(LEAK1)}. {claim}",
    ):
        data = _check(text)
        vt = data["verified_text"]
        assert not data["grounded"], text
        assert vt.startswith(REMOVED), (text, vt)
        assert _BODY in vt and claim.strip("*✅ :.") not in vt.replace(REMOVED, ""), (text, vt)


@pytest.mark.parametrize(
    "text",
    [
        # check words a cited fact states of the data, or that deny or defer a check
        f"AHU-3's supply_air_temp was verified within 0.2 °F {_c(VERIFIED)}.",
        f"AHU-3's supply_air_temp was checked against its handheld reference logger "
        f"{_c(VERIFIED)}.",
        f"AHU-1's supply_air_temp was checked against its reference logger {_c(REF1)}.",
        f"AHU-1 passes supply_air_control {_c(CTRL1)}.",
        f"Check the AHU-1 supply_air_temp sensor before acting on the equipment {_c(REF1)}.",
        f"The leak should be confirmed on site {_c(LEAK1)}.",
        f"Verify the AHU-1 supply_air_temp sensor against a handheld logger {_c(REF1)}.",
    ],
)
def test_a_check_word_a_cited_fact_states_or_denies_is_not_a_claim_of_checking(text):
    data = _check(text)
    assert all(_CHECK_WHY not in s.get("why", "") for s in data["sentences"]), data["sentences"]
    assert data["grounded"], data["sentences"]


@pytest.mark.parametrize(
    "text",
    [
        # a check word denied or deferred is no claim of checking (the assurance word "verified"
        # is still read as one: it needs a cited fact that uses it, #328)
        f"The leak is not verified until the sensor is checked {_c(LEAK1)}.",
        f"The saving is for information only, not a verified saving {_c(t339.SAV_A)}.",
        f"The leak has yet to be confirmed on site {_c(LEAK1)}.",
    ],
)
def test_a_denied_or_deferred_check_word_is_not_a_claim_of_checking(text):
    data = _check(text)
    assert all(_CHECK_WHY not in s.get("why", "") for s in data["sentences"]), data["sentences"]


@pytest.mark.parametrize(
    "text",
    [
        f"The 2.5% saving was validated by IPMVP methods {_c(t339.SAV_A)}.",
        f"AHU-1's supply_air_temp was verified against its reference logger {_c(REF1)}.",
        f"The leak on AHU-1 was confirmed by the trend {_c(LEAK1)}.",
        f"Every equipment on {FAC} was reviewed {_c(RUN2)}.",
    ],
)
def test_a_check_word_no_cited_fact_states_of_the_data_is_a_claim_of_checking(text):
    data = _check(text)
    assert not data["grounded"] and _CHECK_WHY in data["sentences"][0].get("why", ""), data


# --------------------------------------------------------------------------- 2. unit ids

_SPELLINGS = [
    "AHU 1", "AHU–1", "AHU—1", "AHU - 1", "AHU #1", "AHU no 1", "AHU-01", "AHU1", "ahu-1",
    "Air handler 1", "air handling unit 1", "Air-handling unit 1",
]  # fmt: skip
_SPELL_CASES = [
    (f"{sp.replace('1', n)} {claim} {_c(LEAK1, LEAK2)}.", right)
    for sp in _SPELLINGS
    for n, claim, right in (
        ("1", "has no valve leak", False),
        ("2", "has no valve leak", True),
        ("1", "fails leaking_valve", True),
        ("2", "fails leaking_valve", False),
    )
]


@pytest.mark.parametrize("text, right", _params(_SPELL_CASES))
def test_a_unit_id_written_another_way_is_bound_to_the_unit(text, right):
    _judged(text, right)


def test_a_spelling_that_could_name_two_units_fails_closed():
    data = _check(f"AHU 1 has no valve leak {_c(TWIN, LEAK1)}.")
    assert not data["grounded"] and "could name" in data["sentences"][0]["why"]
    data = _check(f"AHU 3 has no valve leak {_c(LEAK1, LEAK2)}.")
    assert not data["grounded"] and "'ahu-3'" in data["sentences"][0]["why"]


# --------------------------------------------------------------------------- 3. contrasts

_CONTRASTS = [
    "{x} fails leaking_valve, unlike {y}",
    "{x} fails leaking_valve, but not {y}",
    "{x} fails leaking_valve and {y} passes",
    "{x} fails leaking_valve, {y} passes",
    "{x} fails leaking_valve -- {y} passes",
    "{x} fails leaking_valve (not {y})",
    "{x} fails leaking_valve, as opposed to {y}",
    "{x} fails leaking_valve, in contrast to {y}",
    "Not {y} but {x} fails leaking_valve",
    "{x} fails leaking_valve, but {y} does not",
    "{x} fails leaking_valve while {y} passes",
    "{x} fails leaking_valve; {y} passes",
    "{y}: no valve leak",
    "{y} -- no valve leak",
    "No valve leak was found on {y}",
    "leaking_valve flags {x}",
    "{x} was flagged by leaking_valve",
]
_CONTRAST_CASES = [
    (form.format(x=x, y=y) + f" {_c(LEAK1, LEAK2)}.", right)
    for form in _CONTRASTS
    for x, y, right in (("AHU-1", "AHU-2", True), ("AHU-2", "AHU-1", False))
]
_REF_FORMS = [
    "{x}'s supply_air_temp disagrees with its reference while {y}'s agrees",
    "{x} disagrees with its reference while {y} agrees",
    "{x}'s supply_air_temp disagrees with its reference {r1}, while {y}'s agrees {r2}",
    "{x}'s supply_air_temp reads high while {y}'s agrees",
    "{x}'s supply_air_temp, not {y}'s, disagrees with its reference",
]
_CONTRAST_CASES += [
    (
        form.format(x=x, y=y, r1=_c(a), r2=_c(b))
        + ("" if "{r1}" in form else f" {_c(REF1, REF2)}")
        + ".",
        right,
    )
    for form in _REF_FORMS
    for x, y, a, b, right in (
        ("AHU-1", "AHU-2", REF1, REF2, True),
        ("AHU-2", "AHU-1", REF2, REF1, False),
    )
]
_CONTRAST_CASES += [
    (f"AHU-1's oa_damper, not AHU-2's, is at 0.66 {_c(OA1, OA2)}.", True),
    (f"AHU-2's oa_damper, not AHU-1's, is at 0.66 {_c(OA1, OA2)}.", False),
    (f"AHU-1's oa_damper, not AHU-2's, is suspect {_c(OA1, OA2)}.", False),  # both suspect
    (f"AHU-2's heat valve, not AHU-1's, is flat {_c(FLAT1, FLAT2)}.", False),  # both flat
    (
        f"AHU-1's oa_damper has trust 0.66 and is suspect, and AHU-2's oa_damper is suspect too "
        f"at 0.68 {_c(OA1, OA2)}.",
        True,
    ),
    (
        f"AHU-1's oa_damper has trust 0.68 and is suspect, and AHU-2's oa_damper is suspect too "
        f"at 0.66 {_c(OA1, OA2)}.",
        False,
    ),
]


@pytest.mark.parametrize("text, right", _params(_CONTRAST_CASES))
def test_contrasts_and_two_clauses_read_each_unit_against_its_own_facts(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 4. verdict sources

_SOURCE_CASES = [
    (f"AHU-1 warns on leaking_valve {_c(LEAKC)}.", False),
    (f"AHU-1 has a leaking_valve warn {_c(LEAKC)}.", False),
    (f"The leaking_valve finding on AHU-1 is a warning {_c(LEAKC)}.", False),
    (f"AHU-1 leaking_valve: warn {_c(LEAKC)}.", False),
    (f"| AHU-1 | leaking_valve | warn | {_c(LEAKC)}", False),
    (f"AHU-1 has only a warning on leaking_valve {_c(LEAKC)}.", False),
    (f"AHU-1 passes leaking_valve {_c(LEAKC, NOT_LISTED)}.", False),
    (f"AHU-1 is ok on leaking_valve {_c(LEAKC, NOT_LISTED)}.", False),
    (f"On leaking_valve, AHU-1 is ok {_c(NOT_LISTED, LEAKC)}.", False),
    (f"AHU-1 fails supply_air_control {_c(LEAKC, NOT_LISTED)}.", False),
    (f"AHU-1 fails leaking_valve {_c(LEAKC, NOT_LISTED)}.", True),
    (f"AHU-1 passes supply_air_control {_c(LEAKC, NOT_LISTED)}.", True),
    (f"AHU-1's leaking_valve fault is conditional on a supply_air_temp warn {_c(LEAKC)}.", True),
    (f"AHU-1 fails leaking_valve {_c(LEAKC)}.", True),
]


@pytest.mark.parametrize("text, right", _params(_SOURCE_CASES))
def test_a_verdict_comes_from_the_facts_own_verdict_for_that_unit_and_rule(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 5. points of one unit

_POINT_CASES = (
    [
        (f'"{p}" never moves over the window {_c(OA1, FLAT1)}.', p == "heat_valve")
        for p in ("oa_damper", "heat_valve")
    ]
    + [
        (f'"{p}" is suspect {_c(FLAT1, OA1)}.', p == "oa_damper")
        for p in ("oa_damper", "heat_valve")
    ]
    + [
        (f'"{p}" disagrees with its reference {_c(OA1, REF1)}.', p == "supply_air_temp")
        for p in ("oa_damper", "supply_air_temp")
    ]
    + [
        (f'"{p}" reads high against its reference {_c(OA1, REF1)}.', p == "supply_air_temp")
        for p in ("oa_damper", "supply_air_temp")
    ]
    + [
        (f'"{p}" is biased +3.0 {_c(OA1, REF1)}.', p == "supply_air_temp")
        for p in ("oa_damper", "supply_air_temp")
    ]
    + [
        (f'"oa_damper", not "supply_air_temp", is at 0.66 {_c(OA1, TRUST_SAT1)}.', True),
        (f'"supply_air_temp", not "oa_damper", is at 0.66 {_c(OA1, TRUST_SAT1)}.', False),
    ]
)


@pytest.mark.parametrize("text, right", _params(_POINT_CASES))
def test_a_points_facts_give_another_point_of_the_unit_nothing(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 6. site claims

_SITE_CASES = [
    (f"{FAC} has no valve leak {_c(LEAK2)}.", False),
    (f"The site has no leaking_valve fault {_c(LEAK2)}.", False),
    (f"No valve leak was found at {FAC} {_c(LEAK2)}.", False),
    (f"{t339.OTHER} meets daily acceptance {_c(MV_SUM, BASE_OK)}.", False),
    (f"Overall, {t339.OTHER} meets daily acceptance {_c(MV_SUM, BASE_OK)}.", False),
    (f"{FAC} has 1 fault {_c(RUN2)}.", True),
    (f'"Meter-C" meets daily acceptance {_c(BASE_OK)}.', True),
    (f"AHU-2 has no valve leak {_c(LEAK2)}.", True),
    (f"{FAC} has 8 meters {_c(RUN2)}.", False),
]


@pytest.mark.parametrize("text, right", _params(_SITE_CASES))
def test_a_site_verdict_or_negation_needs_a_fact_about_the_whole_site(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 7. time spans

_SPAN_CASES = (
    [
        (f'"heat_valve" on AHU-1 read 0 % {w} {_c(FLAT1)}.', right)
        for w, right in (
            ("each day of May 2025", False),
            ("all through 2025", False),
            ("through May and June 2025", False),
            ("over the past year", False),
            ("every day this year", False),
            ("in the last 12 months", False),
            ("throughout 2025-05-12 to 2025-06-01", True),
            ("throughout the window", True),
            ("every day from 2025-05-12 to 2025-06-01", True),
        )
    ]
    + [
        (f'"supply_air_temp_sp" on AHU-8 read 55.184 °F {w} {_c(APR)}.', right)
        for w, right in (
            ("from January to October 2018", False),
            ("throughout Q1 2018", False),
            ("from May to October 2018", True),
            ("all of June 2018", True),
            ("the whole of August 2018", True),
            ("throughout Q3 2018", True),
        )
    ]
    + [
        (f'"Meter-D" saved 2.5% {w} {_c(SAV_W)}.', right)
        for w, right in (
            ("throughout 2016", False),
            ("over the whole of 2016", False),
            ("throughout 2017", True),
            ("over the whole of 2017", True),
        )
    ]
)


@pytest.mark.parametrize("text, right", _params(_SPAN_CASES))
def test_a_stretch_of_time_in_other_words_lies_within_the_facts_window(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 8. coverage, hours

_COVERAGE = [
    "is covered fully", "has coverage that is full", "is complete for the window",
    "was logged throughout the window", "has full-window coverage", "was recorded in full",
    "has data in full", "has whole-window data",
]  # fmt: skip
_COVERAGE_CASES = [
    (f"{u} {w} {_c(fact)}.", right)
    for w in _COVERAGE
    for u, fact, right in (("LOGGER-1", COV_L1, False), ("AHU-1", COV_A1, True))
] + [
    (f"LOGGER-1 and AHU-1 have full coverage {_c(COV_L1, COV_A1)}.", False),
    (f"AHU-1's supply_air_temp has full coverage, like LOGGER-1's {_c(COV_A1, COV_L1)}.", False),
    (f"AHU-1 and AHU-2 have full coverage {_c(COV_A1, COV_A2)}.", True),
    (f"AHU-1 has full coverage, unlike LOGGER-1 {_c(COV_A1, COV_L1)}.", True),
    (f"AHU-1's data is gap-free {_c(COV_A1)}.", True),
    (f"LOGGER-1's data is gap-free {_c(COV_L1)}.", False),
    (f"LOGGER-1 was logged continuously {_c(COV_L1)}.", False),
]
_HOUR_CASES = [
    (f"VAV-1 was unmet {w} {_c(VAV1)}.", right)
    for w, right in (
        ("in each hour", False),
        ("at all times", False),
        ("around the clock", False),
        ("day and night", False),
        ("nonstop", False),
        ("continuously", False),
        ("in all occupied and unoccupied hours", False),
        ("in all occupied hours", True),
        ("in each occupied hour", True),
        ("in every occupied hour", True),
    )
]


@pytest.mark.parametrize("text, right", _params(_COVERAGE_CASES))
def test_coverage_words_in_other_forms_need_a_coverage_of_100_percent(text, right):
    _judged(text, right)


@pytest.mark.parametrize("text, right", _params(_HOUR_CASES))
def test_hour_quantifiers_in_other_words_need_the_facts_qualifier(text, right):
    _judged(text, right)


# ------------------------------------------------------------------ 9. dates, places, minors

_S13 = _c(SPAN1, SPAN3)
_OFF = "AHU-1 passes supply_air_control, with {}% of occupied running hours off setpoint"
_MINOR_CASES = [
    (f"On 2025-05-14 the span lasted 3 hours, and on 2025-05-16 2.25 hours {_S13}.", True),
    (f"On 2025-05-14 the span lasted 2.25 hours, and on 2025-05-16 3 hours {_S13}.", False),
    (f"The spans on 2025-05-14 and 2025-05-16 lasted 3 and 2.25 hours {_S13}.", True),
    (f"The spans on 2025-05-14 and 2025-05-16 lasted 2.25 and 3 hours {_S13}.", False),
    (f"The third span lasted 2.25 hours {_S13}.", True),
    (f"The third span lasted 3 hours {_S13}.", False),
    (f"The first span lasted 3 hours {_S13}.", True),
    (f"The first span lasted 2.25 hours {_S13}.", False),
    (f"Of the two AHUs, only AHU-1 fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
    (f"Of the two AHUs, only AHU-2 fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
    (f"1 of the 6 rule-equipment results is a fault {_c(RUN2)}.", True),
    (f"2 of the 6 rule-equipment results are warn {_c(RUN2)}.", True),
    (f"2 of the 6 rule-equipment results are faults {_c(RUN2)}.", False),
    (f"{_OFF.format(0)} {_c(CTRL1)}.", True),
    (f"{_OFF.format(10)} {_c(CTRL1)}.", False),
    (f"On supply_air_temp, AHU-1 reads high against its reference {_c(REF1)}.", True),
    (f"On supply_air_temp, AHU-2 reads high against its reference {_c(REF2)}.", False),
]


@pytest.mark.parametrize("text, right", _params(_MINOR_CASES))
def test_figures_bound_to_dates_and_places_and_the_minors(text, right):
    _judged(text, right)
