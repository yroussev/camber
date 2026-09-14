"""CalTRACK / IPMVP-aligned whole-building savings (NMEC).

CAMBER's M&V pieces already implement the IPMVP Option-C / CalTRACK *normalized
metered energy consumption* workflow; this module assembles them into one call
using CalTRACK vocabulary and documents the correspondence so results can be
cross-checked against OpenEEmeter (eemeter). See ``docs/MANDV.md`` for the full
terminology bridge and the eemeter cross-check recipe.

Correspondence:

| CalTRACK / IPMVP term        | CAMBER                                          |
|------------------------------|-------------------------------------------------|
| baseline-period model        | `models.best_model` change-point (daily)        |
| fit metrics CV(RMSE) / NMBE  | `stats.fit_stats`                               |
| avoided energy use           | `stats.avoided_energy_savings` (G14 Annex-B FSU)|

This is the daily method (CalTRACK Daily). The hourly method maps onto
:mod:`camber.mandv.towt`; assembling that here is future work.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..ingest.quality import outlier_mask
from .intervalfit import daily_energy_vs_temp, hourly_energy_vs_temp
from .models import N_PARAMS, best_model
from .nonroutine import residual_outliers
from .stats import SavingsResult, avoided_energy_savings, cv_rmse_max_for, fit_stats
from .towt import TOWTAtIndex, fit_towt, hour_of_week


@dataclass
class NMECResult:
    """Whole-building NMEC savings: the baseline fit plus avoided energy use."""

    model_kind: str  # change-point family chosen for the baseline
    baseline_cv_rmse: float  # baseline fit CV(RMSE), fraction
    baseline_nmbe: float  # baseline fit NMBE (net determination bias)
    baseline_r2: float
    baseline_n: int  # baseline days used (after any NRE exclusion)
    savings: SavingsResult  # avoided energy + fractional savings uncertainty
    n_non_routine_excluded: int = 0  # baseline days dropped as non-routine events
    baseline_rho: float | None = None  # lag-1 residual autocorrelation; None if not estimable
    baseline_accepted: bool | None = None  # meets the G14 daily CV(RMSE) gate
    cv_rmse_max: float | None = None  # the gate it was judged against

    def as_dict(self) -> dict:
        """Return the result (with the nested savings) as a plain dict."""
        d = asdict(self)
        d["savings"] = self.savings.as_dict()
        return d


def caltrack_savings(
    baseline_energy: pd.Series,
    baseline_temp: pd.Series,
    reporting_energy: pd.Series,
    reporting_temp: pd.Series,
    *,
    confidence: float = 0.90,
    min_days: int = 60,
    rate_is_energy_rate: bool = False,
    exclude_non_routine: bool = False,
    nre_z: float = 3.5,
) -> NMECResult:
    """CalTRACK Daily / IPMVP Option-C avoided energy use from baseline + reporting.

    Fits a change-point baseline on daily energy vs daily-mean temperature, projects
    it onto the reporting period's weather, and reports avoided energy use with
    ASHRAE G14 Annex-B fractional savings uncertainty. ``*_energy`` are interval
    meter series and ``*_temp`` the matching outdoor temperatures;
    ``rate_is_energy_rate`` marks energy that is a rate (BTU/hr) rather than per-
    interval energy.

    With ``exclude_non_routine`` the baseline is screened for non-routine events
    (days whose residual is a robust outlier at modified-z > ``nre_z``); those days
    are dropped and the baseline refit, so a shutdown or anomaly doesn't skew it.
    """
    base = daily_energy_vs_temp(
        baseline_energy, baseline_temp, rate_is_energy_rate=rate_is_energy_rate
    )
    if len(base) < min_days:
        raise ValueError(f"need >= {min_days} baseline days, got {len(base)}")

    excluded = 0
    if exclude_non_routine:
        m0 = best_model(base["oat"].to_numpy(), base["energy"].to_numpy())
        nre = residual_outliers(base["energy"], base["oat"], m0, z=nre_z)
        excluded = int(nre.sum())
        if excluded:
            base = base[~nre.to_numpy()]

    model = best_model(base["oat"].to_numpy(), base["energy"].to_numpy())
    p = N_PARAMS[model.kind]
    # the daily frame is on a regular grid, so the residuals' lag-1 autocorrelation is meaningful
    # and feeds the savings band's effective-sample-size correction
    st = fit_stats(
        base["energy"].to_numpy(),
        model.predict(base["oat"].to_numpy()),
        p,
        cv_rmse_max=cv_rmse_max_for("daily"),
        time_index=base.index,
    )

    rep = daily_energy_vs_temp(
        reporting_energy, reporting_temp, rate_is_energy_rate=rate_is_energy_rate
    )
    if rep.empty:
        raise ValueError("no usable reporting-period data")
    savings = avoided_energy_savings(
        model,
        rep["oat"].to_numpy(),
        rep["energy"].to_numpy(),
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=p,
        confidence=confidence,
        rho=st.rho_lag1,
    )

    return NMECResult(
        model_kind=model.kind,
        baseline_cv_rmse=st.cv_rmse,
        baseline_nmbe=st.nmbe,
        baseline_r2=st.r2,
        baseline_n=st.n,
        baseline_rho=st.rho_lag1,
        savings=savings,
        n_non_routine_excluded=excluded,
        baseline_accepted=bool(st.accept),
        cv_rmse_max=cv_rmse_max_for("daily"),
    )


@dataclass
class HourlyNMECResult:
    """Hourly NMEC savings on a TOWT baseline, plus the fit and the shape of the model."""

    baseline_cv_rmse: float
    baseline_nmbe: float
    baseline_r2: float
    baseline_n: int  # baseline HOURS used
    baseline_p: int  # effective parameters (TOWT design rank)
    n_tow_bins: int  # hour-of-week bins present at fit, of 168
    occ_split: bool
    n_temp_segments: int
    savings: SavingsResult
    baseline_accepted: bool  # meets the G14 hourly CV(RMSE) gate
    cv_rmse_max: float  # the gate it was judged against
    baseline_rho: float | None = None
    n_non_routine_days_excluded: int = 0

    def as_dict(self) -> dict:
        """Return the result (with the nested savings) as a plain dict."""
        d = asdict(self)
        d["savings"] = self.savings.as_dict()
        return d


def caltrack_savings_hourly(
    baseline_energy: pd.Series,
    baseline_temp: pd.Series,
    reporting_energy: pd.Series,
    reporting_temp: pd.Series,
    *,
    confidence: float = 0.90,
    min_hours: int = 24 * 60,
    min_obs_per_bin: int = 4,
    rate_is_energy_rate: bool = False,
    exclude_non_routine: bool = False,
    nre_z: float = 3.5,
    n_temp_segments: int = 6,
    occ_split: bool = True,
) -> HourlyNMECResult:
    """Hourly NMEC / IPMVP Option-C avoided energy on a **TOWT** baseline.

    The hourly counterpart to :func:`caltrack_savings`. Fits a Time-of-Week & Temperature model
    (:func:`camber.mandv.towt.fit_towt`, the LBNL Mathieu et al. formulation) on hourly baseline
    energy vs temperature, projects it onto the reporting period, and reports avoided energy with
    the G14 Annex-B band.

    **This is not the CalTRACK Hourly specification.** That method prescribes per-calendar-month
    segmented models, six *fixed* temperature bin edges, and occupancy from a month x hour-of-week
    lookup derived from a preliminary daily model. CAMBER's TOWT is a single pooled model with
    quantile-spaced breakpoints and load-median occupancy. The numbers here are defensible IPMVP
    Option-C savings on a published baseline model; they are not eemeter-comparable, and this
    function does not claim to be.

    **Sufficiency is about coverage, not row count.** A TOWT design has a column per hour-of-week
    bin, so a bin observed once is a fitted level carrying no information. ``min_hours`` sets a
    floor on total length and ``min_obs_per_bin`` on each of the 168 bins; both must pass. (The
    50-observation guard inside ``fit_towt`` is far too weak to be the constraint here.)

    **Expect a band comparable to the daily method, not a tighter one.** Hourly residuals are
    strongly serially correlated -- rho around 0.85 is ordinary -- and the effective-sample-size
    correction bites hard: measured on a 20-week synthetic, n=3360 hours becomes n_eff=282 and the
    band goes from 1.6% at rho=0 to 9.7% at rho=0.85. More data at a finer interval does not buy
    proportionally more certainty, and a band that ignored this would be the overconfident one.

    ``exclude_non_routine`` screens at **day** level, not hour level: hourly residuals are
    heavier-tailed, and trimming individual hours on residual magnitude would bias CV(RMSE) down
    and so narrow the band -- the dishonest direction. Whole days are excluded or none.
    """
    base = hourly_energy_vs_temp(
        baseline_energy, baseline_temp, rate_is_energy_rate=rate_is_energy_rate
    )
    if len(base) < min_hours:
        raise ValueError(f"need >= {min_hours} baseline hours, got {len(base)}")
    _require_bin_coverage(base.index, min_obs_per_bin)

    excluded = 0
    if exclude_non_routine:
        m0 = fit_towt(
            base["energy"], base["oat"], n_temp_segments=n_temp_segments, occ_split=occ_split
        )
        resid = base["energy"].to_numpy() - m0.predict(base.index, base["oat"].to_numpy())
        # screen whole days: an NRE is a day-level event (a shutdown, an occupancy change), and
        # trimming single hours on residual size would quietly narrow the uncertainty band
        daily = pd.Series(resid, index=base.index).groupby(base.index.normalize()).mean()
        bad_days = set(daily.index[outlier_mask(daily, cutoff=nre_z).to_numpy()])
        if bad_days:
            keep = ~pd.Index(base.index.normalize()).isin(bad_days)
            excluded = int(len(bad_days))
            base = base[keep]
            _require_bin_coverage(base.index, min_obs_per_bin)

    model = fit_towt(
        base["energy"], base["oat"], n_temp_segments=n_temp_segments, occ_split=occ_split
    )
    st = fit_stats(
        base["energy"].to_numpy(),
        model.predict(base.index, base["oat"].to_numpy()),
        model.n_params,
        cv_rmse_max=cv_rmse_max_for("hourly"),
        time_index=base.index,
    )

    rep = hourly_energy_vs_temp(
        reporting_energy, reporting_temp, rate_is_energy_rate=rate_is_energy_rate
    )
    if rep.empty:
        raise ValueError("no usable reporting-period data")
    if not model.covers(rep.index):
        raise ValueError(
            "the baseline does not cover the reporting period's hour-of-week schedule; "
            "projecting anyway would silently under-predict the baseline (see TOWTModel.predict)"
        )

    savings = avoided_energy_savings(
        TOWTAtIndex(model, rep.index),
        rep["oat"].to_numpy(),
        rep["energy"].to_numpy(),
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=model.n_params,
        confidence=confidence,
        rho=st.rho_lag1,
    )

    return HourlyNMECResult(
        baseline_cv_rmse=st.cv_rmse,
        baseline_nmbe=st.nmbe,
        baseline_r2=st.r2,
        baseline_n=st.n,
        baseline_p=model.n_params,
        n_tow_bins=int(len(model.bins)),
        occ_split=bool(model.split),
        n_temp_segments=int(len(model.breakpoints) - 1),
        savings=savings,
        baseline_accepted=bool(st.accept),
        cv_rmse_max=cv_rmse_max_for("hourly"),
        baseline_rho=st.rho_lag1,
        n_non_routine_days_excluded=excluded,
    )


def _require_bin_coverage(index, min_obs_per_bin: int) -> None:
    """Raise unless every hour-of-week bin present carries at least ``min_obs_per_bin`` samples."""
    tow = hour_of_week(pd.DatetimeIndex(index))
    present, counts = np.unique(tow, return_counts=True)
    if len(present) < 168:
        raise ValueError(
            f"baseline covers only {len(present)} of 168 hour-of-week bins; a TOWT baseline "
            "cannot project onto the hours it never saw"
        )
    thin = int((counts < min_obs_per_bin).sum())
    if thin:
        raise ValueError(
            f"{thin} hour-of-week bin(s) have fewer than {min_obs_per_bin} observations; "
            "each bin is a fitted level, so a thinly-observed one carries no information"
        )
