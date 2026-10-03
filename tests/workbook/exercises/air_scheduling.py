"""Answer key: workbook exercise ``air-scheduling`` (docs/workbook/air-scheduling.md).

Real-data figures were recorded from::

    camber datasets fetch ornl-frp-ops
    camber datasets ingest ornl-frp-ops --store lab_store
    camber datasets config ornl-frp-ops --store lab_store --out ops.json
    camber run ops.json --out ops_out

(CAMBER 0.97.0-dev, ornl-frp-ops default subset, 2026-09-29.) The night fan energy (16.2 kWh
a night in the 24/7 baseline, 3.2 kWh with the setback) is the supply fan's power
(``WH_RTU_Sup_Fan``, mapped as ``power`` in kW) averaged by hour of day with
``camber.loadprofile.daily_profile`` and summed over the unoccupied hours 22:00-07:00, read
from the same store (see ``_night_fan_energy``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, write_standin

from camber.loadprofile import daily_profile
from camber.model.roles import Role
from camber.store import ParquetStore

_FAN_KW = 1.8  # a constant-volume supply fan's draw while it runs


def _rtu(idx: pd.DatetimeIndex, *, setback: bool) -> pd.DataFrame:
    """One ornl-frp-ops RTU at 1-minute resolution, in winter, on the tests' 07:00-22:00 day.

    - Baseline (``setback=False``): the fan runs around the clock and the return air sits at
      the occupied 67 F all night.
    - Setback: the fan stops at 22:00; from midnight to 07:00 it cycles on for 40 minutes of
      each hour to hold the 60.1 F (15.6 C) heating setback, and the return air it draws reads
      63 F.
    - In both the DX compressor runs 5-minute cycles whenever the fan runs (a small winter
      cooling load at minimum airflow).
    """
    minute = np.arange(len(idx))
    hour = idx.hour.to_numpy()
    day = (hour >= 7) & (hour < 22)
    if setback:
        cycling = (hour < 7) & (idx.minute.to_numpy() < 40)
        fan = (day | cycling).astype(float)
    else:
        fan = np.ones(len(idx))
    comp = fan * ((minute % 8) < 5)
    rat = np.where(day | (not setback), 67.0, 63.0)
    return pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: np.where(fan > 0, 60.0, np.nan),
            Role.RETURN_AIR_TEMP: np.where(fan > 0, rat, np.nan),
            Role.AIRFLOW: np.where(fan > 0, 1.2, np.nan),
            Role.POWER: _FAN_KW * fan + 0.004,
            Role.SUPPLY_FAN_STATUS: fan,
            Role.COMPRESSOR_STATUS: comp,
        },
        index=idx,
    )


def standin(store) -> None:
    """ds-ornl-frp-ops: the heating-season baseline (24/7) and night-setback RTUs, three days
    each at 1-minute resolution."""
    idx = pd.date_range("2018-01-01", periods=3 * 24 * 60, freq="1min")
    write_standin(
        store,
        "ornl-frp-ops",
        {
            "RTU__base_heating": ("AHU", _rtu(idx, setback=False)),
            "RTU__sb_heating": ("AHU", _rtu(idx, setback=True)),
        },
    )


def _night_kwh(ctx, equip: str) -> float:
    frame = ParquetStore(ctx.store).read_role_frame(facility_id="ds-ornl-frp-ops", equip=equip)
    prof = daily_profile(frame[Role.POWER])
    return float(prof[(prof.index < 7) | (prof.index >= 22)].sum())


def _setback_cuts_night_energy(ctx) -> None:
    """The setback cuts the fan's night energy well below the 24/7 baseline's."""
    base = _night_kwh(ctx, "RTU__base_heating")
    sb = _night_kwh(ctx, "RTU__sb_heating")
    assert sb < 0.75 * base, f"night fan energy {sb:.1f} kWh (setback) vs {base:.1f} kWh (24/7)"


def _night_kwh_is(equip: str, pinned: float):
    def check(ctx) -> None:
        got = _night_kwh(ctx, equip)
        assert abs(got - pinned) <= 0.05, f"{equip}: night fan energy {got:.2f} kWh ({pinned})"

    return check


def _held(ctx) -> None:
    """The setback verdict rests on the held-setback test (the return air while the fan runs is
    colder than occupied, near the heating setback), not on runtime."""
    f = ctx.finding("night_weekend_setback", "RTU__sb_heating")
    assert f is not None, "no night_weekend_setback finding on RTU__sb_heating"
    m = f.metrics or {}
    assert m.get("setback_basis") == "held_setback", f"setback_basis {m.get('setback_basis')!r}"
    assert m.get("held_side") == "heating", f"held_side {m.get('held_side')!r}"


EXERCISE = Exercise(
    id="air-scheduling",
    title="Scheduling: 24/7 operation, night setback and after-hours fan energy",
    issue=80,
    references=("pnnl-guide-occupancy-scheduling", "pnnl-retuning-ch5"),
    datasets=("ornl-frp-ops",),
    runs=(Run(dataset="ornl-frp-ops"),),
    commands=(
        "camber datasets fetch ornl-frp-ops",
        "camber datasets ingest ornl-frp-ops --store lab_store",
        "camber datasets config ornl-frp-ops --store lab_store --out ops.json",
        "camber run ops.json --out ops_out",
    ),
    expect=(
        # the 24/7 baseline: the fan never stops, so there is no setback
        Finding("night_weekend_setback", "RTU__base_heating", severity=("fault",)),
        Metric("night_weekend_setback", "RTU__base_heating", "fan_run_unoccupied_pct", 100.0, 0.0),
        # the setback test: the fan cycles at night, but only to hold the setback
        Finding("night_weekend_setback", "RTU__sb_heating", severity=("ok",)),
        Check("setback held by fan cycling", _held),
        Metric(
            "night_weekend_setback",
            "RTU__sb_heating",
            "fan_run_unoccupied_pct",
            55.8,
            0.1,
            on=REAL,
            quote="56%",
        ),
        Metric(
            "night_weekend_setback",
            "RTU__sb_heating",
            "unoccupied_duty_when_running_pct",
            71.2,
            0.1,
            on=REAL,
            quote="71%",
        ),
        Metric(
            "night_weekend_setback",
            "RTU__sb_heating",
            "zone_temp_occupied_f",
            67.3,
            0.05,
            on=REAL,
            quote="67.3 °F",
        ),
        Metric(
            "night_weekend_setback",
            "RTU__sb_heating",
            "zone_temp_unoccupied_f",
            63.0,
            0.05,
            on=REAL,
            quote="63.0 °F",
        ),
        # the DX compressor short-cycles in both tests
        Finding("compressor_short_cycle", "RTU__base_heating", severity=("fault",)),
        Finding("compressor_short_cycle", "RTU__sb_heating", severity=("fault",)),
        Metric(
            "compressor_short_cycle",
            "RTU__base_heating",
            "starts_per_day",
            178.3,
            0.1,
            on=REAL,
            quote="178",
        ),
        Metric(
            "compressor_short_cycle",
            "RTU__sb_heating",
            "starts_per_day",
            131.3,
            0.1,
            on=REAL,
            quote="131",
        ),
        # after-hours load: the fan's night energy
        Check("the setback cuts night fan energy", _setback_cuts_night_energy),
        Check(
            "24/7 night fan energy",
            _night_kwh_is("RTU__base_heating", 16.2),
            on=REAL,
            quote="16.2 kWh",
        ),
        Check(
            "setback night fan energy",
            _night_kwh_is("RTU__sb_heating", 3.2),
            on=REAL,
            quote="3.2 kWh",
        ),
    ),
    standin=standin,
)
