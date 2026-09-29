"""Weather privacy guardrails (issue #73) -- offline.

What these tests prove is what is **never sent**: every URL a guarded fetch builds is recorded by
injected transports and scanned. POWER URLs carry only grid-cell centres, Open-Meteo URLs no
finer than the precision, ISD URLs no coordinates at all; ``offline`` makes zero calls; the
audit log matches the requests one for one; a private facility defaults to ``offline``; airport
and city names resolve locally; and with no policy the calls are exactly the pre-0.94 ones.
The one real-network test is marked ``network`` (deselected by default) and uses a public
airport coordinate.
"""

import datetime as dt
import gzip
import json
import math
import os
import re
import sys
import urllib.error
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import weather_privacy as wp  # noqa: E402
from camber import weather_source as ws  # noqa: E402

# a private site's full-precision location: none of these digits may ever leave the machine
LAT, LON = 41.87812, -87.62979
SECRETS = ("41.878", "87.629", "41.87812", "87.62979")


def _truth_c(ts: pd.DatetimeIndex):
    doy = ts.dayofyear.to_numpy()
    hour = ts.hour.to_numpy()
    return (
        10.0
        + 12.0 * np.sin(2 * math.pi * (doy - 110) / 365)
        + 5.0 * np.sin(2 * math.pi * (hour - 9) / 24)
    )


_CATALOG_ROWS = [
    # USAF, WBAN, name, ctry, state, icao, lat, lon, begin, end
    ("725300", "94846", "CHICAGO O'HARE INTL AP", "US", "IL", "KORD", 41.995, -87.934),
    ("725340", "14819", "CHICAGO MIDWAY INTL AP", "US", "IL", "KMDW", 41.786, -87.752),
    ("744665", "99999", "CHICAGO LAKEFRONT", "US", "IL", "", 41.900, -87.620),
]


def _catalog_bytes(end="20260920") -> bytes:
    head = '"USAF","WBAN","STATION NAME","CTRY","STATE","ICAO","LAT","LON","ELEV(M)","BEGIN","END"'
    lines = [head]
    for usaf, wban, name, ctry, st, icao, lat, lon in _CATALOG_ROWS:
        lines.append(
            f'"{usaf}","{wban}","{name}","{ctry}","{st}","{icao}","{lat:+.3f}","{lon:+.3f}",'
            f'"+0200.0","20000101","{end}"'
        )
    return ("\n".join(lines) + "\n").encode()


class Recorder:
    """Every transport of one fetch: ISD (catalogue + station files), POWER and Open-Meteo.

    ``fail=True`` raises on any call (the offline proof); ``urls`` lists every URL, in order.
    """

    def __init__(self, *, fail=False, isd_years=(2024,), isd_last=None):
        self.fail = fail
        self.urls: list = []
        self.isd_years = set(isd_years)
        self.isd_last = pd.Timestamp(isd_last) if isd_last else None

    def _hit(self, url):
        self.urls.append(url)
        if self.fail:
            raise AssertionError(f"network call in a test that must make none: {url}")

    def isd(self, url: str) -> bytes:
        self._hit(url)
        if url.endswith("isd-history.csv"):
            return _catalog_bytes()
        parts = url.split("/")
        year = int(parts[-2])
        if year not in self.isd_years:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        hrs = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
        if self.isd_last is not None:
            hrs = hrs[hrs <= self.isd_last]
        rows = [
            f"{t.year} {t.month:02d} {t.day:02d} {t.hour:02d} {round(v * 10):5d} -100 -9999"
            for t, v in zip(hrs, _truth_c(hrs))
        ]
        return gzip.compress(("\n".join(rows) + "\n").encode())

    def power(self, url: str) -> dict:
        self._hit(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        hrs = pd.date_range(q["start"], pd.Timestamp(q["end"]) + pd.Timedelta(hours=23), freq="h")
        block = {
            t.strftime("%Y%m%d%H"): round(float(v) - 1.0, 2) for t, v in zip(hrs, _truth_c(hrs))
        }
        return {"properties": {"parameter": {"T2M": block}}, "header": {"time_standard": "UTC"}}

    def meteo(self, url: str) -> dict:
        self._hit(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        hrs = pd.date_range(q["start_date"], q["end_date"] + " 23:00", freq="h")
        return {
            "latitude": float(q["latitude"]),
            "longitude": float(q["longitude"]),
            "elevation": 180.0,
            "utc_offset_seconds": 0,
            "hourly_units": {"temperature_2m": "°F"},
            "hourly": {
                "time": [t.strftime("%Y-%m-%dT%H:%M") for t in hrs],
                "temperature_2m": [round(float(v) * 1.8 + 32 - 1.5, 2) for v in _truth_c(hrs)],
            },
        }

    def kw(self) -> dict:
        return {
            "transport": self.isd,
            "power_transport": self.power,
            "meteo_transport": self.meteo,
        }


def _clock(y, m, d):
    return lambda: dt.datetime(y, m, d, tzinfo=dt.timezone.utc)


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _blend(rec, tmp_path, privacy="coarse", audit=None, **kw):
    """ISD through 2024-06-30, then POWER and Open-Meteo for the rest of 2024."""
    with pytest.warns(UserWarning):
        return ws.oat_reference_auto(
            LAT,
            LON,
            "2024-01-01",
            "2024-12-31",
            source="auto",
            cache_dir=str(tmp_path / "wx"),
            privacy=privacy,
            audit=audit,
            clock=_clock(2026, 9, 25),
            **{**rec.kw(), **kw},
        )


# --------------------------------------------------------------------------- coarsening


def test_power_urls_carry_only_grid_centres():
    rng = np.random.default_rng(7)
    for lat, lon in zip(rng.uniform(-60, 70, 200), rng.uniform(-180, 180, 200)):
        q = _q(ws.nasa_power_url(lat, lon, "20240101", "20240102", privacy="coarse"))
        la, lo = float(q["latitude"]), float(q["longitude"])
        assert abs(la / 0.5 - round(la / 0.5)) < 1e-9 and abs(lo / 0.625 - round(lo / 0.625)) < 1e-9
        assert abs(la - lat) <= 0.25 + 1e-9 and abs(lo - lon) <= 0.3125 + 1e-9  # its own cell
        wp.check_url(
            "nasa_power",
            ws.nasa_power_url(lat, lon, "20240101", "20240102", privacy="coarse"),
            "coarse",
        )
    assert (
        float(
            _q(ws.nasa_power_url(LAT, LON, "20240101", "20240101", privacy="coarse"))["latitude"]
        ),
        float(
            _q(ws.nasa_power_url(LAT, LON, "20240101", "20240101", privacy="coarse"))["longitude"]
        ),
    ) == ws.power_grid_cell(LAT, LON)


@pytest.mark.parametrize("precision", [0.1, 0.25, 0.05, 1.0])
def test_open_meteo_urls_are_no_finer_than_the_precision(precision):
    pol = wp.WeatherPolicy("coarse", precision)
    rng = np.random.default_rng(11)
    for lat, lon in zip(rng.uniform(-60, 70, 200), rng.uniform(-180, 180, 200)):
        url = ws.open_meteo_url(lat, lon, "2024-01-01", "2024-01-02", privacy=pol)
        q = _q(url)
        for raw, sent in ((lat, q["latitude"]), (lon, q["longitude"])):
            v = float(sent)
            assert abs(v / precision - round(v / precision)) < 1e-6
            assert abs(v - raw) <= precision / 2 + 1e-9
            frac = sent.split(".")[1] if "." in sent else ""
            assert len(frac) <= max(1, len(str(precision).split(".")[1]))
        wp.check_url("open_meteo", url, pol)
    # the default is 0.1 deg
    q = _q(ws.open_meteo_url(LAT, LON, "2024-01-01", "2024-01-02", privacy="coarse"))
    assert (q["latitude"], q["longitude"]) == ("41.9", "-87.6")


def test_public_leaves_coordinates_as_given():
    q = _q(ws.nasa_power_url(LAT, LON, "20240101", "20240101"))
    assert (q["latitude"], q["longitude"]) == (str(LAT), str(LON))
    q = _q(ws.open_meteo_url(LAT, LON, "2024-01-01", "2024-01-01", privacy="public"))
    assert (q["latitude"], q["longitude"]) == (str(LAT), str(LON))
    assert wp.coarsen("nasa_power", LAT, LON, None) == (LAT, LON)


def test_check_url_refuses_what_the_policy_forbids():
    raw = ws.nasa_power_url(LAT, LON, "20240101", "20240101")
    wp.check_url("nasa_power", raw, "public")  # public: unchanged behaviour
    with pytest.raises(wp.PrivacyViolation, match="finer than the policy"):
        wp.check_url("nasa_power", raw, "coarse")
    om = ws.open_meteo_url(LAT, LON, "2024-01-01", "2024-01-01")
    with pytest.raises(wp.PrivacyViolation, match="finer"):
        wp.check_url("open_meteo", om, "coarse")
    with pytest.raises(wp.PrivacyViolation, match="finer"):  # 0.1 is finer than 0.25
        wp.check_url(
            "open_meteo",
            ws.open_meteo_url(LAT, LON, "2024-01-01", "2024-01-01", privacy="coarse"),
            wp.WeatherPolicy("coarse", 0.25),
        )
    ok = ws.nasa_power_url(LAT, LON, "20240101", "20240101", privacy="coarse")
    with pytest.raises(wp.PrivacyViolation, match="unexpected field"):
        wp.check_url("nasa_power", ok + "&site=Main+Street+Office", "coarse")
    with pytest.raises(wp.PrivacyViolation, match="unexpected host"):
        wp.check_url("nasa_power", ok.replace("power.larc.nasa.gov", "example.com"), "public")
    with pytest.raises(wp.PrivacyViolation, match="ISD request"):
        wp.check_url("isd", ws.isd_url("725300", "94846", 2024) + "?lat=41.9", "coarse")
    with pytest.raises(wp.PrivacyViolation, match="ISD requests carry no coordinates"):
        wp.coarsen("isd", LAT, LON, "coarse")
    with pytest.raises(wp.PrivacyViolation, match="address"):
        wp.check_url("nominatim", ws.nominatim_url("1 Main St"), "coarse")
    with pytest.raises(wp.PrivacyViolation, match="short code"):
        wp.check_url("eia", "https://api.eia.gov/v2/x/?start=someone@example.com", "coarse")
    with pytest.raises(wp.PrivacyViolation, match="unknown outbound"):
        wp.check_url("ftp", "https://example.com", "coarse")


def test_geocoding_is_refused_before_anything_is_sent():
    calls = []
    with pytest.raises(wp.PrivacyViolation, match="address"):
        ws.geocode("1 Main Street, Springfield", transport=calls.append, privacy="coarse")
    with pytest.raises(wp.PrivacyViolation):
        ws.geocode("1 Main Street, Springfield", transport=calls.append, privacy="offline")
    assert calls == []


def test_policy_validation_and_resolution():
    assert wp.resolve_policy().privacy == "public"
    assert wp.resolve_policy(private=True).privacy == "offline"  # the private default
    assert wp.resolve_policy(private=True).origin == "private"
    pol = wp.resolve_policy(private=True, config={"privacy": "coarse", "precision_deg": 0.2})
    assert (pol.privacy, pol.precision_deg, pol.origin) == ("coarse", 0.2, "config")
    assert wp.resolve_policy(config="coarse", spec="offline").privacy == "offline"
    with pytest.raises(ValueError, match="marked private"):
        wp.resolve_policy(private=True, config={"privacy": "public"})
    with pytest.raises(ValueError, match="privacy must be one of"):
        wp.WeatherPolicy("fuzzy")
    with pytest.raises(ValueError, match="between"):
        wp.WeatherPolicy("coarse", 0.001)
    with pytest.raises(ValueError, match="must be a number"):
        wp.WeatherPolicy("coarse", "0.1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown key"):
        wp.WeatherPolicy.coerce({"privacy": "coarse", "radius": 3})
    with pytest.raises(ValueError, match="weather must be"):
        wp.WeatherPolicy.coerce(3)
    assert "0.1°" in wp.coarsening_note("open_meteo", "coarse")
    assert "grid-cell centre" in wp.coarsening_note("nasa_power", "coarse")
    assert "as given" in wp.coarsening_note("nasa_power", None)
    assert "station id" in wp.coarsening_note("isd", "coarse")


# --------------------------------------------------------------------------- the blend, coarse


def test_coarse_blend_sends_no_coordinates_to_isd_and_only_cells_elsewhere(tmp_path):
    rec = Recorder(isd_last="2024-06-30 23:00")
    audit = wp.WeatherAudit(str(tmp_path / "audit.ndjson"), facility_id="fac-123")
    s = _blend(rec, tmp_path, audit=audit)
    assert len(s) and rec.urls
    isd = [u for u in rec.urls if "ncei.noaa.gov" in u]
    assert isd and all(urlparse(u).query == "" for u in isd)
    assert all(
        re.search(r"/isd-lite/\d{4}/\d{6}-\d{5}-\d{4}\.gz$|isd-history\.csv$", u) for u in isd
    )
    power = [u for u in rec.urls if "power.larc" in u]
    meteo = [u for u in rec.urls if "open-meteo" in u]
    assert power, "the blend should have reached POWER for the second half of 2024"
    cell = ws.power_grid_cell(LAT, LON)
    for u in power:
        assert (float(_q(u)["latitude"]), float(_q(u)["longitude"])) == cell
    for u in meteo:
        assert (_q(u)["latitude"], _q(u)["longitude"]) == ("41.9", "-87.6")
    for u in rec.urls:
        wp.check_url(
            "isd" if "ncei" in u else "nasa_power" if "power" in u else "open_meteo", u, "coarse"
        )
        for secret in SECRETS + ("fac-123",):
            assert secret not in u
    prov = s.attrs["weather_provenance"]["privacy"]
    assert prov["privacy"] == "coarse" and prov["cache"] == "on"
    assert "grid-cell centre" in prov["coarsening"]["nasa_power"]
    assert "station id" in prov["coarsening"]["isd"]
    assert prov["requests_sent"] == len(rec.urls)
    assert s.attrs["weather_privacy"] == prov
    assert s.attrs["weather_provenance"]["bias_correction"] is not None  # still corrected


def test_audit_log_matches_the_requests(tmp_path):
    rec = Recorder(isd_last="2024-06-30 23:00")
    path = tmp_path / "audit.ndjson"
    audit = wp.WeatherAudit(str(path), facility_id="fac-123", clock=lambda: "2026-09-28T00:00:00Z")
    _blend(rec, tmp_path, audit=audit)
    rows = wp.read_weather_audit(str(path))
    sent = [r for r in rows if r["sent"]]
    assert [r["url"] for r in sent] == rec.urls  # one record per request, in order
    assert all(r["cache"] == "miss" and r["privacy"] == "coarse" for r in sent)
    assert all(r["facility_id"] == "fac-123" and r["purpose"] == "weather" for r in rows)
    assert {r["service"] for r in sent} == {"isd", "nasa_power", "open_meteo"} or {
        r["service"] for r in sent
    } == {"isd", "nasa_power"}
    assert all("fac-123" not in r["url"] for r in rows)  # the id is local-only
    # a second run is served from the cache: logged as hits, nothing sent
    rec2 = Recorder(fail=True)
    audit2 = wp.WeatherAudit(str(path), facility_id="fac-123")
    _blend(rec2, tmp_path, audit=audit2, privacy="offline")
    assert rec2.urls == [] and audit2.records
    assert all(not r["sent"] and r["cache"] == "hit" for r in audit2.records)
    assert all(r["privacy"] == "offline" for r in audit2.records)
    assert len(wp.read_weather_audit(str(path))) == len(rows) + len(audit2.records)
    assert wp.read_weather_audit(str(path), since="2027-01-01") == []
    assert wp.read_weather_audit(str(path), facility_id="other") == []


# --------------------------------------------------------------------------- offline


def test_offline_makes_zero_calls(tmp_path):
    rec = Recorder(fail=True)
    audit = wp.WeatherAudit(str(tmp_path / "a.ndjson"))
    with pytest.raises(ws.WeatherCacheMiss, match="supply a weather file"):
        ws.oat_reference_auto(
            LAT,
            LON,
            "2024-01-01",
            "2024-01-31",
            source="auto",
            privacy="offline",
            cache_dir=str(tmp_path / "empty"),
            audit=audit,
            clock=_clock(2026, 9, 25),
            **rec.kw(),
        )
    for source, kw in (("nasa_power", rec.power), ("open_meteo", rec.meteo)):
        with pytest.raises(ws.WeatherCacheMiss, match="offline"):
            ws.oat_reference_auto(
                LAT,
                LON,
                "2024-01-01",
                "2024-01-31",
                source=source,
                privacy="offline",
                cache_dir=str(tmp_path / "empty"),
                transport=kw,
            )
    assert rec.urls == []
    assert audit.records and all(not r["sent"] for r in audit.records)


def test_offline_without_a_cache_dir_uses_the_default_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_WEATHER_DIR", str(tmp_path / "default"))
    rec = Recorder(fail=True)
    with pytest.raises(ws.WeatherCacheMiss):
        ws.oat_reference_auto(
            LAT,
            LON,
            "2024-01-01",
            "2024-01-02",
            source="nasa_power",
            privacy="offline",
            transport=rec.power,
        )
    assert rec.urls == []
    assert wp.default_weather_dir() == str(tmp_path / "default")


def test_offline_guard_refuses_even_an_uncached_transport():
    calls = []
    t = wp.guarded_transport("nasa_power", calls.append, policy="offline")
    with pytest.raises(wp.OfflineViolation, match="not sent"):
        t(ws.nasa_power_url(LAT, LON, "20240101", "20240101", privacy="offline"))
    assert calls == []


def test_coarse_fill_then_offline_rerun_is_identical(tmp_path):
    a = _blend(Recorder(isd_last="2024-06-30 23:00"), tmp_path)
    b = _blend(Recorder(fail=True), tmp_path, privacy="offline")
    pd.testing.assert_series_equal(a, b)


# --------------------------------------------------------------------------- places


def test_airport_and_city_resolution_is_local(monkeypatch):
    stations = ws.isd_stations(transport=lambda url: _catalog_bytes())
    kord = ws.resolve_place("KORD", stations=stations)
    assert kord["kind"] == "airport" and kord["station"]["usaf"] == "725300"
    assert (kord["latitude"], kord["longitude"]) == (41.995, -87.934)
    assert ws.resolve_place("725340-14819", stations=stations)["station"]["icao"] == "KMDW"
    city = ws.resolve_place("Chicago, IL")
    assert city == {"latitude": 41.88, "longitude": -87.63, "kind": "city", "label": "chicago, il"}
    assert ws.resolve_place("chicago")["label"] == "chicago, il"
    assert ws.resolve_place("Rome")["label"] == "rome, it"  # a 4-letter city is not a code
    with pytest.raises(ValueError, match="catalogue is needed"):
        ws.resolve_place("KORD")
    with pytest.raises(ValueError, match="no airport"):
        ws.resolve_place("KXYZ", stations=stations)
    with pytest.raises(ValueError, match="no ISD station"):
        ws.resolve_place("000000-00000", stations=stations)
    with pytest.raises(ValueError, match="unknown city"):
        ws.resolve_place("Atlantis")
    monkeypatch.setitem(wp.CITY_TABLE, "portland, me", (43.66, -70.26))
    with pytest.raises(ValueError, match="ambiguous"):
        wp.resolve_city("portland")


def test_place_fetch_sends_only_the_catalogue_and_station_files(tmp_path):
    rec = Recorder(isd_years=(2024,))
    audit = wp.WeatherAudit(str(tmp_path / "a.ndjson"))
    s = ws.oat_reference_auto(
        None,
        None,
        "2024-03-01",
        "2024-03-31",
        source="isd",
        place="KORD",
        privacy="coarse",
        cache_dir=str(tmp_path / "wx"),
        audit=audit,
        clock=_clock(2024, 9, 1),
        **rec.kw(),
    )
    assert len(s) and s.attrs["weather_place"]["kind"] == "airport"
    assert s.attrs["isd_station"]["usaf"] == "725300"
    assert all("ncei.noaa.gov" in u and "?" not in u for u in rec.urls)
    assert [r["url"] for r in audit.records if r["sent"]] == rec.urls
    city = ws.oat_reference_auto(
        None,
        None,
        "2024-03-01",
        "2024-03-01",
        source="nasa_power",
        place="Chicago, IL",
        privacy="coarse",
        cache_dir=str(tmp_path / "wx"),
        transport=rec.power,
    )
    assert city.attrs["weather_place"]["kind"] == "city"
    with pytest.raises(ValueError, match="latitude and longitude, or a place"):
        ws.oat_reference_auto(None, None, "2024-03-01", "2024-03-01", source="nasa_power")


# --------------------------------------------------------------------------- default byte-identity


def test_no_policy_is_exactly_the_old_behaviour(tmp_path):
    rec_old, rec_new = Recorder(isd_last="2024-06-30 23:00"), Recorder(isd_last="2024-06-30 23:00")
    with pytest.warns(UserWarning):
        old = ws.oat_reference_blended(
            LAT,
            LON,
            "2024-01-01",
            "2024-12-31",
            fallbacks=("nasa_power", "open_meteo"),
            clock=_clock(2026, 9, 25),
            **rec_old.kw(),
        )
    with pytest.warns(UserWarning):
        new = ws.oat_reference_auto(
            LAT,
            LON,
            "2024-01-01",
            "2024-12-31",
            source="auto",
            privacy="public",
            audit=wp.WeatherAudit(str(tmp_path / "a.ndjson")),
            clock=_clock(2026, 9, 25),
            **rec_new.kw(),
        )
    assert rec_old.urls == rec_new.urls  # the same requests, byte for byte
    pd.testing.assert_series_equal(old, new)
    assert "privacy" not in old.attrs["weather_provenance"]
    assert "weather_privacy" not in old.attrs
    # the guard with no policy and no audit is the cached transport itself
    inner = Recorder().power
    assert wp.guarded_transport("nasa_power", inner) is inner
    # an old public POWER-cell URL still hits the same cache under coarse
    assert ws.nasa_power_url(*ws.power_grid_cell(LAT, LON), "20240101", "20240101") == (
        ws.nasa_power_url(LAT, LON, "20240101", "20240101", privacy="coarse")
    )
    assert ws.IsdStation("1", "2", "n", 1.0, 2.0, "a", "b").as_dict() == {
        "usaf": "1",
        "wban": "2",
        "name": "n",
        "latitude": 1.0,
        "longitude": 2.0,
        "begin": "a",
        "end": "b",
    }


# --------------------------------------------------------------------------- EIA and URDB


def test_eia_and_urdb_requests_are_checked_audited_and_redacted(tmp_path):
    from camber.interop import eia, openei

    urls = []

    def fake_eia(url):
        urls.append(url)
        return {"response": {"data": [{"period": "2024-01", "price": 12.0}]}}

    audit = wp.WeatherAudit(str(tmp_path / "a.ndjson"), facility_id="fac-9")
    sp = eia.fetch_state_price(
        "electricity",
        "IL",
        "2024-01",
        "2024-01",
        transport=fake_eia,
        privacy="coarse",
        audit=audit,
    )
    assert sp.state == "IL" and len(urls) == 1
    assert audit.records[0]["service"] == "eia" and audit.records[0]["sent"]
    wp.check_url("eia", urls[0], "coarse")
    # offline: zero calls, the cache only
    with pytest.raises(ws.WeatherCacheMiss):
        eia.fetch_state_price(
            "electricity",
            "IL",
            "2024-01",
            "2024-01",
            transport=fake_eia,
            privacy="offline",
            cache_dir=str(tmp_path / "eia"),
        )
    assert len(urls) == 1

    seen = []

    def fake_urdb(url):
        seen.append(url)
        return {"items": [{"label": "abc"}]}

    rate = openei.fetch_urdb_rate(
        "5cb0b5f25457a3c26a2cc0b5",
        "SECRETKEY",
        transport=fake_urdb,
        privacy="coarse",
        audit=audit,
    )
    assert rate == {"label": "abc"} and "api_key=SECRETKEY" in seen[0]
    logged = audit.records[-1]
    assert logged["service"] == "urdb" and "SECRETKEY" not in logged["url"]
    assert "api_key=REDACTED" in logged["url"]
    with pytest.raises(wp.OfflineViolation, match="urdb_file"):
        openei.fetch_urdb_rate("abc", "K", transport=fake_urdb, privacy="offline")
    assert len(seen) == 1
    raw = (tmp_path / "a.ndjson").read_text()
    assert "SECRETKEY" not in raw


# --------------------------------------------------------------------------- configs


def _bills(tmp_path):
    rng = np.random.default_rng(3)
    days = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    rows, i = [], 0
    while i < len(days):
        j = min(i + 30, len(days))
        rows.append(
            {
                "start": days[i].date(),
                "end": days[j - 1].date(),
                "therms": round(float(1000 + 37 * (j - i) + rng.normal(0, 5)), 3),
                "account": "ACCT-778899",
                "meter": "MTR-445566",
            }
        )
        i = j
    pd.DataFrame(rows).to_csv(tmp_path / "gas.csv", index=False)
    return [str(r["therms"]) for r in rows]


def _cfg(**oat):
    return {
        "site": "Private Office Tower",
        "facility_id": "private-office-tower",
        "mv": [
            {
                "bills": {"file": "gas.csv", "energy": "therms"},
                "name": "Gas meter",
                "period": ["2023-01-01", "2024-12-31"],
                "oat": {"fetch": "auto", "latitude": LAT, "longitude": LON, **oat},
            }
        ],
    }


def _patch_network(monkeypatch, rec):
    monkeypatch.setattr(ws, "isd_transport", lambda **k: rec.isd)
    monkeypatch.setattr(ws, "nasa_power_transport", lambda **k: rec.power)
    monkeypatch.setattr(ws, "open_meteo_transport", lambda **k: rec.meteo)


def test_config_never_sends_names_ids_identity_or_energy(tmp_path, monkeypatch):
    from camber.config import run_config

    energy = _bills(tmp_path)
    rec = Recorder(isd_years=(2023, 2024), isd_last="2024-10-31 23:00")
    _patch_network(monkeypatch, rec)
    monkeypatch.setenv("USER", "someone")
    cfg = _cfg(cache_dir="wx")
    cfg["weather"] = {"privacy": "coarse"}
    with pytest.warns(UserWarning):
        res = run_config(cfg, base_dir=str(tmp_path))
    (b,) = [f for f in res.findings if f.rule == "mv_baseline"]
    assert "privacy coarse" in b.metrics["oat_source"]
    assert rec.urls
    forbidden = [
        *SECRETS,
        "Private",
        "Office",
        "Tower",
        "private-office-tower",
        "ACCT",
        "778899",
        "MTR",
        "445566",
        "someone",
        "@",
        "gas.csv",
        *energy,
    ]
    for u in rec.urls:
        for bad in forbidden:
            assert bad not in u, (bad, u)
    log = tmp_path / "wx" / wp.AUDIT_FILE
    rows = wp.read_weather_audit(str(log))
    assert [r["url"] for r in rows if r["sent"]] == rec.urls
    assert {r["purpose"] for r in rows} == {"M&V billing weather"}
    assert all(r["facility_id"] == "private-office-tower" for r in rows)


def test_private_config_defaults_to_offline_and_declines_cleanly(tmp_path, monkeypatch):
    from camber.config import run_config

    _bills(tmp_path)
    rec = Recorder(fail=True)
    _patch_network(monkeypatch, rec)
    monkeypatch.setenv("CAMBER_WEATHER_DIR", str(tmp_path / "default"))
    cfg = _cfg()
    cfg["private"] = True
    res = run_config(cfg, base_dir=str(tmp_path))
    assert rec.urls == []
    why = " ".join(f.summary for f in res.findings)
    assert "offline" in why and "supply a weather file" in why
    cfg["weather"] = {"privacy": "public"}
    with pytest.raises(ValueError, match="marked private"):
        run_config(cfg, base_dir=str(tmp_path))
    cfg["private"] = "yes"
    with pytest.raises(ValueError, match="true or false"):
        run_config(cfg, base_dir=str(tmp_path))


def test_registry_private_flag_applies_to_every_config(tmp_path, monkeypatch):
    from camber.cli import main
    from camber.config import run_config
    from camber.portfolio import Portfolio

    ws_root = tmp_path / "pf"
    pf = Portfolio.init(str(ws_root))
    pf.add_facility(
        "Private Office Tower",
        facility_id="private-office-tower",
        reason="t",
        activate=True,
        private=True,
    )
    assert pf.facility("private-office-tower")["private"] is True
    _bills(tmp_path)
    rec = Recorder(fail=True)
    _patch_network(monkeypatch, rec)
    monkeypatch.setenv("CAMBER_WEATHER_DIR", str(tmp_path / "default"))
    cfg = _cfg()
    cfg["workspace"] = "pf"
    res = run_config(cfg, base_dir=str(tmp_path))
    assert rec.urls == [] and "offline" in " ".join(f.summary for f in res.findings)
    log = wp.weather_audit_path(workspace=str(ws_root), facility_id="private-office-tower")
    assert log.endswith(os.path.join("state", "private-office-tower", wp.AUDIT_FILE))
    rows = wp.read_weather_audit(log)
    assert rows and all(not r["sent"] and r["privacy"] == "offline" for r in rows)
    # opt in to coarse: requests go out, coarsened, logged to the facility's state dir
    rec2 = Recorder(isd_years=(2023, 2024), isd_last="2024-10-31 23:00")
    _patch_network(monkeypatch, rec2)
    cfg["weather"] = {"privacy": "coarse"}
    with pytest.warns(UserWarning):
        run_config(cfg, base_dir=str(tmp_path))
    assert rec2.urls and all(s not in u for u in rec2.urls for s in SECRETS)
    assert [r["url"] for r in wp.read_weather_audit(log) if r["sent"]] == rec2.urls
    # the CLI: clear the flag (audited), and read the log back
    assert (
        main(
            [
                "facility",
                "private",
                "private-office-tower",
                "--off",
                "--reason",
                "t",
                "--workspace",
                str(ws_root),
            ]
        )
        == 0
    )
    assert not pf.facility("private-office-tower")["private"]
    assert any(r["action"] == "facility.private" for r in pf.audit_log())
    assert (
        main(
            ["weather", "audit", "--workspace", str(ws_root), "--facility", "private-office-tower"]
        )
        == 0
    )
    assert main(["weather", "audit", "--workspace", str(ws_root), "--json"]) == 0
    monkeypatch.chdir(tmp_path)
    assert main(["weather", "audit", "--facility", "x"]) == 1  # no workspace


def test_weather_audit_cli(tmp_path, capsys):
    from camber.cli import main

    a = wp.WeatherAudit(str(tmp_path / wp.AUDIT_FILE), facility_id="f1", privacy="coarse")
    a.record(
        service="nasa_power",
        url="https://power.larc.nasa.gov/x?latitude=42.0",
        cache="miss",
        sent=True,
        purpose="p",
    )
    a.record(
        service="isd",
        url="https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv",
        cache="hit",
        sent=False,
        error="E: x",
    )
    assert main(["weather", "audit", "--cache-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "SENT" in out and "local" in out and "2 request(s), 1 sent" in out and "[f1]" in out
    assert main(["weather", "audit", "--file", str(tmp_path / wp.AUDIT_FILE), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_rcx_reference_follows_a_private_facility(tmp_path, monkeypatch):
    from camber.report import rcx

    rec = Recorder(fail=True)
    _patch_network(monkeypatch, rec)
    monkeypatch.setenv("CAMBER_WEATHER_DIR", str(tmp_path / "default"))
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    wctx = wp.WeatherContext(private=True)
    spec = {"fetch": "nasa_power", "latitude": LAT, "longitude": LON}
    with pytest.raises(ws.WeatherCacheMiss, match="supply a weather file"):
        rcx._load_reference_oat(spec, idx, wctx)
    assert rec.urls == []
    guard = rcx._weather_guard({**spec, "place": "KORD"}, wctx)
    assert guard["privacy"].privacy == "offline" and guard["place"] == "KORD"
    assert rcx._weather_guard({"csv": "x.csv"}, wctx) == {}
    assert rcx._weather_guard(spec, None) == {}  # public, nowhere to audit: the old call

    class Run:
        config = {"private": True}
        base_dir = str(tmp_path)

    assert rcx._weather_of(rcx.RcxOptions(), Run()).private is True
    assert rcx._weather_of(rcx.RcxOptions(), object()) is None


# --------------------------------------------------------------------------- real network


@pytest.mark.network
def test_live_coarse_fetch_at_a_public_airport(tmp_path):
    """Chicago O'Hare's published coordinates (a public airport, never a private site)."""
    audit = wp.WeatherAudit(str(tmp_path / "a.ndjson"))
    s = ws.oat_reference_auto(
        41.995,
        -87.934,
        "2024-01-01",
        "2024-01-03",
        source="auto",
        privacy="coarse",
        cache_dir=str(tmp_path / "wx"),
        audit=audit,
    )
    assert len(s) > 40 and 0 < s.mean() < 60
    for r in audit.records:
        wp.check_url(r["service"], r["url"], "coarse")
        assert "41.995" not in r["url"] and "87.934" not in r["url"]
