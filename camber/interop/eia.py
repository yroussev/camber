"""EIA state retail prices for unit-scale screening: opt-in, cached, keyed (provisional, 0.92, #71).

:mod:`camber.unit_scale` judges whether a bill's quantities are plausible at x0.001, x1 or x1000
partly from the implied price. For electricity and natural gas the U.S. Energy Information
Administration publishes the average commercial retail price by state (EIA Open Data API v2):

* electricity: ``electricity/retail-sales`` (``price``, cents/kWh, sector ``COM``);
* natural gas: ``natural-gas/pri/sum`` (process ``PCS``, "Price Delivered to Commercial Sectors",
  $/thousand cubic feet).

:func:`fetch_state_price` averages the months in a window into a :class:`StatePrice` in $/MMBtu.

**Opt-in and private.** Nothing is fetched unless the caller asks (``price_source="eia"`` on the
unit-scale check, or a direct call). The request carries only the fuel's route, the state code and
the dates, plus the API key. The key comes from ``api_key=`` or the ``EIA_API_KEY`` environment
variable (a free key: https://www.eia.gov/opendata/), is added by the live transport only and never
enters a cache key or a result. No site identifier, address or account is ever sent.

**Testable and cacheable** like :mod:`camber.weather_source`: the HTTP call goes through an
injectable ``transport`` (``callable(url) -> parsed JSON``), and ``cache_dir`` wraps it with
:func:`camber.weather_source.cached_transport` (keyed by the key-free URL). ``offline=True`` reads
only the cache and raises :class:`~camber.weather_source.WeatherCacheMiss` on a miss; callers such
as :mod:`camber.unit_scale` then fall back to the bundled price bands.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

__all__ = [
    "EIA_FUELS",
    "StatePrice",
    "eia_price_url",
    "eia_transport",
    "fetch_state_price",
]

_BASE = "https://api.eia.gov/v2/"
# thousand cubic feet of natural gas in MMBtu, for $/Mcf -> $/MMBtu (EIA's own conversions use
# ~1.036; the ENERGY STAR U.S. factor is 1.026 -- either is far inside a screening band)
_MMBTU_PER_MCF = 1.026
_MMBTU_PER_KWH = 3412.14163 / 1.0e6

#: The fuels :func:`fetch_state_price` serves.
EIA_FUELS = ("electricity", "natural_gas")

_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


@dataclass(frozen=True)
class StatePrice:
    """A state's average commercial retail price over a window, in $/MMBtu of billed energy."""

    fuel: str
    state: str
    usd_per_mmbtu: float
    native: float  # the mean in EIA's own unit
    native_unit: str  # "cents/kWh" or "$/Mcf"
    periods: tuple  # the months averaged ("YYYY-MM")
    source: str  # route + facets, no key

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        d = asdict(self)
        d["periods"] = list(self.periods)
        return d


def _month(d) -> str:
    text = str(d)[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        raise ValueError(f"a date (YYYY-MM or later) is needed, got {d!r}")
    return text


def eia_price_url(fuel: str, state: str, start, end) -> str:
    """The key-free EIA API v2 URL for ``fuel``'s monthly commercial price in ``state`` from the
    month of ``start`` to the month of ``end``. Only the state and the months are in it."""
    st = str(state).strip().upper()
    if st not in _STATES:
        raise ValueError(f"unknown U.S. state code {state!r}")
    s, e = _month(start), _month(end)
    if fuel == "electricity":
        route = "electricity/retail-sales/data/"
        q = f"data[0]=price&facets[stateid][]={st}&facets[sectorid][]=COM"
    elif fuel == "natural_gas":
        route = "natural-gas/pri/sum/data/"
        q = f"data[0]=value&facets[duoarea][]=S{st}&facets[process][]=PCS"
    else:
        raise ValueError(f"EIA state prices cover {', '.join(EIA_FUELS)}, not {fuel!r}")
    return f"{_BASE}{route}?frequency=monthly&{q}&start={s}&end={e}&length=5000"


def eia_transport(
    api_key: str | None = None, *, timeout: float = 30.0
) -> Callable[[str], dict]:  # pragma: no cover - the one real-network path
    """The default stdlib-``urllib`` transport: adds the API key (``api_key`` or ``EIA_API_KEY``)
    to a key-free URL and returns the parsed JSON. ``ValueError`` when there is no key."""
    key = api_key or os.environ.get("EIA_API_KEY")
    if not key:
        raise ValueError("EIA API key required: pass api_key= or set EIA_API_KEY")

    def transport(url: str) -> dict:
        from urllib.request import Request, urlopen

        req = Request(f"{url}&api_key={key}", headers={"User-Agent": "camber"})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- fixed EIA host
            return json.loads(resp.read().decode("utf-8"))

    return transport


def fetch_state_price(
    fuel: str,
    state: str,
    start,
    end,
    *,
    transport: Callable[[str], dict] | None = None,
    api_key: str | None = None,
    cache_dir: str | None = None,
    offline: bool = False,
) -> StatePrice:
    """The mean monthly commercial retail price of ``fuel`` in ``state`` between ``start`` and
    ``end`` (inclusive months), as a :class:`StatePrice` in $/MMBtu.

    ``transport`` (``url -> dict``) replaces the network (tests); otherwise the live transport
    needs a key (``api_key`` or ``EIA_API_KEY``). ``cache_dir`` caches responses by their key-free
    URL; ``offline=True`` never calls the network. ``ValueError`` when EIA returns no price.
    """
    url = eia_price_url(fuel, state, start, end)
    if transport is None and not offline:
        transport = eia_transport(api_key)
    if cache_dir is not None or offline:
        from ..weather_source import cached_transport

        if cache_dir is None:
            raise ValueError("offline=True reads the cache: give cache_dir")

        def _no_network(u: str) -> dict:  # pragma: no cover - offline never calls inner
            raise RuntimeError("offline")

        transport = cached_transport(transport or _no_network, cache_dir, offline=offline)
    assert transport is not None
    payload = transport(url)
    rows = ((payload or {}).get("response") or {}).get("data") or []
    field = "price" if fuel == "electricity" else "value"
    vals, periods = [], []
    for r in rows:
        try:
            v = float(r.get(field))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            vals.append(v)
            periods.append(str(r.get("period")))
    if not vals:
        raise ValueError(f"EIA returned no {fuel} price for {state} {_month(start)}..{_month(end)}")
    native = sum(vals) / len(vals)
    if fuel == "electricity":
        usd, unit = native / 100.0 / _MMBTU_PER_KWH, "cents/kWh"
    else:
        usd, unit = native / _MMBTU_PER_MCF, "$/Mcf"
    return StatePrice(
        fuel=fuel,
        state=str(state).strip().upper(),
        usd_per_mmbtu=round(usd, 4),
        native=round(native, 4),
        native_unit=unit,
        periods=tuple(sorted(periods)),
        source=url.replace(_BASE, "EIA API v2 "),
    )
