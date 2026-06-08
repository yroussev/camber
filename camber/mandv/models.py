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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ChangePointModel:
    """A fitted change-point model and its prediction function."""

    kind: str                       # "2P" | "3PC" | "3PH" | "4P" | "5P"
    coeffs: dict                    # named parameters (base, slopes, change points)
    change_points: tuple            # () | (Tc,) | (Tc_lo, Tc_hi)
    sse: float                      # sum of squared residuals at the fit
    n: int                          # number of observations
    _predict: object = field(default=None, repr=False)

    def predict(self, T):
        """Predicted energy for temperature(s) T (scalar or array)."""
        return self._predict(np.asarray(T, dtype=float))


# --- design matrices for each model kind, given change point(s) ---

def _design_2p(T):
    return np.column_stack([np.ones_like(T), T])


def _design_3pc(T, tc):           # cooling: flat then up
    return np.column_stack([np.ones_like(T), np.maximum(0.0, T - tc)])


def _design_3ph(T, tc):           # heating: down then flat
    return np.column_stack([np.ones_like(T), np.maximum(0.0, tc - T)])


def _design_4p(T, tc):            # two slopes meeting at tc
    return np.column_stack([np.ones_like(T),
                            np.minimum(0.0, T - tc),    # left slope (T<tc)
                            np.maximum(0.0, T - tc)])    # right slope (T>tc)


def _design_5p(T, tlo, thi):      # heating below tlo, deadband, cooling above thi
    return np.column_stack([np.ones_like(T),
                            np.maximum(0.0, tlo - T),    # heating arm
                            np.maximum(0.0, T - thi)])    # cooling arm


def _lstsq_sse(X, y):
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return beta, float(resid @ resid)


def _fit_2p(T, y):
    beta, sse = _lstsq_sse(_design_2p(T), y)
    coeffs = {"base": beta[0], "slope": beta[1]}
    return coeffs, sse, (), lambda t: beta[0] + beta[1] * t


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


def _cp_score(beta, X, y, objective):
    resid = y - X @ beta
    if objective == "bias":
        denom = y.sum()
        return abs(resid.sum() / denom) if denom != 0 else abs(resid.sum())
    return float(resid @ resid)            # sse


def _fit_one_cp(T, y, design, name_slopes, objective=None):
    objective = objective or _OBJECTIVE
    best = None
    for tc in _grid(T):
        X = design(T, tc)
        beta, sse = _lstsq_sse(X, y)
        score = _cp_score(beta, X, y, objective)
        if best is None or score < best[0]:
            best = (score, beta, sse, tc)
    _, beta, sse, tc = best
    return beta, sse, tc


def _fit_3pc(T, y, objective=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_3pc, None, objective)
    return ({"base": beta[0], "cool_slope": beta[1], "Tc": tc}, sse, (tc,),
            lambda t: beta[0] + beta[1] * np.maximum(0.0, t - tc))


def _fit_3ph(T, y, objective=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_3ph, None, objective)
    return ({"base": beta[0], "heat_slope": beta[1], "Tc": tc}, sse, (tc,),
            lambda t: beta[0] + beta[1] * np.maximum(0.0, tc - t))


def _fit_3ph_zero(T, y):
    # heating that goes to ZERO above the change point: energy = slope*max(0, tc-T),
    # no intercept (the "heating-to-zero" variant -- gas used only for space heating).
    best = None
    for tc in _grid(T):
        x = np.maximum(0.0, tc - T).reshape(-1, 1)
        beta, sse = _lstsq_sse(x, y)
        if best is None or sse < best[1]:
            best = (beta, sse, tc)
    beta, sse, tc = best
    slope = float(beta[0])
    return ({"base": 0.0, "heat_slope": slope, "Tc": tc}, sse, (tc,),
            lambda t: slope * np.maximum(0.0, tc - t))


def _fit_3pc_zero(T, y):
    # cooling that goes to zero below the change point (the cooling analogue).
    best = None
    for tc in _grid(T):
        x = np.maximum(0.0, T - tc).reshape(-1, 1)
        beta, sse = _lstsq_sse(x, y)
        if best is None or sse < best[1]:
            best = (beta, sse, tc)
    beta, sse, tc = best
    slope = float(beta[0])
    return ({"base": 0.0, "cool_slope": slope, "Tc": tc}, sse, (tc,),
            lambda t: slope * np.maximum(0.0, t - tc))


def _fit_4p(T, y, objective=None):
    beta, sse, tc = _fit_one_cp(T, y, _design_4p, None, objective)
    return ({"base": beta[0], "left_slope": beta[1], "right_slope": beta[2], "Tc": tc},
            sse, (tc,),
            lambda t: beta[0] + beta[1] * np.minimum(0.0, t - tc)
            + beta[2] * np.maximum(0.0, t - tc))


def _fit_5p(T, y):
    grid = _grid(T)
    best = None
    for i, tlo in enumerate(grid):
        for thi in grid[i:]:
            if thi - tlo < (grid[1] - grid[0]):   # keep a real dead-band
                continue
            beta, sse = _lstsq_sse(_design_5p(T, tlo, thi), y)
            if best is None or sse < best[1]:
                best = (beta, sse, tlo, thi)
    if best is None:
        return _fit_2p(T, y)
    beta, sse, tlo, thi = best
    return ({"base": beta[0], "heat_slope": beta[1], "cool_slope": beta[2],
             "Tc_lo": tlo, "Tc_hi": thi}, sse, (tlo, thi),
            lambda t: beta[0] + beta[1] * np.maximum(0.0, tlo - t)
            + beta[2] * np.maximum(0.0, t - thi))


def _fit_5p_zero(T, y):
    # 5P with the dead-band (base) forced to ZERO: heating arm below Tlo, zero
    # between, cooling arm above Thi, no intercept (the heating/cooling-to-zero variant).
    # For weather-only loads (heat + cool, nothing in between), e.g. an all-electric
    # building with no base, or a thermal meter with no standby load.
    grid = _grid(T)
    step = grid[1] - grid[0]
    best = None
    for i, tlo in enumerate(grid):
        for thi in grid[i:]:
            if thi - tlo < step:
                continue
            X = np.column_stack([np.maximum(0.0, tlo - T), np.maximum(0.0, T - thi)])
            beta, sse = _lstsq_sse(X, y)
            if best is None or sse < best[1]:
                best = (beta, sse, tlo, thi)
    if best is None:
        return _fit_2p(T, y)
    beta, sse, tlo, thi = best
    bh, bc = float(beta[0]), float(beta[1])
    return ({"base": 0.0, "heat_slope": bh, "cool_slope": bc,
             "Tc_lo": tlo, "Tc_hi": thi}, sse, (tlo, thi),
            lambda t: bh * np.maximum(0.0, tlo - t) + bc * np.maximum(0.0, t - thi))


_FITTERS = {"2P": _fit_2p, "3PC": _fit_3pc, "3PH": _fit_3ph,
            "3PHZ": _fit_3ph_zero, "3PCZ": _fit_3pc_zero,
            "4P": _fit_4p, "5P": _fit_5p, "5PZ": _fit_5p_zero}

# parameter counts (for fit stats / BIC). Htg-zero / Clg-zero have 2 (slope + Tc);
# 5PZ has 4 (two slopes + two change points, no intercept).
N_PARAMS = {"2P": 2, "3PC": 3, "3PH": 3, "3PHZ": 2, "3PCZ": 2,
            "4P": 4, "5P": 5, "5PZ": 4}


# change-point fitters that accept an `objective` (single-change-point models)
_OBJECTIVE_AWARE = {"3PC", "3PH", "4P"}


def fit_model(T, y, kind: str, *, objective: str = "sse") -> ChangePointModel:
    """Fit one model ``kind`` to (temperature, energy) data.

    ``objective``: "sse" (default, minimize squared error) or "bias" (choose the
    change point that minimizes |Net Determination Bias|, per ASHRAE Guideline 14).
    The bias
    option applies to single-change-point models (3PC/3PH/4P); others ignore it.
    """
    T = np.asarray(T, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(T) & np.isfinite(y)
    T, y = T[mask], y[mask]
    if len(T) < 3:
        raise ValueError("need >=3 finite points to fit")
    if kind in _OBJECTIVE_AWARE:
        coeffs, sse, cps, pred = _FITTERS[kind](T, y, objective=objective)
    else:
        coeffs, sse, cps, pred = _FITTERS[kind](T, y)
    return ChangePointModel(kind=kind, coeffs=coeffs, change_points=cps,
                            sse=sse, n=len(T), _predict=pred)


def best_model(T, y, kinds=("2P", "3PC", "3PH", "4P", "5P")) -> ChangePointModel:
    """Fit several model kinds and return the best by adjusted goodness of fit.

    Selection uses CV(RMSE) penalized for parameters (more parameters must earn
    their keep) -- a simple BIC-like guard against overfitting with 5P. The
    "heating/cooling goes to zero" kinds (3PHZ/3PCZ) are not in the default set
    (they encode a modeling assumption -- no base load -- the caller opts into);
    pass them explicitly via ``kinds`` when appropriate.
    """
    n_params = N_PARAMS
    best, best_score = None, np.inf
    for k in kinds:
        try:
            m = fit_model(T, y, k)
        except Exception:
            continue
        p = n_params[k]
        if m.n - p < 1:
            continue
        # BIC: n*ln(SSE/n) + p*ln(n)
        bic = m.n * np.log(m.sse / m.n + 1e-12) + p * np.log(m.n)
        if bic < best_score:
            best, best_score = m, bic
    if best is None:
        raise ValueError("no model could be fit")
    return best
