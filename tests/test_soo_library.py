"""Tests for the packaged ASHRAE G36 SOO clause library (camber.soo_library)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.soo import Clause, evaluate_soo  # noqa: E402
from camber.soo_library import g36_ahu_sequence, g36_plant_sequence  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _conforming_ahu(n=240):
    """An AHU frame that conforms to every assessable G36 clause."""
    fan = np.ones(n)
    fan[200:] = 0.0                       # last 40 hours: fan off
    econ = np.where(np.arange(n) < 120, 0.0, 1.0)  # econ off while OAT>75 (first 120)
    oat = np.where(np.arange(n) < 120, 80.0, 60.0)
    dmpr = np.full(n, 40.0)
    dmpr[200:] = 0.0                      # damper closed when fan off
    return pd.DataFrame({
        Role.SUPPLY_FAN_STATUS: fan,
        Role.SUPPLY_AIR_TEMP: np.full(n, 55.0),
        Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.0),
        Role.DUCT_STATIC: np.full(n, 1.0),
        Role.DUCT_STATIC_SP: np.full(n, 1.0),
        Role.COOL_VALVE: np.full(n, 30.0),   # cooling active
        Role.HEAT_VALVE: np.zeros(n),        # heating closed -> no simultaneous H/C
        Role.OAT: oat,
        Role.ECON_CMD: econ,
        Role.OA_DAMPER: dmpr,
    }, index=_idx(n))


def test_sequence_shapes():
    ahu = g36_ahu_sequence()
    assert all(isinstance(c, Clause) for c in ahu)
    assert {c.name for c in ahu} >= {"sat_tracks_setpoint", "no_simultaneous_heat_cool",
                                     "economizer_high_limit_lockout"}
    assert g36_plant_sequence()[0].name == "boiler_summer_lockout"


def test_conforming_ahu_all_ok():
    rep = evaluate_soo(_conforming_ahu(), g36_ahu_sequence(), "AHU-1")
    assert all(c.severity in ("ok", "info") for c in rep.clauses)
    assert rep.severity in ("ok", "info")


def test_simultaneous_heat_cool_flagged():
    f = _conforming_ahu()
    f[Role.HEAT_VALVE] = 30.0             # heating open while cooling open -> violation
    rep = evaluate_soo(f, g36_ahu_sequence(), "AHU-1")
    simul = next(c for c in rep.clauses if c.name == "no_simultaneous_heat_cool")
    assert simul.severity == "fault"
    assert rep.severity == "fault"


def test_economizer_high_limit_violation():
    f = _conforming_ahu()
    f[Role.ECON_CMD] = 1.0               # economizer on even when OAT>75
    rep = evaluate_soo(f, g36_ahu_sequence(), "AHU-1")
    econ = next(c for c in rep.clauses if c.name == "economizer_high_limit_lockout")
    assert econ.severity == "fault"


def test_plant_summer_lockout_violation():
    n = 200
    oat = np.array([80.0] * 120 + [50.0] * 80)
    boiler = np.array([1.0] * 120 + [0.0] * 80)   # boiler firing in hot weather
    f = pd.DataFrame({Role.OAT: oat, Role.BOILER_STATUS: boiler}, index=_idx(n))
    rep = evaluate_soo(f, g36_plant_sequence(), "HWP")
    assert rep.clauses[0].severity == "fault"
