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

from dataclasses import dataclass

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
    fraction: float          # flagged / total, 0..1
    mask: pd.Series          # True = non-routine day, indexed by date
    model_kind: str          # baseline model used to compute residuals

    def as_dict(self) -> dict:
        """Return the summary (excluding the mask) as a plain dict."""
        return {"n_total": self.n_total, "n_flagged": self.n_flagged,
                "fraction": self.fraction, "model_kind": self.model_kind}


def residual_outliers(daily_energy: pd.Series, daily_oat: pd.Series, model, *,
                      z: float = 3.5) -> pd.Series:
    """Boolean mask of days whose energy is a robust outlier vs the model prediction."""
    resid = pd.Series(daily_energy.to_numpy() - model.predict(daily_oat.to_numpy()),
                      index=daily_energy.index)
    return outlier_mask(resid, cutoff=z)


def detect_non_routine(energy: pd.Series, temp: pd.Series, *,
                       rate_is_energy_rate: bool = False, z: float = 3.5,
                       min_days: int = 10) -> NonRoutineResult:
    """Flag non-routine days in an (energy, temp) period against its weather baseline.

    Aggregates to daily, fits a change-point baseline, and flags days whose residual
    is a robust (MAD) outlier at modified-z > ``z``.
    """
    df = daily_energy_vs_temp(energy, temp, rate_is_energy_rate=rate_is_energy_rate)
    if len(df) < min_days:
        raise ValueError(f"need >= {min_days} days, got {len(df)}")
    model = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
    mask = residual_outliers(df["energy"], df["oat"], model, z=z)
    return NonRoutineResult(n_total=len(df), n_flagged=int(mask.sum()),
                            fraction=round(float(mask.mean()), 4), mask=mask,
                            model_kind=model.kind)


@dataclass
class StepChangeResult:
    """A sustained level shift in the weather-baseline residuals."""

    detected: bool
    date: pd.Timestamp | None    # first day of the shifted (post-step) segment
    delta: float                 # post-step minus pre-step mean residual (energy units)
    rel_shift: float             # standardized shift magnitude (vs residual noise)
    pre_mean: float              # mean residual before the step
    post_mean: float             # mean residual on/after the step
    n_pre: int
    n_post: int
    n_days: int
    model_kind: str
    mask: pd.Series              # True on/after the step (the non-routine segment)

    def as_dict(self) -> dict:
        """Return the summary (excluding the mask) as a plain dict."""
        return {"detected": self.detected,
                "date": None if self.date is None else str(self.date.date()),
                "delta": self.delta, "rel_shift": self.rel_shift,
                "pre_mean": self.pre_mean, "post_mean": self.post_mean,
                "n_pre": self.n_pre, "n_post": self.n_post,
                "n_days": self.n_days, "model_kind": self.model_kind}


def detect_step_change(energy: pd.Series, temp: pd.Series, *,
                       rate_is_energy_rate: bool = False, z: float = 3.5,
                       min_segment_days: int = 14) -> StepChangeResult:
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

    This is single-step binary segmentation; repeated steps can be found by
    re-running on each segment (a documented future refinement).
    """
    df = daily_energy_vs_temp(energy, temp, rate_is_energy_rate=rate_is_energy_rate)
    n = len(df)
    if n < 2 * min_segment_days:
        raise ValueError(f"need >= {2 * min_segment_days} days, got {n}")
    model = best_model(df["oat"].to_numpy(), df["energy"].to_numpy())
    resid = df["energy"].to_numpy() - model.predict(df["oat"].to_numpy())

    # prefix sums of resid and resid^2 -> each split's segment means/SS in O(1)
    csum = np.concatenate([[0.0], np.cumsum(resid)])
    csum2 = np.concatenate([[0.0], np.cumsum(resid ** 2)])
    best_i, best_stat, best_delta = None, -1.0, 0.0
    for i in range(min_segment_days, n - min_segment_days + 1):
        n_pre, n_post = i, n - i
        s_pre, s_post = csum[i], csum[n] - csum[i]
        q_pre, q_post = csum2[i], csum2[n] - csum2[i]
        delta = s_post / n_post - s_pre / n_pre
        ss = (q_pre - s_pre ** 2 / n_pre) + (q_post - s_post ** 2 / n_post)
        pooled_var = ss / (n - 2)
        if pooled_var <= 0:                      # perfectly clean split
            stat = np.inf if delta != 0 else 0.0
        else:
            stat = abs(delta) / np.sqrt(pooled_var * (1.0 / n_pre + 1.0 / n_post))
        if stat > best_stat:
            best_i, best_stat, best_delta = i, stat, delta

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
        n_pre=int(best_i), n_post=int(n - best_i), n_days=n,
        model_kind=model.kind, mask=mask)
