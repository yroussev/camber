"""Tests for pattern D — OAT cloud-shape scatter, classification, and brush-back
(camber.charts.oat_scatter). Rendering runs headless on Agg."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.oat_scatter import (  # noqa: E402
    CloudShape,
    brush_back,
    classify_shape,
    oat_scatter,
)


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


def _oat(n=400, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="3h")
    return pd.Series(rng.uniform(20, 95, n), index=idx), rng


def test_classify_linear_vs_v_vs_hockey_vs_scattered():
    T, rng = _oat()
    lin = pd.Series(2.0 * T + rng.normal(0, 3, len(T)), index=T.index)
    v = pd.Series(40 + 2.0 * np.abs(T - 60) + rng.normal(0, 3, len(T)), index=T.index)
    hs = pd.Series(30 + np.clip(T - 65, 0, None) * 3 + rng.normal(0, 3, len(T)), index=T.index)
    sc = pd.Series(50 + rng.normal(0, 15, len(T)), index=T.index)  # no OAT dependence

    assert classify_shape(lin, T).shape == "linear"
    assert classify_shape(v, T).shape == "v"
    assert classify_shape(hs, T).shape == "hockey-stick"
    assert classify_shape(sc, T).shape == "scattered"


def test_classify_reports_fit_and_is_jsonable():
    T, rng = _oat()
    lin = pd.Series(2.0 * T + rng.normal(0, 3, len(T)), index=T.index)
    cs = classify_shape(lin, T)
    assert isinstance(cs, CloudShape) and cs.model_kind == "2P" and cs.r2 > 0.9
    d = cs.as_dict()
    assert d["shape"] == "linear" and d["n"] == len(T)


def test_classify_insufficient_data():
    idx = pd.date_range("2024-01-01", periods=3, freq="1h")
    cs = classify_shape(pd.Series([1.0, 2.0, 3.0], index=idx), pd.Series([50, 60, 70], index=idx))
    assert cs.shape == "insufficient" and cs.n == 3


def test_brush_back_maps_region_to_timestamps():
    T, rng = _oat()
    y = pd.Series(2.0 * T + rng.normal(0, 3, len(T)), index=T.index)
    ts = brush_back(y, T, x_range=(20, 40))
    assert len(ts) > 0
    assert bool((T.loc[ts] <= 40).all()) and bool((T.loc[ts] >= 20).all())
    # a y-box narrows it further, never widens
    ts2 = brush_back(y, T, x_range=(20, 40), y_range=(0, 60))
    assert set(ts2).issubset(set(ts))


def test_oat_scatter_returns_axes_fit_and_shape():
    T, rng = _oat()
    v = pd.Series(40 + 2.0 * np.abs(T - 60) + rng.normal(0, 3, len(T)), index=T.index)
    ax, shape = oat_scatter(v, T, ylabel="kW")
    assert ax.collections and ax.get_lines()  # scatter + fit/guide lines drawn
    assert shape.shape == "v" and shape.change_points  # balance point(s) fit


def test_oat_scatter_changepoint_off_and_no_classify():
    T, rng = _oat()
    y = pd.Series(2.0 * T + rng.normal(0, 3, len(T)), index=T.index)
    ax, shape = oat_scatter(y, T, changepoint=False, classify=False)
    assert shape is None
    assert not any(ln.get_label() == "2P fit" for ln in ax.get_lines())  # no fit overlay


def test_oat_scatter_color_by_category():
    T, rng = _oat()
    y = pd.Series(2.0 * T + rng.normal(0, 3, len(T)), index=T.index)
    season = pd.Series(np.where(T < 55, "cold", "warm"), index=T.index, name="season")
    ax, _ = oat_scatter(y, T, by=season)
    assert ax.collections  # colored scatter drawn
