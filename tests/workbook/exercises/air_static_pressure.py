"""Answer key: workbook exercise ``air-static-pressure`` (docs/workbook/air-static-pressure.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-sdahu ornl-frp-vav
    camber datasets ingest lbnl-sdahu ornl-frp-vav --store lab_store
    camber datasets config lbnl-sdahu --exercise air-static-pressure --store lab_store \
        --out sp.json
    camber run sp.json --out sp_out
    camber datasets config ornl-frp-vav --exercise air-static-pressure--ornl-frp-vav \
        --store lab_store --out census.json
    camber run census.json --out census_out

(CAMBER 0.97.0-dev, the default subset of each dataset, 2026-09-29.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _air_standins import SDAHU_STATIC_SP, sdahu
from _workbook import REAL, Exercise, Finding, Metric, Run, hourly_index, write_standin

from camber.model.roles import Role

_ROOMS = (102, 103, 104, 105, 106, 202, 203, 204, 205, 206)


def _box(idx: pd.DatetimeIndex, k: int) -> pd.DataFrame:
    """One ornl-frp-vav box on a light-load day under a fixed, high duct static: the box meets
    its small airflow need by throttling its damper to 30-48 % open all occupied day."""
    occ = ((idx.hour >= 7) & (idx.hour < 22)).astype(float)
    damper = np.where(occ > 0, 30.0 + 2.0 * k + 1.5 * np.sin(np.arange(len(idx))), 0.0)
    return pd.DataFrame(
        {
            Role.SPACE_TEMP: 70.0 + 0.2 * np.cos(np.arange(len(idx)) + k),
            Role.COOL_SP: np.full(len(idx), 75.2),
            Role.HEAT_SP: np.full(len(idx), 69.8),
            Role.DAMPER: damper,
            Role.AIRFLOW: 300.0 * occ,
            Role.OCCUPANCY: occ,
        },
        index=idx,
    )


def standin(store) -> None:
    """ds-lbnl-sdahu (a fixed static setpoint on the fault-free unit; a faulted run with its
    measured static but no setpoint) and ds-ornl-frp-vav (ten throttled boxes on one day)."""
    idx = hourly_index(days=28)
    write_standin(
        store,
        "lbnl-sdahu",
        {
            "AHU__fault_free": ("AHU", sdahu(idx)),
            "AHU__damper_stuck_075": ("AHU", sdahu(idx, stuck_oaf=0.675, static_sp=False)),
        },
        labels={"AHU__fault_free": "", "AHU__damper_stuck_075": "damper"},
    )
    day = hourly_index(days=3)
    boxes = {f"RTU_VAV_{r}__d3_fault_free": ("VAV", _box(day, k)) for k, r in enumerate(_ROOMS)}
    write_standin(store, "ornl-frp-vav", boxes)


EXERCISE = Exercise(
    id="air-static-pressure",
    title="Duct static pressure: a fixed setpoint and the damper census",
    issue=80,
    references=("pnnl-guide-static-pressure", "pnnl-retuning-ch5"),
    datasets=("lbnl-sdahu", "ornl-frp-vav"),
    runs=(
        Run(dataset="lbnl-sdahu", config="air-static-pressure"),
        Run(dataset="ornl-frp-vav", name="census", config="air-static-pressure--ornl-frp-vav"),
    ),
    commands=(
        "camber datasets fetch lbnl-sdahu ornl-frp-vav",
        "camber datasets ingest lbnl-sdahu ornl-frp-vav --store lab_store",
        "camber datasets config lbnl-sdahu --exercise air-static-pressure --store lab_store "
        "--out sp.json",
        "camber run sp.json --out sp_out",
        "camber datasets config ornl-frp-vav --exercise air-static-pressure--ornl-frp-vav "
        "--store lab_store --out census.json",
        "camber run census.json --out census_out",
    ),
    expect=(
        # lbnl-sdahu: the setpoint never moves -- no trim-and-respond reset
        Finding("static_pressure_reset", "AHU__fault_free", severity=("warn",)),
        Metric("static_pressure_reset", "AHU__fault_free", "sp_range_inwc", 0.0, 0.001),
        Metric(
            "static_pressure_reset",
            "AHU__fault_free",
            "sp_median_inwc",
            SDAHU_STATIC_SP,
            0.001,
            quote="1.607 in. w.c.",
        ),
        # ... and a faulted run has no setpoint to judge (the published placeholder is masked)
        Finding("static_pressure_reset", "AHU__damper_stuck_075", severity=("absent",)),
        # ornl-frp-vav: every box throttled below half open -> static likely too high
        Finding("damper_census", "<fleet>", severity=("fault",), run="census"),
        Metric("damper_census", "<fleet>", "n_boxes", 10, 0, run="census"),
        Metric("damper_census", "<fleet>", "pct_boxes_low", 100.0, 0.0, run="census"),
        Metric(
            "damper_census",
            "<fleet>",
            "median_damper_pct",
            39.3,
            0.2,
            run="census",
            on=REAL,
            quote="39%",
        ),
    ),
    standin=standin,
)
