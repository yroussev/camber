"""0.112 (#319): research-only provenance in the stored data.

``camber datasets ingest`` writes the dataset id, its licence and a research-only flag into every
Parquet part file's key-value metadata; every rewrite keeps it; the capability registry refuses a
part marked research-only wherever it sits (a partial copy into a user facility, a mixed facility,
another store), naming no path; the local readers keep reading it; and a user's own data stays
unmarked. Every store is a small one in its own tmp directory; nothing reaches the network.
"""

import datetime as dt
import glob
import json
import os
import shutil
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

from camber import _servable  # noqa: E402
from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.capabilities._policy import run_config_for  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore, _partprov  # noqa: E402
from camber.store.parquet_store import role_frame_to_long  # noqa: E402

REG = default_registry()
NC = _partprov.encode("test-ahu", "CC-BY-NC-4.0", True)
OPEN = _partprov.encode("lbnl-fcu", "CC-BY-4.0", False)
IDX = pd.date_range("2024-06-01", periods=24 * 70, freq="h")  # three months


def _frame(v=55.0, idx=IDX):
    return pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: v + np.arange(len(idx)) % 5, Role.OAT: 60.0, Role.HEAT_VALVE: 0.0},
        index=idx,
    )


def _write(st, fid, prov, v=55.0, equip="AHU-1", idx=IDX):
    """Append one equipment's frame as parts carrying ``prov`` (``None``: the user's own)."""
    long = role_frame_to_long(_frame(v, idx), equip=equip, equip_class="AHU")
    return st._append(long, fid, prov)


def _parts(root, fid="*"):
    return sorted(
        glob.glob(os.path.join(root, f"facility_id={fid}", "**", "*.parquet"), recursive=True)
    )


def _marks(root, fid="*"):
    return [pq.read_metadata(p).metadata.get(_partprov.KEY) for p in _parts(root, fid)]


def _ctx(root, allowed):
    return CallContext(store=str(root), allowed_facilities=frozenset(allowed))


def _refused(ctx, fid, cap="points"):
    with pytest.raises(CapabilityError) as e:
        REG.call(cap, {"facility_id": fid}, ctx)
    assert e.value.code == "refused_licence", e.value.message
    return e.value


def _no_path(e, *roots):
    text = e.message + json.dumps(e.data)
    for r in roots:
        assert str(r) not in text
    for bit in ("<path>", "facility_id=", "month=", ".parquet", "part-"):
        assert bit not in text


# ------------------------------------------------------------------ ingest writes the mark


@pytest.fixture
def ahu(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    zb = _zip_bytes()
    opener = FakeOpener({BASE + "test.zip": zb})

    def entry(licence="CC-BY-4.0", access="open"):
        d = _ahu_entry_dict(zb)
        d.update(licence=licence, access=access)
        return DatasetEntry.from_dict(d)

    return entry, opener, tmp_path


def test_ingest_marks_every_part_and_a_forced_reingest_keeps_it(ahu):
    entry, opener, tmp = ahu
    ro = entry("CC-BY-NC-4.0", "research_only")
    _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)
    store = str(tmp / "store")
    _ingest.ingest_dataset(ro, store)
    marks = _marks(store)
    assert marks and set(marks) == {NC}
    assert json.loads(NC) == {
        "dataset_id": "test-ahu",
        "licence": "CC-BY-NC-4.0",
        "research_only": True,
    }
    before = _parts(store)
    _ingest.ingest_dataset(ro, store, force=True)  # staged, then swapped in
    assert _parts(store) == before and set(_marks(store)) == {NC}
    ctx = _ctx(store, {"ds-test-ahu"})
    _refused(ctx, "ds-test-ahu")


def test_an_open_dataset_is_marked_and_served(ahu):
    entry, opener, tmp = ahu
    op = entry()
    _ops.fetch_dataset(op, opener=opener)
    store = str(tmp / "store")
    _ingest.ingest_dataset(op, store)
    want = _partprov.encode("test-ahu", "CC-BY-4.0", False)
    assert set(_marks(store)) == {want} and not _partprov.research_only(want)
    res = REG.call("points", {"facility_id": "ds-test-ahu"}, _ctx(store, {"ds-test-ahu"}))
    assert res.data["count"] > 0


def test_a_users_own_data_stays_unmarked(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_frame(), facility_id="hq", equip="AHU-1", equip_class="AHU")
    assert set(_marks(st.root)) == {None}
    assert _partprov.facility_marks(st.root, "hq") == {None: len(_parts(st.root))}
    res = REG.call("points", {"facility_id": "hq"}, _ctx(st.root, {"hq"}))
    assert res.data["count"] == 3


# ------------------------------------------------------------------ the copies of DD2-R3-1


@pytest.fixture
def store(tmp_path):
    """A research-only facility (marked, registered as the ingest registers it) and the user's."""
    from camber.datasets import get

    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "ds-at-30bldg-sensors-b02", NC, 55.0)
    _write(st, "hq", None, 60.0)
    st.register_facility(
        "ds-at-30bldg-sensors-b02", name="NC", dataset=get("at-30bldg-sensors").provenance()
    )
    st.register_facility("hq", name="HQ")
    st.points()
    return st


def _copy(src_root, src_fid, dst_root, dst_fid, pick=None, rename=None):
    """Copy part files (``pick`` filters their relative paths) into another facility."""
    sdir = os.path.join(src_root, f"facility_id={src_fid}")
    for p in _parts(src_root, src_fid):
        rel = os.path.relpath(p, sdir)
        if pick is not None and not pick(rel):
            continue
        dst = os.path.join(dst_root, f"facility_id={dst_fid}", rename(rel) if rename else rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(p, dst)


def test_one_month_copied_into_a_test_facility_is_refused(store, tmp_path):
    _copy(store.root, "ds-at-30bldg-sensors-b02", store.root, "b02", pick=lambda r: "month=7" in r)
    store.points()  # the catalog rebuilt, as any later write leaves it
    ctx = _ctx(store.root, {"b02", "hq"})
    for cap in ("points", "coverage", "sensor_trust"):
        e = _refused(ctx, "b02", cap)
        assert e.data == {"facility_id": "b02"}
        assert "'test-ahu'" in e.message and "never served" in e.message
        _no_path(e, tmp_path)
    res = REG.call("facilities", {}, ctx)
    assert [f["facility_id"] for f in res.data["facilities"]] == ["hq"]
    assert res.data["withheld_research_only"] == 1


@pytest.mark.parametrize(
    "how",
    ["drop_one", "renamed_parts", "deeper_dir", "merged"],
)
def test_an_altered_copy_is_refused(store, how):
    src = "ds-at-30bldg-sensors-b02"
    if how == "drop_one":
        _copy(store.root, src, store.root, "b02")
        os.remove(_parts(store.root, "b02")[0])
    elif how == "renamed_parts":
        _copy(store.root, src, store.root, "b02", rename=lambda r: r.replace("part-", "x-"))
    elif how == "deeper_dir":
        _copy(store.root, src, store.root, "b02", rename=lambda r: os.path.join("extra", r))
    else:  # every part merged into one file by hand, keeping the file's metadata
        tables = [pq.read_table(p) for p in _parts(store.root, src)]
        merged = pa.concat_tables(
            [
                t.drop_columns([c for c in ("facility_id", "year", "month") if c in t.column_names])
                for t in tables
            ]
        )
        merged = merged.replace_schema_metadata(tables[0].schema.metadata)
        os.makedirs(os.path.join(store.root, "facility_id=b02", "year=2024"))
        pq.write_table(
            merged, os.path.join(store.root, "facility_id=b02", "year=2024", "all.parquet")
        )
    _refused(_ctx(store.root, {"b02"}), "b02", "coverage")


def test_a_facility_mixing_marked_and_unmarked_parts_is_refused(store):
    _copy(
        store.root,
        "ds-at-30bldg-sensors-b02",
        store.root,
        "hq",
        pick=lambda r: "month=8" in r,
        rename=lambda r: r.replace("part-", "part-9"),
    )
    ctx = _ctx(store.root, {"hq"})
    for cap in ("points", "sensor_trust"):
        _refused(ctx, "hq", cap)
    rows = {r["facility_id"]: r for r in _ops.store_provenance(store.root)}
    assert rows["hq"]["state"] == "mixed" and rows["hq"]["research_only"]
    assert 0 < rows["hq"]["marked"] < rows["hq"]["parts"]


def test_a_copy_into_another_store_is_refused(store, tmp_path):
    other = tmp_path / "other"
    _copy(store.root, "ds-at-30bldg-sensors-b02", str(other), "lab-test")
    e = _refused(_ctx(other, {"lab-test"}), "lab-test", "sensor_trust")
    _no_path(e, tmp_path)
    # a run config naming that store is refused the same way
    cfg = {
        "facility_id": "lab-test",
        "source": {"kind": "store", "store": str(other), "facility_id": "lab-test"},
        "rules": ["sat_too_high"],
    }
    with pytest.raises(CapabilityError) as e:
        run_config_for(_ctx(store.root, {"lab-test"}), cfg, base_dir=str(tmp_path))
    assert e.value.code == "refused_licence"


def test_an_open_mark_is_served_and_an_unreadable_one_is_not(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "open", OPEN)
    _write(st, "bad", b"{not json")
    _write(st, "flag-lies", _partprov.encode("x", "CC-BY-NC-ND-4.0", False))
    _write(st, "catalog-says", _partprov.encode("at-30bldg-sensors", "CC-BY-4.0", False))
    ctx = _ctx(st.root, {"open", "bad", "flag-lies", "catalog-says"})
    assert REG.call("points", {"facility_id": "open"}, ctx).data["count"] == 3
    for fid in ("bad", "flag-lies", "catalog-says"):
        _refused(ctx, fid)


# ------------------------------------------------------------------ the readers


def test_a_served_read_refuses_even_when_no_check_named_the_facility(store, monkeypatch):
    _copy(store.root, "ds-at-30bldg-sensors-b02", store.root, "b02")
    monkeypatch.setattr(_servable, "_marked_research_only", lambda root, fid: None)
    ctx = _ctx(store.root, {"b02"})
    for cap in ("coverage", "sensor_trust"):
        e = _refused(ctx, "b02", cap)
        assert e.data == {"facility_id": "b02"}
    with _partprov.serving():
        for read in (
            lambda: store.read_long(facility_id="b02"),
            lambda: store.read_role_frame(facility_id="b02", equip="AHU-1"),
            lambda: store._ts_span("b02"),
        ):
            with pytest.raises(CapabilityError, match="research-only"):
                read()
        with pytest.raises(CapabilityError) as e:
            store.read_long()  # store-wide: names no facility
        assert e.value.data is None
        assert len(store.read_long(facility_id="hq")) > 0


def test_the_local_readers_still_read_research_only_data(store):
    fid = "ds-at-30bldg-sensors-b02"
    assert not _partprov.is_serving()
    assert len(store.read_long(facility_id=fid)) > 0
    assert not store.read_role_frame(facility_id=fid, equip="AHU-1").empty
    assert store._ts_span(fid)[0] is not None


def test_the_marks_are_read_once_until_the_partition_changes(store, monkeypatch):
    fid = "ds-at-30bldg-sensors-b02"
    _partprov._MARKS.clear()
    calls = []
    real = _partprov.of_file
    monkeypatch.setattr(_partprov, "of_file", lambda p: calls.append(p) or real(p))
    n = len(_parts(store.root, fid))
    assert _partprov.facility_marks(store.root, fid) == {NC: n}
    assert _partprov.facility_marks(store.root, fid) == {NC: n} and len(calls) == n
    _write(store, fid, None, 70.0, equip="AHU-2")  # an append: only its new files are read
    added = len(_parts(store.root, fid)) - n
    assert _partprov.facility_marks(store.root, fid) == {NC: n, None: added}
    assert len(calls) == n + added
    # a served equipment read takes the marks from the footers its index already read
    _partprov._MARKS.clear()
    calls.clear()
    with _partprov.serving(), pytest.raises(CapabilityError):
        store.read_role_frame(facility_id=fid, equip="AHU-1")
    assert calls == []


# ------------------------------------------------------------------ rewrites keep the mark


def test_append_keeps_the_marked_parts(store):
    fid = "ds-at-30bldg-sensors-b02"
    before = set(_parts(store.root, fid))
    store.write_role_frame(_frame(80.0), facility_id=fid, equip="AHU-9", equip_class="AHU")
    new = set(_parts(store.root, fid)) - before
    assert {pq.read_metadata(p).metadata.get(_partprov.KEY) for p in before} == {NC}
    assert {pq.read_metadata(p).metadata.get(_partprov.KEY) for p in new} == {None}


def test_normalize_keeps_the_mark(store):
    fid = "ds-at-30bldg-sensors-b02"
    part = _parts(store.root, fid)[0]
    t = pq.ParquetFile(part).read()
    meta = {**t.schema.metadata, b"pandas": b"{}"}
    pq.write_table(t.replace_schema_metadata(meta), part, row_group_size=50)  # not canonical
    rep = store.verify(facility_id=fid)
    problems = [p for row in rep["parts"] for p in row["problems"]]
    assert "pandas metadata" in problems
    assert not any("other schema metadata" in p for p in problems)
    store.normalize(facility_id=fid, apply=True)
    md = pq.read_metadata(part)
    assert md.metadata.get(_partprov.KEY) == NC and b"pandas" not in md.metadata
    assert md.num_row_groups == 1
    assert set(_marks(store.root, fid)) == {NC}


def test_migrate_partitions_keeps_the_mark(tmp_path):
    root = str(tmp_path / "store")
    long = role_frame_to_long(_frame(), equip="AHU-1", equip_class="AHU")
    long["facility_id"] = "old"
    long["year"] = pd.to_datetime(long["ts"]).dt.year.astype("int32")
    table = pa.Table.from_pandas(long, preserve_index=False)
    table = table.replace_schema_metadata({**table.schema.metadata, _partprov.KEY: NC})
    pads.write_dataset(
        table,
        root,
        format="parquet",
        partitioning=["facility_id", "year"],
        partitioning_flavor="hive",
        basename_template="part-0-{i}.parquet",
    )
    assert set(_marks(root)) == {NC}
    st = ParquetStore(root)
    assert st.migrate_partitions(apply=True)["applied"]
    parts = _parts(root)
    assert parts and all("month=" in p for p in parts)
    assert set(_marks(root)) == {NC}


def test_write_rollup_keeps_the_mark(store, tmp_path):
    dest = ParquetStore(str(tmp_path / "daily"))
    store.write_rollup("D", dest)
    assert set(_marks(dest.root, "ds-at-30bldg-sensors-b02")) == {NC}
    assert set(_marks(dest.root, "hq")) == {None}


def _workspace(tmp_path, start, hours):
    from camber.portfolio import Portfolio

    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility("North", reason="x", activate=True)["facility_id"]
    idx = pd.date_range(start, periods=hours, freq="h")
    _write(pf.store, fid, NC, idx=idx)
    return pf, fid


def test_retention_rollups_keep_the_mark(tmp_path):
    pf, fid = _workspace(tmp_path, "2023-06-01", 24 * 120)
    r = pf.apply_retention(apply=True, now="2026-09-29", reason="nightly")
    assert r["problems"] == []
    for freq in ("hourly", "daily"):
        root = os.path.join(pf.root, "rollups", freq)
        assert _parts(root, fid) and set(_marks(root, fid)) == {NC}, freq
    # a daily rollup rebuilt from the hourly one keeps it too
    june = os.path.join(pf.root, "rollups", "daily", f"facility_id={fid}", "year=2023", "month=6")
    for f in os.listdir(june):
        os.remove(os.path.join(june, f))
    pf.set_retention("raw_trends", {"keep": "indefinite"}, reason="test")
    pf.apply_retention(apply=True, now="2031-01-15", reason="nightly")
    assert _parts(os.path.join(pf.root, "rollups", "daily"), fid)
    assert os.listdir(june)
    assert set(_marks(os.path.join(pf.root, "rollups", "daily"), fid)) == {NC}


def test_archive_and_restore_keep_the_mark(tmp_path):
    pf, fid = _workspace(tmp_path, "2025-01-01", 24 * 40)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)
    pf.offboard(fid, reason="ended", apply=True)
    pf.archive(fid, reason="done", apply=True, now=later)
    assert fid not in pf.store.facilities()
    pf.restore(fid, reason="back", apply=True)
    assert _parts(pf.store_root, fid) and set(_marks(pf.store_root, fid)) == {NC}


# ------------------------------------------------------------------ camber datasets provenance


def test_cli_lists_the_facilities_lacking_provenance(tmp_path, capsys):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "ds-lbnl-fcu", None)  # ingested before 0.112
    _write(st, "ds-test-marked", OPEN)
    _write(st, "hq", None)
    assert main(["datasets", "provenance", "--store", st.root]) == 0
    out = capsys.readouterr().out
    assert "ds-lbnl-fcu" in out and "ingest --force" in out
    assert "lbnl-fcu]" in out and "own data" in out
    assert main(["datasets", "provenance", "--store", st.root, "--missing", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["facility_id"] for r in rows] == ["ds-lbnl-fcu"]
    assert rows[0]["state"] == "missing" and rows[0]["unmarked"] == rows[0]["parts"] > 0
    assert main(["datasets", "provenance", "--store", str(tmp_path / "nope")]) == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_named_like_a_part_does_not_block_the_check(store):
    fifo = os.path.join(store.root, "facility_id=hq", "x.parquet")
    os.mkfifo(fifo)
    try:
        assert _servable.refusal("hq", store.root) is None
        assert _partprov.facility_marks(store.root, "hq") == {
            None: len(_parts(store.root, "hq")) - 1
        }
        assert _partprov.of_files([fifo]) == []
        assert not _partprov.regular(fifo)
    finally:
        os.remove(fifo)  # nothing later may trip over it
