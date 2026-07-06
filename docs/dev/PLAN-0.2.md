# CAMBER 0.2 — development plan

Working plan for the **Next — 0.2** roadmap items. Built on the `0.2-dev` branch; **not pushed
upstream** until 0.2 is ready. Each item lists goal → design/API → **option flags** (a
cross-cutting goal for 0.2: every capability gets well-chosen knobs) → docs → tests → standards.

Cross-cutting for 0.2:
- **Option flags** — each capability exposes thresholds/modes/aggregation/output knobs with safe
  defaults, so it's tunable without forking. Flags are keyword-only, documented, and the rule
  wrappers surface the operationally-useful ones.
- **Documentation** — each capability gets a `docs/<CAP>.md` page (what/why, API, flags,
  worked example) and a README pointer; existing capability docstrings tightened as touched.
- **Tests** — synthetic-fixture proof per capability (the CAMBER contract), plus option-flag
  coverage.

Status legend: ☐ not started · ◐ in progress · ☑ done (on `0.2-dev`).

---

## 1. ASHRAE 62.1 OA-rate + DCV verification  ☑

**Goal.** Verify *delivered* outdoor air against the ASHRAE 62.1 Ventilation Rate Procedure
(VRP) requirement, and verify that Demand-Controlled Ventilation (DCV) actually modulates OA
with occupancy/CO₂. Complements the shipped CO₂-adequacy proxy (`camber.iaq`) and OA-fraction
diagnostic (`camber.oafraction`) with the explicit code-rate check.

**Design / API** — `camber/ventilation.py`:
- `OA_RATES_62_1` — `{space_type: (Rp cfm/person, Ra cfm/ft²)}` from 62.1 Table 6.1 (public
  standard values); `oa_rates_for(space_type)`.
- `required_oa_cfm(area_sqft, population, *, rp, ra, ez=1.0)` → `Voz = (Rp·Pz + Ra·Az)/Ez`.
- `assess_62_1(measured_oa_cfm, *, area_sqft, population, space_type|rp/ra, ez, ...)` →
  `VrpResult` (required, measured, ratio, status under/adequate/over, deficit).
- `assess_dcv(oa_signal, demand_signal, *, ...)` → `DcvResult` (corr, modulation range, status
  functioning/static/uncorrelated, optional CO₂-setpoint breach-at-min flag).
- Rules `camber/rules/ventilation_rule.py`: `DemandControlledVentilation` (config-free) and
  `VentilationRateProcedure` (design params at init). New `Role.OA_AIRFLOW`.

**Option flags.** `assess_62_1`: `space_type` vs explicit `rp`/`ra`, `ez`, `aggregate`
(median/mean/p05/min), `under_tol`, `over_factor`, `occupied_mask`. `assess_dcv`: `min_corr`,
`min_modulation`, `co2_setpoint`, `occupied_mask`.

**Docs:** `docs/VENTILATION.md`. **Tests:** `tests/test_ventilation.py` (VRP math, under/over,
DCV functioning vs static vs uncorrelated, flag effects). **Standards:** ASHRAE 62.1 VRP.

---

## 2. ASHRAE 223P + richer Brick interop  ☑

**Goal.** Go beyond the shipped point/site Brick round-trip toward ASHRAE 223P semantic
modeling and fuller Brick class coverage, so CAMBER consumes/produces models other tools share.

**Design / API** — extend `camber/interop/`:
- `interop/semantic223.py` — map CAMBER `Role`/equip classes ↔ a 223P-shaped RDF subset
  (connections, medium, points), lazy `rdflib` (the existing `[brick]`/rdf path).
- Richer Brick: broaden the role↔Brick map (more equipment classes, point quantities/units),
  and import equipment hierarchy + relationships (`feeds`, `hasPart`, `isPointOf`).

**Option flags.** `to_223(...)`/`from_223(...)`: `profile` (minimal/full), `include_relations`,
`backend` (builtin/rdflib), `strict` (error vs skip on unmapped). Brick import: `infer_equip`,
`unit_normalize`.

**Docs:** extend `docs/ECOSYSTEM.md` + a new `docs/ONTOLOGY.md`. **Tests:** round-trip a
multi-equip site through 223P + Brick; unmapped-token handling. **Standards:** ASHRAE 223P, Brick.

---

## 3. Continuous benchmarking in CI  ☑

**Goal.** Run the rule library against LBNL's labeled public datasets on every change and gate
on accuracy regressions (TPR/FPR/correct-diagnosis), so detector quality can't silently drift.

**Design / API.**
- `examples/lbnl_fdd/benchmark.py` already scores three equipment families; add a
  `--json out.json` and a `--gate thresholds.json` mode (`camber.eval` helper
  `check_against_baseline(metrics, baseline, tol)`).
- `.github/workflows/benchmark.yml` — scheduled + on-PR job: fetch the CC-BY datasets (cached),
  run the benchmark, compare to a committed `benchmark-baseline.json`, fail on regression beyond
  tolerance, and upload the metrics artifact.

**Option flags.** runner: `--families`, `--json`, `--gate`, `--tol`, `--update-baseline`.
`check_against_baseline(..., tol=, metrics=, direction=)`.

**Docs:** extend `docs/VALIDATION.md` (the continuous-benchmark section). **Tests:**
`check_against_baseline` pass/fail/tolerance; a tiny synthetic baseline round-trip. **Standards:**
the existing LBNL benchmark methodology.

---

## 4. Outbound integrations  ☑

**Goal.** Push findings outward: CMMS/work-order write-back, alerting channels, and BI/warehouse
export — all opt-in, all from the *findings* layer (never writing to the BAS/OT).

**Design / API** — extend `camber/integrate/`:
- `integrate/notify.py` — notifier protocol + a stdlib **webhook** notifier (urllib, JSON POST),
  a **Slack/Teams** incoming-webhook formatter, and an **email** (smtplib) notifier. Severity
  filter + dedupe via the existing fault fingerprint.
- `integrate/cmms.py` — render findings to a CMMS work-order record (generic JSON schema) and a
  pluggable `submit` callable (the user wires their CMMS); idempotency via fingerprint.
- `integrate/export.py` — findings/metrics → tabular export (CSV/Parquet/JSON) for BI/warehouse.

**Option flags.** notifiers: `min_severity`, `dedupe`, `template`, `timeout`, `dry_run`.
export: `format`, `columns`, `flatten_metrics`. cmms: `priority_map`, `idempotent`.

**Docs:** `docs/INTEGRATIONS.md`. **Tests:** webhook POST via injected transport (no network),
Slack/Teams payload shape, email via a fake SMTP, severity filter + dedupe, export shapes.
**Standards:** n/a (interop); keep read-only toward OT.

---

## 5. Interactive visualization MVP (A → B → E → I)  ☑

**Goal.** The fuse-graphing-and-diagnostics MVP slice, within the dependency-light constraint
(no web framework): static-but-rich, self-contained **HTML** built from matplotlib + stdlib, with
linked chart-state. A = ingest-readiness ribbon, B = fault-annotated synchronized multi-trend,
E = carpet (already have the primitive), I = data-quality dashboard.

**Design / API** — extend `camber/charts/` + a new `camber/report/dashboard.py`:
- `charts/readiness.py` (A) — before/after resampling-readiness ribbon (coverage, gaps,
  corrections) from `ingest.quality`.
- `charts/multitrend.py` (B) — synchronized multi-trend with **fault-violation spans shaded**
  from Findings (every fault renders its evidence).
- `charts/quality_dashboard.py` (I) — coverage %, gap map, frozen/out-of-range flags, readiness
  score.
- `report/dashboard.py` — assemble A/B/E/I + the finding list into one self-contained HTML
  (matplotlib figures inlined as base64; no JS framework, optional tiny vanilla-JS for linking).

**Option flags.** `build_dashboard(...)`: `sections` (subset of A/B/E/I), `rank_by`
(severity/cost), `top_n`, `theme`, `embed` (inline vs linked assets), `annotate_faults`.
per-chart: `shade`, `min_max_avg` triples, `cmap`.

**Docs:** `docs/VISUALIZATION.md`. **Tests:** each chart returns a populated Axes (Agg);
dashboard HTML contains the expected sections + finding rows; option subsets honored.
**Standards:** clean-room distillation (see ROADMAP Visualizations).

---

## Build order

1 (ventilation) → 3 (CI benchmark) → 4 (outbound) → 5 (viz MVP) → 2 (223P). Rationale: 1/3/4 are
self-contained and high-leverage; 5 is the largest; 2 needs the most ontology research. Each
lands fully (code + flags + docs + tests, suite green) before the next.
