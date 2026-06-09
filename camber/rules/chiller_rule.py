"""Rule: chiller efficiency (kW/ton) vs an expected ceiling (PNNL Ch.8 / ASHRAE).

Flags a chiller running persistently above its design kW/ton at meaningful load --
the central-plant analogue of the air-side efficiency rules, and usually the largest
single electric load in the building. Adapts :func:`camber.chiller.analyze_chiller_efficiency`
to the role-frame interface. The expected efficiency is equipment-specific, so it is
a constructor parameter (set it to the chiller's type/design), not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..chiller import analyze_chiller_efficiency
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.POWER: "Power",
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}


class ChillerEfficiency:
    """Detects a chiller operating above its design kW/ton at load (PNNL Re-tuning Ch.8)."""

    name = "chiller_efficiency"
    roles_required = (Role.POWER, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP, Role.CHW_FLOW)
    roles_optional = ()

    def __init__(self, design_kw_per_ton: float = 0.85):
        # Equipment-specific ceiling: water-cooled centrifugal ~0.5-0.6, air-cooled
        # ~1.0-1.2. Confirm against the chiller schedule for the building.
        self.design_kw_per_ton = design_kw_per_ton

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chiller_efficiency(legacy, equip,
                                         design_kw_per_ton=self.design_kw_per_ton)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need power, CHW flow, supply/return temp)")
        ratio = res.kw_per_ton_median / res.design_kw_per_ton
        if ratio >= 1.5:
            severity = "fault"
        elif ratio >= 1.2:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "kw_per_ton_median": res.kw_per_ton_median,
                "design_kw_per_ton": res.design_kw_per_ton,
                "tons_median": res.tons_median,
                "load_factor_median_pct": res.load_factor_median_pct,
                "pct_hours_inefficient": res.pct_hours_inefficient,
                "n_running": res.n_running,
            },
            summary=(f"{equip}: chiller kW/ton median {res.kw_per_ton_median:.2f} "
                     f"vs design {res.design_kw_per_ton:.2f} "
                     f"({res.pct_hours_inefficient:.0f}% of loaded hours inefficient; "
                     f"median load {res.tons_median:.0f} tons)"),
        )
