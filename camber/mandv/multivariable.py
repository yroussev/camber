"""Change-point + driver multivariable baselines (provisional; issue #21 phase 21c).

A weather-only change-point model (:mod:`camber.mandv.models`) explains energy by outdoor
temperature alone. When energy also follows a **continuously varying** driver -- occupancy,
production, operating hours, a school-day count -- that driver is a *relevant variable* of the
model, not a static factor to be adjusted for afterwards (DOE SEP 50001 M&V Protocol 2019 Ed. 2
§5.4 and the multivariable change-point form of §6.3.2, as summarised in the #21 plan). This
module fits that form::

    E = W(T; change points) beta_w + D gamma

``W`` is the change-point design of one of the usual kinds (2P ... 5P, change points grid-searched
**with the drivers in the design**, so a driver that co-varies with season cannot be absorbed into
the temperature slope), and ``D`` the ``k`` driver columns, entering linearly. The parameter count
is the change-point kind's (change points included) plus ``k``.

The fitted :class:`ChangePointDriverModel` is a first-class CAMBER baseline: it carries the one
private fit record (support per column, ``(X'X)^-1``, ``s2``, ``n``, ``p``, ``rho``), so the
savings functions (``avoided_energy_savings``, ``backcast_savings``, ``normalized_savings``) grade
its coverage per column **and** by leverage, and ``kernel="exact"`` works on it unchanged. Its
drivers are passed as one 2-D array whose first column is outdoor temperature:
``np.column_stack([T, occupancy, ...])``. Change points are treated as known, as elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["ChangePointDriverModel", "fit_cp_driver_model"]


@dataclass
class ChangePointDriverModel:
    """A change-point weather model plus linear driver terms (SEP multivariable form).

    ``kind`` / ``change_points`` are the temperature part's, ``coeffs`` its named coefficients,
    ``driver_names`` / ``driver_coef`` the driver terms. ``predict`` takes an ``(m, 1 + k)`` array
    ``[T, driver_1, ..., driver_k]``.
    """

    kind: str
    coeffs: dict
    change_points: tuple
    driver_names: tuple
    driver_coef: tuple
    sse: float
    n: int
    p: int  # change-point kind's parameters (change points counted) + number of drivers
    fit_range: tuple | None = None  # (min, max) of the fitted temperatures
    _fit_record: object = field(default=None, repr=False, compare=False)

    def _beta(self) -> np.ndarray:
        from .models import _coef_names

        names = _coef_names(self.kind, self.change_points)
        return np.array([float(self.coeffs[nm]) for nm in names] + list(self.driver_coef))

    def predict(self, X):
        """Predicted energy at rows ``[T, driver_1, ..., driver_k]`` (a 2-D array)."""
        D = _drivers_2d(X, len(self.driver_names))
        return _cpd_design(self.kind, self.change_points, D) @ self._beta()

    def coverage(self, X, *, projected=None, policy=None):
        """Coverage of rows ``[T, drivers...]``: per-column support plus the leverage test
        (a :class:`~camber.mandv.coverage.Coverage`; see
        :func:`~camber.mandv.coverage.assess_coverage`)."""
        from .coverage import _linear_coverage

        return _linear_coverage(self, X, projected=projected, policy=policy)

    def as_dict(self) -> dict:
        """A JSON-safe dict from which :meth:`from_dict` rebuilds an identical model."""
        from .coverage import _FitRecord

        rec = self._fit_record
        return {
            "type": "ChangePointDriverModel",
            "kind": self.kind,
            "coeffs": {k: float(v) for k, v in self.coeffs.items()},
            "change_points": [float(c) for c in self.change_points],
            "driver_names": list(self.driver_names),
            "driver_coef": [float(c) for c in self.driver_coef],
            "sse": float(self.sse),
            "n": int(self.n),
            "p": int(self.p),
            "fit_range": None if self.fit_range is None else [float(x) for x in self.fit_range],
            "fit_record": rec.as_dict() if isinstance(rec, _FitRecord) else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChangePointDriverModel:
        """Rebuild a model written by :meth:`as_dict`."""
        from .coverage import _FitRecord

        fr = d.get("fit_range")
        return cls(
            kind=d["kind"],
            coeffs={k: float(v) for k, v in d["coeffs"].items()},
            change_points=tuple(float(c) for c in d.get("change_points") or ()),
            driver_names=tuple(d["driver_names"]),
            driver_coef=tuple(float(c) for c in d["driver_coef"]),
            sse=float(d["sse"]),
            n=int(d["n"]),
            p=int(d["p"]),
            fit_range=None if fr is None else (float(fr[0]), float(fr[1])),
            _fit_record=_FitRecord.from_dict(d.get("fit_record")),
        )


def _drivers_2d(X, k: int) -> np.ndarray:
    D = np.asarray(X, dtype=float)
    if D.ndim == 1:
        D = D.reshape(1, -1) if k and D.size == k + 1 else D[:, None]
    if D.shape[1] != k + 1:
        raise ValueError(f"expected {k + 1} columns [T, {k} driver(s)], got {D.shape[1]}")
    return D


def _cpd_design(kind: str, change_points: tuple, D: np.ndarray) -> np.ndarray:
    """``[W(T), drivers]`` -- the design of a change-point + driver model at rows ``D``."""
    from .models import _design_for

    return np.hstack([_design_for(kind, change_points)(D[:, 0]), D[:, 1:]])


def _fit_kind(T, Dr, y, kind):
    """Best change points for ``kind`` with the drivers in the design; ``(sse, cps, beta)``."""
    from .nonroutine import _cp_candidates

    cps0 = {"2P": ()}.get(kind, (0.0,) if kind in _ONE_CP else (0.0, 1.0))
    best = None
    for cps in _cp_candidates(kind, T, cps0):
        X = _cpd_design(kind, cps, np.column_stack([T, Dr]))
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        r = y - X @ beta
        sse = float(r @ r)
        if best is None or sse < best[0]:
            best = (sse, cps, beta)
    return best


_ONE_CP = {"3PC", "3PH", "3PHZ", "3PCZ", "4P"}


def fit_cp_driver_model(
    T,
    drivers,
    y,
    *,
    kinds=("2P", "3PC", "3PH", "4P", "5P"),
    driver_names=None,
    time_index=None,
) -> ChangePointDriverModel:
    """Fit a change-point + driver model and pick the kind by BIC.

    ``T`` is outdoor temperature, ``drivers`` one driver (1-D) or several (``n x k``), ``y`` the
    energy, all aligned; rows with any non-finite value are dropped. For each change-point
    ``kind`` the change point(s) are grid-searched with the drivers in the design; the kind with
    the lowest BIC (``n ln(SSE/n) + p ln n``, ``p`` counting change points and drivers) wins, as in
    :func:`~camber.mandv.models.best_model`. ``time_index`` (aligned to ``y``) lets the fit record
    the residuals' lag-1 autocorrelation for the exact kernel.
    """
    from .coverage import _linear_fit_record, _safe
    from .models import N_PARAMS, _coef_names, _rho_of

    T = np.asarray(T, dtype=float).ravel()
    Dr = np.asarray(drivers, dtype=float)
    Dr = Dr[:, None] if Dr.ndim == 1 else Dr
    y = np.asarray(y, dtype=float).ravel()
    if not (len(T) == len(Dr) == len(y)):
        raise ValueError("T, drivers and y must be the same length")
    k = Dr.shape[1]
    names = (
        tuple(driver_names)
        if driver_names is not None
        else (("driver",) if k == 1 else tuple(f"driver[{j}]" for j in range(k)))
    )
    if len(names) != k:
        raise ValueError(f"{len(names)} driver names for {k} driver column(s)")
    ok = np.isfinite(T) & np.isfinite(y) & np.all(np.isfinite(Dr), axis=1)
    idx = None if time_index is None else np.asarray(time_index)[ok]
    T, Dr, y = T[ok], Dr[ok], y[ok]
    n = len(y)
    best, best_bic = None, np.inf
    for kind in kinds:
        if kind not in N_PARAMS:
            raise ValueError(f"unknown change-point kind {kind!r}")
        p = N_PARAMS[kind] + k
        if n - p < 1:
            continue
        sse, cps, beta = _fit_kind(T, Dr, y, kind)
        bic = n * np.log(sse / n + 1e-12) + p * np.log(n)
        if bic < best_bic:
            best, best_bic = (kind, sse, cps, beta, p), bic
    if best is None:
        raise ValueError(f"too few rows ({n}) to fit any change-point + {k}-driver model")
    kind, sse, cps, beta, p = best
    cp_names = _coef_names(kind, cps)
    nw = len(cp_names)
    coeffs = {nm: float(b) for nm, b in zip(cp_names, beta[:nw])}
    if kind in ("3PHZ", "3PCZ", "5PZ"):
        coeffs.setdefault("base", 0.0)
    if len(cps) == 1:
        coeffs["Tc"] = float(cps[0])
    elif len(cps) == 2:
        coeffs["Tc_lo"], coeffs["Tc_hi"] = float(cps[0]), float(cps[1])
    D = np.column_stack([T, Dr])
    resid = y - _cpd_design(kind, cps, D) @ beta
    return ChangePointDriverModel(
        kind=kind,
        coeffs=coeffs,
        change_points=tuple(float(c) for c in cps),
        driver_names=names,
        driver_coef=tuple(float(b) for b in beta[nw:]),
        sse=float(sse),
        n=n,
        p=p,
        fit_range=(float(T.min()), float(T.max())),
        _fit_record=_safe(
            _linear_fit_record,
            D,
            ("oat",) + names,
            ("cpd", kind, tuple(float(c) for c in cps)),
            s2=sse / (n - p) if n > p else None,
            n=n,
            p=p,
            rho=_safe(_rho_of, resid, idx),
        ),
    )
