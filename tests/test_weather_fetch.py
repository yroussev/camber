"""Tests for the live weather-fetch adapter (camber.weather_source) — all offline.

Every network path is exercised via an injected transport returning canned NASA POWER JSON; the one
real-network line (the default transport) is covered by monkeypatching urlopen. The load-bearing
timezone-alignment behavior (UTC default vs DST-correct naive local) is locked by an explicit test,
and the two integration hooks prove a fetched series drops straight into sensordrift + M&V.
"""

import io
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import weather_source as ws  # noqa: E402
from camber.sensordrift import compare_to_reference  # noqa: E402


def _canned(hours=8, *, start="2024010100", fill_at=None, params=("T2M", "RH2M")):
    """A small realistic NASA POWER hourly payload; ``fill_at`` injects a -999 at that hour."""
    base = pd.Timestamp(start[:8]) + pd.Timedelta(hours=int(start[8:] or 0))
    keys = [(base + pd.Timedelta(hours=h)).strftime("%Y%m%d%H") for h in range(hours)]
    block = {}
    for p in params:
        seed = 5.0 if p == "T2M" else 70.0
        vals = {k: seed + i * 0.5 for i, k in enumerate(keys)}
        if fill_at is not None:
            vals[keys[fill_at]] = -999.0
        block[p] = vals
    return {"properties": {"parameter": block}, "header": {}, "messages": []}


def _transport(payload):
    return lambda url: payload


# --------------------------------------------------------------------------- url + parse


def test_nasa_power_url_construction():
    url = ws.nasa_power_url(34.05, -118.24, "2024-01-01", "20240107", parameters=("T2M", "RH2M"))
    assert url.startswith(ws._BASE_URL + "?")
    assert "parameters=T2M%2CRH2M" in url  # comma-joined, URL-encoded
    assert "community=RE" in url and "format=JSON" in url
    assert "latitude=34.05" in url and "longitude=-118.24" in url
    assert "start=20240101" in url and "end=20240107" in url  # dashes stripped


def test_parse_canned_json_to_fahrenheit():
    df = ws.fetch_nasa_power(
        0,
        0,
        "20240101",
        "20240101",
        parameters=("T2M", "RH2M"),
        transport=_transport(_canned()),
        tz="UTC",
    )
    assert list(df.columns) == ["oat_f", "rh_pct"]
    assert df["oat_f"].iloc[0] == pytest.approx(41.0)  # 5 °C -> 41 °F via c_to_f
    assert df.index.tz is not None  # tz-aware UTC


def test_fill_value_dropped_to_nan_and_absent_from_reference():
    payload = _canned(hours=6, fill_at=2)
    df = ws.fetch_nasa_power(
        0,
        0,
        "20240101",
        "20240101",
        parameters=("T2M", "RH2M"),
        transport=_transport(payload),
        tz="UTC",
    )
    assert np.isnan(df["oat_f"].iloc[2]) and np.isnan(df["rh_pct"].iloc[2])  # -999 -> NaN
    ref = ws.oat_reference(0, 0, "20240101", "20240101", transport=_transport(payload))
    assert len(ref) == 5 and ref.name == "oat_f"  # the fill hour is dropped


def test_timezone_utc_default_is_aware():
    ref = ws.oat_reference(0, 0, "20240101", "20240101", transport=_transport(_canned()), tz="UTC")
    assert ref.index.tz is not None
    assert ref.index[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")


def test_timezone_local_shift_is_naive_and_dst_correct():
    # UTC 2024-01-01 00:00 in America/Los_Angeles (UTC-8 in winter) -> 2023-12-31 16:00, tz-naive
    ref = ws.oat_reference(
        0, 0, "20240101", "20240101", transport=_transport(_canned()), tz="America/Los_Angeles"
    )
    assert ref.index.tz is None  # naive local civil time -> joins to a BAS trend index
    assert ref.index[0] == pd.Timestamp("2023-12-31 16:00")


def test_rh_column_feeds_wetbulb():
    from camber.coolingtower import stull_wetbulb_f

    df = ws.fetch_nasa_power(
        0, 0, "20240101", "20240101", parameters=("T2M", "RH2M"), transport=_transport(_canned())
    )
    wb = stull_wetbulb_f(df["oat_f"], df["rh_pct"])
    assert np.isfinite(np.asarray(wb)).all()


def test_temperature_only_default():
    df = ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=_transport(_canned()))
    assert list(df.columns) == ["oat_f"]  # default parameters=("T2M",)


# --------------------------------------------------------------------------- robustness / errors


def test_missing_parameter_key_raises():
    payload = {"properties": {"parameter": {"RH2M": {"2024010100": 70.0}}}}  # no T2M
    with pytest.raises(ValueError, match="no T2M data"):
        ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=_transport(payload))


def test_missing_properties_raises():
    with pytest.raises(ValueError, match="missing properties.parameter"):
        ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=_transport({"junk": 1}))


def test_default_transport_parses_json(monkeypatch):
    # cover the real-network factory without a network: fake urlopen returns canned bytes
    import urllib.request

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _Resp(b'{"properties": {"parameter": {}}}')
    )
    got = ws.nasa_power_transport()("https://example.test/x")
    assert got == {"properties": {"parameter": {}}}


# ----------------------------------------------------------------------- integration + determinism


def test_integration_sensordrift_bias():
    # a fetched reference + a site OAT sensor reading 3 °F high -> compare_to_reference flags bias≈3
    payload = _canned(hours=150, params=("T2M",))
    ref = ws.oat_reference(0, 0, "20240101", "20240107", transport=_transport(payload), tz="UTC")
    site = (ref + 3.0).rename("oat")
    result = compare_to_reference(site, ref, name="oat")  # min_samples=100 -> 150 hrs suffices
    assert result.bias == pytest.approx(3.0, abs=0.01)
    assert result.severity == "warn"  # 2.0 (warn) < 3 < 5.0 (fault)


def test_integration_normalization_consumes_like_epw():
    from camber.mandv.weather import monthly_normals

    payload = _canned(hours=24 * 40, params=("T2M",))  # ~40 days spanning >1 month
    ref = ws.oat_reference(0, 0, "20240101", "20240210", transport=_transport(payload), tz="UTC")
    normals = monthly_normals(ref)  # the exact call an EPW series feeds
    assert isinstance(normals, pd.Series) and normals.notna().any()


def test_determinism():
    payload = _canned(hours=12, fill_at=3)
    a = ws.oat_reference(0, 0, "20240101", "20240101", transport=_transport(payload), tz="UTC")
    b = ws.oat_reference(0, 0, "20240101", "20240101", transport=_transport(payload), tz="UTC")
    pd.testing.assert_series_equal(a, b)


# --------------------------------------------------------------------------- multi-year chunking


class _Counter:
    """A URL-aware transport that counts calls and returns hourly data covering the URL's window."""

    def __init__(self, param="T2M", empty_for=()):
        self.calls = 0
        self.param = param
        self.empty_for = set(empty_for)  # start-dates whose chunk returns an empty block

    def __call__(self, url):
        from urllib.parse import parse_qs, urlparse

        self.calls += 1
        q = parse_qs(urlparse(url).query)
        s, e = q["start"][0], q["end"][0]
        if s in self.empty_for:
            return {"properties": {"parameter": {self.param: {}}}}
        keys, d = {}, pd.Timestamp(s)
        while d <= pd.Timestamp(e):
            for h in range(24):
                keys[f"{d.strftime('%Y%m%d')}{h:02d}"] = 5.0
            d += pd.Timedelta(days=1)
        return {"properties": {"parameter": {self.param: keys}}}


def test_multi_year_request_issues_one_call_per_year():
    c = _Counter()
    df = ws.fetch_nasa_power(0, 0, "20220601", "20240315", transport=c, tz="UTC")
    assert c.calls == 3  # 2022, 2023, 2024 calendar-year chunks
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert df.index.min() == pd.Timestamp("2022-06-01 00:00", tz="UTC")
    assert df.index.max() == pd.Timestamp("2024-03-15 23:00", tz="UTC")


def test_single_year_request_makes_one_call():
    c = _Counter()
    ws.fetch_nasa_power(0, 0, "20240101", "20240601", transport=c)
    assert c.calls == 1  # no regression: a within-year window is a single request


def test_chunk_seam_has_no_duplicate_or_missing_hour():
    c = _Counter()
    df = ws.fetch_nasa_power(0, 0, "20221215", "20230115", transport=c, tz="UTC")  # crosses a seam
    expected = pd.date_range("2022-12-15 00:00", "2023-01-15 23:00", freq="1h", tz="UTC")
    assert len(df) == len(expected) and (df.index == expected).all()  # contiguous, no dup/gap


def test_partial_empty_chunk_is_tolerated():
    c = _Counter(empty_for={"20230101"})  # the 2023 chunk comes back empty
    df = ws.fetch_nasa_power(0, 0, "20220601", "20231231", transport=c, tz="UTC")
    assert df.index.max().year == 2022 and len(df) > 0  # the covered year still returns


def test_all_empty_chunks_raise():
    c = _Counter(empty_for={"20240101"})
    with pytest.raises(ValueError, match="no T2M data"):
        ws.fetch_nasa_power(0, 0, "20240101", "20240601", transport=c)


# --------------------------------------------------------------------------- on-disk cache


def test_cached_transport_hit_serves_from_disk(tmp_path):
    inner = _Counter()
    cached = ws.cached_transport(inner, str(tmp_path))
    url = ws.nasa_power_url(0, 0, "20240101", "20240101")
    a, b = cached(url), cached(url)
    assert inner.calls == 1 and a == b  # second call served from disk
    assert any(f.endswith(".json") for f in os.listdir(tmp_path))


def test_cached_transport_miss_delegates_per_url(tmp_path):
    inner = _Counter()
    cached = ws.cached_transport(inner, str(tmp_path))
    cached(ws.nasa_power_url(0, 0, "20240101", "20240101"))
    cached(ws.nasa_power_url(1, 1, "20240101", "20240101"))  # different URL -> another fetch
    assert inner.calls == 2


def test_cached_transport_ttl_expiry_with_injected_clock(tmp_path):
    import datetime as dt

    inner = _Counter()
    now = [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)]
    cached = ws.cached_transport(
        inner, str(tmp_path), ttl=dt.timedelta(hours=1), clock=lambda: now[0]
    )
    url = ws.nasa_power_url(0, 0, "20240101", "20240101")
    cached(url)  # write
    now[0] += dt.timedelta(minutes=30)
    cached(url)  # fresh -> hit
    assert inner.calls == 1
    now[0] += dt.timedelta(hours=2)
    cached(url)  # expired -> re-fetch
    assert inner.calls == 2


def test_cached_transport_corrupt_file_refetches(tmp_path):
    import hashlib

    inner = _Counter()
    cached = ws.cached_transport(inner, str(tmp_path))
    url = ws.nasa_power_url(0, 0, "20240101", "20240101")
    key = hashlib.sha256(url.encode()).hexdigest()
    (tmp_path / f"{key}.json").write_text("{not valid json")  # torn write
    cached(url)  # self-heals: treats as a miss
    assert inner.calls == 1


def test_cached_transport_composes_with_fetch(tmp_path):
    inner = _Counter()
    cached = ws.cached_transport(inner, str(tmp_path))
    for _ in range(2):
        ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=cached, tz="UTC")
    assert inner.calls == 1  # the second fetch is served entirely from disk


# --------------------------------------------------------------------------- geocoding (by address)


def _canned_geo(lat="41.8755616", lon="-87.6244212", name=None):
    """A Nominatim search response (a JSON *array*); generic 'Chicago, IL' — no real client site."""
    name = name or "Chicago, Cook County, Illinois, United States"
    return [{"lat": lat, "lon": lon, "display_name": name, "type": "city"}]


class _GeoCounter:
    """A URL-aware Nominatim transport that counts calls and echoes a canned array."""

    def __init__(self):
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        return _canned_geo()


def test_nominatim_url_encoding():
    url = ws.nominatim_url("Chicago, IL", limit=3)
    assert url.startswith(ws._NOMINATIM_URL + "?")
    assert "format=json" in url and "limit=3" in url
    assert "q=Chicago%2C+IL" in url  # URL-encoded query


def test_geocode_parses_top_match():
    g = ws.geocode("Chicago, IL", transport=_transport(_canned_geo()))
    assert isinstance(g, ws.GeoResult)
    assert g.latitude == pytest.approx(41.8755616) and isinstance(g.latitude, float)
    assert g.longitude == pytest.approx(-87.6244212)
    assert "Chicago" in g.display_name


def test_geocode_as_dict_roundtrips():
    g = ws.geocode("Chicago, IL", transport=_transport(_canned_geo()))
    assert g.as_dict() == {
        "latitude": g.latitude,
        "longitude": g.longitude,
        "display_name": g.display_name,
    }


def test_geocode_no_match_raises():
    with pytest.raises(ValueError, match="no geocoding match"):
        ws.geocode("nowhere at all", transport=_transport([]))


def test_geocode_malformed_row_raises():
    with pytest.raises(ValueError, match="lat/lon"):
        ws.geocode("x", transport=_transport([{"display_name": "no coords"}]))


def test_geocode_limit_passed_through():
    seen = {}

    def t(url):
        seen["url"] = url
        return _canned_geo()

    ws.geocode("Chicago, IL", transport=t, limit=5)
    assert "limit=5" in seen["url"]


def test_nominatim_transport_sets_user_agent(monkeypatch):
    import urllib.request

    captured = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **k):
        captured["ua"] = req.get_header("User-agent")
        return _Resp(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ws.nominatim_transport()("https://example.test/x")
    assert captured["ua"] and "camber" in captured["ua"].lower()  # a non-default UA is set


def test_oat_reference_for_composes_both_transports():
    s = ws.oat_reference_for(
        "Chicago, IL",
        "20240101",
        "20240101",
        geocode_transport=_transport(_canned_geo()),
        transport=_transport(_canned(params=("T2M",))),
        tz="UTC",
    )
    assert s.name == "oat_f" and s.iloc[0] == pytest.approx(41.0)  # 5 °C -> 41 °F
    assert "Chicago" in s.attrs["geocode"]["display_name"]  # resolved place attached


def test_oat_reference_for_tz_local_is_naive():
    s = ws.oat_reference_for(
        "Chicago, IL",
        "20240101",
        "20240101",
        geocode_transport=_transport(_canned_geo()),
        transport=_transport(_canned(params=("T2M",))),
        tz="America/Los_Angeles",
    )
    assert s.index.tz is None  # naive local (the tz caveat holds on the convenience path)


def test_cached_transport_composes_with_geocode(tmp_path):
    inner = _GeoCounter()
    cached = ws.cached_transport(inner, str(tmp_path))
    for _ in range(2):
        ws.geocode("Chicago, IL", transport=cached)
    assert inner.calls == 1  # second geocode served from disk (satisfies the "cache" usage policy)
