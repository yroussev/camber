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
0.98.0-dev (the dual-duct `supply_air_reset` reading 0.100.0-dev, after its cold-deck setpoint
was mapped), with the commands on the [exercise page](air-sat-reset.md#setup).

**Answer key**

1. *Is the single-duct unit's supply air reset?* No. `supply_air_reset` is a **warn** on
   `AHU__fault_free`: "not reset (setpoint flat)". The rule reads the trended setpoint, which
   never moves (a range of 0.00 °F all year at 55.25 °F), so the shape of the supply air does not
   matter. Against the Guideline 36 outdoor-air target the supply air is colder in 71% of the
   occupied running hours, by a mean 6.0 °F (`supply_air_reset_compliance`, a **warn**). The
   faulted runs read the same: the sequence has no reset.
2. *The dual-duct cold deck?* Not reset either: `supply_air_reset` is a **warn**, "not reset
   (setpoint flat at ~55F)", and the supply air is below the Guideline 36 target in 66% of the
   hours. Since 0.100 the cold-deck setpoint is mapped, and the rule reads it: it never moves (a
   range of 0.00 °F all year at 55.0 °F). Before 0.100 no setpoint was mapped and the same
   **warn** came from the supply air itself ("SAT pinned low at ~55 F regardless of OAT"). The
   dataset's documentation describes a 60 °F cold-deck setpoint in economizer weather; the
   setpoint and the supply air hold 55 °F in every row (the data issue *The 60 F economizer
   supply-air setpoint is not in the data*). The published description and the published data
   disagree; CAMBER reads the data. The hot deck has its own setpoint (90 °F), which is not
   mapped: the answer is about the cold deck only.
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
0.97.0-dev (the leak figures 0.98.0-dev), with the commands on the
[exercise page](air-heat-cool.md#setup).

**Answer key**

1. *Does CAMBER flag the leak?* Yes. `leaking_valve` is a **fault** on `AHU__coi_leakage_010`:
   in 60.5% of its occupied, fan-on hours with the valve shut, the supply air sits below the
   mixed air. The fault-free run reads 2.4% (**ok**), and the label score gives `leaking_valve`
   TPR 100%, FPR 0 %.
2. *Compare the two runs.* With the valve commanded shut and the fan running, the fault-free
   unit's supply air sits a median +1.1 °F above its mixed air (the fan's heat, occupied hours);
   the leak run's sits at -0.1 °F. The leak takes about 1 °F out of the air, all day, in every
   hour the valve is meant to be shut. The rule's default only calls a cooling leak when the
   supply air is more than 3 °F below the mixed air, a margin meant to ride over sensor error, so
   with the defaults this leak is **ok** (CAMBER 0.97 missed it). The config credits the unit's
   own fan heat (`measured_fan_heat_f` 1.0 °F, `cool_delta_thr_f` 1.0 °F): a leak is now any
   supply air below the mixed air, and the caveat names the fan heat used. That 1.0 °F was
   measured on `AHU__fault_free`, the same run the score counts as a negative, so its clean
   verdict is partly by construction; the leak run, which the calibration never saw, is the
   evidence. A half-year split (calibrate on one half, judge the other) still reads the
   fault-free run ok and the leak a fault ([VALIDATION.md](../VALIDATION.md)).
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

- A rule with a fixed margin misses small faults; a unit's own baseline catches them, at the
  price of a calibration that must not be judged on the data it came from. Ask where the class
  would put the margin, knowing the sensors' accuracy, and which period they would calibrate
  on. Map it to the
  [AHU heating and cooling guide][pnnl-guide-ahu-heat-cool]'s question on valves that do not
  shut off.
- "Both valves open" is a symptom, not a diagnosis: the system type (dual-duct) and the sequence
  (dehumidification) decide.
- The Irish unit's valves were replaced in May 2022: the going-further step splits its record
  there.

**Common mistakes**

- Quoting the fault-free run's FPR 0 % as proof the calibration works: that run set the
  1.0 °F. The held-out half-year is the honest test.
- Copying the 1.0 °F fan heat to another unit. Fan heat depends on the fan, its speed and the
  sensor positions; measure it on the unit's own known-good hours.
- Calling the dual-duct unit's 27% a fault to fix at the coils.
- Judging a heating leak on supply air downstream of the fan without allowing for fan heat.

[pnnl-guide-ahu-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88359.pdf "Building Re-Tuning Training Guide: AHU Heating and Cooling Control (PNNL-SA-88359)"

<!-- END air-heat-cool -->

<!-- BEGIN air-economizer -->

### `air-economizer`: a stuck outdoor-air damper and missed free cooling

Figures from the real `lbnl-sdahu` default subset, CAMBER 0.97.0-dev (the `free_cooling_missed`
figures of questions 4 and 5, with the rule judging fan-on hours only (#120) and the low limit:
0.102.0-dev), with the commands on the [exercise page](air-economizer.md#setup).

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
   mechanical cooling running in 100% of the free-cooling hours (outdoor air below 60 °F, supply
   fan running): the damper can't open to cool for free, so the coil does the work every hour.
   But the fault-free unit is a **fault** too, at 34.1%. A healthy unit reading as a full fault
   is the puzzle of question 5. The rule does not judge hours with the fan off
   (`n_masked_fan_off`): no air moves across the coil, so there is nothing to economize.
5. *The fault-free unit's fault: the economizer low limit.* Its `missed_cause` is
   `economizer_not_commanded` and `missed_damper_cmd_median_pct` is 10: on the missed hours the
   damper was commanded to its 10 % minimum, not open. The trends show when: on cold winter
   weekdays (December to February, most of them) the damper command sits at its minimum while
   the coil cools the mixed air; on mild days the damper opens, and when the coil runs it runs
   with the damper open (integrated economizer, which the rule does not count as missed). The dataset's inventory (section 1.2, in `camber
   datasets info lbnl-sdahu`) says the economizer runs only between 33.8 °F and 60 °F. Below
   33.8 °F the sequence locks the economizer out, so those hours are not free-cooling weather.
   899 of the fault-free unit's 965 missed hours, 93 %, are below the lockout. With
   `"low_limit_f": 33.8` the rule sets aside 1,014 fan-on hours below it, judges the remaining
   1,818 free-cooling hours, and the fault-free unit reads clean: **ok** at 3.63%. The stuck
   dampers stay caught: the 10 % and 25 % runs are still a **fault**, at 100%, with
   `missed_cause: damper_not_delivering` (the damper commanded open, the outside air not
   arriving). The low limit removes a design behaviour from the opportunity; it does not hide
   the fault, because a stuck damper misses free cooling above the low limit too.
6. *Label score.* Against the dataset's labels, `outdoor_air_fraction` finds 2 of the 4 stuck
   dampers with no false alarm: TPR 50%, FPR 0 %. Over all six scored runs (the four dampers,
   the leak and the fault-free run) the overall detection is TPR 40%, FPR 0 %: the exercise's
   config leaves out `leaking_valve`, so the leak run counts as missed.
7. *The Irish unit (real `irish-ahu` data, whole record)?* `outdoor_air_fraction` is a **warn**:
   in cooling weather (outdoor air above 70 °F) the outdoor-air fraction is above the unit's
   4.1 % minimum in 42% of the hours. `economizer_high_limit` is a **warn**: above the 65 °F high
   limit, with the outdoor air no cooler than the return air, the damper is not back at minimum
   in 21% of the samples. `free_cooling_missed` is **ok**: mechanical cooling ran in 8.0% of
   the free-cooling hours. Raise the excess outdoor air in warm weather as a question for the
   site, with the dates; the free cooling is being used.
8. *Is it the COVID-19 period?* Not only. Splitting the record with `source.start` / `.end`: the
   years before it (2017-06 to 2020-02) show excess outdoor air in 53% of the cooling-weather
   hours, a **fault** on their own, against 45% in the documented 100 % outdoor-air period
   (2020-08 to 2021-11). Cooling weather is rare in Ireland, so each period has few such hours.

**Discussion points**

- A stuck damper is two different faults depending on *where* it sticks: open wastes cooling
  in hot weather, closed wastes free cooling in cool weather. PNNL's economizer guide asks both
  questions; each CAMBER rule answers one of them.
- `free_cooling_missed` is not one of the dataset's declared detector targets, so
  `camber datasets score` doesn't count it. Ask why: without the unit's low limit it also reports
  a fault on the fault-free unit (34.1%). A detector with a non-zero baseline has to be read against the
  unit's own fault-free run, which is what question 4 does; question 5 then finds where that
  baseline comes from.
- "Favorable conditions" in the economizer guide's first question are what the unit's sequence
  says they are. The low limit is a setting of this sequence, not a CAMBER default: ask learners
  where they would find it on a real unit (the sequence of operations, the controller program,
  or a winter of known-good trends) and what the rule would read if it were set too high (stuck
  dampers lose their cold-weather evidence).
- The design minimum matters. With a generic 20 % minimum the fault-free unit would look
  under-ventilated all summer; the config uses this unit's own measured minimum (1.6 %). Point
  to the `_comment` in the exercise config and to the minimum outdoor-air guide.
- On a real unit (questions 6 and 7) the history matters: a documented decision explains part
  of the excess outdoor air, and the rest is a lead to take back to the site.

**Common mistakes**

- Reading the annual median OA fraction (about 40 % on the fault-free unit) as excess outdoor
  air. Excess outdoor air is judged on cooling-weather samples only.
- Concluding that the 10 % and 25 % runs are healthy because the OA-fraction rules are quiet.
- Treating the fault on the fault-free unit's `free_cooling_missed` as a fault to fix, without
  comparing it with the faulted runs or checking when its missed hours happen.
- Setting `low_limit_f` to make the fault go away rather than from the unit's sequence. The value
  is the documented 33.8 °F; a higher number would also set aside hours a stuck damper misses.
- Running the dataset's default config (`camber datasets config lbnl-sdahu` without
  `--exercise`) for steps 2 to 5: it already sets the low limit on `free_cooling_missed`, so the
  fault-free fault of question 5 never appears, and it adds rules this exercise does not ask
  about.
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

<!-- BEGIN slot #146 (AHU maintenance) -->

<!-- BEGIN air-maintenance -->

### `air-maintenance`: Dirty filters and a hunting valve

Figures from the synthetic `synthetic-ahu-maintenance` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.106.0-dev, with the commands on the
[exercise page](air-maintenance.md#setup). How the five units were built: one weather series
(two weeks from 2 June, about 70 °F on average) and one schedule drive five constant-volume air
handlers. Each runs every day from 05:00 to 21:00; with the fan off its filter transmitter reads
about zero and its cooling valve is shut. While it runs, the valve follows the cooling load.
`AHU__healthy` has a clean filter (0.42 to 0.48 in. w.c. with the fan running) and a loop that
settles. `AHU__loading` has a filter that loads up through the fortnight, faster as it fills,
from 0.55 to 1.60 in. w.c.: it crosses 1.0 on the eighth day. `AHU__dirty` has a filter left far
past its change-out, 1.62 to 1.82 in. w.c. `AHU__hunting` has a clean filter and a cooling-valve
PI loop tuned too hot: a limit cycle of about 11 minutes and about 20 % of stroke whenever the
unit runs, the supply air swinging against it. `AHU__near_miss` oscillates more slowly and less,
about 22 minutes and 9 % of stroke: a loop that could be tuned better, but does not hunt.

**Answer key**

1. *Filters over the fortnight.* `AHU__dirty` is a **fault**: a fan-on median of 1.72 in. w.c.
   against a change-out pressure of 1.0 (fault at 1.5). `AHU__loading` passes (`ok`, median
   0.95), although 44.6% of its running samples are above 1.0 and its 95th percentile is 1.54 in.
   w.c. The other three pass with medians under 0.55 (0.45 on `AHU__healthy`).
2. *The fan-off hours.* The fan is off 8 hours a day, a third of the run, and the transmitter
   then reads about zero. The rule sets those samples aside (`fan_gate` "fan status",
   `n_masked_fan_off` 1,344) and judges the running ones: `AHU__dirty` is above 1.0 in all of
   them (`pct_over_threshold` 100%). Judged over every sample (`fan_gate: false`),
   `AHU__loading`'s median would be 0.70, about the lowest quarter of its running samples, and a
   unit that runs under half the day could never warn however dirty its filter. A unit that
   trends no fan status, speed or airflow is judged that way, with a caveat.
3. *The last week.* `AHU__loading` warns: a fan-on median of 1.26 in. w.c., with 89.3% of its
   running samples above 1.0. `AHU__dirty` still faults and the clean units pass. Over the
   fortnight the clean first week held the median down; judged on the latest week, the filter is
   due. Schedule the change, and rerun the rule weekly on the latest week rather than once on a
   long history: a rising filter shows in the trend (and in `filter_dp_p95_inwc`) well before the
   median moves.
4. *Hunting.* `AHU__hunting` is a **warn** at 10.79 reversals per fan-on hour (2,404 in the
   fortnight's 223 running hours), not a fault. A 5-minute trend can show at most 12 reversals an
   hour (`max_resolvable_per_hr`), the fault rate itself, so the rule caps it at warn and says
   so. `AHU__near_miss` passes at 5.39 an hour: its swings are half as fast and less than half as
   large. `AHU__healthy` reads 0.00. Fan-off hours are no longer counted, so the starts and stops
   no longer add reversals.
5. *Score.* `control_hunting` scores TPR 100% and FPR 0 %. `filter_fouling` catches the dirty
   filter but misses the loading one, which is labelled `dirty_filter` because it ends the
   fortnight well past its change-out: TPR 50%, FPR 0 %. On the last week it scores TPR 100%,
   FPR 0 %. The rule did what it says, a median over the run it was given; the miss is the
   window's. Five units is a tiny sample: the 95 % intervals are wide.
6. *10 minutes.* Every unit reads `info` with no rate. A 10-minute trend can show at most 6
   reversals an hour, the warn rate itself, too close to it for the rule to judge, so it declines
   rather than pass the hunting valve. The caveat shows the averaging: `AHU__hunting` reversed on
   0% of the samples it could have (each 10-minute value is the mean of two 5-minute samples,
   which averages its 11-minute cycle away), while the slower `AHU__near_miss` reached 68%. The
   score is TPR 0 %, a decline and not a pass.
7. *15 minutes.* Every unit reads `info` again, now because a 15-minute trend can show at most
   4 reversals an hour, below the warn rate. `AHU__hunting` reversed on 65% of the samples it could have. Both coarse trends say
   the same thing: re-trend at 1 to 5 minutes and judge again.

**Discussion points**

- What does a dirty filter cost? Fan energy against the extra resistance and, on a
  variable-volume unit, lost airflow at the zones; on a constant-volume unit, less air than
  designed.
- Where should the change-out pressure come from: the filter maker's final resistance, the
  site's practice, or the clean pressure plus a margin? Read `filter_dp_median_inwc` on a freshly
  changed filter.
- What makes a loop hunt (gain too high, integral time too short, an oversized valve, a slow
  sensor), and what would you change first?
- Which points would you ask a site to trend every minute for a week, and why only for a week?

**Common mistakes**

- Reading `filter_fouling`'s `ok` on `AHU__loading` as "the filter is fine".
- Lowering `change_dp_inwc` until the loading filter warns over the fortnight: that moves the
  threshold for every unit; judge the latest week instead.
- Reading the 10-minute `info` as "no hunting": the rule declined because the trend cannot
  show it.
- Citing these figures as evidence about real air handlers: the data are synthetic.

<!-- END air-maintenance -->

<!-- END slot #146 (AHU maintenance) -->

<!-- BEGIN air-excess-oa-cost -->

### `air-excess-oa-cost`: What excess outside air costs

Figures from the synthetic `synthetic-ahu-excess-oa` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.108.0-dev, with the commands on the
[exercise page](air-excess-oa-cost.md#setup). How the two units were built: one weather series
(six weeks from 28 April, about 60 °F at the start rising to the high 70s, a cold spell in the
first week, the dew point rising from the mid-40s to the high 60s) drives two identical
variable-air-volume air handlers on a weekday 06:00-19:00 schedule, with the Guideline 36 supply
reset (55 °F above 70 °F outdoors, 65 °F below 60 °F) and 6,000 to about 11,400 cfm of supply air
following the cooling load. `AHU__excess_oa` holds its outdoor-air damper at a fixed 60 %, about
45 % outside air, in all weather. `AHU__economizer` holds its 20 % minimum except below its 65 °F
high limit, where it opens to hold the mixed air at the supply setpoint. Both declare a 20 %
minimum. `AHU__excess_oa_no_flow` is `AHU__excess_oa`'s data without the airflow point.

**Answer key**

1. *The verdicts.* `AHU__excess_oa` is a **fault** on `outdoor_air_fraction`: a median OA
   fraction of 44.9% against the 20 % minimum, above it in 100% of the cooling-weather samples
   (`excess_oa_pct`). `economizer_high_limit` faults it too, but judges only the samples above
   its 65 °F high limit. `AHU__economizer` passes both: a median of 22.2% over all occupied
   hours and 19.9% in cooling weather. `AHU__excess_oa_no_flow` gives the same verdicts as
   `AHU__excess_oa`. The label score is TPR 100%, FPR 0 %.
2. *Three paths.* Of `AHU__excess_oa`'s excess hours, 103.5 read as cooling, all on the enthalpy
   path (`oa_cost_cool_basis` "enthalpy": both humidities are trended), 14.25 as heating (colder
   outdoor air with the heating valve open, `oa_cost_heat_basis` "heat_valve") and 103.25 as
   economizing. Only cooling and heating cost money: in the economizing hours the extra outdoor
   air is free cooling, so it is priced at zero.
3. *The price.* The RCx report prices the unit at $731/yr: about $675 of cooling electricity
   (4,499 kWh at COP 3.5) and $56 of heating gas (46.4 therms at 80 %), at the package
   placeholder prices. The six weeks cover 1,008 observed hours, so the period's energy is
   scaled by 8,760 / 1,008. Both rules flag the unit, and their estimates overlap: they price
   the same extra air. The issue shows the larger, `outdoor_air_fraction`'s $731
   ("member estimates overlap; largest shown"). `economizer_high_limit`'s own estimate, $645,
   prices only its 82 h above the high limit, with no heating. Appendix B lists both rows: the
   minimum "20% (configured)", "trended supply airflow", "enthalpy, 104 h, COP 3.5" and
   "heat_valve, 14 h, boiler efficiency 0.8".
4. *The economizer.* It spends 95.25 hours above its minimum, all of them economizing (outdoor
   air colder than the return air, heating valve shut). Its verdict is `ok`, and only a warn or
   fault verdict is priced: it costs nothing and has no issue or Appendix B row. Outside air
   above the minimum is a penalty only when it has to be cooled or heated.
5. *Without humidity.* Read by dry bulb, 22 of the 103.5 cooling hours move to economizing (81.5
   cooling, 125.25 economizing): outdoor air a little cooler than the return air but much more
   humid. The estimate falls from $731 to $255/yr. In a humid climate the enthalpy reading is
   the right one: the coil has to remove that moisture, so the "free" air is not free. Trend the
   outdoor and return humidity wherever an economizer or a high minimum is in question.
6. *No airflow.* `AHU__excess_oa_no_flow` faults the same way but its issue is uncosted, "needs
   supply_airflow_cfm or design_oa_cfm", and Appendix B shows the airflow as "—". The energy is
   proportional to the airflow, so with no trended airflow and no sizing CAMBER names the input
   rather than guess.
7. *No declared minimum.* The verdicts do not move: the rule judges against its generic 20 %
   (`min_oa_source` "default"). Every estimate declines ("needs min_oa_pct ... the rule's generic
   20% is not priced"), Appendix B shows the minimum as "20% (default)" (still naming the
   trended supply airflow it would use) and nothing is costed.
   The excess is measured from the minimum, so a guessed minimum would price a guess; a real unit
   with a 15 % or 30 % design minimum would cost very differently.
8. *A design airflow.* With `design_oa_cfm` 2,400 the no-flow unit is priced from the airflow its
   declared minimum implies, 2,400 / 0.20 = 12,000 cfm: $876/yr, against $731 for the same unit
   on its trended airflow. 12,000 cfm is the unit's design flow; a VAV unit runs below it at part
   load, so the design figure overstates. The run's firm total is $1,607/yr.

**Discussion points**

- What sets a unit's minimum outside air, and how would you check a 45 % fraction on site
  (damper linkage, minimum position, an airflow station, a CO2 balance)?
- Why does the high limit matter more in a humid climate, and what would a differential enthalpy
  economizer have done on these afternoons?
- The estimate is the coil's load. What else does excess outside air cost (fan energy on a
  constant-volume unit, plant part-load efficiency, humidity carried into the space)?
- Six weeks of spring annualised: what would you trend before quoting the figure to an owner?

**Common mistakes**

- Reading economizing hours as excess-air cost, or reading `excess_oa_pct` as a cost.
- Adding the two rules' estimates for one unit: they price the same air.
- Turning off the humidity points to "simplify": the sensible path misses the latent load.
- Setting `min_oa_pct` to whatever makes the unit pass, instead of the unit's design minimum.
- Citing these figures as evidence about real air handlers: the data are synthetic.

<!-- END air-excess-oa-cost -->

## Terminal units and ventilation

<!-- BEGIN zone-reheat-overcooling -->

### `zone-reheat-overcooling`: the reheat penalty and overcooling at minimum airflow

Figures from the real `lbnl-fpu` default subset, CAMBER 0.97.0-dev (the valve-stuck-shut answer
re-checked on 0.98.0-dev), with the commands on the [exercise page](zone-reheat-overcooling.md#setup).

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
5. *The valve stuck shut.* It is not wasting reheat: it delivers none. Both rules read the
   valve's measured position (`valve_signal` = `position`), which is 0 % all year, so
   `reheat_penalty` is **ok** (0 % open) and so is `overcooling_min_flow` (no overcooling with
   reheat). Each finding carries a caveat: the demand is at or above 90 % while the position reads
   at or below 5 % on 100 % of the occupied full-demand hours, a stuck or failed valve rather than
   a reheat penalty. With only the demand (as before 0.98), both rules rated it a **fault**: the
   demand sits near full (about 93 % when open) because the zone is cold, and the discharge still
   rose 6 °F at full demand, because the parallel box's fan mixes in warm plenum air, which passed
   the valve-versus-discharge cross-check. [`zone-reheat-saturated`](zone-reheat-saturated.md)
   shows what it really is.
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
- Demand versus position. The rules that judge heat delivered read the position; the rules
  that judge how hard the controller asks read the demand. Ask what a site that trends only one
  of the two can and cannot conclude, and what a series box (whose fan runs all the time) does to
  the discharge-air rise.
- Minimums are also a ventilation question: lowering them needs the zone's 62.1 minimum. See
  [`zone-min-oa`](zone-min-oa.md).

**Common mistakes**

- Reading the stuck-shut valve's quiet `reheat_penalty` as "nothing wrong". It is quiet because
  no heat is delivered; the caveat names the stuck valve, and `reheat_capacity_shortfall` in
  [`zone-reheat-saturated`](zone-reheat-saturated.md) grades it.
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

Figures from the real `ornl-frp-vav` default subset (test set 3), CAMBER 0.97.0-dev (the
`actuator_stuck` figures and the label scores 0.98.0-dev), with the commands on the
[exercise page](zone-bad-box.md#setup).

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
7. *`actuator_stuck` and the label score.* It flags box 205 on all six stuck days and stays
   quiet on the fault-free day: TPR 100%, FPR 0 % (one negative day, so the interval is wide).
   Each stuck day's damper holds one position through all the occupied hours
   (100 % of active samples), and the rule says what contradicts it:
   - 0 %: shut through occupied hours (a **fault**; no airflow setpoint is logged, so the caveat
     says it was judged against the occupied mode alone);
   - 20 % and 40 %: part open while the room runs over its cooling setpoint (a **fault**; 6 °F
     and 3.8 °F over, median);
   - 100 %: fully open while the room sits 5.2 °F below its cooling setpoint (a **fault**);
   - 60 % and 80 %: nothing in the room contradicts the position, but it held all day while the
     room temperature moved, so it is a **warn** only. A mid-stroke position the room is happy
     with could be a healthy box holding its minimum.

   The neighbours' dampers do sit still for hours (26-41 % open for 4-10.5 h), but at a minimum
   position in a room below its cooling setpoint: consistent with what the room asked for, and
   never for a whole day, so they stay quiet. `unmet_setpoint_hours` alone would catch 2 of the 6
   stuck days and fire on the fault-free day too: TPR 33%, FPR 100 %. A comfort rule detects a
   stuck damper only on the days the stuck position hurts comfort, and it cannot tell a stuck
   box from a warm room. The dataset logs no airflow setpoint, so `airflow_tracking`, the rule
   that finds stuck dampers on `lbnl-fpu`, cannot run.
8. *Room 102.* No. Its box is not under test, and the room is too cold on every day, fault-free
   included (a **fault** each day). It is a cold room in this building. The reheat is electric
   and not trended as a valve, so CAMBER cannot say whether it was maxed out (see
   [`zone-reheat-saturated`](zone-reheat-saturated.md)).

**Discussion points**

- Chapter 7's advice to trend damper position and airflow for every box is the whole exercise.
  A box that does not move while its neighbours do is the cheapest fault signature there is. No
  comfort rule reproduces it, and neither does a plain "flat for four hours" test: the healthy
  boxes fail that one. `actuator_stuck` works because it asks whether the room wanted the damper
  somewhere else.
- On the full subset `actuator_stuck` finds 17 of the 18 stuck days and none of the 13
  fault-free and airflow-bias days. It misses set 1's 100 % day: the room ran warm, so a fully
  open damper looked like the right answer. A box stuck where a busy day would have put it
  anyway cannot be told from a working one.
- The going-further cohort question: grouped per day, the raw mean airflow never singles out
  box 205 (robust z -1.5 to +2.0), and airflow as a share of each box's own peak flags six
  healthy box-days and scores the fault-free day at z -3.1. Evening out sizes does not isolate a
  stuck box. Its airflow against its own fault-free day does (all six days), and so, partly, does
  its damper's variability on the low tail (z -2.1 to -5.0; three of six past the default 3.5).
- The zone cohort needs a common basis. Each day's boxes are compared under that day's rooftop
  unit (the naming topology), never across days. Ask what goes wrong when a census pools
  boxes from different days (the dataset config's `_comment` explains why `cohort_airflow` is
  left out of the default run, and its `group_by_topology` option fixes the pooling).
- The labelled "fault-free" day is not a trouble-free building. Discuss room 205's warm
  afternoons and room 102's cold mornings as findings in their own right.

**Common mistakes**

- Concluding that the 0 % and 60–100 % days are healthy because `unmet_setpoint_hours` is quiet.
- Reading the 60 % and 80 % warnings as weaker evidence of a fault than the others. The damper is
  just as stuck; the room just did not contradict it that day.
- Reading the rogue-zone census on the fault-free day as a false alarm. It correctly names the
  zone driving the reset, which happens to be a warm room, not a broken box.
- Comparing raw airflows across rooms of different sizes and calling the smallest box the
  starved one.
- Blaming the rooftop unit because its airflow and static change. They follow the stuck box;
  the cohort-starvation census says the cohort as a whole is fine.

<!-- END zone-bad-box -->

<!-- BEGIN zone-reheat-saturated -->

### `zone-reheat-saturated`: a zone below setpoint with its reheat maxed out

Figures from the real `lbnl-fpu` and `ornl-frp-vav` default subsets, CAMBER 0.97.0-dev (answers 4
and 5 re-checked on 0.98.0-dev), with the commands on the
[exercise page](zone-reheat-saturated.md#setup).

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
4. *Which signal?* `reheat_capacity_shortfall` reads the controller's reheat *demand*
   (`heat_valve`): the controller keeps asking for full heat because the zone stays cold, and the
   valve stuck shut cannot respond, so the reheat reads maxed out. `reheat_penalty` reads the
   valve's measured *position* (`heat_valve_position`, `valve_signal` = `position`), which is 0 %
   all year: it is **ok**, 0 % open, with a caveat that the demand is at or above 90 % while the
   position reads shut on 100 % of the full-demand hours, a stuck or failed valve rather than a
   reheat penalty. Before 0.98 it read the demand as reheat delivered in 51% of occupied hours and
   rated it a **fault**: the parallel box's fan mixes in warm plenum air, so the discharge rose a
   few degrees, enough to pass the valve-versus-discharge check (the config now allows for that
   lift with `fan_heat_f` = `"auto"`). On site: stroke the valve, check the discharge air at full
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
- Reading `reheat_penalty`'s **ok** on the stuck-shut valve as "the reheat is fine". It is ok
  because no heat is delivered; read its caveat.
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
6. *Over-ventilated and functioning?* No contradiction. Judged on the hours each room's presence
   sensor reads occupied, CO₂ sits near outdoor in 30 to 66% of them. Only room 925038 (66%)
   reaches the over-ventilation **warn**; the other four are `ok`. On the assumed weekday 07-18
   schedule, which counted empty hours, all five were **warn** at 73 to 86%. DCV responds to
   demand. Whether the minimum is higher than the rooms need is a separate, energy question.
7. *The Danish rooms.* `ROOM1`, the lecture room, functions: a 181 ppm lift, correlation 0.89.
   `ROOM2` and `ROOM3` are "DCV not judged -- CO₂ never reached the level where DCV should
   respond" (reason `demand_below_engage`). The study zones never got busy enough to test the
   DCV. That is neither good nor bad news about the controls, only an absence of evidence.
   `ROOM2` is also over-ventilated (CO₂ near outdoor in 74% of occupied hours).
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

Figures from the real `lbnl-b59` default subset, CAMBER 0.98.0-dev, with the commands on the
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
   given it a verdict. The finding also says "supply fan off while scheduled occupied", for
   41, 82, 40 and 40 h by unit: the October and December 2020 days when the fans did not run
   although the schedule says occupied (speed feedback a few percent). Those hours are the whole 2.0–4.2 % below the
   750 cfm floor, so `below_floor_pct`, the shortfall with the fan running, is 0.0–0.1 % and the
   grade stays `info` (since 0.98). The 2020 smoke-mode weeks are not among them.
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

<!-- BEGIN slot #144 (G36 VAV fleet) -->

<!-- BEGIN zone-g36-fleet -->

### `zone-g36-fleet`: A G36 VAV fleet: rogue zones, starved cohorts and resets

Figures from the synthetic `synthetic-g36-vav-fleet` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.108.0-dev (re-recorded after the generator's version 3, #226,
and the zone-rule fixes #238, #239 and #241), with the commands on the
[exercise page](zone-g36-fleet.md#setup). How the eight air handlers were built: one spring
weather series (about 46-80 °F, three weeks from 28 April) drives eight copies of a VAV air handler
with six single-duct reheat boxes. The zones' loads rise and fall together (people, lights and
plug loads dominate; the solar peaks are small). Each box asks for the airflow its zone's load
needs at the current supply-air temperature, gets what the duct static lets through, and its zone
warms by what it could not get. The boxes' Guideline 36 reset requests are summed, and each reset
takes three trim-and-respond steps an hour on the sum (CAMBER's `SAT_TR` and `STATIC_TR` presets:
SAT 55-65 °F, static 0.1-1.5 in. w.c.), holds its start value (65 °F, 0.5 in. w.c.) while the fan
is off, and keeps a tier-1 request until the loop or the damper falls below 85 %.
`AHU__healthy` has no fault and a dual-maximum reheat sequence. `AHU__rogue_sat`'s zone 1 is an
equipment room no box can cool (83.8 °F on average while occupied). `AHU__rogue_static`'s north
box 4 has a blocked inlet: its damper is wide open and it gets 132 cfm against a 330 cfm minimum,
but its room has almost no load and stays comfortable. `AHU__starved`'s supply fan is capped: the
duct static cannot rise past 0.06 in. w.c. while its setpoint climbs to the top of its range.
`AHU__not_responding`'s reset logic ignores its requests and only trims: the SAT setpoint sits at
65 °F and the static setpoint trims from 0.5 to 0.1 in. w.c. within two hours of each morning's
start. `AHU__reheat`'s west and north boxes reheat at 80 % of their cooling maximum airflow.
`AHU__heat_cool`'s boxes 1 to 5 have a 74 °F heating setpoint above a 73 °F cooling setpoint.
`AHU__near_miss`'s zone 1 carries a little more load than the healthy fleet's on weekday
mornings.

**Answer key**

1. *The resets.* Only `AHU__not_responding` warns on both. Its SAT reset reads **stuck**: 0.06 °F
   of swing against an expected 10 °F, because the reset sits at its 65 °F trim end and never
   leaves it. Its static reset reads `not_responding`: it trims from its 0.5 in. w.c. start to 0.1
   every morning (a swing of 0.4 in. w.c.) and sits at its energy-saving end on 100 % of the
   cycles that asked for more. One fault, two readings: `reason` is the first failure mode, and
   "stuck" comes before "not responding"; the SAT finding's `failure_modes` lists both. Every
   other reset is effective. `AHU__healthy`'s SAT reset swings 6.82 °F; its zones ask together,
   so the reset answers them before any is left warm.
2. *A reset doing its job.* `AHU__rogue_sat`'s SAT reset is effective: it swings 10.05 °F, as its
   requests ask, down to 55 °F every afternoon. The fault is the zone: the SAT census names
   `VAV1__rogue_sat`, which sends all of its air handler's SAT requests and holds the largest one
   on every cycle with any request. The other five zones get air far colder than they need, and
   the cooling plant pays for it. Fix the room (its load or its box), or stop the reset counting
   that zone's requests.
3. *Static rogues.* The static census names `VAV4__rogue_static` (all of its air handler's static
   requests). `AHU__rogue_static`'s reset is effective too: it swings the full 1.0 in. w.c. from
   its start to its top and holds the static there all day for one box. It lists
   `VAV1__rogue_sat` (80.3 % of its air handler's static requests) apart, under
   `overloaded_zones`: the equipment room's box is wide open because the room is short of
   cooling, 3 °F or more above its setpoint on every one of its static requests. It drags the
   duct static as well as the SAT reset, but it is the SAT rogue, not a blocked inlet.
4. *Starved cohorts.* SAT cohort starvation names `AHU__not_responding` (all six zones asking at
   once on 83.1% of the cycles with any request). Static cohort starvation names
   `AHU__not_responding` (86.1%) and `AHU__starved` (100%), and its summary names both, worst
   first. The reset findings tell them apart: `AHU__starved`'s resets are effective,
   with the static setpoint at its 1.5 in. w.c. top, so look at the fan (its drive, its speed
   limit, an upstream damper); `AHU__not_responding`'s are not, so look at the reset logic and
   its request input. `AHU__starved`'s zones are warm too, but on fewer cycles at once (43 %),
   under the SAT side's 50 % bar.
5. *Peers.* `cohort_airflow`, comparing each box with the other five of its air handler, flags
   `VAV1__rogue_sat` (high) and `VAV4__rogue_static` (z -4.0, low). Compared building-wide it
   flags nothing: the boxes range from 600 to 1,100 cfm, and that spread hides both (the rule
   then reads `info`, the cohort too unlike to judge, rather than `ok`).
   `cohort_space_temp`, building-wide, flags `VAV1__rogue_sat` (z 15.1), five of
   `AHU__not_responding`'s boxes and four of `AHU__starved`'s: the warm boxes behind the two
   faulted air handlers.
6. *Reheat.* `reheat_minimization_g36` faults on seven boxes: `VAV2__reheat` and `VAV4__reheat`
   (reheating 12.8 % and 16.1 % of occupied hours, 56.5 % and 72.4 % of it above minimum airflow
   outside the high heating loop) and `VAV1__heat_cool` to `VAV5__heat_cool`. The healthy
   fleet's west box reheats too (10.6 % of occupied hours) and passes: it rises above its
   minimum only in its high heating loop (heating loop over 50 %, 5.3 % of its reheating hours),
   where the dual-maximum sequence raises the airflow by design. `zones_heat_cool_census`
   asks whether some zones heat while others cool. Building-wide it faults: 100 % of occupied
   hours have one, with the five `heat_cool` boxes heating persistently. On `AHU__reheat`'s boxes
   alone it passes (8.9 %): two boxes reheating on cool mornings while the others cool are not a
   fleet problem, and only the per-box rule finds them. On `AHU__heat_cool`'s boxes alone it
   faults (85.0 % of hours; 92.8 % have a box reheating above its minimum, 3.88 boxes on
   average): look at the heating setpoint, which sits above the cooling setpoint.
7. *Score.* `camber datasets score` scores the five per-equipment rules. The three reset and
   reheat rules each find their labels (TPR 100 %, FPR 0 %), but with one, one and seven
   positives the intervals are wide. `airflow_tracking` finds 14 of 14 labelled boxes (the twelve
   behind the starved and the not-responding air handlers and the two rogues) with no false
   positive. `unmet_setpoint_hours` finds 13 of 14: it misses the blocked box, whose room stays
   comfortable, and warns on one `heat_cool` box (an FPR of 2.9 %). Overall
   detection is 22 of 23: only the starved air handler itself is left, found by the fleet rules on `<fleet>`, which
   the score cannot match to any equipment. Check it from the findings' metrics, as in step 4.
8. *Healthy and near miss.* On the healthy fleet alone every rule passes: the censuses,
   `cohort_space_temp` (no outlier among zones within a spread, MAD, of 0.12 °F),
   `zones_heat_cool_census` (6.7 %; 2.2 % with one box heating above its minimum), and every box's
   `unmet_setpoint_hours` and `airflow_tracking`. The near miss's zone 1 sends 30.8 % of its air
   handler's SAT requests and holds the largest one on 44.4 % of the active cycles: the most of
   its six, close to the one-third share bar but clearly under the 50 % binding bar, so it is not
   a rogue. The healthy fleet's zone 1 sends 23 % and holds 36 %.

**Discussion points**

- A reset that does exactly what its requests ask can still be wrong for the building. Who
  decides which zones it should listen to, and how would you write that down?
- Why does a rogue-zone census need to know which boxes each air handler serves? What would it
  say here if it pooled all 48 boxes?
- A starved cohort points upstream. Which trends would you ask for next on `AHU__starved`?
- When should a peer comparison compare a box with its air handler's other boxes, and when with
  every box of the same size or exposure?

**Common mistakes**

- Blaming `AHU__rogue_sat`'s reset: it is effective; the zone is the fault.
- Calling `VAV1__rogue_sat` a static rogue: the census lists it as overloaded, short of cooling.
- Treating the label score as the whole answer: the fleet rules are not in it.
- Calling the healthy west box's high-airflow reheat hours a fault: at a wide-open valve they
  are the dual-maximum sequence working.
- Citing these figures as evidence about real VAV systems: the data are synthetic.

<!-- END zone-g36-fleet -->

<!-- END slot #144 (G36 VAV fleet) -->

<!-- BEGIN rcx-cause-chains -->

### `rcx-cause-chains`: RCx cause chains: one air handler, many boxes

Figures from the synthetic `synthetic-g36-rcx-chains` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.108.0-dev, with the commands on the
[exercise page](rcx-cause-chains.md#setup). The five air handlers run the G36 fleet model of
`zone-g36-fleet` (generator version 3) and trend their fan speed and cooling valve.
`AHU__healthy` has no fault. `AHU__one_bad_box` is healthy but its box 3's damper is stuck at
30 %. `AHU__warm_sat`'s cooling coil cannot make supply air below 63 °F, its valve wide open
whenever the reset asks for colder air. `AHU__low_static`'s supply fan makes at most 0.35 in.
w.c. at full speed. `AHU__cold_sat`'s SAT reset is disabled at 55 °F, and its west and north
boxes run a single-maximum heating sequence (they reheat at 60 % of their cooling maximum).
The run template declares the served-by topology and each unit's reheat-coil size.

**Answer key**

1. *Chains.* Three issues carry a cause chain. `AHU__warm_sat`: supply air too warm, zones
   can't cool, 5 of 6 boxes. `AHU__low_static`: duct static too low, starved boxes, 5 of 6
   boxes. `AHU__cold_sat`: supply air too cold, overcooling and reheat at the boxes, 2 of 6
   boxes (VAV2 and VAV4). The north box `VAV4` needs little air, so neither the warm supply air
   nor the weak fan leaves it short: it has no finding, and the chain counts it among the six
   the air handler serves but not among the linked ones.
2. *Roots.* `g36_afdd` roots the first two: fault condition 13 (supply air too warm with the
   cooling valve wide open) on `AHU__warm_sat` and fault condition 1 (duct static below its
   setpoint with the fan at full speed) on `AHU__low_static`. `supply_air_reset` roots the third:
   the supply air sits at 55 °F with no upward reset.
3. *Cost.* The cold-supply chain's members are `VAV2__cold_sat`'s and `VAV4__cold_sat`'s reheat
   findings, costed at $74.39 and $122.99 a year from the declared coil sizes. The issue costs
   $122.99, the largest member, not their $197.38 sum: the reheat at the two boxes is what the
   cold supply air forces, one waste seen twice. The supply-air-reset finding is uncosted.
4. *The bad box.* `VAV3__one_bad_box` has two issues of its own (the zone unmet, the airflow
   below setpoint) and joins no chain: its air handler has no finding, so there is nothing
   upstream to explain it. The damper reads 30 % whatever the airflow needs: check the actuator
   and the linkage.
5. *Without the topology.* 22 issues instead of 5, a costed total of $197.38 (both reheat boxes
   counted), and Appendix A lists the 13 boxes left unlinked and the three air-handler causes
   found (duct static too low, supply air too cold, supply air too warm). The operator needs the
   5-issue report: three fixes upstream and one box.
6. *Score.* Every scored rule finds its labels with no false positives: overall detection finds
   16 of 16 labelled pieces of equipment.

**Discussion points**

- A chain is only as good as the served-by map. Where would you get it on a real site, and how
  would you check it?
- Why is the largest member, not the sum, the right cost for a chain? When might two members
  really be two wastes?
- The warm-supply and low-static chains are uncosted. What would you need to price them, and is
  the comfort cost enough to act?

**Common mistakes**

- Sending a technician to each of the ten warm or starved boxes before looking at their air
  handlers.
- Folding `VAV3__one_bad_box` into a chain because it is warm: its air handler is healthy.
- Adding the members' costs: the chain's cost is its largest member.
- Citing these figures as evidence about real VAV systems: the data are synthetic.

<!-- END rcx-cause-chains -->

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

Figures from the real `lbnl-chiller` full subset, CAMBER 0.97.0-dev (the fan-effort drift:
CAMBER 0.98.0-dev), with the commands on the [exercise page](plant-cooling-tower.md#setup).

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
6. *Label score and fan effort.* `cooling_tower_approach` scores TPR 0% and FPR 0 %: it finds
   none of its four targets (the three tower foulings and the PI mistuning, on which it declines
   because the fan never reaches 90 %). `cooling_tower_fan_effort_drift` measures what answer 3
   read by hand: the fan speed at matched condenser range and wet-bulb, here against the declared
   reference `PLANT__fault_free`. It is a **fault** on the 65 % and 80 % foulings (+17.8 and
   +10.9 fan %-points) and `ok` on the 95 % one (+3.3, under its 5-point floor): TPR 67% on its
   target, tower fouling, and FPR 0 %, with no alarm on the other 20 runs. The fault-free run
   declines as `is_reference` (it is the yardstick; the scorer still counts it as a correct
   negative, so the false-positive count reads 0 of 21). The +1 and +2 °F tower-sensor biases also
   push the fans harder (+29 and +48 points), but the tower's leaving-water reading moved with
   them (+1.6 and +3.5 °F), so the rule reports a sensor offset (`info`), not fouling.

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
- On the bypass runs `cooling_tower_fan_effort_drift` reads hundreds of fan %-points *below*
  the reference (about -390 to -470), which no fan can do. The bypassed loop runs condenser
  ranges the reference never saw, so the matched-load model extrapolates: read it as "not
  comparable", not as a healthier tower. `condenser_bypass_leak` is the rule for those runs.

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
- Reading the reference's `info` decline as a missing result, or comparing a real plant's tower
  with another tower that was never shown to be healthy: a reference is only as good as the
  evidence that it is clean.

<!-- END plant-cooling-tower -->

<!-- BEGIN plant-chw-reset-pumping -->

### `plant-chw-reset-pumping`: Chilled-water reset and pumping

Figures from the real `lbnl-chiller` default subset, CAMBER 0.98.0-dev (the pumping figures
0.105.0-dev), with the commands on the [exercise page](plant-chw-reset-pumping.md#setup).

**Answer key**

1. *Reset.* Yes: `chwst_reset_present` is true and `chwst_reset_direction` is `expected`, with a
   slope of -0.38 °F of supply per °F of outdoor dry-bulb (median supply 47.6 °F over the
   running, occupied hours). The supply gets warmer in cool weather and colder in hot weather,
   which is the direction the rule expects (`expected_reset_sign: negative`). A slope the other
   way would not count as a reset.
2. *Low delta-T.* Chiller 1's loop delta-T has a median of 6.4 °F, and 80.1% of its running hours
   are below the rule's 8 °F minimum. Yet `chw_plant_reset` is **ok** on the healthy plant: the
   rule reads the chiller's flow, finds it constant (`flow_mode: constant`, a coefficient of
   variation of about 0.1 %), and reports the delta-T without judging it, with a caveat. A
   constant primary flow does not follow the load, so the chiller's delta-T is low at part load
   by design: a property of this plant, and a design question (variable primary flow, a higher
   supply setpoint in mild weather), not a broken component. On a variable-flow plant the same
   figures would count, against `design_deltaT_min_f` (8 °F unless set from the loop's design).
3. *DP reset and pumping.* No: the DP setpoint is flat (`dp_sp_reset_present` false, the same value
   all year). Judged on the hours it runs (`run_source: status`, 5,890 of the year's 8,759), the
   secondary pump (`SECPUMP__fault_free`) runs at a median 91.8% and at 90 % or more in 56.5% of
   its hours: a **warn** for riding the curve against a flat DP setpoint, close to the 60 % fault
   line.
4. *VFD minimum.* There is no floor to see in the running speeds: `vfd_floor_pct` is null,
   `near_min_source` is `default` (the 25 % band) and 0.0% of the running hours are near the
   minimum. The 34.5 % that stands out in the speed trend is not a floor: it is the speed point
   holding the stopped DP loop's last output while the pumps are off, in 2,869 of the fault-free
   run's hours (`n_stopped_speed_above_run`; the caveat says they were judged as stopped). Until
   0.105 the rule read running from speed alone, so it learned 34.5 % as the floor and counted
   those stopped hours as a pump idling there
   ([#180](https://github.com/yroussev/camber/issues/180)).
   The run where the pump runs slowest is the chiller-bias one (`SECPUMP__chiller_bias_2`): the
   pumps are stopped, speed parked, for 6,210 hours, which is why it used to read as a median of
   34.5 % and a warn for sitting at the minimum. On the 2,549 hours they run, the median is 70.3%
   with 8.7% near full: **ok**. Its chiller sensor reads 2 °C high, so the real chilled water is
   colder than it reads and the air handlers need less of it (the
   [sensor exercise](plant-sensor-vs-equipment.md) takes this up).
5. *Stuck bypass.* The plant cannot make chilled water: the supply median is 65.0 °F and the delta-T
   0.7 °F, and the pump runs at a median 100%. The supply warms as the weather warms, a slope of
   +0.76 °F per °F, the reverse of a reset: `chw_plant_reset` is a **warn** with
   `chwst_reset_direction: reverse` (its low delta-T is not judged on this constant-flow plant).
   That warn and `chw_pump_dp_reset` on `SECPUMP__bypass_stuck_075` (a **fault**: 95.5 % of its
   running hours at 90 % or more) are both symptoms; the cause is the tower bypass, which only
   `condenser_bypass_leak` names
   ([`plant-cooling-tower`](plant-cooling-tower.md)).

**Discussion points**

- Map the three answers onto the PNNL cooling guide's three questions. The guide's low-delta-T
  test uses the same 8 °F line; discuss why it does not fit a constant-flow primary loop, and
  what the rule does instead.
- Why does a flat DP setpoint cost energy? Pump power goes roughly with the cube of speed, so the
  hours near full speed dominate the bill.
- A speed point that holds its last value while the pump is stopped looks just like a pump
  idling at its floor. What tells running from stopped here (a status, the pumps' kW, the loop
  flow), and what should a rule do on a plant that has none of them?
- The stuck bypass shows how one fault fans out into symptoms on every loop. Ask which finding
  a technician should act on first.

**Common mistakes**

- Fixing the low delta-T by chasing a broken part: nothing is broken; the plant is designed that
  way.
- Reading the `ok` on the healthy plant as "the delta-T is fine": it is low, and the rule says
  why it does not judge it.
- Reading the bypassed plant's positive slope as a working reset: it is the plant losing its
  chilled water in hot weather.
- Blaming the pump for the bypassed plant: it runs flat out because the chilled water is warm.
- Reading the 34.5 % plateau in the speed trend as the VFD minimum: the pumps are stopped then.

<!-- END plant-chw-reset-pumping -->

<!-- BEGIN plant-boiler -->

### `plant-boiler`: Boiler plant

Figures from the real `lbnl-boiler` full subset, CAMBER 0.97.0-dev (the firing rules and the
boiler efficiency drift: CAMBER 0.98.0-dev), with the commands on the
[exercise page](plant-boiler.md#setup).

**Answer key**

1. *Which rules run.* All four point-in-time rules, on every run: 68 findings, 17 per rule, all
   `ok` (the boiler drift family's findings come on top; see answer 4).
   `boiler_summer_lockout`, `boiler_short_cycle` and `hw_plant_deltat` need to know when the
   boiler fired. The plant's status point is the boiler enable, on all year even while the
   boiler burns no gas, so the catalog leaves it unmapped. The three rules accept either a run
   status or the boiler's gas input, and here they read firing from boiler 1's gas input (above
   5 % of its own 95th percentile): each of their findings carries `run_source` `gas` and a caveat
   that says so. The fault-free boiler fires in 37.8% of the hours, with 0.92 starts a day; the
   hourly trend hides any firing shorter than an hour, so the start count is a floor. Before
   CAMBER 0.98 the three rules printed nothing here, and the RCx report listed them under
   "Checks not evaluated"; that table is now gone.
2. *Reset and summer lockout.* The lockout is judged: the fault-free boiler spends
   0% of its firing hours above the 65 °F lockout (0.35% on `PLANT__boiler_PI`; occupied hours),
   so `ok`. The reset is
   judged too, from the supply temperature against the outdoor temperature over firing hours:
   `hws_reset_present` is false on every run (a flat supply), which the rule reports in its
   summary without raising the severity. There is no supply-temperature setpoint to compare
   with. The trend viewer shows the same: the loop supply flat all year, and boiler 1's gas input
   falling to nearly zero in warm weather while the plant stays enabled and pump 1 keeps running.
3. *Pumping.* `hw_pump_dp_reset` is `ok`: a median speed of 29.0%, almost no hours near full
   speed, none near the 25 % minimum band, so neither riding the curve nor pinned at minimum.
   What the `ok` leaves out: the DP setpoint is flat (`dp_sp_reset_present` false), and the pump
   ran in 8,759 hours, every hour of the year, summer included.
4. *Fouling.* No point-in-time rule fires on `PLANT__boiler_foul_065`: the pump and the three
   gas-read firing rules are all `ok`. The loop delta-T holds its 36.0 °F median; only 12.2% of
   firing hours fall below the 20 °F design floor, under the 20 % warn line. Fouling shows as more
   gas for the same heat, and no point-in-time rule here compares the two.
   `boiler_efficiency_drift` does, against the declared reference `PLANT__fault_free`: gas
   input per unit of heat delivered is +53.7% on the 65 % fouling and +24.9% on the 80 % one (both
   **fault**), and +5.1% on the 95 % one (**warn**; it clears the 5 % warn floor by 0.1 point, so
   a slightly different reference period could miss it). The +2 and +4 °F hot-water temperature
   biases raise the ratio too (+11 % and +25 %), but the gas at matched outdoor temperature did not
   rise: the rule reports a heat-metering problem (`info`), not fouling. The fault-free run
   declines as `is_reference`.
5. *DP sensor bias.* A DP sensor reading 20 % high makes the pump slow to a median 26.4%, and
   one reading 20 % low speeds it up to 32.4% (29.0% healthy), while the DP reading itself stays
   at its setpoint: the controller holds the lying reading there. The rule stays `ok` on both.
   The label score for `boiler_efficiency_drift` is TPR 100% (3 of 3 foulings) and FPR 0 %:
   none of the other 13 runs fire (the scorer still counts the reference as a correct negative,
   so the false-positive count reads 0 of 14). Nothing else in this config detects any of the 16
   faults.

**Discussion points**

- The heating guide asks the same three questions as the cooling guide (reset, delta-T, DP
  reset), plus a summer shutdown. Which could a technician answer from this plant's trends, and
  which need a point it does not have?
- An enable is not a run status. The gas input stands in for one here; ask what trend you would
  add on a real plant (burner firing, flame signal, gas valve) to count short firings the hourly
  gas trend cannot see.
- A pump that runs all summer for a boiler that does not fire is a scheduling opportunity even
  though every rule says `ok`.

**Common mistakes**

- Reading a short-cycle `ok` from the gas input as proof of long firings: sub-hour cycles are
  invisible on an hourly trend.
- Mapping the enable point as the boiler status to make the rules run: every idle hour would
  then count as firing.
- Reading the pump's `ok` as "nothing to re-tune": the flat DP setpoint and the all-year running
  are both opportunities.
- Expecting a temperature rule to see boiler fouling: the supply temperature is held at setpoint
  whatever the boiler's condition.

<!-- END plant-boiler -->

<!-- BEGIN plant-lockout -->

### `plant-lockout`: Heating plant lockout

Figures from the synthetic `synthetic-hw-plant-lockout` entry (generated by CAMBER, so every
copy gives the same numbers), CAMBER 0.103.0-dev, with the commands on the
[exercise page](plant-lockout.md#setup). How the four plants were built: one weather series
(eight weeks from early May, a warm spell in late May and early June) drives four plants whose
outdoor-air enable switches off above their lockout and back on 3 °F below it. While enabled, the
boiler fires when the building needs heat (below 55 °F, a morning warm-up, and zones reheating in
occupied hours) and the pump runs. `PLANT__locked_out` locks out at 65 °F; `PLANT__warm_spell`
does too, but its lockout is overridden through the warm spell; `PLANT__pump_left_on` locks the
boiler out at 65 °F but its pump runs every hour; `PLANT__low_lockout` locks out at 58 °F.

**Answer key**

1. *At 65 °F.* `PLANT__locked_out` and `PLANT__low_lockout` pass both rules (`ok`, 0 % of their
   running hours above the lockout). `PLANT__warm_spell` faults on both: the boiler fires in
   34.62% of its occupied firing hours above 65 °F, the pump runs in 39.53% of its running hours
   above it. `PLANT__pump_left_on` passes `boiler_summer_lockout` and faults on
   `hw_pump_summer_lockout` (70.45%).
2. *The pump on its own.* On `PLANT__pump_left_on` the pump runs in every occupied hour, and the
   boiler is off in 100% of the pump's hours above the lockout (`boiler_off_pct`): the pump was
   left running (in hand, overridden, or on a schedule) after the boiler was locked out. The
   boiler's check reads firing only, so it cannot see a pump. On the warm-spell plant the boiler
   was off in 36.47% of the pump's warm hours: the plant was enabled, the pump ran with it, and the
   boiler fired only when something called for heat. The RCx report groups that plant's two
   findings into one issue, the boiler first, since one lockout fixes both.
3. *Score.* Both rules score TPR 100% and FPR 0 % on the four plants: the boiler rule catches
   the one plant labelled `boiler_not_locked_out` and passes the other three (the pump-left-on
   plant is not a boiler fault); the pump rule catches both labelled plants (the warm spell counts
   for the pump too) and passes the two healthy ones. Four plants is a tiny sample: the 95 %
   intervals are wide.
4. *At 58 °F.* Set on `boiler_summer_lockout` only; the pump rule judges at 58 °F too (its
   `lockout_oat_f` is 58, its `param_basis` says "inherited from boiler_summer_lockout in this
   config"). Now the two plants that lock out at 65 °F fail the boiler rule:
   `PLANT__locked_out` fires in 65.22% of its firing hours above 58 °F, a fault, though it
   behaves exactly as its own sequence says. `PLANT__low_lockout` still passes both rules: it
   stops firing at 58.0 °F, so its own lockout is about 58 °F.
5. *At 75 °F and where each plant stops.* The warm-spell boiler drops to a `warn` (17.95%): a
   higher lockout hides most of the warm spell. The pump still faults on the warm-spell and the
   pump-left-on plants. The warmest firing hour (`max_oat_running_f` on the boiler finding) is
   64.7 °F on `PLANT__locked_out`, 64.8 °F on `PLANT__pump_left_on`, 58.0 °F on
   `PLANT__low_lockout` and 79.9 °F on `PLANT__warm_spell`. Choose the lockout from the site's own
   sequence of operations, or from the outdoor temperature above which no zone called for heat
   in a known-good season, never from the default; then set it once on `boiler_summer_lockout`.

**Discussion points**

- The same plant passes or fails depending on one number. Who owns that number on a real site,
  and where is it written down?
- Is a pump running all summer always a fault? Discuss dehumidification reheat, domestic hot
  water and freeze protection, and what each would do to the lockout.
- Both rules judge occupied hours only. What would a pump running every night add, and how would
  you see it?

**Common mistakes**

- Reading the boiler's `ok` on `PLANT__pump_left_on` as "the plant is locked out".
- Lowering the lockout until a plant fails and calling it a fault: at 58 °F the plant that locks
  out at 65 °F "fails" while doing what its sequence says.
- Citing these figures as evidence about real plants: the data are synthetic.

<!-- END plant-lockout -->

<!-- BEGIN plant-boiler-cycling -->

### `plant-boiler-cycling`: Boiler short-cycling and low hot-water delta-T

Figures from the synthetic `synthetic-hw-plant-cycling` entry (generated by CAMBER, so every
copy gives the same numbers), CAMBER 0.104.0-dev, with the commands on the
[exercise page](plant-boiler-cycling.md#setup). How the four plants were built: one weather series
(three weeks from early March, warming from about 30 °F to 40 °F on average) and one building
load drive four plants. Each boiler holds its loop temperature on a differential: while it fires
at its lowest output above the load the loop warms, it stops at the top of the differential and
restarts when the loop has cooled to the bottom. `PLANT__well_tuned` has a modulating boiler of
1.2 times the design load that turns down to 25 % of its capacity, so it follows the load and
never stops. `PLANT__low_delta_t` has the same boiler, but its coils have three-way valves and its
pump runs at a constant 95% on a flat DP setpoint. `PLANT__short_cycling` has an on/off boiler of
twice the design load on a tight differential: one or two 15-minute samples firing, then
mostly three or four off. `PLANT__marginal` has a boiler of 1.5 times the design load that turns down only to 60 %, so
at low loads it cycles every three hours or so.

**Answer key**

1. *Starts.* `PLANT__short_cycling` is a **fault**: 20.2 starts a day (424 in 21 days), against
   a limit of 6 (fault at 12). It fires in only 23.6% of the samples: a boiler twice the size of
   the load, firing in short bursts. `PLANT__marginal` is a **warn** at 8.15 starts a day.
   `PLANT__well_tuned` and `PLANT__low_delta_t` pass: one start in three weeks, the first sample
   of the trend, since the boiler then fires throughout.
2. *Loop and pump.* `PLANT__low_delta_t` faults on both: its loop delta-T has a median of
   8.6 °F, with 100% of its occupied firing samples under the 20 °F design minimum, and its pump
   runs at a median 95% with 100% of its samples at or above 90 % and a flat DP setpoint
   (`dp_sp_reset_present` false). The three plants with two-way valves have a median delta-T near
   24 °F (24.1 °F on `PLANT__well_tuned`), a DP setpoint that moves with the load and a pump at
   about half speed. The short-cycling plant passes both: its loop and pump are fine, only the
   boiler cycles. The rules judge the loop over firing samples, so the idle samples between its
   firings do not count.
3. *Score.* `hw_plant_deltat` and `hw_pump_dp_reset` score TPR 100% and FPR 0 %: they catch the
   one plant labelled `low_delta_t` and pass the other three. `boiler_short_cycle` catches the
   short-cycling plant (TPR 100%) but its warn on `PLANT__marginal`, which is labelled healthy,
   is a false positive: FPR 33% (one of three). Whether that warn is a fault depends on the
   boiler: see answer 5. Four plants is a tiny sample: the 95 % intervals are wide.
4. *Hourly.* The short-cycling boiler is still caught, but only just: `warn` at 11.2 starts a
   day instead of a `fault` at 20.2. A run resampled to an hour keeps each hour's firing as a
   duty, so the boiler reads 0.25 in most hours. CAMBER counts the fewest starts those duties
   allow: one per firing hour. Two short firings in one hour, or one firing that runs across the
   hour boundary, count as one start, so the count is a floor. `runtime_pct` stays 23.6%, the
   true share of time firing. Every hourly finding carries a caveat that a 60-minute trend is
   coarser than the 15 minutes the count needs. A boiler below the limit whose hours show
   part-hour firing would read `info` ("cannot confirm"), not `ok`. The marginal boiler warns at
   8.16 starts a day. The label score stays TPR 100%. Keep the trend at the resolution it was
   logged: an hourly trend can show that a boiler cycles, but not how often.
5. *A limit of 10.* `PLANT__marginal` passes (`ok`, 8.15 under 10) and `PLANT__short_cycling`
   still faults (20.2 is over twice 10); the score is TPR 100%, FPR 0 %. The limit belongs to the
   boiler: take it from the maker's minimum run time or maximum cycles per hour, or from the
   boiler's controls, and set it once on `boiler_short_cycle`. Raising it until a warn goes away
   is not calibration.

**Discussion points**

- Why does short-cycling cost energy and money? Purge losses at every start, the boiler never
  reaching its steady efficiency, and wear on the ignition and the controls.
- What fixes a short-cycling boiler: a wider differential, a lead boiler with more turndown, a
  smaller boiler for the shoulder season, or a minimum run time in the sequence?
- A low delta-T plant moves water to no purpose. Ask what the three-way valves and the flat DP
  setpoint each contribute, and which is cheaper to fix.
- Which trend would you ask the site for, and at what interval, to see cycling on a real boiler?

**Common mistakes**

- Reading the short-cycling plant's `ok` on `hw_plant_deltat` as "the plant is fine".
- Resampling the trend to an hour "to make it manageable" and reading the hourly starts per day
  as the boiler's real count: it is a floor, roughly half the real one here.
- Calling the marginal boiler's warn a fault before checking the boiler's own limit.
- Citing these figures as evidence about real plants: the data are synthetic.

<!-- END plant-boiler-cycling -->

<!-- BEGIN slot #147 (heat pump) -->
<!-- BEGIN plant-heat-pump -->

### `plant-heat-pump`: Heat pumps: defrost, wrong mode, capacity, staging and the source loop

Figures from the synthetic `synthetic-heat-pump` entry (generated by CAMBER, so every copy gives
the same numbers), CAMBER 0.106.0-dev, with the commands on the
[exercise page](plant-heat-pump.md#setup). How the units were built: one weather series (a
fortnight from 17 February: damp days near 36 °F, a cold snap down to 6 °F, fog at 44 to
47 °F, a sunny spell up to the low 60s) drives every room. Each room is one thermal node with an
envelope loss, internal gains in occupied hours and sun on clear days; each unit runs its own
thermostat on its own sensor. The air-source units have one compressor plus strip heat (the
second heating stage, also used to temper the air during a defrost), and their heating capacity
falls with the outdoor temperature. Frost builds on the outdoor coil while a unit heats below
44 °F, faster in humid air, and a defrost reverses the valve for 5 or 10 minutes.
`ASHP__healthy` and `ASHP__no_aux_heat` defrost on demand (when frost has built up);
`ASHP__no_aux_heat` has lost its strip heat. `ASHP__defrost_timer` uses a time-temperature
control on its 60-minute interval: it defrosts after every hour of heating whenever the coil is
below freezing, frost or not. `ASHP__defrost_sensor` has a coil sensor that reads far too cold,
so it defrosts after every 30 minutes of heating at any outdoor temperature, and each defrost
ends after 5 minutes because there is no frost to melt. The water-source units have two
compressor stages. `WSHP__classroom` and `WSHP__classroom_twin` share a room and agree.
`WSHP__window_side` reads a sensor by cold glass (1 °F or more low) and `WSHP__door_side` a
sensor in the sun by the door (1.4 °F or more high), in one room. `WSHP__stuck_heating` serves an
interior room that needs cooling all day, and its reversing valve is stuck in heating, so every
cooling call heats until its high-pressure switch trips. `WSHP__stage_hunting` has a 0.1 °F
stage-2 differential and no stage-up delay. `LOOP__loop_ok` is a variable-speed loop with two-way
valves at the units, holding 6 psi; `LOOP__loop_flat` runs both pumps at constant speed with the
bypass stuck open, five times the design flow past the plant's sensors.

**Answer key**

1. *Defrost.* `ASHP__defrost_sensor` is a **fault**: 50.0 reversing-valve changes a day (700 in
   14 days) against a limit of 24 (fault at 48). It defrosts in the fog at 41 to 52 °F, 26
   changes on 26 February alone, when the healthy unit does not defrost at all: a coil above
   freezing carries no frost, so those defrosts only waste heat. `ASHP__healthy` reads 13.57 a
   day and `ASHP__no_aux_heat` 15.29, both `ok`.
2. *The near-miss.* `ASHP__defrost_timer` is `ok` at 23.43 a day, just under 24. Its timer
   defrosts after every hour of heating below freezing, frost or not, so it defrosts most in the
   dry cold snap (46 changes a day, against 16 for the healthy unit), when there is little frost.
   With a limit of 20 it **warns** and the healthy unit still passes; the label score then reads
   FPR 33% for `heatpump_defrost` (one of the 3 healthy-labelled air-source units), since the timer unit is labelled healthy. It is not broken: a 60-minute
   time-temperature control is a factory setting. The question is whether a demand defrost, or a
   90-minute interval in this climate, would save the wasted defrosts. NRCan's guide says demand
   defrost is generally the more efficient one.
3. *Mode.* `WSHP__stuck_heating` is a **fault**: 30.33 occupied hours (98.1% of its running time)
   heating a room already above its cooling setpoint. No compressor or valve is trended, so
   `mode_source` is `discharge_vs_zone`: discharge air at least 15 °F above the zone reads as
   heating. `ASHP__no_aux_heat` is `ok`, though it defrosts in the cold snap while its room is
   already cold. With the valve and the compressor trended (`mode_source` `status`), a reversal of
   at most 15 minutes between two heating samples is a defrost, not a changeover, and counts as
   heating: its 3.0 occupied hours of defrost show as `defrost_hours`, and
   `cooling_while_cold_hours` reads 0.
4. *Capacity.* `ASHP__no_aux_heat` is a **fault**: 43.33% of occupied time (47.67 h) below its
   heating band, with the unit heating 100% of that time (its defrosts included): `verdict`
   `capacity`. Without its
   strip heat, the unit cannot carry the load below its balance point. `WSHP__stuck_heating` is
   also a fault, on the cooling side (67.2% of occupied time above its band) with `verdict`
   `control`: the unit was cooling 0% of that time, heating 41% and idle 59%
   (`cooling_opposite_pct` 41.04), and the summary gives that split. A unit heating a room that
   needs cooling is a stuck valve or changeover, not a capacity problem.
5. *Rooms.* The `fleet` finding is a **fault** for `ROOM_B`: one unit heats while the other cools
   62.12% of the co-occupied time, and their sensors differ by 3.9 °F (median). `ROOM_A` is `ok`:
   0.0% fight, sensors 0.4 °F apart. Each unit in `ROOM_B` does what its own sensor asks, so
   `hp_mode_vs_need` passes both. `hp_capacity_shortfall` sees each unit's sensor out of band
   while the unit works: `WSHP__window_side` faults (27.42% of occupied time below its heating
   band) and `WSHP__door_side` warns (12.95% above its cooling band). On its own that reads as
   capacity (`verdict_alone` `capacity`), but with `hp_room_imbalance` in the same run the
   verdict is `room_fight`: the cause is the fight. The fix is the sensors' locations, not bigger
   units.
6. *Stages.* `WSHP__stage_hunting` **warns**: 43.3 stage changes a day while running (warn at 24,
   fault at 48). Starts and stops are not counted: they are `compressor_short_cycle`'s. The units
   that stage well read 1.0 to 2.43 a day. `WSHP__stuck_heating` reads 4.86 (`ok`): its
   high-pressure switch stops it again and again, but those are stops, not stage changes.
7. *Loop.* `LOOP__loop_flat` is a **fault**: a median |delta-T| of 0.4 °F, 0.9 °F at the 90th
   percentile, 91.1% of pumping time under 1 °F, at 14.0 psi. `LOOP__loop_ok` reads a median of
   6.0 °F, 7.2 °F at the 90th percentile and only 4.7% flat, at 6.0 psi.
8. *Score.* Overall, every fault is detected and no healthy unit fires (TPR 100%, FPR 0 %).
   `heatpump_defrost`, `compressor_staging` and `source_loop_deltat` score TPR 100%, FPR 0 %.
   `hp_mode_vs_need` scores TPR 100% and FPR 0 %: the defrosts no longer read as cooling.
   `hp_capacity_shortfall` scores TPR 100% and FPR 33% (three of 9: `WSHP__stuck_heating`,
   `WSHP__window_side` and `WSHP__door_side`). Those units do have a fault, just not a capacity
   fault: the stuck unit's verdict says `control`, the room pair's `room_fight`. `hp_room_imbalance` is not
   scored (a fleet finding).
9. *Every 15 minutes.* A 5-minute defrost inside a 15-minute bin averages to a valve at 0.67,
   which CAMBER counts as two reversals, the fewest the bin allows. `ASHP__defrost_sensor` still
   **faults** at 49.89 changes a day, with a caveat that the count is a floor. The other three
   air-source units and every water-source stage count decline to `info` ("cannot confirm on a
   15-minute trend"): samples part-way between two states show the interval hid changes, so an
   `ok` cannot be vouched for. `WSHP__stage_hunting` reads only 6.58 a day. Keep a heat pump's
   trend at 5 minutes or finer (`max_interval_min`).

**Discussion points**

- What does an unneeded defrost cost? The unit cools the air, the strip heat runs to temper it,
  and no heat reaches the room for those minutes.
- Where does the defrost limit come from on a real unit: its defrost board's interval, its
  demand-defrost settings, or a count in a known-good cold spell?
- A stuck reversing valve and a sensor in the wrong place both show up as a room out of band.
  Which trend tells them apart?
- Two units in one room can each look healthy. What would you check on the walk-down?
- What fixes a flat loop: valves at the units, a variable-speed drive, a DP reset, or closing the
  bypass?

**Common mistakes**

- Reading a 15-minute `info` as `ok`: it means the trend is too coarse to say.
- Ordering bigger units for `ROOM_B` because `hp_capacity_shortfall` says "capacity".
- Lowering the defrost limit until `ASHP__defrost_timer` warns, then calling it broken.
- Citing these figures as evidence about real heat pumps: the data are synthetic.

<!-- END plant-heat-pump -->
<!-- END slot #147 (heat pump) -->

<!-- BEGIN plant-chiller-staging -->

### `plant-chiller-staging`: Chiller staging and condenser-water reset

Figures from the synthetic `synthetic-chw-plant-staging` entry (generated by CAMBER, so every
copy gives the same numbers), CAMBER 0.104.0-dev, with the commands on the
[exercise page](plant-chiller-staging.md#setup). How the five plants were built: one weather
series (three weeks from early July, a heat wave in the third week with the wet-bulb up to
79.8 °F) and one building load drive five plants, each one chiller and one cooling tower. The
chiller is enabled on weekdays from 06:00 to 19:00, except on `PLANT__left_on`. The tower resets
its leaving water to the wet-bulb plus 7 °F, between a floor and 85 °F, and can never get closer
to the wet-bulb than its approach. `PLANT__well_staged` has a chiller of 1.1 times the design load
that turns down to 20 %, with a 65 °F condenser-water floor. `PLANT__short_cycling` has a chiller
of 2.5 times the design load that turns down only to 40 %, more than the building ever needs,
so it starts and stops about every half hour. `PLANT__left_on` is the well-staged chiller left
enabled around the clock. `PLANT__fixed_cw` is the well-staged chiller with its tower held at a
fixed 85 °F (a floor of 85 °F). `PLANT__high_floor` resets but never below 80 °F.

**Answer key**

1. *Staging.* Two plants **fault**, for different reasons. `PLANT__short_cycling` starts 13.2
   times a day (fault at 12); every run is at its minimum output, which is the most it ever
   delivers, so its load factor reads 100% and it is never at low load: the starts alone fail
   it. The finding says so: its `load_spread_pct` is 0.0 (every run at one output) and a caveat
   warns that the load factor says nothing about part load there. `PLANT__left_on` starts once in three weeks but runs all the time: 66.3% of its running
   samples are below 40 % of its own peak load (fault at 50 %), with a median load factor of
   10.8%, idling through nights and weekends. `PLANT__well_staged` starts 0.71 times a day (once
   each weekday) and spends 12.9% of its running time at low load: `ok`.
2. *Condenser water.* `PLANT__fixed_cw` is a **warn**: its leaving water barely follows the
   wet-bulb, a slope of 0.07 °F per °F, and sits at a median 85.0 °F. It rises only in the heat
   wave, when the tower can no longer reach 85 °F. The reset plants follow it at a slope near 1
   (1.06 on `PLANT__well_staged`, median 72.8 °F).
3. *The near miss.* `PLANT__high_floor` passes (`ok`) at a slope of 0.33, just above the 0.3
   line. Its tower resets, but its 80 °F floor holds the water at 80.0 °F in every sample except
   the heat wave's: the slope comes from the hottest days, and the median, 80.0 °F against
   72.8 °F on the well-staged plant, shows the condenser water it could have had. The rule asks
   whether there is a reset, not whether it is a good one: read the median and the floor too.
   `cws_above_wb_plus_approach_median_f` puts a number on it: the high-floor plant's water sits a
   median 7.5 °F above the wet-bulb plus the 7 °F design approach, the well-staged plant's 0.3 °F
   and the fixed tower's 12.5 °F.
4. *Score.* Both rules score TPR 100% and FPR 0 %: `chiller_staging` catches both staging faults
   and passes the other three plants; `condenser_water_reset` catches the fixed tower. The
   high-floor plant is labelled healthy, and the default check agrees.
5. *A slope of 0.5.* `PLANT__high_floor` now warns too, with `PLANT__fixed_cw`; the reset plants
   still pass. Against the labels that is a false positive, FPR 25% (one of four healthy
   plants), but on a real site the high floor is an opportunity worth a question. Choose the line
   from what the plant's sequence promises (a reset to the wet-bulb plus the tower's approach,
   down to the chiller maker's minimum condenser water) rather than to make one plant pass or
   fail.

**Discussion points**

- Short-cycling and running all night fail the same rule for opposite reasons. Which costs more,
  and what fixes each (a smaller chiller or a wider differential; a schedule)?
- Why does colder condenser water save energy? Less lift for the compressor; and why must the
  floor respect the chiller maker's minimum?
- The fixed tower's slope rises in the heat wave. What would a two-day trend from the hottest days
  have said about it?
- Who sets the condenser-water floor on a real site, and where is it written down?

**Common mistakes**

- Reading `PLANT__short_cycling`'s 100 % load factor as a well-loaded chiller (its caveat says
  why it is not).
- Reading `PLANT__high_floor`'s `ok` as "the reset is fine".
- Raising `reset_slope_flat` until a plant fails, and calling that a finding.
- Citing these figures as evidence about real plants: the data are synthetic.

<!-- END plant-chiller-staging -->

<!-- BEGIN slot #145 (two-chiller plant) -->

<!-- BEGIN plant-chiller-multi -->

### `plant-chiller-multi`: Two chillers, a secondary loop and a fouled tower

Figures from the synthetic `synthetic-chw-plant-multi` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.106.0-dev (re-recorded on 2026-10-16 after #183/#184 and the generator's version 2,
whose tower trends its condenser-water flow), with the
commands on the [exercise page](plant-chiller-multi.md#setup). How the seven plants were built: the weather of
`plant-chiller-staging` (three weeks from early July, a heat wave in the third) and one building
load (up to 380 tons) drive seven primary-secondary plants. Each has two 200-ton chillers on
480 gpm of constant primary flow each, enabled on weekdays from 06:00 to 19:00, and a tower that
resets its leaving water to the wet-bulb plus 7 °F between 65 and 85 °F. `well_run` stages the
lag chiller on above 180 tons and off below 150; its coils return water 10-13 °F warmer than
they get it. `over_staged` runs both chillers whenever the plant runs. `reverse_mixing` has coils
that return only 3-4 °F, so the secondary pumps move more water than the running chillers' primary
flow. `fouled_condenser` adds scale to chiller 1's condenser. `tower_full_fan` has a tower whose
full-fan approach is three times a clean one's, `tower_controlled` 1.4 times. `near_miss` stages
the lag chiller on at 120 tons, has coils returning 6-8 °F and a lightly scaled condenser on
chiller 1.

**Answer key**

1. *The pooled fleet.* Over the whole facility `chiller_staging_fleet` counts 14 chillers and
   **faults**, 84.6% of the multi-chiller samples over-staged. It leaves the seven secondary loops
   out (`n_loops_excluded`): their power point is the pumps', not a chiller's. But it still pools
   the 14 chillers of seven separate plants into one fleet on one clock, so a sample with nine
   chillers running reads as a plant that could drop several. Its finding sits on `<fleet>`. The
   fix is an equipment filter per plant: the three variants.
2. *One plant at a time.* `over_staged` **warns**: 44.2% of its two-chiller samples could drop a
   chiller. The rule's chiller capacity is 106.5 kW, the median of each chiller's own 95th
   percentile running power (`capacity_source` `observed_p95`, with a caveat saying so). Neither
   chiller ever runs near its 200 tons there, so that capacity reads well under the roughly
   140 kW a chiller draws at full load, and the verdict stops short of a fault. Declaring the
   full-load power (`capacity_kw`) replaces it. `well_run` is `ok` at 4.9%, `near_miss` `ok` at
   13.2% (warn at 20 %). The counts are samples: 780 two-chiller samples on `over_staged`, which
   `multi_hours` gives as 195 hours.
3. *The secondary loop.* `SECLOOP__reverse_mixing` **faults**: its supply is more than 3 °F above
   setpoint for 35.2% of its running time, a median 4.1 °F over, with a loop delta-T of 3.4 °F.
   Both of its chillers are `ok`, leaving water at 44 °F. The loop is gated on its flow
   (`run_source` `flow`), not on its pumps' power read as a chiller's. With the coils returning so little, the
   secondary pumps draw more than the running chillers' primary flow, so return water flows
   backwards through the decoupler and mixes into the supply. Every other loop is `ok`. The fix is
   at the coils (valves, flushing, setpoints), or staging on flow, not at the chillers.
4. *The condenser.* `CH1__fouled_condenser` **faults**: condenser approach 10.4 °F, 2.08 times the
   5 °F design. Every other chiller except the near miss's chiller 1 reads 2.4-2.9 °F (2.8 °F on
   `CH1__well_run`).
5. *The towers.* `TOWER__tower_full_fan` **faults** on `cooling_tower_approach`: its fan is at
   100 % in every running sample and its approach is a median 12.6 °F. `TOWER__tower_controlled`
   passes (`ok`, 7.1 °F): its controller still makes the wet-bulb plus 7 °F, by running the fan
   harder. The approach rule judged 290 of its samples at 90 % fan and above against 54 for the
   clean tower. `cooling_tower_fan_effort_drift` sees that effort: +37.9 fan points against the
   reference (fault), and +55.3 on the full-fan tower (fault). This is why `cooling_tower_approach`
   scores no fouled run on `lbnl-chiller`, whose tower is always controlled.
   `cooling_tower_approach_drift` now runs on each tower against the reference, matched on the
   heat rejected (`tower_approach_load_source` `cw_heat_rejection`). It **faults** on the full-fan
   tower, its approach +5.8 °F wider at matched load, and passes every other tower. That includes
   `TOWER__tower_controlled`, whose approach holds within a fraction of a degree: the same
   blind spot as `cooling_tower_approach`.
6. *A false positive avoided.* `TOWER__over_staged` is `ok`, +0.8 points. With both chillers on,
   the condenser flow doubles and the range halves for the same heat; matched on range alone, the
   fan would look to work harder (+6.5 points, a warn). The towers trend their condenser flow
   (`cw_flow`), so the rule matches on the heat rejected. The near-miss tower, whose early staging
   doubles the flow part of the time, is `ok` at 0.8 points.
7. *Score.* `chw_supply_tracking` and `chiller_approach_fouling` score TPR 100% and FPR 0 %.
   `cooling_tower_approach` scores TPR 50% (the full-fan tower only), FPR 0 %.
   `cooling_tower_fan_effort_drift` catches both fouled towers with no false positive (FPR 0 %). Overall the plant detects 4 of 6 faulted equipment: the two over-staged
   chillers carry a label but no equipment finding, because the fleet rule's finding sits on
   `<fleet>`, so no label score can count it. Each rule's rates count only the equipment it ran
   on: the 7 towers for the tower rules, the 14 chillers for the approach rule, and the 14
   chillers and 7 loops for supply tracking.
8. *The near miss.* `near_miss` passes everything: the lag chiller stages on early (13.2%
   over-staged), chiller 1's condenser approach is 1.38 times design (warn at 1.5), its coils
   return only 6-8 °F, which early staging hides (the extra primary flow keeps the decoupler
   flowing forward), and its tower's fan effort is 0.8 points over the reference. Each is a trend
   to watch, not a fault.

**Discussion points**

- How would you set up the fleet rule on a real site with two plants, or with a heat-recovery
  chiller beside the main ones?
- Without a declared `capacity_kw` the fleet rule reads capacity from the chillers' own power.
  Where would you find the full-load figure on a real site?
- Reverse mixing and a chiller short of capacity both warm the supply. How do the loop delta-T
  and the chillers' own leaving water tell them apart?
- A controlled tower and a tower at full fan show fouling differently. Which trends would you ask
  a site for to see both?

**Common mistakes**

- Reading the facility-wide fleet fault as a finding about any one plant.
- Blaming the chillers for the warm secondary supply in `reverse_mixing`.
- Reading `TOWER__tower_controlled`'s `ok` on `cooling_tower_approach` as a clean tower.
- Matching tower fan effort on the range alone when chillers and their condenser pumps stage.
- Citing these figures as evidence about real plants: the data are synthetic.

<!-- END plant-chiller-multi -->

<!-- END slot #145 (two-chiller plant) -->

<!-- BEGIN plant-sensor-vs-equipment -->

### `plant-sensor-vs-equipment`: Plant sensor faults vs equipment faults

Figures from the real `lbnl-chiller` full subset, CAMBER 0.97.0-dev (the pump figures
0.105.0-dev), with the commands on the [exercise page](plant-sensor-vs-equipment.md#setup).

**Answer key**

1. *The chiller's leaving-water sensor.* Reading 2 °C high, the chiller reads 3.19 kW/ton, a
   **fault** (1 °C high: 2.13, a warn). Reading 2 °C low it reads 0.67 kW/ton, far better than
   the healthy chiller's 1.44, `ok`. Neither chiller changed. The controller holds the lying
   sensor at setpoint, so the real water is colder (or warmer) than it reads; the chiller works
   harder (or less), and the tons CAMBER computes from the biased temperature are wrong as well.
   A kW/ton better than the healthy machine's is itself a clue.
2. *Where the low-reading sensor also shows.* In the secondary pumps: `chw_pump_dp_reset` is a
   **fault** on the 2 °C-low run (`SECPUMP__chiller_bias_m2`), with the pump at 90 % or more in
   92.2% of its running hours (a median of 100 %). The real chilled water is warmer, so the air
   handlers' valves open wider and the pump works harder to hold the loop DP. The high-reading
   runs go the other way: the real water is colder, and on the hours the pumps run their median
   speed is lower (74.8 % at 1 °C high, 70.3 % at 2 °C high), with `chw_pump_dp_reset` `ok`. Until
   0.105 these runs read as a pump idling at a 34.5 % VFD floor, a warn; that was the speed point
   parked while the pumps were stopped, which the rule now leaves out
   ([#180](https://github.com/yroussev/camber/issues/180)).
3. *Tower sensor vs bypass.* `condenser_bypass_leak` reports both ±2 °C tower-sensor biases as
   `info` with `attribution: sensor_offset`: the water entering the chillers reads -3.1 °F and
   +3.6 °F from the tower's leaving water, and the difference stays the same at every load.
   Warm return water leaking through a bypass would grow with the condenser range, as it does on
   `PLANT__bypass_stuck_075`, a **fault** attributed to the valve. (Reading colder than the
   tower's leaving water is impossible for mixing, which settles the -3.1 °F case.) The ±1 °C
   biases stay under the rule's 2 °F threshold (`ok`).
4. *The loop DP sensor.* Not in the DP reading, which the controller holds at setpoint. It shows
   in the pump speed: a sensor reading 20 % high lets the pump slow to a median 80.0% (`ok`), one
   reading 20 % low drives it to 100.0% (a **fault**, 76.5 % of its running hours near full),
   against 91.8 % healthy.
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
0.97.0-dev (the clipped, schedule, stray-row and per-mode figures 0.98.0-dev), with the commands
on the [exercise page](data-trend-quality.md#setup). Trust scores are the RCx reports' (hourly,
gated on the unit's fan signal; for a unit with no fan point, read per its inferred operating
mode).

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
   lower bound, not a measurement. The trust table names it: the CO2 point carries the `clipped`
   flag, and `SensorTrust.clipped` gives the limit (`2,000 ppm`, high side) and the share of
   those samples with the fan off (1.0). The flag carries no trust penalty; the ingest's quirk
   note and the dataset's data issues say the same.
5. *The held meter.* `ELE_lig_S` holds one value for 32.25 h and 36.5 h, both runs starting on a
   Saturday. A lighting load parked at its base over a weekend is plausible; a logger holding its
   last value is too. The flag says "look", and the trend viewer (does anything else move in
   those hours?) or the site settles it.
6. *Filled or not.* `RTU01_zone_022`'s CO2 has two 30-day windows of continuous values (every
   value unique) from 2020-04-26, between windows that repeat the sensor's resolution: a filled
   stretch, which the publisher's README says exists (gaps filled by interpolation and other
   methods). The nuig fan status repeats whole days too, and the screen now says why: passed the
   role (or seeing a series on two levels), it reports that 191 days follow a fixed schedule (whole
   days sharing a pattern with two or more others) instead of calling them fills. Three repeats
   that no shared pattern explains still warn (two weekend pairs in March and May 2018, and two
   weekdays in May 2018): look at them in the trend viewer, but a schedule override that ran twice
   is the likely story.
7. *The irish supply air.* Two things used to make it *untrusted* (0.19 in CAMBER 0.97), and
   both are now handled. A few stray rows logged years before the rest, then a long gap, made its
   coverage over its own span low: CAMBER now flags `stray_lead` and judges the point from the
   first sample after the gap (2017-06-23), leaving the 4 stray rows out. And a supply air held
   tightly at setpoint made every hour spent idle look like a robust outlier: the unit has no fan
   point, so the RCx table infers an off-mode (OA damper and coil valves closed) and reads the
   outliers per mode. The RCx gated column reads *trusted* at 0.89 (`stray_lead`, no outliers).
   The plain one-population read, `sensor_trust` on the series alone, still reads *untrusted* at
   0.27 with `outliers`: it does not know the operating modes. The sensor is fine; the plain
   verdict described the control, not a fault.

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
- Treating the nuig fan status's repeated days as fabricated data, or the 3 unexplained repeats
  as proof of a fill.
- Concluding the irish supply-air sensor must be replaced because its plain `sensor_trust` score
  is 0.27: read it per operating mode first.
- Running `gapfill_signature` on an hourly resample: the means erase the granularity signature.
- Counting a fan speed as missing: CAMBER uses it as the fan-on gate when no status exists.

<!-- END data-trend-quality -->

<!-- BEGIN data-sensor-conditional -->

### `data-sensor-conditional`: A sensor that reads 3 °F high: conditional findings and money at risk

Figures from the synthetic `synthetic-ahu-sensor-bias` entry (generated by CAMBER, so every copy
gives the same numbers), CAMBER 0.108.0-dev, with the commands on the
[exercise page](data-sensor-conditional.md#setup). How the two units were built: one weather
series (three weeks from 12 May, the low 60s rising to the high 60s on average, cool mornings in
the first week) drives two identical variable-air-volume air handlers on a weekday 06:00-19:00
schedule. The supply-air loop sequences the economizer (20 % minimum, 65 °F dry-bulb high limit)
and then the cooling valve to hold the trended supply-air sensor at a fixed 55 °F; the supply fan
adds 2.5 °F. `AHU__healthy`'s sensor reads the air. `AHU__sat_bias`'s reads 3 °F high, so the
loop holds the false reading at 55 °F and the air actually leaves at 52 °F. The duct loggers read
the air in each duct (to 0.1 °F) from Monday to Thursday of the second week.

**Answer key**

1. *The flags.* `leaking_valve` is a **fault** on `AHU__sat_bias` only: with both valves shut
   (37 samples) the supply air reads a median +3.4 °F beyond the 2 °F fan-heat allowance, and
   86.5% of those samples are more than 3 °F beyond it. On `AHU__healthy` the median is +0.5
   (59 samples). That flag is the reading: 2.5 °F of fan heat plus a 3 °F sensor error.
   `supply_air_reset_compliance` warns on **both** units (below the site's reset target 54.2%
   of the occupied hours on `AHU__healthy`, mean gap 3.06 °F on each): that one is the
   equipment, a sequence the BAS never implemented. `supply_air_control` passes both.
2. *The loop looks perfect.* `supply_air_control` compares the sensor with its setpoint, and the
   loop drives the sensor to the setpoint: `off_setpoint_pct` is 0% on both units. A controller
   always looks good on the sensor it controls to; it says nothing about the air.
3. *A leak that is a sensor.* With both valves shut, the supply air can only be the mixed air plus
   the fan's heat. A rise of 5.5 °F is either heat from a valve that passes water or a sensor that
   reads high, and the trend cannot tell them apart. On site: confirm the heating valve shuts
   (feel the coil, read the coil's leaving water), and hold a calibrated probe next to the
   sensor. Replacing a valve here would fix nothing.
4. *Trust cannot see it.* The sensor-trust table scores both supply-air sensors trusted (1.00,
   fan-on samples). The biased trend is smooth, in range, never stuck and copies nothing; every
   check of one trend, and every cross-sensor check CAMBER runs (copied points, the mixed air
   between outdoor and return air), passes. An offset needs an independent reference.
5. *Before the references* (`before_rcx.html`). Three issues, all firm: the two reset findings,
   priced at $10,480/yr together ($5,230 and $5,250, from the reheat-coil sizes in the config),
   and the leaking valve, uncosted and ranked first as the only fault. Nothing is at risk.
6. *The references.* The data section's reference table (`report.rcx.references`, #267): over
   the logger's 384 samples, the healthy sensor reads +0.0 °F (ok) and the biased one +3.0 °F
   (warn: the bias warns at 2 °F and faults at 5 °F). Drift is not evaluated: four days are not
   enough for a rate. The references change no rule's verdict.
7. *After the references* (`bias_rcx.html`). The comparison is a
   `sensor_drift:supply_air_temp` issue on `AHU__sat_bias`, listing the two findings that read the sensor: its `leaking_valve` and its
   `supply_air_reset_compliance`. Both are now conditional on it, at confidence L. The firm
   total falls to $5,230 (the healthy unit's reset finding) and $5,250 moves to "at risk pending
   a sensor fix". The healthy unit's identical reset finding stays firm at confidence H: its own
   sensor compared clean, and a cause taints only the unit and the point it was found on.
8. *Too low, for now.* The at-risk estimate prices the gap the sensor reports. The air is 3 °F
   colder than that, so today the real reheat penalty is larger. Once the sensor is recalibrated
   the loop holds the air at 55 °F, the unit reads like its twin, and its reset finding becomes
   firm at about the healthy unit's figure. That is what "at risk" means: the verdict stands,
   the number waits for the sensor.

**Discussion points**

- Which points on an air handler would you spot-check with a calibrated probe before trusting a
  trend, and when in the project?
- What other offsets can no single trend reveal (an OAT sensor in the sun, a mixed-air sensor in
  a stratified plenum, a static-pressure tap in the wrong place), and what reference would show
  each?
- Should an RCx report's headline total include money at risk? Who acts on it, and in what order?
- What does a 3 °F offset in the sensor the loop controls to cost, before any rule flags it?

**Common mistakes**

- Replacing the heating valve on the strength of the `leaking_valve` fault.
- Reading "trusted" as "accurate": trust says the trend behaves like a sensor, not that its
  number is right.
- Dropping a conditional issue from the plan: it stays, after the sensor fix.
- Comparing the biased sensor with the healthy unit's sensor: the loops hold both at 55 °F, so
  the two readings agree.
- Citing these figures as evidence about real air handlers: the data are synthetic.

<!-- END data-sensor-conditional -->

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
5. *`UVA_A`'s all-days model.* R² 0.12, CV(RMSE) 26.0%. Its profile shows why: weekdays and
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
0.97.0-dev, with the commands on the [exercise page](mv-baselines.md#setup); the trended
figures were refreshed in 0.105, when daily baselines began dropping partial days. The bills are
synthetic, cut from the open BDG2 meters.

**Answer key**

1. *The 2018 baselines.* `b6400_ElImp` (school) and `b6410_ElImp` (nursing home) meet CAMBER's
   G14 gate. `b6404_ElImp` does not: its CV(RMSE) of 22.5% is within the daily tier, but its R²
   of 0.72 is under the 0.75 CAMBER requires. SEP §6.4.1 rejects the same model for another
   reason: its cooling slope has the wrong sign (the logical-sign test), so its SEnPI is not
   reportable under `validity: both`.
2. *The unexplained saving.* The school `b6400_ElImp` uses 6.2% less in 2019 than its 2018
   model predicts, with a 90 % band of 24,347 on the saving: outside the band. The backcast
   agrees (5.7%). No measure is documented, so before reporting it, find the cause: an
   operating change, a sub-meter moved, an occupancy change. A saving nobody can explain is a
   non-routine question, not a result.
3. *The nursing home.* 0.8%, well inside its band: no change.
4. *The chain.* `UVA_B` (the faculty whose publisher describes an equipment replacement) shows a
   chained saving of 12.7% over 2016-2018; `UVA_A`, -0.2%, none. Neither 2016 baseline meets the
   G14 gate (R² 0.67 and 0.55, under 0.75), though both pass SEP's R² test (0.50 or more), so the
   savings are "for information only". The chain leaves the academic breaks (July and August,
   Christmas, Easter) unmodelled: the occupied-day driver knows public holidays only.
5. *The closure.* Unadjusted, `b6400_ElImp`'s 2020 chained saving is -0.4%, which looks like
   "no change". The closure indicator is material and negative: the school used much less while
   it was shut, which hid the rest of the year. Adjusted, the saving is -7.5%: outside the
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

- Reporting the 6.2% as a re-tuning saving.
- Reading the unadjusted -0.4% as "the school did not change".
- Treating R² as the only acceptance criterion, or ignoring CV(RMSE) because R² is high.
- Forgetting that the bills are synthetic, or presenting them as real utility bills.
- Comparing forecast and backcast percentages without their bands.

<!-- END mv-baselines -->

<!-- BEGIN capstone -->

### `capstone`: RCx report, walk-down, re-tuning plan and verification

Figures from the real `lbnl-sdahu` and `ornl-frp-ops` default subsets, CAMBER 0.97.0-dev (the
damper issue's cause, the M&V data need and the Verify on site section: 0.98.0-dev; the
free-cooling figures, the ranking and the walk-down rows with the economizer low limit in the
config, uncosted issues ranked by confidence, and `free_cooling_missed` judging fan-on hours
only (#120): 0.102.0-dev), with the commands on the [exercise page](capstone.md#setup). The RCx report is the one written before the drift baselines
are frozen. The config sets `free_cooling_missed`'s economizer low-limit lockout at the
documented 33.8 °F, as learners did in step 5 of `air-economizer`, so neither unit's cold-weather
hours at the damper minimum count as missed free cooling.

**Answer key**

1. *The top issue.* Rank 1 is headed "Outdoor-air damper not modulating (stuck low)", on
   `AHU__onset_damper_stuck_025`, a **fault** with confidence H: the only fault and the only
   high-confidence issue in the report. No issue here has a cost (the config gives no equipment
   sizing), so no dollars separate them. The report ranks by severity first, so the damper fault
   leads the warns. Within one severity it puts unconditional issues before conditional ones and
   costed before uncosted, by cost; uncosted issues then rank by confidence, H before M before
   L, and only then by the issue key, a fixed tie-break. That is why the onset unit's supply-air
   reset and the control's static-pressure reset, uncosted warns at confidence M, follow. Rank
   here is not a measure of size: the drift check of question 5 is what confirms the damper. The heading names the cause; the recommended
   action is "Repair the outdoor-air damper or actuator": stroke the damper from the BAS and
   watch the blades, the linkage and the actuator before changing any economizer logic. The
   finding tells the two causes apart by setting the command against the temperatures. On 90%
   of the missed free-cooling hours with a usable temperature balance (640 h), the damper was
   commanded at least 90 % open, yet the outdoor-air fraction from the mixed-air temperature
   stayed near 4 % (`missed_cause: damper_not_delivering`, `commanded_open_pct` 90.3). On the
   fault-free control, the damper was commanded low on its few missed hours
   (`economizer_not_commanded`, 0 %), and the control has no economizer issue at all. "Stuck
   low" is inferred from the mixed-air temperature, and a badly placed mixed-air sensor reads the
   same way. That is why the walk-down confirms it.
2. *Missed free cooling.* With the 33.8 °F low limit, the onset unit ran mechanical cooling in
   42% of the free-cooling hours (outdoor air between 33.8 °F and 60 °F, supply fan running), a
   **fault**, against 3.6% for the fault-free control (**ok**). The rule judges only hours with
   the fan on: with the fan off nothing moves across the coil and there is nothing to
   economize. The share is over the whole year, and the damper stuck only from 1 July, so six
   healthy months still dilute it; it is a fault all the same. A damper stuck at 25 % admits
   about as much outdoor air in cooling weather as the unit's small design minimum, so the
   outdoor-air-fraction rules see nothing to object to. Before 0.102 the rule also counted the
   fan-off nights and weekends, which diluted both shares to about half: 21 % (a warn) and
   2.0 %. Without the low limit the figures are 61 % and 34 %, a fault on both units: most of
   the control's missed hours are cold-weather hours at the damper minimum, by design.
3. *The conditional issue.* The onset unit's static-pressure reset issue is conditional on
   `duct_static_sp` (trust 0.40): in the faulted half of the splice the published setpoint is a
   placeholder that the ingest masks, so half the year has no setpoint. Walk-down item: read the
   static setpoint at the controller and confirm what the trend is mapped to.
4. *Walk-down checklist and re-tuning plan* (a model answer; accept any that covers the
   evidence). The report's **Verify on site** section is the generated version: its sensor table
   leads with the onset unit's `duct_static_sp` (trust 0.40, untrusted), then the equipment table
   lists each issue's item in rank order: first, on issue 1, the onset unit's damper (look at
   the blades, linkage and actuator while the BAS strokes it; confirms if the blades stay near
   one position at a 100 % command; refutes if they travel fully, and then the mixed-air sensor
   is the suspect), then the supply-air and static-reset logic of both units. With the low limit
   the control has no economizer item. It lists no design values: the economizer's minimum outdoor
   air (1.6 %), 60 °F high limit and 33.8 °F low limit are site parameters in this config, not
   rule defaults. The "rule defaults" confidence lines belong to `supply_air_reset`
   and `static_pressure_reset`, whose parameters are detection thresholds, not site facts. A
   student's list should match the generated one and add what a template cannot know:
   - Outdoor-air damper on the onset unit: stroke it from the BAS through its range, watch the
     blades and the linkage, and compare the mixed-air temperature with the command. Expect: the
     blades stay near a quarter open. Confirm the economizer enable logic, the 60 °F high limit
     and the 33.8 °F low-limit lockout in the program.
   - Static-pressure setpoint point: confirm the trend mapping and the value at the controller.
   - Design values worth confirming anyway, from the drawings or the controller: the minimum
     outdoor-air position, the supply-air setpoint and the static setpoint.
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

- The RCx report ranks issues and names the likely cause from the trends; it does not prove
  it. Map each item on the checklist to the walk-down chapter's advice on what to observe, and
  to the economizer guide's two questions.
- Verification is planned before the change: decide the check, the window and the pass mark in
  the re-tuning plan, then run it.
- Drift and M&V answer different questions: "did the equipment go back to how it was?" and "how
  much energy did the change save?". The capstone shows one working and one refused, honestly.

**Common mistakes**

- Freezing the drift baselines before writing the RCx report, so the report already contains the
  drift verdict.
- Reading the rank of uncosted issues of one severity as a measure of size. It orders them by
  confidence; add `report.loads` sizing to cost them and rank them by dollars.
- Dropping the low limit from `cap.json`. The damper stays a fault, and the control becomes one
  too (34 %), for hours its sequence locks the economizer out by design.
- Reprogramming the economizer on a damper-cause issue: the command already opens, so the fix is
  mechanical.
- Ordering a new actuator on the heading alone: "stuck low" comes from the mixed-air
  temperature, so stroke the damper and check the mixed-air sensor first.
- Treating the conditional static-reset issue as a confirmed fault.
- Comparing the two RTU tests' energy directly as a saving: different winters, a week each, no
  weather normalization.
- Moving the drift baseline to "clear" an alarm without a recorded reason.

<!-- END capstone -->
