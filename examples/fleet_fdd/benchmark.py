"""Fleet FDD-accuracy benchmark — score the G36 reset fleet detectors on generated labeled fleets.

Complements the real-data ``examples/lbnl_fdd`` and synthetic ``examples/synthetic_fdd`` benchmarks.
The multi-zone **rogue-zone census**, **cohort-starvation**, and **reset-effectiveness** detectors
can't be scored on public data (no vendorable labeled multi-zone-fleet fault dataset exists — see
``docs/VALIDATION.md``), so this benchmark scores them on fleets *generated* from the public ASHRAE
Guideline 36 Trim-&-Respond logic itself (``camber.fleetlab``): per-zone reset requests → the T&R
reset they imply → one injected fault (a rogue zone, a starved cohort, or an inert reset). It
reports per-detector TPR/FPR **and** an **attribution** rate (did the detector name the right zone /
air handler / failure mode?) — the guard against a generator so easy that firing is meaningless —
and gates against a committed baseline. Deterministic, no download.

    python examples/fleet_fdd/benchmark.py                                   # print scores
    python examples/fleet_fdd/benchmark.py --gate <baseline.json>            # CI gate
    python examples/fleet_fdd/benchmark.py --update-baseline <baseline.json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from camber import fleetlab  # noqa: E402
from camber.eval import benchmark, check_against_baseline  # noqa: E402


def metrics_dict() -> dict:
    """Flat, deterministic metrics for JSON output + baseline gating."""
    rep = benchmark(fleetlab.labeled_records(), fleetlab.targets())
    attrib = fleetlab.attribution()
    cov = fleetlab.coverage()
    o = rep.overall
    m = {
        "fleet.overall.tpr": round(o.true_positive_rate, 4),
        "fleet.overall.fpr": round(o.false_positive_rate, 4),
        "fleet.overall.accuracy": round(o.accuracy, 4),
        "fleet.correct_diagnosis": round(rep.correct_diagnosis, 4),
        "coverage.n_fleet_scored": cov["n_fleet_scored"],
    }
    for name, c in rep.per_detector.items():
        m[f"fleet.{name}.tpr"] = round(c.true_positive_rate, 4)
        m[f"fleet.{name}.fpr"] = round(c.false_positive_rate, 4)
    for name, a in attrib.items():
        m[f"fleet.{name}.attribution"] = round(a, 4)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="generated-fleet G36 reset FDD accuracy benchmark")
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

    rep = benchmark(fleetlab.labeled_records(), fleetlab.targets())
    attrib = fleetlab.attribution()
    cov = fleetlab.coverage()
    o = rep.overall
    print(
        f"Fleet FDD accuracy — {cov['n_fleet_scored']} G36 reset fleet detectors on generated "
        f"labeled fleets (rogue / cohort / reset, x sat+static)"
    )
    print(
        f"  overall: TPR {o.true_positive_rate:.0%}  FPR {o.false_positive_rate:.0%}  "
        f"accuracy {o.accuracy:.0%}  correct-diagnosis {rep.correct_diagnosis:.0%}  (n={o.total})"
    )
    print(f"\n{'detector':30s} {'TPR':>5} {'FPR':>5} {'attrib':>7}   (P/N)")
    for name, c in sorted(rep.per_detector.items()):
        print(
            f"  {name:28s} {c.true_positive_rate:4.0%} {c.false_positive_rate:5.0%} "
            f"{attrib.get(name, float('nan')):6.0%}   ({c.tp + c.fn}/{c.fp + c.tn})"
        )
    print(
        "\nAttribution = fraction of a detector's positive fleets where it named the right zone / "
        "air handler / reset failure mode (not just fired). The fleet is GENERATED from the public "
        "G36 T&R logic, not downloaded — internal-validity accuracy; see docs/VALIDATION.md."
    )

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
            print(f"\n✗ FLEET BENCHMARK REGRESSION (tol {args.tol}):")
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
