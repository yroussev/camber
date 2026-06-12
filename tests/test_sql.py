"""Tests for the SQL/historian adapter over a long/narrow table.

Uses an in-memory stdlib sqlite3 db (no new dependency). Verifies the SqlSource
satisfies the SourceAdapter protocol, that per-point Series carry the right
values and a sorted DatetimeIndex, and that a WHERE clause narrows the rows.
"""

import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.base import SourceAdapter  # noqa: E402
from camber.ingest.sql import SqlSource, read_points  # noqa: E402


# two points, three hourly readings each, intentionally inserted out of order
# to exercise the sort, plus a unit column.
_ROWS = [
    ("2025-07-07 10:00:00", "AHU1_HWValve", 50.0, "%"),
    ("2025-07-07 08:00:00", "AHU1_HWValve", 40.0, "%"),
    ("2025-07-07 09:00:00", "AHU1_HWValve", 45.0, "%"),
    ("2025-07-07 08:00:00", "AHU1_CHWValve", 60.0, "%"),
    ("2025-07-07 09:00:00", "AHU1_CHWValve", 55.0, "%"),
    ("2025-07-07 10:00:00", "AHU1_CHWValve", 50.0, "%"),
]
_HW = [40.0, 45.0, 50.0]
_CC = [60.0, 55.0, 50.0]
_IDX = pd.to_datetime(["2025-07-07 08:00:00", "2025-07-07 09:00:00",
                       "2025-07-07 10:00:00"])


def _make_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE hist (ts TEXT, point TEXT, value REAL, unit TEXT)")
    con.executemany("INSERT INTO hist VALUES (?, ?, ?, ?)", _ROWS)
    con.commit()
    return con


def test_source_satisfies_protocol():
    con = _make_db()
    src = SqlSource(con, "hist", ts_col="ts", point_col="point",
                    value_col="value", unit_col="unit")
    assert isinstance(src, SourceAdapter)
    con.close()


def test_read_points_per_point_series():
    con = _make_db()
    pts = read_points(con, "hist", ts_col="ts", point_col="point",
                      value_col="value")
    con.close()
    assert set(pts) == {"AHU1_HWValve", "AHU1_CHWValve"}
    hw = pts["AHU1_HWValve"]
    assert isinstance(hw.index, pd.DatetimeIndex)
    assert hw.index.is_monotonic_increasing
    assert hw.name == "AHU1_HWValve"
    assert hw.tolist() == _HW
    pd.testing.assert_index_equal(hw.index, _IDX)
    assert pts["AHU1_CHWValve"].tolist() == _CC


def test_source_point_names_and_load():
    con = _make_db()
    src = SqlSource(con, "hist", ts_col="ts", point_col="point",
                    value_col="value", unit_col="unit")
    assert src.point_names() == ["AHU1_CHWValve", "AHU1_HWValve"]
    df = src.load_points(["AHU1_HWValve", "AHU1_CHWValve"], resample="1h")
    assert list(df.columns) == ["AHU1_HWValve", "AHU1_CHWValve"]
    assert df["AHU1_HWValve"].tolist() == _HW
    assert df["AHU1_CHWValve"].tolist() == _CC
    assert isinstance(df.index, pd.DatetimeIndex)
    assert src.units()["AHU1_HWValve"] == "%"
    con.close()


def test_load_points_native_interval():
    con = _make_db()
    src = SqlSource(con, "hist", ts_col="ts", point_col="point",
                    value_col="value")
    df = src.load_points(["AHU1_HWValve"], resample=None)
    pd.testing.assert_index_equal(df.index, _IDX)
    assert df["AHU1_HWValve"].tolist() == _HW
    con.close()


def test_where_clause_narrows():
    con = _make_db()
    pts = read_points(con, "hist", ts_col="ts", point_col="point",
                      value_col="value", where="point = 'AHU1_HWValve'")
    assert set(pts) == {"AHU1_HWValve"}
    assert pts["AHU1_HWValve"].tolist() == _HW

    src = SqlSource(con, "hist", ts_col="ts", point_col="point",
                    value_col="value", where="value >= 50")
    df = src.load_points(["AHU1_HWValve", "AHU1_CHWValve"], resample=None)
    # HW: only the 50.0 row; CHW: the 60/55/50 rows all qualify
    assert src.point_names() == ["AHU1_CHWValve", "AHU1_HWValve"]
    assert df["AHU1_HWValve"].dropna().tolist() == [50.0]
    assert df["AHU1_CHWValve"].tolist() == _CC
    con.close()


def test_empty_result():
    con = _make_db()
    pts = read_points(con, "hist", ts_col="ts", point_col="point",
                      value_col="value", where="point = 'nope'")
    assert pts == {}
    src = SqlSource(con, "hist", ts_col="ts", point_col="point",
                    value_col="value", where="point = 'nope'")
    assert src.point_names() == []
    assert src.load_points(["AHU1_HWValve"]).empty
    con.close()
