"""Tests for the continuous-benchmark gate (camber.eval.check_against_baseline)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.eval import BaselineCheck, check_against_baseline  # noqa: E402

_BASE = {"pooled.tpr": 0.90, "pooled.fpr": 0.05, "pooled.correct_diagnosis": 0.85}


def test_passes_when_equal():
    chk = check_against_baseline(dict(_BASE), _BASE)
    assert isinstance(chk, BaselineCheck) and chk.passed
    assert chk.regressions == [] and chk.unchanged == 3


def test_tpr_drop_is_regression():
    cur = {**_BASE, "pooled.tpr": 0.80}  # higher-is-better fell 0.10 > tol
    chk = check_against_baseline(cur, _BASE, tol=0.02)
    assert not chk.passed
    assert any(r[0] == "pooled.tpr" for r in chk.regressions)


def test_fpr_rise_is_regression():
    cur = {**_BASE, "pooled.fpr": 0.20}  # lower-is-better rose -> regression
    chk = check_against_baseline(cur, _BASE)
    assert not chk.passed and chk.regressions[0][0] == "pooled.fpr"


def test_fpr_drop_is_improvement_not_regression():
    cur = {**_BASE, "pooled.fpr": 0.01}  # FPR fell -> better, passes
    chk = check_against_baseline(cur, _BASE)
    assert chk.passed and any(i[0] == "pooled.fpr" for i in chk.improvements)


def test_within_tolerance_is_unchanged():
    cur = {**_BASE, "pooled.tpr": 0.89}  # 0.01 drop < tol 0.02
    chk = check_against_baseline(cur, _BASE, tol=0.02)
    assert chk.passed and chk.unchanged == 3


def test_missing_metric_fails():
    cur = {"pooled.tpr": 0.90, "pooled.fpr": 0.05}  # correct_diagnosis dropped out
    chk = check_against_baseline(cur, _BASE)
    assert not chk.passed and "pooled.correct_diagnosis" in chk.missing


def test_metrics_subset_limits_comparison():
    cur = {**_BASE, "pooled.fpr": 0.20}  # would regress, but we only check tpr
    chk = check_against_baseline(cur, _BASE, metrics=["pooled.tpr"])
    assert chk.passed and chk.unchanged == 1


def test_benchmark_metrics_dict_shape():
    # the example runner's flattener works on synthetic records (no datasets needed)
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "lbnl_fdd"
        ),
    )
    import benchmark as bench  # noqa: E402

    records = [
        {"truth": "oa_fraction", "fired": {"outdoor_air_fraction"}},
        {"truth": "", "fired": set()},
    ]
    m = bench.metrics_dict("sdahu", records)
    assert set(m) == {"sdahu.tpr", "sdahu.fpr", "sdahu.accuracy", "sdahu.correct_diagnosis"}
    assert all(isinstance(v, float) for v in m.values())
