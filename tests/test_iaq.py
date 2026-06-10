"""Tests for CO2-based ventilation-adequacy diagnostics (camber.iaq)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.iaq import analyze_co2_ventilation  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.iaq_rule import CO2Ventilation  # noqa: E402


def _idx(n=24 * 14):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # starts Monday


def _co2(ppm, outdoor=None):
    n = 24 * 14
    d = {"CO2": np.full(n, float(ppm))}
    if outdoor is not None:
        d["OutdoorCO2"] = np.full(n, float(outdoor))
    return pd.DataFrame(d, index=_idx(n))


# --- diagnostic --------------------------------------------------------------- #

def test_under_ventilated_flagged():
    r = analyze_co2_ventilation(_co2(1400), "VAV-1")     # rise ~980 ppm > 700
    assert r is not None
    assert r.under_vent_pct > 95 and r.over_vent_pct == 0.0
    assert r.co2_p95_ppm == 1400


def test_adequate_ventilation():
    r = analyze_co2_ventilation(_co2(900), "VAV-1")      # rise ~480: neither high nor low
    assert r.under_vent_pct == 0.0 and r.over_vent_pct == 0.0


def test_over_ventilated_flagged():
    r = analyze_co2_ventilation(_co2(500), "VAV-1")      # rise ~80 < 150 -> over-ventilated
    assert r.over_vent_pct > 95 and r.under_vent_pct == 0.0


def test_outdoor_co2_differential_used():
    # outdoor 600 ppm; zone 1100 -> rise 500 (not under-vent vs the higher outdoor ref)
    r = analyze_co2_ventilation(_co2(1100, outdoor=600), "VAV-1")
    assert r.outdoor_co2_ppm == 600 and r.under_vent_pct == 0.0


def test_only_occupied_hours_counted():
    n = 24 * 14
    co2 = np.full(n, 500.0)
    idx = _idx(n)
    night = (idx.hour < 7) | (idx.hour >= 18)
    co2[night] = 1800.0                                  # high CO2 only at night
    r = analyze_co2_ventilation(pd.DataFrame({"CO2": co2}, index=idx), "VAV-1")
    assert r.under_vent_pct == 0.0                       # night excluded -> not flagged


def test_insufficient_data_returns_none():
    assert analyze_co2_ventilation(pd.DataFrame({"X": [1, 2, 3]}, index=_idx(3)),
                                   "VAV-1") is None


# --- rule wrapper ------------------------------------------------------------- #

def test_rule_severity():
    assert isinstance(CO2Ventilation(), Rule)

    def frame(ppm):
        return pd.DataFrame({Role.CO2: np.full(24 * 14, float(ppm))}, index=_idx())

    assert CO2Ventilation().analyze("VAV-1", frame(1400)).severity == "fault"   # under-vent
    assert CO2Ventilation().analyze("VAV-1", frame(900)).severity == "ok"
    assert CO2Ventilation().analyze("VAV-1", frame(480)).severity == "warn"     # over-vent


def test_rule_missing_role_info():
    frame = pd.DataFrame({Role.SPACE_TEMP: np.full(24, 72.0)}, index=_idx(24))
    assert CO2Ventilation().analyze("VAV-1", frame).severity == "info"
