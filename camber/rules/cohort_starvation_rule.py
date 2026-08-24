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
    ):
        if reset not in _RESETS:
            raise ValueError(f"reset must be one of {sorted(_RESETS)}, got {reset!r}")
        self.reset = reset
        roles, label = _RESETS[reset]
        self.name = f"{reset}_cohort_starvation"
        self.roles_required = roles
        self.roles_optional: tuple[Role, ...] = ()
        self._label = label
        self.groups = groups
        self.wants_topology = True
        self._kwargs: dict[str, Any] = {
            "cohort_frac": cohort_frac,
            "sustained_frac": sustained_frac,
            "min_active_cycles": min_active_cycles,
            "min_zones_per_group": min_zones_per_group,
        }

    def _cols(self) -> dict:
        if self.reset == "sat":
            return {"temp_col": Role.SPACE_TEMP, "cool_sp_col": Role.COOL_SP}
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
        }

        if res.starved_groups:
            worst = res.worst_group
            det = res.starved_detail.get(worst, {})
            n_z = det.get("n_zones", 0)
            frac = res.worst_group_frac or 0.0
            grp_txt = "the fleet" if worst == "<fleet>" else worst
            summary = (
                f"fleet: {grp_txt}'s zone cohort is starved -- {n_z} zones simultaneously request "
                f"the {self._label} reset on {frac:.0%} of active cycles "
                f"({len(res.starved_groups)} cohort(s)). {_UPSTREAM[self.reset]}"
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
