"""Privacy guardrails for requests to public weather and price services (provisional, 0.94, #73).

CAMBER fetches outdoor temperature from NOAA ISD, NASA POWER and Open-Meteo, state retail
prices from EIA, and tariffs from the OpenEI URDB. For a non-public site the request itself can
disclose the site: a full-precision latitude and longitude locates one building. This module is
the one place that decides what may leave the machine.

**Policy.** A :class:`WeatherPolicy` has a ``privacy`` mode:

* ``"public"`` -- the behaviour before 0.94, unchanged (coordinates as given);
* ``"coarse"`` -- requests go out, but every location is coarsened first by :func:`coarsen`:
  ISD requests carry **no coordinates** (the station is chosen locally from the downloaded
  catalogue), NASA POWER receives its **grid-cell centre** (0.5° lat × 0.625° lon, the
  resolution POWER resolves anyway), and Open-Meteo receives the point **rounded to
  ``precision_deg``** (default 0.1°);
* ``"offline"`` -- **no network call at all**: caches and user-supplied files only.

A facility marked private (``private: true`` in its portfolio registry entry or its config)
defaults to ``offline`` until the user opts in to ``coarse``; it can never be ``public``.

**By construction.** :func:`coarsen` is the only function that turns a location into request
coordinates, and the URL builders call it. Independently, :func:`guarded_transport` checks
every URL at send time (:func:`check_url`) and raises :class:`PrivacyViolation` if one is more
precise than the policy allows, names a service it should not, or (``offline``) would reach the
network at all. So a bug in a URL builder fails loudly instead of leaking.

**Audit.** :class:`WeatherAudit` appends one JSON line per request (service, the URL as sent
with any API key redacted, purpose, cache hit or miss, whether it was sent) to
``state/<facility_id>/weather_audit.ndjson`` inside a portfolio workspace, else to
``weather_audit.ndjson`` next to the cache. The facility id is written to the local record only;
it never appears in a URL. ``camber weather audit`` prints the log.

Stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

__all__ = [
    "PRIVACY_MODES",
    "DEFAULT_PRECISION_DEG",
    "MIN_PRECISION_DEG",
    "POWER_GRID",
    "AUDIT_FILE",
    "PrivacyViolation",
    "OfflineViolation",
    "WeatherPolicy",
    "resolve_policy",
    "WeatherContext",
    "weather_context",
    "grid_snap",
    "coarsen",
    "coarsening_note",
    "check_url",
    "redact_url",
    "WeatherAudit",
    "read_weather_audit",
    "weather_audit_path",
    "default_weather_dir",
    "guarded_transport",
    "CITY_TABLE",
    "resolve_city",
    "OFFLINE_HELP",
]

#: The privacy modes, most open first.
PRIVACY_MODES = ("public", "coarse", "offline")
#: Open-Meteo rounding in ``coarse`` mode: 0.1° is ~11 km of latitude (city scale).
DEFAULT_PRECISION_DEG = 0.1
#: The finest rounding ``coarse`` accepts: 0.05° is ~5.5 km of latitude.
MIN_PRECISION_DEG = 0.05
#: NASA POWER's meteorology grid (MERRA-2): 0.5° latitude × 0.625° longitude; centres sit on
#: multiples of the step. power.larc.nasa.gov/docs/methodology/data/sources/ ("½° latitude by ⅝°
#: longitude grid from GMAO MERRA-2"), checked 2026-09-28.
POWER_GRID = (0.5, 0.625)
#: The audit log's file name (in ``state/<facility_id>/`` or next to the cache).
AUDIT_FILE = "weather_audit.ndjson"

OFFLINE_HELP = (
    "privacy 'offline' makes no network call: supply a weather file instead (an mv billing "
    'entry\'s "oat": {"file": "oat.csv"}, the config\'s "shared_oat": {"file": ...}, or the RCx '
    'report\'s "oat_reference": {"csv": ...}), or fill the cache once with "privacy": "coarse" '
    "(only coarsened cells and ISD station ids are sent)"
)

# the hosts each service may reach, and the query keys its requests may carry
_HOSTS = {
    "nasa_power": ("power.larc.nasa.gov",),
    "open_meteo": ("archive-api.open-meteo.com",),
    "isd": ("www.ncei.noaa.gov",),
    "nominatim": ("nominatim.openstreetmap.org",),
    "eia": ("api.eia.gov",),
    "urdb": ("api.openei.org",),
}
_KEYS = {
    "nasa_power": {
        "parameters",
        "community",
        "latitude",
        "longitude",
        "start",
        "end",
        "format",
        "time-standard",
    },
    "open_meteo": {
        "latitude",
        "longitude",
        "start_date",
        "end_date",
        "hourly",
        "temperature_unit",
        "timezone",
        "models",
    },
    "isd": set(),
    "eia": {
        "frequency",
        "data[0]",
        "facets[stateid][]",
        "facets[sectorid][]",
        "facets[duoarea][]",
        "facets[process][]",
        "start",
        "end",
        "length",
    },
    "urdb": {"version", "format", "detail", "getpage", "api_key"},
}
_ISD_PATH = re.compile(
    r"^/pub/data/noaa/(isd-history\.csv|isd-lite/\d{4}/[0-9A-Z]{6}-\d{5}-\d{4}\.gz)$"
)
# a query value that could carry free text (a name, an address, an email) never matches these
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9 ._,:+\-]{0,64}$")


class PrivacyViolation(AssertionError):
    """A request would send more than the privacy policy allows (it was not sent)."""


class OfflineViolation(PrivacyViolation):
    """Privacy ``offline``: something tried to reach the network (nothing was sent)."""


# --------------------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class WeatherPolicy:
    """What CAMBER may send: ``privacy`` (:data:`PRIVACY_MODES`), the Open-Meteo rounding
    ``precision_deg``, and where the policy came from (``origin``, for provenance)."""

    privacy: str = "public"
    precision_deg: float = DEFAULT_PRECISION_DEG
    origin: str = "default"

    def __post_init__(self):
        if self.privacy not in PRIVACY_MODES:
            raise ValueError(
                f"weather.privacy must be one of {', '.join(PRIVACY_MODES)}, not {self.privacy!r}"
            )
        p = self.precision_deg
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p):
            raise ValueError(f"weather.precision_deg must be a number, not {p!r}")
        if not MIN_PRECISION_DEG <= float(p) <= 5.0:
            raise ValueError(
                f"weather.precision_deg must be between {MIN_PRECISION_DEG} and 5 degrees "
                f"(finer than ~5 km approaches a street address), not {p!r}"
            )

    @property
    def coarse(self) -> bool:
        """True in ``coarse`` and ``offline`` (the cache is keyed by the coarse cell in both)."""
        return self.privacy != "public"

    @property
    def offline(self) -> bool:
        """True when no network call is allowed."""
        return self.privacy == "offline"

    @classmethod
    def coerce(cls, value) -> WeatherPolicy | None:
        """``None``, a mode string, a ``{"privacy", "precision_deg"}`` dict or a policy."""
        if value is None or isinstance(value, WeatherPolicy):
            return value
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict):
            extra = set(value) - {"privacy", "precision_deg", "origin"}
            if extra:
                raise ValueError(f"weather: unknown key(s) {sorted(extra)}")
            return cls(
                str(value.get("privacy", "public")),
                value.get("precision_deg", DEFAULT_PRECISION_DEG),
                str(value.get("origin", "config")),
            )
        raise ValueError(f'weather must be {{"privacy": ..., "precision_deg": ...}}, not {value!r}')

    def as_dict(self) -> dict:
        """The policy as a plain dict."""
        return asdict(self)


def resolve_policy(*, private: bool = False, config=None, spec=None) -> WeatherPolicy:
    """The policy for one fetch: the fetch ``spec``'s ``weather``, else the config's, else
    ``offline`` for a private facility, else ``public``. A private facility may opt in to
    ``coarse`` but never to ``public`` (``ValueError``)."""
    chosen, origin = None, "default"
    for value, where in ((spec, "fetch"), (config, "config")):
        if value is not None:
            chosen, origin = value, where
            break
    if chosen is None:
        return WeatherPolicy(
            "offline" if private else "public", origin="private" if private else "default"
        )
    pol = WeatherPolicy.coerce(chosen)
    assert pol is not None
    pol = WeatherPolicy(pol.privacy, pol.precision_deg, origin)
    if private and pol.privacy == "public":
        raise ValueError(
            'this facility is marked private: weather privacy "public" would send its exact '
            'location; use "coarse" (coarsened cells and station ids only) or "offline"'
        )
    return pol


@dataclass(frozen=True)
class WeatherContext:
    """A config's weather guardrails: whether its facility is private, the config-level
    ``weather`` block, and where requests are audited (provisional, 0.94)."""

    private: bool = False
    config_weather: object = None
    facility_id: str | None = None
    workspace: str | None = None

    def policy(self, spec_weather=None) -> WeatherPolicy:
        """The policy for one fetch (its own ``weather`` block overrides the config's)."""
        return resolve_policy(private=self.private, config=self.config_weather, spec=spec_weather)

    def audit(self, policy: WeatherPolicy, cache_dir=None) -> WeatherAudit | None:
        """The audit log for requests under ``policy`` (``None`` when there is nowhere to
        write it: a public fetch outside a workspace with no cache)."""
        if cache_dir is None and policy.coarse:
            cache_dir = default_weather_dir()
        path = weather_audit_path(
            workspace=self.workspace, facility_id=self.facility_id, cache_dir=cache_dir
        )
        if path is None:
            return None
        return WeatherAudit(path, facility_id=self.facility_id, privacy=policy.privacy)


def weather_context(config: dict, base_dir: str = ".", *, ctx=None) -> WeatherContext:
    """The :class:`WeatherContext` of a config: private when the config says ``"private": true``
    or its facility's portfolio registry entry does (either one is enough); the config's
    top-level ``"weather"`` block; the facility id and workspace for the audit log."""
    private = config.get("private", False)
    if not isinstance(private, bool):
        raise ValueError('"private" must be true or false')
    weather = config.get("weather")
    if weather is not None:
        WeatherPolicy.coerce(weather)  # validate up front
    fid = ws = None
    if ctx is None:
        try:
            from .config import _facility_context

            ctx = _facility_context(config, base_dir)
        except (ValueError, KeyError, OSError):
            ctx = None
    if ctx is not None:
        fid, ws = ctx.facility_id, ctx.workspace
    if ws and fid and not private:
        from .portfolio import Portfolio

        try:
            private = bool(Portfolio(ws).facility(fid).get("private", False))
        except KeyError:
            pass
    return WeatherContext(bool(private), weather, fid, ws)


# ------------------------------------------------------------------------------------ coarsening


def _decimals(step: float) -> int:
    return max(0, -int(Decimal(str(step)).normalize().as_tuple().exponent))


def grid_snap(latitude, longitude, dlat: float, dlon: float) -> tuple[float, float]:
    """The centre of the regular ``dlat`` × ``dlon`` grid cell holding the point (the nearest
    multiples of the steps, rounded to 6 decimals so the value is a stable cache key)."""
    return (
        round(round(float(latitude) / dlat) * dlat, 6),
        round(round(float(longitude) / dlon) * dlon, 6),
    )


def coarsen(service: str, latitude, longitude, policy=None) -> tuple[float, float]:
    """The coordinates a request to ``service`` may carry -- **the one place** a location becomes
    request coordinates (the URL builders of :mod:`camber.weather_source` call it).

    ``public`` (or no policy) returns them unchanged. ``coarse`` and ``offline`` (an offline
    cache is keyed by the same coarse URL): ``"nasa_power"`` -> the POWER grid-cell centre
    (:data:`POWER_GRID`); ``"open_meteo"`` -> rounded to ``precision_deg``. ``"isd"`` never takes
    coordinates (:class:`PrivacyViolation`): its station is chosen locally.
    """
    pol = WeatherPolicy.coerce(policy)
    if service == "isd":
        raise PrivacyViolation("ISD requests carry no coordinates: choose the station locally")
    if service not in ("nasa_power", "open_meteo"):
        raise ValueError(f"{service!r} takes no coordinates")
    lat, lon = float(latitude), float(longitude)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 360):
        raise ValueError(f"not a latitude/longitude: ({latitude}, {longitude})")
    if pol is None or not pol.coarse:
        return latitude, longitude
    if service == "nasa_power":
        return grid_snap(lat, lon, *POWER_GRID)
    p = float(pol.precision_deg)
    la, lo = grid_snap(lat, lon, p, p)
    nd = _decimals(p)
    return round(la, nd), round(lo, nd)


def coarsening_note(service: str, policy=None) -> str:
    """One line on what a request to ``service`` carries under ``policy`` (for provenance)."""
    pol = WeatherPolicy.coerce(policy)
    if service == "isd":
        return (
            "station chosen locally from the ISD catalogue; requests carry the station id and "
            "year only"
        )
    if pol is None or not pol.coarse:
        return "coordinates as given (privacy public)"
    if service == "nasa_power":
        return "snapped to the POWER grid-cell centre (0.5° lat x 0.625° lon, ~55 x 50 km)"
    km = 111.2 * float(pol.precision_deg)
    return f"rounded to {pol.precision_deg:g}° (~{km:.0f} km of latitude; within ~{km / 2:.0f} km)"


def _on_grid(value: str, step: float) -> bool:
    try:
        v = float(value)
    except ValueError:
        return False
    k = v / step
    frac = value.split(".", 1)[1] if "." in value else ""
    return abs(k - round(k)) < 1e-6 and len(frac) <= 6


def check_url(service: str, url: str, policy=None) -> None:
    """Raise :class:`PrivacyViolation` unless ``url`` is a request ``service`` may send under
    ``policy``. ``public`` checks only the host (the pre-0.94 behaviour is unchanged); ``coarse``
    and ``offline`` check everything: https, the host, the path, that every query key is one
    the service needs, that every value is short and free of free text, and that coordinates
    sit on the POWER grid or the Open-Meteo precision grid. ISD URLs carry no query at all.
    """
    pol = WeatherPolicy.coerce(policy)
    parts = urlsplit(url)
    if service not in _HOSTS:
        raise PrivacyViolation(f"unknown outbound service {service!r}")
    if parts.hostname not in _HOSTS[service]:
        raise PrivacyViolation(f"{service} request to an unexpected host: {parts.hostname}")
    if pol is None or not pol.coarse:
        return
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
        raise PrivacyViolation(f"{service} request is not a plain https URL")
    if service == "nominatim":
        raise PrivacyViolation(
            "geocoding sends an address; with weather privacy coarse/offline give coordinates, "
            "a city or an airport code instead (resolved locally)"
        )
    query = parse_qsl(parts.query, keep_blank_values=True)
    if service == "isd":
        if query or not _ISD_PATH.match(parts.path):
            raise PrivacyViolation(f"ISD request other than the catalogue or a station file: {url}")
        return
    allowed = _KEYS[service]
    for k, v in query:
        if k not in allowed:
            raise PrivacyViolation(f"{service} request carries an unexpected field {k!r}")
        if k == "api_key":
            continue  # a credential for the service itself (URDB), redacted in the audit log
        if not _SAFE_VALUE.match(v):
            raise PrivacyViolation(f"{service} request field {k!r} is not a short code or number")
    q = dict(query)
    if service in ("nasa_power", "open_meteo"):
        if "latitude" not in q or "longitude" not in q:
            raise PrivacyViolation(f"{service} request without coordinates")
        dlat, dlon = POWER_GRID if service == "nasa_power" else (pol.precision_deg,) * 2
        if not (_on_grid(q["latitude"], dlat) and _on_grid(q["longitude"], dlon)):
            raise PrivacyViolation(
                f"{service} request coordinates ({q['latitude']}, {q['longitude']}) are finer "
                f"than the policy allows ({dlat} x {dlon} deg)"
            )


def redact_url(url: str) -> str:
    """``url`` with any ``api_key`` value replaced by ``REDACTED`` (for the audit log)."""
    return re.sub(r"(api_key=)[^&]*", r"\1REDACTED", url)


# ----------------------------------------------------------------------------------------- audit


def default_weather_dir() -> str:
    """Where ``coarse`` / ``offline`` keep their cache (and the audit log) when no ``cache_dir``
    is given: ``$CAMBER_WEATHER_DIR``, else ``$XDG_CACHE_HOME/camber/weather``, else
    ``~/.cache/camber/weather``."""
    env = os.environ.get("CAMBER_WEATHER_DIR")
    if env:
        return env
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "camber", "weather")


def weather_audit_path(*, workspace=None, facility_id=None, cache_dir=None) -> str | None:
    """``state/<facility_id>/weather_audit.ndjson`` in a workspace, else next to ``cache_dir``,
    else ``None``."""
    if workspace and facility_id:
        from .portfolio._state import state_dir

        return os.path.join(state_dir(os.fspath(workspace), facility_id), AUDIT_FILE)
    if cache_dir:
        return os.path.join(os.fspath(cache_dir), AUDIT_FILE)
    return None


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WeatherAudit:
    """Append-only log of outbound requests (one JSON object per line).

    ``facility_id`` is written to each local record (never to a URL); ``privacy`` is the mode
    the requests were made under. ``clock`` (``() -> "YYYY-MM-DDTHH:MM:SSZ"``) is injectable.
    """

    def __init__(self, path: str, *, facility_id=None, privacy: str = "public", clock=None):
        self.path = os.fspath(path)
        self.facility_id = facility_id
        self.privacy = privacy
        self._clock = clock or _utc_now
        self.records: list = []  # what this instance appended, in order

    def record(
        self,
        *,
        service: str,
        url: str,
        purpose: str = "",
        cache: str,
        sent: bool,
        error: str | None = None,
        privacy: str | None = None,
    ) -> dict:
        """Append one record and return it (``privacy`` defaults to the log's own mode)."""
        rec = {
            "ts": self._clock(),
            "service": service,
            "url": redact_url(url),
            "purpose": purpose,
            "cache": cache,
            "sent": bool(sent),
            "privacy": privacy or self.privacy,
        }
        if self.facility_id:
            rec["facility_id"] = self.facility_id
        if error:
            rec["error"] = error
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
        self.records.append(rec)
        return rec


def read_weather_audit(paths, *, since=None, facility_id=None) -> list:
    """Records from one or more audit files, oldest first; ``since`` (``YYYY-MM-DD`` or an ISO
    timestamp) keeps records at or after it; ``facility_id`` keeps that facility's."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn last line
                    if isinstance(rec, dict):
                        out.append(rec)
        except FileNotFoundError:
            continue
    if since:
        cut = str(since)
        out = [r for r in out if str(r.get("ts", "")) >= cut]
    if facility_id:
        out = [r for r in out if r.get("facility_id") == facility_id]
    return sorted(out, key=lambda r: str(r.get("ts", "")))


# ------------------------------------------------------------------------------------- transport


def guarded_transport(
    service: str,
    network: Callable,
    *,
    policy=None,
    audit: WeatherAudit | None = None,
    purpose: str = "",
    cache: Callable[[Callable], Callable] | None = None,
) -> Callable:
    """Wrap a transport so every request is checked, optionally cached, and audited.

    ``network`` is the transport that reaches the service; ``cache`` (``inner -> transport``,
    e.g. ``lambda t: cached_transport(t, dir)``) sits between the check and the network, so a
    cache hit is logged as ``"hit"`` with ``sent: false``. Each URL passes :func:`check_url`
    before anything else; under ``offline`` the network is never called
    (:class:`OfflineViolation`). With no policy (or ``public``) and no audit this is exactly
    ``cache(network)`` -- the pre-0.94 behaviour.
    """
    pol = WeatherPolicy.coerce(policy)
    guarded = pol is not None and pol.coarse
    if not guarded and audit is None:
        return cache(network) if cache is not None else network
    flag = {"sent": False}

    def net(url):
        if pol is not None and pol.offline:
            raise OfflineViolation(f"offline: {service} request not sent. {OFFLINE_HELP}")
        flag["sent"] = True
        return network(url)

    inner = cache(net) if cache is not None else net

    def send(url):
        check_url(service, url, pol)
        flag["sent"] = False
        err, missed = None, False
        try:
            return inner(url)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            missed = type(exc).__name__ in ("WeatherCacheMiss", "OfflineViolation")
            raise
        finally:
            if audit is not None:
                audit.record(
                    service=service,
                    url=url,
                    purpose=purpose,
                    cache="miss" if flag["sent"] or missed else "hit",
                    sent=flag["sent"],
                    error=err,
                    privacy=pol.privacy if pol is not None else "public",
                )

    return send


# ---------------------------------------------------------------------------------------- places
#
# A bundled table of major cities (city-centre coordinates to 0.01°, public knowledge), so a
# location can be named as a city and resolved locally -- nothing is geocoded over the network.
# Airport codes resolve from the ISD catalogue's ICAO column (camber.weather_source.resolve_place).

CITY_TABLE: dict = {
    # United States
    "albuquerque, nm": (35.08, -106.65),
    "anchorage, ak": (61.22, -149.90),
    "atlanta, ga": (33.75, -84.39),
    "austin, tx": (30.27, -97.74),
    "baltimore, md": (39.29, -76.61),
    "birmingham, al": (33.52, -86.80),
    "boise, id": (43.62, -116.20),
    "boston, ma": (42.36, -71.06),
    "buffalo, ny": (42.89, -78.88),
    "charleston, sc": (32.78, -79.93),
    "charlotte, nc": (35.23, -80.84),
    "chicago, il": (41.88, -87.63),
    "cincinnati, oh": (39.10, -84.51),
    "cleveland, oh": (41.50, -81.69),
    "columbus, oh": (39.96, -83.00),
    "dallas, tx": (32.78, -96.80),
    "denver, co": (39.74, -104.99),
    "des moines, ia": (41.59, -93.62),
    "fresno, ca": (36.74, -119.79),
    "hartford, ct": (41.76, -72.68),
    "honolulu, hi": (21.31, -157.86),
    "houston, tx": (29.76, -95.37),
    "indianapolis, in": (39.77, -86.16),
    "jacksonville, fl": (30.33, -81.66),
    "kansas city, mo": (39.10, -94.58),
    "las vegas, nv": (36.17, -115.14),
    "little rock, ar": (34.75, -92.29),
    "los angeles, ca": (34.05, -118.24),
    "louisville, ky": (38.25, -85.76),
    "memphis, tn": (35.15, -90.05),
    "miami, fl": (25.76, -80.19),
    "milwaukee, wi": (43.04, -87.91),
    "minneapolis, mn": (44.98, -93.27),
    "nashville, tn": (36.16, -86.78),
    "new orleans, la": (29.95, -90.07),
    "new york, ny": (40.71, -74.01),
    "oklahoma city, ok": (35.47, -97.52),
    "omaha, ne": (41.26, -95.93),
    "orlando, fl": (28.54, -81.38),
    "philadelphia, pa": (39.95, -75.17),
    "phoenix, az": (33.45, -112.07),
    "pittsburgh, pa": (40.44, -80.00),
    "portland, or": (45.52, -122.68),
    "providence, ri": (41.82, -71.41),
    "raleigh, nc": (35.78, -78.64),
    "richmond, va": (37.54, -77.44),
    "sacramento, ca": (38.58, -121.49),
    "salt lake city, ut": (40.76, -111.89),
    "san antonio, tx": (29.42, -98.49),
    "san diego, ca": (32.72, -117.16),
    "san francisco, ca": (37.77, -122.42),
    "san jose, ca": (37.34, -121.89),
    "seattle, wa": (47.61, -122.33),
    "st. louis, mo": (38.63, -90.20),
    "tampa, fl": (27.95, -82.46),
    "tucson, az": (32.22, -110.97),
    "washington, dc": (38.91, -77.04),
    # Canada
    "calgary, ab": (51.05, -114.07),
    "montreal, qc": (45.50, -73.57),
    "ottawa, on": (45.42, -75.70),
    "toronto, on": (43.65, -79.38),
    "vancouver, bc": (49.28, -123.12),
    # elsewhere
    "amsterdam, nl": (52.37, 4.90),
    "berlin, de": (52.52, 13.40),
    "copenhagen, dk": (55.68, 12.57),
    "dublin, ie": (53.35, -6.26),
    "helsinki, fi": (60.17, 24.94),
    "london, uk": (51.51, -0.13),
    "madrid, es": (40.42, -3.70),
    "mexico city, mx": (19.43, -99.13),
    "oslo, no": (59.91, 10.75),
    "paris, fr": (48.86, 2.35),
    "rome, it": (41.90, 12.50),
    "singapore, sg": (1.35, 103.82),
    "sofia, bg": (42.70, 23.32),
    "stockholm, se": (59.33, 18.07),
    "sydney, au": (-33.87, 151.21),
    "tokyo, jp": (35.68, 139.69),
    "vienna, at": (48.21, 16.37),
    "zurich, ch": (47.38, 8.54),
}


def resolve_city(name: str) -> tuple[str, float, float]:
    """``(key, latitude, longitude)`` of a city in :data:`CITY_TABLE` -- ``"Chicago, IL"`` or,
    when unambiguous, ``"Chicago"``. Local only; ``ValueError`` when unknown or ambiguous."""
    key = " ".join(str(name).strip().lower().split())
    if key in CITY_TABLE:
        return (key, *CITY_TABLE[key])
    hits = [k for k in CITY_TABLE if k.split(",")[0] == key]
    if len(hits) == 1:
        return (hits[0], *CITY_TABLE[hits[0]])
    if len(hits) > 1:
        raise ValueError(f"city {name!r} is ambiguous: {', '.join(sorted(hits))}")
    raise ValueError(
        f"unknown city {name!r}: give latitude/longitude, an airport ICAO code, or one of the "
        "bundled cities (camber.weather_privacy.CITY_TABLE)"
    )
