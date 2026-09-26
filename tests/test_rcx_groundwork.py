"""Phase-A groundwork for the RCx report: operating-state gates, gated sensor trust, the
research-only banner on the site report and dashboard, and the configured registry / lazy frame
resolver on a run.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report.audit import RESEARCH_ONLY_BANNER  # noqa: E402
from camber.rules.satreset_compliance_rule import SupplyAirResetCompliance  # noqa: E402
from camber.schedules import FAN_GATE_NONE, fan_on_mask  # noqa: E402
from camber.sensorhealth import frame_sensor_health, sensor_trust  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

NC = {"dataset_id": "demo-nc", "title": "Demo NC data", "licence": "CC-BY-NC-4.0",
      "access": "research_only"}  # fmt: skip


def _idx(days=14):
    return pd.date_range("2026-03-02", periods=24 * days, freq="h")  # starts on a Monday


# --------------------------------------------------------------------------- fan gate


def test_fan_on_mask_prefers_status_then_speed_then_airflow():
    idx = _idx(2)
    on = (idx.hour >= 6) & (idx.hour < 20)
    frame = pd.DataFrame(
        {
            Role.SUPPLY_FAN_STATUS: on.astype(float),
            Role.SUPPLY_FAN_SPEED: np.where(on, 0.6, 0.0),  # 0-1 accepted
            Role.AIRFLOW: np.where(on, 9000.0, 3.0),  # transmitter noise at zero flow
        },
        index=idx,
    )
    m, src = fan_on_mask(frame)
    assert src == "fan status" and m.sum() == on.sum()
    m, src = fan_on_mask(frame.drop(columns=[Role.SUPPLY_FAN_STATUS]))
    assert src == "fan speed proxy" and m.sum() == on.sum()
    m, src = fan_on_mask(frame[[Role.AIRFLOW]])
    assert src == "airflow proxy" and m.sum() == on.sum()
    assert fan_on_mask(pd.DataFrame({Role.OAT: 1.0}, index=idx)) == (None, FAN_GATE_NONE)
    # an all-NaN status is skipped in favour of the next signal
    frame[Role.SUPPLY_FAN_STATUS] = np.nan
    assert fan_on_mask(frame)[1] == "fan speed proxy"


# --------------------------------------------------------------------------- SAT reset compliance


def _sat_frame(with_fan=True, occ=False):
    """SAT tracks the G36 map while the fan runs on weekdays; parked at 48 °F otherwise."""
    from camber.g36_reset import oat_sat_setpoint

    idx = _idx()
    oat = 55 + 12 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    running = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    sat = np.where(running, oat_sat_setpoint(oat), 48.0)
    cols = {Role.SUPPLY_AIR_TEMP: sat, Role.OAT: oat}
    if with_fan:
        cols[Role.SUPPLY_FAN_STATUS] = running.astype(float)
    if occ:
        cols[Role.OCCUPANCY] = running.astype(float)
    return pd.DataFrame(cols, index=idx)


def test_sat_compliance_gates_on_fan_and_occupancy_by_default():
    frame = _sat_frame(occ=True)
    f = SupplyAirResetCompliance().analyze("DemoAHU", frame)
    assert f.severity == "ok"
    assert f.metrics["fan_gate"] == "fan status"
    assert f.metrics["occupancy_gate"] == "trended occupancy"
    assert f.metrics["reset_source"] == "g36_default"
    assert f.metrics["n"] == f.metrics["n_gated"] == int(frame[Role.SUPPLY_FAN_STATUS].sum())
    # the historical, ungated read scores the parked overnight SAT as "colder than target"
    ungated = SupplyAirResetCompliance(fan_gate=False, occupied_only=False).analyze(
        "DemoAHU", frame
    )
    assert ungated.severity == "warn"
    assert ungated.metrics["fan_gate"] == "off" and ungated.metrics["n_gated"] is None


def test_sat_compliance_reports_the_gate_actually_used():
    f = SupplyAirResetCompliance().analyze("DemoAHU", _sat_frame(with_fan=False))
    assert f.metrics["fan_gate"] == FAN_GATE_NONE
    assert f.metrics["occupancy_gate"].startswith("assumed schedule")
    f = SupplyAirResetCompliance(min_clg_sat=56.0).analyze("DemoAHU", _sat_frame())
    assert f.metrics["reset_source"] == "configured"
    f = SupplyAirResetCompliance(reset_source="site sequence").analyze("DemoAHU", _sat_frame())
    assert f.metrics["reset_source"] == "site sequence"


def test_sat_compliance_decline_keeps_the_gate_metrics():
    frame = _sat_frame()
    frame[Role.SUPPLY_FAN_STATUS] = 0.0  # never running -> nothing to judge
    f = SupplyAirResetCompliance().analyze("DemoAHU", frame)
    assert f.metrics["declined"] is True and f.metrics["n_gated"] == 0


# --------------------------------------------------------------------------- gated sensor trust


def test_fan_off_flatline_is_not_counted_as_stuck():
    idx = _idx(7)
    rng = np.random.default_rng(1)
    running = (idx.dayofweek < 2) | (idx.dayofweek > 5)  # off Wednesday..Saturday (96 h in a row)
    sat = np.where(running, 55 + rng.normal(0, 1.0, len(idx)), 70.0)
    series = pd.Series(sat, index=idx)
    gate = pd.Series(running, index=idx)
    ungated = sensor_trust(series, Role.SUPPLY_AIR_TEMP)
    gated = sensor_trust(series, Role.SUPPLY_AIR_TEMP, gate=gate)
    assert "stuck" in ungated.flags
    assert "stuck" not in gated.flags and gated.trust > ungated.trust
    assert gated.flatline_frac < 0.1
    # a sensor stuck *while the fan runs* is still stuck
    stuck = pd.Series(np.where(running, 55.0, 70.0), index=idx)
    assert "stuck" in sensor_trust(stuck, Role.SUPPLY_AIR_TEMP, gate=gate).flags
    # an ambient role is never gated
    oat = sensor_trust(series, Role.OAT, gate=gate)
    assert oat.flatline_frac == sensor_trust(series, Role.OAT).flatline_frac


def test_frame_sensor_health_fan_gate_and_default_unchanged():
    idx = _idx(7)
    running = (idx.dayofweek < 2) | (idx.dayofweek > 5)
    rng = np.random.default_rng(2)
    frame = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: np.where(running, 55 + rng.normal(0, 1.0, len(idx)), 70.0),
            Role.SUPPLY_FAN_STATUS: running.astype(float),
        },
        index=idx,
    )
    assert "stuck" in frame_sensor_health(frame)[Role.SUPPLY_AIR_TEMP].flags
    assert "stuck" not in frame_sensor_health(frame, gate="fan")[Role.SUPPLY_AIR_TEMP].flags
    with pytest.raises(ValueError):
        frame_sensor_health(frame, gate="moon")


# --------------------------------------------------------------------------- NC banner


def _frame():
    idx = _idx(3)
    return pd.DataFrame({"a": np.arange(len(idx), dtype=float)}, index=idx)


def test_site_report_and_dashboard_carry_the_research_only_banner():
    from camber.report.dashboard import build_dashboard
    from camber.report.site import build_site_report

    for html in (
        build_site_report(_frame(), data_sources=[NC], sections=()),
        build_dashboard(_frame(), data_sources=[NC], sections=()),
    ):
        body = html.split("<body>", 1)[1]
        assert body.lstrip().startswith("<div class='camber-nc-banner'")
        assert RESEARCH_ONLY_BANNER in html
    assert "camber-nc-banner" not in build_dashboard(_frame(), sections=())


# --------------------------------------------------------------------------- RunResult


def _store(tmp_path):
    idx = _idx()
    st = ParquetStore(str(tmp_path / "store"))
    rng = np.random.default_rng(0)
    oat = 60 + 20 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    frame = pd.DataFrame(
        {
            Role.OAT: oat,
            Role.OA_DAMPER: np.where(oat > 70, 90.0, 20.0) + rng.normal(0, 1, len(idx)),
            Role.SUPPLY_AIR_TEMP: 55 + rng.normal(0, 0.5, len(idx)),
        },
        index=idx,
    )
    st.write_role_frame(frame, facility_id="demo-fac", equip="DemoAHU", equip_class="AHU")
    st.register_facility("demo-fac", name="Demo facility", dataset=NC)
    return st


def test_run_result_carries_the_configured_registry_and_a_lazy_frame_resolver(tmp_path):
    _store(tmp_path)
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": "demo-fac"},
        "equipment": [{"class": "AHU"}],
        "rules": [{"name": "economizer_high_limit", "params": {"high_limit_f": 72.0}}],
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.registry.get("economizer_high_limit").high_limit_f == 72.0
    frame = res.frame_for("DemoAHU")
    assert Role.OA_DAMPER in frame.columns and Role.SUPPLY_AIR_TEMP in frame.columns
    assert res.frame_for("DemoAHU") is frame  # memoized
    assert list(res.frame_for("DemoAHU", roles=[Role.OAT]).columns) == [Role.OAT]
    assert res.frame_for("NoSuchUnit") is None
    assert [r.equip for r in res.refs] == ["DemoAHU"]
    assert res.data_sources and res.data_sources[0]["access"] == "research_only"
    assert res.config is cfg and res.base_dir == str(tmp_path)


def test_sat_compliance_names_a_fan_signal_missing_only_when_all_are_absent():
    frame = _sat_frame()
    f = SupplyAirResetCompliance().analyze("DemoAHU", frame)
    assert f.metrics["_missing_optional"] == ["occupancy"]  # not fan speed / airflow
    f = SupplyAirResetCompliance().analyze("DemoAHU", _sat_frame(with_fan=False, occ=True))
    assert f.metrics["_missing_optional"] == ["supply_fan_status"]
    assert (
        "_missing_optional"
        not in SupplyAirResetCompliance().analyze("DemoAHU", _sat_frame(occ=True)).metrics
    )
