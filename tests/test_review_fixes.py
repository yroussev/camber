"""Regression tests for the correctness bugs surfaced by the 0.3 code review.

Each test fails against the pre-fix code and passes after. Grouped here so the review's findings
stay covered.
"""

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


def test_interval_hours_survives_duplicate_timestamps():
    # DST fall-back / concatenated exports produce duplicate timestamps -> dt must stay > 0
    from camber.timegrid import interval_hours

    idx = pd.DatetimeIndex(
        ["2025-11-02 01:00", "2025-11-02 01:00", "2025-11-02 02:00", "2025-11-02 03:00"]
    )
    assert interval_hours(idx) == 1.0  # was 0.0 (median of gaps incl. the 0-gap)


def test_cohort_flags_outlier_when_majority_share_a_value():
    # >half the cohort identical -> MAD==0; the fallback must still catch the deviant unit
    from camber.charts.cohort import cohort_deviation

    idx = pd.date_range("2024-06-01", periods=48, freq="1h")
    frames = {
        f"VAV-{i}": pd.DataFrame({Role.AIRFLOW: pd.Series(1000.0, index=idx)}) for i in range(8)
    }
    frames["VAV-0"] = pd.DataFrame({Role.AIRFLOW: pd.Series(2000.0, index=idx)})  # lone outlier
    res = cohort_deviation(frames, Role.AIRFLOW, k=3.5)
    assert res.outliers == ["VAV-0"]  # was [] (MAD==0 -> all z=0)


def test_degreeday_drops_nan_periods():
    from camber.mandv.degreeday import fit_degree_day

    rng = np.random.default_rng(0)
    tavg = rng.uniform(35, 90, 48)
    _, cdd = np.clip(60 - tavg, 0, None), np.clip(tavg - 60, 0, None)
    energy = 100 + 3.0 * cdd + rng.normal(0, 3, 48)
    energy[5] = np.nan  # a missing bill month
    m = fit_degree_day(tavg, energy)
    assert np.isfinite(m.cooling_slope) and np.isfinite(m.base)  # was all-NaN, silently broken
    assert np.isfinite(m.predict(tavg)).all()


def test_degreeday_rejects_degenerate_fit():
    from camber.mandv.degreeday import fit_degree_day

    with pytest.raises(ValueError):
        fit_degree_day([40.0, 60.0, 80.0], [100.0, 130.0, 160.0], kind="both")  # n==p==3


def test_changedetect_ignores_single_edge_outlier():
    from camber.changedetect import detect_level_shifts

    rng = np.random.default_rng(1)
    x = 50 + rng.normal(0, 1, 200)
    x[1] = 500.0  # one huge boundary spike
    s = pd.Series(x, index=pd.date_range("2025-01-01", periods=200, freq="1h"))
    # min_segment guards the split location -> a 1-point edge segment can't be reported as a regime
    assert detect_level_shifts(s, min_segment=24) == []


def test_savings_chart_no_crash_on_all_nan_report():
    from camber.charts.savings import savings_chart
    from camber.mandv.models import best_model

    rng = np.random.default_rng(2)
    Tb = rng.uniform(20, 90, 100)
    model = best_model(Tb, 2.0 * Tb + 50 + rng.normal(0, 3, 100))
    idx = pd.date_range("2025-01-01", periods=30, freq="D")
    yr = pd.Series(np.nan, index=idx)  # all non-finite reporting
    ax, res = savings_chart(
        model, rng.uniform(20, 90, 30), yr, n_baseline=100, p_baseline=2, cv_rmse=0.05
    )  # was IndexError on cum_avoided[-1]
    assert ax is not None


def test_scorecard_counts_unmapped_rule():
    from camber.scorecard import build_scorecard

    sc = build_scorecard(
        [Finding(rule="some_plugin_rule", equip="E", severity="fault", summary="")]
    )
    assert sc.n_actionable == 1  # was 0 (unmapped -> "other" -> dropped)
    assert sc.overall_score < 100  # the fault must drag the grade down
    assert any(c.category == "other" for c in sc.categories)


def test_hunting_robust_to_unsorted_timestamps():
    from camber.rules.hunting_rule import reversals_per_hour

    idx = pd.date_range("2024-07-01", periods=120, freq="2min")[::-1]  # reverse-ordered
    s = pd.Series([0.2, 0.8] * 60, index=idx)
    rate, n = reversals_per_hour(s, deadband=0.05)
    assert rate > 20  # was 0.0 (endpoint span <= 0)
