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

Full API-level detail — every capability, its flags, and the standard it cites — is in
**[docs/CAPABILITIES.md](docs/CAPABILITIES.md)**.

- **Ingest** — per-point and wide/tabular CSV, a long/tall adapter, named **vendor profiles** and a
  multi-format timestamp/value parser (ISO / US / EU-dayfirst / BAS / epoch / Excel-serial), a
  Project-Haystack `hisRead` client, SQL/historian readers, and **read-only network adapters**
  (Modbus, MQTT/Sparkplug, BACnet incl. experimental BACnet/SC, OPC-UA) — read-only *by
  construction*, lazy-imported, historian-first ([SECURITY](docs/SECURITY.md)).
- **Semantic model** — a vendor-neutral `Role` vocabulary + mapping provider, a site/equipment/point
  entity model with **completeness validation**, a **served-by topology** populated from Brick,
  Haystack or naming, and interop both ways with **Brick**, **Haystack** tags and **ASHRAE 223P**.
- **FDD** — ASHRAE Guideline 36 AFDD (operating states, FC#1–15, trim-and-respond resets) and PNNL
  Building Re-tuning diagnostics; an 11-rule **central plant & hydronic** library; **packaged/DX and
  refrigerant-side** rules (RTU, heat-pump/VRF, DOAS, FCU); a **Sequence-of-Operations conformance
  engine** with a packaged G36 clause library; **cohort/peer** and topology-scoped **fleet** rules.
- **Drift detection** — the complement to "is this value wrong?": *has this equipment been drifting
  away from its own frozen baseline?* Six families — [chiller](docs/CHILLER-DRIFT.md),
  condenser, evaporator, [pump/hydronic](docs/PUMP-DRIFT.md), [AHU air-side](docs/AHU-DRIFT.md) and
  [VAV zone-terminal](docs/VAV-DRIFT.md) — each comparing at *matched load or duty*, each rolling up
  to one localized verdict, and each driveable from a config or `camber drift`
  ([CLI](docs/CLI.md#drift-baselines)).
- **Trim-and-Respond / G36 reset analytics** — does the plant's reset logic do what G36 intends?
  Reset compliance and effectiveness, plus a **rogue-zone census** (which zone monopolizes the reset)
  and its common-mode twin, **cohort starvation**. See [TR-RESET.md](docs/TR-RESET.md).
- **Data trust** — sensor faults are not equipment faults: physical bounds, cross-sensor
  consistency, sensor **drift vs an external reference**, and mapping confidence, wired as a **gate**
  so a rule that cannot trust its inputs declines to fire rather than reporting a false fault.
- **M&V** — IPMVP **Options A / B / C / D**: change-point models (2P–5P + zero variants), LBNL TOWT,
  G14 fit statistics and fractional savings uncertainty, CUSUM, weather normalization, normalized
  annual savings, non-routine adjustment, retrofit isolation, variable-base degree-day, and a
  1R1C/2R2C grey-box calibrated to metered energy ([OPTION-D](docs/OPTION-D.md)). CalTRACK-aligned.
- **Commissioning** — RCx/MBCx: functional-test scoring, before/after persistence checks, and a
  measure register grading each fix to verified / regressed / inconclusive.
- **Money & compliance** — a native tariff engine + OpenEI URDB, bill validation, ECM NPV/IRR/SIR,
  demand & peak analytics, per-fault dollar **economics**, and BPS / EUI compliance checks.
- **Grid & carbon** — demand response and flexibility quantification, carbon-aware load timing,
  hourly/marginal Scope-2, and OpenADR export.
- **Domain analytics** — Std-55 comfort, CO₂/62.1 ventilation, cost, carbon, water, load profiling
  and disaggregation, schedule inference, PV (+ pvlib), psychrometrics (+ PsychroLib), lighting.
- **Weather** — two keyless providers (NASA POWER's global grid, NOAA/ISD's real stations) plus a
  geocoder, so you can fetch outdoor conditions by address ([WEATHER](docs/WEATHER.md)).
- **Advisory & synthesis** — impact prioritization, root-cause grouping, fault-lifecycle tracking,
  advisory setpoint/sequence suggestions (ASO), action plans, and a building health **scorecard**.
- **AI-assist (advisory, provider-agnostic)** — assisted point mapping and **grounded** explanation
  and Q&A over the deterministic layers, citing the rule and data behind every claim. Fully useful
  with no LLM wired; no vendor named, no SDK, no network ([AGENT](docs/AGENT.md)).
- **Reporting & visualization** — ASHRAE/ACCA Standard 211 audits, a portfolio rollup ranked by
  recoverable dollars, ten chart patterns where **every rule renders its own evidence**, a
  self-contained HTML dashboard with cross-panel brush linking, and a live web UI.
- **Storage & platform** — a partitioned Parquet store with rollups, retention and a cached catalog
  (validated to portfolio scale), a plugin API, findings → CMMS + notifiers, a read-only HTTP API,
  and a one-way **edge forwarder** for cybersecure edge→cloud collection.
- **Validation** — accuracy scored against labeled public data and CI-gated, plus `camber validate`,
  a single credibility dossier. Honest about its limits: [VALIDATION.md](docs/VALIDATION.md) states
  per detector family which claims rest on real data and which are synthetic-only.

## Install

Python 3.10+. The PyPI distribution name is **`camber-toolkit`** (it imports as `camber`).

```sh
pip install camber-toolkit             # from PyPI
pip install "camber-toolkit[brick]"    # + rdflib, for robust Brick-model parsing (optional)
```

The core is dependency-light (numpy / pandas / pyarrow / matplotlib). Everything else is an
**optional extra**, lazy-imported so the core never pays for it: `brick`, `haystack`, `modbus`,
`mqtt`, `bacnet`, `opcua`, `pv`, `psychro`, `tariff`, `ml`, `energyplus`, `docs`, `dev`. Install
what you use.

```sh
pip install -e .            # the package (editable)
pip install -e .[dev]       # + pytest / ruff / mypy, for development
```

## Quickstart

```sh
python -m pytest -q                 # run the test suite
python examples/synthetic_demo.py   # data-free FDD demo on generated trends
```

CAMBER installs a `camber` console script. A whole analysis is one JSON config
(source → mapping → equipment → rules → report) and one command:

```sh
camber run    config.json --out out/   # discover equipment, run the rules, write findings.json
camber report config.json --out audit.html
camber ask    "which building is worst?" --config config.json   # grounded, cited
camber serve  ./store                  # read-only API + live dashboard at /ui
```

Drift detection needs a frozen reference, so it has its own verbs — see
[docs/CLI.md](docs/CLI.md#drift-baselines) and the runnable
[`examples/drift/`](examples/drift/) walkthrough:

```sh
camber drift freeze config.json        # establish the baselines (the only create path)
camber drift run    config.json        # score the current window against them
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
frame = pd.DataFrame(
    {
        Role.OAT: 90 + 10 * np.sin((idx.hour - 9) / 24 * 2 * np.pi),
        Role.COOL_VALVE: 70.0,  # cooling all day
        Role.HEAT_VALVE: np.where(
            (idx.dayofweek < 5) & idx.hour.isin([11, 12, 13, 14]), 40.0, 0.0
        ),  # midday reheat — a fault
    },
    index=idx,
)

f = SimultaneousHeatCool().analyze("AHU_1", frame)
print(f.severity, f.metrics["simultaneous_hc_pct"])  # -> fault 36.36
```

**Measurement & verification** — fit a change-point baseline and score it:

```python
import numpy as np
from camber.mandv.models import best_model, N_PARAMS
from camber.mandv.stats import fit_stats

oat = np.linspace(35, 100, 120)
energy = 50 + np.clip(oat - 65, 0, None) * 3 + np.random.default_rng(0).normal(0, 2, 120)
m = best_model(oat, energy)  # picks the inverse model
st = fit_stats(energy, m.predict(oat), N_PARAMS[m.kind])
print(m.kind, round(st.r2, 2), f"{st.cv_rmse:.0%}")  # -> 3PC 1.0 2%
```

**Your own building** — map point names → roles in a small JSON config (or derive
it from a Brick model with `camber.interop.brick`), then `resolve()` assembles the
role-frames. See `examples/` for end-to-end runs on public datasets.

**Reproducible runs** — describe a whole analysis in one JSON config and run it without a
script: `camber run config.json` (or `python -m camber.config config.json`). Add a `drift`
section and the same command scores baseline-vs-current drift alongside the rules.

## Docker

```sh
docker build -t camber .
docker run --rm camber               # runs the test suite as a clean-build proof
docker run --rm -it camber bash      # interactive shell
```

Mount a building's CSV export at `/data` to run analytics on real trends.

## Public datasets

The toolkit is data-agnostic. Two open sources are wired as runnable examples
(referenced + fetched, not bundled):

- **[LBNL Fault Detection and Diagnostics Datasets](https://www.osti.gov/dataexplorer/biblio/dataset/1881324)**
  (CC-BY) — labeled equipment data. `examples/lbnl_fdd/` maps its point names to roles, validates
  completeness, round-trips through the Parquet store, and scores the detector suite across five
  wired subsets: single-duct AHU, fan-coil unit, dual-duct AHU, the VAV fan-power-unit set
  (`--fpu`) and the chiller-plant set (`--chiller`). Which detectors each subset can *honestly*
  test — and which stay synthetic-only — is stated in [VALIDATION.md](docs/VALIDATION.md).
- **[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)**
  (CC-BY) — 3,053 whole-building hourly meters. `examples/bdg2/` fits the
  G14/IPMVP change-point engine (textbook 3PC on cooling energy, R² 0.78–0.94)
  and ingests the portfolio into the store.

Each example has a `fetch.py` (downloads to the git-ignored `examples/_data/`) and
a runnable script. See the per-example READMEs.

## Contributing

Contributions are welcome — new diagnostics, ingest adapters, M&V models, ontology
interop, docs, and fixes. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup
and conventions, [docs/CAPABILITIES.md](docs/CAPABILITIES.md) for a full capability
reference (API + option flags per feature), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
for the layered design, [ROADMAP.md](ROADMAP.md) for what's planned and where to help,
[docs/ECOSYSTEM.md](docs/ECOSYSTEM.md) for the OSS-integration strategy, and the
[Code of Conduct](CODE_OF_CONDUCT.md). Security reports: see [SECURITY.md](SECURITY.md).

## Community & support

Usage questions and ideas: [GitHub Discussions](https://github.com/yroussev/camber/discussions).
Bugs and concrete feature requests: [Issues](https://github.com/yroussev/camber/issues). Please keep
everything **vendor- and site-neutral** — describe scenarios generically and never post a real client
site name or raw building data.

## Provenance

This is a clean-room implementation. Algorithms are reimplemented from public
standards — ASHRAE Guideline 36, Guideline 14, Standard 55, Standard 211; IPMVP;
PNNL Building Re-tuning; NIST APAR. No third-party source code is included.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

> Status: pre-release (v0.x). The public surface is settled and locked by a snapshot test, and
> changes follow the deprecation policy in [docs/API-STABILITY.md](docs/API-STABILITY.md) — but
> until 1.0 it can still move.
