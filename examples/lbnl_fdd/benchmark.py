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
from camber.rules.chiller_rule import ChillerEfficiency  # noqa: E402
from camber.rules.coil_valve_rule import CoilValveDrift  # noqa: E402
from camber.rules.coolingtower_rule import CoolingTowerApproach  # noqa: E402
from camber.rules.duct_static_rule import DuctStaticControlDrift  # noqa: E402
from camber.rules.economizer_damper_rule import EconomizerDamperDrift  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from camber.rules.vav_airflow_rule import VavAirflowDrift  # noqa: E402
from camber.rules.vav_reheat_valve_rule import VavReheatValveDrift  # noqa: E402
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

# VAV zone-terminal drift on the LBNL Fan-Power-Unit subset (the West-zone box is the one faulted).
# vav_airflow_drift (DAMPER ~ AIRFLOW_SP) targets the damper-stuck + airflow-sensor-bias faults;
# vav_reheat_valve_drift (reheat valve at matched duty) targets the reheat-valve leak/stuck + coil
# fouling faults. Each is a cross-negative for the other. Single box -> no rogue/cohort here.
FPU_DRIFT_DETECTORS = {
    "vav_airflow_drift": {
        "build": lambda: VavAirflowDrift(BaselineStore(), site="lbnl_fpu", run_id="bench"),
        "equip": "lbnl_fpu",
        "positive": ("PFPU_VAVDMPRStuck", "PFPU_SensorBias_VAVAirflow"),
        "cross_negative": ("PFPU_ReheatVLV", "PFPU_ReheatCoil"),
    },
    "vav_reheat_valve_drift": {
        "build": lambda: VavReheatValveDrift(BaselineStore(), site="lbnl_fpu", run_id="bench"),
        "equip": "lbnl_fpu",
        "positive": ("PFPU_ReheatVLV", "PFPU_ReheatCoil"),
        "cross_negative": ("PFPU_VAVDMPRStuck", "PFPU_SensorBias_VAVAirflow"),
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
    equip = det.get("equip", "lbnl_sdahu")
    cases = [LabeledCase(equip, baseline, healthy_tail, fault=False, name="fault-free-tail")]
    pos = det.get("positive")
    pos = (pos,) if isinstance(pos, str) else (pos or ())  # str or tuple of positive-fault prefixes
    negs = det.get("cross_negative") or ()
    for name, frame in frames.items():
        if name == fault_free:
            continue
        if any(name.startswith(p) for p in pos):
            cases.append(LabeledCase(equip, baseline, frame, fault=True, name=name))
        elif any(name.startswith(p) for p in negs):
            cases.append(LabeledCase(equip, baseline, frame, fault=False, name=name))
    return cases


def score_drift(frames, detectors=None, *, fault_free="AHU_annual.csv", label="AHU air-side drift"):
    """Score a set of drift detectors on ``frames``; return the flat metrics dict.

    ``detectors`` defaults to the SDAHU air-side set; pass a subset-specific dict (e.g. the FPU VAV
    set) with its own ``fault_free`` baseline file to score another equipment subset with the same
    machinery.
    """
    detectors = detectors if detectors is not None else DRIFT_DETECTORS
    metrics = {}
    print(f"\n=== {label} (camber.driftvalidation) ===")
    for name, det in detectors.items():
        cases = build_drift_cases(frames, det, fault_free=fault_free)
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


# --------------------------------------------------------------------------- #
# Chiller-plant plant-level validation (opt-in via fetch.py --chiller).
#
# The LBNL chiller-plant data carries no refrigerant-side points (evaporator/condenser approach,
# subcooling, superheat), so the refrigerant-side chiller-drift detectors are NOT runnable on it and
# stay synthetic-only (camber.faultlab). What IS runnable are the two PLANT-LEVEL detectors:
# `chiller_efficiency` (kW/ton from metered power + CHW loop) and `cooling_tower_approach` (tower CW
# supply vs wet-bulb). Both use an equipment-specific *absolute* design ceiling, which the simulated
# chiller/tower curves don't publish -- so instead of guessing it, we CALIBRATE each detector's
# ceiling from the plant's own fault-free run (commissioning practice: set the design bar to the
# healthy baseline), then score the labeled physical heat-rejection faults. The fault-free run then
# reads ~1.0x (ok) by construction, so the informative number is the TPR on the faults: does a
# fouled tower / bypassed condenser loop push the metric past the rule's warn ratio? A sensor-bias
# run is a genuine negative for a *physical* detector (the plant is healthy, only a sensor lies) and
# measures the detector's robustness to instrument faults. See docs/VALIDATION.md.
# --------------------------------------------------------------------------- #

CHILLER_FAULT_FREE = "ChillerPlant.csv"
CHILLER_DETECTORS = {
    "cooling_tower_approach": {
        # tower fouling (fouled fill -> can't approach wet-bulb) + condenser-loop PID mistuning
        "make": lambda design=7.0: CoolingTowerApproach(design_approach_f=design),
        "metric": "approach_median_f",
        "positive": ("ChillerPlant_coolingtower_fouling", "ChillerPlant_coolingtower_PI"),
    },
    "chiller_efficiency": {
        # anything that raises chiller lift -> kW/ton: warmer condenser water from a fouled tower,
        # a bypassed three-way valve (leak/stuck), or condenser-loop PID mistuning
        "make": lambda design=0.85: ChillerEfficiency(design_kw_per_ton=design),
        "metric": "kw_per_ton_median",
        "positive": (
            "ChillerPlant_coolingtower_fouling",
            "ChillerPlant_coolingtower_PI",
            "ChillerPlant_bypass_leakage",
            "ChillerPlant_bypass_stuck",
        ),
    },
}


def _calibrated_metric(det, frame):
    """Return the detector's baseline metric on ``frame`` (None if the run is unusable)."""
    find = det["make"]().analyze("CH1", frame)
    val = find.metrics.get(det["metric"]) if find.metrics else None
    return val if (val is not None and val == val) else None  # not NaN


def score_chiller(frames, *, fault_free=CHILLER_FAULT_FREE, label="Chiller-plant heat rejection"):
    """Score the plant-level chiller detectors on ``{scenario_csv: role_frame}``; return metrics.

    Each detector's absolute design ceiling is calibrated from the fault-free run's healthy median
    (the metric is data-derived and design-independent), then the calibrated detector runs on every
    scenario. A run whose name starts with a detector's ``positive`` prefix is a target fault (it
    should fire); every other run -- fault-free and sensor-bias -- is a negative (stays quiet). Pure
    and deterministic given the frames, so it's unit-testable on synthetic plant-shaped frames.
    """
    metrics = {}
    ff = frames.get(fault_free)
    print(f"\n=== {label} (plant-level detectors, baseline-calibrated) ===")
    if ff is None:
        print("  (fault-free baseline absent — skipped)")
        return metrics
    for name, det in CHILLER_DETECTORS.items():
        healthy = _calibrated_metric(det, ff)
        if healthy is None:
            print(f"  {name:24s} skipped (no healthy baseline metric)")
            continue
        rule = det["make"](healthy)  # design ceiling := the healthy plant's own median
        tp = fn = fp = tn = 0
        for fname, frame in sorted(frames.items()):
            fired = rule.analyze("CH1", frame).severity in ("warn", "fault")
            if any(fname.startswith(p) for p in det["positive"]):
                tp, fn = tp + fired, fn + (not fired)
            else:
                fp, tn = fp + fired, tn + (not fired)
        tpr = tp / (tp + fn) if (tp + fn) else float("nan")
        fpr = fp / (fp + tn) if (fp + tn) else float("nan")
        print(
            f"  {name:24s} design~{healthy:.2f}  TPR {tpr:.0%} (n={tp + fn})  "
            f"FPR {fpr:.0%} (n={fp + tn})"
        )
        for key, val in (("tpr", tpr), ("fpr", fpr)):
            if val == val:  # omit NaN -> valid JSON
                metrics[f"chiller.{name}.{key}"] = round(val, 4)
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
        metrics.update(score_drift(sd_frames, label="AHU air-side drift (SDAHU)"))

    # VAV zone-terminal drift on the Fan-Power-Unit subset (opt-in via fetch.py --fpu)
    fpu_map_path = os.path.join(HERE, "mapping_fpu.json")
    fpu_base = os.path.join(DATA, "fpu")
    if os.path.exists(os.path.join(fpu_base, "PFPU_FaultFree.csv")) and os.path.exists(
        fpu_map_path
    ):
        fpu_mapping = MappingProvider.from_dict(json.load(open(fpu_map_path)))
        fpu_frames = {
            f: load_role_frame(os.path.join(fpu_base, f), fpu_mapping)
            for f in os.listdir(fpu_base)
            if f.endswith(".csv")
        }
        metrics.update(
            score_drift(
                fpu_frames,
                FPU_DRIFT_DETECTORS,
                fault_free="PFPU_FaultFree.csv",
                label="VAV zone-terminal drift (FPU)",
            )
        )

    # Chiller-plant plant-level detectors (opt-in via fetch.py --chiller)
    chiller_map_path = os.path.join(HERE, "mapping_chiller.json")
    chiller_base = os.path.join(DATA, "chiller")
    if os.path.exists(os.path.join(chiller_base, CHILLER_FAULT_FREE)) and os.path.exists(
        chiller_map_path
    ):
        chiller_mapping = MappingProvider.from_dict(json.load(open(chiller_map_path)))
        chiller_frames = {
            f: load_role_frame(os.path.join(chiller_base, f), chiller_mapping)
            for f in os.listdir(chiller_base)
            if f.endswith(".csv")
        }
        metrics.update(score_chiller(chiller_frames))

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
