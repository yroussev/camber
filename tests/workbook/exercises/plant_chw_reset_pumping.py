"""Answer key: workbook exercise ``plant-chw-reset-pumping`` (docs/workbook/plant-chw-reset-pumping.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-chiller
    camber datasets ingest lbnl-chiller --store lab_store
    camber datasets config lbnl-chiller --exercise plant-chw-reset-pumping --store lab_store --out chw.json
    camber run chw.json --out chw_out

(CAMBER 0.97.0-dev, lbnl-chiller default subset, 2026-09-29; the four runs it holds read the same
in the full subset.)
"""  # noqa: E501

from __future__ import annotations

from _plant_standins import chiller_standin
from _workbook import REAL, Check, Exercise, Finding, Metric, Run

#: the lbnl-chiller default subset's runs
_RUNS = ("fault_free", "coolingtower_fouling_065", "bypass_stuck_075", "chiller_bias_2")


def standin(store) -> None:
    """ds-lbnl-chiller with the default subset's four runs (see _plant_standins.py)."""
    chiller_standin(store, _RUNS)


def _reset_present(ctx) -> None:
    """The plant resets its chilled water: warmer in cool weather, colder in hot (a negative
    slope on the dry-bulb)."""
    f = ctx.finding("chw_plant_reset", "PLANT__fault_free")
    assert f is not None, "no chw_plant_reset finding on PLANT__fault_free"
    assert f.metrics["chwst_reset_present"] is True, "CHWST reset not found"
    assert f.metrics["chwst_slope_per_F"] < -0.1, f"slope {f.metrics['chwst_slope_per_F']}"


def _no_dp_reset_no_floor(ctx) -> None:
    """The DP setpoint is flat (no reset), and the rule counts no hours near the VFD minimum on
    any run: its 25 % band sits below this pump's floor."""
    for f in ctx.findings():
        if f.rule != "chw_pump_dp_reset":
            continue
        assert f.metrics["dp_sp_reset_present"] is False, f"{f.equip}: DP reset found"
        assert f.metrics["pct_running_near_min"] == 0.0, f"{f.equip}: hours near minimum"


def _bypass_cannot_hold_chwst(ctx) -> None:
    """With the tower bypass stuck the plant cannot make chilled water: its supply runs at least
    5 F warmer than the healthy plant's, at a smaller delta-T."""
    base = ctx.finding("chw_plant_reset", "PLANT__fault_free")
    stuck = ctx.finding("chw_plant_reset", "PLANT__bypass_stuck_075")
    assert base is not None and stuck is not None, "no chw_plant_reset finding"
    b, s = base.metrics["chwst_median_f"], stuck.metrics["chwst_median_f"]
    assert s >= b + 5.0, f"CHWST median {b} F healthy vs {s} F bypassed (expected +5 F)"
    assert stuck.metrics["deltaT_median_f"] < base.metrics["deltaT_median_f"]


EXERCISE = Exercise(
    id="plant-chw-reset-pumping",
    title="Chilled-water reset and pumping: low delta-T, riding the curve and the VFD minimum",
    issue=82,
    references=("pnnl-guide-plant-cooling", "pnnl-retuning-ch8"),
    datasets=("lbnl-chiller",),
    runs=(Run(dataset="lbnl-chiller", config="plant-chw-reset-pumping"),),
    commands=(
        "camber datasets fetch lbnl-chiller",
        "camber datasets ingest lbnl-chiller --store lab_store",
        "camber datasets config lbnl-chiller --exercise plant-chw-reset-pumping "
        "--store lab_store --out chw.json",
        "camber run chw.json --out chw_out",
    ),
    expect=(
        # 1. the chilled-water supply is reset with the weather
        Check("the fault-free plant resets its CHWST", _reset_present),
        Metric(
            "chw_plant_reset",
            "PLANT__fault_free",
            "chwst_slope_per_F",
            -0.382,
            0.005,
            on=REAL,
            quote="-0.38 °F",
        ),
        # 2. ... yet the rule faults the healthy plant: its loop delta-T is low
        Finding("chw_plant_reset", "PLANT__fault_free", severity=("fault",)),
        Metric(
            "chw_plant_reset",
            "PLANT__fault_free",
            "deltaT_median_f",
            6.4,
            0.05,
            on=REAL,
            quote="6.4 °F",
        ),
        Metric(
            "chw_plant_reset",
            "PLANT__fault_free",
            "low_deltaT_pct",
            80.1,
            0.1,
            on=REAL,
            quote="80.1%",
        ),
        # 3. the secondary pump rides the curve against a flat DP setpoint
        Finding("chw_pump_dp_reset", "PLANT__fault_free", severity=("warn",)),
        Check("no DP reset, and nothing counted near the VFD minimum", _no_dp_reset_no_floor),
        Metric(
            "chw_pump_dp_reset",
            "PLANT__fault_free",
            "pct_running_near_full",
            38.0,
            0.1,
            on=REAL,
            quote="38.0%",
        ),
        Metric(
            "chw_pump_dp_reset",
            "PLANT__fault_free",
            "median_speed_pct",
            84.9,
            0.1,
            on=REAL,
            quote="84.9%",
        ),
        # 4. the VFD floor: where the pump idles, the rule's median is the floor itself
        Metric(
            "chw_pump_dp_reset",
            "PLANT__chiller_bias_2",
            "median_speed_pct",
            34.5,
            0.1,
            on=REAL,
            quote="34.5%",
        ),
        # 5. a stuck tower bypass: the plant loses its chilled water and the pump runs flat out
        Check("the bypassed plant cannot hold its CHWST", _bypass_cannot_hold_chwst),
        Finding("chw_plant_reset", "PLANT__bypass_stuck_075", severity=("fault",)),
        Metric(
            "chw_plant_reset",
            "PLANT__bypass_stuck_075",
            "chwst_median_f",
            65.0,
            0.05,
            on=REAL,
            quote="65.0 °F",
        ),
        Metric(
            "chw_plant_reset",
            "PLANT__bypass_stuck_075",
            "deltaT_median_f",
            0.7,
            0.05,
            on=REAL,
            quote="0.7 °F",
        ),
        Finding("chw_pump_dp_reset", "PLANT__bypass_stuck_075", present=True),
        Metric(
            "chw_pump_dp_reset",
            "PLANT__bypass_stuck_075",
            "median_speed_pct",
            100.0,
            0.05,
            on=REAL,
            quote="100%",
        ),
    ),
    standin=standin,
)
