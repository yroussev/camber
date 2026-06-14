# CAMBER capabilities reference

A single index of what CAMBER 0.1 does, grouped by the building-analytics layers, with the key
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
- **Data quality** — `ingest.quality.assess` (coverage, gaps, flatline, outliers, composite score)
  and `clean`. Flags: `expected_freq`, `drop_outliers`.

## Semantic model

- **Roles + mapping** — `model.roles.Role` vocabulary; `model.mapping.MappingProvider` (alias +
  pattern → role). `resolve.resolve(equip, roles)` assembles a role-named frame. Flags: `resample`.
- **Entities + completeness** — `model.entities` (Site/Equip/Point) with equipment-template
  completeness validation.
- **Brick interop** — `interop.mapping_from_brick` / `roles_from_brick` (import); `interop.to_brick`
  and `interop.site_to_ttl` / `site_from_ttl` (export + whole-site round-trip). Flags: `backend`
  (`auto`/`rdflib`/`minimal`; rdflib via the `[brick]` extra).

## FDD — fault detection & diagnostics

Rule engine (`rules.base.Registry`, `rules.builtin.builtin_registry`); each rule consumes a
role-frame and returns a `Finding`. Run with `registry.run(name, equip_refs, mapping, min_trust=…)`.

- **Air-side (G36 + PNNL Re-tuning)** — simultaneous heat/cool, reheat (penalty + G36 minimization),
  SAT reset, overcooling (min-flow + severity), economizer / OA-fraction (incl. under-ventilation),
  night/weekend setback, duct-static, zone census. Per-rule flags (e.g. `threshold`, `min_oa_pct`,
  `occupied_only`).
- **Central plant & hydronic** — chiller kW/ton efficiency, chiller staging + multi-chiller fleet
  over-staging, cooling-tower approach, condenser-water reset, CHW/HW pump (riding-curve + VFD-min),
  CHW reset + low-ΔT, boiler summer-lockout + short-cycle. Flags include design targets
  (`design_kw_per_ton`, `max_starts_per_day`, …).
- **Sensor health / data trust** — `sensorhealth` (physical bounds, cross-sensor consistency,
  per-role trust roll-up + `trusted_roles` gate), `sensordrift` (bias / drift / tracking vs a
  reference), `mapping_confidence`. The runner's `min_trust` flag makes a rule decline when its
  inputs aren't trusted.
- **Prioritization & lifecycle** — `rules.triage`: `rank_findings` (severity, or a magnitude/cost
  key), `group_findings` (root-cause grouping), `FaultRegister` (new/ongoing/resolved across runs).
  Flags: `magnitude_key`, `actionable_only`.
- **Fault economics** — `fault_economics`: per-fault annual $ impact → rank by money. Flags:
  `params` (assumptions), `models`, `min_severity` (via `rank_by_cost`).
- **Accuracy** — `eval.benchmark` + `validation.metrics_with_ci` (Wilson CIs); see
  **[VALIDATION.md](VALIDATION.md)**.

## Sequence-of-Operations conformance

`soo` — a declarative clause engine (gated predicates over roles, JSON-authorable) measuring
operated-vs-designed behavior as a conformance %, with `soo_library` (packaged ASHRAE G36 clauses).
Flags: persistence window, per-class spec.

## M&V — measurement & verification

See **[MANDV.md](MANDV.md)**. Change-point models (`mandv.models`, 2P–5P + zero variants), LBNL
TOWT (`mandv.towt`), fit statistics + G14 fractional savings uncertainty (`mandv.stats`), CUSUM
(`mandv.cusum`), weather normalization (`mandv.weather`), normalized annual savings
(`mandv.normalized`), non-routine adjustment (`mandv.nonroutine`), Option-B retrofit isolation
(`mandv.retrofit_isolation`), CalTRACK alignment (`mandv.caltrack`). Flags: `confidence`,
`exclude_non_routine`, model `kinds`, `aggregate`.

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

## Domain analytics

`comfort` (Std-55 PMV/PPD), `iaq` (CO₂ ventilation adequacy), `cost`, `carbon`, `water` (irrigation
/ cooling-tower / leak), `loadprofile`, `pv` (+ `interop.pvlib_bridge`, `[pv]`),
`interop.psychro` (PsychroLib, `[psychro]`), `lighting`.

## Storage

`store.ParquetStore` — entity-keyed, hive-partitioned (site/year) Parquet with tag-filtered reads,
rollups, retention pruning, **year-partition pruning + column projection + cached catalog**. See
**[SCALE.md](SCALE.md)**. Flags: `read_long(columns=…, start/end)`, `rollup(freq, agg)`,
`prune(before_year)`.

## Reporting, integration & API

- **Audit** — `report.AuditReport` (ASHRAE/ACCA Standard 211, text/HTML) with prioritized findings.
- **Portfolio rollup** — `report.build_fleet_report` (cross-sectional EUI benchmark + fault rollup,
  ranked by recoverable $). Flags: `price`, `loads`, `peer_median_eui`, `top_n`.
- **Outbound** — `integrate`: `finding_to_ticket` / `findings_to_tickets` (neutral CMMS dict),
  `webhook_transport` / `collect_transport`, `Notifier`. Flags: `actionable_only`, `site`.
- **Charts** — `charts`: heating-vs-cooling scatter, reheat boxes, zones, load carpet, CUSUM, energy
  signature. All draw onto a supplied Axes.
- **Read-only API** — `api.server` (`python -m camber.api.server <store> [port]`): GET
  `/about` `/health` `/sites` `/points` `/history`. Env: `CAMBER_STORE` / `CAMBER_API_HOST` /
  `CAMBER_API_PORT`.

## Orchestration & distribution

- **Config-driven runs** — `config`: one JSON config (source → mapping → equipment → rules →
  report) runs a whole analysis: `python -m camber.config run.json`.
- **Distribution** — slim multi-stage Docker image + compose bundle ([DOCKER.md](../DOCKER.md)),
  PyPI (`camber-toolkit`) + GHCR via the tag-driven release workflow, CI on 3.10/3.11.

---

See also: [ARCHITECTURE.md](ARCHITECTURE.md), [ECOSYSTEM.md](ECOSYSTEM.md) (fork-vs-depend
analysis), and the [ROADMAP](../ROADMAP.md).
