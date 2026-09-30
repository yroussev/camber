"""Tests for static-pressure reset + damper-distribution census (PNNL Ch.5/Ch.7)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import FleetRule  # noqa: E402
from camber.rules.static_rule import DamperCensus  # noqa: E402
from camber.staticpressure import analyze_static_reset, damper_census  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday


def _boxes(medians):
    n = 24 * 14
    idx = _idx(n)
    return {
        f"VAV_{i}": pd.DataFrame({"Damper": np.full(n, m)}, index=idx)
        for i, m in enumerate(medians)
    }


def test_census_static_too_high():
    # most boxes throttling low -> static too high
    res = damper_census(_boxes([10, 15, 20, 8, 25, 12, 18, 30]))
    assert res.pct_boxes_low >= 60
    assert "TOO HIGH" in res.verdict


def test_census_static_too_low():
    # several boxes pinned open -> static too low
    res = damper_census(_boxes([95, 100, 92, 60, 98, 55]))
    assert res.pct_boxes_high >= 25
    assert "TOO LOW" in res.verdict


def test_census_healthy():
    res = damper_census(_boxes([55, 60, 65, 70, 58, 62]))
    assert "healthy" in res.verdict
    assert res.pct_boxes_in_band >= 50


def test_static_reset_flat_detected():
    n = 24 * 14
    idx = _idx(n)
    df = pd.DataFrame({"DuctStaticSP": np.full(n, 1.0)}, index=idx)  # flat
    r = analyze_static_reset(df, "AHU_1")
    assert r.sp_std < 0.05
    assert not r.sp_reset_present


def test_static_reset_present():
    n = 24 * 14
    idx = _idx(n)
    rng = np.random.default_rng(0)
    sp = 1.0 + 0.3 * np.sin(np.arange(n) / 12) + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"DuctStaticSP": sp}, index=idx)
    r = analyze_static_reset(df, "AHU_2")
    assert r.sp_std >= 0.05
    assert r.sp_reset_present


def test_fleet_rule_protocol_and_severity():
    rule = DamperCensus()
    assert isinstance(rule, FleetRule)
    n = 24 * 14
    idx = _idx(n)
    frames = {
        f"VAV_{i}": pd.DataFrame({Role.DAMPER: np.full(n, m)}, index=idx)
        for i, m in enumerate([10, 12, 15, 8, 20, 18])
    }
    f = rule.analyze_fleet(frames)
    assert f.severity == "fault"
    assert f.metrics["pct_boxes_low"] >= 60


# --- 0.98 (#84): the census reads each box's trended occupancy ------------------------------


def _weekend_boxes(damper_occ=30.0, damper_unocc=100.0, with_occ=True):
    """Boxes on a Saturday-Sunday: occupied 08-20 (trended), throttled when occupied, wide open
    otherwise -- the assumed weekday schedule finds no occupied sample at all."""
    idx = pd.date_range("2025-07-12", periods=48, freq="1h")  # Saturday
    occ = ((idx.hour >= 8) & (idx.hour < 20)).astype(float)
    frames = {}
    for i in range(4):
        cols = {"Damper": np.where(occ > 0, damper_occ + i, damper_unocc)}
        if with_occ:
            cols["Occupancy"] = occ
        frames[f"VAV_{i}"] = pd.DataFrame(cols, index=idx)
    return frames


def test_census_uses_trended_occupancy_on_a_weekend():
    res = damper_census(_weekend_boxes())
    assert res is not None and res.occupancy_gate == "trended occupancy"
    assert res.median_damper_pct == 31.5 and res.pct_boxes_low == 100.0
    # the pre-0.98 behaviour: the weekday schedule finds nothing on a weekend
    assert damper_census(_weekend_boxes(), use_trended_occupancy=False) is None


def test_census_falls_back_to_the_schedule_and_reports_mixed():
    assert damper_census(_weekend_boxes(with_occ=False)) is None  # schedule: no weekday hours
    boxes = _boxes([10, 20, 30])  # schedule boxes (weekdays)
    boxes["VAV_occ"] = pd.DataFrame(
        {"Damper": np.full(len(boxes["VAV_0"]), 15.0), "Occupancy": 1.0},
        index=boxes["VAV_0"].index,
    )
    assert damper_census(_boxes([10, 20])).occupancy_gate == "assumed schedule (weekdays 07-18)"
    assert damper_census(boxes).occupancy_gate == "mixed"
    assert damper_census(boxes, occupied_only=False).occupancy_gate == "off"
    # an all-null occupancy point is not a trended one
    empty = _boxes([10, 20])
    for f in empty.values():
        f["Occupancy"] = np.nan
    assert damper_census(empty).occupancy_gate == "assumed schedule (weekdays 07-18)"


def test_census_drops_warmup_and_cooldown():
    idx = _idx(24 * 7)
    warm = ((idx.hour >= 7) & (idx.hour < 9)).astype(float)
    df = pd.DataFrame({"Damper": np.where(warm > 0, 100.0, 20.0), "WarmUp": warm}, index=idx)
    assert damper_census({"VAV_0": df}).median_damper_pct == 20.0


def test_fleet_rule_occupancy_gate_param():
    frames = {
        e: f.rename(columns={"Damper": Role.DAMPER, "Occupancy": Role.OCCUPANCY})
        for e, f in _weekend_boxes().items()
    }
    assert Role.OCCUPANCY in DamperCensus.roles_optional
    f = DamperCensus().analyze_fleet(frames)
    assert f.severity == "fault" and f.metrics["occupancy_gate"] == "trended occupancy"
    assert DamperCensus(occupancy_gate="schedule").analyze_fleet(frames).summary == "no damper data"
    off = DamperCensus(occupancy_gate="off").analyze_fleet(frames)
    assert off.metrics["occupancy_gate"] == "off"
    try:
        DamperCensus(occupancy_gate="weekday")
    except ValueError as e:
        assert "occupancy_gate" in str(e)
    else:
        raise AssertionError("an unknown occupancy_gate was accepted")
