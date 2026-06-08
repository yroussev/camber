"""Tests for water analysis (water.py). Targets match the skill's worked examples."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.water import (  # noqa: E402
    effective_precip, evaporation_gpm, flow_duration, gallons_per_ton_hour,
    irrigation_budget, leak_impact, makeup_gpm, min_night_flow,
)


# --- irrigation -------------------------------------------------------------- #

def test_effective_precip_capped_by_eto():
    assert abs(effective_precip(0.2, 8.0) - 0.15) < 1e-9   # min(0.2*0.75, 8)
    assert effective_precip(20.0, 8.0) == 8.0              # capped at ETo


def test_irrigation_budget_worked_example():
    # 50,000 sf, KL 0.7, eff 0.70, ETo 8 in, rain 0.2 in
    b = irrigation_budget(eto_in=8.0, area_sf=50000, actual_gallons=280000,
                          landscape_coeff=0.7, efficiency=0.70, rain_in=0.2)
    assert abs(b.required_inches - 7.79) < 0.01
    assert abs(b.required_gallons - 242575) < 100      # ~242.6k gal
    assert abs(b.overage_pct - 15.4) < 0.3             # ~15% over budget


# --- cooling tower ----------------------------------------------------------- #

def test_cooling_tower_makeup_and_cycles():
    assert abs(evaporation_gpm(500) - 1.5) < 1e-9      # 500*3/1000
    assert abs(makeup_gpm(500, 4) - 2.0) < 1e-6        # 1.5 / (1 - 1/4)
    assert abs(makeup_gpm(500, 6) - 1.8) < 0.01        # raising cycles cuts makeup


def test_gallons_per_ton_hour():
    assert gallons_per_ton_hour(makeup_gallons=300.0, ton_hours=10000.0) == 0.03


# --- leak detection ---------------------------------------------------------- #

def test_leak_impact_one_gpm():
    li = leak_impact(1.0, rate_per_ccf=12.0)
    assert li.gallons_per_day == 1440.0
    assert li.gallons_per_month == 43200.0
    assert abs(li.cost_per_month - 43200 / 748 * 12) < 0.1
    assert abs(li.cost_per_year - li.cost_per_month * 12) < 0.1


def test_min_night_flow_picks_overnight_minimum():
    idx = pd.date_range("2024-06-01", periods=24, freq="1h")
    # high daytime use, a 0.3 GPM floor overnight (a leak)
    vals = [0.3 if h < 6 else 5.0 for h in idx.hour]
    s = pd.Series(vals, index=idx)
    assert min_night_flow(s, night=(2, 6)) == 0.3


def test_flow_duration_sorted_descending():
    s = pd.Series([1.0, 5.0, 3.0])
    fd = flow_duration(s)
    assert list(fd) == [5.0, 3.0, 1.0]
