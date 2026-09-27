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
