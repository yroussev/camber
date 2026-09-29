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

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "CO2VentilationResult",
    "analyze_co2_ventilation",
    "economizer_mode_mask",
    "DEFAULT_ECON_HIGH_LIMIT_F",
]


@dataclass
class CO2VentilationResult:
    """CO2-based ventilation adequacy over occupied hours for one zone."""

    equip: str
    n_occupied: int  # occupied intervals with valid CO2
    co2_median_ppm: float
    co2_p95_ppm: float  # 95th-percentile occupied CO2 (the bad-hour level)
    under_vent_pct: float  # % occupied hrs CO2 above the elevated threshold
    over_vent_pct: float  # % occupied hrs CO2 near outdoor (possible over-ventilation)
    outdoor_co2_ppm: float  # outdoor reference used (measured or assumed)
    high_ppm: float  # elevated-CO2 threshold used
    coverage_start: str
    coverage_end: str
    # 0.93 (#38): with an economizer mask, ``over_vent_pct`` is judged on the occupied hours
    # OUTSIDE economizer mode (None when fewer than ``min_hours`` remain); these report the rest
    econ_hours_pct: float | None = None  # % of occupied CO2 hours in economizer mode
    over_vent_econ_pct: float | None = None  # % of economizer-mode hours with CO2 near outdoor
    over_vent_all_pct: float | None = None  # % of ALL occupied hours with CO2 near outdoor

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_co2_ventilation(
    df: pd.DataFrame,
    equip: str,
    *,
    delta_high_ppm: float = 700.0,  # CO2 this far above outdoor == under-ventilated
    delta_low_ppm: float = 150.0,  # CO2 only this far above outdoor == over-ventilated
    assumed_outdoor_ppm: float = 420.0,  # used when no outdoor-CO2 column is present
    occupied_only: bool = True,
    economizer_mask: pd.Series | None = None,
    min_hours: int = 10,
) -> CO2VentilationResult | None:
    """Score CO2-based ventilation adequacy. ``df`` has 'CO2' (ppm) and optional
    'OutdoorCO2' (ppm); the rule wrapper maps roles to these.

    Thresholds are differential vs outdoor (ASHRAE ventilation-rate guidance): a rise
    over ``delta_high_ppm`` is under-ventilation, a rise under ``delta_low_ppm`` during
    occupancy is likely over-ventilation. With no outdoor sensor, ``assumed_outdoor_ppm``
    (~420) stands in -> ~1120 ppm absolute high threshold.

    ``economizer_mask`` (0.93, #38; see :func:`economizer_mode_mask`) marks the samples when an
    economizer brings in outdoor air beyond the ventilation minimum. CO2 near outdoor then is free
    cooling, not over-ventilation, so those hours are left out of ``over_vent_pct`` and reported
    apart (``econ_hours_pct``, ``over_vent_econ_pct``); ``over_vent_all_pct`` keeps the all-hours
    figure. With fewer than ``min_hours`` occupied hours outside economizer mode,
    ``over_vent_pct`` is ``None`` (not judged). Under-ventilation is judged on every occupied hour:
    an economizer only ever lowers CO2.
    """
    if "CO2" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    co2 = work["CO2"].dropna()
    co2 = co2[(co2 >= 250) & (co2 <= 5000)]  # plausibility guard (drop sensor dropouts)
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
    near = rise < delta_low_ppm
    over_pct: float | None = round(100.0 * float(near.mean()), 1)
    econ_kw: dict = {}
    if economizer_mask is not None:
        em = economizer_mask
        if not em.index.is_unique:
            em = em[~em.index.duplicated(keep="last")]
        econ = em.reindex(co2.index).fillna(False).astype(bool)
        rest = near[~econ]
        econ_kw = {
            "econ_hours_pct": round(100.0 * float(econ.mean()), 1),
            "over_vent_econ_pct": round(100.0 * float(near[econ].mean()), 1)
            if econ.any()
            else None,
            "over_vent_all_pct": over_pct,
        }
        over_pct = round(100.0 * float(rest.mean()), 1) if len(rest) >= min_hours else None

    return CO2VentilationResult(
        equip=equip,
        n_occupied=int(len(co2)),
        co2_median_ppm=round(float(co2.median()), 0),
        co2_p95_ppm=round(float(co2.quantile(0.95)), 0),
        under_vent_pct=round(100.0 * under, 1),
        over_vent_pct=over_pct,  # type: ignore[arg-type]  # None only with an economizer mask
        outdoor_co2_ppm=round(outdoor, 0),
        high_ppm=round(outdoor + delta_high_ppm, 0),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        **econ_kw,
    )


# ============================================================================ 0.93 (#38)
#: Economizer high limit (°F) assumed when no economizer command is trended -- the top of the
#: ASHRAE 90.1 fixed dry-bulb high-limit range, the same default the DCV rule uses.
DEFAULT_ECON_HIGH_LIMIT_F = 75.0


def economizer_mode_mask(
    index,
    *,
    econ_cmd=None,
    oat=None,
    damper=None,
    mat=None,
    rat=None,
    fan_on=None,
    damper_min_pct: float | None = None,
    above_min_pct: float = 5.0,
    high_limit_f: float = DEFAULT_ECON_HIGH_LIMIT_F,
) -> tuple[pd.Series | None, str]:
    """Where an economizer brings in outdoor air beyond the ventilation minimum.

    Returns ``(mask, basis)``, ``mask`` a boolean Series on ``index``:

    1. ``econ_cmd`` -- the trended economizer command; any nonzero hourly mean counts. Basis
       ``"econ_cmd"``.
    2. ``oat`` with ``damper`` and/or ``mat`` + ``rat`` -- the OAT is inside the economizer window
       (below ``high_limit_f``) **and** the unit takes more than its minimum outdoor air: the OA
       damper above its minimum by more than ``above_min_pct`` points, or the unit already on
       (nearly) 100 % outside air by :func:`camber.freecooling.integrated_economizer_mask` (the
       mixed-air temperature balance where it is well conditioned, else the damper). The damper
       minimum is ``damper_min_pct`` when given, else the 5th percentile of the damper while it is
       open (and the fan runs, with ``fan_on``). Basis ``"oat_damper"``.
    3. otherwise ``(None, "none")``: an OAT alone cannot say a unit economizes (a 100 %
       outdoor-air unit has no economizer; a zone may be served by one), so nothing is excluded.

    A 0-1 damper is rescaled to percent. Provisional API (0.93, #38).
    """
    from .freecooling import integrated_economizer_mask
    from .units import normalize_percent

    idx = pd.DatetimeIndex(index)
    if econ_cmd is not None:
        cmd = pd.Series(econ_cmd).reindex(idx)
        return (cmd.fillna(0.0) > 0.0).astype(bool), "econ_cmd"
    have_temps = mat is not None and rat is not None
    if oat is None or (damper is None and not have_temps):
        return None, "none"
    t = pd.Series(oat).reindex(idx).astype(float)
    window = (t < high_limit_f).fillna(False)
    above = pd.Series(False, index=idx)
    d = None
    if damper is not None:
        d = normalize_percent(pd.Series(damper).reindex(idx).astype(float))
        on = d > 0.5
        if fan_on is not None:
            on = on & pd.Series(fan_on).reindex(idx).fillna(False).astype(bool)
        lo = damper_min_pct
        if lo is None:
            lo = float(np.nanpercentile(d[on], 5)) if on.any() else float("nan")
        if lo == lo:
            above = (d > lo + above_min_pct).fillna(False)
    full = integrated_economizer_mask(
        t,
        damper=d,
        mat=pd.Series(mat).reindex(idx) if have_temps else None,
        rat=pd.Series(rat).reindex(idx) if have_temps else None,
    )
    if full is not None:
        above = above | full.reindex(idx).fillna(False).astype(bool)
    return (window & above).astype(bool), "oat_damper"
