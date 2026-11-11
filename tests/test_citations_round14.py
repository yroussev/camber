"""Round 14 of the citation checker (PLAN-CITE-EVAL, measured regime).

1. Fact texts state what hosts quoted from the result's JSON with no fact stating it: a
   finding's energy and cost totals (``totals: oa_cost_excess_cool_kbtu (oa cost excess cool)
   6,182 kBtu``), what a rule needs and reads and when its parameters apply (an inputs fact,
   ``I1@finding_detail`` / ``I<k>@rule_params``), and a reference comparison's RMSE in the
   point's unit, listed with its bias.
2. A rule, metric or check said to need an input needs a cited fact that says it is needed.
3. Strip causes on naive drafts read: a date without a year or with the month named ("5/12",
   "12 May 2025", "11-08 03:00"), "13 h", "starts 0.1 times/day", a figure compared "against
   a threshold", a threshold listed with results, "running" as "run", a possessive before a
   share's noun.
4. An order in time ("followed by", "preceded by") needs both things' dated facts cited.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.capabilities._cap_rule_params import rule_params  # noqa: E402
from camber.capabilities._cap_sensor_trust import _reference_fact  # noqa: E402
from camber.capabilities._common import (  # noqa: E402
    _metric_flags,
    _metric_totals,
    finding_fact,
    rule_inputs_fact,
)
from tools.cite_eval import facttable  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402


@pytest.fixture(scope="module")
def judge():
    return Judge(tempfile.mkdtemp(prefix="cite14-"))


def _ok(judge, draft, *facts) -> bool:
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
    return bool(r["grounded"])


# --------------------------------------------------------------------------- 1. fact texts


def test_finding_fact_states_energy_and_cost_totals():
    metrics = {
        "oa_cost_excess_cool_kbtu": 6182.372,
        "oa_cost_excess_heat_kbtu": 426.921,
        "oa_cost_excess_cool_btu_per_cfm": 584.17,  # a rate: not a total
        "annual_cost_usd": -12.5,
        "oa_cost_flagged": True,
        "waste_kwh": None,
    }
    said = _metric_totals(metrics)
    assert said == (
        "totals: annual_cost_usd (annual cost) -$12.5, oa_cost_excess_cool_kbtu (oa cost excess "
        "cool) 6,182 kBtu, oa_cost_excess_heat_kbtu (oa cost excess heat) 427 kBtu"
    )
    assert _metric_flags(metrics) == "oa_cost_flagged true; " + said + "; null: waste_kwh"
    many = {f"m{i}_kwh": float(i) for i in range(6)}
    assert _metric_totals(many).endswith("m3_kwh (m3) 3 kWh and 2 more")
    assert _metric_totals({"n_valid": 884}) == ""


TOTALS = {
    "id": "F3@rule_run-b471d7",
    "kind": "finding",
    "value": "fault",
    "equip": "AHU-1",
    "rule": "outdoor_air_fraction",
    "facility_id": "site-a",
    "text": '"AHU-1" outdoor_air_fraction (fault): "AHU-1": OAF median 45%; oa_cost_flagged true; '
    "totals: oa_cost_excess_cool_kbtu (oa cost excess cool) 6,182 kBtu, oa_cost_excess_heat_kbtu "
    "(oa cost excess heat) 427 kBtu",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("AHU-1's excess outdoor air cost 6,182 kBtu of cooling and 427 kBtu of heating", True),
        ("AHU-1 reports excess-OA cooling and heating energy totals", True),
        ("AHU-1's excess outdoor air cost 427 kBtu of cooling", False),
        ("AHU-1's excess outdoor air cost 6,182 kBtu of heating", False),
        ("AHU-1's excess outdoor air cost 6,182 kWh of cooling", False),
        ("AHU-1's excess outdoor air cost 6,200 kBtu of cooling", False),
    ],
)
def test_totals_are_read_with_what_they_total(judge, draft, ok):
    assert _ok(judge, f"{draft} [F3@rule_run-b471d7].", TOTALS) == ok


def test_finding_fact_carries_the_totals():
    row = {
        "equip": "AHU-1",
        "rule": "outdoor_air_fraction",
        "severity": "fault",
        "finding_id": "outdoor_air_fraction@AHU-1",
        "summary": "AHU-1: OAF median 45%",
        "metrics": {"oa_cost_excess_cool_kbtu": 6182.372},
    }
    text = finding_fact("F2@rule_run", row)["text"]
    assert text.endswith("; totals: oa_cost_excess_cool_kbtu (oa cost excess cool) 6,182 kBtu")


class _Staging:
    roles_required = ("power",)
    roles_optional = ("chw_flow", "compressor_status")


NOTES = [
    {"name": "low_load_pct", "note": "Needs CHW flow and temperatures. Severity is fixed in code."},
    {"name": "run_frac", "note": "Used only when no run status is mapped."},  # a negation
    {"name": "min_effort_pct", "note": "Used only when a fan speed is trended."},
]


def test_rule_inputs_fact_states_needs_reads_and_conditions():
    f = rule_inputs_fact("I1@finding_detail", "chiller_staging", _Staging(), NOTES)
    assert f["kind"] == "rule_inputs" and f["value"] == "inputs"
    text = f["text"].replace("", "").replace("", "")
    assert text == (
        "chiller_staging inputs: needs power; reads chw_flow and compressor_status when mapped; "
        "parameter conditions: low_load_pct: needs CHW flow and temperatures; min_effort_pct: "
        "used only when a fan speed is trended"
    )
    assert rule_inputs_fact("I1@x", "r", None, []) is None


def test_rule_params_issues_inputs_facts_with_detail():
    from types import SimpleNamespace

    ctx = SimpleNamespace(allowed_facilities=frozenset(), store=None)
    facts = rule_params(ctx, {"rules": ["chiller_staging"]}).facts
    inputs = [f for f in facts if f["kind"] == "rule_inputs"]
    assert len(inputs) == 1 and inputs[0]["id"] == "I1@rule_params"
    assert "needs power" in inputs[0]["text"] and "low_load_pct: " in inputs[0]["text"]
    plain = rule_params(ctx, {"rules": ["chiller_staging"], "detail": False}).facts
    assert not [f for f in plain if f["kind"] == "rule_inputs"]


INPUTS = {
    "id": "I1@finding_detail-fd0763",
    "kind": "rule_inputs",
    "value": "inputs",
    "rule": "chiller_staging",
    "text": "chiller_staging inputs: needs power; reads chw_supply_temp, chw_return_temp, chw_flow "
    "and compressor_status when mapped; parameter conditions: low_load_pct: needs CHW flow and "
    "temperatures",
}
FLAT = {
    "id": "L1@finding_detail-fd0763",
    "kind": "flat_point",
    "value": 600,
    "equip": "PLANT-1",
    "role": "chw_flow",
    "text": '"PLANT-1" "chw_flow" never moves over the window: all 2,016 valid samples from '
    "2025-07-07 00:00 to 2025-07-27 23:45 read 600, so it is flat for the whole window; "
    "chiller_staging reads it",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("The low-load figure needs CHW flow [I1@finding_detail-fd0763]", True),
        ("chiller_staging needs chiller power [I1@finding_detail-fd0763]", True),
        ("chiller_staging reads chw_flow when it is mapped [I1@finding_detail-fd0763]", True),
        ("The valve needs an inspection [I1@finding_detail-fd0763]", True),  # advice: not read
        ("You would need a flow meter to tell [I1@finding_detail-fd0763]", True),  # an aside
        ("The low-load figure needs CHW flow [L1@finding_detail-fd0763]", False),  # reads, no need
        ("The low-load figure needs condenser water flow [I1@finding_detail-fd0763]", False),
        ("The low-load figure needs outdoor air temperature [I1@finding_detail-fd0763]", False),
        ("chiller_staging needs compressor_status [I1@finding_detail-fd0763]", False),
        ("chiller_staging needs chw_supply_temp [I1@finding_detail-fd0763]", False),
        ("chiller_staging requires occupancy [I1@finding_detail-fd0763]", False),
    ],
)
def test_an_input_said_needed_needs_a_fact_that_says_so(judge, draft, ok):
    assert _ok(judge, f"{draft}.", INPUTS, FLAT) == ok


def test_reference_fact_lists_the_rmse_with_the_bias():
    r = {
        "equip": "AHU-1",
        "role": "supply_air_temp",
        "reference": "duct logger",
        "unit": "°F",
        "verdict": "ok",
        "bias": 0.04,
        "rmse": 0.12,
        "n_samples": 384,
        "overlap_start": None,
        "overlap_end": None,
        "summary": "AHU-1 supply_air_temp: bias +0.0, RMSE 0.1",
    }
    text = _reference_fact("R1@sensor_trust", r)["text"]
    assert "; bias +0.0 °F, RMSE 0.1 °F: the bias is the median of the point minus" in text
    no_rmse = _reference_fact("R1@sensor_trust", {**r, "rmse": None})["text"]
    assert "; bias +0.0 °F is the median of the point minus" in no_rmse


REF = {
    "id": "R2@sensor_trust-a43916",
    "kind": "reference_comparison",
    "value": "ok",
    "equip": "AHU-2",
    "role": "supply_air_temp",
    "unit": "°F",
    "text": '"AHU-2" "supply_air_temp" against its configured reference "duct logger" agrees '
    "within the comparison's thresholds (passed): AHU-2 supply_air_temp: bias +0.0, RMSE 0.1; "
    "bias +0.0 °F, RMSE 0.1 °F: the bias is the median of the point minus the reference, so the "
    "point reads neither high nor low against it; 384 paired samples",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("AHU-2's supply-air sensor tracks the duct logger within 0.1 °F RMSE", True),
        ("AHU-2's supply-air sensor has an RMSE of 0.1 °F", True),
        ("AHU-2's supply-air sensor has a bias of 0.1 °F", False),
        ("AHU-2's supply-air sensor tracks the duct logger within 0.2 °F RMSE", False),
    ],
)
def test_rmse_is_told_from_the_bias(judge, draft, ok):
    assert _ok(judge, f"{draft} [R2@sensor_trust-a43916].", REF) == ok


# --------------------------------------------------------------------------- 3. strip causes

WINDOW = {
    "id": "F2@facilities-e1216b",
    "kind": "facility",
    "value": "active",
    "facility_id": "site-a",
    "text": 'site-a ("Test site A"): active, 24 points on 4 equipment, data from '
    "2025-05-12T00:00:00 to 2025-06-01T23:45:00",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("Data only covers 5/12 to 6/1", True),
        ("Data only covers May 12 to June 1", True),
        ("The window is 12 May to 1 June 2025", True),
        ("The window runs from May 12, 2025 to June 1st, 2025", True),
        ("The window runs from 5/12/2025 to 6/1/2025", True),
        ("The data runs from 05-12 to 06-01", True),
        ("Data only covers 5/13 to 6/1", False),
        ("Data only covers May 12 to June 2", False),
        ("The window is 12 May to 1 June 2024", False),
        ("The window runs from 5/12/2025 to 6/2/2025", False),
        ("3/4 of the points are on 4 equipment", False),  # a fraction, not a date
    ],
)
def test_dates_in_other_spellings(judge, draft, ok):
    assert _ok(judge, f"{draft} [F2@facilities-e1216b].", WINDOW) == ok


GAP = {
    "id": "G12@coverage-6e6654",
    "kind": "gap",
    "value": 13,
    "unit": "h",
    "equip": "Meter-1",
    "text": '"Meter-1" "energy_rate" has no valid sample from 2017-11-07 13:00 to 2017-11-08 '
    "03:00: a gap of 13 hours",
}
G16 = {
    "id": "G2@coverage-d923e8",
    "kind": "gap",
    "value": 16,
    "unit": "h",
    "equip": "Meter-1",
    "text": '"Meter-1" "energy_rate" has no valid sample from 2017-06-24 09:00 to 2017-06-25 '
    "02:00: a gap of 16 hours",
}
G17 = {
    "id": "G1@coverage-d923e8",
    "kind": "gap",
    "value": 17,
    "unit": "h",
    "equip": "Meter-1",
    "text": '"Meter-1" "energy_rate" has no valid sample from 2017-06-25 05:00 to 2017-06-25 '
    "23:00: a gap of 17 hours",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("- Meter-1: 13 h gap, 2017-11-07 13:00 to 11-08 03:00 [G12@coverage-6e6654].", True),
        ("- Meter-1: a 13 h gap [G12@coverage-6e6654].", True),
        ("- Meter-1: 14 h gap [G12@coverage-6e6654].", False),
        ("- Meter-1: 13 h gap, 2017-11-07 13:00 to 11-09 03:00 [G12@coverage-6e6654].", False),
        ("- Meter-1: 13 h gap, 2017-11-07 13:00 to 11-08 04:00 [G12@coverage-6e6654].", False),
        ("The 16 h gap was followed by a 17 h gap [G1@coverage-d923e8][G2@coverage-d923e8].", True),
        (
            "The 17 h gap was preceded by the 16 h gap [G1@coverage-d923e8][G2@coverage-d923e8].",
            True,
        ),
        (
            "The 17 h gap was followed by the 16 h gap [G1@coverage-d923e8][G2@coverage-d923e8].",
            False,
        ),
        ("It was followed by a 16 h gap on 2017-06-24 to 06-25 [G2@coverage-d923e8].", False),
    ],
)
def test_gaps_hours_dates_and_order(judge, draft, ok):
    assert _ok(judge, draft, GAP, G16, G17) == ok


STAGING = {
    "id": "F2@rule_run-aa8518",
    "kind": "finding",
    "value": "fault",
    "equip": "PLANT-1",
    "rule": "chiller_staging",
    "text": '"PLANT-1" chiller_staging (fault): "PLANT-1": 0.1 starts/day (threshold 6), running '
    "100% of the time, load-factor median 11% (66% of run hrs below 40%)",
}
CYCLING = {
    "id": "F3@rule_run-aa8518",
    "kind": "finding",
    "value": "fault",
    "equip": "PLANT-2",
    "rule": "chiller_staging",
    "text": '"PLANT-2" chiller_staging (fault): "PLANT-2": 13.2 starts/day (threshold 6), running '
    "21% of the time, load-factor median 100% (0% of run hrs below 40%)",
}


@pytest.mark.parametrize(
    "draft,ok",
    [
        ("PLANT-1 starts only 0.1 times/day [F2@rule_run-aa8518].", True),
        ("PLANT-2 starts 13.2 times a day against a threshold of 6 [F3@rule_run-aa8518].", True),
        ("PLANT-2 starts 6 times a day [F3@rule_run-aa8518].", False),
        ("PLANT-2 runs 13.2 times a day [F3@rule_run-aa8518].", False),
        (
            "It runs 100% of the time at a median 11% load, with 66% of its run hours below 40% "
            "load [F2@rule_run-aa8518].",
            True,
        ),
        ("66% of PLANT-1's run hours are below 40% load [F2@rule_run-aa8518].", True),
        ("11% of PLANT-1's run hours are below 40% load [F2@rule_run-aa8518].", False),
        ("PLANT-1 has a median load factor of 66% [F2@rule_run-aa8518].", False),
        ("Its median load factor is 40% [F2@rule_run-aa8518].", False),
    ],
)
def test_counts_thresholds_and_shares(judge, draft, ok):
    assert _ok(judge, draft, STAGING, CYCLING) == ok
