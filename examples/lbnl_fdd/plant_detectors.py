"""Score the 0.92 plant detectors on the labelled LBNL chiller- and boiler-plant runs (ungated).

Three detectors added in 0.92, each scored with its target faults as positives and every other run
-- the fault-free year, the other physical faults and the sensor biases -- as negatives:

* ``boiler_efficiency_drift`` (#13) on the boiler plant: target = the three boiler-fouling runs.
  A drift rule, so the baseline is the fault-free year and each run is the current period.
* ``cooling_tower_fan_effort_drift`` (#14) on the chiller plant: target = the three tower-fouling
  runs; baseline = the fault-free year. Its sibling ``cooling_tower_approach_drift`` is scored the
  same way for comparison.
* ``condenser_bypass_leak`` (#15) on the chiller plant: target = the five bypass leakage/stuck
  runs. A single-period rule, run on each year.

A detection is a ``warn`` or ``fault``; ``info`` (a declined case, or an attribution to a sensor
problem rather than the equipment) is not. Rates carry 95 % Wilson intervals: with 3-5 positives a
run set this small can only bound a detector, not rank it. These numbers are **measured, not gated**
(the chiller and boiler subsets are opt-in downloads); see docs/VALIDATION.md.

Run ``python examples/lbnl_fdd/fetch.py --chiller`` (and the ``lbnl-boiler`` catalog entry for the
boiler plant) first; the script scores whatever is present.
"""

from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from benchmark import catalog_spec, load_role_frame, packaged_mapping  # noqa: E402

from camber.model.mapping import MappingProvider  # noqa: E402
from camber.rules.boiler_efficiency_rule import BoilerEfficiencyDrift  # noqa: E402
from camber.rules.condenser_bypass_rule import CondenserBypassLeak  # noqa: E402
from camber.rules.coolingtower_drift_rule import CoolingTowerApproachDrift  # noqa: E402
from camber.rules.tower_fan_effort_rule import CoolingTowerFanEffortDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402
from camber.validation import wilson_interval  # noqa: E402

DATA = os.path.join(HERE, "..", "_data")
CHILLER_DIR = os.path.join(DATA, "lbnl", "chiller")
BOILER_GLOB = os.path.join(DATA, "lbnl_boiler", "full", "*", "*.csv")
CHILLER_FAULT_FREE = "ChillerPlant.csv"
BOILER_FAULT_FREE = "BoilerPlant.csv"

#: detector -> (plant, kind, factory, target-run prefixes)
DETECTORS = {
    "boiler_efficiency_drift": (
        "boiler",
        "period",
        lambda: BoilerEfficiencyDrift(BaselineStore(), site="LBNL", run_id="validation"),
        ("BoilerPlant_boiler_foul",),
    ),
    "cooling_tower_fan_effort_drift": (
        "chiller",
        "period",
        lambda: CoolingTowerFanEffortDrift(BaselineStore(), site="LBNL", run_id="validation"),
        ("ChillerPlant_coolingtower_fouling",),
    ),
    "cooling_tower_approach_drift": (
        "chiller",
        "period",
        lambda: CoolingTowerApproachDrift(BaselineStore(), site="LBNL", run_id="validation"),
        ("ChillerPlant_coolingtower_fouling",),
    ),
    "condenser_bypass_leak": (
        "chiller",
        "single",
        CondenserBypassLeak,
        ("ChillerPlant_bypass_leakage", "ChillerPlant_bypass_stuck"),
    ),
}


def load_frames(plant: str) -> dict:
    """``{run file: hourly role frame}`` through the catalog's mapping and ingest spec."""
    if plant == "chiller":
        paths = sorted(glob.glob(os.path.join(CHILLER_DIR, "*.csv")))
        mapping, spec = packaged_mapping("lbnl_chiller.json"), catalog_spec("lbnl-chiller")
    else:
        paths = sorted(glob.glob(BOILER_GLOB))
        mapping, spec = packaged_mapping("lbnl_boiler.json"), catalog_spec("lbnl-boiler")
    mp = MappingProvider.from_dict(mapping)
    return {os.path.basename(p): load_role_frame(p, mp, spec=spec) for p in paths}


def _rate(k: int, n: int) -> dict:
    lo, hi = wilson_interval(k, n)
    return {
        "k": k,
        "n": n,
        "rate": round(k / n, 4) if n else None,
        "ci95": [round(lo, 3), round(hi, 3)] if n else None,
    }


def score(name: str, frames: dict, fault_free: str) -> dict:
    """Score one detector on ``frames``; returns the confusion, rates and per-run verdicts."""
    _plant, kind, make, targets = DETECTORS[name]
    base = frames[fault_free]
    tp = fn = fp = tn = declined = 0
    runs = {}
    for fname, frame in sorted(frames.items()):
        rule = make()
        if kind == "period":
            f = rule.analyze_periods("PLANT", base, frame)
        else:
            f = rule.analyze("PLANT", frame)
        m = f.metrics or {}
        runs[fname] = {"severity": f.severity, "attribution": m.get("attribution")}
        if m.get("declined"):
            declined += 1
            continue
        fired = f.severity in ("warn", "fault")
        if fname.startswith(targets):
            tp, fn = tp + fired, fn + (not fired)
        else:
            fp, tn = fp + fired, tn + (not fired)
    return {
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "declined": declined,
        "tpr": _rate(tp, tp + fn),
        "fpr": _rate(fp, fp + tn),
        "runs": runs,
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="0.92 plant detectors on the LBNL plant runs")
    ap.add_argument("--json", metavar="PATH", help="write the results as JSON")
    args = ap.parse_args(argv)
    out: dict = {}
    for plant, fault_free in (("boiler", BOILER_FAULT_FREE), ("chiller", CHILLER_FAULT_FREE)):
        frames = load_frames(plant)
        if fault_free not in frames:
            print(f"{plant} plant: data not found (skipped)")
            continue
        for name, (p, *_rest) in DETECTORS.items():
            if p != plant:
                continue
            r = score(name, frames, fault_free)
            out[name] = r
            t, f = r["tpr"], r["fpr"]
            print(
                f"{name:32s} TPR {t['k']}/{t['n']} (95% CI {t['ci95'][0]:.2f}-{t['ci95'][1]:.2f})  "
                f"FPR {f['k']}/{f['n']} (95% CI {f['ci95'][0]:.2f}-{f['ci95'][1]:.2f})  "
                f"{r['declined']} declined"
            )
            for fname, v in r["runs"].items():
                if v["attribution"] in ("heat_metering", "sensor_offset"):
                    print(f"    {fname}: {v['severity']} ({v['attribution']})")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
