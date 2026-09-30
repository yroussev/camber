"""Tests for the supply-air control rule (camber.rules.satcontrol_rule) + ASO/registration."""

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
from camber.rules.satcontrol_rule import SupplyAirControl  # noqa: E402


def _frame(sat, sp=55.0, fan=1.0, n=240):
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    sat = sat if isinstance(sat, pd.Series) else pd.Series(sat, index=idx)
    return pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: pd.Series(sp, index=idx),
            Role.SUPPLY_FAN_STATUS: pd.Series(fan, index=idx),
        }
    )


def test_tracking_setpoint_is_ok():
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    sat = pd.Series(55 + np.random.default_rng(0).normal(0, 0.5, 240), index=idx)
    f = SupplyAirControl().analyze("AHU-1", _frame(sat))
    assert f.severity == "ok" and f.metrics["off_setpoint_pct"] < 5


def test_too_warm_faults():
    f = SupplyAirControl().analyze("AHU-2", _frame(62.0))  # 7F above the 55 setpoint
    assert f.severity == "fault" and f.metrics["too_warm_pct"] > 90
    assert f.metrics["mean_abs_dev_F"] > 5


def test_too_cold_faults():
    f = SupplyAirControl().analyze("AHU-3", _frame(48.0))  # 7F below setpoint
    assert f.severity == "fault" and f.metrics["too_cold_pct"] > 90


def test_only_running_hours_counted():
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    # SAT drifts far off only while the fan is OFF -> not a control fault
    fan = pd.Series(np.where(idx.hour < 12, 1.0, 0.0), index=idx)
    sat = pd.Series(np.where(idx.hour < 12, 55.0, 75.0), index=idx)
    frame = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: pd.Series(55.0, index=idx),
            Role.SUPPLY_FAN_STATUS: fan,
        }
    )
    f = SupplyAirControl().analyze("AHU-4", frame)
    assert f.severity == "ok" and f.metrics["off_setpoint_pct"] == 0.0


def test_info_when_fan_never_runs():
    f = SupplyAirControl().analyze("AHU-5", _frame(62.0, fan=0.0))
    assert f.severity == "info"


def test_evidence_recommendation_and_registration():
    frame = _frame(62.0)
    f = SupplyAirControl().analyze("AHU-2", frame)
    ev = finding_evidence(SupplyAirControl(), "AHU-2", frame)
    assert ev is not None and ev.renderer == "multitrend"
    assert recommend(f) is not None and "supply_air_control" in rule_names()


# --- 0.98 (#84): the occupancy gate ----------------------------------------------------------


def _cycling_frame(with_occ=True):
    """Occupied 07-19: SAT on setpoint. Unoccupied: the fan cycles on with SAT 8 F warm."""
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    occ = pd.Series(((idx.hour >= 7) & (idx.hour < 19)).astype(float), index=idx)
    sat = pd.Series(np.where(occ > 0, 55.0, 63.0), index=idx)
    df = _frame(sat)
    if with_occ:
        df[Role.OCCUPANCY] = occ
    return df


def test_occupancy_gate_trended_judges_occupied_hours_only():
    assert Role.OCCUPANCY in SupplyAirControl.roles_optional
    f = SupplyAirControl().analyze("AHU-1", _cycling_frame())
    assert f.severity == "ok" and f.metrics["too_warm_pct"] == 0.0
    assert f.metrics["occupancy_gate"] == "trended occupancy"
    assert f.metrics["n_running"] == 120
    assert "occupied running hours" in f.summary
    # "off" is the pre-0.98 behaviour: the unoccupied cycling counts as too warm
    off = SupplyAirControl(occupancy_gate="off").analyze("AHU-1", _cycling_frame())
    assert off.severity == "fault" and off.metrics["too_warm_pct"] == 50.0
    assert off.metrics["occupancy_gate"] == "off"


def test_occupancy_gate_trended_has_no_schedule_fallback():
    f = SupplyAirControl().analyze("AHU-1", _cycling_frame(with_occ=False))
    assert f.metrics["occupancy_gate"] == "none trended (fan-on hours only)"
    assert f.metrics["n_running"] == 240 and f.severity == "fault"
    # "schedule" falls back to the assumed weekday 07-18 window
    s = SupplyAirControl(occupancy_gate="schedule").analyze("AHU-1", _cycling_frame(False))
    assert s.metrics["occupancy_gate"] == "assumed schedule (weekdays 07-18)"
    assert s.metrics["n_running"] < 240


def test_occupancy_gate_all_null_occupancy_is_not_trended():
    df = _cycling_frame()
    df[Role.OCCUPANCY] = np.nan
    f = SupplyAirControl().analyze("AHU-1", df)
    assert f.metrics["occupancy_gate"] == "none trended (fan-on hours only)"


def test_occupancy_gate_evidence_mask_matches_the_finding():
    df = _cycling_frame()
    ev = SupplyAirControl().evidence("AHU-1", df)
    assert not ev.mask.any()  # the unoccupied warm hours are not shaded as violations
    ev_off = SupplyAirControl(occupancy_gate="off").evidence("AHU-1", df)
    assert int(ev_off.mask.sum()) == 120


def test_occupancy_gate_rejects_unknown_values():
    with pytest.raises(ValueError, match="occupancy_gate"):
        SupplyAirControl(occupancy_gate="weekdays")


def test_occupancy_gate_no_occupied_running_hours():
    df = _cycling_frame()
    df[Role.OCCUPANCY] = 0.0
    f = SupplyAirControl().analyze("AHU-1", df)
    assert f.severity == "info" and f.metrics["occupancy_gate"] == "trended occupancy"
