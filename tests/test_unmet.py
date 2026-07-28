"""Tests for the unmet-setpoint-hours rule (camber.rules.unmet_rule) + ASO/registration."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.aso import recommend  # noqa: E402
from camber.charts.evidence import finding_evidence  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import rule_names  # noqa: E402
from camber.rules.unmet_rule import UnmetHours  # noqa: E402


def _frame(space_occ, space_unocc=71.0, days=10):
    idx = pd.date_range("2024-07-01", periods=days * 24, freq="1h")
    occ = ((idx.hour >= 7) & (idx.hour <= 18)).astype(float)
    st = pd.Series(np.where(occ > 0, space_occ, space_unocc), index=idx)
    return pd.DataFrame(
        {
            Role.SPACE_TEMP: st,
            Role.COOL_SP: pd.Series(74.0, index=idx),
            Role.HEAT_SP: pd.Series(68.0, index=idx),
            Role.OCCUPANCY: pd.Series(occ, index=idx),
        }
    )


def test_comfortable_zone_is_ok():
    f = UnmetHours().analyze("Z1", _frame(72.0))
    assert f.severity == "ok" and f.metrics["unmet_pct"] == 0.0


def test_too_hot_faults():
    f = UnmetHours().analyze("Z2", _frame(78.0))  # above the 74 cooling SP
    assert f.severity == "fault"
    assert f.metrics["too_hot_pct"] > 90 and f.metrics["too_cold_pct"] == 0.0


def test_too_cold_faults():
    f = UnmetHours().analyze("Z3", _frame(64.0))  # below the 68 heating SP
    assert f.severity == "fault" and f.metrics["too_cold_pct"] > 90


def test_only_occupied_hours_counted():
    # comfortable while occupied, wildly hot only when unoccupied -> not unmet
    f = UnmetHours().analyze("Z4", _frame(72.0, space_unocc=90.0))
    assert f.severity == "ok" and f.metrics["unmet_pct"] == 0.0


def test_info_when_no_setpoint_present():
    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    f = UnmetHours().analyze("Z5", pd.DataFrame({Role.SPACE_TEMP: pd.Series(80.0, index=idx)}))
    assert f.severity == "info"


def test_evidence_recommendation_and_registration():
    frame = _frame(78.0)
    f = UnmetHours().analyze("Z2", frame)
    ev = finding_evidence(UnmetHours(), "Z2", frame)
    assert ev is not None and ev.renderer == "multitrend" and len(ev.roles) >= 2
    rec = recommend(f)
    assert rec is not None and "cooling" in rec.action  # leans to the too-hot side
    assert "unmet_setpoint_hours" in rule_names()
