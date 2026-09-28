"""0.91 (#61, #62): equipment-class gating, config topology, CHW plant run status and setpoint
tracking, plant-capacity links in triage, and the per-source OAT reference check. Synthetic only."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.chwplant import analyze_chw_plant, analyze_chw_tracking, chiller_running  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.model.equipclass import EQUIP_FAMILIES, equip_family, family_matches  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.applicability import (  # noqa: E402
    ROLES_SUFFICE,
    RULE_EQUIP_CLASSES,
    rule_equip_classes,
)
from camber.rules.base import Finding, Registry, _class_declined  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chwplant_rule import (  # noqa: E402
    TEMPERATURE_GATE_CAVEAT,
    CHWPlantReset,
    CHWSupplyTracking,
)
from camber.rules.triage import is_sat_high, link_findings, sensor_causes  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.topology_infer import topology_from_config  # noqa: E402


class _Ref:
    def __init__(self, equip, equip_class):
        self.equip, self.equip_class = equip, equip_class


# --------------------------------------------------------------------------- equipment families


@pytest.mark.parametrize(
    "cls,fam",
    [
        ("AHU", "air_handler"),
        ("ahu", "air_handler"),
        ("AHU_DOAS", "air_handler"),
        ("RTU", "air_handler"),
        ("AHU1", "air_handler"),
        ("VAV", "terminal"),
        ("FCAV", "terminal"),
        ("TERMINAL", "terminal"),
        ("FCU", "fan_coil"),
        ("HEAT_PUMP", "heat_pump"),
        ("HeatPump", "heat_pump"),
        ("WSHP", "heat_pump"),
        ("Chiller", "chw_plant"),
        ("CHW_PLANT", "chw_plant"),
        ("CH", "chw_plant"),
        ("HotWaterPlant", "hw_plant"),
        ("HW_PLANT", "hw_plant"),
        ("CHILLEDWATER_METER", "meter"),
        ("Weather", "weather"),
        ("REFRIG_CIRCUIT", "refrigeration"),
    ],
)
def test_equip_family_reads_common_spellings(cls, fam):
    assert equip_family(cls) == fam


def test_unknown_class_has_no_family():
    for cls in ("", None, "M", "Plant", "ZZZ"):
        assert equip_family(cls) is None
    assert family_matches("Plant", ("air_handler",)) is None
    assert family_matches("RTU", ("AHU",)) is True
    assert family_matches("VAV", ("air_handler",)) is False
    assert "air_handler" in EQUIP_FAMILIES


# --------------------------------------------------------------------------- rule applicability


def test_every_builtin_rule_is_classified():
    names = set(rule_names()) | {"chw_supply_tracking"}
    classified = set(RULE_EQUIP_CLASSES) | set(ROLES_SUFFICE)
    assert names <= classified, sorted(names - classified)
    assert not set(RULE_EQUIP_CLASSES) & set(ROLES_SUFFICE)
    for fams in RULE_EQUIP_CLASSES.values():  # every family named is a real family
        assert all(f in EQUIP_FAMILIES for f in fams)


def test_ahu_rules_decline_vav_and_heat_pump_but_not_air_handlers():
    reg = builtin_registry()
    for name in ("supply_air_reset", "supply_air_control", "leaking_valve", "free_cooling_missed"):
        rule = reg.get(name)
        for cls in ("VAV", "HEAT_PUMP", "FCU"):
            f = _class_declined(rule, _Ref("x", cls))
            assert f is not None and f.metrics["declined"] and f.metrics["equip_family"]
        for cls in ("AHU", "RTU", "AHU_DOAS", "", "Plant"):
            assert _class_declined(rule, _Ref("x", cls)) is None
    # terminal rules decline an air handler; plant rules decline a meter
    assert _class_declined(reg.get("reheat_penalty"), _Ref("AHU-1", "AHU")) is not None
    assert _class_declined(reg.get("reheat_penalty"), _Ref("VAV-1", "VAV")) is None
    assert _class_declined(reg.get("chiller_staging"), _Ref("M", "METER")) is not None
    # a roles-only rule is never declined by class
    assert rule_equip_classes(reg.get("night_weekend_setback")) is None
    assert _class_declined(reg.get("night_weekend_setback"), _Ref("x", "HEAT_PUMP")) is None


def test_own_attribute_wins_and_foreign_rules_are_roles_only():
    class Custom:
        name = "supply_air_reset"  # same name as a built-in, defined elsewhere
        roles_required = ()

    assert rule_equip_classes(Custom()) is None

    class Declared:
        name = "x"
        roles_required = ()
        equip_classes = ("terminal",)

    assert rule_equip_classes(Declared()) == ("terminal",)
    assert _class_declined(Declared(), _Ref("a", "AHU")) is not None


def _sat_frame(n=24 * 21):
    idx = pd.date_range("2026-06-01", periods=n, freq="h")
    h = idx.hour.to_numpy()
    on = ((h >= 6) & (h < 19)).astype(float)
    return pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: np.where(on > 0, 55.0, 70.0),
            Role.OAT: 60 + 15 * np.sin((h - 9) / 24 * 2 * np.pi),
            Role.SUPPLY_FAN_STATUS: on,
            Role.SPACE_TEMP: 72.0 + 0 * h,
            Role.COOL_SP: 74.0 + 0 * h,
        },
        index=idx,
    )


def test_registry_declines_vav_and_caveats_an_unknown_class(tmp_path):
    from camber.resolve import discover_store

    st = ParquetStore(str(tmp_path / "s"))
    for eq, cls in (("AHU-1", "AHU"), ("VAV-1", "VAV"), ("HP-1", "HEAT_PUMP"), ("U-1", "Plant")):
        st.write_role_frame(_sat_frame(), facility_id="f", equip=eq, equip_class=cls)
    refs = discover_store(st, "f")
    out = {f.equip: f for f in builtin_registry().run("supply_air_reset", refs, MappingProvider())}
    assert out["VAV-1"].metrics.get("declined") and out["HP-1"].metrics.get("declined")
    assert not out["AHU-1"].metrics.get("declined")
    assert not out["U-1"].metrics.get("declined")
    assert any("not a recognised class" in c for c in out["U-1"].caveats)
    assert not any("recognised" in c for c in out["AHU-1"].caveats)


def test_run_fleet_leaves_other_classes_out_of_the_batch(tmp_path):
    from camber.resolve import discover_store

    st = ParquetStore(str(tmp_path / "s"))
    for eq, cls in (("VAV-1", "VAV"), ("VAV-2", "VAV"), ("HP-1", "HEAT_PUMP")):
        st.write_role_frame(_sat_frame(), facility_id="f", equip=eq, equip_class=cls)
    refs = discover_store(st, "f")
    f = builtin_registry().run_fleet("sat_rogue_zone_census", refs, MappingProvider())
    assert f.metrics["_class_excluded"] == {"n": 1, "classes": ["HEAT_PUMP"]}
    # a roles-only fleet rule keeps every class
    g = builtin_registry().run_fleet("cohort_space_temp", refs, MappingProvider())
    assert "_class_excluded" not in g.metrics


# --------------------------------------------------------------------------- CHW plant


def _plant(n=24 * 14, *, sup_on=48.0, sp=40.0, status="on_days", ret_dt=10.0, seed=0):
    idx = pd.date_range("2026-07-06", periods=n, freq="h")  # a Monday
    rng = np.random.default_rng(seed)
    h = idx.hour.to_numpy()
    on = (idx.dayofweek < 5) & (h >= 6) & (h < 19)
    if status == "never":
        run = np.zeros(n)
    else:
        run = on.astype(float)
    sup = np.where(run > 0, sup_on, 46.0) + rng.normal(0, 0.2, n)  # cold, stagnant loop off
    cols = {
        Role.CHW_SUPPLY_TEMP: sup,
        Role.CHW_SUPPLY_TEMP_SP: np.full(n, sp),
        Role.CHW_RETURN_TEMP: sup + np.where(run > 0, ret_dt, 1.0),
        Role.OAT: 70 + 15 * np.sin((h - 9) / 24 * 2 * np.pi),
    }
    if status is not None:
        cols[Role.COMPRESSOR_STATUS] = run
    return pd.DataFrame(cols, index=idx)


def test_chw_plant_reset_does_not_judge_a_chiller_that_never_ran():
    fr = _plant(status="never", sup_on=44.0)
    # the old temperature gate read a stopped chiller on a cold loop as running
    old = CHWPlantReset().analyze("CH-2", fr.drop(columns=[Role.COMPRESSOR_STATUS]))
    assert old.metrics["n_running"] > 0 and TEMPERATURE_GATE_CAVEAT in old.caveats
    f = CHWPlantReset().analyze("CH-2", fr)
    assert f.severity == "info" and f.metrics["n_running"] == 0
    assert f.metrics["run_source"] == "status" and "did not run" in f.summary


def test_chw_plant_reset_judges_status_hours_only():
    fr = _plant(sup_on=44.0)
    f = CHWPlantReset().analyze("CH-1", fr)
    assert f.metrics["run_source"] == "status"
    assert TEMPERATURE_GATE_CAVEAT not in f.caveats
    run = chiller_running(fr[Role.COMPRESSOR_STATUS])
    legacy = fr.rename(
        columns={Role.CHW_SUPPLY_TEMP: "CHWS_Temp", Role.CHW_RETURN_TEMP: "CHWR_Temp"}
    )
    res = analyze_chw_plant(legacy, "CH-1", running=run)
    assert res.run_source == "status" and 0 < res.n_running <= int(run.sum())


def test_chiller_running_uses_majority_duty():
    s = pd.Series([0.0, 0.4, 0.6, 1.0, np.nan])
    assert chiller_running(s).tolist() == [False, False, True, True, False]
    assert chiller_running(pd.Series([np.nan, np.nan])) is None
    assert chiller_running(None) is None


def test_tracking_flags_48_against_40_most_of_runtime():
    f = CHWSupplyTracking().analyze("CH-1", _plant(sup_on=48.0, sp=40.0))
    m = f.metrics
    assert f.severity == "fault" and m["run_source"] == "status"
    assert m["above_pct"] > 90 and m["setpoint_median_f"] == 40.0
    assert abs(m["chwst_median_f"] - 48.0) < 0.5 and m["deltaT_median_f"] == pytest.approx(
        10.0, 0.1
    )
    assert m["deltaT_median_above_f"] is not None and "running h" in f.summary
    # the first hour after each start is pull-down, not judged
    starts = 10  # ten weekdays
    assert m["n_running"] == m["n_status_on"] - starts


def test_tracking_ok_when_the_plant_makes_setpoint():
    f = CHWSupplyTracking().analyze("CH-1", _plant(sup_on=41.0, sp=40.0))
    assert f.severity == "ok" and f.metrics["above_pct"] == 0.0


def test_tracking_does_not_fault_a_chiller_that_never_ran():
    f = CHWSupplyTracking().analyze("CH-2", _plant(status="never", sup_on=48.0))
    assert f.severity == "info" and f.metrics["n_running"] == 0


def test_tracking_without_status_falls_back_with_a_caveat_and_caps_at_warn():
    f = CHWSupplyTracking().analyze("CH-1", _plant(status=None, sup_on=48.0))
    assert f.metrics["run_source"] == "temperature"
    assert TEMPERATURE_GATE_CAVEAT in f.caveats and f.severity == "warn"
    assert any("capped at warn" in c for c in f.caveats)


def test_tracking_threshold_is_configurable_and_thin_data_declines():
    fr = _plant(sup_on=42.5, sp=40.0)
    assert CHWSupplyTracking().analyze("CH-1", fr).severity == "ok"
    assert CHWSupplyTracking(above_f=2.0).analyze("CH-1", fr).severity == "fault"
    thin = CHWSupplyTracking(min_running=10_000).analyze("CH-1", fr)
    assert thin.severity == "info" and thin.metrics["above_pct"] is None
    no_ret = CHWSupplyTracking().analyze("CH-1", fr.drop(columns=[Role.CHW_RETURN_TEMP]))
    assert no_ret.metrics["deltaT_median_f"] is None
    assert any("deltaT not reported" in c for c in no_ret.caveats)
    assert analyze_chw_tracking(pd.DataFrame({"CHWS_Temp": [1.0]}), "x") is None


def test_tracking_evidence_mask_marks_running_hours_above_setpoint():
    fr = _plant(sup_on=48.0)
    ev = CHWSupplyTracking().evidence("CH-1", fr)
    assert ev.mask.sum() == CHWSupplyTracking().analyze("CH-1", fr).metrics["n_running"]
    assert not ev.mask[fr[Role.COMPRESSOR_STATUS] == 0].any()


def test_tracking_is_registered_and_recommended():
    from camber.aso import recommend
    from camber.scorecard import RULE_CATEGORY

    reg = builtin_registry()
    assert "chw_supply_tracking" in reg.names()
    f = CHWSupplyTracking().analyze("CH-1", _plant())
    rec = recommend(f)
    assert rec is not None and "staging" in rec.action
    assert RULE_CATEGORY["chw_supply_tracking"] == "comfort"


# --------------------------------------------------------------------------- plant links


def _sat_high(equip="AHU-1", warm=60.0, cold=0.0):
    return Finding(
        "supply_air_control",
        equip,
        "fault",
        {"too_warm_pct": warm, "too_cold_pct": cold, "off_setpoint_pct": warm + cold},
        f"{equip}: SAT off setpoint",
    )


def _tracking(equip="CH-1", sev="fault"):
    return Finding("chw_supply_tracking", equip, sev, {"above_pct": 80.0}, f"{equip}: short")


def test_is_sat_high():
    assert is_sat_high(_sat_high())
    assert not is_sat_high(_sat_high(warm=5.0, cold=30.0))
    assert is_sat_high(Finding("g36_fc13", "A", "warn"))
    assert is_sat_high(Finding("g36_afdd", "A", "warn", {"fault_condition": 13}))
    assert is_sat_high(Finding("g36_afdd", "A", "warn", {"fc13_pct": 12.0}))
    assert not is_sat_high(Finding("g36_afdd", "A", "warn", {"fc13_pct": 0}))
    assert not is_sat_high(Finding("supply_air_reset", "A", "warn"))


def test_plant_attaches_as_upstream_cause_without_removing_findings():
    fs = [_sat_high(), _tracking(), _tracking("CH-2", sev="info")]
    issues = link_findings(fs)
    ahu = next(i for i in issues if i.equip == "AHU-1")
    plant = next(i for i in issues if i.equip == "CH-1")
    assert [c.equip for c in ahu.upstream_causes] == ["CH-1"]  # CH-2 (info) is no issue
    c = ahu.upstream_causes[0]
    assert c.kind == "plant_capacity" and c.basis == "site" and c.overlap_share is None
    assert plant.downstream == [fs[0]] and ahu.members == [fs[0]]
    assert not ahu.conditional  # an upstream plant is not a sensor fix: nothing is demoted
    assert any("Likely upstream cause" in w and "not assessed" in w for w in ahu.why)
    assert any("May explain downstream" in w for w in plant.why)


def test_plant_link_needs_the_same_hours_when_masks_exist():
    idx = pd.date_range("2026-07-06", periods=100, freq="h")
    ahu_m = pd.Series(np.arange(100) < 40, index=idx)
    masks = {
        "AHU-1": ahu_m,
        "CH-1": pd.Series(np.arange(100) < 30, index=idx),  # short in 30 of the 40 AHU hours
        "CH-9": pd.Series(np.arange(100) >= 60, index=idx),  # short only when the AHU is fine
    }
    fs = [_sat_high(), _tracking("CH-1"), _tracking("CH-9")]
    issues = link_findings(fs, mask_for=lambda f: masks.get(f.equip))
    ahu = next(i for i in issues if i.equip == "AHU-1")
    assert [c.equip for c in ahu.upstream_causes] == ["CH-1"]
    assert (
        ahu.upstream_causes[0].overlap_share == 0.75 and ahu.upstream_causes[0].plant_share == 1.0
    )
    assert ahu.upstream_causes[0].overlap_hours == 30.0


def test_plant_link_when_the_unit_runs_warm_whenever_the_plant_is_short():
    idx = pd.date_range("2026-07-06", periods=100, freq="h")
    masks = {
        "AHU-1": pd.Series(np.arange(100) < 80, index=idx),  # warm 80 h, from many causes
        "CH-1": pd.Series((np.arange(100) >= 60) & (np.arange(100) < 70), index=idx),  # short 10 h
    }
    issues = link_findings([_sat_high(), _tracking()], mask_for=lambda f: masks.get(f.equip))
    c = next(i for i in issues if i.equip == "AHU-1").upstream_causes[0]
    assert c.overlap_share == 0.125 and c.plant_share == 1.0 and c.overlap_hours == 10.0


def test_plant_link_follows_the_topology():
    topo = Topology.from_parent_map({"AHU-1": "CH-1", "AHU-2": "CH-2"})
    fs = [_sat_high("AHU-1"), _sat_high("AHU-2"), _tracking("CH-1"), _tracking("CH-2")]
    issues = {i.equip: i for i in link_findings(fs, topology=topo)}
    assert [c.equip for c in issues["AHU-1"].upstream_causes] == ["CH-1"]
    assert [c.equip for c in issues["AHU-2"].upstream_causes] == ["CH-2"]
    assert issues["AHU-1"].upstream_causes[0].basis == "topology"
    # a unit the topology does not cover falls back to the site's plants
    fs.append(_sat_high("AHU-3"))
    issues = {i.equip: i for i in link_findings(fs, topology=topo)}
    assert {c.equip for c in issues["AHU-3"].upstream_causes} == {"CH-1", "CH-2"}


def test_no_plant_link_for_cold_supply_air():
    issues = link_findings([_sat_high(warm=1.0, cold=40.0), _tracking()])
    assert not next(i for i in issues if i.equip == "AHU-1").upstream_causes


def test_unit_local_oat_drift_taints_only_its_units():
    f = Finding("sensor_drift:oat", "AHU-2", "fault", {"scope_equips": ["AHU-2"]})
    cs = sensor_causes([f])
    assert [(c.equip, c.shared) for c in cs] == [("AHU-2", False)]
    g = Finding("sensor_drift:oat", "site OAT", "fault", {})
    assert sensor_causes([g])[0].shared is True


# --------------------------------------------------------------------------- config topology


def test_topology_from_config_mapping_and_csv(tmp_path):
    (tmp_path / "v.csv").write_text("vav_id,parent_ahu,floor\nVAV_101,AHU_1,1\nVAV_102,AHU_1,1\n")
    (tmp_path / "a.csv").write_text("unit,feeds\nAHU-1,CH-1\n")
    spec = {
        "parents": {"AHU-2": ["CH-1", "CH-2"], "VAV-201": "AHU-2"},
        "csv": ["v.csv", {"path": "a.csv", "child": "unit", "parent": "feeds"}],
    }
    ids = ["VAV-101", "VAV-102", "VAV-201", "AHU-1", "AHU-2", "CH-1", "CH-2"]
    topo, prov = topology_from_config(spec, base_dir=str(tmp_path), equip_ids=ids)
    assert topo.provenance == "explicit" and prov["source"] == "config"
    assert topo.parents_of("VAV-101") == ("AHU-1",)  # VAV_101 matched VAV-101
    assert set(topo.parents_of("AHU-2")) == {"CH-1", "CH-2"}
    assert "CH-1" in topo.ancestors("VAV-102")
    assert prov["files"] == [{"file": "v.csv", "rows": 2}, {"file": "a.csv", "rows": 1}]
    assert prov["unmatched_ids"] == []
    exact, p2 = topology_from_config(
        {"csv": "v.csv", "match": "exact"}, base_dir=str(tmp_path), equip_ids=ids
    )
    assert exact.parents_of("VAV_101") == ("AHU_1",) and "VAV_101" in p2["unmatched_ids"]


def test_topology_from_config_rejects_bad_specs(tmp_path):
    (tmp_path / "x.csv").write_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError):
        topology_from_config({"csv": "x.csv"}, base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        topology_from_config({"csv": {"path": "x.csv", "child": "nope"}}, base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        topology_from_config({"bogus": 1})
    with pytest.raises(ValueError):
        topology_from_config({"match": "fuzzy"})
    with pytest.raises(ValueError):
        topology_from_config(["not", "a", "dict"])
    (tmp_path / "two.csv").write_text("left,right\nV1,A1\n")
    topo, _ = topology_from_config({"csv": "two.csv"}, base_dir=str(tmp_path))
    assert topo.parents_of("V1") == ("A1",)


def _zone_frame(hot: bool, seed: int):
    idx = pd.date_range("2026-06-01", periods=24 * 14, freq="h")
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            Role.SPACE_TEMP: (78.0 if hot else 72.0) + rng.normal(0, 0.3, len(idx)),
            Role.COOL_SP: np.full(len(idx), 74.0),
        },
        index=idx,
    )


def test_config_topology_overrides_the_naming_heuristic(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    names = {"ZA1": True, "ZA2": False, "ZA3": False, "ZB1": False, "ZB2": False, "ZB3": False}
    for i, (z, hot) in enumerate(names.items()):
        st.write_role_frame(_zone_frame(hot, i), facility_id="f", equip=z, equip_class="VAV")
    (tmp_path / "map.csv").write_text(
        "vav_id,parent_ahu\n"
        + "".join(f"{z},{'AHU-A' if 'A' in z[1] else 'AHU-B'}\n" for z in names)
    )
    cfg = {
        "site": "demo",
        "source": {"kind": "store", "store": "store", "facility_id": "f"},
        "equipment": [{"class": "VAV"}],
        "rules": ["sat_rogue_zone_census"],
        "topology": {"csv": "map.csv"},
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    f = res.findings[0]
    assert res.topology is not None and res.topology.provenance == "explicit"
    assert res.topology_source["n_edges"] == 6
    assert f.metrics.get("grouping_provenance", f.metrics.get("provenance")) == "explicit"
    cfg.pop("topology")
    res2 = run_config(cfg, base_dir=str(tmp_path))
    assert res2.topology is None and res2.topology_source is None


# --------------------------------------------------------------------------- RCx: OAT + air side


def test_oat_of_groups_sources():
    from camber.report.rcx import _oat_of

    idx = pd.date_range("2026-06-01", periods=48, freq="h")
    base = pd.Series(np.linspace(60, 80, 48), index=idx)
    frames = {
        "AHU-1": pd.DataFrame({Role.OAT: base}),
        "AHU-2": pd.DataFrame({Role.OAT: base + 5.0}),
        "AHU-3": pd.DataFrame({Role.OAT: base}),
        "VAV-1": pd.DataFrame({Role.SPACE_TEMP: base}),
    }
    groups = _oat_of(frames, sources=True)
    assert [eqs for _s, eqs in groups] == [["AHU-1", "AHU-3"], ["AHU-2"]]
    assert _oat_of(frames).equals(base)  # the single-series form is unchanged
    assert _oat_of({}, sources=True) == []


def _rcx_store(tmp_path):
    import _rcx_fixture as fx

    st = ParquetStore(str(tmp_path / "store"))
    a1 = fx.ahu_frame(seed=0)
    a2 = fx.ahu_frame(seed=0)
    a2[Role.OAT] = a2[Role.OAT] + 5.0  # the second AHU's own OAT sensor reads +5 F
    st.write_role_frame(a1, facility_id=fx.FID, equip="AHU-1", equip_class="AHU")
    st.write_role_frame(a2, facility_id=fx.FID, equip="AHU-2", equip_class="AHU")
    vav = a1[[Role.SUPPLY_AIR_TEMP, Role.MIXED_AIR_TEMP]].copy()  # discharge + entering air
    vav[Role.SPACE_TEMP] = 72.0
    st.write_role_frame(vav, facility_id=fx.FID, equip="VAV-1", equip_class="VAV")
    st.register_facility(fx.FID, name="Demo facility")
    pd.DataFrame({"time": a1.index, "oat_f": a1[Role.OAT].to_numpy()}).to_csv(
        tmp_path / "ref.csv", index=False
    )
    cfg = fx.config(oat_reference={"csv": "ref.csv"})
    cfg["equipment"] = [{"class": "AHU"}, {"class": "VAV"}]
    return cfg


def test_rcx_reports_every_oat_source_and_keeps_vavs_off_the_air_side(tmp_path, monkeypatch):
    from camber.report import rcx as rcx_mod
    from camber.report.rcx import RcxOptions, build_rcx_report

    monkeypatch.setattr(rcx_mod, "_render", lambda fig, fmt="png", dpi=150: "data:,")
    cfg = _rcx_store(tmp_path)
    run = run_config(cfg, base_dir=str(tmp_path))
    # VAV-1 carries supply/mixed air roles, but no AHU rule runs on it
    vav = [f for f in run.findings if f.equip == "VAV-1"]
    assert vav and all(f.metrics.get("declined") for f in vav)
    opts = RcxOptions(
        sections=("data", "sat", "issues"), oat_reference={"csv": str(tmp_path / "ref.csv")}
    )
    rep = build_rcx_report(run, options=opts)
    drift = [i for i in rep.issues if i.rules == ["sensor_drift:oat"]]
    assert [i.equip for i in drift] == ["AHU-2"]  # AHU-1 reads true; AHU-2's +5 F is caught
    assert drift[0].root.metrics["scope_equips"] == ["AHU-2"]
    assert abs(drift[0].root.metrics["bias"] - 5.0) < 0.2
    data = next(s for s in rep.sections if s["id"] == "data")
    table = next(b for b in data["blocks"] if b["kind"] == "table" and "Bias °F" in b["header"])
    assert [r[0].split(":")[0] for r in table["rows"]] == ["AHU-1", "AHU-2"]
    sat = next(s for s in rep.sections if s["id"] == "sat")
    text = " ".join(str(b) for b in sat["blocks"])
    assert "VAV-1" not in text and "AHU-1" in text
    # AHU-1's findings are not conditional on AHU-2's sensor
    for i in rep.issues:
        if i.equip == "AHU-1":
            assert not i.conditional


def test_registry_run_keeps_its_contract_for_declared_rule(tmp_path):
    reg = Registry()
    reg.register(CHWSupplyTracking())
    from camber.resolve import discover_store

    st = ParquetStore(str(tmp_path / "s"))
    st.write_role_frame(_plant(), facility_id="f", equip="CH-1", equip_class="Chiller")
    st.write_role_frame(_plant(), facility_id="f", equip="M-1", equip_class="METER")
    out = {f.equip: f for f in reg.run("chw_supply_tracking", discover_store(st, "f"), None)}
    assert out["CH-1"].severity == "fault" and out["M-1"].metrics.get("declined")
