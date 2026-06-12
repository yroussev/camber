"""Retro-/monitoring-based-commissioning (RCx/MBCx) workflow + functional-test automation.

Commissioning verifies that systems actually deliver their design intent; monitoring-based
commissioning (MBCx) keeps doing it continuously from trend data instead of a one-time site
visit. This module supplies the two primitives that workflow needs:

- **functional_test** -- automate a Functional Performance Test (FPT). Classic commissioning
  scripts a test ("when X, the system shall do Y") and an agent watches the response. Given a
  ``condition_fn`` that, from a role-frame, returns a boolean Series marking the intervals
  where the expected functional response *was* observed, this scores the pass rate over valid
  (non-null) intervals and grades it. Turns a manual FPT into a continuous trend-based test.

- **before_after** -- the MBCx persistence check. After an intervention (a re-tune, a sequence
  fix, a setpoint change) you want to know whether a metric actually moved and whether the move
  is real or noise. Compares the mean of a metric before vs after the change timestamp; for an
  energy/fault metric lower-is-better, so an improvement is a *decrease*, and we call it
  significant only when the change exceeds half the pooled standard deviation.

References: ASHRAE Guideline 0 (The Commissioning Process) -- functional performance testing;
ASHRAE Guideline 36 (High-Performance Sequences of Operation) -- the expected functional
responses tests verify; ASHRAE Guideline 0.2 / monitoring-based commissioning practice for the
trend-based, continuous variant. Thresholds (80/95% pass rate, 0.5-sigma significance) are our
engineering judgment, not from the standards. pandas + numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask


@dataclass
class FunctionalTestResult:
    """Outcome of one automated functional performance test (FPT) over trend data."""

    name: str
    n: int                        # valid (non-null) intervals scored
    pass_rate_pct: float          # % of valid intervals meeting the expected response
    severity: str                 # "pass" | "marginal" | "fail"
    summary: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def functional_test(frame, name, condition_fn, *, occupied_only=False,
                    min_samples=10) -> FunctionalTestResult | None:
    """Score a Functional Performance Test from trend data.

    ``frame`` is a role-frame (DataFrame, or any object ``condition_fn`` understands).
    ``condition_fn(frame)`` must return a boolean pandas Series, True where the expected
    functional response was observed and NaN/null where the test is not evaluable (e.g.
    a prerequisite point is missing for that interval). The pass rate is the share of
    *valid* (non-null) intervals that are True.

    If ``occupied_only`` is set, the test is restricted to weekday-daytime occupied
    intervals (the frame must carry a DatetimeIndex). Grading: ``fail`` below 80%,
    ``marginal`` below 95%, else ``pass`` -- our judgment, per ASHRAE Guideline 0 FPT
    practice. Returns None if fewer than ``min_samples`` valid intervals remain.
    """
    result = condition_fn(frame)
    cond = pd.Series(result).astype("float")    # True->1.0, False->0.0, null stays NaN
    if occupied_only and isinstance(cond.index, pd.DatetimeIndex):
        occ = occupied_mask(cond.index)
        cond = cond[occ.reindex(cond.index, fill_value=False)]
    valid = cond.dropna()
    n = int(len(valid))
    if n < min_samples:
        return None
    pass_rate = 100.0 * float((valid > 0.5).mean())
    if pass_rate < 80.0:
        severity = "fail"
    elif pass_rate < 95.0:
        severity = "marginal"
    else:
        severity = "pass"
    return FunctionalTestResult(
        name=str(name),
        n=n,
        pass_rate_pct=round(pass_rate, 2),
        severity=severity,
        summary=(f"{name}: {severity} -- met expected response in "
                 f"{pass_rate:.0f}% of {n} valid intervals"),
    )


@dataclass
class MeasureResult:
    """Before/after comparison of a metric across an intervention (MBCx persistence)."""

    metric_before: float
    metric_after: float
    delta: float                  # after - before (negative = improvement, lower-is-better)
    pct_change: float             # % change relative to before
    improved: bool                # after mean < before mean
    significant: bool             # |delta| > 0.5 * pooled std
    n_before: int
    n_after: int
    summary: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def before_after(series, change_time, *, min_samples=10) -> MeasureResult | None:
    """Compare a metric's mean before vs after an intervention timestamp.

    For an energy/fault metric (lower-is-better): ``improved`` when the after-mean is
    below the before-mean, and ``significant`` when the absolute change exceeds half the
    pooled standard deviation of the two periods -- a coarse effect-size screen, our
    judgment, not a formal hypothesis test. This is the MBCx persistence check: did the
    measure stick? Intervals at or after ``change_time`` are the "after" period.

    Returns None if either side has fewer than ``min_samples`` valid (non-null) points.
    """
    s = series.dropna()
    ct = pd.Timestamp(change_time)
    before = s[s.index < ct]
    after = s[s.index >= ct]
    if len(before) < min_samples or len(after) < min_samples:
        return None
    b_mean = float(before.mean())
    a_mean = float(after.mean())
    delta = a_mean - b_mean
    nb, na = int(len(before)), int(len(after))
    # pooled (within-group) standard deviation
    b_var = float(before.var(ddof=1)) if nb > 1 else 0.0
    a_var = float(after.var(ddof=1)) if na > 1 else 0.0
    dof = (nb - 1) + (na - 1)
    pooled_std = float(np.sqrt(((nb - 1) * b_var + (na - 1) * a_var) / dof)) if dof > 0 else 0.0
    improved = a_mean < b_mean
    significant = abs(delta) > 0.5 * pooled_std
    pct = 100.0 * delta / b_mean if b_mean else float("nan")
    direction = "improved" if improved else "regressed"
    sig = "significant" if significant else "not significant"
    return MeasureResult(
        metric_before=round(b_mean, 4),
        metric_after=round(a_mean, 4),
        delta=round(delta, 4),
        pct_change=round(pct, 2) if pct == pct else float("nan"),
        improved=bool(improved),
        significant=bool(significant),
        n_before=nb,
        n_after=na,
        summary=(f"{direction} {abs(pct):.0f}% ({b_mean:.3g} -> {a_mean:.3g}), {sig}"
                 if pct == pct else f"{direction} ({b_mean:.3g} -> {a_mean:.3g}), {sig}"),
    )


# --- measure register (MBCx lifecycle) ---------------------------------------- #

@dataclass
class MeasureRecord:
    """One tracked measure: its before/after result mapped to a lifecycle status."""

    name: str
    status: str                   # "verified" | "regressed" | "inconclusive" | "insufficient"
    result: dict | None           # MeasureResult.as_dict(), or None if not assessable

    def as_dict(self) -> dict:
        """Return the record as a plain dict."""
        return {"name": self.name, "status": self.status, "result": self.result}


@dataclass
class RegisterReport:
    """A portfolio of tracked measures with their persistence verdicts."""

    records: list                 # [MeasureRecord]
    n: int
    n_verified: int               # improved and significant
    n_regressed: int              # got worse and significant
    n_inconclusive: int           # no significant change
    n_insufficient: int           # not enough data either side
    summary: str

    def as_dict(self) -> dict:
        """Return the report (with nested records) as a plain dict."""
        return {"records": [r.as_dict() for r in self.records], "n": self.n,
                "n_verified": self.n_verified, "n_regressed": self.n_regressed,
                "n_inconclusive": self.n_inconclusive,
                "n_insufficient": self.n_insufficient, "summary": self.summary}


def track_measures(measures) -> RegisterReport:
    """Run the before/after persistence check across a set of measures (the MBCx register).

    ``measures`` is an iterable of dicts: ``{"name", "series", "change_time"}`` (plus an
    optional ``"min_samples"``). Each is graded to a commissioning lifecycle status:
    **verified** (improved and significant), **regressed** (worse and significant),
    **inconclusive** (no significant change), or **insufficient** (too little data). This
    is the standing MBCx record -- which fixes stuck, which drifted back.
    """
    records = []
    for m in measures:
        res = before_after(m["series"], m["change_time"],
                           min_samples=m.get("min_samples", 10))
        if res is None:
            status, payload = "insufficient", None
        elif not res.significant:
            status, payload = "inconclusive", res.as_dict()
        elif res.improved:
            status, payload = "verified", res.as_dict()
        else:
            status, payload = "regressed", res.as_dict()
        records.append(MeasureRecord(name=m["name"], status=status, result=payload))

    def _count(s):
        return sum(1 for r in records if r.status == s)
    nv, nr, ni, nx = _count("verified"), _count("regressed"), _count("inconclusive"), _count("insufficient")
    return RegisterReport(
        records=records, n=len(records), n_verified=nv, n_regressed=nr,
        n_inconclusive=ni, n_insufficient=nx,
        summary=(f"{len(records)} measures tracked: {nv} verified, {nr} regressed, "
                 f"{ni} inconclusive, {nx} insufficient data"),
    )
