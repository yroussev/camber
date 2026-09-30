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
   168. A defensible measurement needs a longer baseline covering the reporting weather (months,
   not a week), or an IPMVP Option B isolation measurement of the unit's own energy, set up
   before the change.

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
