"""Answer key: workbook exercise ``plant-boiler`` (docs/workbook/plant-boiler.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-boiler
    camber datasets ingest lbnl-boiler --subset full --store lab_store
    camber datasets config lbnl-boiler --exercise plant-boiler --store lab_store --out boil.json
    camber run boil.json --out boil_out
    camber datasets score lbnl-boiler --store lab_store --findings boil_out/findings.json

(CAMBER 0.97.0-dev, lbnl-boiler full subset, all 17 runs, 2026-09-29; the boiler efficiency drift
figures recorded with CAMBER 0.98.0-dev, 2026-09-30.)
"""

from __future__ import annotations

from _plant_standins import boiler_standin
from _workbook import BOTH, REAL, Check, Exercise, Finding, Metric, Run, Score

_RUNS = (
    "fault_free",
    "boiler_foul_065",
    "hot_water_pressure_bias_20",
    "hot_water_pressure_bias_m20",
)
#: the rules of the exercise config that need a boiler run status this plant does not have
_NEED_STATUS = ("boiler_summer_lockout", "boiler_short_cycle", "hw_plant_deltat")


def standin(store) -> None:
    """ds-lbnl-boiler with the healthy plant, the worst boiler fouling and the +/-20 % loop DP
    sensor biases (see _plant_standins.py)."""
    boiler_standin(store, _RUNS)


def _status_rules_silent(ctx) -> None:
    """Without a boiler run status the lockout, short-cycle and plant delta-T rules produce no
    finding at all, on any run."""
    for f in ctx.findings():
        assert f.rule not in _NEED_STATUS, f"{f.rule} ran on {f.equip}: {f.severity}"


def _pump_never_stops_no_reset(ctx) -> None:
    """Pump 1 runs in every hour of the window (winter and summer alike) against a flat DP
    setpoint, and the rule still says ok."""
    f = ctx.finding("hw_pump_dp_reset", "PLANT__fault_free")
    assert f is not None, "no hw_pump_dp_reset finding on PLANT__fault_free"
    hours = len(ctx.result().frame_for("PLANT__fault_free").index)
    assert f.metrics["n_running"] >= hours - 1, f"pump ran {f.metrics['n_running']} of {hours} h"
    assert f.metrics["dp_sp_reset_present"] is False, "a DP-setpoint reset was found"
    assert f.metrics["pct_running_near_min"] == 0.0, "hours near the VFD minimum"


def _fouling_invisible(ctx) -> None:
    """No point-in-time rule in this config fires on the worst boiler fouling (only the boiler
    drift family's boiler_efficiency_drift, scored against the declared reference, does)."""
    # 0.98 (#86 items 1 and 5, 098-plant-reference) begin
    fired = [
        f
        for f in ctx.findings()
        if f.equip == "PLANT__boiler_foul_065" and f.rule != "boiler_efficiency_drift"
    ]
    # 0.98 (#86 items 1 and 5, 098-plant-reference) end
    fired = [f"{f.rule}={f.severity}" for f in fired if f.severity in ("warn", "fault")]
    assert not fired, f"fired on PLANT__boiler_foul_065: {fired}"


def _dp_bias_moves_the_pump(ctx) -> None:
    """A DP sensor reading high slows the pump, one reading low speeds it up."""
    speed = {}
    for run in ("fault_free", "hot_water_pressure_bias_20", "hot_water_pressure_bias_m20"):
        f = ctx.finding("hw_pump_dp_reset", f"PLANT__{run}")
        assert f is not None, f"no hw_pump_dp_reset finding on PLANT__{run}"
        speed[run] = f.metrics["median_speed_pct"]
    lo, mid, hi = (
        speed["hot_water_pressure_bias_20"],
        speed["fault_free"],
        speed["hot_water_pressure_bias_m20"],
    )
    assert lo < mid < hi, f"median pump speeds +20 %: {lo}, fault-free: {mid}, -20 %: {hi}"


# 0.98 (#86 items 1 and 5, 098-plant-reference) begin
def _reference_declines(ctx) -> None:
    """The declared reference, PLANT__fault_free, declines (``is_reference``); every other run's
    boiler_efficiency_drift finding is scored against it."""
    f = ctx.finding("boiler_efficiency_drift", "PLANT__fault_free")
    assert f is not None, "no boiler_efficiency_drift finding on PLANT__fault_free"
    assert f.severity == "info", f"the reference is {f.severity}, not an info decline"
    assert f.metrics.get("reason") == "is_reference", f"decline reason {f.metrics.get('reason')!r}"
    for g in ctx.findings():
        if g.rule == "boiler_efficiency_drift" and g.equip != "PLANT__fault_free":
            src = g.metrics.get("baseline_source")
            assert src == "reference:PLANT__fault_free", f"{g.equip}: baseline_source {src!r}"


def _temp_bias_is_metering(ctx) -> None:
    """The +2 / +4 F hot-water temperature biases raise the gas-per-heat ratio too, but the gas
    at matched outdoor temperature did not rise: a heat-metering problem (info), not fouling."""
    for run in ("hot_water_temp_bias_2", "hot_water_temp_bias_4"):
        f = ctx.finding("boiler_efficiency_drift", f"PLANT__{run}")
        assert f is not None, f"no boiler_efficiency_drift finding on PLANT__{run}"
        assert f.severity == "info", f"PLANT__{run}: {f.severity}"
        assert f.metrics.get("attribution") == "heat_metering", f"PLANT__{run}: {f.metrics}"


# 0.98 (#86 items 1 and 5, 098-plant-reference) end

EXERCISE = Exercise(
    id="plant-boiler",
    title="Boiler plant: what an enable point hides, pumping and a fouling gap",
    issue=82,
    references=("pnnl-guide-plant-heating", "pnnl-retuning-ch8"),
    datasets=("lbnl-boiler",),
    runs=(Run(dataset="lbnl-boiler", config="plant-boiler", subset="full"),),
    commands=(
        "camber datasets fetch lbnl-boiler",
        "camber datasets ingest lbnl-boiler --subset full --store lab_store",
        "camber datasets config lbnl-boiler --exercise plant-boiler --store lab_store "
        "--out boil.json",
        "camber run boil.json --out boil_out",
        "camber datasets score lbnl-boiler --store lab_store --findings boil_out/findings.json",
    ),
    expect=(
        # 1. the three rules that need a firing status stay silent
        Check("no lockout, short-cycle or delta-T finding", _status_rules_silent),
        Finding("boiler_summer_lockout", "PLANT__fault_free", severity=("absent",)),
        # 2-3. the pump: ok, yet it never stops and its DP setpoint is never reset
        Finding("hw_pump_dp_reset", "PLANT__fault_free", severity=("ok",)),
        Check("the pump runs every hour against a flat DP setpoint", _pump_never_stops_no_reset),
        Metric(
            "hw_pump_dp_reset",
            "PLANT__fault_free",
            "median_speed_pct",
            29.0,
            0.05,
            on=REAL,
            quote="29.0%",
        ),
        Metric(
            "hw_pump_dp_reset",
            "PLANT__fault_free",
            "n_running",
            8759,
            0,
            on=REAL,
            quote="8,759",
        ),
        # 4. boiler fouling is invisible to these rules
        Check("no point-in-time rule fires on the worst boiler fouling", _fouling_invisible),
        # 0.98 (#86 items 1 and 5, 098-plant-reference) begin
        # ... but the boiler drift family, scored against the declared reference, does
        Check("the declared reference declines and scores the others", _reference_declines),
        Finding("boiler_efficiency_drift", "PLANT__boiler_foul_065", severity=("fault",)),
        Finding("boiler_efficiency_drift", "PLANT__hot_water_pressure_bias_20", severity=("ok",)),
        Metric(
            "boiler_efficiency_drift",
            "PLANT__boiler_foul_065",
            "ratio_drift_rel",
            0.537,
            0.001,
            on=REAL,
            quote="+53.7%",
        ),
        Metric(
            "boiler_efficiency_drift",
            "PLANT__boiler_foul_080",
            "ratio_drift_rel",
            0.249,
            0.001,
            on=REAL,
            quote="+24.9%",
        ),
        Finding("boiler_efficiency_drift", "PLANT__boiler_foul_095", severity=("warn",), on=REAL),
        Metric(
            "boiler_efficiency_drift",
            "PLANT__boiler_foul_095",
            "ratio_drift_rel",
            0.051,
            0.001,
            on=REAL,
            quote="+5.1%",
        ),
        Check(
            "a hot-water temperature bias is a metering problem", _temp_bias_is_metering, on=REAL
        ),
        Score("boiler_efficiency_drift", tpr=1.0, fpr=0.0, on=BOTH, quote="TPR 100%"),
        # 0.98 (#86 items 1 and 5, 098-plant-reference) end
        # 5. the loop DP sensor biases show in the pump speed, not in the DP reading
        Check("a DP sensor bias moves the pump speed", _dp_bias_moves_the_pump),
        Finding("hw_pump_dp_reset", "PLANT__hot_water_pressure_bias_20", severity=("ok",)),
        Metric(
            "hw_pump_dp_reset",
            "PLANT__hot_water_pressure_bias_20",
            "median_speed_pct",
            26.4,
            0.05,
            on=REAL,
            quote="26.4%",
        ),
        Metric(
            "hw_pump_dp_reset",
            "PLANT__hot_water_pressure_bias_m20",
            "median_speed_pct",
            32.4,
            0.05,
            on=REAL,
            quote="32.4%",
        ),
    ),
    standin=standin,
)
