"""Tests for the fleet/portfolio rollup (report.fleet)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.report.fleet import BuildingSummary, build_fleet_report  # noqa: E402


class _F:
    def __init__(self, rule, severity):
        self.rule, self.severity = rule, severity
        self.equip, self.metrics = "", {}


def _fleet():
    return [
        {"site": "A", "eui": 80.0, "findings": [_F("reheat_penalty", "fault")]},
        {"site": "B", "eui": 120.0, "findings": [_F("reheat_penalty", "warn"),
                                                 _F("oafraction", "fault"),
                                                 _F("x", "ok")]},
        {"site": "C", "eui": 100.0, "findings": []},
    ]


def test_per_building_counts_and_benchmark():
    r = build_fleet_report(_fleet())
    by = {b.site: b for b in r.buildings}
    assert isinstance(by["A"], BuildingSummary)
    assert by["A"].n_fault == 1 and by["A"].n_warn == 0
    assert by["B"].n_fault == 1 and by["B"].n_warn == 1   # the "ok" is dropped
    # median EUI of [80,100,120] = 100
    assert r.peer_median_eui == 100.0
    assert by["A"].eui_percentile == 100   # lowest EUI -> most efficient
    assert by["A"].pct_vs_median == -20    # (80-100)/100
    assert by["B"].pct_vs_median == 20


def test_fleet_top_rules_counts_buildings():
    r = build_fleet_report(_fleet())
    top = dict(r.fleet_top_rules)
    assert top["reheat_penalty"] == 2     # appears in A and B
    assert top["oafraction"] == 1
    assert "x" not in top                 # non-actionable excluded


def test_orderings_and_render():
    r = build_fleet_report(_fleet())
    # most efficient first by EUI
    assert [b.site for b in r.by_efficiency()] == ["A", "C", "B"]
    # worst by faults: tie on faults (A,B=1) broken by warnings -> B first
    worst = [b.site for b in r.worst_by_faults()]
    assert worst[0] == "B"
    txt = r.to_text()
    assert "Fleet summary -- 3 buildings" in txt and "reheat_penalty" in txt
    assert "<table" in r.to_html()


def test_peer_median_override():
    r = build_fleet_report(_fleet(), peer_median_eui=90.0)
    by = {b.site: b for b in r.buildings}
    assert r.peer_median_eui == 90.0
    assert by["C"].pct_vs_median == round(100 * (100 - 90) / 90)   # ~11
