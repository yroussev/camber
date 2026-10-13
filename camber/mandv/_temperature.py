"""The plausibility screen every M&V baseline fit applies to its temperatures (0.105, #159).

A weather or BAS export sometimes writes a placeholder in place of a missing outdoor temperature:
a large negative sentinel, far below any real outdoor air. Left in, such a value becomes the
coldest point of the fit -- the first temperature breakpoint of a TOWT model, or the change point
of a heating model -- and bends the whole baseline toward it. The screen sets every value outside
the plausible outdoor-air range to missing and counts them, so a fit drops those rows the way it
drops any missing temperature, and its result says how many there were.

The range is the sensor-health check's own (:data:`camber.sensorhealth.PHYSICAL_BOUNDS` for
``Role.OAT``: -40 to 140 degF), so a value the sensor-health report calls out of range is the
value a baseline fit ignores. -40 is the same in degF and degC, so the lower bound also holds for
a Celsius series; the upper bound is then loose but harmless.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def oat_bounds() -> tuple:
    """The plausible outdoor-air temperature range, ``(lo, hi)`` in degF, from sensor health."""
    from ..model.roles import Role
    from ..sensorhealth import PHYSICAL_BOUNDS

    lo, hi = PHYSICAL_BOUNDS[Role.OAT]
    return float(lo), float(hi)


def screen_temperature(temp, bounds: tuple | None = None) -> tuple:
    """``(screened, n_screened)``: ``temp`` with every value outside ``bounds`` set to NaN.

    ``bounds`` is ``(lo, hi)``, inclusive; ``None`` (the default) is :func:`oat_bounds`. Pass
    ``(-inf, inf)`` to keep every finite value. ``n_screened`` counts the values that were present
    (not NaN) and outside the range -- an infinity included. A ``pandas.Series`` comes back as a
    Series on the same index, anything else as a float array. Values inside the range are
    returned unchanged, bit for bit, so clean data fits exactly as before.
    """
    lo, hi = oat_bounds() if bounds is None else (float(bounds[0]), float(bounds[1]))
    if isinstance(temp, pd.Series):
        vals = temp.to_numpy(dtype=float)
    else:
        vals = np.asarray(temp, dtype=float)
    with np.errstate(invalid="ignore"):
        bad = ~np.isnan(vals) & ~((vals >= lo) & (vals <= hi))
    n = int(bad.sum())
    if isinstance(temp, pd.Series):
        if not n:
            return temp, 0
        return temp.astype(float).mask(pd.Series(bad, index=temp.index)), n
    if not n:
        return vals, 0
    out = vals.copy()
    out[bad] = np.nan
    return out, n


def screened_caveat(n: int, what: str = "temperature value(s)") -> str:
    """The one sentence a result carries when ``n`` values were screened out."""
    lo, hi = oat_bounds()
    return (
        f"{n} {what} outside the plausible outdoor range ({lo:g} to {hi:g} degF) were treated as "
        "missing before fitting (a placeholder or sensor fault, not weather)"
    )


def bounds_to_json(bounds: tuple | None) -> list | None:
    """``bounds`` as a JSON-safe list (an infinite end as ``None``); ``None`` stays ``None``, the
    default range (0.105, #168: a model records the bounds it was fitted with)."""
    if bounds is None:
        return None
    return [float(b) if np.isfinite(b) else None for b in (bounds[0], bounds[1])]


def bounds_from_json(v) -> tuple | None:
    """The inverse of :func:`bounds_to_json`."""
    if v is None:
        return None
    lo, hi = v
    return (-np.inf if lo is None else float(lo), np.inf if hi is None else float(hi))


def fitted_bounds(bounds: tuple | None) -> tuple | None:
    """The ``temp_bounds`` a fit was given, as a model stores it: ``None`` for the default range,
    else ``(lo, hi)`` as floats."""
    return None if bounds is None else (float(bounds[0]), float(bounds[1]))
