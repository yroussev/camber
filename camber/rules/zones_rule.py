"""Fleet rule: zones heating vs cooling census.

How many terminal zones heat while others cool, building-wide. A fleet that is
never free of simultaneous heat/cool during occupancy points at central
overcooling re-warmed locally. Adapts :func:`camber.zones.zone_states` /
:func:`camber.zones.time_of_week_profile` to the fleet role-frame interface.

Occupied intervals: when any zone trends ``OCCUPANCY`` the building is occupied whenever
at least one trended zone is (the points replace the schedule); otherwise the
``start_hour``/``end_hour``/``occupied_days`` schedule (default weekday 07-18).

0.106 (#186): the census now judges what its name says -- **different** zones heating and cooling
at once. A zone is *heating* when its reheat valve is open and *cooling* when it is not heating
and its airflow is above its minimum (or, with no airflow setpoint, its space at or above its
cooling setpoint). The severity rests on ``pct_hours_mixed``, the share of occupied hours with at
least one zone heating while another cools. Until 0.106 it rested on one box heating with its
airflow above its minimum (``pct_hours_any_both``, still reported), which is
``reheat_minimization_g36``'s footprint, not the fleet's. The minimum airflow is
``airflow_min_sp`` when mapped, else ``airflow_sp`` read as the minimum, else -- when
``airflow_sp`` moves like an active setpoint -- inferred from it (:mod:`camber.rules._minflow`).
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import effective_occupied_mask
from ..zones import time_of_week_profile, zone_states
from ._minflow import BASIS_INFERRED, min_flow_series
from .base import Finding

_ROLE_TO_ZONE_COL = {
    Role.HEAT_VALVE: "HWValve",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class ZonesHeatCoolCensus:
    """Fleet census of zones heating while others cool (PNNL Re-tuning Ch.5/7)."""

    name = "zones_heat_cool_census"
    roles_required = (Role.HEAT_VALVE,)
    roles_optional = (
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.AIRFLOW_MIN_SP,  # 0.106 (#186): the box minimum, read in place of airflow_sp
        Role.SPACE_TEMP,
        Role.COOL_SP,
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
        # The schedule is only an assumption: trended OCCUPANCY points replace it.
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def _occupied(self, frames: dict, index) -> pd.Series:
        occ_pts = [
            f[Role.OCCUPANCY].reindex(index)
            for f in frames.values()
            if Role.OCCUPANCY in f.columns and f[Role.OCCUPANCY].notna().any()
        ]
        occ = pd.concat(occ_pts, axis=1).max(axis=1) if occ_pts else None
        return effective_occupied_mask(
            index,
            occ=occ,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
        )

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run the diagnostic across the fleet's role-frames; return one aggregate Finding."""
        if not frames:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                summary="no zones with reheat valves",
            )
        legacy = {}
        inferred = []
        for equip, f in frames.items():
            vmin, basis = min_flow_series(f)
            g = f.drop(columns=[Role.AIRFLOW_SP, Role.AIRFLOW_MIN_SP], errors="ignore")
            g = g.rename(columns={r: c for r, c in _ROLE_TO_ZONE_COL.items() if r in g.columns})
            if vmin is not None:
                g["ActFlowSP"] = vmin
            if basis == BASIS_INFERRED:
                inferred.append(equip)
            legacy[equip] = g
        states = zone_states(legacy)
        if states.empty:
            return Finding(
                rule=self.name, equip="<fleet>", severity="info", summary="no zone states computed"
            )
        occupied = self._occupied(frames, states.index)
        occ = states[occupied]
        if occ.empty:
            return Finding(
                rule=self.name, equip="<fleet>", severity="info", summary="no occupied intervals"
            )
        avg_both = float(occ["n_both"].mean())
        pct_any_both = 100.0 * float((occ["n_both"] > 0).mean())
        avg_heating = float(occ["n_heating"].mean())
        avg_cooling = float(occ["n_cooling"].mean())
        avg_cooling_only = float(occ["n_cooling_only"].mean())
        pct_mixed = 100.0 * float(occ["mixed"].mean())
        heat_while_cool = occ.loc[occ["mixed"], "n_heating"]
        # Fleet is "in fault" when it is essentially never free of zones heating while others
        # cool during occupancy (0.106, #186: different zones, not one box above its minimum).
        severity = "fault" if pct_mixed >= 75.0 else ("warn" if pct_mixed >= 25.0 else "ok")
        # Boxes lacking any flow/space-temp signal could not be judged for "cooling":
        # they contribute 0 to n_cooling/n_both, so the census UNDER-counts. Don't let
        # that read as a confident "ok" -- decline to info and caveat which zones were
        # unevaluable (a real warn/fault already found the problem, so it stands).
        caveats = []
        unevaluable = states.attrs.get("cooling_unevaluable", [])
        if unevaluable:
            caveats.append(
                f"cooling not evaluated for {len(unevaluable)} zone(s) lacking flow/space-temp "
                f"setpoints: census may under-count cooling/simultaneous"
            )
            if severity == "ok":
                severity = "info"
        if inferred:
            caveats.append(
                f"{len(inferred)} zone(s) map an airflow_sp that moves like an active airflow "
                "setpoint: their minimum airflow was inferred from it (map airflow_min_sp)"
            )
        # keep the time-of-week profile available for charting downstream
        profile = time_of_week_profile(states, occupied_only=True, occupied=occupied)
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=severity,
            caveats=caveats,
            metrics={
                "n_zones": int(occ["n_zones"].max()),
                "avg_zones_heating": round(avg_heating, 2),
                "avg_zones_cooling": round(avg_cooling, 2),
                "avg_zones_cooling_only": round(avg_cooling_only, 2),
                "pct_hours_mixed": round(pct_mixed, 1),
                "avg_zones_heating_when_mixed": (
                    round(float(heat_while_cool.mean()), 2) if len(heat_while_cool) else 0.0
                ),
                # one box heating with its airflow above its minimum (reheat_minimization_g36's
                # footprint; the severity before 0.106)
                "avg_zones_both": round(avg_both, 2),
                "pct_hours_any_both": round(pct_any_both, 1),
                "max_zones_both": int(occ["n_both"].max()),
                "timeofweek_profile": profile,  # DataFrame, for charts
            },
            summary=(
                f"fleet: {pct_mixed:.0f}% of occupied hours have a zone heating while another "
                f"cools (avg {avg_heating:.1f} zones heating, {avg_cooling_only:.1f} cooling)"
            ),
        )
