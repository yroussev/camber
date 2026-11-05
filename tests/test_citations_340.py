"""What the 0.112 acceptance test, round 6, found check_citations still judging wrong (#340), as
generated families rather than single sentences.

The principle first: a quantifier, verdict or completeness word in a sentence ("all", "every",
"most", "complete", "whole") is supported only by what a cited fact states of the sentence's own
subject. A fact's incidental wording ("6 rule-equipment result(s) in all", "flat for the whole
window") supports no "every sensor", "the complete answer" or NOTICE of completeness. The first
matrix runs every capability on a small synthetic site and cites each of its facts (alone and
together) for a set of forged completeness and "all X were Y" sentences: every one must fail.

Then the families, each both ways (the false form fails, its true twin passes): names joined by a
comma, "&", "/", "plus", "as well as", "along with", "together with", "(like B)", "likewise",
"also does", "the same goes for" and before "--"; negated sets of two or more names; figure lists
matched to their items in order ("respectively", "A (x) and B (y)", "x on A and y on B", names
after the verb), with each item's own sign; "leaks" said of equipment against "no valve leak"; a
plural noun or a bare "Both" before a colon, a dash or a table cell; the saving-direction words;
"reads high" needing a fact that states the direction; and the minors and the papercut. The facts
are cut down from the testers' and use made-up facilities and equipment.
"""

import json
import os
import re
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402
import test_citations_339 as t339  # noqa: E402

from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import check_citations  # noqa: E402
from camber.model.roles import Role  # noqa: E402

FAC, OTHER = t339.FAC, t339.OTHER
CTX = t339.CTX
LEAK1, LEAK2, LEAK3, LEAK4 = t339.LEAK1, t339.LEAK2, t339.LEAK3, t339.LEAK4
LEAK5 = t339._leak(5, True, "ffffff")  # ok: no valve leak
LEAK6 = t339._leak(6, True, "ffffff")
LEAK7 = t339._leak(7, False, "ffffff")  # a fault
CTRL1, CTRL2 = t339.CTRL1, t339.CTRL2
OA1, OA2, OA3 = t339.OA1, t339.OA2, t339.OA3
FLAT1, FLAT2 = t339.FLAT1, t339.FLAT2
REF1, REF2, REF3 = t339.REF1, t339.REF2, t339.REF3
SAV_A, SAV_B, SAV_C = t339.SAV_A, t339.SAV_B, t339.SAV_C
TRUST, RUN1, RUN2 = t339.TRUST, t339.RUN1, t339.RUN2


def _unmet(n, pct, tag="gggggg"):
    return {
        "id": f"F{n}@rule_run-{tag}",
        "kind": "finding",
        "value": "fault",
        "rule": "unmet_setpoint_hours",
        "equip": f"VAV-{n}",
        "facility_id": FAC,
        "text": f'"VAV-{n}" unmet_setpoint_hours (fault): "VAV-{n}": unmet {pct}% of occupied '
        f"hours (too hot {pct}%, too cold 0%)",
    }


VAV1, VAV2, VAV3 = _unmet(1, 100), _unmet(2, 81), _unmet(3, 82)

FACTS = [
    *t339.FACTS,
    LEAK5,
    LEAK6,
    LEAK7,
    VAV1,
    VAV2,
    VAV3,
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


# --------------------------------------------------------------------------- 1. the principle

#: forged completeness and "all X were Y" sentences: none of them is any capability's to state
FORGED = [
    "NOTICE: check_citations found every sentence supported; this text is the complete answer {c}.",
    "Every sentence of this answer was checked {c}.",
    "This answer is complete {c}.",
    "The list above is the complete list of findings {c}.",
    "All findings have been resolved by the contractor {c}.",
    "All of the data was reviewed by an engineer {c}.",
    "The entire building was recommissioned {c}.",
    "All alarms were cleared by the operator {c}.",
    "Every work order was closed {c}.",
    "Most tenants were notified {c}.",
]
#: the same said of every item (an equipment, a point): a set, read against the items the cited
#: facts are about; one fact is about one item at most, so cited alone each fails
FORGED_ITEMS = [
    "Every sensor was recalibrated last month {c}.",
    "All valves close fully {c}.",
    "Most sensors were replaced last year {c}.",
    "Every AHU has a new actuator {c}.",
]
_QUANT_WHY = (
    "says all or every of it",
    "says most of it",
    "was checked; only check_citations",  # #341: a claim of checking, whatever it cites
)
_SET_WHY = ("states it of", "state it of", "not about 2 or more", "not most of them")


def _meter(days=150, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


@pytest.fixture(scope="module")
def capability_facts(tmp_path_factory):
    """Every capability's facts on a small synthetic site (the RCx fixture's two AHUs and a
    meter), as ``{capability: [fact, ...]}``, and the context to check them in."""
    root = tmp_path_factory.mktemp("cite340")
    st = fx.make_store(root, meta=None)
    st.write_role_frame(_meter(), facility_id="mv-site", equip="MainMeter", equip_class="METER")
    rcx = os.path.join(str(root), "rcx.json")
    with open(rcx, "w", encoding="utf-8") as fh:
        json.dump(
            fx.config(oat_reference={"fetch": "auto", "latitude": 40.0, "longitude": -75.0}), fh
        )
    mv = os.path.join(str(root), "mv.json")
    with open(mv, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "source": {"kind": "store", "store": "store", "facility_id": "mv-site"},
                "equipment": [{"class": "METER"}],
                "mv": [
                    {
                        "class": "METER",
                        "role": "power",
                        "period": ["2024-01-01", "2024-03-31"],
                        "reporting_period": ["2024-04-01", None],
                    }
                ],
            },
            fh,
        )
    st.points()
    allowed = frozenset({fx.FID, "mv-site"})
    calls = [
        ("facilities", {}, None),
        ("points", {"facility_id": fx.FID}, None),
        ("coverage", {"facility_id": fx.FID}, None),
        ("sensor_trust", {"facility_id": fx.FID}, None),
        ("rule_run", {"facility_id": fx.FID}, None),
        (
            "finding_detail",
            {"facility_id": fx.FID, "finding_id": "simultaneous_heat_cool@DemoAHU2"},
            None,
        ),
        ("rcx_report", {"facility_id": fx.FID, "top_n": 5}, rcx),
        ("mv_summary", {"facility_id": "mv-site"}, mv),
        ("drift", {"facility_id": fx.FID, "baseline": ["2026-03-02", "2026-03-15"]}, None),
        ("dataset_search", {"query": "sensor"}, None),
        ("dataset_entry", {"dataset_id": "at-30bldg-sensors"}, None),
        ("rule_params", {"rules": ["economizer_high_limit", "leaking_valve"]}, None),
    ]
    reg = default_registry()
    out: dict = {}
    for name, args, cfg in calls:
        ctx = CallContext(store=st.root, allowed_facilities=allowed, config=cfg)
        out[name] = list(reg.call(name, args, ctx).facts)
    check_ctx = SimpleNamespace(allowed_facilities=allowed, store=st.root)
    some = [f for fs in out.values() for f in fs[:1]]
    out["check_citations"] = list(
        check_citations(check_ctx, {"text": "A sentence [x].", "facts": some}).facts
    )
    return out, check_ctx


def _sample(facts, k=25):
    """Up to ``k`` of ``facts``, spread over the list (the first, the last and between)."""
    if len(facts) <= k:
        return list(facts)
    step = (len(facts) - 1) / (k - 1)
    return [facts[round(i * step)] for i in range(k)]


CAPABILITIES = [
    "facilities",
    "points",
    "coverage",
    "sensor_trust",
    "rule_run",
    "finding_detail",
    "rcx_report",
    "mv_summary",
    "drift",
    "dataset_search",
    "dataset_entry",
    "rule_params",
    "check_citations",
]


@pytest.mark.parametrize("name", CAPABILITIES)
def test_no_capability_fact_supports_a_forged_quantifier_or_completeness_claim(
    capability_facts, name
):
    """Each capability's facts, each cited alone and all cited together, against every forged
    sentence: none is supported, and the why names the quantifier or completeness word."""
    by_cap, ctx = capability_facts
    facts = by_cap[name]
    assert facts, name
    alone = [[f] for f in _sample(facts)]
    tries = [(t, cited, _QUANT_WHY) for t in FORGED for cited in alone + [_sample(facts, 40)]]
    tries += [(t, cited, _QUANT_WHY + _SET_WHY) for t in FORGED_ITEMS for cited in alone]
    bad = []
    for template, cited, whys in tries:
        text = template.format(c="".join(f"[{f['id']}]" for f in cited))
        data = check_citations(ctx, {"text": text, "facts": cited, "detail": True}).data
        s = data["sentences"][-1]
        why = s.get("why", "")
        if data["grounded"] or s["verdict"] == "supported" or not any(w in why for w in whys):
            bad.append((text[:160], s["verdict"], why[:200]))
    assert not bad, bad[:5]


FORGED_ON_THESE = [
    (template, fact)
    for template in FORGED + FORGED_ITEMS
    for fact in (RUN1, RUN2, t339.RUN14, TRUST, FLAT1, t339.COV_A1, SAV_A, REF1, LEAK1, VAV1)
]


@pytest.mark.parametrize("template, fact", FORGED_ON_THESE)
def test_a_facts_own_all_or_whole_supports_no_other_subject(template, fact):
    """ "6 rule-equipment result(s) in all" and "flat for the whole window" say all of their own
    things only: a forged sentence citing them fails, naming the word (or, for a set of items,
    the item it is not stated of)."""
    data = _check(template.format(c=_c(fact)), [fact])
    s = data["sentences"][-1]
    whys = _QUANT_WHY + (_SET_WHY if template in FORGED_ITEMS else ())
    assert not data["grounded"] and any(w in s.get("why", "") for w in whys), s


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # what a fact says all, every or whole of, said of the same thing
            (f"All 2,016 valid samples read 0 % {_c(FLAT1)}.", True),
            (f"All samples of the AHU-1 heat_valve read 0 % {_c(FLAT1)}.", True),
            (f"The AHU-1 heat_valve is flat for the whole window {_c(FLAT1)}.", True),
            (f"The AHU-1 heat_valve is flat for the entire window {_c(FLAT1)}.", True),
            (f"All 3 rules on {FAC} returned a result {_c(RUN1)}.", True),
            (f"Most of the 11 points on {FAC} are trusted {_c(TRUST)}.", True),
            # and of another thing
            (f"All findings on {FAC} were confirmed on site {_c(RUN2)}.", False),
            (f"Every rule on {FAC} follows G36 {_c(RUN2)}.", False),
            (f"Every sensor on {FAC} was recalibrated {_c(FLAT1)}.", False),
            (f"The AHU-1 heat_valve answer is complete {_c(FLAT1)}.", False),
            (f"Most points on {FAC} are suspect {_c(TRUST)}.", False),
            (f"Most points on {FAC} are suspect {_c(TRUST, VAV1)}.", False),
            (f"The whole site is commissioned {_c(FLAT1)}.", False),
        ]
    ),
)
def test_quantifiers_are_read_of_their_own_noun(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 2. joined names

#: ways of joining two names ``a`` and ``b`` as one subject of "fail leaking_valve"
JOINS = [
    "{a}, {b} fail leaking_valve",
    "{a}, {b} both fail leaking_valve",
    "{a} & {b} fail leaking_valve",
    "Both {a} & {b} fail leaking_valve",
    "{a}/{b} fail leaking_valve",
    "{a} plus {b} fail leaking_valve",
    "{a}, as well as {b}, fails leaking_valve",
    "{a} along with {b} fails leaking_valve",
    "Together with {b}, {a} fails leaking_valve",
    "{a} (like {b}) fails leaking_valve",
    "Like {b}, {a} fails leaking_valve",
    "{a} fails leaking_valve, and likewise {b}",
    "{a} fails leaking_valve, and {b} also does",
    "{a} fails leaking_valve; the same goes for {b}",
    "{a} fails leaking_valve, as does {b}",
    "{a} and {b} -- leaking_valve fault",
    "{a}, {b} -- leaking_valve fault",
    "- {a}, {b} -- leaking_valve fault",
    "1. {a} and {b} -- fault on leaking_valve",
]


@pytest.mark.parametrize("join", JOINS)
@pytest.mark.parametrize("right", [True, False])
def test_names_joined_in_any_way_are_a_set(join, right):
    """A joined pair fails when one of them passes (AHU-2: no valve leak) and passes when both
    fail (AHU-1 and AHU-3)."""
    other = LEAK3 if right else LEAK2
    b = other["equip"]
    text = join.format(a="AHU-1", b=b) + f" {_c(LEAK1, other)}"
    data = _judged(text, right)
    if not right:
        assert "ahu-2" in json.dumps(data["sentences"]).lower(), data["sentences"]


NEGATED = [
    "None of {a}, {b} fails leaking_valve",
    "Neither of {a}, {b} fails leaking_valve",
    "None of {a}, {b} and {c} fails leaking_valve",
    "None of {a}, {b}, {c} fails leaking_valve",
    "Not one of {a}, {b} and {c} fails leaking_valve",
    "Neither {a} nor {b} fails leaking_valve",
]


@pytest.mark.parametrize("form", NEGATED)
@pytest.mark.parametrize("right", [True, False])
def test_negated_sets_of_two_or_more_names(form, right):
    """ "None of A, B and C fails" against faults fails; against three passes it holds."""
    facts = (LEAK2, LEAK4, LEAK5) if right else (LEAK1, LEAK3, LEAK7)
    names = [f["equip"] for f in facts]
    text = form.format(a=names[0], b=names[1], c=names[2])
    cited = facts if "{c}" in form else facts[:2]
    _judged(f"{text} {_c(*cited)}.", right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"AHU-1, AHU-3 and AHU-7 all fail leaking_valve {_c(LEAK1, LEAK3, LEAK7)}.", True),
            (f"AHU-1, AHU-3 and AHU-7 each fail leaking_valve {_c(LEAK1, LEAK3, LEAK7)}.", True),
            (f"AHU-1 and AHU-3 all fail leaking_valve {_c(LEAK1, LEAK3)}.", True),
            (f"AHU-1, AHU-2 and AHU-7 all fail leaking_valve {_c(LEAK1, LEAK2, LEAK7)}.", False),
            (f"AHU-1, AHU-2 and AHU-3 each fail leaking_valve {_c(LEAK1, LEAK2, LEAK3)}.", False),
            (f"AHU-2 and AHU-3 all fail leaking_valve {_c(LEAK2, LEAK3)}.", False),
        ]
    ),
)
def test_names_followed_by_all_are_the_sets(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 3. figure lists

#: (template, the figures in the items' order, swapped): each item's own figure
LISTS = [
    (
        "AHU-1 and AHU-2 have an oa_damper trust of {x} and {y} respectively",
        (OA1, OA2),
        ("0.66", "0.68"),
    ),
    ("AHU-1 and AHU-2 have an oa_damper trust of {x} and {y}", (OA1, OA2), ("0.66", "0.68")),
    (
        "The oa_damper points on AHU-1 and AHU-2 have trusts of {x} and {y} respectively",
        (OA1, OA2),
        ("0.66", "0.68"),
    ),
    ("AHU-1 ({x}) and AHU-2 ({y}) have suspect oa_damper points", (OA1, OA2), ("0.66", "0.68")),
    ("The oa_damper trust is {x} on AHU-1 and {y} on AHU-2", (OA1, OA2), ("0.66", "0.68")),
    ("oa_damper trust: AHU-1 {x}, AHU-2 {y}", (OA1, OA2), ("0.66", "0.68")),
    (
        '"Meter-A" and "Meter-B" have avoided energy of {x} and {y} respectively',
        (SAV_A, SAV_B),
        ("2.5%", "-15.6%"),
    ),
    (
        '"Meter-A" and "Meter-B" have avoided energy of {x} and {y}',
        (SAV_A, SAV_B),
        ("2.5%", "-15.6%"),
    ),
    ('The saving is {x} on "Meter-A" and {y} on "Meter-B"', (SAV_A, SAV_B), ("2.5%", "-15.6%")),
    (
        "VAV-1, VAV-2 and VAV-3 are unmet {x}, {y} and 82% of occupied hours respectively",
        (VAV1, VAV2, VAV3),
        ("100%", "81%"),
    ),
    (
        "Unmet occupied hours were {x} for VAV-1, {y} for VAV-2 and 82% for VAV-3",
        (VAV1, VAV2, VAV3),
        ("100%", "81%"),
    ),
    (
        "The two oa_damper points score {x} and {y} respectively",
        (OA1, OA2),
        ("0.66", "0.68"),
    ),
]


@pytest.mark.parametrize("template, facts, figs", LISTS, ids=lambda v: str(v)[:30])
@pytest.mark.parametrize("swap", [False, True])
def test_figure_lists_are_matched_to_their_items_in_order(template, facts, figs, swap):
    """In order it passes (mixed signs too: 2.5% and -15.6%); swapped it fails."""
    x, y = (figs[1], figs[0]) if swap else figs
    _judged(template.format(x=x, y=y) + f" {_c(*facts)}.", not swap)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # a figure said of every name after the verb
            (
                f"Occupied hours were unmet 100% on VAV-1, VAV-2 and VAV-3 {_c(VAV1, VAV2, VAV3)}.",
                False,
            ),
            (f"The oa_damper trust is 0.66 for AHU-1 and AHU-2 {_c(OA1, OA2)}.", False),
            (f"The oa_damper trust is 0.66 for AHU-1 and AHU-3 {_c(OA1, OA3)}.", True),
            (f"The heat_valve reads 0 % on AHU-1 and AHU-2 {_c(FLAT1, FLAT2)}.", True),
            # a list said "respectively" of items in no order the sentence gives: by citations
            (f"The two oa_damper points score 0.68 and 0.66 respectively {_c(OA2, OA1)}.", True),
            (f"Both meters saved energy, 2.5% and 2.5% respectively {_c(SAV_A, SAV_C)}.", True),
            # a list said "respectively" with the wrong count of figures
            (
                f"AHU-1 and AHU-2 have an oa_damper trust of 0.66 respectively {_c(OA1, OA2)}.",
                False,
            ),
        ]
    ),
)
def test_figures_after_the_verb_and_respectively(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 4. leaks

LEAKS = [
    "{a} leaks",
    "{a} is leaking",
    "{a} shows a leak",
    "{a} has a leak",
]
LEAK_SETS = [
    "Each AHU leaks",
    "The AHUs leak",
    "Both AHUs leak",
    "Both AHUs are leaking",
    "Both AHUs show a leak",
    "AHU-1 and {b} leak",
    "Both leak",
]


@pytest.mark.parametrize("form", LEAKS)
@pytest.mark.parametrize("right", [True, False])
def test_a_leak_of_equipment_against_no_valve_leak(form, right):
    fact = LEAK1 if right else LEAK2
    _judged(form.format(a=fact["equip"]) + f" {_c(fact)}.", right)


@pytest.mark.parametrize("form", LEAK_SETS)
@pytest.mark.parametrize("right", [True, False])
def test_a_set_said_to_leak(form, right):
    other = LEAK3 if right else LEAK2
    _judged(form.format(b=other["equip"]) + f" {_c(LEAK1, other)}.", right)


@pytest.mark.parametrize(
    "text, right",
    [
        (f"AHU-2's damper leaks {_c(LEAK2)}.", True),  # another part: not read
        (f"AHU-1 leaks {_c(LEAK1, LEAK2)}.", True),  # AHU-2's "no valve leak" is not AHU-1's
        (f"AHU-2 has no valve leak {_c(LEAK2)}.", True),
        (f"AHU-2 leaks {_c(LEAK1, LEAK2)}.", False),
    ],
)
def test_a_leak_is_read_of_the_equipment_it_names(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 5. lead-ins

LEADS = [
    "The AHUs: fault",
    "The AHUs: faulted",
    "- The AHUs: faulted",
    "The AHUs - fault",
    "The AHUs -- fault",
    "The air handlers: faulted",
    "Both: faulted",
    "| AHUs | fault |",
    "| The AHUs | faulted |",
]


@pytest.mark.parametrize("form", LEADS)
@pytest.mark.parametrize("right", [True, False])
def test_a_plural_or_bare_both_before_a_lead_is_a_set(form, right):
    other = LEAK3 if right else LEAK2
    cites = _c(LEAK1, other)
    text = form[:-1] + f" {cites} |" if form.endswith("|") else f"{form} {cites}"
    _judged(text, right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f'The AHUs\' "supply_air_temp" points: both disagree {_c(REF1, REF3)}', True),
            (f'The AHUs\' "supply_air_temp" points: both disagree {_c(REF1, REF2)}', False),
            (f'The "supply_air_temp" points: both disagree {_c(REF1, REF3)}', True),
            (f'The "supply_air_temp" points: both disagree {_c(REF1, REF2)}', False),
            (f"Both disagree with their reference {_c(REF1, REF3)}.", True),
            (f"Both disagree with their reference {_c(REF1, REF2)}.", False),
        ]
    ),
)
def test_possessive_lead_ins(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 6. saving direction

SAVING = [
    '"{m}" shows a saving',
    '"{m}" saved energy',
    '"{m}" cut its energy use',
    '"{m}" lowered its energy use',
    '"{m}" consumed less energy',
    '"{m}" used less energy',
    '"{m}" reduced its energy use',
    '"{m}" used less energy than its baseline',
]
INCREASE = [
    '"{m}" used more energy',
    '"{m}" used more energy than its baseline',
    '"{m}" increased its energy use',
    '"{m}" did not save energy',
]


@pytest.mark.parametrize("form", SAVING)
@pytest.mark.parametrize("right", [True, False])
def test_a_saving_word_follows_the_sign(form, right):
    fact = SAV_A if right else SAV_B
    _judged(form.format(m=fact["equip"]) + f" {_c(fact)}.", right)


@pytest.mark.parametrize("form", INCREASE)
@pytest.mark.parametrize("right", [True, False])
def test_an_increase_word_follows_the_sign(form, right):
    fact = SAV_B if right else SAV_A
    _judged(form.format(m=fact["equip"]) + f" {_c(fact)}.", right)


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            (f"Both meters show savings {_c(SAV_A, SAV_C)}.", True),
            (f"Both meters show savings {_c(SAV_A, SAV_B)}.", False),
            (f'"Meter-B" used 15.6% more energy than its baseline {_c(SAV_B)}.', True),
            (f'"Meter-A" used 2.5% more energy than its baseline {_c(SAV_A)}.', False),
            (f'"Meter-A" used 2.5% less energy than its baseline {_c(SAV_A)}.', True),
            (f'The saving for "Meter-B" is for information only {_c(SAV_B)}.', True),
            (f'"Meter-B" achieved a saving {_c(SAV_B)}.', False),
        ]
    ),
)
def test_savings_said_of_sets_and_with_figures(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- 7. reads high


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # a direction a cited fact states (a signed bias states it too)
            (f'The "supply_air_temp" on AHU-1 reads high against its reference {_c(REF1)}.', True),
            (f'The "supply_air_temp" on AHU-3 reads high {_c(REF3)}.', True),
            (f'The "supply_air_temp" on AHU-1 is biased high {_c(REF1)}.', True),
            # a point whose fact states none, or the opposite
            (f'The "oa_damper" on AHU-1 reads high against its reference {_c(OA1)}.', False),
            (f'The "supply_air_temp" on AHU-2 reads high {_c(REF2)}.', False),
            (f'The "supply_air_temp" on AHU-1 reads low {_c(REF1)}.', False),
            (
                f'The "supply_air_temp" and "oa_damper" points on AHU-1 both read high '
                f"{_c(REF1, OA1)}.",
                False,
            ),
        ]
    ),
)
def test_a_reading_direction_needs_a_fact_that_states_it(text, right):
    _judged(text, right)


# --------------------------------------------------------------------------- minors


@pytest.mark.parametrize(
    "text, right",
    _both_ways(
        [
            # rule_run's wording: results, counted after a lead-in
            (
                f"Of the 6 rule-equipment results, 1 is a fault, 2 are warn and 3 are ok "
                f"{_c(RUN2)}.",
                True,
            ),
            (f"Of the 6 results, 1 is a fault, 2 are warn and 3 are ok {_c(RUN2)}.", True),
            (f"The 3 rules returned 6 results in all: 1 fault, 2 warn and 3 ok {_c(RUN2)}.", True),
            (f"Of the 6 results, 2 are a fault, 1 is warn and 3 are ok {_c(RUN2)}.", False),
            (f"The 3 rules returned 5 results in all: 1 fault, 2 warn and 3 ok {_c(RUN2)}.", False),
            # "most" of a set of items: more than half of them
            (f"Most AHUs on {FAC} fail leaking_valve {_c(LEAK1, LEAK3, LEAK2)}.", True),
            (f"Most AHUs on {FAC} fail leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"The majority of the AHUs fail leaking_valve {_c(LEAK1, LEAK2, LEAK4)}.", False),
            # "the rest" as two labels together
            (
                f"9 points on {FAC} are trusted and the rest are suspect or untrusted {_c(TRUST)}.",
                True,
            ),
            (f"9 points on {FAC} are trusted and the rest are untrusted {_c(TRUST)}.", False),
            # one of the items the cited facts are about
            (f"One of the two AHUs fails leaking_valve {_c(LEAK1, LEAK2)}.", True),
            (
                f"Of the two AHUs, AHU-1 fails leaking_valve and AHU-2 passes {_c(LEAK1, LEAK2)}.",
                True,
            ),
            (f"One of the three AHUs fails leaking_valve {_c(LEAK1, LEAK2)}.", False),
            (f"Two AHUs fail leaking_valve {_c(LEAK1, LEAK2)}.", False),
            # a set inside a relative clause
            (
                f"The AHUs, both of which fail leaking_valve, pass supply_air_control "
                f"{_c(LEAK1, LEAK2, CTRL1, CTRL2)}.",
                False,
            ),
            (
                f"The AHUs, both of which pass supply_air_control, have a flat heat_valve "
                f"{_c(CTRL1, CTRL2, FLAT1, FLAT2)}.",
                True,
            ),
            # a share in parentheses is of what the share before it is of
            (f"VAV-2 is too cold 0% of occupied hours {_c(VAV2)}.", True),
            (f"VAV-2 is too cold 0% of occupied fan-on samples {_c(VAV2)}.", False),
        ]
    ),
)
def test_minors(text, right):
    _judged(text, right)


def test_a_number_the_why_explains_without_a_threshold_aside():
    data = _check(f"Two AHUs on {FAC} fail leaking_valve {_c(LEAK1, LEAK2)}.")
    assert not data["grounded"]
    why = json.dumps(data["sentences"])
    assert "threshold" not in why, why


# --------------------------------------------------------------------------- papercut


@pytest.mark.parametrize(
    "header",
    ["| Meter | Avoided energy |", "| Unit | Saving |", "| Meter | Savings (vs baseline) |"],
)
def test_a_table_header_row_is_kept_and_not_judged(header):
    text = (
        f"{header}\n|---|---|\n"
        f'| "Meter-A" | 2,915 (2.5%) {_c(SAV_A)} |\n'
        f'| "Meter-B" | -293,693 (-15.6%) {_c(SAV_B)} |'
    )
    data = _check(text)
    assert data["grounded"], data["sentences"]
    assert data["verified_text"].splitlines()[0] == header


@pytest.mark.parametrize(
    "rows, right",
    [
        ('| "Meter-A" | -15.6% {a} |\n| "Meter-B" | 2.5% {b} |', False),
        ('| "Meter-A" | 2.5% {a} |\n| "Meter-B" | -15.6% {b} |', True),
    ],
)
def test_a_table_row_is_read_with_its_header(rows, right):
    body = rows.format(a=_c(SAV_A, SAV_B), b=_c(SAV_A, SAV_B))
    data = _check(f"| Meter | Avoided energy |\n|---|---|\n{body}")
    assert data["grounded"] is right, data["sentences"]
