"""Lighting operational efficiency from submetered power vs installed power.

Operational efficiency = metered lighting power / installed lighting power. Read by
occupancy state it reveals controls faults: lights near 100% when unoccupied means
failed scheduling/occupancy sensing; a flat 100% all the time means dimming or
controls are disabled. Needs a lighting submeter (zone/floor/whole-building) and
the installed lighting power for the metered area.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schedules import occupied_mask


def operational_efficiency(metered_kw: pd.Series, installed_kw: float) -> pd.Series:
    """Fraction of installed lighting power drawn at each interval (0..~1)."""
    if installed_kw <= 0:
        raise ValueError("installed_kw must be positive")
    return metered_kw.dropna() / installed_kw


@dataclass(frozen=True)
class LightingSummary:
    """Lighting controls assessment from metered vs installed power."""

    occupied_mean: float        # mean efficiency during occupied hours
    unoccupied_mean: float      # mean efficiency during unoccupied hours
    min_efficiency: float       # lowest interval efficiency (does it ever turn down?)
    flags: tuple                # detected controls faults


def lighting_summary(metered_kw: pd.Series, installed_kw: float, *,
                     occupied=None, unoccupied_max: float = 0.30,
                     always_on_min: float = 0.90) -> LightingSummary:
    """Assess lighting controls.

    ``occupied`` is a boolean mask aligned to the series; if omitted, a weekday
    daytime schedule is used. Flags:
    - ``failed_unoccupied_setback``: high draw when unoccupied (scheduling/sensor fault)
    - ``no_turndown``: never drops below ``always_on_min`` (dimming/controls disabled)
    """
    eff = operational_efficiency(metered_kw, installed_kw)
    if occupied is None:
        occupied = occupied_mask(eff.index)
    occupied = pd.Series(occupied, index=eff.index).reindex(eff.index).fillna(False)

    occ_mean = float(eff[occupied].mean()) if occupied.any() else float("nan")
    unocc = eff[~occupied]
    unocc_mean = float(unocc.mean()) if len(unocc) else float("nan")
    min_eff = float(eff.min())

    flags = []
    if pd.notna(unocc_mean) and unocc_mean > unoccupied_max:
        flags.append("failed_unoccupied_setback")
    if min_eff > always_on_min:
        flags.append("no_turndown")
    return LightingSummary(occupied_mean=round(occ_mean, 4),
                           unoccupied_mean=round(unocc_mean, 4),
                           min_efficiency=round(min_eff, 4), flags=tuple(flags))
