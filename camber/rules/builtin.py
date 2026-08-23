"""Built-in rule registry: every shipped diagnostic, registered by its name.

Lets config-driven runs (and any caller) refer to rules by string name instead of
importing each class. ``builtin_registry()`` returns a fresh :class:`Registry` with
one instance of every rule registered under ``rule.name``.
"""

from __future__ import annotations

from .airflow_rule import AirflowTracking
from .base import Registry
from .boiler_rule import BoilerSummerLockout
from .boilercycle_rule import BoilerShortCycle
from .chiller_approach_rule import ChillerApproachFouling
from .chiller_rule import ChillerEfficiency
from .chillerfleet_rule import ChillerStagingFleet
from .chillerstaging_rule import ChillerStaging
from .chwplant_rule import CHWPlantReset
from .chwpump_rule import CHWPumpDPReset
from .cohort import CohortDeviation
from .compressor_cycle_rule import CompressorShortCycle
from .compressor_stage_rule import CompressorStaging
from .condenserwater_rule import CondenserWaterReset
from .coolingtower_rule import CoolingTowerApproach
from .economizer_lockout_rule import EconomizerHighLimit
from .filter_rule import FilterFouling
from .freecoolingmissed_rule import FreeCoolingMissed
from .heatpump_rule import HeatPumpDefrost
from .hunting_rule import ControlHunting
from .hwplant_deltat_rule import HWPlantDeltaT
from .hwpump_rule import HWPumpDPReset
from .iaq_rule import CO2Ventilation
from .leakvalve_rule import LeakingValve
from .oafraction_rule import OutdoorAirFraction
from .overcooling_rule import OvercoolingMinFlow
from .overcooling_severity_rule import OvercoolingSeverity
from .reheat_min_rule import ReheatMinimization
from .reheat_rule import ReheatPenalty
from .reset_effectiveness_rule import ResetEffectiveness
from .satcontrol_rule import SupplyAirControl
from .satreset_compliance_rule import SupplyAirResetCompliance
from .satreset_rule import SupplyAirReset
from .setback_rule import NightWeekendSetback
from .simul_hc import SimultaneousHeatCool
from .static_rule import DamperCensus
from .staticreset_rule import StaticPressureReset
from .unmet_rule import UnmetHours
from .ventilation_rule import DemandControlledVentilation
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
]

# Parameterized rules shipped as ready-made instances (they take init args, so they can't be
# auto-constructed from RULE_CLASSES). Cohort-deviation fleet rules for the common roles.
from ..model.roles import Role  # noqa: E402


def _extra_instances():
    return [
        CohortDeviation(Role.AIRFLOW, name="cohort_airflow"),
        CohortDeviation(Role.SPACE_TEMP, name="cohort_space_temp"),
        ResetEffectiveness(reset="sat"),
        ResetEffectiveness(reset="static"),
    ]


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


def make_rule(name: str, **params):
    """Construct a built-in rule by ``name``, overriding its constructor defaults with ``params``.

    Enables per-rule tuning from a config (e.g. a building whose design minimum outside air
    isn't the rule's default). Only the auto-registered :data:`RULE_CLASSES` are constructible
    this way. Raises ``KeyError`` for an unknown name and ``TypeError`` (naming the rule) for an
    invalid parameter.
    """
    classes = _class_by_name()
    if name not in classes:
        raise KeyError(name)
    try:
        return classes[name](**params)
    except TypeError as e:
        raise TypeError(f"invalid params for rule {name!r}: {e}") from e
