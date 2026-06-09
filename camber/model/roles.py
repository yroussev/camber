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
    OAT = "oat"                       # outdoor-air temperature
    SUPPLY_AIR_TEMP = "supply_air_temp"
    MIXED_AIR_TEMP = "mixed_air_temp"
    RETURN_AIR_TEMP = "return_air_temp"
    SPACE_TEMP = "space_temp"

    # --- setpoints ---
    COOL_SP = "cool_sp"               # active cooling setpoint
    HEAT_SP = "heat_sp"               # active heating setpoint
    SUPPLY_AIR_TEMP_SP = "supply_air_temp_sp"
    DUCT_STATIC_SP = "duct_static_sp"
    AIRFLOW_SP = "airflow_sp"

    # --- valves / coils / dampers (command or position, %) ---
    HEAT_VALVE = "heat_valve"         # heating-coil / reheat valve position
    COOL_VALVE = "cool_valve"         # cooling-coil valve position
    OA_DAMPER = "oa_damper"
    DAMPER = "damper"                 # terminal/zone damper position

    # --- flows / pressures ---
    AIRFLOW = "airflow"
    DUCT_STATIC = "duct_static"

    # --- status / mode ---
    OCCUPANCY = "occupancy"           # occupied (1) / unoccupied (0)
    WARMUP = "warmup"                 # morning warm-up prep mode flag
    COOLDOWN = "cooldown"             # cool-down prep mode flag
    ECON_CMD = "econ_cmd"             # economizer enable/command
    BOILER_STATUS = "boiler_status"   # boiler running (1) / off (0)
    SUPPLY_FAN_STATUS = "supply_fan_status"  # supply fan running (1) / off (0)
    SUPPLY_FAN_SPEED = "supply_fan_speed"    # supply fan speed (%)

    # --- hot-water plant ---
    HW_SUPPLY_TEMP = "hw_supply_temp" # hot-water supply temp
    HW_RETURN_TEMP = "hw_return_temp" # hot-water return temp
    HW_DIFF_PRESS = "hw_diff_press"   # hot-water loop differential pressure
    HW_PUMP_SPEED = "hw_pump_speed"   # hot-water pump VFD speed (%)

    # --- chilled-water plant ---
    CHW_SUPPLY_TEMP = "chw_supply_temp"      # chilled-water supply temp
    CHW_RETURN_TEMP = "chw_return_temp"      # chilled-water return temp
    CHW_SUPPLY_TEMP_SP = "chw_supply_temp_sp"  # chilled-water supply temp setpoint
    CHW_DIFF_PRESS = "chw_diff_press"        # chilled-water loop differential pressure
    CHW_DIFF_PRESS_SP = "chw_diff_press_sp"  # chilled-water loop DP setpoint
    CHW_PUMP_SPEED = "chw_pump_speed"        # chilled-water pump VFD speed (%)
    CHW_FLOW = "chw_flow"                     # chilled-water volumetric flow (gpm)

    # --- condenser water / cooling tower ---
    CW_SUPPLY_TEMP = "cw_supply_temp"        # condenser water leaving the tower (to condenser)
    CW_RETURN_TEMP = "cw_return_temp"        # condenser water returning to the tower (from condenser)
    TOWER_FAN_SPEED = "tower_fan_speed"      # cooling-tower fan speed (%)

    # --- ambient (psychrometric) ---
    WETBULB_TEMP = "wetbulb_temp"            # outdoor wet-bulb temperature
    OUTDOOR_RH = "outdoor_rh"                # outdoor relative humidity (%)

    # --- energy / power ---
    POWER = "power"                   # electric power (kW)
    ENERGY_RATE = "energy_rate"       # thermal energy rate (BTU meter)


# Roles whose source points are text/event-based status or command signals
# (e.g. "Off"/"Running", "STOP"/"START") rather than numeric trends. The resolve
# layer loads these via load_status (text -> 0/1 step series), not the numeric
# loader which would NaN them.
STATUS_ROLES: frozenset = frozenset({
    Role.BOILER_STATUS, Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN, Role.ECON_CMD,
    Role.SUPPLY_FAN_STATUS,
})


# Non-binding hint of the Haystack tag combination each role maps onto, for the
# future ontology step. Not used at runtime; documentation only.
HAYSTACK_HINT: dict[Role, str] = {
    Role.OAT: "outside air temp sensor",
    Role.SUPPLY_AIR_TEMP: "discharge air temp sensor",
    Role.MIXED_AIR_TEMP: "mixed air temp sensor",
    Role.RETURN_AIR_TEMP: "return air temp sensor",
    Role.SPACE_TEMP: "zone air temp sensor",
    Role.COOL_SP: "zone air temp cooling sp",
    Role.HEAT_SP: "zone air temp heating sp",
    Role.SUPPLY_AIR_TEMP_SP: "discharge air temp sp",
    Role.DUCT_STATIC_SP: "duct air pressure sp",
    Role.AIRFLOW_SP: "discharge air flow sp",
    Role.HEAT_VALVE: "heating valve cmd",
    Role.COOL_VALVE: "cooling valve cmd",
    Role.OA_DAMPER: "outside air damper cmd",
    Role.DAMPER: "damper cmd",
    Role.AIRFLOW: "discharge air flow sensor",
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
    Role.CHW_SUPPLY_TEMP: "chilled water leaving temp sensor",
    Role.CHW_RETURN_TEMP: "chilled water entering temp sensor",
    Role.CHW_SUPPLY_TEMP_SP: "chilled water leaving temp sp",
    Role.CHW_DIFF_PRESS: "chilled water delta pressure sensor",
    Role.CHW_DIFF_PRESS_SP: "chilled water delta pressure sp",
    Role.CHW_PUMP_SPEED: "chilled water pump speed cmd",
    Role.CHW_FLOW: "chilled water flow sensor",
    Role.CW_SUPPLY_TEMP: "condenser water leaving temp sensor",
    Role.CW_RETURN_TEMP: "condenser water entering temp sensor",
    Role.TOWER_FAN_SPEED: "cooling tower fan speed cmd",
    Role.WETBULB_TEMP: "outside air wetBulb temp sensor",
    Role.OUTDOOR_RH: "outside air humidity sensor",
    Role.POWER: "elec power sensor",
    Role.ENERGY_RATE: "thermal energy sensor",
}
