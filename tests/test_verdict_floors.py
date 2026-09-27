"""#57: two false verdicts -- a near-idle unit told its setback is missing, and supply air running
far above the G36 reset target reported as tracking it; plus the AHU-only class decline."""

import numpy as np
import pandas as pd

from camber.model.mapping import MappingProvider
from camber.model.roles import Role
from camber.rules.base import Registry, _class_declined
from camber.rules.satreset_compliance_rule import SupplyAirResetCompliance
from camber.rules.setback_rule import NightWeekendSetback
from camber.setback import analyze_setback


def _idle_frame():
    idx = pd.date_range("2026-01-05", periods=24 * 28, freq="1h")  # 4 weeks hourly
    fan = np.zeros(len(idx))
    fan[[30, 200, 410]] = 1.0  # three isolated hours on, two of them unoccupied
    return pd.DataFrame({Role.SUPPLY_FAN_STATUS: fan}, index=idx)


def test_near_idle_unit_is_not_missing_its_setback():
    f = NightWeekendSetback().analyze("u", _idle_frame())
    assert f.severity == "ok"
    m = f.metrics
    assert m["setback_effective"] is True
    assert m["fan_run_unoccupied_pct"] < 1 and m["fan_run_occupied_pct"] < 1
    # both figures are reported: the two runtimes and their ratio, plus the floor used
    assert m["unoccupied_to_occupied_ratio"] > 0.9
    assert m["min_unoccupied_run_pct"] == 5.0
    assert "materiality floor" in f.summary and "0.44%" in f.summary


def test_floor_is_tunable_and_material_runtime_still_fires():
    # with the floor off, the ratio test alone judges it again (the historical behaviour)
    f = NightWeekendSetback(min_unoccupied_run_pct=0.0).analyze("u", _idle_frame())
    assert f.severity == "warn" and f.metrics["setback_effective"] is False
    # a 24/7 fan is well above any floor
    idx = pd.date_range("2026-01-05", periods=24 * 14, freq="1h")
    f = NightWeekendSetback().analyze("u", pd.DataFrame({Role.SUPPLY_FAN_STATUS: 1.0}, index=idx))
    assert f.severity == "fault" and f.metrics["unoccupied_to_occupied_ratio"] == 1.0


def test_analyze_setback_ratio_is_none_when_never_run_occupied():
    idx = pd.date_range("2026-01-05", periods=24 * 14, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    df = pd.DataFrame({"SupplyFanStatus": np.where(occ, 0.0, 0.03)}, index=idx)
    res = analyze_setback(df, "u")
    assert res.fan_run_occupied_pct == 0.0 and res.unoccupied_to_occupied_ratio is None
    assert res.setback_effective  # 3 % unoccupied is under the 5 % floor


def _warm_sat_frame(sat):
    idx = pd.date_range("2026-06-01", periods=24 * 21, freq="1h")
    oat = 70 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi)
    return pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: sat + 0 * oat, Role.OAT: oat, Role.SUPPLY_FAN_STATUS: 1.0},
        index=idx,
    )


def test_sat_far_above_target_is_not_tracking():
    f = SupplyAirResetCompliance().analyze("ahu", _warm_sat_frame(80.0))
    assert f.severity == "warn"
    assert "NOT tracking" in f.summary and "tracks the G36" not in f.summary
    m = f.metrics
    assert m["pct_above_g36_target"] == 100.0 and m["mean_above_gap_f"] > 15
    assert m["tracks_target"] is False and m["mean_abs_error_f"] > 15


def test_sat_on_target_still_tracks():
    from camber.g36_reset import oat_sat_setpoint

    fr = _warm_sat_frame(0.0)
    fr[Role.SUPPLY_AIR_TEMP] = oat_sat_setpoint(fr[Role.OAT].to_numpy())
    f = SupplyAirResetCompliance().analyze("ahu", fr)
    assert f.severity == "ok" and "tracks the G36 reset target" in f.summary
    assert f.metrics["tracks_target"] is True and f.metrics["mean_abs_error_f"] < 0.5


def test_below_target_opportunity_unchanged():
    f = SupplyAirResetCompliance().analyze("ahu", _warm_sat_frame(50.0))
    assert f.severity == "warn" and "reheat/energy opportunity" in f.summary


class _Ref:
    def __init__(self, equip, equip_class):
        self.equip, self.equip_class = equip, equip_class


def test_class_decline_for_non_ahu_equipment():
    rule = SupplyAirResetCompliance()
    for cls in ("HEAT_PUMP", "VAV", "FCU"):
        f = _class_declined(rule, _Ref("x", cls))
        assert f is not None and f.severity == "info" and f.metrics["declined"] is True
        assert f.metrics["equip_class"] == cls and f.caveats
    for cls in ("AHU", "ahu", "RTU", "DOAS", "AHU_DOAS", ""):
        assert _class_declined(rule, _Ref("x", cls)) is None
    # a rule without equip_classes is never declined by class
    assert _class_declined(NightWeekendSetback(), _Ref("x", "HEAT_PUMP")) is None


def test_registry_run_declines_heat_pump(tmp_path):
    from camber.resolve import discover_store
    from camber.store import ParquetStore

    st = ParquetStore(str(tmp_path / "store"))
    fr = _warm_sat_frame(80.0)
    st.write_role_frame(fr, facility_id="f", equip="HP-1", equip_class="HEAT_PUMP")
    st.write_role_frame(fr, facility_id="f", equip="AHU-1", equip_class="AHU")
    refs = discover_store(st, "f", "HEAT_PUMP") + discover_store(st, "f", "AHU")
    reg = Registry()
    reg.register(SupplyAirResetCompliance())
    out = {f.equip: f for f in reg.run("supply_air_reset_compliance", refs, MappingProvider())}
    assert out["HP-1"].metrics.get("declined") is True
    assert out["AHU-1"].severity == "warn" and not out["AHU-1"].metrics.get("declined")
