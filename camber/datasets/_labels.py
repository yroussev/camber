"""Score a run's findings against the fault labels recorded at ingest.

A labelled dataset's ingest records ``{equipment: fault type}`` on the facility (``""`` for a
fault-free run). Scoring turns findings into the LBNL FDD evaluation records the benchmark uses --
``{"truth": fault type, "fired": {detector, ...}}`` per scenario -- and reports the overall
detection confusion, each detector's confusion against its target fault, and the correct-diagnosis
rate, every rate with a Wilson 95% interval (:func:`camber.validation.metrics_with_ci`).

The **scored suite** is the entry's declared detector targets (``labels.targets``) when it has
any, so rules a template runs for context never change the score; pass ``rules=`` to override.
Spliced onset runs are drift-exercise material, not independent scenarios, and are not scored.
"""

from __future__ import annotations

from ..eval import benchmark
from ..validation import metrics_with_ci

_FIRED = ("warn", "fault")

__all__ = ["records_from_findings", "score_records"]


def _get(f, key):
    return f.get(key) if isinstance(f, dict) else getattr(f, key, None)


def records_from_findings(findings, labels: dict, suite=None) -> list:
    """One benchmark record per labelled equipment (sorted by equipment id).

    ``findings`` are :class:`~camber.rules.base.Finding` objects or their ``as_dict()`` form (a
    ``findings.json`` written by ``camber run --out``). ``suite`` limits which rules count as
    detectors (``None`` -> every rule present in the findings).
    """
    wanted = None if suite is None else set(suite)
    fired: dict = {}
    for f in findings:
        rule, equip = _get(f, "rule"), _get(f, "equip")
        if _get(f, "severity") not in _FIRED or equip not in labels:
            continue
        if wanted is not None and rule not in wanted:
            continue
        fired.setdefault(equip, set()).add(rule)
    return [
        {"equip": eq, "truth": labels[eq] or "", "fired": sorted(fired.get(eq, set()))}
        for eq in sorted(labels)
    ]


def _rates(conf) -> dict:
    ci = metrics_with_ci(conf)
    out = {"n": conf.total, "tp": conf.tp, "fp": conf.fp, "tn": conf.tn, "fn": conf.fn}
    for key, short in (
        ("true_positive_rate", "tpr"),
        ("false_positive_rate", "fpr"),
        ("accuracy", "accuracy"),
    ):
        r = ci[key]
        out[short] = r.rate
        out[f"{short}_ci"] = [r.lo, r.hi]
    return out


def score_records(records: list, targets: dict) -> dict:
    """Benchmark ``records`` against ``targets`` (``{detector: fault type}``)."""
    rep = benchmark(records, targets)
    return {
        "n": rep.n,
        "overall": _rates(rep.overall),
        "correct_diagnosis": rep.correct_diagnosis,
        "per_detector": {d: _rates(c) for d, c in rep.per_detector.items() if c.total},
    }
