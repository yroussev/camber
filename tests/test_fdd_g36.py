"""Tests for the G36 §5.16.14 AHU fault-detection engine (clean-room)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fdd_g36 import (  # noqa: E402
    OS_FAULTS, OS_FREECOOL, OS_HEATING, OS_MECH_ECON, OS_MECH_MINOA, OS_UNKNOWN,
    G36Thresholds, classify_os, run_g36_afdd,
)


# ---- operating-state classifier ----

def test_os_heating():
    assert classify_os(hc=40, cc=0) == OS_HEATING


def test_os_simultaneous_is_unknown():
    # both valves open -> OS#5 (the simultaneous heat/cool signature)
    assert classify_os(hc=30, cc=50) == OS_UNKNOWN


def test_os_free_cooling():
    assert classify_os(hc=0, cc=0) == OS_FREECOOL


def test_os_mech_econ_vs_minoa():
    assert classify_os(hc=0, cc=60, oa_damper=100) == OS_MECH_ECON
    assert classify_os(hc=0, cc=60, oa_damper=10) == OS_MECH_MINOA


def test_os_fault_map_matches_g36():
    # spot-check the OS->FC applicability (G36 5.16.14.9)
    assert set(OS_FAULTS[OS_HEATING]) == {1, 2, 3, 4, 5, 6, 7, 14}
    assert 8 in OS_FAULTS[OS_FREECOOL] and 9 in OS_FAULTS[OS_FREECOOL]
    assert 13 in OS_FAULTS[OS_MECH_ECON]
    assert 4 in OS_FAULTS[OS_UNKNOWN]      # instability checked in every state


# ---- fault conditions on synthetic AHUs ----

def _frame(n=200, **cols):
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    return pd.DataFrame({c: np.full(n, v) for c, v in cols.items()}, index=idx)


def test_fc7_sat_too_low_in_full_heating():
    # heating valve full open but SAT well below setpoint -> FC7 fault
    df = _frame(HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60)
    r = run_g36_afdd(df, "AHU_1")
    assert r.fault_pct[7] > 95               # FC7 trips
    assert r.os_distribution[OS_HEATING] == len(df)


def test_fc13_sat_too_high_in_full_cooling():
    # cooling valve full open, economizer damper open, SAT above setpoint -> FC13
    df = _frame(HC=0, CC=100, SAT=70, SATSP=55, MAT=78, RAT=74, OAT=72, OA_Damper=100)
    r = run_g36_afdd(df, "AHU_2")
    assert r.os_distribution[OS_MECH_ECON] == len(df)
    assert r.fault_pct[13] > 95


def test_fc15_heating_coil_leak():
    # OS free-cooling (both valves shut) but air RISES across the heating coil
    # (HCLT >> HCET) -> leaking/stuck heating valve, FC15
    df = _frame(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68,
                HCET=70, HCLT=80)
    r = run_g36_afdd(df, "AHU_3")
    assert r.os_distribution[OS_FREECOOL] == len(df)
    assert r.fault_pct[15] > 95


def test_no_fault_when_healthy():
    # healthy full-heating AHU: SAT meets setpoint, MAT between OAT/RAT
    df = _frame(HC=100, CC=0, SAT=95, SATSP=95, MAT=68, RAT=72, OAT=55,
                CCET=68, CCLT=68)
    r = run_g36_afdd(df, "AHU_4")
    assert r.fault_pct[7] == 0.0             # SAT meets setpoint -> no FC7
    assert (r.fault_pct[5] or 0) == 0.0      # SAT above MAT -> no FC5


def test_fault_only_evaluated_in_applicable_os():
    # in OS#1 (heating), cooling-side faults like FC13 are not applicable -> None
    df = _frame(HC=100, CC=0, SAT=95, SATSP=95, MAT=68, RAT=72, OAT=55)
    r = run_g36_afdd(df, "AHU_5")
    assert r.fault_pct[13] is None           # FC13 not evaluated in OS#1
    assert r.fault_n_applicable[13] == 0


# ---- opt-in single-signal comparability mode ----

def test_comparability_off_by_default():
    # default run: no single-signal output, and as_dict is unchanged (no extra keys)
    df = _frame(HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60)
    r = run_g36_afdd(df, "AHU_1")
    assert r.fault_pct_singlesignal is None
    assert not any(k.endswith("_singlesignal") for k in r.as_dict())


def test_comparability_same_fires_different_denominator():
    # FC10 (OAT/MAT mismatch) is applicable only in OS#3 (mechanical + economizer).
    # Build cooling rows split between economizer (OS#3) and min-OA (OS#4); make the
    # MAT/OAT mismatch occur ONLY in the economizer rows. The fault FIRES in the same
    # rows under both denominators, but:
    #   - operating-state gating scores it over OS#3 rows only            -> 100%
    #   - single-signal gating scores it over all input-valid rows        ->  50%
    import numpy as np
    import pandas as pd
    n = 100
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    econ = np.arange(n) < 50
    df = pd.DataFrame({
        "HC": np.zeros(n),
        "CC": np.full(n, 60.0),                      # cooling on
        "OA_Damper": np.where(econ, 100.0, 10.0),    # econ -> OS#3, else OS#4
        "MAT": np.where(econ, 80.0, 60.0),           # mismatch only in econ rows
        "OAT": np.full(n, 60.0),
    }, index=idx)

    r = run_g36_afdd(df, "AHU", comparability=True)
    # OS-gated: denominator = 50 econ rows, all fire -> 100%
    assert r.fault_pct[10] == 100.0
    assert r.fault_n_applicable[10] == 50
    # single-signal: denominator = all 100 input-valid rows, 50 fire -> 50%
    assert r.fault_pct_singlesignal[10] == 50.0
    # narrower (operating-state) denominator => >= single-signal magnitude
    assert r.fault_pct[10] >= r.fault_pct_singlesignal[10]
    # as_dict surfaces the comparability keys only in this mode
    assert r.as_dict()["FC10_pct_singlesignal"] == 50.0


def test_comparability_unrunnable_fc_singlesignal_is_none():
    # FC1 needs DSP/DSPSP/FS, absent here. Under OS gating FC1 is *applicable*
    # (it lists in every operating state) but cannot fire without inputs -> 0.0%.
    # Under single-signal gating there are zero input-valid rows -> None (truly
    # unrunnable). The two denominators legitimately disagree on "applicable".
    df = _frame(HC=0, CC=60, MAT=80, OAT=60, OA_Damper=100)
    r = run_g36_afdd(df, "AHU", comparability=True)
    assert r.fault_pct[1] == 0.0
    assert r.fault_pct_singlesignal[1] is None


def test_comparability_does_not_change_default_fault_pct():
    df = _frame(HC=0, CC=60, MAT=80, OAT=60, OA_Damper=100, SAT=70)
    base = run_g36_afdd(df, "AHU")
    comp = run_g36_afdd(df, "AHU", comparability=True)
    assert comp.fault_pct == base.fault_pct          # default output identical
    assert comp.fault_n_applicable == base.fault_n_applicable
