"""Validate the opt-in coil-valve leak drift detector (``coil_leak_drift``, 0.100, #100) on the LBNL
air-side leak runs, against each unit's own fault-free behaviour.

Not part of the gated benchmark (``benchmark.py``): this script prints TPR / FPR with 95 % Wilson
intervals and, with ``--json``, the metrics it would propose as gated keys. Nothing here is a
baseline; the maintainer decides whether any of it is gated.

Two LBNL units carry labelled coil-valve leaks:

* **SDAHU** (single-duct AHU, cooling coil only): one 10 % cooling-valve leak (``coi_leakage_010``;
  the 025/040/050 files are byte-identical copies, see the catalog's data issue).
* **FCU** (four-pipe fan-coil unit): cooling- and heating-valve leaks at 20 / 50 / 80 % of max
  flow (``FCU_VLVLeak_*``). They are in the full archive only (``fetch.py`` and the default catalog
  subset take the damper runs): extract them next to ``FCU_FaultFree.csv``.

The dual-duct AHU (DDAHU) archive has no leak run, so it has nothing to score here.

Each run is scored three ways, all built from the published runs with nothing fitted on the run
being scored:

* ``split`` -- the benchmark's drift design: the baseline is the fault-free run's first 60 %, the
  current window is the fault-free tail (a negative) or a whole faulted run;
* ``twin`` -- a declared **reference equipment** (0.98, S4): the whole fault-free run is the
  baseline and each other run is scored over its whole year; the fault-free run is the yardstick
  and is not scored;
* ``onset`` -- a declared **known-good period**: each run is spliced onto the fault-free run at
  ``ONSET`` (healthy before, the run after), the baseline is the healthy half and the current window
  the other half. The fault-free run spliced onto itself is the negative that measures the seasonal
  false-alarm rate.

Runs are put in four groups, from each detector's documented physics (not from its results):
``positive`` (a leak), ``negative`` (no uncommanded coil flow: the healthy run, damper, fouling,
filter, fan and room-sensor faults, valves stuck **closed**), ``same_symptom`` (a valve stuck
partly or fully **open**, or a reversed valve signal: water passes while the valve is commanded
shut, physically the same symptom; reported, not scored) and ``confound`` (a supply-air sensor
bias, which moves the supply-minus-mixed rise exactly as a leak does; reported, not scored).

    python examples/lbnl_fdd/leak_drift.py --sdahu DIR --fcu DIR [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

from camber.eval import confusion  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.coil_leak_rule import CoilLeakDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402
from camber.validation import metrics_with_ci  # noqa: E402

ONSET = "2018-07-01"  # the catalog's spliced fault-onset run uses the same date
SPLIT_FRAC = 0.6  # the benchmark's drift baseline fraction

#: Per-unit run groups, by file-name prefix. A run on no list is excluded and printed.
UNITS = {
    "sdahu": {
        "dataset": "lbnl-sdahu",
        "mapping": "lbnl_sdahu.json",
        "fault_free": "AHU_annual.csv",
        "positive": {"coi_leakage_010": "cooling"},
        "negative": ("damper_stuck_",),
        "same_symptom": ("coi_stuck_",),
        "confound": ("coi_bias_",),
    },
    "fcu": {
        "dataset": "lbnl-fcu",
        "mapping": "lbnl_fcu.json",
        "fault_free": "FCU_FaultFree.csv",
        "positive": {"FCU_VLVLeak_Cooling_": "cooling", "FCU_VLVLeak_Heating_": "heating"},
        "negative": (
            "FCU_OADMPR",
            "FCU_OABlockage",
            "FCU_Fouling_",
            "FCU_FilterRestriction_",
            "FCU_FanOutletBlockage",
            "FCU_SensorBias_RMTemp_",
            "FCU_Control_Unstable",
            "FCU_VLVStuck_Cooling_0.",
            "FCU_VLVStuck_Heating_0.",
        ),
        "same_symptom": (
            "FCU_VLVStuck_",
            "FCU_Control_CoolingReverse",
            "FCU_Control_HeatingReverse",
        ),
        "confound": (),
    },
}
GROUPS = ("positive", "negative", "same_symptom", "confound")


def group_of(unit: dict, name: str):
    """``(group, coil)`` for a run file ``name`` (``coil`` is the leaking coil of a positive)."""
    if name == unit["fault_free"]:
        return "negative", None
    for prefix, coil in unit["positive"].items():
        if name.startswith(prefix):
            return "positive", coil
    for group in ("negative", "same_symptom", "confound"):  # negatives first: stuck-closed valves
        if any(name.startswith(p) for p in unit[group]):
            return group, None
    return None, None


def coils_of(frame: pd.DataFrame) -> tuple:
    """The coils a unit can be judged on: cooling always, heating when a heating valve is mapped."""
    return ("cooling", "heating") if Role.HEAT_VALVE in frame.columns else ("cooling",)


def splice(healthy: pd.DataFrame, faulted: pd.DataFrame, onset: str = ONSET) -> pd.DataFrame:
    """The healthy run before ``onset`` and the faulted run from it on (one unit, one index)."""
    t = pd.Timestamp(onset)
    return pd.concat([healthy[healthy.index < t], faulted[faulted.index >= t]]).sort_index()


def cases(frames: dict, unit: dict, mode: str) -> list:
    """``[(name, group, coil, baseline_frame, current_frame)]`` for one unit and scoring mode."""
    ff = frames.get(unit["fault_free"])
    if ff is None:
        return []
    out = []
    for name, frame in sorted(frames.items()):
        group, coil = group_of(unit, name)
        if group is None:
            continue
        if mode == "split":
            cut = int(len(ff) * SPLIT_FRAC)
            base = ff.iloc[:cut]
            cur = ff.iloc[cut:] if name == unit["fault_free"] else frame
        elif mode == "twin":
            if name == unit["fault_free"]:
                continue  # the declared reference is the yardstick, not a scored unit
            base, cur = ff, frame
        elif mode == "onset":
            t = pd.Timestamp(ONSET)
            spliced = splice(ff, frame)
            base, cur = spliced[spliced.index < t], spliced[spliced.index >= t]
        else:
            raise ValueError(f"unknown mode {mode!r}")
        out.append((name, group, coil, base, cur))
    return out


def judge(base: pd.DataFrame, cur: pd.DataFrame, **params) -> dict:
    """Run one fresh detector per coil; ``{coil: Finding}``."""
    out = {}
    for coil in coils_of(cur):
        rule = CoilLeakDrift(BaselineStore(), site="lbnl", run_id="leak", coil=coil, **params)
        out[coil] = rule.analyze_periods("UNIT", base, cur)
    return out


def _fired(f) -> bool:
    return f.severity in ("warn", "fault")


def _declined(f) -> bool:
    return bool((f.metrics or {}).get("declined"))


def score_unit(frames: dict, unit: dict, mode: str, **params) -> dict:
    """Score one unit in one mode. Returns per-run rows and the confusion over the scored groups."""
    rows = []
    labels, preds = [], []
    for name, group, coil, base, cur in cases(frames, unit, mode):
        fs = judge(base, cur, **params)
        declined = all(_declined(f) for f in fs.values())
        fired = sorted(c for c, f in fs.items() if _fired(f))
        rows.append(
            {
                "run": name,
                "group": group,
                "coil": coil,
                "declined": declined,
                "fired": fired,
                "right_coil": (coil in fired) if coil else None,
                "drift": {
                    c: (f.metrics or {}).get("coil_leak_drift_f")
                    for c, f in fs.items()
                    if not _declined(f)
                },
                "sigma": {
                    c: (f.metrics or {}).get("coil_leak_drift_sigma")
                    for c, f in fs.items()
                    if not _declined(f)
                },
                # hours judged, and valve-shut hours outside the baseline's mixed-air range
                "n": {c: (f.metrics or {}).get("coil_leak_n_current") for c, f in fs.items()},
                "n_out": {
                    c: (f.metrics or {}).get("coil_leak_n_out_of_scope") for c, f in fs.items()
                },
            }
        )
        if declined or group not in ("positive", "negative"):
            continue
        labels.append(group == "positive")
        preds.append(bool(fired))
    return {"rows": rows, "confusion": confusion(labels, preds)}


def _rate(ci) -> str:
    if ci.n == 0:
        return "n/a"
    return f"{round(ci.rate * ci.n)}/{ci.n} = {ci.rate:.0%} [{ci.lo:.0%}-{ci.hi:.0%}]"


def report(results: dict) -> dict:
    """Print the per-run table and the scores; return the proposed (ungated) metric keys."""
    keys = {}
    for unit_name, by_mode in results.items():
        for mode, res in by_mode.items():
            c = res["confusion"]
            ci = metrics_with_ci(c)
            print(f"\n=== {unit_name} / {mode} ===")
            for r in res["rows"]:
                drift = ", ".join(
                    f"{k} {v:+.2f}F ({r['sigma'][k]:+.1f}s, n={r['n'][k]}, out={r['n_out'][k]})"
                    for k, v in r["drift"].items()
                    if v is not None
                )
                verdict = (
                    "declined"
                    if r["declined"]
                    else ("FIRED " + "+".join(r["fired"]) if r["fired"] else "quiet")
                )
                print(f"  {r['run']:42s} {r['group']:12s} {verdict:22s} {drift}")
            print(
                f"  TPR {_rate(ci['true_positive_rate'])}   FPR {_rate(ci['false_positive_rate'])}"
            )
            pos_right = [r for r in res["rows"] if r["group"] == "positive" and not r["declined"]]
            if pos_right:
                ok = sum(1 for r in pos_right if r["right_coil"])
                print(f"  right coil named on {ok}/{len(pos_right)} scored leaks")
            for g in ("same_symptom", "confound"):
                rs = [r for r in res["rows"] if r["group"] == g and not r["declined"]]
                if rs:
                    print(
                        f"  {g}: fired on {sum(1 for r in rs if r['fired'])}/{len(rs)} (not scored)"
                    )
            pre = f"leak_drift.{unit_name}.{mode}"
            for k, v in (
                ("tp", c.tp),
                ("fn", c.fn),
                ("fp", c.fp),
                ("tn", c.tn),
                (
                    "declined",
                    sum(
                        1
                        for r in res["rows"]
                        if r["declined"] and r["group"] in ("positive", "negative")
                    ),
                ),
            ):
                keys[f"{pre}.{k}"] = int(v)
            if c.tp + c.fn:
                keys[f"{pre}.tpr"] = round(c.true_positive_rate, 4)
            if c.fp + c.tn:
                keys[f"{pre}.fpr"] = round(c.false_positive_rate, 4)
    return keys


def load_unit(directory: str, unit: dict) -> dict:
    """Read every CSV of ``directory`` that belongs to a group, as an hourly role frame."""
    import benchmark as B  # the benchmark's reader: the catalog's mapping, quirks and transforms

    from camber.model.mapping import MappingProvider

    mapping = MappingProvider.from_dict(B.packaged_mapping(unit["mapping"]))
    spec = B.catalog_spec(unit["dataset"])
    frames = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".csv") or group_of(unit, name)[0] is None:
            continue
        frames[name] = B.load_role_frame(os.path.join(directory, name), mapping, spec=spec)
    return frames


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sdahu", help="folder of SDAHU run CSVs (AHU_annual.csv, coi_*, damper_*)")
    ap.add_argument("--fcu", help="folder of FCU run CSVs (FCU_FaultFree.csv, FCU_VLVLeak_*, ...)")
    ap.add_argument("--modes", default="split,twin,onset")
    ap.add_argument("--json", help="write the proposed (ungated) metric keys here")
    args = ap.parse_args(argv)
    results = {}
    for unit_name, directory in (("sdahu", args.sdahu), ("fcu", args.fcu)):
        if not directory:
            continue
        frames = load_unit(directory, UNITS[unit_name])
        results[unit_name] = {
            m: score_unit(frames, UNITS[unit_name], m) for m in args.modes.split(",")
        }
    keys = report(results)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(keys, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
