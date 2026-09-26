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

**Which air temperature is "cold supply"?** Indicator 1 needs the box's *entering*
primary air -- the cold AHU supply feeding the box, upstream of its reheat coil. Pass it
as ``PrimaryAir`` (the role layer maps a terminal's ``MIXED_AIR_TEMP`` here -- the same
convention :mod:`camber.rules.vav_reheat_valve_rule` uses). ``SupplyAir`` is the legacy
column: whatever the caller trended at the box, and at a terminal that is usually the
box's own *discharge* air, downstream of the reheat coil. Reheat warms discharge air, so
"discharge < 60 F with the valve open" is only a **lower bound** on simultaneous
heat/cool (it implies cold primary air, but warm discharge does not rule it out);
``coldsupply_basis`` records which column was used so the caller can caveat it.

Occupied = a trended ``Occupancy`` column when present (it replaces the schedule), else
the ``start_hour``/``end_hour``/``occupied_days`` schedule (default weekday 07-18).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import effective_occupied_mask

__all__ = [
    "BOX_MEASURES",
    "ReheatResult",
    "analyze_box",
]

# Measures we try to load for each terminal box.
BOX_MEASURES = [
    "HWValve",
    "SpaceTemp",
    "SupplyAir",
    "PrimaryAir",
    "ActHeatSP",
    "ActCoolSP",
    "ActFlow",
    "ActFlowSP",
    "Damper",
    "WarmUp",
    "CoolDown",
    "Occupancy",
]


@dataclass
class ReheatResult:
    """VAV terminal reheat diagnostics: valve-open rates and excess-reheat overlaps."""

    equip: str
    n_intervals: int
    n_considered: int
    valve_open_pct: float  # % intervals reheat valve > thr
    reheat_and_coldsupply_pct: float
    reheat_at_high_oat_pct: float
    reheat_above_min_flow_pct: float
    reheat_below_coolsp_pct: float
    mean_valve_when_open: float
    coverage_start: str
    coverage_end: str
    # which column indicator 1 used: "primary" (entering primary air), "supply" (the legacy
    # SupplyAir column -- at a terminal usually the discharge, a lower bound), or None (neither)
    coldsupply_basis: str | None = None

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
    start_hour: float = 7,
    end_hour: float = 18,
    occupied_days=(0, 1, 2, 3, 4),
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

    # Occupied = a trended Occupancy point (replaces the schedule) else the configured schedule,
    # minus WarmUp/CoolDown prep modes (schedules.effective_occupied_mask).
    if occupied_only:
        work = work[
            effective_occupied_mask(
                work.index,
                occ=work["Occupancy"] if "Occupancy" in work.columns else None,
                start_hour=start_hour,
                end_hour=end_hour,
                days=occupied_days,
                warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
                cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
            )
        ]
    n = len(work)
    if n == 0:
        return None

    v = work["HWValve"]
    valve_open = v > valve_thr

    # 1. reheat + cold supply air -- the entering primary air when known, else the legacy
    #    SupplyAir column (at a terminal usually the discharge: a lower bound, see module doc)
    basis = None
    for col, name in (("PrimaryAir", "primary"), ("SupplyAir", "supply")):
        if col in work.columns and work[col].notna().any():
            rc = valve_open & (work[col] < cold_supply_f)
            basis = name
            break
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
        coldsupply_basis=basis,
    )
