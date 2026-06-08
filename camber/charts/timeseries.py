"""Heating/cooling/OAT time-series companion to the heating-vs-cooling scatter.

Heating- and cooling-valve positions on the primary axis (0-100%), OAT on a
secondary axis (°F). Flags simultaneous-heat/cool events and those occurring at
high OAT (the clearest reheat fault). See ``spec-ahu-hec-scatter.md``.
"""

from __future__ import annotations

from .scatter import _resolve


def ahu_hec_timeseries(df, ahu_id, *, threshold=5.0, oat_col=None,
                       cooling_cutoff_f=65.0, ax=None):
    """Draw the heating/cooling/OAT time-series. Returns the primary Axes."""
    import matplotlib.pyplot as plt

    hec, cc, oat_col = _resolve(df, ahu_id, oat_col)
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))

    ax.plot(df.index, df[hec], color="#cc3333", lw=0.9, label="Heating valve %")
    ax.plot(df.index, df[cc], color="#3366cc", lw=0.9, label="Cooling valve %")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Valve position (% open)")
    ax.set_xlabel("Time")

    overlap = (df[hec] > threshold) & (df[cc] > threshold)
    if oat_col:
        ax2 = ax.twinx()
        ax2.plot(df.index, df[oat_col], color="#999999", lw=0.7, label="OAT (°F)")
        ax2.set_ylabel("OAT (°F)")
        hot = overlap & (df[oat_col] > cooling_cutoff_f)
        ax.scatter(df.index[hot], df[hec][hot], color="red", s=10, zorder=5,
                   label="Simul. H/C @ OAT>65°F")

    pct = 100.0 * overlap.mean() if len(df) else 0.0
    ax.set_title(f"AHU{ahu_id} heating / cooling / OAT — "
                 f"{pct:.1f}% of intervals simultaneous H/C")
    ax.legend(loc="upper left", fontsize=8)
    return ax
