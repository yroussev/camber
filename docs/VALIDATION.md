# Validation & methods

How CAMBER's results are kept honest and checkable. The project's promise is
*defensible, citable* analytics, so every layer is validated against a public standard,
an independent implementation, or labeled ground truth — and uncertainty is reported,
not hidden.

```mermaid
flowchart LR
    synth["synthetic faults (camber.faultlab)"] --> ev["camber.eval (confusion, TPR/FPR)"]
    lbnl["real LBNL FDD data"] --> ev
    bdg2["real BDG2 meters (G14 acceptance)"] --> mv["M&V acceptance rate"]
    ev --> ci["metrics_with_ci (Wilson CI)"]
    mv --> ci
    ci --> gate["check_against_baseline (CI gate)"]
    gate --> report["published accuracy"]
```

*Synthetic and real (LBNL, BDG2) benchmarks flow through the eval framework into CI-reported accuracy with confidence intervals and a baseline gate.*

## Principles

- **Clean-room & citable.** Every method cites a public standard (ASHRAE G36/G14/Std-55/
  Std-211, IPMVP, PNNL Building Re-tuning, NIST APAR, CalTRACK); no proprietary code or
  text. Each rule ships a synthetic fixture that proves detection.
- **Honest results.** Report uncertainty and limitations; never overstate a fit or a saving.
- **Reproducible.** Deterministic synthetic fixtures; `camber.validation.check_determinism`
  asserts a function returns identical output across runs; CI runs the suite on Python
  3.10 and 3.11.

## FDD accuracy — labeled public datasets

`camber.eval` implements the LBNL FDD performance-evaluation framework (confusion matrix,
TPR/FPR/accuracy, correct-diagnosis rate). `examples/lbnl_fdd/benchmark.py` scores the
detector suite across **three LBNL equipment families** (single-duct AHU, fan-coil unit,
dual-duct AHU — CC-BY labeled data) and now reports each rate with a **95% Wilson score
confidence interval** (`camber.validation.metrics_with_ci`), because per-family samples
are small and a bare percentage would overstate certainty.

Representative result (OA-fraction detector vs stuck dampers):

| Set | TPR (95% CI) | FPR | n |
|---|---|---:|---:|
| SDAHU | 100% [51–100%] | 0% | 6 |
| FCU | 100% [44–100%] | 0% | 4 |
| DDAHU | 50% [9–91%] | 100% | 3 |
| **Pooled** | **89% [56–98%]** | 25% | 13 |

The honest read the CIs force: OA-fraction transfers cleanly to single-duct AHUs and
FCUs but **degrades on dual-duct AHUs** (mixing-box + mild-weather OAF noise), and the
modulating-valve **leak detector under-fires** — gaps the benchmark *measures* rather
than hides. The pooled interval is the defensible headline; the small-n per-family
numbers are reported with their uncertainty.

### Drift-family real-data validation (which detectors the data can honestly test)

The newer **drift** and **Trim-and-Respond reset** families are only partly validatable on the
public datasets we have, and `examples/lbnl_fdd/benchmark.py` now scores exactly what the LBNL SDAHU
data can support (via `camber.driftvalidation.evaluate`: baseline = fault-free run, current = a
labeled fault run):

| Detector | On LBNL SDAHU | Why |
|---|---|---|
| `coil_valve_drift` | **real TPR** (target = coil-valve leak) | all required points mapped; the clean case |
| `economizer_damper_drift` | **real TPR, with a caveat** (target = stuck damper) | fires cleanly only if `OA_DMPR` is the *command* — if it's the stuck actual position, it reduces to the level check `outdoor_air_fraction` already covers |
| `duct_static_drift` | **specificity only** | no labeled duct-static fault in the fetched set → false-positive rate only |
| `fan_efficiency_drift`, `filter_loading_drift` | **synthetic-only** | need `POWER` / `FILTER_DIFF_PRESS` points the SDAHU simulation does not export |
| chiller / pump / VAV drift | **synthetic-only here** | LBNL SDAHU is air-side; see the sibling datasets below |
| `*_reset_effectiveness` | **synthetic-only** | needs the per-cycle reset-**request** point, absent from LBNL |
| `*_rogue_zone_census`, `*_cohort_starvation` | **synthetic-only (a real gap)** | need a *fleet of zones with per-zone requests*; a single simulated AHU is not a zone fleet, and no vendorable public multi-zone-fleet fault dataset exists |

These are honest boundaries, not oversights: where the data can't support a real-fault score, the
family is validated on the synthetic whole-suite harness below (`camber.faultlab`) and said so here.

**BDG2 is meter-level** — whole-building energy + weather with no component point trends — so it
validates the **M&V / forecast / anomaly** track (below), *not* component FDD; the drift/reset
detectors' required roles (approach, valve %, damper, requests) simply don't exist in it.

**Datasets that would close the gaps.** The genuinely usable (commercially-licensed) path is the
CC-BY LBNL FDD **sibling subsets** — chiller-plant (the open, *simulated* chiller FDD source, which
sidesteps a licence-encumbered ASHRAE chiller-FDD dataset), VAV fan-power-unit (single-box VAV faults
incl. the reheat valve), and RTU/DDAHU/FCU — same schema as the SDAHU slice already wired.

The tempting real-BMS AHU/VAV sets are, on inspection, **not usable for a commercial toolkit's
committed benchmark**: the only publicly-downloadable version of the widely-cited Korean large-office
AHU set is a reduced sample (all-faulted, two coarse labels, no supply-air setpoint, stacked AHUs —
no fault-free baseline to score against), and the richer modern real-labeled AHU/VAV datasets
(multi-building office/auditorium/hospital; the RBC/G36 collection with fault-free baselines; the ORNL
multi-zone VAV fleet) are all **CC-BY-NC-ND** — research-only, not vendorable. So a commercially-usable
*labeled multi-zone VAV fleet* remains the one unfilled gap, and the G36 **reset-request** signal
(also only in NC-ND G36 simulations) would have to be **generated** from the open Modelica Buildings
G36 sequences rather than downloaded.

## FDD accuracy — synthetic whole-suite harness

LBNL's public data labels only a handful of AHU fault modes, so it can accuracy-score only a few
detectors. To measure the **rest** of the suite, `camber.faultlab` injects each rule's target fault
into a role-frame (a labeled positive) and a matching fault-free frame (a negative), and
`examples/synthetic_fdd/benchmark.py` scores the whole registry with the same `camber.eval` framework —
deterministically, with no download, gated in CI against a committed baseline
(`tests/test_faultlab.py`).

Current coverage (0.6): **all 33 single-equipment rules** are accuracy-scored (100% TPR / 0% FPR on
their injected faults) — the fixture-only list is now empty; the 5 fleet rules are scored separately. A
companion harness scores the **G36 FC1–FC15 engine** over 6 representative fault conditions. The runner
prints a scored-vs-fixture coverage table so the credibility story is explicit rather than implied. This
complements — does not replace — the real-data LBNL benchmark above (external validity on real equipment),
which 0.6 broadened with a **cooling-coil-valve leakage severity sweep** (010–100%) to characterize the
leak detector that the pooled result showed under-firing.

## M&V accuracy — real-data acceptance on BDG2

The M&V analogue of the LBNL FDD accuracy benchmark: `examples/bdg2/benchmark.py` scores the **ASHRAE
Guideline 14 baseline-model acceptance rate** on **real** whole-building meters (Building Data Genome 2,
CC-BY, ~2,000 meters). For each building it fits the daily change-point inverse model of energy vs
outdoor temperature and asks whether the fit meets the G14 gate (CV(RMSE) ≤ 30% daily); the headline is
the fraction that pass, with a Wilson CI. Committed baseline, gated in the benchmark CI job.

Representative result (2016, ~2,044 buildings):

| Meter | Acceptance (95% CI) | Median CV(RMSE) | n |
|---|---|---:|---:|
| Chilled water (cooling) | 36% [32–40%] | 32% | 518 |
| Electricity | 8% [7–10%] | 21% | 1,526 |
| **Pooled** | **15% [14–17%]** | 24% | 2,044 |

The honest read: weather-driven **chilled-water** energy is baseline-able at a **meaningfully higher**
rate than schedule/plug-driven **electricity** (~4.5×) — CAMBER reproduces the expected physics — but
real whole-building energy is messy, and half the chilled-water buildings sit near the 30% daily
CV(RMSE) line. Reporting *both* meter types (not just the flattering one) with confidence intervals is
the point. The runner also rolls the portfolio up by EUI at real scale (validating the fleet percentile
path on a real distribution).

## Cross-validation vs an independent implementation

The ASHRAE G36 fault-condition equations (FC1–FC15) are cross-validated against the
open-source **open-fdd** project — they agree to 0.00 pts on every shared, runnable fault
condition (one ≤2.3-pt mixed-air-bounds edge case). Details and the operating-state vs
single-signal gating convention are in [ECOSYSTEM.md](ECOSYSTEM.md).

## M&V

Change-point / TOWT models report ASHRAE Guideline 14 fit statistics (CV(RMSE), NMBE) and
**fractional savings uncertainty** with every saving. The CalTRACK alignment and an
**eemeter cross-check recipe** (no dependency added) are documented in [MANDV.md](MANDV.md).

## Tariffs & finance

The native tariff engine is cross-checkable against **NREL PySAM `UtilityRate5`** (the
optional `[tariff]` extra) for full URDB fidelity, and `validate_bill` reconciles a
recomputed bill against actual invoices. The ECM finance metrics (NPV/IRR/SIR) are the
textbook definitions; IRR is a bisection solver verified against hand-worked cases in
`tests/test_finance.py`.

## Uncertainty & reproducibility toolkit (`camber.validation`)

- `wilson_interval(k, n)` / `rate_ci` — binomial confidence intervals for any rate.
- `metrics_with_ci(confusion)` — TPR/FPR/accuracy each with a Wilson CI.
- `check_determinism(fn, ...)` — reproducibility guard (identical output across runs).

## Robustness / adversarial hardening (pre-1.0)

A dependency-light stress pass (seeded generators + parametrize, no `hypothesis`) exercises the core
entry points on degenerate/adversarial input, and each real bug it found is fixed and regression-locked:

- **`io.load_csv`** — empty / header-only / unparseable-timestamp / text-in-numeric CSVs now raise a
  clear error or coerce cleanly (a stray text cell no longer silently poisons a column to `object`).
- **Every registered rule** on empty / 1-row / all-NaN / all-equal / duplicate-index frames returns a
  `Finding` and never raises (a 191-case parametrized sweep) — two plant rules were hardened.
- **M&V calibration** degrades to `accept=False` (never a `ValueError`) on thin/degenerate energy.
- **Fleet rollup** percentile is O(N log N) (was O(N²)); scale-tested to N=500.
- **Mapping** rejects catastrophic-backtracking (ReDoS) regex patterns at config load.
- **Determinism sweep** — `check_determinism` now nets `calibrate` / `best_model` / `detect_level_shifts`
  / cohort / `faultlab`, not just two spots.
- **Analytics entry points (0.9.6)** — `forecast` / `disaggregate` / `tariff` reject a non-timestamp
  index up front (they used to coerce a numeric index into nanosecond dates and return a plausible
  wrong answer); `EnergyPrice` rejects a negative/NaN rate; `build_scorecard` rejects `None`. Empty
  input stays graceful (empty in → empty out).
- **Untrusted parsers (0.9.6)** — the hand-rolled Brick reader and the rdflib/Haystack/223P paths now
  degrade on malformed input to a clear `ValueError` (or a partial result), never a raw `IndexError` /
  rdflib `BadSyntax` / `AssertionError`: a triple missing its terminator, a predicate list with no
  object, a literal containing the split characters, and a malformed tag set are all fuzzed and locked.
- **Tariff billing (0.9.6)** — a malformed rate structure (empty `energy_rates`, or a schedule naming a
  period with no rate) raises a clear error naming the period, instead of an `IndexError` mid-bill.

## Continuous benchmarking in CI

Accuracy is gated against a committed baseline so it can't silently drift. The cross-equipment
runner emits a flat metrics dict and compares it to a baseline:

```sh
# seed a baseline once (after a known-good run), commit it:
python examples/lbnl_fdd/benchmark.py --update-baseline examples/lbnl_fdd/benchmark-baseline.json
# thereafter, gate (CI fails on a regression beyond tolerance):
python examples/lbnl_fdd/benchmark.py --gate examples/lbnl_fdd/benchmark-baseline.json --tol 0.05
```

`camber.eval.check_against_baseline(current, baseline, *, tol, metrics)` is the reusable gate:
a higher-is-better metric (TPR, accuracy, correct-diagnosis) **regresses** when it falls past
`tol`; a lower-is-better one (FPR, error) regresses when it **rises** past `tol`; a baseline
metric missing from the current run fails too (a detector was removed/renamed). It returns a
`BaselineCheck` (`passed`, `regressions`, `improvements`, `unchanged`, `missing`).

`.github/workflows/benchmark.yml` runs this weekly, on demand, and on PRs that touch the rules
or the benchmark — fetching the CC-BY datasets (cached), gating against the committed baseline
(or seeding one if absent), and uploading the metrics artifact.

> Remaining: capture a packaged "published accuracy" run as a versioned release artifact.
