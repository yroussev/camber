# Terminal units: the reheat penalty and overcooling at minimum airflow

*Workbook exercise `zone-reheat-overcooling` · PNNL re-tuning chapter 7 · about 45 minutes*

## Goal

Find the terminal boxes that cool a zone and then heat the same air back up. Tell the two
causes apart: a minimum airflow set so high that a healthy box overcools its zone, and a stuck
damper that pushes far more air than the box asks for. Then check which box in the data is the
one under test, and score CAMBER against the published fault labels.

## Learn more

Read these first (PNNL, free):

- [Zone Heating and Cooling Control][pnnl-guide-zone-heat-cool], the re-tuning guide for zones:
  read what it says about terminal-box minimum airflow and about reheat, the two halves of this
  exercise.
- [Chapter 7: Terminal Units in Air Distribution System][pnnl-retuning-ch7] of the re-tuning
  training, for how a terminal box's airflow and reheat are meant to work together.

[pnnl-guide-zone-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf "Building Re-Tuning Training Guide: Zone Heating and Cooling Control (PNNL-SA-85200)"
[pnnl-retuning-ch7]: https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"

## Datasets and licence

- **`lbnl-fpu`**: LBNL's simulated fan-powered VAV terminal boxes on one air handler, a year at
  one-minute resolution, as a fault-free run plus labelled faulted runs. Licence **CC-BY-4.0**
  (open; cite it). The download is one 2.2 GB archive; the default subset ingests five of its
  runs. The catalog lists the problems found in the published data and how CAMBER handles each:
  see [its data issues](../DATASETS.md#lbnl-fpu-lbnl-simulated-fan-powered-vav-terminal-units-labelled-faults).

The default subset holds five runs of the parallel fan-powered box, each one piece of equipment
in the facility `ds-lbnl-fpu`: `PFPU__fault_free`, the box damper stuck half and fully open
(`PFPU__VAVDMPRStuck_50pct`, `PFPU__VAVDMPRStuck_100pct`), the reheat valve stuck shut
(`PFPU__ReheatVLVStuck_0pct`) and a leaking reheat valve (`PFPU__ReheatVLVLeak_50pctMaxFlow`).

**Which box is under test?** Each simulated run has four boxes, one per zone (interior, west,
south and east). The faults are imposed on one of them only. CAMBER maps the south-zone box
(`_S` columns), because comparing each faulted run with the fault-free one shows that only the
`_S` box's columns change. Until 0.82 CAMBER mapped the west box and scored a healthy one.

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Tick `lbnl-fpu` and press **Fetch & ingest** (a 2.2 GB download: start it early).
3. When the job is done, the row links to **trends** (the trend viewer) and **report** (the
   dataset's default config). This exercise uses its own config: use the command line for step 2
   of the steps below, and the lab's trend viewer to look at the data.

### On the command line

```
camber datasets fetch lbnl-fpu
camber datasets ingest lbnl-fpu --store lab_store
camber datasets config lbnl-fpu --exercise zone-reheat-overcooling --store lab_store --out fpu.json
camber run fpu.json --out fpu_out
camber datasets score lbnl-fpu --store lab_store --findings fpu_out/findings.json
```

The exercise's config (`--exercise zone-reheat-overcooling`) runs four rules on each run's box:

- `airflow_tracking`: is the airflow following its setpoint?
- `reheat_penalty`: is the box reheating air that was cooled centrally?
- `overcooling_min_flow`: is the zone overcooled while the box sits at its minimum airflow?
- `unmet_setpoint_hours`: how often is the zone outside its setpoints?

The reheat signal is the controller's reheat *demand*, not a measured valve position. Keep that
in mind for question 5. `camber report fpu.json --out fpu.html` gives the same findings as a
report with evidence charts.

## Steps

1. **Look before you judge.** In the trend viewer, open `PFPU__fault_free` for a winter
   weekday. Plot the airflow against its setpoint, the reheat valve, the zone temperature and
   its two setpoints. When does the box reheat, and at what airflow?
2. **Run the exercise's config** (the commands above) and read the findings `camber run` prints.
3. **The healthy box.** Find `overcooling_min_flow` and `reheat_penalty` for `PFPU__fault_free`.
   How often does it sit at its minimum with the zone below its cooling setpoint? What is that
   minimum as a share of its peak airflow?
4. **The stuck dampers.** For the two `VAVDMPRStuck` runs, compare the airflow with its setpoint
   (`airflow_tracking`), the share of occupied hours with reheat (`reheat_penalty`) and the
   cold hours (`unmet_setpoint_hours`).
5. **The valve runs.** Read the same rules for `PFPU__ReheatVLVStuck_0pct` and
   `PFPU__ReheatVLVLeak_50pctMaxFlow`.
6. **Score.** Run `camber datasets score` on the findings and read the per-detector rates.

## Questions

1. Does the fault-free box overcool its zone? How often does it sit at its minimum airflow with
   the zone already below the cooling setpoint, and what is that minimum as a share of the box's
   peak airflow?
2. How often does the fault-free box reheat, and why does that count as a (small) penalty rather
   than normal operation?
3. How far off its setpoint is the airflow of the box whose damper is stuck fully open, and how
   often does that box reheat? Compare it with the half-open damper.
4. `overcooling_min_flow` stays quiet on the stuck-open box, even though it overcools its zone
   more than any other run. Why?
5. The box with its reheat valve stuck shut gets a `reheat_penalty` **fault**. Is it really
   wasting reheat energy? What does the rule see?
6. Which box, of the four in each simulated run, is the device under test, and how would you
   check that yourself?
7. What true- and false-positive rates does the label score give `airflow_tracking`, and which
   faulted runs does the overall score count as missed?

## What CAMBER shows

- **Findings.** `camber run` prints one line per finding with its severity (`fault`, `warn`,
  `ok`); `fpu_out/findings.json` holds every metric, e.g. `mean_abs_rel_error` for
  `airflow_tracking`, `valve_open_pct` for `reheat_penalty`, `overcool_at_minflow_pct` and
  `median_minflow_fraction` for `overcooling_min_flow`, `too_cold_pct` for
  `unmet_setpoint_hours`.
- **Evidence.** In the report, `overcooling_min_flow` shades the hours when the zone runs well
  below its cooling setpoint, and `reheat_penalty` compares the reheat valve with the
  discharge-air rise.
- **Score.** `camber datasets score` prints the true- and false-positive rates, with 95 %
  intervals, for the dataset's declared detectors (here only `airflow_tracking`), and which rules
  fired on each labelled run.

## Caveats

- The data are simulated: one building, one climate, one control sequence, and a fault held all
  year. A real stuck damper sticks at some unknown position on some unknown day.
- The reheat signal is the controller's demand. A valve that cannot open, or that leaks, is
  exactly what the demand cannot see.
- In a parallel fan-powered box, the box fan adds warm plenum air when it runs, so the discharge
  air rises a few degrees even with the reheat valve shut. The entering-air reading is the air
  handler's supply temperature, a proxy.
- The rules judge weekdays 07–18 (their default schedule). The simulated building starts its
  occupied setpoints at 06:00.

## Going further

- Run the dataset's default config (`camber datasets config lbnl-fpu --store lab_store --out
  fpu_default.json`) and compare: which of this exercise's findings are missing?
- The `full` subset (`camber datasets ingest lbnl-fpu --subset full --store lab_store`) adds the
  series fan-powered box and the other faults: airflow-sensor biases, reheat-coil fouling,
  unstable control. Which of them does `airflow_tracking` catch?
- Continue with [`zone-reheat-saturated`](zone-reheat-saturated.md): what the stuck-shut valve
  looks like to a rule built for it.
