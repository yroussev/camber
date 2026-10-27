"""#317: two edge cases of the #306 write fence.

1. A busy marker left by a writer killed mid-swap names a pid; once the system reuses that pid for
   an unrelated process, the marker must not block readers (it records the writer's start time),
   and the next writer to take the store lock resets it.
2. A removal (``datasets remove --purge-store``, ``drop_facility(forget=True)``) keeps its fence
   until the registry entry is gone, so a reader that overlaps it is told to retry.

Every test builds a tiny store in its own tmp directory.
"""

import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.store import _fence  # noqa: E402
from camber.store.facilities import FacilityRegistry  # noqa: E402
from camber.store.parquet_store import ParquetStore  # noqa: E402

FID = fx.FID
REG = default_registry()
needs_start_time = pytest.mark.skipif(
    _fence._started(os.getpid()) is None, reason="no process start time on this platform"
)


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch):
    monkeypatch.setattr(_fence, "_WAIT", 0.05)


@pytest.fixture()
def store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.points()
    return st


@pytest.fixture()
def other():
    """A live process that is not a writer (it stands for a reused pid)."""
    p = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    yield p.pid
    p.stdin.close()
    p.wait(30)


def _ctx(st):
    return CallContext(store=st.root, allowed_facilities=frozenset([FID]))


def _mark(st, text):
    os.makedirs(os.path.join(st.root, "_fence"), exist_ok=True)
    with open(os.path.join(st.root, "_fence", FID), "w") as fh:
        fh.write(text)


def _fence_text(st):
    with open(os.path.join(st.root, "_fence", FID)) as fh:
        return fh.read()


def _in_thread(fn):
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


@needs_start_time
def test_a_writer_records_its_start_time(store):
    seen = []
    with _fence.fence(store.root, FID):
        seen.append(_fence_text(store).split())
    words = seen[0]
    assert words[:3] == ["busy", str(os.getpid()), _fence._started(os.getpid())]
    assert len(words) == 4


@needs_start_time
def test_a_busy_marker_whose_pid_was_reused_does_not_block_readers(store, other):
    assert _fence._started(other) not in (None, _fence._started(os.getpid()))
    _mark(store, f"busy {other} 1.000001 0123456789abcdef\n")  # that pid, another start time
    assert len(ParquetStore(store.root).read_long(facility_id=FID)) > 0
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data
    assert REG.call("facilities", {}, _ctx(store)).data


@needs_start_time
def test_a_busy_marker_of_a_live_writer_still_blocks_readers(store, other):
    _mark(store, f"busy {other} {_fence._started(other)} 0123456789abcdef\n")
    for name, args in (("points", {"facility_id": FID}), ("facilities", {})):
        with pytest.raises(CapabilityError) as ei:
            REG.call(name, args, _ctx(store))
        assert ei.value.code == "unavailable" and ei.value.data["retryable"] is True


@needs_start_time
def test_the_next_store_lock_resets_a_stale_busy_marker(store, other):
    stale = f"busy {other} 1.000001 0123456789abcdef\n"
    _mark(store, stale)
    os.makedirs(os.path.join(store.root, "_fence"), exist_ok=True)
    live = f"busy {other} {_fence._started(other)} fedcba9876543210\n"
    with open(os.path.join(store.root, "_fence", "other-site"), "w") as fh:
        fh.write(live)
    with store.lock():
        pass
    assert _fence_text(store).startswith("idle ")
    with open(os.path.join(store.root, "_fence", "other-site")) as fh:
        assert fh.read() == live  # a writer that may still be at work is left alone


def test_a_dead_writers_marker_is_reset_too(store):
    _mark(store, "busy 999999999 - 0123456789abcdef\n")  # no such process
    assert _fence.recover(store.root) == [FID]
    assert _fence_text(store).startswith("idle ")


def test_a_fence_this_process_holds_is_never_reset(store):
    with _fence.fence(store.root, FID):
        assert _fence.recover(store.root) == []
        assert _fence_text(store).startswith("busy ")


@pytest.mark.parametrize("how", ["drop_facility", "datasets_remove"])
def test_a_removal_holds_its_fence_until_the_registry_entry_is_gone(store, monkeypatch, how):
    real = FacilityRegistry._forget
    seen: list = []

    def forget(self, *a, **kw):
        seen.append(
            (
                _fence_text(store).split()[0],
                _in_thread(lambda: REG.call("points", {"facility_id": FID}, _ctx(store))),
            )
        )
        return real(self, *a, **kw)

    monkeypatch.setattr(FacilityRegistry, "_forget", forget)
    if how == "drop_facility":
        store.drop_facility(FID, forget=True, reason="test")
    else:
        with store.lock():  # as `camber datasets remove --purge-store` holds it
            store._drop_facility(FID, True, reason="datasets remove test")
    (state, out) = seen[0]
    assert state == "busy"
    e = out.get("error")
    assert isinstance(e, CapabilityError) and e.code == "unavailable", out
    assert e.data == {"retryable": True, "facility_id": FID}
    assert _fence_text(store).startswith("idle ")
    assert FID not in ParquetStore(store.root).facilities()
