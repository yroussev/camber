# Tuning thresholds with your own data

Every rule threshold in CAMBER is a starting point. Most defaults are engineering judgment, not
measurements of your building, and [THRESHOLDS.md](THRESHOLDS.md) says which ones. This page is
about re-tuning them as you collect data, without fooling yourself.

> **Status (0.98, provisional).** `camber rules params`, the YAML config format and the `basis`
> provenance map are new in 0.98 and may still change before 1.0.

## The short version

1. **Find the knob.** `camber rules params RULE` lists the rule's parameters with their defaults,
   units, sensible ranges, where each default comes from, and how to calibrate it.
2. **Calibrate on a period you know is good**: a commissioning week, the month after a repair, a
   fault-free run in a labelled dataset. Take a robust statistic (a median, a 95th percentile)
   of the quantity the parameter bounds.
3. **Score on data you did not calibrate on.** A different period, a different unit, or a
   held-out half. A threshold fitted to the data it is then judged on will look better than it is.
4. **Write down what you did**, in the config, beside the value: the data, the period, the
   statistic and the scoring data. YAML configs keep that as comments; the `basis` map copies it
   into every finding of the rule.
5. **Re-check against labelled data** when you have it: a confirmed fault, a service ticket, a
   catalog dataset with fault labels.

## 1. Find the parameter

```
camber rules params                     # every rule
camber rules params leaking_valve       # one rule: default, unit, range, basis, how to calibrate
camber rules params leaking_valve --yaml > leak.yaml   # a config snippet, notes as comments
camber rules params --json              # machine-readable, for your own tooling
```

The same information, for every rule, is in [THRESHOLDS.md](THRESHOLDS.md). Its **basis**
column tells you how much to trust a default:

- `standard: ...` is a value the code takes from a cited standard section. Change it only
  when your sequence of operation says otherwise.
- `public source: ...` comes from a published guide (a PNNL Re-tuning chapter, for example).
  It is a sensible starting point, not a measurement of your building.
- `CAMBER judgment` is an engineering choice, usually screening-grade. **Re-tune these first.**
- `calibrated on ...` was fitted to the named data. Check whether your building resembles it.

Some thresholds are still fixed in code, and THRESHOLDS.md lists them under **Fixed in code**.
If you need one of them tuned, please open an issue.

## 2. Calibrate on a known-good period

A threshold separates normal from faulty, so learn "normal" from data you trust:

- **A commissioning or post-repair period.** After a TAB report, a valve replacement or a
  sensor calibration, the next few weeks are your best known-good data.
- **A fault-free run.** Labelled datasets (the `lbnl-*` simulations, `nist-heatpump-fdd`) ship
  runs with no fault injected.
- **The design documents.** A sequence of operation, a TAB report or a nameplate is independent
  of the trends, so it is the cleanest basis of all: a design minimum outside-air fraction, a
  design loop ΔT, a charging target.

Then measure the quantity the parameter bounds, over the conditions the rule judges (fan on,
occupied, running, and so on), and take a robust statistic:

- **A normal level** (a fan heat, a design minimum OA fraction, a target subcooling): take the
  median.
- **A noise band** (a tolerance, a "closed" valve position, a deadband): take a high percentile
  (the 95th) of the fault-free spread, then leave a margin above it.
- **A severity share** (`warn_pct`, `fault_pct`): look at the metric the finding reports on
  units you know are healthy, and set the level well above their spread.

The findings help. Most rules report the statistic their threshold is compared with (for example
`median_delta_f` for `leaking_valve`, `simultaneous_hc_pct` for `simultaneous_heat_cool`), so a
run on the known-good period at the default settings shows where normal sits.

## 3. Avoid circular calibration

**If you calibrate on a period and then score on the same period, the score is not evidence.**
The threshold was chosen to call that period normal, so of course it does. Keep calibration and
scoring apart:

- **Split in time.** Calibrate on one period and judge the next. A store source takes a window:
  `"source": {"kind": "store", ..., "start": "2018-01-01", "end": "2018-06-30"}`.
- **Split by unit.** Calibrate on some units and judge the others. This is sound only when the
  units are alike (same design, same sequence).
- **Leave one out.** With few labelled runs, calibrate on all but one and score the one left out,
  then repeat for each.
- **Say so when you cannot.** Sometimes the only good data is the data you judge. That is
  acceptable for a first pass, but write it down, and do not quote the result as a detection rate.

## 4. Record the provenance in the config

JSON has no comments, so JSON templates carry a top-level `"_comment"`. YAML (the optional
`[yaml]` extra: `pip install 'camber-toolkit[yaml]'`) lets the note sit beside the value:

```yaml
rules:
  - name: outdoor_air_fraction
    params:
      # Calibrated on AHU-3, 2026-03-02..03-29, fan on + occupied (41,000 minutes): median OA
      # fraction 14.2 %. TAB report 2025-11 design minimum: 15 %. Scored on April-June only.
      min_oa_pct: 14.0
    basis:
      min_oa_pct: "calibrated on AHU-3 March 2026 (median fan-on OA fraction); TAB 15 %"
```

The optional **`basis`** map is copied into each finding of that rule as
`metrics["param_basis"]` (`{"min_oa_pct": {"value": 14.0, "basis": "..."}}`). The provenance
then travels into `findings.json`, the reports and the fault register. A `basis` for a
parameter you did not set records the default. A misspelt name is an error.

JSON and YAML configs that say the same thing give identical results. `camber run`,
`camber report`, `camber explain`, `camber drift`, `camber fleet` and `python -m camber.config`
all read `.yaml` / `.yml`. To start from a catalog template in YAML:

```
camber datasets config lbnl-sdahu --store lab_store --out sdahu.yaml   # or --format yaml
```

The template's notes become comments, so you can edit the values with their reasons beside them.

## 5. Re-check against labelled data

A threshold that looks right on healthy data can still miss faults. When you have labels, score
the tuned config and compare it with the defaults:

```
camber run tuned.yaml --out tuned
camber datasets score lbnl-sdahu --store lab_store --findings tuned/findings.json
```

`datasets score` gives the true-positive rate, the false-positive rate and accuracy, each with a
95% interval. On a few labelled runs the intervals are wide, so read a one-run change as noise
until more data agree. On your own building, a confirmed fault (a work order, a service report)
is a label too: check that the tuned threshold still flags it.

## Worked examples on open data

Each example uses a dataset from the [catalog](DATASETS.md). Fetch and ingest it first
(`camber lab`, or `camber datasets fetch ID` then `camber datasets ingest ID --store lab_store`).

### A design minimum outside air (`lbnl-sdahu`, `outdoor_air_fraction`)

The default `min_oa_pct` is a generic 20%. The `lbnl-sdahu` unit is not generic. Its sequence
(inventory section 1.2) holds the damper at a fixed 10% position, which measures as a 1.6% OA
fraction.

1. **Calibrate.** Run `outdoor_air_fraction` on the fault-free run, `AHU__fault_free`, over
   occupied, fan-on samples. The median OA fraction of its 119,163 occupied, fan-on minutes is
   1.6%, and the dataset template sets `min_oa_pct: 1.6` with that note in its `_comment`
   (`camber datasets config lbnl-sdahu --format yaml` shows it as a comment).
2. **Check it independently.** The inventory's fixed damper position is a design document, not
   the trends, so it corroborates the calibrated value without reusing the scored data.
3. **Know what is circular.** `AHU__fault_free` is also one of the negatives when the dataset is
   scored. Its clean verdict at 1.6% is therefore partly by construction. The evidence is the
   verdicts on the damper-fault runs, which the calibration never saw. For a cleaner split, set
   `source.start` / `source.end` to calibrate on one half of the year and judge the other half.

### A charging target (`nist-heatpump-fdd`, `dx_refrigerant_charge`)

The rule compares each unit's liquid subcooling with a target. The dataset does not publish the
nameplate targets, so the template uses each unit's fault-free median subcooling: 8.6 °F on the
14 SEER unit and 9.7 °F on the 16 SEER unit.

1. **In-sample (the template).** The targets come from the fault-free runs, and those runs are
   then scored. [FDD-DX.md](FDD-DX.md#scored-on-nist-heatpump-fdd) reports 24/24 charge runs
   caught at 0/4 false alarms. The 0/4 is in-sample: those four runs set the targets.
2. **Held out.** When the fault-free runs are split into two halves by file (calibrate on one
   half, judge the other), the rule catches 23/24 charge runs at 0/8 false alarms. Leaving each
   test file out of its own baseline gives 86/95 files caught at 7/90 false alarms. These are the
   numbers to quote. They are honest because no scored file set its own threshold.
3. **What to take away.** Calibration on fault-free data worked, since the held-out rates are
   close. But only the held-out rates show that, and they are what a new building would see.

### A known circularity (`lbnl-sdahu`, `leaking_valve`)

`leaking_valve` allows for the supply fan's heat (`fan_heat_f`, 2 °F by default, the ASHRAE
Guideline 36 initial value). On `lbnl-sdahu`, the fault-free run's median rise from mixed air to
supply air, with both valves closed and the fan on, is about 1 °F. The 0.98 template calibrates
`fan_heat_f` to 1.0 °F on that run, and the fault-free run is again a scored negative. The template
comment and [VALIDATION.md](VALIDATION.md) state the circularity. When you reuse a calibrated
value from a template, check its note for the same trap.

### Your own building

With no labels, the procedure is the same, only slower:

1. Pick a known-good month, just after commissioning or a repair.
2. Run the rules at their defaults on it, and read the finding metrics each threshold is compared
   with.
3. Set the parameters from those statistics, with a margin, and write the notes in the YAML.
4. Judge the following months. When a finding is confirmed or dismissed on site, record it. Those
   records are your labels for the next round of tuning.

## See also

- [THRESHOLDS.md](THRESHOLDS.md): every parameter, its basis and calibration hint (generated).
- [CLI.md](CLI.md#yaml-configs-and-tunable-thresholds-provisional-098): `camber rules params`, `camber datasets config --format`.
- [VALIDATION.md](VALIDATION.md): how CAMBER's own detection rates are measured.
- The [re-tuning workbook](workbook/index.md): exercises that tune a rule to a dataset's unit.
