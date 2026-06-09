"""Built-in rule registry: every shipped diagnostic, registered by its name.

Lets config-driven runs (and any caller) refer to rules by string name instead of
importing each class. ``builtin_registry()`` returns a fresh :class:`Registry` with
one instance of every rule registered under ``rule.name``.
"""

from __future__ import annotations

from .base import Registry
from .boiler_rule import BoilerSummerLockout
from .chiller_rule import ChillerEfficiency
from .chillerstaging_rule import ChillerStaging
from .chwplant_rule import CHWPlantReset
from .chwpump_rule import CHWPumpDPReset
from .coolingtower_rule import CoolingTowerApproach
from .hwplant_deltat_rule import HWPlantDeltaT
from .leakvalve_rule import LeakingValve
from .oafraction_rule import OutdoorAirFraction
from .overcooling_rule import OvercoolingMinFlow
from .overcooling_severity_rule import OvercoolingSeverity
from .reheat_min_rule import ReheatMinimization
from .reheat_rule import ReheatPenalty
from .satreset_rule import SupplyAirReset
from .setback_rule import NightWeekendSetback
from .simul_hc import SimultaneousHeatCool
from .static_rule import DamperCensus
from .zones_rule import ZonesHeatCoolCensus

# Every shipped rule. Per-equipment rules first, then fleet rules.
RULE_CLASSES = [
    SimultaneousHeatCool, SupplyAirReset, ReheatPenalty, OvercoolingMinFlow,
    OvercoolingSeverity, ReheatMinimization, BoilerSummerLockout, HWPlantDeltaT,
    NightWeekendSetback, OutdoorAirFraction, CHWPlantReset, CHWPumpDPReset,
    ChillerEfficiency, ChillerStaging, CoolingTowerApproach, LeakingValve,
    DamperCensus, ZonesHeatCoolCensus,
]


def is_fleet(rule) -> bool:
    """True if ``rule`` is a fleet rule (analyzed over many equipment at once)."""
    return hasattr(rule, "analyze_fleet")


def builtin_registry() -> Registry:
    """A :class:`Registry` with one instance of every built-in rule registered."""
    reg = Registry()
    for cls in RULE_CLASSES:
        reg.register(cls())
    return reg


def rule_names() -> list:
    """Sorted names of all built-in rules."""
    return sorted(cls().name for cls in RULE_CLASSES)
