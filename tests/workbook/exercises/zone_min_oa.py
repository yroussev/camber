"""Answer key: workbook exercise ``zone-min-oa`` (docs/workbook/zone-min-oa.md).

Real-data figures were recorded from::

    camber datasets ingest lbnl-b59 --from-dir <the Dryad download directory> --store lab_store
    camber datasets config lbnl-b59 --exercise zone-min-oa --store lab_store --out b59.json
    camber run b59.json --out b59_out

and, for step 5, the same ``b59.json`` with ``"systems": {"RTU0N": {"ps": 32}}`` (each unit)
added to its ``ventilation`` section, run again with ``camber run``.
(CAMBER 0.97.0-dev, lbnl-b59 default subset, 2026-09-29. Dryad serves the files only to a
browser, so the entry is ``manual``: download them first, as ``camber datasets info lbnl-b59``
explains.)
"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
from _workbook import REAL, Check, Exercise, Finding, Run, hourly_index, write_standin

from camber.config import run_config
from camber.model.roles import Role

RTUS = ("RTU01", "RTU02", "RTU03", "RTU04")
# the CO2 zones and the rooftop unit the ingest (Brick model) puts each under
ZONES = (
    "RTU01_zone_022",
    "RTU02_zone_045",
    "RTU02_zone_052",
    "RTU03_zone_028",
    "RTU03_zone_033",
    "RTU03_zone_044",
    "RTU03_zone_058",
    "RTU03_zone_062",
    "RTU03_zone_072",
    "RTU04_zone_040",
    "RTU04_zone_068",
)
VOT_CFM = 1421.0  # the config's lumped zone: (5 x 63 + 0.06 x 12,513) / 0.75


def standin(store) -> None:
    """ds-lbnl-b59: four rooftop units and the 11 CO2 zones, four weeks, hourly.

    Each unit holds its OA damper at a fixed minimum all day and its flow station reads 5,500-
    7,500 cfm of outdoor air -- several times what 63 people and 12,513 ft2 of office need, so
    the zones' CO2 never leaves outdoor air by more than a few tens of ppm (no demand variation to
    test DCV against, and over-ventilated). The supply fans run around the clock (speed feedback,
    no status point). A mild coastal climate: outdoor air 54-78 F, above the 75 F economizer high
    limit only on warm afternoons.
    """
    rng = np.random.default_rng(3)
    idx = hourly_index(days=28)
    hour = idx.hour.to_numpy()
    oat = 66.0 + 12.0 * np.sin((hour - 9) / 24.0 * 2.0 * np.pi)
    frames: dict = {}
    for i, rtu in enumerate(RTUS):
        oa = 5500.0 + 650.0 * i + rng.normal(0.0, 100.0, len(idx))
        frames[rtu] = (
            "AHU",
            pd.DataFrame(
                {
                    Role.AIRFLOW: np.full(len(idx), 15000.0),
                    Role.OA_AIRFLOW: oa,
                    Role.OA_DAMPER: np.full(len(idx), 10.0),
                    Role.OAT: oat,
                    Role.RETURN_AIR_TEMP: np.full(len(idx), 72.0),
                    Role.MIXED_AIR_TEMP: 0.35 * oat + 0.65 * 72.0,
                    Role.SUPPLY_AIR_TEMP: np.full(len(idx), 60.0),
                    Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 60.0),
                    Role.SUPPLY_FAN_SPEED: np.full(len(idx), 60.0),
                },
                index=idx,
            ),
        )
    for z in ZONES:
        frames[z] = (
            "VAV",
            pd.DataFrame({Role.CO2: 425.0 + rng.normal(0.0, 8.0, len(idx))}, index=idx),
        )
    write_standin(store, "lbnl-b59", frames, labels={eq: "" for eq in frames})


def _per_system(ctx) -> dict:
    f = ctx.finding("ventilation_system_62_1", "<fleet>")
    assert f is not None, "no ventilation_system_62_1 finding"
    return f.metrics["per_system"]


def _all_over(ctx) -> None:
    """Every unit brings in far more than its system requirement: 'over', against the same
    Vot of 1,421 cfm (simplified Ev 0.75, D 1) and capped at warn by the assumed inputs."""
    per = _per_system(ctx)
    assert sorted(per) == list(RTUS), sorted(per)
    for rtu, s in per.items():
        assert s["status"] == "over", f"{rtu}: {s['status']}"
        assert s["severity"] == "warn", f"{rtu}: {s['severity']}"
        assert abs(s["required_cfm"] - VOT_CFM) < 1.0, f"{rtu}: Vot {s['required_cfm']}"
        req = s["requirement"]
        assert req["d"] == 1.0 and req["ev_cooling"] == 0.75, req


def _ratios_real(ctx) -> None:
    """Measured OA over Vot, by unit, on the real data (median over occupied, fan-on hours)."""
    want = {"RTU01": 4.801, "RTU02": 3.911, "RTU03": 4.778, "RTU04": 5.263}
    got = {k: v["ratio"] for k, v in _per_system(ctx).items()}
    for k, w in want.items():
        assert abs(got[k] - w) < 0.01, f"ratios {got}"


def _measured_real(ctx) -> None:
    """The measured OA flow behind those ratios."""
    got = {k: v["measured_cfm"] for k, v in _per_system(ctx).items()}
    assert abs(min(got.values()) - 5557.9) < 5 and abs(max(got.values()) - 7478.9) < 5, got


def _dcv_not_judged(ctx) -> None:
    """No unit's DCV can be judged: the zones' CO2 never varies enough (no false 'functioning')."""
    f = ctx.finding("dcv_system_verification", "<fleet>")
    assert f is not None, "no dcv_system_verification finding"
    per = f.metrics["per_ahu"]
    assert sorted(per) == list(RTUS), sorted(per)
    for rtu, s in per.items():
        assert s["status"] == "insufficient", f"{rtu}: {s['status']}"


def _dcv_reason_real(ctx) -> None:
    f = ctx.finding("dcv_system_verification", "<fleet>")
    for rtu, s in f.metrics["per_ahu"].items():
        assert s["reason"] == "no_demand_variation", f"{rtu}: {s['reason']}"


def _zones_over_ventilated(ctx) -> None:
    """All 11 CO2 zones read over-ventilated outside economizer hours."""
    f = ctx.finding("co2_ventilation_system", "<fleet>")
    assert f is not None, "no co2_ventilation_system finding"
    per = f.metrics["per_zone"]
    assert sorted(per) == sorted(ZONES), sorted(per)
    for z, s in per.items():
        assert s["severity"] == "warn" and "over-ventilated" in s["summary"], (z, s["summary"])


def _co2_medians_real(ctx) -> None:
    f = ctx.finding("co2_ventilation_system", "<fleet>")
    med = [s["co2_median_ppm"] for s in f.metrics["per_zone"].values()]
    assert min(med) == 419.0 and max(med) == 429.0, med


def _with_population(ctx) -> dict:
    """Step 5: the same config with a system population of 32 per unit (half the zone's)."""
    cfg = copy.deepcopy(ctx.config())
    cfg["ventilation"]["systems"] = {rtu: {"ps": 32} for rtu in RTUS}
    res = run_config(cfg, base_dir=ctx.store)
    f = next(f for f in res.findings if f.rule == "ventilation_system_62_1")
    return f.metrics["per_system"]


def _diversity(ctx) -> None:
    """With Ps = 32, D drops to about 0.51 and Vot to 1,366 cfm: every unit is still 'over'."""
    for rtu, s in _with_population(ctx).items():
        assert abs(s["required_cfm"] - 1365.5) < 1.0, f"{rtu}: Vot {s['required_cfm']}"
        assert s["status"] == "over", f"{rtu}: {s['status']}"


def _diversity_ratios_real(ctx) -> None:
    got = sorted(s["ratio"] for s in _with_population(ctx).values())
    assert abs(got[0] - 4.07) < 0.01 and abs(got[-1] - 5.477) < 0.01, got


EXERCISE = Exercise(
    id="zone-min-oa",
    title="Ventilation: minimum outdoor air and ASHRAE 62.1 system ventilation",
    issue=81,
    references=("pnnl-guide-min-oa", "pnnl-retuning-ch5"),
    datasets=("lbnl-b59",),
    runs=(Run(dataset="lbnl-b59", config="zone-min-oa"),),
    commands=(
        "camber datasets info lbnl-b59",
        "camber datasets ingest lbnl-b59 --from-dir b59_download --store lab_store",
        "camber datasets config lbnl-b59 --exercise zone-min-oa --store lab_store --out b59.json",
        "camber run b59.json --out b59_out",
    ),
    expect=(
        Finding("ventilation_system_62_1", "<fleet>", severity=("warn",)),
        Check("every unit over-ventilated against 1,421 cfm", _all_over, quote="1,421 cfm"),
        Check("OA / Vot ratios", _ratios_real, on=REAL, quote="3.91 to 5.26"),
        Check("measured OA", _measured_real, on=REAL, quote="5,558 to 7,479 cfm"),
        Check("a system population barely moves Vot", _diversity, quote="1,366 cfm"),
        Check(
            "ratios with a system population", _diversity_ratios_real, on=REAL, quote="4.07 to 5.48"
        ),
        Finding("dcv_system_verification", "<fleet>", severity=("info",)),
        Check("DCV not judged on any unit", _dcv_not_judged),
        Check("not judged because demand never varied", _dcv_reason_real, on=REAL),
        Finding("co2_ventilation_system", "<fleet>", severity=("warn",)),
        Check("every CO2 zone over-ventilated", _zones_over_ventilated),
        Check("zone CO2 medians", _co2_medians_real, on=REAL, quote="419 to 429 ppm"),
    ),
    standin=standin,
)
