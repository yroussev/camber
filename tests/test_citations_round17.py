"""Round 17 of the citation checker (PLAN-CITE-EVAL, measured regime).

1. Fact texts state what hosts read from the result's JSON with no fact stating it: that a
   rule's occupied fan-on hours are all its occupied hours when the fan ran in every occupied
   sample (``n_occupied``), which points of a page are trusted (``T1@sensor_trust``), each
   equipment's point roles (coverage), a finding's hour counts (its labels fact) and how to
   calibrate each parameter (``rule_params`` with ``detail``).
2. Two causes joined on one "so" must each be a reason a cited fact gives; a dependence said of
   the list next to the sentence ("the findings below depend on it") needs a cited fact for
   each of its results; a trust verdict said of one point needs a fact about that point, not a
   summary's count; a run's result counts said under one of its units are the run's.
3. A cited fact's figure said to be the highest or lowest of its set ("the widest band"), or
   one of a set said to be the only one with something, is read against the session's other
   facts of that set (the ledger's peers), which check_citations now reads.
4. Narrower rules: a share "of those" in a fact is of the share before it, and "of the time" is
   a share of hours or samples; a fact's link from an outcome's cause to its evidence ("disagrees
   (warn): the point is suspect" and the comparison under it) and a cause in an outcome's
   subject ("a pass measured with that input is conditional"); "further" names no check; "more
   than one" is no count of 1.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._common import finding_fact, finding_labels  # noqa: E402
from camber.capabilities._ledger import FactLedger  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.airflow_rule import AirflowTracking  # noqa: E402
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite17-"))


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


def _vav(fan_off_occupied: int = 0) -> pd.DataFrame:
    idx = pd.date_range("2025-05-05", periods=240, freq="1h")
    occ = pd.Series(((idx.hour >= 8) & (idx.hour < 18)).astype(float), index=idx)
    fan = pd.Series(1.0, index=idx)
    if fan_off_occupied:
        fan[occ[occ > 0].index[:fan_off_occupied]] = 0.0
    return pd.DataFrame(
        {
            Role.AIRFLOW: pd.Series(600.0, index=idx),
            Role.AIRFLOW_SP: pd.Series(1000.0, index=idx),
            Role.OCCUPANCY: occ,
            Role.SUPPLY_FAN_STATUS: fan,
        }
    )


def test_airflow_tracking_counts_its_occupied_samples():
    f = AirflowTracking().analyze("VAV-1", _vav())
    assert f.metrics["n_active"] == f.metrics["n_occupied"] == 100
    f = AirflowTracking().analyze("VAV-1", _vav(fan_off_occupied=7))
    assert (f.metrics["n_active"], f.metrics["n_occupied"]) == (93, 100)
    no_occ = _vav().drop(columns=[Role.OCCUPANCY])
    assert "n_occupied" not in AirflowTracking().analyze("VAV-1", no_occ).metrics


_ROW = {
    "equip": "VAV1__not_responding",
    "rule": "airflow_tracking",
    "severity": "fault",
    "finding_id": "airflow_tracking@VAV1__not_responding",
    "summary": "VAV1__not_responding: airflow off setpoint 87% of occupied fan-on hours",
    "metrics": {"n_active": 180, "n_occupied": 180, "off_setpoint_pct": 87.22},
}


def test_finding_fact_says_when_fan_on_hours_are_all_occupied_hours():
    said = "the fan ran in all 180 occupied samples it judged, so its occupied fan-on hours"
    assert said in finding_fact("F4@rule_run-aaaaaa", _ROW)["text"]
    other = {**_ROW, "metrics": {**_ROW["metrics"], "n_active": 170}}
    assert "the fan ran" not in finding_fact("F4@rule_run-aaaaaa", other)["text"]


def test_labels_fact_gives_the_hour_counts():
    row = {
        "equip": "AHU-1",
        "rule": "economizer_high_limit",
        "finding_id": "economizer_high_limit@AHU-1",
        "metrics": {"fan_gate": "fan status", "oa_cost_cool_h": 81.5, "oa_cost_economizing_h": 0.0},
    }
    text = finding_labels("M1@rule_run-aaaaaa", row)["text"]
    assert text.endswith("hours: oa_cost_cool_h 81.5; oa_cost_economizing_h 0")
    hours_only = {**row, "metrics": {"oa_cost_cool_h": 81.5}}
    assert finding_labels("M1@rule_run-aaaaaa", hours_only) is not None


@pytest.fixture(scope="module")
def fixture_ctx():
    import pathlib

    st = fx.make_store(pathlib.Path(tempfile.mkdtemp(prefix="cite17s-")), meta=None)
    return CallContext(store=st.root, allowed_facilities=frozenset([fx.FID]))


def test_sensor_trust_names_the_trusted_points(fixture_ctx):
    res = default_registry().call("sensor_trust", {"facility_id": fx.FID}, fixture_ctx)
    (t1,) = [f for f in res.facts if f["kind"] == "trusted_points"]
    assert t1["id"].startswith("T1@sensor_trust")
    assert '"DemoAHU" "supply_air_temp"' in t1["text"]
    assert '"DemoAHU2" "heat_valve"' not in t1["text"]  # suspect
    assert t1["text"].endswith("are trusted (16 trusted point(s) on this page)")


def test_equipment_coverage_names_its_point_roles(fixture_ctx):
    res = default_registry().call(
        "coverage", {"facility_id": fx.FID, "group_by": "equip"}, fixture_ctx
    )
    f2 = next(f for f in res.facts if f["kind"] == "equip_coverage")
    assert 'over 9 points (roles "cool_valve", "duct_static", "heat_valve",' in f2["text"]
    assert '"supply_air_temp" and "supply_fan_status"); worst' in f2["text"]


def test_rule_params_detail_says_how_to_calibrate():
    ctx = CallContext(store=tempfile.mkdtemp(prefix="cite17p-"), allowed_facilities=frozenset())
    reg = default_registry()
    res = reg.call("rule_params", {"rules": ["outdoor_air_fraction"], "detail": True}, ctx)
    (c1,) = [f for f in res.facts if f["kind"] == "parameter_calibrate"]
    assert c1["text"].startswith("how to calibrate outdoor_air_fraction's parameters: ")
    assert "cooling_cutoff_f: Set it to the economizer high limit of the sequence" in c1["text"]
    res = reg.call("rule_params", {"rules": ["outdoor_air_fraction"], "detail": False}, ctx)
    assert not [f for f in res.facts if f["kind"] == "parameter_calibrate"]
    res = reg.call("rule_params", {"detail": True}, ctx)  # no rule named: a whole page, none
    assert not [f for f in res.facts if f["kind"] == "parameter_calibrate"]


def test_ledger_peers_are_the_same_rule_on_the_same_facility():
    led = FactLedger()
    led.record(
        [
            _f("F1@m-a", "finding", "x", rule="mv_savings", facility_id="s"),
            _f("F2@m-a", "finding", "y", rule="mv_savings", facility_id="s"),
            _f("F3@m-a", "finding", "z", rule="mv_baseline", facility_id="s"),
            _f("F4@m-b", "finding", "w", rule="mv_savings", facility_id="t"),
            _f("N1@m-a", "notice", "n"),
        ]
    )
    assert led.peers(["F1@m-a"]) == ["F2@m-a"]
    assert led.peers(["F3@m-a"]) == [] and led.peers(["N1@m-a"]) == []
    view = led.view(["F1@m-a", *led.peers(["F1@m-a"])])
    assert view.peers(["F1@m-a"]) == ["F2@m-a"]


# --------------------------------------------------------------------------- 2-4. the dev units

_N1_MV = _f(
    "N1@mv_summary-d52a75",
    "notice",
    "5 saving(s) rest on a baseline that does not meet G14 acceptance: for information only, not "
    "a verified saving.",
)
_N2_MV = _f(
    "N2@mv_summary-d52a75",
    "notice",
    "8 saving(s) are in the meter's own units (its trended rate summed over hours; the run "
    "config names no energy unit); add a units block to the run config to report kWh or kBtu.",
)
_LEAK = _f(
    "F2@rule_run-f69f19",
    "finding",
    '"AHU__sat_bias" leaking_valve (fault): "AHU__sat_bias": with both valves shut, air shifts '
    "(median +3.4F beyond 2F fan heat); HW-leak 86% / CHW-leak 0% of both-valves-shut samples "
    "(worst: HW (heating) coil); conditional on sensor_drift:supply_air_temp warn (AHU__sat_bias "
    "supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 days "
    "-- biased +3.0) on AHU__sat_bias; fan_gated true",
    rule="leaking_valve",
    equip="AHU__sat_bias",
    facility_id="ds-synthetic-ahu-sensor-bias",
)
_RESET = _f(
    "F4@rule_run-f69f19",
    "finding",
    '"AHU__sat_bias" supply_air_reset_compliance (warn): "AHU__sat_bias": SAT median 55.0°F vs '
    "configured target 57.1°F; below target 54% of hours (mean gap +3.1°F) -- reheat/energy "
    "opportunity; conditional on sensor_drift:supply_air_temp warn (AHU__sat_bias "
    "supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 samples / 4 "
    "days -- biased +3.0) on AHU__sat_bias",
    rule="supply_air_reset_compliance",
    equip="AHU__sat_bias",
    facility_id="ds-synthetic-ahu-sensor-bias",
)
_TRUST_SUMMARY = _f(
    "F1@sensor_trust-d6c027",
    "trust_summary",
    "ds-synthetic-ahu-sensor-bias: 4 supply_air_temp points judged (rules view: as the rule "
    "runner reads its inputs): 3 trusted, 1 suspect, 0 untrusted; 0 below the trust floor 0.5",
    facility_id="ds-synthetic-ahu-sensor-bias",
)
_TRUSTED = _f(
    "T1@sensor_trust-d6c027",
    "trusted_points",
    'ds-synthetic-ahu-sensor-bias: "LOGGER__sat_bias" "supply_air_temp", "LOGGER__healthy" '
    '"supply_air_temp" and "AHU__healthy" "supply_air_temp" are trusted (3 trusted point(s) on '
    "this page)",
)
_R2_NORTH = _f(
    "R2@sensor_trust-a17ff9",
    "reference_comparison",
    '"AHU-2" "supply_air_temp" against its configured reference "handheld reference logger" '
    "agrees within the comparison's thresholds (passed): AHU-2 supply_air_temp: bias +0.0, drift "
    "not evaluated, RMSE 0.1, r=1.00 over 384 samples / 4 days -- tracks reference; bias +0.0 "
    "°F, RMSE 0.1 °F: the bias is the median of the point minus the reference, so the point reads "
    "neither high nor low against it; drift not evaluated because the overlap spans 4.0 days, "
    "under the 28 days a drift rate needs; 384 paired samples from 2025-05-19 00:00:00 to "
    "2025-05-22 23:45:00",
    equip="AHU-2",
)
_RUN_NORTH = _f(
    "F1@rule_run-13788a",
    "rule_run",
    "1 rules were run on 2 equipment of site-north and 1 of them returned a result, 2 "
    "rule-equipment result(s) in all: 0 fault, 0 warn, 0 info and 2 ok; 0 rule-equipment "
    "pair(s) (0 rule(s)) skipped for missing inputs",
    facility_id="site-north",
)
_RUN_EOA = _f(
    "F1@rule_run-9d37e7",
    "rule_run",
    "5 rules were run on 3 equipment of ds-synthetic-ahu-excess-oa and 4 of them returned a "
    "result, 10 rule-equipment result(s) in all: 6 fault, 0 warn, 1 info and 3 ok; 1 "
    "rule-equipment pair(s) (1 rule(s)) skipped for missing inputs: those pairs were not judged",
    facility_id="ds-synthetic-ahu-excess-oa",
)


def _mv(fid, meter, saving, pct, band, *, info=True):
    tail = "; the baseline does not meet G14 acceptance, so this saving is for information only"
    return _f(
        fid,
        "finding",
        f'"{meter}" mv_savings (info): "{meter}": avoided energy {saving} ({pct}) ± {band} at 90% '
        "over 340 reporting days; baseline coverage in_range; declined false; energy in the "
        "meter's own units (its trended rate summed over hours; the run config names no energy "
        f"unit){tail if info else ''}; reporting period 2017-01-01 to 2017-12-31 (the year 2017)",
        rule="mv_savings",
        equip=meter,
        facility_id="ds-bdg2-fox",
    )


_ADR_CHW = _mv("F3@mv_summary-82f881", "Fox_assembly_Adrianne__chilledwater", "-1,615,675",
               "-15.8%", "1,165,919")  # fmt: skip
_ADR_ELEC = _mv("F5@mv_summary-82f881", "Fox_assembly_Adrianne__electricity", "-293,693",
                "-15.6%", "208,671")  # fmt: skip
_AUD_CHW = _mv("F6@mv_summary-82f881", "Fox_assembly_Audrey__chilledwater", "26,186", "2.4%",
               "61,282", info=False)  # fmt: skip
_STE_CHW = _mv("F5@mv_summary-4fe8ac", "Fox_lodging_Stephen__chilledwater", "30,601", "0.6%",
               "570,988", info=False)  # fmt: skip
_STA_CHW = _mv("F2@mv_summary-4fe8ac", "Fox_lodging_Stephan__chilledwater", "75,518", "2.9%",
               "235,734", info=False)  # fmt: skip
_ADR_BASE = _f(
    "F2@mv_summary-82f881",
    "finding",
    '"Fox_assembly_Adrianne__chilledwater" mv_baseline (info): '
    '"Fox_assembly_Adrianne__chilledwater": 2P baseline, R2 0.62, CV(RMSE) 38.1% over 354 days '
    "-- does not meet daily acceptance (G14 criteria; the 30% daily CV(RMSE) tier is CAMBER's): "
    "CV(RMSE) 38.1% > 30%; R2 0.62 < 0.75; accept false",
    rule="mv_baseline",
    equip="Fox_assembly_Adrianne__chilledwater",
    facility_id="ds-bdg2-fox",
)
_STE_BASE = _f(
    "F9@mv_summary-4fe8ac",
    "finding",
    '"Fox_lodging_Stephen__chilledwater" mv_baseline (ok): "Fox_lodging_Stephen__chilledwater": '
    "3P cooling baseline, R2 0.86, CV(RMSE) 28.0% over 366 days -- meets daily acceptance (G14 "
    "criteria; the 30% daily CV(RMSE) tier is CAMBER's); accept true",
    rule="mv_baseline",
    equip="Fox_lodging_Stephen__chilledwater",
    facility_id="ds-bdg2-fox",
)

#: the dev escapes of R16-DEV-ESCAPES (stages 5-6) the checker closes: never supported
_ESCAPES = [
    (
        "5 two causes on one so",
        "3. Five savings rest on baselines that fail G14 acceptance, and 8 savings are in the "
        "meter's own units, so these are for information only, not verified changes "
        "[N1@mv_summary-d52a75][N2@mv_summary-d52a75].",
        [_N1_MV, _N2_MV],
    ),
    (
        "6 the findings below depend on it",
        "- [ ] Check the supply air temp sensor on AHU__sat_bias first: it shows bias +3.0 over "
        "384 samples / 4 days, and the findings below depend on it [F2@rule_run-f69f19].\n"
        "- [ ] Confirm the leaking_valve fault on AHU__sat_bias [F2@rule_run-f69f19].\n"
        "- [ ] Review the reset compliance warn on AHU__healthy.",
        [_LEAK],
    ),
    (
        "7 rated trusted from a count",
        "The logger itself is rated trusted [F1@sensor_trust-d6c027].",
        [_TRUST_SUMMARY],
    ),
    (
        "9 the rule passed from a run count",
        "2. AHU-2: the supply_air_control rule passed, and its sensor tracks the handheld "
        "reference with bias +0.0 °F and RMSE 0.1 °F [R2@sensor_trust-a17ff9] "
        "[F1@rule_run-13788a].",
        [_R2_NORTH, _RUN_NORTH],
    ),
    (
        "10 run counts under one AHU",
        "- [ ] Check the third AHU: the run counted 6 fault, 0 warn, 1 info and 3 ok results "
        "[F1@rule_run-9d37e7].",
        [_RUN_EOA],
    ),
    (
        "11 the only meter",
        "- **Adrianne chilledwater:** It is the only meter with a large 2017 increase in use, "
        "−1,615,675 or −15.8% ± 1,165,919 [F3@mv_summary-82f881], and its 2P baseline fails "
        "acceptance (R² 0.62, CV(RMSE) 38.1%) [F2@mv_summary-82f881], so the figure is for "
        "information only.",
        [_ADR_CHW, _ADR_BASE, _ADR_ELEC, _AUD_CHW],
    ),
    (
        "12 the widest band",
        "- **Stephen chilledwater:** It shows only 0.6% (30,601) against a very wide ± 570,988 "
        "[F5@mv_summary-4fe8ac], the widest band in absolute terms, even though its baseline "
        "passes (R² 0.86, CV(RMSE) 28.0%, the loosest passing fit) [F9@mv_summary-4fe8ac].",
        [_STE_CHW, _STE_BASE, _STA_CHW, _ADR_CHW],
    ),
]


def _first(judge, draft, facts):
    """The verdict of the draft's first sentence (a checklist's first item)."""
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": facts,
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return r["sentences"][0]


@pytest.mark.parametrize("item,draft,facts", _ESCAPES, ids=[e[0] for e in _ESCAPES])
def test_round17_dev_escape_is_not_supported(judge, item, draft, facts):
    _text, verdict, why = _first(judge, draft, facts)
    assert verdict != "supported", (item, why)


_N1_NORTH = _f(
    "N1@rule_run-2651dd",
    "notice",
    "2 listed finding(s) rest on an input whose configured sensor reference disagrees with it "
    "(conditional_on names the comparison): treat each as a possible sensor problem first and "
    "check that sensor before acting on the equipment. sensor_trust shows the comparison. 1 ok "
    "result(s) below min_severity rest on such an input too and are not listed (a lower "
    "min_severity lists them); a pass measured with that input is conditional as well.",
)
_R1_SB = _f(
    "R1@sensor_trust-d6c027",
    "reference_comparison",
    '"AHU__sat_bias" "supply_air_temp" against its configured reference "duct logger" disagrees '
    "(warn): the point is suspect, and a finding that rests on it is conditional on this sensor: "
    "AHU__sat_bias supply_air_temp: bias +3.0, drift not evaluated, RMSE 3.0, r=1.00 over 384 "
    "samples / 4 days -- biased +3.0; bias +3.0 °F, RMSE 3.0 °F: the bias is the median of the "
    "point minus the reference, so the point reads high against it; drift not evaluated because "
    "the overlap spans 4.0 days, under the 28 days a drift rate needs; 384 paired samples from "
    "2025-05-19 00:00:00 to 2025-05-22 23:45:00",
    equip="AHU__sat_bias",
)
_K1 = _f(
    "K1@rule_run-9693ea",
    "skipped_rules",
    "3 rule(s) skipped for missing inputs on ds-synthetic-g36-vav-fleet, so not judged: "
    "airflow_tracking (missing airflow, airflow_sp); reheat_minimization_g36 (missing "
    "heat_valve, airflow, airflow_min_sp or airflow_sp); unmet_setpoint_hours (missing "
    "space_temp)",
)


def _reheat(fid, vav, hours, above):
    return _f(
        fid,
        "finding",
        f'"{vav}" reheat_minimization_g36 (fault): "{vav}": reheating {hours}% of occupied hours; '
        f"of those, {above}% with airflow above minimum outside the high heating loop (G36 "
        "§5.6.5 would hold min flow until deep heating demand)",
        rule="reheat_minimization_g36",
        equip=vav,
        facility_id="ds-synthetic-g36-vav-fleet",
    )


_SAC = _f(
    "F6@rule_run-bd85cb",
    "finding",
    '"AHU__healthy" supply_air_control (ok): "AHU__healthy": SAT off setpoint 0% of occupied '
    'running hours (mean |Δ| 0.1°F; warm 0%, cold 0%); gates: occupancy_gate "trended occupancy"',
    rule="supply_air_control",
    equip="AHU__healthy",
    facility_id="ds-synthetic-ahu-sensor-bias",
)
_FLEET = _f(
    "F2@rule_run-c0ffee",
    "finding",
    '"<fleet>" chiller_staging_fleet (warn): fleet: 3 chillers, 36% of 780 multi-chiller samples '
    "(195 h) over-staged (median 2 running, capacity 300 kW observed); a redundant chiller "
    "could stage off",
    rule="chiller_staging_fleet",
    equip="<fleet>",
)

#: the true dev sentences round 16's rules (and the share-whole why) stripped: supported now
_STRIPS = [
    (
        "s5 u35 the ok result is conditional too",
        "The ok result also rests on that input, so it is conditional too [N1@rule_run-2651dd].",
        [_N1_NORTH],
    ),
    (
        "s5 u0 reads high, so suspect",
        "Duct-logger check: AHU__sat_bias reads 3.0 °F high (RMSE 3.0, r=1.00) over a 4-day "
        "overlap, so it's suspect [R1@sensor_trust-d6c027].",
        [_R1_SB],
    ),
    (
        "s6 u11 three further rules",
        "Three further rules were skipped for missing inputs ([K1@rule_run-9693ea]).",
        [_K1],
    ),
    (
        "s5 of those hours",
        "VAV4__heat_cool reheats in 99% of occupied hours. In 65% of those hours airflow is "
        "above minimum outside the high heating loop [F21@rule_run-42681f].",
        [_reheat("F21@rule_run-42681f", "VAV4__heat_cool", 99, 65)],
    ),
    (
        "s6 of those hours",
        "In 86% of those hours its airflow is above minimum while outside the high heating loop "
        "[F3@rule_run-66609c].",
        [_reheat("F3@rule_run-66609c", "VAV1__heat_cool", 93, 86)],
    ),
    (
        "s5 0% of the time",
        "- AHU__healthy: SAT off setpoint 0% of the time [F6@rule_run-bd85cb].",
        [_SAC],
    ),
    (
        "s5 36% of the time, more than one chiller",
        "Over-staging: 36% of the time when more than one chiller was running (195 hours) a "
        "redundant chiller could have staged off [F2@rule_run-c0ffee].",
        [_FLEET],
    ),
]


@pytest.mark.parametrize("item,draft,facts", _STRIPS, ids=[s[0] for s in _STRIPS])
def test_round17_dev_strip_is_supported_again(judge, item, draft, facts):
    _text, verdict, why = _last(judge, draft, *facts)
    assert verdict == "supported", (item, why)


#: the fact-text escapes, said against the round-17 fact texts (true there)
_FACT_TEXT = [
    (
        "13 87% of occupied hours",
        "VAV1__not_responding missed its airflow setpoint in 87% of occupied hours, always with "
        "too little air [F4@rule_run-f5613b].",
        [
            _f(
                "F4@rule_run-f5613b",
                "finding",
                '"VAV1__not_responding" airflow_tracking (fault): "VAV1__not_responding": airflow '
                "off setpoint 87% of occupied fan-on hours (mean |err| 52%; starved 87%, over "
                "0%); the fan ran in all 180 occupied samples it judged, so its occupied fan-on "
                'hours are all its occupied hours; gates: fan_gate "fan status", occupancy_gate '
                '"trended occupancy"',
                rule="airflow_tracking",
                equip="VAV1__not_responding",
                facility_id="ds-synthetic-g36-vav-fleet",
            )
        ],
    ),
    (
        "14 the cooling cutoff's calibration",
        "The cooling cutoff is meant to be set to the economizer high limit of the sequence "
        "[C1@rule_params-25e668].",
        [
            _f(
                "C1@rule_params-25e668",
                "parameter_calibrate",
                "how to calibrate outdoor_air_fraction's parameters: cooling_cutoff_f: Set it to "
                "the economizer high limit of the sequence: above it outdoor air is a cooling "
                "penalty, not free cooling",
                rule="outdoor_air_fraction",
            )
        ],
    ),
    (
        "15 each has 9 points, including",
        "AHU__healthy has 9 points, including duct static, supply-air temperature and both "
        "reset-request signals [F2@coverage-163ed5].",
        [
            _f(
                "F2@coverage-163ed5",
                "equip_coverage",
                '"AHU__healthy": mean coverage 100.0% of the window over 9 points (roles '
                '"duct_static", "duct_static_sp", "oat", "occupancy", "sat_reset_requests", '
                '"static_pressure_requests", "supply_air_temp", "supply_air_temp_sp" and '
                '"supply_fan_status"); worst "duct_static" at 100.0%',
                equip="AHU__healthy",
            )
        ],
    ),
    (
        "7 rated trusted, from the trusted-points fact",
        "The logger itself is rated trusted [T1@sensor_trust-d6c027].",
        [_TRUSTED],
    ),
]


@pytest.mark.parametrize("item,draft,facts", _FACT_TEXT, ids=[s[0] for s in _FACT_TEXT])
def test_round17_fact_text_supports_the_true_sentence(judge, item, draft, facts):
    _text, verdict, why = _last(judge, draft, *facts)
    assert verdict == "supported", (item, why)


# --------------------------------------------------------------------------- 2-4. the rules


@pytest.mark.parametrize(
    "draft,ok",
    [
        (
            "Five savings rest on baselines that fail G14 acceptance, so they are for "
            "information only",
            True,
        ),
        (
            "Five savings rest on baselines that fail G14 acceptance, and they are not verified, "
            "so they are for information only",
            True,
        ),
        (
            "Five savings rest on baselines that fail G14 acceptance, and 8 savings are in the "
            "meter's own units, so these are not verified",
            False,
        ),
    ],
)
def test_each_joined_cause_must_be_a_given_reason(judge, draft, ok):
    cited = f"{draft} [N1@mv_summary-d52a75][N2@mv_summary-d52a75]."
    assert _ok(judge, cited, _N1_MV, _N2_MV) == ok


def test_a_contrast_is_no_second_cause(judge):
    draft = (
        "AHU__sat_bias reads 3.0 °F high against the duct logger, while the other point matches "
        "it, so any finding resting on AHU__sat_bias's sensor is conditional "
        "[R1@sensor_trust-d6c027]."
    )
    assert _ok(judge, draft, _R1_SB)


@pytest.mark.parametrize(
    "facts,lines,ok",
    [
        ([_LEAK], 1, False),  # one finding's fact
        ([_LEAK, _RESET], 2, True),
        ([_LEAK, _RESET], 3, False),  # the list holds a third
        ([_N1_NORTH], 2, True),  # a fact that counts them: "2 listed finding(s) rest on"
    ],
)
def test_the_findings_below_need_a_fact_for_each(judge, facts, lines, ok):
    cites = "".join(f"[{f['id']}]" for f in facts)
    draft = f"Check that sensor first: the findings below depend on it {cites}.\n"
    draft += "".join(f"- finding {i + 1}\n" for i in range(lines))
    assert (_first(judge, draft, facts)[1] == "supported") == ok


def test_a_dependence_of_no_list_is_not_read_here(judge):
    # "its findings are conditional on that sensor": no list next to it (r10, labelled true)
    draft = "AHU__sat_bias reads 3.0 °F high, so its findings are conditional on that sensor"
    assert _ok(judge, f"{draft} [R1@sensor_trust-d6c027].", _R1_SB)


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The logger itself is rated trusted", False),
        ("AHU__sat_bias's supply air temperature point is suspect", False),
        ("3 points are trusted", True),
        ("A single point is suspect", True),
        ("Most points are trusted", True),
        ("ds-synthetic-ahu-sensor-bias is untrusted-free", True),
        ("ds-synthetic-ahu-sensor-bias is untrusted free", True),
    ],
)
def test_a_verdict_of_one_point_needs_a_fact_about_it(judge, draft, ok):
    assert _ok(judge, f"{draft} [F1@sensor_trust-d6c027].", _TRUST_SUMMARY) == ok


def test_a_verdict_of_a_point_whole_summary_agrees(judge):
    one = _f(
        "F1@sensor_trust-aaaaaa",
        "trust_summary",
        "site-a: 2 supply_air_temp points judged (rules view): 2 trusted, 0 suspect, 0 untrusted",
    )
    assert _ok(judge, "The logger is trusted [F1@sensor_trust-aaaaaa].", one)


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("Check the third AHU: the run counted 6 fault, 0 warn, 1 info and 3 ok results", False),
        ("AHU__economizer: 6 fault, 0 warn, 1 info and 3 ok results", False),
        ("The run: 6 fault, 0 warn, 1 info and 3 ok results", True),
        ("ds-synthetic-ahu-excess-oa: 6 fault, 0 warn, 1 info and 3 ok results", True),
    ],
)
def test_a_runs_counts_are_not_one_units(judge, draft, ok):
    assert _ok(judge, f"{draft} [F1@rule_run-9d37e7].", _RUN_EOA) == ok


def test_a_one_unit_run_gives_that_units_counts(judge):
    run = _f(
        "F1@rule_run-aaaaaa",
        "rule_run",
        "3 rules were run on 1 equipment of site-a and 3 of them returned a result, 3 "
        "rule-equipment result(s) in all: 1 fault, 1 warn, 0 info and 1 ok; 0 rule-equipment "
        "pair(s) (0 rule(s)) skipped for missing inputs",
    )
    # the run names no unit (another rule reads that); its counts are not the run's of many
    why = _last(judge, "AHU-1: 1 fault, 1 warn and 1 ok results [F1@rule_run-aaaaaa].", run)[2]
    assert "the counts are the run's" not in why


@pytest.mark.parametrize(
    "draft,cited,ok",
    [
        ("Its band, ± 1,165,919, is the widest of the meters", _ADR_CHW, True),
        ("Its band, ± 570,988, is the widest of the meters", _STE_CHW, False),
        ("Fox_assembly_Audrey__chilledwater has the narrowest band, ± 61,282", _AUD_CHW, True),
        ("It has the narrowest band, ± 235,734", _STA_CHW, False),
        ("It is the only meter with a saving as large as 2.9%", _STA_CHW, True),
        ("It is the only meter with a saving of 2.4%", _AUD_CHW, False),  # Stephan's 2.9%
        ("It is the only meter with a large increase, -15.6%", _ADR_ELEC, False),
    ],
)
def test_a_rank_is_read_against_the_set(judge, draft, cited, ok):
    peers = [f for f in (_ADR_CHW, _ADR_ELEC, _AUD_CHW, _STE_CHW, _STA_CHW) if f is not cited]
    assert _ok(judge, f"{draft} [{cited['id']}].", cited, *peers) == ok


def test_a_rank_the_fact_says_or_a_narrowed_set_is_not_read(judge):
    point = _f(
        "P1@coverage-aaaaaa",
        "point_coverage",
        '"Meter-1" "energy": coverage 98.0% of the whole window (8,760 samples), 62 gap(s), the '
        "longest 17 hours",
        equip="Meter-1",
        subject={"facility_id": "site-a", "equip": "Meter-1"},
    )
    other = _f(
        "P2@coverage-aaaaaa",
        "point_coverage",
        '"Meter-2" "energy": coverage 98.7% of the whole window (8,760 samples), 115 gap(s), the '
        "longest 18 hours",
        equip="Meter-2",
        subject={"facility_id": "site-a", "equip": "Meter-2"},
    )
    said = "Meter-1 has 62 gaps, the longest 17 hours [P1@coverage-aaaaaa]."
    assert _ok(judge, said, point, other)  # the fact's own "the longest"
    said = "Its band, ± 570,988, is the widest of the chilled water meters [F5@mv_summary-4fe8ac]."
    why = _last(judge, said, _STE_CHW, _ADR_CHW)[2]
    assert "of the same set gives" not in why  # a set the sentence narrows: not read


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("In 65% of those hours airflow is above minimum", True),
        ("In 65% of occupied hours airflow is above minimum", False),
        ("Reheat runs 99% of the time", True),
        ("Reheat runs in 99% of the zones", False),
    ],
)
def test_shares_of_those_and_of_the_time(judge, draft, ok):
    fact = _reheat("F21@rule_run-42681f", "VAV4__heat_cool", 99, 65)
    assert _ok(judge, f"{draft} [F21@rule_run-42681f].", fact) == ok


def test_a_reason_chained_through_the_verdict_keeps_the_overlap_out(judge):
    # chaining "suspect <- disagrees (warn) <- its evidence" does not make the short overlap
    # (why drift went unmeasured) a reason for the verdict (round 16's escape stays closed)
    for draft, ok in (
        ("AHU__sat_bias reads high against the duct logger, so it is suspect", True),
        ("The comparison covers only 4 days, so the point is suspect", False),
        ("The point reads high, so drift was not evaluated", False),
    ):
        assert _ok(judge, f"{draft} [R1@sensor_trust-d6c027].", _R1_SB) == ok, draft


def test_further_names_no_check_and_a_scope_is_still_needed(judge):
    assert _ok(judge, "Three further rules were skipped [K1@rule_run-9693ea].", _K1)
    v1 = _f(
        "V1@rule_run-42681f",
        "caveat",
        '"VAV1__healthy", "VAV1__heat_cool" reheat_penalty caveat (given for 12 of its 48 '
        "results): no OAT and no primary/supply air temperature: reheat penalty not evaluated",
        rule="reheat_penalty",
        of=48,
    )
    # a decline given for some of the results needs the sentence's scope (round 16)
    assert not _ok(judge, "The reheat penalty was not evaluated [V1@rule_run-42681f].", v1)
    said = "The reheat penalty was not evaluated for 12 of its 48 results [V1@rule_run-42681f]."
    assert _ok(judge, said, v1)


def test_ranked_rule_needs_the_session_peers(tmp_path):
    """check_citations reads the ledger's peers of the cited facts (not passed by the caller)."""
    led = FactLedger()
    led.record([_STE_CHW, _ADR_CHW])
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset(), ledger=led)
    reg = default_registry()
    said = "Its band, ± 570,988, is the widest [F5@mv_summary-4fe8ac]."
    res = reg.call("check_citations", {"text": said, "detail": True}, ctx)
    assert res.data["grounded"] is False
    view = reg.ledger_view("check_citations", {"text": said}, led)
    ctx2 = CallContext(store=str(tmp_path), allowed_facilities=frozenset(), ledger=view)
    assert (
        reg.call("check_citations", {"text": said, "detail": True}, ctx2).data["grounded"] is False
    )
    alone = FactLedger()
    alone.record([_STE_CHW])
    ctx3 = CallContext(store=str(tmp_path), allowed_facilities=frozenset(), ledger=alone)
    assert reg.call("check_citations", {"text": said, "detail": True}, ctx3).data["grounded"]
