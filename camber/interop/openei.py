"""OpenEI Utility Rate Database (URDB) fetch + mapping to the native tariff engine.

The URDB (openei.org/wiki/Utility_Rate_Database) publishes thousands of real utility
tariffs behind a computer-readable API. :func:`fetch_urdb_rate` pulls one rate by its
page label (stdlib ``urllib`` -- no dependency; an OpenEI/NREL API key is required), and
:func:`tariff_from_urdb` maps the URDB JSON onto :class:`camber.tariff.Tariff` so it can
bill a load directly. The network and the parse are separate so the mapper is testable
without a key, and ``fetch_urdb_rate`` accepts an injectable ``transport`` for tests.

The mapper covers the common URDB fields (fixed charge, TOU energy with tiers + 12x24
schedules, TOU and flat monthly demand, ratchet). For exotic rates, the same URDB JSON
can be handed to NREL PySAM via :mod:`camber.interop.tariff_nrel`.
"""

from __future__ import annotations

import json
import os

from ..tariff import Tariff

_URDB_URL = ("https://api.openei.org/utility_rates?version=latest&format=json"
             "&detail=full&getpage={label}&api_key={key}")


def fetch_urdb_rate(label: str, api_key: str | None = None, *, transport=None,
                    timeout: float = 30.0) -> dict:
    """Fetch one URDB rate page by ``label``; return the rate JSON dict.

    ``api_key`` defaults to the ``OPENEI_API_KEY`` environment variable -- get a free key
    at https://openei.org/services/ and export it (never hard-code or commit the key).
    ``transport`` (a ``url -> dict`` callable) overrides the network for tests; the live
    path uses ``urllib``.
    """
    api_key = api_key or os.environ.get("OPENEI_API_KEY")
    if not api_key and transport is None:
        raise ValueError("OpenEI API key required: pass api_key= or set OPENEI_API_KEY")
    url = _URDB_URL.format(label=label, key=api_key or "")
    if transport is not None:
        payload = transport(url)
    else:
        from urllib.request import urlopen        # stdlib; no dependency
        with urlopen(url, timeout=timeout) as resp:   # noqa: S310 -- fixed OpenEI host
            payload = json.loads(resp.read().decode("utf-8"))
    items = payload.get("items") or []
    if not items:
        raise ValueError(f"no URDB rate found for label {label!r}")
    return items[0]


def _rate_structure(structure) -> list:
    """URDB ratestructure (periods of tier dicts) -> [[(upper|None, rate)], ...]."""
    out = []
    for period in structure or []:
        tiers = []
        for t in period:
            upper = t.get("max")
            rate = float(t.get("rate", 0.0) or 0.0) + float(t.get("adj", 0.0) or 0.0)
            tiers.append((upper if upper is not None else None, rate))
        out.append(tiers or [(None, 0.0)])
    return out


def tariff_from_urdb(urdb: dict) -> Tariff:
    """Map a URDB rate JSON dict onto a native :class:`~camber.tariff.Tariff`."""
    fixed = float(urdb.get("fixedchargefirstmeter", 0.0) or 0.0)
    if "month" not in str(urdb.get("fixedchargeunits", "$/month")).lower():
        fixed = 0.0   # only monthly fixed charges map cleanly to the monthly engine

    energy = _rate_structure(urdb.get("energyratestructure")) or [[(None, 0.0)]]
    demand = _rate_structure(urdb.get("demandratestructure"))
    flat = _rate_structure(urdb.get("flatdemandstructure"))
    flat_months = urdb.get("flatdemandmonths") if flat else None

    ratchet = urdb.get("demandratchetpercentage")
    if isinstance(ratchet, list):
        ratchet = max((float(x) for x in ratchet), default=0.0)
    ratchet = float(ratchet or 0.0)
    # URDB ratchet may be a fraction (0..1) or a percent (0..100); normalize to percent
    if 0 < ratchet <= 1.0:
        ratchet *= 100.0

    return Tariff(
        name=str(urdb.get("name", urdb.get("label", "urdb"))),
        fixed_monthly=fixed,
        energy_rates=energy,
        energy_weekday=urdb.get("energyweekdayschedule"),
        energy_weekend=urdb.get("energyweekendschedule"),
        demand_rates=demand,
        demand_weekday=urdb.get("demandweekdayschedule"),
        demand_weekend=urdb.get("demandweekendschedule"),
        flat_demand_rates=flat,
        flat_demand_months=list(flat_months) if flat_months is not None else None,
        ratchet_pct=ratchet,
    )
