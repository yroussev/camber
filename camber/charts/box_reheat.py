"""Per-box reheat visualization: reheat valve, space temp vs setpoints, OAT.

Visual proof for the top reheat offenders. One stacked figure per box:
  (top)    HW reheat valve %  + box airflow vs setpoint
  (bottom) space temp with heating/cooling setpoint band + OAT on 2nd axis
Reheat-at-high-OAT events are highlighted.
"""

from __future__ import annotations

import pandas as pd


def box_reheat_figure(df, equip, *, oat=None, valve_thr=5.0,
                      cooling_cutoff_f=65.0, occupied_only=True):
    """Build a 2-panel reheat diagnostic figure for one box. Returns the Figure."""
    import matplotlib.pyplot as plt

    work = df.copy()
    if occupied_only:
        hour = work.index.hour + work.index.minute / 60.0
        occ = (work.index.dayofweek < 5) & (hour >= 7) & (hour < 18)
        for m in ("WarmUp", "CoolDown"):
            if m in work.columns:
                occ = occ & ~(work[m].fillna(0) > 0.5)
        work = work[occ]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7), sharex=True)

    # --- panel 1: reheat valve + airflow ---
    if "HWValve" in work.columns:
        ax1.plot(work.index, work["HWValve"], color="#cc3333", lw=0.6,
                 label="HW reheat valve %")
    ax1.set_ylabel("Reheat valve (%)", color="#cc3333")
    ax1.set_ylim(0, 100)
    if "ActFlow" in work.columns:
        axf = ax1.twinx()
        axf.plot(work.index, work["ActFlow"], color="#888888", lw=0.5, alpha=0.7,
                 label="Airflow")
        if "ActFlowSP" in work.columns:
            axf.plot(work.index, work["ActFlowSP"], color="#888888", lw=0.5,
                     ls="--", alpha=0.7, label="Airflow SP")
        axf.set_ylabel("Airflow (cfm)", color="#888888")
    ax1.set_title(f"{equip} — reheat diagnostic (occupied hours)")
    ax1.legend(loc="upper left", fontsize=8)

    # --- panel 2: space temp + setpoint band + OAT ---
    if "SpaceTemp" in work.columns:
        ax2.plot(work.index, work["SpaceTemp"], color="#2a7", lw=0.7,
                 label="Space temp")
    if "ActHeatSP" in work.columns and "ActCoolSP" in work.columns:
        ax2.fill_between(work.index, work["ActHeatSP"], work["ActCoolSP"],
                         color="#cccc44", alpha=0.15, label="Setpoint band")
    ax2.set_ylabel("Temp (°F)")
    if oat is not None:
        oat_a = oat.reindex(work.index).ffill(limit=4)
        ax2b = ax2.twinx()
        ax2b.plot(work.index, oat_a, color="#999", lw=0.5, label="OAT")
        ax2b.set_ylabel("OAT (°F)", color="#999")
        # highlight reheat-at-high-OAT
        if "HWValve" in work.columns:
            hot = (work["HWValve"] > valve_thr) & (oat_a > cooling_cutoff_f)
            ax2.scatter(work.index[hot], work["SpaceTemp"][hot] if "SpaceTemp" in work
                        else [cooling_cutoff_f] * int(hot.sum()),
                        color="red", s=4, zorder=5, label="reheat @ OAT>65°F")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.set_xlabel("Time")
    fig.tight_layout()
    return fig
