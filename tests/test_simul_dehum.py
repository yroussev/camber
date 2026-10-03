"""0.93 (#41): simultaneous_heat_cool tells dehumidification with reheat from coil fighting."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahu import analyze_ahu  # noqa: E402
from camber.model.roles import HAYSTACK_HINT, Role  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool, _dew_point_f  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402

IDX = pd.date_range("2025-06-02", periods=24 * 14, freq="1h")  # two weeks from a Monday

# ============================================================================ #41


def _ahu(*, cclt=None, oat=70.0, oa_rh=None, rat=74.0, ra_rh=None, sat=60.0, fan=1.0):
    """Cooling 60 % and post-heat 40 % all the time -- both coils open on every sample."""
    n = len(IDX)
    cols = {
        Role.COOL_VALVE: np.full(n, 60.0),
        Role.HEAT_VALVE: np.full(n, 40.0),
        Role.SUPPLY_AIR_TEMP: np.full(n, sat),
        Role.OAT: np.full(n, oat),
        Role.RETURN_AIR_TEMP: np.full(n, rat),
        Role.SUPPLY_FAN_STATUS: np.full(n, fan),
    }
    if cclt is not None:
        cols[Role.COOL_COIL_LEAVING_TEMP] = np.full(n, cclt)
    if oa_rh is not None:
        cols[Role.OUTDOOR_RH] = np.full(n, oa_rh)
    if ra_rh is not None:
        cols[Role.RETURN_AIR_HUMIDITY] = np.full(n, ra_rh)
    return pd.DataFrame(cols, index=IDX)


def test_new_role_is_wired():
    r = Role.COOL_COIL_LEAVING_TEMP
    assert r.value == "cool_coil_leaving_temp" and r in HAYSTACK_HINT and r in PHYSICAL_BOUNDS


def test_dew_point_magnus():
    dp = _dew_point_f(pd.Series([70.0, 86.0]), pd.Series([50.0, 80.0]))
    assert abs(dp.iloc[0] - 50.5) < 0.5 and abs(dp.iloc[1] - 79.0) < 0.6
    frac = _dew_point_f(pd.Series([70.0, 86.0]), pd.Series([0.5, 0.8]))  # a 0-1 humidity
    assert (frac - dp).abs().max() < 1e-9
    assert _dew_point_f(pd.Series([70.0]), pd.Series([150.0])).isna().all()


def test_wet_coil_with_reheat_is_dehumidification_not_a_fault():
    # outdoor 70 F / 80 % -> dew point ~63.6 F; the coil leaves at 53 F, reheated to 60 F
    f = SimultaneousHeatCool().analyze("AHU-1", _ahu(cclt=53.0, oa_rh=80.0, ra_rh=60.0))
    assert f.metrics["simultaneous_hc_pct"] > 90 and f.metrics["dehum_reheat_pct"] > 90
    assert f.metrics["unexplained_hc_pct"] < 1 and f.severity == "info"
    assert any("dehumidification with reheat" in c for c in f.caveats)
    # declared: the same reading is expected operation
    g = SimultaneousHeatCool(dehumidification=True).analyze(
        "AHU-1", _ahu(cclt=53.0, oa_rh=80.0, ra_rh=60.0)
    )
    assert g.severity == "ok" and g.metrics["dehum_reheat_pct"] > 90


def test_dry_coil_with_reheat_is_still_simultaneous_heating_and_cooling():
    # outdoor 70 F / 30 % -> dew point ~37 F; a coil at 53 F removes no moisture
    f = SimultaneousHeatCool().analyze("AHU-1", _ahu(cclt=53.0, oa_rh=30.0, ra_rh=30.0))
    assert f.severity == "fault" and f.metrics["unexplained_hc_pct"] > 90
    # a declared sequence does not excuse a dry coil either
    g = SimultaneousHeatCool(dehumidification=True).analyze(
        "AHU-1", _ahu(cclt=53.0, oa_rh=30.0, ra_rh=30.0)
    )
    assert g.severity == "fault"


def test_incomplete_evidence_is_a_caveat_not_a_fault():
    # reheat after the coil, but no humidity at all -> possibly dehumidification -> warn at most
    f = SimultaneousHeatCool().analyze("AHU-1", _ahu(cclt=53.0))
    assert f.severity == "warn" and f.metrics["dehum_possible_pct"] > 90
    assert any("may be dehumidification" in c for c in f.caveats)
    # high return humidity but no coil-leaving temperature -> the same
    g = SimultaneousHeatCool().analyze("AHU-1", _ahu(ra_rh=65.0))
    assert g.severity == "warn" and g.metrics["dehum_possible_pct"] > 90
    # declared + reheat after the coil -> dehumidification
    h = SimultaneousHeatCool(dehumidification=True).analyze("AHU-1", _ahu(cclt=53.0))
    assert h.severity == "ok" and h.metrics["dehum_reheat_pct"] > 90


def test_fan_off_and_no_reheat_lift_are_not_dehumidification():
    f = SimultaneousHeatCool().analyze("AHU-1", _ahu(cclt=53.0, oa_rh=80.0, fan=0.0))
    assert f.severity == "fault" and f.metrics["dehum_reheat_pct"] == 0.0
    # the heat is not after the coil: supply no warmer than the coil leaving air
    g = SimultaneousHeatCool().analyze("AHU-1", _ahu(cclt=60.0, sat=60.0, oa_rh=80.0))
    assert g.severity == "fault"


def test_declared_absent_or_no_evidence_counts_as_before():
    no = SimultaneousHeatCool(dehumidification=False).analyze(
        "AHU-1", _ahu(cclt=53.0, oa_rh=80.0, ra_rh=60.0)
    )
    assert no.severity == "fault" and "dehum_reheat_pct" not in no.metrics
    bare = _ahu().drop(columns=[Role.SUPPLY_AIR_TEMP])
    f = SimultaneousHeatCool().analyze("AHU-1", bare)
    assert f.severity == "fault" and "unexplained_hc_pct" not in f.metrics
    assert any("could not be ruled out" in c for c in f.caveats)
    with pytest.raises(ValueError):
        SimultaneousHeatCool(dehumidification="yes")


def test_analyze_ahu_class_breakdown():
    df = pd.DataFrame({"CHW_Valve": 50.0, "HHW_Valve": [30.0, 0.0] * (len(IDX) // 2)}, index=IDX)
    half = pd.Series(IDX.day % 2 == 0, index=IDX)
    res = analyze_ahu(df, "AHU-1", simul_classes={"x": half})
    assert 0 < res.simul_class_pct["x"] < res.simultaneous_hc_pct
    assert analyze_ahu(df, "AHU-1").simul_class_pct is None
