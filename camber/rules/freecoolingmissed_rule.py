"""Rule: mechanical cooling run while free cooling was available.

When it's cool enough outside to cool for free, running the compressor/chiller is pure waste. This
detects it directly — mechanical cooling active while OAT is below the economizer high limit — the
rule companion to the `camber.freecooling` opportunity quantifier. numpy/pandas.

``active`` is a cooling-valve position in **percent** (the role pipeline scales position roles to
0-100 %; a 0-1 source is rescaled here too), so a valve parked at 1 % is not "mechanical cooling
running". Durations are reported in hours from the trend's own sampling interval, not as sample
counts.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding


def _step_hours(index) -> float | None:
    """Median positive sample spacing in hours (``None`` if it can't be determined)."""
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    steps = pd.Series(index.sort_values()).diff().dropna()
    steps = steps[steps > pd.Timedelta(0)]
    return None if steps.empty else steps.median().total_seconds() / 3600.0


class FreeCoolingMissed:
    """Flags mechanical cooling running while outdoor air was cool enough for free cooling."""

    name = "free_cooling_missed"
    roles_required = (Role.COOL_VALVE, Role.OAT)
    roles_optional = ()

    def __init__(
        self,
        *,
        high_limit_f: float = 60.0,
        active: float = 5.0,  # cooling-valve % above which mechanical cooling is running
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
    ):
        self.high_limit_f = high_limit_f
        self.active = active
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat, cool = frame[Role.OAT], normalize_percent(frame[Role.COOL_VALVE])
        available = (oat < self.high_limit_f) & oat.notna() & cool.notna()
        n_avail = int(available.sum())
        step_h = _step_hours(frame.index)
        if n_avail == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no free-cooling weather in the window",
            )
        missed = available & (cool > self.active)
        pct = 100.0 * float(missed.sum()) / n_avail
        hours = None if step_h is None else round(n_avail * step_h, 1)
        span = (
            f"{hours:g} free-cooling hours"
            if hours is not None
            else f"{n_avail} free-cooling samples"
        )
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "missed_pct": round(pct, 2),
                "high_limit_f": self.high_limit_f,
                "active_pct": self.active,
                "n_free_cooling_samples": n_avail,
                "n_free_cooling_hours": hours,
            },
            summary=(
                f"{equip}: mechanical cooling (valve > {self.active:g}%) ran {pct:.0f}% of the "
                f"{span} (OAT < {self.high_limit_f:g}°F)"
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
