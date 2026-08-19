# Pump / hydronic drift detection

The chiller drift family ([CHILLER-DRIFT.md](CHILLER-DRIFT.md)) asks "is this machine slowly getting
worse than it used to be, at matched load?" The pump / hydronic family asks the same question of the
**distribution side** — pumps and the loops they serve — reusing the exact same load-normalized
frozen-baseline engine (`camber.chillerbaseline`, `camber.chillerdrift`), only with a *duty*
normalizer (pump speed or flow) in place of thermal tons.

Like the chiller detectors, each is a **period rule** (`Registry.run_periods`), freezes a
load-normalized baseline into a `BaselineStore` on first use, reports a period statistic **and** a
sustained-shift CUSUM alarm, labels its thresholds *screening-grade* / *provisional-untuned*
(`camber.driftthresholds`), and **declines loudly** (never reads healthy) when an instrumented point
is missing. They are loop-parameterized — one class serves a chilled-water or hot-water loop; the
equip identifies which pump.

## The detector family

| Detector | Signal | Normalizer | Sided | Catches |
|---|---|---|---|---|
| `PumpFlowDrift` | flow | pump speed (Q ∝ N) | down | pump wear (impeller/wear-ring), clogged strainer, cavitation, entrained air — a flow **deficit** at matched speed |
| `PumpHeadDrift` | differential head | pump speed (H ∝ N²) | down | pump wear directly — a head **deficit** at matched speed; with flow, disambiguates pump-wear from system-resistance |
| `LoopDeltaTDrift` | loop ΔT (return − supply) | flow (or speed proxy) | both | low-ΔT syndrome (collapse: overpumping, fouled coils, stuck valves, bypass) vs underflow/starvation (widen) |
| `LoopDPDrift` | loop differential pressure | flow (system curve DP ∝ Q²) | both | rising system resistance / valve-authority loss vs bypass / stuck-open — with the DP-reset schedule subtracted out |
| `PumpPowerDrift` | electrical power | flow (P ∝ Q³) | up | wire-to-water efficiency loss (bearing/seal drag, degrading motor/drive, recirculation) — a power **excess** at matched flow |

**Flow-at-matched-speed is the pump-wear signal.** A healthy VFD pump delivers a repeatable flow at a
given speed; a deficit means the pump (or its suction) has degraded. It is **one-sided down** — only a
deficit is a fault — so `ApproachDriftMonitor` gained a `direction="down"` mode for it (alongside the
existing `up` and `both`).

**The confound is stated, not hidden.** A flow deficit at matched speed is ambiguous between *pump
wear* and a *rise in system resistance* (a throttled or stuck-closed valve downstream). Load
normalization doesn't remove it, so when a loop differential-pressure point is mapped the rule reports
the concurrent DP shift and **caveats** a deficit that co-moves with *rising* DP — the resistance
signature, not pump wear. (The head-drift detector and the loop diagnosis, coming next in the family,
resolve it further: flow↓ **and** head↓ → the pump; flow↓ with head steady → the distribution.)

## One per-loop verdict

The four detectors fail *independently* (a worn impeller, a throttled valve, a fouled coil, and a
lost DP setpoint are different faults) but corroborate when a problem is loop-wide.
`camber.pumpdrift.diagnose_pump_drift(findings)` reads them and returns one localized
`PumpDriftDiagnosis` — naming each cause, flagging **corroboration** when two or more agree, and
running the **flow-vs-head disambiguation** that no single signal can do:

- flow deficit **and** head deficit at matched speed → **the pump itself** (impeller / wear-ring /
  cavitation), corroborated;
- flow deficit with head **steady** → **the distribution** (a throttled / stuck-closed valve
  downstream), not pump wear — look at the loop, not the impeller;
- flow deficit with **no head point** → called ambiguous rather than asserted.

It splits the loop into a mechanical (pump) and a hydraulic (distribution) side and reports a `locus`
(steady · pump · distribution · loop-wide) with a `loop_wide` flag — the pump-side analog of
`condenserdrift`'s tower-disambiguates-head-pressure check. Screening-grade; pure over Findings.

## One per-plant verdict

Real plants run several pumps. `camber.pumpplantdiag.diagnose_pump_plant(diagnoses)` rolls the
per-loop `PumpDriftDiagnosis` objects into one plant verdict with the cross-pump reasoning: exactly
one pump drifting → **single-pump** (stage the spare, schedule that impeller); two or more loops on
the **distribution** side → a shared/central hydraulic cause (plant-wide low-ΔT, a decoupler bypass, a
control problem) is more likely than several independent pumps; two or more pumps otherwise →
**plant-wide** (a common-mode cause — suction, water chemistry, a shared drive). It reports a plant
`locus` (steady · single-pump · distribution · plant-wide) and a plain-language `recommendation`.

## Surfacing the verdict

The per-loop and per-plant verdicts flow downstream like the chiller ones:
`camber.integrate.export.pump_diagnoses_to_frame` / `export_pump_diagnoses` write one row per loop
(locus · severity · loop_wide · corroborated · causes · fingerprint) to CSV/JSON/Parquet, and
`camber.report.pump_diagnosis_table` renders a worst-first HTML table. `build_site_report(...,
pump_diagnoses=[...])` splices that table into the owner-facing site report, alongside the chiller
verdict table.

## Calibration

Thresholds are constructor arguments (screening-grade); the CUSUM parameters are provisional-untuned.
As with the chiller family, `camber.driftvalidation` (`LabeledCase` / `evaluate` / `sweep`) tunes them
once labelled pump-fault periods exist, and the physics generator `camber.pumpsim` (affinity laws + system
curve) characterizes the family end-to-end without a dataset — on clear faults the loop diagnosis
localizes to the right `locus` at ~100% with no false alarms on healthy loops or a DP-reset schedule,
and it proves the flow-vs-head disambiguation (impeller wear → pump, clogged strainer → distribution).
