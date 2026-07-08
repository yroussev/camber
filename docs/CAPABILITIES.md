# CAMBER capabilities reference

A single index of what CAMBER 0.3 does, grouped by the building-analytics layers, with the key
API, the **option flags** that tune each capability, the module, and the standard it cites.
Deeper write-ups are linked where they exist.

Everything is vendor-neutral via the `Role` model, dependency-light (stdlib + numpy / pandas /
pyarrow / matplotlib), and clean-room (every method cites a public standard; every rule ships a
synthetic fixture).

---

## Ingest

Adapters normalize any source to named point series on a common time grid (`SourceAdapter`:
`point_names` / `load_points` / `units`). See **[INGEST-PROTOCOLS.md](INGEST-PROTOCOLS.md)** and
**[SECURITY.md](SECURITY.md)**.

- **CSV** — `ingest.csv_perpoint.PerPointCsvAdapter` (a folder of per-point files) and
  `ingest.csv_wide.WideCsvAdapter` (one wide table). Flags: `resample`.
- **Project-Haystack** — `ingest.haystack.HaystackAdapter` over an injectable transport
  (`http_json_transport`, or `client_transport` to wrap a maintained client). Flags: `range_str`,
  `resample`.
- **SQL / historian** — `ingest.sql.SqlSource` / `read_points` over any PEP-249 connection. Flags:
  `ts_col` / `point_col` / `value_col` / `unit_col`, `where`.
- **Network protocols (read-only)** — Modbus (`[modbus]`), MQTT/Sparkplug (`[mqtt]`), BACnet incl.
  experimental BACnet/SC (`[bacnet]`), OPC-UA (`[opcua]`). Read-only by construction; historian-first
  posture. Per-adapter flags documented in INGEST-PROTOCOLS.md.
- **Data quality** — `ingest.quality.assess` (coverage, gaps, flatline, outliers, duplicate
  timestamps, composite score) and `clean`. Flags: `expected_freq`, `drop_outliers`.
- **Time / DST** — `timegrid`: `interval_hours`, `regularize` (sort + de-duplicate the DST fall-back
  hour), `localize` (tz-localize resolving DST ambiguous/nonexistent), `dst_anomalies`. `load_csv`
  de-duplicates timestamps by default. See **[TIME-HANDLING.md](TIME-HANDLING.md)**.

## Semantic model

- **Roles + mapping** — `model.roles.Role` vocabulary; `model.mapping.MappingProvider` (alias +
  pattern → role). `resolve.resolve(equip, roles)` assembles a role-named frame. Flags: `resample`.
- **Entities + completeness** — `model.entities` (Site/Equip/Point) with equipment-template
  completeness validation.
- **Brick interop** — `interop.mapping_from_brick` / `roles_from_brick` (import); `interop.to_brick`
  and `interop.site_to_ttl` / `site_from_ttl` (export + whole-site round-trip). Flags: `backend`
  (`auto`/`rdflib`/`minimal`; rdflib via the `[brick]` extra).
- **ASHRAE 223P** — `interop.semantic223.site_to_223` / `site_from_223` map a site (roles + equipment)
  to/from a 223P-shaped RDF subset (connections, medium, points; `ROLE_TO_223` quantity-kinds). Flags:
  `profile` (`minimal`/`full`), `include_relations`. See **[ONTOLOGY.md](ONTOLOGY.md)**.

## FDD — fault detection & diagnostics

Rule engine (`rules.base.Registry`, `rules.builtin.builtin_registry`); each rule consumes a
role-frame and returns a `Finding`. Run with `registry.run(name, equip_refs, mapping, min_trust=…)`.

- **Air-side (G36 + PNNL Re-tuning)** — simultaneous heat/cool, reheat (penalty + G36 minimization),
  SAT reset, overcooling (min-flow + severity), economizer / OA-fraction (incl. under-ventilation),
  night/weekend setback, duct-static, zone census, and **unmet-setpoint hours** (`unmet_setpoint_hours`
  — occupied space temp outside the heating/cooling band, the operator-facing comfort/capacity
  metric). Per-rule flags (e.g. `threshold`, `min_oa_pct`, `occupied_only`, `tol_F`).
- **Central plant & hydronic** — chiller kW/ton efficiency, chiller staging + multi-chiller fleet
  over-staging, cooling-tower approach, condenser-water reset, CHW/HW pump (riding-curve + VFD-min),
  CHW reset + low-ΔT, boiler summer-lockout + short-cycle. Flags include design targets
  (`design_kw_per_ton`, `max_starts_per_day`, …).
- **Control stability** — `control_hunting`: flags a modulating output (valve/damper) that reverses
  direction excessively (unstable loop) by counting reversals/hour beyond a deadband. Flags:
  `warn_per_hr`, `fault_per_hr`, `deadband`. `supply_air_control`: flags supply-air temperature
  that fails to *track its setpoint* (control/capacity fault; running hours only). Flags: `tol_F`,
  `warn_pct`, `fault_pct`. `airflow_tracking`: flags measured VAV airflow that fails to track its
  setpoint (stuck/undersized damper, failed actuator, starvation, bad flow sensor). Flags:
  `tol_frac`, `warn_pct`, `fault_pct`.
- **Peer/cohort** — `cohort.CohortDeviation` (fleet rule): flags a unit running unlike its peers on
  a role (robust z of a mean/peak/load-shape summary). Shipped instances `cohort_airflow`,
  `cohort_space_temp`; construct your own for any role. Flags: `k`, `summary`, `min_cohort`.
- **Sensor health / data trust** — `sensorhealth` (physical bounds, cross-sensor consistency,
  per-role trust roll-up + `trusted_roles` gate), `sensordrift` (bias / drift / tracking vs a
  reference), `mapping_confidence`. The runner's `min_trust` flag makes a rule decline when its
  inputs aren't trusted.
- **Prioritization & lifecycle** — `rules.triage`: `rank_findings` (severity, or a magnitude/cost
  key), `group_findings` (root-cause grouping), `FaultRegister` (new/ongoing/resolved across runs).
  Persistent, cross-process: `faultlifecycle.FaultLifecycle` — a fingerprint-keyed fault store with
  an assignment/status workflow, SLA/aging tracking, and atomic JSON persistence. Flags:
  `magnitude_key`, `actionable_only`, `reopen_on_recurrence`, `auto_resolve_absent`.
- **Fault economics** — `fault_economics`: per-fault annual $ impact → rank by money. Flags:
  `params` (assumptions), `models`, `min_severity` (via `rank_by_cost`).
- **Ventilation (ASHRAE 62.1)** — `ventilation.assess_62_1` (Ventilation Rate Procedure: required vs
  delivered OA, deficit) and `assess_dcv` (DCV modulation vs occupancy/CO₂), with the
  `VentilationRateProcedure` / `DemandControlledVentilation` rules and `Role.OA_AIRFLOW`. Flags:
  `space_type` vs `rp`/`ra`, `ez`, `aggregate`, `min_corr`, `min_modulation`. See
  **[VENTILATION.md](VENTILATION.md)**.
- **Accuracy + CI gating** — `eval.benchmark` + `validation.metrics_with_ci` (Wilson CIs), and
  `eval.check_against_baseline` to gate accuracy (TPR/FPR/diagnosis) against a committed baseline in
  CI (`--json` / `--gate` / `--tol` / `--update-baseline`). See **[VALIDATION.md](VALIDATION.md)**.

## Sequence-of-Operations conformance

`soo` — a declarative clause engine (gated predicates over roles, JSON-authorable) measuring
operated-vs-designed behavior as a conformance %, with `soo_library` (packaged ASHRAE G36 clauses).
Flags: persistence window, per-class spec.

## M&V — measurement & verification

See **[MANDV.md](MANDV.md)**. Change-point models (`mandv.models`, 2P–5P + zero variants), LBNL
TOWT (`mandv.towt`), fit statistics + G14 fractional savings uncertainty (`mandv.stats`), CUSUM
(`mandv.cusum`), weather normalization (`mandv.weather`), normalized annual savings
(`mandv.normalized`), non-routine adjustment (`mandv.nonroutine`), Option-B retrofit isolation
(`mandv.retrofit_isolation`), CalTRACK alignment (`mandv.caltrack`), a **variable-base degree-day**
baseline (`mandv.degreeday`, HDD/CDD regression with an auto-fit balance point), and **IPMVP Option
A** (`mandv.option_a`, measured Δparameter × stipulated duty — completing Option A/B/C). Flags:
`confidence`, `exclude_non_routine`, model `kinds`, `aggregate`, `balance_point`.

## Streaming / online

Incremental monitors for a live BAS feed — bounded state, O(1)–O(window) per sample. See
**[STREAMING.md](STREAMING.md)** and **[FORECAST.md](FORECAST.md)**.

- **Online M&V** — `mandv.online.OnlineCusum` (incremental tabular CUSUM of savings/waste vs a
  baseline model → savings-erosion alarm) and `RollingAnomaly` (rolling median/MAD residual
  z-score). Flags: `limit`, `slack`, `window`, `k`, `min_samples`.
- **Online FDD** — `rules.online.OnlineFDD`: sliding trailing-window rule evaluation emitting a
  `Transition` only on a verdict change (no per-sample re-alert), per-equipment isolation. Flags:
  `window`, `eval_every`, `min_samples`, `emit_ok`.
- **Forecasting + learned-normal anomalies** — `forecast.seasonal_forecast` (time-of-week shape +
  additive drift, no ML dep), `backtest` (MAE / MAPE / CV(RMSE) honesty check), `forecast_anomalies`
  (robust residual band → FDD signal). Flags: `drift_window`, `k`, `test_frac`.

## Commissioning (RCx / MBCx)

`rcx`: `functional_test` (FPT pass-rate), `before_after` (MBCx persistence across an intervention
date), `track_measures` (measure register → verified/regressed/inconclusive/insufficient).

## Money & compliance

- **Tariffs** — `tariff` (URDB-shaped: TOU energy + tiers, TOU/flat demand, ratchet, fixed →
  monthly + annual bill), `tariff.validate_bill` (vs actual invoices, MAPE + per-month status),
  `interop.openei` (URDB fetch), `[tariff]` PySAM bridge. Flags: `tol_pct`.
- **ECM finance** — `finance`: payback, NPV, IRR, SIR with escalation / O&M / salvage.
- **Demand & peak** — `demand`: peak + drivers, load factor, baseload, night/weekend baseload
  anomaly, peak-shave $ value. Flags: `near_peak_frac`, `start_hour`/`end_hour`, `target_kw`.
- **BPS compliance** — `bps`: `site_eui`, `emissions_intensity`, `assess_bps` / `assess_eui`
  (compliant?, margin, penalty exposure). Limits are caller-supplied (no hard-coded legal values).

## Grid-interactive (GEB) & carbon timing

Beyond using *less* energy — quantify *shifting and shedding* load, and the carbon cost of *when*
power is used. Advisory analytics (read-only toward the BAS). See **[GEB.md](GEB.md)** and
**[CARBON.md](CARBON.md)**.

- **Demand response & flexibility** — `geb.demand_response` (shed kW/kWh/% + rebound vs a baseline),
  `geb.flexibility` (sheddable load above baseload, peak-to-average headroom). Flags:
  `rebound_hours`, `baseload_pct`.
- **Load timing** — `geb.carbon_aware_shift` (CO₂ saved shifting load dirty→clean hours) and
  `geb.operation_score` (load timing vs a price/carbon signal, rearrangement-inequality bounds).
- **Hourly / marginal Scope-2** — `carbon_hourly.hourly_emissions` (time-varying factor → co2e,
  effective factor, timing premium) and `marginal_vs_average` (load-shift value uses marginal;
  reporting uses average). Flags: `unit_kg_per_kwh`.
- **OpenADR export** — `interop.openadr.to_openadr_report`: map a `demand_response` result to an
  OpenADR-3.0-shaped report payload for a DR program.

## Domain analytics

`comfort` (Std-55 PMV/PPD), `iaq` (CO₂ ventilation adequacy), `cost`, `carbon`, `water` (irrigation
/ cooling-tower / leak), `loadprofile`, `pv` (+ `interop.pvlib_bridge`, `[pv]`),
`interop.psychro` (PsychroLib, `[psychro]`), `lighting`. Plus:
- **Schedule inference** — `schedule.detect_schedule` / `compare_schedule`: the actual weekly
  operating schedule from interval load vs a stated one (setback opportunity). [SCHEDULE.md](SCHEDULE.md).
- **Change-point detection** — `changedetect.detect_level_shifts`: *when* a signal's mean shifts
  (MBCx persistence/regression). [CHANGEDETECT.md](CHANGEDETECT.md).
- **Free-cooling opportunity** — `freecooling.free_cooling_opportunity`: missed economizer hours →
  recoverable kWh/$. [FREECOOLING.md](FREECOOLING.md).
- **Load disaggregation** — `disaggregate.disaggregate_load`: baseload / weather / other split.
  [DISAGGREGATE.md](DISAGGREGATE.md).

## Advisory & synthesis

Read-only, human-in-the-loop layers on top of the findings:
- **ASO** — `aso.recommend` / `recommend_findings`: an actionable finding → a suggested setpoint/
  sequence change, grounded (cites the rule + G36/PNNL), never a BAS command. [ASO.md](ASO.md).
- **Action plan** — `actionplan.build_action_plan`: findings + `fault_economics` ($/yr) + `aso`,
  ranked worst-dollars-first; embeds in the audit report + config runs. [ACTIONPLAN.md](ACTIONPLAN.md).
- **Health scorecard** — `scorecard.build_scorecard`: per-category scores + an overall A–F grade.
  [SCORECARD.md](SCORECARD.md).

## Storage

`store.ParquetStore` — entity-keyed, hive-partitioned (site/year) Parquet with tag-filtered reads,
rollups, retention pruning, **year-partition pruning + column projection + cached catalog**. See
**[SCALE.md](SCALE.md)**. Flags: `read_long(columns=…, start/end)`, `rollup(freq, agg)`,
`prune(before_year)`.

## Reporting, integration & API

- **Audit** — `report.AuditReport` (ASHRAE/ACCA Standard 211, text/HTML) with prioritized findings.
- **Portfolio rollup** — `report.build_fleet_report` (cross-sectional EUI benchmark + fault rollup,
  ranked by recoverable $). Flags: `price`, `loads`, `peer_median_eui`, `top_n`.
- **Outbound** — `integrate`: CMMS work-orders (`finding_to_ticket` / `findings_to_tickets` → neutral
  dict), notifiers (`dispatch_findings` over `webhook_transport` / `email_transport`, with
  `slack_payload` / `teams_payload` formatters; severity filter + fingerprint dedupe), and findings/
  metrics export (`export_findings`: CSV / Parquet / JSON). All opt-in, from the findings layer —
  never writing to the BAS. Flags: `channel`, `min_severity`, `dedupe`, `dry_run`, `format`,
  `flatten_metrics`. See **[INTEGRATIONS.md](INTEGRATIONS.md)**.
- **Charts + dashboard** — the full **visualization pattern catalog A–J**: readiness ribbon (A),
  fault-annotated multi-trend (B), load carpet (E), data-quality dashboard (I), OAT cloud-shape
  scatter (D, `oat_scatter`), templated diagnostic scatters (G, `diagnostic`), **rules as a chart
  engine** (J, `evidence` — every rule renders its own proof), cohort small-multiples (C, `cohort`),
  M&V savings with uncertainty (H, `savings`), load profiles / load-duration curves (F,
  `loadprofile_chart`), plus the legacy scatters/CUSUM/energy-signature. `report.build_dashboard`
  assembles them into one self-contained HTML (matplotlib inlined, no web framework), embeds each
  finding's evidence (`rules=`), and offers a brush-able inline-SVG scatter (`interactive=True`).
  Flags: `sections`, `rank_by`, `top_n`, `normalize`, `rules`, `evidence`, `interactive`. See
  **[VISUALIZATION.md](VISUALIZATION.md)**.
- **Read-only API** — `api.server` (`python -m camber.api.server <store> [port]`): GET
  `/about` `/health` `/sites` `/points` `/history`. Env: `CAMBER_STORE` / `CAMBER_API_HOST` /
  `CAMBER_API_PORT`.

## Orchestration & distribution

- **Config-driven runs** — `config`: one JSON config (source → mapping → equipment → rules →
  report) runs a whole analysis: `python -m camber.config run.json`.
- **Plugins** — `plugins`: third-party rules / ingest adapters / report formats discovered via
  Python entry points (`camber.rules` / `camber.adapters` / `camber.reports`) or registered
  in-process, duck-typed against the existing protocols with per-plugin error isolation. See
  **[PLUGINS.md](PLUGINS.md)**.
- **Distribution & deployment** — slim multi-stage Docker image + compose bundle
  ([DOCKER.md](../DOCKER.md)), PyPI (`camber-toolkit`) + GHCR via the tag-driven release workflow,
  CI on 3.10/3.11, and reference Kubernetes / conda-recipe manifests (`deploy/`). See
  **[DEPLOY.md](DEPLOY.md)**.

---

See also: [ARCHITECTURE.md](ARCHITECTURE.md), [ECOSYSTEM.md](ECOSYSTEM.md) (fork-vs-depend
analysis), and the [ROADMAP](../ROADMAP.md).
