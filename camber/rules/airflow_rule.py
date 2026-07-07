"""Rule: VAV airflow not tracking its setpoint.

A VAV box should deliver the airflow its controller asks for. Persistent deviation means a stuck or
undersized damper, a failed actuator, a starved box (low duct static upstream), or a miscalibrated
flow sensor — and it cascades into unmet zone temperatures and reheat. This counts intervals where
measured airflow departs from the airflow setpoint beyond a tolerance (as a fraction of setpoint),
completing the "meeting its setpoint" family alongside `supply_air_control` (SAT) and
`unmet_setpoint_hours` (zone temp). numpy/pandas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from .base import Finding


class AirflowTracking:
    """Flags measured airflow that fails to track its setpoint (damper/actuator/starvation fault)."""

    name = "airflow_tracking"
    roles_required = (Role.AIRFLOW, Role.AIRFLOW_SP)
    roles_optional = ()

    def __init__(self, *, tol_frac: float = 0.20, min_sp: float = 1e-6,
                 warn_pct: float = 10.0, fault_pct: float = 25.0):
        self.tol_frac = tol_frac
        self.min_sp = min_sp
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def _rel_error(self, frame):
        flow = frame[Role.AIRFLOW]
        sp = frame[Role.AIRFLOW_SP]
        return (flow - sp) / sp.where(sp.abs() > self.min_sp)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on airflow-vs-setpoint tracking."""
        rel = self._rel_error(frame)
        active = rel.notna() & (frame[Role.AIRFLOW_SP].abs() > self.min_sp)
        n = int(active.sum())
        if n == 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary=f"{equip}: no active airflow-setpoint data")
        off = (rel.abs() > self.tol_frac) & active
        off_pct = 100.0 * float(off.sum()) / n
        under = 100.0 * float(((rel < -self.tol_frac) & active).sum()) / n   # starved / undershoot
        over = 100.0 * float(((rel > self.tol_frac) & active).sum()) / n     # overshoot
        mean_abs = float(rel[active].abs().mean())
        sev = ("fault" if off_pct >= self.fault_pct else
               "warn" if off_pct >= self.warn_pct else "ok")
        return Finding(
            rule=self.name, equip=equip, severity=sev,
            metrics={"off_setpoint_pct": round(off_pct, 2), "undershoot_pct": round(under, 2),
                     "overshoot_pct": round(over, 2), "mean_abs_rel_error": round(mean_abs, 3),
                     "n_active": n, "tol_frac": self.tol_frac},
            summary=(f"{equip}: airflow off setpoint {off_pct:.0f}% of active hours "
                     f"(mean |err| {mean_abs:.0%}; starved {under:.0f}%, over {over:.0f}%)"))

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: measured airflow vs its setpoint, off-setpoint spans shaded."""
        from ..charts.evidence import Evidence
        rel = self._rel_error(frame)
        off = (rel.abs() > self.tol_frac).fillna(False)
        return Evidence(renderer="multitrend", roles=[Role.AIRFLOW, Role.AIRFLOW_SP],
                        mask=off, label="off setpoint", title=f"{equip}: airflow vs setpoint")
