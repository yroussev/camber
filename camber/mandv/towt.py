"""Time-Of-Week & Temperature (TOWT) regression baseline.

A schedule-aware M&V baseline for loads driven by occupancy as much as weather.
Whole-building electricity, in particular, follows a weekly schedule (occupied
ramps, nights, weekends) that a plain temperature change-point model cannot
capture -- so it underfits. TOWT adds the weekly shape explicitly:

  E_t = sum_b  alpha_b * 1[hour-of-week(t) = b]      (one level per TOW bin)
      + sum_j  beta_j  * tcomp_j(T_t)                 (piecewise-linear in temp)

The time-of-week term is a set of mutually exclusive hour-of-week indicators
(default 168 = 24*7), so each bin carries its own baseline level (and together
they span the intercept). The temperature term is a continuous piecewise-linear
function built from a small set of breakpoints; optionally it is fit separately
for occupied vs unoccupied bins (occupancy inferred from the load level in each
bin), since a building's weather sensitivity differs by mode.

Fit by ordinary least squares (numpy). For weather-dominated loads (cooling /
heating energy) the change-point models in :mod:`camber.mandv.models` are usually
enough; reach for TOWT when load is largely schedule-driven and hourly.

Reference: Mathieu, Price, Kiliccote, Piette, "Quantifying Changes in Building
Electricity Use, with Application to Demand Response," IEEE Trans. Smart Grid
2(3), 2011 (LBNL). Method described and reimplemented independently here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def hour_of_week(index: pd.DatetimeIndex) -> np.ndarray:
    """Map timestamps to an hour-of-week bin in 0..167 (Mon 00:00 = 0)."""
    idx = pd.DatetimeIndex(index)
    return idx.dayofweek.to_numpy() * 24 + idx.hour.to_numpy()


def _temp_breakpoints(temp: np.ndarray, n_segments: int) -> np.ndarray:
    """Quantile-spaced temperature breakpoints (avoids empty segments)."""
    t = temp[np.isfinite(temp)]
    qs = np.linspace(0.0, 1.0, n_segments + 1)
    bps = np.unique(np.quantile(t, qs))
    if len(bps) < 2:  # degenerate (constant temp)
        bps = np.array([t.min(), t.min() + 1.0])
    return bps


def _temp_components(temp: np.ndarray, breakpoints: np.ndarray) -> np.ndarray:
    """Continuous piecewise-linear basis: column j has slope 1 within segment j.

    Sum_j beta_j * comp_j(T) is a continuous piecewise-linear function of T whose
    slope in segment j is beta_j.
    """
    n_seg = len(breakpoints) - 1
    out = np.zeros((len(temp), n_seg), dtype="float64")
    t = np.asarray(temp, dtype="float64")
    for j in range(n_seg):
        lo, hi = breakpoints[j], breakpoints[j + 1]
        out[:, j] = np.clip(t - lo, 0.0, hi - lo)
    return np.nan_to_num(out)


def _build_design(
    tow: np.ndarray,
    temp: np.ndarray,
    bins: np.ndarray,
    breakpoints: np.ndarray,
    occ_bins: frozenset | None,
) -> np.ndarray:
    """Assemble [TOW one-hot | temperature components] for the given layout."""
    # time-of-week one-hot, columns in `bins` order
    onehot = (tow[:, None] == bins[None, :]).astype("float64")
    tcomp = _temp_components(temp, breakpoints)
    if occ_bins is None:
        return np.hstack([onehot, tcomp])
    # occupied/unoccupied split: route temp components by each row's bin mode
    occ = np.array([b in occ_bins for b in tow], dtype=bool)
    occ_block = tcomp * occ[:, None]
    unocc_block = tcomp * (~occ)[:, None]
    return np.hstack([onehot, occ_block, unocc_block])


@dataclass
class TOWTModel:
    """A fitted Time-of-Week & Temperature model."""

    bins: np.ndarray  # TOW bin ids present at fit (one-hot column order)
    breakpoints: np.ndarray  # temperature segment breakpoints
    occ_bins: frozenset | None  # occupied TOW bins (None if no occ/unocc split)
    beta: np.ndarray  # OLS coefficients
    n_params: int  # effective parameters (design rank), for stats dof
    split: bool

    def predict(self, index, temp) -> np.ndarray:
        """Predict energy for the given timestamps and temperatures.

        Raises ``ValueError`` if any timestamp falls in an hour-of-week bin the fit never saw. The
        one-hot design has a column per *observed* bin, so an unseen bin would otherwise produce an
        all-zero row and the prediction would silently collapse to the temperature term alone --
        measurably, a weekday-only fit projected onto a weekend predicts **-1.2** where the truth is
        **40**. A negative baseline reads downstream as a large negative saving, with no error and
        no NaN to notice. Failing loudly is the only honest option: returning NaN would instead make
        those hours vanish from a savings sum without saying so.
        """
        idx = pd.DatetimeIndex(index)
        tow = hour_of_week(idx)
        unseen = np.setdiff1d(np.unique(tow), self.bins)
        if len(unseen):
            n_rows = int(np.isin(tow, unseen).sum())
            raise ValueError(
                f"cannot project: {len(unseen)} hour-of-week bin(s) were never seen at fit "
                f"({n_rows} of {len(tow)} timestamps), e.g. {sorted(unseen)[:5]}. The baseline "
                "does not cover this period's schedule -- refit over a window that does, or "
                "restrict the projection to covered hours."
            )
        X = _build_design(
            tow, np.asarray(temp, dtype="float64"), self.bins, self.breakpoints, self.occ_bins
        )
        return X @ self.beta

    def covers(self, index) -> bool:
        """Whether every hour-of-week bin in ``index`` was present at fit (see :meth:`predict`)."""
        return not len(np.setdiff1d(np.unique(hour_of_week(pd.DatetimeIndex(index))), self.bins))


def fit_towt(
    energy: pd.Series, temp: pd.Series, *, n_temp_segments: int = 6, occ_split: bool = True
) -> TOWTModel:
    """Fit a TOWT model to an hourly ``energy`` series against ``temp``.

    Both series are aligned on their shared timestamps. ``n_temp_segments`` sets
    the piecewise-linear temperature resolution; ``occ_split`` fits the
    temperature response separately for occupied vs unoccupied hour-of-week bins
    (occupancy inferred from each bin's mean load).
    """
    df = pd.DataFrame({"e": energy, "t": temp}).dropna()
    if len(df) < 50:
        raise ValueError("need >= 50 aligned observations to fit TOWT")
    idx = pd.DatetimeIndex(df.index)
    tow = hour_of_week(idx)
    y = df["e"].to_numpy(dtype="float64")
    t = df["t"].to_numpy(dtype="float64")

    bins = np.unique(tow)
    breakpoints = _temp_breakpoints(t, n_temp_segments)

    occ_bins = None
    if occ_split:
        # classify a TOW bin as occupied if its mean load is above the median of
        # bin-mean loads -- a load-based occupancy proxy (per the TOWT method).
        means = pd.Series(y).groupby(tow).mean()
        cutoff = float(means.median())
        occ_bins = frozenset(int(b) for b, m in means.items() if m > cutoff)

    X = _build_design(tow, t, bins, breakpoints, occ_bins)
    beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    return TOWTModel(
        bins=bins,
        breakpoints=breakpoints,
        occ_bins=occ_bins,
        beta=beta,
        n_params=int(rank),
        split=occ_split,
    )


class TOWTAtIndex:
    """A TOWT model bound to one period's timestamps, exposing the one-argument ``predict(temp)``.

    Every savings consumer in :mod:`camber.mandv` calls ``model.predict(T)`` with a single array and
    coerces it first with ``np.asarray(..., dtype=float)`` -- so a DatetimeIndex cannot be smuggled
    through that argument, and :class:`TOWTModel`, which needs the timestamps to compute
    hour-of-week, cannot be passed directly. Binding the index into a wrapper lets a TOWT
    baseline flow through :func:`camber.mandv.stats.avoided_energy_savings`,
    :func:`camber.mandv.nonroutine.residual_outliers` and the savings chart unchanged.

    ``temp`` must align with ``index`` positionally, which holds for the consumers above because
    they mask non-finite values *after* calling ``predict``.
    """

    def __init__(self, model: TOWTModel, index):
        self.model = model
        self.index = pd.DatetimeIndex(index)

    def predict(self, temp) -> np.ndarray:
        """Predict energy at the bound timestamps for ``temp`` (same length and order)."""
        temp = np.asarray(temp, dtype="float64")
        if len(temp) != len(self.index):
            raise ValueError(
                f"temp has {len(temp)} values but the bound index has {len(self.index)}; "
                "TOWTAtIndex requires them to align positionally"
            )
        return self.model.predict(self.index, temp)
