"""FDD detector evaluation: confusion matrix and accuracy rates.

Turns "the detector flagged something" into "the detector flags faults at X%
true-positive / Y% false-positive," following the LBNL FDD performance-evaluation
framework. Given ground-truth fault labels and detector verdicts over a set of
scenarios (a fault-free baseline plus labeled faults), it computes the confusion
matrix and the standard rates, and — when fault *types* are provided — the
correct-diagnosis rate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Confusion:
    """Binary confusion matrix (faulty = positive)."""

    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def total(self) -> int:
        """Total count of classified intervals."""
        return self.tp + self.fp + self.fn + self.tn

    @property
    def true_positive_rate(self) -> float:
        """Sensitivity / recall: faults correctly caught."""
        d = self.tp + self.fn
        return round(self.tp / d, 4) if d else float("nan")

    @property
    def false_negative_rate(self) -> float:
        """Missed faults."""
        d = self.tp + self.fn
        return round(self.fn / d, 4) if d else float("nan")

    @property
    def false_positive_rate(self) -> float:
        """False alarms on fault-free operation (alarm-fatigue driver)."""
        d = self.fp + self.tn
        return round(self.fp / d, 4) if d else float("nan")

    @property
    def accuracy(self) -> float:
        """Fraction of intervals classified correctly."""
        return round((self.tp + self.tn) / self.total, 4) if self.total else float("nan")

    def as_dict(self) -> dict:
        """Return counts plus derived rates as a plain dict."""
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
                "true_positive_rate": self.true_positive_rate,
                "false_negative_rate": self.false_negative_rate,
                "false_positive_rate": self.false_positive_rate,
                "accuracy": self.accuracy}


def confusion(labels, predictions) -> Confusion:
    """Confusion matrix from truth ``labels`` and detector ``predictions`` (bools).

    Each element is truthy if that scenario is faulty (label) / was flagged
    (prediction). Lengths must match.
    """
    labels = list(labels)
    predictions = list(predictions)
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must be the same length")
    tp = fp = fn = tn = 0
    for y, p in zip(labels, predictions):
        if y and p:
            tp += 1
        elif y and not p:
            fn += 1
        elif (not y) and p:
            fp += 1
        else:
            tn += 1
    return Confusion(tp=tp, fp=fp, fn=fn, tn=tn)


def correct_diagnosis_rate(true_types, predicted_types) -> float:
    """Fraction of *faulty* scenarios whose predicted fault type matches the truth.

    A scenario is faulty when its true type is truthy (non-empty / not None). Only
    faulty scenarios count toward the denominator — diagnosis is about naming the
    fault, not detecting its absence.
    """
    faulty = [(t, p) for t, p in zip(true_types, predicted_types) if t]
    if not faulty:
        return float("nan")
    correct = sum(1 for t, p in faulty if t == p)
    return round(correct / len(faulty), 4)


@dataclass(frozen=True)
class BenchmarkReport:
    """Multi-detector benchmark over labeled scenarios."""

    n: int
    overall: Confusion            # any-detector detection vs faulty/not-faulty
    per_detector: dict            # detector name -> Confusion (vs its target fault)
    correct_diagnosis: float      # of faulty scenarios, a detector for the right fault fired

    def as_dict(self) -> dict:
        """Return the report (with nested confusions) as a plain dict."""
        return {"n": self.n, "overall": self.overall.as_dict(),
                "per_detector": {k: v.as_dict() for k, v in self.per_detector.items()},
                "correct_diagnosis": self.correct_diagnosis}


def benchmark(records, detector_targets: dict) -> BenchmarkReport:
    """Score a detector suite across labeled scenarios.

    ``records``: iterable of ``{"truth": <fault-type str, "" if fault-free>,
    "fired": <iterable of detector names that flagged>}``.
    ``detector_targets``: ``{detector_name: fault_type_it_targets}``.

    Returns the overall detection confusion (any detector vs. faulty), a per-detector
    confusion (each detector judged against the scenarios of its target fault type),
    and the correct-diagnosis rate (a faulty scenario is correctly diagnosed when a
    detector whose target equals the true fault fired) -- the LBNL FDD evaluation
    framework, generalized across rules.
    """
    recs = [{"truth": (r.get("truth") or ""), "fired": set(r.get("fired") or ())}
            for r in records]
    overall = confusion([bool(r["truth"]) for r in recs],
                        [bool(r["fired"]) for r in recs])
    per = {d: confusion([r["truth"] == target for r in recs],
                        [d in r["fired"] for r in recs])
           for d, target in detector_targets.items()}
    faulty = [r for r in recs if r["truth"]]
    if faulty:
        correct = sum(1 for r in faulty
                      if any(detector_targets.get(d) == r["truth"] for d in r["fired"]))
        cd = round(correct / len(faulty), 4)
    else:
        cd = float("nan")
    return BenchmarkReport(n=len(recs), overall=overall, per_detector=per,
                           correct_diagnosis=cd)
