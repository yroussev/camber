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

**Name: "TOWT (CAMBER variant)"** (0.105, #163; :mod:`camber.mandv.names`). It differs from the
reference in ways that change the numbers: hour-of-week bins (168), ``n_temp_segments``
quantile-spaced temperature knots rather than fixed ones, occupancy from each bin's mean load
against the median of bin means, one pooled model over the baseline and no time-weighting of
observations. Report it as a TOWT variant with those differences, not as LBNL's TOWT.

Opt-in options (0.105; defaults unchanged): ``fit_towt(knots=, weights=)`` fixes the temperature
knots and fits by weighted least squares, and :func:`towt_lbnl_config` gives the LBNL RMV2.0
knots, knot pruning, clipping and recency-weight timescale (#162);
``TOWTModel.predict(unseen="fallback")`` predicts hour-of-week bins the fit never saw from
neighbouring bins (#154) and ``clip_negative=True`` sets negative predictions to zero (#155).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


def _temp_components(
    temp: np.ndarray, breakpoints: np.ndarray, open_ends: bool = False
) -> np.ndarray:
    """Continuous piecewise-linear basis: column j has slope 1 within segment j.

    Sum_j beta_j * comp_j(T) is a continuous piecewise-linear function of T whose
    slope in segment j is beta_j. A missing temperature gives a row of NaN (0.105, #159; it was
    zeros, which made the prediction the time-of-week level alone).

    ``open_ends`` (0.105, #162; fixed ``knots``): the outer two segments are unbounded, so the
    first and last slopes continue beyond the fitted range, as in Mathieu et al. (2011) section
    4.2 and LBNL RMV2.0's ``piecewiseVariables``: ``comp_0 = min(T, k_1)``, the middle columns as
    above, and ``comp_last = max(T - k_last, 0)``, with ``k`` the interior breakpoints (the knots).
    Without it (quantile breakpoints) the response is held flat beyond the fitted range.
    """
    n_seg = len(breakpoints) - 1
    out = np.zeros((len(temp), n_seg), dtype="float64")
    t = np.asarray(temp, dtype="float64")
    for j in range(n_seg):
        lo, hi = breakpoints[j], breakpoints[j + 1]
        if open_ends and j == 0:
            out[:, j] = np.minimum(t, hi)
        elif open_ends and j == n_seg - 1:
            out[:, j] = np.maximum(t - lo, 0.0)
        else:
            out[:, j] = np.clip(t - lo, 0.0, hi - lo)
    return out


def _build_design(
    tow: np.ndarray,
    temp: np.ndarray,
    bins: np.ndarray,
    breakpoints: np.ndarray,
    occ_bins: frozenset | None,
    open_ends: bool = False,
) -> np.ndarray:
    """Assemble [TOW one-hot | temperature components] for the given layout."""
    # time-of-week one-hot, columns in `bins` order
    onehot = (tow[:, None] == bins[None, :]).astype("float64")
    tcomp = _temp_components(temp, breakpoints, open_ends)
    if occ_bins is None:
        return np.hstack([onehot, tcomp])
    # occupied/unoccupied split: route temp components by each row's bin mode
    occ = np.array([b in occ_bins for b in tow], dtype=bool)
    occ_block = tcomp * occ[:, None]
    unocc_block = tcomp * (~occ)[:, None]
    return np.hstack([onehot, occ_block, unocc_block])


#: ``unseen=`` values accepted by :meth:`TOWTModel.predict` (0.105, #154)
UNSEEN_MODES = ("raise", "fallback")


def _check_unseen(unseen: str) -> str:
    if unseen not in UNSEEN_MODES:
        raise ValueError(f"unseen must be one of {UNSEEN_MODES}, got {unseen!r}")
    return unseen


def _weekend(day: int) -> bool:
    return day >= 5  # Saturday (5) and Sunday (6); hour_of_week counts from Monday


@dataclass
class TOWTModel:
    """A fitted Time-of-Week & Temperature model."""

    bins: np.ndarray  # TOW bin ids present at fit (one-hot column order)
    breakpoints: np.ndarray  # temperature segment breakpoints
    occ_bins: frozenset | None  # occupied TOW bins (None if no occ/unocc split)
    beta: np.ndarray  # OLS coefficients
    n_params: int  # effective parameters (design rank), for stats dof
    split: bool
    # the one private fit-time record (mode x temperature cell counts; camber.mandv.coverage)
    _fit_record: object = field(default=None, repr=False, compare=False)
    # 0.105 (#159): temperatures outside the plausible range set to missing before the fit
    n_temp_screened: int = 0
    # 0.105 (#162): fixed knots -- the interior breakpoints are the knots and the outer two
    # temperature segments are unbounded (see _temp_components)
    open_ends: bool = False
    # 0.105 (#162): fitted by weighted least squares (fit_towt(weights=...))
    weighted: bool = False
    # 0.105 (#168): the fit's temp_bounds (None: the default range), applied again at predict
    temp_bounds: tuple | None = None

    def as_dict(self) -> dict:
        """A JSON-safe dict from which :meth:`from_dict` rebuilds an identical model."""
        from .coverage import _FitRecord

        rec = self._fit_record
        d = {
            "type": "TOWTModel",
            "bins": [int(b) for b in self.bins],
            "breakpoints": [float(b) for b in self.breakpoints],
            "occ_bins": None if self.occ_bins is None else sorted(int(b) for b in self.occ_bins),
            "beta": [float(b) for b in self.beta],
            "n_params": int(self.n_params),
            "split": bool(self.split),
            "fit_record": rec.as_dict() if isinstance(rec, _FitRecord) else None,
        }
        if self.n_temp_screened:  # 0.105 (#159): only when the screen removed something
            d["n_temp_screened"] = int(self.n_temp_screened)
        # 0.105 (#162): only when set, so a default model's dict is unchanged
        if self.open_ends:
            d["open_ends"] = True
        if self.weighted:
            d["weighted"] = True
        if self.temp_bounds is not None:  # 0.105 (#168): only when not the default range
            from ._temperature import bounds_to_json

            d["temp_bounds"] = bounds_to_json(self.temp_bounds)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TOWTModel:
        """Rebuild a model written by :meth:`as_dict` (a dict from before 0.105 loads too)."""
        from ._temperature import bounds_from_json as _bounds_from_json
        from .coverage import _FitRecord

        occ = d.get("occ_bins")
        return cls(
            bins=np.asarray(d["bins"], dtype=int),
            breakpoints=np.asarray(d["breakpoints"], dtype=float),
            occ_bins=None if occ is None else frozenset(int(b) for b in occ),
            beta=np.asarray(d["beta"], dtype=float),
            n_params=int(d["n_params"]),
            split=bool(d["split"]),
            _fit_record=_FitRecord.from_dict(d.get("fit_record")),
            n_temp_screened=int(d.get("n_temp_screened", 0)),
            open_ends=bool(d.get("open_ends", False)),
            weighted=bool(d.get("weighted", False)),
            temp_bounds=_bounds_from_json(d.get("temp_bounds")),
        )

    def coverage(self, index, temp, *, projected=None, policy=None):
        """How well the baseline's (mode x temperature cell) support covers these hours (see
        :func:`camber.mandv.coverage.towt_coverage`)."""
        from .coverage import towt_coverage

        return towt_coverage(self, index, temp, projected=projected, policy=policy)

    def _rows(self, tow: np.ndarray, temp: np.ndarray) -> np.ndarray:
        return _build_design(tow, temp, self.bins, self.breakpoints, self.occ_bins, self.open_ends)

    def _fallback_plan(self, unseen_bins) -> dict:
        """Per unseen hour-of-week bin, ``("bin", donor)``, ``("mean", donors)`` or
        ``("none", ())`` -- the rule documented in :meth:`predict`."""
        bins = [int(b) for b in self.bins]
        occ = self.occ_bins if self.occ_bins is not None else frozenset()
        plan: dict = {}
        for u in (int(b) for b in unseen_bins):
            day, hour = divmod(u, 24)
            same_hour = [b for b in bins if b % 24 == hour]
            if not same_hour:
                plan[u] = ("none", ())
                continue
            peers = [b for b in same_hour if _weekend(b // 24) == _weekend(day)]
            donors: list = []
            if peers and self.occ_bins is None:
                donors = peers
            elif peers:
                n_occ = sum(b in occ for b in peers)
                if 2 * n_occ != len(peers):  # a majority class (a tie leaves it undetermined)
                    cls = 2 * n_occ > len(peers)
                    donors = [b for b in peers if (b in occ) == cls]
            if not donors:
                plan[u] = ("mean", tuple(same_hour))
                continue

            def distance(b, day=day):
                back, fwd = (day - b // 24) % 7, (b // 24 - day) % 7
                return (min(back, fwd), 0 if back <= fwd else 1)  # a tie takes the earlier day

            plan[u] = ("bin", min(donors, key=distance))
        return plan

    def _design_at(self, tow: np.ndarray, temp: np.ndarray, unseen: str | None):
        """``(X, counts)``: design rows at hour-of-week ``tow`` and ``temp`` under ``unseen``.

        ``unseen=None`` builds the raw design (an unseen bin gives an all-zero one-hot row), as
        :func:`camber.mandv._design.design_rows` always did for a bare model.
        """
        counts = {"n_unseen_fallback": 0, "n_unseen_hour_mean": 0, "n_unseen_unfilled": 0}
        missing = np.setdiff1d(np.unique(tow), self.bins)
        if unseen is None or not len(missing):
            return self._rows(tow, temp), counts
        if _check_unseen(unseen) == "raise":
            n_rows = int(np.isin(tow, missing).sum())
            eg = [int(b) for b in sorted(missing)[:5]]  # plain ints, not np.int32(9) (#173)
            raise ValueError(
                f"cannot project: {len(missing)} hour-of-week bin(s) were never seen at fit "
                f"({n_rows} of {len(tow)} timestamps), e.g. {eg}. The baseline "
                "does not cover this period's schedule -- refit over a window that does, or "
                "restrict the projection to covered hours (or predict with unseen='fallback')."
            )
        plan = self._fallback_plan(missing)
        tow_eff = np.array(tow, copy=True)
        for u, (how, donor) in plan.items():
            if how == "bin":
                tow_eff[tow == u] = donor
        X = self._rows(tow_eff, temp)
        for u, (how, donors) in plan.items():
            sel = tow == u
            k = int(sel.sum())
            if how == "mean":
                X[sel] = np.mean([self._rows(np.full(k, b), temp[sel]) for b in donors], axis=0)
                counts["n_unseen_hour_mean"] += k
            if how == "none":
                X[sel] = np.nan
                counts["n_unseen_unfilled"] += k
            else:
                counts["n_unseen_fallback"] += k
        return X, counts

    def predict(
        self,
        index,
        temp,
        *,
        unseen: str = "raise",
        clip_negative: bool = False,
        return_counts: bool = False,
    ):
        """Predict energy for the given timestamps and temperatures.

        **Unseen hour-of-week bins.** By default (``unseen="raise"``) this raises ``ValueError`` if
        any timestamp falls in an hour-of-week bin the fit never saw. The one-hot design has a
        column per *observed* bin, so an unseen bin would otherwise produce an all-zero row and
        the prediction would silently collapse to the temperature term alone -- measurably, a
        weekday-only fit projected onto a weekend predicts **-1.2** where the truth is **40**. A
        negative baseline reads downstream as a large negative saving, with no error and no NaN to
        notice. Failing loudly is the honest default: returning NaN would instead make those
        hours vanish from a savings sum without saying so.

        ``unseen="fallback"`` (0.105, #154; opt-in) predicts an unseen bin from bins the fit did
        see, in two documented steps:

        1. **Nearest day, same hour, same occupancy class.** The unseen bin's occupancy class is
           the majority class of the seen bins at the same hour of day on the same day type
           (Monday to Friday, or Saturday and Sunday). The row is predicted as the seen bin of
           that hour, class and day type on the nearest day of the week (counted around the
           week; a tie takes the earlier day): its time-of-week level and its class's
           temperature response. Without an occupancy split every bin is one class.
        2. **Hour-of-day mean.** When step 1 has no donor (no seen bin at that hour on that day
           type, or the classes tie), the row is the mean of the predictions of every seen bin at
           the same hour of day, each with its own class's temperature response.

        A bin with no seen bin at its hour of day at all predicts NaN. Neither step is a fitted
        level, and the weekday-only example above still predicts weekend hours from weekday bins
        (step 2) -- far better than -1.2, but not a measured weekend. The coverage check
        (:func:`camber.mandv.coverage.towt_coverage`) grades every such row unsupported.

        **Negative predictions.** ``clip_negative=True`` (0.105, #155; opt-in) sets negative
        predictions to zero, as LBNL's RMV2.0 does for its prediction period. Clipping is applied
        after ``X @ beta``; the projection variance (:mod:`camber.mandv._design`, the exact
        kernel) is computed on the unclipped linear projection.

        ``return_counts=True`` (0.105) returns ``(prediction, counts)``, the counts being
        ``n_unseen_fallback`` (rows filled by either step), ``n_unseen_hour_mean`` (of those, by
        step 2), ``n_unseen_unfilled`` (NaN, no seen bin at that hour), ``n_clipped`` and
        ``n_no_temperature`` -- as :meth:`camber.mandv.caltrack_hourly.CalTRACKHourlyModel.predict`.

        A row whose temperature is missing (NaN, or screened out as implausible) predicts NaN
        (0.105, #159). It used to predict the time-of-week level alone, a number with no weather
        in it; :func:`camber.mandv.stats.avoided_energy_savings` now excludes such rows from its
        sums and counts them in its caveats. The screen is applied here too, with the bounds the
        model was fitted with (0.105, #168): a temperature outside them predicts NaN, counted as
        ``n_temp_screened`` (``n_no_temperature`` counts the rows given no temperature at all).
        """
        from ._temperature import screen_temperature

        idx = pd.DatetimeIndex(index)
        tow = hour_of_week(idx)
        t_in = np.asarray(temp, dtype="float64")
        t, n_screened = screen_temperature(t_in, self.temp_bounds)
        X, counts = self._design_at(tow, t, _check_unseen(unseen))
        out = X @ self.beta
        n_clipped = 0
        if clip_negative:
            neg = out < 0  # NaN compares False: a missing prediction stays missing
            n_clipped = int(neg.sum())
            out = np.where(neg, 0.0, out)
        if not return_counts:
            return out
        counts["n_clipped"] = n_clipped
        counts["n_no_temperature"] = int(np.isnan(t_in).sum())
        counts["n_temp_screened"] = n_screened
        return out, counts

    def covers(self, index) -> bool:
        """Whether every hour-of-week bin in ``index`` was present at fit (see :meth:`predict`)."""
        return not len(np.setdiff1d(np.unique(hour_of_week(pd.DatetimeIndex(index))), self.bins))


def _fixed_knots(knots) -> np.ndarray:
    k = np.asarray(knots, dtype="float64").ravel()
    if not len(k) or not np.all(np.isfinite(k)) or np.any(np.diff(k) <= 0):
        raise ValueError("knots must be one or more finite temperatures in increasing order")
    return k


def _prune_knots(knots: np.ndarray, t: np.ndarray, min_obs: int) -> np.ndarray:
    """Drop outer knots with fewer than ``min_obs`` temperatures beyond them, keeping one.

    LBNL RMV2.0 ``fitLBNLregress`` (``towt_baseline.R``): while fewer than 20 observations lie
    above the highest knot, drop it; then the same below the lowest; at least one knot is kept.
    """
    k = list(knots)
    if min_obs <= 0:
        return np.asarray(k, dtype="float64")
    while len(k) > 1 and int((t > k[-1]).sum()) < min_obs:
        k.pop()
    while len(k) > 1 and int((t < k[0]).sum()) < min_obs:
        k.pop(0)
    return np.asarray(k, dtype="float64")


def fit_towt(
    energy: pd.Series,
    temp: pd.Series,
    *,
    n_temp_segments: int = 6,
    occ_split: bool = True,
    temp_bounds: tuple | None = None,
    knots=None,
    knot_min_obs: int = 0,
    weights=None,
) -> TOWTModel:
    """Fit a TOWT model to an hourly ``energy`` series against ``temp``.

    Both series are aligned on their shared timestamps. ``n_temp_segments`` sets
    the piecewise-linear temperature resolution; ``occ_split`` fits the
    temperature response separately for occupied vs unoccupied hour-of-week bins
    (occupancy inferred from each bin's mean load).

    **Temperature screen** (0.105, #159). Temperatures outside ``temp_bounds`` (default the
    sensor-health outdoor-air range, -40 to 140 degF; see
    :func:`camber.mandv._temperature.screen_temperature`) are treated as missing, so a sentinel
    placeholder cannot become the first temperature breakpoint; the model's ``n_temp_screened``
    counts them.

    **Fixed knots** (0.105, #162; opt-in). ``knots`` (increasing temperatures, in the units of
    ``temp``) replaces the ``n_temp_segments`` quantile-spaced breakpoints: ``k`` knots give
    ``k + 1`` temperature segments whose outer two are unbounded, so the first and last slopes
    continue beyond the fitted range (Mathieu et al. 2011, section 4.2; LBNL RMV2.0
    ``piecewiseVariables``). ``knot_min_obs`` (default 0, keep every knot) drops outer knots with
    fewer than that many temperatures beyond them, keeping at least one, as RMV2.0 does with 20.
    The model's ``breakpoints`` are then ``[min, *knots kept, max]`` of the fitted temperatures
    and its ``open_ends`` is true. :func:`towt_lbnl_config` gives the LBNL reference knots.

    **Weights** (0.105, #162; opt-in). ``weights`` (positive; a series aligned on timestamps or
    an array aligned with ``energy``) fits by weighted least squares (``sqrt(w)`` scales each
    row of the design and of the energy in ``lstsq``). They are normalised to mean 1; equal
    weights take the unweighted path exactly. Occupancy is still classified from unweighted bin
    means (RMV2.0 computes but does not use a weight screen for it). The fit record keeps
    ``pinv(X'WX)`` and the weighted residual variance, so the exact kernel treats the weights as
    variance weights and a reporting hour as one of average weight -- an approximation for
    recency weights such as :func:`lbnl_time_weights`.
    """
    from ._temperature import screen_temperature

    temp, n_screened = screen_temperature(temp, temp_bounds)
    w_norm = None
    if weights is None:
        df = pd.DataFrame({"e": energy, "t": temp}).dropna()
    else:
        from .models import fit_weights

        if isinstance(weights, pd.Series):
            ws = weights
        else:
            wa = np.asarray(weights, dtype="float64").ravel()
            if len(wa) != len(energy):
                raise ValueError(f"weights has {len(wa)} values but energy has {len(energy)}")
            ws = pd.Series(wa, index=energy.index)
        df = pd.DataFrame({"e": energy, "t": temp, "w": ws}).dropna(subset=["e", "t"])
        w_norm, _ = fit_weights(df["w"].to_numpy(dtype="float64"))
    if len(df) < 50:
        raise ValueError("need >= 50 aligned observations to fit TOWT")
    idx = pd.DatetimeIndex(df.index)
    tow = hour_of_week(idx)
    y = df["e"].to_numpy(dtype="float64")
    t = df["t"].to_numpy(dtype="float64")

    bins = np.unique(tow)
    open_ends = knots is not None
    if open_ends:
        k = _prune_knots(_fixed_knots(knots), t, int(knot_min_obs))
        breakpoints = np.concatenate([[min(t.min(), k[0])], k, [max(t.max(), k[-1])]])
    else:
        breakpoints = _temp_breakpoints(t, n_temp_segments)

    occ_bins = None
    if occ_split:
        # classify a TOW bin as occupied if its mean load is above the median of
        # bin-mean loads -- a load-based occupancy proxy (per the TOWT method).
        means = pd.Series(y).groupby(tow).mean()
        cutoff = float(means.median())
        occ_bins = frozenset(int(b) for b, m in means.items() if m > cutoff)

    X = _build_design(tow, t, bins, breakpoints, occ_bins, open_ends)
    if w_norm is None:
        Xw = X
        beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    else:
        sw = np.sqrt(w_norm)
        Xw = X * sw[:, None]
        beta, _res, rank, _sv = np.linalg.lstsq(Xw, y * sw, rcond=None)
    from .coverage import _safe, _towt_fit_record
    from .models import _rho_of

    resid = y - X @ beta
    rss = float(resid @ resid) if w_norm is None else float((w_norm * resid) @ resid)
    n, p = len(y), int(rank)
    return TOWTModel(
        bins=bins,
        breakpoints=breakpoints,
        occ_bins=occ_bins,
        beta=beta,
        n_params=p,
        split=occ_split,
        _fit_record=_safe(
            _towt_fit_record,
            tow,
            t,
            breakpoints,
            occ_bins,
            X=Xw,
            s2=rss / (n - p) if n > p else None,
            n=n,
            p=p,
            rho=_safe(_rho_of, resid, idx),
        ),
        n_temp_screened=n_screened,
        open_ends=open_ends,
        weighted=w_norm is not None,
        temp_bounds=None if temp_bounds is None else (float(temp_bounds[0]), float(temp_bounds[1])),
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

    ``unseen`` and ``clip_negative`` (0.105, #154 / #155) are passed to every
    :meth:`TOWTModel.predict` call, and to :func:`camber.mandv._design.design_rows`; after each
    ``predict`` the counts it returned are in ``predict_counts``, which
    :func:`~camber.mandv.stats.avoided_energy_savings` turns into caveats.
    """

    def __init__(self, model: TOWTModel, index, *, unseen: str = "raise", clip_negative=False):
        self.model = model
        self.index = pd.DatetimeIndex(index)
        self.unseen = _check_unseen(unseen)
        self.clip_negative = bool(clip_negative)
        self.predict_counts: dict | None = None

    def predict(self, temp) -> np.ndarray:
        """Predict energy at the bound timestamps for ``temp`` (same length and order)."""
        temp = np.asarray(temp, dtype="float64")
        if len(temp) != len(self.index):
            raise ValueError(
                f"temp has {len(temp)} values but the bound index has {len(self.index)}; "
                "TOWTAtIndex requires them to align positionally"
            )
        out, counts = self.model.predict(
            self.index,
            temp,
            unseen=self.unseen,
            clip_negative=self.clip_negative,
            return_counts=True,
        )
        self.predict_counts = counts
        return out

    def coverage(self, temp, *, projected=None, policy=None):
        """Coverage of the bound hours at ``temp`` (:func:`camber.mandv.coverage.towt_coverage`)."""
        from .coverage import _predict_or_none, towt_coverage

        if projected is None:
            projected = _predict_or_none(self, temp)
        return towt_coverage(self.model, self.index, temp, projected=projected, policy=policy)


# --------------------------------------------------------------------------- LBNL reference


#: The LBNL reference temperature knots, degF: RMV2.0 ``makeBaseline`` (``towt_baseline.R``)
#: sets ``tempKnots = (c(40, 55, 65, 80, 90) - 32) * 5/9``, i.e. these in degC.
LBNL_KNOTS_F = (40.0, 55.0, 65.0, 80.0, 90.0)


def lbnl_time_weights(index, center, *, timescale_days: float = 14.0) -> np.ndarray:
    """RMV2.0's recency weights about ``center``: ``s^2 / (s^2 + dt^2)``, ``dt`` in days.

    LBNL RMV2.0 ``makeBaseline`` (``towt_baseline.R``) weights each training observation
    ``timescaleDays^2 / (timescaleDays^2 + tDiff^2)``, ``tDiff`` the time from the model run's
    centre in days, with ``timescaleDays = 14`` by default. Pass the result to
    :func:`fit_towt` as ``weights`` to fit one of RMV2.0's local models.
    """
    idx = pd.DatetimeIndex(index)
    dt = (pd.Timestamp(center) - idx).total_seconds().to_numpy(dtype="float64") / 86400.0
    s2 = float(timescale_days) ** 2
    return s2 / (s2 + dt**2)


@dataclass(frozen=True)
class TOWTReferenceConfig:
    """Settings that configure :func:`fit_towt` / :meth:`TOWTModel.predict` like a reference
    TOWT (see :func:`towt_lbnl_config`)."""

    knots: tuple  # temperature knots in ``units``
    units: str  # "degF" | "degC"
    knot_min_obs: int  # outer knots need this many temperatures beyond them
    occ_split: bool  # separate occupied / unoccupied temperature responses
    clip_negative: bool  # predictions below zero are set to zero
    timescale_days: float  # the recency-weight timescale (lbnl_time_weights)
    sources: tuple  # camber.references ids

    def fit_kwargs(self) -> dict:
        """Keyword arguments for :func:`fit_towt`."""
        return {"knots": self.knots, "knot_min_obs": self.knot_min_obs, "occ_split": self.occ_split}

    def predict_kwargs(self) -> dict:
        """Keyword arguments for :meth:`TOWTModel.predict` (or :class:`TOWTAtIndex`)."""
        return {"clip_negative": self.clip_negative}

    def time_weights(self, index, center) -> np.ndarray:
        """:func:`lbnl_time_weights` at this config's timescale."""
        return lbnl_time_weights(index, center, timescale_days=self.timescale_days)


def towt_lbnl_config(units: str = "degF") -> TOWTReferenceConfig:
    """The LBNL reference TOWT settings, as implemented in LBNL's RMV2.0 R package (0.105, #162).

    Verified against RMV2.0 1.1.0 (``R/towt_baseline.R``, GitHub ``LBNL-ETA/RMV2.0``) and
    Mathieu et al. (2011), LBNL-4944E, section 4.2:

    * knots at 40, 55, 65, 80 and 90 degF (``makeBaseline``), six temperature segments whose outer
      two are unbounded (``piecewiseVariables``; the paper's component-temperature algorithm);
    * an outer knot with fewer than 20 temperatures beyond it is dropped, keeping one
      (``fitLBNLregress``);
    * separate occupied and unoccupied regressions, each with the full piecewise temperature term
      (``fitLBNLregress``; the paper's unoccupied mode has a single slope);
    * negative predictions set to zero (``predVec[predVec < 0] = 0``);
    * recency weights ``s^2 / (s^2 + dt^2)`` with ``s = 14`` days (``makeBaseline``).

    Not reproduced here, so a fit with these settings is still a CAMBER TOWT and not RMV2.0's:
    RMV2.0's occupancy rule (an hour of the week is occupied when a load regression on outdoor
    temperature hinged at 50 and 65 degF under-predicts it more than 65% of the time; CAMBER
    compares bin means with their median), its ensemble of locally weighted fits (one fit
    centred every ~14 days, predictions averaged with the same weight function), its 15-minute
    time-of-week intervals (CAMBER bins by the hour) and its NaN for an unseen time of week.
    ``units`` is ``"degF"`` (CAMBER's temperature unit) or ``"degC"`` for the same knots in
    Celsius.
    """
    knots: tuple
    if units == "degF":
        knots = LBNL_KNOTS_F
    elif units == "degC":
        knots = tuple((k - 32.0) * 5.0 / 9.0 for k in LBNL_KNOTS_F)
    else:
        raise ValueError(f"units must be 'degF' or 'degC', got {units!r}")
    return TOWTReferenceConfig(
        knots=knots,
        units=units,
        knot_min_obs=20,
        occ_split=True,
        clip_negative=True,
        timescale_days=14.0,
        sources=("mathieu-2011-towt", "lbnl-rmv2"),
    )
