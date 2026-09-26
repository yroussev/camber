"""Regression tests for the terminal-box reheat penalty (``reheat_penalty``) found on real data:
the terminal air-temperature convention and the trended-occupancy schedule."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.reheat import analyze_box  # noqa: E402
from camber.rules.reheat_rule import ReheatPenalty  # noqa: E402


def _box(n_days=14):
    idx = pd.date_range("2025-01-06", periods=n_days * 24, freq="1h")  # Monday
    # reheat 50 % open all the time; the box is fed 55 F primary air and discharges 85 F
    return idx, pd.DataFrame(
        {
            Role.HEAT_VALVE: np.full(len(idx), 50.0),
            Role.MIXED_AIR_TEMP: np.full(len(idx), 55.0),  # entering primary air
            Role.SUPPLY_AIR_TEMP: np.full(len(idx), 85.0),  # box discharge air
        },
        index=idx,
    )


def test_entering_primary_air_is_judged_not_the_discharge():
    # real case: the natural point at a box is its discharge temp; mapped as SUPPLY_AIR_TEMP it
    # read "85 F = warm supply" and hid a reheat-into-55-F-primary penalty entirely
    _, f = _box()
    got = ReheatPenalty().analyze("VAV", f)
    assert got.metrics["coldsupply_basis"] == "primary"
    assert got.metrics["reheat_and_coldsupply_pct"] > 95 and got.severity == "fault"
    assert not got.caveats


def test_discharge_only_is_caveated_and_never_a_confident_ok():
    _, f = _box()
    discharge_only = f.drop(columns=[Role.MIXED_AIR_TEMP])
    got = ReheatPenalty().analyze("VAV", discharge_only)
    assert got.metrics["coldsupply_basis"] == "supply"
    assert got.severity == "info" and got.metrics["reheat_and_coldsupply_pct"] is None
    assert any("discharge" in c and "MIXED_AIR_TEMP" in c for c in got.caveats)
    # an existing mapping that put the (cold) primary air on SUPPLY_AIR_TEMP still faults
    legacy = f.drop(columns=[Role.MIXED_AIR_TEMP]).assign(**{Role.SUPPLY_AIR_TEMP: 55.0})
    old = ReheatPenalty().analyze("VAV", legacy)
    assert old.severity == "fault" and any("discharge" in c for c in old.caveats)


def test_no_air_temperature_and_no_oat_declines():
    idx, f = _box()
    got = ReheatPenalty().analyze("VAV", f[[Role.HEAT_VALVE]])
    assert got.severity == "info" and got.metrics["reheat_and_coldsupply_pct"] is None


def test_trended_occupancy_replaces_the_weekday_schedule():
    # reheat into cold primary air only on weekend evenings, when a 07-22 building is occupied
    idx, f = _box()
    eve = (idx.dayofweek >= 5) & (idx.hour >= 18) & (idx.hour < 22)
    f[Role.HEAT_VALVE] = np.where(eve, 50.0, 0.0)
    assert ReheatPenalty().analyze("VAV", f).metrics["valve_open_pct"] == 0.0
    occ = ((idx.hour >= 7) & (idx.hour < 22)).astype(float)
    got = ReheatPenalty().analyze("VAV", f.assign(**{Role.OCCUPANCY: occ}))
    assert got.metrics["valve_open_pct"] > 5
    cfg = ReheatPenalty(start_hour=7, end_hour=22, occupied_days=range(7))
    assert cfg.analyze("VAV", f).metrics["valve_open_pct"] > 5
    legacy = f.rename(columns={Role.HEAT_VALVE: "HWValve"}).assign(Occupancy=occ)
    assert analyze_box(legacy, "VAV").valve_open_pct > 5
