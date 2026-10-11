"""``camber store verify`` / ``camber store normalize`` and the edge parts' layout (0.104, #136).

verify reports which parts are canonical (#130) and which fail to read; normalize rewrites the
others in place, content-identical, crash-safely, under the store lock, leaving edge parts,
year-only parts and facilities that do not accept data alone.
"""

import hashlib
import io
import json
import os
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_edge_forwarder import _forwarder  # noqa: E402
from test_store_locking import FAST, _hold, _stop  # noqa: E402
from test_store_reproducible import (  # noqa: E402
    _assert_canonical,
    _frame,
    _old_style_write,
    _parts,
    _threaded_legacy_write,
)

from camber.cli import main  # noqa: E402
from camber.edge import collect_sink  # noqa: E402
from camber.edge import landing as _landing  # noqa: E402
from camber.portfolio import Portfolio, _bundle, _retention  # noqa: E402
from camber.portfolio import _lock as plock  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store import _normalize as N  # noqa: E402
from camber.store._lock import StoreLocked  # noqa: E402
from camber.store.parquet_store import role_frame_to_long  # noqa: E402

_COLS = ["ts", "equip", "equip_class", "role", "value", "facility_id", "year", "month"]


class _Crash(BaseException):
    """A simulated crash: not an Exception, so no cleanup handler runs (as on kill -9)."""


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(plock.LOCK_TIMEOUT_ENV, raising=False)


def _longs():
    a = role_frame_to_long(_frame(periods=24 * 5), equip="AHU_1", equip_class="AHU")
    b = role_frame_to_long(_frame("2024-01-31", periods=24 * 3), equip="AHU_2", equip_class="")
    return a, b


def _legacy_store(root: str, fid: str = "f1") -> ParquetStore:
    """Two pre-0.103 writes, each spanning January and February: four non-canonical parts."""
    a, b = _longs()
    st = ParquetStore(root)
    _old_style_write(root, a, fid, st._next_seq(fid))
    _old_style_write(root, b, fid, st._next_seq(fid))  # the seq write_long would have used
    return st


def _land_edge(root: str, tmp_path, fid: str = "demo-fac-1") -> list:
    """Land one forwarder batch into the store at its key, as a presigned PUT does."""

    class _Landing:
        def put(self, key, data, *, content_type="", metadata=None):
            p = os.path.join(root, key)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
            return {"ok": True}

    return _forwarder(tmp_path / f"spool-{fid}", _Landing()).poll_once().keys


def _files(root: str) -> dict:
    """``{relative path: (bytes, mtime_ns)}`` of every file under ``root`` (but the catalog and the
    lock file)."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.startswith(("_catalog", "_lock")):
                continue
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = (fh.read(), os.stat(p).st_mtime_ns)
    return out


def _rows(st: ParquetStore) -> pd.DataFrame:
    df = st.read_long()
    cols = [c for c in _COLS if c in df.columns]
    return df[cols].sort_values(["facility_id", "ts", "equip", "role"]).reset_index(drop=True)


def _same_rows(st, want):
    got = _rows(st)
    assert dict(got.dtypes) == dict(want.dtypes)
    pd.testing.assert_frame_equal(got, want)


def _leftovers(root: str) -> list:
    return [
        os.path.join(d, n)
        for d, dirs, _f in os.walk(root)
        for n in dirs
        if n.startswith(("_trash-", "_swap-"))
    ]


# --------------------------------------------------------------------------- verify


def test_verify_classifies_canonical_legacy_edge_and_year_only_parts(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    st.write_role_frame(_frame("2024-03-01", periods=24), facility_id="f1", equip="AHU_3")
    _threaded_legacy_write(root, _frame("2022-03-01", periods=24))  # facility "old", year-only
    (edge_key,) = _land_edge(root, tmp_path)
    r = st.verify()
    c = r["counts"]
    assert c["parts"] == 4 + 1 + 1 + 1
    assert (c["canonical"], c["noncanonical"], c["unreadable"]) == (1, 6, 0)
    assert (c["legacy"], c["edge"], c["rollup"]) == (5, 1, 0)
    assert r["ok"] and r["stores"] == ["store"]
    by_path = {p["path"]: p for p in r["parts"]}
    edge = by_path[edge_key]
    assert (
        edge["origin"] == "edge" and not edge["canonical"] and "pandas metadata" in edge["problems"]
    )
    legacy = by_path["facility_id=f1/year=2024/month=1/part-0-0.parquet"]
    assert legacy["origin"] == "store" and not legacy["marker"]
    assert "no camber.layout marker" in legacy["problems"]
    (year_only,) = [p for p in r["parts"] if p["facility_id"] == "old"]
    assert year_only["month"] is None
    (new,) = [p for p in r["parts"] if p["month"] == 3]
    assert new["canonical"] and new["problems"] == [] and new["marker"]
    # one facility only
    assert st.verify(facility_id="old")["counts"]["parts"] == 1


def test_verify_flags_each_layout_problem(tmp_path):
    a, _b = _longs()
    t = pa.Table.from_pandas(a, preserve_index=False)
    path = str(tmp_path / "p.parquet")

    def check(table, **kw):
        pq.write_table(table, path, **kw)
        return N.check_part(path)

    from camber.store.parquet_store import _canonicalize, _write_part

    canon = _canonicalize(t)
    _write_part(canon, path)
    assert N.check_part(path)["canonical"]
    r = check(canon.slice(0).take(list(range(canon.num_rows))[::-1]))
    assert r["problems"] == ["rows not sorted by (ts, equip, role, equip_class, value)"]
    r = check(canon, row_group_size=100)
    assert r["problems"] == [f"{-(-canon.num_rows // 100)} row groups (canonical: 1)"]
    r = check(canon.select(["value", "ts", "equip", "equip_class", "role"]))
    assert r["problems"] == ["columns not in canonical order"]
    r = check(canon.replace_schema_metadata({b"camber.layout": b"2"}))
    assert r["problems"] == ["camber.layout is b'2', not b'1'"]
    r = check(canon.replace_schema_metadata({**canon.schema.metadata, b"x": b"y"}))
    assert r["problems"] == ["other schema metadata: x"]
    with open(path, "wb") as fh:
        fh.write(b"not a parquet file")
    r = N.check_part(path)
    assert r["error"] and not r["canonical"]


def test_verify_counts_unreadable_parts_and_the_cli_exit_codes(tmp_path, capsys):
    root = str(tmp_path / "store")
    _legacy_store(root)
    assert main(["store", "verify", root]) == 0  # readable, not canonical
    assert main(["store", "verify", root, "--strict"]) == 1
    out = capsys.readouterr().out
    assert "4 part(s): 0 canonical, 4 not canonical, 0 unreadable" in out
    assert "camber store normalize" in out
    bad = os.path.join(root, "facility_id=f1", "year=2024", "month=1", "part-9-0.parquet")
    with open(bad, "wb") as fh:
        fh.write(b"garbage")
    assert main(["store", "verify", root, "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["counts"]["unreadable"] == 1 and doc["ok"] is False
    (row,) = [p for p in doc["parts"] if p["error"]]
    assert row["path"].endswith("part-9-0.parquet")
    # normalize leaves the unreadable part alone and says so
    r = ParquetStore(root).normalize()
    assert {s["reason"] for s in r["skipped"]} == {N.UNREADABLE_REASON}
    ParquetStore(root).normalize(apply=True)
    assert open(bad, "rb").read() == b"garbage"
    assert main(["store", "verify", root, "--strict"]) == 1


def test_verify_takes_no_lock(tmp_path):
    root = str(tmp_path / "store")
    _legacy_store(root)
    proc = _hold("store", root)
    try:
        t0 = time.monotonic()
        assert ParquetStore(root).verify()["counts"]["parts"] == 4
        assert ParquetStore(root, lock_timeout=0).normalize()["parts"] == 4  # a dry run neither
        assert time.monotonic() - t0 < FAST
    finally:
        _stop(proc)


# --------------------------------------------------------------------------- normalize


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = str(tmp_path / "store")
    _legacy_store(root)
    before = _files(root)
    r = ParquetStore(root).normalize()
    assert r["dry_run"] and not r["applied"] and r["parts"] == 4 and r["bytes_after"] is None
    assert [(p["year"], p["month"], p["parts"]) for p in r["partitions"]] == [
        (2024, 1, 2),
        (2024, 2, 2),
    ]
    assert main(["store", "normalize", root]) == 0
    assert main(["store", "normalize", root, "--dry-run"]) == 0
    assert "dry run -- nothing changed" in capsys.readouterr().out
    assert _files(root) == before
    assert main(["store", "normalize", root, "--apply"]) == 1  # --apply needs --yes
    assert _files(root) == before


def test_apply_is_content_identical_byte_canonical_and_keeps_names(tmp_path, capsys):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    want = _rows(st)
    names = sorted(_parts(root))
    assert main(["store", "normalize", root, "--apply", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "applied" in out and "total: 4 part(s)" in out
    _same_rows(st, want)
    assert sorted(_parts(root)) == names  # rewritten in place, under the same names
    for blob in _parts(root).values():
        _assert_canonical(blob)
    # byte for byte what write_long writes for the same rows today
    fresh = str(tmp_path / "fresh")
    a, b = _longs()
    ParquetStore(fresh).write_long(a, facility_id="f1")
    ParquetStore(fresh).write_long(b, facility_id="f1")
    assert _parts(fresh) == _parts(root)
    assert ParquetStore(root).verify()["counts"]["canonical"] == 4
    assert main(["store", "verify", root, "--strict"]) == 0
    assert _leftovers(root) == []
    # the wide read and the catalog agree
    assert ParquetStore(root).equipment() == {"f1": {"AHU_1": "AHU", "AHU_2": ""}}


def test_apply_reports_bytes_before_and_after(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    before = {k: len(v) for k, v in _parts(root).items()}
    r = st.normalize(apply=True)
    after = {k: len(v) for k, v in _parts(root).items()}
    assert r["applied"] and r["bytes_before"] == sum(before.values())
    assert r["bytes_after"] == sum(after.values())
    assert sum(p["bytes_after"] for p in r["partitions"]) == r["bytes_after"]
    assert r["rows"] == len(_rows(st))


def test_normalize_is_idempotent(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    st.normalize(apply=True)
    once = _files(root)
    r = st.normalize(apply=True)
    assert r["parts"] == 0 and not r["applied"] and r["partitions"] == []
    assert _files(root) == once


def test_normalize_invalidates_the_catalog(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    st.points()  # cache a catalog
    gen = (
        open(os.path.join(root, "_catalog.gen")).read()
        if os.path.exists(os.path.join(root, "_catalog.gen"))
        else None
    )
    st.normalize(apply=True)
    assert not os.path.exists(os.path.join(root, "_catalog.json"))
    assert open(os.path.join(root, "_catalog.gen")).read() != gen
    assert len(st.points()) == 6  # two equipment, three roles


def test_canonical_parts_and_other_facilities_are_left_alone(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    st.write_role_frame(_frame("2024-03-01", periods=24), facility_id="f1", equip="AHU_3")
    _legacy_store(root, fid="f2")
    mdir = os.path.join(root, "facility_id=f1", "year=2024", "month=3")
    (canon,) = [os.path.join(mdir, n) for n in os.listdir(mdir)]
    keep = os.stat(canon).st_mtime_ns
    f2 = {k: v for k, v in _files(root).items() if "facility_id=f2" in k}
    r = st.normalize(facility_id="f1", apply=True)
    assert r["parts"] == 4 and {p["facility_id"] for p in r["partitions"]} == {"f1"}
    assert os.stat(canon).st_mtime_ns == keep
    assert {k: v for k, v in _files(root).items() if "facility_id=f2" in k} == f2


def test_edge_and_year_only_parts_are_left_alone(tmp_path):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    _threaded_legacy_write(root, _frame("2022-03-01", periods=24))
    (edge_key,) = _land_edge(root, tmp_path)
    keep = {k: v for k, v in _files(root).items() if "facility_id=f1" not in k}
    want = _rows(st)
    r = st.normalize(apply=True)
    assert r["parts"] == 4
    reasons = {(s["facility_id"], s["reason"]) for s in r["skipped"]}
    assert reasons == {("demo-fac-1", N.EDGE_REASON), ("old", N.YEAR_ONLY_REASON)}
    assert {k: v for k, v in _files(root).items() if "facility_id=f1" not in k} == keep
    _same_rows(st, want)
    # the edge part still passes the landing's hash check: its name is its bytes' sha256
    lk, _why = _landing.parse_landed_key(edge_key)
    blob = open(os.path.join(root, edge_key), "rb").read()
    assert hashlib.sha256(blob).hexdigest()[:16] == lk.sha16


# --------------------------------------------------------------------------- crash safety


def _crash_at(monkeypatch, step: str, nth: int = 2):
    """Make the ``nth`` call of ``step`` crash."""
    calls = {"n": 0}

    def wrap(fn):
        def inner(*a, **kw):
            calls["n"] += 1
            if calls["n"] == nth:
                raise _Crash(step)
            return fn(*a, **kw)

        return inner

    target = {
        "write": "_write_part",
        "readback": "_read_part_file",
        "replace": "_replace",
        "remove_stage": "_remove_stage",
        "invalidate": "_invalidate_catalog_at",
    }[step]
    monkeypatch.setattr(N, target, wrap(getattr(N, target)))


@pytest.mark.parametrize(
    "step,nth",
    [
        ("write", 1),
        ("write", 2),
        ("readback", 2),  # the first read is of the original part
        ("readback", 4),
        ("replace", 1),
        ("replace", 2),
        ("remove_stage", 1),
        ("remove_stage", 2),
        ("invalidate", 1),
    ],
)
def test_a_crash_at_any_step_loses_or_duplicates_no_row(tmp_path, monkeypatch, step, nth):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    want = _rows(st)
    with monkeypatch.context() as m:
        _crash_at(m, step, nth)
        with pytest.raises(_Crash):
            st.normalize(apply=True)
    _same_rows(st, want)  # straight after the crash, before any recovery
    assert len(st.read_long()) == len(want)
    r = st.normalize(apply=True)  # the next run recovers and finishes
    assert r["applied"] or r["parts"] == 0
    _same_rows(st, want)
    assert _leftovers(root) == []
    for blob in _parts(root).values():
        _assert_canonical(blob)
    assert st.normalize()["parts"] == 0


def test_a_part_that_does_not_read_back_identical_changes_nothing(tmp_path, monkeypatch):
    root = str(tmp_path / "store")
    st = _legacy_store(root)
    before = _files(root)
    monkeypatch.setattr(N, "_same", lambda back, canon: False)
    with pytest.raises(OSError, match="did not read back identical"):
        st.normalize(apply=True)
    assert _files(root) == before and _leftovers(root) == []


# --------------------------------------------------------------------------- the lock


def test_apply_holds_the_store_lock(tmp_path, capsys):
    root = str(tmp_path / "store")
    _legacy_store(root)
    before = _files(root)
    proc = _hold("store", root)
    try:
        t0 = time.monotonic()
        with pytest.raises(StoreLocked):
            ParquetStore(root, lock_timeout=0).normalize(apply=True)
        rc = main(["store", "normalize", root, "--apply", "--yes", "--lock-timeout", "0"])
        assert rc == 1 and time.monotonic() - t0 < FAST
        assert f"is locked by {proc.pid}@" in capsys.readouterr().err
    finally:
        _stop(proc)
    assert _files(root) == before
    assert main(["store", "normalize", root, "--apply", "--yes"]) == 0


def test_the_rewrite_runs_inside_the_lock(tmp_path, monkeypatch):
    from camber.portfolio._lock import _slot

    root = str(tmp_path / "store")
    st = _legacy_store(root)
    seen = []
    real = N._normalize_partition

    def spy(d, names):
        seen.append(_slot(os.path.realpath(os.path.join(root, "_lock"))).depth > 0)
        return real(d, names)

    monkeypatch.setattr(N, "_normalize_partition", spy)
    st.normalize(apply=True)
    assert seen == [True, True]


# --------------------------------------------------------------------------- workspaces


def _old_rollup(root: str, fid: str, covers: list) -> str:
    """A retention rollup part as written before 0.103: pandas metadata, unsorted, small groups."""
    long = role_frame_to_long(_frame(periods=24 * 2), equip="AHU_1", equip_class="AHU")
    roll = _retention._rollup(long, "hourly").iloc[::-1].reset_index(drop=True)
    t = pa.Table.from_pandas(roll, preserve_index=False)
    t = t.replace_schema_metadata(
        {**t.schema.metadata, b"camber.rollup.covers": json.dumps(covers).encode()}
    )
    d = os.path.join(root, f"facility_id={fid}", "year=2024", "month=1")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "part-0123456789abcdef.parquet")
    pq.write_table(t, path, row_group_size=10)
    return path


def _workspace(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    for name in ("act", "sus", "held", "gone"):
        pf.add_facility(name, facility_id=name, reason="t", activate=True)
        _legacy_store(pf.store_root, fid=name)
    pf.transition("sus", "suspend", reason="t")
    pf.hold("held", reason="litigation")
    pf.offboard("gone", reason="t", apply=True)
    pf.archive("gone", reason="t", apply=True, skip_grace=True)
    _legacy_store(pf.store_root, fid="gone")  # an edge PUT recreated the partition
    return pf


def test_workspace_normalize_skips_facilities_that_do_not_accept_data(tmp_path, capsys):
    pf = _workspace(tmp_path)
    hourly = os.path.join(pf.root, _bundle.ROLLUPS_DIR, "hourly")
    covers = ["year=2024/month=1/part-0-0.parquet"]
    rollup = _old_rollup(hourly, "act", covers)
    rollup_held = _old_rollup(hourly, "held", covers)
    held_bytes = open(rollup_held, "rb").read()
    st = pf.store
    want = _rows(st)
    want_roll = _rows(ParquetStore(hourly))
    others = {k: v for k, v in _files(pf.store_root).items() if "facility_id=act" not in k}
    with pytest.raises(ValueError, match="reason is required"):
        st.normalize(apply=True)
    assert main(["store", "normalize", pf.store_root, "--apply", "--yes"]) == 1
    r = st.normalize(apply=True, reason="0.104 canonical layout")
    assert r["stores"] == ["store", "rollups/hourly"]
    assert {(p["store"], p["facility_id"]) for p in r["partitions"]} == {
        ("store", "act"),
        ("rollups/hourly", "act"),
    }
    reasons = {(s["store"], s["facility_id"]): s["reason"] for s in r["skipped"]}
    assert reasons[("store", "sus")].startswith("suspended")
    assert reasons[("store", "held")].startswith("under a legal hold")
    assert reasons[("rollups/hourly", "held")].startswith("under a legal hold")
    assert reasons[("store", "gone")].startswith("archived")
    _same_rows(st, want)
    _same_rows(ParquetStore(hourly), want_roll)
    assert {k: v for k, v in _files(pf.store_root).items() if "facility_id=act" not in k} == (
        others
    )
    assert open(rollup_held, "rb").read() == held_bytes
    meta = _assert_canonical(open(rollup, "rb").read())
    assert json.loads(meta[b"camber.rollup.covers"]) == covers  # kept, so retention still reads it
    assert _retention._covers_of(rollup) == set(covers)
    (rec,) = [a for a in pf.audit_log() if a["action"] == "store.normalize"]
    assert rec["reason"] == "0.104 canonical layout"
    assert rec["details"]["parts"] == 5 and rec["details"]["stores"] == ["store", "rollups/hourly"]
    # one skipped facility asked for by name is refused
    for fid, why in (("sus", "suspended"), ("held", "legal hold"), ("gone", "archived")):
        with pytest.raises(ValueError, match=why):
            st.normalize(facility_id=fid)
        assert main(["store", "normalize", pf.store_root, "--facility", fid]) == 1
        assert why in capsys.readouterr().err
    # idempotent, and a run with nothing to do writes no audit record
    assert st.normalize(apply=True, reason="again")["parts"] == 0
    assert len([a for a in pf.audit_log() if a["action"] == "store.normalize"]) == 1


def test_a_workspace_rollup_store_given_directly_uses_the_workspace_lock(tmp_path):
    pf = Portfolio.init(tmp_path / "ws")
    pf.add_facility("act", facility_id="act", reason="t", activate=True)
    hourly = os.path.join(pf.root, _bundle.ROLLUPS_DIR, "hourly")
    _old_rollup(hourly, "act", ["year=2024/month=1/part-0-0.parquet"])
    r = N.verify(hourly)
    assert r["workspace"] == pf.root and r["stores"] == ["rollups/hourly"]
    assert r["counts"]["rollup"] == 1 and r["counts"]["noncanonical"] == 1
    with pytest.raises(ValueError, match="reason is required"):
        N.normalize(hourly, apply=True)
    proc = _hold("store", pf.store_root)  # the workspace's lock
    try:
        with pytest.raises(plock.PortfolioLocked):
            N.normalize(hourly, apply=True, reason="t", lock_timeout=0)
    finally:
        _stop(proc)
    assert N.normalize(hourly, apply=True, reason="t")["parts"] == 1
    assert not os.path.exists(os.path.join(hourly, "_lock"))  # no lock file of its own


def test_lifecycle_constants_match_their_sources():
    assert N._ACCEPTING == _landing.ACCEPTING_STATES
    assert N._ROLLUP_FREQS == tuple(_retention.ROLLUP_FREQS)
    assert N._ROLLUPS_DIR == _bundle.ROLLUPS_DIR
    assert N._COVERS_KEY == _retention._COVERS_KEY


# --------------------------------------------------------------------------- edge parts (decision)


def test_the_forwarder_still_writes_content_addressed_non_canonical_parts(tmp_path):
    """The 0.104 decision (#136): edge parts keep their pre-0.103 serialization, so a batch an
    upgraded forwarder re-sends lands on the key the old forwarder used, never on a second one."""
    sink, log = collect_sink()
    _forwarder(tmp_path / "a", sink).poll_once()
    _forwarder(tmp_path / "b", sink).poll_once()
    first, again = log
    assert first["key"] == again["key"] and first["data"] == again["data"]
    sha = hashlib.sha256(first["data"]).hexdigest()
    assert first["key"].endswith(f"part-{sha[:16]}.parquet")
    assert first["metadata"]["content_sha256"] == sha
    meta = pq.ParquetFile(io.BytesIO(first["data"])).schema_arrow.metadata or {}
    assert b"pandas" in meta and b"camber.layout" not in meta


def test_a_resent_edge_batch_after_normalize_adds_no_rows(tmp_path):
    root = str(tmp_path / "store")
    _legacy_store(root)
    keys = _land_edge(root, tmp_path)
    st = ParquetStore(root)
    st.normalize(apply=True)
    n = len(st.read_long())
    assert _land_edge(root, tmp_path) == keys  # the same batch, the same key: overwritten
    assert len(st.read_long()) == n
