"""Rule: outdoor-air-fraction / excess OA (PNNL Ch.5).

Flags an AHU pulling more outdoor air than its minimum while in cooling weather --
a direct cooling penalty in a hot climate. Adapts
:func:`camber.oafraction.analyze_oa_fraction` to the role-frame interface. OAT is
building-level and comes via the runner's ``shared`` channel.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..oafraction import analyze_oa_fraction
from .base import Finding

_ROLE_TO_COL = {
    Role.OAT: "OAT",
    Role.MIXED_AIR_TEMP: "MixedAir",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.OA_DAMPER: "OA_Damper",
}


class OutdoorAirFraction:
    """Detects excess (or insufficient) outdoor-air fraction at an AHU (PNNL Re-tuning Ch.5)."""

    name = "outdoor_air_fraction"
    roles_required = (Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP)
    roles_optional = (Role.OAT, Role.OA_DAMPER)

    def __init__(self, min_oa_pct: float = 20.0, cooling_cutoff_f: float = 70.0):
        # min OA is building-specific (sequence); cooling cutoff is climate-ish
        self.min_oa_pct = min_oa_pct
        self.cooling_cutoff_f = cooling_cutoff_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_oa_fraction(legacy, equip, min_oa_pct=self.min_oa_pct,
                                  cooling_cutoff_f=self.cooling_cutoff_f)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need OAT/MAT/RAT)")
        ex, mn = res.excess_oa_pct, res.min_oa_pct
        order = {"ok": 0, "warn": 1, "fault": 2}
        # excess-OA severity (energy penalty): share of cooling hours above the min
        sev_excess = "fault" if ex >= 50.0 else ("warn" if ex >= 20.0 else "ok")
        # under-ventilation severity (IAQ / code risk): from the MEDIAN OAF shortfall
        # below the minimum -- robust to noise near the floor, unlike a %-below count
        m = res.oaf_median_pct
        if m < mn * 0.5:
            sev_under = "fault"
        elif m < mn - 5.0:
            sev_under = "warn"
        else:
            sev_under = "ok"
        severity = max(sev_excess, sev_under, key=lambda s: order[s])
        if order[sev_under] > order[sev_excess]:
            tail = (f"under-ventilation: median OAF {m:.0f}% below the "
                    f"{mn:.0f}% min ({res.under_vent_pct:.0f}% of occupied hours low)")
        else:
            tail = (f"excess OA {ex:.0f}% of cooling hours above {mn:.0f}% min")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "oaf_median_pct": res.oaf_median_pct,
                "median_oaf_cooling": res.median_oaf_cooling,
                "excess_oa_pct": res.excess_oa_pct,
                "under_vent_pct": res.under_vent_pct,
                "min_oa_pct": res.min_oa_pct,
                "n_cooling": res.n_cooling,
                "n_valid": res.n_valid,
            },
            summary=(f"{equip}: OAF median {res.oaf_median_pct:.0f}% "
                     f"({res.median_oaf_cooling:.0f}% in cooling weather); {tail}"),
        )
