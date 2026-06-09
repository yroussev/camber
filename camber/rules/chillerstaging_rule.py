"""Rule: chiller staging / cycling (starts-per-day + low part-load; PNNL/ASHRAE).

Flags a chiller short-cycling (excess starts per day) and/or running persistently at
low part-load -- both signs of an oversized machine or mis-set staging thresholds,
costing efficiency and compressor life. Adapts
:func:`camber.chillerstaging.analyze_chiller_staging` to the role-frame interface.
``max_starts_per_day`` is the cycling threshold (manufacturer minimum-cycle-time
dependent), so it is a constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..chillerstaging import analyze_chiller_staging
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.POWER: "Power",
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}


class ChillerStaging:
    """Detects chiller short-cycling and/or sustained low part-load (PNNL Re-tuning Ch.8)."""

    name = "chiller_staging"
    roles_required = (Role.POWER,)
    roles_optional = (Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP, Role.CHW_FLOW)

    def __init__(self, max_starts_per_day: float = 6.0, low_load_pct: float = 40.0):
        # Cycling threshold is manufacturer/min-cycle-time dependent; low_load_pct is
        # the "idling" judgment for the optional load-factor metric.
        self.max_starts_per_day = max_starts_per_day
        self.low_load_pct = low_load_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chiller_staging(legacy, equip, min_load_pct=self.low_load_pct)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need chiller power)")
        cyc = res.starts_per_day
        low = res.low_load_pct if res.low_load_pct == res.low_load_pct else 0.0  # NaN-safe
        if cyc >= 2 * self.max_starts_per_day or low >= 50.0:
            severity = "fault"
        elif cyc >= self.max_starts_per_day or low >= 25.0:
            severity = "warn"
        else:
            severity = "ok"
        load_note = (f", load-factor median {res.load_factor_median_pct:.0f}% "
                     f"({res.low_load_pct:.0f}% of run hrs below {self.low_load_pct:.0f}%)"
                     if res.low_load_pct == res.low_load_pct else "")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "starts_per_day": res.starts_per_day,
                "max_starts_per_day": self.max_starts_per_day,
                "runtime_pct": res.runtime_pct,
                "load_factor_median_pct": res.load_factor_median_pct,
                "low_load_pct": res.low_load_pct,
                "n_days": res.n_days,
            },
            summary=(f"{equip}: {res.starts_per_day:.1f} starts/day "
                     f"(threshold {self.max_starts_per_day:.0f}), running "
                     f"{res.runtime_pct:.0f}% of the time{load_note}"),
        )
