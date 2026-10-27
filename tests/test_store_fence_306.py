"""#306: a forced re-ingest (and every other writer that replaces or removes a facility's files in
place) is atomic for readers. A reader caught mid-swap gets a retryable ``StoreReadError`` (the
capability error ``unavailable``), never an answer from a partial or absent facility.

The deterministic tests stop the writer between its two renames (the facility is absent) or run
a whole swap inside a read; the stress test runs forced swaps against reader threads for about a
second and a half. Every test builds a tiny store in its own tmp directory.
"""

import json
import os
import re
import shutil
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.datasets._ingest import _swap_in  # noqa: E402
from camber.store import _fence, _swap  # noqa: E402
from camber.store.facilities import _is_store_dir  # noqa: E402
from camber.store.parquet_store import ParquetStore, StoreReadError, _scratch_store  # noqa: E402

FID = fx.FID
REG = default_registry()
_TOKEN = re.compile(r"(@[A-Za-z0-9_.:\-]+?)-[0-9a-f]{6}\b")


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch):
    monkeypatch.setattr(_fence, "_WAIT", 0.05)  # a reader gives up on a busy writer quickly


@pytest.fixture()
def store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.points()  # a current catalog, as an ingest leaves it
    # the same rows again, ready to be swapped in as a forced re-ingest would
    tpl = str(tmp_path / "template")
    scratch = _scratch_store(tpl)
    scratch.write_role_frame(
        fx.ahu_frame(fault_week=1), facility_id=FID, equip="DemoAHU", equip_class="AHU"
    )
    scratch.write_role_frame(
        fx.ahu_frame(seed=1, reheat_week=2), facility_id=FID, equip="DemoAHU2", equip_class="AHU"
    )
    st.template = tpl
    return st


def _reingest(st) -> None:
    """A forced re-ingest's swap: stage the same facility, swap it in."""
    staging = os.path.join(st.root, "_staging", f"ingest-{os.getpid()}-{threading.get_ident():x}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(st.template, staging)
    try:
        _swap_in(st, staging, FID)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _ctx(st):
    return CallContext(store=st.root, allowed_facilities=frozenset([FID]))


def _norm(res) -> str:
    return _TOKEN.sub(r"\1-TOKEN", json.dumps([res.data, res.text], sort_keys=True, default=str))


def _in_thread(fn):
    """Run ``fn`` in another thread (it is not the writer) and return its outcome."""
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except Exception as e:  # noqa: BLE001 - the outcome is what is checked
            out["error"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(30)
    return out


def _between_renames(monkeypatch, probe):
    """Make the next swap call ``probe()`` right after it renamed the old partition aside."""
    real = os.rename
    seen: list = []

    def rename(src, dst):
        real(src, dst)
        if str(dst).endswith(".old") and not seen:
            seen.append(probe())

    monkeypatch.setattr(_swap.os, "rename", rename)
    return seen


def test_a_read_between_the_two_renames_is_retryable(store, monkeypatch):
    reader = ParquetStore(store.root)
    seen = _between_renames(
        monkeypatch,
        lambda: (
            os.path.isdir(os.path.join(store.root, f"facility_id={FID}")),
            _in_thread(lambda: reader.read_long(facility_id=FID)),
            _in_thread(lambda: REG.call("points", {"facility_id": FID}, _ctx(store))),
            _in_thread(lambda: REG.call("facilities", {}, _ctx(store))),
        ),
    )
    _reingest(store)
    (present, read, points, listing) = seen[0]
    assert not present  # the facility really was absent at that moment
    assert isinstance(read.get("error"), StoreReadError), read  # not an empty frame
    assert read["error"].retryable and read["error"].facility_id == FID
    for out in (points, listing):
        e = out.get("error")
        assert isinstance(e, CapabilityError) and e.code == "unavailable", out
        assert e.data == {"retryable": True, "facility_id": FID}
    # once the swap is done, the same calls answer normally
    assert len(reader.read_long(facility_id=FID)) > 0
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data


def test_a_read_a_swap_overlapped_is_read_again_then_refused(store, monkeypatch):
    reader = ParquetStore(store.root)
    want = reader.read_long(facility_id=FID)
    real = ParquetStore._read_long
    swaps = {"left": 1}

    def read_then_swap(self, *a, **kw):
        df = real(self, *a, **kw)
        if swaps["left"]:
            swaps["left"] -= 1
            _reingest(store)  # the facility is replaced after this read listed its files
        return df

    monkeypatch.setattr(ParquetStore, "_read_long", read_then_swap)
    assert reader.read_long(facility_id=FID).equals(want)  # read again, after the swap
    swaps["left"] = 2  # a swap during the read and during its second try: retryable error
    with pytest.raises(StoreReadError) as ei:
        reader.read_long(facility_id=FID)
    assert ei.value.retryable and ei.value.facility_id == FID


@pytest.mark.parametrize("name", ["points", "coverage", "facilities"])
def test_a_call_a_swap_overlapped_is_unavailable(store, monkeypatch, name):
    real = ParquetStore.points

    def points_then_swap(self, *a, **kw):
        out = real(self, *a, **kw)
        monkeypatch.setattr(ParquetStore, "points", real)
        _reingest(store)  # the answer is computed from data the call read before the swap
        return out

    monkeypatch.setattr(ParquetStore, "points", points_then_swap)
    args = {} if name == "facilities" else {"facility_id": FID}
    with pytest.raises(CapabilityError) as ei:
        REG.call(name, args, _ctx(store))
    assert ei.value.code == "unavailable"
    assert ei.value.data == {"retryable": True, "facility_id": FID}
    assert REG.call(name, args, _ctx(store)).data  # and a retry answers


def test_removing_writers_move_the_fence(store):
    def text():
        with open(os.path.join(store.root, "_fence", FID)) as fh:
            return fh.read()

    _reingest(store)
    seen = [text()]
    store.prune(before_year=2027, reason="test")  # every year of the facility, in one fence
    seen.append(text())
    assert not os.listdir(os.path.join(store.root, f"facility_id={FID}"))
    store.drop_facility(FID, reason="test")
    seen.append(text())
    assert all(t.startswith("idle ") for t in seen) and len(set(seen)) == 3
    # a store whose every facility is gone is still a store
    shutil.rmtree(store.root + "/_staging", ignore_errors=True)
    for n in os.listdir(store.root):
        if n != "_fence":
            p = os.path.join(store.root, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    assert _is_store_dir(store.root)


def test_a_busy_marker_of_a_dead_writer_does_not_block_readers(store):
    os.makedirs(os.path.join(store.root, "_fence"), exist_ok=True)
    with open(os.path.join(store.root, "_fence", FID), "w") as fh:
        fh.write("busy 999999999 0123456789abcdef\n")  # no such process
    assert len(ParquetStore(store.root).read_long(facility_id=FID)) > 0
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data


def test_readers_during_repeated_forced_reingests_never_get_a_wrong_answer(store, monkeypatch):
    monkeypatch.setattr(_fence, "_WAIT", 1.0)
    calls = [("points", {"facility_id": FID}), ("coverage", {"facility_id": FID})]
    calls.append(("facilities", {}))
    base = {n: _norm(REG.call(n, a, _ctx(store))) for n, a in calls}
    stop = threading.Event()
    stats = {"swaps": 0, "same": 0, "unavailable": 0, "wrong": []}
    lock = threading.Lock()

    def writer():
        while not stop.is_set():
            _reingest(store)
            stats["swaps"] += 1

    def reader(k):
        i = k
        while not stop.is_set():
            n, a = calls[i % len(calls)]
            i += 1
            try:
                got, err = _norm(REG.call(n, a, _ctx(store))), None
            except CapabilityError as e:
                got, err = None, e
            with lock:
                if err is not None and err.code == "unavailable" and err.data.get("retryable"):
                    stats["unavailable"] += 1
                elif err is None and got == base[n]:
                    stats["same"] += 1
                else:
                    stats["wrong"].append((n, err and (err.code, err.message), got))

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader, args=(k,)) for k in range(4)
    ]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(60)
    assert stats["swaps"] >= 5 and stats["same"] >= 5, stats
    assert stats["wrong"] == [], stats["wrong"][:3]
