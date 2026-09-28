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
:class:`~camber.mandv.methods.MethodResult` of any SEP method), restates its **baseline side**
entry by entry, and returns an :class:`AdjustedResult` with the adjusted saving and SEnPI, their
combined uncertainty, the resolved ledger and the waterfall from baseline to reporting energy. The
M&V flow is fixed: the declared method gives the saving, the ledger restates it, the result carries
both.

**Sign convention.** Every amount is the change to the *baseline side* of the saving, in the
result's energy units over the rows it summed: a new load in the reporting period (it raises
reporting energy) is a positive amount, a partial shutdown a negative one. For a forecast the
baseline side is the baseline projection; for a backcast it is the measured baseline energy (the
restated baseline is then compared with the reporting model at baseline conditions); at standard
conditions it is the baseline model's projection there; in a chain it is the baseline side of the
one link the entry is dated in (see :func:`apply_adjustments`).

**Non-routine adjustment methods** (``NonRoutineAdjustment.method``):

* ``"indicator"`` -- the event's effect per row, estimated as the coefficient of an indicator
  variable in the weather regression (BPA *Regression for M&V Reference Guide* 2024 §3.1.7; BPA /
  SBW *Potential Analytics for NRAs* 2018 §3.1), by :func:`estimate_nre_indicator`. Each indicator
  adds one parameter to the fit's ``p``. With ``fit_period="baseline"`` the indicator is fitted
  inside the baseline model, the augmented model **replaces** the projection (indicator set to 1 on
  the rows the event covers, 0 elsewhere) and the band uses the **joint** covariance
  ``Sigma = kappa s2 (X'X)^-1`` of the weather and indicator coefficients. With
  ``fit_period="reporting"`` it is a mini pre/post fit inside the reporting period, independent of
  the baseline model, so on a forecast its variance adds in quadrature. On a **backcast** whose
  reporting model was fitted on the same rows, that fit *is* the reporting model refitted with the
  indicator, and it replaces the reporting projection the same way (joint ``Sigma``, ``p + 1``):
  the unadjusted reporting model was fitted *through* the event, whose ``s2`` and ``rho`` made
  its band about 20x too wide (a maintainer decision on #21; Monte Carlo in
  ``tests/test_mandv_mc_coverage.py``).
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
sum of the design rows including the indicator column); so does a backcast's reporting-period
indicator on the reporting side, when the summed rows' ``drivers=`` are given, it is the only
reporting-period indicator, and it was fitted on the reporting model's rows. Otherwise (a chain
link, say) it adds in quadrature to the unadjusted band, which is then conservative, and a caveat
says so. A proportional factor scales the baseline
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
    "VALIDITY",
    "DEFAULT_SETTLE_DAYS",
    "EcmSchedule",
    "check_validity",
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

#: The validity regimes an M&V run can be reported under (issue #21 decision D1): ``"g14"`` (the
#: ASHRAE G14 acceptance gates the savings claim), ``"sep"`` (SEP 2019 Ed. 2 §6.4.1 model validity
#: gates the SEnPI, and §5.3.2's evidence rule gates every adjustment) or ``"both"``. One key and
#: one meaning for the whole ``mv`` path: the config's ``mv[].validity``.
VALIDITY = ("g14", "sep", "both")

#: Days after an ECM date during which the facility meter cannot tell a non-routine event from the
#: measure (CAMBER's default; issue #21 §2.6). Shared by the confounding guard, the detection
#: proposals and -- in phase 21d -- the rebaselining policy.
DEFAULT_SETTLE_DAYS = 14

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


def check_validity(validity: str) -> str:
    """Validate a :data:`VALIDITY` value and return it."""
    if validity not in VALIDITY:
        raise ValueError(f"validity must be one of {VALIDITY}, got {validity!r}")
    return validity


@dataclass(frozen=True)
class EcmSchedule:
    """The declared ECM installation dates and the settle window after each (provisional).

    The one place CAMBER keeps them: the confounding guard of :func:`apply_adjustments`, the
    proposals of :func:`propose_adjustments` and the config's ``mv[].ecm_dates`` /
    ``mv[].settle_days`` all read an :class:`EcmSchedule`, and phase 21d's rebaselining policy is
    to take one too. ``near(date)`` returns ``(ecm_date, gap_days)`` for the first ECM within
    ``settle_days`` of ``date``, else ``None``.
    """

    ecm_dates: tuple = ()
    settle_days: int = DEFAULT_SETTLE_DAYS

    def __post_init__(self):
        dates = self.ecm_dates
        if isinstance(dates, (str, pd.Timestamp)):
            dates = (dates,)
        try:
            days = tuple(sorted(_day(d) for d in (dates or ())))
        except (ValueError, TypeError) as e:
            raise ValueError(f"ecm_dates must be dates, got {self.ecm_dates!r}") from e
        object.__setattr__(self, "ecm_dates", tuple(str(d.date()) for d in days))
        if isinstance(self.settle_days, bool) or int(self.settle_days) != self.settle_days:
            raise ValueError(
                f"settle_days must be a whole number of days, got {self.settle_days!r}"
            )
        if int(self.settle_days) < 0:
            raise ValueError("settle_days must be >= 0")
        object.__setattr__(self, "settle_days", int(self.settle_days))

    def near(self, date):
        """``(ecm_date, gap_days)`` of the first ECM within the settle window of ``date``."""
        d = _day(date)
        for e in self.ecm_dates:
            gap = abs((d - _day(e)).days)
            if gap <= self.settle_days:
                return _day(e), gap
        return None

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        return {"ecm_dates": list(self.ecm_dates), "settle_days": self.settle_days}

    @classmethod
    def from_dict(cls, d: dict | None) -> EcmSchedule:
        """Build from ``{"ecm_dates": [...], "settle_days": n}`` (both optional)."""
        d = dict(d or {})
        extra = set(d) - {"ecm_dates", "settle_days"}
        if extra:
            raise ValueError(f"unknown ECM schedule key(s): {sorted(extra)}")
        return cls(
            ecm_dates=tuple(d.get("ecm_dates") or ()),
            settle_days=d.get("settle_days", DEFAULT_SETTLE_DAYS),
        )


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
    one for the indicator; ``df = n - p``. A fit weighted by billing days (0.92, provisional)
    records ``weight_scale``, the mean bill length, so a measured total over ``m`` rows of one day
    each carries noise ``kappa * s2 * weight_scale * m`` (``None`` for an unweighted fit).
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
    weight_scale: float | None = None

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        from .coverage import _spec_out

        d = asdict(self)
        d["design"] = _spec_out(self.design)
        d["sigma"] = [list(r) for r in self.sigma]
        if self.weight_scale is None:  # unweighted fits serialise exactly as before 0.92
            d.pop("weight_scale")
        return d

    @property
    def noise_scale(self) -> float:
        """Per-row noise multiplier of ``s2``: ``weight_scale`` for a day-weighted fit, else 1."""
        return 1.0 if self.weight_scale is None else float(self.weight_scale)

    @classmethod
    def from_dict(cls, d: dict) -> IndicatorFit:
        """Rebuild a fit written by :meth:`as_dict` (lossless: ``from_dict(f.as_dict()) == f``).

        Unknown keys are an error, so a stored ledger that does not match this CAMBER is refused
        rather than half-read.
        """
        from dataclasses import fields as _fields

        from .coverage import _spec_in

        d = dict(d)
        known = {f.name for f in _fields(cls)}
        extra = set(d) - known
        if extra:
            raise ValueError(f"unknown indicator-fit key(s): {sorted(extra)}")
        d["design"] = _spec_in(d.get("design"))
        d["names"] = tuple(d.get("names") or ())
        d["beta"] = tuple(float(b) for b in d.get("beta") or ())
        d["sigma"] = tuple(tuple(float(x) for x in row) for row in d.get("sigma") or ())
        d["rho"] = None if d.get("rho") is None else float(d["rho"])
        for k in ("n", "p", "df", "n_event_rows"):
            d[k] = int(d[k])
        for k in ("s2", "kappa"):
            d[k] = float(d[k])
        if d.get("weight_scale") is not None:
            d["weight_scale"] = float(d["weight_scale"])
        return cls(**d)


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
    :func:`estimate_nre_indicator`). A ``fit`` written by :meth:`NonRoutineAdjustment.as_dict` is
    rebuilt through :meth:`IndicatorFit.from_dict`, so a stored ledger round-trips losslessly
    (``adjustment_from_dict(a.as_dict()) == a``). Unknown keys are an error.
    """
    d = dict(d)
    kind = d.pop("kind", "nra")
    cls = {"nra": NonRoutineAdjustment, "static": StaticFactorAdjustment}.get(kind)
    if cls is None:
        raise ValueError(f"adjustment kind must be 'nra' or 'static', got {kind!r}")
    fit = d.pop("fit", None)
    if fit is not None and cls is NonRoutineAdjustment:
        d["fit"] = fit if isinstance(fit, IndicatorFit) else IndicatorFit.from_dict(fit)
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


def _research_change_points(spec, D, y, ev, ok, w=None):
    """Re-search a change-point spec's change points with the event indicator in the design
    (weighted least squares when ``w``, aligned to ``D``, is given)."""
    from .coverage import _design
    from .nonroutine import _cp_candidates

    Dk, yk, evk = D[ok], y[ok], ev[ok].astype(float)
    sw = None if w is None else np.sqrt(np.asarray(w, dtype=float)[ok])
    best = None
    for cps in _cp_candidates(spec[1], Dk[:, 0], spec[2]):
        cand = (spec[0], spec[1], tuple(float(c) for c in cps))
        X = np.column_stack([_design(cand, Dk), evk])
        if sw is None:
            beta, *_ = np.linalg.lstsq(X, yk, rcond=None)
            r = yk - X @ beta
        else:
            beta, *_ = np.linalg.lstsq(X * sw[:, None], yk * sw, rcond=None)
            r = (yk - X @ beta) * sw
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
    weights=None,
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

    ``weights`` (provisional, 0.92) makes it a weighted least-squares fit -- billing rows weighted
    by their day counts, whose ``energy`` is energy per day, so ``rate`` is per day. The fit
    records the mean weight (``IndicatorFit.weight_scale``); a bill is inside the event when its
    start date is.
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
    w_all = None if weights is None else np.asarray(weights, dtype=float).ravel()
    if w_all is not None:
        if len(w_all) != len(y):
            raise ValueError("weights must be the same length as energy")
        ok &= np.isfinite(w_all) & (w_all > 0)
    if model is None:
        clean = ok & ~ev
        if D.shape[1] == 1:
            from .models import best_model

            model = best_model(
                D[clean, 0], y[clean], weights=None if w_all is None else w_all[clean]
            )
        else:
            from .multivariable import fit_cp_driver_model

            model = fit_cp_driver_model(D[clean, 0], D[clean, 1:], y[clean])
    spec, p_model = _linear_spec(model, D)
    if refit_change_points and spec[0] in ("cp", "cpd") and spec[2]:
        spec = _research_change_points(spec, D, y, ev, ok, w_all)
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
    from .models import fit_weights

    w, w_scale = fit_weights(w_all, ok)
    if w is None:
        A = np.linalg.pinv(X.T @ X)
        beta = A @ (X.T @ yy)
        resid = yy - X @ beta
        s2 = float(resid @ resid) / df
        rho = lag1_autocorrelation(resid, index=ii)
    else:
        Xw = X * w[:, None]
        A = np.linalg.pinv(X.T @ Xw)
        beta = A @ (Xw.T @ yy)
        resid = yy - X @ beta
        s2 = float((w * resid) @ resid) / df
        rho = lag1_autocorrelation(np.sqrt(w) * resid, index=ii)
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
        weight_scale=w_scale,
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
    settle_days: int = DEFAULT_SETTLE_DAYS,
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
    sched = EcmSchedule(tuple(ecm_dates), settle_days)
    out = []
    for st in steps.steps:
        date = _day(st.date)
        cav = [
            "proposed by detect_step_changes (PELT on the weather-model residuals); detection "
            "only -- nothing is applied until an analyst accepts it",
            "the step size was estimated over the whole detection series; re-estimate it for the "
            "declared window with estimate_nre_indicator before accepting",
        ]
        near = sched.near(date)
        if near:
            cav.append(
                f"within {sched.settle_days} days of ECM date {near[0].date()}: likely the measure "
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
    baseline projection and measured reporting energy; for a chain: each summed over the links,
    so that ``savings = baseline - reporting`` stays SEP's additive Eq 11); ``adjusted_baseline``
    and ``adjusted_reporting`` the sides after the ledger (``adjusted_reporting`` differs only when
    an ``exclude`` entry dropped rows). ``savings = adjusted_baseline - adjusted_reporting``;
    ``enpi`` is the adjusted SEnPI -- ``reporting / baseline`` for one link, the product over the
    links for a chain (Eq 6) -- and ``savings_pct = 1 - enpi``. ``abs_uncertainty`` is the combined
    band at ``confidence`` on ``df`` degrees of freedom (``None``: large-sample) and
    ``enpi_uncertainty`` the SEnPI's delta-method band. ``ledger`` holds each entry with its
    resolved ``amount``, ``se``, ``material`` flag and ``link``, in application order;
    ``waterfall`` the bars from baseline to reporting energy; ``links`` the adjusted sides of each
    link of a chain. ``baseline_version`` is the stored baseline version the saving used (copied
    from the result; see :class:`camber.mandv.rebaseline.MVBaselineStore`).
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
    enpi: float | None = None
    enpi_uncertainty: float | None = None
    unadjusted_enpi: float | None = None
    links: list = field(default_factory=list)
    baseline_version: str | None = None

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        d = asdict(self)
        d["waterfall"] = [
            w.as_dict() if isinstance(w, WaterfallStep) else w for w in self.waterfall
        ]
        return d


def _fin(x):
    return x is not None and np.isfinite(x)


def _check_entries(entries, *, schedule: EcmSchedule, validity):
    check_validity(validity)
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
                near = schedule.near(d)
                if near:
                    e, gap = near
                    raise ConfoundedAdjustment(
                        f"ledger entry {i} ({a.reason!r}): a meter-derived {a.method} NRA "
                        f"dated {d} is {gap} day(s) from ECM date {e.date()}, inside the "
                        f"{schedule.settle_days}-day settle window -- the meter cannot separate "
                        "the event from the measure (IPMVP 2012 §8.2); use an engineering or "
                        "sub-meter estimate"
                    )


class _Rows:
    """Per-row detail of the rows a saving summed (optional; needed by exclude and refits)."""

    def __init__(self, index, drivers, measured, model):
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


@dataclass
class _Side:
    """One single-model comparison to adjust: a whole forecast / backcast / standard-conditions
    result, or one link of a chain.

    ``kind`` says where the model sits: ``"forecast"`` (the baseline side is a projection onto
    the summed rows, which are the later period's), ``"backcast"`` (the baseline side is measured;
    the reporting side is a model fitted on the later period, projected onto the summed rows) or
    ``"standard_conditions"`` (both sides projected onto undated standard rows). ``var_b`` /
    ``var_r`` are the variances on each side; ``later_index`` dates the later period's rows when
    they are not the summed ones (a backcast's or standard-conditions' reporting-model fit rows).
    """

    kind: str
    label: str
    base: float
    rep: float
    var_b: float
    var_r: float
    df: int | None
    kernel: str
    rows: _Rows
    later_index: pd.DatetimeIndex | None = None
    m_rows: int | None = None  # rows summed, when the rows themselves are not given
    rho: float | None = None
    method: str = ""


def _share(index, start, end) -> float:
    """Share of the rows of ``index`` inside ``[start, end)``."""
    if index is None or len(index) == 0:
        return float("nan")
    return float(_event_mask(pd.DatetimeIndex(index), start, end).mean())


def _adjust_side(side: _Side, items, *, threshold, allow_rows_ops: bool) -> dict:
    """Apply ``items`` (``(ledger position, entry)`` pairs) to one side; see apply_adjustments.

    Returns the adjusted sides and their variances, the multiplier applied to the baseline side
    (the chain covariance scales with it), the extra degrees of freedom, ledger rows, bars and
    caveats.
    """
    rows = side.rows
    caveats: list = []
    var_b, var_r = side.var_b, side.var_r
    kernel = side.kernel
    dfs: list = []
    B0, R0 = side.base, side.rep
    B, R = B0, R0
    ledger: list = []
    bars: list = []
    mult_total = 1.0
    pre = f"{side.label}: " if side.label else ""

    def book(i, a, amount, se, label, extra=None):
        mat = None if amount is None else is_material(amount, se, threshold)
        d = a.as_dict()
        d.update({"position": i, "resolved_amount": amount, "resolved_se": se, "material": mat})
        if side.label:
            d["link"] = side.label
        if extra:
            d.update(extra)
        ledger.append(d)
        if amount is not None:
            bars.append(WaterfallStep(pre + label, float(amount), "delta", i))
        for c in getattr(a, "caveats", ()):
            if c not in caveats:
                caveats.append(c)
        if a.meter_derived and _METER_CAVEAT not in caveats:
            caveats.append(_METER_CAVEAT)

    def summed_rows() -> int:
        if rows.n:
            return int(rows.used.sum())
        if side.m_rows is not None:
            return int(side.m_rows)
        raise ValueError(
            f"{pre}dating an adjustment by its share of the period needs the rows the saving "
            "summed (drivers= or index=)"
        )

    order = sorted(
        items,
        key=lambda ia: (
            0
            if ia[1].method == "exclude"
            else 1
            if (ia[1].method == "indicator" and ia[1].fit_period == "baseline")
            else 2
        ),
    )
    # 1. exclusions: drop the event rows from both sides
    excl = [(i, a) for i, a in order if a.method == "exclude"]
    if excl:
        if not allow_rows_ops:
            raise ValueError(
                f"{pre}an exclude entry drops rows from both sides of a link: drop them from the "
                "chain's input rows and recompute the chain instead"
            )
        if side.kind == "standard_conditions":
            raise ValueError(
                "an exclude entry drops dated rows; standard-conditions rows carry no dates "
                "(exclude the span from the fit that built the model instead)"
            )
        rows.need("index", "proj", "measured", why="an exclude adjustment")
        drop = np.zeros(rows.n, bool)
        proj_base = side.kind == "forecast"
        for i, a in excl:
            m = rows.event(a.start, a.end) & ~drop
            drop |= m
            p_span, o_span = float(rows.proj[m].sum()), float(rows.measured[m].sum())
            base_span, rep_span = (p_span, o_span) if proj_base else (o_span, p_span)
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
        var_proj = var_b if proj_base else var_r
        P_old = float(rows.proj[rows.used].sum())
        P_new = float(rows.proj[keep].sum())
        new_var = None
        if kernel == "exact" and rows.model is not None:
            try:
                from ._design import projection_variance

                pv = projection_variance(rows.model, rows.drivers, rows=keep, rho=side.rho)
                new_var = pv.v_param + pv.v_noise
            except TypeError:
                new_var = None
        if new_var is None and np.isfinite(var_proj) and P_old:
            # the G14 band is t * 1.26 * CV * P * sqrt((n/n')(1+2/n)/m): var scales as P^2 / m
            new_var = var_proj * (P_new / P_old) ** 2 * (m_old / m_new)
        if proj_base:
            var_b = new_var if new_var is not None else var_b
        else:
            var_r = new_var if new_var is not None else var_r
        rows.used = keep
        caveats.append(
            f"{pre}{m_old - m_new} of {m_old} rows excluded as non-routine (SEP §6.5 anomaly "
            "mode): the saving covers the remaining rows only"
        )

    # 2. a baseline-period indicator refit replaces the baseline projection (joint Sigma)
    refits = [(i, a) for i, a in order if a.method == "indicator" and a.fit_period == "baseline"]
    if len(refits) > 1:
        raise ValueError("at most one baseline-period indicator fit per saving (fit them jointly)")
    for i, a in refits:
        if not allow_rows_ops:
            raise ValueError(
                f"{pre}a baseline-period indicator refits the model a link projects: refit that "
                "link's model with the indicator and recompute the chain instead"
            )
        if side.kind == "backcast":
            raise ValueError(
                "a baseline-period indicator restates a projected baseline (forecast or "
                "standard conditions); a backcast's baseline side is measured"
            )
        std = side.kind == "standard_conditions"
        if rows.drivers is None or (rows.index is None and not std):
            raise ValueError(
                "a baseline-period indicator needs drivers= (and, except at standard "
                "conditions, index=) of the rows"
            )
        from .coverage import _as_2d, _design

        f = a.fit
        D = _as_2d(rows.drivers)
        W = _design(f.design, D)
        if std:
            ev = np.zeros(len(D), bool)  # standard rows are undated: the event is absent
        else:
            ev = _event_mask(pd.DatetimeIndex(rows.index), a.start, a.end)
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
        # a projection at standard conditions has no noise term (nothing is measured there)
        var_b = float(g_eff @ Sig @ g_eff) + (0.0 if std else f.kappa * f.s2 * m * f.noise_scale)
        dfs.append(f.df)
        bars.append(WaterfallStep(pre + "baseline refit with indicator", P_w - B, "delta", i))
        B = P_w
        book(i, a, amount, se, f"NRA indicator: {a.reason or a.start}", {"rows_affected": d})
        B += amount
        if kernel != "exact":
            caveats.append(
                "the baseline-period indicator replaced the projection: the band is the exact OLS "
                "kernel of the augmented fit (joint covariance), not the G14 kernel"
            )
            kernel = "exact"

    # 2b. a backcast's reporting-period indicator refits the reporting model (joint Sigma, +1 p)
    rep_refit = _reporting_refit(side, order, caveats, allow_rows_ops=allow_rows_ops, pre=pre)

    # 3. additive NRAs and static factors, in ledger order
    for i, a in order:
        if a.method == "exclude" or (a.method == "indicator" and a.fit_period == "baseline"):
            continue
        if isinstance(a, NonRoutineAdjustment):
            if a.method == "indicator":
                assert a.rate is not None and a.rate_se is not None  # checked on construction
                extra: dict | None = {}
                if a.n_rows is not None:
                    d = float(a.n_rows)
                elif side.kind == "forecast":
                    d = float(rows.event(a.start, a.end).sum())
                else:
                    # the later period's model carries the event on its share of that period's
                    # rows; the measured (earlier) side carries it on its own event rows
                    if side.later_index is not None:
                        share = _share(side.later_index, a.start, a.end)
                    elif a.fit is not None and a.fit.fit_period == "reporting":
                        share = a.fit.n_event_rows / a.fit.n
                        caveats.append(
                            f"{pre}indicator {a.reason or a.start!r} weighted by its share of "
                            "its own fit window, taken as the reporting model's fit rows"
                        )
                    else:
                        raise ValueError(
                            f"{pre}an indicator on a {side.kind} result needs n_rows or the "
                            "reporting-model fit rows (reporting_index=) to date it"
                        )
                    m_sum = summed_rows()
                    d_meas = 0.0
                    if side.kind == "backcast" and rows.index is not None:
                        d_meas = float(rows.event(a.start, a.end).sum())
                    d = share * m_sum - d_meas
                    extra = {"share_of_later_period": share}
                amount = d * float(a.rate)
                se = abs(d) * float(a.rate_se)
                if a.fit is not None:
                    dfs.append(a.fit.df)
                extra = {"rows_affected": d, **(extra or {})}
                label = f"NRA indicator: {a.reason or a.start}"
                if rep_refit is not None and rep_refit["position"] == i:
                    # the refitted reporting model carries the event on the same d rows (plus
                    # any measured event rows), so the saving is the event-free comparison and
                    # its variance is the joint one of the refit, not the entry's in quadrature
                    d_meas_rows = rep_refit["d_meas"]
                    R = rep_refit["P_w"] + (d + d_meas_rows) * rep_refit["b_ind"]
                    var_r = rep_refit["var"]
                    B += amount
                    book(i, a, amount, se, label, {**extra, "reporting_model_refit": True})
                    continue
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
        if side.kind == "forecast" and rows.index is not None:
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
        elif side.kind != "forecast" and side.later_index is not None:
            # the later period's model carries the new factor on its rows from ``start`` on
            w = _share(side.later_index, start, None)
        else:
            caveats.append(
                f"{pre}static factor {a.factor!r} applied to the whole period: no row index was "
                f"given to date it from {a.start}"
            )
        mult = 1.0 + (c - 1.0) * w
        affected = w * B
        amount = (c - 1.0) * affected
        r, f = float(a.ratio), float(a.affected_share)
        v_extra = (affected * f * a.ratio_se) ** 2 + (affected * (r - 1.0) * a.share_se) ** 2
        # correlated with the projection: the baseline side's SE scales with the multiplier
        var_b = var_b * mult**2 + v_extra
        mult_total *= mult
        B += amount
        book(
            i,
            a,
            amount,
            math.sqrt(v_extra),
            f"static factor ({a.factor}): x{c:.4g} on {f:.0%}",
            {"multiplier": c, "ratio": r, "share_of_period": w},
        )
    if rep_refit is not None:
        kernel = "exact"
    return {
        "B0": B0,
        "R0": R0,
        "B": B,
        "R": R,
        "var_b": var_b,
        "var_r": var_r,
        "mult": mult_total,
        "dfs": dfs,
        "ledger": ledger,
        "bars": bars,
        "caveats": caveats,
        "kernel": kernel,
        "n_rows": None if rows.n is None else int(rows.used.sum()),
    }


def _reporting_refit(side: _Side, order, caveats: list, *, allow_rows_ops: bool, pre: str):
    """Refit a backcast's reporting model with its reporting-period indicator (decision on #21).

    A backcast's reporting side is the reporting model, fitted *through* the event, projected onto
    the baseline rows. The event inflates that fit's ``s2`` and ``rho``, so its band is far too
    wide (about 20x in the Monte Carlo of ``tests/test_mandv_mc_coverage.py``). When the indicator
    was fitted on the reporting model's own rows, its fit *is* the reporting model refitted with
    the indicator: project that (weather block, joint ``Sigma``, ``p + 1``) instead, as a
    baseline-period indicator does on the baseline side.

    Returns ``None`` (the entry is then added in quadrature to the unadjusted band, with a caveat
    that it is conservative) unless there is exactly one such entry with its fit, the summed
    rows' drivers are given, the side is a whole backcast (not a chain link) and the indicator's
    fit window matches the reporting model's fit rows (``reporting_index=``, when given).
    Otherwise returns the entry's position, the event-free projection ``P_w`` onto the summed
    rows, the measured event rows ``d_meas`` and the variance of the event-free saving.
    """
    if side.kind != "backcast":
        return None
    cands = [
        (i, a)
        for i, a in order
        if a.method == "indicator" and a.fit_period == "reporting" and a.fit is not None
    ]
    if not cands:
        return None
    rows = side.rows
    why = None
    if not allow_rows_ops:
        why = "a chain link's reporting model is shared with the chain's covariance"
    elif len(cands) > 1:
        why = "more than one reporting-period indicator (fit them jointly in one model)"
    elif rows.drivers is None:
        why = "no drivers= for the summed baseline rows"
    else:
        f = cands[0][1].fit
        if side.later_index is not None and f.n != len(side.later_index):
            why = (
                f"the indicator was fitted on {f.n} rows, not the reporting model's "
                f"{len(side.later_index)} (reporting_index=)"
            )
    if why is not None:
        caveats.append(
            f"{pre}the reporting model was not refitted with the indicator ({why}): the band is "
            "the unadjusted reporting model's, which the event inflates, so it is conservative"
        )
        return None
    from .coverage import _as_2d, _design

    i, a = cands[0]
    f = a.fit
    D = _as_2d(rows.drivers)
    W = _design(f.design, D)
    if rows.index is not None:
        ev = _event_mask(pd.DatetimeIndex(rows.index), a.start, a.end)
    else:
        ev = np.zeros(len(D), bool)
    use = (rows.used if rows.n else np.ones(len(D), bool)) & np.all(np.isfinite(W), axis=1)
    X = np.column_stack([W, ev.astype(float)])[use]
    g = X.sum(axis=0)
    beta = np.asarray(f.beta)
    Sig = np.asarray(f.sigma)
    m = int(use.sum())
    # S = O_b - (g_w' b_w + d_meas b_ind): the event-free comparison, measured noise included
    var = float(g @ Sig @ g) + f.kappa * f.s2 * m * f.noise_scale
    caveats.append(
        f"{pre}the reporting model was refitted with the indicator (p + 1, joint covariance): "
        "the band is the exact OLS kernel of the refit"
    )
    return {
        "position": i,
        "P_w": float(g[:-1] @ beta[:-1]),
        "b_ind": float(beta[-1]),
        "d_meas": float(g[-1]),
        "var": var,
    }


# ----------------------------------------------------------------- sides of each result type


def _split_std(var0, terms, pb, pr, caveats, label=""):
    """Split a standard-conditions variance between its two projected sides."""
    vb = (terms or {}).get("v_param_baseline")
    vr = (terms or {}).get("v_param_reporting")
    if _fin(vb) and _fin(vr) and (vb + vr) > 0:
        wb = vb / (vb + vr)
    else:
        wb = pb**2 / (pb**2 + pr**2) if (pb or pr) else 0.5
        caveats.append(
            f"{label}the standard-conditions band was split between its two models assuming "
            "equal relative uncertainty (no exact-kernel terms)"
        )
    return var0 * wb, var0 * (1.0 - wb)


def _single_side(result, rows, later_index, caveats) -> _Side:
    """The side of a single-model result (SavingsResult, forecast, backcast, standard cond.)."""
    from .stats import _t_value

    conf = float(result.confidence)
    band0 = result.abs_uncertainty if _fin(result.abs_uncertainty) else None
    method: str | None
    df: int | None
    base: float
    rep: float
    if hasattr(result, "baseline_projected"):  # SavingsResult (forecast / avoided energy)
        base, rep, kind, method, df = (
            float(result.baseline_projected),
            float(result.reporting_actual),
            "forecast",
            "forecast",
            None,
        )
    else:
        method = getattr(result, "method", None)
        df = getattr(result, "df", None)
        proj, meas, sav = result.projected, result.measured, result.savings
        if method == "standard_conditions":
            terms = result.sep_terms or {}
            pb, pr = terms.get("adjusted_baseline"), terms.get("adjusted_reporting")
            if pb is None or pr is None:
                raise ValueError("the standard-conditions result carries no projections to adjust")
            base, rep, kind = float(pb), float(pr), "standard_conditions"
        else:
            if proj is None or meas is None or sav is None:
                raise ValueError("the result carries no projected / measured totals to adjust")
            tol = 0.02 + 1e-9 * max(abs(proj), abs(meas))
            if method == "forecast" or (method != "backcast" and abs(sav - (proj - meas)) <= tol):
                base, rep, kind = float(proj), float(meas), "forecast"
            elif method == "backcast" or abs(sav - (meas - proj)) <= tol:
                base, rep, kind = float(meas), float(proj), "backcast"
            else:
                raise ValueError(
                    f"cannot adjust a {method!r} result whose saving is neither projected - "
                    "measured nor measured - projected"
                )
    var0 = (band0 / _t_value(conf, df)) ** 2 if band0 is not None else float("nan")
    if kind == "forecast":
        var_b, var_r = var0, 0.0
    elif kind == "backcast":
        var_b, var_r = 0.0, var0
    else:
        var_b, var_r = _split_std(var0, result.uncertainty_terms, base, rep, caveats)
    m_rows = None
    if kind == "standard_conditions":
        m_rows = ((result.coverage or {}).get("baseline") or {}).get("n_report")
    elif kind == "backcast":
        m_rows = (result.coverage or {}).get("n_used")
    return _Side(
        kind=kind,
        label="",
        base=base,
        rep=rep,
        var_b=var_b,
        var_r=var_r,
        df=df,
        kernel=getattr(result, "kernel", "g14"),
        rows=rows,
        later_index=None if later_index is None else pd.DatetimeIndex(later_index),
        m_rows=m_rows,
        rho=getattr(result, "rho", None),
        method=str(method),
    )


_LINK_KIND = {"forecast": "forecast", "backcast": "backcast"}


def _link_rows(spec) -> tuple:
    """``(_Rows, later_index, window)`` from one ``links=`` item (a dict, or ``None``)."""
    spec = dict(spec or {})
    extra = set(spec) - {"index", "drivers", "measured", "model", "reporting_index", "window"}
    if extra:
        raise ValueError(f"unknown link row key(s): {sorted(extra)}")
    rows = _Rows(spec.get("index"), spec.get("drivers"), spec.get("measured"), spec.get("model"))
    later = spec.get("reporting_index")
    win = spec.get("window")
    if win is not None:
        if not (isinstance(win, (list, tuple)) and len(win) == 2):
            raise ValueError(f"a link window must be a [start, end] pair, got {win!r}")
        win = (_day(win[0]), _day(win[1]))
    return rows, (None if later is None else pd.DatetimeIndex(later)), win


def _span(idx) -> tuple | None:
    if idx is None or len(idx) == 0:
        return None
    i = idx.tz_localize(None) if idx.tz is not None else idx
    return _day(i.min()), _day(i.max())


def _chain_sides(result, link_specs, caveats) -> tuple:
    """The two sides of an SEP chain, or one per link of a sequential chain, and their spans.

    A span is the inclusive ``(first, last)`` date range whose adjustments belong to the link.
    """
    from .stats import _t_value

    lk = list(result.links)
    specs = list(link_specs) if link_specs is not None else [None] * len(lk)
    if len(specs) != len(lk):
        raise ValueError(f"links= has {len(specs)} items; the chain has {len(lk)} links")
    parsed = [_link_rows(s) for s in specs]
    sides, spans = [], []
    if result.method == "chaining":
        u = result.uncertainty_terms or {}
        need = ("v_param_baseline", "v_param_reporting", "covariance", "v_noise_baseline",
                "v_noise_reporting")  # fmt: skip
        if any(u.get(k) is None for k in need):
            raise ValueError("the chain carries no uncertainty terms to adjust")
        l1, l2 = lk
        if not (l1.sep_terms and l2.sep_terms):
            raise ValueError("the chain's links carry no sep_terms (recompute it with this CAMBER)")
        (r1, x1, w1), (r2, x2, w2) = parsed
        b1 = float(l1.sep_terms["observed_baseline"])
        p1 = float(l1.sep_terms["adjusted_reporting"])
        p2 = float(l2.sep_terms["adjusted_baseline"])
        o2 = float(l2.sep_terms["observed_reporting"])
        m1 = ((result.coverage or {}).get("baseline") or {}).get("n_used")
        sides.append(
            _Side(
                "backcast",
                "link 1",
                b1,
                p1,
                u["v_noise_baseline"],
                u["v_param_baseline"],
                result.df,
                "exact",
                r1,
                x1,
                m1,
                result.rho,
                "backcast",
            )  # fmt: skip
        )
        sides.append(
            _Side(
                "forecast",
                "link 2",
                p2,
                o2,
                u["v_param_reporting"],
                u["v_noise_reporting"],
                result.df,
                "exact",
                r2,
                x2,
                None,
                result.rho,
                "forecast",
            )  # fmt: skip
        )
        if l1.period and l1.model_window and l2.period:
            i0, i1 = _day(l1.model_window[0]), _day(l1.model_window[1])
            spans = [
                w1 or (_day(l1.period[0]), i0 - pd.Timedelta(days=1)),
                w2 or (i1 + pd.Timedelta(days=1), _day(l2.period[1])),
            ]
            shared = (i0, i1)
        else:
            spans = [w1, w2]
            shared = None
        return sides, spans, shared
    # sequential chain: each link is its own single-model comparison
    conf = float(result.confidence)
    for k, (ln, (rows, later, win)) in enumerate(zip(lk, parsed)):
        kind = _LINK_KIND.get(ln.method) or (
            "standard_conditions" if ln.method == "standard_conditions" else None
        )
        if kind is None:
            raise ValueError(
                f"link {k + 1} is a {ln.method!r} result: adjust a nested chain's own links and "
                "chain the adjusted results instead"
            )
        t = ln.sep_terms or {}
        keys = {
            "forecast": ("adjusted_baseline", "observed_reporting"),
            "backcast": ("observed_baseline", "adjusted_reporting"),
            "standard_conditions": ("adjusted_baseline", "adjusted_reporting"),
        }[kind]
        if any(t.get(key) is None for key in keys):
            raise ValueError(f"link {k + 1} carries no sep_terms to adjust")
        base, rep = float(t[keys[0]]), float(t[keys[1]])
        band = ln.abs_uncertainty if _fin(ln.abs_uncertainty) else None
        var0 = (band / _t_value(conf, ln.df)) ** 2 if band is not None else float("nan")
        if kind == "forecast":
            vb, vr = var0, 0.0
        elif kind == "backcast":
            vb, vr = 0.0, var0
        else:
            vb, vr = _split_std(var0, ln.uncertainty_terms, base, rep, caveats, f"link {k + 1}: ")
        sides.append(
            _Side(
                kind,
                f"link {k + 1}",
                base,
                rep,
                vb,
                vr,
                ln.df,
                ln.kernel or "g14",
                rows,
                later,
                None,
                None,
                ln.method,
            )  # fmt: skip
        )
        own = rows.index if kind == "forecast" else later
        if win is None and kind == "forecast" and ln.period:
            # a link dated by its own record (sequential_chain(windows=...), e.g. from the
            # versioned baseline store): the reporting days it summed
            win = (_day(ln.period[0]), _day(ln.period[1]))
        spans.append(win or _span(own))
    return sides, spans, None


def _assign(entries, spans, shared) -> list:
    """Each entry's link: the one whose span holds its start (and end). Refuses the rest."""
    out = []
    for i, a in enumerate(entries):
        s = _day(a.start)
        e = _day(a.end) - pd.Timedelta(days=1) if getattr(a, "end", None) else s
        if shared is not None and not (e < shared[0] or s > shared[1]):
            raise ValueError(
                f"ledger entry {i} ({a.reason!r}) is dated in the intermediate period "
                f"{shared[0].date()}..{shared[1].date()}, whose model both links share: an SEP "
                "chain cannot restate one link for it (choose another intermediate period, or "
                "rebaseline)"
            )
        hits = [k for k, sp in enumerate(spans) if sp is not None and sp[0] <= s <= sp[1]]
        if any(sp is None for sp in spans) and not hits:
            raise ValueError(
                "dating adjustments against a chain's links needs each link's dates: pass links= "
                "with each link's index (a forecast link) or reporting_index (a backcast or "
                "standard-conditions link), or its window"
            )
        if len(hits) != 1:
            raise ValueError(
                f"ledger entry {i} ({a.reason!r}) dated {a.start} lies in "
                + ("no link" if not hits else f"links {[h + 1 for h in hits]}")
                + " of the chain; an adjustment applies to exactly one link"
            )
        k = hits[0]
        if e > spans[k][1]:
            raise ValueError(
                f"ledger entry {i} ({a.reason!r}) runs past the end of link {k + 1}; split it per "
                "link"
            )
        out.append(k)
    return out


def apply_adjustments(
    result,
    adjustments,
    *,
    index=None,
    drivers=None,
    measured=None,
    model=None,
    ecm_dates=(),
    settle_days: int = DEFAULT_SETTLE_DAYS,
    validity: str = "g14",
    materiality_threshold: float = 0.0,
    baseline_actual: float | None = None,
    reporting_index=None,
    links=None,
    schedule: EcmSchedule | None = None,
) -> AdjustedResult:
    """Restate a saving's baseline side for a ledger of adjustments; see the module docstring.

    ``result`` is a :class:`~camber.mandv.stats.SavingsResult` or any
    :class:`~camber.mandv.methods.MethodResult`:

    * **forecast** -- the baseline side is the baseline projection onto the reporting rows;
    * **backcast** -- the baseline side is the measured baseline energy; the reporting model was
      fitted on the later period, so an entry dated there is weighted by its share of that
      period's rows (``reporting_index=``, the reporting model's fit rows; a reporting-period
      indicator falls back to its own fit window) times the rows summed, and an indicator dated
      inside the summed baseline rows is removed from the measured side;
    * **standard conditions** -- both sides are projections onto undated standard rows: the
      baseline side ``P_b|s`` is restated, entries are weighted as for a backcast, the band is
      split between the two models by their exact-kernel terms, an ``exclude`` is refused and a
      baseline-period indicator replaces ``P_b|s`` by the augmented fit at ``drivers=`` (the
      standard drivers) with the event absent (``n_rows`` sets it present);
    * **chaining** (SEP) and **sequential_chain** -- each entry is assigned to the one link whose
      dates hold it and restates that link's baseline side only; the savings are re-summed (Eq 11)
      and the SEnPI re-multiplied (Eq 6). The SEP chain carries its shared-model covariance
      through: ``Var S = sum(var_b + var_r) - 2 c2 g_b' Sigma g_r`` with ``c2`` the multiplier a
      proportional factor applied to the forecast link's projected side. A sequential chain
      combines its adjusted links as independent (IPMVP 2012 B-19 / B-20). An entry dated in the
      SEP chain's intermediate period is refused (both links share that model), as are
      ``exclude`` entries and baseline-period refits inside a chain (recompute the chain on the
      restated rows or model instead). ``links`` gives each link's rows as a dict with ``index``,
      ``drivers``, ``measured``, ``model``, ``reporting_index`` and/or ``window``.

    The per-row detail of the rows a single result summed -- ``index``, ``drivers``,
    ``measured`` and the ``model`` that projected them -- is optional in general and required by
    an ``exclude`` entry, a baseline-period indicator, dating an indicator's rows on a forecast,
    and dating a proportional factor that starts mid-period.

    ``ecm_dates`` / ``settle_days`` (or one ``schedule``) drive the confounding guard,
    ``validity`` (:data:`VALIDITY`) the SEP evidence rule, ``materiality_threshold`` (energy
    units over the period) the materiality flag. ``baseline_actual`` (the measured
    baseline-period energy of a forecast) adds the routine adjustment bar to the waterfall. Raises
    :class:`ConfoundedAdjustment` or ``ValueError`` rather than adjusting on a refused entry. An
    empty ledger reproduces the result's own saving, SEnPI and bands.
    """
    from .stats import _t_value

    if getattr(result, "declined", False):
        raise ValueError(
            f"cannot adjust a declined saving ({getattr(result, 'declined_reason', None)})"
        )
    entries = list(adjustments)
    sched = schedule if schedule is not None else EcmSchedule(tuple(ecm_dates or ()), settle_days)
    _check_entries(entries, schedule=sched, validity=validity)
    conf = float(result.confidence)
    method = getattr(result, "method", None) if not hasattr(result, "baseline_projected") else None
    caveats: list = []
    chain = method in ("chaining", "sequential_chain")
    if chain:
        sides, spans, shared = _chain_sides(result, links, caveats)
        owner = _assign(entries, spans, shared)
    else:
        if links is not None:
            raise ValueError("links= is for chained results")
        rows = _Rows(index, drivers, measured, model)
        sides = [_single_side(result, rows, reporting_index, caveats)]
        owner = [0] * len(entries)
    outs = []
    for k, side in enumerate(sides):
        items = [(i, a) for i, a in enumerate(entries) if owner[i] == k]
        outs.append(
            _adjust_side(side, items, threshold=materiality_threshold, allow_rows_ops=not chain)
        )
    for o in outs:
        caveats.extend(c for c in o["caveats"] if c not in caveats)
    # one link: processing order (exclusions, refit, then the ledger); a chain: link by link
    bars = [b for o in outs for b in o["bars"]]
    ledger = [d for o in outs for d in o["ledger"]]
    if chain:
        ledger.sort(key=lambda d: d["position"])

    B0 = sum(o["B0"] for o in outs)
    R0 = sum(o["R0"] for o in outs)
    B = sum(o["B"] for o in outs)
    R = sum(o["R"] for o in outs)
    S = B - R
    dfs = [d for o in outs for d in o["dfs"]]
    base_df = sides[0].df if not chain else result.df
    df_all = [d for d in [base_df, *dfs] if d is not None]
    df = min(df_all) if df_all else None
    t = _t_value(conf, df)
    enpi = float(np.prod([o["R"] / o["B"] if o["B"] else float("nan") for o in outs]))
    enpi0 = float(np.prod([o["R0"] / o["B0"] if o["B0"] else float("nan") for o in outs]))

    if method == "chaining":
        cross = float(result.uncertainty_terms["covariance"])
        o1, o2 = outs
        c2 = o2["mult"]
        var = sum(o["var_b"] + o["var_r"] for o in outs) - 2.0 * c2 * cross
        var_ln = (
            sum(o["var_b"] / o["B"] ** 2 + o["var_r"] / o["R"] ** 2 for o in outs)
            - 2.0 * c2 * cross / (o1["R"] * o2["B"])
            if all(o["B"] and o["R"] for o in outs)
            else float("nan")
        )
        kernel = "exact"
    else:
        var = sum(o["var_b"] + o["var_r"] for o in outs)
        var_ln = 0.0
        for side, o in zip(sides, outs):
            if not o["B"]:
                var_ln = float("nan")
            elif side.kind == "standard_conditions":
                if o["kernel"] != "exact" or not o["R"]:
                    var_ln = float("nan")
                else:
                    var_ln += o["var_b"] / o["B"] ** 2 + o["var_r"] / o["R"] ** 2
            else:
                var_ln += (o["var_b"] + o["var_r"]) / o["B"] ** 2
        kernels = {o["kernel"] for o in outs}
        kernel = kernels.pop() if len(kernels) == 1 else "mixed"
        from .stats import _G14_CAVEAT, _G14_PROJECTED_CAVEAT

        for side, o in zip(sides, outs):
            if o["kernel"] == "g14":
                c = _G14_PROJECTED_CAVEAT if side.kind == "standard_conditions" else _G14_CAVEAT
                if c not in caveats:
                    caveats.append(c)
    band = t * math.sqrt(var) if (np.isfinite(var) and var >= 0) else None
    enpi_band = (
        t * abs(enpi) * math.sqrt(var_ln)
        if (np.isfinite(var_ln) and var_ln >= 0 and np.isfinite(enpi))
        else None
    )
    pct = 1.0 - enpi if np.isfinite(enpi) else None
    frac = band / abs(S) if (band is not None and S) else None

    wf: list = []
    if chain:
        labels = ("baseline side, summed over the links", "reporting side, summed over the links")
    elif sides[0].kind == "forecast":
        labels = ("baseline projection", "reporting actual")
    elif sides[0].kind == "backcast":
        labels = ("baseline actual", "reporting model at baseline conditions")
    else:
        labels = (
            "baseline model at standard conditions",
            "reporting model at standard conditions",
        )
    if baseline_actual is not None and not chain and sides[0].kind == "forecast":
        wf.append(WaterfallStep("baseline period actual", float(baseline_actual), "total"))
        wf.append(WaterfallStep("routine adjustment", B0 - float(baseline_actual), "delta"))
    wf.append(WaterfallStep(labels[0], B0, "total"))
    wf.extend(bars)
    wf.append(WaterfallStep("adjusted baseline", B, "total"))
    wf.append(WaterfallStep("savings", -S, "delta"))
    wf.append(WaterfallStep(labels[1] + (" (adjusted)" if R != R0 else ""), R, "total"))
    link_rows = []
    if chain:
        for side, o in zip(sides, outs):
            link_rows.append(
                {
                    "link": side.label,
                    "method": side.method,
                    "baseline": round(o["B0"], 2),
                    "reporting": round(o["R0"], 2),
                    "adjusted_baseline": round(o["B"], 2),
                    "adjusted_reporting": round(o["R"], 2),
                    "savings": round(o["B"] - o["R"], 2),
                    "enpi": _rn(o["R"] / o["B"] if o["B"] else None, 6),
                    "entries": [d["position"] for d in o["ledger"]],
                }
            )
    n_rows = None if chain else outs[0]["n_rows"]
    basis = "reporting-period conditions" if method is None else getattr(result, "basis", "")
    return AdjustedResult(
        method=str(method or "forecast"),
        basis=str(basis),
        kernel=kernel,
        confidence=conf,
        baseline=round(B0, 2),
        reporting=round(R0, 2),
        unadjusted_savings=round(B0 - R0, 2),
        unadjusted_abs_uncertainty=(
            result.abs_uncertainty if _fin(result.abs_uncertainty) else None
        ),
        adjusted_baseline=round(B, 2),
        adjusted_reporting=round(R, 2),
        savings=round(S, 2),
        savings_pct=None if pct is None else round(pct, 4),
        abs_uncertainty=None if band is None else round(band, 2),
        fractional_uncertainty=None if frac is None else round(frac, 4),
        df=df,
        n_rows=n_rows,
        ledger=ledger,
        waterfall=wf,
        caveats=caveats,
        enpi=_rn(enpi, 6),
        enpi_uncertainty=_rn(enpi_band, 6),
        unadjusted_enpi=_rn(enpi0, 6),
        links=link_rows,
        baseline_version=getattr(result, "baseline_version", None),
    )


def _rn(x, nd):
    return round(float(x), nd) if x is not None and np.isfinite(x) else None
