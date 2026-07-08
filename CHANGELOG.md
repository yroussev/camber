# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.3.0] — 2026-07-07

Third feature release. Completes the **visualization pattern catalog** (the "charts and faults are
the same artifact" differentiator), adds an **advisory decision layer** (recommendations, a
prioritized action plan, a health scorecard), deepens **FDD / M&V / analytics**, and hardens
**time/DST handling** and the **release pipeline**. Everything stays **read-only toward the BAS/OT**
and dependency-light (numpy/pandas + stdlib; optional extras stay lazy); each capability ships with
option flags, a `docs/` page, and synthetic-fixture tests.

### Added

**Visualization — the full pattern catalog A–J** (`docs/VISUALIZATION.md`)
- **Pattern D** — `charts.oat_scatter`: X-vs-OAT "cloud-shape" scatter with change-point overlay,
  shape classification (linear / hockey-stick / V / scattered), and **brush-back** (region →
  timestamps).
- **Pattern G** — `charts.diagnostic`: templated subsystem diagnostic scatters (expected band
  overlaid, violations shaded) with a packaged `TEMPLATES` set (SAT/CHW reset, economizer,
  no-simultaneous-heat-cool) and constructors.
- **Pattern J (keystone)** — `charts.evidence`: **rules render their own evidence**. A duck-typed
  `evidence(equip, frame)` hook returns an `Evidence` that `render_evidence` dispatches to a
  B/D/E/G renderer; wired into the HTML dashboard and the Std-211 audit report. Backfilled on nine
  rules (simultaneous H/C, SAT reset, economizer, reheat, setback, overcooling, hunting, unmet,
  SAT/airflow control).
- **Pattern C** — `charts.cohort` + `rules.cohort.CohortDeviation`: peer/cohort small-multiples
  ordered by deviation, and a fleet rule flagging a unit that runs unlike its peers.
- **Pattern H** — `charts.savings`: cumulative M&V baseline-vs-actual with the avoided energy shaded
  and an ASHRAE G14 fractional-savings uncertainty band.
- **Pattern F** — `charts.loadprofile_chart`: load profiles (weekday/weekend) and load-duration
  curves with baseload/peak annotation and cost translation.
- **Interactive linking** — `report.linking`: a brush-able inline-SVG scatter (vanilla JS, no
  framework, CSP-safe) with a linked timestamp readout; `build_dashboard(interactive=True)`.

**FDD rules** (all with evidence hooks + ASO recommenders + `docs/CAPABILITIES.md`)
- `control_hunting` — a modulating output that reverses direction excessively (unstable loop).
- `unmet_setpoint_hours` — occupied space temp outside the heating/cooling band (comfort/capacity).
- `supply_air_control` — supply-air temperature not tracking its setpoint.
- `airflow_tracking` — VAV airflow not tracking its setpoint.
- `cohort_airflow` / `cohort_space_temp` — shipped cohort-deviation fleet-rule instances.

**Advisory decision layer** (read-only, human-in-the-loop)
- `camber.aso` — maps an actionable finding to a suggested setpoint/sequence change, grounded (cites
  the rule + G36/PNNL) with documented override-able targets; never a BAS command. `docs/ASO.md`.
- `camber.actionplan` — fuses findings + `fault_economics` ($/yr) + `aso` into a ranked action plan;
  wired into the audit report and config-driven runs. `docs/ACTIONPLAN.md`.
- `camber.scorecard` — rolls findings into per-category scores + an overall A–F grade.
  `docs/SCORECARD.md`.

**M&V**
- `mandv.degreeday` — variable-base HDD/CDD regression baseline (balance point auto-fit by CV(RMSE)).
- `mandv.option_a` — IPMVP Option A (measured Δparameter × stipulated duty), completing Option
  A/B/C coverage.

**Analytics**
- `camber.schedule` — infer the actual weekly operating schedule from interval load; compare to a
  stated schedule (setback opportunity). `docs/SCHEDULE.md`.
- `camber.changedetect` — operational change-point (level-shift) detection in time, for MBCx
  persistence/regression. `docs/CHANGEDETECT.md`.
- `camber.freecooling` — economizer free-cooling opportunity in hours and dollars.
  `docs/FREECOOLING.md`.
- `camber.disaggregate` — split an interval load into baseload / weather / other.
  `docs/DISAGGREGATE.md`.

**Time handling & DST** — `camber.timegrid` (`docs/TIME-HANDLING.md`)
- `interval_hours` (shared, robust to duplicate/zero gaps), `regularize` (sort + de-duplicate
  timestamps), `localize` (tz-localize resolving DST ambiguous/nonexistent times), and
  `dst_anomalies` (count duplicates + fall-back/spring-forward transitions).

**Standards** — `interop.openadr`: map a `geb.DemandResponseResult` to an OpenADR-3.0-shaped report
payload (`docs/GEB.md`).

### Changed

- **Release pipeline hardened** (`.github/workflows/release.yml`): semver-only trigger, deny-by-
  default token with per-job least privilege, hardened runners (egress audit), no persisted git
  creds, per-job timeouts + single-flight concurrency, a **tag↔version consistency gate**, a
  3.10/3.11 test matrix, a built-wheel install smoke test, PyPI `skip-existing`, SLSA provenance +
  SBOM on the image, and changelog-extracted release notes.
- `io.load_csv(dedupe="first")` collapses duplicate timestamps on load; `ingest.quality` reports
  `n_duplicate_ts`.
- `report.build_dashboard` gains `rules`/`evidence`/`interactive` flags; `AuditReport.to_html`
  gains `rules`/`frames`/`recommend`; config-driven runs support a `recommend` report option.
- `Finding` gains an optional `evidence` field (additive; back-compatible).
- `ROADMAP.md` re-baselined and `docs/CAPABILITIES.md` extended for the 0.3 surface.

### Fixed

Correctness issues surfaced by a multi-agent code review of the 0.3 diff (regression-tested in
`tests/test_review_fixes.py`):
- cohort robust-z no longer masks a real outlier when >half the cohort share a value (MAD=0
  mean-absolute-deviation fallback);
- `scorecard` no longer silently drops unmapped/plugin-rule findings (they'd hide behind an "A");
- `degreeday` drops NaN periods before the fit and rejects degenerate n≤p fits;
- `changedetect` constrains splits by `min_segment` (no spurious shift from a single edge outlier);
- `savings_chart` guards an empty cumulative array; `interval_hours`/`hunting` are robust to
  duplicate (DST fall-back) timestamps;
- evidence rendering closes only its own figure (was `plt.close("all")`) and unifies the dashboard/
  audit loop; `outlier_mask` no longer crashes on a non-unique index;
- config `EnergyPrice` ignores unknown keys instead of crashing late; unmet/overcooling evidence
  masks now match their finding's metric.

## [0.2.0] — 2026-07-06

Second feature release. Extends the 0.1 core along the "Next — 0.2" roadmap and a streaming/
grid/carbon analytics sprint. Everything stays **read-only toward the BAS/OT** and dependency-light
(numpy/pandas + stdlib; optional extras stay lazy). Each capability ships with option flags, a
`docs/` page, and synthetic-fixture tests.

### Added

- **ASHRAE 62.1 ventilation verification** (`camber.ventilation`, `camber.rules.ventilation_rule`)
  — Ventilation Rate Procedure check of delivered outdoor air (`required_oa_cfm`, `assess_62_1`)
  and a DCV-modulation check (`assess_dcv`), plus `DemandControlledVentilation` /
  `VentilationRateProcedure` rules and a new `Role.OA_AIRFLOW`. `docs/VENTILATION.md`.
- **ASHRAE 223P + richer Brick interop** (`camber.interop.semantic223`) — map `Role`/equipment
  classes to a 223P-shaped RDF subset (minimal/full profiles, builtin or rdflib backend), and a
  broadened role↔Brick map with equipment hierarchy + relationships. `docs/ONTOLOGY.md`.
- **Continuous benchmarking gate** (`camber.eval.check_against_baseline`) — the LBNL benchmark
  runner gains `--json`/`--gate`/`--tol`/`--update-baseline` so detector accuracy (TPR/FPR/
  diagnosis) can be gated against a committed baseline in CI. `docs/VALIDATION.md`.
- **Outbound integrations** (`camber.integrate.notify` / `cmms` / `export`) — webhook, Slack/Teams,
  and email notifiers (severity filter + fingerprint dedupe), CMMS work-order rendering with a
  pluggable submit + idempotency, and findings/metrics export (CSV/Parquet/JSON). All opt-in and
  from the findings layer — never writing to the BAS. `docs/INTEGRATIONS.md`.
- **Interactive visualization MVP** (`camber.charts.readiness` / `multitrend` /
  `quality_dashboard`, `camber.report.dashboard`) — ingest-readiness ribbon, fault-annotated
  synchronized multi-trend, and a data-quality dashboard assembled into one self-contained HTML
  (matplotlib inlined; no web framework). `docs/VISUALIZATION.md`.
- **Online / streaming M&V** (`camber.mandv.online`) — `OnlineCusum` (incremental tabular CUSUM of
  savings/waste against a baseline model) and `RollingAnomaly` (rolling MAD-robust residual
  z-score); O(1) per sample. `docs/STREAMING.md`.
- **Online FDD** (`camber.rules.online.OnlineFDD`) — sliding trailing-window rule evaluation that
  emits a `Transition` only on a verdict change (no per-sample re-alert), with per-equipment
  isolation and the duck-typed rule protocol. `docs/STREAMING.md`.
- **Grid-interactive (GEB) analytics** (`camber.geb`) — `demand_response` (shed/rebound vs a
  baseline), `flexibility` (sheddable headroom), `carbon_aware_shift`, and `operation_score`
  (load-timing vs a price/carbon signal, rearrangement-inequality best/worst bounds). Advisory
  analytics; closed-loop DR remains a roadmap item. `docs/GEB.md`.
- **Hourly / marginal Scope-2 carbon** (`camber.carbon_hourly`) — `hourly_emissions` (time-varying
  factor → co2e, effective factor, timing premium) and `marginal_vs_average` (load-shift value uses
  marginal, reporting uses average). `docs/CARBON.md`.
- **Load forecasting + learned-normal anomalies** (`camber.forecast`) — `seasonal_forecast`
  (time-of-week shape + additive drift, no ML dependency), `backtest` (MAE/MAPE/CV(RMSE) honesty
  check), and `forecast_anomalies` (robust residual band → FDD signal). `docs/FORECAST.md`.
- **Persistent fault lifecycle** (`camber.faultlifecycle`) — a durable fault store keyed by the
  (site, equip, rule) fingerprint that survives across runs, with an assignment/status workflow,
  SLA/aging tracking, and atomic JSON persistence.
- **Plugin API** (`camber.plugins`) — third-party rules / ingest adapters / report formats
  discovered via Python entry points (`camber.rules` / `camber.adapters` / `camber.reports`) or
  registered in-process, duck-typed against the existing protocols with per-plugin error
  isolation. `docs/PLUGINS.md`.
- **Deployment references** — `deploy/k8s/camber-api.yaml` (namespace + read-only PVC + non-root
  2-replica Deployment + ClusterIP Service) and a `deploy/conda/meta.yaml` recipe skeleton; nothing
  is published. `docs/DEPLOY.md`.
- **Test hardening** — `camber.inventory` and `camber.io` now carry direct tests; a cross-capability
  `examples/geb_carbon_demo.py` wires GEB → carbon → forecast on synthetic data.

### Fixed

- `carbon_hourly.hourly_emissions` now reports `avg_factor` in the same unit as `effective_factor`
  on the `unit_kg_per_kwh=False` (g/kWh) path (previously left 1000× off; the timing premium was
  already correct).

## [0.1.1] — 2026-06-14

Documentation-only patch (no code or dependency changes).

### Added

- **`docs/CAPABILITIES.md`** — a single capability reference for everything in 0.1: what each
  capability does, its key API, the **option flags** that tune it, the module, and the standard
  it cites, grouped by layer (ingest · semantic model · FDD · SOO · M&V · RCx · money & compliance ·
  domain analytics · storage · reporting/integration/API · orchestration). Linked from the README.

## [0.1.0] — 2026-06-12

First public release.

### Added

- **Ingest** — per-point CSV, wide CSV, and a Project-Haystack `hisRead` client (wired through
  an injectable transport seam: `parse_his_grid` consumes a native typed-client Grid — object
  `.rows`, `datetime`/`Number` values — and `phable_transport` is the one-line hookup for a
  phable client; pyhaystack/any client via `client_transport`); per-point data-quality scoring
  with an auditable cleaning trail; valve/damper unit normalization (0–1 vs 0–100).
- **LBNL BETTER cross-check** — optional `[better]` extra (`camber.interop.better`):
  `compare_changepoint` runs CAMBER's change-point M&V and LBNL BETTER's analytical engine
  (`better-lbnl-os`) on the same monthly energy-vs-temperature series and reports
  model-order / baseload / R² agreement — corroborating a savings baseline with an
  independent engine. PySAM-style lazy import; core stays dependency-free.
- **pvlib bridge** — optional `[pv]` extra (`camber.interop.pvlib_bridge`, BSD-3):
  `poa_from_ghi` transposes horizontal irradiance (GHI/DNI/DHI) onto the array plane and
  `pvwatts_expected_kwh` applies a temperature-derated PVWatts yield — the solar-resource /
  cell-temperature modeling `camber.pv`'s flat-PR monitoring omits; `compare_expected` shows
  the temperature derate. Lazy import; core stays dependency-free.
- **PsychroLib bridge** — optional `[psychro]` extra (`camber.interop.psychro`, MIT): exact
  ASHRAE-formulation psychrometrics (`psychrometrics`: wet-bulb, dew point, humidity ratio,
  enthalpy) and `compare_wetbulb`, which validates CAMBER's dependency-free Stull wet-bulb
  against the exact value (~±1 °F). Lazy import; core stays dependency-free.
- **Network ingest adapters (read-only)** — Modbus TCP (`camber.ingest.modbus`, `[modbus]`/
  pymodbus — register snapshot + poll), MQTT/Sparkplug streaming (`camber.ingest.mqtt_stream`,
  `[mqtt]`/paho-mqtt — subscribe + buffer + shape), BACnet (`camber.ingest.bacnet`,
  `[bacnet]`/bacpypes3 — Trend-Log history + present values) incl. **experimental,
  certificate-gated BACnet/SC** (`wss://`+TLS, hub URI + operational cert config), and OPC-UA
  (`camber.ingest.opcua`, `[opcua]`/asyncua — history + current-value reads, secure-by-design
  `OpcUaSecurity`; asyncua's LGPL kept as a dynamic-only optional dep). Each is
  **read-only by construction** (a test parses the AST and fails on any write/command service),
  lazy-imports its protocol library behind an optional extra, and takes an injectable client so
  the data-shaping cores test without a network. New `docs/SECURITY.md` (NIST SP 800-82 /
  IEC 62443 threat model + posture) and `docs/INGEST-PROTOCOLS.md`. Historian/SQL/Haystack
  stays the recommended ingest path.
- **SQL/historian ingest** — `camber.ingest.sql`: `SqlSource` (a `SourceAdapter`) and
  `read_points` read a long/narrow point table (timestamp, point, value, optional unit +
  `WHERE`) over any PEP-249 DB-API connection into per-point Series — stdlib `sqlite3`,
  no new dependency.
- **Full Brick site-model interop** — `camber.interop.site_model`: `site_to_ttl` /
  `site_from_ttl` round-trip a whole Site→Equip→Point model (with relationships) to and
  from Brick Turtle, reusing the existing role↔Brick maps; minimal parser by default,
  rdflib optional — beyond the prior point→role mapping.
- **Sensor health / data-trust** — builds on the ingest quality stats with role-aware
  physical bounds (catching BAS error sentinels / unit-scaling blunders the robust
  outlier test misses), cross-sensor physical-consistency checks (e.g. mixed-air temp
  must lie between outdoor- and return-air temp), and a per-role trust roll-up with a
  `trusted_roles` gate — wired into the rule runner (and config `trust_gate`) so a rule
  whose required inputs aren't trusted declines to fire (an auditable `info` finding)
  instead of reporting a sensor problem as an equipment fault. Plus **sensor bias/drift
  detection vs a reference** (`camber.sensordrift`): bias, drift-per-month, and tracking
  correlation against an independent series — e.g. validating the outdoor-air (OAT/OSA)
  sensor against NASA POWER / a nearby station / a TMY series, which the BAS can't check
  on its own. And **point-mapping confidence** (`camber.mapping_confidence`): scores how
  surely each BAS tag resolved to its role (alias vs pattern match, ambiguity, and
  physical data-fit), flagging the low-confidence / ambiguous / unmapped tokens so
  onboarding review goes where it's needed.
- **Semantic model** — vendor-neutral `Role` vocabulary, `MappingProvider`, an
  entity model with equipment templates and completeness validation, and
  `resolve()` to assemble role-named frames.
- **FDD** — rule engine with ASHRAE Guideline 36 AFDD and PNNL Building Re-tuning
  diagnostics (simultaneous heat/cool, reheat, SAT/CHW reset, economizer, OA
  fraction incl. under-ventilation, boiler lockout, boiler short-cycling, HW-loop
  low-ΔT, overcooling, setback, static
  and pump resets, chiller efficiency (kW/ton), chiller staging/cycling, multi-chiller
  over-staging (fleet), cooling-tower approach, condenser-water reset, CHW/HW pump
  riding-the-curve + VFD-minimum, leaking valves); impact prioritization and fault
  lifecycle; an
  FDD-accuracy evaluation harness.
- **Fault economics** — `camber.fault_economics`: turns a fault into an estimated annual
  dollar impact so the prioritizer can rank by money, not just severity. Per-archetype models
  combine the rule's intensity metric (% of operating hours) with equipment sizing and
  documented, override-able assumptions — simultaneous-H/C & reheat gas (+ paired cooling),
  chiller kW/ton excess, cooling-tower approach penalty, pump riding-the-curve, duct-static
  fan waste, boiler short-cycle. `estimate_cost`/`cost_findings`/`total_cost`, `rank_by_cost`
  (dollar-first across severity) and `annotate_costs` (feeds `triage.rank_findings`). Every
  estimate carries its `basis` + `assumptions` and returns *uncosted* (naming the missing
  input) instead of fabricating when sizing is absent; triage-grade, distinct from the
  audit-grade M&V/ECM track.
- **RCx / MBCx** — `camber.rcx`: `functional_test` scores a Functional Performance Test
  from trend data (pass-rate over the intervals meeting an expected response),
  `before_after` is the monitoring-based-commissioning persistence check (did a measure's
  metric move across the intervention date, and significantly), and `track_measures` is a
  measure register grading each fix verified / regressed / inconclusive / insufficient.
  Cites ASHRAE Guideline 0/36.
- **Methods validation** — `camber.validation`: Wilson score confidence intervals on the
  FDD-accuracy rates (`metrics_with_ci` over `eval.Confusion`) so TPR/FPR/accuracy carry
  uncertainty, plus a `check_determinism` reproducibility harness; the LBNL benchmark
  publishes accuracy with CIs and `docs/VALIDATION.md` documents the methodology.
- **BPS compliance** — `camber.bps`: `site_eui` (per-fuel energy → kBtu/ft²/yr) and
  `emissions_intensity` (→ kgCO₂e/ft²/yr) compute the metric; `assess_bps` / `assess_eui`
  check it against a supplied Building-Performance-Standard limit (compliant?, margin,
  % of limit, over-amount, penalty exposure). Caller-supplies limits (no hard-coded legal
  values).
- **Sequence-of-Operations conformance** — a declarative clause engine (`camber.soo`):
  gated predicates over roles (`when <gate> then expect <predicate>`) that measure
  operated-vs-designed behavior per clause as a conformance %, with optional
  time-based persistence (forgive transient excursions), JSON-authorable
  (`examples/soo/`) and emitting Findings into the same prioritization/report/triage;
  ships a packaged ASHRAE Guideline 36 clause library (`camber.soo_library`); wired
  into config-driven runs via an optional `soo` section (library or JSON spec per class).
- **M&V retrofit isolation (IPMVP Option B)** — `camber.mandv.retrofit_isolation`: a generic
  `fit_driver_model` (affine least-squares `DriverModel` on a sub-metered system's *own*
  driver — runtime, load, cooling tons, production, or OAT; 1-D, multivariate, or constant)
  feeds `isolation_savings` (reporting-period avoided energy at the sub-meter boundary, with
  the ASHRAE G14 Annex-B fractional uncertainty and the baseline model-acceptance verdict) and
  `isolation_normalized_savings` (savings normalized to a fixed reference driver set). Reuses
  the existing G14 savings/uncertainty machinery at the narrower Option-B boundary — both are
  written against any `predict()`-able model.
- **M&V normalized savings** — `camber.mandv.normalized`: weather-**normalized annual
  savings** (project the baseline and reporting models onto a typical/normal year,
  difference their normalized annual consumption) with an ASHRAE G14 Annex-B uncertainty
  band — the IPMVP "normalized savings" complement to the existing avoided-energy use.
- **M&V** — change-point inverse models (2P–5P + heating/cooling-zero), the LBNL
  TOWT model, fit statistics with fractional savings uncertainty, CUSUM, weather
  normalization, and rate/energy-aware resampling.
- **IAQ / ventilation** — CO₂-based ventilation-adequacy diagnostic (`camber.iaq`):
  flags under-ventilation (elevated occupied CO₂, ~ASHRAE 62.1 ventilation-rate proxy)
  and over-ventilation (CO₂ near outdoor — a conditioning-energy penalty), differential
  to a measured or assumed outdoor CO₂; the air-quality companion to Std-55 comfort.
- **Tariffs / utility rates** — a native, dependency-free tariff engine (`camber.tariff`):
  bills an interval load against a URDB-shaped rate (fixed charge, TOU energy with tiered
  blocks + 12×24 weekday/weekend schedules, TOU and flat monthly demand, ratchet) into a
  per-month + annual cost breakdown. `camber.interop.openei` fetches and maps an OpenEI
  Utility Rate Database (URDB) rate (stdlib `urllib`, API key); an optional `[tariff]`
  extra bridges to NREL PySAM's `UtilityRate5` (`camber.interop.tariff_nrel`) for
  full-fidelity / cross-checking. Bill **recalculation/validation** (`validate_bill`)
  compares the recomputed bill to actual invoices month by month — validating the rate
  model and flagging over/under-billed months (MAPE + per-month high/low status).
- **ECM financials** — `camber.finance`: simple & discounted payback, NPV, IRR (hand-rolled
  bisection — no `numpy_financial`), and SIR for an energy-conservation measure from its
  cost and dollar savings, with savings escalation, annual O&M, and salvage.
- **Demand & peak analytics** — `camber.demand`: peak demand + its drivers (hour/day,
  coincident peak hour, how few intervals set it), load factor, baseload, a
  night/weekend **baseload-anomaly** check (unoccupied vs occupied load — equipment not
  setting back), and **peak-shave $ value** (demand charge recoverable by capping the
  monthly peak at a target).
- **Visualization** — three analytics-driven charts (`camber.charts`): a **load carpet**
  (`carpet`, hour-of-day × date heatmap exposing occupancy bands, weekend setback, and
  stuck-on days), a **CUSUM** savings/waste trajectory (`cusum_chart`, with optional control
  limits), and an **energy-signature** plot (`energy_signature`, energy-vs-temperature scatter
  with the fitted change-point model and balance point(s) overlaid). All draw onto a supplied
  Axes and lazy-import matplotlib, matching the existing chart convention.
- **Domain analytics** — Std-55 comfort (PMV/PPD), utility cost, carbon, water
  (irrigation budget, cooling tower, leak detection), load profiling, PV, lighting.
- **Storage** — Parquet time-series store (entity-keyed, hive-partitioned) with
  tag-filtered reads, rollups, and retention pruning. **Portfolio-scale tuning:** time-range
  reads prune `year` partitions (not just the `ts` column), `read_long` takes a `columns=`
  projection (so `points()` reads only the catalog and `read_role_frame` only ts/role/value),
  and `read_role_frame` uses a fast plain pivot when observations are unique. A **cached
  catalog** (`_catalog.json`, invalidate-on-write + rebuild-on-read) serves `points()` from an
  index instead of a partition scan (~22 ms warm) while keeping writes cheap; `rebuild_catalog()`
  materializes it for older stores. A synthetic generator + benchmark (`camber.store.bench`,
  `python -m camber.store.bench`) and [docs/SCALE.md](docs/SCALE.md) — a single-equipment read
  stays ~flat as the portfolio grows.
- **Interop** — Brick model import (derive role mappings) and Haystack/Brick export.
- **Integration & API** — findings → CMMS tickets with a pluggable notifier; a
  read-only HTTP API over the store.
- **Reporting** — ASHRAE/ACCA Standard 211 audit deliverables (text/HTML), and a
  **portfolio rollup** (`report.fleet`) that ranks a fleet by cross-sectional EUI
  benchmark, actionable-fault burden, and — when an `EnergyPrice` is supplied — estimated
  recoverable **dollars** per building (via `fault_economics`) with a fleet-wide total.
- **Examples** — runnable LBNL FDD and Building Data Genome 2 examples (public
  CC-BY datasets, fetched on demand), plus a data-free synthetic demo.
- **Distribution & Docker** — a multi-stage `Dockerfile` producing a **slim runtime image**
  (installed package + runtime deps only; non-root; healthcheck) that serves the read-only HTTP
  API over a mounted store, plus a `test` stage that proves the built wheel; a `docker compose`
  bundle (`api` / `tool` / `tests`); a release workflow that on a `vX.Y.Z` tag publishes to
  **PyPI via Trusted Publishing (OIDC, no stored token)** and pushes a **multi-arch image
  (amd64 + arm64) to GHCR**, then cuts a GitHub Release — all gated on the test suite; a
  `.devcontainer` for one-click contributor setup; and `DOCKER.md`. CI runs pytest on Python
  3.10 / 3.11.

[0.3.0]: https://github.com/yroussev/camber/releases
[0.2.0]: https://github.com/yroussev/camber/releases
[0.1.0]: https://github.com/yroussev/camber/releases
