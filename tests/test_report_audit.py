"""Tests for the Std-211 audit-deliverable wrapper (packaging layer)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.report.audit import AuditReport, Benchmark, ECM  # noqa: E402


def test_benchmark_pct_over():
    b = Benchmark(site_eui=120.0, peer_median_eui=100.0)
    assert abs(b.pct_over - 20.0) < 0.2          # a site 20% over its peer median


def test_ecm_ranking_high_first():
    r = AuditReport(building="Test", level=2)
    r.add_ecm(ECM("a", "f", "sys", priority="low"))
    r.add_ecm(ECM("b", "f", "sys", priority="high"))
    r.add_ecm(ECM("c", "f", "sys", priority="medium"))
    order = [e.name for e in r.ranked_ecms()]
    assert order == ["b", "c", "a"]


def test_text_report_contains_sections():
    r = AuditReport(building="DemoBuilding", level=2, climate_zone="CA CZ15",
                    benchmark=Benchmark(120.0, 100.0))
    r.add_ecm(ECM("SAT reset", "62% below G36 target", "AHU", priority="high",
                  comfort_iaq_impact="reduces overcooling"))
    r.comfort_notes.append("18/20 zones cold >50% of hours (PMV<-0.5)")
    r.caveats.append("EUI not yet a stable post-commissioning value")
    txt = r.to_text()
    assert "Level 2" in txt and "DemoBuilding" in txt
    assert "+20%" in txt        # benchmark line
    assert "SAT reset" in txt
    assert "Comfort" in txt and "Caveats" in txt


def test_html_report_well_formed_and_escaped():
    r = AuditReport(building="A & B <DemoBuilding>", level=3,
                    benchmark=Benchmark(120.0, 100.0))
    r.add_ecm(ECM("m1", "finding <x>", "AHU"))
    h = r.to_html()
    assert "<h1>" in h and "<table" in h
    assert "&amp;" in h and "&lt;DemoBuilding&gt;" in h    # HTML-escaped
    assert "finding &lt;x&gt;" in h


def test_empty_report_renders():
    r = AuditReport(building="Empty", level=1)
    assert "Level 1" in r.to_text()
    assert "<h1>" in r.to_html()


class _F:
    def __init__(self, rule, equip, severity, summary="", metrics=None):
        self.rule, self.equip, self.severity = rule, equip, severity
        self.summary, self.metrics = summary, metrics or {}


def test_report_ranks_findings_and_drops_ok():
    r = AuditReport(building="DemoBuilding", level=2)
    r.add_findings([
        _F("oa", "AHU_3", "warn", "excess OA", {"pct": 30}),
        _F("simul", "AHU_2", "fault", "heat fighting cool", {"pct": 5}),
        _F("sat", "AHU_1", "ok", "fine"),
    ], magnitude_key="pct")
    txt = r.to_text()
    assert "Prioritized FDD findings (2" in txt          # ok excluded
    # fault ranks above warn
    assert txt.index("simul @ AHU_2") < txt.index("oa @ AHU_3")
    assert "fine" not in txt                              # the ok finding is dropped


def test_report_findings_html_table():
    r = AuditReport(building="B", level=2)
    r.add_findings([_F("simul", "AHU_2", "fault", "x")])
    h = r.to_html()
    assert "Prioritized FDD findings" in h and "<table" in h
    assert "simul" in h and "AHU_2" in h


def test_report_without_findings_omits_section():
    r = AuditReport(building="B", level=1)
    assert "Prioritized FDD findings" not in r.to_text()
