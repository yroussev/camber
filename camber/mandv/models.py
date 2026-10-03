"""Change-point inverse models for M&V (ASHRAE Guideline 14 / IPMVP / RP-1050).

Energy use is modeled as a piecewise-linear function of outdoor temperature. The
standard "p-parameter" family:

* **2P** -- straight line: ``E = b0 + b1*T``. (p=2)
* **3P cooling** -- flat below a change point, sloping up above it:
  ``E = b0 + b1*max(0, T - Tc)``. (p=3)  Cooling rises with temperature.
* **3P heating** -- sloping down below a change point, flat above:
  ``E = b0 + b1*max(0, Tc - T)``. (p=3)  Heating rises as it gets colder.
* **4P** -- two slopes meeting at one change point (different sensitivity each
  side). (p=4)
* **5P** -- flat dead-band in the middle with a heating slope below ``Tc_lo`` and
  a cooling slope above ``Tc_hi``. (p=5)  The model for a building that both
  heats in winter and cools in summer -- the mixed-mode signature.

Fitting follows the ASHRAE RP-1050 inverse-model approach: for models with change
point(s), grid-search the change-point temperature(s) over the observed range and,
at each candidate, solve
the segment slopes/intercept by ordinary least squares; keep the change point that
minimizes the sum of squared residuals. numpy-only (no scipy dependency).

**Weighted fits** (0.92, provisional). :func:`fit_model` and :func:`best_model` take optional
``weights`` -- for billing periods, each bill's day count (:mod:`camber.mandv.billing`), since a
bill's per-day energy is the mean of ``days`` daily values and its variance falls as ``1/days``.
Every least-squares solve, the change-point search and the fit record then use weighted least
squares: the weights are normalised to mean 1 over the fitted rows, ``sse`` is the weighted sum of
squared residuals, and the fit record keeps ``(X'WX)^-1`` and the mean raw weight
(``weight_scale``), which :func:`camber.mandv._design.projection_variance` needs to put a
projected bill total's noise in energy units. Equal weights are **neutral**: the fit takes the
unweighted path, byte for byte, and only ``weight_scale`` is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ChangePointModel:
    """A fitted change-point model and its prediction function."""

    kind: str  # "2P" | "3PC" | "3PH" | "4P" | "5P"
    coeffs: dict  # named parameters (base, slopes, change points)
    change_points: tuple  # () | (Tc,) | (Tc_lo, Tc_hi)
    sse: float  # sum of squared residuals at the fit
    n: int  # number of observations
    _predict: object = field(default=None, repr=False)
    # (min, max) of the finite temperatures the model was fitted on; None when built by hand
    fit_range: tuple | None = None
    # the one private fit-time record (support, pinv(X'X), ...; camber.mandv.coverage)
    _fit_record: object = field(default=None, repr=False, compare=False)

    def predict(self, T):
        """Predicted energy for temperature(s) T (scalar or array)."""
        return self._predict(np.asarray(T, dtype=float))

    def as_dict(self) -> dict:
        """A JSON-safe dict from which :meth:`from_dict` rebuilds an identical model.

        Carries the kind, the named coefficients and change points at full precision, the fit's
        ``sse`` / ``n`` / ``fit_range`` and the private fit record (support, ``(X'X)^-1``, ``s2``,
        ``rho``), so a rebuilt model predicts, grades coverage and computes uncertainty exactly
        as the fitted one.
        """
        from .coverage import _FitRecord

        rec = self._fit_record
        return {
            "type": "ChangePointModel",
            "kind": self.kind,
            "coeffs": {k: float(v) for k, v in self.coeffs.items()},
            "change_points": [float(c) for c in self.change_points],
            "sse": float(self.sse),
            "n": int(self.n),
            "fit_range": None if self.fit_range is None else [float(x) for x in self.fit_range],
            "fit_record": rec.as_dict() if isinstance(rec, _FitRecord) else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChangePointModel:
        """Rebuild a model written by :meth:`as_dict`.

        Prediction is rebuilt from the kind and coefficients through the model's design
        (:func:`_design_for`), which reproduces the fitted model's predictions exactly --
        including the zero-intercept kinds and a 5P that fell back to a line.
        """
        from .coverage import _FitRecord

        kind = d["kind"]
        cps = tuple(float(c) for c in d.get("change_points") or ())
        coeffs = {k: float(v) for k, v in d["coeffs"].items()}
        beta = [coeffs[nm] for nm in _coef_names(kind, cps)]
        fr = d.get("fit_range")
        return cls(
            kind=kind,
            coeffs=coeffs,
            change_points=cps,
            sse=float(d["sse"]),
            n=int(d["n"]),
            _predict=_predict_from_design(kind, cps, beta),
            fit_range=None if fr is None else (float(fr[0]), float(fr[1])),
            _fit_record=_FitRecord.from_dict(d.get("fit_record")),
        )

    def coverage(self, T, *, projected=None, policy=None):
        """How well the fitted temperature range covers ``T``.

        Returns a :class:`~camber.mandv.coverage.Coverage`; see
        :func:`~camber.mandv.coverage.assess_coverage`.
        """
        from .coverage import _linear_coverage

        return _linear_coverage(self, T, projected=projected, policy=policy)


# --- design matrices for each model kind, given change point(s) ---


def _design_2p(T):
    return np.column_stack([np.ones_like(T), T])


def _design_3pc(T, tc):  # cooling: flat then up
    return np.column_stack([np.ones_like(T), np.maximum(0.0, T - tc)])


def _design_3ph(T, tc):  # heating: down then flat
    return np.column_stack([np.ones_like(T), np.maximum(0.0, tc - T)])


def _design_4p(T, tc):  # two slopes meeting at tc
    return np.column_stack(
        [
            np.ones_like(T),
            np.minimum(0.0, T - tc),  # left slope (T<tc)
            np.maximum(0.0, T - tc),
        ]
    )  # right slope (T>tc)


def _design_5p(T, tlo, thi):  # heating below tlo, deadband, cooling above thi
    return np.column_stack(
        [
            np.ones_like(T),
            np.maximum(0.0, tlo - T),  # heating arm
            np.maximum(0.0, T - thi),
        ]
    )  # cooling arm


def _design_for(kind: str, change_points: tuple):
    """The least-squares design ``T -> X`` of a fitted model, given its change point(s).

    Keyed on the number of change points, not ``kind``: the 5P fitters fall back to a 2P line
    (keeping ``kind="5P"`` with ``change_points=()``) when no dead-band fits. Zero-intercept kinds
    have no constant column. ``X @ beta`` reproduces :meth:`ChangePointModel.predict`.
    """
    cps = tuple(float(c) for c in change_points)
    if not cps:
        return _design_2p
    if len(cps) == 1:
        tc = cps[0]
        if kind == "3PC":
            return lambda T: _design_3pc(T, tc)
        if kind == "3PH":
            return lambda T: _design_3ph(T, tc)
        if kind == "3PHZ":
            return lambda T: np.maximum(0.0, tc - T).reshape(-1, 1)
        if kind == "3PCZ":
            return lambda T: np.maximum(0.0, T - tc).reshape(-1, 1)
        if kind == "4P":
            return lambda T: _design_4p(T, tc)
    if len(cps) == 2:
        tlo, thi = cps
        if kind == "5P":
            return lambda T: _design_5p(T, tlo, thi)
        if kind == "5PZ":
            return lambda T: np.column_stack([np.maximum(0.0, tlo - T), np.maximum(0.0, T - thi)])
    raise ValueError(f"no design for kind {kind!r} with change points {cps}")


def _coef_names(kind: str, change_points: tuple) -> tuple:
    """Coefficient names in design-column order (:func:`_design_for`) for a kind and its CPs."""
    if not change_points:  # 2P, and a 5P / 5PZ that fell back to a line
        return ("base", "slope")
    names = {
        "3PC": ("base", "cool_slope"),
        "3PH": ("base", "heat_slope"),
        "3PHZ": ("heat_slope",),
        "3PCZ": ("cool_slope",),
        "4P": ("base", "left_slope", "right_slope"),
        "5P": ("base", "heat_slope", "cool_slope"),
        "5PZ": ("heat_slope", "cool_slope"),
    }
    if kind not in names:
        raise ValueError(f"unknown change-point kind {kind!r}")
    return names[kind]


def _predict_from_design(kind: str, change_points: tuple, beta):
    """``T -> X(T) beta``, accumulated column by column in the fitters' own order (so the rebuilt
    prediction is bit-identical to the fitted closure, not merely close)."""
    design = _design_for(kind, change_points)
    b = [float(v) for v in beta]

    def predict(T):
        t = np.asarray(T, dtype=float)
        X = design(np.atleast_1d(t))
        out = X[:, 0] * b[0]
        for j in range(1, len(b)):
            out = out + X[:, j] * b[j]
        return out[0] if t.ndim == 0 else out.reshape(t.shape)

    return predict


def _lstsq_sse(X, y, w=None):
    if w is None:
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        return beta, float(resid @ resid)
    sw = np.sqrt(w)
    beta, _, _, _ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    resid = y - X @ beta
    return beta, float((w * resid) @ resid)


def fit_weights(weights, mask=None) -> tuple:
    """``(w, scale)``: fit weights normalised to mean 1, and their mean raw value (provisional).

    ``w`` is ``None`` when every weight is equal -- the neutral case, which then takes the
    unweighted least-squares path exactly -- or when ``weights`` is ``None`` (``scale`` is then
    ``None`` too). Non-positive or non-finite weights raise ``ValueError``; ``mask`` selects the
    fitted rows first.
    """
    if weights is None:
        return None, None
    w = np.asarray(weights, dtype=float).ravel()
    if mask is not None:
        w = w[np.asarray(mask, dtype=bool)]
    if not len(w):
        return None, None
    if not np.all(np.isfinite(w)) or np.any(w <= 0):
        raise ValueError("weights must be finite and positive")
    scale = float(w.mean())
    if np.all(w == w[0]):
        return None, scale
    return w / scale, scale


def _fit_2p(T, y, w=None):
    beta, sse = _lstsq_sse(_design_2p(T), y, w)
    coeffs = {"base": beta[0], "slope": beta[1]}
    return coeffs, sse, (), lambda t: beta[0] + beta[1] * t


# Grid-search tie-break (0.98). On a flat objective surface (a change point in a stretch with no
# data, a degenerate 4P/5P on data with no second regime) several grid points give the same SSE
# up to rounding, and which one is smallest by ~1e-15 depends on the BLAS build (Accelerate vs
# OpenBLAS), so a strict ``<`` let the build pick the reported change point. A grid point now
# replaces the best only when it improves the objective by more than this relative margin:
# exact and near ties keep the first grid point (the lowest change point) on every build.
_TIE_REL = 1e-10
# BIC ties between candidate kinds (n*ln(SSE/n) is noise-free to ~1e-12 at these sizes): the
# earlier kind in ``kinds`` wins.
_BIC_TIE = 1e-9


def _tie_floor(y, w=None) -> float:
    """The SSE scale under which differences are rounding: 1e-12 of the (weighted) sum of y^2.

    Floors :func:`_improves` for an exact fit (best SSE ~ 0), where a relative margin is
    itself noise."""
    yy = float(y @ y) if w is None else float((w * y) @ y)
    return 1e-12 * yy if np.isfinite(yy) else 0.0


def _improves(new: float, best: float, floor: float = 0.0) -> bool:
    """Whether ``new`` beats ``best`` by more than the tie margin (see ``_TIE_REL``)."""
    return new < best - _TIE_REL * max(abs(best), floor)


def _grid(T, n=40):
    lo, hi = np.percentile(T, 5), np.percentile(T, 95)
    if hi <= lo:
        lo, hi = T.min(), T.max()
    return np.linspace(lo, hi, n)


# Change-point search objective. "sse" minimizes squared error (the default, RP-1050
# practice); "bias" minimizes |Net Determination Bias| = |sum(resid)/sum(y)| so the
# selected change point yields an overall-unbiased model (an objective recognized by
# ASHRAE Guideline 14). The fit at
# each candidate is always least-squares; only which change point is kept differs.
_OBJECTIVE = "sse"


def _cp_score(beta, X, y, objective, w=None):
    resid = y - X @ beta
    if w is not None:  # weighted: the bias of the weighted totals, the weighted SSE
        if objective == "bias":
            denom = (w * y).sum()
            return abs((w * resid).sum() / denom) if denom != 0 else abs((w * resid).sum())
        return float((w * resid) @ resid)
    if objective == "bias":
        denom = y.sum()
        return abs(resid.sum() / denom) if denom != 0 else abs(resid.sum())
    return float(resid @ resid)  # sse


def _fit_one_cp(T, y, design, name_slopes, objective=None, w=None):
    objective = objective or _OBJECTIVE
    # the bias objective is a dimensionless fraction; the SSE one is on y's squared scale
    floor = 1e-12 if objective == "bias" else _tie_floor(y, w)
    best = None
    for tc in _grid(T):
        X = design(T, tc)
        beta, sse = _lstsq_sse(X, y, w)
        score = _cp_score(beta, X, y, objective, w)
        if best is None or _improves(score, best[0], floor):
            best = (score, beta, sse, tc)
    _, beta, sse, tc = best
    return beta, sse, tc


def _fit_3pc(T, y, objective=None, w=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_3pc, None, objective, w)
    return (
        {"base": beta[0], "cool_slope": beta[1], "Tc": tc},
        sse,
        (tc,),
        lambda t: beta[0] + beta[1] * np.maximum(0.0, t - tc),
    )


def _fit_3ph(T, y, objective=None, w=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_3ph, None, objective, w)
    return (
        {"base": beta[0], "heat_slope": beta[1], "Tc": tc},
        sse,
        (tc,),
        lambda t: beta[0] + beta[1] * np.maximum(0.0, tc - t),
    )


def _fit_3ph_zero(T, y, w=None):
    # heating that goes to ZERO above the change point: energy = slope*max(0, tc-T),
    # no intercept (the "heating-to-zero" variant -- gas used only for space heating).
    best = None
    floor = _tie_floor(y, w)
    for tc in _grid(T):
        x = np.maximum(0.0, tc - T).reshape(-1, 1)
        beta, sse = _lstsq_sse(x, y, w)
        if best is None or _improves(sse, best[1], floor):
            best = (beta, sse, tc)
    beta, sse, tc = best
    slope = float(beta[0])
    return (
        {"base": 0.0, "heat_slope": slope, "Tc": tc},
        sse,
        (tc,),
        lambda t: slope * np.maximum(0.0, tc - t),
    )


def _fit_3pc_zero(T, y, w=None):
    # cooling that goes to zero below the change point (the cooling analogue).
    best = None
    floor = _tie_floor(y, w)
    for tc in _grid(T):
        x = np.maximum(0.0, T - tc).reshape(-1, 1)
        beta, sse = _lstsq_sse(x, y, w)
        if best is None or _improves(sse, best[1], floor):
            best = (beta, sse, tc)
    beta, sse, tc = best
    slope = float(beta[0])
    return (
        {"base": 0.0, "cool_slope": slope, "Tc": tc},
        sse,
        (tc,),
        lambda t: slope * np.maximum(0.0, t - tc),
    )


def _fit_4p(T, y, objective=None, w=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_4p, None, objective, w)
    return (
        {"base": beta[0], "left_slope": beta[1], "right_slope": beta[2], "Tc": tc},
        sse,
        (tc,),
        lambda t: beta[0] + beta[1] * np.minimum(0.0, t - tc) + beta[2] * np.maximum(0.0, t - tc),
    )


def _fit_5p(T, y, w=None):
    grid = _grid(T)
    best = None
    floor = _tie_floor(y, w)
    for i, tlo in enumerate(grid):
        for thi in grid[i:]:
            if thi - tlo < (grid[1] - grid[0]):  # keep a real dead-band
                continue
            beta, sse = _lstsq_sse(_design_5p(T, tlo, thi), y, w)
            if best is None or _improves(sse, best[1], floor):
                best = (beta, sse, tlo, thi)
    if best is None:
        return _fit_2p(T, y) if w is None else _fit_2p(T, y, w)
    beta, sse, tlo, thi = best
    return (
        {"base": beta[0], "heat_slope": beta[1], "cool_slope": beta[2], "Tc_lo": tlo, "Tc_hi": thi},
        sse,
        (tlo, thi),
        lambda t: beta[0] + beta[1] * np.maximum(0.0, tlo - t) + beta[2] * np.maximum(0.0, t - thi),
    )


def _fit_5p_zero(T, y, w=None):
    # 5P with the dead-band (base) forced to ZERO: heating arm below Tlo, zero
    # between, cooling arm above Thi, no intercept (the heating/cooling-to-zero variant).
    # For weather-only loads (heat + cool, nothing in between), e.g. an all-electric
    # building with no base, or a thermal meter with no standby load.
    grid = _grid(T)
    step = grid[1] - grid[0]
    best = None
    floor = _tie_floor(y, w)
    for i, tlo in enumerate(grid):
        for thi in grid[i:]:
            if thi - tlo < step:
                continue
            X = np.column_stack([np.maximum(0.0, tlo - T), np.maximum(0.0, T - thi)])
            beta, sse = _lstsq_sse(X, y, w)
            if best is None or _improves(sse, best[1], floor):
                best = (beta, sse, tlo, thi)
    if best is None:
        return _fit_2p(T, y) if w is None else _fit_2p(T, y, w)
    beta, sse, tlo, thi = best
    bh, bc = float(beta[0]), float(beta[1])
    return (
        {"base": 0.0, "heat_slope": bh, "cool_slope": bc, "Tc_lo": tlo, "Tc_hi": thi},
        sse,
        (tlo, thi),
        lambda t: bh * np.maximum(0.0, tlo - t) + bc * np.maximum(0.0, t - thi),
    )


_FITTERS = {
    "2P": _fit_2p,
    "3PC": _fit_3pc,
    "3PH": _fit_3ph,
    "3PHZ": _fit_3ph_zero,
    "3PCZ": _fit_3pc_zero,
    "4P": _fit_4p,
    "5P": _fit_5p,
    "5PZ": _fit_5p_zero,
}

# parameter counts (for fit stats / BIC). Htg-zero / Clg-zero have 2 (slope + Tc);
# 5PZ has 4 (two slopes + two change points, no intercept).
N_PARAMS = {"2P": 2, "3PC": 3, "3PH": 3, "3PHZ": 2, "3PCZ": 2, "4P": 4, "5P": 5, "5PZ": 4}


# change-point fitters that accept an `objective` (single-change-point models)
_OBJECTIVE_AWARE = {"3PC", "3PH", "4P"}


def fit_model(
    T, y, kind: str, *, objective: str = "sse", time_index=None, weights=None
) -> ChangePointModel:
    """Fit one model ``kind`` to (temperature, energy) data.

    ``objective``: "sse" (default, minimize squared error) or "bias" (choose the
    change point that minimizes |Net Determination Bias|, per ASHRAE Guideline 14).
    The bias
    option applies to single-change-point models (3PC/3PH/4P); others ignore it.

    ``time_index`` (aligned to ``T``) lets the fit record the residuals' lag-1 autocorrelation,
    which the exact uncertainty kernel uses when no ``rho`` is passed to it.

    ``weights`` (aligned to ``T``; provisional, 0.92) makes it a weighted least-squares fit --
    for bills, their day counts (see the module docstring). Equal weights are neutral.
    """
    T = np.asarray(T, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(T) & np.isfinite(y)
    idx = None if time_index is None else np.asarray(time_index)[mask]
    T, y = T[mask], y[mask]
    if len(T) < 3:
        raise ValueError("need >=3 finite points to fit")
    w, scale = fit_weights(weights, mask)
    kw = {} if w is None else {"w": w}
    fitter: Any = _FITTERS[kind]  # the fitters' signatures differ; the union hides that from mypy
    if kind in _OBJECTIVE_AWARE:
        res = fitter(T, y, objective=objective, **kw)
    else:
        res = fitter(T, y, **kw)
    coeffs, sse, cps, pred = res
    from .coverage import _linear_fit_record, _safe

    n, p = len(T), N_PARAMS[kind]
    resid = y - np.asarray(pred(T), dtype=float)
    if w is not None:
        resid = np.sqrt(w) * resid  # standardised: equal variance, for rho
    return ChangePointModel(
        kind=kind,
        coeffs=coeffs,
        change_points=cps,
        sse=sse,
        n=n,
        _predict=pred,
        fit_range=(float(T.min()), float(T.max())),
        _fit_record=_safe(
            _linear_fit_record,
            T,
            ("oat",),
            ("cp", kind, tuple(cps)),
            s2=sse / (n - p) if n > p else None,
            n=n,
            p=p,
            rho=_safe(_rho_of, resid, idx),
            weights=w,
            weight_scale=scale,
        ),
    )


def _rho_of(resid, index):
    """Lag-1 residual autocorrelation when a time index is known, else ``None``."""
    if index is None:
        return None
    from .stats import lag1_autocorrelation

    return lag1_autocorrelation(resid, index=index)


def best_model(
    T, y, kinds=("2P", "3PC", "3PH", "4P", "5P"), *, time_index=None, weights=None
) -> ChangePointModel:
    """Fit several model kinds and return the best by adjusted goodness of fit.

    Selection uses CV(RMSE) penalized for parameters (more parameters must earn
    their keep) -- a simple BIC-like guard against overfitting with 5P. The
    "heating/cooling goes to zero" kinds (3PHZ/3PCZ) are not in the default set
    (they encode a modeling assumption -- no base load -- the caller opts into);
    pass them explicitly via ``kinds`` when appropriate. ``time_index`` and ``weights`` are
    passed to :func:`fit_model` (with weights the BIC uses the weighted SSE).
    """
    n_params = N_PARAMS
    best, best_score = None, np.inf
    for k in kinds:
        try:
            m = fit_model(T, y, k, time_index=time_index, weights=weights)
        except Exception:
            continue
        p = n_params[k]
        if m.n - p < 1:
            continue
        # BIC: n*ln(SSE/n) + p*ln(n)
        bic = m.n * np.log(m.sse / m.n + 1e-12) + p * np.log(m.n)
        if bic < best_score - _BIC_TIE:  # inf - tie is inf: the first finite kind is taken
            best, best_score = m, bic
    if best is None:
        raise ValueError("no model could be fit")
    return best
