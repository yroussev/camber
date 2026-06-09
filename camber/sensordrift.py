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

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .rules.base import Finding


@dataclass
class DriftResult:
    """Bias/drift of a sensor relative to a reference it should track."""

    name: str
    n: int                        # overlapping samples compared
    bias: float                   # median(sensor - reference); + = sensor reads high
    drift_per_month: float        # slope of (sensor - reference) over time
    rmse: float                   # root-mean-square difference
    correlation: float            # Pearson r between sensor and reference
    severity: str                 # "ok" | "warn" | "fault" | "info"
    verdict: str                  # short human label of the dominant issue
    summary: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def compare_to_reference(
    series: pd.Series,
    reference: pd.Series,
    *,
    name: str = "sensor",
    bias_warn: float = 2.0,       # |bias| at/above this == warn (degF defaults)
    bias_fault: float = 5.0,
    drift_warn: float = 1.0,      # |drift|/month at/above this == warn
    drift_fault: float = 3.0,
    min_correlation: float = 0.7,  # below this the sensor isn't tracking the reference
    min_samples: int = 100,
) -> DriftResult:
    """Compare ``series`` to a ``reference`` it should track; report bias, drift, fit.

    The two are aligned on their shared timestamps (inner join). Defaults are tuned for
    temperature sensors (degF / degF-per-month); pass thresholds suited to other units.
    A low correlation dominates the verdict -- if the sensor doesn't track the reference
    at all, the bias/drift numbers aren't meaningful.
    """
    a, b = series.dropna().align(reference.dropna(), join="inner")
    a, b = a.dropna(), b.dropna()
    a, b = a.align(b, join="inner")
    n = len(a)
    if n < min_samples:
        return DriftResult(name, n, float("nan"), float("nan"), float("nan"),
                           float("nan"), "info", "insufficient overlap",
                           f"{name}: only {n} overlapping samples (< {min_samples})")

    diff = (a - b)
    bias = float(diff.median())
    months = (a.index - a.index[0]).total_seconds().to_numpy() / (86400.0 * 30.44)
    if np.std(months) > 0:
        slope = float(np.polyfit(months, diff.to_numpy(), 1)[0])
    else:
        slope = float("nan")
    rmse = float(np.sqrt(np.mean(diff.to_numpy() ** 2)))
    corr = (float(np.corrcoef(a.to_numpy(), b.to_numpy())[0, 1])
            if a.std() > 0 and b.std() > 0 else float("nan"))

    untracking = corr == corr and corr < min_correlation
    drift_mag = abs(slope) if slope == slope else 0.0
    if untracking or abs(bias) >= bias_fault or drift_mag >= drift_fault:
        severity = "fault"
    elif abs(bias) >= bias_warn or drift_mag >= drift_warn:
        severity = "warn"
    else:
        severity = "ok"

    if untracking:
        verdict = f"not tracking reference (r={corr:.2f})"
    elif drift_mag >= drift_warn:
        verdict = f"drifting {slope:+.1f}/month"
    elif abs(bias) >= bias_warn:
        verdict = f"biased {bias:+.1f}"
    else:
        verdict = "tracks reference"

    return DriftResult(
        name=name, n=n,
        bias=round(bias, 2), drift_per_month=round(slope, 3) if slope == slope else slope,
        rmse=round(rmse, 2), correlation=round(corr, 3) if corr == corr else corr,
        severity=severity, verdict=verdict,
        summary=(f"{name}: bias {bias:+.1f}, drift {slope:+.2f}/month, "
                 f"RMSE {rmse:.1f}, r={corr:.2f} over {n} samples -- {verdict}"),
    )


def drift_finding(series: pd.Series, reference: pd.Series, equip: str, role,
                  **kwargs) -> Finding:
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
        metrics={"bias": res.bias, "drift_per_month": res.drift_per_month,
                 "rmse": res.rmse, "correlation": res.correlation, "n": res.n},
        summary=f"{equip} {res.summary}",
    )
