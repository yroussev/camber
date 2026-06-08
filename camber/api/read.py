"""Read API facade over the time-series store (capability-map §8).

A small, transport-agnostic surface that returns JSON-serializable dicts for the
three things an external tool needs: the sites in the store, the catalog of stored
series, and point history. The HTTP layer in :mod:`camber.api.server` is a thin
wrapper over this; tests and in-process callers use the facade directly.
"""

from __future__ import annotations

import pandas as pd


class ReadAPI:
    """Query facade over a :class:`~camber.store.ParquetStore`."""

    def __init__(self, store):
        self.store = store

    def about(self) -> dict:
        """Service info: name, liveness flag, and the sites in the store."""
        return {"service": "camber read-api", "ok": True,
                "sites": self.store.sites()}

    def sites(self) -> dict:
        """List the sites present in the store."""
        return {"sites": self.store.sites()}

    def points(self, *, site=None, equip=None, role=None) -> dict:
        """Catalog of stored series, optionally filtered by site/equip/role."""
        keys = self.store.points(site=site)
        rows = [{"site": k.site, "equip": k.equip, "role": k.role} for k in keys
                if (equip is None or k.equip == equip)
                and (role is None or k.role == role)]
        return {"points": rows, "count": len(rows)}

    def history(self, *, site=None, equip=None, role=None, start=None, end=None,
                limit=None) -> dict:
        """Point history (long form) with ISO timestamps, optionally limited."""
        long = self.store.read_long(
            site=site,
            equips=[equip] if equip else None,
            roles=[role] if role else None,
            start=start, end=end)
        if not long.empty and limit:
            long = long.head(int(limit))
        rows = [] if long.empty else [
            {"ts": pd.Timestamp(ts).isoformat(), "equip": eq, "role": rl,
             "value": (None if pd.isna(v) else float(v))}
            for ts, eq, rl, v in zip(long["ts"], long["equip"],
                                     long["role"], long["value"])]
        return {"history": rows, "count": len(rows)}
