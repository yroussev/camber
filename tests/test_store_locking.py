"""Every plain-store writer takes the store lock, and --lock-timeout / $CAMBER_LOCK_TIMEOUT
(0.104, #135). The lock itself is tested in test_datasets_locking.py (0.103, #124).
"""

import os
import subprocess
import sys
import textwrap
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402
from test_interop_openfdd import TZ, make_package  # noqa: E402
from test_store_reproducible import _threaded_legacy_write  # noqa: E402

from camber import datasets as ds  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ingest, _ops, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.interop.openfdd import ingest_package  # noqa: E402
from camber.lab._jobs import JobQueue  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.portfolio import _lock as plock  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore  # noqa: E402
from camber.store._lock import StoreLocked, store_lock  # noqa: E402
from camber.store.bench import synth_portfolio  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAST = 5.0  # "at once": well under the 30 s default wait


def _hold(kind: str, path: str, seconds: float = 30.0):
    """A subprocess holding the ``kind`` ("store" / "cache") lock on ``path``, once held."""
    code = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {REPO!r})
        if {kind!r} == "store":
            from camber.store._lock import store_lock as lk
        else:
            from camber.datasets._paths import cache_lock as lk
        with lk({path!r}, timeout=0):
            print("held", flush=True)
            time.sleep({seconds!r})
        """
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def _stop(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(10)
    proc.stdout.close()


def _frame(start="2024-01-01", n=48):
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame({"supply_air_temp": range(n)}, index=idx, dtype=float)


def _seeded(root: str) -> ParquetStore:
    st = ParquetStore(root)
    st.write_role_frame(_frame("2023-06-01"), facility_id="f1", equip="AHU_1", name="F1")
    st.write_role_frame(_frame(), facility_id="f1", equip="AHU_1")
    st.write_role_frame(_frame(), facility_id="f2", equip="AHU_1")
    return st


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(plock.LOCK_TIMEOUT_ENV, raising=False)


@pytest.fixture
def lock_opens(monkeypatch):
    """Counts fresh (non-re-entrant) acquisitions of each ``_lock`` file, by its directory."""
    counts: dict = {}
    real = plock._open

    def counting(path):
        d = os.path.dirname(os.path.realpath(path))
        counts[d] = counts.get(d, 0) + 1
        return real(path)

    monkeypatch.setattr(plock, "_open", counting)
    return counts


# --------------------------------------------------------------------------- every writer


def _legacy(root):
    _threaded_legacy_write(root, _frame("2022-03-01"))


WRITERS = {
    "write_role_frame": lambda st: st.write_role_frame(_frame(), facility_id="f3", equip="AHU_1"),
    "write_long": lambda st: st.write_long(
        pd.DataFrame(
            {
                "ts": pd.to_datetime(["2024-02-01"]),
                "equip": ["AHU_1"],
                "equip_class": [""],
                "role": ["supply_air_temp"],
                "value": [1.0],
            }
        ),
        facility_id="f3",
    ),
    "register_facility": lambda st: st.register_facility("f3", name="F3"),
    "prune": lambda st: st.prune(before_year=2024),
    "drop_partition": lambda st: st.drop_partition("f1", 2024, 1),
    "drop_facility": lambda st: st.drop_facility("f2", forget=True),
    "migrate_partitions": lambda st: st.migrate_partitions(apply=True),
    "registry.register": lambda st: FacilityRegistry(st.root, lock_timeout=0).register("f9"),
    "registry.remove": lambda st: FacilityRegistry(st.root, lock_timeout=0).remove("f1"),
}


@pytest.mark.parametrize("op", sorted(WRITERS))
def test_every_store_writer_is_refused_while_another_process_holds_the_lock(tmp_path, no_env, op):
    root = str(tmp_path / "store")
    _seeded(root)
    _legacy(root)
    before = sorted(
        os.path.relpath(os.path.join(d, f), root)
        for d, _, fs in os.walk(root)
        for f in fs
        if f != plock.LOCK_FILE
    )
    proc = _hold("store", root)
    try:
        t0 = time.monotonic()
        with pytest.raises(StoreLocked, match=f"store .* is locked by {proc.pid}@"):
            WRITERS[op](ParquetStore(root, lock_timeout=0))
        assert time.monotonic() - t0 < FAST
    finally:
        _stop(proc)
    after = sorted(
        os.path.relpath(os.path.join(d, f), root)
        for d, _, fs in os.walk(root)
        for f in fs
        if f != plock.LOCK_FILE
    )
    assert after == before  # nothing was written or deleted
    WRITERS[op](ParquetStore(root, lock_timeout=0))  # and it works once the holder is gone


def test_write_rollup_locks_the_destination(tmp_path, no_env):
    src = _seeded(str(tmp_path / "src"))
    dest = str(tmp_path / "dest")
    proc = _hold("store", dest)
    try:
        with pytest.raises(StoreLocked, match=f"locked by {proc.pid}@"):
            src.write_rollup("D", ParquetStore(dest, lock_timeout=0))
        with store_lock(src.root, timeout=0):  # the source is only read: its lock is not needed
            pass
    finally:
        _stop(proc)
    assert src.write_rollup("D", ParquetStore(dest)) > 0


def test_a_writer_waits_for_the_holder_then_writes(tmp_path, no_env):
    root = str(tmp_path / "store")
    proc = _hold("store", root, seconds=0.5)
    try:
        t0 = time.monotonic()
        n = ParquetStore(root, lock_timeout=20).write_role_frame(
            _frame(), facility_id="f1", equip="AHU_1"
        )
        assert n == 48 and time.monotonic() - t0 >= 0.1  # it waited for the holder
    finally:
        _stop(proc)


def test_reads_take_no_lock(tmp_path, no_env):
    root = str(tmp_path / "store")
    st = _seeded(root)
    st.points()
    proc = _hold("store", root)
    try:
        ro = ParquetStore(root, lock_timeout=0)
        assert ro.facilities() == ["f1", "f2"]
        assert len(ro.read_role_frame(facility_id="f1", equip="AHU_1")) == 96
        assert ro.points() and ro.partitions() and ro.facilities_meta()
        assert ro.migrate_partitions()["dry_run"]  # the dry run reads only
    finally:
        _stop(proc)


def test_openfdd_ingest_into_a_plain_store_takes_the_lock(tmp_path, no_env):
    pkg = make_package(str(tmp_path / "pkg"), days=3, meter_days=3)
    store = str(tmp_path / "store")
    proc = _hold("store", store)
    try:
        with pytest.raises(StoreLocked, match=f"locked by {proc.pid}@"):
            ingest_package(pkg, timezone=TZ, unit_system="ip", store=store, lock_timeout=0)
    finally:
        _stop(proc)
    assert ParquetStore(store).facilities() == []
    res = ingest_package(pkg, timezone=TZ, unit_system="ip", store=store)
    assert res.rows > 0 and os.path.isfile(os.path.join(store, plock.LOCK_FILE))


# --------------------------------------------------------------------------- nesting and bulk


def test_nested_operations_under_one_lock_do_not_deadlock(tmp_path, no_env):
    root = str(tmp_path / "store")
    _legacy(root)
    st = ParquetStore(root, lock_timeout=0)
    with st.lock(timeout=0):
        with store_lock(root, timeout=0):
            st.write_role_frame(_frame(), facility_id="f1", equip="AHU_1", name="F1")
            st.register_facility("f1", name="F1")
            FacilityRegistry(root, lock_timeout=0).register("f2", name="F2")
            assert st.migrate_partitions(apply=True)["applied"]
            st.write_rollup("D", ParquetStore(root, lock_timeout=0), facility_id="f1")
            st.drop_partition("f1", 2024, 1)
            st.drop_facility("old", forget=True)
            assert st.prune(before_year=2000) == 0
    assert plock.probe(root) is None  # released


def test_a_bulk_load_takes_the_store_lock_once(tmp_path, no_env, lock_opens):
    st = ParquetStore(str(tmp_path / "store"))
    rows = synth_portfolio(st, facilities=3, equips=4, roles=2, days=2)
    assert rows > 0
    assert lock_opens == {os.path.realpath(st.root): 1}  # 12 writes + 3 registrations, 1 lock


def test_a_write_outside_a_held_lock_locks_per_call(tmp_path, no_env, lock_opens):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id="f1", equip="AHU_1", name="F1")
    st.write_role_frame(_frame(), facility_id="f1", equip="AHU_2")
    assert lock_opens == {os.path.realpath(st.root): 2}


def test_openfdd_ingest_takes_the_store_lock_once(tmp_path, no_env, lock_opens):
    pkg = make_package(str(tmp_path / "pkg"), days=3, meter_days=3)
    store = str(tmp_path / "store")
    res = ingest_package(pkg, timezone=TZ, unit_system="ip", store=store)
    assert res.equipment > 1
    assert lock_opens == {os.path.realpath(store): 1}  # staging writes take no lock of their own


@pytest.fixture
def ahu(tmp_path, monkeypatch):
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(plock.LOCK_TIMEOUT_ENV, raising=False)
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    _ops.fetch_dataset(entry, opener=FakeOpener({BASE + "test.zip": zb}))
    return entry, tmp_path


def test_a_dataset_ingest_takes_the_store_lock_once(ahu, lock_opens):
    entry, tmp = ahu
    store = str(tmp / "store")
    res = _ingest.ingest_dataset(entry, store)
    assert res.rows > 0
    assert lock_opens[os.path.realpath(store)] == 1
    staging = os.path.realpath(os.path.join(store, _ingest._STAGING))
    assert not [d for d in lock_opens if d.startswith(staging)]  # no lock in the staging area


def test_retention_rollup_stores_get_no_lock_file_of_their_own(tmp_path):
    from camber.portfolio._retention import _rollup_store

    pf = Portfolio.init(tmp_path / "ws")
    rs = _rollup_store(pf, "hourly")
    with pf.lock(timeout=0):
        rs.write_role_frame(_frame(), facility_id="f1", equip="AHU_1")
        rs.drop_partition("f1", 2024, 1)
    assert not os.path.exists(os.path.join(rs.root, plock.LOCK_FILE))


# --------------------------------------------------------------------------- --lock-timeout / env


def test_parse_lock_timeout():
    assert plock.parse_lock_timeout("0") == 0.0
    assert plock.parse_lock_timeout(" 2.5 ") == 2.5
    for bad in ("-1", "abc", "nan", "inf", "", None):
        with pytest.raises(ValueError, match="number of seconds >= 0"):
            plock.parse_lock_timeout(bad)


def test_default_wait_scope_then_env_then_30(monkeypatch):
    monkeypatch.delenv(plock.LOCK_TIMEOUT_ENV, raising=False)
    assert plock.default_lock_timeout() == 30.0
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "7")
    assert plock.default_lock_timeout() == 7.0
    with plock.lock_wait(0):
        assert plock.default_lock_timeout() == 0.0
        with plock.lock_wait(None):  # None leaves the current wait alone
            assert plock.default_lock_timeout() == 0.0
    assert plock.default_lock_timeout() == 7.0
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "soon")
    with pytest.raises(ValueError, match=r"\$CAMBER_LOCK_TIMEOUT must be"):
        plock.default_lock_timeout()


def test_env_var_zero_fails_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "0")
    root = str(tmp_path / "store")
    proc = _hold("store", root)
    try:
        t0 = time.monotonic()
        with pytest.raises(StoreLocked, match=f"locked by {proc.pid}@.*waited 0 s"):
            ParquetStore(root).write_role_frame(_frame(), facility_id="f1", equip="AHU_1")
        assert time.monotonic() - t0 < FAST
    finally:
        _stop(proc)


def test_env_var_applies_to_the_cache_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "0")
    cache = str(tmp_path / "cache")
    proc = _hold("cache", cache)
    try:
        with pytest.raises(_paths.CacheLocked, match=f"locked by {proc.pid}@.*waited 0 s"):
            with _paths.cache_lock(cache):
                pass
    finally:
        _stop(proc)


def test_lab_jobs_run_with_the_lab_lock_timeout(monkeypatch):
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "9")
    q = JobQueue(job_context=lambda: plock.lock_wait(0))
    job = q.submit("t", {}, lambda job: plock.default_lock_timeout())
    job._finished.wait(10)
    q.close()
    assert job.result == 0.0  # not the env's 9: the job thread got the lab's --lock-timeout


def _store_held_cli(tmp_path, argv, *, kind="store", path=None):
    proc = _hold(kind, path or str(tmp_path / "store"))
    try:
        t0 = time.monotonic()
        rc = main(argv)
        return rc, time.monotonic() - t0, proc.pid
    finally:
        _stop(proc)


def test_cli_migrate_partitions_lock_timeout_zero_fails_at_once(tmp_path, no_env, capsys):
    root = str(tmp_path / "store")
    _legacy(root)
    rc, took, pid = _store_held_cli(
        tmp_path,
        ["store", "migrate-partitions", root, "--apply", "--yes", "--lock-timeout", "0"],
    )
    assert rc == 1 and took < FAST
    assert f"is locked by {pid}@" in capsys.readouterr().err
    assert main(["store", "migrate-partitions", root, "--apply", "--yes"]) == 0


def test_cli_openfdd_ingest_lock_timeout_zero_fails_at_once(tmp_path, no_env, capsys):
    pkg = make_package(str(tmp_path / "pkg"), days=3, meter_days=3)
    store = str(tmp_path / "store")
    argv = ["interop", "openfdd", "ingest", pkg, "--timezone", TZ, "--units", "ip"]
    rc, took, pid = _store_held_cli(tmp_path, argv + ["--store", store, "--lock-timeout", "0"])
    assert rc == 1 and took < FAST
    assert f"is locked by {pid}@" in capsys.readouterr().err


def test_cli_datasets_ingest_lock_timeout_zero_fails_at_once(ahu, capsys):
    entry, tmp = ahu
    store = str(tmp / "store")
    argv = ["datasets", "ingest", entry.id, "--store", store, "--quiet"]
    rc, took, pid = _store_held_cli(tmp, argv + ["--lock-timeout", "0"])
    assert rc == 1 and took < FAST
    assert f"store {os.path.abspath(store)} is locked by {pid}@" in capsys.readouterr().err
    assert ParquetStore(store).facilities() == []


def test_cli_datasets_ingest_env_var_and_flag(ahu, monkeypatch, capsys):
    entry, tmp = ahu
    store = str(tmp / "store")
    argv = ["datasets", "ingest", entry.id, "--store", store, "--quiet"]
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "0")
    rc, took, pid = _store_held_cli(tmp, argv)
    assert rc == 1 and took < FAST and f"locked by {pid}@" in capsys.readouterr().err
    # the flag wins over the variable: wait for a holder that lets go after half a second
    proc = _hold("store", store, seconds=0.5)
    try:
        assert main(argv + ["--lock-timeout", "20"]) == 0
    finally:
        _stop(proc)
    monkeypatch.setenv(plock.LOCK_TIMEOUT_ENV, "-3")
    assert main(argv) == 2
    assert "$CAMBER_LOCK_TIMEOUT must be a number of seconds >= 0" in capsys.readouterr().err


def test_cli_datasets_remove_lock_timeout_zero_fails_at_once(ahu, capsys):
    entry, tmp = ahu
    cache = str(tmp / "cache")
    rc, took, pid = _store_held_cli(
        tmp, ["datasets", "remove", entry.id, "--lock-timeout", "0"], kind="cache", path=cache
    )
    assert rc == 1 and took < FAST
    assert f"dataset cache {cache} is locked by {pid}@" in capsys.readouterr().err
    assert os.path.isdir(_paths.dataset_dir(cache, entry.id))  # nothing removed


def test_cli_rejects_a_negative_lock_timeout(tmp_path):
    for argv in (
        ["datasets", "ingest", "x", "--store", str(tmp_path), "--lock-timeout", "-1"],
        ["store", "migrate-partitions", str(tmp_path), "--lock-timeout", "x"],
        ["lab", "--lock-timeout", "-0.5"],
    ):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2


def test_cli_retention_apply_accepts_lock_timeout(tmp_path, no_env, capsys):
    pf = Portfolio.init(tmp_path / "ws")
    argv = ["retention", "apply", "--workspace", pf.root, "--apply", "--reason", "r", "--yes"]
    rc, took, pid = _store_held_cli(tmp_path, argv + ["--lock-timeout", "0"], path=pf.store_root)
    assert rc == 75 and took < FAST  # EX_TEMPFAIL, as with --wait
    assert f"locked by {pid}@" in capsys.readouterr().err
