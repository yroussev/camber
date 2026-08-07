"""Coverage-lifting tests for flat-analyzer gaps: the uncalled ``as_dict()`` serializers
and a couple of guard branches (short-series change detection, missing fan-signal setback).

Inputs mirror the per-analyzer test builders; each result's ``as_dict()`` must round-trip to
a plain dict so downstream JSON/report layers can serialize it.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.boilercycle import analyze_boiler_cycling  # noqa: E402
from camber.changedetect import _best_split, detect_level_shifts  # noqa: E402
from camber.chwplant import analyze_chw_plant  # noqa: E402
from camber.condenserwater import analyze_cw_reset  # noqa: E402
from camber.coolingtower import analyze_cooling_tower_approach  # noqa: E402
from camber.freecooling import free_cooling_opportunity  # noqa: E402
from camber.iaq import analyze_co2_ventilation  # noqa: E402
from camber.oafraction import analyze_oa_fraction  # noqa: E402
from camber.setback import analyze_setback  # noqa: E402


def _idx(n, start="2025-07-07"):
    return pd.date_range(start, periods=n, freq="1h")


def _wetbulb(n):
    h = np.arange(n) % 24
    return 66.0 + 11.0 * np.sin((h - 9) / 24 * 2 * np.pi)


def test_boiler_cycling_result_as_dict():
    n = 24 * 7
    r = analyze_boiler_cycling(
        pd.DataFrame({"BoilerStatus": np.tile([1.0, 0.0], n // 2)}, index=_idx(n)), "BLR-1"
    )
    d = r.as_dict()
    assert isinstance(d, dict) and d["starts_per_day"] == r.starts_per_day


def test_chw_plant_result_as_dict():
    n = 24 * 14
    df = pd.DataFrame(
        {
            "CHWS_Temp": np.full(n, 44.0),
            "CHWR_Temp": np.full(n, 55.0),
            "OAT": np.full(n, 85.0),
        },
        index=_idx(n),
    )
    d = analyze_chw_plant(df, "CHW").as_dict()
    assert isinstance(d, dict) and d["chwst_median_f"] == 44.0


def test_cw_reset_result_as_dict():
    n = 24 * 14
    wb = _wetbulb(n)
    df = pd.DataFrame({"CWS_Temp": wb + 7.0, "CWR_Temp": wb + 17.0, "WetBulb": wb}, index=_idx(n))
    d = analyze_cw_reset(df, "CT-1").as_dict()
    assert isinstance(d, dict)


def test_cooling_tower_result_as_dict():
    n = 24 * 14
    wb = _wetbulb(n)
    df = pd.DataFrame({"CWS_Temp": wb + 6.0, "CWR_Temp": wb + 16.0, "WetBulb": wb}, index=_idx(n))
    d = analyze_cooling_tower_approach(df, "CT-1").as_dict()
    assert isinstance(d, dict)


def test_free_cooling_result_as_dict():
    idx = _idx(24 * 20, start="2025-04-01")
    oat = pd.Series(55 + 15 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24), index=idx)
    cool = pd.Series(np.where((idx.hour >= 8) & (idx.hour <= 18), 0.6, 0.0), index=idx)
    kw = pd.Series(np.where(cool > 0, 50.0, 0.0), index=idx)
    r = free_cooling_opportunity(oat, cool, cooling_kw=kw, recover_frac=0.7, price_per_kwh=0.15)
    assert isinstance(r.as_dict(), dict)


def test_co2_ventilation_result_as_dict():
    idx = _idx(24 * 20, start="2025-06-02")
    co2 = np.where(idx.hour == 12, 1300.0, 700.0)
    d = analyze_co2_ventilation(pd.DataFrame({"CO2": co2}, index=idx), "ZONE-1").as_dict()
    assert isinstance(d, dict) and "under_vent_pct" in d


def test_oa_fraction_result_as_dict():
    n = 24 * 14
    df = pd.DataFrame(
        {
            "OAT": np.full(n, 50.0),
            "MixedAir": np.full(n, 70.0),  # OAF = (75-70)/(75-50) = 20% -> physical range
            "ReturnAir": np.full(n, 75.0),
        },
        index=_idx(n),
    )
    r = analyze_oa_fraction(df, "AHU-1")
    assert r is not None and isinstance(r.as_dict(), dict)


def test_setback_result_as_dict():
    n = 24 * 14
    status = (np.arange(n) % 4 < 1).astype(float)
    r = analyze_setback(pd.DataFrame({"SupplyFanStatus": status}, index=_idx(n)), "AHU-1")
    assert r is not None and isinstance(r.as_dict(), dict)


def test_setback_none_without_fan_signal():
    n = 48
    # neither SupplyFanStatus nor SupplyFanSpeed -> _running returns None -> analyze None
    r = analyze_setback(pd.DataFrame({"SpaceTemp": np.full(n, 72.0)}, index=_idx(n)), "AHU-1")
    assert r is None


def test_changedetect_short_series_no_split():
    # series far shorter than 2*min_segment -> the split guard returns no shifts
    s = pd.Series(np.arange(20.0), index=_idx(20))
    assert detect_level_shifts(s) == []


def test_best_split_too_short_returns_none():
    # a segment with no room for both sides at min_segment -> the (None, 0.0) guard
    k, score = _best_split(np.array([1.0]), min_segment=1)
    assert k is None and score == 0.0
