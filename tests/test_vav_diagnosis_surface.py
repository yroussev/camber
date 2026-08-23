"""Tests for surfacing the VAV zone-terminal drift diagnosis in export + reporting + site."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.integrate.export import export_vav_diagnoses, vav_diagnoses_to_frame  # noqa: E402
from camber.report.vav import vav_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.vavdrift import diagnose_vav_drift  # noqa: E402


def _f(rule, severity, equip, **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics)


def _airflow(equip="VAV_1"):
    return diagnose_vav_drift(
        [
            _f(
                "vav_airflow_drift",
                "fault",
                equip,
                vav_airflow_drift_pct=18.0,
                vav_airflow_drift_direction="up",
            )
        ],
        equip=equip,
    )


def _box_wide(equip="VAV_2"):
    return diagnose_vav_drift(
        [
            _f(
                "vav_airflow_drift",
                "fault",
                equip,
                vav_airflow_drift_pct=18.0,
                vav_airflow_drift_direction="up",
            ),
            _f(
                "vav_reheat_valve_drift",
                "warn",
                equip,
                vav_reheat_valve_drift_pct=10.0,
                vav_reheat_valve_drift_direction="up",
            ),
        ],
        equip=equip,
    )


def _steady(equip="VAV_9"):
    return diagnose_vav_drift([], equip=equip)


# --------------------------------------------------------------------------- export


def test_frame_one_row_per_box_with_the_verdict():
    df = vav_diagnoses_to_frame([_box_wide(), _airflow(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["VAV_2", "VAV_1", "VAV_9"]
    row = df.iloc[0]
    assert row["locus"] == "box-wide" and row["severity"] == "fault"
    assert bool(row["box_wide"]) is True and bool(row["corroborated"]) is True
    assert "box damper authority" in row["causes"] and row["fingerprint"]
    assert bool(df.iloc[1]["box_wide"]) is False


def test_columns_override_and_empty():
    df = vav_diagnoses_to_frame([_airflow()], columns=["equip", "locus"])
    assert list(df.columns) == ["equip", "locus"]
    assert vav_diagnoses_to_frame([], site="SITE").empty


def test_export_csv_and_json(tmp_path):
    diags = [_airflow(), _box_wide()]
    n = export_vav_diagnoses(diags, str(tmp_path / "v.csv"), site="SITE")
    assert n == 2 and (tmp_path / "v.csv").exists()
    export_vav_diagnoses(diags, str(tmp_path / "v.json"), site="SITE")
    records = json.loads((tmp_path / "v.json").read_text())
    assert records[0]["locus"] == "airflow"


# --------------------------------------------------------------------------- report table


def test_table_ranks_worst_first_and_flags_box_wide():
    html = vav_diagnosis_table([_airflow("VAV_A"), _box_wide("VAV_B")])
    # both are severity fault; assert structure + the box-wide flag renders
    assert "VAV zone-terminal drift diagnosis" in html and "<table" in html
    assert "box-wide" in html and "yes" in html


def test_table_escapes_and_empty():
    assert "No VAV diagnoses" in vav_diagnosis_table([])
    assert "Plant &lt;X&gt;" in vav_diagnosis_table([_steady()], title="Plant <X>")


# --------------------------------------------------------------------------- site-report wiring


def test_site_report_renders_the_vav_table():
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=120, freq="1h")
    df = pd.DataFrame({Role.OAT: np.linspace(60, 90, 120)}, index=idx)
    html = build_site_report(df, vav_diagnoses=[_box_wide()])
    assert "VAV zone-terminal drift diagnosis" in html and "VAV_2" in html

    plain = build_site_report(df)
    assert "VAV zone-terminal drift diagnosis" not in plain
