"""0.92 (#66): the plant run gate for trust, the chiller power fallback and the OAT peer check.

Synthetic reproductions of what the private real-data check saw: a chiller that is off lets its
chilled-water supply drift to the plant-room temperature (out of the chilled range) and hold still
for days, which the ungated trust read as a stuck / out-of-range sensor.
"""

import numpy as np
import pandas as pd
import pytest

from camber.model.roles import Role
from camber.report.rcx import _oat_peer_check
from camber.rules.chwplant_rule import POWER_GATE_CAVEAT, CHWSupplyTracking
from camber.schedules import PLANT_GATE_NONE, plant_run_mask
from camber.sensorhealth import (
    PLANT_GATED_ROLES,
    _settled,
    frame_sensor_health,
    plant_gates,
    sensor_trust,
    untrusted_roles,
)


def _chiller(days=40, *, status=True, power=True, seed=0):
    """Hourly chiller frame: runs weekdays 7-19, off otherwise (CHWST floats to ~78 F, held)."""
    idx = pd.date_range("2026-06-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    on = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 19)
    chws = np.where(on, 44 + rng.normal(0, 0.4, len(idx)), 78.0)  # off: a held room reading
    chwr = np.where(on, 54 + rng.normal(0, 0.6, len(idx)), 78.0)
    f = pd.DataFrame(
        {Role.CHW_SUPPLY_TEMP: np.round(chws, 1), Role.CHW_RETURN_TEMP: np.round(chwr, 1)},
        index=idx,
    )
    f[Role.CHW_SUPPLY_TEMP_SP] = 44.0
    if status:
        f[Role.COMPRESSOR_STATUS] = on.astype(float)
    if power:
        f[Role.POWER] = np.where(on, 180 + rng.normal(0, 10, len(idx)), 2.0)
    return f, pd.Series(on, index=idx)


# ------------------------------------------------------------------------- the run mask


def test_plant_run_mask_prefers_status_then_power():
    f, on = _chiller()
    m, src = plant_run_mask(f, "chw")
    assert src == "chiller status" and m.equals(on)
    m, src = plant_run_mask(f.drop(columns=[Role.COMPRESSOR_STATUS]), "chw")
    assert src == "chiller power proxy" and bool((m == on).all())
    m, src = plant_run_mask(f.drop(columns=[Role.COMPRESSOR_STATUS, Role.POWER]), "chw")
    assert m is None and src == PLANT_GATE_NONE


def test_power_is_not_a_chiller_gate_on_an_air_handler():
    f, _ = _chiller(status=False)
    f[Role.SUPPLY_FAN_STATUS] = 1.0
    assert plant_run_mask(f, "chw") == (None, PLANT_GATE_NONE)


def test_plant_run_mask_boiler_status_then_gas():
    idx = pd.date_range("2026-01-01", periods=240, freq="1h")
    firing = (idx.hour % 3) == 0
    f = pd.DataFrame({Role.HW_SUPPLY_TEMP: 160.0, Role.GAS_INPUT_RATE: np.where(firing, 400, 0)})
    f.index = idx
    m, src = plant_run_mask(f, "hw")
    assert src == "boiler firing (gas input)" and bool((m.to_numpy() == firing).all())
    f[Role.BOILER_STATUS] = 1.0
    assert plant_run_mask(f, "hw")[1] == "boiler status"
    with pytest.raises(ValueError):
        plant_run_mask(f, "steam")


def test_settled_drops_the_first_half_hour_after_a_start():
    idx = pd.date_range("2026-01-01", periods=12, freq="10min")
    m = pd.Series([False, True, True, True, True, False, True, True, True, True, True, True], idx)
    s = _settled(m)
    want = [False, False, False, False, True, False, False, False, False, True, True, True]
    assert list(s) == want


# ------------------------------------------------------------------------- trust


def test_chiller_off_is_not_a_bad_sensor():
    f, _ = _chiller()
    ungated = frame_sensor_health(f)
    gated = frame_sensor_health(f, plant_gate="auto")
    t0, t1 = ungated[Role.CHW_SUPPLY_TEMP], gated[Role.CHW_SUPPLY_TEMP]
    # ungated: the held weekend reading is a stuck run and 78 F is outside the chilled range
    assert "stuck" in t0.flags and "out_of_range" in t0.flags and t0.verdict != "trusted"
    assert t1.verdict == "trusted" and not t1.flags and t1.run_gate == "chiller status"
    assert t1.range_violation_frac == 0.0
    # power is gated on the status (never on the power it was derived from)
    assert gated[Role.POWER].run_gate == "chiller status"
    # roles that are not plant-gated are untouched
    assert gated[Role.CHW_SUPPLY_TEMP_SP].run_gate is None


def test_power_derived_gate_does_not_judge_power():
    f, _ = _chiller(status=False)
    pg = plant_gates(f)
    assert pg[Role.CHW_SUPPLY_TEMP][1] == "chiller power proxy" and Role.POWER not in pg


def test_a_stuck_sensor_while_running_is_still_caught():
    f, on = _chiller()
    f.loc[f.index[240:480], Role.CHW_SUPPLY_TEMP] = 44.0  # identical for 10 days of running
    t = frame_sensor_health(f, plant_gate="auto")[Role.CHW_SUPPLY_TEMP]
    assert "stuck" in t.flags and t.verdict != "trusted"


def test_a_chiller_that_never_ran_is_not_judged():
    f, _ = _chiller()
    f[Role.COMPRESSOR_STATUS] = 0.0
    t = frame_sensor_health(f, plant_gate="auto")[Role.CHW_SUPPLY_TEMP]
    assert "not_running" in t.flags and t.verdict == "trusted" and t.range_violation_frac != 0.0


def test_run_gate_ignored_for_roles_outside_the_plant_set():
    f, on = _chiller()
    oat = pd.Series(np.linspace(60, 90, len(f)), index=f.index)
    assert sensor_trust(oat, Role.OAT, run_gate=on).run_gate is None
    assert Role.OAT not in PLANT_GATED_ROLES


def test_untrusted_roles_judges_plant_points_on_running_samples():
    f, _ = _chiller()
    assert untrusted_roles(f, [Role.CHW_SUPPLY_TEMP], min_trust=0.8) == []


def test_frame_sensor_health_rejects_an_unknown_plant_gate():
    f, _ = _chiller()
    with pytest.raises(ValueError):
        frame_sensor_health(f, plant_gate="yes")


# ------------------------------------------------------------------------- chiller power gate


def test_supply_tracking_falls_back_to_power_not_temperature():
    f, on = _chiller(status=False)
    fnd = CHWSupplyTracking().analyze("CH-1", f)
    assert fnd.metrics["run_source"] == "power"
    assert POWER_GATE_CAVEAT in fnd.caveats
    assert fnd.severity == "ok"


# ------------------------------------------------------------------------- OAT peer check


def _oat(idx, bias=0.0, seed=0):
    rng = np.random.default_rng(seed)
    base = 60 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi)
    return pd.Series(base + bias + rng.normal(0, 0.3, len(idx)), index=idx)


def test_oat_peer_check_outvotes_one_wrong_sensor():
    idx = pd.date_range("2026-05-01", periods=24 * 45, freq="1h")
    srcs = [(_oat(idx, 0, 1), ["AHU-1"]), (_oat(idx, 7.0, 2), ["AHU-2"]), (_oat(idx, 0, 3), ["WX"])]
    rows, findings, note = _oat_peer_check(srcs)
    assert len(rows) == 3 and note == ""
    by = {f.equip: f for f in findings}
    assert by["AHU-2"].severity == "fault" and by["AHU-2"].metrics["scope_equips"] == ["AHU-2"]
    assert by["AHU-1"].severity == "ok" and by["WX"].severity == "ok"
    assert by["AHU-2"].metrics["reference"] == "peer_median"


def test_oat_peer_check_two_sources_reports_but_raises_nothing():
    idx = pd.date_range("2026-05-01", periods=24 * 45, freq="1h")
    rows, findings, note = _oat_peer_check([(_oat(idx), ["A"]), (_oat(idx, 7.0, 5), ["B"])])
    assert findings == [] and len(rows) == 1 and rows[0][0].startswith("A vs B")
    assert "cannot say which" in note
    assert _oat_peer_check([(_oat(idx), ["A"])]) == ([], [], "")


# ------------------------------------------------------------------------- scoped OAT trust


def test_a_units_own_stuck_oat_taints_only_its_readers():
    from types import SimpleNamespace

    from camber.rules.triage import sensor_causes

    stuck = SimpleNamespace(verdict="suspect", flags=["stuck"], trust=0.75)
    trust = {"AHU-1": {"oat": stuck}}
    site_wide = sensor_causes([], trust=trust)
    assert len(site_wide) == 1 and site_wide[0].shared
    scoped = sensor_causes([], trust=trust, shared_scope={"AHU-1": ["AHU-1"], "CH-1": ["CH-1"]})
    assert [(c.equip, c.shared) for c in scoped] == [("AHU-1", False)]
