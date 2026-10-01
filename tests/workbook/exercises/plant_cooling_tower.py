"""Answer key: workbook exercise ``plant-cooling-tower`` (docs/workbook/plant-cooling-tower.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-chiller
    camber datasets ingest lbnl-chiller --subset full --store lab_store
    camber datasets config lbnl-chiller --exercise plant-cooling-tower --store lab_store --out tower.json
    camber run tower.json --out tower_out
    camber datasets score lbnl-chiller --store lab_store --findings tower_out/findings.json

(CAMBER 0.97.0-dev, lbnl-chiller full subset, all 24 runs, 2026-09-29; the fan-effort drift
figures recorded with CAMBER 0.98.0-dev, 2026-09-30.)
"""  # noqa: E501

from __future__ import annotations

from _plant_standins import chiller_standin
from _workbook import BOTH, REAL, STANDIN, Check, Exercise, Finding, Metric, Run, Score

_RUNS = ("fault_free", "coolingtower_fouling_065", "bypass_stuck_075", "chiller_fouling_065")


def standin(store) -> None:
    """ds-lbnl-chiller with the healthy plant, the worst tower fouling, a stuck tower bypass and
    (a fault that should leave the tower alone) the worst chiller fouling."""
    chiller_standin(store, _RUNS)


def _fouled_tower_works_harder(ctx) -> None:
    """The fouled tower spends at least twice the fault-free tower's hours at or above 90 % fan
    (the approach rule's judged hours, ``n_operating``), for a small rise in approach."""
    base = ctx.finding("cooling_tower_approach", "PLANT__fault_free")
    foul = ctx.finding("cooling_tower_approach", "PLANT__coolingtower_fouling_065")
    assert base is not None and foul is not None, "no cooling_tower_approach finding"
    nb, nf = base.metrics["n_operating"], foul.metrics["n_operating"]
    assert nf >= 2 * nb, f"fouled tower at high fan {nf} h vs fault-free {nb} h (expected >= 2x)"
    ab, af = base.metrics["approach_median_f"], foul.metrics["approach_median_f"]
    assert 0 < af - ab < 3.0, f"approach {ab} -> {af} F: expected a small rise"
    hb, hf = base.metrics["pct_hours_high_approach"], foul.metrics["pct_hours_high_approach"]
    assert hf >= hb + 10.0, f"high-approach share {hb}% -> {hf}% (expected +10 points or more)"


def _bypass_is_the_valve(ctx) -> None:
    f = ctx.finding("condenser_bypass_leak", "PLANT__bypass_stuck_075")
    assert f is not None, "no condenser_bypass_leak finding on PLANT__bypass_stuck_075"
    assert f.metrics["attribution"] == "valve", f"attribution {f.metrics['attribution']!r}"


# 0.98 (#86 items 1 and 5, 098-plant-reference) begin
def _reference_declines(ctx) -> None:
    """The declared reference, PLANT__fault_free, is the yardstick: its fan-effort finding is an
    info decline (``is_reference``), and every other run is scored against it."""
    f = ctx.finding("cooling_tower_fan_effort_drift", "PLANT__fault_free")
    assert f is not None, "no cooling_tower_fan_effort_drift finding on PLANT__fault_free"
    assert f.severity == "info", f"the reference is {f.severity}, not an info decline"
    assert f.metrics.get("reason") == "is_reference", f"decline reason {f.metrics.get('reason')!r}"
    for g in ctx.findings():
        if g.rule == "cooling_tower_fan_effort_drift" and g.equip != "PLANT__fault_free":
            src = g.metrics.get("baseline_source")
            assert src == "reference:PLANT__fault_free", f"{g.equip}: baseline_source {src!r}"


def _bias_is_a_sensor(ctx) -> None:
    """The +1 / +2 F tower-sensor biases push the fans harder too, but the leaving-water sensor
    moved with them: the rule reports a sensor offset (info), not fouling."""
    for run in ("coolingtower_bias_1", "coolingtower_bias_2"):
        f = ctx.finding("cooling_tower_fan_effort_drift", f"PLANT__{run}")
        assert f is not None, f"no cooling_tower_fan_effort_drift finding on PLANT__{run}"
        assert f.severity == "info", f"PLANT__{run}: {f.severity}"
        assert f.metrics.get("attribution") == "sensor_offset", f"PLANT__{run}: {f.metrics}"


# 0.98 (#86 items 1 and 5, 098-plant-reference) end

EXERCISE = Exercise(
    id="plant-cooling-tower",
    title="Cooling tower: approach, fan effort and a leaking tower bypass",
    issue=82,
    references=("pnnl-guide-plant-cooling", "pnnl-retuning-ch8"),
    datasets=("lbnl-chiller",),
    runs=(Run(dataset="lbnl-chiller", config="plant-cooling-tower", subset="full"),),
    commands=(
        "camber datasets fetch lbnl-chiller",
        "camber datasets ingest lbnl-chiller --subset full --store lab_store",
        "camber datasets config lbnl-chiller --exercise plant-cooling-tower --store lab_store "
        "--out tower.json",
        "camber run tower.json --out tower_out",
        "camber datasets score lbnl-chiller --store lab_store --findings tower_out/findings.json",
    ),
    expect=(
        # 1. the healthy tower sits at its calibrated approach at high fan
        Finding("cooling_tower_approach", "PLANT__fault_free", severity=("ok",)),
        Metric(
            "cooling_tower_approach",
            "PLANT__fault_free",
            "approach_median_f",
            8.1,
            0.05,
            on=REAL,
            quote="8.1 °F",
        ),
        # 2. the 65 % fouled tower is NOT flagged: its approach barely moves ...
        Finding("cooling_tower_approach", "PLANT__coolingtower_fouling_065", severity=("ok",)),
        Metric(
            "cooling_tower_approach",
            "PLANT__coolingtower_fouling_065",
            "approach_median_f",
            9.4,
            0.05,
            on=REAL,
            quote="9.4 °F",
        ),
        Metric(
            "cooling_tower_approach",
            "PLANT__coolingtower_fouling_065",
            "pct_hours_high_approach",
            20.4,
            0.1,
            on=REAL,
            quote="20.4%",
        ),
        Metric(
            "cooling_tower_approach",
            "PLANT__fault_free",
            "pct_hours_high_approach",
            0.2,
            0.05,
            on=REAL,
            quote="0.2%",
        ),
        # 3. ... because it works its fan: many more hours at >= 90 % fan
        Check("the fouled tower works its fan harder", _fouled_tower_works_harder),
        Metric(
            "cooling_tower_approach",
            "PLANT__coolingtower_fouling_065",
            "n_operating",
            1617,
            0,
            on=REAL,
            quote="1,617",
        ),
        Metric(
            "cooling_tower_approach",
            "PLANT__fault_free",
            "n_operating",
            483,
            0,
            on=REAL,
            quote="483",
        ),
        # 4. the condenser-water supply follows the wet-bulb
        Finding("condenser_water_reset", "PLANT__fault_free", severity=("ok",)),
        Metric(
            "condenser_water_reset",
            "PLANT__fault_free",
            "cws_slope_per_wetbulb",
            0.931,
            0.005,
            on=REAL,
            quote="0.93",
        ),
        # 5. the stuck bypass: water skips the tower, which then never works hard
        Finding("condenser_bypass_leak", "PLANT__bypass_stuck_075", severity=("fault",)),
        Check("the bypass finding blames the valve", _bypass_is_the_valve),
        Finding("condenser_bypass_leak", "PLANT__fault_free", severity=("ok",)),
        Finding("condenser_bypass_leak", "PLANT__chiller_fouling_065", severity=("ok",)),
        Finding("cooling_tower_approach", "PLANT__bypass_stuck_075", severity=("info",)),
        Metric(
            "condenser_bypass_leak",
            "PLANT__bypass_stuck_075",
            "median_diff_f",
            62.19,
            0.05,
            on=REAL,
            quote="+62.2 °F",
        ),
        # 6. the label score: the approach rule finds none of its four targets
        Score("cooling_tower_approach", tpr=0.0, fpr=0.0, on=BOTH, quote="TPR 0%"),
        # 0.98 (#86 items 1 and 5, 098-plant-reference) begin
        # 6. the fan-effort drift rule, scored against the declared reference, finds the fouling
        Check("the declared reference declines and scores the others", _reference_declines),
        Finding(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_065",
            severity=("warn", "fault"),
        ),
        Finding("cooling_tower_fan_effort_drift", "PLANT__chiller_fouling_065", severity=("ok",)),
        Finding(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_065",
            severity=("fault",),
            on=REAL,
        ),
        Metric(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_065",
            "fan_effort_drift_pct",
            17.8,
            0.05,
            on=REAL,
            quote="+17.8",
        ),
        Metric(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_080",
            "fan_effort_drift_pct",
            10.9,
            0.05,
            on=REAL,
            quote="+10.9",
        ),
        Finding(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_095",
            severity=("ok",),
            on=REAL,
        ),
        Metric(
            "cooling_tower_fan_effort_drift",
            "PLANT__coolingtower_fouling_095",
            "fan_effort_drift_pct",
            3.33,
            0.05,
            on=REAL,
            quote="+3.3",
        ),
        Check("a tower-sensor bias is a sensor offset", _bias_is_a_sensor, on=REAL),
        Score("cooling_tower_fan_effort_drift", tpr=1.0, fpr=0.0, on=STANDIN),
        Score(
            "cooling_tower_fan_effort_drift",
            tpr=0.667,
            fpr=0.0,
            tol=0.001,
            on=REAL,
            quote="TPR 67%",
        ),
        # 0.98 (#86 items 1 and 5, 098-plant-reference) end
    ),
    standin=standin,
)
