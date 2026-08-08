# Feasibility of the remaining chiller-gap tiers, against CAMBER's actual data model

**Status:** evaluation only — nothing here is built · **Companion to:**
[`chiller_drift_detection_plan.md`](chiller_drift_detection_plan.md)

The gap review listed four tiers after the P0 approach-drift work: condenser-water range/ΔT,
cooling-tower approach-to-wet-bulb, head/condensing-pressure trend, and PNNL-style reset + staging
detection. This note checks each against the roles that actually exist in `camber/model/roles.py`
and the modules already shipping, so the recommendation is grounded in available data rather than in
what the metric would be worth if the points existed.

The headline finding is that **most of this is already built, and the one genuinely new item is
small.** The gap review measured metric coverage; CAMBER's real gap was never the metrics, it was
that every one of them is scored as a level against a static constant, with no time axis — exactly
what the P0 work fixed for approach.

## Summary

| Tier | Classification | Verdict |
|---|---|---|
| Condenser-water range / ΔT | **FEASIBLE NOW** | **BUILT** — `chiller_cw_range_drift`, the only real coverage gap, and it was small |
| Cooling-tower approach-to-wet-bulb | **FEASIBLE NOW** — already built | **Park the metric; consider drift** |
| PNNL CHW / CW reset detection | **FEASIBLE NOW** — already built | **Park** |
| PNNL chiller staging detection | **FEASIBLE NOW** — already built | **Park** |
| Head / condensing-pressure trend | **NEEDS NEW INSTRUMENTATION** | **Park until points exist** |

Nothing falls into *feasible with mapping* as a distinct case: every tier either has its roles
defined already (so it is a mapping exercise per building, not a code question) or has no role at
all. The one nuance is wet-bulb, noted below.

---

## 1. Condenser-water range / ΔT — FEASIBLE NOW · **build**

**Roles:** `Role.CW_SUPPLY_TEMP` (`roles.py:74`) and `Role.CW_RETURN_TEMP` (`roles.py:75`) both
exist and are already mapped by two shipping rules (`rules/condenserwater_rule.py:20`,
`rules/coolingtower_rule.py:22`). `camber/sensorhealth.py:65` even carries a plausibility band for
`CW_RETURN_TEMP`, so the point is a first-class citizen. Range is `CWR − CWS`, needing no derivation
beyond a subtraction.

**What exists already:** `analyze_cooling_tower_approach` computes the range *incidentally*, as a
gate — `min_range_f: float = 2.0  # CW range below this == not really rejecting heat`
(`coolingtower.py:78`). It is used to decide whether the tower is working, then discarded. Nothing
diagnoses the range itself.

**Why it is worth building.** A drifting condenser-water range at constant load is the classic
signature of condenser-side flow problems — a fouled or throttled condenser, a failing CW pump, a
stuck balancing valve — and it is *independent evidence* for the same fault the approach drift
detects. Two agreeing indicators from different physics is what turns a drift alert into a
confident work order. There is also a direct precedent in the codebase: `hw_plant_deltat` is the
hot-water analogue and is already a registered rule, so the shape is established.

> **Correction, made when the detector was built.** An earlier revision of this note said a
> *narrowing* range is the flow-problem signature. That has the sign backwards for the
> load-normalized comparison this rule actually makes. The energy balance is
> `Q_cond = 500 × gpm × range`; at matched chiller load `Q_cond` is essentially fixed, so range is
> **inversely** proportional to condenser-water flow. Reduced flow — a restricted bundle, a
> degrading pump, a throttled or drifting balancing valve, a fouling strainer — therefore **widens**
> the range. A *narrowing* range means the opposite: too much flow, or flow bypassing the condenser
> (an opened bypass, short-circuiting, a balancing valve backed off), which wastes pump energy and
> returns colder water than the tower was selected for. Both are genuine faults with different
> causes, so the detector is two-sided: symmetric on magnitude, with the sign reported.
>
> This also sharpens the "independent evidence" claim. A scale layer that impedes *heat transfer*
> without much restricting *flow* widens the approach and barely moves the range; a partly-closed
> balancing valve does the reverse. The two signals fail independently, which is exactly why they
> corroborate each other when they do move together.

**How it reuses the P0 machinery.** Range is load-dependent in the same way approach is — it widens
as tons rise, because the same flow carries more heat. So it is the identical problem:
`fit_approach_baseline` generalizes to `range ~ f(tons)` with nothing but a column swap, tons come
from the same `tons_from_flow` derivation, `drift_stats` gives the °F-and-σ verdict, `BaselineStore`
freezes the coefficients under a new `kind` (`chiller_cw_range`), and `ApproachDriftMonitor` gives
the sustained-shift alarm. In practice this is a small generalization of `chillerbaseline.py` from
approach-specific to a role-agnostic `fit_baseline(y ~ x)` plus a thin rule — which is precisely the
"generalize when the second consumer arrives" trigger flagged as open decision 4 in the plan.
**This tier is that second consumer.**

**Status: built.** The generalization landed first (`fit_load_baseline` / `load_drift_stats`), and
`camber/rules/chiller_cw_range_rule.py` is the thin rule on top of it — a column swap, not a new
module of machinery. The range subtraction itself is now
`camber.coolingtower.cw_range_f`, extracted from the gate above so the two consumers share one sign
convention rather than each spelling it out. Coefficients freeze under `kind="chiller_cw_range"`,
and the sustained-shift alarm is the same `ApproachDriftMonitor` run `direction="both"`.

## 2. Cooling-tower approach-to-wet-bulb — FEASIBLE NOW, but **already built**

**Roles:** `Role.CW_SUPPLY_TEMP` plus `Role.WETBULB_TEMP` (`roles.py:79`), with
`Role.OUTDOOR_RH` (`roles.py:80`) and `Role.OAT` (`roles.py:25`) as the fallback path.

**What exists already:** `camber/coolingtower.py::analyze_cooling_tower_approach` and the registered
`cooling_tower_approach` rule compute exactly this metric — `approach = CW_supply_temp −
wet_bulb_temp` — and it is categorized in `scorecard.RULE_CATEGORY`. It even handles the mapping
problem the gap review would have raised: wet-bulb is rarely a BAS point, so when `WETBULB_TEMP`
is absent it is derived from dry-bulb and RH via Stull's closed-form approximation
(`coolingtower.py::stull_wetbulb_f`), with the source recorded. `TOWER_FAN_SPEED` (`roles.py:76`)
gates "operating".

**Recommendation: park the metric, and treat this as a candidate for drift later.** Building the
metric again would be duplication. The rule has the *same* structural weakness the approach rule
had — `design_approach_f: float = 7.0` is an injected static constant and the verdict is a level,
so a tower whose approach has walked from 4 °F to 7 °F reads identically to one that was always at
7 °F. Applying the frozen-baseline drift treatment to it is a real improvement, but it is a
**second** application of the same machinery, and it should follow the condenser-water range work
rather than precede it: range is a coverage gap, this is a refinement of existing coverage.

## 3. PNNL-style CHW / CW reset detection — FEASIBLE NOW, but **already built**

**Roles:** `Role.CHW_SUPPLY_TEMP`, `Role.CHW_SUPPLY_TEMP_SP` (`roles.py:67`), `Role.CHW_DIFF_PRESS`
and `Role.CHW_DIFF_PRESS_SP` (`roles.py:68-69`), `Role.CW_SUPPLY_TEMP`, `Role.OAT`,
`Role.WETBULB_TEMP`.

**What exists already:** three registered rules cover this ground.

- `camber/condenserwater.py::analyze_cw_reset` — regresses CW supply temp on wet-bulb and flags a
  flat slope as a held setpoint. Its docstring is explicit that this is the reset test.
- `camber/chwplant.py::analyze_chw_plant` — the CHW-side analogue, with its own `_ols_slope`.
- `chw_pump_dp_reset` / `hw_pump_dp_reset` — the differential-pressure reset rules.

All are in `scorecard.RULE_CATEGORY` under `energy`. **Recommendation: park.** This tier is
complete; rebuilding it would be pure duplication. If anything is wanted here it is a review of the
existing slope thresholds, not new analysis.

## 4. PNNL-style chiller staging detection — FEASIBLE NOW, but **already built**

**Roles:** `Role.POWER` (`roles.py:105`), `Role.CHW_FLOW`, `Role.CHW_SUPPLY_TEMP`,
`Role.CHW_RETURN_TEMP` — the same set the efficiency rule uses.

**What exists already:** `camber/chillerstaging.py` provides `analyze_chiller_staging` (starts per
day, low part-load runtime) and `analyze_chiller_staging_fleet`, registered as `chiller_staging`
and `chiller_staging_fleet`. The module docstring cites PNNL Re-tuning / ASHRAE directly and is
candid about its own boundary: true multi-chiller staging *optimization* is a fleet question, and
what ships catches per-machine cycling and idling.

**Recommendation: park.** The honest remaining gap is sequencing optimization across N machines,
which is a materially larger piece of work than anything else on this list, needs plant-level
sequencing intent as an input (which chiller is lead, what the stage-up thresholds are), and has no
dependency on the drift machinery. It should be scoped on its own merits, not folded into this
effort.

## 5. Head / condensing-pressure trend — NEEDS NEW INSTRUMENTATION · **park**

**Confirmed: there is no refrigerant-pressure role and no condensing-temperature role in CAMBER.**
Grepping `roles.py` for pressure returns only water- and air-side points —
`Role.HW_DIFF_PRESS` (`roles.py:61`), `Role.CHW_DIFF_PRESS` / `Role.CHW_DIFF_PRESS_SP`
(`roles.py:68-69`), `Role.DUCT_STATIC` (`roles.py:47`), `Role.FILTER_DIFF_PRESS` (`roles.py:94`).
None is refrigerant-side.

The entire refrigerant-side section of the role enum is two entries
(`roles.py:98-101`): `COND_APPROACH_TEMP` and `EVAP_APPROACH_TEMP`. Both are **already-differenced
degF quantities** — a controller-reported gap between saturation temperature and leaving water —
not the saturation temperatures themselves. Head pressure cannot be recovered from a difference
whose other term was never recorded, and there is no refrigerant-property table in the codebase to
convert a condensing temperature to a pressure even if one were mapped.

This is the same wall superheat hit: suction and discharge superheat need suction/discharge line
temperatures and saturation conditions, and CAMBER has neither role. Head pressure sits in exactly
that category.

**Recommendation: park until the points exist.** Delivering this needs, in order: new `Role`
members for condensing/evaporating pressure or saturation temperature; a point-mapping story
(`ROLE_HINTS` entries, ontology mappings in `interop/`, sensor-health plausibility bands); and
confirmation the chillers in scope actually publish those points to the BAS — many report approach
temperatures precisely *because* they do not expose refrigerant pressures. That is a data-model
change, not an analytics change, and it should not be scheduled before someone has confirmed the
instrumentation exists on real machines.

---

## Recommendation

**Build one thing: condenser-water range/ΔT drift.** It is the only genuine coverage gap, its roles
already exist and are already mapped by shipping rules, it gives independent physical corroboration
of the approach drift the P0 work now detects, and it is the natural second consumer that justifies
generalizing `chillerbaseline.py` from approach-specific to role-agnostic.

**Park the rest.** Three tiers are already implemented (tower approach, reset detection, staging) —
the useful work there is applying the drift treatment to the *existing* rules, not rebuilding their
metrics, and that should queue behind the range work. One tier (head pressure) is blocked on
instrumentation that may not exist on the equipment at all.

### Owner decisions this raises

1. **Generalize `chillerbaseline.py` now?** Open decision 4 in the plan recommended staying
   approach-specific until a second consumer appeared. Condenser-water range is that consumer, so
   the recommendation flips *if* range is approved. Approving range and keeping the module
   approach-specific would mean near-duplicate code, which is the worse of the two outcomes.
2. **Retrofit drift onto the existing tower/reset rules?** Each is a level-vs-static-constant
   verdict with the same blind spot the approach rule had. Worth doing, but it is a distinct piece
   of work and would benefit from the threshold validation (decision 3) landing first, since it
   would inherit the same unvalidated σ/°F floors.
3. **Is refrigerant-side instrumentation available at all?** Before head pressure or superheat is
   scheduled, someone should confirm the chillers publish suction/discharge or condensing/evaporating
   pressures. If they do not, both tiers should be closed rather than parked.
