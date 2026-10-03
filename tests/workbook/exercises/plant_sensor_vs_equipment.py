"""Answer key: workbook exercise ``plant-sensor-vs-equipment`` (docs/workbook/plant-sensor-vs-equipment.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-chiller
    camber datasets ingest lbnl-chiller --subset full --store lab_store
    camber datasets config lbnl-chiller --exercise plant-sensor-vs-equipment --store lab_store --out sensor.json
    camber run sensor.json --out sensor_out
    camber datasets score lbnl-chiller --store lab_store --findings sensor_out/findings.json

(CAMBER 0.97.0-dev, lbnl-chiller full subset, all 24 runs, 2026-09-29.)
"""  # noqa: E501

from __future__ import annotations

from _plant_standins import chiller_standin
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, Score

_SEC = "secondary_chilled_water_pressure_bias"
_RUNS = (
    "fault_free",
    "chiller_bias_2",
    "chiller_bias_m2",
    "coolingtower_bias_2",
    "coolingtower_bias_m2",
    f"{_SEC}_020",
    f"{_SEC}_m020",
    "bypass_stuck_075",
    "chiller_fouling_065",
)


def standin(store) -> None:
    """ds-lbnl-chiller with the healthy plant, a +/-2 C bias on each of the chiller's leaving-water
    and the tower's leaving-water sensors, a +/-20 % bias on the secondary loop's DP sensor, and
    two real equipment faults to compare them with (see _plant_standins.py)."""
    chiller_standin(store, _RUNS)


def _tower_bias_is_an_offset(ctx) -> None:
    """Both tower-sensor biases come back from condenser_bypass_leak as an ``info`` attributed to
    a sensor offset, while the stuck bypass is a ``fault`` on the valve."""
    for run in ("coolingtower_bias_2", "coolingtower_bias_m2"):
        f = ctx.finding("condenser_bypass_leak", f"PLANT__{run}")
        assert f is not None, f"no condenser_bypass_leak finding on PLANT__{run}"
        assert f.severity == "info", f"{run}: {f.severity}"
        assert f.metrics["attribution"] == "sensor_offset", f"{run}: {f.metrics['attribution']}"
    f = ctx.finding("condenser_bypass_leak", "PLANT__bypass_stuck_075")
    assert f is not None and f.severity == "fault" and f.metrics["attribution"] == "valve"


def _dp_bias_moves_the_pump(ctx) -> None:
    """The DP sensor reading high slows the secondary pump; reading low speeds it up."""
    speed = {}
    for run in ("fault_free", f"{_SEC}_020", f"{_SEC}_m020"):
        f = ctx.finding("chw_pump_dp_reset", f"PLANT__{run}")
        assert f is not None, f"no chw_pump_dp_reset finding on PLANT__{run}"
        speed[run] = f.metrics["median_speed_pct"]
    lo, mid, hi = speed[f"{_SEC}_020"], speed["fault_free"], speed[f"{_SEC}_m020"]
    assert lo < mid < hi, f"median pump speeds +20 %: {lo}, fault-free: {mid}, -20 %: {hi}"


EXERCISE = Exercise(
    id="plant-sensor-vs-equipment",
    title="Plant sensor faults vs equipment faults: why a sensor bias is a negative",
    issue=82,
    references=("pnnl-guide-plant-cooling", "pnnl-retuning-ch8"),
    datasets=("lbnl-chiller",),
    runs=(Run(dataset="lbnl-chiller", config="plant-sensor-vs-equipment", subset="full"),),
    commands=(
        "camber datasets fetch lbnl-chiller",
        "camber datasets ingest lbnl-chiller --subset full --store lab_store",
        "camber datasets config lbnl-chiller --exercise plant-sensor-vs-equipment "
        "--store lab_store --out sensor.json",
        "camber run sensor.json --out sensor_out",
        "camber datasets score lbnl-chiller --store lab_store --findings sensor_out/findings.json",
    ),
    expect=(
        # 1. a chiller leaving-water sensor reading 2 C high: chiller_efficiency calls it a fault
        Finding("chiller_efficiency", "PLANT__chiller_bias_2", severity=("fault",)),
        Metric(
            "chiller_efficiency",
            "PLANT__chiller_bias_2",
            "kw_per_ton_median",
            3.188,
            0.005,
            on=REAL,
            quote="3.19",
        ),
        # ... and reading 2 C low it looks better than new
        Finding("chiller_efficiency", "PLANT__chiller_bias_m2", severity=("ok",)),
        Metric(
            "chiller_efficiency",
            "PLANT__chiller_bias_m2",
            "kw_per_ton_median",
            0.668,
            0.005,
            on=REAL,
            quote="0.67",
        ),
        # 2. the same low-reading chiller sensor surfaces as a pump fault
        Finding("chw_pump_dp_reset", "PLANT__chiller_bias_m2", severity=("fault",)),
        Metric(
            "chw_pump_dp_reset",
            "PLANT__chiller_bias_m2",
            "pct_running_near_full",
            82.1,
            0.1,
            on=REAL,
            quote="82.1%",
        ),
        # 3. the tower sensor biases: CAMBER attributes them to a sensor offset itself
        Check("tower-sensor biases are attributed to a sensor offset", _tower_bias_is_an_offset),
        Metric(
            "condenser_bypass_leak",
            "PLANT__coolingtower_bias_2",
            "median_diff_f",
            -3.14,
            0.01,
            on=REAL,
            quote="-3.1 °F",
        ),
        Metric(
            "condenser_bypass_leak",
            "PLANT__coolingtower_bias_m2",
            "median_diff_f",
            3.61,
            0.01,
            on=REAL,
            quote="+3.6 °F",
        ),
        # 4. the loop DP sensor: the reading holds at setpoint, the pump speed carries the bias
        Check("a DP sensor bias moves the secondary pump", _dp_bias_moves_the_pump),
        Finding("chw_pump_dp_reset", f"PLANT__{_SEC}_020", severity=("ok",)),
        Metric(
            "chw_pump_dp_reset",
            f"PLANT__{_SEC}_020",
            "median_speed_pct",
            74.4,
            0.05,
            on=REAL,
            quote="74.4%",
        ),
        Metric(
            "chw_pump_dp_reset",
            f"PLANT__{_SEC}_m020",
            "median_speed_pct",
            97.0,
            0.05,
            on=REAL,
            quote="97.0%",
        ),
        # 5. the label score counts the chiller-sensor alarms as false positives
        Score("chiller_efficiency", fpr=0.154, tol=0.001, on=REAL, quote="FPR 15%"),
        Score("cooling_tower_approach", fpr=0.0, quote="FPR 0%"),
    ),
    standin=standin,
)
