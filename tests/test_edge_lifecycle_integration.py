"""The edge landing wired to the real portfolio lifecycle (0.95 integration, #18 steps 3-5).

The edge branch was built against a stubbed registry and the state *names*; these tests run it
against the real ``Portfolio`` API: public audit / device-note methods, month keys, the real
offboard / archive / purge transitions, retention on edge-landed objects, recovery with the
spool lock, and M&V billing baselines through export and restore bundles.
"""

import ast
import hashlib
import io
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.portfolio import Portfolio  # noqa: E402

A, B = "north-annex-1a2b3c", "east-wing-4d5e6f"
EDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "camber", "edge"
)


def _part(start: str, hours: int, value: float = 1.0) -> bytes:
    """A Parquet part as the edge forwarder serializes it (the long shape, no partitions)."""
    df = pd.DataFrame(
        {
            "ts": pd.date_range(start, periods=hours, freq="h"),
            "equip": "AHU_1",
            "equip_class": "ahu",
            "role": "supply_air_temp",
            "value": float(value),
        }
    )
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf)
    return buf.getvalue()


def _edge_key(fid: str, payload: bytes, year: int, month=None) -> str:
    """The forwarder's key: year=/month= since 0.95; ``month=None`` is a pre-0.95 year-only key."""
    part = f"part-{hashlib.sha256(payload).hexdigest()[:16]}.parquet"
    mid = f"year={year}/" + (f"month={month}/" if month is not None else "")
    return f"facility_id={fid}/{mid}{part}"


def _put(root, key: str, payload: bytes, mtime=None) -> str:
    path = os.path.join(os.fspath(root), *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def pf(tmp_path):
    p = Portfolio.init(tmp_path / "ws")
    p.add_facility("North Annex", facility_id=A, reason="t", activate=True)
    return p


# ---------------------------------------------------------------- 3. public audit + device note


def test_portfolio_audit_is_public_and_guarded(pf):
    rec = pf.audit("edge.land", reason="inbox sweep", facility_id=A, details={"n": 1})
    assert rec["from_state"] == rec["to_state"] == "active"  # looked up
    assert rec["actor"] and rec["host"] and rec["details"] == {"n": 1}
    assert pf.audit_log(facility_id=A)[-1]["action"] == "edge.land"
    assert pf.audit("edge.land", reason="r", facility_id="nobody-000000")["from_state"] is None
    assert pf.audit("edge.land", reason="r", facility_id=A, state=None)["from_state"] is None
    for bad in ("land", "", " edge.x"):
        with pytest.raises(ValueError):
            pf.audit(bad, reason="r")
    for reserved in ("facility.purge", "portfolio.recover", "retention.apply"):
        with pytest.raises(ValueError, match="namespace"):
            pf.audit(reserved, reason="r")
    with pytest.raises(ValueError, match="reason"):
        pf.audit("edge.land", reason=" ")


def test_note_edge_device_audits_before_the_registry(pf, monkeypatch):
    e = pf.note_edge_device(A, "gw-1", {"state": "retired", "retired_at": "t1"}, reason="swap")
    assert e["edge_devices"] == {"gw-1": {"state": "retired", "retired_at": "t1"}}
    pf.note_edge_device(A, "gw-2", {"state": "active"}, reason="new gateway")
    assert set(pf.facility(A)["edge_devices"]) == {"gw-1", "gw-2"}  # other devices kept
    last = pf.audit_log(facility_id=A)[-1]
    assert last["action"] == "edge.device" and last["details"]["device_id"] == "gw-2"
    with pytest.raises(KeyError):
        pf.note_edge_device("nobody-000000", "gw", {}, reason="r")
    with pytest.raises(ValueError):
        pf.note_edge_device(A, "", {}, reason="r")
    # a crash after the audit line leaves the registry untouched: noting again repairs it
    from camber.store.facilities import FacilityRegistry

    def boom(self, *a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(FacilityRegistry, "_update", boom)
    n = len(pf.audit_log())
    with pytest.raises(OSError):
        pf.note_edge_device(A, "gw-3", {"state": "retired"}, reason="r")
    assert len(pf.audit_log()) == n + 1 and "gw-3" not in pf.facility(A)["edge_devices"]


def test_edge_code_calls_no_private_portfolio_api():
    """No ``registry._update`` / ``portfolio._audit`` / direct audit-log appends in camber.edge."""
    offenders = []
    for f in sorted(os.listdir(EDGE_DIR)):
        if not f.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(EDGE_DIR, f), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("_update", "_audit", "_tombstone"):
                offenders.append(f"{f}:{node.lineno} .{node.attr}")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "portfolio._audit" in node.module
            ):
                names = {a.name for a in node.names}
                if names - {"audit_record"}:  # the device-side receipt shape only
                    offenders.append(f"{f}:{node.lineno} imports {sorted(names)}")
    assert not offenders, offenders


def test_record_retirement_uses_the_public_note(pf):
    from camber.edge.decommission import record_retirement

    receipt = {"facility_id": A, "device_id": "gw-1", "retired_at": "2026-09-01T00:00:00Z"}
    assert record_retirement(pf, receipt, reason="swap")["noted"] == "recorded"
    assert record_retirement(pf, receipt, reason="swap")["noted"] == "already recorded"
    note = pf.facility(A)["edge_devices"]["gw-1"]
    assert note["state"] == "retired" and note["retired_at"] == receipt["retired_at"]
    acts = [r["action"] for r in pf.audit_log(facility_id=A)]
    assert acts.count("edge.decommission") == 1
    other = {**receipt, "facility_id": "gone-aaaaaa"}
    assert record_retirement(pf, other, reason="swap")["noted"].startswith("audit only")


# ---------------------------------------------------------------- 4. month keys, legacy keys kept


def test_land_stores_month_and_legacy_keys_and_the_store_reads_both(pf, tmp_path):
    from camber.edge.landing import parse_landed_key, reconcile
    from camber.edge.quarantine import land

    inbox = tmp_path / "inbox"
    new = _part("2024-03-01", 48, 1.0)
    old = _part("2024-01-01", 24 * 40, 2.0)  # a year-only part spanning Jan and Feb
    k_new, k_old = _edge_key(A, new, 2024, 3), _edge_key(A, old, 2024)
    _put(inbox, k_new, new)
    _put(inbox, k_old, old)
    assert parse_landed_key(k_new)[0].month == 3 and parse_landed_key(k_old)[0].month is None
    rep = land(pf, inbox, apply=True, reason="sweep")
    assert rep["counts"]["store"] == 2
    got = pf.store.read_long(facility_id=A)
    assert len(got) == 48 + 24 * 40
    assert {p["legacy"] for p in pf.store.partitions(facility_id=A)} == {True, False}
    assert reconcile(pf)["counts"]["ok"] == 2  # both layouts reconcile as ok

    # migrate-partitions converts the edge's legacy part and leaves its month keys alone
    pf.store.migrate_partitions(apply=True, reason="0.95 layout")
    parts = pf.store.partitions(facility_id=A)
    assert not any(p["legacy"] for p in parts) and {p["month"] for p in parts} == {1, 2, 3}
    assert os.path.isfile(os.path.join(pf.store_root, *k_new.split("/")))
    assert len(pf.store.read_long(facility_id=A)) == 48 + 24 * 40
    assert reconcile(pf)["counts"]["ok"] == 3  # the migrated legacy file is now two month parts


def test_a_legacy_part_resent_after_migration_is_a_quarantined_duplicate(pf, tmp_path):
    from camber.edge.landing import reconcile
    from camber.edge.quarantine import discard, land, quarantine_reconciled, release

    old = _part("2024-01-01", 24 * 40, 2.0)
    k_old = _edge_key(A, old, 2024)
    _put(pf.store_root, k_old, old)  # landed straight into the store by an older forwarder
    pf.store.migrate_partitions(apply=True, reason="0.95 layout")
    rows = len(pf.store.read_long(facility_id=A))
    assert rows == 24 * 40

    # the old forwarder re-sends it (its ack was lost): through an inbox ...
    inbox = tmp_path / "inbox"
    _put(inbox, k_old, old)
    rep = land(pf, inbox, apply=True, reason="sweep")
    assert [r["category"] for r in rep["objects"]] == ["duplicate"]
    assert rep["counts"] == {"store": 0, "quarantine": 1, "inbox": 0}
    assert len(pf.store.read_long(facility_id=A)) == rows  # not counted twice
    refused = release(pf, facility_id=A)["refused"]
    assert refused and "already holds these rows" in refused[0]["why"]

    # ... or straight into the store: reconciliation flags it and --apply moves it out
    _put(pf.store_root, k_old, old)
    assert len(pf.store.read_long(facility_id=A)) == 2 * rows
    rec = reconcile(pf)
    assert rec["counts"]["duplicate"] == 1 and rec["to_quarantine"] == 1
    discard(pf, facility_id=A, apply=True, yes=True, reason="duplicate of migrated rows")
    quarantine_reconciled(pf, reason="dedupe")
    assert len(pf.store.read_long(facility_id=A)) == rows
    # a different part under a legacy key is new data, not a duplicate
    other = _part("2024-02-10", 5, 3.0)
    _put(inbox, _edge_key(A, other, 2024), other)
    assert land(pf, inbox)["objects"][0]["to"] == "store"


# ---------------------------------------------------------------- 5. the real lifecycle states


def test_offboard_archive_purge_end_to_end_with_the_edge_landing(pf, tmp_path):
    """No stub registry: offboard, archive and purge a facility and land uploads at each step."""
    import time

    from camber.edge.landing import reconcile
    from camber.edge.quarantine import land, list_quarantine, quarantine_reconciled, release

    past, later = time.time() - 3600, time.time() + 120
    history = {}
    for month in (3, 4):  # landed while active: the facility's history
        p = _part(f"2024-{month:02d}-01", 24)
        history[_edge_key(A, p, 2024, month)] = p
        _put(pf.store_root, _edge_key(A, p, 2024, month), p, mtime=past)
    rows = len(pf.store.read_long(facility_id=A))
    assert rows == 48

    # -- offboard: late uploads are quarantined, the history is left alone
    pf.offboard(A, reason="contract ended", apply=True)
    assert pf.facility(A)["state"] == "offboarding"
    late = _part("2024-05-01", 24, 9.0)
    k_late = _edge_key(A, late, 2024, 5)
    _put(tmp_path / "in1", k_late, late)
    rep = land(pf, tmp_path / "in1", apply=True, reason="sweep")
    assert [(r["category"], r["state"], r["to"]) for r in rep["objects"]] == [
        ("inactive", "offboarding", "quarantine")
    ]
    late2 = _part("2024-05-02", 24, 9.0)
    k_late2 = _edge_key(A, late2, 2024, 5)
    _put(pf.store_root, k_late2, late2, mtime=later)  # PUT straight into a store-as-bucket
    rec = reconcile(pf)
    actions = {r["key"]: r["action"] for r in rec["objects"]}
    assert actions[k_late2] == "quarantine"
    assert all(actions[k] is None for k in history)  # history: reported, never moved
    assert all("history" in r["detail"] for r in rec["objects"] if r["key"] in history)
    quarantine_reconciled(pf, reason="late uploads")
    assert len(pf.store.read_long(facility_id=A)) == rows
    assert all(os.path.isfile(os.path.join(pf.store_root, *k.split("/"))) for k in history)
    assert {r["key"] for r in list_quarantine(pf, facility_id=A)} == {k_late, k_late2}
    refused = release(pf, facility_id=A)["refused"]
    assert len(refused) == 2 and all("offboarding" in r["why"] for r in refused)

    # -- archive: the hot data goes (quarantine kept); reconcile and land still report correctly
    pf.archive(A, reason="grace over", apply=True, skip_grace=True)
    assert pf.facility(A)["state"] == "archived"
    assert pf.store.partitions(facility_id=A) == []
    rec = reconcile(pf)
    assert rec["objects_scanned"] == 0 and A not in rec["facilities"]
    again = _part("2024-06-01", 24, 7.0)
    k_again = _edge_key(A, again, 2024, 6)
    _put(tmp_path / "in2", k_again, again)
    rec = reconcile(pf, landing=tmp_path / "in2")
    assert rec["facilities"][A] == {"state": "archived", "inactive": 1}
    assert rec["objects"][0]["action"] == "quarantine"
    assert land(pf, tmp_path / "in2", apply=True, reason="sweep")["counts"]["quarantine"] == 1
    # the archived store refuses writes, even once an object PUT into the store recreates the
    # facility's partition (the guard is checked before its partition fast path)
    long = pd.read_parquet(io.BytesIO(again))
    with pytest.raises(ValueError, match="archived"):
        pf.store.write_long(long, facility_id=A)
    _put(pf.store_root, k_again, again, mtime=later)
    with pytest.raises(ValueError, match="archived"):
        pf.store.write_long(long, facility_id=A)
    assert reconcile(pf)["to_quarantine"] == 1
    quarantine_reconciled(pf, reason="late upload")
    assert len(list_quarantine(pf, facility_id=A)) == 3

    # -- purge: the tombstone stays, the quarantined uploads go with everything else
    plan = pf.purge(A, reason="retention period over")
    assert os.path.join(pf.root, "quarantine", f"facility_id={A}") in plan["deletes"]
    pf.purge(A, reason="retention period over", apply=True, confirm=A)
    assert A in pf.registry.tombstones()
    assert list_quarantine(pf, facility_id=A) == []
    ghost = _part("2024-07-01", 24, 5.0)
    k_ghost = _edge_key(A, ghost, 2024, 7)
    _put(tmp_path / "in3", k_ghost, ghost)
    row = reconcile(pf, landing=tmp_path / "in3")["objects"][0]
    assert row["category"] == "unknown_facility" and "tombstoned" in row["detail"]
    rep = land(pf, tmp_path / "in3", apply=True, reason="sweep")
    assert rep["objects"][0]["to"] == "quarantine"
    assert [r["key"] for r in list_quarantine(pf, facility_id=A)] == [k_ghost]
    with pytest.raises(ValueError):
        pf.store.write_long(long, facility_id=A)
    from camber.edge.landing import route_key

    assert route_key(pf, k_ghost) == "_quarantine/" + k_ghost


def test_the_quarantine_dir_is_the_one_purge_deletes():
    from camber.edge.quarantine import QUARANTINE_DIR
    from camber.portfolio import _cascade

    assert _cascade.QUARANTINE_DIR == QUARANTINE_DIR


def test_a_crash_mid_purge_of_the_quarantine_is_finished_by_recover(pf, tmp_path, monkeypatch):
    from camber.edge.quarantine import land, list_quarantine
    from camber.store import _swap

    pf.offboard(A, reason="r", apply=True)
    p = _part("2024-05-01", 24)
    _put(tmp_path / "in", _edge_key(A, p, 2024, 5), p)
    land(pf, tmp_path / "in", apply=True, reason="sweep")
    pf.archive(A, reason="r", apply=True, skip_grace=True)
    real = _swap._rm
    qtree = os.path.join(pf.root, "quarantine")

    def crash(path):
        if os.path.dirname(path) == qtree:
            raise KeyboardInterrupt("power cut while removing the quarantine tree")
        return real(path)

    monkeypatch.setattr(_swap, "_rm", crash)
    with pytest.raises(KeyboardInterrupt):
        pf.purge(A, reason="r", apply=True, confirm=A)
    monkeypatch.setattr(_swap, "_rm", real)
    assert list_quarantine(pf) == []  # the _trash-* leftover is not listed as an upload
    assert any(n.startswith("_trash-") for n in os.listdir(qtree))
    done = pf.recover()
    assert not os.listdir(qtree) and any(d["action"] == "purge_finished" for d in done)


def test_edge_state_names_are_the_lifecycles_states():
    from camber.edge.landing import ACCEPTING_STATES, NON_ACCEPTING_STATES
    from camber.portfolio import STATES

    assert set(ACCEPTING_STATES) | set(NON_ACCEPTING_STATES) == set(STATES)
    assert not set(ACCEPTING_STATES) & set(NON_ACCEPTING_STATES)


# ---------------------------------------------------------------- retention on edge-landed objects


def _rollup_n(pf, freq, fid, year, month) -> int:
    """The raw-row count a rollup month partition stands for (the sum of its ``n``)."""
    import pyarrow.dataset as pads

    path = os.path.join(
        pf.root, "rollups", freq, f"facility_id={fid}", f"year={year}", f"month={month}"
    )
    if not os.path.isdir(path):
        return 0
    return int(sum(pads.dataset(path, format="parquet").to_table().column("n").to_pylist()))


def test_retention_rolls_up_verifies_and_prunes_edge_month_keys(pf, tmp_path):
    from camber.edge.landing import reconcile
    from camber.edge.quarantine import land

    inbox = tmp_path / "in"
    march, april = _part("2024-03-01", 24 * 3, 1.0), _part("2024-04-01", 24 * 2, 3.0)
    legacy = _part("2023-06-01", 24, 2.0)  # an older forwarder's year-only part
    for p, y, m in ((march, 2024, 3), (april, 2024, 4), (legacy, 2023, None)):
        _put(inbox, _edge_key(A, p, y, m), p)
    land(pf, inbox, apply=True, reason="sweep")
    raw = pf.apply_retention(now="2026-09-01")["facilities"][A]["raw"]  # the dry-run plan
    assert {(r["year"], r["month"], r["legacy"]) for r in raw} == {
        (2023, None, True),
        (2024, 3, False),
        (2024, 4, False),
    }
    pf.apply_retention(apply=True, reason="monthly", now="2026-09-01")
    assert pf.store.partitions(facility_id=A) == []  # raw pruned, only after the rollups verified
    assert _rollup_n(pf, "hourly", A, 2024, 3) == 72 and _rollup_n(pf, "daily", A, 2024, 3) == 72
    assert _rollup_n(pf, "hourly", A, 2024, 4) == 48 and _rollup_n(pf, "hourly", A, 2023, 6) == 24
    assert reconcile(pf)["objects_scanned"] == 0
    assert pf.audit_log(facility_id=A)[-1]["action"] == "retention.apply"


def test_a_late_edge_part_in_a_rolled_up_month_adds_to_its_rollup(pf, tmp_path):
    """A backlog uploaded after its month was rolled up and pruned must not replace the rollup."""
    from camber.edge.quarantine import land

    pf.set_retention_override(A, "raw_trends", {"keep_months": 1}, reason="small site")
    first = _part("2026-06-01", 24 * 10, 1.0)
    _put(tmp_path / "a", _edge_key(A, first, 2026, 6), first)
    land(pf, tmp_path / "a", apply=True, reason="sweep")
    pf.apply_retention(apply=True, reason="monthly", now="2026-08-15")
    assert _rollup_n(pf, "hourly", A, 2026, 6) == 240 and not pf.store.partitions(facility_id=A)

    late = _part("2026-06-20", 24 * 2, 5.0)  # a device back online after weeks
    _put(tmp_path / "b", _edge_key(A, late, 2026, 6), late)
    land(pf, tmp_path / "b", apply=True, reason="sweep")
    pf.apply_retention(apply=True, reason="monthly", now="2026-08-16")
    assert _rollup_n(pf, "hourly", A, 2026, 6) == 240 + 48
    assert _rollup_n(pf, "daily", A, 2026, 6) == 240 + 48
    assert not pf.store.partitions(facility_id=A)
    # idempotent: another run changes nothing
    pf.apply_retention(apply=True, reason="monthly", now="2026-08-17")
    assert _rollup_n(pf, "hourly", A, 2026, 6) == 288


def test_a_crash_before_the_prune_then_a_late_part_never_double_counts(pf, tmp_path, monkeypatch):
    from camber.edge.quarantine import land
    from camber.store import ParquetStore

    pf.set_retention_override(A, "raw_trends", {"keep_months": 1}, reason="small site")
    first = _part("2026-06-01", 24 * 10, 1.0)
    _put(tmp_path / "a", _edge_key(A, first, 2026, 6), first)
    land(pf, tmp_path / "a", apply=True, reason="sweep")
    real = ParquetStore.drop_partition

    def crash(self, *a, **k):
        raise KeyboardInterrupt("power cut after the rollup, before the prune")

    monkeypatch.setattr(ParquetStore, "drop_partition", crash)
    with pytest.raises(KeyboardInterrupt):
        pf.apply_retention(apply=True, reason="monthly", now="2026-08-15")
    monkeypatch.setattr(ParquetStore, "drop_partition", real)
    assert _rollup_n(pf, "hourly", A, 2026, 6) == 240  # rolled up, raw still there
    late = _part("2026-06-20", 24 * 2, 5.0)  # lands before the re-run
    _put(tmp_path / "b", _edge_key(A, late, 2026, 6), late)
    land(pf, tmp_path / "b", apply=True, reason="sweep")
    r = pf.apply_retention(apply=True, reason="monthly", now="2026-08-16")
    assert r["problems"] == []
    assert _rollup_n(pf, "hourly", A, 2026, 6) == 288  # the first rollup replaced, not added to
    assert _rollup_n(pf, "daily", A, 2026, 6) == 288
    assert not pf.store.partitions(facility_id=A)
