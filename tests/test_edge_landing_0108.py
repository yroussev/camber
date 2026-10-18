"""Edge landing follow-ups to #142 (0.108): a growing month replaces its earlier part (#228),
`edge reconcile` removes row duplicates already in the store (#229), a re-sent year-only part is
caught against the migrated month partitions (#230), and the upload inbox is the default (#233).
"""

import hashlib
import io
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_edge_forwarder import _FakeSource, _forwarder  # noqa: E402
from test_edge_row_digest import _put  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.edge import collect_sink  # noqa: E402
from camber.edge.landing import INBOX_PREFIX, reconcile, route_key, row_duplicates  # noqa: E402
from camber.edge.quarantine import (  # noqa: E402
    land,
    list_quarantine,
    release,
    remove_row_duplicates,
)
from camber.edge.row_digest import INDEX_NAME, PartitionRows, file_rows_digest  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "demo-fac-1"


class _Growing(_FakeSource):
    """A source whose value at each hour never changes: polling it longer only adds rows."""

    def load_points(self, names, resample="1h"):
        self.reads += 1
        base = np.arange(len(self.idx), dtype="float64")
        return pd.DataFrame({n: base + 50.0 + k for k, n in enumerate(names)}, index=self.idx)


class _Edited(_Growing):
    """The same hours with one value changed: an edited history, not a growing month."""

    def load_points(self, names, resample="1h"):
        f = super().load_points(names, resample)
        f.iloc[0, 0] += 9.0
        return f


class _InProgress(_Growing):
    """A poll taken mid-hour: the last hour's resampled mean holds only the samples so far."""

    def load_points(self, names, resample="1h"):
        f = super().load_points(names, resample)
        f.iloc[-1, :] += 0.25
        return f


def _poll(tmp_path, tag, n, *, start="2024-06-01", source=None):
    """Every part of one forwarder poll of ``n`` hours: ``[(key, bytes)]``."""
    sink, log = collect_sink()
    fwd = _forwarder(tmp_path / f"spool-{tag}", sink)
    fwd.source = source if source is not None else _Growing(start=start, n=n)
    fwd.poll_once()
    return [(o["key"], o["data"]) for o in log]


def _rewrite(data: bytes) -> bytes:
    """The same rows in other bytes: reversed rows, another column order, a timestamp unit,
    a codec and small row groups (a readable part, unlike a forged pandas metadata blob)."""
    t = pq.ParquetFile(io.BytesIO(data)).read().replace_schema_metadata(None)
    t = t.take(pa.array(list(range(t.num_rows))[::-1]))
    t = t.select(["value", "role", "ts", "equip_class", "equip"])
    t = t.set_column(2, "ts", t.column("ts").cast(pa.timestamp("us")))
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd", row_group_size=7)
    assert buf.getvalue() != data
    return buf.getvalue()


def _one(tmp_path, tag, n, **kw):
    (kd,) = _poll(tmp_path, tag, n, **kw)
    return kd


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("Demo", facility_id=FID, reason="t", activate=True)
    return pf


def _parts(pf, key):
    d = os.path.join(pf.store_root, *key.split("/")[:-1])
    return sorted(n for n in os.listdir(d) if n.endswith(".parquet") and not n.startswith("_"))


def _read(pf):
    return ParquetStore(pf.store_root).read_long()


def _audit(pf, action):
    path = os.path.join(pf.root, "_audit.ndjson")
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [r for r in rows if r.get("action") == action]


# --------------------------------------------------------------------------- #228


def test_a_growing_month_across_three_polls_ends_with_one_part(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    keys = []
    for tag, n in (("a", 24), ("b", 48), ("c", 72)):
        key, data = _one(tmp_path, tag, n)
        keys.append(key)
        _put(inbox, key, data)
        rep = land(ws, inbox, apply=True, reason=f"poll {tag}")
        (row,) = rep["objects"]
        assert row["to"] == "store" and row["category"] == "ok"
        if len(keys) > 1:
            assert row["replaces"] == [keys[-2].split("/")[-1]]
            assert row["result"] == "replaced" and rep["replaces"] == 1
        assert _parts(ws, key) == [key.split("/")[-1]]
        long = _read(ws)
        # reads never see duplicated rows
        assert not long.duplicated(["ts", "equip", "role"]).any()
        assert len(long) == n * 3  # three mapped points
    # the row index holds the one part left, and nothing reaches quarantine
    pr = PartitionRows(os.path.join(ws.store_root, *keys[-1].split("/")[:-1]))
    assert set(pr.parts) == {keys[-1].split("/")[-1]}
    assert pr.parts[keys[-1].split("/")[-1]]["ts_min"] < pr.parts[keys[-1].split("/")[-1]]["ts_max"]
    assert list_quarantine(ws) == []
    done = [r for r in _audit(ws, "edge.land") if r["details"].get("phase") == "done"]
    assert done[-1]["details"]["replaced"] == {keys[-1]: [keys[-2].split("/")[-1]]}
    begin = [r for r in _audit(ws, "edge.land") if r["details"].get("phase") == "begin"]
    assert begin[-1]["details"]["replaces"] == {keys[-1]: [keys[-2].split("/")[-1]]}
    # no swap leftovers beside the partition
    ydir = os.path.dirname(os.path.join(ws.store_root, *keys[-1].split("/")[:-1]))
    assert not [n for n in os.listdir(ydir) if n.startswith(("_swap-", "_trash-"))]


def test_three_polls_in_one_run_and_a_late_smaller_resend(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    sent = [_one(tmp_path, t, n) for t, n in (("a", 24), ("b", 48), ("c", 72))]
    for key, data in sent:
        _put(inbox, key, data)
    dry = land(ws, inbox)
    assert dry["dry_run"] and not os.path.exists(os.path.join(ws.store_root, "facility_id=" + FID))
    rep = land(ws, inbox, apply=True, reason="backlog")
    big = sent[-1][0]
    assert _parts(ws, big) == [big.split("/")[-1]]
    assert len(_read(ws)) == 72 * 3
    assert sum(r["to"] == "store" for r in rep["objects"]) >= 1
    # an earlier, smaller poll arriving late: every row is already stored -> a duplicate
    key, data = _one(tmp_path, "late", 36)
    _put(inbox, key, data)
    (row,) = land(ws, inbox, apply=True, reason="late")["objects"]
    assert row["category"] == "duplicate" and row["to"] == "quarantine"
    assert row["duplicate_of"] == big.split("/")[-1]
    assert len(_read(ws)) == 72 * 3


def test_a_poll_taken_mid_hour_is_superseded_by_the_next(ws, tmp_path):
    """The hour in progress at a poll is revised by the next one: a growing month, not a
    conflict (any other changed value is one)."""
    inbox = str(tmp_path / "inbox")
    k1, d1 = _one(tmp_path, "a", 24, source=_InProgress(n=24))
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _one(tmp_path, "b", 48, source=_InProgress(n=48))
    _put(inbox, k2, d2)
    (row,) = land(ws, inbox, apply=True, reason="second")["objects"]
    assert row["to"] == "store" and row["replaces"] == [k1.split("/")[-1]]
    assert row["revised"] == 3  # the last hour of each of the three series
    assert _parts(ws, k2) == [k2.split("/")[-1]]
    long = _read(ws)
    assert len(long) == 48 * 3 and not long.duplicated(["ts", "equip", "role"]).any()
    # the earlier poll arriving late again: its rows are held, but for its own last hour
    _put(inbox, k1, d1)
    (row,) = land(ws, inbox, apply=True, reason="late")["objects"]
    assert row["category"] == "duplicate" and row["to"] == "quarantine"
    assert len(_read(ws)) == 48 * 3


def test_a_conflicting_overlap_is_quarantined_not_merged(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    k1, d1 = _one(tmp_path, "a", 24)
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    before = _read(ws)
    # the next poll grew the month but one stored value changed: overlaps without holding all
    k2, d2 = _one(tmp_path, "b", 48, source=_Edited(n=48))
    _put(inbox, k2, d2)
    rep = land(ws, inbox, apply=True, reason="second")
    (row,) = rep["objects"]
    assert row["category"] == "conflict" and row["to"] == "quarantine"
    assert row["conflicts_with"] == [k1.split("/")[-1]] and "1 shared" in row["detail"]
    assert _parts(ws, k1) == [k1.split("/")[-1]]
    assert len(_read(ws)) == len(before)
    (q,) = list_quarantine(ws)
    assert q["key"] == k2 and q["category"] == "conflict"
    # a disjoint part (another month) still lands beside it
    k3, d3 = _one(tmp_path, "c", 24, start="2024-07-01")
    _put(inbox, k3, d3)
    (row,) = land(ws, inbox, apply=True, reason="july")["objects"]
    assert row["to"] == "store" and not row.get("replaces")


def test_a_held_facility_keeps_the_replaced_part_in_quarantine(ws, tmp_path):
    ws.hold(FID, reason="litigation")
    inbox = str(tmp_path / "inbox")
    k1, d1 = _one(tmp_path, "a", 24)
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _one(tmp_path, "b", 48)
    _put(inbox, k2, d2)
    (row,) = land(ws, inbox, apply=True, reason="second")["objects"]
    assert row["replaced"] == [k1.split("/")[-1]]
    assert _parts(ws, k2) == [k2.split("/")[-1]]
    (q,) = list_quarantine(ws)
    assert q["key"] == k1 and q["category"] == "duplicate" and "legal hold" in q["detail"]
    assert q["sha256"] == hashlib.sha256(d1).hexdigest()
    rel = release(ws, facility_id=FID)
    assert rel["planned"] == []  # never released: its rows are in the store


# --------------------------------------------------------------------------- #229


def _seed_pair(pf, tmp_path):
    """The same rows under two keys of one partition, as a pre-0.107 landing left them."""
    key, data = _one(tmp_path, "a", 24)
    other = _rewrite(data)
    other_key = key.rsplit("/", 1)[0] + f"/part-{hashlib.sha256(other).hexdigest()[:16]}.parquet"
    old = _put(pf.store_root, key, data)
    new = _put(pf.store_root, other_key, other)
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_700_000_500, 1_700_000_500))
    return key, other_key


def test_reconcile_reports_row_duplicates_and_a_dry_run_changes_nothing(ws, tmp_path):
    keep, extra = _seed_pair(ws, tmp_path)
    pdir = os.path.join(ws.store_root, *keep.split("/")[:-1])
    before = sorted(os.listdir(pdir))
    rep = reconcile(ws)
    (g,) = rep["row_duplicates"]
    assert g["keep"] == keep and g["extra"] == [extra] and g["action"] == "remove"
    assert [p["key"] for p in g["parts"]] == [keep, extra] and g["parts"][1]["bytes"] > 0
    assert rep["duplicate_copies"] == 1 and rep["read_only"]
    dry = remove_row_duplicates(ws)
    assert dry["dry_run"] and dry["copies"] == 1
    assert sorted(os.listdir(pdir)) == before  # nothing changed, no cache written
    assert not os.path.exists(os.path.join(pdir, INDEX_NAME))
    with pytest.raises(ValueError, match="yes"):
        remove_row_duplicates(ws, reason="r", apply=True)
    with pytest.raises(ValueError, match="reason"):
        remove_row_duplicates(ws, apply=True, yes=True)


def test_reconcile_apply_yes_keeps_the_oldest_copy(ws, tmp_path, capsys):
    keep, extra = _seed_pair(ws, tmp_path)
    n = len(_read(ws))
    argv = ["edge", "reconcile", "--workspace", ws.root]
    assert main([*argv, "--yes"]) == 1  # --yes confirms --apply
    assert "--apply" in capsys.readouterr().err
    assert main(argv) == 0
    assert "row duplicates" in capsys.readouterr().out
    assert main([*argv, "--apply", "--yes", "--reason", "dedupe"]) == 0
    assert "removed 1 row-duplicate" in capsys.readouterr().out
    assert os.path.exists(os.path.join(ws.store_root, *keep.split("/")))
    assert not os.path.exists(os.path.join(ws.store_root, *extra.split("/")))
    assert len(_read(ws)) == n // 2
    assert row_duplicates(ws) == []
    recs = _audit(ws, "edge.reconcile.dedupe")
    assert [r["details"]["phase"] for r in recs] == ["begin", "done"]
    assert recs[0]["details"]["keys"] == [extra] and recs[0]["reason"] == "dedupe"
    pr = PartitionRows(os.path.join(ws.store_root, *keep.split("/")[:-1]))
    assert set(pr.parts) == {keep.split("/")[-1]}


def test_reconcile_dedupe_under_a_legal_hold_quarantines_the_copy(ws, tmp_path):
    ws.hold(FID, reason="litigation")
    keep, extra = _seed_pair(ws, tmp_path)
    rep = remove_row_duplicates(ws, reason="dedupe", apply=True, yes=True)
    assert rep["groups"][0]["action"] == "quarantine"
    assert not os.path.exists(os.path.join(ws.store_root, *extra.split("/")))
    (q,) = list_quarantine(ws)
    assert q["key"] == extra and q["category"] == "duplicate"


def test_reconcile_dedupe_leaves_a_suspended_facility_alone(ws, tmp_path):
    keep, extra = _seed_pair(ws, tmp_path)
    ws.transition(FID, "suspend", reason="paused")
    (g,) = row_duplicates(ws)
    assert g["action"] is None and "suspended" in g["detail"]
    rep = remove_row_duplicates(ws, reason="r", apply=True, yes=True)
    assert rep["copies"] == 0
    assert os.path.exists(os.path.join(ws.store_root, *extra.split("/")))


# --------------------------------------------------------------------------- #230


def test_a_resent_year_only_part_is_caught_after_its_year_was_migrated(ws, tmp_path):
    parts = _poll(tmp_path, "y", 24 * 45)  # June and part of July
    assert len(parts) == 2
    tables = [pq.ParquetFile(io.BytesIO(d)).read() for _k, d in parts]
    year = pa.concat_tables(tables)
    buf = io.BytesIO()
    pq.write_table(year, buf)  # a pre-0.95 forwarder's year-only part
    data = buf.getvalue()
    ykey = f"facility_id={FID}/year=2024/part-{hashlib.sha256(data).hexdigest()[:16]}.parquet"
    _put(ws.store_root, ykey, data)
    n = len(_read(ws))
    ParquetStore(ws.store_root).migrate_partitions(apply=True, reason="migrate")
    assert len(_read(ws)) == n
    # the same year re-sent in new bytes, under a new key
    other = _rewrite(data)
    okey = f"facility_id={FID}/year=2024/part-{hashlib.sha256(other).hexdigest()[:16]}.parquet"
    inbox = str(tmp_path / "inbox")
    _put(inbox, okey, other)
    (row,) = land(ws, inbox, apply=True, reason="resend")["objects"]
    assert row["category"] == "duplicate" and row["to"] == "quarantine"
    assert "month partitions of 2024" in row["detail"] and "month=6/" in row["duplicate_of"]
    assert len(_read(ws)) == n
    assert list_quarantine(ws)[0]["key"] == okey
    # a year-only part with rows the months do not hold still lands
    aug = _one(tmp_path, "aug", 5, start="2024-08-01")[1]
    more = pa.concat_tables([year, pq.ParquetFile(io.BytesIO(aug)).read()])
    buf = io.BytesIO()
    pq.write_table(more, buf)
    mkey = f"facility_id={FID}/year=2024/part-{hashlib.sha256(buf.getvalue()).hexdigest()[:16]}"
    _put(inbox, mkey + ".parquet", buf.getvalue())
    (row,) = land(ws, inbox)["objects"]
    assert row["to"] == "store"


# --------------------------------------------------------------------------- #233


def test_route_key_signs_into_the_inbox_by_default(ws):
    key = f"facility_id={FID}/year=2024/month=6/part-0123456789abcdef.parquet"
    assert route_key(ws, key) == INBOX_PREFIX + key
    assert route_key(ws, key, inbox=False) == key


@pytest.mark.parametrize("dotfile", [False, True])
def test_an_empty_folder_is_an_empty_store_everywhere(tmp_path, capsys, dotfile):
    from camber.api.server import _store_problem

    p = tmp_path / "fresh"
    p.mkdir()
    if dotfile:
        (p / ".DS_Store").write_bytes(b"")
    before = sorted(os.listdir(p))
    assert _store_problem(p) is None
    assert main(["store", "verify", str(p), "--json", "--strict"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["counts"]["parts"] == 0 and r["ok"]
    for argv in (
        ["store", "normalize", str(p)],
        ["store", "normalize", str(p), "--apply", "--yes"],
        ["store", "migrate-partitions", str(p)],
        ["store", "migrate-partitions", str(p), "--apply", "--yes"],
    ):
        assert main(argv) == 0, argv
        capsys.readouterr()
        assert sorted(os.listdir(p)) == before, argv  # nothing created, not even a _lock
    assert ParquetStore(str(p)).verify()["counts"]["parts"] == 0
    assert ParquetStore(str(p)).normalize(apply=True)["partitions"] == []
    assert ParquetStore(str(p)).migrate_partitions(apply=True)["partitions"] == []
    assert sorted(os.listdir(p)) == before


def test_a_missing_path_or_a_file_is_still_refused(tmp_path, capsys):
    from camber.api.server import _store_problem
    from camber.store.facilities import _require_store_dir

    f = tmp_path / "file"
    f.write_text("x")
    for bad in (tmp_path / "missing", f):
        assert _store_problem(bad) is not None
        with pytest.raises(FileNotFoundError, match="not a ParquetStore"):
            _require_store_dir(bad)
        assert main(["store", "verify", str(bad)]) == 1
        assert "not a ParquetStore" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()


def test_land_invalidates_the_point_catalog(ws, tmp_path):
    from camber.store.parquet_store import _read_gen

    inbox = str(tmp_path / "inbox")
    store = ParquetStore(ws.store_root)
    assert store.points() == []  # caches an (empty) catalog
    key, data = _one(tmp_path, "a", 24)
    _put(inbox, key, data)
    gen = _read_gen(ws.store_root)
    land(ws, inbox, apply=True, reason="first")
    assert _read_gen(ws.store_root) != gen
    assert len(ParquetStore(ws.store_root).points()) == 3


def test_file_rows_digest_unchanged(tmp_path):
    key, data = _one(tmp_path, "a", 24)
    p = _put(str(tmp_path / "x"), "p.parquet", data)
    d, rows = file_rows_digest(p)
    assert d and rows == 24 * 3
