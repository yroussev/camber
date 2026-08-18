"""Tests for the one-shot site report (camber.report.site)."""

import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.fault_economics import EnergyPrice, EquipmentLoad  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report import build_site_report  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402


def _df():
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            Role.HEAT_VALVE: pd.Series(np.where(idx.hour > 12, 0.5, 0.0), index=idx),
            Role.COOL_VALVE: pd.Series(np.where(idx.hour > 12, 0.6, 0.0), index=idx),
            Role.OAT: pd.Series(rng.uniform(60, 90, 240), index=idx),
        }
    )


def _findings():
    return [
        Finding(
            rule="simultaneous_heat_cool",
            equip="AHU-1",
            severity="fault",
            metrics={"simultaneous_hc_pct": 20.0},
            summary="both coils 20%",
        ),
        Finding(rule="unmet_setpoint_hours", equip="Z-1", severity="warn", summary="unmet 12%"),
    ]


def test_full_report_has_all_sections():
    html = build_site_report(
        _df(),
        findings=_findings(),
        rules=[SimultaneousHeatCool()],
        loads={"AHU-1": EquipmentLoad(heating_capacity_kbtuh=200)},
        price=EnergyPrice(),
    )
    for token in (
        "Health scorecard",
        "Findings",
        "Recommended actions",
        "<h2>Evidence</h2>",
        "<img",
    ):
        assert token in html
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")


def test_charts_only_without_findings():
    html = build_site_report(_df())
    assert "Health scorecard" not in html and "Recommended actions" not in html
    assert "<img" in html  # charts still render


def test_sections_subset_honored():
    html = build_site_report(_df(), sections=("A",))
    assert "A. Ingest readiness" in html and "E. Load carpet" not in html


def test_report_is_self_contained():
    html = build_site_report(_df(), findings=_findings(), rules=[SimultaneousHeatCool()])
    # images inlined as data URIs; no external http(s) asset references
    assert "data:image/png;base64," in html
    assert "http://" not in html and "https://" not in html


def test_scorecard_grade_reflects_faults():
    html = build_site_report(_df(), findings=_findings())
    # a fault + a warn -> not a clean A; the overall grade chip is present
    assert "Health scorecard" in html and "actionable finding" in html


def _diagnoses():
    from camber.chillerdiag import diagnose_chiller_drift

    return [
        diagnose_chiller_drift(
            [
                Finding(
                    rule="chiller_head_pressure_drift",
                    equip="CH-1",
                    severity="fault",
                    metrics={"head_pressure_drift_psi": 8.0},
                ),
                Finding(
                    rule="chiller_superheat_drift",
                    equip="CH-1",
                    severity="fault",
                    metrics={"superheat_drift_direction": "up", "superheat_drift_f": 3.0},
                ),
            ],
            equip="CH-1",
        )
    ]


def test_diagnoses_add_a_chiller_verdict_table():
    html = build_site_report(_df(), findings=_findings(), diagnoses=_diagnoses())
    assert "Chiller drift diagnosis" in html
    assert "CH-1" in html and "whole-machine" in html


def test_diagnoses_are_optional():
    html = build_site_report(_df(), findings=_findings())
    assert "Chiller drift diagnosis" not in html
