"""Tests for the sensor-health / data-trust layer (camber.sensorhealth)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.quality import assess  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.sensorhealth import (  # noqa: E402
    PHYSICAL_BOUNDS,
    frame_sensor_health,
    mixing_consistency,
    range_violation_frac,
    sensor_trust,
    trusted_roles,
    untrusted_roles,
)


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _clean_temp(n=24 * 14, base=75.0, amp=15.0):
    """A smooth, healthy temperature sensor (diurnal swing + tiny noise)."""
    rng = np.random.default_rng(0)
    h = np.arange(n)
    return pd.Series(base + amp * np.sin(h / 24 * 2 * np.pi) + rng.normal(0, 0.4, n), index=_idx(n))


# --- physical range ----------------------------------------------------------- #


def test_range_violation_catches_error_sentinels():
    s = _clean_temp().copy()
    s.iloc[::10] = -999.0  # 10% BAS error sentinels
    frac = range_violation_frac(s, Role.OAT)
    assert 0.08 < frac < 0.12


def test_range_violation_clean_and_unbounded():
    valve = pd.Series(np.clip(np.linspace(0, 100, 100), 0, 100), index=_idx(100))
    assert range_violation_frac(valve, Role.COOL_VALVE) == 0.0
    # a role with no defined bounds is not range-checked
    assert np.isnan(range_violation_frac(_clean_temp(), Role.ENERGY_RATE))


# --- per-sensor trust --------------------------------------------------------- #


def test_clean_sensor_is_trusted():
    t = sensor_trust(_clean_temp(), Role.OAT)
    assert t.verdict == "trusted"
    assert t.flags == []


def test_out_of_range_sensor_untrusted():
    s = _clean_temp().copy()
    s.iloc[::5] = -999.0  # 20% impossible readings
    t = sensor_trust(s, Role.OAT)
    assert "out_of_range" in t.flags
    assert t.verdict in ("suspect", "untrusted")
    assert t.trust < sensor_trust(_clean_temp(), Role.OAT).trust


def test_stuck_analog_sensor_flagged():
    stuck = pd.Series(np.full(24 * 14, 72.0), index=_idx(24 * 14))  # frozen reading
    t = sensor_trust(stuck, Role.SUPPLY_AIR_TEMP)
    assert "stuck" in t.flags
    assert t.trust < 0.6


def test_constant_setpoint_not_flagged_stuck():
    # a flat setpoint is normal, not a stuck sensor
    sp = pd.Series(np.full(24 * 14, 74.0), index=_idx(24 * 14))
    t = sensor_trust(sp, Role.COOL_SP)
    assert "stuck" not in t.flags
    assert t.verdict == "trusted"


# --- frame roll-up + gate ----------------------------------------------------- #


def test_frame_health_and_trusted_roles():
    good = _clean_temp()
    bad = _clean_temp().copy()
    bad.iloc[::3] = -999.0  # heavily corrupted
    frame = pd.DataFrame({Role.OAT: good, Role.RETURN_AIR_TEMP: bad})
    health = frame_sensor_health(frame)
    assert health[Role.OAT].verdict == "trusted"
    assert health[Role.RETURN_AIR_TEMP].verdict == "untrusted"

    keep = trusted_roles(frame, min_trust=0.5)
    assert Role.OAT in keep and Role.RETURN_AIR_TEMP not in keep

    # the runner gate's helper: which *required* roles are below the bar
    bad = untrusted_roles(frame, (Role.OAT, Role.RETURN_AIR_TEMP, Role.SPACE_TEMP), min_trust=0.5)
    assert bad == [Role.RETURN_AIR_TEMP]  # OAT trusted; SPACE_TEMP absent -> skipped


# --- cross-sensor consistency ------------------------------------------------- #


def test_mixing_consistency_ok():
    n = 24 * 14
    f = pd.DataFrame(
        {
            Role.OAT: np.full(n, 90.0),
            Role.RETURN_AIR_TEMP: np.full(n, 74.0),
            Role.MIXED_AIR_TEMP: np.full(n, 80.0),  # between OAT and RAT
        },
        index=_idx(n),
    )
    r = mixing_consistency(f)
    assert r.severity == "ok" and r.violation_frac == 0.0


def test_mixing_consistency_fault_on_swapped_sensor():
    n = 24 * 14
    f = pd.DataFrame(
        {
            Role.OAT: np.full(n, 90.0),
            Role.RETURN_AIR_TEMP: np.full(n, 74.0),
            Role.MIXED_AIR_TEMP: np.full(n, 110.0),  # impossibly hotter than both
        },
        index=_idx(n),
    )
    r = mixing_consistency(f)
    assert r.severity == "fault" and r.violation_frac > 0.95


def test_mixing_consistency_missing_inputs_info():
    f = pd.DataFrame({Role.OAT: np.full(20, 90.0)}, index=_idx(20))
    assert mixing_consistency(f).severity == "info"


def test_bounds_table_covers_core_roles():
    for r in (Role.OAT, Role.SUPPLY_AIR_TEMP, Role.COOL_VALVE, Role.POWER):
        assert r in PHYSICAL_BOUNDS


# --- intermittent (duty-cycled) points ------------------------------------- #


def _burst(duty, *, n=720, hi=50.0, noise=0.03, seed=0):
    """A healthy duty-cycled point: `hi` during a daily block, ~0 otherwise. No faults."""
    rng = np.random.default_rng(seed)
    k = max(1, int(24 * duty))
    base = np.where(np.arange(n) % 24 < k, hi, 0.0)
    vals = np.clip(base + rng.normal(0, hi * noise, n), 0.0, None)
    return pd.Series(vals, index=pd.date_range("2024-01-01", periods=n, freq="1h"))


def test_intermittent_meter_is_trusted_not_gated_out():
    """The reported bug: a healthy burst meter scored low enough to silently suppress rules."""
    t = sensor_trust(_burst(0.12), Role.ENERGY_RATE)

    assert t.trust >= 0.9 and t.verdict == "trusted"
    assert "intermittent" in t.flags  # says *why* it scored well
    assert "outliers" not in t.flags


def test_intermittent_roles_are_trusted_across_the_duty_range():
    cases = [
        (Role.HW_FLOW, _burst(0.40)),
        (Role.AIRFLOW, _burst(0.60, hi=9000.0)),
        (Role.CHW_FLOW, _burst(0.80)),
    ]
    for role, series in cases:
        t = sensor_trust(series, role)
        assert t.trust >= 0.9, (role, t.trust)
        assert not untrusted_roles(pd.DataFrame({role: series}), [role], min_trust=0.5)


def test_status_role_at_low_duty_is_trusted():
    """Five rules take a status role as *required*, so the defect gated those too."""
    vals = np.where(np.arange(720) % 24 < 4, 1.0, 0.0)
    s = pd.Series(vals, index=pd.date_range("2024-01-01", periods=720, freq="1h"))
    t = sensor_trust(s, Role.BOILER_STATUS)
    assert t.trust >= 0.9 and "intermittent" in t.flags


def test_railed_flow_stays_untrusted():
    """The sharp case: a genuine fault on a role that IS in the allow-list must not be masked."""
    rng = np.random.default_rng(7)
    vals = 40.0 + rng.normal(0, 3.0, 720)
    vals[rng.random(720) < 0.30] = 0.0  # scattered rail -- not a duty cycle
    s = pd.Series(vals, index=pd.date_range("2024-01-01", periods=720, freq="1h"))

    t = sensor_trust(s, Role.HW_FLOW)
    assert t.trust < 0.5 and t.verdict == "untrusted"
    assert "intermittent" not in t.flags


def test_bimodal_analog_sensor_is_flagged_but_not_exonerated():
    """A role outside the allow-list gets the pooled score -- the flag is information, not mercy."""
    vals = np.where((np.arange(720) // 24) % 2 == 0, 55.0, 75.0)
    s = pd.Series(vals, index=pd.date_range("2024-01-01", periods=720, freq="1h"))

    t = sensor_trust(s, Role.SUPPLY_AIR_TEMP)
    assert "bimodal" in t.flags
    assert "intermittent" not in t.flags
    assert t.outlier_frac == 0.0  # reported pooled, unmasked


def test_continuous_sensor_trust_is_unchanged():
    """Golden: nothing about a well-behaved analog point moves."""
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=720, freq="1h")
    oat = 60 + 15 * np.sin(np.arange(720) / 24 * 2 * np.pi) + rng.normal(0, 1, 720)
    t = sensor_trust(pd.Series(oat, index=idx), Role.OAT)
    assert t.trust > 0.99 and t.verdict == "trusted"
    assert t.flags == []  # in particular: no "bimodal" -- a sine is one population
    assert assess(pd.Series(oat, index=idx)).n_regimes == 1


# --- healthy hydronic plant: skewed / tightly-controlled points ------------ #


def _healthy_hw_plant(n=24 * 90, seed=0):
    """A fault-free HW loop shaped like a simulated boiler plant's year: DP held at setpoint,
    the pump idling dead-headed at ~29 % (zero flow) ~45 % of the time, then ramping with load."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) % 24
    load = np.clip(np.sin((h - 6) / 16 * np.pi), 0.0, None) * (1 + 0.3 * rng.random(n))
    idx = pd.date_range("2018-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {
            Role.HW_PUMP_SPEED: np.clip(28.9 + 55.0 * load**2 + rng.normal(0, 0.1, n), 0, 100),
            Role.HW_FLOW: np.where(load > 0.05, 200.0 * load**2, 0.0)
            + np.abs(rng.normal(0, 0.5, n)),
            Role.HW_DIFF_PRESS: 480.52 + rng.choice([0.0, 0.0, 0.01, -0.01], n),
            Role.HW_SUPPLY_TEMP: 176.0 + rng.normal(0, 0.02, n) - 0.8 * (load > 1.2),
            Role.HW_RETURN_TEMP: 140.0 + np.abs(rng.normal(0, 1.2, n)),  # one-sided tail
        },
        index=idx,
    )


def test_healthy_hw_plant_is_trusted():
    """Regression: a fault-free plant scored 0.2-0.3 on flow/DP/speed and gated detectors off."""
    f = _healthy_hw_plant()
    health = frame_sensor_health(f)
    for role, t in health.items():
        assert t.verdict == "trusted", (role, t.trust, t.outlier_frac)
    assert not untrusted_roles(f, list(f.columns), min_trust=0.5)


def test_spiking_sensor_on_a_healthy_plant_is_still_untrusted():
    f = _healthy_hw_plant()
    bad = f[Role.HW_DIFF_PRESS].copy()
    bad.iloc[::4] = 0.0  # 25 % scattered dropouts to zero
    t = sensor_trust(bad, Role.HW_DIFF_PRESS)
    assert t.verdict == "untrusted" and "outliers" in t.flags
