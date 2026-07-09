"""Tests for the 0.3 economizer-lockout / static-reset / free-cooling-missed rules."""

import os
import sys

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.model.roles import Role  # noqa: E402
from camber.rules.economizer_lockout_rule import EconomizerHighLimit  # noqa: E402
from camber.rules.staticreset_rule import StaticPressureReset  # noqa: E402
from camber.rules.freecoolingmissed_rule import FreeCoolingMissed  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.charts.evidence import finding_evidence  # noqa: E402
from camber.aso import recommend  # noqa: E402


def _oat(n=200, seed=0):
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    return pd.Series(np.random.default_rng(seed).uniform(40, 95, n), index=idx), idx


def test_economizer_lockout_fault_vs_ok():
    oat, idx = _oat()
    bad = pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: pd.Series(np.where(oat > 65, 0.7, 0.2), index=idx)})
    good = pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: pd.Series(np.where(oat > 65, 0.15, 0.6), index=idx)})
    assert EconomizerHighLimit().analyze("AHU-1", bad).severity == "fault"
    assert EconomizerHighLimit().analyze("AHU-1", good).severity == "ok"


def test_economizer_info_when_never_hot():
    idx = pd.date_range("2024-01-01", periods=100, freq="1h")
    cold = pd.DataFrame({Role.OAT: pd.Series(40.0, index=idx), Role.OA_DAMPER: pd.Series(0.5, index=idx)})
    assert EconomizerHighLimit().analyze("AHU-1", cold).severity == "info"


def test_static_reset_flat_vs_resetting():
    idx = pd.date_range("2024-07-01", periods=200, freq="1h")
    flat = pd.DataFrame({Role.DUCT_STATIC_SP: pd.Series(1.5, index=idx)})
    resets = pd.DataFrame({Role.DUCT_STATIC_SP: pd.Series(np.linspace(0.8, 1.6, 200), index=idx)})
    assert StaticPressureReset().analyze("AHU-1", flat).severity == "warn"
    assert StaticPressureReset().analyze("AHU-1", resets).severity == "ok"


def test_free_cooling_missed_fault():
    oat, idx = _oat()
    cold = pd.DataFrame({Role.OAT: oat, Role.COOL_VALVE: pd.Series(np.where(oat < 60, 0.5, 0.3), index=idx)})
    f = FreeCoolingMissed().analyze("AHU-1", cold)
    assert f.severity == "fault" and f.metrics["missed_pct"] > 50


def test_evidence_recommendations_and_registration():
    oat, idx = _oat()
    bad = pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: pd.Series(np.where(oat > 65, 0.7, 0.2), index=idx)})
    assert finding_evidence(EconomizerHighLimit(), "AHU-1", bad).renderer == "diagnostic"
    cold = pd.DataFrame({Role.OAT: oat, Role.COOL_VALVE: pd.Series(np.where(oat < 60, 0.5, 0.3), index=idx)})
    assert finding_evidence(FreeCoolingMissed(), "AHU-1", cold).renderer == "oat_scatter"
    assert recommend(FreeCoolingMissed().analyze("AHU-1", cold)) is not None
    names = set(rule_names())
    assert {"economizer_high_limit", "static_pressure_reset", "free_cooling_missed"} <= names
    reg = builtin_registry()
    assert reg.get("economizer_high_limit").name == "economizer_high_limit"
