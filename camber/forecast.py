"""Dependency-light load forecasting + learned-normal anomaly detection.

A forecaster on top of — not replacing — the deterministic core. Two pieces, no ML dependency:

- **`seasonal_forecast`** — a seasonal-naïve shape plus an additive drift correction: predict each
  interval from the same time-of-week in history (the daily/weekly occupancy shape a change-point
  model misses), then add the recent mean residual of (actual − its own slot mean) to follow slow
  drift *without* distorting that shape. Good enough for next-day/next-week load and as an anomaly
  baseline; honest about being a transparent baseline, not a black-box model.
- **`forecast_anomalies`** — flags intervals whose actual deviates from the forecast beyond a
  robust band (the residual's median/MAD), turning "unlike its own recent normal" into an FDD
  signal.

numpy/pandas only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "seasonal_forecast",
    "AnomalyReport",
    "forecast_anomalies",
    "backtest",
]


def _time_of_week(index: pd.DatetimeIndex, freq_hours: float) -> np.ndarray:
    """Bucket each timestamp into a slot within the week (0 .. slots_per_week-1)."""
    idx = pd.DatetimeIndex(index)
    hours = idx.dayofweek * 24 + idx.hour + idx.minute / 60.0
    return np.floor(hours / freq_hours).astype(int)


def seasonal_forecast(
    history: pd.Series, horizon_index: pd.DatetimeIndex, *, drift_window: int = 168
) -> pd.Series:
    """Forecast ``horizon_index`` from ``history``: seasonal-naïve shape + additive drift.

    The **seasonal** term is the mean of history at each target's time-of-week slot (the daily/
    weekly occupancy shape). To follow slow drift without distorting that shape, an **additive
    drift** correction is added: the mean, over the last ``drift_window`` samples, of
    (actual − its own slot mean). This keeps occupied and unoccupied predictions unbiased (unlike a
    global-level blend, which biases them differently). Slots unseen in history fall back to the
    overall mean.
    """
    h = history.dropna()
    if h.empty:
        return pd.Series(index=horizon_index, dtype=float)
    freq_hours = _infer_freq_hours(h.index)
    slots = _time_of_week(h.index, freq_hours)
    slot_mean = pd.Series(h.to_numpy(), index=slots).groupby(level=0).mean()
    overall = float(h.mean())

    # additive drift = recent mean residual of actual vs its slot expectation
    recent = h.iloc[-drift_window:]
    recent_slots = _time_of_week(recent.index, freq_hours)
    recent_expected = np.array([slot_mean.get(s, overall) for s in recent_slots], dtype=float)
    drift = float(np.mean(recent.to_numpy() - recent_expected))

    tgt_slots = _time_of_week(horizon_index, freq_hours)
    seasonal = np.array([slot_mean.get(s, overall) for s in tgt_slots], dtype=float)
    return pd.Series(seasonal + drift, index=horizon_index)


def _infer_freq_hours(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    deltas = np.diff(pd.DatetimeIndex(index).view("int64")) / 3.6e12
    return max(float(np.median(deltas)), 1e-6)


@dataclass
class AnomalyReport:
    """Learned-normal anomalies of an actual series vs its forecast."""

    n: int
    n_anomalies: int
    anomaly_frac: float
    band: float  # ±threshold on the residual (k·robust-σ)
    mae: float  # mean absolute forecast error
    timestamps: list  # anomalous timestamps (ISO strings)

    def as_dict(self) -> dict:
        return asdict(self)


def forecast_anomalies(actual: pd.Series, forecast: pd.Series, *, k: float = 3.5) -> AnomalyReport:
    """Flag intervals where ``actual`` deviates from ``forecast`` beyond ``k`` robust σ.

    The residual (actual − forecast) is scored with a median/MAD band so a few large deviations
    don't inflate the threshold; any residual outside ``±k·σ`` is an anomaly.
    """
    df = pd.DataFrame({"a": actual, "f": forecast}).dropna()
    if df.empty:
        return AnomalyReport(0, 0, float("nan"), float("nan"), float("nan"), [])
    resid = (df["a"] - df["f"]).to_numpy()
    med = float(np.median(resid))
    mad = float(np.median(np.abs(resid - med)))
    sigma = 1.4826 * mad
    band = k * sigma
    if sigma > 0:
        mask = np.abs(resid - med) > band
    else:
        mask = np.zeros(len(resid), dtype=bool)
    ts = [str(t) for t, m in zip(df.index, mask) if m]
    return AnomalyReport(
        n=int(len(df)),
        n_anomalies=int(mask.sum()),
        anomaly_frac=round(float(mask.mean()), 4),
        band=round(band, 4),
        mae=round(float(np.mean(np.abs(resid))), 4),
        timestamps=ts,
    )


def backtest(history: pd.Series, *, test_frac: float = 0.25, **fc_kw) -> dict:
    """Hold out the last ``test_frac`` of ``history``, forecast it, and report accuracy
    (MAE, MAPE, CV(RMSE)) — a quick honesty check on the forecaster for a given series."""
    h = history.dropna()
    n = len(h)
    cut = int(n * (1.0 - test_frac))
    if cut < 4 or cut >= n:
        return {"error": "not enough data to backtest"}
    train, test = h.iloc[:cut], h.iloc[cut:]
    fc = seasonal_forecast(train, test.index, **fc_kw)
    err = (test - fc).dropna()
    if err.empty:
        return {"error": "no overlapping forecast"}
    mae = float(err.abs().mean())
    denom = float(test.reindex(err.index).abs().mean())
    rmse = float(np.sqrt((err**2).mean()))
    ybar = float(test.reindex(err.index).mean())
    return {
        "n_test": int(len(err)),
        "mae": round(mae, 3),
        "mape": round(mae / denom, 4) if denom else float("nan"),
        "cv_rmse": round(rmse / ybar, 4) if ybar else float("nan"),
    }
