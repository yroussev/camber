"""Tests for G36 trim-and-respond reset + request generation (clean-room)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.g36_reset import (  # noqa: E402
    SAT_TR, STATIC_TR, TRParams, cooling_sat_requests, oat_sat_setpoint,
    sat_reset_compliance, static_pressure_requests, tr_simulate, tr_step,
)


# ---- trim & respond ----

def test_tr_trims_when_no_requests():
    # SAT T&R: no requests -> trim UP (+0.2F) toward energy-saving (warmer SAT)
    sp = tr_step(60.0, requests=0, p=SAT_TR)
    assert abs(sp - 60.2) < 1e-9


def test_tr_responds_to_requests():
    # 5 requests, I=2 -> eff 3 -> 3*(-0.3) = -0.9F, capped at -1.0 -> 60 - 0.9
    sp = tr_step(60.0, requests=5, p=SAT_TR)
    assert abs(sp - 59.1) < 1e-9


def test_tr_response_capped():
    # many requests -> response magnitude capped at sp_res_max (-1.0F)
    sp = tr_step(60.0, requests=50, p=SAT_TR)
    assert abs(sp - 59.0) < 1e-9


def test_tr_clamped_to_range():
    assert tr_step(SAT_TR.sp_max, requests=0, p=SAT_TR) == SAT_TR.sp_max   # can't trim above max
    assert tr_step(SAT_TR.sp_min, requests=99, p=SAT_TR) == SAT_TR.sp_min  # can't respond below min


def test_static_tr_direction():
    # static T&R: trim DOWN (-0.05), respond UP (+0.06) -- opposite signs of SAT
    assert tr_step(0.5, requests=0, p=STATIC_TR) < 0.5     # trims down
    assert tr_step(0.5, requests=10, p=STATIC_TR) > 0.5    # responds up


def test_tr_simulate_converges_up_with_no_requests():
    sp = tr_simulate([0] * 100, SAT_TR)
    assert sp[-1] == SAT_TR.sp_max            # trims to max when never any requests


# ---- OAT-based SAT reset map ----

def test_oat_sat_map_endpoints():
    # at/above OAT_max -> Min_ClgSAT; at/below OAT_min -> t_max
    assert oat_sat_setpoint(80, min_clg_sat=55, t_max=65, oat_min=60, oat_max=70) == 55
    assert oat_sat_setpoint(50, min_clg_sat=55, t_max=65, oat_min=60, oat_max=70) == 65
    mid = oat_sat_setpoint(65, min_clg_sat=55, t_max=65, oat_min=60, oat_max=70)
    assert 59 < mid < 61                      # midpoint ~60F


# ---- request generation ----

def test_cooling_sat_requests_tiers():
    assert cooling_sat_requests(zone_temp=80, cool_sp=74) == 3   # +6F -> 3
    assert cooling_sat_requests(zone_temp=77.5, cool_sp=74) == 2  # +3.5F -> 2
    assert cooling_sat_requests(zone_temp=74, cool_sp=74, cooling_loop=98) == 1
    assert cooling_sat_requests(zone_temp=72, cool_sp=74) == 0


def test_static_pressure_requests_tiers():
    assert static_pressure_requests(airflow=40, airflow_sp=100, damper=98) == 3
    assert static_pressure_requests(airflow=60, airflow_sp=100, damper=98) == 2
    assert static_pressure_requests(airflow=90, airflow_sp=100, damper=98) == 1
    assert static_pressure_requests(airflow=90, airflow_sp=100, damper=50) == 0


# ---- SAT reset compliance vs actual ----

def test_sat_reset_compliance_flags_overcooling():
    # actual SAT pinned at 54F while G36 target rises in mild weather -> flagged
    idx = pd.date_range("2025-01-01", periods=200, freq="1h")
    rng = np.random.default_rng(0)
    oat = rng.uniform(55, 75, 200)
    df = pd.DataFrame({"SAT": np.full(200, 54.0), "OAT": oat}, index=idx)
    r = sat_reset_compliance(df, "AHU_1", min_clg_sat=55, t_max=65,
                             oat_min=60, oat_max=70)
    assert r.pct_below_g36_target > 40        # often colder than the reset target
    assert r.mean_gap_f > 0                    # actual colder than target on average


def test_sat_reset_compliance_ok_when_following():
    idx = pd.date_range("2025-01-01", periods=200, freq="1h")
    oat = np.linspace(55, 75, 200)
    target = oat_sat_setpoint(oat, min_clg_sat=55, t_max=65, oat_min=60, oat_max=70)
    df = pd.DataFrame({"SAT": target, "OAT": oat}, index=idx)
    r = sat_reset_compliance(df, "AHU_2", min_clg_sat=55, t_max=65,
                             oat_min=60, oat_max=70)
    assert r.pct_below_g36_target < 5         # SAT follows the target
