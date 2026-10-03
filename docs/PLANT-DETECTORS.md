# Plant detectors: boiler fouling, tower fan effort, condenser bypass

Three detectors added in 0.92 for plant faults that the earlier rules could not see on the
labelled LBNL chiller and boiler plants. All three are **provisional** (see
[API stability](API-STABILITY.md)); their thresholds are screening-grade, set from the size of
change worth acting on rather than tuned on the validation data. Each one also names the sensor
problem that would fake its symptom, and reports it as a sensor problem (`info`) instead of an
equipment fault.

| Detector | Kind | Watches | Target fault | Fakes it |
|---|---|---|---|---|
| `boiler_efficiency_drift` (#13) | drift, `boiler` family | gas input per unit of heat delivered, at matched load | fire- or water-side fouling | a hot-water temperature or flow sensor that moved |
| `cooling_tower_fan_effort_drift` (#14) | drift, `tower` family | tower fan speed at matched load and wet-bulb | fill fouling, plugged nozzles, lost airflow on a *controlled* tower | a tower leaving-water sensor that reads warm |
| `condenser_bypass_leak` (#15) | single period, built-in | condenser water entering the chillers vs the towers' leaving water, bypass commanded shut | a leaking or stuck-open tower-bypass valve | an offset between those two sensors |

The drift detectors take an injected `BaselineStore` and freeze their reference from a baseline
window, like the [chiller](CHILLER-DRIFT.md) and [pump](PUMP-DRIFT.md) drift families; run them with
`drift.families` in a config (`{"class": "...", "family": "boiler"}` or `"tower"`) or through
`Registry.run_periods`. `camber.plantdrift` rolls each family up to a locus: the equipment, a
sensor, or steady.

<!-- 0.98 (#86 items 1 and 5, 098-plant-reference) begin -->
Since 0.98 a family can also **declare its reference** instead of reading a frozen baseline:
`{"class": "CHW_PLANT", "family": "tower", "reference": {"equip": "PLANT__fault_free"}}` scores
every other unit against a named healthy one, and `"reference": {"period": [start, end]}` against
a known-good window of the same unit. The reference is fitted in memory on every run and never
stored; each finding records it in `baseline_source` with a caveat, and the reference unit itself
declines as `is_reference`. See [the CLI guide](CLI.md#a-declared-reference) for the windows and
the declines, and [Tuning](TUNING.md#drift-references) for choosing a reference.
<!-- 0.98 (#86 items 1 and 5, 098-plant-reference) end -->

## Boiler combustion efficiency (`boiler_efficiency_drift`)

A fouled boiler burns more gas for the same heat, so its input/output ratio (1 / efficiency)
rises. The rule computes

    ratio = gas input rate / heat delivered,   heat = 500 x gpm x (supply - return) / 3412.14 kW

on the firing hours (gas above zero, and the boiler status when one is mapped), fits it against the
heat delivered (part-load efficiency varies) and, when it moves by 2 F or more, the return-water
temperature (a condensing boiler loses efficiency as its return warms), and scores the current
period's median residual. Without a flow meter, pump speed x delta-T indexes the heat instead: the
drift is judged as a **relative** change, so the ratio's units cancel and a gas rate in any
consistent unit works. Warn at +5 % and 1.5 sigma, fault at +15 % and 3 sigma.

**The corroboration.** A biased supply or return sensor moves the computed heat and the ratio
without a single extra cubic foot of gas. So a second frozen model fits the gas input against OAT
(the building's heating signature): fouling raises the gas burned at matched weather, a sensor
bias does not. A ratio rise the gas does not share (a gas rise under a third of the ratio rise) is
reported as `attribution="heat_metering"` at `info`, naming the sensors. Without OAT the rule cannot
tell the two apart and caps its severity at warn.

Inputs: `gas_input_rate` (new in 0.92 -- a rate, kW; difference a totalizer first),
`hw_supply_temp`, `hw_return_temp`, and `hw_flow` or `hw_pump_speed`; `oat` and `boiler_status`
optional. Brick `Natural_Gas_Flow_Sensor` maps to the gas role; a `Gas_Meter` used as a point type
is accepted with a caveat, and `Natural_Gas_Usage_Sensor` (a cumulative amount) is not mapped.

## Cooling-tower fan effort (`cooling_tower_fan_effort_drift`)

Most towers run their fans under a leaving-water controller. A controlled tower that fouls keeps
its approach and works its fans harder, which is why `cooling_tower_approach_drift` scores the LBNL
tower-fouling runs as healthy. This rule fits the fan speed against the condenser load -- the tower's
range (`cw_return_temp - cw_supply_temp`, heat per unit of a usually constant condenser flow), else
the chilled-water tons -- and the wet-bulb (measured, or from OAT + RH), and scores the current
period's median fan-speed residual. Warn at +5 %-points, fault at +10. The high-fan share (hours at
90 % or more) is reported alongside.

**The cross-check.** A tower controller whose leaving-water sensor reads 2 F warm cools the real
water 2 F further and spends the fan effort to do it. When the water entering the chillers
(`cond_entering_water_temp`) is trended, the rule compares the two sensors' offset -- with the bypass
shut, if its valve is mapped -- in the baseline and current periods; a shift of 1 F or more is
reported as `attribution="sensor_offset"` at `info`. Without that point the finding says it could
not rule a sensor bias out.

**Limits.** The range stands in for heat rejected only while the condenser flow is steady; a plant
that changes its condenser pumping between baseline and current (a pump swap, a VFD condenser loop)
moves the fan-effort model for reasons that are not the tower. A tower whose fans are already at
full speed shows fouling in its approach, not its effort -- run both rules (the `tower` family does).

## Condenser-water tower-bypass leak (`condenser_bypass_leak`)

With the bypass valve commanded shut, all the condenser water goes through the towers, so the water
entering the chillers equals the towers' leaving water. A leaking or stuck valve mixes warm return
water back in. The rule takes the samples with the valve at or below 2 % while a chiller runs (its
status, else its power) and scores the median of `cond_entering_water_temp - cw_supply_temp`: warn at
2 F (twice the combined accuracy of two plant sensors), fault at 5 F. With the condenser return
temperature it estimates the mixing fraction the temperatures imply,
`(entering - tower leaving) / (return - tower leaving)`.

**Leak or offset?** The temperatures alone cannot tell a leak from a miscalibrated sensor, but their
shape can: mixed-in return water raises the entering water in proportion to the condenser range, a
sensor offset adds the same amount at every load. The rule regresses the difference on the range;
a difference that does not grow with it (slope under 0.05 per F, intercept carrying at least half
of it) is reported as `attribution="sensor_offset"` at `info`, as is entering water reading colder
than the tower's (mixing can only warm it). Without the return temperature the rule says it cannot
tell them apart.

Two roles are new in 0.92: `cond_entering_water_temp` (condenser water after the bypass mixing) and
`cw_bypass_valve` (the bypass command or position, %). Map the entering temperature only where the
plant trends both points; with one condenser-supply sensor it is `cw_supply_temp`. Brick: a
`Condenser_Water_Bypass_Valve`'s valve points and a `Bypass_Command` on it map to the valve role; a
cooling tower's own `Leaving_/Entering_Water_Temperature_Sensor` map to `cw_supply_temp` /
`cw_return_temp`; and a chiller's `Entering_Condenser_Water_Temperature_Sensor` becomes
`cond_entering_water_temp` when the model also has that tower point.

## Validation

On the labelled LBNL chiller and boiler plants (`examples/lbnl_fdd/plant_detectors.py`; measured,
not gated) the three detectors catch 3/3, 2/3 and 5/5 of their target faults with no false alarm on
any other run; see [Validation](VALIDATION.md#092-plant-detectors-on-the-lbnl-plants) for the
intervals and what the sensor-bias runs did.

<!-- 0.98 (#86 items 1 and 5, 098-plant-reference) begin -->
**Through a config (0.98).** On these datasets each fault is its own year-long run, so the drift
detectors have no before-and-after on one unit. The `lbnl-chiller` and `lbnl-boiler` templates
(and the `plant-cooling-tower` and `plant-boiler` workbook exercises) therefore declare
`PLANT__fault_free` as the reference, and `camber run` gives the same verdict, run by run, as the
example script (`tests/test_drift_reference_098.py` checks it on the real data when present):
`cooling_tower_fan_effort_drift` catches tower fouling 065 and 080 (+17.8 and +10.9 fan %-points)
and misses 095 (+3.3), and `boiler_efficiency_drift` catches all three boiler foulings (+53.7 %,
+24.9 %, +5.1 %; 095 clears the 5 % warn floor by 0.1 point). Both are now the entries' declared
targets for `camber datasets score`. The scorer counts the fault-free run, which declines as the
reference, as a correct negative, so the false-positive counts read 0 of 21 and 0 of 14.
<!-- 0.98 (#86 items 1 and 5, 098-plant-reference) end -->
