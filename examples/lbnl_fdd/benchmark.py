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

Run fetch.py (with --families for FCU/DDAHU) first. The point -> role mappings, the ingest spec
(the ``fix`` quirks for published-data problems, the pinned timestamp format and CAMBER's column
transforms) and each unit's design parameters (the run templates' rule params) are the ones the
dataset catalog ships (``camber/datasets/``): one source of truth for this benchmark and
``camber datasets ingest``, read here exactly as the ingester reads it.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.resources import files

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


def packaged_mapping(name):
    """A mapping JSON shipped with the dataset catalog (``camber/datasets/mappings/<name>``)."""
    text = files("camber.datasets").joinpath("mappings").joinpath(name).read_text("utf-8")
    return json.loads(text)


def catalog_quirks(dataset_id):
    """The ingest quirks the catalog declares for ``dataset_id`` (applied before mapping)."""
    from camber.datasets import get

    return get(dataset_id).ingest.get("quirks", [])


def catalog_spec(dataset_id):
    """The catalog's whole ingest spec for ``dataset_id`` (quirks, timestamp format, transforms)."""
    from camber.datasets import get

    return get(dataset_id).ingest


def template_params(dataset_id, rule):
    """The params the dataset's run template gives ``rule`` (the unit's design parameters)."""
    from camber.datasets import get

    name = get(dataset_id).suggested_analyses["config_template"]
    cfg = json.loads(files("camber.datasets").joinpath("configs").joinpath(name).read_text("utf-8"))
    for r in cfg.get("rules", []):
        if isinstance(r, dict) and r.get("name") == rule:
            return dict(r.get("params") or {})
    return {}


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "lbnl")
# The opt-in FPU / chiller subsets' metrics as last measured: never gated, but docs/VALIDATION.md
# quotes them and tests/test_validation_doc.py checks those cells against this record.
OPTIN_RECORD = os.path.join(HERE, "optin-measured.json")
#: Metric-key prefixes of the opt-in subsets (FPU parallel + series, chiller plant): never gated.
OPTIN_PREFIXES = ("chiller.", "drift.vav_", "drift.sfpu.")

# Detector names are constant; each targets one fault type. OA-fraction is shared
# across all families; the leak detector only applies to the SDAHU coil-leak case.
TARGETS = {OutdoorAirFraction().name: "damper", LeakingValve().name: "valve_leak"}

# One entry per equipment family. The OA-fraction detector's parameters -- above all the unit's
# *design minimum* OA, a per-equipment sequence parameter, not a fudge factor -- come from the
# dataset's run template (camber/datasets/configs/), where each is documented against the
# publisher's sequence and measured on the fault-free run. `use_leak` adds the coil-leak detector
# where a labeled leak scenario exists, with the template's leaking_valve params (0.98).
FAMILIES = [
    {
        "label": "SDAHU (single-duct AHU)",
        "dir": "sdahu",
        "dataset": "lbnl-sdahu",
        "mapping": "lbnl_sdahu.json",
        "use_leak": True,
        "scenarios": [
            ("AHU_annual.csv", ""),
            ("damper_stuck_010_annual.csv", "damper"),
            ("damper_stuck_025_annual.csv", "damper"),
            ("damper_stuck_075_annual.csv", "damper"),
            ("damper_stuck_100_annual_short.csv", "damper"),
            # the one leak run: the zip's coi_leakage_010/025/040/050 are byte-identical copies of
            # a 10% leak (the valve sits at 0.10 whenever commanded shut), so it is scored once
            # under the label that matches the data -- there is no severity sweep
            ("coi_leakage_010_annual.csv", "valve_leak"),
        ],
    },
    {
        "label": "FCU (fan-coil unit)",
        "dir": "fcu",
        "dataset": "lbnl-fcu",
        "mapping": "lbnl_fcu.json",
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
        "dataset": "lbnl-ddahu",
        "mapping": "lbnl_ddahu.json",
        "use_leak": False,
        "scenarios": [
            ("DualDuct_FaultFree.csv", ""),
            ("DualDuct_DMPRStuck_OA_0.csv", "damper"),
            ("DualDuct_DMPRStuck_OA_100.csv", "damper"),
        ],
    },
]


def load_role_frame(csv, mapping, quirks=None, *, spec=None):
    """Read one LBNL CSV into an hourly role-named frame via the family's mapping.

    ``spec`` is the catalog entry's ingest spec (:func:`catalog_spec`): its ``fix`` quirks, pinned
    timestamp format and column transforms are applied exactly as ``camber datasets ingest``
    applies them (:func:`camber.datasets._ingest.read_raw_run`). ``quirks`` alone (the older form)
    applies just those quirks.
    """
    from camber.datasets._ingest import read_raw_run

    spec = dict(spec) if spec is not None else {"timestamp": "Datetime", "quirks": quirks or []}
    df, _ = read_raw_run(csv, mapping, spec)
    df = df.resample("1h").mean()
    frame = pd.DataFrame({mapping.role_of(c): df[c] for c in df.columns if mapping.role_of(c)})
    return normalize_percent_frame(frame)


def family_detectors(fam):
    """The family's detectors, parameterized from the dataset's run template."""
    detectors = [OutdoorAirFraction(**template_params(fam["dataset"], OutdoorAirFraction.name))]
    if fam["use_leak"]:
        # 0.98 (#84, S1): the leak detector also reads the template, whose lbnl-sdahu entry sets
        # a fan heat measured on the fault-free run -- a run scored below as a negative, so that
        # run's verdict is in-sample (the template comment and docs/VALIDATION.md say so)
        detectors.append(LeakingValve(**template_params(fam["dataset"], LeakingValve.name)))
    return detectors


def score_family(fam):
    """Run the family's detectors over its scenarios; return the records list."""
    mapping = MappingProvider.from_dict(packaged_mapping(fam["mapping"]))
    spec = catalog_spec(fam["dataset"])
    detectors = family_detectors(fam)
    base = os.path.join(DATA, fam["dir"])
    records = []
    oaf = detectors[0]
    seasonal = oaf.min_oa_pct_by_month
    mins = f"{oaf.min_oa_pct:g}%" + (
        f", {sorted(set(seasonal.values()))[0]:g}% in months {sorted(seasonal)}" if seasonal else ""
    )
    print(f"\n=== {fam['label']}  (min OA {mins}) ===")
    print(f"{'scenario':32s} {'truth':11s} fired")
    for fname, truth in fam["scenarios"]:
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            continue
        frame = load_role_frame(path, mapping, spec=spec)
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
# on real labeled faults: economizer-damper drift (target = stuck damper) gets a real recall;
# coil-valve drift's targets (fouling / starvation) aren't in the set, so it is specificity-only.
# Duct-static-control drift has no labeled fault in the set and declines every case, so it
# contributes no number at all (declines are excluded, never scored as negatives).
# Fan-efficiency and filter drift need POWER / FILTER_DIFF_PRESS points the SDAHU sim does not
# export -> synthetic-only (camber.faultlab). The multi-zone rogue/cohort census and the
# reset-request detectors are not validatable on a single simulated AHU at all. See
# docs/VALIDATION.md for the full matrix.
#
# A drift detector freezes its baseline the first time it sees a period, so each case pairs the
# fault-free run's first 60% (baseline) with a *current* window: the fault-free tail (a genuine
# negative) or a faulted run (a positive). A run targeting a *different* fault is a cross-negative.
# --------------------------------------------------------------------------- #

# Positives are the faults a detector's *documented physics* says it sees -- chosen from the rule's
# docstring, not from its results. The coil-valve, reheat-valve and VAV-airflow drift detectors are
# all ONE-SIDED UP: they flag the controller having to open a valve/damper *further* for the same
# duty (fouling, waterside starvation, authority loss, a device stuck closed). A leaking or stuck-
# open valve does the opposite -- it delivers capacity the controller didn't ask for, so the demand
# *falls* -- and a high-reading airflow sensor makes the damper close. Those faults are **cross-
# negatives** here: the detector is right to stay silent, and firing on one (a leak misread as
# fouling) counts as a false positive. Until 0.82.0 they were listed as positives, which scored a
# detector against a direction it explicitly does not claim.
DRIFT_DETECTORS = {
    "coil_valve_drift": {
        "build": lambda: CoilValveDrift(BaselineStore(), site="lbnl_sdahu", run_id="bench"),
        # its targets (coil fouling / starvation) are not in the SDAHU set -> specificity only;
        # the coil-valve *leak* is an opposite-direction fault (leaking_valve's job)
        "positive": None,
        "cross_negative": ("damper_stuck", "coi_leakage"),
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

# VAV zone-terminal drift on the LBNL Fan-Power-Unit subset (the South-zone box is the one faulted),
# scored per box family: parallel (PFPU, keys ``drift.vav_*``) and series (SFPU, keys
# ``drift.sfpu.vav_*``, since 0.92.0), each against its own fault-free run. The target lists are
# written from each rule's documented physics (revised in 0.92.0, #12) and are the same for both
# families; a run on no list is excluded (neither a target nor a negative).
#
# vav_airflow_drift (DAMPER ~ AIRFLOW_SP, one-sided UP) targets a damper that has to open further
# for the same commanded flow: stuck at 50 / 80 / 100 % (it sits above where the controller wants
# it) and a LOW-reading airflow sensor (-200 / -400 cfm: the controller opens to make up the
# phantom shortfall). Stuck at 0 / 20 % pulls the damper DOWN, outside the one-sided claim, so
# those are excluded rather than scored either way. Cross-negatives (the relation holds, so the
# detector must stay quiet): a high-reading sensor (+200 / +400 cfm), damper instability, fan
# restriction, the room-temperature faults (they move the command along the same curve) and every
# reheat-valve / coil fault.
#
# vav_reheat_valve_drift (reheat-valve demand at matched duty, one-sided UP) targets a valve that
# delivers less than asked: stuck closed or nearly (0 / 20 %) and coil fouling (air- or waterside).
# Cross-negatives: a valve stuck at 50 / 80 / 100 % or leaking (it over-delivers, demand FALLS),
# the stuck dampers, and the airflow and room-temperature sensor biases (duty moves, the valve ~
# duty relation does not). Excluded: the room-temperature and damper instability runs and fan
# restriction, whose effect on the coil's entering air the rule's physics predicts neither way.
FPU_AIRFLOW_LISTS = {
    "positive": (
        "VAVDMPRStuck_50%",
        "VAVDMPRStuck_80%",
        "VAVDMPRStuck_100%",
        "SensorBias_VAVAirflow_-200CFM",
        "SensorBias_VAVAirflow_-400CFM",
    ),
    "cross_negative": (
        "SensorBias_VAVAirflow_+",
        "VAVDMPRUnstable",
        "VAVFanRestrictFlow",
        "SensorBias_RMTEMP",
        "RMTEMPUnstable",
        "Reheat",
    ),
}
FPU_REHEAT_LISTS = {
    "positive": ("ReheatVLVStuck_0%", "ReheatVLVStuck_20%", "ReheatCoilFouling"),
    "cross_negative": (
        "ReheatVLVStuck_50%",
        "ReheatVLVStuck_80%",
        "ReheatVLVStuck_100%",
        "ReheatVLVLeak",
        "VAVDMPRStuck",
        "SensorBias_VAVAirflow",
        "SensorBias_RMTEMP",
    ),
}


def fpu_drift_detectors(family="PFPU"):
    """The VAV drift detector specs for one FPU family (``"PFPU"`` or ``"SFPU"``)."""
    site = "lbnl_fpu" if family == "PFPU" else f"lbnl_{family.lower()}"  # PFPU: the pre-0.92 name

    def lists(spec):
        return {k: tuple(f"{family}_{p}" for p in v) for k, v in spec.items()}

    return {
        "vav_airflow_drift": {
            "build": lambda: VavAirflowDrift(BaselineStore(), site=site, run_id="bench"),
            "equip": site,
            **lists(FPU_AIRFLOW_LISTS),
        },
        "vav_reheat_valve_drift": {
            "build": lambda: VavReheatValveDrift(BaselineStore(), site=site, run_id="bench"),
            "equip": site,
            **lists(FPU_REHEAT_LISTS),
        },
    }


FPU_DRIFT_DETECTORS = fpu_drift_detectors("PFPU")

# In a SERIES box the fan runs whenever the box is occupied and pulls primary AND plenum air through
# the reheat coil, so the coil's duty is carried by the fan discharge flow (VAV_DA_CFM_S), not the
# primary flow the damper controls (VAV_PM_CFM_S). The SFPU reheat score reads this mapping (the
# packaged lbnl_fpu.json with airflow -> the discharge flow); the SFPU airflow score, like PFPU,
# reads the packaged mapping (damper vs the PRIMARY-flow command). The coil's entering-air
# temperature is still the primary SA_TEMP proxy -- in a series box a primary/plenum mix -- which
# is why the room-temperature-bias runs can read as reheat creep there.
SFPU_REHEAT_FLOW = "VAV_DA_CFM_S"


def sfpu_reheat_mapping():
    """The packaged FPU mapping with the reheat duty's airflow read from the fan discharge."""
    m = packaged_mapping("lbnl_fpu.json")
    aliases = {k: v for k, v in m["aliases"].items() if v != "airflow"}
    aliases[SFPU_REHEAT_FLOW] = "airflow"
    return MappingProvider.from_dict({"aliases": aliases})


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


def score_drift(
    frames,
    detectors=None,
    *,
    fault_free="AHU_annual.csv",
    label="AHU air-side drift",
    counts=False,
    key_prefix="drift.",
):
    """Score a set of drift detectors on ``frames``; return the flat metrics dict.

    ``detectors`` defaults to the SDAHU air-side set; pass a subset-specific dict (e.g. the FPU VAV
    set) with its own ``fault_free`` baseline file to score another equipment subset with the same
    machinery. ``counts`` adds the confusion counts (``tp``, ``fn``, ``fp``, ``tn``, ``declined``),
    which docs/VALIDATION.md quotes for the opt-in subsets (the gated SDAHU keys are unchanged).
    A detector spec may carry its own ``frames`` (a subset read through a different mapping), and
    ``key_prefix`` names the metrics (``drift.`` by default; the series FPU boxes use
    ``drift.sfpu.``).
    """
    detectors = detectors if detectors is not None else DRIFT_DETECTORS
    metrics = {}
    print(f"\n=== {label} (camber.driftvalidation) ===")
    for name, det in detectors.items():
        cases = build_drift_cases(det.get("frames", frames), det, fault_free=fault_free)
        pos = sum(1 for c in cases if c.fault)
        if len(cases) < 2 or (det["positive"] and pos == 0):
            print(f"  {name:24s} skipped (no usable cases)")
            continue
        score = evaluate(det["build"], cases)
        c = score.confusion
        # no negative case evaluated -> no specificity measured (NaN, omitted), never a false 0.0
        fpr = round(c.fp / (c.fp + c.tn), 4) if (c.fp + c.tn) else float("nan")
        kind = "specificity" if det["positive"] is None else "TPR"
        print(
            f"  {name:24s} recall {score.recall} precision {score.precision} "
            f"f1 {score.f1} fpr {fpr}  (n={score.n} scored, {score.n_declined} declined, {kind})"
        )
        # omit NaN metrics (a specificity-only detector has no recall/precision) -> valid JSON
        for key, val in (
            ("recall", score.recall),
            ("precision", score.precision),
            ("f1", score.f1),
            ("fpr", fpr),
        ):
            if val == val:  # not NaN
                metrics[f"{key_prefix}{name}.{key}"] = round(val, 4)
        if counts:
            for key, val in (
                ("tp", c.tp),
                ("fn", c.fn),
                ("fp", c.fp),
                ("tn", c.tn),
                ("declined", score.n_declined),
            ):
                metrics[f"{key_prefix}{name}.{key}"] = int(val)
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
# The plant's three sensor-bias families (#11): a chiller-1 leaving-water sensor (+-1 / +-2 C), a
# tower-1 leaving-water sensor (+-1 / +-2 C) and the secondary-loop differential-pressure sensor
# (+-10 / +-20 %). For a *physical* plant-level detector each is a genuine NEGATIVE: the plant runs
# healthy and only a sensor lies, so firing on one is a false alarm (a biased temperature corrupts
# the tonnage, and the detector cannot tell). Listed explicitly (0.92.0; until then every run that
# was not a positive counted as a negative by default) so a run matching no list is *excluded* and
# printed, never silently scored.
CHILLER_SENSOR_BIAS = (
    "ChillerPlant_chiller_bias",
    "ChillerPlant_coolingtower_bias",
    "ChillerPlant_secondary_chilled_water_pressure_bias",
)
CHILLER_DETECTORS = {
    "cooling_tower_approach": {
        # tower fouling (fouled fill -> can't approach wet-bulb) + condenser-loop PID mistuning
        "make": lambda design=7.0: CoolingTowerApproach(design_approach_f=design),
        "metric": "approach_median_f",
        "positive": ("ChillerPlant_coolingtower_fouling", "ChillerPlant_coolingtower_PI"),
        # a fouled chiller and a bypassed condenser loop leave the tower's own approach alone
        # (cross-negatives); the sensor biases are negatives for every physical detector
        "negative": (
            "ChillerPlant_chiller_fouling",
            "ChillerPlant_bypass_leakage",
            "ChillerPlant_bypass_stuck",
            *CHILLER_SENSOR_BIAS,
        ),
    },
    "chiller_efficiency": {
        # anything that raises chiller lift -> kW/ton: warmer condenser water from a fouled tower,
        # a bypassed three-way valve (leak/stuck), or condenser-loop PID mistuning -- and a fouled
        # chiller itself. The chiller-fouling runs are UNDOCUMENTED: they are in the archive but
        # not in the inventory's Tables 3-4 (see the lbnl-chiller catalog entry's data issues);
        # only chiller 1's power changes (+61% / +6% a year at 065 / 095). Until 0.86.0 they were
        # scored as negatives, so a detection on 065 counted as a false positive.
        "make": lambda design=0.85: ChillerEfficiency(design_kw_per_ton=design),
        "metric": "kw_per_ton_median",
        "positive": (
            "ChillerPlant_coolingtower_fouling",
            "ChillerPlant_coolingtower_PI",
            "ChillerPlant_bypass_leakage",
            "ChillerPlant_bypass_stuck",
            "ChillerPlant_chiller_fouling",  # undocumented in the inventory
        ),
        "negative": CHILLER_SENSOR_BIAS,
    },
}

# Sensor-vs-physical (#11): the two reference pairs that separate a biased sensor from a real
# fault on this plant, scored with camber.sensordrift.compare_to_reference at its documented
# temperature defaults (warn at |bias| >= 2.0 F). Each pair holds only while its physics does:
#   * chiller 1's leaving water (CHL_SW_TEMP_1) IS the primary loop's supply (CWL_PRI_SW_TEMP)
#     when chiller 1 runs alone (chillers 2 and 3 under 1 kW) -- nothing mixes in between;
#   * tower 1's leaving water (CT_SW_TEMP_1) IS the condenser loop's supply (CDWL_SW_TEMP) when
#     tower 1's fan runs alone and the bypass valve is commanded shut (TWV_CTRL <= 0.01).
# Positives are the runs biasing that pair's sensor; every other run is a negative. The bypass
# condition reads the valve COMMAND, which a leaking or stuck valve does not obey, so on those runs
# the tower pair compares tower water with bypass-mixed water: counted as the false alarms they are.
CHILLER_SENSOR_PAIRS = {
    "chiller_leaving_water": {
        "sensor": "CHL_SW_TEMP_1",
        "reference": "CWL_PRI_SW_TEMP",
        "positive": ("ChillerPlant_chiller_bias",),
    },
    "tower_leaving_water": {
        "sensor": "CT_SW_TEMP_1",
        "reference": "CDWL_SW_TEMP",
        "positive": ("ChillerPlant_coolingtower_bias",),
    },
}
CHILLER_SENSOR_COLUMNS = (
    "CHL_SW_TEMP_1",
    "CWL_PRI_SW_TEMP",
    "CT_SW_TEMP_1",
    "CDWL_SW_TEMP",
    "TWV_CTRL",
    "CHL_POW_1",
    "CHL_POW_2",
    "CHL_POW_3",
    "CT_FAN_SPD_1",
    "CT_FAN_SPD_2",
    "CT_FAN_SPD_3",
)


def _run_class(fname, det):
    """``"positive"`` / ``"negative"`` / ``None`` (excluded) for one chiller run and detector."""
    if any(fname.startswith(p) for p in det["positive"]):
        return "positive"
    if fname == CHILLER_FAULT_FREE or any(fname.startswith(p) for p in det.get("negative", ())):
        return "negative"
    return None


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
    should fire); the fault-free run and the ``negative`` runs (sensor biases, cross-faults) should
    stay quiet. Pure
    and deterministic given the frames, so it's unit-testable on synthetic plant-shaped frames.
    A run on neither the ``positive`` nor the ``negative`` list is excluded (and printed).
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
        tp = fn = fp = tn = declined = 0
        excluded = []
        for fname, frame in sorted(frames.items()):
            kind = _run_class(fname, det)
            if kind is None:
                excluded.append(fname)  # on no list: neither a target nor a negative
                continue
            finding = rule.analyze("CH1", frame)
            if (finding.metrics or {}).get("declined"):
                declined += 1  # could not test its claim: neither a detection nor a negative
                continue
            fired = finding.severity in ("warn", "fault")
            if kind == "positive":
                tp, fn = tp + fired, fn + (not fired)
            else:
                fp, tn = fp + fired, tn + (not fired)
        if excluded:
            print(f"  {name:24s} excluded (on no list): {', '.join(excluded)}")
        tpr = tp / (tp + fn) if (tp + fn) else float("nan")
        fpr = fp / (fp + tn) if (fp + tn) else float("nan")
        print(
            f"  {name:24s} design~{healthy:.2f}  TPR {tpr:.0%} (n={tp + fn})  "
            f"FPR {fpr:.0%} (n={fp + tn})  {declined} declined"
        )
        for key, val in (("tpr", tpr), ("fpr", fpr)):
            if val == val:  # omit NaN -> valid JSON
                metrics[f"chiller.{name}.{key}"] = round(val, 4)
        # the confusion counts docs/VALIDATION.md quotes (opt-in subset: never gated)
        for key, n in (("tp", tp), ("fn", fn), ("fp", fp), ("tn", tn), ("declined", declined)):
            metrics[f"chiller.{name}.{key}"] = int(n)
    return metrics


def load_sensor_frame(csv, spec, columns=CHILLER_SENSOR_COLUMNS):
    """The raw (unmapped) chiller-plant columns the sensor-reference pairs read, hourly means.

    Read through the catalog's ingest path (:func:`camber.datasets._ingest.read_raw_run`, so the
    entry's ``fix`` quirks and timestamp handling apply); the placeholder role only tells the reader
    which columns to keep.
    """
    from camber.datasets._ingest import read_raw_run

    keep = MappingProvider.from_dict({"aliases": {c: "power" for c in columns}})
    df, _ = read_raw_run(csv, keep, dict(spec))
    return (
        df[[c for c in columns if c in df.columns]]
        .apply(pd.to_numeric, errors="coerce")
        .resample("1h")
        .mean()
    )


def _pair_windows(df):
    """``{pair: boolean hours}`` where each reference pair's physics holds (see the pairs)."""
    alone_ch1 = (df["CHL_POW_1"] > 1) & (df["CHL_POW_2"] < 1) & (df["CHL_POW_3"] < 1)
    alone_ct1 = (
        (df["CT_FAN_SPD_1"] > 0.01)
        & (df["CT_FAN_SPD_2"] < 0.01)
        & (df["CT_FAN_SPD_3"] < 0.01)
        & (df["TWV_CTRL"] <= 0.01)
    )
    return {"chiller_leaving_water": alone_ch1, "tower_leaving_water": alone_ct1}


def score_chiller_sensors(raw_frames, *, label="Chiller-plant sensor vs reference"):
    """Score the sensor-reference pairs on ``{scenario_csv: raw sensor frame}``; return metrics.

    Each pair runs :func:`camber.sensordrift.compare_to_reference` on the hours its physics holds;
    a ``warn``/``fault`` verdict is a detection. Positives are the pair's own sensor-bias runs,
    every other run (fault-free, physical faults, the other sensors' biases) is a negative, and a
    pair with too few valid hours (``info``) is declined. Keys ``chiller.sensor.<pair>.*`` (opt-in,
    never gated).
    """
    from camber.sensordrift import compare_to_reference

    metrics = {}
    print(f"\n=== {label} (camber.sensordrift, default 2.0 F bias threshold) ===")
    for pair, spec in CHILLER_SENSOR_PAIRS.items():
        tp = fn = fp = tn = declined = 0
        for fname, df in sorted(raw_frames.items()):
            if not {spec["sensor"], spec["reference"]} <= set(df.columns):
                declined += 1
                continue
            hours = _pair_windows(df)[pair]
            res = compare_to_reference(
                df.loc[hours, spec["sensor"]], df.loc[hours, spec["reference"]], name=pair
            )
            if res.severity == "info":
                declined += 1
                continue
            fired = res.severity in ("warn", "fault")
            if any(fname.startswith(p) for p in spec["positive"]):
                tp, fn = tp + fired, fn + (not fired)
            else:
                fp, tn = fp + fired, tn + (not fired)
            print(f"  {pair:22s} {fname:60s} bias {res.bias:+6.2f} F  {res.severity}")
        tpr = tp / (tp + fn) if (tp + fn) else float("nan")
        fpr = fp / (fp + tn) if (fp + tn) else float("nan")
        print(f"  {pair:22s} TPR {tp}/{tp + fn}  FPR {fp}/{fp + tn}  {declined} declined")
        for key, val in (("tpr", tpr), ("fpr", fpr)):
            if val == val:
                metrics[f"chiller.sensor.{pair}.{key}"] = round(val, 4)
        for key, n in (("tp", tp), ("fn", fn), ("fp", fp), ("tn", tn), ("declined", declined)):
            metrics[f"chiller.sensor.{pair}.{key}"] = int(n)
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
    sd_mapping = MappingProvider.from_dict(packaged_mapping(sdahu["mapping"]))
    sd_base = os.path.join(DATA, sdahu["dir"])
    sd_spec = catalog_spec(sdahu["dataset"])
    sd_frames = {
        fname: load_role_frame(os.path.join(sd_base, fname), sd_mapping, spec=sd_spec)
        for fname, _ in sdahu["scenarios"]
        if os.path.exists(os.path.join(sd_base, fname))
    }
    if sd_frames:
        metrics.update(score_drift(sd_frames, label="AHU air-side drift (SDAHU)"))

    # VAV zone-terminal drift on the Fan-Power-Unit subset (opt-in via fetch.py --fpu)
    fpu_base = os.path.join(DATA, "fpu")
    if os.path.exists(os.path.join(fpu_base, "PFPU_FaultFree.csv")):
        fpu_mapping = MappingProvider.from_dict(packaged_mapping("lbnl_fpu.json"))
        fpu_spec = catalog_spec("lbnl-fpu")
        fpu_frames = {
            f: load_role_frame(os.path.join(fpu_base, f), fpu_mapping, spec=fpu_spec)
            for f in os.listdir(fpu_base)
            if f.endswith(".csv")
        }
        metrics.update(
            score_drift(
                {f: v for f, v in fpu_frames.items() if f.startswith("PFPU_")},
                FPU_DRIFT_DETECTORS,
                fault_free="PFPU_FaultFree.csv",
                label="VAV zone-terminal drift (parallel FPU)",
                counts=True,
            )
        )
        if os.path.exists(os.path.join(fpu_base, "SFPU_FaultFree.csv")):
            series = sfpu_reheat_mapping()
            sfpu = fpu_drift_detectors("SFPU")
            sfpu["vav_reheat_valve_drift"]["frames"] = {
                f: load_role_frame(os.path.join(fpu_base, f), series, spec=fpu_spec)
                for f in fpu_frames
                if f.startswith("SFPU_")
            }
            metrics.update(
                score_drift(
                    {f: v for f, v in fpu_frames.items() if f.startswith("SFPU_")},
                    sfpu,
                    fault_free="SFPU_FaultFree.csv",
                    label="VAV zone-terminal drift (series FPU)",
                    counts=True,
                    key_prefix="drift.sfpu.",
                )
            )

    # Chiller-plant plant-level detectors (opt-in via fetch.py --chiller)
    chiller_base = os.path.join(DATA, "chiller")
    if os.path.exists(os.path.join(chiller_base, CHILLER_FAULT_FREE)):
        chiller_mapping = MappingProvider.from_dict(packaged_mapping("lbnl_chiller.json"))
        chiller_spec = catalog_spec("lbnl-chiller")
        chiller_frames = {
            f: load_role_frame(os.path.join(chiller_base, f), chiller_mapping, spec=chiller_spec)
            for f in os.listdir(chiller_base)
            if f.endswith(".csv")
        }
        metrics.update(score_chiller(chiller_frames))
        sensor_frames = {
            f: load_sensor_frame(os.path.join(chiller_base, f), chiller_spec)
            for f in os.listdir(chiller_base)
            if f.endswith(".csv")
        }
        metrics.update(score_chiller_sensors(sensor_frames))

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
        print("fan-coil units, and dual-duct AHUs -- only the point->role mapping and each")
        print("unit's own design minimum OA (from its sequence, measured on its fault-free")
        print("run) differ; every family is judged on fan-on, occupied samples.")
    elif families_present == 1:
        print("\n(Only SDAHU present. Run `python examples/lbnl_fdd/fetch.py --families`")
        print(" to download FCU + DDAHU and score the full cross-equipment benchmark.)")

    optin = {k: v for k, v in metrics.items() if k.startswith(OPTIN_PREFIXES)}
    if optin and os.path.exists(OPTIN_RECORD):
        recorded = json.load(open(OPTIN_RECORD))
        moved = sorted(k for k in set(optin) | set(recorded) if optin.get(k) != recorded.get(k))
        if moved:
            print(f"\nopt-in metrics differ from {os.path.basename(OPTIN_RECORD)} (not gated):")
            for k in moved:
                print(f"  {k}: recorded {recorded.get(k)} -> measured {optin.get(k)}")
            print("  regenerate the record and the docs/VALIDATION.md cells that quote it")
        else:
            print(f"\nopt-in metrics match {os.path.basename(OPTIN_RECORD)}")
    if args.json:
        json.dump(metrics, open(args.json, "w"), indent=2, sort_keys=True)
        print(f"\nwrote metrics -> {args.json}")
    if args.update_baseline:
        json.dump(metrics, open(args.update_baseline, "w"), indent=2, sort_keys=True)
        print(f"wrote baseline -> {args.update_baseline}")
    if args.gate:
        from camber.eval import baseline_report, check_against_baseline

        baseline = json.load(open(args.gate))
        # The committed baseline covers what CI fetches (--families). The opt-in FPU / chiller
        # subsets (~7 GB extracted) are too large for the CI cache, so their metrics are never
        # baselined: with them present locally, report them as ungated rather than fail.
        opt_in = [d for d in ("fpu", "chiller") if os.path.isdir(os.path.join(DATA, d))]
        chk = check_against_baseline(metrics, baseline, tol=args.tol, strict_new=not opt_in)
        print(baseline_report(chk, label="LBNL benchmark", tol=args.tol))
        if not chk.passed:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
