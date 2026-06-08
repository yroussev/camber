"""Utility cost accounting for energy and water.

Energy bills combine a consumption charge, a demand (peak-power) charge, a fixed
service charge, and sometimes time-of-use rates. Water bills add tiered (block)
volume rates, and a wastewater/sewer charge that often applies only to indoor use
(outdoor irrigation generates supply charges but no sewer charge). Conservation
paybacks should use the *marginal* rate (the next unit avoided), not the average.

Currency-agnostic; rates are in whatever $/unit the bill uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnergyRate:
    """An electricity/gas tariff."""

    energy_rate: float            # $ per unit (kWh, therm)
    demand_rate: float = 0.0      # $ per peak kW
    fixed: float = 0.0            # $ per period
    tou: dict = field(default_factory=dict)  # {hour: rate} overrides energy_rate


def energy_cost(energy: float, *, rate: EnergyRate, peak_demand: float = 0.0) -> float:
    """Total energy bill = consumption + demand + fixed."""
    return round(energy * rate.energy_rate
                 + peak_demand * rate.demand_rate + rate.fixed, 4)


def tou_energy_cost(hourly_energy, hourly_hour, rate: EnergyRate) -> float:
    """Consumption cost when time-of-use rates apply (per-hour energy + hour 0-23)."""
    total = 0.0
    for e, h in zip(hourly_energy, hourly_hour):
        total += float(e) * rate.tou.get(int(h), rate.energy_rate)
    return round(total, 4)


def tiered_cost(volume: float, tiers) -> float:
    """Piecewise (block) rate cost. ``tiers`` = [(upper_limit, rate), ...] with the
    last tier's limit None/inf for the top block. Volume above a tier spills up."""
    cost = 0.0
    lower = 0.0
    for limit, rate in tiers:
        cap = float("inf") if limit is None else limit
        vol_in_tier = max(0.0, min(volume, cap) - lower)
        cost += vol_in_tier * rate
        lower = cap
        if volume <= cap:
            break
    return round(cost, 4)


def marginal_rate(volume: float, tiers) -> float:
    """The block rate the next unit at ``volume`` would be billed at."""
    lower = 0.0
    for limit, rate in tiers:
        cap = float("inf") if limit is None else limit
        if volume < cap:
            return rate
        lower = cap
    return tiers[-1][1]


@dataclass(frozen=True)
class WaterBill:
    """Itemized water/wastewater bill with average and marginal unit costs."""

    supply: float
    wastewater: float
    fixed: float
    total: float
    avg_cost_per_unit: float
    marginal_cost_per_unit: float


def water_cost(volume: float, *, supply_tiers, sewer_tiers=None,
               indoor_fraction: float = 1.0, fixed: float = 0.0) -> WaterBill:
    """Water + wastewater bill.

    Supply charges apply to all metered ``volume``; wastewater charges apply only
    to the ``indoor_fraction`` (outdoor irrigation discharges no sewage). Marginal
    cost = supply marginal + sewer marginal (on the indoor portion), the right
    number for sizing conservation paybacks.
    """
    supply = tiered_cost(volume, supply_tiers)
    sewer = 0.0
    if sewer_tiers:
        sewer = tiered_cost(volume * indoor_fraction, sewer_tiers)
    total = round(supply + sewer + fixed, 4)
    avg = round(total / volume, 6) if volume else float("nan")
    marg = marginal_rate(volume, supply_tiers)
    if sewer_tiers and indoor_fraction > 0:
        marg += marginal_rate(volume * indoor_fraction, sewer_tiers) * indoor_fraction
    return WaterBill(supply=supply, wastewater=round(sewer, 4), fixed=fixed,
                     total=total, avg_cost_per_unit=avg,
                     marginal_cost_per_unit=round(marg, 6))
