"""The ``g36_afdd`` rule: the G36 §5.16.14 engine wired into config runs and the RCx report (#60).

Synthetic fixtures only.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule, rule_names  # noqa: E402
from camber.rules.g36_rule import G36AFDD  # noqa: E402


def _ahu(days=5, *, leak=False, heat=False, fan=True, occupancy=False):
    """A 5-minute cooling AHU, fan 06-18, OA damper at its 20 % minimum while it cools. ``leak``:
    from noon the valve is shut and the economizer open (50 %), but the coil still cools the air
    10F (a passing chilled-water valve) on a 80F day."""
    idx = pd.date_range("2026-07-06", periods=12 * 24 * days, freq="5min")
    on = (idx.hour >= 6) & (idx.hour < 18)
    pm = on & (idx.hour >= 12) if leak else np.zeros(len(idx), dtype=bool)
    cols = {
        Role.COOL_VALVE: np.where(on & ~pm, 40.0, 0.0),
        Role.SUPPLY_AIR_TEMP: np.where(on, np.where(pm, 60.0, 55.0), 80.0),
        Role.MIXED_AIR_TEMP: np.where(on, 70.0, 72.0),
        Role.RETURN_AIR_TEMP: np.full(len(idx), 74.0),
        Role.OAT: np.where(on, 80.0, 70.0),
        Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),
        Role.DUCT_STATIC: np.where(on, 1.5, 0.0),
        Role.DUCT_STATIC_SP: np.full(len(idx), 1.5),
        Role.OA_DAMPER: np.where(pm, 50.0, 20.0),
    }
    if fan:
        cols[Role.SUPPLY_FAN_SPEED] = np.where(on, 60.0, 0.0)
    if heat:
        cols[Role.HEAT_VALVE] = np.zeros(len(idx))
    if occupancy:
        cols[Role.OCCUPANCY] = ((idx.hour >= 7) & (idx.hour < 18)).astype(float)
    return pd.DataFrame(cols, index=idx)


def test_registered_and_constructible_from_a_config():
    assert "g36_afdd" in rule_names()
    assert builtin_registry().get("g36_afdd").name == "g36_afdd"
    r = make_rule("g36_afdd", mode_delay_min=15, min_oa_pct=20)
    assert r.mode_delay_min == 15 and r.min_oa_pct == 20
    with pytest.raises(TypeError, match="g36_afdd"):
        make_rule("g36_afdd", nope=1)


def test_cooling_only_leak_is_reported_as_fc14_not_fc8_fc9():
    f = G36AFDD().analyze("AHU-1", _ahu(leak=True))
    m = f.metrics
    assert f.severity == "fault" and m["worst_fc"] == "FC14"
    assert m["fc"]["FC14"]["hours"] == 30.0  # 6 leaking hours a day x 5 days
    # the same hours trip FC8 (SAT/MAT mismatch) and FC9 (OAT too high) -- attributed to FC14
    assert m["fc"]["FC8"]["hours"] == 0.0 and m["fc"]["FC9"]["hours"] == 0.0
    assert m["attributed_to_fc14_hours"] == {"FC8": 30.0, "FC9": 30.0}
    assert m["fc"]["FC7"]["status"] == "omitted" and m["fc"]["FC15"]["status"] == "omitted"
    assert m["fan_gate"] == "fan speed proxy"
    assert "FC14" in f.summary
    assert any("MAT/SAT as the cooling-coil" in c for c in f.caveats)
    assert any("attributed to FC14" in c for c in f.caveats)
    ev = G36AFDD().evidence("AHU-1", _ahu(leak=True))
    assert ev.renderer == "multitrend" and int(ev.mask.sum()) == 30 * 12


def test_with_a_heating_coil_fc14_fc15_decline_and_the_leak_stays_under_fc8():
    # MAT/SAT span both coils: they cannot isolate the cooling coil, so FC14 is declined -- the
    # leak then shows under FC8/FC9, whose G36 diagnoses include a passing cooling valve
    f = G36AFDD().analyze("AHU-1", _ahu(leak=True, heat=True))
    fc = f.metrics["fc"]
    assert fc["FC14"]["status"] == "declined" and "both coils" in fc["FC14"]["reason"]
    assert fc["FC15"]["status"] == "declined"
    assert fc["FC8"]["hours"] == 30.0 and f.metrics["attributed_to_fc14_hours"] == {}
    assert any(c.startswith("FC14 not evaluated") for c in f.caveats)


def test_healthy_ahu_is_ok_and_fan_off_hours_are_not_scored():
    f = G36AFDD().analyze("AHU-1", _ahu())
    assert f.severity == "ok"
    assert f.metrics["fan_on_hours"] == 60.0
    assert f.metrics["os_hours"]["OS2"] == 0.0  # the fan-off, valves-shut hours are not OS#2
    assert f.metrics["suspended_hours"] == 2.5  # ModeDelay after each of the 5 starts


def test_declines_without_a_fan_signal_or_a_known_heating_valve():
    f = G36AFDD().analyze("AHU-1", _ahu(fan=False))
    assert f.severity == "info" and f.metrics["declined"] is True
    assert "supply-fan" in f.metrics["reason"]
    g = G36AFDD(heating_coil=True).analyze("AHU-1", _ahu())
    assert g.metrics["declined"] is True and "heating_coil=True" in g.metrics["reason"]
    assert G36AFDD().evidence("AHU-1", _ahu(fan=False)) is None


def test_heating_coil_false_ignores_a_mapped_heating_valve():
    f = G36AFDD(heating_coil=False).analyze("AHU-1", _ahu(leak=True, heat=True))
    assert f.metrics["fc"]["FC14"]["status"] == "evaluated"
    assert any("heating_coil=False" in c for c in f.caveats)


def test_occupancy_changes_extend_mode_delay_and_min_oa_enables_fc6():
    base = G36AFDD().analyze("AHU-1", _ahu())
    occ = G36AFDD().analyze("AHU-1", _ahu(occupancy=True))
    # the 07:00 unoccupied -> occupied change suspends another 30 minutes a day
    assert occ.metrics["suspended_hours"] == base.metrics["suspended_hours"] + 2.5
    assert base.metrics["fc"]["FC6"]["status"] == "declined"
    assert any("no occupancy" in c for c in base.caveats)
    # %OA from the temperatures: (70-74)/(80-74) < 0 -> far from a 20 % minimum at |OAT-RAT| 6F,
    # which is below dT_min (10F), so FC6 is evaluated but cannot trip here
    fc6 = G36AFDD(min_oa_pct=20).analyze("AHU-1", _ahu()).metrics["fc"]["FC6"]
    assert fc6["status"] == "evaluated" and fc6["pct"] == 0.0


def _ahu_at_10pct_oa():
    """``_ahu`` on a hot day (OAT 90F, RAT 74F) cooling with 10 % OA by the temperature balance."""
    fr = _ahu()
    on = fr[Role.SUPPLY_FAN_SPEED] > 0
    fr[Role.OAT] = np.where(on, 90.0, 70.0)
    fr[Role.MIXED_AIR_TEMP] = np.where(on, 74.0 + 0.1 * 16.0, 72.0)
    return fr


def test_fc6_seasonal_minimum_by_month():
    # 0.100 (#97): a July frame at 10 % OA. Against a fixed 45 % minimum FC6 trips (35 points
    # off, beyond G36's 30-point tolerance); with July's own 12 % minimum it does not.
    fr = _ahu_at_10pct_oa()
    fixed = G36AFDD(min_oa_pct=45).analyze("AHU-1", fr)
    assert fixed.metrics["fc"]["FC6"]["pct"] == 100.0
    seasonal = G36AFDD(min_oa_pct=45, min_oa_pct_by_month={7: 12}).analyze("AHU-1", fr)
    fc6 = seasonal.metrics["fc"]["FC6"]
    assert fc6["status"] == "evaluated" and fc6["pct"] == 0.0
    assert fc6["applicable_hours"] == fixed.metrics["fc"]["FC6"]["applicable_hours"]
    assert any("seasonal minimum of 45 %, by month {7: 12 %}" in c for c in seasonal.caveats)
    assert not any("seasonal" in c for c in fixed.caveats)
    # a month the override does not name keeps min_oa_pct; config keys may be strings
    other = make_rule("g36_afdd", min_oa_pct=45, min_oa_pct_by_month={"1": 12})
    assert other.min_oa_pct_by_month == {1: 12.0}
    assert other.analyze("AHU-1", fr).metrics["fc"]["FC6"]["pct"] == 100.0
    # the default output carries no seasonal wording and the rule's default is None
    assert G36AFDD().min_oa_pct_by_month is None


def test_fc6_seasonal_minimum_is_validated():
    with pytest.raises(ValueError, match="months 1-12"):
        G36AFDD(min_oa_pct=30, min_oa_pct_by_month={13: 10})
    with pytest.raises(ValueError, match="needs min_oa_pct"):
        G36AFDD(min_oa_pct_by_month={6: 10})


def test_missing_inputs_are_declined_not_asserted_clean():
    fr = _ahu().drop(columns=[Role.DUCT_STATIC, Role.DUCT_STATIC_SP])
    f = G36AFDD().analyze("AHU-1", fr)
    assert f.metrics["fc"]["FC1"]["status"] == "declined"
    assert any(c.startswith("FC1") and "not mapped" in c for c in f.caveats)


def test_config_run_and_rcx_report(tmp_path):
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report.rcx import build_rcx_report

    fx.make_store(tmp_path)
    cfg = fx.config()
    cfg["rules"] = ["g36_afdd"]
    res = run_config(cfg, base_dir=str(tmp_path))
    g = {f.equip: f for f in res.findings if f.rule == "g36_afdd"}
    assert set(g) == {"DemoAHU", "DemoAHU2"}
    assert all(f.metrics.get("fan_gate") == "fan status" for f in g.values())
    rep = build_rcx_report(res)
    assert rep.to_html()  # the G36 findings flow through the report without error


# ---- 0.98 (#94): free cooling needs an open economizer; the opt-in occupancy gate ----


def _recirc_ahu(days=5):
    """Fan on around the clock: occupied 07-19 at a 20 % minimum (cooling) or economizing at 60 %;
    unoccupied with the OA damper shut and both valves idle on a hot night."""
    idx = pd.date_range("2026-07-06", periods=24 * days, freq="1h")
    occ = (idx.hour >= 7) & (idx.hour < 19)
    cool = occ & (idx.hour >= 12)
    return pd.DataFrame(
        {
            Role.SUPPLY_FAN_STATUS: np.ones(len(idx)),
            Role.COOL_VALVE: np.where(cool, 60.0, 0.0),
            Role.OA_DAMPER: np.where(occ, np.where(cool, 20.0, 60.0), 0.0),
            Role.SUPPLY_AIR_TEMP: np.full(len(idx), 55.0),
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),
            Role.MIXED_AIR_TEMP: np.where(occ & ~cool, 53.0, 72.0),
            Role.RETURN_AIR_TEMP: np.full(len(idx), 74.0),
            Role.OAT: np.where(occ & ~cool, 50.0, 80.0),
            Role.OCCUPANCY: occ.astype(float),
        },
        index=idx,
    )


def test_unoccupied_recirculation_does_not_trip_fc9():
    f = G36AFDD().analyze("AHU-1", _recirc_ahu())
    m = f.metrics
    assert f.severity == "ok" and m["fc"]["FC9"]["pct"] == 0.0
    assert m["oa_damper_min"] == 20.0 and m["oa_damper_min_source"].startswith("learned")
    assert m["idle_at_min_oa_hours"] == 60.0  # 12 unoccupied hours a day, as OS#5
    assert m["os_hours"]["OS5"] == 60.0 and m["free_cooling_hours"] == 25.0
    assert m["occupancy_gate"] == "off" and m["unoccupied_hours"] == 0.0
    # a stated minimum of 0 % with no tolerance reads the shut damper as closed, still not OS#2
    g = G36AFDD(oa_damper_min=0.0, oa_damper_tol=0.0).analyze("AHU-1", _recirc_ahu())
    assert g.metrics["oa_damper_min_source"] == "caller"
    assert g.metrics["fc"]["FC9"]["pct"] == 0.0


def test_heating_above_minimum_oa_is_counted_and_not_os1():
    # 0.99 (#95): a heating hour with the OA damper open beyond its minimum is OS#5, not OS#1
    df = _recirc_ahu()
    occ = df[Role.OCCUPANCY].to_numpy() > 0
    heat = ~occ & (df.index.hour < 3)  # 3 night hours a day: heating with the damper at 60 %
    df[Role.HEAT_VALVE] = np.where(heat, 80.0, 0.0)
    df.loc[heat, Role.OA_DAMPER] = 60.0
    m = G36AFDD().analyze("AHU-1", df).metrics
    assert m["oa_damper_min"] == 20.0
    assert m["heating_above_min_oa_hours"] == float(heat.sum())
    assert m["os_hours"]["OS1"] == 0.0
    # at its minimum the same hours are heating
    df.loc[heat, Role.OA_DAMPER] = 20.0
    m2 = G36AFDD().analyze("AHU-1", df).metrics
    assert m2["heating_above_min_oa_hours"] == 0.0
    assert m2["os_hours"]["OS1"] == float(heat.sum())


def test_occupancy_gate_trended_evaluates_occupied_hours_only():
    f = G36AFDD(occupancy_gate="trended").analyze("AHU-1", _recirc_ahu())
    assert f.metrics["occupancy_gate"] == "trended occupancy"
    assert f.metrics["unoccupied_hours"] > 0
    assert any("occupancy_gate='trended'" in c for c in f.caveats)
    no_occ = _recirc_ahu().drop(columns=[Role.OCCUPANCY])
    g = G36AFDD(occupancy_gate="trended").analyze("AHU-1", no_occ)
    assert g.metrics["occupancy_gate"].startswith("none trended")
    assert g.metrics["unoccupied_hours"] == 0.0
    assert any("no occupancy point is trended" in c for c in g.caveats)
    with pytest.raises(ValueError, match="occupancy_gate"):
        G36AFDD(occupancy_gate="schedule")
