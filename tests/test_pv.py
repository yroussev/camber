"""Tests for PV monitoring (pv.py)."""

import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.pv import (  # noqa: E402
    expected_generation, net_energy, performance_ratio, pv_summary, specific_yield,
)


def test_performance_ratio():
    # E_ac=8 kWh, POA=10 kWh/m2, rated=1 kW, G_ref=1 kW/m2 -> PR = 0.8
    assert performance_ratio(8.0, 10.0, 1.0) == 0.8


def test_performance_ratio_guards_zero():
    assert math.isnan(performance_ratio(8.0, 10.0, 0.0))
    assert math.isnan(performance_ratio(8.0, 0.0, 1.0))


def test_specific_yield_and_expected():
    assert specific_yield(1500.0, 1.0) == 1500.0
    assert expected_generation(10.0, 1.0, performance_ratio=0.8) == 8.0


def test_net_energy_import_and_export():
    idx = pd.date_range("2024-01-01", periods=2, freq="h")
    load = pd.Series([100.0, 20.0], index=idx)
    gen = pd.Series([30.0, 30.0], index=idx)
    assert list(net_energy(load, gen)) == [70.0, -10.0]


def test_pv_summary_with_self_consumption():
    idx = pd.date_range("2024-06-01", periods=4, freq="h")
    ac = pd.Series([2.0, 2.0, 2.0, 2.0], index=idx)          # 8 kWh total
    load = pd.Series([5.0, 5.0, 1.0, 1.0], index=idx)
    r = pv_summary(ac, poa_irradiation_kwh_m2=10.0, rated_kw=1.0, load=load)
    assert r.generation_kwh == 8.0
    assert r.performance_ratio == 0.8
    assert r.specific_yield == 8.0
    # on-site = min(load,gen) per hour = 2+2+1+1 = 6 of 8 -> 75%
    assert r.self_consumption_pct == 75.0
