# CAMBER 0.3 — development plan

Working plan for **0.3**. Built on the `0.3-dev` branch (off `main` == `v0.2.0`); **not pushed
upstream** until 0.3 is ready. 0.3 is a deliberately **focused, dependency-light** release: it
deepens the visualization differentiator — *charts and faults are the same artifact* — into a full
**rules-as-a-chart-engine**, and folds in packaging/community polish. Heavier directions (grounded
agentic query, ML-assisted point mapping) are **deferred to 0.4** to keep 0.3 inside the
dependency-light contract.

Plan source: `ROADMAP.md` (Visualizations pattern catalog + Phase 0 / packaging leftovers). The 0.2
plan (`docs/dev/PLAN-0.2.md`) is the delivered predecessor; the 0.2 MVP shipped patterns **A → B →
E → I** (readiness ribbon, fault-annotated multi-trend, carpet, data-quality dashboard). 0.3 builds
the next tranche of the documented build order **A→B→E→D→I→G→C→J→H→F**.

Cross-cutting for 0.3:
- **Dependency-light, no web framework.** Charts stay matplotlib + stdlib; interactivity is
  **vanilla JS inlined** into the existing self-contained HTML (the 0.2 dashboard convention) — no
  React/D3/bundler, no CDN, CSP-safe.
- **Every rule renders its own evidence.** The keystone: a Finding carries the trend that proves it,
  with violating spans shaded — the chart *is* the audit evidence and the report figure.
- **Option flags** — each chart/capability exposes knobs (shade, min/max/avg triples, cmap,
  classification thresholds) with safe defaults, keyword-only.
- **Documentation & tests** — each capability gets a `docs/VISUALIZATION.md` section and
  synthetic-fixture tests (a populated Axes under the Agg backend; dashboard HTML contains the
  expected sections + annotations).

Status legend: ☐ not started · ◐ in progress · ☑ done.

---

## 1. Pattern D — OAT cloud-shape scatter + brush-back  ☑

**Goal.** Generalize the shipped energy-signature scatter into a reusable **X-Y-vs-OAT "cloud
shape"** primitive with automatic shape classification and change-point detection, plus
**brush-back** (select a cloud region → the timestamps behind it) — revealing control behavior a
time-series buries.

**Design / API** — `camber/charts/oat_scatter.py`:
- `oat_scatter(series, oat, *, ax=None, classify=True, changepoint="auto", cmap=None)` — scatter of
  any point (energy, airflow, valve %) against OAT; overlays a fitted change-point model (reuse
  `mandv.models`) + balance point(s); returns the Axes and a `CloudShape` (classification + fitted
  breakpoints).
- `classify_shape(series, oat)` — dependency-light classifier (slope/curvature + residual spread vs
  the change-point fit): linear / V / hockey-stick / scattered.
- Brush-back metadata: emit the point→timestamp index so the linking layer (item 5) maps a selected
  region back to time.

**Option flags.** `classify` (on/off), `changepoint` (model kind or off), `cmap`, `by`
(color by season/occupancy), `min_max_avg`.

**Docs:** extend `docs/VISUALIZATION.md` (pattern D). **Tests:** classifier labels a synthetic
V-shape vs linear vs scatter; change-point overlay present; brush-back index maps region →
timestamps. **Standards:** clean-room distillation (ROADMAP Visualizations D).

## 2. Pattern G — templated subsystem diagnostic scatters  ☑

**Goal.** A general framework for **templated diagnostic scatters**: each subsystem has an expected
signature (economizer, SAT/HW/CHW reset, valve/damper travel, no simultaneous heat-cool); plot the
measured behavior with the **expected template overlaid** and **violations shaded**, so each scatter
doubles as rule evidence and a report figure.

**Design / API** — `camber/charts/diagnostic.py`:
- `diagnostic_scatter(frame, template, *, ax=None, shade=True)` — a `DiagnosticTemplate`
  (x-role, y-role, expected-region function/bounds, label) rendered with in-region vs violating
  points distinguished; returns the Axes + the violating mask.
- A packaged `TEMPLATES` set for the common subsystems (economizer OA-fraction vs OAT, reset
  schedules vs OAT/load, valve travel vs demand), each citing the diagnostic it encodes.

**Option flags.** `shade`, `template` (packaged name or custom), `tolerance`, `cmap`.

**Docs:** `docs/VISUALIZATION.md` (pattern G). **Tests:** each packaged template flags out-of-region
points on a synthetic frame; a custom template is accepted. **Standards:** ASHRAE G36 / PNNL
Re-tuning signatures (clean-room).

## 3. Pattern J — rules as a chart engine (keystone)  ☑

**Goal.** Turn the rule library into a **chart engine**: every rule that can mark its violating
timestamps **emits its own evidence chart** (dispatching to the B/D/E/G renderers), so a Finding
*carries* the trend that proves it — the chart is the audit evidence and the report figure. This is
the 0.3 differentiator; `charts/multitrend` already documents the primitive (a rule's violating
mask → shaded spans), and this formalizes rule → evidence.

**Design / API** — extend the rule protocol + `camber/charts/evidence.py`:
- Optional rule hook `evidence(equip, frame) -> Evidence` (duck-typed, back-compatible): an
  `Evidence` names the **renderer** (`multitrend` / `oat_scatter` / `carpet` / `diagnostic`), the
  **series/roles** to plot, and the **violating mask** (reusing `multitrend.mask_to_spans`). Rules
  without the hook are unaffected.
- `render_evidence(finding, frame, *, ax=None)` dispatches to the named renderer; `Finding` gains an
  optional `evidence` payload kept JSON-friendly (the mask/roles, not the figure).
- Wire into `report/dashboard.py` and the Std-211 audit: each actionable finding renders its
  evidence chart inline, ordered by impact (the existing `rank_by`).
- Backfill `evidence()` on a first tranche of high-value rules (simultaneous H/C, reheat, economizer,
  SAT reset, chiller kW/ton).

**Option flags.** `build_dashboard(evidence=on/off, top_n, rank_by)`;
`render_evidence(shade, normalize)`.

**Docs:** `docs/VISUALIZATION.md` (pattern J) + an FDD-docs note (findings carry evidence).
**Tests:** a rule with an `evidence()` hook produces a populated Axes with violating spans shaded;
the dashboard embeds one evidence chart per actionable finding; rules without the hook still run.
**Standards:** clean-room (ROADMAP Visualizations J).

## 4. Pattern C — peer/cohort comparison + cohort-deviation rule  ☑

**Goal.** Concurrent **peer comparison** as small multiples with statistical outlier ranking (one
unit looks fine until you compare it to its siblings), and — enabled by it — a **cohort-deviation
rule** ("this VAV runs unlike its 40 peers").

**Design / API** — `camber/charts/cohort.py` + `camber/rules/cohort.py`:
- `cohort_small_multiples(frames, role, *, ax=None, rank="deviation")` — a small-multiples grid of a
  role across a cohort, ordered by deviation from the cohort norm (robust z of a per-unit summary vs
  the cohort).
- `CohortDeviation` rule — flags a unit whose behavior (a chosen role's profile/summary) deviates
  > k robust-σ from its cohort; emits a Finding + (via item 3) its cohort evidence chart. Read-only,
  synthetic-fixture proven.

**Option flags.** `rank`, `k`, `summary` (mean / profile / load-shape), `min_cohort`.

**Docs:** `docs/VISUALIZATION.md` (pattern C) + FDD docs (cohort rule). **Tests:** small-multiples
ordered by deviation; the rule flags an injected outlier unit and clears a uniform cohort.
**Standards:** clean-room (ROADMAP Visualizations C).

## 5. Interactive linking (vanilla JS)  ☑

**Goal.** Make the self-contained dashboard **linked**: brushing a sub-cloud in a scatter filters
the linked time-series / carpet / calendar to the same points; selection propagates across every
view — **without a web framework**.

**Design / API** — extend `camber/report/dashboard.py`:
- Emit the brush-back point→timestamp indices (from items 1/2) as inline JSON, plus a small
  **vanilla-JS** module (inlined, CSP-safe, no CDN) that wires selection across the embedded
  SVG/figures. Static fallback when JS is off (the 0.2 behavior).
- `build_dashboard(..., interactive=True)` opt-in; embeds stay self-contained (data-URI / inline).

**Option flags.** `interactive` (on/off), `link` (which views), `embed` (inline only).

**Docs:** `docs/VISUALIZATION.md` (interactive linking + the no-framework rationale). **Tests:** the
HTML contains the linking payload + script and the expected element ids; `interactive=False`
reproduces the static dashboard byte-for-byte on the shared parts. **Standards:** clean-room;
dependency-light contract (no framework / CDN).

## 6. Packaging & community polish  ◐

Independent of the viz track; lands in parallel.

- **conda-forge feedstock** — fill the shipped `deploy/conda/meta.yaml` skeleton (`url` / `sha256`
  from the 0.2.0 PyPI sdist) and submit via `staged-recipes`; document the feedstock-maintainer flow
  in `docs/DEPLOY.md`.
- **Docs site (MkDocs)** — publish README / ARCHITECTURE / CAPABILITIES / the `docs/` pages as a
  small MkDocs site (GitHub Pages) behind a docs-only dev extra; no change to the dependency-light
  runtime.
- **Community** — enable GitHub Discussions; confirm the issue/PR templates surface; add repo topics
  + description; track the PEP-541 `camber` name request (distribution stays `camber-toolkit`).
- **Roadmap refresh** — re-baseline `ROADMAP.md`: mark the 0.2-delivered items (223P / 62.1 /
  CI-benchmark / outbound / viz-MVP, plus streaming / fault-lifecycle / plugin API / forecasting /
  GEB / hourly carbon / deploy manifests), and reframe "Next" as 0.3 (viz depth) with agentic query
  + ML-assisted mapping explicitly **0.4**.

**Docs:** `docs/DEPLOY.md` (conda-forge), the MkDocs config. **Tests:** n/a (CI builds the docs
site; the release workflow already covers the conda deps). **Standards:** n/a.

---

**Build order.** 1 (D) → 2 (G) → 3 (J, keystone) → 4 (C) → 5 (linking); **6 (packaging) in
parallel**. Rationale: D and G are the missing *renderers* that J dispatches to; J is the keystone
(rules → evidence charts); C adds the cohort dimension + a new rule; linking is the interactive
layer over all of them; packaging is independent. Each viz item lands fully (chart + flags + docs +
synthetic tests, suite green) before the next.

**Deferred to 0.4** (kept here so they're not lost): **grounded agentic query & explanation** (NL
over the model + history, plain-language fault explanations strictly grounded/cited from the
deterministic core — behind an optional, provider-agnostic LLM seam consistent with the existing
transport/bridge pattern, wired to a hosted LLM API of the operator's choice) and **ML-assisted
point mapping / auto-tagging** (on top of the deterministic mapper + `mapping_confidence`). Both introduce
heavier dependencies and are held until the viz differentiator lands.

## Delivered beyond the plan (0.3.0)

0.3 grew well past the 6 planned items. Also shipped:

- **Visualization** — patterns **H** (M&V savings + G14 uncertainty) and **F** (load profiles /
  load-duration curves), completing the catalog A–J; pattern **J** broadened (more evidence hooks +
  Std-211 audit wiring).
- **Advisory layer** — `aso` (recommendations), `actionplan` (findings + $ + recommendation, wired
  into the audit report and config runs), `scorecard` (category scores + A–F grade).
- **FDD rules** — `control_hunting`, `unmet_setpoint_hours`, `supply_air_control`,
  `airflow_tracking`, and shipped cohort-deviation instances.
- **M&V** — `mandv.degreeday` (variable-base degree-day baseline) and `mandv.option_a` (IPMVP
  Option A).
- **Analytics** — `schedule` (schedule inference), `changedetect` (level shifts in time),
  `freecooling` (economizer opportunity $), `disaggregate` (baseload/weather/other).
- **Standards** — `interop.openadr` (DR → OpenADR report schema).
- **Time / DST** — `timegrid` (interval width, de-duplication, tz-localize, DST anomalies), wired
  into `io.load_csv` and `ingest.quality`.
- **Infra** — hardened `release.yml`; a multi-agent code-review pass fixed a batch of correctness
  bugs (`tests/test_review_fixes.py`).

Remaining packaging/community items (item 6) that need the repo owner: conda-forge feedstock
submission, GitHub Discussions, the PEP-541 `camber` name, and enabling Pages for the MkDocs site.
