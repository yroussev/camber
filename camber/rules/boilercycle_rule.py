"""Rule: boiler short-cycling (firing starts per day; PNNL Ch.8 / boiler guidance).

Flags a boiler firing in short bursts -- too many starts per day -- which wastes
purge-cycle heat, stresses the boiler, and lowers seasonal efficiency; usually an
oversized boiler or too-tight staging hysteresis. Adapts
:func:`camber.boilercycle.analyze_boiler_cycling` to the role-frame interface.
``max_starts_per_day`` is manufacturer/min-cycle-time dependent, so it is a
constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..boilercycle import analyze_boiler_cycling
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {Role.BOILER_STATUS: "BoilerStatus"}


class BoilerShortCycle:
    """Detects a boiler short-cycling (excess firing starts per day) (PNNL Re-tuning Ch.8)."""

    name = "boiler_short_cycle"
    roles_required = (Role.BOILER_STATUS,)
    roles_optional = ()

    def __init__(self, max_starts_per_day: float = 6.0):
        # Manufacturer/min-cycle-time dependent; confirm against the boiler's controls.
        self.max_starts_per_day = max_starts_per_day

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_boiler_cycling(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need boiler status)")
        cyc = res.starts_per_day
        if cyc >= 2 * self.max_starts_per_day:
            severity = "fault"
        elif cyc >= self.max_starts_per_day:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "starts_per_day": res.starts_per_day,
                "max_starts_per_day": self.max_starts_per_day,
                "runtime_pct": res.runtime_pct,
                "n_starts": res.n_starts,
                "n_days": res.n_days,
            },
            summary=(f"{equip}: {res.starts_per_day:.1f} boiler starts/day "
                     f"(threshold {self.max_starts_per_day:.0f}), firing "
                     f"{res.runtime_pct:.0f}% of the time"),
        )
