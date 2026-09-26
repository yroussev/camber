"""Rule: Std-55-aligned overcooling severity (depth x duration below setpoint).

A comfort-severity diagnostic, distinct from :class:`OvercoolingMinFlow` (which
scores the min-flow root cause / ECM). This one measures how far a zone sits below
its cooling setpoint -- relative to the comfort deadband when a heating setpoint is
also present -- and for how long, assigning info/warn/fault tiers only when the
excursion is *sustained* (see :func:`camber.overcooling_severity`).

The ``info`` tier is informational only: it is emitted at Finding severity
``"info"``, which the triage layer treats as non-actionable, so it never enters the
ranked/headline fault totals.

Morning recovery from setback is excluded (``WARMUP`` when mapped, else the first
``recovery_hours`` of each occupied block), and a space below setpoint with its reheat
valve (``HEAT_VALVE``) saturated is reported as a **heating shortfall**, not overcooling.
Occupancy: a trended ``OCCUPANCY`` point replaces the ``start_hour``/``end_hour``/
``occupied_days`` schedule. Samples with the fan trended off (``SUPPLY_FAN_STATUS`` /
``SUPPLY_FAN_SPEED``) are free-floating, not overcooled, and are excluded.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..overcooling_severity import DEFAULT_TIERS, analyze_overcooling_severity
from .base import Finding

_ROLE_TO_COL = {
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.HEAT_SP: "ActHeatSP",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
    Role.OCCUPANCY: "Occupancy",
    Role.HEAT_VALVE: "HWValve",
    Role.SUPPLY_FAN_STATUS: "FanStatus",
    Role.SUPPLY_FAN_SPEED: "FanSpeed",
}


class OvercoolingSeverity:
    """Std-55 overcooling severity: depth x duration below the (deadband-aware) setpoint."""

    name = "overcooling_severity"
    roles_required = (Role.SPACE_TEMP, Role.COOL_SP)
    roles_optional = (
        Role.HEAT_SP,
        Role.WARMUP,
        Role.COOLDOWN,
        Role.OCCUPANCY,
        Role.HEAT_VALVE,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(
        self,
        *,
        tiers: dict | None = None,
        window_min: float = 60.0,
        relative_to_deadband: bool = True,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
        recovery_hours: float = 2.0,
        reheat_saturated_pct: float = 90.0,
    ):
        self.tiers = tiers
        self.window_min = window_min
        self.relative_to_deadband = relative_to_deadband
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)
        self.recovery_hours = recovery_hours
        self.reheat_saturated_pct = reheat_saturated_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the severity diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_overcooling_severity(
            legacy,
            equip,
            tiers=self.tiers,
            window_min=self.window_min,
            relative_to_deadband=self.relative_to_deadband,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
            recovery_hours=self.recovery_hours,
            reheat_saturated_pct=self.reheat_saturated_pct,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        ref = "heating setpoint" if res.mode == "relative_deadband" else "cooling setpoint"
        # Without a heating setpoint the deadband-relative severity can't be computed;
        # analysis falls back to absolute (depth below the COOLING setpoint), which
        # over-flags a healthy space sitting a few degF below its cooling SP. Be
        # conservative: don't emit the top fault tier on the absolute fallback alone,
        # and caveat it. When a heating setpoint IS present, keep the computed severity.
        severity = res.severity  # ok | info | warn | fault (info non-actionable)
        caveats = []
        if Role.HEAT_SP not in frame.columns and res.mode == "absolute":
            caveats.append(
                "no heating setpoint: severity judged on cooling-SP depth only (may over-flag)"
            )
            if severity == "fault":
                severity = "warn"
        shortfall = res.shortfall_severity
        short_pct = (res.shortfall_tier_pct or {}).get("warn")
        if res.reheat_evaluated and shortfall in ("warn", "fault"):
            caveats.append(
                f"heating shortfall, not overcooling: {short_pct:.0f}% of occupied samples sat "
                f">= {(self.tiers or DEFAULT_TIERS)['warn']:g} degF below the reference with "
                f"the reheat valve >= {self.reheat_saturated_pct:g}% open -- excluded from the "
                "overcooling tiers (check reheat capacity / hot-water supply)"
            )
            if severity == "ok":
                severity = "info"
        elif not res.reheat_evaluated and severity in ("warn", "fault"):
            caveats.append(
                "no reheat valve: a sub-setpoint space with its reheat at full output (a heating "
                "shortfall) cannot be told apart from overcooling"
            )
        if res.n_recovery_excluded:
            caveats.append(
                f"{res.n_recovery_excluded} morning-recovery sample(s) excluded (first "
                f"{self.recovery_hours:g} h after unoccupied; no WARMUP point mapped)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "mode": res.mode,
                "interval_min": res.interval_min,
                "window_min": res.window_min,
                "median_depth_f": res.median_depth_f,
                "max_depth_f": res.max_depth_f,
                "fault_pct": res.tier_pct.get("fault"),
                "warn_pct": res.tier_pct.get("warn"),
                "info_pct": res.tier_pct.get("info"),
                "fault_minutes": res.tier_minutes.get("fault"),
                "n_considered": res.n_considered,
                "shortfall_severity": shortfall if res.reheat_evaluated else None,
                "shortfall_warn_pct": short_pct if res.reheat_evaluated else None,
                "n_recovery_excluded": res.n_recovery_excluded,
                "n_fan_off_excluded": res.n_fan_off_excluded,
            },
            summary=(
                f"{equip}: overcooled below {ref} -- max {res.max_depth_f:.1f} degF, "
                f"sustained tiers info/warn/fault = "
                f"{res.tier_pct.get('info'):.0f}/{res.tier_pct.get('warn'):.0f}/"
                f"{res.tier_pct.get('fault'):.0f}% of occupied samples "
                f"({res.window_min:.0f}-min persistence @ {res.interval_min:.0f}-min data)"
                + (
                    f"; heating shortfall (reheat saturated) {short_pct:.0f}% -- not overcooling"
                    if res.reheat_evaluated and shortfall in ("warn", "fault")
                    else ""
                )
            ),
            caveats=caveats,
        )
