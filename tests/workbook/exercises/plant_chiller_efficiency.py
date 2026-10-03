"""Answer key: workbook exercise ``plant-chiller-efficiency`` (docs/workbook/plant-chiller-efficiency.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-chiller
    camber datasets ingest lbnl-chiller --subset full --store lab_store
    camber datasets config lbnl-chiller --exercise plant-chiller-efficiency--generic --store lab_store --out generic.json
    camber run generic.json --out generic_out
    camber datasets score lbnl-chiller --store lab_store --findings generic_out/findings.json
    camber datasets config lbnl-chiller --store lab_store --out chiller.json
    camber run chiller.json --out chiller_out
    camber datasets score lbnl-chiller --store lab_store --findings chiller_out/findings.json

(CAMBER 0.97.0-dev, lbnl-chiller full subset, all 24 runs, 2026-09-29.)
"""  # noqa: E501

from __future__ import annotations

from _plant_standins import chiller_standin
from _workbook import BOTH, REAL, Exercise, Finding, Metric, Run, Score

_RUNS = (
    "fault_free",
    "chiller_fouling_065",
    "chiller_fouling_095",
    "coolingtower_fouling_065",
    "bypass_stuck_075",
    "chiller_bias_2",
    "chiller_bias_m2",
)


def standin(store) -> None:
    """ds-lbnl-chiller with the healthy plant, both chiller foulings, the worst tower fouling, a
    stuck tower bypass and the two +/-2 C chiller-sensor biases (see _plant_standins.py)."""
    chiller_standin(store, _RUNS)


EXERCISE = Exercise(
    id="plant-chiller-efficiency",
    title="Chiller efficiency: calibrating a design ceiling, and chiller fouling",
    issue=82,
    references=("pnnl-guide-plant-cooling", "pnnl-retuning-ch8"),
    datasets=("lbnl-chiller",),
    runs=(
        Run(dataset="lbnl-chiller", subset="full"),
        Run(
            dataset="lbnl-chiller",
            name="generic",
            config="plant-chiller-efficiency--generic",
            subset="full",
        ),
    ),
    commands=(
        "camber datasets fetch lbnl-chiller",
        "camber datasets ingest lbnl-chiller --subset full --store lab_store",
        "camber datasets config lbnl-chiller --exercise plant-chiller-efficiency--generic "
        "--store lab_store --out generic.json",
        "camber run generic.json --out generic_out",
        "camber datasets score lbnl-chiller --store lab_store --findings generic_out/findings.json",
        "camber datasets config lbnl-chiller --store lab_store --out chiller.json",
        "camber run chiller.json --out chiller_out",
        "camber datasets score lbnl-chiller --store lab_store --findings chiller_out/findings.json",
    ),
    expect=(
        # 1. the rule's generic 0.85 kW/ton ceiling calls the healthy plant a fault ...
        Finding("chiller_efficiency", "PLANT__fault_free", severity=("fault",), run="generic"),
        Score("chiller_efficiency", tpr=1.0, run="generic", on=BOTH, quote="TPR 100%"),
        Score("chiller_efficiency", fpr=0.846, tol=0.001, run="generic", on=REAL, quote="FPR 85%"),
        # 2. ... and the ceiling calibrated from the fault-free run (1.44) does not
        Finding("chiller_efficiency", "PLANT__fault_free", present=False),
        Metric(
            "chiller_efficiency",
            "PLANT__fault_free",
            "kw_per_ton_median",
            1.44,
            0.01,
            quote="1.44 kW/ton",
        ),
        # 3. chiller fouling: the severe run is a fault, the mild one passes
        Finding("chiller_efficiency", "PLANT__chiller_fouling_065", severity=("fault",)),
        Finding("chiller_efficiency", "PLANT__chiller_fouling_095", severity=("ok",)),
        Metric(
            "chiller_efficiency",
            "PLANT__chiller_fouling_065",
            "kw_per_ton_median",
            2.364,
            0.005,
            on=REAL,
            quote="2.36",
        ),
        Metric(
            "chiller_efficiency",
            "PLANT__chiller_fouling_095",
            "kw_per_ton_median",
            1.527,
            0.005,
            on=REAL,
            quote="1.53",
        ),
        Metric(
            "chiller_efficiency",
            "PLANT__chiller_fouling_095",
            "pct_hours_inefficient",
            33.3,
            0.1,
            on=REAL,
            quote="33.3%",
        ),
        Metric(
            "chiller_efficiency",
            "PLANT__fault_free",
            "pct_hours_inefficient",
            17.7,
            0.1,
            on=REAL,
            quote="17.7%",
        ),
        # 4. the stuck bypass is a fault; the fouled tower barely moves the chiller's kW/ton
        Finding("chiller_efficiency", "PLANT__bypass_stuck_075", severity=("fault",)),
        Metric(
            "chiller_efficiency",
            "PLANT__bypass_stuck_075",
            "kw_per_ton_median",
            3.017,
            0.005,
            on=REAL,
            quote="3.02",
        ),
        Finding("chiller_efficiency", "PLANT__coolingtower_fouling_065", severity=("ok",)),
        Metric(
            "chiller_efficiency",
            "PLANT__coolingtower_fouling_065",
            "kw_per_ton_median",
            1.47,
            0.005,
            on=REAL,
            quote="1.47",
        ),
        # 5. the label score; the chiller-sensor bias is the false alarm
        Finding("chiller_efficiency", "PLANT__chiller_bias_2", severity=("fault",)),
        Score("chiller_efficiency", tpr=0.545, tol=0.001, on=REAL, quote="TPR 55%"),
        Score("chiller_efficiency", fpr=0.154, tol=0.001, on=REAL, quote="FPR 15%"),
    ),
    standin=standin,
)
