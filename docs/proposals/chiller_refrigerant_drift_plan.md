# Proposal: the refrigerant-circuit drift set

**Status:** in progress — condenser approach and subcooling implemented · **Companion to:**
[`chiller_drift_detection_plan.md`](chiller_drift_detection_plan.md)

The P0 work built one detector — condenser approach drift against a frozen, load-normalized
baseline — and the machinery under it (`chillerbaseline`, `modelstore`, `chillerdrift`, the
`PeriodRule` interface) turned out to be signal-agnostic. This note re-scopes the refrigerant-circuit
work around **which circuit signals actually carry fault information**, rather than around which
ones a dashboard happens to plot.

## The scoping decision

A chiller's refrigerant circuit exposes four candidate drift signals. They are not equally useful,
and two of them are close to worthless as detectors on this class of machine:

| Signal | Verdict | Why |
|---|---|---|
| **Condenser approach** | **Primary** — built (PRs #2–#4) | The broadest single detector: responds to essentially anything that degrades condenser heat transfer, and to several charge-side faults besides |
| **Liquid-line subcooling** | **Build now** — this PR | The charge/inventory detector. Responds strongly to faults that barely move an approach at all, so it is genuinely *complementary* rather than redundant |
| **Discharge superheat** | **Deferred** | Marginal: responds to fewer fault families than either of the above, and only strongly to ones the other two already catch. Also needs a role CAMBER lacks |
| **Suction superheat** | **Rejected — not a detector** | Actively controlled. The expansion device holds it at setpoint, so the control system *erases* the signal: a healthy circuit and a degrading one both read at setpoint until control saturates |

The suction-superheat conclusion is the one worth stating explicitly, because it is
counter-intuitive and it is why "plot every circuit temperature" is the wrong design. **A controlled
variable is a poor fault detector by construction.** Its controller's job is to reject exactly the
disturbances a detector wants to see. What moves instead is the controller's *effort* — valve
position, or the deviation once the device runs out of authority — and that is a different
measurement with different instrumentation needs. Building a suction-superheat drift detector would
produce a rule that stays quiet through real faults, which is worse than having no rule at all.

## Why subcooling is the right second detector

Condenser approach and subcooling answer different physical questions:

- **Approach** measures how well the condenser *transfers heat*. It widens when the surface
  degrades — fouling, scale, air-side or water-side flow loss.
- **Subcooling** measures how much *liquid refrigerant is standing in the condenser*. It moves when
  the refrigerant inventory or its distribution changes.

Faults that change charge without changing surface condition move subcooling sharply while leaving
the approach nearly untouched, and the reverse is also true. Two detectors reading different physics
and agreeing is what turns a drift alert into a confident work order; two detectors reading the same
physics is just a louder version of one.

### Two properties that make it its own rule, not another leg

**1. It is two-sided, and both directions are faults.** Subcooling *falls* when the circuit is short
of liquid — undercharge, a leak — and *rises* when liquid backs up in the condenser: overcharge,
non-condensables, restricted condenser flow. This is a real departure from approach, which only ever
widens. The consequences run through the whole design:

- `ChillerSubcoolingDrift` scores the **magnitude** `|drift|` against its floors and reports the
  sign separately as `subcooling_drift_direction`.
- `ApproachDriftMonitor` gained an opt-in `direction="both"`. The default stays `"up"`, so every
  existing approach detector behaves **exactly** as before; a test asserts a sustained *fall* still
  does not alarm under the default.

**2. It is instrumentation-gated.** `Role.SUBCOOLING_TEMP` is a controller-reported *difference*,
exactly like the approach roles. CAMBER has no refrigerant saturation-temperature or pressure role,
so subcooling **cannot be derived** from a liquid-line temperature, and it cannot be recovered from
the approach temperatures either. It must be mapped directly where the chiller publishes it, and
many chillers do not.

The role is therefore declared **optional** on the rule rather than required. That is deliberate: a
required role makes `Registry.run_periods` skip the equipment *silently*, and a chiller silently
absent from a charge report reads as a chiller with good charge. Instead the rule always runs and
**declines with a caveat** naming the missing instrumentation — the honesty convention in
`rules/base.py` applied to a hardware gap rather than a data gap.

## What is reused, unchanged

Everything structural. Subcooling is load-dependent for the same reason approach is, so it needs no
new analytics:

| Component | Reused as |
|---|---|
| `chillerbaseline.fit_approach_baseline` / `drift_stats` | The fit and the °F-and-σ statistic, on the subcooling column |
| `store.modelstore.BaselineStore` | Frozen coefficients under a new `chiller_subcooling` kind, with the same `accept_new_normal` policy |
| `chillerdrift.ApproachDriftMonitor` | The streaming CUSUM, run `direction="both"` |
| `rules.base.PeriodRule` / `run_periods` | The explicit baseline/current window interface |

The one thing this confirms is that `chillerbaseline`'s naming is now the weakest part of the
design: `fit_approach_baseline` is a general `y ~ f(tons)` fit and is being called on a column that
is not an approach. Open decision 4 of the parent plan — whether to generalize — should be resolved
before a third signal joins.

## One Finding, not two

The approach side emits two Findings (`chiller_approach_drift` and
`chiller_approach_drift_sustained`) because its level check predates the drift work and had to keep
its behaviour. Subcooling has no such legacy, so the period statistic and the sustained-shift alarm
are reported in **one** Finding. "Subcooling has narrowed 2 °F and has stayed there for a fortnight"
is a single work order, and splitting it would create two tickets an operator has to reconcile.

## Thresholds and their confidence

`SUBCOOLING_WARN_F = 1.0`, `SUBCOOLING_FAULT_F = 2.0`, `SUBCOOLING_WARN_SIGMA = 3.0`,
`SUBCOOLING_FAULT_SIGMA = 6.0` — all constructor-overridable, all **screening-grade**, with
`metrics["thresholds_provisional"]` on every Finding.

These are **characterised starting points, not values established on the equipment being
monitored**, and they should be reviewed once a site has accumulated its own trend history with
known charge events. As with approach, a finding must clear **both** a °F floor and a σ floor, in
either direction.

The magnitude floors and the CUSUM timing parameters are labelled separately, because they are not
provisional in the same way (`camber/driftthresholds.py`): the °F/σ floors are `screening-grade`
(characterised for the signal class, adequate for ranking equipment for a walkdown, not established
on this equipment), while the temporal parameters are `provisional-untuned` (never tuned for these
signals at all — their false-alarm rate and detection delay are unmeasured). Findings carry
`magnitude_threshold_confidence` and/or `temporal_threshold_confidence` accordingly, so a downstream
consumer can see which kind of claim it is holding. **Full temporal validation awaits real trended
fault data** — chiller trends with confirmed, dated fault events.

The σ floors sit higher than the approach rule's (3/6 versus 2/3). Subcooling's ordinary run-to-run
scatter is wider relative to its fault response than an approach's, so a 2σ floor of the kind that
suits approach sits inside this signal's normal variation and would fire on healthy machines.

The CUSUM parameters (`slack`, `limit`, `clip`, decision interval) are inherited from the approach
detector unchanged. **They are not independently tuned for subcooling**: they govern temporal
behaviour, and setting them properly needs continuous trend history rather than steady-state
characterisation.

## Deferred: discharge superheat

Not built. It would need a discharge-line-temperature role CAMBER does not have, and it earns less
than either detector above — it responds to fewer fault families, and the ones it responds to
strongly are already covered. If it is ever built it should reuse this exact machinery, one-sided or
two-sided depending on how it behaves on the target equipment.

**This is a recommendation, not a closed door.** If a site's chillers publish discharge-line
temperature and the owner wants circuit coverage as broad as the instrumentation allows, it is a
small addition on top of what now exists.

## Open decisions

1. **Generalize `chillerbaseline.py`? — DECIDED, DONE.** The core fit is now the metric-neutral
   `fit_load_baseline(metric_col, load_col, ...)`, with `load_drift_stats` alongside it;
   `fit_approach_baseline`, `fit_subcooling_baseline` and `drift_stats` are thin, behaviour-
   identical wrappers, and `LoadBaseline`/`LoadDrift` carry `ApproachBaseline`/`ApproachDrift` as
   aliases so nothing written against the old names breaks. Condenser-water range reuses the fit
   with a column swap rather than a fourth copy of it.
2. **Threshold validation** remains open for both detectors, and gates three rules' severities. It
   is now split into two questions, because the two threshold classes need different evidence: the
   magnitude floors need equipment-specific characterisation, the temporal parameters need trend
   history containing confirmed, dated fault events. See "Thresholds and their confidence" above.
3. **Is discharge-line temperature available?** Determines whether the deferred tier is a small
   addition or permanently closed.
4. **Should subcooling alarm asymmetrically? — DEFERRED, deliberately.** It is symmetric today: one
   pair of floors applied to `|drift|`, with the sign reported separately
   (`subcooling_drift_direction`, and the CUSUM's `alarm_direction` under `direction="both"`). A
   tighter falling-side floor is defensible on operational grounds — a leak is a refrigerant-loss
   and environmental issue, an overcharge usually is not — but there is no basis on which to set
   *how much* tighter, and a wrong asymmetry is worse than none: it silently desensitises one half
   of the fault space. Kept symmetric pending real-data validation. It remains an operational
   judgement, not a technical one, so it is an owner decision when the data exists.
