"""Answer key: workbook exercise ``data-trend-quality`` (docs/workbook/data-trend-quality.md).

Real-data figures were recorded from::

    camber datasets fetch nuig-ahu101
    camber datasets fetch irish-ahu
    camber datasets ingest nuig-ahu101 irish-ahu --store lab_store
    camber datasets ingest lbnl-b59 --from-dir b59_download --store lab_store
    camber datasets config nuig-ahu101 --store lab_store --out nuig.json
    camber report nuig.json --layout rcx --out nuig_rcx.html
    camber datasets config lbnl-b59 --store lab_store --out b59.json
    camber report b59.json --layout rcx --out b59_rcx.html
    camber datasets config irish-ahu --store lab_store --out irish.json
    camber report irish.json --layout rcx --out irish_rcx.html

and the Python read of the same store shown on the page (``frame_sensor_health`` with
``gate="fan"`` on hourly frames, ``gapfill_signature`` and ``sensor_trust`` on the stored grid).
The RCx reports' trust tables show the same verdicts and scores as the hourly reads below.

(CAMBER 0.97.0-dev, the default subsets of nuig-ahu101, lbnl-b59 (with its manual download) and
irish-ahu, 2026-09-29.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import BOTH, REAL, Check, Exercise, Run, write_standin

from camber.model.roles import Role
from camber.schedules import fan_on_mask
from camber.sensorhealth import frame_sensor_health, gapfill_signature, sensor_trust
from camber.store import ParquetStore

# --------------------------------------------------------------------------- the answers

#: a core air-handler point list, in CAMBER role names (the page asks the learner to compare it
#: with PNNL's trending guide); a fan status *or* a fan speed counts as the fan point
CORE_AHU = (
    Role.OAT,
    Role.MIXED_AIR_TEMP,
    Role.RETURN_AIR_TEMP,
    Role.SUPPLY_AIR_TEMP,
    Role.SUPPLY_AIR_TEMP_SP,
    Role.OA_DAMPER,
    Role.COOL_VALVE,
    Role.HEAT_VALVE,
    Role.DUCT_STATIC,
    Role.DUCT_STATIC_SP,
)
FAN = (Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED)
#: what each unit lacks from CORE_AHU (+ "fan" when it trends neither fan point)
MISSING = {
    ("nuig", "AHU101__ahu101"): {"mixed_air_temp", "oa_damper", "duct_static", "duct_static_sp"},
    ("b59", "RTU01"): {"cool_valve", "heat_valve", "duct_static", "duct_static_sp"},
    ("irish", "AHU__ahu"): {"supply_air_temp_sp", "duct_static", "duct_static_sp", "fan"},
}


def _store(ctx) -> ParquetStore:
    return ParquetStore(ctx.store)


def _fid(ctx, run: str) -> str:
    return ctx.config(run)["source"]["facility_id"]


def _frame(ctx, run: str, equip: str, resample=None) -> pd.DataFrame:
    return _store(ctx).read_role_frame(facility_id=_fid(ctx, run), equip=equip, resample=resample)


def _health(ctx, run: str, equip: str) -> dict:
    """The trust table the RCx report shows: hourly frames, gated on the unit's own fan signal."""
    return frame_sensor_health(_frame(ctx, run, equip, "1h"), gate="fan", plant_gate="auto")


def _coverage(ctx) -> None:
    """Q1: the core points each unit does not trend."""
    for (run, equip), want in MISSING.items():
        cols = set(_frame(ctx, run, equip, "1D").columns)
        got = {r.value for r in CORE_AHU if r not in cols}
        if not cols & set(FAN):
            got.add("fan")
        assert got == want, f"{equip}: missing {sorted(got)}, expected {sorted(want)}"
    # with no fan point, the trust table is scored ungated
    assert fan_on_mask(_frame(ctx, "irish", "AHU__ahu", "1h"))[0] is None


def _oa_flow_starts_late(ctx) -> None:
    """Q1: B59's OA flow is trended only from April 2020 (earlier values are masked at ingest)."""
    t = _health(ctx, "b59", "RTU01")[Role.OA_AIRFLOW]
    assert "late_start" in t.flags, f"RTU01 oa_airflow flags {t.flags}"
    if ctx.mode == REAL:
        assert t.first_valid.startswith("2020-04-10"), f"first valid {t.first_valid}"


def _copied_return_air(ctx) -> None:
    """Q2: RTU04's return air is a copy of its supply air; the supply air keeps its score."""
    h = _health(ctx, "b59", "RTU04")
    rat, sat = h[Role.RETURN_AIR_TEMP], h[Role.SUPPLY_AIR_TEMP]
    assert "copied_signal" in rat.flags and rat.verdict != "trusted", (rat.verdict, rat.flags)
    assert "copied_signal" not in sat.flags, f"supply air flagged too: {sat.flags}"
    assert sat.trust > rat.trust, (sat.trust, rat.trust)
    for other in ("RTU01", "RTU03"):
        assert "copied_signal" not in _health(ctx, "b59", other)[Role.RETURN_AIR_TEMP].flags


def _copied_score(ctx) -> None:
    t = _health(ctx, "b59", "RTU04")[Role.RETURN_AIR_TEMP]
    assert round(t.trust, 2) == 0.56, f"RTU04 return air trust {t.trust:.2f}"


def _mixing_balance(ctx) -> None:
    """Q3: RTU01's (and RTU02's) mixed air fails the flow-weighted mixing balance; RTU03's and
    RTU04's do not."""
    bad = ("RTU01", "RTU02") if ctx.mode == REAL else ("RTU01",)
    for eq in bad:
        t = _health(ctx, "b59", eq)[Role.MIXED_AIR_TEMP]
        assert "mixing_balance" in t.flags and t.verdict == "suspect", (eq, t.verdict, t.flags)
        if ctx.mode == REAL:
            assert round(t.trust, 2) == 0.75, (eq, t.trust)
    for eq in ("RTU03", "RTU04"):
        t = _health(ctx, "b59", eq)[Role.MIXED_AIR_TEMP]
        assert "mixing_balance" not in t.flags, f"{eq} mixed air flags {t.flags}"


def _clipped_co2(ctx) -> None:
    """Q4: the room CO2 tops out at the sensor's 2000 ppm full scale, only with the fan off."""
    f = _frame(ctx, "nuig", "AHU101__ahu101")
    co2, fan = f[Role.CO2], f[Role.SUPPLY_FAN_STATUS]
    assert 1999.0 <= co2.max() <= 2000.0, f"CO2 max {co2.max()}"
    top = co2 >= 1999.0
    assert top.sum() > 0 and (fan[top] < 0.5).all(), "a full-scale CO2 sample has the fan on"
    if ctx.mode == REAL:
        assert int(top.sum()) == 532, f"{int(top.sum())} full-scale samples"


def _stuck_lighting(ctx) -> None:
    """Q5: the south lighting meter holds one value for more than a day, twice (REAL) / once."""
    f = _frame(ctx, "b59", "ELE_lig_S")
    t = sensor_trust(f[Role.POWER], Role.POWER)
    assert "stuck" in t.flags and t.stuck_intervals, (t.flags, t.stuck_intervals)
    for iv in t.stuck_intervals:  # both held runs start on a Saturday
        assert pd.Timestamp(iv["start"]).dayofweek == 5, iv
    if ctx.mode == REAL:
        assert [round(iv["hours"], 2) for iv in t.stuck_intervals] == [32.25, 36.5], (
            t.stuck_intervals
        )


def _gapfill(ctx) -> None:
    """Q6: a zone CO2 changes value granularity (a filled stretch); nuig's fan status repeats
    whole days exactly -- a fixed schedule, which the same screen also reports."""
    r = gapfill_signature(_frame(ctx, "b59", "RTU01_zone_022")[Role.CO2])
    assert r.severity == "warn" and "granularity" in r.summary, r.summary
    cont = [w for w in r.metrics["windows"] if w["class"] == "continuous"]
    assert cont, r.metrics["windows"]
    if ctx.mode == REAL:
        assert len(cont) == 2 and cont[0]["start"].startswith("2020-04-26"), cont
    fan = gapfill_signature(_frame(ctx, "nuig", "AHU101__ahu101")[Role.SUPPLY_FAN_STATUS])
    assert fan.severity == "warn" and "repeat another day" in fan.summary, fan.summary


def _irish_sat(ctx) -> None:
    """Q7: irish-ahu's supply air reads 'untrusted' over the whole record; after the stray
    early rows (the longest gap) the low-coverage flag goes, the outlier flag stays."""
    sat = _frame(ctx, "irish", "AHU__ahu", "1h")[Role.SUPPLY_AIR_TEMP]
    whole = sensor_trust(sat, Role.SUPPLY_AIR_TEMP)
    assert whole.verdict == "untrusted", whole.verdict
    assert {"low_coverage", "outliers"} <= set(whole.flags), whole.flags
    valid = sat.dropna().index.to_series()
    after_gap = valid.index[valid.diff().argmax()]
    trimmed = sensor_trust(sat[after_gap:], Role.SUPPLY_AIR_TEMP)
    assert "low_coverage" not in trimmed.flags and "outliers" in trimmed.flags, trimmed.flags
    if ctx.mode == REAL:
        assert round(whole.trust, 2) == 0.19, whole.trust
        assert str(after_gap).startswith("2017-06-23"), after_gap


# --------------------------------------------------------------------------- the stand-in


def _ahu_air(idx, rng, *, fan) -> dict:
    """Outdoor air swinging 30-50 F, return air ~72 F, supply air held near 55 F while running."""
    h = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    oat = 40.0 + 10.0 * np.sin((h - 9.0) / 24.0 * 2 * np.pi) + rng.normal(0, 0.3, len(idx))
    rat = 72.0 + rng.normal(0, 0.4, len(idx))
    sat = np.where(fan > 0.5, 55.0 + rng.normal(0, 0.6, len(idx)), rat - 2.0)
    return {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.SUPPLY_AIR_TEMP: sat}


def _b59_rtu(idx, rng, *, mat_bias=0.0, copy_from=None) -> pd.DataFrame:
    """A B59-like RTU: fan speed (no status), supply and OA flow stations, OA flow from day 10.

    The mixed air follows the flow-weighted blend ``f*OAT + (1-f)*RAT``, plus ``mat_bias``. From
    ``copy_from`` on, the return-air point carries the supply-air samples (a copied point).
    """
    occ = (idx.hour >= 6) & (idx.hour < 20)
    speed = np.where(occ, 70.0 + rng.normal(0, 3, len(idx)), 20.0 + rng.normal(0, 1, len(idx)))
    air = _ahu_air(idx, rng, fan=np.ones(len(idx)))
    sa = 10000.0 * speed / 70.0 + rng.normal(0, 50, len(idx))
    frac = np.clip(0.25 + 0.05 * rng.normal(0, 1, len(idx)), 0.1, 0.4)
    oa = sa * frac
    mat = frac * air[Role.OAT] + (1 - frac) * air[Role.RETURN_AIR_TEMP] + mat_bias
    rat = air[Role.RETURN_AIR_TEMP].copy()
    if copy_from is not None:
        rat = np.where(idx >= copy_from, air[Role.SUPPLY_AIR_TEMP], rat)
    oa_flow = np.where(idx >= idx[0] + pd.Timedelta(days=10), oa, np.nan)
    return pd.DataFrame(
        {
            Role.OAT: air[Role.OAT],
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: air[Role.SUPPLY_AIR_TEMP],
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),
            Role.OA_DAMPER: 100.0 * frac,
            Role.AIRFLOW: sa,
            Role.OA_AIRFLOW: oa_flow,
            Role.SUPPLY_FAN_SPEED: speed,
        },
        index=idx,
    )


def _zone_co2(rng) -> pd.DataFrame:
    """90 days of 15-minute zone CO2 at the sensor's 1 ppm resolution, except days 30-59, which
    are interpolated between 6-hourly points (every value unique): a filled stretch."""
    idx = pd.date_range("2018-01-01", periods=90 * 96, freq="15min")
    h = idx.hour.to_numpy()
    co2 = 430.0 + np.where((h >= 8) & (h < 17), 250.0, 0.0) + rng.normal(0, 15, len(idx))
    co2 = np.round(co2)
    s = pd.Series(co2, index=idx)
    mid = (idx >= idx[0] + pd.Timedelta(days=30)) & (idx < idx[0] + pd.Timedelta(days=60))
    anchors = s[mid].iloc[::24] + rng.uniform(0.1, 0.9, len(s[mid].iloc[::24]))
    s[mid] = anchors.reindex(s[mid].index).interpolate().to_numpy()
    return pd.DataFrame({Role.CO2: s}, index=idx)


def _lighting(rng) -> pd.DataFrame:
    """Two weeks of 15-minute lighting power: 20 kW on weekdays 08-18, ~5 kW otherwise, and one
    weekend (Saturday 00:00 on) held at exactly 5.0 kW for 36 h -- a held value."""
    idx = pd.date_range("2018-01-01", periods=14 * 96, freq="15min")
    on = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    p = np.where(on, 20.0, 5.0) + rng.normal(0, 0.4, len(idx))
    held = (idx >= pd.Timestamp("2018-01-06")) & (idx < pd.Timestamp("2018-01-07 12:00"))
    p[held] = 5.0
    return pd.DataFrame({Role.POWER: p}, index=idx)


def _nuig(rng) -> pd.DataFrame:
    """A 100 % outdoor-air unit on a fixed weekday schedule (08-18): the fan status repeats whole
    days exactly (15-minute means of a one-minute status, so the start and stop quarters read
    0.53 and 0.27). Room CO2 climbs while the fan is off and clips at the 1999.9985 ppm full
    scale on some nights; with the fan running it stays well below."""
    idx = pd.date_range("2018-01-01", periods=14 * 96, freq="15min")
    fan = ((idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)).astype(float)
    wk = idx.dayofweek < 5
    fan[wk & (idx.hour == 8) & (idx.minute == 0)] = 0.53
    fan[wk & (idx.hour == 18) & (idx.minute == 0)] = 0.27
    air = _ahu_air(idx, rng, fan=fan)
    co2 = np.where(fan > 0.5, 600.0 + rng.normal(0, 40, len(idx)), 900.0)
    night = (fan < 0.5) & (idx.hour >= 1) & (idx.hour < 5) & (idx.dayofweek < 3)
    co2 = np.where(night, 1999.9985, co2 + np.where(fan < 0.5, rng.normal(0, 60, len(idx)), 0))
    return pd.DataFrame(
        {
            **air,
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),
            Role.HEAT_VALVE: np.where(fan > 0.5, 30.0 + rng.normal(0, 5, len(idx)), 0.0),
            Role.COOL_VALVE: np.zeros(len(idx)),
            Role.COOL_COIL_LEAVING_TEMP: air[Role.SUPPLY_AIR_TEMP] - 1.0,
            Role.SUPPLY_FAN_STATUS: fan,
            Role.SUPPLY_FAN_SPEED: 60.0 * fan,
            Role.CO2: co2,
            Role.SPACE_TEMP: 70.0 + rng.normal(0, 0.5, len(idx)),
            Role.RETURN_AIR_HUMIDITY: 40.0 + rng.normal(0, 2, len(idx)),
            Role.OUTDOOR_RH: 70.0 + rng.normal(0, 5, len(idx)),
        },
        index=idx,
    )


def _irish(rng) -> pd.DataFrame:
    """A 24/7 mixing-box AHU with no fan point: a few stray rows, then a 30-day gap, then 28 days.

    The supply air is held tightly at its ~66 F setpoint 70 % of the time; in the other hours the
    controller runs it anywhere between 56 and 64 F (other operating modes): a healthy,
    well-controlled sensor whose off-setpoint hours look like outliers to a robust test.
    """
    stray = pd.date_range("2017-12-01 01:00", periods=6, freq="15min")
    block = pd.date_range("2018-01-01", periods=28 * 96, freq="15min")
    idx = stray.append(block)
    n = len(idx)
    hour = (idx - idx[0]) // pd.Timedelta(hours=1)
    other = (rng.random(hour.max() + 1) < 0.3)[hour]  # whole hours off setpoint
    level = rng.uniform(56.0, 64.0, hour.max() + 1)[hour]
    sat = np.where(other, level, 66.2 + rng.normal(0, 0.05, n))
    air = _ahu_air(idx, rng, fan=np.ones(n))
    return pd.DataFrame(
        {
            Role.OAT: air[Role.OAT],
            Role.RETURN_AIR_TEMP: air[Role.RETURN_AIR_TEMP],
            Role.MIXED_AIR_TEMP: 0.2 * air[Role.OAT] + 0.8 * air[Role.RETURN_AIR_TEMP],
            Role.SUPPLY_AIR_TEMP: sat,
            Role.OA_DAMPER: np.full(n, 20.0),
            Role.HEAT_VALVE: np.clip(rng.normal(20, 5, n), 0, 100),
            Role.COOL_VALVE: np.zeros(n),
            Role.HEAT_COIL_LEAVING_TEMP: sat + 0.5,
            Role.COOL_COIL_LEAVING_TEMP: sat + 0.2,
        },
        index=idx,
    )


def standin(store) -> None:
    """ds-nuig-ahu101, ds-lbnl-b59 (four RTUs, one zone CO2, the south lighting meter) and
    ds-irish-ahu, with the equipment ids and roles of the real ingest."""
    rng = np.random.default_rng(0)
    write_standin(store, "nuig-ahu101", {"AHU101__ahu101": ("AHU", _nuig(rng))})
    idx = pd.date_range("2018-01-01", periods=28 * 96, freq="15min")
    copy_from = idx[0] + pd.Timedelta(days=7)
    write_standin(
        store,
        "lbnl-b59",
        {
            "RTU01": ("AHU", _b59_rtu(idx, rng, mat_bias=5.0)),
            "RTU03": ("AHU", _b59_rtu(idx, rng)),
            "RTU04": ("AHU", _b59_rtu(idx, rng, copy_from=copy_from)),
            "RTU01_zone_022": ("VAV", _zone_co2(rng)),
            "ELE_lig_S": ("ELECTRICITY_METER", _lighting(rng)),
        },
    )
    write_standin(store, "irish-ahu", {"AHU__ahu": ("AHU", _irish(rng))})


EXERCISE = Exercise(
    id="data-trend-quality",
    title="Are the trends good enough? Point coverage and sensor health",
    issue=83,
    references=("pnnl-trending-requirements", "pnnl-retuning-ch3", "pnnl-retuning-ch4"),
    datasets=("nuig-ahu101", "lbnl-b59", "irish-ahu"),
    runs=(
        Run(dataset="nuig-ahu101", name="nuig"),
        Run(dataset="lbnl-b59", name="b59"),
        Run(dataset="irish-ahu", name="irish"),
    ),
    commands=(
        "camber datasets fetch nuig-ahu101",
        "camber datasets fetch irish-ahu",
        "camber datasets ingest nuig-ahu101 irish-ahu --store lab_store",
        "camber datasets ingest lbnl-b59 --from-dir b59_download --store lab_store",
        "camber datasets config nuig-ahu101 --store lab_store --out nuig.json",
        "camber report nuig.json --layout rcx --out nuig_rcx.html",
        "camber datasets config lbnl-b59 --store lab_store --out b59.json",
        "camber report b59.json --layout rcx --out b59_rcx.html",
        "camber datasets config irish-ahu --store lab_store --out irish.json",
        "camber report irish.json --layout rcx --out irish_rcx.html",
    ),
    expect=(
        Check("core points each unit lacks", _coverage, on=BOTH),
        Check("B59 OA flow starts late", _oa_flow_starts_late, quote="2020-04-10"),
        Check("RTU04 return air is a copy of its supply air", _copied_return_air),
        Check("RTU04 return air trust", _copied_score, on=REAL, quote="0.56"),
        Check("RTU01 mixed air fails the mixing balance", _mixing_balance, quote="0.75"),
        Check("nuig CO2 clipped at full scale with the fan off", _clipped_co2, quote="532"),
        Check("nuig CO2 full scale", _clipped_co2, quote="just under 2000 ppm"),
        Check("B59 lighting meter held over weekends", _stuck_lighting, quote="36.5 h"),
        Check("B59 lighting meter, first held run", _stuck_lighting, on=REAL, quote="32.25 h"),
        Check("gap-fill and repeated-day screens", _gapfill, quote="2020-04-26"),
        Check("gap-fill windows", _gapfill, on=REAL, quote="two 30-day windows"),
        Check("irish supply air: coverage vs outliers", _irish_sat, quote="0.19"),
        Check("irish supply air after the gap", _irish_sat, on=REAL, quote="2017-06-23"),
    ),
    standin=standin,
)
