"""Tests for pattern C — peer/cohort comparison + cohort-deviation rule
(camber.charts.cohort, camber.rules.cohort). Rendering runs headless on Agg."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.charts.cohort import (  # noqa: E402
    CohortResult,
    cohort_deviation,
    cohort_small_multiples,
    cohort_summary,
)
from camber.model.roles import Role  # noqa: E402
from camber.rules.cohort import CohortDeviation  # noqa: E402


def _cohort(n_units=8, outlier=True, seed=0):
    """A cohort of like units on AIRFLOW; optionally one clear high-flow outlier."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-06-01", periods=120, freq="1h")
    frames = {}
    for i in range(n_units):
        level = 1000.0 + rng.normal(0, 20, len(idx))  # peers cluster near 1000
        frames[f"VAV-{i}"] = pd.DataFrame({Role.AIRFLOW: pd.Series(level, index=idx)})
    if outlier:
        idx0 = frames["VAV-0"].index
        frames["VAV-0"] = pd.DataFrame({Role.AIRFLOW: pd.Series(2000.0, index=idx0)})  # 2x flow
    return frames


def test_cohort_summary_reductions():
    frames = _cohort(outlier=False)
    means = cohort_summary(frames, Role.AIRFLOW, summary="mean")
    peaks = cohort_summary(frames, Role.AIRFLOW, summary="peak")
    assert set(means.index) == set(frames) and (peaks >= means).all()
    with pytest.raises(ValueError):
        cohort_summary(frames, Role.AIRFLOW, summary="bogus")


def test_cohort_deviation_flags_the_outlier():
    res = cohort_deviation(_cohort(outlier=True), Role.AIRFLOW, k=3.5)
    assert isinstance(res, CohortResult)
    assert res.outliers == ["VAV-0"]  # the 2x-flow unit
    assert abs(res.z["VAV-0"]) >= 3.5


def test_cohort_deviation_uniform_has_no_outliers():
    res = cohort_deviation(_cohort(outlier=False), Role.AIRFLOW, k=3.5)
    assert res.outliers == []


def test_cohort_deviation_below_min_cohort():
    frames = _cohort(n_units=2, outlier=False)
    res = cohort_deviation(frames, Role.AIRFLOW, min_cohort=3)
    assert res.outliers == [] and res.z == {}  # not enough peers to judge


def test_cohort_small_multiples_orders_by_deviation():
    fig, res = cohort_small_multiples(_cohort(outlier=True), Role.AIRFLOW)
    axes = fig.get_axes()
    assert len(axes) >= len(res.values)  # a panel per unit (+ hidden fillers)
    # worst deviation first: the top-left panel is the outlier
    assert "VAV-0" in axes[0].get_title()


def test_cohort_rule_warns_on_outlier_and_ok_when_uniform():
    rule = CohortDeviation(Role.AIRFLOW, k=3.5)
    assert rule.name == "cohort_deviation_airflow"
    f_out = rule.analyze_fleet(_cohort(outlier=True))
    assert f_out.severity == "warn" and "VAV-0" in f_out.metrics["outliers"]
    f_ok = rule.analyze_fleet(_cohort(outlier=False))
    assert f_ok.severity == "ok" and f_ok.metrics["outliers"] == []


def test_cohort_rule_info_below_min_cohort():
    rule = CohortDeviation(Role.AIRFLOW, min_cohort=5)
    f = rule.analyze_fleet(_cohort(n_units=2, outlier=False))
    assert f.severity == "info" and f.equip == "<fleet>"
