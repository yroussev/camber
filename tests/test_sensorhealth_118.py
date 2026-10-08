"""#118: with the fan gate on, a stuck run is counted in fan-on hours across fan-off spans.

A supply-air sensor frozen for days on a scheduled fan had every flat run broken at fan-off, so no
run reached the 24 h limit. A run now joins across an off span when the reading also held its value
through it; a reading that moves with the fan off still starts a new run.
"""

import numpy as np
import pandas as pd

from camber.model.roles import Role
from camber.sensorhealth import sensor_trust

_IDX = pd.date_range("2018-01-01", periods=14 * 24 * 4, freq="15min")
_FAN = pd.Series((_IDX.hour >= 6) & (_IDX.hour < 19), index=_IDX)  # 13 h a day


def _healthy_sat(seed=0):
    """Supply air near 55 F with the fan on, drifting towards the plenum with it off."""
    rng = np.random.default_rng(seed)
    off = 55.0 + 15.0 * (1 - np.exp(-((_IDX.hour - 19) % 24) / 3.0))
    v = np.where(_FAN, 55.0 + rng.normal(0, 0.3, len(_IDX)), off + rng.normal(0, 0.3, len(_IDX)))
    return pd.Series(np.round(v, 1), index=_IDX)


def test_stuck_sensor_on_a_scheduled_fan_is_flagged():
    s = _healthy_sat()
    frozen = (_IDX >= "2018-01-03") & (_IDX < "2018-01-07")  # 4 days held at one value
    s[frozen] = 57.3
    t = sensor_trust(s, Role.SUPPLY_AIR_TEMP, gate=_FAN)
    assert "stuck" in t.flags
    assert len(t.stuck_intervals) == 1
    iv = t.stuck_intervals[0]
    assert iv["value"] == 57.3
    assert iv["hours"] == 4 * 13  # fan-on hours, not the 96 h of wall-clock
    assert t.longest_flat_hours == 52.0


def test_reading_that_changes_across_fan_off_is_not_flagged():
    # exactly 55.0 every fan-on stretch (a coarse sensor at setpoint), plenum temperature with the
    # fan off: each on stretch is its own run
    s = pd.Series(np.where(_FAN, 55.0, 68.0), index=_IDX)
    t = sensor_trust(s, Role.SUPPLY_AIR_TEMP, gate=_FAN)
    assert not t.stuck_intervals
    assert t.longest_flat_hours == 13.0


def test_flat_during_short_occupied_spans_is_not_flagged():
    # healthy most of the time, legitimately flat for a few hours of each occupied span
    s = _healthy_sat(1)
    held = _FAN & (_IDX.hour >= 10) & (_IDX.hour < 14)
    s[held] = 55.0
    t = sensor_trust(s, Role.SUPPLY_AIR_TEMP, gate=_FAN)
    assert "stuck" not in t.flags
    assert not t.stuck_intervals
    assert t.longest_flat_hours < 24.0
