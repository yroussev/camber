"""Change-point + driver multivariable baselines (#21 phase 21c; camber.mandv.multivariable).

A continuously varying driver (here occupancy) is a relevant variable of the model, not a static
factor: the model recovers its coefficient alongside the change point, serialises, grades coverage
per column and by leverage, and works with both savings kernels unchanged.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv._design import design_names, projection_variance  # noqa: E402
from camber.mandv.coverage import assess_coverage  # noqa: E402
from camber.mandv.models import best_model  # noqa: E402
from camber.mandv.multivariable import ChangePointDriverModel, fit_cp_driver_model  # noqa: E402
from camber.mandv.stats import (  # noqa: E402
    avoided_energy_savings,
    logical_signs,
    model_regression_tests,
    sep_validity,
)


def _site(seed, days=365, occ_shift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=days, freq="D")
    d = np.arange(days)
    T = 60 + 20 * np.sin(2 * np.pi * (d - 100) / 365) + rng.normal(0, 4, days)
    # occupancy co-varies with season (a school-like calendar) plus day-to-day noise
    occ = 300 + 150 * np.cos(2 * np.pi * (d - 20) / 365) + rng.normal(0, 40, days) + occ_shift
    y = 200 + 5 * np.maximum(0, T - 66) + 0.8 * occ + rng.normal(0, 6, days)
    return idx, T, occ, y


def test_recovers_the_change_point_and_the_driver():
    idx, T, occ, y = _site(1)
    m = fit_cp_driver_model(T, occ, y, driver_names=("occupancy",), time_index=idx)
    assert m.kind == "3PC" and m.change_points[0] == pytest.approx(66, abs=1.5)
    assert m.coeffs["cool_slope"] == pytest.approx(5, abs=0.4)
    assert m.driver_coef[0] == pytest.approx(0.8, abs=0.05) and m.p == 3 + 1
    X = np.column_stack([T, occ])
    assert np.sqrt(np.mean((m.predict(X) - y) ** 2)) == pytest.approx(6, rel=0.15)
    # a weather-only model absorbs part of the seasonal occupancy into the wrong shape
    w = best_model(T, y)
    assert w.sse > 2 * m.sse
    assert m._fit_record.rho is not None and m._fit_record.p == 4
    assert design_names(m) == ("base", "cool_slope", "occupancy")
    rt = model_regression_tests(m, X, y)
    assert rt.n_params == 4 and rt.conditional_on_change_points  # 3 coefficients + change point
    assert rt.p[2] < 1e-6
    assert logical_signs(m) == {"cool_slope": 1}
    assert sep_validity(rt, signs=logical_signs(m)).sep_valid


def test_serialises_and_rebuilds_identically():
    idx, T, occ, y = _site(2)
    m = fit_cp_driver_model(T, np.column_stack([occ, occ**0.5]), y, time_index=idx)
    d = json.loads(json.dumps(m.as_dict(), allow_nan=False))
    r = ChangePointDriverModel.from_dict(d)
    X = np.column_stack([T, occ, occ**0.5])
    np.testing.assert_array_equal(r.predict(X), m.predict(X))
    assert r.driver_names == ("driver[0]", "driver[1]") and r.p == m.p
    pv, pr = projection_variance(m, X), projection_variance(r, X)
    assert pv.v_param == pytest.approx(pr.v_param) and pv.total == pytest.approx(pr.total)


def test_savings_coverage_and_the_exact_kernel_work_unchanged():
    idx, T, occ, y = _site(3)
    m = fit_cp_driver_model(T, occ, y, time_index=idx)
    _, Tr, occ_r, yr = _site(4)
    yr = 0.9 * yr
    Xr = np.column_stack([Tr, occ_r])
    true = 0.1 * (yr / 0.9).sum()
    for kernel in ("g14", "exact"):
        s = avoided_energy_savings(m, Xr, yr, cv_rmse=0.02, n_baseline=365, p_baseline=4,
                                   kernel=kernel)  # fmt: skip
        assert s.coverage["tier"] == "in_range" and not s.declined
        assert abs(s.avoided_energy - true) <= 2.5 * s.abs_uncertainty
    # a reporting period whose occupancy leaves the baseline's range is graded, per column
    _, Tr2, occ2, yr2 = _site(4, occ_shift=600)
    cov = assess_coverage(m, np.column_stack([Tr2, occ2]))
    assert cov.tier == "severe"
    assert {v["name"] for v in cov.variables} == {"oat", "driver"}


def test_input_validation():
    idx, T, occ, y = _site(5, days=40)
    with pytest.raises(ValueError, match="same length"):
        fit_cp_driver_model(T, occ[:-1], y)
    with pytest.raises(ValueError, match="driver names"):
        fit_cp_driver_model(T, occ, y, driver_names=("a", "b"))
    with pytest.raises(ValueError, match="unknown change-point kind"):
        fit_cp_driver_model(T, occ, y, kinds=("7P",))
    with pytest.raises(ValueError, match="too few rows"):
        fit_cp_driver_model(T[:3], occ[:3], y[:3], kinds=("5P",))
    m = fit_cp_driver_model(T, occ, y, kinds=("2P",))
    assert m.kind == "2P" and m.change_points == ()
    with pytest.raises(ValueError, match="columns"):
        m.predict(np.column_stack([T, occ, occ]))
    assert m.predict(np.array([70.0, 300.0])).shape == (1,)
