# Weather fetch — NASA POWER + NOAA/ISD (`camber.weather_source`)

CAMBER's weather-dependent analytics — M&V weather normalization and OAT-sensor validation — need an
external temperature series. Until now you brought your own (a local EPW/TMY file via
`mandv.weather.load_epw`, or any series you already had). `camber.weather_source` **fetches** one from
either of two free, keyless providers — **NASA POWER** (global reanalysis; the default below) and
**NOAA/ISD** (real weather stations; see the [NOAA/ISD section](#noaaisd-station-data-a-second-station-precise-provider))
— in the exact °F Series shape (`name="oat_f"`) those consumers already accept.

```sh
# no API key, global coverage; returns a °F pandas Series
python - <<'PY'
from camber.weather_source import oat_reference
ref = oat_reference(34.05, -118.24, "20240101", "20240107", tz="America/Los_Angeles")
print(ref.head())
PY
```

## API

| Function | Returns | Use |
|---|---|---|
| `nasa_power_url(lat, lon, start, end, *, parameters, community)` | `str` | the query URL (pure, no I/O) |
| `nasa_power_transport(*, timeout)` | `callable(url) -> dict` | the default stdlib-`urllib` transport |
| `fetch_nasa_power(lat, lon, start, end, *, parameters, transport, tz, timeout)` | `DataFrame` | `oat_f` (°F) + `rh_pct` when requested |
| `oat_reference(lat, lon, start, end, *, transport, tz, timeout)` | `Series` | just the °F OAT reference (NaNs dropped) |
| `cached_transport(inner, cache_dir, *, ttl, clock)` | `callable(url) -> dict` | wrap any transport with an on-disk cache |
| `geocode(address, *, transport, limit, user_agent, timeout)` | `GeoResult` | address → top match `(latitude, longitude, display_name)` |
| `oat_reference_for(address, start, end, *, tz, geocode_transport, transport, ...)` | `Series` | geocode an address, then fetch its °F OAT |
| `nominatim_url(address, *, limit)` · `nominatim_transport(*, user_agent, timeout)` | `str` · `callable(url) -> dict` | the geocoder's URL builder + default transport |
| `oat_reference_isd(lat, lon, start, end, *, transport, catalog_transport, tz, ...)` | `Series` | **NOAA/ISD** station-precise °F OAT (nearest covering station) |
| `isd_nearest_station(lat, lon, start, end, *, transport, stations, ...)` | `IsdStation` | nearest ISD station covering the window |
| `isd_stations(*, transport, timeout)` · `fetch_isd(usaf, wban, start, end, *, ...)` | `list[IsdStation]` · `DataFrame` | the station catalog · one station's hourly °F |
| `isd_transport(*, timeout)` · `cached_bytes_transport(inner, cache_dir, *, ttl, clock)` | `callable(url) -> bytes` | the ISD default transport + its on-disk cache |

`start`/`end` accept `YYYYMMDD` / `YYYY-MM-DD` strings or date/datetime objects. `parameters` are NASA
POWER codes (`T2M` = 2 m air temperature, `RH2M` = 2 m relative humidity). `GeoResult` and `IsdStation`
are frozen values with `.as_dict()`.

## Geocoding — fetch by address, not just coordinates

NASA POWER is a lat/lon *point* query, so to fetch weather "for an address" you geocode it first, via
[OpenStreetMap Nominatim](https://nominatim.openstreetmap.org) — also **free and keyless**:

```python
from camber.weather_source import geocode, oat_reference_for

g = geocode("Chicago, IL")
print(g.display_name)  # "Chicago, Cook County, Illinois, United States" — confirm the match
print(g.latitude, g.longitude)

# or, one call: geocode the address then fetch its OAT reference
ref = oat_reference_for("Chicago, IL", "2024-01-01", "2024-12-31", tz="America/Chicago")
```

For an **uncertain** address, geocode first and check `.display_name` before fetching. The resolved
place is also attached to `ref.attrs["geocode"]` (best-effort metadata).

- **Precision is a non-issue.** NASA POWER is a ~0.5° (~50 km) reanalysis grid, so city/ZIP-level
  geocoding is plenty — this is a *convenience*, not an address-precision claim.
- **Usage policy.** Nominatim requests send a descriptive `User-Agent` (built in) and ask for ≤ ~1
  request/second with caching — so cache your lookups: `cached_transport(nominatim_transport(),
  cache_dir)` composes with `geocode` exactly like it does with the NASA transport.
- **Timezone still isn't derived** from the address (no dependency-light lat/lon→zone) — pass the site
  IANA `tz` to `oat_reference_for`, the same load-bearing switch as `oat_reference` (below).

## Timezone — read this before you join it to a sensor

NASA POWER hourly timestamps are **UTC**. BAS trend exports are **naive local clock time** (see
[TIME-HANDLING.md](TIME-HANDLING.md)), and `sensordrift.compare_to_reference` aligns the two by an
inner join on shared timestamps — which pandas refuses across a tz-aware/naive mismatch. So:

- `tz="UTC"` (default) — the returned index is **tz-aware UTC**, with no hidden shift. Join it to a
  UTC sensor series.
- `tz="<IANA zone>"` (e.g. `"America/Los_Angeles"`) — the index is DST-correctly converted, then the
  tz is dropped, giving **naive local civil time** that inner-joins directly to a BAS trend index.

NASA's LST option is *solar* time, not clock time, so it would not line up with a DST-observing BAS
export; this adapter deliberately does not use it. Getting this wrong is the one way to silently
corrupt a drift bias or a normalization, so it is an explicit knob, tested for the exact hour mapping.

## Two things it drops into

- **Sensor validation** — feed it as the reference to
  [`sensordrift.compare_to_reference`](VALIDATION.md) to check the site OAT sensor against what the
  weather actually did (bias / drift-per-month / tracking correlation) — otherwise impossible from the
  BAS alone.
- **M&V** — a fetched series feeds `mandv.weather.monthly_normals` / `normalized_annual_from_monthly`
  exactly like an EPW series (same dtype, name, and index kind). NASA POWER hourly is **actual
  reanalysis**, ideal for the *reporting-period actual weather*; for a **typical-year (TMY)**
  normalization baseline, keep using `mandv.weather.load_epw`.

Requesting `RH2M` too lets you derive wet-bulb with `coolingtower.stull_wetbulb_f(oat_f, rh_pct)`.

## Dependency-light + offline-testable

The network call goes through an **injectable transport** (`callable(url) -> parsed-JSON dict`, default
stdlib `urllib` — the same seam as `ingest.haystack.http_json_transport`). Inject your own callable to
add a cache, or a canned one in tests, so every parse / unit / timezone / fill path runs with **no
network**. No third-party dependency (stdlib `urllib` / `json` + pandas). Missing hours (NASA's `-999`
sentinel) become `NaN` rather than a bogus `-999 °C`; a response with no data for a parameter raises a
clear `ValueError`.

## Multi-year requests

NASA POWER caps a single hourly request at ~1 year, but `fetch_nasa_power` handles that
transparently: it splits `[start, end]` into consecutive **calendar-year** chunks (one call per
year), then concatenates them into a single, unique, sorted hourly index. A three-year request "just
works" — no extra argument. Calendar-year seams share no day (one chunk ends Dec-31, the next starts
Jan-01), so no hour is duplicated or dropped.

## On-disk cache

`cached_transport(inner, cache_dir)` wraps *any* transport with a dependency-light on-disk cache, so
repeated fetches don't re-hit the API — and, combined with year-chunking, a re-run only downloads the
years missing from disk:

```python
from camber.weather_source import fetch_nasa_power, nasa_power_transport, cached_transport

transport = cached_transport(nasa_power_transport(), "/var/cache/camber-weather")
df = fetch_nasa_power(34.05, -118.24, "20200101", "20231231", transport=transport)
```

Each URL's parsed JSON is memoized to `<cache_dir>/<sha256(url)>.json` with an atomic write; a corrupt
file self-heals (treated as a miss). NASA POWER historical reanalysis is **stable**, so the default is
cache-forever; the most recent ~months can be revised, so pass a `ttl` (a `datetime.timedelta`) for
windows that touch recent data. `clock` is injectable for deterministic TTL tests.

## NOAA/ISD station data (a second, station-precise provider)

NASA POWER is a global reanalysis on a **~0.5° (~50 km) grid**. When you want a *real weather station*
near the site, `camber.weather_source` also fetches **NOAA's Integrated Surface Database (ISD-Lite)** —
also keyless. `oat_reference_isd` finds the nearest station covering your window and returns the same
°F `oat_f` Series:

```python
from camber.weather_source import oat_reference_isd, isd_nearest_station

st = isd_nearest_station(41.88, -87.63, "2023-01-01", "2023-12-31")
print(st.name, st.usaf, st.wban)  # confirm the station it picked

ref = oat_reference_isd(41.88, -87.63, "2023-01-01", "2023-12-31", tz="America/Chicago")
# ref -> °F Series (name "oat_f"); the resolved station is on ref.attrs["isd_station"]
```

**Station-precise but gappy — the honest trade-off.** ISD is a *point* measurement at a real station,
higher spatial fidelity than NASA's grid **when a station is nearby** — but it is **gappy** (stations
go offline; missing hours are common → dropped to NaN) and **sparse** (no station near remote sites;
coverage windows vary — `isd_nearest_station` filters to stations whose record spans your window and
raises if none does). So: **ISD when a nearby station covers the window and you want station fidelity;
NASA POWER for global coverage, a gap-free series, or anywhere without a station.** They complement.

- **Endpoints** (keyless): the station catalog
  <https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv> (~5 MB) and per-station-per-year gzipped
  hourly files under `.../isd-lite/`. Air temp is tenths of °C; the `-9999` missing sentinel → NaN.
- **Timezone** is the same load-bearing switch as the NASA path (pass the site IANA `tz`; see above).
- **Caching.** ISD uses a **bytes** transport (gzipped/CSV, not JSON), so it has its own cache
  decorator — `cached_bytes_transport(isd_transport(), cache_dir)`. Cache the 5 MB catalog, or resolve
  the station list once with `isd_stations()` and pass it to `isd_nearest_station(..., stations=…)`.
