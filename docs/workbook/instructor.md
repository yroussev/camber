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

Figures from the real `lbnl-sdahu`, `lbnl-ddahu` and `irish-ahu` default subsets, CAMBER
0.98.0-dev, with the commands on the [exercise page](air-sat-reset.md#setup).

**Answer key**

1. *Is the single-duct unit's supply air reset?* No. `supply_air_reset` is a **warn** on
   `AHU__fault_free`: "not reset (setpoint flat)". The rule reads the trended setpoint, which
   never moves (a range of 0.00 °F all year at 55.25 °F), so the shape of the supply air does not
   matter. Against the Guideline 36 outdoor-air target the supply air is colder in 71% of the
   occupied running hours, by a mean 6.0 °F (`supply_air_reset_compliance`, a **warn**). The
   faulted runs read the same: the sequence has no reset.
2. *The dual-duct cold deck?* Not reset either: `supply_air_reset` is a **warn**, "no reset (SAT
   pinned low at ~55 F regardless of OAT)", and the supply air is below the Guideline 36 target
   in 66% of the hours. No setpoint is mapped, so the verdict comes from the supply air itself.
   The dataset's documentation describes a 60 °F cold-deck setpoint in economizer weather; the
   data hold 55 °F in every row (the data issue *The 60 F economizer supply-air setpoint is not in
   the data*). The published description and the published data disagree; CAMBER reads the data.
3. *The Irish unit?* `supply_air_reset` is **ok**, "reset present": over the cooling hours its
   supply air *falls* as the outdoor air warms, by -0.14 °F per °F, and
   `supply_air_reset_compliance` is **ok** (it tracks the target). Falling with outdoor air is
   the right direction for a cooling supply-air reset: warm weather means more cooling load, so
   Guideline 36 lowers the supply air; cool weather lets it rise and saves reheat. The evidence
   is weak, though: the fit is loose and no setpoint is trended, so the finding carries a caveat
   asking for the setpoint. A negative slope can also be a fixed setpoint that the coil only just
   holds in hot weather.
4. *Which run fails to hold its setpoint?* `AHU__damper_stuck_075`: `supply_air_control` is a
   **fault**, too cold in 31% of its occupied running hours. With the damper stuck at 75 % the unit takes
   in cold outdoor air it cannot shut out, and it has no heating coil to warm it back up.
5. *The fault-free unit's too-warm hours?* `supply_air_control` is **ok** on `AHU__fault_free`:
   3% of its occupied running hours are more than 2 °F above the setpoint
   (`occupancy_gate: trended occupancy`). Counting every fan-on hour instead, 78% of the
   too-warm hours are unoccupied: the fan cycling in the simulation's unoccupied mode, with the
   damper shut and warm return air. That is not an occupied-control fault, which is why the rule
   judges only occupied hours when the unit trends occupancy. With `occupancy_gate: "off"` the
   same unit reads a **warn** at 12%, the verdict CAMBER gave before 0.98.

**Discussion points**

- Two different questions: is there a reset (shape), and is the supply air colder than it needs
  to be (target)? A unit can fail one and pass the other. Map both to the
  [discharge-air temperature guide][pnnl-guide-discharge-air-temp]'s questions on reset and on
  what drives it.
- A trended setpoint beats inference. On the single-duct unit the setpoint settles it; on the
  Irish unit only the supply air is trended and CAMBER says so.
- Documentation versus data (question 2): re-tuning starts from what the trends show, and a
  published sequence can be wrong.

**Common mistakes**

- Reading a positive slope against outdoor air as a reset. A cooling supply-air reset moves the
  other way; supply air that rises with the weather is supply air losing to the load.
- Treating the Guideline 36 gap as a fault. It is an energy opportunity, to weigh against zone
  comfort and humidity.
- Blaming the controls for question 4. The controller is doing its best; the damper is the
  fault, and this unit cannot heat.

[pnnl-guide-discharge-air-temp]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84186.pdf "Building Re-Tuning Training Guide: AHU Discharge-Air Temperature Control (PNNL-SA-84186)"

<!-- END air-sat-reset -->

<!-- BEGIN air-static-pressure -->

### `air-static-pressure`: Static pressure reset and the damper census

Figures from the real `lbnl-sdahu` and `ornl-frp-vav` default subsets, CAMBER 0.98.0-dev, with
the commands on the [exercise page](air-static-pressure.md#setup).

**Answer key**

1. *Is the single-duct static setpoint reset?* No. `static_pressure_reset` is a **warn** on
   `AHU__fault_free`: the setpoint never moves (a range of 0.00 in. w.c.), held at
   1.607 in. w.c. all year: "flat (no trim-and-respond reset)".
2. *Why no finding on the faulted runs?* Their published setpoint is a placeholder
   (-400.25 in every faulted run), which the catalog masks to missing at ingest. With no
   setpoint the rule has nothing to judge, so it reports nothing rather than reading a negative
   setpoint. Only the fault-free run, and the fault-free part of the spliced onset run, carry the
   real setpoint.
3. *What does the census say?* `damper_census` is a **fault**: on the fault-free day all 10 boxes
   have a median damper opening below 50 % (a fleet median of 39%) over the hours the boxes'
   own trended occupancy marks occupied, none is near fully open:
   "static likely too high". The proposal is a trim-and-respond static reset: lower the setpoint
   while no box asks for more air, raise it when one does, so that the most-open box ends up
   nearly fully open.
4. *What else could throttle every box?* Light load on the test day (only the minimum airflow
   was needed); oversized boxes or minimum-airflow settings; a static sensor that reads low, so
   the fan makes more pressure than the trend shows; and the test design: the publisher says only
   the box under test was actively controlled. The census is a screening signal that tells you
   where to look.

**Discussion points**

- The two halves need two datasets: the simulated unit trends a setpoint but no box dampers; the
  real building trends every damper but not the setpoint. Real projects face the same gaps; map
  the missing points against the [static pressure guide][pnnl-guide-static-pressure]'s trend
  list.
- A placeholder is not a setpoint (question 2): CAMBER masks what it can prove is not data,
  and says so in the data issues.
- A flat setpoint on a simulation is the designer's choice; the re-tuning question is what
  resetting it would save.

**Common mistakes**

- Reading "no finding" on the faulted runs as "no problem". The rule had nothing to judge.
- Pooling boxes from different test days or different air handlers into one census.
- Assuming the census judges an office schedule. Since 0.98 each box's occupied hours come from
  its own trended occupancy point, so a weekend test day gets a census too (the tests ran
  07:00-22:00 every day); only a box with no occupancy trended falls back to CAMBER's assumed
  weekday 07:00-18:00 schedule, and the finding's `occupancy_gate` says which applied.

[pnnl-guide-static-pressure]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84187.pdf "Building Re-Tuning Training Guide: AHU Static Pressure Control (PNNL-SA-84187)"

<!-- END air-static-pressure -->

<!-- BEGIN air-heat-cool -->

### `air-heat-cool`: Simultaneous heating and cooling, and a leaking valve

Figures from the real `lbnl-sdahu`, `lbnl-ddahu` and `irish-ahu` default subsets, CAMBER
0.97.0-dev, with the commands on the [exercise page](air-heat-cool.md#setup).

**Answer key**

1. *Does CAMBER flag the leak?* No. `leaking_valve` is **ok** on `AHU__coi_leakage_010`, and the
   label score gives `leaking_valve` TPR 0%, FPR 0 %: the one leak run is missed, and nothing
   else is flagged.
2. *Compare the two runs.* With the valve commanded shut and the fan running, the fault-free
   unit's supply air sits a median +1.0 °F above its mixed air (the fan's heat); the leak run's
   sits at -0.1 °F. The leak takes about 1 °F out of the air, all day, in every hour the valve is
   meant to be shut. The rule only calls a cooling leak when the supply air is more than 3 °F
   below the mixed air, a margin meant to ride over sensor error; this leak is real but smaller
   than the margin. You see it by comparing the unit with its own fault-free behaviour.
3. *Is the dual-duct unit fighting itself?* `simultaneous_heat_cool` is a **fault**: both valves
   open in 27% of occupied hours. But the two coils sit in different air streams: the hot deck
   heats while the cold deck cools, by design. The energy question is the mixing at the terminal
   boxes, not coil fighting. The rule's caveat says it could not rule out dehumidification with
   reheat, because no cooling-coil leaving temperature or humidity is trended, and asks you to
   declare the sequence.
4. *Dehumidification or fighting?* Ask for the cooling coil's leaving-air temperature and a
   humidity (return or outdoor, to form a dew point). Dehumidification runs the coil below the
   entering air's dew point and reheats *after* it; a coil leaving above the dew point removes no
   moisture, so reheating after it is fighting. Also ask for the written sequence.
5. *The Irish unit?* `simultaneous_heat_cool` is **ok**: the coils are never open together (0.0 %
   of hours). `leaking_valve` is **ok**, judged on each coil's own leaving-air sensor rather than
   the supply air: a heating-leak signature in 9.8% of the hours with both valves shut, just
   under the rule's 10 % warn line.

**Discussion points**

- A rule with a fixed margin misses small faults; a unit's own baseline catches them. Ask where
  the class would put the margin, knowing the sensors' accuracy. Map it to the
  [AHU heating and cooling guide][pnnl-guide-ahu-heat-cool]'s question on valves that do not
  shut off.
- "Both valves open" is a symptom, not a diagnosis: the system type (dual-duct) and the sequence
  (dehumidification) decide.
- The Irish unit's valves were replaced in May 2022: the going-further step splits its record
  there.

**Common mistakes**

- Reading the leak run's **ok** as "no leak". The label says leak, and question 2 shows it.
- Calling the dual-duct unit's 27% a fault to fix at the coils.
- Judging a heating leak on supply air downstream of the fan without allowing for fan heat.

[pnnl-guide-ahu-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88359.pdf "Building Re-Tuning Training Guide: AHU Heating and Cooling Control (PNNL-SA-88359)"

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
6. *The Irish unit (real `irish-ahu` data, whole record)?* `outdoor_air_fraction` is a **warn**:
   in cooling weather (outdoor air above 70 °F) the outdoor-air fraction is above the unit's
   4.1 % minimum in 42% of the hours. `economizer_high_limit` is a **warn**: above the 65 °F high
   limit, with the outdoor air no cooler than the return air, the damper is not back at minimum
   in 21% of the samples. `free_cooling_missed` is **ok**: mechanical cooling ran in 8.0% of
   the free-cooling hours. Raise the excess outdoor air in warm weather as a question for the
   site, with the dates; the free cooling is being used.
7. *Is it the COVID-19 period?* Not only. Splitting the record with `source.start` / `.end`: the
   years before it (2017-06 to 2020-02) show excess outdoor air in 53% of the cooling-weather
   hours, a **fault** on their own, against 45% in the documented 100 % outdoor-air period
   (2020-08 to 2021-11). Cooling weather is rare in Ireland, so each period has few such hours.

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
- On a real unit (questions 6 and 7) the history matters: a documented decision explains part
  of the excess outdoor air, and the rest is a lead to take back to the site.

**Common mistakes**

- Reading the annual median OA fraction (about 40 % on the fault-free unit) as excess outdoor
  air. Excess outdoor air is judged on cooling-weather samples only.
- Concluding that the 10 % and 25 % runs are healthy because the OA-fraction rules are quiet.
- Treating the `warn` on the fault-free unit's `free_cooling_missed` as a fault to fix, without
  comparing it with the faulted runs.
- Running the dataset's default config (`camber datasets config lbnl-sdahu` without
  `--exercise`): it has no `free_cooling_missed`, so question 4 has nothing to read.
- Reading the Irish findings as scored detections. The data are unlabelled; a documented
  operating decision (100 % outdoor air) is not a fault, and a finding is a question for the site.

<!-- END air-economizer -->

<!-- BEGIN air-scheduling -->

### `air-scheduling`: Scheduling: 24/7 operation, setback and after-hours load

Figures from the real `ornl-frp-ops` default subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](air-scheduling.md#setup).

**Answer key**

1. *The 24/7 baseline?* `night_weekend_setback` is a **fault** on `RTU__base_heating`: the fan
   runs 100 % of the unoccupied hours, as in the occupied ones: "setback MISSING/weak". That is
   the test's design, and the finding a re-tuning visit would make on a real building.
2. *Why is the setback effective?* On `RTU__sb_heating` the fan still runs in 56% of the
   unoccupied minutes, which fails the runtime test. But it *cycles*: in the night hours it runs
   at all, its mean duty is 71%, and while it runs the return air reads 63.0 °F against
   67.3 °F in occupied hours, close to the 60.1 °F setback. The rule's held-setback test decides:
   "setback effective (fan cycling to hold)", an **ok**. Runtime alone would have called it
   missing.
3. *Night fan energy?* 16.2 kWh in an average night (22:00-07:00) in the baseline, 3.2 kWh with
   the setback. The saving is larger than the runtime share suggests: the fan also draws much
   less power when it cycles at night than it does by day.
4. *Compressor short-cycling?* `compressor_short_cycle` is a **fault** in both tests: 178 starts
   a day in the baseline and 131 with the setback, against a 12-a-day threshold. Scheduling
   trims it (fewer running hours) but is not the fix: a DX compressor is cooling a building at
   minimum airflow in winter while the boxes reheat. Lock out mechanical cooling in cold
   weather, check the reheat, and give the compressor minimum on and off times.
5. *Before promising the saving?* The two tests ran in different weeks (March 2021 and January
   2022), so the weather differs; the building was unoccupied; only the fan's power is counted;
   and a setback has to recover by the morning without starting the compressor and reheat
   harder. Normalize for weather and count the whole unit.

**Discussion points**

- Runtime is not the whole story (question 2). The
  [occupancy scheduling guide][pnnl-guide-occupancy-scheduling] asks for unoccupied fan
  *cycling* to hold a setback, not continuous running; CAMBER's held-setback test reads the same
  distinction from the trends.
- The schedule you judge against matters: the config uses the tests' own 07:00-22:00, every day.
  Try an office schedule and watch the verdicts move.
- After-hours load is measured in energy, not hours.

**Common mistakes**

- Calling the setback test "missing" because the fan runs half the night.
- Comparing the two tests' energy as a saving without the weather.
- Treating the short-cycling as a scheduling problem.

[pnnl-guide-occupancy-scheduling]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85194.pdf "Building Re-Tuning Training Guide: Occupancy Scheduling: Night and Weekend Temperature Set back and Supply Fan Cycling during Unoccupied Hours (PNNL-SA-85194)"

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

Figures from the real `lbnl-fpu` and `ornl-frp-vav` default subsets, CAMBER 0.97.0-dev (answer 5
re-checked on 0.98.0-dev), with the commands on the [exercise page](zone-reheat-saturated.md#setup).

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
   graded `fault` on both depth and share: 30% of occupied samples, well over the 20 % a fault
   needs. "Overcooled" would send you to the cooling side
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
   in a few cold hours its extra cold air outruns the reheat. `overcooling_severity` grades that
   shortfall `info`: it is deep enough for a fault, but only 0.8% of occupied samples, under the
   5 % a warning needs. Before 0.98 it graded the depth alone and called it a `fault`.
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
- Return-air CO₂ averages the zones an air handler serves and dilutes the critical one, so a DCV
  verdict judged on an air handler's CO₂ carries a caveat saying so. Here each "unit" is a room
  (equipment class `VAV`) with its own sensor, and CAMBER leaves the caveat off. Ask the class
  what would change if the same data were mapped to one air handler instead.

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

Figures from the real `lbnl-chiller` full subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](plant-chiller-efficiency.md#setup).

**Answer key**

1. *Generic ceiling.* `chiller_efficiency` is a **fault** on `PLANT__fault_free`: its median
   1.44 kW/ton against the rule's generic 0.85 is 1.7 times the ceiling. Every run is flagged
   except the two chiller-sensor biases that read low. The score gives TPR 100% and FPR 85%
   (11 of the 13 negatives). A detector that fires on the healthy plant tells you nothing about
   the faulted ones: it catches everything by flagging everything.
2. *Calibrated ceiling.* The dataset template's 1.44 kW/ton is the fault-free run's own median,
   so the healthy chiller reads 1.44 kW/ton and is `ok` (17.7% of its loaded hours are more than
   15 % above the ceiling). The simulated chiller's design curve is not published; its own
   healthy operation is the only honest reference, as a commissioning agent would set it.
3. *Chiller fouling.* The severe run (`_065`) reads 2.36 kW/ton, a **fault** (1.6 times the
   ceiling); the mild one (`_095`) reads 1.53, about 6 % above the ceiling, `ok` because the warn
   band starts at 1.2 times. The milder fouling still shows in `pct_hours_inefficient`: 33.3%
   of its loaded hours, against 17.7% for the fault-free chiller.
4. *Other runs.* All five tower-bypass runs are **faults** (2.47 to 3.02 kW/ton; the stuck-75 %
   run reads 3.02 at a median load of only 7 tons). The worst tower fouling barely moves the
   chiller: 1.47 kW/ton, `ok`. A controlled tower holds its leaving water by working its fan
   harder, so the chiller hardly notices: look at the tower's fan effort
   ([`plant-cooling-tower`](plant-cooling-tower.md)).
5. *Label score.* `chiller_efficiency` scores TPR 55% (6 of 11: the five bypass runs and the
   severe chiller fouling) and FPR 15% (2 of 13): the false alarms are `PLANT__chiller_bias_1`
   (warn) and `PLANT__chiller_bias_2` (fault), a chiller whose leaving-water sensor reads high.
   Missed: the milder chiller fouling, the three tower foulings and the tower PI mistuning.

**Discussion points**

- Where does a design ceiling come from on a real plant? The chiller schedule gives kW/ton at
  design and part load; commissioning records give the healthy baseline. A generic number
  (0.85 kW/ton here) is a guess about a machine you have not looked at.
- Calibrating on the fault-free run assumes it is healthy. On a real plant you rarely have a
  labelled healthy year; discuss how you would establish one (a post-commissioning period, a
  manufacturer's curve, a sister machine).
- The PNNL cooling guide judges the plant by its controls, not by kW/ton. kW/ton tells you that
  energy is being lost; the control questions tell you where. Bring the reset and pumping
  exercise in here.

**Common mistakes**

- Tuning the ceiling until the faulted runs light up and the healthy one does not, then quoting
  the score as if the ceiling had been set independently. The ceiling here is set from the
  fault-free run alone.
- Reading the 6 % rise of the mild chiller fouling as "no fault". The severity is `ok`; the
  metrics are not.
- Reading the chiller-bias alarms as detections. The chiller is healthy; the sensor lies (see
  [`plant-sensor-vs-equipment`](plant-sensor-vs-equipment.md)).
- Concluding that tower fouling is harmless because the chiller's kW/ton barely moves: the cost
  moved to the tower fan.

<!-- END plant-chiller-efficiency -->

<!-- BEGIN plant-cooling-tower -->

### `plant-cooling-tower`: Cooling tower: approach and fan effort

Figures from the real `lbnl-chiller` full subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](plant-cooling-tower.md#setup).

**Answer key**

1. *The healthy tower.* A median approach of 8.1 °F over the 483 hours its fan ran at 90 % or
   more: `ok`. That is the calibration point of the dataset template's 8.1 °F design approach, so
   it is `ok` by construction; 0.2% of those hours are more than 3 °F above it.
2. *The 65 % fouled tower is not flagged.* Its approach at high fan is 9.4 °F, 1.16 times the
   design approach, below the 1.3 times where a warn starts. The fouling does show in the share of
   high-approach hours: 20.4% against 0.2%.
3. *Fan effort.* The fouled tower's fan ran at 90 % or more for 1,617 hours, against 483 for the
   healthy one: about 3.3 times as long. A tower controlled to a leaving-water setpoint keeps its
   approach and pays for fouling in fan energy. The milder foulings follow the same order (the
   80 % run: 8.4 °F, 1,169 hours; the 95 % run: 8.2 °F, 577 hours).
4. *Condenser-water reset.* Yes: `condenser_water_reset` is `ok` with a slope of 0.93 °F of
   condenser-water supply per °F of wet-bulb over the operating hours. The simulated controller
   holds the tower's water at the wet-bulb plus a fixed offset, with a 60 °F floor.
5. *The stuck bypass.* `condenser_bypass_leak` is a **fault** on all five bypass runs, attributed
   to the valve: with the bypass commanded shut, the water entering the chillers is +62.2 °F
   warmer than the water leaving the tower (median, stuck-75 % run). The approach rule declines
   (`info`) on the bypass runs because the tower fan never reaches 90 % (a peak of 51 % in the
   stuck-75 % run): with the water going around it, the tower has little heat to reject. The
   chiller pays instead ([`plant-chiller-efficiency`](plant-chiller-efficiency.md)).
6. *Label score.* `cooling_tower_approach` scores TPR 0% and FPR 0 %: it finds none of its four
   targets (the three tower foulings and the PI mistuning, on which it declines because the fan
   never reaches 90 %). A detector for a controlled tower should measure the fan effort at
   matched load and wet-bulb against the tower's own baseline, which is what
   `cooling_tower_fan_effort_drift` does when a before-and-after exists.

**Discussion points**

- "The approach looks fine" is not "the tower is fine". Ask what the controller holds constant;
  the fault shows in whatever it spends to do so.
- Why judge the approach only at high fan? In mild weather the controller chooses to leave
  warmer water (the 60 °F floor), and a high approach then is correct control.
- The bypass is a textbook case of a fault invisible to the equipment's own controls: the tower
  holds its setpoint, the valve reads shut, and the chillers get the hot water. Only a sensor
  downstream of the mixing point sees it.
- The simulated bypass runs drive the condenser loop to temperatures a real chiller would trip
  on (the catalog's [data issue](../DATASETS.md#the-condenser-bypass-runs-are-fixed-physically-implausible-bypasses-and-the-two-75-runs-are-one-case)). Discuss what a real plant would show instead
  (high-pressure trips, alarms).

**Common mistakes**

- Reading the fouled tower's `ok` as healthy without comparing its metrics with the fault-free
  run's.
- Reading `info` (declined) on the bypass runs as `ok`.
- Treating the bypassed-fraction estimate as the leak size: in these runs it does not follow the
  severities in the file names (it is fixed at 0.92, 0.97 and 0.99 for the 25, 50 and 75 % runs).
- Counting the two 75 % runs as two detections: in both the valve command reads zero all year and
  the condenser side is the same data, so they are one case.
- Lowering the design approach until the fouled tower is flagged, and not checking what else
  gets flagged with it.

<!-- END plant-cooling-tower -->

<!-- BEGIN plant-chw-reset-pumping -->

### `plant-chw-reset-pumping`: Chilled-water reset and pumping

Figures from the real `lbnl-chiller` default subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](plant-chw-reset-pumping.md#setup).

**Answer key**

1. *Reset.* Yes: `chwst_reset_present` is true, with a slope of -0.38 °F of supply per °F of
   outdoor dry-bulb (median supply 47.6 °F over the running, occupied hours). The supply gets
   warmer in cool weather and colder in hot weather, which is the right direction. The rule would
   count a reset in the wrong direction too, so the sign is the learner's check.
2. *Low delta-T.* `chw_plant_reset` is a **fault** on the healthy plant because chiller 1's loop
   delta-T has a median of 6.4 °F, and 80.1% of its running hours are below the rule's 8 °F
   minimum. The primary flow does not follow the load, so the chiller's delta-T is low at part
   load by design: a property of this plant, and a design question (variable primary flow, a
   higher supply setpoint in mild weather), not a broken component. The rule has no parameter to
   calibrate it.
3. *DP reset and pumping.* No: the DP setpoint is flat (`dp_sp_reset_present` false, the same
   value all year). The secondary pump runs at a median 84.9% and at 90 % or more in 38.0% of
   its hours: a **warn** for riding the curve.
4. *VFD minimum.* The pump's floor is 34.5% (the median speed of the chiller-bias run, where the
   pump idles at its floor for at least half the year; the trend viewer shows the same floor on the
   fault-free run in winter). The rule counts 0 % of hours near minimum on every run because its
   near-minimum band stops at 25 %, below this pump's floor. The pump also runs all year, with no
   cooling load in winter.
5. *Stuck bypass.* The plant cannot make chilled water: the supply median is 65.0 °F and the
   delta-T 0.7 °F, and the pump runs at a median 100%. `chw_plant_reset` (a fault) and
   `chw_pump_dp_reset` (a warn) are both symptoms; the cause is the tower bypass, which only
   `condenser_bypass_leak` names ([`plant-cooling-tower`](plant-cooling-tower.md)).

**Discussion points**

- Map the three answers onto the PNNL cooling guide's three questions. The guide's low-delta-T
  test uses the same 8 °F line; discuss whether it fits a constant-flow primary loop.
- Why does a flat DP setpoint cost energy? Pump power goes roughly with the cube of speed, so the
  hours near full speed dominate the bill.
- A plant whose pump idles at a floor all winter is a scheduling question: can the loop be shut
  down, or the floor lowered?
- The stuck bypass shows how one fault fans out into symptoms on every loop. Ask which finding
  a technician should act on first.

**Common mistakes**

- Fixing the low delta-T by chasing a broken part: nothing is broken; the plant is designed that
  way.
- Reading "0 % near minimum" as "the pump is never at its minimum".
- Reading a negative slope as "no reset" (or a positive one as a working reset) without asking
  which way a chilled-water reset should go.
- Blaming the pump for the bypassed plant: it runs flat out because the chilled water is warm.

<!-- END plant-chw-reset-pumping -->

<!-- BEGIN plant-boiler -->

### `plant-boiler`: Boiler plant

Figures from the real `lbnl-boiler` full subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](plant-boiler.md#setup).

**Answer key**

1. *Which rules run.* Only `hw_pump_dp_reset`: 17 findings, one per run, all `ok`.
   `boiler_summer_lockout`, `boiler_short_cycle` and `hw_plant_deltat` need a boiler run status
   (`boiler_status`). The plant's status point is the boiler enable, on all year even while the
   boiler burns no gas, so the catalog leaves it unmapped and the three rules do not run: no
   finding at all, which is not the same as `ok`.
2. *Reset and summer lockout.* CAMBER cannot judge either on this plant: the lockout rule needs
   the run status, and there is no supply-temperature setpoint. The trend viewer shows the loop
   supply flat all year (no reset), and boiler 1's gas input falling to nearly zero in warm weather: the
   boiler stops firing, but the plant stays enabled and pump 1 keeps running.
3. *Pumping.* `hw_pump_dp_reset` is `ok`: a median speed of 29.0%, almost no hours near full
   speed, none near the 25 % minimum band, so neither riding the curve nor pinned at minimum.
   What the `ok` leaves out: the DP setpoint is flat (`dp_sp_reset_present` false), and the pump
   ran in 8,759 hours, every hour of the year, summer included.
4. *Fouling.* Nothing fires on `PLANT__boiler_foul_065` (its pump finding is `ok`). Fouling shows
   as more gas for the same heat, and no point-in-time rule here compares the two.
   `boiler_efficiency_drift` does, against a frozen baseline of the same boiler; this dataset has
   no before-and-after on one boiler to feed it through a config.
5. *DP sensor bias.* A DP sensor reading 20 % high makes the pump slow to a median 26.4%, and
   one reading 20 % low speeds it up to 32.4% (29.0% healthy), while the DP reading itself stays
   at its setpoint: the controller holds the lying reading there. The rule stays `ok` on both.
   Overall, the label score is TPR 0%: nothing in this config detects any of the 16 faults.

**Discussion points**

- The heating guide asks the same three questions as the cooling guide (reset, delta-T, DP
  reset), plus a summer shutdown. Which could a technician answer from this plant's trends, and
  which need a point it does not have?
- An enable is not a run status. Ask what trend you would add on a real plant (burner firing,
  flame signal, gas valve) before trusting a lockout or short-cycle analysis.
- A pump that runs all summer for a boiler that does not fire is a scheduling opportunity even
  though every rule says `ok`.

**Common mistakes**

- Reading the missing lockout and short-cycle findings as "no problem".
- Mapping the enable point as the boiler status to make the rules run: every idle hour would
  then count as firing.
- Reading the pump's `ok` as "nothing to re-tune": the flat DP setpoint and the all-year running
  are both opportunities.
- Expecting a temperature rule to see boiler fouling: the supply temperature is held at setpoint
  whatever the boiler's condition.

<!-- END plant-boiler -->

<!-- BEGIN plant-sensor-vs-equipment -->

### `plant-sensor-vs-equipment`: Plant sensor faults vs equipment faults

Figures from the real `lbnl-chiller` full subset, CAMBER 0.97.0-dev, with the commands on the
[exercise page](plant-sensor-vs-equipment.md#setup).

**Answer key**

1. *The chiller's leaving-water sensor.* Reading 2 °C high, the chiller reads 3.19 kW/ton, a
   **fault** (1 °C high: 2.13, a warn). Reading 2 °C low it reads 0.67 kW/ton, far better than
   the healthy chiller's 1.44, `ok`. Neither chiller changed. The controller holds the lying
   sensor at setpoint, so the real water is colder (or warmer) than it reads; the chiller works
   harder (or less), and the tons CAMBER computes from the biased temperature are wrong as well.
   A kW/ton better than the healthy machine's is itself a clue.
2. *Where the low-reading sensor also shows.* In the secondary pump: `chw_pump_dp_reset` is a
   **fault** on the 2 °C-low run, with the pump at 90 % or more in 82.1% of its hours (a median
   of 100 %). The real chilled water is warmer, so the air handlers' valves open wider and the
   pump works harder to hold the loop DP.
3. *Tower sensor vs bypass.* `condenser_bypass_leak` reports both ±2 °C tower-sensor biases as
   `info` with `attribution: sensor_offset`: the water entering the chillers reads -3.1 °F and
   +3.6 °F from the tower's leaving water, and the difference stays the same at every load.
   Warm return water leaking through a bypass would grow with the condenser range, as it does on
   `PLANT__bypass_stuck_075`, a **fault** attributed to the valve. (Reading colder than the
   tower's leaving water is impossible for mixing, which settles the -3.1 °F case.) The ±1 °C
   biases stay under the rule's 2 °F threshold (`ok`).
4. *The loop DP sensor.* Not in the DP reading, which the controller holds at setpoint. It shows
   in the pump speed: a sensor reading 20 % high lets the pump slow to a median 74.4% (`ok`), one
   reading 20 % low drives it to 97.0% (a warn), against 84.9 % healthy.
5. *Score.* `chiller_efficiency` scores FPR 15% (the 1 °C- and 2 °C-high chiller-sensor runs,
   2 of 13 negatives); `cooling_tower_approach` scores FPR 0%. The labels are right to count
   these as false alarms: the chiller is healthy, and a technician sent to it would find nothing
   wrong. The right work order is "calibrate the sensor", and a detector that cannot tell the two
   apart sends the wrong trade.

**Discussion points**

- "Treat a sensor bias as a negative" is a statement about the equipment rule, not about the
  sensor. The sensor fault is real and worth finding; it needs its own detector (a comparison
  with an independent reference), and `condenser_bypass_leak` shows one way to separate the two.
- Controlled variables hide their own sensor's bias: the reading sits at setpoint, and the
  error moves to whatever the controller drives (the chiller's lift, the pump speed).
- Ask which PNNL-guide conclusions from the other exercises would flip if one sensor were off by
  2 °C.

**Common mistakes**

- Counting the chiller-bias alarms as detections because "something was wrong".
- Trusting a suspiciously good kW/ton (0.67 here) as good news.
- Reading the tower-bias `info` as `ok`: it names a problem, just not the valve.
- Looking for a DP sensor bias in the DP trend, which looks perfect.

<!-- END plant-sensor-vs-equipment -->

## Data quality, energy charting, M&V and the capstone

<!-- BEGIN data-trend-quality -->

### `data-trend-quality`: are the trends good enough?

Figures from the real `nuig-ahu101`, `lbnl-b59` and `irish-ahu` default subsets, CAMBER
0.97.0-dev, with the commands on the [exercise page](data-trend-quality.md#setup). Trust scores
are the RCx reports' (hourly, gated on the unit's fan signal).

**Answer key**

1. *Which core points does each unit not trend?* `AHU101__ahu101` has no mixed-air
   temperature and no outdoor-air damper (it is a 100 % outdoor-air unit: nothing to mix) and no
   duct static or static setpoint. `RTU01` has no cooling or heating valve and no duct static or setpoint; its fan point is a speed, not a status. `AHU__ahu` has
   no supply-air setpoint, no duct static or setpoint and **no fan point at all**, so its trust
   table is ungated. B59's outdoor-air flow is flagged `late_start`: it starts on 2020-04-10.
   Earlier values were the publisher's gap fill, which the ingest masks.
2. *The copy.* `RTU04`'s return air carries its supply-air data (`copied_signal`). CAMBER blames
   the point that jumps across the gap to the other's level at the stretch's edges: the return
   air suddenly reads supply-air temperatures, while the supply air keeps measuring supply air.
   The return air drops to *suspect* at 0.56; the supply air keeps its score.
3. *Mixing balance.* `RTU01` and `RTU02`'s mixed-air sensors fail the flow-weighted balance and
   are capped at *suspect* 0.75; `RTU03` and `RTU04` pass. The balance rests on flow stations of
   unknown accuracy and cannot say which of mixed, outdoor or return is wrong, so it is
   screening-grade: it can make a point suspect, never untrusted on its own.
4. *The ceiling.* The room CO2 tops out at the sensor's full scale, just under 2000 ppm; 532
   stored 15-minute samples read it, and every one has the supply fan off. It is a clipped reading, a
   lower bound, not a measurement. The trust score does not name it (it flags outliers and a
   bimodal shape); the ingest's quirk note and the dataset's data issues do.
5. *The held meter.* `ELE_lig_S` holds one value for 32.25 h and 36.5 h, both runs starting on a
   Saturday. A lighting load parked at its base over a weekend is plausible; a logger holding its
   last value is too. The flag says "look", and the trend viewer (does anything else move in
   those hours?) or the site settles it.
6. *Filled or not.* `RTU01_zone_022`'s CO2 has two 30-day windows of continuous values (every
   value unique) from 2020-04-26, between windows that repeat the sensor's resolution: a filled
   stretch, which the publisher's README says exists (gaps filled by interpolation and other
   methods). The nuig fan status "repeats whole days exactly" too, but that is a fixed weekday
   schedule doing its job: the same screen, an innocent cause.
7. *The irish supply air.* It reads *untrusted* at 0.19 over the whole record for two reasons:
   a few stray rows logged years before the rest, then a long gap, make its coverage over its
   own span low, and a supply air held tightly at setpoint makes every hour spent at
   another operating level look like a robust outlier. Judged from the first sample after the
   longest gap (2017-06-23), the low-coverage flag goes; the outlier flag stays. The sensor is
   fine; the verdict describes the record and the control, not a fault.

**Discussion points**

- The trending guide's question is "can the data answer the re-tuning questions?", not "is
  every point perfect?". A unit with no fan point (`irish-ahu`) can still be analysed, but every
  gated check runs ungated, and the report says so.
- Tie each flag to a physical story before acting: a copy is a mapping or programming error (fix
  the BAS), a failed balance is a sensor location or calibration issue (a walk-down item), a
  clipped reading is a sensor range limit (change the sensor or accept a lower bound).
- CAMBER already corrected some data at ingest; show `camber datasets info irish-ahu` and the
  `--no-corrections` option, and discuss why corrections must be recorded, not silent.

**Common mistakes**

- Reading *suspect* as "broken": it means "verify before relying on it".
- Treating the nuig fan status's repeated days as fabricated data.
- Concluding the irish supply-air sensor must be replaced because its score is 0.19.
- Running `gapfill_signature` on an hourly resample: the means erase the granularity signature.
- Counting a fan speed as missing: CAMBER uses it as the fan-on gate when no status exists.

<!-- END data-trend-quality -->

<!-- BEGIN data-energy-charting -->

### `data-energy-charting`: energy charting

Figures from the real `bdg2` (site Fox) and `valladolid-uva` default subsets, calendar 2016,
CAMBER 0.97.0-dev, with the commands on the [exercise page](data-energy-charting.md#setup).

**Answer key**

1. *Weekends.* Both university buildings drop: `UVA_A`'s weekend peak (mean load by hour of
   day) is only 0.45 of its weekday peak, and `UVA_B`'s falls further. The assembly building
   `Fox_assembly_Audrey__electricity` does not drop: its weekend profile matches its weekday
   one.
2. *Out-of-hours load.* `baseload_anomaly` calls the assembly building (0.90) and the lodging
   building (1.04) a fault, and both Valladolid buildings ok (`UVA_A` 0.56). The lodging's ratio
   above 1 is right: people sleep there, so its "unoccupied" hours are occupied, and the
   default weekday 07:00-18:00 window does not fit it. The assembly building's is the one to
   question: a building that runs its night and weekend load at nearly its daytime level is the
   first thing PNNL's scheduling guide asks about.
3. *Flatness.* The lodging building: load factor 0.59 against `UVA_B`'s 0.31, and a base load
   (5th percentile) of 0.41 of its peak against 0.085. A flat, high base is normal for lodging
   and a warning sign for an office or classroom building.
4. *Weather.* The lodging's chilled water picks a cooling-only change point (`3PC`) and the
   weather explains most of its daily variation (R² 0.86); it meets the G14 tier. The same
   building's electricity barely follows the weather (R² 0.32) and fails it.
5. *`UVA_A`'s all-days model.* R² 0.12, CV(RMSE) 26.1%. Its profile shows why: weekdays and
   weekends (and holidays and academic breaks) are different loads at the same temperature, so a
   temperature-only model scatters. A day-type driver is missing, which `mv-baselines` adds.

**Discussion points**

- The metrics are only as good as the occupied window: ask each group to set `start_hour` /
  `end_hour` to what they believe for each building and rerun `baseload_anomaly`.
- An "ok" out-of-hours ratio of 0.56 still means more than half the daytime load runs all
  night; compare it with what should be on at 03:00.
- Weather dependence says which meters a weather-normalized M&V baseline suits: chilled water
  here, not the lodging's electricity.

**Common mistakes**

- Calling the lodging's ratio above 1 a scheduling fault.
- Reading a high R² as "a good model" without the CV(RMSE), or a low R² as "a bad meter".
- Comparing peaks across buildings without normalizing (each ratio is within one building).
- Using the hour of the peak from a meter whose clock is in doubt; check the data issues first.

<!-- END data-energy-charting -->

<!-- BEGIN mv-baselines -->

### `mv-baselines`: change-point baselines and M&V

Figures from the real `cofactor-drammen`, `valladolid-uva` and `bdg2` default subsets, CAMBER
0.97.0-dev, with the commands on the [exercise page](mv-baselines.md#setup). The bills are
synthetic, cut from the open BDG2 meters.

**Answer key**

1. *The 2018 baselines.* `b6400_ElImp` (school) and `b6410_ElImp` (nursing home) meet CAMBER's
   G14 gate. `b6404_ElImp` does not: its CV(RMSE) of 22.5% is within the daily tier, but its R²
   of 0.72 is under the 0.75 CAMBER requires. SEP §6.4.1 rejects the same model for another
   reason: its cooling slope has the wrong sign (the logical-sign test), so its SEnPI is not
   reportable under `validity: both`.
2. *The unexplained saving.* The school `b6400_ElImp` uses 5.8% less in 2019 than its 2018
   model predicts, with a 90 % band of 24,051 on the saving: outside the band. The backcast
   agrees (5.1%). No measure is documented, so before reporting it, find the cause: an
   operating change, a sub-meter moved, an occupancy change. A saving nobody can explain is a
   non-routine question, not a result.
3. *The nursing home.* 0.8%, well inside its band: no change.
4. *The chain.* `UVA_B` (the faculty whose publisher describes an equipment replacement) shows a
   chained saving of 12.6% over 2016-2018; `UVA_A`, -0.1%, none. Neither 2016 baseline meets the
   G14 gate (R² 0.66 and 0.55, under 0.75), though both pass SEP's R² test (0.50 or more), so the
   savings are "for information only". The chain leaves the academic breaks (July and August,
   Christmas, Easter) unmodelled: the occupied-day driver knows public holidays only.
5. *The closure.* Unadjusted, `b6400_ElImp`'s 2020 chained saving is -0.9%, which looks like
   "no change". The closure indicator is material and negative: the school used much less while
   it was shut, which hid the rest of the year. Adjusted, the saving is -8.1%: outside the
   closure the school used more than the model expects (plausibly, more ventilation after
   reopening). The unadjusted figure misleads because two opposite effects cancel.
6. *Chilled-water bills.* 10 bills make the 2016 baseline (a bill straddling New Year belongs to
   neither year, and a bill with a missing day is dropped), and the bills choose a 63 F cooling
   base. R² 0.90, but CV(RMSE) 22.7% fails the monthly 15 % tier: a high R² on a load that swings
   from almost nothing in winter to a large summer load says the shape is right, while the
   monthly scatter is still too large for a savings claim. The caveats add that the base is
   poorly determined and the baseline covers less than a year.
7. *Electricity bills.* R² 0.48: under SEP's 0.50, so weather explains little of the variation
   (the lodging's load follows its occupancy); the model is not SEP-valid, and the forecast
   band spans zero. Bills alone cannot measure a saving on this meter.

**Discussion points**

- The order matters: method first, then adjustments, then the result (see the M&V page). Ask
  which method each group would declare before they saw any saving.
- G14 acceptance and SEP validity answer different questions (can this model support a savings
  claim? is this model statistically valid?), and they can disagree in both directions.
- Non-routine events need evidence and approval under SEP §5.3.2; point to the ledger entry's
  `evidence` and `approved_by` fields, and to IPMVP's caution on meter-derived adjustments.
- Bills are the only data many buildings have. Discuss what a sub-meter or trend data would add.

**Common mistakes**

- Reporting the 5.8% as a re-tuning saving.
- Reading the unadjusted -0.9% as "the school did not change".
- Treating R² as the only acceptance criterion, or ignoring CV(RMSE) because R² is high.
- Forgetting that the bills are synthetic, or presenting them as real utility bills.
- Comparing forecast and backcast percentages without their bands.

<!-- END mv-baselines -->

<!-- BEGIN capstone -->

### `capstone`: RCx report, walk-down, re-tuning plan and verification

Figures from the real `lbnl-sdahu` and `ornl-frp-ops` default subsets, CAMBER 0.97.0-dev, with
the commands on the [exercise page](capstone.md#setup). The RCx report is the one written before
the drift baselines are frozen.

**Answer key**

1. *The top issue.* Rank 1 is "Enable economizer free cooling" on
   `AHU__onset_damper_stuck_025`, a **fault**. Its action asks you to check the economizer
   enable logic and high-limit setting, then **verify the OA damper modulates open** when free
   cooling is available. The data cannot separate a stuck damper from wrong logic here: the
   dataset maps the damper to the controller's command, which keeps modulating, so only the
   temperatures show that less outdoor air arrives. The report's title says "enable", the
   likely cause is mechanical: that is what the walk-down is for.
2. *Missed free cooling.* The onset unit ran mechanical cooling in 30% of the free-cooling hours
   (outdoor air below 60 °F), against 17% for the fault-free control. A damper stuck at 25 %
   admits about as much outdoor air in cooling weather as the unit's small design minimum, so the
   outdoor-air-fraction rules see nothing to object to.
3. *The conditional issue.* The onset unit's static-pressure reset issue is conditional on
   `duct_static_sp` (trust 0.40): in the faulted half of the splice the published setpoint is a
   placeholder that the ingest masks, so half the year has no setpoint. Walk-down item: read the
   static setpoint at the controller and confirm what the trend is mapped to.
4. *Walk-down checklist and re-tuning plan* (a model answer; accept any that covers the
   evidence):
   - Outdoor-air damper on the onset unit: stroke it from the BAS through its range, watch the
     blades and the linkage, and compare the mixed-air temperature with the command. Expect: the
     blades stay near a quarter open. Confirm the economizer enable logic and the 60 °F high limit
     in the program.
   - Static-pressure setpoint point: confirm the trend mapping and the value at the controller.
   - Design values the report assumed (its confidence lines say "rule defaults"): the minimum
     outdoor-air position, the supply-air setpoint and the static setpoint, from the drawings or
     the controller.
   - The plan, sensors first: fix the static-setpoint trend; repair the damper actuator or
     linkage; then consider the sequence changes the report suggests (supply-air reset,
     static-pressure reset) as separate measures with their own verification. Verification: the
     frozen drift baseline for the damper (the economizer signal should return to steady), and
     `free_cooling_missed` back to the control's level.
5. *Drift.* The onset unit reads **fault**, locus `outdoor-air`: at matched damper command it
   delivers 83 percentage points less outdoor air than in the first half (a drift of -83). The
   control reads `ok`, `steady`. After the repair, keep the same frozen first-half baseline and
   score a post-repair window: the economizer signal should return to steady. Only if the
   repair changes the design (a new minimum, say) would the baseline be moved, and then with
   `camber drift accept` and a recorded reason.
6. *The scheduling measure.* Before, the baseline test's setback is **missing** (a fault: the fan
   runs every unoccupied hour); after, the setback test's is `ok`, a held setback with the fan
   cycling at night. The measure did not fix the short cycling: 178 compressor starts a day
   before and 131 after, both far above the rule's limit. A second issue for the plan.
7. *M&V.* CAMBER refuses: an hourly baseline needs at least 1440 hours and the baseline test has
   168. The refusal says what is missing: its `need` puts the gap at 1,272 more hours (about 53
   days of data). A defensible measurement needs a longer baseline covering the reporting weather
   (months, not a week), or an IPMVP Option B isolation measurement of the unit's own energy, set
   up before the change.

**Discussion points**

- The RCx report ranks issues; it does not decide causes. Map each item on the checklist to the
  walk-down chapter's advice on what to observe, and to the economizer guide's two questions.
- Verification is planned before the change: decide the check, the window and the pass mark in
  the re-tuning plan, then run it.
- Drift and M&V answer different questions: "did the equipment go back to how it was?" and "how
  much energy did the change save?". The capstone shows one working and one refused, honestly.

**Common mistakes**

- Freezing the drift baselines before writing the RCx report, so the report already contains the
  drift verdict.
- Taking the report's "Enable economizer free cooling" literally and reprogramming instead of
  inspecting the damper.
- Treating the conditional static-reset issue as a confirmed fault.
- Comparing the two RTU tests' energy directly as a saving: different winters, a week each, no
  weather normalization.
- Moving the drift baseline to "clear" an alarm without a recorded reason.

<!-- END capstone -->
