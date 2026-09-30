# Instructor guide

Answer keys, discussion points and common mistakes for the [re-tuning workbook](index.md),
modelled on the instructor notes that accompany the PNNL re-tuning training. Keep this page away
from learners until they have tried each exercise.

**Every figure here comes from a CAMBER run.** Each exercise's key is pinned by the test suite
(`tests/workbook/`): on a small synthetic stand-in for the dataset in every test run, and on the
real catalog data with `pytest -m network`. When CAMBER's answer changes, those tests fail and
name the exercise; the key and this page are then updated together. A figure quoted below is
from the real data unless it says otherwise, with the CAMBER version and the commands on the
exercise page.

Each exercise's section has the same parts: **Answer key**, **Discussion points** and
**Common mistakes**.

## Air side

<!-- BEGIN air-sat-reset -->

### `air-sat-reset`: Supply-air temperature reset

_Placeholder: the key is written with the exercise (#80)._

<!-- END air-sat-reset -->

<!-- BEGIN air-static-pressure -->

### `air-static-pressure`: Static pressure reset and the damper census

_Placeholder: the key is written with the exercise (#80)._

<!-- END air-static-pressure -->

<!-- BEGIN air-heat-cool -->

### `air-heat-cool`: Simultaneous heating and cooling, and a leaking valve

_Placeholder: the key is written with the exercise (#80)._

<!-- END air-heat-cool -->

<!-- BEGIN air-economizer -->

### `air-economizer`: a stuck outdoor-air damper and missed free cooling

Figures from the real `lbnl-sdahu` default subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](air-economizer.md#setup).

**Answer key**

1. *Which runs does CAMBER flag for too much outdoor air?* `AHU__damper_stuck_075` and
   `AHU__damper_stuck_100_short`: `outdoor_air_fraction` is a **fault** on both, and so is
   `economizer_high_limit`. The damper stuck at 75 % brings in a median 67.5% outdoor air in
   cooling weather; stuck at 100 %, all of it. Above the 60 °F high limit neither is ever
   locked out to minimum.
2. *What does the fault-free unit do?* It sits at its own design minimum, a 1.6 % OA fraction,
   in cooling weather: no OA-fraction or high-limit finding. Its *annual* median OA fraction is
   much higher (about 40 %), because it economizes whenever the outdoor air is below 60 °F. That
   is correct operation, and why the rule judges cooling weather against the unit's own minimum.
3. *Which stuck dampers do the OA-fraction rules miss, and why?* `AHU__damper_stuck_010` and
   `AHU__damper_stuck_025`. Stuck near the minimum, they admit about as much outdoor air as a
   healthy unit in cooling weather (1.6 % and 4.4 %), so there is no excess to find.
4. *Where do they show up instead?* In `free_cooling_missed`: both are a **fault**, with
   mechanical cooling running in 49% of the free-cooling hours (outdoor air below 60 °F),
   against 17.5% for the fault-free unit (a **warn**). The damper can't open to cool for free,
   so the coil does the work.
5. *Label score.* Against the dataset's labels, `outdoor_air_fraction` finds 2 of the 4 stuck
   dampers with no false alarm: TPR 50%, FPR 0 %. Over all six scored runs (the four dampers,
   the leak and the fault-free run) the overall detection is TPR 40%, FPR 0 %: the exercise's
   config leaves out `leaking_valve`, so the leak run counts as missed.

**Discussion points**

- A stuck damper is two different faults depending on *where* it sticks: open wastes cooling
  in hot weather, closed wastes free cooling in cool weather. PNNL's economizer guide asks both
  questions; each CAMBER rule answers one of them.
- `free_cooling_missed` is not one of the dataset's declared detector targets, so
  `camber datasets score` doesn't count it. Ask why: it also warns on the fault-free unit
  (17.5%). A detector with a non-zero
  baseline has to be read against the unit's own fault-free run, which is what question 4 does.
- The design minimum matters. With a generic 20 % minimum the fault-free unit would look
  under-ventilated all summer; the config uses this unit's own measured minimum (1.6 %). Point
  to the `_comment` in the exercise config and to the minimum outdoor-air guide.

**Common mistakes**

- Reading the annual median OA fraction (about 40 % on the fault-free unit) as excess outdoor
  air. Excess outdoor air is judged on cooling-weather samples only.
- Concluding that the 10 % and 25 % runs are healthy because the OA-fraction rules are quiet.
- Treating the `warn` on the fault-free unit's `free_cooling_missed` as a fault to fix, without
  comparing it with the faulted runs.
- Running the dataset's default config (`camber datasets config lbnl-sdahu` without
  `--exercise`): it has no `free_cooling_missed`, so question 4 has nothing to read.

<!-- END air-economizer -->

<!-- BEGIN air-scheduling -->

### `air-scheduling`: Scheduling: 24/7 operation, setback and after-hours load

_Placeholder: the key is written with the exercise (#80)._

<!-- END air-scheduling -->

## Terminal units and ventilation

<!-- BEGIN zone-reheat-overcooling -->

### `zone-reheat-overcooling`: the reheat penalty and overcooling at minimum airflow

Figures from the real `lbnl-fpu` default subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](zone-reheat-overcooling.md#setup).

**Answer key**

1. *Does the fault-free box overcool its zone?* Yes. `overcooling_min_flow` is a **warn** on
   `PFPU__fault_free`: in 85% of occupied hours the box sits at its minimum airflow with the zone
   already at or below its cooling setpoint. That minimum is 48% of the box's peak airflow. The
   box is sized for the summer peak, and its minimum is set high enough to overcool the zone
   most of the year.
2. *How often does it reheat?* The reheat valve is open in 11% of occupied hours, every one of
   them into centrally cooled air at minimum airflow (`reheat_penalty` **warn**; the valve
   averages about 21 % when open, on winter mornings). The zone needs the heat only because the
   minimum airflow keeps delivering cold air it does not need. That is the reheat penalty PNNL's
   zone guide sets out to reduce, and the fix is a lower minimum (within the ventilation
   requirement), not a repair.
3. *The stuck-open box.* `airflow_tracking` is a **fault** on `PFPU__VAVDMPRStuck_100pct`: the
   airflow is off its setpoint in every active hour, by 291% on average (always above it). The
   box reheats in 99% of occupied hours, and even so the zone is too cold in 10% of them
   (`unmet_setpoint_hours` **warn**). Half open, the airflow is off by 168 % and the box reheats
   in 67% of occupied hours, but the zone stays in band.
4. *Why is `overcooling_min_flow` quiet on it?* The rule looks for a box *at its minimum* that
   still overcools, which is a setting problem. The stuck box is never at its minimum: its airflow
   is far above its setpoint all the time. Its overcooling is a hardware fault, and
   `airflow_tracking` is the rule that names it.
5. *The valve stuck shut.* It is not wasting reheat. It delivers none. The rule reads the
   controller's reheat *demand*, which sits near full (about 93 % when open) because the zone is
   cold and the valve cannot respond. The discharge air still rises a few degrees, because the
   parallel box's fan mixes in warm plenum air, so the rule's valve-versus-discharge cross-check
   does not catch it. `overcooling_min_flow` also rates it a **fault** for the same reason.
   [`zone-reheat-saturated`](zone-reheat-saturated.md) shows what it really is.
6. *Which box is under test?* The south-zone box (`_S` columns). Comparing each faulted run's
   columns with the fault-free run's shows that only the `_S` box's columns change. CAMBER's
   mapping (`mappings/lbnl_fpu.json`) documents this. It mapped the west box until 0.82 and
   scored a healthy box.
7. *Label score.* `airflow_tracking`, the dataset's only declared detector, finds both stuck
   dampers and never fires on the others: TPR 100%, FPR 0 %. The overall detection is TPR 50%,
   FPR 0 %: the stuck and the leaking reheat valve count as missed, because the dataset declares
   no detector for them.

**Discussion points**

- Two causes, one symptom. A high minimum and a stuck damper both push cold air into a zone that
  must then be reheated. PNNL's zone guide asks about minimum airflow settings, and chapter 7
  about boxes that do not respond. Draw out which rule answers which question.
- The fault-free run is not "no findings". A simulated building designed with a conventional
  minimum still shows a small reheat penalty. That is the re-tuning opportunity, and it is why
  the rules warn rather than stay silent.
- The reheat signal is a demand. Ask what a measured valve position or a discharge-air
  temperature after a series box would change.
- Minimums are also a ventilation question: lowering them needs the zone's 62.1 minimum. See
  [`zone-min-oa`](zone-min-oa.md).

**Common mistakes**

- Reading the `reheat_penalty` fault on the stuck-shut valve as wasted energy. The valve gives
  no heat. The demand is saturated because it cannot.
- Expecting `overcooling_min_flow` to catch the stuck-open damper, and concluding the rule is
  broken when it is quiet.
- Treating the half-open damper as harmless because the zone stays in band. It still reheats in
  two thirds of occupied hours.
- Reading the fault-free box's warnings as faults to repair. They are settings to re-tune.
- Scoring the leak and the stuck valve as `airflow_tracking` misses. They are outside what the
  dataset declares that detector for.

<!-- END zone-reheat-overcooling -->

<!-- BEGIN zone-bad-box -->

### `zone-bad-box`: one bad box in a fleet

Figures from the real `ornl-frp-vav` default subset (test set 3), CAMBER 0.97.0-dev, with the
commands on the [exercise page](zone-bad-box.md#setup).

**Answer key**

1. *Which box is under test?* The box of room 205 (`RTU_VAV_205__d3_<day>`). On every faulted
   day its damper is a flat line at the test position (0, 20, 40, 60, 80 or 100 %) through the
   occupied hours. On the fault-free day it modulates like its nine neighbours, whose dampers
   all move through the day.
2. *Airflow among the neighbours.* On the 0 % day it delivers the least air of the ten boxes;
   on the 100 % day, the most. Compared with its own fault-free day, it starves in the first
   case and floods in the second. The raw airflows of the ten boxes differ widely, because the
   rooms differ in size, so rank and shape are the useful comparisons.
3. *When does `unmet_setpoint_hours` flag it?* On the 20 % day (a **fault**, unmet 53% of
   occupied hours, mostly too hot) and the 40 % day (a **fault**, too hot). It is quiet on the
   0, 60, 80 and 100 % days. Stuck shut, the room still stayed in band that day: its load was
   small enough to do without the air. Stuck 60 % or more open, it got more cold air than it
   needed and still stayed in band. A stuck damper only upsets comfort when the day's load needs
   the airflow it can no longer give, so a one-day test catches it only on some days.
4. *Flagged on the fault-free day too.* Yes, a **fault** with the room too hot in 22% of
   occupied hours. Room 205 is the building's warm room: its afternoons run over the cooling
   setpoint even when its box works. That is a real finding, likely a load or a capacity limit,
   but it is not the fault under test.
5. *The cohort rules.* `sat_rogue_zone_census` is a **warn**. Grouping each day's ten boxes under
   that day's rooftop unit, it names box 205 as the zone dragging the supply-air reset on the
   fault-free, 20 % and 40 % days, and box 105 on the 80 % day. `sat_cohort_starvation` is `ok`:
   no day's cohort as a whole is starved, so the problem is one box, not the rooftop unit. The
   census says which zone is driving demand, not why. On the fault-free day it points at the
   warm room, not a fault.
6. *The rooftop unit.* As the stuck damper goes from shut to fully open, the unit's supply
   airflow rises at every step and its duct static pressure falls. A fixed-speed fan moves more
   air through a more open system. Each position is a different day, so the weather moves these
   numbers too, but the direction holds at every step.
7. *Label score.* `unmet_setpoint_hours` catches 2 of the 6 stuck days and fires on the fault-free
   day too: TPR 33%, FPR 100 %. A comfort rule detects a stuck damper only on the days the stuck
   position hurts comfort. It cannot tell a stuck box from a warm room. The dataset logs no
   airflow setpoint, so `airflow_tracking`, the rule that finds stuck dampers on `lbnl-fpu`,
   cannot run.
8. *Room 102.* No. Its box is not under test, and the room is too cold on every day, fault-free
   included (a **fault** each day). It is a cold room in this building. The reheat is electric
   and not trended as a valve, so CAMBER cannot say whether it was maxed out (see
   [`zone-reheat-saturated`](zone-reheat-saturated.md)).

**Discussion points**

- Chapter 7's advice to trend damper position and airflow for every box is the whole exercise.
  A box that does not move while its neighbours do is the cheapest fault signature there is,
  and no comfort rule reproduces it.
- The zone cohort needs a common basis. Each day's boxes are compared under that day's rooftop
  unit (the naming topology), never across days. Ask what goes wrong when a census pools
  boxes from different days (the dataset config's `_comment` explains why `cohort_airflow` is
  left out).
- The labelled "fault-free" day is not a trouble-free building. Discuss room 205's warm
  afternoons and room 102's cold mornings as findings in their own right.

**Common mistakes**

- Concluding that the 0 % and 60–100 % days are healthy because `unmet_setpoint_hours` is quiet.
- Reading the rogue-zone census on the fault-free day as a false alarm. It correctly names the
  zone driving the reset, which happens to be a warm room, not a broken box.
- Comparing raw airflows across rooms of different sizes and calling the smallest box the
  starved one.
- Blaming the rooftop unit because its airflow and static change. They follow the stuck box;
  the cohort-starvation census says the cohort as a whole is fine.

<!-- END zone-bad-box -->

<!-- BEGIN zone-reheat-saturated -->

### `zone-reheat-saturated`: a zone below setpoint with its reheat maxed out

Figures from the real `lbnl-fpu` and `ornl-frp-vav` default subsets, CAMBER 0.97.0-dev, with the
commands on the [exercise page](zone-reheat-saturated.md#setup).

**Answer key**

1. *Which run?* `PFPU__ReheatVLVStuck_0pct`: `reheat_capacity_shortfall` is a **fault**. The zone
   sits more than 1.5 °F below its heating setpoint with the reheat demand at 90 % or more in
   31.8% of occupied samples, 663 h over the year, a median of 4.5 °F below setpoint. It happens
   in the heating season, when the box needs heat and gets none.
2. *Capacity or airflow?* Capacity. In the shortfall samples the airflow is at 100 % of its
   setpoint, so the box delivers the air it asks for, and the discharge air is only 60.8 °F,
   barely above the supply air. The rule names `capacity` as the likely cause: look at the coil,
   the valve and the hot water, not the damper.
3. *The other two rules.* `unmet_setpoint_hours` is a **fault**, with the zone too cold in 37% of
   occupied hours. It counts the complaint but not the cause. `overcooling_severity` reports no
   overcooling (`info`). It sets the cold, reheat-saturated samples apart as a heating shortfall,
   graded `fault`, in 30% of occupied samples. "Overcooled" would send you to the cooling side
   (minimum airflow, supply-air temperature), when the zone is cold because heat cannot get in.
4. *Why does the valve read fully open?* The mapped signal is the controller's reheat demand,
   not the valve's position. The controller keeps asking for full heat because the zone stays
   cold, and the valve stuck shut cannot respond. `reheat_penalty` reads that demand as reheat
   delivered in 51% of occupied hours and rates it a **fault**. The parallel box's fan mixes in
   warm plenum air, so the discharge air rises a few degrees, enough to pass the rule's
   valve-versus-discharge check. On site: stroke the valve, check the discharge air at full
   demand, and check the hot-water supply to the coil.
5. *Heat to spare.* The fault-free run, the leaking valve and the half-open damper have no
   saturated shortfall at all. The fully open damper has a little (under 2 % of samples, `ok`):
   in a few cold hours its extra cold air outruns the reheat.
6. *ORNL, fault-free day.* Several rooms are too cold on a normal day. Room 102 is below its
   heating setpoint in 33% of occupied hours (`unmet_setpoint_hours` **fault**), and rooms 104,
   105, 106, 204 and 206 are too cold for part of the day too. `overcooling_severity` rates room 104 a
   **fault**, up to 4.6 °F below its heating setpoint, and room 102 `info`.
7. *Why no reheat verdict?* The boxes' reheat is electric and published only as energy per
   minute, not as a valve or a command, so it is not mapped. `reheat_capacity_shortfall` needs a
   reheat valve and produces no finding for any box. Without a reheat signal,
   `overcooling_severity` cannot set a heating shortfall apart from overcooling, so it calls room
   104 "overcooled". Ask the operator to trend each box's reheat command (or stage), its
   discharge-air temperature and its airflow setpoint.

**Discussion points**

- A saturated reheat valve is the end of the story that
  [`zone-reheat-overcooling`](zone-reheat-overcooling.md) begins. PNNL's zone guide asks whether
  zones hold their heating setpoints. This exercise adds "could the box have done more?"
- The same cold room reads as three different problems (unmet hours, overcooling, a heating
  shortfall) depending on what the data carry. Missing data changes the diagnosis, not only the
  confidence.
- Demand versus position: a controller's output tells you what it wanted, not what happened. Pair
  every demand with a response (discharge air, airflow) before trusting it.

**Common mistakes**

- Calling the ORNL rooms "overcooled" and lowering their airflow. With no reheat signal CAMBER
  cannot tell, and the rooms may be short of heat.
- Reading `reheat_penalty` on the stuck-shut valve as an energy saving opportunity.
- Treating `unmet_setpoint_hours` as a diagnosis. It measures a symptom.
- Expecting a finding on every box. A rule that lacks its input stays silent. Check which rules
  produced findings, not only which fired.

<!-- END zone-reheat-saturated -->

<!-- BEGIN zone-dcv -->

### `zone-dcv`: is outdoor air following occupancy?

Figures from the real `finnish-dcv`, `b4b-windesheim` and `sdu-ou44` default subsets (each the
whole published record), CAMBER 0.97.0-dev, with the commands on the
[exercise page](zone-dcv.md#setup).

**Answer key**

1. *The Finnish ventilation test.* Yes: "OA rises with demand (DCV functioning)". CO₂ is 332 ppm
   higher when the supply is raised than when it sits at its floor, compared within the same hour
   of day (correlation 0.82, a diagnostic only).
2. *Why a warn?* The finding also reports outdoor air held above the floor at low demand in
   100 % of low-CO₂ samples. The config's floor is the documented base flow, 3 l/s (6.36 cfm), but
   the room's measured base is 4 l/s (a documented data issue). The rule is reporting the gap
   between the documented law and the trend. That is the point of checking a sequence against
   its data, not a malfunction.
3. *The training records.* Both read "OA modulates, but not with demand" (`info`). They were run
   under assorted ventilation strategies, not a DCV law, so outdoor air moved for other reasons.
   CAMBER describes what the data show and does not call a non-DCV strategy a broken DCV.
4. *Which CO₂ sensor?* Room 917810 reads "functioning" on both sensors (lifts of 139 ppm on the
   building-system sensor and 326 ppm on the desk sensor). Room 999169 depends on the sensor: on
   its desk sensor it functions (a 212 ppm lift), on the building-system sensor "OA modulates,
   but not with demand" (a 12 ppm lift). The valve opens on a morning clock *and* on CO₂. Within
   each hour the clock cancels, and the remaining lift is only as good as the sensor.
5. *Room 925038.* Its valve column is an assumed constant in the published data, not a
   measurement, so it is not mapped. With no outdoor-air signal the DCV rule does not evaluate
   the room: no finding at all.
6. *Over-ventilated and functioning?* No contradiction. The rooms are occupied only a small share
   of the time, and the valve's 20 % minimum keeps CO₂ near outdoor in 73 to 86% of occupied hours
   (`co2_ventilation` **warn** on all five). DCV responds to demand. Whether the minimum is
   higher than the rooms need is a separate, energy question.
7. *The Danish rooms.* `ROOM1`, the lecture room, functions: a 181 ppm lift, correlation 0.89.
   `ROOM2` and `ROOM3` are "DCV not judged -- CO₂ never reached the level where DCV should
   respond" (reason `demand_below_engage`). The study zones never got busy enough to test the
   DCV. That is neither good nor bad news about the controls, only an absence of evidence.
   `ROOM2` is also over-ventilated (CO₂ near outdoor in 66% of occupied hours).
8. *Which sensor to trust?* Check each sensor against outdoor air when the room is empty (does it
   settle near 400–450 ppm?), against the other sensor when the room is busy, and for location
   (a desk sensor sees the occupants, a return-air or duct sensor sees a mix). Calibrate or
   replace before re-tuning the sequence on it.

**Discussion points**

- The minimum outdoor-air guide asks whether outdoor air is reduced when spaces are lightly
  occupied. These three datasets give a yes, a "depends on the sensor" and a "can't tell". A
  verification tool that never says "can't tell" is guessing.
- Clock versus demand: a valve on a time clock correlates with occupancy because people also
  follow the clock. Taking the lift within the hour of day is what separates the two. Ask the
  class to predict what a pooled comparison would say for room 999169.
- The per-finding caveat about return-air CO₂ is written for air handlers. Here each "unit" is a
  room with its own sensor, so the caveat does not apply. Reading caveats critically is part of
  the job.

**Common mistakes**

- Treating "not judged" as a failed DCV and recommending repairs.
- Reading the Finnish test's **warn** as a DCV fault. It is the base flow above the documented
  floor.
- Trusting whichever sensor gives the answer you expected.
- Reading "over-ventilated" as unsafe. It is an energy observation. Under-ventilation is the
  safety question, and no room here is under-ventilated.

<!-- END zone-dcv -->

<!-- BEGIN zone-min-oa -->

### `zone-min-oa`: minimum outdoor air and 62.1 system ventilation

Figures from the real `lbnl-b59` default subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](zone-min-oa.md#setup). The 62.1 procedure follows the public sources listed in
[VENTILATION.md](../VENTILATION.md#system-level-vrp-multiple-zone-systems) (the free 62.1-2016
Addendum f and secondary sources); no standard text is quoted here.

**Answer key**

1. *The requirement.* The same Vot for each unit: 1,421 cfm. By hand: the breathing-zone outdoor
   air is 5 cfm/person × 63 + 0.06 cfm/ft² × 12,513 ft² ≈ 1,066 cfm. With no system population,
   D = 1, so the uncorrected intake is also about 1,066 cfm, and the simplified system efficiency
   for D ≥ 0.60 is 0.75. That gives 1,066 / 0.75 ≈ 1,421 cfm.
2. *Measured, and the verdict.* The flow stations read 5,558 to 7,479 cfm of outdoor air (median
   over occupied, fan-on hours from April 2020), ratio 3.91 to 5.26 against Vot. Every unit is
   **over**-ventilated (a conditioning-energy penalty).
3. *Why a warn?* Over-ventilation is a `warn` (an energy penalty, not a safety problem), and the
   severity is capped at `warn` anyway, because every input is assumed. The finding's caveats
   say three things. The requirement rests on assumed area and population. There is no system
   population, so D = 1, the most conservative case. And the topology lists CO₂ zones under each
   unit that carry no ventilation inputs, so Vot covers the listed zones only. Here the lumped
   zone already stands for the unit's whole share of the floor.
4. *The zones' CO₂.* `co2_ventilation_system` flags all 11 zones as over-ventilated: CO₂ near
   outdoor in nearly every non-economizer occupied hour, with zone medians of 419 to 429 ppm.
   Two independent measurements, flow and CO₂, say the same thing.
5. *DCV.* "DCV not judged" on all four units: the zones' CO₂ never varies enough to test whether
   outdoor air follows it (reason `no_demand_variation`). This building has no DCV, and CAMBER
   does not call it functioning. The specificity lesson: a checker that never refuses would have
   given it a verdict.
6. *How wrong would the inputs have to be?* To read "adequate" (a ratio of 1.5 or less), Vot
   would have to be about 2.6 to 3.5 times larger. That means roughly three times the assumed
   population or floor area. Neither is plausible for this office. Giving a system population
   goes the *other* way: with Ps = 32 per unit, D ≈ 0.51, Ev ≈ 0.67 and Vot falls slightly, to
   1,366 cfm. The ratios become 4.07 to 5.48, so the verdict does not change.
7. *Before lowering the minimum.* Ask for the design ventilation schedule (zone areas,
   populations, space types), confirmation of the flow stations' calibration, the unit's minimum
   damper setting and how it maps to flow, the minimum at part-load fan speed, and the
   wildfire-smoke and pandemic operating modes the owner wants to keep. Lower the setpoint in
   steps and verify with the zone CO₂.

**Discussion points**

- The minimum outdoor-air guide's point, that a minimum set above what the building needs costs
  heating and cooling all year, shows clearly here. This is also where assumptions must be written
  down: the config's `_comment` and the finding's caveats are part of the answer.
- System versus zone: judged against one zone's requirement, a multiple-zone unit looks
  over-ventilated too easily. The system procedure (Vou, D, Ev) is what applies to an air handler
  serving many zones. Walk through why D and Ev move in opposite directions.
- 2020 was not a normal year (shelter-in-place, wildfire smoke). Discuss whether the measured
  flows represent the building's normal minimum.

**Common mistakes**

- Presenting the ratio as a compliance finding. The inputs are assumptions, and CAMBER caps the
  severity to say so.
- Using the unit's 20,000 cfm design supply, or the descriptor's 5,000 cfm design minimum, as the
  62.1 requirement.
- Reading the DCV "not judged" as a DCV fault in a building that has no DCV.
- Lowering the minimum on the strength of the CO₂ readings alone, without the design schedule.

<!-- END zone-min-oa -->

## Central plant

<!-- BEGIN plant-chiller-efficiency -->

### `plant-chiller-efficiency`: Chiller efficiency and fouling

_Placeholder: the key is written with the exercise (#82)._

<!-- END plant-chiller-efficiency -->

<!-- BEGIN plant-cooling-tower -->

### `plant-cooling-tower`: Cooling tower: approach and fan effort

_Placeholder: the key is written with the exercise (#82)._

<!-- END plant-cooling-tower -->

<!-- BEGIN plant-chw-reset-pumping -->

### `plant-chw-reset-pumping`: Chilled-water reset and pumping

_Placeholder: the key is written with the exercise (#82)._

<!-- END plant-chw-reset-pumping -->

<!-- BEGIN plant-boiler -->

### `plant-boiler`: Boiler plant

_Placeholder: the key is written with the exercise (#82)._

<!-- END plant-boiler -->

<!-- BEGIN plant-sensor-vs-equipment -->

### `plant-sensor-vs-equipment`: Plant sensor faults vs equipment faults

_Placeholder: the key is written with the exercise (#82)._

<!-- END plant-sensor-vs-equipment -->

## Data quality, energy charting, M&V and the capstone

<!-- BEGIN data-trend-quality -->

### `data-trend-quality`: Are the trends good enough?

_Placeholder: the key is written with the exercise (#83)._

<!-- END data-trend-quality -->

<!-- BEGIN data-energy-charting -->

### `data-energy-charting`: Energy charting

_Placeholder: the key is written with the exercise (#83)._

<!-- END data-energy-charting -->

<!-- BEGIN mv-baselines -->

### `mv-baselines`: Change-point baselines and M&V

_Placeholder: the key is written with the exercise (#83)._

<!-- END mv-baselines -->

<!-- BEGIN capstone -->

### `capstone`: Capstone: RCx report, walk-down, re-tuning plan and verification

_Placeholder: the key is written with the exercise (#83)._

<!-- END capstone -->
