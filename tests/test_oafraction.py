"""Tests for the outdoor-air-fraction diagnostic (PNNL Ch.5)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.oafraction import analyze_oa_fraction  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _frame(oat, rat, oaf_target):
    # build MAT so that OAF = (RAT-MAT)/(RAT-OAT) == oaf_target
    n = len(oat)
    mat = rat - (oaf_target / 100.0) * (rat - oat)
    return pd.DataFrame({"OAT": oat, "ReturnAir": rat, "MixedAir": mat}, index=_idx(n))


def test_excess_oa_in_cooling_flagged():
    n = 24 * 21
    oat = np.full(n, 95.0)  # hot: cooling weather, OA is a penalty
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=40.0)  # 40% OA, well above 20% min
    r = analyze_oa_fraction(df, "AHU_1", min_oa_pct=20.0)
    assert r.median_oaf_cooling == 40.0
    assert r.excess_oa_pct > 95


def test_minimum_oa_not_flagged():
    n = 24 * 21
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=18.0)  # at/below 20% min
    r = analyze_oa_fraction(df, "AHU_2", min_oa_pct=20.0)
    assert r.excess_oa_pct < 5


def test_under_ventilation_flagged():
    n = 24 * 21
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=3.0)  # stuck-closed: ~3% OA, far below 20% min
    r = analyze_oa_fraction(df, "AHU_4", min_oa_pct=20.0)
    assert r.oaf_median_pct < 10
    assert r.under_vent_pct > 50  # most occupied hours below the minimum

    rule = OutdoorAirFraction(min_oa_pct=20.0)
    frame = pd.DataFrame(
        {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: rat - 0.03 * (rat - oat)},
        index=_idx(n),
    )
    f = rule.analyze("AHU_4", frame)
    assert f.severity == "fault"  # under-ventilation is a fault
    assert "under-ventilation" in f.summary


def test_minimum_oa_is_not_under_ventilation():
    # operating right at the ~min should be neither excess nor under-ventilation
    n = 24 * 21
    rule = OutdoorAirFraction(min_oa_pct=20.0)
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    frame = pd.DataFrame(
        {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: rat - 0.19 * (rat - oat)},
        index=_idx(n),
    )
    assert rule.analyze("AHU_5", frame).severity == "ok"


def test_unstable_denominator_excluded():
    n = 24 * 21
    oat = np.full(n, 73.0)  # RAT-OAT = 1F -> unstable, excluded
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=40.0)
    r = analyze_oa_fraction(df, "AHU_3", denom_min_f=5.0)
    # all rows dropped by the stability guard
    assert r is None


def test_rule_protocol_and_severity():
    rule = OutdoorAirFraction(min_oa_pct=20.0)
    assert isinstance(rule, Rule)
    n = 24 * 21
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    mat = rat - 0.40 * (rat - oat)
    frame = pd.DataFrame(
        {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: mat}, index=_idx(n)
    )
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["excess_oa_pct"] > 95


def _unit(n, oaf_on, oaf_off, *, fan=None, occ=None):
    """A unit at ``oaf_on`` % OA with the fan on and reading ``oaf_off`` % with it off."""
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    on = np.ones(n, bool) if fan is None else fan
    oaf = np.where(on, oaf_on, oaf_off) / 100.0
    cols = {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: rat - oaf * (rat - oat)}
    if fan is not None:
        cols[Role.SUPPLY_FAN_STATUS] = on.astype(float)
    if occ is not None:
        cols[Role.OCCUPANCY] = occ.astype(float)
    return pd.DataFrame(cols, index=_idx(n))


def test_fan_off_samples_are_not_judged_by_default():
    """#23: fan-off still air must not prop up (or sink) the verdict on a running unit."""
    n = 24 * 21
    fan = (np.arange(n) // 12) % 2 == 0  # 12 h on, 12 h off
    frame = _unit(n, oaf_on=2.0, oaf_off=20.0, fan=fan)  # 2 % OA whenever it actually runs
    gated = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU", frame)
    assert gated.severity == "fault" and gated.metrics["fan_gate"] == "fan status"
    assert gated.metrics["oaf_median_pct"] == 2.0
    ungated = OutdoorAirFraction(min_oa_pct=20.0, fan_gate=False).analyze("AHU", frame)
    assert ungated.metrics["fan_gate"] == "off"
    assert ungated.metrics["oaf_median_pct"] > 2.0  # fan-off samples dilute it
    # with the unit's own minimum it is healthy
    assert OutdoorAirFraction(min_oa_pct=1.6).analyze("AHU", frame).severity == "ok"


def test_no_fan_signal_runs_ungated_and_says_so():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU", _unit(24 * 21, 40.0, 40.0))
    assert f.metrics["fan_gate"].startswith("ungated") and "not fan-gated" in f.summary


def test_trended_occupancy_replaces_the_assumed_schedule():
    n = 24 * 21
    idx = _idx(n)
    # occupied only on weekends: the assumed Mon-Fri schedule would judge the wrong hours
    occ = np.asarray(idx.dayofweek >= 5)
    oat, rat = np.full(n, 95.0), np.full(n, 74.0)
    oaf = np.where(occ, 0.40, 0.20)
    frame = pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: rat - oaf * (rat - oat),
            Role.OCCUPANCY: occ.astype(float),
        },
        index=idx,
    )
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU", frame)
    assert f.metrics["occupancy"] == "trended" and f.severity == "fault"
    assert f.metrics["oaf_median_pct"] == 40.0
    no_occ = frame.drop(columns=[Role.OCCUPANCY])
    g = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU", no_occ)
    assert g.metrics["occupancy"] == "assumed" and g.severity == "ok"


def test_seasonal_minimum_judges_each_month_against_its_own():
    idx = pd.date_range("2025-01-01", periods=24 * 365, freq="1h")
    summer = np.asarray(idx.month.isin([6, 7, 8]))
    oat = np.full(len(idx), 95.0)
    rat = np.full(len(idx), 74.0)
    oaf = np.where(summer, 0.12, 0.32)  # 28 % damper in summer, 45 % otherwise
    frame = pd.DataFrame(
        {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: rat - oaf * (rat - oat)},
        index=idx,
    )
    seasonal = {6: 11.9, 7: 11.9, 8: 11.9}
    f = OutdoorAirFraction(min_oa_pct=31.8, min_oa_pct_by_month=seasonal).analyze("AHU", frame)
    assert f.severity == "ok" and f.metrics["min_oa_pct_by_month"] == seasonal
    assert "11.9% in months 6, 7, 8" in f.summary
    # one annual minimum reads the healthy winter as excess OA
    assert OutdoorAirFraction(min_oa_pct=11.9).analyze("AHU", frame).severity != "ok"
    # stuck at the summer position all year: under-ventilated outside summer
    stuck = frame.copy()
    stuck[Role.MIXED_AIR_TEMP] = rat - 0.12 * (rat - oat)
    g = OutdoorAirFraction(min_oa_pct=31.8, min_oa_pct_by_month=seasonal).analyze("AHU", stuck)
    assert g.severity in ("warn", "fault") and "under-ventilation" in g.summary
    r = analyze_oa_fraction(
        stuck.rename(
            columns={
                Role.OAT: "OAT",
                Role.RETURN_AIR_TEMP: "ReturnAir",
                Role.MIXED_AIR_TEMP: "MixedAir",
            }
        ),
        "AHU",
        min_oa_pct=31.8,
        min_oa_by_month={"6": 11.9},
    )
    assert r.min_oa_by_month == {6: 11.9} and r.median_vs_min_pct < -5
    import pytest

    with pytest.raises(ValueError, match="months 1-12"):
        OutdoorAirFraction(min_oa_pct_by_month={13: 5.0})
