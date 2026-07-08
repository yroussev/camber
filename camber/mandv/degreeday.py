"""Variable-base degree-day M&V baseline (HDD/CDD regression).

The classic weather-normalization baseline: regress period energy on **heating and cooling
degree-days** about a balance point — ``E = base + a·HDD + b·CDD``. It's the simplest defensible
weather model (ASHRAE G14 / IPMVP), a lighter cousin of the change-point models in
:mod:`camber.mandv.models`, and a good fit for monthly-bill M&V where you have average temperature
and energy per period. The balance point is fit by minimizing CV(RMSE) unless supplied.

numpy only; fit statistics reuse :func:`camber.mandv.stats.fit_stats`.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .stats import fit_stats


def degree_days(tavg, balance_point: float):
    """Per-period heating and cooling degree-days about ``balance_point`` (°F): ``(hdd, cdd)``."""
    t = np.asarray(tavg, dtype=float)
    return np.clip(balance_point - t, 0.0, None), np.clip(t - balance_point, 0.0, None)


@dataclass
class DegreeDayModel:
    """A fitted variable-base degree-day baseline: ``E = base + a·HDD + b·CDD``."""

    kind: str                        # "heating" | "cooling" | "both"
    balance_point: float
    base: float                      # weather-independent energy per period
    heating_slope: float             # energy per HDD (0 if kind excludes heating)
    cooling_slope: float             # energy per CDD (0 if kind excludes cooling)
    fit: object                      # camber.mandv.stats.FitStats

    def predict(self, tavg):
        """Predicted energy for period average temperature(s) ``tavg``."""
        hdd, cdd = degree_days(tavg, self.balance_point)
        return self.base + self.heating_slope * hdd + self.cooling_slope * cdd

    def as_dict(self) -> dict:
        d = asdict(self)
        d["fit"] = self.fit.as_dict() if hasattr(self.fit, "as_dict") else self.fit
        return d


def _fit_at(tavg, energy, bp: float, kind: str):
    hdd, cdd = degree_days(tavg, bp)
    cols = [np.ones(len(tavg))]
    idx = {}
    if kind in ("heating", "both"):
        idx["h"] = len(cols); cols.append(hdd)
    if kind in ("cooling", "both"):
        idx["c"] = len(cols); cols.append(cdd)
    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, energy, rcond=None)
    fs = fit_stats(energy, X @ coef, p=X.shape[1])
    base = float(coef[0])
    hs = float(coef[idx["h"]]) if "h" in idx else 0.0
    cs = float(coef[idx["c"]]) if "c" in idx else 0.0
    return base, hs, cs, fs


def fit_degree_day(tavg, energy, *, balance_point: float | None = None,
                   balance_range=(50.0, 70.0), step: float = 1.0,
                   kind: str = "both") -> DegreeDayModel:
    """Fit ``E = base + a·HDD + b·CDD``. Returns a :class:`DegreeDayModel`.

    ``tavg``/``energy`` are per-period average temperature and energy (e.g. monthly). If
    ``balance_point`` is None, it's chosen from ``balance_range`` (stepped by ``step``) by minimum
    CV(RMSE). ``kind`` restricts to ``"heating"``/``"cooling"`` or fits ``"both"`` legs.
    """
    if kind not in ("heating", "cooling", "both"):
        raise ValueError("kind must be 'heating', 'cooling', or 'both'")
    tavg = np.asarray(tavg, dtype=float)
    energy = np.asarray(energy, dtype=float)
    if len(tavg) != len(energy):
        raise ValueError("tavg and energy must be the same length")
    finite = np.isfinite(tavg) & np.isfinite(energy)      # drop NaN/inf pairs (a missing bill month)
    tavg, energy = tavg[finite], energy[finite]
    p = 3 if kind == "both" else 2                        # intercept + one or two slopes
    if len(tavg) <= p:                                    # need > p points, else the fit is degenerate
        raise ValueError(f"need more than {p} finite (tavg, energy) points for kind={kind!r}, "
                         f"have {len(tavg)}")

    candidates = ([float(balance_point)] if balance_point is not None
                  else list(np.arange(balance_range[0], balance_range[1] + 1e-9, step)))
    best = None
    for bp in candidates:
        base, hs, cs, fs = _fit_at(tavg, energy, bp, kind)
        key = fs.cv_rmse if fs.cv_rmse == fs.cv_rmse else float("inf")
        if best is None or key < best[0]:
            best = (key, bp, base, hs, cs, fs)
    _, bp, base, hs, cs, fs = best
    return DegreeDayModel(kind=kind, balance_point=round(float(bp), 2), base=round(base, 3),
                          heating_slope=round(hs, 4), cooling_slope=round(cs, 4), fit=fs)
