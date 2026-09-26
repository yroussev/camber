"""Tests for schedules + zones (fleet heating-vs-cooling census)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.schedules import day_type, occupied_mask, time_of_week_bin  # noqa: E402
from camber.zones import time_of_week_profile, zone_states  # noqa: E402


def _idx(n=48):
    # start Monday 2025-07-07 00:00
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_occupied_mask_weekday_window():
    idx = _idx(48)
    m = occupied_mask(idx)
    # hour 10 Monday occupied, hour 3 not, hour 10 Saturday not
    assert m.loc[pd.Timestamp("2025-07-07 10:00")]
    assert not m.loc[pd.Timestamp("2025-07-07 03:00")]


def test_day_type():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    dt = day_type(idx)
    assert dt.loc[pd.Timestamp("2025-07-07 12:00")] == "weekday"  # Monday
    assert dt.loc[pd.Timestamp("2025-07-12 12:00")] == "weekend"  # Saturday


def test_time_of_week_bin_range():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    b = time_of_week_bin(idx)
    assert b.min() == 0
    assert b.max() == 6 * 24 + 23


def test_zone_states_counts():
    idx = _idx(24)
    # zone A: heating on (valve 50), no cooling flow
    a = pd.DataFrame({"HWValve": 50.0, "ActFlow": 100.0, "ActFlowSP": 200.0}, index=idx)
    # zone B: cooling (flow above SP), no heating
    b = pd.DataFrame({"HWValve": 0.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    # zone C: BOTH heating and cooling (reheat penalty)
    c = pd.DataFrame({"HWValve": 40.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    st = zone_states({"A": a, "B": b, "C": c})
    assert (st["n_zones"] == 3).all()
    assert (st["n_heating"] == 2).all()  # A and C
    assert (st["n_cooling"] == 2).all()  # B and C
    assert (st["n_both"] == 1).all()  # C only


def test_time_of_week_profile_shape():
    idx = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")
    a = pd.DataFrame({"HWValve": 50.0, "ActFlow": 400.0, "ActFlowSP": 200.0}, index=idx)
    st = zone_states({"A": a})
    prof = time_of_week_profile(st, occupied_only=True)
    assert not prof.empty
    assert set(["n_zones", "n_heating", "n_cooling", "n_both"]).issubset(prof.columns)


def test_zones_census_uses_trended_occupancy_over_the_weekday_schedule():
    # simultaneous heat/cool only on weekend evenings, when a 07-22 every-day building is occupied
    import numpy as np

    from camber.model.roles import Role
    from camber.rules.zones_rule import ZonesHeatCoolCensus

    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")  # Monday
    eve = (idx.dayofweek >= 5) & (idx.hour >= 18) & (idx.hour < 22)
    occ = ((idx.hour >= 7) & (idx.hour < 22)).astype(float)

    def zone(heating):
        return pd.DataFrame(
            {
                Role.HEAT_VALVE: np.where(eve & heating, 60.0, 0.0),
                Role.AIRFLOW: np.where(eve & ~heating, 900.0, 300.0),
                Role.AIRFLOW_SP: np.full(len(idx), 300.0),
                Role.SPACE_TEMP: np.full(len(idx), 72.0),
                Role.COOL_SP: np.full(len(idx), 75.0),
            },
            index=idx,
        )

    frames = {"Z1": zone(True), "Z2": zone(False)}
    base = ZonesHeatCoolCensus().analyze_fleet(frames)
    with_occ = ZonesHeatCoolCensus().analyze_fleet(
        {k: f.assign(**{Role.OCCUPANCY: occ}) for k, f in frames.items()}
    )
    cfg = ZonesHeatCoolCensus(start_hour=7, end_hour=22, occupied_days=range(7))
    assert with_occ.metrics["n_zones"] == 2
    assert with_occ.metrics["avg_zones_heating"] > base.metrics["avg_zones_heating"] == 0.0
    assert cfg.analyze_fleet(frames).metrics["avg_zones_heating"] > 0.0


def test_effective_occupied_mask_prefers_a_trended_point():
    from camber.schedules import effective_occupied_mask

    idx = pd.date_range("2025-07-12", periods=48, freq="1h")  # Saturday
    occ = pd.Series(((idx.hour >= 7) & (idx.hour < 22)).astype(float), index=idx)
    assert not effective_occupied_mask(idx).any()  # default weekday schedule: weekend is empty
    got = effective_occupied_mask(idx, occ=occ)
    assert got.sum() == 30 and got.equals(occ > 0.5)
    # an all-NaN point is no point: fall back to the schedule
    assert effective_occupied_mask(idx, occ=occ * float("nan"), days=range(7)).sum() == 22
    warm = pd.Series((idx.hour == 7).astype(float), index=idx)
    assert effective_occupied_mask(idx, occ=occ, warmup=warm).sum() == 28
