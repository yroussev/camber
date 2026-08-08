"""Tests for the shared multi-format timestamp parser (camber.tsparse)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.tsparse import parse_timestamps  # noqa: E402


@pytest.mark.parametrize(
    "values,expected",
    [
        (["2024-01-01 00:00:00", "2024-01-01 01:00:00"], "2024-01-01 00:00:00"),  # ISO
        (["2024-01-01T00:00:00", "2024-01-01T01:00:00"], "2024-01-01 00:00:00"),  # ISO-T
        (["07/07/2025 11:00:00 AM", "07/07/2025 12:00:00 PM"], "2025-07-07 11:00:00"),  # US 12h
        (["21-Apr-23 8:30:03 AM PDT", "21-Apr-23 9:30:03 AM PDT"], "2023-04-21 08:30:03"),  # BAS
        (["20240101 00:00", "20240101 01:00"], "2024-01-01 00:00:00"),  # LBNL yyyymmdd
    ],
)
def test_string_formats_parse(values, expected):
    idx = parse_timestamps(values)
    assert idx.isna().sum() == 0
    assert idx[0] == pd.Timestamp(expected)


def test_epoch_seconds_and_millis():
    assert parse_timestamps([1700000000, 1700003600])[0] == pd.Timestamp("2023-11-14 22:13:20")
    assert parse_timestamps([1700000000000, 1700003600000])[0] == pd.Timestamp(
        "2023-11-14 22:13:20"
    )


def test_epoch_unit_override():
    # a small integer that would otherwise look like an Excel serial, forced to epoch seconds
    assert parse_timestamps([3600, 7200], epoch_unit="s")[0] == pd.Timestamp("1970-01-01 01:00:00")


def test_excel_serial():
    assert parse_timestamps([45292.0, 45293.0])[0] == pd.Timestamp("2024-01-01")


def test_dayfirst_disambiguates_european_dates():
    us = parse_timestamps(["03/04/2025 00:00"])  # default US -> March 4
    eu = parse_timestamps(["03/04/2025 00:00"], dayfirst=True)  # European -> April 3
    assert us[0] == pd.Timestamp("2025-03-04") and eu[0] == pd.Timestamp("2025-04-03")


def test_iso_offset_naive_by_default_tz_optional():
    naive = parse_timestamps(["2025-07-07T11:00:00-07:00"])
    assert naive.tz is None and naive[0] == pd.Timestamp("2025-07-07 11:00:00")
    kept = parse_timestamps(["2025-07-07T11:00:00-07:00"], naive=False)
    assert kept.tz is not None


def test_assume_tz_localizes_naive():
    idx = parse_timestamps(["2024-01-01 00:00"], assume_tz="America/Los_Angeles", naive=False)
    assert str(idx.tz) == "America/Los_Angeles"


def test_unparseable_becomes_nat_never_raises():
    idx = parse_timestamps(["garbage", "2024-01-01 00:00", "also bad"])
    assert idx.isna().sum() == 2 and idx.notna().sum() == 1


def test_explicit_format_override():
    idx = parse_timestamps(["01|02|2024 03", "01|02|2024 04"], formats=["%d|%m|%Y %H"])
    assert idx[0] == pd.Timestamp("2024-02-01 03:00:00")


def test_index_has_no_column_name():
    idx = parse_timestamps(pd.Series(["2024-01-01"], name="ts"))
    assert idx.name is None  # never carries the source column name


# --- non-finite / overflowing numerics -> NaT (regression: OverflowError escaped coerce) ----


@pytest.mark.parametrize(
    "values",
    [
        ["INFINITY"],  # to_numeric reads this as float inf -> int64-ns cast used to blow up
        ["infinity", "-Infinity"],
        [float("inf")],
        [float("-inf")],
        [float("inf"), float("-inf"), float("nan")],
        ["1e400"],  # overflows to inf on the way through float
        [1e300],  # finite but far past the int64-nanosecond range
        [9.3e18],
        [-9.3e18],
    ],
)
def test_non_finite_numerics_coerce_to_nat(values):
    idx = parse_timestamps(values)  # must not raise OverflowError
    assert idx.isna().all() and len(idx) == len(values)


def test_non_finite_does_not_poison_its_neighbours():
    idx = parse_timestamps([1_700_000_000, float("inf"), 1_700_000_900])
    assert idx[0] == pd.Timestamp("2023-11-14 22:13:20")
    assert pd.isna(idx[1])
    assert idx[2] == pd.Timestamp("2023-11-14 22:28:20")


def test_out_of_range_excel_serial_coerces_without_disturbing_the_rest():
    idx = parse_timestamps([45000.0, 1e300, 45000.5])  # median keeps this on the Excel path
    assert idx[0] == pd.Timestamp("2023-03-15") and idx[2] == pd.Timestamp("2023-03-15 12:00")
    assert pd.isna(idx[1])


def test_explicit_epoch_unit_also_coerces_non_finite():
    idx = parse_timestamps([float("inf"), 1_700_000_000, float("nan")], epoch_unit="s")
    assert pd.isna(idx[0]) and pd.isna(idx[2])
    assert idx[1] == pd.Timestamp("2023-11-14 22:13:20")
