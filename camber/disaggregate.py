"""Load disaggregation — split an interval load into baseload, weather, and other.

Where does the energy go? A transparent decomposition of an interval load into three components:

- **baseload** — the always-on floor (a low percentile of the series);
- **weather** — the portion above baseload explained by outdoor-air temperature (heating + cooling
  legs about a balance point);
- **other** — the remainder (occupancy, plug loads, and anything the weather model doesn't explain).

It answers "how much of my load is base vs weather vs schedule?" — the framing behind baseload
reduction, envelope/HVAC targeting, and setback opportunity. Deliberately honest: the weather part
is only what OAT explains, and the rest is labeled *other*, not over-attributed. numpy/pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


def _interval_hours(index) -> float:
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return 1.0
    deltas = np.diff(idx.view("int64")) / 3.6e12
    return float(np.median(deltas)) if len(deltas) else 1.0


@dataclass
class LoadComponents:
    """Energy split of an interval load into baseload / weather / other."""

    total_kwh: float
    baseload_kwh: float
    weather_kwh: float
    other_kwh: float
    baseload_frac: float
    weather_frac: float
    other_frac: float
    balance_point_f: float
    baseload_kw: float

    def as_dict(self) -> dict:
        return asdict(self)


def disaggregate_load(load: pd.Series, oat: pd.Series, *, baseload_pct: float = 5.0,
                      balance_point: float | None = None,
                      balance_range=(50.0, 70.0)) -> LoadComponents:
    """Decompose ``load`` into baseload / weather / other using ``oat``.

    ``baseload_pct`` sets the always-on floor (percentile of load). Above that floor, load is
    regressed on heating/cooling degrees about a balance point (searched over ``balance_range`` for
    best fit unless ``balance_point`` is given); the fitted, non-negative part is the **weather**
    component and the remainder is **other**. Returns energies and fractions.
    """
    df = pd.DataFrame({"load": load, "oat": oat}).dropna()
    if df.empty:
        return LoadComponents(0, 0, 0, 0, float("nan"), float("nan"), float("nan"),
                              float("nan"), 0.0)
    dt = _interval_hours(df.index)
    ld = df["load"].to_numpy(float)
    t = df["oat"].to_numpy(float)
    base_kw = float(np.percentile(ld, baseload_pct))
    excess = np.clip(ld - base_kw, 0.0, None)

    def fit_at(bp):
        cdd = np.clip(t - bp, 0.0, None)
        hdd = np.clip(bp - t, 0.0, None)
        X = np.column_stack([cdd, hdd])
        coef, *_ = np.linalg.lstsq(X, excess, rcond=None)
        pred = np.clip(X @ coef, 0.0, excess)          # non-negative, can't exceed the excess
        sse = float(np.sum((excess - (X @ coef)) ** 2))
        return pred, sse

    if balance_point is not None:
        weather_kw, _ = fit_at(float(balance_point))
        bp = float(balance_point)
    else:
        best = None
        for cand in np.arange(balance_range[0], balance_range[1] + 1e-9, 1.0):
            pred, sse = fit_at(cand)
            if best is None or sse < best[0]:
                best = (sse, cand, pred)
        _, bp, weather_kw = best

    other_kw = np.clip(excess - weather_kw, 0.0, None)
    total_kwh = float(np.sum(ld) * dt)
    # baseload can't exceed the actual load in an interval -> components sum to total exactly
    base_kwh = float(np.sum(np.minimum(ld, base_kw)) * dt)
    weather_kwh = float(np.sum(weather_kw) * dt)
    other_kwh = float(np.sum(other_kw) * dt)
    tot = total_kwh if total_kwh > 0 else float("nan")
    return LoadComponents(
        total_kwh=round(total_kwh, 2), baseload_kwh=round(base_kwh, 2),
        weather_kwh=round(weather_kwh, 2), other_kwh=round(other_kwh, 2),
        baseload_frac=round(base_kwh / tot, 4) if tot == tot else float("nan"),
        weather_frac=round(weather_kwh / tot, 4) if tot == tot else float("nan"),
        other_frac=round(other_kwh / tot, 4) if tot == tot else float("nan"),
        balance_point_f=round(bp, 2), baseload_kw=round(base_kw, 3))
