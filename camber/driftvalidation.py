"""Turn a drift detector's *screening-grade* thresholds into *calibrated* ones -- once labelled data
exists to calibrate against.

Every chiller drift detector in CAMBER ships thresholds labelled ``screening-grade`` /
``provisional-untuned`` (see :mod:`camber.driftthresholds`), because the false-alarm rate and
detection delay they produce have never been measured on real trended fault data. That labelling is
honest, but it is a placeholder: the moment a site (or a study) has a set of chiller periods with
**confirmed, dated fault events**, the thresholds can and should be tuned to that evidence. This
module is the harness that makes that tuning turnkey, so calibration is a data problem and not a
code problem.

It does two things, both built on the existing FDD confusion matrix (:func:`camber.eval.confusion`):

- :func:`evaluate` scores a built detector against a set of **labelled cases** -- ``(baseline,
  current)`` period pairs each tagged faulty or healthy -- and returns precision, recall and F1
  alongside the raw confusion counts.
- :func:`sweep` runs that evaluation across a **grid of threshold settings** (the ``warn_f`` /
  ``fault_sigma`` / CUSUM parameters that every drift rule already exposes as constructor
  arguments) and returns the operating point that maximises a chosen objective. That operating point
  is the calibrated threshold set; feed it back into the rule's constructor.

The detector is anything with the period-rule shape
(:class:`camber.rules.base.PeriodRule`): ``analyze_periods(equip, baseline, current) -> Finding``.
Because these detectors freeze a baseline into an injected store on first use, a **fresh** detector
is built per case (via the ``build_rule`` thunk) so cases never leak baselines into one another.

Nothing here lowers the shipped thresholds or changes their labelling: it is the tool you point at
real data to *replace* the placeholders, and until you do the ``provisional-untuned`` label stands.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .eval import Confusion, confusion

__all__ = [
    "LabeledCase",
    "DetectorScore",
    "SweepResult",
    "positive_from_severity",
    "evaluate",
    "sweep",
]

# Actionable-severity order. "ok"/"info" are non-actionable (info = "could not evaluate", which on a
# faulty case is an honest miss, not a healthy verdict); "warn"/"fault" are the positive tiers.
_SEV_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}

_OBJECTIVES = ("f1", "recall", "precision", "accuracy", "youden")


def positive_from_severity(severity: str, *, min_severity: str = "warn") -> bool:
    """Map a Finding ``severity`` to a boolean detection (does it clear ``min_severity``?).

    ``min_severity="warn"`` (the default) counts both ``warn`` and ``fault`` as a detection;
    ``min_severity="fault"`` counts only ``fault``. ``ok`` and ``info`` are never a detection.
    """
    if min_severity not in _SEV_RANK:
        raise ValueError(f"min_severity must be one of {sorted(_SEV_RANK)}; got {min_severity!r}")
    return _SEV_RANK.get(severity, 0) >= _SEV_RANK[min_severity]


@dataclass(frozen=True)
class LabeledCase:
    """One labelled period comparison for a drift detector.

    ``fault`` is the ground truth (was this current period genuinely faulty?). ``baseline`` and
    ``current`` are the role-frames handed to ``analyze_periods``; ``equip`` names the machine and
    ``name`` is an optional human label for the case.
    """

    equip: str
    baseline: object  # pandas.DataFrame (kept untyped to stay import-light)
    current: object  # pandas.DataFrame
    fault: bool
    name: str = ""


def _precision(c: Confusion) -> float:
    d = c.tp + c.fp
    return round(c.tp / d, 4) if d else float("nan")


def _f1(precision: float, recall: float) -> float:
    # NaN in either input (no positive predictions, or no actual faults) -> F1 is undefined
    if precision != precision or recall != recall:
        return float("nan")
    s = precision + recall
    return round(2 * precision * recall / s, 4) if s else 0.0


@dataclass(frozen=True)
class DetectorScore:
    """Precision/recall/F1 over a set of labelled cases, plus the confusion counts."""

    n: int
    confusion: Confusion
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict:
        """Flat, JSON-friendly metrics (confusion counts + derived rates)."""
        return {
            "n": self.n,
            **self.confusion.as_dict(),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


def evaluate(
    build_rule: Callable[[], Any],
    cases: Iterable[LabeledCase],
    *,
    min_severity: str = "warn",
) -> DetectorScore:
    """Score a detector against labelled ``cases``.

    ``build_rule`` is a zero-argument thunk returning a **fresh** detector (with a fresh baseline
    store) -- called once per case so no case's frozen baseline leaks into another. Each case is run
    through ``analyze_periods`` and its Finding's severity is reduced to a detection via
    :func:`positive_from_severity`; the detections are compared to the ``fault`` labels.
    """
    cases = list(cases)
    labels, preds = [], []
    for case in cases:
        rule = build_rule()
        finding = rule.analyze_periods(case.equip, case.baseline, case.current)
        labels.append(bool(case.fault))
        preds.append(positive_from_severity(finding.severity, min_severity=min_severity))
    c = confusion(labels, preds)
    precision, recall = _precision(c), c.true_positive_rate
    return DetectorScore(
        n=len(cases), confusion=c, precision=precision, recall=recall, f1=_f1(precision, recall)
    )


def _objective_value(score: DetectorScore, objective: str) -> float:
    if objective == "f1":
        v = score.f1
    elif objective == "recall":
        v = score.recall
    elif objective == "precision":
        v = score.precision
    elif objective == "accuracy":
        v = score.confusion.accuracy
    elif objective == "youden":  # Youden's J = TPR - FPR (balances catch-rate against false alarms)
        tpr, fpr = score.confusion.true_positive_rate, score.confusion.false_positive_rate
        v = (0.0 if tpr != tpr else tpr) - (0.0 if fpr != fpr else fpr)
    else:
        raise ValueError(f"objective must be one of {_OBJECTIVES}; got {objective!r}")
    return -1.0 if v != v else v  # NaN sorts worst


@dataclass(frozen=True)
class SweepResult:
    """The best threshold set found by :func:`sweep`, plus the full scored grid."""

    objective: str
    best_params: dict
    best_score: DetectorScore
    table: list = field(default_factory=list)  # [(params dict, DetectorScore)], grid order

    def as_dict(self) -> dict:
        """Best operating point as flat metrics; the full table stays on the object."""
        return {
            "objective": self.objective,
            "best_params": dict(self.best_params),
            **self.best_score.as_dict(),
            "grid_points": len(self.table),
        }


def sweep(
    build_rule: Callable[..., Any],
    cases: Sequence[LabeledCase],
    grid: Mapping[str, Sequence],
    *,
    objective: str = "f1",
    min_severity: str = "warn",
) -> SweepResult:
    """Find the threshold set that maximises ``objective`` over a Cartesian ``grid``.

    ``build_rule(**params)`` builds a detector for a given threshold set (the ``warn_f`` /
    ``fault_sigma`` / CUSUM keyword arguments the drift rules already accept). ``grid`` maps each of
    those parameter names to the values to try; every combination is scored with :func:`evaluate`.

    ``objective`` is one of ``f1`` (default), ``recall``, ``precision``, ``accuracy`` or ``youden``.
    Ties are broken toward fewer false positives, then fewer false negatives, so among equally good
    operating points the quieter one wins. Returns the winning ``best_params`` -- feed them straight
    back into the rule's constructor -- and the full scored ``table``.
    """
    if objective not in _OBJECTIVES:
        raise ValueError(f"objective must be one of {_OBJECTIVES}; got {objective!r}")
    keys = list(grid)
    if not keys:
        raise ValueError("grid is empty: give at least one parameter with values to sweep")
    table: list = []
    for combo in itertools.product(*(list(grid[k]) for k in keys)):
        params = dict(zip(keys, combo))
        thunk = functools.partial(build_rule, **params)
        score = evaluate(thunk, cases, min_severity=min_severity)
        table.append((params, score))

    def _key(item):
        _params, score = item
        c = score.confusion
        # maximise objective; then minimise false positives; then minimise false negatives
        return (_objective_value(score, objective), -c.fp, -c.fn)

    best_params, best_score = max(table, key=_key)
    return SweepResult(
        objective=objective, best_params=best_params, best_score=best_score, table=table
    )
