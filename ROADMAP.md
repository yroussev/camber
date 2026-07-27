# CAMBER roadmap

This roadmap is organized by the building-analytics capability layers CAMBER is
built on: **ingest → semantic model → computation → FDD → M&V → storage →
reporting → integration → orchestration**. It is intentionally honest about what
exists today versus what is planned; dates are deliberately omitted in favor of
ordered phases. Contributions toward any item are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Released — v0.1.0

CAMBER 0.1.0 is **public and installable**: PyPI **`camber-toolkit`** (imports as `camber`),
a multi-arch container image at **`ghcr.io/yroussev/camber`**, and a GitHub release. **~634
tests**, CI on Python 3.10/3.11, runnable examples on public CC-BY datasets (LBNL FDD,
Building Data Genome 2).

0.1.0 shipped well beyond a minimal first cut — **most of the originally-planned Phase-1 and
Phase-2 work, and several capability tracks, landed in it** (kept below, marked `[x]`, as the
delivered record). What it includes, by layer:

- **Ingest** — per-point CSV, wide CSV, Project-Haystack `hisRead` (via an injectable
  transport seam + a phable hookup), SQL/historian (any DB-API), and **read-only network
  adapters: Modbus, MQTT/Sparkplug, BACnet (incl. experimental BACnet/SC), and OPC-UA** —
  read-only by construction, lazy-imported, historian-first posture
  ([SECURITY](docs/SECURITY.md), [INGEST-PROTOCOLS](docs/INGEST-PROTOCOLS.md)). Plus
  data-quality scoring + auditable cleaning, unit normalization, and a **sensor-health /
  data-trust gate** (physical bounds, cross-sensor consistency, drift-vs-reference, mapping
  confidence).
- **Semantic model** — vendor-neutral `Role` vocabulary, mapping provider, entity model +
  completeness validation; **Brick import/export and whole-site round-trip**.
- **FDD** — ASHRAE G36 + PNNL Re-tuning diagnostics; an **11-rule central-plant & hydronic
  library**; a **SOO conformance engine** + packaged G36 clause library; impact
  prioritization, **root-cause grouping**, fault-lifecycle tracking, **per-fault dollar
  economics**, and an accuracy benchmark across three LBNL equipment families with Wilson-CI
  methods validation.
- **M&V** — change-point (2P–5P + zero variants), LBNL TOWT, G14 fit stats + fractional
  savings uncertainty, CUSUM, weather normalization, **normalized annual savings**,
  **non-routine adjustment**, and **IPMVP Option-B retrofit isolation**; CalTRACK alignment +
  eemeter / LBNL-BETTER cross-checks.
- **Commissioning** — RCx / MBCx (`functional_test`, `before_after`, `track_measures`).
- **Money & compliance** — tariff engine + OpenEI URDB + bill validation + ECM NPV/IRR/SIR;
  demand & peak analytics; BPS / EUI compliance.
- **Domain analytics** — Std-55 comfort, IAQ/CO₂ ventilation, cost, carbon, water, load
  profiling, PV (+ pvlib bridge), psychrometrics (+ PsychroLib bridge), lighting.
- **Storage** — partitioned Parquet store with rollups/retention, **year-partition pruning,
  column projection, and a cached catalog**, validated to portfolio scale ([SCALE](docs/SCALE.md)).
- **Reporting / integration / API** — Std-211 audit with prioritized findings, a **portfolio
  rollup ranked by recoverable dollars**, findings→CMMS + notifier, a read-only HTTP API, and
  **viz charts** (load carpet, CUSUM, energy signature).
- **Config-driven runs** — a declarative JSON config runs a whole analysis without a script.
- **Distribution** — slim multi-stage Docker image + compose bundle, `.devcontainer`, a
  tag-driven release workflow (PyPI Trusted Publishing + GHCR), CI.

## Phase 0 — Launch — DONE ✅

- [x] Public GitHub repository (`yroussev/camber`).
- [x] **0.1.0 on PyPI** as `camber-toolkit` via Trusted Publishing (OIDC, no stored token),
      a multi-arch GHCR image, and a GitHub release — all from the tag-driven workflow.
- [ ] Enable Discussions; confirm issue/PR templates surface; add repo topics/description.
- [ ] (Optional) Publish README/ARCHITECTURE as a small docs site (MkDocs).
- [ ] (Tracking) Reclaim the bare `camber` PyPI name (PEP-541 request filed) — optional;
      `camber-toolkit` is the permanent distribution name regardless.

## Delivered — v0.2.0

All five near-term 0.2 items shipped, plus a streaming/grid/carbon analytics tranche and several
platform items pulled forward from *Later — toward 1.0* / *Horizon*.

- [x] **ASHRAE 223P + richer Brick interop** — `interop.semantic223` (site↔223P RDF subset,
      minimal/full profiles) + a broadened role↔Brick map.
- [x] **ASHRAE 62.1 OA-rate + DCV verification** — `camber.ventilation` (VRP `assess_62_1`,
      `assess_dcv`) + rules and `Role.OA_AIRFLOW`.
- [x] **Continuous benchmarking in CI** — `eval.check_against_baseline` gates detector accuracy
      against a committed baseline (`--json`/`--gate`/`--tol`).
- [x] **Outbound integrations** — CMMS work-orders, notifiers (webhook/Slack/Teams/email), and
      findings/metrics export (`camber.integrate`).
- [x] **Interactive visualization MVP** — the `A → B → E → I` slice + a self-contained HTML
      assembler (`report.build_dashboard`).
- [x] **Real-time / streaming** — online M&V (`mandv.online`) + online FDD (`rules.online`).
- [x] **Predictive layer (forecasting)** — `camber.forecast` (seasonal-naïve + drift, backtest,
      learned-normal anomalies); no ML dependency. *(ML-assisted point mapping → 0.4.)*
- [x] **Grid-interactive (GEB)** — `camber.geb` (demand response, flexibility, carbon shift,
      operation-timing score) + **hourly/marginal Scope-2** (`camber.carbon_hourly`).
- [x] **Fault lifecycle at scale** — `camber.faultlifecycle` (persistent store, assignment/status
      workflow, SLA/aging).
- [x] **Plugin API** — `camber.plugins` (entry-point + in-process rules/adapters/reports).
- [~] **Packaged deployments** — reference Kubernetes manifests + a conda recipe *skeleton*
      (`deploy/`). Remaining: a conda-forge feedstock submission and a hosted demo → 0.3.

## Next — 0.3 (visualization depth)

Deepen the differentiator — *charts and faults are the same artifact* — into a full
**rules-as-a-chart-engine**, dependency-light (matplotlib + stdlib; interactivity is inlined
vanilla JS, no web framework). Continues the Visualizations build order past the MVP (A/B/E/I).

- [x] **Pattern D** — OAT cloud-shape scatter with classification + brush-back (`charts.oat_scatter`).
- [x] **Pattern G** — templated subsystem diagnostic scatters (`charts.diagnostic`).
- [x] **Pattern J** — rules as a chart engine: every rule renders its own evidence
      (`charts.evidence`; the `evidence()` hook), wired into the dashboard.
- [x] **Pattern C** — peer/cohort comparison + a cohort-deviation rule (`charts.cohort`,
      `rules.cohort`).
- [x] **Interactive linking** — a brush-able inline-SVG scatter (`report.linking`); box-select →
      linked timestamp readout.
- [ ] **Packaging & community** — conda-forge feedstock, a MkDocs docs site, GitHub Discussions,
      and the PEP-541 `camber` name request.

Deferred to **0.4**: grounded agentic query & explanation (NL over the deterministic core, cited),
and ML-assisted point mapping — both behind an optional, provider-agnostic seam.

## Delivered — v0.4.0 (AI-assist: point mapping + grounded agent)

The two deferred AI-assist tracks, built dependency-light and provider-agnostic. Both are
**advisory-only** (never the source of truth, always auditable) and read-only toward the BAS.

- [x] **ML-assisted point mapping** — `camber.mapping_assist`: suggest roles for *unmapped* tags,
      advisory-only (a human-confirmed review list; never mutates a `MappingProvider`). A numpy/stdlib
      `FeatureSuggester` baseline (string + unit + physical-range fit), an optional scikit-learn
      `MLSuggester` behind the `[ml]` extra (no pretrained weights; trains on caller/synthetic labels),
      and an `LLMSuggester` over the agent seam whose proposals are validated + re-scored
      deterministically. See **[MAPPING-ASSIST.md](docs/MAPPING-ASSIST.md)**.
- [x] **Grounded agentic query & explanation** — `camber.agent`: cited explanations of findings and
      NL Q&A over the deterministic layers. A `Context` of citable `Fact`s (order-stable ids),
      number-traceability verification, a deterministic template fallback (fully useful with **no LLM
      wired**), and a fully **provider-agnostic** seam — no vendor named, no SDK, no network; an
      AST guard proves it. See **[AGENT.md](docs/AGENT.md)**.
- [ ] **Packaging & community** — conda-forge feedstock submission, MkDocs site on GitHub Pages,
      Discussions, and the PEP-541 `camber` name request (carried from 0.3).

## Delivered — v0.5.0 (deepen FDD + validation; agent CLI + portfolio)

Validation-led: prove the existing suite, then broaden equipment coverage and make the agent
reachable from the terminal.

- [x] **FDD accuracy — prove the whole suite** — `camber.faultlab` + `examples/synthetic_fdd`: a
      deterministic synthetic fault-injection harness scores **24 of 33 single-equipment rules** at
      100% TPR / 0% FPR (up from 2 in the LBNL benchmark), plus a **G36 FC1–FC15** engine harness;
      CI-gated against a committed baseline with an honest scored-vs-fixture coverage table. The
      real-data LBNL benchmark stays for external validity. See **[VALIDATION.md](docs/VALIDATION.md)**.
- [x] **Packaged / DX & refrigerant-side FDD** — new roles (compressor/DX/humidity/heat-pump/approach)
      + templates (**RTU, HeatPump/VRF, DOAS, FCU**) + rules (`compressor_short_cycle`,
      `compressor_staging`, `heatpump_defrost`, `filter_fouling`, `chiller_approach_fouling`). See
      **[FDD-DX.md](docs/FDD-DX.md)**.
- [x] **Agent CLI** — the `camber` console script gains `run` / `report` / `explain` / `ask` / `fleet`
      subcommands (legacy AHU charts under `charts`), with a vendor-neutral `--llm-cmd` seam. See
      **[CLI.md](docs/CLI.md)**.
- [x] **Portfolio triage** — `agent.facts_from_fleet` + multi-site context: grounded portfolio-wide
      Q&A ("which building is worst?").

## Delivered — v0.6.0 (validation & interop completeness)

A consolidation release — finish the validation and interop stories 0.5 opened.

- [x] **FDD hardening → 33/33** — the synthetic accuracy harness (`camber.faultlab`) now scores **every**
      single-equipment rule (fixture-only list empty), 100% TPR / 0% FPR, CI-gated.
      See **[VALIDATION.md](docs/VALIDATION.md)**.
- [x] **Interop round-trips** — Haystack tag→role **import** (`interop.roles_from_haystack` /
      `mapping_from_haystack`, closing the round-trip to Brick parity) + ASHRAE **223P** broadened from
      21 to **44 of 54 roles** (full plant/DX/refrigerant; status/command roles documented as unmapped).
      See **[ONTOLOGY.md](docs/ONTOLOGY.md)**.
- [x] **Broaden the real-data FDD benchmark (Tier 1)** — a cooling-coil-valve leakage **severity sweep**
      in the already-wired LBNL data (characterizes the under-firing leak detector) + a hardened fetcher.
- [~] **Second labeled dataset (Tier 2)** — a second labeled chiller dataset would give the first real-data
      validation of the refrigerant-side rules; **deferred pending an owner license-clearance** for
      clean-room use.

## Next — 0.7

- [ ] **IPMVP Option D — calibrated simulation** M&V (the one remaining IPMVP boundary; A/B/C ship).
      Feasible dependency-light: a numpy grey-box RC model + calibration loop reusing the existing G14
      `fit_stats` / hourly-CV(RMSE) acceptance and `predict()`-based savings machinery (EnergyPlus only
      an optional cross-validator). The standing 0.7 headline.
- [ ] **Labeled chiller benchmark** — if/when the license clears (carried from 0.6 Tier 2).
- [ ] **Packaging & community** (carried) — conda-forge feedstock, Pages enablement, Discussions,
      PEP-541 `camber` name.

## Delivered in 0.1.0 — diagnosis depth & portfolio  *(originally Phase 1)*

Sharpen the "diagnosis, not just detection" edge and scale to many buildings — all shipped in
0.1.0.

- [x] **Root-cause grouping** — cluster co-occurring findings on an equipment into
      one likely root cause (e.g. SAT/overcooling → reheat → simultaneous H/C).
      (`rules.triage.group_findings`)
- [x] **Fleet / portfolio rollup** — cross-building summary: fault counts by
      severity per site, top fleet-wide findings, and cross-sectional EUI
      benchmarking (percentile vs peers). (`report.fleet`)
- [x] **M&V non-routine adjustment** — flag baseline anomalies (shutdowns, etc.) as
      residual outliers and optionally exclude them before refitting, so savings
      aren't corrupted. (`mandv.nonroutine`, `caltrack_savings(exclude_non_routine=)`)
- [x] **Rule-library benchmark** — generalized multi-detector harness
      (`eval.benchmark`: per-detector confusion + correct-diagnosis) scores the
      detector suite across **three LBNL equipment families** — single-duct AHU,
      fan-coil unit, and dual-duct AHU (`examples/lbnl_fdd/benchmark.py`) — with the
      same rules and only the mapping config changing. It measures both the reach
      (100% TPR / 0% FPR on SDAHU + FCU dampers) and the honest limits (the
      modulating-valve leak under-fires; OA-fraction degrades on dual-duct AHUs).
- [x] **Config-driven runs** — a single declarative JSON config (source → mapping →
      equipment → rules → report) runs a whole analysis without a script
      (`camber.config`, `python -m camber.config run.json`).

## Delivered in 0.1.0 — breadth, rigor & distribution  *(originally Phase 2)*

Mature OSS integrated as **optional extras** rather than reinvented — see
[docs/ECOSYSTEM.md](docs/ECOSYSTEM.md) for the fork-vs-depend analysis. All of the below
shipped in 0.1.0; the two `[~]` items have remainders tracked under **Next — 0.2**.

- [~] **More ingest adapters** — *Shipped:* a SQL/historian reader (`camber.ingest.sql`),
      and **read-only network protocol adapters** — Modbus TCP (`[modbus]`, pymodbus),
      MQTT/Sparkplug streaming (`[mqtt]`, paho-mqtt), and BACnet incl. **experimental,
      cert-gated BACnet/SC** (`[bacnet]`, bacpypes3). Each is read-only *by construction*
      (AST-asserted no write/command service), lazy-imports its library, and uses an
      injectable client so the data-shaping cores are fully tested without a network. Posture,
      threat model, and BACnet/SC details in [docs/SECURITY.md](docs/SECURITY.md) +
      [docs/INGEST-PROTOCOLS.md](docs/INGEST-PROTOCOLS.md); historian/SQL/Haystack remains the
      recommended path (NIST SP 800-82). Now also an **OPC-UA** adapter (`[opcua]`, asyncua —
      LGPL kept as a dynamic-only dep): read-only value/history reads with a secure-by-design
      `OpcUaSecurity` config. The `[haystack]` client is now wired end-to-end through the
      transport seam: `parse_his_grid` consumes a native typed-client Grid (object `.rows`,
      `datetime`/`Number` values), and `phable_transport` is the one-line hookup for phable
      (pyhaystack via `client_transport`). The ingest layer is complete.
- [~] **Full ontology interop** — *Shipped:* whole-site Brick round-trip
      (`camber.interop.site_model`: `site_to_ttl` / `site_from_ttl` over Site→Equip→Point
      with relationships, reusing the existing role↔Brick maps; minimal parser default,
      rdflib optional) — beyond the prior point→role mapping. Remaining: richer Brick
      coverage and ASHRAE 223P mapping.
- [x] **M&V Option B + CalTRACK alignment** — weather-**normalized annual savings**
      (`camber.mandv.normalized`: project baseline + reporting models onto a typical year,
      difference the NAC, with a G14 Annex-B uncertainty band), and **retrofit isolation**
      (`camber.mandv.retrofit_isolation`): a generic `fit_driver_model` (affine OLS on a
      system's own driver — runtime, load, tons, or OAT — not just weather) feeds
      `isolation_savings` (sub-meter avoided energy + FSU + model-acceptance gate) and
      `isolation_normalized_savings` (normalized to a fixed reference driver set), reusing the
      same G14 machinery at the narrower Option-B boundary. CalTRACK terminology + eemeter
      cross-check documented in [docs/MANDV.md](docs/MANDV.md).
- [x] **Optional analytics backends** — `camber.interop.pvlib_bridge` (`[pv]`,
      BSD-3): GHI/DNI/DHI→plane-of-array transposition and temperature-aware PVWatts yield
      beyond `camber.pv`'s flat-PR monitoring, with a `compare_expected` that surfaces the
      temperature derate. `camber.interop.psychro` (`[psychro]`, MIT): exact ASHRAE
      psychrometrics (dew point, humidity ratio, enthalpy) plus `compare_wetbulb` validating
      the dep-free Stull wet-bulb (~±1 °F). Both lazy-imported; core stays dependency-free.
- [x] **Fault economics** — `camber.fault_economics`: converts a Finding's intensity
      metric (% of operating hours) plus equipment sizing into an estimated annual energy
      waste and prices it (`estimate_cost`/`cost_findings`), with per-archetype models
      (simultaneous-H/C & reheat gas, chiller kW/ton excess, cooling-tower approach, pump
      riding-the-curve, duct-static fan, boiler short-cycle). `rank_by_cost` orders faults by
      dollars across severity; `annotate_costs` feeds the existing severity-first prioritizer
      so it ranks within a tier by money. Triage-grade and fully transparent: every estimate
      carries its `basis` + `assumptions`, and returns *uncosted* (naming the missing input)
      rather than fabricating when sizing is absent. (Audit-grade savings remain the
      M&V/ECM track.)
- [~] **Visualization** — *Shipped:* `camber.charts.carpet` (load carpet — an hour-of-day
      × date heatmap that exposes occupancy bands, weekend setback, and stuck-on days at a
      glance), `camber.charts.cusum_chart` (the CUSUM savings/waste trajectory with optional
      control limits), and `camber.charts.energy_signature` (energy-vs-temperature scatter
      with the fitted change-point model and balance point(s) overlaid). All follow the
      draw-on-an-Axes convention. Portfolio rollup shipped too: `report.fleet` now ranks the
      fleet by estimated recoverable **dollars** (via `fault_economics`) alongside the
      cross-sectional EUI benchmark and fault rollup (text/HTML).
- [x] **Distribution & Docker** — multi-stage `Dockerfile` (slim, non-root `runtime`
      image serving the read-only API + a `test` stage proving the wheel); `docker compose`
      bundle (`api`/`tool`/`tests`); a tag-driven release workflow that publishes to PyPI via
      **Trusted Publishing (OIDC)** and pushes a **multi-arch image (amd64+arm64) to GHCR**,
      gated on the suite, then cuts a GitHub Release; a `.devcontainer`; and `DOCKER.md`.
- [x] **Scale** — tuned the store/readers for portfolio scale: **year-partition pruning**
      from the ts range (a one-month query across a multi-year store opens only the relevant
      year), **column projection** (`points()` reads only the catalog columns, `read_role_frame`
      only ts/role/value), and a **fast-path pivot** when observations are unique. A synthetic
      generator + benchmark (`python -m camber.store.bench`) and `tests/test_store_scale.py`
      prove pruning/projection mechanically; a single-equipment read stays ~flat as the
      portfolio grows. Plus a **cached catalog** (`_catalog.json`, invalidate-on-write +
      rebuild-on-read) so `points()` is served from an index instead of a partition scan
      (~22 ms warm vs a ~3.5 s rebuild), with writes kept cheap. See [docs/SCALE.md](docs/SCALE.md).

## Later — toward 1.0

(Continuous-benchmark CI and outbound integrations shipped in **0.2.0**. Real-time/streaming,
fault-lifecycle-at-scale, and the plugin API also landed in 0.2.0.)

- [~] **Interactive dashboards / web UI** — the self-contained HTML dashboard (A/B/E/I + evidence
      + a brush-able scatter) shipped in 0.2/0.3; a live web UI with cross-panel linking remains.
- [x] **Agentic query & triage** — natural-language questions over the model and
      history, and plain-language fault explanations — strictly grounded in the
      deterministic layers, citing the rule + data behind every claim (never the
      source of truth, always auditable). Shipped in **0.4.0** (`camber.agent`), with a
      CLI + portfolio triage in **0.5.0**.
- [x] **Automated system optimization (ASO) hooks** — from diagnosis to suggested
      setpoint/sequence changes (advisory, human-in-the-loop). Shipped in **0.3.0**
      (`camber.aso` + `camber.actionplan`).
- [x] **Real-time / streaming** — online M&V + online FDD on live feeds (0.2.0).
- [x] **Fault lifecycle at scale** — persistent store + assignment/SLA/aging (0.2.0).
- [x] **Plugin API** — entry-point + in-process extension points (0.2.0).
- [~] **Packaged deployments** — reference Kubernetes manifests + a conda recipe skeleton (0.2.0);
      conda-forge feedstock + a hosted demo tracked under **Next — 0.3**.

## Visualizations

A capability area that cuts across ingest, FDD, M&V, and reporting. 0.1.0 shipped the first
**static chart primitives** (load carpet, CUSUM, energy signature); the **interactive MVP
(`A → B → E → I`) is tracked under Next — 0.2**, and this section is the fuller vision for what
CAMBER's visual layer should become. It is a **clean-room distillation from public
building-analytics literature and tools** (e.g. PNNL Re-tuning, LBNL, and
university energy-dashboard work) — capabilities and ideas in our own
words, no copied code, assets, or text. A longer write-up with explicit sources is
maintained separately.

### Core design principle: fuse graphing and diagnostics

The differentiator versus legacy desktop trend tools is that **charts and faults are
the same artifact, viewed two ways**:

- **Every fault renders its own evidence chart.** A finding doesn't just say
  "simultaneous heating and cooling 14% of occupied hours" — it carries the trend
  that proves it, with the violating spans shaded. The chart *is* the audit evidence
  and the report figure.
- **Every chart surfaces the faults inside it.** Open any trend and the rule
  violations that fall within its window are annotated in place, so a chart you
  opened to browse becomes a chart that tells you what's wrong.

Around that core, the visual layer should also provide:

- **Portfolio-scale ranking** — surface the worst zones/equipment/buildings first,
  not a flat wall of plots; the chart grid is ordered by estimated impact.
- **Automated agent narration** — a plain-language caption for each chart/finding,
  grounded strictly in the deterministic rules and the data behind them (cite the
  rule and the series; never invent).
- **Interactive linking** — brushing a sub-cloud in a scatter filters the linked
  time-series, carpet, and calendar views to the same points; selection propagates
  across every view.
- **Transparent provenance** — show min/max/avg triples (not just an average that
  hides excursions) and data-quality guards on every view, so a viewer can always
  see how solid the underlying data is.
- **Continuous, not one-shot** — views refresh as new data lands; the same chart
  serves a one-time audit and ongoing monitoring.

### Pattern catalog

Each pattern: the problem it solves → what to build.

- **A. Ingest readiness & resampling ribbon.** *Problem:* raw BAS exports have
  clock drift, gaps, and mixed intervals, and a bare average hides excursions.
  *Build:* an ingest + time-correction + min/max/avg resampling step with a visible
  before/after "readiness ribbon" showing what was corrected, and min/max/avg
  carried as first-class triples downstream.
- **B. Synchronized multi-trend with fault overlay.** *Problem:* operators need to
  read several points on one time axis and see where rules tripped. *Build:* core
  synchronized multi-trend time-series with a fault-annotation overlay (shaded
  violation spans linked back to the rules that produced them) and shareable
  chart-state URLs.
- **C. Peer / cohort comparison.** *Problem:* one unit looks fine until you compare
  it to its siblings. *Build:* concurrent peer comparison as small multiples with
  statistical outlier ranking, scaling out to the portfolio; this enables
  cohort-deviation rules ("this VAV runs unlike its 40 peers").
- **D. X-Y scatter vs OAT ("cloud shape").** *Problem:* the shape of energy/airflow
  against outdoor temperature reveals control behavior a time-series buries.
  *Build:* regression / X-Y scatter against OAT with automatic shape classification,
  change-point detection, and brush-back-to-time (select a cloud region → see when
  it happened).
- **E. Carpet / heatmap.** *Problem:* schedule and time-of-day problems are invisible
  in a line plot. *Build:* a time-of-day × date × value carpet/heatmap with an
  expected-schedule overlay and a difference mode (actual − scheduled, unit −
  cohort, or pre − post).
- **F. Load profiles & load-duration curves.** *Problem:* base load and peaks drive
  cost but aren't obvious from raw trends. *Build:* load profiles and load-duration
  curves with base-load/peak annotation and translation to cost.
- **G. Templated subsystem diagnostic scatters.** *Problem:* each subsystem has an
  expected signature (economizer, SAT/HW/CHW reset, valve/damper travel, no
  simultaneous heat-cool). *Build:* templated diagnostic scatters with the expected
  template overlaid and violations shaded — each doubles as rule evidence (a
  renderer for the rule) and as a report figure.
- **H. M&V baseline, savings & continuous tracking.** *Problem:* a savings number
  without uncertainty isn't defensible, and savings erode silently. *Build:* M&V
  baseline regression and savings with uncertainty (CV(RMSE)/NMBE, error bars),
  evolving into continuous/CUSUM M&V where savings erosion is itself an FDD signal.
- **I. Data organization, quality & filtering.** *Problem:* analytics on bad data is
  worse than none. *Build:* a data-quality dashboard (coverage %, gap map,
  frozen-sensor and out-of-range flags, an overall readiness score) plus semantic
  auto-grouping (Brick/Haystack), used as **hard guards** on FDD — a rule that can't
  trust its inputs declines to fire.
- **J. Rule-based FDD as a chart engine.** *Problem:* findings need to be
  trustworthy, ranked, and explainable. *Build:* rule-based FDD where every rule
  emits its own evidence chart (patterns B/D/E/G are the renderers), ranked by
  estimated energy/cost/comfort impact, with agentic root-cause synthesis layered on
  top of the transparent deterministic rules and config-not-code rule authoring.

### Prioritized build order

**A → B → E → D → I → G → C → J → H → F.**

The **MVP slice is A → B → E → I**: get data in cleanly with visible readiness (A),
give operators the synchronized fault-annotated trend (B) and the carpet view that
exposes schedule problems (E), and gate all of it on a data-quality dashboard (I).
That slice already delivers the fuse-graphing-and-diagnostics principle end to end;
the remaining patterns deepen comparison (C, D), turn rules into a chart engine (G,
J), and extend into M&V and load economics (H, F).

**Status:** A/B/E/I shipped in **0.2.0**. **0.3 completes the catalog** — D (OAT cloud-shape
scatter), G (templated diagnostic scatters), J (rules as a chart engine — every rule renders its
evidence, wired into the dashboard and the Std-211 audit), C (peer/cohort comparison + a
cohort-deviation rule), H (M&V baseline/savings with G14 uncertainty), F (load profiles &
load-duration curves) — plus interactive linking (a brush-able inline-SVG scatter). All ten
patterns A–J are now built.

## Capability tracks — delivered in 0.1.0

Tracks beyond the original phased plan, each consistent with CAMBER's contract —
vendor-neutral via the `Role` model, clean-room and citable, dependency-light, and
every rule shipping a synthetic fixture that proves detection. **All shipped in 0.1.0**
(the two `[~]` items have remainders tracked under **Next — 0.2**).

### Diagnostic breadth

- [x] **Central plant & hydronic rule library** — FDD at the plant, where the largest
      kWh/therms hide. *Shipped:* chiller efficiency (kW/ton), chiller staging/cycling
      (single + a multi-chiller fleet over-staging census), cooling-tower approach,
      condenser-water reset, CHW & HW pump operation (riding-the-curve + VFD-minimum),
      CHW reset + low-ΔT, boiler summer-lockout, boiler short-cycling, and HW-loop
      low-ΔT — 11 rules, each citing ASHRAE/PNNL plant guidance with a synthetic fixture.
- [~] **IAQ & ventilation analytics** — the air-quality axis alongside Std-55 thermal
      comfort. *Shipped:* CO₂-based ventilation adequacy (`camber.iaq` / `co2_ventilation`
      rule) — under-ventilation (elevated occupied CO₂) and over-ventilation (CO₂ near
      outdoor), differential to measured/assumed outdoor CO₂. Remaining: explicit ASHRAE
      62.1 OA-rate checks and demand-controlled-ventilation (DCV) verification.
- [x] **Demand & peak analytics** — `camber.demand`: peak demand + drivers (hour/day,
      coincident peak hour, peakiness), load factor, baseload, night/weekend
      baseload-anomaly detection, and peak-shave demand-charge value. Deeper than the
      load-profiling basics.

### Commissioning workflow (the "C" in CAMBER)

- [x] **Sequence-of-Operations conformance engine** — encode a sequence of operations
      (or ASHRAE G36 itself) as a machine-checkable spec and auto-verify
      operated-vs-designed behavior from trends. Shipped: a declarative clause engine
      (`camber.soo`) — gated predicates over roles, JSON-authorable (`examples/soo/`),
      reporting per-clause conformance % with time-based persistence and emitting
      Findings; a packaged ASHRAE G36 clause library (`camber.soo_library`); and an
      optional `soo` section in config-driven runs (library or JSON spec per class).
- [x] **RCx / MBCx workflow + functional-test automation** — `camber.rcx`:
      `functional_test` (score a Functional Performance Test from trend data: a pass-rate
      over the intervals meeting an expected response), `before_after` (the MBCx
      persistence check — did a measure's metric move, and significantly, across the
      intervention date), and `track_measures` (a measure register grading each fix to a
      lifecycle status: verified / regressed / inconclusive / insufficient). Cites ASHRAE
      Guideline 0 / G36.

### Foundations & credibility

- [x] **Sensor health & data-trust layer** — sensor faults are not equipment faults;
      this gates FDD so a rule that cannot trust its inputs declines to fire. Shipped:
      `camber.sensorhealth` (role-aware physical bounds, cross-sensor physical-consistency
      like mixed-air temperature ordering, and a per-role trust roll-up with a
      `trusted_roles` gate built on the ingest quality stats) wired into the rule runner
      and config (`trust_gate`); `camber.sensordrift` (bias / drift-per-month / tracking
      correlation vs an independent reference — validating the OAT/OSA sensor against
      external weather such as NASA POWER, a station, or a TMY series); and
      `camber.mapping_confidence` (how surely each BAS tag resolved to its role — alias
      vs pattern, ambiguity, physical data-fit — to focus onboarding review).
- [x] **Methods validation & scientific credibility** — `camber.validation` adds Wilson
      score confidence intervals to the FDD-accuracy rates (`metrics_with_ci` over
      `eval.Confusion`) and a `check_determinism` reproducibility harness; the LBNL
      cross-equipment benchmark now publishes its accuracy *with* CIs, and
      [docs/VALIDATION.md](docs/VALIDATION.md) is the methods write-up (validation
      philosophy, labeled-data accuracy, open-fdd cross-validation, M&V/eemeter check,
      uncertainty + reproducibility). Remaining (Phase 3): continuous-benchmark CI.

### Money & compliance

- [x] **Tariff & financial analytics** — a native utility-rate engine (`camber.tariff`:
      TOU energy + tiers, TOU/flat demand, ratchet, fixed → monthly + annual bill) and
      OpenEI URDB fetch/map (`camber.interop.openei`), with an optional NREL-PySAM bridge
      (`[tariff]` extra) for full-fidelity URDB billing; **bill recalculation/validation**
      against actual invoices (`validate_bill`); and **ECM payback / NPV / IRR / SIR**
      (`camber.finance`, dependency-free).
- [x] **Building Performance Standards (BPS) compliance** — `camber.bps`: `site_eui`
      (per-fuel energy → kBtu/ft²/yr) and `emissions_intensity` (→ kgCO₂e/ft²/yr) compute
      the metric; `assess_bps` / `assess_eui` check it against a supplied limit (compliant?,
      margin, % of limit, over-amount, penalty exposure at a $/unit-over rate). Limits are
      caller-supplied (no hard-coded legal values); motivated by laws like NYC LL97 and
      ENERGY STAR / bEQ targets.

## Horizon (beyond 1.0 — research / exploratory)

Directions worth tracking but not yet committed; each needs validation and likely
collaboration.

- [~] **Predictive / ML layer** — load forecasting + learned-normal anomaly detection shipped in
      0.2.0 (`camber.forecast`, no ML dep); ML-assisted point mapping (auto-tagging) → **0.4**.
- [x] **Grid-interactive (GEB)** — demand-response, load-shed/flex quantification, and TOU /
      carbon-aware operation analytics (0.2.0, `camber.geb`).
- [x] **Measured carbon & Scope-2 hourly** — marginal/hourly emissions accounting against a
      supplied grid signal (0.2.0, `camber.carbon_hourly`).
- [ ] **Closed-loop control** — beyond advisory ASO, supervised write-back of
      optimized sequences (with strong guardrails and audit).
- [ ] **Multi-tenant / SaaS** — auth, tenancy, and an authenticated API if the
      project grows a hosted offering.
- [ ] **Standards leadership** — contribute rule content and mappings back to the
      public commons (Brick/Haystack/223P, ASHRAE G36 test cases).

## Cross-cutting (ongoing)

- **Clean-room & citable.** Every method cites a public standard; no proprietary
  code or text. New rules ship with a synthetic fixture proving detection.
- **Honest results.** Report uncertainty and limitations; never overstate a fit or
  a saving.
- **Dependency-light.** stdlib + numpy/pandas/pyarrow/matplotlib; discuss before
  adding a dependency.
- **Docs & onboarding.** Keep README/ARCHITECTURE/CONTRIBUTING current as layers
  land.

> This roadmap is a living document and will shift with use and contributions.
> Have a need that isn't here? Open a feature request.
