"""Free-cooling (economizer) opportunity — quantify the missed free cooling in hours and dollars.

The economizer *rule* detects when the economizer misbehaves; this quantifies the **opportunity**:
how many hours ran mechanical cooling while the outdoor air was cool enough to cool for free, and —
given a cooling-power series and a price — how much energy and money that represents. It's the
business case that turns an economizer finding into a funded fix, in the spirit of
:mod:`camber.fault_economics`.

numpy/pandas; matplotlib not needed. A supplied electricity price is caller-set (no hard-coded
rate).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .timegrid import interval_hours

__all__ = [
    "DEFAULT_FREE_COOLING_HIGH_LIMIT_F",
    "ECON_DAMPER_MIN_PCT",
    "ECON_MIN_DELTA_F",
    "ECON_OAF_MIN_PCT",
    "FreeCoolingOpportunity",
    "free_cooling_opportunity",
    "integrated_economizer_mask",
]

#: Outdoor-air dry-bulb (°F) below which free cooling counts as *available* (0.98, #91). One value
#: for :func:`free_cooling_opportunity` and the ``free_cooling_missed`` rule. 60 °F is CAMBER's
#: deliberately conservative screening default: below it nearly every economizer sequence in any
#: climate should be cooling with outside air, so a missed hour is clearly missed. Economizer
#: guidance sets the dry-bulb high limit by climate (higher in dry climates, lower in humid ones);
#: pass the unit's own high limit to count more weather as free-cooling weather. See
#: ``docs/TUNING.md``.
DEFAULT_FREE_COOLING_HIGH_LIMIT_F = 60.0

# --- #63: shared "already on (nearly) 100 % outside air" test -------------------------------
#: OA-damper signal (%) at or above which the unit counts as on full outside air. A 0-1 signal is
#: rescaled first. 90 % rather than 100 % because an economizer damper at "full open" commonly
#: reads a few percent short (actuator span, stroke limits) and still passes ~all outside air.
ECON_DAMPER_MIN_PCT = 90.0
#: measured OA fraction (%) at or above which the unit counts as on full outside air -- a mixed-air
#: temperature within ~20 % of the OAT-RAT span of the OAT (sensor error and stratification in the
#: mixing box keep a true 100 %-OA unit from reading exactly 100 %).
ECON_OAF_MIN_PCT = 80.0
#: |OAT - RAT| (°F) below which the temperature-balance OA fraction is ill-conditioned (the
#: denominator is within a couple of sensor errors of zero) and the damper signal is used instead.
ECON_MIN_DELTA_F = 5.0


@dataclass
class FreeCoolingOpportunity:
    """Missed free-cooling hours and (if power is known) the recoverable energy/cost."""

    hours_available: float  # OAT below the economizer high limit
    hours_missed: float  # available AND mechanical cooling running
    missed_fraction: float  # missed / available
    addressable_kwh: float  # mechanical cooling energy during missed hours
    recoverable_kwh: float  # addressable × recover_frac
    savings_usd: float  # recoverable × price (NaN if no price given)
    high_limit_f: float
    # 0.100: the economizer low-limit lockout, when one is passed. Below it the sequence holds the
    # OA damper at minimum by design, so those hours are not free-cooling opportunity; they are
    # counted here instead. Omitted from :meth:`as_dict` when no low limit was passed.
    low_limit_f: float | None = None
    hours_low_limit_excluded: float = 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        if self.low_limit_f is None:  # pre-0.100 output, byte-identical
            del d["low_limit_f"], d["hours_low_limit_excluded"]
        return d


def _check_low_limit(high_limit_f: float, low_limit_f: float | None) -> None:
    if low_limit_f is not None and not low_limit_f < high_limit_f:
        raise ValueError(
            f"low_limit_f ({low_limit_f:g}) must be below high_limit_f ({high_limit_f:g})"
        )


def _free_cooling_weather(oat, high_limit_f: float, low_limit_f: float | None = None):
    """``(available, below_low_limit)`` boolean masks on ``oat``'s index -- the one free-cooling
    weather test :func:`free_cooling_opportunity` and the ``free_cooling_missed`` rule share.

    *Available* is OAT below ``high_limit_f`` and, with a ``low_limit_f``, at or above it.
    ``below_low_limit`` is OAT below the low limit (all ``False`` without one): weather in which
    the economizer is locked out by design, so it is not free-cooling opportunity. NaN OAT is in
    neither mask.
    """
    below_high = oat < high_limit_f
    if low_limit_f is None:
        return below_high, pd.Series(False, index=oat.index)
    _check_low_limit(high_limit_f, low_limit_f)
    below_low = oat < low_limit_f
    return below_high & ~below_low, below_low


def free_cooling_opportunity(
    oat,
    cooling_signal,
    *,
    cooling_kw=None,
    high_limit_f: float = DEFAULT_FREE_COOLING_HIGH_LIMIT_F,
    low_limit_f: float | None = None,
    active_thresh: float = 0.05,
    recover_frac: float = 0.7,
    price_per_kwh: float | None = None,
) -> FreeCoolingOpportunity:
    """Quantify the missed economizer free-cooling opportunity.

    ``oat`` is outdoor-air temperature (°F); ``cooling_signal`` indicates mechanical cooling running
    (a cooling-valve fraction or chiller power — ``> active_thresh`` counts as on), aligned to
    ``oat``.
    Free cooling is *available* when OAT is below ``high_limit_f`` and *missed* when it's available
    yet mechanical cooling runs. ``high_limit_f`` defaults to
    :data:`DEFAULT_FREE_COOLING_HIGH_LIMIT_F` (60 °F, the same as the ``free_cooling_missed`` rule;
    it was 65 °F before 0.98). With ``cooling_kw`` (the mechanical cooling power aligned to
    ``oat``)
    the missed-hours energy is summed; ``recover_frac`` is the fraction an economizer could offset,
    and ``price_per_kwh`` values it.

    ``low_limit_f`` (0.100, default ``None``) is the economizer's low-limit lockout: hours with
    OAT below it are not free-cooling opportunity (the sequence holds the OA damper at minimum
    there by design); they are counted in ``hours_low_limit_excluded`` instead. ``None`` keeps the
    pre-0.100 result. It must be below ``high_limit_f``. The same test the ``free_cooling_missed``
    rule's ``low_limit_f`` applies.
    """
    cols = {"oat": oat, "cool": cooling_signal}
    if cooling_kw is not None:
        cols["kw"] = cooling_kw
    df = pd.DataFrame(cols).dropna(subset=["oat", "cool"])
    _check_low_limit(high_limit_f, low_limit_f)
    if df.empty:
        return FreeCoolingOpportunity(
            0.0, 0.0, float("nan"), 0.0, 0.0, float("nan"), high_limit_f, low_limit_f
        )
    dt = interval_hours(df.index)
    available, below_low = _free_cooling_weather(df["oat"], high_limit_f, low_limit_f)
    active = df["cool"] > active_thresh
    missed = available & active

    hours_available = float(available.sum()) * dt
    hours_missed = float(missed.sum()) * dt
    missed_frac = hours_missed / hours_available if hours_available > 0 else float("nan")

    if "kw" in df.columns:
        addressable = float((df.loc[missed, "kw"].fillna(0.0) * dt).sum())
        recoverable = addressable * recover_frac
        savings = recoverable * price_per_kwh if price_per_kwh is not None else float("nan")
    else:
        addressable = recoverable = 0.0
        savings = float("nan")

    return FreeCoolingOpportunity(
        hours_available=round(hours_available, 2),
        hours_missed=round(hours_missed, 2),
        missed_fraction=round(missed_frac, 4) if np.isfinite(missed_frac) else float("nan"),
        addressable_kwh=round(addressable, 2),
        recoverable_kwh=round(recoverable, 2),
        savings_usd=round(savings, 2) if np.isfinite(savings) else float("nan"),
        high_limit_f=high_limit_f,
        low_limit_f=low_limit_f,
        hours_low_limit_excluded=round(float(below_low.sum()) * dt, 2),
    )


def integrated_economizer_mask(
    oat,
    *,
    damper=None,
    mat=None,
    rat=None,
    damper_min_pct: float = ECON_DAMPER_MIN_PCT,
    oaf_min_pct: float = ECON_OAF_MIN_PCT,
    min_delta_f: float = ECON_MIN_DELTA_F,
):
    """Where the unit already runs on (nearly) 100 % outside air -- an *integrated* economizer.

    Mechanical cooling while the unit is on full outside air is an integrated economizer doing its
    job (outside air alone can't meet the load), not missed free cooling. This is the one test both
    the ``free_cooling_missed`` rule and the RCx report's economizer page apply:

    * where the temperature balance is well conditioned (``|OAT - RAT| >= min_delta_f``), the
      measured OA fraction ``(RAT - MAT) / (RAT - OAT) >= oaf_min_pct`` decides -- a damper
      *command* can read open while the damper is stuck, the mixed-air temperature can't;
    * elsewhere, the OA-damper signal ``>= damper_min_pct`` % decides (0-1 or 0-100 accepted);
    * with neither signal usable on a sample, the sample is not counted as integrated.

    Returns a boolean Series on ``oat``'s index, or ``None`` when neither a damper nor both mixed-
    and return-air temperatures were supplied -- callers must then say they couldn't tell an
    integrated economizer from missed free cooling. Provisional API (0.91, #63).
    """
    from .units import normalize_percent

    oat = pd.Series(oat, dtype=float)
    have_temps = mat is not None and rat is not None
    if damper is None and not have_temps:
        return None
    econ = pd.Series(False, index=oat.index)
    if damper is not None:
        d = normalize_percent(pd.Series(damper).reindex(oat.index).astype(float))
        econ = (d.fillna(0.0) >= damper_min_pct).astype(bool)
    if have_temps:
        m = pd.Series(mat).reindex(oat.index).astype(float)
        r = pd.Series(rat).reindex(oat.index).astype(float)
        dt = r - oat
        stable = (dt.abs() >= min_delta_f).fillna(False)
        oaf = 100.0 * (r - m) / dt.where(stable)
        econ = econ.where(~stable, (oaf >= oaf_min_pct).fillna(False)).astype(bool)
    return econ
