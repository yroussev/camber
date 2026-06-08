"""VAV/CAV terminal-unit reheat fault detection.

These boxes have a reheat (HW) valve but no local cooling coil -- cooling is
delivered by cold central supply air. So "simultaneous heating and cooling" at a
box means: the reheat valve is open WHILE the box is also being cooled. We
compute several complementary indicators (per the user's "all of them" choice):

1. reheat_and_coldsupply -- HWValve open AND supply air is cold (box getting
   cooling) -> true simultaneous heat/cool at the box.
2. reheat_at_high_oat    -- HWValve open while OAT > cooling cutoff (~65 F):
   reheating during cooling weather, the headline reheat-penalty metric.
3. reheat_above_min_flow -- HWValve open while airflow is above box minimum
   (box is dumping cooled air while reheating) -- classic VAV reheat penalty.
4. reheat_while_below_coolsp_or_cooling -- HWValve open while space temp is at/
   below the cooling setpoint (no heating call justified).

Each indicator returns the % of (optionally occupied) intervals that trip it,
plus magnitude stats, so findings are quantitative and rankable across boxes.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask

# Measures we try to load for each terminal box.
BOX_MEASURES = [
    "HWValve", "SpaceTemp", "SupplyAir", "ActHeatSP", "ActCoolSP",
    "ActFlow", "ActFlowSP", "Damper", "WarmUp", "CoolDown",
]


@dataclass
class ReheatResult:
    """VAV terminal reheat diagnostics: valve-open rates and excess-reheat overlaps."""

    equip: str
    n_intervals: int
    n_considered: int
    valve_open_pct: float                 # % intervals reheat valve > thr
    reheat_and_coldsupply_pct: float
    reheat_at_high_oat_pct: float
    reheat_above_min_flow_pct: float
    reheat_below_coolsp_pct: float
    mean_valve_when_open: float
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _pct(mask: pd.Series, n: int) -> float:
    return round(100.0 * int(mask.sum()) / n, 2) if n else 0.0


def analyze_box(
    df: pd.DataFrame,
    equip: str,
    *,
    oat: pd.Series | None = None,
    # Threshold basis:
    #   valve_thr=5.0 %    -- a valve <5% open is effectively shut (deadband against
    #                         sensor/command noise). Our convention, applied uniformly.
    #   cold_supply_f=60 F -- supply air below ~60F is delivering cooling; a reheat
    #                         valve open into it is true simultaneous heat/cool. Our
    #                         judgment for a typical ~55F cooling design.
    #   cooling_cutoff_f=65 F -- OAT above ~65F is cooling weather, so reheat is a
    #                         clear fault. Aligns with PNNL Re-tuning Ch.7 guidance
    #                         that reheat in cooling weather is a primary fault signal.
    valve_thr: float = 5.0,
    cold_supply_f: float = 60.0,
    cooling_cutoff_f: float = 65.0,
    occupied_only: bool = True,
) -> ReheatResult | None:
    """Compute reheat indicators for one terminal box.

    ``df`` columns are measure names (from load_equipment). ``oat`` is an aligned
    outdoor-air-temp Series (optional, enables the high-OAT indicator).
    """
    if "HWValve" not in df.columns:
        return None
    work = df.copy()
    n_all = len(work)
    if n_all == 0:
        return None

    # Occupied = weekday daytime window minus WarmUp/CoolDown prep modes, via the
    # single shared occupancy filter (schedules.occupied_mask).
    if occupied_only:
        work = work[occupied_mask(
            work.index,
            warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )]
    n = len(work)
    if n == 0:
        return None

    v = work["HWValve"]
    valve_open = v > valve_thr

    # 1. reheat + cold supply air
    if "SupplyAir" in work.columns:
        cold = work["SupplyAir"] < cold_supply_f
        rc = valve_open & cold
    else:
        rc = pd.Series(False, index=work.index)

    # 2. reheat at high OAT
    if oat is not None:
        # Align OAT onto this box's grid. NOTE: ffill(limit=4) caps the carry-forward
        # at 4 intervals, whose wall-clock span depends on the resample step (4 h at
        # "1h", 1 h at "15min"). This is a known resample-frequency coupling -- a gap
        # longer than 4 steps leaves OAT NaN and those intervals drop from the high-OAT
        # count. Acceptable at hourly; revisit if finer resampling is used.
        oat_a = oat.reindex(work.index).ffill(limit=4)
        rh = valve_open & (oat_a > cooling_cutoff_f)
    else:
        rh = pd.Series(False, index=work.index)

    # 3. reheat above minimum flow -- box dumping cooled air while it reheats, the
    #    classic VAV reheat penalty (PNNL Re-tuning Ch.7, "overcooling / interior
    #    reheat"). "Above minimum" ideally compares to the box's min-airflow
    #    setpoint; the constants below are OUR engineering thresholds, not from a
    #    standard:
    #      * 1.1  -- require airflow >10% over its setpoint floor so sensor noise
    #               and normal modulation around the floor don't trip the flag.
    #      * 0.10 / 1.5 -- fallback when no flow setpoint exists: treat the 10th
    #               percentile of observed airflow as the de-facto floor and flag
    #               airflow >50% above it. Coarser; the setpoint path is preferred.
    if "ActFlow" in work.columns and "ActFlowSP" in work.columns:
        rf = valve_open & (work["ActFlow"] > work["ActFlowSP"] * 1.1)
    elif "ActFlow" in work.columns:
        floor = work["ActFlow"].quantile(0.10)
        rf = valve_open & (work["ActFlow"] > floor * 1.5)
    else:
        rf = pd.Series(False, index=work.index)

    # 4. reheat while at/below cooling setpoint (space not calling for heat)
    if "SpaceTemp" in work.columns and "ActCoolSP" in work.columns:
        rb = valve_open & (work["SpaceTemp"] <= work["ActCoolSP"])
    else:
        rb = pd.Series(False, index=work.index)

    return ReheatResult(
        equip=equip,
        n_intervals=n_all,
        n_considered=n,
        valve_open_pct=_pct(valve_open, n),
        reheat_and_coldsupply_pct=_pct(rc, n),
        reheat_at_high_oat_pct=_pct(rh, n),
        reheat_above_min_flow_pct=_pct(rf, n),
        reheat_below_coolsp_pct=_pct(rb, n),
        mean_valve_when_open=round(float(v[valve_open].mean()), 1) if valve_open.any() else 0.0,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
