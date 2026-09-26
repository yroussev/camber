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

from dataclasses import asdict, dataclass, field

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
    masked: dict = field(default_factory=dict)  # samples left out, by guard (see _oaf_samples)
    # per-month design minimum overriding ``min_oa_pct`` for those months (None = one minimum)
    min_oa_by_month: dict | None = None
    median_vs_min_pct: float = float("nan")  # median of (OAF - that sample's minimum), %-points
    median_ratio_to_min: float = float("nan")  # median of OAF / that sample's minimum

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _oaf_samples(
    df: pd.DataFrame,
    *,
    denom_min_f: float = 5.0,
    occupied_only: bool = True,
    gate=None,
    occ=None,
) -> pd.DataFrame | None:
    """The per-sample OA fraction the diagnostic judges: ``OAT`` and ``oaf`` (%) columns.

    ``gate`` (optional boolean Series, e.g. fan-on) keeps only the samples where it is True; ``occ``
    (a trended occupied/unoccupied Series) replaces the assumed weekday schedule. The frame's
    ``attrs["masked"]`` counts what was left out: ``fan_off``, ``small_delta_t``
    (``|RAT-OAT| < denom_min_f``, where the balance divides by a near-zero difference) and
    ``out_of_range`` (an OAF outside -20..120 %).

    Occupied (optional), plausible, numerically stable (``|RAT-OAT| >= denom_min_f``) samples with
    an OAF in the physical-ish range. ``None`` when a needed column is missing or fewer than 10
    samples survive the guards. Shared with the rule's evidence chart so the chart draws exactly the
    samples behind the verdict.
    """
    need = ("OAT", "MixedAir", "ReturnAir")
    if any(c not in df.columns for c in need):
        return None
    work = df.copy()
    masked = {"fan_off": 0, "small_delta_t": 0, "out_of_range": 0}
    if occupied_only:
        if occ is not None and pd.Series(occ).notna().any():
            work = work[effective_occupied_mask(work.index, occ=pd.Series(occ))]
        else:
            work = work[occupied_mask(work.index)]
    if gate is not None:
        # with the fan off the three sensors read still air, not a mixing balance
        g = pd.Series(gate)
        if not g.index.equals(work.index):
            g = g[~g.index.duplicated()].reindex(work.index)
        on = g.fillna(False).astype(bool).to_numpy()
        masked["fan_off"] = int((~on & work[list(need)].notna().all(axis=1).to_numpy()).sum())
        work = work[on]
    w = work[list(need)].dropna()
    # plausibility guards (drop sensor dropouts)
    w = w[(w.OAT.between(20, 130)) & (w.MixedAir.between(30, 120)) & (w.ReturnAir.between(40, 110))]
    denom = w.ReturnAir - w.OAT
    stable = denom.abs() >= denom_min_f
    masked["small_delta_t"] = int((~stable).sum())
    w = w[stable]
    if len(w) < 10:
        return None
    oaf = 100.0 * (w.ReturnAir - w.MixedAir) / (w.ReturnAir - w.OAT)
    keep = (oaf > -20) & (oaf < 120)  # physical-ish range
    masked["out_of_range"] = int((~keep).sum())
    out = pd.DataFrame({"OAT": w.OAT[keep], "oaf": oaf[keep]})
    out.attrs["masked"] = masked
    return out


def _sample_minima(index: pd.DatetimeIndex, min_oa_pct: float, by_month: dict | None) -> pd.Series:
    """Each sample's design minimum: ``by_month[month]`` where given, else ``min_oa_pct``."""
    mins = pd.Series(float(min_oa_pct), index=index)
    if by_month:
        month = pd.Series(index.month, index=index)
        mins = month.map({int(k): float(v) for k, v in by_month.items()})
        mins = mins.fillna(float(min_oa_pct)).astype(float)
    return mins


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
    ``OAFractionResult.masked`` counts the samples each guard left out.
    """
    w = _oaf_samples(df, denom_min_f=denom_min_f, occupied_only=occupied_only, gate=gate, occ=occ)
    if w is None:
        return None
    oaf = w["oaf"]
    if len(oaf) < 10:
        return None

    by_month = {int(k): float(v) for k, v in (min_oa_by_month or {}).items()}
    mins = _sample_minima(oaf.index, min_oa_pct, by_month)
    cooling = w["OAT"] > cooling_cutoff_f
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
        masked=dict(w.attrs.get("masked", {})),
        min_oa_by_month=by_month or None,
        median_vs_min_pct=round(float((oaf - mins).median()), 1),
        median_ratio_to_min=round(float((oaf / mins).median()), 3)
        if bool((mins > 0).all())
        else float("nan"),
    )
