"""0.98 (#92): condenser_water_reset derives its wet-bulb at the site elevation, and one config key
(``site_elevation_ft``) feeds every rule and drift detector that derives a wet-bulb."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.condenserwater import analyze_cw_reset  # noqa: E402
from camber.config import _with_site_elevation, run_config, site_elevation_ft  # noqa: E402
from camber.coolingtower import stull_wetbulb_f  # noqa: E402
from camber.driftrun import build_drift_suite  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.condenserwater_rule import CondenserWaterReset  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

FT_1600M = 1600 / 0.3048  # ~5,249 ft


def _hot_dry(n=24 * 21, *, elevation_ft=None, approach=7.0):
    """A tower whose CW supply resets 1:1 with the wet-bulb at ``elevation_ft`` (hot, dry air)."""
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    h = np.arange(n)
    oat = 85.0 + 12.0 * np.sin(h / 24.0 * 2 * np.pi) + 4.0 * np.sin(h / (24.0 * 7) * 2 * np.pi)
    rh = 18.0 + 8.0 * np.cos(h / 24.0 * 2 * np.pi)
    wb = np.asarray(stull_wetbulb_f(oat, rh, elevation_ft=elevation_ft), dtype=float)
    cws = wb + approach
    return pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: cws,
            Role.CW_RETURN_TEMP: cws + 10.0,
            Role.OAT: oat,
            Role.OUTDOOR_RH: rh,
        },
        index=idx,
    )


def _legacy(fr):
    return fr.rename(
        columns={
            Role.CW_SUPPLY_TEMP: "CWS_Temp",
            Role.CW_RETURN_TEMP: "CWR_Temp",
            Role.OAT: "OAT",
            Role.OUTDOOR_RH: "RH",
        }
    )


def test_sea_level_default_is_unchanged():
    fr = _hot_dry()
    res = analyze_cw_reset(_legacy(fr), "CT")
    assert res.wetbulb_source == "derived"
    assert res.cws_slope_per_wetbulb == pytest.approx(1.0, abs=0.01)
    f = CondenserWaterReset().analyze("CT", fr)
    assert f.severity == "ok" and f.metrics["cws_slope_per_wetbulb"] == pytest.approx(1.0, abs=0.01)
    # the only change at the default: a derived wet-bulb with no elevation is caveated
    assert any("sea-level" in c and "elevation_ft" in c for c in f.caveats)


def test_at_1600m_the_elevation_moves_the_x_axis():
    fr = _hot_dry(elevation_ft=FT_1600M)  # the tower really tracks the 1,600 m wet-bulb
    sea = analyze_cw_reset(_legacy(fr), "CT")
    site = analyze_cw_reset(_legacy(fr), "CT", elevation_ft=FT_1600M)
    assert site.cws_slope_per_wetbulb == pytest.approx(1.0, abs=0.01)
    # read at sea level the wet-bulb is too high, and by more in the drier hours: the slope moves
    assert abs(sea.cws_slope_per_wetbulb - site.cws_slope_per_wetbulb) > 0.02
    # a measured barometric pressure does the same as the elevation
    p = analyze_cw_reset(_legacy(fr), "CT", pressure_psia=12.04)
    assert p.cws_slope_per_wetbulb == pytest.approx(site.cws_slope_per_wetbulb, abs=0.02)

    f_sea = CondenserWaterReset().analyze("CT", fr)
    f_site = CondenserWaterReset(elevation_ft=FT_1600M).analyze("CT", fr)
    assert f_site.metrics["cws_slope_per_wetbulb"] == pytest.approx(1.0, abs=0.01)
    assert f_site.metrics["cws_slope_per_wetbulb"] != f_sea.metrics["cws_slope_per_wetbulb"]
    assert not f_site.caveats
    assert not CondenserWaterReset(pressure_psia=12.04).analyze("CT", fr).caveats


def test_reset_present_can_flip_near_the_flat_threshold():
    fr = _hot_dry(elevation_ft=FT_1600M)
    sea = CondenserWaterReset().analyze("CT", fr).metrics["cws_slope_per_wetbulb"]
    site = (
        CondenserWaterReset(elevation_ft=FT_1600M)
        .analyze("CT", fr)
        .metrics["cws_slope_per_wetbulb"]
    )
    thr = (sea + site) / 2.0  # a threshold between the two readings
    a = CondenserWaterReset(reset_slope_flat=thr).analyze("CT", fr)
    b = CondenserWaterReset(reset_slope_flat=thr, elevation_ft=FT_1600M).analyze("CT", fr)
    assert a.metrics["reset_present"] != b.metrics["reset_present"]


def test_measured_wetbulb_ignores_elevation_and_is_not_caveated():
    fr = _hot_dry()
    fr[Role.WETBULB_TEMP] = fr[Role.CW_SUPPLY_TEMP] - 7.0
    a = CondenserWaterReset().analyze("CT", fr)
    b = CondenserWaterReset(elevation_ft=FT_1600M).analyze("CT", fr)
    assert a.metrics == b.metrics and a.metrics["wetbulb_source"] == "measured"
    assert not a.caveats


# ------------------------------------------------------------------ the config key


def test_site_elevation_key_validation():
    assert site_elevation_ft({}) is None
    assert site_elevation_ft({"site_elevation_ft": 5249}) == 5249.0
    for bad in ("5249", True, float("nan"), 20000, -2000):
        with pytest.raises(ValueError, match="site_elevation_ft"):
            site_elevation_ft({"site_elevation_ft": bad})


def test_site_elevation_reaches_only_rules_that_take_it():
    assert _with_site_elevation("condenser_water_reset", {}, 5249.0) == {"elevation_ft": 5249.0}
    assert _with_site_elevation("cooling_tower_approach", {}, 5249.0) == {"elevation_ft": 5249.0}
    # a rule's own value wins, and a measured pressure is never overridden
    assert _with_site_elevation("cooling_tower_approach", {"elevation_ft": 10.0}, 5249.0) == {
        "elevation_ft": 10.0
    }
    assert _with_site_elevation("condenser_water_reset", {"pressure_psia": 12.0}, 5249.0) == {
        "pressure_psia": 12.0
    }
    assert _with_site_elevation("simultaneous_heat_cool", {}, 5249.0) == {}
    assert _with_site_elevation("condenser_water_reset", {}, None) == {}


def test_drift_detectors_take_the_site_elevation():
    for fam in ("tower", "condenser", "chiller"):
        suite = build_drift_suite(fam, BaselineStore(), elevation_ft=5249.0)
        towers = [r for r in suite if hasattr(r, "elevation_ft")]
        assert towers and all(r.elevation_ft == 5249.0 for r in towers), fam
        assert all(
            r.elevation_ft is None
            for r in build_drift_suite(fam, BaselineStore())
            if hasattr(r, "elevation_ft")
        )
    # the families without a wet-bulb detector are untouched
    assert not any(
        hasattr(r, "elevation_ft")
        for r in build_drift_suite("pump", BaselineStore(), elevation_ft=5249.0)
    )


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _plant_config(tmp_path):
    folder = tmp_path / "trends"
    folder.mkdir()
    fr = _hot_dry(elevation_ft=FT_1600M)
    cols = {
        Role.CW_SUPPLY_TEMP: "CWS",
        Role.CW_RETURN_TEMP: "CWR",
        Role.OAT: "OAT",
        Role.OUTDOOR_RH: "RH",
    }
    for role, name in cols.items():
        _write_point(folder, "CT_1", name, fr[role])
    _write_point(folder, "CT_1", "FAN", pd.Series(95.0, index=fr.index))
    return {
        "site": "AltitudeHQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {
            "aliases": {
                "CWS": "cw_supply_temp",
                "CWR": "cw_return_temp",
                "OAT": "oat",
                "RH": "outdoor_rh",
                "FAN": "tower_fan_speed",
            }
        },
        "equipment": [{"class": "CT", "marker": "CWS"}],
        "rules": ["condenser_water_reset", {"name": "cooling_tower_approach", "params": {}}],
    }


def test_config_site_elevation_feeds_the_plant_rules(tmp_path):
    cfg = _plant_config(tmp_path)
    sea = {f.rule: f for f in run_config(cfg, base_dir=str(tmp_path)).findings}
    cfg["site_elevation_ft"] = FT_1600M
    site = {f.rule: f for f in run_config(cfg, base_dir=str(tmp_path)).findings}
    cw_sea, cw_site = sea["condenser_water_reset"], site["condenser_water_reset"]
    assert any("sea-level" in c for c in cw_sea.caveats) and not cw_site.caveats
    assert cw_site.metrics["cws_slope_per_wetbulb"] == pytest.approx(1.0, abs=0.01)
    ct_sea, ct_site = sea["cooling_tower_approach"], site["cooling_tower_approach"]
    assert ct_sea.metrics["wetbulb_pressure_basis"] == "sea-level"
    assert ct_site.metrics["wetbulb_pressure_basis"] == "site"
    assert ct_site.metrics["approach_median_f"] == pytest.approx(7.0, abs=0.1)
    # a rule's own elevation wins over the site key
    cfg["rules"] = [{"name": "cooling_tower_approach", "params": {"elevation_ft": 0.0}}]
    (own,) = [f for f in run_config(cfg, base_dir=str(tmp_path)).findings]
    assert own.metrics["wetbulb_pressure_basis"] == "site"
    assert own.metrics["approach_median_f"] < ct_site.metrics["approach_median_f"] - 1.0
