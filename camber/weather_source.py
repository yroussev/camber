"""Live weather fetch from NASA POWER — an external reference series for M&V + sensor validation.

CAMBER can weather-normalize M&V and validate a temperature sensor against an external reference,
but until now the reference had to be a *local* EPW/TMY file (`camber.mandv.weather.load_epw`) or a
series the caller brought themselves (`camber.sensordrift.compare_to_reference`). This module
*fetches* one:
hourly historical air temperature (and optionally relative humidity) from **NASA POWER**
(https://power.larc.nasa.gov) — a free, keyless, global reanalysis service — and returns it in the
exact °F Series shape those consumers already expect (`name="oat_f"`, matching `load_epw`).

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
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import pandas as pd

from .mandv.weather import c_to_f

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
]

_BASE_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
FILL_VALUE = -999.0  # NASA POWER hourly missing sentinel (values <= this are dropped to NaN)
_PARAM_COL = {"T2M": "oat_f", "RH2M": "rh_pct"}  # NASA parameter -> output column

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
) -> str:
    """Build the NASA POWER hourly point-query URL (pure — the testable half, no I/O).

    ``parameters`` are NASA POWER codes (``T2M`` = 2 m air temp °C, ``RH2M`` = 2 m rel. humidity %);
    ``start``/``end`` accept ``YYYYMMDD``/``YYYY-MM-DD`` strings or date/datetime objects.
    """
    from urllib.parse import urlencode

    query = urlencode(
        {
            "parameters": ",".join(parameters),
            "community": community,
            "latitude": latitude,
            "longitude": longitude,
            "start": _yyyymmdd(start),
            "end": _yyyymmdd(end),
            "format": "JSON",
        }
    )
    return f"{_BASE_URL}?{query}"


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


def cached_transport(
    inner: Callable[[str], dict],
    cache_dir: str,
    *,
    ttl: _dt.timedelta | None = None,
    clock: Callable[[], _dt.datetime] | None = None,
) -> Callable[[str], dict]:
    """Wrap a transport with a dependency-light on-disk cache keyed by URL (composes with any).

    Each URL's parsed JSON is memoized to ``<cache_dir>/<sha256(url)>.json`` (an atomic write via
    ``os.replace``); a hit returns the stored copy, a miss delegates to ``inner`` and writes it.
    NASA POWER historical reanalysis is stable, so the default is **cache-forever** (``ttl=None``);
    the most recent ~months can be revised, so pass a ``ttl`` for windows touching recent data. A
    corrupt/torn cache file is treated as a miss (self-healing). ``clock`` (default UTC ``now``) is
    injectable so TTL expiry is deterministic in tests; it should return a tz-aware UTC datetime.
    stdlib ``json``/``hashlib``/``os`` only.
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
                    ttl is None or (tick() - _dt.datetime.fromisoformat(env["fetched_utc"])) < ttl
                )
                if fresh:
                    return env["payload"]
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                pass  # corrupt / torn write / bad timestamp -> treat as a miss and re-fetch
        payload = inner(url)
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


def _fetch_one(latitude, longitude, start, end, *, parameters, transport, tz) -> pd.DataFrame:
    """Fetch a single ≤1-year window; raise :class:`_NoData` on a valid-but-empty block."""
    payload = transport(nasa_power_url(latitude, longitude, start, end, parameters=parameters))
    try:
        param_block = payload["properties"]["parameter"]
    except (KeyError, TypeError) as e:
        raise ValueError("NASA POWER response missing properties.parameter") from e

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
) -> pd.DataFrame:
    """Fetch hourly NASA POWER weather as a DataFrame (``oat_f`` in °F; ``rh_pct`` when requested).

    Multi-year windows work transparently: the request is split into calendar-year chunks (NASA
    POWER caps a single hourly request at ~1 year), one transport call per year, concatenated into a
    single unique, sorted hourly index. ``transport`` (default the stdlib one) is
    ``callable(url) -> parsed-JSON dict`` — inject a canned one to run offline, or wrap it with
    :func:`cached_transport`. ``tz`` is ``"UTC"`` (tz-aware) or a site IANA zone (naive local; see
    the module docstring). Missing hours (``<= FILL_VALUE``) become NaN; a request returning no data
    for a parameter across *every* chunk raises ``ValueError``. numpy/pandas + stdlib.
    """
    transport = transport or nasa_power_transport(timeout=timeout)
    frames = []
    for cs, ce in _year_chunks(start, end):
        try:
            frames.append(
                _fetch_one(
                    latitude, longitude, cs, ce, parameters=parameters, transport=transport, tz=tz
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
    return frame[~frame.index.duplicated(keep="first")].sort_index()


def oat_reference(
    latitude,
    longitude,
    start,
    end,
    *,
    transport: Callable[[str], dict] | None = None,
    tz: str = "UTC",
    timeout: float = 30.0,
) -> pd.Series:
    """Fetch just the outdoor-air-temperature reference series (°F, NaNs dropped, ``name="oat_f"``).

    The exact shape `sensordrift.compare_to_reference` and `mandv.weather.monthly_normals` consume
    — so a fetched series drops in wherever a `load_epw` series would. Pass the site IANA ``tz`` to
    get a naive-local index that inner-joins to a BAS sensor trend (see the module docstring).
    """
    df = fetch_nasa_power(
        latitude, longitude, start, end, transport=transport, tz=tz, timeout=timeout
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


def nominatim_url(address, *, limit: int = 1) -> str:
    """Build the Nominatim search URL (pure — the testable half; URL-encodes the address)."""
    from urllib.parse import urlencode

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
) -> GeoResult:
    """Geocode an address / place name to coordinates via OpenStreetMap Nominatim (free, keyless).

    Returns the top match as a :class:`GeoResult` — ``.display_name`` (e.g. "Chicago, Cook County,
    Illinois, United States") lets you confirm it before fetching weather. Inject a ``transport``
    (canned JSON) to run offline, or wrap :func:`nominatim_transport` with :func:`cached_transport`.
    Raises ``ValueError`` on no match or a malformed row. stdlib ``urllib``/``json`` only.
    """
    transport = transport or nominatim_transport(user_agent=user_agent, timeout=timeout)
    rows = transport(nominatim_url(address, limit=limit))
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

    def as_dict(self) -> dict:
        """Return the station as a plain dict."""
        return asdict(self)


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
) -> Callable[[str], bytes]:
    """On-disk cache for a **bytes** transport (the ISD analog of :func:`cached_transport`).

    Memoizes each URL's raw bytes to ``<cache_dir>/<sha256(url)>.bin`` (an ISO-timestamp header line
    then the payload), with an atomic ``os.replace``. Caching matters here: the station catalog is
    ~5 MB and per-year files repeat. Default cache-forever (``ttl=None``); ``clock`` (tz-aware UTC)
    is injectable so TTL expiry is deterministic in tests; a corrupt file self-heals.
    """
    tick = clock or _default_clock

    def transport(url: str) -> bytes:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".bin")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    header, _, payload = f.read().partition(b"\n")
                stamp = _dt.datetime.fromisoformat(header.decode("ascii"))
                if ttl is None or (tick() - stamp) < ttl:
                    return payload
            except (OSError, ValueError):
                pass  # corrupt / torn / bad timestamp -> treat as a miss and re-fetch
        payload = inner(url)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(tick().isoformat().encode("ascii") + b"\n" + payload)
        os.replace(tmp, path)  # atomic publish (mirror cached_transport / store.facilities._write)
        return payload

    return transport


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
        raise ValueError(f"no ISD station covers ({latitude}, {longitude}) {start}..{end}")
    return min(
        covering, key=lambda st: _haversine_km(latitude, longitude, st.latitude, st.longitude)
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
) -> pd.DataFrame:
    """Fetch hourly NOAA ISD-Lite data for a station (``oat_f`` °F; ``dewpt_f`` when requested).

    One gzipped file per calendar year in ``[start, end]``, concatenated into a single unique,
    sorted index and trimmed to the window. ``-9999`` becomes NaN; air temp (tenths of °C) becomes
    °F. ``tz`` is the SAME switch as the NASA path: ``"UTC"`` (tz-aware) or a site IANA zone (naive
    local). Raises ``ValueError`` if no year returned data. stdlib ``gzip``/``urllib`` + pandas.
    """
    transport = transport or isd_transport(timeout=timeout)
    s, e = pd.Timestamp(_yyyymmdd(start)), pd.Timestamp(_yyyymmdd(end)) + pd.Timedelta(hours=23)
    frames = []
    for year in range(s.year, e.year + 1):
        raw = transport(f"{_ISD_DATA_BASE}/{year}/{usaf}-{wban}-{year}.gz")
        df = _parse_isd_lite(
            raw, "UTC", dew_point=dew_point
        )  # parse+filter in UTC, tz-switch after
        if not df.empty:
            frames.append(
                df[(df.index >= s.tz_localize("UTC")) & (df.index <= e.tz_localize("UTC"))]
            )
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise ValueError(f"no ISD data for {usaf}-{wban} {start}..{end}")
    frame = pd.concat(frames)
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    if tz.upper() != "UTC":  # apply the naive-local switch after the UTC-based windowing
        frame.index = frame.index.tz_convert(tz).tz_localize(None)
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
) -> pd.Series:
    """Find the nearest covering ISD station to a lat/lon, then fetch its °F OAT reference series.

    A station-precise counterpart to :func:`oat_reference`: returns the same °F ``oat_f`` Series
    (NaNs dropped) with the resolved station on ``series.attrs["isd_station"]``. Two transport seams
    (``catalog_transport`` for the station list, ``transport`` for the hourly data), both
    offline-injectable. ``tz`` is explicit (the same load-bearing switch as :func:`oat_reference`).
    """
    station = isd_nearest_station(
        latitude, longitude, start, end, transport=catalog_transport, timeout=timeout
    )
    df = fetch_isd(
        station.usaf, station.wban, start, end, transport=transport, tz=tz, timeout=timeout
    )
    series = df["oat_f"].dropna()
    series.attrs["isd_station"] = station.as_dict()
    return series
