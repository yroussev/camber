"""Open-Meteo as a third source, the hour-of-day and station-offset corrections, the fallback chain
and its RCx wiring (issue #64) -- offline.

Every path runs on injected transports. A synthetic "true" temperature has a daily cycle; the
gridded doubles read it with a monthly bias and a *damped* daily cycle (warm nights, cool
afternoons), the systematic error a monthly offset alone cannot remove. The real-network tests
are marked ``network`` (deselected by default).
"""

import datetime as dt
import gzip
import json
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
    doy = ts.dayofyear.to_numpy()
    hour = ts.hour.to_numpy()
    return (
        10.0
        + 12.0 * pd.Series(doy).map(lambda d: math.sin(2 * math.pi * (d - 110) / 365)).to_numpy()
        + 5.0 * pd.Series(hour).map(lambda h: math.sin(2 * math.pi * (h - 9) / 24)).to_numpy()
    )


def _grid_c(ts: pd.DatetimeIndex, bias=1.0, damp=0.6):
    """A reanalysis double: the daily cycle damped to ``damp`` and ``bias`` °C cold."""
    daily = pd.Series(_truth_c(ts), index=ts).groupby(ts.normalize()).transform("mean").to_numpy()
    return daily + damp * (_truth_c(ts) - daily) - bias


def _true_f(index):
    return pd.Series(_truth_c(pd.DatetimeIndex(index)) * 1.8 + 32.0, index=index)


class FakeIsd:
    def __init__(self, stations: dict, offset_c: dict | None = None):
        self.stations = stations
        self.offset_c = offset_c or {}

    def __call__(self, url: str) -> bytes:
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


class FakeMeteo:
    """Open-Meteo archive JSON (°F, GMT); ``null`` after ``coverage_end``."""

    def __init__(self, coverage_end=None, unit="°F", fail=False, bias=0.8, damp=0.7):
        self.coverage_end = pd.Timestamp(coverage_end) if coverage_end else None
        self.unit, self.fail, self.bias, self.damp = unit, fail, bias, damp
        self.urls: list = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        if self.fail:
            raise urllib.error.URLError("unreachable")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["timezone"] == "GMT" and q["temperature_unit"] == "fahrenheit"
        assert "apikey" not in q and "email" not in url
        hrs = pd.date_range(q["start_date"], q["end_date"] + " 23:00", freq="h")
        c = _grid_c(hrs, self.bias, self.damp)
        vals = [
            None if self.coverage_end is not None and t > self.coverage_end else round(v, 2)
            for t, v in zip(hrs, c * 1.8 + 32.0 if self.unit == "°F" else c)
        ]
        return {
            "latitude": 41.875,
            "longitude": -87.625,
            "elevation": 180.0,
            "utc_offset_seconds": 0,
            "timezone": "GMT",
            "hourly_units": {"time": "iso8601", "temperature_2m": self.unit},
            "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in hrs], "temperature_2m": vals},
        }


class FakePower:
    def __init__(self, coverage_end=None, fail=False):
        self.coverage_end = pd.Timestamp(coverage_end) if coverage_end else None
        self.fail = fail
        self.urls: list = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        if self.fail:
            raise urllib.error.URLError("unreachable")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        hrs = pd.date_range(q["start"], pd.Timestamp(q["end"]) + pd.Timedelta(hours=23), freq="h")
        vals = _grid_c(hrs, 1.0, 0.6)
        block = {}
        for t, v in zip(hrs, vals):
            fill = self.coverage_end is not None and t > self.coverage_end
            block[t.strftime("%Y%m%d%H")] = -999.0 if fill else round(float(v), 2)
        return {"properties": {"parameter": {"T2M": block}}, "header": {"time_standard": "UTC"}}


def _stn(usaf, lat, lon, begin="20000101", end="20250828"):
    return ws.IsdStation(usaf, "99999", f"FIELD {usaf}", lat, lon, begin, end)


def _clock(y, m, d):
    return lambda: dt.datetime(y, m, d, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- Open-Meteo fetch


def test_open_meteo_url_is_utc_fahrenheit_and_keyless():
    url = ws.open_meteo_url(LAT, LON, "20240101", "2024-01-31")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://archive-api.open-meteo.com/v1/archive?")
    assert q["start_date"] == "2024-01-01" and q["end_date"] == "2024-01-31"
    assert q["timezone"] == "GMT" and q["temperature_unit"] == "fahrenheit"
    assert set(q) == {
        "latitude",
        "longitude",
        "start_date",
        "end_date",
        "hourly",
        "temperature_unit",
        "timezone",
    }
    assert "models=era5" in ws.open_meteo_url(LAT, LON, "20240101", "20240102", models="era5")


def test_fetch_open_meteo_parses_chunks_nulls_and_units():
    om = FakeMeteo(coverage_end="2024-02-10 05:00")
    df = ws.fetch_open_meteo(LAT, LON, "2023-12-30", "2024-02-11", transport=om)
    assert len(om.urls) == 2  # one request per calendar year
    assert df.index.tz is not None and df.index.is_unique and df.index.is_monotonic_increasing
    assert df.attrs["open_meteo_coverage_end"].startswith("2024-02-10 05:00")
    assert df.attrs["open_meteo_cell"]["latitude"] == 41.875
    assert df.attrs["open_meteo_cell"]["models"] == "best_match"
    assert df["oat_f"]["2024-02-10 06:00":].isna().all()  # unpublished hours are missing
    celsius = ws.fetch_open_meteo(
        LAT, LON, "2024-01-01", "2024-01-01", transport=FakeMeteo(unit="°C")
    )
    fahr = ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=FakeMeteo())
    assert (celsius["oat_f"] - fahr["oat_f"]).abs().max() < 0.02
    local = ws.oat_reference_open_meteo(
        LAT, LON, "2024-07-01", "2024-07-01", transport=FakeMeteo(), tz="America/Chicago"
    )
    assert local.index.tz is None and str(local.index[0]) == "2024-06-30 19:00:00"
    assert local.name == "oat_f"


def test_fetch_open_meteo_refuses_bad_payloads():
    def shifted(url):
        return {**FakeMeteo()(url), "utc_offset_seconds": 3600}

    with pytest.raises(ValueError, match="not in UTC"):
        ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=shifted)
    with pytest.raises(ValueError, match="missing hourly"):
        ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=lambda u: {})
    with pytest.raises(ValueError, match="neither"):
        ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=FakeMeteo(unit="K"))
    with pytest.raises(ValueError, match="no data"):
        ws.fetch_open_meteo(
            LAT, LON, "2024-01-01", "2024-01-01", transport=FakeMeteo(coverage_end="2023-12-01")
        )


def test_open_meteo_incomplete_response_is_not_cached(tmp_path):
    om = FakeMeteo(coverage_end="2024-01-01 12:00")
    t = ws.cached_transport(om, str(tmp_path), should_cache=ws._open_meteo_complete)
    ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=t)
    ws.fetch_open_meteo(LAT, LON, "2024-01-01", "2024-01-01", transport=t)
    assert len(om.urls) == 2  # the null tail kept it out of the cache
    assert ws._open_meteo_complete({"hourly": {"temperature_2m": [1.0]}})
    assert not ws._open_meteo_complete({})


# --------------------------------------------------------------------------- hour-of-day correction


def _pairs(start, end):
    idx = pd.date_range(start, end, freq="h", tz="UTC")
    station = _true_f(idx)
    grid = pd.Series(_grid_c(idx, 1.0, 0.6) * 1.8 + 32.0, index=idx)
    return station, grid


def test_hour_of_day_correction_removes_the_damped_daily_cycle():
    station, grid = _pairs("2023-01-01", "2024-12-31 23:00")
    cal, test = slice("2023-01-01", "2023-12-31"), slice("2024-01-01", "2024-12-31")
    monthly = ws._bias_offsets(station[cal], grid[cal], diurnal=False)
    both = ws._bias_offsets(station[cal], grid[cal])
    assert monthly["method"] == "monthly_mean_offset" and "hour_of_day_f" not in monthly
    assert both["method"] == "monthly_mean_offset+hour_of_day"
    assert both["rmse_after_monthly_f"] == monthly["rmse_after_f"]
    assert set(both["hour_basis"]["JJA"].values()) == {"season_hour"}

    def rmse(b):  # out of sample, on the next year
        err = station[test] - (grid[test] + ws._correction(grid[test].index, b))
        return float((err**2).mean() ** 0.5)

    assert rmse(both) < 0.35 * rmse(monthly)  # the daily cycle was most of the error
    # the correction does not move the monthly means: every (season, hour) residual averages 0
    assert abs(both["offsets_f"][7] - monthly["offsets_f"][7]) < 1e-9


def test_hour_of_day_basis_falls_back_to_hour_then_none():
    station, grid = _pairs("2024-06-01", "2024-07-15 23:00")  # summer only, 45 days
    b = ws._bias_offsets(station, grid)
    assert set(b["hour_basis"]["JJA"].values()) == {"season_hour"}
    assert set(b["hour_basis"]["DJF"].values()) == {"hour"}  # no winter pairs: the all-season hour
    short = ws._bias_offsets(station[:100], grid[:100])  # < 168 pairs: no correction at all
    assert set(short["basis"].values()) == {"none"}
    assert set(short["hour_basis"]["JJA"].values()) == {"none"}
    assert all(v == 0.0 for v in short["hour_of_day_f"]["JJA"].values())


# --------------------------------------------------------------------------- the fallback chain


def _after_end(fallbacks, power=None, meteo=None, clock=(2026, 9, 1), **kw):
    cat = [_stn("000001", LAT, LON + 0.1)]
    isd = FakeIsd({"000001-99999": ({2024, 2025}, "2025-08-28 23:00")})
    with pytest.warns(UserWarning):
        return ws.oat_reference_blended(
            LAT,
            LON,
            "2025-01-01",
            "2026-03-31",
            stations=cat,
            transport=isd,
            power_transport=power or FakePower(),
            meteo_transport=meteo or FakeMeteo(),
            fallbacks=fallbacks,
            overlap_days=730,
            clock=_clock(*clock),
            **kw,
        )


def test_default_power_fallback_now_corrects_the_daily_cycle():
    s = _after_end(("nasa_power",))
    prov = s.attrs["weather_provenance"]
    assert prov["method"] == "isd_with_nasa_power_fallback"
    assert prov["bias_correction"]["method"] == "monthly_mean_offset+hour_of_day"
    fb = s["2025-08-29":]
    assert (fb - _true_f(fb.index)).abs().max() < 0.3  # damped cycle and bias both removed
    s_monthly = _after_end(("nasa_power",), diurnal=False)
    fb_m = s_monthly["2025-08-29":]
    assert (fb_m - _true_f(fb_m.index)).abs().max() > 3.0  # the monthly offset alone cannot
    assert any("hour-of-day correction" in c for c in s.attrs["caveats"])


def test_open_meteo_fills_after_power_ends():
    s = _after_end(
        ("nasa_power", "open_meteo"),
        power=FakePower(coverage_end="2026-01-31 23:00"),
        meteo=FakeMeteo(coverage_end="2026-03-20 23:00"),
    )
    prov = s.attrs["weather_provenance"]
    assert prov["method"] == "isd_with_fallbacks:nasa_power,open_meteo"
    assert [g["source"] for g in prov["segments"]] == [
        "isd:000001-99999",
        "nasa_power",
        "open_meteo",
        "missing",
    ]
    assert prov["segments"][2]["start"].startswith("2026-02-01")
    om = [f for f in prov["fallbacks"] if f["source"] == "open_meteo"][0]
    assert om["bias_correction"]["reference_station"] == "000001-99999"
    assert om["cell"]["latitude"] == 41.875
    part = s["2026-02-01":"2026-03-20"]
    assert (part - _true_f(part.index)).abs().max() < 0.3
    assert any("Open-Meteo reanalysis" in c for c in s.attrs["caveats"])
    assert any("ISD and NASA POWER and Open-Meteo all missing" in c for c in s.attrs["caveats"])


def test_a_failed_source_hands_over_to_the_next():
    s = _after_end(("nasa_power", "open_meteo"), power=FakePower(fail=True))
    prov = s.attrs["weather_provenance"]
    assert "URLError" in prov["fallbacks"][0]["error"]
    assert prov["hours_by_source"].get("open_meteo", 0) > 0
    assert any("NASA POWER could not be fetched" in c for c in s.attrs["caveats"])
    with pytest.raises(urllib.error.URLError):  # a single fallback still fails loudly
        _after_end(("nasa_power",), power=FakePower(fail=True))


def test_open_meteo_only_fallback_and_bad_names():
    s = _after_end(("open_meteo",))
    assert s.attrs["weather_provenance"]["power"] is None
    assert s.attrs["weather_provenance"]["hours_by_source"]["open_meteo"] > 0
    with pytest.raises(ValueError, match="unknown fallback"):
        ws.oat_reference_blended(LAT, LON, "2025-01-01", "2025-01-02", fallbacks=("era5",))


def test_station_offsets_can_be_switched_off():
    cat = [_stn("000001", LAT, LON), _stn("000002", LAT, LON + 0.2)]
    isd = FakeIsd(
        {"000001-99999": ({2017, 2019}, None), "000002-99999": ({2017, 2018, 2019}, None)},
        offset_c={"000002-99999": 0.5},
    )
    kw = dict(stations=cat, transport=isd, power_transport=FakePower(), clock=_clock(2026, 9, 1))
    with pytest.warns(UserWarning):
        on = ws.oat_reference_blended(LAT, LON, "2017-01-01", "2019-12-31", **kw)
    with pytest.warns(UserWarning, match="station_offsets=False"):
        off = ws.oat_reference_blended(
            LAT, LON, "2017-01-01", "2019-12-31", station_offsets=False, **kw
        )
    gap = slice("2018-01-01", "2018-12-31")
    assert (on[gap] - _true_f(on[gap].index)).abs().max() < 0.1
    assert (off[gap] - _true_f(off[gap].index)).abs().mean() == pytest.approx(0.9, abs=0.06)
    assert off.attrs["weather_provenance"]["stations"][1]["hours_filled"] == 8760


def test_cache_dir_covers_open_meteo(tmp_path):
    om = FakeMeteo()

    def run(meteo, offline=False):
        return _after_end(("open_meteo",), meteo=meteo, cache_dir=str(tmp_path), offline=offline)

    a = run(om)
    assert os.listdir(tmp_path / "open_meteo")

    def never(url):
        raise AssertionError("offline must not reach a transport")

    cat = [_stn("000001", LAT, LON + 0.1)]
    with pytest.warns(UserWarning):
        b = ws.oat_reference_blended(
            LAT,
            LON,
            "2025-01-01",
            "2026-03-31",
            stations=cat,
            transport=never,
            power_transport=never,
            meteo_transport=never,
            fallbacks=("open_meteo",),
            overlap_days=730,
            cache_dir=str(tmp_path),
            offline=True,
            clock=_clock(2026, 9, 1),
        )
    pd.testing.assert_series_equal(a, b)


# --------------------------------------------------------------------------- oat_reference_auto


def test_oat_reference_auto_dispatches_by_source(monkeypatch, tmp_path):
    seen = {}

    def fake_blend(*a, **k):
        seen.update(k)
        return pd.Series([1.0], name="oat_f")

    monkeypatch.setattr(ws, "oat_reference_blended", fake_blend)
    s = ws.oat_reference_auto(LAT, LON, "2025-01-01", "2025-01-02", source="auto")
    assert seen["fallbacks"] == ("nasa_power", "open_meteo") and s.attrs["weather_source"] == "auto"
    ws.oat_reference_auto(LAT, LON, "2025-01-01", "2025-01-02", source="isd")
    assert seen["fallbacks"] == ()
    om = FakeMeteo()
    s = ws.oat_reference_auto(
        LAT, LON, "2025-01-01", "2025-01-01", source="open_meteo", transport=om,
        cache_dir=str(tmp_path),
    )  # fmt: skip
    assert len(s) == 24 and os.listdir(tmp_path / "open_meteo")
    s = ws.oat_reference_auto(
        LAT, LON, "2025-01-01", "2025-01-01", source="nasa_power", transport=FakePower()
    )
    assert len(s) == 24 and s.attrs["weather_source"] == "nasa_power"
    with pytest.raises(ValueError, match="unknown weather source"):
        ws.oat_reference_auto(LAT, LON, "2025-01-01", "2025-01-02", source="metar")


def test_oat_reference_isd_accepts_open_meteo_fallback(monkeypatch):
    seen = {}

    def fake_blend(*a, **k):
        seen.update(k)
        return pd.Series([1.0], name="oat_f")

    monkeypatch.setattr(ws, "oat_reference_blended", fake_blend)
    ws.oat_reference_isd(LAT, LON, "2025-01-01", "2025-01-02", fallback="open_meteo")
    assert seen["fallbacks"] == ("open_meteo",)


# --------------------------------------------------------------------------- RCx wiring


def test_rcx_reference_fetch_auto_uses_the_chain(monkeypatch):
    from camber.report import rcx

    calls = {}

    def fake_auto(lat, lon, start, end, *, source, tz, cache_dir, offline):
        calls.update(source=source, tz=tz, cache_dir=cache_dir, offline=offline)
        s = pd.Series([50.0, 51.0], index=pd.date_range("2025-01-01", periods=2, freq="h"))
        s.attrs["weather_provenance"] = {
            "hours_by_source": {"isd:1-2": 1, "nasa_power": 1},
            "caveats": ["1 h come from NASA POWER reanalysis."],
        }
        return s

    monkeypatch.setattr(ws, "oat_reference_auto", fake_auto)
    idx = pd.date_range("2025-01-01", periods=2, freq="h")
    spec = {"fetch": "auto", "latitude": 1.0, "longitude": 2.0, "tz": "Etc/UTC", "offline": True}
    ref = rcx._load_reference_oat(spec, idx)
    assert calls == {"source": "auto", "tz": "Etc/UTC", "cache_dir": None, "offline": True}
    note = rcx._reference_source_note(ref)
    assert note.startswith("Reference source by hour -- ") and "NASA POWER reanalysis" in note
    assert rcx._reference_source_note(pd.Series([1.0])) == ""
    with pytest.raises(ValueError, match="unknown oat_reference fetch"):
        rcx._load_reference_oat({"fetch": "metar", "latitude": 1, "longitude": 2}, idx)
    opts = rcx.RcxOptions.from_config(
        {"rcx": {"oat_reference": {"fetch": "auto", "cache_dir": "wx"}}}, base_dir="/base"
    )
    assert opts.oat_reference["cache_dir"] == os.path.join("/base", "wx")


# --------------------------------------------------------------------------- real network (opt-in)


@pytest.mark.network
def test_network_open_meteo_small_window_smoke():
    """A two-day real Open-Meteo window: 48 UTC hours, plausible °F values."""
    df = ws.fetch_open_meteo(41.88, -87.63, "20240115", "20240116")
    assert len(df) == 48 and df.index.tz is not None
    ok = df["oat_f"].dropna()
    assert len(ok) == 48 and ok.between(-60, 120).all()
    assert json.dumps(df.attrs["open_meteo_cell"])  # JSON-safe provenance
