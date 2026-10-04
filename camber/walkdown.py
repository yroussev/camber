"""The site walk-down checklist: what to look at on site to confirm or refute each finding.

Provisional (0.98, #88).

A trend-based finding is a hypothesis until someone looks at the equipment. PNNL's Re-tuning
training gives the walk-down its own chapter (chapter 9, "Building Walk Down"; linked, never
copied: see :data:`WALKDOWN_REFERENCES`). This module turns a report's ranked issues into that
checklist: per issue, what to look at on site, which point to compare, and what result would
confirm or refute the finding. The RCx report renders it as its "Verify on site" section.

:func:`site_checks` builds :class:`SiteCheck` items from four sources, in this order:

1. **Sensors.** Every sensor a *conditional* issue leans on (``Issue.conditional_on``), a
   ``sensor_drift:<role>`` issue, and every input a check declined on as untrusted (Appendix A):
   compare the sensor with a reference instrument and confirm the trend mapping. A sensor wrong
   on site can make any finding below it wrong, so sensors come first.
2. **Equipment.** One item per issue from :data:`SITE_CHECKS` -- a template per rule and per cause
   (the cause read from the finding's metrics, as the recommenders read it), or a generic item
   built from the rule's required inputs.
3. **Design values.** The site facts a check assumed when it ran on its defaults
   (:data:`DESIGN_PARAMS`: a minimum outdoor-air fraction, a high limit, an occupancy schedule):
   confirm each against the drawings or the controller, and set it in the config if it differs.
4. **Data.** The checks that could not run because an input is not mapped or has no data
   (``RunResult.rules_skipped``): find out whether the point exists on site.

The templates describe what a technician physically checks, in plain language, and are CAMBER's
own wording. Dependency-light: stdlib only (the rule and recommendation objects are passed in).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .references import WALKDOWN_REFERENCES as _WALKDOWN_REFERENCES

__all__ = [
    "DESIGN_PARAMS",
    "GENERIC_SITE_CHECK",
    "KINDS",
    "SITE_CHECKS",
    "CAUSE_KEYS",
    "SiteCheck",
    "SiteTemplate",
    "WALKDOWN_REFERENCES",
    "cause_key",
    "site_checks",
]

#: The item kinds, in checklist order.
KINDS = ("sensor", "equipment", "design_value", "data")

#: The reference every walk-down item links to (defined in :mod:`camber.references`).
WALKDOWN_REFERENCES = _WALKDOWN_REFERENCES

_EQUIP_CAP = 6  # equipment named on one data item before "and N more"


@dataclass
class SiteCheck:
    """One walk-down item.

    ``issue_key`` is the RCx issue the item verifies (``""`` for a decline or a skipped check, which
    belong to Appendix A); ``kind`` is one of :data:`KINDS`. ``look_at`` says what to look at on
    site, ``point`` which trended point (or setting) to compare, ``confirms`` the result that would
    confirm the finding (or the flagged problem) and ``refutes`` the result that would clear it.
    ``references`` are :mod:`camber.references` ids, the rule's own first, then the walk-down
    chapter. ``rule`` and ``rank`` (the issue's rank, 0 when none) are additive detail.
    """

    issue_key: str
    equip: str
    kind: str
    look_at: str
    point: str
    confirms: str
    refutes: str
    references: list = field(default_factory=list)
    rule: str = ""
    rank: int = 0

    def as_dict(self) -> dict:
        """Return the item as a plain dict."""
        return asdict(self)


@dataclass(frozen=True)
class SiteTemplate:
    """The text of one per-rule, per-cause walk-down item (``kind`` is ``"equipment"`` or
    ``"sensor"``)."""

    look_at: str
    point: str
    confirms: str
    refutes: str
    kind: str = "equipment"


_T = SiteTemplate

# --------------------------------------------------------------------------- the templates
# One dict per rule: cause key -> template (or a tuple of templates); "*" is the rule's item when
# no listed cause applies. The cause key comes from CAUSE_KEYS[rule](finding) and mirrors the
# metrics the recommender in camber.aso reads, so the walk-down follows the same cause as the
# issue's heading. Sibling branches add rules here; a rule with no entry gets the generic item.

_ECON = {
    "damper_not_delivering": _T(
        "Outdoor-air damper blades, linkage and actuator, while the BAS commands the damper "
        "fully open and then closed",
        "oa_damper (command) vs mixed_air_temp",
        "Commanded to 100 %, the blades stay near one fixed position, or the actuator turns and "
        "the linkage slips on the damper shaft.",
        "The blades travel fully open and closed with the command: the damper works, so check "
        "the mixed-air sensor (its location in the plenum and its calibration), which the "
        "outdoor-air fraction was computed from.",
    ),
    "economizer_not_commanded": _T(
        "The economizer enable, its high-limit setting and any lockouts in the controller, on a "
        "mild hour with cooling running",
        "oa_damper (command), oat, cool_valve",
        "The controller holds the damper at minimum in that weather: economizing is disabled, "
        "the high limit is set below the outdoor temperature, or a lockout (a failed outdoor "
        "sensor, an alarm) blocks it.",
        "The controller commands the damper open in that weather: the trended point is not the "
        "damper command (check the mapping), or the trend lags the controller.",
    ),
    "missed_free_cooling": _T(
        "The outdoor-air damper and the economizer enable on a mild hour with cooling running",
        "oa_damper, oat, cool_valve, mixed_air_temp",
        "The damper stays at minimum, or does not open when commanded, while the cooling coil "
        "runs in weather cool enough to economize.",
        "The damper opens fully and the mixed air drops toward the outdoor temperature: then "
        "check the cooling-valve point (is it this unit's valve, does 0 % mean closed?).",
    ),
    "under_ventilation": _T(
        "The minimum outdoor-air damper position, its actuator and linkage, with the unit "
        "occupied at minimum outdoor air; the outdoor airflow (flow station or a traverse)",
        "oa_damper vs mixed_air_temp, return_air_temp, oat",
        "The blades sit closed or nearly closed at the minimum position (or the linkage slips), "
        "and the measured outdoor airflow is below the design ventilation rate.",
        "The blades open to the design minimum and the measured outdoor airflow meets the "
        "design rate: check where the mixed-air sensor sits (a single-point sensor in a "
        "stratified plenum reads wrong) and the outdoor and return sensors.",
    ),
    "excess_oa": _T(
        "The outdoor-air damper on a hot hour, its minimum position setting, and the "
        "economizer high limit (dry-bulb or differential) in the controller",
        "oa_damper, oat, return_air_temp, mixed_air_temp",
        "Above the high limit the damper stays open beyond its minimum: the lockout does not "
        "act, the blades are stuck open, or the minimum position is set high.",
        "The damper closes to its design minimum above the high limit: check the design "
        "minimum (a high-outdoor-air design can sit well open) and the outdoor sensor.",
    ),
}
_ECON["*"] = _ECON["excess_oa"]

_SAT_RESET = {
    "rising_with_load": _T(
        "The cooling coil and its valve on a hot afternoon, and the chilled water reaching the "
        "coil",
        "supply_air_temp, cool_valve",
        "The cooling valve sits fully open while supply air runs warm, or the chilled water at "
        "the coil is warmer than design: the coil or the plant is short of capacity.",
        "The valve modulates below fully open while supply air is warm: the sequence raises the "
        "setpoint on purpose (read the reset logic), or the supply-air sensor reads high.",
    ),
    "reset_short": _T(
        "The supply-air reset logic in the controller: its upper and lower limits and the input "
        "that drives it (outdoor temperature or zone requests)",
        "supply_air_temp_sp, supply_air_temp",
        "The upper limit is set low, or the driving input never reaches the end that raises the "
        "setpoint (one zone always asking for cooling, say).",
        "The limits and the input are right and the setpoint does reach its upper end at low "
        "load: check that supply air follows the setpoint (sensor, cooling valve).",
    ),
    "wrong_direction": _T(
        "The direction of the supply-air reset schedule in the controller, and the outdoor "
        "sensor that feeds it",
        "supply_air_temp_sp vs oat",
        "The schedule lowers the setpoint as it gets colder outside (the reset is inverted).",
        "The schedule runs the right way: the outdoor sensor feeding it reads wrong, or the "
        "trended setpoint is another point.",
    ),
    "*": _T(
        "The supply-air setpoint logic in the controller",
        "supply_air_temp (and supply_air_temp_sp where trended)",
        "The setpoint is one fixed value: no reset is programmed, or it is disabled.",
        "A reset is programmed and active: check that the trend carries the active setpoint "
        "and that the supply-air sensor reads right against a handheld thermometer.",
    ),
}

_REHEAT = {
    "valve_divergence": _T(
        "The reheat valve and its actuator at the box while the controller calls for full heat",
        "heat_valve (demand) vs heat_valve_position",
        "The valve stays shut (stem at its closed stop, coil and discharge air cold) while the "
        "demand reads near 100 %: a stuck or failed valve or actuator, or no power to it.",
        "The valve opens with the demand and the coil warms: the position feedback is wrong "
        "(its wiring, scaling or mapping).",
    ),
    "*": _T(
        "The box's reheat valve, its minimum airflow setting and the supply air arriving at "
        "the box, on a warm occupied hour",
        "heat_valve, airflow / airflow_sp",
        "The valve opens to reheat cold supply air while the zone needs no heat, with airflow "
        "held at a high minimum.",
        "The valve is shut on site when the trend shows it open (a passing or mis-mapped point), "
        "or the zone does need heat at those hours (a perimeter or exposed space).",
    ),
}

_OVERCOOL = _T(
    "The box at minimum airflow on a cool occupied hour: its damper, its minimum airflow "
    "setting, the supply air arriving at it, and the space sensor",
    "space_temp vs cool_sp, airflow",
    "The box sits at its minimum airflow and the space still runs below its setpoint: the "
    "minimum, or the supply air, is too cold for the load.",
    "The box is not at its minimum (the damper opens past the setting), or the space sensor "
    "reads low against a handheld thermometer (check its location: an outside wall, a "
    "diffuser draft).",
)

_UNMET = {
    "too_hot": _T(
        "The zone on a warm occupied hour: the box damper, the airflow it delivers, the supply "
        "air arriving and the space sensor",
        "space_temp vs the cooling setpoint, airflow",
        "The box is wide open at full airflow and the space stays warm (capacity), or the "
        "damper does not open (stuck, or starved by low duct static).",
        "A handheld reading next to the sensor is near setpoint: the sensor reads high (sun, a "
        "nearby heat source), or the setpoint schedule is wrong.",
    ),
    "too_cold": _T(
        "The zone on a cold occupied hour: the reheat valve and coil, the discharge air, and "
        "the space sensor",
        "space_temp vs the heating setpoint, heat_valve",
        "The reheat valve is open but the coil and the discharge air stay cool (no hot water, a "
        "stuck valve), or the box delivers more cold air than its heating minimum.",
        "A handheld reading next to the sensor is near setpoint: the sensor reads low (an "
        "outside wall, a draft), or the setpoint schedule is wrong.",
    ),
}
_UNMET["*"] = _UNMET["too_hot"]

_SAT_CONTROL = {
    "warm": _T(
        "The cooling valve's stroke, the chilled water at the coil, and the supply-air sensor",
        "supply_air_temp vs supply_air_temp_sp, cool_valve",
        "The valve runs fully open while supply air stays above its setpoint: the coil, the "
        "valve or the chilled water is short.",
        "A handheld reading at the supply-air sensor is at setpoint (the sensor reads high), or "
        "the valve is not fully open (the loop is under-tuned).",
    ),
    "cold": _T(
        "The supply-air loop at the controller (its tuning and output limits) and the cooling "
        "valve's closed position",
        "supply_air_temp vs supply_air_temp_sp, cool_valve",
        "The valve swings or does not close fully (it passes chilled water), so supply air runs "
        "below its setpoint.",
        "A handheld reading at the sensor is at setpoint (the sensor reads low), or the mixed "
        "air is already colder than setpoint with no cooling running.",
    ),
}
_SAT_CONTROL["*"] = _SAT_CONTROL["warm"]

_AIRFLOW = {
    "under": _T(
        "The box damper and actuator, the airflow pickup and its tubing, and the duct static "
        "upstream of the box",
        "airflow vs airflow_sp",
        "The damper is fully open and airflow stays below setpoint (starved: low duct static or "
        "a restriction), or the damper does not stroke.",
        "A balancing hood or a duct traverse meets the setpoint: the airflow sensor reads low "
        "(calibration, kinked or disconnected tubing).",
    ),
    "over": _T(
        "The box damper (does it close down?), the airflow pickup, and the box's minimum and "
        "maximum airflow settings",
        "airflow vs airflow_sp",
        "The damper does not close to the setpoint: stuck open or a slipped linkage.",
        "A balancing hood or a duct traverse matches the setpoint: the airflow sensor reads high.",
    ),
}
_AIRFLOW["*"] = _AIRFLOW["under"]

_CHW_PLANT = {
    "reverse": _T(
        "The chillers on a hot afternoon: whether they hold the supply setpoint, their load and "
        "limits, and the condenser side (towers, condenser-water temperature)",
        "chw_supply_temp (and its setpoint), oat",
        "The chillers run at full load (or a demand limit) and the supply water drifts above "
        "setpoint, or the reset schedule is programmed to raise it as it gets hotter.",
        "The plant holds its setpoint and the schedule lowers it as load rises: the supply "
        "sensor reads high (check its well and calibration).",
    ),
    "low_dt": _T(
        "Bypass and three-way valves, the decoupler, the coil valves at idle units, and the "
        "return-water sensor",
        "chw_supply_temp, chw_return_temp",
        "Water flows through a bypass or the decoupler, coils have three-way valves, or the "
        "valves at idle units stand open.",
        "A thermometer at a test port shows a design temperature difference: the return sensor "
        "reads low.",
    ),
    "*": _T(
        "The chilled-water supply setpoint logic at the plant controller",
        "chw_supply_temp",
        "The setpoint is fixed: no reset is programmed, or it is disabled.",
        "A reset is programmed and active: the trended point is not the supply temperature, or "
        "the sensor reads wrong against a test-port thermometer.",
    ),
}

_PUMP = {
    "at_min_inferred": _T(
        "The pump's variable-speed drive: its programmed minimum speed and the speed it runs at",
        "pump speed vs the minimum speed CAMBER inferred from the trend",
        "The drive sits at its programmed minimum most of the time: the pump is oversized, or "
        "the pressure setpoint is lower than the minimum speed can hold.",
        "The drive's programmed minimum is lower than the floor CAMBER inferred, or the speed "
        "point is scaled wrong: set the pump rule's near_min_pct to the drive's value and re-run.",
    ),
    "at_min": _T(
        "The pump's variable-speed drive: its programmed minimum speed and the speed it runs at",
        "pump speed",
        "The drive sits at its programmed minimum most of the time: the pump is oversized, or "
        "the pressure setpoint is lower than the minimum speed can hold.",
        "The drive runs above its minimum on site: the speed point is scaled wrong (check 0-100 % "
        "against the drive's display).",
    ),
    "reset_full": _T(
        "The coil valves the pressure reset answers to (one stuck open, an undersized coil, a "
        "rogue zone), where the differential-pressure sensor is mounted, and any open bypass",
        "pump speed, the differential pressure and its setpoint",
        "One valve sits fully open and keeps requesting pressure, the sensor is mounted close to "
        "the pump, or a bypass stands open.",
        "No valve is pinned open and the bypasses are shut: check the speed point's scaling and "
        "the pressure sensor's calibration.",
    ),
    "*": _T(
        "The differential-pressure setpoint logic at the controller",
        "pump speed, the differential pressure and its setpoint",
        "The setpoint is fixed: no reset from valve demand is programmed, or it is disabled.",
        "A reset is programmed and active: the trended setpoint is not the active one (check the "
        "mapping).",
    ),
}

_TOWER = {
    "effort_gated": _T(
        "The tower itself: fill, water distribution (nozzles and basins), fans and drives, and "
        "whether discharge air is drawn back into the intake",
        "cw_supply_temp vs wet-bulb, tower fan speed",
        "Scaled or fouled fill, clogged nozzles or uneven basins, a failing fan or belt, or "
        "recirculating discharge air.",
        "The tower is clean and runs evenly: check the wet-bulb (or outdoor temperature and "
        "humidity) sensor and the condenser-water sensor against a handheld instrument.",
    ),
    "*": _T(
        "Tower staging (cells and fans) and the condenser-water setpoint logic",
        "cw_supply_temp vs wet-bulb",
        "The condenser-water setpoint is fixed well above what the tower can make, or cells "
        "stay off while the approach widens.",
        "Staging and the setpoint are right: check the wet-bulb and condenser-water sensors.",
    ),
}

_LEAK = {
    "heating": _T(
        "The heating coil valve with its command at 0 %: the stem position, and the coil's "
        "leaving air or piping temperature",
        "heat_valve vs supply_air_temp and mixed_air_temp",
        "With the valve commanded closed, the coil or its leaving pipe is warm and the supply "
        "air is warmer than the mixed air plus fan heat: the valve passes water (seat, stem, or "
        "an actuator that does not close it).",
        "The valve closes and the coil is cool: the fan heat is larger than assumed (supply "
        "sensor after the fan) or the mixed-air sensor reads low.",
    ),
    "cooling": _T(
        "The cooling coil valve with its command at 0 %: the stem position, and the coil's "
        "leaving air or piping temperature",
        "cool_valve vs supply_air_temp and mixed_air_temp",
        "With the valve commanded closed, the coil or its leaving pipe is cold and the supply "
        "air is colder than the mixed air: the valve passes chilled water.",
        "The valve closes and the coil is at room temperature: the mixed-air sensor reads high "
        "or the supply-air sensor reads low.",
    ),
}
_LEAK["*"] = _LEAK["cooling"]

_DCV = {
    "unventilated": _T(
        "The fan schedule against how the space is used, and the outdoor-air damper whenever "
        "the fan runs in occupied mode",
        "co2, supply fan status, oa_damper",
        "The space is used outside the fan schedule, or the damper stays shut while the fan runs.",
        "The fan and the damper run whenever the space is used: the CO2 sensor reads high "
        "(check it with a handheld meter) or the occupancy signal is wrong.",
    ),
    "below_floor": _T(
        "The minimum outdoor-air damper position and its actuator, the DCV lower limit at the "
        "controller, and the outdoor airflow",
        "outdoor airflow (or oa_damper) vs its floor",
        "The damper closes below the floor (actuator, linkage, a lower limit set too low) and "
        "the measured outdoor airflow is under the design rate.",
        "The measured outdoor airflow meets the floor: the flow station or the damper signal "
        "reads low.",
    ),
    "co2_high_at_min": _T(
        "The CO2 sensor (against a handheld meter, and where it is mounted) and whether DCV is "
        "enabled at the controller",
        "co2 vs its setpoint, oa_damper",
        "DCV is disabled, the CO2 input is not wired to the loop, or the damper does not open "
        "when CO2 rises.",
        "A handheld meter reads well below the sensor: the sensor reads high.",
    ),
    "static": _T(
        "The DCV enable and the input assigned to it at the controller",
        "oa_damper (or outdoor airflow), co2",
        "DCV is disabled or has no CO2 input: outdoor air is held fixed.",
        "DCV is enabled and the damper moves with CO2 at the controller: the trended point is "
        "not the damper (check the mapping).",
    ),
    "uncorrelated": _T(
        "Which input drives the outdoor-air damper (a schedule, the economizer, another "
        "sensor), and the CO2 sensor",
        "oa_damper (or outdoor airflow) vs co2",
        "The damper follows something other than CO2, or the CO2 sensor is in the wrong place "
        "(a corridor, near a door or diffuser).",
        "The damper follows this CO2 sensor at the controller: the trends are from different "
        "zones or times (check the mapping).",
    ),
    "excess_at_low_demand": _T(
        "The DCV lower limit and the minimum outdoor-air setpoint at the controller, against "
        "the design's area-based floor",
        "outdoor airflow (or oa_damper) at low CO2",
        "The lower limit is set above the area-based floor from the design.",
        "The limit matches the design floor: outdoor air is high for another reason (the "
        "economizer, a stuck damper).",
    ),
}
_DCV["fan_off_occupied"] = _T(  # 0.98 (#93): below the floor because the fan was off
    "The supply fan's occupied schedule, its start/stop sequence and any overrides or safeties "
    "that stop it (a freezestat, a smoke or fire shutdown, a tripped drive), on an occupied visit",
    "supply fan status vs occupancy (or the schedule)",
    "The fan is off while the space is scheduled occupied: the schedule, an override or a "
    "safety trip stops it, so the space gets no outdoor air at all.",
    "The fan runs whenever the space is occupied: the status point is mis-mapped (it trends the "
    "command or a drive's 'ready' signal), or the occupancy schedule CAMBER read is wrong.",
)
_DCV["*"] = _DCV["static"]

# 0.98 (#85): actuator_stuck -- the tier of the worst flat run picks the item
_STUCK = {
    # Tier: contradicted. The flat position contradicts what the zone was demanding.
    "contradicted": _T(
        "The terminal's damper (or valve) and actuator: watch the shaft and the position "
        "indicator while the controller commands it through its range",
        "damper / heat_valve_position / cool_valve vs the demand that should move it "
        "(airflow_sp, space_temp vs its setpoint)",
        "The actuator does not move, or it turns and the shaft or blades do not (slipped "
        "linkage, seized damper or valve), or it has no power.",
        "The device strokes fully with the command: the flat trend is the controller holding "
        "it (check the loop's output limits and mode), or a frozen point (check the "
        "controller's communication and the trend).",
    ),
    # Tier: unexplained_flat (warn). Flat while its driver moves, no direct contradiction.
    "*": _T(
        "The terminal's damper (or valve) during a period the trend shows flat, while its "
        "driver moves: the actuator, the linkage and the controller's output",
        "damper / valve position vs its driver (the finding's driver)",
        "The output changes at the controller but the device stays put: a stuck actuator "
        "or linkage.",
        "The controller's output is itself constant (a fixed minimum, an override, a manual "
        "command): release the override, and the device is fine.",
    ),
}

#: rule name -> {cause key -> template, or a tuple of templates}; "*" is the rule's default item.
SITE_CHECKS: dict = {
    "simultaneous_heat_cool": {
        "*": _T(
            "The heating and cooling coil valves and their actuators, watched through a "
            "changeover; the piping temperature downstream of each valve",
            "heat_valve, cool_valve",
            "Both coils are active at once: the heating coil's leaving pipe is warm while the "
            "cooling coil makes cold air, or one valve passes water at a 0 % command.",
            "Only one coil is active at a time: one of the trended points is something else "
            "(a preheat coil, a humidifier, the other unit's valve), so fix the mapping.",
        ),
    },
    "supply_air_reset": _SAT_RESET,
    "outdoor_air_fraction": _ECON,
    "economizer_high_limit": _ECON,
    "free_cooling_missed": _ECON,
    "reheat_penalty": _REHEAT,
    "reheat_minimization_g36": {
        "*": _T(
            "The box's heating-mode airflow setpoints and sequence in the controller, and its "
            "airflow while it reheats",
            "heat_valve, airflow vs airflow_sp",
            "In heating the box raises airflow above its heating minimum before (or while) the "
            "reheat valve opens: there is no dual-maximum heating sequence.",
            "The heating airflow setpoint sits at its minimum while the box reheats: the airflow "
            "sensor reads high (check it with a balancing hood).",
        ),
    },
    "overcooling_min_flow": {"*": _OVERCOOL},
    "overcooling_severity": {"*": _OVERCOOL},
    "night_weekend_setback": {
        "*": _T(
            "The occupancy schedule, holiday schedule and any overrides in the controller; the "
            "fan on a night or weekend visit",
            "supply fan status, occupancy",
            "The schedule keeps the fan on in unoccupied hours, an override or a holiday "
            "schedule is stuck, or one zone's request keeps the unit running.",
            "The fan is off at night on site: the status point is mis-mapped (it trends the "
            "command, or a drive's 'ready' signal).",
        ),
    },
    "unmet_setpoint_hours": _UNMET,
    "supply_air_control": _SAT_CONTROL,
    "airflow_tracking": _AIRFLOW,
    "control_hunting": {
        "*": _T(
            "The actuator named in the finding, watched for a few minutes at the device and at "
            "the controller",
            "the hunting output (the finding's worst signal)",
            "The actuator keeps travelling back and forth: the loop gains or the deadband are "
            "too tight, or the device sticks and then jumps.",
            "The device holds still while the trend swings: a noisy sensor feeds the loop, or "
            "the trend samples a signal that is not the output.",
        ),
    },
    "cohort_airflow": {
        "*": _T(
            "The outlier unit next to a typical sibling: setpoints, schedule, damper travel and "
            "the airflow it delivers",
            "airflow (the outlier vs its peers)",
            "The outlier differs on site in a way that explains its trend: another setpoint or "
            "schedule, a stuck damper, or an airflow sensor reading off a balancing hood.",
            "It matches its sibling on site: the peers may share the problem, or it serves a "
            "different kind of space. Record why it differs.",
        ),
    },
    "cohort_space_temp": {
        "*": _T(
            "The outlier zone next to a typical sibling: setpoints, schedule, the box's damper "
            "and reheat, and the space sensor against a handheld thermometer",
            "space_temp (the outlier vs its peers)",
            "The outlier differs on site in a way that explains its trend: another setpoint, a "
            "stuck damper or valve, or a sensor reading off a handheld thermometer.",
            "It matches its sibling on site: the peers may share the problem, or it serves a "
            "different kind of space. Record why it differs.",
        ),
    },
    "static_pressure_reset": {
        "*": _T(
            "The duct static setpoint logic in the controller, and where the static sensor is "
            "mounted in the duct",
            "duct_static_sp, duct_static",
            "The setpoint is fixed: no reset from the boxes' damper positions is programmed, or "
            "it is disabled.",
            "A reset is programmed and active: the trended setpoint is not the active one "
            "(check the mapping).",
        ),
    },
    "chiller_efficiency": {
        "*": _T(
            "The chiller panel (evaporator and condenser approaches, condenser-water "
            "temperature, load), the staging, and the power and flow meters",
            "power, chw_supply_temp, chw_return_temp, chw_flow",
            "The panel shows high lift or wide approaches (fouled tubes), or machines run "
            "lightly loaded.",
            "The panel's kW and the flow meter disagree with the trends: a metering artefact "
            "(current-transformer ratio, a flow meter out of calibration).",
        ),
    },
    "condenser_water_reset": {
        "*": _T(
            "The condenser-water setpoint logic and the tower controls",
            "cw_supply_temp vs wet-bulb",
            "The condenser-water setpoint is fixed: no reset is programmed, or it is disabled.",
            "A reset is programmed and active: check the wet-bulb (or outdoor temperature and "
            "humidity) sensor that drives it.",
        ),
    },
    "chw_plant_reset": _CHW_PLANT,
    "chw_supply_tracking": {
        "*": _T(
            "The chiller panel (load, limits, alarms) and the staging while supply water runs "
            "above setpoint",
            "chw_supply_temp vs chw_supply_temp_sp",
            "The running chillers are at full load or limited (a demand limit, high head "
            "pressure) and a standby machine was not started.",
            "The panel's leaving-water reading is at setpoint: the trended supply sensor reads "
            "high.",
        ),
    },
    "chw_pump_dp_reset": _PUMP,
    "hw_pump_dp_reset": _PUMP,
    "cooling_tower_approach": _TOWER,
    "boiler_short_cycle": {
        "*": _T(
            "The boiler at low load: its firing controls, its on/off differential, the "
            "hot-water setpoint and the minimum run timers",
            "boiler status (or gas input)",
            "The boiler fires and stops many times an hour at low load: a tight differential, "
            "an oversized boiler, or no hot-water reset.",
            "On site the boiler modulates without stopping: the status point toggles on "
            "something else (a flame-relay flicker, a pump interlock).",
        ),
    },
    "leaking_valve": _LEAK,
    "dcv_verification": _DCV,
    "dcv_system_verification": _DCV,
    "actuator_stuck": _STUCK,
}

#: ``RECOMMENDERS`` rules that deliberately use the generic item (none today).
GENERIC_SITE_CHECK: frozenset = frozenset()


# --------------------------------------------------------------------------- cause keys


def _m(f) -> dict:
    return getattr(f, "metrics", None) or {}


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else 0.0


def _econ_key(f) -> str:
    from .aso import _oa_failure_mode

    mode = _oa_failure_mode(f)
    if mode == "missed_free_cooling":
        cause = _m(f).get("missed_cause")
        if cause in ("damper_not_delivering", "economizer_not_commanded"):
            return str(cause)
    return mode


def _sat_reset_key(f) -> str:
    m = _m(f)
    if m.get("reset_direction") == "rising_with_load":
        return "rising_with_load"
    if m.get("sp_behaviour") == "reset" and not m.get("sp_wrong_direction"):
        return "reset_short"
    return "wrong_direction" if m.get("sp_wrong_direction") else "*"


def _reheat_key(f) -> str:
    from .aso import DEFAULT_PARAMS

    share = _m(f).get("valve_divergence_share")
    thr = _num(DEFAULT_PARAMS["reheat_valve_divergence_share"])
    return "valve_divergence" if share is not None and _num(share) >= thr else "*"


def _stuck_key(f) -> str:
    m = _m(f)
    tiers = [m.get("tier")] + [
        (r or {}).get("tier") for r in (m.get("roles") or {}).values() if isinstance(r, dict)
    ]
    return "contradicted" if "contradicted" in tiers else "*"


def _chw_plant_key(f) -> str:
    from .aso import DEFAULT_PARAMS

    m = _m(f)
    low = _num(m.get("low_deltaT_pct"))
    reset = m.get("chwst_reset_present")
    low_dt = low >= _num(DEFAULT_PARAMS["chw_low_dt_warn_pct"]) or (reset is not False and low > 0)
    if m.get("flow_mode") == "constant":
        low_dt = False
    if not low_dt and m.get("chwst_reset_direction") == "reverse":
        return "reverse"
    return "low_dt" if low_dt else "*"


def _pump_key(f) -> str:
    from .aso import DEFAULT_PARAMS

    m = _m(f)
    full, near_min = _num(m.get("pct_running_near_full")), _num(m.get("pct_running_near_min"))
    if near_min >= _num(DEFAULT_PARAMS["pump_near_min_warn_pct"]) and full < _num(
        DEFAULT_PARAMS["pump_near_full_warn_pct"]
    ):
        return "at_min_inferred" if m.get("near_min_source") in ("learned", "default") else "at_min"
    return "reset_full" if m.get("dp_sp_reset_present") is True else "*"


def _dcv_key(f) -> str:
    from .aso import DEFAULT_PARAMS, _dcv_causes, _dcv_metrics

    m = _dcv_metrics(f)
    causes = _dcv_causes(m, DEFAULT_PARAMS, str(m.get("severity") or getattr(f, "severity", "")))
    return str(causes[0] if causes else m.get("status") or "static")


def _leak_key(f) -> str:
    m = _m(f)
    hw, chw = _num(m.get("hw_leak_pct")), _num(m.get("chw_leak_pct"))
    if hw <= 0.0 and chw <= 0.0:
        return "*"
    return "heating" if hw >= chw else "cooling"


def _share_key(hi: str, lo: str, a: str, b: str):
    def key(f) -> str:
        m = _m(f)
        return hi if _num(m.get(a)) >= _num(m.get(b)) else lo

    return key


#: rule name -> ``fn(finding) -> cause key`` (a key of the rule's :data:`SITE_CHECKS` entry).
#: A rule with no entry always uses its ``"*"`` item.
CAUSE_KEYS: dict = {
    "supply_air_reset": _sat_reset_key,
    "outdoor_air_fraction": _econ_key,
    "economizer_high_limit": _econ_key,
    "free_cooling_missed": _econ_key,
    "reheat_penalty": _reheat_key,
    "unmet_setpoint_hours": _share_key("too_hot", "too_cold", "too_hot_pct", "too_cold_pct"),
    "supply_air_control": _share_key("warm", "cold", "too_warm_pct", "too_cold_pct"),
    "airflow_tracking": _share_key("under", "over", "undershoot_pct", "overshoot_pct"),
    "chw_plant_reset": _chw_plant_key,
    "chw_pump_dp_reset": _pump_key,
    "hw_pump_dp_reset": _pump_key,
    "cooling_tower_approach": lambda f: (
        "effort_gated" if _m(f).get("effort_gated") is True else "*"
    ),
    "leaking_valve": _leak_key,
    "dcv_verification": _dcv_key,
    "dcv_system_verification": _dcv_key,
    "actuator_stuck": _stuck_key,
}


def cause_key(finding) -> str:
    """The walk-down cause key of ``finding`` (``"*"`` when its rule has no cause selector)."""
    fn = CAUSE_KEYS.get(getattr(finding, "rule", ""))
    return str(fn(finding)) if fn is not None else "*"


# --------------------------------------------------------------------------- design values

#: rule name -> ``[(attribute, label, unit)]``: the site facts a rule assumes that only the
#: drawings or the controller can confirm (not its detection thresholds). ``unit`` ``None`` reads
#: the unit from :data:`camber.rules.param_docs.PARAM_DOCS`.
DESIGN_PARAMS: dict = {
    "outdoor_air_fraction": [("min_oa_pct", "Design minimum outdoor-air fraction", None)],
    "economizer_high_limit": [
        ("high_limit_f", "Economizer high limit", None),
        ("differential", "Differential (outdoor vs return) changeover", "flag"),
        ("min_oa_pct", "Design minimum outdoor-air fraction", None),
    ],
    "free_cooling_missed": [("high_limit_f", "Economizer high limit", None)],
    "night_weekend_setback": [
        ("start_hour", "Occupied start hour", "hour"),
        ("end_hour", "Occupied end hour", "hour"),
        ("occupied_days", "Occupied days (Mon=0)", "days"),
        ("unoccupied_heat_sp_f", "Unoccupied heating setpoint", None),
        ("unoccupied_cool_sp_f", "Unoccupied cooling setpoint", None),
    ],
    "unmet_setpoint_hours": [
        ("start_hour", "Occupied start hour", "hour"),
        ("end_hour", "Occupied end hour", "hour"),
    ],
    "chiller_efficiency": [("design_kw_per_ton", "Design chiller efficiency", None)],
    "chw_plant_reset": [("design_deltaT_min_f", "Design chilled-water loop deltaT", None)],
    "cooling_tower_approach": [("design_approach_f", "Design tower approach", None)],
    "boiler_short_cycle": [
        ("max_starts_per_day", "Boiler starts per day the manufacturer allows", None)
    ],
    "chw_pump_dp_reset": [("near_min_pct", "Chilled-water pump drive minimum speed", None)],
    "hw_pump_dp_reset": [("near_min_pct", "Hot-water pump drive minimum speed", None)],
    "leaking_valve": [("fan_heat_f", "Supply-fan heat (rise across the fan)", None)],
    "dcv_verification": [
        ("co2_setpoint", "DCV CO2 setpoint", None),
        ("oa_floor_cfm", "Area-based outdoor-air floor", None),
    ],
}


def _unit_of(rule: str, attr: str, unit) -> str:
    if unit is not None:
        return str(unit)
    try:
        from .rules.param_docs import PARAM_DOCS

        doc = PARAM_DOCS.get(rule, {}).get(attr)
    except Exception:  # noqa: BLE001 - a label without a unit is still a usable item
        doc = None
    return str(getattr(doc, "unit", "") or "")


def _fmt_value(v, unit: str) -> str:
    if v is None:
        return "not set (inferred from the data)"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str):  # a keyword ("auto")
        return v
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, float):
        v = f"{v:g}"
    short = unit.split(" (")[0].split(",")[0].strip() if unit else ""
    if short in ("", "-", "flag", "choice", "days", "hour", "count"):
        return str(v)
    return f"{v} {short}"


# --------------------------------------------------------------------------- the builder


def _slug(role) -> str:
    return str(getattr(role, "value", role))


def _roles_of(rule) -> list:
    out = [_slug(r) for r in getattr(rule, "roles_required", ()) or ()]
    for group in getattr(rule, "roles_any_of", None) or ():
        out.append(" or ".join(_slug(r) for r in group))
    return out


def _generic(rule_name: str, rule) -> SiteTemplate:
    roles = _roles_of(rule) if rule is not None else []
    pts = ", ".join(roles) or "the points this check reads"
    return SiteTemplate(
        f"The devices and sensors behind {pts}, with the equipment running as it did in the "
        "finding",
        pts,
        "The equipment behaves on site as the trend shows (the finding's pattern is visible at "
        "the device and at the controller).",
        "The equipment behaves correctly while the trend shows otherwise: check the sensors and "
        "the trend mapping before acting on the finding.",
    )


def _trust_note(trust, equip: str, slug: str) -> str:
    t = ((trust or {}).get(equip) or {}).get(slug)
    if t is None:
        return ""
    score = getattr(t, "trust", None)
    verdict = getattr(t, "verdict", "")
    if isinstance(score, (int, float)):
        return f" (trust {score:.2f}" + (f", {verdict})" if verdict else ")")
    return ""


def _sensor_item(slug: str, equip: str, why: str, trust) -> tuple:
    """``(look_at, point, confirms, refutes)`` for a sensor or setpoint to verify."""
    point = slug + _trust_note(trust, equip, slug)
    why = f" ({why})" if why else ""
    if slug.endswith("_sp"):
        return (
            f"The {slug} setpoint at the controller, and the point the trend is mapped to{why}",
            point,
            "The controller's value differs from the trend, or the trend is mapped to a "
            "placeholder or another point: fix the trend first.",
            "The trend carries the controller's active setpoint: the flag came from the data "
            "(gaps, a long flat stretch), and the findings that lean on it stand.",
        )
    return (
        f"The {slug} sensor: compare it with a calibrated reference instrument at the sensor, "
        f"and confirm the trend mapping{why}",
        point,
        "The sensor reads off the reference, sits in a poor location, or the trend is mapped "
        "to another point: fix it before acting on the findings that lean on it.",
        "The sensor agrees with the reference and the mapping is right: the findings that lean "
        "on it stand.",
    )


def _refs(*ids) -> list:
    out: list = []
    for group in ids:
        for rid in group or ():
            if rid not in out:
                out.append(rid)
    return out


def _templates(entry, key: str) -> list:
    t = entry.get(key) or entry.get("*")
    if t is None:
        return []
    return list(t) if isinstance(t, (list, tuple)) else [t]


def site_checks(
    issues,
    *,
    recommend: Callable | None = None,
    rule_of: Callable | None = None,
    overrides: dict | None = None,
    trust: dict | None = None,
    skipped=(),
    declined=(),
    heading_causes: dict | None = None,
) -> list:
    """The walk-down checklist for ranked ``issues`` (:class:`camber.rules.triage.Issue`).

    ``recommend(finding) -> Recommendation | None`` supplies the cause-specific references
    (:func:`camber.aso.recommend`); ``rule_of(name) -> rule | None`` the configured rule instance
    (its required inputs for the generic item, its parameters for design values); ``overrides``
    the config's ``{rule: params}`` (a parameter named there is a site value, not a default);
    ``trust`` ``{equip: {role: SensorTrust}}`` adds the trust score to a sensor item; ``skipped``
    is ``RunResult.rules_skipped`` and ``declined`` the declined findings (Appendix A).

    ``heading_causes`` ``{issue key: (rule, cause key)}`` (0.101, #108) names the cause that heads
    an issue when it is not the root's -- the RCx report's member cause (#101). That issue's
    equipment item then follows the named rule and cause (its template, its rule's references and
    the cause's recommendation references when a member finding carries it), so the item matches
    the heading. An issue not named keeps the root's item, as before.

    Returns :class:`SiteCheck` items ordered sensors, equipment, design values, data; within a
    kind, by issue rank (Appendix A items last). Duplicates (same kind, equipment and point) are
    kept once, under the first issue that needs them.
    """
    from .references import reference_ids_for

    overrides = overrides or {}
    trust = {e: {_slug(r): t for r, t in (per or {}).items()} for e, per in (trust or {}).items()}
    rule_of = rule_of or (lambda _n: None)
    out: list = []
    seen: set = set()

    def add(c: SiteCheck) -> None:
        k = (c.kind, c.equip, c.point.split(" (trust")[0], c.look_at if c.kind != "sensor" else "")
        if k not in seen:
            seen.add(k)
            out.append(c)

    def sensor(slug, equip, why, iss, rule, refs):
        look, point, conf, ref = _sensor_item(slug, equip, why, trust)
        key = getattr(iss, "key", "") if iss is not None else ""
        rank = int(getattr(iss, "rank", 0) or 0) if iss is not None else 0
        add(SiteCheck(key, equip, "sensor", look, point, conf, ref, refs, rule, rank))

    walk = list(WALKDOWN_REFERENCES)
    for iss in issues or ():
        root = iss.root
        rname = str(getattr(root, "rule", "") or "")
        rec = recommend(root) if recommend is not None else None
        rrefs = _refs(getattr(rec, "references", None), reference_ids_for(rname), walk)
        # 1. the sensors this issue leans on, or the sensor it is about
        for cause in getattr(iss, "conditional_on", None) or ():
            # a trust cause's detail is the trust score the point column already shows
            why = "" if getattr(cause, "kind", "") == "trust" else getattr(cause, "detail", "")
            for slug in getattr(cause, "roles", ()) or ():
                eq = getattr(cause, "equip", "") or iss.equip
                sensor(slug, eq, why, iss, rname, walk)
        if rname.startswith("sensor_drift:"):
            slug = rname.split(":", 1)[1]
            sensor(slug, iss.equip, f"{rname} {iss.severity}", iss, rname, walk)
            continue
        # 2. the equipment check for this rule and cause -- the heading's, when a member's cause
        # heads the issue (0.101, #108)
        erule, ekey, erefs = rname, cause_key(root), rrefs
        head = (heading_causes or {}).get(getattr(iss, "key", None))
        if head is not None and tuple(head) != (rname, ekey):
            erule, ekey = str(head[0]), str(head[1])
            member = next(
                (
                    f
                    for f in getattr(iss, "members", None) or ()
                    if getattr(f, "rule", "") == erule and cause_key(f) == ekey
                ),
                None,
            )
            mrec = recommend(member) if recommend is not None and member is not None else None
            erefs = _refs(getattr(mrec, "references", None), reference_ids_for(erule), walk)
        entry = SITE_CHECKS.get(erule)
        tmpls = _templates(entry, ekey) if entry else []
        if not tmpls:
            tmpls = [_generic(erule, rule_of(erule))]
        for t in tmpls:
            add(
                SiteCheck(
                    iss.key,
                    iss.equip,
                    t.kind,
                    t.look_at,
                    t.point,
                    t.confirms,
                    t.refutes,
                    erefs,
                    erule,
                    iss.rank,
                )
            )
        # 3. the design values this issue's rules assumed
        for rule_name in dict.fromkeys(getattr(iss, "rules", None) or [rname]):
            spec = DESIGN_PARAMS.get(rule_name)
            rule = rule_of(rule_name) if spec else None
            if rule is None:
                continue
            site = overrides.get(rule_name) or {}
            for attr, label, unit in spec or ():
                if attr in site or not hasattr(rule, attr):
                    continue
                u = _unit_of(rule_name, attr, unit)
                val = _fmt_value(getattr(rule, attr), u)
                add(
                    SiteCheck(
                        iss.key,
                        iss.equip,
                        "design_value",
                        f"{label}: the drawings, the equipment schedule or the controller",
                        f"{label} = {val} (rule default)",
                        "The site's value matches: the finding stands as judged.",
                        f"The site's value differs: set rules[{rule_name}].params.{attr} in the "
                        "config and re-run; the verdict may change.",
                        _refs(reference_ids_for(rule_name), walk),
                        rule_name,
                        iss.rank,
                    )
                )
    # 4. Appendix A: inputs a check declined as untrusted, and checks that could not run
    for f in declined or ():
        m = getattr(f, "metrics", None) or {}
        for slug in m.get("untrusted_roles") or ():
            sensor(
                str(slug),
                getattr(f, "equip", ""),
                f"{getattr(f, 'rule', '')} declined on it",
                None,
                getattr(f, "rule", ""),
                walk,
            )
    groups: dict = {}
    for sk in skipped or ():
        if getattr(sk, "reason", "") not in ("missing_inputs", "no_data"):
            continue
        key = (sk.rule, tuple(sk.missing) if sk.missing else ())
        groups.setdefault(key, [])
        if sk.equip and sk.equip not in groups[key]:
            groups[key].append(sk.equip)
    for (rule_name, missing), equips in groups.items():
        if len(equips) > _EQUIP_CAP:
            where = ", ".join(equips[:_EQUIP_CAP]) + f" and {len(equips) - _EQUIP_CAP} more"
        else:
            where = ", ".join(equips) or "the configured equipment"
        pts = ", ".join(missing) if missing else "the inputs (mapped, but no data)"
        add(
            SiteCheck(
                "",
                where,
                "data",
                f"The controller's points list and trend setup, for the check {rule_name}",
                pts,
                "The point exists on the controller but is not trended or not mapped: trend and "
                "map it, and the check can run.",
                "The equipment has no such point or device: the check does not apply here.",
                _refs(reference_ids_for(rule_name), walk),
                rule_name,
                0,
            )
        )
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda c: (order.get(c.kind, 9), c.rank or 10**9))
