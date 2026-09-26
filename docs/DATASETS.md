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
- **Quirks.** Known dataset errors are declared on the entry. Labelling errors are **fixed** before
  mapping (the chiller plant's swapped outdoor wet/dry-bulb and secondary-loop supply/return
  columns; the single-duct AHU's placeholder static-pressure values). Genuine data faults an
  exercise relies on are **annotated** and left in place.
- **Provenance.** The facility's registry entry records, under its `"dataset"` key, the licence,
  citation, DOIs, file checksums, the ingest's content hash, the fault label of every scenario and
  the quirks applied. Reports read it to print a **Data source & licence** block.
- **Idempotent.** Re-running `ingest` with the same inputs is skipped; `--force` or a different
  subset replaces the dataset's facilities atomically (staged, then swapped in).

## Scoring

`camber datasets score <id> --store DIR` runs the entry's config template (or reads
`--findings findings.json` from `camber run --out`), maps each scenario's findings to "which
detectors fired", and scores them against the labels with the LBNL FDD evaluation framework:
overall detection (TPR / FPR / accuracy), each target detector against its own fault type, and the
correct-diagnosis rate -- every rate with a Wilson 95% interval. The scored detectors are the
entry's declared targets (e.g. `outdoor_air_fraction` -> `damper` for `lbnl-sdahu`), so context
rules in the template do not move the score.

## Where files go

Downloads and extracted members live in the cache: `$CAMBER_DATA_DIR`, else
`$XDG_CACHE_HOME/camber/datasets`, else `~/.cache/camber/datasets` (override per command with
`--dir`). `camber datasets status` shows what is fetched and how much disk it uses;
`camber datasets remove <id>` deletes it (`--purge-store` also drops its facilities from a store).
See [SECURITY.md](SECURITY.md) section 7 for the download guarantees.
