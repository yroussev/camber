"""Water-source heat-pump operating-mode rules and the source-loop delta-T check (0.93, #40)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.heatpump_ops_rule import (  # noqa: E402
    HPCapacityShortfall,
    HPModeVsNeed,
    HPRoomImbalance,
    infer_hp_mode,
)
from camber.rules.source_loop_rule import SourceLoopDeltaT  # noqa: E402

_IDX = pd.date_range("2026-01-05", periods=14 * 24 * 12, freq="5min")  # two weeks, Monday start
_OCC = (_IDX.dayofweek < 5) & (_IDX.hour >= 7) & (_IDX.hour < 18)


def _hp(zat, dat, *, occupancy=True, **extra):
    n = len(_IDX)
    cols = {
        Role.SPACE_TEMP: np.broadcast_to(zat, n).astype(float),
        Role.SUPPLY_AIR_TEMP: np.broadcast_to(dat, n).astype(float),
    }
    if occupancy:
        cols[Role.OCCUPANCY] = _OCC.astype(float)
    for k, v in extra.items():
        cols[Role(k)] = np.broadcast_to(v, n).astype(float)
    return pd.DataFrame(cols, index=_IDX)


def test_infer_mode_from_discharge_and_zone():
    f = _hp(
        np.array([70.0, 70.0, 70.0, 70.0] * (len(_IDX) // 4)),
        np.array([95.0, 56.0, 72.0, np.nan] * (len(_IDX) // 4)),
    )
    m = infer_hp_mode(f)
    assert m.iloc[:4].tolist()[:3] == [1.0, -1.0, 0.0] and np.isnan(m.iloc[3])
    f[Role.SUPPLY_FAN_STATUS] = 0.0
    assert (infer_hp_mode(f).dropna() == 0).all()  # no air, no delivered mode


def test_infer_mode_from_status_points():
    f = _hp(
        70.0,
        70.0,
        compressor_status=np.tile([1.0, 1.0, 0.0], len(_IDX) // 3),
        reversing_valve_cmd=np.tile([1.0, 0.0, 1.0], len(_IDX) // 3),
    )
    assert infer_hp_mode(f).iloc[:3].tolist() == [1.0, -1.0, 0.0]
    assert infer_hp_mode(pd.DataFrame({Role.OAT: [1.0]})).isna().all()


def test_mode_vs_need_flags_cooling_a_cold_room():
    morning = (_IDX.hour >= 7) & (_IDX.hour < 10)
    bad = _hp(np.where(morning, 64.0, 70.0), np.where(morning, 52.0, 70.0))
    f = HPModeVsNeed().analyze("HP1", bad)
    assert f.severity == "fault"
    assert f.metrics["cooling_while_cold_hours"] > 20 and "cooling a room" in f.summary
    assert any("assumed comfort band" in c for c in f.caveats)
    hot = _hp(np.where(morning, 80.0, 72.0), np.where(morning, 100.0, 72.0))
    g = HPModeVsNeed().analyze("HP1", hot)
    assert g.severity == "fault" and "heating a room" in g.summary
    good = _hp(np.where(morning, 66.0, 70.0), np.where(morning, 95.0, 70.0))
    assert HPModeVsNeed().analyze("HP1", good).severity == "ok"


def test_mode_vs_need_uses_mapped_setpoints_and_declines_without_data():
    morning = (_IDX.hour >= 7) & (_IDX.hour < 10)
    bad = _hp(
        np.where(morning, 64.0, 70.0), np.where(morning, 52.0, 70.0), heat_sp=62.0, cool_sp=78.0
    )
    f = HPModeVsNeed().analyze("HP1", bad)
    assert f.severity == "ok" and not any("assumed" in c for c in f.caveats)
    empty = _hp(np.nan, np.nan)
    assert HPModeVsNeed().analyze("HP1", empty).metrics["declined"]


def test_capacity_shortfall_capacity_vs_control():
    cold_heating = _hp(np.where(_OCC, 64.0, 66.0), np.where(_OCC, 92.0, 66.0))
    f = HPCapacityShortfall().analyze("HP1", cold_heating)
    assert f.severity == "fault" and f.metrics["verdict"] == "capacity"
    assert f.metrics["side"] == "heating" and "capacity or airflow" in f.summary
    cold_idle = _hp(np.where(_OCC, 64.0, 66.0), 66.0)
    g = HPCapacityShortfall().analyze("HP1", cold_idle)
    assert g.metrics["verdict"] == "control" and "control problem" in g.summary
    hot_cooling = _hp(np.where(_OCC, 80.0, 75.0), np.where(_OCC, 58.0, 75.0))
    h = HPCapacityShortfall().analyze("HP1", hot_cooling)
    assert h.metrics["side"] == "cooling" and h.metrics["verdict"] == "capacity"
    fine = _hp(70.0, np.where(_OCC, 92.0, 70.0))
    assert HPCapacityShortfall().analyze("HP1", fine).severity == "ok"
    assert HPCapacityShortfall().analyze("HP1", _hp(np.nan, np.nan)).metrics["declined"]


def test_capacity_shortfall_mixed_and_no_occupancy_default():
    half = np.where(_IDX.minute < 30, 92.0, 64.0)
    mixed = _hp(np.where(_OCC, 64.0, 70.0), np.where(_OCC, half, 70.0), occupancy=False)
    f = HPCapacityShortfall().analyze("HP1", mixed)
    assert f.metrics["verdict"] == "mixed" and f.severity == "warn"
    assert any("no occupancy point" in c for c in f.caveats)


def _room_units(fight: bool):
    a = _hp(70.0, np.where(_OCC, 95.0, 70.0))
    b = _hp(70.0, np.where(_OCC, 55.0 if fight else 95.0, 70.0))
    return {"HP1_A101": a, "HP2_A101": b, "HP3_B200": _hp(70.0, 70.0)}


def test_room_imbalance_from_rooms_and_topology():
    rooms = {"A101": ["HP1_A101", "HP2_A101"], "B200": ["HP3_B200"]}
    f = HPRoomImbalance(rooms=rooms).analyze_fleet(_room_units(True))
    assert f.severity == "fault" and f.metrics["n_rooms"] == 1
    assert f.metrics["rooms"]["A101"]["fight_pct"] > 90
    ok = HPRoomImbalance(rooms=rooms).analyze_fleet(_room_units(False))
    assert ok.severity == "ok"
    topo = Topology.from_parent_map({"HP1_A101": "A101", "HP2_A101": "A101", "HP3_B200": "B200"})
    t = HPRoomImbalance().analyze_fleet(_room_units(True), topology=topo)
    assert t.severity == "fault"


def test_room_imbalance_duty_spread_and_sensor_spread():
    units = {
        "HP1": _hp(70.0, np.where(_OCC, 95.0, 70.0)),
        "HP2": _hp(74.5, 74.5),  # idle while its twin works, and its sensor reads 4.5 F warmer
    }
    f = HPRoomImbalance(rooms={"R": ["HP1", "HP2"]}).analyze_fleet(units)
    r = f.metrics["rooms"]["R"]
    assert f.severity == "warn" and r["active_spread_pct"] > 90 and r["zone_sensor_spread_f"] > 4


def test_room_imbalance_declines_without_rooms():
    f = HPRoomImbalance().analyze_fleet(_room_units(True))
    assert f.severity == "info" and f.metrics["declined"]
    empty = {"A": _hp(np.nan, np.nan), "B": _hp(np.nan, np.nan)}
    g = HPRoomImbalance(rooms={"R": ["A", "B"]}).analyze_fleet(empty)
    assert g.metrics["rooms"]["R"]["n_samples"] == 0


def _loop(dt, **extra):
    idx = pd.date_range("2026-01-05", periods=24 * 20, freq="1h")
    n = len(idx)
    cols = {
        Role.SOURCE_LOOP_SUPPLY_TEMP: np.full(n, 45.0),
        Role.SOURCE_LOOP_RETURN_TEMP: 45.0 + np.broadcast_to(dt, n),
    }
    for k, v in extra.items():
        cols[Role(k)] = np.broadcast_to(v, n).astype(float)
    return pd.DataFrame(cols, index=idx)


def test_source_loop_flags_a_loop_pumped_without_heat():
    f = SourceLoopDeltaT().analyze("GEO", _loop(-0.1, pump_status=1.0, source_loop_diff_press=11.0))
    assert f.severity == "fault" and f.metrics["flat_pct"] == 100.0
    assert f.metrics["diff_press_median"] == 11.0 and "overpumping" in f.summary
    warn = SourceLoopDeltaT().analyze(
        "GEO", _loop(np.tile([0.2, 0.3, 3.5, 0.1], 120), pump_status=1.0)
    )
    assert warn.severity == "warn"
    ok = SourceLoopDeltaT().analyze("GEO", _loop(-7.0, source_loop_pump_speed=60.0))
    assert ok.severity == "ok" and ok.metrics["return_colder_pct"] == 100.0


def test_source_loop_running_gate_and_decline():
    off = SourceLoopDeltaT().analyze("GEO", _loop(-0.1, source_loop_pump_speed=0.0))
    assert off.severity == "info" and off.metrics["declined"]
    no_status = SourceLoopDeltaT().analyze("GEO", _loop(-0.1))
    assert any("every sample is treated as pumping" in c for c in no_status.caveats)


def test_hp_rules_leave_an_unclassed_reheat_box_to_reheat_capacity():
    # 0.93 integration: a VAV box out of reheat, with its discharge air mapped and no class
    # recorded, is reported once -- by reheat_capacity_shortfall, not also as a heat pump
    import numpy as np

    from camber import faultlab
    from camber.model.roles import Role
    from camber.rules.builtin import builtin_registry

    reg = builtin_registry()
    f = faultlab.SCENARIOS["reheat_capacity_shortfall"](faultlab._idx(21), faulty=True)
    f[Role.SUPPLY_AIR_TEMP] = np.where(f[Role.HEAT_VALVE] > 0, 60 + 0.35 * f[Role.HEAT_VALVE], 60.0)
    assert reg.get("reheat_capacity_shortfall").analyze("VAV-1", f).severity == "fault"
    for name in ("hp_capacity_shortfall", "hp_mode_vs_need"):
        fd = reg.get(name).analyze("VAV-1", f)
        assert fd.severity == "info" and fd.metrics["declined"], name
    # a heat pump with a supplemental heating valve still runs once a compressor is mapped
    f[Role.COMPRESSOR_STATUS] = 1.0
    assert reg.get("hp_capacity_shortfall").analyze("HP-1", f).severity == "fault"
