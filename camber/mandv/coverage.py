"""Baseline coverage: is the reporting period inside the conditions the baseline was fitted on?

An M&V baseline is a regression fitted over the baseline period's independent variables (outdoor
temperature, a load driver, a time-of-week schedule). Projected onto reporting conditions it never
saw -- a mild-season baseline applied to a hot summer -- the model extrapolates, and the avoided
energy and its fractional savings uncertainty (FSU) describe a model that is no longer supported by
data. This module measures that **coverage**, discloses it, and grades it into a tier the savings
functions act on:

* ``in_range`` -- the reporting drivers sit inside the baseline's support. Savings numbers are
  exactly what they were before coverage existed.
* ``moderate`` -- some extrapolation. The saving is reported with a caveat giving the share of
  points / energy outside the support and how far beyond it they go; for linear-in-parameters models
  the FSU is widened by the parameter-variance ratio ``k`` (see :func:`_fsu_factor`).
* ``severe`` -- a large share outside, or points far beyond the fitted range. With the default
  :class:`ExtrapolationPolicy` the saving is **declined** (the numeric fields become ``None`` and
  ``declined_reason`` says why); ``ExtrapolationPolicy(decline=False)`` computes it anyway, widened,
  under a "not a defensible saving" caveat.
* ``not_evaluated`` -- the model carries no fit-range information (a duck-typed model, a constant
  model), or there are no finite reporting rows. Numbers are unchanged; a caveat says so.

**Support.** For each driver the baseline's finite values are recorded at fit time. The *support
band* is the ``[q, 1-q]`` order-statistic quantiles (``support_quantile``, default 1%), so one
outlier day does not stretch the range a whole reporting season is judged against; points within
``edge_tolerance`` of the band's width count as covered. Shares are counted against the band;
**distance** is measured beyond the *hard* ``[min, max]`` range and expressed as a fraction of its
width. Both sides count -- extrapolating onto a flat segment of a change-point model is still
outside the data, even though the model's arithmetic happens to be benign there.

**Multivariate.** A multi-driver model is checked per column *and* by leverage: a reporting row
whose leverage ``h = x (X'X)^-1 x'`` exceeds the largest baseline leverage lies outside the joint
region the data spans even when every column is individually in range -- "hidden extrapolation"
(Montgomery, Peck & Vining, *Introduction to Linear Regression Analysis*, on regressor-variable
hull extrapolation). A row outside on either test is outside.

**TOWT.** The coverage unit is (occupancy mode x temperature cell): below the fitted range, each of
the model's temperature segments, above it. A reporting hour is unsupported when its cell holds
fewer than ``min_cell_obs`` baseline hours. Hour-of-week x temperature sparsity is reported for
information and never changes the tier. The TOWT temperature basis is clipped at its breakpoints,
so the model holds its response **flat** beyond the fitted range: the error there is bias, not
parameter variance, so TOWT is flagged but its FSU is never widened.

Every threshold here is a **CAMBER policy choice**, not a value taken from a standard: IPMVP and
ASHRAE Guideline 14 expect the baseline to cover reporting conditions, but neither is quoted for
these numbers. Tune them with :class:`ExtrapolationPolicy`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np

__all__ = [
    "TIERS",
    "ExtrapolationPolicy",
    "Coverage",
    "support_of",
    "assess_coverage",
    "towt_coverage",
]

#: Coverage tiers, mildest first; ``not_evaluated`` means coverage could not be assessed.
TIERS = ("in_range", "moderate", "severe", "not_evaluated")


@dataclass(frozen=True)
class ExtrapolationPolicy:
    """Thresholds for grading baseline coverage (all CAMBER policy choices, not standards).

    ``support_quantile`` -- the support band is the ``[q, 1-q]`` order statistics of the baseline
    driver. ``edge_tolerance`` -- a point within this fraction of the band's width counts as
    covered. ``caveat_share`` / ``caveat_distance`` -- the largest share outside (of points, and of
    baseline-projected energy) and the largest distance beyond the fitted range (as a fraction of
    its width) that still reads as ``in_range``. ``decline_share`` / ``decline_distance`` -- the
    share at or above which, or the distance above which, coverage is ``severe``. ``min_cell_obs``
    -- the fewest baseline hours a TOWT (mode x temperature) cell needs to support a reporting hour.
    ``decline`` -- decline a severe saving (numbers become ``None``) rather than report it.
    ``widen_fsu`` -- widen the FSU of a moderate (or undeclined severe) saving by the parameter
    variance factor ``k`` where the model allows it.
    """

    support_quantile: float = 0.01
    edge_tolerance: float = 0.05
    caveat_share: float = 0.05
    caveat_distance: float = 0.10
    decline_share: float = 0.25
    decline_distance: float = 0.50
    min_cell_obs: int = 20
    decline: bool = True
    widen_fsu: bool = True

    def __post_init__(self):
        if not 0.0 <= self.support_quantile < 0.5:
            raise ValueError("support_quantile must be in [0, 0.5)")
        for name in ("edge_tolerance", "caveat_share", "caveat_distance"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.decline_share < self.caveat_share:
            raise ValueError("decline_share must be >= caveat_share")
        if self.decline_distance < self.caveat_distance:
            raise ValueError("decline_distance must be >= caveat_distance")
        if self.min_cell_obs < 1:
            raise ValueError("min_cell_obs must be >= 1")

    @classmethod
    def from_dict(cls, d: dict | None) -> ExtrapolationPolicy:
        """Build a policy from a (config) dict; unknown keys raise ``ValueError``."""
        d = dict(d or {})
        known = set(cls.__dataclass_fields__)
        bad = sorted(set(d) - known)
        if bad:
            raise ValueError(f"unknown extrapolation policy key(s): {bad}; known: {sorted(known)}")
        return cls(**d)

    def as_dict(self) -> dict:
        """Return the policy as a plain dict."""
        return asdict(self)


@dataclass
class Coverage:
    """How well a baseline covers a set of reporting conditions, graded into a tier.

    Shares are fractions of the evaluated reporting rows (``share_points_outside``) and of their
    baseline-projected energy (``share_energy_outside``; ``None`` when that total is not positive).
    ``max_beyond_low`` / ``max_beyond_high`` are the furthest distances below / above the fitted
    (hard) range, and ``max_beyond_rel`` the larger as a fraction of that range's width, taken from
    whichever variable is furthest out; ``variables`` carries the same per variable (and, for TOWT,
    per occupancy mode). ``n_report`` counts reporting rows supplied and ``n_used`` the rows used
    (a savings function sets it to the rows that entered the savings sum).
    """

    tier: str
    n_report: int
    n_used: int
    n_outside: int = 0
    n_below: int = 0
    n_above: int = 0
    share_points_outside: float | None = None
    share_energy_outside: float | None = None
    max_beyond_low: float | None = None
    max_beyond_high: float | None = None
    max_beyond_rel: float | None = None
    variables: list = field(default_factory=list)
    info: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)
    reason: str | None = None
    policy: dict = field(default_factory=dict)
    _outside: np.ndarray | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Return the coverage as a JSON-serialisable dict (the per-row mask is left out)."""
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}

    def outside_mask(self) -> np.ndarray | None:
        """Per reporting row, whether it lies outside the baseline support (``None`` if unknown)."""
        return None if self._outside is None else self._outside.copy()


# --------------------------------------------------------------------------- fit-time support


@dataclass(frozen=True)
class _TOWTCells:
    """Baseline hours per (occupancy mode x temperature segment) of a fitted TOWT (private)."""

    breakpoints: np.ndarray
    modes: tuple  # ("occupied", "unoccupied") or ("all",)
    counts: np.ndarray  # (n_modes, n_seg) baseline hours per cell
    mode_min: np.ndarray
    mode_max: np.ndarray
    tow_counts: np.ndarray  # (168, n_seg) baseline hours per hour-of-week x segment


@dataclass(frozen=True)
class _FitRecord:
    """Everything a fitted baseline remembers about its own fit -- one private record per model.

    Attached to each model as ``model._fit_record`` (``repr=False``). For a linear-in-parameters
    baseline, ``values`` holds each driver column's finite baseline values, sorted; ``design`` is a
    picklable spec for :func:`_design` and ``xtx_pinv`` the baseline ``pinv(X'X)`` for that design
    (the leverage test and the FSU widening factor use it). A TOWT fit carries its cell counts in
    ``towt`` instead. Deliberately the single place fit-time state lives, so it can grow (residual
    variance, n, rho, serialisation) without scattering private attributes across the models.
    """

    names: tuple = ()
    values: tuple = ()
    design: tuple | None = None
    xtx_pinv: np.ndarray | None = None
    h_max: float | None = None
    towt: _TOWTCells | None = None

    @property
    def linear(self) -> bool:
        """Whether this record describes a linear-in-parameters driver support."""
        return bool(self.values)


def _design(spec: tuple, D: np.ndarray) -> np.ndarray:
    """Design matrix for a support ``spec`` at driver rows ``D`` (n x k)."""
    kind = spec[0]
    if kind == "cp":
        from .models import _design_for

        return _design_for(spec[1], spec[2])(D[:, 0])
    if kind == "affine":
        return np.hstack([np.ones((len(D), 1)), D])
    if kind == "dd":
        from .degreeday import degree_days

        hdd, cdd = degree_days(D[:, 0], spec[2])
        cols = [np.ones(len(D))]
        if spec[1] in ("heating", "both"):
            cols.append(hdd)
        if spec[1] in ("cooling", "both"):
            cols.append(cdd)
        return np.column_stack(cols)
    raise ValueError(f"unknown design spec {spec!r}")


def _as_2d(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim == 0:
        a = a.reshape(1)
    return a[:, None] if a.ndim == 1 else a


def _linear_fit_record(drivers, names, design: tuple | None) -> _FitRecord | None:
    """Record the finite baseline support (and ``pinv(X'X)``) of a fitted linear baseline."""
    D = _as_2d(drivers)
    D = D[np.all(np.isfinite(D), axis=1)]
    if not len(D):
        return None
    values = tuple(np.sort(D[:, j]) for j in range(D.shape[1]))
    A = h_max = None
    if design is not None:
        X = _design(design, D)
        A = np.linalg.pinv(X.T @ X)
        h = np.einsum("ij,jk,ik->i", X, A, X)
        h_max = float(np.max(h)) if len(h) else None
    return _FitRecord(tuple(names), values, design, A, h_max)


def _safe(fn, *args, **kwargs):
    """Call a fit-time support recorder, never letting it break a fit (it returns ``None``)."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 -- coverage must never make a fit fail
        return None


def _band(v: np.ndarray, q: float) -> tuple:
    """``(lo, hi)`` order-statistic quantile band of sorted ``v`` (never interpolated)."""
    n = len(v)
    lo = v[int(math.floor(q * (n - 1)))]
    hi = v[int(math.ceil((1.0 - q) * (n - 1)))]
    return float(lo), float(hi)


def support_of(x, *, quantile: float = 0.01) -> dict:
    """The support of a baseline driver: fitted range and ``[q, 1-q]`` support band.

    Returns ``{"n", "fit_min", "fit_max", "support_lo", "support_hi"}`` over the finite values of
    ``x`` (the values are ``None`` when there are none). The band uses order statistics, so it is
    always one of the observed values.
    """
    v = np.asarray(x, dtype=float).ravel()
    v = np.sort(v[np.isfinite(v)])
    if not len(v):
        return {"n": 0, "fit_min": None, "fit_max": None, "support_lo": None, "support_hi": None}
    lo, hi = _band(v, quantile)
    return {
        "n": int(len(v)),
        "fit_min": float(v[0]),
        "fit_max": float(v[-1]),
        "support_lo": lo,
        "support_hi": hi,
    }


# --------------------------------------------------------------------------- assessment


def _r(x, nd=4):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _fmt(x) -> str:
    if x is None:
        return "n/a"
    return f"{x:.1f}" if abs(x) >= 10 else f"{x:.3g}"


def _not_evaluated(n_report: int, why: str, policy: ExtrapolationPolicy) -> Coverage:
    text = f"Baseline coverage not evaluated: {why}."
    return Coverage(
        tier="not_evaluated",
        n_report=int(n_report),
        n_used=0,
        caveats=[text],
        reason=text,
        policy=policy.as_dict(),
    )


def _column(v: np.ndarray, x: np.ndarray, pol: ExtrapolationPolicy, name: str):
    """Per-column masks + metrics for finite reporting values ``x`` against sorted support ``v``."""
    fmin, fmax = float(v[0]), float(v[-1])
    lo, hi = _band(v, pol.support_quantile)
    width = fmax - fmin
    band_w = hi - lo
    tol = pol.edge_tolerance * (band_w if band_w > 0 else width)
    below = x < lo - tol
    above = x > hi + tol
    b_lo = max(0.0, fmin - float(x.min())) if len(x) else 0.0
    b_hi = max(0.0, float(x.max()) - fmax) if len(x) else 0.0
    zero_conflict = False
    if width > 0:
        rel = max(b_lo, b_hi) / width
    else:
        rel = None
        zero_conflict = bool(b_lo > 0 or b_hi > 0)
        if not zero_conflict:
            rel = 0.0
    var = {
        "name": name,
        "fit_min": _r(fmin),
        "fit_max": _r(fmax),
        "support_lo": _r(lo),
        "support_hi": _r(hi),
        "n_below": int(below.sum()),
        "n_above": int(above.sum()),
        "share_points_outside": _r((below | above).mean()) if len(x) else None,
        "max_beyond_low": _r(b_lo, 3),
        "max_beyond_high": _r(b_hi, 3),
        "max_beyond_rel": _r(rel),
        "zero_width": bool(width == 0),
    }
    return below, above, var, zero_conflict


def _linear_masks(support: _FitRecord, D: np.ndarray, pol: ExtrapolationPolicy, suffix: str = ""):
    """Masks for all-finite driver rows ``D`` against a linear support (plus the leverage test)."""
    n = len(D)
    below = np.zeros(n, bool)
    above = np.zeros(n, bool)
    variables = []
    zero_conflict = False
    for j, v in enumerate(support.values):
        name = (support.names[j] if j < len(support.names) else f"x{j}") + suffix
        b, a, var, zc = _column(v, D[:, j], pol, name)
        below |= b
        above |= a
        variables.append(var)
        zero_conflict |= zc
    outside = below | above
    info: dict = {}
    if D.shape[1] >= 2 and support.xtx_pinv is not None and support.design is not None and n:
        X = _design(support.design, D)
        h = np.einsum("ij,jk,ik->i", X, support.xtx_pinv, X)
        h_max = support.h_max or 0.0
        lev = h > h_max * (1.0 + 1e-9) + 1e-12
        info["n_leverage_outside"] = int(lev.sum())
        info["max_leverage_ratio"] = _r(float(h.max()) / h_max) if h_max > 0 else None
        outside = outside | lev
    return outside, below, above, variables, zero_conflict, info


def _tier(sp, se, rel, zero_conflict, pol: ExtrapolationPolicy) -> str:
    share = max(sp, se if se is not None else 0.0)
    if (
        zero_conflict
        or share >= pol.decline_share
        or (rel is not None and rel > pol.decline_distance)
    ):
        return "severe"
    if share <= pol.caveat_share and (rel is None or rel <= pol.caveat_distance):
        return "in_range"
    return "moderate"


def _describe(cov: Coverage) -> str:
    energy = (
        f" ({cov.share_energy_outside:.1%} of baseline-projected energy)"
        if cov.share_energy_outside is not None
        else ""
    )
    parts = []
    for v in cov.variables:
        if v["n_below"] or v["n_above"] or (v["max_beyond_rel"] or 0) > 0 or v["zero_width"]:
            parts.append(
                f"{v['name']} support {_fmt(v['support_lo'])} to {_fmt(v['support_hi'])}, "
                f"fitted {_fmt(v['fit_min'])} to {_fmt(v['fit_max'])}"
            )
    where = "; ".join(parts[:4]) or "see coverage.variables"
    text = (
        f"{cov.share_points_outside:.1%} of reporting points{energy} lie outside the baseline "
        f"support ({where})"
    )
    if cov.max_beyond_rel is None:
        text += "; the baseline driver never varied, so any other value is outside it"
    elif cov.max_beyond_rel > 0:
        far = max(cov.max_beyond_low or 0.0, cov.max_beyond_high or 0.0)
        text += (
            f"; the furthest is {_fmt(far)} beyond the fitted range "
            f"({cov.max_beyond_rel:.0%} of its width)"
        )
    return text


def _summarise(
    *,
    n_report: int,
    rows: np.ndarray,
    outside: np.ndarray,
    below: np.ndarray,
    above: np.ndarray,
    variables: list,
    projected,
    pol: ExtrapolationPolicy,
    info: dict | None = None,
    caveats: list | None = None,
    zero_conflict: bool = False,
) -> Coverage:
    """Grade row-level masks (aligned to the reporting rows) into a :class:`Coverage`."""
    n_eval = int(rows.sum())
    if n_eval == 0:
        return _not_evaluated(n_report, "no reporting row has finite drivers", pol)
    caveats = list(caveats or [])
    sp = float(outside[rows].sum()) / n_eval
    se = None
    if projected is not None:
        p = np.asarray(projected, dtype=float).ravel()
        if len(p) == len(rows):
            ok = rows & np.isfinite(p)
            tot = float(p[ok].sum())
            if np.isfinite(tot) and tot > 0:
                se = float(p[ok & outside].sum()) / tot
            else:
                caveats.append(
                    "share of energy outside the baseline support not computed: the baseline-"
                    "projected total is not positive"
                )
    # the furthest-out variable supplies the headline distance
    far = None
    for v in variables:
        r = v["max_beyond_rel"]
        if r is not None and (far is None or (far["max_beyond_rel"] or 0) < r):
            far = v
    rel = far["max_beyond_rel"] if far else (None if zero_conflict else 0.0)
    if zero_conflict:
        rel = None
    tier = _tier(sp, se, rel, zero_conflict, pol)
    cov = Coverage(
        tier=tier,
        n_report=int(n_report),
        n_used=n_eval,
        n_outside=int(outside[rows].sum()),
        n_below=int(below[rows].sum()),
        n_above=int(above[rows].sum()),
        share_points_outside=_r(sp),
        share_energy_outside=_r(se),
        max_beyond_low=far["max_beyond_low"] if far else None,
        max_beyond_high=far["max_beyond_high"] if far else None,
        max_beyond_rel=_r(rel),
        variables=variables,
        info=dict(info or {}),
        policy=pol.as_dict(),
        _outside=outside & rows,
    )
    if tier == "moderate":
        cov.reason = "Moderate extrapolation: " + _describe(cov) + "."
    elif tier == "severe":
        cov.reason = "SEVERE extrapolation — not a defensible saving: " + _describe(cov) + "."
    cov.caveats = ([cov.reason] if cov.reason else []) + caveats
    return cov


def _predict_or_none(model, *args):
    try:
        return np.asarray(model.predict(*args), dtype=float)
    except Exception:  # noqa: BLE001 -- the energy share is optional
        return None


def _linear_coverage(model, drivers, *, projected=None, policy=None) -> Coverage:
    """Coverage for a model carrying a linear :class:`_FitRecord` in ``model._fit_record``."""
    pol = policy or ExtrapolationPolicy()
    D = _as_2d(drivers)
    support = getattr(model, "_fit_record", None)
    if not (isinstance(support, _FitRecord) and support.linear):
        return _not_evaluated(len(D), "the baseline model carries no fit-range information", pol)
    if D.shape[1] != len(support.values):
        return _not_evaluated(
            len(D), f"expected {len(support.values)} driver column(s), got {D.shape[1]}", pol
        )
    rows = np.all(np.isfinite(D), axis=1)
    n = len(D)
    outside = np.zeros(n, bool)
    below = np.zeros(n, bool)
    above = np.zeros(n, bool)
    variables: list = []
    zc = False
    info: dict = {}
    if rows.any():
        o, b, a, variables, zc, info = _linear_masks(support, D[rows], pol)
        outside[rows], below[rows], above[rows] = o, b, a
    if projected is None:
        projected = _predict_or_none(model, drivers)
    return _summarise(
        n_report=n,
        rows=rows,
        outside=outside,
        below=below,
        above=above,
        variables=variables,
        projected=projected,
        pol=pol,
        info=info,
        zero_conflict=zc,
    )


def _categorical_coverage(model, T, cat, *, projected=None, policy=None) -> Coverage:
    """Coverage for a :class:`~camber.mandv.categorical.CategoricalModel` (unseen = unsupported)."""
    pol = policy or ExtrapolationPolicy()
    T = np.asarray(T, dtype=float).ravel()
    cat = np.asarray(cat)
    n = len(T)
    rows = np.isfinite(T)
    known = list(model.models)
    unseen = rows & ~np.isin(cat, known)
    outside = unseen.copy()
    below = np.zeros(n, bool)
    above = np.zeros(n, bool)
    variables: list = []
    zc = False
    for c, sub in model.models.items():
        sel = rows & (cat == c)
        if not sel.any():
            continue
        support = getattr(sub, "_fit_record", None)
        if not (isinstance(support, _FitRecord) and support.linear):
            return _not_evaluated(n, f"the model for category {c!r} carries no fit range", pol)
        o, b, a, vs, z, _ = _linear_masks(support, T[sel][:, None], pol, suffix=f"[{c}]")
        outside[sel], below[sel], above[sel] = o, b, a
        variables += vs
        zc |= z
    caveats = []
    info: dict = {"n_unseen_category": int(unseen.sum())}
    if unseen.any():
        names = sorted({str(c) for c in cat[unseen]})
        info["unseen_categories"] = names[:10]
        caveats.append(
            f"{int(unseen.sum())} reporting row(s) fall in categories the baseline never fitted "
            f"({', '.join(names[:5])}); they have no baseline projection and count as unsupported"
        )
    if projected is None:
        projected = _predict_or_none(model, T, cat)
    return _summarise(
        n_report=n,
        rows=rows,
        outside=outside,
        below=below,
        above=above,
        variables=variables,
        projected=projected,
        pol=pol,
        info=info,
        caveats=caveats,
        zero_conflict=zc,
    )


# --------------------------------------------------------------------------- TOWT


def _towt_modes(tow: np.ndarray, occ_bins) -> np.ndarray:
    if occ_bins is None:
        return np.zeros(len(tow), dtype=int)
    occ = np.fromiter((int(b) in occ_bins for b in tow), dtype=bool, count=len(tow))
    return np.where(occ, 0, 1)


def _towt_segments(t: np.ndarray, bps: np.ndarray) -> np.ndarray:
    """Segment index per temperature: -1 below the fitted range, n_seg above, else 0..n_seg-1."""
    n_seg = len(bps) - 1
    seg = np.clip(np.searchsorted(bps, t, side="right") - 1, 0, n_seg - 1)
    seg = np.where(t < bps[0], -1, seg)
    return np.where(t > bps[-1], n_seg, seg)


def _towt_fit_record(tow, temp, breakpoints, occ_bins) -> _FitRecord | None:
    """Record the (mode x temperature cell) support of a TOWT fit."""
    t = np.asarray(temp, dtype=float)
    tow = np.asarray(tow)
    ok = np.isfinite(t)
    t, tow = t[ok], tow[ok]
    if not len(t):
        return None
    bps = np.asarray(breakpoints, dtype=float)
    n_seg = len(bps) - 1
    modes = ("all",) if occ_bins is None else ("occupied", "unoccupied")
    mode = _towt_modes(tow, occ_bins)
    seg = np.clip(_towt_segments(t, bps), 0, n_seg - 1)
    counts = np.zeros((len(modes), n_seg), dtype=int)
    np.add.at(counts, (mode, seg), 1)
    tow_counts = np.zeros((168, n_seg), dtype=int)
    np.add.at(tow_counts, (tow.astype(int) % 168, seg), 1)
    mode_min = np.array(
        [t[mode == i].min() if (mode == i).any() else np.nan for i in range(len(modes))]
    )
    mode_max = np.array(
        [t[mode == i].max() if (mode == i).any() else np.nan for i in range(len(modes))]
    )
    return _FitRecord(towt=_TOWTCells(bps, modes, counts, mode_min, mode_max, tow_counts))


def towt_coverage(model, index, temp, *, projected=None, policy=None) -> Coverage:
    """Coverage of a TOWT baseline over reporting timestamps ``index`` and temperatures ``temp``.

    A reporting hour is unsupported when its (occupancy mode x temperature cell) holds fewer than
    ``policy.min_cell_obs`` baseline hours, or when it lies beyond its mode's fitted temperature
    range; cells below and above the fitted range hold none. Distances are per mode. The share of
    reporting hours whose (hour-of-week x temperature cell) the baseline never saw is reported in
    ``info`` for information only. TOWT is flagged, never widened: its temperature response is held
    flat beyond the fitted range, so the error there is bias rather than parameter variance.
    """
    import pandas as pd

    from .towt import hour_of_week

    pol = policy or ExtrapolationPolicy()
    t = np.asarray(temp, dtype=float).ravel()
    n = len(t)
    rec = getattr(model, "_fit_record", None)
    support = rec.towt if isinstance(rec, _FitRecord) else None
    if support is None:
        return _not_evaluated(n, "the TOWT model carries no fit-range information", pol)
    idx = pd.DatetimeIndex(index)
    if len(idx) != n:
        raise ValueError(f"index has {len(idx)} timestamps but temp has {n} values")
    tow = hour_of_week(idx)
    rows = np.isfinite(t)
    mode = _towt_modes(tow, getattr(model, "occ_bins", None) if len(support.modes) == 2 else None)
    bps = support.breakpoints
    n_seg = len(bps) - 1
    seg = _towt_segments(np.where(rows, t, bps[0]), bps)
    inside = (seg >= 0) & (seg < n_seg)
    segc = np.clip(seg, 0, n_seg - 1)
    cell = np.where(inside, support.counts[mode, segc], 0)
    mmin = support.mode_min[mode]
    mmax = support.mode_max[mode]
    with np.errstate(invalid="ignore"):
        below = rows & ~(t >= mmin)  # also true for a mode never seen at fit (NaN range)
        above = rows & (t > mmax)
    outside = rows & ((cell < pol.min_cell_obs) | below | above)
    variables = []
    for i, name in enumerate(support.modes):
        sel = rows & (mode == i)
        if not sel.any():
            continue
        lo, hi = support.mode_min[i], support.mode_max[i]
        if not np.isfinite(lo):
            variables.append(
                {
                    "name": f"oat[{name}]",
                    "fit_min": None,
                    "fit_max": None,
                    "support_lo": None,
                    "support_hi": None,
                    "n_below": int(sel.sum()),
                    "n_above": 0,
                    "share_points_outside": 1.0,
                    "max_beyond_low": None,
                    "max_beyond_high": None,
                    "max_beyond_rel": None,
                    "zero_width": False,
                }
            )
            continue
        x = t[sel]
        b_lo = max(0.0, lo - float(x.min()))
        b_hi = max(0.0, float(x.max()) - hi)
        width = hi - lo
        variables.append(
            {
                "name": f"oat[{name}]",
                "fit_min": _r(lo),
                "fit_max": _r(hi),
                "support_lo": _r(lo),
                "support_hi": _r(hi),
                "n_below": int((below & sel).sum()),
                "n_above": int((above & sel).sum()),
                "share_points_outside": _r(float(outside[sel].mean())),
                "max_beyond_low": _r(b_lo, 3),
                "max_beyond_high": _r(b_hi, 3),
                "max_beyond_rel": _r(max(b_lo, b_hi) / width) if width > 0 else 0.0,
                "zero_width": bool(width == 0),
            }
        )
    tow_cell = np.where(inside, support.tow_counts[tow % 168, segc], 0)
    n_eval = int(rows.sum())
    beyond_fit = rows & ((t < bps[0]) | (t > bps[-1]))
    info = {
        "unit": "occupancy mode x temperature cell",
        "min_cell_obs": int(pol.min_cell_obs),
        "n_sparse_cell": int((rows & inside & (cell < pol.min_cell_obs)).sum()),
        "n_beyond_fit_range": int(beyond_fit.sum()),
        "tow_temp_unseen_share": _r(float((rows & (tow_cell == 0)).sum()) / n_eval)
        if n_eval
        else None,
        "breakpoints": [_r(b, 3) for b in bps],
    }
    caveats = []
    if beyond_fit.any():
        caveats.append(
            f"TOWT holds its temperature response flat beyond the fitted range "
            f"({_fmt(float(bps[0]))} to {_fmt(float(bps[-1]))}); {int(beyond_fit.sum())} reporting "
            "hour(s) lie there, where the error is bias rather than parameter variance, so the "
            "uncertainty band is not widened to cover it"
        )
    if projected is None:
        projected = _predict_or_none(model, idx, t)
    return _summarise(
        n_report=n,
        rows=rows,
        outside=outside,
        below=below,
        above=above,
        variables=variables,
        projected=projected,
        pol=pol,
        info=info,
        caveats=caveats,
        zero_conflict=False,
    )


# --------------------------------------------------------------------------- dispatch + FSU


def assess_coverage(model, drivers, *, projected=None, policy=None) -> Coverage:
    """Grade how well ``model``'s baseline support covers reporting ``drivers``.

    Direction-agnostic: ``model`` is whichever fitted model is being projected and ``drivers``
    the conditions it is projected onto -- a baseline onto reporting drivers, or equally a
    reporting model back onto baseline drivers (a backcast) or onto a normal year.

    Dispatches to ``model.coverage(drivers, projected=..., policy=...)`` -- implemented by the
    change-point, degree-day, driver (Option B) and bound TOWT / categorical models. A duck-typed
    model with no ``coverage`` method, or one whose coverage cannot be evaluated, returns tier
    ``not_evaluated`` with a caveat; this never raises. ``projected`` (the baseline projection at
    ``drivers``) is used for the energy share and computed via ``model.predict`` when omitted.
    """
    pol = policy or ExtrapolationPolicy()
    try:
        n = len(drivers)
    except TypeError:
        n = 1
    fn = getattr(model, "coverage", None)
    if fn is None:
        return _not_evaluated(n, f"{type(model).__name__} exposes no fit-range information", pol)
    try:
        cov = fn(drivers, projected=projected, policy=pol)
    except Exception as e:  # noqa: BLE001 -- coverage is disclosure; it must not break savings
        return _not_evaluated(n, f"coverage raised {type(e).__name__}: {e}", pol)
    if not isinstance(cov, Coverage):
        return _not_evaluated(n, f"{type(model).__name__}.coverage returned no Coverage", pol)
    return cov


def _fsu_factor(model, drivers_used, *, m: int, projected_kernel: bool, policy) -> float | None:
    """Parameter-variance widening factor ``k`` for a linear-in-parameters baseline, or ``None``.

    With ``s`` the sum of the reporting design rows and ``s_c`` the same with each driver clamped
    into its support band, and ``A = pinv(X'X)`` of the baseline design:

    * measured kernel (avoided energy): ``k = sqrt((m + s'As) / (m + s_c'As_c))``
    * projected kernel (normalized savings): ``k = sqrt(s'As / s_c'As_c)``

    i.e. the ratio of the variance of the projected total at the actual reporting drivers to what
    it would be had they stayed inside the support. It is conditional on the fitted change points
    (they are treated as known), and it is 1 where the extrapolated points sit on a flat segment,
    because clamping them changes nothing there. ``None`` for models without a linear design (TOWT,
    categorical, duck-typed).
    """
    support = getattr(model, "_fit_record", None)
    if not isinstance(support, _FitRecord) or support.xtx_pinv is None or support.design is None:
        return None
    try:
        D = _as_2d(drivers_used)
        D = D[np.all(np.isfinite(D), axis=1)]
        if not len(D) or D.shape[1] != len(support.values):
            return None
        Dc = D.copy()
        for j, v in enumerate(support.values):
            lo, hi = _band(v, policy.support_quantile)
            Dc[:, j] = np.clip(D[:, j], lo, hi)
        A = support.xtx_pinv
        s = _design(support.design, D).sum(axis=0)
        sc = _design(support.design, Dc).sum(axis=0)
        q, qc = float(s @ A @ s), float(sc @ A @ sc)
        if projected_kernel:
            k = math.sqrt(q / qc) if qc > 0 else float("nan")
        else:
            k = math.sqrt((m + q) / (m + qc)) if m + qc > 0 else float("nan")
    except Exception:  # noqa: BLE001
        return None
    return float(k) if np.isfinite(k) else None


def _with_used(cov: Coverage, n_report: int, n_used: int) -> Coverage:
    return replace(cov, n_report=int(n_report), n_used=int(n_used))


def _worst(*tiers: str) -> str:
    for t in ("severe", "moderate", "not_evaluated"):
        if t in tiers:
            return t
    return "in_range"
