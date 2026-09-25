"""Tests for the cooling-tower approach diagnostic (CW supply vs wet-bulb)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.coolingtower import (  # noqa: E402
    analyze_cooling_tower_approach,
    stull_wetbulb_f,
)
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.coolingtower_rule import CoolingTowerApproach  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _tower(n, approach_f, wetbulb_f=68.0, rng_f=10.0):
    """Steady tower frame at a given approach (legacy columns), wet-bulb measured."""
    cws = wetbulb_f + approach_f
    return pd.DataFrame(
        {
            "CWS_Temp": np.full(n, cws),
            "CWR_Temp": np.full(n, cws + rng_f),
            "WetBulb": np.full(n, wetbulb_f),
        },
        index=_idx(n),
    )


# --- diagnostic --------------------------------------------------------------- #


def test_good_approach_not_flagged():
    r = analyze_cooling_tower_approach(_tower(24 * 14, 5.0), "CT-1", design_approach_f=7.0)
    assert r is not None
    assert abs(r.approach_median_f - 5.0) < 0.2
    assert r.wetbulb_source == "measured"
    assert r.pct_hours_high_approach < 5


def test_high_approach_flagged():
    r = analyze_cooling_tower_approach(_tower(24 * 14, 14.0), "CT-1", design_approach_f=7.0)
    assert abs(r.approach_median_f - 14.0) < 0.2
    assert r.pct_hours_high_approach > 95


def test_wetbulb_derived_from_oat_rh():
    n = 24 * 14
    # OAT 95F / RH 35% -> wet-bulb ~73F (Stull); CW supply 80F -> approach ~7F
    df = pd.DataFrame(
        {
            "CWS_Temp": np.full(n, 80.0),
            "CWR_Temp": np.full(n, 90.0),
            "OAT": np.full(n, 95.0),
            "RH": np.full(n, 35.0),
        },
        index=_idx(n),
    )
    r = analyze_cooling_tower_approach(df, "CT-1", design_approach_f=7.0)
    assert r.wetbulb_source == "derived"
    assert 4.0 < r.approach_median_f < 11.0  # plausible derived approach


def test_insufficient_data_returns_none():
    n = 24
    # no wet-bulb and no OAT+RH -> cannot compute approach
    df = pd.DataFrame({"CWS_Temp": np.full(n, 80.0)}, index=_idx(n))
    assert analyze_cooling_tower_approach(df, "CT-1") is None


def test_stull_wetbulb_saturated_approaches_drybulb():
    # at 100% RH the wet-bulb equals the dry-bulb
    assert abs(stull_wetbulb_f(80.0, 100.0) - 80.0) < 1.5
    # wet-bulb is always <= dry-bulb in unsaturated air
    assert stull_wetbulb_f(95.0, 30.0) < 95.0


# --- rule wrapper ------------------------------------------------------------- #


def test_rule_is_a_rule_and_severity_scales():
    assert isinstance(CoolingTowerApproach(), Rule)

    def role_frame(approach_f):
        n = 24 * 14
        cws = 68.0 + approach_f
        return pd.DataFrame(
            {
                Role.CW_SUPPLY_TEMP: np.full(n, cws),
                Role.CW_RETURN_TEMP: np.full(n, cws + 10.0),
                Role.WETBULB_TEMP: np.full(n, 68.0),
            },
            index=_idx(n),
        )

    rule = CoolingTowerApproach(design_approach_f=7.0)
    assert rule.analyze("CT-1", role_frame(5.0)).severity == "ok"
    assert rule.analyze("CT-1", role_frame(10.0)).severity == "warn"  # 1.43x design
    assert rule.analyze("CT-1", role_frame(13.0)).severity == "fault"  # 1.86x design


def test_rule_missing_roles_reports_info():
    n = 24
    frame = pd.DataFrame({Role.CW_SUPPLY_TEMP: np.full(n, 80.0)}, index=_idx(n))
    assert CoolingTowerApproach().analyze("CT-1", frame).severity == "info"


def _winter_floor_frame(n=96):
    """A healthy tower in cold weather held at a 60F minimum condenser-water temperature: the fan
    idles at its minimum and the leaving water sits far above wet-bulb + design -- by control."""
    idx = pd.date_range("2025-01-06", periods=n, freq="1h")
    return pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: np.full(n, 60.0),
            Role.WETBULB_TEMP: np.full(n, 40.0),  # approach 20F, all of it the setpoint floor
            Role.TOWER_FAN_SPEED: np.full(n, 29.0),
        },
        index=idx,
    )


def test_minimum_leaving_temp_at_part_fan_is_not_a_fault():
    """The LBNL chiller-plant bypass runs: the tower ran all winter at its 60F floor with the fan
    at minimum, and the approach rule called it a fouled tower."""
    got = CoolingTowerApproach(design_approach_f=8.0).analyze("CT-1", _winter_floor_frame())
    assert got.severity == "info" and got.metrics["declined"] is True
    # the old "fan running" gate reproduces the false alarm
    old = CoolingTowerApproach(design_approach_f=8.0, min_effort_pct=None).analyze(
        "CT-1", _winter_floor_frame()
    )
    assert old.severity == "fault"


def test_high_approach_at_full_fan_still_fires():
    frame = _winter_floor_frame()
    frame[Role.TOWER_FAN_SPEED] = 100.0  # fan flat out, still 20F off wet-bulb: cannot reject heat
    got = CoolingTowerApproach(design_approach_f=8.0).analyze("CT-1", frame)
    assert got.severity == "fault" and got.metrics["effort_gated"] is True


def test_no_fan_trend_is_caveated():
    frame = _winter_floor_frame().drop(columns=[Role.TOWER_FAN_SPEED])
    got = CoolingTowerApproach(design_approach_f=8.0).analyze("CT-1", frame)
    assert got.metrics["effort_gated"] is None
    assert any("minimum condenser-water temperature" in c for c in got.caveats)
