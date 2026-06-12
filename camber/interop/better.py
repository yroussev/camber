"""Optional bridge to LBNL's BETTER analytical engine (better-lbnl-os).

BETTER (Building Efficiency Targeting Tool for Energy Retrofits, LBNL) fits change-point
models to monthly energy-vs-temperature and turns them into benchmarking and retrofit
targeting. CAMBER already has its own change-point M&V (:mod:`camber.mandv`), so the
high-value use of BETTER is **cross-validation** -- run both engines on the same monthly
energy + outdoor-temperature series and confirm they agree on the model order, baseload,
and fit -- the same own-it-+-cross-check pattern CAMBER uses with eemeter for CalTRACK.

Optional path -- install the extra (keeps the core dependency-free):

    pip install "camber[better]"      # pulls better-lbnl-os (modified BSD + U.S. DOE clauses)

BETTER is imported lazily. ``fit_changepoint`` wraps its model; ``compare_changepoint``
runs CAMBER and BETTER side by side and reports agreement.
"""

from __future__ import annotations

import numpy as np


def _require_better():
    try:
        from better_lbnl_os import fit_changepoint_model  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            'the LBNL BETTER bridge needs the optional extra: pip install "camber[better]"'
        ) from e
    return fit_changepoint_model


def _attr(obj, name):
    """Read ``name`` from a result that may be an object or a dict."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def fit_changepoint(temps, energy) -> dict:
    """Fit BETTER's change-point model on (outdoor temp, energy); return a normalized dict.

    ``{"model_type", "r_squared", "baseload", "raw"}``. Requires the ``[better]`` extra.
    """
    fit = _require_better()
    res = fit(list(map(float, temps)), list(map(float, energy)))
    return {"model_type": _attr(res, "model_type"),
            "r_squared": _attr(res, "r_squared"),
            "baseload": _attr(res, "baseload"),
            "raw": res}


def compare_changepoint(temps, energy, *, baseload_tol_pct: float = 15.0) -> dict:
    """Cross-validate CAMBER's change-point against BETTER's on the same monthly series.

    Returns each engine's model order, baseload, and R², plus an ``agreement`` block
    (same model order? baseload within ``baseload_tol_pct``?). Use this to corroborate a
    savings baseline before it goes in a report -- two independent engines agreeing is far
    stronger than one. Requires the ``[better]`` extra.
    """
    from ..mandv.models import best_model        # CAMBER's own engine (always available)

    T = np.asarray(temps, dtype=float)
    y = np.asarray(energy, dtype=float)
    cm = best_model(T, y)
    sst = float(((y - y.mean()) ** 2).sum())
    cam = {"kind": cm.kind, "baseload": round(float(cm.coeffs.get("base", float("nan"))), 3),
           "r_squared": round(1.0 - cm.sse / sst, 4) if sst > 0 else float("nan")}

    bet = fit_changepoint(T, y)
    cam_order = "".join(ch for ch in str(cam["kind"]) if ch.isdigit())
    bet_order = "".join(ch for ch in str(bet["model_type"]) if ch.isdigit())
    cb, bb = cam["baseload"], bet["baseload"]
    if cb == cb and bb not in (None,) and bb == bb and max(abs(cb), abs(bb)) > 0:
        base_pct = 100.0 * abs(cb - bb) / max(abs(cb), abs(bb))
    else:
        base_pct = float("nan")
    agreement = {
        "order_match": bool(cam_order and cam_order == bet_order),
        "baseload_pct_diff": round(base_pct, 1) if base_pct == base_pct else float("nan"),
        "baseload_within_tol": bool(base_pct == base_pct and base_pct <= baseload_tol_pct),
    }
    return {"camber": cam, "better": {k: bet[k] for k in ("model_type", "r_squared", "baseload")},
            "agreement": agreement}
