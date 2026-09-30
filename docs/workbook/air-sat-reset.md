# Supply-air temperature reset: is it reset, and which way?

*Workbook exercise `air-sat-reset` · PNNL re-tuning chapter 5 · about 45 minutes*

## Goal

Decide, for three air handlers, whether the supply-air (discharge-air) temperature is reset at
all, which way it moves with the outdoor air, and how far it sits below a standard reset target.
Then check the other half of discharge-air control: does the unit meet the setpoint it has?
Cold supply air that is never reset is one of the commonest reasons terminal reheat runs all year.

## Learn more

Read these first (PNNL, free):

- [AHU Discharge-Air Temperature Control][pnnl-guide-discharge-air-temp], the re-tuning guide:
  its questions ask whether the discharge-air temperature is reset, what drives the reset, and
  whether the unit holds its setpoint. This exercise answers each one from trends.
- [Chapter 5: Air Handling Units][pnnl-retuning-ch5] of the re-tuning training, for the trends
  to collect and how to read them.

[pnnl-guide-discharge-air-temp]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84186.pdf "Building Re-Tuning Training Guide: AHU Discharge-Air Temperature Control (PNNL-SA-84186)"
[pnnl-retuning-ch5]: https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning (PNNL-SA-85063)"

## Datasets and licence

- **`lbnl-sdahu`**: LBNL's simulated single-duct VAV air handler (cooling coil only, dry-bulb
  economizer), a year at one-minute resolution, fault-free plus labelled faulted runs. Licence
  **CC-BY-4.0** (open; cite it). About 600 MB to download. See
  [its data issues](../DATASETS.md#lbnl-sdahu-lbnl-simulated-single-duct-ahu-labelled-faults).
- **`lbnl-ddahu`**: LBNL's simulated dual-duct air handler (a hot deck and a cold deck), a year
  at one-minute resolution. Licence **CC-BY-4.0** (open; cite it). About 1.8 GB to download.
  See [its data issues](../DATASETS.md#lbnl-ddahu-lbnl-simulated-dual-duct-ahu-labelled-faults),
  in particular the one about the economizer supply-air setpoint.
- **`irish-ahu`**: five and a half years of 15-minute BMS trends from one real air handler at an
  industrial site in Ireland. Licence **CC-BY-4.0** (open; cite it). About 22 MB. See
  [its data issues](../DATASETS.md#irish-ahu-irish-industrial-ahu-real-bms-trends-unlabelled).

The equipment used: `AHU__fault_free` and `AHU__damper_stuck_075` in `ds-lbnl-sdahu`,
`DDAHU__fault_free` (the cold deck) in `ds-lbnl-ddahu`, and `AHU__ahu` in `ds-irish-ahu`.

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Tick `lbnl-sdahu`, `lbnl-ddahu` and `irish-ahu` and press **Fetch & ingest**.
3. When the jobs are done, open each row's **trends** link to look at the supply-air
   temperature, its setpoint (where there is one) and the outdoor air. The exercise's own
   configs are run from the command line.

### On the command line

```
camber datasets fetch lbnl-sdahu lbnl-ddahu irish-ahu
camber datasets ingest lbnl-sdahu lbnl-ddahu irish-ahu --store lab_store
camber datasets config lbnl-sdahu --exercise air-sat-reset --store lab_store --out sat.json
camber run sat.json --out sat_out
camber datasets config lbnl-ddahu --exercise air-sat-reset--lbnl-ddahu --store lab_store --out sat_dd.json
camber run sat_dd.json --out sat_dd_out
camber datasets config irish-ahu --exercise air-sat-reset--irish-ahu --store lab_store --out sat_ie.json
camber run sat_ie.json --out sat_ie_out
```

Each config runs the supply-air rules only:

- `supply_air_reset` asks the *shape* question. Where a setpoint is trended it reads the
  setpoint: does it move, repeatedly, with the outdoor air? Where none is trended it fits the
  supply air against the outdoor air over the cooling hours.
- `supply_air_reset_compliance` asks the *target* question: how often, and by how much, is the
  supply air colder than the ASHRAE Guideline 36 outdoor-air reset would ask for (55 °F when it
  is 70 °F or warmer outside, rising to 65 °F at 60 °F and below)? None of these units documents a
  reset of its own, so the standard target is the yardstick.
- `supply_air_control` (single-duct unit only) asks whether the supply air stays within 2 °F of
  its setpoint while the fan runs in occupied hours. Its `occupancy_gate` (default `"trended"`)
  reads the unit's own trended occupancy point when there is one; a unit without one is judged
  on every fan-on hour.

## Steps

1. **Look first.** In the trend viewer, plot `AHU__fault_free`'s supply-air temperature and its
   setpoint over a week in spring and a week in summer. Then do the same for the Irish unit's
   supply air against its outdoor temperature.
2. **Run the three configs** and read the findings each `camber run` prints.
3. **Shape.** For each unit, read `supply_air_reset`: its verdict, `slope_per_F` (°F of supply
   air per °F of outdoor air) and, on the single-duct unit, `sp_range_f` (how far the setpoint
   moved).
4. **Target.** Read `supply_air_reset_compliance`: `pct_below_g36_target` and `mean_gap_f`.
5. **Holding the setpoint.** Read `supply_air_control` on the single-duct runs: the share of
   occupied running hours too warm and too cold, and `occupancy_gate` (which hours it judged).
   Then set `"occupancy_gate": "off"` on the rule in `sat.json` (a rule entry becomes
   `{"name": "supply_air_control", "params": {"occupancy_gate": "off"}}`), run it again and
   compare the fault-free unit.

## Questions

1. Is the single-duct unit's supply-air temperature reset? What does CAMBER read to decide, and
   how far below the Guideline 36 target does the supply air sit?
2. Is the dual-duct unit's cold deck reset? What does the dataset's documentation say it should
   do in economizer weather, and what does the data show?
3. Does the Irish unit reset its supply air? In which direction does it move as the outdoor air
   warms, and is that the direction a cooling supply-air reset should move?
4. Which single-duct run fails to hold its supply-air setpoint, in which direction, and why can
   that unit not correct it?
5. The fault-free single-duct unit's supply air also runs more than 2 °F above its setpoint in
   some fan-on hours, yet `supply_air_control` reads it as healthy. When do those hours happen,
   what changes when the rule counts every fan-on hour, and are they a control fault?

## What CAMBER shows

- **Findings.** `camber run` prints one line per finding; `*_out/findings.json` holds every
  metric: `reset_direction`, `slope_per_F`, `sp_behaviour` and `sp_range_f` for
  `supply_air_reset`; `pct_below_g36_target`, `mean_gap_f` and `tracks_target` for
  `supply_air_reset_compliance`; `too_warm_pct`, `too_cold_pct` and `occupancy_gate` for
  `supply_air_control`.
- **Caveats.** A reset inferred from the supply air alone, with no setpoint trended, carries a
  caveat that asks for the setpoint: read it.
- **Report.** `camber report sat.json --out sat.html` shows the same findings with evidence
  charts.

## Caveats

- The two LBNL units are simulations with a fixed sequence; the Irish unit is real but trends
  no supply-air setpoint and no fan status, so its reset is inferred from the supply air.
- The Guideline 36 target is a standard yardstick, not these units' design: the saving is an
  opportunity to weigh against zone comfort and humidity, not a fault.
- The Irish unit's outdoor sensor reads warmer than the nearby weather station, most in the
  afternoon (see its data issues); the slope is against the unit's own sensor.
- A negative slope can also be load tracking: warmer weather, more cooling, colder supply air at
  a fixed setpoint that the coil only just holds. Only a trended setpoint settles it.

## Going further

- Tighten or loosen the target: `supply_air_reset_compliance` takes `min_clg_sat`, `t_max`,
  `oat_min` and `oat_max`. Set them to a reset you would propose for the single-duct unit and
  see how its gap changes.
- Run the dual-duct unit's default config (`camber datasets config lbnl-ddahu --store
  lab_store --out dd.json`) and compare its outdoor-air findings across the seasons.
- The exercise `air-economizer` uses the same single-duct runs for the outdoor-air damper.
