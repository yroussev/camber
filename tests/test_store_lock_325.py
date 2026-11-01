"""0.112 (#325): a writer on another system sharing a store is excluded too.

``flock`` reaches only the processes of one kernel, so a macOS host and a Docker Desktop
container (or two machines on one share) both got the store lock of one bind-mounted store. A
writer now also claims ``<store>/_lock.owner`` with an exclusive create and keeps a heartbeat in
it; another writer waits for a live claim and takes over a dead one. Another system is simulated
here by a claim naming another kernel, kept alive by a thread.
"""

import json
import os
import threading
import time

import pytest

from camber.portfolio import _lock
from camber.store._lock import StoreLocked, store_lock


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(_lock, "CLAIM_BEAT", 0.05)
    monkeypatch.setattr(_lock, "CLAIM_STALE", 0.5)


class _Foreign:
    """A claim written by a writer of another kernel; ``beat()`` keeps it alive from a thread
    until ``stop()``, which removes it as that writer would on release."""

    def __init__(self, root, kernel="linux:another-kernel"):
        self.path = os.path.join(root, _lock.CLAIM_FILE)
        self.rec = {"pid": 4242, "host": "box", "kernel": kernel, "since": "x", "token": "t"}
        self.n = 0
        self._write()
        self._stop = threading.Event()
        self._t = None

    def _write(self):
        with open(self.path, "w") as fh:
            json.dump({**self.rec, "beat": f"{self.n:012d}"}, fh)

    def beat(self):
        def run():
            while not self._stop.wait(0.05):
                self.n += 1
                self._write()

        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        return self

    def stop(self, remove=True):
        self._stop.set()
        if self._t is not None:
            self._t.join()
        if remove and os.path.exists(self.path):
            os.remove(self.path)


def test_a_writer_claims_the_store_and_lets_go(tmp_path):
    root = str(tmp_path)
    claim = os.path.join(root, _lock.CLAIM_FILE)
    with store_lock(root, timeout=0):
        rec = json.loads(open(claim).read())
        assert rec["pid"] == os.getpid() and rec["kernel"] == _lock.kernel_id()
        first = open(claim).read()
        time.sleep(0.2)
        assert open(claim).read() != first  # the heartbeat moves
        with store_lock(root, timeout=0):  # re-entrant: no second claim
            pass
        assert os.path.exists(claim)
    assert not os.path.exists(claim)


def test_a_live_writer_on_another_system_is_refused_with_a_clear_message(tmp_path):
    root = str(tmp_path)
    other = _Foreign(root).beat()
    try:
        t0 = time.monotonic()
        with pytest.raises(StoreLocked) as ei:
            with store_lock(root, timeout=1.0):
                pytest.fail("got the lock a live writer on another system holds")
        assert time.monotonic() - t0 >= 0.9  # it waited, and did not take the claim over
        msg = str(ei.value)
        assert "4242@box" in msg and "another system" in msg and "linux:another-kernel" in msg
        assert json.loads(open(other.path).read())["token"] == "t"
    finally:
        other.stop()
    with store_lock(root, timeout=0):  # its flock was released with the refusal
        pass


def test_a_waiting_writer_gets_the_lock_when_the_other_system_lets_go(tmp_path):
    root = str(tmp_path)
    other = _Foreign(root).beat()
    threading.Timer(0.3, other.stop).start()
    with store_lock(root, timeout=5.0):
        rec = json.loads(open(other.path).read())
        assert rec["pid"] == os.getpid()


def test_a_dead_claim_of_another_system_is_taken_over_after_its_heartbeat_stops(tmp_path):
    root = str(tmp_path)
    other = _Foreign(root)  # never beats: its writer was killed
    t0 = time.monotonic()
    with store_lock(root, timeout=5.0):
        assert time.monotonic() - t0 >= _lock.CLAIM_STALE
        assert json.loads(open(other.path).read())["pid"] == os.getpid()
    assert not os.path.exists(other.path)


def test_a_dead_claim_of_this_kernel_is_taken_over_at_once(tmp_path):
    import subprocess
    import sys

    root = str(tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    other = _Foreign(root, kernel=_lock.kernel_id())  # a writer here, killed
    other.rec.update(pid=dead.pid, pidns=_lock.pid_namespace())  # #330: seen to be gone
    other._write()
    with store_lock(root, timeout=0):
        pass


def test_an_unreadable_claim_is_stale_after_the_wait_not_a_hang(tmp_path):
    root = str(tmp_path)
    with open(os.path.join(root, _lock.CLAIM_FILE), "wb") as fh:
        fh.write(b"\x00garbage")
    with store_lock(root, timeout=5.0):
        pass


def test_a_takeover_never_removes_a_claim_made_meanwhile(tmp_path):
    path = os.path.join(str(tmp_path), _lock.CLAIM_FILE)
    with open(path, "wb") as fh:
        fh.write(b'{"token": "new"}')
    _lock._take_over(path, b'{"token": "old"}')
    assert open(path, "rb").read() == b'{"token": "new"}'
    assert os.listdir(str(tmp_path)) == [_lock.CLAIM_FILE]
    _lock._take_over(path, b'{"token": "new"}')
    assert os.listdir(str(tmp_path)) == []


def test_the_kernel_id_is_stable_and_names_its_kind():
    kid = _lock.kernel_id()
    assert kid == _lock.kernel_id()
    assert kid.split(":", 1)[0] in ("linux", "darwin", "host") and kid.split(":", 1)[1]
