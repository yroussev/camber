# CAMBER architecture

CAMBER is organized as a pipeline with supporting layers around it. The design
goal is **vendor-neutrality**: raw BAS points are mapped to a small vocabulary of
*roles*, and everything downstream is written against roles, so one rule set runs
on any building once its tags are mapped.

```
raw BAS/meter data
  → [ingest]    source adapters → named point series on a common time grid
  → [model]     roles + mapping + entity model (what each stream means)
  → [resolve]   discover equipment, assemble role-named frames
  → [rules]     FDD rule engine        [mandv]  M&V / change-point engine
  → [report]    Std-211 audit deliverables
```

Around the pipeline: `store/` (persistence), `interop/` (Brick/Haystack),
`integrate/` (tickets/notifications), `api/` (read API), `charts/` (visuals).

## Layers (and where they live)

### Ingest — `camber/ingest/`
Source adapters that normalize any input to named point series on a common time
grid: `csv_perpoint` (one file per point), `csv_wide` (tabular), `haystack`
(a Project-Haystack `hisRead` client behind an injectable transport). `quality.py`
scores per-point data quality (robust outliers, flatline/gap detection) with an
auditable cleaning trail. `units.py` normalizes 0–1 vs 0–100 position signals.

### Semantic model — `camber/model/`
- `roles.py` — the vendor-neutral `Role` vocabulary (the heart of the design).
- `mapping.py` — `MappingProvider`: alias/regex tables that map a source's tags to
  roles (a config file per building, not code).
- `entities.py` — Site/Equip/Point entities, equipment templates, and
  **completeness validation** (which analytics a building's instrumentation can
  support).

### Resolve — `camber/resolve.py`
`discover()` finds equipment of a class; `resolve()` loads the requested roles into
a single role-named DataFrame (the unit every rule consumes). Occupancy filtering
and percent-unit normalization happen here.

### FDD — `camber/rules/` (+ the math modules)
`rules/base.py` defines the `Rule` protocol (`roles_required`, `analyze() ->
Finding`) and a `Registry` runner. Each rule is a thin adapter over a math module
(`ahu.py`, `reheat.py`, `oafraction.py`, `chwplant.py`, `fdd_g36.py`, …). Rules are
gated by the roles present, so an under-instrumented building is skipped with a
reason rather than crashing. `rules/triage.py` ranks findings by impact and tracks
their lifecycle (new/ongoing/resolved).

### M&V — `camber/mandv/`
Change-point inverse models (`models.py`: 2P–5P + heating/cooling-zero), the
schedule-aware TOWT model (`towt.py`), fit statistics + savings uncertainty
(`stats.py`), CUSUM (`cusum.py`), weather normalization (`weather.py`), and
rate/energy-aware resampling (`resample.py`, `intervalfit.py`).

### Domain analytics
`comfort.py` (Std-55 PMV/PPD), `cost.py` (utility cost), `carbon.py` (emissions),
`water.py` (irrigation/cooling-tower/leak), `loadprofile.py` (peak/load shape),
`pv.py` / `lighting.py` (on-site systems), `eval.py` (FDD-accuracy harness).

### Reporting — `camber/report/`
`audit.py` assembles ASHRAE/ACCA Standard 211 deliverables (benchmark + ECM table +
prioritized findings) to text/HTML.

### Storage — `camber/store/`
`ParquetStore`: a tidy long-form, hive-partitioned (site/year) Parquet store keyed
to the entity model, with tag-filtered reads, rollups, and retention pruning.

### Interop — `camber/interop/`
`brick.py` derives a role mapping from a Brick (`.ttl`) model; `export.py` emits
Haystack tags / a Brick model from the entity model (round-trips).

### Integration & API — `camber/integrate/`, `camber/api/`
`integrate/tickets.py` turns findings into CMMS-ticket records with a pluggable
notifier. `api/` is a read-only HTTP facade over the store (`/sites`, `/points`,
`/history`).

## Conventions

- **Role-named frames** are the lingua franca between layers: a `DataFrame` whose
  columns are `Role` enum members.
- **Findings** (`rules/base.Finding`) are the structured output of a diagnostic
  (rule, equip, severity, metrics, summary).
- **Synthetic-first tests**: each diagnostic/model has a synthetic fixture proving
  detection/fit; real public datasets are exercised in `examples/`.
