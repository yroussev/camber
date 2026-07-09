"""Tests for IPMVP Option A — measured-parameter savings (camber.mandv.option_a)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.option_a import (  # noqa: E402
    OptionAResult, option_a_savings, stipulated_annual_hours,
)


def test_savings_scalar_measurement():
    r = option_a_savings(100.0, 60.0, stipulated_factor=3500)
    assert isinstance(r, OptionAResult)
    assert r.measured_delta == 40.0 and r.savings == 140000.0
    assert abs(r.reduction_pct - 0.40) < 1e-9 and r.unit == "kWh"


def test_savings_series_takes_means():
    r = option_a_savings(np.array([101.0, 99.0, 100.0]), np.array([61.0, 59.0, 60.0]),
                         stipulated_factor=2600)
    assert abs(r.baseline_measured - 100.0) < 1e-9 and abs(r.reporting_measured - 60.0) < 1e-9
    assert r.savings == 104000.0


def test_basis_names_measured_and_stipulated_and_is_jsonable():
    r = option_a_savings(100.0, 60.0, stipulated_factor=3500)
    assert "measured" in r.basis and "stipulated" in r.basis and "3500" in r.basis
    assert r.as_dict()["savings"] == 140000.0


def test_stipulated_annual_hours_schedule():
    assert stipulated_annual_hours(10) == 2600.0                 # 10h × 5d × 52w
    assert stipulated_annual_hours(24, days_per_week=7) == 24 * 7 * 52


def test_negative_savings_when_use_increased():
    r = option_a_savings(60.0, 100.0, stipulated_factor=3500)    # reporting > baseline
    assert r.measured_delta < 0 and r.savings < 0


def test_zero_baseline_pct_is_nan():
    r = option_a_savings(0.0, 0.0, stipulated_factor=1000)
    assert r.savings == 0.0 and r.reduction_pct != r.reduction_pct  # NaN
