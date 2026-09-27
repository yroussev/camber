"""Non-routine event (NRE) detection for M&V.

Routine adjustment models explain energy as a function of weather. A *non-routine
event* -- a shutdown, an occupancy change, an equipment left running, a meter
outage -- is by definition what the weather model can't explain, and it corrupts
both the baseline fit and the savings calc if left in (IPMVP / ASHRAE G14 call for
identifying and adjusting for these).

Approach: fit the weather baseline, then flag intervals whose residual (actual -
model) is a robust outlier (median/MAD modified z-score, reusing the data-quality
screen). A shutdown shows up as a large negative residual, an anomaly as a large
positive one; weather-extreme days the model already explains are *not* flagged.
Flagged baseline intervals can then be excluded before refitting a clean baseline.

This module provides two complementary detectors:

* :func:`detect_non_routine` -- point-wise: flags individual days whose residual is
  a robust outlier. Catches one-off shutdowns/spikes.
* :func:`detect_step_change` -- sustained level shift: flags a *persistent* change
  in the mean residual (a new operating level held for weeks/months). Catches what
  the point-wise screen misses, because each day of a gradual monthly escalation is
  only mildly off on its own but the segment mean shifts significantly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..ingest.quality import outlier_mask
from .intervalfit import daily_energy_vs_temp
from .models import best_model


@dataclass
class NonRoutineResult:
    """Which daily intervals look non-routine relative to the weather baseline."""

    n_total: int
    n_flagged: int
    fraction: float  # flagged / total, 0..1
    mask: pd.Series  # True = non-routine day, indexed by date
    model_kind: str  # baseline model used to compute residuals

    def as_dict(self) -> dict:
        """Return the summary (excluding the mask) as a plain dict."""
        return {
            "n_total": self.n_total,
            "n_flagged": self.n_flagged,
            "fraction": self.fraction,
            "model_kind": self.model_kind,
        }


def residual_outliers(
    daily_energy: pd.Series, daily_oat: pd.Series, model, *, z: float = 3.5
) -> pd.Series:
    """Boolean mask of days whose energy is a robust outlier vs the model prediction."""
    resid = pd.Series(
        daily_energy.to_numpy() - model.predict(daily_oat.to_numpy()), index=daily_energy.index
    )
    return outlier_mask(resid, cutoff=z)


def detect_non_routine(
    energy: pd.Series,
    temp: pd.Series,
    *,
    rate_is_energy_rate: bool = False,
    z: float = 3.5,
    min_days: int = 10,
) -> NonRoutineResult:
    """Flag non-routine days in an (energy, temp) period against its weather baseline.

    Aggregates to daily, fits a change-point baseline, and flags days whose residual
    is a robust (MAD) outlier at modified-z > ``z``.
    """
    df = daily_energy_vs_temp(energy, temp, rate_is_energy_rate=rate_is_energy_rate)
    if len(df) < min_days:
        raise ValueError(f"need >= {min_days} days, got {len(df)}")
    model = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
    mask = residual_outliers(df["energy"], df["oat"], model, z=z)
    return NonRoutineResult(
        n_total=len(df),
        n_flagged=int(mask.sum()),
        fraction=round(float(mask.mean()), 4),
        mask=mask,
        model_kind=model.kind,
    )


@dataclass
class StepChangeResult:
    """A sustained level shift in the weather-baseline residuals."""

    detected: bool
    date: pd.Timestamp | None  # first day of the shifted (post-step) segment
    delta: float  # post-step minus pre-step mean residual (energy units)
    rel_shift: float  # standardized shift magnitude (vs residual noise)
    pre_mean: float  # mean residual before the step
    post_mean: float  # mean residual on/after the step
    n_pre: int
    n_post: int
    n_days: int
    model_kind: str
    mask: pd.Series  # True on/after the step (the non-routine segment)
    rho: float | None = None  # residual lag-1 autocorrelation, when autocorrelation=True

    def as_dict(self) -> dict:
        """Return the summary (excluding the mask) as a plain dict; ``rho`` only when set."""
        d = {
            "detected": self.detected,
            "date": None if self.date is None else str(self.date.date()),
            "delta": self.delta,
            "rel_shift": self.rel_shift,
            "pre_mean": self.pre_mean,
            "post_mean": self.post_mean,
            "n_pre": self.n_pre,
            "n_post": self.n_post,
            "n_days": self.n_days,
            "model_kind": self.model_kind,
        }
        if self.rho is not None:
            d["rho"] = self.rho
        return d


def detect_step_change(
    energy: pd.Series,
    temp: pd.Series,
    *,
    rate_is_energy_rate: bool = False,
    z: float = 3.5,
    min_segment_days: int = 14,
    autocorrelation: bool = False,
) -> StepChangeResult:
    """Detect a sustained level shift in the daily weather-baseline residuals.

    Aggregates to daily energy, fits a change-point weather baseline, then scans
    every split (with at least ``min_segment_days`` on each side) for the largest
    two-sample shift in the residuals, scored by the classic structural-break
    t-statistic::

        stat = |mean(post) - mean(pre)| / sqrt(pooled_var * (1/n_pre + 1/n_post))

    where ``pooled_var`` is the within-segment residual variance at that split. A
    step is reported when the best ``stat`` reaches ``z``. Using the *within-segment*
    scatter (rather than the whole-series spread) is what separates a genuine
    sustained shift -- which leaves each segment tight -- from a single spike or a
    smooth seasonal drift, which keep within-segment variance high and the statistic
    low. So a one-day shutdown (already handled by :func:`detect_non_routine`) does
    not register here. ``mask`` flags the post-step segment, ready to exclude or
    re-baseline like a point-wise non-routine flag.

    This is single-step; :func:`detect_step_changes` finds several steps at once.

    ``autocorrelation=True`` (recommended; off by default so existing results do not move)
    divides the statistic by ``sqrt(kappa)``, ``kappa = (1+rho)/(1-rho)`` with ``rho`` the lag-1
    autocorrelation of the residuals: serially correlated residuals make a segment mean far less
    certain than independent ones, and daily whole-building residuals routinely have rho around
    0.6 (the BDG2 median), which inflates the uncorrected statistic about 2x. The result then
    carries ``rho``.
    """
    df = daily_energy_vs_temp(energy, temp, rate_is_energy_rate=rate_is_energy_rate)
    n = len(df)
    if n < 2 * min_segment_days:
        raise ValueError(f"need >= {2 * min_segment_days} days, got {n}")
    model = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
    resid = df["energy"].to_numpy() - model.predict(df["oat"].to_numpy())

    # prefix sums of resid and resid^2 -> each split's segment means/SS in O(1)
    csum = np.concatenate([[0.0], np.cumsum(resid)])
    csum2 = np.concatenate([[0.0], np.cumsum(resid**2)])
    best_i, best_stat, best_delta = None, -1.0, 0.0
    for i in range(min_segment_days, n - min_segment_days + 1):
        n_pre, n_post = i, n - i
        s_pre, s_post = csum[i], csum[n] - csum[i]
        q_pre, q_post = csum2[i], csum2[n] - csum2[i]
        delta = s_post / n_post - s_pre / n_pre
        ss = (q_pre - s_pre**2 / n_pre) + (q_post - s_post**2 / n_post)
        pooled_var = ss / (n - 2)
        if pooled_var <= 0:  # perfectly clean split
            stat = np.inf if delta != 0 else 0.0
        else:
            stat = abs(delta) / np.sqrt(pooled_var * (1.0 / n_pre + 1.0 / n_post))
        if stat > best_stat:
            best_i, best_stat, best_delta = i, stat, delta

    rho = None
    if autocorrelation:
        from .stats import lag1_autocorrelation

        rho = lag1_autocorrelation(resid, index=df.index)
        if rho is not None:
            best_stat = best_stat / float(np.sqrt((1.0 + rho) / (1.0 - rho)))
    # the loop always runs (range is non-empty given n >= 2*min_segment_days) and the first
    # iteration sets best_i, since any stat > the -1.0 seed; so best_i is never None here.
    assert best_i is not None
    date = df.index[best_i]
    detected = bool(best_stat >= z)
    mask = pd.Series((df.index >= date) if detected else False, index=df.index)
    pre_mean = float(csum[best_i] / best_i)
    post_mean = float((csum[n] - csum[best_i]) / (n - best_i))
    return StepChangeResult(
        detected=detected,
        date=date if detected else None,
        delta=round(float(best_delta), 4),
        rel_shift=round(float(best_stat), 3),
        pre_mean=round(pre_mean, 4),
        post_mean=round(post_mean, 4),
        n_pre=int(best_i),
        n_post=int(n - best_i),
        n_days=n,
        model_kind=model.kind,
        mask=mask,
        rho=None if rho is None else round(rho, 4),
    )


# --------------------------------------------------------------------------- multi-step (PELT)


@dataclass
class StepChange:
    """One detected step: the first day of the new level and its size (post minus pre)."""

    date: pd.Timestamp
    delta: float  # post-step minus pre-step level, energy units per day
    se: float | None  # standard error of delta (rho-inflated), None if not estimable
    z: float | None  # delta / se

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return {"date": str(self.date.date()), "delta": self.delta, "se": self.se, "z": self.z}


@dataclass
class StepChangesResult:
    """Sustained level shifts found by PELT on the weather-model residuals, refitted jointly.

    ``steps`` are in date order. ``levels`` are the fitted segment levels relative to the first
    segment (the indicator coefficients), ``segment`` numbers every day's segment (0 = before the
    first step). ``rho`` / ``sigma`` are the residual lag-1 autocorrelation and standard deviation
    of the final segmented fit; ``penalty`` the per-step penalty in units of the rho-inflated
    variance. ``converged`` is false when the step set was still changing after ``max_iter``
    rounds.
    """

    steps: list
    levels: list
    n_days: int
    model_kind: str
    change_points: tuple  # of the weather model refitted with the segment indicators
    rho: float | None
    sigma: float
    penalty: float
    iterations: int
    converged: bool
    segment: pd.Series  # segment number per day
    caveats: list = field(default_factory=list)

    @property
    def detected(self) -> bool:
        """Whether any step was found."""
        return bool(self.steps)

    def as_dict(self) -> dict:
        """Return the summary (excluding the per-day segment series) as a plain dict."""
        return {
            "detected": self.detected,
            "steps": [s.as_dict() for s in self.steps],
            "levels": self.levels,
            "n_days": self.n_days,
            "model_kind": self.model_kind,
            "change_points": list(self.change_points),
            "rho": self.rho,
            "sigma": self.sigma,
            "penalty": self.penalty,
            "iterations": self.iterations,
            "converged": self.converged,
            "caveats": list(self.caveats),
        }


def _pelt(x: np.ndarray, *, scale: float, penalty: float, min_seg: int) -> list:
    """Optimal mean-change segmentation of ``x`` by PELT (Killick, Fearnhead & Eckley 2012).

    Gaussian cost with known variance ``scale``: a segment costs its within-segment sum of squares
    divided by ``scale``; each change costs ``penalty``. Segments are at least ``min_seg`` long.
    Returns the change positions (first index of each new segment), ascending.
    """
    n = len(x)
    s1 = np.concatenate([[0.0], np.cumsum(x)])
    s2 = np.concatenate([[0.0], np.cumsum(x * x)])

    def cost(s: np.ndarray, t: int) -> np.ndarray:
        L = t - s
        return ((s2[t] - s2[s]) - (s1[t] - s1[s]) ** 2 / L) / scale

    F = np.full(n + 1, np.inf)
    F[0] = -penalty
    last = np.zeros(n + 1, dtype=int)
    cand = np.array([0])
    for t in range(min_seg, n + 1):
        ok = cand[t - cand >= min_seg]
        if len(ok):
            vals = F[ok] + cost(ok, t) + penalty
            j = int(np.argmin(vals))
            F[t], last[t] = float(vals[j]), int(ok[j])
            # prune (the K = 0 PELT rule): a start that cannot beat F[t] now never will
            waiting = cand[t - cand < min_seg]
            keep = ok[F[ok] + cost(ok, t) <= F[t]]
            cand = np.concatenate([keep, waiting])
        if t <= n - min_seg:
            cand = np.append(cand, t)
    cps = []
    t = n
    while t > 0:
        s = int(last[t])
        if s > 0:
            cps.append(s)
        t = s
    return sorted(cps)


def _pelt_capped(
    x: np.ndarray, *, scale: float, penalty: float, min_seg: int, max_steps: int, floor: float = 0.0
) -> tuple | None:
    """:func:`_pelt` with at most ``max_steps`` changes: the penalty is raised x1.5 at a time.

    Returns ``(change positions, penalty used)``, or ``None`` when the noise scale is zero, not
    finite or at most ``floor`` (an exact fit: a constant or dead meter, or a noise-free series).
    There the Gaussian cost is 0/0 or pure rounding and no penalty would prune the segmentation, so
    the caller stops. The single guard for every PELT round with a noise scale estimated from a
    refit (:func:`detect_step_changes` and the rebaseline T1 savings-step search).
    """
    if not (np.isfinite(scale) and scale > floor and scale > 0):
        return None
    p_used = float(penalty)
    new = _pelt(x, scale=scale, penalty=p_used, min_seg=min_seg)
    for _ in range(200):  # 1.5**200 ~ 1e35: unreachable above the floor; a guard, not a limit
        if len(new) <= max_steps:
            return new, p_used
        p_used *= 1.5
        new = _pelt(x, scale=scale, penalty=p_used, min_seg=min_seg)
    return None


def _segment_ids(n: int, cps: list) -> np.ndarray:
    seg = np.zeros(n, dtype=int)
    for c in cps:
        seg[c:] += 1
    return seg


def _cp_candidates(kind: str, T: np.ndarray, cps0: tuple) -> list:
    """Change-point sets to search when refitting ``kind`` with segment indicators."""
    from .models import _grid

    if not cps0:  # 2P, or a 5P / 5PZ that fell back to a line
        return [()]
    grid = _grid(T)
    if len(cps0) == 1:
        return [(float(tc),) for tc in grid]
    step = grid[1] - grid[0]
    return [
        (float(lo), float(hi)) for i, lo in enumerate(grid) for hi in grid[i:] if hi - lo >= step
    ]


def _segmented_fit(T, y, kind, cps0, seg):
    """Refit ``kind`` with one level indicator per segment after the first (change points
    re-searched on the same grid). Returns (weather-part prediction, indicator coefs, cps, A, s2,
    resid)."""
    from .models import N_PARAMS, _design_for

    k = int(seg.max())
    ind = (seg[:, None] == np.arange(1, k + 1)[None, :]).astype(float)
    best = None
    for cps in _cp_candidates(kind, T, cps0):
        W = _design_for(kind, cps)(T)
        X = np.hstack([W, ind])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        r = y - X @ beta
        sse = float(r @ r)
        if best is None or sse < best[0]:
            best = (sse, cps, W, X, beta, r)
    assert best is not None
    sse, cps, W, X, beta, r = best
    nw = W.shape[1]
    dof = len(y) - (N_PARAMS[kind] + k)
    s2 = sse / dof if dof > 0 else float("nan")
    A = np.linalg.pinv(X.T @ X)
    return W @ beta[:nw], beta[nw:], cps, A[nw:, nw:], s2, r


def _refit(T, y, model, kind, cps0, cps, n):
    """The weather fit given step positions ``cps`` (the plain fit when there are none)."""
    if cps:
        weather, levels, wcps, A_lvl, s2, resid = _segmented_fit(
            T, y, kind, cps0, _segment_ids(n, cps)
        )
        return weather, levels, A_lvl, wcps, s2, resid
    weather = np.asarray(model.predict(T), dtype=float)
    resid = y - weather
    return weather, np.zeros(0), np.zeros((0, 0)), cps0, float(np.var(resid, ddof=1)), resid


def detect_step_changes(
    energy: pd.Series,
    temp: pd.Series,
    *,
    rate_is_energy_rate: bool = False,
    min_segment_days: int = 28,
    max_steps: int = 5,
    penalty: float | None = None,
    max_iter: int = 3,
) -> StepChangesResult:
    """Detect several sustained level shifts in daily energy against its weather baseline.

    1. Aggregate to daily energy and fit a change-point weather baseline (:func:`best_model`).
    2. Segment the residual series with **PELT** (Killick, Fearnhead & Eckley 2012), a Gaussian
       mean-change cost whose variance is the residual variance inflated for serial correlation,
       ``sigma^2 * kappa`` with ``kappa = (1+rho)/(1-rho)`` -- the variance of a segment mean
       under AR(1) residuals -- and an mBIC-style penalty of ``3 ln n`` per step (the penalty the
       R ``changepoint`` package uses for MBIC); segments are at least ``min_segment_days`` long.
       This is the method of Touzani, Ravache, Crowe & Granderson (*Energy & Buildings* 185, 2019),
       applied here to the model residuals.
    3. Refit the weather model with **one level indicator per segment** (change points re-searched)
       so a step is not absorbed into the temperature slope, recompute ``sigma``, ``rho`` and the
       residuals-plus-levels series, and segment again -- until the step set is stable, at most
       ``max_iter`` rounds.

    More than ``max_steps`` steps raise the penalty (x1.5 at a time) until at most that many
    remain. Each step reports its size (post minus pre level) with a rho-inflated standard error.
    Unlike :func:`detect_step_change` (one split, independent residuals) and
    :func:`camber.changedetect.detect_level_shifts` (greedy binary segmentation), the segmentation
    is jointly optimal for the penalty and accounts for autocorrelation.
    """
    from .stats import lag1_autocorrelation

    df = daily_energy_vs_temp(energy, temp, rate_is_energy_rate=rate_is_energy_rate)
    n = len(df)
    if n < 2 * min_segment_days:
        raise ValueError(f"need >= {2 * min_segment_days} days, got {n}")
    if max_steps < 1 or max_iter < 1:
        raise ValueError("max_steps and max_iter must be >= 1")
    T = df["oat"].to_numpy(dtype=float)
    y = df["energy"].to_numpy(dtype=float)
    model = best_model(T, y)
    kind, cps0 = model.kind, tuple(model.change_points)
    pen = float(penalty) if penalty is not None else 3.0 * float(np.log(n))
    caveats: list = []

    # Round 1 cannot trust the weather fit's residual variance or rho: a step inflates both (and
    # makes rho look near 1). It segments liberally on the first-difference noise scale, which a
    # level shift barely touches; every later round uses sigma^2 * kappa from the segmented refit,
    # which prunes what round 1 over-found. The reported steps always come from a later round.
    x = y - np.asarray(model.predict(T), dtype=float)
    d = np.diff(x)
    scale = (1.4826 * float(np.median(np.abs(d - np.median(d))))) ** 2 / 2.0
    if not scale > 0:
        scale = float(np.var(x, ddof=1)) or 1.0
    cps_found = _pelt(x, scale=scale, penalty=pen, min_seg=min_segment_days)
    weather, levels, A_lvl, wcps, s2, resid = _refit(T, y, model, kind, cps0, cps_found, n)
    converged = False
    it = 1
    while it < max_iter + 1:
        it += 1
        rho = lag1_autocorrelation(resid, index=df.index)
        kappa = 1.0 if rho is None else (1.0 + rho) / (1.0 - rho)
        x = y - weather  # residual plus the segment levels
        # an exact fit (a constant or dead meter) gives a 0/0 cost that no penalty prunes: keep
        # the steps found so far and stop. The floor is rounding noise on the meter's own scale.
        found = _pelt_capped(
            x,
            scale=s2 * kappa,
            penalty=pen,
            min_seg=min_segment_days,
            max_steps=max_steps,
            floor=(1e-9 * float(np.mean(np.abs(y)))) ** 2,
        )
        if found is None:
            caveats.append("the residual variance is zero or not finite; segmentation stopped")
            converged = True
            break
        new, p_used = found
        if p_used != pen:
            note = f"penalty raised to {p_used:.1f} to keep at most max_steps={max_steps} steps"
            caveats = [c for c in caveats if not c.startswith("penalty raised")] + [note]
        stable = new == cps_found
        cps_found = new
        weather, levels, A_lvl, wcps, s2, resid = _refit(T, y, model, kind, cps0, cps_found, n)
        if stable:
            converged = True
            break
    if not converged:
        caveats.append(f"the step set was still changing after max_iter={max_iter} rounds")
    rho = lag1_autocorrelation(resid, index=df.index)
    kappa = 1.0 if rho is None else (1.0 + rho) / (1.0 - rho)
    full = np.concatenate([[0.0], levels])
    steps = []
    for i, c in enumerate(cps_found):
        delta = float(full[i + 1] - full[i])
        # Var(c_i+1 - c_i) from the joint (X'X)^-1 of the indicator block, rho-inflated
        v = A_lvl[i, i] if i < len(A_lvl) else float("nan")
        if i > 0:
            v = v + A_lvl[i - 1, i - 1] - 2 * A_lvl[i, i - 1]
        var = kappa * s2 * v
        se = float(np.sqrt(var)) if np.isfinite(var) and var > 0 else None
        steps.append(
            StepChange(
                date=df.index[c],
                delta=round(delta, 4),
                se=None if se is None else round(se, 4),
                z=None if se is None else round(delta / se, 3),
            )
        )
    return StepChangesResult(
        steps=steps,
        levels=[round(float(v), 4) for v in full],
        n_days=n,
        model_kind=kind,
        change_points=tuple(round(float(c), 3) for c in wcps),
        rho=None if rho is None else round(rho, 4),
        sigma=round(float(np.sqrt(s2)), 4),
        penalty=round(pen, 3),
        iterations=it,
        converged=converged,
        segment=pd.Series(_segment_ids(n, cps_found), index=df.index, name="segment"),
        caveats=caveats,
    )
