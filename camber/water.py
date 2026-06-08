"""Water consumption analysis: irrigation budget, cooling towers, leak detection.

The water counterpart to the energy diagnostics. Three method groups:

- **Irrigation water budget** -- how much landscape water is actually needed from
  reference evapotranspiration (ETo), a landscape coefficient, irrigation
  efficiency, and effective precipitation; compared to metered use to flag
  over/under-irrigation.
- **Cooling-tower makeup** -- evaporation + blowdown from cooling load and cycles
  of concentration, and the gallons-per-ton-hour efficiency metric.
- **Leak detection** -- minimum night flow, flow-duration, and the cost of a
  continuous leak.

Conversions: 1 inch over 1 ft2 = 0.623 gal; 1 CCF = 748 gal; 1 GPM = 1,440 gal/day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

GAL_PER_INCH_SF = 0.623
GAL_PER_CCF = 748.0
MIN_PER_DAY = 1440.0


# --------------------------------------------------------------------- irrigation
def effective_precip(rain_in: float, eto_in: float, fraction: float = 0.75) -> float:
    """Rain that actually offsets irrigation: ``min(rain*fraction, ETo)``."""
    return min(rain_in * fraction, eto_in)


def irrigation_budget_inches(eto_in: float, landscape_coeff: float,
                             efficiency: float, eff_precip_in: float = 0.0) -> float:
    """Required application depth (inches) = (ETo*KL - effective precip) / efficiency."""
    need = eto_in * landscape_coeff - eff_precip_in
    return max(0.0, need) / efficiency


def inches_to_gallons(inches: float, area_sf: float) -> float:
    """Convert inches of water over an area (ft2) to gallons."""
    return inches * area_sf * GAL_PER_INCH_SF


@dataclass(frozen=True)
class IrrigationBudget:
    """Required irrigation water (inches/gallons) vs metered use, with overage %."""

    required_inches: float
    required_gallons: float
    actual_gallons: float
    overage_pct: float       # (actual - budget) / budget * 100


def irrigation_budget(*, eto_in: float, area_sf: float, actual_gallons: float,
                      landscape_coeff: float = 0.7, efficiency: float = 0.70,
                      rain_in: float = 0.0) -> IrrigationBudget:
    """Monthly irrigation water budget vs metered use.

    Defaults: KL=0.7 (mixed landscape), efficiency=0.70 (spray). Overage beyond
    ~10-15% suggests leaks, over-runtime, or poor scheduling.
    """
    ep = effective_precip(rain_in, eto_in)
    inches = irrigation_budget_inches(eto_in, landscape_coeff, efficiency, ep)
    budget_gal = inches_to_gallons(inches, area_sf)
    over = ((actual_gallons - budget_gal) / budget_gal * 100.0) if budget_gal else float("nan")
    return IrrigationBudget(required_inches=round(inches, 3),
                            required_gallons=round(budget_gal, 1),
                            actual_gallons=round(actual_gallons, 1),
                            overage_pct=round(over, 1))


# ------------------------------------------------------------------ cooling tower
def evaporation_gpm(cooling_tons: float) -> float:
    """Approximate tower evaporation rate: ~3 GPM per 1000 tons of cooling."""
    return cooling_tons * 3.0 / 1000.0


def makeup_gpm(cooling_tons: float, cycles_of_concentration: float) -> float:
    """Total makeup = evaporation / (1 - 1/cycles). Higher cycles -> less makeup."""
    evap = evaporation_gpm(cooling_tons)
    return evap / (1.0 - 1.0 / cycles_of_concentration)


def gallons_per_ton_hour(makeup_gallons: float, ton_hours: float) -> float:
    """Tower water efficiency; typical 0.02-0.04 gal/ton-hr (higher = opportunity)."""
    return round(makeup_gallons / ton_hours, 5) if ton_hours else float("nan")


# ----------------------------------------------------------------- leak detection
def min_night_flow(flow_hourly: pd.Series, night=(2, 6)) -> float:
    """Minimum hourly flow during the overnight window (e.g. 2-6 AM).

    With irrigation off and the building unoccupied, this should approach zero;
    a persistent floor indicates a leak or continuous use.
    """
    idx = pd.DatetimeIndex(flow_hourly.dropna().index)
    night_mask = (idx.hour >= night[0]) & (idx.hour < night[1])
    night_vals = flow_hourly.dropna()[night_mask]
    return float(night_vals.min()) if len(night_vals) else float("nan")


def flow_duration(flow: pd.Series):
    """Flow-duration curve: values sorted descending (for the % time >= level plot).

    A facility with only intermittent uses should sit near zero most of the time;
    substantial flow most hours implies a continuous use or leak.
    """
    return np.sort(flow.dropna().to_numpy())[::-1]


@dataclass(frozen=True)
class LeakImpact:
    """Volume and cost of a continuous leak (per day, per month, per year)."""

    gpm: float
    gallons_per_day: float
    gallons_per_month: float
    cost_per_month: float
    cost_per_year: float


def leak_impact(gpm: float, *, rate_per_ccf: float = 12.0) -> LeakImpact:
    """Volume and cost of a continuous leak at ``gpm`` (combined water+sewer $/CCF)."""
    gpd = gpm * MIN_PER_DAY
    gpmonth = gpd * 30.0
    cost_month = gpmonth / GAL_PER_CCF * rate_per_ccf
    return LeakImpact(gpm=gpm, gallons_per_day=round(gpd, 1),
                      gallons_per_month=round(gpmonth, 1),
                      cost_per_month=round(cost_month, 2),
                      cost_per_year=round(cost_month * 12.0, 2))
