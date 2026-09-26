"""Tests for DCV verification (camber.ventilation.assess_dcv + the single and fleet DCV rules).

Most fixtures come from :func:`camber.faultlab.dcv_sim`, a well-mixed zone CO₂ mass balance under
proportional, integral (PI) or static DCV with an economizer -- so the verdicts are checked against
the physics the test is meant to see through, not against hand-drawn curves.
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.faultlab import _idx, dcv_sim  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.ventilation_rule import (  # noqa: E402
    DcvSystemVerification,
    DemandControlledVentilation,
)
from camber.schedules import occupied_mask  # noqa: E402
from camber.ventilation import DcvResult, assess_dcv, economizer_active_mask  # noqa: E402

IDX = _idx(21)


def _sim(**kw):
    return dcv_sim(IDX, **kw)


def _judge(f, *, econ="cmd", **kw):
    """assess_dcv over a simulated frame, occupied hours, with the chosen economizer evidence."""
    mask = occupied_mask(f.index)
    em = None
    if econ == "cmd":
        em, _ = economizer_active_mask(f.index, econ_cmd=f[Role.ECON_CMD])
    elif econ == "inferred":
        em, _ = economizer_active_mask(f.index, oat=f[Role.OAT], heat_valve=f[Role.HEAT_VALVE])
    return assess_dcv(f[Role.OA_AIRFLOW], f[Role.CO2], occupied_mask=mask, economizer_mask=em, **kw)


# --------------------------------------------------------------------------- economizer mask


def test_economizer_mask_bases():
    idx = pd.date_range("2025-07-07", periods=4, freq="1h")
    cmd = pd.Series([0.0, 0.3, 1.0, np.nan], index=idx)
    m, basis = economizer_active_mask(idx, econ_cmd=cmd)
    # any nonzero hourly mean is a partial economizer hour; a gap is conservatively "active"
    assert basis == "econ_cmd" and m.tolist() == [False, True, True, True]

    oat = pd.Series([40.0, 60.0, 80.0, 60.0], index=idx)
    hv = pd.Series([30.0, 0.0, 0.0, 10.0], index=idx)
    m, basis = economizer_active_mask(idx, oat=oat, heat_valve=hv)
    # heating or above the 75F high limit -> known non-economizing; mild + no heat -> maybe
    assert basis == "oat_heat_valve" and m.tolist() == [False, True, False, False]

    m, basis = economizer_active_mask(idx, oat=oat)
    assert basis == "oat" and m.tolist() == [True, True, False, True]

    assert economizer_active_mask(idx) == (None, "none")


# --------------------------------------------------------------------------- assess_dcv verdicts


def test_proportional_dcv_functioning():
    r = _judge(_sim(control="proportional", economizer=False), econ=None)
    assert r.status == "functioning" and r.demand_lift > 100


def test_working_dcv_with_economizer_functioning_once_economizer_excluded():
    f = _sim(control="proportional", economizer=True)
    r = _judge(f)
    assert r.status == "functioning" and r.n_econ_excluded > 0 and r.econ_excluded is True
    # the old failure mode: judged with the economizer in, OA follows OAT and reads unrelated
    assert _judge(f, econ=None).status == "uncorrelated"


def test_static_dcv_with_economizer_reads_static():
    """The key detection: the economizer's range used to hide a DCV stuck at design OA."""
    f = _sim(control="static", economizer=True)
    r = _judge(f)
    assert r.status == "static" and r.modulation < 0.1
    assert _judge(f, econ=None).status != "static"  # why the exclusion is needed


def test_integral_dcv_functioning():
    """A PI loop holds CO2 under setpoint with OA at its floor much of the time; conditioning
    demand on the OA-raised samples still sees the response."""
    for econ in (False, True):
        r = _judge(_sim(control="pi", economizer=econ), econ="cmd" if econ else None)
        assert r.status == "functioning", (econ, r)


def test_integral_dcv_with_inferred_economizer():
    r = _judge(_sim(control="pi", economizer=True), econ="inferred")
    assert r.status == "functioning"


def test_inferred_economizer_is_honest_when_it_leaves_too_little():
    # OAT + heating valve keeps only heating / hot hours; a proportional reset rarely rises there
    r = _judge(_sim(control="proportional", economizer=True), econ="inferred")
    assert r.status == "insufficient" and r.reason == "oa_rarely_raised"


def test_lightly_occupied_building_is_insufficient_not_static():
    r = _judge(_sim(control="proportional", economizer=False, occupancy_scale=0.3), econ=None)
    assert r.status == "insufficient" and r.reason == "demand_below_engage"


def test_flat_demand_is_insufficient_not_uncorrelated():
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    oa = pd.Series(np.linspace(700, 1000, 48), index=idx)
    r = assess_dcv(oa, pd.Series(900.0, index=idx))
    assert r.status == "insufficient" and r.reason == "no_demand_variation"


def test_too_few_samples():
    idx = pd.date_range("2025-07-07", periods=2, freq="1h")
    r = assess_dcv(pd.Series([1.0, 2.0], index=idx), pd.Series([400.0, 500.0], index=idx))
    assert r.status == "insufficient" and r.reason == "too_few_samples"


def test_one_spike_does_not_flip_static():
    f = _sim(control="static", economizer=False)
    occ_rows = np.flatnonzero(occupied_mask(f.index).to_numpy())
    f.iloc[occ_rows[40], f.columns.get_loc(Role.OA_AIRFLOW)] = 6000.0
    assert _judge(f, econ=None).status == "static"


def test_unflagged_warmup_closure_does_not_fake_a_response():
    """A damper shut 07-08 with no WARMUP flag used to make a static DCV read "functioning"."""
    f = _sim(control="static", economizer=False, warmup_closure=True)
    r = _judge(f, econ=None)
    assert r.status == "static" and r.closed_pct > 5.0


def test_inverse_modulation_uncorrelated():
    f = _sim(control="proportional", economizer=False)
    f[Role.OA_AIRFLOW] = 1740.0 - f[Role.OA_AIRFLOW]  # OA falls as CO2 rises
    r = _judge(f, econ=None)
    assert r.status == "uncorrelated" and r.demand_lift < 0


def test_breach_at_minimum_uses_robust_minimum():
    idx = pd.date_range("2025-07-07", periods=60, freq="1h")
    oa = np.full(60, 300.0)
    oa[:6] = 900.0
    oa[10] = 0.0  # one closed sample: excluded, and no longer drags the "at minimum" band down
    co2 = np.full(60, 1200.0)
    r = assess_dcv(pd.Series(oa, index=idx), pd.Series(co2, index=idx), co2_setpoint=1000.0)
    assert r.co2_breach_at_min_pct > 50.0 and r.closed_pct > 0


def test_floor_subchecks():
    f = _sim(control="proportional", economizer=False)
    below = _judge(f, econ=None, oa_floor=900.0)  # a floor above the DCV's 720 cfm minimum
    assert below.below_floor_pct > 20.0
    ok = _judge(f, econ=None, oa_floor=720.0)
    assert ok.below_floor_pct == 0.0
    assert ok.excess_at_low_demand_pct is not None and ok.excess_at_low_demand_pct < 50.0
    none = _judge(f, econ=None)
    assert none.below_floor_pct is None and none.excess_at_low_demand_pct is None


def test_static_at_design_is_excess_at_low_demand():
    r = _judge(_sim(control="static", economizer=False), econ=None, oa_floor=720.0)
    assert r.excess_at_low_demand_pct == 100.0


def test_binary_occupancy_demand():
    idx = pd.date_range("2025-07-07", periods=24 * 21, freq="1h")
    busy = np.random.default_rng(5).random(len(idx)) < 0.6  # occupied some days/hours, not others
    occ = pd.Series(((idx.hour >= 8) & (idx.hour < 17) & busy).astype(float), index=idx)
    working = assess_dcv(700.0 + 300.0 * occ, occ)
    assert working.status == "functioning" and working.demand_lift == 1.0
    # a pure schedule (the same hours every day) cannot show occupancy response
    sched = pd.Series(((idx.hour >= 8) & (idx.hour < 17)).astype(float), index=idx)
    confounded = assess_dcv(700.0 + 300.0 * sched, sched)
    assert confounded.reason == "schedule_confounded"
    static = assess_dcv(pd.Series(1000.0, index=idx), occ)
    assert static.status == "static" and static.co2_breach_at_min_pct is None


def test_nan_gaps_tolerated():
    f = _sim(control="proportional", economizer=False)
    f.loc[f.index[::7], Role.CO2] = np.nan
    f.loc[f.index[::11], Role.OA_AIRFLOW] = np.nan
    assert _judge(f, econ=None).status == "functioning"


def test_negative_oa_offset_keeps_modulation_bounded():
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    r = assess_dcv(
        pd.Series(np.linspace(-50, 20, 48), index=idx), pd.Series(np.linspace(500, 1200, 48), idx)
    )
    assert np.isnan(r.modulation) or 0.0 <= r.modulation <= 1.0


def test_min_corr_is_deprecated():
    idx = pd.date_range("2025-07-07", periods=30, freq="1h")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assess_dcv(pd.Series(1.0, index=idx), pd.Series(800.0, index=idx), min_corr=0.3)
    assert any(issubclass(x.category, DeprecationWarning) for x in w)


def test_dcv_result_positional_backcompat():
    r = DcvResult("AHU-1", 10, 0.5, 0.2, "functioning", None)
    assert r.demand_lift is None and r.econ_excluded is None and r.closed_pct is None


# --------------------------------------------------------------------------- single-equipment rule


def test_rule_returns_none_for_zone_frame():
    f = _sim()[[Role.CO2]]  # a VAV zone: CO2 and no OA signal
    assert DemandControlledVentilation().analyze("VAV-1", f) is None


def test_rule_static_warn_and_working_ok():
    rule = DemandControlledVentilation()
    bad = rule.analyze("AHU-1", _sim(control="static"))
    assert bad.severity == "warn" and bad.metrics["status"] == "static"
    assert bad.metrics["economizer_basis"] == "econ_cmd"
    good = rule.analyze("AHU-1", _sim(control="proportional"))
    assert good.severity == "ok" and good.metrics["status"] == "functioning"
    assert any("return-air CO₂" in c for c in good.caveats)
    assert any("not evaluated" in c for c in good.caveats)  # no setpoint, no floor


def test_rule_without_economizer_evidence_downgrades_to_info():
    f = _sim(control="static").drop(columns=[Role.ECON_CMD, Role.OAT, Role.HEAT_VALVE])
    got = DemandControlledVentilation().analyze("AHU-1", f)
    assert got.metrics["status"] == "uncorrelated" and got.severity == "info"
    assert any("could not be excluded" in c for c in got.caveats)


def test_rule_uses_occupancy_and_fan_status():
    f = _sim(control="proportional")
    rule = DemandControlledVentilation()
    n_all = rule.analyze("AHU-1", f).metrics["n"]
    g = f.copy()
    g[Role.OCCUPANCY] = (g.index.day % 2).astype(float)  # a BAS point: every other day unoccupied
    assert rule.analyze("AHU-1", g).metrics["n"] < n_all
    h = f.copy()
    h.loc[h.index.hour == 10, Role.SUPPLY_FAN_STATUS] = 0.5  # partial-hour fan transitions
    assert rule.analyze("AHU-1", h).metrics["n"] < n_all


def test_rule_fault_thresholds():
    f = _sim(control="proportional", economizer=False)
    f[Role.OA_AIRFLOW] = 400.0 + 0.0 * f[Role.OA_AIRFLOW]
    f.loc[f.index.hour == 12, Role.OA_AIRFLOW] = 900.0  # some modulation, mostly pinned low
    f.loc[:, Role.CO2] = f[Role.CO2] + 300.0
    tight = DemandControlledVentilation(co2_setpoint=1000.0, breach_fault_pct=5.0)
    loose = DemandControlledVentilation(co2_setpoint=1000.0, breach_fault_pct=99.0)
    assert tight.analyze("AHU-1", f).severity == "fault"
    assert loose.analyze("AHU-1", f).severity != "fault"
    below = DemandControlledVentilation(oa_floor_cfm={"AHU-1": 720.0}).analyze("AHU-1", f)
    assert below.severity == "fault" and below.metrics["below_floor_pct"] > 50.0


def test_rule_floor_with_damper_only_is_declined_with_caveat():
    f = _sim(control="proportional").rename(columns={Role.OA_AIRFLOW: Role.OA_DAMPER})
    got = DemandControlledVentilation(oa_floor_cfm=720.0).analyze("AHU-1", f)
    assert got.metrics["below_floor_pct"] is None
    assert any("damper position" in c for c in got.caveats)


# --------------------------------------------------------------------------- fleet rule


def _fleet(controls: dict, *, zones_per_ahu=4, seed=0):
    """{AHU: control} -> frames: each AHU's OA frame + its VAV zones' CO2 frames."""
    frames, parents = {}, {}
    rng = np.random.default_rng(seed)
    for k, (ahu, control) in enumerate(controls.items()):
        sim = dcv_sim(IDX, control=control, seed=seed + k)
        frames[ahu] = sim.drop(columns=[Role.CO2])
        for j in range(zones_per_ahu):
            z = f"{ahu}_VAV_{j + 1}"
            scale = 0.7 + 0.3 * j / max(1, zones_per_ahu - 1)  # the last zone is the critical one
            co2 = 420.0 + (sim[Role.CO2] - 420.0) * scale + rng.normal(0, 5, len(sim))
            frames[z] = pd.DataFrame({Role.CO2: co2}, index=sim.index)
            parents[z] = ahu
    return frames, parents


def test_fleet_working_system_ok():
    frames, parents = _fleet({"AHU_1": "proportional"})
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    assert got.severity == "ok" and got.metrics["n_zones_joined"] == 4
    assert got.metrics["per_ahu"]["AHU_1"]["status"] == "functioning"


def test_fleet_attributes_the_faulty_air_handler():
    frames, parents = _fleet({"AHU_1": "proportional", "AHU_2": "static"})
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    per = got.metrics["per_ahu"]
    assert per["AHU_1"]["severity"] == "ok"
    assert per["AHU_2"]["severity"] == "warn" and per["AHU_2"]["status"] == "static"
    assert got.severity == "warn" and "AHU_2" in got.summary


def test_fleet_heuristic_grouping_caps_severity_and_caveats():
    frames, parents = _fleet({"AHU_1": "proportional"})
    rule = DcvSystemVerification(co2_setpoint=1000.0, breach_fault_pct=0.0)
    frames["AHU_1"][Role.OA_AIRFLOW] = 400.0  # pinned low: under-ventilated at minimum
    frames["AHU_1"].loc[frames["AHU_1"].index.hour == 12, Role.OA_AIRFLOW] = 900.0
    explicit = rule.analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    heuristic = rule.analyze_fleet(
        frames, topology=Topology.from_parent_map(parents, provenance="heuristic")
    )
    assert explicit.severity == "fault"
    assert heuristic.severity == "warn"
    assert any("naming" in c for c in heuristic.caveats)


def test_fleet_single_source_without_topology():
    frames, _ = _fleet({"AHU_1": "proportional"})
    got = DcvSystemVerification().analyze_fleet(frames, topology=None)
    assert got.metrics["grouping_provenance"] == "single_source"
    assert got.metrics["n_zones_joined"] == 4 and any("only OA source" in c for c in got.caveats)


def test_fleet_two_sources_without_topology_declines():
    frames, _ = _fleet({"AHU_1": "proportional", "AHU_2": "static"})
    got = DcvSystemVerification().analyze_fleet(frames, topology=None)
    assert got.severity == "info" and got.metrics["declined"] is True
    assert got.metrics["n_zones_unattributed"] == 8


def test_fleet_excludes_stuck_and_offset_zone_sensors():
    frames, parents = _fleet({"AHU_1": "proportional"})
    frames["AHU_1_VAV_1"][Role.CO2] = 1500.0  # stuck
    frames["AHU_1_VAV_2"][Role.CO2] = frames["AHU_1_VAV_2"][Role.CO2] + 600.0  # offset high
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    assert got.metrics["n_zones_excluded"] == 2 and got.metrics["n_zones_joined"] == 2
    assert got.severity == "ok"  # the stuck 1500 ppm zone did not become a false breach


def test_fleet_leaves_ahu_with_own_co2_to_single_rule():
    frames, parents = _fleet({"AHU_1": "proportional"})
    frames["AHU_1"][Role.CO2] = 800.0  # return-air CO2 on the air handler itself
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    assert got.metrics["n_oa_sources"] == 0 and got.severity == "info"


def test_fleet_mean_aggregation_and_bad_agg():
    frames, parents = _fleet({"AHU_1": "proportional"})
    got = DcvSystemVerification(agg="mean").analyze_fleet(
        frames, topology=Topology.from_parent_map(parents)
    )
    assert got.metrics["agg"] == "mean"
    with pytest.raises(ValueError):
        DcvSystemVerification(agg="median")


def test_fleet_through_registry(monkeypatch):
    """End to end: run_fleet resolves every equipment and auto-builds the naming topology."""
    import camber.rules.base as base
    from camber.resolve import EquipRef
    from camber.rules.builtin import builtin_registry

    frames, _ = _fleet({"AHU_1": "proportional", "AHU_2": "static"})
    refs = [
        EquipRef(equip=e, equip_class="AHU" if "VAV" not in e else "VAV", folder="f")
        for e in frames
    ]
    monkeypatch.setattr(
        base, "resolve", lambda ref, mapping, load, resample="1h": frames[ref.equip]
    )
    got = builtin_registry().run_fleet("dcv_system_verification", refs, mapping=None)
    assert got.metrics["grouping_provenance"] == "heuristic"
    assert got.metrics["per_ahu"]["AHU_2"]["status"] == "static"
    assert got.metrics["per_ahu"]["AHU_1"]["status"] == "functioning"


# --------------------------------------------------------------------------- real-data regressions
# Each test reproduces a defect found running 0.82.0-dev against open datasets (see CHANGELOG).


def _hourly(n=24 * 21, start="2025-07-07"):
    return pd.date_range(start, periods=n, freq="1h")


def test_occupant_count_demand_is_judged_not_discarded():
    """A 0..30 people count used to be read as CO2 and filtered out (n=0, too_few_samples)."""
    idx = _hourly()
    rng = np.random.default_rng(3)
    count = np.where(rng.random(len(idx)) < 0.3, 0, rng.integers(1, 30, len(idx)))  # some empty
    people = pd.Series(np.where(idx.hour.isin(range(9, 17)), count, 0), idx)
    oa = 700.0 + 25.0 * people  # OA follows the count
    r = assess_dcv(oa, people.astype(float), occupied_mask=occupied_mask(idx))
    assert r.demand_kind == "count" and r.n > 100
    assert r.status == "functioning" and r.demand_lift >= 1.0


def test_fractional_presence_is_presence_not_co2():
    """A PIR point averaged to 10-min / hourly means is fractional; it was dropped as CO2."""
    idx = _hourly()
    frac = pd.Series(np.where(idx.hour.isin(range(9, 17)), 0.7, 0.0), idx)
    frac[idx.hour == 12] = 0.4
    r = assess_dcv(700.0 + 400.0 * frac, frac, occupied_mask=occupied_mask(idx))
    assert r.demand_kind == "presence" and r.n > 100


def test_schedule_driven_oa_is_not_occupancy_dcv():
    """OA opened by a clock that happens to overlap occupancy must not read "functioning": the
    lift is taken within each hour of day, where a time clock shows none."""
    idx = _hourly()
    occ = pd.Series(idx.hour.isin(range(12, 17)).astype(float), idx)  # occupied 12-17
    oa = pd.Series(np.where(idx.hour.isin(range(7, 17)), 1000.0, 300.0), idx)  # open 07-17
    r = assess_dcv(oa, occ, occupied_mask=occupied_mask(idx))
    assert r.status == "insufficient" and r.reason == "schedule_confounded"
    assert r.raised_when_vacant_pct > 50


def test_microsecond_indexes_align():
    """Two regular us-unit indexes with different starts used to intersect to ~0 rows."""
    a = pd.date_range("2022-10-10", periods=3456, freq="10min", unit="us")
    b = pd.date_range("2022-10-12 16:10", periods=3013, freq="10min", unit="us")
    rng = np.random.default_rng(0)
    r = assess_dcv(
        pd.Series(rng.uniform(300, 900, len(a)), a), pd.Series(rng.uniform(450, 1200, len(b)), b)
    )
    assert r.n > 2500


def test_below_floor_survives_economizer_exclusion():
    """A damper held below the floor all day is under-ventilation even when every sample might
    be economizing -- it used to vanish into too_few_samples (the wildfire closure)."""
    idx = _hourly()
    oa = pd.Series(800.0, idx)
    co2 = pd.Series(700.0, idx)
    r = assess_dcv(oa, co2, oa_floor=2000.0, economizer_mask=pd.Series(True, idx))
    assert r.below_floor_pct == 100.0 and r.status == "insufficient"


def test_fan_off_while_occupied_with_high_co2_is_a_fault():
    """A lecture theatre at the CO2 sensor's 2000 ppm full scale with the fan off read
    "not judged" because the verdict excludes fan-off samples."""
    f = _sim(control="static", economizer=False)
    occ_rows = occupied_mask(f.index) & (f.index.dayofweek < 2)
    f.loc[occ_rows, Role.SUPPLY_FAN_STATUS] = 0.0
    f.loc[occ_rows, Role.OA_AIRFLOW] = 0.0
    f.loc[occ_rows, Role.CO2] = 2000.0
    got = DemandControlledVentilation().analyze("AHU-1", f)
    assert got.severity == "fault" and got.metrics["unventilated_high_co2_hours"] >= 4


def test_short_unventilated_blip_is_not_a_fault():
    f = _sim(control="proportional", economizer=False)
    blip = (
        occupied_mask(f.index) & (f.index.dayofyear == f.index[0].dayofyear) & (f.index.hour == 10)
    )
    f.loc[blip, [Role.SUPPLY_FAN_STATUS, Role.OA_AIRFLOW]] = 0.0
    f.loc[blip, Role.CO2] = 1500.0
    got = DemandControlledVentilation().analyze("AHU-1", f)
    assert got.metrics["unventilated_high_co2_hours"] == 1.0 and got.severity != "fault"


def test_celsius_oat_is_caveated():
    f = _sim(control="proportional").drop(columns=[Role.ECON_CMD, Role.HEAT_VALVE])
    f[Role.OAT] = (f[Role.OAT] - 32.0) / 1.8  # a BAS trending OAT in C
    got = DemandControlledVentilation().analyze("AHU-1", f)
    assert any("°C" in c for c in got.caveats)


def test_occupancy_point_replaces_weekday_schedule():
    """A 24/7 space with a trended occupancy point lost two-thirds of its samples to the default
    weekday 07-18 window."""
    idx = _hourly()
    co2 = pd.Series(600.0 + 400.0 * np.abs(np.sin(np.arange(len(idx)) / 5.0)), idx)
    f = pd.DataFrame({Role.CO2: co2, Role.OA_AIRFLOW: 700.0 + 0.8 * (co2 - 600.0)}, index=idx)
    base = DemandControlledVentilation().analyze("SZ-1", f).metrics["n"]
    f[Role.OCCUPANCY] = 1.0
    assert DemandControlledVentilation().analyze("SZ-1", f).metrics["n"] > 2 * base
    custom = DemandControlledVentilation(start_hour=0, end_hour=24, occupied_days=range(7))
    assert custom.analyze("SZ-1", f.drop(columns=[Role.OCCUPANCY])).metrics["n"] > 2 * base


def test_fleet_follows_brick_ahu_vav_zone_chain():
    """Brick chains AHU -> VAV -> zone; the fleet rule took the zone's direct parent (the VAV, not
    an OA source) and attributed 0 zones."""
    frames, parents = _fleet({"AHU_1": "proportional"})
    edges = []
    for z, ahu in parents.items():
        box = z.replace("_VAV_", "_BOX_")
        edges += [(ahu, box), (box, z)]
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_edges(edges))
    assert got.metrics["n_zones_joined"] == 4 and got.severity == "ok"


def test_fleet_tolerates_duplicate_zone_timestamps():
    frames, parents = _fleet({"AHU_1": "proportional"})
    z = frames["AHU_1_VAV_4"]
    frames["AHU_1_VAV_4"] = pd.concat([z, z.iloc[:50]]).sort_index()
    got = DcvSystemVerification().analyze_fleet(frames, topology=Topology.from_parent_map(parents))
    assert got.metrics["n_zones_joined"] == 4
