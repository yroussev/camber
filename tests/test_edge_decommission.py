"""Edge decommissioning: flush, wait for acks, retire, record (camber.edge.decommission, 0.95)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.edge.decommission as dmod  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.edge.decommission import (  # noqa: E402
    decommission,
    default_device_id,
    record_retirement,
)
from camber.edge.spool import Spool, SpoolRetired  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402

FID = "north-annex-1a2b3c"


class _Sink:
    def __init__(self, ok=True, fail_first=0):
        self.ok, self.fail_first, self.calls, self.got = ok, fail_first, 0, []

    def put(self, key, data, *, content_type="", metadata=None):
        self.calls += 1
        if not self.ok or self.calls <= self.fail_first:
            return {"ok": False}
        self.got.append(key)
        return {"ok": True}


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _spool(tmp_path, n=3):
    sp = Spool(str(tmp_path / "spool"))
    for i in range(n):
        sp.enqueue(f"facility_id={FID}/year=2024/part-{i}.parquet", b"x", content_type="x")
    return sp


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("North Annex", facility_id=FID, reason="t", activate=True)
    return pf


def _hold(pf, fid):
    p = os.path.join(pf.root, "_portfolio.json")
    doc = json.load(open(p))
    doc["legal_holds"] = {fid: {"reason": "litigation"}}
    json.dump(doc, open(p, "w"))


def test_dry_run_changes_nothing(tmp_path):
    sp = _spool(tmp_path)
    sink = _Sink()
    res = decommission(sp, sink, facility_id=FID, device_id="pi-7")
    assert res.dry_run and res.pending_before == 3 and not res.retired
    assert "not acknowledged yet" in res.refused and len(res.unacknowledged) == 3
    assert sink.calls == 0 and sp.retirement() is None and sp.depth()[0] == 3


def test_apply_needs_reason_and_confirmation(tmp_path):
    sp = _spool(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        decommission(sp, _Sink(), facility_id=FID, apply=True, yes=True)
    with pytest.raises(ValueError, match="--yes"):
        decommission(sp, _Sink(), facility_id=FID, apply=True, reason="r")
    with pytest.raises(ValueError, match="--yes"):
        decommission(sp, _Sink(), facility_id=FID, apply=True, reason="r", confirm="other")
    assert sp.retirement() is None


def test_flush_ack_retire_and_registry_note(tmp_path, ws):
    sp = _spool(tmp_path)
    clock = _Clock()
    sink = _Sink(fail_first=2)  # the link is flaky: two failures, then it recovers
    res = decommission(
        sp, sink, facility_id=FID, device_id="pi-7", reason="building sold", apply=True,
        confirm=FID, portfolio=ws, _sleep=clock.sleep, _monotonic=clock,
    )  # fmt: skip
    assert res.retired and not res.forced and res.forwarded == 3 and res.pending_after == 0
    assert len(sink.got) == 3 and res.registry_noted == "recorded"
    rec = sp.retirement()
    assert rec["device_id"] == "pi-7" and rec["reason"] == "building sold" and rec["actor"]
    assert rec["unacknowledged"] == []
    note = ws.registry.get(FID)["edge_devices"]["pi-7"]
    assert note["state"] == "retired" and note["retired_at"] == rec["retired_at"]
    audit = [r for r in ws.audit_log(facility_id=FID) if r["action"] == "edge.decommission"]
    assert len(audit) == 1 and audit[0]["details"]["device_id"] == "pi-7"
    with pytest.raises(SpoolRetired):
        sp.enqueue("k", b"x", content_type="x")
    # running it again is a no-op: already retired, already recorded
    again = decommission(
        sp, sink, facility_id=FID, device_id="pi-7", reason="r", apply=True, yes=True,
        portfolio=ws,
    )  # fmt: skip
    assert again.already_retired and again.registry_noted == "already recorded"
    assert len([r for r in ws.audit_log() if r["action"] == "edge.decommission"]) == 1


def test_refuses_while_unacknowledged(tmp_path, ws):
    sp = _spool(tmp_path)
    clock = _Clock()
    res = decommission(
        sp, _Sink(ok=False), facility_id=FID, reason="r", apply=True, yes=True, wait=5,
        portfolio=ws, _sleep=clock.sleep, _monotonic=clock,
    )  # fmt: skip
    assert res.refused and "still unacknowledged" in res.refused and not res.retired
    assert sp.retirement() is None and sp.depth()[0] == 3
    assert not [r for r in ws.audit_log() if r["action"] == "edge.decommission"]


def test_force_retires_keeps_payloads_and_is_blocked_by_a_legal_hold(tmp_path, ws):
    sp = _spool(tmp_path)
    clock = _Clock()
    kw = dict(apply=True, yes=True, wait=0, _sleep=clock.sleep, _monotonic=clock)
    with pytest.raises(ValueError, match="reason"):
        decommission(sp, _Sink(ok=False), facility_id=FID, force=True, **kw)
    _hold(ws, FID)
    with pytest.raises(ValueError, match="legal hold"):
        decommission(
            sp, _Sink(ok=False), facility_id=FID, force=True, reason="r", portfolio=ws, **kw
        )
    assert sp.retirement() is None
    res = decommission(
        sp, _Sink(ok=False), facility_id=FID, force=True, reason="device destroyed", **kw
    )
    assert res.retired and res.forced and len(res.unacknowledged) == 3
    assert res.registry_noted is None  # no workspace passed
    assert sp.depth()[0] == 3  # the payloads are kept on disk
    assert sp.retirement()["forced"] is True


def test_a_crash_before_the_registry_note_is_finished_by_a_rerun(tmp_path, ws, monkeypatch):
    sp = _spool(tmp_path, n=1)

    def boom(*a, **k):
        raise OSError("crash before the central note")

    monkeypatch.setattr(dmod, "record_retirement", boom)
    with pytest.raises(OSError):
        decommission(sp, _Sink(), facility_id=FID, reason="r", apply=True, yes=True, portfolio=ws)
    monkeypatch.undo()
    assert sp.retirement() is not None and "edge_devices" not in ws.registry.get(FID)
    res = decommission(sp, _Sink(), facility_id=FID, reason="r", apply=True, yes=True, portfolio=ws)
    assert res.already_retired and res.registry_noted == "recorded"
    assert default_device_id() in ws.registry.get(FID)["edge_devices"]


def test_a_crash_mid_flush_leaves_a_consistent_spool(tmp_path):
    sp = _spool(tmp_path)

    class _DiesAfterOne(_Sink):
        def put(self, key, data, **kw):
            if self.calls == 1:
                raise KeyboardInterrupt("process killed")
            return super().put(key, data, **kw)

    with pytest.raises(KeyboardInterrupt):
        decommission(sp, _DiesAfterOne(), facility_id=FID, reason="r", apply=True, yes=True)
    fresh = Spool(sp.root)
    assert fresh.retirement() is None and fresh.depth()[0] == 2  # one acked, two still queued
    res = decommission(fresh, _Sink(), facility_id=FID, reason="r", apply=True, yes=True)
    assert res.retired and res.forwarded == 2


def test_record_retirement_centrally(tmp_path, ws):
    sp = _spool(tmp_path, n=0)
    decommission(sp, _Sink(), facility_id=FID, device_id="pi-9", reason="r", apply=True, yes=True)
    receipt = sp.retirement()
    assert record_retirement(ws, receipt, reason="recorded from the device")["noted"] == "recorded"
    assert record_retirement(ws, receipt, reason="again")["noted"] == "already recorded"
    other = dict(receipt, facility_id="ghost-hall-778899")
    assert "audit only" in record_retirement(ws, other, reason="r")["noted"]
    with pytest.raises(ValueError, match="receipt"):
        record_retirement(ws, {"facility_id": FID}, reason="r")
    with pytest.raises(ValueError, match="reason"):
        record_retirement(ws, receipt, reason="")
    with pytest.raises(ValueError, match="retired for facility"):
        decommission(sp, _Sink(), facility_id="ghost-hall-778899")


def _cfg(tmp_path, **extra):
    doc = {
        "facility_id": FID,
        "spool_dir": str(tmp_path / "spool"),
        "sink": {"kind": "collect"},
        "device_id": "pi-7",
        **extra,
    }
    p = tmp_path / "edge.json"
    p.write_text(json.dumps(doc))
    return str(p)


def test_cli_decommission_and_record(tmp_path, ws, capsys, monkeypatch):
    for k in ("CAMBER_EDGE_SPOOL_DIR", "CAMBER_EDGE_FACILITY_ID", "CAMBER_EDGE_DEVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    monkeypatch.chdir(tmp_path)
    _spool(tmp_path, n=2)
    cfg = _cfg(tmp_path)
    assert main(["edge", "decommission", cfg]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "2 batch(es) pending" in out and "would refuse" in out
    assert main(["edge", "decommission", cfg, "--apply", "--reason", "sold"]) == 1
    assert "--yes" in capsys.readouterr().err
    assert main(["edge", "decommission", cfg, "--workspace", str(tmp_path / "nope")]) == 1
    capsys.readouterr()
    rc = main(["edge", "decommission", cfg, "--apply", "--yes", "--reason", "sold", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["retired"] and data["forwarded"] == 2 and data["device_id"] == "pi-7"
    assert main(["edge", "decommission", cfg, "--apply", "--yes", "--reason", "sold"]) == 0
    out = capsys.readouterr().out
    assert "already retired" in out and "record-retirement" in out
    receipt = str(tmp_path / "spool" / "retired.json")
    rc = main(
        ["edge", "record-retirement", receipt, "--reason", "from site", "--workspace", ws.root]
    )
    assert rc == 0 and "recorded" in capsys.readouterr().out
    rc = main(["edge", "decommission", cfg, "--workspace", ws.root, "--apply", "--yes",
               "--reason", "sold"])  # fmt: skip
    assert rc == 0 and "registry: already recorded" in capsys.readouterr().out


def test_cli_decommission_refused(tmp_path, capsys, monkeypatch):
    for k in ("CAMBER_EDGE_SPOOL_DIR", "CAMBER_EDGE_FACILITY_ID", "CAMBER_EDGE_DEVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    monkeypatch.chdir(tmp_path)
    _spool(tmp_path, n=1)
    cfg = _cfg(tmp_path)

    class _Down:
        def put(self, *a, **k):
            return {"ok": False}

    monkeypatch.setattr("camber.edge.config._build_sink", lambda spec, env: _Down())
    monkeypatch.setattr("camber.edge.spool._default_backoff", lambda n: 0.0)
    rc = main(["edge", "decommission", cfg, "--apply", "--yes", "--reason", "r", "--wait", "0"])
    assert rc == 1 and "REFUSED" in capsys.readouterr().out
    rc = main(["edge", "decommission", cfg, "--apply", "--yes", "--reason", "r", "--wait", "0",
               "--force"])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "FORCED" in out and "kept on disk" in out
