"""CalTRACK 2.0 Hourly method (0.105, #156).

A faithful implementation of the CalTRACK 2.0 hourly method, the "CalTRACK TOWT": one model per
calendar month, each fitted on its own month (weight 1.0) and the months either side (weight
0.5); occupancy per hour of the week from a preliminary model's residuals; six fixed temperature
bin endpoints, merged where a bin holds fewer than 20 hours; 168 hour-of-week levels plus the
temperature bins interacted with occupancy, no intercept; weighted least squares per segment;
and prediction by calendar month.

Every rule cites its id in the CAMBER spec (``CT-H-*``; constants in
:mod:`camber.mandv._caltrack` with their sources in the CalTRACK documents and eemeter 2.10.0).

**Defaults follow the published CalTRACK 2.0 documents.** ``eemeter_compat=True`` reproduces
eemeter 2.10 where the two disagree:

* CT-H-1.1: each month segment needs over 90 % of the hours of its month and both neighbours
  (eemeter 2.10 has this check as a stub, so compat fits every segment that has data);
* CT-H-1.2: temperature gaps of up to six hours are interpolated (not in eemeter 2.10).

Two eemeter 2.10 behaviours are **bugs and are never reproduced**, as eemeter 3.1 fixed them: an
hour of the week with no data in a segment predicts **NaN** (eemeter 2.10 gives it a coefficient
of about 0 from the pseudo-inverse and calls it occupied, so its prediction is the temperature
terms alone, CT-H-4.4, CT-H-6.3), and the count of such hours is reported. Under
``eemeter_compat`` the occupancy of such an hour reads as occupied (the eemeter lookup), but its
prediction is still NaN.

This is a different estimator from CAMBER's own TOWT (:mod:`camber.mandv.towt`, one pooled model
with quantile breakpoints and load-median occupancy, used by
:func:`camber.mandv.caltrack.towt_savings_hourly`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import _caltrack as K
from ._caltrack import (
    CalTRACKResult,
    ProjectionView,
    caltrack_metrics,
    floats_from,
    floats_or_none,
    wls,
)
from ._temperature import screen_temperature, screened_caveat

__all__ = [
    "SEGMENT_NAMES",
    "CalTRACKHourlyModel",
    "bin_features",
    "caltrack_hourly_savings",
    "fit_caltrack_hourly",
    "fit_temperature_bins",
    "hour_of_week",
    "hourly_frame",
    "occupancy_lookup",
    "segment_weights",
    "single_model_tests",
]

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# eemeter's segment names, centre month m -> "<m-1>-<m>-<m+1>-weighted" (CT-H-2.1)
SEGMENT_NAMES = tuple(
    f"{_MONTHS[(m - 2) % 12]}-{_MONTHS[m - 1]}-{_MONTHS[m % 12]}-weighted" for m in range(1, 13)
)


def hour_of_week(index) -> np.ndarray:
    """Hour of the week, 0..167, 0 = Monday 00:00-01:00 on the local clock (CT-H-3.1, K40)."""
    idx = pd.DatetimeIndex(index)
    return idx.dayofweek.to_numpy() * 24 + idx.hour.to_numpy()


def segment_weights(months, centre: int | None) -> np.ndarray:
    """Each row's weight in the segment centred on calendar month ``centre`` (CT-H-2.1): 1.0 in
    that month, 0.5 in the months either side (December and January are neighbours), 0 otherwise.
    ``centre=None`` is the single 365-day model (CT-H-2.3): every row 1.0."""
    m = np.asarray(months, dtype=int)
    if centre is None:
        return np.ones(len(m))
    prev, nxt = (centre - 2) % 12 + 1, centre % 12 + 1
    w = np.zeros(len(m))
    w[(m == prev) | (m == nxt)] = K.SEGMENT_NEIGHBOUR_WEIGHT
    w[m == centre] = K.SEGMENT_CENTRE_WEIGHT
    return w


# --------------------------------------------------------------------------- data preparation
def hourly_frame(
    energy: pd.Series,
    temp: pd.Series,
    *,
    fuel: str = "electricity",
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    eemeter_compat: bool = False,
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Hourly energy and temperature on one hourly grid (missing values kept as NaN).

    With ``timezone`` naive wall-clock data are placed on the site's real instants first (the
    repeated fall-back hour becomes two hours; :func:`camber.mandv.intervalfit.
    hourly_energy_vs_temp`'s convention) and the frame is indexed by tz-aware local time, so the
    hour of the week is the local clock's. Without one, a duplicate timestamp raises (possibly two
    meters, E18). Energy per interval is summed per hour (a rate is integrated); for electricity
    a value of exactly 0 is missing (CT-D-1.3). Temperatures are screened to the plausible range,
    sub-hourly ones spread to minutes and averaged per hour (CT-D-4.3), and gaps of up to six
    hours interpolated unless ``eemeter_compat`` (CT-H-1.2). ``attrs`` count what was masked.
    """
    K.check_fuel(fuel)
    from .caltrack_daily import _dedupe_or_raise
    from .intervalfit import _on_zone_clock, rate_to_energy
    from .resample import resample

    v, n_zero, n_neg = K.mask_meter_values(energy, fuel)
    t, n_screened = screen_temperature(
        pd.to_numeric(temp, errors="coerce").astype(float), temp_bounds
    )
    v = v.dropna()
    if timezone:
        v, _ = _on_zone_clock(v, timezone)
        t, _ = _on_zone_clock(t, timezone)
    else:
        v = _dedupe_or_raise(v.sort_index(), None, "the meter series")
        t = _dedupe_or_raise(t.sort_index(), None, "the temperature series")
    if rate_is_energy_rate:
        e = rate_to_energy(v, "1h")
    else:
        e = resample(v, "1h", method="time_weighted_sum")
    th, n_filled = K.hourly_temperature(t, interpolate=not eemeter_compat)
    f = pd.DataFrame({"energy": e, "temp": th})
    if len(f):
        tz = f.index.tz
        grid = pd.date_range(f.index.min(), f.index.max(), freq="h", tz=tz)
        f = f.reindex(grid)
    f.attrs.update(
        {
            "n_zero_masked": n_zero,
            "n_negative": n_neg,
            "n_temp_screened": n_screened,
            "n_temp_interpolated": n_filled,
        }
    )
    return f


# --------------------------------------------------------------------------- occupancy and bins
def occupancy_lookup(
    tow, residuals, *, threshold: float = K.OCCUPANCY_THRESHOLD, eemeter_compat: bool = False
) -> np.ndarray:
    """Occupancy per hour of the week from preliminary-model residuals (CT-H-4.2 .. CT-H-4.4).

    An hour is occupied when the share of its rows with a positive residual (actual above the
    preliminary prediction: under-predicted) is strictly greater than ``threshold`` (0.65). The
    share is an unweighted count over the rows given (the segment's rows of positive weight that
    were fitted). Returns 168 values: 1.0 occupied, 0.0 unoccupied, NaN for an hour with no rows
    (unknown); under ``eemeter_compat`` such an hour is 1.0, as eemeter's lookup casts it.
    """
    tow = np.asarray(tow, dtype=int)
    r = np.asarray(residuals, dtype=float)
    ok = np.isfinite(r)
    n = np.bincount(tow[ok], minlength=168).astype(float)
    pos = np.bincount(tow[ok], weights=(r[ok] > 0).astype(float), minlength=168)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = pos / n
    out = np.where(n > 0, (ratio > threshold).astype(float), np.nan)
    if eemeter_compat:
        out = np.where(np.isnan(out), 1.0, out)
    return out


def _bin_counts(temps: np.ndarray, endpoints) -> np.ndarray:
    """Hours per right-closed bin ``(-inf, B1], (B1, B2], ..., (BN, inf)``."""
    edges = np.asarray(endpoints, dtype=float)
    idx = np.searchsorted(edges, temps, side="left")  # T == B_k falls in bin k (right-closed)
    return np.bincount(idx, minlength=len(edges) + 1)


def fit_temperature_bins(
    temps,
    endpoints=K.BIN_ENDPOINTS,
    *,
    min_count: int = K.MIN_HOURS_PER_BIN,
) -> list:
    """The kept bin endpoints after CalTRACK's merge of sparse bins (CT-H-5.1, CT-H-5.2).

    Bins are right-closed. Each pass counts the hours per bin; a bin with fewer than
    ``min_count`` (20) is invalid. An invalid first bin loses its right endpoint and an invalid
    last bin its left one; **only when neither edge bin was invalid**, every invalid middle bin
    loses its right endpoint. Passes repeat until nothing is removed or one bin remains (eemeter's
    algorithm, ``fit_temperature_bins``).
    """
    t = np.asarray(temps, dtype=float)
    t = t[np.isfinite(t)]
    eps = [float(e) for e in endpoints]
    while eps:
        counts = _bin_counts(t, eps)
        invalid = counts < min_count
        remove = set()
        if invalid[0]:
            remove.add(eps[0])
        if invalid[-1]:
            remove.add(eps[-1])
        if not remove:
            for i in range(1, len(counts) - 1):
                if invalid[i]:
                    remove.add(eps[i])
        if not remove:
            break
        eps = [e for e in eps if e not in remove]
    return eps


def bin_features(temp, endpoints) -> np.ndarray:
    """The temperature-bin features (CT-H-5.3): with kept endpoints ``B1 < ... < BN``,
    ``f1 = min(T, B1)``, ``fn = clip(T - B(n-1), 0, Bn - B(n-1))`` and ``f(N+1) = max(T - BN, 0)``,
    so the features sum to ``T``. With no endpoint the single feature is ``T``. A NaN temperature
    gives NaN features. Shape ``(len(temp), N + 1)``.
    """
    T = np.asarray(temp, dtype=float)
    eps = np.asarray(endpoints, dtype=float)
    if not len(eps):
        return T[:, None].copy()
    cols = [np.minimum(T, eps[0])]
    for k in range(1, len(eps)):
        cols.append(np.clip(T - eps[k - 1], 0.0, eps[k] - eps[k - 1]))
    cols.append(np.maximum(T - eps[-1], 0.0))
    F = np.column_stack(cols)
    F[~np.isfinite(T)] = np.nan
    return F


# --------------------------------------------------------------------------- segments
@dataclass
class _Segment:
    name: str
    centre: int | None
    status: str  # "fitted" | "insufficient" | "no_data"
    month_coverage: dict = field(default_factory=dict)
    occupancy: np.ndarray = field(default_factory=lambda: np.full(168, np.nan))
    endpoints: list = field(default_factory=list)
    interaction: bool = True
    tow_coef: np.ndarray = field(default_factory=lambda: np.full(168, np.nan))
    bin_occ: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bin_unocc: np.ndarray = field(default_factory=lambda: np.zeros(0))
    n_rows: int = 0
    num_parameters: int = 0
    metrics: dict = field(default_factory=dict)

    def predict(self, tow: np.ndarray, T: np.ndarray) -> tuple:
        """``(prediction, unseen)``: NaN where the temperature or the hour's coefficient is
        missing (``unseen`` marks the hours of the week without one, CT-H-8.3)."""
        n = len(T)
        if self.status != "fitted":
            return np.full(n, np.nan), np.zeros(n, bool)
        coef = self.tow_coef[tow]
        unseen = ~np.isfinite(coef)
        F = bin_features(T, self.endpoints)
        occ = self.occupancy[tow]
        with np.errstate(invalid="ignore"):
            b_occ = F @ self.bin_occ
            b_unocc = F @ self.bin_unocc
        bins = np.where(occ == 1.0, b_occ, b_unocc) if self.interaction else b_occ
        pred = coef + bins
        bad = unseen | ~np.isfinite(T) | ~np.isfinite(occ)
        return np.where(bad, np.nan, pred), unseen

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "centre_month": self.centre,
            "status": self.status,
            "month_coverage": dict(self.month_coverage),
            "occupancy": floats_or_none(self.occupancy),
            "endpoints": list(self.endpoints),
            "interaction": bool(self.interaction),
            "tow_coef": floats_or_none(self.tow_coef),
            "bin_occupied": floats_or_none(self.bin_occ),
            "bin_unoccupied": floats_or_none(self.bin_unocc),
            "n_rows": int(self.n_rows),
            "num_parameters": int(self.num_parameters),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, d: dict) -> _Segment:
        return cls(
            name=d["name"],
            centre=d.get("centre_month"),
            status=d["status"],
            month_coverage=dict(d.get("month_coverage") or {}),
            occupancy=floats_from(d["occupancy"]),
            endpoints=[float(x) for x in d.get("endpoints") or []],
            interaction=bool(d.get("interaction", True)),
            tow_coef=floats_from(d["tow_coef"]),
            bin_occ=floats_from(d.get("bin_occupied") or []),
            bin_unocc=floats_from(d.get("bin_unoccupied") or []),
            n_rows=int(d.get("n_rows", 0)),
            num_parameters=int(d.get("num_parameters", 0)),
            metrics=dict(d.get("metrics") or {}),
        )


def _month_coverage(frame: pd.DataFrame, grid: pd.DatetimeIndex) -> dict:
    """Share of each calendar month's in-baseline hours that have consumption (CT-H-1.1)."""
    total = pd.Series(1, index=grid).groupby(grid.month).sum()
    present = frame["energy"].notna()
    have = present.groupby(frame.index.month).sum()
    return {int(m): float(have.get(m, 0) / total[m]) for m in total.index}


def _fit_segment(
    f: pd.DataFrame,
    tow: np.ndarray,
    months: np.ndarray,
    centre: int | None,
    *,
    coverage: dict,
    eemeter_compat: bool,
    occupancy_threshold: float,
    endpoints,
    min_bin_hours: int,
) -> _Segment:
    name = "all" if centre is None else SEGMENT_NAMES[centre - 1]
    seg = _Segment(name=name, centre=centre, status="no_data")
    if centre is not None:
        needed = [(centre - 2) % 12 + 1, centre, centre % 12 + 1]
        seg.month_coverage = {str(m): round(coverage.get(m, 0.0), 6) for m in needed}
        short = [m for m in needed if not coverage.get(m, 0.0) > K.HOURLY_MONTH_COVERAGE]
        if short and not eemeter_compat:
            seg.status = "insufficient"
            return seg
    w = segment_weights(months, centre)
    y = f["energy"].to_numpy(float)
    T = f["temp"].to_numpy(float)
    pos = w > 0
    fit_rows = pos & np.isfinite(y) & np.isfinite(T)
    if not fit_rows.any():
        return seg
    # CT-H-4.1: the preliminary model UPH = mu + beta_H HDH50 + beta_C CDH65, weighted
    Tf = T[fit_rows]
    Xp = np.column_stack(
        [
            np.ones(fit_rows.sum()),
            np.maximum(Tf - K.PRELIM_CDD_BP, 0.0),
            np.maximum(K.PRELIM_HDD_BP - Tf, 0.0),
        ]
    )
    prelim = wls(Xp, y[fit_rows], w[fit_rows])
    occ = occupancy_lookup(
        tow[fit_rows],
        prelim.resid,
        threshold=occupancy_threshold,
        eemeter_compat=eemeter_compat,
    )
    # CT-H-5.2: bins counted on the rows of positive weight with a temperature
    eps = fit_temperature_bins(T[pos & np.isfinite(T)], endpoints, min_count=min_bin_hours)
    # CT-H-6.1 / 6.2: hour-of-week levels, no intercept, bins x occupancy unless one mode only
    occ_rows = occ[tow]
    pos_occ = occ_rows[pos]
    known = pos_occ[np.isfinite(pos_occ)]
    interaction = bool(len(known)) and 0 < known.sum() < len(known)
    seen = np.unique(tow[fit_rows])
    if eemeter_compat:
        # eemeter's design has a column for every hour present in the baseline data passed
        # (weight-0 rows included), so its parameter count (and the metrics' n - p) includes
        # them; their ~0 coefficients are not kept (below)
        seen = np.unique(tow[np.isfinite(y) & np.isfinite(T)])
    col_of = {int(h): j for j, h in enumerate(seen)}
    n = int(fit_rows.sum())
    D = np.zeros((n, len(seen)))
    rows_tow = tow[fit_rows]
    D[np.arange(n), [col_of[int(h)] for h in rows_tow]] = 1.0
    F = bin_features(Tf, eps)
    o = occ_rows[fit_rows]
    o = np.where(np.isfinite(o), o, 0.0)
    if interaction:
        X = np.hstack([D, F * o[:, None], F * (1.0 - o)[:, None]])
    else:
        X = np.hstack([D, F])
    r = wls(X, y[fit_rows], w[fit_rows], has_const=False)
    tow_coef = np.full(168, np.nan)
    trained = set(np.unique(rows_tow).tolist())
    for h, j in col_of.items():
        # an hour with no positive-weight row has no coefficient: it predicts NaN in both modes
        # (eemeter 3.1's fix; eemeter 2.10's ~0 from the pseudo-inverse is a bug, CT-H-6.3)
        if h in trained:
            tow_coef[h] = r.params[j]
    k = F.shape[1]
    b = r.params[len(seen) :]
    seg.status = "fitted"
    seg.occupancy = occ
    seg.endpoints = eps
    seg.interaction = interaction
    seg.tow_coef = tow_coef
    seg.bin_occ = b[:k].copy()
    seg.bin_unocc = b[k:].copy() if interaction else b[:k].copy()
    seg.n_rows = n
    seg.num_parameters = int(X.shape[1])
    # CT-H-7.2: the segment's metrics over its centre month (weight 1) only
    centre_rows = (w == K.SEGMENT_CENTRE_WEIGHT) & np.isfinite(y) & np.isfinite(T)
    pred, _ = seg.predict(tow[centre_rows], T[centre_rows])
    seg.metrics = caltrack_metrics(y[centre_rows], pred, seg.num_parameters)
    return seg


# --------------------------------------------------------------------------- the model
@dataclass
class CalTRACKHourlyModel:
    """A fitted CalTRACK 2.0 hourly model: one segment per calendar month (or one for the year).

    ``segments`` maps a segment name (eemeter's, e.g. ``"dec-jan-feb-weighted"``, or ``"all"``)
    to its fit: status (``fitted``, ``insufficient`` under CT-H-1.1, or ``no_data``), occupancy
    per hour of the week, kept bin endpoints, the 168 hour-of-week coefficients (NaN for an hour
    the segment never saw) and the occupied / unoccupied bin coefficients. Build one with
    :func:`fit_caltrack_hourly`; :meth:`predict` projects it, :meth:`as_dict` / :meth:`from_dict`
    serialise it.
    """

    segment_type: str
    segments: dict
    eemeter_compat: bool = False
    timezone: str | None = None
    baseline_start: str | None = None
    baseline_end: str | None = None
    temperature_range: tuple | None = None

    def predict(self, index, temp, *, return_counts: bool = False):
        """Predicted hourly energy at ``index`` for temperatures ``temp`` (CT-H-8).

        A reporting hour uses the model of its own calendar month (CT-H-8.1): hour of the week
        from the timestamp, occupancy from that segment's lookup, bin features from its kept
        endpoints (CT-H-8.2; beyond the baseline range the edge bins extrapolate linearly). NaN
        for a missing temperature, an hour of the week the segment never saw, or a segment with
        no model (CT-H-8.3). ``temp`` is an array aligned with ``index`` or a series (reindexed to
        it). The series' ``attrs`` (and, with ``return_counts``, a second return value) count
        ``n_unseen_hour_of_week``, ``n_no_segment_model`` and ``n_no_temperature``.
        """
        idx = pd.DatetimeIndex(index)
        if isinstance(temp, pd.Series) and isinstance(temp.index, pd.DatetimeIndex):
            T = temp.reindex(idx).to_numpy(float)
        else:
            T = np.asarray(temp, dtype=float)
            if len(T) != len(idx):
                raise ValueError("temp must align with index")
        tow = hour_of_week(idx)
        months = idx.month.to_numpy()
        out = np.full(len(idx), np.nan)
        unseen = np.zeros(len(idx), bool)
        no_model = np.zeros(len(idx), bool)
        groups: dict[str, np.ndarray]
        if self.segment_type == "single":
            groups = {"all": np.flatnonzero(np.ones(len(idx), bool))}
        else:
            groups = {SEGMENT_NAMES[m - 1]: np.flatnonzero(months == m) for m in np.unique(months)}
        for name, rows in groups.items():
            seg = self.segments.get(name)
            if seg is None or seg.status != "fitted":
                no_model[rows] = True
                continue
            p, u = seg.predict(tow[rows], T[rows])
            out[rows] = p
            unseen[rows] = u & np.isfinite(T[rows])
        counts = {
            "n_unseen_hour_of_week": int(unseen.sum()),
            "n_no_segment_model": int(no_model.sum()),
            "n_no_temperature": int((~np.isfinite(T) & ~no_model).sum()),
        }
        s = pd.Series(out, index=idx, name="predicted")
        s.attrs.update(counts)
        return (s, counts) if return_counts else s

    @property
    def n_parameters(self) -> int:
        """Parameters over the fitted segments (each segment's design columns)."""
        return int(sum(s.num_parameters for s in self.segments.values() if s.status == "fitted"))

    def as_dict(self) -> dict:
        """A JSON-safe dict; :meth:`from_dict` rebuilds the model."""
        return {
            "type": "CalTRACKHourlyModel",
            "method": "CalTRACK 2.0 hourly",
            "segment_type": self.segment_type,
            "eemeter_compat": self.eemeter_compat,
            "timezone": self.timezone,
            "baseline_start": self.baseline_start,
            "baseline_end": self.baseline_end,
            "temperature_range": None
            if self.temperature_range is None
            else list(self.temperature_range),
            "segments": {k: s.as_dict() for k, s in self.segments.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> CalTRACKHourlyModel:
        """Rebuild a model written by :meth:`as_dict`."""
        tr = d.get("temperature_range")
        return cls(
            segment_type=d["segment_type"],
            segments={k: _Segment.from_dict(v) for k, v in d["segments"].items()},
            eemeter_compat=bool(d.get("eemeter_compat", False)),
            timezone=d.get("timezone"),
            baseline_start=d.get("baseline_start"),
            baseline_end=d.get("baseline_end"),
            temperature_range=None if tr is None else tuple(tr),
        )


def _window(f: pd.DataFrame, intervention_start) -> tuple:
    if intervention_start is not None:
        end = pd.Timestamp(intervention_start)
        tz = f.index.tz
        if tz is not None and end.tzinfo is None:
            end = end.tz_localize(tz)
    else:
        end = f["energy"].dropna().index.max() + pd.Timedelta(hours=1)
    return end - pd.Timedelta(days=K.BASELINE_DAYS), end


def fit_caltrack_hourly(
    energy: pd.Series,
    temp: pd.Series,
    *,
    segment_type: str = "three_month_weighted",
    eemeter_compat: bool = False,
    intervention_start=None,
    fuel: str = "electricity",
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    occupancy_threshold: float = K.OCCUPANCY_THRESHOLD,
    bin_endpoints=K.BIN_ENDPOINTS,
    min_bin_hours: int = K.MIN_HOURS_PER_BIN,
) -> tuple:
    """Fit a CalTRACK 2.0 hourly model on the 365 days before the intervention: ``(model, info)``.

    ``segment_type`` is ``"three_month_weighted"`` (the default: twelve month models, CT-H-2.1)
    or ``"single"`` (one model on the year, allowed under the conditions of CT-H-2.3, which
    :func:`single_model_tests` evaluates). There is no fixed minimum baseline length (CT-H-1.1):
    a month segment whose month or either neighbour has consumption for 90 % of its baseline
    hours or fewer is ``insufficient`` and predicts NaN (not checked under ``eemeter_compat``,
    as in eemeter 2.10). ``info`` holds ``frame`` (the baseline hours), ``month_coverage`` and the
    preparation counts. A baseline in which no segment could be fitted raises
    :class:`~camber.mandv.sufficiency.InsufficientBaseline`.
    """
    if segment_type not in ("three_month_weighted", "single"):
        raise ValueError("segment_type must be 'three_month_weighted' or 'single'")
    f = hourly_frame(
        energy,
        temp,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
        temp_bounds=temp_bounds,
    )
    if f.empty or f["energy"].dropna().empty:
        raise ValueError("CalTRACK hourly: no meter data")
    start, end = _window(f, intervention_start)
    n_outside = int(f["energy"].notna()[(f.index < start) | (f.index >= end)].sum())
    attrs = dict(f.attrs)
    f = f[(f.index >= start) & (f.index < end)]
    grid = pd.date_range(start, end, freq="h", tz=f.index.tz, inclusive="left")
    f = f.reindex(grid)
    coverage = _month_coverage(f, grid)
    tow = hour_of_week(f.index)
    months = f.index.month.to_numpy()
    centres: list = [None] if segment_type == "single" else list(range(1, 13))
    segments = {}
    for c in centres:
        seg = _fit_segment(
            f,
            tow,
            months,
            c,
            coverage=coverage,
            eemeter_compat=eemeter_compat,
            occupancy_threshold=occupancy_threshold,
            endpoints=bin_endpoints,
            min_bin_hours=min_bin_hours,
        )
        segments[seg.name] = seg
    if not any(s.status == "fitted" for s in segments.values()):
        from .sufficiency import InsufficientBaseline, baseline_need

        n_have = int(f["energy"].notna().sum())
        raise InsufficientBaseline(
            "CalTRACK hourly: no month segment has more than 90 % of the hours of its month and "
            "both neighbouring months (CT-H-1.1)",
            baseline_need("hourly", n_have, min_n=int(np.ceil(0.9 * 24 * 92)) + 1),
        )
    T = f["temp"].to_numpy(float)
    fin = T[np.isfinite(T)]
    model = CalTRACKHourlyModel(
        segment_type=segment_type,
        segments=segments,
        eemeter_compat=eemeter_compat,
        timezone=timezone,
        baseline_start=str(start),
        baseline_end=str(end),
        temperature_range=(float(fin.min()), float(fin.max())) if len(fin) else None,
    )
    info = {
        "frame": f,
        "month_coverage": coverage,
        "n_hours_outside_baseline": n_outside,
        **attrs,
    }
    return model, info


def single_model_tests(
    model: CalTRACKHourlyModel, frame: pd.DataFrame, reporting_temp=None
) -> dict:
    """The two CT-H-2.3 conditions under which a single 365-day model is allowed.

    (a) At most two baseline months with ``|NMBE| > 1 %`` (eemeter's sign; the methods document
    does not say whether the 1 % is absolute, CAMBER reads it as ``|NMBE|``), evaluated on
    ``model``'s in-sample predictions over ``frame`` (the baseline hours); (b) reporting
    temperatures more than 10 % of the baseline range outside it. Either allows a single model.
    """
    pred = model.predict(frame.index, frame["temp"].to_numpy(float))
    y = frame["energy"]
    ok = y.notna() & pred.notna()
    nmbe: dict = {}
    for m, rows in y[ok].groupby(y[ok].index.month).groups.items():
        yy = y.loc[rows].to_numpy(float)
        pp = pred.loc[rows].to_numpy(float)
        nmbe[int(m)] = float((pp - yy).sum() / yy.sum()) if yy.sum() else None
    over = [m for m, v in nmbe.items() if v is not None and abs(v) > K.SINGLE_MODEL_NMBE]
    out: dict[str, Any] = {
        "monthly_nmbe": nmbe,
        "n_months_nmbe_over_1pct": len(over),
        "nmbe_rule_allows_single": len(over) <= K.SINGLE_MODEL_MAX_MONTHS,
    }
    tb = frame["temp"].dropna()
    if reporting_temp is not None and len(tb):
        tr = pd.Series(np.asarray(reporting_temp, dtype=float)).dropna()
        lo, hi = float(tb.min()), float(tb.max())
        margin = K.SINGLE_MODEL_RANGE_FRACTION * (hi - lo)
        beyond = bool(len(tr)) and (float(tr.min()) < lo - margin or float(tr.max()) > hi + margin)
        out.update(
            {
                "baseline_temp_range": [lo, hi],
                "reporting_temp_range": [float(tr.min()), float(tr.max())] if len(tr) else None,
                "temp_range_rule_allows_single": beyond,
            }
        )
    return out


def caltrack_hourly_savings(
    baseline_energy: pd.Series,
    baseline_temp: pd.Series,
    reporting_energy: pd.Series,
    reporting_temp: pd.Series,
    *,
    segment_type: str = "three_month_weighted",
    eemeter_compat: bool = False,
    intervention_start=None,
    fuel: str = "electricity",
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    confidence: float = 0.90,
    extrapolation=None,
) -> CalTRACKResult:
    """CalTRACK 2.0 **Hourly** avoided energy use, as a :class:`CalTRACKResult`.

    Fits :func:`fit_caltrack_hourly` and projects each reporting hour with its month's model:
    ``AEU_h = predicted_h - observed_h`` (CT-H-8.4). Hours with no prediction (a missing
    temperature, an hour of the week the month's segment never saw, a month without a model) or
    no observation are left out of the sums and counted in the caveats, never filled.

    ``result.savings`` is CAMBER's :class:`~camber.mandv.stats.SavingsResult` with the ASHRAE
    Guideline 14 band, computed on the baseline hours each predicted by its own month's model,
    with ``p`` the parameters of all fitted segments (the pooled form of eemeter's per-segment
    ``n - num_parameters``). CalTRACK 2.0 publishes FSU coefficients for daily and billing only,
    so ``result.fsu`` is ``None``. ``result.sufficiency`` holds each month's coverage, the
    segments' statuses and the single-model tests (CT-H-2.3); a reporting temperature far outside
    the baseline's range adds a caveat (E16).
    """
    model, info = fit_caltrack_hourly(
        baseline_energy,
        baseline_temp,
        segment_type=segment_type,
        eemeter_compat=eemeter_compat,
        intervention_start=intervention_start,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        temp_bounds=temp_bounds,
    )
    base = info["frame"]
    rep = hourly_frame(
        reporting_energy,
        reporting_temp,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
        temp_bounds=temp_bounds,
    )
    rep = rep[rep["energy"].notna() | rep["temp"].notna()]
    if rep["energy"].dropna().empty:
        raise ValueError("no usable reporting-period data")
    yb = base["energy"].to_numpy(float)
    Tb = base["temp"].to_numpy(float)
    pb = model.predict(base.index, Tb).to_numpy(float)
    ok = np.isfinite(yb) & np.isfinite(pb)
    months_used = {
        SEGMENT_NAMES[m - 1] if model.segment_type != "single" else "all"
        for m in np.unique(base.index.month[ok])
    }
    p = int(sum(model.segments[k].num_parameters for k in months_used))
    if p >= int(ok.sum()):
        p = max(s.num_parameters for s in model.segments.values())
    from .stats import avoided_energy_savings, cv_rmse_max_for, fit_stats

    fit = fit_stats(yb, pb, p, cv_rmse_max=cv_rmse_max_for("hourly"), time_index=base.index)
    from .coverage import _linear_fit_record, _safe

    resid = (yb - pb)[ok]
    rec = _safe(
        _linear_fit_record,
        Tb[ok],
        ("temperature",),
        None,
        s2=float(resid @ resid) / max(len(resid) - p, 1),
        n=int(ok.sum()),
        p=p,
        rho=fit.rho_lag1,
    )
    rep_idx = rep.index
    view = ProjectionView(lambda T: model.predict(rep_idx, T).to_numpy(float), rec)
    yr = rep["energy"].to_numpy(float)
    Tr = rep["temp"].to_numpy(float)
    savings = avoided_energy_savings(
        view,
        Tr,
        yr,
        cv_rmse=fit.cv_rmse,
        n_baseline=fit.n,
        p_baseline=p,
        confidence=confidence,
        rho=fit.rho_lag1,
        extrapolation=extrapolation,
    )
    pr, counts = model.predict(rep_idx, Tr, return_counts=True)
    table = pd.DataFrame(
        {"observed": yr, "predicted": pr.to_numpy(float), "temp": Tr}, index=rep_idx
    )
    table["avoided"] = table["predicted"] - table["observed"]
    caveats = []
    has_y = table["observed"].notna().to_numpy()
    if counts["n_unseen_hour_of_week"]:
        n = int((has_y & np.isfinite(Tr) & ~np.isfinite(pr.to_numpy(float))).sum())
        caveats.append(
            f"{counts['n_unseen_hour_of_week']} reporting hour(s) fall on an hour of the week "
            "their month's baseline segment never saw: predicted NaN (as eemeter 3.1; eemeter "
            f"2.10 gave them a coefficient of about 0) and left out of the savings ({n} had "
            "usage)"
        )
    if counts["n_no_segment_model"]:
        caveats.append(
            f"{counts['n_no_segment_model']} reporting hour(s) fall in a month whose segment has "
            "no model (insufficient or no baseline data, CT-H-1.1): left out of the savings"
        )
    if counts["n_no_temperature"]:
        caveats.append(
            f"{counts['n_no_temperature']} reporting hour(s) have no temperature: no "
            "counterfactual (CT-H-8.3)"
        )
    statuses = {k: s.status for k, s in model.segments.items()}
    not_fitted = [k for k, v in statuses.items() if v != "fitted"]
    if not_fitted:
        caveats.append(
            f"{len(not_fitted)} month segment(s) not fitted ({', '.join(not_fitted)}): too few "
            "baseline hours in the month or a neighbour (CT-H-1.1)"
        )
    if info.get("n_hours_outside_baseline"):
        caveats.append(
            f"{info['n_hours_outside_baseline']} meter hour(s) outside the 365-day baseline before "
            "the intervention were not used (CT-H-1.3)"
        )
    for what, a in (("baseline", info), ("reporting", rep.attrs)):
        if a.get("n_temp_screened"):
            caveats.append(
                screened_caveat(int(a["n_temp_screened"]), f"{what} temperature value(s)")
            )
        if a.get("n_zero_masked"):
            caveats.append(
                f"{a['n_zero_masked']} {what} electricity value(s) of exactly 0 were treated as "
                "missing (CT-D-1.3)"
            )
        if a.get("n_negative"):
            caveats.append(
                f"{a['n_negative']} negative {what} meter value(s): flagged for review (possible "
                "net metering, CT-D-1.5)"
            )
    single = single_model_tests(model, base, Tr)
    if single.get("temp_range_rule_allows_single"):
        caveats.append(
            "reporting temperatures lie more than 10 % of the baseline range outside it: the "
            "edge temperature bins extrapolate linearly, and CalTRACK allows a single 365-day "
            "model in this case (CT-H-2.3(b), E16)"
        )
    savings.caveats.extend(caveats)
    sufficiency = {
        "status": "PASS" if not not_fitted else "PARTIAL",
        "segment_type": model.segment_type,
        "baseline_start": model.baseline_start,
        "baseline_end": model.baseline_end,
        "month_coverage": {str(k): round(v, 6) for k, v in info["month_coverage"].items()},
        "segments": statuses,
        "single_model": single,
        "reporting_counts": counts,
    }
    return CalTRACKResult(
        method="CalTRACK 2.0 hourly",
        model=model,
        savings=savings,
        sufficiency=sufficiency,
        fit=fit,
        fsu=None,
        eemeter_compat=eemeter_compat,
        reporting=table,
    )
