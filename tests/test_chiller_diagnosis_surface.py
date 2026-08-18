"""Tests for surfacing the chiller drift roll-up in export + reporting.

The roll-ups are built from synthetic Findings via the real diagnose_chiller_drift; nothing runs the
detectors or touches data here.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerdiag import diagnose_chiller_drift  # noqa: E402
from camber.integrate.export import (  # noqa: E402
    diagnoses_to_frame,
    export_diagnoses,
)
from camber.report.chiller import chiller_diagnosis_table  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="CH_1", severity=severity, metrics=metrics)


def _machine_wide():
    return diagnose_chiller_drift(
        [
            _f("chiller_head_pressure_drift", "fault", head_pressure_drift_psi=8.0),
            _f(
                "chiller_superheat_drift",
                "fault",
                superheat_drift_direction="up",
                superheat_drift_f=3.0,
            ),
            _f(
                "chiller_subcooling_drift",
                "warn",
                subcooling_drift_direction="up",
                subcooling_drift_f=2.5,
            ),
        ],
        equip="CH_1",
    )


def _condenser_only():
    d = diagnose_chiller_drift(
        [_f("chiller_head_pressure_drift", "warn", head_pressure_drift_psi=3.0)], equip="CH_2"
    )
    return d


def _steady():
    return diagnose_chiller_drift([], equip="CH_3")


# --------------------------------------------------------------------------- export


def test_diagnoses_to_frame_one_row_per_machine():
    df = diagnoses_to_frame([_machine_wide(), _condenser_only(), _steady()], site="SITE")
    assert len(df) == 3
    assert list(df["equip"]) == ["CH_1", "CH_2", "CH_3"]
    # whole-machine columns are present and populated
    assert set(
        ["locus", "severity", "machine_wide", "condenser_severity", "charge_cause"]
    ).issubset(df.columns)


def test_frame_carries_the_rollup_verdict():
    df = diagnoses_to_frame([_machine_wide()], site="SITE")
    row = df.iloc[0]
    assert row["locus"] == "whole-machine"
    assert row["severity"] == "fault"
    assert bool(row["machine_wide"]) is True
    assert row["condenser_severity"] == "fault" and row["evaporator_severity"] == "fault"
    assert "non-condensables" in row["charge_cause"]
    assert "condenser:" in row["causes"] and "evaporator:" in row["causes"]
    assert row["fingerprint"]  # non-empty, stable id


def test_steady_machine_exports_cleanly():
    df = diagnoses_to_frame([_steady()], site="SITE")
    row = df.iloc[0]
    assert row["locus"] == "steady" and row["severity"] == "ok"
    assert bool(row["machine_wide"]) is False and row["causes"] == ""


def test_columns_override_restricts_and_orders():
    df = diagnoses_to_frame([_machine_wide()], columns=["equip", "locus", "severity"])
    assert list(df.columns) == ["equip", "locus", "severity"]


def test_empty_diagnoses_is_an_empty_frame():
    df = diagnoses_to_frame([], site="SITE")
    assert df.empty


def test_export_diagnoses_csv_and_json_roundtrip(tmp_path):
    diags = [_machine_wide(), _condenser_only()]
    csv_path = tmp_path / "diag.csv"
    n = export_diagnoses(diags, str(csv_path), site="SITE")
    assert n == 2 and csv_path.exists()

    json_path = tmp_path / "diag.json"
    export_diagnoses(diags, str(json_path), site="SITE")
    records = json.loads(json_path.read_text())
    assert len(records) == 2
    assert records[0]["locus"] == "whole-machine"


def test_export_rejects_unknown_format(tmp_path):
    try:
        export_diagnoses([_steady()], str(tmp_path / "x.xlsx"))
    except ValueError as exc:
        assert "unsupported export format" in str(exc)
    else:
        raise AssertionError("expected ValueError on an unknown format")


# --------------------------------------------------------------------------- report


def test_table_renders_rows_ranked_worst_first():
    html = chiller_diagnosis_table([_condenser_only(), _machine_wide(), _steady()])
    assert "<table" in html and "Chiller drift diagnosis" in html
    # the fault machine must appear before the warn machine in the row order
    assert html.index("CH_1") < html.index("CH_2")
    # machine-wide is flagged
    assert "yes" in html


def test_table_escapes_and_handles_empty():
    assert "No chiller diagnoses" in chiller_diagnosis_table([])
    html = chiller_diagnosis_table([_steady()], title="Plant <A>")
    assert "Plant &lt;A&gt;" in html  # title is HTML-escaped
    assert "steady" in html  # a steady machine shows "steady" for its (empty) causes
