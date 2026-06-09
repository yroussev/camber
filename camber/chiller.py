"""Chiller efficiency diagnostic: measured kW/ton vs an expected ceiling.

Cooling output and electrical input are both metered, so the chiller's operating
efficiency can be computed directly rather than inferred:

    tons   = gpm * (CHWR - CHWS) / 24          # 500 * dT * gpm / 12000 BTU/ton
    kW/ton = chiller_kW / tons

A chiller running persistently far above its design kW/ton at meaningful load is
burning excess electricity for the same cooling -- fouled tubes, low refrigerant
charge, high condenser-water/lift, failed staging, or simply an oversized machine
short-cycling. This is the central-plant analogue of the air-side efficiency rules,
and on most buildings the plant is where the largest kWh actually hides (PNNL
Building Re-tuning Ch.8; ASHRAE chiller-plant guidance).

The expected efficiency is **equipment-specific** -- a water-cooled centrifugal
(~0.5-0.6 kW/ton design) and an air-cooled screw (~1.0-1.2) are judged against very
different ceilings -- so ``design_kw_per_ton`` is an injected parameter, not a baked
constant. kW/ton is unstable at trivial load (tons -> 0 makes the ratio explode), so
intervals below a minimum load and with the chiller off are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd


@dataclass
class ChillerEfficiencyResult:
    """Measured chiller kW/ton over loaded, running hours vs the design ceiling."""

    equip: str
    n_running: int                # intervals chiller on and above min load
    kw_per_ton_median: float      # median kW/ton over running hours
    tons_median: float            # median cooling output (tons) over running hours
    load_factor_median_pct: float  # median load as % of observed peak tons
    pct_hours_inefficient: float  # % running hrs above design * (1 + margin)
    design_kw_per_ton: float      # the expected ceiling used (equipment-specific)
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_chiller_efficiency(
    df: pd.DataFrame,
    equip: str,
    *,
    design_kw_per_ton: float = 0.85,   # expected ceiling -- SET to the chiller type/design
    inefficient_margin: float = 0.15,  # kW/ton above design*(1+margin) == inefficient
    min_load_tons: float = 5.0,        # below this, kW/ton is too noisy to trust
    min_power_kw: float = 2.0,         # below this the chiller is effectively off
    min_dt_f: float = 0.5,             # need a real loop dT to be making cooling
) -> ChillerEfficiencyResult | None:
    """Compute chiller kW/ton from metered power, CHW flow, and loop dT.

    Expects legacy columns ``Power`` (kW), ``CHWS_Temp``/``CHWR_Temp`` (F), and
    ``CHW_Flow`` (gpm); the rule wrapper maps roles to these. ``design_kw_per_ton``
    is the equipment-specific ceiling (confirm against the chiller schedule); the
    load/power/dT floors are stability guards, not efficiency judgments.
    """
    need = ("Power", "CHWS_Temp", "CHWR_Temp", "CHW_Flow")
    if any(c not in df.columns for c in need):
        return None
    w = df[list(need)].dropna()
    # plausibility guards (drop sensor dropouts / impossible values)
    w = w[(w.Power >= 0) & (w.CHWS_Temp.between(35, 60)) &
          (w.CHWR_Temp.between(38, 80)) & (w.CHW_Flow >= 0)]
    dt = w.CHWR_Temp - w.CHWS_Temp
    tons = w.CHW_Flow * dt / 24.0
    # "running and loaded": chiller drawing power and producing real cooling
    run = (w.Power >= min_power_kw) & (dt >= min_dt_f) & (tons >= min_load_tons)
    w, tons = w[run], tons[run]
    if len(w) < 10:
        return None

    kw_per_ton = (w.Power / tons)
    kw_per_ton = kw_per_ton[(kw_per_ton > 0) & (kw_per_ton < 5)]  # physical-ish range
    if len(kw_per_ton) < 10:
        return None
    tons = tons.reindex(kw_per_ton.index)

    peak_tons = float(tons.max())
    inefficient = float((kw_per_ton > design_kw_per_ton * (1 + inefficient_margin)).mean())

    return ChillerEfficiencyResult(
        equip=equip,
        n_running=int(len(kw_per_ton)),
        kw_per_ton_median=round(float(kw_per_ton.median()), 3),
        tons_median=round(float(tons.median()), 1),
        load_factor_median_pct=round(100.0 * float(tons.median()) / peak_tons, 1)
        if peak_tons > 0 else float("nan"),
        pct_hours_inefficient=round(100.0 * inefficient, 1),
        design_kw_per_ton=float(design_kw_per_ton),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
