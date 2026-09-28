"""ISD missing-year tolerance and the bias-corrected NASA POWER fallback (issue #53) -- offline.

Every path runs on synthetic stations and injected transports: a known "true" temperature, ISD
files that exist only for some years (or stop at the catalog's end), and a NASA POWER double that
reads the true temperature minus a known monthly bias and publishes nothing after its coverage
end. The single real-network test is marked ``network`` (deselected by default).
"""

import datetime as dt
import gzip
import math
import os
import sys
import urllib.error
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import weather_source as ws  # noqa: E402

LAT, LON = 41.88, -87.63


def _truth_c(ts: pd.DatetimeIndex):
    """The synthetic "true" air temperature (°C) at UTC hours ``ts``."""
    doy = ts.dayofyear.to_numpy()
    hour = ts.hour.to_numpy()
    return (
        10.0
        + 12.0 * pd.Series(doy).map(lambda d: math.sin(2 * math.pi * (d - 110) / 365)).to_numpy()
        + 5.0 * pd.Series(hour).map(lambda h: math.sin(2 * math.pi * (h - 9) / 24)).to_numpy()
    )


def _power_bias_c(month: int) -> float:
    """POWER runs this much *colder* than the station (°C), by calendar month."""
    return 1.0 + 0.5 * math.cos(2 * math.pi * month / 12)


class FakeIsd:
    """ISD-Lite files per station-year; a year not in ``years`` is a 404; data stops at ``last``."""

    def __init__(self, stations: dict, offset_c: dict | None = None):
        self.stations = stations  # "usaf-wban" -> (set of years, last UTC hour or None)
        self.offset_c = offset_c or {}
        self.urls: list = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        parts = url.split("/")
        year, sid = int(parts[-2]), parts[-1].rsplit("-", 1)[0]
        years, last = self.stations.get(sid, (set(), None))
        if year not in years:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        hrs = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
        if last is not None:
            hrs = hrs[hrs <= pd.Timestamp(last)]
        vals = _truth_c(hrs) + self.offset_c.get(sid, 0.0)
        rows = [
            f"{t.year} {t.month:02d} {t.day:02d} {t.hour:02d} {round(v * 10):5d} -100 -9999"
            for t, v in zip(hrs, vals)
        ]
        return gzip.compress(("\n".join(rows) + "\n").encode())


class FakePower:
    """NASA POWER hourly JSON: truth minus a monthly bias, fill (-999) after ``coverage_end``."""

    def __init__(self, coverage_end: str | None = None):
        self.coverage_end = pd.Timestamp(coverage_end) if coverage_end else None
        self.urls: list = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["time-standard"] == "UTC"
        hrs = pd.date_range(q["start"], pd.Timestamp(q["end"]) + pd.Timedelta(hours=23), freq="h")
        vals = _truth_c(hrs) - [_power_bias_c(m) for m in hrs.month]
        block = {}
        for t, v in zip(hrs, vals):
            fill = self.coverage_end is not None and t > self.coverage_end
            block[t.strftime("%Y%m%d%H")] = -999.0 if fill else round(float(v), 2)
        return {
            "properties": {"parameter": {"T2M": block}},
            "header": {"time_standard": "UTC", "fill_value": -999.0},
        }


def _stn(usaf, lat, lon, begin="20000101", end="20250828"):
    return ws.IsdStation(usaf, "99999", f"FIELD {usaf}", lat, lon, begin, end)


def _clock(y, m, d):
    return lambda: dt.datetime(y, m, d, tzinfo=dt.timezone.utc)


def _true_f(index):
    return pd.Series(_truth_c(pd.DatetimeIndex(index)) * 1.8 + 32.0, index=index)


# --------------------------------------------------------------------------- fetch_isd missing year


def test_fetch_isd_tolerates_a_missing_year():
    isd = FakeIsd({"000001-99999": ({2017, 2019}, None)})
    with pytest.warns(UserWarning, match="no file for 2018"):
        df = ws.fetch_isd("000001", "99999", "2017-01-01", "2019-12-31", transport=isd)
    assert df.attrs["isd_missing_years"] == [2018]
    years = set(df.index.year)
    assert years == {2017, 2019}


def test_fetch_isd_missing_year_can_still_raise():
    isd = FakeIsd({"000001-99999": ({2017}, None)})
    with pytest.raises(urllib.error.HTTPError):
        ws.fetch_isd(
            "000001", "99999", "2017-01-01", "2018-12-31", transport=isd, on_missing_year="raise"
        )
    with pytest.raises(ValueError, match="on_missing_year"):
        ws.fetch_isd("000001", "99999", "2017-01-01", "2017-12-31", on_missing_year="x")


def test_fetch_isd_all_years_missing_names_them():
    isd = FakeIsd({})
    with pytest.raises(ValueError, match="no file for 2017, 2018"):
        ws.fetch_isd("000001", "99999", "2017-01-01", "2018-12-31", transport=isd)


def test_fetch_isd_other_errors_propagate():
    def boom(url):
        raise urllib.error.HTTPError(url, 500, "Server Error", None, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError):
        ws.fetch_isd("000001", "99999", "2017-01-01", "2017-12-31", transport=boom)


# --------------------------------------------------------------------------- stale catalog


def test_isd_catalog_end_and_stale_hint():
    cat = [_stn("000001", 0.0, 0.0), _stn("000002", 1.0, 1.0, end="20240101")]
    assert ws.isd_catalog_end(cat) == "20250828"
    assert ws.isd_catalog_end([]) is None
    with pytest.warns(UserWarning, match="looks stale"):
        with pytest.raises(ValueError, match="catalog ends 20250828.*fallback='nasa_power'"):
            ws.isd_nearest_station(0.0, 0.0, "2025-01-01", "2026-04-30", stations=cat)


# ----------------------------------------------------------------- POWER grid + time standard


def test_power_grid_cell_snaps_to_cell_centres():
    assert ws.power_grid_cell(41.88, -87.63) == (42.0, -87.5)
    assert ws.power_grid_cell(41.76, -87.30) == (42.0, -87.5)  # same ~55 km cell
    assert ws.power_grid_cell(-33.9, 151.2) == (-34.0, 151.25)


def test_snapped_fetch_shares_one_url_per_cell():
    pw = FakePower()
    a = ws.fetch_nasa_power(41.88, -87.63, "20240101", "20240101", transport=pw, snap_to_cell=True)
    ws.fetch_nasa_power(41.76, -87.30, "20240101", "20240101", transport=pw, snap_to_cell=True)
    assert pw.urls[0] == pw.urls[1]  # one cache key for the whole cell
    assert a.attrs["power_cell"]["latitude"] == 42.0
    assert a.attrs["power_cell"]["requested_latitude"] == 41.88
    assert a.attrs["power_coverage_end"] == "2024-01-01 23:00:00+00:00"


def test_power_url_requests_utc_and_rejects_lst_payload():
    assert "time-standard=UTC" in ws.nasa_power_url(0, 0, "20240101", "20240101")
    lst = {
        "properties": {"parameter": {"T2M": {"2024010100": 1.0}}},
        "header": {"time_standard": "LST"},
    }
    with pytest.raises(ValueError, match="LST"):
        ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=lambda u: lst)


def test_power_trailing_fill_is_missing_not_data():
    pw = FakePower(coverage_end="2024-01-01 17:00")
    df = ws.fetch_nasa_power(0, 0, "20240101", "20240101", transport=pw)
    assert df["oat_f"].isna().sum() == 6
    assert df.attrs["power_coverage_end"].startswith("2024-01-01 17:00")
    assert ws._power_complete(pw(ws.nasa_power_url(0, 0, "20240101", "20240101"))) is False
    assert ws._power_complete(FakePower()(ws.nasa_power_url(0, 0, "20240101", "20240101")))
    assert ws._power_complete({"bad": 1}) is False


# --------------------------------------------------------------------------- the blend


def test_blend_fills_a_missing_year_from_the_next_station_without_power():
    cat = [
        _stn("000001", LAT, LON + 0.1, end="20261231"),
        _stn("000002", LAT + 0.2, LON, end="20261231"),
    ]
    isd = FakeIsd(
        {"000001-99999": ({2017, 2019}, None), "000002-99999": ({2017, 2018, 2019}, None)},
        offset_c={"000002-99999": 0.3},
    )
    pw = FakePower()
    with pytest.warns(UserWarning, match="no data file for 2018"):
        s = ws.oat_reference_blended(
            LAT,
            LON,
            "2017-01-01",
            "2019-12-31",
            stations=cat,
            transport=isd,
            power_transport=pw,
            clock=_clock(2026, 9, 1),
        )
    prov = s.attrs["weather_provenance"]
    assert pw.urls == []  # the second station covered it: no reanalysis needed
    assert [g["source"] for g in prov["segments"]] == [
        "isd:000001-99999",
        "isd:000002-99999",
        "isd:000001-99999",
    ]
    assert prov["segments"][1]["start"].startswith("2018-01-01")
    assert prov["segments"][1]["end"].startswith("2018-12-31 23")
    assert prov["stations"][0]["missing_years"] == [2018]
    assert prov["bias_correction"] is None
    # 0.92: the second station runs 0.3 °C warm; its offset is estimated over 2017 and 2019, where
    # both stations report, and removed before it fills 2018
    off = prov["stations"][1]["offset_correction"]
    assert off["reference_station"] == "000001-99999"
    assert all(v == pytest.approx(-0.54, abs=0.02) for v in off["offsets_f"].values())
    gap = s["2018-01-01":"2018-12-31"]
    assert (gap - _true_f(gap.index)).abs().max() < 0.1
    assert any("offset (-0.5 to -0.5 °F)" in c for c in s.attrs["caveats"])
    assert len(s) == 3 * 8760  # 2017-2019, no leap day
    assert s.attrs["isd_station"]["usaf"] == "000001"


def _after_end_case(overlap_days, clock=(2026, 9, 1), coverage_end=None):
    cat = [_stn("000001", LAT, LON + 0.1), _stn("000002", LAT + 0.5, LON)]
    isd = FakeIsd(
        {
            "000001-99999": ({2023, 2024, 2025}, "2025-08-28 23:00"),
            "000002-99999": ({2023, 2024, 2025}, "2025-08-28 23:00"),
        }
    )
    pw = FakePower(coverage_end=coverage_end)
    with pytest.warns(UserWarning):
        s = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-01-01",
            "2026-03-31",
            stations=cat,
            transport=isd,
            power_transport=pw,
            overlap_days=overlap_days,
            clock=_clock(*clock),
        )
    return s, pw


def test_blend_after_catalog_end_falls_back_to_bias_corrected_power():
    s, pw = _after_end_case(overlap_days=730)
    prov = s.attrs["weather_provenance"]
    assert prov["catalog_stale"] is True
    assert [g["source"] for g in prov["segments"]] == ["isd:000001-99999", "nasa_power"]
    assert prov["segments"][1]["start"] == "2025-08-29 00:00:00+00:00"
    assert "cell" in prov["power"] and prov["power"]["cell"]["latitude"] == 42.0
    bias = prov["bias_correction"]
    assert bias["reference_station"] == "000001-99999"
    assert set(bias["basis"].values()) == {"month"}  # two years of overlap: every month its own
    for m in range(1, 13):  # the known monthly bias, recovered (°C -> °F)
        assert bias["offsets_f"][m] == pytest.approx(1.8 * _power_bias_c(m), abs=0.05)
    assert bias["rmse_after_f"] < 0.1 < bias["rmse_before_f"]
    # out of sample: the corrected fallback segment tracks the (unseen) truth
    fb = s["2025-08-29":]
    err = (fb - _true_f(fb.index)).abs()
    assert err.max() < 0.15
    raw_err = 1.8 * pd.Series([_power_bias_c(m) for m in fb.index.month])  # uncorrected POWER
    assert raw_err.abs().mean() > 1.0  # without the correction POWER is ~1-2.7 °F cold
    assert any("NASA POWER" in c and "monthly mean offset" in c for c in s.attrs["caveats"])
    assert any("looks stale" in c for c in s.attrs["caveats"])


def test_blend_offset_basis_falls_back_to_season_and_global():
    s, _ = _after_end_case(overlap_days=365)  # overlap = Jan..Aug 2025 only
    basis = s.attrs["weather_provenance"]["bias_correction"]["basis"]
    assert [basis[m] for m in range(1, 9)] == ["month"] * 8
    assert basis[12] == "season"  # DJF has Jan/Feb pairs
    assert basis[9] == basis[10] == basis[11] == "global"  # SON has none


def test_blend_power_latency_tail_stays_missing():
    s, _ = _after_end_case(overlap_days=365, clock=(2026, 3, 20), coverage_end="2026-03-17 23:00")
    prov = s.attrs["weather_provenance"]
    assert prov["segments"][-1]["source"] == "missing"
    assert prov["segments"][-1]["start"].startswith("2026-03-18")
    assert s.index.max() == pd.Timestamp("2026-03-17 23:00", tz="UTC")
    assert prov["power"]["coverage_end"].startswith("2026-03-17 23:00")
    assert any("have no source" in c and "2026-03-17" in c for c in s.attrs["caveats"])


def test_blend_local_clock_and_isd_fallback_kwarg():
    cat = [_stn("000001", LAT, LON + 0.1)]
    isd = FakeIsd({"000001-99999": ({2025}, "2025-08-28 23:00")})
    with pytest.warns(UserWarning):
        s = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-08-01",
            "2025-09-30",
            stations=cat,
            transport=isd,
            power_transport=FakePower(),
            tz="America/Chicago",
            clock=_clock(2026, 9, 1),
        )
    assert s.index.tz is None  # naive local clock
    seg = s.attrs["weather_provenance"]["segments"]
    assert seg[1]["start"] == "2025-08-28 19:00:00"  # 2025-08-29 00:00 UTC in CDT
    with pytest.raises(ValueError, match="fallback"):
        ws.oat_reference_isd(LAT, LON, "2025-08-01", "2025-09-30", fallback="open-meteo")


def test_oat_reference_isd_fallback_delegates(monkeypatch):
    seen = {}

    def fake_blend(*a, **k):
        seen.update(k)
        return pd.Series([1.0], name="oat_f")

    monkeypatch.setattr(ws, "oat_reference_blended", fake_blend)
    out = ws.oat_reference_isd(LAT, LON, "2025-08-01", "2025-09-30", fallback="nasa_power")
    assert out.iloc[0] == 1.0 and "power_transport" in seen


def test_blend_without_any_station_is_uncorrected_power():
    cat = [_stn("000009", 10.0, 10.0)]  # far away
    with pytest.warns(UserWarning, match="UNCORRECTED"):
        s = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-01-01",
            "2025-01-31",
            stations=cat,
            transport=FakeIsd({}),
            power_transport=FakePower(),
            clock=_clock(2025, 6, 1),
        )
    prov = s.attrs["weather_provenance"]
    assert prov["stations"] == [] and prov["bias_correction"]["reference_station"] is None
    assert prov["hours_by_source"] == {"nasa_power": 31 * 24}
    assert "isd_station" not in s.attrs


def test_blend_station_with_no_data_is_recorded():
    cat = [
        _stn("000001", LAT, LON + 0.1, end="20251231"),
        _stn("000002", LAT, LON + 0.3, end="20251231"),
    ]
    isd = FakeIsd({"000002-99999": ({2025}, None)})
    with pytest.warns(UserWarning):
        s = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-02-01",
            "2025-02-28",
            stations=cat,
            transport=isd,
            power_transport=FakePower(),
            clock=_clock(2025, 3, 1),
        )
    st = s.attrs["weather_provenance"]["stations"]
    assert st[0]["hours_filled"] == 0 and "no ISD data" in st[0]["error"]
    assert st[1]["hours_filled"] == 28 * 24


def test_blend_rejects_reversed_window():
    with pytest.raises(ValueError, match="before start"):
        ws.oat_reference_blended(LAT, LON, "2025-02-01", "2025-01-01", stations=[])


def test_blend_cache_first_and_offline(tmp_path):
    kw = dict(stations=[_stn("000001", LAT, LON + 0.1)], overlap_days=365)
    isd = FakeIsd({"000001-99999": ({2024, 2025}, "2025-08-28 23:00")})
    pw = FakePower()
    with pytest.warns(UserWarning):
        a = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-06-01",
            "2025-10-31",
            transport=isd,
            power_transport=pw,
            cache_dir=str(tmp_path),
            clock=_clock(2026, 9, 1),
            **kw,
        )

    def never(url):
        raise AssertionError("offline must not reach a transport")

    with pytest.warns(UserWarning):
        b = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-06-01",
            "2025-10-31",
            transport=never,
            power_transport=never,
            cache_dir=str(tmp_path),
            offline=True,
            clock=_clock(2026, 9, 1),
            **kw,
        )
    pd.testing.assert_series_equal(a, b)  # offline replay: identical bytes, identical series
    with pytest.raises(ws.WeatherCacheMiss), pytest.warns(UserWarning, match="stale"):
        ws.oat_reference_blended(
            LAT,
            LON,
            "2025-06-01",
            "2025-10-31",
            transport=never,
            power_transport=never,
            cache_dir=str(tmp_path / "empty"),
            offline=True,
            clock=_clock(2026, 9, 1),
            **kw,
        )


def test_power_response_with_fill_tail_is_not_cached(tmp_path):
    pw = FakePower(coverage_end="2025-01-01 12:00")
    cached = ws.cached_transport(pw, str(tmp_path), should_cache=ws._power_complete)
    url = ws.nasa_power_url(0, 0, "20250101", "20250101")
    cached(url)
    cached(url)
    assert len(pw.urls) == 2  # not frozen into the cache: re-fetched until published
    off = ws.cached_transport(pw, str(tmp_path), offline=True)
    with pytest.raises(ws.WeatherCacheMiss):
        off(url)


def test_cached_bytes_transport_remembers_not_found(tmp_path):
    isd = FakeIsd({})
    cached = ws.cached_bytes_transport(isd, str(tmp_path))
    url = f"{ws._ISD_DATA_BASE}/2018/000001-99999-2018.gz"
    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError):
            cached(url)
    assert len(isd.urls) == 1  # the second 404 came from the cache
    off = ws.cached_bytes_transport(isd, str(tmp_path), offline=True)
    with pytest.raises(urllib.error.HTTPError):
        off(url)  # offline replays the remembered 404, so a re-run agrees


def test_not_found_code_maps_local_missing_files():
    assert ws._not_found_code(FileNotFoundError("x")) == 404
    assert ws._not_found_code(RuntimeError("x")) is None


# --------------------------------------------------------------------------- real network (opt-in)


@pytest.mark.network
def test_network_power_small_window_smoke():
    """A two-day real POWER window: 48 UTC hours at the snapped cell, plausible °F values."""
    df = ws.fetch_nasa_power(41.88, -87.63, "20240115", "20240116", snap_to_cell=True)
    assert len(df) == 48 and df.index.tz is not None
    assert df.attrs["power_cell"]["latitude"] == 42.0
    ok = df["oat_f"].dropna()
    assert len(ok) == 48 and ok.between(-60, 120).all()
