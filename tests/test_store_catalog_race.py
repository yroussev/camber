"""A read-side catalog rebuild never caches a catalog older than the store (0.104, #141).

``points()`` / ``equipment()`` / ``rebuild_catalog()`` rebuild ``_catalog.json`` without the store
lock. A writer that changes the store while such a rebuild is scanning must not be hidden by the
rebuild's write-back: the interleaving tests below run the writer from inside the reader's scan
(no timing), and the stress test runs real writer and reader processes side by side.
"""

import json
import multiprocessing as mp
import os
import sys
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.store import facilities as fac  # noqa: E402
from camber.store import parquet_store as ps  # noqa: E402
from camber.store.parquet_store import ParquetStore  # noqa: E402

_ROLE = list(Role)[0]
_IDX = pd.date_range("2024-01-01", periods=24, freq="1h")


def _write(store, equip, facility_id="S1", equip_class="AHU"):
    frame = pd.DataFrame({_ROLE: range(len(_IDX))}, index=_IDX)
    store.write_role_frame(frame, facility_id=facility_id, equip=equip, equip_class=equip_class)


def _keys(points):
    return {(p.facility_id, p.equip, p.role) for p in points}


def _write_during_scan(store, monkeypatch, equip="B"):
    """Make the reader's next scan finish on the old parts, then let a writer (a second store
    object on the same root, as another process would be) write ``equip`` and invalidate the
    catalog before the reader writes its result back."""
    real_scan = ParquetStore._scan
    writer = ParquetStore(store.root)
    fired = []

    def scan_then_write(self):
        result = real_scan(self)
        if not fired:
            fired.append(True)
            _write(writer, equip)
        return result

    monkeypatch.setattr(ParquetStore, "_scan", scan_then_write)
    return fired


@pytest.mark.parametrize("rebuild", ["points", "equipment", "rebuild_catalog"])
def test_a_rebuild_racing_a_write_does_not_hide_the_write(tmp_path, monkeypatch, rebuild):
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")
    fired = _write_during_scan(store, monkeypatch)
    getattr(store, rebuild)()  # scans {A}; B lands before the write-back
    assert fired
    monkeypatch.undo()
    # the reader's {A} catalog must not be served: B is in the store
    assert {k[1] for k in _keys(store.points())} == {"A", "B"}
    assert set(store.equipment()["S1"]) == {"A", "B"}
    # and the catalog is cached again once nothing races it
    assert os.path.isfile(store._catalog_path())
    store.read_long = lambda *a, **k: pytest.fail("rescanned a warm catalog")
    assert {k[1] for k in _keys(store.points())} == {"A", "B"}


def test_the_racing_reader_still_returns_what_it_scanned(tmp_path, monkeypatch):
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")
    _write_during_scan(store, monkeypatch)
    # a read that began before the write may or may not see it; it must not fail
    assert {k[1] for k in _keys(store.points())} == {"A"}


def test_a_stale_write_back_is_skipped(tmp_path, monkeypatch):
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")
    _write_during_scan(store, monkeypatch)
    store.points()
    # the writer bumped the generation after the reader's scan began: nothing is cached
    assert not os.path.isfile(store._catalog_path())


def test_a_stale_catalog_that_lands_anyway_is_ignored(tmp_path):
    """The check-then-replace window: a write-back carrying an old generation that is renamed in
    after the writer's invalidation is treated as absent, never served."""
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")
    stale_gen = ps._read_gen(store.root)
    keys, equipment = store._scan()
    _write(ParquetStore(store.root), "B")  # writer: new parts, new generation
    store._write_catalog(keys, equipment, gen=stale_gen, check=False)  # lands regardless
    assert os.path.isfile(store._catalog_path())
    assert store._read_catalog() is None
    assert {k[1] for k in _keys(store.points())} == {"A", "B"}


def test_every_invalidation_changes_the_generation(tmp_path):
    store = ParquetStore(str(tmp_path / "store"))
    seen = {ps._read_gen(store.root)}
    _write(store, "A")
    for op in (
        lambda: _write(store, "B"),
        lambda: store.drop_partition("S1", 2024),
        lambda: _write(store, "C"),
        lambda: store.drop_facility("S1"),
    ):
        op()
        gen = ps._read_gen(store.root)
        assert gen not in seen  # a fresh token: no ABA, even if a generation file is lost
        seen.add(gen)


def test_legacy_catalog_without_a_generation_is_still_served(tmp_path):
    """A store written before #141 has a catalog and no generation file: it stays valid until
    the first write after the upgrade."""
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")
    store.points()
    os.remove(os.path.join(store.root, ps._CATALOG_GEN))
    payload = store._read_catalog_payload(validate=False)
    payload.pop("gen", None)
    with open(store._catalog_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    store.read_long = lambda *a, **k: pytest.fail("rescanned a valid legacy catalog")
    assert {k[1] for k in _keys(store.points())} == {"A"}


def test_migrate_site_to_facility_bumps_the_generation(tmp_path):
    root = str(tmp_path / "db")
    st = ParquetStore(root)
    _write(st, "A", facility_id="Demo")
    st.points()
    before = ps._read_gen(root)
    os.rename(os.path.join(root, "facility_id=Demo"), os.path.join(root, "site=Demo"))
    assert fac.migrate_site_to_facility(root) == 1
    assert ps._read_gen(root) != before
    assert {k[0] for k in _keys(ParquetStore(root).points())} == {"Demo"}


def test_a_read_only_store_still_answers(tmp_path, monkeypatch):
    store = ParquetStore(str(tmp_path / "store"))
    _write(store, "A")

    def refuse(*a, **k):
        raise PermissionError("read-only store")

    monkeypatch.setattr(ps.tempfile, "mkstemp", refuse)
    assert {k[1] for k in _keys(store.points())} == {"A"}
    assert set(store.equipment()["S1"]) == {"A"}
    assert not os.path.isfile(store._catalog_path())


# --------------------------------------------------------------------------- multi-process stress

_WRITERS, _READERS, _PER_WRITER = 3, 3, 6


def _writer(root, w):
    store = ParquetStore(root)
    for i in range(_PER_WRITER):
        _write(store, f"W{w}_{i:02d}", facility_id=f"F{w}")


def _reader(root, stop):
    store = ParquetStore(root)
    while not stop.is_set():
        store.points()
        store.equipment()


def test_concurrent_writers_and_readers_leave_a_correct_catalog(tmp_path):
    root = str(tmp_path / "store")
    _write(ParquetStore(root), "seed", facility_id="F0")
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    readers = [ctx.Process(target=_reader, args=(root, stop)) for _ in range(_READERS)]
    writers = [ctx.Process(target=_writer, args=(root, w)) for w in range(_WRITERS)]
    t0 = time.monotonic()
    for p in readers + writers:
        p.start()
    for p in writers:
        p.join(60)
    stop.set()
    for p in readers:
        p.join(60)
    assert all(p.exitcode == 0 for p in readers + writers)
    assert time.monotonic() - t0 < 60

    store = ParquetStore(root)
    cached = _keys(store.points())  # whatever the readers left on disk, if still valid
    scanned, equipment = store._scan()
    assert cached == _keys(scanned)
    assert store.equipment() == equipment
    assert len({k[1] for k in cached}) == 1 + _WRITERS * _PER_WRITER


def test_an_append_never_exposes_a_half_written_part(tmp_path, monkeypatch):
    # The parts are written under _staging/ and renamed into place: while pyarrow is still
    # writing, the store's own partitions hold no new file a reader could open.
    root = str(tmp_path / "store")
    store = ParquetStore(root)
    _write(store, "seed", facility_id="F0")
    before = sorted(_part_files(root))
    seen = []
    real = ParquetStore._write_parts

    def spy(table, stage_root, seq):
        real(table, stage_root, seq)
        seen.append(sorted(_part_files(root)))
        assert os.path.basename(stage_root).startswith("append-")

    monkeypatch.setattr(ParquetStore, "_write_parts", staticmethod(spy))
    _write(store, "next", facility_id="F0")
    assert seen == [before]
    assert len(_part_files(root)) > len(before)
    assert not os.path.exists(os.path.join(root, "_staging"))


def _part_files(root):
    return [
        os.path.relpath(os.path.join(d, f), root)
        for d, _dirs, files in os.walk(root)
        if not os.path.relpath(d, root).startswith("_")
        for f in files
        if f.endswith(".parquet")
    ]
