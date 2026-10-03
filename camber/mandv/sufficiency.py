"""What an M&V baseline is short of, stated as data needed (0.98, #88).

An M&V refusal for too little baseline data is only half an answer: the engineer also needs to
know how much more data would carry a fit. :func:`baseline_need` states it as a small dict --
required, available, shortfall, the unit, and the calendar days it takes to collect -- and
:class:`InsufficientBaseline` carries that dict on the exception the fitting functions raise.

The exception is a :class:`ValueError` whose message is exactly what the fitting functions
raised before, so callers that catch ``ValueError`` or match the message are unaffected; the new
information is on ``.need``.
"""

from __future__ import annotations

import math

__all__ = ["InsufficientBaseline", "baseline_need"]

# calendar days one more unit takes to collect, per interval (a missing hour-of-week bin, or one
# more observation in every bin, takes up to a week)
_DAYS_PER_UNIT = {"daily": 1.0, "hourly": 1.0 / 24.0}
_UNITS = {"daily": "days", "hourly": "hours"}


def baseline_need(
    interval: str,
    n_have: int,
    *,
    min_n: int,
    unit: str | None = None,
    days_per_unit: float | None = None,
    what: str = "baseline",
) -> dict:
    """How much more baseline data a fit needs.

    ``interval`` is ``"daily"`` or ``"hourly"``; ``n_have`` is what the baseline has and
    ``min_n`` what the fit requires, both in ``unit`` (``"days"`` / ``"hours"`` by default, or a
    custom count such as hour-of-week bins). ``days_per_unit`` converts the shortfall to the
    calendar days it takes to collect (default: 1 per day, 1/24 per hour); ``None`` for a custom
    unit with no fixed rate leaves ``days_short`` as ``None``.

    Returns ``{interval, required, have, shortfall, unit, days_short, text}``; ``text`` is a
    one-line reading such as "1,440 baseline hours needed, 168 available: 1,272 more hours (about
    53 days of data)".
    """
    if interval not in _DAYS_PER_UNIT and unit is None:
        raise ValueError(f"interval must be 'daily' or 'hourly' (or give unit=), got {interval!r}")
    unit = unit or _UNITS[interval]
    if days_per_unit is None and unit == _UNITS.get(interval):
        days_per_unit = _DAYS_PER_UNIT[interval]
    required, have = int(min_n), int(n_have)
    shortfall = max(required - have, 0)
    days_short = None if days_per_unit is None else int(math.ceil(shortfall * days_per_unit))
    text = f"{required:,} {what} {unit} needed, {have:,} available"
    if shortfall:
        text += f": {shortfall:,} more {unit}"
        if days_short is not None and unit != "days":
            text += f" (about {days_short:,} days of data)"
    return {
        "interval": interval,
        "required": required,
        "have": have,
        "shortfall": shortfall,
        "unit": unit,
        "days_short": days_short,
        "text": text,
    }


class InsufficientBaseline(ValueError):
    """A baseline too short (or too thinly covered) to fit; ``.need`` is :func:`baseline_need`'s
    dict. ``str()`` is the message the fitting function has always raised."""

    def __init__(self, message: str, need: dict):
        super().__init__(message)
        self.need = need
