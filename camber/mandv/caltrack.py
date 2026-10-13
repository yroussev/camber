"""Whole-building NMEC savings on CAMBER's own baselines, and the deprecated CalTRACK-named entry
points (0.105, #156).

CAMBER's M&V pieces implement the IPMVP Option-C *normalized metered energy consumption*
workflow; this module assembles them into one call per interval:

| function                      | baseline model                                              |
|-------------------------------|-------------------------------------------------------------|
| `daily_changepoint_savings`   | `models.best_model` change-point on daily energy (BIC pick) |
| `towt_savings_hourly`         | `towt.fit_towt`, the LBNL time-of-week & temperature model  |

Both report avoided energy with the ASHRAE Guideline 14 Annex-B band
(`stats.avoided_energy_savings`). **Neither is a CalTRACK method.** The faithful CalTRACK 2.0
Daily, Billing and Hourly methods are :func:`camber.mandv.caltrack_daily.caltrack_daily_savings`,
:func:`~camber.mandv.caltrack_daily.caltrack_billing_savings` and
:func:`camber.mandv.caltrack_hourly.caltrack_hourly_savings`.

Until 0.105 the two functions here were called ``caltrack_savings`` and
``caltrack_savings_hourly`` (results ``NMECResult`` and ``HourlyNMECResult``). Those names stay as
deprecated aliases that return the same numbers and warn (removal in 1.0); they were never
CalTRACK-comparable, which is why they were renamed rather than repointed.
"""

from __future__ import annotations

import inspect
import warnings
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np
import pandas as pd

from .._deprecation import deprecated
from ..ingest.quality import outlier_mask
from .coverage import ExtrapolationPolicy
from .intervalfit import _frame_caveats, daily_energy_vs_temp, hourly_energy_vs_temp
from .models import N_PARAMS, best_model
from .nonroutine import residual_outliers
from .stats import SavingsResult, avoided_energy_savings, cv_rmse_max_for, fit_stats
from .sufficiency import InsufficientBaseline, baseline_need
from .towt import TOWTAtIndex, _check_unseen, fit_towt, hour_of_week


@dataclass
class DailyNMECResult:
    """Whole-building NMEC savings on a daily change-point baseline: the fit plus avoided energy
    (the result of :func:`daily_changepoint_savings`; called ``NMECResult`` before 0.105)."""

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


def daily_changepoint_savings(
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
    extrapolation: ExtrapolationPolicy | None = None,
    min_coverage: float = 0.9,
    timezone: str | None = None,
) -> DailyNMECResult:
    """IPMVP Option-C avoided energy use on a daily change-point baseline.

    Fits a change-point baseline on daily energy vs daily-mean temperature (the best of the
    2P..5P families by BIC, :func:`camber.mandv.models.best_model`), projects it onto the
    reporting period's weather, and reports avoided energy use with ASHRAE G14 Annex-B
    fractional savings uncertainty. **This is not CalTRACK Daily** (no balance-point grid search,
    adjusted-R^2 selection or CalTRACK sufficiency rules): for that, use
    :func:`camber.mandv.caltrack_daily.caltrack_daily_savings`. Called ``caltrack_savings``
    before 0.105; the old name is a deprecated alias. ``*_energy`` are interval
    meter series and ``*_temp`` the matching outdoor temperatures;
    ``rate_is_energy_rate`` marks energy that is a rate (BTU/hr) rather than per-
    interval energy.

    With ``exclude_non_routine`` the baseline is screened for non-routine events
    (days whose residual is a robust outlier at modified-z > ``nre_z``); those days
    are dropped and the baseline refit, so a shutdown or anomaly doesn't skew it.

    The reporting period's temperatures are checked against the baseline's fitted range
    (``extrapolation``, see :func:`camber.mandv.stats.avoided_energy_savings`): a severe
    extrapolation declines the saving by default. A baseline shorter than 365 days carries a
    caveat, since it cannot span a full weather year. (CalTRACK itself prescribes no extrapolation
    test; this is a CAMBER policy.)

    A baseline under ``min_days`` raises :class:`camber.mandv.sufficiency.InsufficientBaseline`
    (a ``ValueError``, message unchanged) whose ``.need`` says how much more data is needed.

    Days whose meter data cover less than ``min_coverage`` of the day are dropped from both periods
    and temperatures outside the plausible outdoor range are treated as missing (0.105; see
    :func:`~camber.mandv.intervalfit.daily_energy_vs_temp`, which also takes ``timezone``); the
    savings caveats count both.
    """
    kw: dict[str, Any] = {
        "rate_is_energy_rate": rate_is_energy_rate,
        "min_coverage": min_coverage,
        "timezone": timezone,
    }
    base = daily_energy_vs_temp(baseline_energy, baseline_temp, **kw)
    notes = _frame_caveats(base, "baseline")
    if len(base) < min_days:
        raise InsufficientBaseline(
            f"need >= {min_days} baseline days, got {len(base)}",
            baseline_need("daily", len(base), min_n=min_days),
        )

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

    rep = daily_energy_vs_temp(reporting_energy, reporting_temp, **kw)
    notes += _frame_caveats(rep, "reporting")
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
        extrapolation=extrapolation,
    )
    span = (base.index.max() - base.index.min()).days + 1 if len(base) else 0
    if span < 365:
        savings.caveats.append(
            f"the baseline spans {span} days (< 365): it cannot cover a full weather year, so "
            "reporting conditions outside it are more likely"
        )
    savings.caveats.extend(notes)

    return DailyNMECResult(
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
class TOWTHourlyResult:
    """Hourly NMEC savings on a TOWT baseline, plus the fit and the shape of the model (the result
    of :func:`towt_savings_hourly`; called ``HourlyNMECResult`` before 0.105).

    ``model`` (0.105) is the fitted :class:`~camber.mandv.towt.TOWTModel`, so the baseline can be
    projected again (``model.predict(index, temp)``); it is not part of :meth:`as_dict`."""

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
    model: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Return the result (with the nested savings, without the model) as a plain dict."""
        d = {}
        for f in fields(self):
            if f.name == "model":
                continue
            v = getattr(self, f.name)
            d[f.name] = v.as_dict() if f.name == "savings" else v
        return d


def towt_savings_hourly(
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
    extrapolation: ExtrapolationPolicy | None = None,
    timezone: str | None = None,
    unseen: str = "raise",
    clip_negative: bool = False,
) -> TOWTHourlyResult:
    """Hourly NMEC / IPMVP Option-C avoided energy on a **TOWT** baseline.

    The hourly counterpart to :func:`daily_changepoint_savings`. Called
    ``caltrack_savings_hourly`` before 0.105; the old name is a deprecated alias. The result
    carries the fitted model (``result.model``). Fits a Time-of-Week & Temperature model
    (:func:`camber.mandv.towt.fit_towt`, the LBNL Mathieu et al. formulation) on hourly baseline
    energy vs temperature, projects it onto the reporting period, and reports avoided energy with
    the G14 Annex-B band.

    **This is not the CalTRACK Hourly specification.** That method prescribes per-calendar-month
    segmented models, six *fixed* temperature bin edges, and occupancy from a month x hour-of-week
    lookup derived from a preliminary model's residuals; CAMBER implements it as
    :func:`camber.mandv.caltrack_hourly.caltrack_hourly_savings`. The TOWT here is a single pooled
    model with quantile-spaced breakpoints and load-median occupancy. The numbers are defensible
    IPMVP Option-C savings on a published baseline model; they are not eemeter-comparable, and
    this function does not claim to be.

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

    Reporting hours are checked against the baseline's (occupancy mode x temperature cell) support
    (:func:`camber.mandv.coverage.towt_coverage`); a severe extrapolation declines by default. TOWT
    holds its temperature response flat beyond the fitted range, so it is flagged but never
    widened.

    A baseline under ``min_hours``, missing hour-of-week bins, or with bins under
    ``min_obs_per_bin`` raises :class:`camber.mandv.sufficiency.InsufficientBaseline` (a
    ``ValueError``, message unchanged) whose ``.need`` says how much more data is needed, and a
    reporting period in hour-of-week bins the baseline never saw raises ``ValueError``.
    ``unseen="fallback"`` (0.105, #154; opt-in) accepts a baseline missing some bins (each bin
    present still needs ``min_obs_per_bin``) and predicts reporting hours in them by
    :meth:`~camber.mandv.towt.TOWTModel.predict`'s fallback rule; coverage grades those hours
    unsupported and the savings caveats count them. ``clip_negative=True`` (0.105, #155;
    opt-in) sets negative reporting projections to zero; the band stays on the unclipped
    projection and a caveat counts them.

    ``timezone`` (0.105, #158) places the hours on the site's clock, so a daylight-saving
    fall-back hour is two hours, not one counted twice (see
    :func:`~camber.mandv.intervalfit.hourly_energy_vs_temp`); without it duplicate stamps are
    averaged into one hour. Implausible temperatures are treated as missing. The savings caveats
    count what either step merged or dropped.
    """
    kw: dict[str, Any] = {"rate_is_energy_rate": rate_is_energy_rate, "timezone": timezone}
    base = hourly_energy_vs_temp(baseline_energy, baseline_temp, **kw)
    notes = _frame_caveats(base, "baseline")
    if len(base) < min_hours:
        raise InsufficientBaseline(
            f"need >= {min_hours} baseline hours, got {len(base)}",
            baseline_need("hourly", len(base), min_n=min_hours),
        )
    allow_missing = _check_unseen(unseen) == "fallback"
    _require_bin_coverage(base.index, min_obs_per_bin, allow_missing=allow_missing)

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
            _require_bin_coverage(base.index, min_obs_per_bin, allow_missing=allow_missing)

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

    rep = hourly_energy_vs_temp(reporting_energy, reporting_temp, **kw)
    notes += _frame_caveats(rep, "reporting")
    if rep.empty:
        raise ValueError("no usable reporting-period data")
    if not allow_missing and not model.covers(rep.index):
        raise ValueError(
            "the baseline does not cover the reporting period's hour-of-week schedule; "
            "projecting anyway would silently under-predict the baseline (see TOWTModel.predict)"
        )

    savings = avoided_energy_savings(
        TOWTAtIndex(model, rep.index, unseen=unseen, clip_negative=clip_negative),
        rep["oat"].to_numpy(),
        rep["energy"].to_numpy(),
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=model.n_params,
        confidence=confidence,
        rho=st.rho_lag1,
        extrapolation=extrapolation,
    )
    if len(model.bins) < 168:
        savings.caveats.append(
            f"the baseline covers {len(model.bins)} of 168 hour-of-week bins (unseen='fallback')"
        )
    savings.caveats.extend(notes)

    return TOWTHourlyResult(
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
        model=model,
    )


def _require_bin_coverage(index, min_obs_per_bin: int, *, allow_missing: bool = False) -> None:
    """Raise unless every hour-of-week bin present carries at least ``min_obs_per_bin`` samples
    (and, unless ``allow_missing``, all 168 bins are present)."""
    tow = hour_of_week(pd.DatetimeIndex(index))
    present, counts = np.unique(tow, return_counts=True)
    if len(present) < 168 and not allow_missing:
        raise InsufficientBaseline(
            f"baseline covers only {len(present)} of 168 hour-of-week bins; a TOWT baseline "
            "cannot project onto the hours it never saw",
            baseline_need("hourly", len(present), min_n=168, unit="hour-of-week bins"),
        )
    thin = int((counts < min_obs_per_bin).sum())
    if thin:
        # each further week of data adds one observation to every bin
        raise InsufficientBaseline(
            f"{thin} hour-of-week bin(s) have fewer than {min_obs_per_bin} observations; "
            "each bin is a fitted level, so a thinly-observed one carries no information",
            baseline_need(
                "hourly",
                int(counts.min()),
                min_n=min_obs_per_bin,
                unit="observations per hour-of-week bin",
                days_per_unit=7.0,
            ),
        )


# --------------------------------------------------------------------------- deprecated names
# The pre-0.105 names. Each returns exactly what its renamed function returns, as an instance of
# the old class (a subclass of the new one, so isinstance() checks against either name hold).


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.caltrack.DailyNMECResult")
class NMECResult(DailyNMECResult):
    """Deprecated name of :class:`DailyNMECResult`."""


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.caltrack.TOWTHourlyResult")
class HourlyNMECResult(TOWTHourlyResult):
    """Deprecated name of :class:`TOWTHourlyResult`."""


def _as_legacy(cls, res):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return cls(**{f.name: getattr(res, f.name) for f in fields(res)})


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.caltrack.daily_changepoint_savings")
def caltrack_savings(*args, **kwargs) -> DailyNMECResult:
    """Deprecated name of :func:`daily_changepoint_savings` (a daily change-point baseline, not
    CalTRACK Daily: see :func:`camber.mandv.caltrack_daily.caltrack_daily_savings`)."""
    return _as_legacy(NMECResult, daily_changepoint_savings(*args, **kwargs))


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.caltrack.towt_savings_hourly")
def caltrack_savings_hourly(*args, **kwargs) -> TOWTHourlyResult:
    """Deprecated name of :func:`towt_savings_hourly` (a pooled TOWT baseline, not CalTRACK
    Hourly: see :func:`camber.mandv.caltrack_hourly.caltrack_hourly_savings`)."""
    return _as_legacy(HourlyNMECResult, towt_savings_hourly(*args, **kwargs))


caltrack_savings.__signature__ = inspect.signature(daily_changepoint_savings)  # type: ignore[attr-defined]
caltrack_savings_hourly.__signature__ = inspect.signature(towt_savings_hourly)  # type: ignore[attr-defined]
