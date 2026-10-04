"""#103: the setpoint level is not a grouping key for the outlier read (measured and rejected).

Reading a controlled point's outliers within each level of a few-valued setpoint marked healthy
supply air down on the catalog data (see docs/SENSOR-HEALTH.md). These tests pin that
``mode="auto"`` keeps its 0.98 behaviour when a setpoint with two to four levels is trended.
"""

import numpy as np
import pandas as pd

from camber.model.roles import Role
from camber.sensorhealth import frame_sensor_health


def _stepped_setpoint_unit(seed=0, fan=True):
    """An AHU whose supply-air and static setpoints step between two levels (occupied / setback),
    the points tracking them closely."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=28 * 24, freq="1h")
    occ = (idx.hour >= 7) & (idx.hour < 19)
    sat_sp = np.where(occ, 55.0, 65.0)
    st_sp = np.where(occ, 1.5, 0.8)
    f = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat_sp + rng.normal(0, 0.1, len(idx)),
            Role.SUPPLY_AIR_TEMP_SP: sat_sp,
            Role.DUCT_STATIC: st_sp + rng.normal(0, 0.02, len(idx)),
            Role.DUCT_STATIC_SP: st_sp,
            Role.OAT: 40 + rng.normal(0, 3, len(idx)),
        },
        index=idx,
    )
    if fan:
        f[Role.SUPPLY_FAN_STATUS] = 1.0
    return f


def test_fan_gated_unit_with_a_stepped_setpoint_keeps_the_pooled_read():
    f = _stepped_setpoint_unit()
    a = frame_sensor_health(f, gate="fan")
    b = frame_sensor_health(f, gate="fan", mode="auto")
    for role in (Role.SUPPLY_AIR_TEMP, Role.DUCT_STATIC):
        assert b[role].mode_source is None and b[role].mode_outlier_frac is None
        assert a[role].trust == b[role].trust


def test_fan_less_unit_with_a_stepped_setpoint_is_not_split_by_it():
    # no fan signal and no damper / valves: no off-mode can be inferred, and the setpoint
    # levels are not used in its place
    f = _stepped_setpoint_unit(fan=False)
    pooled = frame_sensor_health(f)
    moded = frame_sensor_health(f, mode="auto")
    for role in (Role.SUPPLY_AIR_TEMP, Role.DUCT_STATIC):
        assert moded[role].mode_source is None
        assert moded[role].trust == pooled[role].trust
