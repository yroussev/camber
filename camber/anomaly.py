"""Anomaly ensemble — one unified verdict from several detectors.

Any single anomaly test has a blind spot: a robust point test misses a slow regime shift; a
change-point test misses isolated spikes; both miss a series that's simply full of gaps. This fuses
three signals CAMBER already computes into one :class:`AnomalyResult` with a combined severity:

- **point anomalies** — robust median/MAD outliers of the residual (against a supplied forecast, or
  the series' own robust centre) — the ``camber.forecast`` / learned-normal signal;
- **change points** — level shifts in time (``camber.changedetect``) — did behaviour *step*?;
- **data quality** — coverage / gaps / flatline / duplicates (``camber.ingest.quality``).

Turns "is this series behaving?" into one answer that a rule, a report, or an alert can act on.
numpy/pandas; reuses the canonical MAD z-score and detectors (no new math).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .changedetect import detect_level_shifts
from .ingest.quality import _mad_z, assess

__all__ = [
    "AnomalyResult",
    "detect_anomalies",
]


@dataclass
class AnomalyResult:
    """Combined anomaly verdict for one series."""

    n: int
    n_point_anomalies: int  # robust MAD outliers of the residual
    n_change_points: int  # level shifts in time
    quality_score: float  # ingest.quality composite (1 = clean)
    anomaly_frac: float  # point anomalies / n
    severity: str  # "ok" | "warn" | "fault" (combined)
    change_points: list  # ISO timestamps of level shifts
    point_anomalies: list  # ISO timestamps of point anomalies

    def as_dict(self) -> dict:
        return asdict(self)


def detect_anomalies(
    series: pd.Series,
    *,
    forecast: pd.Series | None = None,
    k: float = 3.5,
    change_z: float = 4.0,
    min_segment: int = 24,
    warn_quality: float = 0.8,
    fault_quality: float = 0.5,
    fault_frac: float = 0.05,
) -> AnomalyResult:
    """Combine point / change-point / data-quality signals into one :class:`AnomalyResult`.

    ``forecast`` (aligned to ``series``) turns the point test into a residual test against expected
    behaviour; without it the residual is against the series' own robust centre. Severity is
    ``fault`` when point anomalies exceed ``fault_frac``, ≥2 change points occur, or quality drops
    below ``fault_quality``; ``warn`` for any single signal (or quality below ``warn_quality``).
    """
    s = series.dropna()
    n = len(s)

    if forecast is not None:
        from .forecast import forecast_anomalies

        rep = forecast_anomalies(series, forecast, k=k)
        point_ts, n_point = list(rep.timestamps), rep.n_anomalies
        denom = rep.n  # the series∩forecast overlap the anomalies are counted over
    elif n >= 3:
        z = np.abs(_mad_z(s.to_numpy(dtype="float64")))
        mask = z > k
        point_ts = [str(t) for t, m in zip(s.index, mask) if m]
        n_point = int(mask.sum())
        denom = n
    else:
        point_ts, n_point, denom = [], 0, n

    shifts = detect_level_shifts(series, z=change_z, min_segment=min_segment)
    q = assess(series)
    frac = n_point / denom if denom else 0.0

    if frac >= fault_frac or len(shifts) >= 2 or q.score < fault_quality:
        severity = "fault"
    elif n_point > 0 or shifts or q.score < warn_quality:
        severity = "warn"
    else:
        severity = "ok"

    return AnomalyResult(
        n=n,
        n_point_anomalies=n_point,
        n_change_points=len(shifts),
        quality_score=q.score,
        anomaly_frac=round(frac, 4),
        severity=severity,
        change_points=[str(sh.at) for sh in shifts],
        point_anomalies=point_ts,
    )
