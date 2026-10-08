"""Answer key: workbook exercise ``air-economizer`` (docs/workbook/air-economizer.md).

The worked example of the framework (#79); #80 extended it with the irish-ahu part.

Real-data figures were recorded from::

    camber datasets fetch lbnl-sdahu
    camber datasets ingest lbnl-sdahu --store lab_store
    camber datasets config lbnl-sdahu --exercise air-economizer --store lab_store --out econ.json
    camber run econ.json --out econ_out
    camber datasets score lbnl-sdahu --store lab_store --findings econ_out/findings.json
    camber datasets fetch irish-ahu
    camber datasets ingest irish-ahu --store lab_store
    camber datasets config irish-ahu --store lab_store --out irish.json
    camber run irish.json --out irish_out

(CAMBER 0.97.0-dev, the default subset of each dataset, 2026-09-29.) The irish-ahu period
figures (53% / 45%) come from the same config with ``source.start`` / ``source.end`` set to
2017-06-01 .. 2020-03-01 and 2020-08-01 .. 2021-12-01 (see ``_irish_by_period``). The low-limit
figures (step 5, #111: 3.63% with the lockout, 899 of the 965 missed hours below it) come from
``econ.json`` with ``"low_limit_f": 33.8`` added to ``free_cooling_missed`` (see ``_low_limit``;
CAMBER 0.102.0-dev, 2026-10-04). ``free_cooling_missed`` judges fan-on hours only from 0.102
(#120); its figures here were re-recorded then (2026-10-08).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _air_standins import irish
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

from camber.config import run_config
from camber.model.roles import Role

# the stuck position's OA fraction, as measured on the real runs (median, fan on, cooling weather)
_STUCK_OAF = {
    "damper_stuck_010": 0.016,
    "damper_stuck_025": 0.044,
    "damper_stuck_075": 0.675,
    "damper_stuck_100_short": 1.0,
}
_MIN_OAF = 0.016  # the unit's own design minimum (a 10 % damper position)
_LOW_LIMIT_F = 33.8  # the unit's economizer low-limit lockout (inventory section 1.2)
_LABELS = {
    "AHU__fault_free": "",
    "AHU__coi_leakage_010": "valve_leak",
    **{f"AHU__{k}": "damper" for k in _STUCK_OAF},
}


def _ahu(idx: pd.DatetimeIndex, stuck_oaf: float | None) -> pd.DataFrame:
    """One single-duct AHU, cooling coil only, fixed 60 F dry-bulb economizer high limit and a
    33.8 F low-limit lockout.

    Four cold days (OAT 20-32 F: below the lockout), the rest of two cool weeks (33-57 F: free
    cooling), then two warm ones (70-90 F: cooling weather), occupied 06-18 on weekdays with the
    fan on only then. Below the lockout the sequence holds the damper at its minimum, so the coil
    cools the mixed air, as on the real unit. The controller's damper *command* otherwise always
    modulates; a stuck damper fixes the actual OA fraction behind it.
    """
    day = np.arange(len(idx)) // 24
    phase = np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    oat = np.where(
        day < 4, 26.0 + 6.0 * phase, np.where(day < 14, 45.0 + 12.0 * phase, 80.0 + 10.0 * phase)
    )
    rat = np.full(len(idx), 72.0) + 0.3 * np.cos(np.arange(len(idx)))
    occ = (idx.dayofweek < 5) & (idx.hour >= 6) & (idx.hour < 18)
    fan = occ.astype(float)
    # economizer below the high limit: the OA fraction that mixes to 55 F, else the minimum
    with np.errstate(divide="ignore", invalid="ignore"):
        need = np.clip((rat - 55.0) / (rat - oat), _MIN_OAF, 1.0)
    cmd_oaf = np.where((oat >= _LOW_LIMIT_F) & (oat < 60.0), need, _MIN_OAF)
    damper = np.where(occ, 10.0 + 90.0 * (cmd_oaf - _MIN_OAF) / (1.0 - _MIN_OAF), 0.0)
    oaf = cmd_oaf if stuck_oaf is None else np.full(len(idx), stuck_oaf)
    mat = np.where(occ, oaf * oat + (1.0 - oaf) * rat, rat - 1.0)
    valve = np.where(occ, np.clip((mat - 55.5) * 8.0, 0.0, 100.0), 0.0)
    sat = np.where(occ, mat - valve / 8.0 + 1.0, mat)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),
            Role.OA_DAMPER: damper,
            Role.COOL_VALVE: valve,
            Role.SUPPLY_FAN_STATUS: fan,
            Role.SUPPLY_FAN_SPEED: 0.8 * fan,
            Role.OCCUPANCY: fan,
        },
        index=idx,
    )


def standin(store) -> None:
    """ds-lbnl-sdahu with the default subset's scored equipment (no spliced onset run)."""
    idx = hourly_index(days=28)
    frames = {
        "AHU__fault_free": ("AHU", _ahu(idx, None)),
        # the leak run's economizer behaves normally: its fault is the coil valve, not the damper
        "AHU__coi_leakage_010": ("AHU", _ahu(idx, None)),
        **{f"AHU__{k}": ("AHU", _ahu(idx, v)) for k, v in _STUCK_OAF.items()},
    }
    write_standin(store, "lbnl-sdahu", frames, labels=_LABELS)
    # ds-irish-ahu: a real unit's economizer, held at 100 % outdoor air for a day and a half of a
    # warm spell (a documented operating decision, not a fault of the controls)
    write_standin(store, "irish-ahu", {"AHU__ahu": ("AHU", irish(idx, full_oa=(24.0, 25.65)))})


def _stuck_closed_miss_more(ctx) -> None:
    """The dampers stuck near minimum run mechanical cooling in free-cooling weather far more
    often than the fault-free unit: at least twice its missed share."""
    base = ctx.finding("free_cooling_missed", "AHU__fault_free")
    assert base is not None, "no free_cooling_missed finding on AHU__fault_free"
    for eq in ("AHU__damper_stuck_010", "AHU__damper_stuck_025"):
        f = ctx.finding("free_cooling_missed", eq)
        assert f is not None, f"no free_cooling_missed finding on {eq}"
        got, ref = f.metrics["missed_pct"], base.metrics["missed_pct"]
        assert got >= 2 * ref, f"{eq} missed {got}% vs fault-free {ref}% (expected >= 2x)"


def _low_limit(ctx):
    """Step 5: ``econ.json`` re-run with the unit's economizer low-limit lockout set on
    ``free_cooling_missed`` (``"low_limit_f": 33.8``), as the page has learners do."""
    cache = ctx.__dict__.setdefault("_air_economizer", {})
    if "low" not in cache:
        cfg = ctx.config()
        rule = next(r for r in cfg["rules"] if r.get("name") == "free_cooling_missed")
        assert "low_limit_f" not in rule["params"], "the exercise config already sets low_limit_f"
        rule["params"]["low_limit_f"] = _LOW_LIMIT_F
        cache["low"] = run_config(cfg, base_dir=ctx.store)
    return {f.equip: f for f in cache["low"].findings if f.rule == "free_cooling_missed"}


def _lockout_explains_fault_free(ctx) -> None:
    """Step 5: most of the fault-free unit's missed hours are below the lockout (damper commanded
    at its minimum); without the lockout it is a fault, with it it reads ok."""
    base = ctx.finding("free_cooling_missed", "AHU__fault_free")
    assert base.severity == "fault", (base.severity, base.metrics["missed_pct"])
    before = base.metrics
    assert before["missed_cause"] == "economizer_not_commanded", before
    after = _low_limit(ctx)["AHU__fault_free"]
    assert after.severity == "ok", (after.severity, after.metrics)
    m = after.metrics
    missed = before["missed_pct"] / 100 * before["n_free_cooling_hours"]
    below = m["low_limit_cooling_hours"]
    assert below / missed >= 0.9, f"{below} of {missed:.0f} missed hours below the lockout"
    if ctx.mode == REAL:
        assert abs(m["missed_pct"] - 3.63) <= 0.1, m["missed_pct"]
        assert (round(missed), below) == (965, 899.0), (missed, below)
        assert m["low_limit_excluded_hours"] == 1014.0, m


def _lockout_keeps_stuck_caught(ctx) -> None:
    """Step 5: with the lockout set, the dampers stuck near minimum are still a fault."""
    low = _low_limit(ctx)
    for eq in ("AHU__damper_stuck_010", "AHU__damper_stuck_025"):
        f = low[eq]
        assert f.severity == "fault", (eq, f.severity, f.metrics["missed_pct"])
        assert f.metrics["missed_cause"] == "damper_not_delivering", (eq, f.metrics)
        if ctx.mode == REAL:
            assert f.metrics["missed_pct"] == 100.0, (eq, f.metrics["missed_pct"])


def _excess_oa(ctx, start: str, end: str) -> float:
    cfg = ctx.config("irish")
    cfg["source"].update({"start": start, "end": end})
    res = run_config(cfg, base_dir=ctx.store)
    f = next(f for f in res.findings if f.rule == "outdoor_air_fraction" and f.equip == "AHU__ahu")
    return float(f.metrics["excess_oa_pct"])


def _irish_by_period(ctx) -> None:
    """The Irish unit's excess outdoor air is not only the documented COVID-19 100 % OA period:
    the years before it (2017-06 to 2020-02) show more of it than the period itself."""
    before = _excess_oa(ctx, "2017-06-01", "2020-03-01")
    covid = _excess_oa(ctx, "2020-08-01", "2021-12-01")
    assert abs(before - 53.3) <= 0.5, f"2017-06..2020-02 excess OA {before}% (pinned 53%)"
    assert abs(covid - 44.8) <= 0.5, f"2020-08..2021-11 excess OA {covid}% (pinned 45%)"


EXERCISE = Exercise(
    id="air-economizer",
    title="Economizer: a stuck outdoor-air damper and missed free cooling",
    issue=80,
    references=("pnnl-guide-economizer", "pnnl-retuning-ch6", "pnnl-guide-min-oa"),
    datasets=("lbnl-sdahu", "irish-ahu"),
    runs=(
        Run(dataset="lbnl-sdahu", config="air-economizer"),
        Run(dataset="irish-ahu", name="irish"),
    ),
    commands=(
        "camber datasets fetch lbnl-sdahu",
        "camber datasets ingest lbnl-sdahu --store lab_store",
        "camber datasets config lbnl-sdahu --exercise air-economizer --store lab_store "
        "--out econ.json",
        "camber run econ.json --out econ_out",
        "camber datasets score lbnl-sdahu --store lab_store --findings econ_out/findings.json",
        "camber datasets fetch irish-ahu",
        "camber datasets ingest irish-ahu --store lab_store",
        "camber datasets config irish-ahu --store lab_store --out irish.json",
        "camber run irish.json --out irish_out",
    ),
    expect=(
        # stuck open: too much outdoor air in cooling weather, and above the high limit
        Finding("outdoor_air_fraction", "AHU__damper_stuck_075", severity=("fault",)),
        Finding("outdoor_air_fraction", "AHU__damper_stuck_100_short", severity=("fault",)),
        Finding("economizer_high_limit", "AHU__damper_stuck_075", severity=("fault",)),
        Finding("economizer_high_limit", "AHU__damper_stuck_100_short", severity=("fault",)),
        Metric(
            "outdoor_air_fraction",
            "AHU__damper_stuck_075",
            "median_oaf_cooling",
            67.5,
            1.0,
            quote="67.5%",
        ),
        # the fault-free unit sits at its own 1.6 % minimum in cooling weather
        Finding("outdoor_air_fraction", "AHU__fault_free", present=False),
        Finding("economizer_high_limit", "AHU__fault_free", present=False),
        Metric("outdoor_air_fraction", "AHU__fault_free", "median_oaf_cooling", 1.6, 0.2),
        # stuck near minimum: invisible to the OA-fraction rules ...
        Finding("outdoor_air_fraction", "AHU__damper_stuck_010", present=False),
        Finding("outdoor_air_fraction", "AHU__damper_stuck_025", present=False),
        # ... but the free cooling it can't deliver shows up as mechanical cooling
        Finding("free_cooling_missed", "AHU__damper_stuck_010", severity=("fault",)),
        Finding("free_cooling_missed", "AHU__damper_stuck_025", severity=("fault",)),
        # the fault-free unit is a fault: its coil runs below the low-limit lockout (step 5)
        Finding("free_cooling_missed", "AHU__fault_free", severity=("fault",)),
        Check("stuck-closed dampers miss free cooling far more", _stuck_closed_miss_more),
        Metric(
            "free_cooling_missed",
            "AHU__damper_stuck_025",
            "missed_pct",
            100.0,
            0.1,
            on=REAL,
            quote="100%",
        ),
        Metric(
            "free_cooling_missed",
            "AHU__fault_free",
            "missed_pct",
            34.1,
            0.5,
            on=REAL,
            quote="34.1%",
        ),
        # step 5: the low-limit lockout explains the fault-free fault and keeps the stuck dampers
        Check(
            "the lockout explains the fault-free fault", _lockout_explains_fault_free, quote="3.63%"
        ),
        Check(
            "with the lockout the stuck dampers stay caught",
            _lockout_keeps_stuck_caught,
            on=BOTH,
            quote="100%",
        ),
        # the label score: outdoor_air_fraction finds 2 of the 4 stuck dampers, no false alarm
        Score("outdoor_air_fraction", tpr=0.5, fpr=0.0, quote="TPR 50%", on=BOTH),
        Score(None, tpr=0.4, fpr=0.0, quote="TPR 40%"),
        # irish-ahu: a real unit, unlabelled -- excess OA in warm weather, economizer not locked
        # out above the high limit, and little mechanical cooling in free-cooling weather
        Finding("outdoor_air_fraction", "AHU__ahu", severity=("warn",), run="irish"),
        Metric(
            "outdoor_air_fraction",
            "AHU__ahu",
            "excess_oa_pct",
            42.4,
            0.5,
            run="irish",
            on=REAL,
            quote="42%",
        ),
        Finding("economizer_high_limit", "AHU__ahu", severity=("warn",), run="irish"),
        Metric(
            "economizer_high_limit",
            "AHU__ahu",
            "not_locked_out_pct",
            20.6,
            0.5,
            run="irish",
            on=REAL,
            quote="21%",
        ),
        Finding("free_cooling_missed", "AHU__ahu", severity=("ok",), run="irish"),
        Metric(
            "free_cooling_missed",
            "AHU__ahu",
            "missed_pct",
            8.0,
            0.1,
            run="irish",
            on=REAL,
            quote="8.0%",
        ),
        Check("irish excess OA by period", _irish_by_period, on=REAL, quote="53%"),
    ),
    standin=standin,
)
