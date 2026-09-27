"""Non-routine and static-factor adjustments to an M&V saving (provisional; #21 phase 21c).

IPMVP writes savings as ``(Baseline - Reporting) ± Routine ± Non-routine`` (IPMVP 2012 Vol. I
§4.5.3, Eq 1a). The routine part is the adjustment model -- the baseline regression projected onto
reporting conditions. The **non-routine** part restates the baseline for what the model cannot
see: a change in a *static factor* (floor area, occupancy type, shifts, equipment) or a
*non-routine event* (a shutdown, a new load, a process change). The DOE SEP 50001 M&V Protocol
(2019 Ed. 2 §5.3.2) requires the numeric inputs of such an adjustment to be observed, measured or
metered, the method and rationale recorded, and prior Verification Body approval.

CAMBER keeps every adjustment as an explicit **ledger entry** -- a :class:`NonRoutineAdjustment`
or :class:`StaticFactorAdjustment` -- and never adjusts implicitly. :func:`apply_adjustments` takes
a finished saving (a :class:`~camber.mandv.stats.SavingsResult` or a
:class:`~camber.mandv.methods.MethodResult`), restates its **baseline side** entry by entry, and
returns an :class:`AdjustedResult` with the adjusted saving, its combined uncertainty, the resolved
ledger and the waterfall from baseline to reporting energy.

**Sign convention.** Every amount is the change to the *baseline side* of the saving, in the
result's energy units over the rows it summed: a new load in the reporting period (it raises
reporting energy) is a positive amount, a partial shutdown a negative one. For a forecast the
baseline side is the baseline projection; for a backcast it is the measured baseline energy (the
restated baseline is then compared with the reporting model at baseline conditions).

**Non-routine adjustment methods** (``NonRoutineAdjustment.method``):

* ``"indicator"`` -- the event's effect per row, estimated as the coefficient of an indicator
  variable in the weather regression (BPA *Regression for M&V Reference Guide* 2024 §3.1.7; BPA /
  SBW *Potential Analytics for NRAs* 2018 §3.1), by :func:`estimate_nre_indicator`. Each indicator
  adds one parameter to the fit's ``p``. With ``fit_period="baseline"`` the indicator is fitted
  inside the baseline model, the augmented model **replaces** the projection (indicator set to 1 on
  the rows the event covers, 0 elsewhere) and the band uses the **joint** covariance
  ``Sigma = kappa s2 (X'X)^-1`` of the weather and indicator coefficients. With
  ``fit_period="reporting"`` it is a mini pre/post fit inside the reporting period, independent of
  the baseline model, so its variance adds in quadrature.
* ``"engineering"`` -- an estimate with its standard error; ``evidence`` is required.
* ``"exclude"`` -- drop the event's span from both sides of the saving: SEP §6.5 treats an anomaly
  as a separate operating mode. The saving then covers fewer rows, and a caveat says so.
* ``"submeter"`` -- the effect measured at a sub-meter, the IPMVP Option B path that IPMVP 2012
  §8.2 prefers to using the facility meter; :func:`nra_from_isolation` builds one from an
  :class:`~camber.mandv.retrofit_isolation.IsolationSavings`.

**Static factors** (:class:`StaticFactorAdjustment`): ``"proportional"`` scales the affected share
``f`` of the baseline by the factor's ratio ``r = s_r / s_b`` -- ``B' = (1 + (r - 1) f) B`` --
with **no default share** (CAMBER decision D8 on #21: the share must be stated); ``"engineering"``
is an estimate plus standard error with evidence. A driver that varies continuously (occupancy,
production, hours) is a relevant variable, not a static factor: model it with
:func:`~camber.mandv.multivariable.fit_cp_driver_model` instead.

**Uncertainty** (the #21 plan, §2.4). The saving's own band is converted to a standard error at its
confidence and degrees of freedom. Engineering, submeter and reporting-period indicator terms add
in quadrature (IPMVP 2012 App. B-5, B-19: independent components). A baseline-period indicator
replaces the projection variance with ``g'Sigma g + kappa s2 m`` of the augmented fit (``g`` the
sum of the design rows including the indicator column). A proportional factor scales the baseline
side's standard error by its multiplier -- correlated with the projection, not in quadrature --
and adds the terms of the user-supplied standard errors of ``r`` and ``f``. The combined band is
at Student's t on the smallest contributing degrees of freedom.

**Guards.**

* *Confounding.* An NRA derived from the facility meter (indicator, exclude) whose start or end
  falls within ``settle_days`` of an ECM date is refused (:class:`ConfoundedAdjustment`): the meter
  cannot tell the event from the measure there. IPMVP 2012 §8.2 goes further -- "Option C cannot be
  used to determine savings when the facility's energy meter is also used to quantify the impact of
  changes to static factors" -- so every meter-derived entry also carries that caveat (CAMBER
  decision D6 on #21 allows it with the guard).
* *SEP.* Under ``validity="sep"`` (or ``"both"``) every entry needs ``evidence`` and
  ``approved_by`` (SEP 2019 Ed. 2 §5.3.2).
* *Proposals.* :func:`propose_adjustments` turns
  :func:`~camber.mandv.nonroutine.detect_step_changes` output into ``status="proposed"`` entries.
  :func:`apply_adjustments` refuses a proposed entry: it must be accepted explicitly
  (:meth:`NonRoutineAdjustment.accept`). Detection never adjusts a saving by itself.

**Materiality** is CAMBER's own rule, by analogy with IPMVP 2012 App. B-1.2 (savings should exceed
twice their standard error): an event is flagged material when ``|effect| >= max(threshold,
2 SE)`` (:func:`is_material`).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

__all__ = [
    "NRA_METHODS",
    "STATIC_METHODS",
    "ConfoundedAdjustment",
    "IndicatorFit",
    "NonRoutineAdjustment",
    "StaticFactorAdjustment",
    "WaterfallStep",
    "AdjustedResult",
    "is_material",
    "estimate_nre_indicator",
    "nra_from_isolation",
    "propose_adjustments",
    "apply_adjustments",
    "adjustment_from_dict",
]

NRA_METHODS = ("indicator", "engineering", "exclude", "submeter")
STATIC_METHODS = ("proportional", "engineering")
_STATUSES = ("proposed", "accepted")
_FIT_PERIODS = ("baseline", "reporting")

_METER_CAVEAT = (
    "meter-derived adjustment: IPMVP 2012 §8.2 says Option C cannot determine savings when the "
    "facility meter also quantifies a static-factor change; prefer an engineering or sub-meter "
    "(Option B) estimate where one is available"
)


class ConfoundedAdjustment(ValueError):
    """A meter-derived NRA dated within the settle window of an ECM (IPMVP 2012 §8.2)."""


def _day(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts


def _date_str(x) -> str | None:
    return None if x is None else str(_day(x).date())


def is_material(effect: float, se: float | None, threshold: float = 0.0) -> bool:
    """CAMBER's materiality rule: ``|effect| >= max(threshold, 2 * se)``.

    ``threshold`` is in the effect's units. An unknown ``se`` counts as zero (only the threshold
    applies). A non-finite effect is never material.
    """
    if effect is None or not np.isfinite(effect):
        return False
    s = 0.0 if se is None or not np.isfinite(se) else float(se)
    return bool(abs(float(effect)) >= max(float(threshold), 2.0 * s))


# --------------------------------------------------------------------------- ledger entries


@dataclass(frozen=True)
class IndicatorFit:
    """The regression an indicator NRA was estimated from (one indicator column, last).

    ``design`` is the weather model's design spec (change points fixed), ``beta`` all coefficients
    in design order with the indicator last, ``sigma`` their joint covariance
    ``kappa * s2 * (X'X)^-1``. ``p`` counts the model's parameters (change points included) plus
    one for the indicator; ``df = n - p``.
    """

    fit_period: str
    design: tuple
    names: tuple
    beta: tuple
    sigma: tuple  # rows of the covariance matrix (JSON-safe)
    s2: float
    rho: float | None
    kappa: float
    n: int
    p: int
    df: int
    n_event_rows: int

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        from .coverage import _spec_out

        d = asdict(self)
        d["design"] = _spec_out(self.design)
        d["sigma"] = [list(r) for r in self.sigma]
        return d


def _check_date(x, what: str):
    if x is None:
        return None
    try:
        return _date_str(x)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{what} must be a date, got {x!r}") from e


@dataclass(frozen=True)
class NonRoutineAdjustment:
    """One non-routine adjustment: an explicit, attributed ledger entry (see the module docstring).

    ``amount`` / ``se`` (engineering, submeter) are the change to the baseline side over the rows
    the saving summed and its standard error. ``rate`` / ``rate_se`` (indicator) are the effect
    per row; the amount is ``rate`` times the rows inside ``[start, end)`` (``end=None`` is open)
    unless ``n_rows`` says how many rows it applies to. ``fit`` is the indicator regression;
    ``fit_period`` says whether it is the baseline model (joint covariance) or independent of it.
    ``status="proposed"`` entries (from detection) cannot be applied until accepted.
    """

    method: str
    start: str
    reason: str
    end: str | None = None
    amount: float | None = None
    se: float | None = None
    rate: float | None = None
    rate_se: float | None = None
    n_rows: int | None = None
    fit_period: str | None = None
    fit: IndicatorFit | None = None
    evidence: str | None = None
    approved_by: str | None = None
    status: str = "accepted"
    material: bool | None = None  # set on proposals; the applied ledger recomputes it
    caveats: tuple = ()

    def __post_init__(self):
        if self.method not in NRA_METHODS:
            raise ValueError(f"unknown NRA method {self.method!r}; use one of {NRA_METHODS}")
        if self.status not in _STATUSES:
            raise ValueError(f"unknown status {self.status!r}; use one of {_STATUSES}")
        object.__setattr__(self, "start", _check_date(self.start, "start"))
        object.__setattr__(self, "end", _check_date(self.end, "end"))
        if self.start is None:
            raise ValueError("a non-routine adjustment needs a start date")
        if self.end is not None and _day(self.end) <= _day(self.start):
            raise ValueError(f"end {self.end} is not after start {self.start}")
        if self.fit_period is not None and self.fit_period not in _FIT_PERIODS:
            raise ValueError(f"fit_period must be one of {_FIT_PERIODS}, got {self.fit_period!r}")
        if self.method in ("engineering", "submeter"):
            if self.amount is None or self.se is None:
                raise ValueError(f"a {self.method} NRA needs amount and se (its standard error)")
            if not (np.isfinite(self.amount) and np.isfinite(self.se) and self.se >= 0):
                raise ValueError("amount and se must be finite, se >= 0")
            if self.method == "engineering" and not self.evidence:
                raise ValueError("an engineering NRA needs evidence (SEP 2019 Ed. 2 §5.3.2)")
        if self.method == "indicator":
            if self.rate is None or self.rate_se is None:
                raise ValueError("an indicator NRA needs rate and rate_se (estimate_nre_indicator)")
            if self.fit_period == "baseline" and self.fit is None:
                raise ValueError("fit_period='baseline' needs the indicator fit (its joint Sigma)")
        if self.n_rows is not None and self.n_rows < 0:
            raise ValueError("n_rows must be >= 0")

    @property
    def meter_derived(self) -> bool:
        """Whether the effect was read off the facility meter (indicator, exclude)."""
        return self.method in ("indicator", "exclude")

    def accept(self, *, approved_by: str, evidence: str | None = None) -> NonRoutineAdjustment:
        """An accepted copy of a (proposed) entry, attributed to ``approved_by``."""
        if not approved_by:
            raise ValueError("accepting an adjustment needs approved_by")
        return replace(
            self,
            status="accepted",
            approved_by=approved_by,
            evidence=evidence if evidence is not None else self.evidence,
        )

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        d = asdict(self)
        d["kind"] = "nra"
        d["fit"] = None if self.fit is None else self.fit.as_dict()
        d["caveats"] = list(self.caveats)
        return d


@dataclass(frozen=True)
class StaticFactorAdjustment:
    """One static-factor adjustment: an explicit, attributed ledger entry.

    ``"proportional"``: the factor moved from ``baseline_value`` to ``reporting_value``
    (``r = reporting / baseline``) and ``affected_share`` -- **required, no default** -- of the
    baseline side scales with it; ``ratio_se`` / ``share_se`` are optional standard errors of
    ``r`` and ``f``. ``"engineering"``: ``amount`` and ``se`` with ``evidence``. ``start`` dates
    the change; rows before it are not scaled when the rows are known.
    """

    factor: str
    method: str
    start: str
    reason: str
    baseline_value: float | None = None
    reporting_value: float | None = None
    affected_share: float | None = None
    ratio_se: float = 0.0
    share_se: float = 0.0
    amount: float | None = None
    se: float | None = None
    evidence: str | None = None
    approved_by: str | None = None
    status: str = "accepted"

    def __post_init__(self):
        if self.method not in STATIC_METHODS:
            raise ValueError(f"unknown static-factor method {self.method!r}; use {STATIC_METHODS}")
        if self.status not in _STATUSES:
            raise ValueError(f"unknown status {self.status!r}; use one of {_STATUSES}")
        object.__setattr__(self, "start", _check_date(self.start, "start"))
        if self.start is None:
            raise ValueError("a static-factor adjustment needs a start date")
        if self.method == "proportional":
            if self.affected_share is None:
                raise ValueError(
                    "a proportional static factor needs an explicit affected_share (no default)"
                )
            if not 0.0 <= float(self.affected_share) <= 1.0:
                raise ValueError("affected_share must be in [0, 1]")
            if self.baseline_value is None or self.reporting_value is None:
                raise ValueError(
                    "a proportional static factor needs baseline_value and reporting_value"
                )
            if not float(self.baseline_value) > 0 or not float(self.reporting_value) >= 0:
                raise ValueError("baseline_value must be > 0 and reporting_value >= 0")
            if self.ratio_se < 0 or self.share_se < 0:
                raise ValueError("ratio_se and share_se must be >= 0")
        else:
            if self.amount is None or self.se is None or self.se < 0:
                raise ValueError("an engineering static factor needs amount and se >= 0")
            if not self.evidence:
                raise ValueError("an engineering static factor needs evidence")

    @property
    def ratio(self) -> float | None:
        """``r = reporting_value / baseline_value`` (proportional only)."""
        if self.method != "proportional":
            return None
        return float(self.reporting_value) / float(self.baseline_value)  # type: ignore[arg-type]

    @property
    def multiplier(self) -> float | None:
        """``1 + (r - 1) f`` (proportional only)."""
        r = self.ratio
        return None if r is None else 1.0 + (r - 1.0) * float(self.affected_share)  # type: ignore[arg-type]

    meter_derived = False

    def accept(self, *, approved_by: str, evidence: str | None = None) -> StaticFactorAdjustment:
        """An accepted copy, attributed to ``approved_by``."""
        if not approved_by:
            raise ValueError("accepting an adjustment needs approved_by")
        return replace(
            self,
            status="accepted",
            approved_by=approved_by,
            evidence=evidence if evidence is not None else self.evidence,
        )

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        d = asdict(self)
        d["kind"] = "static"
        return d


def adjustment_from_dict(d: dict):
    """Build a ledger entry from a plain dict (``"kind": "nra"`` or ``"static"``).

    The keys are the dataclass fields; ``kind`` picks the class. An indicator entry cannot be
    rebuilt this way without its ``rate`` / ``rate_se`` (estimate it with
    :func:`estimate_nre_indicator`); ``fit`` is not rebuilt. Unknown keys are an error.
    """
    d = dict(d)
    kind = d.pop("kind", "nra")
    cls = {"nra": NonRoutineAdjustment, "static": StaticFactorAdjustment}.get(kind)
    if cls is None:
        raise ValueError(f"adjustment kind must be 'nra' or 'static', got {kind!r}")
    d.pop("fit", None)
    d.pop("meter_derived", None)
    from dataclasses import fields

    known = {f.name for f in fields(cls)}
    extra = set(d) - known
    if extra:
        raise ValueError(f"unknown {kind} adjustment key(s): {sorted(extra)}")
    if "caveats" in d:
        d["caveats"] = tuple(d["caveats"] or ())
    return cls(**d)


# --------------------------------------------------------------------------- estimation


def _event_mask(index: pd.DatetimeIndex, start, end) -> np.ndarray:
    idx = index.tz_localize(None) if index.tz is not None else index
    m = idx >= _day(start)
    if end is not None:
        m &= idx < _day(end)
    return np.asarray(m, dtype=bool)


def _linear_spec(model, D):
    """``(design spec, p)`` of a fitted CAMBER linear model, or of a fresh fit on ``D`` rows."""
    rec = getattr(model, "_fit_record", None)
    spec = getattr(rec, "design", None)
    if spec is None:
        raise TypeError(
            f"{type(model).__name__} has no linear design record; fit it with CAMBER "
            "(change-point, change-point + driver, degree-day or driver model)"
        )
    p = rec.p if rec.p is not None else None
    if p is None:
        from .coverage import _design

        p = _design(spec, D[:1]).shape[1]
    return spec, int(p)


def _research_change_points(spec, D, y, ev, ok):
    """Re-search a change-point spec's change points with the event indicator in the design."""
    from .coverage import _design
    from .nonroutine import _cp_candidates

    Dk, yk, evk = D[ok], y[ok], ev[ok].astype(float)
    best = None
    for cps in _cp_candidates(spec[1], Dk[:, 0], spec[2]):
        cand = (spec[0], spec[1], tuple(float(c) for c in cps))
        X = np.column_stack([_design(cand, Dk), evk])
        beta, *_ = np.linalg.lstsq(X, yk, rcond=None)
        r = yk - X @ beta
        sse = float(r @ r)
        if best is None or sse < best[0]:
            best = (sse, cand)
    return spec if best is None else best[1]


def estimate_nre_indicator(
    drivers,
    energy,
    index,
    *,
    start,
    fit_period: str,
    end=None,
    model=None,
    refit_change_points: bool = True,
    reason: str = "",
    evidence: str | None = None,
    approved_by: str | None = None,
) -> NonRoutineAdjustment:
    """Estimate a non-routine event's effect with an indicator variable (BPA 2024 §3.1.7).

    ``drivers`` (outdoor temperature, or ``[T, drivers...]`` rows for a change-point + driver
    model), ``energy`` and ``index`` are the rows of the fit window: the **baseline** period when
    ``fit_period="baseline"`` (the indicator is fitted inside the baseline model, whose augmented
    form then replaces the projection), or a window of the **reporting** period with rows before
    and after the event when ``fit_period="reporting"`` (a mini pre/post fit, independent of the
    baseline). The indicator is 1 on ``[start, end)`` (``end=None``: to the end of the window).

    ``model`` fixes the weather design's kind; its change points are re-searched with the
    indicator in the design (on the model's own grid), because a model fitted *through* the event
    places them where the event pulled them -- pass ``refit_change_points=False`` to keep them.
    Without ``model`` a change-point model (or, for 2-D drivers, a change-point + driver model) is
    fitted to the rows outside the event first. The fit has ``p = p_model + 1`` parameters
    (BPA/SBW 2018 §3.2.1.1 adds to ``p`` for an NRE term), its residual lag-1 autocorrelation
    inflates the covariance by ``kappa = (1+rho)/(1-rho)``, and the change points are treated as
    known, so the standard error is conditional on them.

    Returns an accepted ``"indicator"`` :class:`NonRoutineAdjustment` whose ``rate`` is the
    effect per row (post minus pre) and ``rate_se`` its standard error.
    """
    from .coverage import _as_2d, _design
    from .stats import lag1_autocorrelation

    if fit_period not in _FIT_PERIODS:
        raise ValueError(f"fit_period must be one of {_FIT_PERIODS}, got {fit_period!r}")
    D = _as_2d(drivers)
    y = np.asarray(energy, dtype=float).ravel()
    idx = pd.DatetimeIndex(index)
    if not (len(D) == len(y) == len(idx)):
        raise ValueError("drivers, energy and index must be the same length")
    ev = _event_mask(idx, start, end)
    ok = np.isfinite(y) & np.all(np.isfinite(D), axis=1)
    if model is None:
        clean = ok & ~ev
        if D.shape[1] == 1:
            from .models import best_model

            model = best_model(D[clean, 0], y[clean])
        else:
            from .multivariable import fit_cp_driver_model

            model = fit_cp_driver_model(D[clean, 0], D[clean, 1:], y[clean])
    spec, p_model = _linear_spec(model, D)
    if refit_change_points and spec[0] in ("cp", "cpd") and spec[2]:
        spec = _research_change_points(spec, D, y, ev, ok)
    W = _design(spec, D)
    ok &= np.all(np.isfinite(W), axis=1)
    X = np.column_stack([W, ev.astype(float)])[ok]
    yy, ii = y[ok], idx[ok]
    n_ev = int(ev[ok].sum())
    if n_ev < 2 or len(yy) - n_ev < 2:
        raise ValueError(
            f"the fit window needs rows both inside and outside the event "
            f"({n_ev} inside, {len(yy) - n_ev} outside)"
        )
    p = p_model + 1
    n = len(yy)
    df = n - p
    if df < 1:
        raise ValueError(f"too few rows ({n}) for {p} parameters")
    A = np.linalg.pinv(X.T @ X)
    beta = A @ (X.T @ yy)
    resid = yy - X @ beta
    s2 = float(resid @ resid) / df
    rho = lag1_autocorrelation(resid, index=ii)
    kappa = 1.0 if rho is None else (float("inf") if rho >= 1 else (1.0 + rho) / (1.0 - rho))
    sigma = kappa * s2 * A
    from ._design import design_names

    try:
        names = tuple(design_names(model)) + ("indicator",)
    except TypeError:
        names = tuple(f"x{j}" for j in range(W.shape[1])) + ("indicator",)
    fit = IndicatorFit(
        fit_period=fit_period,
        design=tuple(spec),
        names=names,
        beta=tuple(float(b) for b in beta),
        sigma=tuple(tuple(float(v) for v in row) for row in sigma),
        s2=s2,
        rho=None if rho is None else float(rho),
        kappa=float(kappa),
        n=n,
        p=p,
        df=df,
        n_event_rows=n_ev,
    )
    caveats = [
        "the indicator's standard error is conditional on the weather model's change points",
    ]
    if rho is None:
        caveats.append("residual autocorrelation not estimable; the standard error is unadjusted")
    return NonRoutineAdjustment(
        method="indicator",
        start=start,
        end=end,
        reason=reason,
        rate=float(beta[-1]),
        rate_se=float(math.sqrt(sigma[-1, -1])) if np.isfinite(sigma[-1, -1]) else float("nan"),
        fit_period=fit_period,
        fit=fit,
        evidence=evidence,
        approved_by=approved_by,
        caveats=tuple(caveats),
    )


def nra_from_isolation(
    iso,
    *,
    start,
    reason: str,
    end=None,
    evidence: str | None = None,
    approved_by: str | None = None,
) -> NonRoutineAdjustment:
    """A ``"submeter"`` NRA from an Option B isolation of the system the event changed.

    ``iso`` is the :class:`~camber.mandv.retrofit_isolation.IsolationSavings` of the affected
    sub-meter across the event (its baseline before, its reporting period after). Its saving is
    the drop in that system's energy, so the facility baseline is restated by ``-iso.savings``;
    the standard error is its band divided by Student's t at its confidence and the sub-meter
    fit's degrees of freedom.
    """
    from .stats import _t_value

    if iso.declined or iso.savings is None or iso.abs_uncertainty is None:
        raise ValueError(f"the isolation saving is declined or incomplete: {iso.declined_reason}")
    p = iso.model.p if iso.model is not None else 2
    t = _t_value(iso.confidence, iso.n_baseline - p)
    band = float(iso.abs_uncertainty)
    se = band / t if np.isfinite(band) else float("nan")
    return NonRoutineAdjustment(
        method="submeter",
        start=start,
        end=end,
        reason=reason,
        amount=-float(iso.savings),
        se=se,
        evidence=evidence or (f"sub-meter isolation: {iso.boundary}" if iso.boundary else None),
        approved_by=approved_by,
    )


def propose_adjustments(
    steps,
    *,
    ecm_dates=(),
    settle_days: int = 14,
    threshold: float = 0.0,
) -> list:
    """Proposed indicator NRAs from :func:`~camber.mandv.nonroutine.detect_step_changes`.

    One ``status="proposed"`` entry per detected step, starting on the step's date and open-ended
    (a sustained level shift), with the step's size and standard error as ``rate`` / ``rate_se``
    (per day). ``material`` applies :func:`is_material` to the per-day step with ``threshold`` in
    energy per day. A step within ``settle_days`` of an ECM date is most likely the measure
    itself; its proposal says so, and :func:`apply_adjustments` would refuse it. Nothing here
    adjusts anything: a proposal must be re-estimated for the declared window
    (:func:`estimate_nre_indicator`) or accepted explicitly
    (:meth:`NonRoutineAdjustment.accept`).
    """
    ecms = [_day(d) for d in ecm_dates]
    out = []
    for st in steps.steps:
        date = _day(st.date)
        cav = [
            "proposed by detect_step_changes (PELT on the weather-model residuals); detection "
            "only -- nothing is applied until an analyst accepts it",
            "the step size was estimated over the whole detection series; re-estimate it for the "
            "declared window with estimate_nre_indicator before accepting",
        ]
        near = [e for e in ecms if abs((date - e).days) <= settle_days]
        if near:
            cav.append(
                f"within {settle_days} days of ECM date {near[0].date()}: likely the measure "
                "itself, not a non-routine event (a meter-derived NRA here is refused)"
            )
        out.append(
            NonRoutineAdjustment(
                method="indicator",
                start=date,
                reason=f"detected step change of {st.delta:+.4g} per day",
                rate=float(st.delta),
                rate_se=float(st.se) if st.se is not None else float("nan"),
                status="proposed",
                material=is_material(st.delta, st.se, threshold),
                caveats=tuple(cav),
            )
        )
    return out


# --------------------------------------------------------------------------- application


@dataclass
class WaterfallStep:
    """One bar of the adjustment waterfall: a running ``total`` or a signed ``delta``."""

    label: str
    value: float
    kind: str  # "total" | "delta"
    entry: int | None = None  # ledger position for an adjustment bar

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


@dataclass
class AdjustedResult:
    """A saving restated for non-routine and static-factor adjustments.

    ``baseline`` / ``reporting`` are the unadjusted baseline and reporting sides (for a forecast:
    baseline projection and measured reporting energy); ``adjusted_baseline`` and
    ``adjusted_reporting`` the sides after the ledger (``adjusted_reporting`` differs only when an
    ``exclude`` entry dropped rows). ``savings = adjusted_baseline - adjusted_reporting``;
    ``savings_pct`` is of the adjusted baseline. ``abs_uncertainty`` is the combined band at
    ``confidence`` on ``df`` degrees of freedom (``None``: large-sample). ``ledger`` holds each
    entry with its resolved ``amount``, ``se`` and ``material`` flag, in application order;
    ``waterfall`` the bars from baseline to reporting energy.
    """

    method: str
    basis: str
    kernel: str
    confidence: float
    baseline: float
    reporting: float
    unadjusted_savings: float
    unadjusted_abs_uncertainty: float | None
    adjusted_baseline: float
    adjusted_reporting: float
    savings: float
    savings_pct: float | None
    abs_uncertainty: float | None
    fractional_uncertainty: float | None
    df: int | None
    n_rows: int | None
    ledger: list = field(default_factory=list)
    waterfall: list = field(default_factory=list)
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        d = asdict(self)
        d["waterfall"] = [
            w.as_dict() if isinstance(w, WaterfallStep) else w for w in self.waterfall
        ]
        return d


def _fin(x):
    return x is not None and np.isfinite(x)


def _sides(result) -> dict:
    """The baseline / reporting sides of a finished saving, and which one is projected."""
    if getattr(result, "declined", False):
        raise ValueError(
            f"cannot adjust a declined saving ({getattr(result, 'declined_reason', None)})"
        )
    if hasattr(result, "baseline_projected"):  # SavingsResult (forecast / avoided energy)
        return {
            "method": "forecast",
            "basis": "reporting-period conditions",
            "base": float(result.baseline_projected),
            "rep": float(result.reporting_actual),
            "projected": "base",
            "df": None,
        }
    method = getattr(result, "method", None)
    proj, meas, sav = result.projected, result.measured, result.savings
    if proj is None or meas is None or sav is None:
        raise ValueError("the result carries no projected / measured totals to adjust")
    tol = 0.02 + 1e-9 * max(abs(proj), abs(meas))
    if method == "forecast" or (method != "backcast" and abs(sav - (proj - meas)) <= tol):
        side = "base"
        base, rep = float(proj), float(meas)
    elif method == "backcast" or abs(sav - (meas - proj)) <= tol:
        side = "rep"
        base, rep = float(meas), float(proj)
    else:
        raise ValueError(
            f"cannot adjust a {method!r} result whose saving is neither projected - measured nor "
            "measured - projected (a chained or standard-conditions saving): adjust each link"
        )
    return {
        "method": method,
        "basis": getattr(result, "basis", ""),
        "base": base,
        "rep": rep,
        "projected": side,
        "df": getattr(result, "df", None),
    }


def _check_entries(entries, *, ecm_dates, settle_days, validity):
    if validity not in ("g14", "sep", "both"):
        raise ValueError(f"validity must be 'g14', 'sep' or 'both', got {validity!r}")
    ecms = [_day(d) for d in ecm_dates]
    for i, a in enumerate(entries):
        if not isinstance(a, (NonRoutineAdjustment, StaticFactorAdjustment)):
            raise TypeError(f"ledger entry {i} is a {type(a).__name__}, not an adjustment")
        if a.status != "accepted":
            raise ValueError(
                f"ledger entry {i} ({a.reason!r}) is only proposed: accept it explicitly "
                "(.accept(approved_by=...)) before applying -- detection never adjusts by itself"
            )
        if validity in ("sep", "both") and not (a.evidence and a.approved_by):
            raise ValueError(
                f"ledger entry {i} ({a.reason!r}) needs evidence and approved_by under "
                "validity='sep' (SEP 2019 Ed. 2 §5.3.2: recorded method and rationale, prior "
                "Verification Body approval)"
            )
        if a.meter_derived:
            for d in (a.start, getattr(a, "end", None)):
                if d is None:
                    continue
                for e in ecms:
                    gap = abs((_day(d) - e).days)
                    if gap <= settle_days:
                        raise ConfoundedAdjustment(
                            f"ledger entry {i} ({a.reason!r}): a meter-derived {a.method} NRA "
                            f"dated {d} is {gap} day(s) from ECM date {e.date()}, inside the "
                            f"{settle_days}-day settle window -- the meter cannot separate the "
                            "event from the measure (IPMVP 2012 §8.2); use an engineering or "
                            "sub-meter estimate"
                        )


class _Rows:
    """Per-row detail of the rows a saving summed (optional; needed by exclude and refits)."""

    def __init__(self, index, drivers, measured, model, n_expected):
        self.index = None if index is None else pd.DatetimeIndex(index)
        self.drivers = drivers
        self.model = model
        self.measured = None if measured is None else np.asarray(measured, dtype=float).ravel()
        self.proj = None
        if model is not None and drivers is not None:
            self.proj = np.asarray(model.predict(np.asarray(drivers, dtype=float)), dtype=float)
        n = {len(x) for x in (self.index, self.measured, self.proj) if x is not None}
        if len(n) > 1:
            raise ValueError("index, drivers and measured must describe the same rows")
        self.n = n.pop() if n else None
        mask = np.ones(self.n or 0, bool)
        if self.proj is not None:
            mask &= np.isfinite(self.proj)
        if self.measured is not None:
            mask &= np.isfinite(self.measured)
        self.used = mask  # rows the saving summed

    def need(self, *what, why):
        missing = [w for w in what if getattr(self, w) is None]
        if missing:
            args = {"proj": "model= and drivers=", "index": "index=", "measured": "measured="}
            raise ValueError(f"{why} needs {', '.join(args[m] for m in missing)}")

    def event(self, start, end):
        self.need("index", why="dating an adjustment against the rows")
        return _event_mask(self.index, start, end) & self.used


def apply_adjustments(
    result,
    adjustments,
    *,
    index=None,
    drivers=None,
    measured=None,
    model=None,
    ecm_dates=(),
    settle_days: int = 14,
    validity: str = "g14",
    materiality_threshold: float = 0.0,
    baseline_actual: float | None = None,
) -> AdjustedResult:
    """Restate a saving's baseline side for a ledger of adjustments; see the module docstring.

    ``result`` is a :class:`~camber.mandv.stats.SavingsResult` (forecast) or a
    :class:`~camber.mandv.methods.MethodResult` whose saving is ``projected - measured`` or
    ``measured - projected`` (a backcast). ``adjustments`` is the ledger. The per-row detail of
    the rows the saving summed -- ``index`` (their timestamps), ``drivers``, ``measured`` and the
    ``model`` that projected them -- is optional in general and required by an ``exclude`` entry,
    a baseline-period indicator, dating an indicator's rows, and dating a proportional factor
    that starts mid-period.

    ``ecm_dates`` / ``settle_days`` drive the confounding guard, ``validity`` the SEP evidence
    rule, ``materiality_threshold`` (energy units over the period) the materiality flag.
    ``baseline_actual`` (the measured baseline-period energy of a forecast) adds the routine
    adjustment bar to the waterfall. Raises :class:`ConfoundedAdjustment` or ``ValueError``
    rather than adjusting on a refused entry.
    """
    from .stats import _t_value

    entries = list(adjustments)
    _check_entries(entries, ecm_dates=ecm_dates, settle_days=settle_days, validity=validity)
    s = _sides(result)
    conf = float(result.confidence)
    kernel = getattr(result, "kernel", "g14")
    rows = _Rows(index, drivers, measured, model, None)
    caveats: list = []
    band0 = result.abs_uncertainty if _fin(result.abs_uncertainty) else None
    df0 = s["df"]
    t0 = _t_value(conf, df0)
    var0 = (band0 / t0) ** 2 if band0 is not None else float("nan")
    if s["projected"] == "base":
        var_b, var_r = var0, 0.0
    else:
        var_b, var_r = 0.0, var0
    dfs = [df0] if df0 is not None else []
    B0, R0 = s["base"], s["rep"]
    B, R = B0, R0
    ledger: list = []
    bars: list = []

    def book(i, a, amount, se, label, extra=None):
        mat = None if amount is None else is_material(amount, se, materiality_threshold)
        d = a.as_dict()
        d.update({"position": i, "resolved_amount": amount, "resolved_se": se, "material": mat})
        if extra:
            d.update(extra)
        ledger.append(d)
        if amount is not None:
            bars.append(WaterfallStep(label, float(amount), "delta", i))
        for c in getattr(a, "caveats", ()):
            if c not in caveats:
                caveats.append(c)
        if a.meter_derived and _METER_CAVEAT not in caveats:
            caveats.append(_METER_CAVEAT)

    order = sorted(
        range(len(entries)),
        key=lambda i: (
            0
            if entries[i].method == "exclude"
            else 1
            if (entries[i].method == "indicator" and entries[i].fit_period == "baseline")
            else 2
        ),
    )
    # 1. exclusions: drop the event rows from both sides
    excl = [i for i in order if entries[i].method == "exclude"]
    if excl:
        rows.need("index", "proj", "measured", why="an exclude adjustment")
        drop = np.zeros(rows.n, bool)
        for i in excl:
            a = entries[i]
            m = rows.event(a.start, a.end) & ~drop
            drop |= m
            p_span, o_span = float(rows.proj[m].sum()), float(rows.measured[m].sum())
            base_span, rep_span = (p_span, o_span) if s["projected"] == "base" else (o_span, p_span)
            B -= base_span
            R -= rep_span
            book(
                i,
                a,
                -base_span,
                None,
                f"exclude: {a.reason or a.start}",
                {"rows_excluded": int(m.sum()), "reporting_side_removed": -rep_span},
            )
            ledger[-1]["material"] = None
        m_old = int(rows.used.sum())
        keep = rows.used & ~drop
        m_new = int(keep.sum())
        if m_new == 0:
            raise ValueError("the exclusions leave no rows in the saving")
        var_proj = var_b if s["projected"] == "base" else var_r
        P_old = float(rows.proj[rows.used].sum())
        P_new = float(rows.proj[keep].sum())
        new_var = None
        if kernel == "exact" and model is not None:
            try:
                from ._design import projection_variance

                pv = projection_variance(
                    model, drivers, rows=keep, rho=getattr(result, "rho", None)
                )
                new_var = pv.v_param + pv.v_noise
            except TypeError:
                new_var = None
        if new_var is None and np.isfinite(var_proj) and P_old:
            # the G14 band is t * 1.26 * CV * P * sqrt((n/n')(1+2/n)/m): var scales as P^2 / m
            new_var = var_proj * (P_new / P_old) ** 2 * (m_old / m_new)
        if s["projected"] == "base":
            var_b = new_var if new_var is not None else var_b
        else:
            var_r = new_var if new_var is not None else var_r
        rows.used = keep
        caveats.append(
            f"{m_old - m_new} of {m_old} rows excluded as non-routine (SEP §6.5 anomaly mode): "
            "the saving covers the remaining rows only"
        )

    # 2. a baseline-period indicator refit replaces the projection (joint Sigma)
    refits = [
        i for i in order if entries[i].method == "indicator" and entries[i].fit_period == "baseline"
    ]
    if len(refits) > 1:
        raise ValueError("at most one baseline-period indicator fit per saving (fit them jointly)")
    for i in refits:
        a = entries[i]
        if s["projected"] != "base":
            raise ValueError(
                "a baseline-period indicator applies to a forecast (projected baseline)"
            )
        if drivers is None or index is None:
            raise ValueError("a baseline-period indicator needs drivers= and index= of the rows")
        from .coverage import _as_2d, _design

        f = a.fit
        D = _as_2d(drivers)
        W = _design(f.design, D)
        ev = _event_mask(pd.DatetimeIndex(index), a.start, a.end)
        use = (
            rows.used & np.all(np.isfinite(W), axis=1) if rows.n else np.all(np.isfinite(W), axis=1)
        )
        X = np.column_stack([W, ev.astype(float)])[use]
        g = X.sum(axis=0)
        beta = np.asarray(f.beta)
        Sig = np.asarray(f.sigma)
        P_w = float(g[:-1] @ beta[:-1])
        d = float(g[-1]) if a.n_rows is None else float(a.n_rows)
        g_eff = g.copy()
        g_eff[-1] = d
        amount = d * float(beta[-1])
        se = abs(d) * float(a.rate_se)
        m = int(use.sum())
        var_b = float(g_eff @ Sig @ g_eff) + f.kappa * f.s2 * m
        dfs.append(f.df)
        refit_delta = P_w - B
        bars.append(WaterfallStep("baseline refit with indicator", refit_delta, "delta", i))
        B = P_w
        book(i, a, amount, se, f"NRA indicator: {a.reason or a.start}", {"rows_affected": d})
        B += amount
        if kernel != "exact":
            caveats.append(
                "the baseline-period indicator replaced the projection: the band is the exact OLS "
                "kernel of the augmented fit (joint covariance), not the G14 kernel"
            )
            kernel = "exact"

    # 3. additive NRAs and static factors, in ledger order
    for i in order:
        a = entries[i]
        if a.method == "exclude" or (a.method == "indicator" and a.fit_period == "baseline"):
            continue
        if isinstance(a, NonRoutineAdjustment):
            if a.method == "indicator":
                if a.n_rows is not None:
                    d = float(a.n_rows)
                else:
                    d = float(rows.event(a.start, a.end).sum())
                assert a.rate is not None and a.rate_se is not None  # checked on construction
                amount = d * float(a.rate)
                se = abs(d) * float(a.rate_se)
                if a.fit is not None:
                    dfs.append(a.fit.df)
                extra = {"rows_affected": d}
                label = f"NRA indicator: {a.reason or a.start}"
            else:
                assert a.amount is not None and a.se is not None  # checked on construction
                amount, se, extra = float(a.amount), float(a.se), None
                label = f"NRA {a.method}: {a.reason or a.start}"
            B += amount
            var_b = var_b + se**2 if np.isfinite(se) else float("nan")
            book(i, a, amount, se, label, extra)
            continue
        # static factor
        if a.method == "engineering":
            amount, se = float(a.amount), float(a.se)
            B += amount
            var_b += se**2
            book(i, a, amount, se, f"static factor ({a.factor}): {a.reason}")
            continue
        assert a.multiplier is not None and a.ratio is not None and a.affected_share is not None
        c = float(a.multiplier)
        w = 1.0
        start = _day(a.start)
        if rows.index is not None:
            idx = rows.index.tz_localize(None) if rows.index.tz is not None else rows.index
            after = rows.used & np.asarray(idx >= start)
            if rows.proj is not None:
                P_all = float(rows.proj[rows.used].sum())
                w = float(rows.proj[after].sum()) / P_all if P_all else 1.0
            else:
                w = float(after.sum()) / max(1, int(rows.used.sum()))
                if 0.0 < w < 1.0:
                    caveats.append(
                        f"static factor {a.factor!r} weighted by its share of rows ({w:.0%}): "
                        "pass model= and drivers= to weight it by projected energy"
                    )
        else:
            caveats.append(
                f"static factor {a.factor!r} applied to the whole period: no row index was given "
                f"to date it from {a.start}"
            )
        mult = 1.0 + (c - 1.0) * w
        affected = w * B
        amount = (c - 1.0) * affected
        r, f = float(a.ratio), float(a.affected_share)
        v_extra = (affected * f * a.ratio_se) ** 2 + (affected * (r - 1.0) * a.share_se) ** 2
        # correlated with the projection: the baseline side's SE scales with the multiplier
        var_b = var_b * mult**2 + v_extra
        B += amount
        book(
            i,
            a,
            amount,
            math.sqrt(v_extra),
            f"static factor ({a.factor}): x{c:.4g} on {f:.0%}",
            {"multiplier": c, "ratio": r, "share_of_period": w},
        )

    S = B - R
    var = var_b + var_r
    df = min(dfs) if dfs else None
    band = t0 * math.sqrt(var) if (np.isfinite(var) and var >= 0 and not dfs) else None
    if dfs and np.isfinite(var) and var >= 0:
        band = _t_value(conf, df) * math.sqrt(var)
    pct = S / B if B else None
    frac = band / abs(S) if (band is not None and S) else None
    labels = {
        "base": ("baseline projection", "reporting actual"),
        "rep": ("baseline actual", "reporting model at baseline conditions"),
    }[s["projected"]]
    wf: list = []
    if baseline_actual is not None and s["projected"] == "base":
        wf.append(WaterfallStep("baseline period actual", float(baseline_actual), "total"))
        wf.append(WaterfallStep("routine adjustment", B0 - float(baseline_actual), "delta"))
    wf.append(WaterfallStep(labels[0], B0, "total"))
    wf.extend(bars)
    wf.append(WaterfallStep("adjusted baseline", B, "total"))
    wf.append(WaterfallStep("savings", -S, "delta"))
    wf.append(WaterfallStep(labels[1] + (" (adjusted)" if R != R0 else ""), R, "total"))
    return AdjustedResult(
        method=str(s["method"]),
        basis=str(s["basis"]),
        kernel=kernel,
        confidence=conf,
        baseline=round(B0, 2),
        reporting=round(R0, 2),
        unadjusted_savings=round(B0 - R0, 2),
        unadjusted_abs_uncertainty=band0,
        adjusted_baseline=round(B, 2),
        adjusted_reporting=round(R, 2),
        savings=round(S, 2),
        savings_pct=None if pct is None else round(pct, 4),
        abs_uncertainty=None if band is None else round(band, 2),
        fractional_uncertainty=None if frac is None else round(frac, 4),
        df=df,
        n_rows=None if rows.n is None else int(rows.used.sum()),
        ledger=ledger,
        waterfall=wf,
        caveats=caveats,
    )
