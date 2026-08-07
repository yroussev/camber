"""Coverage-lifting tests for chart branches the main suites skip.

Targets the still-red branches in box_reheat, multitrend, timeseries, cusum_chart,
energy_signature, scatter and diagnostic: empty / minimal frames, the supply-an-axes
path, and the no-OAT / no-Occ fall-throughs. Rendering runs headless on Agg.
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.box_reheat import box_reheat_figure  # noqa: E402
from camber.charts.cusum_chart import cusum_plot  # noqa: E402
from camber.charts.diagnostic import TEMPLATES, _col, diagnostic_scatter  # noqa: E402
from camber.charts.energy_signature import energy_signature  # noqa: E402
from camber.charts.multitrend import fault_multitrend, mask_to_spans  # noqa: E402
from camber.charts.scatter import ahu_hec_scatter, hec_metrics  # noqa: E402
from camber.charts.timeseries import ahu_hec_timeseries  # noqa: E402
from camber.model.roles import Role  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


# --------------------------------------------------------------------------- box_reheat


def _box_index(days=3, freq="30min"):
    return pd.date_range("2024-06-03", periods=days * 48, freq=freq)  # a Monday


def test_box_reheat_full_frame_with_oat():
    """Every column present + OAT -> exercises both panels, twinx, and the hot-OAT scatter."""
    idx = _box_index()
    n = len(idx)
    oat = pd.Series(np.linspace(50, 80, n), index=idx)
    df = pd.DataFrame(
        {
            "HWValve": np.linspace(0, 40, n),  # some > valve_thr
            "ActFlow": np.full(n, 500.0),
            "ActFlowSP": np.full(n, 550.0),
            "SpaceTemp": np.full(n, 72.0),
            "ActHeatSP": np.full(n, 70.0),
            "ActCoolSP": np.full(n, 75.0),
            "WarmUp": np.zeros(n),  # present; CoolDown absent -> both loop arms exercised
        },
        index=idx,
    )
    fig = box_reheat_figure(df, "VAV-1", oat=oat)
    assert isinstance(fig, Figure)
    assert len(fig.axes) >= 2


def test_box_reheat_actflow_without_setpoint_and_no_hwvalve():
    """ActFlow present but no ActFlowSP, and no HWValve -> the skipped-branch paths."""
    idx = _box_index(days=2)
    n = len(idx)
    df = pd.DataFrame({"ActFlow": np.full(n, 400.0), "SpaceTemp": np.full(n, 71.0)}, index=idx)
    # OAT present but no HWValve -> the hot-OAT highlight branch is skipped
    oat = pd.Series(np.full(n, 70.0), index=idx)
    fig = box_reheat_figure(df, "VAV-2", oat=oat, occupied_only=False)
    assert isinstance(fig, Figure)


def test_box_reheat_hwvalve_only_no_actflow():
    """HWValve present but no ActFlow -> the airflow twinx branch is skipped."""
    idx = _box_index(days=2)
    n = len(idx)
    df = pd.DataFrame({"HWValve": np.full(n, 10.0)}, index=idx)
    fig = box_reheat_figure(df, "VAV-3", occupied_only=False)
    assert isinstance(fig, Figure)


# --------------------------------------------------------------------------- multitrend


def test_mask_to_spans_all_false_is_empty():
    idx = pd.date_range("2024-01-01", periods=10, freq="h")
    assert mask_to_spans(pd.Series(False, index=idx)) == []


def test_fault_multitrend_normalized_with_spans():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    df = pd.DataFrame({"a": np.linspace(0, 100, 48), "b": np.linspace(20, 5, 48)}, index=idx)
    mask = pd.Series(df.index.hour < 3, index=idx)
    ax = fault_multitrend(df, ["a", "b"], spans={"night": mask}, normalize=True)
    assert isinstance(ax, Axes)
    assert "fault overlay" in ax.get_title()


# --------------------------------------------------------------------------- timeseries / scatter


def _hec_index(n=200):
    return pd.date_range("2024-07-01", periods=n, freq="h")


def _hec_frame(*, with_oat=True, with_occ=True, n=200, seed=0):
    """Legacy-column AHU frame: AHU1_HeC / AHU1_CC (+ optional OAT / Occ)."""
    idx = _hec_index(n)
    rng = np.random.default_rng(seed)
    data = {
        "AHU1_HeC": rng.uniform(0, 40, n),
        "AHU1_CC": rng.uniform(0, 40, n),
    }
    if with_oat:
        data["Bldg_TempOa"] = rng.uniform(40, 90, n)
    if with_occ:
        data["AHU1_Occ"] = (idx.hour >= 7) & (idx.hour < 18)
    return pd.DataFrame(data, index=idx)


def test_hec_timeseries_supplied_axes_and_no_oat():
    """ax supplied (skips subplots) and a frame with no OAT column (skips the twinx)."""
    _, ax = plt.subplots()
    out = ahu_hec_timeseries(_hec_frame(with_oat=False, with_occ=False), 1, ax=ax)
    assert out is ax


def test_hec_metrics_no_oat_no_occ():
    """oat_col None -> the simult_hot=0 branch; occupied_only with no Occ -> mask-None branch."""
    m = hec_metrics(_hec_frame(with_oat=False, with_occ=False), 1, occupied_only=True)
    assert m.simultaneous_pct_oat_gt_65 == 0.0


def test_hec_scatter_missing_cc_raises():
    idx = _hec_index(20)
    df = pd.DataFrame({"AHU1_HeC": np.zeros(20)}, index=idx)  # no _CC
    with pytest.raises(KeyError):
        ahu_hec_scatter(df, 1)


def test_hec_scatter_plain_color_and_occupied():
    """color_by != 'oat' -> plain scatter; occupied_only with an Occ column exercises the mask."""
    _, ax = plt.subplots()
    out, m = ahu_hec_scatter(
        _hec_frame(with_oat=True, with_occ=True), 1, color_by="plain", occupied_only=True, ax=ax
    )
    assert out is ax and m.n_considered <= m.n_intervals


# ------------------------------------------------------------------- cusum / energy_signature


def test_cusum_plot_supplied_axes():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    _, ax = plt.subplots()
    out = cusum_plot(pd.Series(100.0, index=idx), pd.Series(90.0, index=idx), ax=ax, limit=50)
    assert out is ax


def test_energy_signature_supplied_axes():
    T = np.linspace(45, 95, 40)
    y = 50 + 2.0 * np.maximum(0.0, T - 65)
    _, ax = plt.subplots()
    out, model = energy_signature(T, y, ax=ax)
    assert out is ax and model is not None


# --------------------------------------------------------------------------- diagnostic


def test_diagnostic_col_string_fallback_and_missing():
    """_col resolves a Role by matching a string column name, and raises when absent."""
    idx = pd.date_range("2024-01-01", periods=5, freq="h")
    # columns are plain strings equal to the Role names, not Role enums
    frame = pd.DataFrame(
        {Role.OAT.name: np.arange(5.0), Role.SUPPLY_AIR_TEMP.name: np.arange(5.0)}, index=idx
    )
    resolved = _col(frame, Role.OAT)
    assert list(resolved) == list(range(5))
    with pytest.raises(KeyError):
        _col(frame, Role.CHW_SUPPLY_TEMP)


def test_diagnostic_scatter_empty_after_dropna():
    """All-NaN frame -> nothing survives dropna; the len(xv)==0 branch is taken."""
    idx = pd.date_range("2024-01-01", periods=6, freq="h")
    frame = pd.DataFrame(
        {Role.OAT: np.full(6, np.nan), Role.SUPPLY_AIR_TEMP: np.full(6, np.nan)}, index=idx
    )
    ax, mask = diagnostic_scatter(frame, TEMPLATES["sat_reset"])
    assert isinstance(ax, Axes) and len(mask) == 0
