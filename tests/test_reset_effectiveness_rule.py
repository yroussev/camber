"""Tests for the ResetEffectiveness rule (G36 T&R reset actually trimming-and-responding)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.faultlab import (  # noqa: E402
    _idx,
    _sat_reset_effectiveness,
    _static_reset_effectiveness,
)
from camber.g36_reset import SAT_TR, tr_simulate  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.reset_effectiveness_rule import ResetEffectiveness  # noqa: E402


def _req_blocks(idx):
    days = ((idx - idx[0]) / pd.Timedelta("1D")).astype(int).to_numpy()
    return np.where((days // 2) % 2 == 0, 6.0, 0.0)


def test_sat_scenario_faulty_warns_healthy_ok():
    idx = _idx()
    rule = ResetEffectiveness(reset="sat")
    assert rule.analyze("AHU-1", _sat_reset_effectiveness(idx, faulty=True)).severity == "warn"
    assert rule.analyze("AHU-1", _sat_reset_effectiveness(idx, faulty=False)).severity == "ok"


def test_static_scenario_faulty_warns_healthy_ok():
    idx = _idx()
    rule = ResetEffectiveness(reset="static")
    assert rule.analyze("AHU-1", _static_reset_effectiveness(idx, faulty=True)).severity == "warn"
    assert rule.analyze("AHU-1", _static_reset_effectiveness(idx, faulty=False)).severity == "ok"


def test_faulty_reports_stuck_reason_and_metrics():
    idx = _idx()
    f = ResetEffectiveness(reset="sat").analyze("AHU-1", _sat_reset_effectiveness(idx, faulty=True))
    assert f.metrics["reason"] == "stuck"
    assert f.metrics["stuck"] is True
    assert f.metrics["effective"] is False
    assert f.metrics["reset"] == "sat"
    assert "stuck" in f.summary


def test_names_and_roles_differ_by_family():
    sat = ResetEffectiveness(reset="sat")
    static = ResetEffectiveness(reset="static")
    assert sat.name == "sat_reset_effectiveness"
    assert static.name == "static_reset_effectiveness"
    assert sat.roles_required == (Role.SUPPLY_AIR_TEMP_SP, Role.SAT_RESET_REQUESTS)
    assert static.roles_required == (Role.DUCT_STATIC_SP, Role.STATIC_PRESSURE_REQUESTS)


def test_declines_when_request_point_unmapped():
    idx = _idx()
    req = _req_blocks(idx)
    # setpoint present, request count absent -> the informative decline path
    df = pd.DataFrame({Role.SUPPLY_AIR_TEMP_SP: tr_simulate(req, SAT_TR)}, index=idx)
    f = ResetEffectiveness(reset="sat").analyze("AHU-1", df)
    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert "SAT_RESET_REQUESTS" in str(f.metrics["reason"]) or "reset_requests" in str(
        f.metrics["reason"]
    )


def test_declines_when_too_few_rows():
    idx = _idx(days=1)
    short = pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP_SP: [60.0] * 5, Role.SAT_RESET_REQUESTS: [6.0] * 5},
        index=idx[:5],
    )
    f = ResetEffectiveness(reset="sat").analyze("AHU-1", short)
    assert f.severity == "info"
    assert f.metrics["declined"] is True


def test_bad_reset_argument_raises():
    with pytest.raises(ValueError):
        ResetEffectiveness(reset="bogus")


def test_both_families_registered_as_builtins():
    names = builtin_registry().names()
    assert "sat_reset_effectiveness" in names
    assert "static_reset_effectiveness" in names
