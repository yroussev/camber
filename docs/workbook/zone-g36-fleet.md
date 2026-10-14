# A G36 VAV fleet: rogue zones, starved cohorts and resets

*Workbook exercise `zone-g36-fleet` · PNNL re-tuning chapters 5 and 7 · about 60 minutes*

## Goal

An air handler that resets its supply-air temperature and its duct static pressure by
trim-and-respond, as ASHRAE Guideline 36 lays out, serves its zones only as well as the
requests they send it. Find a zone that drags a reset for everyone, a whole group of zones short
of air at once, a reset that ignores its requests, and boxes that reheat more air than they need
to. Then decide, for each, whether the fault is in a zone, in the air handler or in the reset
itself. No open dataset in the catalog trends the reset requests these checks need.

## Learn more

Read these first (PNNL, free):

- [Chapter 5: Air Handling Units][pnnl-retuning-ch5] of the re-tuning training: resetting the
  supply-air temperature and the duct static pressure, and what to trend to see a reset at work.
- [Chapter 7: Terminal Units in Air Distribution System][pnnl-retuning-ch7]: what each VAV box
  asks of its air handler, and how one box can hold a reset where the others do not need it.
- [Zone Heating and Cooling Control][pnnl-guide-zone-heat-cool], the re-tuning guide on
  terminal units: reheat at minimum airflow, and zones that heat and cool at once.

[pnnl-retuning-ch5]: https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning (PNNL-SA-85063)"
[pnnl-retuning-ch7]: https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"
[pnnl-guide-zone-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf "Building Re-Tuning Training Guide: Zone Heating and Cooling Control (PNNL-SA-85200)"

## Datasets and licence

- **`synthetic-g36-vav-fleet`**: a **synthetic** VAV system that CAMBER generates on your
  computer. Nothing is downloaded and no real building is behind it. Each air handler trends its
  supply-air temperature and duct static pressure with their setpoints, and the reset requests
  its boxes sent it for each. Each of its six single-duct reheat boxes trends its space
  temperature and setpoints, its airflow and minimum airflow setpoint, its damper and its reheat
  valve. The data is hourly, for three weeks of spring, and the units run weekdays from 05:00 to
  19:00. Licence **Apache-2.0** (CAMBER's own; the data may be shared). About 0.9 MB. See
  [synthetic datasets](../DATASETS.md#synthetic-datasets) for how CAMBER generates it.

The same weather drives eight air handlers, each ingested with its boxes in the facility
`ds-synthetic-g36-vav-fleet`: `AHU__<name>` and `VAV1__<name>` to `VAV6__<name>`, where the name
is one of `healthy`, `rogue_sat`, `rogue_static`, `starved`, `not_responding`, `reheat`,
`heat_cool` and `near_miss`. Boxes 1 to 4 face east, west, south and north; boxes 5 and 6 are
interior. What each name hides is the exercise; the instructor key describes how each was built.

## Setup

### In the lab

1. Run `camber lab` and open the URL it prints (`http://127.0.0.1:8765/lab?token=...`).
2. Tick `synthetic-g36-vav-fleet` (its kind reads `synthetic`) and press **Fetch & ingest**.
   CAMBER generates the file instead of downloading it, then ingests it like any other dataset.
3. When the job is done, the row links to **trends** (the trend viewer) and to **report**, the
   audit report of the dataset's template. The steps below use the command line.

### On the command line

```
camber datasets fetch synthetic-g36-vav-fleet
camber datasets ingest synthetic-g36-vav-fleet --store lab_store
camber datasets config synthetic-g36-vav-fleet --exercise zone-g36-fleet --store lab_store --out g36.json
camber run g36.json --out g36_out
camber datasets score synthetic-g36-vav-fleet --store lab_store --findings g36_out/findings.json
```

The exercise's config (`--exercise zone-g36-fleet`) runs ten rules over all 56 pieces of
equipment and declares which boxes each air handler serves (its `topology`). Most of the rules
are **fleet rules**: they read many pieces of equipment at once and write one finding, on
`<fleet>`, for all of them. Four variants keep one air handler each, so a fleet rule judges that
one alone:

```
camber datasets config synthetic-g36-vav-fleet --exercise zone-g36-fleet--healthy --store lab_store --out g36_healthy.json
camber run g36_healthy.json --out g36_healthy_out
camber datasets config synthetic-g36-vav-fleet --exercise zone-g36-fleet--near-miss --store lab_store --out g36_near.json
camber run g36_near.json --out g36_near_out
camber datasets config synthetic-g36-vav-fleet --exercise zone-g36-fleet--reheat --store lab_store --out g36_reheat.json
camber run g36_reheat.json --out g36_reheat_out
camber datasets config synthetic-g36-vav-fleet --exercise zone-g36-fleet--heat-cool --store lab_store --out g36_heatcool.json
camber run g36_heatcool.json --out g36_heatcool_out
```

## Steps

1. **Look before you judge.** In the trend viewer, plot `AHU__healthy`'s SAT setpoint with its
   SAT requests over one week, then its duct static setpoint with its static requests. Do the
   same for `AHU__rogue_sat`, `AHU__starved` and `AHU__not_responding`. Then plot one box of each
   with its space temperature, cooling setpoint, damper and airflow.
2. **Run the exercise's config** (the commands above). Read `sat_reset_effectiveness` and
   `static_reset_effectiveness` for each air handler: the severity, `reason`,
   `actual_sp_range`, `expected_sp_range` and `pct_high_demand_unresponsive`.
3. **The rogue zones.** Read the two rogue-zone censuses on `<fleet>`: `rogue_by_group`,
   `worst_zone`, `worst_zone_share`, and each box's `zone_request_share` and
   `zone_binding_frac`.
4. **The starved cohorts.** Read the two cohort-starvation censuses: `starved_groups`,
   `worst_group` and `group_sustained_frac`.
5. **Peers.** Read `cohort_airflow` and `cohort_space_temp`: their `outliers` and `z`.
6. **Reheat.** Read `reheat_minimization_g36` for each box and `zones_heat_cool_census` on
   `<fleet>`. Then run the `reheat` and `heat-cool` variants and read the same two rules.
7. **Score.** Run `camber datasets score` and read the rates for the three rules it scores.
8. **The healthy fleet and the near miss.** Run the `healthy` and `near-miss` variants and read
   every finding.

## Questions

1. Which air handler's resets does CAMBER flag, and why does the same fault give two different
   reasons for its SAT and its static reset?
2. `AHU__rogue_sat`'s SAT reset is effective and its setpoint spends the afternoons at 55 °F.
   Is the reset at fault? Which zone drives it, and what share of the requests does it send?
3. Which boxes does the static-pressure census flag, and why is one of them also the SAT rogue?
4. Which air handlers does each cohort-starvation census name? How do the reset findings tell
   `AHU__starved` and `AHU__not_responding` apart, and what would you check first on each?
5. Which boxes run unlike their peers on airflow, and on space temperature? Why does
   `cohort_airflow` compare each box only with the others of its air handler?
6. Which boxes does `reheat_minimization_g36` flag? What does `zones_heat_cool_census` say about
   the `reheat` boxes alone, and about the `heat_cool` boxes alone? What setpoint would you look
   at on `heat_cool`?
7. What does the label score say, and which of the faults can it not score at all?
8. On the healthy fleet alone, which rule flags something? Is it a fault? What separates the
   near miss's zone 1 from a rogue?

## What CAMBER shows

- **Findings.** `camber run` prints one line per finding: one per rule and air handler or box
  for the per-equipment rules, and one on `<fleet>` for each fleet rule.
- **Resets.** `sat_reset_effectiveness` and `static_reset_effectiveness` rebuild the setpoint a
  trim-and-respond reset should have reached from the air handler's own request counts and
  compare it with the trended setpoint. They warn when it is `stuck` (flat while the requests
  would have moved it), `not_responding` (at its energy-saving end under demand),
  `not_trimming` (at its demand end with no demand) or `diverges` (moving the wrong way).
- **Rogue zones.** The rogue-zone censuses rebuild each box's reset requests from its trends
  (SAT: 3 or 5 °F above the cooling setpoint; static: the damper over 95 % open, and the airflow
  below 70 % or 50 % of its setpoint) and flag a box that holds the largest request on at least
  half the cycles with any request **and** sends a large share of its air handler's requests
  (at least 30 %, and at least two boxes' worth). They count only cycles with the fan on and the
  building occupied.
- **Starved cohorts.** The cohort-starvation censuses flag an air handler when at least 75 % of
  its boxes request at once on at least half the cycles with any request.
- **Peers.** `cohort_airflow` and `cohort_space_temp` flag a box whose mean lies more than 3.5
  robust standard deviations from the others' median.
- **Reheat.** `reheat_minimization_g36` faults a box when, of its occupied hours with the
  reheat valve open, 40 % or more have its airflow over 1.2 times its airflow setpoint (warn at
  15 %). `zones_heat_cool_census` counts, for each occupied hour, the boxes whose valve is open
  while their airflow is over 1.1 times its setpoint, and faults when 75 % of the hours have at
  least one (warn at 25 %).
- **Score.** `camber datasets score` scores only the rules that judge one piece of equipment.

![Each of the 48 boxes placed by its share of its air handler's SAT reset requests and by how often it holds the largest request; only the equipment room of AHU__rogue_sat lies in the rogue corner, with the near miss's zone 1 and the healthy fleet's zone 1 just outside it](../img/workbook/zone-g36-fleet.png)

*From this exercise's synthetic air handlers: the SAT rogue-zone census for every box. A box is a rogue only in the shaded corner, where it both sends a large share of its air handler's requests and holds the largest request on half the cycles or more.*

## Caveats

- The data are synthetic: CAMBER's own model of a VAV system, built to show these faults. They
  show what the rules see, not how real systems behave; never cite them as evidence about a
  building.
- The resets here take one trim-and-respond step per trended hour, so the reset rules rebuild
  them exactly. A real controller steps every few minutes; on an hourly trend of one, the rebuilt
  trajectory is coarse.
- The fleet rules write one finding for everything they see. To judge one air handler, keep
  only its equipment (the variants do), and declare the served-by topology: without one, the
  censuses guess the grouping from the equipment names.
- `ZN{n}_FLOW_MIN`, the box's minimum airflow setpoint, is the airflow setpoint every rule here
  reads. The reheat rules mean the minimum; Guideline 36's static-pressure requests compare the
  airflow with the box's active setpoint, so the static census counts fewer 2- and 3-request
  cycles than the air handler did.
- The SAT census cannot count a box's 1-request cycles (its cooling loop over 95 %): no box
  trends its cooling loop.

## Going further

- Run `camber rules params sat_rogue_zone_census` and `camber rules params
  reheat_minimization_g36` to see where each threshold comes from.
- Read [the bad-box exercise](zone-bad-box.md): which of its rules are fleet rules, and what does
  its topology come from?
