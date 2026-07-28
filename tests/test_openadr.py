"""Tests for the OpenADR report-schema mapping (camber.interop.openadr)."""

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.geb import demand_response  # noqa: E402
from camber.interop.openadr import openadr_report_json, to_openadr_report  # noqa: E402


def _dr():
    idx = pd.date_range("2026-07-15 00:00", periods=12, freq="1h")
    vals = [100.0] * 12
    for h in (4, 5, 6, 7):
        vals[h] = 60.0
    return demand_response(pd.Series(vals, index=idx), 100.0, event_start=idx[4], event_end=idx[7])


def _payloads(rep):
    return {p["type"]: p["values"][0] for p in rep["resources"][0]["intervals"][0]["payloads"]}


def test_maps_dr_result_to_report_structure():
    rep = to_openadr_report(_dr(), program_id="PROG-1", event_id="EV-42", client_name="HQ")
    assert rep["objectType"] == "REPORT" and rep["reportName"] == "DR_EVENT_PERFORMANCE"
    assert rep["programID"] == "PROG-1" and rep["eventID"] == "EV-42" and rep["clientName"] == "HQ"
    pl = _payloads(rep)
    assert pl == {"BASELINE": 400.0, "USAGE": 240.0, "REDUCTION": 160.0}  # 4h × (100 / 60 / 40)
    assert all(p["unit"] == "KWH" for p in rep["resources"][0]["intervals"][0]["payloads"])


def test_performance_summary_carries_kw_metrics():
    s = to_openadr_report(_dr())["performanceSummary"]
    assert s["eventHours"] == 4.0
    assert s["avgReductionKW"] == 40.0 and s["peakReductionKW"] == 40.0
    assert abs(s["pctReduction"] - 0.4) < 1e-9 and s["reductionUnit"] == "KW"


def test_accepts_plain_dict_and_is_json_serializable():
    d = _dr().as_dict()
    rep = to_openadr_report(d, event_id="EV-9")  # a plain dict, not the dataclass
    assert _payloads(rep)["REDUCTION"] == 160.0
    assert isinstance(json.loads(openadr_report_json(d)), dict)


def test_created_timestamp_not_fabricated():
    rep = to_openadr_report(_dr())  # no created= supplied
    assert rep["createdDateTime"] == ""  # empty, never invented
    rep2 = to_openadr_report(_dr(), created="2026-07-15T20:00:00Z")
    assert rep2["createdDateTime"] == "2026-07-15T20:00:00Z"
