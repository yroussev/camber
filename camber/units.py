"""Engineering-unit normalization for position/percent signals.

Valve, damper, and speed signals arrive 0-100 (percent) from some BAS and 0-1
(fraction) from others. Threshold-based rules assume percent (e.g. "valve <= 5%
is closed"), so a 0-1 source silently misfires -- every value looks closed, or
fully open. :func:`normalize_percent` detects a fraction-scaled series and
rescales it to percent; it is a no-op on data already in percent.

Heuristic and its limit: a series whose finite values all fall within ~[0, 1.5] is
treated as a fraction and multiplied by 100. The rare false case -- a genuine
0-100 signal that never rises above ~1.5% in the window -- would be mis-scaled;
that is far less likely than a 0-1 source, and the alternative (silent
0-1 misfire) is worse. Pass an explicit ``fraction_max`` to tune, or normalize at
the adapter if the source's units are known.
"""

from __future__ import annotations

import pandas as pd

from .model.roles import Role

# Roles whose magnitude is a position/percent (0-100) that rules threshold on.
PERCENT_ROLES = frozenset({
    Role.HEAT_VALVE, Role.COOL_VALVE, Role.OA_DAMPER, Role.DAMPER,
    Role.SUPPLY_FAN_SPEED, Role.CHW_PUMP_SPEED,
})

FRACTION_MAX = 1.5   # finite max at/below this (and non-negative) => 0-1 fraction


def looks_like_fraction(s: pd.Series, fraction_max: float = FRACTION_MAX) -> bool:
    """True if the series appears to be a 0-1 fraction rather than 0-100 percent."""
    v = pd.to_numeric(s, errors="coerce").dropna()
    if v.empty:
        return False
    return v.max() <= fraction_max and v.min() >= -0.01


def normalize_percent(s: pd.Series, fraction_max: float = FRACTION_MAX) -> pd.Series:
    """Rescale a 0-1 fraction series to 0-100 percent; no-op if already percent."""
    return s * 100.0 if looks_like_fraction(s, fraction_max) else s


def normalize_percent_frame(frame: pd.DataFrame, *, roles=PERCENT_ROLES,
                            fraction_max: float = FRACTION_MAX) -> pd.DataFrame:
    """Normalize the percent/position-role columns of a role-named frame to percent.

    Only columns whose label is in ``roles`` are touched; everything else
    (temperatures, statuses, flows) is left unchanged.
    """
    if frame is None or frame.empty:
        return frame
    out = frame.copy()
    for col in out.columns:
        if col in roles:
            out[col] = normalize_percent(out[col], fraction_max)
    return out
