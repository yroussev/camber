"""Answer key: workbook exercise ``zone-dcv`` (docs/workbook/zone-dcv.md).

Real-data figures were recorded from::

    camber datasets fetch finnish-dcv
    camber datasets ingest finnish-dcv --store lab_store
    camber datasets config finnish-dcv --store lab_store --out finnish.json
    camber run finnish.json --out finnish_out
    camber datasets fetch b4b-windesheim
    camber datasets ingest b4b-windesheim --store lab_store
    camber datasets config b4b-windesheim --store lab_store --out b4b.json
    camber run b4b.json --out b4b_out
    camber datasets fetch sdu-ou44
    camber datasets ingest sdu-ou44 --store lab_store
    camber datasets config sdu-ou44 --store lab_store --out ou44.json
    camber run ou44.json --out ou44_out

(CAMBER 0.97.0-dev, the default subsets -- each dataset's whole published record -- 2026-09-29.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, write_standin

from camber.model.roles import Role

# a sedentary adult's CO2 output, cfm (5 mL/s): the steady-state lift is N * G / Q
_G_CFM = 0.0105
_OUT = 430.0  # outdoor CO2, ppm


def _steady_co2(people, oa_cfm):
    """Well-mixed room at steady state: C = C_out + N G / Q (ppm)."""
    return _OUT + 1e6 * np.asarray(people) * _G_CFM / np.maximum(np.asarray(oa_cfm), 1.0)


def _days(start: str, n: int, every: int, hours: tuple, freq: str = "15min") -> pd.DatetimeIndex:
    """``n`` separate days ``every`` days apart, each covering ``hours`` = (first, last) hour."""
    out = []
    for d in range(n):
        day = pd.Timestamp(start) + pd.Timedelta(days=d * every)
        out.append(
            pd.date_range(
                day + pd.Timedelta(hours=hours[0]),
                day + pd.Timedelta(hours=hours[1]),
                freq=freq,
                inclusive="left",
            )
        )
    return out[0].append(out[1:])


def _finnish(store) -> None:
    """ds-finnish-dcv: one lab office room, three separate records of 06-18 days.

    The ventilation test runs the documented law, supply (all outdoor air) = base + 6 l/s per
    occupant, with the base at the measured 4 l/s (8.5 cfm) rather than the documented 3 l/s: CO2
    and outdoor air rise together, and OA sits above the 6.36 cfm floor even when the room is
    empty. The two training records run a strategy unrelated to occupancy (a fixed 10 or 20 l/s,
    alternating by day), so a busier day at the same hour has *less* air per person, not more.
    """
    rng = np.random.default_rng(0)
    frames = {}
    for eq, law in (
        ("ROOM__training_1", "fixed"),
        ("ROOM__training_2", "fixed"),
        ("ROOM__ventilation_test", "dcv"),
    ):
        idx = _days("2025-11-03", 8, 1, (6, 18))
        day = (idx.normalize() - idx[0].normalize()).days.to_numpy()
        hour = idx.hour.to_numpy()
        people = np.where(
            (hour >= 8) & (hour < 16),
            rng.integers(0, 5, size=len(idx) // 4 + 1).repeat(4)[: len(idx)],
            0,
        )
        if law == "dcv":
            oa = 8.5 + 12.7 * people
        else:
            oa = np.where(day % 2 == 0, 21.2, 42.4)
        frames[eq] = (
            "VAV",
            pd.DataFrame(
                {
                    Role.CO2: _steady_co2(people, oa),
                    Role.OA_AIRFLOW: oa,
                    Role.OCCUPANCY: people.astype(float),
                    Role.OUTDOOR_CO2: np.full(len(idx), _OUT),
                    Role.SPACE_TEMP: np.full(len(idx), 70.0),
                },
                index=idx,
            ),
        )
    write_standin(store, "finnish-dcv", frames, labels={eq: "" for eq in frames})


def _b4b(store) -> None:
    """ds-b4b-windesheim: three office rooms, three weeks at 15 minutes; two rooms also carry a
    desk CO2 sensor (``_scd41``).

    The ventilation valve (a fraction of the room's maximum, mapped as the OA damper) sits at its
    20 % minimum, opens to 60 % on a weekday-morning clock whether or not anyone is in, and opens
    to 90 % when the room's CO2 climbs. Rooms are occupied an hour at a time, three times a weekday;
    most visits are one person (CO2 stays near outdoor), a third are meetings (CO2 near 1,000
    ppm). Room 999169's BMS sensor follows the building's afternoon CO2 build-up (as a sensor in
    a shared extract duct would) and sees only a twentieth of this room's own rise, so at a given
    hour of day it reads the same whether the valve opened or not; its desk sensor sees the room
    as it is. Room 925038 has no valve signal at all.
    """
    rng = np.random.default_rng(1)
    idx = pd.date_range("2022-10-10", periods=21 * 96, freq="15min")
    hour = idx.hour.to_numpy()
    weekday = np.asarray(idx.dayofweek < 5)
    frames, labels = {}, {}
    for room in ("917810", "999169", "925038"):
        visit = weekday & np.isin(hour, (9, 11, 14))
        meeting_day = rng.random(len(idx) // 96 + 1).repeat(96)[: len(idx)] < 0.35
        people = np.where(visit, np.where(meeting_day, 6, 1), 0)
        co2 = np.where(people >= 6, 1000.0, np.where(people == 1, 470.0, 440.0))
        valve = np.where(co2 > 800, 90.0, np.where(weekday & (hour >= 7) & (hour < 9), 60.0, 20.0))
        occ = (people > 0).astype(float)
        if room == "999169":  # reads the building's afternoon build-up, not this room
            bms = np.where((hour >= 14) & (hour < 16), 850.0, 450.0) + 0.05 * (co2 - 440.0)
        else:
            bms = co2
        base = {Role.CO2: bms, Role.OCCUPANCY: occ, Role.SPACE_TEMP: np.full(len(idx), 69.5)}
        if room != "925038":
            base[Role.OA_DAMPER] = valve
            frames[f"ROOM_{room}_scd41"] = (
                "VAV",
                pd.DataFrame(
                    {Role.CO2: co2, Role.OA_DAMPER: valve, Role.OCCUPANCY: occ}, index=idx
                ),
            )
            labels[f"ROOM_{room}_scd41"] = ""
        frames[f"ROOM_{room}"] = ("VAV", pd.DataFrame(base, index=idx))
        if room != "925038":
            labels[f"ROOM_{room}"] = ""
    write_standin(store, "b4b-windesheim", frames, labels=labels)


def _ou44(store) -> None:
    """ds-sdu-ou44: three teaching rooms on 12 separate days (stored a week apart, as the
    ingest's synthetic day clock does), 05-17 UTC occupied, 15 minutes.

    Room 1 (a lecture room) fills up on some days and its VAV damper opens with CO2: CO2 and the
    damper rise together, day against day at the same hour. Rooms 2 and 3 (study zones) are
    lightly used: their CO2 moves 150-300 ppm but never reaches the 800 ppm where DCV should
    respond, so there is nothing to judge. Room 2 sits within 150 ppm of outdoor most of the
    time (over-ventilated for its use); room 3 a little higher.
    """
    rng = np.random.default_rng(2)
    idx = _days("2000-01-03", 12, 7, (0, 24))
    hour = idx.hour.to_numpy()
    occ = (hour >= 5) & (hour < 17)
    level = rng.random(len(idx) // 4 + 1).repeat(4)[: len(idx)]
    frames = {}
    co2_1 = np.where(occ, 450.0 + 650.0 * level, 440.0)
    damper_1 = np.where(occ, np.clip((co2_1 - 600.0) / 4.0, 5.0, 100.0), 0.0)
    co2_2 = np.where(occ, np.where(level > 0.75, 650.0 + 80.0 * level, 470.0 + 60.0 * level), 440.0)
    co2_3 = np.where(occ, 540.0 + 250.0 * level, 440.0)
    for eq, co2, damper in (
        ("ROOM1", co2_1, damper_1),
        ("ROOM2", co2_2, np.where(occ, 20.0 + 10.0 * level, 0.0)),
        ("ROOM3", co2_3, np.where(occ, 20.0 + 10.0 * level, 0.0)),
    ):
        frames[eq] = (
            "VAV",
            pd.DataFrame(
                {
                    Role.CO2: co2,
                    Role.OA_DAMPER: damper,
                    Role.OCCUPANCY: occ.astype(float),
                    Role.SPACE_TEMP: np.full(len(idx), 72.0),
                },
                index=idx,
            ),
        )
    write_standin(store, "sdu-ou44", frames, labels={eq: "" for eq in frames})


def standin(store) -> None:
    """The three DCV datasets' facilities."""
    _finnish(store)
    _b4b(store)
    _ou44(store)


def _status(ctx, run: str, equip: str, want: str, reason: str | None = None) -> None:
    f = ctx.finding("dcv_verification", equip, run)
    assert f is not None, f"no dcv_verification finding on {equip}"
    got = f.metrics.get("status")
    assert got == want, f"{equip}: DCV status {got!r}, expected {want!r}"
    if reason is not None:
        assert f.metrics.get("reason") == reason, f"{equip}: reason {f.metrics.get('reason')!r}"


def _finnish_verdicts(ctx) -> None:
    """The documented DCV law reads 'functioning'; the training records 'not with demand'."""
    _status(ctx, "finnish", "ROOM__ventilation_test", "functioning")
    _status(ctx, "finnish", "ROOM__training_1", "uncorrelated")
    _status(ctx, "finnish", "ROOM__training_2", "uncorrelated")


def _b4b_verdicts(ctx) -> None:
    """Room 917810 functions on both sensors; room 999169 only on its desk sensor."""
    _status(ctx, "b4b", "ROOM_917810", "functioning")
    _status(ctx, "b4b", "ROOM_917810_scd41", "functioning")
    _status(ctx, "b4b", "ROOM_999169", "uncorrelated")
    _status(ctx, "b4b", "ROOM_999169_scd41", "functioning")


def _b4b_over_real(ctx) -> None:
    """Every b4b room, on either sensor, has CO2 near outdoor in 73-86 % of occupied hours."""
    got = [f.metrics["over_vent_pct"] for f in ctx.findings("b4b") if f.rule == "co2_ventilation"]
    assert len(got) == 5 and 72.5 <= min(got) and max(got) < 86.0, got


def _ou44_verdicts(ctx) -> None:
    """The lecture room functions; the two study zones are honestly not judged."""
    _status(ctx, "ou44", "ROOM1", "functioning")
    _status(ctx, "ou44", "ROOM2", "insufficient", "demand_below_engage")
    _status(ctx, "ou44", "ROOM3", "insufficient", "demand_below_engage")


EXERCISE = Exercise(
    id="zone-dcv",
    title="Ventilation: is outdoor air following occupancy (DCV)?",
    issue=81,
    references=("pnnl-guide-min-oa", "pnnl-retuning-ch5"),
    datasets=("finnish-dcv", "b4b-windesheim", "sdu-ou44"),
    runs=(
        Run(dataset="finnish-dcv", name="finnish"),
        Run(dataset="b4b-windesheim", name="b4b"),
        Run(dataset="sdu-ou44", name="ou44"),
    ),
    commands=(
        "camber datasets fetch finnish-dcv",
        "camber datasets ingest finnish-dcv --store lab_store",
        "camber datasets config finnish-dcv --store lab_store --out finnish.json",
        "camber run finnish.json --out finnish_out",
        "camber datasets fetch b4b-windesheim",
        "camber datasets ingest b4b-windesheim --store lab_store",
        "camber datasets config b4b-windesheim --store lab_store --out b4b.json",
        "camber run b4b.json --out b4b_out",
        "camber datasets fetch sdu-ou44",
        "camber datasets ingest sdu-ou44 --store lab_store",
        "camber datasets config sdu-ou44 --store lab_store --out ou44.json",
        "camber run ou44.json --out ou44_out",
    ),
    expect=(
        # a known DCV law, and two records that are not DCV
        Check("finnish-dcv verdicts", _finnish_verdicts),
        Finding("dcv_verification", "ROOM__ventilation_test", severity=("warn",), run="finnish"),
        Metric(
            "dcv_verification",
            "ROOM__ventilation_test",
            "demand_lift",
            331.7,
            0.5,
            run="finnish",
            on=REAL,
            quote="332 ppm",
        ),
        Metric(
            "dcv_verification",
            "ROOM__ventilation_test",
            "excess_at_low_demand_pct",
            100.0,
            0.1,
            run="finnish",
            on=REAL,
            quote="100 %",
        ),
        # which CO2 sensor do you trust?
        Check("b4b-windesheim verdicts", _b4b_verdicts),
        Finding("dcv_verification", "ROOM_925038", severity=("absent",), run="b4b"),
        Metric(
            "dcv_verification",
            "ROOM_917810",
            "demand_lift",
            138.6,
            0.5,
            run="b4b",
            on=REAL,
            quote="139 ppm",
        ),
        Metric(
            "dcv_verification",
            "ROOM_917810_scd41",
            "demand_lift",
            326.1,
            0.5,
            run="b4b",
            on=REAL,
            quote="326 ppm",
        ),
        Check("b4b over-ventilation shares", _b4b_over_real, on=REAL, quote="73 to 86%"),
        Metric(
            "dcv_verification",
            "ROOM_999169",
            "demand_lift",
            11.8,
            0.5,
            run="b4b",
            on=REAL,
            quote="12 ppm",
        ),
        Metric(
            "dcv_verification",
            "ROOM_999169_scd41",
            "demand_lift",
            212.1,
            0.5,
            run="b4b",
            on=REAL,
            quote="212 ppm",
        ),
        *(
            Finding("co2_ventilation", eq, severity=("warn",), run="b4b")
            for eq in (
                "ROOM_917810",
                "ROOM_917810_scd41",
                "ROOM_925038",
                "ROOM_999169",
                "ROOM_999169_scd41",
            )
        ),
        # functioning, and "not judged"
        Check("sdu-ou44 verdicts", _ou44_verdicts),
        Finding("dcv_verification", "ROOM1", severity=("ok",), run="ou44"),
        Metric(
            "dcv_verification",
            "ROOM1",
            "demand_lift",
            181.4,
            0.5,
            run="ou44",
            on=REAL,
            quote="181 ppm",
        ),
        Finding("co2_ventilation", "ROOM2", severity=("warn",), run="ou44"),
        Metric(
            "co2_ventilation", "ROOM2", "over_vent_pct", 66.5, 0.2, run="ou44", on=REAL, quote="66%"
        ),
        Finding("co2_ventilation", "ROOM1", severity=("ok",), run="ou44"),
        Finding("co2_ventilation", "ROOM3", severity=("ok",), run="ou44"),
    ),
    standin=standin,
)
