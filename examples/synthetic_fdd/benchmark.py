"""Synthetic FDD-accuracy benchmark — score the rule suite against injected labeled faults.

Complements the real-data ``examples/lbnl_fdd`` benchmark: that one proves a few detectors on LBNL's
public labeled faults; this one deterministically scores *every covered rule* against its own
injected fault (and a clean frame), so the whole single-equipment suite is accuracy-measured
with no download. Reuses ``camber.eval.benchmark`` (the LBNL evaluation method) and gates
against a committed baseline.

    python examples/synthetic_fdd/benchmark.py                       # print scores + coverage
    python examples/synthetic_fdd/benchmark.py --gate examples/synthetic_fdd/benchmark-baseline.json
    python examples/synthetic_fdd/benchmark.py --update-baseline \
        examples/synthetic_fdd/benchmark-baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from camber import faultlab  # noqa: E402
from camber.eval import benchmark, check_against_baseline  # noqa: E402


def metrics_dict() -> dict:
    """Flat, deterministic metrics for JSON output + baseline gating."""
    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    cov = faultlab.coverage()
    g36 = faultlab.g36_accuracy()
    o = rep.overall
    m = {
        "overall.tpr": round(o.true_positive_rate, 4),
        "overall.fpr": round(o.false_positive_rate, 4),
        "overall.accuracy": round(o.accuracy, 4),
        "coverage.n_scored": len(cov["scored"]),
        "coverage.n_single": cov["n_single"],
        "g36.tpr": g36["tpr"],
        "g36.fpr": g36["fpr"],
        "g36.n_fc": g36["n_fc"],
    }
    for name, c in rep.per_detector.items():
        m[f"{name}.tpr"] = round(c.true_positive_rate, 4)
        m[f"{name}.fpr"] = round(c.false_positive_rate, 4)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="synthetic whole-suite FDD accuracy benchmark")
    ap.add_argument("--json", metavar="PATH", help="write the flat metrics dict as JSON")
    ap.add_argument(
        "--gate", metavar="PATH", help="baseline JSON to gate against (exit 2 on regression)"
    )
    ap.add_argument(
        "--tol", type=float, default=0.0, help="regression tolerance (default 0.0 — deterministic)"
    )
    ap.add_argument(
        "--update-baseline", metavar="PATH", help="write current metrics as a new baseline"
    )
    args = ap.parse_args(argv)

    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    cov = faultlab.coverage()
    o = rep.overall
    print(
        f"Synthetic FDD accuracy — {len(cov['scored'])}/{cov['n_single']} "
        f"single-equipment rules scored"
    )
    print(
        f"  overall: TPR {o.true_positive_rate:.0%}  FPR {o.false_positive_rate:.0%}  "
        f"accuracy {o.accuracy:.0%}  (n={o.total})"
    )
    print(f"\n{'rule':26s} {'TPR':>5} {'FPR':>5}")
    for name, c in sorted(rep.per_detector.items()):
        if c.total:
            print(f"  {name:24s} {c.true_positive_rate:4.0%} {c.false_positive_rate:5.0%}")

    g36 = faultlab.g36_accuracy()
    print(
        f"\nG36 §5.16.14 FC engine — {g36['n_fc']} representative fault conditions:  "
        f"TPR {g36['tpr']:.0%}  FPR {g36['fpr']:.0%}"
    )
    for fc, d in sorted(g36["per_fc"].items()):
        print(
            f"  FC{fc:<2} faulty {d['faulty_pct']:5.0f}%  clean {d['clean_pct']:4.0f}%  "
            f"{'detected' if d['detected'] else 'MISSED':8s} "
            f"{'quiet' if d['clean_quiet'] else 'NOISY'}"
        )

    print("\nCoverage (honest — LBNL labels only a few AHU faults, so the rest are scored here):")
    print(f"  scored ({len(cov['scored'])}): {', '.join(cov['scored'])}")
    print(f"  fixture-only ({len(cov['fixture_only'])}): {', '.join(cov['fixture_only'])}")
    print(f"  fleet, scored elsewhere ({len(cov['fleet'])}): {', '.join(cov['fleet'])}")
    xf = faultlab.cross_fire()
    if xf:
        print(
            "\nCo-detections (a scenario legitimately containing a second condition — "
            "not a false positive):"
        )
        for name, others in xf.items():
            print(f"  {name}: also {', '.join(others)}")

    m = metrics_dict()
    if args.json:
        json.dump(m, open(args.json, "w"), indent=2, sort_keys=True)
        print(f"\nwrote metrics -> {args.json}")
    if args.update_baseline:
        json.dump(m, open(args.update_baseline, "w"), indent=2, sort_keys=True)
        print(f"wrote baseline -> {args.update_baseline}")
    if args.gate:
        chk = check_against_baseline(m, json.load(open(args.gate)), tol=args.tol)
        if not chk.passed:
            print(f"\n✗ SYNTHETIC BENCHMARK REGRESSION (tol {args.tol}):")
            for k, b, c, d in chk.regressions:
                print(f"    {k}: {b} -> {c}  ({d:+})")
            for k in chk.missing:
                print(f"    {k}: missing from current run")
            return 2
        print(
            f"\n✓ gate OK — {chk.unchanged} stable, "
            f"{len(chk.improvements)} improved, none regressed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
