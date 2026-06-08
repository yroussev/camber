"""AHU night/weekend setback diagnostic (PNNL Re-tuning Ch.5).

The cheapest large saver: an air handler that runs 24/7 with no night or weekend
setback wastes fan energy (and the heating/cooling to condition air nobody needs).
This flags the supply fan running during *unoccupied* hours.

Unoccupied = the complement of the occupied window (weekday daytime), i.e. nights
and weekends. "Running" can be read from a fan status point (preferred) or, if
only speed is trended, from speed above a small threshold.

Headline metric: fraction of unoccupied hours the fan is running. A well-scheduled
AHU is near 0%; continuous operation is ~100%. We also report the occupied-vs-
unoccupied run ratio so a partial/ineffective setback is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask

SETBACK_MEASURES = ["SupplyFanStatus", "SupplyFanSpeed"]


@dataclass
class SetbackResult:
    """Unoccupied-setback diagnostics: fan runtime occupied vs unoccupied."""

    equip: str
    n_occupied: int
    n_unoccupied: int
    fan_run_occupied_pct: float       # % occupied hrs fan running (sanity: should be high)
    fan_run_unoccupied_pct: float     # % unoccupied hrs fan running (the fault metric)
    setback_effective: bool           # unoccupied run materially below occupied run
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _running(work: pd.DataFrame, speed_thr: float):
    """Boolean 'fan running' from status (preferred) or speed."""
    if "SupplyFanStatus" in work.columns and work["SupplyFanStatus"].notna().any():
        return work["SupplyFanStatus"].fillna(0) > 0.5
    if "SupplyFanSpeed" in work.columns:
        return work["SupplyFanSpeed"].fillna(0) > speed_thr
    return None


def analyze_setback(
    df: pd.DataFrame,
    equip: str,
    *,
    speed_thr: float = 5.0,           # fan speed above this counts as running
    setback_ratio: float = 0.5,       # unoccupied run < this * occupied run == effective
    start_hour: int = 7,
    end_hour: int = 18,
) -> SetbackResult | None:
    """Detect missing night/weekend setback for one AHU.

    ``setback_ratio`` is OUR judgment threshold: a setback is "effective" only if
    unoccupied run fraction is below half the occupied run fraction. ``speed_thr``
    is the run deadband when only fan speed is available.
    """
    run = _running(df, speed_thr)
    if run is None:
        return None
    occ = occupied_mask(df.index, start_hour=start_hour, end_hour=end_hour)
    unocc = ~occ
    n_occ = int(occ.sum())
    n_un = int(unocc.sum())
    if n_un == 0:
        return None

    occ_run = round(100.0 * float(run[occ].mean()), 2) if n_occ else 0.0
    un_run = round(100.0 * float(run[unocc].mean()), 2)
    effective = un_run < setback_ratio * occ_run if occ_run > 0 else un_run < 5.0

    return SetbackResult(
        equip=equip,
        n_occupied=n_occ,
        n_unoccupied=n_un,
        fan_run_occupied_pct=occ_run,
        fan_run_unoccupied_pct=un_run,
        setback_effective=bool(effective),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
