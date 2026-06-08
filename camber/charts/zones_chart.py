"""Fleet zone chart: # zones heating vs # cooling vs both, by time-of-week + vs OAT.

Visualizes the reheat-penalty census from ``zones.py`` -- how many zones heat
while others cool, across the week and against outdoor temperature.
"""

from __future__ import annotations

import pandas as pd


def zones_timeofweek_figure(profile, *, title="Zones heating vs cooling (time-of-week)"):
    """profile: DataFrame indexed by time-of-week hour (0..167) with n_heating/
    n_cooling/n_both columns. Returns a Figure."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(13, 4))
    ax.plot(profile.index, profile["n_cooling"], color="#3366cc", lw=1.0, label="# zones cooling")
    ax.plot(profile.index, profile["n_heating"], color="#cc3333", lw=1.0, label="# zones heating (reheat)")
    ax.fill_between(profile.index, 0, profile["n_both"], color="purple", alpha=0.25,
                    label="# zones BOTH (reheat penalty)")
    for d in range(1, 7):
        ax.axvline(d * 24, color="#dddddd", lw=0.5)
    ax.set_xlim(0, 168)
    ax.set_xticks([d * 24 + 12 for d in range(7)])
    ax.set_xticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ax.set_ylabel("Average # of zones")
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def zones_vs_oat_figure(states, oat, *, title="Zones heating/cooling vs OAT"):
    """states: per-timestamp counts; oat: aligned OAT Series. Returns a Figure."""
    import matplotlib.pyplot as plt

    oa = oat.reindex(states.index).ffill(limit=4)
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(oa, states["n_heating"], s=4, alpha=0.3, color="#cc3333", label="# heating")
    ax.scatter(oa, states["n_cooling"], s=4, alpha=0.3, color="#3366cc", label="# cooling")
    ax.set_xlabel("OAT (°F)")
    ax.set_ylabel("# of zones")
    ax.set_title(title)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig
