"""Tests for utility cost accounting (cost.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cost import (  # noqa: E402
    EnergyRate, energy_cost, marginal_rate, tiered_cost, tou_energy_cost, water_cost,
)


def test_energy_cost_components():
    r = EnergyRate(energy_rate=0.12, demand_rate=15.0, fixed=100.0)
    assert energy_cost(1000, rate=r, peak_demand=50) == 970.0  # 120 + 750 + 100


def test_tiered_cost_spills_into_upper_block():
    tiers = [(100, 2.0), (None, 3.0)]
    assert tiered_cost(150, tiers) == 350.0             # 100*2 + 50*3
    assert tiered_cost(80, tiers) == 160.0              # all in tier 1


def test_marginal_rate_is_current_block():
    tiers = [(100, 2.0), (None, 3.0)]
    assert marginal_rate(50, tiers) == 2.0
    assert marginal_rate(150, tiers) == 3.0


def test_water_cost_sewer_on_indoor_only():
    bill = water_cost(100, supply_tiers=[(None, 3.0)],
                      sewer_tiers=[(None, 4.0)], indoor_fraction=0.6, fixed=10.0)
    assert bill.supply == 300.0
    assert bill.wastewater == 240.0                     # 100*0.6*4
    assert bill.total == 550.0
    assert bill.avg_cost_per_unit == 5.5
    # marginal = supply 3.0 + sewer 4.0 on the 0.6 indoor share
    assert abs(bill.marginal_cost_per_unit - 5.4) < 1e-9


def test_tou_overrides_energy_rate():
    r = EnergyRate(energy_rate=0.10, tou={17: 0.50})
    # two hours: one off-peak (h=2 -> 0.10), one on-peak (h=17 -> 0.50)
    assert tou_energy_cost([100, 100], [2, 17], r) == 60.0
