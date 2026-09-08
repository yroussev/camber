"""Tests for pattern G — templated subsystem diagnostic scatters (camber.charts.diagnostic).
Rendering runs headless on Agg."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.charts.diagnostic import (  # noqa: E402
    TEMPLATES,
    band,
    diagnostic_scatter,
    fitted_band,
    reset_line,
)
from camber.chillerbaseline import LoadBaseline  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _oat(n=200, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(rng.uniform(10, 95, n), index=idx), rng, idx


def test_sat_reset_conforming_vs_flat():
    oat, rng, idx = _oat()
    good = pd.Series(
        65 + (55 - 65) / 60 * oat.clip(0, 60) + rng.normal(0, 0.5, len(oat)), index=idx
    )
    flat = pd.Series(np.full(len(oat), 55.0) + rng.normal(0, 0.5, len(oat)), index=idx)
    _, m_good = diagnostic_scatter(
        pd.DataFrame({Role.OAT: oat, Role.SUPPLY_AIR_TEMP: good}), TEMPLATES["sat_reset"]
    )
    _, m_flat = diagnostic_scatter(
        pd.DataFrame({Role.OAT: oat, Role.SUPPLY_AIR_TEMP: flat}), TEMPLATES["sat_reset"]
    )
    assert m_good.mean() < 0.05  # conforms to the reset schedule (endpoints clamp)
    assert m_flat.mean() > 0.4  # a flat SAT ignores the reset -> many violations


def test_economizer_stuck_minimum_flagged():
    oat, rng, idx = _oat()
    good = pd.Series(np.where(oat < 65, 0.8, 0.1), index=idx)  # opens for free cooling
    stuck = pd.Series(np.full(len(oat), 0.1), index=idx)  # never opens
    _, m_good = diagnostic_scatter(
        pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: good}), TEMPLATES["economizer"]
    )
    _, m_stuck = diagnostic_scatter(
        pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: stuck}), TEMPLATES["economizer"]
    )
    assert m_good.mean() < 0.02 and m_stuck.mean() > 0.3


def test_no_simultaneous_heat_cool_flagged():
    oat, rng, idx = _oat()
    cool = pd.Series(np.where(oat > 70, 0.7, 0.0), index=idx)
    heat_ok = pd.Series(np.where(oat < 40, 0.6, 0.0), index=idx)  # never both open
    heat_bad = pd.Series(np.full(len(oat), 0.5), index=idx)  # heating on during cooling
    _, m_ok = diagnostic_scatter(
        pd.DataFrame({Role.COOL_VALVE: cool, Role.HEAT_VALVE: heat_ok}),
        TEMPLATES["no_simultaneous_hc"],
    )
    _, m_bad = diagnostic_scatter(
        pd.DataFrame({Role.COOL_VALVE: cool, Role.HEAT_VALVE: heat_bad}),
        TEMPLATES["no_simultaneous_hc"],
    )
    assert m_ok.mean() == 0.0 and m_bad.mean() > 0.2


def test_returns_axes_and_mask_aligned_to_index():
    oat, rng, idx = _oat(n=50)
    y = pd.Series(np.full(50, 55.0), index=idx)
    ax, mask = diagnostic_scatter(
        pd.DataFrame({Role.OAT: oat, Role.SUPPLY_AIR_TEMP: y}), TEMPLATES["sat_reset"]
    )
    assert ax.collections  # expected band + scatter drawn
    assert isinstance(mask, pd.Series) and mask.index.equals(idx) and mask.dtype == bool


def test_custom_band_template_and_tolerance():
    idx = pd.date_range("2024-01-01", periods=20, freq="1h")
    x = pd.Series(range(20), index=idx)
    y = pd.Series([5.0] * 19 + [50.0], index=idx)  # one clear outlier
    t = band("x", "y", low=0.0, high=10.0, name="approach")
    _, m = diagnostic_scatter(pd.DataFrame({"x": x, "y": y}), t)
    assert m.sum() == 1 and bool(m.iloc[-1])
    # tolerance widens the band enough to forgive it
    _, m2 = diagnostic_scatter(pd.DataFrame({"x": x, "y": y}), t, tolerance=45.0)
    assert m2.sum() == 0


def test_reset_line_clamps_outside_range():
    # beyond the schedule's x-range the expected band holds the endpoint, not the extrapolated slope
    t = reset_line("x", "y", p1=(0.0, 65.0), p2=(60.0, 55.0), tol=1.0)
    lo, hi = t.expected(np.array([-20.0, 120.0]))  # far outside [0, 60]
    assert 64.0 <= lo[0] <= 66.0  # clamped at y1=65, not 65+extrapolation
    assert 54.0 <= hi[1] <= 56.0  # clamped at y2=55


# --------------------------------------------------------------------------- fitted_band


def _baseline(sigma=0.5):
    """approach = 5.0 + 0.01 * tons, fitted over a 50–150 ton envelope."""
    return LoadBaseline(
        n=200,
        slope_f_per_ton=0.01,
        intercept_f=5.0,
        sigma_f=sigma,
        r2=0.95,
        tons_min=50.0,
        tons_max=150.0,
    )


def _frame(tons, approach):
    idx = pd.date_range("2025-06-01", periods=len(tons), freq="1h")
    return pd.DataFrame({"tons": tons, "approach_f": approach}, index=idx)


def test_fitted_band_follows_the_baseline_line():
    tmpl = fitted_band(_baseline(), "tons", "approach_f", k=2.0)
    low, high = tmpl.expected(np.array([50.0, 100.0, 150.0]))

    np.testing.assert_allclose(low, [5.5 - 1.0, 6.0 - 1.0, 6.5 - 1.0])
    np.testing.assert_allclose(high, [5.5 + 1.0, 6.0 + 1.0, 6.5 + 1.0])


def test_a_reading_off_the_frozen_line_is_flagged():
    frame = _frame([60.0, 100.0, 140.0], [5.6, 6.0, 9.0])  # the last is ~2.6 degF high
    _, mask = diagnostic_scatter(frame, fitted_band(_baseline(), "tons", "approach_f"))
    assert list(mask) == [False, False, True]


def test_outside_the_load_envelope_nothing_is_claimed():
    """Judging a reading against an extrapolated fit is the asserted negative the rules refuse."""
    frame = _frame([20.0, 100.0, 200.0], [99.0, 6.0, 99.0])  # wild values, but off the envelope
    tmpl = fitted_band(_baseline(), "tons", "approach_f")

    low, high = tmpl.expected(np.array([20.0, 100.0, 200.0]))
    assert np.isnan(low[0]) and np.isnan(high[0])
    assert np.isnan(low[2]) and np.isnan(high[2])
    assert not np.isnan(low[1])

    _, mask = diagnostic_scatter(frame, tmpl)
    assert list(mask) == [False, False, False]  # unjudged, not judged-and-passed


def test_extrapolation_is_opt_in():
    tmpl = fitted_band(_baseline(), "tons", "approach_f", within_envelope=False)
    low, high = tmpl.expected(np.array([200.0]))
    assert not np.isnan(low[0]) and not np.isnan(high[0])

    _, mask = diagnostic_scatter(_frame([200.0], [99.0]), tmpl)
    assert list(mask) == [True]


def test_k_widens_the_band():
    narrow = fitted_band(_baseline(), "tons", "approach_f", k=1.0).expected(np.array([100.0]))
    wide = fitted_band(_baseline(), "tons", "approach_f", k=3.0).expected(np.array([100.0]))
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_a_zero_scatter_baseline_gives_a_degenerate_band_not_a_crash():
    tmpl = fitted_band(_baseline(sigma=0.0), "tons", "approach_f")
    low, high = tmpl.expected(np.array([100.0]))
    assert low[0] == high[0] == 6.0
    _, mask = diagnostic_scatter(_frame([100.0], [6.0]), tmpl)
    assert list(mask) == [False]


def test_renders_against_a_frozen_store_baseline():
    """The end-to-end shape: rebuild a frozen record's model and plot the current period on it."""
    from camber.store.modelstore import BaselineStore

    store = BaselineStore()
    store.freeze(
        _baseline(),
        site="S",
        equip="CH_1",
        kind="chiller_approach_cond",
        frozen_at="2025-06-01",
        period=("2025-01-01", "2025-03-01"),
    )
    model = store.model_for("S", "CH_1", "chiller_approach_cond")
    ax, mask = diagnostic_scatter(
        _frame([100.0, 120.0], [6.0, 9.5]),
        fitted_band(model, "tons", "approach_f", name="CH_1 condenser approach"),
    )
    assert list(mask) == [False, True]
    assert "CH_1 condenser approach" in ax.get_title()
