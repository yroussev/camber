"""Smoke/coverage tests for previously-untested modules (cli, inventory, charts).

These don't assert pixel output -- they exercise the code paths end to end so a
crash regression is caught and the modules carry real coverage.
"""

import os
import sys

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # headless rendering for the chart functions
from matplotlib.figure import Figure  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import cli, inventory  # noqa: E402
from camber.charts.box_reheat import box_reheat_figure  # noqa: E402
from camber.charts.zones_chart import (  # noqa: E402
    zones_timeofweek_figure, zones_vs_oat_figure,
)


# --- cli (covers cli + charts/scatter + charts/timeseries + synth) ---------- #

def test_cli_demo_writes_outputs(tmp_path):
    out = str(tmp_path / "out")
    rc = cli.main(["--demo", "reheat", "--ahu", "1", "--out", out])
    assert rc == 0
    assert os.path.exists(os.path.join(out, "hec_summary.json"))
    pngs = [f for f in os.listdir(out) if f.endswith(".png")]
    assert len(pngs) >= 2


# --- inventory -------------------------------------------------------------- #

def test_parse_name_generic():
    et, eid, meas = inventory.parse_name("AHU_1_CHW_Valve.csv")
    assert et == "AHU" and eid == "1" and "CHW_Valve" in meas


def test_inventory_and_to_rows(tmp_path):
    folder = tmp_path / "trends"
    folder.mkdir()
    idx = pd.date_range("2025-01-01", periods=3, freq="1h").strftime("%d-%b-%y %I:%M:%S %p")
    for meas in ("CHW_Valve", "HHW_Valve"):
        pd.DataFrame({"Timestamp": idx + " PST", "Value (%)": [10, 20, 30]}).to_csv(
            folder / f"AHU_1_{meas}.csv", index=False)
    points = inventory.inventory([str(folder)], count_rows=True)
    assert len(points) == 2
    rows = inventory.to_rows(points)
    assert len(rows) == 2
    assert any("CHW_Valve" in str(r) for r in rows)


# --- charts: box_reheat + zones --------------------------------------------- #

def test_box_reheat_figure():
    idx = pd.date_range("2025-07-07", periods=24 * 5, freq="1h")  # a weekday span
    df = pd.DataFrame({"HWValve": 30.0, "ActFlow": 800.0, "ActFlowSP": 700.0}, index=idx)
    fig = box_reheat_figure(df, "VAV_101")
    assert isinstance(fig, Figure)


def test_zones_figures():
    profile = pd.DataFrame({"n_heating": range(168), "n_cooling": range(168),
                            "n_both": [1] * 168}, index=range(168))
    assert isinstance(zones_timeofweek_figure(profile), Figure)

    idx = pd.date_range("2025-07-01", periods=48, freq="1h")
    states = pd.DataFrame({"n_heating": 2, "n_cooling": 3}, index=idx)
    oat = pd.Series(80.0, index=idx)
    assert isinstance(zones_vs_oat_figure(states, oat), Figure)
