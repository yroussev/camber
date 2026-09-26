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

**Machine size, fixed-capacity machines and a second regressor.** The load gates were written as
absolute tons (5 t floor, 10 t identifiable span) -- 10 % / 20 % of a 50-ton machine. On a 5-ton
chiller or a 3-ton heat pump those gates discard everything, so :func:`size_relative_load_gates`
scales them to the observed capacity and never exceeds the absolute values, which leaves every
machine of 50 t and up exactly where it was. A machine whose load does not move (fixed-capacity,
single-stage) cannot identify a slope at all; ``level_fallback=True`` then fits a flat level valid
only inside the load band it was observed at (see :meth:`LoadBaseline.in_scope`). Some metrics are
driven as much by a second, measured condition as by load -- head pressure by the entering
condenser-water (or outdoor-air) temperature, suction pressure by the leaving chilled-water
temperature -- so ``covariate_col`` adds that regressor to the fit and every score is then made at
matched load *and* matched condition.

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
    "residual_lag1",
    "size_relative_load_gates",
    "tons_from_flow",
    "unscoreable_reason",
    "FULL_SIZE_MIN_LOAD",
    "FULL_SIZE_MIN_LOAD_SPAN",
]

_DAYS_PER_MONTH = 30.4375  # mean Gregorian month, for degF/month trends

# The absolute load gates the chiller detectors were written with. They are 10 % and 20 % of a
# 50-ton machine -- the small end of the commercial chiller range -- so
# :func:`size_relative_load_gates` keeps those fractions for smaller machines and never goes above
# these values for larger ones.
FULL_SIZE_MIN_LOAD = 5.0  # tons: below this a full-size chiller is effectively unloaded
FULL_SIZE_MIN_LOAD_SPAN = 10.0  # tons: narrower than this and a slope is not identified
_LOAD_GATE_FRACTION = 0.10  # of observed capacity -- 5 t of a 50 t machine
_SPAN_GATE_FRACTION = 0.20  # of observed capacity -- 10 t of a 50 t machine
_CAPACITY_PERCENTILE = 95.0  # "observed capacity": robust to a few over-range samples


def size_relative_load_gates(
    load,
    *,
    min_load: float | None = None,
    min_load_span: float | None = None,
    full_min_load: float = FULL_SIZE_MIN_LOAD,
    full_min_load_span: float = FULL_SIZE_MIN_LOAD_SPAN,
) -> tuple[float, float]:
    """The (minimum load, minimum identifiable load span) gates for a machine of this size.

    ``load`` is the baseline period's load series (tons). Its observed capacity -- the 95th
    percentile of the positive samples -- sets the gates at 10 % and 20 % of capacity, **capped at**
    ``full_min_load`` / ``full_min_load_span`` (5 t / 10 t). The cap is what keeps large-machine
    behaviour unchanged: any machine of 50 t or more gets exactly the absolute gates it always had,
    while a 5-ton chiller gets 0.5 t / 1 t instead of gates it can never meet.

    An explicit ``min_load`` / ``min_load_span`` always wins (a caller who knows the machine's rated
    capacity should pass gates derived from it). With no usable load samples the absolute gates are
    returned -- the fit will then decline on its own, naming why.
    """
    vals = pd.to_numeric(pd.Series(np.asarray(load, dtype=float).ravel()), errors="coerce")
    vals = vals[np.isfinite(vals) & (vals > 0)]
    if len(vals):
        cap = float(np.percentile(vals.to_numpy(dtype=float), _CAPACITY_PERCENTILE))
        rel_load = min(full_min_load, _LOAD_GATE_FRACTION * cap)
        rel_span = min(full_min_load_span, _SPAN_GATE_FRACTION * cap)
    else:
        rel_load, rel_span = full_min_load, full_min_load_span
    return (
        float(min_load) if min_load is not None else round(rel_load, 6),
        float(min_load_span) if min_load_span is not None else round(rel_span, 6),
    )


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

    Two optional extensions, both off by default (and absent from records frozen before them):

    * ``load_model="level"`` -- a flat level (``slope_f_per_ton == 0``) fitted because the
      baseline's load never moved enough to identify a slope. It is only claimed valid inside
      ``[tons_min - load_band, tons_max + load_band]``; :meth:`in_scope` says which loads that is.
    * ``covariate`` -- a second regressor (e.g. entering condenser-water temperature).
      ``covariate_slope`` is metric units per covariate unit, referred to ``covariate_ref`` (the
      baseline mean), so ``predict(tons)`` alone is the expectation *at the reference condition*
      and ``predict(tons, covariate)`` the expectation at the actual one.

    ``resid_lag1`` / ``sample_seconds`` record how serially correlated the baseline residuals were
    at the baseline's sampling interval (see :func:`residual_lag1`); the streaming CUSUM uses them
    so that one slow excursion sampled every minute is not counted as sixty independent ones.
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
    load_model: str = "linear"  # "linear" | "level" (flat; see in_scope)
    load_band: float = 0.0  # level model: load pad either side of the fitted envelope
    covariate: str = ""  # name of the second regressor; "" = load-only
    covariate_slope: float = 0.0  # metric per covariate unit
    covariate_ref: float = 0.0  # covariate value the intercept is referred to (baseline mean)
    covariate_min: float = 0.0  # fitted covariate envelope, low end
    covariate_max: float = 0.0  # fitted covariate envelope, high end
    resid_lag1: float = (
        0.0  # lag-1 autocorrelation of the residuals (0 = not significant / unknown)
    )
    sample_seconds: float = 0.0  # the baseline's sampling interval that resid_lag1 refers to

    def predict(self, tons, covariate=None):
        """Expected metric (degF) at ``tons``; scalar in -> float out, array in -> array out.

        With a covariate baseline and ``covariate`` given, the expectation at that condition;
        without it, the expectation at the reference condition ``covariate_ref``. Duck-types the
        ``predict`` callable :class:`camber.mandv.online.OnlineCusum` takes (load only).
        """
        arr = np.asarray(tons, dtype=float)
        out = self.intercept_f + self.slope_f_per_ton * arr
        if covariate is not None and self.covariate:
            cov = np.asarray(covariate, dtype=float)
            out = out + self.covariate_slope * (cov - self.covariate_ref)
        out = np.asarray(out, dtype=float)
        return float(out) if out.ndim == 0 else out

    def adjust(self, metric_f, covariate):
        """``metric_f`` referred to the reference condition (identity for a load-only baseline).

        ``metric - covariate_slope * (covariate - covariate_ref)`` -- the reading this machine
        would have shown at the baseline's mean condition, which is what :meth:`predict` with no
        covariate predicts. The streaming alarm scores this adjusted series.
        """
        m = np.asarray(metric_f, dtype=float)
        if self.covariate:
            m = m - self.covariate_slope * (np.asarray(covariate, dtype=float) - self.covariate_ref)
        return float(m) if m.ndim == 0 else m

    def residual(self, tons, metric_f, covariate=None):
        """Actual minus expected metric (degF). **Positive means above the baseline line.**"""
        actual = np.asarray(metric_f, dtype=float)
        out = actual - np.asarray(self.predict(tons, covariate), dtype=float)
        return float(out) if out.ndim == 0 else out

    def z(self, tons, metric_f, covariate=None):
        """:meth:`residual` in baseline residual sigmas (NaN if the fit had no scatter)."""
        resid = np.asarray(self.residual(tons, metric_f, covariate), dtype=float)
        out = resid / self.sigma_f if self.sigma_f > 0 else np.full_like(resid, np.nan)
        return float(out) if out.ndim == 0 else out

    def in_scope(self, tons):
        """Which loads this baseline may score: all of them, except for a flat ``level`` model.

        A level baseline was fitted where load did not move, so it says nothing about the metric
        at a different load; it is only scored inside its envelope widened by ``load_band``.
        """
        arr = np.asarray(tons, dtype=float)
        if self.load_model != "level":
            out = np.ones(arr.shape, dtype=bool)
        else:
            out = (arr >= self.tons_min - self.load_band) & (arr <= self.tons_max + self.load_band)
        return bool(out) if out.ndim == 0 else out

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
    covariate_extrapolated: bool = False  # >10% of the covariate fell outside its fitted envelope
    n_out_of_scope: int = 0  # samples a level baseline could not score (load off its band)

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
    covariate_col=None,
) -> pd.DataFrame:
    """Rows usable for fitting or scoring: both points present, real load, physical metric.

    Below ``min_load`` the chiller is effectively unloaded and the metric carries no information
    about equipment condition; the metric bounds drop sensor dropouts and impossible values.
    Returns a two-column frame named ``tons``/``metric`` (plus ``cov`` when ``covariate_col`` is
    given), preserving the original index.
    """
    cols = ["tons", "metric"] + (["cov"] if covariate_col is not None else [])
    need = [metric_col, load_col] + ([covariate_col] if covariate_col is not None else [])
    if any(c not in frame.columns for c in need):
        return pd.DataFrame(columns=cols)
    data = {
        "tons": pd.to_numeric(frame[load_col], errors="coerce"),
        "metric": pd.to_numeric(frame[metric_col], errors="coerce"),
    }
    if covariate_col is not None:
        data["cov"] = pd.to_numeric(frame[covariate_col], errors="coerce")
    w = pd.DataFrame(data, index=frame.index).dropna()
    lo, hi = metric_range
    return w[(w["tons"] >= min_load) & w["metric"].between(lo, hi)]


def residual_lag1(index, resid) -> tuple[float, float]:
    """Lag-1 autocorrelation of time-ordered residuals, and the sampling interval it refers to.

    Only pairs of *consecutive* samples (spaced at the modal interval, within 1.5x) count, so the
    gap between two separate baseline days never pairs the last sample of one with the first of the
    next. Returns ``(0.0, interval)`` when the estimate is not significantly above zero (the usual
    white-noise band, ``2 / sqrt(pairs)``) -- a CUSUM needs no correction for independent samples --
    and ``(0.0, 0.0)`` when the index is not a time index or too short to say.
    """
    r = np.asarray(resid, dtype=float)
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 10:
        return 0.0, 0.0
    order = np.argsort(index.asi8, kind="stable")
    t = index.asi8[order].astype(float) / 1e9
    r = r[order]
    dt = np.diff(t)
    pos = dt[dt > 0]
    if not len(pos):
        return 0.0, 0.0
    step = float(np.median(pos))
    pair = (dt > 0) & (dt <= 1.5 * step)
    n_pairs = int(pair.sum())
    if n_pairs < 10:
        return 0.0, step
    a, b = r[:-1][pair], r[1:][pair]
    a, b = a - a.mean(), b - b.mean()
    den = float(np.sqrt((a @ a) * (b @ b)))
    rho = float(a @ b) / den if den > 0 else 0.0
    if rho <= 2.0 / np.sqrt(n_pairs):
        return 0.0, step
    return min(rho, 0.99), step


def _col_name(col) -> str:
    """A plain-string name for a column key (a :class:`Role` stores as its value)."""
    return str(getattr(col, "value", col))


def fit_load_baseline(
    frame: pd.DataFrame,
    *,
    metric_col,
    load_col: str = "tons",
    min_load: float = 5.0,  # below this the metric carries no condition information
    metric_range: tuple[float, float] = (0.0, 50.0),  # physical-ish degF bounds
    min_samples: int = 30,  # too few points and the slope isn't identified
    min_load_span: float = 10.0,  # too narrow a load range and the slope isn't identified
    covariate_col=None,
    min_covariate_span: float = 0.0,
    level_fallback: bool = False,
) -> LoadBaseline | None:
    """Fit ``metric ~ intercept + slope * load`` over a baseline period; retain residual sigma.

    The metric-neutral core of this module: condenser/evaporator approach, liquid-line subcooling
    and condenser-water range are all load-dependent degF signals and all fit the same way. Only
    ``metric_col`` and the plausibility bounds change between them.

    ``frame`` is indexed by time and carries a metric column and a load column (derive the latter
    with :func:`tons_from_flow` when tons aren't metered). Column keys may be strings or
    :class:`~camber.model.roles.Role` members -- whatever the frame is keyed by.

    ``covariate_col`` adds a second regressor (``metric ~ a + b*load + c*(covariate - ref)``); its
    baseline 5-95 % spread must be at least ``min_covariate_span`` (and non-zero) or its coefficient
    is not identified and the fit returns ``None``. ``level_fallback=True`` fits a flat level
    (slope 0) when the load range is narrower than ``min_load_span`` instead of declining -- the
    honest model for a
    machine whose load never moves -- and records the band it is valid over (see
    :meth:`LoadBaseline.in_scope`).

    Returns ``None`` -- never a fabricated fit -- when the guards leave too few samples, when the
    observed load range is too narrow to identify a slope (and no level fallback was asked for),
    when a covariate is not identified, or when the fit has no residual degrees of freedom. A caller
    must treat ``None`` as "could not evaluate", not as "no drift"; :func:`unscoreable_reason` says
    which guard it was.
    """
    w = _clean(
        frame,
        metric_col,
        load_col,
        min_load=min_load,
        metric_range=metric_range,
        covariate_col=covariate_col,
    )
    if len(w) < max(min_samples, 3):
        return None
    x = w["tons"].to_numpy(dtype=float)
    y = w["metric"].to_numpy(dtype=float)
    load_span = float(x.max() - x.min())
    has_slope = load_span >= min_load_span
    if not has_slope and not level_fallback:
        return None

    cov_slope, cov_ref, cov_min, cov_max = 0.0, 0.0, 0.0, 0.0
    if covariate_col is None and has_slope:
        # the original load-only path, kept numerically identical
        slope, intercept = (float(v) for v in np.polyfit(x, y, 1))
        n_par = 2
    else:
        cols = [np.ones_like(x)]
        if has_slope:
            cols.append(x)
        if covariate_col is not None:
            c = w["cov"].to_numpy(dtype=float)
            cov_min, cov_max = float(c.min()), float(c.max())
            # identification needs the covariate to have genuinely *moved*: judge its 5-95 %
            # spread, not max-min, which sensor noise alone inflates over a long baseline
            p5, p95 = (float(v) for v in np.percentile(c, [5.0, 95.0]))
            if not (p95 - p5) > 0 or (p95 - p5) < min_covariate_span:
                return None
            cov_ref = float(c.mean())
            cols.append(c - cov_ref)
        design = np.column_stack(cols)
        coef, _res, rank, _sv = np.linalg.lstsq(design, y, rcond=None)
        n_par = design.shape[1]
        if rank < n_par:
            return None
        intercept = float(coef[0])
        slope = float(coef[1]) if has_slope else 0.0
        if covariate_col is not None:
            cov_slope = float(coef[-1])
    resid = y - (intercept + slope * x)
    if covariate_col is not None:
        resid = resid - cov_slope * (w["cov"].to_numpy(dtype=float) - cov_ref)
    dof = len(w) - n_par
    if dof < 1:
        return None
    sigma = float(np.sqrt(float(resid @ resid) / dof))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else float("nan")
    start, end = _coverage(w.index)
    rho, step = residual_lag1(w.index, resid)

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
        load_model="linear" if has_slope else "level",
        load_band=0.0 if has_slope else round(max(min_load_span - load_span, 0.0) / 2.0, 4),
        covariate=_col_name(covariate_col) if covariate_col is not None else "",
        covariate_slope=round(cov_slope, 6),
        covariate_ref=round(cov_ref, 4),
        covariate_min=round(cov_min, 4),
        covariate_max=round(cov_max, 4),
        resid_lag1=round(rho, 4),
        sample_seconds=round(step, 3),
    )


def unscoreable_reason(
    frame: pd.DataFrame,
    *,
    metric_col,
    load_col="tons",
    min_load: float,
    metric_range: tuple[float, float],
    min_samples: int,
    min_load_span: float | None = None,
    covariate_col=None,
    baseline: LoadBaseline | None = None,
    metric_name: str | None = None,
    load_name: str | None = None,
) -> str:
    """Say *which* guard left ``frame`` unfittable / unscoreable, in words a reader can act on.

    A decline that blames the wrong cause ("no loaded samples" when the metric column is all-NaN,
    or when every reading sat outside a plausibility band written in another unit) sends the
    reader to fix the wrong thing. This walks the same guards :func:`fit_load_baseline` and
    :func:`load_drift_stats` apply, in order, and names the first that emptied the frame.
    """
    mname = metric_name or _col_name(metric_col)
    load_name = load_name or _col_name(load_col)
    if metric_col not in frame.columns:
        return f"the {mname} column is absent"
    if load_col not in frame.columns:
        return f"the {load_name} column is absent"
    metric = pd.to_numeric(frame[metric_col], errors="coerce")
    load = pd.to_numeric(frame[load_col], errors="coerce")
    if len(frame) == 0:
        return "the period is empty"
    if metric.notna().sum() == 0:
        return f"{mname} has no numeric values in the period (all missing / non-numeric)"
    if load.notna().sum() == 0:
        return f"{load_name} has no numeric values in the period (all missing / non-numeric)"
    both = metric.notna() & load.notna()
    if both.sum() == 0:
        return f"{mname} and {load_name} are never present at the same time"
    loaded = both & (load >= min_load)
    if loaded.sum() == 0:
        return (
            f"no samples at {load_name} >= {min_load:g} (observed {load_name} median "
            f"{float(load[both].median()):.3g}, max {float(load[both].max()):.3g})"
        )
    lo, hi = metric_range
    m_loaded = metric[loaded]
    in_range = loaded & metric.between(lo, hi)
    if in_range.sum() == 0:
        return (
            f"every loaded {mname} reading lies outside its plausible range [{lo:g}, {hi:g}] "
            f"(observed median {float(m_loaded.median()):.4g}) -- check the point's units or "
            "sentinel coding"
        )
    usable = in_range
    if covariate_col is not None:
        if covariate_col not in frame.columns:
            return f"the covariate {_col_name(covariate_col)} column is absent"
        usable = usable & pd.to_numeric(frame[covariate_col], errors="coerce").notna()
        if usable.sum() == 0:
            return f"the covariate {_col_name(covariate_col)} is never present with the metric"
    if baseline is not None and baseline.load_model == "level":
        scoped = usable & pd.Series(
            baseline.in_scope(load.fillna(-np.inf).to_numpy(dtype=float)), index=frame.index
        )
        if scoped.sum() < min_samples:
            return (
                f"the baseline is a flat level valid only for {load_name} "
                f"{baseline.tons_min - baseline.load_band:.3g}-"
                f"{baseline.tons_max + baseline.load_band:.3g}, and only {int(scoped.sum())} "
                f"sample(s) ran there (need {min_samples})"
            )
        usable = scoped
    if usable.sum() < min_samples:
        return f"only {int(usable.sum())} usable sample(s) (need {min_samples})"
    if min_load_span is not None:
        span = float(load[usable].max() - load[usable].min())
        if span < min_load_span:
            return (
                f"the {load_name} range is only {span:.3g} wide (need {min_load_span:g} to "
                "identify a slope)"
            )
    if covariate_col is not None:
        return (
            f"the covariate {_col_name(covariate_col)} did not vary enough to identify its "
            "effect, or the fit was degenerate"
        )
    return "the fit was degenerate (no residual degrees of freedom)"


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
    covariate_col=None,
) -> LoadDrift | None:
    """Score a current period against a fitted ``baseline``; return the drift statistics.

    ``drift_f`` is the **median** residual (actual minus baseline-predicted metric at the same
    load) -- median rather than mean so a handful of dropouts or a short spike doesn't set the
    headline number. ``slope_f_per_month`` is the residual's own trend inside the period, which
    separates "stepped up and stayed" from "still climbing".

    Because every comparison happens at matched load, a period that is simply *busier* than the
    baseline scores near zero drift -- which a level-vs-level comparison cannot do. A baseline
    fitted with a covariate is scored at matched covariate too, and needs ``covariate_col``; a
    flat ``level`` baseline only scores the samples inside its load band.

    Returns ``None`` when the guards leave fewer than ``min_samples`` scoreable rows.
    """
    if baseline.covariate and covariate_col is None:
        raise ValueError(
            f"this baseline was fitted with covariate {baseline.covariate!r}; pass covariate_col"
        )
    use_cov = covariate_col if baseline.covariate else None
    w = _clean(
        frame,
        metric_col,
        load_col,
        min_load=min_load,
        metric_range=metric_range,
        covariate_col=use_cov,
    )
    n_all = len(w)
    if baseline.load_model == "level" and n_all:
        w = w[baseline.in_scope(w["tons"].to_numpy(dtype=float))]
    if len(w) < min_samples:
        return None
    tons = w["tons"].to_numpy(dtype=float)
    cov = w["cov"].to_numpy(dtype=float) if use_cov is not None else None
    resid = np.asarray(baseline.residual(tons, w["metric"].to_numpy(dtype=float), cov), dtype=float)
    drift_f = float(np.median(resid))
    sigma = baseline.sigma_f
    outside = float(np.mean(np.abs(resid) >= 2.0 * sigma)) if sigma > 0 else float("nan")
    off_envelope = float(np.mean((tons < baseline.tons_min) | (tons > baseline.tons_max)))
    cov_off = (
        float(np.mean((cov < baseline.covariate_min) | (cov > baseline.covariate_max)))
        if cov is not None
        else 0.0
    )
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
        covariate_extrapolated=bool(cov_off > 0.10),
        n_out_of_scope=int(n_all - len(w)),
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
