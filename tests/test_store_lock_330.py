"""0.112 (#330): a writer that lost its lock claim stops; liveness that holds across kernels.

1. A writer stopped for longer than the claim's stale time (Ctrl-Z, a debugger) has its claim
   taken over by a writer on another system. When it resumes, it must not carry on: every step
   that changes the store checks the claim first, and the writer stops with the lock's error
   (also a ``ClaimLost``), leaving the store as the new holder has it.
2. ``flock`` excludes nothing on a Docker Desktop bind mount, so neither a claim of this kernel
   nor a matplotlib directory is judged dead by the lock alone: a process of this kernel and pid
   namespace is checked by pid and start time, and one of another kernel or container by its
   heartbeat.
3. A killed pid-1 ingest's ``_staging/ingest-1-*`` is removed by the next ingest, and ``store
   verify`` says so: pid 1 is alive everywhere, so a staging directory records its writer.

Another system is simulated by a record naming another kernel; a broken ``flock`` by patching
the lock or the probe. No Docker is needed.
"""

import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

import camber.datasets as ds  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.portfolio import _lock  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore, _normalize, _swap  # noqa: E402
from camber.store._fence import _started  # noqa: E402
from camber.store._leftovers import OWNER_FILE, staging_leftovers  # noqa: E402
from camber.store._lock import StoreLocked, store_lock  # noqa: E402

FID = "ds-test-ahu"
OTHER = "linux:another-kernel"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(_lock, "CLAIM_BEAT", 0.05)
    monkeypatch.setattr(_lock, "CLAIM_STALE", 0.5)


def _take_over(root, pid=4242):
    """What a writer on another system does once this one's heartbeat stopped: the claim is
    moved aside and removed, and its own claim made in its place."""
    path = os.path.join(root, _lock.CLAIM_FILE)
    aside = path + ".stale-test"
    os.rename(path, aside)
    os.remove(aside)
    rec = {"pid": pid, "host": "box", "kernel": OTHER, "since": "x", "token": "theirs"}
    with open(path, "w") as fh:
        json.dump({**rec, "beat": "000000000001"}, fh)
    return path


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _here(pid) -> dict:
    """An identity record of process ``pid`` of this kernel and pid namespace."""
    return {
        "pid": pid,
        "kernel": _lock.kernel_id(),
        "pidns": _lock.pid_namespace(),
        "started": _started(pid),
    }


@pytest.fixture
def sleeper():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p
    p.kill()
    p.wait()


# --------------------------------------------------------------------------- 1. a lost claim


def test_a_writer_whose_claim_was_taken_over_stops_before_the_next_change(tmp_path):
    root = str(tmp_path / "store")
    st = ParquetStore(root)
    st.register_facility("a", name="A")
    before = FacilityRegistry(root).all()
    with pytest.raises(StoreLocked) as ei:
        with store_lock(root, timeout=0):
            claim = _take_over(root)
            FacilityRegistry(root).register("b", name="B")  # the registry write is refused
    assert isinstance(ei.value, _lock.ClaimLost)
    msg = str(ei.value)
    assert "lost its claim" in msg and "4242@box" in msg and OTHER in msg
    assert "run the command again" in msg
    assert FacilityRegistry(root).all() == before
    # the new holder's claim is left as it is
    assert json.loads(open(claim).read())["token"] == "theirs"


def test_a_partition_swap_is_refused_once_the_claim_is_lost(tmp_path):
    root = str(tmp_path / "store")
    target = os.path.join(root, "facility_id=x")
    os.makedirs(target)
    open(os.path.join(target, "old.parquet"), "w").close()
    with pytest.raises(_lock.ClaimLost):
        with store_lock(root, timeout=0):
            new = _swap.staging(target)
            open(os.path.join(new, "new.parquet"), "w").close()
            _take_over(root)
            _swap.commit(target)
    assert os.listdir(target) == ["old.parquet"]


def test_a_lost_claim_found_at_release_fails_the_command(tmp_path):
    """A writer stopped between its last check and its last change: it cannot know whether
    that change overlapped the new holder's, so it must not report success."""
    root = str(tmp_path / "store")
    with pytest.raises(StoreLocked) as ei:
        with store_lock(root, timeout=0):
            _take_over(root)
    assert isinstance(ei.value, _lock.ClaimLost)
    assert "may have overlapped" in str(ei.value) and "store verify" in str(ei.value)
    assert os.path.exists(os.path.join(root, _lock.CLAIM_FILE))  # theirs: not removed


def test_an_error_caused_by_the_takeover_is_reported_as_the_lost_claim(tmp_path):
    root = str(tmp_path / "store")
    with pytest.raises(_lock.ClaimLost) as ei:
        with store_lock(root, timeout=0):
            _take_over(root)
            raise FileNotFoundError("its staging directory, removed by the new holder")
    assert isinstance(ei.value.__cause__, FileNotFoundError)


def test_a_writer_that_was_stopped_beats_before_it_checks(tmp_path):
    root = str(tmp_path / "store")
    with store_lock(root, timeout=0):
        slot = next(s for s in _lock._HELD.values() if s.claim is not None)
        claim = slot.claim
        claim.stop.set()  # as if the heartbeat thread had not run for a while
        claim.thread.join()
        claim.last -= 60
        n = claim.n
        _lock.confirm_claims()
        assert claim.n == n + 1  # a waiter about to take it over now sees it move
        rec = json.loads(open(os.path.join(root, _lock.CLAIM_FILE)).read())
        assert rec["beat"] == f"{n + 1:012d}" and rec["token"] == claim.info["token"]


def test_confirm_claims_is_a_no_op_without_a_lock():
    _lock.confirm_claims()


@pytest.fixture
def cat(tmp_path, monkeypatch):
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    opener = FakeOpener({BASE + "test.zip": zb})
    cache = str(tmp_path / "cache")
    monkeypatch.setenv("CAMBER_DATA_DIR", cache)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    _ops.fetch_dataset(entry, data_dir=cache, opener=opener)
    return entry, cache, tmp_path


def _rows(store) -> int:
    return sum(p["rows"] for p in ParquetStore(store).partitions(facility_id=FID))


@pytest.mark.parametrize("remove_staging", [False, True], ids=["resumes", "staging-removed"])
def test_a_resumed_ingest_stops_and_leaves_the_store_as_the_new_holder_has_it(
    cat, monkeypatch, remove_staging
):
    """The UAT case: an ingest stopped mid-way (Ctrl-Z) has its claim taken over by a writer in
    a container, which ingests and finishes; the resumed ingest must not swap its partitions in."""
    entry, cache, tmp = cat
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    n, reg = _rows(store), FacilityRegistry(store).all()
    adapter = entry.ingest["adapter"]
    real = _ingest._ADAPTERS[adapter]

    def stopped_meanwhile(entry_, sname, inputs, root, staging, *a):
        out = real(entry_, sname, inputs, root, staging, *a)
        _take_over(store)  # the other side took the claim while this writer was stopped
        if remove_staging:  # and its next ingest removed this one's staging as abandoned
            import shutil

            shutil.rmtree(staging)
        return out

    monkeypatch.setitem(_ingest._ADAPTERS, adapter, stopped_meanwhile)
    with pytest.raises(StoreLocked) as ei:
        _ingest.ingest_dataset(entry, store, data_dir=cache, force=True)
    assert isinstance(ei.value, _lock.ClaimLost) and "lost its claim" in str(ei.value)
    assert _rows(store) == n and FacilityRegistry(store).all() == reg
    assert json.loads(open(os.path.join(store, _lock.CLAIM_FILE)).read())["token"] == "theirs"


def test_the_cli_exits_1_with_the_lost_claim_message(cat, monkeypatch, capsys):
    entry, cache, tmp = cat
    store = str(tmp / "store")
    adapter = entry.ingest["adapter"]
    real = _ingest._ADAPTERS[adapter]

    def stopped_meanwhile(*a):
        out = real(*a)
        _take_over(store)
        return out

    monkeypatch.setitem(_ingest._ADAPTERS, adapter, stopped_meanwhile)
    rc = main(["datasets", "ingest", entry.id, "--dir", cache, "--store", store])
    assert rc == 1
    err = capsys.readouterr().err
    assert "lost its claim" in err and "Traceback" not in err
    assert ParquetStore(store).facilities() == []


@pytest.mark.skipif(os.name == "nt", reason="pid namespaces and start times: POSIX")
def test_a_live_claim_of_this_kernel_is_not_taken_over_when_flock_excludes_nothing(
    tmp_path, monkeypatch, sleeper
):
    """Two processes of one container on a Docker Desktop bind mount both get the flock; the
    second must still wait for the first's claim, not take it over as dead."""
    root = str(tmp_path)
    path = os.path.join(root, _lock.CLAIM_FILE)
    rec = {**_here(sleeper.pid), "host": "here", "since": "x", "token": "t"}
    with open(path, "w") as fh:
        json.dump(rec, fh)
    with pytest.raises(StoreLocked) as ei:
        with store_lock(root, timeout=0.3):
            # seen once on a CI runner, not reproduced: say why the claim looked gone
            pytest.fail(
                "took over the claim of a live process: "
                f"poll={sleeper.poll()} state={_lock.process_state(rec)} "
                f"started={rec['started']!r} now={_started(sleeper.pid)!r}"
            )
    assert "on this system" in str(ei.value) and "does not enforce" in str(ei.value)
    assert json.loads(open(path).read())["token"] == "t"
    # once that process has exited, the claim is taken over at once
    sleeper.kill()
    sleeper.wait()
    with store_lock(root, timeout=0):
        pass


@pytest.mark.skipif(os.name == "nt", reason="pid namespaces and start times: POSIX")
def test_process_state_tells_what_it_can_and_no_more(sleeper):
    assert _lock.process_state(_here(os.getpid())) is True
    assert _lock.process_state(_here(sleeper.pid)) is True
    assert _lock.process_state({**_here(sleeper.pid), "started": "1.000000"}) is False  # reused
    assert _lock.process_state({**_here(os.getpid()), "pid": _dead_pid()}) is False
    assert _lock.process_state({**_here(1), "started": "1.000000"}) is False  # another pid 1
    assert _lock.process_state({**_here(os.getpid()), "kernel": OTHER}) is None
    assert _lock.process_state({**_here(os.getpid()), "pidns": "pid:[1]"}) is None
    assert _lock.process_state({"pid": os.getpid()}) is None
    assert _lock.process_state(None) is None


# --------------------------------------------------------------------------- 2. the mpl sweep


def _mpl_dir(tmp_path, rec, tag, age=0.0):
    from camber.mcp import _launch

    d = tmp_path / f"{_launch._MPL_PREFIX}1-{tag}"
    d.mkdir()
    owner = d / _launch._MPL_OWNER
    owner.write_text(json.dumps(rec), encoding="utf-8")
    if age:
        os.utime(owner, (time.time() - age,) * 2)
    return d


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
@pytest.mark.parametrize("flock_works", [True, False], ids=["volume", "bind-mount"])
def test_a_live_server_in_another_container_keeps_its_directory(tmp_path, monkeypatch, flock_works):
    """U112-LINUX-2: on a bind-mounted /tmp the lock proves nothing; the heartbeat does."""
    from camber.mcp import _launch

    monkeypatch.setattr(_launch, "_flock_works", lambda d: flock_works)
    rec = {"pid": 7, "kernel": _lock.kernel_id(), "pidns": "pid:[4026532999]", "started": "1"}
    live = _mpl_dir(tmp_path, rec, "live01")
    dead = _mpl_dir(tmp_path, rec, "dead01", age=10 * _launch._MPL_STALE)
    removed = _launch.sweep_matplotlib_dirs(str(tmp_path))
    if flock_works:  # LINUX-7: an unheld lock on a filesystem whose locks work means gone
        assert removed == 2 and not live.exists() and not dead.exists()
    else:  # its heartbeat is fresh: alive; the other's stopped long ago: dead
        assert removed == 1 and live.exists() and not dead.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_a_dead_pid_1_servers_directory_is_swept_and_a_live_ones_kept(tmp_path, sleeper):
    """Servers of this kernel and pid namespace are judged by pid and start time, whatever the
    lock says: pid 1 that started at another time is not that server."""
    from camber.mcp import _launch

    dead1 = _mpl_dir(tmp_path, {**_here(1), "started": "1.000000"}, "pid1")
    gone = _mpl_dir(tmp_path, {**_here(os.getpid()), "pid": _dead_pid()}, "gone")
    live = _mpl_dir(tmp_path, _here(sleeper.pid), "live")  # no lock held: the record decides
    assert _launch.sweep_matplotlib_dirs(str(tmp_path)) == 2
    assert live.exists() and not dead1.exists() and not gone.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_the_owner_file_appears_whole_and_locked(tmp_path, monkeypatch):
    """A sweep that reads the owner file while the server writes it must never see it empty or
    unlocked: on a local disk that reads as a dead server's directory."""
    from camber.mcp import _launch

    owner = tmp_path / _launch._MPL_OWNER
    replace, renamed = os.replace, []

    def checked(src, dst):
        assert not owner.exists()  # nothing to read until the record is complete
        assert json.loads(open(src).read())["pid"] == os.getpid()
        assert _launch._owner_gone(str(tmp_path)) is None  # no owner file yet: not "gone"
        replace(src, dst)
        renamed.append(dst)

    monkeypatch.setattr(_launch.os, "replace", checked)
    _launch._lock_owner(str(tmp_path))
    try:
        assert renamed == [str(owner)]
        assert os.listdir(tmp_path) == [_launch._MPL_OWNER]
        assert json.loads(owner.read_text())["pid"] == os.getpid()
        assert _launch._owner_gone(str(tmp_path)) is False  # this process runs and holds it
    finally:
        while _launch._MPL_LOCKS:
            os.close(_launch._MPL_LOCKS.pop())


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_the_flock_probe_and_the_servers_heartbeat(tmp_path, monkeypatch):
    import tempfile

    from camber.mcp import _launch

    assert _launch._flock_works(str(tmp_path)) is True  # a local disk
    assert os.listdir(tmp_path) == []  # the probe leaves nothing
    monkeypatch.setattr(_launch, "_MPL_BEAT", 0.05)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    path = _launch.isolate_matplotlib()
    owner = os.path.join(path, _launch._MPL_OWNER)
    try:
        assert json.loads(open(owner).read())["pid"] == os.getpid()
        os.utime(owner, (time.time() - 1000,) * 2)
        deadline = time.time() + 10
        while os.stat(owner).st_mtime < time.time() - 500 and time.time() < deadline:
            time.sleep(0.05)
        assert os.stat(owner).st_mtime > time.time() - 500  # touched again
        os.remove(owner)  # a sweep removed it; the server puts its record back
        while not os.path.exists(owner) and time.time() < deadline:
            time.sleep(0.05)
        assert json.loads(open(owner).read())["pid"] == os.getpid()
    finally:
        while _launch._MPL_BEATS:
            stop, beat = _launch._MPL_BEATS.pop()
            stop.set()
            beat.join()
        while _launch._MPL_LOCKS:
            os.close(_launch._MPL_LOCKS.pop())


# --------------------------------------------------------------------------- 3. staging


def _scratch(store, name, rec=None):
    d = os.path.join(store, "_staging", name)
    os.makedirs(os.path.join(d, "facility_id=x"))
    if rec is not None:
        with open(os.path.join(d, OWNER_FILE), "w") as fh:
            json.dump(rec, fh)
    return d


def test_an_ingest_records_who_made_its_staging_directory(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    path = _ingest._staging_root(st)
    rec = json.loads(open(os.path.join(path, OWNER_FILE)).read())
    assert rec == _lock.process_identity()
    (item,) = staging_leftovers(st.root)
    assert item["alive"] is True and item["elsewhere"] is False


@pytest.mark.skipif(os.name == "nt", reason="pid namespaces and start times: POSIX")
def test_the_next_ingest_removes_a_killed_pid_1_ingests_staging(cat, capsys, sleeper):
    """U112-LINUX-3: ``ingest-1-*`` of a killed pid-1 container ingest (0.111: no record), and
    the same with a record of another container; a live writer's is left alone."""
    entry, cache, tmp = cat
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, data_dir=cache)
    legacy = _scratch(store, "ingest-1-ffff9efc23b0")
    other = _scratch(
        store, "ingest-1-aaaa", {"pid": 1, "kernel": OTHER, "pidns": "", "started": "9"}
    )
    here_dead = _scratch(store, "ingest-1-bbbb", {**_here(1), "started": "1.000000"})
    live = _scratch(store, f"ingest-{sleeper.pid}-cccc", _here(sleeper.pid))

    assert main(["store", "verify", store]) == 0
    out = capsys.readouterr().out
    for d in (legacy, other, here_dead):
        line = next(x for x in out.splitlines() if os.path.basename(d) in x)
        assert "still running" not in line and "claim" not in line
    assert "(its process is still running)" in next(
        x for x in out.splitlines() if os.path.basename(live) in x
    )
    assert "the next ingest into this store removes or recovers these" in out

    res = _ingest.ingest_dataset(entry, store, data_dir=cache)
    assert res.skipped
    assert sorted(os.listdir(os.path.join(store, "_staging"))) == [os.path.basename(live)]


def test_a_staging_directory_of_a_writer_on_another_system_that_holds_the_store(tmp_path, capsys):
    store = str(tmp_path / "store")
    ParquetStore(store).register_facility("a", name="A")
    rec = {"pid": 7, "kernel": OTHER, "pidns": "pid:[4026532999]", "started": "123"}
    d = _scratch(store, "ingest-7-abcd", rec)
    claim = os.path.join(store, _lock.CLAIM_FILE)
    with open(claim, "w") as fh:
        json.dump({**rec, "host": "ctr", "since": "x", "token": "t", "beat": "1"}, fh)
    (item,) = staging_leftovers(store)
    assert item["alive"] is True and item["elsewhere"] is True
    assert main(["store", "verify", store]) == 0
    out = capsys.readouterr().out
    assert "(made on another system by the writer that holds the store's claim now)" in out
    assert "once that claim is let go, or taken over" in out
    os.remove(claim)  # that writer let go (or was killed and its claim taken over)
    (item,) = staging_leftovers(store)
    assert item["alive"] is False
    rep = _normalize.verify(store)
    assert rep["leftovers"][0]["path"] == os.path.relpath(d, store)
