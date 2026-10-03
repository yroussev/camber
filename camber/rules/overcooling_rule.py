"""Rule: high-minimum-airflow / overcooling root cause (PNNL Ch.7).

Flags terminal boxes that overcool because they cannot throttle below their
minimum airflow when already satisfied on cooling -- the root cause behind the
reheat penalty. Adapts :func:`camber.overcooling.analyze_overcooling` to the
role-frame interface.

0.98 (#85): the "with reheat" overlap asks whether heat was actually delivered, so it reads the
reheat valve's measured position (``HEAT_VALVE_POSITION``) when it is mapped beside the demand
(``HEAT_VALVE``); ``valve_signal`` records which. A valve stuck shut then adds no reheat overlap,
and the finding carries the same demand-vs-position caveat as ``reheat_penalty`` (demand >= 90 %
while the position is <= 5 % on >= 25 % of the occupied full-demand samples).
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..overcooling import analyze_overcooling
from ..schedules import effective_occupied_mask
from .base import Finding
from .reheat_rule import _divergence_caveat, _reheat_valve_role, _valve_divergence, _with_valve

_ROLE_TO_COL = {
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.DAMPER: "Damper",
    Role.HEAT_VALVE: "HWValve",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
    Role.OCCUPANCY: "Occupancy",
}


class OvercoolingMinFlow:
    """Detects overcooling driven by too-high minimum airflow at terminal boxes
    (PNNL Re-tuning Ch.7)."""

    name = "overcooling_min_flow"
    roles_required = (Role.SPACE_TEMP, Role.COOL_SP)
    roles_optional = (
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.DAMPER,
        Role.HEAT_VALVE,
        Role.HEAT_VALVE_POSITION,  # 0.98 (#85): read in place of the demand when mapped
        Role.WARMUP,
        Role.COOLDOWN,
        Role.OCCUPANCY,
    )

    def __init__(
        self,
        *,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
    ):
        # The schedule is only an assumption: a trended OCCUPANCY point replaces it.
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        # 0.98 (#85): the reheat overlap reads the measured position when it is mapped
        valve_role, valve_signal = _reheat_valve_role(frame)
        legacy = _with_valve(frame, cols, valve_role)
        res = analyze_overcooling(
            legacy,
            equip,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary="insufficient data",
            )
        if not res.minflow_evaluable or res.overcool_at_minflow_pct is None:
            missing = [
                r.value for r in (Role.AIRFLOW, Role.AIRFLOW_SP) if r not in frame.columns
            ] or ["airflow/airflow-setpoint overlap"]
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={
                    "declined": True,
                    "satisfied_pct": res.satisfied_pct,
                    "overcool_at_minflow_pct": None,
                    "overcool_with_reheat_pct": None,
                    "median_minflow_fraction": None,
                    "n_considered": res.n_considered,
                },
                summary=f"{equip}: overcooling at minimum flow not evaluated",
                caveats=[
                    "at-minimum-flow not evaluated: needs measured airflow and its setpoint "
                    f"(missing: {', '.join(missing)}) -- the box may still overcool at its minimum"
                ],
            )
        # Severity from overcooling-at-min-flow that co-occurs with reheat (the
        # actionable, wasteful case). Fall back to overcool-at-min-flow ONLY when
        # there is genuinely no heat-valve data -- not when reheat merely never
        # overlaps (a real 0% reheat overlap is a finding, not a missing input). The
        # old ``a or b`` collapsed those two cases, scoring on the broader metric
        # whenever the valve existed but never co-occurred.
        oc = res.overcool_with_reheat_pct if res.has_heat_valve else res.overcool_at_minflow_pct
        oc = 0.0 if oc is None else oc  # both are set whenever minflow_evaluable
        severity = "fault" if oc >= 15.0 else ("warn" if oc >= 5.0 else "ok")
        # Without a damper column the "at minimum flow" condition can't be CONFIRMED --
        # at_min rests on flow alone, which over-counts. Don't let that broadened,
        # unconfirmed count drive the top tier: cap at warn and caveat. The damper-present
        # path (a genuine, confirmed overcooling-at-min fault) is untouched and still fires.
        caveats = []
        if not res.damper_present:
            caveats.append("damper unavailable: at-min inferred from flow only (unconfirmed)")
            if severity == "fault":
                severity = "warn"
        div = self._divergence(frame)
        if div["diverges"]:
            caveats.append(_divergence_caveat(div["valve_divergence_share"]))
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "satisfied_pct": res.satisfied_pct,
                "overcool_at_minflow_pct": res.overcool_at_minflow_pct,
                "overcool_with_reheat_pct": res.overcool_with_reheat_pct,
                "median_minflow_fraction": res.median_minflow_fraction,
                "n_considered": res.n_considered,
                "valve_signal": valve_signal,
                "valve_divergence_share": div["valve_divergence_share"],
            },
            summary=(
                f"{equip}: overcools at min flow {res.overcool_at_minflow_pct:.0f}% "
                f"of occupied hours ({res.overcool_with_reheat_pct:.0f}% with "
                f"reheat); min-flow ~{res.median_minflow_fraction:.0%} of peak"
            ),
            caveats=caveats,
        )

    def _divergence(self, frame: pd.DataFrame) -> dict:
        """Demand vs position over the rule's occupied samples (see reheat_rule)."""
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        keep = effective_occupied_mask(
            frame.index,
            occ=occ,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
            warmup=frame[Role.WARMUP] if Role.WARMUP in frame.columns else None,
            cooldown=frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None,
        )
        return _valve_divergence(frame, pd.Series(keep, index=frame.index))

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: space temp vs cooling setpoint, spans where space runs below setpoint
        shaded."""
        from ..charts.evidence import Evidence

        if Role.SPACE_TEMP in frame.columns and Role.COOL_SP in frame.columns:
            # a comfortable space normally sits below the cooling setpoint; only shade where it
            # runs well below it (overcooled past the deadband), not routine operation
            margin = 3.0
            mask = ((frame[Role.COOL_SP] - frame[Role.SPACE_TEMP]) > margin).fillna(False)
            return Evidence(
                renderer="multitrend",
                roles=[Role.SPACE_TEMP, Role.COOL_SP],
                mask=mask,
                label="overcooled (>3F below SP)",
                title=f"{equip}: overcooling",
            )
        return None
