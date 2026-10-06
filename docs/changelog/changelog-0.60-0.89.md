# Changelog: 0.60 to 0.89

Archived CAMBER releases 0.60.0 to 0.89.0, in the same format as the
[current changelog](https://github.com/yroussev/camber/blob/main/CHANGELOG.md), which holds the recent releases.
The [changelog index](index.md) lists every archive.

## [0.89.0] — 2026-09-27

**Catalog release 2.** The research-only tier, manual-download entries, Excel workbooks and
Brick-grouped ingest (the framework), one reconciled design for the source layouts real buildings
publish, and 14 new catalog entries -- 21 in all. CAMBER's first result on real, labelled
multi-zone VAV data is published in `docs/VALIDATION.md`. The `camber.datasets` API stays
**provisional**.

### Added

#### The catalog framework
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

#### Source layouts for real buildings (one design)
Four intake branches built these in parallel; 0.89 ships them as one design, each concept with one
key and one code path (`camber/datasets/_readers.py`), documented in
[DATASETS.md](../DATASETS.md#source-layouts):
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

#### 14 catalog entries
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

#### Maintainer decisions
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

### Added

#### M&V coverage
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

#### M&V foundations for rebaselining (#21, phase 21a)
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

#### Portfolio lifecycle (provisional, `camber.portfolio`)
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

### Fixed

#### The catalog's own assumptions (#23, #25-#29, #31)
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

### Fixed

#### Sensor health
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

#### Semantic models and mapping
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

### Added

#### Sensor health
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

#### Semantic models
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

### Changed

#### Read these if you pass the old values
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

#### DCV, from a real-data pass
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

### Hardening

#### Benchmarks and validation claims
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
