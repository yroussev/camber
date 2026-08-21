"""Tests for surfacing the AHU air-side drift diagnosis in export + reporting + the site report."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahudrift import diagnose_ahu_drift  # noqa: E402
from camber.integrate.export import ahu_diagnoses_to_frame, export_ahu_diagnoses  # noqa: E402
from camber.report.ahu import ahu_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, equip, **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics)


def _air_path(equip="AHU_1"):
    return diagnose_ahu_drift(
        [
            _f(
                "fan_efficiency_drift",
                "fault",
                equip,
                fan_power_drift_kw=1.5,
                fan_power_drift_direction="up",
            ),
            _f(
                "filter_loading_drift",
                "warn",
                equip,
                filter_dp_drift_inwc=0.2,
                filter_dp_drift_direction="up",
            ),
        ],
        equip=equip,
    )


def _fan(equip="AHU_2"):
    return diagnose_ahu_drift(
        [
            _f(
                "duct_static_drift",
                "warn",
                equip,
                duct_static_drift_direction="down",
                duct_static_drift_inwc=0.2,
            )
        ],
        equip=equip,
    )


def _steady(equip="AHU_3"):
    return diagnose_ahu_drift([], equip=equip)


def _outdoor_air(equip="AHU_4"):
    return diagnose_ahu_drift(
        [
            _f(
                "economizer_damper_drift",
                "fault",
                equip,
                econ_oa_fraction_drift_pct=24.0,
                econ_oa_fraction_drift_direction="up",
            )
        ],
        equip=equip,
    )


# --------------------------------------------------------------------------- export


def test_the_outdoor_air_locus_flows_through_export_and_report():
    """The economizer's outdoor-air locus is a free string (no enum), so it surfaces unchanged."""
    df = ahu_diagnoses_to_frame([_outdoor_air()], site="SITE")
    assert df.iloc[0]["locus"] == "outdoor-air"
    assert "outdoor-air" in ahu_diagnosis_table([_outdoor_air()])


def test_frame_one_row_per_ahu_with_the_verdict():
    df = ahu_diagnoses_to_frame([_air_path(), _fan(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["AHU_1", "AHU_2", "AHU_3"]
    row = df.iloc[0]
    assert row["locus"] == "air-path" and row["severity"] == "fault"
    assert bool(row["ahu_wide"]) is False and bool(row["corroborated"]) is True
    assert "air filter loading" in row["causes"] and row["fingerprint"]


def test_columns_override_and_empty():
    df = ahu_diagnoses_to_frame([_air_path()], columns=["equip", "locus"])
    assert list(df.columns) == ["equip", "locus"]
    assert ahu_diagnoses_to_frame([], site="SITE").empty


def test_export_csv_and_json(tmp_path):
    diags = [_air_path(), _fan()]
    n = export_ahu_diagnoses(diags, str(tmp_path / "a.csv"), site="SITE")
    assert n == 2 and (tmp_path / "a.csv").exists()
    export_ahu_diagnoses(diags, str(tmp_path / "a.json"), site="SITE")
    records = json.loads((tmp_path / "a.json").read_text())
    assert records[0]["locus"] == "air-path"


# --------------------------------------------------------------------------- report table


def test_table_ranks_worst_first_and_flags_ahu_wide():
    aw = diagnose_ahu_drift(
        [
            _f(
                "filter_loading_drift",
                "fault",
                "AHU_9",
                filter_dp_drift_inwc=0.4,
                filter_dp_drift_direction="up",
            ),
            _f(
                "coil_valve_drift",
                "warn",
                "AHU_9",
                coil_valve_which="cooling",
                coil_valve_drift_pct=10.0,
                coil_valve_drift_direction="up",
            ),
        ],
        equip="AHU_9",
    )
    html = ahu_diagnosis_table([_fan(), aw])
    assert "AHU air-side drift diagnosis" in html and "<table" in html
    assert html.index("AHU_9") < html.index("AHU_2")  # fault before warn
    assert "yes" in html  # ahu-wide flagged


def test_table_escapes_and_empty():
    assert "No AHU diagnoses" in ahu_diagnosis_table([])
    assert "Plant &lt;X&gt;" in ahu_diagnosis_table([_steady()], title="Plant <X>")


# --------------------------------------------------------------------------- site-report wiring


def test_site_report_renders_the_ahu_table():
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=120, freq="1h")
    df = pd.DataFrame({Role.OAT: np.linspace(60, 90, 120)}, index=idx)
    html = build_site_report(df, ahu_diagnoses=[_air_path()])
    assert "AHU air-side drift diagnosis" in html and "AHU_1" in html

    plain = build_site_report(df)
    assert "AHU air-side drift diagnosis" not in plain
