"""Outdoor-air-fraction diagnostic (PNNL Ch.5, "Minimum outdoor air").

The fraction of supply air drawn from outdoors is computed from a temperature
balance across the mixing box:

    OAF = (RAT - MAT) / (RAT - OAT)

(return-air, mixed-air, outdoor-air temps). In a hot-dry climate every excess
percent of outdoor air when *not* economizing is a direct cooling penalty -- you
pay to cool hot makeup air you didn't need. PNNL's point: "a 20% damper is never
20% OA", so compute the fraction, don't trust the damper command.

This flags two opposite faults: **excess OA above the design minimum while in
cooling weather** (OAT above a cutoff, so economizing isn't the reason -- a cooling
penalty), and **under-ventilation**, where the median OAF sits below the minimum
across occupied hours (a stuck-closed / under-driven OA damper -- an IAQ and
ventilation-code risk). The temperature balance is numerically unstable when
RAT ~= OAT (denominator near zero), so those intervals are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask


@dataclass
class OAFractionResult:
    """Outside-air-fraction diagnostics: excess-OA in cooling and under-ventilation rates."""

    equip: str
    n_valid: int                  # intervals with a stable OAF
    oaf_median_pct: float
    n_cooling: int                # valid intervals in cooling weather (OAT > cutoff)
    excess_oa_pct: float          # % of cooling intervals with OAF above min + margin
    median_oaf_cooling: float     # median OAF during cooling weather
    under_vent_pct: float         # % of occupied intervals with OAF below min - margin
    min_oa_pct: float             # the design-minimum assumption used
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_oa_fraction(
    df: pd.DataFrame,
    equip: str,
    *,
    min_oa_pct: float = 20.0,        # assumed design minimum OA fraction
    excess_margin_pct: float = 5.0,  # OAF above min+margin == excess
    under_margin_pct: float = 5.0,   # OAF below min-margin == under-ventilation
    cooling_cutoff_f: float = 70.0,  # OAT above this == cooling weather (not economizing)
    denom_min_f: float = 5.0,        # require |RAT-OAT| >= this for a stable OAF
    occupied_only: bool = True,
) -> OAFractionResult | None:
    """Compute OAF and flag excess outdoor air in cooling weather.

    Thresholds are OUR judgment / PNNL Ch.5: min_oa_pct (design minimum, confirm
    against the sequence), denom_min_f=5 (stability guard on the temperature
    balance), cooling_cutoff_f=70 (above this, OA is a penalty not free cooling).
    """
    need = ("OAT", "MixedAir", "ReturnAir")
    if any(c not in df.columns for c in need):
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    w = work[list(need)].dropna()
    # plausibility guards (drop sensor dropouts)
    w = w[(w.OAT.between(20, 130)) & (w.MixedAir.between(30, 120)) & (w.ReturnAir.between(40, 110))]
    denom = w.ReturnAir - w.OAT
    w = w[denom.abs() >= denom_min_f]
    if len(w) < 10:
        return None
    oaf = 100.0 * (w.ReturnAir - w.MixedAir) / (w.ReturnAir - w.OAT)
    oaf = oaf[(oaf > -20) & (oaf < 120)]   # physical-ish range
    if len(oaf) < 10:
        return None

    cooling = w.OAT.reindex(oaf.index) > cooling_cutoff_f
    oaf_cool = oaf[cooling]
    n_cool = int(len(oaf_cool))
    excess = float((oaf_cool > min_oa_pct + excess_margin_pct).mean()) if n_cool else 0.0

    # Under-ventilation: OAF persistently below the minimum across occupied hours
    # (a stuck-closed/under-driven OA damper -- the opposite of excess OA, and an
    # IAQ/ventilation-code risk rather than an energy penalty).
    under = float((oaf < min_oa_pct - under_margin_pct).mean())

    return OAFractionResult(
        equip=equip,
        n_valid=int(len(oaf)),
        oaf_median_pct=round(float(oaf.median()), 1),
        n_cooling=n_cool,
        excess_oa_pct=round(100.0 * excess, 1),
        median_oaf_cooling=round(float(oaf_cool.median()), 1) if n_cool else float("nan"),
        under_vent_pct=round(100.0 * under, 1),
        min_oa_pct=float(min_oa_pct),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
