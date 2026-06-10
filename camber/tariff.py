"""Utility tariff engine: bill an interval load against a URDB-shaped rate.

A native, dependency-free engine for the tariff structures that cover the large
majority of real electricity bills: a fixed monthly charge, time-of-use (TOU) energy
with tiered/block rates, TOU and/or flat monthly demand charges, and a demand ratchet.

The :class:`Tariff` model deliberately mirrors the OpenEI **Utility Rate Database
(URDB)** shape -- period rate structures plus 12x24 weekday/weekend schedules -- so a
URDB rate maps onto it directly (:mod:`camber.interop.openei`). For exotic URDB rates
(coincident/ratcheted-by-season demand, deeply nested look-back tiers) bridge to NREL
PySAM's battle-tested ``UtilityRate5`` via the optional ``[tariff]`` extra
(:mod:`camber.interop.tariff_nrel`). This engine handles the common cases with no
dependency; the bridge handles full fidelity. Currency-agnostic ($/unit as billed).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

# A "tier" is (upper_bound, rate): covers consumption from the previous tier's bound up
# to this one at ``rate``; an upper_bound of None means "no limit" (the top tier).
Tier = tuple


@dataclass
class Tariff:
    """A URDB-shaped electricity tariff (period rate structures + 12x24 schedules)."""

    name: str = ""
    fixed_monthly: float = 0.0
    # energy: one tier-list per period; schedules index periods by [month0-11][hour0-23]
    energy_rates: list = field(default_factory=lambda: [[(None, 0.0)]])
    energy_weekday: list | None = None   # 12x24 period indices; None -> all period 0
    energy_weekend: list | None = None
    # TOU demand (optional): one tier-list per period + its own schedules
    demand_rates: list = field(default_factory=list)
    demand_weekday: list | None = None
    demand_weekend: list | None = None
    # flat (non-TOU) monthly demand (optional): tier-lists per period + month->period map
    flat_demand_rates: list = field(default_factory=list)
    flat_demand_months: list | None = None   # 12 period indices; None -> no flat demand
    ratchet_pct: float = 0.0             # billed demand >= pct% of the trailing peak

    def as_dict(self) -> dict:
        """Return the tariff as a plain dict (JSON friendly)."""
        return asdict(self)


@dataclass
class BillResult:
    """A computed utility bill: per-month rows plus annual component totals."""

    months: list                  # [{period, kwh, peak_kw, energy, demand, fixed, total}]
    energy_charge: float
    demand_charge: float
    fixed_charge: float
    total: float
    n_months: int

    def as_dict(self) -> dict:
        """Return the bill as a plain dict."""
        return asdict(self)


def _sched(arr) -> np.ndarray:
    """A 12x24 period-index grid; None -> all-zero (a single period)."""
    return np.zeros((12, 24), dtype=int) if arr is None else np.asarray(arr, dtype=int)


def _tiered(qty: float, tiers: list) -> float:
    """Block-rate charge for ``qty`` over cumulative-bound ``tiers`` [(upper|None, rate)]."""
    cost, prev = 0.0, 0.0
    for upper, rate in tiers:
        cap = qty if upper is None else min(qty, float(upper))
        if cap > prev:
            cost += (cap - prev) * rate
            prev = cap
        if upper is not None and qty <= upper:
            break
    return cost


def _interval_hours(index: pd.DatetimeIndex) -> float:
    """Hours per interval, from the median timestamp spacing (default 1.0)."""
    if len(index) < 2:
        return 1.0
    secs = float(pd.Series(index).diff().dt.total_seconds().median())
    return secs / 3600.0 if secs and secs == secs else 1.0


def compute_bill(tariff: Tariff, load_kw: pd.Series) -> BillResult:
    """Bill an interval ``load_kw`` (average kW per interval) against ``tariff``.

    Energy per interval is ``kW * interval-hours`` (interval inferred from the index).
    Energy is summed per TOU period per month and charged through that period's tiers;
    demand is the per-period (TOU) or monthly (flat) peak kW, with an optional ratchet
    on the flat-demand peak. Months are billing months in the data.
    """
    s = load_kw.dropna()
    if s.empty:
        return BillResult([], 0.0, 0.0, 0.0, 0.0, 0)
    dt = _interval_hours(s.index)
    idx = s.index
    kw = np.asarray(s.values, dtype=float)
    kwh = kw * dt
    month = np.asarray(idx.month)
    hour = np.asarray(idx.hour)
    wknd = np.asarray(idx.dayofweek) >= 5
    ym = np.asarray(idx.year) * 100 + month

    ewd, ewe = _sched(tariff.energy_weekday), _sched(tariff.energy_weekend)
    e_period = np.where(wknd, ewe[month - 1, hour], ewd[month - 1, hour])
    use_tou_demand = bool(tariff.demand_rates)
    if use_tou_demand:
        dwd, dwe = _sched(tariff.demand_weekday), _sched(tariff.demand_weekend)
        d_period = np.where(wknd, dwe[month - 1, hour], dwd[month - 1, hour])
    use_flat_demand = bool(tariff.flat_demand_rates) and tariff.flat_demand_months is not None

    rows, peaks = [], []
    e_tot = d_tot = f_tot = 0.0
    for key in sorted(set(ym.tolist())):
        m = ym == key
        mon = int(key % 100)
        # --- energy: sum kWh per TOU period, charge through that period's tiers ---
        e = 0.0
        for p in set(e_period[m].tolist()):
            e += _tiered(float(kwh[m][e_period[m] == p].sum()), tariff.energy_rates[p])
        # --- demand ---
        d = 0.0
        peak = float(kw[m].max())
        if use_tou_demand:
            for p in set(d_period[m].tolist()):
                pk = float(kw[m][d_period[m] == p].max())
                d += _tiered(pk, tariff.demand_rates[p])
        if use_flat_demand:
            billed = peak
            if tariff.ratchet_pct > 0 and peaks:
                billed = max(peak, tariff.ratchet_pct / 100.0 * max(peaks))
            fp = tariff.flat_demand_months[mon - 1]
            d += _tiered(billed, tariff.flat_demand_rates[fp])
        peaks.append(peak)
        f = tariff.fixed_monthly
        rows.append({"period": f"{key // 100:04d}-{mon:02d}",
                     "kwh": round(float(kwh[m].sum()), 2), "peak_kw": round(peak, 2),
                     "energy": round(e, 2), "demand": round(d, 2), "fixed": round(f, 2),
                     "total": round(e + d + f, 2)})
        e_tot += e
        d_tot += d
        f_tot += f

    return BillResult(months=rows, energy_charge=round(e_tot, 2),
                      demand_charge=round(d_tot, 2), fixed_charge=round(f_tot, 2),
                      total=round(e_tot + d_tot + f_tot, 2), n_months=len(rows))


# --- convenience constructors ------------------------------------------------- #

def flat_tariff(energy_rate: float, *, demand_rate: float = 0.0,
                fixed_monthly: float = 0.0) -> Tariff:
    """A flat tariff: one energy rate, optional flat monthly demand, fixed charge."""
    return Tariff(
        name="flat",
        fixed_monthly=fixed_monthly,
        energy_rates=[[(None, energy_rate)]],
        flat_demand_rates=[[(None, demand_rate)]] if demand_rate else [],
        flat_demand_months=[0] * 12 if demand_rate else None,
    )


def hours_schedule(peak_hours, *, peak_period: int = 1) -> list:
    """A 12x24 schedule with ``peak_period`` during ``peak_hours`` (else period 0)."""
    pk = set(peak_hours)
    return [[peak_period if h in pk else 0 for h in range(24)] for _ in range(12)]


def tou_tariff(off_peak_rate: float, peak_rate: float, peak_hours, *,
               demand_rate: float = 0.0, fixed_monthly: float = 0.0) -> Tariff:
    """A two-period TOU energy tariff (period 0 off-peak, period 1 peak) over ``peak_hours``."""
    sched = hours_schedule(peak_hours)
    return Tariff(
        name="tou",
        fixed_monthly=fixed_monthly,
        energy_rates=[[(None, off_peak_rate)], [(None, peak_rate)]],
        energy_weekday=sched, energy_weekend=sched,
        flat_demand_rates=[[(None, demand_rate)]] if demand_rate else [],
        flat_demand_months=[0] * 12 if demand_rate else None,
    )
