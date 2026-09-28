"""#17 (0.92): system-level ASHRAE 62.1 Ventilation Rate Procedure (Vot = Vou / Ev).

Synthetic fixtures only. The arithmetic follows ASHRAE 62.1-2016 Addendum f (Vou, D, the simplified
Ev) and the multiple-zone appendix calculation (Zpz, Xs, Evz); the standard's text is not quoted.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.ventilation_rule import (  # noqa: E402
    VentilationRateProcedure,
    VentilationSystemVRP,
)
from camber.store.parquet_store import ParquetStore  # noqa: E402
from camber.ventilation import (  # noqa: E402
    VentZone,
    assess_system_62_1,
    estimate_oa_cfm,
    load_vent_zones,
    simplified_ev,
    system_outdoor_air,
    zones_from_records,
)


def _office(n=4, **kw):
    return [
        VentZone(f"Z{i}", area_sqft=1000, population=5, space_type="office", **kw) for i in range(n)
    ]


# --------------------------------------------------------------------------- the requirement


def test_simplified_ev_breakpoint():
    assert simplified_ev(1.0) == 0.75 and simplified_ev(0.6) == 0.75
    assert simplified_ev(0.5) == pytest.approx(0.66)
    assert simplified_ev(0.25) == pytest.approx(0.44)
    with pytest.raises(ValueError):
        simplified_ev(0.0)
    with pytest.raises(ValueError):
        simplified_ev(1.2)


def test_multiple_zone_vot_is_vou_over_ev():
    # four 1,000 ft2 offices of 5 people: Vbz = 5*5 + 0.06*1000 = 85 cfm each
    req = system_outdoor_air(_office())
    assert req.d == 1.0 and req.vou_cfm == 340.0 and req.ev_cooling == 0.75
    assert req.vot_cooling_cfm == pytest.approx(453.3, abs=0.1)
    assert req.vot_heating_cfm == req.vot_cooling_cfm  # the simplified Vot has no Ez
    assert any("D = 1" in c for c in req.caveats)
    # diversity: Ps = 10 of 20 -> D = 0.5, Vou = 0.5*100 + 240 = 290, Ev = 0.66
    div = system_outdoor_air(_office(), ps=10)
    assert div.d == 0.5 and div.vou_cfm == 290.0 and div.ev_cooling == pytest.approx(0.66)
    assert div.vot_cooling_cfm == pytest.approx(439.4, abs=0.1)
    assert not any("D = 1" in c for c in div.caveats)
    assert system_outdoor_air(_office(), d=0.5).vou_cfm == 290.0
    # zone-level rows carry both modes' Voz (0.8 heating default)
    z = req.zones[0]
    assert z["vbz_cfm"] == 85.0 and z["voz_cooling_cfm"] == 85.0 and z["voz_heating_cfm"] == 106.2


def test_single_zone_and_100pct_oa_use_ez_by_mode():
    one = system_outdoor_air(_office(1), system_type="single_zone")
    assert one.vot_cooling_cfm == 85.0 and one.vot_heating_cfm == pytest.approx(106.2, abs=0.1)
    doas = system_outdoor_air(_office(), system_type="100pct_oa")
    assert doas.vot_cooling_cfm == 340.0 and doas.vot_heating_cfm == pytest.approx(425.0)
    assert system_outdoor_air(_office(2), system_type="single_zone").declined
    with pytest.raises(ValueError):
        system_outdoor_air(_office(), system_type="dual_duct")
    with pytest.raises(ValueError):
        system_outdoor_air(_office(), method="table")


def test_appendix_method():
    zs = _office(vpz_min_cfm=300)
    req = system_outdoor_air(zs, method="appendix", vps_cfm=4000)
    # Xs = 340/4000; Zpz = 85/300 (cooling), 106.25/300 (heating); Evz = 1 + Xs - Zpz
    assert req.ev_cooling == pytest.approx(1 + 0.085 - 85 / 300, abs=1e-4)
    assert req.ev_heating == pytest.approx(1 + 0.085 - 106.25 / 300, abs=1e-4)
    assert req.vot_heating_cfm > req.vot_cooling_cfm
    # Vps defaults to sum(Vpz), with a caveat
    dflt = system_outdoor_air(zs, method="appendix")
    assert any("Vps" in c for c in dflt.caveats)
    # a zone without a primary airflow declines the appendix method
    assert system_outdoor_air(_office(), method="appendix").declined


def test_simplified_vpz_min_check_and_missing_inputs():
    short = system_outdoor_air(_office(vpz_min_cfm=100))  # 1.5 x 85 = 127.5 > 100
    assert {x["zone"] for x in short.vpz_min_short} == {"Z0", "Z1", "Z2", "Z3"}
    assert any("1.5 x Voz" in c for c in short.caveats)
    fine = system_outdoor_air(_office(vpz_min_cfm=200))  # >= 1.5 x 106.25 heating too
    assert not fine.vpz_min_short
    bad = system_outdoor_air([VentZone("Z0", area_sqft=1000), *_office(1)])
    assert bad.declined and "population" in bad.declined and bad.vot_cooling_cfm is None
    unk = system_outdoor_air([VentZone("Z0", area_sqft=1000, population=5, space_type="spa")])
    assert unk.declined and "space_type" in unk.declined
    assumed = system_outdoor_air(_office(area_assumed=True))
    assert len(assumed.assumed) == 4 and any("assumed" in c for c in assumed.caveats)


# --------------------------------------------------------------------------- OA and the verdict


def _idx(days=14):
    return pd.date_range("2025-03-03", periods=days * 24, freq="h")


def test_estimate_oa_cfm_recovers_the_mix():
    idx = _idx()
    t = np.arange(len(idx))
    oat = pd.Series(45 + 5 * np.sin(2 * np.pi * t / 24), index=idx)
    rat = pd.Series(72.0, index=idx)
    sa = pd.Series(8000.0, index=idx)
    f = 0.2
    mat = f * oat + (1 - f) * rat
    est = estimate_oa_cfm(mat, rat, oat, sa)
    assert len(est) == len(idx)
    assert est["oa_cfm"].median() == pytest.approx(1600.0, rel=1e-6)
    assert (est["oa_lo_cfm"] < 1600).all() and (est["oa_hi_cfm"] > 1600).all()
    # |OAT - RAT| under 10 F: not estimated
    near = estimate_oa_cfm(mat, rat, pd.Series(66.0, index=idx), sa)
    assert near.empty


def test_assess_system_statuses_and_modes():
    req = system_outdoor_air(_office(vpz_min_cfm=300), method="appendix", vps_cfm=4000)
    idx = _idx()
    heat = pd.Series(idx.hour < 8, index=idx)
    ok = assess_system_62_1(
        pd.Series(req.vot_heating_cfm * 1.05, index=idx), req, heating_mask=heat
    )
    assert ok.status == "adequate" and ok.n_heating == int(heat.sum())
    assert ok.ratio_heating == pytest.approx(1.05, abs=1e-3)
    assert ok.ratio_cooling > ok.ratio_heating  # cooling Vot is lower
    under = assess_system_62_1(pd.Series(0.4 * req.vot_cooling_cfm, index=idx), req)
    assert under.status == "under" and under.deficit_cfm > 0 and under.under_hours_pct == 100.0
    over = assess_system_62_1(pd.Series(3.0 * req.vot_cooling_cfm, index=idx), req)
    assert over.status == "over"
    few = assess_system_62_1(pd.Series(500.0, index=idx[:5]), req)
    assert few.status == "insufficient"
    judged = pd.Series(False, index=idx)
    assert assess_system_62_1(pd.Series(500.0, index=idx), req, judged_mask=judged).n == 0
    # an estimate's band straddling a threshold is "uncertain", not a verdict
    s = pd.Series(0.85 * req.vot_cooling_cfm, index=idx)
    unc = assess_system_62_1(s, req, oa_lo_cfm=0.5 * s, oa_hi_cfm=1.3 * s)
    assert unc.status == "uncertain" and unc.basis == "temperature_estimate"
    sure = assess_system_62_1(s * 0.5, req, oa_lo_cfm=0.4 * s, oa_hi_cfm=0.6 * s)
    assert sure.status == "under"


# --------------------------------------------------------------------------- the rule


def _ahu(oa_cfm, *, days=14, flow_station=True, copied_rat=False, seed=0, sa_cfm=8000.0, oat0=45):
    idx = _idx(days)
    n = len(idx)
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    oat = oat0 + 6 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 0.3, n)
    rat = 72 + rng.normal(0, 0.2, n)
    sa = np.full(n, sa_cfm)
    f = oa_cfm / sa
    mat = f * oat + (1 - f) * rat
    sat = 56 + rng.normal(0, 0.3, n)
    cols = {
        Role.OAT: oat,
        Role.RETURN_AIR_TEMP: sat if copied_rat else rat,
        Role.MIXED_AIR_TEMP: mat,
        Role.SUPPLY_AIR_TEMP: sat,
        Role.AIRFLOW: sa,
        Role.SUPPLY_FAN_STATUS: np.ones(n),
    }
    if flow_station:
        cols[Role.OA_AIRFLOW] = oa_cfm + rng.normal(0, 10, n)
    return pd.DataFrame(cols, index=idx)


def _zones4(system="AHU-1"):
    return [
        {
            "zone": f"VAV-{i}",
            "area_sqft": 1000,
            "population": 5,
            "space_type": "office",
            "system": system,
        }
        for i in range(4)
    ]


def test_no_zones_declines():
    f = VentilationSystemVRP().analyze_fleet({"AHU-1": _ahu(500)})
    assert f.severity == "info" and f.metrics["declined"]
    assert "ventilation.zones" in f.summary


def test_a_correctly_ventilated_system_is_adequate_where_one_zone_reads_over():
    fr = _ahu(480.0)  # Vot = 453 cfm for the four zones
    sysf = VentilationSystemVRP(zones=_zones4()).analyze_fleet({"AHU-1": fr})
    m = sysf.metrics["per_system"]["AHU-1"]
    assert sysf.severity == "ok" and m["status"] == "adequate" and m["basis"] == "oa_flow"
    assert m["requirement"]["vot_cooling_cfm"] == pytest.approx(453.3, abs=0.1)
    assert m["membership"] == ["declared"]
    # the zone-level rule judges the same air handler against one zone and calls it over
    one = VentilationRateProcedure(area_sqft=1000, population=5, space_type="office").analyze(
        "AHU-1", fr
    )
    assert one.metrics["status"] == "over" and one.metrics["ratio"] > 5


def test_a_60pct_short_system_is_under():
    f = VentilationSystemVRP(zones=_zones4()).analyze_fleet({"AHU-1": _ahu(180.0)})
    m = f.metrics["per_system"]["AHU-1"]
    assert f.severity == "fault" and m["status"] == "under" and m["deficit_cfm"] > 250
    assert "under" in f.summary


def test_assumed_areas_cap_at_warn_and_say_so():
    zs = [dict(z, area_assumed=True) for z in _zones4()]
    f = VentilationSystemVRP(zones=zs).analyze_fleet({"AHU-1": _ahu(180.0)})
    assert f.severity == "warn" and f.metrics["per_system"]["AHU-1"]["status"] == "under"
    assert any("assumed inputs" in c for c in f.caveats)


def test_membership_from_topology():
    zs = [{k: v for k, v in z.items() if k != "system"} for z in _zones4()]
    frames = {"AHU-1": _ahu(180.0), "AHU-2": _ahu(900.0, seed=1)}
    parents = {f"VAV-{i}": "AHU-1" for i in range(4)}
    explicit = Topology.from_parent_map(parents)
    f = VentilationSystemVRP(zones=zs).analyze_fleet(frames, topology=explicit)
    m = f.metrics["per_system"]["AHU-1"]
    assert m["membership"] == ["declared"] and f.severity == "fault"
    assert "AHU-2" not in f.metrics["per_system"]
    brick = Topology.from_parent_map(parents, provenance="semantic")
    g = VentilationSystemVRP(zones=zs).analyze_fleet(frames, topology=brick)
    assert g.metrics["per_system"]["AHU-1"]["membership"] == ["semantic"]
    assert g.severity == "warn" and any("Brick" in c for c in g.caveats)
    # ids match ignoring case and separators
    loose = Topology.from_parent_map({f"vav_{i}": "AHU-1" for i in range(4)})
    h = VentilationSystemVRP(zones=zs).analyze_fleet(frames, topology=loose)
    assert h.metrics["n_zones_attributed"] == 4
    # the topology lists a fifth terminal with no inputs
    more = Topology.from_parent_map({**parents, "VAV-9": "AHU-1"})
    k = VentilationSystemVRP(zones=zs).analyze_fleet(frames, topology=more)
    assert k.metrics["per_system"]["AHU-1"]["n_served_without_inputs"] == 1


def test_single_source_fallback_and_unattributed():
    zs = [{k: v for k, v in z.items() if k != "system"} for z in _zones4()]
    one = VentilationSystemVRP(zones=zs).analyze_fleet({"AHU-1": _ahu(180.0)})
    assert one.metrics["per_system"]["AHU-1"]["membership"] == ["single_source"]
    assert one.severity == "warn"
    two = VentilationSystemVRP(zones=zs).analyze_fleet(
        {"AHU-1": _ahu(180.0), "AHU-2": _ahu(180.0, seed=1)}
    )
    assert two.metrics["declined"] and any("could not be attributed" in c for c in two.caveats)


def test_estimated_oa_path_and_its_guards():
    # a small unit in cold weather: the temperatures resolve the OA fraction well enough
    cold = _ahu(180.0, flow_station=False, sa_cfm=1000.0, oat0=20)
    f = VentilationSystemVRP(zones=_zones4()).analyze_fleet({"AHU-1": cold})
    m = f.metrics["per_system"]["AHU-1"]
    assert m["basis"] == "temperature_estimate" and m["status"] == "under"
    assert m["ratio_hi"] < 0.9
    assert f.severity == "warn"  # an estimate caps at warn
    assert any("OA estimated from MAT/RAT/OAT" in c for c in f.caveats)
    # a large unit at a 2 % OA fraction: the band swamps it -- uncertain, not a verdict
    big = VentilationSystemVRP(zones=_zones4()).analyze_fleet(
        {"AHU-1": _ahu(180.0, flow_station=False)}
    )
    assert big.metrics["per_system"]["AHU-1"]["status"] == "uncertain" and big.severity == "info"
    cp = VentilationSystemVRP(zones=_zones4()).analyze_fleet(
        {"AHU-1": _ahu(180.0, flow_station=False, copied_rat=True)}
    )
    mc = cp.metrics["per_system"]["AHU-1"]
    assert mc["status"] == "declined" and "not usable" in mc["reason"]
    nothing = _ahu(180.0, flow_station=False).drop(columns=[Role.MIXED_AIR_TEMP])
    nf = VentilationSystemVRP(zones=_zones4()).analyze_fleet({"AHU-1": nothing})
    assert nf.metrics["per_system"]["AHU-1"]["status"] == "declined"


def test_heating_valve_splits_the_modes():
    fr = _ahu(500.0)
    fr[Role.HEAT_VALVE] = np.where(fr.index.hour < 12, 40.0, 0.0)
    zs = [dict(z, vpz_min_cfm=300) for z in _zones4()]
    f = VentilationSystemVRP(
        zones=zs, systems={"AHU-1": {"method": "appendix", "vps_cfm": 4000}}
    ).analyze_fleet({"AHU-1": fr})
    m = f.metrics["per_system"]["AHU-1"]
    assert m["n_heating"] > 0 and m["ratio_heating"] < m["ratio_cooling"]
    assert "heating" in m["summary"]
    no_valve = VentilationSystemVRP(
        zones=zs, systems={"AHU-1": {"method": "appendix", "vps_cfm": 4000}}
    ).analyze_fleet({"AHU-1": _ahu(500.0)})
    assert any("no heating-valve point" in c for c in no_valve.caveats)


def test_bad_system_keys_raise():
    with pytest.raises(ValueError):
        VentilationSystemVRP(zones=_zones4(), systems={"AHU-1": {"population": 3}})


# --------------------------------------------------------------------------- zones and config


def test_zone_records_and_csv(tmp_path):
    zs = zones_from_records(
        [
            {
                "id": "A",
                "ahu": "AHU-1",
                "area": "900",
                "pz": "4",
                "space_type": "office",
                "area_assumed": "yes",
                "vpz_min_cfm": "",
            }
        ]
    )
    z = zs[0]
    assert z.zone == "A" and z.system == "AHU-1" and z.area_sqft == 900 and z.population == 4
    assert z.area_assumed and not z.population_assumed and z.vpz_min_cfm is None
    with pytest.raises(ValueError):
        zones_from_records([{"zone": "A", "areaa": 1}])
    with pytest.raises(ValueError):
        zones_from_records([{"area_sqft": 1}])
    (tmp_path / "z.csv").write_text(
        "zone,system,area_sqft,population,space_type\nV1,AHU-1,1000,5,office\n"
    )
    got = load_vent_zones(
        ["z.csv", {"zone": "V2", "area_sqft": 500, "population": 2, "rp": 5, "ra": 0.06}],
        base_dir=str(tmp_path),
    )
    assert [z.zone for z in got] == ["V1", "V2"] and got[1].rates() == (5.0, 0.06)
    with pytest.raises(ValueError):
        load_vent_zones(["z.csv", "z.csv"], base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        load_vent_zones([42], base_dir=str(tmp_path))


def test_config_ventilation_section_runs_the_rule(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_ahu(180.0), facility_id="f", equip="AHU-1", equip_class="AHU")
    (tmp_path / "zones.csv").write_text(
        "zone,area_sqft,population,space_type\n"
        + "".join(f"VAV-{i},1000,5,office\n" for i in range(4))
    )
    (tmp_path / "map.csv").write_text(
        "vav_id,parent_ahu\n" + "".join(f"VAV-{i},AHU-1\n" for i in range(4))
    )
    cfg = {
        "site": "demo",
        "source": {"kind": "store", "store": "store", "facility_id": "f"},
        "equipment": [{"class": "AHU"}],
        "rules": [],
        "topology": {"csv": "map.csv"},
        "ventilation": {"zones": "zones.csv", "systems": {"AHU-1": {"ps": 20}}},
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    assert "ventilation_system_62_1" in res.rules_run
    f = next(x for x in res.findings if x.rule == "ventilation_system_62_1")
    m = f.metrics["per_system"]["AHU-1"]
    assert m["status"] == "under" and m["membership"] == ["declared"]
    # listed in rules too: it runs once
    cfg["rules"] = ["ventilation_system_62_1"]
    res2 = run_config(cfg, base_dir=str(tmp_path))
    assert res2.rules_run.count("ventilation_system_62_1") == 1
    assert sum(x.rule == "ventilation_system_62_1" for x in res2.findings) == 1
    cfg["ventilation"] = {"zones": "zones.csv", "typo": 1}
    with pytest.raises(ValueError):
        run_config(cfg, base_dir=str(tmp_path))
    cfg["ventilation"] = ["zones.csv"]
    with pytest.raises(ValueError):
        run_config(cfg, base_dir=str(tmp_path))
