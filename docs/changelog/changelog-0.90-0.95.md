# Changelog: 0.90 to 0.95

Archived CAMBER releases 0.90.0 to 0.95.0, in the same format as the
[current changelog](https://github.com/yroussev/camber/blob/main/CHANGELOG.md), which holds the recent releases.
The [changelog index](index.md) lists every archive.

## [0.95.0] — 2026-10-03

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

### Added

#### M&V: the SEP methods and non-routine adjustments (issue #21, phases 21b and 21c; #46, #47)
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

#### M&V: versioned baselines and the rebaseline policy (issue #21, phase 21d; #48)
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

#### M&V validation: the BDG2 savings benchmark (issue #21, phase 21e; #49)
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

#### M&V: the change-point + driver form on the config `mv` path (#47, #48)
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

### Changed

#### The maintainer's decisions on #21
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
