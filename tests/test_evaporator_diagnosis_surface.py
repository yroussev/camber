"""Tests for surfacing the evaporator / CHW drift diagnosis in export + reporting + site."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.evaporatordrift import diagnose_evaporator_drift  # noqa: E402
from camber.integrate.export import (  # noqa: E402
    evaporator_diagnoses_to_frame,
    export_evaporator_diagnoses,
)
from camber.report.evaporator import evaporator_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, equip, **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics)


def _superheat(equip="CH_1"):
    return diagnose_evaporator_drift(
        [
            _f(
                "chiller_superheat_drift",
                "fault",
                equip,
                superheat_drift_direction="up",
                superheat_drift_f=6.0,
            )
        ],
        equip=equip,
    )


def _corroborated(equip="CH_2"):
    return diagnose_evaporator_drift(
        [
            _f(
                "chiller_superheat_drift",
                "fault",
                equip,
                superheat_drift_direction="up",
                superheat_drift_f=6.0,
            ),
            _f(
                "chiller_suction_pressure_drift",
                "warn",
                equip,
                suction_pressure_drift_direction="down",
                suction_pressure_drift_psi=3.0,
            ),
        ],
        equip=equip,
    )


def _steady(equip="CH_9"):
    return diagnose_evaporator_drift([], equip=equip)


# --------------------------------------------------------------------------- export


def test_frame_one_row_per_loop_with_the_verdict():
    df = evaporator_diagnoses_to_frame([_corroborated(), _superheat(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["CH_2", "CH_1", "CH_9"]
    row = df.iloc[0]
    assert row["severity"] == "fault" and bool(row["corroborated"]) is True
    assert "starved" in row["causes"] and row["fingerprint"]
    assert bool(df.iloc[1]["corroborated"]) is False


def test_corroboration_surfaces_a_caveat():
    assert evaporator_diagnoses_to_frame([_corroborated()]).iloc[0]["n_caveats"] >= 1


def test_columns_override_and_empty():
    df = evaporator_diagnoses_to_frame([_superheat()], columns=["equip", "severity"])
    assert list(df.columns) == ["equip", "severity"]
    assert evaporator_diagnoses_to_frame([], site="SITE").empty


def test_export_csv_and_json(tmp_path):
    diags = [_superheat(), _corroborated()]
    n = export_evaporator_diagnoses(diags, str(tmp_path / "e.csv"), site="SITE")
    assert n == 2 and (tmp_path / "e.csv").exists()
    export_evaporator_diagnoses(diags, str(tmp_path / "e.json"), site="SITE")
    records = json.loads((tmp_path / "e.json").read_text())
    assert records[0]["severity"] == "fault"


# --------------------------------------------------------------------------- report table


def _warn_superheat(equip="CH_A"):
    return diagnose_evaporator_drift(
        [
            _f(
                "chiller_superheat_drift",
                "warn",
                equip,
                superheat_drift_direction="up",
                superheat_drift_f=3.0,
            )
        ],
        equip=equip,
    )


def test_table_ranks_worst_first_and_flags_corroborated():
    html = evaporator_diagnosis_table([_warn_superheat("CH_A"), _corroborated("CH_B")])
    assert "Evaporator / chilled-water drift diagnosis" in html and "<table" in html
    assert html.index("CH_B") < html.index("CH_A")  # fault before warn
    assert "yes" in html  # corroboration flagged


def test_table_escapes_and_empty():
    assert "No evaporator diagnoses" in evaporator_diagnosis_table([])
    assert "Plant &lt;X&gt;" in evaporator_diagnosis_table([_steady()], title="Plant <X>")


# --------------------------------------------------------------------------- site-report wiring


def test_site_report_renders_the_evaporator_table():
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=120, freq="1h")
    df = pd.DataFrame({Role.OAT: np.linspace(60, 90, 120)}, index=idx)
    html = build_site_report(df, evaporator_diagnoses=[_corroborated()])
    assert "Evaporator / chilled-water drift diagnosis" in html and "CH_2" in html

    plain = build_site_report(df)
    assert "Evaporator / chilled-water drift diagnosis" not in plain
