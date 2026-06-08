"""Tests for the ingest adapters: per-point CSV, wide CSV, Haystack stub.

Key property: per-point and wide adapters yield the *same* normalized frame from
equivalent inputs, so everything above the adapter is source-agnostic.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.base import SourceAdapter  # noqa: E402
from camber.ingest.csv_perpoint import PerPointCsvAdapter  # noqa: E402
from camber.ingest.csv_wide import WideCsvAdapter  # noqa: E402
from camber.ingest.haystack import HaystackAdapter  # noqa: E402


# three hourly readings on a Monday; perpoint timestamps in the BAS export format
_TS = ["07-Jul-25 08:00:00 AM PDT", "07-Jul-25 09:00:00 AM PDT",
       "07-Jul-25 10:00:00 AM PDT"]
_TS_PLAIN = ["2025-07-07 08:00", "2025-07-07 09:00", "2025-07-07 10:00"]
_HW = [40.0, 45.0, 50.0]
_CC = [60.0, 55.0, 50.0]


def _write_perpoint(folder, name, unit, vals):
    with open(os.path.join(folder, f"{name}.csv"), "w", encoding="utf-8") as f:
        f.write(f"Timestamp,Value ({unit})\n")
        for t, v in zip(_TS, vals):
            f.write(f"{t},{v}\n")


def _write_wide(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("Timestamp,AHU1_HWValve,AHU1_CHWValve\n")
        for t, h, c in zip(_TS_PLAIN, _HW, _CC):
            f.write(f"{t},{h},{c}\n")


def test_adapters_satisfy_protocol(tmp_path):
    pp = PerPointCsvAdapter(str(tmp_path))
    assert isinstance(pp, SourceAdapter)
    assert isinstance(WideCsvAdapter(str(tmp_path / "x.csv")), SourceAdapter)
    assert isinstance(HaystackAdapter("http://x"), SourceAdapter)


def test_perpoint_load(tmp_path):
    folder = str(tmp_path)
    _write_perpoint(folder, "AHU1_HWValve", "%", _HW)
    _write_perpoint(folder, "AHU1_CHWValve", "%", _CC)
    a = PerPointCsvAdapter(folder)
    assert a.point_names() == ["AHU1_CHWValve", "AHU1_HWValve"]
    df = a.load_points(["AHU1_HWValve", "AHU1_CHWValve"], resample="1h")
    assert list(df.columns) == ["AHU1_HWValve", "AHU1_CHWValve"]
    assert df["AHU1_HWValve"].tolist() == _HW
    assert a.units()["AHU1_HWValve"] == "%"


def test_wide_load(tmp_path):
    path = str(tmp_path / "wide.csv")
    _write_wide(path)
    a = WideCsvAdapter(path)
    assert set(a.point_names()) == {"AHU1_HWValve", "AHU1_CHWValve"}
    df = a.load_points(["AHU1_HWValve"], resample="1h")
    assert df["AHU1_HWValve"].tolist() == _HW


def test_perpoint_and_wide_agree(tmp_path):
    # equivalent data through both adapters -> identical normalized frames
    pp_dir = tmp_path / "pp"
    pp_dir.mkdir()
    _write_perpoint(str(pp_dir), "AHU1_HWValve", "%", _HW)
    _write_perpoint(str(pp_dir), "AHU1_CHWValve", "%", _CC)
    wide = str(tmp_path / "wide.csv")
    _write_wide(wide)

    names = ["AHU1_HWValve", "AHU1_CHWValve"]
    a = PerPointCsvAdapter(str(pp_dir)).load_points(names, resample="1h")
    b = WideCsvAdapter(wide).load_points(names, resample="1h")
    pd.testing.assert_frame_equal(a[names], b[names], check_freq=False)


def test_haystack_stub_raises(tmp_path):
    h = HaystackAdapter("http://example", point_refs={"AHU1_HWValve": "@abc"})
    assert h.point_names() == ["AHU1_HWValve"]
    with pytest.raises(NotImplementedError):
        h.load_points(["AHU1_HWValve"])
