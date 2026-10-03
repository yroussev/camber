"""Vendor-neutral measure *roles*.

A `Role` is what a point *means*, independent of what any particular BAS named it.
``AHU_1_HHW_Valve``, ``AHU1_HeC``, and ``ahu1.heatingValve`` are three vendors'
names for the same role: ``HEAT_VALVE``. Rules and diagnostics are written against
roles, so one rule runs on any building once its tags are mapped (see
``model.mapping``).

This is intentionally a *flat vocabulary*, not an ontology — the minimum set of
meanings the current diagnostics consume. It is designed to map cleanly onto a
Project Haystack tag set later (each role corresponds to a small marker-tag
combination, noted in ``HAYSTACK_HINT``); adopting a full ontology is a later step
and does not require changing rule code that keys off these roles.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Canonical meaning of a measured/commanded point. Value is a stable slug."""

    # --- air-side temperatures ---
    OAT = "oat"  # outdoor-air temperature
    # At an AHU: supply/discharge air. At a terminal box: the box's own *discharge* air (downstream
    # of its reheat coil); the box's *entering* primary air is mapped to MIXED_AIR_TEMP.
    SUPPLY_AIR_TEMP = "supply_air_temp"
    MIXED_AIR_TEMP = "mixed_air_temp"  # at a terminal box: the entering primary (AHU supply) air
    RETURN_AIR_TEMP = "return_air_temp"
    SPACE_TEMP = "space_temp"
    # --- 0.93 (#41, #42): coil leaving-air temperatures -- the air straight after an air handler's
    # own heating / cooling coil, upstream of a draw-through supply fan and (for the cooling coil)
    # of any post-heat (reheat) coil; G36's HCLT / CCLT. Distinct from SUPPLY_AIR_TEMP, which is
    # downstream of every coil and carries the fan's heat. leaking_valve prefers them to the supply
    # air (#42); simultaneous_heat_cool uses the cooling-coil one to tell dehumidification with
    # reheat (coil at or below the dew point, reheat warming the air back) from coil fighting (#41).
    HEAT_COIL_LEAVING_TEMP = "heat_coil_leaving_temp"  # air leaving the heating coil
    COOL_COIL_LEAVING_TEMP = "cool_coil_leaving_temp"  # air leaving the cooling coil

    # --- setpoints ---
    COOL_SP = "cool_sp"  # active cooling setpoint
    HEAT_SP = "heat_sp"  # active heating setpoint
    SUPPLY_AIR_TEMP_SP = "supply_air_temp_sp"
    DUCT_STATIC_SP = "duct_static_sp"
    AIRFLOW_SP = "airflow_sp"

    # --- reset requests (G36 trim-and-respond); the aggregated per-cycle request count ---
    SAT_RESET_REQUESTS = "sat_reset_requests"
    STATIC_PRESSURE_REQUESTS = "static_pressure_requests"

    # --- valves / coils / dampers (command or position, %) ---
    HEAT_VALVE = "heat_valve"  # heating-coil / reheat valve command or position (see below)
    # 0.98 (#85): the reheat / heating valve's MEASURED position (feedback), for a unit that trends
    # both the controller's demand and the position. Map the demand to HEAT_VALVE and the feedback
    # here: the drift and capacity rules read how hard the controller asks (HEAT_VALVE), and the
    # reheat-penalty rules judge the heat actually delivered from the position when it is mapped
    # (a valve stuck shut is 0 % open whatever the demand). With only one valve point, map it to
    # HEAT_VALVE whichever it is.
    HEAT_VALVE_POSITION = "heat_valve_position"
    COOL_VALVE = "cool_valve"  # cooling-coil valve position
    OA_DAMPER = "oa_damper"
    DAMPER = "damper"  # terminal/zone damper position

    # --- flows / pressures ---
    AIRFLOW = "airflow"
    OA_AIRFLOW = "oa_airflow"  # outdoor-air volumetric flow (cfm) — 62.1 VRP / DCV
    DUCT_STATIC = "duct_static"

    # --- status / mode ---
    OCCUPANCY = "occupancy"  # occupied (1) / unoccupied (0)
    WARMUP = "warmup"  # morning warm-up prep mode flag
    COOLDOWN = "cooldown"  # cool-down prep mode flag
    ECON_CMD = "econ_cmd"  # economizer enable/command
    BOILER_STATUS = "boiler_status"  # boiler running (1) / off (0)
    SUPPLY_FAN_STATUS = "supply_fan_status"  # supply fan running (1) / off (0)
    SUPPLY_FAN_SPEED = "supply_fan_speed"  # supply fan speed (%)

    # --- hot-water plant ---
    HW_SUPPLY_TEMP = "hw_supply_temp"  # hot-water supply temp
    HW_RETURN_TEMP = "hw_return_temp"  # hot-water return temp
    HW_DIFF_PRESS = "hw_diff_press"  # hot-water loop differential pressure
    HW_DIFF_PRESS_SP = "hw_diff_press_sp"  # hot-water loop DP setpoint
    HW_PUMP_SPEED = "hw_pump_speed"  # hot-water pump VFD speed (%)
    HW_FLOW = "hw_flow"  # hot-water volumetric flow (gpm)
    # 0.92 (#13): a boiler's fuel (gas) input *rate*, kW of fuel energy -- an instantaneous
    # rate, never a cumulative meter reading (difference a totalizer before mapping it here). The
    # boiler efficiency drift is judged on a relative change, so a consistent rate unit is what
    # matters; kW is the documented canonical unit. Also the hot-water run gate's firing signal
    # when no boiler status is mapped (camber.schedules.plant_run_mask).
    GAS_INPUT_RATE = "gas_input_rate"

    # --- chilled-water plant ---
    CHW_SUPPLY_TEMP = "chw_supply_temp"  # chilled-water supply temp
    CHW_RETURN_TEMP = "chw_return_temp"  # chilled-water return temp
    CHW_SUPPLY_TEMP_SP = "chw_supply_temp_sp"  # chilled-water supply temp setpoint
    CHW_DIFF_PRESS = "chw_diff_press"  # chilled-water loop differential pressure
    CHW_DIFF_PRESS_SP = "chw_diff_press_sp"  # chilled-water loop DP setpoint
    CHW_PUMP_SPEED = "chw_pump_speed"  # chilled-water pump VFD speed (%)
    CHW_FLOW = "chw_flow"  # chilled-water volumetric flow (gpm)
    PUMP_STATUS = "pump_status"  # pump running (1) / off (0); gates pump drift to running samples
    PUMP_HEAD = "pump_head"  # pump differential head (discharge - suction across the pump), psi

    # --- condenser water / cooling tower ---
    CW_SUPPLY_TEMP = "cw_supply_temp"  # condenser water leaving the tower (to condenser)
    CW_RETURN_TEMP = "cw_return_temp"  # condenser water returning to the tower (from condenser)
    TOWER_FAN_SPEED = "tower_fan_speed"  # cooling-tower fan speed (%)
    # 0.92 (#15): the condenser water actually *entering the chiller condensers*, downstream of
    # the tower-bypass mixing -- equal to the tower's leaving water (CW_SUPPLY_TEMP) whenever the
    # bypass is shut. Map it only where the plant trends both points (with no bypass, or no
    # separate tower-leaving sensor, the one condenser-supply point is CW_SUPPLY_TEMP).
    COND_ENTERING_WATER_TEMP = "cond_entering_water_temp"
    # 0.92 (#15): the condenser-water tower-bypass valve's command or position, % open to the
    # bypass (0 = all condenser water through the towers).
    CW_BYPASS_VALVE = "cw_bypass_valve"

    # --- ambient (psychrometric) ---
    WETBULB_TEMP = "wetbulb_temp"  # outdoor wet-bulb temperature
    OUTDOOR_RH = "outdoor_rh"  # outdoor relative humidity (%)

    # --- air quality ---
    CO2 = "co2"  # zone/space CO2 concentration (ppm)
    OUTDOOR_CO2 = "outdoor_co2"  # outdoor CO2 concentration (ppm)

    # --- packaged / DX equipment (RTU, heat pump) ---
    COMPRESSOR_STATUS = "compressor_status"  # DX compressor running (1) / off (0)
    COMPRESSOR_STAGE = "compressor_stage"  # active DX cooling stage (0,1,2,...)
    CONDENSER_FAN_STATUS = "condenser_fan_status"  # condenser/outdoor fan running (1) / off (0)
    HEAT_STAGE = "heat_stage"  # active (gas/electric) heating stage (0,1,2,...)
    REVERSING_VALVE_CMD = "reversing_valve_cmd"  # heat-pump mode: heating (1) / cooling (0)

    # --- air-side humidity / filtration ---
    FILTER_DIFF_PRESS = "filter_diff_press"  # differential pressure across the air filter (inH2O)
    SUPPLY_AIR_HUMIDITY = "supply_air_humidity"  # supply/discharge air relative humidity (%)
    RETURN_AIR_HUMIDITY = "return_air_humidity"  # return air relative humidity (%)

    # --- refrigerant-side chiller (fouling / charge proxies) ---
    COND_APPROACH_TEMP = "cond_approach_temp"  # condenser approach (refrigerant - CW leaving), degF
    EVAP_APPROACH_TEMP = (
        "evap_approach_temp"  # evaporator approach (CHW leaving - refrigerant), degF
    )
    # Liquid-line subcooling (condensing temp - liquid line temp), degF. Like the approach roles
    # this is a *difference*, not a raw temperature: mapped directly where the chiller publishes
    # it, or (0.93, #39) derived by camber.refrigerant from the liquid-line (else discharge)
    # pressure and the liquid-line temperature when the equipment's refrigerant is named.
    SUBCOOLING_TEMP = "subcooling_temp"
    # Suction superheat (suction temp - evaporator saturation temp), degF. The evaporator-side
    # counterpart to SUBCOOLING_TEMP: a *difference*, mapped directly or derived the same way from
    # the suction pressure and suction-line temperature. Low superheat = the evaporator is overfed
    # (liquid-floodback risk); high superheat = it is starved (underfeed / undercharge /
    # restriction).
    SUPERHEAT_TEMP = "superheat_temp"
    # Discharge (head / condensing) refrigerant pressure, psig — the high-side pressure. Climbs as
    # the condenser loses its ability to reject heat: tube fouling / scale, non-condensables in the
    # circuit, high entering condenser-water temperature, or reduced CW flow. A *raw* pressure,
    # trended in its own right (see camber.rules.chiller_head_pressure_rule) and, with a named
    # refrigerant, the saturation reference for subcooling, discharge superheat and the condenser
    # approach (camber.refrigerant).
    DISCHARGE_PRESSURE = "discharge_pressure"
    # Suction (evaporating) refrigerant pressure, psig — the low-side pressure, and the evaporator
    # counterpart to DISCHARGE_PRESSURE. Optional enriching context for the head-pressure detector
    # (the condensing-over-suction *lift*, which helps separate a genuine high-side fault from an
    # ambient-/load-driven rise). Also a raw pressure, mapped directly where the chiller reports it.
    SUCTION_PRESSURE = "suction_pressure"
    # --- 0.93 (#39): refrigerant line temperatures and pressures (raw readings) ---
    # With the equipment's refrigerant named (a run config's equipment ``"refrigerant"``),
    # camber.refrigerant turns these into the saturation-referenced differences above:
    # subcooling from the liquid-line pressure (or, failing that, the discharge pressure) and the
    # liquid-line temperature, superheat from the suction pressure and suction-line temperature,
    # discharge superheat from the discharge pressure and discharge-line temperature.
    LIQUID_LINE_TEMP = "liquid_line_temp"  # refrigerant liquid-line temperature, degF
    SUCTION_LINE_TEMP = "suction_line_temp"  # refrigerant suction-line temperature, degF
    DISCHARGE_LINE_TEMP = "discharge_line_temp"  # compressor discharge-line temperature, degF
    LIQUID_LINE_PRESSURE = "liquid_line_pressure"  # liquid-line refrigerant pressure, psig
    # Compressor discharge superheat (discharge-line temp - dew temp at discharge pressure), degF:
    # a *difference*, mapped where a controller publishes it or derived as above (issue #6).
    DISCHARGE_SUPERHEAT_TEMP = "discharge_superheat_temp"
    # 0.93 (#40): the return (coil-entering) air dew point, degF -- the latent load a DX coil's
    # temperature split depends on (dx_indoor_airflow). Where only return RH is trended, the rule
    # computes it from RH and the return-air temperature.
    RETURN_AIR_DEWPOINT_TEMP = "return_air_dewpoint_temp"

    # --- 0.93 (#40): a water-source / ground-source heat-pump (condenser) loop ---
    # Generic source-loop points, kept apart from the chilled-water and cooling-tower roles whose
    # rules would misread a heat-pump loop. Supply = the water the loop sends to the heat pumps;
    # return = what comes back from them.
    SOURCE_LOOP_SUPPLY_TEMP = "source_loop_supply_temp"  # degF
    SOURCE_LOOP_RETURN_TEMP = "source_loop_return_temp"  # degF
    SOURCE_LOOP_DIFF_PRESS = "source_loop_diff_press"  # loop differential pressure, psi
    SOURCE_LOOP_PUMP_SPEED = "source_loop_pump_speed"  # loop pump speed (%)

    # --- energy / power ---
    POWER = "power"  # electric power (kW)
    ENERGY_RATE = "energy_rate"  # thermal energy rate (BTU meter)


# Roles whose source points are text/event-based status or command signals
# (e.g. "Off"/"Running", "STOP"/"START") rather than numeric trends. The resolve
# layer loads these via load_status (text -> 0/1 step series), not the numeric
# loader which would NaN them.
STATUS_ROLES: frozenset = frozenset(
    {
        Role.BOILER_STATUS,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
        Role.ECON_CMD,
        Role.SUPPLY_FAN_STATUS,
        Role.COMPRESSOR_STATUS,
        Role.CONDENSER_FAN_STATUS,
        Role.REVERSING_VALVE_CMD,
        Role.PUMP_STATUS,
    }
)


# Non-binding hint of the Haystack tag combination each role maps onto, for the
# future ontology step. Not used at runtime; documentation only.
HAYSTACK_HINT: dict[Role, str] = {
    Role.OAT: "outside air temp sensor",
    Role.SUPPLY_AIR_TEMP: "discharge air temp sensor",
    Role.MIXED_AIR_TEMP: "mixed air temp sensor",
    Role.RETURN_AIR_TEMP: "return air temp sensor",
    Role.SPACE_TEMP: "zone air temp sensor",
    Role.HEAT_COIL_LEAVING_TEMP: "heating coil leaving air temp sensor",  # 0.93 (#42)
    Role.COOL_COIL_LEAVING_TEMP: "cooling coil leaving air temp sensor",  # 0.93 (#41, #42)
    Role.COOL_SP: "zone air temp cooling sp",
    Role.HEAT_SP: "zone air temp heating sp",
    Role.SUPPLY_AIR_TEMP_SP: "discharge air temp sp",
    Role.DUCT_STATIC_SP: "duct air pressure sp",
    Role.SAT_RESET_REQUESTS: "discharge air temp reset request point",
    Role.STATIC_PRESSURE_REQUESTS: "duct air pressure reset request point",
    Role.AIRFLOW_SP: "discharge air flow sp",
    Role.HEAT_VALVE: "heating valve cmd",
    Role.HEAT_VALVE_POSITION: "heating valve sensor",  # 0.98 (#85): the measured position
    Role.COOL_VALVE: "cooling valve cmd",
    Role.OA_DAMPER: "outside air damper cmd",
    Role.DAMPER: "damper cmd",
    Role.AIRFLOW: "discharge air flow sensor",
    Role.OA_AIRFLOW: "outside air flow sensor",
    Role.DUCT_STATIC: "duct air pressure sensor",
    Role.OCCUPANCY: "occupied",
    Role.WARMUP: "warmup",
    Role.COOLDOWN: "cooldown",
    Role.ECON_CMD: "economizer cmd",
    Role.BOILER_STATUS: "boiler run sensor",
    Role.SUPPLY_FAN_STATUS: "discharge fan run sensor",
    Role.SUPPLY_FAN_SPEED: "discharge fan speed cmd",
    Role.HW_SUPPLY_TEMP: "hot water leaving temp sensor",
    Role.HW_RETURN_TEMP: "hot water entering temp sensor",
    Role.HW_DIFF_PRESS: "hot water delta pressure sensor",
    Role.HW_PUMP_SPEED: "hot water pump speed cmd",
    Role.HW_DIFF_PRESS_SP: "hot water delta pressure sp",
    Role.HW_FLOW: "hot water flow sensor",
    Role.GAS_INPUT_RATE: "naturalGas flow sensor",
    Role.CHW_SUPPLY_TEMP: "chilled water leaving temp sensor",
    Role.CHW_RETURN_TEMP: "chilled water entering temp sensor",
    Role.CHW_SUPPLY_TEMP_SP: "chilled water leaving temp sp",
    Role.CHW_DIFF_PRESS: "chilled water delta pressure sensor",
    Role.CHW_DIFF_PRESS_SP: "chilled water delta pressure sp",
    Role.CHW_PUMP_SPEED: "chilled water pump speed cmd",
    Role.CHW_FLOW: "chilled water flow sensor",
    Role.PUMP_STATUS: "pump run sensor",
    Role.PUMP_HEAD: "pump delta pressure sensor",
    Role.CW_SUPPLY_TEMP: "condenser water leaving temp sensor",
    Role.CW_RETURN_TEMP: "condenser water entering temp sensor",
    Role.TOWER_FAN_SPEED: "cooling tower fan speed cmd",
    Role.COND_ENTERING_WATER_TEMP: "condenser water entering temp sensor chiller",
    Role.CW_BYPASS_VALVE: "condenser water bypass valve cmd",
    Role.WETBULB_TEMP: "outside air wetBulb temp sensor",
    Role.OUTDOOR_RH: "outside air humidity sensor",
    Role.CO2: "zone air co2 sensor",
    Role.OUTDOOR_CO2: "outside air co2 sensor",
    Role.COMPRESSOR_STATUS: "compressor run sensor",
    Role.COMPRESSOR_STAGE: "compressor stage sensor",
    Role.CONDENSER_FAN_STATUS: "condenser fan run sensor",
    Role.HEAT_STAGE: "heating stage sensor",
    Role.REVERSING_VALVE_CMD: "heatPump reversing valve cmd",
    Role.FILTER_DIFF_PRESS: "filter air delta pressure sensor",
    Role.SUPPLY_AIR_HUMIDITY: "discharge air humidity sensor",
    Role.RETURN_AIR_HUMIDITY: "return air humidity sensor",
    Role.COND_APPROACH_TEMP: "condenser refrig temp approach sensor",
    Role.EVAP_APPROACH_TEMP: "evaporator refrig temp approach sensor",
    Role.SUBCOOLING_TEMP: "refrig subcooling temp sensor",
    Role.SUPERHEAT_TEMP: "refrig superheat temp sensor",
    Role.DISCHARGE_PRESSURE: "discharge refrig pressure sensor",
    Role.SUCTION_PRESSURE: "suction refrig pressure sensor",
    Role.LIQUID_LINE_TEMP: "refrig liquid temp sensor",
    Role.SUCTION_LINE_TEMP: "refrig suction temp sensor",
    Role.DISCHARGE_LINE_TEMP: "refrig discharge temp sensor",
    Role.LIQUID_LINE_PRESSURE: "refrig liquid pressure sensor",
    Role.DISCHARGE_SUPERHEAT_TEMP: "refrig discharge superheat temp sensor",
    Role.RETURN_AIR_DEWPOINT_TEMP: "return air dewPoint sensor",
    Role.SOURCE_LOOP_SUPPLY_TEMP: "loop water supply temp sensor",
    Role.SOURCE_LOOP_RETURN_TEMP: "loop water return temp sensor",
    Role.SOURCE_LOOP_DIFF_PRESS: "loop water delta pressure sensor",
    Role.SOURCE_LOOP_PUMP_SPEED: "loop pump speed cmd",
    Role.POWER: "elec power sensor",
    Role.ENERGY_RATE: "thermal energy sensor",
}
