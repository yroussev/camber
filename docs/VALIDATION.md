# Validation & methods

How CAMBER's results are kept honest and checkable. The project's promise is
*defensible, citable* analytics, so every layer is validated against a public standard,
an independent implementation, or labeled ground truth — and uncertainty is reported,
not hidden.

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
