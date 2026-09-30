"""Answer key: workbook exercise ``capstone`` (docs/workbook/capstone.md).

Real-data figures were recorded from::

    camber datasets fetch lbnl-sdahu
    camber datasets fetch ornl-frp-ops
    camber datasets ingest lbnl-sdahu ornl-frp-ops --store lab_store
    camber datasets config lbnl-sdahu --exercise capstone --store lab_store --out cap.json
    camber run cap.json --out cap_out
    camber report cap.json --layout rcx --out cap_rcx.html
    camber drift freeze cap.json
    camber drift run cap.json
    camber datasets config ornl-frp-ops --store lab_store --out ornl.json
    camber run ornl.json --out ornl_out

and the M&V attempt in Python shown on the page (``caltrack_savings_hourly`` on the RTU's
power, baseline test against setback test).

(CAMBER 0.97.0-dev, the lbnl-sdahu and ornl-frp-ops default subsets, 2026-09-29; the M&V
refusal's data need, 0.98.0-dev, 2026-09-30.)
"""

from __future__ import annotations

import copy
import os
import tempfile

import numpy as np
import pandas as pd
from _workbook import REAL, Check, Exercise, Finding, Metric, Run, write_standin

from camber.config import run_config, run_drift_config
from camber.mandv.caltrack import caltrack_savings_hourly
from camber.mandv.sufficiency import InsufficientBaseline
from camber.model.roles import Role
from camber.report import build_rcx_report
from camber.store import ParquetStore

ONSET, FREE = "AHU__onset_damper_stuck_025", "AHU__fault_free"
_MIN_OAF = 0.016  # the unit's own design minimum (its fixed 10 % damper position)
_STUCK_OAF = 0.044  # the 25 % damper's OA fraction, a design parameter of the fault


# --------------------------------------------------------------------------- the checks


def _rcx(ctx):
    """The RCx report on the exercise config *before* any drift baseline is frozen (the drift
    store points at a file that does not exist, so the drift checks decline, as on the page)."""
    cache = ctx.__dict__.setdefault("_capstone", {})
    if "rcx" not in cache:
        cfg = copy.deepcopy(ctx.config("main"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg["drift"]["store"] = os.path.join(tmp, "not-frozen.json")
            cache["rcx"] = build_rcx_report(run_config(cfg, base_dir=ctx.store))
    return cache["rcx"]


def _rcx_ranking(ctx) -> None:
    """Step 1: the top issue is the onset unit's economizer chain, at fault severity; its
    recommended action asks for an on-site check of the damper."""
    rep = _rcx(ctx)
    top = rep.to_dict()["issues"][0]
    assert top["equip"] == ONSET and top["severity"] == "fault", top
    assert "free_cooling_missed" in top["rules"] and top["chain"] == "econ", top
    assert "verify the OA damper modulates open" in rep.to_html()


def _rcx_conditional(ctx) -> None:
    """Step 1: the onset unit's static-reset issue is conditional on an untrusted setpoint."""
    issues = _rcx(ctx).to_dict()["issues"]
    cond = [i for i in issues if i["conditional_on"]]
    assert len(cond) == 1, [(i["equip"], i["rules"], i["conditional_on"]) for i in issues]
    (c,) = cond
    assert c["equip"] == ONSET and c["rules"] == ["static_pressure_reset"], c
    assert "duct_static_sp untrusted" in c["conditional_on"][0], c
    if ctx.mode == REAL:
        assert "(trust 0.40)" in c["conditional_on"][0], c


def _drift(ctx) -> None:
    """Step 4: frozen on the first half, scored on the second: the onset unit's economizer
    under-delivers outdoor air; the fault-free control is steady."""
    cfg = copy.deepcopy(ctx.config("main"))
    with tempfile.TemporaryDirectory() as tmp:
        cfg["drift"]["store"] = os.path.join(tmp, "capstone-baselines.json")
        res = run_drift_config(cfg, base_dir=ctx.store, freeze_if_missing=True)
    diag = {d.equip: d for fam in res.families for d in fam.diagnoses}
    on, free = diag[ONSET].as_dict(), diag[FREE].as_dict()
    assert (on["severity"], on["locus"]) == ("fault", "outdoor-air"), on
    assert (free["severity"], free["locus"]) == ("ok", "steady"), free
    drift = on["signals"]["economizer_damper_drift"]["drift"]
    assert drift < 0, drift
    if ctx.mode == REAL:
        assert round(drift) == -83, drift


def _held_setback(ctx) -> None:
    """Step 5: after the measure the fan cycles at night to hold the unoccupied setpoint."""
    f = ctx.finding("night_weekend_setback", "RTU__sb_heating", "ornl")
    assert f.metrics["setback_basis"] == "held_setback", f.metrics
    assert f.metrics["held_side"] == "heating", f.metrics


def _ornl_frame(ctx, equip: str, resample="1h") -> pd.DataFrame:
    fid = ctx.config("ornl")["source"]["facility_id"]
    return ParquetStore(ctx.store).read_role_frame(facility_id=fid, equip=equip, resample=resample)


def _mv_refused(ctx) -> None:
    """Step 5: a one-week test cannot carry an hourly M&V baseline: CAMBER refuses it."""
    base = _ornl_frame(ctx, "RTU__base_heating")[Role.POWER]
    rep = _ornl_frame(ctx, "RTU__sb_heating")[Role.POWER]
    b_oat = _ornl_frame(ctx, "WEATHER__base_heating")[Role.OAT]
    r_oat = _ornl_frame(ctx, "WEATHER__sb_heating")[Role.OAT]
    try:
        caltrack_savings_hourly(base, b_oat, rep, r_oat)
    except ValueError as e:
        msg, err = str(e), e
    else:
        raise AssertionError("caltrack_savings_hourly accepted a one-week baseline")
    assert "need >= 1440 baseline hours" in msg, msg
    if ctx.mode == REAL:
        assert msg.endswith("got 168"), msg
    ctx.__dict__.setdefault("_capstone", {})["mv_error"] = err


def _mv_need(ctx) -> None:
    """Step 5 (0.98, #88): the refusal says what data is needed -- 1,272 more hours."""
    err = ctx.__dict__.get("_capstone", {}).get("mv_error")
    if err is None:
        _mv_refused(ctx)
        err = ctx.__dict__["_capstone"]["mv_error"]
    assert isinstance(err, InsufficientBaseline), type(err)
    need = err.need
    assert (need["interval"], need["unit"], need["required"]) == ("hourly", "hours", 1440), need
    assert need["shortfall"] == need["required"] - need["have"] > 0, need
    if ctx.mode == REAL:
        assert (need["have"], need["shortfall"], need["days_short"]) == (168, 1272, 53), need


# --------------------------------------------------------------------------- the stand-in


def _sdahu(idx: pd.DatetimeIndex, onset: pd.Timestamp | None) -> pd.DataFrame:
    """A single-duct AHU in free-cooling weather (OAT 40-58 F), running 05-21 every day.

    The economizer mixes to 55 F above the unit's 1.6 % minimum; from ``onset`` the damper is
    stuck, so the OA fraction stays at 4.4 % while the command keeps modulating, and the cooling
    coil makes up the difference. The static setpoint is the fault-free unit's flat 1.6 in.w.c.;
    after the onset the published setpoint is a placeholder that ingest masks (missing)."""
    n = len(idx)
    h = idx.hour.to_numpy()
    oat = 49.0 + 9.0 * np.sin((h - 9) / 24 * 2 * np.pi) + 0.3 * np.cos(np.arange(n) * 0.7)
    rat = 72.0 + 0.3 * np.cos(np.arange(n))
    occ = (h >= 5) & (h < 21)
    fan = occ.astype(float)
    need = np.clip((rat - 55.0) / (rat - oat), _MIN_OAF, 1.0)
    damper = np.where(occ, 10.0 + 90.0 * (need - _MIN_OAF) / (1.0 - _MIN_OAF), 0.0)
    stuck = np.zeros(n, dtype=bool) if onset is None else (idx >= onset)
    oaf = np.where(stuck, _STUCK_OAF, need)
    mat = np.where(occ, oaf * oat + (1.0 - oaf) * rat, rat - 1.0)
    # the coil takes up to 25 F off the mixed air (+1 F fan heat) to hold the 55.25 F setpoint
    valve = np.where(occ, np.clip((mat + 1.0 - 55.25) / 0.25, 0.0, 100.0), 0.0)
    wobble = 0.3 * np.sin(np.arange(n) * 1.3)
    sat = np.where(occ, mat + 1.0 - 0.25 * valve + wobble, rat - 3.0 + wobble)
    sp = np.where(stuck, np.nan, 1.6)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.25),
            Role.OA_DAMPER: damper,
            Role.COOL_VALVE: valve,
            Role.SUPPLY_FAN_STATUS: fan,
            Role.SUPPLY_FAN_SPEED: 80.0 * fan,
            Role.AIRFLOW: 10000.0 * fan,
            Role.DUCT_STATIC: np.where(occ, 1.6 + 0.02 * np.sin(np.arange(n)), 0.0),
            Role.DUCT_STATIC_SP: sp,
            Role.OCCUPANCY: fan,
            Role.SPACE_TEMP: 72.0 + 0.5 * np.sin(np.arange(n) / 5),
        },
        index=idx,
    )


def _rtu(idx: pd.DatetimeIndex, *, setback: bool, rng) -> pd.DataFrame:
    """ORNL-like heating RTU at one-minute resolution. The compressor cycles 10 min on / 5 off
    whenever it runs (about 96 starts a day). Baseline: fan on around the clock. Setback: fan on
    07-22; at night it cycles on (45 min of each hour) to hold the 60 F unoccupied setpoint."""
    n = len(idx)
    h = idx.hour.to_numpy()
    minute = idx.minute.to_numpy()
    night = (h < 7) | (h >= 22)
    fan = np.ones(n) if not setback else np.where(night, (minute < 45).astype(float), 1.0)
    comp = ((np.arange(n) % 15) < 10).astype(float) * fan
    rat = np.where(setback & night, 61.0, 68.0) + rng.normal(0, 0.2, n)
    return pd.DataFrame(
        {
            Role.SUPPLY_FAN_STATUS: fan,
            Role.COMPRESSOR_STATUS: comp,
            Role.POWER: 0.4 * fan + 3.0 * comp + rng.normal(0, 0.05, n),
            Role.AIRFLOW: 1500.0 * fan,
            Role.RETURN_AIR_TEMP: rat,
            Role.SUPPLY_AIR_TEMP: np.where(fan > 0, rat + 25.0 * comp, rat),
            Role.RETURN_AIR_HUMIDITY: 35.0 + rng.normal(0, 1, n),
            Role.SUPPLY_AIR_HUMIDITY: 25.0 + rng.normal(0, 1, n),
        },
        index=idx,
    )


def _weather(idx, rng, mean) -> pd.DataFrame:
    h = idx.hour.to_numpy()
    oat = mean + 8.0 * np.sin((h - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.5, len(idx))
    return pd.DataFrame(
        {Role.OAT: oat, Role.OUTDOOR_RH: 60.0 + rng.normal(0, 3, len(idx))}, index=idx
    )


def standin(store) -> None:
    """ds-lbnl-sdahu (the onset run and its fault-free control, 10 days before and 18 after the
    2018-07-01 onset) and ds-ornl-frp-ops (the baseline and setback heating tests, three days
    each at one minute)."""
    idx = pd.date_range("2018-06-21", periods=28 * 24, freq="1h")
    write_standin(
        store,
        "lbnl-sdahu",
        {
            FREE: ("AHU", _sdahu(idx, None)),
            ONSET: ("AHU", _sdahu(idx, pd.Timestamp("2018-07-01"))),
        },
        labels={FREE: "", ONSET: "damper"},
    )
    rng = np.random.default_rng(4)
    base = pd.date_range("2021-03-04", periods=3 * 1440, freq="1min")
    sb = pd.date_range("2022-01-08", periods=3 * 1440, freq="1min")
    write_standin(
        store,
        "ornl-frp-ops",
        {
            "RTU__base_heating": ("AHU", _rtu(base, setback=False, rng=rng)),
            "RTU__sb_heating": ("AHU", _rtu(sb, setback=True, rng=rng)),
            "WEATHER__base_heating": ("WEATHER", _weather(base, rng, 46.0)),
            "WEATHER__sb_heating": ("WEATHER", _weather(sb, rng, 36.0)),
        },
    )


EXERCISE = Exercise(
    id="capstone",
    title="Capstone: RCx report, walk-down, re-tuning plan and verification",
    issue=83,
    references=("pnnl-guide-economizer", "pnnl-retuning-ch9", "pnnl-retuning-ch10"),
    datasets=("lbnl-sdahu", "ornl-frp-ops"),
    runs=(
        Run(dataset="lbnl-sdahu", name="main", config="capstone"),
        Run(dataset="ornl-frp-ops", name="ornl"),
    ),
    commands=(
        "camber datasets fetch lbnl-sdahu",
        "camber datasets fetch ornl-frp-ops",
        "camber datasets ingest lbnl-sdahu ornl-frp-ops --store lab_store",
        "camber datasets config lbnl-sdahu --exercise capstone --store lab_store --out cap.json",
        "camber run cap.json --out cap_out",
        "camber report cap.json --layout rcx --out cap_rcx.html",
        "camber drift freeze cap.json",
        "camber drift run cap.json",
        "camber datasets config ornl-frp-ops --store lab_store --out ornl.json",
        "camber run ornl.json --out ornl_out",
    ),
    expect=(
        # step 1: the findings and the RCx report
        Finding("free_cooling_missed", ONSET, severity=("fault",)),
        Finding("free_cooling_missed", FREE, severity=("ok", "warn")),
        Finding("outdoor_air_fraction", ONSET, present=False),
        Metric("free_cooling_missed", ONSET, "missed_pct", 30.0, 0.5, on=REAL, quote="30%"),
        Metric("free_cooling_missed", FREE, "missed_pct", 17.0, 0.5, on=REAL, quote="17%"),
        Check("RCx: the top issue and its on-site check", _rcx_ranking),
        Check("RCx: the conditional issue", _rcx_conditional, quote="trust 0.40"),
        # step 4: verification by drift
        Check("drift localizes the onset to the outdoor-air path", _drift, quote="-83"),
        # step 5: the ORNL scheduling measure, before and after
        Finding("night_weekend_setback", "RTU__base_heating", severity=("fault",), run="ornl"),
        Finding("night_weekend_setback", "RTU__sb_heating", severity=("ok",), run="ornl"),
        Check("the setback is held by fan cycling", _held_setback),
        Finding("compressor_short_cycle", "RTU__base_heating", severity=("fault",), run="ornl"),
        Finding("compressor_short_cycle", "RTU__sb_heating", severity=("fault",), run="ornl"),
        Metric(
            "compressor_short_cycle",
            "RTU__base_heating",
            "starts_per_day",
            178.3,
            0.05,
            run="ornl",
            on=REAL,
            quote="178",
        ),
        Metric(
            "compressor_short_cycle",
            "RTU__sb_heating",
            "starts_per_day",
            131.35,
            0.05,
            run="ornl",
            on=REAL,
            quote="131",
        ),
        Check("M&V on a one-week test is refused", _mv_refused, quote="168"),
        Check("the refusal says what data is needed", _mv_need, quote="1,272"),
    ),
    standin=standin,
)
