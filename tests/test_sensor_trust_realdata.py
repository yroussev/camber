"""#58: sensor-trust misses seen on real data, each on a synthetic reproduction."""

import numpy as np
import pandas as pd

from camber.model.roles import Role
from camber.sensorhealth import frame_sensor_health, sensor_trust, untrusted_roles


def _idx(days, freq="5min", start="2026-07-06"):
    return pd.date_range(start, periods=int(days * 24 * 60 / int(freq[:-3])), freq=freq)


def test_constant_status_for_months_is_not_trusted():
    idx = pd.date_range("2025-08-01", "2026-07-01", freq="5min")
    t = sensor_trust(pd.Series(0.0, index=idx), Role.SUPPLY_FAN_STATUS, expected_freq="5min")
    assert t.verdict == "suspect" and "never_changes" in t.flags and t.n_state_changes == 0


def test_seasonal_pump_status_is_flagged_not_penalized():
    idx = pd.date_range("2026-01-01", periods=24 * 60, freq="1h")
    t = sensor_trust(pd.Series(0.0, index=idx), Role.PUMP_STATUS)
    assert "never_changes" in t.flags and t.verdict == "trusted"


def test_twelve_day_stuck_space_temp_is_reported():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-08-01", "2026-07-01", freq="5min")
    zn = 70 + 1.5 * np.sin(np.arange(len(idx)) / 288 * 2 * np.pi) + rng.normal(0, 0.3, len(idx))
    zn = pd.Series(np.round(zn, 1), index=idx)
    zn["2025-12-04":"2025-12-16"] = 45.0
    t = sensor_trust(zn, Role.SPACE_TEMP, expected_freq="5min")
    assert t.verdict == "suspect" and "stuck" in t.flags
    assert len(t.stuck_intervals) == 1
    iv = t.stuck_intervals[0]
    assert iv["value"] == 45.0 and iv["hours"] == 312.0 and iv["start"].startswith("2025-12-04")
    assert t.longest_flat_hours == 312.0
    # a per-role override moves the limit
    t2 = sensor_trust(zn, Role.SPACE_TEMP, expected_freq="5min", stuck_hours={Role.SPACE_TEMP: 400})
    assert not t2.stuck_intervals


def test_idle_flow_over_a_weekend_is_not_stuck():
    idx = pd.date_range("2026-01-05", periods=24 * 14, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    flow = np.where(occ, 800 + np.random.default_rng(1).normal(0, 20, len(idx)), 0.0)
    t = sensor_trust(pd.Series(flow, index=idx), Role.CHW_FLOW)
    assert "stuck" not in t.flags and not t.stuck_intervals


def test_fan_off_duct_temperature_hold_is_only_judged_gated():
    idx = pd.date_range("2026-01-05", periods=24 * 14, freq="1h")
    on = (idx.dayofweek < 5) & (idx.hour >= 6) & (idx.hour < 19)
    sat = np.where(on, 55 + np.random.default_rng(2).normal(0, 0.3, len(idx)), 70.0)
    s = pd.Series(sat, index=idx)
    assert not sensor_trust(s, Role.SUPPLY_AIR_TEMP).stuck_intervals  # ungated: undecidable
    assert not sensor_trust(s, Role.SUPPLY_AIR_TEMP, gate=pd.Series(on, index=idx)).stuck_intervals


def test_fractional_native_status_is_suspect_but_duty_resample_is_not():
    idx = _idx(20)
    ramp = pd.Series(np.clip(np.sin(np.arange(len(idx)) / 50.0), 0, 1), index=idx)
    t = sensor_trust(ramp, Role.SUPPLY_FAN_STATUS)
    assert "fractional_status" in t.flags and t.verdict == "suspect"
    hourly = ramp.resample("1h").mean()
    assert "fractional_status" not in sensor_trust(hourly, Role.SUPPLY_FAN_STATUS).flags


def _r10_frame():
    rng = np.random.default_rng(0)
    ix = _idx(14, "15min")
    on = (ix.hour >= 6) & (ix.hour < 18)
    fr = pd.DataFrame(
        {
            Role.SUPPLY_FAN_STATUS: np.where(on, 1.0, 0.0),
            Role.SUPPLY_FAN_SPEED: np.where(on, 60 + rng.normal(0, 3, len(ix)), 0.0),
            Role.DUCT_STATIC: np.where(
                on, 1.6 + rng.normal(0, 0.03, len(ix)), 7.3 + rng.normal(0, 0.1, len(ix))
            ),
        },
        index=ix,
    )
    fr.loc[(ix.day == 10) & on, Role.SUPPLY_FAN_STATUS] = 0.0  # status off while the VFD runs
    return fr


def test_duct_static_with_fan_off_and_status_vs_speed():
    fr = _r10_frame()
    for gate in (None, "fan"):
        h = frame_sensor_health(fr, gate=gate)
        dsp = h[Role.DUCT_STATIC]
        assert "implausible_fan_off" in dsp.flags and dsp.verdict != "trusted"
        chk = dsp.frame_checks[0]
        assert chk["check"] == "fan_off_pressure" and chk["median_fan_off"] > 7
        st = h[Role.SUPPLY_FAN_STATUS]
        assert "status_speed_mismatch" in st.flags and st.verdict == "suspect"
        mm = st.frame_checks[0]
        assert mm["n_status_off_speed_running"] == 48 and mm["first"].startswith("2026-07-10")
        assert "status_speed_mismatch" in h[Role.SUPPLY_FAN_SPEED].flags
    # the runner's trust gate sees the frame-level checks too
    assert untrusted_roles(fr, [Role.DUCT_STATIC], min_trust=0.8) == [Role.DUCT_STATIC]


def test_consistent_fan_frame_is_clean():
    fr = _r10_frame()
    ix = fr.index
    on = (ix.hour >= 6) & (ix.hour < 18)
    fr[Role.SUPPLY_FAN_STATUS] = np.where(on, 1.0, 0.0)
    fr[Role.DUCT_STATIC] = np.where(on, fr[Role.DUCT_STATIC], 0.02)
    h = frame_sensor_health(fr)
    assert all(not t.frame_checks for t in h.values())


def test_late_starting_point_is_judged_on_its_own_span():
    rng = np.random.default_rng(0)
    ix = _idx(120, "60min")
    late = pd.Series(np.where(ix >= ix[-240], 150 + rng.normal(0, 20, len(ix)), np.nan), index=ix)
    t = sensor_trust(late, Role.AIRFLOW)
    assert "late_start" in t.flags and "low_coverage" not in t.flags
    assert t.verdict == "trusted" and t.coverage == 1.0
    assert t.first_valid == str(ix[-240]) and t.window_coverage < 0.1
    # a point that stops part-way is still low coverage
    early = pd.Series(np.where(ix < ix[240], 72.0 + rng.normal(0, 0.5, len(ix)), np.nan), index=ix)
    assert "low_coverage" in sensor_trust(early, Role.SPACE_TEMP).flags


def test_all_points_freeze_is_detected():
    rng = np.random.default_rng(0)
    ix = _idx(30, "15min")
    fr = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: 55 + rng.normal(0, 0.5, len(ix)),
            Role.OAT: 60 + 10 * np.sin(2 * np.pi * ix.hour / 24) + rng.normal(0, 0.3, len(ix)),
            Role.SUPPLY_FAN_SPEED: 60 + rng.normal(0, 3, len(ix)),
        },
        index=ix,
    )
    blk = (ix >= "2026-07-15") & (ix < "2026-07-18")
    fr.loc[blk] = fr.loc[blk].iloc[0].to_numpy()
    h = frame_sensor_health(fr)
    for role in fr.columns:
        assert "all_points_frozen" in h[role].flags and h[role].verdict == "suspect"
    iv = h[Role.OAT].frame_checks[0]["intervals"]
    assert len(iv) == 1 and iv[0]["hours"] == 72.0 and iv[0]["start"].startswith("2026-07-15")


def test_constant_fixture_columns_are_not_a_freeze():
    ix = _idx(7, "60min")
    fr = pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: 55.0, Role.MIXED_AIR_TEMP: 60.0, Role.OAT: 70.0}, index=ix
    )
    h = frame_sensor_health(fr)
    assert all("all_points_frozen" not in t.flags for t in h.values())


def test_as_dict_carries_the_new_fields():
    ix = _idx(3, "60min")
    d = sensor_trust(pd.Series(np.arange(len(ix), dtype=float), index=ix), Role.OAT).as_dict()
    for k in (
        "longest_flat_hours",
        "stuck_intervals",
        "first_valid",
        "window_coverage",
        "n_state_changes",
        "frame_checks",
    ):
        assert k in d
