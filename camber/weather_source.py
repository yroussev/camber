"""Live weather fetch from NASA POWER — an external reference series for M&V + sensor validation.

CAMBER can weather-normalize M&V and validate a temperature sensor against an external reference,
but until now the reference had to be a *local* EPW/TMY file (`camber.mandv.weather.load_epw`) or a
series the caller brought themselves (`camber.sensordrift.compare_to_reference`). This module
*fetches* one:
hourly historical air temperature (and optionally relative humidity) from **NASA POWER**
(https://power.larc.nasa.gov) — a free, keyless, global reanalysis service — and returns it in the
exact °F Series shape those consumers already expect (`name="oat_f"`, matching `load_epw`).
Two more sources follow the same contract: NOAA ISD-Lite stations (station-precise, gappy) and,
since 0.92, **Open-Meteo** (keyless gridded reanalysis). :func:`oat_reference_blended` joins them:
the nearest station first, then offset-corrected neighbouring stations, then bias-corrected
gridded fallbacks; :func:`oat_reference_auto` selects a source by name.

Dependency-light and testable: the HTTP call goes through an **injectable transport** (a
``callable(url) -> parsed-JSON dict``, default a stdlib ``urllib`` one, mirroring
`camber.ingest.haystack.http_json_transport`), so every parse / unit / timezone / fill path is
tested on canned JSON with **no network**. No third-party dependency (stdlib ``urllib``/``json``).

**Timezone (the load-bearing detail).** NASA POWER hourly timestamps are UTC. BAS trend exports
are naive *local clock* time (see docs/TIME-HANDLING.md), and `sensordrift.compare_to_reference`
aligns on shared timestamps by an inner join — which pandas refuses across a tz-aware/naive
mismatch. So ``tz="UTC"`` (default) returns a tz-aware UTC index (no hidden shift); a site IANA zone
``"America/Los_Angeles"``) converts DST-correctly and drops the tz, yielding **naive local civil
time** that joins directly to a BAS sensor series. (NASA's LST option is solar time, not clock time,
so it would not match a DST-observing export — it is deliberately not used.)

NASA POWER hourly is *actual reanalysis*, not a typical-meteorological-year: ideal for validating a
sensor against what the weather actually did and for reporting-period actual weather; for a TMY
normalization baseline, keep using `mandv.weather.load_epw`.
"""

from __future__ import annotations

import csv
import datetime as _dt
import gzip
import hashlib
import io
import json
import math
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import pandas as pd

from .mandv.weather import c_to_f
from .weather_privacy import (
    OFFLINE_HELP,
    POWER_GRID,
    WeatherPolicy,
    coarsen,
    coarsening_note,
    default_weather_dir,
    grid_snap,
    guarded_transport,
    resolve_city,
)

__all__ = [
    "FILL_VALUE",
    "nasa_power_url",
    "nasa_power_transport",
    "cached_transport",
    "fetch_nasa_power",
    "oat_reference",
    "GeoResult",
    "nominatim_url",
    "nominatim_transport",
    "geocode",
    "oat_reference_for",
    # NOAA/ISD-Lite station source (a second provider; a bytes transport, not JSON)
    "IsdStation",
    "isd_transport",
    "cached_bytes_transport",
    "isd_stations",
    "isd_nearest_station",
    "fetch_isd",
    "oat_reference_isd",
    # provisional (0.90.1): ISD with a bias-corrected NASA POWER fallback
    "POWER_GRID_DEG",
    "WeatherCacheMiss",
    "power_grid_cell",
    "isd_catalog_end",
    "oat_reference_blended",
    # provisional (0.92): Open-Meteo as a third source, and one entry point by source name
    "FALLBACKS",
    "open_meteo_url",
    "open_meteo_transport",
    "fetch_open_meteo",
    "oat_reference_open_meteo",
    "oat_reference_auto",
    # provisional (0.94, #73): privacy guardrails -- a station file URL and local place names
    "isd_url",
    "resolve_place",
]

_BASE_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
FILL_VALUE = -999.0  # NASA POWER hourly missing sentinel (values <= this are dropped to NaN)
_PARAM_COL = {"T2M": "oat_f", "RH2M": "rh_pct"}  # NASA parameter -> output column
# The POWER meteorology grid (MERRA-2 / GEOS-IT native): 0.5° latitude x 0.625° longitude, with
# cell centres on multiples of the step. Provisional.
POWER_GRID_DEG = POWER_GRID
_POWER_HOURLY_START = pd.Timestamp("2001-01-01")  # first day the hourly point API serves

# OpenStreetMap Nominatim geocoding (free, keyless). Its usage policy requires a descriptive
# User-Agent and asks for <= ~1 request/second + caching (compose with cached_transport).
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_USER_AGENT = "camber-toolkit (https://github.com/yroussev/camber)"

# NOAA Integrated Surface Database (ISD-Lite), keyless. Station catalog + per-station-per-year
# gzipped hourly files. Missing sentinel is -9999; air temp is in tenths of °C.
_ISD_HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
_ISD_DATA_BASE = "https://www.ncei.noaa.gov/pub/data/noaa/isd-lite"
_ISD_MISSING = -9999


def _yyyymmdd(d) -> str:
    """Normalize a date (``YYYYMMDD``/``YYYY-MM-DD`` string, or a date/datetime) to ``YYYYMMDD``."""
    if isinstance(d, str):
        return d.replace("-", "")
    return pd.Timestamp(d).strftime("%Y%m%d")


def nasa_power_url(
    latitude,
    longitude,
    start,
    end,
    *,
    parameters: Sequence[str] = ("T2M",),
    community: str = "RE",
    time_standard: str = "UTC",
    privacy=None,
) -> str:
    """Build the NASA POWER hourly point-query URL (pure — the testable half, no I/O).

    ``parameters`` are NASA POWER codes (``T2M`` = 2 m air temp °C, ``RH2M`` = 2 m rel. humidity %);
    ``start``/``end`` accept ``YYYYMMDD``/``YYYY-MM-DD`` strings or date/datetime objects.
    ``time_standard`` is sent explicitly: the service's own default is ``LST`` (local *solar*
    time), and this module parses the hour keys as UTC. The coordinates pass through
    :func:`camber.weather_privacy.coarsen`: with ``privacy`` ``"coarse"`` / ``"offline"``
    (0.94) the URL carries the POWER grid-cell centre; ``None`` / ``"public"`` leaves them as given.
    """
    from urllib.parse import urlencode

    latitude, longitude = coarsen("nasa_power", latitude, longitude, privacy)

    query = urlencode(
        {
            "parameters": ",".join(parameters),
            "community": community,
            "latitude": latitude,
            "longitude": longitude,
            "start": _yyyymmdd(start),
            "end": _yyyymmdd(end),
            "format": "JSON",
            "time-standard": time_standard,
        }
    )
    return f"{_BASE_URL}?{query}"


def power_grid_cell(latitude, longitude) -> tuple[float, float]:
    """The POWER meteorology grid-cell centre holding ``(latitude, longitude)``. Provisional.

    The grid is regular (:data:`POWER_GRID_DEG`), so rounding to the nearest step multiple gives
    a stable per-cell key: every point in one ~55 km cell maps to the same centre, and requesting
    the centre reads the same cell the raw point would.
    """
    return grid_snap(latitude, longitude, *POWER_GRID_DEG)


def nasa_power_transport(*, timeout: float = 30.0) -> Callable[[str], dict]:
    """Return the default stdlib-``urllib`` transport: ``callable(url) -> parsed JSON dict``.

    Mirrors `camber.ingest.haystack.http_json_transport`; inject your own callable (a cache or a
    test double) via ``fetch_nasa_power(..., transport=...)`` to avoid the network entirely.
    """
    import json as _json
    from urllib.request import Request, urlopen

    def transport(url: str) -> dict:  # pragma: no cover - the one real-network path
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310 - https NASA POWER endpoint
            return _json.loads(resp.read().decode("utf-8"))

    return transport


def _default_clock() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class WeatherCacheMiss(LookupError):
    """A cache-only (``offline=True``) read found nothing on disk. Provisional.

    Raised instead of a silent live fetch, so an offline, reproducible re-run fails loudly rather
    than reaching the network (or presenting a slow success).
    """


def cached_transport(
    inner: Callable[[str], dict],
    cache_dir: str,
    *,
    ttl: _dt.timedelta | None = None,
    clock: Callable[[], _dt.datetime] | None = None,
    offline: bool = False,
    should_cache: Callable[[dict], bool] | None = None,
) -> Callable[[str], dict]:
    """Wrap a transport with a dependency-light on-disk cache keyed by URL (composes with any).

    Each URL's parsed JSON is memoized to ``<cache_dir>/<sha256(url)>.json`` (an atomic write via
    ``os.replace``); a hit returns the stored copy, a miss delegates to ``inner`` and writes it.
    NASA POWER historical reanalysis is stable, so the default is **cache-forever** (``ttl=None``);
    the most recent ~months can be revised, so pass a ``ttl`` for windows touching recent data. A
    corrupt/torn cache file is treated as a miss (self-healing). ``clock`` (default UTC ``now``) is
    injectable so TTL expiry is deterministic in tests; it should return a tz-aware UTC datetime.
    stdlib ``json``/``hashlib``/``os`` only.

    Provisional keywords: ``offline=True`` never calls ``inner`` -- a miss raises
    :class:`WeatherCacheMiss` (cache-first, offline-reproducible reads); ``should_cache(payload)``
    decides whether a fetched payload is written at all (the POWER fallback uses it to keep a
    response whose trailing hours are still fill from being frozen into a cache-forever entry).
    """
    tick = clock or _default_clock

    def transport(url: str) -> dict:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    env = json.load(f)
                fresh = (
                    ttl is None
                    or offline  # an offline read serves whatever the cache holds
                    or (tick() - _dt.datetime.fromisoformat(env["fetched_utc"])) < ttl
                )
                if fresh:
                    return env["payload"]
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                pass  # corrupt / torn write / bad timestamp -> treat as a miss and re-fetch
        if offline:
            raise WeatherCacheMiss(f"offline: no cached response for {url}")
        payload = inner(url)
        if should_cache is not None and not should_cache(payload):
            return payload
        env = {"fetched_utc": tick().isoformat(), "url": url, "payload": payload}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(env, f)
        os.replace(tmp, path)  # atomic publish (mirror store/facilities._write, edge/spool.enqueue)
        return payload

    return transport


def _index(keys, tz: str) -> pd.DatetimeIndex:
    """Parse ``YYYYMMDDHH`` keys to a DatetimeIndex; UTC-aware, or DST-correct naive-local."""
    idx = pd.to_datetime(list(keys), format="%Y%m%d%H", utc=True)
    if tz.upper() == "UTC":
        return idx
    return idx.tz_convert(tz).tz_localize(
        None
    )  # naive local civil time (joins to a BAS trend index)


class _NoData(ValueError):
    """A NASA POWER response with a valid but *empty* parameter block (no hours in the window)."""


def _year_chunks(start, end) -> list[tuple[str, str]]:
    """Split ``[start, end]`` into consecutive calendar-year ``(YYYYMMDD, YYYYMMDD)`` windows.

    NASA POWER hourly rejects a window longer than ~1 year. Calendar-year chunks share **no day** at
    a seam (one ends Dec-31, the next starts Jan-01), so no hour is duplicated or dropped.
    """
    s = pd.Timestamp(_yyyymmdd(start))
    e = pd.Timestamp(_yyyymmdd(end))
    if e < s:
        raise ValueError(f"end {end!r} is before start {start!r}")
    out = []
    for y in range(s.year, e.year + 1):
        cs = max(s, pd.Timestamp(year=y, month=1, day=1))
        ce = min(e, pd.Timestamp(year=y, month=12, day=31))
        out.append((cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d")))
    return out


def _fetch_one(
    latitude, longitude, start, end, *, parameters, transport, tz, privacy=None
) -> pd.DataFrame:
    """Fetch a single ≤1-year window; raise :class:`_NoData` on a valid-but-empty block."""
    payload = transport(
        nasa_power_url(latitude, longitude, start, end, parameters=parameters, privacy=privacy)
    )
    try:
        param_block = payload["properties"]["parameter"]
    except (KeyError, TypeError) as e:
        raise ValueError("NASA POWER response missing properties.parameter") from e
    header = payload.get("header") if isinstance(payload, dict) else None
    standard = str((header or {}).get("time_standard") or "UTC").upper()
    if standard != "UTC":  # e.g. a payload cached from a URL that let the service default to LST
        raise ValueError(
            f"NASA POWER response is in time standard {standard!r}, not UTC; the hour keys would "
            "be read hours off (re-fetch with time-standard=UTC)"
        )

    columns: dict = {}
    for p in parameters:
        raw = param_block.get(p)
        if not raw:
            raise _NoData(
                f"NASA POWER returned no {p} data for ({latitude}, {longitude}) {start}..{end}"
            )
        keys = sorted(raw)  # chronological YYYYMMDDHH keys
        values = pd.Series([float(raw[k]) for k in keys], index=_index(keys, tz), dtype=float)
        values = values.where(values > FILL_VALUE)  # -999 fill -> NaN (never treated as -999 °C)
        if p == "T2M":
            values = c_to_f(values)  # °C -> °F, matching load_epw's contract
        columns[_PARAM_COL.get(p, p.lower())] = values
    return pd.DataFrame(columns)


def fetch_nasa_power(
    latitude,
    longitude,
    start,
    end,
    *,
    parameters: Sequence[str] = ("T2M",),
    transport: Callable[[str], dict] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    snap_to_cell: bool = False,
    privacy=None,
) -> pd.DataFrame:
    """Fetch hourly NASA POWER weather as a DataFrame (``oat_f`` in °F; ``rh_pct`` when requested).

    ``snap_to_cell=True`` (provisional) requests the POWER grid-cell centre
    (:func:`power_grid_cell`) instead of the raw point: the service returns the same cell either
    way, but the snapped URL -- hence a :func:`cached_transport` key -- is shared by every point
    in the cell. The result's ``attrs`` carry ``power_cell`` (requested and cell coordinates) and
    ``power_coverage_end`` (the last hour holding a real value: POWER lags real time by days to
    weeks and returns its not-yet-available trailing hours as fill, which become NaN here).
    ``privacy`` (0.94, provisional) ``"coarse"`` / ``"offline"`` always requests the cell centre
    (see :mod:`camber.weather_privacy`), whatever ``snap_to_cell`` says.

    Multi-year windows work transparently: the request is split into calendar-year chunks (NASA
    POWER caps a single hourly request at ~1 year), one transport call per year, concatenated into a
    single unique, sorted hourly index. ``transport`` (default the stdlib one) is
    ``callable(url) -> parsed-JSON dict`` — inject a canned one to run offline, or wrap it with
    :func:`cached_transport`. ``tz`` is ``"UTC"`` (tz-aware) or a site IANA zone (naive local; see
    the module docstring). Missing hours (``<= FILL_VALUE``) become NaN; a request returning no data
    for a parameter across *every* chunk raises ``ValueError``. numpy/pandas + stdlib.
    """
    transport = transport or nasa_power_transport(timeout=timeout)
    pol = WeatherPolicy.coerce(privacy)
    snap = snap_to_cell or (pol is not None and pol.coarse)
    qlat, qlon = power_grid_cell(latitude, longitude) if snap else (latitude, longitude)
    frames = []
    for cs, ce in _year_chunks(start, end):
        try:
            frames.append(
                _fetch_one(
                    qlat,
                    qlon,
                    cs,
                    ce,
                    parameters=parameters,
                    transport=transport,
                    tz=tz,
                    privacy=pol,
                )
            )
        except _NoData:
            continue  # a covered-but-empty year (e.g. running into an undefined range) — skip it
    if not frames:
        raise _NoData(
            f"NASA POWER returned no {parameters[0]} data for "
            f"({latitude}, {longitude}) {start}..{end}"
        )
    frame = pd.concat(frames)
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    valid = frame.dropna(how="all")
    frame.attrs["power_coverage_end"] = str(valid.index.max()) if len(valid) else None
    frame.attrs["power_cell"] = {
        "requested_latitude": float(latitude),
        "requested_longitude": float(longitude),
        "latitude": float(qlat),
        "longitude": float(qlon),
        "snapped": bool(snap),
        "resolution_deg": list(POWER_GRID_DEG),
    }
    if pol is not None:
        frame.attrs["power_cell"]["privacy"] = pol.privacy
    return frame


def oat_reference(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], dict] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    privacy=None,
) -> pd.Series:
    """Fetch just the outdoor-air-temperature reference series (°F, NaNs dropped, ``name="oat_f"``).

    The exact shape `sensordrift.compare_to_reference` and `mandv.weather.monthly_normals` consume
    — so a fetched series drops in wherever a `load_epw` series would. Pass the site IANA ``tz`` to
    get a naive-local index that inner-joins to a BAS sensor trend (see the module docstring).
    ``privacy`` (0.94) as in :func:`fetch_nasa_power`.
    """
    df = fetch_nasa_power(
        latitude,
        longitude,
        start,
        end,
        transport=transport,
        tz=tz,
        timeout=timeout,
        **({} if privacy is None else {"privacy": privacy}),
    )
    return df["oat_f"].dropna()


# --------------------------------------------------------------------------- geocoding (by address)
#
# NASA POWER is a lat/lon point query, so to fetch weather "for an address" you first geocode it.
# OpenStreetMap Nominatim is free and keyless (like NASA POWER). Precision honesty: NASA POWER is a
# ~0.5° (~50 km) reanalysis grid, so city/ZIP-level geocoding is plenty — a *convenience*, not an
# address-precision claim. Uses the same injectable ``callable(url) -> dict`` transport seam, so it
# is offline-testable and composes with :func:`cached_transport`.


@dataclass(frozen=True)
class GeoResult:
    """The top geocoding match: coordinates plus the human-readable place to confirm it."""

    latitude: float
    longitude: float
    display_name: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def nominatim_url(address, *, limit: int = 1, privacy=None) -> str:
    """Build the Nominatim search URL (pure — the testable half; URL-encodes the address).

    Geocoding sends the address itself, so with ``privacy`` ``"coarse"`` / ``"offline"`` (0.94)
    it is refused (:class:`~camber.weather_privacy.PrivacyViolation`): name a city or an airport
    code, resolved locally by :func:`resolve_place`, instead.
    """
    from urllib.parse import urlencode

    from .weather_privacy import PrivacyViolation

    pol = WeatherPolicy.coerce(privacy)
    if pol is not None and pol.coarse:
        raise PrivacyViolation(
            f"privacy {pol.privacy!r}: geocoding would send the address; give coordinates, a "
            "city or an airport code (resolve_place, local) instead"
        )

    query = urlencode({"q": address, "format": "json", "limit": limit})
    return f"{_NOMINATIM_URL}?{query}"


def nominatim_transport(
    *, user_agent: str = _DEFAULT_USER_AGENT, timeout: float = 30.0
) -> Callable[[str], dict]:
    """Return the default stdlib-``urllib`` transport for Nominatim: ``callable(url) -> dict``.

    Sends a descriptive ``User-Agent`` (Nominatim's usage policy blocks the default urllib UA); stay
    <= ~1 request/second and cache results (wrap with :func:`cached_transport`).
    """
    import json as _json
    from urllib.request import Request, urlopen

    def transport(url: str) -> dict:  # pragma: no cover - the one real-network path
        request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310 - https Nominatim endpoint
            return _json.loads(resp.read().decode("utf-8"))

    return transport


def geocode(
    address,
    *,
    transport: Callable[[str], dict] | None = None,
    limit: int = 1,
    user_agent: str = _DEFAULT_USER_AGENT,
    timeout: float = 30.0,
    privacy=None,
) -> GeoResult:
    """Geocode an address / place name to coordinates via OpenStreetMap Nominatim (free, keyless).

    Returns the top match as a :class:`GeoResult` — ``.display_name`` (e.g. "Chicago, Cook County,
    Illinois, United States") lets you confirm it before fetching weather. Inject a ``transport``
    (canned JSON) to run offline, or wrap :func:`nominatim_transport` with :func:`cached_transport`.
    Raises ``ValueError`` on no match or a malformed row. stdlib ``urllib``/``json`` only.
    ``privacy`` ``"coarse"`` / ``"offline"`` refuses before anything is sent (0.94).
    """
    url = nominatim_url(address, limit=limit, privacy=privacy)
    transport = transport or nominatim_transport(user_agent=user_agent, timeout=timeout)
    rows = transport(url)
    if not rows:
        raise ValueError(f"no geocoding match for {address!r}")
    top = rows[0]
    try:
        return GeoResult(float(top["lat"]), float(top["lon"]), str(top["display_name"]))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Nominatim row missing lat/lon/display_name: {top!r}") from e


def oat_reference_for(
    address,
    start,
    end,
    *,
    tz: str = "UTC",
    geocode_transport: Callable[[str], dict] | None = None,
    transport: Callable[[str], dict] | None = None,
    user_agent: str = _DEFAULT_USER_AGENT,
    timeout: float = 30.0,
) -> pd.Series:
    """Geocode ``address``, then fetch the °F OAT reference series for it (geocode + oat_reference).

    A convenience over :func:`geocode` + :func:`oat_reference`: returns the same °F Series
    (``name="oat_f"``) that drops into ``sensordrift.compare_to_reference`` / M&V. Two transport
    seams — ``geocode_transport`` (Nominatim) and ``transport`` (NASA POWER) — so both halves are
    offline-injectable. The resolved place is attached to ``series.attrs["geocode"]`` (best-effort).
    ``tz`` is **not** derived from the address (no dependency-light lat/lon->zone) — pass the site
    IANA zone for a naive-local index that joins to a BAS trend; a wrong ``tz`` silently offsets it.
    For an uncertain address, geocode first and confirm ``.display_name`` before fetching.
    """
    g = geocode(address, transport=geocode_transport, user_agent=user_agent, timeout=timeout)
    series = oat_reference(
        g.latitude, g.longitude, start, end, transport=transport, tz=tz, timeout=timeout
    )
    series.attrs["geocode"] = g.as_dict()  # non-fragile metadata; pandas may drop attrs across ops
    return series


# --------------------------------------------------------------------------- NOAA/ISD-Lite station
#
# A second, *station-precise* weather source: real NOAA/NCEI stations (vs NASA POWER's ~0.5°
# (~50 km) reanalysis grid). Higher spatial fidelity when a station is nearby, but **gappy**
# (stations offline; missing hours are common) and **sparse** (no station near remote sites; the
# coverage window varies) -- so it complements, not replaces, NASA POWER (global + gap-free +
# coarse). Returns the
# same °F ``oat_f`` Series contract and reuses the same ``_index`` timezone switch.
#
# ISD is not JSON: the catalog is CSV and the hourly files are gzipped fixed-width, so this uses a
# **different transport type** -- ``callable(url) -> bytes`` (the parser decodes) -- with its own
# default factory (:func:`isd_transport`) and cache sibling (:func:`cached_bytes_transport`). The
# bytes seam does NOT compose with the JSON seam (``nasa_power_transport`` / ``cached_transport``).


@dataclass(frozen=True)
class IsdStation:
    """A NOAA ISD station: identity, coordinates, and its ``YYYYMMDD`` coverage window."""

    usaf: str
    wban: str
    name: str
    latitude: float
    longitude: float
    begin: str  # YYYYMMDD (first day of record)
    end: str  # YYYYMMDD (last day of record)
    icao: str = ""  # the airport code, when the station is one (0.94, provisional)

    def as_dict(self) -> dict:
        """Return the station as a plain dict (``icao`` only when the station has one)."""
        d = asdict(self)
        if not d["icao"]:
            del d["icao"]
        return d


def isd_transport(*, timeout: float = 30.0) -> Callable[[str], bytes]:
    """Return the default stdlib transport for NOAA ISD: ``callable(url) -> raw bytes``.

    A *different* contract from :func:`nasa_power_transport` (raw ``bytes``, not parsed JSON) --
    the ISD catalog is CSV and the hourly files are gzipped, so the parser decodes. Inject a canned
    one to run offline, or wrap with :func:`cached_bytes_transport` (the JSON
    :func:`cached_transport` does not apply to bytes).
    """
    from urllib.request import Request, urlopen

    def transport(url: str) -> bytes:  # pragma: no cover - the one real-network path
        with urlopen(Request(url), timeout=timeout) as resp:  # noqa: S310 - https NCEI endpoint
            return resp.read()

    return transport


def cached_bytes_transport(
    inner: Callable[[str], bytes],
    cache_dir: str,
    *,
    ttl: _dt.timedelta | None = None,
    clock: Callable[[], _dt.datetime] | None = None,
    offline: bool = False,
) -> Callable[[str], bytes]:
    """On-disk cache for a **bytes** transport (the ISD analog of :func:`cached_transport`).

    Memoizes each URL's raw bytes to ``<cache_dir>/<sha256(url)>.bin`` (an ISO-timestamp header line
    then the payload), with an atomic ``os.replace``. Caching matters here: the station catalog is
    ~5 MB and per-year files repeat. Default cache-forever (``ttl=None``); ``clock`` (tz-aware UTC)
    is injectable so TTL expiry is deterministic in tests; a corrupt file self-heals.
    ``offline=True`` (provisional) never calls ``inner``: a miss raises :class:`WeatherCacheMiss`.
    """
    tick = clock or _default_clock

    def transport(url: str) -> bytes:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".bin")
        if os.path.exists(path):
            hit = None
            try:
                with open(path, "rb") as f:
                    header, _, payload = f.read().partition(b"\n")
                when, _, status = header.decode("ascii").partition("\t")
                stamp = _dt.datetime.fromisoformat(when)
                if ttl is None or offline or (tick() - stamp) < ttl:
                    hit = int(status) if status else 0
            except (OSError, ValueError):
                pass  # corrupt / torn / bad timestamp -> treat as a miss and re-fetch
            if hit:  # a remembered "not found": replay it, so an offline re-run agrees
                raise _http_not_found(url, hit)
            if hit == 0:
                return payload
        if offline:
            raise WeatherCacheMiss(f"offline: no cached response for {url}")
        try:
            payload = inner(url)
        except Exception as e:
            code = _not_found_code(e)
            if code is None:
                raise
            _write_atomic(path, f"{tick().isoformat()}\t{code}".encode("ascii") + b"\n")
            raise
        _write_atomic(path, tick().isoformat().encode("ascii") + b"\n" + payload)
        return payload

    return transport


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)  # atomic publish (mirror cached_transport / store.facilities._write)


def _not_found_code(exc: BaseException) -> int | None:
    """404/410 when ``exc`` says "no such file" (HTTP, or a local-file transport), else None."""
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError) and exc.code in (404, 410):
        return int(exc.code)
    if isinstance(exc, FileNotFoundError):
        return 404
    return None


def _http_not_found(url: str, code: int = 404):
    import urllib.error

    return urllib.error.HTTPError(url, code, "Not Found (cached)", None, None)  # type: ignore[arg-type]


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (km) between two lat/lon points (stdlib math only)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def isd_stations(
    *, transport: Callable[[str], bytes] | None = None, timeout: float = 30.0
) -> list[IsdStation]:
    """Fetch + parse the NOAA ISD station catalog (``isd-history.csv``) into :class:`IsdStation`.

    The catalog is ~5 MB; wrap ``transport`` with :func:`cached_bytes_transport`, or pass the result
    to :func:`isd_nearest_station` via ``stations=`` to avoid re-downloading. Rows with a blank or
    null-island (``0.000``) latitude/longitude are skipped.
    """
    transport = transport or isd_transport(timeout=timeout)
    text = transport(_ISD_HISTORY_URL).decode("utf-8", "replace")
    out: list[IsdStation] = []
    for row in csv.DictReader(io.StringIO(text)):
        lat, lon = (row.get("LAT") or "").strip(), (row.get("LON") or "").strip()
        if lat in ("", "0.000") or lon in ("", "0.000"):
            continue
        try:
            out.append(
                IsdStation(
                    usaf=(row.get("USAF") or "").strip(),
                    wban=(row.get("WBAN") or "").strip(),
                    name=(row.get("STATION NAME") or "").strip(),
                    latitude=float(lat),
                    longitude=float(lon),
                    begin=(row.get("BEGIN") or "").strip(),
                    end=(row.get("END") or "").strip(),
                    icao=(row.get("ICAO") or "").strip().upper(),
                )
            )
        except ValueError:
            continue  # unparseable lat/lon -> skip
    return out


def isd_nearest_station(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], bytes] | None = None,
    stations: list[IsdStation] | None = None,
    timeout: float = 30.0,
) -> IsdStation:
    """Nearest ISD station to ``(latitude, longitude)`` whose coverage spans ``[start, end]``.

    Great-circle (haversine) nearest among stations with ``begin <= start`` and ``end >= end`` (so a
    decommissioned or not-yet-begun station isn't picked). Pass ``stations=`` (from
    :func:`isd_stations`) to skip the ~5 MB catalog download. Raises ``ValueError`` if none covers.
    """
    cat = stations if stations is not None else isd_stations(transport=transport, timeout=timeout)
    s, e = _yyyymmdd(start), _yyyymmdd(end)
    covering = [st for st in cat if st.begin and st.end and st.begin <= s and st.end >= e]
    if not covering:
        last = isd_catalog_end(cat)
        hint = ""
        if last and e > last:
            _warn_stale(last, e)
            hint = (
                f"; the station catalog ends {last}, so no station can cover a later window "
                "(oat_reference_isd(..., fallback='nasa_power') fills it from NASA POWER)"
            )
        raise ValueError(f"no ISD station covers ({latitude}, {longitude}) {start}..{end}{hint}")
    return min(
        covering, key=lambda st: _haversine_km(latitude, longitude, st.latitude, st.longitude)
    )


def isd_url(usaf, wban, year) -> str:
    """The ISD-Lite file URL of one station-year (pure; provisional, 0.94).

    It carries the station id and the year only -- never the site's coordinates: the station is
    chosen locally from the catalogue (:func:`isd_nearest_station`, :func:`resolve_place`).
    """
    return f"{_ISD_DATA_BASE}/{int(year)}/{usaf}-{wban}-{int(year)}.gz"


def _place_code(text: str) -> str | None:
    """``"station"`` for ``USAF-WBAN``, ``"airport"`` for an ICAO code in capitals, else None."""
    text = str(text).strip()
    if re.fullmatch(r"[0-9A-Z]{6}-\d{5}", text.upper()):
        return "station"
    # an airport code is written in capitals ("KORD"); "Rome" is a city
    if re.fullmatch(r"[A-Z][A-Z0-9]{3}", text):
        return "airport"
    return None


def resolve_place(place, *, stations=None) -> dict:
    """A location named without coordinates, resolved **locally** (provisional, 0.94).

    ``place`` is an airport ICAO code (``"KORD"``) or an ISD station id (``"725300-94846"``),
    looked up in ``stations`` (the ISD catalogue, :func:`isd_stations`; its download carries
    nothing site-specific), or a city in :data:`camber.weather_privacy.CITY_TABLE`
    (``"Chicago, IL"``). Returns ``{"latitude", "longitude", "kind", "label"}`` plus the
    ``station`` (as a dict) for an airport or station. Nothing is geocoded over the network;
    ``ValueError`` when the place is unknown (or a code is given without ``stations``).
    """
    text = str(place).strip()
    code = text.upper()
    kind = _place_code(text)
    is_station = kind == "station"
    if kind:
        if stations is None:
            raise ValueError(f"{text!r} is a station or airport code: the ISD catalogue is needed")
        if is_station:
            hits = [s for s in stations if f"{s.usaf}-{s.wban}" == code]
        else:
            hits = [s for s in stations if s.icao == code]
        if hits:
            st = max(hits, key=lambda s: s.end or "")
            return {
                "latitude": st.latitude,
                "longitude": st.longitude,
                "kind": "station" if is_station else "airport",
                "label": f"{code} ({st.name}, ISD {st.usaf}-{st.wban})",
                "station": st.as_dict(),
            }
        kind = "ISD station" if is_station else "airport with ICAO code"
        raise ValueError(f"no {kind} {text!r} in the ISD catalogue")
    key, lat, lon = resolve_city(text)
    return {"latitude": lat, "longitude": lon, "kind": "city", "label": key}


def isd_catalog_end(stations) -> str | None:
    """The latest ``end`` (``YYYYMMDD``) any station in the catalog reports. Provisional.

    A live catalog ends within days of today; a much older value means the catalog (or the cached
    copy of it) is stale, and every window after it has no "covering" station.
    """
    ends = [st.end for st in stations if st.end]
    return max(ends) if ends else None


def _warn_stale(last: str, wanted: str) -> None:
    import warnings

    warnings.warn(
        f"the ISD station catalog looks stale: its latest station end date is {last}, before the "
        f"requested {wanted}. Refresh a cached catalog, or fall back to another source for the "
        "later dates",
        UserWarning,
        stacklevel=3,
    )


def _parse_isd_lite(raw: bytes, tz: str, *, dew_point: bool) -> pd.DataFrame:
    """Parse one gzipped ISD-Lite year into a °F frame (``oat_f``; ``dewpt_f`` when requested)."""
    lines = gzip.decompress(raw).decode("ascii", "replace").splitlines()
    keys, temps, dews = [], [], []
    for line in lines:
        f = line.split()
        if len(f) < 6:
            continue
        keys.append(f"{int(f[0]):04d}{int(f[1]):02d}{int(f[2]):02d}{int(f[3]):02d}")
        temps.append(int(f[4]))
        dews.append(int(f[5]))

    def _degf(vals):
        s = pd.Series([float(v) for v in vals], dtype=float)
        s = s.where(s != _ISD_MISSING)  # -9999 -> NaN
        return c_to_f(s / 10.0)  # tenths of °C -> °C -> °F

    if not keys:
        return pd.DataFrame({"oat_f": pd.Series(dtype=float)})
    idx = _index(keys, tz)
    cols = {"oat_f": _degf(temps).to_numpy()}
    if dew_point:
        cols["dewpt_f"] = _degf(dews).to_numpy()
    return pd.DataFrame(cols, index=idx)


def fetch_isd(
    usaf,
    wban,
    start,
    end,
    *,
    transport: Callable[[str], bytes] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    dew_point: bool = False,
    on_missing_year: str = "skip",
) -> pd.DataFrame:
    """Fetch hourly NOAA ISD-Lite data for a station (``oat_f`` °F; ``dewpt_f`` when requested).

    One gzipped file per calendar year in ``[start, end]``, concatenated into a single unique,
    sorted index and trimmed to the window. ``-9999`` becomes NaN; air temp (tenths of °C) becomes
    °F. ``tz`` is the SAME switch as the NASA path: ``"UTC"`` (tz-aware) or a site IANA zone (naive
    local). Raises ``ValueError`` if no year returned data. stdlib ``gzip``/``urllib`` + pandas.

    A year whose file does not exist (HTTP 404/410 -- it happens even inside the catalog's
    coverage) no longer aborts the fetch: it is skipped with a warning and listed in
    ``frame.attrs["isd_missing_years"]``. ``on_missing_year="raise"`` restores the old abort.
    """
    import warnings

    if on_missing_year not in ("skip", "raise"):
        raise ValueError("on_missing_year must be 'skip' or 'raise'")
    transport = transport or isd_transport(timeout=timeout)
    s, e = pd.Timestamp(_yyyymmdd(start)), pd.Timestamp(_yyyymmdd(end)) + pd.Timedelta(hours=23)
    frames = []
    missing: list[int] = []
    for year in range(s.year, e.year + 1):
        try:
            raw = transport(isd_url(usaf, wban, year))
        except Exception as exc:
            if on_missing_year == "raise" or _not_found_code(exc) is None:
                raise
            missing.append(year)
            continue
        df = _parse_isd_lite(
            raw, "UTC", dew_point=dew_point
        )  # parse+filter in UTC, tz-switch after
        if not df.empty:
            frames.append(
                df[(df.index >= s.tz_localize("UTC")) & (df.index <= e.tz_localize("UTC"))]
            )
    frames = [f for f in frames if not f.empty]
    gone = f" (no file for {', '.join(map(str, missing))})" if missing else ""
    if not frames:
        raise ValueError(f"no ISD data for {usaf}-{wban} {start}..{end}{gone}")
    if missing:
        warnings.warn(
            f"ISD station {usaf}-{wban} has no file for {', '.join(map(str, missing))}; those "
            "years are missing from the series",
            UserWarning,
            stacklevel=2,
        )
    frame = pd.concat(frames)
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    if tz.upper() != "UTC":  # apply the naive-local switch after the UTC-based windowing
        frame.index = frame.index.tz_convert(tz).tz_localize(None)
    frame.attrs["isd_missing_years"] = missing
    return frame


def oat_reference_isd(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], bytes] | None = None,
    catalog_transport: Callable[[str], bytes] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    fallback: str | None = None,
    power_transport: Callable[[str], dict] | None = None,
) -> pd.Series:
    """Find the nearest covering ISD station to a lat/lon, then fetch its °F OAT reference series.

    A station-precise counterpart to :func:`oat_reference`: returns the same °F ``oat_f`` Series
    (NaNs dropped) with the resolved station on ``series.attrs["isd_station"]``. Two transport seams
    (``catalog_transport`` for the station list, ``transport`` for the hourly data), both
    offline-injectable. ``tz`` is explicit (the same load-bearing switch as :func:`oat_reference`).

    ``fallback="nasa_power"`` (provisional) delegates to :func:`oat_reference_blended`: missing
    station years are filled from the next-nearest station, and dates no station serves (e.g.
    after the catalog's end) from bias-corrected NASA POWER (``power_transport``), with the source
    of every date recorded in ``series.attrs["weather_provenance"]``. ``fallback="open_meteo"``
    (0.92) falls back to Open-Meteo instead (its transport is the stdlib default; use
    :func:`oat_reference_blended` to inject one).
    """
    if fallback is not None:
        if fallback not in FALLBACKS:
            raise ValueError(f"fallback must be None or one of {FALLBACKS}")
        return oat_reference_blended(
            latitude,
            longitude,
            start,
            end,
            transport=transport,
            catalog_transport=catalog_transport,
            power_transport=power_transport,
            tz=tz,
            timeout=timeout,
            fallbacks=(fallback,),
        )
    station = isd_nearest_station(
        latitude, longitude, start, end, transport=catalog_transport, timeout=timeout
    )
    df = fetch_isd(
        station.usaf, station.wban, start, end, transport=transport, tz=tz, timeout=timeout
    )
    series = df["oat_f"].dropna()
    series.attrs["isd_station"] = station.as_dict()
    return series


# ------------------------------------------------------------------------ Open-Meteo (third source)
#
# Provisional (0.92). Open-Meteo's historical-weather API serves hourly reanalysis (ERA5 and its
# higher-resolution companions, "best_match" by default) for any point, keyless. Like NASA POWER it
# is gap-free and gridded, not a station, so the blend uses it as a fallback and bias-corrects it
# against the reference station exactly as it does POWER. Its data are CC BY 4.0 (attribute
# "Weather data by Open-Meteo.com"), and the free API is for non-commercial use under its terms --
# see docs/WEATHER.md. The request carries coordinates and dates only: no key, no identifier.

_OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
_OPEN_METEO_START = pd.Timestamp("1940-01-01")  # the archive's first day


def open_meteo_url(
    latitude, longitude, start, end, *, models: str | None = None, privacy=None
) -> str:
    """Build the Open-Meteo historical-weather URL for hourly 2 m temperature in °F, UTC (pure).

    Provisional (0.92). ``start`` / ``end`` accept ``YYYYMMDD`` / ``YYYY-MM-DD`` strings or dates;
    ``models`` selects a reanalysis (the service default, ``best_match``, when ``None``). The
    coordinates pass through :func:`camber.weather_privacy.coarsen`: with ``privacy``
    ``"coarse"`` / ``"offline"`` (0.94) they are rounded to its ``precision_deg`` (default 0.1°).
    """
    from urllib.parse import urlencode

    latitude, longitude = coarsen("open_meteo", latitude, longitude, privacy)

    def iso(d):
        t = _yyyymmdd(d)
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"

    q = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": iso(start),
        "end_date": iso(end),
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
    }
    if models:
        q["models"] = models
    return f"{_OPEN_METEO_URL}?{urlencode(q)}"


def open_meteo_transport(*, timeout: float = 30.0) -> Callable[[str], dict]:
    """The default stdlib transport for Open-Meteo: ``callable(url) -> parsed JSON`` (provisional).

    Sends only an ``Accept`` header (no key, no identifier). Compose with
    :func:`cached_transport`, or inject a canned callable to run offline.
    """
    import json as _json
    from urllib.request import Request, urlopen

    def transport(url: str) -> dict:  # pragma: no cover - the one real-network path
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310 - https Open-Meteo endpoint
            return _json.loads(resp.read().decode("utf-8"))

    return transport


def _open_meteo_complete(payload) -> bool:
    """False when the last hour is still ``null``: Open-Meteo has not published it yet."""
    try:
        vals = payload["hourly"]["temperature_2m"]
    except (KeyError, TypeError):
        return False
    return bool(vals) and vals[-1] is not None


def fetch_open_meteo(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], dict] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    models: str | None = None,
    privacy=None,
) -> pd.DataFrame:
    """Fetch hourly Open-Meteo reanalysis temperature as a frame (``oat_f``, °F). Provisional.

    One request per calendar year (the cache key of :func:`cached_transport`), concatenated into a
    unique, sorted hourly index; ``null`` hours become NaN. ``tz`` is the same switch as the NASA
    POWER and ISD paths (``"UTC"`` tz-aware, or a site IANA zone for naive local time). The result's
    ``attrs`` carry ``open_meteo_cell`` (requested and grid-point coordinates, elevation, model)
    and ``open_meteo_coverage_end`` (the last hour holding a value). A payload not in UTC, or in an
    unknown temperature unit, is refused; a window with no values raises ``ValueError``.
    ``privacy`` (0.94) rounds the request coordinates (see :func:`open_meteo_url`).
    """
    transport = transport or open_meteo_transport(timeout=timeout)
    pol = WeatherPolicy.coerce(privacy)
    frames = []
    meta: dict = {}
    for cs, ce in _year_chunks(start, end):
        payload = transport(open_meteo_url(latitude, longitude, cs, ce, models=models, privacy=pol))
        try:
            hourly = payload["hourly"]
            times, vals = hourly["time"], hourly["temperature_2m"]
        except (KeyError, TypeError) as e:
            raise ValueError("Open-Meteo response missing hourly.time / temperature_2m") from e
        if int(payload.get("utc_offset_seconds") or 0) != 0:
            raise ValueError("Open-Meteo response is not in UTC (timezone=GMT was not honoured)")
        unit = str((payload.get("hourly_units") or {}).get("temperature_2m") or "°F")
        s = pd.Series(
            [float("nan") if v is None else float(v) for v in vals],
            index=pd.to_datetime(list(times), utc=True),
            dtype=float,
        )
        if unit.replace("°", "").strip().upper() in ("C", "CELSIUS"):
            s = c_to_f(s)
        elif unit.replace("°", "").strip().upper() not in ("F", "FAHRENHEIT"):
            raise ValueError(f"Open-Meteo temperature unit {unit!r} is neither °F nor °C")
        frames.append(s)
        if not meta:
            meta = {
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "elevation_m": payload.get("elevation"),
            }
    oat = pd.concat(frames) if frames else pd.Series(dtype=float)
    oat = oat[~oat.index.duplicated(keep="first")].sort_index()
    if oat.dropna().empty:
        raise _NoData(f"Open-Meteo returned no data for ({latitude}, {longitude}) {start}..{end}")
    if tz.upper() != "UTC":
        oat.index = oat.index.tz_convert(tz).tz_localize(None)
    frame = pd.DataFrame({"oat_f": oat})
    valid = oat.dropna()
    frame.attrs["open_meteo_coverage_end"] = str(valid.index.max()) if len(valid) else None
    frame.attrs["open_meteo_cell"] = {
        "requested_latitude": float(latitude),
        "requested_longitude": float(longitude),
        **meta,
        "models": models or "best_match",
    }
    if pol is not None:
        qlat, qlon = coarsen("open_meteo", latitude, longitude, pol)
        frame.attrs["open_meteo_cell"].update(
            privacy=pol.privacy, sent_latitude=float(qlat), sent_longitude=float(qlon)
        )
    return frame


def oat_reference_open_meteo(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], dict] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
    privacy=None,
) -> pd.Series:
    """The Open-Meteo °F OAT series (NaNs dropped, ``name="oat_f"``). Provisional (0.92)."""
    df = fetch_open_meteo(
        latitude,
        longitude,
        start,
        end,
        transport=transport,
        tz=tz,
        timeout=timeout,
        **({} if privacy is None else {"privacy": privacy}),
    )
    s = df["oat_f"].dropna()
    s.name = "oat_f"
    s.attrs["open_meteo_cell"] = df.attrs["open_meteo_cell"]
    return s


# ------------------------------------------------------------------------- ISD + gridded fallbacks
#
# Provisional (0.90.1; refined 0.92). ISD is station-precise but gappy, and its catalog can lag
# real time by months; NASA POWER and Open-Meteo are gap-free and global but gridded reanalysis that
# lags real time by days to weeks. The blend takes the station where it exists and fills the rest,
# correcting each other source's offset against the reference station over the dates both cover,
# and says which dates came from where.

_SEASON = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM"}
_SEASON.update({6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"})
_MIN_PAIRS_MONTH = 240  # paired hours (~10 days) for a month to get its own offset
_MIN_PAIRS_SEASON = 240  # ... else its season's offset
_MIN_PAIRS_GLOBAL = 168  # ... else one offset from >= 7 days of overlap; below that, no correction
_MIN_PAIRS_HOUR = 30  # paired hours for a (season, UTC hour) cell of the daily-cycle correction
FALLBACKS = ("nasa_power", "open_meteo")  # the gridded sources the blend can fall back to
_SOURCE_NAME = {"nasa_power": "NASA POWER", "open_meteo": "Open-Meteo"}


def _power_complete(payload) -> bool:
    """False when any parameter's last hour is still fill: POWER has not published it yet."""
    try:
        block = payload["properties"]["parameter"]
    except (KeyError, TypeError):
        return False
    for raw in block.values():
        if raw and float(raw[max(raw)]) <= FILL_VALUE:
            return False
    return True


def _bias_offsets(station: pd.Series, power: pd.Series, *, diurnal: bool = True) -> dict:
    """Offset (station - other source, °F) over the hours both carry: monthly, then daily cycle.

    Step 1, **monthly**: a calendar month with at least ``_MIN_PAIRS_MONTH`` paired hours (pooled
    across years) gets its own mean offset; otherwise its meteorological season's
    (``_MIN_PAIRS_SEASON``); otherwise one offset over all pairs (``_MIN_PAIRS_GLOBAL``);
    otherwise none (0, ``basis="none"``). The mean, not the median: an additive correction that
    preserves the station's mean is what degree-day and M&V consumers integrate.

    Step 2, **hour of day** (``diurnal``, 0.92): what the monthly offset leaves is averaged per
    (season, UTC hour) cell with at least ``_MIN_PAIRS_HOUR`` pairs, else per UTC hour over all
    seasons, else 0. Reanalysis damps and shifts the daily cycle (warm nights, cool afternoons);
    this removes the systematic part. Each cell's mean residual is zero by construction, so the
    correction does not move the monthly means. UTC hours are fixed to the sun at a site, unlike a
    local clock across DST.
    """
    d = (station - power.reindex(station.index)).dropna()
    n = len(d)
    months = pd.Series(d.index.month, index=d.index)
    by_month = d.groupby(months).agg(["mean", "size"])
    by_season = d.groupby(months.map(_SEASON)).agg(["mean", "size"])
    overall = float(d.mean()) if n >= _MIN_PAIRS_GLOBAL else None
    offsets: dict = {}
    basis: dict = {}
    pairs: dict = {}
    for m in range(1, 13):
        cnt = int(by_month["size"].get(m, 0))
        pairs[m] = cnt
        sea = _SEASON[m]
        if cnt >= _MIN_PAIRS_MONTH:
            offsets[m], basis[m] = float(by_month["mean"][m]), "month"
        elif int(by_season["size"].get(sea, 0)) >= _MIN_PAIRS_SEASON:
            offsets[m], basis[m] = float(by_season["mean"][sea]), "season"
        elif overall is not None:
            offsets[m], basis[m] = overall, "global"
        else:
            offsets[m], basis[m] = 0.0, "none"
    applied = months.map(offsets) if n else months
    rmse_before = float((d**2).mean() ** 0.5) if n else None
    rmse_monthly = float(((d - applied) ** 2).mean() ** 0.5) if n else None
    min_pairs: dict = {
        "month": _MIN_PAIRS_MONTH,
        "season": _MIN_PAIRS_SEASON,
        "global": _MIN_PAIRS_GLOBAL,
    }
    out = {
        "method": "monthly_mean_offset",
        "units": "degF",
        "offsets_f": {m: round(v, 3) for m, v in offsets.items()},
        "basis": basis,
        "n_pairs": pairs,
        "n_pairs_total": n,
        "overlap": [str(d.index.min()), str(d.index.max())] if n else None,
        "min_pairs": min_pairs,
        "rmse_before_f": None if rmse_before is None else round(rmse_before, 3),
        "rmse_after_f": None if rmse_monthly is None else round(rmse_monthly, 3),
    }
    if not diurnal:
        return out
    hourly: dict = {s: {h: 0.0 for h in range(24)} for s in ("DJF", "MAM", "JJA", "SON")}
    hbasis: dict = {s: {} for s in hourly}
    if n and any(b != "none" for b in basis.values()):
        r = d - applied
        hrs = pd.Series(d.index.hour, index=d.index)
        cell = r.groupby([months.map(_SEASON), hrs]).agg(["mean", "size"])
        by_hour = r.groupby(hrs).agg(["mean", "size"])
        for sea in hourly:
            for h in range(24):
                key = (sea, h)
                if key in cell.index and int(cell.loc[key, "size"]) >= _MIN_PAIRS_HOUR:
                    hourly[sea][h], hbasis[sea][h] = float(cell.loc[key, "mean"]), "season_hour"
                elif h in by_hour.index and int(by_hour.loc[h, "size"]) >= _MIN_PAIRS_HOUR:
                    hourly[sea][h], hbasis[sea][h] = float(by_hour.loc[h, "mean"]), "hour"
                else:
                    hbasis[sea][h] = "none"
    else:
        for sea in hourly:
            hbasis[sea] = {h: "none" for h in range(24)}
    out["method"] = "monthly_mean_offset+hour_of_day"
    out["hour_of_day_f"] = {s: {h: round(v, 3) for h, v in hs.items()} for s, hs in hourly.items()}
    out["hour_basis"] = hbasis
    min_pairs["season_hour"] = _MIN_PAIRS_HOUR
    out["rmse_after_monthly_f"] = out["rmse_after_f"]
    if n:
        total = _correction(d.index, out)
        out["rmse_after_f"] = round(float(((d - total) ** 2).mean() ** 0.5), 3)
    return out


def _correction(index: pd.DatetimeIndex, bias: dict) -> pd.Series:
    """The additive correction (°F) a :func:`_bias_offsets` result applies at UTC ``index``."""
    off = pd.Series(index.month, index=index).map(bias["offsets_f"]).astype(float)
    hod = bias.get("hour_of_day_f")
    if hod:
        seasons = pd.Series(index.month, index=index).map(_SEASON)
        hours = index.hour
        off = off + [float(hod[s][h]) for s, h in zip(seasons, hours)]
    return off


def _bias_note(bias: dict, against: str) -> str:
    """How a fallback source was corrected, for a caveat."""
    if bias["n_pairs_total"] == 0 or all(b == "none" for b in bias["basis"].values()):
        return (
            "UNCORRECTED: too little station overlap to estimate an offset "
            f"({bias['n_pairs_total']} paired hours; {_MIN_PAIRS_GLOBAL} needed)"
        )
    offs = sorted(set(bias["offsets_f"].values()))
    how = f"a monthly mean offset ({min(offs):+.1f} to {max(offs):+.1f} °F)"
    if bias.get("hour_of_day_f"):
        how += " and an hour-of-day correction"
    return (
        f"bias-corrected against {against} by {how}; {bias['n_pairs_total']} paired hours, "
        f"hourly RMSE {bias['rmse_before_f']:.1f} -> {bias['rmse_after_f']:.1f} °F in the overlap"
    )


def _long_gaps(values: pd.Series, gap_hours: int) -> pd.Series:
    """Hours inside a run of at least ``gap_hours`` consecutive missing hours."""
    miss = values.isna()
    run = (miss != miss.shift()).cumsum()
    return miss & (miss.groupby(run).transform("size") >= gap_hours)


def _segments(label: pd.Series, tz: str) -> list[dict]:
    """Runs of one source label -> ``[{source, start, end, hours}]`` in the output clock."""
    lab = label.ffill().bfill()
    out: list[dict] = []
    if lab.empty:
        return out
    run = (lab != lab.shift()).cumsum()
    for _, grp in lab.groupby(run):
        a, b = grp.index[0], grp.index[-1]
        if tz.upper() != "UTC":
            a, b = a.tz_convert(tz).tz_localize(None), b.tz_convert(tz).tz_localize(None)
        out.append(
            {"source": str(grp.iloc[0]), "start": str(a), "end": str(b), "hours": int(len(grp))}
        )
    return out


def oat_reference_blended(
    latitude,
    longitude,
    start,
    end,
    *,
    tz: str = "UTC",
    transport: Callable[[str], bytes] | None = None,
    catalog_transport: Callable[[str], bytes] | None = None,
    stations: list[IsdStation] | None = None,
    power_transport: Callable[[str], dict] | None = None,
    max_stations: int = 3,
    max_distance_km: float = 100.0,
    gap_hours: int = 24,
    overlap_days: int = 365,
    stale_after_days: int = 60,
    cache_dir: str | None = None,
    offline: bool = False,
    clock: Callable[[], _dt.datetime] | None = None,
    timeout: float = 30.0,
    fallbacks: Sequence[str] = ("nasa_power",),
    meteo_transport: Callable[[str], dict] | None = None,
    diurnal: bool = True,
    station_offsets: bool = True,
    privacy=None,
    audit=None,
    purpose: str = "weather",
) -> pd.Series:
    """°F OAT for a window: nearest ISD station, gaps from the next ones, the rest from reanalysis.

    Provisional (0.90.1; refined 0.92). Returns the same ``oat_f`` Series contract as
    :func:`oat_reference_isd` (NaNs dropped; ``tz`` the same UTC / naive-local switch) and never
    fails just because the ISD catalog ends before the window or one station-year file is missing:

    1. **Stations.** Up to ``max_stations`` ISD stations within ``max_distance_km`` whose record
       overlaps the window, nearest first. A station still reporting when the catalog was built
       (its end within 30 days of the catalog's latest end) is tried past its catalog end date, in
       case the catalog is stale and the files are not. The nearest station that returns data is
       the **reference**; each later one fills only the runs of at least ``gap_hours`` missing
       hours its predecessors left (a missing year file, a long outage), **offset-corrected**
       against the reference (``station_offsets``, 0.92) over the hours both carry in
       ``[min(start, end − overlap_days), end]`` -- the same monthly + hour-of-day correction as
       below. Shorter ISD gaps stay missing, as in :func:`oat_reference_isd`.
    2. **Gridded fallbacks**, in the order of ``fallbacks`` (a subset of :data:`FALLBACKS`:
       ``"nasa_power"`` at the snapped POWER grid cell, :func:`power_grid_cell`, and
       ``"open_meteo"``, :func:`fetch_open_meteo`, 0.92, through ``meteo_transport``). Each fills
       the runs still missing,
       bias-corrected against the reference station over the same calibration window by a
       **monthly mean offset** (a month with >= 240 paired hours gets its own, else its
       season's, else one overall offset from >= 168 pairs, else none) and, with ``diurnal``
       (0.92, default), an **hour-of-day** correction per (season, UTC hour) -- see
       :func:`_bias_offsets`. A source's not-yet-published trailing hours stay missing; a source
       whose fetch fails is recorded and the next one is tried.
    3. **Provenance.** ``series.attrs["weather_provenance"]`` records the stations tried (distance,
       missing years, hours filled, the offset correction applied to each gap-filling station),
       the per-source date ``segments``, every fallback's grid cell, coverage end and
       ``bias_correction`` under ``fallbacks`` (POWER's also under ``power`` /
       ``bias_correction``, as before), and the ``caveats``, which are also on
       ``series.attrs["caveats"]``. A UserWarning summarises any fallback, and a catalog whose
       latest end is more than ``stale_after_days`` before today is warned about as stale.

    ``cache_dir`` wraps the transports in :func:`cached_bytes_transport` / :func:`cached_transport`
    (a gridded response with an unpublished tail is not cached; a missing ISD file is remembered);
    ``offline=True`` reads only that cache and raises :class:`WeatherCacheMiss` on a miss.
    ``clock`` (tz-aware UTC ``now``) is injectable for tests.

    **Privacy** (0.94, provisional; :mod:`camber.weather_privacy`). ``privacy`` ``"coarse"``
    keeps the station choice local (ISD requests carry a station id and a year, never
    coordinates), sends NASA POWER its grid-cell centre and Open-Meteo the point rounded to
    ``precision_deg``, and checks every URL before it leaves; ``"offline"`` sends nothing and
    reads only the cache. Both cache under :func:`~camber.weather_privacy.default_weather_dir`
    when no ``cache_dir`` is given. ``audit`` (a :class:`~camber.weather_privacy.WeatherAudit`)
    logs every request, cache hits included, under ``purpose``. With ``privacy`` given,
    ``weather_provenance["privacy"]`` records the mode and the coarsening of each source.
    """
    import warnings

    fallbacks = tuple(fallbacks)
    bad = [f for f in fallbacks if f not in FALLBACKS]
    if bad:
        raise ValueError(f"unknown fallback source(s) {bad}; use a subset of {FALLBACKS}")
    now = (clock or _default_clock)()
    today = pd.Timestamp(now.astimezone(_dt.timezone.utc).date())
    s = pd.Timestamp(_yyyymmdd(start))
    e = pd.Timestamp(_yyyymmdd(end))
    if e < s:
        raise ValueError(f"end {end!r} is before start {start!r}")

    pol = WeatherPolicy.coerce(privacy)
    if pol is not None and pol.coarse and cache_dir is None:
        cache_dir = default_weather_dir()
    if pol is not None and pol.offline:
        offline = True
    n_logged = len(audit.records) if audit is not None else 0
    raw_isd = transport or isd_transport(timeout=timeout)
    raw_cat = catalog_transport or raw_isd
    raw_pow = power_transport or nasa_power_transport(timeout=timeout)
    raw_om = meteo_transport or open_meteo_transport(timeout=timeout)
    cached = cache_dir is not None

    def _wire(service, net, cache):
        return guarded_transport(
            service, net, policy=pol, audit=audit, purpose=purpose, cache=cache if cached else None
        )

    def _sub(name):
        return os.path.join(str(cache_dir), name)

    t_isd = _wire("isd", raw_isd, lambda t: cached_bytes_transport(t, _sub("isd"), offline=offline))
    t_cat = _wire(
        "isd",
        raw_cat,
        lambda t: cached_bytes_transport(
            t, _sub("isd"), ttl=_dt.timedelta(days=7), offline=offline
        ),
    )
    t_pow = _wire(
        "nasa_power",
        raw_pow,
        lambda t: cached_transport(t, _sub("power"), offline=offline, should_cache=_power_complete),
    )
    t_om = _wire(
        "open_meteo",
        raw_om,
        lambda t: cached_transport(
            t, _sub("open_meteo"), offline=offline, should_cache=_open_meteo_complete
        ),
    )

    cat = stations if stations is not None else isd_stations(transport=t_cat, timeout=timeout)
    last = isd_catalog_end(cat)
    caveats: list[str] = []
    stale = last is not None and pd.Timestamp(last) < today - pd.Timedelta(days=stale_after_days)
    if stale or (last is not None and pd.Timestamp(last) < e):
        _warn_stale(str(last), e.strftime("%Y%m%d") if not stale else today.strftime("%Y%m%d"))
        caveats.append(f"The ISD station catalog looks stale (latest station end {last}).")

    current = pd.Timestamp(last) - pd.Timedelta(days=30) if last else None

    def _eff_end(st: IsdStation) -> pd.Timestamp:
        end_ts = pd.Timestamp(st.end)
        return e if current is not None and end_ts >= current else end_ts

    cands = [
        st
        for st in cat
        if st.begin
        and st.end
        and pd.Timestamp(st.begin) <= e
        and _eff_end(st) >= s
        and _haversine_km(latitude, longitude, st.latitude, st.longitude) <= max_distance_km
    ]
    cands.sort(key=lambda st: _haversine_km(latitude, longitude, st.latitude, st.longitude))
    cands = cands[:max_stations]

    ext = min(s, e - pd.Timedelta(days=overlap_days))  # the calibration window starts here
    grid = pd.date_range(s, e + pd.Timedelta(hours=23), freq="h", tz="UTC")
    values = pd.Series(float("nan"), index=grid)
    label = pd.Series(None, index=grid, dtype=object)
    tried: list[dict] = []
    ref: IsdStation | None = None
    ref_series: pd.Series | None = None
    calib_cache: dict = {}

    def _calib() -> pd.Series | None:
        """The reference station over the calibration window ``[ext, e]`` (fetched once)."""
        if ref is None or ref_series is None:
            return None
        if "s" not in calib_cache:
            calib = ref_series
            if ext < s:  # the reference station before the window, for the overlap
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    try:
                        pre = fetch_isd(
                            ref.usaf,
                            ref.wban,
                            ext,
                            s - pd.Timedelta(days=1),
                            transport=t_isd,
                            tz="UTC",
                        )["oat_f"]
                        calib = pd.concat([pre, ref_series]).sort_index()
                        calib = calib[~calib.index.duplicated(keep="first")]
                    except ValueError:
                        pass
            calib_cache["s"] = calib
        return calib_cache["s"]

    for st in cands:
        need = _long_gaps(values, gap_hours)
        if not need.any():
            break
        hours = need[need].index
        rec: dict = {
            **st.as_dict(),
            "distance_km": round(_haversine_km(latitude, longitude, st.latitude, st.longitude), 1),
        }
        secondary = ref is not None and station_offsets
        lo = hours[0]
        if secondary:  # fetch the overlap with the reference too, to estimate the offset
            lo = min(hours[0], pd.Timestamp(ext).tz_localize("UTC"))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            try:
                df = fetch_isd(st.usaf, st.wban, lo, hours[-1], transport=t_isd, tz="UTC")
            except ValueError as exc:
                rec.update(hours_filled=0, missing_years=None, error=str(exc))
                tried.append(rec)
                continue
        oat = df["oat_f"]
        if secondary:
            calib = _calib()
            off = _bias_offsets(calib, oat, diurnal=diurnal) if calib is not None else None
            if (
                off is not None
                and off["n_pairs_total"]
                and any(b != "none" for b in off["basis"].values())
            ):
                off["reference_station"] = f"{ref.usaf}-{ref.wban}"  # type: ignore[union-attr]
                oat = oat + _correction(oat.index, off)
                rec["offset_correction"] = off
            else:
                rec["offset_correction"] = None
        fill = need & oat.reindex(grid).notna()
        values[fill] = oat.reindex(grid)[fill]
        label[fill] = f"isd:{st.usaf}-{st.wban}"
        rec.update(hours_filled=int(fill.sum()), missing_years=df.attrs.get("isd_missing_years"))
        tried.append(rec)
        if ref is None:
            ref, ref_series = st, oat

    for rec in tried:
        if rec.get("missing_years"):
            caveats.append(
                f"ISD station {rec['usaf']}-{rec['wban']} has no data file for "
                f"{', '.join(map(str, rec['missing_years']))}; those dates are filled from the "
                "next source."
            )
    for rec in tried:
        if rec.get("error"):
            caveats.append(
                f"ISD station {rec['usaf']}-{rec['wban']} ({rec['distance_km']} km) returned no "
                "data for the dates it was asked for."
            )
    used = [r for r in tried if r.get("hours_filled")]
    if len(used) > 1:
        fixed = [r for r in used[1:] if r.get("offset_correction")]
        raw = [r for r in used[1:] if not r.get("offset_correction")]
        if fixed:
            names = ", ".join(f"{r['usaf']}-{r['wban']} ({r['distance_km']} km)" for r in fixed)
            ref_name = f"{used[0]['usaf']}-{used[0]['wban']}"
            caveats.append(
                f"Gaps of the nearest station are filled from station(s) {names}, "
                + "; ".join(
                    _bias_note(r["offset_correction"], f"station {ref_name}") for r in fixed
                )
                + "."
            )
        if raw:
            names = ", ".join(f"{r['usaf']}-{r['wban']} ({r['distance_km']} km)" for r in raw)
            why = "" if station_offsets else " (station_offsets=False)"
            caveats.append(
                f"Gaps of the nearest station are filled from station(s) {names}, uncorrected for "
                f"any station-to-station offset{why}."
            )

    fb_info: list[dict] = []
    power_info: dict | None = None
    bias: dict | None = None
    for src in fallbacks:
        need = _long_gaps(values, gap_hours)
        if not need.any():
            break
        first = _POWER_HOURLY_START if src == "nasa_power" else _OPEN_METEO_START
        p_start = max(min(ext, need[need].index[0].tz_localize(None).normalize()), first)
        p_end = min(e, today)
        info: dict = {"source": src, "requested": [str(p_start.date()), str(p_end.date())]}
        series = None
        if p_end >= p_start:
            try:
                if src == "nasa_power":
                    pw = fetch_nasa_power(
                        latitude,
                        longitude,
                        p_start,
                        p_end,
                        transport=t_pow,
                        tz="UTC",
                        snap_to_cell=True,
                        privacy=pol,
                    )
                    info.update(
                        cell=pw.attrs.get("power_cell"),
                        coverage_end=pw.attrs.get("power_coverage_end"),
                    )
                else:
                    pw = fetch_open_meteo(
                        latitude, longitude, p_start, p_end, transport=t_om, privacy=pol
                    )
                    info.update(
                        cell=pw.attrs.get("open_meteo_cell"),
                        coverage_end=pw.attrs.get("open_meteo_coverage_end"),
                    )
                series = pw["oat_f"]
            except _NoData:
                series = None
            except (OSError, ValueError) as exc:  # a failed source: record it, try the next
                if src == fallbacks[-1] and len(fallbacks) == 1:
                    raise
                info["error"] = f"{type(exc).__name__}: {exc}"
                caveats.append(f"{_SOURCE_NAME[src]} could not be fetched ({info['error']}).")
        b: dict | None = None
        if series is not None:
            calib = _calib()
            if calib is not None and ref is not None:
                b = _bias_offsets(calib, series, diurnal=diurnal)
                b["reference_station"] = f"{ref.usaf}-{ref.wban}"
            else:
                b = _bias_offsets(
                    pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC")),
                    series,
                    diurnal=diurnal,
                )
                b["reference_station"] = None
            corrected = (series + _correction(series.index, b)).reindex(grid)
            fill = need & corrected.notna()
            values[fill] = corrected[fill]
            label[fill] = src
            info["hours_filled"] = int(fill.sum())
        info["bias_correction"] = b
        fb_info.append(info)
        if src == "nasa_power":
            power_info = {k: info[k] for k in ("cell", "coverage_end", "requested") if k in info}
            bias = b

    gone = _long_gaps(values, gap_hours)
    label[gone] = "missing"
    n_missing = int(gone.sum())
    segs = _segments(label, tz)
    for info in fb_info:
        src = info["source"]
        n_src = int((label == src).sum())
        if not n_src:
            continue
        dates = "; ".join(f"{g['start'][:10]}..{g['end'][:10]}" for g in segs if g["source"] == src)
        bc = info["bias_correction"]
        assert bc is not None
        how = _bias_note(bc, f"station {bc.get('reference_station')}")
        cell = info.get("cell") or {}
        size = "~55 km" if src == "nasa_power" else "~9-25 km"
        caveats.append(
            f"{n_src} h ({100 * n_src / len(grid):.0f}% of the window) come from "
            f"{_SOURCE_NAME[src]} reanalysis (grid cell {cell.get('latitude')}, "
            f"{cell.get('longitude')}, {size}), not a station: {dates}; {how}."
        )
    if n_missing:
        ends = [
            f"{_SOURCE_NAME[i['source']]} is published through {i['coverage_end']}"
            for i in fb_info
            if i.get("coverage_end")
        ]
        names = " and ".join(["ISD", *(_SOURCE_NAME[f] for f in fallbacks)])
        caveats.append(
            f"{n_missing} h of the window have no source ({names} "
            + ("both " if len(fallbacks) == 1 else "all ")
            + "missing"
            + (f"; {'; '.join(ends)}" if ends else "")
            + ")."
        )
    if caveats:
        warnings.warn(" ".join(caveats), UserWarning, stacklevel=2)

    out = values.dropna()
    if tz.upper() != "UTC":
        out.index = out.index.tz_convert(tz).tz_localize(None)
    out.name = "oat_f"
    out.attrs["weather_provenance"] = {
        "method": (
            "isd_with_nasa_power_fallback"
            if fallbacks == ("nasa_power",)
            else "isd_with_fallbacks:" + ",".join(fallbacks)
            if fallbacks
            else "isd_only"
        ),
        "window": [str(s.date()), str(e.date())],
        "tz": tz,
        "catalog_end": last,
        "catalog_stale": bool(stale),
        "stations": tried,
        "segments": segs,
        "hours_by_source": {k: int(v) for k, v in label.value_counts().items()},
        "power": power_info,
        "bias_correction": bias,
        "fallbacks": fb_info,
        "caveats": caveats,
    }
    if pol is not None:
        srcs = ["isd", *(str(i["source"]) for i in fb_info)]
        out.attrs["weather_provenance"]["privacy"] = _privacy_record(
            pol, srcs, audit, n_logged, cache_dir
        )
    out.attrs["caveats"] = list(caveats)
    if ref is not None:
        out.attrs["isd_station"] = ref.as_dict()
    return out


def _privacy_record(pol, services, audit, n_logged: int, cache_dir) -> dict:
    """What the requests of one fetch carried, for provenance (0.94)."""
    rec: dict = {
        **pol.as_dict(),
        "coarsening": {s: coarsening_note(s, pol) for s in dict.fromkeys(services)},
        "cache": "on" if cache_dir is not None else "off",
    }
    if audit is not None:
        mine = audit.records[n_logged:]
        rec["requests_sent"] = sum(1 for r in mine if r.get("sent"))
        rec["cache_hits"] = sum(1 for r in mine if r.get("cache") == "hit")
        rec["audit_log"] = os.path.basename(audit.path)
    return rec


def oat_reference_auto(
    latitude,
    longitude,
    start,
    end,
    *,
    source: str = "auto",
    tz: str = "UTC",
    cache_dir: str | None = None,
    offline: bool = False,
    privacy=None,
    audit=None,
    purpose: str = "weather",
    place=None,
    **kwargs,
) -> pd.Series:
    """°F OAT reference from a named source -- what a config's ``fetch`` key selects. Provisional.

    ``source``: ``"auto"`` -- ISD with the NASA POWER then Open-Meteo fallbacks
    (:func:`oat_reference_blended`, ``fallbacks=("nasa_power", "open_meteo")``); ``"isd"`` -- ISD
    stations only, gap-filled and offset-corrected between stations (``fallbacks=()``);
    ``"nasa_power"`` -- NASA POWER alone (:func:`oat_reference`, uncorrected); ``"open_meteo"``
    -- Open-Meteo alone (:func:`oat_reference_open_meteo`, uncorrected). ``cache_dir`` /
    ``offline`` cache the requests as in :func:`oat_reference_blended`; ``kwargs`` go to the
    underlying function (transports, ``clock`` ...). The series' ``attrs["weather_source"]``
    names the source used.

    ``privacy`` / ``audit`` / ``purpose`` (0.94, provisional) apply the guardrails of
    :mod:`camber.weather_privacy` to every source (see :func:`oat_reference_blended`); under
    ``"offline"`` a cache miss raises :class:`WeatherCacheMiss` saying how to supply a file.
    ``attrs["weather_privacy"]`` then records the mode and the coarsening applied. ``place``
    (0.94) names the location instead of ``latitude`` / ``longitude`` (pass ``None`` for both):
    an airport ICAO code or ISD station id (from the ISD catalogue) or a bundled city, resolved
    locally by :func:`resolve_place` and recorded on ``attrs["weather_place"]``.
    """
    if source not in ("auto", "isd", "nasa_power", "open_meteo"):
        raise ValueError(
            f"unknown weather source {source!r}; use auto, isd, nasa_power or open_meteo"
        )
    pol = WeatherPolicy.coerce(privacy)
    if pol is not None and pol.coarse and cache_dir is None:
        cache_dir = default_weather_dir()
    if pol is not None and pol.offline:
        offline = True
    guard = {} if pol is None and audit is None else {"privacy": pol, "audit": audit}
    n_logged = len(audit.records) if audit is not None else 0
    place_info = None
    try:
        if place is not None:
            stations = kwargs.get("stations")
            if _place_code(place) and stations is None:
                cat_net = kwargs.pop("catalog_transport", None)
                if cat_net is None:
                    cat_net = kwargs.get("transport") if source in ("auto", "isd") else None
                cat_cache = None
                if cache_dir is not None:
                    cdir = os.path.join(cache_dir, "isd")

                    def cat_cache(inner):
                        return cached_bytes_transport(
                            inner, cdir, ttl=_dt.timedelta(days=7), offline=offline
                        )

                t_cat = guarded_transport(
                    "isd",
                    cat_net or isd_transport(),
                    policy=pol,
                    audit=audit,
                    purpose=purpose,
                    cache=cat_cache,
                )
                stations = isd_stations(transport=t_cat)
                if source in ("auto", "isd"):
                    kwargs["stations"] = stations
            place_info = resolve_place(place, stations=stations)
            latitude, longitude = place_info["latitude"], place_info["longitude"]
        if latitude is None or longitude is None:
            raise ValueError("give latitude and longitude, or a place")
        if source in ("auto", "isd"):
            fb = ("nasa_power", "open_meteo") if source == "auto" else ()
            s = oat_reference_blended(
                latitude,
                longitude,
                start,
                end,
                tz=tz,
                cache_dir=cache_dir,
                offline=offline,
                fallbacks=kwargs.pop("fallbacks", fb),
                **({**guard, "purpose": purpose} if guard else {}),
                **kwargs,
            )
        else:
            t = kwargs.pop("transport", None)
            if t is None:
                t = nasa_power_transport() if source == "nasa_power" else open_meteo_transport()
            complete = _power_complete if source == "nasa_power" else _open_meteo_complete
            sub = "power" if source == "nasa_power" else "open_meteo"
            cache = None
            if cache_dir is not None:
                cdir = os.path.join(cache_dir, sub)

                def cache(inner):
                    return cached_transport(inner, cdir, offline=offline, should_cache=complete)

            t = guarded_transport(source, t, policy=pol, audit=audit, purpose=purpose, cache=cache)
            fn = oat_reference if source == "nasa_power" else oat_reference_open_meteo
            extra: dict = {} if pol is None else {"privacy": pol}
            s = fn(latitude, longitude, start, end, transport=t, tz=tz, **extra, **kwargs)
    except WeatherCacheMiss as exc:
        if pol is not None and pol.offline:
            raise WeatherCacheMiss(f"{exc}. {OFFLINE_HELP}") from None
        raise
    s.attrs["weather_source"] = source
    if place_info is not None:
        s.attrs["weather_place"] = place_info
    if pol is not None:
        used = [source] if source in FALLBACKS else ["isd"]
        prov = s.attrs.get("weather_provenance") or {}
        s.attrs["weather_privacy"] = prov.get("privacy") or _privacy_record(
            pol, used, audit, n_logged, cache_dir
        )
    return s
