"""Tests for surfacing the pump drift roll-up in export + reporting + the site report."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.integrate.export import export_pump_diagnoses, pump_diagnoses_to_frame  # noqa: E402
from camber.pumpdrift import diagnose_pump_drift  # noqa: E402
from camber.report.pump import pump_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="P_1", severity=severity, metrics=metrics)


def _pump(equip="P_1"):
    return diagnose_pump_drift(
        [
            Finding(
                rule="pump_flow_drift",
                equip=equip,
                severity="fault",
                metrics={"pump_flow_drift_gpm": -70.0},
            ),
            Finding(
                rule="pump_head_drift",
                equip=equip,
                severity="fault",
                metrics={"pump_head_drift_psi": -8.0},
            ),
        ],
        equip=equip,
    )


def _distribution(equip="P_2"):
    return diagnose_pump_drift(
        [
            Finding(
                rule="loop_dp_drift",
                equip=equip,
                severity="warn",
                metrics={"loop_dp_drift_direction": "up"},
            )
        ],
        equip=equip,
    )


def _steady(equip="P_3"):
    return diagnose_pump_drift([], equip=equip)


# --------------------------------------------------------------------------- export


def test_frame_one_row_per_loop_with_the_verdict():
    df = pump_diagnoses_to_frame([_pump(), _distribution(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["P_1", "P_2", "P_3"]
    row = df.iloc[0]
    assert row["locus"] == "pump" and row["severity"] == "fault"
    assert bool(row["loop_wide"]) is False and bool(row["corroborated"]) is True
    assert "pump wear" in row["causes"] and row["fingerprint"]


def test_columns_override_and_empty():
    df = pump_diagnoses_to_frame([_pump()], columns=["equip", "locus"])
    assert list(df.columns) == ["equip", "locus"]
    assert pump_diagnoses_to_frame([], site="SITE").empty


def test_export_csv_and_json(tmp_path):
    diags = [_pump(), _distribution()]
    n = export_pump_diagnoses(diags, str(tmp_path / "p.csv"), site="SITE")
    assert n == 2 and (tmp_path / "p.csv").exists()
    export_pump_diagnoses(diags, str(tmp_path / "p.json"), site="SITE")
    records = json.loads((tmp_path / "p.json").read_text())
    assert records[0]["locus"] == "pump"


# --------------------------------------------------------------------------- report table


def test_table_ranks_worst_first_and_flags_loop_wide():
    lw = diagnose_pump_drift(
        [
            _f("pump_head_drift", "fault", pump_head_drift_psi=-8.0),
            _f("loop_dp_drift", "warn", loop_dp_drift_direction="up"),
        ],
        equip="P_9",
    )
    html = pump_diagnosis_table([_distribution(), lw])
    assert "Pump / hydronic drift diagnosis" in html and "<table" in html
    assert html.index("P_9") < html.index("P_2")  # fault before warn
    assert "yes" in html  # loop-wide flagged


def test_table_escapes_and_empty():
    assert "No pump diagnoses" in pump_diagnosis_table([])
    assert "Plant &lt;X&gt;" in pump_diagnosis_table([_steady()], title="Plant <X>")


# --------------------------------------------------------------------------- site-report wiring


def test_site_report_renders_the_pump_table():
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=120, freq="1h")
    df = pd.DataFrame({Role.OAT: np.linspace(60, 90, 120)}, index=idx)
    html = build_site_report(df, pump_diagnoses=[_pump()])
    assert "Pump / hydronic drift diagnosis" in html and "P_1" in html

    plain = build_site_report(df)
    assert "Pump / hydronic drift diagnosis" not in plain
