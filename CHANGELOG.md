# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## Older releases

Releases before 0.103.0 are archived under [`docs/changelog/`](docs/changelog/index.md):

- [0.98.0 to 0.102.0](docs/changelog/changelog-0.98-0.102.md)
- [0.90.0 to 0.97.0](docs/changelog/changelog-0.90-0.97.md)
- [0.60.0 to 0.89.0](docs/changelog/changelog-0.60-0.89.md)
- [0.1.0 to 0.59.0](docs/changelog/changelog-0.1-0.59.md)

## [0.106.0] — Unreleased

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

## [0.105.0] — 2026-10-17

**0.105: faithful CalTRACK 2.0 Daily, Billing and Hourly methods, an M&V names registry with the industry-standard names, a TOWT matching LBNL RMV2.0, sturdier M&V data handling, pump drift on hot-water loops, and four synthetic catalog entries with workbook exercises (#144–#149, #151–#180).** **Renames (#156, #163):** `mandv.caltrack.caltrack_savings` and `caltrack_savings_hourly` were not CalTRACK-comparable and are now `daily_changepoint_savings` and `towt_savings_hourly`; `fit_degree_day` / `DegreeDayModel` are now `fit_mean_temp_degree_day` / `MeanTempDegreeDayModel`. The old names keep working with a `DeprecationWarning` until 1.0 and return the same numbers. The `caltrack_daily_savings`, `caltrack_billing_savings` and `caltrack_hourly_savings` functions implement the published methods and match eemeter 2.10 where asked (#156, #166, #167, #170, #171). Reports, legends and the dossier name models by their registry labels (#163, #174, #179). TOWT gains an unseen-bin fallback, optional non-negative predictions, fixed knots and recency weighting, and an RMV2.0 variant that matches the R reference to 1e-10 (#154, #155, #162, #165, #176–#178). **M&V numbers can move on messy data:** daily frames drop partial days, the fall-back DST hour is no longer double-counted, and implausible temperatures are screened at fit and prediction time (#157–#159, #168, #169); clean data gives the same numbers. `best_model` and `ModelFitError` say why no model could be fit (#160, #172, #173, #175), and `seasonal_forecast` warns on long horizons (#161). On the FDD side, the pump drift family runs on hot-water loops (#152), the pump DP-reset rules judge only running hours (#180), `lbnl-chiller`'s pump rules read pump data (#164), the trust score checks the mixed-air order without airflow stations (#151), and plant-rule context is sharper (#148, #149, #153). Four synthetic entries (G36 VAV fleet, two-chiller plant, AHU maintenance, heat pumps) bring workbook exercises (#144–#147); answers in `plant-chw-reset-pumping` and `plant-sensor-vs-equipment` are re-recorded on running hours (#180).

### Added
- `mandv.intervalfit.day_coverage` (each day's covered share) and `day_hours` (each day's
  length on the site's clock).
- **The CalTRACK 2.0 Daily, Billing and Hourly methods (#156).** `mandv.caltrack_daily` and
  `mandv.caltrack_hourly` implement the published methods, rule by rule and with every constant
  cited:
  - `caltrack_daily_savings`, `caltrack_billing_savings` and `caltrack_hourly_savings`, each
    returning the fitted model with a `SavingsResult` in a `CalTRACKResult`;
  - the `fit_caltrack_*` functions;
  - `CalTRACKDailyModel`, `CalTRACKBillingModel` and `CalTRACKHourlyModel`, each with
    `predict`, `as_dict` and `from_dict`, so a baseline can be stored and projected later.

  Daily and Billing apply CalTRACK's data sufficiency, usage-per-day and temperature rules, its
  billing-period rules and its balance-point grid. They fit intercept-only, HDD, CDD and HDD+CDD
  candidates, qualify them on degree days and signs, select by adjusted R² with eemeter's tie
  order, and report CalTRACK's fractional savings uncertainty. Hourly fits one model per
  calendar month, weighting the months either side at 0.5. Its occupancy comes from a
  preliminary model's residuals, and its six fixed temperature bins are merged where sparse. An
  hour of the week a month never saw predicts NaN and is counted.

  Defaults follow the CalTRACK documents. `eemeter_compat=True` reproduces eemeter 2.10 where
  they differ, except eemeter 2.10's bug on unseen hours. A dev-only cross-check
  (`tests/test_caltrack_eemeter_xcheck.py`, skipped without a separate eemeter 2.10.11
  environment) matches eemeter to 3e-12 on synthetic cases. See docs/MANDV.md, "CalTRACK 2.0
  methods".
- **An M&V names registry (#163).** `camber.mandv.names` gives every model kind, degree-day kind,
  savings method and M&V function one row: its CAMBER code, the label reports show, the
  industry-standard name, the citation, the implementing function and the known differences from
  the standard (`mv_name`, `label`, `model_label`, `citations`). The "Method correspondence"
  table of `docs/MANDV.md` is generated from it (`scripts/mv_names_doc.py`, checked by the tests
  and `scripts/gates.sh`), and a new "Naming and citations" section says how to cite a model by
  its standard name and how to describe an ensemble by its components.
- **M&V citations** in `camber.references`: ASHRAE Guideline 14-2014, the RP-1050 Inverse
  Modeling Toolkit (Kissock, Haberl and Claridge 2003), PRISM (Fels 1986), TOWT (Mathieu, Price,
  Kiliccote and Piette 2011), Granderson et al. 2015 (LBNL-187596), the LBNL M&V test-method
  reports LBNL-187225 and LBNL-6963E, CalTRACK 2.0, IPMVP Core Concepts 2022 and the DOE SEP
  50001 M&V Protocol 2019. New kinds `PAPER` and `STANDARD`, `MV_REFERENCES`, the `Reference`
  fields `authors`, `year`, `venue` and `doi`, and `Reference.citation()`. `docs/REFERENCES.md`
  lists them.
- `mandv.degreeday.fit_mean_temp_degree_day` and `MeanTempDegreeDayModel`, the honest names of
  the period-mean-temperature degree-day model (see Deprecated).
- **`best_model` says why no model could be fit (#160).** It raised a bare `ValueError("no model
  could be fit")`. It now raises `mandv.models.ModelFitError`, a `ValueError`, whose message
  lists every kind tried with its parameter count and why it was refused: the fit's own error,
  `n - p < 1` with the counts, or a non-finite BIC. The list is also on `.candidates`.
  `best_model(..., return_candidates=True)` returns `(model, candidates)`, with each fitted
  kind's `n`, `sse`, `bic` and `selected`. The choice of model is unchanged. The billing
  selector `_mvform.best_billing_model` raises the same `ModelFitError` when neither the
  change-point kinds nor the degree-day model can be fit, listing each with its reason.
- **`seasonal_forecast` warns past its horizon (#161).** The forecaster is built for next-day
  to next-week use. A new `max_horizon=` (default 14 days, `None` to turn it off) issues a
  `forecast.ForecastHorizonWarning` when the latest target lies further than that past the end
  of history. The same warning, with the count, says when target intervals take the overall
  mean because history never saw their time-of-week slot. The forecast is still returned.
  `backtest` passes `max_horizon` through, so a hold-out longer than 14 days warns too.
  `docs/FORECAST.md` documents the intended horizon.
- **TOWT can predict hour-of-week bins the fit never saw, on request (#154).**
  `TOWTModel.predict(..., unseen="fallback")` fills such a row from the seen bin of the same hour
  and occupancy class on the nearest day of the same day type, or else from the mean of that
  hour's seen bins (NaN when the hour was never seen); `return_counts=True` counts each step.
  `TOWTAtIndex(unseen=)`, `_design.design_rows(unseen=)` and
  `towt_savings_hourly(unseen=)` carry it through, `avoided_energy_savings` caveats the count,
  and coverage grades the filled rows unsupported. The default still raises.
- **Optional non-negative predictions (#155).** `clip_negative=True` on `TOWTModel.predict`,
  `TOWTAtIndex`, `towt_savings_hourly`, `ChangePointModel.predict` and
  `MeanTempDegreeDayModel.predict` sets negative predictions to zero after `X @ beta` and counts
  them; the uncertainty stays on the unclipped projection. Off by default.
- **A synthetic catalog entry for a G36 VAV fleet (#144).** `synthetic-g36-vav-fleet` is a VAV
  air handler with six single-duct reheat boxes under Guideline 36 trim-and-respond SAT and
  duct-static resets, hourly for three weeks of spring, as eight air handlers: a healthy fleet,
  a rogue zone dragging the SAT reset, a blocked box holding duct static up, a capped fan
  starving every box, a reset that ignores its requests, single-maximum reheat, overlapping
  heating and cooling setpoints, and a warm zone that is not quite a rogue. Each air handler
  trends its setpoints with the reset requests its boxes sent; each box its space temperature
  and setpoints, airflow and minimum, damper and reheat valve. It is generated by CAMBER
  (generator `g36_vav_fleet`, seed 144), Apache-2.0, open, labelled and about 0.9 MB, with two
  mappings and a run template that declares the served-by topology. No open dataset in the
  catalog trends reset requests, so nine G36 zone-fleet rules had no case to fire on:
  `sat_`/`static_reset_effectiveness`, `static_rogue_zone_census`,
  `sat_`/`static_cohort_starvation`, `cohort_airflow`, `cohort_space_temp`,
  `reheat_minimization_g36` and `zones_heat_cool_census`. The workbook exercise
  `zone-g36-fleet` comes with it, with an instructor key, a figure and answers pinned by the
  test suite, and four variants that judge one air handler each. `camber.fleetlab` now draws its
  setpoint noise from the integer hash the synthetic entries use (moved to
  `camber._hashnoise`), so its fleets are the same bytes on every machine; the `fleet_fdd`
  benchmark is unchanged.
- **TOWT with fixed knots, weights and the LBNL reference settings (#162).**
  `fit_towt(..., knots=, knot_min_obs=, weights=)`: fixed temperature knots with unbounded outer
  segments (Mathieu et al. 2011, Table 3), RMV2.0's outer-knot pruning, and weighted least
  squares. `towt_lbnl_config()` returns the settings verified against LBNL's RMV2.0 1.1.0
  (knots 40, 55, 65, 80, 90 °F, pruning under 20 observations, zero clip, recency weight
  `s² / (s² + Δt²)` with `s = 14` days, `lbnl_time_weights`); RMV2.0's occupancy rule and its
  ensemble of local fits are not part of `fit_towt` but of `fit_towt_rmv2` (#165), and the
  registry's TOWT row points there. Models with
  these options serialise `open_ends` / `weighted`; older stored models load unchanged.
  `camber.references` adds `lbnl-rmv2`.
- **`clip_negative` on the CalTRACK predicts (#171).** `CalTRACKHourlyModel.predict`, and the
  Daily and Billing predicts, take `clip_negative=False`. Negative predictions are set to zero
  after the linear prediction and counted as `n_clipped` in `return_counts`. The uncertainty is
  unchanged, as for #155.
- **A synthetic catalog entry for air-handler maintenance (#146).** `synthetic-ahu-maintenance`
  is a constant-volume air handler, every 5 minutes for two weeks of early summer on a daily
  05:00-21:00 schedule, as five units: a healthy unit, a filter that loads up and crosses its
  change-out pressure part-way, a filter far past it, a cooling valve whose PI loop hunts, and one
  that oscillates a little. It is generated by CAMBER (generator `ahu_maintenance`, seed 146),
  Apache-2.0, open, labelled and about 0.9 MB, with a mapping and a run template. No open
  dataset in the catalog shows a fouled filter or a hunting loop for `filter_fouling` and
  `control_hunting` to find. The
  workbook exercise `air-maintenance` comes with it, with an instructor key, a figure and answers
  pinned by the test suite. It shows a loading filter that passes on the fortnight's median but
  warns on its last week (variant `--lastweek`), and the hunting valve passing at 10 minutes and
  declined at 15 (variants `--10min` and `--15min`).
- **`mandv.names.ensemble_description` (#174)** builds the "Ensemble approach combining ..."
  sentence of docs/MANDV.md, "Naming and citations". It names each component by its registry
  standard name and citation (`component_description`), then the combination rule. The short
  citation texts are `names.SHORT_CITATIONS`.
- **A synthetic two-chiller plant and its workbook exercise (#145).** `synthetic-chw-plant-multi`
  is a primary-secondary chilled-water plant: two chillers, a decoupler, a variable-flow secondary
  loop and one cooling tower, every 15 minutes for three summer weeks, about 1.7 MB. Each of its
  seven plants is four equipment, `CH1`, `CH2`, `SECLOOP` and `TOWER`. The plants are a well-run
  one, chillers over-staged, decoupler reverse mixing from low delta-T coils, a fouled condenser,
  a fouled tower pinned at full fan, a fouled tower that holds its setpoint by fan effort, and a
  near miss. The exercise `plant-chiller-multi` runs `chiller_staging_fleet` (per plant, through
  three equipment-filtered variants), `chw_supply_tracking`, `chiller_approach_fouling`,
  `cooling_tower_approach` and the tower drift family against `TOWER__well_run`. It shows why
  `cooling_tower_approach` sees a fouled tower only at full fan while fan effort sees a
  controlled one. A synthetic entry's golden file may list its ingest `runs` and `equipment`
  count when a plant is several equipment.
- **TOWT (LBNL RMV2.0 variant) (#165).** New module `mandv.towt_rmv2`: `fit_towt_rmv2(...)` is
  an opt-in reimplementation of RMV2.0 1.1.0's `makeBaseline` (`R/towt_baseline.R`, commit
  979a963). It returns a `TOWTRMV2Model` with `predict`, `as_dict` and `from_dict`, and
  reproduces:
  - RMV2.0's occupancy rule: a load regression hinged at 50 and 65 °F, with an interval occupied
    when over 65 % of its residuals are positive;
  - its ensemble of local fits, centred about every 14 days by row count across the baseline;
  - its prediction, in which each local fit is clipped at zero and the fits are averaged with
    the weights `s² / (s² + Δt²)` about the prediction time;
  - its NaN for an unseen time of week, with `unseen="previous"`, `"raise"` and `"fallback"`
    opt-in.

  It supports any interval of the week that divides a day; the default is hourly. It matches
  RMV2.0's own `makeBaseline` to 1e-8 on synthetic data. A dev-only test runs that comparison
  when `CAMBER_RMV2_RSCRIPT` is set; R is not a dependency. On a synthetic held-out year with a
  seasonal schedule, its hourly CV(RMSE) is about 3 points lower than one recency-weighted fit
  (`towt_lbnl_config()`). The registry has a new row, labelled "TOWT (LBNL RMV2.0 variant)"
  with the standard name "Time-of-Week-and-Temperature (TOWT) model (Mathieu et al. 2011)", so
  the two read together without repeating the name, as the TOWT row does (#179); its ensemble
  description is "... TOWT model (Mathieu et al. 2011), LBNL RMV2.0 variant [LBNL RMV2.0]".
  `towt_lbnl_config()`'s documentation points to it in place of one end-centred weighted fit.
  A stored `TOWTRMV2Model` rebuilds through `rebaseline.mv_model_from_dict`, and its predict
  counts a temperature outside its fitted bounds as `n_temp_screened` and a missing one as
  `n_no_temperature`, as the other predicts do (#168).
- **Synthetic heat pumps and the workbook exercise `plant-heat-pump` (#147).** A new synthetic
  catalog entry, `synthetic-heat-pump`, holds one small building's heat pumps through a
  fortnight of late winter with a cold snap and a sunny spell, every 5 minutes, as twelve
  scenarios: four air-source units (healthy, a defrost sensor reading far too cold, a 60-minute
  defrost timer, failed strip heat), six water-source units (two sharing a room, a reversing
  valve stuck in heating, a second stage that hunts, two units that fight in one room) and their
  source loop (variable flow, and constant speed with the bypass stuck open). It is generated by
  CAMBER (generator `heat_pump`, seed 147, in its own module), Apache-2.0, open, labelled, about
  3 MB, with three mappings and a run template that declares the shared rooms as `topology`.
  Until now `heatpump_defrost`, `hp_mode_vs_need`, `hp_capacity_shortfall`,
  `hp_room_imbalance`, `compressor_staging` and `source_loop_deltat` had no open-data case where
  they flag a problem. The exercise has an instructor key, a figure and answers pinned by the test
  suite; its variants show the defrost and stage counts vanish on a 15-minute trend (`--15min`)
  and a 60-minute defrost timer just under the default limit (`--20rev`). The heat-pump rules now
  link to public heat-pump guides (Natural Resources Canada, NEEP) and an EPRI brief on loop
  pumping, and `compressor_staging` to the small-building re-tuning chapter 3, in
  `camber.references` and docs/REFERENCES.md.
- **Plant-rule context (#148).** `condenser_water_reset` reports
  `cws_above_wb_plus_approach_median_f`, the median over the operating samples of how far the
  condenser-water supply sits above the wet-bulb plus the tower's design approach, and takes a new
  `design_approach_f` (default 7 °F, as in `cooling_tower_approach`), so a reset held up by a high
  floor is visible: 7.5 °F on the `plant-chiller-staging` high-floor plant against 0.3 °F on the
  well-staged one. `chiller_staging` reports `load_spread_pct` (the 10th to 90th percentile of the
  running tons, as a share of their peak) and adds a caveat when it is 5 % or less: every run is
  at about one output, so the load factor says nothing about part load. Both are information
  only; no verdict changes.
- **Pump and loop drift on hot-water loops (#152).** The new `hw_pump` drift family runs the
  pump family's five detectors on a hot-water loop's roles (`hw_flow`, `hw_pump_speed`,
  `hw_supply_temp` − `hw_return_temp`, `hw_diff_press`, `hw_diff_press_sp`), with the 0.104
  size-relative gates and the same thresholds (`camber.rules.hw_pump_rules`). The detectors are
  `hw_pump_flow_drift`, `hw_pump_head_drift`, `hw_loop_deltat_drift`, `hw_loop_dp_drift` and
  `hw_pump_power_drift`. Each has its own baseline-store kind, so an equipment that carries both
  loops keeps one baseline per loop. `diagnose_pump_drift` and the `plant` roll-up read either
  loop. The detectors share their parameter docs with the chilled-water ones
  (`param_docs.HW_PUMP_DRIFT_TWINS`, docs/THRESHOLDS.md). The `pump` family is unchanged. On
  `lbnl-boiler` against its fault-free run, the PI-mistuning run is a loop ΔT `warn` (1.6 °F,
  3.0σ) and the eight temperature-bias runs are loop ΔT `fault`s. The fouling and DP-bias runs
  are `ok`. The pump flow and loop DP detectors are `ok` on every run (docs/PUMP-DRIFT.md,
  "Hot-water loops").

### Changed
- **The trust score checks the mixed-air order when a unit has no airflow stations (#151).** The
  0.92 mixed-air flow balance needs both an outside-air and a supply airflow, which most AHUs do
  not trend. Without them, `frame_sensor_health` now checks that the mixed air lies between
  outdoor and return air (`mixing_consistency`), on fan-on samples only: the gate passed in, or
  the frame's own fan signal. A unit with no fan signal is not checked. On a warning or fault the
  mixed air is flagged `mixing_order` and capped at "suspect"; OAT and RAT are flagged and keep
  their scores. `frame_checks` takes the fan gate as `gate=`. In triage, a unit whose RCx mixing
  result fires too gets that one cause, not a second per flagged point. The cap stays above the
  runner's trust bar, so no rule's gating or benchmark moves. The RCx golden report and the
  workbook answers are unchanged: the B59 RTUs keep the balance, and `irish-ahu` has no fan
  signal.
- **The CalTRACK look-alikes are renamed (#156).** `mandv.caltrack.caltrack_savings`, a daily
  change-point baseline chosen by BIC, is now `daily_changepoint_savings`, returning
  `DailyNMECResult`. `caltrack_savings_hourly`, a pooled TOWT, is now `towt_savings_hourly`,
  returning `TOWTHourlyResult`, which also carries the fitted model (`model`, not in
  `as_dict`). Neither was CalTRACK-comparable. The numbers are unchanged, and so are the
  workbook answers. The `caltrack_*` names now belong to the CalTRACK 2.0 methods above, under
  new names, so no old name changes meaning.
- **The period-mean degree-day model counts a searched balance point in its parameters (#163).**
  `fit_mean_temp_degree_day` now adds 1 to p when it searches for the balance point, as
  `basetemp` does for its fitted bases, so its fit statistics and uncertainty band are no longer
  optimistic; a fixed `balance_point` is unchanged. Savings and projections do not move; the band
  widens slightly (in the coverage test, 65.9 → 66.93, fractional 0.1153 → 0.1171). No benchmark,
  M&V vector or workbook answer uses this model.
- Clean data (whole days, plausible temperatures, no duplicate stamps) gives the same numbers as
  before. Where a day is partial, a temperature is implausible or an hourly stamp repeats, the
  M&V frames, fits and savings change as described under Fixed. The default `rate_is_energy_rate`
  differs between the frame builders (`True`) and the savings functions (`False`). It is now
  documented and is unchanged.
- **Reports name M&V models by their registry labels (#163).** The M&V report's Model, Method and
  Degree-day kind columns, the `mv_baseline` summary (daily and billing), the energy-signature
  and OAT-scatter legends and titles say "3P cooling", "3P heating, zero base (CAMBER)" or
  "2P (5P fallback)" instead of "3PC", "3PHZ" or a "5P" with no change points. The daily summary
  says the 30 % daily CV(RMSE) tier is CAMBER's, not G14's. The validation dossier names the
  BDG2 model "daily change-point (2P–5P, BIC)". The `kind` codes in metrics, JSON and stored
  baselines are unchanged; the chained report's version rows gain `model_label`.
- **Option D savings are labelled for what they are (#163).** A claimed
  `OptionDSavings.basis` reads "Option D-style modeled savings (calibrated 1R1C grey box;
  pre-implementation estimate)" (2R2C or multi-zone RC for those models), not "IPMVP Option D
  (calibrated simulation)": the model is a calibrated grey box, not a simulation program. The
  calibration gate is unchanged; the docs now say that NMBE is gated alongside CV(RMSE).
- Docstrings and docs corrected (#163): `best_model` selects by BIC (its docstring said
  CV(RMSE)); the 5P fallback to a line and the zero-intercept kinds (a CAMBER extension) are
  documented; the daily 30 % CV(RMSE) tier is CAMBER's interpolation, not a Guideline 14 value;
  TOWT is labelled "TOWT (CAMBER variant)" with its differences from the reference;
  `seasonal_forecast` is "Mean-Week + drift (LBNL M4 analogue), not an M&V method"; and
  `docs/MANDV.md` no longer says `caltrack_savings` is "exactly" CalTRACK Daily (it is not;
  faithful CalTRACK methods follow, #156).
- **`ModelFitError` names why no points were left (#172).** When the day-coverage rule, the
  temperature screen or missing values left too few points, `best_model` and
  `best_billing_model` listed each kind as "need >=3 finite points". The message now names the
  cause, such as days dropped by `min_coverage`, temperatures screened or rows missing, and
  `.causes` holds the counts. `.candidates` is unchanged. `best_model(upstream=)` takes a
  frame's `attrs`, and billing frames count `attrs["dropped_bills"]`.
- **Registry `differences` texts paste verbatim (#174).** Instructions such as `best_model`'s
  "Report the kind it chose…" moved to docs/MANDV.md, "Naming and citations". The texts now only
  describe how CAMBER departs from the cited standard.
- **`ModelFitError` says what the fit saw (#175).** Without the frame's counts it said "(no
  input rows)" even when points remained or rows were dropped before the call. It now states how
  many finite (temperature, energy) points every candidate saw, why the others were lost, and,
  when the frame builder's counts were not available, that rows dropped before the call are not
  known. `best_model(frame)` takes a frame with `oat` and `energy` columns (its index as
  `time_index`), and a pandas `y` carries the frame's `attrs` as a pandas `T` did, so the cause
  is found without `upstream=`; `upstream=` also takes the frame itself. `.candidates` and
  `.causes` are unchanged, and `.upstream_given` is new.
- **`fit_towt_rmv2` solves its local fits on one shared design (#176).** The design is factored
  once and each local fit is a narrow weighted solve, about 16 times faster on a year of hourly
  data (28 local fits), close to one pooled TOWT fit. The coefficients are the same
  minimum-norm weighted least-squares solutions as before, to about 1e-10, rank-deficient designs
  included, and the RMV2.0 cross-check still matches to 1e-8.
- **RMV2.0 TOWT's horizon is documented (#177).** Its weighting about the prediction time leans
  on the local fits at the end of the baseline, so several months into a season the baseline saw
  little of it can do worse than unweighted TOWT. This is RMV2.0's own behaviour. The
  `fit_towt_rmv2` docstring, the `towt_rmv2` registry row and docs/MANDV.md say when to prefer it
  (short horizons, tracking recent conditions) and when to compare it with unweighted TOWT.
- **`TOWTRMV2Model.predict` counts clipping as the other models do (#178).** `n_clipped` is now
  the rows whose final prediction was clipped, 0 by construction since the local predictions are
  clipped first. The new `n_local_clipped` counts rows where any local prediction was clipped,
  which `n_clipped` counted before.
- **A synthetic catalog entry now adds files instead of editing shared ones.** A generator can
  live in its own module, `camber/datasets/_synthetic_<name>.py`, which registers itself with
  `_synthetic.register`; `_synthetic` imports every such module. A catalog entry can live in a
  file of its own, `camber/datasets/catalog.d/<NNN>-<id>.json`, loaded after `catalog.json` in
  the order of its prefix. The three synthetic entries moved there, and the loaded catalog is
  unchanged: the same entries in the same order. A docs figure can live in
  `scripts/docs_figures.d/`. The catalog and synthetic tests derive their entry lists from
  `catalog.d` and the generator registry, and check each entry against its own expectation file
  in `tests/golden/synthetic/`. `mkdocs.yml`, the workbook instructor page and
  `camber/references.py` reserve marked slots for the four synthetic entries planned for 0.105
  (#144-#147). The dataset-host exemption of the site-neutrality guard covers `catalog.d`.
  docs/DATASETS.md has a new section, "Adding a synthetic entry".

### Deprecated
- `mandv.degreeday.fit_degree_day` and `DegreeDayModel` (#163), since 0.105, removed in 1.0: the
  names said "variable-base degree-day", but the model uses each period's mean temperature and one
  shared balance point. Use `fit_mean_temp_degree_day` and `MeanTempDegreeDayModel`; the old
  names keep working with a `DeprecationWarning`, and stored models keep `"type":
  "DegreeDayModel"`.
- `mandv.caltrack.caltrack_savings`, `caltrack_savings_hourly`, `NMECResult` and
  `HourlyNMECResult` (#156), to be removed in 1.0. Each warns and returns the same numbers as
  `daily_changepoint_savings`, `towt_savings_hourly`, `DailyNMECResult` and `TOWTHourlyResult`.
  An old result class is a subclass of its new one, so `isinstance` checks against either name
  hold.

### Fixed
- **Daily M&V frames drop partial days (#157).** `mandv.intervalfit.daily_energy_vs_temp` summed
  whatever samples a day held, so a day missing hours counted as a whole day and biased a gappy
  meter's baseline low. A day is now kept only when its samples cover at least `min_coverage`
  of it (default 0.9): the samples present times the meter's modal interval, over the day's
  hours. With the site's zone a daylight-saving day is judged against its own 23 or 25 hours.
  The frame's `attrs["dropped_days"]` counts the days dropped. `daily_changepoint_savings`, the
  non-routine detectors and a config's `mv` findings take `min_coverage` (config:
  `mv[].min_coverage`) and report what was dropped. `min_coverage=0` keeps every day, as before.
  The docstring no longer implies a rule that did not exist. The workbook answers move with it,
  because the real datasets hold partial days. In `mv-baselines`, `b6400_ElImp` loses two
  near-empty baseline days (R² 0.868 to 0.887). Its forecast saving goes from 5.8 % to 6.2 %, with
  a band of 24,051 to 24,347, and its backcast from 5.1 % to 5.7 %. The closure chain goes from
  -0.9 % to -0.4 %, and from -8.1 % to -7.5 % adjusted. `UVA_A` goes from R² 0.66 to 0.67 and a
  saving of -0.1 % to -0.2 %, and `UVA_B`'s saving from 12.6 % to 12.7 %. In
  `data-energy-charting`, `UVA_A`'s CV(RMSE) goes from 26.1 % to 26.0 %.
- **The hourly fall-back hour is no longer counted twice (#158).** `hourly_energy_vs_temp`
  summed duplicate timestamps, so an export that kept both readings of the repeated
  daylight-saving hour gave that hour double energy. With the new `timezone=` (also on
  `towt_savings_hourly` and `daily_changepoint_savings`), both real hours are kept on a tz-aware local
  index. A lone stored sample in the repeated hour stands for both, as on the daily path. Without
  a zone, duplicate stamps are averaged into one hour, counted in `attrs["n_duplicate_stamps"]`,
  with a `TimezoneWarning`. A rate meter's step now comes from the positive gaps only, so
  duplicate stamps can no longer give a zero step and zero energy.
- **Baseline fits screen implausible temperatures (#159).** A placeholder such as -237 for a
  missing outdoor temperature became TOWT's first temperature breakpoint, or a change-point
  model's coldest point. `fit_towt`, `models.fit_model` / `best_model`,
  `degreeday.fit_mean_temp_degree_day` and the `intervalfit` frames now treat values outside the
  sensor-health outdoor-air range (-40 to 140 °F) as missing. The count is reported as
  `n_temp_screened` on the model and the frame, and in the savings caveats. `temp_bounds=` sets
  another range.
- **TOWT coverage no longer grades an unseen hour-of-week bin as unoccupied (#154).** An hour
  in a bin the baseline never saw took the unoccupied mode's temperature cells and could pass as
  supported. It is now unsupported whatever its temperature, counted in
  `coverage["info"]["n_unseen_tow_bin"]` and caveated.
- **A TOWT row without a temperature predicts NaN (#159).** `TOWTModel.predict` used to give it
  its time-of-week level alone, a projection with no weather in it. `avoided_energy_savings`
  excludes such rows, as it did any non-finite projection, and its caveat now says how many rows
  had no finite projection.
- With the site's zone, an energy-per-interval export that kept both readings of the repeated
  hour under one stamp counted each reading twice on the daily path. Both readings now count
  once each.
- **CalTRACK Hourly survives a least-squares solve that does not converge (#166).** The
  pseudo-inverse's SVD can fail to converge on a finite, full-rank design, and one failing month
  segment aborted all twelve. The solve now falls back to `lstsq` and then to a QR solve (the
  same coefficients on a well-conditioned design). A segment that still cannot be fitted is
  `unfit`, with a `reason` and a caveat, and only its month predicts NaN.
- **CalTRACK Hourly declines when no reporting hour can be predicted (#167).**
  `caltrack_hourly_savings` returned a saving of 0 with only a caveat when every reporting hour
  lacked a model or a temperature. It now declines (`savings.declined`, with a reason that counts
  the causes), as the other savings functions do when nothing can be projected.
- **The temperature screen also runs at prediction time (#168).** `TOWTModel.predict`,
  `CalTRACKHourlyModel.predict`, the CalTRACK Daily and Billing predicts,
  `ChangePointModel.predict` and `MeanTempDegreeDayModel.predict` extrapolated to a placeholder
  such as -237 °F. A temperature outside the bounds the model was fitted with now predicts NaN,
  counted as `n_temp_screened` in `return_counts`. The coverage check treats such a row as a row
  without a driver, not an extrapolation, and the savings sums exclude and count it. A model
  stores `temp_bounds` only when they are not the default range, so default and older stored
  models are unchanged.
- **A saving with nothing to sum is declined.** `avoided_energy_savings` returned a saving of 0
  when no reporting row had both a finite baseline projection and actual energy (every
  temperature missing or screened, for example). It now declines with a reason that counts the
  rows without a projection and without energy, as CalTRACK Hourly does (#167).
- **Daily frames count a duplicate timestamp once without a zone (#169).**
  `daily_energy_vs_temp` without `timezone=` summed a repeated fall-back hour twice. Duplicate
  stamps are now averaged into one hour, with the same `TimezoneWarning` and
  `attrs["n_duplicate_stamps"]` as the hourly path (#158). The zone-aware path is unchanged.
- **The CalTRACK duplicate-timestamp message says a zone is needed (#170).** Without
  `timezone=` it said the duplicates fell outside a fall-back hour, which it cannot know. It now
  says a timezone is needed to tell a fall-back hour from a real duplicate, and suggests
  `timezone=`.
- **Error messages print plain numbers (#173).** The TOWT unseen-bin error printed
  `np.int32(9)`; it prints `9`. The SEP chaining period error prints dates, not `Timestamp(...)`.
- **`hw_pump_summer_lockout` weights its running shares by duty (#149).** As #143 did for
  `boiler_summer_lockout`, a pump or boiler status resampled coarser than it was logged is read as
  its duty (clipped to 0-1), so a pump running part of a warm hour is no longer read as off.
  `pump_running_pct` and `summer_run_pct` weight each sample by the pump's duty, and
  `boiler_off_pct` by the pump's duty times the share of the sample the boiler was off. The
  evidence mask shades every warm sample the pump ran in at all. `n_running` and
  `max_oat_running_f` still read the samples it ran for most of. A speed or flow stand-in has no
  duty and keeps its running cut; its caveat now says so. Results on a 0/1 trend are unchanged,
  and so are the `plant-lockout` answers.
- **An `ok` loop ΔT finding names no fault (#153).** `loop_deltat_drift` said "loop ΔT
  collapsed 0.0°F … low-ΔT syndrome" or "widened … starvation" on an `ok` Finding. It now says the
  loop ΔT "holds near its baseline", with the signed shift and its σ. `warn` and `fault` keep the
  direction and the cause. Only the text moves: in `tests/golden/pumpsim_fullsize.json` the 17 `ok`
  `loop_deltat_drift` summaries change, and every severity, metric and caveat is unchanged.
- **Pump rules on `lbnl-chiller` read pump data (#164).** The plant's secondary pump speed and
  loop DP sat on the plant equipment, so a pump rule read chiller 1's kW as pump power and its
  constant primary flow as the pump's flow. The secondary pumps are now their own equipment,
  `SECPUMP__<run>` (class `CHW_PUMP`), with the two pumps' summed kW, the loop flow, the shared
  speed, the loop DP and setpoint, and a run status derived from pump 1's kW. Each run keeps
  `PLANT__<run>` with chiller 1's power and constant flow. The new pump equipment is recorded
  beside the plant and never scored, so `camber datasets score` is unchanged. The new data issue
  `secondary-pump-status-and-speed` records that the pumps' first status point is an enable and
  their speed holds its last value while they are stopped. In `plant-chw-reset-pumping` and
  `plant-sensor-vs-equipment` the `chw_pump_dp_reset` answers move from `PLANT__<run>` to
  `SECPUMP__<run>` with the same values.
- **The pump DP-reset rules no longer count stopped hours as running (#180).**
  `chw_pump_dp_reset` and `hw_pump_dp_reset` read "running" from speed above 5 %, so a speed
  point that holds its last value while the pump is stopped read as a pump idling at its VFD
  floor. On `lbnl-chiller` the secondary pump speed parks at the stopped DP loop's output (0.345)
  for 2,869 of 8,759 fault-free hours (966 to 6,210 across the 24 runs). Both rules now take an
  optional `pump_status` and judge only the samples the pump ran for more than half of (a
  resampled status is a duty, as in #143). The speed distribution, the learned VFD floor and the
  DP-setpoint check all read those samples only. New metrics `run_source` (`status` or `speed`)
  and `n_stopped_speed_above_run` say what was used. Without a status the figures are unchanged,
  with a caveat that a parked speed cannot be ruled out. On `lbnl-chiller`'s fault-free run the
  pump has no floor plateau while it runs: near the minimum goes from 31.3 % to 0.0 %, the learned
  34.5 % floor goes away (the band falls back to 25 %), the median goes from 84.9 % to 91.8 % and
  near full from 38.0 % to 56.5 %. Severity moves on 10 of the 24 runs. `lbnl-boiler`'s hot-water
  pump runs every hour, so nothing moves there. The data issue `secondary-pump-status-and-speed`
  now records the parked speed and the DP reading its setpoint at zero flow. The workbook
  answers move with it, re-recorded on running hours. In `plant-chw-reset-pumping` the lesson
  is no longer "the pump idles at its VFD floor": no floor is learned (`near_min_source`
  `default`, 0.0 % near the minimum), the fault-free pump rides near full against the flat DP
  setpoint (median 91.8 %, 56.5 % near full, still a warn), the chiller-bias run goes from a
  warn at a 34.5 % median to `ok` at 70.3 % (8.7 % near full; its pumps are stopped for 6,210
  hours), and the stuck-bypass pump goes from warn to fault; its check is now "no DP reset;
  judged on running hours, no VFD floor is learned". In `plant-sensor-vs-equipment` the 2 °C-low
  chiller-sensor run is near full in 92.2 % of its running hours (was 82.1 %), and the loop DP
  biases read medians of 80.0 % (+20 %, was 74.4 %) and 100.0 % (-20 %, was 97.0 %, now a
  fault). The workbook stand-in's secondary pumps now stop with the chiller, their speed parked
  at 34.5 %, as the real data's do. The `near_min_pct` rationale in `docs/THRESHOLDS.md` no
  longer calls 34.5 % the pump's floor.

## [0.104.0] — 2026-10-17

**0.104: a safer `camber serve`, store locks on every write, store verify/normalize, coarse-trend caveats for start counts, pump drift gates that scale to the pump, and two synthetic plant cases (#134–#139, #141, #143, #150).** **Behaviour change (#134):** `camber serve` now refuses cross-site browser requests (403); `curl`, scripts and health probes are unaffected, but a link to the viewer from another site no longer opens it. **Deprecation (#134):** a future release, 0.105 at the earliest, will make `--auth token` the default on loopback; pass `--auth token` or `--auth none` now to choose. Every write to a plain store now takes its single-writer lock, and `--lock-timeout` / `CAMBER_LOCK_TIMEOUT` set the wait (#135). `camber store verify` and `camber store normalize` check and rewrite parts into the 0.103 canonical layout (#136). `boiler_short_cycle`, `compressor_short_cycle` and `chiller_staging` say when a trend is too coarse to count starts, and a resampled run status no longer hides short cycling (#143). The pump and loop drift detectors size their gates to the pump, so small and constant-speed pumps are checked instead of declined; full-size results are unchanged (#150). Two new synthetic catalog entries bring the `plant-boiler-cycling` and `plant-chiller-staging` exercises (#137). Also: README images render on PyPI (#138), a flaky test (#139) and a catalog-rebuild race (#141) are fixed.

### Added
- **`camber serve --auth none` (#134)** chooses no authentication explicitly: the same open
  server as leaving `--auth` out, without the notice below. `auth="none"` is the Python form and
  `CAMBER_API_AUTH=none` the `python -m camber.api.server` form.
- **A `camber.api.server` entry in `docs/API-STABILITY.md` (#134):** its public names, their
  stability, and the planned change of the `auth` default.
- **`--lock-timeout SECONDS` and `CAMBER_LOCK_TIMEOUT` (#135)** set how long a command waits for a
  store or dataset-cache lock that another process holds, on `camber datasets fetch|ingest|remove`,
  `camber lab`, `camber interop openfdd ingest` and `camber store migrate-partitions`. `0` fails at
  once, naming the holder. Without the flag, the environment variable sets the wait, else it is 30
  seconds as before. A value that is not a number of seconds >= 0 is refused (exit 2).
  `camber retention apply` takes `--lock-timeout` too, as the new name for `--wait`, which still
  works; its default stays 0. In Python: `ParquetStore(root, lock_timeout=…)`, `ParquetStore.lock()`
  for holding the lock across a bulk load, `lock_timeout=` on `ingest_package`, and
  `LabApp(lock_timeout=…)`.
- **`camber store verify` and `camber store normalize` (#136).** `verify` reports, for a store or
  one facility (`--facility`), whether each part file is in the canonical layout of 0.103 (the
  `camber.layout` marker, no pandas metadata, one row group, rows sorted by the canonical key),
  with counts of canonical, legacy (pre-0.103, no marker), edge and rollup parts, and lists any
  part that fails to read. It takes no lock and changes nothing; `--json`; exit code 0 when every
  part is readable, 1 when one is not, or with `--strict` when one is not canonical. `normalize`
  rewrites the non-canonical parts, partition by partition, under the store lock
  (`--lock-timeout`). It is a dry run unless `--apply --yes`, with `--reason` inside a workspace,
  where it is audited as `store.normalize` and covers the rollup stores too. Each part keeps its
  name and exactly its rows (a rollup part keeps `camber.rollup.covers`). The new part is staged
  inside the partition, read back and compared, then moved over the original, so a crash at any
  point loses and duplicates no row and the next run finishes. It is idempotent, invalidates the
  catalog and reports the bytes before and after. It leaves alone canonical parts, edge parts,
  year-only parts (`migrate-partitions` rewrites those), unreadable parts and facilities that do
  not accept data or are under a legal hold, each with its reason, and refuses such a facility
  named with `--facility`. In Python: `ParquetStore.verify()` and `ParquetStore.normalize()`.
- **The edge forwarder's parts stay content-addressed (#136), and `docs/EDGE-DEPLOY.md` says
  why.** Canonical bytes would give a batch the forwarder re-sends after an upgrade a new key, so
  its rows would land twice; and the landing checks each name against the sha256 of its bytes.
  `store verify` counts edge parts on their own and `store normalize` leaves them as they are.
- **Two more synthetic catalog entries (#137), for plant faults no open dataset shows.**
  `synthetic-hw-plant-cycling` is a hot-water plant, every 15 minutes for three weeks of early
  spring, as four plants: a well-tuned modulating boiler, an oversized on/off boiler that
  short-cycles, a boiler that cycles a little too often, and a loop with three-way valves and a
  constant-speed pump. `synthetic-chw-plant-staging` is one chiller and its cooling tower, every
  15 minutes for three summer weeks with a heat wave, as five plants: a well-staged chiller, an
  oversized chiller that short-cycles, a chiller left on around the clock, a tower held at a fixed
  85 °F and a tower reset with a high 80 °F floor. Both are generated by CAMBER (generators
  `hw_plant_cycling` and `chw_plant_staging`, seed 137), Apache-2.0, open, labelled, under 1 MB,
  with a mapping and a run template. Until now `boiler_short_cycle`, `hw_plant_deltat`,
  `hw_pump_dp_reset`, `chiller_staging` and `condenser_water_reset` had no open-data case where
  they flag a problem.
- **Workbook exercises `plant-boiler-cycling` and `plant-chiller-staging` (#137)**, with
  instructor keys, a figure each and answers pinned by the test suite on the generated data. The
  boiler exercise shows a short-cycling boiler's start count fall to a floor when the trend is
  resampled to an hour, and a warn that depends on the boiler's own starts-per-day limit
  (variants `--hourly` and `--10starts`). The chiller exercise separates the two ways `chiller_staging` fails, and shows a
  condenser-water reset that passes the check but saves little (variant `--slope05`). The
  workbook now has 22 exercises.
- **`pumpsim` sizes the pump and runs constant-speed pumps (#150).** `simulate_case` and
  `make_cases` take `design_flow_gpm` (flow at 100 % speed, default 1000 gpm), which scales flow,
  power and their noise and leaves head, loop DP and ΔT alone, plus `fixed_speed` (a constant-speed
  pump with a `PUMP_STATUS` of 1) and `speed_point=False` (no speed column). The default pump's
  frames are byte-identical to 0.103's.
- **The pump drift family's parameters are documented (#150)** in `camber.rules.param_docs` and
  `docs/THRESHOLDS.md`, under "Pump and loop drift detectors": every constructor parameter of
  `pump_flow_drift`, `pump_head_drift`, `loop_deltat_drift`, `loop_dp_drift` and
  `pump_power_drift`, with its unit, range, basis and how to calibrate it.

### Changed
- **`camber serve` refuses cross-site browser requests (#134), as `camber lab` does.** A request
  with `Sec-Fetch-Site: cross-site` (or an unknown value), or with an `Origin` that is not `http://`
  or `https://` plus an allowed `Host` name, is 403 on every route and method, after the Host check
  and before the token check. `same-origin`, `same-site` and `none` pass. Requests with neither
  header pass, so `curl`, scripts, the image's `HEALTHCHECK` and the Kubernetes `/health` probes
  are unaffected, and so are the trend viewer's own requests. A link to the viewer from a page on
  another site is now refused: open the URL directly. Behind a proxy that rewrites `Host`, list the
  public name with `--allow-host`, since browsers send it in `Origin`.
- **One Host and Origin check for both servers (#134).** `camber lab`'s own Host and Origin check
  now uses the shared `HostAllowlist` and cross-site check in `camber._access`, with its narrower
  settings: `127.0.0.1` and `localhost` at the bound port, `http` origins, and `Sec-Fetch-Site`
  `same-origin` or `none`. The lab answers the same requests with the same errors; host names are
  now matched without regard to letter case.

- **Every write to a plain store takes the store lock (#135).** In 0.103 only the dataset
  catalog's writers locked a plain store. Now every `ParquetStore` write method holds the store's
  single-writer lock (`<store>/_lock`, or the workspace's lock for a workspace store):
  `write_long`, `write_role_frame`, `register_facility`, `write_rollup` (on the destination),
  `prune`, `drop_partition`, `drop_facility` and `migrate_partitions(apply=True)`. So do the
  facility registry's changes on a plain store. The lock is re-entrant within a process.
  `camber interop openfdd ingest --store`, `camber store migrate-partitions --apply`, the dataset
  ingests and `synth_portfolio()` take it once for the whole command, and the writes inside
  re-enter it. Reads still take no lock. A plain store now gets a `_lock` file on its first write.
  A script that writes many times can hold `with store.lock():` around the whole load, so the
  lock is taken once; otherwise each write takes it on its own, which costs under a millisecond.
  An ingest's staging area and a workspace's rollup stores take no lock of their own, because the
  lock on the store they belong to already covers them.
- **Start-counting rules say when a trend is too coarse to count starts (#143).**
  `boiler_short_cycle`, `compressor_short_cycle` and `chiller_staging` take a new parameter,
  `max_interval_min` (default 15 minutes). On a trend coarser than that, a start count is a floor,
  since a cycle shorter than the interval is invisible. A count at or above `max_starts_per_day`
  keeps its verdict and gains a caveat saying so. Below the limit, `boiler_short_cycle` and
  `compressor_short_cycle` decline (`info`, "cannot confirm on a 60-minute trend") when samples
  show the equipment on for only part of an interval, because the interval hid starts; with no
  part-on samples, `ok` stands with the caveat. `chiller_staging` reads an averaged power trend,
  which cannot show hidden cycles, so it adds the caveat only. A run at the stored 15-minute
  interval is unchanged. Both cycle rules report two new metrics, `interval_min` and `n_partial`
  (samples on for part of their interval). The resampling rules for run status are written up in
  `docs/TIME-HANDLING.md`.
- **The pump and loop drift detectors scale their gates to the pump (#150).** Until now they gated
  on fixed numbers that a small pump never meets. A 40 gpm pump never reached 50 gpm, so
  `loop_deltat_drift`, `loop_dp_drift` and `pump_power_drift` declined, and a worn impeller's whole
  deficit sat under the 15 gpm floor of `pump_flow_drift`. With the defaults now `None`:
  - **Flow gates.** The minimum flow and identifiable flow span are 10 % and 20 % of the
    baseline's p95 flow (`size_relative_load_gates`, as the chiller rules since 0.84), capped at the
    former 50 and 10 gpm. A pump whose baseline p95 is 500 gpm or more is gated exactly as before.
  - **Speed gates.** The minimum speed of `pump_flow_drift` and `pump_head_drift` is the pump's
    learned VFD floor plus 2 points, else 15 %, so samples parked at the drive minimum (where the
    drive, not the load, sets the speed) are left out. `loop_deltat_drift` uses the same speed gate,
    not 50, when a pump speed stands in for flow.
  - **Backstop floors.** The flow-deficit floor is 2 % / 4 % of the baseline's p95 flow (capped at
    15 / 30 gpm, reached at a 750 gpm pump). The power-excess floor is 5.5 % / 11 % of its p95
    power (capped at 1 / 2 kW, reached at 18.2 kW). The head detector's flow-rise confound is 2.5 %
    of p95 flow (capped at 20 gpm). The sigma floors still carry the weight.
  - **Constant-speed pumps** are compared as a flat level, with a caveat, instead of declining.
    This applies to a speed whose coefficient of variation over the baseline is at most 2 %
    (`constant_speed_cv`), or a pump with no speed point but a running status: `pump_flow_drift`
    and `pump_head_drift` now run on a speed point *or* a status (`roles_any_of`). The caveat says
    that a throttled balancing valve moves a constant-speed pump along its curve just as wear
    does. A loop whose flow barely moves (`constant_cv`, 2 %) gets a flat-level baseline in the
    three flow-normalized detectors.

  An explicit constructor value restores the old gate. Every Finding reports the gates and floors
  it used (`*_speed_gate_pct`, `*_load_gate`, `*_gate_source`, `*_vfd_floor_pct`, `*_warn_floor_*`,
  `*_speed_mode` / `*_normalizer_mode`, `*_baseline_model`).

  On the pumpsim sweep from 20 to 1000 gpm, every fault family is now detected and localized at
  every size as it is at full size, with no false alarm on healthy or DP-reset runs; at 0.103,
  `pump_flow_drift` caught no flow fault below 200 gpm. Full-size results are unchanged (a golden
  from 0.103 pins them). On `lbnl-chiller`, the three flow-normalized detectors now score chiller
  1's constant 279 gpm flow as a level instead of declining, all `ok`. On `lbnl-boiler`, scored
  with the detectors built on hot-water roles (the family is chilled-water only until #152), no
  severity changes; its lead pump turns out to be variable-speed (a 28.9 % VFD floor, not constant).

### Deprecated
- **No authentication by default for `camber serve` on loopback (#134).** A future release, 0.105.0
  at the earliest, will make `--auth token` the default when the server is bound to loopback
  (`make_server` / `serve` with `auth` unset too). Nothing changes in 0.104: `camber serve` on
  loopback without `--auth` prints a one-line notice. Pass `--auth token` to opt in now, or
  `--auth none` to keep an open server after the switch; either one hides the notice. Binds to
  other interfaces, including the container image's `0.0.0.0`, are not part of this change.

### Fixed
- **Writers of a plain store could race each other (#135).** `camber interop openfdd ingest
  --store`, `camber store migrate-partitions --apply` and any script writing through
  `ParquetStore` did not take the store lock, so they could interleave with a `camber datasets
  ingest`, a lab job or each other on the same store. Each now waits for the lock (30 s by
  default), then stops with `store … is locked by <pid>@<host> since <time>`.
- **A flaky portfolio-migrate test (#139).** `test_ambiguous_label_is_refused_and_nothing_is_written`
  compared the whole workspace before and after a refused apply, including `_lock`. A refused apply
  still takes the lock and rewrites its holder line, which carries the time to the second, so the
  test failed only when the two writes fell in different seconds. The comparison now leaves `_lock`
  out, as the lock-contention test already did; a clock that ticks on every call reproduced the
  failure and passes with the fix. Test-only change.
- **The README's links and images work on PyPI (#138).** PyPI's project page is the README, where
  relative paths do not resolve, so the gallery thumbnails and the 35 links into `docs/` were
  broken there. They now use absolute GitHub URLs (images from `raw.githubusercontent.com`), and
  `tests/test_readme_links.py` fails on any relative README link or image, or an image path that
  is not in the repository.
- **A catalog rebuild could hide a write that raced it (#141).** `points()`, `equipment()` and
  `rebuild_catalog()` rebuild `_catalog.json` without the store lock. A rebuild that scanned just
  before a writer's change, and wrote its result back just after, cached a catalog without the new
  points until the next write. Writers now stamp a fresh generation token (`_catalog.gen`) when
  they invalidate the catalog. A rebuild caches its result only if the token it read before
  scanning is still current, and a catalog stamped with an old token is never served. Reads still
  take no lock, a warm `points()` costs the same, and a store that can't be written to still
  answers (it just isn't cached). Two readers rebuilding at once also no longer fail on a shared
  temp file.
- **A resampled run status hid a short-cycling boiler (#143).** A run config that resamples above
  the stored interval gives a 0/1 run status the bin's duty, the share of the bin it was on, as
  ingest and the folder path already do. `boiler_short_cycle` and `compressor_short_cycle` then
  read "firing" as a duty above 0.5, so a boiler firing a quarter of every hour read as never
  firing: 0 starts and 0 % runtime on `synthetic-hw-plant-cycling`'s short-cycling plant at 1 h.
  They now count the fewest starts the duties allow (a sample on for part of its interval holds a
  start or a stop), and `runtime_pct` is the mean duty, the share of time firing. On a 0/1 trend
  both are unchanged. The same plant at 1 h now reads 11.2 starts a day and 23.6 % firing, a warn
  with the coarse-trend caveat; at 15 minutes it is still 20.2 starts a day. `boiler_summer_lockout`
  weights its firing shares (`boiler_running_pct`, `summer_run_pct`) by duty in the same way, so a
  boiler firing part of every hot hour is no longer read as locked out. The duty is kept, not
  replaced by the bin's max: "on at any moment" is a duty above 0, while a max would read a
  quarter-hour of firing as a full hour and still show a cycling boiler as one long run.
  `ParquetStore.read_role_frame(resample=...)` now takes the max for the warm-up and cool-down
  flags, as ingest and `camber.resolve` already did.
  The `plant-boiler-cycling` exercise's hourly lesson changes with it: on the `--hourly` variant
  `PLANT__short_cycling` was `ok` at 0 starts and 0 % firing and is now a `warn` at 11.17 starts
  a day (234 starts, 23.6 % firing, with the coarse-trend caveat); `PLANT__marginal` reads 8.16
  starts a day instead of 7.97, still a warn; the label score for `boiler_short_cycle` on that
  variant goes from TPR 0 to TPR 100 % (FPR stays 33 %). The answer key, question 4, the
  instructor's answer and the variant's description now teach that an hourly count is a floor.

## [0.103.0] — 2026-10-11

**0.103: numpy 2, a hardened `camber serve`, a better trend viewer and lab, reproducible store files, and a hot-water pump lockout check with a synthetic lab case (#122–#126, #128–#130, #132, #133).** **Breaking (#129):** numpy 2.0 or newer is now required (pandas ≥ 2.2.2, pyarrow ≥ 16, matplotlib ≥ 3.8.4); pyarrow 26 needs numpy 2 but does not declare it. **Deployment change (#128):** `camber serve` now answers only the host names it is bound to, a defence against DNS rebinding; behind a proxy or ingress, add the forwarded name with `--allow-host` or `CAMBER_API_ALLOWED_HOSTS`, or those requests get 403. Token auth is opt-in (`--auth token`). The trend viewer shows the whole span with min/max thinning, a date range and brush-to-zoom, and says "local time" when no time zone is recorded (#122). The lab gains a disk check that matches the fetch, extras gating, Remove / From a folder / force re-ingest controls and evidence charts in its report (#123, #125). Stores and caches lock against a second writer, and a read-only shared cache works for a class (#124, #126). New store part files are byte-reproducible for the same inputs, CAMBER and pyarrow versions, and about 1.8x smaller; existing stores read unchanged (#130). `hw_pump_summer_lockout` checks hot-water pumps in warm weather, and a synthetic catalog kind brings the `plant-lockout` workbook exercise (#132, #133).

### Added
- **`camber serve --auth token`: optional token auth, like `camber lab` (#128).** Every route
  (`/ui`, `/facilities`, `/points`, `/history`, `/about`) then needs this run's access token. The
  command prints a launch URL ending in `?token=...` and saves it in a launch file
  `serve-<port>.url` that only you can read (0600, in a 0700 folder). Opening it sets an
  `HttpOnly; SameSite=Strict` session cookie `camber-serve-<port>` and redirects (303) to the same
  page without the token; scripts send `Authorization: Bearer <token>`. Without credentials a
  request is 401 (403 for a wrong token); a bare `GET /health` answers only `{"ok": true}`, for
  health checks. `CAMBER_API_TOKEN` (16 or more characters) fixes the token. Python:
  `make_server(..., auth="token", access_token=...)` and `serve(...)` take the same arguments;
  `python -m camber.api.server` reads `CAMBER_API_AUTH` and `CAMBER_API_TOKEN`. **Off by default
  in 0.103**, so existing deployments keep working; a later release may make token auth the
  default on loopback.
- **`camber serve --allow-host NAME` (repeatable) and `CAMBER_API_ALLOWED_HOSTS` (#128)**, the
  extra `Host` names to answer for a proxy or DNS name: a bare name on any port, `NAME:PORT`
  exactly. `make_server(..., allowed_hosts=[...])` is the Python form, and
  `camber.api.server.check_request` the pure request check.
- **Single-writer locks on plain stores and dataset caches (#124).** Two processes writing one
  cache or one store, such as two `camber lab`s or a lab and `camber datasets ingest`, no longer
  corrupt each other. Every write to a dataset cache holds `<cache>/_lock`: a fetch that
  downloads, `ingest --from-dir`, an ingest extracting archive members or recording an
  acknowledgement, and `remove`. An ingest and `remove --purge-store` hold the store's lock:
  `<store>/_lock` for a plain store, or the workspace's own lock for a store in a portfolio
  workspace, which is re-entered rather than doubled, so the lab's workspace ingest cannot
  deadlock. It is the portfolio's kernel lock (`flock`, `msvcrt` on Windows), released when its
  process exits, so a crash leaves no stale lock. A second writer waits up to 30 seconds, then
  stops with `dataset cache … is locked by <pid>@<host> since <time>` (or `store … is locked by
  …`); `camber datasets` exits 1. Reads take no lock. See
  [DATASETS.md](docs/DATASETS.md#caches-locks-and-a-read-only-shared-cache).
- **A read-only shared cache (#124).** A dataset cache the user cannot write, such as a course
  folder shared read-only across a classroom, now works for `camber datasets` and `camber lab`.
  A fetch whose files are all present and match their pinned SHA-256 downloads and writes
  nothing. An ingest reads the downloads in place and uses the archive members already extracted
  there. Members still needed are extracted into a scratch folder beside the ingest's staging
  area in the store, deleted when the ingest ends. Downloading, `ingest --from-dir` and `remove`
  stop with *the dataset cache … is read-only for this user* and change nothing.
  [Using the lab](docs/LAB.md#sharing-one-cache-read-only) describes setting one up for a class.
- **Trend viewer: date range, brush-to-zoom and a whole-span view (#122).** The `/ui` trend
  viewer that `camber serve` and `camber lab` both serve gains **From** / **to** date inputs,
  **Last 7 days** / **Last 30 days** presets (counted back from the last stored sample) and
  **All**. Brushing a panel now zooms to that span and reads it again at full resolution (up to
  20,000 samples per series), and still selects its samples for the `N selected` readout; **Zoom
  out** steps back through the spans viewed. A line under the controls gives the span shown, its
  time-axis label, and how many samples were drawn out of how many stored. See
  [Visualization](docs/VISUALIZATION.md#live-web-ui-072) and [the lab guide](docs/LAB.md).
- **`/history?max_points=` (#122).** The read API's `/history` takes an optional `max_points`, a
  per-series budget: a longer series is cut into `max_points / 2` equal time buckets, and each
  keeps its minimum and maximum sample, so spikes, dips and flatlines survive the thinning. The
  reply adds `source_count` (rows in the window before thinning), `downsampled`, `max_points`,
  and `first` / `last` (the window's first and last stored timestamp). A request without
  `max_points` returns the same rows as before.
- **`time_axis` on `/points` (#122).** `/points?facility_id=…` also returns how to label that
  facility's time axis: `timezone` (or `null`), `local_label`, and `utc_label` (`null` when the
  store records no time zone for the facility).
- **`camber lab`: remove a dataset from the page (#123).** Each row with anything on disk has a
  **Remove…** button. After you type the dataset id, a job deletes the dataset's downloads and
  extractions from the cache, as `camber datasets remove` does. A tick box also drops its
  facilities from the store, as `--purge-store` does. The route is `POST /lab/jobs/remove`, under
  the lab's usual checks (access token, CSRF token, JSON only, catalog ids only), and needs
  `confirm` equal to the id. In a portfolio workspace the purge follows the facility lifecycle:
  it is refused, with the reason shown in the dialog, while a facility is under a legal hold or
  is suspended, offboarding or archived, and it is checked again under the workspace lock.
  Removals and purges are audited (`lab.remove`, `lab.purge`).
- **`camber lab`: ingest a manual download from a folder (#123).** A `manual: true` entry has a
  **From a folder…** button that does what `camber datasets ingest --from-dir` does
  (`POST /lab/jobs/from-dir`). The server treats the path as untrusted input. It must be an
  absolute path (`~` is expanded) of at most 4096 characters, with no control character, to a
  readable folder. A catalog file that links outside the folder is refused. The folder is only
  read: each pinned file is verified (size and SHA-256), then copied or hard-linked into the
  cache and ingested. A research-only entry needs its acknowledgement; in a workspace the ingest
  is audited (`lab.adopt`).
- **`camber lab`: force re-ingest (#123).** A **force re-ingest** tick box next to **Ingest
  (already fetched)**, also used by **From a folder…**, re-ingests data whose inputs are
  unchanged, which an ingest otherwise skips.
- **`camber lab` shows each dataset's optional extras (#123).** A row lists its
  `requires_extras` (for example *needs xlsx*). When an extra is not installed the badge turns
  red with the install command, the row cannot be ticked, and the server refuses its fetch or
  ingest (409) before anything is downloaded. Before, an Excel dataset downloaded in full and
  then failed at ingest.
- **Evidence charts in the audit report (#125).** `camber report` (the default audit layout) and
  the lab's **report** link now include a **Finding evidence** section. Each chart shows the
  samples the rule judged, shaded where it flags them, drawn with the run's own rule instances
  and data. To bound the report's size and build time, it draws the charts of the 12 worst
  findings that have one (`camber.report.audit.EVIDENCE_LIMIT`; about 50 KB each) and says so
  when there were more. The RCx layout (`--layout rcx`) still gives each issue its own page.
- **`compressor_short_cycle` gets a recommended action and a Learn more link (#125).** The
  recommended action covers the compressor's minimum on/off timers, the stage differential and
  where the calling sensor is mounted, with a site check for the walk-down. The link goes to the
  measures chapter of PNNL's small/medium-sized building re-tuning course (PNNL-SA-92685), new in
  `camber.references` as `pnnl-small-retuning-ch3`. It covers packaged units and their
  thermostats.
- **`hw_pump_summer_lockout`: the hot-water pump's warm-weather lockout (#132).** PNNL's
  heating-plant re-tuning locks out the hot-water pumps, not only the boiler, in warm weather; a
  pump left running all summer with the boiler off passed `boiler_summer_lockout`. The new rule
  reads when the pump runs (its status, else its speed above 5 %, else the loop flow) and
  reports the share of its occupied running hours above the lockout, with the boiler check's
  semantics and severity (warn at 5 %, fault at 20 %), plus `max_oat_running_f` and
  `boiler_off_pct` (the pump running with the boiler off). It shares the boiler's
  `summer_lockout_oat_f` (default 65 °F): a config that sets it on `boiler_summer_lockout` only
  passes it on, recorded in the finding's `param_basis`. It declines on a pump with no hot-water
  point (it may serve another loop). Wired through the parameter registry and `THRESHOLDS.md`,
  the references, the scorecard, a recommended action and a walk-down item (both new for
  `boiler_summer_lockout` too), the RCx report's cause chains (a boiler and its pump on one
  plant are one issue), the G36 plant sequence (`g36_plant`) and a faultlab scenario, now a
  gated synthetic benchmark key (TPR 1.0, FPR 0.0; `coverage.n_scored` 46 → 47). See `docs/PLANT-DETECTORS.md`.
- **Synthetic catalog entries (#133).** A catalog entry of `kind: "synthetic"` is generated
  locally by one of CAMBER's own generators instead of downloaded: `camber datasets fetch` (and
  the lab's **Fetch & ingest**) writes it into the cache from a fixed seed, the same bytes on
  every machine, and it is ingested like any other entry. It carries CAMBER's licence
  (Apache-2.0, open tier), no URL, size or SHA-256, and is labelled synthetic in `camber datasets
  list` / `info`, the lab (a **synthetic** badge and a kind filter) and every report's "Data
  source & licence" block. The catalog check still validates it; the refresh and link-check
  scripts skip it. See `docs/DATASETS.md`, "Synthetic datasets".
- **`synthetic-hw-plant-lockout` and the `plant-lockout` workbook exercise (#133).** The first
  synthetic entry: one hot-water plant, eight weeks hourly with a warm spell, as four plants
  (locked out at 65 °F, left enabled through the warm spell, the pump left running, locked out
  at 58 °F). The new exercise finds the boiler and the pump left on, then varies the lockout
  (`plant-lockout--58f`, `plant-lockout--75f`) to show how the verdict rests on it and where
  each plant really stops firing. Its answers are pinned by the workbook tests, with an
  instructor key and a figure.

### Changed
- **Breaking: numpy 2 is now required; numpy 1.x is no longer supported (#129).** pyarrow 26
  needs numpy 2 at import but does not declare it, so pip could pair it with numpy 1.x and
  `import pyarrow` then failed. The core floors move to the first releases built for numpy 2:
  `numpy>=2.0,<3`, `pandas>=2.2.2,<3`, `pyarrow>=16.0` and `matplotlib>=3.8.4`. The `ml` extra
  moves to `scikit-learn>=1.4.2`. CI's numpy-1.x leg becomes a numpy-2.0 leg, the `pyarrow<26`
  workaround is gone, and the min-deps job pins the new floors.
- **`camber serve` binding `0.0.0.0` or `::` refuses to start without a Host allowlist (#128)**
  (`--allow-host` or `CAMBER_API_ALLOWED_HOSTS`; `'*'` turns the check off and is unsafe). The
  Docker image and `docker-compose.yml` now set `CAMBER_API_ALLOWED_HOSTS=localhost,127.0.0.1`,
  and `deploy/k8s/camber-api.yaml` lists the Service's names and probes `/health` with
  `Host: localhost`: **add the hostname your proxy or ingress forwards**, or it gets 403. Bound to
  anything but loopback without `--auth token`, `camber serve` prints a warning. `--host ::`
  (IPv6) now binds. `HEAD`, `PUT`, `DELETE`, `PATCH` and `OPTIONS` get 405 like `POST` (they were
  501); the API stays GET-only.
- **The lab's token, cookie and launch-file helpers moved to `camber._access` (#128)**, shared with
  `camber serve`; `camber.lab._auth` keeps its names and behaviour.
- **A fetch of files already in the cache no longer rewrites the manifest (#124).** When every
  file of the subset is present, verifies, and is already recorded, `camber datasets fetch` (and
  the lab's **Fetch & ingest**) leaves `manifest.json` untouched; its `fetched_at` keeps the time
  of the fetch that downloaded the files. A research-only fetch still records its
  acknowledgement.
- **Research-only acknowledgements against a read-only cache are per user (#124).** They go to
  the user's own ledger, `$XDG_STATE_HOME/camber/datasets/acknowledgements.json` (else
  `~/.local/state/camber/datasets/acknowledgements.json`), with the cache's path in each record.
  Only that ledger counts for a read-only cache. The acceptance recorded in the cache by whoever
  filled it does not stand in for another user's, so each learner accepts a research-only
  dataset's terms once. A writable cache keeps its acknowledgements in its own ledger and
  manifest, as before.
- **The trend viewer draws each series' whole span, not its first 5,000 samples (#122).** It
  opens on every stored sample, thinned on the server to at most 2,000 per series with a min/max
  envelope, so a long series is no longer cut to its first few days and series that start at
  different times share one axis.
- **The trend viewer's facility and equipment lists refresh without a reload (#122).** They are
  read again when either list is opened, on **Reload lists** or **Refresh**, and on each live
  poll, so a facility ingested after the page opened appears; the chosen equipment and ticked
  roles are kept.
- **A facility with a recorded time zone has its trend axis labelled `local time (<zone>)`
  (#122)**, and `time (UTC)` only while the **UTC** box is ticked. It was `time (<zone>)`.
- **The lab's disk check counts everything a job writes (#123).** The page and the server now
  add up the download still to fetch, the archive members the ingest extracts into the cache,
  and the store estimate. Each gets the fetch's own 5 % headroom
  (`camber.datasets._fetch.DISK_MARGIN`, which the fetch uses too), and the needs are added
  together when the cache and the store share a disk. Extraction is exact from the zip's own
  index once it is downloaded, and estimated from the catalog's `extracted_size` before.
  **Fetch & ingest** is disabled for any selection the fetch would refuse. **Ingest (already
  fetched)** is now checked against the store's disk too. The server refuses a job that does
  not fit with HTTP 507 before anything is downloaded. Before, the check left out extraction
  and the margin, so Fetch could be enabled and still fail.
- **The workbook's report notes now say the audit report has evidence charts (#125)**
  (`air-economizer`, `air-scheduling`, `air-static-pressure`, `zone-reheat-overcooling`,
  `zone-reheat-saturated`), and `--layout rcx` adds a page per issue.
- **`docs/LAB.md`, `docs/DATASETS.md`, `docs/CLI.md` and `docs/SECURITY.md` §11** describe the
  new lab controls and POST routes, and the lab screenshots are re-rendered.
- **`boiler_summer_lockout` reports `max_oat_running_f` (#133)**, the warmest outdoor
  temperature at which the boiler fired: where the plant actually stops firing, to compare with
  the lockout. Severities and the other metrics are unchanged.

### Fixed
- **Store part files are byte-reproducible (#130).** Ingesting the same files twice gave part
  files with the same rows in a different order, because the writer stored rows in the order its
  threads finished. Every part the store writes (`write_long`, `write_rollup`, dataset ingest,
  `migrate-partitions` and retention rollups) now has its rows sorted by
  `(ts, equip, role, equip_class, value)` and is written single-threaded as one row group, with
  fixed writer options. It carries a `camber.layout = "1"` marker and no pandas metadata, which
  recorded library versions. The same inputs with the same CAMBER and pyarrow versions now give
  byte-identical files with the same names. On another pyarrow version only the rows are
  guaranteed. Large ingests are up to about 3x smaller (2 to 3.4x for the LBNL simulation archives,
  `irish-ahu` and `nuig-ahu101`). Existing stores read unchanged and need no re-ingest;
  `camber datasets ingest <id> --force` rewrites one in the new layout. Edge forwarder parts are
  unchanged. See [SCALE.md](docs/SCALE.md#reproducible-part-files).
- **The trend viewer's UTC box stays hidden when no time zone is recorded (#122).** A `display`
  rule on the control labels overrode the `hidden` attribute, so the box still showed.
- **`ornl-frp-ops` ingests without pandas `DtypeWarning`s (#126).** The export has a units row
  under its header, and pandas' chunked CSV parsing typed each chunk of a column separately,
  then warned that the columns had mixed types. The catalog's CSV reader now types each column
  in one pass (`low_memory=False`). The stored values are unchanged: every value column goes
  through `pd.to_numeric` either way, and re-ingesting all 23 locally cached dataset subsets
  gives the same rows as 0.102. No other catalog dataset raised the warning.
- **The trend viewer no longer labels a zone-less clock as UTC (#122).** For a facility with no
  recorded time zone (most LBNL and ORNL datasets), the time axis and hover readout said UTC
  although they showed the publisher's local clock. They now say `local time (no time zone
  recorded)`, and there is no UTC box.
- **`/history` answers a malformed `start`, `end`, `limit` or `max_points` with a 400 (#122)**,
  naming the parameter, instead of a 500. An offset on `start` / `end` is dropped rather than
  compared against the store's naive wall clock.
- **`camber lab` on a port already in use (#123)** printed only `error: [Errno 48] Address
  already in use`. It now says the port is taken and suggests `camber lab --port N`, and exits
  with code 1, with no traceback. `make_lab_server` raises `camber.lab.PortInUse`, an `OSError`.
- **`scripts/docs_figures.py --screenshots` captured the lab's 401 page for
  `docs/img/shots/lab.png`** after 0.102 required an access token on every lab route. It now opens
  the launch URL the lab prints.

### Security
- **`camber serve` checks the `Host` header against DNS rebinding (#128).** A web page on another
  site could point its own DNS name at your machine and read the store through your browser. Every
  request whose `Host` is not an allowed name is now refused with 403: `127.0.0.1`, `localhost`
  and `[::1]` at the bound port when bound to loopback, the bound host, and any `--allow-host`.
  On by default. [SECURITY.md](docs/SECURITY.md) section 3 describes the model and its residual
  risks (plain HTTP, no accounts, a per-process token).
