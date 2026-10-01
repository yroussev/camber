"""Answer key: workbook exercise ``air-heat-cool`` (docs/workbook/air-heat-cool.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-sdahu lbnl-ddahu irish-ahu
    camber datasets ingest lbnl-sdahu lbnl-ddahu irish-ahu --store lab_store
    camber datasets config lbnl-sdahu --store lab_store --out sdahu.json
    camber run sdahu.json --out sdahu_out
    camber datasets score lbnl-sdahu --store lab_store --findings sdahu_out/findings.json
    camber datasets config lbnl-ddahu --exercise air-heat-cool--lbnl-ddahu --store lab_store \
        --out hc_dd.json
    camber run hc_dd.json --out hc_dd_out
    camber datasets config irish-ahu --store lab_store --out irish.json
    camber run irish.json --out irish_out

(CAMBER 0.97.0-dev, the default subset of each dataset, 2026-09-29; the leak figures re-recorded
with CAMBER 0.98.0-dev, 2026-09-30.)

Until 0.97 ``leaking_valve`` did not fire on the published 10 % leak (``AHU__coi_leakage_010``):
with the valve commanded shut the leak takes about 1 F out of the air (the supply air sits 0.1 F
*below* the mixed air, against about 1 F above it on the fault-free unit), less than the rule's
3 F margin. Since 0.98 (#84) the ``lbnl-sdahu`` template credits the unit's own measured fan heat
(``measured_fan_heat_f`` 1.0 F, ``cool_delta_thr_f`` 1.0 F, occupied hours only), so a leak is
supply air below the mixed air, and the leak run is a fault. The 1.0 F was measured on the
fault-free run, which is also scored: the exercise asks the learner to notice that circularity.
"""

from __future__ import annotations

from _air_standins import ddahu, irish, sdahu
from _workbook import (
    BOTH,
    REAL,
    Check,
    Exercise,
    Finding,
    Metric,
    Run,
    Score,
    hourly_index,
    write_standin,
)

_LEAK_F = 1.1  # the leak's pull on the air: more than the fan's heat, less than its heat + 3 F


def standin(store) -> None:
    """ds-lbnl-sdahu (a fault-free unit and one whose shut cooling valve still takes 1.1 F out
    of the air, just more than the fan's 1.0 F), ds-lbnl-ddahu (a hot deck heating while the
    cold deck cools, by design) and ds-irish-ahu (two coils that never open together and do not
    leak)."""
    idx = hourly_index(days=28)
    write_standin(
        store,
        "lbnl-sdahu",
        {
            "AHU__fault_free": ("AHU", sdahu(idx)),
            "AHU__coi_leakage_010": ("AHU", sdahu(idx, leak_f=_LEAK_F)),
        },
        labels={"AHU__fault_free": "", "AHU__coi_leakage_010": "valve_leak"},
    )
    write_standin(store, "lbnl-ddahu", {"DDAHU__fault_free": ("AHU", ddahu(idx))})
    write_standin(store, "irish-ahu", {"AHU__ahu": ("AHU", irish(idx))})


def _leak_run_sits_colder(ctx) -> None:
    """With the valve shut, the leak run's supply air sits at least 0.8 F further below its mixed
    air than the fault-free unit's: the chilled water the valve still passes."""
    base = ctx.finding("leaking_valve", "AHU__fault_free")
    leak = ctx.finding("leaking_valve", "AHU__coi_leakage_010")
    assert base is not None and leak is not None, "no leaking_valve finding on the two runs"
    b, lk = base.metrics["chw_median_delta_f"], leak.metrics["chw_median_delta_f"]
    assert lk <= b - 0.8, f"leak {lk} F vs fault-free {b} F (expected at least 0.8 F lower)"


EXERCISE = Exercise(
    id="air-heat-cool",
    title="Heating and cooling at the air handler: a leaking valve, and coils that fight",
    issue=80,
    references=("pnnl-guide-ahu-heat-cool", "pnnl-retuning-ch5"),
    datasets=("lbnl-sdahu", "lbnl-ddahu", "irish-ahu"),
    runs=(
        Run(dataset="lbnl-sdahu"),
        Run(dataset="lbnl-ddahu", name="ddahu", config="air-heat-cool--lbnl-ddahu"),
        Run(dataset="irish-ahu", name="irish"),
    ),
    commands=(
        "camber datasets fetch lbnl-sdahu lbnl-ddahu irish-ahu",
        "camber datasets ingest lbnl-sdahu lbnl-ddahu irish-ahu --store lab_store",
        "camber datasets config lbnl-sdahu --store lab_store --out sdahu.json",
        "camber run sdahu.json --out sdahu_out",
        "camber datasets score lbnl-sdahu --store lab_store --findings sdahu_out/findings.json",
        "camber datasets config lbnl-ddahu --exercise air-heat-cool--lbnl-ddahu "
        "--store lab_store --out hc_dd.json",
        "camber run hc_dd.json --out hc_dd_out",
        "camber datasets config irish-ahu --store lab_store --out irish.json",
        "camber run irish.json --out irish_out",
    ),
    expect=(
        # lbnl-sdahu: the leak is below the default 3 F margin, caught against the unit's own
        # measured fan heat (the template's leaking_valve params, 0.98)
        Finding("leaking_valve", "AHU__coi_leakage_010", severity=("fault",)),
        Finding("leaking_valve", "AHU__fault_free", severity=("ok",)),
        Check("the leak run's supply air sits colder", _leak_run_sits_colder),
        Metric("leaking_valve", "AHU__coi_leakage_010", "cool_shift_f", 1.0, 0.0),
        Metric(
            "leaking_valve",
            "AHU__fault_free",
            "chw_median_delta_f",
            1.1,
            0.05,
            on=REAL,
            quote="+1.1 °F",
        ),
        Metric(
            "leaking_valve",
            "AHU__coi_leakage_010",
            "chw_median_delta_f",
            -0.1,
            0.05,
            on=REAL,
            quote="-0.1 °F",
        ),
        Metric(
            "leaking_valve",
            "AHU__coi_leakage_010",
            "chw_leak_pct",
            60.5,
            0.05,
            on=REAL,
            quote="60.5%",
        ),
        Metric(
            "leaking_valve",
            "AHU__fault_free",
            "chw_leak_pct",
            2.4,
            0.05,
            on=REAL,
            quote="2.4%",
        ),
        Score("leaking_valve", tpr=1.0, fpr=0.0, quote="TPR 100%", on=BOTH),
        # lbnl-ddahu: both valves open by design
        Finding("simultaneous_heat_cool", "DDAHU__fault_free", severity=("fault",), run="ddahu"),
        Metric(
            "simultaneous_heat_cool",
            "DDAHU__fault_free",
            "simultaneous_hc_pct",
            27.2,
            0.1,
            run="ddahu",
            on=REAL,
            quote="27%",
        ),
        # irish-ahu: a real unit whose coils never open together, and no leak signature
        Finding("simultaneous_heat_cool", "AHU__ahu", severity=("ok",), run="irish"),
        Metric("simultaneous_heat_cool", "AHU__ahu", "simultaneous_hc_pct", 0.0, 0.0, run="irish"),
        Finding("leaking_valve", "AHU__ahu", severity=("ok",), run="irish"),
        Metric(
            "leaking_valve",
            "AHU__ahu",
            "hw_leak_pct",
            9.8,
            0.05,
            run="irish",
            on=REAL,
            quote="9.8%",
        ),
    ),
    standin=standin,
)
