"""Read API facade over the time-series store (capability-map §8).

A small, transport-agnostic surface that returns JSON-serializable dicts for the
three things an external tool needs: the facilities in the store, the catalog of stored
series, and point history. Facilities are addressed by their stable ``facility_id`` (the
legacy ``site=`` argument is still accepted as an alias). The HTTP layer in
:mod:`camber.api.server` is a thin wrapper over this; tests and in-process callers use the
facade directly.
"""

from __future__ import annotations

import pandas as pd


class ReadAPI:
    """Query facade over a :class:`~camber.store.ParquetStore`."""

    def __init__(self, store):
        self.store = store

    def _facility_list(self) -> list:
        """``[{"facility_id", "name"}]`` per facility; name from the registry (falls back to id)."""
        meta = self.store.facilities_meta()
        return [
            {"facility_id": f, "name": (meta.get(f) or {}).get("name") or f}
            for f in self.store.facilities()
        ]

    def about(self) -> dict:
        """Service info: name, liveness flag, and the facilities in the store."""
        facilities = self._facility_list()
        return {
            "service": "camber read-api",
            "ok": True,
            "facilities": facilities,
            "sites": [f["facility_id"] for f in facilities],  # deprecated alias
        }

    def facilities(self) -> dict:
        """List the facilities present in the store as ``{"facility_id", "name"}``."""
        return {"facilities": self._facility_list()}

    def sites(self) -> dict:
        """Deprecated alias for :meth:`facilities` (returns facility_ids under a ``sites`` key)."""
        return {"sites": self.store.facilities()}

    def points(self, *, facility_id=None, site=None, equip=None, role=None) -> dict:
        """Catalog of stored series, optionally filtered by facility_id/equip/role."""
        facility_id = facility_id or site  # accept the legacy ``site=`` alias
        keys = self.store.points(facility_id=facility_id)
        rows = [
            {"facility_id": k.facility_id, "equip": k.equip, "role": k.role}
            for k in keys
            if (equip is None or k.equip == equip) and (role is None or k.role == role)
        ]
        return {"points": rows, "count": len(rows)}

    def history(
        self,
        *,
        facility_id=None,
        site=None,
        equip=None,
        role=None,
        start=None,
        end=None,
        limit=None,
    ) -> dict:
        """Point history (long form) with ISO timestamps, optionally limited."""
        facility_id = facility_id or site  # accept the legacy ``site=`` alias
        long = self.store.read_long(
            facility_id=facility_id,
            equips=[equip] if equip else None,
            roles=[role] if role else None,
            start=start,
            end=end,
        )
        if not long.empty and limit:
            long = long.head(int(limit))
        rows = (
            []
            if long.empty
            else [
                {
                    "ts": pd.Timestamp(ts).isoformat(),
                    "equip": eq,
                    "role": rl,
                    "value": (None if pd.isna(v) else float(v)),
                }
                for ts, eq, rl, v in zip(long["ts"], long["equip"], long["role"], long["value"])
            ]
        )
        return {"history": rows, "count": len(rows)}
