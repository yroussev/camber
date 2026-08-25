# Live weather fetch — NASA POWER (`camber.weather_source`)

CAMBER's weather-dependent analytics — M&V weather normalization and OAT-sensor validation — need an
external temperature series. Until now you brought your own (a local EPW/TMY file via
`mandv.weather.load_epw`, or any series you already had). `camber.weather_source` **fetches** one:
hourly historical air temperature (and optionally relative humidity) from **NASA POWER**
(<https://power.larc.nasa.gov>) — a free, keyless, global reanalysis service — in the exact °F Series
shape (`name="oat_f"`) those consumers already accept.

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

`start`/`end` accept `YYYYMMDD` / `YYYY-MM-DD` strings or date/datetime objects. `parameters` are NASA
POWER codes (`T2M` = 2 m air temperature, `RH2M` = 2 m relative humidity).

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

**Not (yet) here:** an on-disk cache (the injected transport is the seam for one), NOAA/ISD station
ingest, and multi-year request chunking (NASA POWER caps a single hourly request at ~1 year).
