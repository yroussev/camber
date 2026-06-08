"""ECM savings estimation from metered energy (measured-waste method).

IMPORTANT method note. Proper IPMVP "avoided energy use" needs a post-retrofit
period to compare against; a pre-retrofit-only dataset has none yet. So these
functions do NOT claim IPMVP avoided energy. Instead they quantify the **energy actually delivered under a
wasteful operating condition**, directly from the BTU meters -- e.g. heating energy
metered while it is hot outside, or the heating/cooling that overlaps in time.
That metered quantity is a defensible *upper-bound savings estimate* for the
corresponding ECM: fixing the condition cannot save more than the energy currently
spent on it, and realistically saves a fraction of it.

Each result reports the metered waste energy AND states its method/assumption so it
is never mistaken for a measured post-retrofit saving. Once a measure is
implemented, the change-point M&V engine (models.py + stats.py) gives the true
avoided-energy-with-uncertainty figure.

Energy units: BTU meters report a rate (BTU/hr); we integrate to energy with the
rate-aware resampler. Helpers convert to therms (heating) and ton-hours (cooling).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .intervalfit import rate_to_energy
from ..schedules import occupied_mask

BTU_PER_THERM = 100_000.0
BTU_PER_TON_HOUR = 12_000.0


@dataclass
class WasteEstimate:
    """Metered waste energy for one ECM as an upper-bound savings estimate."""

    ecm: str
    method: str                 # plain-language description of what was measured
    waste_btu: float            # metered energy under the wasteful condition (BTU/yr)
    waste_display: str          # human units (therms or ton-hours)
    total_btu: float            # total metered energy of that stream (for context)
    waste_fraction_pct: float   # waste as % of that stream
    basis: str = "upper-bound (metered waste, not a measured post-retrofit saving)"

    def as_dict(self):
        """Return as a plain dict."""
        return asdict(self)


def _annual(rate_btu_hr: pd.Series, lo: str, hi: str) -> pd.Series:
    """Integrate a BTU/hr rate to hourly BTU energy over a date window."""
    e = rate_to_energy(rate_btu_hr, "1h")
    return e.loc[lo:hi].dropna()


def heating_in_cooling_weather(hhw_rate, oat, *, cutoff_f=70.0,
                               window=("2024-01-01", "2024-12-31")) -> WasteEstimate:
    """Heating energy metered while OAT is above ``cutoff_f`` (boiler-lockout ECM).

    Upper bound on the boiler-summer-lockout / HW-reset measure: heating delivered
    in cooling weather is energy a comfort-only heating plant should not be
    spending.
    """
    e = _annual(hhw_rate, *window)
    oat_h = oat.resample("1h").mean().reindex(e.index).ffill(limit=4)
    hot = oat_h > cutoff_f
    waste = float(e[hot].sum())
    total = float(e.sum())
    return WasteEstimate(
        ecm="boiler summer lockout + HW reset",
        method=f"heating energy metered while OAT > {cutoff_f:.0f}F",
        waste_btu=waste, waste_display=f"{waste / BTU_PER_THERM:,.0f} therms/yr",
        total_btu=total,
        waste_fraction_pct=round(100.0 * waste / total, 1) if total else 0.0,
    )


def simultaneous_heat_cool_energy(hhw_rate, chw_rate, *,
                                  window=("2024-01-01", "2024-12-31")) -> WasteEstimate:
    """Heating energy delivered in hours when cooling is ALSO being delivered.

    Upper bound on the simultaneous-heating/cooling + overcooling/reheat measures:
    the heating that overlaps cooling in time is the reheat-fighting energy. We
    count the heating side (the addressable, smaller stream).
    """
    he = _annual(hhw_rate, *window)
    ce = _annual(chw_rate, *window)
    both = pd.DataFrame({"h": he, "c": ce}).dropna()
    overlap = (both["h"] > 0) & (both["c"] > 0)
    waste = float(both.loc[overlap, "h"].sum())
    total = float(he.sum())
    return WasteEstimate(
        ecm="reduce simultaneous heating/cooling (SAT/CHW reset, min-flow, reheat)",
        method="heating energy metered during hours that also delivered cooling",
        waste_btu=waste, waste_display=f"{waste / BTU_PER_THERM:,.0f} therms/yr",
        total_btu=total,
        waste_fraction_pct=round(100.0 * waste / total, 1) if total else 0.0,
    )


def unoccupied_cooling_energy(chw_rate, index_for_occ=None, *,
                              window=("2024-01-01", "2024-12-31")) -> WasteEstimate:
    """Cooling energy metered during unoccupied hours (AHU setback ECM).

    Upper bound on the night/weekend setback measure: cooling delivered when the
    building is unoccupied. Uses the weekday-daytime occupancy proxy.
    """
    e = _annual(chw_rate, *window)
    occ = occupied_mask(e.index)
    waste = float(e[~occ].sum())
    total = float(e.sum())
    return WasteEstimate(
        ecm="AHU night/weekend setback (cooling side)",
        method="cooling energy metered during unoccupied (nights/weekends) hours",
        waste_btu=waste, waste_display=f"{waste / BTU_PER_TON_HOUR:,.0f} ton-hours/yr",
        total_btu=total,
        waste_fraction_pct=round(100.0 * waste / total, 1) if total else 0.0,
    )
