"""Hourly / marginal Scope-2 carbon accounting.

`camber.carbon` computes annual CO₂e from fuel totals with a single average factor. Grid
electricity emissions actually vary hour to hour, so a building's *real* Scope-2 footprint depends
on **when** it uses power. This module accounts emissions against a time-varying grid factor series:

- **average (location-based) hourly** — interval load × the hourly average emissions factor;
- **marginal** — interval load × the hourly *marginal* factor (the emissions of the next kWh, what
  actually changes when the building shifts load), which is the right signal for load-shift value;
- the **timing premium** — how much more/less the building emits than a flat-operation baseline,
  because of when it runs.

Factors are user-supplied (from a grid-signal provider); no hard-coded grid data. numpy/pandas only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .timegrid import interval_hours as _interval_hours


def _align(load_kw: pd.Series, factor):
    f = (
        factor.reindex(load_kw.index)
        if isinstance(factor, pd.Series)
        else pd.Series(np.asarray(factor, dtype=float), index=load_kw.index)
    )
    df = pd.DataFrame({"l": load_kw, "f": f}).dropna()
    return df


@dataclass
class HourlyEmissions:
    """Emissions of an interval-load profile against a time-varying grid factor."""

    kwh: float
    co2e_kg: float  # Σ load·factor·Δt
    avg_factor: float  # time-average of the supplied factor
    effective_factor: float  # co2e / kwh — the load-weighted factor actually incurred
    timing_premium_pct: float  # effective vs a flat-operation baseline (+ = worse timing)

    def as_dict(self) -> dict:
        return asdict(self)


def hourly_emissions(
    load_kw: pd.Series, factor, *, unit_kg_per_kwh: bool = True
) -> HourlyEmissions:
    """Scope-2 emissions of ``load_kw`` (kW) against a time-varying ``factor`` (kgCO₂/kWh).

    The **effective factor** (co2e/kWh) is what the building actually incurred given its timing;
    comparing it to the plain time-average factor gives the **timing premium** — positive when the
    building runs disproportionately in dirty hours.
    """
    df = _align(load_kw, factor)
    if df.empty:
        return HourlyEmissions(0.0, 0.0, float("nan"), float("nan"), float("nan"))
    dt = _interval_hours(df.index)
    kwh = float((df["l"] * dt).sum())
    co2e = float((df["l"] * df["f"] * dt).sum())
    avg_f = float(df["f"].mean())
    eff = co2e / kwh if kwh else float("nan")
    premium = 100.0 * (eff - avg_f) / avg_f if avg_f else float("nan")
    if not unit_kg_per_kwh:  # factor given in g/kWh -> report kg throughout
        co2e /= 1000.0
        avg_f /= 1000.0  # keep avg_factor comparable to effective_factor
        eff = co2e / kwh if kwh else float("nan")
    return HourlyEmissions(
        kwh=round(kwh, 2),
        co2e_kg=round(co2e, 3),
        avg_factor=round(avg_f, 5),
        effective_factor=round(eff, 5),
        timing_premium_pct=round(premium, 2) if premium == premium else float("nan"),
    )


@dataclass
class MarginalComparison:
    """Average- vs marginal-emissions accounting for the same load."""

    co2e_avg_kg: float  # against the average (location-based) factor
    co2e_marginal_kg: float  # against the marginal factor
    marginal_over_avg: float  # ratio (a marginal signal usually differs from average)

    def as_dict(self) -> dict:
        return asdict(self)


def marginal_vs_average(load_kw: pd.Series, avg_factor, marginal_factor) -> MarginalComparison:
    """Compare emissions under average vs marginal grid factors.

    Load-shift *value* should use the **marginal** factor (what changes when you move a kWh), while
    compliance/reporting typically uses the **average** (location-based) factor. Reporting both
    makes the gap explicit.
    """
    a = hourly_emissions(load_kw, avg_factor)
    m = hourly_emissions(load_kw, marginal_factor)
    ratio = m.co2e_kg / a.co2e_kg if a.co2e_kg else float("nan")
    return MarginalComparison(
        co2e_avg_kg=a.co2e_kg,
        co2e_marginal_kg=m.co2e_kg,
        marginal_over_avg=round(ratio, 3) if ratio == ratio else float("nan"),
    )
