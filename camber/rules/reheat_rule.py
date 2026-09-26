"""Rule: terminal-box reheat penalty.

A VAV/CAV box reheating while it is also being cooled (cold central supply air,
high outdoor temp, airflow above minimum, or space at/below the cooling setpoint)
wastes energy. Adapts :func:`camber.reheat.analyze_box` to the role-frame
interface; needs HEAT_VALVE, and uses OAT / air temperatures / airflow / setpoint
roles opportunistically for the richer indicators.

**Terminal air-temperature convention.** At a terminal box, ``SUPPLY_AIR_TEMP`` is the box's
*discharge* air (downstream of its reheat coil -- the natural point on most VAV controllers, and
the Haystack "discharge air temp" hint), and the box's *entering* primary air (the cold AHU supply)
is mapped to ``MIXED_AIR_TEMP`` -- the convention :mod:`camber.rules.vav_reheat_valve_rule` already
uses. The cold-supply indicator judges the entering air when it is mapped. With only the discharge
mapped it still runs, but reheat warms discharge air, so the count is a lower bound: the rule
caveats it and, when it is the headline (no OAT), never reports a confident "ok" from it.
Occupancy: a trended ``OCCUPANCY`` point replaces the ``start_hour``/``end_hour``/
``occupied_days`` schedule.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..reheat import analyze_box
from .base import Finding

# role -> the legacy column name analyze_box expects
_ROLE_TO_BOX_COL = {
    Role.HEAT_VALVE: "HWValve",
    Role.SPACE_TEMP: "SpaceTemp",
    Role.SUPPLY_AIR_TEMP: "SupplyAir",  # at a terminal: the box discharge (see module doc)
    Role.MIXED_AIR_TEMP: "PrimaryAir",  # at a terminal: the entering primary (AHU supply) air
    Role.HEAT_SP: "ActHeatSP",
    Role.COOL_SP: "ActCoolSP",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.DAMPER: "Damper",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
    Role.OCCUPANCY: "Occupancy",
}


class ReheatPenalty:
    """Detects terminal-box reheat that coincides with cooling (reheat penalty)
    (PNNL Re-tuning Ch.7)."""

    name = "reheat_penalty"
    roles_required = (Role.HEAT_VALVE,)
    roles_optional = (
        Role.OAT,
        Role.SPACE_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.HEAT_SP,
        Role.COOL_SP,
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.DAMPER,
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
        cols = {r: c for r, c in _ROLE_TO_BOX_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        # OAT is passed to analyze_box as a separate series, not a column
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        res = analyze_box(
            legacy,
            equip,
            oat=oat,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        # Headline = reheat at high OAT (heating in cooling weather). Falls back to
        # the cold-supply indicator if no OAT was available.
        hi = res.reheat_at_high_oat_pct
        headline = hi if oat is not None else res.reheat_and_coldsupply_pct
        severity = "fault" if headline >= 20.0 else ("warn" if headline >= 5.0 else "ok")
        caveats: list = []
        cold_metric: float | None = res.reheat_and_coldsupply_pct
        if res.coldsupply_basis == "supply":
            caveats.append(
                "cold-supply indicator judged on SUPPLY_AIR_TEMP, which at a terminal is the box "
                "discharge (downstream of the reheat coil): reheat warms it, so simultaneous "
                "heat/cool is under-counted -- map the entering primary air as MIXED_AIR_TEMP"
            )
            if oat is None and severity == "ok":
                severity = "info"
                cold_metric = None
        elif res.coldsupply_basis is None and oat is None:
            caveats.append(
                "no OAT and no primary/supply air temperature: reheat penalty not evaluated"
            )
            severity = "info" if severity == "ok" else severity
            cold_metric = None
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "valve_open_pct": res.valve_open_pct,
                "reheat_at_high_oat_pct": res.reheat_at_high_oat_pct,
                "reheat_and_coldsupply_pct": cold_metric,
                "coldsupply_basis": res.coldsupply_basis,
                "reheat_above_min_flow_pct": res.reheat_above_min_flow_pct,
                "reheat_below_coolsp_pct": res.reheat_below_coolsp_pct,
                "mean_valve_when_open": res.mean_valve_when_open,
                "n_considered": res.n_considered,
            },
            summary=(
                f"{equip}: reheat valve open {res.valve_open_pct:.0f}% of occupied "
                f"hours; "
                + (
                    f"{res.reheat_at_high_oat_pct:.0f}% at OAT>65F"
                    if oat is not None
                    else f"{res.reheat_and_coldsupply_pct:.0f}% into cold supply air"
                    if res.coldsupply_basis is not None
                    else "no OAT or air temperature to judge the penalty"
                )
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: reheat-valve position vs OAT — heating in warm weather stands out."""
        from ..charts.evidence import Evidence

        if Role.HEAT_VALVE in frame.columns and Role.OAT in frame.columns:
            return Evidence(
                renderer="oat_scatter",
                roles=[Role.HEAT_VALVE],
                title=f"{equip}: reheat valve vs OAT",
            )
        return None
