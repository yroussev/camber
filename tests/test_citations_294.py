"""What a "supported" sentence must satisfy (0.112, #294 part 2) and how verified_text keeps the
draft's lines (#322 item 3).

Every case the 0.111 acceptance test found wrongly supported or wrongly rejected, positive and
negative, plus sentences of the blind testers' real triage drafts (their facts, cut down).
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.agent.verify import _judge, check, extract_cites  # noqa: E402
from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._cap_check_citations import (  # noqa: E402
    REMOVED,
    _context,
    check_citations,
)

REG = default_registry()

F1 = {
    "id": "F1@rule_run-aaaaaa",
    "kind": "rule_run",
    "facility_id": "fac-a",
    "value": 2,
    "counts": {"fault": 4, "warn": 0, "info": 0, "ok": 2},
    "text": "2 rules were run on 3 equipment of fac-a and 2 of them gave findings: 4 fault, 0 "
    "warn, 0 info and 2 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for missing inputs",
}
F2 = {
    "id": "F2@rule_run-aaaaaa",
    "kind": "finding",
    "value": "fault",
    "rule": "free_cooling_missed",
    "equip": "AHU__excess_oa",
    "finding_id": "free_cooling_missed@AHU__excess_oa",
    "text": '"AHU__excess_oa" free_cooling_missed (fault): "AHU__excess_oa": mechanical cooling '
    "(valve > 5%) ran 46% of the 64 free-cooling hours (OAT < 60°F)",
    "metrics": {"cmd_open_pct": 90, "pct": 46.2},
}
F3 = {
    "id": "F3@rule_run-aaaaaa",
    "kind": "finding",
    "value": "fault",
    "rule": "outdoor_air_fraction",
    "equip": "AHU__excess_oa",
    "finding_id": "outdoor_air_fraction@AHU__excess_oa",
    "text": '"AHU__excess_oa" outdoor_air_fraction (fault): "AHU__excess_oa": OAF median 45% (45% '
    "in cooling weather); excess OA 100% of cooling hours above 20% min",
    "metrics": {"oaf_median_pct": 44.9},
}
F4 = {
    "id": "F4@rule_run-aaaaaa",
    "kind": "finding",
    "value": "ok",
    "rule": "free_cooling_missed",
    "equip": "AHU__economizer",
    "finding_id": "free_cooling_missed@AHU__economizer",
    "text": '"AHU__economizer" free_cooling_missed (ok): no missed free cooling',
}
MV_BASE = {
    "id": "F2@mv_summary-bbbbbb",
    "kind": "finding",
    "value": "fault",
    "rule": "mv_baseline",
    "equip": "meter_A",
    "text": '"meter_A" mv_baseline (fault): 4P baseline CV(RMSE) 25.9%, does not meet G14 '
    "acceptance",
}
MV_A = {
    "id": "F3@mv_summary-bbbbbb",
    "kind": "finding",
    "value": "info",
    "rule": "mv_savings",
    "equip": "meter_A",
    "text": '"meter_A" mv_savings (info): "meter_A": avoided energy 47,980 (3.2%) ± 74,469 at 90% '
    "over 364 reporting days; energy in the meter's own units; the baseline does not meet "
    "G14 acceptance, so this saving is for information only",
    "metrics": {"reporting_period": "2017-01-01 to 2017-12-31", "fsu": 1.5521},
}
MV_B = {
    "id": "F5@mv_summary-bbbbbb",
    "kind": "finding",
    "value": "info",
    "rule": "mv_savings",
    "equip": "meter_B",
    "text": '"meter_B" mv_savings (info): avoided energy 24,527 (2.0%)',
}
FACTS = [F1, F2, F3, F4, MV_BASE, MV_A, MV_B]
CTX = _context(FACTS)


def _verdict(text, facts=None, per_sentence=True):
    ctx = CTX if facts is None else _context(facts)
    out = _judge(text, ctx, per_sentence=per_sentence)
    assert len(out) == 1, [j.text for j in out]
    j = out[0]
    return "unknown" if j.unknown else (j.reason or "supported")


f1, f2, f3 = F1["id"], F2["id"], F3["id"]
mb, ma, mvb = MV_BASE["id"], MV_A["id"], MV_B["id"]

SUPPORTED = [
    f"AHU__excess_oa ran mechanical cooling in 46% of the 64 free-cooling hours [{f2}].",
    f"AHU__excess_oa had a median outdoor-air fraction of 45%, against a 20% minimum [{f3}].",
    f"Both findings are faults: 46% of 64 hours [{f2}] and an OAF median of 45% [{f3}].",
    f"Free cooling was available below 60°F [{f2}].",
    f"Mechanical cooling ran in 46 percent of the 64 free-cooling hours [{f2}].",
    f"Mechanical cooling ran in 46.0% of the 64 free-cooling hours [{f2}].",
    f"Mechanical cooling ran in forty-six percent of free-cooling hours [{f2}].",
    f"Mechanical cooling ran in 46% of sixty-four free-cooling hours [{f2}].",
    f"meter_A avoided 47,980 (3.2%) ± 74,469 at 90% confidence [{ma}].",
    f"meter_A's 4P baseline does not meet G14 acceptance, with CV(RMSE) 25.9% [{mb}].",
    f"meter_A's baseline fails G14 [{mb}].",
    f"Measured over 364 reporting days [{ma}].",
    # wrongly rejected before 0.112: a year a cited fact carries as a date, a rank, a list marker
    f"meter_A avoided 47,980 (3.2%) in 2017 [{ma}].",
    f"Ranked cause 1, the outdoor-air fraction of AHU__excess_oa [{f3}].",
    f"Ranked cause two: the outdoor-air fraction is a fault [{f3}].",
    f"1. The outdoor-air fraction of AHU__excess_oa is a fault [{f3}].",
    f"The 2nd finding is the outdoor-air fraction [{f3}].",
    # grouped citations (DESKSIM-1)
    f"Both findings are faults [{f2}, {f3}].",
    f"Both findings are faults [{f2}; {f3}].",
    f"Both findings are faults [{f2},{f3}].",
    f"AHU__economizer has no missed free cooling [{F4['id']}].",
    f"AHU__economizer shows no sign of missed free cooling [{F4['id']}].",
    f"It gave 4 fault, 0 warn and 2 ok results [{f1}].",
]

WRONG_NUMBER = [
    # a changed figure (caught before too)
    f"AHU__excess_oa ran mechanical cooling in 51% of the 64 free-cooling hours [{f2}].",
    f"AHU__excess_oa ran 1,064 free-cooling hours [{f2}].",
    # sign
    f"Mechanical cooling fell by -46% [{f2}].",
    f"The median OAF was -45% [{f3}].",
    f"The median OAF was −45% [{f3}].",
    f"The median OAF changed by minus 45% [{f3}].",
    f"meter_A's savings were -3.2% [{ma}].",
    # a number only in the metrics, never stated
    f"The cooling valve was 90% open [{f2}].",
    f"The median OAF in cooling weather was 44.9% [{f3}].",
    # thresholds stated as results
    f"Fixing it saves 60% [{f2}].",
    f"Fixing it saves 5% [{f2}].",
    f"The valve was open 5% [{f2}].",
    f"The unit wastes 20% [{f3}].",
    # units
    f"AHU__excess_oa ran mechanical cooling at 46°F [{f2}].",
    f"Free cooling was available below 60°C [{f2}].",
    f"Outdoor airflow was 45 cfm [{f3}].",
    f"The excess outside air cost 45 kWh [{f3}].",
    f"OAF rose 45 percentage points [{f3}].",
    f"It used 20 MWh [{f3}].",
    f"meter_A saved 47,980 therms [{ma}].",
    f"meter_A saved 47,980 MWh [{ma}].",
    f"meter_A saved 47,980 kWh [{ma}].",
    f"The supply air was 2.0 degrees low [{f2}].",
    # the counted quantity
    f"Mechanical cooling ran on 64 days of free cooling [{f2}].",
    f"Mechanical cooling ran for 64 samples [{f2}].",
    f"Measured over 364 weeks of reporting [{ma}].",
    f"There were 0 fault and 1 warn findings [{f1}].",
    f"It saves 100% of the cooling energy [{f3}].",
    # derived numbers, a date the fact does not carry, a number in words
    f"The campus avoided 72,507 in total [{ma}] [{mvb}].",
    f"The savings were reported on 2017-06-01 [{ma}].",
    f"Mechanical cooling ran in nearly half of its free-cooling hours [{f2}].",
]

WRONG_CLAIM = [
    # another equipment, facility, rule or meter than the cited fact's
    f"AHU__economizer ran mechanical cooling in 46% of the 64 free-cooling hours [{f2}].",
    f"At fac-b2, mechanical cooling ran in 46% of the 64 free-cooling hours [{f2}].",
    f"AHU__excess_oa failed economizer_high_limit in 46% of 64 hours [{f2}].",
    f"meter_B avoided 47,980 (3.2%) [{ma}].",
    f'"fac-b" ran mechanical cooling in 46% of the 64 free-cooling hours [{f2}].',
    # negation and direction
    f"AHU__excess_oa did not run mechanical cooling in 46% of the 64 hours [{f2}].",
    f"AHU__excess_oa has no fault [{f3}].",
    f"AHU__excess_oa needs no action [{f3}].",
    f"meter_A's consumption rose 3.2% against the baseline [{ma}].",
    f"The outdoor air wastes energy [{f3}].",
    # qualitative verdicts
    f"AHU__excess_oa's economizer works correctly and needs no action [{f3}].",
    f"The outdoor_air_fraction check on AHU__excess_oa passed (ok) with 45% [{f3}].",
    f"The unit is healthy [{f3}].",
    f"The sensor was confirmed accurate [{f3}].",
    f"meter_A's saving of 47,980 is statistically significant at 90% [{ma}].",
    f"meter_A's 4P baseline meets the G14 acceptance criteria, with CV(RMSE) 25.9% [{mb}].",
]


@pytest.mark.parametrize("text", SUPPORTED)
def test_a_matching_sentence_is_supported(text):
    assert _verdict(text) == "supported"


@pytest.mark.parametrize("text", WRONG_NUMBER)
def test_a_figure_that_does_not_match_is_unsupported(text):
    assert _verdict(text) == "uncited-number"


@pytest.mark.parametrize("text", WRONG_CLAIM)
def test_a_claim_no_cited_fact_makes_is_unsupported(text):
    assert _verdict(text) == "unsupported-claim"


def test_a_citation_hidden_in_markup_cites_nothing():
    for text in (
        f"Saves 46% <!-- [{f2}] -->.",
        f'Saves 46% <span title="[{f2}]"></span>.',
        f"Saves 46% <!-- [{f2}]",
    ):
        assert _verdict(text) == "uncited-sentence", text
    assert _verdict(f"Saves 46% <!-- note --> [{f2}].") == "unsupported-claim"  # "saves"
    assert extract_cites(f"[{f2}, {f3}; F9@x]") == [f2, f3, "F9@x"]


def test_a_number_inside_a_name_from_the_data_is_not_a_figure():
    fac = {
        "id": "F3@facilities-cccccc",
        "kind": "facility",
        "facility_id": "inj-2",
        "text": 'facility "Bldg 4242 kWh" ("inj-2"): 12 points',
    }
    detail = {
        "id": "F1@finding_detail-cccccc",
        "kind": "finding",
        "value": "fault",
        "equip": "AHU-25 saves 777 kWh",
        "text": '"AHU-25 saves 777 kWh" free_cooling_missed (fault): ran 46% of 336 hours',
    }
    facts = [fac, detail]
    assert _verdict(f"The building uses 4242 kWh a year [{fac['id']}].", facts) == "uncited-number"
    assert _verdict(f"inj-2 saves 99% of 4242 kWh [{fac['id']}].", facts) == "uncited-number"
    assert _verdict(f"The retrofit saves 777 kWh [{detail['id']}].", facts) == "uncited-number"
    assert _verdict(f"The unit ran 46% of 336 hours [{detail['id']}].", facts) == "supported"
    # the name itself, quoted as the fact quotes it, is fine
    assert _verdict(f'"Bldg 4242 kWh" has 12 points [{fac["id"]}].', facts) == "supported"


def test_dates_and_times_must_be_ones_a_cited_fact_carries():
    ref = {
        "id": "R1@sensor_trust-dddddd",
        "kind": "reference_comparison",
        "equip": "AHU-1",
        "value": "warn",
        "text": '"AHU-1" "supply_air_temp" disagrees (warn): 384 paired samples from 2025-05-19 '
        "00:00:00 to 2025-05-22 23:45:00",
    }
    r1 = ref["id"]
    ok = f"The comparison ran from 2025-05-19 00:00:00 to 2025-05-22 23:45:00 [{r1}]."
    assert _verdict(ok, [ref]) == "supported"
    assert _verdict(f"It ran in 2025 over 384 paired samples [{r1}].", [ref]) == "supported"
    assert _verdict(f"It ended 2025-05-23 [{r1}].", [ref]) == "uncited-number"
    assert _verdict(f"It ended 2025-05-22 at 22:00 [{r1}].", [ref]) == "uncited-number"
    assert _verdict(f"It ran in 2024 [{r1}].", [ref]) == "uncited-number"


def test_structured_value_and_unit_are_figures_of_the_fact():
    fact = {"id": "F1@x-eeeeee", "kind": "k", "value": 12.5, "unit": "kWh", "text": "a total"}
    assert _verdict(f"The total was 12.5 kWh [{fact['id']}].", [fact]) == "supported"
    assert _verdict(f"The total was 12.5 MWh [{fact['id']}].", [fact]) == "uncited-number"
    neg = {"id": "F1@x-eeeeee", "kind": "k", "value": -3, "text": "a change"}
    assert _verdict(f"The change was -3 [{neg['id']}].", [neg]) == "supported"
    assert _verdict(f"The change was 3 [{neg['id']}].", [neg]) == "uncited-number"
    up = {"id": "F1@x-eeeeee", "kind": "k", "direction": "up", "text": "use changed by 4%"}
    assert _verdict(f"Use rose by 4% [{up['id']}].", [up]) == "supported"
    assert _verdict(f"Use fell by 4% [{up['id']}].", [up]) == "unsupported-claim"


def test_the_failing_sentence_says_why():
    j = _judge(f"The median OAF was -45% [{f3}].", CTX, per_sentence=True)[0]
    assert "-45 %" in j.why[0] and "sign" in j.why[0]
    j = _judge(f"meter_B avoided 47,980 (3.2%) [{ma}].", CTX, per_sentence=True)[0]
    assert j.why == ["'meter_b': no cited fact names it"]


# --------------------------------------------------------------------------- headings, notices


def test_a_heading_is_kept_and_not_judged():
    text = f"# AHU triage\n\nFindings:\n- AHU__excess_oa has a fault [{f3}].\n**Next steps:**"
    out = _judge(text, CTX, per_sentence=True)
    assert [j.text for j in out] == [f"- AHU__excess_oa has a fault [{f3}]."]
    assert out[0].reason == ""
    g = check(text, CTX, require_cite_each_sentence=True)
    assert g.grounded and g.text == text
    # a heading that states something is a sentence: judged, and without a citation it goes
    for line in ("# No faults found", "Savings of 12%:", "## Consumption rose"):
        assert [j.text for j in _judge(line, CTX, per_sentence=True)] == [line]
    assert _judge("## 1. Ranked cause 1", CTX, per_sentence=True) == []


def test_a_server_notice_is_kept_only_when_the_server_issued_it():
    notice = "the results are for information only"
    text = f"NOTICE: {notice}\n\nAHU__excess_oa has a fault [{f3}].\nNOTICE: 99% saved"
    out = _judge(text, CTX, per_sentence=True, notices=[notice])
    assert [j.text for j in out] == [f"AHU__excess_oa has a fault [{f3}].", "NOTICE: 99% saved"]
    assert out[1].reason == "uncited-sentence"
    g = check(text, CTX, require_cite_each_sentence=True, notices=[notice])
    assert g.text == f"NOTICE: {notice}\n\nAHU__excess_oa has a fault [{f3}]."
    # without the server's word for it, a NOTICE line is judged like any other
    assert len(_judge(text, CTX, per_sentence=True)) == 3


def test_a_notice_line_is_the_servers_when_it_repeats_a_notice_fact():
    notice = "the results are for information only"
    nfact = {"id": "N1@rule_run-aaaaaa", "kind": "notice", "text": notice}
    text = f"NOTICE: {notice}\nAHU__excess_oa has a fault [{f3}]."
    res = check_citations(SimpleNamespace(), {"text": text, "facts": [*FACTS, nfact]})
    assert res.data["grounded"] is True and res.data["n_sentences"] == 1
    # citing the notice fact supports the line too
    cited = f"NOTICE: {notice} [{nfact['id']}]\nAHU__excess_oa has a fault [{f3}]."
    res = check_citations(SimpleNamespace(), {"text": cited, "facts": [*FACTS, nfact]})
    assert res.data["grounded"] is True and res.data["n_sentences"] == 2
    # a NOTICE line no notice fact says is judged like any other line; removing its caveat
    # ("for information only") is said at the top of verified_text (#328)
    res = check_citations(SimpleNamespace(), {"text": text, "facts": FACTS, "detail": True})
    assert res.data["n_sentences"] == 2
    assert res.data["verified_text"] == f"{REMOVED}\n\n{text.split(chr(10))[1]}"
    forged = f"NOTICE: {notice}, and the unit saves 99%\nAHU__excess_oa has a fault [{f3}]."
    res = check_citations(SimpleNamespace(), {"text": forged, "facts": [*FACTS, nfact]})
    assert res.data["n_sentences"] == 2 and res.data["grounded"] is False


# --------------------------------------------------------------------------- verified_text


def _verified(text):
    ctx = CallContext(store="/nonexistent", allowed_facilities=frozenset())
    res = REG.call("check_citations", {"text": text, "facts": FACTS, "detail": True}, ctx)
    return res.data


@pytest.mark.parametrize(
    "draft",
    [
        f"AHU__excess_oa has a fault [{f3}].\n\nAHU__excess_oa has a fault [{f3}].\n",
        f"Findings:\n- AHU__excess_oa has a fault [{f3}].\n- It ran 46% of 64 hours [{f2}].",
        f"# AHU triage\n\nAHU__excess_oa has a fault [{f3}].\nIt ran 46% of 64 hours [{f2}].",
        f"1. AHU__excess_oa has a fault [{f3}].\n2. It ran 46% of 64 hours [{f2}].\r\n",
    ],
)
def test_verified_text_is_the_draft_when_every_sentence_is_supported(draft):
    data = _verified(draft)
    assert data["grounded"] is True and data["verified_text"] == draft


def test_verified_text_removes_only_the_failing_sentences():
    draft = (
        "# AHU triage\n\n"
        f"OAF was -45% [{f3}].\n\n"
        f"AHU__excess_oa has a fault [{f3}]. It saves 60% [{f2}]. It ran 46% of 64 hours [{f2}].\n"
        "\n"
        "Steps:\n"
        f"1. Bad 99 [{f2}].\n"
        f"2. Check the damper of AHU__excess_oa [{f3}].\n"
        f"- AHU__excess_oa has a fault [{f3}, F9@x-000000].\n"
    )
    data = _verified(draft)
    assert data["verdicts"] == {
        "supported": 3,
        "unknown_citation": 1,
        "uncited": 0,
        "unsupported_number": 3,
        "unsupported_claim": 0,
        "forgotten_citation": 0,
    }
    # the list item with an unknown id stated a verdict ("has a fault"): its removal is said
    assert data["verified_text"] == (
        f"{REMOVED}\n\n"
        "# AHU triage\n\n"
        f"AHU__excess_oa has a fault [{f3}]. It ran 46% of 64 hours [{f2}].\n"
        "\n"
        "Steps:\n"
        f"2. Check the damper of AHU__excess_oa [{f3}].\n"
    )
    bad = [s for s in data["sentences"] if s["verdict"] != "supported"]
    assert all(s["why"] for s in bad) and not any(
        "why" in s for s in data["sentences"] if s["verdict"] == "supported"
    )


def test_strict_check_keeps_lines_and_strips_unknown_ids():
    text = f"# Title\n\nAHU__excess_oa has a fault [{f3}, F9@x]. Bad 99 [{f3}].\n\nEnd [F9@x]."
    g = check(text, CTX)
    assert g.text == f"# Title\n\nAHU__excess_oa has a fault [{f3}].\n\nEnd."
    assert not g.grounded and g.cited == [f3]


# --------------------------------------------------------------------------- real drafts

# the facts of a blind tester's triage of one air handler (0.111 acceptance test, round 5), cut
# to what the sentences below cite
BLIND = [
    {
        "id": "F1@points-4d3762",
        "kind": "point_count",
        "facility_id": "site-north",
        "value": 11,
        "text": 'site-north stores 11 points on 1 equipment matching equip "AHU-1"',
    },
    {
        "id": "F1@sensor_trust-a182b5",
        "kind": "trust_summary",
        "facility_id": "site-north",
        "value": 0,
        "text": "site-north: 11 matching points judged (rules view): 9 trusted, 2 suspect, 0 "
        "untrusted; 0 below the trust floor 0.5",
    },
    {
        "id": "F3@sensor_trust-a182b5",
        "kind": "point_trust",
        "equip": "AHU-1",
        "role": "supply_air_temp",
        "value": 0.9991,
        "text": '"AHU-1" "supply_air_temp": trust 1.00 (suspect; bimodal, reference_warn); '
        "coverage 100% of its own span",
    },
    {
        "id": "R1@sensor_trust-a182b5",
        "kind": "reference_comparison",
        "equip": "AHU-1",
        "role": "supply_air_temp",
        "value": "warn",
        "text": '"AHU-1" "supply_air_temp" against its configured reference "handheld reference '
        'logger" disagrees (warn): the point is suspect, and a finding that rests on it is '
        "conditional on this sensor: AHU-1 supply_air_temp: bias +3.0, drift not evaluated, "
        "RMSE 3.0, r=1.00 over 384 samples / 4 days -- biased +3.0; bias +3.0 is the median of "
        "the point minus the reference, so the point reads high against it; 384 paired samples "
        "from 2025-05-19 00:00:00 to 2025-05-22 23:45:00",
    },
    {
        "id": "F1@rule_run-7de39b",
        "kind": "rule_run",
        "facility_id": "site-north",
        "value": 3,
        "text": "3 rules were run on 1 equipment of site-north and 3 of them returned a result: "
        "1 fault, 1 warn, 0 info and 1 ok; 0 rule-equipment pair(s) (0 rule(s)) skipped for "
        "missing inputs",
    },
    {
        "id": "F1@finding_detail-358e19",
        "kind": "finding",
        "value": "fault",
        "rule": "leaking_valve",
        "equip": "AHU-1",
        "text": '"AHU-1" leaking_valve (fault): "AHU-1": with both valves shut, air shifts '
        "(median +3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut "
        "samples (worst: HW (heating) coil); conditional on sensor_drift:supply_air_temp warn "
        "(AHU-1 supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 "
        "samples / 4 days -- biased +3.0) on AHU-1",
        "metrics": {"hw_leak_pct": 86.5, "fan_heat_f": 2.0},
    },
    {
        "id": "F2@finding_detail-358e19",
        "kind": "evidence_hours",
        "value": 8.0,
        "text": "The rule flagged 8 hours (32 of 37 judged both-valves-shut samples, 86.5%) in 3 "
        "contiguous span(s)",
    },
    {
        "id": "F4@finding_detail-358e19",
        "kind": "parameters",
        "value": 8,
        "text": "leaking_valve ran with fan_heat_f = 2.0, delta_thr_f = 3.0, valve_closed_thr = "
        "5.0, coil_sensor_fan_heat = False, measured_fan_heat_f not set",
    },
    {
        "id": "F1@finding_detail-43b375",
        "kind": "finding",
        "value": "warn",
        "rule": "supply_air_reset_compliance",
        "equip": "AHU-1",
        "text": '"AHU-1" supply_air_reset_compliance (warn): "AHU-1": SAT median 55.0°F vs '
        "configured target 57.1°F; below target 54% of hours (mean gap +3.1°F) -- reheat/energy "
        "opportunity",
    },
]

BLIND_SUPPORTED = """\
Triage of air handler "AHU-1" at site-north [F1@points-4d3762]. The unit was judged by 3 rules, \
which gave 1 fault, 1 warn, 0 info and 1 ok, and 0 rules were skipped for missing inputs \
[F1@rule_run-7de39b].

Ranked cause one, the supply air temperature sensor of "AHU-1" [R1@sensor_trust-a182b5]. Its \
configured reference, a handheld reference logger, \
disagrees with it at warn level, so the point is suspect although its trust score is 1.00 \
[F3@sensor_trust-a182b5] [R1@sensor_trust-a182b5]. The comparison reads bias +3.0, RMSE 3.0 and \
r=1.00 over 384 paired samples in 4 days, from 2025-05-19 00:00:00 to 2025-05-22 23:45:00, and \
drift was not evaluated [R1@sensor_trust-a182b5].

Ranked cause two, a possible heating valve leak [F1@finding_detail-358e19]. The leaking_valve rule \
reports a fault: with both valves shut, air shifts (median +3.4F beyond 2F fan heat), with HW-leak \
86% and CHW-leak 0% of both-valves-shut samples [F1@finding_detail-358e19]. The rule flagged 8 \
hours (32 of 37 judged both-valves-shut samples, 86.5%) in 3 contiguous spans \
[F2@finding_detail-358e19]. The rule ran with fan_heat_f = 2.0, delta_thr_f = 3.0 and \
valve_closed_thr = 5.0, measured_fan_heat_f not set [F4@finding_detail-358e19].

Ranked cause three: the supply_air_reset_compliance rule warns that the SAT median is 55.0°F \
against a configured target of 57.1°F, below target 54% of hours with a mean gap of +3.1°F \
[F1@finding_detail-43b375]. It sits below the configured target 57.1°F for 54% of hours \
[F1@finding_detail-43b375]. Of the 11 matching points, 9 are trusted, 2 suspect and 0 untrusted, \
and 0 are below the trust floor 0.5 [F1@sensor_trust-a182b5].
"""

#: sentences of the same drafts that 0.112 no longer supports, each for a stated reason
BLIND_FLAGGED = [
    # "failing" is a verdict R1 does not state (it says warn and suspect)
    "A near-perfect correlation with a steady offset is the signature of a calibration offset "
    "rather than a failing or noisy sensor [R1@sensor_trust-a182b5].",
    # "suspect" is stated by R1, not by the cited finding
    "Supply air below its reset target also rests on the suspect sensor "
    "[F1@finding_detail-43b375].",
    # "no check was declined": the trust summary gives no count of declined checks
    "No check was declined for an untrusted input, since no point is below the trust floor "
    "[F1@sensor_trust-a182b5].",
    # a 4-hour figure where the fact has 8
    "The rule flagged 4 hours in 3 contiguous spans [F2@finding_detail-358e19].",
    # a confidence no cited fact gives (#328: an assurance word passes only where a fact uses it)
    'Ranked cause one, the supply air temperature sensor of "AHU-1" (confidence high that it is '
    "suspect) [R1@sensor_trust-a182b5].",
]


def test_a_blind_testers_draft_stays_supported():
    data = check_citations(
        SimpleNamespace(), {"text": BLIND_SUPPORTED, "facts": BLIND, "detail": True}
    ).data
    failing = [(s["text"], s.get("why")) for s in data["sentences"] if s["verdict"] != "supported"]
    assert failing == [] and data["n_sentences"] == 12
    assert data["verified_text"] == BLIND_SUPPORTED


@pytest.mark.parametrize("sentence", BLIND_FLAGGED)
def test_a_blind_testers_overreaching_sentence_is_flagged(sentence):
    data = check_citations(SimpleNamespace(), {"text": sentence, "facts": BLIND}).data
    assert data["grounded"] is False and data["sentences"][0]["why"]


# --------------------------------------------------------------------------- other callers


def test_without_per_sentence_an_uncited_sentence_states_no_claim():
    text = f"AHU__excess_oa has a fault [{f3}]. The unit should be replaced. AHU-9 is fine."
    assert all(j.reason == "" for j in _judge(text, CTX))
    # its figures still trace to the facts the text cites
    out = _judge(f"The OAF median was 45% [{f3}]. Savings are 64%.", CTX)
    assert [j.reason for j in out] == ["", "uncited-number"]


# --------------------------------------------------------------------------- structured fields


def test_a_claims_own_figure_never_supports_a_measured_statement():
    claimed = {
        "id": "A1@mv_summary-bbbbbb",
        "kind": "claimed_figure",
        "basis": "claimed",
        "value": 15.0,
        "unit": "%",
        "subject": {"facility_id": "fac-a"},
        "text": "The claim under check states 15%: the claim's own figure, not a measured or "
        "computed result",
    }
    facts = [claimed]
    assert _verdict(f"The project saved 15% [{claimed['id']}].", facts) == "uncited-number"
    assert _verdict(f"The measured saving was 15% [{claimed['id']}].", facts) == "uncited-number"
    assert _verdict(f"The claim states 15% [{claimed['id']}].", facts) == "supported"
    assert _verdict(f"The vendor claimed 15% [{claimed['id']}].", facts) == "supported"


def test_structured_subject_unit_and_verdicts():
    flat = {
        "id": "F4@rule_run-ffffff",
        "kind": "flat_point",
        "equip": "AHU-2",
        "role": "heat_valve",
        "value": 0.0,
        "unit": "%",
        "verdict": "flat",
        "subject": {"facility_id": "fac-a", "equip": "AHU-2", "role": "heat_valve"},
        "text": '"AHU-2" "heat_valve" never moves over the window: all 780 valid samples from '
        "2025-05-19 00:00 to 2025-05-22 23:45 read 0 %, so it is flat for the whole window",
    }
    effect = {
        "id": "E1@rule_run-ffffff",
        "kind": "reference_effect",
        "value": 3.0,
        "unit": "°F",
        "verdict": "persists",
        "subject": {"facility_id": "fac-a", "equip": "AHU-1", "finding": "leaking_valve@AHU-1"},
        "text": "With the supply air sensor's +3.0 °F bias removed, the leaking_valve finding "
        "on AHU-1 persists",
    }
    other = {"id": "F9@points-ffffff", "kind": "x", "subject": {"equip": "AHU-3"}, "text": "x"}
    facts = [flat, effect, other]
    assert _verdict(f"The heat valve of AHU-2 is flat at 0% [{flat['id']}].", facts) == "supported"
    ok = f"Removing the +3.0°F bias leaves the leak on AHU-1: it persists [{effect['id']}]."
    assert _verdict(ok, facts) == "supported"
    explained = f"The leak on AHU-1 is explained by the sensor [{effect['id']}]."
    assert _verdict(explained, facts) == "unsupported-claim"
    assert _verdict(f"The +3.0°C bias persists [{effect['id']}].", facts) == "uncited-number"
    # a subject named only in another fact's structured subject is still a name to match
    assert _verdict(f"AHU-3's heat valve is flat [{flat['id']}].", facts) == "unsupported-claim"


def test_an_issue_rank_is_a_label():
    issue = {
        "id": "I1@rcx_report-gggggg",
        "kind": "rcx_issue",
        "rank": 1,
        "of": 4,
        "value": 1,
        "text": "Issue 1 of 4: supply air held cold on AHU-1",
    }
    assert _verdict(f"Issue 1 of 4 is supply air held cold on AHU-1 [{issue['id']}].", [issue]) == (
        "supported"
    )
    assert _verdict(f"Issue 1 of 5 is on AHU-1 [{issue['id']}].", [issue]) == "uncited-number"
