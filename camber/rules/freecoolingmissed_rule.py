"""Rule: mechanical cooling run while free cooling was available.

When it's cool enough outside to cool for free, running the compressor/chiller is pure waste. This
detects it directly — mechanical cooling active while OAT is below the economizer high limit — the
rule companion to the `camber.freecooling` opportunity quantifier. numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding


class FreeCoolingMissed:
    """Flags mechanical cooling running while outdoor air was cool enough for free cooling."""

    name = "free_cooling_missed"
    roles_required = (Role.COOL_VALVE, Role.OAT)
    roles_optional = ()

    def __init__(
        self,
        *,
        high_limit_f: float = 60.0,
        active: float = 0.05,
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
    ):
        self.high_limit_f = high_limit_f
        self.active = active
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat, cool = frame[Role.OAT], frame[Role.COOL_VALVE]
        available = (oat < self.high_limit_f) & oat.notna() & cool.notna()
        n_avail = int(available.sum())
        if n_avail == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no free-cooling weather in the window",
            )
        missed = available & (cool > self.active)
        pct = 100.0 * float(missed.sum()) / n_avail
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "missed_pct": round(pct, 2),
                "high_limit_f": self.high_limit_f,
                "n_free_cooling_hours": n_avail,
            },
            summary=(
                f"{equip}: mechanical cooling ran {pct:.0f}% of the "
                f"{n_avail} free-cooling hours (OAT < {self.high_limit_f:g}°F)"
            ),
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: cooling-valve position vs OAT — cooling at low OAT stands out."""
        from ..charts.evidence import Evidence

        return Evidence(
            renderer="oat_scatter",
            roles=[Role.COOL_VALVE],
            title=f"{equip}: cooling vs OAT (free-cooling)",
        )
