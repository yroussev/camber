"""Tests for the durable store-and-forward spool (camber.edge.spool)."""

from datetime import datetime, timezone

from camber.edge.spool import Spool


def _clock():
    return datetime(2024, 1, 1, tzinfo=timezone.utc)


class _OkSink:
    def __init__(self):
        self.got = []

    def put(self, key, data, *, content_type="", metadata=None):
        self.got.append((key, data))
        return {"ok": True}


class _FlakySink:
    """Raises on the first ``fail_first`` calls, then succeeds."""

    def __init__(self, fail_first):
        self.calls = 0
        self.fail_first = fail_first
        self.got = []

    def put(self, key, data, *, content_type="", metadata=None):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("link down")
        self.got.append((key, data))
        return {"ok": True}


def test_enqueue_is_atomic_and_pending(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k1", b"aaa", content_type="x", metadata={"n": 1})
    sp.enqueue("k2", b"bbbb", content_type="x", metadata={})
    pend = sp.pending()
    assert [e.key for e in pend] == ["k1", "k2"]  # oldest-first
    assert sp.depth() == (2, 7)


def test_drain_delivers_and_acks(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k1", b"aaa", content_type="x", metadata={})
    sink = _OkSink()
    res = sp.drain(sink)
    assert res.forwarded == 1 and res.remaining == 0
    assert sink.got == [("k1", b"aaa")] and sp.depth() == (0, 0)


def test_retry_then_success_loses_nothing(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k1", b"one", content_type="x", metadata={})
    sp.enqueue("k2", b"two", content_type="x", metadata={})
    flaky = _FlakySink(fail_first=2)
    calls = []
    # First drain: k1 fails -> stop, nothing delivered, both remain.
    r1 = sp.drain(flaky, _sleep=calls.append)
    assert r1.forwarded == 0 and sp.depth()[0] == 2 and calls  # a backoff was applied
    # Subsequent drains eventually deliver both, in order, byte-for-byte.
    sp.drain(flaky, _sleep=lambda s: None)
    sp.drain(flaky, _sleep=lambda s: None)
    assert flaky.got == [("k1", b"one"), ("k2", b"two")]
    assert sp.depth() == (0, 0)


def test_backfill_survives_a_restart(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k1", b"aaa", content_type="x", metadata={})
    sp.enqueue("k2", b"bbb", content_type="x", metadata={})
    # Simulate a reboot: a brand-new Spool over the same directory reconstructs from the journal.
    reborn = Spool(str(tmp_path), clock=_clock)
    assert [e.key for e in reborn.pending()] == ["k1", "k2"]
    sink = _OkSink()
    reborn.drain(sink)
    assert reborn.depth() == (0, 0) and len(sink.got) == 2


def test_bounded_disk_evicts_oldest_with_warning(tmp_path, caplog):
    sp = Spool(str(tmp_path), max_bytes=10, clock=_clock)
    sp.enqueue("k1", b"aaaaa", content_type="x", metadata={})  # 5 bytes
    sp.enqueue("k2", b"bbbbb", content_type="x", metadata={})  # 5 bytes -> at cap
    import logging

    with caplog.at_level(logging.WARNING, logger="camber.edge"):
        sp.enqueue("k3", b"ccccc", content_type="x", metadata={})  # evicts oldest (k1)
    keys = [e.key for e in sp.pending()]
    assert "k1" not in keys and "k3" in keys
    assert any("evict" in r.message and "k1" in r.message for r in caplog.records)


def test_ack_is_idempotent(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    e = sp.enqueue("k1", b"aaa", content_type="x", metadata={})
    sp.ack(e)
    sp.ack(e)  # no error, no resurrection
    assert sp.depth() == (0, 0)
