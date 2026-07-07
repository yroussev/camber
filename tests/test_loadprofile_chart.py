"""Tests for pattern F — load profiles & load-duration curves
(camber.charts.loadprofile_chart). Rendering runs headless on Agg."""

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


from camber.loadprofile import LoadMetrics  # noqa: E402
from camber.charts.loadprofile_chart import load_duration_chart, load_profile_chart  # noqa: E402


def _load(days=21, seed=0):
    idx = pd.date_range("2024-06-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    occ = ((idx.hour >= 8) & (idx.hour <= 18) & (idx.dayofweek < 5)).astype(float)
    return pd.Series(40 + 60 * occ + rng.normal(0, 2, len(idx)), index=idx)


def test_load_profile_split_weekday_vs_weekend():
    ax, m = load_profile_chart(_load(), split=True)
    assert isinstance(m, LoadMetrics)
    lines = ax.get_lines()
    assert len(lines) == 3                               # weekday, weekend, baseload ref
    wk, we = lines[0].get_ydata(), lines[1].get_ydata()
    assert wk[12] > we[12] + 30                          # occupied weekday midday >> weekend


def test_load_profile_single_daily_avg():
    ax, m = load_profile_chart(_load(), split=False, annotate=False)
    assert len([ln for ln in ax.get_lines()]) == 1       # one daily-average line, no baseload ref


def test_load_duration_curve_monotone_and_annotated():
    ax, m = load_duration_chart(_load())
    ydata = ax.get_lines()[0].get_ydata()
    assert np.all(np.diff(ydata) <= 1e-9)                # sorted high-to-low
    assert 0.0 < m.load_factor < 1.0
    labels = " ".join(ln.get_label() for ln in ax.get_lines())
    assert "peak" in labels and "baseload" in labels


def test_load_duration_cost_translation():
    ax, _ = load_duration_chart(_load(), price=0.15)
    title = ax.get_title()
    assert "kWh" in title and "$" in title               # energy × price surfaced


def test_load_duration_no_price_no_cost_note():
    ax, _ = load_duration_chart(_load())
    assert "$" not in ax.get_title()
