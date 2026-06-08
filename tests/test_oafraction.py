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
    oat = np.full(n, 95.0)          # hot: cooling weather, OA is a penalty
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=40.0)   # 40% OA, well above 20% min
    r = analyze_oa_fraction(df, "AHU_1", min_oa_pct=20.0)
    assert r.median_oaf_cooling == 40.0
    assert r.excess_oa_pct > 95


def test_minimum_oa_not_flagged():
    n = 24 * 21
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=18.0)   # at/below 20% min
    r = analyze_oa_fraction(df, "AHU_2", min_oa_pct=20.0)
    assert r.excess_oa_pct < 5


def test_under_ventilation_flagged():
    n = 24 * 21
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    df = _frame(oat, rat, oaf_target=3.0)    # stuck-closed: ~3% OA, far below 20% min
    r = analyze_oa_fraction(df, "AHU_4", min_oa_pct=20.0)
    assert r.oaf_median_pct < 10
    assert r.under_vent_pct > 50             # most occupied hours below the minimum

    rule = OutdoorAirFraction(min_oa_pct=20.0)
    frame = pd.DataFrame({Role.OAT: oat, Role.RETURN_AIR_TEMP: rat,
                          Role.MIXED_AIR_TEMP: rat - 0.03 * (rat - oat)}, index=_idx(n))
    f = rule.analyze("AHU_4", frame)
    assert f.severity == "fault"             # under-ventilation is a fault
    assert "under-ventilation" in f.summary


def test_minimum_oa_is_not_under_ventilation():
    # operating right at the ~min should be neither excess nor under-ventilation
    n = 24 * 21
    rule = OutdoorAirFraction(min_oa_pct=20.0)
    oat = np.full(n, 95.0)
    rat = np.full(n, 74.0)
    frame = pd.DataFrame({Role.OAT: oat, Role.RETURN_AIR_TEMP: rat,
                          Role.MIXED_AIR_TEMP: rat - 0.19 * (rat - oat)}, index=_idx(n))
    assert rule.analyze("AHU_5", frame).severity == "ok"


def test_unstable_denominator_excluded():
    n = 24 * 21
    oat = np.full(n, 73.0)          # RAT-OAT = 1F -> unstable, excluded
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
    frame = pd.DataFrame({Role.OAT: oat, Role.RETURN_AIR_TEMP: rat,
                          Role.MIXED_AIR_TEMP: mat}, index=_idx(n))
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["excess_oa_pct"] > 95
