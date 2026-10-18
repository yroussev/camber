"""Byte-independent dedupe at the edge landing (0.107, #142).

A month re-sent in different bytes (a forwarder upgrade to canonical parts, a pandas or pyarrow
upgrade on the device) must be recognised by its rows and never stored twice; a month whose rows
changed must land; parts in the pre-0.107 bytes must keep landing.
"""

import hashlib
import io
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_edge_forwarder import _FakeSource, _forwarder  # noqa: E402

from camber.edge import collect_sink  # noqa: E402
from camber.edge.landing import INBOX_PREFIX, QUARANTINE_PREFIX, route_key  # noqa: E402
from camber.edge.quarantine import land, list_quarantine, release  # noqa: E402
from camber.edge.row_digest import (  # noqa: E402
    INDEX_NAME,
    PartitionRows,
    file_rows_digest,
    rows_digest,
)
from camber.portfolio import Portfolio  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "demo-fac-1"


class _Bumped(_FakeSource):
    """The same source with every value shifted: a month whose rows genuinely changed."""

    def load_points(self, names, resample="1h"):
        return super().load_points(names, resample) + 1.0


def _batch(tmp_path, tag, *, layout="canonical", source=None):
    """One forwarder batch (one June 2024 part): ``(key, bytes)``."""
    sink, log = collect_sink()
    kw = {"part_layout": layout}
    fwd = _forwarder(tmp_path / f"spool-{tag}", sink, **kw)
    if source is not None:
        fwd.source = source
    fwd.poll_once()
    (obj,) = log
    return obj["key"], obj["data"]


def _put(root, key, data):
    p = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def _rewrite(data: bytes) -> bytes:
    """The same rows in other bytes, as another pandas / pyarrow version might write them:
    shuffled rows, other column order, microsecond timestamps, dictionary and large strings,
    small row groups, another codec and another pandas metadata blob."""
    t = pq.ParquetFile(io.BytesIO(data)).read()
    t = t.take(pa.array(list(range(t.num_rows))[::-1]))
    t = t.select(["value", "role", "ts", "equip_class", "equip"])
    t = t.set_column(2, "ts", t.column("ts").cast(pa.timestamp("us")))
    t = t.set_column(1, "role", t.column("role").dictionary_encode())
    t = t.set_column(4, "equip", t.column("equip").cast(pa.large_string()))
    t = t.replace_schema_metadata({b"pandas": b'{"pandas_version": "9.9.9"}'})
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd", row_group_size=7, use_dictionary=False)
    out = buf.getvalue()
    assert out != data
    return out


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("Demo", facility_id=FID, reason="t", activate=True)
    return pf


def _rows(pf):
    return len(ParquetStore(pf.store_root).read_long())


# --------------------------------------------------------------------------- the digest


def test_the_digest_ignores_bytes_but_not_rows(tmp_path):
    _key, data = _batch(tmp_path, "a", layout="legacy")
    t = pq.ParquetFile(io.BytesIO(data)).read()
    d = rows_digest(t)
    assert d and len(d) == 64
    assert rows_digest(pq.ParquetFile(io.BytesIO(_rewrite(data))).read()) == d
    _ck, canon = _batch(tmp_path, "b")
    assert canon != data and rows_digest(pq.ParquetFile(io.BytesIO(canon)).read()) == d
    # a tz-aware column in another zone holds the same instants
    aware = t.set_column(0, "ts", t.column("ts").cast(pa.timestamp("ns", tz="UTC")))
    shifted = aware.set_column(0, "ts", aware.column("ts").cast(pa.timestamp("ns", tz="Etc/GMT+5")))
    assert rows_digest(aware) == rows_digest(shifted) != d  # aware differs from naive
    # any change to the rows changes it: a value, a dropped row, a duplicated row, -0.0 does not
    vals = t.column("value").to_pylist()
    changed = t.set_column(4, "value", pa.array([vals[0] + 0.5, *vals[1:]], pa.float64()))
    assert rows_digest(changed) != d
    assert rows_digest(t.slice(1)) != d
    assert rows_digest(pa.concat_tables([t, t.slice(0, 1)])) != d
    z = pa.table(
        {
            "ts": t.column("ts").slice(0, 2),
            "equip": ["a", "a"],
            "equip_class": ["", ""],
            "role": ["r", "r"],
            "value": [0.0, 1.0],
        }
    )
    neg = z.set_column(4, "value", pa.array([-0.0, 1.0]))
    assert rows_digest(z) == rows_digest(neg)
    # not the long five columns, or not Parquet: no digest
    assert rows_digest(t.drop(["equip_class"])) is None
    assert rows_digest(t.append_column("x", pa.array([1] * t.num_rows))) is None
    junk = _put(str(tmp_path / "j"), "x.parquet", b"not parquet")
    assert file_rows_digest(junk) == (None, None)


def test_the_forwarder_manifest_digest_matches_the_landing(tmp_path):
    sink, log = collect_sink()
    _forwarder(tmp_path / "s", sink).poll_once()
    (obj,) = log
    p = _put(str(tmp_path / "x"), "part.parquet", obj["data"])
    assert file_rows_digest(p)[0] == obj["metadata"]["rows_sha256"]


# --------------------------------------------------------------------------- the landing


def test_legacy_parts_still_land_and_are_recorded(ws, tmp_path):
    key, data = _batch(tmp_path, "old", layout="legacy")
    inbox = str(tmp_path / "inbox")
    _put(inbox, key, data)
    rep = land(ws, inbox, apply=True, reason="nightly")
    (row,) = rep["objects"]
    assert row["to"] == "store" and row["category"] == "ok" and row["rows_sha256"]
    assert os.path.exists(os.path.join(ws.store_root, *key.split("/")))
    n = _rows(ws)
    assert n > 0
    idx = os.path.join(ws.store_root, *key.split("/")[:-1], INDEX_NAME)
    with open(idx, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["parts"][key.split("/")[-1]]["rows_sha256"] == row["rows_sha256"]
    # the same bytes again: the same key, deduplicated as before
    _put(inbox, key, data)
    again = land(ws, inbox, apply=True, reason="again")
    assert again["objects"][0]["to"] == "store" and _rows(ws) == n
    assert not os.listdir(os.path.join(inbox, *key.split("/")[:-1]))


def test_a_resend_in_different_bytes_is_deduplicated(ws, tmp_path):
    """An older forwarder's part landed straight into the store (no row index); the upgraded
    forwarder re-sends the month in canonical bytes under a new key."""
    old_key, old = _batch(tmp_path, "old", layout="legacy")
    _put(ws.store_root, old_key, old)
    n = _rows(ws)
    new_key, new = _batch(tmp_path, "new")
    assert new_key != old_key
    inbox = str(tmp_path / "inbox")
    _put(inbox, new_key, new)
    dry = land(ws, inbox)
    (row,) = dry["objects"]
    assert row["category"] == "duplicate" and row["to"] == "quarantine"
    assert row["duplicate_of"] == old_key.split("/")[-1]
    assert not os.path.exists(os.path.join(ws.store_root, *old_key.split("/")[:-1], INDEX_NAME))
    land(ws, inbox, apply=True, reason="nightly")
    assert _rows(ws) == n
    assert not os.path.exists(os.path.join(ws.store_root, *new_key.split("/")))
    (q,) = list_quarantine(ws)
    assert q["key"] == new_key and q["category"] == "duplicate"
    # the existing part was digested once and is now cached beside it
    pr = PartitionRows(os.path.join(ws.store_root, *old_key.split("/")[:-1]))
    assert pr.parts[old_key.split("/")[-1]]["rows_sha256"] == row["rows_sha256"]
    # a duplicate is never released into the store
    rel = release(ws, facility_id=FID)
    assert rel["planned"] == [] and rel["refused"][0]["key"] == new_key


def test_a_resend_after_a_pandas_or_pyarrow_upgrade_is_deduplicated(ws, tmp_path):
    key, data = _batch(tmp_path, "a")
    inbox = str(tmp_path / "inbox")
    _put(inbox, key, data)
    land(ws, inbox, apply=True, reason="first")
    n = _rows(ws)
    other = _rewrite(data)
    other_key = key.rsplit("/", 1)[0] + f"/part-{hashlib.sha256(other).hexdigest()[:16]}.parquet"
    _put(inbox, other_key, other)
    rep = land(ws, inbox, apply=True, reason="second")
    (row,) = rep["objects"]
    assert row["category"] == "duplicate" and row["duplicate_of"] == key.split("/")[-1]
    assert _rows(ws) == n


def test_the_cache_is_only_a_cache(ws, tmp_path):
    key, data = _batch(tmp_path, "a", layout="legacy")
    inbox = str(tmp_path / "inbox")
    _put(inbox, key, data)
    land(ws, inbox, apply=True, reason="first")
    pdir = os.path.join(ws.store_root, *key.split("/")[:-1])
    os.remove(os.path.join(pdir, INDEX_NAME))  # lost: the part is digested again
    new_key, new = _batch(tmp_path, "b")
    _put(inbox, new_key, new)
    assert land(ws, inbox)["objects"][0]["category"] == "duplicate"
    # a stale entry (the part changed since it was recorded) is not trusted
    pr = PartitionRows(pdir)
    pr.record(key.split("/")[-1], "0" * 64, 1)
    pr.parts[key.split("/")[-1]]["bytes"] = -1
    pr.save()
    assert land(ws, inbox)["objects"][0]["category"] == "duplicate"


def test_a_genuinely_changed_month_is_not_deduplicated(ws, tmp_path):
    """The same observations with other values: not a duplicate, and since 0.108 (#228) not
    stacked beside the first either -- a conflict, quarantined, never merged."""
    key, data = _batch(tmp_path, "a", layout="legacy")
    inbox = str(tmp_path / "inbox")
    _put(inbox, key, data)
    land(ws, inbox, apply=True, reason="first")
    n = _rows(ws)
    k2, d2 = _batch(tmp_path, "b", source=_Bumped())
    assert k2 != key
    _put(inbox, k2, d2)
    rep = land(ws, inbox, apply=True, reason="second")
    (row,) = rep["objects"]
    assert row["to"] == "quarantine" and row["category"] == "conflict"
    assert row["conflicts_with"] == [key.split("/")[-1]]
    assert _rows(ws) == n


def test_two_objects_with_the_same_rows_in_one_run(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    k1, d1 = _batch(tmp_path, "a", layout="legacy")
    k2, d2 = _batch(tmp_path, "b")
    _put(inbox, k1, d1)
    _put(inbox, k2, d2)
    rep = land(ws, inbox, apply=True, reason="both")
    assert sorted(r["to"] for r in rep["objects"]) == ["quarantine", "store"]
    dup = next(r for r in rep["objects"] if r["to"] == "quarantine")
    assert dup["category"] == "duplicate"
    stored = next(r for r in rep["objects"] if r["to"] == "store")
    assert dup["duplicate_of"] == stored["key"].split("/")[-1]


def test_release_refuses_rows_the_store_already_holds(ws, tmp_path):
    ws.transition(FID, "suspend", reason="paused")
    inbox = str(tmp_path / "inbox")
    k1, d1 = _batch(tmp_path, "a", layout="legacy")
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="while suspended")  # quarantined: inactive
    ws.transition(FID, "resume", reason="back")
    k2, d2 = _batch(tmp_path, "b")
    _put(ws.store_root, k2, d2)  # the same rows reached the store another way
    rel = release(ws, facility_id=FID)
    assert rel["planned"] == [] and "already holds these rows" in rel["refused"][0]["why"]


def test_route_key_can_stage_accepted_uploads_in_an_inbox(ws):
    key = f"facility_id={FID}/year=2024/month=6/part-0123456789abcdef.parquet"
    assert route_key(ws, key) == INBOX_PREFIX + key  # the default since 0.108 (#233)
    assert route_key(ws, key, inbox=True) == INBOX_PREFIX + key
    assert route_key(ws, key, inbox=False) == key
    ghost = "facility_id=ghost-hall-778899/year=2024/month=6/part-0123456789abcdef.parquet"
    assert route_key(ws, ghost, inbox=True) == QUARANTINE_PREFIX + ghost
