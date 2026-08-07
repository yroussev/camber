# Proposal: time-based chiller approach-drift detection

**Status:** proposed · **Scope:** P0 of the chiller-drift gap review · **Target:** 0.11.x–0.13.x

## Problem

Every chiller diagnostic in CAMBER today collapses the whole input window to one median and
compares it to a **static, constructor-supplied design constant**:

- `camber/rules/chiller_approach_rule.py` — `median(approach) / design_f`, severity at ratio ≥ 1.5 / 2.0.
- `camber/chiller.py::analyze_chiller_efficiency` — `median(kW/ton) / design_kw_per_ton`.

Neither observes *change*. A chiller that has sat at 8 °F approach since commissioning and one that
walked 4 °F → 8 °F over six weeks emit an identical `Finding`. The high-value signal — *approach
climbing for weeks before failure* — is not produced anywhere in the codebase. The rule docstring's
word "degradation" is aspirational: the implementation measures level, not trajectory.

Two structural gaps make this more than a threshold tweak:

1. **Approach is load-dependent.** Approach naturally widens with load. Comparing a lightly-loaded
   baseline month against a peak-summer current month without normalizing for tons produces drift
   that is really just a load-mix shift. A drift statistic is only meaningful *at matched load*.
2. **No chiller entry point accepts a time split.** `Rule.analyze(equip, frame)`
   (`camber/rules/base.py:78`) takes one frame and no date arguments. Adding baseline-vs-current is
   therefore an **interface change**, not just a new rule.

## Reconciliation: the "vs Load (tons), Baseline Fit" panels are not ours

The review that motivated this proposal cited a dashboard with four panels — evaporator approach,
suction superheat, condenser approach, discharge superheat, each plotted against load in tons with a
"Baseline Fit" line. That surface is **not produced by CAMBER**, and the distinction matters for
sizing: none of that fit exists to be reused.

- `superheat` appears in **no** source file, doc, role, test, or example in this repository.
- `camber/model/roles.py` defines `COND_APPROACH_TEMP` and `EVAP_APPROACH_TEMP` (`roles.py:99-100`)
  and **no** suction/discharge temperature or refrigerant-pressure roles, so superheat is not a
  quantity CAMBER can currently compute from mapped points.
- There is no chiller tons `Role` either; tons are derived from flow and loop ΔT (`chiller.py:83`).
- CAMBER's own dashboard (`camber/report/dashboard.py`) is a self-contained HTML page assembling the
  readiness ribbon, fault-annotated multi-trend, load carpet, and data-quality panels — no
  approach-vs-load scatter, no fitted baseline line.
- `camber/charts/diagnostic.py` is the closest visual analogue (scatter + expected band + violation
  mask), but its bands come from hand-specified `band()` / `reset_line()` schedules, and its packaged
  templates plot against OAT or valve position — **never** against load tons.

Conclusion: the panels come from a separate product surface (a chiller controller's own analytics or
a third-party plant-analytics tool), not from this codebase or any prototype in it. Nothing in the
P0 scope below is already done. The design here is deliberately shaped so a backend *could* feed
such a view — `ApproachBaseline.predict` plus `sigma_f` is exactly the `expected(x) -> (low, high)`
contract `DiagnosticTemplate` already consumes, which is why a `fitted_band()` constructor appears in
the phase-3 file list.

## What already exists (reuse, do not rebuild)

| Module | What it gives us | Wired to chiller? |
|---|---|---|
| `camber/mandv/online.py::OnlineCusum` | Two-sided tabular CUSUM of `predict(driver) − actual` with slack/limit and a sustained-shift `alarm` | No — energy/M&V only |
| `camber/changedetect.py` | CUSUM binary-segmentation level shifts on any series | No — zero callers |
| `camber/faultlifecycle.py` | Durable finding store keyed by `(site, equip, rule)`, status workflow, SLA aging | Yes, for findings |
| `camber/charts/diagnostic.py::DiagnosticTemplate` | `expected(x) -> (low, high)` scatter-with-band plotting + violation mask | Bands are hand-specified schedules, not fits |
| `camber/store/parquet_store.py` | Long-format time-series persistence | Data only — no model coefficients |

`OnlineCusum` takes any `predict: f(driver) -> float`. Swapping its driver from weather→energy to
**tons→approach** is the shortest credible path to a sustained-drift alarm, and requires no change
to `online.py` itself.

## Design

### Layer 1 — `camber/chillerbaseline.py` (new, additive) — **this PR**

A pure-function, dependency-light fit of `approach ~ f(tons)` that retains residual σ.

```python
@dataclass
class ApproachBaseline:
    n: int                     # samples retained after guards
    slope_f_per_ton: float     # °F of approach per ton
    intercept_f: float         # °F at zero load (extrapolated)
    sigma_f: float             # residual standard deviation, °F
    r2: float                  # goodness of fit
    tons_min: float            # fitted load envelope (for extrapolation guards)
    tons_max: float
    coverage_start: str
    coverage_end: str

    def predict(self, tons) -> float | np.ndarray: ...   # duck-types OnlineCusum's `predict`
    def residual(self, tons, approach_f) -> float | np.ndarray: ...   # actual − predicted, °F
    def z(self, tons, approach_f) -> float | np.ndarray: ...          # residual / sigma_f
    def covers(self, tons) -> bool: ...                   # inside the fitted load envelope?
    def as_dict(self) -> dict: ...                        # JSON-round-trippable
    @classmethod
    def from_dict(cls, d) -> ApproachBaseline: ...

def fit_approach_baseline(frame, *, approach_col, tons_col, min_tons=..., ...) -> ApproachBaseline | None
def tons_from_flow(frame, *, flow_col, supply_col, return_col) -> pd.Series
def drift_stats(baseline, frame, *, approach_col, tons_col) -> ApproachDrift | None
```

Design choices:

- **Ordinary least squares, degree 1.** Approach-vs-load is close to linear over a chiller's
  operating band, and a 2-parameter model is stable on the ~200–2000 hourly samples a month of
  trend data yields. Higher-order or binned fits are a later refinement, not a v1 requirement.
  The dataclass is deliberately coefficient-shaped so a future quadratic can extend it additively.
- **Guards mirror `chiller.py`'s existing ones** — drop non-running / trivial-load intervals, drop
  non-physical approach values — so the baseline is fit on the same population the efficiency rule
  already trusts.
- **Returns `None`, never a fabricated fit,** when the guards leave too few samples or the load
  range is too narrow to identify a slope. This follows the repo's honesty convention
  (`rules/base.py` module docstring): never assert a negative that wasn't tested.
- **`predict` duck-types `OnlineCusum`'s `predict` argument** so layer 4 needs no adapter.
- **`tons_from_flow` is exposed** because there is **no `Role` for chiller tons** in
  `camber/model/roles.py` — tons are derived (`gpm · ΔT / 24`) exactly as `chiller.py:83` does it.
  Callers with a metered tons point can pass it directly instead.

Nothing existing changes. `chiller_approach_fouling` keeps its median-vs-design behavior verbatim.

### Layer 2 — baseline/current period split (**interface change**) — follow-up

`Rule.analyze(self, equip, frame) -> Finding` is a `Protocol` with `@runtime_checkable`, and
`Registry.run` calls `rule.analyze(ref.equip, frame)` positionally
(`camber/rules/base.py:202`). The split must be **purely additive** so all ~40 existing rules keep
working untouched.

Proposed mechanism — an **optional second protocol**, not a change to `Rule`:

```python
@runtime_checkable
class PeriodRule(Protocol):
    """A diagnostic that compares a baseline window against a current window."""

    name: str
    roles_required: tuple
    roles_optional: tuple

    def analyze_periods(
        self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame
    ) -> Finding: ...
```

and a sibling runner `Registry.run_periods(rule_name, equip_refs, mapping, *, baseline, current, ...)`
where `baseline`/`current` are `(start, end)` timestamp pairs. `run_periods` resolves each equipment
once over the union span, slices the two windows, and dispatches to `analyze_periods`.

Backward-compatibility guarantees:

- `Rule`, `FleetRule`, `Finding`, and `Registry.run` are **unmodified**. No existing rule gains a
  required parameter; no existing call site changes signature.
- `PeriodRule` is a new name in `camber/rules/base.py` — an additive public-API entry, snapshot
  regenerated deliberately per `docs/API-STABILITY.md`.
- A `PeriodRule` may *also* implement `analyze` (degrading to whole-window behavior) so it can be
  registered in the ordinary registry and run by existing pipelines; `run_periods` is opt-in.
- `run_periods` reuses `_merge_shared`, `_missing_optional`, and the `min_trust` sensor-health gate
  verbatim — the honesty and trust conventions apply identically.

**DECIDED (decision 1): explicit `(start, end)` pairs.** `run_periods` takes caller-supplied bounds;
either endpoint may be `None` for an open-ended side. No rolling-window convention — a reference
window that moves on every re-run is neither auditable nor reproducible.

### Layer 3 — the drift statistic and its `Finding` — follow-up

```python
@dataclass
class ApproachDrift:
    n_current: int
    drift_f: float  # median residual of current vs baseline fit, at matched load
    drift_sigma: float  # drift_f / baseline.sigma_f
    slope_f_per_month: float  # trend of the residual within the current window
    pct_outside_2sigma: float
    extrapolated: bool  # >10% of current load fell outside the fitted envelope
    coverage_start: str
    coverage_end: str
```

`drift_f` is the **median** residual rather than the mean, so a handful of sensor dropouts or one
short spike cannot set the headline number — consistent with how the existing chiller rules
already reduce with medians. `slope_f_per_month` separates "stepped up and stayed" from "still
climbing", which is the distinction an operator actually acts on.

New rule `chiller_approach_drift`, registered alongside `chiller_approach_fouling` (which is *not*
retired — level and trajectory are different questions and a plant can fail either).

Severity, as a starting proposal to be tuned against real trend data:

| Condition | Severity |
|---|---|
| `drift_f ≥ 2.0` **and** `drift_sigma ≥ 3` | `fault` |
| `drift_f ≥ 1.0` **and** `drift_sigma ≥ 2` | `warn` |
| `extrapolated` or `n_current` below floor | `info` + caveat |
| otherwise | `ok` |

Both a °F floor and a σ floor are required so that a very tight baseline (small σ) doesn't fire on a
thermally meaningless 0.2 °F shift, and a noisy baseline doesn't mask a real 3 °F widening.

FDD integration is free: `FaultLifecycle.update` duck-types findings on `severity`/`equip`/`rule`
(`camber/faultlifecycle.py`), so a `chiller_approach_drift` finding gets a distinct fingerprint,
new/ongoing/resolved tracking, and SLA aging with **no change to `faultlifecycle.py`**.

### Layer 4 — `OnlineCusum` as the drift-alarm engine — follow-up

```python
from camber.mandv.online import OnlineCusum

cusum = OnlineCusum(baseline.predict, limit=..., slack=baseline.sigma_f * 0.5)
for ts, row in current.iterrows():
    state = cusum.update(row[tons_col], row[approach_col])
```

`OnlineCusum` computes `predicted − actual`, so for approach a **rising** approach shows up in the
`low` accumulator and reports `alarm == "waste"`. A thin wrapper (`ApproachDriftMonitor`) should
relabel the alarm to `"drift"`/`None` rather than reinterpret it at every call site — but
`online.py` itself needs **no modification**.

Setting `slack ≈ 0.5σ` and `limit ≈ 4–5σ` is the textbook tabular-CUSUM parameterization for
detecting a 1σ sustained shift; it should be validated against real drift traces before defaults
are baked in.

### Layer 5 — persisting fitted coefficients — follow-up

Neither `faultlifecycle.py` (findings) nor `camber/store/parquet_store.py` (long-format data) stores
model coefficients. A drift alert is meaningless if the baseline is refit from the current window on
every run.

Proposed: `camber/store/modelstore.py` — a JSON document store mirroring `FaultLifecycle`'s proven
shape (atomic `os.replace` write, `load`/`save` classmethods, records keyed by a stable fingerprint),
holding `{fingerprint: {kind, fitted_at, coverage, coefficients}}`. `ApproachBaseline.as_dict` /
`from_dict` (shipped in this PR) are the serialization contract.

JSON over parquet: coefficient sets are tens of rows, not millions; they need atomic replace and
human inspection more than they need columnar scans; and reusing `FaultLifecycle`'s pattern means no
new dependency and a reviewed precedent for the atomic-write path.

**DECIDED (decision 2): freeze at commissioning; refit only on an explicit "accept new normal".**
The baseline is fit once over the supplied baseline period, written with provenance, and thereafter
only read. `BaselineStore.freeze` **refuses to overwrite** an existing record; moving the reference
requires `accept_new_normal(...)`, which demands a non-empty `accepted_by` and `reason` and files
the superseded record in `history`. No scheduled or automatic refit exists, by construction — a
baseline refit from the window being judged would define away the drift it is meant to catch.

## File-by-file change list

| File | Phase | Change |
|---|---|---|
| `camber/chillerbaseline.py` | **1 (this PR)** | **New.** `ApproachBaseline`, `fit_approach_baseline`, `tons_from_flow`, `drift_stats`, `ApproachDrift` |
| `tests/test_chillerbaseline.py` | **1 (this PR)** | **New.** Fit correctness, residual σ, stable-vs-drifting discrimination, guard/`None` paths, serialization round-trip |
| `tests/public_api_snapshot.json` | **1 (this PR)** | Regenerated for the new module (`python tests/test_public_api.py --update`) |
| `docs/proposals/chiller_drift_detection_plan.md` | **1 (this PR)** | This document |
| `camber/rules/base.py` | 2 | **Add** `PeriodRule` protocol + `Registry.run_periods`. No edits to `Rule`/`Finding`/`run` |
| `camber/rules/chiller_drift_rule.py` | 3 | **New.** `ChillerApproachDrift(PeriodRule)` |
| `camber/rules/builtin.py` | 3 | Register `chiller_approach_drift` |
| `camber/scorecard.py` | 3 | Add the rule to the chiller category (`scorecard.py:32-34`) |
| `camber/charts/diagnostic.py` | 3 (optional) | `fitted_band(baseline, k=2)` constructor beside `band()`/`reset_line()`; a `chiller_approach` template with `x = tons` |
| `camber/mandv/online.py` | 4 | **No change.** Consumed as-is |
| `camber/chillerdrift.py` or `camber/rules/chiller_drift_rule.py` | 4 | `ApproachDriftMonitor` wrapper relabeling the `savings`/`waste` alarm |
| `camber/store/modelstore.py` | 5 | **New.** JSON coefficient store |
| `docs/FDD-DX.md`, `docs/CAPABILITIES.md`, `CHANGELOG.md` | 3–5 | Document the new rule and the period interface |

## Backward-compatibility guarantees

1. `chiller_approach_fouling` and `chiller_efficiency` keep their exact current behavior and output.
   No metric key is renamed, removed, or re-valued.
2. `Rule`, `FleetRule`, `Finding`, `Registry.run`, `Registry.run_fleet` keep their signatures.
   Every additional entry point is a **new name**.
3. New public names go through the deliberate snapshot regeneration that
   `tests/test_public_api.py` enforces — the API surface change is reviewed, not incidental.
4. Phase 1 (this PR) adds one leaf module that nothing imports yet: it cannot regress any existing
   behavior, which is precisely why it lands first.

## Test plan

Phase 1 (this PR):

- **Fit recovery** — synthesize `approach = a + b·tons + ε` with known `a`, `b`, `σ`; assert the fit
  recovers all three within tolerance.
- **Discrimination (the headline test)** — a stable chiller (approach flat at ~4 °F, varying load)
  and a drifting chiller (4 °F → 8 °F over the window at the *same* load profile) fit against a
  common baseline period must produce clearly separated drift statistics: the stable unit's
  `|drift_sigma|` stays small while the drifting unit's exceeds it by a wide margin.
- **Load confounding** — a chiller whose approach rises *only* because its load rose must yield a
  near-zero drift statistic. This is the test that justifies load-normalization existing at all; a
  naive median-vs-median comparison fails it.
- **Guards** — too few samples, degenerate load range, all-NaN, missing columns → `None`, not a fit.
- **Serialization** — `as_dict` → `from_dict` round-trips and predicts identically.
- **`tons_from_flow`** — matches `chiller.py`'s `gpm · ΔT / 24` convention.

Later phases: `run_periods` dispatch and window slicing; existing rules unaffected by the protocol
addition; severity-table boundaries; `FaultLifecycle` fingerprint distinctness between
`chiller_approach_fouling` and `chiller_approach_drift`; CUSUM alarm on a synthetic sustained shift
and no-alarm on noise; model-store atomic write and reload.

## Phased rollout

| Phase | Lands | Risk |
|---|---|---|
| **1** | `chillerbaseline.py` + tests + this plan | **None** — additive leaf module, no callers · **LANDED** (PR #2) |
| **2** | `PeriodRule` + `Registry.run_periods` (explicit period pairs) | Low — additive protocol · **LANDED** |
| **3** | `chiller_approach_drift` rule, scorecard category | Medium — first user-visible finding; thresholds still provisional · **LANDED** |
| **5** | `modelstore.py` coefficient persistence, frozen with `accept_new_normal` | Medium — new durable artifact · **LANDED** |
| **4** | `OnlineCusum` wiring for streaming drift alarms | Low — `online.py` unmodified · **LANDED** |

Phases 2, 3 and 5 landed together: with the two policy decisions made, the period split, the drift
Finding, and the frozen store are one coherent slice — a drift rule without persistence would refit
its own reference and report nothing, so shipping 3 without 5 would have been misleading rather than
merely incomplete. Phase 4 then landed on top, adding `camber/chillerdrift.py` and the
`chiller_approach_drift_sustained` rule: `OnlineCusum` wrapped around the same frozen baseline, with
outlier clipping and a presence-gated decision interval so a short burst cannot masquerade as a
sustained shift. `online.py` is unmodified, as designed.

**All five layers are now implemented.** The only thing outstanding is decision 3 (threshold
validation), which gates production use of the severities but not the machinery. A survey of the
gap review's remaining optional tiers is in
[`chiller_gap_optional_tiers_feasibility.md`](chiller_gap_optional_tiers_feasibility.md).

## Open decisions

**Decided.** (1) Period specification — explicit `(start, end)` pairs; see layer 2.
(2) Baseline refit policy — freeze at commissioning, move only via `accept_new_normal`; see layer 5.

Still open:

3. **Drift thresholds — OPEN, and the one thing blocking production use.** The °F and σ floors in
   `rules/chiller_drift_rule.py` (`DRIFT_WARN_F`, `DRIFT_FAULT_F`, `DRIFT_WARN_SIGMA`,
   `DRIFT_FAULT_SIGMA`) are engineering-judgement starting points, not measurements. They are
   labelled `PROVISIONAL` in source and every Finding carries `metrics["thresholds_provisional"]`.
   They need validation against real trend data with confirmed fouling events before anyone
   dispatches on the severities.
4. **Scope beyond approach.** The gap review's next tier — condenser-water range, cooling-tower
   approach-to-wet-bulb, head/condensing-pressure trend — is the *same* fit-baseline/compare-period
   machinery with different roles. Should layers 1–2 be generalized now (a role-agnostic
   `fit_baseline(y_role ~ x_role)`) or kept chiller-approach-specific until a second consumer
   actually exists? Recommendation: keep it specific; generalize when the second case arrives.
5. **Suction/discharge superheat is out of scope and cannot be added without new instrumentation.**
   `superheat` appears nowhere in the repository, and `camber/model/roles.py` has no suction or
   discharge temperature roles — only `COND_APPROACH_TEMP` and `EVAP_APPROACH_TEMP`
   (`roles.py:99-100`). Modeling superheat requires new `Role` members and a point-mapping story
   first. Confirm whether that instrumentation is available before it is planned.
