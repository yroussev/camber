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

readiness_ribbon(df, max_bins=240)  # df: wide point/role frame
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

ax, shape = oat_scatter(load_kw, oat, ylabel="kW")  # overlays the change-point fit + guides
shape.shape  # "linear" | "hockey-stick" | "v" | "scattered"
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

ax, violating = diagnostic_scatter(role_frame, TEMPLATES["sat_reset"])  # violating: bool Series
```
Each subsystem has an *expected signature*; a `DiagnosticTemplate` names two roles and an
`expected(x) -> (low, high)` band, and `diagnostic_scatter` overlays that band, shades the points
outside it, and returns the **violating mask** — so the figure doubles as a rule's evidence (feeds
pattern J). Packaged `TEMPLATES`: `sat_reset`, `chw_reset` (reset schedules vs OAT — clamped at the
endpoints), `economizer` (OA damper open for free cooling, minimum when hot), `no_simultaneous_hc`
(heating valve must be ~0 when cooling is active). Build your own with `band`, `reset_line`,
`economizer_template`, `no_simultaneous_template`. Flags: `shade`, `tolerance`.

### J — rules as a chart engine (the keystone)
Every rule that can mark its violating timestamps **renders its own evidence** — the chart *is* the
audit evidence and the report figure. A rule opts in with an optional, duck-typed hook:
```python
class SimultaneousHeatCool:
    def evidence(self, equip, frame):  # optional; rules without it are unaffected
        from camber.charts.diagnostic import TEMPLATES
        from camber.charts.evidence import Evidence

        return Evidence(renderer="diagnostic", template=TEMPLATES["no_simultaneous_hc"])
```
An `Evidence` names a **renderer** (`diagnostic` / `multitrend` / `oat_scatter` / `carpet`) and the
roles / mask / template it needs; `render_evidence(evidence, frame, ax=…)` dispatches to that
pattern primitive. `finding_evidence(rule, equip, frame)` calls the hook safely (returns `None` when
absent or declined), and `evidence_descriptor(evidence)` is the JSON-friendly payload (renderer +
roles + violating timestamps) for export/linking. `Finding` carries an optional `evidence` field.
**Every rule renders evidence.** Rules with a *tailored* hook map to the fitting renderer and shade
the specific violation: `simultaneous_heat_cool` & `outdoor_air_fraction` (diagnostic),
`supply_air_reset` (diagnostic reset), `reheat_penalty` (OAT scatter — heating in warm weather),
`night_weekend_setback` (carpet — the fault *is* the schedule), `overcooling_min_flow` /
`unmet_setpoint_hours` / `supply_air_control` / `airflow_tracking` (multitrend, violating spans
shaded). Every *other* rule falls back to a **default** multitrend of the roles it examined — so the
whole library (present and future rules) carries evidence, no per-rule map required. Fleet findings
(no single equipment frame) render none.

The dashboard wires it automatically — pass `rules=`:
```python
html = build_dashboard(df, findings=findings, rules=registry)  # evidence=True by default
```
Each actionable finding whose rule opts in renders its evidence chart under an **Evidence** section.
The **Std-211 audit report** does the same — `AuditReport.to_html(rules=…, frames={equip: frame})`
embeds each finding's evidence beneath the findings table (per-equipment frames, so a fleet audit
renders the right trend for each unit).

### C — peer/cohort comparison + cohort-deviation rule
```python
from camber.charts.cohort import cohort_small_multiples, cohort_deviation

fig, res = cohort_small_multiples(frames, Role.AIRFLOW)  # frames: {equip: role-frame}
res.outliers  # units > k robust-σ from the cohort norm
```
Small multiples of a role across a **cohort** of like equipment, ordered by deviation (worst first),
outliers in red. Deviation is a robust z-score (median/MAD) of a per-unit `summary` (`mean` / `peak`
/ `load_factor`), so a couple of odd units don't move the reference. The same score powers a FDD
rule — `camber.rules.cohort.CohortDeviation(role, k=…, summary=…)` is a fleet rule that flags "this
unit runs unlike its peers", a signal no per-unit absolute-bound rule can see. Flags: `k`, `summary`,
`min_cohort`, `rank`, `ncols`, `max_units`.

### H — M&V baseline, savings & uncertainty
```python
from camber.charts.savings import savings_chart

ax, res = savings_chart(
    baseline_model, t_report, y_report, n_baseline=200, p_baseline=2, cv_rmse=0.08
)
res.avoided_energy, res.abs_uncertainty  # e.g. 2983 ± 1318 at 90%
```
The IPMVP Option-C picture: cumulative **baseline-projected vs actual** energy, the avoided energy
shaded between them (green = saved, red = excess), and the **ASHRAE G14 Annex-B fractional savings
uncertainty** carried as a ± band on the running total — savings *and* how sure we are of them. Fit
quality (CV(RMSE)) annotates the baseline's credibility. `cumulative_savings(...)` returns the raw
`(index, cum_baseline, cum_actual, cum_avoided)` arrays. Reuses `mandv.stats.avoided_energy_savings`
and any `predict()`-able baseline (`mandv.models.best_model`). Flags: `confidence`, `rho`
(autocorrelation), `ylabel`.

### F — load profiles & load-duration curves
```python
from camber.charts.loadprofile_chart import load_profile_chart, load_duration_chart

load_profile_chart(load_kw, split=True)  # weekday vs weekend hour-of-day shape
load_duration_chart(load_kw, price=0.15)  # LDC + energy-cost translation
```
Two views on load shape. `load_profile_chart` plots the average load by hour-of-day (weekday vs
weekend when `split`, exposing schedule gaps) with the base load annotated. `load_duration_chart`
sorts every interval high-to-low against the % of time it's exceeded — the area is energy, the left
edge the peak, the right shoulder base load — and with a `price` ($/kWh) adds an energy-cost figure.
Both return `(ax, LoadMetrics)` and reuse `camber.loadprofile`. Flags: `split`, `annotate`, `price`.

## The HTML dashboard

`camber.report.build_dashboard` assembles the sections + the ranked findings into **one
self-contained HTML page** — matplotlib figures inlined as base64 PNG, no web framework, no
external assets.

```python
from camber.report import build_dashboard

html = build_dashboard(
    df,
    findings=findings,
    spans={"high_load": df["load_kw"] > 95},
    carpet_col="load_kw",
    rank_by="cost",
    title="Site dashboard",
)
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
| `rules` | `None` | Registry / {name: rule} / rules — enables per-finding evidence (pattern J) |
| `evidence` | `True` | render each actionable finding's evidence chart when `rules` is supplied |
| `interactive` | `False` | add a brush-able inline-SVG scatter (vanilla JS, no framework) |
| `link_x` / `link_y` | auto | the scatter's axes (default: an OAT-like x, the first other column) |

### Interactive linking (brush → select)
```python
html = build_dashboard(df, interactive=True)  # link_x defaults to an OAT-like column
```
With `interactive=True` the dashboard adds a **brush-able** scatter drawn by a small **vanilla-JS**
module (no framework, no CDN, CSP-safe) from an inline JSON payload. Drag a box over the cloud and the
selected points highlight while a linked readout lists their **timestamps** — the pattern-D brush-back
made live.

**Cross-panel linking (0.8).** The brush no longer stops at the scatter: a shared `window.CAMBER`
selection bus (a Set of selected timestamp strings) lets the selection **propagate across every view**.
Panels **B (fault multitrend)** and **E (load carpet)** are promoted from static PNG to **inline SVG**
that subscribe to the bus — brushing a cluster in the scatter shades the corresponding **time ranges**
in the multitrend and highlights the matching **hour × date cells** in the carpet. Every panel keys off
the same `str(timestamp)` from the frame index, so they interoperate without sharing a coordinate
system; panels A and I stay PNG (aggregate matrices, not timestamp-indexed). Still a single
self-contained CSP-safe file. `selection_bus_html()`, `carpet_svg_html(series, …)`, and
`multitrend_svg_html(df, cols, spans=…)` build the pieces; `interactive_scatter_html(...)` the scatter.

## Scope

This is the dependency-light MVP: rich, self-contained HTML rather than a live web UI. The fuller
interactive vision (brush-linked views, agent narration, continuous refresh) is in the
[ROADMAP](../ROADMAP.md) Visualizations section.
