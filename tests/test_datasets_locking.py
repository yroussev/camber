"""Locks on plain stores and dataset caches, and a read-only shared cache (0.103, #124), plus the
CSV reader's mixed-type columns (#126). All offline, on the synthetic ``test-ahu`` entry.
"""

import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import threading
import time
import warnings
import zipfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

from camber.datasets import _ingest, _ops, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.portfolio import PortfolioLocked  # noqa: E402
from camber.portfolio._lock import LOCK_FILE, exclusive_lock, read_holder  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store._lock import StoreLocked, store_lock  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions / signals")
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)


@pytest.fixture
def ahu(tmp_path, monkeypatch):
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    opener = FakeOpener({BASE + "test.zip": zb})
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return entry, opener, tmp_path


def _hold(kind: str, path: str, seconds: float = 30.0):
    """Start a subprocess that takes ``kind`` ("store" / "cache") lock on ``path`` and sleeps;
    returns it once the lock is held."""
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


def _frames(store: str) -> pd.DataFrame:
    """Every row of the test facility, in a stable order (row order within a part may vary)."""
    import pyarrow.parquet as pq

    part = os.path.join(store, "facility_id=ds-test-ahu")
    files = sorted(
        os.path.join(d, f) for d, _, fs in os.walk(part) for f in fs if f.endswith(".parquet")
    )
    assert files
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    return df.sort_values(list(df.columns)).reset_index(drop=True)


# --------------------------------------------------------------------------- the lock itself


def test_a_second_process_waits_then_is_refused_with_the_holder(tmp_path):
    root = str(tmp_path / "store")
    proc = _hold("store", root)
    try:
        t0 = time.monotonic()
        with pytest.raises(StoreLocked) as exc:
            with store_lock(root, timeout=0.5):
                pass
        assert time.monotonic() - t0 >= 0.45  # it waited for the timeout first
        msg = str(exc.value)
        assert f"store {root} is locked by {proc.pid}@" in msg and "retry" in msg
    finally:
        _stop(proc)
    with store_lock(root, timeout=0):  # released when the holder exits
        assert read_holder(root)["pid"] == os.getpid()


def test_a_waiting_writer_gets_the_lock_when_the_holder_finishes(tmp_path):
    root = str(tmp_path / "cache")
    proc = _hold("cache", root, seconds=0.5)
    try:
        with _paths.cache_lock(root, timeout=20):
            assert read_holder(root)["pid"] == os.getpid()
    finally:
        _stop(proc)


@posix_only
def test_a_killed_holder_leaves_no_stale_lock(tmp_path):
    root = str(tmp_path / "store")
    proc = _hold("store", root)
    assert read_holder(root)["pid"] == proc.pid
    os.kill(proc.pid, signal.SIGKILL)  # no cleanup runs: the holder line stays behind
    _stop(proc)
    assert read_holder(root)["pid"] == proc.pid
    with store_lock(root, timeout=0):  # the kernel dropped the lock with the process
        assert read_holder(root)["pid"] == os.getpid()


def test_stale_holder_text_from_a_dead_pid_is_overwritten(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / LOCK_FILE).write_bytes(b"\n" + json.dumps({"pid": 999999, "host": "x"}).encode())
    with _paths.cache_lock(str(root), timeout=0):
        assert read_holder(str(root))["pid"] == os.getpid()


def test_lock_is_reentrant_and_other_threads_wait(tmp_path):
    root = str(tmp_path / "d")
    os.makedirs(root)
    order = []
    with exclusive_lock(root, timeout=0):
        with exclusive_lock(root, timeout=0):  # re-entrant in this thread
            pass

        def other():
            with exclusive_lock(root, timeout=10):
                order.append("other")

        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.2)
        order.append("main")
    t.join(10)
    assert order == ["main", "other"]


# --------------------------------------------------------------------------- ingest and fetch


def test_ingest_into_a_plain_store_is_refused_while_another_process_writes_it(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = str(tmp / "store")
    proc = _hold("store", store)
    try:
        with pytest.raises(StoreLocked, match=f"locked by {proc.pid}@"):
            _ingest.ingest_dataset(entry, store, lock_timeout=0.2)
    finally:
        _stop(proc)
    assert ParquetStore(store).facilities() == []  # nothing was written
    res = _ingest.ingest_dataset(entry, store)
    assert res.facilities == ["ds-test-ahu"]
    assert os.path.isfile(os.path.join(store, LOCK_FILE))


def test_two_concurrent_ingests_into_one_store_serialize(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    ref = str(tmp / "ref")
    _ingest.ingest_dataset(entry, ref)
    store = str(tmp / "store")
    errors, results = [], []

    def run():
        try:
            results.append(_ingest.ingest_dataset(entry, store, force=True))
        except Exception as exc:  # pragma: no cover - the failure is the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == [] and len(results) == 2
    assert results[0].rows == results[1].rows
    pd.testing.assert_frame_equal(_frames(store), _frames(ref))
    assert not os.listdir(os.path.join(store, _ingest._STAGING))  # no staging left behind


def test_fetch_waits_for_the_cache_lock_then_names_the_holder(ahu, monkeypatch):
    entry, opener, tmp = ahu
    cache = str(tmp / "cache")
    monkeypatch.setattr(_paths, "CACHE_LOCK_TIMEOUT", 0.2)
    proc = _hold("cache", cache)
    try:
        with pytest.raises(_paths.CacheLocked, match=f"dataset cache .* locked by {proc.pid}@"):
            _ops.fetch_dataset(entry, opener=opener)
    finally:
        _stop(proc)
    assert opener.calls == []  # nothing downloaded while another process held the cache
    assert _ops.fetch_dataset(entry, opener=opener).downloaded_bytes > 0


def test_a_workspace_store_uses_the_workspace_lock_without_deadlock(ahu):
    from camber.portfolio import Portfolio

    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    pf = Portfolio.init(tmp / "ws")
    with pf.lock(timeout=0):  # e.g. the lab holds it around its ingest: re-entered, no deadlock
        res = _ingest.ingest_dataset(entry, pf.store_root)
    assert res.facilities == ["ds-test-ahu"]
    assert not os.path.exists(os.path.join(pf.store_root, LOCK_FILE))  # no second lock
    proc = _hold("store", pf.store_root)  # store_lock on a workspace store = the workspace lock
    try:
        assert read_holder(str(tmp / "ws"))["pid"] == proc.pid
        with pytest.raises(PortfolioLocked):
            _ingest.ingest_dataset(entry, pf.store_root, force=True, lock_timeout=0.2)
    finally:
        _stop(proc)


# --------------------------------------------------------------------------- read-only cache


def _tree(root: str) -> dict:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(dirpath, n)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def _chmod_tree(root: str, writable: bool) -> None:
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            p = os.path.join(dirpath, n)
            mode = os.stat(p).st_mode
            os.chmod(p, (mode | stat.S_IWUSR) if writable else (mode & ~0o222))
        mode = os.stat(dirpath).st_mode
        os.chmod(dirpath, (mode | stat.S_IWUSR) if writable else (mode & ~0o222))


@pytest.fixture
def readonly(ahu):
    """A fetched cache (``extracted/`` removed, so an ingest must extract), made read-only."""
    entry, opener, tmp = ahu
    cache = str(tmp / "cache")
    _ops.fetch_dataset(entry, opener=opener)
    ref = str(tmp / "ref")
    _ingest.ingest_dataset(entry, ref)  # the reference ingest, from the writable cache
    import shutil

    shutil.rmtree(_paths.extracted_dir(cache, entry.id))
    _chmod_tree(cache, writable=False)
    yield entry, opener, tmp, cache, ref
    _chmod_tree(cache, writable=True)


@posix_only
@not_root
def test_fetch_of_verified_files_in_a_read_only_cache_writes_nothing(readonly):
    entry, _opener, _tmp, cache, _ref = readonly
    before = _tree(cache)
    offline = FakeOpener({})
    res = _ops.fetch_dataset(entry, opener=offline)
    assert offline.calls == [] and res.downloaded_bytes == 0
    assert [f["skipped"] for f in res.files] == [True]
    assert res.files[0]["sha256"] == entry.files[0]["sha256"]
    assert _tree(cache) == before  # no manifest rewrite, no lock file, nothing


def test_fetch_of_verified_files_in_a_writable_cache_does_not_rewrite_the_manifest(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    man = os.path.join(str(tmp / "cache"), _paths.MANIFEST)
    before = os.stat(man).st_mtime_ns, open(man, "rb").read()
    time.sleep(0.01)
    res = _ops.fetch_dataset(entry, opener=FakeOpener({}))
    assert res.files[0]["skipped"]
    assert (os.stat(man).st_mtime_ns, open(man, "rb").read()) == before


@posix_only
@not_root
def test_a_read_only_cache_refuses_what_needs_a_write(readonly):
    entry, _opener, tmp, cache, _ref = readonly
    d = entry.as_dict()
    d["id"] = "test-other"
    d["ingest"]["facility"] = "ds-test-other"
    other = DatasetEntry.from_dict(d)
    with pytest.raises(_paths.CacheReadOnly, match="read-only for this user"):
        _ops.fetch_dataset(other, opener=FakeOpener({}))
    with pytest.raises(_paths.CacheReadOnly):
        _ops.remove_dataset(entry)
    src = tmp / "src"
    src.mkdir()
    (src / "test.zip").write_bytes(_zip_bytes())
    with pytest.raises(_paths.CacheReadOnly):
        _ops.adopt_local_files(entry, str(src))


@posix_only
@not_root
def test_ingest_from_a_read_only_cache_extracts_into_store_scratch(readonly):
    entry, _opener, tmp, cache, ref = readonly
    before = _tree(cache)
    store = str(tmp / "store")
    res = _ingest.ingest_dataset(entry, store)
    assert res.facilities == ["ds-test-ahu"] and not res.skipped
    assert _tree(cache) == before  # the cache was only read
    pd.testing.assert_frame_equal(_frames(store), _frames(ref))  # same data as the writable path
    staging = os.path.join(store, _ingest._STAGING)
    assert os.listdir(staging) == []  # the scratch extraction was removed


@posix_only
@not_root
def test_ingest_uses_members_already_extracted_in_a_read_only_cache(ahu):
    entry, opener, tmp = ahu
    cache = str(tmp / "cache")
    _ops.fetch_dataset(entry, opener=opener)
    _ingest.ingest_dataset(entry, str(tmp / "ref"))  # leaves the members in the cache
    _chmod_tree(cache, writable=False)
    try:
        before = _tree(cache)
        res = _ingest.ingest_dataset(entry, str(tmp / "store"))
        assert res.facilities == ["ds-test-ahu"] and _tree(cache) == before
    finally:
        _chmod_tree(cache, writable=True)
    pd.testing.assert_frame_equal(_frames(str(tmp / "store")), _frames(str(tmp / "ref")))


@posix_only
@not_root
def test_research_only_acknowledgements_against_a_read_only_cache_are_per_user(ahu):
    entry, opener, tmp = ahu
    d = entry.as_dict()
    d.update(licence="CC-BY-NC-4.0", access="research_only")
    ro = DatasetEntry.from_dict(d)
    cache = str(tmp / "cache")
    _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)  # whoever filled the cache
    _chmod_tree(cache, writable=False)
    try:
        from camber.datasets import _licence

        # the filler's acknowledgement in the read-only cache does not stand in for this user's
        assert _licence.acknowledged(cache, ro) is None
        with pytest.raises(PermissionError, match="ingest it"):
            _ingest.ingest_dataset(ro, str(tmp / "store"))
        before = _tree(cache)
        _ops.fetch_dataset(ro, opener=FakeOpener({}), accept_noncommercial=True)
        res = _ingest.ingest_dataset(ro, str(tmp / "store"))  # this user's own ack now counts
        assert res.facilities == ["ds-test-ahu"] and _tree(cache) == before
    finally:
        _chmod_tree(cache, writable=True)
    ledger = _paths.read_acknowledgements(_paths.user_state_dir())
    assert _paths.user_state_dir() == str(tmp / "state" / "camber" / "datasets")
    assert [(r["dataset_id"], r["via"], r["cache"]) for r in ledger] == [
        ("test-ahu", "fetch", os.path.realpath(cache))
    ]
    meta = ParquetStore(str(tmp / "store")).facilities_meta()["ds-test-ahu"]["dataset"]
    assert meta["acknowledgement"] == ledger[0]["accepted_at"]
    assert meta["acknowledged_licence"] == "CC-BY-NC-4.0"
    assert len(_paths.read_acknowledgements(cache)) == 1  # the cache's own ledger is untouched


# --------------------------------------------------------------------------- #126 mixed types


def _units_row_zip() -> bytes:
    """``_zip_bytes`` with a units row under each header, as ornl-frp-ops publishes them: every
    value column then holds one string above thousands of numbers."""
    src = zipfile.ZipFile(io.BytesIO(_zip_bytes()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            header, rest = src.read(info).split(b"\n", 1)
            # no unit under the clock and the masked placeholder column (as in the real file,
            # whose units row leaves the timestamp empty)
            units = b",".join(
                b"" if c in (b"Datetime", b"SA_SPSPT") else b"Deg F" for c in header.split(b",")
            )
            z.writestr(info, header + b"\n" + units + b"\n" + rest)
    return buf.getvalue()


def test_mixed_type_columns_ingest_without_a_dtype_warning(tmp_path, monkeypatch):
    import pandas._libs.parsers as parsers

    # pandas types a column per ~1 MiB chunk; shrink the chunk so a small fixture spans many
    if hasattr(parsers, "DEFAULT_BUFFER_HEURISTIC"):
        monkeypatch.setattr(parsers, "DEFAULT_BUFFER_HEURISTIC", 2**12)
    zb = _units_row_zip()
    member = zipfile.ZipFile(io.BytesIO(zb)).read("TEST/AHU_ff.csv")
    with warnings.catch_warnings(record=True) as seen:  # the fixture does provoke the warning
        warnings.simplefilter("always")
        pd.read_csv(io.BytesIO(member))
    if hasattr(parsers, "DEFAULT_BUFFER_HEURISTIC"):
        assert any(issubclass(w.category, pd.errors.DtypeWarning) for w in seen)

    d = _ahu_entry_dict(zb)
    assert d["files"][0]["sha256"] == hashlib.sha256(zb).hexdigest()
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _ops.fetch_dataset(entry, opener=FakeOpener({BASE + "test.zip": zb}))
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        res = _ingest.ingest_dataset(entry, str(tmp_path / "store"))
    assert [w for w in seen if issubclass(w.category, pd.errors.DtypeWarning)] == []
    assert res.rows > 0

    # the values are those of the same data without the units row
    plain = DatasetEntry.from_dict(_ahu_entry_dict(_zip_bytes()))
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache2"))
    _ops.fetch_dataset(plain, opener=FakeOpener({BASE + "test.zip": _zip_bytes()}))
    _ingest.ingest_dataset(plain, str(tmp_path / "plain"))
    pd.testing.assert_frame_equal(
        _frames(str(tmp_path / "store")), _frames(str(tmp_path / "plain"))
    )


# --------------------------------------------------------------------------- the lab, unchanged


@posix_only
@not_root
def test_the_lab_fetches_and_ingests_from_a_read_only_cache(tmp_path, monkeypatch):
    """The lab gets the read-only cache for free: its jobs call the same fetch and ingest."""
    from test_lab import _app, _wait, get, post

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ack = {"test-nc": "test-nc"}
    app = _app(tmp_path)
    try:  # whoever fills the shared cache fetches both datasets into it
        body = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu", "test-nc"], "acknowledge": ack})[1]
        assert _wait(app, body["job"]["id"])["state"] == "done"
    finally:
        app.close()
    cache = str(tmp_path / "cache")
    _chmod_tree(cache, writable=False)
    app = _app(tmp_path)
    try:
        before = _tree(cache)
        rows = {d["id"]: d for d in get(app, "/lab/catalog")[1]["datasets"]}
        assert rows["test-nc"]["acknowledged"] is False  # the filler's acceptance is not ours
        ids = {"ids": ["test-ahu", "test-nc"], "ingest": True, "acknowledge": ack}
        view = _wait(app, post(app, "/lab/jobs/fetch", ids)[1]["job"]["id"])
        assert view["state"] == "done", view["error"]
        assert [r["fetch"]["downloaded_bytes"] for r in view["result"]] == [0, 0]
        assert {f for r in view["result"] for f in r["ingest"]["facilities"]} == {
            "ds-test-ahu",
            "ds-test-nc",
        }
        rows = {d["id"]: d for d in get(app, "/lab/catalog")[1]["datasets"]}
        assert rows["test-nc"]["acknowledged"] is True
        assert _tree(cache) == before  # the lab only read the cache
    finally:
        app.close()
        _chmod_tree(cache, writable=True)
    ledger = _paths.read_acknowledgements(_paths.user_state_dir())
    assert [(r["dataset_id"], r["via"]) for r in ledger] == [("test-nc", "lab fetch")]
