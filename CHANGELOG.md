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
