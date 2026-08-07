"""Tests for the load-normalized chiller approach baseline (camber.chillerbaseline)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import (  # noqa: E402
    ApproachBaseline,
    drift_stats,
    fit_approach_baseline,
    tons_from_flow,
)

# A plausible water-cooled machine: ~2 degF approach unloaded, widening to ~5 degF at 300 tons.
_INTERCEPT_F = 2.0
_SLOPE_F_PER_TON = 0.01
_SIGMA_F = 0.25


def _load_profile(n, seed=0):
    """A realistic duty cycle: diurnal load swing between roughly 40 and 300 tons."""
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    tons = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(tons, 40.0, 320.0)


def _chiller(n=24 * 30, *, start="2025-05-01", seed=0, extra_f=0.0, ramp_f=0.0):
    """Hourly approach/tons frame for one chiller.

    ``extra_f`` is a constant offset added to approach; ``ramp_f`` is a linear widening applied
    across the window (0 at the start, ``ramp_f`` at the end) -- i.e. the fouling signal.
    """
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _load_profile(n, seed=seed)
    ramp = ramp_f * np.linspace(0.0, 1.0, n)
    approach = _INTERCEPT_F + _SLOPE_F_PER_TON * tons + extra_f + ramp + rng.normal(0, _SIGMA_F, n)
    return pd.DataFrame({"tons": tons, "approach_f": approach}, index=idx)


# --------------------------------------------------------------------------- fit


def test_fit_recovers_known_coefficients_and_sigma():
    b = fit_approach_baseline(_chiller())
    assert b is not None
    assert abs(b.slope_f_per_ton - _SLOPE_F_PER_TON) < 0.001
    assert abs(b.intercept_f - _INTERCEPT_F) < 0.15
    assert abs(b.sigma_f - _SIGMA_F) < 0.05
    assert b.r2 > 0.9
    assert b.n == 24 * 30
    assert b.tons_min >= 40.0 and b.tons_max <= 320.0
    assert b.coverage_start.startswith("2025-05-01")


def test_predict_residual_and_z_scale_as_expected():
    b = fit_approach_baseline(_chiller())
    expected = b.predict(200.0)
    assert isinstance(expected, float)
    assert abs(expected - (_INTERCEPT_F + _SLOPE_F_PER_TON * 200.0)) < 0.2
    # a reading 3 degF above the line is a +3 degF residual and ~3/sigma sigmas
    assert abs(b.residual(200.0, expected + 3.0) - 3.0) < 1e-9
    assert abs(b.z(200.0, expected + 3.0) - 3.0 / b.sigma_f) < 1e-6
    # array in -> array out, same length
    tons = np.array([100.0, 200.0, 300.0])
    assert np.asarray(b.predict(tons)).shape == (3,)
    assert b.covers(200.0) and not b.covers(5000.0)


def test_fit_declines_rather_than_fabricating():
    # too few samples
    assert fit_approach_baseline(_chiller(n=12)) is None
    # load range too narrow to identify a slope (constant load)
    flat = _chiller()
    flat["tons"] = 150.0
    assert fit_approach_baseline(flat) is None
    # missing columns
    assert fit_approach_baseline(pd.DataFrame({"tons": [1.0] * 100})) is None
    # all approach values non-physical
    bad = _chiller()
    bad["approach_f"] = np.nan
    assert fit_approach_baseline(bad) is None


def test_serialization_round_trips_and_predicts_identically():
    b = fit_approach_baseline(_chiller())
    again = ApproachBaseline.from_dict(b.as_dict())
    assert again == b
    assert again.predict(180.0) == b.predict(180.0)


def test_tons_from_flow_matches_the_repo_convention():
    frame = pd.DataFrame({"CHW_Flow": [1200.0], "CHWS_Temp": [44.0], "CHWR_Temp": [56.0]})
    tons = tons_from_flow(frame)
    assert abs(float(tons.iloc[0]) - 1200.0 * 12.0 / 24.0) < 1e-9


# --------------------------------------------------------------------------- drift


def test_stable_and_drifting_chillers_are_distinguishable():
    """The headline case: same baseline, same load profile, only one chiller degrades.

    Chiller A holds its commissioned approach; chiller B walks from ~4 to ~8 degF at matched load
    over the current window. Both are 'ok' to the existing median-vs-design rule until B crosses an
    absolute threshold; against a fitted baseline they separate immediately.
    """
    baseline_window = _chiller(start="2025-05-01", seed=1)
    baseline = fit_approach_baseline(baseline_window)
    assert baseline is not None

    stable = _chiller(start="2025-06-01", seed=2)
    drifting = _chiller(start="2025-06-01", seed=2, ramp_f=4.0)

    a = drift_stats(baseline, stable)
    b = drift_stats(baseline, drifting)
    assert a is not None and b is not None

    # the stable unit sits on its own baseline; the drifting one sits well above it
    assert abs(a.drift_f) < 0.3
    assert abs(a.drift_sigma) < 2.0
    assert b.drift_f > 1.5
    assert b.drift_sigma > 5.0
    # and the separation is unambiguous, not a marginal call
    assert b.drift_sigma > a.drift_sigma + 5.0

    # only the drifting unit is still climbing, at roughly 4 degF over the 30-day window
    assert abs(a.slope_f_per_month) < 0.5
    assert 3.0 < b.slope_f_per_month < 5.5

    # and it spends most of its hours outside the baseline's 2-sigma band
    assert a.pct_outside_2sigma < 10.0
    assert b.pct_outside_2sigma > 50.0
    assert not a.extrapolated and not b.extrapolated


def test_a_busier_period_alone_is_not_drift():
    """Load normalization earns its keep: more load must not read as degradation.

    The current window runs at a much higher load than the baseline window, so its *raw median
    approach* is clearly higher -- the comparison the existing rule would make. At matched load the
    drift statistic stays near zero.
    """
    baseline_window = _chiller(start="2025-05-01", seed=3)
    baseline = fit_approach_baseline(baseline_window)
    assert baseline is not None

    busy = _chiller(start="2025-06-01", seed=4)
    busy["tons"] = np.clip(busy["tons"] + 90.0, 40.0, 400.0)
    busy["approach_f"] = (
        _INTERCEPT_F
        + _SLOPE_F_PER_TON * busy["tons"]
        + np.random.default_rng(9).normal(0, _SIGMA_F, len(busy))
    )

    raw_shift = float(busy["approach_f"].median() - baseline_window["approach_f"].median())
    assert raw_shift > 0.7  # a level-vs-level comparison sees a real-looking rise

    d = drift_stats(baseline, busy)
    assert d is not None
    assert abs(d.drift_f) < 0.3  # at matched load, there is no drift
    assert d.extrapolated  # and the caller is told the load left the fitted envelope


def test_drift_declines_when_there_is_nothing_to_score():
    baseline = fit_approach_baseline(_chiller())
    assert baseline is not None
    assert drift_stats(baseline, _chiller(n=4)) is None
    assert drift_stats(baseline, pd.DataFrame({"tons": [], "approach_f": []})) is None
    # unloaded hours carry no fouling information and are excluded
    idle = _chiller(n=200)
    idle["tons"] = 1.0
    assert drift_stats(baseline, idle) is None


def test_drift_slope_is_nan_without_a_usable_time_index():
    baseline = fit_approach_baseline(_chiller())
    assert baseline is not None
    untimed = _chiller(start="2025-06-01").reset_index(drop=True)
    d = drift_stats(baseline, untimed)
    assert d is not None
    assert d.slope_f_per_month != d.slope_f_per_month  # NaN: no time axis to trend against
    assert d.coverage_start == ""
