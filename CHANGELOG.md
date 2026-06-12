# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.1.0] — unreleased

First public pre-release.

### Added

- **Ingest** — per-point CSV, wide CSV, and a Project-Haystack `hisRead` client;
  per-point data-quality scoring with an auditable cleaning trail; valve/damper
  unit normalization (0–1 vs 0–100).
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
  `[mqtt]`/paho-mqtt — subscribe + buffer + shape), and BACnet (`camber.ingest.bacnet`,
  `[bacnet]`/bacpypes3 — Trend-Log history + present values), incl. **experimental,
  certificate-gated BACnet/SC** (`wss://`+TLS, hub URI + operational cert config). Each is
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
  and `read_role_frame` uses a fast plain pivot when observations are unique. A synthetic
  generator + benchmark (`camber.store.bench`, `python -m camber.store.bench`) and
  [docs/SCALE.md](docs/SCALE.md) — a single-equipment read stays ~flat as the portfolio grows.
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

[0.1.0]: https://github.com/yroussev/camber/releases
