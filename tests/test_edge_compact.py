"""Spool journal compaction, the spool lock and retirement (camber.edge.spool, 0.95, #18)."""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.edge.spool as spool_mod  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.edge.spool import Spool, SpoolRetired  # noqa: E402
from camber.portfolio import PortfolioLocked  # noqa: E402


def _clock():
    return datetime(2024, 1, 1, tzinfo=timezone.utc)


class _OkSink:
    def __init__(self):
        self.got = []

    def put(self, key, data, *, content_type="", metadata=None):
        self.got.append((key, data))
        return {"ok": True}


class _FailSink:
    def put(self, key, data, *, content_type="", metadata=None):
        return {"ok": False}


def _ident(sp):
    return [
        (e.seq, e.key, e.file, e.content_type, e.metadata, e.bytes, e.enqueued_ts, e.attempts)
        for e in sp.pending()
    ]


def _lines(sp):
    with open(os.path.join(sp.root, "journal.ndjson"), encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


def _busy_spool(tmp_path):
    """Five batches: two delivered, one failed twice (attempts=2), two untouched."""
    sp = Spool(str(tmp_path), clock=_clock)
    for i in range(5):
        sp.enqueue(f"k{i}", f"payload-{i}".encode(), content_type="x", metadata={"i": i})
    sp.drain(_OkSink(), max_batches=2)
    for _ in range(2):
        sp.drain(_FailSink(), _sleep=lambda s: None)
    return sp


def test_compaction_keeps_every_pending_batch_exactly(tmp_path):
    sp = _busy_spool(tmp_path)
    before = _ident(sp)
    assert [b[1] for b in before] == ["k2", "k3", "k4"] and before[0][7] == 2
    n_before = len(_lines(sp))
    res = sp.compact()
    assert res.compacted and res.pending == 3 and res.records_before == n_before
    assert res.records_after == 4 and len(_lines(sp)) == 4  # 3 enqueue + 1 mark
    assert res.bytes_after < res.bytes_before
    assert _ident(Spool(str(tmp_path), clock=_clock)) == before  # a fresh process sees the same
    # sequence numbers are never reused: the mark carries the high-water mark
    e = sp.enqueue("k5", b"new", content_type="x")
    assert e.seq == 5 and not os.path.exists(os.path.join(sp.root, "journal.ndjson.compact"))
    # the queue still drains in order and delivers everything
    sink = _OkSink()
    sp.drain(sink)
    assert [k for k, _ in sink.got] == ["k2", "k3", "k4", "k5"]


def test_compaction_of_a_fully_delivered_spool_keeps_the_high_water_mark(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    for i in range(3):
        sp.enqueue(f"k{i}", b"x", content_type="x")
    sp.drain(_OkSink())
    res = sp.compact()
    assert res.pending == 0 and res.records_after == 1 and res.high_water_seq == 2
    assert sp.enqueue("k3", b"y", content_type="x").seq == 3
    assert sp.compact().records_after == 2  # idempotent-ish: one pending + the mark


def test_compaction_dry_run_changes_nothing(tmp_path):
    sp = _busy_spool(tmp_path)
    raw = open(os.path.join(sp.root, "journal.ndjson"), "rb").read()
    res = sp.compact(dry_run=True)
    assert res.dry_run and not res.compacted and res.records_after == 4
    assert open(os.path.join(sp.root, "journal.ndjson"), "rb").read() == raw


def test_crash_before_the_swap_leaves_the_old_journal(tmp_path, monkeypatch):
    sp = _busy_spool(tmp_path)
    before = _ident(sp)
    raw = open(os.path.join(sp.root, "journal.ndjson"), "rb").read()

    def boom(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(spool_mod.os, "replace", boom)
    with pytest.raises(OSError, match="power cut"):
        sp.compact()
    monkeypatch.undo()
    # the side file exists, the journal is untouched and the queue is whole
    assert os.path.exists(os.path.join(sp.root, "journal.ndjson.compact"))
    assert open(os.path.join(sp.root, "journal.ndjson"), "rb").read() == raw
    fresh = Spool(str(tmp_path), clock=_clock)
    assert _ident(fresh) == before
    # a torn side file from the crash is simply overwritten by the next compaction
    with open(os.path.join(sp.root, "journal.ndjson.compact"), "w") as fh:
        fh.write('{"op":"enq')
    assert fresh.compact().compacted and _ident(fresh) == before
    assert not os.path.exists(os.path.join(sp.root, "journal.ndjson.compact"))


def test_crash_after_the_swap_is_the_new_journal(tmp_path, monkeypatch):
    sp = _busy_spool(tmp_path)
    before = _ident(sp)

    def boom(path):
        raise OSError("crash before dir fsync")

    monkeypatch.setattr(spool_mod, "_fsync_dir", boom)
    with pytest.raises(OSError):
        sp.compact()
    monkeypatch.undo()
    assert len(_lines(sp)) == 4 and _ident(Spool(str(tmp_path), clock=_clock)) == before


def test_torn_lines_are_dropped_and_appends_start_a_fresh_line(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k0", b"a", content_type="x")
    with open(os.path.join(sp.root, "journal.ndjson"), "a", encoding="utf-8") as fh:
        fh.write('{"op":"enqueue","seq":1,"ke')  # a crash mid-append, no newline
    # the next commit must not be glued onto the torn line (it would be lost with it)
    sp.enqueue("k1", b"b", content_type="x")
    assert [e.key for e in sp.pending()] == ["k0", "k1"]
    res = sp.compact()
    assert res.torn == 1 and res.pending == 2 and [e.key for e in sp.pending()] == ["k0", "k1"]
    assert all(json.loads(ln) for ln in _lines(sp))


def test_orphans_and_missing_payloads_are_reported_not_deleted(tmp_path):
    sp = Spool(str(tmp_path), clock=_clock)
    sp.enqueue("k0", b"a", content_type="x")
    e1 = sp.enqueue("k1", b"b", content_type="x")
    os.remove(os.path.join(sp.root, "pending", e1.file))  # an ack cut short after the delete
    with open(os.path.join(sp.root, "pending", "000000000099.blob"), "wb") as fh:
        fh.write(b"uncommitted")  # an enqueue cut short before its commit
    with open(os.path.join(sp.root, "tmp", "000000000100.blob"), "wb") as fh:
        fh.write(b"half")
    res = sp.compact()
    assert res.missing_payloads == [e1.file]
    assert res.orphan_payloads == ["000000000099.blob"] and res.tmp_files == ["000000000100.blob"]
    assert os.path.exists(os.path.join(sp.root, "pending", "000000000099.blob"))
    assert os.path.exists(os.path.join(sp.root, "tmp", "000000000100.blob"))
    assert [e.key for e in sp.pending()] == ["k0"]


@pytest.mark.skipif(sys.platform == "win32", reason="flock semantics")
def test_a_busy_spool_refuses_compaction(tmp_path):
    import fcntl

    sp = Spool(str(tmp_path), clock=_clock, lock_timeout=0.0)
    sp.enqueue("k0", b"a", content_type="x")
    fh = open(os.path.join(sp.root, "_lock"), "a+b")  # another holder (a separate descriptor)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PortfolioLocked):
            sp.compact()
        with pytest.raises(PortfolioLocked):
            sp.enqueue("k1", b"b", content_type="x")
    finally:
        fh.close()
    assert sp.compact().pending == 1


def test_retired_spool_refuses_new_batches_and_stops_the_daemon(tmp_path):
    from camber.edge.forwarder import Forwarder

    sp = Spool(str(tmp_path), clock=_clock)
    assert sp.retirement() is None
    sp.retire({"retired_at": "2024-01-02T00:00:00Z", "device_id": "pi-7"})
    assert sp.retirement()["device_id"] == "pi-7"
    with pytest.raises(SpoolRetired, match="pi-7"):
        sp.enqueue("k0", b"a", content_type="x")

    class _Src:
        def point_names(self):
            return ["ahu1_sat"]

        def load_points(self, names, resample="1h"):
            import pandas as pd

            idx = pd.date_range("2024-01-01", periods=3, freq="h")
            return pd.DataFrame({"ahu1_sat": [55.0, 56.0, 57.0]}, index=idx)

    fwd = Forwarder(_Src(), _OkSink(), facility_id="fox-lodge-9f3a1c", spool=sp)
    with pytest.raises(SpoolRetired):
        fwd.run(0, iterations=3, _sleep=lambda s: None)
    # a torn retired.json still counts as retired (fail closed)
    with open(os.path.join(sp.root, "retired.json"), "w") as fh:
        fh.write("{")
    assert sp.retirement()["unreadable"] is True


def _cfg(tmp_path, spool_dir):
    csv = tmp_path / "trend.csv"
    csv.write_text("timestamp,ahu1_sat\n2024-01-01 00:00,55\n2024-01-01 01:00,56\n")
    p = tmp_path / "edge.json"
    doc = {
        "facility_id": "fox-lodge-9f3a1c",
        "spool_dir": spool_dir,
        "sink": {"kind": "collect"},
        "source": {"kind": "csv_wide", "path": str(csv)},
    }
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def test_cli_compact_and_status(tmp_path, capsys, monkeypatch):
    for k in ("CAMBER_EDGE_SPOOL_DIR", "CAMBER_EDGE_FACILITY_ID", "CAMBER_EDGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    sp = _busy_spool(tmp_path / "spool")
    with open(os.path.join(sp.root, "pending", "000000000099.blob"), "wb") as fh:
        fh.write(b"x")
    cfg = _cfg(tmp_path, sp.root)
    assert main(["edge", "compact", cfg, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would compact" in out and "3 batch(es) still pending" in out and "orphan" in out
    assert main(["edge", "compact", cfg, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["compacted"] and data["pending"] == 3
    assert main(["edge", "status", cfg]) == 0
    assert "RETIRED" not in capsys.readouterr().out
    sp.retire({"retired_at": "2024-01-02T00:00:00Z", "device_id": "pi-7"})
    assert main(["edge", "status", cfg]) == 0
    assert "RETIRED" in capsys.readouterr().out
    assert main(["edge", "send-once", cfg]) == 1
    assert "decommissioned" in capsys.readouterr().err
    assert main(["edge", "run", cfg]) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="flock semantics")
def test_cli_compact_busy(tmp_path, capsys, monkeypatch):
    import fcntl

    for k in ("CAMBER_EDGE_SPOOL_DIR", "CAMBER_EDGE_FACILITY_ID", "CAMBER_EDGE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    sp = Spool(str(tmp_path / "spool"))
    cfg = _cfg(tmp_path, sp.root)
    fh = open(os.path.join(sp.root, "_lock"), "a+b")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["edge", "compact", cfg, "--lock-timeout", "0"]) == 1
        assert "busy" in capsys.readouterr().err
    finally:
        fh.close()
