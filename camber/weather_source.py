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

from collections.abc import Callable, Sequence

import pandas as pd

from .mandv.weather import c_to_f

__all__ = [
    "FILL_VALUE",
    "nasa_power_url",
    "nasa_power_transport",
    "fetch_nasa_power",
    "oat_reference",
]

_BASE_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
FILL_VALUE = -999.0  # NASA POWER hourly missing sentinel (values <= this are dropped to NaN)
_PARAM_COL = {"T2M": "oat_f", "RH2M": "rh_pct"}  # NASA parameter -> output column


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


def _index(keys, tz: str) -> pd.DatetimeIndex:
    """Parse ``YYYYMMDDHH`` keys to a DatetimeIndex; UTC-aware, or DST-correct naive-local."""
    idx = pd.to_datetime(list(keys), format="%Y%m%d%H", utc=True)
    if tz.upper() == "UTC":
        return idx
    return idx.tz_convert(tz).tz_localize(
        None
    )  # naive local civil time (joins to a BAS trend index)


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

    ``transport`` (default the stdlib one) is ``callable(url) -> parsed-JSON dict`` — inject a
    canned one to run offline. ``tz`` is ``"UTC"`` (tz-aware) or a site IANA zone (naive local;
    see the module docstring). Missing hours (``<= FILL_VALUE``) become NaN; a response with no data
    for a requested parameter raises ``ValueError``. numpy/pandas + stdlib only, deterministic.
    """
    transport = transport or nasa_power_transport(timeout=timeout)
    payload = transport(nasa_power_url(latitude, longitude, start, end, parameters=parameters))
    try:
        param_block = payload["properties"]["parameter"]
    except (KeyError, TypeError) as e:
        raise ValueError("NASA POWER response missing properties.parameter") from e

    columns: dict = {}
    for p in parameters:
        raw = param_block.get(p)
        if not raw:
            raise ValueError(
                f"NASA POWER returned no {p} data for ({latitude}, {longitude}) {start}..{end}"
            )
        keys = sorted(raw)  # chronological YYYYMMDDHH keys
        values = pd.Series([float(raw[k]) for k in keys], index=_index(keys, tz), dtype=float)
        values = values.where(values > FILL_VALUE)  # -999 fill -> NaN (never treated as -999 °C)
        if p == "T2M":
            values = c_to_f(values)  # °C -> °F, matching load_epw's contract
        columns[_PARAM_COL.get(p, p.lower())] = values
    return pd.DataFrame(columns)


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
