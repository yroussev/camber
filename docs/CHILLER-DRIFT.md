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

**The condenser side pairs up.** `ChillerCwRangeDrift` (hydraulics) and `CoolingTowerApproachDrift`
(tower heat rejection) sit on the same condenser water loop as the chiller's condenser approach — a
widening tower approach raises condenser-water temperature, chiller lift, and kW/ton, so it shows up
downstream in the chiller too. The tower approach is one-sided (fouling only widens it) and is scored
against a load-normalized baseline just like the chiller detectors; wet-bulb is taken measured, or
derived from outdoor dry-bulb + RH (Stull) when it isn't a BAS point.

**One condenser-loop verdict.** These three condenser-side signals fail *independently* (a scaling
tube, a throttled valve, and a fouled tower are three different faults) but corroborate when a problem
is system-wide. `camber.condenserdrift.diagnose_condenser_drift(findings)` reads the individual drift
Findings and returns one localized `CondenserDriftDiagnosis` — naming the cause of each drifting signal
(tube fouling/scale · reduced CW flow vs. bypass · tower heat-rejection) and flagging **corroboration**
when two or more agree. It isolates the chiller condenser leg from the evaporator leg (the approach
rule scores both), and stays screening-grade: corroboration raises priority and specificity, not the
severity tier — the thing that turns a set of screening alerts into a work order.

**Subcooling and superheat are complementary.** Subcooling watches the condenser/liquid side (how much
liquid is standing in the condenser); superheat watches the evaporator/suction side (whether the
evaporator is fed correctly). Both are **two-sided** because both directions are genuine faults —
subcooling falls on undercharge and rises on overcharge; superheat falls on overfeed (liquid-floodback
risk) and rises on starvation. The magnitude is scored symmetrically and the **direction is reported
alongside**, so an equal rise and fall score identically while the sign says which fault it is.

### Instrumentation gating

Subcooling and superheat are **controller-reported differences** — CAMBER has no refrigerant
saturation-temperature or pressure role, so they cannot be derived from a raw temperature and must be
mapped directly (`Role.SUBCOOLING_TEMP`, `Role.SUPERHEAT_TEMP`). Many chillers do not publish them, so
these roles are **optional** and the rule **declines with a caveat** when a point is absent — a chiller
missing from a charge/feed report must never read as a healthy one.

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
