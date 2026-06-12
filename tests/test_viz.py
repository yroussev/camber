"""Tests for the visualization charts (carpet, CUSUM, energy signature).

Rendering runs on the headless Agg backend; tests check the reshape/overlay logic and that
each plotter returns a populated Axes.
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.carpet import carpet_matrix, load_carpet  # noqa: E402
from camber.charts.cusum_chart import cusum_plot  # noqa: E402
from camber.charts.energy_signature import energy_signature  # noqa: E402


def _hourly_load(days=21, seed=0):
    """Synthetic hourly kW: low overnight, high midday, weekend setback."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-06-01", periods=days * 24, freq="1h")
    base = 40.0
    occ = ((idx.hour >= 7) & (idx.hour <= 18) & (idx.dayofweek < 5)).astype(float)
    load = base + 60.0 * occ + rng.normal(0, 2, len(idx))
    return pd.Series(load, index=idx)


def test_carpet_matrix_shape_and_pattern():
    s = _hourly_load(days=14)
    mat, hours, dates = carpet_matrix(s)
    assert mat.shape == (24, 14)                     # 24 hours x 14 days
    assert list(hours) == list(range(24))
    # midday (hour 12) on a weekday is far above 3am — the occupancy band is real
    assert np.nanmean(mat[12, :]) > np.nanmean(mat[3, :]) + 20.0


def test_carpet_empty_series():
    mat, hours, dates = carpet_matrix(pd.Series(dtype=float))
    assert mat.size == 0
    ax = load_carpet(pd.Series(dtype=float))         # must not raise
    assert "no data" in ax.get_title()


def test_load_carpet_returns_axes_with_image():
    ax = load_carpet(_hourly_load(days=10), title="t")
    assert ax.get_ylabel() == "Hour of day"
    assert len(ax.images) == 1                        # the heatmap was drawn


def test_cusum_plot_direction_and_title():
    idx = pd.date_range("2024-01-01", periods=60, freq="D")
    projected = pd.Series(100.0, index=idx)
    actual = pd.Series(90.0, index=idx)              # using 10 less/day -> savings
    ax = cusum_plot(projected, actual, limit=200, units="kWh")
    assert "savings" in ax.get_title()
    assert len(ax.lines) >= 1


def test_cusum_plot_waste_when_overconsuming():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    ax = cusum_plot(pd.Series(80.0, index=idx), pd.Series(95.0, index=idx))
    assert "waste" in ax.get_title()


def test_cusum_plot_empty():
    ax = cusum_plot(pd.Series(dtype=float), pd.Series(dtype=float))
    assert "no overlapping data" in ax.get_title()


def test_energy_signature_fits_and_overlays_cooling():
    T = np.linspace(45, 100, 60)
    y = 50 + 2.0 * np.maximum(0.0, T - 65) + np.random.default_rng(1).normal(0, 1, 60)
    ax, model = energy_signature(T, y)
    assert model.kind in ("3PC", "4P", "5P")         # a cooling signature
    assert len(ax.collections) >= 1                  # scatter present
    assert any(ln.get_label().endswith("fit") for ln in ax.lines)
    # change point near 65 °F drawn as a guide
    assert model.change_points and 55 < float(model.change_points[0]) < 75


def test_energy_signature_accepts_prefit_model():
    T = np.linspace(45, 100, 40)
    y = 30 + 1.5 * np.maximum(0.0, T - 60)
    from camber.mandv.models import best_model
    m = best_model(T, y)
    ax, model = energy_signature(T, y, model=m)
    assert model is m                                # used the supplied model, no refit
