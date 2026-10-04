# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.101.0] — Unreleased

<!-- 0101-g36-seasonal -->
### Added
- **`g36_afdd` takes a seasonal OA damper minimum (#105).** A new `oa_damper_min_by_month`
  parameter (`{month: position %}`, mirroring `min_oa_pct_by_month`) overrides `oa_damper_min`
  in the months it names, so the operating states judge each interval's OA damper against its own
  month's minimum position: heating (OS#1) at it, free cooling (OS#2) beyond it. It needs
  `oa_damper_min`, the position in the other months. `run_g36_afdd` gains the same keyword and
  `G36Result` a trailing `oa_damper_min_by_month` field; the finding reports the metric only when
  it is set, and a caveat names the seasonal minimum. The default is `None`, so default outputs
  are byte-identical. Param docs and `docs/THRESHOLDS.md` cover it.
<!-- /0101-g36-seasonal -->

## [0.100.0] — Unreleased

<!-- Each 0.100 branch adds its bullets only inside its own marked block. -->

**0.100: the dual-duct unit's seasonal minimum, equipment causes heading RCx issues, and an
opt-in leak drift detector (#97–#104).** `g36_afdd` FC6 takes a seasonal minimum OA (#97). The
`lbnl-ddahu` mapping adds its cold-deck SAT setpoint, so G36 FC9, FC11 and FC13 are evaluated
there (#98); the dataset re-ingests once, and the open-fdd cross-check was re-run on the merged
code. `reheat_penalty` takes a `box_type` that caps its fan-heat estimate by box (#99). A new
opt-in `coil_leak_drift` judges coil-valve leaks against the unit's own baseline (#100). Its
results are measured records, not gated. An RCx issue is now headed by a member finding's cause
when that cause names the equipment at fault (#101). The `lbnl-ddahu` and `lbnl-sdahu`
templates now run `free_cooling_missed`, which re-heads two catalog issues. The point-role
suggester reads more public naming conventions and keeps weather-station points outdoors
(#102). This intentionally changes the default suggester output. Setpoint-level grouping for the
sensor-health outlier read was measured and rejected (#103). Config runs resolve a facility's
time zone the way the read API does (#104). The synthetic, fleet, LBNL, BDG2 and BDG2 savings
benchmark gates did not move.

<!-- 0100-integration -->
### Changed
- **Dataset templates: `lbnl-ddahu` and `lbnl-sdahu` run `free_cooling_missed` (#101).** Both
  templates now run it at the documented 60 °F high limit, reading each unit's own OA damper
  command and mixed-air temperature. Hourly, `lbnl-ddahu` `DMPRStuck_OA_0` reads *warn* (14.5 %
  of free-cooling hours, cause: damper not delivering), and its fault-free and `DMPRStuck_OA_100`
  runs read *ok*. On `lbnl-sdahu`, `damper_stuck_010`, `damper_stuck_025` and
  `onset_damper_stuck_025` read *fault* (49 %, 49 % and 30 %) and `damper_stuck_075` *warn* (13 %),
  all with cause damper not delivering. The fault-free and `coi_leakage_010` runs read *warn*
  (17.5 % and 18.3 %, economizer not commanded): 93 % of those hours fall below the unit's 33.8 °F
  low-limit lockout, which holds the damper at its minimum by design, and the rule has no low
  limit. The template comment records this. The lbnl-sdahu RCx report has 13 issues, up from 8.
  `free_cooling_missed` is not a scored target, so `camber datasets score` and the gated
  benchmarks do not move. The other four air-handler templates do not run it, and each comment
  says why. `nuig-ahu101` is 100 % outdoor air with no mixing box. On `lbnl-b59`, `ornl-frp-ops`
  and `ornl-frp-vav` the rooftop units trend no cooling-valve command, so the rule would decline.
<!-- /0100-integration -->

<!-- 0100-sensor -->
### Documentation
- **Sensor health: the setpoint level as a grouping key is measured and rejected (#103).** The
  alternative to the residual read deferred from #87 judges a controlled point's outliers within
  each level of its setpoint, when the setpoint takes at most 4 distinct values. It was measured
  for supply air and duct static on every catalog RCx report (511 trust-table rows). As proposed,
  it moves no row: no catalog setpoint has two to four levels. The LBNL single-duct runs hold one
  constant level, and `lbnl-b59` and `nuig-ahu101` reset theirs. Grouping by the four most common
  levels does reach those two units, and it lowers every supply air it touches by 0.01-0.12.
  `lbnl-b59` RTU03 and RTU04 go from *trusted* to *suspect*. Not adopted; there is no code
  change. docs/SENSOR-HEALTH.md records the measurement, and a new test pins that `mode="auto"`
  does not group by setpoint.
<!-- /0100-sensor -->

<!-- 0100-rcx -->
### Changed
- **An RCx issue is headed by a member's cause when it names the equipment at fault (#101).**
  An issue's heading came from its chain's root finding, which can hide a more specific cause on
  a member. A cause is equipment-level when it names a component that does not do what it is told:
  a damper that does not deliver the outside air it is commanded to (`free_cooling_missed`,
  `damper_not_delivering`), a reheat valve whose position does not follow its demand, a leaking
  valve, a stuck actuator, or a drifting OA damper. The precedence: a root with an equipment-level
  cause keeps the heading; otherwise the most upstream member with one heads the issue; otherwise
  the root's cause, as before. The action, its title and its links stay the root's, and the page
  says in one line which member named the cause. The executive summary's Issue column and the
  `cause` key of `RcxReport.to_dict()` issues follow the heading; each issue gains `cause_rule`,
  the rule of the finding that names it. See docs/RCX-REPORT.md, "Which cause heads an issue".
  On the catalog run templates (15 datasets, default subsets, 98 issues) two headings change,
  both through `free_cooling_missed`, which the `lbnl-ddahu` and `lbnl-sdahu` templates now run
  (see *Dataset templates* below): `lbnl-ddahu` `DMPRStuck_OA_0` reads "Outdoor-air damper not
  modulating (stuck low)" (was "Outside air below the ventilation minimum"), and `lbnl-sdahu`
  `damper_stuck_075` reads "Outdoor-air damper not modulating (stuck part open)" (was "Economizer
  open above the high limit"). No other heading changes. The RCx golden file is unchanged: its
  synthetic site has no member with an equipment-level cause.

### Fixed
- **Config runs resolve a store facility's time zone the same way as the read API (#104).**
  Since 0.99.1 `/facilities` reads a facility's zone from its registry entry's `timezone` first.
  Config runs on a store source read only the catalog entry's zone or the open-fdd provenance, so
  an explicit `timezone` on the registry entry was ignored by runs. Both now use one lookup: the
  entry's `timezone`, the dataset block on the entry, the catalog entry, then the open-fdd
  provenance. Behaviour change: a per-site zone in a facility's dataset block now also sets a
  run's zone, as `/facilities` already reported it. Each BDG2 site records one. On the ten BDG2
  sites' template runs (65 daily meter baselines) the fall-back and spring-forward days now have
  their 25 and 23 hours; no verdict changes, CV(RMSE) moves by at most 0.0003 and R² by at most
  0.0012.
<!-- /0100-rcx -->

<!-- 0100-terminal -->
### Added
- **`reheat_penalty(box_type=None)`: the `"auto"` fan-heat cap by box type (#99,
  provisional).** The `"auto"` estimate of a fan-powered box's closed-valve lift was clipped to
  0-8 °F whatever the box. On the LBNL series boxes (`lbnl-fpu` SFPU runs) the real lift is
  13.7 °F, so the clip always bound. `box_type` declares the box: `"single_duct"` (no fan) caps
  the estimate at 3 °F, and `"parallel"` caps it at 8 °F, as before. `"series"` caps the no-rise
  allowance (valve open) at 10 °F and keeps the big-rise allowance (valve shut) at 8 °F. The big-rise
  check judges the closed-valve samples that the estimate is learned from, so with a higher cap a
  passing valve's heat is learned as fan heat and the check cancels itself out. With `box_type`
  set, the finding also reports `box_type`, `fan_lift_f` (the uncapped estimate) and
  `fan_heat_closed_f`. The default (None) and a numeric `fan_heat_f` are unchanged, and default
  outputs are byte-identical. Measured on all 31 PFPU and 31 SFPU runs (hourly, read through the
  measured position and through the demand alone): `"parallel"` matches the default on every PFPU
  run. `"series"` leaves every SFPU verdict unchanged and widens the declines' margin. The valve
  stuck at 20 % (12.9 °F rise) was declined 0.06 °F under the old 13 °F bound and is now 2.1 °F
  under the 15 °F bound, while the lowest working full-valve rise (17.6 °F) stays 2.6 °F above
  it. A symmetric series cap of 9-11 °F would have dropped the passing-valve caveat on the 50 %
  and 80 % leaks and on the valves stuck at 80 % and 100 % (read through the demand). The caps
  were set on these labelled runs, so they are an in-sample fit. The `lbnl-fpu` exercise configs
  are unchanged, because their boxes are parallel. `docs/THRESHOLDS.md` documents `box_type`.
<!-- /0100-terminal -->

<!-- 0100-ddahu -->
### Added
- **`g36_afdd` FC6 takes a seasonal minimum OA (#97).** A new `min_oa_pct_by_month` parameter
  (`{month: pct}`, the override `outdoor_air_fraction` already takes) makes FC6 judge each sample
  against its own month's minimum. It needs `min_oa_pct` for the other months, and the FC6
  caveat names the seasonal minimum. The default is `None`, so default outputs are unchanged.
  Param docs and `docs/THRESHOLDS.md` cover it.
- **The `lbnl-ddahu` run template runs `g36_afdd` (#97).** FC6 is judged against the unit's
  documented seasonal minimum: 31.8 %, and 11.9 % in June to August (the values
  `outdoor_air_fraction` uses), with a `basis` map. Hourly, FC6 reads 0.13 % on the fault-free
  run (`ok`), 1.96 % on `DMPRStuck_OA_0` and 0.13 % on `DMPRStuck_OA_100`. No verdict depends on
  FC6. FC6 is not a stuck-closed detector on this unit. Since #95 its applicable hours are almost
  all in June to August, the learned OA damper minimum being the 28 % summer position, and
  against an 11.9 % minimum a damper stuck shut is off by less than G36's 30-point tolerance. A
  fixed 31.8 % would flag `DMPRStuck_OA_0` (32.1 %), but only by judging summer hours against
  the winter minimum. The #94 figures for a single minimum (fault-free FC6 20.5 % at 31.8, 47.1 %
  at 11.9) predate #95. On the current code both single minima read the fault-free run `ok`.

### Changed
- **`lbnl-ddahu` maps its cold-deck supply-air setpoint (#98).** `CSA_TEMPSPT` (the
  publisher's Brick model types it as a supply-air temperature setpoint of the cold deck; 55.0 °F
  in every row) now maps to `supply_air_temp_sp`, next to the cold-deck `CSA_TEMP`. Both roles are
  the cold deck. The hot deck's own 90 °F setpoint is not mapped, and the mapping, template and
  cross-check caveats say so. **The dataset content hash changes, so stores re-ingest
  `lbnl-ddahu` once.**
  - `g36_afdd` now evaluates FC9, FC11 and FC13 on the unit. `DMPRStuck_OA_0` adds FC9 (20.7 %)
    and FC11 (12.2 %) to the FC8, FC10 and FC12 it already flagged. FC13 reads 0 % on every run.
    The fault-free run's FC9 hits fall on its 4 free-cooling hours, under the 24-hour floor, so
    it stays `ok`. No run's severity changes.
  - **The open-fdd cross-check was re-run** (`examples/openfdd_crosscheck/results/`). Only
    `lbnl-ddahu` verdicts change. Every engine's any-FC result per run is unchanged. The open-fdd
    engines now evaluate FC7, FC9, FC11 and FC13 there: FC11 fires on `DMPRStuck_OA_0` in all four
    open-fdd runs, and FC13 in the SQL engine and the pandas engine at its own defaults. In month
    windows, the pandas engine at G36 tolerances goes from TPR 0.58 with FPR 5/12 to TPR 0.62 with
    FPR 6/12, because FC9 fires on one fault-free month. `docs/ECOSYSTEM.md` has the before/after.
  - **Workbook `air-sat-reset`.** `supply_air_reset` now reads the flat cold-deck setpoint on
    `DDAHU__fault_free`: "not reset (setpoint flat at ~55F)", where it read "no reset (SAT pinned
    low at ~55 F regardless of OAT)" from the supply air before. The severity (`warn`), the
    direction (`flat`) and the 66 % below the G36 target are unchanged. The instructor key, the
    page, the exercise config comment and the stand-in follow, and a new check pins
    `sp_range_f` 0.0.
- **Unchanged.** No gated benchmark key moves: the synthetic, fleet, LBNL, BDG2 and BDG2
  savings benchmarks all hold. The LBNL benchmark's DDAHU family scores `outdoor_air_fraction`
  only, which does not read the setpoint.
<!-- /0100-ddahu -->

<!-- 0100-leak-drift -->
### Added
- **A coil-valve leak drift detector against the unit's own baseline (#100; opt-in,
  provisional).** This is the follow-up planned in #84. `leaking_valve` catches the published
  10 % leak on `lbnl-sdahu` only with a fan heat calibrated on the fault-free run, and that run is
  also scored. The new `coil_leak_drift` (`camber.rules.coil_leak_rule.CoilLeakDrift`) calibrates
  nothing. It fits the coil's valve-shut air rise (the coil's leaving air, or the supply air,
  minus the mixed air, on fan-on hours with every mapped coil valve shut) against the mixed air on
  a known-good window. It then flags a shift in the current window at the same mixed-air
  temperature: down for a cooling leak, up for a heating leak.
  - **How it runs.** It uses the existing drift machinery, so the baseline can be a frozen one or
    a declared reference from 0.98 (another unit, or a known-good period of the same unit).
  - **What it judges.** Only current hours inside the baseline's mixed-air range are judged. The
    rest are counted and caveated, never extrapolated.
  - **What it reports.** A finding reports the shift in °F and in baseline sigmas, the basis
    sensor, the gates applied and a sustained-shift alarm. A warn or fault also says that a
    shifted supply-air or mixed-air sensor, or a valve stuck partly open, reads the same.
  - **How to switch it on.** An `ahu` drift family entry opts in with `"coil_leak": ["cooling"]`
    (or `["cooling", "heating"]`) and may tune it with `"coil_leak_params": {...}`.
    `build_drift_suite` and `refit_baselines` take the matching `coil_leak=` and
    `coil_leak_params=` keywords.
  - **Roll-up.** `diagnose_ahu_drift` puts a leak on the coil side as
    `coil_leak_drift:<coil>`. The scorecard counts it under maintenance.
  - **Defaults are unchanged.** Without the key no suite, config, template or benchmark changes.
  - **Documentation.** Its parameters are documented in the new
    `camber.rules.param_docs.DRIFT_PARAM_DOCS` and rendered in `docs/THRESHOLDS.md` under "Opt-in
    drift detectors". Also updated: CLI.md ("Leak drift (opt-in)"), TUNING.md, AHU-DRIFT.md and
    API-STABILITY.md.
- **Validation on the LBNL leak runs (measured, not gated).** `examples/lbnl_fdd/leak_drift.py`
  scores the detector against each unit's fault-free run three ways: a 60/40 split, a declared
  twin, and a mid-year onset splice. It reports TPR/FPR with 95 % Wilson intervals.
  - **`lbnl-sdahu`.** The 10 % leak reads −1.05 to −1.11 °F (−3.5 to −3.7σ). TPR is 1/1
    (0.21-1.00) in every mode and FPR 0/2-0/3, with no calibration.
  - **`lbnl-fcu`.** Cooling and heating leaks at 20 / 50 / 80 %: TPR 6/6 (5/5 onset), each on
    the right coil. FPR is 1/33 split (a −4 °C room-sensor bias, +2.5σ) and 0/32-0/33 for twin and
    onset. The point-in-time `leaking_valve` at its defaults reads 24/33 there.
  - **Not scored.** Stuck-open valves (the same symptom) fire on 4/4 SDAHU and 9/10 FCU runs.
    The SDAHU supply-air sensor biases −2 / −4 °C fire as cooling leaks; this sensor confound is
    stated in every finding.
  - **No leak runs elsewhere.** The dual-duct archive has none.
  - **Not gated (maintainer decision).** The detector's results stay measured, opt-in records.
    No `drift.coil_leak_drift` key is added to any benchmark baseline, and the LBNL benchmark
    does not run the detector.
<!-- /0100-leak-drift -->

<!-- 0100-suggester -->
### Changed
- **Point-role suggester: vocabulary from public naming conventions (#102). This changes the
  default name-only output.** The 0.96 real-name evaluation showed most misses were vocabulary
  gaps. `FeatureSuggester` now reads:
  - new abbreviations: `AF`, `WH`, `STA`, `Enable`, `SS`, `PM` / `PMP`, `CHWP` / `HWP` / `CWP`,
    `CT`, `SAF` / `SF`, `BOI`, `SW` / `RW` / `SWT` / `RWT` / `LWT` / `EWT`, `HWL` / `CHWL` /
    `CDWL`, `HValve` / `CValve`, `HC` / `CC` / `LAT`, presence, occupant, `PIR`, weather / `WX`,
    and dry-bulb, wet-bulb and dew point;
  - run-together words (`OADMPR`, `RMCLGSPT`) split into known abbreviations, and vowel-dropped
    abbreviations (`Sply`).

  Context rules use the same lexical score:
  - a water point is not given an air-side role (a chilled-water flow is not `airflow`);
  - a chiller, boiler or tower places a point on its loop;
  - an unlocated temperature (`air_temperature`) reads as a space temperature, not outdoor air;
  - `temp_setpoint` reads as a zone setpoint, not `supply_air_temp_sp`;
  - a wet-bulb is not a dry-bulb `oat`;
  - pump and tower-fan speeds no longer read as `supply_fan_speed`;
  - a UUID or `PM2.5` names nothing.

  On a 6,546-case name × unit corpus, the top-1 role changed in 854 cases. The 0.95 golden cases
  are unchanged. Module constants `WATER_AIR_PENALTY` (0.4) and `UNLOCATED_TEMP_PENALTY` (0.6)
  hold the two new multipliers. Results on the same inputs (`examples/suggester_eval`):
  - real names, pooled, name only: 82.5 → 94.3 % top-1 (in-sample: the vocabulary was chosen
    from these misses);
  - held-out catalog names, name only (new `catalog_names.py`, out of sample): 72.2 → 83.0 %;
  - BTS anonymised: unchanged.
- **Weather-station guard on the time-series path (#102).** With `use_timeseries=True`, the data
  ranks only the outdoor roles when a point's name places it outdoors. For a weather station
  (`weather`, `WX`, meteo, the Synoptic `_set_N` suffix), it ranks only `oat`, `outdoor_rh` and
  `outdoor_co2`. A wet-bulb needs the name to say so. The data no longer turns an outdoor
  temperature into a wet-bulb or an outdoor humidity into a supply-air humidity. On real names,
  the data's top-1 losses fell from 5 to 2.
- **Not changed: the time-series path stays opt-in.** `docs/MAPPING-ASSIST.md` recommends
  turning it on when series are passed. That needs maintainer sign-off.

The synthetic, fleet, LBNL, BDG2 and BDG2 savings benchmark gates are not affected.
<!-- /0100-suggester -->

## [0.99.1] — 2026-10-03

**0.99.1 patch: open-fdd-ingested facilities report their time zone (#96).** The synthetic, fleet,
LBNL and BDG2 benchmark gates did not move.

### Fixed
- **The read API did not report the time zone of an open-fdd-ingested facility (#96).**
  `camber interop openfdd ingest` recorded the site zone only in the facility's `openfdd`
  provenance. Config runs found it there, but `/facilities` looked a zone up only on the registry
  entry or through a catalog `dataset_id`, so these facilities had no `timezone` and the trend
  viewer labelled their site wall clock as UTC. The importer now also writes the zone to the
  facility's `meta["timezone"]`. The read API looks up a facility's zone in this order: the
  explicit `timezone`, the catalog dataset zone, then the `openfdd` provenance zone, so facilities
  ingested with 0.99.0 report their zone without a re-ingest. An unchanged re-ingest, which is
  skipped, also fills in the missing key. Facilities with no recorded zone are unchanged.

## [0.99.0] — 2026-10-03

<!-- Each 0.99 branch adds its bullets only inside its own marked block. -->

**0.99: open-fdd interop and G36 heating at minimum OA (#22 items 1–4, #95).** CAMBER and
[open-fdd](https://github.com/bbartling/open-fdd) now meet at a file and process boundary, with
every result labelled by the engine and version that produced it. A reusable cross-check (#22
item 1) scores CAMBER's `g36_afdd` and open-fdd's pandas and SQL engines (commit `32a6d44`, PyPI
4.4.9) on the same labelled LBNL air-handler data, and explains each difference from public code
and open data. An importer (#22 item 2) reads an open-fdd building package or historian layout
into a CAMBER store, so drift, M&V and sensor-trust checks can run on data collected at the
open-fdd edge. Columns map through a versioned role crosswalk (#22 item 4), and the results go
back as a draft findings-exchange JSON (#22 item 3). `docs/ECOSYSTEM.md` opens with an entry
point for open-fdd users. In `g36_afdd`, heating (operating state 1) now needs the OA damper at
its minimum, the companion of #94's free-cooling test (#95).

### Added
<!-- 099-openfdd-crosscheck -->
- **G36 cross-check against current open-fdd (#22 item 1).** `examples/openfdd_crosscheck`
  runs CAMBER's `g36_afdd` and open-fdd's two engines on the same labelled frames, pinned to
  open-fdd commit `32a6d44` (PyPI 4.4.9). It works through files and processes only: the pandas
  engine runs in its own venv as a subprocess, and the SQL engine runs as `fdd_cli` in a container
  with no network and read-only mounts.

  The harness ships:
  - a versioned role mapping (`role_map.json`);
  - tolerance profiles (`profiles.json`): open-fdd's defaults, and the G36 Table 5.16.14.7
    tolerances CAMBER uses;
  - one normaliser per engine, recording each verdict's denominator;
  - per-engine, per-FC scoring with Wilson intervals, where "not evaluated" is kept apart from
    "not detected";
  - synthetic probes that isolate single engine behaviours.

  Results for all three engines on `lbnl-sdahu` (full subset) and `lbnl-ddahu` replace the "not
  yet re-compared" section of `docs/ECOSYSTEM.md`, which now also says where the 0.1.5
  comparison no longer applies. CAMBER runs with each dataset's run-template parameters, so FC6
  is evaluated on `lbnl-sdahu` (#94). Synthetic probes run through `fdd_cli` confirm which G36
  tolerances the SQL tuning file cannot set (FC7, FC9, FC11, FC13–FC15). Offline tests run
  without Docker or open-fdd: `tests/test_openfdd_crosscheck.py`.
<!-- /099-openfdd-crosscheck -->
<!-- 099-openfdd-importer -->
- **CAMBER reads open-fdd data (#22 item 2, provisional).** The new
  `camber.interop.openfdd` module reads an open-fdd building package (`openfdd_package_v1`, a
  folder or a `.zip`) or open-fdd's historian Parquet layout. CAMBER's drift, M&V and
  sensor-trust checks can then run on data collected at the open-fdd edge.
  - **Boundary.** It works through files and processes only. No open-fdd code is imported or
    copied, and nothing is written back.
  - **Required inputs.** `read_package` / `read_historian` take the site time zone and the unit
    system as required arguments, because a package carries neither and CAMBER will not guess.
  - **Mapping.** Columns map through the package's own map, which is authoritative. Every other
    column is counted and reported with its reason: no name, unknown name, deliberately
    unmapped, wrong equipment type, unusable unit or duplicate role.
  - **Units and time.** It handles UTC and local stamps, SI and IP units, declared units, and
    0–1 vs 0–100 percent. It reads equipment classes from the `equipType` stamps and the
    inventory, never from ids.
- **`ingest_package` and `camber interop openfdd ingest` (provisional).** They write one facility
  per building into a ParquetStore or a portfolio workspace.
  - **Lifecycle.** In a workspace the facility is registered through the lifecycle
    (`provisioning`, or `active` with `--activate`), the write takes the workspace lock and is
    audited (`interop.openfdd.ingest`), and a facility in any other state is refused.
  - **Provenance.** Recorded under `openfdd` on the registry entry: the source and schema
    version, the sha256 of every file read, the crosswalk version and its pinned docs commit, the
    zone, units and coverage.
  - **Re-ingest.** It is idempotent, and data is staged and swapped in. `--config-out` writes a
    starting run config: rules the mapped roles can run, the package weather as the shared
    outdoor temperature, and a daily M&V baseline per metered class. `camber interop openfdd
    inspect` does the same read without writing.
- **A versioned open-fdd → CAMBER role crosswalk (#22 item 4, provisional).** It ships as
  `camber/interop/openfdd/crosswalk.json`, version 1, written from open-fdd's documented
  Haystack-name and SQL-role vocabulary at a pinned commit. 50 of its 71 rows map; the other 21
  are deliberate non-mappings, each with its reason. `camber interop openfdd crosswalk [--json]`
  prints it.
- **A findings-exchange JSON, draft 0.1 (#22 item 3, provisional).** `findings_document`,
  `run_findings` and `camber interop openfdd findings CONFIG --out FILE` emit an engine-labelled
  document that a separate process (an open-fdd agent or pipeline) reads back.
  - Every record carries `{engine: {name, version}}`.
  - `declined` and `not_evaluated` are distinct statuses.
  - `magnitude` names its denominator.
  - Fields CAMBER does not yet produce are `null`.
  - See docs/INTEROP-OPENFDD.md.
<!-- /099-openfdd-importer -->

### Changed
<!-- 099-openfdd-importer -->
- **A store facility ingested from open-fdd knows its zone.** A store-source config's
  `source.timezone` now defaults to the zone the facility was ingested in, as it already did for
  catalog datasets. This is additive. A facility without an `openfdd` provenance block is
  unchanged.
<!-- /099-openfdd-importer -->
<!-- 099-g36-heating (#95) -->
- **G36 heating needs the OA damper at its minimum (#95).** `g36_afdd` now reads a fan-on hour
  with the heating coil alone active as operating state 1 only when the OA damper is at its
  minimum position, per the G36 §5.16.14 operating-state definitions. It uses the same learned or
  configured `oa_damper_min` and `oa_damper_tol` as the free-cooling test from #94. A heating
  hour with the damper open beyond the minimum is state 5, where only FC1–FC4 apply. A missing
  damper reading there leaves the hour unclassified. Without an OA damper point the valves-only
  reading stays, and the caveat now names both states.
  - **New outputs (provisional).** The `G36Result.n_heating_above_min_oa` field and the finding
    metric `heating_above_min_oa_hours`.
  - **Before/after.** No change on `lbnl-sdahu` (no heating coil), `nuig-ahu101` (no OA damper
    point) or the synthetic G36 scenarios, and no gated benchmark moved. On the dual-duct
    `lbnl-ddahu` the hot deck heats while the OA damper command is well above the learned 28 %
    minimum (median 55 % on the fault-free run). Between 885 and 1,520 heating hours per run move
    from state 1 to state 5, so FC5 is no longer applicable on the two stuck-damper runs. No
    run's flagged FCs or severity changed.
<!-- /099-g36-heating -->

## [0.98.0] — 2026-10-03

<!-- 0.98 is stacked on 0.97 (unreleased, below). This entry gets its date when 0.98 is
released. Each branch adds its bullets only inside its own marked block below. -->

**0.98: hardening from the workbook (#84-#93).** Working through the 0.97 workbook on real data
showed where CAMBER's answers were wrong, silent or hard to tune. On the air side (#84) the damper
census and `supply_air_control` gate on trended occupancy, and `leaking_valve` can credit a unit's measured
fan heat. For terminal units and ventilation (#85) a new rule, `actuator_stuck`, finds a box's
damper or valve stuck against the zone's demand, `reheat_penalty` reads a valve's measured
position, a heating shortfall is graded on how often it happens as well as how deep it goes, and
cohort deviation gains opt-in grouping and normalisation. The central plant (#86) checks the
sign of the chilled-water reset, learns a pump's VFD floor, runs the boiler rules without a run
status, and accepts a declared drift reference. Sensor health (#87) reads a fan-less unit's
outliers per operating mode and names clipped readings, stray rows and scheduled status points.
The RCx report (#88) names each issue's cause, lists the checks that did not run, says what data
an M&V refusal needs, and ends the issues with a generated "Verify on site" checklist. More
rules link to the PNNL re-tuning material and `ornl-frp-vav` declares a scored detector (#89).
Every tunable threshold is documented with its basis and a way to calibrate it, and run configs
read from YAML as well as JSON (#90). Three follow-ups close small inconsistencies: one 60 °F
free-cooling high limit (#91), a site elevation for derived wet-bulbs (`site_elevation_ft`, #92),
and named fan-off hours with an opt-in duration fault in `dcv_verification` (#93).

Four maintainer decisions shape the benchmarks. **S1:** the LBNL benchmark reads the
`leaking_valve` parameters from the `lbnl-sdahu` template, which moves the SDAHU and pooled
leaking-valve keys (the fan heat was calibrated on a run that is also a scored negative;
`docs/VALIDATION.md` states the circularity). **S2:** faultlab's healthy chilled-water reset now
runs the right way, with no synthetic key moving. **S3:** `actuator_stuck` is a scored synthetic
scenario (46 scored rules, new `actuator_stuck` TPR/FPR keys). **S4:** drift accepts a declared,
never-stored reference (another unit or a known-good period). For sensor health, decision (a)
applies: only units with no fan signal are read per inferred operating mode, and fan-gated units
are unchanged. Every other benchmark is unchanged.

### Added
<!-- 098-core -->
- **`roles_any_of` (#86 item 4a, provisional).** A rule may declare `roles_any_of`, a tuple of
  role groups of which each needs at least one role present (a run status *or* a gas input). The
  runners load every role of a group, skip the equipment when a group has none, and include the
  present group roles in the sensor-health gate. `camber.model.entities.missing_inputs` is the
  one test shared by the runners and `runnable_rules`, and `Runnable.missing_any_of` names the
  unmet groups. The mechanism alone leaves default outputs unchanged; the first rules to
  declare groups are the boiler firing rules (see Changed, 098-plant-boiler).
- **Rules that did not run are listed (#88 item 3, #86 item 4b).** `Registry.run`, `run_periods`
  and `run_fleet` take an optional `skipped=` list and append a `RuleSkip` (rule, equipment,
  class, missing inputs, reason `missing_inputs` / `no_data` / `no_verdict`) for each equipment
  a rule applies to but produced nothing on. A skip is recorded only when the rule applies: its
  declared classes match the equipment's, or at least one of its inputs is present (a chiller
  rule on an air handler is not applicable, not skipped). The default (`None`) records nothing,
  so the benchmarks and faultlab are untouched. `run_config` collects them in
  `RunResult.rules_skipped`, adding a rule-level record when a configured rule found none of its
  inputs on any equipment; they stay out of `findings` (and `findings.json`, so `datasets score`
  decline counts do not move).
  - **RCx report.** Appendix A gains a "Checks not evaluated (missing inputs)" table (rule,
    missing inputs, equipment: one row per rule and missing set, six units named, then "and N
    more"), and the cover a "Checks not evaluated" row, both only when there is something to
    list. `no_verdict` records are kept for later use but not listed.
- **An M&V refusal says what data is needed (#88 item 4).** New `camber.mandv.sufficiency`:
  `baseline_need(interval, n_have, min_n=...)` returns the required, available and missing
  amount, its unit, the calendar days it takes to collect, and a one-line text ("1,440 baseline
  hours needed, 168 available: 1,272 more hours (about 53 days of data)").
  `caltrack_savings` and `caltrack_savings_hourly` raise `InsufficientBaseline`, a `ValueError`
  whose message is byte-identical to before, with the gap on `.need`: too few days or hours,
  missing hour-of-week bins, or thinly observed bins. The config's daily M&V decline for too few
  days adds `metrics["data_needed"]` to its `mv_baseline` and `mv_savings` findings
  (`declined_reason` and `summary` unchanged), and the RCx "M&V and drift" section prints a
  "Data needed" paragraph per meter. Hourly M&V has no config path, so its need is reported
  through the exception only. Workbook `capstone` pins the one-week refusal's need at 1,272 hours.
<!-- /098-core -->
<!-- 098-thresholds -->
- **Tunable thresholds, documented (#90, provisional).** `camber/rules/param_docs.py` documents
  every numeric, flag and enumerated constructor parameter of every built-in rule, including the
  extra instances. Each entry gives the unit, a sensible range, the basis of the default
  (`standard: ...` only where the code cites a section, `public source: ...`, `CAMBER judgment` or
  `calibrated on ...`) and how to calibrate it from your own data. Defaults are read from the
  constructors, never copied. `FIXED` lists thresholds that are still hard-coded, and a test fails
  on any undocumented or stale parameter.
- **`camber rules params [RULE] [--json|--yaml]`.** Prints each parameter with its default,
  basis and calibration hint, plus a ready-to-paste config snippet. The YAML snippet carries each
  note as a comment beside its value.
- **Every built-in rule is tunable from a config.** `make_rule` and a config's `params` now also
  cover the extra instances (`cohort_airflow`, `cohort_space_temp`, `sat_/static_reset_effectiveness`,
  `sat_/static_rogue_zone_census`, `sat_/static_cohort_starvation`); their identity arguments
  (the cohort role, the reset kind) stay fixed. `rule_factories()` lists them.
- **YAML run configs (the `[yaml]` extra, `pyyaml>=6`).** `camber run`, `report`, `explain`,
  `ask`, `fleet`, `drift`, `mv`, `python -m camber.config`, the portfolio migration and the edge
  config read `.yaml` / `.yml`. Without PyYAML the command stops with an error that names the
  extra. The loader types values as JSON does (dates, `07:00`, `no` / `on` and `012` stay
  strings), so equivalent JSON and YAML configs give identical results, as a test asserts. JSON
  stays the dependency-free default.
- **`camber datasets config --format yaml`** (and `--out *.yaml`) writes a template as YAML, with
  its `_comment` notes turned into comments; `config_template(..., format=)`. Writing YAML needs
  no extra.
- **Calibration provenance in findings.** A rule entry may carry a `basis` map
  (`{"fan_heat_f": "calibrated on ..."}`), which each finding of the rule records as
  `metrics["param_basis"]` = `{param: {"value", "basis"}}`. A misspelt parameter name is an
  error. Existing configs are unaffected.
- **Docs.** `docs/THRESHOLDS.md` is generated from the registry by `scripts/thresholds_doc.py`
  (`--check` is a test). The new guide `docs/TUNING.md`, "Tuning thresholds with your own data",
  covers calibrating on a known-good period, avoiding circular calibration, recording provenance
  in config comments and re-checking against labelled data. Its worked examples use
  `lbnl-sdahu` and `nist-heatpump-fdd`. Also updated: CLI.md, API-STABILITY (provisional), the
  mkdocs nav, the README, and a "going further" pointer in the workbook index.
<!-- /098-thresholds -->
<!-- 098-plant-reference -->
- **A declared, never-stored drift reference (#86 items 1 and 5, decision S4; provisional).** A
  `drift.families[]` entry may carry `"reference": {"equip": ID}` (score every other unit of the
  class against a named healthy one, optionally with its own `"period"`) or
  `"reference": {"period": [start, end]}` (a known-good window of the same unit). The reference is
  fitted in a scratch in-memory store on every run and never saved, so a run still cannot mint
  its own baseline. Findings carry `metrics["baseline_source"]` (`reference:<equip>` or
  `period:<start>..<end>`) and a caveat, and their summaries read "vs the reference ..." instead
  of "vs frozen baseline". The reference unit declines as `is_reference`; a reference that cannot
  serve a detector declines every target (`reference_missing_inputs`, `reference_untrusted`,
  `empty_reference`). A section whose families all declare a reference needs no `store` and no
  windows. `DriftFamilyResult.reference` is new (also in `as_dict`); the drift report and
  `camber drift run` name the reference. Nothing changes for configs without one.
- **The LBNL plant templates score the plant drift detectors.** `lbnl-chiller.json` and
  `lbnl-boiler.json` (and the `plant-cooling-tower` / `plant-boiler` exercise configs) declare
  `PLANT__fault_free` as the reference for the `tower` and `boiler` families, and the catalog
  declares `cooling_tower_fan_effort_drift: [tower_fouling]` and
  `boiler_efficiency_drift: [boiler_fouling]` as scored targets. Through `camber run` the verdicts
  match `examples/lbnl_fdd/plant_detectors.py` run by run: tower fan effort 2/3 (fouling 065 and
  080 at +17.8 / +10.9 fan %-points; 095 at +3.3 is under the floor), boiler efficiency 3/3
  (+53.7 / +24.9 / +5.1 %; 095 clears the 5 % warn floor by 0.1 point), no false alarm on the
  20 / 13 other runs. The scorer counts the reference run as a correct negative (0/21, 0/14).
<!-- /098-plant-reference -->
<!-- 098-terminal-reheat -->
- **`heat_valve_position`: a reheat valve's measured position beside its demand (#85 item 3,
  provisional).** A new role for a unit that trends both the controller's demand and the valve's
  feedback: map the demand to `heat_valve` and the feedback to `heat_valve_position`. It is a
  percent role (0-1 fractions are rescaled), physically bounded like the other valves, carries
  the Haystack tags `heating valve sensor` and the 223P quantity of `heat_valve`, and is offered
  by the mapping assistant for percent units. It is not exported as a Brick point (the importer
  reads a heating coil's position sensor as `heat_valve`). `lbnl-fpu` maps `RH_VLV_S` to it; its
  catalog known issue and mapping comment say why.
- **`reheat_penalty(fan_heat_f=None)` (#85 item 3).** A fan-powered box's own fan (and, in a
  parallel box, the plenum air it mixes in) lifts the discharge above the entering air with the
  valve shut. A number of °F raises both valve-vs-discharge bounds (5 °F no rise at full valve,
  10 °F big rise with it shut) by that much; `"auto"` estimates it per box as the median
  closed-valve, airflow-bearing lift over the entering air (fan-on samples only when the fan
  status is mapped, >= 12 samples), clipped to 0-8 °F. The value used is reported as
  `fan_heat_f`. The default `None` changes nothing; the two `lbnl-fpu` exercise configs set
  `"auto"`, with its basis.
<!-- /098-terminal-reheat -->
<!-- 098-rcx-cause -->
- **Why free cooling was missed (#88 item 2).** `free_cooling_missed` records the cause as
  additive metrics; its severity and `missed_pct` do not change. `missed_cause` is
  `damper_not_delivering` when, on missed hours with a usable temperature balance
  (|OAT - RAT| >= 5 °F), the OA damper was commanded at least 90 % open while the measured
  OA fraction stayed below 80 %, on at least 20 % of those hours and 24 h. It is
  `economizer_not_commanded` when a damper command is trended but stayed below open, and
  `undetermined` otherwise. The rule also reports `commanded_open_pct`, `commanded_open_hours`,
  `commanded_open_oaf_median_pct` and `missed_damper_cmd_median_pct`. The new parameters
  (`cmd_open_pct`, `oaf_open_pct`, `stuck_min_share_pct`, `stuck_min_hours`,
  `stuck_low_oaf_pct`) are documented in `docs/THRESHOLDS.md`. Measured on real data: the four
  lbnl-sdahu stuck-damper runs read `damper_not_delivering` (40-87 % of those hours) and the
  fault-free and valve-leak runs 0 %; on lbnl-ddahu the damper stuck closed reads 95 % and the
  fault-free run 0 %.
- **`Recommendation.cause` (#88 item 2, provisional).** Every recommender names the finding's
  cause in a short phrase built from the metrics it reads, e.g. "Outdoor-air damper not
  modulating (stuck low)" or "Hot-water pump pinned at its minimum speed". `title` stays the
  action. `free_cooling_missed` with `damper_not_delivering` gets a new recommendation, "Repair
  the outdoor-air damper or actuator", linked to the PNNL economizer guide and Re-tuning ch. 6.
  "Stuck low" or "stuck part open" follows `commanded_open_oaf_median_pct` against
  `stuck_low_oaf_pct` (default mirrored in `DEFAULT_PARAMS["econ_stuck_low_oaf_pct"]`).
<!-- /098-rcx-cause -->

<!-- 098-sensor-health -->
- **Clipped readings are named (#87 item 2, provisional).** `sensorhealth.clipped_at_limit(series,
  role)` finds a pile-up at a round-number range limit (at least 12 samples and 0.5 % within 0.1 %
  of the span of the extreme, ten times the density of the adjacent 5 % band, and the limit a round
  number to within 0.01 %, in degF or degC for temperatures). `frame_checks` flags the point
  `clipped` and fills `SensorTrust.clipped` (`side`, `limit`, `limit_label`, `n`, `frac`,
  `frac_fan_off`); no trust penalty. Checked on CO2, OAT, wet bulb, space and return air (both
  ends) and airflows, water flows and duct static (high end). On the catalog: nuig-ahu101's CO2 at
  2,000 ppm (all fan-off), lbnl-b59's OA flow at 20,000 cfm, and the simulated 140 °F bound of one
  LBNL dual-duct fault run.
- **Outliers read per operating mode (#87 item 1a, provisional).** `sensor_trust(mode=,
  mode_source=)` and `frame_sensor_health(mode=None | "auto" | Series)` judge a fan-dependent
  duct point's outliers within each operating mode; `"auto"` infers an off-mode (OA damper <= 2 %,
  every coil valve <= 1 %) on a unit with no fan signal only. New `SensorTrust.mode_source` and
  `mode_outlier_frac`.
- **Stray lead / tail rows (#87 item 1b).** A point's rows beyond a gap of 30 days or more (and 20 %
  of its span) that hold at most 1 % of its samples are left out of the judgement: flag
  `stray_lead` / `stray_tail`, with `SensorTrust.main_start`, `main_end` and `n_stray`.
- **Scheduled status points (#87 item 3).** `gapfill_signature(series, role=None)`: for a stepwise
  point (a status role, or >= 95 % of samples on two levels) days sharing a pattern with two or
  more others are reported as "N days follow a fixed schedule" (`scheduled_days`,
  `n_schedule_patterns`) rather than warned on; `repeated_days` keeps only unexplained repeats.
<!-- /098-sensor-health -->
<!-- 098-rcx-verify -->
- **"Verify on site": a generated walk-down checklist in the RCx report (#88 item 1).** New
  provisional module `camber.walkdown`: `site_checks(issues, *, recommend, rule_of, overrides,
  trust, skipped, declined)` returns `SiteCheck` items (`issue_key`, `equip`, `kind`, `look_at`,
  `point`, `confirms`, `refutes`, `references`, plus `rule` and `rank`), ordered sensors,
  equipment, design values, data. Sensors: each sensor a conditional issue leans on (with its
  gated trust), a `sensor_drift` issue's sensor, and each input a check declined as untrusted.
  Equipment: one item per issue from `SITE_CHECKS[rule][cause]`, a template for every rule with a
  recommender, its cause read from the finding's metrics (`CAUSE_KEYS`: `missed_cause`, the CHW
  reset direction and flow mode, a pump's inferred VFD floor, the reheat valve divergence, the
  DCV causes, ...); other rules get a generic item built from their required inputs. Design
  values: `DESIGN_PARAMS` (site facts such as a minimum outdoor-air fraction, a high limit or an
  occupancy schedule, never detection thresholds) still at the rule's default. Data: the checks
  not evaluated for missing inputs. The texts describe what a technician checks on site, in
  CAMBER's own words; every item links PNNL Re-tuning chapter 9 through the references registry
  (`camber.references.WALKDOWN_REFERENCES`, not a rule mapping), and nothing from the chapter is
  reproduced.
<!-- /098-rcx-verify -->

<!-- 098-terminal-stuck -->
- **`actuator_stuck`: a terminal or fan-coil damper or valve stuck against the zone's demand (#85
  item 1, provisional).** A new rule for terminal boxes and fan coils (an air handler's outdoor-air
  damper is out of scope). It finds the runs where an actuator holds one position over occupied
  (trended occupancy, else the schedule), fan-on hours, through a thin wrapper around the
  sensor-health run finder (`camber/rules/_flat_runs.py`), and judges each run of at least
  `min_flat_hours` by what the zone asked for. **Contradicted** runs can reach `fault`: a damper
  shut through occupied hours with the airflow at or below 5 % of `AIRFLOW_SP` or `min_airflow`
  (with neither, judged against the occupied mode alone, with a caveat); a damper or cooling
  valve below its open limit while the zone runs `warm_margin_f` over its cooling setpoint for a
  quarter of the run (a heating valve: under its heating setpoint); fully open while the zone
  sits `satisfied_margin_f` inside its setpoint for half the run; a heating-valve position flat
  while its demand moves 20 points. **Unexplained** runs (one value for `whole_day_share` of a
  day's active samples while the demand, airflow setpoint, a setpoint or the zone temperature
  moves) warn at most, and a run at a limit the demand agrees with is saturated, not stuck. Roles:
  `damper`, `heat_valve_position` (else `heat_valve`), `cool_valve`. Metrics per role:
  `flat_runs`, `stuck_share`, `value`, `tier`, `reason`, `driver` and the flagged runs. Registered
  in `RULE_CLASSES`, the applicability table (`terminal`, `fan_coil`), the scorecard
  (maintenance), the references (PNNL chapter 7), the parameter docs, and the advisory
  recommender (cause "Damper stuck at 20 %: the zone runs warm while it holds still"; stroke the
  actuator before retuning anything). On `ornl-frp-vav` it finds 6 of 6 stuck days on the default
  subset and 17 of 18 on the full one, with no false alarm on the 13 fault-free and airflow-bias
  days (see Changed).
- **`actuator_stuck` is a scored synthetic scenario (S3, approved).** `faultlab` gains a VAV box
  whose damper sticks at 30 % through warm afternoons; `coverage.n_scored` and `n_single` go 45 ->
  46 and the synthetic benchmark gains `actuator_stuck.tpr` 1.0 and `.fpr` 0.0. Every other
  synthetic key is unchanged.
- **Cohort-deviation options (#85 item 2, opt-in).** `CohortDeviation` (`cohort_airflow`,
  `cohort_space_temp`) takes `group_by_topology` (compare only the units behind one air handler,
  grouped like the rogue-zone census), `normalise` (`"design_max"`: by `design_max={equip: cfm}`,
  else the peak `AIRFLOW_SP`; `"reference"`: by `reference={equip: reference_equip}`),
  `summary="variability"` (the standard deviation) and `tail` (`"low"` / `"high"`). Every default
  reproduces the earlier result. `camber.charts.cohort` gains the `variability` summary, a `tail`
  argument and `cohort_deviation_from_values`. Size normalisation alone cannot isolate a stuck box:
  on the ORNL set the share of design airflow flagged six healthy box-days and one stuck day of
  six; each box against its own fault-free day flagged all six.
<!-- /098-terminal-stuck -->

<!-- 098-mv-vectors -->
- **Shared M&V test vectors (`examples/mv_vectors/`).** These are inputs, CAMBER's expected
  outputs and a standalone checker, so that another change-point / Guideline 14 implementation
  (open-fdd's helpers first) can be cross-checked through files alone.
  - **Synthetic cases.** 8 seeded cases with exact truth: 2P; 3PH at 58 °F; 3PC at 65 °F; 4P;
    5P at 55 / 68 °F; a noise pair either side of the baseline gate; and a weak-weather load.
  - **BDG2 meters.** 6 meters (electricity, chilled water, steam and gas) as daily and
    calendar-month aggregates, plus one irregular-bill variant. Each has a baseline year and a
    reporting year, both raw and with a 10 % injected saving.
  - **BDG2 data stays local.** CAMBER redistributes no datasets, so the BDG2 inputs and predicted
    series are not committed; `expected.json` keeps only their statistics. `fetch_bdg2.py`
    (numpy, pandas and the standard library) rebuilds them:
    - it downloads the publisher's files at the catalog's URLs and checks their sha256 pins;
    - it rebuilds the aggregates deterministically and checks each derived CSV against its own
      pinned sha256;
    - it writes them to a git-ignored `local/` folder, and prints the citation and the CC BY-SA
      4.0 licence.
  - **Bill cases.** 3 cases (1 synthetic, 2 BDG2) with mid-month 28–35-day reads, one estimated
    read and one missing bill. They run through CAMBER's billing config path: `base_f: "auto"`
    bases with their ranges, degree days built from each day vs from the bill's mean, the
    degree-day model against the change-point models by BIC, Portfolio Manager calendarization
    and avoided cost at each bill's own (synthetic) rate. The synthetic bill case is committed;
    the 2 BDG2 bill cases are rebuilt locally.
  - **Expected outputs.** `expected.json` uses the versioned schema `mv_vectors/1` and records,
    per fit:
    - the selected kind, every candidate's BIC and the BIC gap;
    - the coefficients, in CAMBER's form and in a convention-free `slopes_dEdT` form;
    - n, p, R², adjusted R², CV(RMSE) and NMBE;
    - the baseline-gate, calibrated-simulation-gate and SEP verdicts side by side;
    - Option C savings with FSU.

    `predictions/` holds CAMBER's predicted series row by row.
  - **Consumer tools.** These never import CAMBER, so a pandas library and a SQL twin are checked
    alike:
    - `check_vectors.py` (numpy and pandas only) rebuilds the expected numbers (`--self-test`),
      compares another implementation's results JSON with the documented tolerances and prints
      CAMBER's own results (`--template`);
    - `export_parquet.py` writes typed Parquet copies of every CSV;
    - `example_results.json` is a worked results file.
  - **Documentation and licence.** `SCHEMA.md` documents every field, the two tiers, the method
    (grid, BIC, p counting, day weighting, the bill choices) and the tolerances. Everything
    committed is Apache-2.0. The locally rebuilt BDG2 files are CC BY-SA 4.0, with attribution.
  - **Regression test.** `tests/test_mv_vectors.py` regenerates the synthetic tier offline and
    requires an exact match, so any move in CAMBER's M&V numbers shows up there.
    - With `examples/_data/bdg2` present, it also rebuilds the BDG2 tier, checks every sha256
      pin and requires the BDG2 statistics to regenerate exactly.
    - `-m network` does the same from a fresh download.

    No default output changes.
<!-- /098-mv-vectors -->

### Changed
<!-- 098-core -->
- **Equipment class on role frames; the DCV return-air caveat only where it applies (#85 item
  4).** The rule runners (and a run's lazy `frame_for`) set
  `frame.attrs["camber_equip_class"]` to the equipment's class. `dcv_verification` keeps its
  "typically return-air CO₂" caveat on air handlers and on equipment of absent or unrecognised
  class (direct API calls are unchanged), and drops it on terminals and fan coils, whose CO₂ is
  the room's own: the finnish-dcv, b4b-windesheim and sdu-ou44 rooms (class `VAV`) lose it. Only
  that caveat changes; severities and metrics do not. Workbook `zone-dcv` pins the caveat absent
  and its instructor discussion point is rewritten.
<!-- /098-core -->
<!-- 098-air-gates -->
- **`damper_census` reads trended occupancy (#84).** Each box's occupied hours now come from its
  own trended occupancy point when it has any non-null value, and from the assumed weekday
  07:00-18:00 schedule only when it has none; trended warm-up / cool-down flags drop prep-mode
  samples (the rule declared them but never read them). A weekend test day now gets a census
  instead of "no damper data". New rule param `occupancy_gate` (`"trended"` default,
  `"schedule"` for the old behaviour, `"off"` for every sample), new `occupancy_gate` metric and
  `DamperCensusResult.occupancy_gate` field (`trended occupancy` / the assumed schedule / `mixed`
  / `off`), and `damper_census(..., use_trended_occupancy=True)`. **Intended default change:**
  on `ornl-frp-vav` the fault-free day's census median moves 39.3 -> 38.7 % (still a fault, all
  10 boxes low), and the weekend days `d3_stuck_000`, `d3_stuck_060` and `d3_stuck_100`, which
  returned "no damper data", now read 36.8 % / 40.3 % / 41.8 %.
- **`supply_air_control` gates on trended occupancy (#84).** New param `occupancy_gate`:
  `"trended"` (default) judges only fan-on samples that the unit's trended occupancy marks
  occupied, and keeps the fan-only gate when no occupancy is trended (no assumed-schedule
  fallback); `"schedule"` falls back to the weekday 07-18 schedule; `"off"` is the pre-0.98
  fan-only gate. The gate sits in the running mask, so the finding, its evidence chart and the
  triage violation mask judge the same samples. New `occupancy_gate` metric; the summary says
  "occupied running hours" when gated. **Intended default change:** the fault-free `lbnl-sdahu`
  unit goes from a warn at 12.1 % too warm (78 % of those hours were unoccupied fan cycling) to
  ok at 2.97 %; `AHU__damper_stuck_075` stays a fault, too cold 32.6 -> 31.2 %; the capstone's
  RCx report loses the fault-free control's `supply_air_control` issue (6 issues, was 7).
- **`irish-ahu` template comment corrected (#84).** The excess outdoor air is not "mostly the
  COVID-19 period": 53 % of the cooling-weather hours before it (2017-06 to 2020-02) against
  45 % during it (2020-08 to 2021-11).
- **Workbook.** `air-sat-reset` (fault-free unit ok at 3 %, the stuck damper 31 %, question 5
  recast around the gate, with a pinned check that `occupancy_gate: "off"` restores the 12 %
  warn), `air-static-pressure` (census median 38.7 %, the weekend caveat and common mistake
  rewritten) and the `air-economizer` instructor key (the stale sentence about the catalog note
  removed) updated; `capstone` re-verified.
<!-- /098-air-gates -->
<!-- 098-terminal-ventilation -->
- **`overcooling_severity` grades a heating shortfall on share as well as depth (#85).** The
  shortfall grade was the deepest tier sustained for an hour, so a few cold hours a year with the
  reheat saturated read `fault`. It is now the lesser of the depth tier and a share tier set by
  the share of occupied samples in a sustained shortfall at least `warn` deep (new
  `shortfall_share_pct`, default `{"warn": 5, "fault": 20}`; below 5 % a shortfall is `info`).
  **Intended default change:** on `lbnl-fpu` the fully open damper
  (`PFPU__VAVDMPRStuck_100pct`) goes from shortfall `fault` to `info` (fault-deep, but 0.81 % of
  samples) and its finding from `info` to `ok`; the stuck-shut reheat valve stays a shortfall `fault`
  (29.9 %). New metric `shortfall_depth_severity` keeps the depth-only grade, and a caveat says
  when the share lowered it. `shortfall_share_pct=None` restores the old grading. The same gate
  is available for the overcooling tiers as `share_pct`, off by default (no overcooling verdict
  changes). Workbook `zone-reheat-saturated`: answers 3 and 5 updated, and the new grade pinned.
<!-- /098-terminal-ventilation -->
<!-- 098-refs-catalog -->
- **More rules link to the PNNL Re-tuning material (#89).** `RULE_REFERENCES` maps 48 rules, up
  from 35, each checked against the guide's own headings. `sat_rogue_zone_census` links the
  discharge-air-temperature guide, whose reset section bases a zone-driven reset on the zones
  served, setting aside the warmest and coolest. `static_rogue_zone_census`,
  `static_cohort_starvation` and `damper_census` link the static-pressure guide, whose "too high
  or too low" section reads the box damper positions. `sat_cohort_starvation` maps to
  chapters 5 and 7; `reheat_capacity_shortfall`, `cohort_airflow` and `cohort_space_temp` to
  chapter 7.
  `condenser_bypass_leak`, `chiller_approach_fouling`, `chiller_staging_fleet`,
  `cooling_tower_fan_effort_drift` and `boiler_efficiency_drift` map to chapter 8. The G36
  reset-effectiveness rules, `filter_fouling`, `g36_afdd`, the DX and heat-pump rules and the other
  drift detectors stay unmapped, with the reason in a comment and in `docs/REFERENCES.md`.
  Reports gain "Learn more" links for these findings; no finding changes.
- **`lbnl-chiller` data issue `condenser-bypass-runs-implausible` (#89, annotate).** The five
  condenser-bypass runs hold a fixed bypass all year (0.917 / 0.967 / 0.988 of the condenser flow
  for 25 / 50 / 75), drive the condenser loop to 118-162 °F at p90 / maximum, and the two 75 % runs
  are the same data with the valve command at 0.0 in every row. The runs stay as published and
  scored. The ingest is unchanged, so every `content_hash` is too. The `plant-cooling-tower`
  workbook caveats and instructor notes now say "both 75 % runs" and link the issue.
- **`rbc-g36-ahu`: the stale `leaking_valve` note is corrected.** The rule has gated on a mapped
  fan status since 0.93 (#42). The catalog note and the template comment now say so, and say that
  the template still leaves the rule out until it is re-checked on these baselines.
<!-- /098-refs-catalog -->
<!-- 098-plant-chw -->
- **`chw_plant_reset` checks the reset's sign and recognises constant-flow plants (#86 item 2).**
  New params `design_deltaT_min_f` (8 °F, was fixed), `expected_reset_sign` (`"negative"`
  default: an outdoor-air reset lowers CHWST as OAT rises; `"positive"`, or `"any"` for the old
  either-way test), `flow_mode` (`"auto"` default, `"constant"`, `"variable"`) and
  `constant_flow_cv` (0.05). A clear slope the wrong way is no longer a reset:
  `chwst_reset_present` is false, the new `chwst_reset_direction` metric reads `reverse`
  (`expected` / `flat` otherwise), a caveat says so, and the finding warns. `Role.CHW_FLOW` is
  now an optional input: with `flow_mode="auto"`, a flow whose coefficient of variation over the
  running hours is at most `constant_flow_cv` (on at least 24 hours) marks a constant-flow plant,
  whose low loop delta-T is reported but left out of severity, with a caveat. New metrics
  `flow_mode` (`constant` / `variable` / `unknown`), `flow_cv` and `design_deltaT_min_f`;
  `CHWPlantResult` gains trailing `flow_cv` / `n_flow`. The recommender gives no "fix low ΔT"
  advice on a constant-flow plant and has a new branch for a reversed reset, "Find why the
  chilled-water supply warms in hot weather". **Intended default change** on `lbnl-chiller`
  (chiller 1's flow varies by at most 0.11 % on every run): the fault-free plant goes `fault` ->
  `ok` (delta-T median 6.4 °F, 80.1 % of hours below 8 °F, now not judged), and so do the other
  18 non-bypass runs (the chiller-bias runs' `fault`/`warn` were never this rule's to raise); the
  five tower-bypass runs go `fault` -> `warn`, their CHWST rising with OAT at +0.57 to +0.95 °F/°F.
- **The pump rules learn the VFD floor (#86 item 3).** `chw_pump_dp_reset` and
  `hw_pump_dp_reset` take `near_min_pct` (a number, or `"auto"`) and `floor_tol_pct` (1.0).
  `"auto"`, the chilled-water default, learns the pump's minimum speed from a plateau in its
  running speeds (`camber.chwpump.learn_vfd_floor`: the 2nd percentile, accepted when >= 10 %
  of running samples sit within `floor_tol_pct` of it and the 90th percentile is >= 20 points
  above it) and counts speeds at or below `max(25, floor + floor_tol_pct)` as near the minimum;
  with no plateau it falls back to 25. The hot-water rule keeps the fixed 25 % band by default.
  New metrics `vfd_floor_pct`, `near_min_band_pct` and `near_min_source` (`learned` / `default`
  / `fixed`) on both rules; the summary names the band. **Intended default change** on
  `lbnl-chiller`: the secondary pump's floor is learned at 34.5 %, so the fault-free run counts
  31.3 % of hours near minimum (was 0), and the two high-reading chiller-sensor-bias runs, where
  the pump idles at its floor 68 % of the time, go `ok` -> `warn`. No other severity moves.
- **Parameter registry: keyword-or-number ranges.** A parameter that takes a keyword or a number
  lists the keywords first and ends with the numeric range (`("auto", 10.0, 60.0)`), printed as
  '"auto", or 10.0 to 60.0' by `camber rules params` and `docs/THRESHOLDS.md`.
  `chw_plant_reset`, `chw_pump_dp_reset` and `hw_pump_dp_reset` move from fixed-in-code notes to
  documented tunables.
- **Workbook.** `plant-chw-reset-pumping`: the healthy plant's `chw_plant_reset` is pinned `ok`
  with the constant-flow check, the stuck bypass `warn` with a reversed reset (+0.76 °F/°F), and
  the learned floor (34.5 %, 31.3 % of fault-free hours; the chiller-bias run a `warn` at 68.6 %);
  the page's setup, steps, questions and caveats and the instructor key (answers 1, 2, 4 and 5,
  discussion, mistakes) are rewritten. `plant-sensor-vs-equipment`: answer 2 notes the
  high-reading runs' pump now warns at its floor.
<!-- /098-plant-chw -->
<!-- 098-plant-boiler -->
- **The boiler firing rules run without a boiler run status (#86 item 4a).**
  `boiler_summer_lockout`, `boiler_short_cycle` and `hw_plant_deltat` now declare
  `roles_any_of = ((boiler_status, gas_input_rate),)` instead of requiring `boiler_status`. With
  no run status mapped they read firing from the gas input above 5 % of its own 95th percentile
  (`camber.schedules.plant_run_mask`); a sample with no gas reading stays missing rather than
  counting as a stop. Such findings carry the metric `run_source: "gas"` and a caveat that a
  firing shorter than the resample interval is invisible. A frame that has a run status gives
  byte-identical findings. On `lbnl-boiler` (hourly) the three rules now give 17 findings each,
  all `ok`: the fault-free boiler fires 37.8 % of hours with 0.92 starts a day, 0 % of firing
  hours above the summer lockout, and a 36 °F loop delta-T median (12.2 % of hours below the
  20 °F floor on the worst fouling run). The three rows leave the RCx report's "Checks not
  evaluated" table. The online monitor, the default evidence chart, the RCx confidence inputs
  and the triage sensor-precedence roles now count a rule's `roles_any_of` inputs too. The
  workbook exercise `plant-boiler` is updated.
<!-- /098-plant-boiler -->
<!-- 098-plant-reference -->
- **`camber drift freeze` refuses a config whose families declare a reference** (exit 1, naming
  them), and `drift accept` / `drift_refit` leave those families out: there is nothing stored to
  freeze or move. The config docstring's "a run never mints its own baseline" now states the S4
  exception. Docs: CLI.md ("A declared reference"), TUNING.md ("Drift references"),
  PLANT-DETECTORS.md and VALIDATION.md.
- **Workbook.** `plant-cooling-tower` pins the fan-effort findings and
  `cooling_tower_fan_effort_drift` TPR 67% (step 6, question 6, the instructor's answer 6 and a
  discussion point on the bypass runs, where the matched-load model extrapolates).
  `plant-boiler`: the point-in-time rules still miss the fouling, but `boiler_efficiency_drift`
  catches all three (TPR 100%, replacing the overall TPR 0 % answer); page steps 5 and 7,
  questions 4 and 5 and instructor answers 4 and 5 rewritten.
<!-- /098-plant-reference -->
<!-- 098-terminal-reheat -->
- **`reheat_penalty` and `overcooling_min_flow` read the valve position when it is mapped (#85
  item 3).** Both judge heat actually delivered, so with `heat_valve_position` mapped they read it
  in place of the demand (new metric `valve_signal`: `position` or `demand`). When the demand is
  at or above 90 % while the position is at or below 5 % on at least 25 % of the occupied
  full-demand samples (>= 12 of them), the finding carries a "stuck or failed valve, not a reheat
  penalty" caveat (new metric `valve_divergence_share`). Sites with one valve point are unchanged.
  **Intended default change** (from the new `lbnl-fpu` mapping): on `PFPU__ReheatVLVStuck_0pct`
  `reheat_penalty` goes from `fault` (51 % "open", read from the demand) to `ok` (0 %) with the
  caveat, and `overcooling_min_flow` from `fault` to `ok` with the caveat; the healthy runs' figures
  hold (11 / 67 / 99 % open). `reheat_capacity_shortfall`, `overcooling_severity` and the
  reheat-valve drift detector keep reading the demand. Workbook: `zone-reheat-overcooling`
  question 5 and `zone-reheat-saturated` question 4 rewritten, with their instructor keys.
<!-- /098-terminal-reheat -->
<!-- 098-air-leak -->
- **`leaking_valve` can credit a unit's measured fan heat (#84 item 1, provisional).** New opt-in
  params, all off by default so default outputs are byte-identical: `measured_fan_heat_f` (the
  unit's own fan rise; a cooling leak on the supply-air path is then a rise below
  `measured_fan_heat_f - cool_delta_thr_f`), `cool_delta_thr_f` (the cooling margin, default
  `delta_thr_f`), `occupied_only` (judge occupied samples, from the trended occupancy when
  mapped, else the weekday 07-18 schedule; `Role.OCCUPANCY` becomes an optional input only on
  such an instance) and `judge_heating_on_supply_air` (`False` judges a heating leak only on the
  heating coil's own leaving air). New metrics `cool_shift_f` and `occupancy_gate` appear only
  when configured, with caveats; `LeakValveResult` gains `cool_shift_f`, `occupancy_gate` and
  `hw_judged`, and `analyze_leak_valves` the same keywords. Documented in `param_docs.py` and
  `docs/THRESHOLDS.md`.
- **The `lbnl-sdahu` template catches the published valve leak.** Its `leaking_valve` entry sets
  `measured_fan_heat_f: 1.0`, `cool_delta_thr_f: 1.0` and `occupied_only: true`, with a `basis`
  map. **Intended default change:** `AHU__coi_leakage_010` goes ok -> fault (60.5 % of occupied,
  fan-on, valve-shut hours below the mixed air, was 0.6 % beyond the 3 °F margin); the fault-free
  run stays ok at 2.4 % and the damper runs under 2 %. **The 1.0 °F was calibrated on the
  fault-free run, which is also a scored negative**: its verdict is in-sample, and the template
  comment, `docs/VALIDATION.md` and `docs/TUNING.md` say so; a half-year split (calibrate on one
  half, judge the other) keeps the fault-free run ok (2.4 % / 2.7 %) and the leak a fault
  (54.3 % / 77.2 %). The `lbnl-ddahu` template comment records why the rule is not run there
  (its mapped supply air is the cold deck; run anyway it false-faults `DDAHU__DMPRStuck_OA_0`).
- **LBNL benchmark (approved gated move, S1).** `examples/lbnl_fdd/benchmark.py` builds
  `LeakingValve` from the same template params. SDAHU TPR 0.4 -> 0.6, accuracy 0.5 -> 0.6667,
  correct diagnosis 0.4 -> 0.6; pooled TPR 0.7 -> 0.8, accuracy 0.7692 -> 0.8462, correct
  diagnosis 0.7 -> 0.8; FPR unchanged at 0. No other benchmark key moves. The committed baseline
  is refreshed by the integrator.
- **Workbook.** `air-heat-cool`: the leak run is now a fault (TPR 100 %, FPR 0 %), the fault-free
  median rise reads +1.1 °F over occupied hours (was +1.0 °F over all fan-on hours); the page,
  questions, going-further steps and instructor key are rewritten around the calibrated fan heat
  and its circularity.
<!-- /098-air-leak -->
<!-- 098-rcx-cause -->
- **RCx issue headings name the cause, not the remedy (#88 item 2).** An issue page is headed
  "Issue N: {cause}", and the executive summary's Issue column shows the cause. The action
  paragraph reads "Recommended action — {title}: {action}", so the action titles stay in the
  report. `RcxReport.to_dict()` issues gain `title` and `cause`. The RCx golden file changes
  (intended). Workbook `capstone`: the top issue is now headed "Outdoor-air damper not
  modulating (stuck low)", with a damper repair as its action. The answer key and the page
  questions are updated.
<!-- /098-rcx-cause -->
<!-- 098-rcx-verify -->
- **RCx report: a "Verify on site" section, on by default (#88 item 1).** Section id `verify`
  (slot `section:verify`, so `--notes-template` writes it), after the issue pages and before
  Further reading; omitted when it would be empty, or left out with `report.rcx.sections`. A
  lead paragraph links chapter 9, then one table per kind of item: # (linked to the issue, or A
  for Appendix A), Equipment, Look at, Point, Confirms, Refutes. Further reading adds chapter 9
  when the section is present. The RCx golden file changes (intended). Workbook `capstone`: step 2
  now compares the student's checklist with the generated one, answer 4 of the instructor key
  points to it, and a new check pins the section's static-setpoint sensor item (trust 0.40) and
  the onset unit's damper item. The capstone's minimum outdoor air and high limit are site
  parameters in its config, so the section lists no design values for it; the key now says so.
<!-- /098-rcx-verify -->

<!-- 098-sensor-health -->
- **RCx trust table (#87).** A unit with no fan signal is scored per its inferred operating mode in
  the gated column, and *Gate used* says "outliers read per mode: inferred off-mode (OA damper and
  coil valves closed)". On the catalog only `irish-ahu` changes: its supply air reads *trusted*
  0.89 (was *untrusted* 0.19; plain `sensor_trust` now 0.27), its stray 2015 rows are left out
  (return air, OA damper and both valves *suspect* -> *trusted*), and its economizer high-limit
  issue's confidence moves M -> H. Every point flagged `clipped` shows the flag (one LBNL dual-duct
  return air). Fan-gated units are unchanged: reading them per fan mode too was measured and
  rejected (see docs/SENSOR-HEALTH.md). The workbook exercise `data-trend-quality` answers 4, 6
  and 7 are rewritten.
<!-- /098-sensor-health -->
<!-- 098-followups -->
- **One free-cooling high limit, 60 °F (#91).** `camber.freecooling.free_cooling_opportunity`
  defaulted `high_limit_f` to 65 °F while the `free_cooling_missed` rule used 60 °F. Both now read
  one constant, `DEFAULT_FREE_COOLING_HIGH_LIMIT_F` (60 °F), CAMBER's deliberately conservative
  screening default. Only a direct library call with no `high_limit_f` changes (it counts fewer
  free-cooling hours). Rule findings and reports are unchanged (the RCx economizer page passes
  `economizer_high_limit`'s value and prints it), and so are the synthetic and fleet benchmarks.
  `docs/TUNING.md` gains guidance on a climate-appropriate dry-bulb high limit (the unit's
  sequence, the energy code's high limit for the climate zone, or the trends).
- **`condenser_water_reset` takes the site elevation (#92).** `CondenserWaterReset` and
  `analyze_cw_reset` gain `elevation_ft` and `pressure_psia` (default `None`), passed to
  `stull_wetbulb_f` when the wet-bulb is derived from OAT + RH. Without either, a derived wet-bulb
  is now caveated as sea-level, as the cooling-tower rules already did; that caveat is the only
  change to default output. A new top-level config key, `site_elevation_ft` (feet; validated by
  `camber.config.site_elevation_ft`), sets the elevation once for the site. It reaches
  `cooling_tower_approach` and `condenser_water_reset` and, through the `drift` section
  (`run_drift`, `refit_baselines` and `build_drift_suite` gain `elevation_ft`),
  `cooling_tower_approach_drift` and `cooling_tower_fan_effort_drift`. A rule's own `elevation_ft`
  or `pressure_psia` wins. On a tower that resets 1:1 with the true wet-bulb at 1,600 m in hot,
  dry air, the sea-level slope reads 0.93 and the corrected one 1.00, so `reset_present` can flip
  near `reset_slope_flat`. The catalog's plant data trend a measured wet-bulb and do not change.
  Documented in `docs/CLI.md` and `param_docs`.
- **`dcv_verification` names fan-off hours and gains an opt-in duration fault (#93).** Occupied
  hours below `oa_floor_cfm` with the supply fan off (from `SUPPLY_FAN_STATUS`, else
  `SUPPLY_FAN_SPEED` at or below the new `fan_off_speed_pct`, 5 %) are named in the summary and a
  caveat ("supply fan off while scheduled occupied"), counted as `fan_off_occupied_pct` /
  `fan_off_occupied_hours`, and left out of `below_floor_pct`, which is now the shortfall with the
  fan running. The share fault still reads every below-floor sample (`below_floor_total_pct`, the
  old `below_floor_pct`), so severity does not move; with no fan signal nothing changes. The new
  `below_floor_fault_hours` (default `None`, opt-in) faults a contiguous below-floor episode of
  that many occupied hours whatever its share; the longest episode (`below_floor_longest_h`,
  `_start`, `_fan_off_h`) is reported either way. `assess_dcv` gains `fan_off_mask` and the
  matching `DcvResult` fields. The ASO recommender reads a `fan_off_occupied` cause ("Run the
  supply fan whenever the space is occupied"). On `lbnl-b59` the October and December 2020 days
  are now named fan-off (41, 82, 40 and 40 h); `below_floor_pct` goes 2.2 / 4.2 / 2.1 / 2.0 % ->
  0.1 / 0.0 / 0.1 / 0.0 %, severity stays `info`, and the 2020 smoke-mode window stays
  unflagged. `b4b-windesheim` is unchanged and `finnish-dcv` (no fan signal) gains only the new
  metrics. `docs/VENTILATION.md`, `docs/VALIDATION.md` and the workbook `zone-min-oa` (caveat,
  answer 5 and two new checks) are updated.
<!-- /098-followups -->

<!-- 098-terminal-stuck -->
- **`ornl-frp-vav` declares `actuator_stuck` as its detector (#89 item 3, #85 item 2).**
  `labels.targets` is now `{"actuator_stuck": "terminal_damper"}` and the template runs it;
  `unmet_setpoint_hours` stays as context. Full subset: TPR 17/18, 94 % [74-99 %] (Wilson 95 %),
  FPR 0/13, 0 % [0-23 %], against `unmet_setpoint_hours`' 10/18 and 1/13; default subset 6/6 and
  0/1 (was 2/6 and 1/1). The miss is room 106 stuck at 100 % on a warm day, where an open damper
  is what the zone asked for. `known_issues` gains three lines: the airflow-bias days have no
  detector, a detection pointing upstream on the duct-static-collapse days is legitimate, and
  fleet and neighbour findings are not scored. The workbook exercise `zone-bad-box` scores
  `actuator_stuck` and reads its verdict on each day.
- **A reheat valve that diverges from its demand gets a repair recommendation.** When a
  `reheat_penalty` finding's `valve_divergence_share` is at or above 0.25 (the rule's caveat
  threshold; `DEFAULT_PARAMS["reheat_valve_divergence_share"]`), the advice is "Repair the reheat
  valve or actuator", with the cause "reheat valve stuck or failed shut (the controller calls for
  heat the valve does not deliver)", instead of "Minimize reheat".
<!-- /098-terminal-stuck -->

### Documentation
<!-- 098-refs-catalog -->
- **`docs/DATASETS.md`: "Running the workbook answer checks on local files" (#89).** Covers
  `CAMBER_WORKBOOK_FROM_DIR`, the `<dir>/<dataset-id>/` layout, symlinking a checkout's differently
  named files, the manual `lbnl-b59` files (`Building_59.zip`, `README_Dryad_Bldg59.txt`) and the
  per-dataset file list, which a test keeps in step with the harness and the catalog.
<!-- /098-refs-catalog -->
<!-- 098-mv-vectors -->
- **ECOSYSTEM: open-fdd's ECM tooling.** A reciprocal note covers open-fdd's ECM workbooks,
  their reference calculators and EnergyPlus-twin comparison, and its change-point and G14
  helpers. It explains how its pre-retrofit estimates and CAMBER's post-retrofit measurement fit
  together, with measured inputs for the calculators (#22), and links the shared M&V vectors.
  The "not yet re-compared" note now applies to the fault conditions only.
<!-- /098-mv-vectors -->

### Fixed
<!-- 098-terminal-ventilation -->
- **`docs/VENTILATION.md` overstated the `lbnl-b59` wildfire result (#85).** It said the 2020
  smoke mode was a below-floor `fault` on two rooftop units. Re-measured on the default subset
  (which holds the whole OA-flow record, April–December 2020): all four units are below the
  750 cfm floor in 2.0–4.2 % of occupied hours, `info` under the 10 % fault share, and those hours
  are fan-off days in October and December. In the smoke-mode weeks (2020-08-24 to 09-06) the
  dampers sat at their 10 % minimum and the units still took in 1,130–3,625 cfm, 0–0.9 % below
  the floor. The page, `docs/VALIDATION.md` and the `zone-min-oa` caveat now say so.
<!-- /098-terminal-ventilation -->
<!-- 098-refs-catalog -->
- **`tests/test_references.py` sees instance-named rules.** `_rule_names()` now includes
  `builtin.rule_names()`, so a mapping for `cohort_airflow` or the census and starvation rules no
  longer fails the "every mapped rule exists" check.
<!-- /098-refs-catalog -->
<!-- 098-w1-integration -->
- **Stale rule docstrings and comments corrected (text only, no behaviour change).** The
  `supply_air_reset_compliance` module described the SAT reset as a positive slope against OAT
  (it is negative since 0.92); `dcv_verification` said a trended occupancy is AND-ed with the
  schedule (it replaces it); the rogue-zone census said the fleet runner carries no topology (it
  auto-builds a naming one, which takes precedence over `groups`); `chw_supply_tracking` left out
  the chiller-power run gate added in 0.92; and the `compressor_short_cycle` / `heatpump_defrost`
  default comments got their arithmetic wrong (a 5 min timer allows about 12 starts an *hour*;
  hourly defrost is about 48 reversing-valve transitions a day), so 12 and 24 a day are screening
  ceilings, not generous ones. The `camber rules params` basis text for those two says so.
- **Parameter registry: the `occupancy_gate` and share-gate entries.** `supply_air_control` and
  `damper_census` `occupancy_gate`, and `overcooling_severity` `shortfall_share_pct` and
  `share_pct`, are documented in `camber/rules/param_docs.py` and `docs/THRESHOLDS.md`. A
  dict-valued tier map names its keys in its unit and gives the range of each value ("each value:
  0 to 100"); `overcooling_severity` `tiers` follows the same convention, and a test checks every
  tier map's default values against the range. The `damper_census` fixed-in-code note no longer
  says occupied hours are always the weekday schedule.
<!-- /098-w1-integration -->
<!-- 098-plant-chw -->
- **The synthetic healthy chilled-water reset ran the wrong way (S2).** `faultlab`'s clean
  `chw_plant_reset` scenario raised CHWST as OAT rose (`42 + 0.30 x (OAT - 55)`); a healthy
  outdoor-air reset lowers it. It is now `clip(52 - 0.30 x (OAT - 55), 42, 52)`. The synthetic
  benchmark's metrics JSON is byte-identical before and after (sha256 `a0d5c8fb...`); without the
  fix the new sign check would have raised `chw_plant_reset.fpr` 0 -> 1. A test fixture in
  `tests/test_optional_role_honesty.py` had the same wrong-way "working reset" and is corrected.
<!-- /098-plant-chw -->
<!-- 098-w2-integration -->
- **A constant held while the fan is off is not a clipped sensor.** `clipped` now needs the
  pile-up on the fan-on samples too for a fan-dependent point (return air, airflow, OA airflow,
  duct static) on a unit with a fan signal: a BAS or gap-fill that parks the return air at a round
  70.0 °F while the fan is off had read as a clip at its range limit. CO2 and the outdoor and space
  points are still judged on every sample (a CO2 transmitter topping out in a closed room
  overnight is a real clip). The four clips on the catalog store all remain; the RCx golden loses
  its four `clipped` tags on the demo units' fan-off return air.
- **Every recommendation names a cause.** The reversed chilled-water reset recommendation (from
  `098-plant-chw`) gets the cause "Chilled-water supply warms in hot weather (plant capacity or a
  reversed reset)", and a test checks that every recommender call in `camber.aso` sets one.
- **`reheat_penalty` `fan_heat_f` documents its keyword:** its range is `"auto"`, or 0.0 to 8.0
  (the keyword-or-number convention from `098-plant-chw`).
<!-- /098-w2-integration -->
<!-- 098-integration -->
- **The walk-down covers `actuator_stuck` and the DCV fan-off cause.** `camber.walkdown` gains a
  `SITE_CHECKS["actuator_stuck"]` entry (a "contradicted" item when any actuator's flat run
  contradicts the zone's demand, else the unexplained-flat item) and a `fan_off_occupied` item for
  `dcv_verification` (#93), so every cause the DCV recommender can lead with has its own check.
  The reheat walk-down cause now reads `DEFAULT_PARAMS["reheat_valve_divergence_share"]`, the
  threshold the recommender uses.
<!-- /098-integration -->
<!-- 098-pyarrow-compat -->
- **A store with migrated partitions opens on pyarrow 17-24.** `migrate_partitions` (0.95) read
  each legacy part file with `pyarrow.parquet.read_table(path)`. On pyarrow 17 through 24 that
  applies hive partition discovery to a single file's own path, so a part under
  `facility_id=X/year=Y/` came back with dictionary-typed `facility_id` and `year` columns, and
  the rewritten month files carried them. The store then refused to open ("Unable to merge: Field
  facility_id has incompatible types: dictionary<values=string, indices=int32> vs string"). Part
  files are now read with `ParquetFile(path).read()`, which returns only the stored columns on
  every pyarrow; the retention rollups' legacy reads use the same helper. On pyarrow 14-16 and 25
  nothing changes: a migrated store is byte-identical before and after. `_migrate_year` calls
  `pyarrow.compute` through `call_function`, so mypy passes with pyarrow builds that bundle type
  stubs (which do not declare the generated compute functions) as well as without them.
- **A constant meter finds no steps on any BLAS.** `detect_step_changes` round 1 scaled its
  segmentation by the first-difference noise and fell back to the residual variance, with no
  rounding floor: a constant meter's exact fit leaves residuals of zero or ~1e-15 depending on the
  BLAS build (Accelerate gives zero, OpenBLAS does not), and on OpenBLAS round 1 segmented that
  rounding noise into two zero-size "steps" that the later rounds kept. Round 1 now applies the
  same rounding floor as the later rounds (falling back to a unit scale below it).
- **Dependency floors match what works.** `pyproject.toml` now declares `numpy>=1.24.1`,
  `pandas>=2.2.1` and `pyarrow>=14.0.2` (were 1.24, 2.0 and 14). CAMBER and its tests use pandas
  2.2 API (`Index.round`, the `"ME"` and `"min"` aliases); pandas 2.2.0 has a `concat` regression
  that left a SQL source's merged index unsorted; numpy 1.24.0 breaks matplotlib's masked
  `fill_between` on time axes; pyarrow 14.0.0/14.0.1 emit pandas 2.2's BlockManager
  `DeprecationWarning` on every store read (14.0.0 also carries CVE-2023-47248). A new CI job,
  `min-deps`, runs the suite on Python 3.10 with every core floor pinned
  (`.github/min-deps.txt`; a test keeps it equal to the declared floors).
- **Change-point ties break the same way on every platform.** The change-point grid searches in
  `camber.mandv.models` (3PC/3PH/4P, the to-zero variants, 5P/5PZ) kept the grid point with the
  strictly lowest SSE. On a flat SSE surface (no data between grid points, or a 4P/5P with no
  second regime) several points tie up to rounding, and the BLAS build (Accelerate vs OpenBLAS)
  picked the winner: fitting the five kinds to 1,961 BDG2 2016 meters, daily and monthly (19,610
  fits), 170 fits' change points differed between the two builds. A later grid point now
  replaces the best only when it lowers the objective by more than 1e-10 of it (floored at 1e-12
  of the weighted sum of y² for exact fits), so ties keep the first grid point; `best_model`
  treats BICs within 1e-9 as a tie (the earlier kind wins). The two builds now agree on every
  fit. Default output: the five benchmarks are byte-identical before and after on both builds,
  and no selected kind changes. Monthly change points do move within a tie, by at most 2.2 °F
  (151 fits on Accelerate, 166 on OpenBLAS; 29 of them the selected model, whose SSE is
  unchanged to 1e-10); daily fits do not move. The shared M&V vectors are regenerated: two
  non-selected candidates' change points move (`syn_2p` monthly 4P 79.762 -> 78.64791, and the
  `bills_bdg2_rat_public_leta_elec` 5P low change point 39.71 -> 38.62). The vectors test now
  compares non-selected candidates' change points within SCHEMA's ±2 °F and everything else
  exactly, and SCHEMA.md says how ties are broken.
<!-- /098-pyarrow-compat -->
<!-- 098-fc9 -->
- **`g36_afdd`: shut valves alone are not free cooling (#94).** The G36 operating-state
  classifier read every fan-on interval with both coil valves shut as OS#2 (free cooling), even
  with the outdoor-air damper shut. Following the G36 §5.16.14 operating-state definitions, OS#2
  now also needs the OA damper open beyond its minimum position plus `oa_damper_tol` (5 points).
  At or below that, the interval is OS#5, where only the state-independent FC1-FC4 apply. These
  are deadband hours and unoccupied recirculation runs. OS#5 is used rather than "unclassified"
  because the definitions place an interval that fits none of OS#1-#4 there, and it keeps FC1-FC4.
  The minimum position is `oa_damper_min`. By default it is learned as the median damper command
  over fan-on mechanical-cooling intervals below `econ_damper_open` (the OS#4 position), or taken
  as 0 % with a caveat when there are fewer than 24 such intervals. A missing damper reading with
  both valves shut is unclassified. A frame without an OA damper point keeps the valves-only
  reading and gets a caveat. New finding metrics: `oa_damper_min`, `oa_damper_min_source`,
  `idle_at_min_oa_hours`, `occupancy_gate` and `unoccupied_hours`. New trailing `G36Result`
  fields: `oa_damper_min`, `oa_damper_min_source`, `n_idle_at_min_oa` and `n_unoccupied`.
  `run_g36_afdd` gains `oa_damper_min=`, `oa_damper_tol=` and `occupied=`, and `classify_os`
  gains `oa_damper_min=` and `oa_damper_tol=`.
  - **Occupancy.** Unoccupied operation is still evaluated by default, since G36 suspends AFDD
    only while the AHU is not operating. A new `occupancy_gate` parameter (`"off"` by default, or
    `"trended"`) limits the evaluation to the occupied hours of a trended occupancy point, with
    no assumed-schedule fallback. Once OS#2 is fixed it changes no verdict on `lbnl-sdahu` or
    `lbnl-ddahu`.
  - **lbnl-sdahu, full subset, at defaults.**
    - The fault-free run goes from `warn` to `ok`: FC9 drops from 18.65 % to 0 %. All 323 FC9
      hours were unoccupied hours with the fan at full speed and the damper at 0 %, and those
      483 hours are now OS#5. The learned minimum is 10 %, the unit's documented fixed minimum.
    - `onset_damper_stuck_025`: FC9 drops from 11.65 % to 0 % (the same unoccupied pattern). The
      run stays `fault` on FC10 and FC11.
    - `damper_stuck_075` and `damper_stuck_100_short` go from `fault` to `ok`. FC8 drops from
      30.9 % and 26.8 % to 0 %, and FC12 from 8.75 % and 3.98 % to 0 %. Those hours had the damper
      *commanded* to its 10 % minimum with both valves shut, while the stuck damper let in 68-100 %
      outdoor air. That is OS#5 by command, so the free-cooling tests no longer apply, and their
      earlier hits came from hours that were wrongly read as free cooling. The G36 test for this
      fault is FC6 (outdoor-air fraction vs the minimum). With the template's own `min_oa_pct`
      1.6, FC6 flags both runs (27.8 % and 28.1 %, `fault`), while the fault-free run reads
      1.18 %.
    - `coi_stuck_050`: 7 hours move to OS#5, and FC14 goes from 13.06 % to 12.68 % (still
      `fault` on FC13).
    - The other ten runs are unchanged.
  - **Other datasets.**
    - `lbnl-ddahu` `DMPRStuck_OA_0`: 186 hours with the damper at or below its learned 28 %
      minimum (176 of them at 0 %) move to OS#5, and FC8 goes from 16.2 % to 39.7 % (still
      `fault` on FC10). The fault-free and `DMPRStuck_OA_100` runs are unchanged.
    - `nuig-ahu101` is unchanged, and `irish-ahu` declines before and after (it has no fan
      signal).
  - **The `lbnl-sdahu` template runs `g36_afdd` with FC6 enabled.** It passes the unit's
    documented minimum, `min_oa_pct` 1.6 (the 10 % fixed damper minimum measured as an OA
    fraction, the value `outdoor_air_fraction` already uses), with that provenance in the template
    comment and its `basis` map. The rule's own defaults are unchanged. From the template,
    `damper_stuck_075` and `damper_stuck_100_short` read `fault` again on FC6 (27.8 % and 28.1 %),
    and the fault-free run reads `ok` (FC6 1.18 %). No other run's verdict changes; FC6 reads
    0-1.5 % on the other runs.
    - **Templates left unchanged.** `lbnl-ddahu` states a seasonal minimum (31.8 %, 11.9 % in
      Jun-Aug). No single `min_oa_pct` works there: FC6 faults the fault-free run at 20.5 % with
      31.8 and at 47.1 % with 11.9. `g36_afdd` declines `irish-ahu` (no fan signal) and
      `lbnl-fcu` (a fan-coil unit), so they are left as they were. No exercise config runs
      `g36_afdd`. `camber datasets score lbnl-sdahu` reads only the declared targets, so its score
      is unchanged.
  - **Unchanged.** No gated benchmark key moves (the synthetic, fleet, LBNL, BDG2 and BDG2 savings
    benchmarks are all stable), and no workbook answer changes (no exercise runs `g36_afdd`).
<!-- /098-fc9 -->

## [0.97.0] — 2026-10-03

<!-- 0.97 is stacked on 0.96 (unreleased, below). This entry gets its date when 0.97 is
released. Each branch adds its bullets only inside its own marked block below. -->

**0.97: the PNNL re-tuning workbook (#79, #80, #81, #82, #83).** A hands-on course that follows
PNNL's free Building Re-tuning training, run on the open dataset catalog in `camber lab`. Default
outputs are unchanged, and no gated benchmark moves.

### Added
<!-- 097-framework (#79) -->
- **The workbook framework (#79, provisional).** `docs/workbook/` holds a curriculum map from the
  PNNL chapters and guides to exercises and datasets (`index.md`), an exercise page template
  (`_template.md`: goal, Learn more, datasets and licence, setup in the lab and on the CLI,
  steps, questions, what CAMBER shows, caveats, going further) and an instructor page with a
  section per exercise (answer key, discussion points, common mistakes). The docs site has a new
  *Re-tuning workbook* section, and the README a "Learn re-tuning with CAMBER" section.
  - **Answer keys pinned by tests.** Each exercise declares its datasets, configs, CLI commands and
    expected answers (a finding present or absent on given equipment, a metric within a
    tolerance, a label score, or a free-form check) in `tests/workbook/exercises/<id>.py`, with a
    synthetic stand-in shaped like each dataset. The offline tests run the exercise's configs on
    the stand-in every time; `pytest -m network` runs them on the real catalog data. A stale
    answer fails and names the exercise, and the instructor page must quote every pinned figure.
  - **Reference links from the registry.** Workbook pages link PNNL only through reference-style
    Markdown links labelled with `camber.references` ids; `scripts/workbook_refs.py` prints the
    definitions and `--check` (also a test) rejects an unknown id, a URL that differs from the
    registry's, or a PNNL link made any other way. No mkdocs plugin is needed.
  - **Catalog `exercise` field.** `suggested_analyses.exercise` is validated: a docs-relative
    workbook page (`workbook/<id>.md`, optionally `#<anchor>`) or an https URL. `camber datasets
    info` prints its published URL. `camber lab` serves a relative link offline from the local
    docs tree (`/lab/docs/workbook/<id>.md`, a script-free reading copy; `--docs DIR`, by default
    the checkout's `docs/`), and links the published docs site otherwise.
  - **Exercise config templates.** `camber/datasets/configs/exercises/<id>.json` (package data)
    holds a tuned config per exercise where the dataset's default doesn't fit;
    `camber datasets config <dataset> --exercise <id>` (and
    `datasets.config_template(..., exercise=)`) writes it.
  - **Worked example: `air-economizer`** on `lbnl-sdahu`: a damper stuck open is found by
    `outdoor_air_fraction` and `economizer_high_limit`, one stuck near minimum only as missed free
    cooling (`free_cooling_missed`), and the label score is read against the published labels.
<!-- /097-framework -->
<!-- 097-air (#80) -->
- **Workbook: the air side (#80).** Five exercises following PNNL re-tuning chapters 5 and 6 and
  the AHU guides, each with an instructor key pinned by the answer-key tests:
  - `air-sat-reset` (`lbnl-sdahu`, `lbnl-ddahu`, `irish-ahu`): is the supply-air temperature
    reset, which way should it move with the outdoor air, how far below the Guideline 36 target
    does it sit, and does the unit hold its setpoint.
  - `air-static-pressure` (`lbnl-sdahu`, `ornl-frp-vav`): a flat duct static setpoint, and a
    damper census over one real test day's ten boxes.
  - `air-heat-cool` (`lbnl-sdahu`, `lbnl-ddahu`, `irish-ahu`): the published 10 % cooling-valve
    leak (which `leaking_valve`'s 3 °F margin misses; the exercise teaches the comparison with
    the fault-free run), both valves open on a dual-duct unit by design, and a real unit's coils.
  - `air-economizer` gains an `irish-ahu` part: the economizer rules on a real, unlabelled unit
    with a documented 100 % outdoor-air period, split by period.
  - `air-scheduling` (`ornl-frp-ops`): a 24/7 baseline against a night setback held by fan
    cycling, compressor short-cycling, and the supply fan's night energy.
  - Exercise configs `air-sat-reset`, `air-sat-reset--lbnl-ddahu`, `air-sat-reset--irish-ahu`,
    `air-static-pressure`, `air-static-pressure--ornl-frp-vav` and `air-heat-cool--lbnl-ddahu`;
    the catalog links `lbnl-ddahu`, `irish-ahu` and `ornl-frp-ops` to their exercises.
<!-- /097-air -->
<!-- 097-zone (#81) -->
- **Workbook: terminal units and ventilation (#81).** Five exercises for PNNL chapter 7 and the
  zone heating and cooling and minimum outdoor-air guides, each with an instructor key pinned by
  offline stand-in tests and `-m network` tests on the real data:
  - `zone-reheat-overcooling` (`lbnl-fpu`): a high minimum airflow that overcools and reheats,
    against a stuck damper that floods the box (`airflow_tracking`, `reheat_penalty`,
    `overcooling_min_flow`); the south-zone box is the one under test.
  - `zone-bad-box` (`ornl-frp-vav`): the one box with a flat damper among ten, the SAT rogue-zone
    census, the rooftop unit's airflow and static following the stuck box, and why a comfort rule
    scores poorly as a stuck-damper detector (TPR 33 %, FPR 100 % on the published labels).
  - `zone-reheat-saturated` (`lbnl-fpu`, `ornl-frp-vav`): a zone below its heating setpoint with
    the reheat demand saturated (`reheat_capacity_shortfall`), a heating shortfall rather than
    overcooling; and a real building whose electric reheat is not trended, where no reheat verdict
    is possible.
  - `zone-dcv` (`finnish-dcv`, `b4b-windesheim`, `sdu-ou44`): a documented DCV law, a verdict
    that depends on which CO₂ sensor you trust, and the honest "not judged".
  - `zone-min-oa` (`lbnl-b59`): the system-level 62.1 Ventilation Rate Procedure with stated
    assumptions (public sources only), measured OA several times the requirement, and CO₂ and
    DCV as second opinions.
  - Four exercise config templates (`zone-reheat-overcooling`, `zone-reheat-saturated`,
    `zone-reheat-saturated--ornl-frp-vav`, `zone-min-oa`); the catalog entries `lbnl-fpu`,
    `ornl-frp-vav`, `finnish-dcv`, `b4b-windesheim`, `sdu-ou44` and `lbnl-b59` link their
    exercise.
<!-- /097-zone -->
<!-- 097-plant (#82) -->
- **Workbook: the central plant (#82).** Five exercises on the open LBNL plant datasets, each with
  a page, an instructor key, a tuned config and a synthetic stand-in:
  - `plant-chiller-efficiency` (`lbnl-chiller`): a generic kW/ton ceiling flags the healthy plant;
    the ceiling calibrated from the fault-free run finds the severe chiller fouling and the tower
    bypass, and the label score shows its false alarms.
  - `plant-cooling-tower` (`lbnl-chiller`): a controlled tower hides fouling in fan effort rather
    than approach; condenser-water reset; `condenser_bypass_leak` on the stuck bypass.
  - `plant-chw-reset-pumping` (`lbnl-chiller`): chilled-water reset, a low delta-T that is a
    property of the constant-flow primary loop, a flat DP setpoint, and a VFD floor above the pump
    rule's fixed near-minimum band.
  - `plant-boiler` (`lbnl-boiler`): why the lockout, short-cycle and delta-T rules stay silent on
    a plant whose boiler status is an enable, a pump that runs all year, and boiler fouling that no
    point-in-time rule sees.
  - `plant-sensor-vs-equipment` (`lbnl-chiller`): where each sensor bias shows up (kW/ton, the
    secondary pump, a sensor-offset attribution) and why the score counts it as a negative.
  - The `lbnl-chiller` and `lbnl-boiler` catalog entries link their exercise.
- **`lbnl-chiller` mapping (#82).** The secondary loop's lead pump speed and its DP and DP
  setpoint now map to `chw_pump_speed`, `chw_diff_press` and `chw_diff_press_sp`, so
  `chw_pump_dp_reset` can run on the plant. Re-ingest (`--force`) to pick them up. The dataset
  template's rules do not read them, so its findings are unchanged.
<!-- /097-plant -->
<!-- 097-practice (#83) -->
- **Workbook: data quality, energy charting, M&V and the capstone (#83, provisional).** Four
  exercises, each with its page, instructor key and answer-key test (stand-in offline, real data
  under `-m network`):
  - **`data-trend-quality`** (`nuig-ahu101`, `lbnl-b59`, `irish-ahu`): the core points each unit
    trends, then the sensor-health layer on real faults -- a copied return-air point, mixed-air
    sensors that fail the flow balance, room CO2 clipped at full scale, a meter held over two
    weekends, a gap-filled CO2 stretch -- and an "untrusted" supply air that is a well-controlled
    sensor.
  - **`data-energy-charting`** (`bdg2`, `valladolid-uva`): weekday and weekend profiles,
    out-of-hours base load, load factor and the daily change-point baselines as the
    weather-dependence chart.
  - **`mv-baselines`** (`cofactor-drammen`, `valladolid-uva`, `bdg2`): G14 acceptance and SEP
    50001 §6.4.1 validity, forecast against backcast, SEP chaining, a chain across the spring 2020
    school closure with a non-routine adjustment, and bill-only M&V on synthetic bills cut from
    an open meter (days-weighted fit, bases chosen from the bills, calendarized). Four exercise
    configs: `mv-baselines`, `mv-baselines--forecast`, `--backcast` and `--covid`.
  - **`capstone`** (`lbnl-sdahu`, `ornl-frp-ops`): the RCx report on the fault-onset splice, a
    walk-down checklist built from its recommended actions, conditional issues and default
    assumptions, a re-tuning plan, verification by drift (a first-half baseline scored on the
    second half), and a before-and-after scheduling test where hourly M&V is refused for lack of
    baseline data. Exercise config `capstone`.
  - Catalog `exercise` links: `nuig-ahu101` to `data-trend-quality`, `bdg2` to
    `data-energy-charting`, `valladolid-uva` and `cofactor-drammen` to `mv-baselines`.
<!-- /097-practice -->

### Fixed
<!-- 097-integration -->
- **Workbook network tests (test harness only).** `pytest -m network` now ingests each run's
  `subset`: the plant exercises that need `full` get their own sibling store, so the default and
  full ingests of `lbnl-chiller` no longer replace each other. A dataset already in the store is
  ingested again when its subset or mapping has changed (for example the new `lbnl-chiller` pump
  roles). A manual download (`lbnl-b59`) whose files are neither in `CAMBER_WORKBOOK_FROM_DIR`
  nor in the cache is skipped, with a message listing the files to download, instead of failing
  on a fetch. The harness docstring lists the files `CAMBER_WORKBOOK_FROM_DIR` needs for each
  dataset, and a test keeps that list in step with the catalog.
- **Workbook index.** The status note says that all 19 exercises are written.
<!-- /097-integration -->

## [0.96.0] — 2026-10-03

<!-- 0.96 is stacked on 0.95 (unreleased, below). This entry gets its date when 0.96 is
released. -->

**0.96: a local catalog UI, a real-building dataset for point mapping, report fixes, and a
fault-lifecycle scoping fix (#75, #45, #76, #77, #78).** `camber lab` serves the dataset catalog
as a loopback-only page with fetch and ingest jobs, licence gates and workspace registration
(#77). The catalog gains BTS, three real buildings with about 20,000 Brick-labelled BMS points,
and a `brick_streams` adapter to ingest it (#75); the point-role suggester can now read a point's
data as well as its name, and is evaluated on BTS and on real published point names (#45).
Recommended actions follow each finding's cause, trend-only reports are no longer titled as
Std-211 audits, the trend viewer draws one panel per unit, and reports link to the PNNL Building
Re-tuning guides (#78). A site-keyed fault run no longer resolves other sites' faults (#76).
Default outputs are unchanged apart from the report text and titles noted below, and no gated
benchmark moves.

### Added
<!-- 096-lab (#77) -->
- **`camber lab`: a loopback-only local UI for the dataset catalog (#77, provisional).**
  `camber lab [--workspace W | --store S] [--dir D] [--port 8765]` serves a catalog page at
  `http://127.0.0.1:8765/lab`.
  - **The page.** It is vanilla JS: no framework, no CDN, everything inline. Datasets carry
    licence badges; the table has filters, and shows download and store sizes against the free
    disk. **Fetch & ingest** runs as a job, one at a time on a single worker thread, with
    progress and cancel. Rows link to the trend viewer (`/ui?facility_id=ds-<id>`, a new deep
    link), an on-demand audit report, and the publisher.
  - **Research-only datasets.** A dialog asks the user to accept the terms and type the dataset
    id. The acceptance is recorded in the existing `acknowledgements.json` ledger
    (`via: "lab fetch"`), and research-only reports carry the non-commercial banner.
  - **Workspaces.** In a portfolio workspace, a dataset's `ds-<id>` facility is registered
    `provisioning`, ingested under the single-writer lock, and then activated. Every fetch,
    acknowledgement and ingest is audited (`lab.*`). Suspended, offboarding and archived dataset
    facilities are not re-ingested. Without a workspace the lab uses a plain store, as the
    `camber datasets` commands do.
  - **API.** `camber.lab`: `LabApp`, `dispatch_lab` (pure routing; the read routes `/ui`,
    `/facilities`, `/points` and `/history` are delegated unchanged to
    `camber.api.server.dispatch`), `make_lab_server`, `serve_lab`, `JobQueue`.
- **Lab security** (docs/SECURITY.md §11):
  - It binds 127.0.0.1 only; any other bind address is refused.
  - A Host and Origin allowlist (against DNS rebinding) and a `Sec-Fetch-Site` check.
  - A per-run CSRF token compared with `hmac.compare_digest`.
  - JSON only (415), a 16 KiB body cap (413), unknown fields refused, catalog ids only.
  - A hash-pinned CSP on the page, and sandboxed reports.
  - A static test proves `camber.lab` and `camber.datasets` reach no BACnet, Modbus, OPC-UA,
    MQTT, OpenADR or edge module.

<!-- /096-lab -->
<!-- 096-report (#78) -->
- **Linked PNNL Building Re-tuning references (#78, provisional).** `camber.references` is a
  registry of the nine guides to re-tuning measures, the ten training chapters, *Trending
  Requirements for Re-tuning*, the ECAM interval-data guide, the large-office savings report and
  the project pages: id, title, publisher, document number, URL, kind and `verified_on`
  (2026-09-29). `RULE_REFERENCES` maps 35 rules to them, checked against each guide's section
  headings; a rule no guide clearly covers maps to a training chapter or stays unmapped.
  - Reports: the audit findings table and the Recommended actions table get **Learn more**
    links; each RCx issue page ends its action with them, and the RCx report adds a short
    **Further reading** section (id `reading`) listing only the guides relevant to its issues.
  - Text and JSON carry the ids: `learn more: <ids>` in the text audit,
    `Recommendation.references`, the action-plan rows' `references`, each RCx issue's
    `references`.
  - **Link only**: no PNNL text, figure or PDF is copied into the repository or a report.
    `scripts/datasets_linkcheck.py` now also checks every reference URL weekly (a 404 / 410 is
    drift; `--no-references` skips them).
  - Docs: a [references page](docs/REFERENCES.md), links from the rule pages (ventilation,
    economizer, reset), and ECAM as a related tool in the ecosystem page.
<!-- /096-report -->
<!-- 096-bts (#75, #45) -->
- **The `bts` catalog entry (#75).** BTS, the Building TimeSeries dataset (Prabowo et al.,
  NeurIPS 2024 Datasets and Benchmarks; CC BY 4.0, open tier): three real Australian buildings,
  about 20,000 Brick-labelled BMS points over 2021-2023, the data behind the Brick by Brick 2024
  challenge. It is the evaluation set of the time-series role suggester (#45).
  - Fetched from the data archive, never the MIT-licensed repository snippet; the MIT (code and
    snippet) versus CC BY 4.0 (data) split is recorded in the entry's known issues. All nine
    files are pinned (size and SHA-256) from real downloads with `scripts/datasets_refresh.py`.
  - `default` is the three sites' metadata and Brick models plus site B's streams (1.5 GB,
    38 MB once ingested); `full` is all three sites (19 GB, 1.1 GB once ingested).
  - Nine data issues, each with evidence: UTC timestamps, undocumented units, 125 listed streams
    without a file, site C running outside the documented period, week-long whole-site outages,
    site C's zero dropouts (masked by a `fix` quirk; `--no-corrections` keeps them), placeholder
    and 32-bit overflow values, site C's negative airflows and mixed pressure scales, and 136 site
    C points that keep a non-anonymised second stream id.
- **The `brick_streams` ingest adapter (#75).** Per-site Brick models whose points name their
  series through a literal (`senaps:stream_id`), a stream index and a zip of series files: one
  facility per site, roles from the Brick class, equipment from the `isPointOf` owner or the
  first containing entity whose class `equip_classes` maps (named `<class>_<id prefix>`), UTC
  instants moved to each site's wall clock, sample-and-hold resampling. A second point with the
  same role on one owner becomes equipment `<equip>-2`, never averaged; unmapped points are
  counted per class in the provenance. Series pickles are read with a restricted unpickler that
  resolves only numpy's array globals, straight from the verified zip. Quirks run per stream (a
  quirk's `runs` names sites).
- **Time-series evidence for point-role suggestion (#45).** `FeatureSuggester(use_timeseries=True)`
  also reads what a point's data says, for exports whose names are anonymised
  (`camber.mapping_timeseries`, provisional, numpy and pandas only):
  - `profile_series` / `SeriesProfile`: value quantiles, cadence and the change-of-value pattern,
    binary and two-level values, plateaus at the series' extremes, step-like movement, daily and
    weekly periodicity, and the correlation with a site outdoor-air series (`oat=`);
  - `ROLE_TEMPLATES` / `template_scores`: 45 hand-written role templates scored in every plausible
    unit when none is declared;
  - `ProfileModel`: an optional numpy Gaussian class model fitted on other buildings' labelled
    points (`model=`);
  - `blend`: the data's weight falls from 0.8 to 0.1 as the name becomes informative, so a clear
    name still dominates. `suggest()` also takes `oat=` and a precomputed `profile=`; the basis
    `timeseries` is new.
  - Evaluated in `examples/suggester_eval` (none of it a gated benchmark):
    - **Real BMS point names** (`real_names.py`): every mapped point of seven open
      real-building catalog datasets, scored by its published name against the catalog
      mapping (hand-curated by CAMBER). Pooled over 422 points, the name alone reaches 82.5 %
      top-1 and the name plus the data 83.9 % top-1 / 89.1 % top-3. Excluding `irish-ahu` and
      `lbnl-b59`, whose names the tokenizer was written against (129 points), the figures are
      52.7 % and 58.1 % top-1, and 53.5 % and 72.9 % top-3. The data helped 11 points and hurt
      5, all of the losses on weather-station points and one valve. The LBNL simulated FDD sets
      are reported apart: 48.6 → 56.9 % top-1 over 72 points.
    - **BTS with the names hidden** (`bts.py`, leave one building out): the name-only suggester
      places 0 % of 903 points; the data alone places 48.0 % top-1 / 65.2 % top-3 with the
      templates, and 38.4 / 58.8 with the fitted model. With **Brick-class labels used as names
      (an upper bound, not real-world naming)**, adding the data moves top-1 from 93.0 to 95.2 %.
    - **Synthetic vendor-style names** (`messy_names.py`, five seeded styles, labelled
      synthetic): 25-88 % top-1 from the name alone, 52-91 % with the data.
    - Results and caveats in docs/MAPPING-ASSIST.md and docs/VALIDATION.md.
  - Opt-in: without `use_timeseries=True` the suggestions are byte-identical to 0.95.
<!-- /096-bts -->

### Fixed
<!-- 096-faults (#76) -->
- **A site's fault run no longer resolves other sites' faults (#76).** In the legacy, site-keyed
  path (no `facility_id`), `FaultLifecycle.update(..., auto_resolve_absent=True)` resolved every
  open fault in the store file, including other sites' faults and facility-keyed faults. The
  `absent` list had the same error without `auto_resolve_absent`.
  - A run now covers only the records whose fingerprint is keyed by its `site`. The fingerprint
    decides, not the stored label. `site=""` is its own scope, and `aliases` never widen it.
    Facility-id runs are unchanged.
  - A site-keyed record whose site cannot be told is never auto-resolved. This covers records
    whose fingerprint matches neither the run's `site` nor their own stored label, such as
    hand-edited records or records with no stored `site`, which now load with `""` instead of
    failing. Such records are listed under a new `unscoped` key in the result, present only when
    non-empty, and `camber run` prints a line for them.
  - The same fix reaches config runs that share a `faults.store`.
  - The in-memory `rules.triage.FaultRegister` shared the flaw across sites and facilities, and
    now resolves only faults keyed like the run.
  - Outputs for single-site stores and facility-id runs are byte-identical. See
    docs/FAULT-LIFECYCLE.md, "Which faults a run can close".
<!-- /096-faults -->
<!-- 096-report (#78) -->
- **Recommended actions follow the cause, not the rule (#78).** A `dcv_verification` finding
  whose DCV works but whose outdoor air stays above its floor at low demand was told to
  "Enable / repair demand-controlled ventilation"; it is now told to **lower the minimum outdoor
  air at low demand** (to the area-based Ra·Az floor). Under-ventilation causes get "restore the
  OA floor", "make OA respond to high CO₂" or "restore ventilation while occupied", never "cuts
  over-ventilation". `dcv_system_verification` reads the cause from the air handler that set its
  severity. An audit of every recommender fixed the same pattern in `chw_plant_reset` (low loop
  ΔT vs a flat CHWST), `chw_pump_dp_reset` / `hw_pump_dp_reset` (pinned at the VFD minimum, or a
  reset already present), `supply_air_reset` (a setpoint that already resets; SAT rising with
  load), `cooling_tower_approach` (wide at full fan), `reheat_minimization_g36` (the dual-maximum
  sequence) and the overcooling rules (no "raise the cooling setpoint" for a box at minimum).
  Finding severities and metrics are unchanged; only the advisory text and target change.
- **Audit report title and scope (#78).** A report built from trend data alone (a dataset, one
  room) was titled "ASHRAE Std-211 Level 2 Audit". It is now a **"Building analytics report"**
  unless it carries the Std-211 inputs (an EUI benchmark, plus an ECM table at Level 2 and
  above); `report.title` sets a title and `report.ecms` supplies the ECM table from a config.
  Empty sections (the ECM table) are left out, in HTML and text. The report carries minimal
  scoped styling (no external asset; light and dark), wide tables scroll inside the page at phone
  width, and `camber report`, the config's `out_html` and the lab write a full HTML document
  (charset, viewport, title).
- **Trend viewer (`/ui`) (#78).** Series of different scales shared one unlabelled axis, so CO₂ in
  ppm flattened temperature and airflow. The viewer now draws one panel per unit with a labelled y
  axis, a shared time axis (UTC), a legend with each series' unit and range, a hover readout,
  line breaks at data gaps and a **Normalised (0–1)** toggle; one request per ticked role. Units
  come from `camber.api.ui.role_units()` (IP, as stored). Still vanilla JS, same CSP, no CDN.
- **Lab page (#78).** The header shows the workspace, store and cache home-relative (`~/…`) and
  shortened; full paths stay in the startup log and the JSON (new `display` block). Each
  dataset's *what it teaches* list is collapsed by default.
<!-- /096-report -->
<!-- 096-integration -->
- **Trend viewer: site time and count units.** The time axis and the hover readout show the
  site's wall clock labelled with its zone (e.g. `time (Australia/Sydney)`) when the facility's
  zone is recorded, with a **UTC** box to convert; without a zone they read UTC as before. The
  zone reaches the page as a new `timezone` key on `/facilities` rows, present only when known (a
  registry `timezone`, or the zone a catalog dataset was ingested into). `occupancy` now reads
  persons instead of "no unit", the G36 request roles requests, and stage roles stage; the
  humidity, filter pressure drop, pump head and source-loop roles get their documented units. The
  mapping suggester's unit table (`ROLE_UNIT`) is unchanged.
<!-- /096-integration -->

### Unchanged
<!-- 096-lab (#77) -->
- `camber serve` stays GET-only (regression test), and default outputs are unchanged.
<!-- /096-lab -->

## [0.95.0] — 2026-10-03

<!-- 0.95 is stacked on 0.94, 0.93 and 0.92 (unreleased, below). This entry gets its date when
0.95 is released. -->

**0.95: bill-based M&V follow-ups (#74) and the portfolio lifecycle from offboarding to the edge
(#18 steps 3–5).** A billing meter's rebaseline window is now searched, as a daily meter's is, so
`camber mv rebaseline` no longer needs `--period` for bills; an opt-in step test built for bills
gives trigger T1 a calibrated false-alarm rate; and the degree-day model at bases selected from the
bills is a candidate in the SEP method proposal. A facility now leaves a portfolio in audited,
reversible steps (offboard, archive, restore, purge), and nothing is deleted without a verified
export bundle. The agreed retention defaults are enforced by `camber retention apply`, which rolls
raw data up, verifies the rollup, and only then prunes; the store writes month partitions so
retention works month by month. At the edge, what lands in the cloud follows the facility
registry: uploads from a facility that has left are quarantined instead of stored, a device can
be retired without losing data, and the bucket's own lifecycle rules are generated from the
retention policy.

### Added
- **Rebaseline windows of whole bills (#74).** `camber mv propose` answers a rebaseline-class
  trigger on a billing meter with a window, from `camber.mandv.billwindow.new_bill_window`
  (provisional).
  - Candidates are the shortest runs of whole bills covering a full service year
    (`min_baseline_days`), ending at each bill from the latest backwards.
  - A window starts `settle_days` after the trigger, avoids ECM installation windows and declared
    events, holds `min_bills`, and leaves at most `max_missing_frac` of its days unserved (the
    freeze rule). Its model must be valid under `require_validity` and must not be a `severe`
    extrapolation of the bills seen. `auto` bases are selected on each candidate.
  - Ranking is the daily path's: the latest qualifying window wins. At most 12 fits, stepping
    back one bill after a failure. The proposal states the ranking. Its `window` adds `n_bills`,
    `bases`, `ranking` and `tried`.
  - `camber mv rebaseline` without `--period` uses the search. `--from-proposal` freezes the
    proposed model exactly (statistics weighted by days, at the monthly G14 thresholds).
    `--period` still works.
- **A step test on bills, opt-in (#74).** `"rebaseline": {"bill_steps": "scan"}` on a billing
  entry makes trigger T1 a scan of the two-sample t of the bills' deviation from the frozen
  projection (`camber.mandv.billsteps`, provisional). Each bill is weighted by its projected
  energy, at least 6 bills are needed each side, and the variance is inflated by the
  bias-corrected lag-1 ρ. The series is cut at ECM installation windows and declared events.
  - The threshold 3.75 is calibrated by simulation to a **5% false-alarm rate per meter over 36
    bills**, looked at after every bill (4.8% pooled; 1 to 13% by cell). The simulation used an
    office and a heating-only gas meter, bills of 28 to 35 days, baselines of 12 or 24 bills, and
    a monthly CV(RMSE) of 3 to 13%, with 300 seeds a cell.
  - Detection: 20% steps 61 to 100%, 10% steps 21 to 99%, 5% steps 3 to 62%, with a median delay
    of 5 to 10 bills.
  - The daily PELT on bills, for comparison, raised false alarms on 13 to 22% of meters with 6
    bills a segment, and on 49 to 62% with 3. The other candidates were two CUSUM forms.
  - With strongly persistent residuals the false-alarm rate rose to 22%. Declared events remain
    the recommended path, and the docs say so.
- **The degree-day model in `method: "auto"` (#74).** With `base_f: "auto"`, `select_method`
  offers the degree-day model at the selected bases in every period (the same bases throughout,
  counted in `p`).
  - The criterion is unchanged: SEP validity (§6.4.1), then adjusted R² with every fitted
    parameter counted.
  - The `mv_method_proposal` finding states it (`model_criterion`, a caveat) and names the
    candidate (`degree_day_candidate`).
  - Standard conditions pair the baseline model with a reporting model of the same form.
  - The adjusted sensitivity rows follow the same models.
  - `select_method(degree_day=False)` leaves it out.
- **Validation (#74).** `tests/test_mv_billing_followups.py` covers:
  - the window search's rules (settle days, ECM windows, events, `min_bills`, gaps, stepping
    back) and the propose / rebaseline paths;
  - the scan's calibration on independent noise, its detection and dating of a 20% step, the
    opt-in, and ECMs and declared events not detected again;
  - the degree-day candidate in the proposal.
- **`camber facility offboard | archive | restore | purge`** (and `Portfolio.offboard`,
  `archive`, `restore`, `purge`; provisional).
  - `offboard` writes a verified export bundle, then starts a 30-day reversible grace period.
  - `archive`, after the grace period (or with `--skip-grace`, audited), deletes the hot data:
    store and rollup partitions, `state/<fid>/`, and unchanged external report files. It keeps
    the bundle, re-exporting first if anything changed during the grace period.
  - `restore` brings an offboarding or archived facility back; from a bundle it re-verifies every
    checksum after the copy.
  - `purge` deletes everything but the tombstone and the audit record; the id is never reused.
  - Each is a dry run unless `--apply`, needs `--reason` and a confirmation (`--yes` or the typed
    id; `purge` only the typed id), takes the portfolio lock and is audited with the OS user. A
    legal hold blocks `archive` and `purge`.
- **Export bundles** under `archive/<fid>/<bundle_id>/`: raw partitions, rollups, the whole
  `state/<fid>/` (fault history, drift baselines, M&V and bill-based M&V baselines, the #73
  weather audit log, reports, the manifest), external artifacts, the registry entry, catalog keys
  and retention override, with a sha256 manifest and a checksum of the manifest itself.
  `camber facility export` makes one on demand; `camber facility bundles --verify` re-hashes
  them.
- **Crash safety.** Trees are replaced through a fsynced `_swap-*` stage and deleted through one
  atomic rename to `_trash-*`. Every lifecycle command first finishes or rolls back whatever a
  crash left (`Portfolio.recover`, audited as `portfolio.recover`). Tests cover a crash at each
  step, including a killed child process.

- **`camber retention show | set | override | hold | release | apply`** (and
  `Portfolio.set_retention`, `set_retention_override`, `hold`, `release_hold`,
  `apply_retention`, `retention_policy`; provisional). Defaults: raw trends 25 months, hourly
  rollups 7 years, daily rollups indefinite, findings 7 years, drift baselines for the life of
  the equipment with the last 10 versions, M&V (and bill-based M&V) baselines every version,
  reports the last 12 per facility, the weather audit log while the facility exists, the audit
  log never. Precedence: legal hold > facility override > portfolio default.
  - `apply` rolls expired raw month partitions up into `rollups/hourly/` and `rollups/daily/`
    (mean and count per bucket), verifies that the counts add up to the raw rows, and only then
    prunes. It also trims closed faults, drift baseline history and old reports, and archives
    offboarding facilities whose grace period has ended.
  - It is a dry run unless `--apply --reason R --yes`, takes the lock (`--wait S`; exit 75 when
    held), recovers interrupted work first, audits each facility before acting, and is
    idempotent, so it is safe from cron.
- **The policy as a documented JSON document** (`camber retention show --json`,
  `Portfolio.retention_policy()`), described by the JSON Schema
  `camber.portfolio.RETENTION_SCHEMA`: each class's storage location, the effective rule per
  facility with its source, a conservative `min_age_days` for object-store lifecycle rules, and
  the legal holds.
- **`camber store migrate-partitions`** (`ParquetStore.migrate_partitions`): converts year-only
  partitions to `year=/month=`, a dry run unless `--apply --yes`, crash-safe and idempotent.
  `ParquetStore.partitions()` and `drop_partition()` list and delete single partitions.
- **Central reconciliation (#18).** `camber edge reconcile` (provisional,
  `camber.edge.landing`) classifies landed objects against the registry as `ok`, `orphaned`,
  `unknown_facility`, `unregistered`, `inactive` or `quarantined`. It reads the workspace store, a
  local landing directory, or a key listing exported from S3, GCS or Azure. It is read-only by
  default and never calls a cloud API. In the store, an inactive facility's objects that landed
  before its state change are history and are only reported.
- **Quarantine (#18).** Uploads for a facility that is `suspended`, `offboarding`, `archived`,
  `purged` or unknown, and objects whose content fails the hash in their name, go to
  `<workspace>/quarantine/` with a record of why, not into the store. The routes are
  `camber edge land <inbox>`, `camber edge reconcile --apply`, or `route_key()` for a
  presigned-URL broker, which routes to the bucket's `_quarantine/` prefix.
  `camber edge quarantine list | release | discard` are dry runs by default. They take the lock
  and are audited with a reason. `discard` needs `--yes` or the typed facility id, and a legal
  hold refuses it.
- **Edge decommissioning (#18).** `camber edge decommission` flushes the spool, waits for the
  landing to acknowledge every batch, then retires the device. The spool refuses new batches from
  then on. The retirement is recorded as an audit line and an `edge_devices.<device_id>` note on
  the facility's registry entry, directly or later with `camber edge record-retirement`. It
  refuses while data is unacknowledged unless `--force` is given with a reason. A forced
  retirement keeps the payloads on disk, and a legal hold refuses it.
- **Spool journal compaction (#18).** `camber edge compact` / `Spool.compact()` rewrite the
  append-only journal to the pending batches. The rewrite is verified before an atomic swap, so a
  crash never drops an unacknowledged batch, and sequence numbers are never reused.
- **Bucket lifecycle rules (#18).** `camber edge bucket-rules --provider s3|gcs|azure`
  (`camber.edge.bucket_rules`) emits lifecycle JSON from a retention-policy dict or the
  workspace's policy document (`Portfolio.retention_policy()`), whose `location` patterns give
  the prefixes (`rollups/hourly/`, `rollups/daily/`; `--layout store|workspace`). Ages are
  conservative, and facility overrides and legal holds produce per-facility rules; a held
  facility gets no expiry rule. It is text only: the admin applies the rules.
- `EdgeConfig.device_id` (config `device_id`, env `CAMBER_EDGE_DEVICE_ID`).
- **`Portfolio.audit` and `Portfolio.note_edge_device`** (provisional): the public, audited way
  for code outside the lifecycle (the edge landing) to write an audit record or an
  `edge_devices.<device_id>` registry note. `audit` refuses the lifecycle's own namespaces
  (`facility.`, `portfolio.`, `retention.`); the note is audited before the registry changes.

### Changed
- **Bill-based M&V (#74): nothing changes for existing configs.** A byte-identity harness
  compared the findings of billing entries (numeric and `auto` bases, every method, adjustments,
  versioned runs, `mv report`) and of a daily workspace (`propose`, dry-run `rebaseline`, `report`) before and after: they were
  identical. The exceptions are the intended ones:
  - a billing meter's `propose` / `rebaseline` with a rebaseline-class trigger (a window instead
    of a decline);
  - `method: "auto"` on a billing entry with selected bases: the proposal gains the degree-day
    candidate, and the 0.94 caveat "ranks the change-point models only" is gone.

  `RebaselinePolicy.as_dict()`, stored in rebaseline provenance, carries `bill_steps` only when
  it is set.
- **`ParquetStore` writes `year=/month=` partitions** (was `year=`). Year-only and mixed stores
  are read unchanged, and range reads also skip month directories. A full `read_long` now
  returns a `month` column.
- An **archived** facility refuses store writes (its data lives in its bundle).
- `ParquetStore.read_long` on a store with no partitions left returns an empty frame instead of
  raising.
- `ParquetStore.prune` and `drop_facility` delete through one atomic rename, then removal, so a
  crash never leaves a half-deleted partition visible.
- Spool journal writes now take the spool's single-writer lock (`<spool>/_lock`), and an append
  after a torn last line starts a fresh line. Before, the next record could be glued onto the
  torn line and lost with it. Spool contents and forwarding are otherwise unchanged.
- `camber edge status` adds a `RETIRED` line for a decommissioned device. `edge run` and
  `send-once` refuse a retired spool, and the forwarder daemon stops on one.
- **The edge forwarder writes `year=/month=` keys**
  (`facility_id=<id>/year=<yyyy>/month=<m>/part-<sha16>.parquet`, one part per month), matching
  the store's layout, so retention prunes month by month without splitting an edge part. The
  batch manifest gains `month`. Year-only keys from older forwarders are still accepted and read,
  and `camber store migrate-partitions` converts them. A year-only part re-sent after its year
  was migrated is recognised by name and sha256 and quarantined as a `duplicate` (a new
  reconciliation category) instead of being stored twice; `release` refuses it.

### Fixed
- **A write after `ParquetStore.prune` could overwrite live data.** The part-file counter was the
  number of files left, so after a prune a new write could reuse the name of an existing file in
  the same partition and replace it. The counter is now one past the highest part number on
  disk.
- The store read caches (the per-facility fragment index and the resolve frame cache) now notice
  writes into month directories.
- **An archived facility accepted store writes again once an edge object recreated its
  partition.** The archived check ran after the "partition exists" fast path, so an upload PUT
  straight into a store-as-bucket reopened the facility. It now runs first.
- **A late upload into an already rolled-up month replaced that month's rollup.** Retention
  replaced the whole rollup partition with the rollup of whatever raw rows were left, so raw
  rows landing in a month after it was rolled up and pruned (an edge backlog, a backfill; likely
  with a short `raw_trends` override) wiped the month's earlier rollup. Each rollup part now
  records the raw files it covers: a run replaces only the parts whose raw files are all still
  there and keeps the rest, so re-runs never double-count and late rows add to the month.
- **Purge left a facility's quarantined edge uploads behind.** Purge now deletes
  `quarantine/facility_id=<id>/` with the rest (crash-safe, finished by `Portfolio.recover`, which
  also sweeps `quarantine/`). Archive keeps them: they are not in the bundle.
- **Migrating a year a second time could destroy rows migrated the first time.** Year-only files
  that land after a migration (an older edge forwarder) are migrated again. The second run reused
  the first run's `part-legacy0-0` name and wrote through the stage's hard link to that file,
  truncating it, then refused with "nothing was changed". Migrated parts are now named by the
  source file's content and written to a temporary name first. Each migrated year also records
  its source files and their sha256 in `year=Y/_migrated.json`
  (`ParquetStore.migrated_files`), so the edge landing recognises a re-sent legacy upload.

## [0.94.0] — 2026-10-03

<!-- 0.94 is stacked on 0.93 and 0.92 (both unreleased, below). This entry gets its date when 0.94
is released. -->

**0.94: weather for non-public sites, with privacy guardrails (#73).** M&V and FDD can use real
weather for client sites without disclosing where they are. A privacy mode per config or per
fetch decides what leaves the machine: `coarse` sends only ISD station ids, NASA POWER grid-cell
centres and Open-Meteo points rounded to 0.1°; `offline` sends nothing. A facility marked
private defaults to `offline`. Every request to a weather or price service is audited, and
`camber weather audit` shows exactly what was sent.

**Bill-based M&V (#72).** Pre/post M&V from utility bills alone: versioned billing baselines
through `camber mv freeze | rebaseline | adjust | report`, Portfolio Manager calendarization,
billed cost and avoided cost, and degree-day bases chosen from the bills (`base_f: "auto"`, with
separate heating and cooling bases, degree days built from each day, a selection profile with
ranges and a flat-profile warning, and R² / adjusted R² beside CV(RMSE) and NMBE).

### Added
- **Weather privacy modes (#73).** `camber.weather_privacy` (provisional) adds the modes
  `"public"` (the behaviour before 0.94, still the default), `"coarse"` and `"offline"`. Set them
  as a config's `"weather": {"privacy": ..., "precision_deg": 0.1}`, or as a `weather` block in an
  `mv` entry's `oat` or in `report.rcx.oat_reference`. `coarsen()` is the one function that turns
  a location into request coordinates, and the URL builders call it:
  - ISD requests never carry coordinates, because the station is chosen locally from the
    downloaded catalogue;
  - NASA POWER gets its grid-cell centre (0.5° × 0.625°, MERRA-2's native grid, per the POWER
    data-sources page);
  - Open-Meteo gets the point rounded to `precision_deg` (default 0.1°, about 11 km; no finer
    than 0.05°).

  Independently, `check_url()` checks every request at send time. It refuses a coordinate finer
  than the policy, any coordinate in an ISD request, a field the service does not need, and, under
  `offline`, any request at all (`PrivacyViolation`). Geocoding, which sends an address, is
  refused under both guarded modes.
- **Private facilities.** `private: true` in a facility's registry entry (`camber facility add
  --private`, or `camber facility private <id> --reason R`, audited) or in its config makes its
  weather and price requests default to `offline`. It may opt in to `coarse`, never to `public`.
  Offline reads only caches and user-supplied weather files. A billing entry with nothing cached
  declines and says how to supply a file.
- **Places without coordinates.** A fetch may name `"place": "KORD"` (an airport ICAO code, from
  the ISD catalogue's new `IsdStation.icao`), an ISD station id, or one of about 80 bundled cities
  (`"Chicago, IL"`). All are resolved locally (`weather_source.resolve_place`).
- **Audit log.** Each outbound request to NOAA ISD, NASA POWER, Open-Meteo, EIA or OpenEI URDB is
  appended to `state/<facility_id>/weather_audit.ndjson` in a portfolio workspace, or to
  `weather_audit.ndjson` next to the cache. Cache hits are logged too. Each record holds the
  timestamp, the service, the URL as sent (API keys redacted), the purpose, the cache hit or miss,
  whether it was sent and the privacy mode. The facility id appears only in the local record. `camber
  weather audit [--facility] [--since] [--json]` prints the log.
- **Provenance.** A guarded fetch records the mode, the precision, the policy's origin, the
  coarsening applied to each source, and the requests sent and served from the cache. They go in
  `attrs["weather_privacy"]` (and `weather_provenance["privacy"]`), next to the existing station,
  cell and bias-correction records.
- **EIA and URDB** requests (`fetch_state_price`, `fetch_urdb_rate`, and the billing unit-scale
  check) take the same `privacy=` / `audit=`. URDB under `offline` is refused with a pointer to
  `urdb_file`.
- Docs: a SECURITY.md section on what CAMBER sends to weather and price services and what it
  never sends; privacy modes in WEATHER.md; `camber weather audit` in CLI.md.
- **Degree-day bases chosen from the bills (#72).** `"base_f": "auto"` on a billing `mv` entry
  searches a heating base and a cooling base separately. The search lives in
  `camber.mandv.basetemp` (provisional: `select_bases`, `BaseSearch`, `BillingDegreeDayModel`,
  `bill_degree_days`, `fit_bill_degree_day`, `compare_models`).
  - Candidate degree days come from each day's temperatures (hourly when sub-daily) summed over
    each bill's service days, not from the bill's mean temperature.
  - `DD-H`, `DD-C` and `DD-HC` are fitted by days-weighted least squares, with the bases counted
    as parameters (`p` = 3 or 5).
  - The degree-day model at the selected bases competes with the change-point models by BIC.
  - A slope of the wrong sign is refused.
  - `base_search` sets the ranges and step (or `grid: "data"`), the likelihood-ratio
    `tolerance` of a base's range (3.84), `flat_share`, `fixed_f` (65), `kinds` and `r2_min`.

  The `mv_baseline` finding carries:
  - `base_selection`: a profile row per candidate base with SSE, R², adjusted R², CV(RMSE),
    NMBE and BIC, plus each base's range and `flat` / `at_edge` caveats;
  - `heating_base_f` / `cooling_base_f`, `adj_r2` and `n_params`;
  - `model_comparison`: every change-point kind, the fitted-base model and the fixed-65 °F model,
    each with R², adjusted R², CV(RMSE), NMBE, BIC, and `mismatch` where R² and CV(RMSE) / NMBE
    disagree;
  - `fixed_base`.

  One set of bases feeds `hdd_total` / `cdd_total`, the reporting rows, the standard-conditions
  projection and every refit. An R² under 0.50 is a caveat, not a refusal, under `validity:
  "g14"`. 0.50 is the SEP 50001 M&V Protocol 2019 Ed. 2 §6.4.1 threshold, verified against the
  public DOE document.
- **Calendarization (#72).** `BillingSeries.calendarize()` / `mandv.billing.calendarize()`
  (provisional) prorate each bill's energy and cost per day into calendar months, the ENERGY STAR
  Portfolio Manager method (Technical Reference, *Thermal Energy Conversions*, Figure 1 step 3).
  - Months are flagged complete or estimated, and carry HDD / CDD from the same daily series.
  - Gaps and overlaps are listed. Beyond `max_gap_days` (0) the totals are withheld, as Portfolio
    Manager withholds metrics.
  - `annual()` and `total()` give calendar totals.
  - A config entry's `"calendarize": true` adds `calendarized` to its `mv_baseline` finding.
  - Models are still fitted on the billing periods.
- **Billed and avoided cost (#72).** `bills.cost` names the cost column (`BillingSeries` carries
  it as `frame["cost"]`, and merged estimated reads sum it).
  - The baseline finding reports `billed_cost` and `unit_cost` (the implied $/unit per bill, the
    quantity #71's scale check screens).
  - `"avoided_cost": "bills"` prices a forecast saving at each reporting bill's own rate;
    `{"rate": r}` uses a stated rate.
- **Versioned billing baselines (#72).** `camber mv freeze | rebaseline | adjust | propose | report
  | list | run` handle billing entries, keyed `(facility, name, "mv_bills")`, with the #48
  reason, lock and audit rules.
  - The frozen record stores the model (including `BillingDegreeDayModel`), the bills (start, end,
    days, energy, estimated, cost), the unit, the weather basis and the bases with their selection
    profile.
  - The run path and `mv report` measure against the in-force version at its own bases.
  - A rebaseline names its window (`--period`) and, under `auto`, selects the bases afresh. A
    change of bases is recorded as `bases_changed`.
  - `mv report` shows each version's bases, the avoided cost per link and the calendarized months.
- **Validation (#72).** `tests/test_mv_billing_bases.py` covers:
  - synthetic buildings with heating / cooling bases of 58 / 68 °F, recovered within one 1 °F
    step. A scratch run over 40 seeds recovered them in 40 at low noise and 37 at moderate noise,
    with the truth inside both ranges in 38;
  - a flat profile, which is flagged;
  - degree days built from each day against those from the bill mean on shoulder-month bills
    (per-day RMSE about 1.7 against 5.0, better on 40 of 40 seeds);
  - bills of 28 to 35 days from a mid-month start with an estimated read, and a known 12% saving.

  `examples/bdg2/billing_agreement.py` (not gated) re-expresses the BDG2 daily meters as 28 to 35
  day bills with a 10% injected saving. On electricity meters whose daily model has CV(RMSE) ≤ 30%
  (886), the billing saving (`auto`) was within 1 / 2 / 5 percentage points of the daily path's
  on 67 / 85 / 98% of meters. The median difference was 0.0 points, and the saving was inside the
  daily 90% band on 97%. On chilled water (172 meters) the figures were 58 / 82 / 98%, a median of
  0.1 points and 98%. The mean differences are dominated by a few meters whose projection is near
  zero, so medians and shares are the figures to read.

### Changed
- Nothing by default. With no `weather` block and no private flag, every request is the same URL
  as before, and every report and finding is byte-identical. The only new file is the audit log,
  which a configured weather fetch writes into its workspace state or next to its cache.
- The RCx report's `oat_reference` with `"fetch": "nasa_power"` goes through
  `oat_reference_auto` (POWER alone, as before) when a privacy policy or an audit log applies, so
  it honours `cache_dir` / `offline` there.
- Billing entries (#72): with a numeric `base_f` (or none), and no new keys, every finding is
  byte-identical. Additions:
  - a stored `mv_bills` version is now used by the run path. Before, a same-named stored version
    was ignored with a caveat;
  - `fit_frame_sha256` also hashes `days`, `hdd` and `cdd` for a bills frame, while daily frames
    hash as before;
  - `BillingSeries.energy_vs_temp` takes `heating_base_f` / `cooling_base_f` and records its bases
    in `attrs`;
  - `mvrun.MeterSeries` gains `bills` / `oat` / `oat_source`;
  - `MeterChain` gains `billing`.

## [0.93.0] — 2026-10-03

<!-- 0.93 is stacked on 0.92, whose entry is the "## Unreleased" block below. The 0.92 release
stamps that block; this one gets its date when 0.93 is released. -->

**0.93: hardening from real data, and the refrigerant cluster (#6, #37-#44, #68, #70).** Real-data
fixes to setback, leaking valves, simultaneous heating and cooling (dehumidification with
reheat), DCV verification and CO2 over-ventilation (economizer hours); a VAV rule for a zone the
box cannot heat; daylight-saving day lengths, holiday calendars and a break-day driver for M&V,
and the energy-units follow-ups; an R-410A (and R-134a, R-22, R-32, CO2) saturation curve that
turns refrigerant pressures and line temperatures into subcooling, superheat and approach; DX /
heat-pump charge and indoor-airflow rules scored on the NIST heat-pump FDD data; the
discharge-superheat drift detector deferred in #6; and operating-mode, capacity, same-room and
source-loop rules for water-source heat pumps trended with three points.

### Added
- **A VAV rule for a zone the box cannot heat (#44).** `rules.reheat_capacity_rule.
  ReheatCapacityShortfall` (`reheat_capacity_shortfall`, built-in, terminal boxes only) flags a
  zone more than 1.5 F below its heating setpoint while its reheat valve is at or above 90 %: the
  box has run out of heating, so it is a capacity or airflow problem, not a tuning one. It judges
  occupied samples, leaving out morning recovery, `warmup` and fan-off samples. It warns at 5 %
  and faults at 20 % of them, once 10 hours have accumulated (screening-grade). The setpoint comes
  from `heat_sp`, else from the config's `heat_sp_f` (one value, or `{equip: degF}`); without
  either the rule declines. With `airflow` and `airflow_sp` the finding says whether the box was
  short of air or of heat; the summary also reports the under-heated time with reheat to spare. On
  `lbnl-b59`, zone 051 faults (35.8 % of occupied hours, median 2.9 F below its setpoint) and
  nine more terminals warn. The rule is in that dataset's run template, and its `faultlab` scenario
  is a gated synthetic key (see Benchmarks).
- **Coil leaving-air roles (#41, #42).** `Role.HEAT_COIL_LEAVING_TEMP` /
  `Role.COOL_COIL_LEAVING_TEMP` (`heat_coil_leaving_temp`, `cool_coil_leaving_temp`): the air
  straight after an air handler's own heating / cooling coil (G36's HCLT / CCLT), upstream of a
  draw-through supply fan and, for the cooling coil, of any post-heat coil. One definition each,
  shared by `leaking_valve` (#42) and `simultaneous_heat_cool` (#41), with sensor-health bounds,
  flatline and fan-gated trust, the AHU equipment template and 223P hints. The `irish-ahu` mapping
  maps `HCALTemp` / `CCALTemp` to them (their 0.00 C outage readings are blanked like the other
  temperatures); the `nuig-ahu101` mapping maps TE_101_3 to the cooling-coil one (the dataset
  needs a re-ingest to pick it up).
- **`co2_ventilation_system`** (`rules.iaq_rule.CO2VentilationSystem`), a fleet rule, built in:
  each CO2 zone is joined to its serving air handler (served-by topology, else the naming
  heuristic; one economizing unit with no grouping takes every zone) and judged by
  `co2_ventilation` with that unit's economizer-mode hours left out (#38). One finding, a
  `per_zone` breakdown; category `ventilation`; roles-only applicability.
- **`iaq.economizer_mode_mask`** (provisional): where an economizer brings in outdoor air beyond
  the ventilation minimum -- a trended economizer command, else the OAT below the high limit
  (75 F) with the OA damper more than 5 points above its minimum or the unit on ~100 % outside air
  (`freecooling.integrated_economizer_mask`). An OAT alone excludes nothing (#38).
- `DemandControlledVentilation` / `DcvSystemVerification` parameters `full_outdoor_air` and
  `stratify_hour`; `assess_dcv(stratify_hour=True)`; `DcvResult.lift_basis` and
  `demand_lift_pooled`; `analyze_ahu(simul_classes=...)` and `AHUResult.simul_class_pct`;
  `SimultaneousHeatCool` parameters `dehumidification`, `reheat_lift_f`, `dewpoint_margin_f`,
  `humid_rh_pct`, `fault_pct`, `warn_pct`; `CO2Ventilation` parameters `exclude_economizer`,
  `oa_damper_min_pct`, `econ_high_limit_f`; `CO2VentilationResult.econ_hours_pct`,
  `over_vent_econ_pct` and `over_vent_all_pct`.

- **Daylight-saving day lengths in daily M&V (#68).** With the site's zone known
  (`source.timezone`, or a catalog store's `local_timezone`), a daily `mv` day is as long as its
  clock: the autumn fall-back day sums 25 hours of energy (the repeated hour, which a naive index
  holds once, counts for both passes) and weights that hour twice in its mean temperature; the
  spring-forward day has 23. `mandv.intervalfit.daily_energy_vs_temp` and `rate_to_energy` take
  `timezone=`; `repeated_hour_weights` is new. Only fall-back days change, and nothing changes
  without a zone. On `cofactor-drammen` the fall-back days now match the publisher's
  fixed-offset data exactly (they were 3-5 % short).
- **Holiday calendars for the occupied-day driver (#68).** `mv[].holiday_calendar` takes a
  country code for bundled public holidays, `"US"` (federal, observed, 2011-2030), `"NO"` (Norway,
  2000-2040) or `"ES-<community>"` (Spain per autonomous community, 2016-2026). It can also take
  `{"country", "subdivision", "files", "dates"}` to add calendar CSV files (`date`, or
  `start`/`end` ranges) and single dates. Each bundled file cites its sources (5 U.S.C. 6103 /
  E.O. 11582 checked against OPM; the Norwegian statutes; the annual BOE resolutions), and
  `scripts/calendars_refresh.py` rebuilds them. The new module `camber.calendars`
  (`HolidayCalendar`, `public_holidays`, `load_calendar_csv`, `register_calendar` for any other
  source, e.g. the `holidays` package, which CAMBER does not depend on) serves them. A day outside
  a calendar's coverage is left out, never treated as holiday-free. The new driver `"break_day"`
  with `mv[].break_calendar` gives school breaks their own coefficient. As holidays they made the
  COFACTOR school models worse; as break days 15 of 16 schools met daily G14 acceptance (13
  without). The `cofactor-drammen` template now uses `"holiday_calendar": "NO"`, with identical
  results.
- **Energy-units follow-ups (#70).** The chain report `camber mv report` follows
  `units.system`: its page, JSON (`units` per meter) and CUSUM give energy in kBtu or kWh
  (`MeterChain.units`). Trended gas meters measured as a volume flow (`"units": "cfh"`, `CCF/h`,
  `Mcf/h`, `m3/h`) convert with `mv[].heat_content` or a `units.factor_set`
  (`mv[].meter_type`), with no default (`energy_units.VOLUME_FLOW_UNITS`,
  `quantity_of_rate`). The fleet report takes `eui_unit=` / `units=` and labels its EUIs
  (`FleetReport.eui_unit`), `camber fleet` passes the configs' shared system, and the agent
  context's fleet facts name the unit. Carbon factors may be given per any unit
  (`{"rate", "per"}`), converted through `convert_rate` (`carbon.factor_per`). In a bills file,
  spellings of one unit (`kWh` / `kwh`) are one unit, not mixed units. A greenhouse-gas factor
  set (eGRID / EIA) is documented as a follow-up. Every default output is unchanged.

- **Refrigerant properties (#39).** `camber.refrigerant`: `saturation_temp` / `saturation_pressure`
  (bubble and dew) for R-410A (Lemmon 2003), R-744 (Span & Wagner 1996), R-134a, R-22 and R-32
  (CoolProp's published ancillary fits to the reference equations of state; credited in NOTICE),
  gauge or absolute, psi / kPa / bar / MPa, degF / degC / K. No new dependency: the Wagner-form
  correlations are evaluated directly. R-410A matches the NIST REFPROP saturation temperatures
  published in the NIST heat-pump data within 0.011 degF (bubble) and 0.007 degF (dew); CoolProp
  is an optional cross-check in the tests only. Transforms `subcooling`, `superheat`,
  `discharge_superheat`, `condenser_approach`, `evaporator_approach`; NaN (a decline) above the
  critical pressure -- a transcritical CO2 gas cooler -- below the valid range or below vacuum;
  `is_supercritical`. See docs/REFRIGERANT.md.
- **Derived refrigerant roles.** New roles `liquid_line_temp`, `suction_line_temp`,
  `discharge_line_temp`, `liquid_line_pressure`, `discharge_superheat_temp`. A config equipment
  entry's `"refrigerant": "R-410A"` (or `EquipRef` / `StoreEquipRef.refrigerant`) makes `resolve`
  derive subcooling, superheat, discharge superheat and both approaches wherever a rule asks for
  them (`refrigerant.derive_refrigerant_roles`; a controller-reported value is kept).
- **DX / heat-pump refrigerant charge and indoor airflow (#40).** `dx_refrigerant_charge`
  (subcooling; superheat as corroboration, or `metric="superheat"` for a fixed orifice) and
  `dx_indoor_airflow` (evaporator temperature split matched on return air and the new
  `return_air_dewpoint_temp`, or return RH), built-in: manufacturer targets (`targets`, per
  equipment by glob), universal limits without one, or a frozen fault-free baseline at matched
  outdoor / return-air conditions (`analyze_periods`; the new `dx` drift family,
  `dxdrift.diagnose_dx_drift`). A narrowed split with lost subcooling is reported as a capacity
  (charge) symptom, not high airflow. On `nist-heatpump-fdd`, leave-one-file-out: charge TPR 91%
  [83-95] at 8% [4-15] of fault-free files; airflow 22% [14-31] at 2% [1-8] (18/44 at 15%+
  airflow reduction).
- **Discharge-superheat drift (#6).** `rules.dx_discharge_superheat_rule.DischargeSuperheatDrift`
  (`discharge_superheat_drift`, in the `dx` drift family): two-sided frozen-baseline drift with
  the family's CUSUM, normalized on OAT and return air (DX) or tons (a water-cooled chiller);
  discharge superheat is mapped or derived from a discharge pressure and discharge-line temperature.
  On the NIST data it catches 32% [23-41] of charge-fault files at 6% [2-12] of fault-free ones:
  the weaker signal on TXV units, as #6 anticipated.
- **Water-source heat pumps with three points (#40).** `rules.heatpump_ops_rule.infer_hp_mode`
  (mode from discharge air against the zone) and the built-in `hp_mode_vs_need`,
  `hp_capacity_shortfall` (capacity vs control verdict) and `hp_room_imbalance` (fleet: units in
  one room that fight or split the work unevenly). `source_loop_deltat`: a heat-pump loop pumped
  with next to no temperature difference. New roles `source_loop_supply_temp`,
  `source_loop_return_temp`, `source_loop_diff_press`, `source_loop_pump_speed`; new equipment
  families `dx` and `source_loop`.

### Changed
- **`night_weekend_setback` tells a fan holding the setback from a missing one (#43).** When the
  runtime test fails, a fan that cycles (mean duty below 90 % in the unoccupied hours it runs)
  while the zone sits at setback now reads "effective (fan cycling to hold)" (`ok`,
  `setback_basis="held_setback"`). The zone signal is `space_temp`, else the return air while the
  fan runs. The setback is judged against the trended `heat_sp` / `cool_sp` in unoccupied hours,
  else the new `unoccupied_heat_sp_f` / `unoccupied_cool_sp_f`, else (heating side only) a zone at
  least `min_setback_depth_f` (3 F) below its occupied temperature. A trended setpoint that never
  sets back vetoes the test. A fan that runs through every unoccupied hour is still "MISSING", and the
  5 % absolute runtime floor (#57) still decides first. With no zone signal, the runtime verdict
  now carries a caveat that it cannot tell the two apart. On `ornl-frp-ops`, the heating setback
  test (the fan cycles 46-85 % of each night hour to hold 15.6 C) moves from MISSING to effective.
  The baseline and pre-heat tests, which run the fan through the night by design, stay MISSING.
  The template now carries the descriptor's unoccupied setpoints.
- **`leaking_valve` allows for fan heat and prefers the coils' own sensors (#42).** New
  constructor parameters: `fan_heat_f` (the supply fan's temperature rise, default 2 F, G36's
  ΔT_SF, `fdd_g36.G36Thresholds.dT_sf`), `delta_thr_f`, `valve_closed_thr` and
  `coil_sensor_fan_heat`. Fan heat is now an allowance, not an offset: a heating leak must rise
  more than `fan_heat_f` + 3 F above the mixed air, while a cooling leak gets no credit for it
  (supply more than 3 F below the mixed air). Until 0.93 a fixed 1 F was subtracted on both sides.
  A mapped fan status or speed now limits the check to fan-on samples. A coil with its own
  leaving-air sensor is judged on it (against the mixed air) instead of the supply air downstream
  of the fan; set `coil_sensor_fan_heat=True` on a blow-through unit. New metrics: `fan_heat_f`,
  `fan_gated`, `hw_basis` / `chw_basis`, `hw_median_delta_f` / `chw_median_delta_f`. On
  `irish-ahu` the 34 % heating-leak signature before the 2022-05-01 valve replacement (0.2 %
  after) falls to 11 % on the heating coil's own leaving air (0 % after). The supply air sat
  2.6 F above the cooling coil's leaving air before the date, so most of the old signature was
  downstream of the coils. The whole record reads ok. The LBNL SDAHU benchmark runs keep their
  verdicts; the 10 % leak run is still missed.
- **`simultaneous_heat_cool` tells dehumidification with reheat from coil fighting (#41).** A
  both-open interval with the fan running, the cooling coil leaving at least 2 F below the supply
  air (the heat is added after the coil) and at or within 2 F of the entering dew point (outdoor
  and return dew points from temperature + RH, the lower of the two; else a return humidity of
  55 % or more) is dehumidification with reheat: reported (`dehum_reheat_pct`), not counted. A
  coil leaving above the entering dew point is dry, and the reheat after it still counts.
  Partial evidence (reheat after the coil but no humidity, or high humidity but no coil-leaving
  temperature) is `dehum_possible_pct`: a caveat that caps the finding at `warn`, never a fault.
  `dehumidification=true` declares the sequence (reheat after the coil suffices unless a dew point
  shows the coil dry); `false` counts every both-open interval as before. The severity is judged
  on `unexplained_hc_pct`. Units with none of these signals are judged exactly as before, with a
  caveat when they trip. On `nuig-ahu101` (13.3 % of occupied hours both open) 5.2 % now reads as
  dehumidification with reheat (June-July, the coil held at ~12 C below the outdoor dew point) and
  8.1 % stays unexplained (winter hours with the fan stopped, and summer hours with the coil held
  at ~12 C while the entering air is drier): still a fault, now for the part that is one.
- **DCV verification compares CO2 within the hour of day (#37).** `assess_dcv` takes the CO2 lift
  (CO2 when OA is raised minus CO2 at its floor) within each hour of day, weekdays and weekends
  apart, as it already did for occupancy, whenever enough same-hour pairs exist; else the pooled
  lift decides, with a caveat that a time clock cannot then be told from CO2 response. A valve
  that follows a clock and CO2 no longer reads "uncorrelated" because its clock-driven morning
  opening drags the pooled lift down: on `b4b-windesheim` room 917810 now reads "functioning" on
  both of its sensors (pooled: "uncorrelated" on one); room 999169 stays sensor-dependent.
  `finnish-dcv`'s DCV-law test still reads "functioning" (lift 332 ppm); its two training sets of
  undocumented mixed strategies both read "uncorrelated" (training 1 was "functioning" pooled).
- **DCV falls back to the OA damper where OA flow is missing, and to fan speed on a 100 %
  outdoor-air unit (#37).** The best OA signal judges the samples it covers and a lesser one the
  rest (`metrics["oa_segments"]`, each with its verdict and date span; worst severity wins). On
  `lbnl-b59` the months before the OA-flow record (Aug 2019 - Mar 2020) are now judged on the
  damper: "not judged -- demand never varied", like the flow period (no DCV; zone CO2 within ~150
  ppm of outdoor). `full_outdoor_air=true` adds the supply airflow, then the supply fan speed, as
  proxies with no economizer exclusion; the `nuig-ahu101` template now runs `dcv_verification`
  on the fan speed ("not judged" on fan-on hours; the occupied fan-off hours at full-scale CO2 are
  the fault they were designed to catch).
- **`co2_ventilation` leaves economizer-mode hours out of over-ventilation (#38).** Where the
  equipment carries an economizer command, or an OAT with an OA damper or mixed/return
  temperatures, `over_vent_pct` is judged on the other occupied hours (not judged below 10) and
  the economizer hours are reported apart; a zone with no such evidence that reads over-ventilated
  says the air handler's economizer may be the cause and points to `co2_ventilation_system`. The
  `lbnl-b59` template now runs `co2_ventilation_system`: 57-71 % of occupied hours are economizer
  mode and set apart, and the zones still sit within 150 ppm of outdoor in 99-100 % of the
  remaining minimum-damper hours -- over-ventilated at the minimum, not only while economizing.
- **`nist-ibal`** maps its liquid- and suction-line RTDs to `liquid_line_temp` /
  `suction_line_temp` and its run template names R-410A, so the whole refrigerant-side chiller
  drift family runs. Specificity: 0 of 36 monthly detector-windows (April-September 2025 against a
  January-March baseline) raised a magnitude alarm; 2 provisional CUSUM prompts at severity ok.
  New data issue `refrigerant-pressure-below-vacuum` (a dead transducer reading -70 psig).
- **`nist-heatpump-fdd`** runs `dx_refrigerant_charge` (target mode) and declares it as the scored
  detector for the charge-fault runs. Its mapping now carries discharge superheat, the raw line
  temperatures and pressures and the indoor inlet dew point.

### Fixed
- A catalog entry whose `timezone` is a prose description of its clock (BDG2, Valladolid) is no
  longer taken as the site's zone by a store source (it failed a `shared_oat` file read).
- **`nist-heatpump-fdd` suction pressure.** The 16 SEER unit's `1710_ODSuctPort_psia` is a copy of
  the discharge pressure; `suction_pressure` now maps the vapour service valve pressure
  (`1701_ODVapSV_psia`), the one NIST itself used for that unit. New data issues
  `suction-port-pressure-copies-discharge` and `fault-free-points-not-steady-cooling`.

### Benchmarks
- **Synthetic baseline refreshed with the maintainer's sign-off.** The faultlab scenarios of the
  six new single-equipment rules moved from `faultlab.PENDING_SCENARIOS` (now empty) into the
  gated `SCENARIOS`: `reheat_capacity_shortfall` (#44), `dx_refrigerant_charge`,
  `dx_indoor_airflow`, `hp_mode_vs_need`, `hp_capacity_shortfall` and `source_loop_deltat` (#40).
  The synthetic baseline gains their `.tpr` (1.0) and `.fpr` (0.0) keys, and `coverage.n_scored`
  / `coverage.n_single` move from 39 / 39 to 45 / 45 (the other new rules are fleet or drift
  rules: `co2_ventilation_system`, `hp_room_imbalance`, `discharge_superheat_drift`). Every other
  gated key is byte-identical, as are the fleet, LBNL, BDG2 and BDG2 savings baselines. See
  docs/VALIDATION.md.


## [0.92.0] — 2026-10-03

**0.92: detection gaps on the complete catalog data (#11-#17, #50, #64-#67, #69, #71).** New plant detectors
(boiler combustion efficiency, tower fouling from fan effort, the condenser-water bypass leak), a
plant run gate and cross-sensor physics in sensor trust, the system-level ASHRAE 62.1 VRP, the
G36 supply-air reset direction and the FC13-only plant link, days-weighted billing M&V with a
config path and weather fallbacks, energy reported in kBtu or kWh by a config unit system,
revised benchmark target lists, and two open meter datasets, one of them the published real-data
SEP chaining case.

### Added
- **A plant run gate for sensor trust (#66).** `schedules.plant_run_mask(frame, loop)` reads when
  a chiller (`"chw"`: run status, else power above a tenth of its own 95th percentile) or a boiler
  (`"hw"`: status, else gas input) ran, and `frame_sensor_health(frame, plant_gate="auto")` judges
  the plant roles (`sensorhealth.PLANT_GATED_ROLES`) on those running samples: range, outliers,
  flatline and stuck runs over running samples, coverage over the whole span, the first 30 minutes
  after a start left out. A chiller that sat off no longer reads as a stuck or out-of-range
  chilled-water sensor. New flag `not_running`; `SensorTrust.run_gate` names the gate. The runner's
  trust gate and the RCx report use it; `frame_sensor_health` keeps `plant_gate=None` by default.
- **`Role.GAS_INPUT_RATE`** (`gas_input_rate`): a boiler's fuel input rate, kW (#13, #66).
- **Boiler combustion-efficiency drift (#13).** `rules.boiler_efficiency_rule.BoilerEfficiencyDrift`
  (`boiler_efficiency_drift`, the new `boiler` drift family) compares a boiler's gas input per
  unit of heat delivered (`500 x gpm x delta-T`, or pump speed x delta-T without a flow meter)
  with a frozen baseline at matched load (and return-water temperature where it moves).
  One-sided up; warn at +5 % and 1.5 sigma, fault at +15 % and 3 sigma (screening-grade). A
  second frozen model, gas against OAT, corroborates: a ratio rise that the gas burned at matched
  weather does not share is reported as a heat-metering problem (`info`,
  `attribution="heat_metering"`), not a fouled boiler; without OAT the severity is capped at
  warn. `plantdrift.diagnose_boiler_drift` rolls it up. The `lbnl-boiler` catalog mapping now maps
  `BOI_GAS_CSUM_1` (boiler 1's gas input, kW) to `gas_input_rate`; Brick `Natural_Gas_Flow_Sensor`
  maps to it, and `Gas_Meter` used as a point type is accepted as an alias with a caveat.
- **Condenser-water tower-bypass valve leak (#15).** `rules.condenser_bypass_rule.
  CondenserBypassLeak` (`condenser_bypass_leak`, built-in) compares the water entering the chiller
  condensers with the towers' leaving water while the bypass is commanded shut and a chiller runs:
  warn at a 2 F median difference, fault at 5 F (screening-grade), with the bypassed fraction
  estimated from the condenser range. A difference that does not grow with the range (a
  miscalibrated sensor), or entering water colder than the tower's, is reported as a sensor offset
  (`info`), not a leak. Two new roles: `Role.COND_ENTERING_WATER_TEMP` (condenser water after the
  bypass mixing) and `Role.CW_BYPASS_VALVE` (the bypass command/position, %). Brick:
  `Condenser_Water_Bypass_Valve` valve points and `Bypass_Command` map to the valve role; a cooling
  tower's `Leaving_/Entering_Water_Temperature_Sensor` map to `cw_supply_temp` / `cw_return_temp`,
  and a chiller's `Entering_Condenser_Water_Temperature_Sensor` becomes `cond_entering_water_temp`
  when the model has that separate tower point. The `lbnl-chiller` catalog mapping maps
  `CDWL_SW_TEMP` and `TWV_CTRL` to them. Its synthetic scenario `condenser_bypass_leak` is a gated
  synthetic benchmark key (see Benchmarks).
- **Cooling-tower fouling from fan effort (#14).** `rules.tower_fan_effort_rule.
  CoolingTowerFanEffortDrift` (`cooling_tower_fan_effort_drift`, in the new `tower` drift family
  with the approach drift) compares the tower's fan speed with a frozen baseline at matched load
  (the tower range, else chilled-water tons) and wet-bulb (measured, or OAT + RH). A controlled
  tower that fouls keeps its approach and works its fans harder, which the approach rules cannot
  see. One-sided; warn at +5 %-points, fault at +10 (screening-grade). A biased leaving-water
  sensor drives the fans the same way, so when the condenser-entering water is trended the rule
  checks the two sensors' offset against the baseline; a shift of 1 F or more is reported as a
  sensor problem (`info`, `attribution="sensor_offset"`). `plantdrift.diagnose_tower_drift`
  rolls the family up.
- **`examples/lbnl_fdd/plant_detectors.py`** scores the three plant detectors on the labelled
  LBNL chiller and boiler plants with Wilson intervals (measured, not gated): boiler fouling 3/3
  with 0/14 false alarms, tower fouling from fan effort 2/3 with 0/21 (the approach drift: 0/3),
  the condenser bypass 5/5 with 0/19. New page `docs/PLANT-DETECTORS.md`; results in
  `docs/VALIDATION.md`.
- **OAT cross-check without a reference (#66).** With no `oat_reference`, the RCx report compares
  the site's OAT sources with each other: three or more against their median (the outlier gets a
  scoped `sensor_drift:oat` finding), two shown side by side with no finding.
- **System-level ASHRAE 62.1 Ventilation Rate Procedure (#17).** An air handler serving several
  zones is judged against the system intake `Vot = Vou / Ev` (`Vou = D·ΣRp·Pz + ΣRa·Az`,
  `D = Ps / ΣPz`), not one zone's Voz. `camber.ventilation.system_outdoor_air` computes it with the
  simplified Ev of the free 62.1-2016 Addendum f (`0.88·D + 0.22` below D = 0.60, else 0.75; the
  default, with the addendum's `Vpz-min ≥ 1.5·Voz` check when minimum primary airflows are given)
  or the multiple-zone appendix calculation (`Evz = 1 + Xs − Zpz`), for multiple-zone, single-zone
  and 100 % OA systems, with mode-aware Ez (1.0 cooling, 0.8 heating). `assess_system_62_1`
  judges each sample against its mode's Vot; without a flow station `estimate_oa_cfm` estimates OA
  from the mixing temperatures × supply airflow with a propagated band, and a verdict must hold
  across it. The fleet rule `ventilation_system_62_1` (`VentilationSystemVRP`) runs from a new
  config `ventilation` section (a zones CSV or list, per-system `ps` / `d` / `vps_cfm` /
  `system_type` / `method`); zones join their air handler by a declared `system`, else the config
  `topology`. It declines when an input is missing, caps at `warn` for assumed areas or
  populations, a temperature estimate, or membership from a Brick model, the naming heuristic or
  a single-source fallback, and says so. Section and table numbers of the 2019/2022 editions,
  default densities and Ez rows are marked unverified (docs/VENTILATION.md). On the open LBNL
  Building 59 data, with a stated area assumption, all four RTUs are over-ventilated 3.4–5.6×.
- **Days-weighted billing fits (#64).** A bill's per-day energy is the mean of its days, so its variance
  falls as 1/days. The change-point fitters, `fit_stats`, the regression tests, the NRE indicator
  fit, the exact kernel and the G14 FSU now take the bills' days: `weights=` fits by weighted least
  squares, and `days=` sums per-day predictions back to energy with the matching noise term.
  Equal weights are neutral (the unweighted fit, byte for byte), and daily and hourly paths are
  unchanged. On synthetic bills of uneven length (8-62 days), the exact 90% band covers the planted
  saving 89.9% of the time over 1,000 runs, and a linear fit's slope error falls 11%. The
  non-routine detectors fit billing baselines with the same weights.
- **Config `mv` entries on bills (#64).** An entry with `"bills": "gas.csv"` (start, end, energy, and
  optional units and estimated-read columns) runs the full M&V flow on
  `camber.mandv.billing.BillingSeries`: per-bill mean temperature and degree-days, days-weighted
  baseline at the G14 monthly thresholds, coverage, validity, every SEP method and `auto`, and the
  adjustments ledger (bills expanded to their days, so an adjustment is dated to the day).
  Estimated reads are merged into the next actual read (`BillingSeries.merge_estimated`). The
  temperature comes from the entry's `oat` file, an opt-in fetch, or `shared_oat`; a bills-only
  config needs no `source`. Versioned baselines and `cp_driver` are not supported for bills yet
  (docs/MANDV.md, "Billing data").
- **Weather fallbacks (#64).** An hour-of-day correction after the monthly offset, per season and UTC
  hour: out of sample at three public airports it cut the NASA POWER fallback's hourly RMSE from
  3.8-5.9 °F to 2.9-3.4 °F. Neighbouring ISD stations that fill gaps are now offset-corrected
  against the reference station by the same method. **Open-Meteo** is a third, keyless source
  (`fetch_open_meteo`), bias-corrected in the same way. `oat_reference_blended(fallbacks=...)` sets
  the fallback order, and `oat_reference_auto` picks a source by name. The RCx `oat_reference`
  accepts `"fetch": "auto"`: ISD, then POWER, then Open-Meteo, with the source of each hour in the
  report. Weather requests carry only coordinates and dates.
- **Quirk ops `remap` and `fill` (#50).** `remap` moves columns within a time window, all at once, for a
  header that names the wrong columns from a date onward. `fill` fills a short run of missing
  values with the median of the same clock time on nearby days; gaps longer than `max_run` stay
  missing.
- **`lbnl-b59` ingests its electricity meters and the heat pump's water temperature (#50).** Six
  `ELECTRICITY_METER` equipment and `HP` are added. The 2020 column shift is undone by a `remap`
  fix. The HVAC meters' zero dropouts are masked and filled. The heat-pump swap that leaves the
  metering boundary is annotated, not corrected: it is a non-routine event. The fixes reproduce the
  earlier hand correction to within 0.4 % of HVAC energy.
- **Catalog: `valladolid-uva` (#50).** Two University of Valladolid buildings (Mendeley Data
  doi:10.17632/mzkyh37mtr.2, CC BY 4.0, re-verified on the host): hourly whole-building
  electricity 2016-2020 with the publisher's daily NASA POWER weather. Pinned originals, mappings,
  a daily M&V template, and eight data issues with evidence: hour-ending local stamps with DST
  handling that changes between years, daily weather repeated on every hour, mislabelled weather
  units, a `HOLIDAY` flag that is an academic calendar and changes in 2020, gaps left empty
  although the paper says they were interpolated, files named A/B against the paper's Building 1/2,
  Building B's meter netting out on-site generation, and a 2020 COVID-19 year the description does
  not mention (kept out of the chaining analysis).
- **`examples/valladolid/chaining.py` (#50):** the real-data SEP chaining case. Working days,
  monthly rows weighted by days, a station OAT series from the weather fallback chain, baseline
  2016, reporting 2019, 2017 and 2018 intermediates and `select_method`'s own proposal; 2020 kept
  out. Published in docs/VALIDATION.md with the maintainer's sign-off: building 2 SEnPI
  0.838 ± 0.037 (16.2%, including on-site generation behind the meter), building 1 (the
  control) 1.022 ± 0.046, agreeing through both intermediate years.
- **Catalog: `cofactor-drammen` (#50).** 45 Norwegian public buildings (Lien, Walnum & Sørensen
  2025, doi:10.1038/s41597-025-04708-3; Zenodo v3, CC BY 4.0, re-verified on the host): four years
  of hourly electricity import, sub-meters, district heat and per-building outdoor temperature.
  The fixed UTC+1 stamps are stored on the Europe/Oslo clock. Twelve data issues are documented,
  among them the spring-2020 COVID-19 closures, a heat-pump heat meter 100x too large at one
  building until May 2019 (masked), meters that stop in March 2020, and sub-meters that exceed
  the import. An M&V template fits 2018 daily baselines, electricity with an occupied-day driver.
  The wide-CSV reader gains `sep` and `header_marker` for text exports that open with a metadata
  block.
- **Energy units: kBtu (IP) or kWh (SI) (#69, provisional).** A config `"units": {"system": "ip" |
  "si"}` reports energy in kBtu or kWh, demand in kBtu/h or kW, and EUI in kBtu/ft2/yr or
  kWh/m2/yr. Temperatures, pressures and flows are unchanged. Without the block every output is
  byte-identical to before. The new `camber.energy_units` converts kWh, MWh, Wh, kBtu, MMBtu,
  Btu, therms, GJ, MJ and ton-hours through kWh, and kW, kBtu/h, MBH and tons. The factors are exact:
  the IT Btu is 1055.05585262 J, so 1 kWh = 3.412142 kBtu and 1 kBtu = 1.055056 MJ. Gas by volume
  (Mcf, CCF, m3) needs an explicit heat content and steam by mass an explicit enthalpy. Unknown
  and ambiguous units (`MBtu`, `Mlb`, a bare `ton` for energy) are refused. Where the system
  applies:
  - **M&V.** Savings, bands, adjusted figures, chain links, the waterfall and the `auto`
    sensitivity table are converted and named (`energy_unit`, `meter_unit`, the unit in each
    summary). The fits stay in the meter's unit. A trended entry names its rate unit
    (`"units": "kW"`).
  - **Billing.** The bills' unit must parse, and gas in Mcf needs `bills.heat_content`.
    `BillingSeries.converted` converts a series in code.
  - **SEP.** `primary_energy` and `aggregate_energy_types` take `units=` and convert each energy
    type's delivered units to energy before the Annex B multipliers.
  - **EUI.** `bps.site_eui_units` takes a stated area unit, and `report.benchmark.unit` labels the
    audit report's EUI.
  - **Prices.** A `price` block takes a rate in any unit (`{"rate": 8.5, "per": "Mcf",
    "heat_content": ...}`).

  `bps.EUI_FACTORS_KBTU` keeps its historical 3.412 kBtu/kWh, 0.004% below the exact factor, so
  `site_eui` does not move (docs/UNITS.md).
- **Energy conversion factor sets: ENERGY STAR "Thermal Energy Conversions" (#69,
  provisional).** The new `camber.energy_factors` loads published factor sets from JSON files in
  the package and validates each one. The first set is `energy_star_thermal_2015`: every factor
  of Figures 2 and 3 of the ENERGY STAR Portfolio Manager technical reference (U.S. EPA, August
  2015), with US and Canadian columns. It covers 17 meter types, from electricity, natural gas,
  fuel oils, propane and district energy to coal, coke and wood, in 210 entries. The set is
  pinned by URL, edition, retrieval date and sha256, and keeps the source's footnotes (40 CFR 98
  Tables C-1/C-2, Statistics Canada, IDEA). The loader checks every multiplier against its heat
  content and unit size. Five rows that the source prints inconsistently are kept as printed and
  raise a caveat when used. `to_kbtu(value, unit, meter_type, factor_set=, region=)` and
  `factor_for` convert with a set.
  - **Config (opt-in).** `"units": {"system": ..., "factor_set": "energy_star_thermal_2015",
    "region": "US" | "CA"}` converts billing entries in volume or mass with the set's heat
    contents: gas in cf/kcf/MMcf/m3 (1,026 Btu/cf US, 1,031.43 CA), oil, diesel, kerosene and
    propane in gallons or litres, steam in lb/klb, and coal and wood in tons. `bills.meter_type`
    names the fuel. An explicit `heat_content` or `enthalpy` always wins. Energy units keep the
    exact factors. The findings record the factor used (`energy_factor`) and a provenance
    caveat. Without `factor_set` nothing changes.
  - **"M" means a thousand or a million.** ENERGY STAR writes Mcf for a million cubic feet, and
    many US utilities write it for a thousand. A bare `Mcf` stays a thousand cubic feet, as in
    `camber.energy_units`, and `MBtu` and `Mlb` stay refused. Each conversion of a bare `Mcf`
    now carries a caveat naming the 1,000x conflict. With a set, `kcf`, `MMcf` and
    `million cf`, `MMBtu` and `MMlb` are accepted.
  - `bps.EUI_FACTORS_KBTU` is unchanged. Its 3.412 per kWh, 100 per therm and 12 per ton-hour
    equal ENERGY STAR's, and a test keeps them so. docs/UNITS.md has the details, and
    docs/ENERGY-FACTORS.md is the generated table. `scripts/energy_factors_refresh.py`
    validates, re-pins and documents a set.
- **Unit-scale plausibility: 1000x prefix errors in billed quantities (#71, provisional).** The
  new `camber.unit_scale` asks of each meter whether its quantities are right as given, x0.001 or
  x1000. A steam bill printing `MLb` for thousands of pounds, `MMcf` entered as `Mcf`, and MWh
  labelled kWh all pass every schema check and move every result 1,000x. `check_bills`,
  `check_series` and `check_eui` return a `UnitScaleCheck`: the most likely scale, a confidence,
  each evidence item's verdict and an explanation. The evidence items are:
  - a tariff recompute of each electricity bill under a `Tariff` or URDB rate (weight 4);
  - the implied $/MMBtu against the state's EIA commercial price, or the bundled bands (3);
  - site EUI against the ENERGY STAR property-type median and hard bounds (2 or 3);
  - heating fuel per HDD per ft2 and the peak bill's load, as lb/h for steam (2);
  - the load factor (2) and the meter-read arithmetic (2);
  - ~1000x steps bill to bill, confirmed year over year.

  The rule is fixed and conservative. A scale is ruled out only by an item of weight 3 or more
  (or 4 in total) that outweighs the strongest item for it. Price and tariff evidence outweigh
  EUI, and decisive price evidence is enough on its own. **Nothing is corrected**: the explicit
  `bills.scale_override: {"factor": 0.001, "reason": ...}` is the only correction, and it is
  recorded on every finding. Where it runs:
  - **Billing M&V.** Bills that are implausible as given get a `unit_scale` warning, and their
    M&V is declined unless `bills.scale_check.on_implausible` is `"warn"`. `bills.scale_check`
    names the fuel, area, property type, cost and demand columns, a tariff and the price source.
    Plausible bills produce exactly the output they did before.
  - **Report benchmark.** An implausible `benchmark.site_eui` adds a `unit_scale` finding.
  - **BPS.** `bps.site_eui_plausibility` judges each fuel behind an EUI.
  - **BDG2 ingest.** Each implausible meter is an ingest warning. The data are not rescaled.

  Two screening references join `camber.energy_factors` as new kinds (`factor_sets(kind)`,
  `get_reference_set`). `camber_price_bands_2024` (`price_band`) holds $/MMBtu bands per fuel,
  from EIA 2024 state prices and CBECS 2018 Tables C1/C2. `energy_star_us_median_eui_2024`
  (`eui_reference`) is the ENERGY STAR *U.S. Energy Use Intensity by Property Type* (August
  2024), sha256-pinned, with CAMBER's policy factors. `camber.interop.eia.fetch_state_price` is
  opt-in: it needs `EIA_API_KEY`, sends only the state and the months, caches by the key-free URL
  and works offline. Without a key the bundled bands are used.

  On the BDG2 cleaned meters the check independently catches the documented Eagle chilled-water
  error: 82 of 87 meters are implausible and 4 more are uncertain, all pointing to x0.001. It
  also finds the same signature in 58 of Eagle's 60 hot-water meters (now a catalog data issue,
  below). It flags 4 of 1,572 electricity meters (0.25%), all meters that stopped reading.
  docs/UNITS.md, "Unit-scale plausibility".
- **The ENERGY STAR median EUI reference is complete, and two BDG2 hot-water data issues (#71).**
  `energy_star_us_median_eui_2024` now carries all 46 printed Portfolio Manager property types,
  the Public Services row that opens page 4 included. The site-neutrality guard exempts that one
  rule in that one file only (`exempt_paths`), and the local denylist accepts the same per-line
  exemption (`pattern<TAB>paths`, see `.githooks/denylist.local.example`). The `bdg2` entry
  records `eagle-hot-water-1000x` (58 of 60 meters at a median 867x the building's electricity,
  x0.001 most likely) and `fox-hot-water-scale` (15 meters at 2,244-43,785 kBtu/ft2/yr on sound
  floor areas; the hot-water energy scale is at fault, with no single factor). Both are
  annotated, flagged by the unit-scale check at ingest and never rescaled. docs/DATASETS.md.

### Changed
- **Sensor trust reads the mixed-air flow balance and copied points (#16).** `frame_checks` (and
  so `frame_sensor_health`, the runner's trust gate and the RCx report) now applies
  `copied_signal_consistency` and `mixing_flow_consistency`. A measured point that carries another
  point's data is flagged `copied_signal`; the copy is told from the original at the edges of the
  identical stretch (it jumps across the gap between them) and loses trust by the share of its
  samples that are copied, capped at "suspect"; when the copy cannot be told apart both are
  flagged and capped at "suspect". A MAT that fails the flow-weighted OA/RA balance is flagged
  `mixing_balance` and capped at "suspect"; OAT and RAT are flagged, not lowered. Both flags make
  the unit's findings on that point conditional in triage (`sensor_causes`), on that unit only. On
  the open LBNL Building 59 data (catalog example) RTU01/RTU02's MAT drop to "suspect" and RTU04's
  copied return air to 0.56, while its supply air keeps its score.
- **The chilled-water plant rules fall back to chiller power (#66).** `chw_plant_reset` and
  `chw_supply_tracking` gate on the chiller's power (`run_source="power"`, with a caveat) when no
  run status is mapped, before falling back to the supply temperature.
- The all-points-frozen trust check ignores intervals a plant was off throughout (#66).
- **`oat_reference_blended` corrects the daily cycle by default (#64)** (`diurnal=False` gives
  the 0.90.1 monthly-only offset) and corrects gap-filling stations (`station_offsets=False` turns
  this off).
  The bias record's `rmse_after_f` is the RMSE after the full correction, and
  `rmse_after_monthly_f` is the RMSE after the monthly step alone.
- **`lbnl-b59` is a documented data-issues teaching case for M&V, with no published savings
  (#50).** Its known issues now point to `valladolid-uva` for chaining, and say that the
  system-level 62.1 VRP (#17) runs on it with stated assumptions rather than not at all.

### Fixed
- **`supply_air_reset` read supply air rising with OAT as a reset (#65).** A G36 cooling SAT reset
  lowers supply air as OAT rises and for each cooling request (§5.16.2.2; the trim-and-respond
  response is negative), so the reset is now a **negative** SAT-vs-OAT slope, and a trended
  setpoint must move against its driver (OAT or SAT reset requests). Supply air that rises with OAT
  over cooling hours reads "SAT RISING WITH LOAD (possible capacity shortfall, not a reset)"
  (`info`, `warn` when cold supply air dominates) with a caveat to check the chilled-water supply
  and the cooling valve; a setpoint that rises with its driver is "in the wrong direction"
  (`sp_wrong_direction`) and never `ok`. New: `SATResetResult.direction` and the finding metric
  `reset_direction` (`reset` / `rising_with_load` / `flat` / None). The `faultlab` clean scenario
  for `supply_air_reset` now resets in the G36 direction; the synthetic benchmark did not move.
- **The G36 -> plant link overlapped the plant with every G36 fault hour (#67).** The same-hours
  test for a `g36_afdd` FC13 finding used the rule's violation mask, the union of all fault
  conditions, so duct-static (FC1) or economizer hours counted as plant symptoms. `g36_afdd`
  evidence now carries one mask per evaluated fault condition (`Evidence.masks`, provisional), and
  `link_findings(part_mask_for=...)` reads FC13's own hours (the RCx report passes it).
  `UpstreamCause.unit_hours` says which hours were used (`"FC13"` or `"finding"`), and the "why"
  line names them.
- **One unit's stuck OAT made the whole site conditional (#66).** A stuck or untrusted OAT on
  one air handler that trends its own sensor was a site-wide cause, so the chiller and boiler
  findings (which read the weather station) were marked conditional on it. The RCx report now
  passes `link_findings(..., shared_scope=...)` (and `sensor_causes`) the units that read each
  OAT source, and the cause taints only them.

### Benchmarks
- **Chiller-plant benchmark lists are explicit (#11).** The chiller-fouling runs have been
  `chiller_efficiency` targets since 0.86. The chiller, tower and secondary-DP sensor-bias runs
  are now *listed* as negatives for both plant rules, and the chiller-fouling and bypass runs as
  cross-negatives for `cooling_tower_approach`. A run on no list is excluded and printed instead of
  counting as a negative by default. No count moved: `chiller_efficiency` TPR 6/11, FPR 2/13;
  `cooling_tower_approach` TPR 0/3, FPR 0/14 with 7 declined.
- **The `lbnl-chiller` catalog entry declares its scored targets**, so `camber datasets score
  lbnl-chiller` reproduces the benchmark's `chiller_efficiency` 6/11 and 2/13 from the store.
- **Sensor bias vs physical fault on the chiller plant (#11, new, opt-in, ungated).**
  `compare_to_reference` compares chiller 1's leaving water with the primary supply (chiller 1
  running alone) and tower 1's leaving water with the condenser supply (tower 1 alone, bypass
  commanded shut). At the default 2.0 °F threshold both pairs score TPR 2/4. The chiller pair has
  FPR 0/20. The tower pair has FPR 5/20: on the five bypass-valve runs the valve ignores its
  command. New keys `chiller.sensor.*`.
- **VAV drift target lists revised, and series fan-powered boxes scored (#12).** The lists now
  follow each rule's one-sided physics. Stuck dampers at 0 / 20 % are excluded from airflow drift.
  Reheat targets the valve stuck at 0 / 20 % and coil fouling. Over-delivering valves, the stuck
  dampers and the sensor biases are cross-negatives. Parallel boxes: airflow 4/6 → 5/5 with 0 false
  positives in 24, reheat 1/7 → 2/8 with 0 in 17. Series boxes are scored for the first time, with
  the reheat duty on the fan discharge flow: airflow 5/5 with 4 false positives in 24, reheat 2/8
  with 4 in 14. New keys `drift.sfpu.*`. None of these metrics is gated (`optin-measured.json`).
- **Synthetic baseline refreshed for `condenser_bypass_leak` (#15; maintainer sign-off for 0.92).**
  Its `faultlab` scenario moved from `faultlab.PENDING_SCENARIOS` (now empty) into `SCENARIOS`.
  Added: `condenser_bypass_leak.tpr` 1.0 / `condenser_bypass_leak.fpr` 0.0; `coverage.n_scored`
  and `coverage.n_single` 38 -> 39. Every other synthetic key is byte-identical to 0.91.0, and the
  fleet, LBNL, BDG2 and BDG2 savings baselines did not move. The plant run gate (#66) left the
  opt-in chiller metrics unchanged (`optin-measured.json` matches).

## [0.91.0] — 2026-09-27

**0.91: real-data correctness (#60-#63, #32, #33, #35).** Fixes for wrong or misleading results
found in private checks on real buildings (two offices with air handlers, chillers, boilers and VAV
boxes; a ground-source heat-pump school), plus RCx report speed and plant weeks. Every fix has a
synthetic reproduction in the test suite. New names are provisional (`docs/API-STABILITY.md`).
The fleet, LBNL, BDG2 and BDG2 savings benchmark gates did not move; the synthetic baseline gained
the two new rules and nothing else (see Changed).

### Fixed
- **The G36 engine scored fan-off intervals (#60).** `run_g36_afdd` read a stopped AHU with both
  valves shut as OS#2 free cooling and tripped FC8/FC9 on stagnant duct air. G36 §5.16.14
  suspends AFDD while the AHU is not operating, so the engine now gates on the supply fan in the
  `schedules.fan_on_mask` order: `FAN_STATUS`, else `FS` (speed), else `AIRFLOW`.
  `G36Result.fan_gate` records which signal was used. With no fan signal the run is declined
  (`G36Result.declined` plus a caveat). `fan_gate="none"` evaluates every row and says so.
  `os_distribution` and `n_unclassified` now count fan-on intervals only.
- **Cooling-only AHUs returned None (#60).** A frame without `HC` is now an AHU without a heating
  coil: HC is taken as 0 %, FC7 and FC15 are omitted (`G36Result.omitted`), and a caveat says so.
  A frame without `CC` is handled the same way (FC13 and FC14 omitted). With neither valve the
  run is declined.
- **No G36 time delays (#60).** FC1 fired on the normal morning duct-static ramp, and a single
  5-minute excursion at a state change counted as a fault. The engine now applies the §5.16.14
  filters, with the defaults verified in the public Addendum p to Guideline 36-2021:
  - **ModeDelay**, 30 min: no evaluation after a fan start, or after a change of the optional
    `MODE` (zone-group mode) column.
  - **AlarmDelay**, 30 min: an FC counts only in episodes that stayed true that long. A confirmed
    episode counts in full, and a data gap breaks an episode.
  - **Rolling averages**, 5 min, of the measured temperatures and duct static.

  All three are keyword arguments (`mode_delay_min`, `alarm_delay_min`, `avg_window_min`); set
  them to 0 to score the raw per-interval equations.
- **Rules ran on the wrong kind of equipment (#61).** Rules are gated by roles, so a VAV box's
  discharge air ran `supply_air_reset`, `supply_air_control` and the SAT reset census as if it were
  an air handler, an air handler's heating valve entered the terminal reheat census, and any
  `power` point looked like a chiller. Every built-in rule is now classified
  (`camber.rules.applicability`): air-handler rules, terminal-box rules and plant rules decline a
  recognised equipment class they are not written for (an `info` finding that says why), fleet
  rules leave it out of the batch, and the rules whose roles already say enough stay roles-only.
  Class names are read by family (`camber.model.equipclass`: `RTU` and `AHU` are both air
  handlers, `HEAT_PUMP` and `WSHP` are heat pumps, `CHILLEDWATER_METER` is a meter). An
  unrecognised class is never declined; the rule runs on its roles with a caveat. The RCx report's
  air side (the SAT reset census, the economizer, air distribution) holds air handlers only.
- **The RCx OAT reference check compared only the first AHU's OAT (#61).** Every distinct OAT source
  is now compared, one row per source: units reading the same sensor share a row, and a unit
  trending its own sensor gets its own `sensor_drift:oat` finding, scoped (`scope_equips`) to the
  units that read it, so one AHU's offset no longer makes the other AHU's findings conditional.
- **`chw_plant_reset` faulted a chiller that never ran (#62).** It decided "running" from the supply
  temperature alone, which a stopped chiller on a cold or shared loop satisfies. It now uses the
  chiller's run status or command (`compressor_status`) when mapped: a chiller that never ran is
  reported as not judged. Without a status it falls back to the temperature window and says so.
- **`free_cooling_missed` counted an integrated economizer as missed free cooling (#63).**
  Mechanical cooling while the unit is already on ~100 % outside air is no longer counted. The test
  is the RCx economizer page's, now shared as `camber.freecooling.integrated_economizer_mask`: the
  measured OA fraction ≥ 80 % where `|OAT − RAT| ≥ 5 °F`, else the OA damper ≥ 90 %. The rule
  takes `OA_DAMPER`, `MIXED_AIR_TEMP` and `RETURN_AIR_TEMP` as optional roles and reports the
  integrated hours; without any of them it counts as before and caveats that it can't tell.
- **`reheat_penalty` trusted contradictory valve data (#63).** The reheat valve is cross-checked
  against the discharge-air rise over the entering primary air (else the box's closed-valve
  discharge, else a nominal 55 °F primary air). A valve at ≥ 90 % with a median rise under 5 °F is
  declined: severity `info`, the valve-based shares withheld, so nothing is counted or costed. A
  shut valve with a ≥ 10 °F rise on ≥ 25 % of its samples is caveated, and a confident `ok`
  becomes `info`.
- **`static_pressure_reset` called a one-time setpoint step a reset, and `supply_air_reset` read a
  capacity shortfall as one (#63).** The new `camber.setpoint_reset.classify_setpoint_reset` requires
  a reset to move on at least 3 days and 10 % of the days judged (fan-on samples), and to move with
  its driver (requests; else supply airflow for static, OAT for supply air; `|Spearman rho| ≥ 0.3`).
  A flat or stepped static setpoint warns as "not reset"; a trended SAT setpoint that is flat or
  stepped reads "NOT RESET (setpoint flat …); SAT deviates …" instead of "reset present". A
  setpoint that moves but not with its driver is not confirmed (`info`). Without a SAT setpoint, a
  reset read from the SAT shape alone carries a capacity-shortfall caveat.
- **The RCx report's week selection was slow on long or irregular trends (#35).** `select_week`
  reindexed every series onto every candidate week's grid, so one 23-month sensor logged in
  sub-second bursts took about 8 minutes. Each window's coverage, occupied days and evidence are
  now counted once per series from the timestamps it holds: about 0.1 s on the same shape of data.
  The chosen week, its scores and every candidate are byte-identical (a test holds the fast path to
  the per-window reference).
- **A store-backed run read the whole facility for every equipment (#35).** Each one-equipment
  read opened every part file of the facility, so a template run over thousands of equipment grew
  with the square of their count (about 108 minutes for 2,460). `ParquetStore` keeps a
  per-facility index of which part files hold which equipment (rebuilt when the facility's
  partition changes) and reads only those files. A synthetic 600-equipment run fell from 363 s
  to 45 s and a 2,460-equipment one takes 267 s, with identical findings.
- **RCx reports for chiller and boiler plants declined the representative week (#32).** The week
  view considered only air-side roles. Water-side plants now have their own panels
  (`PLANT_FAMILIES`: CHW / CW / HW supply and return temperatures, loop differential pressure, kW
  and status). A report with no air handler chooses its week on the plants; a mixed site keeps its
  air-side week and adds up to two plant panels. `select_week` takes a provisional `roles_for` for
  per-equipment roles. Air-only reports are unchanged.
- **The RCx report gave G36 advice to units with no declared G36 sequence (#32).** A check that
  assumes a G36 sequence (`*_g36`) now gets "engineer to specify" unless a `soo` entry with a
  `g36_*` library covers the unit's class (a terminal unit counts when `AHU` has one), and any other
  action worded as G36 practice is labelled a reference to check against the unit's own sequence.
- **Two stale `docs/VALIDATION.md` cells (#33).** `cooling_tower_approach` on the LBNL chiller plant
  reads FPR 0/14 with 7 declined (was 0/2, 6 declined); the FPU drift row reads airflow recall 4/6,
  0 false positives in 16, and reheat valve recall 1/7, 0 false positives in 15, 3 declined (was
  3/3 and 1/2 on an earlier, smaller file set). The opt-in LBNL benchmark now writes the confusion
  counts for the FPU and chiller subsets, records them in `examples/lbnl_fdd/optin-measured.json`
  and says when a run differs from that record; a test checks the VALIDATION cells against it and
  the SDAHU drift rows against the gated baseline. No gated metric moved.
- **The RCx issue page now names a short plant as the upstream cause (#62).** An air-handler issue
  that `link_findings` ties to a chilled-water plant short of setpoint opens with an "Upstream cause:
  plant short of setpoint" banner, and its recommended action (on the issue page and in the summary
  table) starts with "Check the chilled-water plant first", before any advice on the unit's coil
  valve. The plant's own page lists the downstream findings it may explain.

### Added
- **`g36_afdd`: the G36 engine as a registered rule (#60).** `rules: ["g36_afdd"]` in a config
  runs FC1-FC15 on every AHU. The results reach `camber run`, the audit report and the RCx
  report, and the RCx evidence shades the reported fault hours. The rule gives one finding per AHU:
  - each FC's rate, hours and applicable hours;
  - the fan gate;
  - the ModeDelay suspension, which follows a fan start or a change in the occupancy, warm-up or
    cool-down points.

  Coil semantics:
  - MAT/SAT stand in for the cooling-coil entering/leaving temperatures only on an AHU without a
    heating coil. The FC14 fan-heat term is signed for SAT downstream of the fan.
  - Otherwise FC14 and FC15 are declined with a caveat.
  - FC8/FC9 hours that coincide with a confirmed FC14 are attributed to FC14, so a passing
    chilled-water valve no longer reports under the free-cooling labels.

  `heating_coil=True` declines an AHU whose heating valve isn't trended, rather than guessing.
  `min_oa_pct` enables FC6. Severity is screening-grade (`warn_pct` 5 %, `fault_pct` 20 % of
  applicable hours, with at least 24 applicable hours).
- **`chw_supply_tracking` (#62):** does chilled-water supply temperature reach its trended setpoint
  while the plant runs? Gated on the run status (the first hour after each start left out as
  pull-down), it reports the share of running time more than 3 °F above setpoint and the loop ΔT,
  overall and while short. `warn` at 10 %, `fault` at 25 % of running time. 3 °F sits outside a
  healthy loop's ~1 °F control band plus ~0.5 °F sensor accuracy and hourly staging transients;
  a plant supplying 48 °F against a 40 °F setpoint clears it by a wide margin. Without a run status
  it falls back to the temperature window, caveats that, and never goes beyond `warn`.
- **Plant capacity in the cause chains (#62).** `link_findings` attaches a chilled-water plant that
  is short of setpoint as an `UpstreamCause` of an air handler's supply-air-too-warm finding
  (`supply_air_control` running warm, or a G36 FC13) when the two coincide in the same hours (at
  least 25 % either way) and the plant serves the unit (per the topology, else the site's plant).
  Nothing is removed, demoted or re-costed; both issues say why they are linked.
- **Served-by topology in the config (#61):** a `topology` section with a `{child: parent}` map
  and/or CSV schedules (`vav_id,parent_ahu` style), matched to the discovered equipment. It
  replaces the naming heuristic for the grouping-aware fleet rules, feeds the plant link, and is
  recorded with its provenance on `RunResult.topology_source`.
- **The plant link reads the `g36_afdd` finding (#60, #62).** The `g36_afdd` finding lists the fault
  conditions it reports in `metrics["flagged_fcs"]`, and `is_sat_high` treats FC13 (supply air too
  warm with the cooling valve full open) as the supply-air-too-warm symptom a short plant produces
  (`triage.G36_SAT_HIGH_FCS`). FC12 (SAT above MAT, which a coil that is simply off also trips)
  and FC1 (duct static) are not plant symptoms and are not linked.

### Changed
- **One equipment-class table (#60, #61).** `g36_afdd` is gated by `camber.rules.applicability`
  like every other class-gated built-in rule: it runs on the air-handler family (AHU, RTU, DOAS,
  MAU and their spellings) and declines a VAV box, a heat pump, a fan coil or a plant. The
  `equip_classes` attribute that `supply_air_reset_compliance` has carried since 0.90.1 (and that
  `g36_afdd` now carries) is read from that table, so a built-in rule's classes are declared in one
  place. A custom rule's own attribute still wins. A declined finding names the family
  (`air_handler`) rather than the four class spellings.
- **The RCx economizer page uses `integrated_economizer_mask` (#63),** the helper the
  `free_cooling_missed` rule uses, instead of its own copy of the test. The page's numbers are
  unchanged.
- New provisional names: `fdd_g36.MODE_DELAY_MIN`, `ALARM_DELAY_MIN`, `AVG_WINDOW_MIN`,
  `FC_OMIT_NO_HEATING`, `FC_OMIT_NO_COOLING`; `G36Thresholds.fc14_fan_heat` (the fan-heat term in
  FC14, signed for where the coil sensors sit); `G36Result` fields `fan_gate`, `n_fan_off`,
  `n_suspended`, `fault_hours`, `omitted`, `missing_inputs`, `caveats`, `declined`, `delays` and
  `masks` (`keep_masks=True`).
- The synthetic G36 scenarios (`faultlab.g36_accuracy`) now show the supply fan running (`FS`
  100 %) unless a scenario sets it. Without that, every scenario but FC1 would be declined for
  lack of a fan signal. The G36 benchmark numbers did not move.
- **Synthetic benchmark baseline refreshed for the two new rules** (maintainer sign-off for 0.91).
  Added: `g36_afdd.tpr` 1.0 / `g36_afdd.fpr` 0.0 and `chw_supply_tracking.tpr` 1.0 /
  `chw_supply_tracking.fpr` 0.0 (each rule has a `faultlab` scenario); `coverage.n_scored` and
  `coverage.n_single` 36 -> 38. Every other synthetic key is byte-identical, and the fleet, LBNL,
  BDG2 and BDG2 savings baselines were not touched.

### Known follow-ups
- **#66:** sensor trust has no run gate for plant equipment, so a chilled-water supply sensor that
  drifts while its chiller is off can score untrusted and make a genuine plant issue conditional.
  Also deferred there: cross-checking OAT sources against each other without a reference, and
  chiller power as a fallback run signal.

## [0.90.1] — 2026-09-27

**0.90.1 patch: fixes from two private real-data checks (#51-#59).** Every fix has a synthetic
reproduction in the test suite. New names are provisional (`docs/API-STABILITY.md`). The synthetic,
fleet, LBNL, BDG2 and BDG2 savings benchmark gates did not move.

### Fixed
- **UTC `Z` timestamps were read as local time (#56).** `parse_timestamps` dropped a `Z` / `+hh:mm`
  offset without converting it, so at a US Central site a fan that ran 07:00-16:00 appeared to run
  12:00-21:00 and `night_weekend_setback` reported "setback MISSING". Given the site's IANA zone
  (`timezone=` on `parse_timestamps`, `load_csv`, `load_point` / `load_status` /
  `load_equipment`, and the wide / long / per-point CSV, SQL, OPC-UA and BACnet adapters;
  `source.timezone` in a config), stamps that name an instant (ISO offsets, mixed offsets across
  a DST switch, a trailing `UTC` / `GMT`, epoch numbers) are converted to the site's wall clock
  and then made naive. The DST fall-back repeat is collapsed by the existing dedupe
  (`timegrid.regularize`, whose sort is now stable, so `"first"` keeps the earlier instant), and
  the spring-forward hour is a gap, as in a naive local export.
  Without a zone the clock as written is kept for back-compat, with a `TimezoneWarning` that says
  schedule and occupancy rules will be shifted; `strict_timezone=True` (`source.strict_timezone`)
  refuses such data instead. A store facility ingested from the dataset catalog defaults to the
  entry's `local_timezone`. Re-ingesting catalog entries that declare `source_timezone` gives
  byte-identical frames. Mixed offsets no longer raise inside pandas.
- **Lag-1 autocorrelation was never estimated for monthly or billing data (#51).**
  `lag1_autocorrelation` admitted only pairs spaced within 1% of the median, which calendar
  months and billing cycles never are, so every monthly fit got `rho = None` and a band left
  silently uncorrected (too narrow). When the modal spacing is month-like (25-36 days), every
  spacing in that window is adjacent; the optional `period_start` / `period_end` treat
  contiguous periods as neighbours. Forecast, backcast and chaining now caveat an unknown rho
  instead of silently using 0.
- **`select_method` never proposed chaining on monthly rows (#52).** It compared an intermediate
  window's row count with its length in days. The threshold is now 90% of the rows the window
  holds at the frame's own sampling interval (unchanged for daily rows).
- **The ISD weather source failed after its catalog end date and on one missing year (#53).**
  `fetch_isd` skips a station-year file that does not exist (404), warns, and lists it in
  `attrs["isd_missing_years"]` (`on_missing_year="raise"` keeps the old abort).
  `isd_nearest_station` warns when the station catalog looks stale.
- **NASA POWER hours were local solar time read as UTC (#53).** The request now sends
  `time-standard=UTC` (the service's default is local solar time, about 6 h off at a US Central
  site), and a non-UTC payload is refused. POWER's not-yet-published trailing fill stays missing,
  and the last real hour is reported as `attrs["power_coverage_end"]`. An existing POWER cache
  misses once and re-fetches.
- **`_t_value` rejected `confidence=0.68` (#55).** Any confidence in (0, 1) is accepted, so the
  G14 reporting criterion (savings uncertainty under 50% at 68%) can be evaluated. The 80 / 90 /
  95% table stays the fast path, unchanged; other levels are the exact Student-t quantile from
  the incomplete-beta tail.
- **One very cold bill could make coverage severe on its own (#55).**
  `ExtrapolationPolicy.min_points_outside` (default 2): the share test makes coverage `severe`
  only when at least that many reporting points lie outside the baseline support; with fewer it
  is `moderate` with a caveat, and the distance test still applies. It binds only when a single
  row carries a quarter of the points or energy, which in practice means monthly or billing data.
  `min_points_outside=1` restores the old grading.
- **Short baselines on the config `mv` path (#59).** A baseline window shorter than 365 days, or
  missing more than 10% of its days, is fitted with a caveat on `mv_baseline`
  (`metrics.short_baseline`) and on the savings or proposal findings that rest on it. It is the
  same rule `fit_version` (`camber mv freeze`) enforces, now `mvrun.baseline_window_check`.
- **Degree-day models with a slope of the wrong sign (#59).** `fit_degree_day` prefers a balance
  point whose heating and cooling slopes are >= 0; when none is, the fit is declined
  (`fit.accept` false) with a caveat. `logical_signs` gives degree-day models their expected
  signs, so the SEP sign test catches them too.
- **`night_weekend_setback` flagged a nearly idle unit (#57).** Unoccupied runtime below
  `min_unoccupied_run_pct` (default 5%) counts as an effective setback whatever the ratio, and both
  runtimes and their ratio are reported.
- **`supply_air_reset_compliance` said "ok" with supply air far above the target (#57).** Supply
  air more than `track_gap_f` (5 °F) above the G36 target in warm weather is a warn ("NOT
  tracking"), and the tracking error is reported. The rule declines on classes other than air
  handlers (a heat pump's discharge air, for example).
- **Sensor trust missed long stuck stretches (#58).** Stuck runs are judged by absolute duration
  against a per-role limit, so long series no longer dilute real outages, and the stuck
  intervals are reported. A point that starts late is judged over its own span (`late_start`,
  `first_valid`), not marked untrusted for low coverage.
- **The non-routine event detectors paired each bill with one day's temperature (#54).**
  `detect_non_routine`, `detect_step_change` and `detect_step_changes` now pair billing-period
  energy with its own period's mean temperature, per day, and `min_days` / `min_segment_days`
  count days of service instead of bills (each billing segment also needs at least 3 bills).
  Daily and hourly input is unchanged.

### Added
- `camber.tsparse.TimezoneWarning` and `check_timezone`; `timezone=` / `strict_timezone=` on the
  loaders and adapters listed above; `EquipRef.timezone` / `strict_timezone`; `source.timezone` /
  `source.strict_timezone` in a config (#56).
- `lag1_autocorrelation(period_start=, period_end=)` (#51).
- `oat_reference_blended` and `oat_reference_isd(..., fallback="nasa_power")` (#53): the nearest
  ISD station, long gaps from the next-nearest stations, and the rest from NASA POWER at the
  snapped grid cell. POWER is corrected by a monthly mean offset against the station over the
  overlap, falling back to a seasonal or overall offset, or none, when there are too few pairs.
  Per-date sources, the correction and caveats are recorded in `attrs["weather_provenance"]`.
  Also added: `power_grid_cell`, `isd_catalog_end`, `WeatherCacheMiss`,
  `fetch_nasa_power(snap_to_cell=)`, and `cached_transport(offline=, should_cache=)` /
  `cached_bytes_transport(offline=)` for offline, cache-first reads.
- `ExtrapolationPolicy.min_points_outside` (#55); `mvrun.baseline_window_check` and
  `DegreeDayModel.caveats` (#59).
- An `equip_classes` rule attribute that `Registry.run` declines other equipment classes by
  (#57).
- `SensorTrust` fields `longest_flat_hours`, `stuck_intervals`, `first_valid`, `window_coverage`,
  `n_state_changes` and `frame_checks`, plus `sensorhealth.STUCK_HOURS` and `stuck_hours=`. New
  status-point flags `never_changes` and `fractional_status`. `sensorhealth.frame_checks` adds
  fan-off pressure plausibility, status-vs-speed consistency and all-points-freeze detection, and
  the trust gate applies them (#58).
- `camber.mandv.billing` (#54): `BillingSeries` carries each bill's start, end, days,
  estimated-read flag and units. It gives each bill's mean temperature and heating / cooling
  degree-days (from hourly temperatures when available) and a day-weighted total.
  `daily_energy_vs_temp` accepts it, and the detector results report `billing` / `n_periods`.

## [0.90.0] — 2026-09-27

**M&V rebaselining: the rest of #21.** A reported saving now declares its SEP method, restates its
baseline side through an explicit, attributed adjustments ledger, and names the frozen baseline
version it used. The baseline moves only on an operator's audited decision (phases 21b-21d). The
savings and their bands are validated on real BDG2 meters and in a Monte Carlo index of every
kernel, and the result is published in `docs/VALIDATION.md` (phase 21e). Three maintainer
decisions on #21 are applied: the published validation numbers, a refit for an adjusted backcast,
and G14 kept as the default kernel with a calibration caveat on every result. The config `mv`
path also gains the change-point + driver model form. The existing BDG2 acceptance and LBNL
benchmark gates did not move.

### Added -- M&V: the SEP methods and non-routine adjustments (issue #21, phases 21b and 21c; #46, #47)
One flow for a reported saving: **the declared method gives the saving, the adjustments ledger
restates its baseline side, and the result carries both**. Every new name below is provisional
(`docs/API-STABILITY.md`).

- **`camber.mandv.methods`** gains the rest of the DOE SEP 50001 M&V Protocol 2019 Ed. 2 §6.2
  methods, all returning `MethodResult`: `forecast_savings` (wraps `avoided_energy_savings`),
  `standard_conditions_savings` (wraps `normalized_savings`, exact kernel by default) and
  `chained_savings`. `chained_savings` is **exactly SEP's chaining**: one intermediate period of
  the same length as the baseline and reporting periods, lying between them, whose model covers
  both. Its SEnPI is the Eq 6 product and its saving the Eq 11 sum. `sequential_chain` chains any
  number of results; it is **a CAMBER extension, not an SEP method**, and says so.
- **`select_method`** proposes a method in SEP's order (forecast, backcast, chaining, standard
  conditions, decline), ranking candidate models by SEP validity and then adjusted R², as the DOE
  EnPI tool does. It only proposes: the proposal has no headline figure, only a sensitivity table
  of every valid method.
- **`MethodResult`** gains trailing fields: `enpi` (SEnPI) and `enpi_uncertainty`, `links`
  (`ChainLink`), `sep_terms`, `sep_range_valid` / `sep_range`, `uncertainty_terms` and
  `baseline_version`. `measured` may now be `None` (standard conditions has no measured total).
  `backcast_savings` fills the new fields and takes a keyword-only `baseline_version`.
- **Uncertainty.** The SEP chain uses the shared-model covariance
  `(g_r − g_b)′Σ_i(g_r − g_b)` plus the noise of both measured periods. The SEnPI band comes from
  the delta method. Sequential chains combine their links by IPMVP 2012 B-19 / B-20. The kernel is
  recorded on every result: G14 by default for single-model results, exact for multi-model ones
  (decision D7). In a CI Monte Carlo (AR(1) ρ = 0, 0.4, 0.8), the chain band covers 90–92% at
  nominal 90%, and the independence form's variance is about 1.5× the exact one.
- **`camber.mandv.sep`** (new): primary energy (Eq 1) with the Protocol's Annex B multipliers as
  defaults, overridable by a user table; `senpi` (Eq 5), `chained_senpi` (Eq 6),
  `improvement_pct` (Eq 7), `top_down_savings` (Eq 8–11), `bottom_up_reconciliation` (Eq 12, the
  RF < 0.80 rule), `aggregate_energy_types` (the same method for every type, summed on primary
  energy), and `sep_range_check`. That last one is the SEP mean-in-range rule (§6.4.2.1),
  reported as a secondary `sep_range_valid` verdict beside the per-point coverage tiers
  (decision D2).
- **Config:** `mv[].method` (`forecast` | `backcast` | `chaining` with `intermediate_period` |
  `standard_conditions` with `normal_year` | `auto`) and `mv[].kernel` (`g14` | `exact`).
  `mv_savings` findings gain `method`, `method_declared`, `basis`, `kernel`, `enpi`,
  `enpi_uncertainty` and `sep_range_valid`. `"method": "auto"` gives an `mv_method_proposal`
  finding instead of a saving. When no method is declared the run still uses the forecast, as
  before, and the finding carries a caveat saying no method was declared.
- **`camber.mandv.adjustments`** (new): `NonRoutineAdjustment` and `StaticFactorAdjustment` are
  explicit, attributed ledger entries. `apply_adjustments` restates the baseline side of a
  `SavingsResult` or of **any** `MethodResult` -- forecast, backcast, standard conditions (the
  baseline model's projection at standard conditions; the band split between the two models by
  their exact-kernel terms), the SEP chain and `sequential_chain` -- and returns an
  `AdjustedResult` with the adjusted saving, SEnPI and bands, the resolved ledger and the
  waterfall components. An empty ledger reproduces the method's own numbers.
- **Chains are adjusted per link.** Each entry restates the one link whose dates hold it; the
  savings are re-summed (Eq 11) and the SEnPI re-multiplied (Eq 6). The SEP chain carries the
  shared-model covariance through (a proportional factor on the forecast link scales it); an
  entry dated in its intermediate period is refused, since both links share that model. A
  sequential chain combines its adjusted links by B-19 / B-20. `ChainLink` gains trailing
  `period`, `model_window`, `sep_terms`, `df`, `enpi_uncertainty` and `uncertainty_terms`.
- **NRA methods:** `indicator` (`estimate_nre_indicator`; +1 to `p`; a baseline-period indicator
  replaces the projection and uses the joint covariance; a reporting-period one adds in
  quadrature, except on a backcast, where it refits the reporting model -- see *Changed*),
  `engineering` (estimate + SE, evidence required),
  `exclude` (SEP §6.5 anomaly mode) and `submeter` (Option B; `nra_from_isolation`). Static
  factors: `proportional` with an explicit affected share (no default) or `engineering`.
- **Guards**: a meter-derived NRA dated within `settle_days` of an ECM date raises
  `ConfoundedAdjustment` (IPMVP 2012 §8.2). ECM dates and the settle window live in one place,
  `EcmSchedule` (`DEFAULT_SETTLE_DAYS = 14`), which the guard, the proposals and the config read.
  `validity="sep"` requires `evidence` and `approved_by`. `propose_adjustments` turns
  `detect_step_changes` output into proposed entries that must be accepted explicitly. Materiality: `|effect| >= max(threshold, 2 SE)`.
- **`camber.mandv.multivariable`** (new): `fit_cp_driver_model` /
  `ChangePointDriverModel`, a change-point + linear-driver baseline for continuously varying
  drivers (occupancy, production); it works with coverage, both savings kernels, the regression
  tests and model serialisation unchanged.
- **`camber.charts.adjustment_waterfall`**, and the config key **`mv[].adjustments`** (with
  `ecm_dates`, `settle_days`, `materiality_threshold`). It applies after whichever `mv[].method`
  is declared; `mv_savings` findings gain the adjusted saving and SEnPI, ledger and waterfall.
  With `"method": "auto"` each sensitivity row shows the adjusted figures beside the unadjusted
  ones and the proposal itself is unchanged. Monte Carlo coverage of the indicator band is in
  `docs/MANDV.md` (0.85-0.95 at nominal 90%; 0.80-0.95 at AR(1) rho 0.8, where a one-year lag-1
  estimate is biased low).
- **`mv[].validity`** (`g14` | `sep` | `both`; decision D1) is one key for the whole entry: under
  `sep` or `both` the finding carries each projecting model's SEP §6.4.1 verdict (`sep_valid`,
  `sep_validity`) and every adjustment needs `evidence` and `approved_by` (§5.3.2).

### Added -- M&V: versioned baselines and the rebaseline policy (issue #21, phase 21d; #48)
A reported saving now names the baseline version it used, and the baseline moves only on an
operator's attributed, audited decision. **CAMBER never rebaselines automatically.** Every new
name below is provisional (`docs/API-STABILITY.md`).

- **`camber.mandv.rebaseline`** (new).
  - `MVBaselineStore` wraps `BaselineStore` and keeps **every version** in
    `state/<fid>/mv_baselines.json`, a file with a `"schema"` field.
  - Each version carries a provenance record:
    - the reason, the trigger ids, and `accepted_by` plus the OS user and host;
    - the data window and a sha256 of the fit frame, so a later change to the data under a
      frozen baseline is reported;
    - the model's `as_dict`, `FitStats`, `RegressionTests` and SEP verdict;
    - the declared method and kernel, the validity regime and the policy;
    - the append-only adjustment ledger, the CAMBER version and a `content_sha256`.
- **`RebaselinePolicy`** and **`assess_triggers`** cover triggers T1-T6:
  - T1: a material step the declared ECMs do not explain. It is found by PELT on the relative
    deviation of every day since the baseline from the frozen projection.
  - T2: a declared change.
  - T3: a static factor beyond its tolerance.
  - T4: an invalid model or a severe extrapolation.
  - T5: an achievement period over 36 months.
  - T6: a new ECM with 12 months of post-ECM data (advisory).

  Outcomes follow BPA's taxonomy: NRA, or rebaseline then chain. The 20% line between an
  indicator NRA and a rebaseline (`major_step_frac`) is CAMBER's choice.
- **`propose_rebaseline`** / **`new_baseline_window`** find the latest 12 consecutive months
  that:
  - start at least `settle_days` after the trigger, and after any later step;
  - avoid ECM installation windows;
  - miss at most 10% of their days;
  - are valid under the entry's `validity`;
  - cover the expected conditions.

  Otherwise they **decline**, with the days still needed ("unresolved non-routine event on DATE;
  rebaseline needs N more days").
- **`camber mv run | freeze | list | propose | rebaseline | adjust | report`**.
  - `run`, `list`, `propose` and `report` never write.
  - `freeze`, `rebaseline` and `adjust` need `--reason` and are dry runs unless `--apply`.
    Inside a workspace they take the lock and are audited (`mv.freeze`, `mv.rebaseline`,
    `mv.adjust`).
  - `rebaseline --from-proposal` freezes a proposed model exactly, after checking its data has
    not changed.
  - The drift CLI's `_drift_audit` became the shared `_state_audit`.
- **The run path reads frozen versions.** `camber run` and `camber mv run` measure a meter with a
  frozen version against it, never refitting. They record `baseline_version`, emit `mv_trigger`
  findings and cut the saving at an unresolved trigger (`partial`, or declined, with a caveat).
  Every `MethodResult` and `AdjustedResult` records the `baseline_version` it used.
- **`camber mv report`** chains savings across versions (`sequential_chain`, each link dated from
  the store) with a new **chained CUSUM** (`camber.charts.chained_cusum_plot`): one segment per
  version, rebaseline markers, and the unreported rebaseline windows shaded.
- **Portfolio.**
  - A new `mv_baselines` retention class ("indefinite", all versions) and manifest kind.
  - `camber portfolio migrate` moves a config's `mv_store` into `state/<fid>/`, every version
    included.
  - A non-active facility is skipped by every `camber mv` verb.
- **Additive hooks.** On `BaselineStore`: keyword-only `model_types=`, a trailing
  `BaselineRecord.provenance` (left out of `as_dict` when empty, so drift baseline files are
  unchanged) and `LIST_KEY` / `SCHEMA`. Also:
  - `IndicatorFit.from_dict`, with `adjustment_from_dict` now rebuilding `fit` losslessly;
  - `AdjustedResult.baseline_version`;
  - `MethodProposal.fitted` (the chosen models' `as_dict`);
  - `sequential_chain(windows=)`;
  - `camber.config.run_mv_config`;
  - the config keys `mv[].rebaseline` (a `settle_days` there that differs from
    `mv[].settle_days` is refused) and the top-level `mv_store`.

### Added -- M&V validation: the BDG2 savings benchmark (issue #21, phase 21e; #49)
- **`examples/bdg2/savings_benchmark.py`** (new) scores CAMBER's savings and their bands on real
  BDG2 meters: 2016 baseline, 2017 reporting, cleaned meters, whole days, and at least 328 whole
  days in each year (CalTRACK 2.0 §2.2.1.2). Data are fetched, never redistributed; every draw is
  seeded. Four experiments:
  - a **placebo** scored with Touzani et al. 2019's UICF and EUR, for forecast and backcast with
    both kernels and standard conditions with the exact kernel;
  - **injected 5/10/20% savings**: forecast recovery is asserted as an exact identity, and backcast
    and standard conditions are measured (error, SEnPI-band coverage, significance);
  - **injected single and double steps**: `detect_step_changes` detection and date error,
    spurious detections, and indicator-NRA recovery and interval coverage of δ;
  - an **injected proportional static factor**.
- **A new baseline file, `examples/bdg2/savings-benchmark-baseline.json`**, is gated at `--tol 0.05`
  in the `mv-accuracy` CI job and in `scripts/gates.sh`. It holds only the new metrics, because
  `benchmark-baseline.json` is untouched: the acceptance benchmark's `accept` did not change, and
  none of its keys moved. Coverage is gated on regression, never on the nominal rate: real bands
  under-cover.
  - Placebo and injection run on every eligible meter. Steps and static factors run on a seeded
    subsample of 150 meters per type (`--sample`).
  - `--jobs` runs buildings in worker processes; results are identical whatever the count.
- **Dossier track `bdg2_mv_savings`** (`camber validate`): the placebo UICF of the G14 and exact
  forecast kernels, cited and cross-checked exactly against the new baseline by
  `tests/test_dossier.py`.
- **`tests/test_mandv_mc_coverage.py`** indexes Monte Carlo coverage for every savings kernel and
  references the cells already tested elsewhere.
  - New cells: G14 forecast and backcast, exact backcast and standard conditions, G14 standard
    conditions, the sequential chain, an adjusted static factor, and an adjusted backcast.
  - It records that the G14 kernel under-covers on a year of daily data even with a correct
    model (82-88% at nominal 90%), that G14 standard conditions and the sequential chain are
    conservative, and that the adjusted backcast is on target once refitted (see *Changed*).
- **`lbnl-b59` data issues** (catalog and `docs/DATASETS.md`) for `ele.csv`, which the catalog
  does not ingest:
  - from 2020 the file carries six meters under five column names, shifted one place;
  - the HVAC panel meters read exactly 0 while the rooftop units run;
  - the replaced heat pump was metered on `hvac_N`, and its replacement is on no meter, so a
    2018 vs later saving overstates the retrofit.

### Added -- M&V: the change-point + driver form on the config `mv` path (#47, #48)
- **`mv[].model: "cp_driver"`** with **`mv[].drivers`** fits phase 21c's change-point + driver
  model instead of the temperature-only one, on every `mv` path: the plain run, each declared
  method, validity verdicts, the adjustments ledger, and the versioned baselines of `camber mv`
  (freeze, rebaseline windows, triggers, adjust, the chained report). A driver is `"weekday"`,
  `"occupied_day"` (`mv[].occupied_weekdays`, default Monday to Friday, minus `mv[].holidays`), or
  any mapped numeric role (its daily mean).
- Phase 21d found that an occupancy-driven building fails validity with the temperature-only
  form, which left `mv` unusable there; with a weekday driver the weekly cycle is modelled.
- `mv_baseline` gains `model_form`, `drivers` and `driver_coef`. A frozen driver model reads its
  own drivers, and the fit-frame sha256 covers them. `"method": "auto"` and
  `"standard_conditions"` are refused with this form, each with the reason. Without `mv[].model`
  nothing changes.

### Changed -- the maintainer's decisions on #21
- **Published: the BDG2 M&V savings validation result** (`docs/VALIDATION.md`). With nothing
  injected, the nominal-90% forecast band covers zero for 33% of 1,023 electricity and 52% of 334
  chilled-water meters (G14; exact 36% / 54%). At nominal 95% the G14 figures are 38% / 57%,
  against the ~71% Touzani et al. 2019 found on 69 screened buildings. It also publishes the
  injected-saving recovery, step detection and static-factor figures. Every figure was
  regenerated from the 0.90 code, and all 321 gated metrics are identical to the committed
  baseline. A new `tests/test_dossier.py` check recomputes each published percentage from that
  file. No `lbnl-b59` figures are published.
- **An adjusted backcast with a reporting-period indicator refits the reporting model** with the
  indicator (joint Σ, `p + 1`), as a baseline-period indicator already does on the baseline side.
  Before, the band was the reporting model's, fitted *through* the event: about 20× too wide,
  covering 100% at nominal 90%. The refit covers 86% / 88% / 86% at AR(1) ρ = 0 / 0.4 / 0.8,
  unbiased, gated at [0.85, 0.95]. The refit needs the baseline rows' `drivers=` (the config
  path passes them). Without them, inside a chain, with a second such indicator, or with an
  indicator fitted on another window, the old conservative band is kept with a caveat.
- **G14 stays the default kernel, with a caveat on every result that uses it.** In simulation
  with a correct model the G14 forecast and backcast bands under-cover (about 82-88% at nominal
  90%), and CAMBER's projected G14 kernel for standard conditions is conservative (99-100%), while
  `kernel="exact"` is on target. Every `SavingsResult`, `MethodResult` and `AdjustedResult` with a
  G14 band says so and recommends `kernel="exact"` for calibrated bands. `docs/MANDV.md` gives the
  Monte Carlo evidence; the default will be revisited at 1.0. The caveat is text only: no band,
  saving or benchmark metric changes.

### Fixed
- **The step searches no longer hang on a constant, dead or noise-free series.** A series the fit
  reproduces exactly (one BDG2 chilled-water meter reads 0 all of 2017) gave PELT a 0/0 cost that
  no penalty could prune, so the `max_steps` loop never ended. `detect_step_changes` and the
  rebaseline T1 step search now share one guard: they stop with the steps found so far (the
  detector adds a caveat).
- `camber mv propose` says why it runs no SEP method proposal for a `cp_driver` entry, and prints
  a proposal error instead of skipping it silently.

## [0.89.0] — 2026-09-27

**Catalog release 2.** The research-only tier, manual-download entries, Excel workbooks and
Brick-grouped ingest (the framework), one reconciled design for the source layouts real buildings
publish, and 14 new catalog entries -- 21 in all. CAMBER's first result on real, labelled
multi-zone VAV data is published in `docs/VALIDATION.md`. The `camber.datasets` API stays
**provisional**.

### Added -- the catalog framework
- **Research-only tier** (`access: "research_only"`): `camber datasets fetch` refuses such an
  entry without `--accept-noncommercial` (exit 3) on every fetch, with no environment-variable
  bypass; each acceptance goes to the `acknowledgements.json` ledger and the manifest before
  anything downloads. `fetch --all` is the open tier only; research-only entries need
  `--licence all` and `--accept-noncommercial`. `ingest` needs an acknowledgement of the entry's
  current licence (or its own `--accept-noncommercial`) and records `redistribution: prohibited`.
  `datasets list` shows the licence tier.
- **Banners everywhere**: store reads stamp the facility's dataset provenance on the frame, so
  `build_dashboard` and `build_site_report` show the licence block and the non-commercial /
  do-not-redistribute banner even when no `data_sources` are passed (audit, RCx and drift already
  did through the config).
- **Manual-download entries** (`manual: true` + `manual_instructions`) and `camber datasets ingest
  <id> --from-dir DIR` (API `ingest(..., from_dir=)`): files downloaded by hand are verified
  against their pins (size + SHA-256; a mismatch exits 2 and leaves the file untouched) and
  adopted into the cache. Works for any entry whose files you already have.
- **`xlsx` extra** (`openpyxl>=3.1`, imported lazily): catalog runs may read `.xlsx` workbooks
  (`"sheet"` names the worksheet); a missing extra is an actionable error. The validator requires
  `"requires_extras": ["xlsx"]` on such entries. Legacy `.xls` is not in the extra (no entry needs
  one).
- **Brick owner grouping**: `BrickPointMapping.owner` / `owner_class` (from `hasPoint` or the
  inverse `isPointOf`, now read by both parsers) and `interop.brick.part_parents_from_brick`. A
  catalog run with `"group": "brick"` splits a per-quantity table into equipment by the entry's
  Brick model (`ingest.brick.equip_classes`), walking up `hasPart` / `isPartOf`; the mapping file's
  `aliases`, `equipment` and `equipment_classes` override the model.
- **Link check**: `scripts/datasets_linkcheck.py` and the weekly `datasets-linkcheck` workflow
  (advisory: job summary + warnings, never fails the build) check every file's served size / ETag
  and, with `licence_check` (`expect`, `json_path`), the licence page. `pytest -m network` fetches
  one small open file end to end.

### Added -- source layouts for real buildings (one design)
Four intake branches built these in parallel; 0.89 ships them as one design, each concept with one
key and one code path (`camber/datasets/_readers.py`), documented in
[DATASETS.md](docs/DATASETS.md#source-layouts):
- `members` -- a run's per-quantity tables joined on the timestamp, or `{raw column: member}` for
  one file per point (a headerless `timestamp,value` export or a table whose last non-clock column
  is the value); only the needed members are extracted, and a table several runs share is parsed
  once.
- `where: {column: value or [values]}` -- a run's rows of a stacked table.
- A run's own `mapping` with `vars` filling `{placeholders}`, and its own `timestamp_format`,
  `units`, `encoding` and `sheet`; `equip_id` stores its equipment verbatim.
- `group: "mapping"` splits one table into equipment by the mapping file (no Brick model), and a
  grouped run's `target` names the equipment under test -- the rest is unscored `context`.
  `derive` gains `copy`. The naming topology understands scenario equipment
  (`RTU_VAV_104__d2_stuck_040` sits under `RTU__d2_stuck_040`).
- The `per_point` adapter: one series file per sensor, a sensor `index`, one facility per group,
  sample-and-hold resampling (`hold`).
- `clock`: an `elapsed` simulation clock, or a **synthetic** `day` / `rows` clock (recorded as such
  in the notes and the provenance); `timestamp_format: "ISO8601"`; `source_timezone` ("UTC", an
  IANA zone or `"offset"`) + `local_timezone` move a dataset onto the site's wall clock (offset
  labels are otherwise dropped, the wall clock kept).
- Units `psia` (to psig), `psig`, `m3/min`; licences `CDLA-Permissive-1.0` and `NIST-PD` (open);
  tar archives' extracted sizes are checked against free disk; `contiguous: false` marks an entry
  whose days are not consecutive.
- Duplicate-run detection compares the members' bytes **and** what a run selects from them (rows,
  mapping, placeholders, worksheet), so a stacked table's rooms are not "duplicates".
- The validator rejects the intake branches' earlier spellings (`points`, `where: {column, in}`,
  `tz_convert`, `synthetic_index`, `day_clock`, `timestamp_unit` / `timestamp_origin`, the
  `per_point` adapter's `points`) and names the key to use.

### Added -- 14 catalog entries
Every licence re-verified on the host's page, every file pinned (size + SHA-256), every problem in
the published data described with its evidence and handling (`docs/DATASETS.md`).
- **DCV and CO2:** `lbnl-b59` (LBNL Building 59: four RTUs with measured OA flow, 11 CO2 zones, 51
  underfloor terminals, Brick model; manual download, CC-BY-4.0), `finnish-dcv` (a laboratory
  room with occupancy-based DCV and ground-truth counts, CC-BY-4.0), `b4b-windesheim` (three office
  rooms, two CO2 sensors each, CC-BY-4.0; cited by pinned commit, it has no DOI).
- **AHU and refrigerant side:** `nuig-ahu101` (a 100%-OA lecture-theatre AHU, CDLA-Permissive-1.0),
  `irish-ahu` (an industrial mixing-box AHU, 5.5 years, CC-BY-4.0), `nist-heatpump-fdd` (labelled
  residential heat-pump lab test points, NIST-PD; `xlsx` extra), `nist-ibal` (a lab chiller with
  refrigerant pressures; manual download, NIST-PD).
- **Real buildings and refrigeration:** `robod` (five Singapore rooms, CC-BY-4.0), `sdu-ou44` (three
  Danish rooms, anonymised days on a synthetic day clock, CC0-1.0), `ornl-frp-ops` (one RTU and ten
  VAV boxes under seven operating scenarios, CC-BY-4.0), `ornl-supermarket-fdd` (a CO2 booster
  rack with six labelled faults, reference only, CC-BY-4.0).
- **Multi-zone VAV and the research-only tier:** `ornl-frp-vav` (one RTU and ten VAV boxes, 31
  labelled one-day tests, CC-BY-4.0), `rbc-g36-ahu` (simulated G36 and rule-based AHUs with five
  VAV zones, 414 runs; **research-only**, below), `at-30bldg-sensors` (1,832 raw sensors from a
  30-building campus, CC-BY-NC-SA-4.0, research-only).

### Changed -- maintainer decisions
- **`rbc-g36-ahu` is research-only for a stated reason.** Its record is CC BY 4.0, but the archive
  bundles a folder of ASHRAE 1312-RP data (`01_RBC-ASHRAE1312`) whose open licence CAMBER cannot
  vouch for. The folder is never ingested (data issue `bundled-1312-rp-folder`), and the whole
  entry sits behind `--accept-noncommercial`. The new `access_reason` states why an open-licence
  entry is research-only; the licence gate, `datasets info`, the facility provenance and every
  report's banner and source block show it. An NC / ND licence can still never be labelled open,
  and `access_reason` is rejected anywhere but on an open-licence research-only entry.
  `datasets.catalog(licence="commercial")` is the open tier (it excludes such an entry).
- **Data-issue citations.** A data issue cites the documentation it contradicts by DOI whenever the
  dataset has one; a dataset without a DOI may cite a **pinned** https URL -- fixed to a commit or
  a version, never a moving branch or landing page. The validator enforces exactly this.
- **The ORNL multi-zone VAV result** is published in `docs/VALIDATION.md`: on `ornl-frp-vav`'s full
  subset, `unmet_setpoint_hours` catches 10 of 18 stuck-damper days (56%, Wilson 95% 34-75%) with
  1 false alarm in 13 negatives (8%, 1-33%) -- the documented solar-overheating day. It is a
  symptom rule on small n in one building, not a gated benchmark, and the caveats are listed there.
- **Default subsets are bounded by size, not run count:** at most 100 MB once ingested. With that,
  `ornl-frp-ops`'s default keeps all ten boxes of its two heating scenarios (24 runs, 8.6 MB)
  instead of two.

### Changed
- The site-neutrality guard's rules may carry per-rule `exempt_paths` (a third output field).
  Only the third-party dataset-host rule and its DOI-prefix rule use it, and only for
  `camber/datasets/catalog.json`, which must link data as published; commit messages, the
  CHANGELOG and release artefacts still apply every rule, and the licence-encumbered dataset rules
  are never exempt. The CI guard, the pre-commit hook and `scripts/gates.sh` apply it.
- CI installs the `xlsx` extra so the workbook path is tested.
- `scripts/catalog_sweep.py` verifies manual entries from their seeded copies, sweeps
  research-only entries only with `--accept-noncommercial`, requires the non-commercial banner
  **exactly** on research-only data, builds an unlabelled entry's reports for its first
  equipment, and with `--skip-rcx ID,...` builds only the audit report for the named entries (the
  RCx report's `select_week` takes about 8 minutes on one 23-month `at-30bldg-sensors` sensor,
  #35).

### Fixed
- **#34:** three flaky tests are deterministic -- the manual-entry fixture zip has fixed member
  dates (it is built twice and compared against its pin, so a second boundary between the builds
  changed its hash; this also broke the research-only manual-entry test), and the portfolio
  idempotence test injects the lock's clock.
- **Store timestamps are always nanoseconds.** A facility written from a millisecond source
  (the `at-30bldg-sensors` parquet series) made pyarrow read every facility of a shared store at
  millisecond resolution, and rules that derive sample spacing from nanosecond integers misread
  it by 1,000x (`control_hunting` faulted every `rbc-g36-ahu` scenario at 77,381 reversals/hour).
  The store now writes and reads nanoseconds.
- **#36:** the sweep's "no G36 verdict without a sequence" check skips citation columns (an action
  plan's `Cite`, an ECM `Standard` / `Reference`) and judges verdict text only.

### Known follow-ups (0.89 intake)
- #35: `select_week` (RCx) is slow on long or large runs, and a template run over the
  2,460-equipment `rbc-g36-ahu` full subset takes a long time.
- #37 DCV verification by hour of day with a damper fallback; #38 `co2_ventilation` and economizer
  air; #39 an R-410A saturation transform; #40 heat-pump / DX refrigerant rules on the NIST data;
  #41 dehumidification-with-reheat vs simultaneous heating and cooling; #42 a fan-heat allowance
  for `leaking_valve`; #43 setback held by a cycling fan; #44 a zone below setpoint with reheat
  saturated; #45 a time-series point-type suggester.

## [0.88.0] — 2026-09-26

**A retro-commissioning report.** `camber report --layout rcx` turns one run into a printable RCx
document: what the data covers and which sensors to trust, one representative week, the
economizer and supply-air reset, and one page per issue, with related findings linked into ranked
issues and priced once. Re-reading its output against the LBNL single-duct AHU runs also found
several rules judging fan-off samples; those are fixed. The report and its issue layer are
**provisional** until 1.0.

### Added
- **The RCx report layout** (provisional, `camber.report.rcx`): `camber report CONFIG --layout
  rcx`, or `camber.report.build_rcx_report`. One printable HTML document: a cover with the data
  source and provenance (the non-commercial banner on every printed page for research-only data),
  a one-page executive summary, data coverage and gated sensor trust, a representative week, the
  economizer, a three-tier SAT reset census that declines a G36 verdict when the site has no
  declared sequence, air distribution, M&V and drift, one page per issue, and appendices of
  declined checks and assumptions. The week is chosen deterministically (`select_week`:
  `evidence`, `oat-range`, `typical` or a fixed date) and the report says why. Engineer notes
  (`--notes`, `--notes-template`), the fault store's notes on each issue page (`--lifecycle`),
  `--paper letter|a4` and `--week`; `report.layout`
  and a `report.rcx` config section; other layouts plug in through the `camber.reports` entry
  point. See the new `docs/RCX-REPORT.md`.
- **Linked issues** (provisional): `camber.rules.triage.link_findings` groups findings into ranked
  `Issue`s. A sensor fault that could explain a finding is attached as a conditional cause, never
  used to delete it; violation hours are the union across the linked findings, and a cause chain
  is costed at its largest member, not the sum. `finding_confidence` grades each issue H / M / L
  with its reasons; `issue_totals` rolls them up.
- `RunResult.registry`, `frame_for`, `refs`, `data_sources`, `config` and `base_dir`;
  `data_sources=` on `build_site_report` / `build_dashboard` (so the site report and dashboard carry
  the non-commercial banner too); `sensor_trust(..., gate=)`, which
  ignores flat stretches while the fan is off (opt-in, the default is unchanged);
  `charts.template_violations`; `charts.box_by_hour`, which labels each hour with its sample count
  (red under 10); and an `"equip": [names]` filter on config equipment entries.
- `outdoor_air_fraction` takes `denom_min_f` (default 5 °F, the |RAT − OAT| guard); its fan gate
  has been on by default since 0.86. Both OA rules report how many samples each guard left out
  (`n_masked_fan_off`, `n_masked_small_delta_t`, `n_masked_out_of_range`; `OAFractionResult.masked`)
  and `outdoor_air_fraction` records its `failure_mode`.

### Changed
- **Rules judge fan-on samples.** `supply_air_reset` fits and draws occupied, cooling, fan-on
  samples only (on the LBNL stuck-damper run its slope goes from +0.015 to 0.000 and the verdict
  holds); `supply_air_reset_compliance` judges fan-on, occupied samples by default and records
  `fan_gate`, `occupancy_gate`, `n_gated` and `reset_source`; `supply_air_control` counts the fan
  as running only when it is on for most of the interval, so start-up hours are no longer scored
  as off setpoint; `economizer_high_limit` is fan-gated by default. All record which fan signal
  gated them. Benchmark metrics are unchanged.
- **Evidence matches the verdict.** `economizer_high_limit` and `outdoor_air_fraction` charts are
  built from the rule's configured envelope and exactly the samples the verdict counts (with a
  seasonal `min_oa_pct_by_month`, the OA-fraction chart plots each sample against its own month's
  minimum);
  `supply_air_reset` evidence is the SAT-vs-OAT cloud the rule fits, not a packaged G36-like band.
- **Advice follows the failure mode.** `aso` recommendations for an OA-fraction finding now depend
  on what failed: under-ventilation gets "Restore minimum outside air", excess outside air or a
  missed high limit gets lockout advice at the rule's configured limit, and missed free cooling
  gets "enable economizing". The audit report's action plan benefits too.
- When nothing is costed, reports show "no costed issues" (and the fleet rollup "no costed
  findings") instead of a $0 total; `FleetReport.cost_estimated` records whether a price was given.
- A citation's DOI is printed once, in every report that renders citations, and a lead-in line
  stays on the page with its figure or short table.

### Fixed
- `CAUSE_CHAINS` named `reheat_minimization` instead of `reheat_minimization_g36`, so that link
  never grouped. The chains are now `sat`, `econ` and `static`, and a test checks every member is
  an emitted rule.
- The high-limit summary printed "OAF > 50%" while counting > 55%; it now states the threshold it
  uses.
- Chart legends read `Role.SUPPLY_AIR_TEMP`; they now read `supply_air_temp`.
- `supply_air_reset_compliance` listed unused fan-signal alternatives as missing inputs; it now
  names a fan signal as missing only when none is present.

### Known issues
- A rule without a rule-derived violation mask (e.g. `simultaneous_heat_cool`) shows its hours as
  "not measured" in the RCx report. Economizer and OA-fraction issues are uncosted (there is no
  excess-outside-air cost model yet).
- The LBNL chiller- and boiler-plant RCx reports decline the representative week although the
  plant data is there (the week panels consider air-side roles only), and a unit with no declared
  G36 sequence can still get G36 advice on an issue page (#32).

### Notes
- New provisional names: `camber.report.rcx` (`RcxOptions`, `RcxReport`, `build_rcx_report`,
  `select_week`, `WeekChoice`, `load_notes`, `notes_template`, `P3_FAMILIES`, `WEEK_MODES`) and in
  `camber.rules.triage` `Issue`, `SensorCause`, `Confidence`, `link_findings`,
  `finding_confidence`, `sensor_causes`, `issue_totals`, `SHARED_ROLES`. The additive parameters
  and fields above are stable in shape. Snapshot regenerated. No new dependency.
- Next for the RCx report: grounded AI prose with a cited fact index, an excess-OA cost model,
  cross-equipment (AHU → VAV) issues, and PDF output.

## [0.87.0] — 2026-09-26

**M&V baselines no longer extrapolate silently (#20), and the groundwork for rebaselining is in
(#21, phase 21a).** No savings path checked whether the reporting period's drivers lay inside the
range the baseline was fitted on: a spring baseline projected onto a summer reported a saving, and
a savings band, as if the model held there. Models now serialise losslessly, report coefficient
p-values and the DOE SEP validity verdict, and gain backcast savings, an exact uncertainty kernel
and multi-step non-routine event detection.

### Added — M&V coverage
- `camber.mandv.coverage`: `ExtrapolationPolicy`, `Coverage`, `TIERS`, `support_of`,
  `assess_coverage` and `towt_coverage` grade how well a fitted model's driver support covers a
  set of conditions — `in_range`, `moderate`, `severe` or `not_evaluated` — with the share of
  points and of baseline-projected energy outside the support, and the furthest distance beyond
  the fitted range. Multi-driver models add a leverage test (hidden extrapolation); TOWT is judged
  per occupancy mode x temperature cell; a categorical model's unseen category is unsupported.
- `coverage()` on `ChangePointModel`, `DegreeDayModel`, `DriverModel`, `TOWTModel` /
  `TOWTAtIndex` and `CategoricalModel` (plus `CategoricalModel.at(cat)` to bind a period);
  `ChangePointModel.fit_range`.
- `SavingsResult` and `IsolationSavings` gain `coverage`, `declined`, `declined_reason`, `caveats`
  and `fsu_extrapolation_factor`; `NormalizedSavings` gains the same plus `coverage_baseline` /
  `coverage_reporting`. `NMECResult` / `HourlyNMECResult` carry them in the nested savings.
- A keyword-only `extrapolation=ExtrapolationPolicy(...)` on `avoided_energy_savings`,
  `caltrack_savings`, `caltrack_savings_hourly`, `isolation_savings`,
  `isolation_normalized_savings`, `normalized_savings` and `savings_chart`.
- A moderate extrapolation widens the FSU of linear-in-parameters baselines by the parameter
  variance factor `k` (see docs/MANDV.md); TOWT is flagged, never widened, since it holds its
  response flat beyond the fitted range. `caltrack_savings` caveats a baseline shorter than 365
  days. The savings chart rug-marks out-of-support points, suffixes a moderate title and draws a
  declined saving as "extrapolated — not a saving" with no band.
- Config `mv` entries take an optional `"reporting_period": [start, end]`, emitting one
  `mv_savings` finding per meter (`avoided_energy`, `savings_pct`, `fsu`,
  `fsu_extrapolation_factor` and the coverage metrics; a severe extrapolation is an `info` finding
  with `metrics["declined"]`, the reason and a caveat), and an optional `"extrapolation": {...}`
  policy (`ExtrapolationPolicy.from_dict`; unknown keys are an error). `mv_baseline` findings gain
  `oat_fit_min`, `oat_fit_max`, `oat_support_lo`, `oat_support_hi` and `rho`.

### Added — M&V foundations for rebaselining (#21, phase 21a)
- **Model serialisation.** `as_dict()` / `from_dict()` on `ChangePointModel`, `DriverModel`,
  `DegreeDayModel` and `TOWTModel` round-trip through JSON losslessly: prediction is rebuilt from
  the kind and coefficients through the model's design and is bit-identical to the fitted model
  (zero-intercept kinds and a 5P that fell back to a line included), and the private fit record
  travels with it, so coverage and uncertainty are unchanged. `DriverModel.as_dict()` now reports
  full-precision coefficients (it rounded to 6 decimals) plus `type` and `fit_record`.
- **Coefficient p-values and the SEP validity verdict** (provisional). `stats.regression_tests`
  returns `RegressionTests` (coefficients, standard errors, t, two-sided p, the overall F p-value,
  adjusted R², `rho` and rho-adjusted p-values); change points count as parameters and flag the
  slope p-values as conditional on them. `stats.sep_validity` applies DOE SEP 50001 M&V Protocol
  2019 Ed. 2 §6.4.1 (F p < 0.10, every relevant variable p < 0.20, one p < 0.10, R² >= 0.50,
  logical signs) and reports the verdict as written and rho-adjusted, caveating a disagreement —
  a separate verdict from the G14 `accept` gate, which is unchanged. `model_regression_tests` and
  `logical_signs` apply them to a fitted model. The t and F tails are computed in the standard
  library (a continued-fraction incomplete beta), checked against closed forms and the NIST StRD
  *Norris* certified values. `FitStats` gains trailing `f_pvalue` and `adj_r2`.
- **Backcast savings** (provisional), `camber.mandv.methods.backcast_savings`: the DOE SEP
  backcast (SEP 2019 Ed. 2 §6.2.2, Eq 9), `S = O_b - P_r|b` — a model fitted on the reporting
  period projected back onto the baseline period's conditions. Coverage is the **reporting**
  model's support graded against the baseline drivers; the uncertainty is the reporting model's
  (G14 kernel by default, exact optional). Returns a `MethodResult` labelled
  `method="backcast"`, `basis="baseline-period conditions"`.
- **Multi-step non-routine event detection** (provisional), `nonroutine.detect_step_changes`
  (also exported from `camber.mandv`): PELT (Killick, Fearnhead & Eckley 2012) with a Gaussian
  mean-change cost on the weather-model residuals, the variance inflated for serial correlation
  (`sigma^2 * kappa`), an mBIC-style penalty of `3 ln n` per step, `min_segment_days=28` and
  `max_steps`, after Touzani et al. (*Energy & Buildings* 185, 2019). The weather model is refitted
  with one level indicator per segment so a step is not absorbed into the slope, and detection
  repeats until the step set is stable (at most three refinement rounds). Each step reports its
  size and a rho-inflated standard error. On synthetic AR(1) sites it places two planted steps to
  the day and flagged none of 50 step-free runs at rho = 0, 0.4 or 0.8.
- `detect_step_change` takes a keyword-only `autocorrelation=True` that divides its statistic by
  `sqrt(kappa)` (recommended: at rho = 0.8 the uncorrected statistic fired on most step-free
  runs). The default is unchanged, so existing results do not move.
- **An exact uncertainty kernel**, `kernel="exact"` on `avoided_energy_savings`,
  `normalized_savings`, `isolation_savings` and `isolation_normalized_savings` (default `"g14"`,
  unchanged): the OLS projection variance `kappa*s2*(g'Ag + m)` of the fitted model at the
  application conditions (textbook OLS; BPA/SBW 2017 §3.3), with `t` on the fit's `n - p` degrees
  of freedom. It carries the leverage of extrapolating itself, so `fsu_extrapolation_factor` is
  1.0 under it. In a seeded Monte Carlo on change-point data with AR(1) residuals (rho
  estimated), its nominal 90% band covered 90.2%, 90.0% and 86.6% at rho = 0, 0.4 and 0.8.
  `SavingsResult`, `NormalizedSavings` and `IsolationSavings` record the `kernel` used.
- The single private fit record (#20) also carries the fit's residual variance `s2`, `n`, `p`
  and `rho`; `fit_model`, `best_model`, `fit_driver_model` and `fit_degree_day` take a
  keyword-only `time_index` so `rho` is recorded, and `fit_towt` records it from its own index.

- Docs: `docs/MANDV.md` documents the p-values and the SEP verdict, the backcast, the exact
  kernel (with its Monte Carlo coverage) and multi-step detection; its stale "step-change
  detection is on the roadmap" is gone. `docs/API-STABILITY.md` lists the new surfaces as
  provisional.

### Changed
- **Severe extrapolation declines by default**: `avoided_energy`, `baseline_projected`,
  `savings_pct`, `fractional_uncertainty` and `abs_uncertainty` (and `IsolationSavings.savings` /
  `adjusted_baseline`, `NormalizedSavings.normalized_savings` and the extrapolated side's NAC)
  become `None`, with `declined_reason`. **These fields are now `float | None`.** To keep the
  old behaviour — computed, with a widened band and a "SEVERE extrapolation — not a defensible
  saving" caveat — pass `extrapolation=ExtrapolationPolicy(decline=False)`. In-range results are
  byte-identical to 0.86.0, and the BDG2 benchmark (in-sample) is unchanged.

### Fixed
- **`normalized_savings` used the large-sample t** (1.645 at 90%) whatever the fits' sizes; it
  now uses Student's t on `min(n - p)` of the two models — a 12-point monthly fit gets 1.812. It
  also applied one `rho` to both models: `rho` is now the baseline's and a new keyword-only
  `rho_reporting` the reporting model's, each falling back to the rho its fit recorded, with the
  baseline's substituted (and caveated) only when the reporting one is unknown. The module
  docstring claimed a G14 `1.26` form the code does not use; corrected. Bands change only where
  `n - p <= 120` or the two rhos differ (#21).
- **Option B dropped autocorrelation and parameter counts.** `isolation_savings` never
  estimated rho (no time index reached the fit statistics) — pass the new `baseline_index`;
  `isolation_normalized_savings` never passed `p_baseline` / `p_reporting`, so a multi-driver
  model was banded as `p = 2` — it now passes each model's `p`, and takes `baseline_index` /
  `reporting_index` for a rho per model. The in-range golden for `isolation_normalized_savings`
  moves from ±23.79 to ±24.17 (the df-aware t on 58 degrees of freedom); savings are unchanged (#21).
- `avoided_energy_savings` dropped reporting rows without a finite baseline projection silently —
  e.g. a `CategoricalModel` category the baseline never fitted. The rows are now counted
  (`coverage["n_used"]` vs `["n_report"]`), caveated and, for an unseen category, graded as
  unsupported (#20).
- The config `mv` path passed no time index to the fit statistics, so the residual
  autocorrelation `rho` was never estimated and any band from it would have been unadjusted.

## [0.86.0] — 2026-09-26

**An open dataset catalog, analysis that runs from the store, and the first two steps of the
portfolio lifecycle.** A learner — or anyone validating CAMBER — can list open building datasets,
download them with verified checksums, ingest them into a `ParquetStore`, and run the ordinary
`camber run` / `report` / `drift` against them. A portfolio workspace now gives facilities a
lifecycle state, an audit trail and a stable `facility_id`, so renaming a facility no longer
orphans its fault history, drift baselines or tickets. This is the first of four catalog
releases; both APIs are **provisional** until 1.0.

### Added
- **`camber.datasets`** and `camber datasets list | info | fetch | ingest | status | remove |
  config | score`. Seven entries: the LBNL FDD single-duct AHU, fan-coil, dual-duct AHU,
  fan-powered units (parallel and series), chiller plant and boiler plant (CC-BY-4.0), and Building
  Data Genome 2 (CC-BY-SA-4.0, all 19 files). Every file is pinned by size and SHA-256.
  - Downloads are HTTPS-only (redirects included), resumable, verified before use, written
    atomically; a changed upstream file is refused, never silently accepted. A disk-space check
    runs first. Research-only (NC/ND) entries need `--accept-noncommercial`, recorded in a ledger.
  - Archive extraction refuses path traversal, absolute paths, links and decompression bombs.
  - Ingest is idempotent (content-hashed; `--force` replaces, never appends): one facility per
    dataset (`ds-<id>`, BDG2 one per site), each labelled run as `<equip>__<scenario>`, a spliced
    fault-onset run for drift exercises, metric → IP unit conversion, and declared data fixes vs
    annotations. Provenance and labels are recorded on the facility.
  - `camber datasets score` scores findings against the dataset's labels with Wilson intervals.
  - CAMBER redistributes no datasets; it downloads them from their publishers (`NOTICE`,
    `docs/SECURITY.md` §7, new `docs/DATASETS.md`).
- **Published-data issues, described and handled (#24).** The catalog links each dataset exactly
  as its publisher provides it and never corrects it silently: every problem found in the
  published data is a `data_issues` record with the affected columns, the evidence (numbers), the
  publisher documentation it contradicts (cited by DOI) and CAMBER's handling -- `fix`,
  `annotate`, `exclude` or `none`. `validate_catalog()` checks the records and that each handling
  is wired (every quirk links to its issue; a `fix` has a fix quirk; an `exclude` names what it
  excludes). 29 issues across the seven entries, all re-measured on the data, from the audit of
  2026-09-26.
  - `camber datasets ingest --no-corrections` (`ingest(corrections=False)`) skips the fix quirks
    and ingests the data as published; the mode is part of the content hash and recorded in the
    provenance with the data issues and the runs excluded from scoring.
  - `camber datasets info <id>` lists the issues and their handling, and each subset's estimated
    store size (now per subset; `ingest` warns when the disk is smaller than the estimate).
  - `docs/DATASETS.md` gains a "Data issues and how CAMBER handles them" section generated from
    `catalog.json` (`scripts/datasets_issues_doc.py`; a test keeps the two identical).
  - The ingest spec can pin a `timestamp_format` and declare CAMBER's own column semantics
    (`recode`, e.g. a 0/1/2 mode point read as occupied only in mode 1; `derive`, a sum of columns
    or a 0/1 "above a threshold" flag). `camber.eval.benchmark` accepts several target fault types
    per detector.
- **Store-backed runs:** `"source": {"kind": "store", "path": …, "facility_id": …}` in a config;
  `resolve.StoreEquipRef` / `discover_store` / `clear_store_cache`; `ParquetStore.drop_facility`
  (an irreversible, policy-free primitive) and `.equipment`. Rules, drift and SOO run unchanged.
- **Data source & licence in reports:** `AuditReport.data_sources` renders the citation and licence
  in audit and drift reports, with a non-commercial banner for research-only data and a
  share-alike note.
- A config `mv` section: a daily change-point M&V baseline per meter.

### Added — portfolio lifecycle (provisional, `camber.portfolio`)
- **A portfolio workspace:** `camber portfolio init | adopt | status | audit`. A workspace holds
  the store, the retention policy, an fsynced append-only audit log and a single-writer lock.
  `adopt` wraps an existing store in place (it moves nothing, and re-adopting is a no-op). The
  retention defaults are stored now and enforced by a later release: raw trends 25 months, hourly
  rollups 7 years, daily rollups indefinitely, findings 7 years, drift baselines for the life of
  the equipment plus the last 10 versions, the last 12 reports, and the audit log forever; a legal
  hold beats a facility override, which beats the default. See the new `docs/PORTFOLIO.md`.
- **Facility lifecycle states** with `camber facility add | list | show | rename | activate |
  suspend | resume`. Every change needs `--reason` and is audited with the OS user and host. A
  second change command while the lock is held is refused at once; automated writes inside a
  workspace (store writes, dataset ingest) wait up to 30 s. `offboard`, `archive`, `restore` and
  `purge` are defined in the state machine and exit 2 until a later release adds export bundles
  and the deletion cascade.
- **Registry v2:** each entry gains `state`, lifecycle dates, an editable `display_name`, owner,
  tags and notes; entries written before 0.86 read as active and are not rewritten until changed.
  Removed ids are tombstoned (in `store/_tombstones.json`) and never reused, and an id that differs
  from a known one only by case is refused, on registration and on write.
- **Suspended facilities are skipped:** `ParquetStore.active_facilities()` and
  `facility_state()`; `discover_store` and store-backed configs skip a suspended facility with one
  `UserWarning` (`include_inactive=True`, or `"include_inactive": true` in a config, overrides).
  The read API's `/facilities` adds `display_name` and `state`, and the `/ui` selector marks
  facilities that are not active.
- **Per-facility state keyed by `facility_id`.** `FaultLifecycle`, `BaselineStore`, the ticket and
  findings-export helpers and `FaultRegister` take `facility_id=`; with it, fingerprints are keyed
  by the facility and `site` is only a label, so a rename keeps the history. Records gain
  `facility_id` and `aliases`, and a lookup by an old fingerprint still resolves. Without
  `facility_id=` nothing changes.
- **`camber portfolio migrate [FILE...] [--config CFG] [--map SITE=ID] [--apply --reason R]`**
  re-keys site-keyed fault and baseline files into `state/<facility_id>/`. It is a dry run by
  default; it refuses the whole apply (exit 1, nothing written) on an ambiguous, unknown or
  tombstoned label; it keeps the originals under `migrated/`, leaves redirect stubs so existing
  configs keep working, merges a history split across an old and a new name, and writes a sha256
  manifest. A second apply writes nothing; `--apply` takes the lock and is audited.
- **Config runs inside a workspace** open facility-bound stores (by default under
  `state/<facility_id>/`) and list every output in the facility's manifest, which
  `camber facility show` prints. Folder configs accept `facility_id` and `workspace`; a
  tombstoned facility's config is refused. A new optional `faults` config section keeps fault
  history across runs, and `RunResult` gains `facility_id`, `workspace` and `faults`. Inside a
  workspace `drift freeze` needs `--reason`, and `drift freeze` / `drift accept` take the lock and
  are audited.

### Fixed -- the catalog's own assumptions (#23, #25-#29, #31)
The 2026-09-26 audit compared every catalog config with its publisher's documentation and with the
data. CAMBER's own mistakes are simply fixed:
- **Single-duct AHU (#25, #23).** `SF_CS` is the supply-fan *speed* (it was mapped as the status;
  `SF_SPD`, a constant 0.9, was mapped as the speed); a fan status is derived as `SF_CS > 0` (it
  agrees with fan power in 99.8% of rows). `SYS_CTL` maps to occupancy: the simulated schedule is
  not Mon-Fri 07-18. The unit's minimum OA is a 10% damper position that measures as a **1.6%** OA
  fraction, not the assumed 20%; the template adds `economizer_high_limit` at the sequence's 60 F.
  `SA_CFM` / `RA_CFM` are divided by 60 (published as cfm x 60). The one leak run is
  `coi_leakage_010`: the four published "severities" are one file with a 10% leak.
- **Dual-duct AHU (#26).** The minimum OA is **seasonal** (28% damper Jun-Aug, 45% otherwise:
  11.9% and 31.8% OA); the flat 20% read the fault-free unit as excess OA -- the family's FPR of
  1.0. Fan status, occupancy (setback read as unoccupied), measured OA flow and supply flow are
  mapped; `simultaneous_heat_cool` leaves the template (the hot deck heats by design); the
  month-first timestamp format is pinned.
- **Fan-coil and fan-powered units (#27).** The FCU's `FCU_SPD` is in rev/s (no longer read as a
  percent; the fan status comes from the discharge airflow) and `FCU_CTRL` maps to occupancy. The
  FPU's room setpoints map to `cool_sp` / `heat_sp` (so `unmet_setpoint_hours` evaluates),
  `reheat_minimization_g36` leaves its template, and scoring targets are declared.
- **Plants (#28).** The undocumented chiller-fouling runs are scored as `chiller_efficiency`
  positives (065 reads 2.36 kW/ton and was scored as a false positive); the default subset uses the
  most severe tower fouling (065, not the mildest 095). The boiler's `BOI_GAS_CSUM_*` is an
  instantaneous gas input in kW, not a counter; with no gas-input role or gas-per-heat rule yet,
  that check -- and a supply-vs-setpoint check -- is recorded as a follow-up. The boiler's loop DP
  points are declared in inH2O.
- **BDG2 benchmark (#29).** It scored the *raw* meters (~24,700 all-zero outage days per meter
  type in 2016) while the catalog ingests the cleaned ones; it now uses the cleaned meters, fits
  whole days only (at least 23 hourly readings) and computes EUI once per building from
  electricity only (it was per meter record, mixing thermal and electric kWh).
- **`mandv.rate_to_energy` credited the reading before a gap with the whole gap (#31)** -- two
  missing days after an hourly reading of 1 put 72 into one day. Each interval is now capped at the
  series' nominal step, so gaps stay unfilled and a coverage rule can drop the day.
- **Duplicate runs in published data are ingested once.** Publishers ship one simulation under
  several labels — the LBNL single-duct AHU "leak severities" 010/025/040/050 are one file four
  times, as are its four OA-sensor-bias runs and the fan-coil set's cooling/heating airside
  minor-fouling runs. Ingest now keeps the first of any byte-identical archive members (same CRC-32
  and size) and records the rest as duplicates, so one case is scored once. (The same flaw in the
  LBNL benchmark was fixed on `main` before this release: it listed three copies of the leak run.)
- The dataset-ingest test fixture's "leak" run was byte-identical to its fault-free run (its leak
  never fired); the duplicate guard caught it.
- `camber drift accept` on a store config without `"site"` saved the new baseline under an empty
  key that runs never read.
- A suspended facility whose config had a `drift` section raised an error instead of being
  skipped.

### Changed
- **`outdoor_air_fraction` judges fan-on samples by default** (`fan_gate=True`; fan status, else
  fan speed, else airflow -- `camber.schedules.fan_on_mask`) and uses a trended `occupancy` point
  when the unit has one; a unit with no fan signal is judged ungated and the finding says so. With
  the fan stopped the mixing-box temperatures read still air: on the LBNL single-duct unit they
  alone made 1.6% OA read "ok" against a 20% assumption (#23). `min_oa_pct_by_month` sets a
  seasonal minimum. The synthetic and fleet benchmarks are unchanged; `fan_gate=False` restores the
  old behaviour.
- The LBNL point mappings moved to `camber/datasets/mappings/`, one source of truth for the
  benchmark and the catalog; the chiller plant's wet-/dry-bulb and secondary-loop supply/return
  swaps are catalog `fix` quirks. The LBNL benchmark now also reads each dataset's ingest spec
  (quirks, timestamp format, column transforms) and its run template's rule parameters, so it
  scores exactly what `camber datasets ingest` produces.
- An unknown `source.kind` in a config now warns and falls back to folders.
- `FacilityRegistry.remove()`, `drop_facility(forget=True)` and `datasets remove --purge-store`
  now tombstone the id. Re-ingesting the same dataset lifts its own tombstone
  (`FacilityRegistry.reclaim`, audited); no other id can.
- `FacilityRegistry.name()` and `facility_name()` return the display name, so reports show a
  facility's new name after a rename; `name` keeps the name it was registered under.

### Deprecated
- Reading site-keyed fault and baseline records (fingerprint `sha1(site, equip, rule/kind)`)
  through the compatibility path of a store opened with `facility_id=` — it warns with a
  `DeprecationWarning` — and the ticket field `legacy_fingerprint`. Both are deprecated since 0.86
  and removed in 2.0; run `camber portfolio migrate`. Outside a workspace nothing warns and
  nothing changes. See `docs/API-STABILITY.md`.

### Notes
- **The LBNL and BDG2 benchmark baselines are refreshed (#30), with the maintainer's sign-off.**
  Every gated metric that moved beyond the 0.05 tolerance (or changed count), old -> new:
  - LBNL single-duct AHU: TPR 0.8 -> 0.4, correct diagnosis 0.8 -> 0.4, accuracy 0.8333 -> 0.5
    (FPR stays 0). `outdoor_air_fraction` now judges fan-on, occupied samples against the unit's
    own 1.6% minimum, not an assumed 20%: the dampers stuck at 10% (the minimum itself) and 25%
    (4.4% OA) look like normal ventilation outside economizer weather and are no longer "detected"
    as under-ventilation against 20%. Their real symptom, the missed economizer, is
    `economizer_damper_drift`'s, which still catches 4/4 (#23, #25).
  - LBNL dual-duct AHU: FPR 1.0 -> 0.0, TPR 0.5 -> 1.0, correct diagnosis 0.5 -> 1.0, accuracy
    0.3333 -> 1.0. The seasonal design minimum (31.8%, 11.9% Jun-Aug) on fan-on, operate-mode
    samples replaces a flat 20% on all hours: fault-free no longer reads as excess OA, and the
    damper stuck shut now reads as under-ventilation (#26).
  - LBNL pooled: TPR 0.8 -> 0.7 and correct diagnosis 0.8 -> 0.7 (7/10: two single-duct
    detections lost, one dual-duct gained), FPR 0.3333 -> 0.0 (the dual-duct false alarm is gone);
    accuracy stays 0.7692 (10/13). The fan-coil unit and the drift metrics are unchanged.
  - BDG2 chilled water: buildings 518 -> 514, with a fitted rho 507 -> 501, median rho 0.6163 ->
    0.6664, p10 rho 0.3024 -> 0.3953, share with rho > 0.3 0.9014 -> 0.9521. Electricity:
    buildings 1,526 -> 1,497, with a fitted rho 1,515 -> 1,497. Pooled: buildings 2,044 -> 2,011,
    with a fitted rho 2,022 -> 1,998. The benchmark now scores the publisher's cleaned meters (the
    raw ones carry ~24,700 all-zero outage days per meter type) and fits whole days only (>= 23
    hourly readings), so zero and partial days no longer enter the fits or the residual
    autocorrelation (#29).
  - BDG2 EUI: median 12.23 -> 11.07 kWh/ft2/yr, n 2,044 -> 1,497. It is now one value per
    building, electricity only, from complete days annualized; it was one per meter record, mixing
    chilled-water and electric kWh and including one site's ~1,000x chilled water (#29).
  - Within tolerance, for the record: acceptance chilled water 0.3552 -> 0.3638, electricity
    0.0839 -> 0.0982, pooled 0.1526 -> 0.1661; median CV(RMSE) pooled 0.2357 -> 0.2037. #31 moves
    no BDG2 benchmark metric (the benchmark sums energy per interval rather than reading rates).
  `docs/VALIDATION.md` and the validation dossier's cited figures (OA-fraction pooled TPR 78%
  [45-94%], FPR 0%; BDG2 acceptance 17% [15-18%] over 2,011 meters) are updated to match. The FCU
  result rests on a 0.4-point margin (its leak runs give 12.7 / 15.4 / 17.4% OA against a 15%
  line).
- New public names: `camber.datasets.*`, `resolve.StoreEquipRef` / `discover_store` /
  `clear_store_cache`, `config.data_sources`, the report `data_sources` helpers, `ParquetStore`
  methods, `schedules.fan_on_mask` / `FAN_GATE_NONE`. Snapshot regenerated. No new core
  dependency.
- New provisional names for the lifecycle: `camber.portfolio` (`Portfolio`, `PortfolioLocked`,
  `LifecycleError`, `STATES`, `TRANSITIONS`, `DELETING`, `DEFAULT_POLICY`, `allowed_actions`,
  `transition`, `find_workspace`, `is_workspace`), `FacilityRegistry.state` / `tombstones` /
  `reclaim` and its `lock_timeout=`, and the `facility_id=` keywords above.
- Until authentication lands, "admin" means write access to the portfolio root; the lock and the
  audit log record who acted, they do not authorise. See `docs/SECURITY.md` §8.
- Offboarding, archive bundles, restore, purge and retention enforcement are the next lifecycle
  steps.

## [0.85.0] — 2026-09-25

**Sensor health and semantic models, checked against real buildings.** The trust layer scored a
healthy plant as broken and missed a documented sensor fault; the Brick importer could not read
back what the exporter wrote, and point-name matching got 3 of 16 real names right.

### Fixed — sensor health
- **Trust collapsed on a healthy plant.** Points held at a setpoint have almost no spread, so
  rounding noise scored as outliers; a pump idling at minimum then ramping with load is one skewed
  population, not two regimes, so all load operation counted as outliers. On a fault-free boiler
  plant flow, pump speed and loop DP scored 0.18–0.28 trust — the trust gate would have switched
  off the one detector that works there (loop ΔT, 8/8). `assess(shape_aware=, scale_floor=)`
  judges each side of the median against its own spread with a precision floor per role
  (0.5 °F, 1 %-pt, 30 ppm CO₂), and only excuses points that persist in runs; `sensor_trust` opts
  in. The same plant now reads 0.92–0.99; spikes, scattered rails and sentinels stay untrusted.
- **`compare_to_reference` extrapolated drift from days of data.** Three days of room-vs-duct
  CO₂ read "fault, drifting +275 ppm/month" with a 7 ppm bias. Drift now needs ≥ 28 days and 14
  daily baselines (else `drift_per_month` is `None` with a caveat — **a field that was always a
  number can now be `None`**), is estimated by a robust (Theil–Sen) slope through daily offsets,
  optionally restricted to quiet hours (`baseline_hours=`), and the verdict leads with the worst
  issue — a 148 ppm bias fault no longer reads as "drifting +28.7/month".

### Added — sensor health
- `copied_signal_consistency`: two roles on one unit carrying identical *changing* data (a return
  air temperature that was a copy of supply air for 2,627 consecutive hours).
- `gapfill_signature`, `cross_unit_identity`: screening checks for imputed data — a change in
  value granularity, repeated days, units implausibly identical (r ≈ 0.998). Warn at most; a
  known false positive (fans on one common speed) is named in the caveat.
- `percent_scale_suspect` and physical bounds for `OA_AIRFLOW`: a 0–100 (or 0–1) signal mapped to
  an airflow role is flagged in sensor trust (50 of 51 fan-speed points typed as airflow in a
  published building model).
- `mixing_flow_consistency`: mixed-air temperature against the OA/SA flow balance
  (f·OAT + (1−f)·RAT), widened for flow-station uncertainty, warn at most. Found two units reading
  +3.1 / +4.7 °F warm where the dataset documents inaccurate mixed-air sensors. Not yet part of
  the trust score.
- `co2_outdoor_consistency` and a `below_ambient` trust flag: indoor CO₂ below outdoor (or under
  ~380 ppm background) means one sensor is wrong.
- New `docs/SENSOR-HEALTH.md`.

### Fixed — semantic models and mapping
- **Brick export and import were not inverses.** The exporter wrote `CO2_Sensor`,
  `Outside_Air_Flow_Sensor`, outdoor CO₂ / RH and `Supply_Air_Flow_Setpoint`; the importer knew
  none of them, so DCV inputs could never come from a Brick model. A test now enumerates every
  exportable role and round-trips it. Import coverage 15 → 45 of 64 roles.
- **Hydronic plants were invisible to Brick import** — a published boiler-plant model mapped 1 of
  22 points; now 17. A boiler on/off status or any `Enable_*` class is reported as *ambiguous* and
  never mapped to `boiler_status` (an enable held at 1 all year made summer lockout fault on every
  run).
- **Point-name matching used letter fragments** of unsplit camelCase names (`OaTemp` →
  `oa_airflow`, `ReHeatVlvPos` → `evap_approach_temp`). Names are now split into words and
  matched whole: a published 16-point air-handler list goes 3 → 16 correct by name alone.
- **Range checks were always °F**, so declaring °C sent every temperature to `wetbulb_temp`.
- **Haystack return / exhaust / relief / mixed-air dampers imported as the VAV-box damper.**
- **A percent point typed as a flow rated "high" confidence.** `mapping_confidence` flags a flow
  role whose data stays within 0–100 or 0–1 (`percent_scale`) or whose declared unit is `%`
  (`unit_mismatch`); `score_token(unit=)`, `score_mapping(units=)`, `review(units=)`.

### Added — semantic models
- `brick_mapping_report(ttl)`: per point, `mapped` / `alias` (a non-standard class accepted with a
  caveat — `Outdoor_Air_Flow_Rate`, `Outdoor_*` spellings) / `ambiguous` (and why) / `unmapped`.
  A flow-typed point named like a speed or percent is never mapped to a cfm role. On a published
  267-point building model: 193 mapped (17 via aliases), 57 ambiguous, 17 unmapped — where 0.84
  mapped 134, 51 of them wrongly.

### Notes
- New public names: `sensorhealth.{mixing_flow_consistency, copied_signal_consistency,
  gapfill_signature, cross_unit_identity, co2_outdoor_consistency, percent_scale_suspect}`,
  `interop.brick.{brick_mapping_report, report_from_triples, BrickMappingReport,
  BrickPointMapping, ALIAS_CLASS_TO_ROLE, AMBIGUOUS_CLASSES}`, plus additive fields and
  parameters. Snapshot regenerated. No new dependency. Benchmark baselines unchanged.
- `docs/ONTOLOGY.md` coverage corrected: Brick import 45/64, export 39/64, Haystack 64/64, 223P
  51/64 (its previous count was stale).
- The "insufficient data" results of `chiller_efficiency` and `overcooling_min_flow` now carry
  `metrics["declined"]`, so a scorer excludes them instead of counting a correct negative (the
  convention `driftvalidation` and the LBNL benchmark rely on). Found re-checking 0.82–0.85 on the
  complete open datasets, which otherwise reproduced every recorded result.
- The LBNL chiller-plant mapping notes a second label swap in that dataset: the secondary loop's
  supply / return temperatures (return reads colder than supply in 98 % of flowing hours).
- **BDG2's licence was misstated as CC-BY.** The data repository is **CC-BY-SA 4.0**
  (Attribution-ShareAlike); the *paper* describing it is CC-BY, which is where "CC-BY" came from.
  Commercial use and analysis are unaffected; a redistributed *adapted* version of the data must stay
  CC-BY-SA. Corrected in the README, `docs/VALIDATION.md`, `docs/DEPLOY.md`, the `camber validate`
  dossier and `examples/bdg2/`.

## [0.84.0] — 2026-09-25

**Plant and refrigerant drift, checked against real machines.** Run on open data from a lab
heat pump with labelled refrigerant faults, two real 5-ton water-cooled chillers, a CO₂
transcritical refrigeration rack, a pilot wet cooling tower and a simulated boiler plant, the
refrigerant-side drift detectors declined almost everything they were given — and where they did
score, they discarded the fault.

### Fixed
- **Superheat and subcooling drift threw away the fault.** A 0–50 °F plausibility band dropped
  liquid floodback (superheat below 0 °F — the direction the rule itself calls most urgent), flash
  gas, and superheat above 50 °F. A half-flooded period read "ok, −0.1 °F". Ranges are now
  −20…150 °F (superheat) and −20…80 °F (subcooling), shared with `sensorhealth`; sentinels are
  still rejected.
- **Small machines always declined.** The baseline fit demanded a 10-ton load spread and samples
  at ≥ 5 tons, so a 5-ton chiller and 2–4 ton heat pumps declined 100 %. Gates now scale to the
  machine — 10 % / 20 % of observed capacity, capped at the old 5 t / 10 t, so ≥ 50-ton chillers
  are unchanged (a test pins that) — via `size_relative_load_gates`, with `min_tons` /
  `min_tons_span` on all eight tons-based rules. A machine whose load never moves gets a
  flat-level baseline, scored only near the load it saw, with a caveat. Heat pump: 151 of 151
  labelled cases declined → none; real chillers: 22 normal days scored with no false alarms.
- **Pressure ceilings excluded CO₂.** Head 700 psig / suction 400 psig (with comments calling them
  refrigerant-neutral) dropped every sample of a transcritical rack. Now 2000 / 1000 psig.
- **Head and suction pressure were normalized on load alone**, although pressure follows the heat
  sink. Head pressure now regresses on entering condenser water (`CW_SUPPLY_TEMP`, else `OAT`),
  suction on leaving chilled water (`CHW_SUPPLY_TEMP`), falling back to load only with a caveat.
  Heat pump head-pressure scatter 61 → 5 psi (recall 0.01 → 0.32); a real chiller's suction false
  alarms on chilled-water reset days 33 % → 0 %.
- **The sustained-shift CUSUM assumed independent samples.** At 1–5 minute cadence residuals are
  autocorrelated (lag-1 0.8–0.9), so normal days alarmed: approach CUSUM 59 % → 5 % (5-min) and
  75 % → 15 % (1-min) on a real chiller; subcooling 90 % → 5 % at 1-min. Limits now scale by the
  long-run sigma; hourly data with independent residuals is unchanged. Injected steps are still
  caught.
- **Loop DP drift declined any non-psi loop** — the plausible band was (0, 100), so a plant in
  inH₂O declined every case and blamed the wrong cause. The band now scales to the loop's own
  median (unit-free); `dp_range=` sets it.
- **Declines named the wrong cause.** "No loaded samples" when the metric column was all-NaN; new
  `unscoreable_reason` names the check that failed, across chiller, pump and loop rules.
- **`hw_pump_dp_reset` never evaluated the DP-setpoint reset it claimed**, and failed on a 0–1
  speed when called directly. It now reads `HW_DIFF_PRESS_SP` and says "not evaluated" when absent.
- **Cooling tower:** the decline says what happened ("only 5 samples at ≥ 90 % fan… fewer than
  the 10 needed" rather than "never reached 90 %"); derived wet-bulb takes `elevation_ft` /
  `pressure_psia` and is solved psychrometrically (within 0.06 °F of a reference). Sea-level
  Stull reads ~1.4 °F high at 500 m and ~2.6 °F at 1600 m hot/dry; the default is unchanged and
  now caveated.

### Notes
- **What still misses, honestly:** refrigerant-side recall on the heat pump is 0.22–0.52 per
  detector (subcooling catches every overcharge, 15/15, and half the undercharges); suction
  pressure catches nothing there; the rack's condenser-air blockage is not detected (the fault
  day was hotter than every baseline day, and the only load proxy rises with the fault). One real
  chiller still alarms on 33–62 % of summer days because it ran outside its spring baseline — a
  tuning question, documented, not tuned.
- The condenser simulator's tower-fouling case no longer corroborates through head pressure: at
  matched entering-water temperature the chiller's high side really is healthy.
- New public names in `chillerbaseline` (`size_relative_load_gates`, `unscoreable_reason`,
  `residual_lag1`, `FULL_SIZE_MIN_LOAD`, `FULL_SIZE_MIN_LOAD_SPAN`), `coolingtower`
  (`psychrometric_wetbulb_f`, `pressure_psia_at_elevation`, `SEA_LEVEL_PSIA`), `loop_dp_rule`
  and the superheat / subcooling rules, plus defaulted fields — baselines frozen by earlier
  versions still load. Snapshot regenerated. No new dependency. Benchmark baselines unchanged.

## [0.83.0] — 2026-09-25

**Air-side rules, checked against real buildings.** Every fix below came from running the rules on
open real-building data (an office-building fleet, a messy industrial air handler, a test
building's VAV fleet), and every regression test fails on 0.82.0.

### Changed — read these if you pass the old values
- **`ControlHunting(deadband=)` is now in percent** (default 5). It was 0.05 applied to 0–100 %
  data, i.e. effectively no deadband. A 0–1 signal is rescaled first.
- **`FreeCoolingMissed(active=)` is now in percent** (default 5), for the same reason: a valve
  sitting at 1 % read as "mechanical cooling ran 100 %". `n_free_cooling_hours` is now real hours
  — it was a sample count labelled hours (140,274 "hours" of 15-minute samples ≈ 35,000 h).
- **`EconomizerHighLimit(min_oa_pct=)` now defaults to `None`** (unknown). The 20 % assumption
  faulted all four units of an office building whose measured minimum is ~33 %. With measured
  `OA_AIRFLOW` / `AIRFLOW` the rule judges real flow; differential economizing (OA cooler than
  return) is left alone; with no configured minimum it faults only on excess above a generous
  bound, and declines when the verdict hinges on the unknown design minimum.
- **`realio.load_status` resamples to time-weighted duty** (`how="duty"`); `how="any"` keeps the
  old per-bin max. A cycling fan's max-per-bin inflated its duty, so the setback verdict depended
  on the resample interval. (A test pins 1-min / 15-min / hourly to the same verdict. The one
  real-data case reported by the investigation did not reproduce, so this rests on synthetic
  evidence.)
- **`fdd_g36.classify_os` returns 0 (`OS_UNCLASSIFIED`) for missing valve data** instead of
  "free cooling". On the industrial air handler FC8 was diluted from 25.7 % to 15.0 %.

### Fixed
- **`control_hunting` reported "stable" when it could not see.** At 15-minute sampling the 6 /h
  warn rate is unresolvable; the rule now declines (info + caveat, `None` rate) and caps at warn
  when only the fault rate is out of reach. Data gaps no longer count as calm time. A damper
  limit-cycling 20 → 54 → 20 → 51 every sample had read "stable (2.3×/hr)".
- **`overcooling_min_flow` said "ok" without testing.** With no `AIRFLOW_SP`, "at minimum" was
  all-False, so every box read ok at 0 % — with a caveat claiming the minimum had been inferred.
  It now declines.
- **`overcooling_severity` called other things overcooling.** Morning recovery from setback (via
  `WARMUP`, else the first 2 h), fan-off free-floating, and a saturated reheat valve (≥ 90 %,
  now reported as a **heating shortfall**) are no longer overcooling. On the office building:
  38 zone faults → 22, with 32 zones now correctly reported as short of heat.
- **Five rules ignored a trended occupancy point** — setback, overcooling, overcooling severity,
  zones census and reheat used a hard-coded weekday 07–18 window. New
  `schedules.effective_occupied_mask`: a trended `OCCUPANCY` point replaces the schedule,
  otherwise `start_hour` / `end_hour` / `occupied_days` (defaults unchanged).
- **The reset-request census fired with the HVAC off.** The SAT rogue-zone census and cohort
  starvation now gate request cycles on supply-fan status / occupancy (`gate_cols=`), and caveat
  when ungated (`UNGATED_CAVEAT`). A free-floating week went from "starved cohort" to info once
  fan status reaches the zone frames; with occupancy alone it still fires.
- **`reheat_penalty` read the wrong air at a terminal.** At a VAV box `MIXED_AIR_TEMP` is the
  entering primary air and `SUPPLY_AIR_TEMP` the discharge (the convention the reheat-valve drift
  rule already used); a discharge-only judgement is caveated as a lower bound, never a confident
  ok. On a 10-box test fleet: 9 ok / 1 warn → 10 fault with the primary air mapped.
- **`run_g36_afdd` crashed on unsorted or duplicate timestamps** (`rolling("60min")`); input is
  now sorted and deduplicated, and a non-time index raises a clear `TypeError`.
- **`supply_air_reset` said "pinned low"** for a flat reset held at 68 °F; it now says "held at".
- A served-by topology that covers no zones reports no provenance, and says 0 of N.

### Notes
- New public names: `schedules.effective_occupied_mask`,
  `rules.hunting_rule.max_resolvable_per_hour`, `fdd_g36.OS_UNCLASSIFIED`,
  `g36_reset.UNGATED_CAVEAT`, plus additive result fields and constructor arguments. Snapshot
  regenerated. No new dependency. Benchmark baselines unchanged.

## [0.82.0] — 2026-09-25

**DCV verification judged the economizer, not the DCV.** On a normally modelled building
`dcv_verification` evaluated nothing: CO₂ lives on the VAV zones, OA on the air handler, and no
equipment frame had both. Where it did run, it scored whatever moved OA — usually the economizer.

Measured on `faultlab.dcv_sim` (a zone CO₂ mass balance, 21 days hourly), old check vs new rule:

| Case | 0.81.0 | 0.82.0 |
|---|---|---|
| Working proportional DCV + economizer | uncorrelated (corr −0.81) | functioning (ok) |
| Working PI DCV + economizer | uncorrelated (corr −0.82) | functioning (ok) |
| Static DCV + economizer | uncorrelated (corr −0.78) | **static (warn)** |
| Static DCV + morning warm-up closure, no WARMUP flag | **functioning** (corr 0.71) | static (warn) |
| Working DCV, lightly occupied building | **static** | insufficient (info) |

### Fixed
- **Economizer periods were judged as DCV.** OA follows `max(economizer, DCV minimum)`; while
  economizing it tracks outdoor temperature. Those samples are now excluded, from `ECON_CMD` (any
  nonzero hourly mean) or, failing that, inferred from `OAT` + `HEAT_VALVE`. With no economizer
  evidence at all, an "uncorrelated" verdict drops to `info` with a caveat.
- **A closed damper made a stuck DCV look responsive.** A warm-up closure inside the occupied
  window put OA at 0 while CO₂ was low, which read as a response. OA at or below 2% of its p95 is
  now excluded and reported as `closed_pct`. Fan-off / partial-fan hours (`SUPPLY_FAN_STATUS`) and
  `WARMUP` / `COOLDOWN` are excluded too.
- **The `OCCUPANCY` point was loaded and never used.** It now gates the occupied mask. The rule's
  documented fallback to occupancy *as demand* could not run (CO₂ is required) and is no longer
  claimed; binary occupancy demand still works through `assess_dcv`.
- **Flat or low demand produced a verdict.** A building whose CO₂ never reached the level where DCV
  should respond read "static"; flat CO₂ read "uncorrelated". Both are now `insufficient`, with a
  `reason`.
- **One spike could flip the verdict.** `modulation` used raw max/min; it now uses p5–p95, and the
  "OA at minimum" band for the CO₂-breach check is built from the robust minimum.
- **Duplicate timestamps crashed the rule** (`cannot reindex on an axis with duplicate labels`).

### Changed
- `assess_dcv`'s verdict compares CO₂ when OA was raised with CO₂ when OA sat at its floor
  (`demand_lift`, ppm) instead of a Pearson correlation. Correlation is still reported, as a
  diagnostic. `min_corr` is **deprecated** and ignored (warns; removal in 1.0).
- `dcv_verification` returns nothing for a frame with CO₂ but no OA signal (a VAV zone) instead of
  an `info` "declined" finding on every zone; `dcv_system_verification` reports zone coverage.
- `modulation` is now the robust p5–p95 range, so its values differ from 0.81.0.

### Added
- **`dcv_system_verification`** (`DcvSystemVerification`), an auto-registered fleet rule: zone CO₂
  joined to the serving air handler's OA through the served-by topology (a naming-heuristic
  grouping caps severity at `warn`), per-timestamp maximum across zones, with zone sensors that are
  implausible, stuck or offset when unoccupied excluded. One finding with a `per_ahu` breakdown.
- **`economizer_active_mask`**, `DEFAULT_DCV_ENGAGE_PPM`, `DEFAULT_ECON_HIGH_LIMIT_F`.
- OA-floor sub-checks — `below_floor_pct` (62.1 dynamic-reset floor `Ra·Az`) and
  `excess_at_low_demand_pct` (DCV not saving energy) — via `oa_floor` / the rule's `oa_floor_cfm`
  (a number or `{equip: cfm}`); `breach_fault_pct`, `below_floor_fault_pct`, `excess_warn_pct`.
- New `DcvResult` fields (all defaulted, so positional construction still works): `demand_lift`,
  `reason`, `demand_span`, `oa_low_demand`, `oa_high_demand`, `n_econ_excluded`, `econ_excluded`,
  `below_floor_pct`, `excess_at_low_demand_pct`, `closed_pct`.
- **`faultlab.dcv_sim`** — the simulator above (proportional / PI / static DCV, economizer, warm-up
  closure, fan schedule). The `dcv_verification` benchmark scenario now uses it: TPR 1.0 / FPR 0.0.
- `OA_AIRFLOW` and `AIRFLOW` are optional roles on the AHU template (`OA_AIRFLOW` on RTU), so an
  air handler's OA flow is discovered at all. Completeness scores for AHUs and RTUs without those
  points go down.
- `CO2` / `OUTDOOR_CO2` physical bounds in the sensor-health gate, and stuck-flat detection for
  zone CO₂.

### Fixed — DCV, from a real-data pass
The new DCV code was run against five open real-building datasets (licence-clean, cited by DOI in
`docs/VENTILATION.md`, not vendored) before release. It held up where it should — a lab room with
a known DCV law reads `functioning`, an office building with no DCV reads `insufficient` on every
unit, never a false `functioning` — and turned up nine defects:
- **An occupant count was discarded.** Anything not strictly 0/1 was read as CO₂ and filtered as
  implausible, so a people count — or a presence point averaged to 10-minute means — returned
  `too_few_samples, n=0` on every dataset. `assess_dcv` now detects `co2` / `presence` / `count`
  (`demand_kind`), with `min_lift_people`.
- **A time clock was credited as DCV.** Occupancy follows the clock, so a valve opened on a
  schedule "responded" to presence (lift 0.245). Occupancy demand is now judged within each hour
  of day, weekdays and weekends apart; the scheduled valve drops to 0.087, the real responders
  keep 2–20 people of lift. OA never both raised and at its floor within an hour is
  `insufficient`, `reason="schedule_confounded"`.
- **The wildfire damper closure was invisible.** The below-floor check ran after the economizer
  and closed-damper exclusions, then vanished into `too_few_samples`. It now runs on every
  occupied sample first: the 2020 closure is a fault on the two units that fell below the 62.1
  requirement, and not on the two that stayed above it.
- **The worst outage read "not judged".** Fan off, CO₂ at the sensor's 2000 ppm full scale, 110
  occupied hours — excluded as closed-OA samples. New `unventilated_high_co2_hours`: a fault at
  ≥ 4 h (`unventilated_fault_hours`), judged by duration because an outage's share of a long
  dataset says nothing about its severity.
- **Brick chains attributed nothing.** The fleet rule took a zone's direct parent — in a Brick
  model the VAV, not the unit bringing in outdoor air — and joined 0 of 11 zones. It now takes the
  nearest ancestor with an OA signal: 11 of 11.
- **Duplicate zone timestamps crashed the fleet rule** (`cannot reindex on an axis with duplicate
  labels`, on raw 1-minute BMS data).
- **A trended occupancy point could only narrow the weekday 07–18 window**, so a 24/7 space lost
  two-thirds of its samples and the fleet rule dropped its working zones as "offset". The point now
  replaces the schedule; `start_hour` / `end_hour` / `occupied_days` set it otherwise
  (`schedules.occupied_mask` gains `days`).
- **A Celsius OAT excluded every sample** as possibly economizing, with no hint why. CAMBER
  temperatures are °F; the finding now says so when nearly everything is excluded.
- **Microsecond timestamps silently dropped rows.** Two regular indexes at `us` resolution (as
  parquet loads them) aligned to 10 rows instead of 3,013 under pandas ≥ 2; inputs are normalized
  to nanoseconds.

### Hardening — benchmarks and validation claims
- **The benchmark gates never saw a newly scored detector.** `check_against_baseline` only walked
  keys already in the baseline, so a metric the baseline had never seen was silently ungated — six
  synthetic-suite metrics (the three reset rules added in 0.55–0.56), fifteen BDG2 ρ metrics (0.80.0), and
  every LBNL metric, because **no LBNL baseline was ever committed**: CI's LBNL step took its "seed
  a baseline" branch on every run and gated nothing. The check now lists `unbaselined` metrics and,
  with `strict_new=True` (all four gate scripts), fails on them. `examples/lbnl_fdd/benchmark-baseline.json`
  is committed for the CI-fetched families; the synthetic and BDG2 baselines are refreshed (no
  existing value changed). New `eval.baseline_report` replaces four copies of the gate printout,
  one of which dropped missing metrics without naming them.
- **Declined drift cases were scored as correct negatives.** `driftvalidation.evaluate` treated a
  detector that could not test its claim (no fittable baseline, nothing scoreable) as "did not
  fire": a healthy decline became a true negative and a faulted one a miss. `duct_static_drift`
  declines all six LBNL cases and was reported as **FPR 0.0 / specificity** it never measured.
  Declines are now excluded and counted in `DetectorScore.n_declined`; an unmeasured FPR is
  omitted, not 0.0. Two plumbing tests had only passed because of this and now test a detector that
  actually scores.
- **The real-data drift validation was scoring the wrong things — four bugs in the benchmark, one in
  the library.** `docs/VALIDATION.md` had said "real TPR" since 0.64–0.66 without ever writing a
  number down; measured, every AHU and VAV drift detector scored **0**. Each zero traced to a defect:
  - **The fan-powered-box mapping pointed at a healthy box.** Its comment said the faults are imposed
    on the West zone (`_W`); diffing each faulted run against the fault-free one shows they are on the
    **South** box (`_S`). Remapped: `vav_airflow_drift` **0/4 → 3/3**, `vav_reheat_valve_drift`
    **0/5 → 1/2**, no false positives.
  - **Valves and dampers were read from their measured positions** where the datasets also carry the
    controller demand (`*_DM`). CAMBER's rules read these roles as commands, and a stuck device's
    position is exactly what hides its fault: against its position a stuck damper's OA fraction
    matches, so `economizer_damper_drift` scored **0/4**; against the command it scores **4/4**
    (11–17σ). All four LBNL mappings now use the demand columns; the main per-family scores are
    unchanged.
  - **Positives contradicted the detectors' documented design.** The coil-valve, reheat-valve and
    VAV-airflow drift detectors are one-sided *up* — the controller opening further for the same duty.
    A leaking or stuck-open valve does the opposite (the demand falls), yet it was listed as their
    target. Those faults are now cross-negatives, chosen from each rule's docstring rather than its
    results; `coil_valve_drift` becomes specificity-only (its targets aren't in the set) with **1 false
    alarm in 6** (a stuck-open OA damper's latent load, a confound the rule documents).
  - **The chiller dataset's wet-bulb and dry-bulb labels are swapped.** `OA_TEMP_WB` exceeds `OA_TEMP`
    in 97% of rows, and read as labelled the tower would cool water below wet-bulb half the time;
    read the other way its approach is 5.9–12.5 °F and never below. Remapped.
  - **Library bug: `TOWER_FAN_SPEED` (and `HW_PUMP_SPEED` and the three humidity roles) were never
    rescaled from 0–1.** `camber.units.PERCENT_ROLES` missed them though the sensor-health bounds
    treat them as percent, so a BAS trending tower fan speed as a fraction never cleared
    `cooling_tower_approach`'s "fan > 5%" gate and the rule never ran — on any site, not just here. A
    test now holds `PERCENT_ROLES` in step with the bounds. Running at last, the tower rule fired on
    every condenser-bypass-valve run — see the next entry.
  What is left is genuine: severe reheat-coil fouling leaves no trace in the box's trended points,
  a high-reading airflow sensor is out of a one-sided detector's scope, and duct-static drift still
  declines every case.
- **`cooling_tower_approach` called a healthy tower fouled whenever it held a minimum
  condenser-water temperature.** Plants keep condenser water above a floor (~60 °F) in cold weather,
  so the tower deliberately leaves water far above wet-bulb + design with its fan idling — a high
  approach that is correct control. The rule judged every fan-running hour, so it fired on all five
  LBNL condenser-bypass-valve runs (a leaking bypass keeps the tower running all winter at its floor,
  holding its setpoint to 0.0 °F with the fan at minimum). Approach is now judged only at **high fan
  effort** (`min_effort_pct=90`), the CTI basis for a tower's capability; a tower that never reaches
  it declines. Without a trended fan speed the old gate applies, with a caveat. The LBNL result:
  TPR 0/3, FPR 0/2, 6 declined (was FPR 4/7). Fouling is visible at high fan (20% of hours above
  design + 3 °F at 65% capacity vs 0% healthy) but the median-based severity stays `ok` — not tuned
  here, because tuning a threshold on the validation set would only fit it. `score_chiller` also
  stopped scoring declined runs as negatives, the bug fixed in `driftvalidation` above.
- `RELEASING.md` gains the two lessons of the 0.80/0.81 release: push stacked tags oldest-first and
  wait for each `image` job (`:latest` otherwise goes to whichever finishes last), and review the
  conda-forge autotick PR's dependencies. `deploy/conda/recipe.yaml` is now documented as a mirror of
  the feedstock's recipe and synced to 0.81.0.

### Notes
- **Validated on simulation and on open real-building data** — see the real-data pass below and
  `docs/VENTILATION.md#validation-and-limits`. No labeled DCV-fault dataset exists, so detection is
  established on one lab room with a known DCV law, not a sample.
- **Pearson correlation was not the main defect.** In simulation an integral loop still correlates
  at 0.48–0.66 once the economizer and closures are excluded. The economizer, closed-damper samples
  and outliers were what broke the old check.
- **Without `ECON_CMD`, expect `insufficient` more often.** The OAT-only fallback discards all mild
  weather. That is the honest answer.
- A fixed OA damper on a variable-speed fan moves OA flow with fan speed, which tends to follow
  occupancy; that can mimic DCV and is not detected.
- The system-level ASHRAE 62.1 VRP (the shipped check compares an air handler's OA to one zone's
  requirement) is next.
- Six new public names → `tests/public_api_snapshot.json` regenerated. No new dependency.

## [0.81.0] — 2026-09-13

**Hourly NMEC, and three documented claims that were false.** 0.80.0 fixed the savings-uncertainty
kernel; that was the real blocker for an hourly path. This wires one up — and fixes the claims that
said it already existed.

### Fixed
- **`docs/VALIDATION.md` said "Change-point / TOWT models report … fractional savings uncertainty
  with every saving".** False for the TOWT half: *no code path anywhere* could produce an FSU from a
  TOWT model. `TOWTModel.predict(index, temp)` takes two arguments while all five savings consumers
  call `predict(T)` and coerce with `np.asarray(..., dtype=float)` first, so the timestamps could
  not be passed at all.
- **`camber/mandv/normalized.py` claimed it "operates on any model with a `predict(temps)` method
  (e.g. a change-point or TOWT model)".** Passing a TOWT model raised `TypeError`. No test covered
  it, so nothing caught it.
- **`docs/MANDV.md` equated `mandv.towt` with CalTRACK Hourly.** It is a different estimator; the
  page now carries the differences table.
- **`TOWTModel.predict` silently produced a wrong baseline on unseen hours.** The one-hot design has
  a column per hour-of-week bin *observed at fit*, so a bin the baseline never saw yielded an
  all-zero row and the prediction collapsed to the temperature term. Measured: a weekday-only fit
  projected onto a weekend predicts **−1.2** where the truth is **40** — a negative baseline, which
  reads downstream as a large negative saving, with no error and no NaN. It now raises, naming the
  bins and the row count. Returning NaN was rejected: those hours would vanish from a savings sum
  without saying so.
- **The daily `caltrack_savings` judged its fit by the 0.20 default** rather than
  `cv_rmse_max_for("daily")` = 0.30, then discarded the verdict entirely. Now judged against the
  right gate and surfaced as `baseline_accepted` / `cv_rmse_max`.

### Added
- **`caltrack_savings_hourly`** → `HourlyNMECResult`: hourly NMEC / IPMVP Option-C on a TOWT
  baseline, reusing `intervalfit.hourly_energy_vs_temp` (which had zero production callers until
  now) and 0.80.0's corrected kernel, ρ estimator and df-aware t-table.
- **`towt.TOWTAtIndex`** — binds a reporting index so a TOWT model satisfies the one-argument
  `predict(temp)` contract, unblocking all five consumers with no signature change to any of them.
- **`TOWTModel.covers(index)`** — ask before projecting.

### Notes
- **Sufficiency is coverage, not row count.** A TOWT design has a column per hour-of-week bin, so a
  bin seen once is a fitted level carrying no information. `fit_towt`'s own 50-observation guard
  cannot be the constraint — 50 hours cannot populate 168 bins. `min_hours` **and**
  `min_obs_per_bin` must both pass.
- **Expect a band comparable to the daily method, not tighter.** Hourly residuals are strongly
  serially correlated. Measured on a 20-week synthetic: n=3360 hours → n_eff=282 at ρ=0.85, and the
  band goes 1.6% (ρ=0) → 9.7% (ρ=0.85). More data at a finer interval does not buy proportionally
  more certainty; a band that ignored this would be the overconfident one.
- **Non-routine screening is day-level.** Hourly residuals are heavier-tailed, and trimming
  individual hours on residual magnitude would bias CV(RMSE) down and so *narrow* the band — the
  dishonest direction. Whole days are excluded or none.
- **Not CalTRACK Hourly, and does not claim to be.** That specification prescribes per-calendar-month
  segmented models, six fixed temperature bin edges and residual-derived occupancy; CAMBER's TOWT is
  pooled, quantile-spaced and load-median. Same posture the daily method already takes.
- Three new public names → `tests/public_api_snapshot.json` regenerated. No new dependency.

## [0.80.0] — 2026-09-13

**Every fractional savings uncertainty CAMBER has ever reported was wrong.** The ASHRAE G14 Annex-B
bracket was transcribed with `n′` where the published form has `n/n′`. One substitution, two
defects, three copies.

**The band was √n too wide.** At ρ=0 the bracket became `(n/m)(1+2/n)` instead of `(1+2/n)/m` — a
factor of `n` under the root. On a near-perfect fit (CV(RMSE) 2.87%, 365 baseline days, 90 reporting
days, clean 20% saving) CAMBER reported **19.9% ± 60.4%**; the published form gives **± 3.1%**. The
ratio is 19.105 against √365 = 19.105. A textbook-quality M&V result was being reported as
statistically unusable.

**And the autocorrelation correction ran backwards.** `(n′/m)(1+2/n′)` collapses algebraically to
`(n′+2)/m`, strictly *increasing* in `n′` — so passing `rho`, documented as
"autocorrelation-adjusted", made the band **narrower**. Nothing ever passed it, which is the only
reason this did not compound: `caltrack_savings` and `isolation_savings` had no `rho` parameter at
all, and the one site that could pass it forwarded its own `0.0` default.

### Fixed
- **`mandv.stats.avoided_energy_savings`** now computes
  `t·1.26·CV·√((n/n′)·(1+2/n)·(1/m))/F`, with `(n/n′)` written as a literal factor so the expression
  reads term-for-term against the published one. More autocorrelation now correctly *widens* the
  band.
- **`mandv.normalized`** had a second, independent copy of the same bracket with no `rho` hook at
  all; **`mandv.rc_model`** (Option D) imported it and called it with `n == m`, which collapsed the
  bracket to ≈1 and left Option D's band with **no sample-size content whatsoever**. Both now use a
  shared kernel.
- **The t-table silently substituted.** `stats.py` held three confidence levels and fell back to
  1.645 for anything else, so `confidence=0.99` quietly returned a **90%** band; `normalized.py`
  had a different five-entry table. Now one df-aware table that raises `ValueError` on an
  unsupported level. (A monthly model at df=7 needs t=1.895, not 1.645 — a 15% understatement in
  the dishonest direction.)

### Added
- **`mandv.stats.lag1_autocorrelation`** — the ρ the correction needs, estimated from residuals.
  Returns `None`, never `0.0`, when it cannot be estimated: `0.0` asserts independence nobody
  tested. Non-finite residuals invalidate the pairs on both sides; pass a `time_index` and only
  pairs one modal interval apart are admitted, so a gap never glues its neighbours together.
  Negative estimates are clamped to 0 — a noisy negative would narrow the band.
- **`FitStats.rho_lag1`** and a keyword-only `fit_stats(..., time_index=)`. Required rather than
  cosmetic: `fit_stats` drops non-finite rows, which compresses the array and would otherwise make
  non-adjacent residuals look neighbouring.
- `caltrack_savings` estimates ρ from its baseline residuals and applies it automatically;
  `SavingsResult` gains `rho`, `n_effective` and `fsu_autocorrelation_adjusted` so a reader can tell
  an adjusted band from an unadjusted one. `NMECResult` gains `baseline_rho`.
- `examples/bdg2` now reports the **measured** lag-1 residual autocorrelation across ~2,044 real
  meters (`rho_metrics`: median, p10/p90, fraction above 0.3). Reported and archived but deliberately
  **ungated** — a descriptive distribution is not a pass/fail quantity.

### Notes
- **Two kernels, and the boundary is physical, not modular.** `measured − projected` carries the
  reporting period's residual noise, which averages down over `m`. `projected − projected` (NAC,
  Option D) contains no measured energy — only parameter error, shared by every projected period —
  so its band does **not** depend on how many periods you project onto. Measured directly, the
  spread of a projected total is flat across a 730× range in `m`. An earlier draft of this fix used
  one shared kernel; that would have been ~4× too **narrow** at 8760 hourly periods, replacing a
  conservative error with an overconfident one in a documented case.
- **Different provenance, stated.** The measured kernel is the published G14 expression, `1.26` and
  all. The projected kernel is `CV·√(p/n)` — plain OLS average leverage, citable as regression
  theory. G14's empirical constant is deliberately *not* carried across to a case it was never
  derived for.
- **Verified two ways**, neither of them the paywalled standard: reconstruction from Reddy & Claridge
  (2000) as reproduced in the public BPA/LBNL/NYSERDA M&V guides, and an independent Monte Carlo of
  AR(1)-residual fits. Consistent with the project's clean-room rule; say so rather than implying
  the standard was read.
- **Why the tests did not catch it:** every existing uncertainty assertion was `> 0`, `isfinite`, or
  monotonic in the savings fraction. All of them pass both before and after a 19× correction. They
  are replaced with pinned magnitudes, a pinned ρ direction, an m-scaling test, and a test that the
  projected kernel is invariant to the projection length.
- Out of scope and named: the Sun & Baltazar polynomial refinement of `1.26`; autocorrelation in the
  CUSUM/online control limits (a different literature); an exact design-matrix projection
  uncertainty to replace the `√(p/n)` approximation.
- One new public name → `tests/public_api_snapshot.json` regenerated. No new dependency.

## [0.79.0] — 2026-09-09

**A healthy duty-cycled point could silently switch a diagnostic off.** `ingest.quality.assess`
assumes a series is one population, so an intermittent signal — an HHW BTU meter near zero except
during heating events, a lead pump, a 0/1 status — has every legitimate burst counted as an outlier.
That score *is* the trust score (`sensorhealth.py`: `trust = q.score * (1 - rng_pen)`), and a role
below `trust_gate.min_trust` makes a rule **decline to fire**. So the layer built to prevent false
negatives was manufacturing them.

Measured on a healthy synthetic meter (720 hourly samples, no faults injected), the score was not
merely low but **chaotic** — 0.166 to 0.998 with no monotonic relation to duty cycle, because
`_mad_z` switches between its MAD branch and its meanAD fallback as the median crosses into the "on"
band. Four of seven duty cycles landed under 0.5. A building's lead pump and its standby pump could
score 0.166 and 0.998 on identical health. Five rules take a **status** role as *required*
(`boiler_summer_lockout`, `boiler_short_cycle`, `compressor_short_cycle`, `compressor_staging`,
`hw_plant_deltat`), so the reach went well past BTU meters.

### Fixed
- **`assess` now reads two-regime structure** and reports the outlier count judged *within* each
  regime. A duty-cycled point scores ~0.998 across the whole 0.10–0.80 duty range instead of
  swinging 0.166–0.998, and nothing in that sweep is gated at `min_trust=0.5`.
- `sensorhealth.sensor_trust` opts in per role via a new `_INTERMITTENT_ROLES` allow-list (BTU/flow/
  airflow/power/stage roles, unioned with `STATUS_ROLES`), mirroring the existing `_SENSOR_ROLES`
  precedent. An HHW meter goes `0.741 / suspect / ["outliers"]` → `0.998 / trusted /
  ["intermittent"]`.

### Added
- `QualityReport.n_regimes` / `regime_threshold` / `n_regime_outliers` / `regime_outlier_frac`,
  tri-state per the honesty convention: `None` means the split could not be **tested**, never "no
  split found".
- `assess(..., *, regime_aware=False)` — keyword-only and defaulted, so the stable import path is
  unchanged.
- Two `sensor_trust` flags: `intermittent` (an expected duty cycle; explains why outliers were read
  within-regime) and `bimodal` (a point that should have one population has two — new information
  the tool did not previously surface, carrying no penalty).
- An opt-in `regime_outlier_frac` metric in the pattern-I quality dashboard, kept out of the default
  set so existing dashboards are unchanged; a metric that cannot be computed now renders blank
  rather than raising.

### Notes
- **A split is claimed only on mass *and* separation *and* temporal coherence.** Mass is what keeps
  spike detection intact — a lone spike is ~5% of the samples and never qualifies, so
  `[10.0]*20 + [10000.0]` still flags exactly one point. Coherence is what stops the fix becoming a
  masking bug: a flow meter randomly railed to zero for 30% of the record has median run length 1,
  is refused a split, and stays `untrusted` at 0.397. A real duty cycle persists (4–48 samples).
- **The pooled `n_outliers` / `outlier_frac` never change meaning and are never masked** — that is
  what keeps a two-regime read visible next to the plain one.
- `regime_aware` is **off by default** and enabled per role, because scoring a duty cycle as normal
  is the direction that could hide a fault; a role-blind caller therefore cannot mask anything.
  Flipping the default is a follow-up once there is field evidence.
- **Deferred, and pinned by tests so they stay visible:** `anomaly.detect_anomalies` still returns
  `fault` on a healthy intermittent meter — it is role-blind *and* its point test independently
  counts every burst, so fixing the quality door alone does not fix it. An in-range sensor railing
  between two plausible values in long blocks still scores ~0.99 (`longest_flatline` measures the
  longest single run); this change neither creates nor closes that gap, but `n_regimes` now makes
  the structure visible. A contiguous rail on an allow-listed role remains undecidable from values
  alone — the house answer is to corroborate with a mapped status point.
- No new dependency; no public-API surface change (the new names are private, the new fields are
  dataclass attributes, the new parameter is keyword-only) — `tests/public_api_snapshot.json` is
  unchanged and `tests/test_public_api.py` passes unmodified.
- **Packaging, landed after the tag was cut:** `deploy/conda/recipe.yaml` and the conda-forge
  submission (`conda-forge/staged-recipes#34742`) were retargeted from 0.74.1 to 0.79.0, with the
  sdist `sha256` verified by hashing the published artifact rather than reading the PyPI API.
  Runtime dependencies are unchanged across 0.75–0.79, so only the version, hash and documentation
  URL moved; `linux_64` / `osx_64` / `win_64` all build green. The recipe's `documentation` now
  points at the MkDocs site instead of `github.com/.../blob/main/docs` — the old link worked (it
  301s to `/tree/`) but it is a file browser that also surfaces `docs/dev/` and `docs/proposals/`,
  internal notes the docs site deliberately excludes.
- **Release-process fix:** `camber/__init__.py` had been left at `0.74.1` while `pyproject.toml`
  advanced through 0.75–0.79, so every commit in the stack was internally inconsistent. Caught
  during the pre-release checks and corrected before any tag was pushed; `tests/test_public_api.py`
  now fails if the two ever drift again.

## [0.78.0] — 2026-09-08

**Every drift finding now renders its own evidence.** 0.77.0 built `fitted_band` — a baseline's own
fitted line as a plottable band — but nothing was connected to it: of the ~20 drift rules, **none**
had an `evidence()` hook, so pattern J fell back to a default multitrend of the raw roles. For a
drift detector that default is actively misleading: it shows the *levels* and hides the *movement*,
which is the only thing the rule claims.

### Added
- **`camber.charts.evidence.drift_evidence(rule, equip, frame)`** — builds the chart a drift rule
  actually reasons about: the current period scattered on its frozen baseline's band. Duck-typed
  over two new methods every drift rule now declares — `drift_signature() -> (kind, load_col,
  metric_col)` and `drift_frame(frame)` — so one implementation serves the whole family and a new
  detector opts in by declaring them.
- **`Evidence.frame`** — a prepared frame to render instead of the caller's role-frame. Most drift
  baselines are fitted on *derived* columns (a chiller's `tons`, a coil's air-ΔT) that no raw
  role-frame carries, so the rule hands over the frame it actually fitted.
- **`camber drift report --charts`** — embeds those charts in the drift page, and
  `run_drift(..., evidence=True)` / `run_drift_config(..., evidence=True)` build them. Off by
  default: it re-resolves each equipment's current window, which a scoring run doesn't need, and the
  plain page stays pure text and tables with no matplotlib import.
- `finding_evidence` tries the drift band before the default trend, so the dashboard picks it up too.

### Fixed
- **`fitted_band` was never exported from `camber.charts`** (0.77.0 added it to
  `camber.charts.diagnostic` only, unlike its four sibling constructors). Now exported.
- **Three rules unwrapped their prepared frame wrongly** — `economizer_damper_drift`,
  `vav_airflow_drift` and `vav_reheat_valve_drift` call a `_prepared()` that returns
  `(frame, excluded_fraction)`, and took the tuple. Found by an AST check across all 20 rules after
  the first one showed up as a silently missing chart, then pinned by a per-family coverage test.

### Notes
- **A rule with nothing frozen produces no chart, deliberately.** A scatter with no band invites the
  reader to judge the cloud by eye — precisely the comparison the frozen baseline exists to make.
- The charts separate the cases rather than decorating the verdict: on the `examples/drift` site an
  injected loading filter reads **100%** of the period outside the band (and fan power **93%**, the
  corroboration the roll-up reports), while the healthy unit sits at 3–6% — about what you expect
  outside ±2σ.
- Two new public names → `tests/public_api_snapshot.json` regenerated. No new dependency; matplotlib
  stays lazy-imported and is not touched unless `--charts` is passed.

## [0.77.0] — 2026-09-08

**A drift baseline becomes something you can plot.** The drift detectors score residuals against a
frozen, load-normalized line — but that line had no renderer, so the comparison stayed inside the
rule and every drift finding's evidence had to be described rather than shown. The chiller
drift-detection plan called for a `fitted_band` constructor and it was the one line item never built.

### Added
- **`camber.charts.diagnostic.fitted_band(baseline, x, y, *, k=2.0, within_envelope=True, …)`** — a
  pattern-G `DiagnosticTemplate` whose expected band is a frozen `LoadBaseline`'s own fitted line ±
  `k` residual sigmas. Rebuild a model from the baseline store, hand it to `diagnostic_scatter`, and
  the current period is plotted against exactly what the detector compares it to.

### Notes
- Every other template constructor here encodes a band someone *designed* (a reset schedule, a high
  limit); this is the first that encodes one the equipment *earned*.
- **Outside the fitted load envelope the band is `NaN`.** Two things follow, both deliberate: the
  shaded region shows a visible gap where there is no claim, and a point out there is **not** counted
  as a violation — judging a reading against an extrapolated fit is the same asserted negative the
  drift rules refuse to make. `within_envelope=False` opts into extrapolating.
- `k` is a band *width*, not a severity threshold: the detectors' own sigma floors decide what warns
  or faults, and those remain screening-grade (`camber.driftthresholds`).
- One new public name → `tests/public_api_snapshot.json` regenerated. No new dependency; matplotlib
  stays lazy-imported.

## [0.76.0] — 2026-09-08

**Moving a drift baseline becomes a signed operator decision you can run.** 0.75.0 made the drift
family reachable and gave it a create path (`camber drift freeze`); the reference could still only
be *moved* from Python. This adds the second write verb and the runnable walkthrough that shows why
there are two.

### Added
- **`camber drift accept <config> --equip EQ --by NAME --reason TEXT`** — supersede a frozen
  baseline with one re-fit over an acceptance window. `--by` and `--reason` are `required=True` at
  the argparse level, so the command exits 2 before any code runs without them
  (`BaselineStore.accept_new_normal` re-rejects an empty one as the backstop). `--equip` is
  repeatable and required — there is no blanket "accept everything". `--kind` narrows further,
  `--period START END` picks the re-fit window (default: the config's `drift.current`, because
  accepting a new normal means *what it is doing now is the reference*), and `--dry-run` shows the
  moves without writing. Prints `frozen_at <old> -> <new> (supersedes …, history now N)` per record.
- **`camber.driftrun.refit_baselines`** and **`accept_new_normal_from_periods`**, plus
  **`config.drift_refit`** which re-fits every configured family over one window.
- **`examples/drift/`** — a data-free, network-free walkthrough (`make_data.py` + `mapping.json` +
  `config.json` + README) over three air handlers: one with an injected loading filter, one healthy,
  and one whose points never resolve. It runs the whole loop `freeze → run → accept → run` and ends
  with the drift cleared, which is the point of the third verb.

### Notes
- **The re-fit is the detector's own.** Rather than reimplementing each rule's fit, `refit_baselines`
  runs the family against a **scratch in-memory** `BaselineStore` with `freeze_if_missing=True` and
  harvests what it froze. Every model therefore comes from the rule it will later be compared
  against — same metric and load columns, same minimum-load filter, plausibility bounds and
  running-status gate — and cannot drift out of sync with it. A detector that cannot fit over the
  window produces no entry, and `accept` says so (`could not refit … — leaving it frozen`) instead
  of substituting something.
- No new dependency; four new public names → `tests/public_api_snapshot.json` regenerated.

## [0.75.0] — 2026-09-08

**The drift family becomes reachable without writing Python.** Releases 0.40–0.67 built ~20 drift
detectors across six families (AHU air-side, chiller, condenser, evaporator, pump, VAV) with
per-family roll-ups and HTML tables — but they are `PeriodRule`s, so they never appeared in the
single-frame `builtin_registry()` that config-driven runs and the CLI use. There was no `drift`
config section and no `camber drift` subcommand: the largest capability in the toolkit could only be
driven from a script. This adds the missing layer — *equipment discovery → period slicing → suite →
roll-up → output* — plus the baseline lifecycle the comparison depends on.

### Added
- **`camber.driftrun`** — the family runner and the single source of truth for suite membership.
  `DRIFT_FAMILIES` / `DriftFamily` / `family_names()`, `build_drift_suite(family, store, …)`, and
  `run_drift(refs_by_class, mapping, *, store, families, baseline, current, …)` → `DriftResult` /
  `DriftFamilyResult`. The six `build_*_suite` helpers in the `*sim` modules now delegate here, so
  the production path and the physics-validation path cannot diverge (a parity test pins the exact
  classes and ordering each one returns).
- **A `drift` section in the JSON config** — `store` / `baseline` / `current` / `run_id` plus a
  `families` list (`class`, `family`, and the optional `coils` / `plant` / `sustained_alarm` and
  per-family window overrides). `camber run` scores drift alongside the ordinary rules and folds the
  verdicts, caveats and tables into the audit report. New `config.run_drift_config` and
  `config.drift_store_path` run or locate just the drift half.
- **`camber drift run | report | freeze | list`** — score a current window against the frozen
  baselines, write the standalone HTML page, establish missing references (`--dry-run` shows what it
  would do), and inspect what is frozen and on whose say-so (`--equip` / `--kind` / `--json`).
- **`camber.report.drift_report_html`** — one page composing every family's existing verdict table,
  the plant roll-up, the threshold-confidence banner, and an *Equipment not evaluated* table. Plus
  `threshold_confidence_html()`.

### Fixed
- **Scorecard category parity.** Twenty-eight shipped rules had no `RULE_CATEGORY` entry and fell
  through to `"other"` — including four drift rules (`chiller_superheat_drift`,
  `chiller_suction_pressure_drift`, `chiller_head_pressure_drift`, `cooling_tower_approach_drift`)
  whose four siblings *were* mapped to `"maintenance"`, so half the chiller drift family scored
  outside the maintenance grade. All are now categorized (the drift family as `maintenance`, the G36
  Trim-&-Respond reset family as `energy`), and a new parity test fails if any shipped rule is added
  without one.

### Notes
- **The write policy is a verb, not a setting.** Every drift rule defaults `freeze_if_missing=True`
  and writes the reference inline, so a scheduled run would quietly mint baselines from whatever
  window the config called "baseline" — circular by construction. `run`/`report` and the config
  section always pass `False` and never save; only `freeze` creates, and it still refuses to
  overwrite. Moving a reference remains `BaselineStore.accept_new_normal`'s attributed decision.
- **Untested is not steady.** Two paths leave an equipment unscored — no required role resolved, or
  every detector declined (nothing frozen, an untrusted input, an empty window) — and both would
  roll up to `severity="ok"`, `locus="steady"`, asserting a negative nobody tested. Neither is
  diagnosed now: the equipment is listed under `unevaluated` with the reason, in the terminal, in
  `drift.json` and in the HTML, and the no-role case also emits an `info` Finding so it reaches the
  audit report's caveats.
- **Screening-grade, un-suppressibly.** Every drift command prints and every drift page renders the
  two-class threshold note from `camber.driftthresholds`; `drift.json` carries the block. There is
  no flag to hide it.
- Two same-named `coil_valve_drift` instances (cooling + heating) cannot share one `Registry`, which
  keys on `rule.name` — each rule runs through its own one-entry scratch registry, reusing all of
  `run_periods` unchanged. No change to `camber/rules/base.py`.
- Six new public names across two new modules → `tests/public_api_snapshot.json` regenerated. **No
  new dependency** (stdlib `json`/`argparse`/`html` + the existing numeric stack).

## [0.74.1] — 2026-09-08

**Fix: `camber` CLI crashed on a legacy Windows (cp1252) console.** The help text and finding
summaries contain non-ASCII characters (`—`, `°`, `→`); on a non-UTF-8 console, argparse writing
`--help` (which lists the `edge` subcommand, whose help contains `→`) raised `UnicodeEncodeError`.
`main()` now reconfigures `stdout`/`stderr` to UTF-8 (with a `backslashreplace` fallback) before
parsing, so every CLI code path is encodable on any platform.

### Fixed
- **Windows CLI portability** — `camber --help` and all CLI output no longer crash on a `cp1252`
  console. Caught by the conda-forge `win_64` build; regression-tested against a simulated cp1252
  stdout in `tests/test_cli.py`.

### Notes
- Patch release: no public-API change (`_ensure_utf8_streams` is private) → snapshot unchanged, no
  new dependency. The `deploy/conda/recipe.yaml` and the conda-forge submission bump to 0.74.1.

## [0.74.0] — 2026-08-30

**A second weather provider: real NOAA weather stations.** NASA POWER (0.70+) is a global ~50 km
reanalysis grid; this adds **NOAA's Integrated Surface Database (ISD-Lite)** — also keyless — so you
can pull from a *real weather station* near the site. `oat_reference_isd` finds the nearest station
covering your window and returns the identical °F `oat_f` Series the weather-normalization and
OAT-sensor-validation consumers already accept, so it drops into `sensordrift.compare_to_reference`
and `mandv.weather` unchanged.

### Added
- **`oat_reference_isd(lat, lon, start, end, *, transport, catalog_transport, tz, timeout)`** — the
  station-precise counterpart to `oat_reference`: resolves the nearest covering station, fetches its
  hourly °F, and attaches the resolved station to `series.attrs["isd_station"]`.
- **`isd_nearest_station(lat, lon, start, end, *, transport, stations, timeout)`** — haversine-nearest
  station **filtered to those whose record spans the window** (skips decommissioned / not-yet-begun);
  raises a clear `ValueError` when no station covers it.
- **`isd_stations(*, transport, timeout)`** → `list[IsdStation]` (the parsed catalog; null-island
  `0.000/0.000` rows skipped) and **`fetch_isd(usaf, wban, start, end, *, transport, tz, timeout,
  dew_point)`** → a `DataFrame` of one station's hourly °F (per-year gz files concatenated to a unique,
  sorted index; tenths-of-°C → °F; `-9999` → `NaN`; optional `dewpt_f`).
- **`IsdStation`** (frozen value, `.as_dict()`), **`isd_transport(*, timeout)`** (the default stdlib
  **bytes** transport), and **`cached_bytes_transport(inner, cache_dir, *, ttl, clock)`** — the bytes
  sibling of `cached_transport` (ISD payloads are gzipped/CSV, not JSON), memoizing to
  `<cache_dir>/<sha256(url)>.bin` with the same atomic-write / self-healing / injectable-clock TTL
  behavior.

### Changed
- `docs/WEATHER.md` becomes a two-provider guide (NASA POWER + NOAA/ISD), with a "NOAA/ISD station
  data" section on the station-precise-but-gappy trade-off, endpoints, tz caveat, and bytes caching.
  `docs/CAPABILITIES.md` and the mkdocs nav name both providers.

### Notes
- Seven new public names under `camber.weather_source` → `tests/public_api_snapshot.json` regenerated.
  **No new runtime dependency** (stdlib `urllib`/`gzip`/`csv`/`math` + pandas). ISD is a **second
  transport seam** (bytes, vs. the NASA path's JSON) — both offline-injectable, so every parse /
  nearest-station / tz / missing-value / cache path is tested with no network. **Honest trade-off:**
  ISD is station-precise but gappy and sparse (no station near remote sites); NASA POWER stays the
  choice for global coverage and a gap-free series. They complement.

## [0.73.0] — 2026-08-25

**Fetch weather by address, not just coordinates.** Adds a keyless geocoder to `camber.weather_source`
so you can specify a location by place name; NASA POWER is a lat/lon point query, so an address is
geocoded to coordinates first.

### Added
- **`geocode(address, *, transport, limit, user_agent, timeout)`** → a frozen **`GeoResult`**
  `(latitude, longitude, display_name)` (the display name lets you confirm the match) via OpenStreetMap
  **Nominatim** — also free and keyless. Plus `nominatim_url(...)` (pure builder) and
  `nominatim_transport(...)` (default stdlib transport that sends the policy-required `User-Agent`).
- **`oat_reference_for(address, start, end, *, tz, geocode_transport, transport, ...)`** — a
  convenience that geocodes then fetches, returning the same °F OAT Series as `oat_reference` (with the
  resolved place on `series.attrs["geocode"]`). Two transport seams, both offline-injectable.

### Changed
- `docs/WEATHER.md` gains a "Geocoding — fetch by address" section (the keyless/User-Agent/rate-limit
  policy, the ~50 km-grid precision honesty, and the still-explicit `tz` caveat).

### Notes
- New public names (`GeoResult`, `geocode`, `nominatim_url`, `nominatim_transport`,
  `oat_reference_for`) → `tests/public_api_snapshot.json` regenerated. **No new runtime dependency**
  (stdlib `urllib`/`json`). `geocode` composes with `cached_transport` (satisfies Nominatim's
  cache-your-lookups policy). `tz` is not derived from the address — still an explicit param.

## [0.72.0] — 2026-08-25

**A live web dashboard served by the read-only API.** The self-contained HTML dashboard is a one-shot
snapshot; this adds its live counterpart — a framework-free page that fetches the running store and
polls, so views refresh as new data lands. Bridges the two halves that already existed (the stdlib
read-only API and the `window.CAMBER` cross-panel selection bus) with no new dependency.

### Added
- **`camber.api.ui.live_dashboard_html()`** + a **`GET /ui`** route on `ReadAPIHandler` — a single
  vanilla-JS page (inline JS/SVG, no framework, no CDN) that fetches `/facilities`/`/points`/`/history`
  same-origin, renders facility/equip/role selectors + a synchronized multitrend, brush-links via the
  shipped `window.CAMBER` bus, and **polls** (default 15 s, adjustable, with a Live toggle). The JSON
  endpoints are unchanged; the HTML route carries a strict same-origin `Content-Security-Policy`.
- **`camber serve <store> [--host --port]`** CLI verb — starts the read-only API + live `/ui`.

### Changed
- `dispatch` now returns HTML (a `str`) for `/ui` and JSON (a `dict`) for every other route; the
  handler branches content-type accordingly. `docs/VISUALIZATION.md`, `CAPABILITIES.md`, `CLI.md`,
  and `SECURITY.md` document the live UI + its read-only/localhost/CSP posture.

### Notes
- Read-only (GET-only) and `127.0.0.1`-bound by default; no auth (documented). New public module
  `camber.api.ui` → `tests/public_api_snapshot.json` regenerated. No new runtime dependency (stdlib
  `http.server` + vanilla JS). A live carpet panel + agent narration remain nice-to-haves.

## [0.71.0] — 2026-08-25

**Complete the weather-fetch adapter: multi-year requests + an on-disk cache.** Rounds out
`camber.weather_source` (0.70) with the two pieces its docs flagged as deferred, staying entirely
within the stdlib-only, injectable-transport, offline-testable design.

### Added
- **Transparent multi-year chunking** in `fetch_nasa_power` — NASA POWER caps a single hourly request
  at ~1 year, so the request is split into **calendar-year** chunks (one call per year) and
  concatenated into a single unique, sorted hourly index. A multi-year request just works; the public
  signature is unchanged. Calendar-year seams share no day, so no hour is duplicated or dropped.
- **`camber.weather_source.cached_transport(inner, cache_dir, *, ttl, clock)`** — a dependency-light
  on-disk cache decorator that composes with any transport (default or test double). Memoizes each
  URL's parsed JSON to `<cache_dir>/<sha256(url)>.json` with an atomic write; corrupt files self-heal;
  cache-forever by default (historical reanalysis is stable) with an optional `ttl` for revised recent
  months; `clock` is injected so TTL expiry is deterministic in tests.

### Changed
- `docs/WEATHER.md` documents multi-year requests + the on-disk cache (drops the two "not yet here"
  notes; NOAA/ISD station ingest remains a deferred future arc).

### Notes
- New public name `cached_transport` → `tests/public_api_snapshot.json` regenerated. No new runtime
  dependency (stdlib `urllib`/`json`/`hashlib`/`os` + pandas). Existing 0.70 API + tests unchanged.

## [0.70.0] — 2026-08-25

**A live weather-fetch adapter (NASA POWER).** CAMBER could weather-normalize M&V and validate an OAT
sensor against an external reference, but only from a *local* EPW/TMY file or a series you brought
yourself. This fetches one — hourly historical temperature (and optional RH) from NASA POWER, a free,
keyless, global reanalysis service — in the exact °F Series shape those consumers already accept.

### Added
- **`camber.weather_source`** — `oat_reference(lat, lon, start, end, ...)` → a °F OAT Series
  (`name="oat_f"`, matching `mandv.weather.load_epw`) that drops straight into
  `sensordrift.compare_to_reference` and M&V normalization; `fetch_nasa_power(...)` → a DataFrame
  (`oat_f` + optional `rh_pct`); `nasa_power_url(...)` (pure URL builder) and `nasa_power_transport(...)`
  (the default stdlib-`urllib` transport). Dependency-light with an **injectable transport** (mirrors
  `ingest.haystack.http_json_transport`), so every parse/unit/timezone/fill path is tested offline.
- **Timezone handling** is explicit (`tz="UTC"` tz-aware, or a site IANA zone → DST-correct naive-local
  that inner-joins to a BAS trend) — the one thing a naive fetch gets silently wrong, so it's tested
  for the exact UTC→local hour mapping. NASA's `-999` fill becomes `NaN`, not a bogus `-999 °C`.

### Changed
- New `docs/WEATHER.md` (+ nav); `docs/MANDV.md`, `docs/CAPABILITIES.md`, `ROADMAP.md` reference it.

### Notes
- New public module `camber.weather_source` → `tests/public_api_snapshot.json` regenerated. **No new
  runtime dependency** (stdlib `urllib`/`json` + pandas). No API key needed. On-disk caching,
  NOAA-station ingest, and multi-year chunking are noted as future work.

## [0.69.0] — 2026-08-24

**Option-D depth — a 2R2C thermal-mass grey-box, multi-zone calibration, and an EnergyPlus
cross-validator.** Extends IPMVP Option-D (`camber.mandv.rc_model`) beyond the 1R1C core, each addition
preserving its honesty invariant: grid the nonlinear time-constant(s), OLS the linear
conductances/gains, gate on ASHRAE G14, no scipy. The 1R1C path is unchanged.

### Added
- **2R2C** — `RC2Model(ua_env, uc_mass, gain_eff, tau_air, tau_mass, w)` + `calibrate2(...)`: a slow
  thermal-mass node coupled to the air node, capturing the post-re-entry recovery tail a single `tau`
  can't. Grids `(tau_air, tau_mass, w)` and OLS-fits the conductances, with `p=6` in the G14 gate. On
  mass-dominated data it beats 1R1C on CV(RMSE) (the test that earns the complexity).
- **Multi-zone** — `calibrate_zones(oat, schedules, metered_energy, *, order=1|2)` +
  `MultiZoneModel`/`ZoneModel`: several zones whose hourly predictions sum to the whole-building meter,
  calibrated by stacking their basis columns into one OLS. Reuses `option_d_savings` unchanged (pass
  per-zone schedule dicts). Candid about the whole-building split being under-determined without
  differing schedules or sub-metering.
- **`camber.interop.energyplus.compare_option_d`** (`[energyplus]` extra, `eppy`) — runs a
  user-supplied IDF under as-found/as-corrected control and diffs its avoided energy against the
  grey-box saving, returning an `agreement` block. The runner is injectable, so the compare logic is
  fully tested without the E+ engine (own-it-then-cross-check, like the pvlib/BETTER bridges).

### Changed
- `docs/OPTION-D.md` gains 2R2C / multi-zone / EnergyPlus sections (dropping the "out of scope" note);
  `docs/MANDV.md`, `docs/CAPABILITIES.md`, `ROADMAP.md` reference the depth.

### Notes
- New public names (`RC2Model`, `calibrate2`, `ZoneModel`, `MultiZoneModel`, `calibrate_zones`, and the
  `camber.interop.energyplus` module) → `tests/public_api_snapshot.json` regenerated. The 2R2C +
  multi-zone core adds **no** runtime dependency (numpy only); `[energyplus]` is an optional extra.

## [0.68.0] — 2026-08-24

**A unified validation & credibility dossier + a `camber validate` command.** CAMBER validates itself
four ways (synthetic whole-suite FDD, generated multi-zone fleet FDD, real-data FDD on LBNL, real-data
M&V on BDG2), each with its own benchmark and CI gate but no single artifact. This adds one — a
legible, sellable capstone across all four tracks, in text / self-contained HTML / JSON.

### Added
- **`camber/dossier.py`** — `build_dossier()` → `ValidationDossier` / `TrackResult`. It
  **live-recomputes** the two pure tracks (`faultlab`, `fleetlab`) on every run (deterministic, no
  download) and **cites** the two real-data tracks (LBNL FDD, BDG2 M&V) from committed reference
  figures with provenance + a reproduce command. Rates carry 95% Wilson intervals
  (`camber.validation`); each track shows a LIVE/CITED tag, coverage, and its honest boundary. The
  HTML is a single self-contained file (no external assets, pure-CSS CI bars, `camber.report`
  theme-safe style). No wall-clock timestamp — anchored on the package version, so builds are
  byte-identical.
- **`camber validate`** CLI verb — `--html` / `--json` / `--full`.
- **`tests/test_dossier.py`** — plumbing + two **anti-rot** cross-checks: the cited BDG2 figures must
  equal the committed `examples/bdg2/benchmark-baseline.json` exactly, and the cited LBNL figures must
  match the pooled OA-fraction row in `docs/VALIDATION.md` — a drift fails CI.

### Changed
- **`docs/VALIDATION.md`** gains a "The unified dossier — `camber validate`" section; `docs/CLI.md`,
  `docs/CAPABILITIES.md`, and `ROADMAP.md` reference the new verb.

### Notes
- New public module `camber.dossier` → `tests/public_api_snapshot.json` regenerated. No new runtime
  dependency (numpy/pandas + stdlib). Only CC-BY datasets already named in the docs are named.

## [0.67.0] — 2026-08-24

**Validation of the G36 reset fleet family on a generated labeled multi-zone fleet.** Closes the one
unfilled validation gap `docs/VALIDATION.md` carried: the multi-zone rogue-zone census,
cohort-starvation, and reset-effectiveness detectors could never be accuracy-scored on public data (no
vendorable labeled multi-zone-fleet fault dataset exists), so they are now scored on a fleet
*generated* from the public ASHRAE Guideline 36 Trim-&-Respond logic — never copying any encumbered
simulation.

### Added
- **`camber/fleetlab.py`** — a clean-room generator + validation harness. `generate_fleet(...)` emits
  a physically-coherent labeled fleet (per-zone role-frames whose requests aggregate into the T&R
  reset via `g36_reset.tr_simulate`, a served-by `Topology`, and a ground-truth `FleetLabel`) with one
  injected fault: a rogue zone, a starved cohort, or an inert reset in one of four G36 failure modes.
  `labeled_records`/`targets`/`attribution`/`coverage` score the six detectors (3 × SAT + static) with
  `camber.eval.benchmark`, adding an **attribution** rate (did it name the right zone / air handler /
  failure mode?) on top of TPR/FPR, with genuine cross-archetype negatives.
- **`examples/fleet_fdd/benchmark.py`** + committed `benchmark-baseline.json` — the CI-gated runner
  (`--json`/`--gate`/`--tol`/`--update-baseline`), deterministic, no download.
- **`tests/test_fleetlab.py`** + **`tests/test_fleet_benchmark.py`** — always-run plumbing: generator
  determinism, label correctness, per-archetype firing + attribution, fault-free/cross-archetype
  quiet, and the runner reproducing its committed baseline at `tol 0.0`.

### Changed
- **`docs/VALIDATION.md`** — the three `*_reset_effectiveness` / `*_rogue_zone_census` /
  `*_cohort_starvation` "synthetic-only (a real gap)" verdicts flip to scored, with a new
  fleet-validation section (mermaid, the attribution guard, the honest internal-validity framing, and
  the deferred Modelica Buildings cross-check — revised BSD-3, but needs a Modelica toolchain outside
  the dependency-light envelope). `ROADMAP.md` ticks the labeled-multi-zone-fleet item.

### Notes
- New public module `camber.fleetlab` → `tests/public_api_snapshot.json` regenerated. No new runtime
  dependency (numpy/pandas + stdlib). The encumbered ASHRAE chiller dataset is never named.

## [0.66.0] — 2026-08-24

**Real-data validation of the plant-level chiller detectors (LBNL chiller-plant subset).** Wires the
CC-BY LBNL chiller-plant subset into the benchmark, turning the chiller `chiller_efficiency` /
`cooling_tower_approach` detectors from synthetic-only into real-data-scored — the open, *simulated*
chiller FDD source that sidesteps a licence-encumbered ASHRAE chiller-FDD dataset.

### Added
- **`examples/lbnl_fdd/fetch.py --chiller`** — fetches the LBNL chiller-plant subset (basename-matched
  extraction) + **`mapping_chiller.json`** (chiller-1 power + CHW loop and tower-1 supply-temp +
  wet-bulb → roles; the plant-level points the runnable detectors need).
- **`examples/lbnl_fdd/benchmark.py` `score_chiller`** — scores **`chiller_efficiency`** (kW/ton;
  target = tower fouling / PID + three-way-bypass leak/stuck) and **`cooling_tower_approach`** (CW
  supply vs wet-bulb; target = tower fouling / PID) on the labeled physical faults. Because the
  simulated chiller/tower design curves aren't published, each detector's absolute design ceiling is
  **calibrated from the plant's own fault-free run** (commissioning practice) rather than guessed, so
  the informative number is the TPR on the faults; sensor-bias runs act as genuine negatives.
- **`tests/test_lbnl_benchmark.py`** — chiller calibrate-and-fire + metric-key + empty-baseline +
  registration plumbing tests on synthetic plant-shaped frames (always run).

### Notes
- The subset is **water-side only** — it exports no refrigerant-side points (evaporator/condenser
  approach, subcooling, superheat), so the refrigerant-side chiller-drift family stays synthetic-only.
  Documented in `docs/VALIDATION.md`; the encumbered ASHRAE chiller dataset is never named.
- No `camber/` change → public-API snapshot unchanged; new code is `examples/` + tests.

## [0.65.0] — 2026-08-24

**Real-data validation of the VAV zone-terminal drift family (LBNL Fan-Power-Unit subset).** Wires the
CC-BY LBNL FPU subset into the benchmark, turning two "synthetic-only" verdicts into real-data ones.

### Added
- **`examples/lbnl_fdd/fetch.py --fpu`** — fetches the LBNL Fan-Power-Unit subset (basename-matched
  extraction, robust to the archive layout) + **`mapping_fpu.json`** (the West-zone box + AHU points
  → roles; the West zone is the one faulted, so the other zones stay healthy).
- **`examples/lbnl_fdd/benchmark.py`** now scores **`vav_airflow_drift`** (target = damper-stuck +
  airflow-sensor-bias faults) and **`vav_reheat_valve_drift`** (target = reheat-valve leak/stuck +
  coil-fouling) on the FPU data via `camber.driftvalidation.evaluate` (baseline = fault-free run,
  current = each labeled fault run). `build_drift_cases`/`score_drift` generalized to take a
  subset-specific detector set + baseline file + multiple positive-fault prefixes.
- **`tests/test_lbnl_benchmark.py`** — FPU case-builder + scorer plumbing tests on synthetic
  FPU-shaped frames (always run); real-data scoring runs in the benchmark CI job with the data.

### Notes
- The LBNL chiller-plant subset is not yet wired here (its download path was still unresolved at this
  release; resolved and wired in 0.66.0). Documented in `docs/VALIDATION.md`. The encumbered ASHRAE
  chiller dataset is never named.
- No `camber/` change → public-API snapshot unchanged; new code is `examples/` + tests.

## [0.64.0] — 2026-08-24

**Real-data validation of the AHU air-side drift family (LBNL FDD).** Extends the LBNL benchmark to
score the newer drift detectors on labeled real faults, and documents — honestly — which new families
the public datasets can and cannot validate.

### Added
- **`examples/lbnl_fdd/benchmark.py` drift-scoring path** — scores `coil_valve_drift` (target = coil
  leak, real TPR), `economizer_damper_drift` (target = stuck damper, real TPR with a command-vs-position
  caveat), and `duct_static_drift` (specificity only) on the LBNL SDAHU data via
  `camber.driftvalidation.evaluate` (baseline = fault-free run, current = each labeled fault run). The
  `drift.*` metrics flow through the existing `--json` / `--gate` / `--update-baseline` path; NaN
  metrics (a specificity-only detector has no recall) are omitted to keep the JSON valid.
- **`tests/test_lbnl_benchmark.py`** — locks the case-builder + scorer plumbing on synthetic
  SDAHU-shaped frames (always runs, no download); the real-data scoring test skips when the ~580 MB
  LBNL CSVs are absent (they run in the benchmark CI job).

### Notes
- **Honest feasibility matrix** in `docs/VALIDATION.md`: fan-efficiency / filter drift need points the
  SDAHU sim doesn't export (synthetic-only); chiller/pump/VAV drift are air-side-absent here; the
  reset-effectiveness detectors need the per-cycle reset-**request** point; and the multi-zone
  rogue-zone / cohort-starvation census have **no vendorable public multi-zone-fleet fault dataset**
  (a real gap) — all validated on the synthetic whole-suite harness (`camber.faultlab`) instead.
- BDG2 is meter-level → M&V/anomaly only, not component FDD. Documents the open-licensed datasets that
  would close the gaps (CC-BY LBNL sibling subsets incl. the *simulated* chiller plant that sidesteps
  the encumbered ASHRAE chiller-FDD dataset; the CC-BY Korean office AHU set) and the ones to avoid
  (re-uploads of that encumbered chiller dataset; the CC-BY-NC-ND multi-zone VAV set — research-only,
  not vendorable).
- No `camber/` change → public-API snapshot unchanged; all new code is in `examples/` + tests.

## [0.63.0] — 2026-08-24

**Fault economics for the fleet reset rules (rogue-zone census & cohort starvation).** Completes the
Trim-and-Respond reset family's dollar-impact coverage, with per-air-handler load attribution for the
fleet-level findings.

### Added
- **`sat`/`static_rogue_zone_census`** cost model → the whole air handler over-services because one
  zone drags the reset: avoidable **reheat** (SAT) / **fan** (static) scaled by the rogue's request
  share (`worst_zone_share`). Uncosted when the census is *ungrouped* (building-wide screening only)
  or the AHU sizing is missing.
- **`static_cohort_starvation`** cost model → sustained **fan** over-pressure from the upstream fault,
  scaled by the cohort's sustained fraction (`worst_group_frac`); **`sat_cohort_starvation`** is
  unmet-load/comfort → **uncosted by design**.
- **`<fleet>`-finding load attribution** — a new `_resolve_load` / `_fleet_load` path resolves sizing
  for `equip="<fleet>"` findings to the **offending air handler** named in the metrics (`worst_group`,
  or the group holding `worst_zone`), so a `{equip: EquipmentLoad}` map keyed by AHU costs each
  handler on its own fan/coil. Applied consistently in `cost_findings` and `annotate_costs`.

### Notes
- Registered in `DEFAULT_MODELS`; flows through `rank_by_cost` / scorecard / action plan with no
  call-site changes; public API surface unchanged (estimators + helpers are private). With this the
  entire Trim-and-Respond reset family is money-rankable (drift remains uncosted-by-design).

## [0.62.0] — 2026-08-24

**Fault economics for the Trim-and-Respond reset family.** Extends `camber.fault_economics` so the
reset family's findings can be ranked by money, not just severity — triage-grade, honest, and
uncosted-by-design where the physics doesn't support a dollar figure.

### Added
- **`supply_air_reset_compliance`** cost model → avoidable **terminal reheat** (reheat-coil capacity ×
  below-target hours × diversity, **scaled by the `mean_gap_f` below-target gap**); uncosted without
  `EquipmentLoad.heating_capacity_kbtuh`.
- **`sat`/`static_reset_effectiveness`** cost model (dispatched on the failure `reason`): `not_trimming`
  → wasted **fan** energy (static, needs `fan_kw`) or avoidable **reheat** (SAT, needs coil capacity).
  `not_responding` (comfort/capacity risk), `stuck` and `diverges` (indeterminate energy sign) are
  **uncosted by design** — an honest basis, never a fabricated `$`.
- Two documented assumptions (`g36_gap_ref_f`, `reset_fan_excess_frac`) in `DEFAULTS`; `_reheat_gas`
  gains an `intensity_scale` so a marginal gap costs proportionally less.

### Notes
- Registered in `DEFAULT_MODELS`, so the new models flow through `rank_by_cost` / the scorecard /
  action plan with no call-site changes; the public API surface is unchanged (estimators are private).
- **Drift families remain uncosted by design** (documented + a lock test): a drift is a leading
  recommission indicator whose magnitude is a condition-space residual, not a priceable energy
  quantity. Fleet-rule (rogue/cohort) costing lands next, after the per-air-handler load attribution.

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
