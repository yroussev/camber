"""Built-in rule registry: every shipped diagnostic, registered by its name.

Lets config-driven runs (and any caller) refer to rules by string name instead of
importing each class. ``builtin_registry()`` returns a fresh :class:`Registry` with
one instance of every rule registered under ``rule.name``.
"""

from __future__ import annotations

from .actuator_stuck_rule import ActuatorStuck  # 0.98 (#85)
from .airflow_rule import AirflowTracking
from .base import Registry
from .boiler_rule import BoilerSummerLockout
from .boilercycle_rule import BoilerShortCycle
from .chiller_approach_rule import ChillerApproachFouling
from .chiller_rule import ChillerEfficiency
from .chillerfleet_rule import ChillerStagingFleet
from .chillerstaging_rule import ChillerStaging
from .chwplant_rule import CHWPlantReset, CHWSupplyTracking
from .chwpump_rule import CHWPumpDPReset
from .cohort import CohortDeviation
from .cohort_starvation_rule import CohortStarvation
from .compressor_cycle_rule import CompressorShortCycle
from .compressor_stage_rule import CompressorStaging
from .condenser_bypass_rule import CondenserBypassLeak  # 0.92 (#15)
from .condenserwater_rule import CondenserWaterReset
from .coolingtower_rule import CoolingTowerApproach
from .dx_airflow_rule import DXIndoorAirflow  # 0.93 (#40)
from .dx_charge_rule import DXRefrigerantCharge  # 0.93 (#40)
from .economizer_lockout_rule import EconomizerHighLimit
from .filter_rule import FilterFouling
from .freecoolingmissed_rule import FreeCoolingMissed
from .g36_rule import G36AFDD
from .heatpump_ops_rule import HPCapacityShortfall, HPModeVsNeed, HPRoomImbalance  # 0.93 (#40)
from .heatpump_rule import HeatPumpDefrost
from .hunting_rule import ControlHunting
from .hwplant_deltat_rule import HWPlantDeltaT
from .hwpump_lockout_rule import HWPumpSummerLockout  # 0.103 (#132)
from .hwpump_rule import HWPumpDPReset
from .iaq_rule import CO2Ventilation, CO2VentilationSystem  # 0.93 (#38): + the fleet twin
from .leakvalve_rule import LeakingValve
from .oafraction_rule import OutdoorAirFraction
from .overcooling_rule import OvercoolingMinFlow
from .overcooling_severity_rule import OvercoolingSeverity
from .reheat_capacity_rule import ReheatCapacityShortfall  # 0.93 rules1 (#44)
from .reheat_min_rule import ReheatMinimization
from .reheat_rule import ReheatPenalty
from .reset_effectiveness_rule import ResetEffectiveness
from .rogue_zone_census_rule import RogueZoneCensus
from .satcontrol_rule import SupplyAirControl
from .satreset_compliance_rule import SupplyAirResetCompliance
from .satreset_rule import SupplyAirReset
from .setback_rule import NightWeekendSetback
from .simul_hc import SimultaneousHeatCool
from .source_loop_rule import SourceLoopDeltaT  # 0.93 (#40)
from .static_rule import DamperCensus
from .staticreset_rule import StaticPressureReset
from .unmet_rule import UnmetHours
from .ventilation_rule import DcvSystemVerification, DemandControlledVentilation
from .zones_rule import ZonesHeatCoolCensus

# Every shipped rule. Per-equipment rules first, then fleet rules.
# (VentilationRateProcedure needs per-zone design inputs, so it is instantiated explicitly
# by the caller rather than auto-registered here.)
RULE_CLASSES: list[type] = [
    SimultaneousHeatCool,
    SupplyAirReset,
    SupplyAirResetCompliance,
    ReheatPenalty,
    OvercoolingMinFlow,
    OvercoolingSeverity,
    ReheatMinimization,
    BoilerSummerLockout,
    BoilerShortCycle,
    HWPlantDeltaT,
    HWPumpDPReset,
    NightWeekendSetback,
    OutdoorAirFraction,
    CHWPlantReset,
    CHWSupplyTracking,
    CHWPumpDPReset,
    ChillerEfficiency,
    ChillerStaging,
    CoolingTowerApproach,
    CondenserWaterReset,
    CO2Ventilation,
    DemandControlledVentilation,
    LeakingValve,
    DamperCensus,
    ZonesHeatCoolCensus,
    ControlHunting,
    UnmetHours,
    SupplyAirControl,
    AirflowTracking,
    EconomizerHighLimit,
    StaticPressureReset,
    FreeCoolingMissed,
    CompressorShortCycle,
    CompressorStaging,
    HeatPumpDefrost,
    FilterFouling,
    ChillerApproachFouling,
    ChillerStagingFleet,
    DcvSystemVerification,
    G36AFDD,
    CondenserBypassLeak,  # 0.92 (#15)
    # --- 0.93 rules1
    ReheatCapacityShortfall,  # 0.93 (#44)
    # --- 0.93 rules2 (#38)
    CO2VentilationSystem,
    # --- 0.93 (#40) DX / heat-pump block (093-refrig)
    DXRefrigerantCharge,
    DXIndoorAirflow,
    HPModeVsNeed,
    HPCapacityShortfall,
    HPRoomImbalance,
    SourceLoopDeltaT,
    # --- end 0.93 (#40) block
    ActuatorStuck,  # 0.98 (#85): a terminal / fan-coil damper or valve stuck against demand
    HWPumpSummerLockout,  # 0.103 (#132): a hot-water pump running in warm weather
]

# Parameterized rules shipped as ready-made instances (they take init args, so they can't be
# auto-constructed from RULE_CLASSES). Cohort-deviation fleet rules for the common roles.
from ..model.roles import Role  # noqa: E402

# name -> (class, the constructor arguments that make the instance what it is). 0.98 (#90): a
# config can tune these too -- make_rule() rebuilds the instance with its identity arguments plus
# the overrides.
_EXTRA_SPECS: dict = {
    "cohort_airflow": (CohortDeviation, {"role": Role.AIRFLOW, "name": "cohort_airflow"}),
    "cohort_space_temp": (
        CohortDeviation,
        {"role": Role.SPACE_TEMP, "name": "cohort_space_temp"},
    ),
    "sat_reset_effectiveness": (ResetEffectiveness, {"reset": "sat"}),
    "static_reset_effectiveness": (ResetEffectiveness, {"reset": "static"}),
    "sat_rogue_zone_census": (RogueZoneCensus, {"reset": "sat"}),
    "static_rogue_zone_census": (RogueZoneCensus, {"reset": "static"}),
    "sat_cohort_starvation": (CohortStarvation, {"reset": "sat"}),
    "static_cohort_starvation": (CohortStarvation, {"reset": "static"}),
}


def _extra_instances():
    return [cls(**kw) for cls, kw in _EXTRA_SPECS.values()]


def is_fleet(rule) -> bool:
    """True if ``rule`` is a fleet rule (analyzed over many equipment at once)."""
    return hasattr(rule, "analyze_fleet")


def builtin_registry() -> Registry:
    """A :class:`Registry` with one instance of every built-in rule registered."""
    reg = Registry()
    for cls in RULE_CLASSES:
        reg.register(cls())
    for inst in _extra_instances():
        reg.register(inst)
    return reg


def rule_names() -> list:
    """Sorted names of all built-in rules."""
    return sorted([cls().name for cls in RULE_CLASSES] + [r.name for r in _extra_instances()])


def _class_by_name() -> dict:
    """Map each auto-registered rule's name -> its class (for params-overridden builds)."""
    return {cls().name: cls for cls in RULE_CLASSES}


def rule_factories() -> dict:
    """Every built-in rule name -> ``(class, fixed constructor arguments)`` (0.98, #90).

    The fixed arguments are empty for the auto-registered :data:`RULE_CLASSES`; for the extra
    instances they are the identity arguments (the cohort role, the reset kind) that
    :func:`make_rule` keeps when it applies a config's overrides.
    """
    out: dict = {name: (cls, {}) for name, cls in _class_by_name().items()}
    out.update({name: (cls, dict(kw)) for name, (cls, kw) in _EXTRA_SPECS.items()})
    return out


def make_rule(name: str, **params):
    """Construct a built-in rule by ``name``, overriding its constructor defaults with ``params``.

    Enables per-rule tuning from a config (e.g. a building whose design minimum outside air
    isn't the rule's default). Every built-in rule is constructible this way: since 0.98 (#90)
    that includes the extra instances (``cohort_airflow``, ``sat_reset_effectiveness``, ...),
    whose identity arguments (the role, the reset kind) cannot be overridden. Raises
    ``KeyError`` for an unknown name and ``TypeError`` (naming the rule) for an invalid
    parameter. ``camber rules params`` lists every rule's tunable parameters.
    """
    factories = rule_factories()
    if name not in factories:
        raise KeyError(name)
    cls, fixed = factories[name]
    clash = sorted(set(fixed) & set(params))
    if clash:
        raise TypeError(
            f"invalid params for rule {name!r}: {', '.join(clash)} is fixed for this rule"
        )
    try:
        return cls(**fixed, **params)
    except TypeError as e:
        raise TypeError(f"invalid params for rule {name!r}: {e}") from e
