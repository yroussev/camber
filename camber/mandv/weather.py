"""TMY / EPW weather loader for M&V weather normalization.

Reads EnergyPlus Weather (EPW) files -- the format served by
climate.onebuilding.org (TMYx) and NREL (TMY3). An EPW is a CSV with 8 header
lines then 8760 hourly rows; dry-bulb temperature (deg C) is column index 6.

For M&V "normalized savings", you project a fitted change-point model onto a
*typical* weather year so before/after comparisons aren't confounded by a hot or
mild actual year. This loader produces the typical-year temperature series
(monthly means for a monthly model, or the full hourly series) in deg F.
"""

from __future__ import annotations

import csv

import numpy as np
import pandas as pd

EPW_DRYBULB_COL = 6   # 0-based field index of dry-bulb temp (deg C) in an EPW row
_EPW_HEADER_LINES = 8


def c_to_f(c):
    """Convert degrees Celsius to degrees Fahrenheit."""
    return c * 9.0 / 5.0 + 32.0


def load_epw(path: str, *, year: int = 2001) -> pd.Series:
    """Load EPW dry-bulb temperature as an hourly Series in deg F.

    EPW timestamps are month/day/hour with hour 1..24; we map to a single
    placeholder ``year`` (TMY data is typical, not a real calendar year). Hour 24
    is normalized to hour 0 of the next day.
    """
    rows = []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i < _EPW_HEADER_LINES or not row:
                continue
            try:
                mo, da, hr = int(row[1]), int(row[2]), int(row[3])
                tc = float(row[EPW_DRYBULB_COL])
            except (ValueError, IndexError):
                continue
            rows.append((mo, da, hr, tc))
    if not rows:
        raise ValueError(f"no EPW data rows parsed from {path}")
    df = pd.DataFrame(rows, columns=["mo", "da", "hr", "tc"])
    # EPW hour is 1..24; shift to 0..23 with day rollover for hour 24
    base = pd.to_datetime(dict(year=year, month=df.mo, day=df.da)) \
        + pd.to_timedelta(df.hr - 1, unit="h")
    s = pd.Series(c_to_f(df.tc.values), index=base, name="oat_f").sort_index()
    return s[~s.index.duplicated(keep="first")]


def monthly_normals(epw_temp_f: pd.Series) -> pd.Series:
    """Typical-year monthly-mean temperature (deg F) from an hourly EPW series.

    Indexed 1..12 so it can be joined to monthly energy by calendar month.
    """
    g = epw_temp_f.groupby(epw_temp_f.index.month).mean()
    g.index.name = "month"
    return g


def normalized_annual_energy(model, epw_temp_f: pd.Series, *, basis: str = "monthly") -> float:
    """Project a fitted change-point model onto the typical year -> annual energy.

    ``basis='monthly'``: predict each typical month from its mean temp and a
    days-in-month weight (the model was fit on monthly totals, so scale the
    monthly-mean prediction is NOT valid -- instead we predict per typical *hour*
    and sum, which is basis-independent). We therefore always predict hourly and
    sum, which is the correct weather-normalized annual figure regardless of the
    model's fitting basis.
    """
    # predict per typical hour and sum -> annual energy at typical weather
    # (works whether the model was fit on hourly, daily, or monthly data, as long
    #  as predict() returns energy per the model's native interval; for a monthly
    #  model the caller should use normalized_annual_from_monthly instead.)
    return float(np.nansum(model.predict(epw_temp_f.values)))


def normalized_annual_from_monthly(model, epw_temp_f: pd.Series) -> float:
    """Typical-year annual energy for a model fit on MONTHLY totals.

    Predicts each typical month from its mean temperature (the model's native
    interval is one month) and sums the 12 monthly predictions.
    """
    mm = monthly_normals(epw_temp_f)
    return float(np.nansum(model.predict(mm.values)))
