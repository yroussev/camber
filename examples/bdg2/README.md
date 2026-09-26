# Building Data Genome 2 example — M&V engine & portfolio storage

Runs CAMBER against the **Building Data Genome Project 2** dataset (3,053
whole-building meters, 1,636 buildings, hourly, 2016–2017), a real portfolio
across many sites/climates. It demonstrates:

- **M&V** — the ASHRAE G14 / IPMVP change-point engine fit to daily energy vs
  outdoor temperature. Chilled-water cooling energy yields textbook **3PC** fits
  (R² 0.78–0.94); office **electricity** is largely schedule/plug-load driven and
  fits weakly — the engine reports that honestly (`accept=False`), it doesn't
  pretend.
- **Storage** — many buildings' hourly meters ingested into the Parquet store
  keyed by site/equipment, then queried via the catalog and read back.

## Run

```sh
python examples/bdg2/fetch.py     # downloads metadata/weather/electricity/chilledwater (LFS media)
python examples/bdg2/run_mv.py
```

## Data & license

Dataset: **Building Data Genome Project 2** (Miller et al., *Scientific Data*,
2020). The data are licensed **Creative Commons Attribution-ShareAlike 4.0 (CC-BY-SA 4.0)** — the
repository's `LICENSE`; the *paper* describing them is CC-BY, which is where earlier CAMBER docs
got "CC-BY" from. Commercial use and analysis are permitted; a redistributed *adapted* version of the
data must carry the same licence.
<https://github.com/buds-lab/building-data-genome-project-2>

Data is **not** bundled; `fetch.py` pulls it from the repo's Git-LFS media
endpoint into `examples/_data/` (git-ignored).
