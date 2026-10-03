"""Portfolio lifecycle, step 3: offboard, archive, restore and purge with export bundles.

The load-bearing claims: nothing is deleted without a verified bundle that matches the data, a
restore round-trips byte for byte (store, faults, drift and M&V billing baselines, the weather
audit log), a legal hold blocks every deleting step, a purged id is never reused, and a crash at
any point leaves a state that the next command finishes or rolls back.
"""

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import textwrap

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import LifecycleError, Portfolio  # noqa: E402
from camber.portfolio import _bundle as _bundle  # noqa: E402
from camber.portfolio import _cascade as _cascade  # noqa: E402
from camber.store import _swap as _swap  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LATER = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)


def _frame(start="2025-01-01", days=40):
    idx = pd.date_range(start, periods=24 * days, freq="h")
    return pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: np.linspace(55, 60, len(idx)), Role.OAT: 70.0}, index=idx
    )


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _tree_shas(base):
    out = {}
    for dirpath, _d, names in os.walk(base):
        for n in names:
            p = os.path.join(dirpath, n)
            out[os.path.relpath(p, base)] = _sha(p)
    return out


def _setup(tmp_path, name="North Campus"):
    """A workspace with one active facility: trend data, a rollup partition, faults, drift and
    M&V (billing) baselines, the weather audit log, a report inside and one outside state/."""
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility(name, reason="contract", activate=True)["facility_id"]
    pf.store.write_role_frame(_frame(), facility_id=fid, equip="AHU_1", equip_class="ahu")
    roll = os.path.join(pf.root, "rollups", "daily", f"facility_id={fid}", "year=2025", "month=1")
    os.makedirs(roll)
    with open(os.path.join(roll, "part-0.parquet"), "wb") as fh:
        fh.write(b"rollup-bytes")
    sdir = pf.state_dir(fid)
    os.makedirs(os.path.join(sdir, "reports"))
    files = {
        "faults.json": {"faults": [{"fingerprint": "f1", "facility_id": fid, "status": "open"}]},
        "baselines.json": {"baselines": [{"fingerprint": "b1", "kind": "pump_flow"}]},
        "mv_baselines.json": {"schema": 1, "mv_baselines": [{"kind": "mv_bills", "equip": "gas"}]},
    }
    for n, doc in files.items():
        with open(os.path.join(sdir, n), "w") as fh:
            json.dump(doc, fh)
    with open(os.path.join(sdir, "weather_audit.ndjson"), "w") as fh:
        fh.write(json.dumps({"ts": "2025-01-02T00:00:00Z", "service": "isd", "sent": True}) + "\n")
    with open(os.path.join(sdir, "reports", "r1.html"), "w") as fh:
        fh.write("<html>inside</html>")
    ext = tmp_path / "out" / "report.html"
    ext.parent.mkdir()
    ext.write_text("<html>outside</html>")
    shared = tmp_path / "out" / "shared_faults.json"
    shared.write_text('{"faults": []}')
    from camber.portfolio._state import record_outputs

    record_outputs(pf.root, fid, {str(ext): "report", str(shared): "faults"})
    return pf, fid, str(ext), str(shared)


# --------------------------------------------------------------------------- the happy path


def test_offboard_archive_restore_round_trip(tmp_path):
    pf, fid, ext, shared = _setup(tmp_path)
    before_rows = pf.store.read_long(facility_id=fid)
    before_state = _tree_shas(pf.state_dir(fid))

    plan = pf.offboard(fid, reason="contract ended")
    assert plan["dry_run"] and plan["to_state"] == "offboarding"
    assert plan["export"]["store_rows"] == len(before_rows)
    assert pf.bundles(fid) == [] and pf.registry.state(fid) == "active"  # a dry run changes nothing

    r = pf.offboard(fid, reason="contract ended", apply=True)
    assert pf.registry.state(fid) == "offboarding"
    b = pf.bundles(fid, verify=True)
    assert len(b) == 1 and b[0]["kind"] == "offboard" and b[0]["verify"]["ok"]
    man = _bundle.read_bundle_manifest(b[0]["path"])
    rels = set(man["files"])
    for n in ("faults.json", "baselines.json", "mv_baselines.json", "weather_audit.ndjson"):
        assert f"state/{n}" in rels
    assert "state/reports/r1.html" in rels and "registry.json" in rels and "audit.ndjson" in rels
    assert any(r.startswith(f"rollups/daily/facility_id={fid}/") for r in rels)
    assert {e["path"] for e in man["external"]} == {ext, shared}
    assert man["counts"]["store_rows"] == len(before_rows)
    reg = json.load(open(os.path.join(b[0]["path"], "registry.json")))
    assert reg["entry"]["name"] == "North Campus" and reg["catalog"]["equipment"] == {
        "AHU_1": "ahu"
    }
    until = dt.datetime.fromisoformat(r["grace_until"].replace("Z", "+00:00"))
    assert dt.timedelta(days=29) < until - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(days=30)
    assert pf.registry.get(fid)["offboarding"]["bundle"] == r["bundle"]
    # analyses skip it, but it is still readable and restorable during the grace period
    assert fid not in pf.store.active_facilities()

    with pytest.raises(LifecycleError, match="grace period until"):
        pf.archive(fid, reason="done", apply=True)
    plan = pf.archive(fid, reason="done", now=_LATER)
    assert plan["bundle"] == b[0]["bundle_id"] and not plan["new_bundle"]
    assert plan["deletes_external"] == [ext]  # the report; the shared fault store stays
    assert [k["path"] for k in plan["kept_external"]] == [shared]

    r = pf.archive(fid, reason="done", apply=True, now=_LATER)
    assert pf.registry.state(fid) == "archived"
    assert pf.registry.get(fid)["archive"]["hot_deleted"] is True
    assert fid not in pf.store.facilities() and not os.path.exists(pf.state_dir(fid))
    assert not os.path.exists(os.path.join(pf.root, "rollups", "daily", f"facility_id={fid}"))
    assert not os.path.exists(ext) and os.path.exists(shared)
    with pytest.raises(ValueError, match="is archived"):
        pf.store.write_role_frame(_frame(), facility_id=fid, equip="AHU_2")

    r = pf.restore(fid, reason="client came back", apply=True)
    assert r["restored"]["verified"] and pf.registry.state(fid) == "active"
    after = pf.store.read_long(facility_id=fid)
    pd.testing.assert_frame_equal(after, before_rows)
    assert _tree_shas(pf.state_dir(fid)) == before_state
    assert open(ext).read() == "<html>outside</html>"
    e = pf.registry.get(fid)
    assert "archive" not in e and "offboarding" not in e
    acts = [x["action"] for x in pf.audit_log(facility_id=fid)]
    assert acts[-3:] == ["facility.offboard", "facility.archive", "facility.restore"]
    assert all(x["actor"] and x["reason"] for x in pf.audit_log(facility_id=fid))


def test_restore_from_offboarding_copies_nothing(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    r = pf.restore(fid, reason="changed our mind", apply=True)
    assert r["bundle"] is None and r["restored"] is None and pf.registry.state(fid) == "active"
    assert len(pf.bundles(fid)) == 1  # the offboard bundle is kept as history


def test_archive_reexports_when_data_changed_during_grace(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    pf.store.write_role_frame(_frame("2025-03-01", 2), facility_id=fid, equip="AHU_1")
    rows = len(pf.store.read_long(facility_id=fid))
    plan = pf.archive(fid, reason="done", now=_LATER)
    assert plan["new_bundle"] and plan["bundle"] is None
    r = pf.archive(fid, reason="done", apply=True, now=_LATER)
    assert r["new_bundle"] and r["bundle"].endswith("-archive")
    assert [b["kind"] for b in pf.bundles(fid)] == ["offboard", "archive"]
    pf.restore(fid, reason="back", apply=True)
    assert len(pf.store.read_long(facility_id=fid)) == rows  # the late rows came back too


def test_skip_grace_is_audited(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    r = pf.archive(fid, reason="client asked for deletion now", apply=True, skip_grace=True)
    assert r["skip_grace"] is True
    assert pf.audit_log(facility_id=fid)[-1]["details"]["skip_grace"] is True


# --------------------------------------------------------------------------- holds, purge, ids


def _hold(pf, fid):
    p = os.path.join(pf.root, "_portfolio.json")
    doc = json.load(open(p))
    doc["legal_holds"] = {fid: {"reason": "litigation"}}
    json.dump(doc, open(p, "w"))


def test_legal_hold_blocks_archive_and_purge(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)  # offboarding deletes nothing: allowed under a hold
    _hold(pf, fid)
    with pytest.raises(LifecycleError, match="legal hold"):
        pf.archive(fid, reason="x", apply=True, now=_LATER)
    assert fid in pf.store.facilities()
    with pytest.raises(LifecycleError, match="legal hold"):
        pf.archive(fid, reason="x", now=_LATER)  # the dry run refuses too


def test_purge_needs_typed_id_and_never_reuses_the_id(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    pf.archive(fid, reason="x", apply=True, now=_LATER)
    plan = pf.purge(fid, reason="retention period over")
    assert plan["dry_run"] and len(plan["bundles"]) == 1
    with pytest.raises(LifecycleError, match="typing the facility id"):
        pf.purge(fid, reason="x", apply=True, confirm="yes")
    with pytest.raises(LifecycleError, match="typed facility id"):
        pf.transition(fid, "purge", reason="x")
    assert pf.registry.state(fid) == "archived"
    pf.purge(fid, reason="retention period over", apply=True, confirm=fid)
    assert not os.path.exists(os.path.join(pf.root, "archive", fid))
    assert fid not in pf.registry.all()
    tomb = pf.registry.tombstones()[fid]
    assert tomb["state"] == "purged" and tomb["purged_at"] and "purge_pending" not in tomb
    assert pf.status()["by_state"]["purged"] == 1
    with pytest.raises(ValueError, match="never reused"):
        pf.add_facility("North Campus", reason="new building, same name")
    with pytest.raises(ValueError, match="tombstoned"):
        pf.store.write_role_frame(_frame(), facility_id=fid, equip="AHU_1")
    with pytest.raises(KeyError, match="removed"):
        pf.facility(fid)
    log = pf.audit_log(facility_id=fid)
    assert log[-1]["action"] == "facility.purge" and log[-1]["to_state"] == "purged"


def test_transition_runs_the_cascade(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    r = pf.transition(fid, "offboard", reason="leaving")
    assert r == {"facility_id": fid, "from_state": "active", "to_state": "offboarding"}
    assert len(pf.bundles(fid)) == 1
    assert pf.transition(fid, "restore", reason="back")["to_state"] == "active"


def test_tampered_bundle_is_refused_before_anything_changes(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    pf.archive(fid, reason="x", apply=True, now=_LATER)
    b = pf.bundles(fid)[0]["path"]
    victim = os.path.join(b, "state", "faults.json")
    with open(victim, "a") as fh:
        fh.write(" ")
    open(os.path.join(b, "stray.txt"), "w").write("x")
    v = _bundle.verify_bundle(b)
    assert not v["ok"] and "checksum mismatch: state/faults.json" in v["problems"]
    assert "not in manifest: stray.txt" in v["problems"]
    with pytest.raises(ValueError, match="failed verification"):
        pf.restore(fid, reason="x", apply=True)
    assert pf.registry.state(fid) == "archived" and fid not in pf.store.facilities()
    os.remove(os.path.join(b, "manifest.sha256"))
    assert "unreadable manifest" in _bundle.verify_bundle(b)["problems"][0]


def test_manual_export_changes_nothing_else(tmp_path):
    pf, fid, _e, _s = _setup(tmp_path)
    man = pf.export(fid, reason="annual backup")
    assert man["kind"] == "manual" and pf.registry.state(fid) == "active"
    assert pf.audit_log(facility_id=fid)[-1]["action"] == "facility.export"


# --------------------------------------------------------------------------- crash safety


def test_swap_recovery_rolls_forward_or_back(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "a").write_text("old")
    # incomplete stage (no .ready): rolled back, target untouched
    stage = _swap.staging(str(target))
    open(os.path.join(stage, "a"), "w").write("half")
    assert _swap.recover(str(target)) == "rolled_back"
    assert (target / "a").read_text() == "old" and not os.path.exists(stage)
    # complete stage, crash after the target was moved aside: rolled forward
    stage = _swap.staging(str(target))
    open(os.path.join(stage, "a"), "w").write("new")
    new, old, ready = _swap._paths(str(target))
    open(ready, "w").close()
    os.rename(str(target), old)
    assert not target.exists()
    out = _swap.recover_tree(str(tmp_path))
    assert out == [{"path": str(target), "action": "rolled_forward"}]
    assert (target / "a").read_text() == "new" and not os.path.exists(old)
    # a trash leftover is removed
    os.makedirs(tmp_path / "_trash-x-123" / "deep")
    assert _swap.recover_tree(str(tmp_path)) == [
        {"path": str(tmp_path / "_trash-x-123"), "action": "trash_removed"}
    ]
    assert _swap.recover(str(tmp_path / "nothing")) is None
    assert not _swap.discard(str(tmp_path / "nothing"))


def test_crash_during_archive_deletion_is_finished_by_recovery(tmp_path, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    rows = pf.store.read_long(facility_id=fid)
    pf.offboard(fid, reason="x", apply=True)
    real = _swap.discard
    calls = []

    def boom(path):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt("simulated crash after the first tree")
        return real(path)

    monkeypatch.setattr(_swap, "discard", boom)
    with pytest.raises(KeyboardInterrupt):
        pf.archive(fid, reason="x", apply=True, now=_LATER)
    monkeypatch.setattr(_swap, "discard", real)
    assert pf.registry.state(fid) == "archived"
    assert pf.registry.get(fid)["archive"]["hot_deleted"] is False
    done = pf.recover()
    assert {"facility_id": fid, "action": "archive_finished"} in done
    assert pf.registry.get(fid)["archive"]["hot_deleted"] is True
    assert not os.path.exists(pf.state_dir(fid))
    assert pf.audit_log()[-1]["action"] == "portfolio.recover"
    pf.restore(fid, reason="x", apply=True)
    pd.testing.assert_frame_equal(pf.store.read_long(facility_id=fid), rows)


def test_recovery_refuses_to_delete_when_the_bundle_is_damaged(tmp_path, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    monkeypatch.setattr(_cascade, "_delete_hot", lambda pf, fid: None)  # crash before deletion
    pf.archive(fid, reason="x", apply=True, now=_LATER)
    monkeypatch.undo()
    b = pf.bundles(fid)[0]["path"]
    os.remove(os.path.join(b, "state", "faults.json"))
    done = pf.recover()
    assert {"facility_id": fid, "action": "archive_blocked_bad_bundle"} in done
    assert fid in pf.store.facilities()  # the only good copy was not deleted


def test_crash_during_restore_is_finished_by_rerunning(tmp_path, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    rows = pf.store.read_long(facility_id=fid)
    state = _tree_shas(pf.state_dir(fid))
    pf.offboard(fid, reason="x", apply=True)
    pf.archive(fid, reason="x", apply=True, now=_LATER)
    real = _swap.commit
    n = []

    def boom(target):
        n.append(target)
        if len(n) == 2:  # the store is back, the rollups are staged but not swapped in
            raise KeyboardInterrupt("simulated crash")
        return real(target)

    monkeypatch.setattr(_swap, "commit", boom)
    with pytest.raises(KeyboardInterrupt):
        pf.restore(fid, reason="x", apply=True)
    monkeypatch.setattr(_swap, "commit", real)
    assert pf.registry.state(fid) == "archived"  # the state flips only once everything is back
    pf.restore(fid, reason="x", apply=True)
    pd.testing.assert_frame_equal(pf.store.read_long(facility_id=fid), rows)
    assert _tree_shas(pf.state_dir(fid)) == state


def test_crash_while_staging_a_bundle_leaves_no_bundle(tmp_path, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    real = _bundle._copy_hashed
    n = []

    def boom(src, dst):
        n.append(src)
        if len(n) == 3:
            raise KeyboardInterrupt("simulated crash")
        return real(src, dst)

    monkeypatch.setattr(_bundle, "_copy_hashed", boom)
    with pytest.raises(KeyboardInterrupt):
        pf.offboard(fid, reason="x", apply=True)
    monkeypatch.undo()
    assert pf.bundles(fid) == [] and pf.registry.state(fid) == "active"
    adir = os.path.join(pf.root, "archive", fid)
    assert any(x.startswith("_swap-") for x in os.listdir(adir))
    pf.offboard(fid, reason="x", apply=True)  # the next command cleans up and succeeds
    assert [x for x in os.listdir(adir) if x.startswith("_")] == []
    assert len(pf.bundles(fid, verify=True)) == 1


def test_crash_during_purge_is_finished_by_recovery(tmp_path, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    pf.offboard(fid, reason="x", apply=True)
    pf.archive(fid, reason="x", apply=True, now=_LATER)
    monkeypatch.setattr(_cascade, "_finish_purge", lambda pf, fid: None)
    pf.purge(fid, reason="x", apply=True, confirm=fid)
    monkeypatch.undo()
    assert pf.registry.tombstones()[fid]["purge_pending"] is True
    assert os.path.exists(os.path.join(pf.root, "archive", fid))
    assert {"facility_id": fid, "action": "purge_finished"} in pf.recover()
    assert not os.path.exists(os.path.join(pf.root, "archive", fid))
    assert "purge_pending" not in pf.registry.tombstones()[fid]


def test_killed_process_mid_archive_recovers(tmp_path):
    """A real crash: the child process dies (os._exit) right after moving the store partition to
    the trash, with no chance to clean up. The next command finishes the archive."""
    pf, fid, _e, _s = _setup(tmp_path)
    rows = pf.store.read_long(facility_id=fid)
    pf.offboard(fid, reason="x", apply=True)
    script = textwrap.dedent(
        f"""
        import os, sys, datetime as dt
        sys.path.insert(0, {_REPO!r})
        from camber.store import _swap
        from camber.portfolio import Portfolio
        real = _swap.discard
        def discard(path):
            parent, name = os.path.split(path)
            os.rename(path, os.path.join(parent, "_trash-" + name + "-crash"))
            os._exit(9)
        _swap.discard = discard
        pf = Portfolio({pf.root!r})
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)
        pf.archive({fid!r}, reason="x", apply=True, now=later)
        """
    )
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert p.returncode == 9, p.stderr
    assert pf.registry.state(fid) == "archived" and fid not in pf.store.facilities()
    trash = [x for x in os.listdir(pf.store_root) if x.startswith("_trash-")]
    assert trash  # the crash left a half-deleted tree behind, invisible to readers
    assert pf.store.read_long(facility_id=fid).empty
    done = pf.recover()
    assert any(d.get("action") == "trash_removed" for d in done)
    assert {"facility_id": fid, "action": "archive_finished"} in done
    assert [x for x in os.listdir(pf.store_root) if x.startswith("_trash-")] == []
    pf.restore(fid, reason="x", apply=True)
    pd.testing.assert_frame_equal(pf.store.read_long(facility_id=fid), rows)


# --------------------------------------------------------------------------- CLI


def _cli(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_cascade_confirmation(tmp_path, capsys, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    monkeypatch.setattr(sys, "stdin", open(os.devnull))  # not a terminal: no prompt
    rc, out, _ = _cli(capsys, "facility", "offboard", fid)
    assert rc == 0 and "dry run -- nothing changed" in out and "--yes" in out
    rc, _, err = _cli(capsys, "facility", "offboard", fid, "--apply", "--yes")
    assert rc == 1 and "--apply needs --reason" in err
    rc, _, err = _cli(capsys, "facility", "offboard", fid, "--apply", "--reason", "x")
    assert rc == 1 and "needs confirmation" in err
    rc, _, err = _cli(
        capsys, "facility", "offboard", fid, "--apply", "--reason", "x", "--confirm", "nope"
    )
    assert rc == 1 and "does not match" in err and pf.registry.state(fid) == "active"
    rc, out, _ = _cli(capsys, "facility", "offboard", fid, "--apply", "--reason", "x", "--yes")
    assert rc == 0 and "-> offboarding  (done)" in out and "bundle" in out
    rc, _, err = _cli(capsys, "facility", "archive", fid, "--apply", "--reason", "x", "--yes")
    assert rc == 1 and "grace period" in err
    rc, out, _ = _cli(
        capsys, "facility", "archive", fid, "--apply", "--reason", "x", "--yes", "--skip-grace"
    )
    assert rc == 0 and "skipped (--skip-grace" in out
    rc, out, _ = _cli(capsys, "facility", "show", fid)
    assert rc == 0 and "archived" in out and "bundle" in out
    rc, out, _ = _cli(capsys, "facility", "bundles", fid, "--verify")
    assert rc == 0 and "verified" in out and "1 bundle(s)" in out
    with pytest.raises(SystemExit):  # purge has no --yes at all
        _cli(capsys, "facility", "purge", fid, "--apply", "--reason", "x", "--yes")
    capsys.readouterr()
    rc, _, err = _cli(capsys, "facility", "purge", fid, "--apply", "--reason", "x")
    assert rc == 1 and f"--confirm {fid}" in err and "not enough" in err
    rc, out, _ = _cli(capsys, "facility", "purge", fid)
    assert rc == 0 and "deletes bundle" in out and f"--confirm {fid}" in out
    monkeypatch.setattr(sys, "stdin", _Tty(fid))
    rc, out, _ = _cli(capsys, "facility", "purge", fid, "--apply", "--reason", "done")
    assert rc == 0 and "-> purged  (done)" in out
    rc, out, _ = _cli(capsys, "portfolio", "audit", "--facility", fid)
    assert "facility.purge" in out


class _Tty:
    """A stdin that is a terminal and answers ``input()`` with one line."""

    def __init__(self, answer):
        self.answer = answer

    def isatty(self):
        return True

    def readline(self):
        return self.answer + "\n"


def test_cli_export_and_restore_json(tmp_path, capsys, monkeypatch):
    pf, fid, _e, _s = _setup(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    rc, out, _ = _cli(capsys, "facility", "export", fid, "--reason", "backup")
    assert rc == 0 and "sha256 manifest verified" in out
    rc, out, _ = _cli(capsys, "facility", "offboard", fid, "--json")
    assert rc == 0 and json.loads(out)["dry_run"] is True
    _cli(capsys, "facility", "offboard", fid, "--apply", "--reason", "x", "--yes")
    rc, out, _ = _cli(capsys, "facility", "restore", fid, "--apply", "--reason", "x",
                      "--confirm", fid)  # fmt: skip
    assert rc == 0 and "-> active  (done)" in out
    b = pf.bundles(fid)[0]["path"]
    os.remove(os.path.join(b, "registry.json"))
    rc, out, _ = _cli(capsys, "facility", "bundles", fid, "--verify")
    assert rc == 1 and "FAILED" in out
