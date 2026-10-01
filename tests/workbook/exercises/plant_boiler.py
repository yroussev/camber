"""Answer key: workbook exercise ``plant-boiler`` (docs/workbook/plant-boiler.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-boiler
    camber datasets ingest lbnl-boiler --subset full --store lab_store
    camber datasets config lbnl-boiler --exercise plant-boiler --store lab_store --out boil.json
    camber run boil.json --out boil_out
    camber datasets score lbnl-boiler --store lab_store --findings boil_out/findings.json

(CAMBER 0.97.0-dev, lbnl-boiler full subset, all 17 runs, 2026-09-29; the boiler firing-rule
figures re-recorded with CAMBER 0.98.0-dev, 2026-09-30.)
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
# 0.98 (#86 item 4a, 098-plant-boiler) begin
#: the rules of the exercise config that need to know when the boiler fired: this plant has no
#: boiler run status, so since 0.98 they read firing from boiler 1's gas input instead
_NEED_STATUS = ("boiler_summer_lockout", "boiler_short_cycle", "hw_plant_deltat")
# 0.98 (#86 item 4a, 098-plant-boiler) end


def standin(store) -> None:
    """ds-lbnl-boiler with the healthy plant, the worst boiler fouling and the +/-20 % loop DP
    sensor biases (see _plant_standins.py)."""
    boiler_standin(store, _RUNS)


# 0.98 (#86 item 4a, 098-plant-boiler) begin
def _status_rules_from_gas(ctx) -> None:
    """With no boiler run status the lockout, short-cycle and plant delta-T rules read firing
    from the gas input: each gives one finding per run, every one ok, with ``run_source`` gas
    and the caveat that says so."""
    equips = {f.equip for f in ctx.findings() if f.rule == "hw_pump_dp_reset"}
    assert equips, "no hw_pump_dp_reset findings"
    for rule in _NEED_STATUS:
        got = {f.equip: f for f in ctx.findings() if f.rule == rule}
        assert set(got) == equips, f"{rule} missing on {sorted(equips - set(got))}"
        for equip, f in got.items():
            assert f.severity == "ok", f"{rule} on {equip}: {f.severity}"
            assert f.metrics.get("run_source") == "gas", f"{rule} on {equip}: {f.metrics}"
            assert any("gas input" in c for c in f.caveats), f"{rule} on {equip}: {f.caveats}"


# 0.98 (#86 item 4a, 098-plant-boiler) end


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
    """Nothing in this config fires on the worst boiler fouling."""
    fired = [f for f in ctx.findings() if f.equip == "PLANT__boiler_foul_065"]
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
        # 0.98 (#86 item 4a, 098-plant-boiler) begin
        # 1-2. the three rules that need a firing status read it from the gas input: all ok
        Check("lockout, short-cycle and delta-T read firing from the gas", _status_rules_from_gas),
        Finding("boiler_summer_lockout", "PLANT__fault_free", severity=("ok",)),
        Finding("boiler_short_cycle", "PLANT__fault_free", severity=("ok",)),
        Finding("hw_plant_deltat", "PLANT__fault_free", severity=("ok",)),
        Metric(
            "boiler_summer_lockout",
            "PLANT__fault_free",
            "summer_run_pct",
            0.0,
            0.0,
            quote="0% of its firing hours",
        ),
        Metric(
            "boiler_summer_lockout",
            "PLANT__boiler_PI",
            "summer_run_pct",
            0.35,
            0.05,
            on=REAL,
            quote="0.35%",
        ),
        Metric(
            "boiler_short_cycle",
            "PLANT__fault_free",
            "starts_per_day",
            0.92,
            0.01,
            on=REAL,
            quote="0.92 starts a day",
        ),
        Metric(
            "boiler_short_cycle",
            "PLANT__fault_free",
            "runtime_pct",
            37.8,
            0.05,
            on=REAL,
            quote="37.8%",
        ),
        Metric(
            "hw_plant_deltat",
            "PLANT__fault_free",
            "deltaT_median_f",
            36.0,
            0.05,
            on=REAL,
            quote="36.0 °F",
        ),
        Metric(
            "hw_plant_deltat",
            "PLANT__boiler_foul_065",
            "low_deltaT_pct",
            12.2,
            0.05,
            on=REAL,
            quote="12.2%",
        ),
        # 0.98 (#86 item 4a, 098-plant-boiler) end
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
        Check("nothing fires on the worst boiler fouling", _fouling_invisible),
        Score(None, tpr=0.0, on=BOTH, quote="TPR 0%"),
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
