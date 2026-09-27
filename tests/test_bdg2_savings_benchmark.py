"""Tests for the BDG2 savings benchmark's pure functions (no dataset download; #49).

The data-dependent scoring runs in the benchmark CI job; these lock the metric flatteners, the
step matching, the deterministic sampling and the gate's direction rules on synthetic records,
plus one end-to-end building on synthetic daily data.
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bench():
    path = os.path.join(_ROOT, "examples", "bdg2", "savings_benchmark.py")
    spec = importlib.util.spec_from_file_location("bdg2_savings_benchmark", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _bench()


def test_eur_is_touzanis_ratio_and_needs_a_band():
    assert B.eur(110.0, 100.0, 20.0) == 0.5
    assert B.eur(80.0, 100.0, 10.0) == -2.0
    assert B.eur(1.0, 0.0, None) is None and B.eur(1.0, 0.0, 0.0) is None
    assert B.eur(1.0, 0.0, float("nan")) is None


def test_placebo_metrics_counts_uncovered_bands_and_declines():
    recs = [
        {"eur": 0.2, "declined": False},
        {"eur": -0.9, "declined": False},
        {"eur": 1.5, "declined": False},
        {"eur": None, "declined": False},  # no finite band: not covered
        {"eur": None, "declined": True},
    ]
    m = B.placebo_metrics(recs, "x")
    assert m["x.n"] == 4 and m["x.declined_rate"] == 0.2
    assert m["x.uicf"] == 0.5
    assert m["x.eur_abs_p50"] == 0.9 and m["x.eur_abs_p90"] == 1.5
    assert m["info.x.eur_median"] == 0.2
    assert B.placebo_metrics([], "y") == {"y.n": 0, "y.declined_rate": 0.0}


def test_recovery_metrics():
    recs = [
        {"err": 0.01, "covered": True, "significant": True},
        {"err": -0.03, "covered": False, "significant": True},
        {"err": 0.02, "covered": True, "significant": False},
        {"err": None, "declined": True},
    ]
    m = B.recovery_metrics(recs, "r")
    assert m["r.n"] == 3 and m["r.declined_rate"] == 0.25
    assert m["r.abs_error_p50"] == 0.02 and m["r.abs_error_p90"] == 0.03
    assert m["r.coverage"] == round(2 / 3, 4) and m["r.significant_rate"] == round(2 / 3, 4)
    assert m["info.r.bias_median"] == 0.01


def test_match_steps_pairs_each_detection_once_within_the_tolerance():
    assert B.match_steps([100], [103]) == [(100, 103)]
    assert B.match_steps([100], [110]) == [(100, None)]
    assert B.match_steps([100, 104], [102]) == [(100, 102), (104, None)]
    assert B.match_steps([100, 200], [199, 98, 300]) == [(100, 98), (200, 199)]
    assert B.match_steps([50], []) == [(50, None)]


def test_spurious_excludes_planted_and_native_steps():
    assert B.spurious_count([100, 150, 300], planted=[98], native=[152]) == 1
    assert B.spurious_count([], planted=[98], native=[]) == 0


def test_step_metrics():
    recs = [
        {
            "steps": [
                {
                    "found": True,
                    "date_error": 2,
                    "ind_rel_error": 0.1,
                    "ind_covered": True,
                    "det_covered": True,
                }
            ],
            "spurious": 0,
            "native": 1,
        },
        {
            "steps": [
                {
                    "found": False,
                    "date_error": None,
                    "ind_rel_error": 0.3,
                    "ind_covered": False,
                    "det_covered": None,
                }
            ],
            "spurious": 2,
            "native": 0,
        },
    ]
    m = B.step_metrics(recs, "s")
    assert m["s.n_series"] == 2 and m["s.n_steps"] == 2
    assert m["s.detect_rate"] == 0.5 and m["s.date_error_p50"] == 2
    assert m["s.spurious_per_series"] == 1.0
    assert m["s.indicator_rel_error_p50"] == 0.1 and m["s.indicator_coverage"] == 0.5
    assert m["s.detector_coverage"] == 1.0 and m["info.s.native_rate"] == 0.5


def test_static_metrics():
    recs = [
        {
            "recovery_error": 0.0,
            "unadjusted_error": 0.15,
            "adjusted_covered": True,
            "unadjusted_covered": False,
        },
        {
            "recovery_error": 0.01,
            "unadjusted_error": 0.14,
            "adjusted_covered": False,
            "unadjusted_covered": False,
        },
    ]
    m = B.static_metrics(recs, "f")
    assert m["f.n"] == 2 and m["f.recovery_error_p90"] == 0.01
    assert m["f.unadjusted_error_p50"] == 0.14  # nearest rank: the lower of two
    assert m["f.adjusted_uicf"] == 0.5 and m["f.unadjusted_uicf"] == 0.0


def test_sampling_is_deterministic_and_order_free():
    ids = [f"b{i}" for i in range(50)]
    a = B.sample_ids(ids, 10)
    assert a == B.sample_ids(list(reversed(ids)), 10) and len(a) == 10 and a == sorted(a)
    assert B.sample_ids(ids, 0) == sorted(ids) and B.sample_ids(ids, 99) == sorted(ids)
    assert B.building_seed("b1") == B.building_seed("b1") != B.building_seed("b2")


def test_gate_directions_and_info_keys_are_not_gated():
    from camber.eval import check_against_baseline

    base = {
        "x.uicf": 0.7,
        "x.eur_abs_p90": 2.0,
        "x.declined_rate": 0.01,
        "s.spurious_per_series": 0.2,
        "r.abs_error_p50": 0.03,
    }
    worse = {
        "x.uicf": 0.6,
        "x.eur_abs_p90": 2.2,
        "x.declined_rate": 0.1,
        "s.spurious_per_series": 0.4,
        "r.abs_error_p50": 0.1,
        "info.x.eur_median": 9.0,
    }
    chk = check_against_baseline(
        B.gated(worse), base, tol=0.05, lower_is_better=B.LOWER_IS_BETTER, strict_new=True
    )
    assert {r[0] for r in chk.regressions} == set(base)
    better = {
        "x.uicf": 0.8,
        "x.eur_abs_p90": 1.5,
        "x.declined_rate": 0.0,
        "s.spurious_per_series": 0.0,
        "r.abs_error_p50": 0.0,
    }
    chk = check_against_baseline(better, base, tol=0.05, lower_is_better=B.LOWER_IS_BETTER)
    assert chk.passed and len(chk.improvements) == 3  # the other two moved within tol


def test_normal_year_has_365_days():
    idx = pd.date_range("2016-01-01", "2017-12-31 23:00", freq="h")
    oat = pd.Series(50 + 20 * np.sin(2 * np.pi * idx.dayofyear / 366), idx)
    S = B.normal_year(oat)
    assert S.shape == (365,) and np.all(np.isfinite(S))


def _synthetic_building(seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-01", "2017-12-31 23:00", freq="h")
    day = idx.dayofyear.to_numpy()
    oat = pd.Series(
        55 + 20 * np.sin(2 * np.pi * (day - 110) / 365) + rng.normal(0, 3, len(idx)), idx
    )
    hourly = (40 + 1.5 * np.maximum(0, oat - 60)) / 24 + rng.normal(0, 0.3, len(idx))
    d = B.daily_frame(pd.Series(hourly, idx), oat)
    return d.loc["2016"], d.loc["2017"], oat


def test_one_synthetic_building_end_to_end():
    """No measure in the data: the forecast identity holds, every method recovers an injected
    saving closely, a planted 20% step is found and the static factor is restored exactly."""
    base, rep, oat = _synthetic_building()
    fb = B._Fit(base)
    placebo, injected, dev = B.savings_records(fb, rep, B.normal_year(oat))
    assert dev < 1e-6
    assert set(placebo) == {f"{a}_{b}" for a, b in B.METHOD_KERNELS} | {"forecast_g14_95"}
    for (s, mk), r in injected.items():
        assert not r["declined"] and abs(r["err"]) < 0.02, (s, mk, r)
    rec = B.step_record(rep, np.random.default_rng(3), sizes=(0.2,))
    assert rec["steps"][0]["found"] and abs(rec["steps"][0]["date_error"]) <= 7
    assert rec["steps"][0]["ind_rel_error"] < 0.2
    full = B.static_record(fb, rep, "2017-01-01")
    assert full["recovery_error"] == pytest.approx(0.0, abs=1e-4)
    assert full["unadjusted_error"] > 0.1
