"""0.107: the dataset catalog's writers follow the facility lifecycle (#212), an ingest's partition
swap survives a crash at any point (#213), and `datasets config` refuses a dataset that is not in
the store (#218).

Every test works in its own tmp directory: the cache (``--dir``) and the store (``--store``) are
always passed explicitly, so no default cache is ever touched.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

import camber.datasets as ds  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ingest, _lifecycle, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.portfolio import LifecycleError, Portfolio  # noqa: E402
from camber.store import (  # noqa: E402
    FacilityRegistry,
    ParquetStore,
    _normalize,  # noqa: E402
)

FID = "ds-test-ahu"


@pytest.fixture
def cat(tmp_path, monkeypatch):
    """The test catalog (one AHU dataset) behind the CLI, with a cache and a workspace in tmp."""
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    opener = FakeOpener({BASE + "test.zip": zb})
    cache = str(tmp_path / "cache")
    monkeypatch.setenv("CAMBER_DATA_DIR", cache)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    real = _ops.fetch_dataset
    monkeypatch.setattr(
        ds, "fetch_dataset", lambda e, **kw: real(e, **{**kw, "opener": kw.get("opener") or opener})
    )
    _ops.fetch_dataset(entry, data_dir=cache, opener=opener)
    return entry, cache, tmp_path


def _ws(tmp):
    pf = Portfolio.init(tmp / "ws")
    return pf, pf.store_root


def _cli(*argv) -> int:
    return main(list(argv))


def _actions(pf, fid=None) -> list:
    return [r["action"] for r in pf.audit_log(facility_id=fid)]


# --------------------------------------------------------------------------- #212 lifecycle


def test_cli_ingest_into_a_workspace_needs_a_reason_and_is_audited(cat, capsys):
    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    assert _cli("datasets", "ingest", "test-ahu", "--dir", cache, "--store", store) == 1
    assert "needs --reason" in capsys.readouterr().err
    assert ParquetStore(store).facilities() == []
    assert (
        _cli(
            "datasets", "ingest", "test-ahu", "--dir", cache, "--store", store, "--reason", "course"
        )
        == 0
    )
    rec = [r for r in pf.audit_log(facility_id=FID) if r["action"] == "datasets.ingest"]
    assert len(rec) == 1 and rec[0]["reason"] == "course"
    assert rec[0]["details"]["dataset_id"] == "test-ahu" and rec[0]["details"]["rows"] > 0


def test_a_held_facility_is_never_purged_from_the_cli(cat, capsys):
    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    _ingest.ingest_dataset(entry, store, data_dir=cache, reason="setup")
    pf.hold(FID, reason="litigation")
    rows = ParquetStore(store).facilities()
    argv = ("datasets", "remove", "test-ahu", "--dir", cache, "--store", store, "--purge-store")
    assert _cli(*argv) == 1  # no reason
    assert "needs --reason" in capsys.readouterr().err
    assert _cli(*argv, "--reason", "done") == 1  # no confirmation
    assert "--yes" in capsys.readouterr().err
    assert _cli(*argv, "--reason", "done", "--yes") == 1  # the hold
    err = capsys.readouterr().err
    assert "under a legal hold" in err and "nothing was removed" in err
    assert ParquetStore(store).facilities() == rows  # data kept
    assert FID not in FacilityRegistry(store).tombstones()  # id not retired
    assert ds.status(data_dir=cache)[0]["fetched"]["default"]  # nor the cache emptied
    assert "datasets.purge" not in _actions(pf)
    pf.release_hold(FID, reason="settled")
    assert _cli(*argv, "--reason", "course over", "--confirm", "test-ahu") == 0
    assert ParquetStore(store).facilities() == []
    recs = {r["action"]: r for r in pf.audit_log()}
    assert recs["datasets.remove"]["reason"] == "course over"
    assert recs["datasets.purge"]["facility_id"] == FID


def test_a_held_facility_refuses_a_forced_re_ingest_on_every_path(cat, capsys):
    """A legal hold means nothing held is deleted: replacing the partition is refused."""
    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    _ingest.ingest_dataset(entry, store, data_dir=cache, reason="setup")
    n = _rows(store)
    pf.hold(FID, reason="litigation")
    argv = ("datasets", "ingest", "test-ahu", "--dir", cache, "--store", store, "--reason", "r")
    assert _cli(*argv, "--force") == 1
    err = capsys.readouterr().err
    assert "under a legal hold" in err and "nothing was ingested" in err
    with pytest.raises(LifecycleError, match="legal hold"):
        _ingest.ingest_dataset(entry, store, data_dir=cache, reason="r", force=True)
    # a changed ingest (here: corrections off) would replace it too
    with pytest.raises(LifecycleError, match="legal hold"):
        _ingest.ingest_dataset(entry, store, data_dir=cache, reason="r", corrections=False)
    assert _rows(store) == n and not os.listdir(os.path.join(store, "_staging"))
    assert _cli(*argv) == 0  # unchanged: skipped, nothing deleted
    assert "skipped" in capsys.readouterr().out
    pf.release_hold(FID, reason="settled")
    assert _cli(*argv, "--force") == 0


@pytest.mark.parametrize("state", ["suspended", "offboarding", "archived"])
def test_a_facility_outside_provisioning_active_refuses_ingest_and_purge(cat, state, capsys):
    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    _ingest.ingest_dataset(entry, store, data_dir=cache, reason="setup")
    if state == "suspended":
        pf.transition(FID, "suspend", reason="t")
    else:
        pf.offboard(FID, reason="t", apply=True)
        if state == "archived":
            pf.archive(FID, reason="t", apply=True, skip_grace=True)
    before = sorted(os.listdir(store))
    argv = ("--dir", cache, "--store", store, "--reason", "r")
    assert _cli("datasets", "ingest", "test-ahu", *argv, "--force") == 1
    err = capsys.readouterr().err
    assert f"facility {FID} is {state}" in err and "nothing was ingested" in err
    assert sorted(os.listdir(store)) == before  # no hot data back in the store
    assert _cli("datasets", "remove", "test-ahu", *argv, "--purge-store", "--yes") == 1
    assert f"{FID} is {state}" in capsys.readouterr().err
    assert pf.registry.state(FID) == state and FID not in pf.registry.tombstones()
    assert _actions(pf, FID).count("datasets.ingest") == 1  # the setup's only


def test_the_ingest_swap_passes_the_registry_write_guard(cat):
    """A plain store: an archived facility (no workspace to follow) still refuses the swap."""
    entry, cache, tmp = cat
    store = str(tmp / "plain")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    FacilityRegistry(store)._update(FID, {"state": "archived"})
    with pytest.raises(ValueError, match="archived"):
        _ingest.ingest_dataset(entry, store, data_dir=cache, force=True)
    # the old partition is untouched and no staging was left behind
    assert FID in ParquetStore(store).facilities()
    assert not os.listdir(os.path.join(store, "_staging"))


def test_plain_store_cli_flow_is_unchanged(cat):
    entry, cache, tmp = cat
    store = str(tmp / "plain")
    assert _cli("datasets", "ingest", "test-ahu", "--dir", cache, "--store", store) == 0
    argv = ("datasets", "remove", "test-ahu", "--dir", cache, "--store", store, "--purge-store")
    assert _cli(*argv) == 0  # no workspace: no reason, no confirmation
    assert ParquetStore(store).facilities() == []


def test_shared_rules_are_what_the_lab_uses(cat):
    from camber.lab import _app

    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    _ingest.ingest_dataset(entry, store, data_dir=cache, reason="setup")
    pf.hold(FID, reason="x")
    app = _app.LabApp(workspace=pf.root, data_dir=cache, entries=[entry])
    try:
        assert app._purge_blockers(entry) == _lifecycle.purge_blockers(pf, entry.id, by="the lab")
        assert "legal hold" in app._purge_blockers(entry)[0]
    finally:
        app.close()
    known = pf.registry.all()
    pf.transition(FID, "suspend", reason="x")
    assert _lifecycle.ingest_blockers(known, [FID]) == []  # the snapshot said active
    why = _lifecycle.ingest_blockers(pf.registry.all(), [FID], by="the lab")
    assert why == [
        f"facility {FID} is suspended: the lab ingests only into provisioning or active "
        "facilities (use `camber facility resume|restore` first)"
    ]
    with pytest.raises(LifecycleError):
        _ingest.ingest_dataset(entry, store, data_dir=cache, reason="r", force=True)


def test_an_ingest_never_drops_a_held_facility_its_subset_no_longer_produces(cat, monkeypatch):
    entry, cache, tmp = cat
    pf, store = _ws(tmp)
    _ingest.ingest_dataset(entry, store, data_dir=cache, reason="setup")
    # a second facility of the same dataset (e.g. a BDG2 site the new subset no longer has)
    reg = FacilityRegistry(store)
    other = "ds-test-ahu-old-site"
    meta = reg.get(FID)["dataset"]
    ParquetStore(store).write_long(_frame(), facility_id=other, dataset=meta)
    pf.hold(other, reason="kept")
    with pytest.raises(LifecycleError, match="legal hold"):
        _ingest.ingest_dataset(entry, store, data_dir=cache, reason="r", force=True)
    assert other in ParquetStore(store).facilities()
    pf.release_hold(other, reason="released")
    res = _ingest.ingest_dataset(entry, store, data_dir=cache, reason="r", force=True)
    assert res.dropped == [other] and other not in ParquetStore(store).facilities()
    assert "datasets.purge" in _actions(pf, other)


def _frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "ts": pd.to_datetime(["2020-01-01"]),
            "equip": ["AHU"],
            "equip_class": ["AHU"],
            "role": ["oat"],
            "value": [50.0],
        }
    )


# --------------------------------------------------------------------------- #213 crash safety


def _rows(store) -> int:
    return sum(p["rows"] for p in ParquetStore(store).partitions(facility_id=FID))


def _crash_on(monkeypatch, fn_name: str, when):
    """Make ``os.<fn_name>(src, dst)`` raise when ``when(src, dst)`` is true: a simulated crash
    at that step (the ingest's own ``finally`` still removes its staging directory, as it would
    not after a kill; the swap's leftovers at the store root are what a crash leaves)."""
    real = getattr(os, fn_name)

    def boom(src, dst, *a, **kw):
        if when(os.fspath(src), os.fspath(dst)):
            raise KeyboardInterrupt(f"simulated crash in os.{fn_name}")
        return real(src, dst, *a, **kw)

    boom.__wrapped__ = real
    monkeypatch.setattr(os, fn_name, boom)


@pytest.mark.parametrize(
    "fn_name, when, stranded",
    [
        # the staged partition moving into the swap's .new: the target is untouched
        ("replace", lambda s, d: d.endswith(f"_swap-facility_id={FID}.new"), False),
        # the old partition moving aside: .ready is written, the new content complete
        ("rename", lambda s, d: d.endswith(f"_swap-facility_id={FID}.old"), False),
        # the worst moment: the old partition is aside, the new one not yet in place
        ("rename", lambda s, d: s.endswith(f"_swap-facility_id={FID}.new"), True),
    ],
    ids=["before-swap", "moving-old-aside", "old-aside-new-not-in"],
)
def test_a_crash_mid_swap_leaves_the_old_or_the_new_partition(
    cat, monkeypatch, capsys, fn_name, when, stranded
):
    entry, cache, tmp = cat
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    n = _rows(store)
    _crash_on(monkeypatch, fn_name, when)
    with pytest.raises(KeyboardInterrupt):
        _ingest.ingest_dataset(entry, store, data_dir=cache, force=True)
    monkeypatch.setattr(os, fn_name, getattr(os, fn_name).__wrapped__)
    rep = _normalize.verify(store)
    assert rep["ok"] is not stranded
    assert any(x["stranded"] for x in rep["leftovers"]) is stranded
    assert (FID in ParquetStore(store).facilities()) is not stranded
    if stranded:
        assert _cli("store", "verify", store) == 1
        assert "! a facility's data is outside its partition" in capsys.readouterr().out
    # the next ingest recovers the facility whole: never a mix, never missing
    res = _ingest.ingest_dataset(entry, store, data_dir=cache)
    assert res.skipped and FID in ParquetStore(store).facilities() and _rows(store) == n
    assert _normalize.verify(store)["leftovers"] == []
    assert _cli("store", "verify", store, "--strict") == 0


def test_store_verify_flags_a_stranded_partition_and_the_next_ingest_restores_it(cat, capsys):
    """What a pre-0.107 crash left: the partition in ``_staging/<fid>.old-<pid>`` and the
    ingest's working directory, both of a process that is gone."""
    entry, cache, tmp = cat
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    n = _rows(store)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sdir = os.path.join(store, "_staging")
    os.replace(
        os.path.join(store, f"facility_id={FID}"), os.path.join(sdir, f"{FID}.old-{dead.pid}")
    )
    scratch = os.path.join(sdir, f"ingest-{dead.pid}-abc123")
    os.makedirs(os.path.join(scratch, "facility_id=x"))
    alive = os.path.join(sdir, f"ingest-{os.getppid()}-def456")  # a running process: left alone
    os.makedirs(alive)

    assert _cli("store", "verify", store) == 1  # stranded data fails even without --strict
    out = capsys.readouterr().out
    assert f"partition moved aside by an interrupted ingest of {FID}" in out
    assert "ingest staging directory" in out and "registered but has no part files" in out
    assert _cli("store", "verify", store, "--json") == 1
    rep = json.loads(capsys.readouterr().out)
    assert rep["ok"] is False and rep["empty_facilities"] == [
        {"facility_id": FID, "state": "active"}
    ]

    res = _ingest.ingest_dataset(entry, store, data_dir=cache)  # recovers, then is up to date
    assert res.skipped and any("partition_restored" in x for x in res.warnings)
    assert _rows(store) == n
    assert sorted(os.listdir(sdir)) == [os.path.basename(alive)]
    assert _cli("store", "verify", store) == 0  # the live one is listed, not a failure
    assert _cli("store", "verify", store, "--strict") == 1
    os.rmdir(alive)
    assert _cli("store", "verify", store, "--strict") == 0


def test_a_stale_aside_beside_a_new_partition_is_removed(cat):
    entry, cache, tmp = cat
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    aside = os.path.join(store, "_staging", f"{FID}.old-{dead.pid}")
    os.makedirs(os.path.join(aside, "year=2018"))
    done = _ingest.recover_staging(ParquetStore(store))
    assert [d["action"] for d in done] == ["stale_partition_removed"]
    assert not os.path.exists(aside) and FID in ParquetStore(store).facilities()


# --------------------------------------------------------------------------- #218 datasets config


def test_datasets_config_refuses_a_dataset_not_in_the_store(cat, capsys):
    entry, cache, tmp = cat
    store = str(tmp / "store")
    out = str(tmp / "run.json")
    assert _cli("datasets", "config", "test-ahu", "--store", store, "--out", out) == 1
    err = capsys.readouterr().err
    assert "test-ahu is not ingested in" in err and "camber datasets ingest test-ahu" in err
    assert not os.path.exists(out) and not os.path.exists(store)
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    assert (
        _cli("datasets", "config", "test-ahu", "--store", store, "--out", out, "--facility", "x")
        == 1
    )
    assert "facility 'x' of test-ahu is not ingested" in capsys.readouterr().err
    assert _cli("datasets", "config", "test-ahu", "--store", store, "--out", out) == 0
    assert json.load(open(out))["source"]["facility_id"] == FID
    # printing a template for a look still works, with a warning
    assert _cli("datasets", "config", "test-ahu", "--store", str(tmp / "empty")) == 0
    cap = capsys.readouterr()
    assert "warning: test-ahu is not ingested" in cap.err and '"rules"' in cap.out
