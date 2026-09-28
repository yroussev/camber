"""Rule: duct static-pressure setpoint not resetting.

A fixed duct-static setpoint runs the supply fan harder than the zones need. ASHRAE G36 trim-and-
respond resets static down when no zone is starved. A setpoint that never moves across a range of
load is a missed reset (fan energy left on the table). Flags a static setpoint whose range over the
window is below a threshold. numpy/pandas.

**A one-time step is not a reset (#63).** A setpoint held at one value for weeks and then changed
once (an operator edit, a sequence change) has a wide range too. The rule therefore also requires
the setpoint to move *repeatedly* -- on at least 3 days and 10 % of the days judged -- and, when a
demand driver is trended, to move *with* it (``|Spearman rho| >= 0.3``): the zones' static-pressure
reset requests, else the supply airflow. Fan speed is not used as a driver: it follows the static
setpoint through the static-pressure loop, so it would confirm any change by construction. Movement
is judged on fan-on samples when a fan signal exists. See :mod:`camber.setpoint_reset`.

========================  ========  ===============================================================
setpoint behaviour        severity  reads as
========================  ========  ===============================================================
flat                      warn      no trim-and-respond reset
step (one-time / manual)  warn      a step or manual change, not a reset
resets with the driver    ok        resets with demand
varies, no driver         ok        varies repeatedly; caveat: demand driver not trended
varies, not with driver   info      varies but not with requests/airflow -- not confirmed
unclear                   info      moves, but too little to call either way
========================  ========  ===============================================================
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import fan_on_mask
from ..setpoint_reset import classify_setpoint_reset
from .base import Finding

# A move of 0.05 inWC is about one G36 trim-and-respond response step (SPres ~0.04-0.06 inWC) and
# well above setpoint-register rounding; smaller day ranges are not "moving".
_MOVE_MIN_INWC = 0.05


class StaticPressureReset:
    """Flags a duct static-pressure setpoint that doesn't reset (stays flat or only steps)."""

    name = "static_pressure_reset"
    roles_required = (Role.DUCT_STATIC_SP,)
    roles_optional = (
        Role.AIRFLOW,
        # #63: the demand driver a reset should follow (requests preferred over airflow) ...
        Role.STATIC_PRESSURE_REQUESTS,
        # ... and the fan-on gate: status, else speed (airflow is the third alternative)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(self, *, min_range_inwc: float = 0.15, move_min_inwc: float = _MOVE_MIN_INWC):
        self.min_range_inwc = min_range_inwc
        self.move_min_inwc = move_min_inwc

    @staticmethod
    def _driver(frame: pd.DataFrame, keep):
        for role, label in (
            (Role.STATIC_PRESSURE_REQUESTS, "static-pressure requests"),
            (Role.AIRFLOW, "supply airflow"),
        ):
            if role in frame.columns and frame[role].notna().any():
                s = pd.to_numeric(frame[role], errors="coerce")
                return (s if keep is None else s[keep]), label
        return None, None

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        sp = frame[Role.DUCT_STATIC_SP].dropna()
        if len(sp) < 3:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: insufficient static-pressure-setpoint data",
            )
        rng = float(sp.max() - sp.min())
        fan, fan_src = fan_on_mask(frame)
        keep = None if fan is None else fan.reindex(frame.index).fillna(False).astype(bool)
        judged = frame[Role.DUCT_STATIC_SP] if keep is None else frame[Role.DUCT_STATIC_SP][keep]
        driver, driver_label = self._driver(frame, keep)
        beh = classify_setpoint_reset(
            judged,
            driver,
            driver_label=driver_label,
            min_range=self.min_range_inwc,
            move_min=self.move_min_inwc,
            units="inWC",
        )
        caveats: list = []
        kind = beh.kind
        if kind == "insufficient":  # too few samples to judge movement: the range alone decides
            resets = rng >= self.min_range_inwc
            sev = "ok" if resets else "warn"
            verdict = "resets with demand" if resets else "flat (no trim-and-respond reset)"
            caveats.append(
                "too few setpoint samples to tell a reset from a one-time step: judged on the "
                "range alone"
            )
        elif kind == "flat":
            resets, sev = False, "warn"
            verdict = "flat (no trim-and-respond reset)"
        elif kind == "step":
            resets, sev = False, "warn"
            verdict = f"not reset: {beh.label}"
        elif kind == "reset":
            resets, sev = True, "ok"
            verdict = f"resets with demand -- {beh.label}"
        elif kind == "varies" and not beh.driver_checked:
            resets, sev = True, "ok"
            verdict = f"resets (unconfirmed) -- {beh.label}"
            caveats.append(
                "no static-pressure requests or supply airflow trended: the setpoint moves "
                "repeatedly, but it can't be confirmed that it follows demand -- map "
                "STATIC_PRESSURE_REQUESTS"
            )
        else:  # varies but not with its driver, or unclear
            resets, sev = False, "info"
            verdict = f"reset not confirmed -- {beh.label}"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "sp_range_inwc": round(rng, 4),
                "sp_median_inwc": round(float(sp.median()), 4),
                "min_range_inwc": self.min_range_inwc,
                "resets": resets,
                # #63: how the setpoint moved (see camber.setpoint_reset)
                "sp_behaviour": kind,
                "sp_moving_days": beh.n_moving_days,
                "sp_days": beh.n_days,
                "sp_levels_inwc": beh.levels,
                "sp_driver": beh.driver,
                "sp_driver_rho": beh.driver_rho,
                "fan_gate": fan_src,
            },
            summary=f"{equip}: static-pressure setpoint range {rng:.2f} inWC — {verdict}",
            caveats=caveats,
        )
