"""Shared value coercion for BAS/interval exports — numeric cleanup + status/command text → 0/1.

Vendor exports embed values in ways bare ``pd.to_numeric`` mangles: thousands separators
(``1,234``), European decimal commas (``1,5``), and a zoo of null/quality tokens (``N/A``, ``---``,
``Bad``, ``Comm Fail``). Status and command points carry *text* (``Running``/``Off``,
``Open``/``Closed``, ``Fault``/``Normal``). This module centralizes both so every adapter shares one
coercion path instead of a copy-pasted ``to_numeric(errors="coerce")``.

Pure numpy/pandas + stdlib. Vocabularies are overridable (see the vendor profiles) so a role- or
site-specific mapping can be supplied.
"""

from __future__ import annotations

import pandas as pd

#: tokens that mean "no value" — normalized to NaN before numeric parsing (case-insensitive)
NULL_TOKENS = frozenset(
    {
        "",
        "nan",
        "null",
        "none",
        "n/a",
        "na",
        "#n/a",
        "---",
        "--",
        "[-]",
        "-",
        "?",
        "bad",
        "no data",
        "nodata",
        "comm fail",
        "commfail",
        "err",
        "error",
        "#value!",
    }
)

#: text that maps to 1.0 (ON / active / open) — extends the classic BAS on/off vocabulary
STATUS_ON = frozenset(
    {
        "running",
        "on",
        "start",
        "started",
        "enabled",
        "active",
        "occupied",
        "true",
        "yes",
        "1",
        "open",
        "opened",
        "fault",
        "alarm",
        "tripped",
        "trip",
        "override",
        "hand",
        "manual",
    }
)
#: text that maps to 0.0 (OFF / inactive / closed / normal)
STATUS_OFF = frozenset(
    {
        "off",
        "stop",
        "stopped",
        "disabled",
        "inactive",
        "unoccupied",
        "false",
        "no",
        "0",
        "standby",
        "idle",
        "closed",
        "close",
        "normal",
        "auto",
        "ready",
        "reset",
        "ok",
    }
)


def coerce_numeric(
    series, *, thousands: str | None = ",", decimal: str = ".", null_tokens=NULL_TOKENS
) -> pd.Series:
    """Coerce ``series`` to float: normalize null tokens, strip thousands/decimal, then to_numeric.

    Clean numeric input is unchanged; only previously-unparseable cells (thousands-grouped numbers,
    null/quality tokens) are recovered or set to NaN. Never raises.
    """
    s = pd.Series(series)
    if s.dtype.kind in "biufc":  # already numeric
        return pd.to_numeric(s, errors="coerce")
    text = s.astype(str).str.strip()
    low = text.str.lower()
    text = text.mask(low.isin(null_tokens))  # null tokens -> NaN
    if thousands:
        text = text.str.replace(thousands, "", regex=False)
    if decimal and decimal != ".":
        text = text.str.replace(decimal, ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


def coerce_status(series, *, on=STATUS_ON, off=STATUS_OFF) -> pd.Series:
    """Map status/command text to a 1.0/0.0 float Series (numeric-nonzero → on; unknown → NaN)."""
    vals = pd.Series(series).astype(str).str.strip().str.lower()

    def _to01(v):
        if v in on:
            return 1.0
        if v in off:
            return 0.0
        try:  # numerically-logged status ("1.0", "85.0")
            return 1.0 if float(v.replace(",", "")) != 0.0 else 0.0
        except (ValueError, AttributeError):
            return float("nan")

    return vals.map(_to01)
