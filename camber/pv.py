"""Photovoltaic (PV) array monitoring: generation, performance ratio, net energy.

Tracks how much a PV system actually produces versus what it should, and how its
output offsets building load. The performance ratio normalizes generation by the
incident solar resource and the array's rated capacity, so a declining PR signals
soiling or module/inverter degradation independent of weather.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Reference irradiance at standard test conditions: 1 kW/m^2 (1000 W/m^2).
G_REF_KW_M2 = 1.0


def daily_generation(ac_energy: pd.Series) -> pd.Series:
    """Daily generated energy from an interval AC-energy series (sum per day)."""
    return ac_energy.dropna().resample("D").sum()


def performance_ratio(ac_energy_kwh: float, poa_irradiation_kwh_m2: float,
                      rated_kw: float, *, g_ref_kw_m2: float = G_REF_KW_M2) -> float:
    """Performance ratio = (E_ac / P_rated) / (H_POA / G_ref), a unitless fraction.

    ``ac_energy_kwh`` is generation over a period; ``poa_irradiation_kwh_m2`` is the
    plane-of-array insolation over the same period; ``rated_kw`` is the array's
    nameplate DC capacity. PR > ~0.75-0.80 is healthy; a falling trend means
    soiling or degradation.
    """
    if rated_kw <= 0 or poa_irradiation_kwh_m2 <= 0:
        return float("nan")
    yield_ratio = ac_energy_kwh / rated_kw
    reference_yield = poa_irradiation_kwh_m2 / g_ref_kw_m2
    return round(yield_ratio / reference_yield, 4)


def specific_yield(ac_energy_kwh: float, rated_kw: float) -> float:
    """Specific yield (kWh per kW installed) over the period."""
    return round(ac_energy_kwh / rated_kw, 2) if rated_kw else float("nan")


def expected_generation(poa_irradiation_kwh_m2: float, rated_kw: float, *,
                        performance_ratio: float = 0.80,
                        g_ref_kw_m2: float = G_REF_KW_M2) -> float:
    """Expected AC energy for a given insolation, capacity and assumed PR."""
    return round(rated_kw * (poa_irradiation_kwh_m2 / g_ref_kw_m2)
                 * performance_ratio, 2)


def net_energy(load: pd.Series, generation: pd.Series) -> pd.Series:
    """Net building energy = load - PV generation (positive = import, negative = export)."""
    df = pd.DataFrame({"l": load, "g": generation}).fillna(0.0)
    return df["l"] - df["g"]


@dataclass(frozen=True)
class PvSummary:
    """Period PV performance summary."""

    generation_kwh: float
    expected_kwh: float
    performance_ratio: float
    specific_yield: float
    self_consumption_pct: float    # share of generation used on-site (vs exported)


def pv_summary(ac_energy: pd.Series, poa_irradiation_kwh_m2: float,
               rated_kw: float, *, load: pd.Series | None = None) -> PvSummary:
    """Summarize PV performance over the series' period.

    If ``load`` is given, also reports self-consumption (the fraction of generation
    consumed on-site rather than exported).
    """
    gen = float(ac_energy.dropna().sum())
    pr = performance_ratio(gen, poa_irradiation_kwh_m2, rated_kw)
    exp = expected_generation(poa_irradiation_kwh_m2, rated_kw)
    sc = float("nan")
    if load is not None:
        df = pd.DataFrame({"l": load, "g": ac_energy}).fillna(0.0)
        on_site = (df[["l", "g"]].min(axis=1)).clip(lower=0).sum()
        sc = round(100.0 * on_site / gen, 1) if gen else float("nan")
    return PvSummary(generation_kwh=round(gen, 2), expected_kwh=exp,
                     performance_ratio=pr,
                     specific_yield=specific_yield(gen, rated_kw),
                     self_consumption_pct=sc)
