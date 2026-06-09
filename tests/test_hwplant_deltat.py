"""Tests for hot-water plant loop low-deltaT (camber.plant + the HWPlantDeltaT rule)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.plant import analyze_hw_plant  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.hwplant_deltat_rule import HWPlantDeltaT  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # starts Monday


def _hwplant(n, hws, hwr, running=1):
    return pd.DataFrame({
        "BoilerStatus": np.full(n, running, dtype=float),
        "HWS_Temp": np.full(n, hws, dtype=float),
        "HWR_Temp": np.full(n, hwr, dtype=float),
    }, index=_idx(n))


# --- diagnostic --------------------------------------------------------------- #

def test_low_deltaT_flagged():
    # boiler running, but loop dT only 10F (HWS 140 / HWR 130) -> low-deltaT
    r = analyze_hw_plant(_hwplant(24 * 14, 140, 130), "HWP", design_deltaT_min_f=20.0)
    assert r is not None
    assert r.deltaT_median_f == 10.0
    assert r.low_deltaT_pct > 95


def test_healthy_deltaT_not_flagged():
    r = analyze_hw_plant(_hwplant(24 * 14, 140, 110), "HWP", design_deltaT_min_f=20.0)
    assert r.deltaT_median_f == 30.0
    assert r.low_deltaT_pct < 5


def test_deltaT_nan_when_boiler_off():
    # boiler never runs -> no running hours -> loop dT undefined (NaN)
    r = analyze_hw_plant(_hwplant(24 * 14, 140, 130, running=0), "HWP")
    assert r is not None
    assert r.deltaT_median_f != r.deltaT_median_f   # NaN


# --- rule wrapper ------------------------------------------------------------- #

def test_rule_is_a_rule_and_severity():
    assert isinstance(HWPlantDeltaT(), Rule)
    n = 24 * 14

    def role_frame(hws, hwr):
        return pd.DataFrame({
            Role.BOILER_STATUS: np.ones(n),
            Role.HW_SUPPLY_TEMP: np.full(n, hws, dtype=float),
            Role.HW_RETURN_TEMP: np.full(n, hwr, dtype=float),
        }, index=_idx(n))

    rule = HWPlantDeltaT(design_deltaT_min_f=20.0)
    assert rule.analyze("HWP", role_frame(140, 110)).severity == "ok"     # 30F dT
    assert rule.analyze("HWP", role_frame(140, 130)).severity == "fault"  # 10F dT, all low


def test_rule_reports_info_when_not_running():
    n = 24 * 14
    frame = pd.DataFrame({
        Role.BOILER_STATUS: np.zeros(n),
        Role.HW_SUPPLY_TEMP: np.full(n, 140.0),
        Role.HW_RETURN_TEMP: np.full(n, 130.0),
    }, index=_idx(n))
    assert HWPlantDeltaT().analyze("HWP", frame).severity == "info"
