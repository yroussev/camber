"""Central reconciliation of landed objects against the registry (camber.edge.landing, 0.95)."""

import calendar
import hashlib
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.edge.landing import (  # noqa: E402
    QUARANTINE_PREFIX,
    facility_status,
    parse_landed_key,
    read_key_listing,
    reconcile,
    route_key,
)
from camber.portfolio import Portfolio  # noqa: E402

ACTIVE = "north-annex-1a2b3c"
SUSP = "east-wing-4d5e6f"
GHOST = "ghost-hall-778899"


def _key(fid, payload=b"x", year=2024, month=None):
    sha = hashlib.sha256(payload).hexdigest()[:16]
    mid = f"year={year}/" + (f"month={month}/" if month else "")
    return f"facility_id={fid}/{mid}part-{sha}.parquet"


def _put(root, key, payload=b"x", mtime=None):
    path = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _force_state(pf, fid, state, changed_at):
    """Stub a lifecycle state this base may not implement yet (offboarding/archived/purged)."""
    pf.registry._update(fid, {"state": state, "state_changed_at": changed_at})


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("North Annex", facility_id=ACTIVE, reason="t", activate=True)
    pf.add_facility("East Wing", facility_id=SUSP, reason="t", activate=True)
    pf.transition(SUSP, "suspend", reason="contract paused")
    return pf


class _StubPortfolio:
    """A stubbed registry: any state name, including ones the base lifecycle lacks."""

    def __init__(self, entries, tombstones=(), store_root=""):
        self._entries = entries
        self.store_root = store_root

        class _Reg:
            def tombstones(_self):
                return {t: {"removed_at": "2025-01-01T00:00:00Z"} for t in tombstones}

        self.registry = _Reg()

    def facilities(self):
        return self._entries


def _e(state, registered=True, changed="2025-01-01T00:00:00Z"):
    return {"state": state, "registered": registered, "state_changed_at": changed}


def test_parse_landed_key():
    lk, why = parse_landed_key(_key(ACTIVE, month=3))
    assert why is None and lk.facility_id == ACTIVE and lk.year == 2024 and lk.month == 3
    assert lk.sha16 and lk.ext == "parquet"
    assert parse_landed_key("facility_id=a-b/year=2024/notes.txt")[0] is None
    assert "invalid facility id" in parse_landed_key("facility_id=Bad Id/year=2024/p.parquet")[1]
    assert "year=" in parse_landed_key(f"facility_id={ACTIVE}/2024/p.parquet")[1]
    assert "month=" in parse_landed_key(f"facility_id={ACTIVE}/year=2024/month=13/p.parquet")[1]
    assert "facility_id=" in parse_landed_key("site=x/year=2024/p.parquet")[1]
    assert parse_landed_key("a/b")[0] is None


@pytest.mark.parametrize(
    "state,status",
    [
        ("active", "ok"),
        ("provisioning", "ok"),
        ("suspended", "inactive"),
        ("offboarding", "inactive"),
        ("archived", "inactive"),
        ("purged", "inactive"),
        ("frozen-by-a-future-release", "inactive"),  # fail closed on a state we do not know
    ],
)
def test_facility_status_by_state_name_with_a_stub_registry(state, status):
    pf = _StubPortfolio({"f-1": _e(state)})
    st = facility_status(pf, "f-1")
    assert st["status"] == status and st["state"] == state
    want = "f-1/" if status == "ok" else QUARANTINE_PREFIX
    assert want in route_key(pf, _key("f-1"))


def test_unknown_tombstoned_and_unregistered_with_a_stub_registry():
    pf = _StubPortfolio({"legacy-1": _e("active", registered=False)}, tombstones=["gone-1"])
    assert facility_status(pf, "nobody-1")["status"] == "unknown_facility"
    gone = facility_status(pf, "gone-1")
    assert gone["status"] == "unknown_facility" and "tombstoned" in gone["detail"]
    assert facility_status(pf, "legacy-1")["status"] == "unregistered"
    assert route_key(pf, _key("legacy-1")).startswith("facility_id=legacy-1/")
    assert route_key(pf, _key("gone-1")).startswith(QUARANTINE_PREFIX)
    with pytest.raises(ValueError, match="not a landed-object key"):
        route_key(pf, "../../etc/passwd")


def test_reconcile_store_is_read_only_and_splits_history_from_late_uploads(ws):
    store = ws.store_root
    changed = ws.facility(SUSP)["state_changed_at"]
    t_change = calendar.timegm(time.strptime(changed, "%Y-%m-%dT%H:%M:%SZ"))
    _put(store, _key(ACTIVE, b"a"))
    _put(store, _key(SUSP, b"old"), b"old", mtime=t_change - 3600)  # history: keep
    late = _key(SUSP, b"late")
    _put(store, late, b"late", mtime=t_change + 3600)  # landed after the suspension
    _put(store, "facility_id=Bad Id/year=2024/part-0000000000000000.parquet")
    _put(store, f"facility_id={ACTIVE}/year=2024/README.txt")
    _put(store, _key("legacy-bldg-aa11bb", b"l"), b"l")  # data, no registry entry
    before = sorted(os.walk(store))
    rep = reconcile(ws)
    assert sorted(os.walk(store)) == before  # nothing moved
    assert rep["read_only"] and rep["source"]["kind"] == "store"
    c = rep["counts"]
    assert c["ok"] == 1 and c["orphaned"] == 2 and c["unregistered"] == 1 and c["inactive"] == 2
    assert rep["to_quarantine"] == 1
    act = [r for r in rep["objects"] if r["action"]]
    assert [r["key"] for r in act] == [late] and "after the state change" in act[0]["detail"]
    hist = [r for r in rep["objects"] if r["category"] == "inactive" and not r["action"]]
    assert "history" in hist[0]["detail"]
    assert rep["facilities"][SUSP]["inactive"] == 2 and rep["facilities"][SUSP]["state"] == (
        "suspended"
    )


def test_reconcile_store_with_stubbed_future_states(ws):
    store = ws.store_root
    for fid, state in (
        ("off-board-111111", "offboarding"),
        ("arch-ive-222222", "archived"),
        ("pur-ged-333333", "purged"),
    ):
        ws.add_facility(fid, facility_id=fid, reason="t", activate=True)
        _force_state(ws, fid, state, "2000-01-01T00:00:00Z")  # long ago: every object is late
        _put(store, _key(fid, fid.encode()), fid.encode())
    rep = reconcile(ws)
    acted = {r["facility_id"]: r for r in rep["objects"] if r["action"] == "quarantine"}
    assert set(acted) == {"off-board-111111", "arch-ive-222222", "pur-ged-333333"}
    assert acted["arch-ive-222222"]["state"] == "archived"
    # an unknown time of state change -> report only
    _force_state(ws, "arch-ive-222222", "archived", None)
    rep = reconcile(ws)
    row = next(r for r in rep["objects"] if r["facility_id"] == "arch-ive-222222")
    assert row["action"] is None and "unknown" in row["detail"]


def test_reconcile_store_with_a_tombstoned_partition(ws):
    fid = "old-site-abcdef"
    ws.add_facility(fid, facility_id=fid, reason="t", activate=True)
    _put(ws.store_root, _key(fid))
    ws.registry.remove(fid, reason="retired")
    rep = reconcile(ws)
    row = next(r for r in rep["objects"] if r["facility_id"] == fid)
    assert row["category"] == "unknown_facility" and row["action"] is None
    assert "tombstoned" in row["detail"]


def test_reconcile_landing_dir_flags_every_non_accepted_upload(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    _put(inbox, _key(ACTIVE, b"a"), b"a")
    _put(inbox, _key(SUSP, b"s"), b"s")
    _put(inbox, _key(GHOST, b"g"), b"g")
    _put(inbox, "stray.bin")
    _put(inbox, "_tmp/upload.part")  # underscore paths are skipped
    rep = reconcile(ws, landing=inbox)
    assert rep["source"]["kind"] == "landing" and rep["objects_scanned"] == 4
    c = rep["counts"]
    assert c["ok"] == 1 and c["inactive"] == 1 and c["unknown_facility"] == 1
    assert c["orphaned"] == 1 and rep["to_quarantine"] == 2
    with pytest.raises(FileNotFoundError):
        reconcile(ws, landing=str(tmp_path / "nope"))
    with pytest.raises(ValueError, match="at most one"):
        reconcile(ws, landing=inbox, keys=inbox)


def test_read_key_listing_formats(tmp_path):
    k = _key(ACTIVE)
    s3 = tmp_path / "s3.json"
    s3.write_text(
        json.dumps(
            {"Contents": [{"Key": f"lake/{k}", "LastModified": "2025-02-01T00:00:00Z", "Size": 5}]}
        )
    )
    rows = read_key_listing(str(s3), prefix="lake")
    assert rows[0][0] == k and rows[0][1].year == 2025 and rows[0][2] == 5
    gcs = tmp_path / "gcs.json"
    gcs.write_text(json.dumps([{"name": k, "updated": "2025-02-01T00:00:00+00:00", "size": "7"}]))
    assert read_key_listing(str(gcs))[0][2] == 7
    az = tmp_path / "az.json"
    az.write_text(
        json.dumps(
            [{"name": k, "properties": {"lastModified": "2025-02-01T00:00:00", "contentLength": 9}}]
        )
    )
    assert read_key_listing(str(az))[0][1].tzinfo is not None
    assert read_key_listing(str(az))[0][2] == 9
    plain = tmp_path / "keys.txt"
    plain.write_text(f"s3://bucket/{k}\n\n2025-02-01 10:00:00       12 {k}\n")
    rows = read_key_listing(str(plain))
    assert rows[0] == (k, None, None) and rows[1][2] == 12
    lst = tmp_path / "list.json"
    lst.write_text(json.dumps([k, 3, {"nokey": 1}]))
    assert [r[0] for r in read_key_listing(str(lst))] == [k]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"Contents": 5}))
    with pytest.raises(ValueError, match="unrecognised"):
        read_key_listing(str(bad))


def test_reconcile_keys_listing(ws, tmp_path):
    listing = tmp_path / "keys.txt"
    listing.write_text(
        "\n".join(
            [
                _key(ACTIVE),
                _key(SUSP),
                _key(GHOST),
                QUARANTINE_PREFIX + _key(SUSP, b"q"),
                "_facilities.json",
                "junk/object.bin",
            ]
        )
    )
    rep = reconcile(ws, keys=str(listing))
    c = rep["counts"]
    assert c["ok"] == 1 and c["inactive"] == 1 and c["unknown_facility"] == 1
    assert c["quarantined"] == 1 and c["orphaned"] == 1 and rep["objects_scanned"] == 5


def test_cli_reconcile(ws, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    inbox = str(tmp_path / "inbox")
    _put(inbox, _key(SUSP, b"s"), b"s")
    _put(inbox, "stray.bin")
    assert main(["edge", "reconcile", "--workspace", ws.root, "--landing", inbox]) == 0
    out = capsys.readouterr().out
    assert "read-only" in out and "inactive" in out and "1 object(s) should be quarantined" in out
    assert (
        main(["edge", "reconcile", "--workspace", ws.root, "--landing", inbox, "--limit", "0"]) == 0
    )
    assert "more (use --json" in capsys.readouterr().out
    assert main(["edge", "reconcile", "--workspace", ws.root, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["objects_scanned"] == 0
    assert main(["edge", "reconcile", "--workspace", str(tmp_path)]) == 1
    assert "not a portfolio workspace" in capsys.readouterr().err
