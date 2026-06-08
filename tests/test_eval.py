"""Tests for the FDD evaluation harness (eval.py)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.eval import confusion, correct_diagnosis_rate  # noqa: E402


def test_confusion_counts():
    labels = [True, True, True, False, False]
    preds = [True, True, False, False, True]   # 2 TP, 1 FN, 1 TN, 1 FP
    c = confusion(labels, preds)
    assert (c.tp, c.fn, c.tn, c.fp) == (2, 1, 1, 1)
    assert c.total == 5


def test_rates():
    labels = [True, True, True, True, False, False, False, False]
    preds = [True, True, True, False, False, False, False, True]
    c = confusion(labels, preds)
    assert c.true_positive_rate == 0.75       # 3/4
    assert c.false_negative_rate == 0.25      # 1/4
    assert c.false_positive_rate == 0.25      # 1/4
    assert c.accuracy == 0.75                 # (3+3)/8


def test_perfect_detector():
    c = confusion([True, False, True], [True, False, True])
    assert c.false_positive_rate == 0.0 and c.false_negative_rate == 0.0
    assert c.accuracy == 1.0


def test_length_mismatch_raises():
    try:
        confusion([True], [True, False])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_correct_diagnosis_rate_only_counts_faulty():
    true_types = ["damper_stuck", "leak", "", "leak"]   # "" = fault-free
    pred_types = ["damper_stuck", "leak", "leak", "stuck"]
    # faulty cases: idx 0,1,3 -> 0 and 1 correct, 3 wrong -> 2/3
    assert abs(correct_diagnosis_rate(true_types, pred_types) - 2 / 3) < 1e-4


def test_correct_diagnosis_rate_no_faults_is_nan():
    assert math.isnan(correct_diagnosis_rate(["", ""], ["x", "y"]))


# --- multi-detector benchmark harness --------------------------------------- #

from camber.eval import BenchmarkReport, benchmark  # noqa: E402


def test_benchmark_overall_and_per_detector():
    records = [
        {"truth": "", "fired": []},                 # fault-free, nothing fired (TN)
        {"truth": "damper", "fired": ["oa"]},        # damper caught by oa
        {"truth": "damper", "fired": ["oa"]},
        {"truth": "valve_leak", "fired": []},        # leak missed
    ]
    rep = benchmark(records, {"oa": "damper", "leak": "valve_leak"})
    assert isinstance(rep, BenchmarkReport) and rep.n == 4
    # overall: 3 faulty, 2 detected, 1 missed, 1 clean true-negative
    assert rep.overall.true_positive_rate == round(2 / 3, 4)
    assert rep.overall.false_positive_rate == 0.0
    # oa is a perfect classifier for damper here; leak never fires
    assert rep.per_detector["oa"].true_positive_rate == 1.0
    assert rep.per_detector["leak"].true_positive_rate == 0.0
    # correct diagnosis: 2 of 3 faulty got the right-target detector
    assert rep.correct_diagnosis == round(2 / 3, 4)


def test_benchmark_counts_false_positive():
    records = [{"truth": "", "fired": ["oa"]},        # false alarm on a clean unit
               {"truth": "damper", "fired": ["oa"]}]
    rep = benchmark(records, {"oa": "damper"})
    assert rep.per_detector["oa"].fp == 1
    assert rep.overall.false_positive_rate == 1.0
