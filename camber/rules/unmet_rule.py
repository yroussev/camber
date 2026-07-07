"""Rule: unmet setpoint (comfort / capacity) hours.

The most operator-facing FDD metric: how often is the space **outside its setpoint band** when it
should be comfortable? Sustained unmet hours mean a comfort complaint and often a capacity, airflow,
or control problem upstream. This counts occupied intervals where the space temperature runs above
the cooling setpoint (too hot) or below the heating setpoint (too cold), beyond a tolerance.

Occupancy comes from the OCCUPANCY role when present, else a default occupied-hours window. numpy/
pandas; a synthetic fixture proves it.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..resolve import occupied
from .base import Finding


class UnmetHours:
    """Flags excessive occupied hours with space temperature outside the setpoint band."""

    name = "unmet_setpoint_hours"
    roles_required = (Role.SPACE_TEMP,)
    roles_optional = (Role.HEAT_SP, Role.COOL_SP, Role.OCCUPANCY)

    def __init__(self, *, tol_F: float = 1.5, warn_pct: float = 5.0, fault_pct: float = 15.0,
                 start_hour: int = 7, end_hour: int = 18):
        self.tol_F = tol_F
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.start_hour = start_hour
        self.end_hour = end_hour

    def _occupied_mask(self, frame):
        if Role.OCCUPANCY in frame.columns:
            return frame[Role.OCCUPANCY].fillna(0) > 0
        return occupied(frame, start_hour=self.start_hour, end_hour=self.end_hour)

    def _unmet_masks(self, frame):
        st = frame[Role.SPACE_TEMP]
        false = pd.Series(False, index=frame.index)
        too_hot = (st > frame[Role.COOL_SP] + self.tol_F) if Role.COOL_SP in frame.columns else false
        too_cold = (st < frame[Role.HEAT_SP] - self.tol_F) if Role.HEAT_SP in frame.columns else false
        return too_hot.fillna(False), too_cold.fillna(False)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on occupied unmet hours."""
        if Role.COOL_SP not in frame.columns and Role.HEAT_SP not in frame.columns:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary=f"{equip}: no heating/cooling setpoint present")
        occ = self._occupied_mask(frame) & frame[Role.SPACE_TEMP].notna()
        n = int(occ.sum())
        if n == 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary=f"{equip}: no occupied space-temp data")
        too_hot, too_cold = self._unmet_masks(frame)
        hot_pct = 100.0 * float((too_hot & occ).sum()) / n
        cold_pct = 100.0 * float((too_cold & occ).sum()) / n
        unmet_pct = 100.0 * float(((too_hot | too_cold) & occ).sum()) / n
        sev = ("fault" if unmet_pct >= self.fault_pct else
               "warn" if unmet_pct >= self.warn_pct else "ok")
        return Finding(
            rule=self.name, equip=equip, severity=sev,
            metrics={"unmet_pct": round(unmet_pct, 2), "too_hot_pct": round(hot_pct, 2),
                     "too_cold_pct": round(cold_pct, 2), "n_occupied": n, "tol_F": self.tol_F},
            summary=(f"{equip}: unmet {unmet_pct:.0f}% of occupied hours "
                     f"(too hot {hot_pct:.0f}%, too cold {cold_pct:.0f}%)"))

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: space temp with its setpoint(s), unmet spans shaded."""
        from ..charts.evidence import Evidence
        roles = [Role.SPACE_TEMP] + [r for r in (Role.COOL_SP, Role.HEAT_SP)
                                     if r in frame.columns]
        if len(roles) < 2:
            return None
        too_hot, too_cold = self._unmet_masks(frame)
        return Evidence(renderer="multitrend", roles=roles, mask=(too_hot | too_cold),
                        label="unmet", title=f"{equip}: unmet setpoint")
