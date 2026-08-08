"""Load-normalized chiller baselines: fit ``metric ~ f(tons)`` and score drift against it.

A chiller's condenser/evaporator **approach** -- the gap between refrigerant saturation temperature
and the water leaving that heat exchanger -- widens as tubes foul. That is the classic degradation
indicator, but it is only readable if you account for **load**: approach widens with tons all by
itself, so a lightly-loaded shoulder month and a peak-summer month are not comparable as raw levels.
:mod:`camber.rules.chiller_approach_rule` compares a whole-window median against a static design
constant, which answers "is the approach high?" but never "has it been climbing?" -- a chiller at
8 degF since commissioning and one that walked 4 -> 8 degF over six weeks score identically there.

This module supplies the missing piece: a **fitted baseline** of the metric against load, retaining
the residual scatter ``sigma_f``, so a later period can be scored *at matched load*. Two units are
therefore separable -- a stable chiller's later readings sit on its own baseline line (drift ~ 0),
while a fouling one sits progressively above it, in degF and in sigma.

**The fit is metric-neutral.** :func:`fit_load_baseline` takes whichever column carries the signal;
approach was simply the first consumer. Liquid-line subcooling and condenser-water range are
load-dependent in exactly the same way and need exactly the same treatment, so they share the fit
rather than each growing their own copy. :func:`fit_approach_baseline` and
:func:`fit_subcooling_baseline` are thin, behaviour-identical wrappers that name the two cases whose
argument spellings predate the generalization.

The fit is ordinary least squares of degree 1: the metric-vs-load relation is close to linear across
a chiller's operating band, and two parameters stay stable on the few hundred to few thousand hourly
samples a month of trend data yields. Guards mirror :mod:`camber.chiller` -- trivial-load and
non-physical intervals are dropped -- and a fit that cannot be identified returns ``None`` rather
than a fabricated line, per the honesty convention in :mod:`camber.rules.base`.

:meth:`LoadBaseline.predict` deliberately duck-types the ``predict`` callable that
:class:`camber.mandv.online.OnlineCusum` expects, so the same baseline can drive a streaming
sustained-shift alarm with no adapter. Note that ``OnlineCusum`` accumulates ``predicted - actual``,
so a *widening* approach registers on its ``low`` accumulator.

Dependency-light: numpy + pandas only. See ``docs/proposals/chiller_drift_detection_plan.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "ApproachBaseline",
    "ApproachDrift",
    "LoadBaseline",
    "LoadDrift",
    "fit_approach_baseline",
    "fit_load_baseline",
    "fit_subcooling_baseline",
    "drift_stats",
    "load_drift_stats",
    "tons_from_flow",
]

_DAYS_PER_MONTH = 30.4375  # mean Gregorian month, for degF/month trends


def tons_from_flow(
    frame: pd.DataFrame,
    *,
    flow_col: str = "CHW_Flow",
    supply_col: str = "CHWS_Temp",
    return_col: str = "CHWR_Temp",
) -> pd.Series:
    """Derive cooling output in tons from chilled-water flow and loop dT.

    ``tons = gpm * (CHWR - CHWS) / 24`` -- the same convention
    :func:`camber.chiller.analyze_chiller_efficiency` uses (500 * dT * gpm / 12000 BTU per ton).
    There is no chiller-tons :class:`~camber.model.roles.Role`, so load is derived; callers with a
    metered tons point should pass that series to the fit directly instead.
    """
    need = (flow_col, supply_col, return_col)
    missing = [c for c in need if c not in frame.columns]
    if missing:
        raise KeyError(f"tons_from_flow needs column(s) {missing}")
    dt = frame[return_col] - frame[supply_col]
    return frame[flow_col] * dt / 24.0


@dataclass
class LoadBaseline:
    """A fitted ``metric = intercept + slope * tons`` line plus its residual scatter.

    ``sigma_f`` is the residual standard deviation in degF -- the natural scale for judging whether
    a later reading is meaningfully off the line. ``tons_min``/``tons_max`` record the load
    envelope the fit was identified on, so a caller can decline to extrapolate beyond it.

    The field names carry ``tons`` and ``_f`` because load is always in tons and every metric this
    fits so far is a temperature difference in degF. They are also the on-disk keys of a frozen
    :class:`camber.store.modelstore.BaselineRecord`, so they stay put across the generalization.
    """

    n: int  # samples retained after guards
    slope_f_per_ton: float  # degF of metric per ton of load
    intercept_f: float  # degF at zero load (extrapolated; not a physical reading)
    sigma_f: float  # residual standard deviation, degF
    r2: float  # coefficient of determination of the fit
    tons_min: float  # fitted load envelope, low end
    tons_max: float  # fitted load envelope, high end
    coverage_start: str = ""
    coverage_end: str = ""

    def predict(self, tons):
        """Expected metric (degF) at ``tons``; scalar in -> float out, array in -> array out.

        Duck-types the ``predict`` callable :class:`camber.mandv.online.OnlineCusum` takes.
        """
        arr = np.asarray(tons, dtype=float)
        out = self.intercept_f + self.slope_f_per_ton * arr
        return float(out) if arr.ndim == 0 else out

    def residual(self, tons, metric_f):
        """Actual minus expected metric (degF). **Positive means above the baseline line.**"""
        actual = np.asarray(metric_f, dtype=float)
        out = actual - np.asarray(self.predict(tons), dtype=float)
        return float(out) if out.ndim == 0 else out

    def z(self, tons, metric_f):
        """:meth:`residual` in baseline residual sigmas (NaN if the fit had no scatter)."""
        resid = np.asarray(self.residual(tons, metric_f), dtype=float)
        out = resid / self.sigma_f if self.sigma_f > 0 else np.full_like(resid, np.nan)
        return float(out) if out.ndim == 0 else out

    def covers(self, tons) -> bool:
        """Whether ``tons`` falls inside the load envelope the baseline was fitted on."""
        return bool(self.tons_min <= float(tons) <= self.tons_max)

    def as_dict(self) -> dict:
        """Return the baseline as a plain dict (JSON-friendly; see :meth:`from_dict`)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LoadBaseline:
        """Rebuild a baseline from :meth:`as_dict` output (the model-persistence contract)."""
        return cls(**d)


@dataclass
class LoadDrift:
    """How far a period's metric sits off its fitted baseline, at matched load."""

    n_current: int  # samples scored after guards
    drift_f: float  # median residual vs the baseline fit, degF (+ = above the fitted line)
    drift_sigma: float  # drift_f in baseline sigmas (NaN if the baseline had no scatter)
    slope_f_per_month: float  # trend of the residual within the period (NaN if untimed)
    pct_outside_2sigma: float  # % of scored samples at |z| >= 2
    extrapolated: bool  # >10% of the period's load fell outside the fitted envelope
    coverage_start: str = ""
    coverage_end: str = ""

    def as_dict(self) -> dict:
        """Return the drift statistics as a plain dict."""
        return asdict(self)


# The names the approach detectors were written against, kept so no caller (or pickle of a
# ``kind`` -> model-class mapping) breaks. They are the same objects, not subclasses.
ApproachBaseline = LoadBaseline
ApproachDrift = LoadDrift


def _coverage(index) -> tuple[str, str]:
    """Coverage stamps for a frame index (empty strings when it isn't a time index)."""
    if isinstance(index, pd.DatetimeIndex) and len(index):
        return str(index.min()), str(index.max())
    return "", ""


def _clean(
    frame: pd.DataFrame,
    metric_col,
    load_col,
    *,
    min_load: float,
    metric_range: tuple[float, float],
) -> pd.DataFrame:
    """Rows usable for fitting or scoring: both points present, real load, physical metric.

    Below ``min_load`` the chiller is effectively unloaded and the metric carries no information
    about equipment condition; the metric bounds drop sensor dropouts and impossible values.
    Returns a two-column frame named ``tons``/``metric``, preserving the original index.
    """
    if metric_col not in frame.columns or load_col not in frame.columns:
        return pd.DataFrame(columns=["tons", "metric"])
    w = pd.DataFrame(
        {
            "tons": pd.to_numeric(frame[load_col], errors="coerce"),
            "metric": pd.to_numeric(frame[metric_col], errors="coerce"),
        },
        index=frame.index,
    ).dropna()
    lo, hi = metric_range
    return w[(w["tons"] >= min_load) & w["metric"].between(lo, hi)]


def fit_load_baseline(
    frame: pd.DataFrame,
    *,
    metric_col,
    load_col: str = "tons",
    min_load: float = 5.0,  # below this the metric carries no condition information
    metric_range: tuple[float, float] = (0.0, 50.0),  # physical-ish degF bounds
    min_samples: int = 30,  # too few points and the slope isn't identified
    min_load_span: float = 10.0,  # too narrow a load range and the slope isn't identified
) -> LoadBaseline | None:
    """Fit ``metric ~ intercept + slope * load`` over a baseline period; retain residual sigma.

    The metric-neutral core of this module: condenser/evaporator approach, liquid-line subcooling
    and condenser-water range are all load-dependent degF signals and all fit the same way. Only
    ``metric_col`` and the plausibility bounds change between them.

    ``frame`` is indexed by time and carries a metric column and a load column (derive the latter
    with :func:`tons_from_flow` when tons aren't metered). Column keys may be strings or
    :class:`~camber.model.roles.Role` members -- whatever the frame is keyed by.

    Returns ``None`` -- never a fabricated fit -- when the guards leave too few samples, when the
    observed load range is too narrow to identify a slope, or when the fit has no residual degrees
    of freedom. A caller must treat ``None`` as "could not evaluate", not as "no drift".
    """
    w = _clean(frame, metric_col, load_col, min_load=min_load, metric_range=metric_range)
    if len(w) < max(min_samples, 3):
        return None
    x = w["tons"].to_numpy(dtype=float)
    y = w["metric"].to_numpy(dtype=float)
    if float(x.max() - x.min()) < min_load_span:
        return None

    slope, intercept = (float(v) for v in np.polyfit(x, y, 1))
    resid = y - (intercept + slope * x)
    dof = len(w) - 2
    if dof < 1:
        return None
    sigma = float(np.sqrt(float(resid @ resid) / dof))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else float("nan")
    start, end = _coverage(w.index)

    return LoadBaseline(
        n=int(len(w)),
        slope_f_per_ton=round(slope, 6),
        intercept_f=round(intercept, 4),
        sigma_f=round(sigma, 4),
        r2=round(r2, 4) if r2 == r2 else float("nan"),
        tons_min=round(float(x.min()), 2),
        tons_max=round(float(x.max()), 2),
        coverage_start=start,
        coverage_end=end,
    )


def fit_approach_baseline(
    frame: pd.DataFrame,
    *,
    approach_col="approach_f",
    tons_col: str = "tons",
    min_tons: float = 5.0,
    approach_range: tuple[float, float] = (0.0, 50.0),
    min_samples: int = 30,
    min_tons_span: float = 10.0,
) -> LoadBaseline | None:
    """Fit an approach baseline: :func:`fit_load_baseline` under the approach argument spelling.

    Behaviour-identical to the generic fit; kept so callers written before the generalization keep
    working unchanged.
    """
    return fit_load_baseline(
        frame,
        metric_col=approach_col,
        load_col=tons_col,
        min_load=min_tons,
        metric_range=approach_range,
        min_samples=min_samples,
        min_load_span=min_tons_span,
    )


def fit_subcooling_baseline(
    frame: pd.DataFrame,
    *,
    subcooling_col="subcooling_temp",
    tons_col: str = "tons",
    min_tons: float = 5.0,
    subcooling_range: tuple[float, float] = (0.0, 50.0),
    min_samples: int = 30,
    min_tons_span: float = 10.0,
) -> LoadBaseline | None:
    """Fit a liquid-line subcooling baseline: :func:`fit_load_baseline`, named for its metric.

    Behaviour-identical to the generic fit. The default column is the value of
    :attr:`camber.model.roles.Role.SUBCOOLING_TEMP`, which compares equal to it (``Role`` is a
    ``str`` enum), so a frame keyed either way resolves.
    """
    return fit_load_baseline(
        frame,
        metric_col=subcooling_col,
        load_col=tons_col,
        min_load=min_tons,
        metric_range=subcooling_range,
        min_samples=min_samples,
        min_load_span=min_tons_span,
    )


def _residual_slope_per_month(index, resid: np.ndarray) -> float:
    """OLS slope of ``resid`` against time, in degF per month (NaN when time isn't usable)."""
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return float("nan")
    days = (index - index[0]).total_seconds().to_numpy(dtype=float) / 86400.0
    if float(days.max() - days.min()) <= 0:
        return float("nan")
    slope = float(np.polyfit(days, resid, 1)[0])
    return round(slope * _DAYS_PER_MONTH, 4)


def load_drift_stats(
    baseline: LoadBaseline,
    frame: pd.DataFrame,
    *,
    metric_col,
    load_col: str = "tons",
    min_load: float = 5.0,
    metric_range: tuple[float, float] = (0.0, 50.0),
    min_samples: int = 10,
) -> LoadDrift | None:
    """Score a current period against a fitted ``baseline``; return the drift statistics.

    ``drift_f`` is the **median** residual (actual minus baseline-predicted metric at the same
    load) -- median rather than mean so a handful of dropouts or a short spike doesn't set the
    headline number. ``slope_f_per_month`` is the residual's own trend inside the period, which
    separates "stepped up and stayed" from "still climbing".

    Because every comparison happens at matched load, a period that is simply *busier* than the
    baseline scores near zero drift -- which a level-vs-level comparison cannot do.

    Returns ``None`` when the guards leave fewer than ``min_samples`` scoreable rows.
    """
    w = _clean(frame, metric_col, load_col, min_load=min_load, metric_range=metric_range)
    if len(w) < min_samples:
        return None
    tons = w["tons"].to_numpy(dtype=float)
    resid = np.asarray(baseline.residual(tons, w["metric"].to_numpy(dtype=float)), dtype=float)
    drift_f = float(np.median(resid))
    sigma = baseline.sigma_f
    outside = float(np.mean(np.abs(resid) >= 2.0 * sigma)) if sigma > 0 else float("nan")
    off_envelope = float(np.mean((tons < baseline.tons_min) | (tons > baseline.tons_max)))
    start, end = _coverage(w.index)

    return LoadDrift(
        n_current=int(len(w)),
        drift_f=round(drift_f, 4),
        drift_sigma=round(drift_f / sigma, 4) if sigma > 0 else float("nan"),
        slope_f_per_month=_residual_slope_per_month(w.index, resid),
        pct_outside_2sigma=round(100.0 * outside, 2) if outside == outside else float("nan"),
        extrapolated=bool(off_envelope > 0.10),
        coverage_start=start,
        coverage_end=end,
    )


def drift_stats(
    baseline: LoadBaseline,
    frame: pd.DataFrame,
    *,
    approach_col="approach_f",
    tons_col: str = "tons",
    min_tons: float = 5.0,
    approach_range: tuple[float, float] = (0.0, 50.0),
    min_samples: int = 10,
) -> LoadDrift | None:
    """Score approach drift: :func:`load_drift_stats` under the approach argument spelling.

    Behaviour-identical to the generic scorer; kept for callers written before the generalization.
    """
    return load_drift_stats(
        baseline,
        frame,
        metric_col=approach_col,
        load_col=tons_col,
        min_load=min_tons,
        metric_range=approach_range,
        min_samples=min_samples,
    )
