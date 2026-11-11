"""0.112 (#329): the provenance and registry gaps the 0.112 acceptance test found.

- A research-only part saved under a name without the ``.parquet`` suffix (``.parq``,
  ``.PARQUET``, no suffix, ``.parquet.tmp``) is read by the store, so the provenance check sees it
  too: one definition of a part file (:func:`camber.store._partprov.is_part_name`).
- Archiving a research-only facility does not take the rest of the store offline.
- A deeply nested provenance mark or registry is unreadable (research-only / a registry problem),
  never a ``RecursionError``.
- A registry that keeps breaking while it is read is ``unavailable``, never a licence refusal; one
  that is lastingly a directory, unreadable or a symlink loop is the registry problem.
- A local report built from marked research-only parts carries the banner and licence block.

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
from test_store_provenance_319 import (  # noqa: E402
    NC,
    OPEN,
    REG,
    _copy,
    _ctx,
    _parts,
    _refused,
    _workspace,
    _write,
)

from camber import _servable  # noqa: E402
from camber.capabilities import CapabilityError  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.datasets import _ops  # noqa: E402
from camber.store import ParquetStore, _partprov  # noqa: E402

store = p319.store  # the fixture: a research-only facility and the user's own
NCF = "ds-at-30bldg-sensors-b02"
DEEP = b"[" * 50000 + b"]" * 50000


@pytest.fixture(autouse=True)
def _fresh_memos(monkeypatch):
    _partprov._MARKS.clear()
    _servable._SEEN.clear()
    monkeypatch.setattr(_servable, "_RETRY_WAITS", (0.0, 0.001, 0.001, 0.001))


# ------------------------------------------------------------------ odd part-file names


@pytest.mark.parametrize("name", ["x.parq", "x.PARQUET", "x", "x.parquet.tmp", "x.Parquet"])
def test_a_research_only_part_under_an_odd_name_is_checked(store, name):
    _copy(
        store.root,
        NCF,
        store.root,
        "hq",
        rename=lambda r: os.path.join(os.path.dirname(r), os.path.basename(r) + "-" + name),
    )
    # the store reads these files (the bypass needed exactly that)
    assert len(store.read_long(facility_id="hq")) > len(store.read_long(facility_id=NCF))
    marks = _partprov.facility_marks(store.root, "hq")
    assert marks.get(NC) == len(_parts(store.root, NCF))
    assert _servable.refusal("hq", store.root)[0] == "research_only"
    ctx = _ctx(store.root, {"hq"})
    res = REG.call("facilities", {}, ctx)
    assert res.data["facilities"] == [] and res.data["withheld_research_only"] == 1
    for cap in ("points", "coverage"):
        _refused(ctx, "hq", cap)
    with pytest.raises(CapabilityError):
        REG.call("points", {"facility_id": "hq", "equip_class": "AHU"}, ctx)
    row = next(r for r in _ops.store_provenance(store.root) if r["facility_id"] == "hq")
    assert row["state"] == "mixed" and row["research_only"]


@pytest.mark.parametrize("prefix", [".", "_"])
def test_a_hidden_file_is_neither_read_nor_checked(store, prefix):
    _copy(store.root, NCF, store.root, "hq", rename=lambda r: r.replace("part-", prefix + "part-"))
    assert None in _partprov.facility_marks(store.root, "hq")
    assert NC not in _partprov.facility_marks(store.root, "hq")
    assert _servable.refusal("hq", store.root) is None
    assert REG.call("points", {"facility_id": "hq"}, _ctx(store.root, {"hq"})).data["count"] == 3


def test_a_part_nested_deeper_than_the_check_goes_fails_closed(store):
    deep = os.path.join(*["d"] * (_partprov._MAX_DEPTH + 1))
    _copy(store.root, "hq", store.root, "hq", rename=lambda r: os.path.join(deep, r))
    assert _partprov.UNCHECKED in _partprov.facility_marks(store.root, "hq")
    _refused(_ctx(store.root, {"hq"}), "hq")


def test_the_copy_check_lists_odd_names_as_parts(store):
    _copy(store.root, NCF, store.root, "hq2", rename=lambda r: r + ".parq")
    assert _servable._part_files(store.root, "hq2") == sorted(
        os.path.relpath(p, os.path.join(store.root, f"facility_id={NCF}")) + ".parq"
        for p in _parts(store.root, NCF)
    )


# ------------------------------------------------------------------ archive


def test_archiving_a_research_only_facility_keeps_the_others_served(tmp_path):
    from camber.datasets import get

    pf, fid = _workspace(tmp_path, "2025-01-01", 24 * 40)
    pf.registry._update(fid, {"dataset": get("at-30bldg-sensors").provenance()})
    other = pf.add_facility("South", reason="x", activate=True)["facility_id"]
    _write(pf.store, other, OPEN, v=60.0)
    pf.offboard(fid, reason="ended", apply=True)
    pf.archive(fid, reason="done", apply=True, skip_grace=True)
    assert fid not in pf.store.facilities()
    entries, problem = _servable.read_registry(pf.store_root)
    assert problem is None and entries[fid]["state"] == "archived"
    assert _servable.refusal(other, pf.store_root) is None
    assert _servable.refusal(fid, pf.store_root)[0] == "research_only"
    ctx = _ctx(pf.store_root, {other, fid})
    assert REG.call("points", {"facility_id": other}, ctx).data["count"] == 3
    _refused(ctx, fid)
    # a research-only entry whose directory went missing otherwise is still the #312 problem
    shutil.rmtree(os.path.join(pf.store_root, f"facility_id={other}"))
    pf.registry._update(other, {"dataset": get("at-30bldg-sensors").provenance()})
    assert "directory is missing" in _servable.read_registry(pf.store_root)[1]


# ------------------------------------------------------------------ deeply nested JSON


def test_a_deeply_nested_mark_counts_as_research_only(tmp_path, capsys):
    assert _partprov.decode(DEEP) is None and _partprov.research_only(DEEP)
    assert _partprov.decode(b"{" + b" " * 5000 + b"}") is None  # longer than any real mark
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "own1", DEEP)
    _write(st, "own2", OPEN)
    ctx = _ctx(st.root, {"own1", "own2"})
    _refused(ctx, "own1")
    res = REG.call("facilities", {}, ctx)
    assert [f["facility_id"] for f in res.data["facilities"]] == ["own2"]
    assert main(["datasets", "provenance", "--store", st.root]) == 0
    assert "own1" in capsys.readouterr().out


def test_a_deeply_nested_registry_is_the_registry_problem(tmp_path, capsys):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "ds-x", OPEN)
    with open(os.path.join(st.root, "_facilities.json"), "wb") as fh:
        fh.write(DEEP)
    assert _servable.read_registry(st.root) == ({}, "cannot be read")
    assert st.facilities_meta() == {}  # the store's own read: none, as for any damaged one
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "ds-x"}, _ctx(st.root, {"ds-x"}))
    assert e.value.code == "refused_licence"
    assert main(["datasets", "provenance", "--store", st.root]) == 0
    capsys.readouterr()


# ------------------------------------------------------------------ registry churn


def _registry_store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "ds-x", OPEN)
    st.register_facility("ds-x", dataset={"dataset_id": "x", "licence": "CC0-1.0"})
    return st, os.path.join(st.root, "_facilities.json")


def _between_reads(monkeypatch, flip):
    """Run ``flip(i)`` at every retry wait: the registry is put back and broken again between two
    reads, which therefore all fail alike."""
    count = [0]

    def sleep(_s):
        count[0] += 1
        flip(count[0])

    monkeypatch.setattr(_servable.time, "sleep", sleep)


def test_a_registry_renamed_away_again_between_reads_is_unavailable(tmp_path, monkeypatch):
    st, reg = _registry_store(tmp_path)
    away = reg + ".away"
    os.rename(reg, away)

    def flip(i):
        os.rename(away, reg)
        os.rename(reg, away)
        os.utime(st.root, ns=(i, i))  # the rename's own mtime may fall in the same tick

    _between_reads(monkeypatch, flip)
    with pytest.raises(_servable.RegistryUnavailable):
        _servable.read_registry(st.root)


def test_a_registry_truncated_again_between_reads_is_unavailable(tmp_path, monkeypatch):
    st, reg = _registry_store(tmp_path)
    with open(reg, "rb") as fh:
        good = fh.read()
    open(reg, "wb").close()

    def flip(i):
        with open(reg, "wb") as fh:
            fh.write(good)
        open(reg, "wb").close()
        os.utime(reg, ns=(i, i))

    _between_reads(monkeypatch, flip)
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "ds-x"}, _ctx(st.root, {"ds-x"}))
    assert e.value.code == "unavailable" and e.value.data["reason"] == "registry_unreadable"


def _symlink_loop(reg):
    os.remove(reg)
    os.symlink(reg, reg)


def _directory(reg):
    os.remove(reg)
    os.mkdir(reg)


@pytest.mark.parametrize("make", [_symlink_loop, _directory])
def test_a_registry_that_stays_unreadable_is_the_registry_problem(tmp_path, make):
    st, reg = _registry_store(tmp_path)
    try:
        make(reg)
    except (OSError, NotImplementedError):  # pragma: no cover - no symlinks here
        pytest.skip("cannot build that registry here")
    assert _servable.read_registry(st.root) == ({}, "cannot be read")
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "ds-x"}, _ctx(st.root, {"ds-x"}))
    assert e.value.code == "refused_licence"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads a mode-000 file"
)
def test_a_registry_without_read_permission_is_the_registry_problem(tmp_path, monkeypatch):
    st, reg = _registry_store(tmp_path)
    os.chmod(reg, 0)
    try:
        assert _servable.read_registry(st.root) == ({}, "cannot be read")
        # one that is re-permissioned while it is read is a writer at work: unavailable
        _between_reads(monkeypatch, lambda i: os.utime(reg, ns=(i, i)))
        with pytest.raises(_servable.RegistryUnavailable):
            _servable.read_registry(st.root)
    finally:
        os.chmod(reg, 0o644)


# ------------------------------------------------------------------ local report banner


def _cfg(root, fid):
    return {
        "facility_id": fid,
        "source": {"kind": "store", "store": root, "facility_id": fid},
        "rules": ["sat_too_high"],
    }


def test_a_local_report_on_marked_research_only_parts_carries_the_banner(store, tmp_path):
    from camber.config import data_sources
    from camber.report.audit import RESEARCH_ONLY_BANNER, data_sources_text

    other = str(tmp_path / "other")
    _copy(store.root, NCF, other, "b02", pick=lambda r: "month=7" in r)
    _copy(store.root, NCF, store.root, "hq", rename=lambda r: r.replace("part-", "part-9"))
    for root, fid in ((other, "b02"), (store.root, "hq")):
        srcs = data_sources(_cfg(root, fid))
        assert [s["access"] for s in srcs] == ["research_only"]
        assert srcs[0]["dataset_id"] == "test-ahu" and srcs[0]["licence"] == "CC-BY-NC-4.0"
        text = data_sources_text(srcs)
        assert RESEARCH_ONLY_BANNER.split(":")[0] in text and "Data source & licence" in text
        frame = ParquetStore(root).read_role_frame(facility_id=fid, equip="AHU-1")
        assert frame.attrs["camber_data_sources"] == srcs
    # the user's own data and an open mark add nothing
    assert data_sources(_cfg(store.root, "ds-at-30bldg-sensors-b02"))[0]["dataset_id"] == (
        "at-30bldg-sensors"
    )
    clean = ParquetStore(str(tmp_path / "clean"))
    _write(clean, "own", None)
    _write(clean, "open", OPEN)
    assert data_sources(_cfg(clean.root, "own")) == []
    assert data_sources(_cfg(clean.root, "open")) == []


def test_a_catalog_dataset_mark_takes_the_catalogs_licence_block(tmp_path):
    from camber._provenance import store_sources

    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "b02", _partprov.encode("at-30bldg-sensors", "CC-BY-NC-SA-4.0", True))
    _write(st, "odd", b"{not json")
    (src,) = store_sources(st.root, "b02", {})
    assert src["dataset_id"] == "at-30bldg-sensors" and src.get("citation")
    assert src["access"] == "research_only" and src["redistribution"] == "prohibited"
    (odd,) = store_sources(st.root, "odd", {})
    assert odd["access"] == "research_only" and "title" in odd
    # a registry block naming the same dataset as open is made research-only, not repeated
    meta = {"dataset": {"dataset_id": "at-30bldg-sensors", "licence": "CC-BY-4.0"}}
    (one,) = store_sources(st.root, "b02", meta)
    assert one["access"] == "research_only" and one["licence"] == "CC-BY-4.0"
    with _partprov.serving():  # a served read refused the parts before this
        assert store_sources(st.root, "b02", {}) == []
    assert json.loads(NC)["research_only"] is True
