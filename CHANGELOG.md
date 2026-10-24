# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## Older releases

Releases before 0.106.0 are archived under [`docs/changelog/`](docs/changelog/index.md):

- [0.103.0 to 0.105.0](docs/changelog/changelog-0.103-0.105.md)
- [0.98.0 to 0.102.0](docs/changelog/changelog-0.98-0.102.md)
- [0.90.0 to 0.97.0](docs/changelog/changelog-0.90-0.97.md)
- [0.60.0 to 0.89.0](docs/changelog/changelog-0.60-0.89.md)
- [0.1.0 to 0.59.0](docs/changelog/changelog-0.1-0.59.md)

## [Unreleased]

### Added
- **The capability registry, `camber.capabilities` (#275), provisional.** One read-only core the
  MCP server and, later, the A2A server expose as thin adapters. A `Capability` has JSON Schema
  2020-12 input and output schemas and a handler returning a `CapabilityResult` (`data`, a `text`
  citing `[F1@ctx]` fact ids, `facts`, `banners`, `truncated`). `Registry.call` enforces the
  policy in one place: input validation, a required per-caller facility allowlist
  (`not_allowed`), research-only (NC/ND) data refused with no override (`refused_licence`),
  no-persist runs, a `min_trust` floor of 0.5, caps on rows (truncated with a banner), bytes
  (`too_large`) and run time (`timeout`), citations checked with `camber.agent.verify`, and any
  other failure reported as `internal` with no local path in the message. `default_registry()`
  carries two capabilities, `facilities` and `points`; adding one is a single `_cap_<name>.py`
  module. See docs/CAPABILITY-REGISTRY.md.
- **A no-persist config run (#276).** `run_config(..., persist=False)` and
  `run_config_file(..., persist=False)` compute the same findings, drift and report but write
  nothing: the `faults` fold runs against an in-memory copy of the stored history and is not saved
  (`run.faults["persisted"]` is `False`), the config's `report.out_text` / `out_html` files are not
  written, nothing is listed in a workspace manifest, the store does not write back its catalog
  cache, and an M&V billing entry fetches no weather. `RunResult.persisted` says which it was. On
  the CLI: `camber run --no-persist` and `camber report --no-persist` (the `--out` file is still
  written). A run inside the private `camber._nopersist.no_persist()` scope is always no-persist;
  the capability registry uses it.
- **`camber mcp`: a Model Context Protocol server over stdio (#277, provisional).** AI clients
  such as Claude Desktop and the `claude` command line start it as a subprocess and see CAMBER's
  capability registry as read-only tools and prompts. It speaks both 2026-07-28 (stateless: the version and
  client capabilities in each request's `_meta`, `server/discover`, `resultType` on every result,
  `ttlMs` and `cacheScope` on the list results) and 2025-11-25 (the `initialize` handshake;
  2025-06-18 accepted too), so newer and older clients both connect. The store, the facility
  allowlist (`--allow FACILITY`, repeatable, or `--allow-all-active`, one of them required),
  `--config` and `--min-trust` come from the launch, never from the client. A refused or failed
  call the model can act on (a facility outside the allowlist, research-only data, bad
  arguments, nothing found, too large, timed out) is a tool result with `isError: true`; an
  unknown tool or a malformed request is a JSON-RPC error. Standard library only (the `mcp` extra
  is an empty marker); stdout carries protocol messages only, logs go to stderr. New package
  `camber.mcp` (`McpServer`, `serve`, `serve_stdio`, `build_context`, `launch`). See
  docs/MCP.md.

## [0.109.1] — 2026-10-24

**0.109.1: a store fix (#274).** An append now stages its Parquet parts and renames each into place, so a process reading the store while another writes to it can no longer open a half-written part.

### Fixed
- **A concurrent reader can no longer open a half-written Parquet part (#274).** An append wrote
  its parts straight into the live partitions, so a reader listing the store mid-write could hit
  `ArrowInvalid: Parquet file size is 4 bytes`. The parts are now written under
  `<store>/_staging/append-<pid>-<token>/` and renamed into place one by one; an interrupted
  append's scratch shows up with the other store leftovers.

## [0.109.0] — 2026-10-24

**0.109: the promised 0.108 removals and a forwarder that sends only what is new (#266, #271, #272).** **Breaking (#271):** an unknown edge-config key is refused with an error that names it and the closest known key, on every `camber edge` command; 0.108 only warned. **Breaking (#272):** `supply_air_reset_compliance` findings no longer carry the `pct_below_g36_target`, `g36_target_median` and `pct_above_g36_target` aliases; read `pct_below_target`, `target_median` and `pct_above_target`. **Behaviour change (#266):** the edge forwarder keeps a per-point high-water mark of the last acknowledged observation in `<spool>/high_water.json`, so an idle poll sends nothing and a poll sends only the months with a newer or revised observation; delivery stays at-least-once, and `Forwarder(high_water=False)` keeps the full read.

### Changed
- **Breaking: an unknown edge-config key is refused (#271).** `load_config` raises a
  `ValueError` for a key it does not know, at the top level or in a file source or sink, naming
  the key and the closest known one (`'spool_dri' (did you mean 'spool_dir'?)`), so every
  command that reads an edge config (`edge run`, `send-once`, `status`, `selftest`, `requeue`,
  `compact`, `decommission`) exits 1 with that error before it builds anything. 0.108 ignored the
  key with a WARNING and a `FutureWarning`. Fix the key, or prefix a comment key with `_` (`note`,
  `comment` and `description` stay comments).
- **Behaviour change: a forwarder poll sends only what is new (#266).** The forwarder keeps a
  high-water mark per source point, the last observation the sink acknowledged, in
  `<spool>/high_water.json`. A poll reads each point from the start of its mark's month and sends
  a month only when one of its points has a newer observation or a revised last one; the open
  month is still sent whole, so the landing replaces its stored part as in 0.108, and a revised
  last observation (the hour that was in progress) is sent. An idle poll sends nothing.
  `poll_once(since=...)` still reads and sends the window given. `Forwarder(high_water=False)`
  keeps the pre-0.109 full read.

### Added
- **The forwarder's high-water mark (#266)** (`camber.edge.high_water`: `HighWater`,
  `read_high_water`, `config_fingerprint`). The mark advances only when a drain acknowledges the
  part (a part's marks wait in the file until then, and are dropped if the disk cap evicts it),
  survives restarts and is written atomically under the spool lock. A missing file means a full
  read, as before; an unreadable one, or a change of facility, source, mapping, `resample` or
  `timezone`, is logged as a `WARNING edge.high_water`, read in full and rewritten. `edge status`
  shows the mark (`high_water` in `--json`), and the poll result counts the `unchanged` months
  and the points `advanced`. A source whose `load_points` accepts `since=` is given the earliest
  month start, so it can read less. Delivery stays at-least-once.

### Removed
- `EdgeConfig.warnings` and the `config_warnings` key of `edge status --json`, which carried the
  unknown config keys 0.108 ignored (#271); `edge status` and `edge selftest` no longer print
  `CONFIG WARNING` lines.
- **The `*_g36_target` SAT-reset metric aliases (#272), breaking.** `supply_air_reset_compliance`
  findings no longer carry `pct_below_g36_target`, `g36_target_median` or
  `pct_above_g36_target`, the pre-0.108 names 0.108 kept as aliases for one release. Read
  `pct_below_target`, `target_median` and `pct_above_target` (the same values). The cost estimate
  and the cause-chain linking read only `pct_below_target`, so a finding saved before 0.108 is no
  longer priced from the old key. The `SATResetComplianceResult` fields of `camber.g36_reset` are
  unchanged. The `air-sat-reset` workbook exercise reads the new key (same answers).

## [0.108.0] — 2026-10-17

**0.108: a second audit and the 0.107 follow-ups — 42 issues across the rules, the edge and ingest path, legal holds and the facility lifecycle, interop, charts and reports — plus a redesigned synthetic G36 fleet, three new synthetic lab cases and sensor references for any point (#226, #228–#265, #267–#269).** **Behaviour changes (edge, #228, #233, #245, #249):** presigned uploads go to the bucket's `_inbox/` by default (`inbox=False` keeps the old layout); a growing month replaces its earlier part, an overlap that agrees is merged, and a conflicting overlap is quarantined; permanent sink errors dead-letter after five attempts (`edge requeue`), while a 401/403 is retried with an "auth failing" warning; unknown edge-config keys warn now and will be refused in 0.109. **Behaviour changes (lifecycle, #250–#252, #255, #256):** inside a workspace the store and registry deletes refuse a held or non-active facility, need a reason and are audited; config runs record no state for archived or purged facilities; restore is all-or-nothing; a rename cannot take another facility's name; crash recovery never deletes a held facility's data. **Behaviour changes (rules):** `chiller_staging` uses a size-relative run threshold (or compressor status) instead of a fixed 2 kW (#235); short-cycle rates are per observed day (#237); tower drift declines when its baseline was fitted on another load (#234); `zones_heat_cool_census` no longer faults a fleet on one or two boxes (#241). **Workbook:** the synthetic G36 fleet generates version 3, so the `zone-g36-fleet` answers are re-recorded (#226); new exercises `rcx-cause-chains`, `air-excess-oa-cost` and `data-sensor-conditional`. The RCx report takes a reference reading for any sensor (`report.rcx.references`, #267), and a distrusted cost-only input puts that issue's dollars at risk (#268). Also: BACnet trend logs read the newest records and keep devices apart (#243, #248), Haystack history reads cross DST (#244), Brick export round-trips names and parts (#253, #254), reports are written as UTF-8 with escaped point names (#259, #260), and evidence charts shade the samples their rule flagged (#258, #262). Changelog 0.103–0.105 archived.

### Added
- **A worked example of the RCx report's generated prose (#231).**
  `examples/rcx_prose/prose.json` is a prose file for the synthetic RCx demo (the golden report's
  store and config). It fills the executive summary, which is shown boxed with its cited facts,
  and one issue slot whose last sentence cites nothing, so that slot falls back and Appendix A
  records it. docs/RCX-REPORT.md walks through it ("A worked example") with a screenshot,
  `docs/img/shots/rcx-prose.png`. `tests/test_rcx_prose_example_231.py` renders the example and
  checks the prose box, the cited facts, the fallback and its Appendix A row against the docs. The
  default golden report is unchanged.
- **Docs screenshots from per-entry modules.** A module under `scripts/docs_figures.d/` can
  register a browser screenshot with `df.screenshot(path, what)`; `--screenshots --only <path>`
  takes just those. Dev tooling, not packaged.
- **`camber edge reconcile` finds and removes row duplicates already in the store (#229).** A
  store reconciliation lists the groups of parts of one facility that hold identical rows under
  different keys (left by a landing before 0.107, which had no row check), with their keys,
  sizes and landing times and the copy it keeps, the oldest (`row_duplicates` and
  `duplicate_copies` in the report; `camber.edge.landing.row_duplicates`). `edge reconcile
  --apply --yes --reason R` deletes the other copies under the store lock, audited as
  `edge.reconcile.dedupe` before anything is deleted (`camber.edge.quarantine.remove_row_duplicates`).
  A facility under a legal hold has its extra copies moved to quarantine instead, and facilities
  that do not accept data are left alone.
- `camber.edge.row_digest.PartitionView` and `read_rows`, the row comparisons behind the landing,
  and a timestamp span (`ts_min`, `ts_max`) in each `_edge_rows.json` entry.
- **`camber edge requeue` and the spool's dead-letter folder (#245).** A batch the sink refuses
  permanently is kept in `<spool>/dead/` with a record of why (see Changed);
  `camber edge requeue <edge.json> [--key K] --apply` queues it again once the cause is fixed
  (`Spool.dead_letters`, `Spool.requeue_dead`, `Spool.stuck`). `camber edge status` shows the
  stuck and dead batches, and `--json` prints them. The forwarder's poll result counts
  `already_spooled`, `rejected` and `dead` batches.
- **Edge config: `timezone` and `dead_letter_after` (#245, #249).** `timezone` (or
  `CAMBER_EDGE_TIMEZONE`) is the site's IANA zone, passed to the file sources (`source.timezone`
  overrides it); `dead_letter_after` (default 5) is how many permanent rejections a batch gets.
- **BACnet: the trend-log read count is configurable (#243)** (`Bacpypes3Client(trend_records=)`,
  `BacnetClientConfig.trend_records`, `read_trend_log(max_records=)`), and a `BacnetPoint`
  carries its device's `device_instance` and `address` (#248).
- **Haystack: `HaystackAdapter(timezone=, strict_timezone=)`, `parse_his_grid(timezone=,
  strict_timezone=)` and `haystack_tz` (#244).**
- **A synthetic fleet whose faults reach the boxes, and the `rcx-cause-chains` exercise
  (0.108, C1).** The new catalog entry `synthetic-g36-rcx-chains` (generator `g36_rcx_chains`)
  runs the G36 fleet model with each air handler's supply fan speed and cooling valve, as five
  air handlers: a healthy one, a healthy one with one stuck box damper, a cooling coil that
  cannot make supply air below 63 °F, a weak fan that cannot make more than 0.35 in. w.c., and
  a SAT reset disabled at 55 °F with two single-maximum reheat boxes. Its run template declares
  the served-by topology and the reheat-coil sizes, so the RCx report shows the #196 cause
  chains: supply air too warm (5 of 6 boxes), duct static too low (5 of 6 boxes) and supply air
  too cold (2 of 6 boxes, costed at its largest member), while the stuck box stays its own
  issue. Without the topology the same run ranks 22 issues instead of 5 and counts the reheat
  twice. The workbook exercise `rcx-cause-chains` walks through it, with an instructor key and a
  figure.
- **`unmet_setpoint_hours` and `airflow_tracking` on the G36 fleet (#226).** The
  `synthetic-g36-vav-fleet` run template and the `zone-g36-fleet` exercise configs run both
  rules, and the entry labels the boxes behind `AHU__starved` and `AHU__not_responding`
  (`starved_by_air_handler`) so `camber datasets score` scores them. With the boxes' symptoms in
  the run, the RCx report links them to `AHU__not_responding`'s reset findings (6 of 6 boxes,
  for both resets).
- `reheat_minimization_g36`'s `min_reheat_hours` parameter (default 2 h) and its metric
  `n_reheat_samples` (#238). `cohort_starvation`'s `min_present_frac` parameter and the
  `group_skipped_cycles` metric and result field (#239). `zones_heat_cool_census`'s
  `min_heating_frac`, `max_driver_zones` and `persistent_frac` parameters and its metrics
  `heating_frac_when_mixed`, `persistent_heating_zones`, `driver_zones`,
  `pct_hours_mixed_without_drivers` and `severity_capped` (#241).
- **Excess outside air priced end to end, and the `air-excess-oa-cost` exercise (0.108, C2).**
  The new catalog entry `synthetic-ahu-excess-oa` (generator `ahu_excess_oa`, catalog file 102)
  generates two variable-air-volume air handlers with trended outdoor and return humidity, supply
  airflow, a heating valve and occupancy, every 15 minutes for six weeks of a humid spring: one
  holding about 45 % outside air against its declared 20 % minimum in all weather, one
  economizing below a 65 °F high limit. The first is ingested a second time without its airflow
  point (`AHU__excess_oa_no_flow`). Its run template prices the excess with the 0.107 estimator
  (#195): $731/yr on the enthalpy path (cooling and heating), $255/yr if the humidity points are
  dropped, $0 for the economizer, and declines without airflow or a declared minimum. The
  workbook exercise `air-excess-oa-cost` walks through it, with two config variants
  (`--no-min`, `--design-oa`), an instructor key and a figure.
- **A biased sensor, conditional findings and the at-risk total: the `data-sensor-conditional`
  exercise (0.108, C3).** The new catalog entry `synthetic-ahu-sensor-bias` (generator
  `ahu_sensor_bias`, catalog file 103) generates two identical air handlers on a fixed 55 °F
  supply setpoint, one with a supply-air sensor reading 3 °F high, and a calibrated duct logger
  in each for four days (`LOGGER__<unit>`). The false reading makes `leaking_valve` fault, the
  loop it drives makes `supply_air_control` pass, and the sensor-trust layer scores it trusted.
  With the loggers as `report.rcx.references` (#267, below), the bias becomes a
  `sensor_drift:supply_air_temp` finding: the unit's dependent findings turn conditional and its
  reset estimate moves to the RCx report's at-risk total ($5,250/yr), while the healthy twin's
  identical finding stays firm. The workbook exercise `data-sensor-conditional` walks through it
  (the `--no-reference` variant is the report before the references), with an instructor key
  and a figure.
- **Sensor references for any point in the RCx report (#267).** `report.rcx.references` maps an
  equipment to `{role: reference}`: another equipment's reading in the store facility (a duct
  logger ingested as its own equipment), or a CSV file, with an optional label and comparison
  thresholds. Each comparison is the OAT reference's (bias, drift, correlation), listed in the
  data section; at warn or fault it is a `sensor_drift:<role>` issue on that unit, and the unit's
  findings that read the point become conditional, their dollars at risk. A constant offset
  that no trust check can see now reaches the report through the config alone.
  `references["*"]["oat"]` is an alias of `oat_reference`. See docs/RCX-REPORT.md, "Sensor
  references".
- `Issue.cost_conditional_on`, `Issue.cost_at_risk` and the `n_cost_conditional` total (#268);
  the `supply_air_reset_compliance` metrics `target_source`, `pct_below_target`,
  `target_median` and `pct_above_target` (#269).

### Changed
- **A rule's docstring citations are checked against its references (#232).**
  `tests/test_references.py` now checks that every PNNL guide or Re-tuning chapter a rule's class
  docstring cites ("PNNL Re-tuning Ch.5/7") is in that rule's `RULE_REFERENCES` entry.
  `zones_heat_cool_census` now also links chapter 5, whose discharge-air temperature section lists
  reheating in the cooler zones among the effects of a low discharge-air temperature. Its
  Learn-more links gain that chapter. docs/REFERENCES.md explains both changes.
- **Behaviour change: the upload inbox is the default (#233).** `route_key(portfolio, key)` now
  signs an accepted upload into the bucket's `_inbox/` prefix, so every upload passes the
  landing's row checks before it reaches the store; `inbox=False` keeps the straight-to-store
  layout of 0.107. Upgrade the cloud landing first (with `camber edge land <bucket>/_inbox
  --apply` scheduled), then the brokers; forwarders need no change. docs/EDGE-DEPLOY.md gives the
  order.
- **Behaviour change: a growing month replaces its earlier part (#228).** The forwarder re-sends
  the month in progress on every poll with more rows. When a landed object holds every row of
  the stored parts it overlaps, `camber edge land` replaces them instead of storing it beside
  them, so a month keeps one part and reads never see a row twice. The partition is swapped in
  whole (a reader or a crash sees the old partition or the new one), the replacement is audited
  in the `edge.land` records and recorded in the row-digest cache, and under a legal hold the
  replaced part is copied into quarantine first. The last observation of each series in the
  stored part may differ: that is the hour still in progress when it was polled (a resampled mean
  of the samples so far), which the next poll revises. An object whose rows are all stored
  already (a late, smaller poll) is a `duplicate`.
- **Behaviour change: a conflicting overlap is quarantined (#228).** An object that shares an
  observation (timestamp, equipment and role) with a stored part without holding all of its
  rows, such as a month whose history changed at the source, goes to quarantine as a `conflict`
  with the parts it overlaps, instead of landing beside them. Releasing it stores it beside them.
- **Behaviour change: an empty folder is an empty store everywhere (#233).** `camber store
  verify` reports 0 parts and exits 0 on an empty directory, and `store normalize` and
  `migrate-partitions` do nothing and create nothing there, not even a lock file, as `camber
  serve` already accepted it. A missing path, a file or a directory of other things is still
  refused. `camber serve` and the store commands now share one definition of a store directory.
- **Chiller run gate in `analyze_chiller_staging` and `analyze_chiller_staging_fleet` (#235).**
  Their `min_power_kw` keyword now defaults to `None`, which reads a chiller as running above a
  tenth of its own 95th-percentile power (the new `run_frac` keyword,
  `camber.schedules.CHILLER_POWER_RUN_FRAC`), or by a `Status` column when one is present. Pass
  `min_power_kw=2.0` for the fixed 2 kW gate used before 0.108.
- **Tower drift baselines record their load (#234).** A `cooling_tower_approach_drift` or
  `cooling_tower_fan_effort_drift` baseline frozen from 0.108 records the load it was fitted on
  (`load_source` in the stored coefficients). A baseline frozen earlier has no `load_source`: it
  is still scored, as fitted on the current load, with a caveat that asks for a refreeze if the
  load has changed since.
- **Behaviour change: a legal hold refuses the store and registry delete primitives inside a
  workspace (#250).** `ParquetStore.drop_facility`, `prune` and `drop_partition` and
  `FacilityRegistry.remove` now refuse a facility under a legal hold (`LifecycleError`, nothing
  deleted). They also need a `reason=` (`ValueError` without one) and are audited as
  `store.drop_facility`, `store.prune` (one line per facility) and `store.drop_partition`, with
  `facility.remove` carrying the reason given. `drop_facility` and `remove` also refuse a facility
  that is not `provisioning` or `active` (suspended, offboarding, archived), as a dataset purge
  does: an archived facility leaves through `camber facility purge`. The rule lives with the 0.107
  dataset rules in
  `camber.datasets._lifecycle`, so every caller shares it. There is no override flag: release the
  hold first. A plain store outside a workspace is unchanged.
- **Behaviour change: store writes cannot change a facility's privacy flag or display name inside
  a workspace (#250).** `FacilityRegistry.register`, and so a store write's metadata, refuses to
  change `private`, `display_name`, `archive`, `offboarding` or `edge_devices`. Before this,
  `write_role_frame(..., private=False, display_name=...)` turned a private facility public and
  renamed it, with no audit line. Use `camber facility private|rename`, which audit the change. A
  facility created by its first write may still start `private` or with a display name; its
  `facility.register` line records them.
- **Behaviour change: config runs write no state for an archived or purged facility (#252).** A
  run with a `faults` fold is refused up front (`LifecycleError`), and so are `camber drift
  freeze|accept` and `camber mv freeze|rebaseline|adjust --apply`. A report such a run writes is
  still written, but it is not recorded in the facility manifest, and a warning says so. A run
  with no fault fold still runs. A legal hold alone does not block these writes: it blocks only
  deleting or replacing data, so a held active facility keeps folding faults and freezing
  baselines.
- **Behaviour change: `facility rename` refuses another facility's labels (#255).** The new name
  may not equal, exactly or loosely, another facility's id, registered name, or current or past
  display name, live or tombstoned.
- **Behaviour change: the Brick export writes encoded names and per-equipment parts (#253,
  #254).** `to_brick`, `site_to_ttl` and `site_to_223` percent-encode every character of a name
  outside `[A-Za-z0-9_-]` in its local name (`bldg:SA%20Temp`). The Brick output adds an
  `rdfs:label` with the exact name and names each part node after its equipment
  (`bldg:AHU_1.Cooling_Coil`). Models written by other tools are read as before.
- **Behaviour change: a rolling window merges with the part it overlaps.** A source that holds
  only a window (a BACnet trend log is a ring buffer, a Haystack range can roll) sends a later
  poll that lacks the oldest rows of the stored part; that landed as a `conflict`. Now, when
  every observation the new object shares with the stored parts it overlaps carries the same row
  (the stored part's last observation of each series may still be revised), `camber edge land`
  stores the union of their rows as one canonical part named by its own bytes, swapped in with
  the same crash-safe partition swap as a replacement. It is audited in `edge.land` as `merges` /
  `merged`, with the stored key and the counts of stored rows kept, new rows and revised rows.
  Any disagreement in the overlap is still a `conflict`, an exact superset is still a
  replacement, duplicates are still quarantined, and under a legal hold the parts a merge
  supersedes are copied into quarantine first (`camber.edge.row_digest.merge_parts`).
- **Behaviour change: a permanently refused batch is dead-lettered and no longer blocks the spool
  (#245).** A 4xx answer other than 401, 403, 408, 425 and 429, or a validation error, is permanent:
  the drain counts it and goes on with the batches behind it (it used to retry the head batch
  forever and send nothing else). After `dead_letter_after` rejections the batch moves to
  `<spool>/dead/` with a `<file>.json` record, a `dead` journal record and a WARNING. Transient
  errors still stop the cycle and back off. Dead letters count toward the disk cap, and `edge
  decommission` counts them as unacknowledged (`camber.edge.sink.is_permanent_error`,
  `is_permanent_status`, `PermanentSinkError`).
- **Behaviour change: SDK sinks send a compact metadata subset (#245).** The S3, Azure and GCS
  sinks send `sha256`, `rows`, `rows_sha256`, `schema_version`, `year` and `month`,
  JSON-encoded and ASCII-safe (`camber.edge.sink.sink_metadata`), instead of the whole manifest as
  Python reprs, which exceeded S3's 2 KB metadata limit for a site with a few hundred equipment
  and broke on non-ASCII names. The full manifest stays in the spool journal.
- **Behaviour change: the spool keeps one copy of each batch (#246).** `Spool.enqueue` skips a
  batch whose key and content sha256 are already pending or dead-lettered, so a forwarder that
  re-reads its window while offline no longer queues a copy per poll.
- **Behaviour change: the edge config is validated (#249).** `load_config` refuses a non-boolean
  `quality`, an `interval`, `spool_max_bytes` or `sink.timeout` of 0 or less, an unknown
  `wire_format`, `part_layout`, sink kind, `resample` or `timezone`, and, as secrets, any value with
  a signature or credential in it whatever its scheme (an Azure SAS `sig=`, `X-Goog-Signature`,
  `signature=` / `token=` parameters, `AccountKey=`) and any URL with a password in its userinfo.
  Keys starting with `_`, and `note`, `comment`, `description`, are comments. `Spool` refuses a
  `max_bytes` of 0 or less.
- **Deprecation: unknown edge-config keys warn in 0.108 and will be refused in 0.109.** A key
  `load_config` does not know, at the top level or in a file source or sink (a typo such as
  `spool_dri`, which leaves the default in force), is ignored with a WARNING that names the
  closest known key: logged, raised as a `FutureWarning`, kept in `EdgeConfig.warnings` and shown
  by `edge status` (`CONFIG WARNING`; `config_warnings` in `--json`) and `edge selftest`. The
  forwarder keeps running. **CAMBER 0.109 will refuse unknown keys**: fix or remove them now, or
  prefix a comment key with `_`.
- **Behaviour change: a 401 or 403 from the sink is transient, never dead-lettered.** An
  authentication or authorization refusal is a fleet-wide credential or configuration problem
  (an expired presigned URL template, a revoked key), not a bad batch: the drain stops the cycle
  and backs off, as for a network error, and logs a loud `edge.spool.auth AUTH FAILING since
  <time>` WARNING. `edge status` shows `AUTH FAILING since <time>` (`auth_failing` in `--json`;
  `Spool.auth_failing`) until a batch is delivered again. Other 4xx answers and validation errors
  stay permanent (`camber.edge.sink.is_auth_error`, `is_auth_status`, `AUTH_STATUSES`).
- **Behaviour change: duplicate BACnet point names are refused (#248).** `BacnetSource` raises
  instead of silently keeping one of two points with one name, and `discovery_to_points` names
  objects that share a name on different devices `<device name>_<object name>` (then
  `_<type><instance>`), with a warning.
- **Behaviour change: Haystack history is on the site's wall clock (#244).** `parse_his_grid`
  returns a tz-naive index through `parse_timestamps`, like every other adapter, using the
  adapter's `timezone`, else the Haystack tz name on the timestamps (`New_York`), instead of a
  fixed-offset aware index.
- **Behaviour change: `edge land` isolates per-object errors (#247).** One object that fails is
  reported in the report's `errors` and the `done` audit record, the run goes on, and `land
  --apply` exits 1. `edge reconcile --apply` does the same.
- **The G36 fleet generator is at version 3 (#226).** On version 2, `unmet_setpoint_hours` and
  `airflow_tracking` flagged the healthy air handler's boxes: its zones sat 3-5 °F above their
  cooling setpoint for up to 28 % of occupied hours, with dampers wide open at 40-56 % of their
  airflow setpoint. The rules were right and their defaults stand: an unmet tolerance of 4 °F
  still left a healthy box 18 % unmet, and `airflow_tracking` needed half the setpoint. The generator was the cause: each perimeter zone
  peaked alone, and a correct reset that ignores two requests (G36's I = 2) left that zone
  warm. One trim-and-respond cycle an hour could not follow the morning load, and the duct
  static trimmed to its minimum overnight. Version 3 gives the zones loads that rise and fall
  together, takes three reset cycles per trended hour, holds both resets at their start value
  while the fan is off (G36 §5.1.14), and holds a tier-1 request until the loop or the damper
  falls below 85 % (G36's hysteresis). The blocked inlet of `rogue_static` moves from box 5 to
  the north box 4, and the near miss's extra load becomes 0.008 of its cooling maximum on weekday
  mornings (it was 0.06 all day). Every labelled fault is still detected and the label scores
  hold. **Workbook:** the `zone-g36-fleet` answers are re-recorded (key, instructor block, page
  and config comments). The headline moves: the SAT rogue sends all of its air handler's SAT
  requests (92.5 % before) and the static rogue is `VAV4__rogue_static` (was `VAV5`, 95.9 %, now
  100 %); SAT cohort starvation on `AHU__not_responding` 57.8 % → 83.1 % of active cycles, static
  71.1 % → 86.1 %; `cohort_space_temp` adds four `AHU__starved` boxes to its outliers; the
  healthy SAT reset swings 5.15 → 6.82 °F; `VAV2__reheat` and `VAV4__reheat` above minimum
  69.7 % → 56.5 % and 93.1 % → 72.4 % (still faults); the building-wide census 98.3 % → 100 %
  mixed, the `reheat` run 18.3 % → 8.9 %, the `heat_cool` run 98.3 % → 85.0 %; overall detection
  73 % → 22 of 23, with `unmet_setpoint_hours` (13 of 14) and `airflow_tracking` (14 of 14)
  newly scored; the near miss's zone 1 holds the binding request on 45.4 % → 44.4 % of active
  cycles and sends 43.0 % → 30.8 % of the requests. On the healthy fleet the north zone's z is
  now −2.2 (it was −7.6, excused by the 1 °F floor), so that answer is gone.
- **Behaviour change (#238):** `reheat_minimization_g36` reports `info`, with a caveat, below
  `min_reheat_hours` of occupied reheat, so one sample no longer decides the verdict.
  `n_reheat_hours` now counts hours (samples × the sample interval), and heating-loop samples
  that read nothing are left out instead of read as a low loop.
- **Behaviour change (#239):** `sat_` and `static_cohort_starvation` judge a cycle only when at
  least `min_present_frac` (default `cohort_frac`) of the group's zones have data, and say how
  many cycles they skipped. One box reporting through the others' outage is no longer a starved
  cohort.
- **Behaviour change (#241):** `zones_heat_cool_census` faults only when a meaningful share of
  the fleet heats in the mixed hours (`min_heating_frac`, 10 %), and is at most `warn` when one
  or two boxes drive the result in a larger fleet. It names the zones that heat persistently.

### Fixed
- **A distrusted cost-only input left an estimate firm (#268).** The points a rule reads only
  to price its finding (`roles_cost`: the excess-outside-air rules' humidity, supply air,
  heating valve and occupancy) never made the finding conditional, so an excess-OA estimate
  priced on stuck humidity sensors stayed a firm $/yr. Now the verdict stands but the issue's
  dollars move to "at risk pending a sensor fix", with the cause named on the issue page.
- **The SAT-reset target is named by its source (#269).** `supply_air_reset_compliance` said "vs
  G36 target" even for a configured or site-sequence reset map. Its summary, metrics and cost
  basis now name the source ("vs configured target", `sat-below-configured-target->reheat`); a
  G36-default map reads as before, and the old `*_g36_target` metric keys stay as aliases until
  0.109.
- **Appendix B named no airflow on a missing-minimum decline (#269).** The excess-OA estimate
  returned before resolving its airflow; it now names the source it would use (the trended
  supply airflow, or the configured one) on that decline too.
- **`leaking_valve` cited a chapter that does not cover it (#232).** Its docstrings cited
  Re-tuning chapters 5 and 7, but the rule judges an air handler's coil valves, which chapter 5
  covers. Chapter 7's reheat-valve leakage is a terminal box's. The docstrings now cite chapter 5,
  which matches the rule's mapping.
- **The `overcooling_min_flow` evidence shades the rule's own samples (#258).** It shaded every
  hour more than 3 °F below the cooling setpoint, occupied or not, so on a box overcooling at its
  minimum flow it could shade only the nights and none of the flagged hours. `analyze_overcooling`
  now returns the samples behind its percentages (`masks_out`), and the chart shades the occupied
  samples the verdict counts: satisfied on cooling, at minimum flow and, when a heating valve is
  trended, with the valve open. The chart also plots the airflow against its minimum setpoint and
  the heating valve, scaled 0–1 per unit (`normalize="unit"` in `fault_multitrend`) so each
  setpoint stays comparable with its point. `OvercoolingMinFlow.violation_masks` exposes both
  masks.
- **Reports are written as UTF-8 on every platform (#259).** `camber report`, the RCx and M&V
  reports, the dossier, `camber fleet`, the drift report, the JSON outputs and the config's
  `out_text` / `out_html` were written in the locale's encoding: under a cp1252 or Latin-1 locale
  a report with `≥` in its text failed and left an empty file, and elsewhere the bytes did not
  match the page's `utf-8` charset. Every text write now names `encoding="utf-8"`.
  `camber fleet --out` writes a whole HTML document with its charset
  (`FleetReport.to_html_document`); `to_html` stays a fragment.
- **The interactive dashboard escapes point names (#260).** The scatter's axis labels and the
  carpet's title were put into the page as written, so a column name holding HTML became markup
  in a shareable page.
- **The fleet report's most common findings are the same on every run (#261).** Ties were ordered
  by the hash seed; they now break by rule name.
- **Evidence spans cover every flagged sample and stop at data gaps (#262).** A run of flagged
  samples was shaded only to its last sample, so two flagged hours shaded one, and a run bridged
  missing data. Each flagged sample is now shaded over its own interval, to the next sample, and
  a run splits where a step is longer than 2.5 times the median step. This applies to the
  evidence charts, the dashboard and the RCx trend charts.
- **Load carpets keep days with no data (#263).** The static and the interactive carpet had a
  column only for days with data, so an outage disappeared and the date axis was not linear.
  Both now have a column for every calendar day from the first sample to the last.
- **Point names with `$` no longer break charts (#264).** Matplotlib read a `$` in a point or
  equipment name as math text: the evidence chart was silently dropped, a name such as
  `VAV $5 to $10` was garbled, and a dashboard with such a column failed. Every data-derived
  label, legend entry, tick label and title now escapes `$`. A dashboard or site-report section
  whose chart fails to draw shows a note, and the rest of the report builds.
- **Dashboard robustness (#265).** In the interactive dashboard, a trend line breaks at missing
  samples instead of dropping to the series minimum, and an all-NaN column is left out; the
  scatter's range is computed with a loop, so a year of 1-minute data no longer leaves it blank.
  A dashboard or site report on a frame with a text column (a mode point) no longer fails: the
  data-quality section, the default trend and the scatter skip non-numeric columns. A zero-row
  frame builds.
- **A year-only part re-sent in new bytes after its year was migrated is caught (#230).** The
  landing compares a year-only object's rows with the month partitions of its year and
  quarantines it as a `duplicate` when they already hold every row, instead of storing the year
  again beside the migrated months.
- `camber edge land` now invalidates the store's point catalog after it stores parts, so new
  equipment and points show up without a manual `rebuild_catalog`.
- **Tower drift rules no longer score against a baseline fitted on another load (#234).**
  `cooling_tower_approach_drift` and `cooling_tower_fan_effort_drift` match on chilled-water
  tons, condenser heat rejected or the tower's range. The frozen baseline did not record which,
  so mapping `cw_flow` after the range caveat asked for it, or running a tower as its own
  equipment after running it with its chiller's points, scored tons against degrees. A healthy
  tower then read "approach narrowed 116.5°F" or "fan speed -2436.2%-pts", and a fresh baseline
  on the same input read 0.0°F. Both rules now decline (`info`, reason
  `load_source_mismatch`, metrics for the current and the baseline's load) with "baseline fitted
  on X, current load is Y; refreeze", and a caveat that names `camber drift accept`.
- **`chiller_staging` and `chiller_staging_fleet` no longer read standby draw as running (#235).**
  A chiller ran above a fixed 2 kW, but a stopped chiller draws 5 to 15 kW for its controls, oil
  heater and crankcase heater. A chiller cycling an hour on and an hour off over an 8 kW standby
  read 0.1 starts a day and 100 % runtime; it now reads 12 starts a day (fault) and 50 %. A
  chiller runs by its `compressor_status` when that is mapped, else above `run_frac` (0.10) of
  its own 95th-percentile power, taken over the samples that draw any power so that a lag chiller
  at 0 kW most of the time still runs. Both rules take `min_power_kw` (default `None`) and
  `run_frac` parameters and report the metric `run_source`; `chiller_staging` also reports
  `run_threshold_kw`.
- **`control_hunting` checks resolution per signal (#236).** The resolution ceiling was the
  lowest across all modulating signals, so one 15-minute OA damper made the rule decline and hid
  a cooling valve sampled every minute and hunting at 20 reversals an hour. Its caveat also read
  "observed reversals on 500% of the resolvable maximum". Now a signal whose sampling cannot show
  the warn threshold is not evaluated: its rate is `None`, it is listed in the new metric
  `not_evaluated_signals`, and a caveat names it. The verdict is the worst signal that can be
  evaluated, judged against that signal's own ceiling. The rule declines only when no signal can
  be evaluated.
- **`boiler_short_cycle` and `compressor_short_cycle` count starts per observed day (#237).**
  Starts were divided by the span from the first sample to the last, so a logger gap counted as
  days with no starts. Two days cycling 24 times a day, a 28-day gap and a day off read 1.6
  starts a day. The rate is now divided by the observed days: the sum of the sample steps no
  longer than three median intervals, as `control_hunting` measures its observed time. The same
  trend reads 16 a day (boiler fault, compressor warn). The new metric `n_days_observed` sits
  beside `n_days` (the span), and a caveat is added when fewer than half the span's days are
  observed.
- **`heatpump_defrost` and `compressor_staging` read a short trend over one day (#240).** The
  span was floored at 1e-9 days, so one defrost in two hours read 24.2 reversing-valve cycles a
  day (warn), and one stage excursion read 24.2 stage changes a day. The span is now floored at
  one day, as `boiler_short_cycle` does: those trends read 2.0 a day (ok), with a caveat that the
  trend spans under a day. `n_days` still reports the real span.
- **The `heatpump_defrost` ok summary names no cause (#240).** It ended "— check defrost
  termination" even with no reversals. That cause now appears only on warn and fault, as for the
  drift rules (#189).
- **The store and registry primitives bypassed legal holds and the audit log (#250).** With a
  facility under a hold, `drop_facility` deleted every row, `registry.remove` tombstoned it and
  left the hold pointing at a removed id, and `prune` and `drop_partition` deleted partitions,
  none of them audited. #212 had fixed this for the `camber datasets` path only.
- **`interop openfdd ingest` replaced a held facility's data (#251).** A changed or forced
  re-ingest now runs the same hold check as `camber datasets ingest` and is refused. An unchanged
  re-ingest is still skipped.
- **A config run wrote state for an archived facility, and the next restore failed half-way
  (#252).** A folder or CSV config bound to an archived (even held) facility wrote
  `state/<fid>/faults.json` and the manifest. The next restore then swapped the store partition
  back in and failed its round-trip check on files the bundle did not hold, leaving the facility
  `archived` with live data, and every retry failed the same way. Runs no longer write that state
  (see Changed). Restore now stages every tree and re-hashes the staged files before the first
  swap, so a mismatch leaves the live trees as they were. It also empties every live hot tree the
  bundle does not list (its plan lists them under `deletes`), and under a legal hold it refuses a
  restore that would delete or change a live file. A restore interrupted between two swaps leaves
  the facility `archived`, and re-running it finishes the restore.
- **Brick site export shared part nodes across equipment (#253).** Every air handler's valves
  hung off one `bldg:Cooling_Coil`, so after `site_from_ttl(site_to_ttl(site))` each air handler
  came back with every air handler's valves, dampers and fan points, with either backend. Each
  equipment now has its own part nodes, and a test round-trips a two-air-handler site.
- **The Brick export wrote invalid or mangled Turtle for real point names (#254).** A name with a
  space was a syntax error for rdflib and returned nothing from the minimal reader, `AHU-1/SAT`
  came back as `SAT`, `SAT.` did not parse, and the `AHU_1:supply_air_temp` names of
  `Equip.from_roles` lost their prefix, so two air handlers' points collided. Names are now
  percent-encoded local names with the exact name as `rdfs:label`, and both readers decode them.
  The same applies to equipment ids in the minimal 223P export.
- **A facility rename could permanently break another facility's site resolution (#255).**
  Renaming B to A's name or id made A's labels ambiguous for ever, even after B was renamed back,
  so A's site-keyed legacy faults and baselines stopped being adopted and `portfolio migrate`
  blocked on them. Such renames are now refused (see Changed). A facility's own labels (id,
  registered name, current display name) now outrank other facilities' past display names, so a
  workspace that already holds such a rename resolves A's labels again.
- **`portfolio recover` deleted a held facility's hot data when it finished an interrupted
  archive (#256).** A hold placed after the crash now blocks the deletion: recover reports
  `archive_blocked_legal_hold`, keeps the data, and finishes the archive once the hold is
  released.
- **Haystack JSON v3 and Hayson tags resolved nothing (#257).** `roles_from_haystack` and
  `topology_from_haystack` counted only `true`, `"M"` and `"✓"` as markers and used ids as given.
  They now read v3 markers (`"m:"`) and refs (`"r:<id> <dis>"`) and Hayson markers
  (`{"_kind": "marker"}`) and refs (`{"_kind": "ref", "val": ...}`), for point ids, `ahuRef` and
  `equipRef`. Fixtures in both encodings are under `tests/golden/haystack/`.
- **`camber edge selftest` no longer drains the real spool (#242).** It ran the forwarder on the
  configured spool, so a backlog queued during an outage was "sent" to the in-memory sink and
  acknowledged, then deleted. It now runs on a temporary spool and never reads, creates or writes
  the configured one, and a retired spool gives an error and exit 1 instead of a traceback.
- **BACnet trend-log reads return the newest records (#243).** `read_trend_log` read 1000 records
  from position 1, the oldest in the ring buffer, so once a log held more than 1000 records a
  polling forwarder never saw new data. It now reads `record-count` and the newest records.
- **A Haystack hisRead across a DST change parses, and units are kept (#244).** A range whose UTC
  offset changed raised "Tz-aware datetime.datetime cannot be converted"; `units()` was always
  empty and now holds each point's unit from the grid.
- **A quarantine key collision no longer corrupts the record or aborts the landing (#247).** A
  second, different upload at a key quarantine already held overwrote the record with its own
  hash, then failed the move, and the exception stopped the run, so every later object stayed in
  the inbox on every run. The record is never overwritten: the upload stays in the inbox as a
  `quarantine_collision`, and the run goes on. `quarantine release` hashes each held object again
  and refuses one that matches neither its name nor its record, record or not.
- **BACnet objects with one name on two devices are both kept and read at their devices (#248).**
  Discovery dropped each object's device; two controllers with a `ZN-T TREND` gave one point, and
  every read went to the single target address.
- **The edge config's secret scan catches SAS URLs and DSN passwords (#249)**, and file sources
  get the config's `timezone` (it was dropped).
- **The RCx report no longer reads supply air above its target as supply air too cold.**
  `supply_air_reset_compliance` warns on both sides of the G36 target. The "supply air too
  cold" cause chain took either one, so a coil that could not cool would root a chain of reheat
  findings. It now takes only the below-target side.

## [0.107.0] — 2026-10-17

**0.107: the RCx report's next phase (an excess-outside-air cost, AHU-to-VAV cause chains, direct PDF output and optional grounded prose), byte-independent dedupe for edge uploads, and 28 fixes from an audit of the rules, M&V reports, CLI, store, servers and docs (#142, #194–#225).** **Behaviour change (#221):** `camber serve` bound to loopback now requires its access token when `--auth` is left out, completing the 0.104 deprecation; the launch URL with the token is printed and saved, `/health` stays open, `--auth none` keeps an open server, and binds beyond loopback (the container image, Kubernetes) are unchanged. **Behaviour change (#212):** dataset writes follow the facility lifecycle on every path, as the lab did: a purge or a forced or changed re-ingest of a facility under a legal hold is refused, an ingest into an archived or suspended facility is refused, and workspace ingests and purges need `--reason`. **Workbook:** the `zone-dcv` answers are re-recorded, because `co2_ventilation` now judges a room's trended occupancy instead of an assumed schedule (#200). The RCx report prices excess outside air (#195), groups an air handler's fault with the symptoms at the boxes it serves when a topology is declared (#196), writes PDF with the optional `pdf` extra (#197), and can carry generated prose in which every sentence cites a fact from the report, off by default (#194). The edge landing recognises a month re-sent in different bytes, and the forwarder writes canonical parts (#142). Rules no longer judge fan-off, compressor-off or idle hours (#198, #201–#203), and missing valve data no longer passes (#199); several ok summaries no longer name a fault (#204). `mv report` and `mv propose` agree with `mv run` (#205–#207, #225), and normalized savings no longer go NaN on one missing temperature (#208). An interrupted ingest no longer empties a facility (#213); config mistakes, bad ports and missing stores give one-line errors (#214, #215, #217, #218); server errors carry no paths (#216). The docs' examples, CLI flags and reference links are now checked by tests (#219–#224).

### Added
- **The edge landing recognises a month re-sent in different bytes (#142).** `camber edge land`
  computes a row digest of every Parquet object before it stores it: a sha256 over the decoded
  rows, with normalized types, in the store's canonical sort order, so row order, column order,
  row groups, compression, timestamp unit, string type and schema metadata do not change it
  (`camber.edge.row_digest`). When a part in the object's store partition, or another object of
  the same run, already holds those rows, the object is a `duplicate`: it goes to quarantine with
  the name of the matching part, and `edge quarantine release` refuses it. Each partition keeps
  its parts' digests in `_edge_rows.json`, a cache checked against each part's size and
  modification time; parts with no entry are digested on first use. A month whose rows changed
  lands as before.
- **Presigned uploads can be staged in an inbox (#142).** `route_key(portfolio, key, inbox=True)`
  signs an accepted upload into the bucket's `_inbox/` prefix instead of the store's layout, so
  `camber edge land` checks it before anything reaches the store. docs/EDGE-DEPLOY.md describes
  the arrangement and the upgrade order.
- The edge config's `part_layout` (`canonical` or `legacy`; `CAMBER_EDGE_PART_LAYOUT`) and two
  manifest fields, `part_layout` and `rows_sha256`, for audit.
- **The RCx report writes PDF directly (#197).** `camber report CONFIG --layout rcx --out
  report.pdf` renders the report's own HTML through WeasyPrint, so there is still one layout
  source: `--paper` sets the page size, sections start pages, figures and rows stay whole, table
  headers and the research-only banner repeat, and the inline charts are embedded. The `.pdf`
  extension picks PDF, as `.yaml` picks a YAML config; `--out` stays the one output flag.
  `RcxReport.to_pdf(path=None)` returns the bytes. WeasyPrint fetches only `data:` URLs, so the
  render reads nothing from disk or the network. The PDF's dates are the end of the data period,
  or `SOURCE_DATE_EPOCH` when set, so the same data gives the same bytes. WeasyPrint is the new
  optional `pdf` extra (`pip install "camber-toolkit[pdf]"`, `weasyprint>=68.1`), and it needs
  the Pango system libraries. Without the extra or Pango, the command exits 2 before the run and
  names the extra. On conda-forge, install `weasyprint` from conda-forge or with pip. Only the
  rcx layout writes PDF: `--layout audit` with a `.pdf` path exits 2. A separate CI job installs
  Pango and runs the PDF tests; the core matrix skips them.
- **The RCx report costs excess outside air (#195).** A new estimator in
  `camber.fault_economics` prices `outdoor_air_fraction` and `economizer_high_limit`
  findings. It costs only the outside air above the declared minimum, on occupied fan-on
  hours. Cooling is `4.5 × cfm × e × (h_OA − h_RA)` when outdoor and return humidity are
  trended, else `1.08 × cfm × e × (OAT − RAT)`, at `cool_cop`. Heating is
  `1.08 × cfm × e × (RAT − OAT)` while the unit heats, at `boiler_efficiency`, or at
  `heat_pump_cop` when that is set. Excess while economizing costs nothing. The airflow is the
  trended supply airflow, else the new `EquipmentLoad.supply_airflow_cfm`, else
  `design_oa_cfm / (min_oa_pct / 100)`. When an input is missing the estimator names it
  (`uncosted — needs supply_airflow_cfm or design_oa_cfm`, or `needs min_oa_pct`). Appendix B
  lists each unit's minimum and its source, the airflow source, the cooling and heating bases,
  and the estimate. In the `econ` chain the issue still shows the larger member, not the sum.
  See docs/RCX-REPORT.md.
- Both rules report the estimator's inputs as new `oa_cost_*` metrics. These are the period's
  excess energy per cfm of supply air (or in kBtu with a trended airflow), the cooling,
  heating, economizing and unjudged hours, and the bases used. `outdoor_air_fraction` also
  reports `min_oa_source`. Severities do not change.
- `camber.oafraction` gains `excess_oa_penalty`, `humidity_ratio`, `moist_air_enthalpy` and the
  standard-air constants `SENSIBLE_BTUH_PER_CFM_F` (1.08) and `ENTHALPY_BTUH_PER_CFM` (4.5), plus the
  heating thresholds `HEATING_RISE_F` (2 °F) and `HEATING_VALVE_PCT` (5 %).
- A rule may declare `roles_cost`: roles it reads only to price its finding. They load like
  optional roles, but they never gate the rule, are not reported as missing and do not make the
  finding conditional.
- `camber.api.BadQuery` (also `camber.api.read.BadQuery`, #216): the `ValueError` subclass a
  malformed `start`, `end`, `limit` or `max_points` raises. It is the only error that the read
  API's `dispatch` answers with 400.
- **RCx issues chain an air handler to the boxes it serves (#196).** When the config declares a
  served-by `topology`, an AHU finding roots one issue, and the matching findings on the boxes it
  serves become members of that issue. The page and the summary give the count ("DemoAHU + 14 of
  22 boxes"). There are three pairs (`camber.rules.triage.ZONE_CHAINS`):
  - supply air too warm (`supply_air_control` with too-warm leading, G36 FC13, a SAT reset that
    is not responding) leads to zones that can't cool (`unmet_setpoint_hours` with too-hot
    leading);
  - supply air too cold (`supply_air_control` with too-cold leading, `supply_air_reset`,
    `supply_air_reset_compliance`, a SAT reset that is not trimming) leads to overcooling and
    reheat (`overcooling_min_flow`, `overcooling_severity`, `reheat_penalty`,
    `reheat_minimization_g36`);
  - duct static too low (G36 FC1, a static reset that is not responding) leads to starved boxes
    (`airflow_tracking` with under-setpoint leading, or a zone that can't cool).

  The chain follows the existing issue rules: its cost is the largest member, never the sum,
  and its hours are the union of the members' masks. A box symptom joins only when its hours
  coincide with the root's, or when a side has no mask, and then the link says the overlap was
  not assessed. A box symptom that leans on a distrusted sensor stays its own issue and is
  counted on the link. An AHU finding that leans on one roots no chain. A SAT or static
  rogue-zone census is listed on the air handler whose reset its rogue boxes sit under, and the
  census stays its own issue (`ZONE_ROGUE_LINKS`). Nothing is linked without a declared
  topology. A naming guess is not used, and neither is a box the topology does not cover or a
  box served by another air handler. Appendix A names the boxes that a missing topology left
  unlinked (`zone_chain_gaps`). `Issue.zone_links` holds the `ZoneLink` objects, and
  `to_dict()` issues gain `zone_chains`. The `synthetic-g36-vav-fleet` template declares its
  topology, but its rules form no chain: the one air handler whose resets fail has no box
  symptom among the template's rules.
- **The RCx report can carry grounded generated prose (#194).** Two kinds of prose slot: one
  paragraph in the executive summary (`exec_summary`), and a "what we found / why it matters"
  paragraph on each issue page (`issue:<key>`). These are the slot ids the engineer notes use.
  Prose is off by default, and the report stays the deterministic one. It comes from a JSON file
  keyed by slot (`--prose prose.json`, or `report.rcx.prose`), which runs offline and
  reproducibly, or from a model you wire. On the CLI that is `--llm-cmd`, the same seam as
  `camber explain` and `camber ask`; in Python it is an `AgentClient` passed as
  `build_rcx_report(client=...)`. A slot is shown only when every sentence cites the report's
  fact index and every figure in the sentence is in a fact that sentence cites. Otherwise the
  page keeps its standard text, and Appendix A lists the slot and the reason. An engineer note on
  a slot overrides its prose. Shown prose carries a marker and the list of the facts it cites.
  `--prose-prompts out.json` (`camber.report.rcx.prose_prompts`) writes each slot's prompt, with
  its facts, for offline generation. `camber.report.rcx.load_prose` reads a prose file. The PDF
  output renders the prose too; a summary that shows prose drops its one-page print cap, so
  the paragraph is never clipped.
- **The RCx fact index (#194).** `RcxReport.facts` is built from the same objects the tables
  render: the KPIs, each issue's headline, cost, member costs, uncosted reason, hours, members,
  action, confidence reasons, caveats, sensor dependencies and AHU-to-VAV chain counts, plus the
  declines, missing inputs, caveats and chain gaps of Appendix A and the assumptions of Appendix
  B. Its ids are context-qualified: `K1@summary`, `X1@appA`, `A3@appB`, `C1@<issue key>`. An
  issue's ids follow its key, so the same inputs give the same ids. With prose on, Appendix D
  lists the whole index; with prose off it says the index is listed when prose is enabled.
  `to_dict()` gains `facts` and `prose`.
- `camber.agent.verify.check(..., require_cite_each_sentence=True)`: a sentence without a
  resolving `[id]` is dropped (flagged `uncited-sentence`), and a sentence's figures must be in
  the facts it cites itself. Citations may be context-qualified (`[F1@ctx]`); plain `[F1]` ids
  work as before.
- **The docs' Python examples run in the test suite (#219).** `tests/test_docs_fences.py` runs
  every `python` fence in README.md and docs/ (the workbook included; not docs/dev/ or
  docs/proposals/), each page's fences in order in one fresh interpreter, offline and in a
  temporary folder. A fence that cannot run on its own (a fragment, or one that needs a dataset,
  a store, a file, an optional extra or the network) carries a `fence: skip (reason)` HTML comment on
  the line above it, which renders on neither GitHub nor the docs site.
- **The CLI reference is checked against the parser (#224).** `tests/test_cli_reference.py`
  fails when docs/CLI.md does not name a command or an option the parser accepts. docs/CLI.md
  now documents `camber bacnet-discover`, `mv freeze --allow-short` (the only way past the
  baseline-length rule) and every flag the reference was missing, including `--run-id`,
  `--no-strict`, the `charts` options, `--quiet`, `--store` / `--equip` on `mv`, and the 0.107
  `datasets ingest|remove --reason` and `remove --yes|--confirm`.
- **The provisional-surfaces ledger covers 0.105 to 0.107 (#223).** docs/API-STABILITY.md lists
  the TOWT and RMV2.0 additions, `ModelFitError`, the 0.106 rule parameters, metrics, `cw_flow`
  and `is_chw_loop`, and the 0.107 surfaces, and notes the #183 metric rename.
- `tests/test_references.py` checks that every PNNL guide or chapter a rule parameter's basis
  cites is in that rule's `RULE_REFERENCES` entry, and that the references page lists every
  `MV_REFERENCES` id (#222). `tests/test_docs_counts.py` computes the role counts that
  docs/ONTOLOGY.md states (#220).

### Changed
- **The edge forwarder writes canonical parts (#142).** Each part is in the store's 0.103 layout
  (rows sorted, fixed writer options, no pandas metadata) and still named by the sha256 of its
  bytes, so the same rows give the same key and `camber store verify` counts edge parts as
  canonical. NDJSON output is unchanged.
- **Compatibility.** Parts that already landed are untouched: they still read the same, still
  pass the hash check, and `store normalize` still leaves non-canonical edge parts alone. The
  landing digests them on first use, so the first canonical re-send of a month an older forwarder
  sent is recognised as a duplicate. Older forwarders keep working: their parts land and are
  recorded as before. Upgrade the cloud landing first, and route presigned uploads through the
  inbox: a pre-0.107 landing accepts canonical parts but would store each re-sent month twice. A
  forwarder that must keep sending to an older landing can set `part_layout: legacy`. Batches
  already in a spool keep their bytes and key. Uploads signed straight into the store's layout
  get no row check.
- `outdoor_air_fraction`'s `min_oa_pct` defaults to `None`, which means the generic 20 %. The
  rule judges exactly as before. The new `min_oa_source` metric tells a configured minimum from
  the default, and only a configured one is priced.
- **Behaviour change (#221): `camber serve` on loopback requires its access token by default.**
  This completes the switch announced as a deprecation in 0.104 (#134). Bound to loopback
  (`127.0.0.1`, the default; `localhost`; `::1`) with `--auth` left out, the server now uses token
  auth: it prints the launch URL with `?token=` and saves it in the launch file
  `serve-<port>.url`, as `--auth token` does, and says in a one-line note that this is the
  default. The 0.104 notice about a future switch is gone. `make_server` / `serve` with `auth`
  unset and `python -m camber.api.server` with `CAMBER_API_AUTH` unset follow the same default.
  `--auth none` (`CAMBER_API_AUTH=none`, `auth="none"`) keeps an open server. Binds to any other
  address, including the container image's `0.0.0.0` and the Kubernetes manifest, keep no
  authentication by default, with the warning, exactly as before; `GET /health` still needs no
  token. Scripts that read a loopback server without credentials get 401 now: send
  `Authorization: Bearer <token>` (fix it with `CAMBER_API_TOKEN`), or pass `--auth none`.
- An RCx issue's heading never comes from a linked box member (#196): a box's reheat valve
  cause stays on the box's own row, and the air handler's cause heads its chain. `link_findings`
  judges sensor precedence for each member on that member's own equipment, which gives the same
  result for a single-equipment issue.
- **Dataset writes follow the facility lifecycle on every path (#212).** `camber datasets
  ingest` and `remove --purge-store` (and `camber.datasets.ingest` / `remove`) now apply the
  rules `camber lab` already applied, from one shared module (`camber.datasets._lifecycle`).
  Inside a portfolio workspace, new refusals:
  - an ingest into a facility that is not `provisioning` or `active` (suspended, offboarding,
    archived) is refused before anything is written;
  - a purge of a facility under a legal hold, or not `provisioning` / `active`, is refused
    before anything is deleted, the dataset cache included; so is an ingest that would drop such
    a facility because its subset no longer produces it;
  - both need `--reason` (`reason=` in the API), and a purge also needs `--yes` or
    `--confirm ID`. They are audited as `datasets.ingest`, `datasets.remove` and
    `datasets.purge`.

  Every ingest swap now passes the registry's write guard, so no ingest, in a workspace or not,
  writes into an archived facility. Plain stores need no reason or confirmation, as before.
  `camber lab` behaves as before.
- **Behaviour change: a legal hold blocks a forced re-ingest, on every path (#212).** A hold
  means nothing held is deleted, and a re-ingest replaces the facility's partition. So
  `camber datasets ingest --force` (or a changed re-ingest) into a held facility that has data
  is refused with `LifecycleError` and changes nothing: exit 1 in the CLI, 409 in `camber lab`
  (which allowed it before), and the API raises. An unchanged re-ingest is still skipped, and
  release the hold (`camber retention release`) to re-ingest. There is no override.
- **`camber store verify` reports interrupted ingests (#213).** It lists leftover swap and
  staging directories, and registered facilities with no part files in a state that should have
  some. A facility whose data is stranded outside its partition fails verify even without
  `--strict`; `--strict` also fails on any leftover or empty facility. The JSON report gains
  `leftovers` and `empty_facilities`.
- **More rules link their PNNL reading (#222).** Rules whose threshold bases cite a PNNL guide
  or chapter now map to it, so reports give them *Learn more* links: the pump family's ten drift
  detectors (`pump_flow_drift`, `pump_head_drift`, `loop_deltat_drift`, `loop_dp_drift`,
  `pump_power_drift` and their `hw_` twins) link chapter 8, and the loop delta-T pair also the
  plant guide whose heading asks whether the loop delta-T is low; `coil_leak_drift` links
  chapter 5's leaking-valve checks; `economizer_damper_drift` links chapters 6 and 5; and
  `outdoor_air_fraction` and `economizer_high_limit` add chapter 5, whose outdoor-air fraction
  their bases cite. The pump detectors' `min_speed` basis cites chapter 8 only: the plant guides'
  pressure headings ask about resetting the setpoint, which those detectors do not judge. In the
  RCx report the economizer issue gains a chapter 5 link.
- The docs build fails on a link to a heading that does not exist (`validation: anchors` in
  mkdocs.yml, so `mkdocs build --strict` catches it) (#220).

### Fixed
- **`camber serve` error codes (#216).** A corrupt or unreadable store file gave 400 (pyarrow's
  `ArrowInvalid` is a `ValueError`), and 400 and 500 bodies could name server paths. Now only a
  malformed query parameter is 400. Any other failure is 500 with the body
  `{"error": "internal error"}`, with no `detail`, and the exception and traceback go to the
  server's stderr. `camber lab`'s `/history`, `/points` and `/facilities` routes go through the
  same code, so their errors carry no path either.
- **`camber serve` and `camber lab` check their startup arguments (#217).** `--port` outside 0 to
  65535 is a usage error (exit 2) instead of an `OverflowError` traceback, and `make_server`
  raises `ValueError` for such a port. `camber serve` exits 1 with "not a ParquetStore" when the
  store path is missing, is a file, or is a folder with no store partitions or store files,
  instead of serving an empty store. An empty folder is still served as a store with nothing
  written yet.
- **`simultaneous_heat_cool` judges only samples where both valves report (#199).** A missing
  valve sample used to read as a closed valve, so an all-missing heating valve passed as `ok` at
  0 % and gaps understated the both-open share. The rule now declines (`info`, reason
  `no_valve_data`) when no sample has both valves, takes its percentages over the samples that
  do, and reports the new `valve_coverage_pct` metric. Below 95 % of occupied samples
  (`VALVE_COVERAGE_CAVEAT_PCT`) a caveat says how many were not judged.
- **The air-cooled DX rules need a running gate (#201).** With no compressor status and no
  return + supply air temperature, `dx_refrigerant_charge` (target, limits and baseline modes)
  and `discharge_superheat_drift` read every row as cooling, so compressor-off hours were judged
  as a low charge. They now decline (`info`, reason `no_run_gate`). A reversing valve or
  supply-fan status alone is not a gate: the valve gives the mode and the fan runs through the
  compressor's off cycles. A water-cooled unit is still gated on its chilled-water load.
  `dx_indoor_airflow` requires the return and supply air temperatures, so it was always gated.
- **`cooling_tower_approach` needs an operating signal (#202).** With only the CW supply and a
  wet-bulb, idle-tower hours were judged as operating. The rule now also gates on a pump run
  status, a chiller run status or the condenser-water flow (above 5 % of its 95th percentile,
  the #184 loop-flow cut) when one is mapped, on top of the fan or range gate, and reports
  `run_gate`. With no fan speed, CW return or run signal it declines (`info`, reason
  `no_operating_signal`), and with a run signal that never reads as running it declines with
  reason `never_running`. `analyze_cooling_tower_approach` takes the matching `run_mask`.
- **The store commands refuse a path that is not a store (#215).** `store verify`, `normalize`
  and `migrate-partitions` exit 1 with "not a ParquetStore" for a missing path, a file, or a
  directory with no partitions, registry or workspace marker, and create nothing (normalize no
  longer creates the directory and its `_lock`). `--facility` must name a facility the store
  knows, and `retention apply --facility` one the workspace knows. `store verify --limit`
  must be 0 or more.
- **`camber datasets config --out` refuses a dataset that is not ingested in the store (#218)**,
  or a `--facility` that is not one of its ingested facilities, and names the ingest command to
  run. Printing the template without `--out` still works, with a warning.
- **Config mistakes are one-line errors (#214).** Any command that hits a missing or unreadable
  file, invalid JSON or YAML, or a bad value now prints `error: <message>` and exits 1 instead of
  a traceback. Set `CAMBER_DEBUG=1` for the traceback. A config is checked when it loads:
  - the top level must be an object;
  - `rules` must be a list of names or `{"name": ...}` entries;
  - `equipment` must be a list of `{"class": ...}` entries;
  - `resample` must be a pandas frequency.

  An unknown rule name lists close matches and every valid rule.
- The IPMVP Option A example in docs/MANDV.md called `option_a_savings` with keywords it does
  not take; it now passes the measured values by position (#219).
- docs/ONTOLOGY.md, docs/CAPABILITIES.md and docs/workbook/index.md stated role and exercise
  counts from earlier releases; the role counts are current and tested, and the rest no longer
  give a number. docs/LAB.md's link to "Fetch & ingest is greyed out" pointed at a GitHub-style
  anchor, and docs/API-STABILITY.md still named the 0.9.x series (#220).
- docs/REFERENCES.md's M&V table lacked the LBNL RMV2.0 row (`lbnl-rmv2`) (#222).
- docs/DATASETS.md listed `--lock-timeout` on fewer commands than accept it (#224).
- **A crash during an ingest's partition swap no longer empties the facility (#213).** The swap
  (also used by the open-fdd ingest) now uses the store's crash-recoverable replace
  (`camber.store._swap`). A crash at any point leaves the old or the new partition once recovered.
  Every ingest first finishes or rolls back an interrupted swap. It also puts back a partition
  that a pre-0.107 swap left in `_staging/<fid>.old-<pid>`, and removes the staging directories
  of ingests whose process is gone.
- Catalog errors print their message, not a quoted `KeyError` repr (#214).
- `"rules": "filter_fouling"` was iterated one character at a time; it is now refused (#214).
- **`camber mv report`, `mv propose` and `mv rebaseline` apply the entry's extrapolation policy
  (#205).** They graded coverage with the default policy and ignored an `mv` entry's
  `extrapolation` block, which `mv run` applies. A strict policy could decline the saving in
  `mv run` while `mv report` showed it as `in_range`. All four paths now build the policy from the
  entry and reach the same verdict. Only configs that set `extrapolation` change.
- **`mv run` and `mv report` use the same ledger date window (#206).** The report kept only
  ledger entries dated inside the reported rows. A non-routine adjustment recorded with
  `camber mv adjust` and dated between the baseline end and the reporting start, or inside the
  baseline period, restated the saving in `mv run` but was skipped in `mv report` with no
  caveat. Both paths now keep the entries dated from the baseline start to the reporting end.
- **The M&V report's "Reported segments" table matches results by version (#207).** It paired
  segments with results by position, so a segment declined for "no data" showed the next
  version's savings, and that version had no row of its own. The declined segment now says
  "no data", and each version shows its own savings.
- **Normalized and standard-conditions savings skip normal-year periods with no finite projection
  (#208).** One NaN or implausible temperature in the normal-year set turned every figure of
  `normalized_savings` and `standard_conditions_savings` into NaN, with no caveat and no
  decline. A period that either model cannot project is now left out, counted
  (`NormalizedSavings.n_excluded`) and named in a caveat, as `avoided_energy_savings` has done
  since 0.105. With no period left the saving is declined. Clean data is unchanged.
- **More text shows the M&V registry's model labels (#209).** The versioned-store
  `mv_baseline` summary, the RCx report's M&V table, the freeze and rebaseline plan text and
  `camber mv list` now say "3P cooling" or "2P (5P fallback)" rather than "3PC" or "5P", as the
  rest of the output has since 0.105. The metrics, the plan's `model` field and the `mv list`
  JSON keep the codes; the plan rows gain `model_label`.
- **The chained CUSUM chart takes its unit from the meter (#210).** Without a `units` block it
  labelled every meter "Cumulative kWh", including a gas billing meter in therms. The unit now
  comes from the unit system, else the meter's bills, else the energy its trended rate
  integrates to (`MeterChain.meter_unit`). When none is known the axis reads "Cumulative energy".
  `cusum_plot` and `chained_cusum_plot` default to that unit-free label.
- **The fleet report's median EUI is a true median (#211).** With an even number of buildings it
  took the upper of the two middle values, so every "vs median" percentage was off. EUIs of 40,
  60, 80 and 200 now give a median of 70, not 80.
- **`mv run`, `mv report` and `mv propose` date the T4 extrapolation trigger alike (#225).** The
  run dated T4 at the reporting start, while the report, propose and rebaseline dated it the day
  after the baseline ended. These paths now assess the triggers over the entry's
  `reporting_period`, as the run does, so T4 falls on the reporting start everywhere. The
  rebaseline settle days count from that date. Entries without a `reporting_period` are
  unchanged.
- **`airflow_tracking` judges fan-on, occupied hours (#198).** It judged every sample with a
  setpoint, so a healthy box read as starved for every hour its air handler was off. It now reads
  `SUPPLY_FAN_STATUS` (else `SUPPLY_FAN_SPEED`) and `OCCUPANCY` as optional roles and judges only
  fan-on samples, and only occupied ones when an occupancy point is trended, as
  `supply_air_control` does. Measured airflow is not used as the fan proxy, because a starved box
  reads zero flow. With neither signal it judges every sample as before and adds a caveat. New
  metrics `fan_gate` and `occupancy_gate`, and the summary names the hours judged. A new
  `abs_tol` (cfm, default 0, off) counts a sample as off setpoint only when it also misses by more
  than that, so a small miss on a tiny minimum setpoint is not inflated by the relative error.
- **`co2_ventilation` and `co2_ventilation_system` honour a trended occupancy (#200).** Both used
  the assumed weekday 07-18 schedule, so a room's empty hours inside it counted as
  over-ventilated. `OCCUPANCY` is now an optional role: a trended point replaces the schedule
  (`effective_occupied_mask`, as in `dcv_verification`). A finding judged on the assumed schedule
  carries a caveat, and the new `occupancy_basis` metric says which was used.
  `analyze_co2_ventilation` takes an `occupied` mask. Workbook `zone-dcv` is re-recorded on the
  rooms' trended presence points. The b4b-windesheim rooms' over-ventilation share moves from
  73-86 % to 30-66 %, and `co2_ventilation` moves from warn to ok on ROOM_917810,
  ROOM_917810_scd41, ROOM_999169 and ROOM_999169_scd41 (ROOM_925038 stays warn). The sdu-ou44
  ROOM2 `over_vent_pct` moves from 66.5 to 74.4 (still warn). The b4b stand-in gives its two
  valve rooms two-person visits, so it tells the same story.
- **`static_pressure_reset` takes its range and median from fan-on samples (#203).** Only the
  classifier saw the fan-gated setpoint; `sp_range_inwc` and `sp_median_inwc` came from every
  sample, so a flat setpoint that reads 0 with the fan stopped showed a range of its full value
  and a median of 0, and a short trend was judged "resets with demand" on that range. Both now
  come from the fan-on samples. With fewer than three fan-on samples the rule returns `info`.
- **`ok` summaries name no fault (#204).** An `ok` `overcooling_severity` finding says "no
  sustained overcooling below" its reference, and `max_depth_f` is floored at 0, so a zone above
  its heating setpoint no longer reads "overcooled ... max -2.0 degF". An `ok`
  `economizer_high_limit` finding says the economizer is locked out above the high limit instead
  of ending "economizer not locked out". An `ok` `leaking_valve` finding says "no valve leak" and
  drops "(worst: ...)". `warn` and `fault` wording is unchanged, and no severity moves.


## [0.106.0] — 2026-10-17

**0.106: rule fixes found while building the synthetic lab cases, for air-side maintenance, chiller plants, heat pumps and G36 zone fleets, and drift findings whose wording matches their verdict (#181–#190).** **Breaking (#183):** `chiller_staging_fleet`'s `n_running_hours` and `n_multi_hours` were sample counts and are renamed `n_running_samples` and `n_multi_samples`, with no aliases; the hours are new metrics beside them. **Scoring change (#184):** `camber datasets score` now scores each rule only on the equipment it applies to, so per-rule false-positive rates move on mixed-equipment entries; the overall confusion is unchanged. `filter_fouling` and `control_hunting` judge fan-on samples, and an unreachable hunting threshold no longer reads as a pass (#181, #182). `chiller_staging_fleet` counts only chillers and takes a declared `capacity_kw` (#183). A new `cw_flow` role lets tower drift match on heat rejected; chilled-water loops are gated on their pumps, and approach checks read running samples only (#184). Heat-pump rules handle coarse trends, no longer read a defrost as the wrong mode, and count stage changes only while running (#185). The G36 zone-fleet rules read `airflow_min_sp`, stop counting G36's high heating loop as a violation, and rate mixed heating and cooling by hours (#186). An `ok` drift finding now says the signal holds near its baseline instead of naming a fault, the drift reference declines only the detectors it could run, and the tower-approach and head-pressure detectors say which way the signal moved (#187–#190). The two synthetic entries publish the new points (generator version 2); the `air-maintenance`, `plant-chiller-multi`, `plant-heat-pump` and `zone-g36-fleet` workbook answers are re-recorded.

### Added
- **A condenser-water flow role, `cw_flow` (#184).** The flow through the tower in gpm. With
  the tower's range it gives the heat rejected, so the tower drift rules match load correctly
  when chillers and their condenser pumps stage. It has a Haystack hint, the Brick class
  `Condenser_Water_Flow_Sensor`, a 223P quantity, physical bounds and the gpm unit check.
- **`chiller_staging_fleet` takes a declared capacity (#183).** The new `capacity_kw` parameter
  is each chiller's full-load power: one number for equal chillers, or an `{equip: kW}` map.
  The new metric `capacity_source` (`declared`, `observed_p95` or `mixed`) says where the
  capacity came from. Without a declaration a caveat says the observed figure reads low on a
  plant that never loads a chiller fully.
- `camber.model.equipclass.is_chw_loop` tells a chilled-water loop (`CHW_LOOP`, `SECLOOP`,
  `CHILLED_WATER_LOOP`) from a chiller in the `chw_plant` family. `CHWLOOP` and
  `CHILLEDWATERLOOP` are now listed spellings of that family.
- The roles `airflow_min_sp` (a VAV box's minimum airflow setpoint), `zone_heat_loop` and
  `zone_cool_loop` (a zone's heating and cooling loop outputs, %) (#186).

### Changed
- **`filter_fouling` and `control_hunting` judge fan-on samples (#181).** Both read the fan
  from supply-fan status, else speed, else airflow (`schedules.fan_on_mask`), as the other
  air-side rules do. `filter_fouling` takes its median over fan-on readings only: over every
  sample, a unit running under half the day could never warn however dirty its filter, because
  the transmitter reads about zero with the fan off. `control_hunting` counts only steps between
  two fan-on samples, so fan-off hours no longer dilute the reversal rate. A unit that trends no
  fan signal is judged ungated, with a caveat. New metrics: `fan_gate` on both,
  `n_judged`, `n_masked_fan_off` and `fault_dp_inwc` on `filter_fouling`, and `fan_on_hours`
  and `fault_reachable` on `control_hunting`. Each rule has a `fan_gate` parameter (default on).
- `filter_fouling`'s fault multiplier, 1.5 times the change-out pressure, is now the documented
  parameter `fault_factor` (#182).
- `filter_fouling` links to PNNL Re-tuning chapter 9 (Building Walk Down). The chapter's
  air-handler walk-down lists missing, dirty, plugged or collapsed filters among the things to
  inspect. It gives no pressure threshold, so the change-out default is still CAMBER's own.
- **`compressor_staging` counts only stage changes while the compressor runs (#185).** A change
  to or from stage 0 is a start or a stop, which `compressor_short_cycle` already judges; counting
  it here made a healthy unit cycling two or three times an hour read as hunting. On
  `synthetic-heat-pump` the well-staged units now read 1.0 to 2.43 changes a day (4.29 to 6.5
  before) and `WSHP__stage_hunting` 43.3 (52.23), a warn rather than a fault.
- **`hp_capacity_shortfall` names a fighting room as the cause (#185).** When `hp_room_imbalance`
  runs in the same config and finds a unit's room fighting, that unit's `capacity` or `mixed`
  verdict becomes `room_fight` (`verdict_alone` keeps the old one, `deferred_to` and `room` say
  where to look). The severity stands: the room is out of band, but a bigger unit will not fix it.
- **`chiller_staging_fleet` counts only chillers (#183).** A chilled-water loop's power point
  is its pumps', so loops are left out of the fleet (new metric `n_loops_excluded`, with a
  caveat naming them).
- **Breaking: `chiller_staging_fleet`'s count metrics are renamed (#183).** They were sample
  counts named as hours. `n_running_hours` is now `n_running_samples` and `n_multi_hours` is now
  `n_multi_samples`, with no aliases: a reader of the old keys gets nothing. The hours are new
  metrics beside them (`running_hours`, `multi_hours`, and `sample_interval_min`).
- **`chw_supply_tracking` and `chw_plant_reset` gate a chilled-water loop on its pumps (#184).**
  Equipment whose class is a loop is "running" by its pumps' `pump_status` (duty above 0.5),
  else its `chw_flow` above 5 % of its 95th percentile, else its pumps' `power` above a tenth of
  its 95th percentile. `run_source` reads `pump_status`, `flow` or `pump_power`. A loop's power
  is no longer called the chiller's. Chillers keep their status-then-power gate.
- **`chiller_approach_fouling` reads running samples only (#184).** The medians read the samples
  the chiller ran, by its `compressor_status` (duty above 0.5) or else its `power` above a tenth
  of its 95th percentile. With neither, every sample is read, with a caveat. New metrics
  `run_source` and `n_judged`. A chiller that never ran is `info`.
- **`cooling_tower_fan_effort_drift` matches on heat rejected when the condenser flow is
  trended (#184).** The new `load_source` `cw_heat_rejection` is `cw_flow` times the range, over
  24. On the range alone the rule assumes a constant condenser flow, and a fired finding now says
  so: when a second chiller and its condenser pump stage on, the range halves for the same heat
  and the fans read as working harder.
- **`cooling_tower_approach_drift` runs on a tower of its own (#184).** It needed chilled-water
  flow and temperatures on the tower's own equipment, so it never ran on a separate tower. It
  now matches the approach on chilled-water tons when they are there, else on the heat rejected
  (`cw_flow` and the range), else on the range, with the constant-flow caveat on a fired
  finding. The new metric `tower_approach_load_source` says which. Equipment with chilled-water
  tons scores as before.
- **`camber datasets score` scores each rule only on the equipment it applies to (#184).** This
  changes per-rule label scores on any entry whose rules do not all run on every equipment. A
  rule's false-positive rate counted every labelled equipment, including equipment the rule
  cannot run on (the chillers, for a tower rule). Each record now lists the rules that ran on it
  (`scored`, leaving out a class decline), and each rule's confusion counts those records only.
  A suite rule with no finding anywhere is still scored over every record. The overall
  confusion is unchanged. `camber.eval.benchmark` reads the optional `scored` key.
- The `plant-chiller-multi` workbook answers are re-recorded (key, page, instructor block and the
  config comments). Over
  the whole facility the fleet counts 14 chillers, not 21, and 84.6 % of multi-chiller samples
  are over-staged (was 100 %), still a fault. The secondary loops' `run_source` is `flow` (was
  `power`), with the same figures. The `cooling_tower_fan_effort_drift` label score counts the
  7 towers, not all 28 pieces of equipment, so its false-positive rate is 1 of 5 (was 1 of 26).
  That rate is now 0 of 5: the synthetic tower trends its condenser flow (generator version 2,
  below), so the over-staged plant's tower no longer warns.
  `cooling_tower_approach_drift` now runs on the seven towers. It faults on the full-fan tower
  (+5.7 °F) and passes the others, the controlled fouled tower included; the key now records it.
- The `plant-heat-pump` label scores move with the scoring change and are re-recorded. Each rule now counts only the heat pumps or loops it ran on: `hp_mode_vs_need` and
  `hp_capacity_shortfall` over the 10 heat pumps, `heatpump_defrost` over the 4 air-source heat
  pumps, `compressor_staging` over the 6 water-source heat pumps and `source_loop_deltat` over the
  2 loops, not all 12. The same false positives now read `hp_capacity_shortfall` 3 of 9 (was 3 of 11) and
  `heatpump_defrost` on the 20-reversal variant 1 of 3 (was 1 of 11). `hp_mode_vs_need`'s one
  false positive is gone with #185 (a defrost reads as heating). Every true-positive rate and
  every zero false-positive rate stays.
- **G36 zone-fleet rules (#186).** `airflow_sp` is the box's active airflow setpoint; the
  reheat-at-minimum rules (`reheat_minimization_g36`, `zones_heat_cool_census`, `reheat_penalty`,
  `overcooling_min_flow`) read `airflow_min_sp` when it is mapped. With only `airflow_sp` they
  read it as the minimum as before, unless it swings with load while the airflow tracks it: then
  the G36 rules infer the minimum and say so. `zones_heat_cool_census` now rates the occupied
  hours with one zone heating while another cools (`pct_hours_mixed`), not one box heating
  above its minimum. `reheat_minimization_g36` no longer counts G36's high heating loop (the
  heating loop above 50 %, else the valve at 90 % or more or the discharge 15 °F or more above
  the space) as a violation, and reads a trended occupancy point. Reset effectiveness judges
  "not responding" and "not trimming" only on the cycles where the request-implied trajectory
  had left that end, and lists every failure mode (`failure_modes`). The static rogue census
  lists a rogue that is short of cooling under `overloaded_zones`, not `rogues`. The SAT
  censuses count tier-1 requests from `zone_cool_loop`. The cohort-starvation summary names every
  starved air handler. `cohort_space_temp` needs a 1 °F difference from the median
  (`min_abs_dev`). `cohort_airflow` reads `info` when its cohort spreads past 0.3 (`max_spread`)
  and nothing stands out. See docs/TR-RESET.md and docs/CAPABILITIES.md.
- **Two synthetic entries publish the points #184 and #186 added (generator version 2).** The
  cache regenerates them on the next fetch or ingest, because the generator version changed.
  `synthetic-chw-plant-multi`'s tower now trends its condenser-water flow (`CW_GPM`, mapped to
  `cw_flow`). Each running chiller has one 600 gpm pump, so the flow is 600 or 1,200 gpm,
  read about 1 % noisy. The tower drift rules now match on the heat rejected. The over-staged
  plant's tower no longer reads as working harder: `cooling_tower_fan_effort_drift` drops from
  +6.5 to +0.8 points, `ok`. The two fouled towers still fault. `synthetic-g36-vav-fleet`'s boxes
  now trend their active airflow setpoint (`ZN{n}_FLOW_SP`, mapped to `airflow_sp`) apart from
  their minimum (`ZN{n}_FLOW_MIN`, now `airflow_min_sp`). They also trend their heating and
  cooling loops (`ZN{n}_HTG_LOOP` and `ZN{n}_CLG_LOOP`, mapped to `zone_heat_loop` and
  `zone_cool_loop`). The censuses now count about as many requests as the air handlers' own
  `SAT_REQ` and `DSP_REQ`. `reheat_minimization_g36` reads the high heating loop from the loop,
  not the valve, and both single-maximum boxes still fault. The `plant-chiller-multi` and
  `zone-g36-fleet` workbook answers are re-recorded (keys, pages, instructor blocks and the
  `zone-g36-fleet` figure, whose census now counts the cooling loop). On `plant-chiller-multi`
  the fan-effort drift reads +37.9 and +55.3 points on the fouled towers (was +34.9 and +59.1),
  +0.8 on the near miss (was +4.9), and the full-fan tower's approach drift +5.8 °F (was +5.7);
  the fan-effort rule's false-positive rate is 0 of 5 (was 1 of 5) and the plant's 0 of 22 (was
  1 of 22). On `zone-g36-fleet` the SAT census's rogue sends 92.5 % of its air handler's
  requests (was 98.9 %), the equipment room 60.8 % of the static requests (was 48.6 %), the
  single-maximum boxes reheat above minimum outside the high heating loop 69.7 % and 93.1 % of
  their reheating hours (was 93.9 % and 100 %), and the near miss's zone 1 sends 43.0 % and binds
  on 45.4 % (was 41.3 % and 46.8 %).

### Fixed
- **An `ok` pump or loop drift finding names no fault (#187).** `loop_dp_drift` ended every
  summary with a cause, "rising system resistance / valve authority" or "bypass / stuck-open",
  even on an `ok` Finding. `pump_flow_drift` and `pump_head_drift` called a sub-floor fall a
  "deficit", and `pump_power_drift` called a sub-floor rise an "excess". An `ok` finding now says
  the signal "holds near its baseline", with the signed shift and its σ. An `ok` loop DP move that
  the DP setpoint reset explains keeps its size and says so. `warn` and `fault` wording is
  unchanged, and the `hw_*` twins follow. Only the text moves: in
  `tests/golden/pumpsim_fullsize.json` 39 `ok` summaries change (17 loop DP, 10 pump head, 8 pump
  power, 4 pump flow), and every severity, metric and caveat is unchanged. `diagnose_pump_drift`
  already named causes only for `warn` and `fault`.
- **The drift reference declines only the detectors it could run (#188).** With a declared
  `reference: {"equip": ...}`, the reference equipment declined as `is_reference` for every
  detector in the family, including those it has no inputs for. It now gets the same inputs check
  as every other unit: it declines as `is_reference` only where it has the detector's inputs, and
  is skipped otherwise. The units that can run such a detector still decline as
  `reference_missing_inputs`. The `camber datasets score` counts for `lbnl-chiller` and
  `lbnl-boiler` are unchanged.
- **`control_hunting` no longer reads an unreachable threshold as a pass (#182).** A trend can
  show at most about one reversal per sample, and in practice a little less. A threshold now
  counts as reachable only when that ceiling is at least `resolution_margin` times it (new
  parameter, default 1.25). At 10-minute data the ceiling equals the 6/hr warn threshold, so
  the rule now declines (info) instead of returning an `ok` it could not have failed. At
  5-minute data the ceiling equals the 12/hr fault threshold, so severity is capped at warn,
  with a caveat. The caveat now fits the verdict: "severity capped at warn" on a warning, and
  "only warn-level hunting was testable" on an `ok`.
- `filter_fouling`'s summary said "% of hours" above the change-out pressure; the figure is a
  share of samples (fan-on samples when gated), and it now says so.
- **A coarse trend no longer hides a heat pump's defrosts and stage changes (#185).**
  `heatpump_defrost` and `compressor_staging` take `max_interval_min` (default 5 minutes). As
  `boiler_short_cycle` does since #143, on a coarser trend the count is a floor: a count at or
  above the limit keeps its verdict with a caveat, and below it, samples part-way between two
  states decline the verdict to `info` ("cannot confirm on a 15-minute trend"). A resampled
  reversing valve also counts the fewest changes its means allow, so a 5-minute defrost in a
  15-minute bin (0.67) is two changes, not none. On `plant-heat-pump--15min`,
  `ASHP__defrost_sensor` now faults at 49.89 a day (0.14, `ok`, before). Both rules report
  `interval_min` and `n_partial`. **This changes behaviour on any trend coarser than 5 minutes:**
  `heatpump_defrost` and `compressor_staging` now caveat every such finding, and decline a count
  below the limit to `info`, where they used to pass silently. Run at the stored interval, or
  raise `max_interval_min` only if the unit's defrosts and stage delays are longer than the
  interval.
- **A defrost no longer reads as cooling a cold room (#185).** With the mode read from the
  compressor and the reversing valve, `hp_mode_vs_need`, `hp_capacity_shortfall` and
  `hp_room_imbalance` count a cooling run of at most `max_defrost_min` (15 minutes) with heating
  on both sides as heating. `hp_mode_vs_need` reports it as `defrost_hours`. `ASHP__no_aux_heat`
  on `synthetic-heat-pump` no longer warns for its defrosts.
- **`hp_capacity_shortfall`'s control verdict no longer calls a unit running the other way
  "mostly idle" (#185).** The summary gives the split (in the needed mode, the other mode, idle),
  and a new metric per side, `heating_opposite_pct` / `cooling_opposite_pct`, holds the share in
  the other mode.
- **An `ok` air-side, coil or chiller drift finding names no fault (#189).** The #187 fix now
  covers the other drift detectors that named a cause for any move, even one under the floors.
  `duct_static_drift` named "over-pressurization" or "the fan cannot hold setpoint",
  `chiller_cw_range_drift` "less condenser-water flow" or "more flow",
  `chiller_superheat_drift` an evaporator "starving" or "overfeeding", and
  `economizer_damper_drift` a damper "leaking / stuck" or "slipping closed". On a rise,
  `coil_valve_drift`, `vav_airflow_drift` and `vav_reheat_valve_drift` reported a "creep" with a
  cause, `filter_loading_drift` called it "loading" and `fan_efficiency_drift` an "excess".
  `coil_leak_drift` named "a leaking or passing valve" for a shift in the leak direction. An `ok`
  finding now says the signal "holds near its baseline", with the signed shift and its σ. An `ok`
  duct-static move that the static setpoint reset explains keeps its size and says so. All
  `warn` and `fault` wording is unchanged, and so is the wording that says a fall is not a
  fault. The pump and loop detectors now use the same helper, and their text is unchanged. Only
  the text moves: no golden file quotes these summaries, and no severity, metric, caveat or
  benchmark figure changes.
- **`cooling_tower_approach_drift` and `chiller_head_pressure_drift` say which way the signal
  moved (#190).** The tower summary said "approach widened" whatever the sign, so a fall read
  "widened -0.4°F". It now says "narrowed" with the size of the fall, and adds that a narrower
  approach is not a fault. `chiller_head_pressure_drift` said "climbed" on any rise, even an
  `ok` one, and printed a fall as a bare signed number. A fall now says "fell", still noting that
  a fall is not a high-side fault. An `ok` rise on either detector now says the signal "holds
  near its baseline", with the signed shift and its σ, as in #187 and #189. `warn` and `fault`
  wording is unchanged. Only the text moves: no golden file or workbook answer quotes these
  summaries, and no severity, metric or caveat changes.
