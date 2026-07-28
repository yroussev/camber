"""Tests for vendor ingest profiles (camber.ingest.profiles) applied via io.load_csv."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.profiles import PROFILES, IngestProfile, get_profile  # noqa: E402
from camber.io import load_csv  # noqa: E402


def _csv(tmp_path, text, name="t.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


def test_get_profile_resolves_name_object_none():
    assert get_profile(None).name == "generic"
    assert get_profile("desigo").delimiter == ";"
    assert isinstance(get_profile(IngestProfile("x")), IngestProfile)
    with pytest.raises(ValueError, match="unknown ingest profile"):
        get_profile("nope")


def test_desigo_semicolon_decimal_comma_dayfirst(tmp_path):
    text = "ts;power\n07.01.2024 00:00:00;1.234,5\n07.01.2024 01:00:00;2.000,0\n"
    df = load_csv(_csv(tmp_path, text), profile="desigo")
    assert df.index[0].month == 1 and df.index[0].day == 7  # DD.MM day-first
    assert df["power"].tolist() == [1234.5, 2000.0]  # decimal comma + thousands "."


def test_metasys_us_format_with_preamble_rows(tmp_path):
    text = (
        "Export report\nSite XYZ\nTimestamp,Value\n"
        "07/07/2025 11:00:00 AM,42\n07/07/2025 12:00:00 PM,43\n"
    )
    df = load_csv(_csv(tmp_path, text), profile="metasys", skiprows=2)
    assert df.index[0].hour == 11 and df["Value"].tolist() == [42, 43]


def test_explicit_kwargs_override_profile(tmp_path):
    text = "ts|v\n2024-01-01 00:00|5\n"
    df = load_csv(_csv(tmp_path, text), delimiter="|")  # override generic comma
    assert df["v"].tolist() == [5]


def test_generic_profile_is_backward_compatible(tmp_path):
    df = load_csv(_csv(tmp_path, "ts,v\n2024-01-01 00:00,10\n2024-01-01 01:00,20\n"))
    assert df["v"].tolist() == [10, 20] and df.index[0].year == 2024


def test_presets_present():
    assert {"generic", "niagara_n4", "metasys", "webctrl", "tracer", "desigo"} <= set(PROFILES)
