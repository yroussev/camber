"""0.111 (#293): a capability call reads only the facilities it is about, not the whole store.

A no-persist call (every MCP call) never writes the store's point catalog back, so while that
catalog was stale each ``points`` / ``facilities`` / run call rescanned every facility: seconds
and gigabytes in a large store for a reply about one small facility. Now:

- ``points(facility_id=X)`` / ``equipment(facility_id=X)`` on a stale catalog scan X's partition
  only (memoized until it changes);
- ``facilities`` answers from the catalog and the part files' statistics, reading no rows;
- a call on one facility opens no dataset over another facility or the store root, and works
  even when every other facility's files are unreadable -- the guard that fails if a read path
  starts touching the rest of the store again;
- the call still writes nothing to the store.

Every test builds a tiny three-facility store in its own tmp directory.
"""

import glob
import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.store import parquet_store as ps  # noqa: E402
from camber.store.parquet_store import ParquetStore  # noqa: E402

FID, OTHERS = fx.FID, ("annex", "campus")
REG = default_registry()
WIN = ["2026-03-02", "2026-03-08"]
CALLS = [
    ("facilities", {}),
    ("points", {"facility_id": FID}),
    ("points", {"facility_id": FID, "equip_class": "AHU"}),
    ("coverage", {"facility_id": FID}),
    ("sensor_trust", {"facility_id": FID}),
    ("rule_run", {"facility_id": FID}),
    ("rcx_report", {"facility_id": FID}),
    ("mv_summary", {"facility_id": FID}),
    ("drift", {"facility_id": FID, "baseline": WIN}),
]


def _make(root, *, others=True, stale=True) -> ParquetStore:
    st = fx.make_store(root, meta=None)
    if others:
        for i, fid in enumerate(OTHERS):
            for k in range(3):
                st.write_role_frame(
                    fx.ahu_frame(seed=10 + 3 * i + k),
                    facility_id=fid,
                    equip=f"AHU-{k}",
                    equip_class="AHU",
                )
    st.points()  # a current catalog, as an ingest leaves it
    if stale:  # what a write the catalog has not caught up with leaves (the UAT store's state)
        st._invalidate_catalog()
    return st


def _poison(st, fids) -> None:
    """Make every part file of ``fids`` unreadable garbage: a read of them can only fail."""
    for fid in fids:
        for part in glob.glob(
            os.path.join(st.root, f"facility_id={fid}", "**", "*.parquet"), recursive=True
        ):
            with open(part, "wb") as fh:
                fh.write(b"not parquet " * 512)


def _manifest(root) -> dict:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _outcome(st, name, args):
    ctx = CallContext(store=st.root, allowed_facilities=frozenset({FID}))
    try:
        res = REG.call(name, args, ctx)
    except CapabilityError as e:
        return ("error", e.code)
    # a fact id's token depends on the store it was read from (#295): compare without it
    return (
        "ok",
        re.sub(r"(@[A-Za-z0-9_]*)-[0-9a-f]{6}\b", r"\1", json.dumps(res.data, sort_keys=True)),
    )


@pytest.fixture(autouse=True)
def _fresh_memo():
    with ps._FRAG_LOCK:
        ps._FAC_CATALOG.clear()
        ps._FRAG_INDEX.clear()
    yield


@pytest.fixture()
def opened(monkeypatch):
    """Every path a dataset is opened over (the store root, a facility partition ...)."""
    seen: list = []
    real = ps.ds.dataset

    def spy(source, *a, **k):
        seen.append(os.path.abspath(str(source)))
        return real(source, *a, **k)

    monkeypatch.setattr(ps.ds, "dataset", spy)
    return seen


@pytest.mark.parametrize("stale", [True, False], ids=["stale-catalog", "current-catalog"])
@pytest.mark.parametrize("name,args", CALLS, ids=[f"{n}-{i}" for i, (n, _a) in enumerate(CALLS)])
def test_a_call_on_one_facility_reads_only_that_facility(tmp_path, opened, name, args, stale):
    alone = _make(tmp_path / "alone", others=False, stale=stale)
    want = _outcome(alone, name, args)
    assert want[0] == "ok" or want[1] not in ("unavailable", "internal"), want
    st = _make(tmp_path / "many", stale=stale)
    _poison(st, OTHERS)
    before = _manifest(st.root)
    opened.clear()
    assert _outcome(st, name, args) == want
    own = os.path.join(os.path.abspath(st.root), f"facility_id={FID}")
    stray = [p for p in opened if p != own]
    assert not stray, f"{name} opened datasets outside {FID}: {stray}"
    assert _manifest(st.root) == before  # no-persist: nothing written, not even the catalog


def test_a_stale_catalog_scan_of_one_facility_matches_the_store_wide_catalog(tmp_path):
    st = _make(tmp_path, stale=False)
    keys, eqs = set(st.points()), st.equipment()
    st._invalidate_catalog()
    for fid in (FID, *OTHERS):
        assert set(st.points(facility_id=fid)) == {k for k in keys if k.facility_id == fid}
        assert st.equipment(facility_id=fid) == {fid: eqs[fid]}
    assert st.points(facility_id="nowhere") == [] and st.equipment(facility_id="nowhere") == {}
    assert set(st.points()) == keys and st.equipment() == eqs  # the store-wide call rebuilds


def test_the_per_facility_memo_follows_writes(tmp_path):
    st = _make(tmp_path)
    assert {k.equip for k in st.points(facility_id=FID)} == {"DemoAHU", "DemoAHU2"}
    st.write_role_frame(fx.ahu_frame(seed=7), facility_id=FID, equip="AHU-new", equip_class="AHU")
    st._invalidate_catalog()  # the write rebuilt nothing; keep the catalog stale
    assert "AHU-new" in {k.equip for k in st.points(facility_id=FID)}
    assert st.equipment(facility_id=FID)[FID]["AHU-new"] == "AHU"
    st.drop_partition(FID, 2026)
    assert st.points(facility_id=FID) == []


def test_the_per_facility_memo_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "_FAC_CATALOG_MAX", 2)
    st = _make(tmp_path)
    for fid in (FID, *OTHERS):
        st.points(facility_id=fid)
    assert len(ps._FAC_CATALOG) == 2


def test_facilities_reads_no_rows(tmp_path, monkeypatch):
    st = _make(tmp_path)
    reads: list = []
    real = ParquetStore.read_long

    def spy(self, **kw):
        reads.append(kw)
        return real(self, **kw)

    monkeypatch.setattr(ParquetStore, "read_long", spy)
    ctx = CallContext(store=st.root, allowed_facilities=frozenset({FID, *OTHERS}))
    res = REG.call("facilities", {}, ctx)
    rows = {r["facility_id"]: r for r in res.data["facilities"]}
    assert rows[FID]["start"] == "2026-03-02T00:00:00" and rows[FID]["n_points"] == 18
    assert rows["annex"]["n_equipment"] == 3
    # the span comes from file statistics; only the stale catalog's per-facility key scan reads
    assert all("ts" not in (kw.get("columns") or ()) for kw in reads), reads
    assert {kw.get("facility_id") for kw in reads} <= {FID, *OTHERS}


def test_a_damaged_facility_does_not_hide_the_others_on_a_stale_catalog(tmp_path):
    st = _make(tmp_path)
    _poison(st, ["annex"])
    ctx = CallContext(store=st.root, allowed_facilities=frozenset({FID, *OTHERS}))
    rows = {r["facility_id"]: r for r in REG.call("facilities", {}, ctx).data["facilities"]}
    assert rows["annex"]["unavailable"] == {"retryable": False} and rows["annex"]["start"] is None
    assert "unavailable" not in rows[FID] and rows[FID]["n_points"] == 18
    assert "unavailable" not in rows["campus"] and rows["campus"]["n_equipment"] == 3
    assert REG.call("points", {"facility_id": "campus"}, ctx).data["count"] == 27
