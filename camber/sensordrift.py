"""Sensor bias & drift detection by comparison against a reference series.

A single trend can show a sensor is *stuck* or *out of range* (see
:mod:`camber.sensorhealth`), but it cannot reveal **calibration drift** -- a reading
that is smoothly, plausibly wrong. For that you need an independent reference the
sensor should agree with, and you measure three things in the difference:

- **bias** -- a roughly constant offset (the sensor reads consistently high/low):
  miscalibration,
- **drift** -- a trend in the offset over time (the error grows month over month):
  aging / fouling calibration,
- **tracking** -- how well the two move together at all (correlation): a low
  correlation means the sensor isn't measuring what it claims (swapped, failed, or
  comparing the wrong reference).

The headline use is validating a building's **outdoor-air-temperature (OSA/OAT)
sensor** against an independent weather reference -- NASA POWER, a nearby NOAA station,
or a TMY/EPW series -- which is otherwise impossible to check from the BAS alone. The
engine is generic, though: any sensor vs any reference it should track (a redundant
sensor, a sister unit, a physics-derived estimate).

Reference data is passed in as a pandas Series; CAMBER stays source-agnostic and
dependency-light (you bring the external series from whatever provider you use).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .rules.base import Finding

__all__ = [
    "DriftResult",
    "compare_to_reference",
    "drift_finding",
]


@dataclass
class DriftResult:
    """Bias/drift of a sensor relative to a reference it should track.

    ``drift_per_month`` is ``None`` when drift could not be evaluated (too short an overlap, or too
    few daily baselines) -- per the honesty convention that is "not evaluated", never "no drift",
    and ``caveats`` says why.
    """

    name: str
    n: int  # overlapping samples compared
    bias: float  # median(sensor - reference); + = sensor reads high
    drift_per_month: float | None  # robust slope of the daily-median offset; None = not evaluated
    rmse: float  # root-mean-square difference
    correlation: float  # Pearson r between sensor and reference
    severity: str  # "ok" | "warn" | "fault" | "info"
    verdict: str  # short human label of the issues, most severe first
    summary: str
    span_days: float = float("nan")  # time span of the overlap
    n_drift_days: int = 0  # daily baselines the drift slope was fitted to
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


_SEVERITY_RANK = {"ok": 0, "warn": 1, "fault": 2}
_DAYS_PER_MONTH = 30.44


def _level(mag: float, warn: float, fault: float) -> str:
    return "fault" if mag >= fault else ("warn" if mag >= warn else "ok")


def _theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    """Median of pairwise slopes -- robust to the odd bad day that levers a least-squares fit."""
    i, j = np.triu_indices(len(x), k=1)
    dx = x[j] - x[i]
    ok = dx > 0
    return float(np.median((y[j] - y[i])[ok] / dx[ok])) if ok.any() else float("nan")


def compare_to_reference(
    series: pd.Series,
    reference: pd.Series,
    *,
    name: str = "sensor",
    bias_warn: float = 2.0,  # |bias| at/above this == warn (degF defaults)
    bias_fault: float = 5.0,
    drift_warn: float = 1.0,  # |drift|/month at/above this == warn
    drift_fault: float = 3.0,
    min_correlation: float = 0.7,  # below this the sensor isn't tracking the reference
    min_samples: int = 100,
    min_drift_days: float = 28.0,
    min_drift_baselines: int = 14,
    baseline_hours=None,
) -> DriftResult:
    """Compare ``series`` to a ``reference`` it should track; report bias, drift, fit.

    The two are aligned on their shared timestamps (inner join). Defaults are tuned for
    temperature sensors (degF / degF-per-month); pass thresholds suited to other units.
    A low correlation dominates the verdict -- if the sensor doesn't track the reference
    at all, the bias/drift numbers aren't meaningful.

    **Drift** is a rate, so it is only evaluated over enough time to measure one: the overlap must
    span at least ``min_drift_days`` (default 28 -- a per-month rate from a shorter window is an
    extrapolation) and yield ``min_drift_baselines`` daily baselines. Each day contributes one
    baseline -- the median offset that day, over ``baseline_hours`` only if given (e.g. ``range(1,
    5)`` for a matched unoccupied-night condition on a CO2 sensor) -- and the drift is the
    Theil-Sen slope through them. A per-sample least-squares slope over a few days is dominated by
    the diurnal swing in the offset (a room and an exhaust CO2 sensor disagree by hundreds of ppm
    while occupied), which is how a 7 ppm bias was once reported as "+275/month". When drift is not
    evaluated ``drift_per_month`` is ``None`` and a caveat says why.

    The **verdict** lists every issue at warn or above, most severe first; at equal severity a
    measured bias comes before an extrapolated drift rate.
    """
    a, b = series.dropna().align(reference.dropna(), join="inner")
    a, b = a.dropna(), b.dropna()
    a, b = a.align(b, join="inner")
    n = len(a)
    if n < min_samples:
        return DriftResult(
            name,
            n,
            float("nan"),
            None,
            float("nan"),
            float("nan"),
            "info",
            "insufficient overlap",
            f"{name}: only {n} overlapping samples (< {min_samples})",
            caveats=["too few overlapping samples to compare"],
        )

    caveats: list = []
    diff = a - b
    bias = float(diff.median())
    span_days = float((a.index.max() - a.index.min()).total_seconds() / 86400.0)

    base = diff
    if baseline_hours is not None:
        base = diff[np.isin(diff.index.hour, list(baseline_hours))]
    daily = base.groupby(base.index.normalize()).median().dropna()
    slope: float | None = None
    if span_days < min_drift_days:
        caveats.append(
            f"drift not evaluated: overlap spans {span_days:.1f} days (< {min_drift_days:g}); "
            "a per-month rate from a shorter window is an extrapolation"
        )
    elif len(daily) < min_drift_baselines:
        caveats.append(
            f"drift not evaluated: only {len(daily)} daily baselines (< {min_drift_baselines})"
        )
    else:
        x = (daily.index - daily.index[0]).total_seconds().to_numpy() / (86400.0 * _DAYS_PER_MONTH)
        est = _theil_sen(x, daily.to_numpy(dtype="float64"))
        slope = est if est == est else None
    rmse = float(np.sqrt(np.mean(diff.to_numpy() ** 2)))
    corr = (
        float(np.corrcoef(a.to_numpy(), b.to_numpy())[0, 1])
        if a.std() > 0 and b.std() > 0
        else float("nan")
    )

    untracking = corr == corr and corr < min_correlation
    bias_level = _level(abs(bias), bias_warn, bias_fault)
    drift_level = "ok" if slope is None else _level(abs(slope), drift_warn, drift_fault)
    issues = []  # (rank, tiebreak, label): bias before drift at equal severity
    if bias_level != "ok":
        issues.append((_SEVERITY_RANK[bias_level], 1, f"biased {bias:+.1f}"))
    if drift_level != "ok" and slope is not None:
        issues.append((_SEVERITY_RANK[drift_level], 0, f"drifting {slope:+.1f}/month"))
    issues.sort(reverse=True)

    levels = [bias_level, drift_level] + (["fault"] if untracking else [])
    severity = max(levels, key=_SEVERITY_RANK.__getitem__)
    if untracking:
        verdict = f"not tracking reference (r={corr:.2f})"
    elif issues:
        verdict = "; ".join(label for _, _, label in issues)
    else:
        verdict = "tracks reference"

    drift_txt = "not evaluated" if slope is None else f"{slope:+.2f}/month"
    return DriftResult(
        name=name,
        n=n,
        bias=round(bias, 2),
        drift_per_month=None if slope is None else round(slope, 3),
        rmse=round(rmse, 2),
        correlation=round(corr, 3) if corr == corr else corr,
        severity=severity,
        verdict=verdict,
        summary=(
            f"{name}: bias {bias:+.1f}, drift {drift_txt}, "
            f"RMSE {rmse:.1f}, r={corr:.2f} over {n} samples / {span_days:.0f} days -- {verdict}"
        ),
        span_days=round(span_days, 2),
        n_drift_days=int(len(daily)) if slope is not None else 0,
        caveats=caveats,
    )


def drift_finding(series: pd.Series, reference: pd.Series, equip: str, role, **kwargs) -> Finding:
    """Compare a sensor to a reference and return a :class:`Finding` (role in the name).

    So sensor-drift results flow through the same prioritization / report / triage as
    everything else, e.g. ``rule="sensor_drift:oat"``.
    """
    role_slug = getattr(role, "value", str(role))
    res = compare_to_reference(series, reference, name=role_slug, **kwargs)
    return Finding(
        rule=f"sensor_drift:{role_slug}",
        equip=equip,
        severity=res.severity,
        metrics={
            "bias": res.bias,
            "drift_per_month": res.drift_per_month,
            "rmse": res.rmse,
            "correlation": res.correlation,
            "n": res.n,
        },
        summary=f"{equip} {res.summary}",
        caveats=list(res.caveats),
    )
