# Open datasets

CAMBER ships a reviewed **catalog** of open building datasets that it knows how to download,
verify, normalize and analyse. Pick a dataset, and CAMBER fetches it from its publisher, ingests it
into a Parquet store, and writes a config so `camber run`, `camber report` and `camber drift` work
on it straight away. Labelled datasets can then be **scored**: how many of the known faults did the
rules find, and how many false alarms did they raise?

CAMBER redistributes none of the data. The catalog (`camber/datasets/catalog.json`, package data)
records where each dataset is published, its licence and citation, and the size and SHA-256 of
every file; `camber datasets fetch` downloads the files from the publisher onto your machine.

> **Status:** 0.86 shipped the first seven entries and the command-line workflow; 0.89 adds the
> research-only tier, manual-download entries, Excel workbooks (the `xlsx` extra) and Brick-grouped
> ingest. A local catalog UI (`camber lab`) and worked exercises follow in later releases. The
> Python API (`camber.datasets`) is **provisional** -- see [API-STABILITY.md](API-STABILITY.md).

## Quick start

```
camber datasets list
camber datasets info lbnl-sdahu
camber datasets fetch lbnl-sdahu                       # ~608 MB, verified
camber datasets ingest lbnl-sdahu --store lab_store
camber datasets config lbnl-sdahu --store lab_store --out sdahu.json
camber report sdahu.json --out sdahu.html
camber datasets score lbnl-sdahu --store lab_store
```

The same from Python:

```python
from camber import datasets

datasets.fetch("lbnl-sdahu")
datasets.ingest("lbnl-sdahu", "lab_store")
datasets.config_template("lbnl-sdahu", "lab_store", out="sdahu.json")
print(datasets.score("lbnl-sdahu", "lab_store")["overall"])
```

## The catalog

| id | what | kind | labelled | licence | default subset |
|---|---|---|---|---|---|
| `lbnl-sdahu` | single-duct AHU, 21 runs | simulated | yes | CC-BY-4.0 | 8 runs + a spliced onset run |
| `lbnl-fcu` | fan-coil unit, 49 runs | simulated | yes | CC-BY-4.0 | 4 runs |
| `lbnl-ddahu` | dual-duct AHU, 56 runs | simulated | yes | CC-BY-4.0 | 3 runs |
| `lbnl-fpu` | fan-powered VAV boxes (parallel + series), 62 runs | simulated | yes | CC-BY-4.0 | 5 runs |
| `lbnl-chiller` | chiller plant, 24 runs | simulated | yes | CC-BY-4.0 | 4 runs |
| `lbnl-boiler` | boiler plant, 17 runs, Brick model | simulated | yes | CC-BY-4.0 | 4 runs |
| `bdg2` | 3,053 whole-building meters, 19 sites | real | no | CC-BY-SA-4.0 | 10 sites x up to 4 buildings |
| `lbnl-b59` | real office: 4 rooftop units with measured OA flow, 11 zone CO2 sensors, 51 underfloor terminals, Brick model (manual download) | real | no | CC-BY-4.0 | RTUs, CO2 zones and weather, 3 years |
| `finnish-dcv` | laboratory office room with occupancy-based DCV and ground-truth counts | lab | no | CC-BY-4.0 | all 3 files |
| `b4b-windesheim` | 3 office rooms, two CO2 sensors each, ventilation valve, PIR | real | no | CC-BY-4.0 | all 5 runs |

`camber datasets info <id>` prints the full entry: publisher, citation and DOI, what it teaches,
the subsets and their download sizes, and the entry's **known issues**.

Every entry has a `default` subset (small enough to try) and a `full` one. A subset selects the
files to download and, for labelled datasets, the runs to ingest; for `bdg2` it selects sites,
meters and a cap on buildings per site. Choose one with `--subset full`.

## Licences

Each entry carries an SPDX licence id and a licence **tier** (`access`), shown by
`camber datasets list`:

- **open** -- the licence allows commercial use (CC0, CC-BY, CC-BY-SA, CDLA-Permissive-1.0, US
  federal public-domain data such as NIST's, `NIST-PD`, ...). Fetch it freely; cite the publisher. A **share-alike** licence (BDG2 is CC-BY-SA-4.0) additionally means a
  *redistributed adaptation* of the data must keep the same licence -- analysing it, including
  commercially, is fine. Reports built from share-alike data say so.
- **research-only** (`access: "research_only"`) -- the licence is non-commercial (NC) or
  no-derivatives (ND). You may download and analyse the data for research, but not use it
  commercially or redistribute it (or anything built from it). CAMBER makes that an explicit act:
  - `fetch` refuses it unless you pass `--accept-noncommercial` -- on every fetch; there is no
    environment-variable bypass. The acceptance is appended to `acknowledgements.json` in the
    cache (and to the manifest) before anything downloads.
  - `fetch --all` covers the **open tier only**; research-only entries need `--licence all`
    **and** `--accept-noncommercial`.
  - `ingest` needs an acknowledgement of the entry's current licence (from the fetch, or its own
    `--accept-noncommercial`), and records `redistribution: "prohibited"` on the facility.
  - Every report built from the data -- audit, RCx, drift, site report, dashboard -- carries a
    **non-commercial / do-not-redistribute** banner.

The catalog validator enforces that `access` is `research_only` exactly when the licence is NC or
ND, and that every URL is HTTPS. Files are pinned (size + SHA-256) unless an entry says why not.

## Manual downloads

Some publishers hand files out only through a portal with terms to accept. Such an entry is
`manual: true` with `manual_instructions`; CAMBER never downloads it (`fetch` exits 1 with the
instructions; `fetch --all` skips it). Download the files yourself, then:

```
camber datasets ingest <id> --from-dir ~/Downloads/<publisher files> --store lab_store
```

`--from-dir` finds each catalog file under the directory by its catalog path, then by its bare
name, **verifies every pinned file** (size + SHA-256; a mismatch is refused with exit code 2 and
your file is left untouched), hashes unpinned ones with a warning, and hard-links (or copies) them
into the cache, recorded in the manifest as `source: "local"` -- from then on `status`, `remove`
and re-ingest treat them like fetched files. `--from-dir` works for any entry whose files you
already have. A research-only manual entry also needs `--accept-noncommercial`.

## Excel workbooks (the `xlsx` extra)

A few publishers ship `.xlsx` workbooks. Reading them needs the optional extra:

```
pip install "camber-toolkit[xlsx]"      # openpyxl, imported only when a workbook is read
```

Such an entry lists `"requires_extras": ["xlsx"]` (the validator requires it whenever a run reads a
workbook), and `ingest` stops with that install command when the extra is missing. A run names its
worksheet with `"sheet"` (default: the first). Legacy `.xls` files are **not** covered by the extra:
the only one published beside a planned entry (a heat-pump test report's appendix) is a transposed
steady-state summary table, not time-series data, so the entry does not use it. CAMBER reads an
`.xls` only if you install `xlrd` yourself.

## Brick-grouped ingest

Some datasets publish one wide table per *quantity* (every air handler's supply temperature in
one file) and a Brick model saying which point belongs to which equipment. A run with
`"group": "brick"` is split by the entry's `ingest.brick` model instead of becoming one equipment:

- each column is a Brick point (matched by name); its role comes from the Brick class (mapped
  and alias classes only -- ambiguous points are left out);
- its equipment is the point's **owner** (`brick:hasPoint`, or the inverse `brick:isPointOf`), or
  the first entity up the `hasPart` / `isPartOf` chain whose Brick class `equip_classes` maps to a
  CAMBER class (`{"Rooftop_Unit": "AHU"}`); points with no such owner go to the run's own
  `equip` / `class`;
- **the dataset's mapping file wins**: its `aliases` override a column's role, its
  `"equipment": {column: equip}` the owner and its `"equipment_classes": {equip: class}` the class;
  each override is listed in the ingest notes.

Columns of several unlabelled files merge into one equipment per owner, so every per-quantity
file contributes to the same air handler.

## Source layouts

Measured datasets rarely come as one wide table per scenario. Each layout has one key, validated by
`camber.datasets` (`validate_catalog`) and read by one code path (`camber/datasets/_readers.py`):

- **Several files per run** -- `"members"`: a list of per-quantity tables whose columns are joined
  on the timestamp (`lbnl-b59`'s underfloor terminals), or `{raw column: member}` for one file per
  point (`nuig-ahu101`, `sdu-ou44`): each member's value -- its last non-clock column, or the
  second column of a headerless `timestamp,value` export -- becomes the named raw column. Only the
  members a run needs are extracted, and a table several runs share is parsed once.
- **Rows of a stacked table** -- `"where": {column: value or [values]}` keeps a run's rows (one room
  of `b4b-windesheim`'s table, one test's points of `nist-heatpump-fdd`'s sheet), compared as text,
  before duplicate stamps are dropped.
- **A run's own mapping** -- `"mapping"`, with `"vars"` filling `{placeholders}` in it: one table
  holding a rooftop unit and ten VAV boxes (`ornl-frp-ops`) becomes one run per box, each reading
  `T_Room_{n}` with its own `n`. A run may also carry its own `timestamp_format`, `units`,
  `encoding` and `sheet` (two `ornl-supermarket-fdd` files stamp minutes where the others stamp
  seconds).
- **Plain equipment names** -- `"equip_id"` stores a run's equipment verbatim instead of
  `<equip>__<scenario>` (real, unlabelled data has no scenarios; `RTU01_zone_022` joins `RTU01`
  through the naming topology).
- **Several pieces of equipment in one table, no Brick model** -- `"group": "mapping"`: the
  mapping file's `equipment` / `equipment_classes` split the table (`ornl-frp-vav`'s test sheets
  into the RTU and its ten boxes). A grouped run's `"target"` names the equipment under test: only
  it carries the run's fault label; the rest is recorded as unscored `context`. Name boxes under
  their air handler (`RTU` and `RTU_VAV_104`) and the naming topology places each scenario's boxes
  under that scenario's air handler. A `derive` of the form `{"column": ..., "copy": ...}` hands
  one building-wide schedule column to every box.
- **One file per sensor** -- `"adapter": "per_point"` (`at-30bldg-sensors`): an `index` table gives
  each sensor's group (a building) and class; every group becomes a facility `ds-<id>-<group>`,
  every sensor an equipment with one role from the entry's `class_map`, read from `series` by the
  same one-point reader. Change-of-value logs are resampled **sample-and-hold** (`"hold":
  "8h5min"`: an empty bin holds the last sample while it is at most that old). The subset's
  `groups` pick the buildings.
- **Clocks.** `timestamp_format` pins the parse (a strftime format, or `"ISO8601"` for stamps
  that mix precisions). A source without a wall-clock column declares a `clock`: `{"kind":
  "elapsed", "unit": "s", "origin": "2025-01-01"}` counts from a stated origin (`rbc-g36-ahu`'s
  simulation seconds); `{"kind": "day", "day": ..., "time": ..., "start": ..., "every_days": 7}`
  stores anonymised day `d` on `start + d * every_days` (`sdu-ou44`); `{"kind": "rows", "freq":
  "1min", "start": ...}` numbers a steady-state sheet's rows (`nist-heatpump-fdd`). The `day` and
  `rows` clocks are **synthetic**: the ingest notes and the provenance (`clock`) say so, and rates,
  schedules and drift over them mean nothing.
- **Time zones.** The store holds naive wall-clock time. A stamp carrying a UTC offset keeps its
  wall clock as written (an export that labels local time `+00:00` is a data issue on its entry).
  A dataset published in another clock declares `source_timezone` -- `"UTC"`, an IANA zone, or
  `"offset"` for stamps with true per-row offsets (`nist-ibal`'s `-05:00` / `-04:00`) -- and
  `local_timezone`; the index moves to the site's wall clock after the quirks, so quirk timestamps
  are in the publisher's clock. A DST fall-back's repeated hour keeps both readings (the resample
  averages them).
- **Text encoding** -- `"encoding"` on the spec or a run (`"latin-1"`).
- **Units** add `psia` (converted to psig, CAMBER's refrigerant-pressure unit), `psig` and `m3/min`.
- **Days that are not consecutive** -- `"contiguous": false` (`robod` has no weekends, `sdu-ou44`
  shuffles its days) is recorded in the provenance; such an entry's template runs no drift,
  setback or other schedule rule.

Four intake branches built these layouts in parallel before 0.89 reconciled them; the validator
rejects their earlier spellings and names the key to use:

| earlier spelling | use |
|---|---|
| run `"points": {column: member}` | `"members": {column: member}` |
| run `"where": {"column": c, "in": [...]}` | `"where": {c: [...]}` |
| `"tz_convert": zone` | `"source_timezone": "offset"` + `"local_timezone": zone` |
| `"synthetic_index": {"start", "freq"}` | `"clock": {"kind": "rows", ...}` |
| `"day_clock": {...}` | `"clock": {"kind": "day", ...}` |
| `"timestamp_unit"` + `"timestamp_origin"` | `"clock": {"kind": "elapsed", "unit", "origin"}` |
| `per_point` `"points"` (the sensor index) | `"index"` |

## How a dataset lands in the store

- **Facility.** Each dataset becomes one facility, `ds-<id>` (`bdg2`: one per site,
  `ds-bdg2-<site>`).
- **Scenarios as equipment.** Each labelled run becomes an equipment `<equip>__<scenario>` of the
  same class -- `AHU__fault_free`, `AHU__damper_stuck_025`, ... -- so one `camber run` scores every
  scenario, side by side.
- **Onset runs.** A `splice` joins a fault-free run and a faulted run at an onset date
  (`AHU__onset_damper_stuck_025`: healthy until 2018-07-01, stuck damper after) for drift and
  fault-onset exercises.
- **Normalization.** Only the mapped columns are read; values are resampled to 15 minutes (status
  points become duty), converted to IP units where the source is metric (°C, L/s, Pa, W, ...), and
  valve/damper fractions are rescaled to percent. Implausible medians after conversion are reported
  as warnings.
- **Data issues and quirks.** Every problem CAMBER knows of in the *published* data is described
  on the entry -- the columns, the evidence, the publisher documentation it contradicts and how
  CAMBER handles it (see [Data issues](#data-issues-and-how-camber-handles-them) below). A **fix**
  is applied before mapping by a declared quirk (the chiller plant's swapped outdoor wet/dry-bulb
  and secondary-loop supply/return columns; the single-duct AHU's placeholder static-pressure
  values); `camber datasets ingest --no-corrections` skips every fix and ingests the data exactly
  as published, so the two can be compared (use a second store). An **annotated** problem is left
  in place; an **excluded** run is ingested for inspection but never scored. CAMBER's *own*
  mistakes (a mapping, an assumed design parameter, a template rule) are simply fixed.
- **Provenance.** The facility's registry entry records, under its `"dataset"` key, the licence,
  citation, DOIs, file checksums, the ingest's content hash, the fault label of every scenario,
  the runs excluded from scoring, the quirks applied, the data issues and whether corrections were
  applied. Reports read it to print a **Data source & licence** block.
- **Idempotent.** Re-running `ingest` with the same inputs is skipped; `--force`, a different
  subset or a different corrections mode (the content hash covers it) replaces the dataset's
  facilities atomically (staged, then swapped in).
- **Disk.** `camber datasets info <id>` shows each subset's download size and its estimated size
  once ingested; `ingest` warns when the store's filesystem has less free space than the estimate
  for the subset being ingested (a `full` subset is many times its `default`).

## Scoring

`camber datasets score <id> --store DIR` runs the entry's config template (or reads
`--findings findings.json` from `camber run --out`), maps each scenario's findings to "which
detectors fired", and scores them against the labels with the LBNL FDD evaluation framework:
overall detection (TPR / FPR / accuracy), each target detector against its own fault type, and the
correct-diagnosis rate -- every rate with a Wilson 95% interval. The scored detectors are the
entry's declared targets (e.g. `outdoor_air_fraction` -> `damper` for `lbnl-sdahu`), so context
rules in the template do not move the score.

## Data issues and how CAMBER handles them

<!-- BEGIN data-issues: generated from camber/datasets/catalog.json by scripts/datasets_issues_doc.py; do not edit by hand -->

The catalog links each dataset exactly as its publisher provides it. Every problem
CAMBER knows of in the *published* data is described below with its evidence, the
publisher documentation it contradicts, and how CAMBER handles it: **fix** (corrected
at ingest by a declared quirk; `camber datasets ingest --no-corrections` ingests the
published data as-is), **annotate** (left in place and recorded in the facility's
provenance), **exclude** (kept out of scoring or of an analysis) or **none** (described
only). Nothing is corrected silently. `camber datasets info <id>` prints the same list.

### `lbnl-sdahu`: LBNL simulated single-duct AHU (labelled faults)

#### SA_SPSPT is a -400.25 placeholder in every faulted run

- **Issue:** `sa-spspt-placeholder`
- **Columns:** `SA_SPSPT`
- **Evidence:** All 20 faulted runs export SA_SPSPT as the constant -400.25; the fault-free run exports the setpoint 1.60746 inH2O.
- **Contradicts:** SDAHU inventory Table 2 (SA_SPSPT: supply air duct static pressure setpoint, inches H2O) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Masked to missing (mask quirk: values below -100), so static-pressure rules decline instead of reading a negative setpoint.

#### Fault-free SA_SP is the static-pressure setpoint in Pa, not a measurement

- **Issue:** `sa-sp-fault-free-in-pa`
- **Columns:** `SA_SP`
- **Runs:** `fault_free`
- **Evidence:** Fault-free SA_SP holds 401.9 (median) with the fan off and 403.5 with it on (range 401.8-410.6): the ~1.61 inH2O setpoint expressed in Pa, which never falls toward zero with the fan stopped. In the 20 faulted runs SA_SP is the measured static in inH2O (fan-on median 1.51-1.61, fan-off median 0.003-0.005).
- **Contradicts:** SDAHU inventory Table 2 (SA_SP: supply air duct static pressure, inches H2O) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Masked to missing in the fault-free run (mask quirk: values above 100); the faulted runs' measured static is kept.

#### SA_CFM and RA_CFM are cfm x 60

- **Issue:** `sa-ra-cfm-units`
- **Columns:** `SA_CFM`, `RA_CFM`
- **Evidence:** SA_CFM peaks at 1,266,232 (RA_CFM 1,264,430) with a fan-on median of 596,187 in the fault-free run -- read as cfm, tens of times any single-floor air handler. Divided by 60 (ft3/h to cfm) they peak at 21,104 cfm (9.96 m3/s) with a 9,936 cfm fan-on median, a plausible floor-level supply. Caveat: at those flows the exported supply-fan power (SF_WAT, at most 1,622 W) is only ~0.08 W/cfm, itself implausibly low, so treat the flow scale with care.
- **Contradicts:** SDAHU inventory Table 2 (SA_CFM / RA_CFM: supply / return volumetric airflow, CFM) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Divided by 60 before mapping (scale quirk) in every run; `--no-corrections` keeps the published magnitudes.

#### OA_CFM is a constant

- **Issue:** `oa-cfm-constant`
- **Columns:** `OA_CFM`
- **Evidence:** OA_CFM is 357,730.44 in every row of all 21 runs, fan on or off, damper closed or fully open.
- **Contradicts:** SDAHU inventory Table 2 (OA_CFM: outdoor volumetric airflow, CFM) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: exclude** -- kept out of scoring / analysis. Not mapped: there is no measured outdoor airflow, so OA fraction is taken from the mixing-box temperature balance.

#### The simulated calendar runs one weekday late and follows DST

- **Issue:** `calendar-shifted-one-weekday`
- **Columns:** `SYS_CTL`, `Datetime`
- **Evidence:** SYS_CTL (the occupied-mode flag) is 0 on every Monday of 2018 and occupied Tuesday to Sunday: the simulation treats 2018-01-01 (a Monday) as a Sunday, so its Mon-Fri 06-22 schedule falls on Tue-Sat and its Saturday 06-18 on Sunday. The timestamps are standard time but the schedule observes daylight saving: the first occupied minute moves from 06:01 to 05:01 from 2018-03-13 to 2018-11-04 (the first and last occupied days after and before the changes, which fall on unoccupied days). With the fan on and SYS_CTL = 1 the OA damper is never below its 10% minimum (0 of 277,392 rows); every fan-on row with the damper shut has SYS_CTL = 0 (29,364 rows), the unoccupied-mode cycling the inventory describes.
- **Contradicts:** SDAHU inventory section 1.2 (occupied Monday-Friday 6:00am-10:00pm, Saturday 6:00am-6:00pm) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published. SYS_CTL is mapped to the occupancy role, so occupancy-aware rules use the simulation's own schedule instead of CAMBER's assumed Mon-Fri 07-18 office hours; the OA-fraction rule also judges fan-on samples only.

#### Every run starts at 01:00 on 1 January

- **Issue:** `first-hour-missing`
- **Columns:** `Datetime`
- **Evidence:** All 21 runs start at 2018-01-01 01:00 and have 525,540 one-minute rows (a full year is 525,600); damper_stuck_100 runs 2018-04-01 01:00 to 2018-11-01 00:00 (308,101 rows).
- **Contradicts:** SDAHU inventory section 3 and Table 4 (each file is one year of 1-minute data) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published; the first hour of the year is simply absent (the chiller-plant inventory documents the same trimming of simulation start-up).

#### The four OA-temperature bias runs carry no bias

- **Issue:** `oa-bias-runs-carry-no-bias`
- **Columns:** `OA_TEMP`
- **Evidence:** oa_bias_-4/-2/2/4 are byte-identical (same size, 143,313,007 bytes, and CRC-32) and their OA_TEMP is within 0.33 F of the fault-free run in every row (median difference 0.015 F), where a +-2 / +-4 C bias would shift it by 3.6 / 7.2 F.
- **Contradicts:** SDAHU inventory Tables 3-4 (outdoor air temperature sensor bias of -4, -2, +2 and +4 C) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: exclude** -- kept out of scoring / analysis. Ingested once (the other three are byte-identical copies) and excluded from scoring: they are fault-free replicates, not sensor faults.

#### The four valve-leak 'severities' are one 10% leak

- **Issue:** `leak-severities-are-one-10pct-run`
- **Columns:** `CHWC_VLV`, `CHWC_VLV_DM`
- **Evidence:** coi_leakage_010/025/040/050 are byte-identical (139,023,633 bytes, same CRC-32). In it the valve position CHWC_VLV is 0.10 (minimum and median) in all 332,140 rows where the demand CHWC_VLV_DM is 0 -- a 10% leak; in the fault-free run the valve reads 0 in 99.6% of its 335,004 zero-demand rows.
- **Contradicts:** SDAHU inventory Tables 3-4 (cooling coil valve leaking at 10%, 25%, 40% and 50%) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: exclude** -- kept out of scoring / analysis. coi_leakage_010 is the one leak run (its label matches the data); the 025/040/050 copies are excluded from ingest and scoring. There is no leak severity sweep in the published data.

#### The coi_bias runs are the inventory's supply-air-temperature bias runs

- **Issue:** `coi-bias-is-supply-air-bias`
- **Columns:** `SA_TEMP`
- **Evidence:** The archive has coi_bias_-4/-2/2/4 and no sa_bias_* files. OA_TEMP matches the fault-free run exactly (median difference 0.0); the logged SA_TEMP stays at the 55.2 F setpoint in mechanical cooling while the cooling-valve demand moves with the bias sign (medians 0.32 / 0.46 / 0.61 fault-free / 0.77 / 1.00 for -4 / -2 / +2 / +4 C): a supply-air sensor offset the controller holds at setpoint.
- **Contradicts:** SDAHU inventory Table 4 (sa_bias_-2/-4/2/4_annual.csv: supply air temperature sensor bias) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Ingested under the published file names; labelled sensor_bias, described as a supply-air-temperature offset.

### `lbnl-fcu`: LBNL simulated fan-coil unit (labelled faults)

#### The damper 'stuck at 30%' run is the unit's normal minimum

- **Issue:** `stuck-at-30-is-normal-operation`
- **Columns:** `FCU_DMPR`, `FCU_MAT`
- **Evidence:** The unit's minimum OA damper position is 30%, which is also the fault-free position in 174,453 occupied, fan-on minutes. In OADMPRStuck_30 the occupied fan-on OA fraction is 10.53% (temperature balance) / 10.54% (OA_CFM / supply flow), identical to the fault-free run's 10.53% / 10.54%; the two differ only in unoccupied setback minutes with the fan cycling (10.5% vs 0.2%).
- **Contradicts:** FCU inventory Table 3 (OA damper stuck at 30%, a fault case) and section 1.2 (minimum damper position 30%) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: exclude** -- kept out of scoring / analysis. Ingested for inspection and excluded from scoring: in the occupied hours every rule judges it is fault-free operation.

#### The cooling and heating airside-minor-fouling runs are one file

- **Issue:** `airside-minor-fouling-runs-identical`
- **Columns:** `FCU_CVLV_DM`, `FCU_HVLV_DM`
- **Evidence:** FCU_Fouling_Cooling_Airside_Minor.csv and FCU_Fouling_Heating_Airside_Minor.csv are byte-identical (83,997,327 bytes, same CRC-32); the moderate and severe pairs differ. The data cannot say which label is right.
- **Contradicts:** FCU inventory Table 4 (distinct cooling- and heating-coil airside fouling cases) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: exclude** -- kept out of scoring / analysis. Ingested once under the cooling label (first in the archive); the heating copy is excluded from ingest and scoring.

### `lbnl-ddahu`: LBNL simulated dual-duct AHU (labelled faults)

#### Table 4 mislabels two stuck-OA-damper severities

- **Issue:** `stuck-oa-severity-labels`
- **Columns:** `OA_DMPR`
- **Evidence:** Table 4 lists DualDuct_DMPRStuck_OA_28 as 'Stuck at 20%' and DMPRStuck_OA_45 as 'Stuck at 50%'; Table 3, the file names and the data (fan-on median OA_DMPR 0.28 and 0.45) say 28% and 45% -- the unit's two design minimum positions.
- **Contradicts:** DDAHU inventory Table 4 (file inventory) vs Table 3 (OA damper stuck at 0, 28, 45, 80 and 100%) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Run ids follow the file names (28 / 45), which match the data.

#### The stuck-OA runs' measured OA does not follow the stuck position

- **Issue:** `stuck-oa-runs-do-not-follow-the-position`
- **Columns:** `OA_CFM`, `CSA_CFM`, `HSA_CFM`, `OA_DMPR`
- **Evidence:** Measured OA fraction OA_CFM / (CSA_CFM + HSA_CFM), fan-on medians: DMPRStuck_OA_100 gives 21.3% in Jun-Aug and 42% over the year, where the fault-free unit reaches 95% with its damper fully open (17,723 minutes); DMPRStuck_OA_0 gives 17.9% in Jan, Feb, Apr and Oct-Dec (0.3-1.3% in the other months).
- **Contradicts:** DDAHU inventory Table 3 (OA damper stuck fully open / fully closed: a fixed simulated device position) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published and scored under the published labels; a detector that misses the 'stuck open' run in summer is seeing a unit that brings in 21% OA.

#### The static-pressure bias runs are labelled 10x too large

- **Issue:** `static-bias-labels-10x`
- **Columns:** `CSA_SP`, `HSA_SP`, `CSF_DP`, `HSF_DP`
- **Evidence:** Run ids say +-2 / +-4 in.wg. The logged deck static stays at the 1.6 setpoint (the controller holds the biased reading) and the fan differential pressure moves by the bias: fault-free cold-deck fan DP 2.18 in.wg; CSP +2 / +4 give 1.98 / 1.78 (-0.2 / -0.4), CSP -2 gives 2.38 (+0.2) and CSP -4 also 2.38 (+0.2, not +0.4); HSP -4 / -2 / +2 / +4 move the hot-deck fan DP by +0.40 / +0.20 / -0.20 / -0.41.
- **Contradicts:** DDAHU inventory Table 4 (sensor bias +-2 / +-4 in.wg) vs Table 3 (-0.4, -0.2, +0.2, +0.4 in.wg) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Run ids keep the published names; read them as +-0.2 / +-0.4 in.wg, and SensorBias_CSP_m4inwg as a -0.2 in.wg run.

#### The 60 F economizer supply-air setpoint is not in the data

- **Issue:** `economizer-sat-reset-absent`
- **Columns:** `CSA_TEMPSPT`, `CSA_TEMP`
- **Evidence:** CSA_TEMPSPT is 55.0 in every row, and in the 89,670 occupied, fan-on economizer minutes (OAT below 60 F, cooling valve shut, outside Jun-Aug) the cold-deck supply temperature itself has a median of 55.0 F.
- **Contradicts:** DDAHU inventory section 1.2(iii) (economizer cooling mode holds 60 F at the cold deck in the transition season and winter) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published: rules comparing the cold deck with its setpoint see a fixed 55 F.

### `lbnl-fpu`: LBNL simulated fan-powered VAV terminal units (labelled faults)

#### Faults are imposed on the South-zone box, not the West one

- **Issue:** `faults-on-the-south-box`
- **Columns:** `VAV_DMPR_S`, `RH_VLV_S`
- **Evidence:** Diffing each faulted PFPU run against PFPU_FaultFree, the changed columns are the _S ones: averaged over each box's ten points, the median absolute change in VAVDMPRStuck_50pct is 46.7 for the _S box and 0.06-0.07 for the other three. VAV_DMPR_S is a constant 0.50 in VAVDMPRStuck_50pct (each stuck-damper run holds its own position) and RH_VLV_S a constant 0 in ReheatVLVStuck_0pct, while the _W box varies as in the fault-free run.
- **Contradicts:** FPU inventory section 3 (faults imposed on the west-zone box, variables suffixed _W) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. The mapping reads the _S box (until 0.82.0 it read the healthy _W box).

#### The airflow-bias runs log the true flow; the positive offsets appear on the setpoint

- **Issue:** `airflow-bias-on-the-setpoint-column`
- **Columns:** `VAV_PM_CFM_S`, `VAV_PM_CFM_SP_S`
- **Evidence:** Occupied-hour medians: fault-free setpoint and flow 201.3 / 201.3 cfm; SensorBias_VAVAirflow_+400CFM 537.7 / 137.7 and +200CFM 336.2 / 136.0 (the offset sits on the setpoint column); -200CFM and -400CFM keep the 201.3 setpoint while the flow reads 401.3 and 601.2 (the unbiased flow the controller drives up). The logged flow is the true flow in every direction.
- **Contradicts:** FPU inventory section 2 (for sensor faults the logged value of the faulty sensor is the faulty value) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published; the runs are labelled airflow_sensor_bias and scored as airflow_tracking targets (measured flow no longer tracks its setpoint either way).

### `lbnl-chiller`: LBNL simulated chiller plant (labelled faults)

#### Outdoor dry-bulb and wet-bulb columns are swapped

- **Issue:** `oa-dry-wet-bulb-swapped`
- **Columns:** `OA_TEMP`, `OA_TEMP_WB`
- **Evidence:** Two independent checks. The tower leaving-water setpoint follows max(wet-bulb + 8 F, 60 F): on the 210,661 minutes above the 60 F floor CT_SW_TEMPSPT equals OA_TEMP + 8 to a median 0.28 F but OA_TEMP_WB + 8 only to 6.27 F. The chilled-water reset follows the outdoor dry-bulb: CWL_PRI_SW_TEMPSPT matches the reset of OA_TEMP_WB with a p90 error of 0.49 F (hourly), and the reset of OA_TEMP only to 4.54 F. OA_TEMP_WB also exceeds OA_TEMP in 99.3% of rows, which a wet-bulb cannot do.
- **Contradicts:** Chiller-plant inventory Table 2 (OA_TEMP: dry bulb; OA_TEMP_WB: wet bulb), Eq. 3 / Table 1 (tower setpoint = wet-bulb + 8 F) and Eq. 2 (the chilled-water reset follows the dry-bulb) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Swapped back before mapping (swap quirk), in every run.

#### Secondary-loop supply and return temperatures are swapped

- **Issue:** `secondary-supply-return-swapped`
- **Columns:** `CWL_SEC_SW_TEMP`, `CWL_SEC_RW_TEMP`
- **Evidence:** In the 169,809 rows where the secondary loop carries load (CWL_SEC_LOAD > 10) the labelled return is colder than the labelled supply in 100% of rows, while the primary loop's return is warmer in 99.9% of them; hourly (supply - return) correlates +0.88 with CWL_SEC_LOAD.
- **Contradicts:** Chiller-plant inventory Table 2 (CWL_SEC_SW_TEMP: supply, CWL_SEC_RW_TEMP: return water temperature) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Swapped back before mapping (swap quirk), in every run; the primary loop is left as published.

#### The chilled-water reset spans 44-54 F, not 42-52 F

- **Issue:** `chw-reset-range-44-54`
- **Columns:** `CWL_PRI_SW_TEMPSPT`
- **Evidence:** CWL_PRI_SW_TEMPSPT ranges 44.0-54.0 F (54.0 in 327,603 rows, 44.0 in 36,899). Eq. 2 with 44 / 54 F bounds reproduces it from the true dry-bulb to a median 0.00 F (p90 0.49 F, hourly); with the inventory's 42 / 52 F bounds the median error is 2.00 F.
- **Contradicts:** Chiller-plant inventory Eq. 2 (chilled-water setpoint reset between 42 F and 52 F over 60-80 F outdoor dry-bulb) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published; the setpoint is not mapped to a role, and nothing in CAMBER assumes the 42-52 F range.

#### CHL_STA_1 and CT_STA_1 are enables, not run status

- **Issue:** `status-points-are-enables`
- **Columns:** `CHL_STA_1`, `CT_STA_1`
- **Evidence:** CHL_STA_1 and CT_STA_1 are 1 in 100% of rows of the fault-free run while chiller 1 draws under 1 kW in 5.5% of rows and tower 1's fan is stopped in 71%; units 2 and 3 read 1 in 12.4% and 0.4% of rows.
- **Contradicts:** Chiller-plant inventory Table 2 (CHL_STA / CT_STA: on-off status of a chiller / cooling tower) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Not mapped: rules that need a chiller or tower run status decline instead of reading the enable as running.

#### The chiller-fouling runs are not in the inventory

- **Issue:** `chiller-fouling-runs-undocumented`
- **Columns:** `CHL_POW_1`
- **Evidence:** The archive has ChillerPlant_chiller_fouling_065.csv and _095.csv, which Tables 3-4 do not list. Against the fault-free run chiller 1's annual energy rises 61% (065) and 6.3% (095); chiller 2's falls 3.5% / rises 0.2%.
- **Contradicts:** Chiller-plant inventory Tables 3-4 (21 faulted cases; no chiller fouling) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Ingested and scored as chiller_fouling, marked undocumented; the severity is read from the file name by analogy with the tower-fouling runs (heat-transfer coefficient x 0.65 / 0.95), which the inventory does not confirm.

### `lbnl-boiler`: LBNL simulated boiler plant (labelled faults, Brick model)

#### BOI_STA is the boiler enable, not burner firing

- **Issue:** `boiler-status-is-enable`
- **Columns:** `BOI_STA_1`, `BOI_STA_2`
- **Evidence:** In the fault-free run BOI_STA_1 is 1 in 100% of rows while boiler 1 burns no gas (BOI_GAS_CSUM_1 = 0) in 50.5% of them.
- **Contradicts:** Boiler-plant inventory Table 2 (BOI_STA: on-off status of a boiler, 0-Off; 1-On) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left unmapped: mapping it to boiler_status would read every enabled-but-idle minute as firing, so rules needing boiler_status decline on this plant.

#### HWL_DPSPT is the loop DP setpoint in inH2O, not a temperature setpoint

- **Issue:** `dp-setpoint-described-as-temperature`
- **Columns:** `HWL_DPSPT`
- **Evidence:** HWL_DPSPT is the constant 480.52 in every row: 17.36 psi expressed in inH2O, matching Table 1's 17.5 psi loop differential-pressure setpoint and the fault-free HWL_DP median (480.52).
- **Contradicts:** Boiler-plant inventory Table 2 (HWL_DPSPT: hot water loop supply water temperature setpoint, F) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Mapped as hw_diff_press_sp with its unit declared as inH2O (and HWL_DP as hw_diff_press, inH2O, as Table 2 says).

#### Pump 2's power has negative spikes in the fault-free run

- **Issue:** `negative-pump-power-spikes`
- **Columns:** `PM_POW_2`
- **Evidence:** Fault-free PM_POW_2 reaches -102,400.7 kW (2018-12-15 11:19, pump 2 off); 40 rows are below -1 kW and 13 below -1,000 kW. (The literal 'NAN' pump-power entries in eight runs are documented by the inventory's Table 4 footnote.)
- **Contradicts:** Boiler-plant inventory Table 2 (PM_POW: power consumption of pump, kW) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published; pump power is not mapped to a role in this entry.

#### hot_water_temp_bias biases the loop return, not the supply

- **Issue:** `loop-temp-bias-is-on-the-return`
- **Columns:** `HWL_RW_TEMP`, `HWL_SW_TEMP`
- **Evidence:** In the four hot_water_temp_bias runs HWL_RW_TEMP moves by -7.2 / -3.6 / +3.6 / +7.2 F (median difference from the fault-free run for -4 / -2 / +2 / +4 C) and HWL_SW_TEMP by 0.0.
- **Contradicts:** Boiler-plant inventory Tables 3-4 (bias of the hot water leaving temperature sensor of the hot water loop) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Scored under the published label; read these runs as a loop return-temperature sensor bias.

#### The boiler and loop-DP bias faults are hidden in their own sensors

- **Issue:** `where-the-bias-faults-show`
- **Columns:** `BOI_SW_TEMP_1`, `HWL_SW_TEMP`, `HWL_DP`, `PM_SPD_1`
- **Evidence:** The controlled sensors keep reading their setpoints: in the boiler_bias runs BOI_SW_TEMP_1 stays at 176 F while the loop supply HWL_SW_TEMP moves by +7.2 / +3.6 / -3.6 / -7.2 F (-4 / -2 / +2 / +4 C); in the hot_water_pressure_bias runs HWL_DP stays at its setpoint (median difference 0.0) and pump 1's speed moves by +0.034 / +0.016 / -0.013 / -0.025 (-20 / -10 / +10 / +20%).
- **Contradicts:** Boiler-plant inventory section 3 (for sensor bias faults the logged value of the faulty sensor is the faulty value) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published: a rule looking for these faults must read the other column (HWL_SW_TEMP, PM_SPD_1).

### `bdg2`: Building Data Genome 2 (whole-building meters)

#### One site's chilled water is about 1,000x too large

- **Issue:** `eagle-chilled-water-1000x`
- **Columns:** `Eagle_* (chilledwater)`
- **Evidence:** The 87 Eagle chilled-water meters have a median 2016 intensity of 17,417 kWh/ft2/yr (cleaned; 17,913 raw) and a median 1,389x the same building's electricity; the other sites' chilled-water meters have a per-building median of 45.8 kWh/ft2/yr and no site median above 77.5. Divided by 1,000 Eagle's median is 17.4 kWh/ft2/yr, 1.4x its electricity -- the factor a kBTU series converted as mmBTU (the unit Table 4 lists for Eagle's chilled water) would carry.
- **Contradicts:** Miller et al. 2020, Table 4 (Eagle chilled water in mmBTU) and Usage Notes (unit-conversion mistakes fixed in the raw and cleaned sets) (Miller et al. 2020, Sci Data 7:368, doi:10.1038/s41597-020-00712-x)
- **Handling: exclude** -- kept out of scoring / analysis. Ingested as published (no rescale: the factor is inferred, not documented). Kept out of the benchmark's EUI rollup, which is electricity-only; the change-point acceptance and residual-autocorrelation metrics are scale-free.

#### One site's meter timestamps lag its weather by about 4-5 hours

- **Issue:** `eagle-meters-lag-weather`
- **Columns:** `timestamp (Eagle_* meters)`
- **Evidence:** Eagle's weekday electricity profile runs ~5 h later than the pooled profile of the other sites (overnight minimum at 07-08 h vs 03 h; daytime plateau 14-19 h vs 10-15 h) while its air temperature peaks at 15 h like every site's, and its chilled water peaks at 20 h (12-17 h elsewhere). Cross-correlated with its own air temperature, Eagle's meters align best when the weather is shifted 5 h (chilled water) / 3 h (electricity) later, against -4 to +1 h at the other sites (where the correlation is meaningful) -- a UTC-like clock on a US/Eastern site.
- **Contradicts:** Miller et al. 2020, Usage Notes (the BDG2 timestamps, weather included, are in the local time zone) (Miller et al. 2020, Sci Data 7:368, doi:10.1038/s41597-020-00712-x)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published: the offset is inferred (4 or 5 h), not documented, so it is not shifted. Daily models are barely affected; an hourly or time-of-week analysis of Eagle should shift its meters.

### `lbnl-b59`: LBNL Building 59: three years of a real office's rooftop units, zone CO2 and underfloor terminals

#### Timestamps are UTC, which no document states

- **Issue:** `timestamps-in-utc`
- **Columns:** `date (every CSV)`
- **Evidence:** Solar radiation at the campus station peaks at 20:00-21:00 (792-817 W/m2, 15-25 June 2019) and is zero from 04:00 to 12:00; the camera occupancy counts peak at 17:00-22:00 on weekdays (25 people at 17:00 vs 0.1 at 11:00); and the RTU files run straight through the US spring-forward hour (71 rows from 01:55 to 03:05 on 2018-03-11, 2019-03-10 and 2020-03-08) with no repeated hour in November. Solar noon in Berkeley is about 20:10 UTC in June, so the stamps are UTC (Pacific time minus 7-8 h).
- **Contradicts:** README_Dryad_Bldg59.txt and the data description table give no time zone; the data descriptor presents the building's schedules and the Table 2 event dates as local time (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: none** -- described only. Not a correction of values: the ingest declares source_timezone UTC and local_timezone America/Los_Angeles (CAMBER's timestamp semantics, applied in every mode), so the store holds Berkeley wall-clock time and the occupied-hour schedules line up. The November fall-back hour keeps both readings (averaged); quirk timestamps below are UTC.

#### RTU outdoor-air flow before 2020-04-10 is imputed, not measured

- **Issue:** `oa-flow-gap-filled`
- **Columns:** `rtu_001_oa_flow_tn`, `rtu_002_oa_flow_tn`, `rtu_003_oa_flow_tn`, `rtu_004_oa_flow_tn`
- **Evidence:** Up to 2020-04-10 21:59 UTC the hourly OA flows of the four RTUs correlate 0.998-0.999 pairwise (one pattern scaled four ways, 1,005,495 minute rows per RTU); from 22:00 UTC on they correlate 0.72-0.83, the daily minimum drops from above 2,590 cfm to 1,305-1,730 cfm, and the readings reach the transmitter ceiling of 19,999 cfm, which the earlier values never exceed (maxima 13,093-16,148 cfm).
- **Contradicts:** Data description table (rtu_oa_fr.csv: available Apr-Dec 2020) versus the file, which has values for all of 2018-2020; README methods (gaps filled by linear interpolation, KNN and matrix factorization) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Masked (NaN) before 2020-04-10 22:00 UTC in every RTU run, so no OA-flow rule or check judges the imputed years; the OA damper position is kept for the whole period.

#### RTU04's return-air temperature is a copy of its supply-air temperature from September 2019

- **Issue:** `rtu4-return-copies-supply`
- **Columns:** `rtu_004_ra_temp`, `rtu_004_sa_temp`
- **Evidence:** rtu_004_ra_temp equals rtu_004_sa_temp to the last digit in 0.04% of 2018 minutes, 29.8% of 2019 and 99.1% of 2020; from 2019-09-19 on 98.5% of days are at least 90% identical. The other three RTUs' return and supply agree in 0.13-0.22% of minutes (median difference 4.2-5.2 F).
- **Contradicts:** Brick model (rtu_004_ra_temp is a Return_Air_Temperature_Sensor of RTU04) and the data description table (Roof Top Unit return air temperature) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place on purpose: it is a genuine data fault that the sensor-health layer's copied-signal check (camber.sensorhealth.copied_signal_consistency) should find. Economizer and mixing-balance results on RTU04 after 2019-09-19 judge a copy.

#### Mixed-air temperatures leave the outdoor/return band, mostly in 2018-2019

- **Issue:** `mixed-air-outside-oa-ra-band`
- **Columns:** `rtu_001_ma_temp`, `rtu_002_ma_temp`, `rtu_003_ma_temp`, `rtu_004_ma_temp`
- **Evidence:** Hourly mixed-air temperature lies more than 2 F outside [min(OAT, RAT), max(OAT, RAT)] -- which a blend of the two cannot do -- in 10.7% (Jul-Dec 2018), 23.0% (Jul-Dec 2019) and 0.6% (Jul-Dec 2020) of RTU01 hours and 4.2%, 9.2% and 13.5% of RTU04 hours (RTU04's return is a copy of supply from 2019-09, see above); RTU02 and RTU03 stay at or below 3.6%.
- **Contradicts:** Data description table (rtu_*_ma_temp: Roof Top Unit mixed air temperature; the only outlier criterion is < 32 F or > 122 F) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place: an inaccurate mixed-air sensor is a real sensor-health test (camber.sensorhealth.mixing_consistency; issue #16 would weigh the flow balance into the trust score). Rules that read MAT on RTU01/RTU04 in 2018-2019 are judging a doubtful sensor.

#### Measured OA flow sits at a 19,999 cfm ceiling and above the unit's own supply flow

- **Issue:** `oa-flow-exceeds-supply`
- **Columns:** `rtu_001_oa_flow_tn`, `rtu_002_oa_flow_tn`, `rtu_003_oa_flow_tn`, `rtu_004_oa_flow_tn`, `rtu_001_fltrd_sa_flow_tn`, `rtu_002_fltrd_sa_flow_tn`, `rtu_003_fltrd_sa_flow_tn`, `rtu_004_fltrd_sa_flow_tn`
- **Evidence:** After 2020-04-10 the OA flow reads exactly 19,999 cfm in 5.55% of RTU04 minutes and 1.24% of RTU01 minutes, and exceeds the same unit's filtered supply airflow in 7.3% (RTU04), 4.1% (RTU01), 1.5% (RTU03) and 1.3% (RTU02) of hours. The descriptor gives each RTU a design supply airflow of 20,000 cfm and a minimum OA of 5,000 cfm.
- **Contradicts:** Data descriptor, HVAC system description (design airflow 20,000 cfm per RTU, minimum outdoor air 5,000 cfm) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place: the ceiling is the transmitter span, and OA above supply is a flow-station error the DCV rule's floor check is not affected by (it looks at low flows). Treat OA flow above the supply flow as unmeasured.

#### One zone CO2 sensor reads far below outdoor air, and every sensor carries a 72 ppm floor

- **Issue:** `zone-co2-below-outdoor`
- **Columns:** `zone_022_co2`, `zone_028_co2`, `zone_033_co2`, `zone_040_co2`, `zone_044_co2`, `zone_045_co2`, `zone_052_co2`, `zone_058_co2`, `zone_062_co2`, `zone_068_co2`, `zone_072_co2`
- **Evidence:** zone_022_co2 is below 350 ppm in 27,120 minutes (8,420 of them in June 2020, when its monthly median is 362 ppm against 409-420 ppm for the other ten zones) and reads exactly 72 ppm in 11,661 minutes; the other ten sensors touch that same 72 ppm value 1-5 times each. Outdoor CO2 was about 410 ppm.
- **Contradicts:** Data description table (zone_*_co2: CO2 concentration; the only outlier criterion is < 0) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place: dcv_system_verification drops CO2 outside 250-5,000 ppm and excludes a zone whose CO2 stays implausible, and sensor health's zone-vs-outdoor CO2 check flags it.

#### Fan-speed feedback reads -25% while the fans are stopped

- **Issue:** `fan-speed-negative-when-off`
- **Columns:** `rtu_001_sf_vfd_spd_fbk_tn`, `rtu_002_sf_vfd_spd_fbk_tn`, `rtu_003_sf_vfd_spd_fbk_tn`, `rtu_004_sf_vfd_spd_fbk_tn`
- **Evidence:** Each supply- and return-fan speed feedback reads -24.9% or -25.0% in 0.9% of minutes (12,419 minutes for RTU01's supply fan, in 13 episodes, e.g. 2018-01-29 and 2019-02-26); in those minutes the unit's filtered supply airflow has a median of 0 cfm.
- **Contradicts:** Data description table (rtu_*_sf_vfd_spd_fbk_tn: supply fan speed in %) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place: it reads as fan off (supply flow 0). Sensor health's range check counts the negative values; no template rule gates on the fan speed.

#### The Brick model and the descriptor's Table 1 assign the zones to different RTUs

- **Issue:** `brick-and-table-1-disagree-on-rtu-zones`
- **Columns:** `zone_*_co2`, `zone_*_temp`
- **Evidence:** For all 11 CO2 zones the serving RTU differs between the Brick model (zone hasLocation RTU0N_Zone) and Table 1 (e.g. zone 22: RTU01 vs RTU4; zone 40: RTU04 vs RTU1; zone 52: RTU02 vs RTU4); over all 51 terminal zones Brick gives 11/10/18/12 zones to RTU01-04 and Table 1 gives 14/23/8/12. Table 4 of the same paper labels RTU 3-4 as North (consistent with Brick) while Table 1 puts RTU 1-2 in the North wing. A physical check is inconclusive: hourly zone-temperature changes correlate best with the Table 1 RTU's supply-temperature changes in 6 of 11 zones and with the Brick RTU's in 5 (all correlations 0.2 or less; the underfloor plenum mixes the units' air).
- **Contradicts:** Data descriptor Table 1 (RTU service zones) and Table 4 (electrical panels) versus the Brick model Bldg59_w_occ Brick model.ttl (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. CAMBER follows the Brick model (the machine-readable metadata shipped with the data) and encodes it in the equipment names (RTU01_zone_022); the naming grouping is heuristic, so fleet DCV severities are capped at warn.

#### Brick point names do not match the CSV column names for key RTU points

- **Issue:** `brick-point-names-differ-from-csv`
- **Columns:** `rtu_*_oa_flow_tn`, `rtu_*_oadmpr_pct`, `rtu_*_fltrd_gnd_lvl_plenum_press_tn`, `rtu_002_econ_stpt_tn`
- **Evidence:** 15 of the Brick model's points name no CSV column (rtu_00N_oa_fr for rtu_00N_oa_flow_tn, rtu_00N_oa_damper for rtu_00N_oadmpr_pct, rtu_00N_fltrd_gnd_plenum_press_tn for rtu_00N_fltrd_gnd_lvl_plenum_press_tn, rtu_021_econ_stpt_tn for RTU02's economizer setpoint, occ_forth_south, lig_S_+), and 77 of the 336 CSV columns have no Brick point (the 44 reheat valves, the SAT setpoints, supply airflows and OA temperatures of all four RTUs, among others).
- **Contradicts:** README_Dryad_Bldg59.txt (the Brick model represents the metadata of the equipment and sensors) and the data descriptor, Methods: metadata model (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: annotate** -- left as published and recorded in the provenance. The catalog's mappings name the CSV columns directly (b59_rtu.json, b59_zone.json, b59_uft.json) and take only the zone -> RTU chain from the Brick model; a Brick-driven mapping needs these renames as overrides.

#### The data description table miscounts some files and gives the static pressure in psi

- **Issue:** `description-table-counts`
- **Columns:** `zone_*_co2`, `zone_*_fan_spd`, `zone_*_hw_valve`, `rtu_*_pa_static_stpt_tn`
- **Evidence:** zone_co2.csv has 11 CO2 columns (the table says 13; the Brick model also has 11 CO2 sensors); uft_fan_spd.csv has 51 columns and uft_hw_valve.csv 44 (the table says 44 and 51); the plenum static setpoint reads 0.06 (RTU03 also 0.6) -- an underfloor-plenum setpoint in inH2O, not the 'psi' the table states (0.06 psi would be 1.7 inH2O).
- **Contradicts:** data_description_table_3year_clean_data.xlsx (Number of data points, Unit) (Luo et al. 2022, Sci Data 9:156, doi:10.1038/s41597-022-01257-x; data doi:10.7941/D1N33Q)
- **Handling: none** -- described only. Nothing to correct in the values: the catalog maps the 11 CO2 columns that exist, and does not map the plenum static setpoint.

### `finnish-dcv`: Finnish laboratory office room with occupancy-based DCV (ground-truth counts)

#### The test's DCV law runs at 4 l/s + 6 l/s per person, not the documented 3 l/s base

- **Issue:** `dcv-base-flow-is-4-not-3`
- **Columns:** `Supply airflow (l/s)`, `Occupant count (ML estimate)`
- **Evidence:** In ventilation_test.csv the median supply airflow is 4.04, 10.06, 15.93 and 22.01 l/s at 0, 1, 2 and 3 estimated occupants -- 4 + 6n -- and the median residual against 3 + 6n is +1.02 l/s over the 1,430 minutes (median absolute residual 1.09 l/s; 76% of minutes within 1.5 l/s once the 5-minute update lag is allowed for). A +1 l/s offset at 4 l/s is 25%, beyond the flow sensor's stated +/-5%.
- **Contradicts:** Zenodo record description, 'Occupancy-based control': base ventilation 3 l/s (unoccupied), 6 l/s per detected occupant, updated every 5 minutes (Mikala, Xu, Huotari 2026, Zenodo doi:10.5281/zenodo.18299691)
- **Handling: none** -- described only. Nothing is changed: CAMBER's DCV rule does not assume a control law, it tests whether outdoor air rises with demand. The template's area floor (Ra x Az) sits below both 3 and 4 l/s.

#### Supply airflow reads negative near zero flow and spikes above 100 l/s

- **Issue:** `airflow-negative-and-spikes`
- **Columns:** `Supply airflow (l/s)`, `Extract airflow (l/s)`
- **Evidence:** The supply airflow is negative in 52, 47 and 14 minutes of the three files (minimum -6.40 l/s in training 1), and above 100 l/s in 63 minutes of training 1 (maximum 362.4 l/s on 2024-09-06, 2024-09-19 and 2025-03-25) -- about 59 air changes per hour in the 22 m3 room, against a test-period maximum of 64.9 l/s.
- **Contradicts:** Zenodo record description (Lindab FTCU airflow, +/-5%; supply and exhaust flows held equal) (Mikala, Xu, Huotari 2026, Zenodo doi:10.5281/zenodo.18299691)
- **Handling: annotate** -- left as published and recorded in the provenance. Left in place: the small negatives are the transmitter's zero offset with the supply stopped, which the DCV rule reads as no ventilation; the spikes are single minutes that the 5-minute resample averages down.

#### One relative-humidity reading of 666%

- **Issue:** `humidity-over-100`
- **Columns:** `Relative Humidity (%)`
- **Evidence:** training_data_1.csv reads 665.99% at 2024-09-16 09:53; every other reading of the three files is at most 52.5% except this one (training 1 median 37.9%).
- **Contradicts:** Zenodo record description (Miran DLS relative humidity, +/-2.5%) (Mikala, Xu, Huotari 2026, Zenodo doi:10.5281/zenodo.18299691)
- **Handling: annotate** -- left as published and recorded in the provenance. Not mapped (CAMBER has no room-humidity role), so it never reaches a rule; recorded for learners reading the raw file.

### `b4b-windesheim`: Brains4Buildings Windesheim office rooms: two CO2 sensors, ventilation valve and occupancy

#### Room 925038's valve fraction is an assumed constant, not a measurement

- **Issue:** `valve-assumed-constant`
- **Columns:** `bms_valve_frac__0 (room 925038)`
- **Evidence:** bms_valve_frac__0 is exactly 1.00 in all 2,683 of room 925038's intervals (one unique value), while the other two rooms' valves sit at the 0.20 minimum in 79-81% of intervals and reach 0.99.
- **Contradicts:** README, footnote 1 to the properties table: valve fractions of this building could not be exported and 1.00 was assumed because ventilation was intended to run at maximum; the file presents it as the BMS-measured property valve_frac__0 (Brains4Buildings2022 dataset README, https://github.com/energietransitie/b4b-windesheim-brains4buildings2022-dataset/blob/188573d39ca9a775f8ef17a10ccac784d84b7be7/README.md)
- **Handling: exclude** -- kept out of scoring / analysis. Room 925038 is ingested without its valve (mapping b4b_bms_novalve.json), so no DCV verdict is drawn from an assumed constant; its CO2, temperature and occupancy remain for inspection.

#### CO2 values are baseline-shifted by the publisher, and one room's minimum is not the stated 416 ppm

- **Issue:** `co2-baseline-shifted`
- **Columns:** `bms_co2__ppm`, `CO2-meter-SCD4x_co2__ppm`
- **Evidence:** The documented shift raises each sensor's minimum to 416 ppm, but the preprocessed minima are 455.0 ppm (room 917810, BMS), 416.1 and 426.7 ppm (the other BMS sensors) and 421-436 ppm (SCD41). In the same room the two sensors differ by a median of +7.8 ppm (917810) and -39 ppm (925038, 999169), correlating 0.80-0.91.
- **Contradicts:** README, Preprocessed data: per room and source the minimum CO2 value is raised to 415 ppm plus a 1 ppm margin (Brains4Buildings2022 dataset README, https://github.com/energietransitie/b4b-windesheim-brains4buildings2022-dataset/blob/188573d39ca9a775f8ef17a10ccac784d84b7be7/README.md)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published: the DCV rule reads CO2 changes (lift within the hour of day), which a constant shift does not move; absolute thresholds (co2_ventilation) read shifted values.

<!-- END data-issues -->

## Where files go

Downloads and extracted members live in the cache: `$CAMBER_DATA_DIR`, else
`$XDG_CACHE_HOME/camber/datasets`, else `~/.cache/camber/datasets` (override per command with
`--dir`). `camber datasets status` shows what is fetched and how much disk it uses;
`camber datasets remove <id>` deletes it (`--purge-store` also drops its facilities from a store).
Dropped facility ids are tombstoned and so never reused for another building; re-ingesting the
*same* dataset reclaims its own id (see [PORTFOLIO.md](PORTFOLIO.md#facility-identity)).
See [SECURITY.md](SECURITY.md) section 7 for the download guarantees.

## Pre-release catalog check (maintainers)

Before a release that changes the catalog, run every entry end to end on local data:

```sh
python scripts/catalog_sweep.py --out /scratch/sweep --local-root examples/_data --run-benchmarks
python scripts/catalog_sweep.py --out /scratch/sweep --skip-done           # resume after a stop
python scripts/catalog_sweep.py --out /scratch/sweep2 ... --compare /scratch/sweep/summary.json
```

For each entry (the `full` subset by default; `--subset default` for a quick pass) it seeds the
cache from the local copies (hard links, matched by size and name) and verifies them through the
real `fetch` path with the network refused (`--download-missing` allows it for files with no local
copy); ingests into one store inside a portfolio workspace and re-ingests to prove the skip on an
unchanged content hash; runs each config template with `camber run` (and `camber drift run` when a
template has a drift section); scores labelled entries, including a re-score on the LBNL
benchmark's scenario set against `examples/lbnl_fdd/benchmark-baseline.json`; lists every rule
that trips on a fault-free scenario; builds the RCx and audit reports for one fault-free and one
faulted equipment and checks their licence block and that no G36 verdict appears without a
sequence; and runs the BDG2 M&V baseline over every site against
`examples/bdg2/benchmark-baseline.json`. It writes `summary.json` and `summary.md`, stops before
free disk would drop below `--min-free-gb` (40 GB), and is resumable per dataset (`--only`,
`--skip-done`). Manual entries are verified from the seeded copies the way `ingest --from-dir`
does, and research-only entries are swept only with `--accept-noncommercial`. It is dev tooling,
not part of the package and not run in CI (it needs the full local data, tens of GB).

## Link check (maintainers)

`scripts/datasets_linkcheck.py` asks every publisher whether it still serves each catalog file at
its pinned size and ETag (`HEAD`, falling back to a one-byte ranged `GET`), and -- where an entry
sets `licence_check` (`{"url": ..., "expect": "CC BY 4.0", "json_path": "license.name"}`) --
whether the licence page still states the recorded licence. The `datasets-linkcheck` workflow runs
it weekly (and on demand) and writes the report to the job summary with a warning per drift; it
never fails the build, because `fetch` already refuses any file that differs from its pin. On
drift, re-verify the entry (`scripts/datasets_refresh.py <id>`) and re-check its licence on the
host's own page before re-pinning. `pytest -m network` runs the one test that downloads a real
(small) file end to end.
