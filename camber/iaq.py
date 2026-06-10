"""Indoor air quality / ventilation-adequacy diagnostics (CO2-based).

Std-55 (see :mod:`camber.comfort`) answers "is the space thermally comfortable?";
this answers the complementary "is it adequately *ventilated*?" using zone CO2 as the
practical proxy. At steady state a space's CO2 rises above outdoor by an amount
inversely proportional to the outdoor-air rate per person, so:

- **persistently elevated CO2** during occupancy means **under-ventilation** -- too
  little outdoor air per person (an IAQ / ASHRAE 62.1 concern), and
- **CO2 sitting near outdoor** during occupancy means **over-ventilation** -- more
  outdoor air than needed, which in a hot-dry climate is a direct conditioning penalty
  (the energy flip side of the same knob).

ASHRAE's long-standing guidance ties a steady-state rise of ~700 ppm above outdoor to
the ~7.5 L/s-person minimum for a typical office; with ~400 ppm outdoor that lands near
**1100 ppm absolute**. So the default flags elevated CO2 above ~1100 ppm (or >700 ppm
above a supplied outdoor reference) and over-ventilation when CO2 barely rises above
outdoor during occupied hours. CO2 is a *ventilation-rate proxy, read with occupancy in
mind*, not a toxicity threshold; this measures the rate, it doesn't diagnose the cause.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask


@dataclass
class CO2VentilationResult:
    """CO2-based ventilation adequacy over occupied hours for one zone."""

    equip: str
    n_occupied: int               # occupied intervals with valid CO2
    co2_median_ppm: float
    co2_p95_ppm: float            # 95th-percentile occupied CO2 (the bad-hour level)
    under_vent_pct: float         # % occupied hrs CO2 above the elevated threshold
    over_vent_pct: float          # % occupied hrs CO2 near outdoor (possible over-ventilation)
    outdoor_co2_ppm: float        # outdoor reference used (measured or assumed)
    high_ppm: float               # elevated-CO2 threshold used
    coverage_start: str
    coverage_end: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_co2_ventilation(
    df: pd.DataFrame,
    equip: str,
    *,
    delta_high_ppm: float = 700.0,    # CO2 this far above outdoor == under-ventilated
    delta_low_ppm: float = 150.0,     # CO2 only this far above outdoor == over-ventilated
    assumed_outdoor_ppm: float = 420.0,  # used when no outdoor-CO2 column is present
    occupied_only: bool = True,
) -> CO2VentilationResult | None:
    """Score CO2-based ventilation adequacy. ``df`` has 'CO2' (ppm) and optional
    'OutdoorCO2' (ppm); the rule wrapper maps roles to these.

    Thresholds are differential vs outdoor (ASHRAE ventilation-rate guidance): a rise
    over ``delta_high_ppm`` is under-ventilation, a rise under ``delta_low_ppm`` during
    occupancy is likely over-ventilation. With no outdoor sensor, ``assumed_outdoor_ppm``
    (~420) stands in -> ~1120 ppm absolute high threshold.
    """
    if "CO2" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    co2 = work["CO2"].dropna()
    co2 = co2[(co2 >= 250) & (co2 <= 5000)]   # plausibility guard (drop sensor dropouts)
    if len(co2) < 10:
        return None

    if "OutdoorCO2" in work.columns and work["OutdoorCO2"].notna().any():
        oa = work["OutdoorCO2"].reindex(co2.index)
        oa = oa[(oa >= 300) & (oa <= 700)]
        outdoor = float(oa.median()) if len(oa) else assumed_outdoor_ppm
    else:
        outdoor = assumed_outdoor_ppm

    rise = co2 - outdoor
    under = float((rise > delta_high_ppm).mean())
    over = float((rise < delta_low_ppm).mean())

    return CO2VentilationResult(
        equip=equip,
        n_occupied=int(len(co2)),
        co2_median_ppm=round(float(co2.median()), 0),
        co2_p95_ppm=round(float(co2.quantile(0.95)), 0),
        under_vent_pct=round(100.0 * under, 1),
        over_vent_pct=round(100.0 * over, 1),
        outdoor_co2_ppm=round(outdoor, 0),
        high_ppm=round(outdoor + delta_high_ppm, 0),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
