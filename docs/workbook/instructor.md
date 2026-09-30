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
  on. Discuss what a real plant would show instead (high-pressure trips, alarms).

**Common mistakes**

- Reading the fouled tower's `ok` as healthy without comparing its metrics with the fault-free
  run's.
- Reading `info` (declined) on the bypass runs as `ok`.
- Treating the bypassed-fraction estimate as the leak size: in these runs it does not follow the
  severities in the file names.
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
