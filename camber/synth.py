"""Synthetic trend-data generator for development and tests.

Produces realistic BAS-style AHU trends so the diagnostics can be exercised
without a live data source. Two regimes:

* ``fault="none"``  -- heating and cooling never overlap (good operation).
* ``fault="reheat"`` -- simultaneous heating + cooling at high OAT (the classic
  VAV/reheat pathology): cooling valve open in the afternoon heat while a reheat
  loop also drives the heating valve.

Deterministic given ``seed``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ahu_trends(
    days: int = 14,
    freq_min: int = 15,
    fault: str = "none",
    ahu_id: int = 1,
    seed: int = 0,
):
    """Return a DataFrame of synthetic AHU trends with BAS-style column names."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-07-01 00:00")  # summer: cooling-dominated CZ15
    n = int(days * 24 * 60 / freq_min)
    idx = pd.date_range(start, periods=n, freq=f"{freq_min}min")

    hour = idx.hour + idx.minute / 60.0
    doy = idx.dayofyear

    # Outdoor air temp: diurnal swing peaking ~16:00, hot desert summer.
    oat = (
        92
        + 14 * np.sin((hour - 9) / 24 * 2 * np.pi)
        + 3 * np.sin(doy / 365 * 2 * np.pi)
        + rng.normal(0, 1.0, n)
    )

    occupied = (idx.dayofweek < 5) & (hour >= 7) & (hour < 18)

    # Cooling-coil valve: tracks cooling demand -> driven by OAT when occupied.
    cc = np.clip((oat - 70) * 4 + rng.normal(0, 3, n), 0, 100)
    cc = np.where(occupied, cc, np.clip(cc - 60, 0, 100))  # setback when unoccupied

    # Heating-coil valve: in good operation, only on cold mornings (OAT low).
    hec = np.clip((60 - oat) * 5 + rng.normal(0, 2, n), 0, 100)

    if fault == "reheat":
        # Reheat fault: during occupied afternoons (high OAT) a reheat loop opens
        # the heating valve WHILE the cooling coil is also open -> simultaneous H/C.
        afternoon = occupied & (hour >= 12) & (hour < 17)
        hec = np.where(afternoon, np.clip(35 + rng.normal(0, 5, n), 0, 100), hec)

    df = pd.DataFrame(
        {
            "Timestamp": idx,
            f"AHU{ahu_id}_HeC": hec.round(1),
            f"AHU{ahu_id}_CC": cc.round(1),
            "Bldg_TempOa": oat.round(1),
            f"AHU{ahu_id}_Occ": occupied.astype(int),
        }
    )
    return df.set_index("Timestamp")
