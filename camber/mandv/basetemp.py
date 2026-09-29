"""Degree-day base temperatures chosen from the data, for bills (provisional; 0.94, issue #72).

A degree-day model ``E = base + a·HDD + b·CDD`` is only as good as its base temperatures. The
fixed 65 °F convention rarely matches a building: internal gains, setpoints and envelope set its
balance points, and heating and cooling generally balance at different temperatures. This module
chooses them from the bills themselves:

* **Degree days from each day, not from the bill's mean.** For every candidate base, each day's
  heating / cooling degree-days are built from that day's temperatures (the hourly values when the
  series is sub-daily, the daily mean otherwise; :func:`camber.mandv.billing.daily_weather`) and
  summed over the bill's service days. Degree days computed from the *mean* temperature of a
  ~30-day bill undercount the swing days: a shoulder-month bill whose mean sits above the base
  still has cold days below it. (:func:`bill_degree_days`)
* **Separate heating and cooling bases.** ``DD-H`` (heating only), ``DD-C`` (cooling only) and
  ``DD-HC`` (both, heating base no higher than the cooling base) are each searched over their own
  grid by days-weighted least squares (a bill's per-day energy has variance ``1/days``).
* **A selection profile.** For every candidate base the profile row gives the weighted SSE, R²,
  adjusted R², CV(RMSE), NMBE and BIC of the best fit at that base (for ``DD-HC``, minimised over
  the other leg's base). The **range** of a base is the set of candidates whose SSE is within the
  likelihood-ratio tolerance of the best, ``n·ln(SSE/SSE_min) <= tolerance`` (default 3.84, the
  95% point of chi-square with one degree of freedom: a profile-likelihood interval, approximate
  because SSE is not smooth in a base). A range covering half the search grid or more is
  **flat**: the data do not determine the base, and a caveat says so. A base on the edge of the
  grid is a caveat too.
* **Honest parameter counts.** A fitted base is a parameter: ``DD-H`` and ``DD-C`` have
  ``p = 3`` (intercept, slope, base), ``DD-HC`` ``p = 5``, the way a change-point model's change
  points count (:data:`camber.mandv.models.N_PARAMS`). BIC, CV(RMSE)'s ``n - p``, adjusted R² and
  the regression tests all use them.
* **Signs.** A slope of the wrong (negative) sign is refused at that base; a kind with no logical
  fit anywhere on its grid is refused.

**Selection.** Within a kind the base(s) with the least weighted SSE win -- for a fixed parameter
count that is also the highest R² and the lowest CV(RMSE), so R² adds no selection signal and is
reported, not used. Across kinds, and against the change-point models of
:mod:`camber.mandv.models`, the choice is BIC ``n·ln(SSE/n) + p·ln(n)`` with the full parameter
counts, exactly as :func:`~camber.mandv.models.best_model` ranks change-point kinds. This is the
inverse-modelling family of ASHRAE RP-1050 and ASHRAE Guideline 14 (variable-base degree-day and
change-point models; no text quoted, and no section number claimed: the standard is paywalled).

:class:`BillingDegreeDayModel` is a first-class CAMBER baseline: it predicts energy per day from a
bill's per-day HDD and CDD at its own bases, and carries the private fit record (support per
column, ``(X'WX)^-1``, ``s2``, ``n``, ``p``, ``rho``, ``weight_scale``), so coverage, both savings
kernels, the regression tests, the SEP verdict and the versioned store all work on it unchanged.

Every name here is provisional (docs/API-STABILITY.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "DD_KINDS",
    "BillingDegreeDayModel",
    "BaseSearch",
    "BaseSelection",
    "bill_degree_days",
    "frame_rows",
    "rows_at_bases",
    "fit_bill_degree_day",
    "select_bases",
    "compare_models",
]

#: The degree-day kinds: heating only, cooling only, both legs.
DD_KINDS = ("DD-H", "DD-C", "DD-HC")
_LEGS = {"DD-H": ("h",), "DD-C": ("c",), "DD-HC": ("h", "c")}
_COLS = {"h": "hdd", "c": "cdd"}
_SLOPE = {"h": "heating_slope", "c": "cooling_slope"}

# the likelihood-ratio tolerance of a base's range: chi-square(1) at 95%
_LR_95 = 3.841458820694124


# --------------------------------------------------------------------------- the model


@dataclass
class BillingDegreeDayModel:
    """A degree-day baseline on bills: ``E/day = base + a·HDD/day + b·CDD/day`` (provisional).

    ``kind`` is one of :data:`DD_KINDS`. ``heating_base_f`` / ``cooling_base_f`` are the bases the
    degree days are built at (``None`` for a leg the kind lacks). ``predict`` takes the per-day
    degree days of each row, one column per leg in the order heating, cooling
    (:attr:`columns`). ``p`` counts every parameter, the bases included when ``bases_fitted``.
    """

    kind: str
    heating_base_f: float | None
    cooling_base_f: float | None
    intercept: float
    heating_slope: float
    cooling_slope: float
    sse: float
    n: int
    p: int
    bases_fitted: bool = True
    _fit_record: object = field(default=None, repr=False, compare=False)
    caveats: list = field(default_factory=list)

    @property
    def columns(self) -> tuple:
        """The frame columns (period totals) this model's rows are built from."""
        return tuple(_COLS[g] for g in _LEGS[self.kind])

    @property
    def coef(self) -> tuple:
        """The slopes, in :attr:`columns` order."""
        return tuple(getattr(self, _SLOPE[g]) for g in _LEGS[self.kind])

    @property
    def change_points(self) -> tuple:
        """The bases, in :attr:`columns` order (a degree-day model's balance points)."""
        return tuple(
            self.heating_base_f if g == "h" else self.cooling_base_f for g in _LEGS[self.kind]
        )

    @property
    def coeffs(self) -> dict:
        d = {"base": self.intercept}
        for g in _LEGS[self.kind]:
            d[_SLOPE[g]] = getattr(self, _SLOPE[g])
        return d

    def predict(self, X):
        """Energy per day at rows of per-day degree days (``n x k``, or 1-D when ``k == 1``)."""
        D = _rows_2d(X, len(_LEGS[self.kind]))
        out = self.intercept + D @ np.asarray(self.coef, dtype=float)
        a = np.asarray(X, dtype=float)
        return out[0] if a.ndim == 0 or (a.ndim == 1 and len(self.coef) > 1) else out

    def rows_from_temps(self, temps) -> np.ndarray:
        """Per-day degree-day rows at this model's bases from **daily mean** temperatures (for a
        normal year given as daily temperatures)."""
        t = np.asarray(temps, dtype=float).ravel()
        cols = []
        for g in _LEGS[self.kind]:
            b = self.heating_base_f if g == "h" else self.cooling_base_f
            assert b is not None  # a leg of the kind always has its base
            cols.append(np.clip(b - t, 0.0, None) if g == "h" else np.clip(t - b, 0.0, None))
        return np.column_stack(cols)

    def coverage(self, X, *, projected=None, policy=None):
        """Coverage of rows of per-day degree days (a :class:`~camber.mandv.coverage.Coverage`)."""
        from .coverage import _linear_coverage

        return _linear_coverage(
            self, _rows_2d(X, len(self.coef)), projected=projected, policy=policy
        )

    def as_dict(self) -> dict:
        """A JSON-safe dict from which :meth:`from_dict` rebuilds an identical model."""
        from .coverage import _FitRecord

        rec = self._fit_record
        d: dict = {
            "type": "BillingDegreeDayModel",
            "kind": self.kind,
            "heating_base_f": None if self.heating_base_f is None else float(self.heating_base_f),
            "cooling_base_f": None if self.cooling_base_f is None else float(self.cooling_base_f),
            "intercept": float(self.intercept),
            "heating_slope": float(self.heating_slope),
            "cooling_slope": float(self.cooling_slope),
            "sse": float(self.sse),
            "n": int(self.n),
            "p": int(self.p),
            "bases_fitted": bool(self.bases_fitted),
            "fit_record": rec.as_dict() if isinstance(rec, _FitRecord) else None,
        }
        if self.caveats:
            d["caveats"] = list(self.caveats)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> BillingDegreeDayModel:
        """Rebuild a model written by :meth:`as_dict`."""
        from .coverage import _FitRecord

        hb, cb = d.get("heating_base_f"), d.get("cooling_base_f")
        return cls(
            kind=d["kind"],
            heating_base_f=None if hb is None else float(hb),
            cooling_base_f=None if cb is None else float(cb),
            intercept=float(d["intercept"]),
            heating_slope=float(d["heating_slope"]),
            cooling_slope=float(d["cooling_slope"]),
            sse=float(d["sse"]),
            n=int(d["n"]),
            p=int(d["p"]),
            bases_fitted=bool(d.get("bases_fitted", True)),
            _fit_record=_FitRecord.from_dict(d.get("fit_record")),
            caveats=list(d.get("caveats") or []),
        )


def _rows_2d(X, k: int) -> np.ndarray:
    D = np.asarray(X, dtype=float)
    if D.ndim == 0:
        D = D.reshape(1, 1)
    elif D.ndim == 1:
        D = D.reshape(1, -1) if (k > 1 and D.size == k) else D[:, None]
    if D.shape[1] != k:
        raise ValueError(f"expected {k} degree-day column(s), got {D.shape[1]}")
    return D


def frame_rows(frame: pd.DataFrame, model) -> np.ndarray:
    """A bills frame's per-day degree-day rows for ``model``: each period total over its days.

    The frame must have been built at the model's bases; its ``attrs`` (set by
    :meth:`~camber.mandv.billing.BillingSeries.energy_vs_temp`) are checked when they carry them.
    """
    return rows_at_bases(frame, model.kind, model.heating_base_f, model.cooling_base_f)


def rows_at_bases(frame: pd.DataFrame, kind: str, heating_base_f, cooling_base_f) -> np.ndarray:
    """:func:`frame_rows` for a kind and its bases (no model needed)."""
    legs = _LEGS[kind]
    for g, attr in (("h", "heating_base_f"), ("c", "cooling_base_f")):
        if g not in legs:
            continue
        have = frame.attrs.get(attr)
        want = heating_base_f if g == "h" else cooling_base_f
        if have is not None and want is not None and abs(float(have) - float(want)) > 1e-9:
            raise ValueError(
                f"the bills' {_COLS[g].upper()} are at {float(have):g} F, but the model's "
                f"{'heating' if g == 'h' else 'cooling'} base is {float(want):g} F"
            )
    d = frame["days"].to_numpy(float) if "days" in frame.columns else 1.0
    return np.column_stack([frame[_COLS[g]].to_numpy(float) / d for g in legs])


# --------------------------------------------------------------------------- degree days


def _hourly_or_daily(temp: pd.Series) -> tuple:
    """``(values, day_index)``: the hourly means (sub-daily input) or daily means, and the
    normalised day each value belongs to -- the same reduction as ``daily_weather``."""
    t = pd.Series(temp).dropna().sort_index()
    t.index = pd.DatetimeIndex(t.index)
    if t.empty:
        return np.array([]), pd.DatetimeIndex([])
    step = float(np.median(np.diff(t.index.asi8))) / 3.6e12 if len(t) > 1 else 24.0
    if step < 23.0:
        h = t.resample("1h").mean().dropna()
        return h.to_numpy(float), h.index.normalize()
    d = t.groupby(t.index.normalize()).mean()
    return d.to_numpy(float), pd.DatetimeIndex(d.index)


def bill_degree_days(temp: pd.Series, starts, ends, bases, *, leg: str) -> np.ndarray:
    """Per-day degree days of each bill at each base: an ``(n_bills, n_bases)`` array.

    ``starts`` / ``ends`` are each bill's first day and exclusive end. ``leg`` is ``"heating"``
    or ``"cooling"``. Each day's degree days are the mean hourly contribution (sub-daily
    ``temp``) or the daily mean's (daily ``temp``), and a bill's value is their mean over the
    days it covers -- :meth:`~camber.mandv.billing.BillingSeries.period_weather`'s period total
    divided by the bill's days. A bill with no covered day is NaN.
    """
    if leg not in ("heating", "cooling"):
        raise ValueError("leg must be 'heating' or 'cooling'")
    bases = np.asarray(list(bases), dtype=float)
    vals, day = _hourly_or_daily(temp)
    n = len(pd.DatetimeIndex(starts))
    out = np.full((n, len(bases)), np.nan)
    if not len(vals):
        return out
    days, inv = np.unique(day.asi8, return_inverse=True)
    counts = np.bincount(inv).astype(float)
    starts_i = pd.DatetimeIndex(starts).normalize().asi8
    ends_i = pd.DatetimeIndex(ends).normalize().asi8
    lo = np.searchsorted(days, starts_i, side="left")
    hi = np.searchsorted(days, ends_i, side="left")  # exclusive end
    ncov = hi - lo
    for j, b in enumerate(bases):
        dd = np.clip(b - vals, 0.0, None) if leg == "heating" else np.clip(vals - b, 0.0, None)
        per_day = np.bincount(inv, weights=dd) / counts
        cs = np.concatenate([[0.0], np.cumsum(per_day)])
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:, j] = np.where(ncov > 0, (cs[hi] - cs[lo]) / np.maximum(ncov, 1), np.nan)
    return out


# --------------------------------------------------------------------------- fitting


def _wls(X: np.ndarray, y: np.ndarray, w) -> tuple:
    from .models import _lstsq_sse

    return _lstsq_sse(X, y, w)


def _bic(sse: float, n: int, p: int) -> float:
    return float(n * np.log(sse / n + 1e-12) + p * np.log(n))


def fit_bill_degree_day(
    rows,
    energy,
    *,
    kind: str,
    heating_base_f: float | None = None,
    cooling_base_f: float | None = None,
    days=None,
    bases_fitted: bool = True,
    time_index=None,
) -> BillingDegreeDayModel:
    """Fit a :class:`BillingDegreeDayModel` at given bases.

    ``rows`` are the per-day degree days (``n x k``, heating then cooling), ``energy`` the energy
    per day, ``days`` each bill's days (the weights; ``None`` for daily rows). ``bases_fitted``
    says whether the bases were chosen from these data (they then count in ``p``). A slope of the
    wrong sign is kept and named in ``caveats`` (the caller refuses the fit).
    """
    from .coverage import _linear_fit_record, _safe
    from .models import _rho_of, fit_weights

    if kind not in DD_KINDS:
        raise ValueError(f"kind must be one of {DD_KINDS}, got {kind!r}")
    legs = _LEGS[kind]
    D = _rows_2d(rows, len(legs))
    y = np.asarray(energy, dtype=float).ravel()
    ok = np.isfinite(y) & np.all(np.isfinite(D), axis=1)
    idx = None if time_index is None else np.asarray(time_index)[ok]
    w, scale = fit_weights(days, ok)
    D, y = D[ok], y[ok]
    n = len(y)
    p = 1 + len(legs) + (len(legs) if bases_fitted else 0)
    if n <= p:
        raise ValueError(f"need more than {p} bills for a {kind} fit with its bases, have {n}")
    X = np.column_stack([np.ones(n), D])
    beta, sse = _wls(X, y, w)
    slopes = {g: float(b) for g, b in zip(legs, beta[1:])}
    caveats = []
    for g, b in slopes.items():
        if b < 0:
            caveats.append(
                f"{'heating' if g == 'h' else 'cooling'} slope {b:.4g} per degree-day is negative"
            )
    resid = y - X @ beta
    if w is not None:
        resid = np.sqrt(w) * resid
    for g, base_g in (("h", heating_base_f), ("c", cooling_base_f)):
        if g in legs and base_g is None:
            raise ValueError(f"a {kind} fit needs its {'heating' if g == 'h' else 'cooling'} base")
    hb = float(heating_base_f) if "h" in legs and heating_base_f is not None else None
    cb = float(cooling_base_f) if "c" in legs and cooling_base_f is not None else None
    bases = tuple(v for v in (hb, cb) if v is not None)
    names = tuple(f"{_COLS[g]}_per_day" for g in legs)
    model = BillingDegreeDayModel(
        kind=kind,
        heating_base_f=hb,
        cooling_base_f=cb,
        intercept=float(beta[0]),
        heating_slope=slopes.get("h", 0.0),
        cooling_slope=slopes.get("c", 0.0),
        sse=float(sse),
        n=n,
        p=p,
        bases_fitted=bool(bases_fitted),
        caveats=caveats,
    )
    model._fit_record = _safe(
        _linear_fit_record,
        D,
        names,
        ("bdd", kind, bases, len(bases) if bases_fitted else 0),
        s2=sse / (n - p),
        n=n,
        p=p,
        rho=_safe(_rho_of, resid, idx),
        weights=w,
        weight_scale=scale,
    )
    return model


# --------------------------------------------------------------------------- the search


@dataclass(frozen=True)
class BaseSearch:
    """How the bases are searched (the ``mv[].base_search`` block; provisional).

    ``heating`` / ``cooling`` are ``(lo, hi)`` in °F, ``step`` the grid step. ``grid="data"``
    replaces both ranges with the 5th--95th percentile of the baseline's daily mean temperatures,
    widened to whole steps. ``tolerance`` is the likelihood-ratio bound of a base's range,
    ``flat_share`` the share of the grid a range must cover to be called flat, ``fixed_f`` the
    conventional base the fitted result is compared with, ``kinds`` the degree-day kinds tried.
    ``r2_min`` is the R² below which a caveat says weather explains little of the variation
    (default 0.50, the SEP 50001 M&V Protocol 2019 Ed. 2 §6.4.1 model-validity threshold).
    """

    heating: tuple = (40.0, 70.0)
    cooling: tuple = (50.0, 80.0)
    step: float = 1.0
    grid: str = "fixed"
    tolerance: float = _LR_95
    flat_share: float = 0.5
    fixed_f: float = 65.0
    kinds: tuple = DD_KINDS
    r2_min: float = 0.50

    _KEYS = ("heating", "cooling", "step", "grid", "tolerance", "flat_share", "fixed_f", "kinds")

    def __post_init__(self):
        for nm in ("heating", "cooling"):
            v = getattr(self, nm)
            if not (isinstance(v, (list, tuple)) and len(v) == 2 and float(v[0]) < float(v[1])):
                raise ValueError(f"base_search.{nm} must be [lo, hi] with lo < hi, got {v!r}")
            object.__setattr__(self, nm, (float(v[0]), float(v[1])))
        if not float(self.step) > 0:
            raise ValueError("base_search.step must be positive")
        if self.grid not in ("fixed", "data"):
            raise ValueError('base_search.grid must be "fixed" or "data"')
        if not float(self.tolerance) > 0:
            raise ValueError("base_search.tolerance must be positive")
        if not 0 < float(self.flat_share) <= 1:
            raise ValueError("base_search.flat_share must be in (0, 1]")
        kinds = tuple(self.kinds)
        if not kinds or any(k not in DD_KINDS for k in kinds):
            raise ValueError(f"base_search.kinds must be a non-empty subset of {DD_KINDS}")
        object.__setattr__(self, "kinds", kinds)
        if not 0 <= float(self.r2_min) < 1:
            raise ValueError("base_search.r2_min must be in [0, 1)")

    @classmethod
    def from_dict(cls, d: dict | None) -> BaseSearch:
        """A search from the config block (unknown keys are a ``ValueError``)."""
        d = dict(d or {})
        extra = set(d) - set(cls._KEYS) - {"r2_min"}
        if extra:
            raise ValueError(f"base_search: unknown key(s) {sorted(extra)}")
        if "kinds" in d:
            d["kinds"] = tuple(d["kinds"])
        return cls(**d)

    def as_dict(self) -> dict:
        return {
            "heating": list(self.heating),
            "cooling": list(self.cooling),
            "step": float(self.step),
            "grid": self.grid,
            "tolerance": float(self.tolerance),
            "flat_share": float(self.flat_share),
            "fixed_f": float(self.fixed_f),
            "kinds": list(self.kinds),
            "r2_min": float(self.r2_min),
        }

    def grids(self, daily_means=None) -> tuple:
        """``(heating candidates, cooling candidates)`` in °F."""
        st = float(self.step)
        if self.grid == "data":
            t = np.asarray(daily_means, dtype=float)
            t = t[np.isfinite(t)]
            if len(t) < 10:
                raise ValueError("base_search.grid 'data' needs at least 10 days of temperature")
            lo = math.floor(float(np.percentile(t, 5)) / st) * st
            hi = math.ceil(float(np.percentile(t, 95)) / st) * st
            h = c = (lo, hi)
        else:
            h, c = self.heating, self.cooling

        def rng(a, b):
            k = int(round((b - a) / st))
            return np.round(a + st * np.arange(k + 1), 6)

        return rng(*h), rng(*c)


@dataclass
class BaseSelection:
    """The outcome of :func:`select_bases` (provisional).

    ``model`` is the best degree-day model (``None`` when every kind was refused), ``kind`` its
    kind; ``heating_base_f`` / ``cooling_base_f`` the selected bases (a leg the kind lacks is
    ``None``) and ``heating_range`` / ``cooling_range`` their likelihood-ratio ranges.
    ``profiles`` maps ``"heating"`` / ``"cooling"`` to the profile rows of the selected kind (or,
    for a leg it lacks, of the best kind that has it); ``candidates`` has one row per kind tried
    (its best fit, or why it was refused). ``flat`` / ``at_edge`` name the legs affected;
    ``caveats`` say it in words.
    """

    model: BillingDegreeDayModel | None
    kind: str | None
    heating_base_f: float | None
    cooling_base_f: float | None
    heating_range: tuple | None
    cooling_range: tuple | None
    profiles: dict
    candidates: list
    flat: list
    at_edge: list
    grid: dict
    search: BaseSearch
    caveats: list = field(default_factory=list)
    declined_reason: str | None = None

    def as_dict(self) -> dict:
        from .rebaseline import _json_safe

        return _json_safe(
            {
                "kind": self.kind,
                "heating_base_f": self.heating_base_f,
                "cooling_base_f": self.cooling_base_f,
                "heating_range": None if self.heating_range is None else list(self.heating_range),
                "cooling_range": None if self.cooling_range is None else list(self.cooling_range),
                "flat": list(self.flat),
                "at_edge": list(self.at_edge),
                "grid": dict(self.grid),
                "search": self.search.as_dict(),
                "candidates": list(self.candidates),
                "profiles": {k: list(v) for k, v in self.profiles.items()},
                "caveats": list(self.caveats),
                "declined_reason": self.declined_reason,
            }
        )


def _stats_row(y, yhat, p, days, *, cv_max) -> dict:
    from .stats import fit_stats

    st = fit_stats(y, yhat, p, cv_rmse_max=cv_max, weights=days)
    return {
        "r2": st.r2,
        "adj_r2": st.adj_r2,
        "cv_rmse": st.cv_rmse,
        "nmbe": st.nmbe,
    }


def _kind_search(y, days, H, C, hgrid, cgrid, kind, w) -> dict:
    """Weighted SSE and slope-sign validity of one kind over its grid.

    Returns ``{"sse": array, "valid": bool array, "beta": object array}`` with shape
    ``(len(hgrid),)``, ``(len(cgrid),)`` or ``(len(hgrid), len(cgrid))`` by kind.
    """
    n = len(y)
    one = np.ones(n)

    def solve(cols):
        X = np.column_stack([one, *cols])
        if any(np.count_nonzero(c > 0) < 2 for c in cols):
            return None, np.inf  # a degenerate leg: (almost) no degree days at this base
        beta, sse = _wls(X, y, w)
        return beta, sse

    if kind == "DD-H":
        shape: tuple = (len(hgrid),)
    elif kind == "DD-C":
        shape = (len(cgrid),)
    else:
        shape = (len(hgrid), len(cgrid))
    sse = np.full(shape, np.inf)
    valid = np.zeros(shape, bool)
    for ij in np.ndindex(*shape):
        if kind == "DD-H":
            cols = [H[:, ij[0]]]
        elif kind == "DD-C":
            cols = [C[:, ij[0]]]
        else:
            if hgrid[ij[0]] > cgrid[ij[1]]:
                continue  # the heating base above the cooling base: not searched
            cols = [H[:, ij[0]], C[:, ij[1]]]
        beta, s = solve(cols)
        if beta is None:
            continue
        sse[ij] = s
        valid[ij] = bool(np.all(beta[1:] >= 0))
    return {"sse": sse, "valid": valid}


def _range(grid, prof_sse, sse_min, n, tol) -> tuple | None:
    ok = np.isfinite(prof_sse) & (n * np.log(np.maximum(prof_sse, 1e-300) / sse_min) <= tol + 1e-12)
    if not ok.any():
        return None
    return float(grid[ok].min()), float(grid[ok].max())


def select_bases(
    frame: pd.DataFrame,
    temp: pd.Series,
    *,
    search: BaseSearch | None = None,
    cv_rmse_max: float | None = None,
) -> BaseSelection:
    """Choose heating and cooling bases for bills (see the module docstring).

    ``frame`` is the bills frame of :meth:`~camber.mandv.billing.BillingSeries.energy_vs_temp`
    (energy per day, ``start``, ``end``, ``days``), restricted to the baseline; ``temp`` the
    temperature series it was built from. Returns a :class:`BaseSelection`; its ``model`` is fitted
    at the selected bases on the degree days :func:`bill_degree_days` builds.
    """
    from .stats import cv_rmse_max_for

    s = search or BaseSearch()
    cv_max = cv_rmse_max_for("monthly") if cv_rmse_max is None else float(cv_rmse_max)
    y = frame["energy"].to_numpy(float)
    days = frame["days"].to_numpy(float) if "days" in frame.columns else None
    n = len(y)
    from .models import fit_weights

    w, _ = fit_weights(days)
    daily_means = None
    if s.grid == "data":
        vals, day = _hourly_or_daily(temp)
        dm = pd.Series(vals, index=day).groupby(level=0).mean()
        lo, hi = frame["start"].min(), frame["end"].max()
        daily_means = dm.loc[lo : hi - pd.Timedelta(days=1)].to_numpy(float)
    hgrid, cgrid = s.grids(daily_means)
    starts, ends = frame["start"], frame["end"]
    H = bill_degree_days(temp, starts, ends, hgrid, leg="heating")
    C = bill_degree_days(temp, starts, ends, cgrid, leg="cooling")
    grid_info = {
        "heating": [float(hgrid[0]), float(hgrid[-1])],
        "cooling": [float(cgrid[0]), float(cgrid[-1])],
        "step": float(s.step),
        "kind": s.grid,
        "degree_days_from": "each day's temperatures, summed over the bill's days",
    }
    ok_rows = np.isfinite(y) & np.all(np.isfinite(H), axis=1) & np.all(np.isfinite(C), axis=1)
    if not ok_rows.all():
        y, H, C = y[ok_rows], H[ok_rows], C[ok_rows]
        days = None if days is None else days[ok_rows]
        w, _ = fit_weights(days)
        n = len(y)
    candidates: list = []
    best_by_kind: dict = {}
    searches: dict = {}
    for kind in s.kinds:
        legs = _LEGS[kind]
        p = 1 + 2 * len(legs)
        if n <= p:
            candidates.append({"kind": kind, "p": p, "refused": f"only {n} bills for p={p}"})
            continue
        res = _kind_search(y, days, H, C, hgrid, cgrid, kind, w)
        searches[kind] = res
        sse = np.where(res["valid"], res["sse"], np.inf)
        if not np.isfinite(sse).any():
            neg = np.isfinite(res["sse"]).any()
            candidates.append(
                {
                    "kind": kind,
                    "p": p,
                    "refused": (
                        "a slope has the wrong (negative) sign at every candidate base"
                        if neg
                        else "no candidate base gives degree days on enough bills"
                    ),
                }
            )
            continue
        ij = np.unravel_index(int(np.argmin(sse)), sse.shape)
        hb = float(hgrid[ij[0]]) if "h" in legs else None
        cb = float(cgrid[ij[-1]]) if "c" in legs else None
        rows = _rows_at(H, C, hgrid, cgrid, legs, hb, cb)
        m = fit_bill_degree_day(rows, y, kind=kind, heating_base_f=hb, cooling_base_f=cb, days=days)
        row = {
            "kind": kind,
            "p": p,
            "heating_base_f": hb,
            "cooling_base_f": cb,
            "sse": float(m.sse),
            "bic": _bic(m.sse, n, p),
            **_stats_row(y, m.predict(rows), p, days, cv_max=cv_max),
        }
        candidates.append(row)
        best_by_kind[kind] = (row, m, sse)
    if not best_by_kind:
        return BaseSelection(
            None,
            None,
            None,
            None,
            None,
            None,
            {},
            candidates,
            [],
            [],
            grid_info,
            s,
            caveats=["no degree-day kind could be fitted with logical slopes"],
            declined_reason="every degree-day kind was refused ("
            + "; ".join(f"{c['kind']}: {c['refused']}" for c in candidates)
            + ")",
        )
    kind = min(best_by_kind, key=lambda k: best_by_kind[k][0]["bic"])
    row, model, sse = best_by_kind[kind]
    for c in candidates:
        c["selected"] = c.get("kind") == kind
    legs = _LEGS[kind]
    profiles: dict = {}
    ranges: dict = {"h": None, "c": None}
    flat: list = []
    edge: list = []
    caveats: list = []
    for g, leg, grid in (("h", "heating", hgrid), ("c", "cooling", cgrid)):
        # the profile of the leg: from the selected kind, else the best kind that has the leg
        src = kind if g in legs else _best_with_leg(best_by_kind, g)
        if src is None:
            continue
        _r, _m, sse_k = best_by_kind[src]
        prof, arg = _profile(sse_k, src, g)
        p_src = 1 + 2 * len(_LEGS[src])
        rows_out: list = []
        for i, b in enumerate(grid):
            if not np.isfinite(prof[i]):
                rows_out.append({"base_f": float(b), "valid": False})
                continue
            hb_i, cb_i = _bases_at(src, g, i, arg, hgrid, cgrid)
            rws = _rows_at(H, C, hgrid, cgrid, _LEGS[src], hb_i, cb_i)
            mi = fit_bill_degree_day(
                rws, y, kind=src, heating_base_f=hb_i, cooling_base_f=cb_i, days=days
            )
            rows_out.append(
                {
                    "base_f": float(b),
                    "valid": True,
                    "other_base_f": cb_i
                    if g == "h" and src == "DD-HC"
                    else (hb_i if src == "DD-HC" else None),
                    "sse": float(prof[i]),
                    "bic": _bic(float(prof[i]), n, p_src),
                    **_stats_row(y, mi.predict(rws), p_src, days, cv_max=cv_max),
                }
            )
        profiles[leg] = rows_out
        rng = _range(grid, prof, float(np.nanmin(np.where(np.isfinite(prof), prof, np.nan))), n,
                     s.tolerance)  # fmt: skip
        if g in legs:
            ranges[g] = rng
            sel = model.heating_base_f if g == "h" else model.cooling_base_f
            width = float(grid[-1] - grid[0])
            if rng is not None and width > 0 and (rng[1] - rng[0]) >= s.flat_share * width:
                flat.append(leg)
                caveats.append(
                    f"the {leg} base is poorly determined: every base from {rng[0]:g} to "
                    f"{rng[1]:g} F fits within the tolerance of the best ({sel:g} F), "
                    f"{(rng[1] - rng[0]) / width:.0%} of the search range -- the fit statistic is "
                    "flat, so the bills do not pin the base down"
                )
            if sel is not None and (abs(sel - grid[0]) < 1e-9 or abs(sel - grid[-1]) < 1e-9):
                edge.append(leg)
                caveats.append(
                    f"the {leg} base {sel:g} F is at the edge of the search range "
                    f"[{grid[0]:g}, {grid[-1]:g}] F: the best base may lie outside it; widen "
                    f"base_search.{leg}"
                )
    return BaseSelection(
        model,
        kind,
        model.heating_base_f,
        model.cooling_base_f,
        ranges["h"],
        ranges["c"],
        profiles,
        candidates,
        flat,
        edge,
        grid_info,
        s,
        caveats=caveats,
    )


def _best_with_leg(best_by_kind: dict, g: str) -> str | None:
    ks = [k for k in best_by_kind if g in _LEGS[k]]
    return min(ks, key=lambda k: best_by_kind[k][0]["bic"]) if ks else None


def _profile(sse: np.ndarray, kind: str, g: str) -> tuple:
    """``(profile SSE per base of leg g, argmin of the other leg)`` for one kind's grid."""
    if sse.ndim == 1:
        return sse, None
    if g == "h":
        return sse.min(axis=1), sse.argmin(axis=1)
    return sse.min(axis=0), sse.argmin(axis=0)


def _bases_at(kind, g, i, arg, hgrid, cgrid) -> tuple:
    if kind == "DD-H":
        return float(hgrid[i]), None
    if kind == "DD-C":
        return None, float(cgrid[i])
    if g == "h":
        return float(hgrid[i]), float(cgrid[int(arg[i])])
    return float(hgrid[int(arg[i])]), float(cgrid[i])


def _rows_at(H, C, hgrid, cgrid, legs, hb, cb) -> np.ndarray:
    cols = []
    if "h" in legs:
        cols.append(H[:, int(np.argmin(np.abs(hgrid - hb)))])
    if "c" in legs:
        cols.append(C[:, int(np.argmin(np.abs(cgrid - cb)))])
    return np.column_stack(cols)


# --------------------------------------------------------------------------- comparison


def compare_models(
    frame: pd.DataFrame,
    models: list,
    *,
    cv_rmse_max: float | None = None,
    r2_min: float = 0.50,
) -> list:
    """One row per ``(label, model, frame_for_it)``: kind, ``p``, R², adjusted R², CV(RMSE),
    NMBE and BIC on the bills (days-weighted), and ``mismatch`` when R² and CV(RMSE) / NMBE
    disagree (a high R² with a poor CV(RMSE) or NMBE, or the reverse).

    ``models`` holds ``(label, model, rows_frame)`` tuples; each model is judged on its own rows
    (a degree-day model on the frame built at its bases). ``frame`` supplies nothing but is kept
    for the signature's symmetry with :func:`select_bases`.
    """
    from . import _mvform
    from .stats import cv_rmse_max_for, fit_stats

    cv_max = cv_rmse_max_for("monthly") if cv_rmse_max is None else float(cv_rmse_max)
    out = []
    for label, model, fr in models:
        y = fr["energy"].to_numpy(float)
        days = _mvform.row_days(fr)
        p = _mvform.n_params(model)
        st = fit_stats(
            y, model.predict(_mvform.design_rows(fr, model)), p, cv_rmse_max=cv_max, weights=days
        )
        n = int(st.n)
        sse = float(st.rmse) ** 2 * (n - p)
        row: dict = {
            "label": label,
            "model": getattr(model, "kind", type(model).__name__),
            "p": int(p),
            "r2": st.r2,
            "adj_r2": st.adj_r2,
            "cv_rmse": st.cv_rmse,
            "nmbe": st.nmbe,
            "bic": _bic(sse, n, p) if n > 0 else None,
            "g14_accept": bool(st.accept),
        }
        if isinstance(model, BillingDegreeDayModel):
            row["heating_base_f"] = model.heating_base_f
            row["cooling_base_f"] = model.cooling_base_f
        else:
            row["change_points"] = [round(float(t), 2) for t in model.change_points]
        good_cv = np.isfinite(st.cv_rmse) and st.cv_rmse <= cv_max
        good_nmbe = np.isfinite(st.nmbe) and abs(st.nmbe) <= 0.005
        hi_r2 = np.isfinite(st.r2) and st.r2 >= 0.75
        lo_r2 = not (np.isfinite(st.r2) and st.r2 >= r2_min)
        if hi_r2 and not (good_cv and good_nmbe):
            row["mismatch"] = (
                f"R2 {st.r2:.2f} is high, but "
                + ("CV(RMSE) " + f"{st.cv_rmse:.1%} exceeds {cv_max:.0%}" if not good_cv else "")
                + ("; " if not good_cv and not good_nmbe else "")
                + (f"|NMBE| {abs(st.nmbe):.2%} exceeds 0.5%" if not good_nmbe else "")
            )
        elif lo_r2 and good_cv:
            row["mismatch"] = (
                f"R2 {st.r2:.2f} is low while CV(RMSE) {st.cv_rmse:.1%} meets {cv_max:.0%}: "
                "the load varies little, and weather explains little of what varies"
            )
        out.append(row)
    return out
