"""Tests for the drift-detector validation/calibration harness (camber.driftvalidation).

The pure harness logic (precision/recall/F1, sweep, objectives, tie-breaks) is exercised with a
tiny deterministic fake detector so the arithmetic is checkable by hand; one end-to-end test runs
the real superheat detector over synthetic labelled cases. Nothing is drawn from measured data.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import driftvalidation as dv  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.chiller_superheat_rule import ChillerSuperheatDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402


class _FakeRule:
    """A detector whose verdict is: fault iff the ``current`` scalar clears ``threshold``.

    ``current`` in each :class:`~camber.driftvalidation.LabeledCase` is just a number here, so a
    known set of cases produces an exactly known confusion matrix.
    """

    def __init__(self, threshold: float = 0.0):
        self.threshold = threshold

    def analyze_periods(self, equip, baseline, current) -> Finding:
        sev = "fault" if float(current) >= self.threshold else "ok"
        return Finding(rule="fake", equip=equip, severity=sev, metrics={}, summary="")


def _case(value, fault):
    return dv.LabeledCase(equip="X", baseline=None, current=value, fault=fault)


# --------------------------------------------------------------------------- severity mapping


def test_positive_from_severity_default_and_strict():
    assert [dv.positive_from_severity(s) for s in ("fault", "warn", "info", "ok")] == [
        True,
        True,
        False,
        False,
    ]
    # strict: only a fault counts
    assert dv.positive_from_severity("warn", min_severity="fault") is False
    assert dv.positive_from_severity("fault", min_severity="fault") is True


def test_positive_from_severity_rejects_a_bad_threshold():
    with pytest.raises(ValueError):
        dv.positive_from_severity("fault", min_severity="critical")


# --------------------------------------------------------------------------- evaluate


def test_evaluate_computes_precision_recall_f1_from_a_known_confusion():
    # threshold 0.5: values >= 0.5 -> fault. Build tp=2, fp=1, fn=1, tn=2.
    cases = [
        _case(1.0, True),  # predicted fault, truly fault  -> tp
        _case(0.9, True),  # tp
        _case(0.8, False),  # predicted fault, not fault    -> fp
        _case(0.2, True),  # predicted ok, truly fault      -> fn
        _case(0.1, False),  # tn
        _case(0.0, False),  # tn
    ]
    score = dv.evaluate(lambda: _FakeRule(threshold=0.5), cases)
    c = score.confusion
    assert (c.tp, c.fp, c.fn, c.tn) == (2, 1, 1, 2)
    assert score.precision == pytest.approx(2 / 3, abs=1e-4)
    assert score.recall == pytest.approx(2 / 3, abs=1e-4)
    assert score.f1 == pytest.approx(2 / 3, abs=1e-4)
    assert score.n == 6
    d = score.as_dict()
    assert d["tp"] == 2 and d["precision"] == score.precision


def test_precision_is_nan_when_nothing_is_flagged():
    cases = [_case(0.0, True), _case(0.0, False)]
    score = dv.evaluate(lambda: _FakeRule(threshold=10.0), cases)  # nothing clears it
    assert score.confusion.tp == 0 and score.confusion.fp == 0
    assert score.precision != score.precision  # NaN
    assert score.f1 != score.f1  # NaN propagates


def test_evaluate_builds_a_fresh_detector_per_case():
    """The thunk must be called once per case (so real detectors don't leak frozen baselines)."""
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return _FakeRule(threshold=0.5)

    dv.evaluate(build, [_case(1.0, True), _case(0.0, False), _case(0.9, True)])
    assert calls["n"] == 3


# --------------------------------------------------------------------------- sweep


def _sweep_cases():
    # faults at value 1.0, healthy at 0.3; a threshold between 0.3 and 1.0 is perfect.
    return [_case(1.0, True), _case(1.0, True), _case(0.3, False), _case(0.3, False)]


def test_sweep_finds_the_threshold_that_maximizes_f1():
    res = dv.sweep(_FakeRule, _sweep_cases(), {"threshold": [0.1, 0.5, 2.0]}, objective="f1")
    assert res.best_params == {"threshold": 0.5}  # 0.1 -> fp on healthy; 2.0 -> misses faults
    assert res.best_score.f1 == 1.0
    assert len(res.table) == 3
    assert res.as_dict()["objective"] == "f1" and res.as_dict()["grid_points"] == 3


def test_sweep_tie_breaks_toward_fewer_false_positives():
    """0.1 and 0.5 both catch every fault (recall 1.0); 0.5 is quieter, so it wins on the tie."""
    res = dv.sweep(_FakeRule, _sweep_cases(), {"threshold": [0.1, 0.5]}, objective="recall")
    assert res.best_score.recall == 1.0
    assert res.best_params == {"threshold": 0.5}  # 0.1 raises a false alarm on the healthy cases


def test_sweep_objective_precision_prefers_the_strict_threshold():
    res = dv.sweep(_FakeRule, _sweep_cases(), {"threshold": [0.1, 0.5]}, objective="precision")
    assert res.best_params == {"threshold": 0.5} and res.best_score.precision == 1.0


def test_sweep_rejects_an_empty_grid_and_a_bad_objective():
    with pytest.raises(ValueError):
        dv.sweep(_FakeRule, _sweep_cases(), {})
    with pytest.raises(ValueError):
        dv.sweep(_FakeRule, _sweep_cases(), {"threshold": [0.5]}, objective="nonsense")


def test_sweep_covers_a_multi_parameter_grid():
    res = dv.sweep(_FakeRule, _sweep_cases(), {"threshold": [0.1, 0.5]}, objective="accuracy")
    assert len(res.table) == 2


# --------------------------------------------------------------------------- end-to-end (real rule)


def _sh_frame(n=24 * 30, *, start="2025-05-01", seed=0, offset_f=0.0):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    h = np.arange(n)
    tons = np.clip(
        170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n), 40, 400
    )
    return pd.DataFrame(
        {
            Role.CHW_FLOW: tons * 2.0,
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
            Role.CHW_RETURN_TEMP: np.full(n, 56.0),
            Role.SUPERHEAT_TEMP: 10.0 + 0.008 * tons + offset_f + rng.normal(0, 0.4, n),
        },
        index=idx,
    )


def test_the_harness_scores_the_real_superheat_detector():
    base = _sh_frame(start="2025-05-01", seed=1)
    # two genuine faults (a big fall and a big rise) and two healthy periods
    cases = [
        dv.LabeledCase(
            "CH_1", base, _sh_frame(start="2025-06-01", seed=2, offset_f=-5.0), fault=True
        ),
        dv.LabeledCase(
            "CH_1", base, _sh_frame(start="2025-06-01", seed=3, offset_f=5.0), fault=True
        ),
        dv.LabeledCase(
            "CH_1", base, _sh_frame(start="2025-06-01", seed=4, offset_f=0.0), fault=False
        ),
        dv.LabeledCase(
            "CH_1", base, _sh_frame(start="2025-06-01", seed=5, offset_f=0.0), fault=False
        ),
    ]

    def build(**params):
        return ChillerSuperheatDrift(BaselineStore(), site="S", run_id="r", **params)

    score = dv.evaluate(lambda: build(), cases)
    assert score.recall == 1.0 and score.precision == 1.0 and score.f1 == 1.0

    res = dv.sweep(build, cases, {"fault_f": [3.0, 4.0], "fault_sigma": [6.0, 8.0]}, objective="f1")
    assert res.best_score.f1 == 1.0
    assert len(res.table) == 4
