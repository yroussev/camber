"""Rule: AHU night/weekend setback (PNNL Re-tuning Ch.5).

Flags an air handler whose supply fan runs during unoccupied hours -- no effective
night/weekend setback, the cheapest large saver. Adapts
:func:`camber.setback.analyze_setback` to the role-frame interface.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..setback import analyze_setback
from .base import Finding

_ROLE_TO_COL = {
    Role.SUPPLY_FAN_STATUS: "SupplyFanStatus",
    Role.SUPPLY_FAN_SPEED: "SupplyFanSpeed",
    Role.OCCUPANCY: "Occupancy",
}


def _pct(v: float) -> str:
    """A runtime percentage with enough digits that a near-idle unit doesn't read as 0%."""
    return f"{v:.0f}%" if v >= 10 or v == 0 else f"{v:.2g}%"


class NightWeekendSetback:
    """Detects an AHU fan running unoccupied / missing night-weekend setback
    (PNNL Re-tuning Ch.5)."""

    name = "night_weekend_setback"
    # status preferred; speed is an acceptable substitute, so require neither
    # specifically -- gate on the pair via a custom check below.
    roles_required = ()
    roles_optional = (Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED, Role.OCCUPANCY)

    def __init__(
        self,
        *,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
        min_unoccupied_run_pct: float = 5.0,
    ):
        # The schedule is only an assumption: a trended OCCUPANCY point replaces it.
        # ``min_unoccupied_run_pct``: unoccupied runtime below this share of unoccupied time is
        # immaterial -- the rule only fires when the unit actually runs unoccupied (#57).
        self.min_unoccupied_run_pct = float(min_unoccupied_run_pct)
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if (
            Role.SUPPLY_FAN_STATUS not in frame.columns
            and Role.SUPPLY_FAN_SPEED not in frame.columns
        ):
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="no fan status or speed"
            )
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_setback(
            legacy,
            equip,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
            min_unoccupied_run_pct=self.min_unoccupied_run_pct,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        un = res.fan_run_unoccupied_pct
        ratio = res.unoccupied_to_occupied_ratio
        ratio_txt = f", ratio {ratio:.2f}" if ratio is not None else ""
        if not res.setback_effective:
            verdict = "MISSING/weak"
        elif un < self.min_unoccupied_run_pct:
            verdict = (
                f"effective (unoccupied runtime below the {self.min_unoccupied_run_pct:g}% "
                "materiality floor)"
            )
        else:
            verdict = "effective"
        # High unoccupied run = no setback. ok only if setback is effective.
        if res.setback_effective:
            severity = "ok"
        elif un >= 50.0:
            severity = "fault"
        else:
            severity = "warn"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "fan_run_unoccupied_pct": res.fan_run_unoccupied_pct,
                "fan_run_occupied_pct": res.fan_run_occupied_pct,
                "setback_effective": res.setback_effective,
                "n_unoccupied": res.n_unoccupied,
                "n_occupied": res.n_occupied,
                "unoccupied_to_occupied_ratio": res.unoccupied_to_occupied_ratio,
                "min_unoccupied_run_pct": res.min_unoccupied_run_pct,
            },
            caveats=[]
            if Role.OCCUPANCY in frame.columns
            else [
                f"no trended occupancy: unoccupied = outside the assumed schedule "
                f"({self.start_hour:g}-{self.end_hour:g}h, days {list(self.occupied_days)})"
            ],
            summary=(
                f"{equip}: supply fan runs {_pct(res.fan_run_unoccupied_pct)} of "
                f"unoccupied hours (vs {_pct(res.fan_run_occupied_pct)} occupied{ratio_txt}); "
                f"setback {verdict}"
            ),
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: a carpet of fan runtime — the fault *is* the schedule (night/weekend run)."""
        from ..charts.evidence import Evidence

        col = (
            Role.SUPPLY_FAN_SPEED
            if Role.SUPPLY_FAN_SPEED in frame.columns
            else Role.SUPPLY_FAN_STATUS
            if Role.SUPPLY_FAN_STATUS in frame.columns
            else None
        )
        if col is not None:
            return Evidence(renderer="carpet", roles=[col], title=f"{equip}: fan schedule")
        return None
