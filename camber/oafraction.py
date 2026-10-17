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

import numpy as np
import pandas as pd

from .schedules import effective_occupied_mask, occupied_mask

__all__ = [
    "OAFractionResult",
    "analyze_oa_fraction",
    "SENSIBLE_BTUH_PER_CFM_F",
    "HEATING_RISE_F",
    "HEATING_VALVE_PCT",
    "ENTHALPY_BTUH_PER_CFM",
    "humidity_ratio",
    "moist_air_enthalpy",
    "excess_oa_penalty",
]

# Standard-air constants for airflow energy (IP units). Standard air is 0.075 lb/ft³ dry air with a
# specific heat of 0.24 Btu/lb·°F: 0.075 lb/ft³ x 60 min/h x 0.24 Btu/lb·°F = 1.08 Btu/h per cfm per
# °F of sensible difference, and 0.075 x 60 = 4.5 lb/h per cfm, i.e. 4.5 Btu/h per cfm per Btu/lb of
# enthalpy difference. The usual HVAC load factors (q = 1.08 cfm ΔT, q = 4.5 cfm Δh).
SENSIBLE_BTUH_PER_CFM_F = 1.08
ENTHALPY_BTUH_PER_CFM = 4.5
_P_ATM_PSIA = 14.696

# A heating coil adds heat when the supply air leaves this much warmer than the mixed air (above
# the 1-2 °F a supply fan adds); with a heating valve trended, a valve above this % is heating.
HEATING_RISE_F = 2.0
HEATING_VALVE_PCT = 5.0
# A trended supply airflow whose 95th percentile sits at or below this (cfm) is read as a percent
# signal mapped to a flow role and not used as an airflow.
_MIN_PLAUSIBLE_CFM = 100.0


def _sat_pressure_psia(t_f):
    """Saturation vapour pressure (psia) over liquid water (≥ 32 °F) or ice (< 32 °F).

    The Hyland-Wexler correlations in IP units, as given in the psychrometrics chapter of the ASHRAE
    Handbook--Fundamentals. Vectorised over arrays.
    """
    t = np.asarray(t_f, dtype=float)
    T = t + 459.67
    with np.errstate(invalid="ignore", divide="ignore"):
        ln_water = (
            -1.0440397e4 / T
            - 1.1294650e1
            - 2.7022355e-2 * T
            + 1.2890360e-5 * T**2
            - 2.4780681e-9 * T**3
            + 6.5459673 * np.log(T)
        )
        ln_ice = (
            -1.0214165e4 / T
            - 4.8932428
            - 5.3765794e-3 * T
            + 1.9202377e-7 * T**2
            + 3.5575832e-10 * T**3
            - 9.0344688e-14 * T**4
            + 4.1635019 * np.log(T)
        )
        return np.exp(np.where(t >= 32.0, ln_water, ln_ice))


def humidity_ratio(t_f, rh_pct=None, *, dewpoint_f=None, pressure_psia: float = _P_ATM_PSIA):
    """Humidity ratio (lb water / lb dry air) from dry-bulb °F and RH % -- or from a dew point.

    ``W = 0.621945 pw / (p - pw)`` with ``pw = RH x pws(t)`` (or ``pws(dew point)``). Vectorised;
    NaN where an input is missing or out of range (RH outside 0-100 %).
    """
    if dewpoint_f is not None:
        pw = _sat_pressure_psia(dewpoint_f)
    else:
        rh = np.asarray(rh_pct, dtype=float)
        rh = np.where((rh >= 0.0) & (rh <= 100.0), rh, np.nan)
        pw = rh / 100.0 * _sat_pressure_psia(t_f)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 0.621945 * pw / (pressure_psia - pw)


def moist_air_enthalpy(t_f, w):
    """Moist-air enthalpy (Btu/lb dry air): ``h = 0.240 t + W (1061 + 0.444 t)`` (IP)."""
    t = np.asarray(t_f, dtype=float)
    return 0.240 * t + np.asarray(w, dtype=float) * (1061.0 + 0.444 * t)


def _interval_h(index) -> float:
    """The median sample interval (hours) of a DatetimeIndex (1 h when it cannot be read)."""
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return 1.0
    d = pd.Series(idx).diff().dropna()
    d = d[d > pd.Timedelta(0)]
    if d.empty:
        return 1.0
    return float(d.median() / pd.Timedelta(hours=1))


def excess_oa_penalty(
    frame: pd.DataFrame,
    oaf_pct: pd.Series,
    min_pct: pd.Series,
    *,
    margin_pct: float,
    observed_h: float,
) -> dict:
    """The energy behind excess outside air on the judged samples (``oa_cost_*`` metrics).

    ``frame`` holds the judged samples' conditions, indexed like ``oaf_pct``: ``OAT`` and
    ``ReturnAir`` (°F), and optionally ``MixedAir`` / ``SupplyAir`` (°F), ``HeatValve`` (%),
    ``OAT_RH`` / ``RA_RH`` (%), ``RA_DP`` (°F) and ``SupplyCFM``. ``oaf_pct`` is each sample's OA
    fraction (%) and ``min_pct`` its declared design minimum (%). A sample carries **excess** when
    its OA fraction is above ``min + margin_pct`` (the rule's own detection margin, so sensor noise
    at the minimum is not priced); the excess priced is ``e = (OAF - min) / 100`` of the supply air.

    Each excess sample is read for what the extra outside air does there:

    * **heating** -- outdoor air colder than return air while the unit heats (a heating valve above
      5 %, else supply air more than 2 °F above the mixed air): the coil reheats the excess, at
      ``1.08 x e x (RAT - OAT)`` Btu/h per supply cfm, capped at the coil's measured rise
      ``1.08 x (SAT - MAT)`` when both temperatures are trended;
    * **cooling** -- outdoor air with more enthalpy than return air (or, with no humidity trended
      at both, warmer than it): the coil removes ``4.5 x e x (h_OA - h_RA)`` (or ``1.08 x e x (OAT -
      RAT)``) Btu/h per supply cfm;
    * **economizing** -- outdoor air colder than return air and the unit not heating: free cooling,
      not a penalty (counted in ``oa_cost_economizing_h``, priced at zero);
    * **not judged** -- colder outdoor air on a unit that trends neither a heating valve nor supply
      and mixed air, so heating cannot be told from economizing (``oa_cost_heat_unjudged_h``).

    Sums are Btu per cfm of supply airflow over the period (``oa_cost_excess_*_btu_per_cfm``) and,
    when the unit trends a plausible supply airflow (``SupplyCFM`` on at least 90 % of the priced
    samples), kBtu over the period (``oa_cost_excess_*_kbtu``). ``observed_h`` -- the hours of data
    the period covers -- is what an estimator annualises against.
    """
    oaf = pd.Series(oaf_pct, dtype=float)
    mins = pd.Series(min_pct, dtype=float).reindex(oaf.index)
    w = frame.reindex(oaf.index)
    dt = _interval_h(w.index)
    e = ((oaf - mins) / 100.0).where(oaf > mins + float(margin_pct), 0.0).clip(lower=0.0)
    e = e.fillna(0.0)
    oat, rat = w["OAT"].astype(float), w["ReturnAir"].astype(float)
    excess = e > 0
    cold = oat < rat

    def col(name):
        return w[name].astype(float) if name in w.columns else None

    mat, sat, hv = col("MixedAir"), col("SupplyAir"), col("HeatValve")
    heat_basis = None
    heating = pd.Series(np.nan, index=w.index)
    if hv is not None and hv.notna().any():
        heating = (hv > HEATING_VALVE_PCT).astype(float).where(hv.notna())
        heat_basis = "heat_valve"
    elif sat is not None and mat is not None and sat.notna().any():
        heating = (sat > mat + HEATING_RISE_F).astype(float).where(sat.notna() & mat.notna())
        heat_basis = "supply_air_temp"

    # enthalpy where both humidities are trended on the sample, sensible otherwise
    h_oa = h_ra = pd.Series(np.nan, index=w.index)
    oa_rh, ra_rh, ra_dp = col("OAT_RH"), col("RA_RH"), col("RA_DP")
    if oa_rh is not None and (ra_rh is not None or ra_dp is not None):
        w_oa = humidity_ratio(oat, oa_rh)
        if ra_rh is not None and ra_rh.notna().any():
            w_ra = humidity_ratio(rat, ra_rh)
        elif ra_dp is not None:
            w_ra = humidity_ratio(rat, dewpoint_f=ra_dp)
        else:
            w_ra = np.full(len(w), np.nan)
        h_oa = pd.Series(moist_air_enthalpy(oat, w_oa), index=w.index)
        h_ra = pd.Series(moist_air_enthalpy(rat, w_ra), index=w.index)
    has_h = h_oa.notna() & h_ra.notna()

    heat_s = excess & cold & (heating == 1.0)
    warm_h = has_h & (h_oa > h_ra)
    warm_t = ~has_h & (oat > rat)
    cool_s = excess & ~heat_s & (warm_h | warm_t)
    econ_s = excess & cold & ~heat_s & ~cool_s & (heating == 0.0)
    unjudged = excess & cold & ~heat_s & ~cool_s & heating.isna()

    cool_rate = pd.Series(0.0, index=w.index)  # Btu/h per supply cfm
    cool_rate = cool_rate.where(~(cool_s & has_h), ENTHALPY_BTUH_PER_CFM * e * (h_oa - h_ra))
    cool_rate = cool_rate.where(~(cool_s & ~has_h), SENSIBLE_BTUH_PER_CFM_F * e * (oat - rat))
    heat_rise = e * (rat - oat)
    if sat is not None and mat is not None:
        coil = (sat - mat).clip(lower=0.0)
        heat_rise = heat_rise.where(coil.isna(), np.minimum(heat_rise, coil))
    heat_rate = (SENSIBLE_BTUH_PER_CFM_F * heat_rise).where(heat_s, 0.0)
    cool_rate, heat_rate = cool_rate.fillna(0.0), heat_rate.fillna(0.0)

    n_cool_h = int(cool_s.sum())
    if n_cool_h == 0:
        cool_basis = None
    elif int((cool_s & has_h).sum()) == n_cool_h:
        cool_basis = "enthalpy"
    elif int((cool_s & has_h).sum()) == 0:
        cool_basis = "sensible (no outdoor and return humidity trended)"
    else:
        cool_basis = "enthalpy (sensible where humidity is missing)"
    out = {
        "oa_cost_observed_h": round(float(observed_h), 2),
        "oa_cost_interval_h": round(dt, 4),
        "oa_cost_margin_pct": float(margin_pct),
        "oa_cost_excess_cool_btu_per_cfm": round(float(cool_rate.sum()) * dt, 3),
        "oa_cost_excess_heat_btu_per_cfm": round(float(heat_rate.sum()) * dt, 3),
        "oa_cost_excess_cool_kbtu": None,
        "oa_cost_excess_heat_kbtu": None,
        "oa_cost_cool_h": round(n_cool_h * dt, 2),
        "oa_cost_heat_h": round(int(heat_s.sum()) * dt, 2),
        "oa_cost_economizing_h": round(int(econ_s.sum()) * dt, 2),
        "oa_cost_heat_unjudged_h": round(int(unjudged.sum()) * dt, 2),
        "oa_cost_cool_basis": cool_basis,
        "oa_cost_heat_basis": heat_basis
        or "not judged (needs heat_valve, or supply_air_temp with mixed_air_temp)",
        "oa_cost_airflow": None,
    }
    sa = col("SupplyCFM")
    priced = cool_s | heat_s
    if sa is not None and bool(priced.any()):
        sa = sa.where(sa > 0)
        p95 = sa.quantile(0.95)
        cover = float(sa[priced].notna().mean())
        if p95 == p95 and p95 > _MIN_PLAUSIBLE_CFM and cover >= 0.9:
            sa = sa.fillna(float(sa[priced].median()))
            out["oa_cost_excess_cool_kbtu"] = round(float((cool_rate * sa).sum()) * dt / 1e3, 3)
            out["oa_cost_excess_heat_kbtu"] = round(float((heat_rate * sa).sum()) * dt / 1e3, 3)
            out["oa_cost_airflow"] = "trended supply airflow"
    return out


def _observed_hours(frame: pd.DataFrame, cols) -> float:
    """Hours of data in ``frame``: rows with every one of ``cols`` present x the median interval."""
    have = [c for c in cols if c in frame.columns]
    if not have or frame.empty:
        return 0.0
    n = int(frame[have].notna().all(axis=1).sum())
    return n * _interval_h(frame.index)


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
