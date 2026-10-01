# Boiler plant: what an enable point hides, pumping and a fouling gap

*Workbook exercise `plant-boiler` · PNNL re-tuning chapter 8 · about 40 minutes*

## Goal

Ask the heating plant's re-tuning questions (is the hot-water supply temperature reset, are the
boilers shut down in summer, do they short-cycle, is the loop DP setpoint reset?) of a simulated
boiler plant, and find out which of them CAMBER can answer on this data and why. Then see what a
fouled boiler and a lying DP sensor look like to the rules that do run.

## Learn more

Read these first (PNNL, free):

- [Central Utility Plant Heating Control][pnnl-guide-plant-heating], the re-tuning guide: its
  questions (a reset on the hot-water supply temperature, a low loop delta-T, a constant loop DP
  setpoint) and its advice to shut comfort-only boilers down in summer are what this exercise
  asks of the plant.
- [Chapter 8: Central Utility Plant][pnnl-retuning-ch8] of the re-tuning training.

[pnnl-guide-plant-heating]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89222.pdf "Building Re-Tuning Training Guide: Central Utility Plant Heating Control (PNNL-SA-89222)"
[pnnl-retuning-ch8]: https://www.pnnl.gov/sites/default/files/media/file/ch8_central_plant.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 8: Central Utility Plant: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"

## Datasets and licence

- **`lbnl-boiler`**: LBNL's simulated hot-water plant (two boilers, two pumps, one loop), a year
  at one-minute resolution, as a fault-free run plus labelled faulted runs, with the plant's
  Brick model. Licence **CC-BY-4.0** (open; cite it). The download is about 320 MB; this exercise
  ingests the `full` subset (all 17 runs). The catalog lists the problems found in the published
  data and how CAMBER handles each, among them the boiler status point that is really an enable:
  see [its data issues](../DATASETS.md#lbnl-boiler-lbnl-simulated-boiler-plant-labelled-faults-brick-model).

Each run becomes `PLANT__<run>` in the facility `ds-lbnl-boiler`; boiler 1 and pump 1 are the
mapped units. This exercise reads `PLANT__fault_free`, the three boiler-fouling runs
(`PLANT__boiler_foul_065`, the most severe, `_080` and `_095`), the loop DP sensor biases
(`PLANT__hot_water_pressure_bias_20` ... `PLANT__hot_water_pressure_bias_m20`) and, for the
score, every other run.

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Choose the **full** subset, tick `lbnl-boiler` and press **Fetch & ingest**.
3. When the job is done, the row links to **trends** (the trend viewer). This exercise uses its
   own config: use the command line for step 2 of the steps below.

### On the command line

```
camber datasets fetch lbnl-boiler
camber datasets ingest lbnl-boiler --subset full --store lab_store
camber datasets config lbnl-boiler --exercise plant-boiler --store lab_store --out boil.json
camber run boil.json --out boil_out
camber datasets score lbnl-boiler --store lab_store --findings boil_out/findings.json
```

The exercise's config (`--exercise plant-boiler`) lists four rules: `hw_pump_dp_reset` (the
dataset template's one rule), `boiler_summer_lockout`, `boiler_short_cycle` and
`hw_plant_deltat`, all with their defaults. Read its `_comment`: the last three are listed on
purpose.

Its `drift` section adds the boiler drift family with a **declared reference**:
`boiler_efficiency_drift` scores every run's gas input per unit of heat delivered against
`PLANT__fault_free`, fitted in memory on each run and never stored.

## Steps

1. **Look before you judge.** In the trend viewer, open `PLANT__fault_free` and plot the loop
   supply and return temperatures with the outdoor temperature over the whole year. Then plot
   boiler 1's gas input.
2. **Run the exercise's config** (the commands above). Count the findings per rule.
3. **The missing rules.** Read the dataset's data issues and known issues on the boiler status
   point (`camber datasets info lbnl-boiler` prints them).
4. **Pumping.** Read `hw_pump_dp_reset` for the fault-free run in `boil_out/findings.json`:
   `median_speed_pct`, `pct_running_near_full`, `pct_running_near_min`, `dp_sp_reset_present` and
   `n_running`.
5. **Fouling.** Find every finding on `PLANT__boiler_foul_065`, and compare its gas input with
   the fault-free run's in the trend viewer. Then read `boiler_efficiency_drift` for the three
   boiler-fouling runs and the two hot-water temperature biases (`PLANT__hot_water_temp_bias_2`,
   `_4`): `ratio_drift_rel`, `gas_rise_at_matched_oat`, `attribution` and the summary.
6. **The DP sensor.** Compare `median_speed_pct` for the two ±20 % loop DP sensor biases with the
   fault-free run's, and plot the loop DP against its setpoint for one of them.
7. **Score.** Run `camber datasets score` and read the rates for `boiler_efficiency_drift`.

## Questions

1. The config lists four rules. Which produce findings, and why do the others print nothing?
2. Is the hot-water supply temperature reset, and is the boiler shut down in summer? What can
   CAMBER say about each on this plant, and what does the trend viewer show you?
3. What does `hw_pump_dp_reset` find on the fault-free plant, and what does its `ok` leave out?
4. Does any point-in-time rule see the worst boiler fouling? Where does it show? What does
   `boiler_efficiency_drift`, scored against the declared reference, find on the three foulings
   and on the hot-water temperature biases?
5. What does a loop DP sensor that reads 20 % high or low do to the pump, and to the DP reading
   itself? What does the label score say for `boiler_efficiency_drift`?

## What CAMBER shows

- **Findings.** `camber run` prints one line per finding. A rule whose required points are not
  mapped does not run on that equipment, and prints nothing: no finding is not the same as `ok`.
- **Metrics.** For `hw_pump_dp_reset`: the median speed, the shares near full (90 % or more) and
  near minimum (25 % or less), the median DP setpoint, whether it is reset, and `n_running` (the
  hours the pump ran).
- **Drift.** `boiler_efficiency_drift` reports `ratio_drift_rel` (the change in gas input per
  unit of heat delivered, as a fraction), `gas_rise_at_matched_oat`, `attribution` and
  `baseline_source`. The declared reference itself declines as `is_reference`: it is the
  yardstick, not a result.
- **Score.** `camber datasets score` prints the true- and false-positive rates, with 95 %
  intervals, for the entry's one declared detector, `boiler_efficiency_drift` (target: boiler
  fouling).

## Caveats

- The data are simulated: one plant, one climate, one control sequence.
- The plant's boiler status is an enable, on all year; mapping it as a run status would make
  every idle hour look like firing, so CAMBER leaves it unmapped and the status-based rules stay
  silent. On a real plant, trend the burner's firing signal.
- The plant exports no hot-water supply setpoint, so no rule compares the supply with it.
- `hw_pump_dp_reset` reads pump 1 only; pump 2 is not mapped.
- The `hot_water_temp_bias` runs bias the loop return, not the supply as documented (see the data
  issues).

## Going further

- Here `boiler_efficiency_drift` compares each run with another boiler (the fault-free run),
  because each fault is its own year-long run. On a real plant, compare a boiler with itself:
  declare a clean season as the reference (`"reference": {"period": [start, end]}`), or freeze a
  baseline with `camber drift freeze` and compare later seasons with it (see
  [the CLI guide](../CLI.md#a-declared-reference)).
- Compare this plant with [the chilled-water side](plant-chw-reset-pumping.md): the same three
  questions, answered from different points.
- See [the sensor exercise](plant-sensor-vs-equipment.md) for why a sensor bias is scored as a
  negative.
