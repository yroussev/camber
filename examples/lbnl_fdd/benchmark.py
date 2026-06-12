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

from camber.eval import benchmark  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
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
            ("coi_leakage_050_annual.csv", "valve_leak"),
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
    df = (pd.read_csv(csv, usecols=lambda c: c == "Datetime" or mapping.role_of(c),
                      parse_dates=["Datetime"])
          .set_index("Datetime").resample("1h").mean())
    frame = pd.DataFrame({mapping.role_of(c): df[c] for c in df.columns
                          if mapping.role_of(c)})
    return normalize_percent_frame(frame)


def score_family(fam):
    """Run the family's detectors over its scenarios; return the records list."""
    mapping = MappingProvider.from_dict(
        json.load(open(os.path.join(HERE, fam["mapping"]))))
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
        fired = {rule.name for rule in detectors
                 if rule.analyze("EQUIP", frame).severity in ("warn", "fault")}
        records.append({"truth": truth, "fired": fired})
        print(f"{fname:32s} {truth or 'fault-free':11s} {sorted(fired)}")
    return records


def print_scores(title, records):
    """Print the benchmark scores for a set of records, with Wilson confidence intervals."""
    from camber.validation import metrics_with_ci   # noqa: E402

    rep = benchmark(records, TARGETS)
    o = rep.overall
    print(f"\n--- {title}: scores (LBNL eval framework, 95% Wilson CI) ---")
    oc = metrics_with_ci(o)
    print(f"overall detection: TPR {o.true_positive_rate:.0%} "
          f"[{oc['true_positive_rate'].lo:.0%}-{oc['true_positive_rate'].hi:.0%}]  "
          f"FPR {o.false_positive_rate:.0%}  accuracy {o.accuracy:.0%}")
    print(f"correct diagnosis (right detector for the fault): {rep.correct_diagnosis:.0%}")
    for name, c in rep.per_detector.items():
        if c.total:
            ci = metrics_with_ci(c)
            t, f = ci["true_positive_rate"], ci["false_positive_rate"]
            print(f"  {name:22s} TPR {t.rate:.0%} [{t.lo:.0%}-{t.hi:.0%}]  "
                  f"FPR {f.rate:.0%} [{f.lo:.0%}-{f.hi:.0%}]  (n={c.total})")


def main() -> int:
    if not os.path.exists(os.path.join(DATA, "sdahu", FAMILIES[0]["scenarios"][0][0])):
        print("Data not found. Run:  python examples/lbnl_fdd/fetch.py")
        return 1

    pooled = []
    for fam in FAMILIES:
        recs = score_family(fam)
        if recs:
            print_scores(fam["label"], recs)
            pooled.extend(recs)

    families_present = sum(1 for fam in FAMILIES
                           if os.path.exists(os.path.join(DATA, fam["dir"],
                                                          fam["scenarios"][0][0])))
    if families_present > 1:
        print("\n" + "=" * 60)
        print_scores(f"POOLED across {families_present} equipment families", pooled)
        print("\nThe same role-based detectors run unchanged across single-duct AHUs,")
        print("fan-coil units, and dual-duct AHUs -- only the point->role mapping and the")
        print("unit's design-min OA differ. OA-fraction transfers cleanly to single-duct")
        print("AHUs and FCUs; on dual-duct AHUs it degrades (the hot/cold-deck mixing and")
        print("mild-weather OAF noise blur the signal) -- a transferability gap the")
        print("cross-equipment benchmark measures rather than hides.")
    elif families_present == 1:
        print("\n(Only SDAHU present. Run `python examples/lbnl_fdd/fetch.py --families`")
        print(" to download FCU + DDAHU and score the full cross-equipment benchmark.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
