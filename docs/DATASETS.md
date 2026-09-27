# Open datasets

CAMBER ships a reviewed **catalog** of open building datasets that it knows how to download,
verify, normalize and analyse. Pick a dataset, and CAMBER fetches it from its publisher, ingests it
into a Parquet store, and writes a config so `camber run`, `camber report` and `camber drift` work
on it straight away. Labelled datasets can then be **scored**: how many of the known faults did the
rules find, and how many false alarms did they raise?

CAMBER redistributes none of the data. The catalog (`camber/datasets/catalog.json`, package data)
records where each dataset is published, its licence and citation, and the size and SHA-256 of
every file; `camber datasets fetch` downloads the files from the publisher onto your machine.

> **Status:** 0.86 ships the first seven entries and the command-line workflow. More datasets,
> the research-only tier, a local catalog UI (`camber lab`) and worked exercises follow in later
> releases. The Python API (`camber.datasets`) is **provisional** -- see
> [API-STABILITY.md](API-STABILITY.md).

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

## The catalog (0.86)

| id | what | kind | labelled | licence | default subset |
|---|---|---|---|---|---|
| `lbnl-sdahu` | single-duct AHU, 21 runs | simulated | yes | CC-BY-4.0 | 8 runs + a spliced onset run |
| `lbnl-fcu` | fan-coil unit, 49 runs | simulated | yes | CC-BY-4.0 | 4 runs |
| `lbnl-ddahu` | dual-duct AHU, 56 runs | simulated | yes | CC-BY-4.0 | 3 runs |
| `lbnl-fpu` | fan-powered VAV boxes (parallel + series), 62 runs | simulated | yes | CC-BY-4.0 | 5 runs |
| `lbnl-chiller` | chiller plant, 24 runs | simulated | yes | CC-BY-4.0 | 4 runs |
| `lbnl-boiler` | boiler plant, 17 runs, Brick model | simulated | yes | CC-BY-4.0 | 4 runs |
| `bdg2` | 3,053 whole-building meters, 19 sites | real | no | CC-BY-SA-4.0 | 10 sites x up to 4 buildings |

`camber datasets info <id>` prints the full entry: publisher, citation and DOI, what it teaches,
the subsets and their download sizes, and the entry's **known issues**.

Every entry has a `default` subset (small enough to try) and a `full` one. A subset selects the
files to download and, for labelled datasets, the runs to ingest; for `bdg2` it selects sites,
meters and a cap on buildings per site. Choose one with `--subset full`.

## Licences

Each entry carries an SPDX licence id and an **access** tier:

- **open** -- the licence allows commercial use (CC0, CC-BY, CC-BY-SA, ...). Fetch it freely; cite
  the publisher. A **share-alike** licence (BDG2 is CC-BY-SA-4.0) additionally means a
  *redistributed adaptation* of the data must keep the same licence -- analysing it, including
  commercially, is fine. Reports built from share-alike data say so.
- **research_only** -- the licence is non-commercial (NC) or no-derivatives (ND). `fetch` refuses
  it unless you pass `--accept-noncommercial`; the acceptance is recorded in
  `acknowledgements.json`, and every report built from the data carries a **non-commercial /
  do-not-redistribute** banner. (0.86 ships no research-only entries yet.)

The catalog validator enforces that `access` is `research_only` exactly when the licence is NC or
ND, that every URL is HTTPS, and that every file is pinned.

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
- **Evidence:** Fault-free SA_SP holds 401.9 with the fan off (median) and 403.5 with it on (range 401.8-410.6): ~1.61 inH2O expressed in Pa, and it never falls toward zero with the fan stopped. In the 20 faulted runs SA_SP is the measured static in inH2O (fan-on median 1.51-1.61, fan-off median 0.003-0.005).
- **Contradicts:** SDAHU inventory Table 2 (SA_SP: supply air duct static pressure, inches H2O) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Masked to missing in the fault-free run (mask quirk: values above 100); the faulted runs' measured static is kept.

#### SA_CFM and RA_CFM are not in cfm

- **Issue:** `sa-ra-cfm-units`
- **Columns:** `SA_CFM`, `RA_CFM`
- **Evidence:** SA_CFM peaks at 1,266,232 and has a fan-on median of 596,187 -- implausible as cfm for one floor's air handler.
- **Contradicts:** SDAHU inventory Table 2 (SA_CFM / RA_CFM: supply / return airflow, CFM) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left as published; treat the airflow role as relative, not absolute.

### `lbnl-fcu`: LBNL simulated fan-coil unit (labelled faults)

No published-data issues are recorded for this dataset.

### `lbnl-ddahu`: LBNL simulated dual-duct AHU (labelled faults)

No published-data issues are recorded for this dataset.

### `lbnl-fpu`: LBNL simulated fan-powered VAV terminal units (labelled faults)

No published-data issues are recorded for this dataset.

### `lbnl-chiller`: LBNL simulated chiller plant (labelled faults)

#### Outdoor dry-bulb and wet-bulb columns are swapped

- **Issue:** `oa-dry-wet-bulb-swapped`
- **Columns:** `OA_TEMP`, `OA_TEMP_WB`
- **Evidence:** The tower leaving-water setpoint follows max(wet-bulb + 8 F, 60 F): on the 210,661 minutes above the 60 F floor CT_SW_TEMPSPT equals OA_TEMP + 8 to a median 0.28 F but OA_TEMP_WB + 8 only to 6.27 F; OA_TEMP_WB exceeds OA_TEMP in 99.3% of rows (a wet-bulb cannot exceed its dry-bulb).
- **Contradicts:** Chiller-plant inventory Table 2 (OA_TEMP: dry bulb; OA_TEMP_WB: wet bulb) and Eq. 3 / Table 1 (tower setpoint = wet-bulb + 8 F) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Swapped back before mapping (swap quirk), in every run.

#### Secondary-loop supply and return temperatures are swapped

- **Issue:** `secondary-supply-return-swapped`
- **Columns:** `CWL_SEC_SW_TEMP`, `CWL_SEC_RW_TEMP`
- **Evidence:** In the 169,809 rows where the secondary loop carries load (CWL_SEC_LOAD > 10) the labelled return is colder than the labelled supply in 100% of rows, while the primary loop's return is warmer in 99.9% of them; hourly (supply - return) correlates +0.88 with CWL_SEC_LOAD.
- **Contradicts:** Chiller-plant inventory Table 2 (CWL_SEC_SW_TEMP: supply, CWL_SEC_RW_TEMP: return water temperature) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: fix** -- corrected at ingest (skipped by `--no-corrections`). Swapped back before mapping (swap quirk), in every run; the primary loop is left as published.

### `lbnl-boiler`: LBNL simulated boiler plant (labelled faults, Brick model)

#### BOI_STA is the boiler enable, not burner firing

- **Issue:** `boiler-status-is-enable`
- **Columns:** `BOI_STA_1`, `BOI_STA_2`
- **Evidence:** In the fault-free run BOI_STA_1 is 1 in 100% of rows while boiler 1 burns no gas (BOI_GAS_CSUM_1 = 0) in 50.5% of them.
- **Contradicts:** Boiler-plant inventory Table 2 (BOI_STA: on-off status of a boiler, 0-Off; 1-On) (LBNL FDD Data Sets, doi:10.25984/1881324; Granderson et al. 2023, Sci Data 10:342, doi:10.1038/s41597-023-02197-w)
- **Handling: annotate** -- left as published and recorded in the provenance. Left unmapped: mapping it to boiler_status would read every enabled-but-idle minute as firing, so rules needing boiler_status decline on this plant.

### `bdg2`: Building Data Genome 2 (whole-building meters)

No published-data issues are recorded for this dataset.

<!-- END data-issues -->

## Where files go

Downloads and extracted members live in the cache: `$CAMBER_DATA_DIR`, else
`$XDG_CACHE_HOME/camber/datasets`, else `~/.cache/camber/datasets` (override per command with
`--dir`). `camber datasets status` shows what is fetched and how much disk it uses;
`camber datasets remove <id>` deletes it (`--purge-store` also drops its facilities from a store).
Dropped facility ids are tombstoned and so never reused for another building; re-ingesting the
*same* dataset reclaims its own id (see [PORTFOLIO.md](PORTFOLIO.md#facility-identity)).
See [SECURITY.md](SECURITY.md) section 7 for the download guarantees.
