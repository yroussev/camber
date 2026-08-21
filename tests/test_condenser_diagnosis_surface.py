"""Tests for surfacing the condenser heat-rejection drift diagnosis in export + reporting + site."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.condenserdrift import diagnose_condenser_drift  # noqa: E402
from camber.integrate.export import (  # noqa: E402
    condenser_diagnoses_to_frame,
    export_condenser_diagnoses,
)
from camber.report.condenser import condenser_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, equip, **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics)


def _tower(equip="TOWER_1"):
    return diagnose_condenser_drift(
        [_f("cooling_tower_approach_drift", "fault", equip, tower_approach_drift_f=3.5)],
        equip=equip,
    )


def _corroborated(equip="LOOP_1"):
    return diagnose_condenser_drift(
        [
            _f("cooling_tower_approach_drift", "fault", equip, tower_approach_drift_f=3.5),
            _f("chiller_head_pressure_drift", "warn", equip, head_pressure_drift_psi=3.0),
        ],
        equip=equip,
    )


def _cw_flow(equip="CT_1"):
    return diagnose_condenser_drift(
        [
            _f(
                "chiller_cw_range_drift",
                "warn",
                equip,
                cw_range_drift_direction="up",
                cw_range_drift_f=1.5,
            )
        ],
        equip=equip,
    )


def _steady(equip="LOOP_9"):
    return diagnose_condenser_drift([], equip=equip)


# --------------------------------------------------------------------------- export


def test_frame_one_row_per_loop_with_the_verdict():
    df = condenser_diagnoses_to_frame([_corroborated(), _cw_flow(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["LOOP_1", "CT_1", "LOOP_9"]
    row = df.iloc[0]
    assert row["severity"] == "fault" and bool(row["corroborated"]) is True
    assert "cooling-tower heat rejection" in row["causes"] and row["fingerprint"]
    assert bool(df.iloc[1]["corroborated"]) is False


def test_corroboration_surfaces_a_caveat():
    assert condenser_diagnoses_to_frame([_corroborated()]).iloc[0]["n_caveats"] >= 1


def test_columns_override_and_empty():
    df = condenser_diagnoses_to_frame([_tower()], columns=["equip", "severity"])
    assert list(df.columns) == ["equip", "severity"]
    assert condenser_diagnoses_to_frame([], site="SITE").empty


def test_export_csv_and_json(tmp_path):
    diags = [_tower(), _cw_flow()]
    n = export_condenser_diagnoses(diags, str(tmp_path / "c.csv"), site="SITE")
    assert n == 2 and (tmp_path / "c.csv").exists()
    export_condenser_diagnoses(diags, str(tmp_path / "c.json"), site="SITE")
    records = json.loads((tmp_path / "c.json").read_text())
    assert records[0]["severity"] == "fault"


# --------------------------------------------------------------------------- report table


def test_table_ranks_worst_first_and_flags_corroborated():
    html = condenser_diagnosis_table([_cw_flow(), _corroborated()])
    assert "Condenser heat-rejection drift diagnosis" in html and "<table" in html
    assert html.index("LOOP_1") < html.index("CT_1")  # fault before warn
    assert "yes" in html  # corroboration flagged


def test_table_escapes_and_empty():
    assert "No condenser diagnoses" in condenser_diagnosis_table([])
    assert "Plant &lt;X&gt;" in condenser_diagnosis_table([_steady()], title="Plant <X>")


# --------------------------------------------------------------------------- site-report wiring


def test_site_report_renders_the_condenser_table():
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=120, freq="1h")
    df = pd.DataFrame({Role.OAT: np.linspace(60, 90, 120)}, index=idx)
    html = build_site_report(df, condenser_diagnoses=[_corroborated()])
    assert "Condenser heat-rejection drift diagnosis" in html and "LOOP_1" in html

    plain = build_site_report(df)
    assert "Condenser heat-rejection drift diagnosis" not in plain
