"""Tests for the long/tall CSV adapter (camber.ingest.csv_long)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.csv_long import LongCsvAdapter  # noqa: E402

_LONG = (
    "timestamp,point,value,unit\n"
    "2024-01-01 00:00,AHU1_SAT,55.0,degF\n"
    "2024-01-01 01:00,AHU1_SAT,56.0,degF\n"
    "2024-01-01 00:00,AHU1_Flow,1000,cfm\n"
    "2024-01-01 01:00,AHU1_Flow,1100,cfm\n"
)


def _csv(tmp_path, text=_LONG):
    p = tmp_path / "long.csv"
    p.write_text(text)
    return str(p)


def test_pivots_to_wide_point_series(tmp_path):
    src = LongCsvAdapter(_csv(tmp_path))
    assert src.point_names() == ["AHU1_Flow", "AHU1_SAT"]
    df = src.load_points(["AHU1_SAT", "AHU1_Flow"], resample=None)
    assert list(df.columns) == ["AHU1_SAT", "AHU1_Flow"]
    assert df["AHU1_SAT"].tolist() == [55.0, 56.0]
    assert df.index.is_monotonic_increasing


def test_units_parsed_from_unit_column(tmp_path):
    src = LongCsvAdapter(_csv(tmp_path))
    assert src.units() == {"AHU1_SAT": "degF", "AHU1_Flow": "cfm"}


def test_missing_column_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("ts,pt,val\n2024-01-01,X,1\n")
    with pytest.raises(ValueError, match="not in"):
        LongCsvAdapter(str(p)).point_names()


def test_custom_column_names_and_resample(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("t,name,reading\n2024-01-01 00:00,P1,10\n2024-01-01 00:30,P1,20\n")
    src = LongCsvAdapter(str(p), ts_col="t", point_col="name", value_col="reading", unit_col=None)
    df = src.load_points(["P1"], resample="1h")
    assert df["P1"].iloc[0] == 15.0            # hourly mean of 10,20
