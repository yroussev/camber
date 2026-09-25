"""Tests for percent/position unit normalization (units.py)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.units import (  # noqa: E402
    looks_like_fraction,
    normalize_percent,
    normalize_percent_frame,
)


def test_looks_like_fraction():
    assert looks_like_fraction(pd.Series([0.0, 0.5, 1.0]))
    assert not looks_like_fraction(pd.Series([0.0, 50.0, 100.0]))
    assert not looks_like_fraction(pd.Series([], dtype="float64"))
    assert not looks_like_fraction(pd.Series([-5.0, 0.5]))  # negatives -> not a fraction


def test_normalize_percent_scales_fraction_only():
    frac = pd.Series([0.1, 0.5, 1.0])
    assert list(normalize_percent(frac)) == [10.0, 50.0, 100.0]
    pct = pd.Series([0.0, 50.0, 100.0])
    assert list(normalize_percent(pct)) == [0.0, 50.0, 100.0]  # unchanged


def test_normalize_frame_touches_only_percent_roles():
    frame = pd.DataFrame(
        {
            Role.COOL_VALVE: [0.1, 0.5, 1.0],  # fraction -> scale
            Role.HEAT_VALVE: [0.0, 50.0, 100.0],  # already percent -> unchanged
            Role.SUPPLY_AIR_TEMP: [0.5, 0.6, 0.7],  # not a percent role -> unchanged
        }
    )
    out = normalize_percent_frame(frame)
    assert list(out[Role.COOL_VALVE]) == [10.0, 50.0, 100.0]
    assert list(out[Role.HEAT_VALVE]) == [0.0, 50.0, 100.0]
    assert list(out[Role.SUPPLY_AIR_TEMP]) == [0.5, 0.6, 0.7]  # temp left alone


def test_percent_roles_cover_every_percent_bounded_role():
    """Every role the sensor-health gate bounds as a percent must also be rescaled from 0-1.

    A 0-1 tower fan speed once never cleared the tower rule's "fan > 5%" gate, so the rule never
    ran -- the role was bounded as a percent but missing from PERCENT_ROLES.
    """
    from camber.sensorhealth import PHYSICAL_BOUNDS
    from camber.units import PERCENT_ROLES

    percent_bounded = {r for r, (_lo, hi) in PHYSICAL_BOUNDS.items() if hi == 102.0}
    missing = sorted(r.value for r in percent_bounded - PERCENT_ROLES)
    assert not missing, missing


def test_tower_rule_runs_on_fraction_fan_speed():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.rules.coolingtower_rule import CoolingTowerApproach
    from camber.units import normalize_percent_frame

    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    frame = pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: np.full(48, 85.0),
            Role.WETBULB_TEMP: np.full(48, 70.0),  # 15F approach: high
            Role.TOWER_FAN_SPEED: np.full(48, 0.95),  # a 0-1 BAS point, fan near full
        },
        index=idx,
    )
    got = CoolingTowerApproach().analyze("CT-1", normalize_percent_frame(frame))
    assert got.severity in ("warn", "fault")
