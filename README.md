# CAMBER

[![CI](https://github.com/yroussev/camber/actions/workflows/ci.yml/badge.svg)](https://github.com/yroussev/camber/actions/workflows/ci.yml)

**Commissioning, Analytics & M&V for Building Energy Re-tuning**

A vendor-neutral Python toolkit for analyzing Building Automation System (BAS)
trend data — fault detection & diagnostics (FDD), measurement & verification
(M&V), and retro-commissioning (RCx) — across *any* building, independent of the
BAS vendor.

The core idea: points are mapped to a small vocabulary of vendor-neutral **roles**
(`HEAT_VALVE`, `SUPPLY_AIR_TEMP`, `OAT`, …), and every diagnostic is written
against those roles. Map a building's tags once and the whole rule set runs on it
— one rule, all equipment, any BAS.

## What it does

- **Ingest** — adapters for per-point CSV exports, wide/tabular CSV, and a Project
  Haystack `hisRead` client (behind an injectable transport).
- **Semantic model** — a role vocabulary + mapping provider, plus a site/equipment
  /point entity model with **completeness validation** (which analytics a
  building's instrumentation can support). **Brick interop**: derive the
  point→role mapping automatically from a building's Brick (`.ttl`) model.
- **FDD** — a rule engine implementing ASHRAE Guideline 36 AFDD (operating states,
  fault conditions FC#1–15, trim-and-respond resets) and PNNL Building Re-tuning
  diagnostics (simultaneous heat/cool, reheat penalty, SAT/CHW reset, economizer,
  boiler lockout, overcooling, setback, OA fraction incl. under-ventilation,
  leaking valves, static/pump resets). Plus impact prioritization, fault-lifecycle
  tracking, and an FDD-accuracy benchmark harness (scored against labeled data).
- **M&V** — IPMVP / ASHRAE Guideline 14 change-point inverse models (2P–5P plus
  heating-zero variants) and the LBNL Time-of-Week & Temperature (TOWT) model for
  schedule-driven loads, CV(RMSE)/NMBE statistics and fractional savings
  uncertainty, CUSUM savings tracking, weather normalization (TMY/EPW), and
  rate/energy-aware resampling.
- **Cost & carbon** — utility cost accounting (energy + demand + time-of-use, and
  tiered water/wastewater with marginal vs. average rates) and GHG emissions
  (eGRID/EIA factors → CO₂e and intensity).
- **Water** — irrigation water budgets (ETo / landscape coefficient / efficiency /
  effective precipitation), cooling-tower makeup (cycles of concentration,
  gal/ton-hr), and leak detection (minimum night flow, flow duration, leak cost).
- **Load profiling** — peak / near-peak / base, base-to-peak ratio, load factor,
  load-duration curve, and weekday/weekend daily shapes.
- **On-site systems** — PV performance ratio, specific yield, and net/self-
  consumption energy; lighting operational efficiency vs. installed power with
  controls-fault flags (failed setback, no turndown).
- **Comfort** — Fanger PMV/PPD per ASHRAE Standard 55 / ISO 7730.
- **Reporting** — ASHRAE/ACCA Standard 211 audit deliverables (benchmark +
  prioritized findings + ECM table) to text/HTML, and a fleet/portfolio rollup
  (cross-sectional EUI benchmarking + fleet-wide fault counts).
- **Storage** — a Parquet time-series store keyed to the entity model, with
  tag-filtered reads.
- **Quality** — per-point data-quality scoring (robust outliers, flatline/gap
  detection) with an auditable cleaning trail.
- **Integration** — findings → CMMS-ticket records and a pluggable notifier; a
  read-only HTTP API over the store (`python -m camber.api.server <store> [port]`:
  `/sites`, `/points`, `/history`).

## Install

Python 3.10+.

```sh
pip install -e .            # the package (editable)
pip install -e .[dev]       # + pytest, for development
pip install -e .[brick]     # + rdflib, for robust Brick-model parsing (optional)
pip install -e .[haystack]  # + a Haystack client (phable / pyhaystack) (optional)
```

## Quickstart

```sh
python -m pytest -q                 # run the test suite
python examples/synthetic_demo.py   # data-free FDD demo on generated trends
```

## Usage

Everything runs on **role-named frames** — a DataFrame whose columns are
vendor-neutral `Role`s. Map a building's tags to roles once, then every diagnostic
and model runs on it.

**Fault detection** — run a diagnostic, get a structured `Finding`:

```python
import numpy as np, pandas as pd
from camber.model.roles import Role
from camber.rules.simul_hc import SimultaneousHeatCool

idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
frame = pd.DataFrame({
    Role.OAT:        90 + 10 * np.sin((idx.hour - 9) / 24 * 2 * np.pi),
    Role.COOL_VALVE: 70.0,                                  # cooling all day
    Role.HEAT_VALVE: np.where((idx.dayofweek < 5) & idx.hour.isin([11, 12, 13, 14]),
                              40.0, 0.0),                    # midday reheat — a fault
}, index=idx)

f = SimultaneousHeatCool().analyze("AHU_1", frame)
print(f.severity, f.metrics["simultaneous_hc_pct"])         # -> fault 36.36
```

**Measurement & verification** — fit a change-point baseline and score it:

```python
import numpy as np
from camber.mandv.models import best_model, N_PARAMS
from camber.mandv.stats import fit_stats

oat = np.linspace(35, 100, 120)
energy = 50 + np.clip(oat - 65, 0, None) * 3 + np.random.default_rng(0).normal(0, 2, 120)
m = best_model(oat, energy)                                 # picks the inverse model
st = fit_stats(energy, m.predict(oat), N_PARAMS[m.kind])
print(m.kind, round(st.r2, 2), f"{st.cv_rmse:.0%}")         # -> 3PC 1.0 2%
```

**Your own building** — map point names → roles in a small JSON config (or derive
it from a Brick model with `camber.interop.brick`), then `resolve()` assembles the
role-frames. See `examples/` for end-to-end runs on public datasets.

**Reproducible runs** — describe a whole analysis (source → mapping → equipment →
rules → report) in one JSON config and run it without a script:

```sh
python -m camber.config run.json     # discovers equipment, runs the rules, writes the report
```

## Docker

```sh
docker build -t camber .
docker run --rm camber               # runs the test suite as a clean-build proof
docker run --rm -it camber bash      # interactive shell
```

Mount a building's CSV export at `/data` to run analytics on real trends.

## Public datasets

The toolkit is data-agnostic. Two open datasets are wired as runnable examples
(referenced + fetched, not bundled):

- **[LBNL Fault Detection and Diagnostics Datasets](https://www.osti.gov/dataexplorer/biblio/dataset/1881324)**
  (CC-BY) — labeled single-duct-AHU data. `examples/lbnl_fdd/` maps its point
  names to roles, validates completeness, round-trips through the Parquet store,
  and detects a labeled stuck-damper fault (OAF ~21% baseline → ~100% fault).
- **[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)**
  (CC-BY) — 3,053 whole-building hourly meters. `examples/bdg2/` fits the
  G14/IPMVP change-point engine (textbook 3PC on cooling energy, R² 0.78–0.94)
  and ingests the portfolio into the store.

Each example has a `fetch.py` (downloads to the git-ignored `examples/_data/`) and
a runnable script. See the per-example READMEs.

## Contributing

Contributions are welcome — new diagnostics, ingest adapters, M&V models, ontology
interop, docs, and fixes. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup
and conventions, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layered
design, [ROADMAP.md](ROADMAP.md) for what's planned and where to help,
[docs/ECOSYSTEM.md](docs/ECOSYSTEM.md) for the OSS-integration strategy, and the
[Code of Conduct](CODE_OF_CONDUCT.md). Security reports: see [SECURITY.md](SECURITY.md).

## Provenance

This is a clean-room implementation. Algorithms are reimplemented from public
standards — ASHRAE Guideline 36, Guideline 14, Standard 55, Standard 211; IPMVP;
PNNL Building Re-tuning; NIST APAR. No third-party source code is included.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

> Status: pre-release (v0.x). APIs may change.
