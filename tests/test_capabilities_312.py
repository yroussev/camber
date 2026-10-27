"""0.111 (#312): fact ids follow in-place data rewrites; a renamed research-only partition is not
served.

1. The fact-id token's store generation stats every file of the facility partitions a call reads
   (and the point catalog), so a part file rewritten in place -- same name, directory times
   unchanged -- gives new ids, while an unchanged store keeps them.
2. A research-only registry entry whose facility directory is missing (the partition was renamed
   or moved) refuses the whole store, and a partition holding a research-only facility's very
   part files under another id is refused as research-only.

Every store is a small synthetic one in its own tmp directory; nothing reaches the network.
"""

import glob
import os
import shutil
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402
from test_capabilities_290 import NC, OPEN, OWN, _frame  # noqa: E402

from camber import _servable  # noqa: E402
from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.datasets import get  # noqa: E402
from camber.mcp._launch import build_context  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

REG = default_registry()


# ---------------------------------------------------------------- 1. fact ids


def _ids(root, fid=fx.FID, name="points") -> list:
    ctx = CallContext(store=root, allowed_facilities=frozenset({fx.FID}))
    return [f["id"] for f in REG.call(name, {"facility_id": fid}, ctx).facts]


def _rewrite_in_place(path, delta) -> None:
    """Rewrite a part file's values under the same name, keeping its directories' times."""
    dirs = [os.path.dirname(path), os.path.dirname(os.path.dirname(path))]
    times = [os.stat(d) for d in dirs]
    t = pq.ParquetFile(path).read()  # the file alone, no partition columns from its path
    values = pa.compute.add(t.column("value"), delta)
    t = t.set_column(t.schema.get_field_index("value"), "value", values)
    with open(path, "r+b") as fh:  # the same inode, as an in-place editor leaves it
        fh.truncate(0)
        pq.write_table(t, fh)
    for d, st in zip(dirs, times):
        os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_an_in_place_rewrite_changes_the_ids(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    before = _ids(st.root, name="coverage")
    assert _ids(st.root, name="coverage") == before
    part = sorted(
        glob.glob(os.path.join(st.root, f"facility_id={fx.FID}", "**", "*.parquet"), recursive=True)
    )[0]
    _rewrite_in_place(part, 7.25)
    after = _ids(st.root, name="coverage")
    assert not set(before) & set(after)
    assert _ids(st.root, name="coverage") == after  # stable again while the data stays


def test_the_point_catalog_counts(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.points()  # the catalog written
    before = _ids(st.root)
    os.remove(os.path.join(st.root, "_catalog.json"))
    assert not set(before) & set(_ids(st.root))


def test_an_identical_store_elsewhere_has_the_same_ids_after_a_copy_keeping_times(tmp_path):
    """No path enters the token: the same files with the same times give the same ids."""
    st = fx.make_store(tmp_path / "a", meta=None)
    st.points()
    other = tmp_path / "b"
    shutil.copytree(st.root, other, copy_function=shutil.copy2)
    for dirpath, _dirs, _files in os.walk(st.root):  # copytree sets directory times late
        rel = os.path.relpath(dirpath, st.root)
        s = os.stat(dirpath)
        os.utime(os.path.join(other, rel), ns=(s.st_atime_ns, s.st_mtime_ns))
    assert _ids(st.root) == _ids(str(other))


# ---------------------------------------------------------------- 2. registry hardening


@pytest.fixture
def store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    for v, fid in enumerate((NC, OPEN, OWN)):  # other values: no facility copies another
        st.write_role_frame(_frame(55.0 + v), facility_id=fid, equip="AHU-1", equip_class="AHU")
    st.register_facility(NC, name="NC", dataset=get("at-30bldg-sensors").provenance())
    st.register_facility(OPEN, name="Open", dataset=get("lbnl-fcu").provenance())
    st.register_facility(OWN, name="HQ")
    st.points()
    return st


def _ctx(store, allowed):
    return CallContext(store=store.root, allowed_facilities=frozenset(allowed))


def _refused(ctx, fid):
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": fid}, ctx)
    assert e.value.code == "refused_licence", e.value.message
    return e.value


def _fdir(store, fid):
    return os.path.join(store.root, f"facility_id={fid}")


def test_a_renamed_research_only_partition_refuses_the_store(store, caplog):
    os.rename(_fdir(store, NC), _fdir(store, "b02"))
    os.remove(os.path.join(store.root, "_catalog.json"))  # as any later write leaves it
    ctx = _ctx(store, ("b02", NC, OPEN, OWN))
    for fid in ("b02", OPEN, OWN):
        e = _refused(ctx, fid)
        assert e.data == {"facility_id": fid, "reason": "licence_unverified"}
        assert "directory is missing" in e.message
    res = REG.call("facilities", {}, ctx)
    assert res.data["facilities"] == []
    assert any("directory is missing" in b for b in res.banners)
    # --allow-all-active then serves nothing
    assert build_context(store.root, allow_all_active=True).allowed_facilities == frozenset()


def test_a_research_only_entry_mid_swap_is_not_missing(store):
    os.rename(_fdir(store, NC), os.path.join(store.root, f"_swap-facility_id={NC}.old"))
    assert _servable.read_registry(store.root)[1] is None


def test_a_removed_research_only_facility_leaves_the_store_served(store):
    store.drop_facility(NC, forget=True)
    ctx = _ctx(store, (OPEN, OWN))
    for fid in (OPEN, OWN):
        assert REG.call("points", {"facility_id": fid}, ctx).facts


@pytest.mark.parametrize("how", ["copy", "rename_and_drop_entry"])
def test_a_copy_of_research_only_part_files_is_refused(store, how):
    if how == "copy":
        shutil.copytree(_fdir(store, NC), _fdir(store, "b02"))
    else:  # the partition renamed and its registry entry renamed, the dataset block kept
        os.rename(_fdir(store, NC), _fdir(store, "b02"))
        shutil.copytree(_fdir(store, "b02"), _fdir(store, NC))  # the original still present
    ctx = _ctx(store, ("b02", OWN))
    e = _refused(ctx, "b02")
    assert e.data == {"facility_id": "b02"} and "research-only" in e.message
    assert REG.call("points", {"facility_id": OWN}, ctx).facts  # the rest is served
    listed = REG.call("facilities", {}, ctx).data
    assert [f["facility_id"] for f in listed["facilities"]] == [OWN]
    assert listed["withheld_research_only"] == 1


def test_own_data_laid_out_like_research_only_data_is_served(store):
    """The same part-file names (and here sizes) as a research-only facility, other values."""
    assert _servable._part_files(store.root, OWN) == _servable._part_files(store.root, NC)
    assert _servable.refusal(OWN, store.root) is None
    assert _servable.refusal(OPEN, store.root) is None
