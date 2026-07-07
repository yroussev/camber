"""Rule: supply-air temperature not meeting its setpoint (discharge-air control).

A healthy AHU holds discharge (supply) air temperature at its setpoint. Persistent deviation means a
control or capacity problem — a starved or oversized coil, a hunting loop, a bad sensor, or a valve
that can't reach the needed position. This counts intervals where SAT departs from SAT setpoint
beyond a tolerance (optionally only when the fan runs). Complements the SAT-*reset* rule (is the
setpoint right?) by asking: is the unit even *meeting* the setpoint it has? numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding


class SupplyAirControl:
    """Flags supply-air temperature that fails to track its setpoint (control/capacity fault)."""

    name = "supply_air_control"
    roles_required = (Role.SUPPLY_AIR_TEMP, Role.SUPPLY_AIR_TEMP_SP)
    roles_optional = (Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED)

    def __init__(self, *, tol_F: float = 2.0, warn_pct: float = 10.0, fault_pct: float = 25.0):
        self.tol_F = tol_F
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def _running_mask(self, frame):
        if Role.SUPPLY_FAN_STATUS in frame.columns:
            return frame[Role.SUPPLY_FAN_STATUS].fillna(0) > 0
        if Role.SUPPLY_FAN_SPEED in frame.columns:
            return frame[Role.SUPPLY_FAN_SPEED].fillna(0) > 0.05
        return pd.Series(True, index=frame.index)

    def _deviation(self, frame):
        return (frame[Role.SUPPLY_AIR_TEMP] - frame[Role.SUPPLY_AIR_TEMP_SP])

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on SAT-vs-setpoint tracking."""
        dev = self._deviation(frame)
        run = self._running_mask(frame) & dev.notna()
        n = int(run.sum())
        if n == 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary=f"{equip}: no running supply-air data")
        off = (dev.abs() > self.tol_F) & run
        off_pct = 100.0 * float(off.sum()) / n
        above = 100.0 * float(((dev > self.tol_F) & run).sum()) / n     # SAT too warm
        below = 100.0 * float(((dev < -self.tol_F) & run).sum()) / n    # SAT too cold
        mean_abs = float(dev[run].abs().mean())
        sev = ("fault" if off_pct >= self.fault_pct else
               "warn" if off_pct >= self.warn_pct else "ok")
        return Finding(
            rule=self.name, equip=equip, severity=sev,
            metrics={"off_setpoint_pct": round(off_pct, 2), "too_warm_pct": round(above, 2),
                     "too_cold_pct": round(below, 2), "mean_abs_dev_F": round(mean_abs, 2),
                     "n_running": n, "tol_F": self.tol_F},
            summary=(f"{equip}: SAT off setpoint {off_pct:.0f}% of running hours "
                     f"(mean |Δ| {mean_abs:.1f}°F; warm {above:.0f}%, cold {below:.0f}%)"))

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: SAT vs its setpoint, off-setpoint spans shaded."""
        from ..charts.evidence import Evidence
        run = self._running_mask(frame)
        off = (self._deviation(frame).abs() > self.tol_F) & run
        return Evidence(renderer="multitrend",
                        roles=[Role.SUPPLY_AIR_TEMP, Role.SUPPLY_AIR_TEMP_SP],
                        mask=off.fillna(False), label="off setpoint",
                        title=f"{equip}: SAT vs setpoint")
