"""Fleet rule: is a whole air-handler zone cohort demand-starved at once (an upstream fault)?

The **common-mode twin** of :class:`camber.rules.rogue_zone_census_rule.RogueZoneCensus`. The rogue
census finds when *one* zone monopolizes an air handler's G36 reset requests (an outlier); this
finds the opposite shape -- when **most or all** of an AHU's zones raise reset requests at once.
That cohort-wide pattern is not N independent zone faults: it points at **one upstream fault** the
reset cannot fix (duct-static setpoint capped too low, supply fan maxed / undersized / failing, or a
restricted upstream damper). Diagnosing "AHU-1's whole cohort is starved -> look upstream" instead
of raising a fault on every zone is a cross-layer call that only the served-by
[topology](camber.model.topology) makes possible.

Like the rogue census it is a :class:`camber.rules.base.FleetRule` (``wants_topology=True``, so
:meth:`Registry.run_fleet` auto-builds a naming grouping when no semantic one is supplied), scopes
**per air handler**, and attaches the same provenance/coverage-aware caveat. ``reset`` selects the
family: ``"static"`` (airflow below setpoint while the damper is open -- the clean primary case) or
``"sat"`` (all zones warm -- caveated as a possible design-day rather than a fault). Warn-level
(an operational opportunity, not a hard fault); thresholds are screening / opportunity-grade.
"""

from __future__ import annotations

from typing import Any

from ..g36_reset import cohort_starvation
from ..model.roles import Role
from ._topology_grouping import resolve_grouping
from .base import Finding

# request cycles count only while the system is on: supply-fan status and/or occupancy, when the
# zone frames carry them (per zone, or merged in as a building-level ``shared`` series)
_GATES: tuple[Role, ...] = (Role.SUPPLY_FAN_STATUS, Role.OCCUPANCY)

# reset key -> (roles_required, human label)
_RESETS: dict[str, tuple[tuple[Role, ...], str]] = {
    "sat": ((Role.SPACE_TEMP, Role.COOL_SP), "supply-air-temp"),
    "static": ((Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER), "duct-static"),
}

_UPSTREAM = {
    "static": "look upstream (duct-static SP capped, supply fan maxed/failing, or a restricted "
    "upstream damper), not at individual zones",
    "sat": "look upstream (cooling capacity or the SAT reset maxed, or an OA/coil fault), not at "
    "individual zones",
}


class CohortStarvation:
    """Flags an air handler whose whole zone cohort raises reset requests at once (upstream fault).

    Groups zones per air handler (via a served-by topology, else a naming-heuristic one, else
    building-wide with a caveat) and flags a group when a high fraction of its zones request the
    reset simultaneously on a sustained fraction of active cycles -- the common-mode shape the
    rogue census's single-outlier statistic never trips. ``reset`` selects ``"static"`` (airflow
    vs setpoint and damper) or ``"sat"`` (zone temp vs cooling setpoint). Warn-level.
    """

    def __init__(
        self,
        reset: str = "static",
        *,
        groups=None,
        cohort_frac: float = 0.75,
        sustained_frac: float = 0.50,
        min_active_cycles: int = 10,
        min_zones_per_group: int = 3,
        min_present_frac: float | None = None,
    ):
        if reset not in _RESETS:
            raise ValueError(f"reset must be one of {sorted(_RESETS)}, got {reset!r}")
        self.reset = reset
        roles, label = _RESETS[reset]
        self.name = f"{reset}_cohort_starvation"
        self.roles_required = roles
        # gate request cycles on the system being on (a free-floating building isn't demanding)
        self.roles_optional: tuple[Role, ...] = _GATES
        if reset == "sat":
            # 0.106 (#186): the zone cooling loop adds G36's tier-1 SAT request (loop > 95 %)
            self.roles_optional = _GATES + (Role.ZONE_COOL_LOOP,)
        self._label = label
        self.groups = groups
        self.wants_topology = True
        self._kwargs: dict[str, Any] = {
            "cohort_frac": cohort_frac,
            "sustained_frac": sustained_frac,
            "min_active_cycles": min_active_cycles,
            "min_zones_per_group": min_zones_per_group,
            "min_present_frac": min_present_frac,
        }

    def _cols(self) -> dict:
        if self.reset == "sat":
            return {
                "temp_col": Role.SPACE_TEMP,
                "cool_sp_col": Role.COOL_SP,
                "cool_loop_col": Role.ZONE_COOL_LOOP,  # 0.106 (#186): SAT tier-1 requests
            }
        return {
            "flow_col": Role.AIRFLOW,
            "flow_sp_col": Role.AIRFLOW_SP,
            "damper_col": Role.DAMPER,
        }

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run the cohort-starvation census across the fleet's zone role-frames; one Finding.

        Scopes per air handler when a ``topology`` is available (semantic or the naming-heuristic
        :meth:`Registry.run_fleet` auto-builds), with a provenance/coverage-aware caveat; else pools
        building-wide.
        """
        if not frames:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                summary=f"no zones carrying {self._label} reset-request signals",
            )

        groups, grouping_caveats, provenance, n_ungrouped = resolve_grouping(
            self.groups, frames.keys(), topology
        )
        res = cohort_starvation(
            frames,
            reset=self.reset,
            groups=groups,
            **self._cols(),
            **self._kwargs,
            gate_cols=_GATES,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={"declined": True},
                summary=f"{self._label} cohort-starvation: too few usable request rows across "
                "the fleet",
                caveats=["cohort starvation not evaluated: no zone had enough usable request rows"],
            )

        caveats = list(res.caveats) + grouping_caveats
        metrics = {
            "reset": res.reset,
            "grouped": res.grouped,
            "grouping_provenance": provenance,
            "n_zones_ungrouped": n_ungrouped,
            "n_zones_evaluated": res.n_zones_evaluated,
            "n_groups": res.n_groups,
            "total_requests": res.total_requests,
            "starved_groups": res.starved_groups,
            "starved_detail": res.starved_detail,
            "worst_group": res.worst_group,
            "worst_group_frac": res.worst_group_frac,
            "group_sustained_frac": res.group_sustained_frac,
            "unevaluable_zones": res.unevaluable_zones,
            "group_skipped_cycles": res.group_skipped_cycles,
        }

        if res.starved_groups:
            # 0.106 (#186): name every starved cohort, worst first (not only the worst)
            ranked = sorted(
                res.starved_groups, key=lambda g: (-res.group_sustained_frac.get(g, 0.0), g)
            )
            parts = []
            for g in ranked:
                det = res.starved_detail.get(g, {})
                grp_txt = "the fleet" if g == "<fleet>" else g
                parts.append(
                    f"{grp_txt} ({det.get('n_zones', 0)} zones on "
                    f"{det.get('sustained_frac', 0.0):.0%} of active cycles)"
                )
            summary = (
                f"fleet: {len(ranked)} zone cohort(s) starved, their zones simultaneously "
                f"requesting the {self._label} reset -- {'; '.join(parts)}. "
                f"{_UPSTREAM[self.reset]}"
            )
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="warn",
                metrics=metrics,
                caveats=caveats,
                summary=summary,
            )

        inconclusive = (
            res.n_zones_evaluated == 0 or res.total_requests == 0 or bool(res.unevaluable_zones)
        )
        if inconclusive:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics=metrics,
                caveats=caveats,
                summary=(
                    f"{self._label} cohort-starvation inconclusive: "
                    f"{res.n_zones_evaluated} zone(s), {res.total_requests} requests"
                ),
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity="ok",
            metrics=metrics,
            caveats=caveats,
            summary=(
                f"fleet: {res.n_zones_evaluated} zones across {res.n_groups} group(s) show no "
                f"cohort-wide {self._label} starvation"
            ),
        )
