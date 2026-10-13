"""TOWT, LBNL RMV2.0 variant: RMV2.0's occupancy rule and weighted ensemble (opt-in, 0.105, #165).

A reimplementation of the Time-of-Week-and-Temperature baseline in LBNL's RMV2.0 R package
(version 1.1.0, ``R/towt_baseline.R`` at commit ``979a963``, GitHub ``LBNL-ETA/RMV2.0``), for a
like-for-like comparison with the LBNL reference. Default CAMBER TOWT
(:func:`camber.mandv.towt.fit_towt`) and :func:`~camber.mandv.towt.towt_lbnl_config` are
unchanged.

**Name: "TOWT, LBNL RMV2.0 variant"** (:mod:`camber.mandv.names`, code ``towt_rmv2``).

What RMV2.0 does, function by function (line numbers in ``R/towt_baseline.R`` at ``979a963``):

* **Time of week** (``fitLBNLregress``, lines 239-240). ``intervalOfWeek = 1 +
  floor((1440 * wday + 60 * hour + min) / intervalMinutes)``; ``towt_baseline`` defaults
  ``intervalMinutes = 15`` (line 48). On data stamped at one fixed minute of the hour (hourly
  meters) 15-minute and 60-minute intervals group the rows identically. Here the default is 60
  (hour-of-week, CAMBER's main case); pass ``interval_minutes=15`` for 15-minute data.
* **Occupancy** (``findOccUnocc``, lines 186-220; called at line 293). One ordinary least-squares
  regression of load on ``1``, ``min(T - 50, 0)`` and ``max(T - 65, 0)`` (``T`` in degF) over every
  training row with load. An interval of the week is occupied when its positive residuals number
  more than ``0.65`` times its rows. The occupancy split is used only when more than two intervals
  are occupied (line 298); otherwise every row is unoccupied and one model is fitted. The rule is
  unweighted and the same for every local fit: ``fitLBNLregress`` computes a weight screen
  ``okUseForOcc`` (lines 230-237) but never passes it on.
* **Temperature basis** (``piecewiseVariables``, lines 163-181; knots at line 456). Knots at 40,
  55, 65, 80 and 90 degF, the outer segments unbounded; an outer knot with fewer than 20
  temperatures beyond it is dropped, keeping one (lines 306-343). CAMBER's
  :func:`~camber.mandv.towt._build_design` with ``open_ends=True`` spans the same columns, so the
  fit is the same (RMV2.0 works in degC; the conversion is affine and absorbed by the
  time-of-week levels).
* **Local fits** (``fitLBNLregress``, lines 355-402). Separate weighted least-squares fits for
  occupied and unoccupied rows, each with a level per time of week and its own temperature
  slopes. CAMBER fits both in one block-separable design, which gives the same coefficients.
* **Ensemble** (``makeBaseline``, lines 420-485). ``nsegments = max(1, ceiling(deltaT /
  timescaleDays))``, ``deltaT`` the training span in days; the centres are the training rows at
  positions ``floor(sort(npoints - segmentwidth * (0:nsegments)) + 0.001)``, ``segmentwidth =
  (npoints - 1) / nsegments`` (lines 427-433): ``nsegments + 1`` rows evenly spaced *by row
  count* from the first training row to the last, all within the training period. Local fit
  ``k`` weights training row ``i`` by ``s^2 / (s^2 + (c_k - t_i)^2)`` (days, ``s = timescaleDays
  = 14``, line 451).
* **Prediction** (lines 404-406, 454, 472). Each local prediction is set to zero where negative,
  then the predictions are averaged with weights ``s^2 / (s^2 + (c_k - t)^2)`` about the
  prediction time ``t``: ``sum_k w_k p_k / sum_k w_k``. A time of week that was never in the
  training data is ``NA`` in every local fit (lines 386 and 399), so its prediction is ``NA``.
  The training-period fit (line 473) is the same average without the zero clip
  (``predict(..., clip_negative=False)`` on the training timestamps).

Not reproduced: ``towt_baseline``'s data cleaning (``clean_eload`` drops negative loads and
quantile outliers, ``clean_Temp`` drops rows outside -80 to 130 degF; ``preprocessing.R``) --
clean the data first, or pass ``temp_bounds=(-80, 130)``; and its final ``approx(...,
method="constant")`` (lines 119-124), which fills an ``NA`` prediction with the previous
timestamp's (offered as ``unseen="previous"``). RMV2.0 never stops pruning when the last knot has
too few temperatures below it (``ntempknots`` passes 1 without equalling it, lines 331-343);
CAMBER keeps that knot. In a rank-deficient local fit R's pivoted QR and numpy's minimum-norm
least squares can give different predictions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .towt import (
    LBNL_KNOTS_F,
    TOWTModel,
    _build_design,
    _fixed_knots,
    _prune_knots,
    lbnl_time_weights,
)

__all__ = [
    "RMV2_UNSEEN_MODES",
    "TOWTRMV2Model",
    "fit_towt_rmv2",
    "interval_of_week",
    "rmv2_centres",
    "rmv2_occupied_intervals",
]

#: ``unseen=`` values accepted by :meth:`TOWTRMV2Model.predict`
RMV2_UNSEEN_MODES = ("nan", "previous", "raise", "fallback")

# findOccUnocc (towt_baseline.R lines 199-216): hinges in degF and the positive-residual share
_OCC_HINGES_F = (50.0, 65.0)
_OCC_SHARE = 0.65
_MIN_OBS = 50


def interval_of_week(index, interval_minutes: int = 60) -> np.ndarray:
    """Interval-of-week ids, ``(1440 * day + 60 * hour + minute) // interval_minutes``, Monday 00:00
    = 0. With ``interval_minutes=60`` this is :func:`camber.mandv.towt.hour_of_week`.

    RMV2.0 counts from Sunday and from 1 (``fitLBNLregress``, lines 239-240); with an interval that
    divides a day that relabels the same intervals, so every fit and prediction is the same.
    """
    m = _check_interval(interval_minutes)
    idx = pd.DatetimeIndex(index)
    mow = idx.dayofweek.to_numpy() * 1440 + idx.hour.to_numpy() * 60 + idx.minute.to_numpy()
    return mow // m


def _check_interval(interval_minutes) -> int:
    m = int(interval_minutes)
    if m != interval_minutes or m <= 0 or 1440 % m:
        raise ValueError(f"interval_minutes must divide 1440 (a day), got {interval_minutes!r}")
    return m


def rmv2_occupied_intervals(tow, load, temp_f) -> frozenset:
    """The intervals of the week RMV2.0's ``findOccUnocc`` calls occupied (lines 186-220).

    Regress ``load`` on ``1``, ``min(T - 50, 0)`` and ``max(T - 65, 0)`` (``temp_f`` in degF) by
    ordinary least squares; an interval is occupied when more than 65 % of its rows have a positive
    residual (the regression under-predicts it). Rows with a missing load or temperature are left
    out. The caller applies RMV2.0's "more than two occupied intervals" rule.
    """
    tow = np.asarray(tow)
    y = np.asarray(load, dtype="float64")
    t = np.asarray(temp_f, dtype="float64")
    ok = np.isfinite(y) & np.isfinite(t)
    tow, y, t = tow[ok], y[ok], t[ok]
    lo, hi = _OCC_HINGES_F
    X = np.column_stack([np.ones(len(t)), np.minimum(t - lo, 0.0), np.maximum(t - hi, 0.0)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pos = (y - X @ beta) > 0
    occ = set()
    for b in np.unique(tow):
        sel = tow == b
        if int(pos[sel].sum()) > _OCC_SHARE * int(sel.sum()):
            occ.add(int(b))
    return frozenset(occ)


def rmv2_centres(index, timescale_days: float = 14.0) -> np.ndarray:
    """0-based positions in the (time-sorted) training ``index`` of RMV2.0's local-fit centres.

    ``makeBaseline`` (lines 425-433): ``nsegments = max(1, ceiling(deltaT / timescaleDays))``,
    ``segmentwidth = (npoints - 1) / nsegments`` and the 1-based positions
    ``floor(sort(npoints - segmentwidth * (0:nsegments)) + 0.001)`` -- ``nsegments + 1`` rows evenly
    spaced by row count, from the first row to the last. A repeated position is kept, as in R.
    """
    idx = pd.DatetimeIndex(index)
    n = len(idx)
    if n < 2:
        raise ValueError("need at least two training timestamps")
    delta_days = (idx.max() - idx.min()).total_seconds() / 86400.0
    nseg = max(1, math.ceil(delta_days / float(timescale_days)))
    width = (n - 1) / nseg
    pts = np.floor(np.sort(n - width * np.arange(nseg + 1, dtype="float64")) + 0.001)
    return pts.astype(int) - 1


def _units_to_f(t: np.ndarray, units: str) -> np.ndarray:
    return t if units == "degF" else t * 9.0 / 5.0 + 32.0


def _default_bounds(units: str):
    from ._temperature import oat_bounds

    lo, hi = oat_bounds()
    if units == "degF":
        return (lo, hi)
    return ((lo - 32.0) * 5.0 / 9.0, (hi - 32.0) * 5.0 / 9.0)


def _check_units(units: str) -> str:
    if units not in ("degF", "degC"):
        raise ValueError(f"units must be 'degF' or 'degC', got {units!r}")
    return units


@dataclass
class TOWTRMV2Model:
    """A fitted RMV2.0-variant TOWT: one design, one coefficient vector per local fit.

    ``betas`` has a row per centre (``centres``, the training timestamps the local fits are
    weighted about). ``occupied`` is every interval RMV2.0's rule calls occupied; ``occ_bins`` is
    the split actually fitted (``None`` when the rule found two or fewer occupied intervals, or
    every interval occupied, so one model was fitted).
    """

    bins: np.ndarray  # interval-of-week ids seen at fit (one-hot column order)
    breakpoints: np.ndarray  # [lowest, *knots kept, highest]; outer segments unbounded
    occ_bins: frozenset | None
    occupied: frozenset
    betas: np.ndarray  # (n_centres, n_columns)
    centres: pd.DatetimeIndex
    timescale_days: float = 14.0
    interval_minutes: int = 60
    units: str = "degF"
    temp_bounds: tuple | None = None
    n_obs: int = 0
    n_temp_screened: int = 0
    kind: str = field(default="towt_rmv2", init=False)

    # ---- serialisation ----------------------------------------------------------------------
    def as_dict(self) -> dict:
        """A JSON-safe dict from which :meth:`from_dict` rebuilds an identical model."""
        tb = self.temp_bounds
        return {
            "type": "TOWTRMV2Model",
            "kind": self.kind,
            "bins": [int(b) for b in self.bins],
            "breakpoints": [float(b) for b in self.breakpoints],
            "occ_bins": None if self.occ_bins is None else sorted(int(b) for b in self.occ_bins),
            "occupied": sorted(int(b) for b in self.occupied),
            "betas": [[float(v) for v in row] for row in self.betas],
            "centres": _centres_out(self.centres),
            "tz": None if self.centres.tz is None else str(self.centres.tz),
            "timescale_days": float(self.timescale_days),
            "interval_minutes": int(self.interval_minutes),
            "units": self.units,
            "temp_bounds": None if tb is None else [float(v) for v in tb],
            "n_obs": int(self.n_obs),
            "n_temp_screened": int(self.n_temp_screened),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TOWTRMV2Model:
        """Rebuild a model written by :meth:`as_dict`."""
        occ = d.get("occ_bins")
        tb = d.get("temp_bounds")
        return cls(
            bins=np.asarray(d["bins"], dtype=int),
            breakpoints=np.asarray(d["breakpoints"], dtype=float),
            occ_bins=None if occ is None else frozenset(int(b) for b in occ),
            occupied=frozenset(int(b) for b in d.get("occupied", ())),
            betas=np.asarray(d["betas"], dtype=float).reshape(len(d["centres"]), -1),
            centres=_centres_in(d["centres"], d.get("tz")),
            timescale_days=float(d.get("timescale_days", 14.0)),
            interval_minutes=int(d.get("interval_minutes", 60)),
            units=str(d.get("units", "degF")),
            temp_bounds=None if tb is None else (float(tb[0]), float(tb[1])),
            n_obs=int(d.get("n_obs", 0)),
            n_temp_screened=int(d.get("n_temp_screened", 0)),
        )

    # ---- prediction -------------------------------------------------------------------------
    def weights(self, index) -> np.ndarray:
        """``(len(index), n_centres)``: RMV2.0's weight of each local fit at each timestamp,
        ``s^2 / (s^2 + (c_k - t)^2)`` in days (``makeBaseline``, line 454)."""
        idx = pd.DatetimeIndex(index)
        return np.column_stack(
            [lbnl_time_weights(idx, c, timescale_days=self.timescale_days) for c in self.centres]
        )

    def local_predictions(self, index, temp, *, clip_negative: bool = True) -> np.ndarray:
        """``(len(index), n_centres)``: each local fit's prediction (``NaN`` at an unseen time of
        week or a missing temperature), clipped at zero by default as in RMV2.0."""
        X, _unseen, _counts = self._design(index, temp, "nan")
        return self._local(X, clip_negative)[0]

    def _local(self, X: np.ndarray, clip_negative: bool):
        P = X @ self.betas.T
        if not clip_negative:
            return P, 0
        neg = P < 0  # NaN compares False
        return np.where(neg, 0.0, P), int(neg.any(axis=1).sum())

    def _design(self, index, temp, unseen: str):
        idx = pd.DatetimeIndex(index)
        tow = interval_of_week(idx, self.interval_minutes)
        t = np.asarray(temp, dtype="float64")
        if len(t) != len(idx):
            raise ValueError(f"temp has {len(t)} values but index has {len(idx)}")
        unseen_rows = ~np.isin(tow, self.bins)
        counts = {"n_unseen_fallback": 0, "n_unseen_hour_mean": 0}
        if unseen == "fallback" and unseen_rows.any():
            if self.interval_minutes != 60:
                raise ValueError(
                    "unseen='fallback' needs hour-of-week bins (interval_minutes=60); "
                    f"this model has {self.interval_minutes}-minute intervals"
                )
            proxy = TOWTModel(
                bins=self.bins,
                breakpoints=self.breakpoints,
                occ_bins=self.occ_bins,
                beta=self.betas[0],
                n_params=0,
                split=self.occ_bins is not None,
                open_ends=True,
            )
            X, c = proxy._design_at(tow, t, "fallback")
            counts["n_unseen_fallback"] = int(c["n_unseen_fallback"])
            counts["n_unseen_hour_mean"] = int(c["n_unseen_hour_mean"])
            return X, unseen_rows & ~np.isfinite(X).all(axis=1), counts
        if unseen == "raise" and unseen_rows.any():
            missing = sorted(int(b) for b in np.unique(tow[unseen_rows]))
            raise ValueError(
                f"cannot project: {len(missing)} interval(s) of the week were never seen at fit "
                f"({int(unseen_rows.sum())} of {len(tow)} timestamps), e.g. {missing[:5]}"
            )
        X = _build_design(tow, t, self.bins, self.breakpoints, self.occ_bins, open_ends=True)
        X[unseen_rows] = np.nan
        return X, unseen_rows, counts

    def predict(
        self,
        index,
        temp,
        *,
        unseen: str = "nan",
        clip_negative: bool = True,
        return_counts: bool = False,
    ):
        """RMV2.0's ensemble prediction: ``sum_k w_k p_k / sum_k w_k`` (``makeBaseline``, line 472).

        ``p_k`` is local fit ``k``'s prediction, set to zero where negative when ``clip_negative``
        (RMV2.0, line 406; ``clip_negative=False`` on the training timestamps gives RMV2.0's
        unclipped training fit, line 473), and ``w_k`` its weight at the timestamp (see
        :meth:`weights`). A missing or screened-out temperature predicts NaN.

        ``unseen`` -- a timestamp whose interval of the week was never in the training data:

        * ``"nan"`` (default): NaN, as ``makeBaseline`` returns;
        * ``"previous"``: the prediction at the latest earlier timestamp with one, as
          ``towt_baseline``'s ``approx(..., method="constant")`` fills it (lines 119-124); a row
          before the first or after the last predicted timestamp stays NaN;
        * ``"raise"``: ``ValueError``;
        * ``"fallback"`` (hour-of-week models only): the nearest-day, same-hour rule of
          :meth:`camber.mandv.towt.TOWTModel.predict`, applied to every local fit. Not RMV2.0.

        ``return_counts=True`` returns ``(prediction, counts)`` with ``n_unseen_fallback``,
        ``n_unseen_hour_mean``, ``n_unseen_previous``, ``n_unseen_unfilled`` (unseen rows left
        NaN), ``n_clipped`` (rows where any local prediction was clipped), ``n_no_temperature``
        and ``n_temp_screened``.
        """
        if unseen not in RMV2_UNSEEN_MODES:
            raise ValueError(f"unseen must be one of {RMV2_UNSEEN_MODES}, got {unseen!r}")
        from ._temperature import screen_temperature

        idx = pd.DatetimeIndex(index)
        bounds = self.temp_bounds if self.temp_bounds is not None else _default_bounds(self.units)
        t, n_screened = screen_temperature(np.asarray(temp, dtype="float64"), bounds)
        X, unseen_rows, counts = self._design(idx, t, unseen)
        P, n_clipped = self._local(X, clip_negative)
        W = self.weights(idx)
        out = (P * W).sum(axis=1) / W.sum(axis=1)  # NaN propagates, as R's sum does
        n_prev = 0
        if unseen == "previous" and unseen_rows.any():
            out, n_prev = _fill_previous(idx, out, unseen_rows)
        if not return_counts:
            return out
        counts["n_unseen_previous"] = n_prev
        counts["n_unseen_unfilled"] = int((unseen_rows & np.isnan(out)).sum())
        counts["n_clipped"] = n_clipped
        counts["n_no_temperature"] = int((~np.isfinite(t)).sum())
        counts["n_temp_screened"] = int(n_screened)
        return out, counts

    def covers(self, index) -> bool:
        """Whether every interval of the week in ``index`` was present at fit."""
        tow = interval_of_week(pd.DatetimeIndex(index), self.interval_minutes)
        return bool(np.isin(tow, self.bins).all())


def _centres_out(centres: pd.DatetimeIndex) -> list:
    """ISO timestamps, in UTC when tz-aware (one offset, whatever the zone's DST)."""
    c = centres if centres.tz is None else centres.tz_convert("UTC")
    return [ts.isoformat() for ts in c]


def _centres_in(values, tz) -> pd.DatetimeIndex:
    if tz is None:
        return pd.DatetimeIndex(pd.to_datetime(list(values)))
    return pd.DatetimeIndex(pd.to_datetime(list(values), utc=True)).tz_convert(tz)


def _fill_previous(idx: pd.DatetimeIndex, out: np.ndarray, unseen_rows: np.ndarray):
    """``approx(x, y, xout, method="constant")`` with R's default ``rule = 1``: an unseen row takes
    the value of the latest earlier-or-equal timestamp with a prediction, NaN outside their span."""
    ok = np.isfinite(out)
    if not ok.any():
        return out, 0
    tn = idx.asi8
    src_t = tn[ok]
    order = np.argsort(src_t, kind="stable")
    src_t, src_y = src_t[order], out[ok][order]
    res = out.copy()
    n = 0
    for i in np.flatnonzero(unseen_rows & ~ok):
        if tn[i] < src_t[0] or tn[i] > src_t[-1]:
            continue
        j = int(np.searchsorted(src_t, tn[i], side="right")) - 1
        res[i] = src_y[j]
        n += 1
    return res, n


def fit_towt_rmv2(
    energy: pd.Series,
    temp: pd.Series,
    *,
    units: str = "degF",
    interval_minutes: int = 60,
    timescale_days: float = 14.0,
    knots=None,
    knot_min_obs: int = 20,
    temp_bounds: tuple | None = None,
) -> TOWTRMV2Model:
    """Fit RMV2.0's TOWT (``makeBaseline``): its occupancy rule and its ensemble of local fits.

    ``energy`` and ``temp`` are aligned on their shared timestamps; rows missing either (or whose
    temperature lies outside ``temp_bounds``, default the sensor-health outdoor-air range, as in
    :func:`~camber.mandv.towt.fit_towt`) are dropped and the rest sorted by time. ``units`` is the
    temperature unit (``"degF"`` or ``"degC"``); the occupancy hinges are 50 and 65 degF either
    way. ``knots`` defaults to RMV2.0's 40, 55, 65, 80 and 90 degF (in ``units``), pruned with
    ``knot_min_obs = 20``. ``interval_minutes`` (default 60, hour-of-week) must divide a day;
    RMV2.0's default is 15, the same grouping on hourly data. ``timescale_days`` is RMV2.0's
    ``timescaleDays`` (14).

    One local fit per centre (:func:`rmv2_centres`), each weighted by
    :func:`~camber.mandv.towt.lbnl_time_weights` about its centre; occupancy
    (:func:`rmv2_occupied_intervals`) and knot pruning use every row unweighted, so all local fits
    share one design. A 365-day year of hourly data gives 28 local fits (27 segments of about 13.5
    days).
    """
    from ._temperature import screen_temperature

    _check_units(units)
    m = _check_interval(interval_minutes)
    if not timescale_days > 0:
        raise ValueError("timescale_days must be positive")
    bounds = temp_bounds if temp_bounds is not None else _default_bounds(units)
    temp, n_screened = screen_temperature(temp, bounds)
    df = pd.DataFrame({"e": energy, "t": temp}).dropna().sort_index(kind="stable")
    if len(df) < _MIN_OBS:
        raise ValueError(f"need >= {_MIN_OBS} aligned observations to fit TOWT")
    idx = pd.DatetimeIndex(df.index)
    y = df["e"].to_numpy(dtype="float64")
    t = df["t"].to_numpy(dtype="float64")
    tow = interval_of_week(idx, m)
    bins = np.unique(tow)

    occupied = rmv2_occupied_intervals(tow, y, _units_to_f(t, units))
    # line 298: the split only with more than two occupied intervals; all occupied is one model too
    split = len(occupied) > 2 and len(occupied) < len(bins)
    occ_bins = occupied if split else None

    if knots is None:
        knots = (
            LBNL_KNOTS_F if units == "degF" else tuple((k - 32.0) * 5.0 / 9.0 for k in LBNL_KNOTS_F)
        )
    k = _prune_knots(_fixed_knots(knots), t, int(knot_min_obs))
    breakpoints = np.concatenate([[min(t.min(), k[0])], k, [max(t.max(), k[-1])]])

    X = _build_design(tow, t, bins, breakpoints, occ_bins, open_ends=True)
    pos = rmv2_centres(idx, timescale_days)
    centres = idx[pos]
    betas = np.empty((len(pos), X.shape[1]), dtype="float64")
    for r, c in enumerate(centres):
        sw = np.sqrt(lbnl_time_weights(idx, c, timescale_days=timescale_days))
        betas[r], *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    return TOWTRMV2Model(
        bins=bins,
        breakpoints=breakpoints,
        occ_bins=occ_bins,
        occupied=occupied,
        betas=betas,
        centres=pd.DatetimeIndex(centres),
        timescale_days=float(timescale_days),
        interval_minutes=m,
        units=units,
        temp_bounds=None if temp_bounds is None else (float(bounds[0]), float(bounds[1])),
        n_obs=len(y),
        n_temp_screened=n_screened,
    )
