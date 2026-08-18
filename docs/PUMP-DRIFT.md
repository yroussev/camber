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

## Calibration

Thresholds are constructor arguments (screening-grade); the CUSUM parameters are provisional-untuned.
As with the chiller family, `camber.driftvalidation` (`LabeledCase` / `evaluate` / `sweep`) tunes them
once labelled pump-fault periods exist, and a physics generator (affinity laws + system curve) will
characterize the family end-to-end without a dataset.
