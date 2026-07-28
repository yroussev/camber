"""Regression tests for the second code-review round (0.3 fold-in additions)."""

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


from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def test_fleet_rule_gets_no_default_evidence():
    # a fleet rule handed a single frame must not render a bogus per-frame "evidence" chart
    from camber.charts.evidence import finding_evidence
    from camber.rules.chillerfleet_rule import ChillerStagingFleet

    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    frame = pd.DataFrame({Role.POWER: pd.Series(100.0, index=idx)})
    assert finding_evidence(ChillerStagingFleet(), "<fleet>", frame) is None


def test_site_report_skips_fleet_evidence():
    from camber.report import build_site_report
    from camber.rules.cohort import CohortDeviation

    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    df = pd.DataFrame({Role.AIRFLOW: pd.Series(1000.0, index=idx)})
    fleet_finding = Finding(rule="cohort_airflow", equip="<fleet>", severity="warn", summary="x")
    html = build_site_report(
        df, findings=[fleet_finding], rules=[CohortDeviation(Role.AIRFLOW, name="cohort_airflow")]
    )
    assert "<h2>Evidence</h2>" not in html  # no bogus fleet evidence


def test_localize_does_not_crash_on_single_fallback_reading():
    from camber.timegrid import localize

    # a single (unresolvable-by-infer) reading in the DST fall-back hour must not raise
    idx = pd.DatetimeIndex(["2025-11-02 00:00", "2025-11-02 01:30", "2025-11-02 02:00"])
    out = localize(idx, "America/Los_Angeles")
    assert len(out) == 3


def test_regularize_mean_keeps_non_numeric_columns():
    from camber.timegrid import regularize

    idx = pd.DatetimeIndex(["2025-01-01 01:00", "2025-01-01 01:00", "2025-01-01 02:00"])
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0], "s": ["a", "b", "c"]}, index=idx)
    out = regularize(df, dedupe="mean")
    assert list(out["v"]) == [1.5, 3.0]  # numeric averaged
    assert list(out["s"]) == ["a", "c"]  # non-numeric kept (first), not dropped/crashed


def test_dst_anomalies_handles_tz_aware_index():
    from camber.timegrid import dst_anomalies

    aware = pd.date_range("2025-06-01", periods=10, freq="1h", tz="America/Los_Angeles")
    assert dst_anomalies(aware, "America/Los_Angeles") == {"duplicate_timestamps": 0}  # no crash


def test_anomaly_forecast_frac_uses_overlap_denominator():
    from camber.anomaly import detect_anomalies

    idx = pd.date_range("2025-01-01", periods=300, freq="1h")
    s = pd.Series(50 + np.random.default_rng(0).normal(0, 1, 300), index=idx)
    s.iloc[10] += 40
    fc = pd.Series(50.0, index=idx[:20])  # forecast covers only 20 points
    r = detect_anomalies(s, forecast=fc)
    # 1 anomaly over the 20-point overlap = 5% -> fault; not diluted to 1/300
    assert r.anomaly_frac >= 0.05 and r.severity == "fault"


def test_carpet_section_survives_non_numeric_first_column():
    from camber.report import build_site_report

    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    # first column is text; the carpet must not KeyError/garble
    df = pd.DataFrame({"label": ["x"] * 48, "load": np.linspace(40, 90, 48)}, index=idx)
    html = build_site_report(df, sections=("E",))
    assert "<img" in html


def test_new_energy_rules_categorized():
    from camber.scorecard import category_for

    for r in ("economizer_high_limit", "static_pressure_reset", "free_cooling_missed"):
        assert category_for(r) == "energy"  # not silently "other"
