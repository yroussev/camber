"""Packaged ASHRAE Guideline 36 sequence clauses for SOO conformance.

Ready-made :mod:`camber.soo` clause lists that encode common ASHRAE Guideline 36
high-performance-sequence expectations, so a building can be checked for
operated-vs-designed conformance without authoring a sequence from scratch. The
clauses are our own expression of the public standard's intent (ASHRAE Guideline
36-2021), keyed off roles, and parameterized where the value is genuinely
site-specific (tolerances, the economizer high limit, the summer-lockout OAT) -- those
are injected, not baked. Use a sequence as-is, or as a starting template to edit.

Note these are *single-equipment* clauses expressible as gated role predicates; richer
G36 logic (state machines, multi-input interlocks) belongs in the rule library, while
the simultaneous-heating/cooling prohibition is captured here as "when the cooling coil
is open, the heating coil must be closed" (and vice versa).
"""

from __future__ import annotations

from .model.roles import Role
from .soo import Clause, Predicate


def g36_ahu_sequence(
    *,
    sat_tol: float = 2.0,             # SAT must track its setpoint within this (degF)
    static_tol: float = 0.3,         # duct static must track its setpoint within this (inWC)
    econ_high_limit_f: float = 75.0,  # economizer high-limit OAT lockout
    valve_closed_pct: float = 5.0,    # a valve at/below this is "closed"
    damper_closed_pct: float = 5.0,   # OA damper at/below this is "closed"
    persistence: int = 2,             # forgive single-interval transients
) -> list:
    """A G36-style single-duct AHU sequence as SOO clauses (ASHRAE Guideline 36)."""
    R = Role
    return [
        Clause("sat_tracks_setpoint",
               when=Predicate(R.SUPPLY_FAN_STATUS, "on"),
               expect=Predicate(R.SUPPLY_AIR_TEMP, "within", ref=R.SUPPLY_AIR_TEMP_SP,
                                tol=sat_tol),
               persistence=persistence),
        Clause("duct_static_tracks_setpoint",
               when=Predicate(R.SUPPLY_FAN_STATUS, "on"),
               expect=Predicate(R.DUCT_STATIC, "within", ref=R.DUCT_STATIC_SP,
                                tol=static_tol),
               persistence=persistence),
        Clause("no_simultaneous_heat_cool",
               when=Predicate(R.COOL_VALVE, "gt", value=valve_closed_pct),
               expect=Predicate(R.HEAT_VALVE, "le", value=valve_closed_pct),
               persistence=persistence),
        Clause("economizer_high_limit_lockout",
               when=Predicate(R.OAT, "gt", value=econ_high_limit_f),
               expect=Predicate(R.ECON_CMD, "off"),
               persistence=persistence),
        Clause("oa_damper_closed_when_fan_off",
               when=Predicate(R.SUPPLY_FAN_STATUS, "off"),
               expect=Predicate(R.OA_DAMPER, "le", value=damper_closed_pct),
               persistence=persistence),
    ]


def g36_plant_sequence(
    *,
    summer_lockout_oat_f: float = 65.0,   # boiler should be off above this OAT
    persistence: int = 2,
) -> list:
    """A small G36-style heating-plant sequence as SOO clauses (ASHRAE Guideline 36)."""
    R = Role
    return [
        Clause("boiler_summer_lockout",
               when=Predicate(R.OAT, "gt", value=summer_lockout_oat_f),
               expect=Predicate(R.BOILER_STATUS, "off"),
               persistence=persistence),
    ]
