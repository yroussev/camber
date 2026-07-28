"""Tests for the variable-base degree-day M&V baseline (camber.mandv.degreeday)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.degreeday import DegreeDayModel, degree_days, fit_degree_day  # noqa: E402


def _cooling_data(seed=0, slope=3.0, base=100.0, bp=60.0, n=48):
    rng = np.random.default_rng(seed)
    tavg = rng.uniform(35, 90, n)
    _, cdd = degree_days(tavg, bp)
    energy = base + slope * cdd + rng.normal(0, 3, n)
    return tavg, energy


def test_degree_days_helper():
    hdd, cdd = degree_days([40.0, 60.0, 80.0], balance_point=60.0)
    assert list(hdd) == [20.0, 0.0, 0.0]  # heating below the balance point
    assert list(cdd) == [0.0, 0.0, 20.0]  # cooling above it


def test_recovers_slope_balance_and_base():
    tavg, energy = _cooling_data(slope=3.0, base=100.0, bp=60.0)
    m = fit_degree_day(tavg, energy)
    assert isinstance(m, DegreeDayModel)
    assert abs(m.cooling_slope - 3.0) < 0.3
    assert abs(m.balance_point - 60.0) <= 3.0  # balance point searched by min CV(RMSE)
    assert abs(m.base - 100.0) < 8.0
    assert abs(m.heating_slope) < 0.5  # no heating dependence in the data
    assert m.fit.accept  # meets G14 thresholds


def test_predict_low_error():
    tavg, energy = _cooling_data()
    m = fit_degree_day(tavg, energy)
    mae = float(np.mean(np.abs(m.predict(tavg) - energy)))
    assert mae < 5.0  # ~ the injected noise level


def test_kind_cooling_only_zeroes_heating():
    tavg, energy = _cooling_data()
    m = fit_degree_day(tavg, energy, kind="cooling")
    assert m.heating_slope == 0.0 and m.cooling_slope > 2.5
    assert m.as_dict()["kind"] == "cooling" and "fit" in m.as_dict()


def test_fixed_balance_point_used_verbatim():
    tavg, energy = _cooling_data(bp=60.0)
    m = fit_degree_day(tavg, energy, balance_point=55.0)
    assert m.balance_point == 55.0  # not searched


def test_invalid_inputs():
    tavg, energy = _cooling_data()
    with pytest.raises(ValueError):
        fit_degree_day(tavg, energy, kind="bogus")
    with pytest.raises(ValueError):
        fit_degree_day([50.0, 60.0], [100.0, 110.0])  # < 3 points
