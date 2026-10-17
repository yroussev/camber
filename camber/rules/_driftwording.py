"""Shared wording for drift-detector summaries.

An ``ok`` drift Finding is a move short of the magnitude floors, so it is not a fault and its
summary names no cause: it reports the signal holding near its baseline, with the signed shift and
its sigma, and leaves the likely cause to ``warn`` and ``fault`` (0.106, #187 and #189).
"""

from __future__ import annotations


def signed(value: float, decimals: int) -> str:
    """``value`` with its sign, as an ``ok`` summary prints it; a shift that rounds to zero is 0."""
    text = f"{value:+.{decimals}f}"
    return text[1:] if float(text) == 0.0 else text


def holds_near_baseline(
    equip: str,
    signal: str,
    shift: float,
    sigma: float,
    *,
    decimals: int,
    unit: str,
    matched: str,
) -> str:
    """The summary of an ``ok`` drift Finding: ``signal`` holds near its baseline, no cause named.

    ``unit`` is appended to the shift as given, so it carries its own leading space (``" kW"``) or
    none (``"°F"``, ``"%"``); ``matched`` is what the baseline is matched on (``"matched flow"``).
    """
    return (
        f"{equip}: {signal} holds near its baseline ({signed(shift, decimals)}{unit}, "
        f"{abs(sigma):.1f}σ vs frozen baseline at {matched})"
    )
