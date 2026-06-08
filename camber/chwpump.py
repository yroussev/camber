"""Chilled-water pump differential-pressure reset diagnostic (PNNL Ch.8).

Variable-speed CHW pumps should slow at part load, with the loop differential-
pressure (DP) setpoint reset down as valves open, so the pumps only work as hard
as the load needs. Because pump power varies with roughly the cube of speed, a
pump pinned near full speed at part load wastes large amounts of energy.

This flags pumps running at/near full speed: the fraction of running hours pump
speed is above a high threshold. A healthy variable-flow plant spends much of its
time well below 100%; pumps pinned at 100% indicate no effective DP reset (or a DP
setpoint held too high). Operates over one or more pump-speed series.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask


@dataclass
class CHWPumpResult:
    """Chilled-water pump speed and DP-setpoint reset diagnostics for one pump."""

    equip: str
    n_running: int
    median_speed_pct: float
    pct_running_near_full: float   # % running hrs speed >= near_full_pct
    median_dp_sp: float            # median DP setpoint (if available)
    dp_sp_reset_present: bool      # DP setpoint varies materially
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_chw_pump(
    df: pd.DataFrame,
    equip: str,
    *,
    run_thr: float = 5.0,          # speed above this == pump running
    near_full_pct: float = 90.0,   # speed at/above this == effectively full speed
    dp_sp_flat_std: float = 0.5,   # DP-SP std below this == flat (no reset)
    occupied_only: bool = False,   # pumps run on load, not occupancy; default all hrs
) -> CHWPumpResult | None:
    """Diagnose CHW pump speed / DP reset. ``df`` has 'PumpSpeed' (and optional
    'DiffPressSP'). Speeds are %; running = speed > run_thr.

    Thresholds are OUR judgment / PNNL Ch.8: near_full_pct=90 (a VFD pinned >=90%
    is effectively full speed and getting no cube-law benefit); dp_sp_flat_std=0.5
    (a DP setpoint that barely moves is not being reset).
    """
    if "PumpSpeed" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    spd = work["PumpSpeed"].dropna()
    running = spd[spd > run_thr]
    n = len(running)
    if n == 0:
        return None
    median_speed = round(float(running.median()), 1)
    pct_full = round(100.0 * float((running >= near_full_pct).mean()), 1)

    if "DiffPressSP" in work.columns:
        sp = work["DiffPressSP"].dropna()
        sp = sp[sp > 0]
        if len(sp) >= 10:
            dp_std = float(sp.std())
            dp_med = round(float(sp.median()), 2)
            dp_reset = dp_std >= dp_sp_flat_std
        else:
            dp_med, dp_reset = float("nan"), False
    else:
        dp_med, dp_reset = float("nan"), False

    return CHWPumpResult(
        equip=equip,
        n_running=n,
        median_speed_pct=median_speed,
        pct_running_near_full=pct_full,
        median_dp_sp=dp_med,
        dp_sp_reset_present=bool(dp_reset),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
