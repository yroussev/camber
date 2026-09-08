"""Tests for camber.report.drift — the composed drift page.

The renderer is duck-typed over a DriftResult, so these use light stand-ins: the point is what the
page must always contain, not how the verdicts were produced.
"""

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.report import drift_report_html  # noqa: E402
from camber.report.drift import threshold_confidence_html  # noqa: E402


@dataclass
class _Diag:
    equip: str
    severity: str = "ok"
    locus: str = "steady"
    causes: list = field(default_factory=list)
    ahu_wide: bool = False
    machine_wide: bool = False
    loop_wide: bool = False
    box_wide: bool = False
    corroborated: bool = False
    condenser: object = None
    evaporator: object = None

    def as_dict(self):
        return {"equip": self.equip, "severity": self.severity}


@dataclass
class _Fam:
    family: str
    label: str
    equip_class: str = "AHU"
    baseline: tuple = ("2025-05-01", "2025-05-30")
    current: tuple = ("2025-06-01", "2025-07-01")
    diagnoses: list = field(default_factory=list)
    unevaluated: list = field(default_factory=list)
    plant: object = None


@dataclass
class _Result:
    site: str = "T"
    run_id: str = "R"
    families: list = field(default_factory=list)


def test_banner_is_present_even_with_nothing_to_report():
    html = drift_report_html(_Result())
    assert "screening-grade" in html
    assert "provisional" in html
    assert "No drift families were run." in html


def test_a_family_renders_its_table_and_its_window():
    fam = _Fam(
        "ahu",
        "AHU air-side drift",
        diagnoses=[_Diag("AHU_1", "fault", "air-path", ["loading filter"], ahu_wide=True)],
    )
    html = drift_report_html(_Result(families=[fam]))

    assert "AHU air-side drift diagnosis" in html
    assert "AHU_1" in html and "loading filter" in html
    assert "2025-05-01" in html and "2025-06-01" in html


def test_unevaluated_equipment_gets_its_own_table_and_is_not_a_verdict():
    fam = _Fam(
        "ahu",
        "AHU air-side drift",
        diagnoses=[_Diag("AHU_1", "ok")],
        unevaluated=[
            {"equip": "AHU_3", "reason": "no required role resolved", "roles_required": ["power"]}
        ],
    )
    html = drift_report_html(_Result(families=[fam]))

    assert "Equipment not evaluated" in html
    assert "AHU_3" in html and "power" in html
    assert "is not a verdict of steady" in html


def test_chiller_family_renders_the_machine_verdict_and_both_sides():
    diag = _Diag(
        "CH_1",
        "warn",
        "condenser",
        ["condenser tube fouling"],
        condenser=_Diag("CH_1", "warn", "condenser"),
        evaporator=_Diag("CH_1", "ok", "steady"),
    )
    html = drift_report_html(
        _Result(families=[_Fam("chiller", "Chiller drift", "CH", diagnoses=[diag])])
    )

    assert "Chiller drift diagnosis" in html
    assert "Condenser heat-rejection drift diagnosis" in html
    assert "Evaporator / chilled-water drift diagnosis" in html


def test_pump_plant_rollup_is_surfaced():
    @dataclass
    class _Plant:
        summary: str = "2 of 3 loops degrading"
        recommendation: str = "investigate the central distribution"

    fam = _Fam("pump", "Pump / hydronic drift", "CHWP", diagnoses=[_Diag("P_1")], plant=_Plant())
    html = drift_report_html(_Result(families=[fam]))

    assert "2 of 3 loops degrading" in html
    assert "investigate the central distribution" in html


def test_standalone_wraps_the_document_and_non_standalone_does_not():
    res = _Result(families=[_Fam("vav", "VAV zone-terminal drift", "VAV")])
    assert drift_report_html(res).startswith("<html>")
    assert not drift_report_html(res, standalone=False).startswith("<html>")


def test_html_is_escaped():
    fam = _Fam("ahu", "AHU air-side drift", diagnoses=[_Diag("<script>", "ok")])
    html = drift_report_html(_Result(families=[fam]))
    assert "<script>" not in html.replace("<script>alert", "")
    assert "&lt;script&gt;" in html


def test_banner_helper_names_both_threshold_classes():
    banner = threshold_confidence_html()
    assert "screening-grade" in banner
    assert "provisional" in banner
