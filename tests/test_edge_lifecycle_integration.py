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
