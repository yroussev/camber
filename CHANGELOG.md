# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.61.0] — 2026-08-24

**AHU cohort-starvation diagnosis (topology-aware fleet analytics — arc item 4).** The common-mode
twin of the rogue-zone census: where a rogue is one zone monopolizing an air handler's reset, a
starved cohort is most/all of an AHU's zones requesting at once — one upstream fault (duct-static SP
capped, supply fan maxed, restricted upstream damper), not N zone faults. A cross-layer call only the
served-by topology makes possible.

### Added
- **`camber.g36_reset.cohort_starvation`** + `CohortStarvationResult` — per AHU group, measures the
  fraction of active cycles on which at least `cohort_frac` of the group's zones request the reset at
  once; flags a group as starved when that sustained fraction clears `sustained_frac` (with group-size
  and active-cycle floors). Provably distinct from the rogue statistic: a lone dominant zone never
  reaches the cohort fraction, and a starved cohort shares requests too evenly to be a rogue.
- **`camber.rules.cohort_starvation_rule.CohortStarvation`** — a `FleetRule` shipped as two instances,
  `static_cohort_starvation` (airflow vs setpoint and damper — the primary case) and
  `sat_cohort_starvation` (zone temp vs cooling setpoint, caveated as a possible design-day). Scopes
  per air handler via the served-by topology (`wants_topology=True`), names the AHU, and says "look
  upstream, not at individual zones". Warn-level.

### Changed
- Internal refactor (no behaviour change): the shared per-zone request-series builder
  (`_build_request_series`) and the topology grouping-resolution + caveat matrix
  (`camber.rules._topology_grouping`) are factored out so the rogue-zone census and cohort-starvation
  twins stay byte-identical.

## [0.60.0] — 2026-08-24

**Topology-aware fleet grouping (topology-aware fleet analytics — arc item 3, the payoff).** The
fleet runner now hands a served-by `Topology` to grouping-aware rules, so the **rogue-zone census
auto-scopes per air handler** instead of pooling every zone building-wide.

### Added
- **`Registry.run_fleet(..., topology=None)`** — passes a served-by `Topology` to the rule. When it is
  `None` and the rule opts in (`wants_topology`), a **naming-heuristic** grouping is auto-built from
  the fleet's own equipment ids, so the census scopes per-AHU even with no semantic model.
- **`FleetRule.analyze_fleet(self, frames, *, topology=None)`** — the protocol gains an optional
  topology channel; the four non-grouping fleet rules ignore it (behaviour unchanged).

### Changed
- **`sat`/`static_rogue_zone_census`** now scope **per air handler** whenever a topology is available,
  with a provenance/coverage-aware caveat: a **semantic** grouping (Brick `feeds` / Haystack `ahuRef`)
  **drops** the confound caveat; a **heuristic** (naming-inferred) grouping keeps a softened screening
  caveat; **partial** coverage pools the uncovered remainder building-wide and caveats it; **no**
  topology retains the original building-wide pool + full caveat. New metrics `grouping_provenance`
  and `n_zones_ungrouped` record the grouping used.

### Notes
- Backward compatible: every existing `run_fleet` caller and a census with no topology reproduce the
  pre-0.60.0 output exactly. Heuristic auto-scoping is screening-grade and always labelled as such —
  never a false-confident per-AHU verdict.

## [0.59.0] — 2026-08-24

**Automatic served-by topology population (topology-aware fleet analytics — arc item 2).** Builds a
`Topology` (0.58.0) from a building's existing semantic model, or — as a last resort — its naming
conventions, each stamped with a `provenance` so consumers know how much to trust it.

### Added
- **`camber.interop.topology_from_brick(ttl, *, backend="auto")`** — a served-by `Topology` from Brick
  `feeds` (edge parent→child) and `isFedBy` (inverted); `provenance="semantic"`. `hasPart`
  (containment) is deliberately not treated as served-by. Reuses both parser backends (rdflib when
  present, else the zero-dependency minimal reader).
- **`site_from_ttl` now auto-populates `Site.topology`** from those relations — a Brick building with
  `feeds` needs no extra call (empty topology when the model has no flow relations; no regression).
- **`camber.interop.topology_from_haystack(entities, *, parent_refs=("ahuRef","equipRef"))`** — a
  served-by `Topology` from Haystack reference tags; `provenance="semantic"`. `equipRef` is followed
  only for `equip`-marked entities (a point's `equipRef` is ownership, handled by `roles_from_haystack`,
  not served-by); `siteRef` / `spaceRef` are ignored.
- **`camber.topology_infer.topology_from_naming(equips, *, ahu_classes=…, terminal_classes=…)`** — the
  screening-grade fallback: links a terminal to an air handler by a shared space label or an
  `AHU_1_VAV_3`-style id prefix, emitting an edge only when exactly one AHU matches (ambiguous /
  unmatched terminals skipped). `provenance="heuristic"` so consumers can caveat it.

### Notes
- Honest degradation throughout: unresolved refs / malformed inputs are skipped or raise a clear
  `ValueError` (Brick, matching `roles_from_brick`); cyclic relations are broken into a DAG by the
  `Topology` core; empty inputs yield an empty topology.
- **ASHRAE 223P** served-by extraction is **deferred** — 223P models flow as a multi-hop, medium-typed
  connection graph (not a single parent ref) and CAMBER emits none of it; Brick `feeds` covers the
  authoritative-semantic layer today.

## [0.58.0] — 2026-08-24

**Served-by topology model (topology-aware fleet analytics — arc foundation).** Adds a vocabulary for
*which equipment serves which* (plant → AHU → zone), the missing piece that lets fleet analytics scope
per system instead of pooling a whole building. No analytic consumes it yet; automatic population and
runner auto-scoping land in the following minors.

### Added
- **`camber.model.topology.Topology`** — a frozen directed served-by graph over equipment ids
  (edge `(parent, child)` = parent serves child). Explicit builders (`from_edges`, `from_parent_map`,
  `from_site`) and cycle-safe queries (`children_of` / `parents_of`, `descendants` / `ancestors`,
  `roots` / `leaves`, `zones_of`, `nearest_ancestor`, `group_of`, `group_map`). Layers are not
  hard-coded — plant/AHU/zone emerge from edge direction; class-aware queries take a predicate, so the
  graph stays id-only (no `Equip` dependency, no import cycle).
- **`Site.topology`** — a defaulted field carrying a `Site`'s served-by graph (empty by default, so
  every existing `Site(...)` constructor is unaffected).

### Notes
- Honesty is built into the type: a partial graph returns empty for unknown ids and `group_map`
  **omits** them (so a consumer keeps its building-wide fallback for the remainder); a cyclic input is
  broken into a best-effort DAG with removed edges recorded in `dropped_cycle_edges` (never a hang);
  and `provenance` (`explicit` / `semantic` / `heuristic`) lets downstream caveat a heuristic graph.
- No new roles; no behavior change to any existing analytic.
- **Release note:** versions **0.26.0 and 0.27.0 were withheld from PyPI.** Those two tags fall in the
  window where `HW_FLOW` had been added (0.26.0) but the deterministic BACnet role tie-break had not
  yet landed (fixed in 0.28.0), so an ambiguous gpm "Flow" point resolved `chw_flow`/`hw_flow`
  nondeterministically and their CI was unreliable. Both tags remain on GitHub; PyPI therefore skips
  from 0.25.0 to 0.28.0.

## [0.57.0] — 2026-08-23

**Rogue-zone census (G36 Trim-and-Respond) — Arc B item 3; family complete.** Where the compliance
and effectiveness rules look at the reset *setpoint*, this looks at the *demand side* that drives it:
in G36 the SAT / duct-static reset responds to the high-percentile of per-zone requests (§5.14.8), so
one chronically over-demanding zone can monopolize the requests and drag the whole reset — the plant
then serves one bad box at everyone else's energy expense.

### Added
- **`camber.g36_reset.rogue_zone_census`** + `RogueZoneCensusResult` — a fleet analytic that computes
  each zone's per-cycle reset-request series (a vectorized `cooling_sat_requests` /
  `static_pressure_requests`), pools zones by an optional `groups` map, and per group scores each zone
  by its **share** of the group's total requests and the **fraction of active cycles it holds the
  binding (maximum) request**. A zone is a **rogue** when it clears both — requiring both keeps a
  uniformly-busy fleet quiet (equal shares → no one singled out).
- **`camber.rules.rogue_zone_census_rule.RogueZoneCensus`** — a `FleetRule` shipped as two registered
  instances, `sat_rogue_zone_census` (zone temp vs cooling setpoint) and `static_rogue_zone_census`
  (zone airflow vs setpoint and damper). Warn-level; names the worst offender; declines to `info`
  when zones were unevaluable or no zone generated any request.

### Notes
- **No new roles** (reuses `SPACE_TEMP` / `COOL_SP` / `AIRFLOW` / `AIRFLOW_SP` / `DAMPER`).
- **Topology honesty:** without a zone→AHU map the census pools zones building-wide and attaches a
  loud confound caveat (a flagged zone may simply serve a hotter loop — a screening signal only);
  supplying `groups` (`{zone: ahu}` dict or `zone → ahu` callable) scopes it per air handler and
  drops the caveat. The deferred piece is *automatic* zone→AHU discovery: the census accepts a
  grouping, the fleet runner does not yet derive one.
- Completes the Trim-and-Respond / G36-reset family (five detectors on the shared `camber.g36_reset`
  engine). Screening-grade thresholds.

## [0.56.0] — 2026-08-23

**Reset effectiveness (G36 Trim-and-Respond) — Arc B item 2.** Where `supply_air_reset_compliance`
(0.55.0) asks whether the reset sits at the right *target*, this asks whether the reset *mechanism*
works at all: does the actual reset setpoint follow the Trim-and-Respond trajectory the plant's own
requests imply?

### Added
- **`camber.g36_reset.reset_effectiveness`** + `ResetEffectivenessResult` — a reset-agnostic analyzer
  that reconstructs the expected setpoint from the per-cycle reset-request count (`tr_simulate`,
  G36 §5.1.14) and scores the trended setpoint against it, classifying four failure modes: **stuck**
  (flat while requests demand movement), **not responding** (parked at the energy-saving end under
  demand — zones starve), **not trimming** (parked at the demand end while idle — energy wasted), and
  **diverges** (moving the wrong way vs the T&R command). The reconstructed-trajectory error
  (`mean_abs_error_sp`) is informational only; the verdict rests on the cadence-robust mode detectors.
- **`camber.rules.reset_effectiveness_rule.ResetEffectiveness`** — shipped as two registered instances,
  `sat_reset_effectiveness` (supply-air-temp setpoint, °F, `SAT_TR` preset) and
  `static_reset_effectiveness` (duct-static setpoint, in. w.c., `STATIC_TR` preset). Two-sided
  (starving and wasting both fault), warn-level. Needs the reset **setpoint and the request count**
  both mapped, declining loudly on trend exports that carry no request point.
- **`Role.SAT_RESET_REQUESTS`** / **`Role.STATIC_PRESSURE_REQUESTS`** — the aggregated per-cycle
  reset-request count points, with Haystack mapping hints.

### Notes
- The zone-fleet path (computing the requests from the zone census rather than a mapped point) is
  deferred to the planned rogue-zone census: the current fleet runner hands rules a flat
  per-equipment frame map with no zone→AHU grouping, so it cannot yet express it.

## [0.55.0] — 2026-08-23

**SAT-reset compliance (G36) — Arc B begins.** Opens a new Trim-and-Respond / Guideline-36 reset
analytics family by wiring the dormant `camber.g36_reset` engine into a rule.

### Added
- **`camber.rules.satreset_compliance_rule.SupplyAirResetCompliance`** (`supply_air_reset_compliance`)
  — flags supply air held **colder than the G36 §5.16.2.2.b OAT→SAT reset target** (an avoidable
  reheat/energy opportunity), wrapping the existing-but-unwired `camber.g36_reset.sat_reset_compliance`
  analyzer. Compares actual SAT to the OAT-*computed* target (not to a mapped setpoint), so it works
  on a typical SAT+OAT trend export. Complementary to the existing `supply_air_reset` slope check:
  one asks "does it reset up at all?", this asks "is it colder than the G36 target?". One-sided
  (too-cold-vs-target), warn-level (opportunity, not a fault), with a mean-gap floor so trivial gaps
  don't flag; the four G36 map parameters are constructor args for site-specific schedules.
  Auto-registered; declines loudly when OAT is unmapped or too few rows. New family doc
  `docs/TR-RESET.md`.

## [0.54.0] — 2026-08-22

**VAV drift in export + reporting** — the per-box verdict where it acts, completing the VAV
zone-terminal drift family (detectors → diagnosis → sim → surfacing).

### Added
- **`camber.integrate.export.vav_diagnoses_to_frame` / `export_vav_diagnoses`** — flatten the per-box
  VAV drift diagnoses (`camber.vavdrift.VavDriftDiagnosis`) into a one-row-per-box table (locus ·
  severity · box_wide · corroborated · joined causes · caveat count · stable fingerprint) and write
  CSV / JSON / Parquet. Like the AHU table it carries a locus + a wide flag (`box_wide`).
- **`camber.report.vav_diagnosis_table`** — a self-contained HTML table of the VAV verdicts, ranked
  worst-first, flagging box-wide cases; a standalone renderer to splice into a report.

### Changed
- **`camber.report.build_site_report`** gains an optional `vav_diagnoses=` argument that renders the
  `vav_diagnosis_table` after the AHU table. Backward compatible.

## [0.53.0] — 2026-08-22

**VAV physics validator** — `camber.vavsim`, characterizing the terminal-box drift family end-to-end
and directly measuring the plant-vs-box disambiguation.

### Added
- **`camber.vavsim`** — a physics-grounded synthetic generator for the VAV zone terminal that runs
  the two real VAV drift detectors + `diagnose_vav_drift` end-to-end without a dataset. Because the
  diagnosis returns a `locus`, it scores a **`LocusConfusion`** (like `ahusim`/`pumpsim`) over the
  five loci — steady · airflow · reheat · upstream · box-wide. The generator models a single
  **two-regime diurnal box**: occupied-daytime cooling (swept command + modulating damper + closed
  reheat) feeds `VavAirflowDrift`, night/morning heating (min airflow + modulating reheat valve)
  feeds `VavReheatValveDrift` — each detector's gating carves out its regime. The **upstream-vs-box
  disambiguation is directly measured**: `damper_authority_loss → airflow` and `upstream_starvation
  → upstream` inject the same damper creep, but only the latter also drops the upstream `DUCT_STATIC`
  (tripping `vav_upstream_starvation_suspected`); `box_wide → box-wide`. A mild `hw_reset` is a
  `steady` negative (the reheat HW confound is a caveat, not a locus demotion — noted honestly). On
  clear faults it localizes all five loci at ~100% with no false alarms. Public API: `VavFault`,
  `FAULTS`, `SimulatedCase`, `simulate_case`, `make_cases`, `build_vav_suite`, `diagnose_vav_frames`,
  `LocusConfusion`, `locus_confusion`.

## [0.52.0] — 2026-08-22

**Per-box VAV drift verdict** — `diagnose_vav_drift` rolls the two VAV detectors into one localized
diagnosis with the upstream-vs-box disambiguation.

### Added
- **`camber.vavdrift.diagnose_vav_drift` / `VavDriftDiagnosis`** — synthesize the `vav_airflow_drift`
  and `vav_reheat_valve_drift` Findings for one box into a single localized verdict: names each cause,
  flags corroboration when both agree, and runs the **upstream-vs-box disambiguation** (the headline,
  the terminal-box twin of `diagnose_ahu_drift`'s fan-power resolution). A damper creep is ambiguous
  between the box's own actuator/linkage failing and upstream duct-static starvation (a plant fault);
  the airflow detector's `vav_upstream_starvation_suspected` flag resolves it — creep + flag → locus
  `upstream` ("fix the plant, not the box"), creep without it → locus `airflow`. Reheat creep → locus
  `reheat` (a co-moving HW-supply fall is caveated). Reports a `locus`
  (steady · airflow · reheat · upstream · box-wide) + a `box_wide` flag; an `upstream` verdict is a
  plant symptom **excluded** from `box_wide`, so only two real box faults (airflow + reheat) read as
  box-wide. Screening-grade; pure over Findings.

## [0.51.0] — 2026-08-22

**Second VAV zone-terminal drift detector** — reheat-coil heat-transfer drift, the leading indicator
to the instantaneous reheat rules. Also wires the AHU / pump / VAV drift-family docs into the site
nav (they existed but were only cross-linked).

### Added
- **`camber.rules.vav_reheat_valve_rule.VavReheatValveDrift`** — catches a VAV box's hot-water reheat
  coil losing heat-transfer capacity (waterside fouling/scale, low HW flow or ΔT, air bypass,
  valve-authority loss): the reheat valve creeps open to deliver the same reheat, weeks before the box
  misses setpoint and `reheat_penalty` / `reheat_minimization_g36` see it. Freezes a
  `valve ~ f(reheat duty)` baseline and scores the current period's valve residual at matched duty;
  **one-sided up** (a valve fall is a capacity gain, not a fault). The load is the **reheat duty ≈
  airflow × ΔT**, not ΔT alone — a VAV box's airflow varies, and G36 reheat is not reliably pinned at
  min flow (flow rises in the high heating loop, the regime `reheat_minimization_g36` flags), so duty
  is correct across both regimes. The box's entering primary air is mapped to `MIXED_AIR_TEMP` (the
  coil-valve heating convention), so no new role is added; a colder HW-supply reset is caveated, not
  subtracted; `load_basis="deltat"` is a constructor option for boxes without a mapped flow. Registers
  a `vav_reheat_valve` model kind. Screening-grade; declines loudly. Not auto-registered (injected
  `BaselineStore`).

### Changed
- **Docs nav** — the `PUMP-DRIFT`, `AHU-DRIFT`, and `VAV-DRIFT` family pages are now listed under
  Analytics (previously present but only reachable via cross-links). `docs/VAV-DRIFT.md` gains the
  reheat detector.

## [0.50.0] — 2026-08-22

**First VAV zone-terminal drift detector** — opens a new drift family for the terminal box, the
leading indicator to the instantaneous zone rules.

### Added
- **`camber.rules.vav_airflow_rule.VavAirflowDrift`** — catches a VAV box's damper creeping open at
  matched commanded airflow: a slipping/worn actuator, a stuck linkage, or rising upstream duct-static
  starvation makes the box spend its reserve damper authority to hold the same flow, weeks before the
  instantaneous `airflow_tracking` undershoot check fires. Freezes a `damper ~ f(commanded airflow)`
  baseline and scores the current period's damper residual at matched command; **one-sided up** (less
  damper is an authority gain, not a fault). The airflow-setpoint confound is neutralized *by
  construction* (the setpoint is the load axis), and an upstream duct-static fall (via the runner's
  `shared` channel) is surfaced as a starvation caveat + `vav_upstream_starvation_suspected` metric so
  the box is never silently blamed for a plant problem. `load_role=AIRFLOW` is a constructor option
  for boxes without a mapped setpoint. Reuses existing roles (`DAMPER` / `AIRFLOW_SP`); registers a
  `vav_damper` model kind. Screening-grade; declines loudly on unmapped inputs or a command that never
  sweeps. Not auto-registered (injected `BaselineStore`; run via `Registry.run_periods`). New family
  doc `docs/VAV-DRIFT.md`.

## [0.49.0] — 2026-08-21

**Evaporator / chilled-water physics validator** — `camber.evaporatorsim`, completing the evaporator
family to parity with the chiller/pump/AHU/condenser sims (detectors → diagnosis → sim → surfacing).

### Added
- **`camber.evaporatorsim`** — a physics-grounded synthetic generator for the evaporator / low side
  that runs the three real evaporator drift detectors + `diagnose_evaporator_drift` end-to-end
  without a dataset. Because the diagnosis produces a *cause + corroboration* verdict (no `locus`),
  it scores a **`CauseConfusion`** (mirroring `camber.condensersim`). The low side is coupled through
  one shared feed latent — an overfeed lowers superheat and raises suction pressure together, a
  starvation raises superheat and lowers suction — so the superheat-vs-suction cross-check fires
  emergently and the diagnosis corroborates it; evaporator fouling widens the approach alone. Includes
  the negative confound `chw_reset` (a chilled-water-supply shift lifts suction via the evaporating
  temperature with superheat quiet), which the cross-check correctly does not corroborate. On clear
  faults it names the right cause with the right corroboration flag at ~100% and no false alarms.
  Public API: `EvaporatorFault`, `FAULTS`, `SimulatedCase`, `simulate_case`, `make_cases`,
  `build_evaporator_suite`, `diagnose_evaporator_frames`, `CauseConfusion`, `cause_confusion`.

## [0.48.0] — 2026-08-21

**Evaporator / chilled-water drift in export + reporting** — the low-side verdict where it acts,
bringing the evaporator family toward parity with the chiller/pump/AHU/condenser ones.

### Added
- **`camber.integrate.export.evaporator_diagnoses_to_frame` / `export_evaporator_diagnoses`** —
  flatten the per-loop evaporator / CHW drift diagnoses
  (`camber.evaporatordrift.EvaporatorDriftDiagnosis`) into a one-row-per-loop table (severity ·
  corroborated · joined causes · caveat count · stable fingerprint) and write CSV / JSON / Parquet.
  Like the condenser diagnosis it carries no locus / loop-wide flag, so those columns are absent.
- **`camber.report.evaporator_diagnosis_table`** — a self-contained HTML table of the evaporator
  verdicts, ranked worst-first, flagging corroborated loops.

### Changed
- **`camber.report.build_site_report`** gains an optional `evaporator_diagnoses=` argument that
  renders the `evaporator_diagnosis_table` between the condenser and pump verdict tables. Backward
  compatible.

## [0.47.0] — 2026-08-21

**One-way cybersecure edge→cloud forwarder** — `camber.edge`: push BAS trend data to an org cloud
data lake from a Raspberry Pi or a Windows BAS front-end, built to pass IT/network-security review.

### Added
- **`camber.edge`** — a one-way forwarder that reads BAS trends **read-only** (historian-first; live
  BACnet/BACnet-SC/Modbus/OPC-UA only when there is no historian), maps point→role, runs
  `quality.assess` (report-only), serializes Parquet **directly into the `ParquetStore`
  `facility_id=/year=` Hive layout** (so the cloud reads it with the existing
  `read_long`/`ReadAPI`, zero transform), and **store-and-forwards it outbound-only** through a
  pluggable `Sink`. Public API: `Sink`, `PresignedHttpsSink`, `S3Sink`, `AzureBlobSink`, `GcsSink`,
  `collect_sink`, `Spool`, `SpoolEntry`, `DrainResult`, `Forwarder`, `BatchResult`, `EdgeConfig`,
  `load_config`, `build_forwarder`.
  - **`PresignedHttpsSink`** (default) — stdlib `urllib` HTTPS PUT to a short-lived presigned URL (or
    a per-object broker), **no long-lived cloud credentials on the edge**, TLS verification never
    disabled, single-host egress allowlist. SDK sinks (S3/Azure/GCS) are behind new
    `edge-s3`/`edge-azure`/`edge-gcs` extras; the default path adds **zero new dependencies**.
  - **`Spool`** — a durable, crash-safe store-and-forward queue (atomic enqueue, write-ahead
    journal, retry+backoff, backfill after reboot, bounded-disk eviction with a logged WARNING) so a
    connectivity loss never loses a batch.
  - **`camber edge` CLI** — `run` (daemon), `send-once` (cron / Windows Task Scheduler),
    `status` (spool depth), `selftest` (dry-run through an in-memory sink; proves the read-only path
    with no egress).
- **`docs/EDGE-DEPLOY.md`** — the IT-approval security dossier: the one-way reference architecture,
  the enforced-properties table (each mapped to the test that proves it), NIST SP 800-82r3 / ISA-62443
  / ASHRAE-135 mapping, Pi (systemd) + Windows (Task Scheduler) install recipes, and an
  egress-allowlist request template.

### Changed
- The read-only ingest AST guard (`tests/test_ingest_protocols.py`) now also scans every
  `camber/edge` module and fails if it references a BAS-write service, an inbound-listener identifier
  (`bind`/`listen`/`accept`/`recv`/`socket`/`*HTTPServer`), or a TLS-disabling identifier — so the
  edge is *provably* read-only toward the BAS and one-way toward the cloud.

## [0.46.0] — 2026-08-21

**Condenser heat-rejection physics validator** — `camber.condensersim`, closing the condenser family
to parity with the chiller/pump/AHU sims (detectors → diagnosis → sim → surfacing).

### Added
- **`camber.condensersim`** — a physics-grounded synthetic generator for the condenser-water /
  cooling-tower loop that runs the four real condenser drift detectors + `diagnose_condenser_drift`
  end-to-end without a dataset. Because the diagnosis produces a *cause + corroboration* verdict (it
  has no `locus`), the validator scores a **`CauseConfusion`** (did it name the expected cause, with
  the right corroboration flag?) rather than a locus confusion. The loop is coupled through two
  shared quantities — condensing temperature `TCOND = CWS + condenser approach` and entering water
  `CWS = wet-bulb + tower approach` — so co-movement is emergent: tube scaling widens the condenser
  approach and lifts head pressure (corroborated system-side scaling); a fouling tower lifts `CWS`
  and head pressure. It includes the negative confound `ambient_cw_rise` (a CW/head rise with a quiet
  tower) that the head-pressure confound demotes to likely-ambient rather than flagging. On clear
  faults it names the right cause and sets the right corroboration flag at ~100% with no false
  alarms. Public API: `CondenserFault`, `FAULTS`, `SimulatedCase`, `simulate_case`, `make_cases`,
  `build_condenser_suite`, `diagnose_condenser_frames`, `CauseConfusion`, `cause_confusion`.

## [0.45.0] — 2026-08-21

**Condenser heat-rejection drift in export + reporting** — the condenser-loop verdict where it acts,
bringing the heat-rejection family toward parity with the chiller/pump/AHU ones.

### Added
- **`camber.integrate.export.condenser_diagnoses_to_frame` / `export_condenser_diagnoses`** — flatten
  the per-loop condenser heat-rejection diagnoses (`camber.condenserdrift.CondenserDriftDiagnosis`)
  into a one-row-per-loop table (severity · corroborated · joined causes · caveat count · stable
  fingerprint) and write CSV / JSON / Parquet. Unlike the AHU/chiller tables the condenser diagnosis
  carries no locus / loop-wide flag, so those columns are absent by design.
- **`camber.report.condenser_diagnosis_table`** — a self-contained HTML table of the condenser
  verdicts, ranked worst-first, flagging corroborated loops; a standalone renderer to splice into a
  report.

### Changed
- **`camber.report.build_site_report`** gains an optional `condenser_diagnoses=` argument that renders
  the `condenser_diagnosis_table` between the chiller and pump verdict tables. Backward compatible.

## [0.44.0] — 2026-08-21

**ahusim exercises the outdoor-air locus** — the physics validator now models the economizer OA
mixing box, so the fifth AHU locus is characterized end-to-end alongside the original four.

### Changed
- **`camber.ahusim`** gains an outdoor-air / economizer mixing regime. `MIXED_AIR_TEMP` is now a
  genuine outdoor/return-air mix driven by a swept `OA_DAMPER` command (`OAT` and `RETURN_AIR_TEMP`
  channels added), so the economizer detector's OA-fraction signal is exercised; `SUPPLY_AIR_TEMP` is
  derived as `MAT − dt` so the cooling-coil air-ΔT is preserved by construction and the existing four
  fault families stay bit-identical (the confusion matrix is undisturbed). Adds an `AhuFault`
  `d_oa_fraction` lever and two faults — `econ_damper_leak` (over-delivery, up) and
  `econ_damper_stuck_closed` (under-delivery, down), both `expected_locus="outdoor-air"` — and wires
  `EconomizerDamperDrift` into `build_ahu_suite`. On clear faults the diagnosis localizes all five
  loci at ~100% with no economizer false alarms on healthy AHUs. No public API change (new symbols
  are internal constants, a dataclass field, and `FAULTS` data).

## [0.43.0] — 2026-08-21

**Economizer wired into the per-AHU verdict** — `diagnose_ahu_drift` now consumes the economizer
drift detector as the fifth `outdoor-air` locus, completing the AHU roll-up.

### Changed
- **`camber.ahudrift.diagnose_ahu_drift`** now reads `economizer_damper_drift` and localizes it to a
  new **`outdoor-air`** side (economizer OA mixing). It is an *independent* side like a coil: it
  contributes a localized cause (up = damper leaking / stuck-open = excess OA; down = stuck or
  slipping closed = lost free cooling / under-ventilation), corroborates with other signals, and can
  make the verdict `ahu-wide` — but it is held **outside** the fan-power disambiguation by design,
  because its signal is outdoor-air fraction, not fan power. A declined economizer finding is a
  caveat, not a cause. `AhuDriftDiagnosis.locus` gains the `outdoor-air` value; no API change
  (existing fields, `as_dict`, and `__all__` are unchanged). The `outdoor-air` locus is not yet
  exercised by `camber.ahusim`'s confusion matrix — the OA-mixing simulation regime is a documented
  follow-on.

## [0.42.0] — 2026-08-21

**Economizer OA-delivery drift** — the fifth AHU air-side detector, completing the family's
mechanical coverage (fan · filter · duct-static · coil · **economizer damper**).

### Added
- **`camber.rules.economizer_damper_rule.EconomizerDamperDrift`** — catches an outdoor-air damper no
  longer delivering its baseline outdoor-air fraction *for the same command*: linkage slipping, seals
  leaking, the blade sticking, minimum-position creep. Freezes an `OAF ~ f(damper command)` baseline
  (`OAF = 100·(RAT−MAT)/(RAT−OAT)`) and scores the current period's OA-fraction residual at matched
  command. **Two-sided:** a residual up = a leaking / stuck-open damper (excess outdoor air), down = a
  stuck or slipping-closed damper (lost free cooling / under-ventilation). Reuses the existing `OAT` /
  `RETURN_AIR_TEMP` / `MIXED_AIR_TEMP` / `OA_DAMPER` roles (no new role); registers a
  `economizer_damper` model kind. It is a *mechanical* delivery check, not a sequence check — an
  economizer commanded wrong for the conditions remains the job of `economizer_lockout_rule` /
  `freecoolingmissed_rule`. Confounds handled: the mixed-air sensor stratifies badly and sits in the
  ratio's numerator, so a standing caveat (Sellers, *Relative Accuracy*) flags it and the magnitude
  floor sits above that noise; ill-conditioned rows (`|RAT−OAT|` small) are excluded before the fit
  and the excluded fraction reported. Screening-grade; declines loudly when a required point is
  unmapped or the damper command never sweeps a usable range. Rolling it into `diagnose_ahu_drift` as
  a fifth `outdoor-air` locus is a follow-on.

## [0.41.0] — 2026-08-21

**AHU drift in export + reporting** — the air-side verdict where it acts, completing the AHU family.

### Added
- **`camber.integrate.export.ahu_diagnoses_to_frame` / `export_ahu_diagnoses`** — flatten the per-AHU
  air-side drift diagnoses (`camber.ahudrift.AhuDriftDiagnosis`) into a one-row-per-AHU table (locus ·
  severity · ahu_wide · corroborated · joined causes · caveat count · stable fingerprint) and write
  CSV / JSON / Parquet, mirroring the findings, chiller- and pump-diagnosis exports.
- **`camber.report.ahu_diagnosis_table`** — a self-contained HTML table of the AHU verdicts, ranked
  worst-first, flagging AHU-wide cases; a standalone renderer to splice into a report.

### Changed
- **`camber.report.build_site_report`** gains an optional `ahu_diagnoses=` argument that renders the
  `ahu_diagnosis_table` alongside the chiller and pump verdict tables, under the health scorecard.
  Backward compatible.

## [0.40.0] — 2026-08-21

**Physics-grounded AHU validation** — characterize the air-side drift stack without a dataset.

### Added
- **`camber.ahusim`** — the air-side mirror of `camber.pumpsim` / `camber.driftsim`: a physically
  consistent synthetic generator that produces `(baseline, current)` role-frame pairs for a healthy
  air handler and for the standard air-side fault families (fan belt slip · bearing drag · filter
  loading · duct-static loss · over-pressurization · cooling-coil fouling · a static-reset negative),
  imposing each fault's signature at a graded severity. **The channels are coupled through the system
  curve** (ΔP ∝ Q²): fan power is computed from the sum of component pressures, so a **loading filter
  raises the filter-DP channel AND fan power** (the fan fights more upstream drop) — the emergent
  co-move that makes the diagnosis's **fan-power disambiguation** real (filter loading → air-path;
  fan-mechanical → fan). Helpers run the whole suite + AHU diagnosis end-to-end (`build_ahu_suite`,
  `diagnose_ahu_frames`) and score localization (`locus_confusion`); `SimulatedCase.to_labeled` feeds
  the existing `driftvalidation` per-detector ROC. On clear faults (severity ≥ 3) the diagnosis
  localizes to the correct `locus` at ~100% with no false alarms on healthy AHUs **or a static-reset
  schedule**, proves the fan-power disambiguation routes correctly, and degrades gracefully at
  marginal severity. v1 models the cooling coil as the single active coil (a heating regime is a
  documented follow-on). Deterministic; core deps only; no dataset shipped.

## [0.39.0] — 2026-08-19

**Per-AHU co-movement diagnosis** — the air-side analog of `diagnose_pump_drift`.

### Added
- **`camber.ahudrift.diagnose_ahu_drift`** (+ `AhuDriftDiagnosis`) — synthesizes the four air-side
  drift Findings (fan efficiency, filter loading, duct static, coil valve) into one per-AHU diagnosis:
  it names each drifting signal's localized cause, flags **corroboration** when two or more agree, and
  runs the **fan-power disambiguation** no single signal can — a fan-power excess **with** a loading
  filter or rising duct static is the *air path* (fix the filter/duct first; the fan power is
  corroborating), **with** a falling duct static is *fan degradation*, with a **clean** filter and
  **steady** static is the *fan itself*, and with **no** filter/static point is called ambiguous. It
  splits the AHU into fan (mechanical) / air-path (filter + static) / coil sides, reports a `locus`
  (steady · fan · air-path · coil · ahu-wide) and an `ahu_wide` flag, names a cooling and a heating
  coil separately, and stays screening-grade; pure over Findings.

### Changed
- **`camber.rules.coil_valve_rule.CoilValveDrift`** now emits a `coil_valve_which` metric
  (`"cooling"`/`"heating"`) so the AHU diagnosis can name and de-duplicate the two coils. Additive.

## [0.38.0] — 2026-08-19

**Coil heat-transfer drift** — valve-position creep at matched delivered air-ΔT.

### Added
- **`camber.rules.coil_valve_rule.CoilValveDrift`** — detects a cooling or heating coil losing
  heat-transfer capacity (fouling, waterside starvation / low flow, air bypass, valve-authority loss)
  as **valve creep at matched delivered air-ΔT**: the valve opening further over time to hold SAT. The
  ΔT (MIXED_AIR ↔ SUPPLY_AIR) is the exogenous weather-driven demand and the valve is the endogenous
  response, so `valve ~ f(ΔT)` isolates the coil's transfer function — fouling raises valve-at-matched-ΔT
  with the weather unchanged. It's the **leading** indicator to `satcontrol_rule`'s off-setpoint failure
  (fires weeks before the valve pins). One-sided up (a valve *fall* is a capacity gain). **Economizer
  samples are gated out** (during free cooling the cool valve is driven by mixed-air control, not coil
  demand); the **waterside-reset confound is caveated** (colder CHW / hotter HW needs less valve). A
  known screening limitation — the sensible air-ΔT misses a wet cooling coil's latent load — is stated
  in the docs. Coil-parameterized (a cooling and a heating coil on one AHU freeze under distinct model
  kinds); reuses only existing roles; declines loudly when the valve or air-temperature pair is
  unmapped; not auto-registered. Grounded in Sellers, *Relative Accuracy* (a coil ΔT is more
  trustworthy than absolute temps).

## [0.37.0] — 2026-08-19

**Duct static-pressure control drift** — reset-schedule aware, the air-side twin of loop-DP drift.

### Added
- **`camber.rules.duct_static_rule.DuctStaticControlDrift`** — tracks a VAV system's duct static
  drifting from a frozen, airflow-normalized `static ~ f(airflow)` baseline. **Two-sided**: a *fall*
  at matched airflow means the fan can no longer hold setpoint (fan/belt degradation, a leakier duct
  system); a *rise* is over-pressurization (a static sensor reading low, a stuck downstream damper, or
  collapsed demand with the setpoint stuck). **The static-reset (Guideline-36 trim-and-respond)
  confound is handled, not just flagged:** when a duct-static-setpoint point is mapped the rule judges
  on the residual drift *not* explained by the setpoint move — a static move fully accounted for by a
  reset does not fault (reported as a caveat), and a partial one is demoted to the residual tier. The
  air-side twin of `loop_dp_rule`. Reuses the existing `DUCT_STATIC` / `AIRFLOW` / `DUCT_STATIC_SP`
  roles (no new role); declines loudly when static or airflow is unmapped; not auto-registered.

## [0.36.0] — 2026-08-19

**Air-filter loading drift** — the dirty-filter detector, normalized for airflow.

### Added
- **`camber.rules.filter_loading_rule.FilterLoadingDrift`** — tracks a filter's pressure drop drifting
  **up** at matched airflow from a frozen (clean-filter) `filterDP ~ f(airflow)` baseline. A filter's
  DP rises monotonically as its media loads, but it also grows with face velocity, so on a VAV system
  raw DP confuses "more air" with "dirtier"; the system curve is quadratic in flow (ΔP ∝ Q²), so the
  residual at matched airflow isolates the loading (physics per Chimack & Sellers, ACEEE Summer
  Study). One-sided up — a DP *fall* is a filter change (a welcome reset), not a fault. Filter DP is
  measured across the filter, so the signal is filter-specific (a wetted coil or duct restriction that
  raises *system* static does not move it); airflow is the only confound and normalization removes it.
  A sustained rise is the "schedule a filter change" signal, weeks before a static alarm. Reuses the
  existing `FILTER_DIFF_PRESS` + `AIRFLOW` roles (no new role); declines loudly when either is
  unmapped; not auto-registered.

## [0.35.0] — 2026-08-19

**Supply-fan efficiency drift** — the first of the AHU / air-side drift family.

### Added
- **`camber.rules.fan_efficiency_rule.FanEfficiencyDrift`** — tracks a supply fan's power drifting
  **up** from a frozen `power ~ f(airflow)` baseline: a power *excess* at matched airflow is
  wire-to-air efficiency loss (a slipping/worn belt, bearing drag, a degrading motor/VFD, or the fan
  pushed off its curve) — the air-side twin of `PumpPowerDrift`, and a direct energy-cost signal since
  fan energy is a large share of an AHU's use. One-sided up; reuses the generic `Role.POWER` on the
  AHU equip-frame (no new role) with `Role.AIRFLOW` as the normalizer. **The duct-static confound is
  surfaced:** fan power also rises when the static setpoint is raised, so a power excess that co-moves
  with rising duct static is reported and caveated. Declines loudly when power or airflow is unmapped;
  not auto-registered. First of the air-side family (docs/AHU-DRIFT.md), complementing the static
  `economizer_lockout` / `satreset` / `staticreset` rules.

## [0.34.0] — 2026-08-18

**Pump drift in export + reporting** — the loop verdict where it acts, completing the pump family.

### Added
- **`camber.integrate.export.pump_diagnoses_to_frame` / `export_pump_diagnoses`** — flatten the
  per-loop pump drift diagnoses (`camber.pumpdrift.PumpDriftDiagnosis`) into a one-row-per-loop table
  (locus · severity · loop_wide · corroborated · joined causes · caveat count · stable fingerprint)
  and write CSV / JSON / Parquet, mirroring the findings and chiller-diagnosis exports.
- **`camber.report.pump_diagnosis_table`** — a self-contained HTML table of the loop verdicts, ranked
  worst-first, flagging loop-wide cases; a standalone renderer to splice into a report.

### Changed
- **`camber.report.build_site_report`** gains an optional `pump_diagnoses=` argument that renders the
  `pump_diagnosis_table` alongside the chiller verdict table, just under the health scorecard.
  Backward compatible.

## [0.33.0] — 2026-08-18

**Physics-grounded pump validation** — characterize the pump drift stack without a dataset.

### Added
- **`camber.pumpsim`** — the pump-family mirror of `camber.driftsim`: a physically consistent synthetic
  generator (affinity laws Q ∝ N, H ∝ N², P ∝ Q + the system curve) that produces `(baseline, current)`
  role-frame pairs for a healthy pump loop and for the standard pump/hydronic fault families (impeller
  wear · cavitation · bearing drag · entrained air · clogged strainer · overpumping · valve-authority
  loss · a DP-reset negative), imposing each fault's known signature at a graded severity. Helpers run
  the **whole pump suite + loop diagnosis** end-to-end (`build_pump_suite`, `diagnose_pump_frames`) and
  score the diagnosis' localization (`locus_confusion`); `SimulatedCase.to_labeled(relevant=…)` feeds
  the existing `camber.driftvalidation` per-detector ROC. On clear faults (severity ≥ 3) the loop
  diagnosis localizes to the correct `locus` at ~100% with no false alarms on healthy loops **or a
  DP-reset schedule**, proves the flow-vs-head disambiguation (impeller wear → pump, clogged strainer →
  distribution), and degrades gracefully at marginal severity. Deterministic; core deps only.

## [0.32.0] — 2026-08-18

**Per-plant pump roll-up** — which pump to stage/service, or a shared cause.

### Added
- **`camber.pumpplantdiag.diagnose_pump_plant`** (+ `PumpPlantDiagnosis`) — rolls the per-loop pump
  diagnoses across a plant's pumps (lead/lag, primary/secondary, per-zone) into one verdict with the
  cross-pump reasoning no single loop can do: exactly one pump drifting → **single-pump** (stage the
  spare, schedule that impeller); two or more loops drifting on the **distribution** side → a shared /
  central hydraulic cause (plant-wide low-ΔT, a decoupler bypass, a control problem) is more likely
  than several independent pumps — look there first; two or more pumps otherwise → **plant-wide** (a
  common-mode cause: suction conditions, water chemistry, a shared drive/control). Reports a plant
  `locus` (steady · single-pump · distribution · plant-wide), the worst severity, the nested per-loop
  diagnoses, and a plain-language `recommendation`. Screening-grade; pure over the per-loop diagnoses.

## [0.31.0] — 2026-08-18

**Pump power drift + fold-in** — the wire-to-water efficiency signal, corroborating the pump side.

### Added
- **`camber.rules.pump_power_rule.PumpPowerDrift`** — tracks a pump's electrical power drifting **up**
  from a frozen `power ~ f(flow)` baseline (P ∝ Q³): a power *excess* at matched flow is wire-to-water
  efficiency loss (bearing/seal drag, a degrading motor/drive, internal recirculation, off-BEP
  operation) — the energy-cost complement to the flow/head detectors. One-sided up; reuses the generic
  `Role.POWER` on the pump equip-frame (no new role); declines loudly when power or flow is unmapped.
- **`camber.pumpdrift.diagnose_pump_drift`** now folds `pump_power_drift` in as a **pump-side
  (mechanical)** signal — a power excess corroborates a flow/head-derived pump fault.

## [0.30.0] — 2026-08-18

**Per-loop pump/hydronic drift diagnosis** — one localized verdict, with the flow-vs-head call.

### Added
- **`camber.pumpdrift.diagnose_pump_drift`** (+ `PumpDriftDiagnosis`) — synthesizes the four
  pump/hydronic drift Findings (pump flow, pump head, loop ΔT, loop DP) into one per-loop diagnosis:
  it names each drifting signal's localized cause, flags **corroboration** when two or more agree, and
  runs the **flow-vs-head disambiguation** no single signal can — a flow deficit **and** head deficit
  is the pump itself (impeller/wear-ring/cavitation); a flow deficit with **steady head** is the
  distribution (a throttled valve downstream), not the pump; a flow deficit with **no head point** is
  called ambiguous. It splits the loop into a mechanical (pump) and a hydraulic (distribution) side,
  reports a `locus` (steady · pump · distribution · loop-wide) and a `loop_wide` flag, and stays
  screening-grade; pure over Findings.

## [0.29.0] — 2026-08-18

**Hydronic loop DP drift** — the system-resistance / control detector, reset-schedule aware.

### Added
- **`camber.rules.loop_dp_rule.LoopDPDrift`** — tracks a loop's differential pressure drifting from a
  frozen, flow-normalized `DP ~ f(flow)` baseline (system curve DP ∝ Q²). **Two-sided**: a *rise* at
  matched flow is added system resistance / valve-authority loss; a *fall* is a bypass / stuck-open
  valve. **The reset-schedule confound is handled, not just flagged:** when a DP-setpoint point is
  mapped the rule measures the concurrent setpoint shift and **judges on the residual drift not
  explained by it** — a DP move fully accounted for by a reset does not fault (it is reported as a
  caveat), and a move only partly explained is demoted to the residual's tier. Loop-parameterized;
  declines loudly when DP or the flow normalizer is unmapped; not auto-registered.
- **`camber.model.roles.Role.HW_DIFF_PRESS_SP`** — hot-water loop DP setpoint (parallels
  `CHW_DIFF_PRESS_SP`), wired for the reset confound on the hot-water side.

## [0.28.0] — 2026-08-18

**Hydronic loop delta-T drift** — the low-ΔT-syndrome detector.

### Added
- **`camber.rules.loop_deltat_rule.LoopDeltaTDrift`** — tracks a hydronic loop's temperature
  difference drifting from a frozen, flow-normalized `ΔT ~ f(flow)` baseline. **Two-sided**: a
  *collapse* at matched flow is the classic low-ΔT syndrome (overpumping, fouled/air-bound coils,
  stuck-open valves, a bypass short-circuit) that wastes pump energy and starves distribution; a
  *widening* is underflow / starvation. Flow is the normalizer **by design** — the loop's own thermal
  load is `flow × ΔT`, so normalizing ΔT on load would be circular; flow is the non-circular proxy
  (and pump speed is the affinity fallback where no flow point exists). Loop-parameterized by the
  warm/cool temperature pair (chilled-water warm=return/cool=supply, hot-water warm=supply/cool=return)
  and the normalizer; declines loudly when an input is unmapped; not auto-registered.

### Fixed
- **`camber.interop.bacnet._candidate_roles`** now returns its unit-/status-implied candidate
  vocabulary sorted by role slug, so BACnet name-suggestion is deterministic when two roles tie (e.g.
  a `gpm` tag against both `chw_flow` and `hw_flow` after the hot-water flow role was added) instead
  of depending on frozenset iteration order.

## [0.27.0] — 2026-08-18

**Pump head-at-speed drift** — the direct pump-condition read, and the flow-vs-head disambiguator.

### Added
- **`camber.rules.pump_head_rule.PumpHeadDrift`** — tracks a pump's differential head drifting **down**
  from a frozen `head ~ f(speed)` baseline (affinity H ∝ N²): a head deficit at matched speed is the
  less-ambiguous pump-wear read (worn impeller/wear-ring, cavitation, internal recirculation) and, with
  flow, disambiguates pump-wear from system-resistance (flow↓ **and** head↓ → the pump; flow↓ with head
  steady → the distribution). One-sided down; **instrumentation-gated** (declines loudly when no
  pump-head point is mapped). **The operating-point confound is surfaced:** head also falls riding down
  the pump curve at higher flow, so a head deficit that co-moves with a flow rise is reported and
  caveated. Loop-parameterized; not auto-registered.
- **`camber.model.roles.Role.PUMP_HEAD`** — per-pump differential head (psi), wired end-to-end
  (Haystack hint, 223P `Pressure`/`Water`, physical bounds, `psi`/`ft` unit tokens).

## [0.26.0] — 2026-08-18

**Pump flow-at-speed drift** — the first of the hydronic drift family, plus a one-sided-down CUSUM.

### Added
- **`camber.rules.pump_flow_rule.PumpFlowDrift`** — tracks a pump's flow-at-matched-speed drifting
  **down** from a frozen, load-normalized `flow ~ f(speed)` baseline (affinity Q ∝ N): the wear
  signal (worn impeller/wear-ring, clogged strainer, cavitation, entrained air) that shows before a
  pump is pegged, where the existing static pump heuristics can't. One-sided down (a surplus at
  matched speed is not a fault). **The system-resistance confound is surfaced:** a deficit that
  co-moves with rising loop DP points at a throttled/stuck-closed valve downstream, not pump wear —
  reported and caveated. Loop-parameterized (defaults to chilled-water roles; `flow_role`/`speed_role`
  point it at a hot-water loop); optionally masks to running samples via a pump-status point; declines
  loudly when flow or speed is unmapped. Not auto-registered; run via `Registry.run_periods`.
- **`camber.model.roles.Role.HW_FLOW` / `PUMP_STATUS`** — the first hydronic-flow and pump-status
  roles, wired end-to-end (Haystack hints, 223P quantity/status classification, physical bounds,
  `gpm` unit token, `STATUS_ROLES`).

### Changed
- **`camber.chillerdrift.ApproachDriftMonitor`** gains a `direction="down"` mode (alongside `up` /
  `both`) — the sustained-shift CUSUM can now alarm on a falling signal, which the pump flow-deficit
  detector needs. Additive; existing one-sided-up and two-sided callers are unchanged.

## [0.25.0] — 2026-08-18

**Chiller roll-up → CMMS ticket / notify path** — route the whole-machine verdict to where it acts.

### Added
- **`camber.integrate.diagnosis_to_ticket` / `diagnoses_to_tickets`** (+ `Notifier.emit_diagnoses`) —
  turn the chiller drift roll-ups (`camber.chillerdiag.diagnose_chiller_drift`) into neutral,
  JSON-serializable CMMS ticket dicts and route them through the existing pluggable transport
  (webhook / email / in-memory). A whole-machine verdict is one work order, not one per drifting
  signal, so it tickets under a stable `chiller_drift` fingerprint (recurring drift updates one
  ticket) with `machine_wide` / `locus` / `causes` carried for CMMS escalation rules. A
  `machine_wide_only` filter routes just the circuit-wide "gauge the whole machine" cases — the
  high-value subset to page on. Duck-typed; no new dependency.

## [0.24.0] — 2026-08-18

**Chiller diagnosis in the site report** — the whole-machine verdict lands in the owner-facing page.

### Changed
- **`camber.report.build_site_report`** now accepts an optional `diagnoses=` argument (the chiller
  drift roll-ups from `camber.chillerdiag.diagnose_chiller_drift`) and renders the
  `chiller_diagnosis_table` just under the health scorecard — so the per-machine locus / severity /
  machine-wide verdict rides along with the scorecard, findings and action plan in one self-contained
  HTML page. Backward compatible: omit `diagnoses` and the report is unchanged.

## [0.23.0] — 2026-08-18

**Surface the chiller roll-up in export + reporting** — the whole-machine verdict where it acts.

### Added
- **`camber.integrate.export.diagnoses_to_frame` / `export_diagnoses`** — flatten the chiller drift
  roll-ups (`camber.chillerdiag.ChillerDriftDiagnosis`) into a one-row-per-machine table (locus ·
  severity · machine_wide · per-side severities · charge cause · joined causes · caveat count ·
  stable fingerprint) and write CSV / JSON / Parquet, mirroring the findings export — the shape a BI
  tool or screening dashboard ranks and filters on.
- **`camber.report.chiller_diagnosis_table`** — a self-contained HTML `<table>` of the roll-up
  verdicts, ranked worst-severity first, flagging machine-wide cases; a standalone renderer to splice
  into a site or fleet report. Pure string building; no new dependency.

## [0.22.0] — 2026-08-18

**Physics-grounded synthetic validation** — characterize the whole drift stack without a dataset.

### Added
- **`camber.driftsim`** — a physically consistent synthetic chiller generator that produces
  `(baseline, current)` role-frame pairs for a healthy chiller and for the standard centrifugal-chiller
  fault families (condenser fouling · reduced condenser/evaporator water flow · tower degradation ·
  under/overcharge · non-condensables · excess oil), imposing each fault's known signature on the
  right channels at a graded severity. Refrigerant pressures come from a monotone illustrative
  saturation curve (`saturation_psig`) applied to the condensing/evaporating temperatures, so head and
  suction pressures move correctly with the faults. Helpers run the **whole drift suite + roll-up**
  end-to-end (`build_chiller_suite`, `diagnose_frames`) and score the roll-up's localization
  (`locus_confusion`) — plus `SimulatedCase.to_labeled(relevant=…)` to feed the existing
  `camber.driftvalidation` per-detector ROC / threshold sweep. On clear faults (severity ≥ 3) the
  roll-up localizes to the correct `locus` at ~100% with no false alarms on healthy periods, and
  degrades gracefully at marginal severity — turning the stack's *screening-grade* thresholds into
  *characterized* ones (real-data tuning still applies where labelled data exists). Deterministic;
  core deps only.

## [0.21.0] — 2026-08-18

**Whole-machine chiller drift roll-up** — one per-chiller verdict from both side diagnoses.

### Added
- **`camber.chillerdiag.diagnose_chiller_drift`** (+ `ChillerDriftDiagnosis`) — rolls the condenser
  (`condenserdrift`) and evaporator (`evaporatordrift`) side diagnoses into a single per-chiller
  verdict and adds the **cross-side reasoning** neither side can do alone: only the condenser side
  degrading localizes to the condenser loop, only the evaporator side to the evaporator, but **both
  sides drifting together** points at a *circuit-wide* cause (refrigerant charge, non-condensables, a
  compressor / metering fault) rather than one fouled heat exchanger. Liquid-line **subcooling** is
  folded in as the dedicated charge signal — a subcooling drift alongside both sides moving
  corroborates a charge / inventory problem. Reports a `locus` (steady · condenser · evaporator ·
  charge · whole-machine) and a `machine_wide` flag so a screening pass can separate "one exchanger
  needs a walkdown" from "gauge the whole machine". Re-uses the side diagnoses unchanged; stays
  screening-grade; pure over Findings.

## [0.20.0] — 2026-08-18

**Evaporator-loop drift diagnosis** — the low-side mirror of the condenser co-movement verdict.

### Added
- **`camber.evaporatordrift.diagnose_evaporator_drift`** (+ `EvaporatorDriftDiagnosis`) — synthesizes
  the three evaporator-side drift Findings (chiller **evaporator-approach** leg, **superheat**,
  **suction-pressure**) into one localized diagnosis: it names each drifting signal's cause
  (evaporator tube fouling/scale · overfeed-floodback vs. starvation from superheat · heat-transfer
  loss/low-charge vs. overfeed/flooding from suction pressure), isolates the evaporator leg from the
  condenser leg the approach rule also scores, and flags **corroboration** when two or more agree.
  Superheat and suction pressure are two reads on the same feed/charge axis, so it **cross-checks**
  them — both agreeing on overfeed (falling superheat + rising suction) or starvation (rising
  superheat + falling suction) is a strong, specific verdict, while a disagreement is called ambiguous
  rather than asserted (the evaporator-side twin of `condenserdrift`'s confound disambiguation). Stays
  screening-grade; pure over Findings.

## [0.19.0] — 2026-08-18

**Suction / evaporating-pressure drift** — the low-side companion to evaporator-approach drift, and
the pressure-domain twin of head-pressure drift on the evaporator side.

### Added
- **`camber.rules.chiller_suction_pressure_rule.ChillerSuctionPressureDrift`** — tracks a chiller's
  suction (evaporating) pressure drifting from a frozen, load-normalized baseline: a **fall** at
  matched load is the evaporator heat-transfer-loss / undercharge / starved-feed signature, a **rise**
  is overfeed / flooding. **Two-sided** (both directions are faults, scored on `|drift|` with the sign
  reported), gauged directly off the low side. Shares the head-pressure rule's threshold philosophy
  (same screening-grade psi + sigma floors — both are raw refrigerant pressures) and its confound
  honesty: the **chilled-water-reset confound** is surfaced — suction pressure tracks CHW supply
  temperature, so a co-moving CHW-supply shift is reported and caveated as possibly setpoint-driven
  rather than an evaporator fault. Same period-statistic + two-sided sustained-shift CUSUM readout as
  the subcooling detector. Instrumentation-gated (declines loudly when no suction-pressure point is
  mapped); not auto-registered (needs an injected `BaselineStore`); run via `Registry.run_periods`.

## [0.18.0] — 2026-08-18

**Head pressure joins the condenser-loop diagnosis** — the co-movement verdict now reads four signals.

### Changed
- **`camber.condenserdrift.diagnose_condenser_drift`** now folds in the `chiller_head_pressure_drift`
  Finding as a fourth condenser-side signal (cause: *condenser high-side pressure rising — fouling /
  non-condensables*), so a rising discharge pressure both contributes a localized cause and
  **corroborates** with the condenser-approach, CW-range and tower-approach signals — the high-value
  case where the gauge pressure confirms the approach-derived fouling. The head-pressure **confound is
  disambiguated by the tower signal**: a co-moving entering-CW-temperature rise *backed by* a degrading
  tower approach reads as corroborating a real heat-rejection fault, while the same rise with a quiet
  tower is flagged as likely ambient / high-load rather than a high-side fault. Stays screening-grade
  and pure over Findings; existing three-signal behaviour is unchanged when no head-pressure Finding is
  present.

## [0.17.0] — 2026-08-17

**Head- / condensing-pressure drift** — the high-side companion to condenser-approach drift, plus the
refrigerant-pressure roles it needs.

### Added
- **`camber.model.roles.Role.DISCHARGE_PRESSURE` / `SUCTION_PRESSURE`** — the first refrigerant-side
  *pressure* roles (psig). Raw pressures, not saturation temperatures (CAMBER models no refrigerant
  saturation curve). Wired end-to-end: Haystack hints, physical bounds, a `psi`/`psig` mapping-unit
  token, and an ASHRAE-223P `("Pressure", "Refrigerant")` quantity/medium.
- **`camber.rules.chiller_head_pressure_rule.ChillerHeadPressureDrift`** — tracks a chiller's head /
  condensing pressure drifting **up** from a frozen, load-normalized baseline: the fouling /
  non-condensables / reduced-CW-flow signal, read directly off the discharge-pressure gauge and often
  earlier than the computed approach. One-sided (a fall is not a high-side fault), with the same
  period-statistic + sustained-shift CUSUM readout and screening-grade threshold labelling as the
  approach and subcooling detectors. **The CW-temperature confound is surfaced, not hidden:** when a
  condenser-water supply point is mapped it reports the concurrent CW-supply shift and caveats a
  co-moving rise (some of the climb may be ambient / heat-rejection-driven); a mapped suction pressure
  adds the condensing-over-suction lift as context. Instrumentation-gated — declines loudly when no
  discharge-pressure point is mapped rather than reading as a healthy high side. Not auto-registered
  (needs an injected `BaselineStore`); run via `Registry.run_periods`.

## [0.16.0] — 2026-08-17

**Condenser-loop drift diagnosis** — one localized verdict from the condenser-side drift detectors.

### Added
- **`camber.condenserdrift.diagnose_condenser_drift`** (+ `CondenserDriftDiagnosis`) — synthesizes the
  three condenser-side drift Findings (chiller condenser-approach leg, condenser-water range,
  cooling-tower approach) into one diagnosis: it names the localized cause of each drifting signal
  (tube fouling / scale · reduced CW flow vs. bypass · tower heat-rejection), isolates the chiller
  condenser leg from the evaporator leg the approach rule also scores, and flags **corroboration** when
  two or more signals drift together — turning a set of screening-grade alerts into a prioritized,
  localized walkdown. Stays screening-grade (corroboration raises priority, not the severity tier);
  pure over Findings.

## [0.15.0] — 2026-08-17

**Cooling-tower approach drift** — the condenser-water loop's heat-rejection detector, over time.

### Added
- **`camber.rules.coolingtower_drift_rule.CoolingTowerApproachDrift`** — tracks a cooling tower's
  approach (`CW supply − wet-bulb`) drifting **up** from a frozen, load-normalized baseline: the
  fouled-fill / plugged-nozzle / reduced-airflow signal that shows weeks before a static
  approach-vs-design check trips. One-sided (fouling only widens an approach), with the same
  period-statistic + sustained-shift CUSUM readout and screening-grade threshold labelling as the
  chiller drift detectors. A period rule (run via `Registry.run_periods`); declines with a caveat when
  there's no condenser-water supply temperature or wet-bulb source.
- **`camber.coolingtower.tower_approach_f`** — the tower approach as a series (CW supply − wet-bulb,
  measured or Stull-derived from OAT + RH), the metric the drift rule fits its baseline on.

## [0.14.0] — 2026-08-14

**A read-only bacpypes3 client — BACnet works out of the box.** 0.13.0 gave BACnet discovery its brains
(discover → inventory → role mapping) but left the network wiring to the caller. This ships the hands: a
concrete, read-only bacpypes3-backed client implementing both the discovery and read seams, configurable
via API, YAML/JSON, or CLI. Live polling stays the fallback — historian-first remains recommended.

### Added
- **`camber.ingest.bacnet_client`** — `Bacpypes3Client`, a read-only **sync facade** over a bacpypes3
  async `Application` implementing both the `DiscoveryClient` protocol and the `BacnetSource` read
  client (managed background event loop; dashed→camelCase object-type normalization; Trend-Log records
  → `(timestamp, value)`). Builders `bacnet_read_client(target)` / `bacnet_discovery_client()` /
  `discover_default()` construct a real client from a `BacnetClientConfig`; the async app is **injected**
  so all shaping is unit-tested with no bacpypes3 and no network — the real `Application` factory is the
  only network-touching code. Read-only by construction (asserted by the ingest AST guard).
- **`BacnetClientConfig`** — deployment config (local device identity, interface/BBMD binding, timeout,
  Who-Is device range) settable three equivalent ways: the **API**, a **YAML/JSON file** (`from_file`),
  or the new **`camber bacnet-discover`** CLI.
- **`camber bacnet-discover`** CLI — discover a network read-only and bootstrap a role mapping,
  configured via `--config` (YAML/JSON) and/or flags.
- **Unicast / known-address discovery** — `camber.ingest.bacnet_discovery.discover_addresses(client,
  addresses)` (with `BacnetClientConfig.known_addresses` and `camber bacnet-discover --device`)
  enumerates specific device addresses by a **directed** (unicast) Who-Is, for **segmented / cloud
  networks** where broadcast Who-Is doesn't reach devices without a BBMD. `who_is` gains an optional
  directed `address`.

### Changed
- `camber.interop.bacnet.normalize_bacnet_unit` now also accepts bacpypes3's **dashed** unit strings
  (`degrees-fahrenheit`), alongside the camelCase name and the integer code.
- `BacnetSource`'s no-client error now points at `bacnet_read_client(target)`.

### Notes
- bacpypes3 is upstream **Pre-Alpha (0.0.x)** and **BACnet/SC is experimental**; this client is
  best-effort (one `Application` per process; BBMD for cross-subnet Who-Is) and the historian / SQL /
  Haystack path stays recommended for production.
- The live path is validated by `tests/integration/test_bacnet_live.py` — an in-memory bacpypes3
  `VirtualNetwork` simulated device driven through the real client (Who-Is, object-list enumeration,
  ReadPropertyMultiple, present-value, unit→role mapping) plus Trend-Log record shaping against real
  `LogRecord`/`EngineeringUnits` objects. Skip-guarded (needs bacpypes3 + `CAMBER_BACNET_LIVE=1`), so
  it stays out of the default suite but is deterministic and reproducible.

## [0.13.0] — 2026-08-14

**BACnet discovery + vendor proprietary-property bridge.** CAMBER can now *discover* a BACnet network
(not just read a point list it was handed) and bootstrap a point→Role mapping from what it finds, with
an optional bridge to [ace-bacnet-devices](https://github.com/ACE-IoT-Solutions/ace-bacnet-devices)
(MIT) for typed decoding of vendor proprietary properties. All read-only.

### Added
- **`camber.ingest.bacnet_discovery`** — read-only device/object discovery. `discover(client)`
  enumerates a network (Who-Is/I-Am → `object-list` → descriptive metadata) via an **injected**
  `DiscoveryClient` (core builds no bacpypes3 app, so it's testable without a network), returning
  `DiscoveredDevice`/`DiscoveredObject`. `discovery_to_points` (Trend-Log objects → `BacnetPoint` for
  the existing read adapter), `discovery_to_inventory` + `to_rows` (a flat per-object inventory). The
  service/property allowlists (`DISCOVERY_SERVICES`, `DISCOVERY_READ_PROPERTIES`) are asserted
  read-only by the same AST guard as the read adapter — no write/command path.
- **`camber.interop.bacnet`** — the discovery→role adapter mirroring `interop.haystack_semantic`.
  `roles_from_bacnet` / `mapping_from_bacnet` bridge each object's name + object type + engineering
  units to a `Role` (candidate roles bounded by object-type ∩ unit, then ranked by the existing
  `camber.mapping_assist` suggester); `review_bacnet` shapes the rest into `review_unmapped`
  suggestions. `normalize_bacnet_unit` and the `BACNET_UNIT_TO_TOKEN` / `OBJECT_TYPE_ROLE_HINT` tables.
- **`camber.interop.bacnet_vendor`** — optional `[bacnet-vendor]` bridge to ace-bacnet-devices:
  `install_vendor_decoders` (register typed decoders into a bacpypes3 stack — at client-construction
  time; gracefully no-ops when the extra is absent), `available_vendors`, and `vendor_hint_tokens` /
  `vendor_aliases` (surface the vendor catalog as mapping hints; aliases are strict to avoid
  mis-mapping). New optional extra `bacnet-vendor = ["ace-bacnet-devices>=0.2"]`.
- **docs/INGEST-PROTOCOLS.md** — a BACnet discovery recipe (build a `DiscoveryClient`, register vendor
  decoders at app-build time, `discover` → `mapping_from_bacnet` → `review_bacnet`).

## [0.12.0] — 2026-08-10

**Refrigerant-side feed diagnosis and threshold calibration.** A suction-superheat drift detector
completes the charge/feed pair with subcooling, and a new validation harness turns the drift
detectors' screening-grade thresholds into calibrated ones once labelled fault data exists.

### Added
- **`camber.rules.chiller_superheat_rule.ChillerSuperheatDrift`** — suction-superheat drift, the
  evaporator-side counterpart to subcooling. Two-sided (falling superheat = overfeed / liquid-floodback
  risk; rising = starvation / undercharge / restriction), load-normalized against a frozen baseline,
  with the same period-statistic + sustained-shift CUSUM readout as the other drift rules. A period
  rule, run via `Registry.run_periods`; instrumentation-gated (declines with a caveat when the point
  is absent) exactly like subcooling.
- **`Role.SUPERHEAT_TEMP`** — suction superheat, a controller-reported difference mapped directly
  (like `SUBCOOLING_TEMP`; CAMBER has no saturation-temperature/pressure role to derive it from).
  Mapped in the ASHRAE 223P interop as `("Temperature", "Refrigerant")`.
- **`camber.driftvalidation`** — a calibration harness for the drift detectors' thresholds (all of
  which are already constructor arguments). `evaluate(build_rule, cases)` scores a detector against
  labelled `(baseline, current)` period pairs and returns precision / recall / F1 on top of CAMBER's
  FDD confusion matrix (`camber.eval.confusion`); `sweep(build_rule, cases, grid, objective=...)`
  searches a grid of threshold settings for the operating point that maximizes an objective
  (`f1` / `recall` / `precision` / `accuracy` / `youden`), breaking ties toward fewer false positives.
  It does not lower the shipped screening-grade defaults — it is the tool for replacing them with
  calibrated values once real labelled fault data exists.
- **docs/CHILLER-DRIFT.md** — a page covering the whole chiller refrigerant-drift family (approach,
  subcooling, superheat, condenser-water range), the frozen-baseline + CUSUM design, the two
  threshold-confidence classes, and the calibration workflow.

### Notes
- Refrigerant *pressure* roles remain deliberately absent: CAMBER adds a role only once a shipped
  detector consumes it, so pressure-based diagnostics (e.g. head-pressure trend) are deferred until a
  detector needs them.

## [0.11.0] — 2026-08-10

**Chiller drift-detection** — catching a chiller that degrades *over time*, not just one that reads
badly right now: a load-normalized baseline, then streaming and period-based FDD scored against it,
plus modern-stack support (numpy 2.x, Python 3.12/3.13). The drift thresholds ship **screening-grade**
and every finding says so — they rank a walkdown, they do not dispatch a truck (see below).

### Added — chiller drift-detection
- **`camber.chillerbaseline`** — load-normalized baselines: fit `metric ~ f(tons)` and score how far a
  later period sits above it. `fit_approach_baseline` / `fit_load_baseline` / `fit_subcooling_baseline`
  (with `ApproachBaseline` / `LoadBaseline`), `drift_stats` / `load_drift_stats` (with `ApproachDrift` /
  `LoadDrift`), and `tons_from_flow`. A baseline can be frozen via `camber.store.modelstore`, so later
  runs score against a fixed reference rather than a moving average that drifts along with the fault.
- **`camber.chillerdrift`** — `ApproachDriftMonitor`, a streaming *sustained-shift* alarm (tabular CUSUM,
  reusing `camber.mandv.online.OnlineCusum`) that answers "has approach moved up **and stayed up**?"
  instead of firing on a single hot hour. Emits `DriftAlarmState` / `DriftAlarmRun`.
- **`camber.driftthresholds`** — `threshold_confidence()`, which rides on each finding and grades a
  threshold into one of two honest classes: **magnitude floors = screening-grade** (fit to rank, not to
  dispatch) and **temporal/CUSUM parameters = provisional-untuned** (not yet validated against labelled
  fault data). Screening-grade is a documented posture here, not a hidden assumption.
- **New FDD rules**, each usable in the batch registry: `ChillerApproachDrift` (period-over-period
  approach rise), `ChillerApproachSustainedDrift` (the streaming CUSUM alarm as a rule),
  `ChillerSubcoolingDrift` (two-sided liquid-line-subcooling drift — a refrigerant-charge signal), and
  `ChillerCwRangeDrift` (condenser-water range / ΔT drift — the condenser-side hydraulic signal). Each
  carries both an absolute (°F) and a normalized (σ) threshold.
- **Period-based FDD** — a `PeriodRule` protocol and `Registry.run_periods(...)` in `camber.rules.base`,
  so a rule can score a sequence of reporting periods (e.g. each week against the frozen baseline)
  rather than a single window.
- **`Role.SUBCOOLING_TEMP`** — a controller-reported liquid-line-subcooling difference. CAMBER has no
  refrigerant saturation-temperature or pressure role, so subcooling is mapped directly where a chiller
  publishes it, not derived.

### Changed
- **numpy 2 support** — the dependency pin widens from `numpy>=1.24,<2` to `numpy>=1.24,<3`; CI proves
  both ends (a numpy-1.x floor leg plus numpy-2.x on the default legs). No source change was required.
- **Python 3.12 and 3.13** are now tested and advertised — the CI and release matrices cover 3.10–3.13
  and the classifiers are updated. The supported floor stays 3.10.
- Internal tidy, no API or behavior change: curated `__all__` on `camber.mandv.online` and
  `camber.rules.online`; a stray mid-module import hoisted in `camber.geb`.

## [0.10.1] — 2026-08-06

Test-quality hardening: a **coverage gate** and **property-based tests**. Dev-facing only — no
public API or runtime change (`public_api_snapshot.json` unaffected).

### Added
- **Coverage gate.** `pytest-cov` + `hypothesis` in the `dev` extra; `[tool.coverage]` config
  (branch coverage, `source = ["camber"]`) + `[tool.pytest.ini_options]`. A dedicated `coverage`
  CI job enforces `--cov-fail-under=90` (current total ~93%). Coverage was lifted with focused
  tests on the reddest genuinely-testable modules (chart rendering branches, rule-wrapper severity
  tiers, flat-analyzer serializers).
- **Property-based tests** (`tests/test_properties.py`, `tests/test_properties_metamorphic.py`,
  ~25 laws) under a CI `hypothesis` profile, across three tiers:
  - *round-trip / idempotence* — `make_facility_id` path-safety (any string → a valid partition
    key), `normalize_percent` double-scale idempotence, `coerce`/`tsparse` totality, `timegrid`
    `regularize`.
  - *invariants* — `wilson_interval` unit-range, `confusion` count conservation, `degree_days`
    complementarity/monotonicity, `fit_stats` perfect-fit, `resample_energy` total-energy conservation.
  - *metamorphic* — whole-week time-shift + bounded flow-scale / common-temp-offset invariance of the
    reheat/overcooling/leakvalve analyzers, with negative controls pinning exactly where each
    relation stops (a day-shift, an absolute-threshold scale, or a one-sided offset).

## [0.10.0] — 2026-08-04

Portfolio-scale **facility identity**. The time-series store now keys each facility by a stable,
path-safe **`facility_id`** rather than a raw `site` name, decoupling storage identity from the
human display name — so a portfolio of many facilities scales without name collisions,
rename-orphaning, or a silent data-loss bug. **Breaking** to the store + read-API surface (allowed
pre-1.0), softened by deprecation aliases and a migration helper.

### Added
- **`camber.store.facilities`** — `make_facility_id(name)` (deterministic slug + short hash),
  `valid_facility_id` / `require_facility_id` (path-safe validation), `FacilityRegistry`
  (`_facilities.json`: `facility_id -> {name, ...metadata}`, with collision detection), and
  `migrate_site_to_facility(root)` to convert an existing `site=<name>` store in place.
- The store validates the id at write, so an unsafe name (space / `/` / unicode) is **rejected up
  front** instead of URL-encoding the partition directory and silently overwriting earlier part
  files — the previous `_next_seq` data-loss hazard, now impossible.
- Record a facility's display name + metadata on write (`write_role_frame(..., name=, **meta)`);
  read API gains `GET /facilities` returning `[{facility_id, name}]`.

### Changed (breaking)
- Store partition key `site` → **`facility_id`** (`<root>/facility_id=<id>/year=<Y>/`). The `site=`
  parameter on `write_long`/`read_long`/`read_role_frame`/`points`/`rollup`/`prune`, and
  `PointKey.site`, are renamed to `facility_id`. `ParquetStore.sites()` → **`facilities()`** (the
  `sites()` alias is kept and emits a `DeprecationWarning`). The read-API `site=` query param and
  `/sites` endpoint remain as deprecated aliases. Existing stores convert with
  `migrate_site_to_facility`. See **[docs/SCALE.md](docs/SCALE.md)**.

## [0.9.6] — 2026-08-02

Pre-1.0 hardening: **honest failures at the edges.** Analytics entry points and the untrusted
parsers now degrade to a clear error or a partial result instead of a raw traceback or a
plausible-looking wrong answer. No change on valid input.

### Fixed / Added — input validation
- **`forecast` / `disaggregate` / `tariff.compute_bill`** reject a non-`DatetimeIndex` series up
  front (they previously coerced a numeric index into nanosecond timestamps and returned a
  meaningless result). Empty input stays graceful. New private `camber._validate` helpers.
- **`fault_economics.EnergyPrice`** rejects a negative or NaN rate at construction (rather than
  "costing" a fault at a bogus rate); **`scorecard.build_scorecard`** rejects `None` and now
  accepts any iterable of findings.

### Fixed — adversarial fuzzing of the hand-rolled / untrusted parsers
- **`interop.brick`** — the minimal Turtle reader no longer `IndexError`s on a predicate list with
  no object, and `roles_from_brick`/`mapping_from_brick` normalize any backend parse failure
  (rdflib `BadSyntax`/`AssertionError`, or a minimal-reader error) into a clear `ValueError`.
- **`interop.haystack_semantic`** — `role_from_tags` tolerates a malformed tag collection (non-string
  markers) and `roles_from_haystack` skips un-parseable points instead of crashing on an unpack.
- **`tariff.compute_bill`** — a malformed rate structure (empty `energy_rates`, or a schedule naming
  a period with no matching rate) raises a clear error naming the period, not an `IndexError`.
- Regression tests: `tests/test_input_validation.py`, `tests/test_hardening_interop.py`,
  `tests/test_hardening_reports_econ.py` (report builders confirmed robust on empty/single/large).

## [0.9.5] — 2026-08-02

Pre-1.0 hardening: **CAMBER is now a typed library.** No API or behavior change (full suite
unchanged).

### Added
- **`py.typed` marker** (PEP 561), shipped in the wheel — downstream mypy/pyright now trust
  CAMBER's inline type hints.
- **`mypy` gate.** A pragmatic `[tool.mypy]` config (optional-extra libs import-ignored,
  untyped function bodies not deep-checked yet — a floor that ratchets in later releases) and
  a `types` CI job running `mypy` on every push/PR. `mypy` added to the `dev` extra.
- **Local, gitignored content denylist** for the pre-commit guard (`.githooks/denylist.local`,
  templated by `.githooks/denylist.local.example`): a per-clone file that can hold sensitive
  terms — e.g. a real client site name — to block them at commit time *without* committing the
  term itself. Reinforces the vendor-/site-neutral contribution rule.

### Changed
- Backfilled type annotations across ~25 modules to reach a clean `mypy` run — missing variable
  annotations, `None`-narrowing asserts that restate invariants the code already enforced, a
  `Site`→`Equip` parameter-annotation fix in `interop/site_model`, and targeted
  `# type: ignore[code]` (with reasons) only where mypy can't see a dynamic/dataclass/numpy
  type. No runtime behavior change.

## [0.9.4] — 2026-07-31

Correctness release (same high-outside-air design as 0.9.3): the `economizer_high_limit` rule
false-faulted a high-outside-air building, and there was no way to tell it the design minimum.

### Fixed
- **`economizer_high_limit` OA-damper unit bug.** `OA_DAMPER` is a percent role (the pipeline
  scales it to 0–100), but the rule compared it against a `0.25` *fraction* — so every open damper
  read "not locked out" (≈99.99% of hot hours in the field). The damper is now canonicalized to a fraction
  regardless of source scale (0–1 or 0–100).
- **Judge on outside-air fraction when available.** When mixed- and return-air temperatures are
  present, the rule now judges on temperature-balance OA-fraction (the `camber.oafraction` method)
  instead of damper position — damper % isn't linear in OA flow. It falls back to a damper threshold
  otherwise, recording a caveat that names the basis. Distinct from `outdoor_air_fraction` (excess OA
  in cooling generally) vs this rule's lockout-above-the-high-limit.
- Both the high limit and the minimum (`high_limit_f`, `min_damper`/`min_oa_pct`) are documented as
  tunable; the defaults encode a typical, not universal, building (CA Title 24 sets the changeover by
  climate zone).

### Added
- **Per-rule config parameters.** A `rules` entry in a config may now be `{"name", "params"}` to
  override a rule's constructor for the run, alongside the existing bare-string form (backward
  compatible). Benefits ~24 tunable rules. New `camber.rules.builtin.make_rule(name, **params)`
  constructs a built-in rule by name with clear errors on an unknown name or invalid parameter.

## [0.9.3] — 2026-07-31

Correctness release (field-found on a high-outside-air, 50%-OA VAV design): a rule must never
assert a negative it did not test. When an absent **optional** input made a sub-check
impossible, several rules silently collapsed the missing input into a confident wrong
verdict — a `False` metric, a raised severity, or a summary asserting something untested.

### Fixed — the "could not evaluate" honesty convention
- **`Finding.caveats: list[str]`** (new field) + a documented convention in
  `camber.rules.base`: an unevaluated sub-check is represented as `None` (tri-state), never a
  `nan`/`False`/`0` sentinel; it is excluded from severity, written as a null metric, kept out
  of the summary, and recorded as a caveat. The audit report surfaces finding caveats.
- **`Registry.run`/`run_fleet`** now record any absent optional roles on each finding
  (`metrics["_missing_optional"]`) so the whole class is visible without reading each rule.
- **Rules corrected** (absent optional role no longer flips the verdict): `chw_plant_reset`
  (OAT → false "no reset"/warn — the exemplar), `boiler_summer_lockout` (OAT → false clean),
  `supply_air_control` (no fan signal → false off-setpoint fault), `overcooling_min_flow`
  (no damper → over-count), `overcooling_severity` (no heating SP → over-flag),
  `zones_heat_cool_census` (missing flow/SP → under-count), `chw_pump_dp_reset` (false "flat
  DP setpoint"), `supply_air_reset` (false "load-tracking" verdict), `unmet_setpoint_hours`
  (one-sided setpoint → false "0%"). Each now declines the sub-check with a caveat instead.
- Hardened three `camber.aso` recommender comparisons against `None`-valued metrics.

**Behavior change (non-breaking, pre-1.0):** affected metrics may now be `null` instead of a
(wrong) `False`, and some findings that previously read `warn`/`ok` now read `ok`/`info` with a
caveat. These are corrected results; per `docs/API-STABILITY.md`, bug fixes that change a
genuinely wrong result are allowed. `Finding.caveats` and `_missing_optional` are additive.

## [0.9.2] — 2026-07-28

Second pre-1.0 hardening release: **the public API contract** — the biggest 1.0 prerequisite.
Settles what is public, writes down the SemVer + deprecation promise, and locks it in CI. No
behavior change to existing analytics (full suite green).

### Added
- **`docs/API-STABILITY.md`** — the public-API + deprecation policy: a name is public iff it
  (and its module) has no leading underscore; `__all__` is each module's curated surface;
  SemVer from 1.0; a deprecation window of at least one minor release and never removed before
  the next major. Wired into the docs nav.
- **`camber._deprecation`** (private) — `@deprecated(since=, remove_in=, use=)` decorator and
  `warn_deprecated()` helper emitting a consistent `DeprecationWarning`, attaching a
  machine-readable `__deprecated__` marker and a docstring note. Works on functions and classes.
- **`__all__` everywhere** — declared on the three previously-bare subpackages (`rules`,
  `model`, `charts`, with curated re-exports) and on all 70 flat top-level modules (each
  module's non-underscore surface). `camber.ingest` now surfaces its data-quality API
  (`assess`/`clean`/…) and `camber.report` its interactive-viz helpers, closing docstring-vs-
  export gaps.
- **`tests/test_public_api.py` + `tests/public_api_snapshot.json`** — a committed snapshot of
  the entire public surface (200 modules, 832 names); adding/removing any public name fails CI
  until the snapshot is regenerated. Also asserts every `__all__` name resolves, no private
  name leaks into an `__all__`, and every public function/class is documented (575, all pass).

### Changed
- Top-level `camber` is now an explicit namespace: it exposes only `__version__` (documented),
  not a re-export surface — import from subpackages/modules per the policy.
- `camber.scorecard.category_for` / `grade_for` reclassified as private (`_category_for` /
  `_grade_for`) — they were trivial internal lookups; users get their results via `Scorecard`.



First of the pre-1.0 hardening series. **A lint + format gate** — no behavior change, no
API change; the whole codebase is now machine-formatted and lint-clean, and CI enforces it.

### Added
- **`ruff` gate** (`[tool.ruff]` in `pyproject.toml`): line length 100, targeting Python
  3.10, rule set `E`/`F`/`I`/`W`/`UP`/`B` (pycodestyle, pyflakes, import sorting, pyupgrade,
  flake8-bugbear). A `lint` job in CI runs `ruff check .` and `ruff format --check .`.
- **`.pre-commit-config.yaml`** wiring the `ruff` + `ruff-format` hooks for local use,
  alongside the existing attribution guards in `.githooks/`.

### Changed
- Whole codebase auto-formatted with `ruff format` and lint-cleaned: removed unused imports,
  sorted imports, applied `pyupgrade` modernizations, and fixed a handful of bugbear findings
  (`assert False` → `raise AssertionError` in tests, unused loop/local variables). Two
  deferred `from .timegrid import interval_hours` imports moved to module top (no cycle).
  `zip(strict=...)` auditing (`B905`) is intentionally deferred to a later release.



Ninth release — **ingest robustness across vendor formats** + a **real-data M&V validation** on Building
Data Genome 2. Dependency-light throughout; the ingest refactor is fully backward compatible (existing
BAS/ISO exports parse identically — full suite green).

### Added — ingest robustness (`docs/INGEST-FORMATS.md`)
- **`camber.tsparse`** — one shared multi-format timestamp parser behind every adapter: an ordered
  format try-list (ISO 8601, US, European `dayfirst`, the BAS 12-hour format, LBNL `yyyymmdd`), epoch
  seconds/millis + Excel-serial detection, tz-abbrev strip, auto-detect by parse rate, naive-local by
  default. Replaces 5 scattered inline `pd.to_datetime` sites. **Fixes silent traps:** European
  `03/04/2025` read as US, a non-BAS per-point format yielding an empty series, and a trailing `AM`/`PM`
  meridiem being stripped as a timezone.
- **`camber.coerce`** — shared value coercion: a null/quality-token vocabulary (`N/A`, `---`, `Bad`,
  `Comm Fail`, …) + thousands-separator / European-decimal-comma handling, and an extended, overridable
  status vocabulary (On/Off, Open/Closed, Fault/Alarm/Normal, Override/Hand/Manual, Auto).
- **Vendor profiles** (`camber.ingest.profiles`) — an `IngestProfile` + presets
  (`niagara_n4`/`metasys`/`webctrl`/`tracer`/`desigo`) capturing each export tool's delimiter/encoding/
  skiprows/timestamp/decimal conventions; `load_csv(..., profile=…)` (and explicit `encoding`/`delimiter`/
  `skiprows`/`decimal`/`dayfirst` overrides).
- **`camber.ingest.csv_long.LongCsvAdapter`** — the `timestamp,point,value[,unit]` historian shape.
- A synthetic **per-vendor equivalence corpus** asserting every vendor format normalizes to the same frame.

### Added — BDG2 M&V validation (`docs/VALIDATION.md`)
- **`examples/bdg2/benchmark.py`** — the M&V analogue of the LBNL FDD benchmark: the ASHRAE G14
  baseline-model **acceptance rate** across ~2,044 real BDG2 whole-building meters, with Wilson CIs.
  Verified on real data: chilled water **36%** [32–40%] vs electricity **8%** [7–10%], pooled 15% — the
  engine reproduces the expected physics (weather-driven energy is ~4.5× more baseline-able) and reports
  both honestly. Committed a real (not CI-seeded) baseline; deterministic; new `mv-accuracy` CI job.

### Tests
- +43 (1285 → 1328): `test_tsparse`, `test_coerce`, `test_ingest_profiles`, `test_ingest_long`,
  `test_ingest_formats` (per-vendor equivalence), `test_bdg2_benchmark` (pure metrics, no download).

## [0.8.0] — 2026-07-27

Eighth release — one **feature** plus a **pre-1.0 stress-test / hardening pass**. Dependency-light
throughout (no new deps; the hardening uses seeded generators, not `hypothesis`).

### Added
- **Cross-panel interactive linking** (`camber.report`) — a brush in the dashboard scatter now
  propagates to every view. A shared `window.CAMBER` selection bus (a Set of selected timestamp
  strings) drives two panels promoted from static PNG to **inline SVG**: **B (fault multitrend)** shades
  the brushed time ranges and **E (load carpet)** highlights the matching hour×date cells. Panels A + I
  stay PNG; every panel keys off the same `str(timestamp)`, so they interoperate without a shared
  coordinate system. Single self-contained CSP-safe file, vanilla JS, no framework. New helpers
  `selection_bus_html`, `carpet_svg_html`, `multitrend_svg_html`.

### Hardened (bugs found + fixed by the stress pass)
- **`io.load_csv`** — empty / header-only / unparseable-timestamp CSVs now raise a clear `ValueError`;
  a single bad timestamp row is dropped instead of crashing the load; value columns are coerced to
  numeric so a stray text cell no longer silently poisons a column to `object` dtype.
- **FDD rules** — a 191-case sweep asserts every registered rule returns a `Finding` (never raises) on
  empty / 1-row / all-NaN / all-equal / duplicate-index frames; two plant rules (`condenser_water_reset`,
  `cooling_tower_approach`) hardened against a duplicate-index reindex crash.
- **M&V calibration** — `rc_model.calibrate` degrades to `accept=False` (not `ValueError`) on <4-point /
  all-NaN / gapped / constant energy, so the savings layer refuses to claim a number.
- **Fleet rollup** — the EUI-percentile loop is O(N log N) via `bisect` (was O(N²)); scale-tested to N=500.
- **Mapping** — `MappingProvider` rejects catastrophic-backtracking (ReDoS) regex patterns at config
  load (a `(a+)+`-style pattern could hang the mapper); legitimate patterns unaffected.
- **Determinism** — `validation.check_determinism` now nets `calibrate` / `best_model` /
  `detect_level_shifts` / cohort / `faultlab` (was 2 spots).

### Tests
- +230 (1055 → 1285): `test_hardening_*` (io, rules sweep, mandv, scale+determinism, timegrid+mapping),
  cross-panel linking additions, and a shared `tests/conftest.py` of degenerate-frame factories.

## [0.7.0] — 2026-07-27

Seventh release — **IPMVP Option D (calibrated simulation)**, the last remaining IPMVP boundary.
Dependency-light (numpy only), read-only toward the BAS, clean-room (ISO 13790 simple-hourly / ASHRAE
inverse-modeling lineage), synthetic-fixture tested. **CAMBER now covers IPMVP Options A/B/C/D.**

### Added
- **`camber.mandv.rc_model`** — a forward, schedule-driven **1R1C grey-box** building model.
  `RCModel(ua_eff, gain_eff, tau).predict(oat, schedule)` returns hourly HVAC energy and can be run under
  a counterfactual (as-corrected) control — the capability the inverse models (A/B/C) lack.
  `daily_schedule(...)` builds an occupied/setback control schedule.
- **`calibrate(oat, schedule, metered_energy)`** — mirrors the change-point fitter: grid the one
  nonlinear parameter `tau` (coarse→fine), OLS the linear conductance/gain, keep the best CV(RMSE);
  gated by the existing ASHRAE G14 acceptance (`stats.fit_stats` + `cv_rmse_max_for("hourly")`).
  Deterministic (`validation.check_determinism`). Returns a `Calibration` (model + fit + accept).
- **`option_d_savings(calibration, oat, as_found, as_corrected)`** — differences the calibrated model's
  as-found vs as-corrected annual profiles into modeled avoided energy with a **G14 Annex-B fractional
  savings uncertainty** band. **Refuses to claim a saving when the calibration fails the G14 gate**
  (`valid=False`, `avoided_energy=None`) — the same refuse-to-fabricate posture as `fault_economics`
  (`costed`) and `ecm_savings` (upper bound).
- **`mandv.ecm_savings.modeled_savings(...)`** — bridges the metered-waste **upper bound** to the
  pre-implementation **modeled** Option-D saving, closing the caveat that module's docstring flagged.

### Docs
- `docs/OPTION-D.md`; IPMVP A/B/C/D noted complete in `MANDV.md` + `CAPABILITIES.md`; ROADMAP marks
  Option D delivered and reshapes Next-0.8 (chiller benchmark, Option-D depth, packaging).

### Tests
- +11 (1044 → 1055): `test_rc_model` — recovers a known model within tolerance, calibration is
  deterministic, unstructured noise fails the G14 gate, savings match the direct profile difference, and
  a failed calibration claims no saving.

## [0.6.0] — 2026-07-27

Sixth release — **validation & interop completeness**: finish the stories 0.5 opened rather than open a
new headline (IPMVP Option D / calibrated simulation is deferred to 0.7). Read-only toward the BAS,
dependency-light, clean-room/citable; synthetic-fixture tests + docs per capability.

### Added
- **FDD hardening → 33/33.** The synthetic fault-injection harness (`camber.faultlab`) now scores
  **every single-equipment rule** at 100% TPR / 0% FPR — scenarios added for the last 9 fixture-only
  rules (boiler summer-lockout, HW-plant ΔT, condenser-water reset, CHW/HW pump DP reset, leaking valve,
  night/weekend setback, OA-fraction, G36 reheat minimization). The fixture-only list is now empty; the
  committed baseline is regenerated and CI-gated.
- **Haystack tag→role import** (`camber.interop.haystack_semantic`): `role_from_tags`,
  `roles_from_haystack`, `mapping_from_haystack` — inverting `HAYSTACK_HINT` (subset match, most-specific
  tie-break) to close the export→import round-trip to Brick parity. All 54 roles round-trip.
- **ASHRAE 223P coverage 21 → 44 roles** (`interop.semantic223.ROLE_TO_223`): the full plant/hydronic
  side (CHW/HW/CW temps, loop pressures, pump/tower speeds), power + thermal energy, ambient/humidity,
  and the refrigerant-side approach temps. The 10 remaining binary status/command roles carry no QUDT
  quantity-kind and are listed in `_NO_223_QUANTITY` (intentionally unmapped); a test asserts the mapped
  and unmapped sets partition every role.
- **Broadened real-data LBNL benchmark (Tier 1):** a cooling-coil-valve leakage **severity sweep**
  (010–100%) characterizes the leak detector that the pooled result showed under-firing; the fetcher is
  hardened to skip zip members absent from a given release and to gate its no-op on a proven-present core.

### Deferred
- **IPMVP Option D — calibrated simulation** → 0.7 (feasible as a dependency-light grey-box RC model).
- **Second labeled chiller dataset (Tier 2)** — real-data validation of the refrigerant-side rules,
  pending a license-clean, fault-labeled validation dataset.

### Tests
- +13 net (1031 → 1044): `test_faultlab` (33/33, empty fixture-only), `test_haystack_semantic`,
  `test_semantic223` (plant/DX round-trip + partition), `test_lbnl_fetch` (robust fetch, synthetic zip).

## [0.5.0] — 2026-07-26

Fifth feature release. **Validation-led**: prove the existing FDD suite, then broaden equipment
coverage, and make the 0.4 grounded agent reachable from the terminal. Everything stays read-only
toward the BAS, dependency-light, clean-room/citable, with synthetic-fixture tests + a `docs/` page per
capability. IPMVP Option D (calibrated simulation) is deferred to 0.6.

### Added

**FDD accuracy — prove the whole suite** (`camber.faultlab`, `examples/synthetic_fdd`, `docs/VALIDATION.md`)
- A deterministic synthetic fault-injection harness that scores the registry: each rule's target fault
  is injected (a labeled positive) alongside a fault-free frame (a negative), scored with the existing
  `camber.eval` LBNL framework. **24 of 33 single-equipment rules** are now accuracy-scored at 100% TPR
  / 0% FPR (up from 2 in the LBNL benchmark); the remaining 9 are honestly reported as fixture-only.
- A **G36 §5.16.14 FC1–FC15** engine harness (6 representative fault conditions, all detected, clean
  quiet). Runner (`--json/--gate/--update-baseline`) + committed baseline, gated in normal CI
  (`tests/test_faultlab.py`) and the benchmark workflow (no download). Honest scored-vs-fixture
  coverage table.

**Packaged / DX & refrigerant-side FDD** (`docs/FDD-DX.md`)
- 10 new roles: `compressor_status`/`compressor_stage`, `condenser_fan_status`, `heat_stage`,
  `reversing_valve_cmd`, `filter_diff_press`, `supply/return_air_humidity`,
  `cond/evap_approach_temp` — each with `PHYSICAL_BOUNDS` + a Haystack hint.
- 4 equipment templates: **RTU**, **HeatPump** (VRF), **DOAS** (ERV via optional roles), and **FCU**
  (now distinct from the VAV alias).
- 5 rules: `compressor_short_cycle`, `compressor_staging`, `heatpump_defrost`, `filter_fouling`, and
  `chiller_approach_fouling` (condenser/evaporator approach-temperature degradation — the
  refrigerant-side indicator that needs no refrigerant-pressure instrumentation).

**Agent CLI** (`camber.cli`, `docs/CLI.md`)
- The `camber` console script becomes a subcommand CLI: `run`, `report`, `explain`, `ask`, `fleet`,
  and `charts`. `explain`/`ask` are grounded and useful with no LLM; `--llm-cmd` wires any model via a
  **vendor-neutral** shell seam (prompt on stdin → completion on stdout) whose subprocess wrapper lives
  in the CLI so `camber.agent` stays pure.

**Portfolio triage** (`camber.agent`)
- `facts_from_fleet(FleetReport)` (a `fleet` fact kind) and multi-site `Context`
  (`build_context(fleet=…, runs=…)`) enable grounded portfolio-wide Q&A ("which building is worst?").

### Changed
- **BREAKING (CLI):** the legacy top-level `--csv`/`--demo` AHU heating-vs-cooling charts now live under
  `camber charts` (e.g. `python -m camber.cli charts --demo reheat`).

### Tests
- +41 tests (990 → 1031): `test_faultlab`, `test_new_roles`, `test_dx_rules`, `test_cli`, plus template
  completeness and portfolio-context additions.

## [0.4.0] — 2026-07-10

Fourth feature release. Adds the two deferred **AI-assist** tracks — **assisted point mapping** and
a **grounded explanation & Q&A agent** — built dependency-light, **advisory-only** (never the source
of truth, always auditable), and **read-only toward the BAS**. The LLM path is fully
**provider-agnostic**: no vendor is named, no SDK or network client is imported, and an AST guard
proves it; everything works with **no LLM wired** via deterministic fallbacks. Each capability ships
option flags, a `docs/` page, and synthetic-fixture tests.

### Added

**Assisted point mapping** (`camber.mapping_assist`, `docs/MAPPING-ASSIST.md`)
- `suggest_roles(token, …)` / `review_unmapped(tokens, mapping, …)` — suggest roles for **unmapped**
  BAS tags as a human-confirmed review list; **never mutates a `MappingProvider`** (advisory boundary).
- `FeatureSuggester` — dependency-light baseline (numpy/stdlib): tag initials + edit distance vs the
  `Role` vocabulary, a unit-compatibility table, and physical-range fit (reusing
  `sensorhealth.range_violation_frac`) so a role the data physically contradicts is demoted.
- `MLSuggester` — optional learned backend behind the new **`[ml]` extra** (scikit-learn, lazy
  `_require()`); a char-n-gram classifier trained on the caller's / synthetic labels (`fit`,
  `from_mapping`) — **no pretrained weights** (clean-room). Predictions pass the same range gate.
- `LLMSuggester` — reuses the agent seam (no new dependency); the model proposes roles, each is
  validated `Role(value)` and **re-scored** via `mapping_confidence.score_token` so a
  physically-inconsistent suggestion can't outrank a good one.

**Grounded explanation & Q&A** (`camber.agent`, `docs/AGENT.md`)
- `agent.explain(findings, …)` and `agent.ask(question, …)` — cited, plain-language explanations and
  NL Q&A over the deterministic layers; return a `Grounded(text, cited, facts, grounded, flagged,
  source)`.
- `agent.context` — a **grounding whitelist**: `Fact(id, kind, equip, text, data)` + `Context` with
  order-stable, deterministic ids (`F1`/`C1`/`R1`/…). Builders `facts_from_findings`, `facts_from_run`,
  `facts_from_scorecard`, `facts_from_completeness` (why a rule couldn't run), `facts_from_history`
  (**bounded stats only**, never raw series), and `facts_from_mapping`. Cost facts never fabricate a
  dollar figure when uncosted — they state the basis.
- `agent.verify` — grounding by **number-traceability**: an answer is grounded iff every `[id]`
  resolves and every number it states appears in a cited fact; `strict` mode repairs (drops
  untraceable sentences, strips unknown cites), non-strict marks only.
- `agent.templates` — deterministic (no-LLM) `explain_from_facts` / `answer_from_facts`; trivially
  100% grounded and the oracle the LLM path is verified against.
- `agent.client` — the **provider-agnostic seam**: `AgentClient` wraps an injected
  `complete(prompt, **opts) -> str` callable (`client_from_callable`, network-free `stub_client`).
  Unwired is a valid state (falls back to templates); `generate()` raises a helpful error only when
  actually called.

**Packaging**
- `[ml]` optional extra (`scikit-learn>=1.3`); conda recipe filled to 0.4.0 with a `run_constrained`
  for it; a hardened MkDocs → GitHub Pages workflow (`.github/workflows/pages.yml`) + an **AI-assist**
  docs nav group. `docs/DEPLOY.md` documents the conda-forge / Pages / community owner-actions.

### Guarantees
- `tests/test_agent_readonly_guard.py` — an AST guard over `camber/agent/*.py` + `camber/mapping_assist.py`
  fails on any write/command/actuation symbol **and** on any LLM-provider or network import, mechanically
  enforcing the read-only and no-vendor/no-network contracts.

### Tests
- +77 tests (913 → 990): `test_mapping_assist`, `test_agent_context`, `test_agent_verify`,
  `test_agent_client_seam`, `test_agent_explain_ask`, `test_agent_readonly_guard`.

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
- **Pattern J (keystone)** — `charts.evidence`: **every rule renders its own evidence**. A duck-typed
  `evidence(equip, frame)` hook returns an `Evidence` that `render_evidence` dispatches to a
  B/D/E/G renderer; wired into the HTML dashboard and the Std-211 audit report. Rules without a
  tailored hook fall back to a default multi-trend of the roles they examined, so the whole 33-rule
  library (and future rules) carries evidence with no per-rule map.
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
- `economizer_high_limit` (OA damper not locked out above the high limit), `free_cooling_missed`
  (mechanical cooling while free cooling was available), `static_pressure_reset` (duct-static
  setpoint that doesn't trim with demand).

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
- `camber.anomaly` — anomaly ensemble: fuse point (MAD), change-point, and data-quality signals
  into one severity verdict. `docs/ANOMALY.md`.

**Reporting**
- `report.build_site_report` — a one-shot self-contained HTML deliverable: health scorecard +
  chart sections + ranked action plan + per-finding evidence. `docs/SITE-REPORT.md`.

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
