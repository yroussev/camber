"""#87: per-mode outlier read, stray lead rows, clipped-at-limit and scheduled status points."""

import numpy as np
import pandas as pd
import pytest

from camber.model.roles import Role
from camber.sensorhealth import (
    clipped_at_limit,
    frame_sensor_health,
    gapfill_signature,
    sensor_trust,
)


def _two_mode_unit(n_days=28, seed=0):
    """A fan-less AHU: SAT held tight at 66 F while conditioning, 56-64 F whenever the OA damper
    and both coil valves sit closed (an off-mode)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n_days * 24, freq="1h")
    off = rng.random(len(idx)) < 0.3
    sat = np.where(off, rng.uniform(56.0, 64.0, len(idx)), 66.2 + rng.normal(0, 0.05, len(idx)))
    return pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat,
            Role.OAT: 40 + rng.normal(0, 3, len(idx)),
            Role.OA_DAMPER: np.where(off, 0.0, 30.0),
            Role.HEAT_VALVE: np.where(off, 0.0, 20.0 + rng.normal(0, 2, len(idx))),
            Role.COOL_VALVE: np.zeros(len(idx)),
        },
        index=idx,
    )


# --------------------------------------------------------------------------- 1a per-mode read


def test_tight_controlled_sat_is_trusted_when_read_per_inferred_mode():
    f = _two_mode_unit()
    pooled = frame_sensor_health(f)[Role.SUPPLY_AIR_TEMP]
    assert pooled.verdict == "untrusted" and "outliers" in pooled.flags
    assert pooled.mode_source is None and pooled.mode_outlier_frac is None
    moded = frame_sensor_health(f, mode="auto")[Role.SUPPLY_AIR_TEMP]
    assert moded.verdict == "trusted" and "outliers" not in moded.flags
    assert moded.mode_source.startswith("inferred off-mode")
    assert moded.mode_outlier_frac < 0.05
    # the pooled outlier_frac keeps its meaning
    assert moded.outlier_frac == pooled.outlier_frac


def test_mode_auto_leaves_a_fan_gated_unit_pooled():
    f = _two_mode_unit()
    f[Role.SUPPLY_FAN_STATUS] = 1.0
    a = frame_sensor_health(f, gate="fan")[Role.SUPPLY_AIR_TEMP]
    b = frame_sensor_health(f, gate="fan", mode="auto")[Role.SUPPLY_AIR_TEMP]
    assert a.trust == b.trust and b.mode_source is None


def test_mode_is_not_inferred_without_a_valve_or_when_off_dominates():
    f = _two_mode_unit().drop(columns=[Role.HEAT_VALVE, Role.COOL_VALVE])
    assert frame_sensor_health(f, mode="auto")[Role.SUPPLY_AIR_TEMP].mode_source is None
    g = _two_mode_unit()
    g[Role.OA_DAMPER] = 0.0
    g[Role.HEAT_VALVE] = np.where(np.arange(len(g)) % 10 == 0, 20.0, 0.0)  # off 90 % of samples
    assert frame_sensor_health(g, mode="auto")[Role.SUPPLY_AIR_TEMP].mode_source is None


def test_mode_applies_to_fan_dependent_roles_only_and_never_to_a_plant_gate():
    f = _two_mode_unit()
    mode = pd.Series(np.where(f[Role.OA_DAMPER] == 0, "off", "on"), index=f.index)
    oat = sensor_trust(f[Role.OAT], Role.OAT, mode=mode, mode_source="x")
    assert oat.mode_source is None
    sat = sensor_trust(f[Role.SUPPLY_AIR_TEMP], Role.SUPPLY_AIR_TEMP, mode=mode, mode_source="x")
    assert sat.mode_source == "x" and sat.verdict == "trusted"
    small = pd.Series("on", index=f.index)
    small.iloc[:10] = "off"  # a mode with < 24 samples is judged pooled
    t = sensor_trust(f[Role.SUPPLY_AIR_TEMP], Role.SUPPLY_AIR_TEMP, mode=small, mode_source="x")
    assert t.mode_source == "x" and t.mode_outlier_frac > 0.05
    assert "outliers" in t.flags  # the "on" mode holds both populations: still outliers


def test_mode_rejects_a_bad_value():
    with pytest.raises(ValueError):
        frame_sensor_health(_two_mode_unit(), mode="fan")


# --------------------------------------------------------------------------- 1b stray rows


def _with_stray(series, n=4, gap_days=400):
    stray_idx = pd.date_range(series.index[0] - pd.Timedelta(days=gap_days), periods=n, freq="1h")
    full_idx = pd.date_range(stray_idx[0], series.index[-1], freq="1h")
    out = series.reindex(full_idx)
    out.loc[stray_idx] = float(series.iloc[0])
    return out


def test_stray_lead_rows_are_left_out_of_the_judgement():
    idx = pd.date_range("2018-01-01", periods=60 * 24, freq="1h")
    rat = pd.Series(72 + np.random.default_rng(1).normal(0, 0.4, len(idx)), index=idx)
    s = _with_stray(rat)
    t = sensor_trust(s, Role.RETURN_AIR_TEMP)
    assert "stray_lead" in t.flags and "low_coverage" not in t.flags and t.verdict == "trusted"
    assert t.n_stray == 4 and t.main_start == str(idx[0]) and t.main_end == str(idx[-1])
    assert t.first_valid == str(s.first_valid_index())  # keeps its meaning
    assert t.window_coverage < 0.2


def test_stray_tail_and_a_real_outage_are_told_apart():
    idx = pd.date_range("2018-01-01", periods=60 * 24, freq="1h")
    rat = pd.Series(72 + np.random.default_rng(2).normal(0, 0.4, len(idx)), index=idx)
    tail_idx = pd.date_range(idx[-1] + pd.Timedelta(days=200), periods=3, freq="1h")
    full = rat.reindex(pd.date_range(idx[0], tail_idx[-1], freq="1h"))
    full.loc[tail_idx] = 72.0
    t = sensor_trust(full, Role.RETURN_AIR_TEMP)
    assert "stray_tail" in t.flags and t.n_stray == 3
    # a gap with plenty of data on both sides is an outage, not stray rows
    two = rat.copy()
    two.iloc[20 * 24 : 50 * 24] = np.nan
    u = sensor_trust(two, Role.RETURN_AIR_TEMP)
    assert not {"stray_lead", "stray_tail"} & set(u.flags) and u.n_stray is None


# --------------------------------------------------------------------------- 2 clipped


def _co2_clipped(top=1999.9985, n_top=60):
    rng = np.random.default_rng(3)
    idx = pd.date_range("2018-01-01", periods=24 * 40, freq="1h")
    v = 500 + rng.gamma(2.0, 200.0, len(idx))
    v = np.minimum(v, 1900.0)
    v[rng.choice(len(idx), n_top, replace=False)] = top
    return pd.Series(v, index=idx)


def test_co2_at_full_scale_is_clipped_and_named():
    d = clipped_at_limit(_co2_clipped(), Role.CO2)
    assert d["side"] == "high" and d["limit_label"] == "2,000 ppm" and d["n"] == 60
    assert d["frac"] == pytest.approx(60 / 960, abs=1e-4)


def test_not_clipped_off_round_number_too_few_or_excluded_role():
    assert clipped_at_limit(_co2_clipped(top=1873.4), Role.CO2) is None  # not a round number
    assert clipped_at_limit(_co2_clipped(top=1996.8), Role.CO2) is None  # near, not on, 2000
    assert clipped_at_limit(_co2_clipped(n_top=5), Role.CO2) is None  # too few
    assert clipped_at_limit(_co2_clipped(), Role.SUPPLY_AIR_TEMP) is None  # not checked
    # airflow: the high end only -- a pile at zero is "off"
    flow = _co2_clipped() * 10
    flow.iloc[:200] = 0.0
    d = clipped_at_limit(flow, Role.AIRFLOW)
    assert d["side"] == "high" and d["limit_label"] == "20,000 cfm"


def test_temperature_clip_is_tested_in_celsius_too():
    rng = np.random.default_rng(4)
    idx = pd.date_range("2018-01-01", periods=24 * 40, freq="1h")
    oat = np.minimum(60 + rng.normal(0, 15, len(idx)), 104.0)  # pinned at 40 C = 104 F
    oat[:40] = 104.0
    d = clipped_at_limit(pd.Series(oat, index=idx), Role.OAT)
    assert d is not None and d["limit_label"].startswith("40 °C")


def test_frame_checks_flag_clipped_with_fan_off_share_and_no_penalty():
    co2 = _co2_clipped()
    fan = pd.Series(np.where(co2 >= 1999, 0.0, 1.0), index=co2.index)
    f = pd.DataFrame({Role.CO2: co2, Role.SUPPLY_FAN_STATUS: fan})
    t = frame_sensor_health(f)[Role.CO2]
    assert "clipped" in t.flags and t.clipped["frac_fan_off"] == 1.0
    assert t.trust == frame_sensor_health(f[[Role.CO2]])[Role.CO2].trust
    assert any(c["check"] == "clipped_at_limit" for c in t.frame_checks)
    assert frame_sensor_health(f[[Role.CO2]])[Role.CO2].clipped["frac_fan_off"] is None
    assert t.as_dict()["clipped"]["limit_label"] == "2,000 ppm"


# --------------------------------------------------------------------------- 3 schedules


def _schedule_status(weeks=6, odd_days=()):
    idx = pd.date_range("2018-01-01", periods=weeks * 7 * 96, freq="15min")
    fan = ((idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)).astype(float)
    wk = idx.dayofweek < 5
    fan[wk & (idx.hour == 8) & (idx.minute == 0)] = 0.53
    fan[wk & (idx.hour == 18) & (idx.minute == 0)] = 0.27
    s = pd.Series(fan, index=idx)
    for day in odd_days:  # two days sharing one off-schedule pattern
        for d in day:
            sel = s.index.normalize() == pd.Timestamp(d)
            s[sel & (s.index.hour == 20)] = 0.4
            s[sel & (s.index.hour == 21)] = 0.7
    return s


def test_scheduled_status_is_reported_as_a_schedule_not_a_fill():
    r = gapfill_signature(_schedule_status(), Role.SUPPLY_FAN_STATUS, min_window_samples=100)
    assert r.severity != "warn" and "follow a fixed schedule" in r.summary
    assert r.metrics["scheduled_days"] == 30 and r.metrics["n_schedule_patterns"] == 1
    assert r.metrics["repeated_days"] == []
    # stepwise is also recognised without the role (>= 95 % of samples on two levels)
    assert gapfill_signature(_schedule_status(), min_window_samples=100).severity != "warn"


def test_unexplained_repeats_still_warn_next_to_a_schedule():
    s = _schedule_status(odd_days=[("2018-01-06", "2018-01-13")])
    r = gapfill_signature(s, Role.SUPPLY_FAN_STATUS, min_window_samples=100)
    assert r.severity == "warn" and "1 non-constant days repeat" in r.summary
    assert r.metrics["repeated_days"] == [("2018-01-06", "2018-01-13")]
    assert "follow a fixed schedule" in r.summary


def test_analog_repeats_are_not_excused_as_a_schedule():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2018-01-01", periods=10 * 96, freq="15min")
    day = np.round(400 + rng.normal(0, 50, 96))
    s = pd.Series(np.tile(day, 10), index=idx)  # one analog day copied ten times
    r = gapfill_signature(s, Role.CO2, min_window_samples=100)
    assert r.severity == "warn" and r.metrics["scheduled_days"] == 0
