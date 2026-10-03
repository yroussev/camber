"""Answer key: workbook exercise ``air-sat-reset`` (docs/workbook/air-sat-reset.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-sdahu lbnl-ddahu irish-ahu
    camber datasets ingest lbnl-sdahu lbnl-ddahu irish-ahu --store lab_store
    camber datasets config lbnl-sdahu --exercise air-sat-reset --store lab_store --out sat.json
    camber run sat.json --out sat_out
    camber datasets config lbnl-ddahu --exercise air-sat-reset--lbnl-ddahu --store lab_store \
        --out sat_dd.json
    camber run sat_dd.json --out sat_dd_out
    camber datasets config irish-ahu --exercise air-sat-reset--irish-ahu --store lab_store \
        --out sat_ie.json
    camber run sat_ie.json --out sat_ie_out

(CAMBER 0.97.0-dev, the default subset of each dataset, 2026-09-29.) The share of the
fault-free unit's too-warm running hours that are unoccupied (78%) was read from the same
store with ``ParquetStore.read_role_frame(..., resample="1h")`` and
``camber.schedules.fan_on_mask``, the rule's own fan gate (see ``_warm_hours_unoccupied``).
"""

from __future__ import annotations

from _air_standins import ddahu, irish, sdahu
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, hourly_index, write_standin

from camber.model.roles import Role
from camber.schedules import fan_on_mask
from camber.store import ParquetStore


def standin(store) -> None:
    """Three units, each shaped like its dataset's ingest.

    - ds-lbnl-sdahu: the fault-free unit holds its fixed 55.25 F setpoint (no reset); the damper
      stuck at 75 % floods the mixing box with cold air in the cool weeks and, with no heating
      coil, the supply air falls below its setpoint.
    - ds-lbnl-ddahu: the cold deck is held at 55 F in all weather, with no setpoint trended.
    - ds-irish-ahu: the supply air is reset down as the outdoor air warms (a G36-style reset).
    """
    idx = hourly_index(days=28)
    write_standin(
        store,
        "lbnl-sdahu",
        {
            "AHU__fault_free": ("AHU", sdahu(idx)),
            "AHU__damper_stuck_075": ("AHU", sdahu(idx, stuck_oaf=0.675)),
        },
        labels={"AHU__fault_free": "", "AHU__damper_stuck_075": "damper"},
    )
    write_standin(store, "lbnl-ddahu", {"DDAHU__fault_free": ("AHU", ddahu(idx))})
    write_standin(store, "irish-ahu", {"AHU__ahu": ("AHU", irish(idx))})


def _direction(run: str, equip: str, want: str):
    def check(ctx) -> None:
        f = ctx.finding("supply_air_reset", equip, run)
        assert f is not None, f"no supply_air_reset finding on {equip}"
        got = (f.metrics or {}).get("reset_direction")
        assert got == want, f"{equip}: reset_direction {got!r}, expected {want!r}"

    return check


def _warm_hours_unoccupied(ctx) -> None:
    """Most of the fault-free unit's too-warm running hours (supply air more than 2 F above its
    setpoint) are unoccupied: the fan cycling at night, not the occupied control."""
    frame = ParquetStore(ctx.store).read_role_frame(
        facility_id="ds-lbnl-sdahu", equip="AHU__fault_free", resample="1h"
    )
    mask, _ = fan_on_mask(frame)
    on = frame[mask.to_numpy(dtype=bool)]
    warm = on[(on[Role.SUPPLY_AIR_TEMP] - on[Role.SUPPLY_AIR_TEMP_SP]) > 2.0]
    share = 100.0 * float((warm[Role.OCCUPANCY] < 0.5).mean())
    assert 77.0 <= share <= 79.0, f"{share:.1f}% of the too-warm hours unoccupied (pinned 78%)"


EXERCISE = Exercise(
    id="air-sat-reset",
    title="Supply-air temperature reset: is it reset, and which way?",
    issue=80,
    references=("pnnl-guide-discharge-air-temp", "pnnl-retuning-ch5"),
    datasets=("lbnl-sdahu", "lbnl-ddahu", "irish-ahu"),
    runs=(
        Run(dataset="lbnl-sdahu", config="air-sat-reset"),
        Run(dataset="lbnl-ddahu", name="ddahu", config="air-sat-reset--lbnl-ddahu"),
        Run(dataset="irish-ahu", name="irish", config="air-sat-reset--irish-ahu"),
    ),
    commands=(
        "camber datasets fetch lbnl-sdahu lbnl-ddahu irish-ahu",
        "camber datasets ingest lbnl-sdahu lbnl-ddahu irish-ahu --store lab_store",
        "camber datasets config lbnl-sdahu --exercise air-sat-reset --store lab_store "
        "--out sat.json",
        "camber run sat.json --out sat_out",
        "camber datasets config lbnl-ddahu --exercise air-sat-reset--lbnl-ddahu "
        "--store lab_store --out sat_dd.json",
        "camber run sat_dd.json --out sat_dd_out",
        "camber datasets config irish-ahu --exercise air-sat-reset--irish-ahu "
        "--store lab_store --out sat_ie.json",
        "camber run sat_ie.json --out sat_ie_out",
    ),
    expect=(
        # lbnl-sdahu: a flat setpoint, so no reset, and supply air far below the G36 target
        Finding("supply_air_reset", "AHU__fault_free", severity=("warn",)),
        Metric("supply_air_reset", "AHU__fault_free", "sp_range_f", 0.0, 0.01),
        Check("sdahu reads as flat", _direction("main", "AHU__fault_free", "flat")),
        Finding("supply_air_reset_compliance", "AHU__fault_free", severity=("warn",)),
        Metric(
            "supply_air_reset_compliance",
            "AHU__fault_free",
            "pct_below_g36_target",
            71.0,
            0.5,
            on=REAL,
            quote="71%",
        ),
        Metric(
            "supply_air_reset_compliance",
            "AHU__fault_free",
            "mean_gap_f",
            6.0,
            0.1,
            on=REAL,
            quote="6.0 °F",
        ),
        # ... and meeting the setpoint it has: a stuck-open damper can't, with no heating coil
        Finding("supply_air_control", "AHU__damper_stuck_075", severity=("fault",)),
        Metric(
            "supply_air_control",
            "AHU__damper_stuck_075",
            "too_cold_pct",
            32.6,
            0.5,
            on=REAL,
            quote="33%",
        ),
        Finding("supply_air_control", "AHU__fault_free", severity=("warn",), on=REAL),
        Metric(
            "supply_air_control",
            "AHU__fault_free",
            "too_warm_pct",
            12.1,
            0.5,
            on=REAL,
            quote="12%",
        ),
        Check(
            "fault-free too-warm hours are unoccupied", _warm_hours_unoccupied, on=REAL, quote="78%"
        ),
        # lbnl-ddahu: the cold deck pinned at 55 F
        Finding("supply_air_reset", "DDAHU__fault_free", severity=("warn",), run="ddahu"),
        Check("ddahu reads as flat", _direction("ddahu", "DDAHU__fault_free", "flat")),
        Finding(
            "supply_air_reset_compliance", "DDAHU__fault_free", severity=("warn",), run="ddahu"
        ),
        Metric(
            "supply_air_reset_compliance",
            "DDAHU__fault_free",
            "pct_below_g36_target",
            66.2,
            0.5,
            run="ddahu",
            on=REAL,
            quote="66%",
        ),
        # irish-ahu: supply air that falls as OAT rises -- the reset direction G36 asks for
        Finding("supply_air_reset", "AHU__ahu", severity=("ok",), run="irish"),
        Check("irish reads as a reset", _direction("irish", "AHU__ahu", "reset")),
        Metric(
            "supply_air_reset",
            "AHU__ahu",
            "slope_per_F",
            -0.137,
            0.005,
            run="irish",
            on=REAL,
            quote="-0.14 °F per °F",
        ),
        Finding("supply_air_reset_compliance", "AHU__ahu", severity=("ok",), run="irish"),
    ),
    standin=standin,
)
