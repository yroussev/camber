"""Rule: hot-water pump warm-weather lockout (PNNL Re-tuning Ch.8; 0.103, #132).

PNNL's heating-plant re-tuning locks out the hot-water *pumps* in warm weather, not only the boiler.
``boiler_summer_lockout`` judges boiler firing alone, so a pump left running all summer with the
boiler off passes it: the pump's energy is wasted, and the loop it keeps warm feeds heat into every
air handler and box whose heating valve leaks.

This rule reads when the pump runs -- its run status (``pump_status``), else its speed
(``hw_pump_speed`` > 5 %, the running cut of ``hw_pump_dp_reset``), else the loop flow (``hw_flow``
above 5 % of its own 95th percentile) -- and reports the share of its running hours at an outdoor
temperature above the lockout, with the same semantics and severity as ``boiler_summer_lockout``
(occupied hours on the generic weekday schedule; warn at 5 %, fault at 20 %; no OAT -> not
evaluated). It is a separate rule, not more metrics on the boiler's, so each finding names one
piece of equipment to lock out: a boiler and its pump can be locked out by different sequences,
a pump can be its own equipment (class ``pump``), and the boiler's finding and its existing answers
do not change.

**The lockout is shared.** The parameter has the boiler rule's name and default
(``summer_lockout_oat_f``, :data:`camber.plant.SUMMER_LOCKOUT_OAT_F`). In a config that sets it on
``boiler_summer_lockout`` but not here, :func:`camber.config.run_config` passes the boiler's value
on (recorded in the finding's ``param_basis``), so one site lockout judges both.

**Which pump.** A ``pump_status`` point says nothing about which loop the pump serves. On an
equipment with no hot-water point at all (no ``hw_pump_speed``, ``hw_flow``, hot-water temperature
or boiler signal), the rule declines (``info``) rather than judge what may be a chilled-water pump.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..plant import SUMMER_LOCKOUT_OAT_F, analyze_hw_pump_lockout, lockout_severity
from ..units import normalize_percent
from ._boilerrun import with_boiler_status
from .base import Finding

__all__ = ["HWPumpSummerLockout", "PUMP_RUN_FLOW_FRAC", "PUMP_RUN_SPEED_PCT", "pump_run"]

#: A pump runs when its speed is above this (%), as in ``hw_pump_dp_reset``.
PUMP_RUN_SPEED_PCT = 5.0
#: A pump runs when the loop flow is above this share of its own 95th percentile.
PUMP_RUN_FLOW_FRAC = 0.05

#: Roles that place an equipment on the hot-water loop.
_HW_ROLES = (
    Role.HW_PUMP_SPEED,
    Role.HW_FLOW,
    Role.HW_SUPPLY_TEMP,
    Role.HW_RETURN_TEMP,
    Role.BOILER_STATUS,
    Role.GAS_INPUT_RATE,
)

_SOURCE_LABEL = {
    Role.PUMP_STATUS: "pump status",
    Role.HW_PUMP_SPEED: "pump speed",
    Role.HW_FLOW: "hot-water flow",
}


def _num(frame: pd.DataFrame, role):
    if role not in frame.columns:
        return None
    s = pd.to_numeric(frame[role], errors="coerce")
    return s if s.notna().any() else None


def pump_run(frame: pd.DataFrame):
    """``(run, role)``: the pump's 0/1 running series (missing where its source has no reading)
    and the role it was read from, strongest evidence first; ``(None, None)`` when none."""
    status = _num(frame, Role.PUMP_STATUS)
    if status is not None:
        return (status > 0.5).astype(float).where(status.notna()), Role.PUMP_STATUS
    speed = _num(frame, Role.HW_PUMP_SPEED)
    if speed is not None:
        speed = normalize_percent(speed)
        return (speed > PUMP_RUN_SPEED_PCT).astype(float).where(speed.notna()), Role.HW_PUMP_SPEED
    flow = _num(frame, Role.HW_FLOW)
    if flow is not None:
        p95 = float(flow.quantile(0.95))
        if p95 > 0:
            on = flow > PUMP_RUN_FLOW_FRAC * p95
            return on.astype(float).where(flow.notna()), Role.HW_FLOW
    return None, None


class HWPumpSummerLockout:
    """Detects a hot-water pump running in warm weather (PNNL Re-tuning Ch.8)."""

    name = "hw_pump_summer_lockout"
    roles_required = ()
    roles_any_of = ((Role.PUMP_STATUS, Role.HW_PUMP_SPEED, Role.HW_FLOW),)
    roles_optional = (
        Role.OAT,
        Role.BOILER_STATUS,
        Role.GAS_INPUT_RATE,
        Role.HW_SUPPLY_TEMP,
        Role.HW_RETURN_TEMP,
    )

    def __init__(self, summer_lockout_oat_f: float = SUMMER_LOCKOUT_OAT_F):
        # the site's heating lockout; run_config passes boiler_summer_lockout's value on
        self.summer_lockout_oat_f = summer_lockout_oat_f

    def _decline(self, equip: str, why: str) -> Finding:
        return Finding(
            rule=self.name, equip=equip, severity="info", summary="insufficient data", caveats=[why]
        )

    def _legacy(self, frame: pd.DataFrame):
        """``(legacy frame, run role)`` for :func:`camber.plant.analyze_hw_pump_lockout`."""
        run, role = pump_run(frame)
        if run is None:
            return None, None
        legacy = pd.DataFrame({"PumpRun": run}, index=frame.index)
        if Role.OAT in frame.columns:
            legacy["OAT"] = pd.to_numeric(frame[Role.OAT], errors="coerce")
        boiler, _src = with_boiler_status(frame)
        fire = _num(boiler, Role.BOILER_STATUS)
        if fire is not None:
            legacy["BoilerRun"] = (fire > 0.5).astype(float).where(fire.notna())
        return legacy, role

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if not any(r in frame.columns for r in _HW_ROLES):
            return self._decline(
                equip,
                "not evaluated: only a pump status is mapped, with no hot-water point "
                "(hw_pump_speed, hw_flow, a hot-water temperature or a boiler signal), so the "
                "pump may serve another loop",
            )
        legacy, role = self._legacy(frame)
        if legacy is None:
            return self._decline(equip, "not evaluated: no pump status, speed or flow readings")
        res = analyze_hw_pump_lockout(legacy, equip, summer_lockout_oat_f=self.summer_lockout_oat_f)
        if res is None:
            return self._decline(equip, "not evaluated: no occupied-hour pump readings")
        source = _SOURCE_LABEL[role]
        caveats = []
        if role is not Role.PUMP_STATUS:
            caveats.append(
                f"no pump run status mapped: running read from the {source} "
                + (
                    f"(above {PUMP_RUN_SPEED_PCT:g} %)"
                    if role is Role.HW_PUMP_SPEED
                    else f"(above {PUMP_RUN_FLOW_FRAC:.0%} of its own 95th percentile)"
                )
            )
        sp = res.summer_run_pct
        severity = lockout_severity(sp)
        if sp is None:
            caveats.append("warm-weather lockout not evaluated: no OAT")
        if res.boiler_off_pct is None and sp:
            caveats.append("boiler firing not compared: no boiler run status or gas input mapped")
        summer_note = (
            f"{sp:.0f}% of running hours at OAT>{res.lockout_oat_f:.0f}F"
            if sp is not None
            else "warm-weather lockout not evaluated (no OAT)"
        )
        boiler_note = (
            f"; the boiler was off in {res.boiler_off_pct:.0f}% of those hours"
            if res.boiler_off_pct is not None
            else ""
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "pump_running_pct": res.pump_running_pct,
                "summer_run_pct": sp,
                "lockout_oat_f": res.lockout_oat_f,
                "max_oat_running_f": res.max_oat_running_f,
                "boiler_off_pct": res.boiler_off_pct,
                "n_running": res.n_running,
                "n_considered": res.n_considered,
                "run_source": source,
            },
            summary=(
                f"{equip}: HW pump runs {res.pump_running_pct:.0f}% of occupied hours; "
                f"{summer_note}{boiler_note}"
            ),
            caveats=caveats,
        )

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The occupied samples (the hours the rule judges) in which the pump ran at an OAT above
        the lockout."""
        from ..schedules import occupied_mask

        legacy, _role = self._legacy(frame)
        if legacy is None or "OAT" not in legacy.columns:
            return pd.Series(False, index=frame.index)
        warm = (legacy["PumpRun"] > 0.5) & (legacy["OAT"] > self.summer_lockout_oat_f)
        return warm & occupied_mask(frame.index)

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the outdoor temperature and the pump's speed (else its run signal), the
        occupied hours it ran above the lockout shaded."""
        from ..charts.evidence import Evidence

        _legacy, role = self._legacy(frame)
        if role is None or Role.OAT not in frame.columns:
            return None
        # a speed in % reads on the same axis as the outdoor temperature in °F; a 0/1 status
        # sits along the bottom of it
        shown = Role.HW_PUMP_SPEED if _num(frame, Role.HW_PUMP_SPEED) is not None else role
        return Evidence(
            renderer="multitrend",
            roles=[Role.OAT, shown],
            mask=self.violation_mask(frame),
            label=f"pump running above {self.summer_lockout_oat_f:g}°F (occupied hours)",
            title=f"{equip}: hot-water pump in warm weather",
        )
