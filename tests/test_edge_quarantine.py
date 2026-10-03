"""Quarantine of uploads from facilities that do not accept data (camber.edge.quarantine, 0.95)."""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.edge.quarantine as qmod  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.edge.quarantine import (  # noqa: E402
    RECORD_SUFFIX,
    discard,
    land,
    list_quarantine,
    quarantine_reconciled,
    quarantine_root,
    release,
)
from camber.portfolio import Portfolio  # noqa: E402

ACTIVE = "north-annex-1a2b3c"
SUSP = "east-wing-4d5e6f"
GHOST = "ghost-hall-778899"


def _key(fid, payload, year=2024):
    return f"facility_id={fid}/year={year}/part-{hashlib.sha256(payload).hexdigest()[:16]}.parquet"


def _put(root, key, payload, mtime=None):
    path = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _audit(pf, action):
    return [r for r in pf.audit_log() if r["action"] == action]


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("North Annex", facility_id=ACTIVE, reason="t", activate=True)
    pf.add_facility("East Wing", facility_id=SUSP, reason="t", activate=True)
    pf.transition(SUSP, "suspend", reason="contract paused")
    return pf


@pytest.fixture
def inbox(tmp_path):
    root = str(tmp_path / "inbox")
    _put(root, _key(ACTIVE, b"a"), b"a")
    _put(root, _key(SUSP, b"s"), b"s")
    _put(root, _key(GHOST, b"g"), b"g")
    _put(root, f"facility_id={ACTIVE}/year=2024/part-0000000000000000.parquet", b"tampered")
    nd = hashlib.sha256(b"n").hexdigest()[:16]
    _put(root, f"facility_id={ACTIVE}/year=2024/part-{nd}.ndjson", b"n")
    _put(root, "stray.bin", b"?")
    return root


def test_land_dry_run_changes_nothing(ws, inbox):
    before = sorted(os.walk(inbox))
    rep = land(ws, inbox)
    assert rep["dry_run"] and not rep["applied"] and sorted(os.walk(inbox)) == before
    assert rep["counts"] == {"store": 1, "quarantine": 3, "inbox": 2}
    cats = {r["key"]: r["category"] for r in rep["objects"]}
    assert cats[_key(SUSP, b"s")] == "inactive" and cats[_key(GHOST, b"g")] == "unknown_facility"
    assert "hash_mismatch" in cats.values()
    assert not os.path.exists(quarantine_root(ws)) and not _audit(ws, "edge.land")
    with pytest.raises(ValueError, match="reason"):
        land(ws, inbox, apply=True)
    with pytest.raises(FileNotFoundError):
        land(ws, inbox + "-nope")


def test_land_apply_routes_and_audits(ws, inbox):
    rep = land(ws, inbox, apply=True, reason="nightly landing")
    assert rep["applied"]
    assert os.path.exists(os.path.join(ws.store_root, *_key(ACTIVE, b"a").split("/")))
    # nothing of the suspended / unknown facility entered the store
    assert not os.path.exists(os.path.join(ws.store_root, f"facility_id={SUSP}"))
    assert not os.path.exists(os.path.join(ws.store_root, f"facility_id={GHOST}"))
    q = {r["key"]: r for r in list_quarantine(ws)}
    assert set(q) == {
        _key(SUSP, b"s"),
        _key(GHOST, b"g"),
        f"facility_id={ACTIVE}/year=2024/part-0000000000000000.parquet",
    }
    rec = q[_key(SUSP, b"s")]
    assert rec["status"] == "held" and rec["state"] == "suspended"
    assert rec["reason"] == "nightly landing" and rec["sha256"] == hashlib.sha256(b"s").hexdigest()
    # orphan and ndjson stay in the inbox
    assert os.path.exists(os.path.join(inbox, "stray.bin"))
    assert len(list_quarantine(ws, facility_id=SUSP)) == 1
    audits = _audit(ws, "edge.land")
    assert {a["details"]["phase"] for a in audits} == {"begin", "done"}
    assert all(a["actor"] and a["reason"] == "nightly landing" for a in audits)
    assert {a["facility_id"] for a in audits} == {ACTIVE, SUSP, GHOST}
    # idempotent: a second run has nothing left to move
    again = land(ws, inbox, apply=True, reason="again")
    assert again["counts"]["store"] == 0 and again["counts"]["quarantine"] == 0


def test_land_refuses_a_store_conflict_and_dedupes_identical(ws, tmp_path):
    inbox = str(tmp_path / "in2")
    k = f"facility_id={ACTIVE}/year=2024/part-a.parquet"  # no sha16: no hash check
    _put(inbox, k, b"new")
    _put(ws.store_root, k, b"old")
    rep = land(ws, inbox)
    assert rep["objects"][0]["to"] == "inbox" and "different content" in rep["objects"][0]["detail"]
    k2 = _key(ACTIVE, b"same")
    _put(inbox, k2, b"same")
    _put(ws.store_root, k2, b"same")
    rep = land(ws, inbox, apply=True, reason="t")
    row = next(r for r in rep["objects"] if r["key"] == k2)
    assert row["result"] == "deduplicated" and not os.path.exists(os.path.join(inbox, k2))


def test_reconcile_apply_quarantines_late_store_uploads(ws, tmp_path):
    ws.add_facility("Arch", facility_id="arch-ive-222222", reason="t", activate=True)
    # a stubbed state the base lifecycle does not implement yet
    ws.registry._update(
        "arch-ive-222222", {"state": "archived", "state_changed_at": "2000-01-01T00:00:00Z"}
    )
    late = _key("arch-ive-222222", b"late")
    _put(ws.store_root, late, b"late")
    _put(ws.store_root, _key(ACTIVE, b"a"), b"a")
    with pytest.raises(ValueError, match="reason"):
        quarantine_reconciled(ws, reason=" ")
    rep = quarantine_reconciled(ws, reason="archived facility kept uploading")
    assert rep["quarantined"] == 1 and not rep["read_only"]
    assert not os.path.exists(os.path.join(ws.store_root, *late.split("/")))
    assert list_quarantine(ws)[0]["state"] == "archived"
    assert os.path.exists(os.path.join(ws.store_root, *_key(ACTIVE, b"a").split("/")))
    assert len(_audit(ws, "edge.reconcile.quarantine")) == 2


def test_release_needs_an_accepting_facility(ws, inbox):
    land(ws, inbox, apply=True, reason="t")
    rep = release(ws, facility_id=SUSP)
    assert rep["dry_run"] and rep["planned"] == [] and "resume" in rep["refused"][0]["why"]
    ws.transition(SUSP, "resume", reason="contract renewed")
    rep = release(ws, facility_id=SUSP)
    assert rep["planned"] == [_key(SUSP, b"s")] and not rep["applied"]
    assert list_quarantine(ws, facility_id=SUSP)  # dry run moved nothing
    rep = release(ws, facility_id=SUSP, apply=True, reason="renewed; keep the gap data")
    assert rep["applied"]
    assert os.path.exists(os.path.join(ws.store_root, *_key(SUSP, b"s").split("/")))
    assert not list_quarantine(ws, facility_id=SUSP)
    assert len(_audit(ws, "edge.quarantine.release")) == 2
    # hash-mismatch objects can only be discarded; unknown keys are an error
    bad = f"facility_id={ACTIVE}/year=2024/part-0000000000000000.parquet"
    assert "hash" in release(ws, keys=[bad])["refused"][0]["why"]
    with pytest.raises(KeyError, match="not in quarantine"):
        release(ws, keys=["facility_id=x-1/year=2024/part-a.parquet"])
    with pytest.raises(ValueError, match="name what"):
        release(ws)
    with pytest.raises(KeyError, match="nothing in quarantine"):
        release(ws, facility_id=SUSP)


def test_discard_is_guarded_and_honours_legal_holds(ws, inbox):
    land(ws, inbox, apply=True, reason="t")
    k = _key(GHOST, b"g")
    with pytest.raises(ValueError, match="--yes"):
        discard(ws, facility_id=GHOST, apply=True, reason="junk")
    with pytest.raises(ValueError, match="--yes"):
        discard(ws, facility_id=GHOST, apply=True, reason="junk", confirm="wrong-id")
    with pytest.raises(ValueError, match="reason"):
        discard(ws, facility_id=GHOST, apply=True, yes=True)
    dry = discard(ws, facility_id=GHOST)
    assert dry["planned"] == [k] and dry["bytes"] == 1 and list_quarantine(ws, facility_id=GHOST)
    rep = discard(ws, facility_id=GHOST, apply=True, reason="never ours", confirm=GHOST)
    assert rep["applied"] and not list_quarantine(ws, facility_id=GHOST)
    begin = [a for a in _audit(ws, "edge.quarantine.discard") if a["details"]["phase"] == "begin"]
    assert begin[0]["details"]["keys"] == [k] and begin[0]["reason"] == "never ours"
    # a legal hold refuses the discard
    doc_path = os.path.join(ws.root, "_portfolio.json")
    doc = json.load(open(doc_path))
    doc["legal_holds"] = {SUSP: {"since": "2025-01-01", "reason": "litigation"}}
    json.dump(doc, open(doc_path, "w"))
    rep = discard(ws, facility_id=SUSP, apply=True, yes=True, reason="cleanup")
    assert rep["planned"] == [] and "legal hold" in rep["refused"][0]["why"]
    assert list_quarantine(ws, facility_id=SUSP)


def test_a_crash_mid_quarantine_is_recoverable(ws, inbox, monkeypatch):
    k = _key(SUSP, b"s")

    def boom(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(qmod, "_move", boom)
    with pytest.raises(OSError):
        land(ws, inbox, apply=True, reason="t")
    monkeypatch.undo()
    # the record was written, the object never left the inbox: nothing is lost
    rows = {r["key"]: r for r in list_quarantine(ws)}
    assert os.path.exists(os.path.join(inbox, *k.split("/")))
    assert any(r["status"] == "incomplete" for r in rows.values())
    # re-running finishes the job
    land(ws, inbox, apply=True, reason="t (rerun)")
    assert {r["status"] for r in list_quarantine(ws)} == {"held"}
    assert not os.path.exists(os.path.join(inbox, *k.split("/")))


def test_a_crash_mid_discard_is_recoverable(ws, inbox, monkeypatch):
    land(ws, inbox, apply=True, reason="t")
    k = _key(GHOST, b"g")
    obj = os.path.join(quarantine_root(ws), *k.split("/"))
    real_remove = os.remove

    def remove_object_then_crash(p):
        real_remove(p)
        if p.endswith(".parquet"):
            raise OSError("crash after deleting the object")

    monkeypatch.setattr(qmod.os, "remove", remove_object_then_crash)
    with pytest.raises(OSError):
        discard(ws, facility_id=GHOST, apply=True, yes=True, reason="junk")
    monkeypatch.undo()
    assert not os.path.exists(obj) and os.path.exists(obj + RECORD_SUFFIX)
    assert list_quarantine(ws, facility_id=GHOST)[0]["status"] == "incomplete"
    discard(ws, facility_id=GHOST, apply=True, yes=True, reason="junk (rerun)")
    assert not list_quarantine(ws, facility_id=GHOST)


def test_cross_filesystem_move_and_conflict(tmp_path, monkeypatch):
    src = _put(str(tmp_path), "a/obj", b"x")
    dst = str(tmp_path / "b" / "obj")
    real = os.replace

    def exdev_once(s, d):
        if s == src:
            raise OSError(18, "Invalid cross-device link")
        return real(s, d)

    monkeypatch.setattr(qmod.os, "replace", exdev_once)
    assert qmod._move(src, dst) == "moved" and open(dst, "rb").read() == b"x"
    assert not os.path.exists(src)
    src2 = _put(str(tmp_path), "a/obj", b"y")
    with pytest.raises(FileExistsError):
        qmod._move(src2, dst)
    assert os.path.exists(src2)


def test_list_shows_unrecorded_and_unreadable(ws):
    root = quarantine_root(ws)
    k = _key(SUSP, b"u")
    _put(root, k, b"u")
    _put(root, _key(SUSP, b"v") + RECORD_SUFFIX, b"{not json")
    _put(root, _key(SUSP, b"w") + ".part", b"partial")
    rows = {r["key"]: r for r in list_quarantine(ws)}
    assert rows[k]["status"] == "unrecorded"
    assert rows[_key(SUSP, b"v")]["status"] == "incomplete"
    assert len(rows) == 2


def test_cli_quarantine_flow(ws, inbox, capsys, monkeypatch):
    monkeypatch.setenv("CAMBER_PORTFOLIO", ws.root)
    assert main(["edge", "land", inbox]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "1 to the store, 3 to quarantine, 2 left" in out
    assert main(["edge", "land", inbox, "--apply"]) == 1
    assert "reason" in capsys.readouterr().err
    assert main(["edge", "land", inbox, "--apply", "--reason", "nightly", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"]
    assert main(["edge", "quarantine", "list"]) == 0
    out = capsys.readouterr().out
    assert "3 object(s) in quarantine" in out and "inactive" in out
    assert main(["edge", "quarantine", "list", "--facility", SUSP, "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert main(["edge", "quarantine", "release", "--facility", SUSP]) == 1
    assert "refused" in capsys.readouterr().out
    assert main(["edge", "quarantine", "discard", "--facility", GHOST]) == 0
    assert "would be discarded (dry run" in capsys.readouterr().out
    rc = main(["edge", "quarantine", "discard", "--facility", GHOST, "--apply", "--reason", "junk"])
    assert rc == 1 and "--yes" in capsys.readouterr().err
    rc = main(
        ["edge", "quarantine", "discard", "--facility", GHOST, "--apply", "--reason", "junk",
         "--confirm", GHOST]
    )  # fmt: skip
    assert rc == 0 and "1 object(s) discarded" in capsys.readouterr().out
    ws.transition(SUSP, "resume", reason="back")
    rc = main(
        ["edge", "quarantine", "release", "--key", "_quarantine/" + _key(SUSP, b"s"),
         "--apply", "--reason", "back", "--json"]
    )  # fmt: skip
    assert rc == 0 and json.loads(capsys.readouterr().out)["applied"]
    assert main(["edge", "quarantine", "discard", "--key", "facility_id=no-pe-1/year=2024/x"]) == 1
    assert "not in quarantine" in capsys.readouterr().err
    assert main(["edge", "quarantine", "list"]) == 0
    assert "1 object(s) in quarantine" in capsys.readouterr().out


def test_cli_reconcile_apply(ws, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CAMBER_PORTFOLIO", ws.root)
    landing = str(tmp_path / "landing")
    _put(landing, _key(SUSP, b"s"), b"s")
    rc = main(["edge", "reconcile", "--landing", landing])
    assert rc == 0 and "--apply --reason R" in capsys.readouterr().out
    rc = main(["edge", "reconcile", "--landing", landing, "--apply", "--reason", "late upload"])
    assert rc == 0 and "quarantined 1 object(s)" in capsys.readouterr().out
    keys = tmp_path / "keys.txt"
    keys.write_text(_key(SUSP, b"s") + "\n")
    assert main(["edge", "reconcile", "--keys", str(keys), "--apply", "--reason", "x"]) == 1
    assert "never moves cloud objects" in capsys.readouterr().err
    assert main(["edge", "quarantine", "list"]) == 0
    capsys.readouterr()
    assert main(["edge", "quarantine", "list", "--facility", "nobody-1"]) == 0
    assert "empty" in capsys.readouterr().out
