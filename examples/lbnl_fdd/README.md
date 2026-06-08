# LBNL FDD example — semantic model, storage & fault detection

Runs CAMBER against the **LBNL Fault Detection and Diagnostics Datasets**
(single-duct AHU, fan-coil unit, and dual-duct AHU). Because these datasets use
completely different point-naming conventions *and* ship ground-truth fault labels,
they're an end-to-end test of:

- **Mapping** — LBNL point names (`CHWC_VLV`, `SA_TEMP`, `OA_DMPR`, …) → CAMBER
  roles via `mapping*.json` (a config file, not code) — one per equipment family.
- **Completeness** — the entity model reports the SDAHU is cooling-only (no heating
  valve) and gates heat-coil rules accordingly.
- **Storage** — the role-frame round-trips through the Parquet store.
- **Detection** — outdoor-air-fraction diagnostic on the fault-free baseline vs a
  labeled stuck-open-damper scenario (OAF ~21% → `ok` vs ~100% → `fault`).
- **Cross-equipment generalization** — the same rules scored across three equipment
  families with only the mapping config changing (see the benchmark table below).

## Run

```sh
python examples/lbnl_fdd/fetch.py            # SDAHU zip (~580 MB), extract the CSVs
python examples/lbnl_fdd/run_fdd.py          # mapping -> completeness -> store -> detection
python examples/lbnl_fdd/run_brick.py        # derive the role mapping from the Brick (.ttl) model
python examples/lbnl_fdd/fetch.py --families # also FCU + DDAHU (large: ~0.5 + ~1.7 GB)
python examples/lbnl_fdd/benchmark.py        # FDD-accuracy across equipment families
```

`run_brick.py` parses the dataset's Brick model and derives the point→role mapping
automatically (cooling vs heating valve, OA damper, supply fan resolved from the
equipment relationships) — no hand-written `mapping.json` — then runs the pipeline
on it.

### Cross-equipment benchmark

`benchmark.py` runs the **detector suite** (outdoor-air-fraction, leaking-valve)
over labeled fault scenarios from **three different equipment families and naming
conventions** — single-duct AHU (SDAHU), fan-coil unit (FCU), and dual-duct AHU
(DDAHU) — and scores each family plus the pooled set with the generalized harness
(`camber.eval.benchmark`): overall detection, **per-detector** confusion against
each detector's target fault, and the correct-diagnosis rate. The *same* role-based
rules run unchanged across all three; only the `mapping_*.json` config and the unit's
design-minimum OA differ. (Runs on whatever's downloaded; SDAHU-only without
`--families`.)

Results illustrate both the reach and the honest limits of a temperature-based OA
diagnostic:

| Family | OA-fraction TPR | FPR | Note |
|--------|----------------:|----:|------|
| SDAHU  | 100% | 0% | dampers open *and* closed; leak detector under-fires on the modulating-valve leak |
| FCU    | 100% | 0% | incl. the OA-damper leak, once the FCU's ~10% design-min OA is configured |
| DDAHU  |  50% | 100% | **degrades** — dual-duct hot/cold-deck mixing + mild-weather OAF noise blur the signal |

The benchmark **measures** these gaps (the leak under-fire; the dual-duct
transferability loss) rather than hides them — which is the point of evaluating a
rule library against public labeled data.

## Data & license

Dataset: **LBNL Fault Detection and Diagnostics Datasets**, by LBNL/PNNL/NREL/
ORNL/Drexel — Creative Commons Attribution (CC-BY).
<https://www.osti.gov/dataexplorer/biblio/dataset/1881324>

Data is **not** bundled; `fetch.py` downloads it to `examples/_data/` (git-ignored).
The dataset also ships Brick (`.ttl`) semantic models, a natural fit for a future
Haystack/Brick interop example.
