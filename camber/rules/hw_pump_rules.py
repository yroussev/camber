"""The pump and loop drift detectors on a **hot-water** loop (0.105, #152).

The five pump-family detectors are loop-parameterized but default to the chilled-water roles, so the
``pump`` drift family only ever watched a chilled-water loop. These subclasses are the same
detectors -- same physics, same size-relative gates (:mod:`camber.rules._pumpgates`), same
thresholds -- built on the hot-water roles:

* :class:`HwPumpFlowDrift` -- ``HW_FLOW`` at matched ``HW_PUMP_SPEED`` (``HW_DIFF_PRESS`` /
  ``HW_DIFF_PRESS_SP`` as the resistance confound);
* :class:`HwPumpHeadDrift` -- ``PUMP_HEAD`` at matched ``HW_PUMP_SPEED`` (``HW_FLOW`` confound);
* :class:`HwLoopDeltaTDrift` -- supply - return (``HW_SUPPLY_TEMP`` - ``HW_RETURN_TEMP``) at
  matched ``HW_FLOW``;
* :class:`HwLoopDPDrift` -- ``HW_DIFF_PRESS`` at matched ``HW_FLOW`` (``HW_DIFF_PRESS_SP``);
* :class:`HwPumpPowerDrift` -- ``POWER`` at matched ``HW_FLOW``.

Each carries its own rule ``name`` (``hw_`` + the chilled-water name) and baseline-store ``kind``
(``hw_`` + the chilled-water kind), so an equipment that carries both loops keeps two separate
frozen baselines and two separately named Findings instead of one loop silently overwriting the
other's reference. The metric keys are the chilled-water detector's own, so
:func:`camber.pumpdrift.diagnose_pump_drift` reads either loop. ``PUMP_STATUS``, ``PUMP_HEAD`` and
``POWER`` are loop-neutral roles: on an equipment carrying both loops they are one point, so map
the hot-water pump as its own equipment when each pump has its own status, head and power.

Every role stays a constructor argument, so a hot-water loop with no flow meter can pass
``load_role=HW_PUMP_SPEED`` to the ΔT detector (the affinity proxy). Not auto-registered (each
needs an injected ``BaselineStore``); the ``hw_pump`` drift family builds them.
"""

from __future__ import annotations

from ..model.roles import Role
from .loop_deltat_rule import LoopDeltaTDrift
from .loop_dp_rule import LoopDPDrift
from .pump_flow_rule import PumpFlowDrift
from .pump_head_rule import PumpHeadDrift
from .pump_power_rule import PumpPowerDrift

__all__ = [
    "HwLoopDPDrift",
    "HwLoopDeltaTDrift",
    "HwPumpFlowDrift",
    "HwPumpHeadDrift",
    "HwPumpPowerDrift",
]


class HwPumpFlowDrift(PumpFlowDrift):
    """:class:`~camber.rules.pump_flow_rule.PumpFlowDrift` on the hot-water loop's roles."""

    name = "hw_pump_flow_drift"
    kind = "hw_pump_flow"

    def __init__(
        self,
        store,
        *,
        flow_role: Role = Role.HW_FLOW,
        speed_role: Role = Role.HW_PUMP_SPEED,
        dp_role: Role = Role.HW_DIFF_PRESS,
        dp_sp_role: Role = Role.HW_DIFF_PRESS_SP,
        **kw,
    ):
        super().__init__(
            store,
            flow_role=flow_role,
            speed_role=speed_role,
            dp_role=dp_role,
            dp_sp_role=dp_sp_role,
            **kw,
        )


class HwPumpHeadDrift(PumpHeadDrift):
    """:class:`~camber.rules.pump_head_rule.PumpHeadDrift` on the hot-water loop's roles."""

    name = "hw_pump_head_drift"
    kind = "hw_pump_head"

    def __init__(
        self,
        store,
        *,
        speed_role: Role = Role.HW_PUMP_SPEED,
        flow_role: Role = Role.HW_FLOW,
        **kw,
    ):
        super().__init__(store, speed_role=speed_role, flow_role=flow_role, **kw)


class HwLoopDeltaTDrift(LoopDeltaTDrift):
    """:class:`~camber.rules.loop_deltat_rule.LoopDeltaTDrift` on the hot-water loop: warm is the
    supply, cool is the return."""

    name = "hw_loop_deltat_drift"
    kind = "hw_loop_deltat"

    def __init__(
        self,
        store,
        *,
        warm_role: Role = Role.HW_SUPPLY_TEMP,
        cool_role: Role = Role.HW_RETURN_TEMP,
        load_role: Role = Role.HW_FLOW,
        **kw,
    ):
        super().__init__(store, warm_role=warm_role, cool_role=cool_role, load_role=load_role, **kw)


class HwLoopDPDrift(LoopDPDrift):
    """:class:`~camber.rules.loop_dp_rule.LoopDPDrift` on the hot-water loop's roles."""

    name = "hw_loop_dp_drift"
    kind = "hw_loop_dp"

    def __init__(
        self,
        store,
        *,
        dp_role: Role = Role.HW_DIFF_PRESS,
        flow_role: Role = Role.HW_FLOW,
        sp_role: Role = Role.HW_DIFF_PRESS_SP,
        **kw,
    ):
        super().__init__(store, dp_role=dp_role, flow_role=flow_role, sp_role=sp_role, **kw)


class HwPumpPowerDrift(PumpPowerDrift):
    """:class:`~camber.rules.pump_power_rule.PumpPowerDrift` normalized on the hot-water flow."""

    name = "hw_pump_power_drift"
    kind = "hw_pump_power"

    def __init__(self, store, *, flow_role: Role = Role.HW_FLOW, **kw):
        super().__init__(store, flow_role=flow_role, **kw)
