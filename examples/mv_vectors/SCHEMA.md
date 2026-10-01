# Shared M&V test vectors: schema, method and tolerances

This file defines every input column, every field of `expected.json`, the results format that
`check_vectors.py` reads, and the tolerances it applies. [README.md](README.md) says what the
vectors are for and how to use them.

Conventions used throughout:

- Temperatures are in °F.
- Energy is in the case's `unit`: kWh for electricity and the synthetic cases, kBtu for chilled
  water and steam, therms for natural gas.
- Percentages are percent (`cvrmse_pct: 4.07` means 4.07 %). `savings_fraction` and `fsu` are
  fractions.
- Residuals are **measured minus predicted**. A positive NMBE means the model under-predicts.
- Numbers in `expected.json` carry 7 significant figures.
- Dates are **local calendar days** (`YYYY-MM-DD`), with no time zone and no time of day. In
  Parquet they are `date32`.
- Every CSV has a header row, uses `,` as the separator and `.` as the decimal point, and is
  UTF-8. `export_parquet.py` writes the same columns as Parquet.
- The schema id is **`mv_vectors/1`**. Any change that breaks a field's meaning increments it.

## Inputs

**Where the files are.** Paths in `expected.json` are relative to one of two folders, chosen by
each case's `data` field:

- **`data: "repo"`** (the synthetic tier): this folder.
- **`data: "local"`** (the BDG2 tier): the folder `fetch_bdg2.py` writes, by default `local/`.
  These files are never committed, because CAMBER redistributes no datasets.
  - `fetch_bdg2.py` downloads the publisher's files from the URLs of CAMBER's `bdg2` catalog
    entry and checks them against its sha256 pins (`SOURCES`).
  - It rebuilds every derived CSV with the code in the script, and checks each one against its
    pinned sha256 (`DERIVED_SHA256`).
  - A mismatch stops it with a non-zero exit.

### Daily tables: `inputs/synthetic/*_daily.csv`, `inputs/bdg2/*_daily.csv`

| Column | Meaning |
|---|---|
| `date` | the day, `YYYY-MM-DD` (local calendar day) |
| `period` | `baseline` or `reporting` |
| `oat_f` | the day's mean outdoor air temperature |
| `energy` | the day's energy |

- **Synthetic:** 2021 is the baseline year and 2022 the reporting year, with 365 days each.
- **BDG2:** 2016 is the baseline year and 2017 the reporting year. A day appears only when the
  meter has all 24 hourly readings and the site's weather station at least 20 hourly air
  temperatures. `oat_f` is the mean of those readings, converted from °C.

### Period tables: `*_monthly.csv` and `*_bills.csv`

These are built from the daily CSV, so anyone can re-derive them.

| Column | Meaning |
|---|---|
| `start`, `end` | first and last day of the period, **both inclusive** |
| `days` | days in the period, `end - start + 1` |
| `days_observed` | days of the period present in the daily CSV |
| `period` | `baseline` or `reporting` |
| `oat_f` | mean of the observed days' `oat_f` |
| `energy` | the period's energy: `sum(observed daily energy) x days / days_observed` |

- A period is kept only when `days_observed >= 0.9 x days`.
- `*_monthly.csv` uses calendar months.
- `*_bills.csv` (one case) uses seeded irregular periods of 27–35 days. A short tail joins the
  last bill of its year, and no period crosses a year boundary.

### Bill cases: `inputs/bills/*_bills.csv` and `*_oat.csv`

| Column | Meaning |
|---|---|
| `start`, `end` | the service period as printed on a bill: first and last day, both inclusive |
| `days`, `days_observed` | as above; the energy is scaled the same way |
| `period` | `baseline` or `reporting` |
| `estimated` | `True` for an estimated read |
| `energy` | the bill's energy as billed |
| `energy_injected` | the same, with reporting bills x 0.90 (rounded to 0.01) |
| `cost`, `cost_injected` | `energy x rate` and `energy_injected x rate`, at a **synthetic** seeded rate per bill |

The bills also have these properties:

- **Read periods** are 28–35 days and start mid-month (the 14th or 15th).
- **Estimated read:** bill 5 is the utility's guess, taken as the previous bill's rate of use times
  its days. The next bill carries the true-up, so the two together hold the metered energy.
- **Gap:** the seventh reporting bill is missing.
- **Costs:** BDG2 publishes no costs. The rate is a seeded draw around $0.13 / kWh (synthetic),
  $0.12 / kWh (electricity) or $0.025 / kBtu (steam), so it differs bill to bill.

`*_oat.csv` holds the daily outdoor temperature (`date`, `oat_f`) that the bills are paired with.

`inputs/bills/calendarize_overlap.csv` is a plain table, not a set of contiguous bills: `start`,
inclusive `end`, `energy`, `estimated` and `cost`. It has one 7-day overlap, one 4-day gap and one
estimated read.

### CAMBER's predicted series: `predictions/*.csv`

There is one file per fit, so a twin can compare its predictions row by row. Each file covers
every row of its input, in both periods.

- **Synthetic tier:** the series are committed.
- **BDG2 tier:** the series are written locally. `generate.py` writes them from the fitted models;
  `fetch_bdg2.py --predictions` rebuilds them from the coefficients in `expected.json` (7
  significant figures). Where neither has run, `check_vectors.py` rebuilds a fit's series from
  the coefficients itself.

| Fit | Columns |
|---|---|
| daily | `date`, `period`, `predicted` (energy) |
| period table | `start`, `end`, `period`, `predicted_per_day`, `predicted` (the period total) |
| bill case | `start`, `end` (inclusive), `days`, `period`, `predicted_per_day`, `predicted` |

The values come from the fitted model at full precision. Rebuilding them from the
7-significant-figure coefficients in `expected.json` agrees to about 1e-6 of the mean.

## `expected.json`

### Top level

| Field | Meaning |
|---|---|
| `schema` | `"mv_vectors/1"` |
| `generator.camber_version` | `0.98.0-dev at <commit>`: the commit the generator ran on |
| `method` | the method notes repeated below, as text |
| `gates` | the three gates' definitions |
| `local_data` | where the BDG2 tier comes from: the fetch script, its default folder, licence and citation |
| `cases` | the change-point cases: one entry per case, with a `fits` object per interval |
| `bills` | the bill cases (`bills.cases`) and the overlap calendarization (`bills.calendarization`) |

### `cases[]`

| Field | Meaning |
|---|---|
| `id`, `data` (`repo` / `local`), `source` (`synthetic` / `bdg2`), `fuel`, `unit`, `note` | identification; `data` says which folder its paths are relative to |
| `truth` (synthetic) | `kind`, `base`, `slopes_dEdT` and `change_points` of the generating model, in the convention-free form below; also `noise_sd_fraction_of_mean`, `seed` and `recovery_expected` |
| `bdg2` (real) | `building_id` and `meter` in BDG2 |
| `fits.daily`, `fits.monthly`, `fits.bills` | one block per input table (below) |

### `cases[].fits.<interval>`

| Field | Meaning |
|---|---|
| `input` | the CSV, relative to this folder |
| `n_baseline`, `n_reporting` | rows in each period |
| `coef_tolerance_floor` | the absolute floors of the coefficient tolerance: `base` is 0.5 % of mean y, and `slope` is that over the 5th–95th percentile OAT span. They are stored so that no input file is needed to compare coefficients |
| `y`, `weights` | what was fitted: `energy` (daily), or `energy / days` with weights `days` (monthly, bills) |
| `selection.best`, `.runner_up`, `.bic_gap` | CAMBER's choice, the next best kind, and the BIC difference between them |
| `selection.candidates[]` | for every kind: `n_params`, `sse` (weighted for period tables), `bic`, `change_points` |
| `model.kind`, `.n_params`, `.change_points` | the selected model |
| `model.base`, `.slopes_dEdT` | the selected model in the convention-free form below |
| `model.coeffs_camber` | CAMBER's own coefficient names (`heat_slope` is positive for a heating arm) |
| `model.fell_back_to_line` | true when a 5P search found no dead-band and fitted a line |
| `stats` | `n`, `n_params`, `r2`, `adj_r2`, `cvrmse_pct`, `nmbe_pct`, `rmse`, `f_stat`, `rho_lag1` (null when not estimable) |
| `stats_totals_unweighted` (period tables) | the same statistics on period **totals**, unweighted, from the same model: what a monthly scorer fed with totals computes |
| `baseline_gate` | `pass_` plus its thresholds and `notes` |
| `calsim_gate` | `pass_`, `tolerance_row` (`monthly` or `hourly`), thresholds, `notes` |
| `gates_disagree` | `baseline_gate.pass_ != calsim_gate.pass_` |
| `sep_validity` | `valid`, `failures`, `valid_rho_adjusted`, `f_p`, `slope_p` |
| `option_c.raw`, `option_c.injected` | `savings_total` (avoided energy), `baseline_projected`, `reporting_actual`, `savings_fraction`, `fsu`, `abs_uncertainty`, `rho`, `n_report`, `coverage_tier`, `declined`, `declined_reason` |
| `option_c.injected_saving_exact` | `0.10 x` the raw reporting total. Up to rounding, `injected.savings_total - raw.savings_total` must equal it for **any** model |
| `predictions` | the CSV of CAMBER's predicted series for this table (below) |
| `option_c.baseline_kind`, `.confidence`, `.kernel` | `baseline_kind` mirrors open-fdd's `option_c_savings` key |
| `truth_recovery` (synthetic daily) | `kind`, `change_points` (±2 °F) and `coefficients` (±5 %): did CAMBER recover the truth? |

### The convention-free model form

Change-point coefficients are named differently in every tool, and the sign of the heating slope
differs too. The vectors therefore also state each model as `base`, `change_points` and
`slopes_dEdT`. The slopes are the signed **dE/dT** of each segment, left to right:

| Kind | `change_points` | `slopes_dEdT` | `base` is | Prediction |
|---|---|---|---|---|
| 2P | `[]` | `[s]` | the intercept at 0 °F | `base + s T` |
| 3PH | `[Tb]` | `[sL, 0]`, `sL < 0` for heating | the flat level | `base + sL min(0, T - Tb)` |
| 3PC | `[Tb]` | `[0, sR]` | the flat level | `base + sR max(0, T - Tb)` |
| 4P | `[Tb]` | `[sL, sR]` | the value at `Tb` | `base + sL min(0, T - Tb) + sR max(0, T - Tb)` |
| 5P | `[Tlo, Thi]` | `[sL, 0, sR]` | the dead-band level | `base + sL min(0, T - Tlo) + sR max(0, T - Thi)` |

`check_vectors.predict` implements this table. CAMBER's own `heat_slope` is `-sL`.

### `bills.cases[]`

These come from CAMBER's config path for bills: an `mv` entry with `bills`, `base_f: "auto"`,
`calendarize: true`, `avoided_cost: "bills"`, `method: "forecast"`, `kernel: "g14"` and
`validity: "both"`. `config_entry` records the entry, including its `period` and
`reporting_period`. The path runs twice, once on `energy` / `cost` and once on
`energy_injected` / `cost_injected`.

| Field | Meaning |
|---|---|
| `estimated_reads` | `merged`, `dropped`, and the merged `spans` (first and last day, `n_estimated`) |
| `baseline` | `n_bills`, `n_days`, `mean_bill_days`, `bills_dropped`, `short_baseline`, `r2`, `adj_r2`, `cvrmse_pct`, `nmbe_pct`, `n_params`, `rho`, `hdd_total`, `cdd_total` |
| `baseline.model` | `family` (`degree_day` or `change_point`) and the selected model's coefficients |
| `base_selection` | the `base_f: "auto"` search: `kind` (DD-H / DD-C / DD-HC), `heating_base_f`, `cooling_base_f`, `heating_range`, `cooling_range`, `flat`, `at_edge`, `grid`, `candidates`, and `profiles` (per candidate base: `sse`, `bic`, `r2`) |
| `degree_days` | per baseline bill, at the selected bases: `hdd_from_daily` / `cdd_from_daily` (built from each day's OAT) and `hdd_from_mean` / `cdd_from_mean` (from the bill's mean OAT); their `totals`; and `degree_day_model_on_baseline.from_daily` / `.from_mean`, the selected degree-day kind refitted on each set |
| `model_comparison.rows[]` | every change-point kind, the degree-day kind at the fitted bases, and the same kind at a fixed 65 °F: `n_params`, `r2`, `adj_r2`, `cvrmse_pct`, `nmbe_pct`, `bic`, `baseline_gate`, `selected` |
| `model_comparison.bic_gap` | the BIC gap between the two best **candidates**. The fixed-65 °F row is a reference with `p` two lower, not a candidate |
| `baseline_gate`, `calsim_gate`, `gates_disagree`, `sep_validity` | as for the change-point cases, judged on the monthly row |
| `calendarized` | Portfolio Manager calendar months: `months[]` (`days_in_month`, `days_covered`, `complete`, `energy`, `cost`, `n_bills`, `estimated`, `oat`, `hdd`, `cdd`), `gaps`, `overlaps`, `declined`, `declined_reason`, `annual` |
| `unit_cost`, `billed_cost_baseline` | the implied cost per unit of the baseline bills (blended, min, median, max) |
| `option_c.raw` / `.injected` | as above, plus `avoided_cost`, `avoided_cost_rate` (blended), `avoided_cost_uncertainty`, `avoided_cost_basis`, `n_report_bills`, `n_report_days`, `sep_valid` and `sep_failures` |
| `option_c.injected_saving_exact` | `sum(energy - energy_injected)` over the reporting bills |
| `truth` (synthetic) | the generating daily degree-day model |

`bills.cases[].data` works as for the change-point cases. `bills.cases[].predictions` is the CSV
of CAMBER's predicted series for the bills it fitted or projected. Estimated reads are already
merged there, so it has one row fewer than the bills file.

`bills.calendarization` is the overlap table calendarized twice, as `max_gap_days_0` and
`max_gap_days_10`, with the same fields as `calendarized`.

## Method: how CAMBER computes these numbers

The method details below explain most of the differences you are likely to see.

### Change points

- **Search.** The grid has 40 evenly spaced candidates from the 5th to the 95th percentile of the
  baseline OAT (`numpy.percentile` with linear interpolation, then `numpy.linspace`).
- **Fit.** At each candidate the intercept and slopes are solved by least squares, and the
  candidate with the lowest SSE wins.
- **Ties.** The SSE surface can be flat: several grid points fit equally well when no data lies
  between them, or when a 4P or 5P model has no second regime to find (common for the
  candidates CAMBER does not select, and on monthly data). Their SSEs then differ only by
  rounding, which depends on the BLAS build. CAMBER keeps the **first** grid point (the lowest
  change point, and for 5P the lowest `lo`, then the lowest `hi`) unless a later one is lower by
  more than 1e-10 of the SSE, so the result is the same on every platform. Another tool may
  break the same tie differently. That is why the change points of non-selected candidates are
  compared within the change-point tolerance below, and never more tightly.
- **5P.** The search tries every pair `(lo, hi)` on the same grid with `hi - lo` of at least one
  grid step, so the dead-band is never zero-width. When no pair qualifies, the model falls back
  to a line that keeps the label 5P.
- **Consequence.** A grid over the full range, or a finer one, moves change points by up to a grid
  step (about 1.5–2 °F on a year of data) and can flip close kind decisions.

### Selection

- CAMBER fits 2P, 3PC, 3PH, 4P and 5P, and keeps the lowest **BIC = n ln(SSE/n + 1e-12) + p ln(n)**.
- A tie keeps the earlier kind in that order (BICs within 1e-9 count as a tie).
- For period tables, the SSE is day-weighted.
- R² and adjusted R² are reported, but they are not used to choose.

### How p is counted

`p` counts the least-squares coefficients **plus every grid-searched change point or fitted
degree-day base**:

| Model | p |
|---|---|
| 2P | 2 |
| 3PC, 3PH | 3 |
| 4P | 4 |
| 5P | 5 |
| DD-H, DD-C | 3 |
| DD-HC | 5 |
| Degree-day model at a fixed base | 2 per leg, plus 1 |

The same `p` goes into the BIC, the `n - p` of CV(RMSE) and NMBE, adjusted R² and the F-test.
Counting only the regression coefficients, as some tools do, gives a smaller `p`. That raises
`n - p`, which slightly lowers CV(RMSE) and changes adjusted R².

### Statistics

- R² = 1 − SSE/SST
- adjusted R² = 1 − (1 − R²)(n − 1)/(n − p)
- CV(RMSE) = √(SSE/(n − p)) / ȳ
- NMBE = Σ(y − ŷ) / ((n − p) ȳ)

CAMBER rounds R² and CV(RMSE) to 4 decimals and NMBE to 5, as fractions, before applying the
gates.

**In-sample NMBE is zero.** For a least-squares fit with an intercept, scored on its own baseline,
the residuals sum to zero. Every NMBE in these vectors is therefore 0 to rounding, and neither
NMBE limit decides a verdict here. The NMBE limits matter for a model scored on other data, or for
a fit that is not least squares with an intercept.

### Day weighting (monthly, bills and the bill cases)

- **What is fitted.** A period's energy per day, `energy / days`, is the mean of `days` daily
  values, so its variance falls as `1/days`. CAMBER fits it by weighted least squares with
  weight = `days`. The weights are normalised to mean 1, and the change-point search is weighted
  too.
- **Weighted statistics.** SSE and SST are weighted, ȳ is the weighted mean (total energy over
  total days), and NMBE uses the weighted residual sum. `n` is the number of periods.
- **Totals.** The model predicts energy per day, and a period's total is `days x` that.
- **Scoring totals instead.** A tool that fits or scores monthly **totals**, unweighted, gets a
  different R² and CV(RMSE). `stats_totals_unweighted` gives those numbers for CAMBER's model, so
  a G14 scorer can be checked on its own.

### Bills: estimated reads, per-day normalisation and the base temperature

- **Estimated reads.** Each run of estimated bills is merged into the next contiguous actual bill,
  as one period with the summed energy and cost. An estimate that no actual read follows is
  dropped. The merged bill is what gets fitted, and calendarization flags the months it touches as
  estimated.
- **Per-day energy and weather.**
  - Each bill becomes `energy / days`, paired with the mean daily OAT of its own service days.
  - A bill is dropped when fewer than 90 % of its days have a temperature.
  - The bill's degree days are the mean, over its days, of each day's
    `max(0, base - T_day)` (HDD) or `max(0, T_day - base)` (CDD). They are **not** computed from
    the bill's mean temperature. `degree_days` shows what that choice changes.
- **Base temperature** (`base_f: "auto"`, CAMBER 0.94).
  - Three degree-day kinds are searched:
    - DD-H: `E/day = b + a HDD/day`;
    - DD-C: `E/day = b + c CDD/day`;
    - DD-HC: both legs, with the heating base no higher than the cooling base.
  - Each kind is searched on a 1 °F grid, from 40 to 70 °F for heating and from 50 to 80 °F for
    cooling. The fit is weighted least squares.
  - A base with a negative slope is refused.
  - Within a kind, the bases with the least weighted SSE win.
  - Across kinds, the lowest BIC (with `p` as above) wins.
  - A base's **range** is the set of bases with `n ln(SSE/SSE_min) <= 3.84`, the 95 %
    profile-likelihood interval.
  - The degree-day model at the selected bases then competes with the five change-point kinds by
    BIC, and the lowest BIC is the baseline. A change-point model can win, as it does for both BDG2
    bill cases.
- **Calendarization.**
  - Each bill's energy and cost per day are spread over its days, and each day goes to its
    calendar month: the ENERGY STAR Portfolio Manager method (Technical Reference, *Thermal Energy
    Conversions*, Figure 1, step 3).
  - A month is `complete` only when every one of its days is served by exactly one bill.
  - A gap or overlap longer than `max_gap_days` (0 here) sets `declined_reason`, and the annual
    totals are withheld.
  - The months are an output view and are never fitted.
- **Avoided cost.**
  - For each reporting bill, `(projected - actual energy) x its own rate`, where the rate is that
    bill's `cost / energy`.
  - `avoided_cost_rate` is the blended rate.
  - `avoided_cost_uncertainty` is the energy band times the blended rate.

### Option C savings and FSU

- **Avoided energy** = Σ projected baseline − Σ actual, over the reporting rows (bills: × days).
- **FSU** is the ASHRAE Guideline 14 Annex B form at 90 % confidence:
  `t x 1.26 x CV x sqrt((n/n') (1 + 2/n) / m) / F`.
  - `t` is from CAMBER's table on `n - p` degrees of freedom, rounded down to a table row.
  - `n' = n (1 - rho)/(1 + rho)`, where rho is the lag-1 autocorrelation of the baseline
    residuals. It needs at least 30 adjacent pairs; otherwise rho = 0.
  - Monthly and bill baselines never have 30 pairs, so their FSU is unadjusted.
- **Coverage tier.** It grades how well the baseline's OAT range covers the reporting period. A
  `severe` tier declines the saving.

### The three gates

| Gate | NMBE | CV(RMSE) | R² | Where it comes from |
|---|---|---|---|---|
| **Baseline** (CAMBER's regression-baseline acceptance) | \|NMBE\| ≤ 0.5 % | ≤ 15 % monthly and bills, ≤ 30 % daily (`camber.mandv.stats.cv_rmse_max_for`) | ≥ 0.75 | `fit_stats(...).accept` |
| **Calibrated simulation** (the tolerances open-fdd's `score_g14_monthly` applies) | ≤ 5 % monthly, ≤ 10 % hourly | ≤ 15 % monthly, ≤ 30 % hourly | none | computed here from the same statistics |
| **SEP validity** (SEP 50001 M&V Protocol 2019 Ed. 2, §6.4.1) | none | none | ≥ 0.50 | F-test p < 0.10, every slope p < 0.20 and one < 0.10, physical slope signs |

- **Which calibrated-simulation row.** Guideline 14 states calibrated-simulation tolerances for
  monthly and hourly data only. Daily fits are judged on the **hourly** row, the looser of the
  two, and `tolerance_row` says which row was used.
- **SEP.** The SEP verdict is computed as written, assuming independent residuals.
  `valid_rho_adjusted` repeats it with autocorrelation accounted for, where rho is known.

## Results format (what `check_vectors.py` reads)

```json
{
 "schema": "mv_vectors/1",
 "implementation": "open-fdd 4.x.y",
 "results": {
  "syn_3ph_58": {
   "daily": {
    "kind": "3PH",
    "n_params": 3,
    "change_points": [58.3],
    "base": 400.2,
    "slopes_dEdT": [-14.6, 0.0],
    "r2": 0.979,
    "cvrmse_pct": 4.06,
    "nmbe_pct": 0.0,
    "pass_calsim": true,
    "pass_baseline": true,
    "predictions": [400.1, 512.3],
    "savings_total": {"raw": 1234.5, "injected": 14980.2}
   },
   "monthly": {"kind": "3PH", "y": "totals", "predictions": [9500.0, 8800.0]}
  }
 },
 "bills": {
  "bills_syn_dd_hc_55_68": {
   "kind": "DD-HC",
   "heating_base_f": 54.0,
   "cooling_base_f": 70.0,
   "r2": 0.999,
   "cvrmse_pct": 0.7,
   "nmbe_pct": 0.0,
   "savings_total": {"raw": 600.0, "injected": 13700.0},
   "avoided_cost": {"raw": 75.0, "injected": 1750.0},
   "calendarized": {"2021-01": 15700.1}
  }
 }
}
```

Every field except `kind` is optional, and the checker compares only what is present. `schema`
should be `"mv_vectors/1"`; any other value is a FAIL. `example_results.json` is a complete small
example.

- **`predictions`:** one value per **baseline** row, in the order of the input CSV.
  - For daily tables, give energy.
  - For period tables, give the period **total** in the CSV's energy unit. With `"y": "totals"`
    the statistics are compared with `stats_totals_unweighted`; otherwise they are taken per day,
    weighted by days, and compared with `stats`.
  - When predictions are given, the checker recomputes R², CV(RMSE) and NMBE from them using
    `n_params`. It requires your reported statistics to match, then compares the recomputed values
    with the expected ones.
  - The checker also compares predictions row by row with `predictions/`. The result is
    **informational**: a PASS within 2 % of the mean, otherwise a NOTE. Predictions from a
    different kind or grid legitimately differ.
- **Coefficients** use the convention-free form. For period tables they are per day: a fit of
  monthly totals has slopes about 30 times larger. With `"y": "totals"`, `base` and
  `slopes_dEdT` are not compared.
- **Savings** are period totals in the case's unit.
- **Bill degree-day kinds** use CAMBER's names (`DD-H`, `DD-C`, `DD-HC`). Change-point kinds
  use `2P`, `3PC`, `3PH`, `4P` and `5P`.

`python check_vectors.py --template` prints CAMBER's own outputs in this format. Comparing that
file with the vectors passes, so it is a working example.

## Tolerances

| Quantity | Tolerance | Why |
|---|---|---|
| change point, degree-day base | ±2 °F | grid differences: CAMBER's grid step is about 1.5–2 °F, and the base grid is 1 °F |
| `base`, each slope | ±5 % of the expected value, or ±0.5 % of mean y (base) / ±0.5 % of mean y over the 5th–95th percentile OAT span (slopes), whichever is larger | relative error, with a floor for slopes near zero |
| R² | ±0.01 absolute | |
| CV(RMSE) | ±0.5 percentage points | |
| NMBE | ±0.1 percentage points | |
| Option C savings | ±1 % of the expected baseline projection | |
| avoided cost (bills) | ±1 % of the projection's cost at the blended rate | |
| calendarized month energy | ±0.1 % | proration is exact arithmetic |
| gate verdicts | exact | a value right at a threshold can flip; look at the statistics first |
| self-consistency | 0.1 % | your reported statistics vs those of your own `predictions` |
| predictions, row by row | max deviation ≤ 2 % of the mean: PASS, otherwise NOTE | informational, never a failure |

**Kind disagreements.**
- **NOTE:** your kind's BIC, in CAMBER's own candidate list, is within **2** of CAMBER's best.
  That is a legitimate disagreement, and the change points and coefficients are then not compared
  (they are not comparable across kinds).
- **FAIL:** a kind CAMBER rates more than 2 BIC worse.

The BIC gap to the runner-up is listed for every fit. Below about 2, the two models fit about
equally well, and a different grid, objective or parameter count can reasonably tip the choice.
Many monthly fits sit below 2.
