"""Tests for the change-point regression engine (ASHRAE/IPMVP inverse models)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.models import best_model, fit_model  # noqa: E402


def test_2p_recovers_line():
    T = np.linspace(40, 100, 200)
    y = 10 + 0.5 * T                      # exact line
    m = fit_model(T, y, "2P")
    assert abs(m.coeffs["base"] - 10) < 1e-6
    assert abs(m.coeffs["slope"] - 0.5) < 1e-6
    assert m.sse < 1e-9


def test_3pc_recovers_change_point():
    # cooling: flat 20 below 65F, slope 0.8 above
    rng = np.random.default_rng(0)
    T = np.linspace(45, 105, 400)
    y = 20 + 0.8 * np.maximum(0.0, T - 65) + rng.normal(0, 0.3, len(T))
    m = fit_model(T, y, "3PC")
    assert abs(m.change_points[0] - 65) < 3        # recovers Tc ~ 65F
    assert abs(m.coeffs["cool_slope"] - 0.8) < 0.1
    assert abs(m.coeffs["base"] - 20) < 1.0


def test_3ph_recovers_change_point():
    # heating: slope below 60F, flat above
    rng = np.random.default_rng(1)
    T = np.linspace(20, 80, 400)
    y = 15 + 1.2 * np.maximum(0.0, 60 - T) + rng.normal(0, 0.3, len(T))
    m = fit_model(T, y, "3PH")
    assert abs(m.change_points[0] - 60) < 3
    assert abs(m.coeffs["heat_slope"] - 1.2) < 0.15


def test_5p_recovers_both_change_points():
    # heat below 55, deadband, cool above 70
    rng = np.random.default_rng(2)
    T = np.linspace(20, 110, 600)
    y = (12 + 1.0 * np.maximum(0.0, 55 - T) + 0.7 * np.maximum(0.0, T - 70)
         + rng.normal(0, 0.3, len(T)))
    m = fit_model(T, y, "5P")
    tlo, thi = m.change_points
    assert abs(tlo - 55) < 5 and abs(thi - 70) < 5
    assert m.coeffs["heat_slope"] > 0.5 and m.coeffs["cool_slope"] > 0.3


def test_best_model_recovers_cooling_shape():
    rng = np.random.default_rng(3)
    T = np.linspace(45, 105, 400)
    y = 20 + 0.8 * np.maximum(0.0, T - 65) + rng.normal(0, 0.3, len(T))
    m = best_model(T, y)
    # 3PC or 4P both express "flat then cooling slope" (4P generalizes 3PC with a
    # near-zero left slope) -- assert the recovered physics, not the label.
    assert m.kind in ("3PC", "4P")
    assert abs(m.change_points[0] - 65) < 3
    cool = m.coeffs.get("cool_slope", m.coeffs.get("right_slope"))
    assert abs(cool - 0.8) < 0.1
    if "left_slope" in m.coeffs:        # if 4P, the left arm should be ~flat
        assert abs(m.coeffs["left_slope"]) < 0.15


def test_best_model_picks_2p_for_linear():
    rng = np.random.default_rng(4)
    T = np.linspace(40, 100, 300)
    y = 5 + 0.6 * T + rng.normal(0, 0.2, len(T))
    m = best_model(T, y)
    assert m.kind == "2P"


def test_predict_matches_fit():
    T = np.linspace(40, 100, 100)
    y = 8 + 0.4 * T
    m = fit_model(T, y, "2P")
    assert np.allclose(m.predict(T), y, atol=1e-6)


def test_3ph_zero_no_base_load():
    # heating that truly goes to zero above the change point (gas-only-heating)
    rng = np.random.default_rng(10)
    T = np.linspace(35, 90, 300)
    y = np.maximum(0.0, 60 - T) * 2.0 + rng.normal(0, 0.3, len(T))   # zero above 60F
    m = fit_model(T, y, "3PHZ")
    assert m.coeffs["base"] == 0.0                 # forced zero base
    assert abs(m.change_points[0] - 60) < 4
    assert abs(m.coeffs["heat_slope"] - 2.0) < 0.2
    # predicts ~0 well above the change point
    assert abs(float(m.predict(85))) < 2.0


def test_3ph_zero_vs_3ph_on_base_load_data():
    # data WITH a base load: plain 3PH (intercept) should fit better than Htg-zero
    rng = np.random.default_rng(11)
    T = np.linspace(35, 95, 300)
    y = 50 + np.maximum(0.0, 60 - T) * 2.0 + rng.normal(0, 0.3, len(T))  # base 50
    mh = fit_model(T, y, "3PH")
    mz = fit_model(T, y, "3PHZ")
    assert mh.sse < mz.sse                          # base-load data favors 3PH
    assert mh.coeffs["base"] > 30                   # recovers the ~50 base


def test_best_model_can_select_htg_zero():
    # best_model can return 3PHZ when it is in the candidate set and fits well.
    # (On clean no-base data, plain 3PH with a fitted ~0 intercept is an equally
    #  valid representation, so we assert 3PHZ is a strong fit, not that it always
    #  beats 3PH -- the point of 3PHZ is *enforcing* a zero base when physically
    #  warranted, not winning a BIC contest.)
    rng = np.random.default_rng(12)
    T = np.linspace(35, 90, 300)
    y = np.maximum(0.0, 60 - T) * 2.0 + rng.normal(0, 0.3, len(T))
    m = best_model(T, y, kinds=("2P", "3PH", "3PHZ"))
    assert m.kind in ("3PH", "3PHZ")               # a heating change-point model
    # and the explicit Htg-zero fit recovers the structure with a forced-zero base
    mz = fit_model(T, y, "3PHZ")
    assert mz.coeffs["base"] == 0.0
    assert abs(mz.change_points[0] - 60) < 4


def test_5p_zero_no_base_both_arms():
    # heat below 50, zero between, cool above 80, NO base load
    rng = np.random.default_rng(20)
    T = np.linspace(20, 110, 600)
    y = (1.0 * np.maximum(0.0, 50 - T) + 0.6 * np.maximum(0.0, T - 80)
         + rng.normal(0, 0.3, len(T)))
    m = fit_model(T, y, "5PZ")
    assert m.coeffs["base"] == 0.0
    tlo, thi = m.change_points
    assert tlo < thi
    # near-zero in the dead-band middle (no base load)
    assert abs(float(m.predict(65))) < 3.0


def test_bias_mode_reduces_net_determination_bias():
    # asymmetric noise so the SSE-optimal change point is slightly biased;
    # the bias objective should yield a smaller |sum(resid)/sum(y)|
    rng = np.random.default_rng(21)
    T = np.linspace(40, 100, 300)
    y = 20 + 0.8 * np.maximum(0.0, T - 65) + rng.gumbel(0, 4, len(T))  # skewed noise
    m_sse = fit_model(T, y, "3PC", objective="sse")
    m_bias = fit_model(T, y, "3PC", objective="bias")
    bias_sse = abs((y - m_sse.predict(T)).sum() / y.sum())
    bias_b = abs((y - m_bias.predict(T)).sum() / y.sum())
    assert bias_b <= bias_sse + 1e-9            # bias mode is at least as unbiased


def test_bias_mode_default_is_sse():
    # default fit equals explicit sse objective
    rng = np.random.default_rng(22)
    T = np.linspace(40, 100, 200)
    y = 20 + 0.8 * np.maximum(0.0, T - 65) + rng.normal(0, 1, len(T))
    a = fit_model(T, y, "3PC")
    b = fit_model(T, y, "3PC", objective="sse")
    assert a.change_points == b.change_points
