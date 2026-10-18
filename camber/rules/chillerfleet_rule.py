"""Fleet rule: multi-chiller staging (over-staging census; PNNL Ch.8 / ASHRAE).

Looks across all chillers at once: how often does the plant run more machines than
the load needs? Running two chillers at part-load when one could carry the whole load
wastes energy (each machine sits low on its efficiency curve and pumps/auxiliaries
double up). Adapts :func:`camber.chillerstaging.analyze_chiller_staging_fleet` to the
fleet role-frame interface. This is the plant-level companion to the single-chiller
``chiller_staging`` cycling/idling rule.

0.106 (#183): only chillers enter the fleet. A chilled-water loop (``CHW_LOOP``, ``SECLOOP``:
:func:`camber.model.equipclass.is_chw_loop`) is in the chilled-water family and often carries a
``power`` point, but that is its pumps' power, so it is left out (counted in
``n_loops_excluded``). A declared ``capacity_kw`` replaces the observed 95th-percentile capacity,
which reads low on a plant that never loads a chiller fully. The counts are samples
(``n_running_samples``, ``n_multi_samples``) with their hours beside them.

0.108 (#235): a chiller reads as running by its run status / command (``compressor_status``) when
mapped, else by power above ``run_frac`` of its own 95th-percentile draw, else above an explicit
``min_power_kw`` (before 0.108, a fixed 2 kW that read standby draw as running).
"""

from __future__ import annotations

from ..chillerstaging import analyze_chiller_staging_fleet
from ..model.equipclass import is_chw_loop
from ..model.roles import Role
from ..schedules import CHILLER_POWER_RUN_FRAC
from .base import Finding


class ChillerStagingFleet:
    """Census of chiller staging across the plant to flag over-staging (PNNL Re-tuning Ch.8)."""

    name = "chiller_staging_fleet"
    roles_required = (Role.POWER,)
    roles_optional = (Role.COMPRESSOR_STATUS,)  # 0.108 (#235): the run gate

    def __init__(
        self,
        redundancy_ceiling: float = 0.9,
        capacity_kw=None,
        min_power_kw: float | None = None,
        run_frac: float = CHILLER_POWER_RUN_FRAC,
    ):
        # How fully a single chiller can be loaded before the next must stage on.
        self.redundancy_ceiling = redundancy_ceiling
        # 0.106 (#183): each chiller's full-load power, kW -- one number for equal chillers or
        # {equip: kW}; None reads each chiller's observed 95th-percentile power instead
        if capacity_kw is not None and not isinstance(capacity_kw, dict):
            capacity_kw = float(capacity_kw)
            if not capacity_kw > 0:
                raise ValueError(f"capacity_kw must be positive, got {capacity_kw!r}")
        self.capacity_kw = capacity_kw
        # 0.108 (#235): the power run gate -- an explicit kW floor, else run_frac x p95 power
        self.min_power_kw = min_power_kw
        self.run_frac = run_frac

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run across the chillers' role-frames; return one aggregate Finding."""
        loops = sorted(
            e for e, f in frames.items() if is_chw_loop(f.attrs.get("camber_equip_class"))
        )
        chillers = {e: f for e, f in frames.items() if e not in loops}
        caveats = []
        if loops:
            caveats.append(
                f"{len(loops)} chilled-water loop(s) left out of the fleet ({_names(loops)}): a "
                "loop's power point is its pumps', not a chiller's"
            )
        if not chillers:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={"n_loops_excluded": len(loops)},
                summary="no chillers with power points",
                caveats=caveats,
            )
        legacy = {
            e: f.rename(columns={Role.POWER: "Power", Role.COMPRESSOR_STATUS: "Status"})
            for e, f in chillers.items()
        }
        res = analyze_chiller_staging_fleet(
            legacy,
            redundancy_ceiling=self.redundancy_ceiling,
            capacity_kw=self.capacity_kw,
            min_power_kw=self.min_power_kw,
            run_frac=self.run_frac,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={"n_loops_excluded": len(loops)},
                summary="need >= 2 chillers with power data to assess staging",
                caveats=caveats,
            )
        if res.capacity_source != "declared":
            caveats.append(
                f"chiller capacity ({res.rep_capacity_kw:g} kW) read from each chiller's own "
                "95th-percentile power, not declared: a plant that never runs a chiller near full "
                "load (an over-staged one) reads a low capacity and under-states its over-staging "
                "-- declare the chillers' full-load power (capacity_kw)"
            )
        pct = res.pct_overstaged
        if pct >= 50.0:
            severity = "fault"
        elif pct >= 20.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=severity,
            metrics={
                "n_chillers": res.n_chillers,
                "n_loops_excluded": len(loops),
                "n_running_samples": res.n_running_samples,
                "n_multi_samples": res.n_multi_samples,
                "running_hours": res.running_hours,
                "multi_hours": res.multi_hours,
                "pct_overstaged": res.pct_overstaged,
                "median_running_count": res.median_running_count,
                "rep_capacity_kw": res.rep_capacity_kw,
                "capacity_source": res.capacity_source,
                "run_source": res.run_source,  # 0.108 (#235)
            },
            summary=(
                f"fleet: {res.n_chillers} chillers, "
                f"{res.pct_overstaged:.0f}% of {res.n_multi_samples} multi-chiller samples "
                f"({res.multi_hours:,.0f} h) over-staged (median "
                f"{res.median_running_count:.0f} running, capacity {res.rep_capacity_kw:g} kW "
                f"{'declared' if res.capacity_source == 'declared' else 'observed'}); a "
                "redundant chiller could stage off"
            ),
            caveats=caveats,
        )


def _names(equips: list, limit: int = 5) -> str:
    """``a, b, c`` -- at most ``limit`` names, then ``and N more``."""
    head = ", ".join(equips[:limit])
    return head if len(equips) <= limit else f"{head} and {len(equips) - limit} more"
