"""Tests for the BDG2 M&V benchmark's pure metric functions (no dataset download).

The data-dependent scoring runs in the benchmark CI job; these lock the flattener + rollup shape on
synthetic per-building fit records, mirroring test_eval_baseline::test_benchmark_metrics_dict_shape.
"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bench():
    # load under a unique name (the example is one of several `benchmark.py` files)
    path = os.path.join(_ROOT, "examples", "bdg2", "benchmark.py")
    spec = importlib.util.spec_from_file_location("bdg2_benchmark", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_acceptance_metrics_shape_and_values():
    B = _bench()
    recs = [
        {"cv_rmse": 0.12, "accept": True},
        {"cv_rmse": 0.28, "accept": True},
        {"cv_rmse": 0.55, "accept": False},
        {"cv_rmse": 0.40, "accept": False},
    ]
    m = B.acceptance_metrics(recs, "chilledwater")
    assert m["chilledwater.acceptance_rate"] == 0.5
    assert m["chilledwater.n_buildings"] == 4
    assert m["chilledwater.median_cv_rmse"] == 0.34
    assert (
        0.0
        <= m["chilledwater.acceptance_ci_lo"]
        <= 0.5
        <= m["chilledwater.acceptance_ci_hi"]
        <= 1.0
    )


def test_acceptance_metrics_empty():
    B = _bench()
    m = B.acceptance_metrics([], "pooled")
    assert m["pooled.acceptance_rate"] == 0.0 and m["pooled.n_buildings"] == 0


def test_eui_metrics_percentile_monotonic():
    B = _bench()
    m = B.eui_metrics([120.0, 65.0, 90.0, 200.0, 45.0])
    assert m["eui.n_buildings"] == 5 and m["eui.median"] == 90.0
    assert m["eui.percentile_monotonic"] is True


def test_eui_metrics_drops_nan_eui():
    B = _bench()
    m = B.eui_metrics([100.0, float("nan"), None, 50.0])
    assert m["eui.n_buildings"] == 2


def test_rho_metrics_shape_and_quantiles():
    """The real-data residual-autocorrelation distribution behind the savings-band correction."""
    b = _bench()
    recs = [{"rho_lag1": r} for r in (0.05, 0.2, 0.35, 0.5, 0.8)]
    m = b.rho_metrics(recs, "daily")

    assert m["daily.median_rho"] == 0.35
    assert m["daily.p10_rho"] == 0.05
    assert m["daily.p90_rho"] == 0.8
    assert m["daily.frac_rho_gt_0_3"] == 0.6
    assert m["daily.n_with_rho"] == 5


def test_rho_metrics_excludes_unestimable_rather_than_counting_it_as_zero():
    """Treating an unestimable rho as 0.0 would assert an independence nobody tested."""
    b = _bench()
    recs = [{"rho_lag1": 0.4}, {"rho_lag1": 0.6}, {"rho_lag1": None}, {}]
    m = b.rho_metrics(recs, "pooled")

    assert m["pooled.n_with_rho"] == 2  # the two estimable ones
    assert m["pooled.median_rho"] == 0.5  # not dragged toward 0 by the missing ones


def test_rho_metrics_with_nothing_estimable():
    b = _bench()
    assert b.rho_metrics([{"rho_lag1": None}], "daily") == {"daily.n_with_rho": 0}
