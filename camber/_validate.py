"""Small shared input-validation helpers.

Public analytics functions that take a time series assume a timestamp index and numeric
values; on a wrong-shaped input they otherwise surface a cryptic pandas/numpy error deep in
the math (or, worse, silently coerce a numeric index to nanosecond timestamps and return a
plausible-looking wrong answer). These helpers raise one clear ``ValueError`` up front instead,
in the style of :func:`camber.io.load_csv`. Private module -- not public API.
"""

from __future__ import annotations

import pandas as pd


def require_series(x, name: str) -> pd.Series:
    """Raise ``ValueError`` unless ``x`` is a pandas Series."""
    if not isinstance(x, pd.Series):
        raise ValueError(f"{name} must be a pandas Series, got {type(x).__name__}")
    return x


def require_datetime_index(s, name: str, *, allow_empty: bool = True) -> pd.Series:
    """Raise ``ValueError`` unless ``s`` is a Series with a ``DatetimeIndex``.

    A numeric/range index is the common mistake: pandas would happily read it as nanosecond
    timestamps and return a meaningless result, so we reject it explicitly. An **empty** series
    is passed through by default (it carries no data to mis-index, and callers handle "empty in →
    empty out" gracefully); the index requirement is enforced only where there is data.
    """
    require_series(s, name)
    if allow_empty and len(s) == 0:
        return s
    if not isinstance(s.index, pd.DatetimeIndex):
        raise ValueError(
            f"{name} must be indexed by timestamp (a pandas DatetimeIndex), "
            f"got a {type(s.index).__name__}"
        )
    return s
