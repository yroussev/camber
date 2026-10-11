"""Byte-reproducible store parts (#130).

The same rows written by the same CAMBER and pyarrow versions give byte-identical part files: rows
sorted timestamp first, one row group per part, fixed writer options, no pandas metadata and a
``camber.layout`` marker. Across pyarrow versions only the content is guaranteed, which the
version-free content digest below checks; the byte golden is pinned to one exact pyarrow version.
"""

import copy
import hashlib
import io
import os
import shutil
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _file, _run_csv  # noqa: E402

from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import _retention as _ret  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store.parquet_store import _read_part_file, role_frame_to_long  # noqa: E402

# The dataset writer's batch size: above it, the pre-#130 threaded write could land a part's rows
# in thread-completion order (rarely just above it, nearly always at a few times it).
_BIG = 1 << 17


def _parts(root) -> dict:
    """``{relative path: bytes}`` of every part file under ``root``."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".parquet"):
                p = os.path.join(dirpath, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root).replace(os.sep, "/")] = fh.read()
    return dict(sorted(out.items()))


def _assert_same_parts(a, b):
    pa_, pb = _parts(a), _parts(b)
    assert pa_ and list(pa_) == list(pb)
    for k in pa_:
        assert pa_[k] == pb[k], f"{k} differs"


def _frame(start="2024-01-30", periods=24 * 10, freq="h", roles=None) -> pd.DataFrame:
    """Exactly representable values (no transcendental functions or RNG: the goldens must not
    move with numpy's SIMD paths or random streams)."""
    idx = pd.date_range(start, periods=periods, freq=freq)
    t = np.arange(periods, dtype="int64")
    roles = roles or [Role.SUPPLY_AIR_TEMP, Role.OAT, Role.RETURN_AIR_TEMP]
    return pd.DataFrame(
        {r: ((t * (37 + 11 * i)) % 1000) / 8.0 + 40 for i, r in enumerate(roles)}, index=idx
    )


def _shuffled(df: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return df.iloc[rng.permutation(len(df))]


# --------------------------------------------------------------------------- dataset ingest


def _big_zip(order) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order:
            kind = {"AHU_ff.csv": "ok", "AHU_damper.csv": "damper", "AHU_leak.csv": "leak"}[name]
            info = zipfile.ZipInfo(f"TEST/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            z.writestr(info, _run_csv(kind, periods=20_000), compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _ingest_big(tmp, monkeypatch, order=("AHU_ff.csv", "AHU_damper.csv", "AHU_leak.csv")) -> str:
    """Ingest a 1-minute, unresampled three-run dataset (each run's write exceeds ``_BIG`` rows);
    returns the store root."""
    zb = _big_zip(order)
    d = copy.deepcopy(_ahu_entry_dict(zb))
    members = d["files"][0]["members"]
    d["files"] = [_file("test.zip", zb, archive="zip", members=members)]
    d["ingest"]["resample"] = None
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp / "cache"))
    _ops.fetch_dataset(entry, opener=FakeOpener({BASE + "test.zip": zb}))
    root = str(tmp / "store")
    _ingest.ingest_dataset(entry, ParquetStore(root), subset="full")
    return root


def test_ingesting_twice_gives_byte_identical_parts(tmp_path, monkeypatch):
    a = _ingest_big(tmp_path / "a", monkeypatch)
    b = _ingest_big(tmp_path / "b", monkeypatch)
    _assert_same_parts(a, b)
    rows = [pq.ParquetFile(os.path.join(a, p)).metadata.num_rows for p in _parts(a)]
    assert max(rows) > _BIG  # more than one writer batch in a part


def test_archive_member_order_does_not_change_the_parts(tmp_path, monkeypatch):
    a = _ingest_big(tmp_path / "a", monkeypatch)
    b = _ingest_big(
        tmp_path / "b", monkeypatch, order=("AHU_leak.csv", "AHU_ff.csv", "AHU_damper.csv")
    )
    _assert_same_parts(a, b)


# --------------------------------------------------------------------------- write_long


def test_a_large_write_repeats_byte_for_byte(tmp_path):
    """Half a million rows in one month partition: the pre-#130 threaded write gave different
    bytes on almost every repeat at this size."""
    f = _frame("2024-01-01", periods=44_000, freq="min", roles=list(Role)[:12])
    long = role_frame_to_long(f, equip="AHU_1", equip_class="AHU")
    assert len(long) > 4 * _BIG
    roots = [str(tmp_path / n) for n in ("a", "b", "c")]
    for r in roots:
        ParquetStore(r).write_long(long, facility_id="f1")
    for r in roots[1:]:
        _assert_same_parts(roots[0], r)


def test_row_and_column_order_do_not_change_the_parts(tmp_path):
    f = _frame(periods=_BIG // 3 + 5000, freq="min")
    long = role_frame_to_long(f, equip="AHU_1", equip_class="AHU")
    assert len(long) > _BIG
    roots = [str(tmp_path / n) for n in ("a", "b", "c", "d")]
    ParquetStore(roots[0]).write_long(long, facility_id="f1")
    ParquetStore(roots[1]).write_long(_shuffled(long), facility_id="f1")
    ParquetStore(roots[2]).write_long(long[long.columns[::-1]], facility_id="f1")
    ParquetStore(roots[3]).write_role_frame(
        f[f.columns[::-1]], facility_id="f1", equip="AHU_1", equip_class="AHU"
    )
    for r in roots[1:]:
        _assert_same_parts(roots[0], r)


def test_appends_give_the_same_names_and_bytes(tmp_path):
    roots = [str(tmp_path / n) for n in ("a", "b")]
    for r in roots:
        st = ParquetStore(r)
        st.write_role_frame(_frame(), facility_id="f1", equip="AHU_1", equip_class="AHU")
        st.write_role_frame(_frame("2024-02-05"), facility_id="f1", equip="AHU_2")
        st.write_role_frame(_frame("2024-02-06"), facility_id="f1", equip="AHU_1")
    _assert_same_parts(*roots)
    names = list(_parts(roots[0]))
    assert "facility_id=f1/year=2024/month=2/part-2-0.parquet" in names


def test_write_rollup_is_byte_reproducible(tmp_path):
    src = ParquetStore(str(tmp_path / "src"))
    src.write_role_frame(_frame(freq="15min", periods=4 * 24 * 40), facility_id="f1", equip="A")
    src.write_role_frame(_frame(freq="15min", periods=4 * 24 * 40), facility_id="f2", equip="B")
    dests = [str(tmp_path / n) for n in ("a", "b")]
    for d in dests:
        assert src.write_rollup("h", ParquetStore(d)) == 2 * 24 * 40 * 3
    _assert_same_parts(*dests)


def _threaded_legacy_write(root, frame):
    """The pre-0.95 year-only layout, written the way the old write_long did (threaded)."""
    long = role_frame_to_long(frame, equip="AHU_1", equip_class="ahu")
    long["facility_id"] = "old"
    long["year"] = pd.to_datetime(long["ts"]).dt.year.astype("int32")
    pads.write_dataset(
        pa.Table.from_pandas(long, preserve_index=False),
        root,
        format="parquet",
        partitioning=["facility_id", "year"],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
        basename_template="part-0-{i}.parquet",
    )


def test_migrate_partitions_is_byte_reproducible(tmp_path):
    legacy = str(tmp_path / "legacy")
    _threaded_legacy_write(legacy, _frame("2024-11-15", 24 * 60))
    roots = [str(tmp_path / n) for n in ("a", "b")]
    for r in roots:
        shutil.copytree(legacy, r)  # plain files, no symlinks
        assert ParquetStore(r).migrate_partitions(apply=True)["applied"]
    _assert_same_parts(*roots)
    for blob in _parts(roots[0]).values():
        _assert_canonical(blob)


def test_retention_rollup_part_is_byte_reproducible(tmp_path):
    long = role_frame_to_long(_frame(periods=24 * 2), equip="AHU_1", equip_class="AHU")
    roll = _ret._rollup(long, "hourly")
    outs = []
    for name, frame in (("a", roll), ("b", _shuffled(roll)[roll.columns[::-1]])):
        root = str(tmp_path / name)
        _ret._write_rollup(
            root, "f1", 2024, 1, frame, covers=["year=2024/month=1/part-0-0.parquet"]
        )
        outs.append(root)
    _assert_same_parts(*outs)
    (blob,) = _parts(outs[0]).values()
    meta = _assert_canonical(blob)
    assert b"camber.rollup.covers" in meta  # kept: retention reads it back


# --------------------------------------------------------------------------- the layout itself


def _assert_canonical(blob: bytes) -> dict:
    f = pq.ParquetFile(io.BytesIO(blob))
    meta = f.schema_arrow.metadata or {}
    assert meta.get(b"camber.layout") == b"1"
    assert b"pandas" not in meta
    assert f.metadata.num_row_groups == 1
    t = f.read()
    keys = [c for c in ("ts", "equip", "role", "equip_class", "value") if c in t.column_names]
    assert t.equals(t.sort_by([(c, "ascending") for c in keys]))
    return meta


def _old_style_write(root, long, fid, seq):
    """A part exactly as write_long wrote it before #130 (pandas metadata, default options)."""
    df = long.copy()
    df["facility_id"] = fid
    df["year"] = df["ts"].dt.year.astype("int32")
    df["month"] = df["ts"].dt.month.astype("int32")
    pads.write_dataset(
        pa.Table.from_pandas(df, preserve_index=False),
        root,
        format="parquet",
        partitioning=["facility_id", "year", "month"],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
        basename_template=f"part-{seq}-{{i}}.parquet",
    )


def _norm(df):
    cols = ["ts", "equip", "equip_class", "role", "value", "facility_id", "year", "month"]
    return df[cols].sort_values(["ts", "equip", "role"]).reset_index(drop=True)


def test_new_parts_are_canonical_and_read_back_like_old_ones(tmp_path):
    a = _frame(periods=24 * 5)
    b = _frame("2024-01-31", periods=24 * 3, roles=[Role.MIXED_AIR_TEMP])
    la = role_frame_to_long(a, equip="AHU_1", equip_class="AHU")
    lb = role_frame_to_long(b, equip="AHU_2", equip_class="")
    new, old = str(tmp_path / "new"), str(tmp_path / "old")
    ParquetStore(new).write_long(la, facility_id="f1")
    ParquetStore(new).write_long(lb, facility_id="f1")
    _old_style_write(old, la, "f1", 0)
    _old_style_write(old, lb, "f1", 1)
    for blob in _parts(new).values():
        _assert_canonical(blob)
    for blob in _parts(old).values():
        assert b"pandas" in (pq.ParquetFile(io.BytesIO(blob)).schema_arrow.metadata or {})
    want = ParquetStore(old).read_long()
    got = ParquetStore(new).read_long()
    assert dict(got.dtypes) == dict(want.dtypes)
    pd.testing.assert_frame_equal(_norm(got), _norm(want))
    # a store mixing both, either one first in discovery order, reads back the same
    for first, second in (("old", "new"), ("new", "old")):
        mixed = str(tmp_path / f"mixed-{first}")
        st = ParquetStore(mixed)
        for kind, long in ((first, la), (second, lb)):
            if kind == "old":
                _old_style_write(mixed, long, "f1", st._next_seq("f1"))
            else:
                st.write_long(long, facility_id="f1")
        assert len(_parts(mixed)) == 4  # each frame spans January and February
        got = st.read_long()
        assert dict(got.dtypes) == dict(want.dtypes)
        pd.testing.assert_frame_equal(_norm(got), _norm(want))
        wide = st.read_role_frame(facility_id="f1", equip="AHU_1")
        pd.testing.assert_frame_equal(
            wide, ParquetStore(old).read_role_frame(facility_id="f1", equip="AHU_1")
        )


# --------------------------------------------------------------------------- goldens

_GOLDEN_ROOT = "golden"
_GOLDEN_DIR = "facility_id=f1/year=2024/"


def _golden_store(tmp_path) -> str:
    root = str(tmp_path / _GOLDEN_ROOT)
    st = ParquetStore(root)
    st.write_role_frame(_frame(), facility_id="f1", equip="AHU_1", equip_class="AHU")
    st.write_role_frame(
        _frame("2024-02-03", periods=24 * 2, roles=[Role.MIXED_AIR_TEMP]),
        facility_id="f1",
        equip="AHU_2",
    )
    return root


_SCHEMA = pa.schema(
    [
        ("ts", pa.timestamp("ns")),
        ("equip", pa.string()),
        ("equip_class", pa.string()),
        ("role", pa.string()),
        ("value", pa.float64()),
    ]
)


def _content_digest(blob: bytes) -> str:
    """sha256 of the decoded rows as an Arrow IPC stream with a fixed schema and no metadata:
    the same on every pyarrow version that decodes the same rows in the same order."""
    t = pq.ParquetFile(io.BytesIO(blob)).read().cast(_SCHEMA)
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, _SCHEMA) as w:
        w.write_table(t.combine_chunks())
    return hashlib.sha256(sink.getvalue()).hexdigest()


CONTENT_GOLDEN = {
    "month=1/part-0-0.parquet": "18e2baf462d8eb5adbc45505a0d0d4111dc0b2bc9174d7fd0845d3f395f96075",
    "month=2/part-0-0.parquet": "180c0e6b35d59d5a892b531d21e47d16786b336c9860be031ed2f181ff501a48",
    "month=2/part-2-0.parquet": "0a259d52df385d0d362626c36d23f7fab131a0bff2f502b30a326af1ce7ffe19",
}

# Bytes depend on the exact pyarrow build (``created_by`` and the encoder): pinned to one version
# and skipped on any other, so a pyarrow upgrade or the minimum-dependency CI leg never fails it.
# On a pyarrow bump, regenerate with the digest printed by the assertion message.
BYTE_GOLDEN_PYARROW = "25.0.1"
BYTE_GOLDEN = {
    "month=1/part-0-0.parquet": "83dfd15fd27d1305a0de65db0aaf9c19358d399dc72fcd43e128948eec38bed4",
    "month=2/part-0-0.parquet": "a6e5f4950cae21f8297391467dd6d60630431e305a463ef10f39ef5265bb0ee5",
    "month=2/part-2-0.parquet": "a00664ad2145a5ee5622ccf6b03f4308b007123f1254c77c6d50b96cb06e712f",
}


def test_content_digest_golden(tmp_path):
    parts = _parts(_golden_store(tmp_path))
    got = {k.removeprefix(_GOLDEN_DIR): _content_digest(v) for k, v in parts.items()}
    assert got == CONTENT_GOLDEN


def test_byte_golden_for_the_pinned_pyarrow(tmp_path):
    if pa.__version__ != BYTE_GOLDEN_PYARROW:
        pytest.skip(
            f"byte golden is pinned to pyarrow {BYTE_GOLDEN_PYARROW} (not {pa.__version__})"
        )
    parts = _parts(_golden_store(tmp_path))
    got = {k.removeprefix(_GOLDEN_DIR): hashlib.sha256(v).hexdigest() for k, v in parts.items()}
    assert got == BYTE_GOLDEN


def test_read_part_file_round_trips_a_canonical_part(tmp_path):
    root = _golden_store(tmp_path)
    p = os.path.join(root, "facility_id=f1", "year=2024", "month=1", "part-0-0.parquet")
    t = _read_part_file(p)
    assert t.column_names == ["ts", "equip", "equip_class", "role", "value"]
