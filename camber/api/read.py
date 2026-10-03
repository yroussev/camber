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


def _facility_timezone(meta: dict) -> str | None:
    """The site's IANA zone for a registry entry, when the store or registry records one.

    0.96: the store holds naive wall-clock time, so the trend viewer labels its time axis with
    this zone. Looked up in order: a ``timezone`` on the registry entry, the dataset-catalog block
    (``local_timezone`` of a per-site ingest, else ``timezone``), then the catalog entry the
    facility was ingested from, then (0.99.1, #96) the zone an open-fdd ingest recorded in its
    ``"openfdd"`` provenance, so a facility ingested with 0.99.0 needs no re-ingest. Anything that
    is not a valid IANA zone is ignored.
    """
    from ..tsparse import check_timezone

    def valid(tz) -> str | None:
        if not isinstance(tz, str) or not tz.strip():
            return None
        try:
            return check_timezone(tz.strip())
        except ValueError:
            return None

    meta = meta or {}
    block = meta.get("dataset")
    if not isinstance(block, dict):
        block = {}
    for tz in (meta.get("timezone"), block.get("local_timezone"), block.get("timezone")):
        if valid(tz):
            return valid(tz)
    if block.get("dataset_id"):
        from ..config import _catalog_timezone

        # the dataset block alone: the catalog zone comes before the open-fdd provenance below
        tz = valid(_catalog_timezone({"dataset": block}))
        if tz:
            return tz
    ofdd = meta.get("openfdd")
    if isinstance(ofdd, dict):
        return valid(ofdd.get("timezone"))
    return None


class ReadAPI:
    """Query facade over a :class:`~camber.store.ParquetStore`."""

    def __init__(self, store):
        self.store = store

    def _facility_list(self) -> list:
        """``[{"facility_id", "name", "display_name", "state"}]`` per facility in the store.

        ``name`` is the registered name (falls back to the id), ``display_name`` the editable one,
        ``state`` the lifecycle state (``"active"`` when unregistered). Read-only: lifecycle
        changes happen through ``camber facility``, never over HTTP. 0.96: a ``timezone`` key
        (the site's IANA zone) is added only when the store or registry records one.
        """
        meta = self.store.facilities_meta()
        out = []
        for f in self.store.facilities():
            m = meta.get(f) or {}
            row = {
                "facility_id": f,
                "name": m.get("name") or f,
                "display_name": m.get("display_name") or m.get("name") or f,
                "state": m.get("state") or "active",
            }
            tz = _facility_timezone(m)
            if tz:
                row["timezone"] = tz
            out.append(row)
        return out

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
        """The facilities in the store: ``{"facility_id", "name", "display_name", "state"}``."""
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
