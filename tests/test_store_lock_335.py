"""0.112 (#335): who takes a stopped writer's claim over, and no temp file from a lost writer.

1. A writer stopped while it holds the lock keeps its kernel's ``flock``, so every other writer of
   that kernel (another host process; any container of the same Docker Desktop VM) waits for it
   and is refused when its wait runs out, told the holder is alive but stopped. Only a writer on
   another system takes such a claim over (tests/test_store_lock_330.py). On a Docker Desktop
   bind mount the ``flock`` of the descriptor whose open created the file excludes nothing, so
   the lock file is never locked through that open.
2. A writer that lost its claim makes no temp file for a registry, manifest or workspace write,
   and one whose write fails removes it; ``store verify`` lists such a file, and the next ingest
   removes it.

Another system is simulated by a claim naming another kernel, a stopped writer by a SIGSTOPped
child process of this kernel. No Docker is needed.
"""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from camber.cli import main  # noqa: E402
from camber.datasets import _ingest, _paths  # noqa: E402
from camber.portfolio import _lock, _workspace  # noqa: E402
from camber.portfolio._workspace import Portfolio  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore, _normalize  # noqa: E402
from camber.store._leftovers import staging_leftovers  # noqa: E402
from camber.store._lock import StoreLocked, store_lock  # noqa: E402

OTHER = "linux:another-kernel"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(_lock, "CLAIM_BEAT", 0.05)
    monkeypatch.setattr(_lock, "CLAIM_STALE", 0.5)


def _take_over(root, pid=4242):
    """What a writer on another system does once this one's heartbeat stopped."""
    path = os.path.join(root, _lock.CLAIM_FILE)
    os.remove(path)
    rec = {"pid": pid, "host": "box", "kernel": OTHER, "since": "x", "token": "theirs"}
    with open(path, "w") as fh:
        json.dump({**rec, "beat": "000000000001"}, fh)
    return path


def _temp_files(d) -> list:
    return sorted(n for n in os.listdir(d) if ".tmp" in n)


# --------------------------------------------------------------------------- 1. a stopped writer


def test_the_lock_file_is_never_locked_through_the_open_that_created_it(tmp_path, monkeypatch):
    flags: dict = {}
    real = os.open

    def spy(path, flag, *a, **kw):
        fd = real(path, flag, *a, **kw)
        flags[fd] = flag
        return fd

    monkeypatch.setattr(os, "open", spy)
    path = str(tmp_path / "_lock")
    for _ in range(2):  # creating it, then finding it
        with _lock._open(path) as fh:
            assert not flags[fh.fileno()] & os.O_CREAT
    assert os.path.isfile(path)


_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[2])
from camber.portfolio import _lock
from camber.store._lock import store_lock
_lock.CLAIM_BEAT = 0.05
with store_lock(sys.argv[1], timeout=0):
    print("held", flush=True)
    time.sleep(60)
"""


@pytest.fixture
def holder(tmp_path):
    """A writer of this kernel holding the store's lock and claim, in a child process."""
    root = str(tmp_path / "store")
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, root, ROOT], stdout=subprocess.PIPE, text=True
    )
    try:
        assert p.stdout.readline().strip() == "held"
        yield root, p
    finally:
        if os.name != "nt":
            os.kill(p.pid, signal.SIGCONT)
        p.kill()
        p.wait()
        p.stdout.close()


@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP: POSIX")
def test_a_stopped_writer_of_this_kernel_is_waited_for_and_named_as_stopped(holder):
    root, p = holder
    claim = os.path.join(root, _lock.CLAIM_FILE)
    token = json.loads(open(claim).read())["token"]
    os.kill(p.pid, signal.SIGSTOP)
    time.sleep(0.2)
    t0 = time.monotonic()
    with pytest.raises(StoreLocked) as ei:
        with store_lock(root, timeout=1.5):
            pytest.fail("took the lock of a stopped writer of this kernel")
    assert time.monotonic() - t0 >= 1.4  # it waited its full time
    msg = str(ei.value)
    assert f"is locked by {p.pid}@" in msg
    assert "alive but stopped" in msg and "heartbeat has not moved" in msg
    assert "docker unpause" in msg and "waited 1.5 s" in msg
    assert json.loads(open(claim).read())["token"] == token  # its claim stands
    # resumed, it still holds a valid claim and beats again
    os.kill(p.pid, signal.SIGCONT)
    time.sleep(0.3)
    assert json.loads(open(claim).read())["token"] == token


@pytest.mark.skipif(os.name == "nt", reason="a child holding flock: POSIX")
def test_a_running_writer_is_not_called_stopped(holder):
    root, _p = holder
    with pytest.raises(StoreLocked) as ei:
        with store_lock(root, timeout=1.0):
            pytest.fail("took the lock of a running writer")
    assert "stopped" not in str(ei.value) and "waited 1 s" in str(ei.value)


def test_only_a_claim_of_this_kernel_counts_as_a_stopped_lock_holder(tmp_path):
    path = str(tmp_path / _lock.CLAIM_FILE)
    with open(path, "w") as fh:
        json.dump({"pid": 7, "kernel": OTHER, "token": "t", "beat": "1"}, fh)
    w = _lock._Watch(path)
    time.sleep(0.1)
    assert w.frozen() == 0.0  # another system's claim holds no lock here
    with open(path, "w") as fh:
        json.dump({"pid": 7, "kernel": _lock.kernel_id(), "token": "t", "beat": "1"}, fh)
    w.frozen()
    time.sleep(0.1)
    assert w.frozen() >= 0.1
    os.remove(path)  # none at all (a writer of 0.111 or older)
    assert w.frozen() == 0.0


# --------------------------------------------------------------------------- 2. temp files


def test_a_writer_that_lost_its_claim_makes_no_registry_temp_file(tmp_path):
    root = str(tmp_path / "store")
    ParquetStore(root).register_facility("a", name="A")
    before = FacilityRegistry(root).all()
    with pytest.raises(_lock.ClaimLost):
        with store_lock(root, timeout=0):
            _take_over(root)
            FacilityRegistry(root).register("b", name="B")
    assert _temp_files(root) == []
    assert FacilityRegistry(root).all() == before


def test_a_registry_write_that_fails_removes_its_temp_file(tmp_path, monkeypatch):
    root = str(tmp_path / "store")
    ParquetStore(root).register_facility("a", name="A")
    calls = []
    real_confirm = _lock.confirm_claims

    def confirm():  # still the writer before the temp file, no longer before the rename
        calls.append(1)
        if len(calls) > 1:
            raise _lock.ClaimLost("lost")

    with store_lock(root, timeout=0):
        monkeypatch.setattr(_lock, "confirm_claims", confirm)
        with pytest.raises(_lock.ClaimLost):
            FacilityRegistry(root).register("b", name="B")
        assert calls == [1, 1] and _temp_files(root) == []
        monkeypatch.setattr(_lock, "confirm_claims", real_confirm)
        real = os.replace
        monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(OSError, match="disk"):
            FacilityRegistry(root).register("b", name="B")
        monkeypatch.setattr(os, "replace", real)
        assert _temp_files(root) == []
    assert sorted(FacilityRegistry(root).all()) == ["a"]


def test_a_cache_writer_that_lost_its_claim_makes_no_temp_file(tmp_path, monkeypatch):
    cache = str(tmp_path / "cache")
    os.makedirs(cache)
    _paths.write_manifest(cache, {"x": {}})
    with pytest.raises(_lock.ClaimLost):
        with _paths.cache_lock(cache, timeout=0):
            _take_over(cache)
            _paths.write_manifest(cache, {"y": {}})
    assert _temp_files(cache) == []
    assert _paths.read_manifest(cache) == {"x": {}}
    # and one whose claim is found lost just before the rename removes the file it made
    calls = []

    def confirm():
        calls.append(1)
        if len(calls) > 1:
            raise _lock.ClaimLost("lost")

    monkeypatch.setattr(_paths, "_confirm_claims", confirm)
    with pytest.raises(_lock.ClaimLost):
        _paths.write_manifest(cache, {"y": {}})
    assert calls == [1, 1] and _temp_files(cache) == []


def test_a_workspace_writer_that_lost_its_claim_makes_no_temp_file(tmp_path):
    ws = str(tmp_path / "ws")
    Portfolio.init(ws)
    path = os.path.join(ws, "_portfolio.json")
    before = open(path).read()
    with pytest.raises(_lock.ClaimLost):
        with _lock.portfolio_lock(ws):
            _take_over(ws)
            _workspace._write_json(path, {"changed": True})
    assert _temp_files(ws) == [] and open(path).read() == before


def test_store_verify_lists_a_temp_file_and_the_next_ingest_removes_it(tmp_path, capsys):
    root = str(tmp_path / "store")
    ParquetStore(root).register_facility("a", name="A")
    names = ["_facilities.json.tmp", "_facilities.json.tmp-7-0a1b2c3d", "_catalog.gen.x1y2.tmp"]
    for n in names:
        with open(os.path.join(root, n), "w") as fh:
            fh.write("{}")
    os.makedirs(os.path.join(root, "_notes.tmp"))  # a directory is not a temp file
    items = staging_leftovers(root)
    assert sorted(os.path.basename(x["path"]) for x in items) == sorted(names)
    assert all(x["kind"] == "tmp" and x["alive"] is False for x in items)
    rep = _normalize.verify(root)
    assert rep["ok"] and len(rep["leftovers"]) == 3
    assert main(["store", "verify", root]) == 0
    out = capsys.readouterr().out
    assert "leftover _facilities.json.tmp: temporary file of an interrupted registry" in out
    assert "the next ingest into this store removes or recovers these" in out
    assert main(["store", "verify", root, "--strict"]) == 1
    capsys.readouterr()
    # while another writer holds the store, a temp file may be its write in progress
    claim = os.path.join(root, _lock.CLAIM_FILE)
    with open(claim, "w") as fh:
        json.dump({"pid": 7, "kernel": OTHER, "token": "t", "beat": "1"}, fh)
    assert all(x["alive"] for x in staging_leftovers(root))
    assert main(["store", "verify", root]) == 0
    assert "it may be that write in progress" in capsys.readouterr().out
    os.remove(claim)
    with store_lock(root, timeout=0):  # the next ingest's recovery, holding the store
        done = _ingest.recover_staging(ParquetStore(root))
    assert sorted(x["action"] for x in done) == ["tmp_removed"] * 3
    assert _temp_files(root) == ["_notes.tmp"]
    assert staging_leftovers(root) == []


# --------------------------------------------------------------------------- 3. a whole claim


def test_the_claim_appears_whole(tmp_path, monkeypatch):
    """A writer reading the claim while it is made must never find it empty: that would name no
    holder, and the claim would look stale (CI under coverage, test_store_lock_325)."""
    claim = tmp_path / _lock.CLAIM_FILE
    link, linked = os.link, []

    def checked(src, dst):
        assert claim.exists() == bool(linked)  # nothing to read until the record is complete
        rec = json.loads(open(src).read())
        assert rec["pid"] == os.getpid() and rec["kernel"] == _lock.kernel_id()
        link(src, dst)
        linked.append(dst)

    monkeypatch.setattr(_lock.os, "link", checked)
    c = _lock._try_claim(str(tmp_path))
    try:
        assert linked == [str(claim)]  # the only way the claim appeared
        assert os.listdir(tmp_path) == [_lock.CLAIM_FILE]  # its temporary name is gone
        assert json.loads(claim.read_text())["token"] == c.info["token"]
        c.n += 1  # a heartbeat writes the same length over the record, through the same file
        c._write()
        assert json.loads(claim.read_text())["beat"] == "000000000001"
        assert _lock._try_claim(str(tmp_path)) is None  # the link is the exclusive create
        assert os.listdir(tmp_path) == [_lock.CLAIM_FILE]
    finally:
        assert c.release()
    assert os.listdir(tmp_path) == []


def test_without_hard_links_the_claim_is_still_exclusive(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(_lock.os, "link", no_links)
    c = _lock._try_claim(str(tmp_path))
    try:
        assert _lock._try_claim(str(tmp_path)) is None
        assert os.listdir(tmp_path) == [_lock.CLAIM_FILE]
        assert json.loads((tmp_path / _lock.CLAIM_FILE).read_text())["token"] == c.info["token"]
    finally:
        assert c.release()


def test_a_claim_read_mid_write_still_names_its_holder(tmp_path):
    """A holder whose claim is read empty at the deadline is named from its last whole record,
    and an empty claim is taken over only once it has stayed so for the stale time."""
    path = str(tmp_path / _lock.CLAIM_FILE)
    rec = {"pid": 4242, "host": "box", "kernel": OTHER, "since": "x", "token": "t", "beat": "1"}
    with open(path, "w") as fh:
        json.dump(rec, fh)
    reads = iter(range(10**6))
    real = _lock._read_claim

    def flaky(p):  # after the first, every read finds it mid-write (a truncating writer)
        raw, got = real(p)
        if raw is not None and next(reads):
            return b"", None
        return raw, got

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_lock, "_read_claim", flaky)
        with pytest.raises(StoreLocked) as ei:
            with store_lock(str(tmp_path), timeout=0.3):
                pytest.fail("took a live claim over")
    assert "4242@box" in str(ei.value) and OTHER in str(ei.value)
    assert json.loads(open(path).read())["token"] == "t"
    # an empty claim of a writer before #335 that never gets written is taken over in time
    open(path, "w").close()
    with store_lock(str(tmp_path), timeout=2.0):
        assert json.loads(open(path).read())["pid"] == os.getpid()
