"""FDD-accuracy benchmark: score the detector suite across LBNL equipment families.

Runs CAMBER diagnostics over labeled fault scenarios from THREE different LBNL
equipment types and naming conventions -- single-duct AHU (SDAHU), fan-coil unit
(FCU), and dual-duct AHU (DDAHU) -- and scores them with the generalized evaluation
harness (`camber.eval.benchmark`): overall detection, per-detector confusion against
each detector's target fault, and the correct-diagnosis rate.

The point: the *same* role-based rules run unchanged across all three families; only
the mapping config differs. Each family is scored on its own, then pooled into one
cross-equipment benchmark -- the LBNL FDD performance-evaluation approach applied
across the rule library and across equipment types, so coverage gaps are measured,
not guessed.

Run fetch.py (with --families for FCU/DDAHU) first.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd  # noqa: E402

from camber.driftvalidation import LabeledCase, evaluate  # noqa: E402
from camber.eval import benchmark  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.rules.coil_valve_rule import CoilValveDrift  # noqa: E402
from camber.rules.duct_static_rule import DuctStaticControlDrift  # noqa: E402
from camber.rules.economizer_damper_rule import EconomizerDamperDrift  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402
from camber.units import normalize_percent_frame  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "lbnl")

# Detector names are constant; each targets one fault type. OA-fraction is shared
# across all families; the leak detector only applies to the SDAHU coil-leak case.
TARGETS = {OutdoorAirFraction().name: "damper", LeakingValve().name: "valve_leak"}

# One entry per equipment family. `min_oa_pct` is the unit's *design minimum* OA
# (a per-equipment sequence parameter, not a fudge factor): single-duct AHUs here
# sit at ~20%, this FCU at ~10%. `use_leak` adds the coil-leak detector where a
# labeled leak scenario exists.
FAMILIES = [
    {
        "label": "SDAHU (single-duct AHU)",
        "dir": "sdahu",
        "mapping": "mapping.json",
        "min_oa_pct": 20.0,
        "use_leak": True,
        "scenarios": [
            ("AHU_annual.csv", ""),
            ("damper_stuck_010_annual.csv", "damper"),
            ("damper_stuck_025_annual.csv", "damper"),
            ("damper_stuck_075_annual.csv", "damper"),
            ("damper_stuck_100_annual_short.csv", "damper"),
            # cooling-coil-valve leakage severity sweep (characterizes the leak detector, which
            # under-fires at low severity — each missing CSV is skipped via the
            # os.path.exists guard)
            ("coi_leakage_010_annual.csv", "valve_leak"),
            ("coi_leakage_025_annual.csv", "valve_leak"),
            ("coi_leakage_050_annual.csv", "valve_leak"),
            ("coi_leakage_100_annual.csv", "valve_leak"),
        ],
    },
    {
        "label": "FCU (fan-coil unit)",
        "dir": "fcu",
        "mapping": "mapping_fcu.json",
        "min_oa_pct": 10.0,
        "use_leak": False,
        "scenarios": [
            ("FCU_FaultFree.csv", ""),
            ("FCU_OADMPRStuck_0.csv", "damper"),
            ("FCU_OADMPRStuck_100.csv", "damper"),
            ("FCU_OADMPRLeak_50.csv", "damper"),
        ],
    },
    {
        "label": "DDAHU (dual-duct AHU)",
        "dir": "ddahu",
        "mapping": "mapping_ddahu.json",
        "min_oa_pct": 20.0,
        "use_leak": False,
        "scenarios": [
            ("DualDuct_FaultFree.csv", ""),
            ("DualDuct_DMPRStuck_OA_0.csv", "damper"),
            ("DualDuct_DMPRStuck_OA_100.csv", "damper"),
        ],
    },
]


def load_role_frame(csv, mapping):
    """Read one LBNL CSV into an hourly role-named frame via the family's mapping."""
    df = (
        pd.read_csv(
            csv, usecols=lambda c: c == "Datetime" or mapping.role_of(c), parse_dates=["Datetime"]
        )
        .set_index("Datetime")
        .resample("1h")
        .mean()
    )
    frame = pd.DataFrame({mapping.role_of(c): df[c] for c in df.columns if mapping.role_of(c)})
    return normalize_percent_frame(frame)


def score_family(fam):
    """Run the family's detectors over its scenarios; return the records list."""
    mapping = MappingProvider.from_dict(json.load(open(os.path.join(HERE, fam["mapping"]))))
    detectors = [OutdoorAirFraction(min_oa_pct=fam["min_oa_pct"])]
    if fam["use_leak"]:
        detectors.append(LeakingValve())
    base = os.path.join(DATA, fam["dir"])
    records = []
    print(f"\n=== {fam['label']}  (min OA {fam['min_oa_pct']:.0f}%) ===")
    print(f"{'scenario':32s} {'truth':11s} fired")
    for fname, truth in fam["scenarios"]:
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            continue
        frame = load_role_frame(path, mapping)
        fired = {
            rule.name
            for rule in detectors
            if rule.analyze("EQUIP", frame).severity in ("warn", "fault")
        }
        records.append({"truth": truth, "fired": fired})
        print(f"{fname:32s} {truth or 'fault-free':11s} {sorted(fired)}")
    return records


def print_scores(title, records):
    """Print the benchmark scores for a set of records, with Wilson confidence intervals."""
    from camber.validation import metrics_with_ci  # noqa: E402

    rep = benchmark(records, TARGETS)
    o = rep.overall
    print(f"\n--- {title}: scores (LBNL eval framework, 95% Wilson CI) ---")
    oc = metrics_with_ci(o)
    print(
        f"overall detection: TPR {o.true_positive_rate:.0%} "
        f"[{oc['true_positive_rate'].lo:.0%}-{oc['true_positive_rate'].hi:.0%}]  "
        f"FPR {o.false_positive_rate:.0%}  accuracy {o.accuracy:.0%}"
    )
    print(f"correct diagnosis (right detector for the fault): {rep.correct_diagnosis:.0%}")
    for name, c in rep.per_detector.items():
        if c.total:
            ci = metrics_with_ci(c)
            t, f = ci["true_positive_rate"], ci["false_positive_rate"]
            print(
                f"  {name:22s} TPR {t.rate:.0%} [{t.lo:.0%}-{t.hi:.0%}]  "
                f"FPR {f.rate:.0%} [{f.lo:.0%}-{f.hi:.0%}]  (n={c.total})"
            )


def metrics_dict(label, records):
    """Flatten a family's benchmark to ``{label.tpr, label.fpr, label.accuracy,
    label.correct_diagnosis}`` for JSON output and baseline gating."""
    rep = benchmark(records, TARGETS)
    o = rep.overall
    return {
        f"{label}.tpr": round(o.true_positive_rate, 4),
        f"{label}.fpr": round(o.false_positive_rate, 4),
        f"{label}.accuracy": round(o.accuracy, 4),
        f"{label}.correct_diagnosis": round(rep.correct_diagnosis, 4),
    }


# --------------------------------------------------------------------------- #
# Drift-family real-data validation (SDAHU only).
#
# Only the AHU air-side *drift* detectors whose required points LBNL actually exports can be scored
# on real labeled faults: coil-valve drift (target = valve leak) and economizer-damper drift
# (target = stuck damper) get real TPR; duct-static-control drift has no labeled fault in the
# set, so it contributes a specificity (false-positive) number only. Fan-efficiency and filter
# drift need POWER / FILTER_DIFF_PRESS points the SDAHU sim does not export -> synthetic-only
# (camber.faultlab). The multi-zone rogue/cohort census and the reset-request detectors are not
# validatable on a single simulated AHU at all. See docs/VALIDATION.md for the full matrix.
#
# A drift detector freezes its baseline the first time it sees a period, so each case pairs the
# fault-free run's first 60% (baseline) with a *current* window: the fault-free tail (a genuine
# negative) or a faulted run (a positive). A run targeting a *different* fault is a cross-negative.
# --------------------------------------------------------------------------- #

DRIFT_DETECTORS = {
    "coil_valve_drift": {
        "build": lambda: CoilValveDrift(BaselineStore(), site="lbnl_sdahu", run_id="bench"),
        "positive": "coi_leakage",  # labeled fault this detector targets
        "cross_negative": ("damper_stuck",),  # a fault it does NOT target -> negative
    },
    "economizer_damper_drift": {
        "build": lambda: EconomizerDamperDrift(BaselineStore(), site="lbnl_sdahu", run_id="bench"),
        "positive": "damper_stuck",
        "cross_negative": ("coi_leakage",),
    },
    "duct_static_drift": {  # specificity only: no labeled duct-static fault in the fetched set
        "build": lambda: DuctStaticControlDrift(BaselineStore(), site="lbnl_sdahu", run_id="bench"),
        "positive": None,
        "cross_negative": ("damper_stuck", "coi_leakage"),
    },
}


def build_drift_cases(frames, det, *, baseline_frac=0.6, fault_free="AHU_annual.csv"):
    """Build labeled drift cases from ``{scenario_csv: role_frame}`` for one detector spec.

    The fault-free run is split baseline (first ``baseline_frac``) vs a healthy current tail (a
    ``fault=False`` negative). Runs whose name starts with ``det["positive"]`` are ``fault=True``;
    runs matching ``det["cross_negative"]`` are ``fault=False``. Pure + deterministic (unit-testable
    without the dataset).
    """
    ff = frames.get(fault_free)
    if ff is None or len(ff) < 20:
        return []
    cut = int(len(ff) * baseline_frac)
    baseline, healthy_tail = ff.iloc[:cut], ff.iloc[cut:]
    cases = [LabeledCase("lbnl_sdahu", baseline, healthy_tail, fault=False, name="fault-free-tail")]
    pos, negs = det.get("positive"), det.get("cross_negative") or ()
    for name, frame in frames.items():
        if name == fault_free:
            continue
        if pos and name.startswith(pos):
            cases.append(LabeledCase("lbnl_sdahu", baseline, frame, fault=True, name=name))
        elif any(name.startswith(p) for p in negs):
            cases.append(LabeledCase("lbnl_sdahu", baseline, frame, fault=False, name=name))
    return cases


def score_drift(sdahu_frames):
    """Score the runnable AHU-drift detectors on the SDAHU frames; return the flat metrics dict."""
    metrics = {}
    print("\n=== AHU air-side drift (SDAHU, camber.driftvalidation) ===")
    for name, det in DRIFT_DETECTORS.items():
        cases = build_drift_cases(sdahu_frames, det)
        pos = sum(1 for c in cases if c.fault)
        if len(cases) < 2 or (det["positive"] and pos == 0):
            print(f"  {name:24s} skipped (no usable cases)")
            continue
        score = evaluate(det["build"], cases)
        c = score.confusion
        fpr = round(c.fp / (c.fp + c.tn), 4) if (c.fp + c.tn) else 0.0
        kind = "specificity" if det["positive"] is None else "TPR"
        print(
            f"  {name:24s} recall {score.recall} precision {score.precision} "
            f"f1 {score.f1} fpr {fpr}  (n={score.n}, {kind})"
        )
        # omit NaN metrics (a specificity-only detector has no recall/precision) -> valid JSON
        for key, val in (
            ("recall", score.recall),
            ("precision", score.precision),
            ("f1", score.f1),
            ("fpr", fpr),
        ):
            if val == val:  # not NaN
                metrics[f"drift.{name}.{key}"] = round(val, 4)
    return metrics


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LBNL cross-equipment FDD benchmark")
    ap.add_argument("--json", metavar="PATH", help="write the flat metrics dict as JSON")
    ap.add_argument(
        "--gate", metavar="PATH", help="baseline JSON to gate against (exit 2 on regression)"
    )
    ap.add_argument("--tol", type=float, default=0.02, help="regression tolerance (default 0.02)")
    ap.add_argument(
        "--update-baseline", metavar="PATH", help="write current metrics as a new baseline JSON"
    )
    args = ap.parse_args(argv)

    if not os.path.exists(os.path.join(DATA, "sdahu", FAMILIES[0]["scenarios"][0][0])):
        print("Data not found. Run:  python examples/lbnl_fdd/fetch.py")
        return 1

    pooled, metrics = [], {}
    for fam in FAMILIES:
        recs = score_family(fam)
        if recs:
            print_scores(fam["label"], recs)
            metrics.update(metrics_dict(fam["label"], recs))
            pooled.extend(recs)

    # AHU air-side drift-family validation (SDAHU only — the family with the full air-side points)
    sdahu = FAMILIES[0]
    sd_mapping = MappingProvider.from_dict(json.load(open(os.path.join(HERE, sdahu["mapping"]))))
    sd_base = os.path.join(DATA, sdahu["dir"])
    sd_frames = {
        fname: load_role_frame(os.path.join(sd_base, fname), sd_mapping)
        for fname, _ in sdahu["scenarios"]
        if os.path.exists(os.path.join(sd_base, fname))
    }
    if sd_frames:
        metrics.update(score_drift(sd_frames))

    families_present = sum(
        1
        for fam in FAMILIES
        if os.path.exists(os.path.join(DATA, fam["dir"], fam["scenarios"][0][0]))
    )
    if families_present > 1:
        print("\n" + "=" * 60)
        print_scores(f"POOLED across {families_present} equipment families", pooled)
        metrics.update(metrics_dict("pooled", pooled))
        print("\nThe same role-based detectors run unchanged across single-duct AHUs,")
        print("fan-coil units, and dual-duct AHUs -- only the point->role mapping and the")
        print("unit's design-min OA differ. OA-fraction transfers cleanly to single-duct")
        print("AHUs and FCUs; on dual-duct AHUs it degrades (the hot/cold-deck mixing and")
        print("mild-weather OAF noise blur the signal) -- a transferability gap the")
        print("cross-equipment benchmark measures rather than hides.")
    elif families_present == 1:
        print("\n(Only SDAHU present. Run `python examples/lbnl_fdd/fetch.py --families`")
        print(" to download FCU + DDAHU and score the full cross-equipment benchmark.)")

    if args.json:
        json.dump(metrics, open(args.json, "w"), indent=2, sort_keys=True)
        print(f"\nwrote metrics -> {args.json}")
    if args.update_baseline:
        json.dump(metrics, open(args.update_baseline, "w"), indent=2, sort_keys=True)
        print(f"wrote baseline -> {args.update_baseline}")
    if args.gate:
        from camber.eval import check_against_baseline

        baseline = json.load(open(args.gate))
        chk = check_against_baseline(metrics, baseline, tol=args.tol)
        if not chk.passed:
            print(f"\n✗ BENCHMARK REGRESSION (tol {args.tol}):")
            for k, b, c, d in chk.regressions:
                print(f"    {k}: {b} -> {c}  ({d:+})")
            for k in chk.missing:
                print(f"    {k}: missing from current run")
            return 2
        print(
            f"\n✓ benchmark gate OK — {chk.unchanged} stable, "
            f"{len(chk.improvements)} improved, none regressed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
