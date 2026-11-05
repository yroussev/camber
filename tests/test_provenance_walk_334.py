"""0.112 (#334): the provenance walk checks every file a store read could open.

- An entry the walk cannot read or resolve (a dangling symlink, an unreadable directory) no longer
  stops it: the files after it are still checked, and the facility is refused because not every
  file could be checked, never served.
- A facility nested too deep or holding a symlink loop is refused for that reason, not as
  "marked research-only", and ``datasets provenance`` invents no marked part for it.
- ``store verify`` lists every file the store reads (the provenance check's definition of a part
  file) and every entry that keeps a facility from being checked, and fails when the store would.
- The ``datasets provenance`` table says when a facility counts as research-only without naming a
  research-only dataset, as ``--json`` and the server do.

Every store is a small one in its own tmp directory; nothing reaches the network.
"""

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_store_provenance_319 as p319  # noqa: E402
from test_store_provenance_319 import NC, OPEN, REG, _copy, _ctx, _refused, _write  # noqa: E402

from camber import _servable  # noqa: E402
from camber.capabilities import CapabilityError  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ops  # noqa: E402
from camber.store import ParquetStore, _normalize, _partprov  # noqa: E402

store = p319.store  # the fixture: a research-only facility and the user's own ("hq")
NCF = "ds-at-30bldg-sensors-b02"


@pytest.fixture(autouse=True)
def _fresh_memos(monkeypatch):
    _partprov._MARKS.clear()
    _servable._SEEN.clear()
    monkeypatch.setattr(_servable, "_RETRY_WAITS", (0.0, 0.001, 0.001, 0.001))


def _fdir(root, fid):
    return os.path.join(root, f"facility_id={fid}")


def _dangle(root, fid, rel, tmp_path):
    """A symlink at ``rel`` in the facility to a path that does not exist."""
    link = os.path.join(_fdir(root, fid), rel)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(str(tmp_path / "does-not-exist"), link)
    return link


def _unlink(path):
    if os.path.lexists(path):
        os.unlink(path)


def _row(root, fid):
    return next(r for r in _ops.store_provenance(root) if r["facility_id"] == fid)


def _unverified(ctx, fid, cap="points"):
    e = _refused(ctx, fid, cap)
    assert e.data == {"facility_id": fid, "reason": "licence_unverified"}
    assert "not all of its files could be checked" in e.message
    assert "marked as research-only" not in e.message
    return e


# ------------------------------------------------------------------ the walk goes on


@pytest.mark.parametrize("rel", ["year=2000/month=1/x.parquet", "year=2000/month=1/sub", "x"])
def test_a_dangling_symlink_does_not_hide_the_research_only_parts_after_it(store, tmp_path, rel):
    from camber.config import data_sources

    link = _dangle(store.root, "hq", rel, tmp_path)
    try:
        _copy(store.root, NCF, store.root, "hq", rename=lambda r: r.replace("part-", "zz-"))
        marks = _partprov.facility_marks(store.root, "hq")
        assert marks.get(NC) and marks.get(_partprov.UNCHECKED) == 1
        assert _servable.refusal("hq", store.root)[0] == "research_only"
        ctx = _ctx(store.root, {"hq"})
        res = REG.call("facilities", {}, ctx)
        assert res.data["facilities"] == [] and res.data["withheld_research_only"] == 1
        for args in ({}, {"equip_class": "AHU"}):
            with pytest.raises(CapabilityError) as e:
                REG.call("points", {"facility_id": "hq", **args}, ctx)
            assert e.value.code == "refused_licence"
        row = _row(store.root, "hq")
        assert row["research_only"] and row["marked"] == marks[NC] and row["unchecked"] == 1
        assert row["research_only_datasets"] == ["test-ahu"]
        cfg = {"facility_id": "hq", "source": {"kind": "store", "store": store.root}}
        cfg["source"]["facility_id"] = "hq"
        srcs = data_sources(cfg)  # the report's banner and licence block
        assert {s["access"] for s in srcs} == {"research_only"}
        assert "test-ahu" in [s.get("dataset_id") for s in srcs]
    finally:
        _unlink(link)


def test_a_dangling_symlink_in_open_data_refuses_as_unchecked_until_removed(store, tmp_path):
    n = len(p319._parts(store.root, "hq"))
    link = _dangle(store.root, "hq", "year=2024/month=6/zz.parquet", tmp_path)
    try:
        assert _servable.refusal("hq", store.root)[0] == "unverified"
        ctx = _ctx(store.root, {"hq"})
        res = REG.call("facilities", {}, ctx)
        assert res.data["facilities"] == [] and res.data["withheld_unverified"] == 1
        assert any("could be checked" in b for b in res.banners)
        for cap in ("points", "coverage"):
            _unverified(ctx, "hq", cap)
        # the read-time check inside a served read says the same
        with _partprov.serving(), pytest.raises(CapabilityError) as e:
            store.read_long(facility_id="hq")
        assert "not all of its files could be checked" in e.value.message
        row = _row(store.root, "hq")
        assert (row["marked"], row["unchecked"], row["research_only"]) == (0, 1, True)
        assert row["parts"] == n  # the link is no part
    finally:
        _unlink(link)
    assert _servable.refusal("hq", store.root) is None  # the signature saw it go
    assert REG.call("points", {"facility_id": "hq"}, _ctx(store.root, {"hq"})).data["count"] == 3


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads mode 000")
def test_an_unreadable_directory_is_skipped_and_fails_closed(store):
    _copy(store.root, NCF, store.root, "hq", rename=lambda r: r.replace("part-", "zz-"))
    _write(store, "own", None)
    blocked = [os.path.join(_fdir(store.root, f), "year=2000") for f in ("hq", "own")]
    for d in blocked:
        os.makedirs(d)
        os.chmod(d, 0)
    try:
        marks = _partprov.facility_marks(store.root, "hq")
        assert marks.get(NC) and _partprov.UNCHECKED in marks  # the walk went on past it
        assert _servable.refusal("hq", store.root)[0] == "research_only"
        assert _servable.refusal("own", store.root)[0] == "unverified"
        _unverified(_ctx(store.root, {"own"}), "own")
    finally:
        for d in blocked:
            os.chmod(d, 0o755)


# ------------------------------------------------------------------ depth and loops


def test_open_data_nested_too_deep_is_unchecked_not_research_only(store, capsys):
    deep = os.path.join(*["d"] * (_partprov._MAX_DEPTH + 1))
    _copy(store.root, "hq", store.root, "hq", rename=lambda r: os.path.join(deep, r))
    assert _servable.refusal("hq", store.root)[0] == "unverified"
    _unverified(_ctx(store.root, {"hq"}), "hq")
    row = _row(store.root, "hq")
    assert row["marked"] == 0 and row["unchecked"] == 1 and row["research_only"]
    assert row["parts"] == len(p319._parts(store.root, "hq")) // 2  # the deep copies not seen
    assert main(["datasets", "provenance", "--store", store.root]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("hq "))
    assert "not all files could be checked: counts as research-only" in line


def test_a_symlink_loop_is_unchecked_and_listed_once(store):
    n = len(p319._parts(store.root, "hq"))
    loop = os.path.join(_fdir(store.root, "hq"), "year=2024", "month=6", "loop")
    os.symlink("..", loop)
    try:
        files, bad = _partprov.scan(_fdir(store.root, "hq"))
        assert [why for _p, why in bad] == ["a symlink loop"]
        assert len(files) == n  # no file counted twice
        assert _servable.refusal("hq", store.root)[0] == "unverified"
        assert _row(store.root, "hq")["marked"] == 0
    finally:
        _unlink(loop)


def test_a_change_below_the_month_reaches_a_cached_check(store):
    assert _servable.refusal("hq", store.root) is None  # cached as open
    _copy(store.root, NCF, store.root, "hq", rename=lambda r: os.path.join("a", "b", r))
    assert _servable.refusal("hq", store.root)[0] == "research_only"


# ------------------------------------------------------------------ store verify


@pytest.mark.parametrize(
    "rel, readable",
    [
        ("year=2024/month=6/notes.txt", False),
        ("year=2024/month=6/Icon\r", False),
        ("README", False),
        ("year=2024/month=6/x.parquet~", True),
        ("x.parquet", True),
        ("year=2024/month=6/a/b/x.parquet", True),
    ],
)
def test_store_verify_lists_every_file_the_store_reads(store, rel, readable, capsys):
    path = os.path.join(_fdir(store.root, "hq"), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if readable:
        shutil.copy(p319._parts(store.root, "hq")[0], path)
    else:
        with open(path, "w") as fh:
            fh.write("not parquet")
    r = _normalize.verify(store.root, facility_id="hq")
    (row,) = [p for p in r["parts"] if p["path"].endswith(rel)]
    assert r["counts"]["parts"] == len(_partprov.scan(_fdir(store.root, "hq"))[0])
    assert not row["canonical"] and "the store reads it" in row["problems"][0]
    assert (row["error"] is None) == readable and r["ok"] == readable
    assert main(["store", "verify", store.root, "--facility", "hq"]) == (0 if readable else 1)
    assert rel.split("/")[-1].strip() in capsys.readouterr().out
    # the store does read it: an unreadable one takes the facility's reads down
    if not readable:
        from camber.store.parquet_store import StoreReadError

        with pytest.raises(StoreReadError):
            store.read_long(facility_id="hq")


def test_store_verify_counts_each_part_once_and_reports_unchecked_entries(store, tmp_path):
    r = _normalize.verify(store.root)
    assert r["ok"] and r["counts"]["parts"] == len(p319._parts(store.root))
    link = _dangle(store.root, "hq", "year=2000/month=1/x.parquet", tmp_path)
    try:
        r = _normalize.verify(store.root, facility_id="hq")
        (bad,) = [p for p in r["parts"] if p["error"]]
        assert bad["path"].endswith("year=2000/month=1/x.parquet")
        assert "not every file could be checked" in bad["error"] and not r["ok"]
        assert main(["store", "verify", store.root]) == 1
    finally:
        _unlink(link)


# ------------------------------------------------------------------ the provenance table


def test_the_table_tags_a_mark_that_cannot_be_read(tmp_path, capsys):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "padded", OPEN[:-1] + b" " * 4100 + b"}")
    _write(st, "open", OPEN)
    assert main(["datasets", "provenance", "--store", st.root]) == 0
    out = {x.split()[0]: x for x in capsys.readouterr().out.splitlines() if x.strip()}
    assert "[mark unreadable: counts as research-only]" in out["padded"]
    assert "research-only" not in out["open"] and "[lbnl-fcu]" in out["open"]
    assert main(["datasets", "provenance", "--store", st.root, "--json"]) == 0
    rows = {r["facility_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["padded"]["research_only"] and rows["padded"]["unreadable_marks"] > 0
    assert not rows["open"]["research_only"] and rows["open"]["unreadable_marks"] == 0
    _refused(_ctx(st.root, {"padded"}), "padded")


def test_a_file_removed_between_the_listing_and_its_stat_is_skipped_not_unchecked(
    store, monkeypatch
):
    """A re-ingest can swap a file out after the walk listed it: no read can open it any more,
    so it is skipped, not counted as unchecked (which refused the facility mid-swap). A
    dangling link, still there to ``lstat``, stays unchecked."""
    fdir = _fdir(store.root, "hq")
    files, bad = _partprov.scan(fdir)
    assert files and not bad
    gone, real_stat = files[0][0], os.stat
    dangling = os.path.join(os.path.dirname(gone), "dangling.parquet")
    os.symlink(os.path.join(os.path.dirname(gone), "missing.parquet"), dangling)

    removed = []

    def stat(path, *a, **k):
        if path == gone and not removed:
            removed.append(path)
            os.remove(gone)  # the swap lands between scandir and stat
        return real_stat(path, *a, **k)

    monkeypatch.setattr(_partprov.os, "stat", stat)
    try:
        files2, bad2 = _partprov.scan(fdir)
    finally:
        _unlink(dangling)
    assert [p for p, *_ in files2] == [p for p, *_ in files[1:]]
    assert removed == [gone]
    assert bad2 == [(dangling, "cannot be resolved (a dangling symlink or a loop?)")]
