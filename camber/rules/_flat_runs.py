"""Flat (identical-value) runs of an actuator signal, for :mod:`camber.rules.actuator_stuck_rule`.

A thin wrapper around the sensor-health run finder (:func:`camber.sensorhealth._value_runs`), so the
stuck-actuator rule and the sensor-health stuck read count a run the same way. The signal is first
put on a 0-100 % scale (:func:`camber.units.normalize_percent`) and rounded to ``tol_pct``, so a
position that jitters by a fraction of a percent on a 0.1 % resolution trend still reads as one
value. Private (0.98, #85).
"""

from __future__ import annotations

import pandas as pd

from ..sensorhealth import _value_runs
from ..units import normalize_percent


def percent_signal(series: pd.Series, tol_pct: float = 0.5) -> pd.Series:
    """``series`` as numeric percent (0-100), rounded to the nearest ``tol_pct`` (when > 0)."""
    s = normalize_percent(pd.to_numeric(series, errors="coerce"))
    if tol_pct and tol_pct > 0:
        s = (s / tol_pct).round() * tol_pct
    return s


def flat_runs(series: pd.Series, gate=None, *, tol_pct: float = 0.5) -> pd.DataFrame:
    """Identical-value runs of the percent ``series`` within the ``gate`` samples.

    Returns the :func:`camber.sensorhealth._value_runs` frame (``start``, ``end``, ``n``, ``value``,
    ``hours``). A run breaks wherever the gate goes False, so with an occupied (and fan-on) gate
    each occupied period is judged on its own.
    """
    return _value_runs(percent_signal(series, tol_pct), gate)
