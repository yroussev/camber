"""Tests for the 0.5 packaged/DX/refrigerant FDD rules: compressor cycling + staging, heat-pump
defrost, filter fouling, and chiller approach-temperature fouling."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import rule_names  # noqa: E402
from camber.rules.compressor_cycle_rule import CompressorShortCycle  # noqa: E402
from camber.rules.compressor_stage_rule import CompressorStaging  # noqa: E402
from camber.rules.heatpump_rule import HeatPumpDefrost  # noqa: E402
from camber.rules.filter_rule import FilterFouling  # noqa: E402
from camber.rules.chiller_approach_rule import ChillerApproachFouling  # noqa: E402

_FINE = pd.date_range("2025-07-07", periods=24 * 12 * 3, freq="5min")   # 3 days @ 5 min
_HR = pd.date_range("2025-07-07", periods=24 * 7, freq="1h")


def _steps(vals):
    n = len(_FINE)
    return pd.Series(np.tile(vals, n // len(vals) + 1)[:n], index=_FINE)


# --- compressor short-cycle ---

def test_compressor_cycle_fault_and_clean():
    fault = pd.DataFrame({Role.COMPRESSOR_STATUS: _steps([1.0, 0.0])})
    clean = pd.DataFrame({Role.COMPRESSOR_STATUS: pd.Series(1.0, index=_FINE)})
    assert CompressorShortCycle().analyze("RTU1", fault).severity == "fault"
    assert CompressorShortCycle().analyze("RTU1", clean).severity == "ok"


def test_compressor_cycle_info_without_status():
    f = CompressorShortCycle().analyze("RTU1", pd.DataFrame({Role.OAT: pd.Series(70.0, index=_HR)}))
    assert f.severity == "info"


# --- compressor staging ---

def test_compressor_staging_fault_and_clean():
    fault = pd.DataFrame({Role.COMPRESSOR_STAGE: _steps([1.0, 2.0])})
    clean = pd.DataFrame({Role.COMPRESSOR_STAGE: pd.Series(1.0, index=_FINE)})
    assert CompressorStaging().analyze("RTU1", fault).severity == "fault"
    assert CompressorStaging().analyze("RTU1", clean).severity == "ok"


# --- heat-pump defrost ---

def test_heatpump_defrost_fault_and_clean():
    fault = pd.DataFrame({Role.REVERSING_VALVE_CMD: _steps([1.0, 0.0])})
    n = len(_FINE)
    clean = pd.DataFrame({Role.REVERSING_VALVE_CMD:
                          pd.Series(np.concatenate([np.ones(n // 2), np.zeros(n - n // 2)]), index=_FINE)})
    assert HeatPumpDefrost().analyze("HP1", fault).severity == "fault"
    assert HeatPumpDefrost().analyze("HP1", clean).severity == "ok"


# --- filter fouling ---

def test_filter_fouling_fault_warn_clean():
    hi = pd.DataFrame({Role.FILTER_DIFF_PRESS: pd.Series(1.8, index=_HR)})
    mid = pd.DataFrame({Role.FILTER_DIFF_PRESS: pd.Series(1.1, index=_HR)})
    lo = pd.DataFrame({Role.FILTER_DIFF_PRESS: pd.Series(0.4, index=_HR)})
    assert FilterFouling().analyze("AHU1", hi).severity == "fault"
    assert FilterFouling().analyze("AHU1", mid).severity == "warn"
    assert FilterFouling().analyze("AHU1", lo).severity == "ok"


# --- chiller approach fouling ---

def test_chiller_approach_fault_and_clean():
    fault = pd.DataFrame({Role.COND_APPROACH_TEMP: pd.Series(12.0, index=_HR),
                          Role.EVAP_APPROACH_TEMP: pd.Series(9.0, index=_HR)})
    clean = pd.DataFrame({Role.COND_APPROACH_TEMP: pd.Series(4.0, index=_HR),
                          Role.EVAP_APPROACH_TEMP: pd.Series(3.0, index=_HR)})
    ff = ChillerApproachFouling().analyze("CH1", fault)
    assert ff.severity == "fault" and ff.metrics["worst_approach_ratio"] >= 2.0
    assert ChillerApproachFouling().analyze("CH1", clean).severity == "ok"


def test_chiller_approach_info_without_inputs():
    f = ChillerApproachFouling().analyze("CH1", pd.DataFrame({Role.POWER: pd.Series(100.0, index=_HR)}))
    assert f.severity == "info"


# --- registration ---

def test_new_rules_registered():
    names = rule_names()
    for n in ("compressor_short_cycle", "compressor_staging", "heatpump_defrost",
              "filter_fouling", "chiller_approach_fouling"):
        assert n in names
