"""Fleet zone heating-vs-cooling census.

At each timestamp, count how many terminal zones are calling for HEATING vs how
many are calling for COOLING, and profile that against time-of-week and OAT. In a
well-run building the two counts are rarely both high at once; a building that
overcools centrally and re-warms locally will show many zones cooling while a
subset simultaneously reheats -- the reheat-penalty signature this census
quantifies.

Per-zone state each interval:
  - heating  = reheat (HW) valve open above threshold
  - cooling  = airflow above its minimum setpoint (box pulling cooling air) OR,
               if no flow setpoint, space temp at/above cooling setpoint
A zone can be counted in BOTH (heating with its airflow above the minimum) -- we track that
explicitly as ``n_both``.

0.106 (#186): what the census is named for is **different** zones heating and cooling at once
(central overcooling re-warmed locally), not one box heating above its minimum airflow (that is
:class:`camber.rules.reheat_min_rule.ReheatMinimization`'s footprint, and in G36's dual-maximum
sequence it is by design at high heating demand). ``n_cooling_only`` counts the zones cooling
without heating, and ``mixed`` marks the intervals where at least one zone heats while at least
one *other* zone cools.
"""

from __future__ import annotations

import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "ZONE_MEASURES",
    "zone_states",
    "time_of_week_profile",
]

ZONE_MEASURES = ["HWValve", "ActFlow", "ActFlowSP", "SpaceTemp", "ActCoolSP", "WarmUp", "CoolDown"]


def zone_states(box_frames, *, valve_thr=5.0, flow_margin=1.10):
    """Per-timestamp heating/cooling/both counts across many zones.

    ``box_frames`` : dict {equip -> DataFrame with measure columns}, each already
    on a common time grid. Returns a DataFrame indexed by time with columns:
    n_zones, n_heating, n_cooling, n_both.

    "Cooling" definition (our modeling choice): airflow above its setpoint by
    ``flow_margin`` (default 1.10 = 10% over, so normal modulation around the floor
    doesn't count as cooling), or -- when no flow setpoint exists -- space temp at or
    above the cooling setpoint. ``valve_thr`` (5%) is the shut-valve deadband.
    """
    heating_cols = {}
    cooling_cols = {}
    cooling_unevaluable = []  # boxes with no flow/space-temp signal to judge "cooling"
    for equip, df in box_frames.items():
        if "HWValve" not in df.columns:
            continue
        heating = df["HWValve"] > valve_thr
        if "ActFlow" in df.columns and "ActFlowSP" in df.columns:
            cooling = df["ActFlow"] > df["ActFlowSP"] * flow_margin
        elif "SpaceTemp" in df.columns and "ActCoolSP" in df.columns:
            cooling = df["SpaceTemp"] >= df["ActCoolSP"]
        else:
            # No cooling criterion available: this box's "cooling" is NOT evaluated. Fill
            # False so the columns align, but record it so a consumer can caveat that the
            # census may under-count cooling/simultaneous rather than read a confident 0.
            cooling = pd.Series(False, index=df.index)
            cooling_unevaluable.append(equip)
        heating_cols[equip] = heating
        cooling_cols[equip] = cooling

    if not heating_cols:
        return pd.DataFrame()

    H = pd.DataFrame(heating_cols).fillna(False)
    C = pd.DataFrame(cooling_cols).fillna(False)
    present = pd.DataFrame(
        {e: f.notna().any(axis=1) for e, f in box_frames.items() if "HWValve" in f.columns}
    ).fillna(False)

    out = pd.DataFrame(index=H.index)
    out["n_zones"] = present.sum(axis=1)
    out["n_heating"] = H.sum(axis=1)
    out["n_cooling"] = C.sum(axis=1)
    out["n_both"] = (H & C).sum(axis=1)
    # 0.106 (#186): different zones heating and cooling at once
    out["n_cooling_only"] = (C & ~H).sum(axis=1)
    out["mixed"] = (out["n_heating"] > 0) & (out["n_cooling_only"] > 0)
    out.attrs["cooling_unevaluable"] = cooling_unevaluable
    return out


def time_of_week_profile(states, *, occupied_only=True, occupied=None):
    """Average heating/cooling/both counts by time-of-week bin (0..167).

    ``occupied`` (a boolean Series on ``states.index``) overrides the default weekday
    07-18 window used when ``occupied_only``.
    """
    s = states
    if occupied_only:
        mask = occupied_mask(s.index) if occupied is None else occupied.reindex(s.index)
        s = s[mask.fillna(False).astype(bool)]
    if s.empty:
        return pd.DataFrame()
    tow = s.index.dayofweek * 24 + s.index.hour
    g = s.groupby(tow)[["n_zones", "n_heating", "n_cooling", "n_both"]].mean()
    g.index.name = "time_of_week_hour"
    return g
