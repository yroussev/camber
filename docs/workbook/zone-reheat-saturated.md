# Terminal units: a zone below setpoint with its reheat maxed out

*Workbook exercise `zone-reheat-saturated` · PNNL re-tuning chapter 7 · about 40 minutes*

## Goal

A cold zone is a complaint. The question for re-tuning is whether the box could have done
more. If its reheat is already wide open and the zone is still below its heating setpoint, the
box has run out of heating. That is a capacity or airflow problem to fix in the field, not a
setting to tune. Find such a zone, tell it apart from an overcooled one, and see what happens
when the data do not say how hard the reheat was working.

## Learn more

Read these first (PNNL, free):

- [Zone Heating and Cooling Control][pnnl-guide-zone-heat-cool], the re-tuning guide for zones:
  what to check when a zone does not reach its heating setpoint.
- [Chapter 7: Terminal Units in Air Distribution System][pnnl-retuning-ch7] of the re-tuning
  training, on reheat at the terminal box.

[pnnl-guide-zone-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf "Building Re-Tuning Training Guide: Zone Heating and Cooling Control (PNNL-SA-85200)"
[pnnl-retuning-ch7]: https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"

## Datasets and licence

- **`lbnl-fpu`**: LBNL's simulated fan-powered VAV terminal boxes, a year at one-minute
  resolution, fault-free plus labelled faults. Licence **CC-BY-4.0** (open; cite it). A 2.2 GB
  download; the default subset ingests five runs. See
  [its data issues](../DATASETS.md#lbnl-fpu-lbnl-simulated-fan-powered-vav-terminal-units-labelled-faults).
- **`ornl-frp-vav`**: a real test building's rooftop unit and ten VAV boxes with electric
  reheat, one day per test scenario. Licence **CC-BY-4.0** (open; cite it). The default subset is
  a 7.7 MB workbook. See
  [its data issues](../DATASETS.md#ornl-frp-vav-ornl-multi-zone-vav-terminal-faults-real-building-labelled).

From `lbnl-fpu` the exercise uses the five default runs (`PFPU__fault_free`,
`PFPU__VAVDMPRStuck_50pct`, `PFPU__VAVDMPRStuck_100pct`, `PFPU__ReheatVLVStuck_0pct`,
`PFPU__ReheatVLVLeak_50pctMaxFlow`). From `ornl-frp-vav` it uses only the ten boxes of the
fault-free day (`RTU_VAV_<room>__d3_fault_free`): a real building on a normal day.

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Tick `lbnl-fpu` and `ornl-frp-vav` and press **Fetch & ingest**.
3. When the jobs are done, use each row's **trends** link to look at the data. This exercise
   uses its own configs: run them from the command line.

### On the command line

```
camber datasets fetch lbnl-fpu
camber datasets ingest lbnl-fpu --store lab_store
camber datasets config lbnl-fpu --exercise zone-reheat-saturated --store lab_store --out rh.json
camber run rh.json --out rh_out
camber datasets fetch ornl-frp-vav
camber datasets ingest ornl-frp-vav --store lab_store
camber datasets config ornl-frp-vav --exercise zone-reheat-saturated--ornl-frp-vav --store lab_store --out rh_ornl.json
camber run rh_ornl.json --out rh_ornl_out
```

Both configs run the same three rules:

- `reheat_capacity_shortfall` counts the occupied samples with the zone more than 1.5 °F below
  its heating setpoint *and* the reheat at 90 % or more. It leaves out the warm-up after each
  occupied start.
- `overcooling_severity` measures how far, and for how long, a zone sits below its setpoints,
  but it sets samples with the reheat saturated apart as a heating shortfall.
- `unmet_setpoint_hours` counts the cold (and hot) hours without asking about the reheat.

The `lbnl-fpu` config also runs `reheat_penalty`, for question 4. The box trends both the
controller's reheat demand and the valve's measured position: `reheat_capacity_shortfall` and
`overcooling_severity` read the demand (how hard the controller asks for heat), and
`reheat_penalty` reads the position (how much heat the valve lets through). The `ornl-frp-vav` config
limits itself to the fault-free day with an `"equip"` list.

## Steps

1. **Look first.** In the trend viewer, open `PFPU__ReheatVLVStuck_0pct` for a January weekday.
   Plot the zone temperature, its heating setpoint, the reheat valve signal, the airflow and its
   setpoint, and the discharge-air temperature. Then look at `PFPU__fault_free` on the same day.
2. **Run the `lbnl-fpu` config** and read `reheat_capacity_shortfall` for each run: the share of
   samples in shortfall, the hours, the median deficit and the likely cause it names.
3. **Tell shortfall from overcooling.** Read `overcooling_severity` and `unmet_setpoint_hours` for
   the same run.
4. **Run the `ornl-frp-vav` config.** Which rooms are cold on the fault-free day? What does
   `reheat_capacity_shortfall` say about them?

## Questions

1. Which run has a zone below its heating setpoint with its reheat maxed out? For how much of
   the occupied time, how many hours, and how far below setpoint?
2. Is that a heating-capacity problem or an airflow problem, according to CAMBER? What evidence
   does it use?
3. How do `unmet_setpoint_hours` and `overcooling_severity` describe the same run? Why is
   "overcooled" the wrong word for it?
4. The reheat valve in that run is stuck *shut*, yet `reheat_capacity_shortfall` finds the
   reheat maxed out. Which valve signal does it read, and which does `reheat_penalty` read? What
   does `reheat_penalty` report, and what does its caveat tell you? What would you check on site?
5. Which runs have heat to spare, and how can you tell?
6. On the ORNL fault-free day, which rooms are too cold, and for how much of the occupied time?
   How deep does the worst one go below its heating setpoint?
7. Why can CAMBER not tell whether those ORNL rooms had their reheat maxed out? What would you
   ask the building's operator to trend?

## What CAMBER shows

- **Findings.** `reheat_capacity_shortfall` reports `shortfall_pct`, `shortfall_hours`,
  `median_deficit_f`, `airflow_to_sp_ratio`, `discharge_temp_f` and `likely_cause`;
  `overcooling_severity` reports its depth tiers, `max_depth_f` and, separately,
  `shortfall_severity`, graded on both depth and share of samples (`shortfall_depth_severity`
  is the depth alone); `unmet_setpoint_hours` reports `too_cold_pct`.
- **What is missing is also an answer.** A rule that needs a signal the data do not have
  produces no finding for that box.
- **Report.** `camber report rh.json --out rh.html` shows the same findings with evidence charts.

## Caveats

- `lbnl-fpu` is simulated: the stuck-shut valve is the only saturated-reheat case in the default
  subset. The shortfall rules read the controller's demand; with only a measured position a
  stuck-shut valve would look like a box that never needed heat.
- A parallel fan-powered box mixes in warm plenum air when its fan runs, so a few degrees of
  discharge-air rise do not prove the coil is heating. The config sets `reheat_penalty`'s
  `fan_heat_f` to `"auto"` to allow for that lift.
- The ORNL building is real, but each scenario is one day. A day's cold room may be that day's
  weather or occupancy, not a fault.
- The shortfall thresholds (1.5 °F, 90 %, 5 % and 20 % of samples) are screening-grade judgement,
  not a standard's.

## Going further

- The `full` subset of `lbnl-fpu` adds reheat-coil fouling at three severities, on both the air
  and the water side. Does a fouled coil reach saturation, or does the controller hide it? (The
  drift rule `vav_reheat_valve_drift` is built for the early stage.)
- `lbnl-b59` has 35 underfloor terminals with a heating setpoint and a reheat valve in its
  `full` subset, and its default config runs `reheat_capacity_shortfall` on them: a real building
  where the rule can see the valve.
