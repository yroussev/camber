# Shared M&V test vectors

These vectors let two independent M&V implementations check each other, through files only:

- **inputs**: CSV, with optional Parquet copies;
- **expected outputs**: JSON;
- **a results contract**: JSON that any engine can write.

They cover change-point models (2P, 3PC, 3PH, 4P and 5P), ASHRAE Guideline 14 statistics and
gates, and IPMVP Option C savings with fractional savings uncertainty. They also cover CAMBER's
bill-based M&V: per-day fits weighted by days, degree-day bases chosen from the bills, estimated
reads, ENERGY STAR Portfolio Manager calendarization and avoided cost.

CAMBER produced the expected outputs. Nothing here needs CAMBER to *consume* the vectors:

- `check_vectors.py` needs numpy and pandas;
- `fetch_bdg2.py` needs numpy, pandas and the standard library;
- `export_parquet.py` needs pandas and pyarrow.

The
checker never imports or calls the implementation under test, so that implementation can be a
pandas library, a DataFusion SQL twin or anything else. It only has to write a results file.

The vectors were built so that CAMBER and
[open-fdd](https://bbartling.github.io/open-fdd/ecm/ipmvp-changepoint.html)'s change-point and G14
helpers can be cross-checked without either codebase importing the other: the files-and-processes
boundary of [CAMBER issue #22](https://github.com/yroussev/camber/issues/22). Function and field
names follow open-fdd's where they can: `kind`, `n_params`, `cvrmse_pct`, `nmbe_pct`, `pass_`,
`savings_total` and `baseline_kind`.

## Two tiers: synthetic in the repository, BDG2 rebuilt locally

**CAMBER redistributes no datasets**, and these vectors keep that promise. So the data comes in
two tiers:

- **Synthetic tier, in the repository.** 8 seeded change-point cases with known truth, 1
  synthetic bill case and an overlap table for calendarization. Their inputs, predicted series
  and expected outputs are all committed.
- **BDG2 tier, rebuilt on your machine.** 6 real meters from Building Data Genome 2 and 2 bill
  cases built from them. Their expected **statistics** are committed in `expected.json`: kinds,
  change points, coefficients, fit statistics, verdicts and savings. Those are facts about the
  data, not a copy of it.
  - The **inputs are not committed**. `fetch_bdg2.py` downloads the publisher's own files, using
    the URLs and sha256 pins of CAMBER's `bdg2` catalog entry, and checks each file's sha256. It
    rebuilds the daily, calendar-month and bill aggregates deterministically, and checks every
    derived CSV against its own pinned sha256. It writes them to `local/`, which is git-ignored.
  - The **predicted series are not committed** either. `fetch_bdg2.py --predictions` rebuilds
    them from the coefficients in `expected.json`. `generate.py`, with CAMBER installed, writes
    them from the fitted models.

```sh
python fetch_bdg2.py                 # download (about 330 MB) to a cache, build into ./local
python fetch_bdg2.py --predictions   # also write CAMBER's BDG2 predicted series
python fetch_bdg2.py --source DIR    # use BDG2 files you already have (still sha256-checked)
```

The script prints the BDG2 citation and licence. The cache defaults to
`~/.cache/mv_vectors/bdg2` (or `$XDG_CACHE_HOME`). Without `local/`, `check_vectors.py` still
checks every synthetic case and every summary result for the BDG2 cases. It skips only what needs
the BDG2 inputs, and says so.

## What is here

| Path | What |
|---|---|
| `inputs/synthetic/` | the 8 synthetic change-point cases: daily, plus calendar-month tables built from them |
| `inputs/bills/` | the synthetic bill case (bills plus daily OAT) and an overlapping table for calendarization |
| `predictions/` | CAMBER's predicted series for the synthetic cases, row by row, both periods |
| `expected.json` | CAMBER's outputs for **every** case, BDG2 included: schema `mv_vectors/1`, documented in [SCHEMA.md](SCHEMA.md) |
| `example_results.json` | a tiny results file in the contract's shape, which passes with one NOTE |
| `check_vectors.py` | the checker (numpy and pandas only) |
| `fetch_bdg2.py` | rebuilds the BDG2 tier into `local/` (numpy, pandas and the standard library) |
| `export_parquet.py` | writes every CSV as Parquet with typed columns (pandas and pyarrow) |
| `generate.py`, `gen_bills.py` | regenerate everything with CAMBER (deterministic) |
| `local/` (not committed) | `inputs/bdg2/`, `inputs/bills/bills_bdg2_*` and `predictions/` of the BDG2 tier |

Units are stated per case in `expected.json`:

- °F;
- kWh for electricity and the synthetic cases;
- kBtu for chilled water and steam;
- therms for gas.

**Time.** Every date is a **local calendar day**, with no time zone and no time of day. BDG2
publishes naive local timestamps, and its daily values here are local-day sums and means. The
synthetic cases have no location. In Parquet the dates are `date32`.

## Using the vectors from another implementation

1. **Read the inputs.** For the BDG2 tier, run `fetch_bdg2.py` first.
   - Each daily CSV is `date, period, oat_f, energy`.
   - Period tables add `start`, `end` (both inclusive) and `days`.
   - Engines that prefer Parquet can run `python export_parquet.py OUT_DIR` first.
2. **Fit.** Fit each table's `baseline` rows, project onto its `reporting` rows, and score with
   your Guideline 14 helpers. The "injected" reporting energy is `energy x 0.90`.
3. **Write a results JSON**, as in [SCHEMA.md](SCHEMA.md#results-format-what-check_vectorspy-reads):
   `{"schema": "mv_vectors/1", "implementation": ..., "results": {case: {interval: {...}}},
   "bills": {...}}`. Every field except `kind` is optional. Include `predictions` (the baseline
   rows' predicted values) whenever you can. The checker then recomputes R², CV(RMSE) and NMBE from
   them, independently of your scorer, and compares them row by row with CAMBER's series.
4. **Run the checker:**

   ```sh
   python check_vectors.py your_results.json          # every check
   python check_vectors.py r.json --local DIR         # BDG2 files somewhere other than ./local
   python check_vectors.py your_results.json --quiet  # only NOTE and FAIL lines
   python check_vectors.py example_results.json       # the worked example
   ```

### How a SQL engine would produce this

A SQL twin needs no Python to produce the results, only CSV or Parquet tables in and numbers
out. Register an input table and your predictions, join them on `date` (daily) or `start`
(period tables), and aggregate. The query below gives the Guideline 14 statistics with CAMBER's
conventions:

- residuals are measured minus predicted;
- `p` counts change points;
- the `n - p` divisor applies to CV(RMSE) and NMBE.

```sql
-- obs: an input CSV; twin: your predictions with columns (date, predicted)
WITH r AS (
  SELECT o.energy AS y, t.predicted AS yhat
  FROM obs o JOIN twin t ON o.date = t.date
  WHERE o.period = 'baseline'
),
s AS (
  SELECT COUNT(*) AS n, 3 AS p, AVG(y) AS ybar,
         SUM((y - yhat) * (y - yhat)) AS sse, SUM(y - yhat) AS rsum
  FROM r
),
t AS (SELECT SUM((r.y - s.ybar) * (r.y - s.ybar)) AS sst FROM r CROSS JOIN s)
SELECT n, p,
       1 - sse / sst                         AS r2,
       1 - (sse / sst) * (n - 1) / (n - p)   AS adj_r2,
       100 * SQRT(sse / (n - p)) / ybar      AS cvrmse_pct,
       100 * rsum / ((n - p) * ybar)         AS nmbe_pct
FROM s CROSS JOIN t;
```

**Monthly and bill tables, weighted by days.**

- Use `y = energy / days` and the weight `w = days / AVG(days)`.
- Weight every sum: `SUM(w * (y - yhat)^2)`, the weighted mean `SUM(w * y) / SUM(w)`, and the
  weighted residual sum.
- Scored on period **totals** without weights, the same model gives `stats_totals_unweighted`.

The predictions themselves can come from a `CASE` expression on the change points, such as
`base + sL * LEAST(0, oat_f - tb) + sR * GREATEST(0, oat_f - tb)` (the convention-free form in
[SCHEMA.md](SCHEMA.md#the-convention-free-model-form)). Write them out as a results file, or
compare them row by row with `predictions/`.

## Using them from CAMBER

```sh
python examples/mv_vectors/generate.py            # everything; BDG2 from examples/_data/bdg2
python examples/mv_vectors/generate.py --offline  # synthetic tier only; BDG2 entries kept
python examples/mv_vectors/check_vectors.py --self-test
```

- **What `generate.py` does with BDG2.** With the BDG2 files in `examples/_data/bdg2` (or
  `--source DIR`), it rebuilds the BDG2 tier into `local/` through `fetch_bdg2.py`'s own code,
  then refits it. Otherwise it keeps the committed BDG2 entries of `expected.json`. When an
  aggregation change is intended, refresh the derived-CSV pins in `fetch_bdg2.DERIVED_SHA256`.
- **Regression test.** `tests/test_mv_vectors.py` regenerates the synthetic tier offline. It
  requires byte-identical inputs and predicted series, and identical `expected.json` entries.
  - When `examples/_data/bdg2` holds the BDG2 files, it also rebuilds the BDG2 tier, checks every
    pin, and requires the BDG2 entries to regenerate exactly.
  - `-m network` does the same from a fresh download.

  Any change to CAMBER's change-point fit, selection, statistics, gates, SEP verdict, savings or
  billing path that moves a vector fails there. Regenerate only for an intended change, and
  record it in the CHANGELOG.
- **Self-test.** `--self-test` rebuilds every expected statistic, saving, degree day, calendar
  month and predicted value from the inputs and the stored coefficients, with numpy and pandas
  alone. It shows that `expected.json` is internally consistent.

## The cases

### Change-point cases

Each case has a daily fit and a calendar-month fit, weighted by days. The gas meter also has an
irregular-bill fit. Abbreviations in the table:

- "Raw saving": the reporting year as measured, with nothing done to it.
- "Injected saving": the same year with 10 % removed.
- "FSU": the injected saving's fractional savings uncertainty at 90 %.

| Case | Interval | n | Best | Runner-up | BIC gap | R² | CV(RMSE) % | Baseline gate | Cal-sim gate | SEP | Raw saving | Injected saving | FSU (90 %) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `syn_2p` | daily | 365 | 2P | 4P | 10.68 | 0.969 | 4.1 | pass | pass | pass | -0.3 % | 9.7 % | 0.05 |
| `syn_2p` | monthly | 12 | 2P | 4P | 2.05 | 0.999 | 0.6 | pass | pass | pass | -0.3 % | 9.7 % | 0.05 |
| `syn_3ph_58` | daily | 365 | 3PH | 4P | 5.43 | 0.979 | 4.1 | pass | pass | pass | 0.4 % | 10.3 % | 0.04 |
| `syn_3ph_58` | monthly | 12 | 4P | 3PH | 0.56 | 0.999 | 1.2 | pass | pass | pass | 0.4 % | 10.4 % | 0.08 |
| `syn_3pc_65` | daily | 365 | 3PC | 4P | 4.02 | 0.987 | 4.0 | pass | pass | pass | 0.0 % | 10.0 % | 0.04 |
| `syn_3pc_65` | monthly | 12 | 4P | 5P | 1.37 | 0.999 | 1.3 | pass | pass | pass | -0.3 % | 9.7 % | 0.10 |
| `syn_4p_60` | daily | 365 | 4P | 5P | 7.46 | 0.940 | 3.2 | pass | pass | pass | -0.4 % | 9.7 % | 0.04 |
| `syn_4p_60` | monthly | 12 | 5P | 4P | 7.61 | 0.996 | 0.8 | pass | pass | pass | -0.2 % | 9.8 % | 0.06 |
| `syn_5p_55_68` | daily | 365 | 5P | 4P | 192.91 | 0.968 | 4.1 | pass | pass | pass | -0.1 % | 10.0 % | 0.04 |
| `syn_5p_55_68` | monthly | 12 | 5P | 4P | 21.48 | 0.998 | 1.0 | pass | pass | pass | 0.1 % | 10.1 % | 0.07 |
| `syn_3pc_noise_pass` | daily | 365 | 3PC | 4P | 5.66 | 0.866 | 14.4 | pass | pass | pass | -1.1 % | 9.0 % | 0.17 |
| `syn_3pc_noise_pass` | monthly | 12 | 3PC | 4P | 2.05 | 0.996 | 2.4 | pass | pass | pass | -1.2 % | 8.9 % | 0.19 |
| `syn_3pc_noise_fail` | daily | 365 | 3PC | 4P | 5.78 | 0.429 | 42.2 | **fail** | **fail** | **fail** | 1.1 % | 10.9 % | 0.44 |
| `syn_3pc_noise_fail` | monthly | 12 | 3PC | 4P | 1.89 | 0.952 | 8.2 | pass | pass | pass | 0.7 % | 10.6 % | 0.55 |
| `syn_weak_weather` | daily | 365 | 2P | 3PH | 5.02 | 0.020 | 5.8 | **fail** | pass | **fail** | -0.4 % | 9.6 % | 0.07 |
| `syn_weak_weather` | monthly | 12 | 2P | 3PC | 2.47 | 0.598 | 0.9 | **fail** | pass | pass | -0.4 % | 9.6 % | 0.07 |
| `bdg2_rat_public_leta_elec` | daily | 362 | 5P | 4P | 6.00 | 0.931 | 8.0 | pass | pass | pass | 1.5 % | 11.3 % | 0.11 |
| `bdg2_rat_public_leta_elec` | monthly | 12 | 3PC | 4P | 2.33 | 0.983 | 4.0 | pass | pass | pass | 2.2 % | 12.0 % | 0.24 |
| `bdg2_fox_lodging_stephen_chw` | daily | 365 | 3PC | 4P | 3.67 | 0.863 | 28.0 | pass | pass | pass | 0.6 % | 10.5 % | 1.09 |
| `bdg2_fox_lodging_stephen_chw` | monthly | 12 | 2P | 3PC | 0.87 | 0.909 | 23.9 | **fail** | **fail** | pass | 1.1 % | 11.0 % | 1.55 |
| `bdg2_hog_education_jewel_steam` | daily | 366 | 3PH | 4P | 3.10 | 0.915 | 22.4 | pass | pass | pass | -0.4 % | 9.7 % | 0.84 |
| `bdg2_hog_education_jewel_steam` | monthly | 12 | 3PH | 4P | 1.76 | 0.972 | 13.6 | pass | pass | pass | 0.1 % | 10.1 % | 0.96 |
| `bdg2_panther_education_sophia_gas` | daily | 365 | 5P | 4P | 11.02 | 0.867 | 16.2 | pass | pass | **fail** | -1.5 % | 8.6 % | 0.40 |
| `bdg2_panther_education_sophia_gas` | monthly | 12 | 5P | 2P | 1.65 | 0.983 | 6.7 | pass | pass | **fail** | -4.5 % | 6.0 % | 0.83 |
| `bdg2_panther_education_sophia_gas` | bills | 12 | 2P | 5P | 0.76 | 0.965 | 8.0 | pass | pass | pass | -3.9 % | 6.5 % | 0.91 |
| `bdg2_robin_education_lizbeth_elec` | daily | 364 | 2P | 4P | 4.98 | 0.794 | 7.3 | pass | pass | pass | 6.5 % | 15.8 % | 0.09 |
| `bdg2_robin_education_lizbeth_elec` | monthly | 12 | 3PC | 2P | 0.16 | 0.971 | 2.6 | pass | pass | pass | 6.6 % | 16.0 % | 0.12 |
| `bdg2_hog_office_napoleon_elec` | daily | 366 | 2P | 3PH | 4.75 | 0.000 | 12.5 | **fail** | pass | **fail** | 1.2 % | 11.1 % | 0.21 |
| `bdg2_hog_office_napoleon_elec` | monthly | 12 | 5P | 3PH | 0.07 | 0.572 | 2.9 | **fail** | pass | **fail** | 1.8 % | 11.6 % | 0.18 |

**Reading the table.**

- **Synthetic truth.** All five shape cases recover their truth on the daily data: the kind, the
  change points within ±2 °F, and the base and slopes within ±5 % (`truth_recovery`). The noise
  cases bracket the baseline gate on daily data as intended. Aggregated to 12 months, even the
  45 % daily noise passes, because monthly sums average it away.
- **Monthly kinds often differ.** With 12 points, many BIC gaps are below 2. The 3PH truth fits as
  4P, the 3PC truth as 4P, and the 4P truth as 5P. Those are legitimate selection outcomes, not
  errors.
- **3PH vs 4P.** Even on daily data the choice is close: the 3PH truth at `syn_3ph_58` lost to 4P
  on three of ten seeds tried. The committed seed is one where 3PH wins by 5.4 BIC.
- **Raw savings on real meters.** The raw reporting year is not always flat:
  - the 2P electricity meter used 6.5 % less in 2017 with nothing injected;
  - the gas meter used 1.5–4.5 % more.

  These are the meters' own year-to-year changes. The "injected" column minus the "raw" column is
  exactly 10 % of the raw reporting total for every model, and `option_c.injected_saving_exact`
  records it.
- **The gas meter.** Its daily and monthly best fit is a 5P whose "cooling" arm falls. SEP's sign
  test rejects that model while the G14 gates pass it.

### Bill cases

Each bill case has 28–35-day bills starting mid-month, one estimated read and one missing bill.

| Bill case | Bills (days) | Selected | Bases or change point (°F) | Base ranges (°F) | BIC gap | R² | adj R² | CV(RMSE) % | Baseline gate | Cal-sim gate | SEP | Raw saving | Injected saving | Avoided cost, injected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bills_syn_dd_hc_55_68` | 11 (391) | DD-HC | 53 / 71 | H 52–55, C 69–72 (DD-HC) | 17.60 | 0.999 | 0.999 | 0.6 | pass | pass | pass | 0.4 % | 10.4 % | 1,755 |
| `bills_bdg2_hog_education_jewel_steam` | 11 (366) | 3PH | 63.3 | H 58–70 (DD-H) | 0.68 | 0.973 | 0.967 | 14.1 | pass | pass | pass | -3.6 % | 6.8 % | 17,573 |
| `bills_bdg2_rat_public_leta_elec` | 11 (376) | 3PC | 61.4 | C 60–66 (DD-C) | 0.47 | 0.989 | 0.986 | 3.4 | pass | pass | pass | 1.5 % | 11.3 % | 1,624 |

**Reading the bill table.**

- **Which model wins.** On the synthetic bills, the degree-day model at the selected bases wins by
  17.6 BIC. On both BDG2 meters a change-point model wins, by less than 1 BIC over the degree-day
  model.
- **Bases vs truth.** The synthetic truth is a heating base of 55 °F and a cooling base of 68 °F.
  With 11 bills CAMBER selects 53 / 71. The heating range 52–55 contains the truth; the cooling
  range 69–72 just misses it.
- **Degree days built two ways** (`degree_days`).
  - Built from each day's OAT, the synthetic baseline has 2,455 HDD and 950 CDD. Built from each
    bill's mean OAT, it has 2,343 HDD and 674 CDD.
  - Refitted on the mean-built degree days, the same model drops from R² 0.999 to 0.964.
- **Calendarization.** It declines the annual totals on all three cases because of the missing
  bill: Portfolio Manager computes no metric across a gap. The overlap table shows an overlap
  being reported as well.

## Calibrated-simulation gate vs baseline gate

open-fdd's `score_g14_monthly` applies Guideline 14's **calibrated-simulation** tolerances:

- monthly: |NMBE| ≤ 5 % and CV(RMSE) ≤ 15 %;
- hourly: |NMBE| ≤ 10 % and CV(RMSE) ≤ 30 %.

CAMBER gates a **regression baseline** used for savings:

- |NMBE| ≤ 0.5 %;
- CV(RMSE) ≤ 15 % monthly or ≤ 30 % daily (`camber/mandv/stats.py` `cv_rmse_max_for`);
- **and** R² ≥ 0.75.

SEP 50001 validity is a third, separate test: R² ≥ 0.50, an F-test, slope p-values and physical
slope signs (§6.4.1).

The vectors report all three verdicts side by side:

- **Where the gates disagree.** `gates_disagree` is true where the two G14 gates differ. Here
  that happens only through **R²**: a load with little weather signal and little noise passes the
  calibrated-simulation tolerances and fails the baseline gate. That covers `syn_weak_weather`
  (daily and monthly) and `bdg2_hog_office_napoleon_elec` (daily and monthly, R² 0.000 and
  0.572).
- **The CV(RMSE) limits agree.** The CV(RMSE) limits are the same on monthly data. Daily data has
  no calibrated-simulation row, so it is judged on the hourly row, which matches CAMBER's daily
  limit.
- **NMBE never decides here.** A least-squares fit with an intercept, scored on its own baseline,
  has a residual sum of zero. Every in-sample NMBE in these vectors is therefore 0, and the 0.5 %
  vs 5 % difference never decides a verdict here. It matters for a model scored on other data,
  such as a calibrated simulation, or for a fit that is not least squares.

## Why two implementations can differ, and what is a real disagreement

[SCHEMA.md](SCHEMA.md#method-how-camber-computes-these-numbers) states CAMBER's method in full.
The usual sources of difference are:

- **The change-point grid.** CAMBER uses 40 points between the 5th and 95th percentile of the OAT,
  and minimises SSE.
- **Selection.** CAMBER keeps the lowest BIC, counting change points in `p`.
- **The 5P dead-band.** CAMBER never allows it to be zero-width.
- **Day weighting.** CAMBER weights period tables by days.
- **Sign conventions.** CAMBER's own `heat_slope` is positive; `slopes_dEdT` avoids the sign
  question.

A kind disagreement where CAMBER's BIC for your kind is within 2 of its best is reported as a
NOTE, and the BIC gap is listed for every fit.

As a sanity run, an independent numpy sketch with a 0.5 °F grid over the full range was checked
against the vectors (it is not committed). Of 267 checks, 263 passed and 1 was a NOTE. The 3
failures were all grid-driven: one daily kind decision 4 BIC apart, and two coefficients whose
change points moved.

## Licence and attribution

- **In the repository:** everything is **Apache-2.0**, like the rest of CAMBER. That covers the
  code, the synthetic inputs and predicted series, and `expected.json`.
  - For the BDG2 cases, `expected.json` holds only statistics computed from the data: model
    kinds, change points, coefficients, fit statistics, verdicts and savings totals. It holds no
    rows of the data.
  - CAMBER redistributes no datasets; see NOTICE and docs/DATASETS.md.
- **Rebuilt locally:** the files `fetch_bdg2.py` writes under `local/` are an adaptation of the
  Building Data Genome Project 2 data. They carry its licence, **CC BY-SA 4.0**: the repository
  licence, which CAMBER's catalog records as `CC-BY-SA-4.0` under the id `bdg2` (the paper
  describing the data is CC BY 4.0). Keep them local, or share them under CC BY-SA 4.0 with this
  attribution:
  - **Source:** Miller, C., Kathirgamanathan, A., Picchetti, B. et al. (2020). The Building Data
    Genome Project 2, energy meter data from the ASHRAE Great Energy Predictor III competition.
    *Scientific Data* 7, 368. doi:10.1038/s41597-020-00712-x.
    <https://github.com/buds-lab/building-data-genome-project-2>
  - **Changes `fetch_bdg2.py` makes:**
    - the publisher's cleaned hourly meters are aggregated to whole local days, calendar months
      and synthetic bill periods;
    - OAT comes from each site's own weather station, converted to °F;
    - energy is converted from the published kWh to kBtu (thermal) or therms (gas);
    - a 10 % saving is injected into the reporting bills' `energy_injected` column;
    - an estimated read and a missing bill are simulated;
    - the costs are a synthetic tariff, because BDG2 has no costs.

## Contributing results back

If you run these vectors through another implementation, please send the results, matches and
mismatches alike. Open an issue or a pull request on
[CAMBER](https://github.com/yroussev/camber) with your results JSON and the checker's output. A
NOTE or FAIL that turns out to be CAMBER's error gets fixed in CAMBER, and the vectors are
regenerated. A disagreement that both sides can defend is documented here, with both readings.
