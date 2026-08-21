# Chiller drift detection & threshold calibration

Most FDD asks "is this reading bad **right now**?" Drift detection asks a harder, quieter question:
"is this machine **slowly getting worse** than it used to be?" A chiller can pass every instantaneous
check while its condenser fouls, its charge leaks, or its evaporator feed degrades over months. CAMBER
catches that by comparing the machine **to its own frozen past, at matched load**.

All of these detectors are **period rules** — run them with
[`Registry.run_periods`](API-STABILITY.md) against a baseline period and a current period. Each
freezes a load-normalized baseline into a [`BaselineStore`](SCALE.md) on first use, so the reference
survives between runs instead of drifting along with the fault.

## The load-normalized baseline

A chiller's approach, subcooling and superheat all move with load, so a raw month-to-month comparison
confuses "ran harder this month" with "degraded this month." Every detector fits
`metric ~ f(tons)` over the baseline period (`camber.chillerbaseline.fit_load_baseline`) and scores
the current period as the **residual against that fit** — the drift at matched load. Two readouts come
off the same frozen baseline:

- a **period statistic** — how far the current period sits above/below the baseline, in °F and in
  baseline sigmas (`camber.chillerbaseline.load_drift_stats`);
- a **sustained-shift alarm** — a streaming tabular CUSUM
  (`camber.chillerdrift.ApproachDriftMonitor`) that fires only when the signal moves **and stays
  moved**, not on a single hot hour.

## The detector family

| Detector | Signal | Sides | Catches |
|---|---|---|---|
| `ChillerApproachFouling` / `ChillerApproachDrift` | condenser/evaporator approach | one-sided (up) | heat-transfer loss (fouling, air/water flow) |
| `ChillerSubcoolingDrift` | liquid-line subcooling | two-sided | refrigerant **charge** (under/overcharge, non-condensables) |
| `ChillerSuperheatDrift` | suction superheat | two-sided | evaporator **feed** (overfeed/floodback vs. starvation) |
| `ChillerCwRangeDrift` | condenser-water range / ΔT | two-sided | condenser-side hydraulics |
| `CoolingTowerApproachDrift` | tower approach (CW supply − wet-bulb) | one-sided (up) | tower heat rejection (fouled/scaled fill, plugged nozzles, reduced airflow) |
| `ChillerHeadPressureDrift` | discharge / condensing pressure | one-sided (up) | high-side heat rejection (fouling/scale, non-condensables, reduced CW flow), read off the gauge |
| `ChillerSuctionPressureDrift` | suction / evaporating pressure | two-sided | low-side evaporator condition — a fall is heat-transfer loss / low charge / starved feed, a rise is overfeed / flooding |

**The condenser side pairs up.** `ChillerCwRangeDrift` (hydraulics) and `CoolingTowerApproachDrift`
(tower heat rejection) sit on the same condenser water loop as the chiller's condenser approach — a
widening tower approach raises condenser-water temperature, chiller lift, and kW/ton, so it shows up
downstream in the chiller too. The tower approach is one-sided (fouling only widens it) and is scored
against a load-normalized baseline just like the chiller detectors; wet-bulb is taken measured, or
derived from outdoor dry-bulb + RH (Stull) when it isn't a BAS point.

**One condenser-loop verdict.** These four condenser-side signals fail *independently* (a scaling
tube, a throttled valve, a fouled tower, and a rising high-side pressure localize different things) but
corroborate when a problem is system-wide. `camber.condenserdrift.diagnose_condenser_drift(findings)`
reads the individual drift Findings and returns one localized `CondenserDriftDiagnosis` — naming the
cause of each drifting signal (tube fouling/scale · reduced CW flow vs. bypass · tower heat-rejection ·
high-side pressure rising) and flagging **corroboration** when two or more agree. It isolates the
chiller condenser leg from the evaporator leg (the approach rule scores both), and for head pressure it
uses the tower signal to disambiguate the entering-CW-temperature confound — a co-moving CW-temp rise
*backed by* a degrading tower corroborates a real heat-rejection fault, while the same rise with a
quiet tower is flagged as likely ambient rather than a high-side fault. Stays screening-grade:
corroboration raises priority and specificity, not the severity tier — the thing that turns a set of
screening alerts into a work order.

**Surfacing the verdict.** The per-loop verdicts flow downstream like the chiller, pump, and AHU
ones: `camber.integrate.export.condenser_diagnoses_to_frame` / `export_condenser_diagnoses` write one
row per loop (severity · corroborated · joined causes · caveat count · fingerprint — note there is
**no locus / loop-wide** column, since the condenser diagnosis carries neither) to CSV/JSON/Parquet,
and `camber.report.condenser_diagnosis_table` renders a worst-first HTML table.
`build_site_report(..., condenser_diagnoses=[...])` splices that table into the owner-facing site
report, alongside the chiller, pump, and AHU verdict tables.

**Head pressure is the high side, read directly.** `ChillerHeadPressureDrift` trends the discharge /
condensing pressure (`Role.DISCHARGE_PRESSURE`, psig) — the same fault modes that widen the condenser
approach (fouling/scale, non-condensables, reduced CW flow) also raise head pressure, but the pressure
is directly instrumented, often earlier, and it is what a mechanic actually gauges. It is **one-sided**
like approach (only a rise is a fault) and scored against the same load-normalized frozen baseline. **Its
confound is stated, not hidden:** head pressure also climbs with entering condenser-water temperature and
ambient wet-bulb, which load normalization does *not* remove — so when a CW-supply point is mapped the
rule reports the concurrent CW-supply shift and **caveats a co-moving rise** (some of the climb may be
heat-rejection/ambient-driven, not a high-side fault); a mapped `Role.SUCTION_PRESSURE` adds the
condensing-over-suction *lift* as further context. Absolute head pressure is refrigerant-dependent, so
the **sigma floor carries the weight** (self-scaling against the baseline's own scatter) and the psi
floor is only a coarse backstop.

**Suction pressure is the low side, read directly.** `ChillerSuctionPressureDrift` is the evaporator
twin: it trends the suction / evaporating pressure (`Role.SUCTION_PRESSURE`, psig). At matched load a
*fall* is the evaporator heat-transfer-loss / low-charge / starved-feed signature and a *rise* is
overfeed / flooding, so unlike head pressure it is **two-sided** (both directions are faults, scored on
magnitude with the sign reported), sharing head pressure's psi/σ floors because it is the same raw-gauge
signal class. Its confound is the mirror of head pressure's: suction pressure tracks *chilled-water*
supply temperature, so a chilled-water reset lifts it with no fault — the rule reports the concurrent
CHW-supply shift and **caveats a co-moving move** as possibly setpoint-driven.

**Subcooling and superheat are complementary.** Subcooling watches the condenser/liquid side (how much
liquid is standing in the condenser); superheat watches the evaporator/suction side (whether the
evaporator is fed correctly). Both are **two-sided** because both directions are genuine faults —
subcooling falls on undercharge and rises on overcharge; superheat falls on overfeed (liquid-floodback
risk) and rises on starvation. The magnitude is scored symmetrically and the **direction is reported
alongside**, so an equal rise and fall score identically while the sign says which fault it is.

**One evaporator-loop verdict.** Mirroring the condenser side, three evaporator-side signals — the
chiller's **evaporator-approach** leg (heat transfer), **superheat** (feed), and **suction pressure**
(the low-side pressure itself) — combine in `camber.evaporatordrift.diagnose_evaporator_drift(findings)`,
which returns one localized `EvaporatorDriftDiagnosis` naming each cause (tube fouling/scale ·
overfeed-floodback vs. starvation · heat-transfer loss/low-charge vs. overfeed/flooding) and flagging
**corroboration** when two or more agree. It isolates the evaporator leg from the condenser leg (the
approach rule scores both). Because superheat and suction pressure are two reads on the same
feed/charge axis, it **cross-checks** them: both agreeing on overfeed (falling superheat + rising
suction) or on starvation (rising superheat + falling suction) is a strong, specific verdict, while a
disagreement is called ambiguous rather than asserted — the low-side twin of the tower-disambiguates-
head-pressure check on the condenser side. Screening-grade and pure over Findings.

**Surfacing the verdict.** Like the condenser side, the per-loop evaporator verdicts flow downstream:
`camber.integrate.export.evaporator_diagnoses_to_frame` / `export_evaporator_diagnoses` write one row
per loop (severity · corroborated · joined causes · caveat count · fingerprint — **no locus /
loop-wide** column, as the diagnosis carries neither) to CSV/JSON/Parquet, and
`camber.report.evaporator_diagnosis_table` renders a worst-first HTML table.
`build_site_report(..., evaporator_diagnoses=[...])` splices it into the owner-facing site report,
alongside the chiller, condenser, pump, and AHU verdict tables.

**One whole-machine verdict.** `camber.chillerdiag.diagnose_chiller_drift(findings)` rolls both side
diagnoses into a single per-chiller `ChillerDriftDiagnosis` and adds the cross-side reasoning neither
side can do alone: only the condenser side degrading localizes to the condenser loop, only the
evaporator side to the evaporator, but **both sides drifting together** points at a *circuit-wide*
cause (refrigerant charge, non-condensables, a compressor / metering fault) rather than one fouled
heat exchanger. Liquid-line subcooling folds in as the dedicated charge signal — a subcooling drift
alongside both sides moving corroborates a charge / inventory problem. It reports a `locus` (steady ·
condenser · evaporator · charge · whole-machine) and a `machine_wide` flag, so a screening pass can
separate "one exchanger needs a walkdown" from "gauge the whole machine". Screening-grade; re-uses the
side diagnoses unchanged.

### Instrumentation gating

Subcooling and superheat are **controller-reported differences**, and discharge/suction pressure are
**raw pressures** — CAMBER models no refrigerant saturation curve, so none of them can be derived from a
plain temperature and each must be mapped directly (`Role.SUBCOOLING_TEMP`, `Role.SUPERHEAT_TEMP`,
`Role.DISCHARGE_PRESSURE`). Many chillers do not publish a given point, so the rule that depends on it
**declines with a caveat** when it is absent — a chiller missing from a charge/feed/high-side report must
never read as a healthy one.

## Thresholds are honest about what they are

Every finding is labelled with two threshold classes (`camber.driftthresholds`), because they are not
provisional in the same way:

- **Magnitude floors** (`warn_f`, `fault_sigma`, …) are **screening-grade**: characterized for the
  signal class and good enough to **rank a walkdown**, but not established on your specific machines.
- **Temporal / CUSUM parameters** are **provisional-untuned**: textbook starting points whose
  false-alarm rate and detection delay have never been measured on real chiller trends.

Read a sustained alarm as "worth looking at now," never as a dispatch-grade verdict — until you
calibrate.

## Calibrating the thresholds

Every threshold above is a **constructor argument**, so tuning is a config change, not a code change.
When you have chiller periods with **confirmed, dated fault events**, `camber.driftvalidation` turns
that evidence into a calibrated operating point.

**No labelled data yet? Characterize on physics.** `camber.driftsim` generates physically consistent
`(baseline, current)` frame pairs for a healthy chiller and for the standard fault families (condenser
fouling, reduced CW/evaporator flow, tower degradation, under/overcharge, non-condensables, excess
oil), imposing each fault's known signature at a graded severity. It runs the whole suite + roll-up
end-to-end (`diagnose_frames`) and scores localization (`locus_confusion`) — on clear faults the
roll-up lands on the right `locus` at ~100% with no false alarms on healthy periods, and its
`SimulatedCase.to_labeled(relevant=…)` feeds the same `evaluate`/`sweep` per-detector ROC below. This
turns *screening-grade* thresholds into *characterized* ones; real-data tuning still refines them.

```python
from camber.driftsim import make_cases, locus_confusion

lc = locus_confusion(make_cases(), min_severity=3)  # localization on the clear faults
print(lc.accuracy, lc.as_dict()["matrix"])
```

**The condenser heat-rejection family has its own physics validator.** Because
`diagnose_condenser_drift` produces a *cause + corroboration* verdict (not a `locus`),
`camber.condensersim` characterizes it with a `CauseConfusion` instead: it models the coupled loop —
condensing temperature `TCOND = CWS + condenser approach`, entering water `CWS = wet-bulb + tower
approach` — so co-movement is emergent (tube scaling widens the condenser approach **and** lifts head
pressure; a fouling tower lifts `CWS` **and** head pressure), and it includes the negative confound
`ambient_cw_rise` — a CW/head rise with a **quiet tower** that the head-pressure confound must demote
to likely-ambient rather than flag. On clear faults it names the right cause and sets the right
corroboration flag at ~100% with no false alarms, and `SimulatedCase.to_labeled(relevant=…)` feeds
the same per-detector ROC.

```python
from camber.condensersim import make_cases, cause_confusion

cc = cause_confusion(
    make_cases(), min_severity=3
)  # cause detection + corroboration on clear faults
print(cc.accuracy, cc.corroboration_accuracy, cc.as_dict()["matrix"])
```

```python
from camber.driftvalidation import LabeledCase, evaluate, sweep
from camber.rules.chiller_superheat_rule import ChillerSuperheatDrift
from camber.store.modelstore import BaselineStore

# Label each (baseline, current) period pair faulty or healthy.
cases = [
    LabeledCase("CH_1", baseline_df, current_df, fault=True),
    LabeledCase("CH_2", baseline_df2, current_df2, fault=False),
    # ...
]


# A fresh detector per case (so no case leaks its frozen baseline into another).
def build(**params):
    return ChillerSuperheatDrift(BaselineStore(), site="plant", run_id="cal", **params)


# Score the shipped defaults:
score = evaluate(lambda: build(), cases)
print(score.as_dict())  # precision / recall / f1 + confusion counts

# Search a grid for the best operating point:
best = sweep(
    build,
    cases,
    {"fault_f": [3.0, 4.0, 5.0], "fault_sigma": [6.0, 8.0]},
    objective="f1",  # or "recall" / "precision" / "accuracy" / "youden"
)
print(best.best_params)  # feed these straight back into the rule's constructor
```

`sweep` scores every combination in the grid and returns the parameters that maximize the objective,
breaking ties toward **fewer false positives** (the quieter operating point wins). The harness is built
on CAMBER's FDD confusion matrix (`camber.eval.confusion`), so its precision/recall are the same
metrics the rest of the validation suite uses.

The shipped defaults stay screening-grade until you replace them with calibrated values — the harness
is the tool for doing that against real data, not a change to the defaults themselves.
