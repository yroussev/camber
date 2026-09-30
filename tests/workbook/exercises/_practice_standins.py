"""Stand-ins shared by the #83 practice exercises (data-energy-charting, mv-baselines).

Whole-building meters shaped like the real ingests: same facility ids, equipment ids, classes and
roles. The behaviours they encode, one line each:

- outdoor air: an annual sinusoid (coldest mid-January) plus a daily swing and seeded noise;
- a lodging building runs around the clock: flat electricity, day and night, every day, with a
  month-to-month level that follows its occupancy rather than the weather;
- an assembly building on the same campus barely sets back: a small daytime bump, 7 days a week;
- its chilled water is cooling-driven: zero below a ~60 F balance point, rising with OAT above it;
- a university building follows the working week: a daytime plateau on weekdays only, and a
  weekday load that grows a little in hot and cold weather;
- a Norwegian school follows the week and the heating season (electric heat below ~55 F), and
  in 2020 its weekday daytime load halves from 12 March to 11 May (the closure); one school
  steps down 6 % from 2019 without a documented reason;
- a nursing home runs every day, heating-driven, and has no closure.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import write_standin

from camber.model.roles import Role


def _oat(idx: pd.DatetimeIndex, rng, *, mean=55.0, swing=25.0, daily=8.0) -> np.ndarray:
    doy = idx.dayofyear.to_numpy()
    h = idx.hour.to_numpy()
    return (
        mean
        - swing * np.cos((doy - 15) / 365.25 * 2 * np.pi)
        + daily * np.sin((h - 9) / 24 * 2 * np.pi)
        + rng.normal(0, 2.0, len(idx))
    )


def bdg2_fox(store, *, start="2016-01-01", end="2017-12-31 23:00") -> None:
    """ds-bdg2-fox: its weather, two lodging meters (electricity, chilled water) and one assembly
    building's electricity, hourly 2016-2017."""
    rng = np.random.default_rng(1)
    idx = pd.date_range(start, end, freq="1h")
    n = len(idx)
    oat = _oat(idx, rng, mean=72.0, swing=18.0, daily=10.0)
    h = idx.hour.to_numpy()
    month = (idx.year - idx.year[0]) * 12 + idx.month - 1
    occupancy = rng.normal(1.0, 0.06, month.max() + 1)[month]  # guests per month, not weather
    lodging_el = 80.0 * occupancy + 0.05 * np.maximum(oat - 75.0, 0) + rng.normal(0, 6.0, n)
    daytime = ((h >= 10) & (h < 20)).astype(float)
    assembly_el = 12.0 + 3.0 * daytime + rng.normal(0, 1.0, n)
    daily_oat = pd.Series(oat, index=idx).groupby(idx.normalize()).transform("mean").to_numpy()
    chw = 30.0 * np.maximum(daily_oat - 60.0, 0.0) + rng.normal(0, 60.0, n)
    frames = {
        "weather": ("WEATHER", pd.DataFrame({Role.OAT: oat}, index=idx)),
        "Fox_lodging_Stephen__electricity": (
            "ELECTRICITY_METER",
            pd.DataFrame({Role.POWER: lodging_el}, index=idx),
        ),
        "Fox_lodging_Stephen__chilledwater": (
            "CHILLEDWATER_METER",
            pd.DataFrame({Role.ENERGY_RATE: np.maximum(chw, 0.0)}, index=idx),
        ),
        "Fox_assembly_Audrey__electricity": (
            "ELECTRICITY_METER",
            pd.DataFrame({Role.POWER: assembly_el}, index=idx),
        ),
    }
    write_standin(store, "bdg2", frames, facility_id="ds-bdg2-fox")


def valladolid(store, *, start="2016-01-01", end="2018-12-31 23:00") -> None:
    """ds-valladolid-uva: two university buildings and the shared weather, hourly 2016-2018.

    UVA_B uses 12 % less from 2018 on (the equipment replacement the publisher describes);
    UVA_A is unchanged."""
    rng = np.random.default_rng(2)
    idx = pd.date_range(start, end, freq="1h")
    n = len(idx)
    oat = _oat(idx, rng, mean=54.0, swing=16.0)
    h, dow = idx.hour.to_numpy(), idx.dayofweek.to_numpy()
    day = ((h >= 8) & (h < 20) & (dow < 5)).astype(float)
    weather = 1.0 + 0.004 * np.abs(oat - 60.0)
    b_factor = np.where(idx.year >= 2018, 0.88, 1.0)
    uva_a = 120.0 + 150.0 * day * weather + rng.normal(0, 12.0, n)
    uva_b = (50.0 + 95.0 * day * weather) * b_factor + rng.normal(0, 8.0, n)
    frames = {
        "weather": ("WEATHER", pd.DataFrame({Role.OAT: oat}, index=idx)),
        "UVA_A": ("ELECTRICITY_METER", pd.DataFrame({Role.POWER: uva_a}, index=idx)),
        "UVA_B": ("ELECTRICITY_METER", pd.DataFrame({Role.POWER: uva_b}, index=idx)),
    }
    write_standin(store, "valladolid-uva", frames)


def cofactor(store, *, start="2018-01-01", end="2020-12-31 23:00") -> None:
    """ds-cofactor-drammen: two schools and a nursing home (ElImp, with each meter's own OAT),
    hourly 2018-2020; the schools' weekday daytime load halves during the spring 2020 closure.
    School b6400 uses 6 % less from 2019 on, a change no one documented."""
    rng = np.random.default_rng(3)
    idx = pd.date_range(start, end, freq="1h")
    n = len(idx)
    oat = _oat(idx, rng, mean=44.0, swing=20.0)
    h, dow = idx.hour.to_numpy(), idx.dayofweek.to_numpy()
    day = ((h >= 7) & (h < 16) & (dow < 5)).astype(float)
    heat = np.maximum(55.0 - oat, 0.0)
    closed = (idx >= pd.Timestamp("2020-03-12")) & (idx < pd.Timestamp("2020-05-11"))
    frames = {}
    for eq, base, dayload, k in (
        ("b6400_ElImp", 40.0, 90.0, 1.2),
        ("b6404_ElImp", 30.0, 70.0, 0.9),
    ):
        school_day = np.where(closed, 0.5, 1.0) * dayload * day
        drop = np.where((eq == "b6400_ElImp") & (idx.year >= 2019), 0.94, 1.0)
        p = (base + school_day + k * heat) * drop + rng.normal(0, 5.0, n)
        frames[eq] = (
            "ELECTRICITY_METER",
            pd.DataFrame({Role.POWER: p, Role.OAT: oat}, index=idx),
        )
    home = 60.0 + 10.0 * ((h >= 7) & (h < 22)) + 1.5 * heat + rng.normal(0, 4.0, n)
    frames["b6410_ElImp"] = (
        "ELECTRICITY_METER",
        pd.DataFrame({Role.POWER: home, Role.OAT: oat}, index=idx),
    )
    write_standin(store, "cofactor-drammen", frames)
