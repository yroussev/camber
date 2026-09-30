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
0.97.0-dev, with the commands on the [exercise page](air-sat-reset.md#setup).

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
   **fault**, too cold in 33% of its running hours. With the damper stuck at 75 % the unit takes
   in cold outdoor air it cannot shut out, and it has no heating coil to warm it back up.
5. *The fault-free unit's too-warm hours?* `supply_air_control` is a **warn** on
   `AHU__fault_free`: 12% of its running hours are more than 2 °F above the setpoint. 78% of
   those hours are unoccupied: the fan cycling in the simulation's unoccupied mode, with the
   damper shut and warm return air. That is not an occupied-control fault; the rule does not
   separate unoccupied cycling from occupied control.

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

Figures from the real `lbnl-sdahu` and `ornl-frp-vav` default subsets, CAMBER 0.97.0-dev, with
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
   have a median damper opening below 50 % (a fleet median of 39%), none is near fully open:
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
- Running the census on a weekend test day and reading "no damper data" as an empty building:
  the rule's occupied hours follow CAMBER's assumed weekday schedule.

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
   The catalog's own note on this unit reads the warning as mostly the COVID period; the
   period split does not bear that out.

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

### `zone-reheat-overcooling`: Reheat penalty and overcooling

_Placeholder: the key is written with the exercise (#81)._

<!-- END zone-reheat-overcooling -->

<!-- BEGIN zone-bad-box -->

### `zone-bad-box`: One bad box in a fleet

_Placeholder: the key is written with the exercise (#81)._

<!-- END zone-bad-box -->

<!-- BEGIN zone-reheat-saturated -->

### `zone-reheat-saturated`: Reheat saturated

_Placeholder: the key is written with the exercise (#81)._

<!-- END zone-reheat-saturated -->

<!-- BEGIN zone-dcv -->

### `zone-dcv`: DCV: is outdoor air following occupancy?

_Placeholder: the key is written with the exercise (#81)._

<!-- END zone-dcv -->

<!-- BEGIN zone-min-oa -->

### `zone-min-oa`: Minimum outdoor air and 62.1 system ventilation

_Placeholder: the key is written with the exercise (#81)._

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
