"""Tests for the NOAA/ISD-Lite station weather source (camber.weather_source) — all offline.

Every parse / nearest-station / tz / missing-value / multi-year / cache path runs on canned bytes
via injected transports; the only real-network line (the default transport) is a monkeypatched
urlopen. Uses generic stations (never a real client site). Mirrors tests/test_weather_fetch.py.
"""

import datetime as dt
import gzip
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import weather_source as ws  # noqa: E402
from camber.sensordrift import compare_to_reference  # noqa: E402

# generic stations: one near Chicago (covers 2023), one far, one decommissioned, one not-yet-begun
_HISTORY = (
    b"USAF,WBAN,STATION NAME,CTRY,STATE,ICAO,LAT,LON,ELEV(M),BEGIN,END\n"
    b"725300,94846,NEAR FIELD,US,IL,KORD,41.9950,-87.9336,201.8,19460101,20261231\n"
    b"722190,13874,FAR FIELD,US,GA,KATL,33.6301,-84.4418,308.0,19730101,20261231\n"
    b"999990,99991,OLD FIELD,US,IL,,41.9000,-87.6500,180.0,19600101,20001231\n"
    b"999992,99992,NEW FIELD,US,IL,,41.8900,-87.6400,181.0,20250101,20261231\n"
    b"000000,00000,NULL ISLAND,,,,0.000,0.000,0.0,19900101,20261231\n"
)


def _catalog(_url=None):
    return _HISTORY


def _isd_gz(hours=4, *, fill_at=None, start="2023-01-01"):
    """A gzipped ISD-Lite payload of consecutive hourly rows; tenths of °C (fill_at=-9999)."""
    import pandas as pd

    rows = []
    for i, ts in enumerate(pd.date_range(start, periods=hours, freq="1h")):
        t = -9999 if (fill_at is not None and i == fill_at) else -72 + (i % 24) * 10
        rows.append(
            f"{ts.year} {ts.month:02d} {ts.day:02d} {ts.hour:02d}  {t}  -100 10132 200 30 4 0 -9999"
        )
    return gzip.compress(("\n".join(rows) + "\n").encode())


def _data(gz):
    return lambda url: gz


# --------------------------------------------------------------------------- station catalog


def test_isd_history_parses_stations():
    stns = ws.isd_stations(transport=_catalog)
    names = {s.usaf: s for s in stns}
    assert "725300" in names and names["725300"].name == "NEAR FIELD"
    assert names["725300"].latitude == pytest.approx(41.9950)


def test_isd_stations_skips_null_island():
    assert all(
        s.usaf != "000000" for s in ws.isd_stations(transport=_catalog)
    )  # 0.000/0.000 skipped


def test_isd_nearest_station_picks_closest_covering():
    s = ws.isd_nearest_station(41.88, -87.63, "20230101", "20231231", transport=_catalog)
    assert s.usaf == "725300"  # NEAR FIELD (KORD), the closest station that covers 2023


def test_isd_nearest_station_skips_decommissioned():
    # OLD FIELD (ends 2000) is geographically closest to (41.90,-87.65) but doesn't cover 2023
    s = ws.isd_nearest_station(41.90, -87.65, "20230101", "20231231", transport=_catalog)
    assert s.end >= "20231231" and s.usaf != "999990"


def test_isd_nearest_station_skips_not_yet_begun():
    # NEW FIELD begins 2025 -> excluded for a 2023 window
    s = ws.isd_nearest_station(41.89, -87.64, "20230101", "20231231", transport=_catalog)
    assert s.begin <= "20230101" and s.usaf != "999992"


def test_isd_nearest_station_no_coverage_raises():
    with pytest.raises(ValueError, match="no ISD station covers"):
        ws.isd_nearest_station(41.88, -87.63, "19000101", "19001231", transport=_catalog)


def test_isd_nearest_station_accepts_prefetched_stations():
    stns = ws.isd_stations(transport=_catalog)
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return _HISTORY

    ws.isd_nearest_station(41.88, -87.63, "20230101", "20231231", transport=counting, stations=stns)
    assert calls["n"] == 0  # the pre-fetched list means no catalog download


# --------------------------------------------------------------------------- hourly fetch + parse


def test_fetch_isd_parses_gz_to_fahrenheit():
    df = ws.fetch_isd(
        "725300", "94846", "20230101", "20230101", transport=_data(_isd_gz()), tz="UTC"
    )
    assert list(df.columns) == ["oat_f"]  # dew point off by default
    assert df["oat_f"].iloc[0] == pytest.approx(19.04)  # -72 tenths -> -7.2 °C -> 19.04 °F
    assert df.index.tz is not None  # tz-aware UTC


def test_fetch_isd_missing_value_dropped_to_nan():
    df = ws.fetch_isd(
        "725300", "94846", "20230101", "20230101", transport=_data(_isd_gz(fill_at=2)), tz="UTC"
    )
    assert df["oat_f"].isna().iloc[2]  # -9999 -> NaN, not -999.9 °C


def test_fetch_isd_dew_point_column_optional():
    df = ws.fetch_isd(
        "725300", "94846", "20230101", "20230101", transport=_data(_isd_gz()), dew_point=True
    )
    assert "dewpt_f" in df.columns


def test_fetch_isd_tz_local_is_naive_and_dst_correct():
    # UTC 2023-01-01 00:00 in America/Chicago (UTC-6 winter) -> 2022-12-31 18:00, tz-naive
    df = ws.fetch_isd(
        "725300", "94846", "20221231", "20230101", transport=_data(_isd_gz()), tz="America/Chicago"
    )
    assert df.index.tz is None
    assert df.index[0] == __import__("pandas").Timestamp("2022-12-31 18:00")


def test_fetch_isd_all_empty_years_raise():
    with pytest.raises(ValueError, match="no ISD data"):
        ws.fetch_isd("725300", "94846", "20230101", "20230101", transport=_data(gzip.compress(b"")))


def test_fetch_isd_multi_year_unique_index():
    def per_year(url):  # URL carries the year: <base>/<year>/<usaf>-<wban>-<year>.gz
        year = int(url.rsplit("/", 1)[1].split("-")[-1].split(".")[0])
        return _isd_gz(hours=24, start=f"{year}-01-01")

    df = ws.fetch_isd("725300", "94846", "20220101", "20230101", transport=per_year, tz="UTC")
    assert df.index.is_unique and df.index.is_monotonic_increasing and len(df) > 24


def test_fetch_isd_determinism():
    kw = dict(transport=_data(_isd_gz(fill_at=1)), tz="UTC")
    import pandas as pd

    pd.testing.assert_frame_equal(
        ws.fetch_isd("725300", "94846", "20230101", "20230101", **kw),
        ws.fetch_isd("725300", "94846", "20230101", "20230101", **kw),
    )


# --------------------------------------------------------------------------- oat_reference_isd


def test_oat_reference_isd_composes_both_transports():
    ref = ws.oat_reference_isd(
        41.88,
        -87.63,
        "20230101",
        "20230101",
        transport=_data(_isd_gz()),
        catalog_transport=_catalog,
        tz="UTC",
    )
    assert ref.name == "oat_f" and ref.iloc[0] == pytest.approx(19.04)
    assert ref.attrs["isd_station"]["usaf"] == "725300"  # resolved station attached


def test_oat_reference_isd_feeds_compare_to_reference():
    ref = ws.oat_reference_isd(
        41.88,
        -87.63,
        "20230101",
        "20230105",
        transport=_data(_isd_gz(hours=120)),
        catalog_transport=_catalog,
        tz="UTC",
    )
    site = (ref + 3.0).rename("oat")
    r = compare_to_reference(site, ref, name="oat", min_samples=100)
    assert r.bias == pytest.approx(3.0, abs=0.01)  # drops into the sensor-validation path


# --------------------------------------------------------------------------- cached_bytes_transport


def test_cached_bytes_transport_hit_serves_from_disk(tmp_path):
    calls = {"n": 0}

    def inner(url):
        calls["n"] += 1
        return b"payload-bytes"

    cached = ws.cached_bytes_transport(inner, str(tmp_path))
    a, b = cached("http://x/y"), cached("http://x/y")
    assert calls["n"] == 1 and a == b == b"payload-bytes"
    assert any(f.endswith(".bin") for f in os.listdir(tmp_path))


def test_cached_bytes_transport_ttl_expiry_with_injected_clock(tmp_path):
    calls = {"n": 0}
    now = [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)]

    def inner(url):
        calls["n"] += 1
        return b"data"

    cached = ws.cached_bytes_transport(
        inner, str(tmp_path), ttl=dt.timedelta(hours=1), clock=lambda: now[0]
    )
    cached("http://x")
    now[0] += dt.timedelta(minutes=30)
    cached("http://x")  # fresh -> hit
    assert calls["n"] == 1
    now[0] += dt.timedelta(hours=2)
    cached("http://x")  # expired -> re-fetch
    assert calls["n"] == 2


def test_cached_bytes_transport_corrupt_file_refetches(tmp_path):
    import hashlib

    calls = {"n": 0}

    def inner(url):
        calls["n"] += 1
        return b"ok"

    cached = ws.cached_bytes_transport(inner, str(tmp_path))
    key = hashlib.sha256(b"http://x").hexdigest()
    (tmp_path / f"{key}.bin").write_bytes(b"no-newline-garbage")  # unparseable header
    cached("http://x")  # self-heals -> re-fetch
    assert calls["n"] == 1


def test_cached_bytes_transport_composes_with_isd():
    # a cache wrapping the catalog transport -> second nearest-station lookup served from disk
    import tempfile

    d = tempfile.mkdtemp()
    calls = {"n": 0}

    def inner(url):
        calls["n"] += 1
        return _HISTORY

    cached = ws.cached_bytes_transport(inner, d)
    for _ in range(2):
        ws.isd_nearest_station(41.88, -87.63, "20230101", "20231231", transport=cached)
    assert calls["n"] == 1


# --------------------------------------------------------------------------- default transport


def test_isd_transport_returns_raw_bytes(monkeypatch):
    import io as _io
    import urllib.request

    class _Resp(_io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(b"\x1f\x8braw"))
    got = ws.isd_transport()("https://example.test/x")
    assert got == b"\x1f\x8braw"  # raw bytes, not decoded/parsed
