"""Building Performance Standards (BPS) compliance: limit checks, margin, penalty.

A growing number of jurisdictions impose **building performance standards** -- a
cap on a building's annual energy-use intensity (EUI) or greenhouse-gas emissions
intensity, enforced with an over-the-limit penalty. New York City's Local Law 97
(NYC Administrative Code Title 28, Article 320) is the motivating example: it sets
emissions-intensity limits per occupancy group and assesses a civil penalty for
each metric ton of CO2-equivalent over the limit. Other programs (Washington
State Clean Buildings, Boston BERDO, the federal/voluntary "national definition
of a zero-emissions building") follow the same shape: a metric, a limit, and a
cost for exceeding it.

This module is deliberately **jurisdiction-neutral**: it hard-codes no legal
limit or penalty rate. The caller supplies the limit and an optional generic
penalty (dollars per unit over) via :class:`BPSStandard`; this module computes the
compliance margin, percent of limit, over-amount, and resulting penalty. It also
provides :func:`emissions_intensity`, which converts a per-fuel energy breakdown
into an annual emissions intensity using caller-supplied emission factors (e.g.
EPA eGRID for electricity, EPA GHG factors for combustion fuels) so the result
can be checked against an emissions-type standard.

stdlib only -- no numpy/pandas needed for these scalar computations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict


@dataclass
class BPSStandard:
    """A building performance standard: a metric, its limit, and an over-penalty.

    ``metric`` is ``"eui"`` (energy-use intensity) or ``"emissions"`` (emissions
    intensity); both are evaluated as "lower is better" against ``limit`` in the
    given ``unit``. ``penalty_per_unit_over`` is a generic cost (e.g. dollars) per
    unit by which the value exceeds the limit -- supply the jurisdiction's rate, or
    leave at 0 to skip penalty pricing.
    """

    name: str
    metric: str                          # "eui" | "emissions"
    limit: float
    unit: str = ""
    penalty_per_unit_over: float = 0.0

    def as_dict(self) -> dict:
        """Return the standard as a plain dict."""
        return asdict(self)


@dataclass
class BPSResult:
    """Compliance of a single value against a :class:`BPSStandard`."""

    standard_name: str
    metric: str
    value: float
    limit: float
    unit: str
    compliant: bool
    margin: float            # limit - value (positive = headroom)
    pct_of_limit: float      # 100 * value / limit
    over_amount: float       # max(0, value - limit)
    penalty: float           # over_amount * penalty_per_unit_over
    verdict: str             # "compliant" | "over"

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def assess_bps(value: float, standard: BPSStandard) -> BPSResult | None:
    """Assess a measured ``value`` against a BPS ``standard`` (lower is better).

    Returns ``None`` if the inputs are unusable (non-finite value, or a limit that
    is non-finite or not positive -- a limit must be a positive cap to be
    meaningful). Otherwise returns a :class:`BPSResult` with the compliance margin
    (``limit - value``), the value as a percent of the limit, the over-amount, and
    the resulting penalty.
    """
    v = float(value)
    lim = float(standard.limit)
    if not math.isfinite(v) or not math.isfinite(lim) or lim <= 0:
        return None
    over = max(0.0, v - lim)
    compliant = v <= lim
    penalty = over * float(standard.penalty_per_unit_over)
    return BPSResult(
        standard_name=standard.name,
        metric=standard.metric,
        value=round(v, 4),
        limit=round(lim, 4),
        unit=standard.unit,
        compliant=compliant,
        margin=round(lim - v, 4),
        pct_of_limit=round(100.0 * v / lim, 2),
        over_amount=round(over, 4),
        penalty=round(penalty, 2),
        verdict="compliant" if compliant else "over",
    )


def emissions_intensity(energy_by_fuel: dict, factors: dict,
                        area_sqft: float) -> float:
    """Annual emissions intensity (kgCO2e per sqft) from per-fuel energy.

    ``energy_by_fuel`` maps a fuel key (e.g. ``"electricity"``, ``"natural_gas"``)
    to that fuel's annual energy in the unit the matching emission factor expects.
    ``factors`` maps the same keys to a caller-supplied emission factor in kgCO2e
    per energy unit (e.g. EPA eGRID for grid electricity, EPA GHG factors for
    combustion fuels). Fuels present in ``energy_by_fuel`` but missing from
    ``factors`` contribute zero. Returns ``nan`` if ``area_sqft`` is not positive.
    """
    if not math.isfinite(area_sqft) or area_sqft <= 0:
        return float("nan")
    total = 0.0
    for fuel, energy in energy_by_fuel.items():
        factor = factors.get(fuel, 0.0)
        total += float(energy) * float(factor)
    return total / float(area_sqft)
