"""Portfolio lifecycle, step 1: workspace, registry v2, tombstones, audit log, lock, transitions.

The load-bearing claims: every change is attributable (one fsynced audit line with the OS user and
a reason), a second writer is refused with a message naming the holder, a retired facility id is
never handed to a new building, old registries keep working unchanged, and analyses skip
facilities that are not active.
"""

import json
import os
import subprocess
import sys
import textwrap
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.api.read import ReadAPI  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import (  # noqa: E402
    DEFAULT_POLICY,
    DELETING,
    STATES,
    TRANSITIONS,
    LifecycleError,
    Portfolio,
    PortfolioLocked,
    allowed_actions,
    find_workspace,
    is_workspace,
    transition,
)
from camber.portfolio import _audit as _audit  # noqa: E402
from camber.portfolio import _lock as _lock  # noqa: E402
from camber.resolve import discover_store  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore, make_facility_id  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frame(n=48):
    idx = pd.date_range("2025-07-07", periods=n, freq="h")
    return pd.DataFrame(
        {
            Role.COOL_VALVE: 60.0,
            Role.HEAT_VALVE: 0.0,
            Role.MIXED_AIR_TEMP: 72.0,
            Role.SUPPLY_AIR_TEMP: np.linspace(55, 56, n),
            Role.OAT: 88.0,
        },
        index=idx,
    )


# --------------------------------------------------------------------------- state machine

_EXPECTED = {
    ("provisioning", "activate"): "active",
    ("active", "suspend"): "suspended",
    ("suspended", "resume"): "active",
    ("active", "offboard"): "offboarding",
    ("suspended", "offboard"): "offboarding",
    ("offboarding", "restore"): "active",
    ("offboarding", "archive"): "archived",
    ("archived", "restore"): "active",
    ("archived", "purge"): "purged",
}


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("action", list(TRANSITIONS))
def test_transition_table_is_exactly_the_agreed_one(state, action):
    want = _EXPECTED.get((state, action))
    if want is None:
        with pytest.raises(LifecycleError, match=f"cannot {action}.*allowed from {state}"):
            transition(state, action)
    else:
        assert transition(state, action) == want
        assert action in allowed_actions(state)


def test_legal_hold_blocks_only_data_deleting_transitions():
    assert DELETING == {"archive", "purge"}
    for (state, action), want in _EXPECTED.items():
        if action in DELETING:
            with pytest.raises(LifecycleError, match="legal hold"):
                transition(state, action, legal_hold=True)
        else:
            assert transition(state, action, legal_hold=True) == want


def test_state_machine_rejects_unknowns_and_purged_is_final():
    with pytest.raises(LifecycleError, match="unknown lifecycle action"):
        transition("active", "explode")
    with pytest.raises(LifecycleError, match="unknown lifecycle state"):
        allowed_actions("limbo")
    assert allowed_actions("purged") == []
    with pytest.raises(LifecycleError, match="purged is final"):
        transition("purged", "restore")


# --------------------------------------------------------------------------- workspace


def test_init_is_idempotent_and_lays_out_the_workspace(tmp_path):
    root = tmp_path / "ws"
    pf = Portfolio.init(root)
    assert is_workspace(root) and os.path.isdir(root / "store")
    doc = json.load(open(root / "_portfolio.json"))
    assert doc["schema_version"] == 1 and doc["store"] == "store"
    assert doc["retention"]["defaults"] == DEFAULT_POLICY
    assert DEFAULT_POLICY["raw_trends"] == {"keep_months": 25}
    assert DEFAULT_POLICY["drift_baselines"] == {"keep": "equipment_life", "keep_versions": 10}
    assert doc["legal_holds"] == {} and doc["offboarding_grace_days"] == 30
    for reserved in ("rollups", "state", "archive"):  # lazily created by later releases
        assert not os.path.exists(root / reserved)
    assert [r["action"] for r in pf.audit_log()] == ["portfolio.init"]
    Portfolio.init(root)  # again: nothing changes, nothing audited
    assert len(pf.audit_log()) == 1
    assert json.load(open(root / "_portfolio.json")) == doc
    with pytest.raises(FileNotFoundError, match="not a portfolio workspace"):
        Portfolio(tmp_path / "nope")


def test_future_schema_is_refused(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    p = tmp_path / "ws" / "_portfolio.json"
    doc = json.load(open(p))
    doc["schema_version"] = 99
    json.dump(doc, open(p, "w"))
    with pytest.raises(ValueError, match="upgrade CAMBER"):
        pf.status()


def test_adopt_wraps_an_existing_store_moving_nothing(tmp_path):
    store = tmp_path / "lake"
    st = ParquetStore(str(store))
    st.write_role_frame(_frame(), facility_id="old-a", equip="AHU_1", name="Old A")
    st.write_role_frame(_frame(), facility_id="bare", equip="AHU_1")  # data, no registry entry
    before = sorted(os.listdir(store))
    pf = Portfolio.adopt(store, reason="bring the lake under lifecycle")
    assert pf.root == str(tmp_path) and pf.store_root == str(store)
    assert set(os.listdir(store)) - set(before) == {"_workspace.json"}  # only the marker
    rec = pf.audit_log()[-1]
    assert rec["action"] == "portfolio.adopt" and rec["reason"].startswith("bring")
    assert rec["details"] == {"store": "lake", "registered": ["old-a"], "unregistered": ["bare"]}
    facs = pf.facilities()
    assert facs["old-a"]["state"] == "active" and facs["bare"]["registered"] is False
    # idempotent; a different workspace for the same store is refused
    assert Portfolio.adopt(store, tmp_path, reason="again").root == pf.root
    assert len(pf.audit_log()) == 1
    with pytest.raises(ValueError, match="already belongs"):
        Portfolio.adopt(store, tmp_path / "other", reason="x")
    # from now on, the registry audits through the workspace
    st.write_role_frame(_frame(), facility_id="new-b", equip="AHU_1", name="New B")
    assert pf.audit_log(facility_id="new-b")[0]["action"] == "facility.register"


def test_adopt_store_outside_the_root_and_conflicts(tmp_path):
    store = tmp_path / "data" / "lake"
    ParquetStore(str(store)).write_role_frame(_frame(), facility_id="a", equip="AHU_1")
    pf = Portfolio.adopt(store, tmp_path / "admin", reason="x")
    assert pf.store_root == str(store)
    assert json.load(open(tmp_path / "admin" / "_portfolio.json"))["store"] == str(store)
    other = tmp_path / "lake2"
    other.mkdir()
    with pytest.raises(ValueError, match="already a workspace"):
        Portfolio.adopt(other, tmp_path / "admin", reason="x")
    with pytest.raises(FileNotFoundError):
        Portfolio.adopt(tmp_path / "missing", reason="x")


def test_find_workspace_explicit_env_and_cwd(tmp_path, monkeypatch):
    Portfolio.init(tmp_path / "ws")
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert find_workspace() is None
    assert find_workspace(str(tmp_path / "ws")) == str(tmp_path / "ws")
    assert find_workspace(str(tmp_path)) is None  # an explicit non-workspace is not searched past
    monkeypatch.setenv("CAMBER_PORTFOLIO", str(tmp_path / "ws"))
    assert find_workspace() == str(tmp_path / "ws")
    monkeypatch.delenv("CAMBER_PORTFOLIO")
    monkeypatch.chdir(tmp_path / "ws")
    assert find_workspace() == str(tmp_path / "ws")


def test_effective_retention_precedence(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    p = tmp_path / "ws" / "_portfolio.json"
    doc = json.load(open(p))
    doc["retention"]["overrides"] = {
        "f1": {"raw_trends": {"keep_months": 60}, "audit": {"keep_years": 1}}
    }
    doc["legal_holds"] = {"f2": {"reason": "litigation"}}
    json.dump(doc, open(p, "w"))
    f1, f2, f3 = (pf.effective_retention(f) for f in ("f1", "f2", "f3"))
    assert f1["raw_trends"] == {"rule": {"keep_months": 60}, "source": "facility"}
    assert f1["audit"]["source"] == "default"  # the audit log can never be overridden
    assert all(v["source"] == "legal_hold" for v in f2.values())
    assert f3["findings"] == {"rule": {"keep_years": 7}, "source": "default"}


# --------------------------------------------------------------------------- audit


def test_audit_append_is_fsynced_and_parses(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(_audit.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    rec = _audit.append_audit(
        str(tmp_path),
        _audit.audit_record("facility.suspend", facility_id="f", reason="why", details={"n": 1}),
    )
    assert synced and rec["actor"] and rec["host"] and rec["ts"].endswith("Z")
    assert set(rec) == {
        "ts",
        "actor",
        "host",
        "action",
        "facility_id",
        "from_state",
        "to_state",
        "reason",
        "details",
    }
    with open(tmp_path / "_audit.ndjson", "a") as fh:  # a torn trailing line and junk are skipped
        fh.write('\n[1, 2]\n{"action": "torn')
    rows = _audit.read_audit(str(tmp_path))
    assert rows == [rec]
    assert _audit.read_audit(str(tmp_path), facility_id="g") == []
    assert _audit.read_audit(str(tmp_path / "none")) == []


# --------------------------------------------------------------------------- lock

_HOLDER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {repo!r})
    from camber.portfolio._lock import portfolio_lock
    with portfolio_lock({root!r}):
        print("held", flush=True)
        sys.stdin.read()
    """
)


def test_second_process_is_refused_with_the_holder(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(repo=_REPO, root=pf.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(PortfolioLocked, match=rf"portfolio is locked by {child.pid}@\S+ since"):
            pf.add_facility("X", reason="blocked")
        assert pf.status()["locked_by"].startswith(f"{child.pid}@")
        # the automated registry path waits (bounded) and then gives the same clear error
        reg = FacilityRegistry(pf.store_root, lock_timeout=0.2)
        with pytest.raises(PortfolioLocked, match="locked by"):
            reg.register("f-1", name="F")
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    assert pf.status()["locked_by"] is None
    pf.add_facility("X", reason="lock released")  # the kernel dropped it with the process


def test_waiting_for_the_lock_succeeds_once_released(tmp_path):
    root = str(tmp_path)
    src = _HOLDER.format(repo=_REPO, root=root).replace(
        "sys.stdin.read()", "import time; time.sleep(0.6)"
    )
    child = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    with _lock.portfolio_lock(root, timeout=20):
        assert _lock.read_holder(root)["pid"] == os.getpid()
    child.wait(timeout=30)


def test_stale_holder_text_is_not_a_lock_and_the_lock_is_reentrant(tmp_path):
    root = str(tmp_path)
    with open(tmp_path / "_lock", "wb") as fh:  # left behind by a crashed process
        fh.write(b'\n{"host": "elsewhere", "pid": 999999, "since": "2020-01-01T00:00:00Z"}\n')
    assert _lock.read_holder(root)["pid"] == 999999
    assert _lock.probe(root) is None  # text only: nobody holds it
    with _lock.portfolio_lock(root):
        with _lock.portfolio_lock(root):  # re-entrant within the process
            assert _lock.read_holder(root)["pid"] == os.getpid()
            assert _lock.probe(root)["pid"] == os.getpid()
    assert _lock.probe(str(tmp_path / "nolock")) is None
    assert _lock.describe_holder({}) == "an unknown process"
    (tmp_path / "_lock").write_bytes(b"\nnot json")
    assert _lock.read_holder(root) == {}


# --------------------------------------------------------------------------- registry v2


def test_v1_registry_reads_as_active_with_unknown_dates(tmp_path):
    root = tmp_path / "db"
    root.mkdir()
    json.dump(
        {"old-fac": {"name": "Old", "climate_zone": "4A"}}, open(root / "_facilities.json", "w")
    )
    reg = FacilityRegistry(str(root))
    e = reg.get("old-fac")
    assert e["state"] == "active" and e["created_at"] is None and e["state_changed_at"] is None
    assert e["display_name"] == "Old" and e["portfolio"] == [] and e["climate_zone"] == "4A"
    assert reg.state("old-fac") == "active" and reg.state("never") == "active"
    reg.register("old-fac", name="Old", extra=1)  # same name: fine, metadata merges
    assert reg.get("old-fac")["extra"] == 1
    with pytest.raises(ValueError, match="already registered as 'Old'"):
        reg.register("old-fac", name="Someone Else")
    with pytest.raises(ValueError, match="cannot change state"):
        reg.register("old-fac", state="suspended")
    with pytest.raises(ValueError, match="cannot set created_at"):
        reg.register("brand-new", created_at="2020")
    with pytest.raises(ValueError, match="starts provisioning or active"):
        reg.register("brand-new", state="archived")
    # the raw file still has no derived fields added for the untouched keys
    raw = json.load(open(root / "_facilities.json"))["old-fac"]
    assert "display_name" not in raw and "state" not in raw
    assert ParquetStore(str(root)).active_facilities() == []  # no data yet


def test_new_registration_is_active_with_dates(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    st.write_role_frame(_frame(), facility_id="f-1", equip="AHU_1", name="F One")
    e = st.facilities_meta()["f-1"]
    assert e["state"] == "active" and e["created_at"] and e["created_at"] == e["state_changed_at"]
    assert st.facility_name("f-1") == "F One" and st.facility_state("f-1") == "active"


def test_tombstones_refuse_reuse(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    reg = FacilityRegistry(st.root)
    st.write_role_frame(_frame(), facility_id="gone", equip="AHU_1", name="Gone")
    assert reg.remove("gone") is True and "gone" not in reg.all()
    assert reg.tombstones()["gone"]["name"] == "Gone"
    assert reg.remove("gone") is False  # already removed; still tombstoned
    with pytest.raises(ValueError, match="tombstoned.*never reused"):
        reg.register("gone", name="A New Building")
    with pytest.raises(ValueError, match="tombstoned"):
        st.write_role_frame(_frame(), facility_id="gone", equip="AHU_1")  # no joining old data
    # drop_facility(forget=True) tombstones even an unregistered facility that had data
    st.write_role_frame(_frame(), facility_id="bare", equip="AHU_1")
    assert st.drop_facility("bare", forget=True) > 0
    assert "bare" in reg.tombstones()
    assert reg.reclaim("bare", reason="same facility, re-created") is True
    assert reg.reclaim("bare", reason="again") is False
    st.write_role_frame(_frame(), facility_id="bare", equip="AHU_1")  # allowed again


def test_case_insensitive_collisions_are_refused(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    reg = FacilityRegistry(st.root)
    st.write_role_frame(_frame(), facility_id="DemoSite", equip="AHU_1")  # data, unregistered
    with pytest.raises(ValueError, match="differs only by letter case.*'DemoSite'"):
        reg.register("demosite", name="Demo")
    with pytest.raises(ValueError, match="letter case"):
        st.write_role_frame(_frame(), facility_id="DEMOSITE", equip="AHU_1")
    reg.register("DemoSite", name="Demo")  # the exact id is fine
    reg.register("x-1", name="X")
    reg.remove("x-1")
    with pytest.raises(ValueError, match="letter case"):
        reg.register("X-1", name="X again")


def test_registry_without_workspace_takes_no_lock_and_writes_no_audit(tmp_path):
    st = ParquetStore(str(tmp_path / "db"))
    st.write_role_frame(_frame(), facility_id="f", equip="AHU_1", name="F")
    assert not os.path.exists(tmp_path / "db" / "_lock")
    assert not os.path.exists(tmp_path / "_audit.ndjson")
    # a marker pointing at a missing / non-workspace directory is ignored
    json.dump({"portfolio": ".."}, open(tmp_path / "db" / "_workspace.json", "w"))
    st.register_facility("f", name="F")
    json.dump({"portfolio": 3}, open(tmp_path / "db" / "_workspace.json", "w"))
    st.register_facility("f", name="F")
    assert not os.path.exists(tmp_path / "_audit.ndjson")


# --------------------------------------------------------------------------- portfolio API


def test_add_transition_rename_and_audit(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    e = pf.add_facility("North Campus", reason="onboarding", owner="ops", tags=["east", "east"])
    fid = e["facility_id"]
    assert fid == make_facility_id("North Campus")
    assert e["state"] == "provisioning" and e["owner"] == "ops" and e["portfolio"] == ["east"]
    with pytest.raises(ValueError, match="already registered"):
        pf.add_facility("North Campus", reason="dup")
    with pytest.raises(ValueError, match="reason is required"):
        pf.transition(fid, "activate", reason=" ")
    with pytest.raises(LifecycleError, match="allowed from provisioning: activate"):
        pf.transition(fid, "suspend", reason="too early")
    assert pf.transition(fid, "activate", reason="commissioned")["to_state"] == "active"
    assert pf.transition(fid, "suspend", reason="contract paused")["to_state"] == "suspended"
    assert pf.facilities(state="suspended").keys() == {fid}
    assert pf.transition(fid, "resume", reason="contract resumed")["to_state"] == "active"
    with pytest.raises(NotImplementedError, match="later release"):
        pf.transition(fid, "offboard", reason="leaving")
    assert pf.registry.state(fid) == "active"  # a refused action changes nothing
    r = pf.rename(fid, "North Campus (Bldg 2)", reason="owner's naming")
    assert r["display_name"] == "North Campus (Bldg 2)" and r["name"] == "North Campus"
    assert pf.store.facility_name(fid) == "North Campus (Bldg 2)"
    with pytest.raises(ValueError, match="empty"):
        pf.rename(fid, "  ", reason="x")
    with pytest.raises(KeyError, match="unknown facility"):
        pf.transition("ghost", "suspend", reason="x")
    with pytest.raises(ValueError, match="unknown state"):
        pf.facilities(state="limbo")
    log = pf.audit_log(facility_id=fid)
    assert [r["action"] for r in log] == [
        "facility.add",
        "facility.activate",
        "facility.suspend",
        "facility.resume",
        "facility.rename",
    ]
    assert all(r["actor"] and r["host"] and r["reason"] for r in log)
    assert log[2]["from_state"] == "active" and log[2]["to_state"] == "suspended"
    assert log[4]["details"] == {"from": "North Campus", "to": "North Campus (Bldg 2)"}
    st = pf.status()
    assert st["by_state"]["active"] == 1 and st["audit_records"] == 6 and st["locked_by"] is None


def test_legal_hold_blocks_deleting_actions_in_the_workspace(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility("Held", reason="x", activate=True)["facility_id"]
    reg = pf.registry
    with reg._locked():
        reg._update(fid, {"state": "archived"})  # stand-in for a later release's archive step
    p = tmp_path / "ws" / "_portfolio.json"
    doc = json.load(open(p))
    doc["legal_holds"] = {fid: {"reason": "litigation"}}
    json.dump(doc, open(p, "w"))
    with pytest.raises(LifecycleError, match="legal hold"):
        pf.transition(fid, "purge", reason="cleanup")
    with pytest.raises(NotImplementedError):  # a non-deleting action is not blocked by the hold
        pf.transition(fid, "restore", reason="back")


def test_lifecycle_on_a_bare_store_partition_registers_it(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    pf.store.write_role_frame(_frame(), facility_id="landed", equip="AHU_1")  # e.g. edge-landed
    assert pf.facility("landed")["registered"] is False
    pf.transition("landed", "suspend", reason="unpaid")
    assert [r["action"] for r in pf.audit_log(facility_id="landed")] == [
        "facility.register",
        "facility.suspend",
    ]
    pf.rename("landed", "Landed Bldg", reason="named")
    fid2 = "landed-2"
    pf.store.write_role_frame(_frame(), facility_id=fid2, equip="AHU_1")
    pf.rename(fid2, "Second", reason="named")
    assert pf.facility(fid2)["registered"] is True
    pf.registry.remove(fid2, reason="retired")
    with pytest.raises(KeyError, match="tombstoned"):
        pf.facility(fid2)
    assert pf.status()["tombstoned_with_data"] == [fid2]


def test_purged_facility_cannot_be_renamed(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility("P", reason="x")["facility_id"]
    reg = pf.registry
    with reg._locked():
        reg._update(fid, {"state": "purged"})
    with pytest.raises(LifecycleError, match="purged"):
        pf.rename(fid, "Q", reason="x")
    with pytest.raises(KeyError, match="not registered"):
        reg._update("nobody", {})


# --------------------------------------------------------------------------- analyses skip


def _suspended_store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id="fac", equip="AHU_1", equip_class="AHU", name="Fac")
    st.write_role_frame(_frame(), facility_id="other", equip="AHU_1", equip_class="AHU")
    reg = FacilityRegistry(st.root)
    reg._update("fac", {"state": "suspended"})
    return st


def test_active_facilities_and_discover_store_skip_suspended(tmp_path):
    st = _suspended_store(tmp_path)
    assert st.facilities() == ["fac", "other"] and st.active_facilities() == ["other"]
    with pytest.warns(UserWarning, match="'fac' is suspended; skipping"):
        assert discover_store(st, "fac", "AHU") == []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert [r.equip for r in discover_store(st, "fac", "AHU", include_inactive=True)] == [
            "AHU_1"
        ]
        assert len(discover_store(st, "other", "AHU")) == 1


def test_store_config_skips_suspended_with_one_warning_and_override(tmp_path):
    _suspended_store(tmp_path)
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": "fac"},
        "equipment": [{"class": "AHU"}],
        "shared_oat": {"equip": "AHU_1", "role": "oat"},
        "rules": ["simultaneous_heat_cool"],
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = run_config(cfg, base_dir=str(tmp_path))
    skipped = [w for w in caught if "is suspended; skipping" in str(w.message)]
    assert len(skipped) == 1 and res.equipment == 0 and res.findings == []
    cfg["source"]["include_inactive"] = True
    assert run_config(cfg, base_dir=str(tmp_path)).equipment == 1
    del cfg["source"]["include_inactive"]
    cfg["include_inactive"] = True  # top-level spelling accepted too
    assert run_config(cfg, base_dir=str(tmp_path)).equipment == 1


def test_read_api_shows_lifecycle_state(tmp_path):
    st = _suspended_store(tmp_path)
    FacilityRegistry(st.root)._update("fac", {"display_name": "Fac (renamed)"})
    facs = {f["facility_id"]: f for f in ReadAPI(st).facilities()["facilities"]}
    assert facs["fac"] == {
        "facility_id": "fac",
        "name": "Fac",
        "display_name": "Fac (renamed)",
        "state": "suspended",
    }
    assert facs["other"]["state"] == "active" and facs["other"]["display_name"] == "other"
