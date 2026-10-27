"""0.111 (#290): the research-only refusal and the lifecycle filter fail closed.

A store's facility registry (``_facilities.json``) that is deleted, corrupt, empty, or lacks a
facility's entry must never widen what an assistant adapter serves: a facility named for a
research-only catalog dataset is refused whatever the registry says, a damaged registry refuses
every facility (and ``--allow-all-active`` then allows none), and each result says why.
"""

import json
import logging
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import _servable  # noqa: E402
from camber.capabilities import (  # noqa: E402
    CallContext,
    CapabilityError,
    default_registry,
    run_config_for,
)
from camber.datasets import get  # noqa: E402
from camber.mcp._launch import LaunchError, build_context  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

IDX = pd.date_range("2024-03-01", periods=24, freq="h")
NC = "ds-at-30bldg-sensors-b02"  # the catalog holds at-30bldg-sensors research-only (NC)
OPEN = "ds-lbnl-fcu"  # an open catalog dataset
OWN = "hq"  # the user's own data
OLD = "old-wing"  # the user's own, suspended

REG = default_registry()


def _frame(v=55.0):
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: v, Role.OAT: 60.0, Role.HEAT_VALVE: 0.0}, index=IDX)


@pytest.fixture
def store(tmp_path):
    assert get("at-30bldg-sensors").research_only and not get("lbnl-fcu").research_only
    st = ParquetStore(str(tmp_path / "store"))
    for v, fid in enumerate((NC, OPEN, OWN, OLD)):  # other values: none a copy of another (#312)
        st.write_role_frame(_frame(55.0 + v), facility_id=fid, equip="AHU-1", equip_class="AHU")
    st.register_facility(NC, name="NC", dataset=get("at-30bldg-sensors").provenance())
    st.register_facility(OPEN, name="Open", dataset=get("lbnl-fcu").provenance())
    st.register_facility(OWN, name="HQ")
    st.register_facility(OLD, name="Old wing")
    _edit(st, lambda r: r[OLD].update(state="suspended"))
    st.points()
    return st


def _edit(store, fn):
    path = _registry_path(store)
    with open(path) as fh:
        reg = json.load(fh)
    fn(reg)
    with open(path, "w") as fh:
        json.dump(reg, fh)


def _registry_path(store):
    return os.path.join(store.root, "_facilities.json")


def _deleted(store):
    os.remove(_registry_path(store))


def _corrupt(store):
    with open(_registry_path(store), "w") as fh:
        fh.write("{")


def _zero_bytes(store):
    open(_registry_path(store), "w").close()


def _not_an_object(store):
    with open(_registry_path(store), "w") as fh:
        fh.write('["hq"]')


DAMAGE = {
    "deleted": _deleted,
    "corrupt": _corrupt,
    "zero_bytes": _zero_bytes,
    "not_an_object": _not_an_object,
}


def _ctx(store, allowed):
    return CallContext(store=store.root, allowed_facilities=frozenset(allowed))


def _refused(ctx, name, args):
    with pytest.raises(CapabilityError) as e:
        REG.call(name, args, ctx)
    assert e.value.code == "refused_licence", (name, e.value.message)
    return e.value


def test_intact_registry_serves_as_before(store):
    ctx = _ctx(store, (NC, OPEN, OWN))
    assert _refused(ctx, "points", {"facility_id": NC}).data == {"facility_id": NC}
    for fid in (OPEN, OWN):
        assert not REG.call("points", {"facility_id": fid}, ctx).banners
    listed = REG.call("facilities", {}, ctx).data
    assert [f["facility_id"] for f in listed["facilities"]] == [OPEN, OWN]
    assert listed["withheld_research_only"] == 1 and listed["withheld_unverified"] == 0


@pytest.mark.parametrize("damage", sorted(DAMAGE))
def test_a_damaged_registry_refuses_every_facility(store, damage):
    DAMAGE[damage](store)
    ctx = _ctx(store, (NC, OPEN, OWN))
    assert _refused(ctx, "points", {"facility_id": NC}).data == {"facility_id": NC}
    for fid in (OPEN, OWN):
        for name, args in (
            ("points", {"facility_id": fid}),
            ("coverage", {"facility_id": fid, "group_by": "equip"}),
            ("rule_run", {"facility_id": fid}),
        ):
            e = _refused(ctx, name, args)
            assert e.data == {"facility_id": fid, "reason": "licence_unverified"}
            assert "facility registry" in e.message
    res = REG.call("facilities", {}, ctx)
    assert res.data["facilities"] == [] and res.data["withheld_research_only"] == 1
    assert res.data["withheld_unverified"] == 2
    assert any("facility registry" in b and "no facility is served" in b for b in res.banners)
    # every result carries the notice, even one that names no facility
    assert any("facility registry" in b for b in REG.call("dataset_search", {}, ctx).banners)


@pytest.mark.parametrize("damage", sorted(DAMAGE))
def test_allow_all_active_allows_nothing_on_a_damaged_registry(store, damage, caplog):
    DAMAGE[damage](store)
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        ctx = build_context(store.root, allow_all_active=True)
    assert ctx.allowed_facilities == frozenset()
    assert "facility registry" in caplog.text and "no facility" in caplog.text
    res = REG.call("facilities", {}, ctx)
    assert res.data["count"] == 0 and any("facility registry" in b for b in res.banners)


@pytest.mark.parametrize("damage", sorted(DAMAGE))
def test_explicit_allow_on_a_damaged_registry_warns_and_refuses(store, damage, caplog):
    DAMAGE[damage](store)
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        ctx = build_context(store.root, allow=[NC, OWN])
    assert ctx.allowed_facilities == frozenset({NC, OWN})
    assert "facility registry" in caplog.text
    assert f"--allow {NC}" in caplog.text and "research-only" in caplog.text
    _refused(ctx, "points", {"facility_id": NC})
    _refused(ctx, "points", {"facility_id": OWN})


def test_an_intact_allow_all_active_keeps_the_lifecycle_filter(store, caplog):
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        ctx = build_context(store.root, allow_all_active=True)
    assert ctx.allowed_facilities == frozenset({NC, OPEN, OWN})  # OLD is suspended
    assert not caplog.text
    _refused(ctx, "points", {"facility_id": NC})


def test_a_missing_entry_of_a_research_only_dataset_is_still_refused(store, caplog):
    _edit(store, lambda r: r.pop(NC))
    ctx = _ctx(store, (NC, OPEN, OWN))
    assert _refused(ctx, "points", {"facility_id": NC}).data == {"facility_id": NC}
    res = REG.call("facilities", {}, ctx)
    assert NC not in [f["facility_id"] for f in res.data["facilities"]]
    assert res.data["withheld_research_only"] == 1
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        allowed = build_context(store.root, allow_all_active=True).allowed_facilities
        build_context(store.root, allow=[NC])
    assert allowed == frozenset({OPEN, OWN})  # unregistered ds- facility: lifecycle unknown
    assert "no entry" in caplog.text and f"--allow {NC}" in caplog.text


def test_a_missing_entry_of_an_open_dataset_is_unverified(store, caplog):
    _edit(store, lambda r: r.pop(OPEN))
    ctx = _ctx(store, (OPEN, OWN))
    e = _refused(ctx, "points", {"facility_id": OPEN})
    assert e.data["reason"] == "licence_unverified" and "no entry" in e.message
    assert REG.call("points", {"facility_id": OWN}, ctx).data
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        build_context(store.root, allow=[OPEN])
    assert f"--allow {OPEN}" in caplog.text


def test_an_own_facility_without_an_entry_counts_as_active(store):
    _edit(store, lambda r: r.pop(OWN))
    ctx = build_context(store.root, allow_all_active=True)
    assert OWN in ctx.allowed_facilities
    assert REG.call("points", {"facility_id": OWN}, ctx).data


@pytest.mark.parametrize(
    "block",
    [
        {"dataset_id": "lbnl-fcu", "licence": "CC-BY-4.0", "access": "open"},
        {"licence": "CC0-1.0", "access": "open", "redistribution": "allowed"},
        None,
    ],
)
def test_a_wrong_open_licence_for_a_research_only_dataset_is_refused(store, block, caplog):
    def wrong(r):
        if block is None:
            r[NC].pop("dataset")
        else:
            r[NC]["dataset"] = block

    _edit(store, wrong)
    ctx = _ctx(store, (NC,))
    assert _refused(ctx, "points", {"facility_id": NC}).data == {"facility_id": NC}
    assert REG.call("facilities", {}, ctx).data["withheld_research_only"] == 1
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        ctx = build_context(store.root, allow_all_active=True)
    assert NC in ctx.allowed_facilities  # active, but every call naming it is refused
    _refused(ctx, "points", {"facility_id": NC})
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        build_context(store.root, allow=[NC])
    assert f"--allow {NC}" in caplog.text and "research-only" in caplog.text


def test_a_ds_facility_neither_catalog_nor_registry_vouches_for_is_refused(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id="ds-not-a-dataset", equip="A", equip_class="AHU")
    st.register_facility("ds-not-a-dataset", name="Mystery")
    e = _refused(_ctx(st, ("ds-not-a-dataset",)), "points", {"facility_id": "ds-not-a-dataset"})
    assert e.data["reason"] == "licence_unverified"


def test_a_folder_config_named_for_a_research_only_dataset_is_refused(store, tmp_path):
    cfg = {"site": NC, "facility_id": NC, "source": {"folder": "trends"}}
    with pytest.raises(CapabilityError) as e:
        run_config_for(_ctx(store, (NC,)), cfg, base_dir=str(tmp_path))
    assert e.value.code == "refused_licence"


def test_a_config_store_is_checked_with_its_own_registry(store, tmp_path):
    _corrupt(store)
    cfg = {"site": "HQ", "source": {"kind": "store", "store": store.root, "facility_id": OWN}}
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(CapabilityError) as e:
        run_config_for(CallContext(store=str(other), allowed_facilities={OWN}), cfg)
    assert e.value.code == "refused_licence" and e.value.data["reason"] == "licence_unverified"


def test_catalog_names_map_to_their_dataset():
    assert [e.id for e in _servable.catalog_entries_for(NC)] == ["at-30bldg-sensors"]
    assert [e.id for e in _servable.catalog_entries_for("DS-AT-30BLDG-SENSORS")] == [
        "at-30bldg-sensors"
    ]
    assert _servable.catalog_entries_for("ds-at-30bldg-sensorsx") == []
    assert _servable.catalog_entries_for("hq") == []


def test_no_active_facility_in_an_intact_store_is_still_a_launch_error(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id=OLD, equip="A", equip_class="AHU")
    st.register_facility(OLD, name="Old")
    _edit(st, lambda r: r[OLD].update(state="suspended"))
    with pytest.raises(LaunchError, match="no active facility"):
        build_context(st.root, allow_all_active=True)


def test_the_mcp_server_shows_the_model_a_damaged_registry(store):
    from camber.mcp import McpServer

    _corrupt(store)
    server = McpServer(REG, build_context(store.root, allow_all_active=True))
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "facilities",
            "arguments": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
            },
        },
    }
    out = server.handle(req)["result"]
    assert not out["isError"] and out["structuredContent"]["count"] == 0
    assert out["content"][0]["text"].startswith("NOTICE: The store's facility registry")
