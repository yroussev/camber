"""0.108: legal holds, audit and lifecycle states under every primitive (#250, #251, #252, #256),
facility renames that cannot steal another facility's labels (#255), and interop fixes: per
equipment Brick part nodes (#253), valid encoded Turtle names (#254) and Haystack JSON v3 /
Hayson tags (#257).

Every test works in its own tmp directory; no default cache or example data is touched.
"""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_interop_openfdd import TZ, make_package  # noqa: E402
from test_portfolio_continuity import (  # noqa: E402
    _folder_config,
    _run,
    _store_config,
    _workspace,
)

import camber.portfolio._bundle as bundle_mod  # noqa: E402
import camber.portfolio._cascade as cascade  # noqa: E402
from camber.interop.brick import _have_rdflib, roles_from_brick  # noqa: E402
from camber.interop.export import to_brick  # noqa: E402
from camber.interop.haystack_semantic import (  # noqa: E402
    roles_from_haystack,
    topology_from_haystack,
)
from camber.interop.site_model import site_from_ttl, site_to_ttl  # noqa: E402
from camber.model.entities import Equip, Point, Site  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import LifecycleError, Portfolio  # noqa: E402
from camber.portfolio._state import SiteResolver  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "haystack")
BACKENDS = ["minimal"] + (["rdflib"] if _have_rdflib() else [])
IDX = pd.date_range("2024-01-01", periods=48, freq="h")


def _held_ws(tmp_path):
    """A workspace with one active, private facility holding two days of data."""
    pf = Portfolio.init(tmp_path / "ws")
    pf.add_facility("Bldg A", facility_id="bldg_a", reason="t", activate=True, private=True)
    pf.store.write_role_frame(
        pd.DataFrame({"sat": 55.0}, index=IDX), facility_id="bldg_a", equip="AHU1"
    )
    return pf


def _actions(pf, n0):
    return [(a["action"], a["reason"]) for a in pf.audit_log()[n0:]]


# ------------------------------------------------------------------ #250 store and registry


def test_store_write_cannot_flip_privacy_or_rename_in_a_workspace(tmp_path):
    pf = _held_ws(tmp_path)
    more = pd.DataFrame({"sat": 56.0}, index=IDX + pd.Timedelta("2D"))
    for meta in ({"private": False}, {"display_name": "Other Name"}, {"archive": {"x": 1}}):
        with pytest.raises(ValueError, match="cannot change"):
            pf.store.write_role_frame(more, facility_id="bldg_a", equip="AHU1", **meta)
    e = pf.facility("bldg_a")
    assert e["private"] is True and e["display_name"] == "Bldg A"
    # the same values are not a change; a fresh facility may start private (audited)
    pf.store.write_role_frame(more, facility_id="bldg_a", equip="AHU1", private=True)
    pf.store.register_facility("bldg_b", name="B", private=True)
    rec = [a for a in pf.audit_log() if a["facility_id"] == "bldg_b"][-1]
    assert rec["action"] == "facility.register" and rec["details"]["private"] is True
    with pytest.raises(ValueError, match="cannot set edge_devices"):
        pf.store.register_facility("bldg_c", edge_devices={"d": {}})
    # the audited routes still work
    pf.set_private("bldg_a", False, reason="consented")
    assert pf.facility("bldg_a")["private"] is False


def test_plain_store_keeps_accepting_metadata_and_deletes(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(pd.DataFrame({"sat": 55.0}, index=IDX), facility_id="f1", equip="AHU1")
    st.register_facility("f1", private=True, display_name="Shown")
    st.register_facility("f1", private=False)
    assert st.facilities_meta()["f1"]["private"] is False
    assert st.drop_partition("f1", 2024, 1) == 48
    assert st.drop_facility("f1", forget=True) == 0
    st.register_facility("f2", name="Two")
    assert FacilityRegistry(st.root).remove("f2") is True
    assert FacilityRegistry(st.root).tombstones()["f2"]["reason"] == "registry entry removed"


def test_delete_primitives_refuse_a_held_facility(tmp_path):
    pf = _held_ws(tmp_path)
    pf.hold("bldg_a", reason="litigation")
    st = ParquetStore(pf.store_root)
    n0 = len(pf.audit_log())
    calls = {
        "drop_facility": lambda: st.drop_facility("bldg_a", forget=True, reason="x"),
        "prune": lambda: st.prune(before_year=2030, reason="x"),
        "drop_partition": lambda: st.drop_partition("bldg_a", 2024, 1, reason="x"),
        "remove": lambda: pf.registry.remove("bldg_a", reason="x"),
    }
    for call in calls.values():
        with pytest.raises(LifecycleError, match="under a legal hold"):
            call()
    assert sum(p["rows"] for p in st.partitions(facility_id="bldg_a")) == 48
    assert "bldg_a" in pf.registry.all() and not pf.registry.tombstones()
    assert _actions(pf, n0) == []  # nothing deleted, nothing audited


@pytest.mark.parametrize("stage", ["offboarding", "archived"])
def test_removing_a_facility_that_left_active_is_refused(tmp_path, stage):
    pf, fid = _workspace(tmp_path)
    pf.offboard(fid, reason="leaving", apply=True)
    if stage == "archived":
        pf.archive(fid, reason="done", apply=True, skip_grace=True)
    st = ParquetStore(pf.store_root)
    with pytest.raises(LifecycleError, match=f"{stage}: its lifecycle owns its data"):
        st.drop_facility(fid, forget=True, reason="x")
    with pytest.raises(LifecycleError, match=stage):
        pf.registry.remove(fid, reason="x")
    assert pf.facility(fid)["state"] == stage and not pf.registry.tombstones()


def test_delete_primitives_need_a_reason_and_audit_it(tmp_path):
    pf = _held_ws(tmp_path)
    st = ParquetStore(pf.store_root)
    with pytest.raises(ValueError, match="needs a reason"):
        st.drop_partition("bldg_a", 2024, 1)
    with pytest.raises(ValueError, match="needs a reason"):
        pf.registry.remove("bldg_a")
    n0 = len(pf.audit_log())
    assert st.prune(before_year=2000, reason="nothing that old") == 0
    assert st.drop_partition("bldg_a", 2024, 1, reason="bad import") == 48
    assert _actions(pf, n0) == [("store.drop_partition", "bad import")]
    st.write_role_frame(pd.DataFrame({"sat": 1.0}, index=IDX), facility_id="bldg_a", equip="A")
    n1 = len(pf.audit_log())
    assert st.prune(before_year=2025, facility_id="bldg_a", reason="old years") == 1
    assert _actions(pf, n1) == [("store.prune", "old years")]
    st.write_role_frame(pd.DataFrame({"sat": 1.0}, index=IDX), facility_id="bldg_a", equip="A")
    n2 = len(pf.audit_log())
    assert st.drop_facility("bldg_a", forget=True, reason="leaving the portfolio") == 48
    assert _actions(pf, n2) == [
        ("facility.remove", "leaving the portfolio"),
        ("store.drop_facility", "leaving the portfolio"),
    ]


def test_retention_still_prunes_through_the_private_primitive(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    pf.add_facility("Old", facility_id="old", reason="t", activate=True)
    idx = pd.date_range("2020-01-01", periods=24 * 40, freq="h")
    pf.store.write_role_frame(pd.DataFrame({"sat": 55.0}, index=idx), facility_id="old", equip="A")
    r = pf.apply_retention(apply=True, reason="monthly", now="2026-10-01")
    assert r["problems"] == [] and not pf.store.partitions(facility_id="old")


# ------------------------------------------------------------------ #251 open-fdd under a hold


def test_openfdd_reingest_refuses_a_held_facility(tmp_path):
    big = make_package(tmp_path / "big", days=14)
    small = make_package(tmp_path / "small", days=2)
    ws = Portfolio.init(tmp_path / "ws")
    from camber.interop.openfdd import ingest_package

    r = ingest_package(big, timezone=TZ, unit_system="ip", workspace=ws.root, activate=True)
    fid = r.facility_id

    def rows():
        return sum(p["rows"] for p in ws.store.partitions(facility_id=fid))

    before = rows()
    ws.hold(fid, reason="litigation: preserve all trend data")
    with pytest.raises(LifecycleError, match="under a legal hold"):
        ingest_package(small, timezone=TZ, unit_system="ip", workspace=ws.root, reason="newer")
    with pytest.raises(LifecycleError, match="under a legal hold"):
        ingest_package(big, timezone=TZ, unit_system="ip", workspace=ws.root, force=True)
    assert rows() == before
    # an identical re-ingest deletes nothing: still skipped, not refused
    again = ingest_package(big, timezone=TZ, unit_system="ip", workspace=ws.root)
    assert again.skipped and rows() == before


# ------------------------------------------------------------------ #252 config runs + restore


def _archive(pf, fid):
    pf.offboard(fid, reason="leaving", apply=True)
    pf.archive(fid, reason="done", apply=True, skip_grace=True)


def test_config_run_writes_no_state_for_an_archived_held_facility(tmp_path):
    pf, fid = _workspace(tmp_path)
    _archive(pf, fid)
    pf.hold(fid, reason="litigation")
    (tmp_path / "f").mkdir()
    cfg = _folder_config(tmp_path / "f", facility_id=fid, workspace=str(tmp_path / "ws"))
    c = json.load(open(cfg))
    del c["faults"]["store"], c["drift"]["store"]
    json.dump(c, open(cfg, "w"))
    n0 = len(pf.audit_log())
    with pytest.raises(LifecycleError, match="archived"):
        _run(cfg)
    assert not os.path.isdir(pf.state_dir(fid)) and _actions(pf, n0) == []
    pf.release_hold(fid, reason="over")
    with pytest.raises(LifecycleError, match="archived"):
        _run(cfg)


def test_a_hold_allows_non_destructive_state_writes_on_an_active_facility(tmp_path, monkeypatch):
    from camber.cli import main

    pf, fid = _workspace(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    cfg = _store_config(tmp_path, fid)
    pf.hold(fid, reason="litigation")
    # a hold blocks deleting or replacing data only: the faults fold and a baseline freeze
    # (which add to the state) still run, and the run's report is recorded
    assert main(["drift", "freeze", cfg, "--reason", "commissioning baseline"]) == 0
    res = _run(cfg)
    assert res.faults is not None
    assert os.path.isfile(os.path.join(pf.state_dir(fid), "faults.json"))
    assert any(e.get("kind") == "report" for e in pf.manifest(fid)["external"].values())


@pytest.mark.filterwarnings("ignore:facility .* is archived; skipping it")
def test_an_archived_facility_records_no_run_outputs(tmp_path):
    pf, fid = _workspace(tmp_path)
    (tmp_path / "f").mkdir()
    cfg = _folder_config(tmp_path / "f", facility_id=fid, workspace=str(tmp_path / "ws"))
    c = json.load(open(cfg))
    del c["faults"], c["drift"]
    c["report"] = {"out_html": "report.html"}
    json.dump(c, open(cfg, "w"))
    _archive(pf, fid)
    with pytest.warns(UserWarning, match="not recorded in the facility manifest"):
        _run(cfg)
    assert not os.path.isdir(pf.state_dir(fid))
    assert os.path.isfile(tmp_path / "f" / "report.html")


def test_restore_discards_live_trees_the_bundle_does_not_list(tmp_path):
    pf, fid = _workspace(tmp_path)
    _archive(pf, fid)
    # what a pre-0.108 config run left behind: state written while the facility was archived
    sdir = pf.state_dir(fid)
    os.makedirs(sdir)
    open(os.path.join(sdir, "faults.json"), "w").write("{}")
    assert pf.restore(fid, reason="back")["deletes"] == [sdir]  # the dry run says so
    res = pf.restore(fid, reason="back", apply=True)
    assert res["restored"]["verified"] and pf.facility(fid)["state"] == "active"
    assert not os.path.exists(os.path.join(sdir, "faults.json"))
    assert fid in pf.store.facilities()


def test_restore_under_a_hold_refuses_before_touching_anything(tmp_path):
    pf, fid = _workspace(tmp_path)
    _archive(pf, fid)
    sdir = pf.state_dir(fid)
    os.makedirs(sdir)
    open(os.path.join(sdir, "faults.json"), "w").write("{}")
    pf.hold(fid, reason="litigation")
    with pytest.raises(LifecycleError, match="legal hold.*faults.json"):
        pf.restore(fid, reason="back", apply=True)
    assert pf.facility(fid)["state"] == "archived"
    assert fid not in pf.store.facilities()  # no partition swapped back in
    assert os.path.isfile(os.path.join(sdir, "faults.json"))


def test_restore_staged_mismatch_swaps_nothing_and_a_retry_succeeds(tmp_path, monkeypatch):
    pf, fid = _workspace(tmp_path)
    _archive(pf, fid)
    real = bundle_mod._stage_tree
    monkeypatch.setattr(bundle_mod, "_stage_tree", lambda *a: real(*a) + ["store/x.parquet"])
    with pytest.raises(OSError, match="staged files do not match"):
        pf.restore(fid, reason="back", apply=True)
    assert pf.facility(fid)["state"] == "archived" and fid not in pf.store.facilities()
    leftovers = [n for n in os.listdir(pf.store_root) if n.startswith("_swap-")]
    assert leftovers == []
    monkeypatch.setattr(bundle_mod, "_stage_tree", real)
    assert pf.restore(fid, reason="retry", apply=True)["restored"]["verified"]
    assert pf.facility(fid)["state"] == "active"


def test_restore_interrupted_between_swaps_is_finished_by_a_retry(tmp_path, monkeypatch):
    pf, fid = _workspace(tmp_path)
    pf.export(fid, reason="snapshot")  # gives the facility state files, so two trees swap
    _store_run = _store_config(tmp_path, fid)
    _run(_store_run)
    _archive(pf, fid)
    real = bundle_mod._swap.commit
    calls = []

    def flaky(target):
        calls.append(target)
        if len(calls) == 2:
            raise KeyboardInterrupt("power cut between two swaps")
        return real(target)

    monkeypatch.setattr(bundle_mod._swap, "commit", flaky)
    with pytest.raises(KeyboardInterrupt):
        pf.restore(fid, reason="back", apply=True)
    assert pf.facility(fid)["state"] == "archived"
    monkeypatch.setattr(bundle_mod._swap, "commit", real)
    res = pf.restore(fid, reason="retry", apply=True)
    assert res["restored"]["verified"] and pf.facility(fid)["state"] == "active"
    assert os.path.isfile(os.path.join(pf.state_dir(fid), "faults.json"))


# ------------------------------------------------------------------ #256 recover under a hold


def test_recover_keeps_a_held_facilitys_hot_data(tmp_path, monkeypatch):
    pf, fid = _workspace(tmp_path)
    pf.offboard(fid, reason="leaving", apply=True)
    real = cascade._delete_hot

    def crash(*a, **k):
        raise KeyboardInterrupt("power cut")

    monkeypatch.setattr(cascade, "_delete_hot", crash)
    with pytest.raises(KeyboardInterrupt):
        pf.archive(fid, reason="done", apply=True, skip_grace=True)
    monkeypatch.setattr(cascade, "_delete_hot", real)
    part = os.path.join(pf.store_root, f"facility_id={fid}")
    pf.hold(fid, reason="litigation hold: preserve everything")
    assert pf.recover() == [{"facility_id": fid, "action": "archive_blocked_legal_hold"}]
    assert os.path.isdir(part)
    pf.release_hold(fid, reason="settled")
    assert pf.recover() == [{"facility_id": fid, "action": "archive_finished"}]
    assert not os.path.isdir(part)


# ------------------------------------------------------------------ #255 renames


def test_rename_refuses_another_facilitys_labels(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    a = pf.add_facility("North Campus", reason="t", activate=True)["facility_id"]
    b = pf.add_facility("South Campus", reason="t", activate=True)["facility_id"]
    c = pf.add_facility("East Wing", reason="t", activate=True)["facility_id"]
    pf.rename(c, "East Annex", reason="renamed")
    pf.store.register_facility("gone", name="Old Depot")
    pf.registry.remove("gone", reason="sold")
    for label in ("North Campus", "  north   CAMPUS ", a, "East Wing", "Old Depot", "gone"):
        with pytest.raises(ValueError, match="cannot rename"):
            pf.rename(b, label, reason="typo")
    assert pf.legacy_sites(a) == sorted(["North Campus", a])
    pf.rename(b, "South Campus Annex", reason="ok")
    pf.rename(b, "South Campus", reason="its own former name is fine")
    assert pf.facility(b)["display_name"] == "South Campus"


def test_resolver_is_not_poisoned_by_an_old_transient_rename(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    a = pf.add_facility("North Campus", reason="t", activate=True)["facility_id"]
    b = pf.add_facility("South Campus", reason="t", activate=True)["facility_id"]
    # what a pre-0.108 workspace may hold: B briefly renamed to A's name and id, then back
    for frm, to in (("South Campus", "North Campus"), ("North Campus", a), (a, "South Campus")):
        pf._audit("facility.rename", facility_id=b, reason="old", details={"from": frm, "to": to})
    res = SiteResolver(pf)
    assert res.resolve("North Campus")["facility_id"] == a
    assert res.resolve(a)["facility_id"] == a
    assert pf.legacy_sites(a) == sorted(["North Campus", a])
    assert "North Campus" not in pf.legacy_sites(b)


# ------------------------------------------------------------------ #253 / #254 Brick


def _two_ahus():
    return Site(
        id="campus",
        equips=(
            Equip(
                "AHU_1",
                "AHU",
                points=(
                    Point("AHU_1_SAT", Role.SUPPLY_AIR_TEMP),
                    Point("AHU_1_CHW_VLV", Role.COOL_VALVE),
                    Point("AHU_1_OAD", Role.OA_DAMPER),
                    Point("AHU_1_SF", Role.SUPPLY_FAN_STATUS),
                ),
            ),
            Equip(
                "AHU_2",
                "AHU",
                points=(
                    Point("AHU_2_SAT", Role.SUPPLY_AIR_TEMP),
                    Point("AHU_2_CHW_VLV", Role.COOL_VALVE),
                    Point("AHU_2_SF", Role.SUPPLY_FAN_STATUS),
                ),
            ),
        ),
    )


def _points(site):
    return {e.id: sorted((p.name, p.role.value) for p in e.points) for e in site.equips}


@pytest.mark.parametrize("backend", BACKENDS)
def test_multi_equipment_site_round_trips_part_points(backend):
    site = _two_ahus()
    ttl = site_to_ttl(site)
    assert "bldg:AHU_1.Cooling_Coil" in ttl and "bldg:AHU_2.Cooling_Coil" in ttl
    assert _points(site_from_ttl(ttl, backend=backend)) == _points(site)


NAMES = [
    ("AHU 1", "SA Temp"),
    ("AHU-1", "AHU-1/SAT"),
    ("AHU_1", "SAT."),
    ("AHU:1", "OA-T (deg F)"),
    ("-AHU", '50% valve, "quoted"; #1'),
    ("Lüftung 1", "Zuluft °C"),
]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("equip,point", NAMES)
def test_turtle_names_are_valid_and_round_trip_exactly(backend, equip, point):
    ttl = to_brick(
        equip,
        "AHU",
        [Role.SUPPLY_AIR_TEMP, Role.COOL_VALVE],
        point_names={Role.SUPPLY_AIR_TEMP: point, Role.COOL_VALVE: point + " vlv"},
    )
    assert roles_from_brick(ttl, backend=backend) == {
        point: Role.SUPPLY_AIR_TEMP,
        point + " vlv": Role.COOL_VALVE,
    }
    site = Site(
        id="site 1",
        equips=(Equip(equip, "AHU", points=(Point(point, Role.SUPPLY_AIR_TEMP),)),),
    )
    back = site_from_ttl(site_to_ttl(site), backend=backend)
    assert back.id == "site 1" and _points(back) == _points(site)


@pytest.mark.skipif(not _have_rdflib(), reason="needs rdflib")
def test_turtle_labels_carry_the_exact_names():
    import rdflib

    ttl = site_to_ttl(
        Site(id="s", equips=(Equip("AHU 1", "AHU", points=(Point("SA/T.", Role.OAT),)),))
    )
    g = rdflib.Graph().parse(data=ttl, format="turtle")
    labels = {str(o) for _s, p, o in g if str(p).endswith("#label")}
    assert {"AHU 1", "SA/T.", "s"} <= labels


@pytest.mark.parametrize("backend", BACKENDS)
def test_default_point_names_of_two_equipment_do_not_collide(backend):
    site = Site(
        id="campus",
        equips=(
            Equip.from_roles("AHU_1", "AHU", [Role.SUPPLY_AIR_TEMP]),
            Equip.from_roles("AHU_2", "AHU", [Role.SUPPLY_AIR_TEMP]),
        ),
    )
    assert _points(site_from_ttl(site_to_ttl(site), backend=backend)) == _points(site)


# ------------------------------------------------------------------ #257 Haystack encodings


@pytest.mark.parametrize("fixture", ["read_v3.json", "read_hayson.json"])
def test_haystack_json_encodings_resolve_roles_and_refs(fixture):
    grid = json.load(open(os.path.join(GOLDEN, fixture), encoding="utf-8"))
    rows = grid["rows"]
    assert roles_from_haystack(rows) == {
        "p1": Role.OAT,
        "p2": Role.SUPPLY_AIR_TEMP,
        "p3": Role.SPACE_TEMP,
    }
    topo = topology_from_haystack(rows)
    assert ("AHU_1", "VAV_1") in topo.edges
