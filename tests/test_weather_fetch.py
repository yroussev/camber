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
