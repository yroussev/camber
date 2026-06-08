"""Tests for the migrated rules: reheat, satreset (single) + zones (fleet)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding, FleetRule, Registry, Rule  # noqa: E402
from camber.rules.reheat_rule import ReheatPenalty  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402
from camber.rules.zones_rule import ZonesHeatCoolCensus  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday start


# ---- reheat ----

def test_reheat_rule_protocol():
    assert isinstance(ReheatPenalty(), Rule)


def test_reheat_fault_at_high_oat():
    n = 24 * 21
    idx = _idx(n)
    hour = idx.hour
    # reheat valve open during occupied afternoons; OAT hot then
    hw = np.where((hour >= 12) & (hour < 17), 40.0, 0.0)
    oat = np.where((hour >= 12) & (hour < 17), 95.0, 70.0)
    frame = pd.DataFrame({Role.HEAT_VALVE: hw, Role.OAT: oat}, index=idx)
    f = ReheatPenalty().analyze("VAV_1", frame)
    assert f.metrics["reheat_at_high_oat_pct"] > 20
    assert f.severity == "fault"


def test_reheat_ok_when_no_reheat():
    n = 24 * 21
    frame = pd.DataFrame({Role.HEAT_VALVE: np.zeros(n),
                          Role.OAT: np.full(n, 95.0)}, index=_idx(n))
    f = ReheatPenalty().analyze("VAV_2", frame)
    assert f.severity == "ok"


# ---- satreset ----

def test_satreset_rule_protocol():
    assert isinstance(SupplyAirReset(), Rule)


def test_satreset_warns_when_cold_and_flat():
    n = 24 * 30
    idx = _idx(n)
    rng = np.random.default_rng(0)
    oat = 90 + 12 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    sat = 55 + rng.normal(0, 0.4, n)          # pinned cold, no reset
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.COOL_VALVE: np.full(n, 60.0),
                          Role.OAT: oat}, index=idx)
    f = SupplyAirReset().analyze("AHU_1", frame)
    assert f.metrics["pct_sat_below_58"] > 50
    assert f.severity == "warn"


def test_satreset_ok_when_resetting_up():
    n = 24 * 30
    idx = _idx(n)
    rng = np.random.default_rng(1)
    oat = 90 + 12 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    sat = np.clip(55 + 0.25 * (oat - 75) + rng.normal(0, 0.5, n), 53, 65)
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.COOL_VALVE: np.full(n, 60.0),
                          Role.OAT: oat}, index=idx)
    f = SupplyAirReset().analyze("AHU_2", frame)
    assert f.metrics["slope_per_F"] > 0.10
    assert f.severity == "ok"


# ---- zones (fleet) ----

def test_zones_is_fleet_rule():
    assert isinstance(ZonesHeatCoolCensus(), FleetRule)


def test_zones_fleet_detects_simultaneous():
    n = 24 * 14
    idx = _idx(n)
    # zone A always heating; zone B always cooling (flow above setpoint)
    a = pd.DataFrame({Role.HEAT_VALVE: np.full(n, 40.0),
                      Role.AIRFLOW: np.full(n, 400.0),
                      Role.AIRFLOW_SP: np.full(n, 200.0)}, index=idx)  # A both
    b = pd.DataFrame({Role.HEAT_VALVE: np.zeros(n),
                      Role.AIRFLOW: np.full(n, 400.0),
                      Role.AIRFLOW_SP: np.full(n, 200.0)}, index=idx)  # B cooling only
    f = ZonesHeatCoolCensus().analyze_fleet({"VAV_A": a, "VAV_B": b})
    assert f.equip == "<fleet>"
    assert f.metrics["avg_zones_both"] >= 1.0
    assert f.metrics["pct_hours_any_both"] > 75
    assert f.severity == "fault"


def test_zones_fleet_empty():
    f = ZonesHeatCoolCensus().analyze_fleet({})
    assert f.severity == "info"


def test_registry_holds_all_migrated():
    reg = Registry()
    for r in (ReheatPenalty(), SupplyAirReset(), ZonesHeatCoolCensus()):
        reg.register(r)
    assert set(reg.names()) == {"reheat_penalty", "supply_air_reset",
                                "zones_heat_cool_census"}
