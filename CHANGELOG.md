# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## Older releases

Releases before 0.98.0 are archived under [`docs/changelog/`](docs/changelog/index.md):

- [0.90.0 to 0.97.0](docs/changelog/changelog-0.90-0.97.md)
- [0.60.0 to 0.89.0](docs/changelog/changelog-0.60-0.89.md)
- [0.1.0 to 0.59.0](docs/changelog/changelog-0.1-0.59.md)

## [0.105.0] — Unreleased

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

## [0.104.0] — Unreleased

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

## [0.102.0] — 2026-10-10

**0.102: follow-ups from 0.101, sample charts across the docs, a guide to the lab, and an access token on every lab route (#110–#121, #127).** The suggester's unitless range check no longer overturns a strong name on a dirty series (#110); the `air-economizer` workbook exercise teaches the economizer low-limit lockout and the capstone adopts it (#111); and the RCx report ranks uncosted issues of equal severity by confidence before the issue key, so the capstone's stuck-damper issue ranks first again (#112). The docs site gains charts and screenshots rendered by CAMBER's own chart code (#113) and a step-by-step guide to `camber lab` (#121). Evidence charts shade exactly what each rule flags and name roles with their units (#114–#117, #119). A stuck supply-air sensor on a scheduled fan is now flagged (#118). **Behaviour change (#120):** `free_cooling_missed` no longer judges fan-off hours, so missed shares rise on units whose fan runs only when occupied; the `air-economizer` and capstone answers move with it. **Security (#127):** `camber lab` now requires a per-run access token on every route; open the URL it prints.

### Added
- **Sample charts and screenshots across the docs (#113).** The docs site and README had no
  images. `scripts/docs_figures.py` renders 36 figures and three README thumbnails into
  `docs/img/` with CAMBER's own chart code: the `camber.charts` primitives, the rules' evidence
  hooks and the drift detectors' frozen-baseline bands. The data is deterministic synthetic data (`camber.synth`, the `ahusim`,
  `driftsim`, `vavsim` and `pumpsim` simulators, the DCV simulator and small seeded generators);
  no downloaded dataset is read. A fixed style, the Agg backend, a fixed dpi and size and PNGs
  with no metadata keep reruns byte-stable on one machine.
- **One figure per chart pattern in [Visualization](docs/VISUALIZATION.md) (#113)**, next to its
  code example: readiness ribbon, fault-annotated trend, load carpet (with a new short section),
  quality dashboard, OAT scatter, diagnostic scatter, the fitted drift band, rule evidence, cohort
  small multiples, M&V savings, load profile and load-duration curve, box by hour, and a new
  CUSUM and energy-signature section.
- **A hero figure on the analytics pages (#113)**: free cooling, chiller, AHU, VAV and pump
  drift, M&V (the change-point fit and the savings chart), sensor health and schedule inference.
- **The evidence chart each workbook exercise discusses (#113)**, with a caption that marks it as
  a synthetic illustration rather than the exercise's dataset: the stuck outdoor-air damper's OA
  fraction, the leaking cooling valve, the reheat penalty, chiller kW/ton, tower approach, the
  change-point baseline and the rest. The exercises' text and answers are unchanged.
- **Screenshots (#113)** of the RCx report, the site report, the `camber lab` catalog page and
  the `camber serve` trend viewer, on a synthetic two-AHU site. `--screenshots` takes them with
  headless Google Chrome, serving on 127.0.0.1 and stopping the servers afterwards; it is
  optional, as CI has no browser.
- **A small gallery in the README (#113)**: three thumbnails linking to the docs pages.
- **`scripts/docs_figures.py --check` (#113)**, run by `scripts/gates.sh` and by
  `tests/test_docs_figures.py`. It fails when an image referenced from `docs/**/*.md` or
  `README.md` is missing or over 150 KB, when a docs image has no alt text or caption, when a
  generated figure is missing, unreferenced or stale, or when `docs/img/` exceeds 5 MB.
- **[Using the lab](docs/LAB.md), a step-by-step guide to `camber lab` (#121)**, early in the
  docs navigation and linked from the README, DATASETS.md, CLI.md and the workbook. A first
  session on the open `ornl-frp-ops` dataset (install, start, pick a dataset, Fetch & ingest,
  trends, the report's top finding, the exercise, and the matching CLI commands), then how to
  read the trend viewer and the report, where the cache and the store live and how to clean
  them up, a classroom setup (pre-fetching for an offline room, manual downloads with
  `--from-dir`, a portfolio workspace, what loopback-only means on a shared computer) and
  troubleshooting. `scripts/docs_figures.py --screenshots` adds five walkthrough screenshots;
  unlike the other figures they show a real open dataset (credited in their captions), read
  from a local copy through the verified `--from-dir` path and never downloaded, and they are
  skipped when no local copy is present.

### Changed
- **Workbook `air-economizer` teaches the economizer low-limit lockout (#111).** A new step 5
  has learners read `free_cooling_missed`'s warn on the fault-free unit (17.5 %). They then find
  from the trends and the unit's sequence that 899 of its 965 missed hours (93 %) fall below the
  documented 33.8 °F lockout, where the damper is held at its minimum by design. They set
  `low_limit_f` 33.8 and re-run: the fault-free unit reads ok (1.96 %), and the dampers stuck at
  10 % and 25 % stay a fault (49.6 %). The exercise config leaves the low limit out on purpose,
  and its `_comment` says so. The declaration pins both readings, and the page (a new step and
  question, with later ones renumbered) and the instructor key are updated. The stand-in now
  models the lockout (four cold days below it), so both readings are also checked offline.
- **Workbook `capstone` adopts the same low limit (#111), which intentionally changes its
  answers.** `free_cooling_missed` in the capstone config now sets `low_limit_f` 33.8, with a
  basis, to match the dataset template and the economizer exercise. On the real data the
  control unit reads ok (2.0 %, was a 17 % warn) and loses its spurious "enable the economizer"
  issue and walk-down item. The onset unit reads a warn at 21 % (was a 30 % fault): six healthy
  months dilute a 1 July onset in a year-long share. Its damper issue keeps its cause heading
  and confidence H, and it now has stronger cause evidence (`commanded_open_pct` 89.9 % of 640
  h, was 40.3 % of 673 h). With #112 it ranks first again: among
  uncosted issues of one severity, confidence now decides the order before the issue key. Drift is
  unchanged (-83, fault). The declaration, the page (questions 1 and 2, a caveat) and the key are
  updated.

- **RCx: uncosted issues of one severity rank by confidence before the issue key (#112).**
  `link_findings` used to order uncosted issues of one tier by issue key, a fixed but arbitrary
  tie-break. They now rank by input-trust confidence, H before M before L, and then by key.
  Severity, conditional-last and costed-first by dollars are unchanged, and costed issues keep
  their dollar order. This intentionally changes the RCx ranking where uncosted issues of one
  tier differ in confidence. On the 15 catalog run templates (default subsets), 4 of 96 issues
  change rank, in 2 reports, and each pair swaps. In `lbnl-ddahu`, `DMPRStuck_OA_100`'s
  `outdoor_air_fraction` fault (H) moves from 2 to 1 above `DMPRStuck_OA_0`'s `g36_afdd` fault
  (M). In `ornl-frp-ops`, `base_heating`'s `night_weekend_setback` fault (H) moves from 2 to 1
  above `sb_heating`'s `compressor_short_cycle` fault (M). Both reports keep their chosen week;
  only its evidence score, which weights issues by 1/rank, changes. The RCx golden is unchanged:
  its synthetic site has no uncosted tie with mixed confidence. `docs/RCX-REPORT.md` describes
  the new order.
- **Workbook `capstone`: the damper issue ranks first again (#112).** On the real data, the onset
  unit's stuck-damper issue (an uncosted warn, confidence H) now ranks 1, above the onset unit's
  supply-air reset and the control's static-pressure reset (uncosted warns, confidence M). It
  ranked 3 in #111. The declaration asserts rank 1 in both modes. The page's question 1 asks what
  puts the issue first when nothing is costed. The instructor key explains the confidence order,
  lists the damper first in the walk-down, and rewords the common mistake about reading an
  uncosted rank as a measure of size.
- **The unitless range check no longer overturns a strong name on a dirty series (#110).** On
  the time-series path (the default when a series is passed, from 0.101), a point with no
  declared unit is range-checked in every plausible unit. Two changes act on that check only:
  - an exact zero that the role cannot read in any plausible unit (a 0 °C room, 0 ppm CO2) is
    a dropout and is left out; an all-zero series gives no range verdict;
  - when the role is the name's best lexical match and scores at least 0.6, the check keeps at
    least `STRONG_NAME_RANGE_FLOOR` (0.75) of its score. A series with no reading inside the
    bounds in any unit (a `-999` dead channel) is still demoted in full.

  **This intentionally changes suggestions when a series is passed without a unit.** A declared
  unit, the `use_timeseries=False` opt-out and the name-only default are unchanged. At default
  settings with the series passed, top-1 0.101 → 0.102:
  - held-out catalog names, real names, simulated LBNL sets, BTS anonymised: unchanged
    (73.0, 95.7, 74.4, 46.2 %);
  - BTS Brick-class names (upper bound): 89.4 → 93.4 % (name alone 93.2 %);
  - the five synthetic styles: 70.4-89.9 → 73.0-93.9 %; `ahu_03_supply_temp` 89.9 → 93.9 %
    (name alone 93.9 %).

  No pool loses top-1 or top-3. Before and after tables, and the 4 synthetic points that now
  miss, are in `docs/MAPPING-ASSIST.md`.
- **`camber.mapping_assist.STRONG_NAME_RANGE_FLOOR`** (new, provisional): the floor above, a
  module constant like `WATER_AIR_PENALTY`.

None of these figures is a gated benchmark.

### Fixed
- **`camber lab` needs its access token on every route (#127, security).** The lab binds
  127.0.0.1, but every GET was unauthenticated and the page carried the CSRF token, so any
  other account on a shared computer could load `/lab`, read the token, queue fetches and
  ingests, and read the ingested data, reports and trends. Each run now makes a random access
  token, separate from the CSRF token, and prints the launch URL
  `http://127.0.0.1:<port>/lab?token=...`. A valid `?token=` sets an `HttpOnly`,
  `SameSite=Strict` session cookie and redirects to the URL without it; every request, GET or
  POST, the delegated `/ui`, `/facilities`, `/points` and `/history` included, then needs the
  cookie (or `Authorization: Bearer <token>`), else 401. POSTs still need the CSRF header and
  the Origin / Host checks. The URL is also saved to a 0600 launch file, `lab-<port>.url`, in a
  0700 folder under `$XDG_RUNTIME_DIR/camber` or `~/.config/camber`; a folder or file that is
  group- or world-writable or owned by another account is refused. The lab opens no browser,
  so the token never appears in `ps`. New in the provisional API: `LabApp(access_token=...)`,
  `LabApp.launch_url()` and `camber.lab.announce`. SECURITY.md §11 describes the model and
  its residual risks; LAB.md, CLI.md, DATASETS.md, the README and the workbook pages now say to
  open the URL `camber lab` prints.
- **A stuck supply-air sensor on a scheduled fan is now flagged (#118).** With the fan gate on,
  a fan-dependent role's flat run was broken at every fan-off span, so a sensor frozen for four
  days on a 13 h schedule never reached the 24 h stuck limit. A run now joins across a fan-off
  span when the reading held the same value through it, and its length is the fan-on hours it
  covers, as the plant gate already counts running hours. A reading that moves with the fan off
  still starts a new run, so a supply air held at setpoint each day is not flagged.
- **The `simultaneous_heat_cool` evidence chart uses the rule's threshold on the percent scale
  (#114).** `no_simultaneous_template` assumed 0–1 valves (`active=0.05`), so on CAMBER's percent
  valves it shaded points from 0.05 % open. It now defaults to `active=5.0`, `y_max=100.0` with
  "(%)" axes, and the rule's evidence passes its own flagged samples (occupied, both valves above
  5 %, less the dehumidification-with-reheat classes it does not count) through a new
  `violating=` argument of `diagnostic_scatter`. A test checks that the shaded samples equal the
  rule's. `economizer_template` still reads its damper as 0–1.
- **Evidence axes and carpet colour bars name the role and its unit (#115).** The carpet colour
  bar always read "Load (kW)", and scatter axes showed raw role names such as
  `COND_APPROACH_TEMP`. Evidence carpets, OAT scatters, diagnostic-axis fallbacks, drift-band
  axes and multi-trend legends now read e.g. "cond approach temp (°F)", with the unit from
  `camber.api.ui.role_units`, the trend viewer's source. A multi-trend whose series share a unit
  shows it on the y axis. The site report's carpet colour bar names its column.
- **`savings_chart` keeps the unit's case (#116).** `ylabel="kWh"` rendered "Cumulative kwh".
  The default label is now "energy".
- **Cohort small multiples get concise date ticks (#117).** `cohort_small_multiples` panels use
  matplotlib's concise date formatter (at most four ticks), so multi-week dates no longer
  overlap. The docs-figure workaround is removed.
- **Seven more rules shade their flagged samples in their evidence (#119).** Each has a
  `violation_mask(frame)` built from what the rule counts:
  - `leaking_valve`: the fan-on, both-valves-shut samples counted in `hw_leak_pct` /
    `chw_leak_pct`;
  - `static_pressure_reset`: when not resetting, the judged setpoint samples on days it held;
  - `dcv_verification`: high-demand samples the OA did not answer (static or uncorrelated), plus
    the setpoint-breach, below-floor and unventilated samples;
  - `co2_ventilation`: the occupied samples counted as under-ventilated, or as over-ventilated
    when that is the finding;
  - `cooling_tower_approach`: an approach-against-wet-bulb scatter of the judged samples, the ones
    above design + 3 °F in red;
  - `chw_plant_reset`: low-ΔT running hours where ΔT is judged, and CHWST held at or below 46 °F
    when there is no reset;
  - `boiler_short_cycle`: each start on a day with at least `max_starts_per_day` starts.

  The analysis functions behind them take an optional `masks_out` dict; their results are
  unchanged. A one-sample shaded span in a multi-trend is now drawn to the next sample instead
  of as a hairline.

  No verdict or finding changes. In the RCx report these issues, and `simultaneous_heat_cool`,
  now report violation hours instead of "not measured", and the evidence-mode representative week
  can move to the top issue's fault week. The synthetic RCx golden's week moves from 2026-03-09 to
  2026-03-16, the planted reheat week of its top issue. The workbook cooling-tower and CHW-reset
  figures are now drawn from those rules' evidence.
- **`free_cooling_missed` judges fan-on hours only, and its evidence plots only the hours it
  judges (#120).** The rule had no fan gate: overnight and weekend hours with the supply fan
  stopped counted as free-cooling weather, so they diluted the missed share, and a cooling-valve
  output parked open with the fan off read as missed free cooling. It now uses the fan-on gate the
  other air-side rules use (`camber.schedules.fan_on_mask`: fan status, else speed, else
  airflow), with a `fan_gate` parameter (default on) and the `fan_gate` and `n_masked_fan_off`
  metrics. A unit with no fan signal is judged ungated, as before, and names
  `supply_fan_status` in `_missing_optional`. The evidence chart is now a diagnostic scatter of
  the judged samples (fan on, OAT inside the free-cooling window) with the missed ones in red;
  integrated-economizer samples are drawn but never red, so the chart's out-of-band share is
  `missed_pct` (the rule's mask is what the chart shades, through #114's `violating=`), and the
  axes are labelled by role and unit (#115). It used to scatter every sample, fan-off and
  warm-weather hours included. No gated benchmark moves.

  This intentionally raises the missed share on units whose fan stops, and the workbook answers
  move with it (real `lbnl-sdahu` data; the fan runs only in occupied hours there):
  - `air-economizer`: the fault-free unit is a **fault** at 34.1 % (was a 17.5 % warn); its 965
    missed hours, 899 of them below the lockout, are unchanged, but the fan-off hours no longer
    dilute them. With `low_limit_f` 33.8 it reads ok at 3.63 % (was 1.96 %), with 1,014 hours
    set aside (was 2,141). The dampers stuck at 10 % and 25 % miss free cooling in 100 % of the
    fan-on free-cooling hours, with and without the lockout (were 49.4 % and 49.6 %). The page
    now frames step 5 as the healthy unit reading as a full fault until the lockout is set. The
    Irish unit trends no fan signal and is unchanged (8.0 %, ok).
  - `capstone`: the onset unit is a **fault** at 41.7 % (was a 21 % warn) and the control ok at
    3.6 % (was 2.0 %); `commanded_open_pct` is 90.3 (was 89.9) on the same 640 h. The damper
    issue is now the report's only fault and still ranks first, with confidence H; the rest of
    the ranking is unchanged.

  The declarations, the two exercise pages and the instructor key are updated.

## [0.101.0] — 2026-10-04

**0.101: a seasonal OA damper minimum, data-led point-role suggestions by default, and a
walk-down that follows the heading cause (#105–#109).** `g36_afdd` takes a seasonal OA damper
minimum, `oa_damper_min_by_month` (#105). The `lbnl-ddahu` template uses it: 45 %, and 28 % in
June to August. The unit's winter heating hours return to OS#1 there, and its winter hours
idling at the minimum are no longer read as free cooling. #105 has a limit: FC6 still cannot
catch the dual-duct damper stuck shut (`DMPRStuck_OA_0`) on this data. Outside the summer that
run's damper command saturates open, so its hours are OS#5, where FC6 does not apply. The run is
still caught, through FC8, FC10, FC11 and FC12. The time-series point-role suggester gains a
setpoint guard and a range check for points with no unit (#106). **Default change (#107):**
`suggest_roles`, `review_unmapped` and `review_bacnet` now read the data whenever a series is
passed; `use_timeseries=False` opts out and gives the 0.100 output byte for byte. With a series
passed, top-1 rises against 0.100 on every evaluation pool. Against the name alone, pools with
strong names dip slightly on dirty series: BTS Brick-class names read 89.4 % with the series
against 93.2 % from the name alone, because site C's negative airflows and dropouts fail the
range check. `docs/MAPPING-ASSIST.md` advises `use_timeseries=False`, or cleaning the series,
where the names are already reliable. The RCx walk-down's equipment item now follows the cause
that heads the issue (#108), which changes the two re-headed catalog issues (`lbnl-ddahu`
`DMPRStuck_OA_0`, `lbnl-sdahu` `damper_stuck_075`). The open-fdd cross-check leaves the
per-verdict list out of its JSON by default (#109). It was re-run once on the merged code with
all three engines: only CAMBER's `lbnl-ddahu` verdicts change, as #105 reported, and every
engine's any-FC result is unchanged. The synthetic, fleet, LBNL, BDG2 and BDG2 savings benchmark
gates did not move.

### Changed
- **The walk-down's equipment item follows the cause that heads the RCx issue (#108).** Since
  0.100 (#101) a member finding's equipment-level cause can head an issue, but the "Verify on
  site" equipment item still followed the root finding. `camber.walkdown.site_checks` takes
  `heading_causes={issue key: (rule, cause key)}` (additive, provisional), and the RCx report
  passes the cause it chose for each re-headed issue. That issue's equipment item is then the
  member rule's template for that cause, with the member rule's references; an issue the root
  heads keeps the root's item. This intentionally changes the Verify on site section for
  re-headed issues only. On the 15 catalog run templates (default subsets), 2 of 98 issues
  change, the two #101 re-headed: `lbnl-ddahu` `DMPRStuck_OA_0` and `lbnl-sdahu`
  `damper_stuck_075` now ask for the outdoor-air damper's blades, linkage and actuator under a
  full open and close command, not the minimum-position setting or the damper on a hot hour. The
  RCx golden is unchanged (its synthetic site has no re-headed issue).
- **open-fdd cross-check: the per-verdict list is left out of the JSON by default (#109).**
  `run_crosscheck.py` now omits the per-verdict list and collapses the per-equipment "not
  evaluated" reasons to counts unless `--keep-verdicts` is passed; a month-window run otherwise
  writes about 200k more lines. `--omit-verdicts` is still accepted (it is the default). The
  Markdown output is the same either way. The committed results were re-run at integration
  with the new default, so none of them carries the per-verdict list.
- **The `lbnl-ddahu` template sets the unit's seasonal damper minimum (#105).** `g36_afdd` now
  runs with `oa_damper_min` 45 and `oa_damper_min_by_month` 28 in June to August (the documented
  sequence positions), where the learned minimum was the 28 % summer position all year. Hourly,
  every verdict holds: the fault-free and `DMPRStuck_OA_100` runs stay *ok* and `DMPRStuck_OA_0`
  *fault*. The winter heating hours at the 45 % minimum return to OS#1, so FC6 judges 1,111 h on
  the fault-free run (was 744 h; 0.09 %) and 1,273 h on `DMPRStuck_OA_100` (was 762 h; 0.08 %),
  and FC5 and FC7 are evaluated there (367 h and 529 h, 0 %). Winter hours idling at the 45 %
  minimum are no longer free cooling: the fault-free run's 4 h of FC9 hits go, and on
  `DMPRStuck_OA_0` FC8 reads 100 % of 48 h (was 39.7 % of 121 h) and FC9 0 % (was 20.7 %).
  **FC6 still does not catch `DMPRStuck_OA_0`** (1.96 % of 766 h, unchanged), which #105 set out
  to do. Outside June to August that run's damper command saturates (median 100 %) while the hot
  deck heats, or both coils run, so its hours are OS#5, where G36 does not apply FC6, and the
  temperature balance there reads a median 17.7 % OA, within G36's 30-point tolerance of the
  31.8 % minimum anyway. `lbnl-sdahu` is unchanged. `g36_afdd` is not a scored target, so
  `camber datasets score` and the gated benchmarks do not move. The open-fdd cross-check's CAMBER
  verdicts on `lbnl-ddahu` change (any-FC unchanged in both windows); docs/ECOSYSTEM.md has the
  before/after.
- **Default change: the point-role suggester reads the data when a series is passed (#107).**
  `suggest_roles`, `review_unmapped` and `camber.interop.bacnet.review_bacnet` now use the
  time-series path (`FeatureSuggester(use_timeseries=True)`) when a series is passed for a token
  and no suggester is given. **This intentionally changes their output whenever a series is
  passed**, including the BACnet review path when `series_by_name` is given. The new keyword
  `use_timeseries=False` opts out and gives the 0.100 output byte for byte. Without a series the
  output is unchanged, and so are `roles_from_bacnet` and `vendor_aliases`, which pass no
  series. The `FeatureSuggester` class default stays name-only. At default settings with the
  series passed (no unit, no outdoor-air series), top-1 before → after:
  - held-out catalog names: 49.4 → 73.0 %;
  - real names: 84.8 → 95.7 %;
  - simulated LBNL sets: 73.1 → 74.4 %;
  - BTS anonymised: 0.0 → 46.2 %;
  - BTS Brick-class names (upper bound): 25.5 → 89.4 %;
  - the five synthetic styles: 15-26 → 70-90 %.

  On strong names the unitless range check costs a little against the name alone. With BTS
  Brick-class names it is 89.4 % against 93.2 %, because site C's negative airflows are out of
  range. The full table is in `docs/MAPPING-ASSIST.md`.
- **Time-series suggester, step 1: held-out measurement and the two known fixes (#106).** Both
  fixes act only on the time-series path, which from #107 is the default when a series is
  passed.
  - **Setpoint guard.** When a point's name says setpoint (`setpoint`, `SP`, `STPT`,
    `RMCLGSPT`), the data ranks only the setpoint roles. `robod`'s `temp_setpoint` stays
    `cool_sp`, where the data alone had made it `space_temp`.
  - **Range check without a unit.** A point with no declared unit is now range-checked on the
    time-series path. Before, it skipped the check. It is read in every plausible unit of the
    role (the units the role templates try), and the best reading counts. A °C room temperature
    passes, and a sentinel such as `-999` is demoted, as on the default path.
  - **Measured on the held-out catalog names with their data** (`catalog_names.py --data`, new;
    89 of the 223 names have a usable open-tier series). Top-1 / top-3 %:
    - name only: 76.4 / 79.8;
    - name plus data: 76.4 / 82.0 before, 75.3 / 80.9 after;
    - the default suggester with the series passed: 49.4 / 50.6. Its range check reads a
      unitless temperature as °F.

    After the fixes the data helps 3 points and hurts 4. The 4 losses are refrigerated-case air
    temperatures labelled as air-handler roles. Two of them, at about -3 °F, are below those
    roles' physical bounds.
  - **Other evaluation pools** (name plus data, top-1): real names 95.3 → 95.7 %, with 0 losses
    (was 2); real names excluding in-sample names 89.1 → 90.7 %. Simulated LBNL sets and BTS are
    unchanged. The synthetic names move by at most +0.1. Details and the helped and hurt lists
    are in `docs/MAPPING-ASSIST.md`.
- **`real_names.dataset_points` takes `keep_series`** and returns each point's 15-minute series
  with its profile. The evaluation scripts only.

None of these figures is a gated benchmark. The synthetic, fleet, LBNL, BDG2 and BDG2 savings
benchmark gates are not affected.

### Added
- **`g36_afdd` takes a seasonal OA damper minimum (#105).** A new `oa_damper_min_by_month`
  parameter (`{month: position %}`, mirroring `min_oa_pct_by_month`) overrides `oa_damper_min`
  in the months it names, so the operating states judge each interval's OA damper against its own
  month's minimum position: heating (OS#1) at it, free cooling (OS#2) beyond it. It needs
  `oa_damper_min`, the position in the other months. `run_g36_afdd` gains the same keyword and
  `G36Result` a trailing `oa_damper_min_by_month` field; the finding reports the metric only when
  it is set, and a caveat names the seasonal minimum. The default is `None`, so default outputs
  are byte-identical. Param docs and `docs/THRESHOLDS.md` cover it.

## [0.100.0] — 2026-10-04

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

### Changed
- **Dataset templates: `lbnl-ddahu` and `lbnl-sdahu` run `free_cooling_missed` (#101).** Both
  templates now run it at the documented 60 °F high limit, reading each unit's own OA damper
  command and mixed-air temperature. Hourly, `lbnl-ddahu` `DMPRStuck_OA_0` reads *warn* (14.5 %
  of free-cooling hours, cause: damper not delivering), and its fault-free and `DMPRStuck_OA_100`
  runs read *ok*. `lbnl-sdahu` also sets the unit's documented 33.8 °F low-limit lockout
  (`low_limit_f`, below), so 3,374 of its 5,515 sub-60 °F hours count as free-cooling weather.
  There `damper_stuck_010` and `damper_stuck_025` read *fault* (49.6 %), and
  `onset_damper_stuck_025` and `damper_stuck_075` read *warn* (21.1 % and 20.8 %), all with cause
  damper not delivering. The fault-free and `coi_leakage_010` runs read *ok* (1.96 % and 1.93 %).
  Without the low limit they read *warn* (17.5 % and 18.3 %), because 93 % of their missed hours
  fall below the lockout, where the sequence holds the damper at its minimum by design. The
  template comment records both. The lbnl-sdahu RCx report has 11 issues, up from 8.
  `free_cooling_missed` is not a scored target, so `camber datasets score` and the gated
  benchmarks do not move. The other four air-handler templates do not run it, and each comment
  says why. `nuig-ahu101` is 100 % outdoor air with no mixing box. On `lbnl-b59`, `ornl-frp-ops`
  and `ornl-frp-vav` the rooftop units trend no cooling-valve command, so the rule would decline.
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

### Added
- **`free_cooling_missed` takes an economizer low-limit lockout, `low_limit_f` (provisional).**
  Many sequences lock the economizer out below a low outdoor-air temperature and hold the OA
  damper at its minimum, so mechanical cooling in that weather is not missed free cooling. With
  `low_limit_f` set, hours below it are neither available nor missed. They are reported as
  `low_limit_excluded_hours` (and `low_limit_cooling_hours`, those with mechanical cooling
  running), with a caveat. `camber.freecooling.free_cooling_opportunity` takes the same
  parameter through the same weather test and reports `hours_low_limit_excluded`. The RCx
  report's economizer page passes the rule's value to it. The default `None` keeps both outputs
  byte-identical, with none of the new metrics or fields. `lbnl-sdahu` sets 33.8 °F. `lbnl-ddahu`
  does not: no lockout is documented for that unit, and on its fault-free run the OA damper
  modulates above its minimum down to about 15 °F. Its findings are unchanged. The two #101 RCx
  headings hold. The `air-economizer` and `capstone` workbook exercises keep their own configs
  and do not set it, so their answers do not move.
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

### Changed
- **A store facility ingested from open-fdd knows its zone.** A store-source config's
  `source.timezone` now defaults to the zone the facility was ingested in, as it already did for
  catalog datasets. This is additive. A facility without an `openfdd` provenance block is
  unchanged.
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

## [0.98.0] — 2026-10-03

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

### Changed
- **Equipment class on role frames; the DCV return-air caveat only where it applies (#85 item
  4).** The rule runners (and a run's lazy `frame_for`) set
  `frame.attrs["camber_equip_class"]` to the equipment's class. `dcv_verification` keeps its
  "typically return-air CO₂" caveat on air handlers and on equipment of absent or unrecognised
  class (direct API calls are unchanged), and drops it on terminals and fan coils, whose CO₂ is
  the room's own: the finnish-dcv, b4b-windesheim and sdu-ou44 rooms (class `VAV`) lose it. Only
  that caveat changes; severities and metrics do not. Workbook `zone-dcv` pins the caveat absent
  and its instructor discussion point is rewritten.
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
- **RCx issue headings name the cause, not the remedy (#88 item 2).** An issue page is headed
  "Issue N: {cause}", and the executive summary's Issue column shows the cause. The action
  paragraph reads "Recommended action — {title}: {action}", so the action titles stay in the
  report. `RcxReport.to_dict()` issues gain `title` and `cause`. The RCx golden file changes
  (intended). Workbook `capstone`: the top issue is now headed "Outdoor-air damper not
  modulating (stuck low)", with a damper repair as its action. The answer key and the page
  questions are updated.
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

- **RCx trust table (#87).** A unit with no fan signal is scored per its inferred operating mode in
  the gated column, and *Gate used* says "outliers read per mode: inferred off-mode (OA damper and
  coil valves closed)". On the catalog only `irish-ahu` changes: its supply air reads *trusted*
  0.89 (was *untrusted* 0.19; plain `sensor_trust` now 0.27), its stray 2015 rows are left out
  (return air, OA damper and both valves *suspect* -> *trusted*), and its economizer high-limit
  issue's confidence moves M -> H. Every point flagged `clipped` shows the flag (one LBNL dual-duct
  return air). Fan-gated units are unchanged: reading them per fan mode too was measured and
  rejected (see docs/SENSOR-HEALTH.md). The workbook exercise `data-trend-quality` answers 4, 6
  and 7 are rewritten.
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

### Documentation
- **`docs/DATASETS.md`: "Running the workbook answer checks on local files" (#89).** Covers
  `CAMBER_WORKBOOK_FROM_DIR`, the `<dir>/<dataset-id>/` layout, symlinking a checkout's differently
  named files, the manual `lbnl-b59` files (`Building_59.zip`, `README_Dryad_Bldg59.txt`) and the
  per-dataset file list, which a test keeps in step with the harness and the catalog.
- **ECOSYSTEM: open-fdd's ECM tooling.** A reciprocal note covers open-fdd's ECM workbooks,
  their reference calculators and EnergyPlus-twin comparison, and its change-point and G14
  helpers. It explains how its pre-retrofit estimates and CAMBER's post-retrofit measurement fit
  together, with measured inputs for the calculators (#22), and links the shared M&V vectors.
  The "not yet re-compared" note now applies to the fault conditions only.

### Fixed
- **`docs/VENTILATION.md` overstated the `lbnl-b59` wildfire result (#85).** It said the 2020
  smoke mode was a below-floor `fault` on two rooftop units. Re-measured on the default subset
  (which holds the whole OA-flow record, April–December 2020): all four units are below the
  750 cfm floor in 2.0–4.2 % of occupied hours, `info` under the 10 % fault share, and those hours
  are fan-off days in October and December. In the smoke-mode weeks (2020-08-24 to 09-06) the
  dampers sat at their 10 % minimum and the units still took in 1,130–3,625 cfm, 0–0.9 % below
  the floor. The page, `docs/VALIDATION.md` and the `zone-min-oa` caveat now say so.
- **`tests/test_references.py` sees instance-named rules.** `_rule_names()` now includes
  `builtin.rule_names()`, so a mapping for `cohort_airflow` or the census and starvation rules no
  longer fails the "every mapped rule exists" check.
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
- **The synthetic healthy chilled-water reset ran the wrong way (S2).** `faultlab`'s clean
  `chw_plant_reset` scenario raised CHWST as OAT rose (`42 + 0.30 x (OAT - 55)`); a healthy
  outdoor-air reset lowers it. It is now `clip(52 - 0.30 x (OAT - 55), 42, 52)`. The synthetic
  benchmark's metrics JSON is byte-identical before and after (sha256 `a0d5c8fb...`); without the
  fix the new sign check would have raised `chw_plant_reset.fpr` 0 -> 1. A test fixture in
  `tests/test_optional_role_honesty.py` had the same wrong-way "working reset" and is corrected.
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
- **The walk-down covers `actuator_stuck` and the DCV fan-off cause.** `camber.walkdown` gains a
  `SITE_CHECKS["actuator_stuck"]` entry (a "contradicted" item when any actuator's flat run
  contradicts the zone's demand, else the unexplained-flat item) and a `fan_off_occupied` item for
  `dcv_verification` (#93), so every cause the DCV recommender can lead with has its own check.
  The reheat walk-down cause now reads `DEFAULT_PARAMS["reheat_valve_divergence_share"]`, the
  threshold the recommender uses.
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
