"""0.93 (#37): DCV verification -- the CO2 lift within the hour of day, the OA-damper fallback
where OA flow is missing, and fan-speed / airflow proxies on a 100 % outdoor-air unit."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.faultlab import _idx, dcv_sim  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.ventilation_rule import (  # noqa: E402
    DcvSystemVerification,
    DemandControlledVentilation,
)
from camber.schedules import occupied_mask  # noqa: E402
from camber.ventilation import assess_dcv  # noqa: E402

IDX = pd.date_range("2025-06-02", periods=24 * 14, freq="1h")  # two weeks from a Monday

# ============================================================================ #37


def _clock_and_co2(days=28, seed=1):
    """A valve opened by a weekday 08-12 clock AND by CO2 above 1000 ppm; CO2 builds through the
    day, by a different amount each day (the B4B pattern: the morning clock opens the valve at
    low CO2, which drags the pooled raised-valve CO2 down)."""
    idx = pd.date_range("2025-03-03", periods=24 * days, freq="1h")
    rng = np.random.default_rng(seed)
    busy = rng.random(days)[(idx.normalize() - idx[0].normalize()).days]  # per-day occupancy
    h = idx.hour
    occ = h.isin(range(8, 18)) & (idx.dayofweek < 5)
    ramp = np.clip((h - 8) / 8.0, 0, 1)
    co2 = 450.0 + occ * (ramp * (300.0 + 600.0 * busy) + 100.0) + rng.normal(0, 10, len(idx))
    clock = h.isin(range(8, 12)) & (idx.dayofweek < 5)
    oa = np.where(clock | (co2 > 1000.0), 100.0, 20.0)
    return pd.Series(oa, idx), pd.Series(co2, idx), idx


def test_hour_of_day_lift_sees_co2_response_behind_a_clock():
    oa, co2, idx = _clock_and_co2()
    occ = occupied_mask(idx)
    r = assess_dcv(oa, co2, occupied_mask=occ)
    assert r.lift_basis == "hour_of_day" and r.status == "functioning"
    assert r.demand_lift_pooled < 50.0 < r.demand_lift  # the clock hides it from a pooled lift
    pooled = assess_dcv(oa, co2, occupied_mask=occ, stratify_hour=False)
    assert pooled.lift_basis == "pooled" and pooled.status == "uncorrelated"
    assert pooled.demand_lift == r.demand_lift_pooled


def test_pure_time_clock_reads_uncorrelated_within_the_hour():
    idx = pd.date_range("2025-03-03", periods=24 * 28, freq="1h")
    rng = np.random.default_rng(2)
    busy = rng.random(28)[(idx.normalize() - idx[0].normalize()).days]
    occ = idx.hour.isin(range(8, 18)) & (idx.dayofweek < 5)
    co2 = pd.Series(450.0 + occ * (150.0 + 700.0 * busy) + rng.normal(0, 10, len(idx)), idx)
    # opened on busy DAYS by a calendar, not by CO2 -- within an hour it tracks the day, not CO2
    oa = pd.Series(np.where(occ & (rng.random(len(idx)) > 0.5), 100.0, 20.0), idx)
    r = assess_dcv(oa, co2, occupied_mask=occupied_mask(idx))
    assert r.lift_basis == "hour_of_day" and r.status == "uncorrelated"


def test_thin_strata_fall_back_to_pooled_with_a_caveat():
    f = dcv_sim(_idx(21), control="proportional")
    got = DemandControlledVentilation().analyze("AHU-1", f)
    assert got.metrics["status"] == "functioning"
    if got.metrics["lift_basis"] == "pooled":
        assert any("pooled across hours" in c for c in got.caveats)


def test_damper_judges_where_oa_flow_is_missing():
    f = dcv_sim(_idx(42), control="proportional", economizer=False)
    f[Role.OA_DAMPER] = 10.0 + 90.0 * (f[Role.OA_AIRFLOW] - 720.0).clip(lower=0) / 300.0
    half = f.index < f.index[len(f) // 2]
    f.loc[half, Role.OA_AIRFLOW] = np.nan  # a masked flow station for the first three weeks
    got = DemandControlledVentilation().analyze("AHU-1", f)
    segs = got.metrics["oa_segments"]
    assert [s["oa_signal"] for s in segs] == ["oa_airflow", "oa_damper"]
    assert all(s["status"] == "functioning" for s in segs)
    assert segs[1]["end"] <= segs[0]["start"]
    assert "where the better OA signal is missing" in got.summary
    assert any("where OA flow is missing" in c for c in got.caveats)
    # one signal only: no segment list
    assert (
        "oa_segments"
        not in DemandControlledVentilation()
        .analyze("AHU-1", f.drop(columns=[Role.OA_DAMPER]))
        .metrics
    )


def test_fan_speed_proxy_on_a_declared_100pct_oa_unit():
    f = dcv_sim(_idx(21), control="proportional", economizer=False)
    unit = f.drop(columns=[Role.OA_AIRFLOW, Role.ECON_CMD]).assign(
        **{Role.SUPPLY_FAN_SPEED.value: 100.0 * f[Role.OA_AIRFLOW] / 1020.0}
    )
    unit.columns = [Role(c) if not isinstance(c, Role) else c for c in unit.columns]
    assert DemandControlledVentilation().analyze("DOAS-1", unit) is None  # not declared
    got = DemandControlledVentilation(full_outdoor_air=True).analyze("DOAS-1", unit)
    assert got.metrics["oa_signal"] == "supply_fan_speed"
    assert got.metrics["economizer_basis"] == "full_outdoor_air"
    assert got.metrics["status"] == "functioning"
    assert any("100% outdoor-air unit" in c for c in got.caveats)
    # the supply airflow outranks the fan speed
    unit[Role.AIRFLOW] = f[Role.OA_AIRFLOW]
    got = DemandControlledVentilation(full_outdoor_air=True).analyze("DOAS-1", unit)
    assert got.metrics["oa_signal"] == "airflow"


def test_system_rule_uses_the_fallback_too():
    sim = dcv_sim(_idx(42), control="proportional", economizer=False)
    ahu = sim.drop(columns=[Role.CO2])
    ahu[Role.OA_DAMPER] = 10.0 + 90.0 * (ahu[Role.OA_AIRFLOW] - 720.0).clip(lower=0) / 300.0
    ahu.loc[ahu.index < ahu.index[len(ahu) // 2], Role.OA_AIRFLOW] = np.nan
    frames = {"AHU_1": ahu, "AHU_1_VAV_1": pd.DataFrame({Role.CO2: sim[Role.CO2]})}
    got = DcvSystemVerification().analyze_fleet(
        frames, topology=Topology.from_parent_map({"AHU_1_VAV_1": "AHU_1"})
    )
    assert len(got.metrics["per_ahu"]["AHU_1"]["oa_segments"]) == 2
