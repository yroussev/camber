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

Only samples that can mean something are judged: occupied ones (the unit's trended occupancy
point when it has one, else an assumed weekday schedule) and, when the caller passes a fan-on
``gate``, fan-on ones -- with the fan stopped the three sensors read still air, not a mixing
balance. The design minimum may differ by season (``min_oa_by_month``): a sequence that holds a
lower minimum damper position in summer is judged against that month's minimum.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import effective_occupied_mask, occupied_mask

__all__ = [
    "OAFractionResult",
    "analyze_oa_fraction",
]


@dataclass
class OAFractionResult:
    """Outside-air-fraction diagnostics: excess-OA in cooling and under-ventilation rates."""

    equip: str
    n_valid: int  # intervals with a stable OAF
    oaf_median_pct: float
    n_cooling: int  # valid intervals in cooling weather (OAT > cutoff)
    excess_oa_pct: float  # % of cooling intervals with OAF above min + margin
    median_oaf_cooling: float  # median OAF during cooling weather
    under_vent_pct: float  # % of occupied intervals with OAF below min - margin
    min_oa_pct: float  # the design-minimum assumption used
    coverage_start: str
    coverage_end: str
    # per-month design minimum overriding ``min_oa_pct`` for those months (None = one minimum)
    min_oa_by_month: dict | None = None
    median_vs_min_pct: float = float("nan")  # median of (OAF - that sample's minimum), %-points
    median_ratio_to_min: float = float("nan")  # median of OAF / that sample's minimum

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_oa_fraction(
    df: pd.DataFrame,
    equip: str,
    *,
    min_oa_pct: float = 20.0,  # assumed design minimum OA fraction
    excess_margin_pct: float = 5.0,  # OAF above min+margin == excess
    under_margin_pct: float = 5.0,  # OAF below min-margin == under-ventilation
    cooling_cutoff_f: float = 70.0,  # OAT above this == cooling weather (not economizing)
    denom_min_f: float = 5.0,  # require |RAT-OAT| >= this for a stable OAF
    occupied_only: bool = True,
    gate=None,
    occ=None,
    min_oa_by_month: dict | None = None,
) -> OAFractionResult | None:
    """Compute OAF and flag excess outdoor air in cooling weather.

    Thresholds are OUR judgment / PNNL Ch.5: min_oa_pct (design minimum, confirm
    against the sequence), denom_min_f=5 (stability guard on the temperature
    balance), cooling_cutoff_f=70 (above this, OA is a penalty not free cooling).
    ``gate`` (optional boolean Series, e.g. fan-on) keeps only the samples where it is True;
    ``occ`` (a trended occupied/unoccupied Series) replaces the assumed weekday schedule;
    ``min_oa_by_month`` (``{month: pct}``) overrides ``min_oa_pct`` for those months.
    """
    need = ("OAT", "MixedAir", "ReturnAir")
    if any(c not in df.columns for c in need):
        return None
    work = df.copy()
    if occupied_only:
        if occ is not None and pd.Series(occ).notna().any():
            work = work[effective_occupied_mask(work.index, occ=pd.Series(occ))]
        else:
            work = work[occupied_mask(work.index)]
    if gate is not None:
        g = pd.Series(gate)
        if not g.index.equals(work.index):
            g = g[~g.index.duplicated()].reindex(work.index)
        work = work[g.fillna(False).astype(bool).to_numpy()]
    w = work[list(need)].dropna()
    # plausibility guards (drop sensor dropouts)
    w = w[(w.OAT.between(20, 130)) & (w.MixedAir.between(30, 120)) & (w.ReturnAir.between(40, 110))]
    denom = w.ReturnAir - w.OAT
    w = w[denom.abs() >= denom_min_f]
    if len(w) < 10:
        return None
    oaf = 100.0 * (w.ReturnAir - w.MixedAir) / (w.ReturnAir - w.OAT)
    oaf = oaf[(oaf > -20) & (oaf < 120)]  # physical-ish range
    if len(oaf) < 10:
        return None

    by_month = {int(k): float(v) for k, v in (min_oa_by_month or {}).items()}
    mins = pd.Series(float(min_oa_pct), index=oaf.index)
    if by_month:
        month = pd.Series(oaf.index.month, index=oaf.index)
        mins = month.map(by_month).fillna(float(min_oa_pct)).astype(float)
    cooling = w.OAT.reindex(oaf.index) > cooling_cutoff_f
    oaf_cool = oaf[cooling]
    n_cool = int(len(oaf_cool))
    excess = float((oaf_cool > mins[cooling] + excess_margin_pct).mean()) if n_cool else 0.0

    # Under-ventilation: OAF persistently below the minimum across occupied hours
    # (a stuck-closed/under-driven OA damper -- the opposite of excess OA, and an
    # IAQ/ventilation-code risk rather than an energy penalty).
    under = float((oaf < mins - under_margin_pct).mean())

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
        min_oa_by_month=by_month or None,
        median_vs_min_pct=round(float((oaf - mins).median()), 1),
        median_ratio_to_min=round(float((oaf / mins).median()), 3)
        if bool((mins > 0).all())
        else float("nan"),
    )
