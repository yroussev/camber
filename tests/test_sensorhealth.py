"""Tests for the sensor-health / data-trust layer (camber.sensorhealth)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.sensorhealth import (  # noqa: E402
    PHYSICAL_BOUNDS, frame_sensor_health, mixing_consistency,
    range_violation_frac, sensor_trust, trusted_roles,
)


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _clean_temp(n=24 * 14, base=75.0, amp=15.0):
    """A smooth, healthy temperature sensor (diurnal swing + tiny noise)."""
    rng = np.random.default_rng(0)
    h = np.arange(n)
    return pd.Series(base + amp * np.sin(h / 24 * 2 * np.pi) + rng.normal(0, 0.4, n),
                     index=_idx(n))


# --- physical range ----------------------------------------------------------- #

def test_range_violation_catches_error_sentinels():
    s = _clean_temp().copy()
    s.iloc[::10] = -999.0                         # 10% BAS error sentinels
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
    s.iloc[::5] = -999.0                          # 20% impossible readings
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
    bad.iloc[::3] = -999.0                        # heavily corrupted
    frame = pd.DataFrame({Role.OAT: good, Role.RETURN_AIR_TEMP: bad})
    health = frame_sensor_health(frame)
    assert health[Role.OAT].verdict == "trusted"
    assert health[Role.RETURN_AIR_TEMP].verdict == "untrusted"

    keep = trusted_roles(frame, min_trust=0.5)
    assert Role.OAT in keep and Role.RETURN_AIR_TEMP not in keep


# --- cross-sensor consistency ------------------------------------------------- #

def test_mixing_consistency_ok():
    n = 24 * 14
    f = pd.DataFrame({
        Role.OAT: np.full(n, 90.0),
        Role.RETURN_AIR_TEMP: np.full(n, 74.0),
        Role.MIXED_AIR_TEMP: np.full(n, 80.0),   # between OAT and RAT
    }, index=_idx(n))
    r = mixing_consistency(f)
    assert r.severity == "ok" and r.violation_frac == 0.0


def test_mixing_consistency_fault_on_swapped_sensor():
    n = 24 * 14
    f = pd.DataFrame({
        Role.OAT: np.full(n, 90.0),
        Role.RETURN_AIR_TEMP: np.full(n, 74.0),
        Role.MIXED_AIR_TEMP: np.full(n, 110.0),  # impossibly hotter than both
    }, index=_idx(n))
    r = mixing_consistency(f)
    assert r.severity == "fault" and r.violation_frac > 0.95


def test_mixing_consistency_missing_inputs_info():
    f = pd.DataFrame({Role.OAT: np.full(20, 90.0)}, index=_idx(20))
    assert mixing_consistency(f).severity == "info"


def test_bounds_table_covers_core_roles():
    for r in (Role.OAT, Role.SUPPLY_AIR_TEMP, Role.COOL_VALVE, Role.POWER):
        assert r in PHYSICAL_BOUNDS
