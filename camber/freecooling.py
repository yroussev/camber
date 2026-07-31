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
    "FreeCoolingOpportunity",
    "free_cooling_opportunity",
]


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

    def as_dict(self) -> dict:
        return asdict(self)


def free_cooling_opportunity(
    oat,
    cooling_signal,
    *,
    cooling_kw=None,
    high_limit_f: float = 65.0,
    active_thresh: float = 0.05,
    recover_frac: float = 0.7,
    price_per_kwh: float | None = None,
) -> FreeCoolingOpportunity:
    """Quantify the missed economizer free-cooling opportunity.

    ``oat`` is outdoor-air temperature (°F); ``cooling_signal`` indicates mechanical cooling running
    (a cooling-valve fraction or chiller power — ``> active_thresh`` counts as on), aligned to
    ``oat``.
    Free cooling is *available* when OAT is below ``high_limit_f`` and *missed* when it's available
    yet mechanical cooling runs. With ``cooling_kw`` (the mechanical cooling power aligned to
    ``oat``)
    the missed-hours energy is summed; ``recover_frac`` is the fraction an economizer could offset,
    and ``price_per_kwh`` values it.
    """
    cols = {"oat": oat, "cool": cooling_signal}
    if cooling_kw is not None:
        cols["kw"] = cooling_kw
    df = pd.DataFrame(cols).dropna(subset=["oat", "cool"])
    if df.empty:
        return FreeCoolingOpportunity(0.0, 0.0, float("nan"), 0.0, 0.0, float("nan"), high_limit_f)
    dt = interval_hours(df.index)
    available = df["oat"] < high_limit_f
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
    )
