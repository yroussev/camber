# AHU / air-side drift detection

The chiller ([CHILLER-DRIFT.md](CHILLER-DRIFT.md)) and pump ([PUMP-DRIFT.md](PUMP-DRIFT.md)) families
watch the refrigerant and hydronic sides. The AHU family asks the same "is this slowly getting worse
than it used to be, at matched load?" question of the **air side** — supply fans, coils, filters, and
duct-static control — reusing the exact same load-normalized frozen-baseline engine
(`camber.chillerbaseline`, `camber.chillerdrift`), with an *air-side duty* normalizer (airflow) in
place of thermal tons.

Like the other families, each detector is a **period rule** (`Registry.run_periods`), freezes a
load-normalized baseline into a `BaselineStore` on first use, reports a period statistic **and** a
sustained-shift CUSUM alarm, labels its thresholds *screening-grade* / *provisional-untuned*, and
**declines loudly** (never reads healthy) when an instrumented point is missing. They complement the
existing static air-side rules (`economizer_lockout`, `satreset`, `staticreset`, `airflow`) the way
the chiller drift rules complement the static approach check.

## The detector family

| Detector | Signal | Normalizer | Sided | Catches |
|---|---|---|---|---|
| `FanEfficiencyDrift` | supply-fan power | airflow (cfm) | up | wire-to-air efficiency loss — a slipping/worn belt, bearing drag, a degrading motor/VFD, or the fan pushed off its curve; a power **excess** at matched airflow |
| `FilterLoadingDrift` | filter differential pressure | airflow (cfm) | up | filter loading (dirty filter) — a DP **rise** at matched airflow; a fall is a filter change |

**Fan efficiency is the air-side energy signal.** A healthy fan draws a repeatable power at a given
airflow; more power at matched airflow is efficiency loss. It is **one-sided up** and reuses the
generic `Role.POWER` on the AHU equip-frame (the equip identifies the fan) with `Role.AIRFLOW` as the
normalizer — the air-side twin of `PumpPowerDrift`. **Its confound is stated:** fan power also rises
when the *duct-static setpoint* is raised (the fan works harder to hold a higher static), so when a
duct-static point is mapped the rule reports the concurrent static shift and caveats a power excess
that co-moves with rising static.

## Calibration

Thresholds are constructor arguments (screening-grade); the CUSUM parameters are provisional-untuned.
As with the other families, `camber.driftvalidation` tunes them once labelled AHU-fault periods exist,
and a physics generator will characterize the family end-to-end without a dataset.
