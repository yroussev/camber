"""Zone-fleet rule fixes (0.108): the reheat-minimization floor and hours (#238), the cohort
starvation present-zone floor (#239), and the heating/cooling census on a large fleet (#241)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.cohort_starvation_rule import CohortStarvation  # noqa: E402
from camber.rules.reheat_min_rule import ReheatMinimization  # noqa: E402
from camber.rules.zones_rule import ZonesHeatCoolCensus  # noqa: E402

_WEEK15 = pd.date_range("2026-01-05", periods=5 * 96, freq="15min")  # Mon-Fri, 15-minute


def _box(idx, **cols):
    base = {Role.HEAT_VALVE: 0.0, Role.AIRFLOW: 300.0, Role.AIRFLOW_MIN_SP: 300.0}
    base.update(cols)
    return pd.DataFrame(base, index=idx)


# ---- #238 reheat_minimization_g36 --------------------------------------------------------------


def test_one_reheat_sample_is_not_rated():
    df = _box(_WEEK15)
    df.loc["2026-01-05 08:00", [Role.HEAT_VALVE, Role.AIRFLOW]] = [50.0, 450.0]
    f = ReheatMinimization().analyze("VAV1", df)
    assert f.severity == "info"
    assert f.metrics["n_reheat_samples"] == 1
    assert f.metrics["n_reheat_hours"] == 0.25  # one 15-minute sample, in hours
    assert any("min_reheat_hours" in c for c in f.caveats)


def test_reheat_hours_are_samples_times_the_interval():
    df = _box(_WEEK15)
    sl = slice("2026-01-05 08:00", "2026-01-05 11:45")  # 16 samples = 4 h
    df.loc[sl, Role.HEAT_VALVE] = 40.0
    df.loc[sl, Role.AIRFLOW] = 500.0
    f = ReheatMinimization().analyze("VAV1", df)
    assert f.metrics["n_reheat_samples"] == 16 and f.metrics["n_reheat_hours"] == 4.0
    assert f.severity == "fault"  # 4 h above the floor: rated, all of it above the minimum
    assert ReheatMinimization(min_reheat_hours=5.0).analyze("VAV1", df).severity == "info"


def test_missing_heating_loop_samples_are_dropped_not_read_as_low():
    df = _box(_WEEK15, **{Role.ZONE_HEAT_LOOP: 0.0})
    sl = slice("2026-01-05 08:00", "2026-01-05 12:00")
    df.loc[sl, Role.HEAT_VALVE] = 60.0
    df.loc[sl, Role.AIRFLOW] = 500.0
    df.loc[sl, Role.ZONE_HEAT_LOOP] = 80.0  # the high heating loop: above the minimum by design
    df.loc[df.loc[sl].index[::2], Role.ZONE_HEAT_LOOP] = np.nan
    f = ReheatMinimization().analyze("VAV1", df)
    assert f.severity == "ok" and f.metrics["reheat_above_min_pct"] == 0.0
    assert any("no heating-loop reading" in c for c in f.caveats)


# ---- #239 cohort_starvation --------------------------------------------------------------------


def _cohort_frames(outage_rows):
    idx = pd.date_range("2026-07-06", periods=4 * 7 * 24, freq="1h")
    frames = {}
    for z in ("VAV1", "VAV2", "VAV3", "VAV4"):
        df = pd.DataFrame(
            {
                Role.AIRFLOW: 400.0,
                Role.AIRFLOW_SP: 400.0,
                Role.DAMPER: 50.0,
                Role.SUPPLY_FAN_STATUS: 1.0,
            },
            index=idx,
        )
        if z == "VAV1":
            df[Role.DAMPER] = 100.0
            df[Role.AIRFLOW] = 150.0  # one starved box, always requesting
        else:
            df.iloc[:outage_rows, :3] = np.nan  # a logger outage on the other three
        frames[z] = df
    return frames


def test_one_box_reporting_through_an_outage_is_not_a_starved_cohort():
    frames = _cohort_frames(3 * 7 * 24)
    f = CohortStarvation("static", groups=dict.fromkeys(frames, "AHU1")).analyze_fleet(frames)
    assert f.severity != "warn" and f.metrics["starved_groups"] == []
    assert f.metrics["group_skipped_cycles"] == {"AHU1": 3 * 7 * 24}
    assert any("not judged" in c for c in f.caveats)


def test_a_lower_present_floor_restores_the_old_reading():
    frames = _cohort_frames(3 * 7 * 24)
    rule = CohortStarvation("static", groups=dict.fromkeys(frames, "AHU1"), min_present_frac=0.25)
    f = rule.analyze_fleet(frames)
    assert f.severity == "warn" and f.metrics["starved_groups"] == ["AHU1"]
    assert f.metrics["group_skipped_cycles"] == {}


# ---- #241 zones_heat_cool_census ---------------------------------------------------------------


def _fleet(n_boxes, heating):
    frames = {f"VAV{i:02d}": _box(_WEEK15, **{Role.AIRFLOW: 600.0}) for i in range(n_boxes)}
    for name in heating:
        frames[name][Role.HEAT_VALVE] = 8.0
        frames[name][Role.AIRFLOW] = 300.0
    return frames


def test_one_heating_box_in_a_large_fleet_is_at_most_warn_and_named():
    f = ZonesHeatCoolCensus().analyze_fleet(_fleet(40, ["VAV00"]))
    assert f.metrics["pct_hours_mixed"] == 100.0
    assert f.severity == "warn"
    assert f.metrics["persistent_heating_zones"] == ["VAV00"]
    assert f.metrics["driver_zones"] == ["VAV00"]
    assert "VAV00" in f.summary


def test_two_driving_boxes_cap_at_warn_even_above_the_share_floor():
    f = ZonesHeatCoolCensus(min_heating_frac=0.0).analyze_fleet(_fleet(12, ["VAV00", "VAV01"]))
    assert f.severity == "warn" and f.metrics["severity_capped"] == "drivers"
    assert f.metrics["pct_hours_mixed_without_drivers"] == 0.0


def test_many_heating_boxes_still_fault():
    heating = [f"VAV{i:02d}" for i in range(8)]
    f = ZonesHeatCoolCensus().analyze_fleet(_fleet(20, heating))
    assert f.severity == "fault" and f.metrics["severity_capped"] is None
    assert f.metrics["heating_frac_when_mixed"] == 0.4
    assert f.metrics["persistent_heating_zones"] == heating
