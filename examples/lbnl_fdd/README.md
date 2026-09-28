# LBNL FDD example — semantic model, storage & fault detection

Runs CAMBER against the **LBNL Fault Detection and Diagnostics Datasets**
(single-duct AHU, fan-coil unit, and dual-duct AHU). Because these datasets use
completely different point-naming conventions *and* ship ground-truth fault labels,
they're an end-to-end test of:

- **Mapping** — LBNL point names (`CHWC_VLV`, `SA_TEMP`, `OA_DMPR`, …) → CAMBER
  roles via a mapping file (config, not code) — one per equipment family. The mappings ship
  with the dataset catalog (`camber/datasets/mappings/lbnl_*.json`), the single source of truth
  for these examples and `camber datasets ingest`.
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
python examples/lbnl_fdd/fetch.py --fpu      # also the VAV fan-power-unit subset (VAV-drift)
python examples/lbnl_fdd/fetch.py --chiller  # also the chiller-plant subset (plant-level detectors)
python examples/lbnl_fdd/benchmark.py        # FDD-accuracy + drift (AHU + FPU) + chiller-plant scores
```

`run_brick.py` parses the dataset's Brick model and derives the point→role mapping
automatically (cooling vs heating valve, OA damper, supply fan resolved from the
equipment relationships) — no hand-written mapping — then runs the pipeline
on it.

### Cross-equipment benchmark

`benchmark.py` runs the **detector suite** (outdoor-air-fraction, leaking-valve)
over labeled fault scenarios from **three different equipment families and naming
conventions** — single-duct AHU (SDAHU), fan-coil unit (FCU), and dual-duct AHU
(DDAHU) — and scores each family plus the pooled set with the generalized harness
(`camber.eval.benchmark`): overall detection, **per-detector** confusion against
each detector's target fault, and the correct-diagnosis rate. The *same* role-based
rules run unchanged across all three; only the `lbnl_*.json` mapping and each unit's
own design minimum OA differ. The mappings, the ingest spec (the catalog's fixes for
problems in the published data, its pinned timestamp formats) and each unit's rule
parameters (the run templates) all come from the dataset catalog, so the benchmark scores
exactly what `camber datasets ingest` produces. (Runs on whatever's downloaded;
SDAHU-only without `--families`.)

Results with the 0.86 catalog fixes (#24–#29; the committed `benchmark-baseline.json` was
refreshed to these in 0.86, #30):

| Family | Overall TPR | FPR | Note |
|--------|------------:|----:|------|
| SDAHU  | 40% | 0% | judged on fan-on, occupied samples against the unit's own **1.6%** minimum OA (a 10% damper position): dampers stuck at 75/100% fire; stuck at 10% (the minimum itself) or 25% (4.4% OA) look like normal operation outside economizer weather — their real symptom is the missed economizer, which `economizer_damper_drift` catches 4/4. `leaking_valve` misses the one leak run, a 10% leak (the published 010/025/040/050 "severities" are one file) |
| FCU    | 100% | 0% | incl. the OA-damper leak against the unit's 10% minimum (a 30% damper position measures 10.5% OA) — but that leak gives 15.4% OA against a 15% line: a 0.4-point margin |
| DDAHU  | 100% | 0% | against the unit's **seasonal** minimum (31.8% OA; 11.9% Jun–Aug). The flat 20% used until 0.86 read the fault-free unit as excess OA (FPR 100%) |

The benchmark **measures** these gaps rather than hides them — which is the point of
evaluating a rule library against public labeled data. What the catalog found wrong in the
published data, and how it is handled, is listed in `docs/DATASETS.md`.

### AHU-drift validation

`benchmark.py` also scores the AHU air-side **drift** detectors the SDAHU data can support, via
`camber.driftvalidation` (baseline = the fault-free annual run, current = each labeled fault run):
**economizer-damper drift** (target = stuck damper, read against the damper *command*
`OA_DMPR_DM`): recall 4/4. **Coil-valve drift** is one-sided up (fouling / starvation), which the set
doesn't label — its coil-valve leak is the opposite direction — so it is scored for specificity (1
false alarm in 6). **Duct-static-control drift** declines every case (no fittable baseline) and has
no number. Declined cases are excluded from the score and reported as `n_declined`.
Fan-efficiency and filter-loading drift need fan-power / filter-DP points the SDAHU simulation does
not export, so they stay synthetic-only; the multi-zone rogue/cohort census and the reset-request
detectors aren't validatable on a single simulated AHU at all. See `docs/VALIDATION.md` for the full
feasibility matrix and the open-licensed datasets that would close the gaps.

### Chiller-plant validation (`--chiller`)

`benchmark.py` also scores the **plant-level** chiller detectors on the CC-BY LBNL chiller-plant
subset (`lbnl_chiller.json`, plus the catalog's `fix` quirk that swaps the exported
wet-bulb/dry-bulb columns back): **chiller-efficiency** (kW/ton, target = tower fouling / PID +
three-way-bypass leak/stuck — all raise chiller lift) and **cooling-tower approach** (CW-supply vs
wet-bulb, target = tower fouling / PID). The simulated chiller/tower design curves aren't published,
so each detector's absolute design ceiling is **calibrated from the plant's own fault-free run**
(commissioning practice) rather than guessed — the informative number is then the TPR on the labeled
physical faults, with sensor-bias runs as genuine negatives. The subset is **water-side only** (no
refrigerant points), so the refrigerant-side chiller-drift family (evaporator/condenser approach,
subcooling, superheat) stays synthetic-only.

<!-- 092-plant -->
`plant_detectors.py` scores the 0.92 plant detectors on the chiller- and boiler-plant runs:
boiler fouling (`boiler_efficiency_drift`), tower fouling from fan effort
(`cooling_tower_fan_effort_drift`) and the condenser-bypass valve (`condenser_bypass_leak`), with
TPR/FPR and Wilson intervals (measured, not gated; see `docs/VALIDATION.md`).
<!-- /092-plant -->

## Data & license

Dataset: **LBNL Fault Detection and Diagnostics Datasets**, by LBNL/PNNL/NREL/
ORNL/Drexel — Creative Commons Attribution (CC-BY).
<https://www.osti.gov/dataexplorer/biblio/dataset/1881324>

Data is **not** bundled; `fetch.py` downloads it to `examples/_data/` (git-ignored). The same data
is in CAMBER's dataset catalog (`camber datasets fetch lbnl-sdahu`, see `docs/DATASETS.md`), which
verifies checksums and ingests it into a Parquet store for `camber run` / `camber report`.
The dataset also ships Brick (`.ttl`) semantic models, a natural fit for a future
Haystack/Brick interop example.
