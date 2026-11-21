"""Round 18 of the citation checker (PLAN-CITE-EVAL, measured regime): fact texts first.

1. Fact texts state what hosts wrote from the result's JSON, so a true sentence has a fact that
   says it and a false one a fact it can be checked against:
   - why an excess-OA finding's kBtu totals are null (``oa_cost_kbtu_declined``: no trended
     supply airflow, an implausible one, one trended on too few of the priced samples, nothing to
     price, or the generic minimum), in its finding fact and its labels fact; any ``*_declined``
     metric is stated in its own words;
   - which way an airflow_tracking finding missed when it missed only one way ("always too little
     air, never too much");
   - reheat_minimization_g36's "outside the high heating loop" also as "while the heating loop is
     not high";
   - the M&V notice of savings that fail G14 acceptance gives their whole and names them ("They
     are 5 of the 8 savings: ...").
2. The checker: a fact another sentence of the draft cites is still one of the set a sentence
   ranks its own fact in (the widest band, the only meter); a word of the run's facility id ("the
   g36 VAV fleet") is the run's scope, not one of its units; a trust verdict said of one point
   from a count points the host at the trusted-points fact.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._cap_mv_summary import _of  # noqa: E402
from camber.capabilities._common import finding_fact, finding_labels  # noqa: E402
from camber.capabilities._ledger import FactLedger  # noqa: E402
from camber.capabilities._sanitize import unmark  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.oafraction import excess_oa_penalty  # noqa: E402
from camber.rules.airflow_rule import AirflowTracking  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite18-"))


def _f(fid, kind, text, **kw):
    return {"id": fid, "kind": kind, "text": text, **kw}


def _last(judge, draft, *facts):
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
    return r["sentences"][-1]


def _ok(judge, draft, *facts) -> bool:
    return _last(judge, draft, *facts)[1] == "supported"


# --------------------------------------------------------------------------- 1. fact texts

_IDX = pd.date_range("2025-07-01", periods=20, freq="1h")


def _hot(**cols) -> dict:
    """The kBtu part of excess_oa_penalty on 20 hot hours with excess OA (OAF 50 % over a 20 %
    minimum), with the extra columns given."""
    cond = pd.DataFrame({"OAT": 88.0, "ReturnAir": 75.0, **cols}, index=_IDX)
    return excess_oa_penalty(
        cond, pd.Series(50.0, index=_IDX), pd.Series(20.0, index=_IDX), margin_pct=5.0,
        observed_h=20,
    )  # fmt: skip


def test_the_kbtu_totals_say_why_they_are_null():
    out = _hot()
    assert out["oa_cost_excess_cool_kbtu"] is None
    assert out["oa_cost_kbtu_declined"].startswith("no trended supply airflow (none is trended)")
    assert "not costed in kBtu" in out["oa_cost_kbtu_declined"]
    priced = _hot(SupplyCFM=pd.Series(2000.0, index=_IDX))
    assert priced["oa_cost_excess_cool_kbtu"] > 0 and "oa_cost_kbtu_declined" not in priced
    implausible = _hot(SupplyCFM=pd.Series(40.0, index=_IDX))
    assert "not a plausible airflow" in implausible["oa_cost_kbtu_declined"]
    gappy = pd.Series(2000.0, index=_IDX).where(pd.Series(range(20), index=_IDX) % 2 == 0)
    assert "on only 50% of the priced samples" in _hot(SupplyCFM=gappy)["oa_cost_kbtu_declined"]


def test_the_generic_minimum_says_why_its_figures_are_null():
    idx = pd.date_range("2025-07-01", periods=24 * 7, freq="1h")
    frame = pd.DataFrame(
        {
            Role.OAT: 88.0,
            Role.RETURN_AIR_TEMP: 75.0,
            Role.MIXED_AIR_TEMP: 81.5,
            Role.SUPPLY_FAN_STATUS: 1.0,
        },
        index=idx,
    )
    m = OutdoorAirFraction().analyze("AHU-1", frame).metrics
    if m.get("oa_cost_excess_cool_btu_per_cfm", 0) is None:  # the generic 20 % minimum
        assert m["oa_cost_kbtu_declined"].startswith("no min_oa_pct is configured")


_EOA = {
    "equip": "AHU__excess_oa_no_flow",
    "rule": "outdoor_air_fraction",
    "severity": "fault",
    "finding_id": "outdoor_air_fraction@AHU__excess_oa_no_flow",
    "summary": "AHU__excess_oa_no_flow: OAF median 45% (45% in cooling weather)",
    "metrics": {
        "oa_cost_airflow": None,
        "oa_cost_excess_cool_kbtu": None,
        "oa_cost_cool_basis": "enthalpy",
        "oa_cost_kbtu_declined": _hot()["oa_cost_kbtu_declined"],
    },
}


def test_finding_and_labels_facts_state_the_declined_reason():
    said = "oa_cost_kbtu_declined: no trended supply airflow (none is trended), so its excess"
    assert said in unmark(finding_fact("F5@rule_run-aaaaaa", _EOA)["text"])
    labels = unmark(finding_labels("M4@rule_run-aaaaaa", _EOA)["text"])
    assert said in labels and 'oa_cost_kbtu_declined "' not in labels  # its words, not a value
    assert 'oa_cost_cool_basis "enthalpy"' in labels
    plain = {**_EOA, "metrics": {"oa_cost_excess_cool_kbtu": 6182.4}}
    assert "declined" not in finding_fact("F3@rule_run-aaaaaa", plain)["text"]


def _vav(low: bool = True, high: bool = False) -> pd.DataFrame:
    idx = pd.date_range("2025-05-05", periods=240, freq="1h")
    flow = pd.Series(1000.0, index=idx)
    if low:
        flow.iloc[::2] = 500.0
    if high:
        flow.iloc[1::4] = 1500.0
    return pd.DataFrame(
        {
            Role.AIRFLOW: flow,
            Role.AIRFLOW_SP: pd.Series(1000.0, index=idx),
            Role.OCCUPANCY: pd.Series(1.0, index=idx),
            Role.SUPPLY_FAN_STATUS: pd.Series(1.0, index=idx),
        }
    )


def test_airflow_says_which_way_it_missed_when_one_way():
    starved = AirflowTracking().analyze("VAV-1", _vav()).summary
    assert "always too little air (starved), never too much" in starved
    both = AirflowTracking().analyze("VAV-1", _vav(high=True)).summary
    assert "always" not in both and "never" not in both
    over = AirflowTracking().analyze("VAV-1", _vav(low=False, high=True)).summary
    assert "always too much air (over), never too little" in over
    on = AirflowTracking().analyze("VAV-1", _vav(low=False)).summary
    assert "always" not in on


def test_of_gives_the_whole():
    assert _of(5, 8) == "They are 5 of the 8 savings"
    assert _of(8, 8) == "They are all 8 savings"
    assert _of(1, 8) == "It is 1 of the 8 savings"
    assert _of(1, 1) == "It is the only saving"


# --------------------------------------------------------------------------- 2. dev units

_NO_FLOW = (
    '"AHU__excess_oa_no_flow" outdoor_air_fraction (fault): "AHU__excess_oa_no_flow": OAF median '
    "45% (45% in cooling weather); excess OA 100% of cooling hours above 20% min; oa_cost_flagged "
    'true; gates: fan_gate "fan status"; null: min_oa_pct_by_month, oa_cost_airflow, '
    "oa_cost_excess_cool_kbtu, oa_cost_excess_heat_kbtu; oa_cost_kbtu_declined: no trended supply "
    "airflow (none is trended), so its excess outside air is not costed in kBtu (no kBtu cost is "
    "calculated; oa_cost_excess_cool_kbtu and oa_cost_excess_heat_kbtu are null)"
)
F5_EOA = _f(
    "F5@rule_run-53a917", "finding", _NO_FLOW, rule="outdoor_air_fraction",
    equip="AHU__excess_oa_no_flow", facility_id="ds-synthetic-ahu-excess-oa",
)  # fmt: skip
F3_EOA = _f(
    "F3@rule_run-53a917",
    "finding",
    '"AHU__excess_oa" outdoor_air_fraction (fault): "AHU__excess_oa": OAF median 45% (45% in '
    "cooling weather); excess OA 100% of cooling hours above 20% min; oa_cost_flagged true; gates: "
    'fan_gate "fan status"; totals: oa_cost_excess_cool_kbtu (oa cost excess cool) 6,182 kBtu, '
    "oa_cost_excess_heat_kbtu (oa cost excess heat) 427 kBtu; null: min_oa_pct_by_month",
    rule="outdoor_air_fraction",
    equip="AHU__excess_oa",
    facility_id="ds-synthetic-ahu-excess-oa",
)
F3_G36 = _f(
    "F3@rule_run-3e0a26",
    "finding",
    '"VAV1__heat_cool" reheat_minimization_g36 (fault): "VAV1__heat_cool": reheating 93% of '
    "occupied hours; of those, 86% with airflow above minimum outside the high heating loop, that "
    "is while the heating loop is not high (G36 §5.6.5 would hold min flow until deep heating "
    "demand)",
    rule="reheat_minimization_g36",
    equip="VAV1__heat_cool",
    facility_id="ds-synthetic-g36-vav-fleet",
)
F4_G36 = _f(
    "F4@rule_run-3e0a26",
    "finding",
    '"VAV1__not_responding" airflow_tracking (fault): "VAV1__not_responding": airflow off '
    "setpoint 87% of occupied fan-on hours (mean |err| 52%; starved 87%, over 0%: off setpoint, it "
    "was always too little air (starved), never too much); the fan ran in all 180 occupied "
    "samples it judged, so its occupied fan-on hours are all its occupied hours",
    rule="airflow_tracking",
    equip="VAV1__not_responding",
    facility_id="ds-synthetic-g36-vav-fleet",
)
RUN_G36 = _f(
    "F1@rule_run-cb39fb",
    "rule_run",
    "2 rules were run on 56 equipment of ds-synthetic-g36-vav-fleet and 2 of them returned a "
    "result, 16 rule-equipment result(s) in all: 0 fault, 1 warn, 2 info and 13 ok; 0 "
    "rule-equipment pair(s) (0 rule(s)) skipped for missing inputs",
    facility_id="ds-synthetic-g36-vav-fleet",
)
RUN_EOA = _f(
    "F1@rule_run-9d37e7",
    "rule_run",
    "5 rules were run on 3 equipment of ds-synthetic-ahu-excess-oa and 4 of them returned a "
    "result, 10 rule-equipment result(s) in all: 6 fault, 0 warn, 1 info and 3 ok; 1 "
    "rule-equipment pair(s) (1 rule(s)) skipped for missing inputs: those pairs were not judged",
    facility_id="ds-synthetic-ahu-excess-oa",
)
_FAILING = (
    '"Fox_assembly_Adrianne__chilledwater", "Fox_assembly_Adrianne__electricity", '
    '"Fox_assembly_Audrey__electricity", "Fox_lodging_Stephan__electricity", '
    '"Fox_lodging_Stephen__electricity"'
)
N1_MV = _f(
    "N1@mv_summary-e4208b",
    "notice",
    "5 saving(s) rest on a baseline that does not meet G14 acceptance: for information only, not "
    f"a verified saving. They are 5 of the 8 savings: {_FAILING}.",
)


@pytest.mark.parametrize(
    "draft,facts",
    [
        # stage 6 (3 dev strips): no trended airflow, so the cost is null
        ("AHU__excess_oa_no_flow has no trended airflow, so the kBtu cost figures are null "
         "[F5@rule_run-53a917].", [F5_EOA]),
        ("There is no trended airflow on AHU__excess_oa_no_flow, so I can't cost it "
         "[F5@rule_run-53a917].", [F5_EOA]),
        ("AHU__excess_oa_no_flow has no trended supply airflow, so its excess-OA cost could not "
         "be calculated [F5@rule_run-53a917].", [F5_EOA]),
        ("Its kBtu cost is not calculated because there is no trended airflow "
         "[F5@rule_run-53a917].", [F5_EOA]),
        # r17 dev strips (3): "outside the high heating loop" said as "not high"
        ("VAV1__heat_cool reheats in 93% of occupied hours [F3@rule_run-3e0a26]. In 86% of those "
         "hours, airflow is above the minimum while the heating demand is not high "
         "[F3@rule_run-3e0a26].", [F3_G36]),
        ("VAV1__heat_cool reheats in 93% of occupied hours [F3@rule_run-3e0a26]. In 86% of those "
         "hours airflow is above minimum while the heating loop is not high "
         "[F3@rule_run-3e0a26].", [F3_G36]),
        # r17 dev judge error u16, now stated by the fact
        ("- It is always too little air, never too much [F4@rule_run-3e0a26].", [F4_G36]),
        # r17 dev false strip u2: the g36 VAV fleet is the run's dataset, not a unit
        ("> Duct static reset check on the g36 VAV fleet: 2 rules ran on 56 equipment, 0 faults, "
         "1 warn, 2 info, 13 ok [F1@rule_run-cb39fb].", [RUN_G36]),
        ("ds-synthetic-g36-vav-fleet: 0 faults, 1 warn, 2 info, 13 ok [F1@rule_run-cb39fb].",
         [RUN_G36]),
        # r10 strip: the whole of a count
        ("- **Acceptance:** Five of the 8 savings rest on a baseline that fails G14 acceptance, so "
         "they are for information only and are not verified savings [N1@mv_summary-e4208b].",
         [N1_MV]),
        ("The Fox_assembly_Adrianne__chilledwater saving rests on a baseline that does not meet "
         "G14 acceptance [N1@mv_summary-e4208b].", [N1_MV]),
        ("Five savings rest on a baseline that does not meet G14 acceptance "
         "[N1@mv_summary-e4208b].", [N1_MV]),
    ],
)  # fmt: skip
def test_round18_true_sentence_is_supported(judge, draft, facts):
    assert _ok(judge, draft, *facts), _last(judge, draft, *facts)


@pytest.mark.parametrize(
    "draft,facts",
    [
        ("AHU__excess_oa_no_flow has no trended airflow, so its fault was not judged "
         "[F5@rule_run-53a917].", [F5_EOA]),
        ("AHU__excess_oa has no trended airflow, so the kBtu cost figures are null "
         "[F3@rule_run-53a917].", [F3_EOA]),
        ("AHU__excess_oa_no_flow has no trended outdoor air temperature, so the kBtu cost figures "
         "are null [F5@rule_run-53a917].", [F5_EOA]),
        ("AHU__excess_oa_no_flow has trended supply airflow, so its excess outside air is costed "
         "in kBtu [F5@rule_run-53a917].", [F5_EOA]),
        ("VAV3: 0 faults, 1 warn, 2 info, 13 ok [F1@rule_run-cb39fb].", [RUN_G36]),
        ("Check the third AHU: the run counted 6 fault, 0 warn, 1 info and 3 ok results "
         "[F1@rule_run-9d37e7].", [RUN_EOA]),
        ("The Fox_assembly_Audrey__chilledwater saving rests on a baseline that does not meet G14 "
         "acceptance [N1@mv_summary-e4208b].", [N1_MV]),
        ("Five of the 9 savings rest on a baseline that does not meet G14 acceptance "
         "[N1@mv_summary-e4208b].", [N1_MV]),
        ("Six of the 8 savings rest on a baseline that does not meet G14 acceptance "
         "[N1@mv_summary-e4208b].", [N1_MV]),
        ("All 8 savings rest on a baseline that does not meet G14 acceptance "
         "[N1@mv_summary-e4208b].", [N1_MV]),
    ],
)  # fmt: skip
def test_round18_false_sentence_stays_unsupported(judge, draft, facts):
    assert not _ok(judge, draft, *facts)


# --------------------------------------------------------------------------- 3. the checker


def _mv(fid, meter, saving, pct, band):
    return _f(
        fid,
        "finding",
        f'"{meter}" mv_savings (info): "{meter}": avoided energy {saving} ({pct}) ± {band} at 90% '
        "over 340 reporting days; baseline coverage in_range; declined false; reporting period "
        "2017-01-01 to 2017-12-31 (the year 2017)",
        rule="mv_savings",
        equip=meter,
        facility_id="ds-bdg2-fox",
    )


_ADR_CHW = _mv("F3@mv_summary-82f881", "Fox_assembly_Adrianne__chilledwater", "-1,615,675",
               "-15.8%", "1,165,919")  # fmt: skip
_ADR_ELEC = _mv("F5@mv_summary-82f881", "Fox_assembly_Adrianne__electricity", "-293,693",
                "-15.6%", "208,671")  # fmt: skip
_STE_CHW = _mv("F5@mv_summary-4fe8ac", "Fox_lodging_Stephen__chilledwater", "30,601", "0.6%",
               "570,988")  # fmt: skip

# the two remaining r17 dev checker gaps, as the dev draft writes them: each rank's refuting fact
# is cited by another bullet of the same draft
_DRAFT = (
    "- **Adrianne electricity:** avoided energy -293,693 (-15.6%) [F5@mv_summary-82f881].\n"
    "- **Adrianne chilledwater:** It is the only meter with a large 2017 increase in use, "
    "−1,615,675 or −15.8% ± 1,165,919 [F3@mv_summary-82f881], so the figure is for information "
    "only.\n"
    "- **Stephen chilledwater:** It shows only 0.6% (30,601) against a very wide ± 570,988 "
    "[F5@mv_summary-4fe8ac], the widest band in absolute terms."
)


def _check(tmp_path, draft, facts):
    led = FactLedger()
    led.record(facts)
    reg = default_registry()
    view = reg.ledger_view("check_citations", {"text": draft}, led)
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset(), ledger=view)
    return reg.call("check_citations", {"text": draft, "detail": True}, ctx).data["sentences"]


def test_a_fact_cited_elsewhere_is_still_in_the_set(tmp_path):
    out = _check(tmp_path, _DRAFT, [_ADR_ELEC, _ADR_CHW, _STE_CHW])
    verdicts = [s["verdict"] for s in out]
    assert verdicts[0] == "supported"
    assert verdicts[1] == "unsupported_claim" and "F5@mv_summary-82f881" in out[1]["why"]
    assert verdicts[2] == "unsupported_claim" and "F3@mv_summary-82f881" in out[2]["why"]
    # the true ranks, with the same facts cited
    true = (
        "- Stephen chilledwater shows ± 570,988 [F5@mv_summary-4fe8ac].\n"
        "- Adrianne chilledwater: ± 1,165,919, the widest band [F3@mv_summary-82f881]."
    )
    assert [s["verdict"] for s in _check(tmp_path, true, [_STE_CHW, _ADR_CHW])] == [
        "supported",
        "supported",
    ]


def test_one_point_verdict_why_names_the_trusted_points_fact(judge):
    f1 = _f(
        "F1@sensor_trust-de77fb",
        "trust_summary",
        "ds-synthetic-chw-plant-staging: 9 points on PLANT__left_on judged (rules view: as the "
        "rule runner reads its inputs): 8 trusted, 0 suspect, 1 untrusted; 1 below the trust "
        "floor 0.5",
        facility_id="ds-synthetic-chw-plant-staging",
    )
    _t, verdict, why = _last(judge, "The power input is trusted. [F1@sensor_trust-de77fb]", f1)
    assert verdict == "unsupported_claim"
    assert "trusted_points fact (its T1 fact)" in str(why)


# --------------------------------------------------------------------------- 4. round 18b

_NULL_TLDR = (
    "TL;DR: AHU__excess_oa_no_flow is flagged for excess outside air like its sibling, but its "
    "OA-cost airflow is null, so its excess-OA energy totals are null [F5@rule_run-53a917]."
)


@pytest.mark.parametrize(
    "draft,ok",
    [
        (_NULL_TLDR, True),  # r18 dev false strip: "are null" against "not costed"
        ("Its kBtu cost totals are null [F5@rule_run-53a917].", True),
        ("AHU__excess_oa_no_flow's excess-OA cost was declined: no trended supply airflow "
         "[F5@rule_run-53a917].", True),
        ("AHU__excess_oa_no_flow's excess outside air is costed in kBtu [F5@rule_run-53a917].",
         False),
        ("AHU__excess_oa_no_flow has trended supply airflow, so its excess outside air is costed "
         "in kBtu [F5@rule_run-53a917].", False),
    ],
)  # fmt: skip
def test_null_and_not_costed_agree(judge, draft, ok):
    assert _ok(judge, draft, F5_EOA) == ok, _last(judge, draft, F5_EOA)
