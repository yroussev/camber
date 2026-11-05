"""0.112 (#319 follow-up): footer readers that predate #319 never open a FIFO named like a part.

Opening a FIFO for reading waits for a writer, so a FIFO named like a part file would hang the
reader: ``ParquetStore.partitions()``, the normalizer's part checks and row counts, and the
retention roll-up's partition read. Each skips (or reports) a non-regular file without opening
it. Every reader runs in a thread with a deadline, and every FIFO is removed in ``finally``.
"""

import glob
import os
import threading

import pandas as pd
import pytest

from camber.store import ParquetStore

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range("2026-03-02", periods=24, freq="h")
    st.write_role_frame(
        pd.DataFrame({"sat": 55.0}, index=idx), facility_id="hq", equip="A", equip_class="AHU"
    )
    return st


def _month_dir(st):
    (d,) = glob.glob(os.path.join(st.root, "facility_id=hq", "year=*", "month=*"))
    return d


def _bounded(fn):
    """``fn()``'s result, or a test failure when it does not return within 10 s."""
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", fn()), daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "the reader opened a FIFO and blocked"
    return box["r"]


class _Fifo:
    def __init__(self, path):
        self.path = path
        os.mkfifo(path)

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        try:  # release a reader that did open it, then remove it
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass
        os.remove(self.path)


def test_partitions_skip_a_fifo(tmp_path):
    st = _store(tmp_path)
    with _Fifo(os.path.join(_month_dir(st), "part-9-0.parquet")):
        (p,) = _bounded(st.partitions)
    assert p["rows"] == 24


def test_the_normalizer_reports_a_fifo_and_counts_no_rows_from_it(tmp_path):
    from camber.store import _normalize

    st = _store(tmp_path)
    d = _month_dir(st)
    with _Fifo(os.path.join(d, "part-9-0.parquet")) as fifo:
        info = _bounded(lambda: _normalize.check_part(fifo))
        assert info["error"] == "not a regular file" and info["rows"] is None
        assert _bounded(lambda: _normalize._dir_rows(d)) == 24


def test_the_retention_rollup_read_skips_a_fifo(tmp_path):
    from camber.portfolio import _retention

    st = _store(tmp_path)
    d = _month_dir(st)
    with _Fifo(os.path.join(d, "part-9-0.parquet")):
        df = _bounded(lambda: _retention._read_dir(d))
    assert len(df) == 24 and set(df["role"]) == {"sat"}
    assert _retention._read_dir(str(tmp_path / "nope")).empty
