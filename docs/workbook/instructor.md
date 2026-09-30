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
