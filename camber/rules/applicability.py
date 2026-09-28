"""Which equipment each built-in rule applies to, by equipment family (provisional, 0.91).

Rules are gated by roles: a rule runs on any equipment that carries its required roles. That is
enough for most rules -- a boiler status point means a boiler -- but not for rules whose meaning
belongs to one kind of equipment. A VAV box's discharge-air sensor is a ``supply_air_temp`` too,
so ``supply_air_reset`` ran on terminal boxes; an air handler's heating valve is a ``heat_valve``,
so it entered the terminal reheat census; a meter's ``power`` looked like a chiller's.

:data:`RULE_EQUIP_CLASSES` names, per rule, the equipment families
(:mod:`camber.model.equipclass`) the rule is written for. :meth:`camber.rules.base.Registry.run`
declines a recognised class outside them (an ``info`` finding that says why) and
:meth:`~camber.rules.base.Registry.run_fleet` leaves such equipment out of the batch. An
unrecognised class is never declined: the rule runs on its roles, with a caveat.

A rule's own ``equip_classes`` attribute wins over this table (a custom rule declares its own).
A built-in rule that carries the attribute (``supply_air_reset_compliance`` since 0.90.1,
``g36_afdd``) takes its value *from* this table, so the table is the one place a built-in rule's
classes are declared and the attribute can't disagree with it.
Rules absent from the table -- and :data:`ROLES_SUFFICE`, listed so the classification is
complete -- keep the roles-only behaviour: their inputs already say what the equipment is, or the
check is valid on any equipment that carries them.
"""

from __future__ import annotations

__all__ = ["RULE_EQUIP_CLASSES", "ROLES_SUFFICE", "rule_equip_classes"]

_AIR = ("air_handler",)
_TERMINAL = ("terminal",)
_CHW = ("chw_plant",)
_HW = ("hw_plant",)

#: rule name -> the equipment families it applies to.
RULE_EQUIP_CLASSES: dict = {
    # --- air handlers (AHU / RTU / DOAS / MAU): supply-air, economizer, duct-static sequences
    "supply_air_reset": _AIR,
    "supply_air_reset_compliance": _AIR,
    "supply_air_control": _AIR,
    "sat_reset_effectiveness": _AIR,
    "economizer_high_limit": _AIR,
    "free_cooling_missed": _AIR,
    "outdoor_air_fraction": _AIR,
    "leaking_valve": _AIR,
    "static_pressure_reset": _AIR,
    "static_reset_effectiveness": _AIR,
    "g36_afdd": _AIR,  # G36 §5.16.14 AHU fault conditions (#60)
    # both coils on one unit: an air handler or a four-pipe fan coil (a VAV box has no cooling
    # valve; a heat pump has no valves)
    "simultaneous_heat_cool": ("air_handler", "fan_coil"),
    # --- terminal boxes (VAV / CAV / FCAV): reheat, minimum flow, per-AHU zone censuses
    "reheat_penalty": _TERMINAL,
    "reheat_minimization_g36": _TERMINAL,
    "overcooling_min_flow": _TERMINAL,
    "airflow_tracking": _TERMINAL,
    "cohort_airflow": _TERMINAL,
    "damper_census": _TERMINAL,
    "sat_rogue_zone_census": _TERMINAL,
    "static_rogue_zone_census": _TERMINAL,
    "sat_cohort_starvation": _TERMINAL,
    "static_cohort_starvation": _TERMINAL,
    "zones_heat_cool_census": ("terminal", "fan_coil"),
    # --- chilled-water plant
    "chw_plant_reset": _CHW,
    "chw_supply_tracking": _CHW,
    "chiller_efficiency": _CHW,
    "chiller_staging": _CHW,
    "chiller_staging_fleet": _CHW,
    "chiller_approach_fouling": _CHW,
    "chw_pump_dp_reset": ("chw_plant", "pump"),
    "condenser_water_reset": ("cooling_tower", "chw_plant"),
    "cooling_tower_approach": ("cooling_tower", "chw_plant"),
    # --- hot-water plant
    "boiler_summer_lockout": _HW,
    "boiler_short_cycle": _HW,
    "hw_plant_deltat": _HW,
    "hw_pump_dp_reset": ("hw_plant", "pump"),
}

#: Built-in rules that stay roles-only, so the classification above is complete.
ROLES_SUFFICE: tuple = (
    "co2_ventilation",  # a CO2 point on any unit or zone
    "cohort_space_temp",  # a zone-temperature cohort: boxes, fan coils and heat pumps alike
    "compressor_short_cycle",  # any compressor: chiller, RTU, heat pump
    "compressor_staging",
    "control_hunting",  # any modulating valve or damper
    "dcv_system_verification",  # groups zones to air handlers itself (served-by topology)
    "dcv_verification",  # declines without an OA signal; valid wherever CO2 and OA meet
    "filter_fouling",  # any filtered fan unit
    "heatpump_defrost",  # a reversing-valve command already means a heat pump
    "night_weekend_setback",  # any fan unit
    "overcooling_severity",  # any zone with a temperature and a cooling setpoint
    "unmet_setpoint_hours",
)


def rule_equip_classes(rule) -> tuple | None:
    """The classes / families ``rule`` applies to, or ``None`` for a roles-only rule.

    A rule's own ``equip_classes`` attribute is used when it has one. Otherwise a built-in rule
    (defined in the ``camber`` package) is looked up in :data:`RULE_EQUIP_CLASSES` by its name; a
    rule from elsewhere that declares nothing is roles-only.
    """
    if hasattr(rule, "equip_classes"):
        cls = rule.equip_classes
        return tuple(cls) if cls else None
    if not type(rule).__module__.startswith("camber."):
        return None
    return RULE_EQUIP_CLASSES.get(getattr(rule, "name", ""))
