# Weather fetch — NASA POWER, NOAA/ISD and Open-Meteo (`camber.weather_source`)

CAMBER's weather-dependent analytics — M&V weather normalization and OAT-sensor validation — need an
external temperature series. Until now you brought your own (a local EPW/TMY file via
`mandv.weather.load_epw`, or any series you already had). `camber.weather_source` **fetches** one from
either of two free, keyless providers — **NASA POWER** (global reanalysis; the default below) and
**NOAA/ISD** (real weather stations; see the [NOAA/ISD section](#noaaisd-station-data-a-second-station-precise-provider))
— in the exact °F Series shape (`name="oat_f"`) those consumers already accept. Since 0.92,
**Open-Meteo** (keyless reanalysis) is a third source; see [Open-Meteo](#open-meteo-a-third-source-provisional-092).

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
| `oat_reference_blended(lat, lon, start, end, *, tz, transport, stations, power_transport, ...)` | `Series` | *provisional* — ISD, gaps from the next station, the rest from bias-corrected NASA POWER ([below](#isd-with-a-nasa-power-fallback-provisional)) |
| `power_grid_cell(lat, lon)` · `isd_catalog_end(stations)` | `(lat, lon)` · `str` | *provisional* — the POWER cell centre · the catalog's latest station end date |

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
export; this adapter deliberately does not use it. LST is the service's *default*, so the URL sends
`time-standard=UTC` explicitly (before 0.90.1 it did not, and the hour keys it parsed as UTC were
local solar time -- about six hours off at a US Central site), and a payload whose header says
anything but UTC is refused. Getting this wrong is the one way to silently
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
- **A missing year no longer aborts.** A station-year file that does not exist (HTTP 404, which
  happens even inside the catalog's coverage) is skipped with a warning and listed in
  `frame.attrs["isd_missing_years"]`; `fetch_isd(..., on_missing_year="raise")` restores the abort.
  `cached_bytes_transport` remembers a 404, so an offline re-run sees the same gap.
- **A stale catalog is named.** When no station covers a window that ends after the catalog's latest
  station end date, `isd_nearest_station` warns that the catalog looks stale and says so in its error.

## ISD with a NASA POWER fallback (provisional)

`oat_reference_blended` (or `oat_reference_isd(..., fallback="nasa_power")`) returns the same `oat_f`
Series, but does not fail when the ISD catalog ends before your window or one station-year is missing:

1. **Stations.** Up to `max_stations` (3) stations within `max_distance_km` (100 km), nearest first.
   A station still reporting when the catalog was built is tried past its catalog end date, in case
   only the catalog is stale. The nearest station that returns data is the reference and fills the
   window. Each later one fills only runs of at least `gap_hours` (24) missing hours. Since 0.92 it is
   first **offset-corrected** against the reference: the same monthly and hour-of-day correction as
   below, estimated over the hours both stations report (`station_offsets=False` turns this off).
2. **NASA POWER** fills what is still missing, read at the snapped grid cell (`power_grid_cell`: the
   0.5° × 0.625° cell centre, so every point in a cell shares one URL and one cache entry). POWER lags
   real time by days to weeks and returns not-yet-published trailing hours as fill: those stay
   *missing*, never data, and the last real hour is reported as the coverage end.
3. **Bias correction.** POWER is corrected against the reference station (the nearest one with data)
   by a **monthly mean offset**, station − POWER, over the hours both carry in
   `[min(start, end − overlap_days), end]` (`overlap_days` = 365). A calendar month with at least 240
   paired hours (~10 days, pooled across years) gets its own offset; otherwise its meteorological
   season's (DJF/MAM/JJA/SON, at least 240 pairs); otherwise one offset over all pairs (at least 168);
   otherwise none, and the caveat says **UNCORRECTED**. Since 0.92 an **hour-of-day** step follows
   (`diurnal=True`, the default). What the monthly offset leaves is averaged per (season, UTC hour),
   where a cell has at least 30 pairs; otherwise per UTC hour over all seasons; otherwise 0.
   Reanalysis damps the daily cycle (warm nights, cool afternoons), and a monthly offset cannot
   remove that. Each cell's residual averages zero, so the monthly and daily means do not move. The
   RMSE of the paired hours is recorded before (`rmse_before_f`), after the monthly step
   (`rmse_after_monthly_f`) and after both (`rmse_after_f`).
4. **Provenance.** `series.attrs["weather_provenance"]` holds the stations tried (distance, missing
   years, hours filled), the per-source date `segments` (`isd:<usaf>-<wban>`, `nasa_power`,
   `missing`), the POWER cell and coverage end, the `bias_correction` (offsets, basis per month, pair
   counts, RMSE) and the `caveats`, which are also on `series.attrs["caveats"]`. Any fallback raises one
   `UserWarning` with the same text.

`cache_dir=` caches every source. A POWER or Open-Meteo response whose trailing hours are not yet
published is not cached, so it is re-fetched once it is. With `offline=True` only the cache is read,
and a miss raises `WeatherCacheMiss`, so a re-run is reproducible without the network.

<!-- 092-mv -->
### How much the corrections help (0.92)

The measure is out of sample: each correction was estimated on 2022-2023 and tested on 2024, at
three public airports with a second ISD station 3-22 km away. The table gives the hourly RMSE
against the airport station, in °F:

| Site | Source | Raw | Monthly offset | + hour of day |
|---|---|---|---|---|
| Chicago O'Hare | NASA POWER | 4.67 | 3.83 | **2.86** |
| | Open-Meteo | 3.35 | 2.60 | **2.31** |
| | nearby station | 2.11 | 1.99 | **1.96** |
| Phoenix Sky Harbor | NASA POWER | 7.30 | 5.85 | **2.99** |
| | Open-Meteo | 4.27 | 2.99 | **2.72** |
| | nearby station | 3.68 | 2.04 | **1.95** |
| Boston Logan | NASA POWER | 4.98 | 4.20 | **3.43** |
| | Open-Meteo | 2.99 | 2.82 | **2.59** |
| | nearby station | 1.67 | 1.45 | **1.32** |

The daily-mean RMSE (1.2-2.1 °F) is the same with or without the hour-of-day step, which leaves
daily means alone by design. The step matters for hourly uses (sensor-drift checks, hourly models,
degree-hours), not for daily or billing M&V. For those the monthly offset is the correction that
counts.

### The fallback order, and one entry point

`oat_reference_blended(..., fallbacks=("nasa_power",))` is the 0.90.1 behaviour and stays the
default. `fallbacks=("nasa_power", "open_meteo")` fills whatever POWER leaves (its publication lag,
a failed request) from Open-Meteo, which is corrected the same way. A source whose request fails is
recorded in `weather_provenance["fallbacks"]` and the next one is tried. With a single fallback, a
failure is still raised. Each fallback's grid cell, coverage end, hours filled and
`bias_correction` are recorded there, and POWER's also stay under `power` / `bias_correction`.

`oat_reference_auto(lat, lon, start, end, source=...)` picks the source by name:

| `source` | What it returns |
|---|---|
| `"auto"` | ISD, offset-corrected neighbouring stations, then NASA POWER, then Open-Meteo |
| `"isd"` | ISD stations only (gap-filled, offset-corrected) |
| `"nasa_power"` | NASA POWER alone, uncorrected |
| `"open_meteo"` | Open-Meteo alone, uncorrected |

A config reaches it in two places:
- the RCx report's `oat_reference` (`{"fetch": "auto", "latitude", "longitude", "tz", "cache_dir",
  "offline"}`), where the report lists the hours from each source and the fallback caveats under
  the OAT comparison. `{"fetch": "nasa_power"}` still means POWER alone;
- an `mv` billing entry's `oat` (see [MANDV.md](MANDV.md#billing-data)).

Weather requests carry coordinates and dates only: no key, account or other identifier.

## Open-Meteo, a third source (provisional, 0.92)

`fetch_open_meteo(lat, lon, start, end)` reads Open-Meteo's historical-weather API
(`archive-api.open-meteo.com`): hourly 2 m temperature in °F, UTC, from its `best_match` reanalysis
(ERA5 and higher-resolution companions; pass `models=` to choose one). The frame's `attrs` carry the
grid point (coordinates and elevation) as `open_meteo_cell`, and `open_meteo_coverage_end`.
`oat_reference_open_meteo` returns the `oat_f` series. Requests are one per calendar year, through the
same injectable JSON transport as POWER (`open_meteo_transport`, `cached_transport`). `null` hours
are missing, never data. A payload not in UTC, or in an unknown unit, is refused.

Like POWER it is gridded, not a station, so in the blend it is a fallback, bias-corrected against
the reference station. In the table above its raw error is lower than POWER's. **Terms.**
Open-Meteo's data are licensed CC BY 4.0: attribute "Weather data by Open-Meteo.com" where you
publish them. Its free API is for non-commercial use under its terms of service, within limits
(fewer than 10,000 calls a day). Commercial use needs one of its subscriptions or a self-hosted
instance. The reanalysis itself comes from the Copernicus Climate Change Service. These terms were
checked on open-meteo.com on 2026-09-27. CAMBER only sends the request, and whether your use is
covered is for you to check.
<!-- /092-mv -->
