"""Hardening: io.load_csv must survive malformed / adversarial CSVs (pre-1.0 stress pass).

Each test fails on the pre-hardening code (raise cryptic error / object-dtype poison) and
passes after.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.io import load_csv  # noqa: E402


def _csv(tmp_path, text, name="t.csv"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_empty_file_raises_clear_valueerror(tmp_path):
    with pytest.raises(ValueError, match="empty CSV"):
        load_csv(_csv(tmp_path, ""))


def test_header_only_returns_empty_frame(tmp_path):
    df = load_csv(_csv(tmp_path, "ts,v\n"))
    assert df.empty and list(df.columns) == ["v"]


def test_all_unparseable_timestamps_raises(tmp_path):
    with pytest.raises(ValueError, match="no parseable timestamps"):
        load_csv(_csv(tmp_path, "ts,v\ngarbage,1\nnope,2\n"))


def test_one_bad_timestamp_row_is_dropped_not_fatal(tmp_path):
    df = load_csv(_csv(tmp_path, "ts,v\n2024-01-01,1\ngarbage,2\n2024-01-03,3\n"))
    assert len(df) == 2 and list(df["v"]) == [1, 3]


def test_text_in_numeric_column_coerced_to_float_not_object(tmp_path):
    df = load_csv(_csv(tmp_path, "ts,v\n2024-01-01,1\n2024-01-02,oops\n2024-01-03,3\n"))
    assert str(df["v"].dtype) == "float64"  # not silently object-poisoned
    assert df["v"].isna().sum() == 1  # the text cell -> NaN


def test_duplicate_and_unsorted_index_regularized(tmp_path):
    df = load_csv(_csv(tmp_path, "ts,v\n2024-01-02,2\n2024-01-01,1\n2024-01-01,9\n"))
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert list(df["v"]) == [1, 2]  # dedupe="first" keeps the first duplicate


def test_missing_named_timestamp_col_raises(tmp_path):
    with pytest.raises(ValueError, match="timestamp column"):
        load_csv(_csv(tmp_path, "ts,v\n2024-01-01,1\n"), timestamp_col="nope")


def test_resample_survives_coerced_frame(tmp_path):
    df = load_csv(
        _csv(tmp_path, "ts,v\n2024-01-01 00:00,1\n2024-01-01 00:30,x\n2024-01-01 01:00,3\n"),
        resample="1h",
    )
    assert str(df["v"].dtype) == "float64" and len(df) >= 1
