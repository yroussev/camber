"""Rule: VAV airflow not tracking its setpoint.

A VAV box should deliver the airflow its controller asks for. Persistent deviation means a stuck or
undersized damper, a failed actuator, a starved box (low duct static upstream), or a miscalibrated
flow sensor — and it cascades into unmet zone temperatures and reheat. This counts intervals where
measured airflow departs from the airflow setpoint beyond a tolerance (as a fraction of setpoint),
completing the "meeting its setpoint" family alongside `supply_air_control` (SAT) and
`unmet_setpoint_hours` (zone temp). numpy/pandas.

0.107 (#198): a box delivers no air while its air handler is off, whatever the setpoint reads, so
fan-off hours are not starvation. With a fan status or speed in the frame (the box's own fan, or
the air handler's propagated to it) only fan-on samples are judged, as in `supply_air_control`;
with a trended ``OCCUPANCY`` point only occupied ones. Measured airflow is not used as the fan
proxy: a starved box reads zero flow, which is the fault itself. With neither signal every sample
with a setpoint is judged, and a caveat says so.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, effective_occupied_mask, fan_on_mask
from .base import Finding

_UNGATED_CAVEAT = (
    "no fan status/speed or occupancy point: every sample with an airflow setpoint is judged, "
    "so hours with the air handler off can read as starved -- map SUPPLY_FAN_STATUS or OCCUPANCY"
)


class AirflowTracking:
    """Flags measured airflow that fails to track its setpoint
    (damper/actuator/starvation fault)."""

    name = "airflow_tracking"
    roles_required = (Role.AIRFLOW, Role.AIRFLOW_SP)
    #: 0.107 (#198): the running gate (fan status, else speed) and the occupied gate
    roles_optional = (Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED, Role.OCCUPANCY)

    def __init__(
        self,
        *,
        tol_frac: float = 0.20,
        min_sp: float = 1e-6,
        abs_tol: float = 0.0,
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
    ):
        self.tol_frac = tol_frac
        self.min_sp = min_sp
        self.abs_tol = abs_tol
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def _rel_error(self, frame):
        flow = frame[Role.AIRFLOW]
        sp = frame[Role.AIRFLOW_SP]
        return (flow - sp) / sp.where(sp.abs() > self.min_sp)

    def _gate(self, frame):
        """``(mask, fan_source, occupancy_gate)``: the samples the box is judged on (0.107, #198).

        Fan-on (status, else speed; never the airflow proxy) AND occupied (a trended ``OCCUPANCY``
        point; no assumed schedule). All True when neither signal is in the frame.
        """
        gate = pd.Series(True, index=frame.index)
        fan, fan_src = fan_on_mask(frame[[c for c in frame.columns if c != Role.AIRFLOW]])
        if fan is not None:
            gate &= pd.Series(fan.to_numpy(dtype=bool), index=frame.index)
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        occ_label = "none trended"
        if occ is not None and occ.notna().any():
            om = effective_occupied_mask(frame.index, occ=occ)
            gate &= pd.Series(om.to_numpy(dtype=bool), index=frame.index)
            occ_label = "trended occupancy"
        return gate, fan_src, occ_label

    def _off(self, rel, sp):
        """Samples off setpoint: beyond ``tol_frac`` of the setpoint and beyond ``abs_tol``."""
        far = (rel * sp).abs() > self.abs_tol
        return (rel < -self.tol_frac) & far, (rel > self.tol_frac) & far

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on airflow-vs-setpoint tracking."""
        rel = self._rel_error(frame)
        sp = frame[Role.AIRFLOW_SP]
        gate, fan_src, occ_label = self._gate(frame)
        ungated = fan_src == FAN_GATE_NONE and occ_label == "none trended"
        active = rel.notna() & (sp.abs() > self.min_sp) & gate
        n = int(active.sum())
        caveats = [_UNGATED_CAVEAT] if ungated else []
        gate_metrics = {"fan_gate": fan_src, "occupancy_gate": occ_label}
        if n == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"n_active": 0, **gate_metrics},
                summary=(
                    f"{equip}: no active airflow-setpoint data"
                    + (
                        ""
                        if ungated
                        else " while the fan runs"
                        + (" and the zone is occupied" if occ_label != "none trended" else "")
                    )
                ),
                caveats=caveats,
            )
        low, high = self._off(rel, sp)
        under = 100.0 * float((low & active).sum()) / n  # starved / undershoot
        over = 100.0 * float((high & active).sum()) / n  # overshoot
        off_pct = under + over
        mean_abs = float(rel[active].abs().mean())
        sev = "fault" if off_pct >= self.fault_pct else "warn" if off_pct >= self.warn_pct else "ok"
        hours = self._hours(fan_src, occ_label)
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "off_setpoint_pct": round(off_pct, 2),
                "undershoot_pct": round(under, 2),
                "overshoot_pct": round(over, 2),
                "mean_abs_rel_error": round(mean_abs, 3),
                "n_active": n,
                "tol_frac": self.tol_frac,
                **gate_metrics,
            },
            summary=(
                f"{equip}: airflow off setpoint {off_pct:.0f}% of {hours} "
                f"(mean |err| {mean_abs:.0%}; starved {under:.0f}%, over {over:.0f}%)"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: measured airflow vs its setpoint, off-setpoint spans shaded."""
        from ..charts.evidence import Evidence

        rel = self._rel_error(frame)
        sp = frame[Role.AIRFLOW_SP]
        low, high = self._off(rel, sp)
        gate, fan_src, occ_label = self._gate(frame)
        # 0.111 (#310): the samples analyze counts in n_active, the share's denominator
        active = (rel.notna() & (sp.abs() > self.min_sp) & gate).fillna(False)
        off = ((low | high) & active).fillna(False)
        return Evidence(
            renderer="multitrend",
            roles=[Role.AIRFLOW, Role.AIRFLOW_SP],
            mask=off,
            label="off setpoint",
            title=f"{equip}: airflow vs setpoint",
            judged=active,
            judged_basis=self._hours(fan_src, occ_label).replace("hours", "samples"),
        )

    @staticmethod
    def _hours(fan_src, occ_label) -> str:
        """What the judged samples are, as the summary names them."""
        if fan_src == FAN_GATE_NONE and occ_label == "none trended":
            return "active hours"
        if fan_src == FAN_GATE_NONE:
            return "occupied hours"
        return "fan-on hours" if occ_label == "none trended" else "occupied fan-on hours"
