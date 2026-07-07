# Visualization

CAMBER's charts fuse graphing and diagnostics: **every chart can surface the faults inside it,
and every fault can render the trend that proves it.** All charts are matplotlib, draw onto a
supplied `Axes`, and lazy-import pyplot. The 0.2 MVP is the **A → B → E → I** slice plus a
self-contained HTML assembler; the primitives (load carpet, CUSUM, energy signature) shipped in
0.1.

## The MVP slice

| Pattern | Module | What it shows |
|---|---|---|
| **A** Ingest readiness | `camber.charts.readiness` | per-point presence ribbon (green = data present) + coverage % |
| **B** Fault-annotated trend | `camber.charts.multitrend` | synchronized multi-trend with rule-violation spans shaded |
| **E** Load carpet | `camber.charts.carpet` | hour-of-day × date heatmap (occupancy, setback, stuck-on days) |
| **I** Data-quality dashboard | `camber.charts.quality_dashboard` | points × {coverage, score, flatline, outliers} heatmap |

### A — readiness ribbon
```python
from camber.charts.readiness import readiness_ribbon
readiness_ribbon(df, max_bins=240)        # df: wide point/role frame
```
Flags = `max_bins` (time resolution), `title`, `max_xticks`. `presence_matrix(df)` returns the
raw `(matrix, bin_starts, coverage)` if you want the numbers.

### B — fault-annotated multi-trend
```python
from camber.charts.multitrend import fault_multitrend
fault_multitrend(df, ["load_kw", "sat"], spans={"high_load": df["load_kw"] > 95}, normalize=True)
```
`spans` is `{label: boolean Series}` — each rule supplies the timestamps where it tripped, and
each True run is shaded once. Flags: `normalize` (overlay disparate units 0–1), `shade_color`,
`shade_alpha`, `title`. `mask_to_spans(mask)` is the reusable mask→intervals helper.

### I — data-quality dashboard
```python
from camber.charts.quality_dashboard import quality_dashboard
quality_dashboard(df, metrics=("coverage", "score", "flatline_frac", "outlier_frac"))
```
A heatmap colored so **green = good** for every metric (higher-is-better and lower-is-better are
both mapped correctly). Built from `camber.ingest.quality.assess`.

## Deepening the catalog (0.3)

Beyond the MVP slice, 0.3 builds the pattern catalog toward **rules as a chart engine** — every
rule renders its own evidence. First renderer: pattern **D**.

### D — OAT cloud-shape scatter
```python
from camber.charts.oat_scatter import oat_scatter, classify_shape, brush_back
ax, shape = oat_scatter(load_kw, oat, ylabel="kW")   # overlays the change-point fit + guides
shape.shape          # "linear" | "hockey-stick" | "v" | "scattered"
```
The *shape of the cloud* against outdoor-air temperature reveals control behavior a time-series
buries. `classify_shape(series, oat)` labels it from the fitted change-point model + goodness of fit
(a weak fit → `scattered`, i.e. no OAT dependence) with no chart required, returning a
JSON-friendly `CloudShape`. `brush_back(series, oat, x_range=…, y_range=…)` maps a selected region
of the cloud back to the **timestamps** that produced it — the primitive the interactive-linking
layer (below) uses to answer "when did this cluster happen?".

Flags: `changepoint` (`"auto"`/a kind/`False`), `classify`, `by` (colour by season/occupancy),
`min_max_avg` (per-point min–max whiskers — provenance over a bare average), `cmap`. Generalizes the
`energy_signature` plot to any point (airflow, valve %, ΔT), not just energy.

### G — templated subsystem diagnostic scatters
```python
from camber.charts.diagnostic import diagnostic_scatter, TEMPLATES
ax, violating = diagnostic_scatter(role_frame, TEMPLATES["sat_reset"])   # violating: bool Series
```
Each subsystem has an *expected signature*; a `DiagnosticTemplate` names two roles and an
`expected(x) -> (low, high)` band, and `diagnostic_scatter` overlays that band, shades the points
outside it, and returns the **violating mask** — so the figure doubles as a rule's evidence (feeds
pattern J). Packaged `TEMPLATES`: `sat_reset`, `chw_reset` (reset schedules vs OAT — clamped at the
endpoints), `economizer` (OA damper open for free cooling, minimum when hot), `no_simultaneous_hc`
(heating valve must be ~0 when cooling is active). Build your own with `band`, `reset_line`,
`economizer_template`, `no_simultaneous_template`. Flags: `shade`, `tolerance`.

## The HTML dashboard

`camber.report.build_dashboard` assembles the sections + the ranked findings into **one
self-contained HTML page** — matplotlib figures inlined as base64 PNG, no web framework, no
external assets.

```python
from camber.report import build_dashboard
html = build_dashboard(df, findings=findings, spans={"high_load": df["load_kw"] > 95},
                       carpet_col="load_kw", rank_by="cost", title="Site dashboard")
open("dashboard.html", "w").write(html)
```

### Option flags — `build_dashboard`
| flag | default | effect |
|---|---|---|
| `sections` | `("A","B","E","I")` | which sections to render, in order |
| `findings` | `None` | listed ranked beneath the charts (actionable only) |
| `spans` | `None` | `{label: boolean Series}` shaded in section B |
| `rank_by` | `"severity"` | findings order — `"severity"` or `"cost"` (annual $ if present) |
| `top_n` | `20` | max findings listed |
| `carpet_col` | first column | which point the carpet (E) draws |
| `multitrend_cols` | all | which points section B overlays |
| `normalize` | `True` | normalize the multi-trend overlay |

## Scope

This is the dependency-light MVP: rich, self-contained HTML rather than a live web UI. The fuller
interactive vision (brush-linked views, agent narration, continuous refresh) is in the
[ROADMAP](../ROADMAP.md) Visualizations section.
