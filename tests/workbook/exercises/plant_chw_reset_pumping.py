"""Answer key: workbook exercise ``plant-chw-reset-pumping`` (docs/workbook/plant-chw-reset-pumping.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-chiller
    camber datasets ingest lbnl-chiller --store lab_store
    camber datasets config lbnl-chiller --exercise plant-chw-reset-pumping --store lab_store --out chw.json
    camber run chw.json --out chw_out

(CAMBER 0.97.0-dev, lbnl-chiller default subset, 2026-09-29; the four runs it holds read the same
in the full subset. Re-recorded for 0.98.0-dev on 2026-09-30 after #86 items 2 and 3: the reset's
sign is checked, a constant-flow plant's delta-T is not judged, and the pump's VFD floor is
learned.)
Since 0.105 (#164) the secondary pumps are their own equipment, so the chw_pump_dp_reset
figures are read on SECPUMP__<run> instead of PLANT__<run>; the values did not change.
Re-recorded for 0.105.0-dev on 2026-10-16 after #180: chw_pump_dp_reset judges only the hours the
pumps run (pump_status, derived from pump 1's kW). The 34.5 % "VFD floor" was the speed point
parked at the stopped DP loop's last output; on running hours the pump has no floor plateau
(near_min_source: default), rides near full speed more (fault-free median 84.9 -> 91.8 %, near
full 38.0 -> 56.5 %), and the chiller-bias run, which had read as idling at the floor, is ok.
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
    slope on the dry-bulb, the direction the rule expects)."""
    f = ctx.finding("chw_plant_reset", "PLANT__fault_free")
    assert f is not None, "no chw_plant_reset finding on PLANT__fault_free"
    assert f.metrics["chwst_reset_present"] is True, "CHWST reset not found"
    assert f.metrics["chwst_reset_direction"] == "expected", f.metrics["chwst_reset_direction"]
    assert f.metrics["chwst_slope_per_F"] < -0.1, f"slope {f.metrics['chwst_slope_per_F']}"


def _constant_flow(ctx) -> None:
    """Chiller 1's primary flow does not follow the load: the rule recognises a constant-flow
    plant from the flow point and reports its delta-T without judging it."""
    f = ctx.finding("chw_plant_reset", "PLANT__fault_free")
    assert f is not None, "no chw_plant_reset finding on PLANT__fault_free"
    assert f.metrics["flow_mode"] == "constant", f.metrics["flow_mode"]
    assert f.metrics["flow_cv"] is not None and f.metrics["flow_cv"] <= 0.05, f.metrics["flow_cv"]
    assert any("constant primary flow" in c for c in f.caveats), f.caveats


def _no_dp_reset_running_hours(ctx) -> None:
    """The DP setpoint is flat (no reset) on every run, and the rule judges only the hours the
    pumps run (pump_status): the speed parked while they are stopped is left out, and on the
    fault-free run the running speeds show no floor plateau, so no VFD floor is learned and the
    near-minimum band falls back to 25 % with no hours in it (0.105, #180)."""
    for f in ctx.findings():
        if f.rule != "chw_pump_dp_reset":
            continue
        assert f.metrics["dp_sp_reset_present"] is False, f"{f.equip}: DP reset found"
        assert f.metrics["run_source"] == "status", f"{f.equip}: {f.metrics['run_source']}"
    f = ctx.finding("chw_pump_dp_reset", "SECPUMP__fault_free")
    assert f is not None, "no chw_pump_dp_reset finding on SECPUMP__fault_free"
    assert f.metrics["n_stopped_speed_above_run"] > 0, "no parked speed while stopped"
    assert f.metrics["near_min_source"] == "default", f.metrics["near_min_source"]
    assert f.metrics["vfd_floor_pct"] is None, f.metrics["vfd_floor_pct"]
    assert f.metrics["near_min_band_pct"] == 25.0, f.metrics["near_min_band_pct"]
    assert f.metrics["pct_running_near_min"] == 0.0, f.metrics["pct_running_near_min"]
    assert any("parked speed" in c for c in f.caveats), f.caveats


def _bypass_reverse_reset(ctx) -> None:
    """With the tower bypass stuck, the supply warms as the weather warms: the rule reads the
    reverse of a reset, not a working one."""
    f = ctx.finding("chw_plant_reset", "PLANT__bypass_stuck_075")
    assert f is not None, "no chw_plant_reset finding on PLANT__bypass_stuck_075"
    assert f.metrics["chwst_slope_per_F"] > 0.1, f"slope {f.metrics['chwst_slope_per_F']}"
    assert f.metrics["chwst_reset_direction"] == "reverse", f.metrics["chwst_reset_direction"]
    assert f.metrics["chwst_reset_present"] is False


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
        # 2. the loop delta-T is low, but by design: a constant-flow primary loop (0.98, #86;
        # until 0.97 the rule faulted the healthy plant on it)
        Finding("chw_plant_reset", "PLANT__fault_free", severity=("ok",)),
        Check("the rule recognises a constant-flow plant", _constant_flow),
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
        Finding("chw_pump_dp_reset", "SECPUMP__fault_free", severity=("warn",)),
        Check(
            "no DP reset; judged on running hours, no VFD floor is learned",
            _no_dp_reset_running_hours,
        ),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__fault_free",
            "pct_running_near_full",
            56.5,
            0.1,
            on=REAL,
            quote="56.5%",
        ),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__fault_free",
            "median_speed_pct",
            91.8,
            0.1,
            on=REAL,
            quote="91.8%",
        ),
        # 4. the VFD minimum: the speed point parks at the stopped DP loop's output (34.5 %) while
        # the pumps are off; judged on running hours (0.105, #180) there is no floor plateau
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__fault_free",
            "n_stopped_speed_above_run",
            2869,
            0,
            on=REAL,
            quote="2,869",
        ),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__fault_free",
            "pct_running_near_min",
            0.0,
            0.05,
            quote="0.0%",
        ),
        # ... and the chiller-bias run, which read as idling at that "floor", runs slower but ok
        Finding("chw_pump_dp_reset", "SECPUMP__chiller_bias_2", severity=("ok",)),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__chiller_bias_2",
            "median_speed_pct",
            70.3,
            0.1,
            on=REAL,
            quote="70.3%",
        ),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__chiller_bias_2",
            "pct_running_near_full",
            8.7,
            0.1,
            on=REAL,
            quote="8.7%",
        ),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__chiller_bias_2",
            "n_stopped_speed_above_run",
            6210,
            0,
            on=REAL,
            quote="6,210",
        ),
        # 5. a stuck tower bypass: the plant loses its chilled water and the pump runs flat out
        Check("the bypassed plant cannot hold its CHWST", _bypass_cannot_hold_chwst),
        Finding("chw_plant_reset", "PLANT__bypass_stuck_075", severity=("warn",)),
        Check("the bypassed plant's supply warms with the weather", _bypass_reverse_reset),
        Metric(
            "chw_plant_reset",
            "PLANT__bypass_stuck_075",
            "chwst_slope_per_F",
            0.757,
            0.005,
            on=REAL,
            quote="+0.76 °F",
        ),
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
        Finding("chw_pump_dp_reset", "SECPUMP__bypass_stuck_075", severity=("fault",)),
        Metric(
            "chw_pump_dp_reset",
            "SECPUMP__bypass_stuck_075",
            "median_speed_pct",
            100.0,
            0.05,
            on=REAL,
            quote="100%",
        ),
    ),
    standin=standin,
)
