"""Read API facade over the time-series store (capability-map §8).

A small, transport-agnostic surface that returns JSON-serializable dicts for the
three things an external tool needs: the facilities in the store, the catalog of stored
series, and point history. Facilities are addressed by their stable ``facility_id`` (the
legacy ``site=`` argument is still accepted as an alias). The HTTP layer in
:mod:`camber.api.server` is a thin wrapper over this; tests and in-process callers use the
facade directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# the trend viewer's time-axis labels (0.103, #122): UTC is claimed only when a zone is recorded
_NO_ZONE_LABEL = "local time (no time zone recorded)"
_UTC_LABEL = "time (UTC)"


def _facility_timezone(meta: dict) -> str | None:
    """The site's IANA zone for a registry entry, when the store or registry records one (0.96).

    The store holds naive wall-clock time, so the trend viewer labels its time axis with this zone.
    0.100 (#104): the lookup is :func:`camber._provenance.facility_timezone`, shared with config
    runs: the entry's ``timezone``, the dataset-catalog block, the catalog entry, then the open-fdd
    provenance (0.99.1, #96).
    """
    from .._provenance import facility_timezone

    return facility_timezone(meta)


def _time_axis(tz: str | None) -> dict:
    """How the trend viewer labels its time axis for a facility whose zone is ``tz`` (0.103, #122).

    The store holds the publisher's naive wall clock. With a recorded zone the axis shows it as
    local time in that zone (``local_label``) and a UTC toggle converts it (``utc_label``). With
    no zone the clock cannot be placed on UTC, so there is no toggle and the label says so instead
    of claiming UTC (``utc_label`` is ``None``).
    """
    if tz:
        return {"timezone": tz, "local_label": f"local time ({tz})", "utc_label": _UTC_LABEL}
    return {"timezone": None, "local_label": _NO_ZONE_LABEL, "utc_label": None}


def _parse_ts(value, name: str):
    """A ``start`` / ``end`` query value as a naive :class:`pandas.Timestamp` (``None`` passes).

    The store holds naive wall-clock time, so an offset on the value is dropped (the wall clock it
    names is kept). A value pandas cannot parse raises ``ValueError`` (a 400 over HTTP).
    """
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: not a timestamp: {value!r}") from exc
    if ts is pd.NaT:
        raise ValueError(f"{name}: not a timestamp: {value!r}")
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _positive_int(value, name: str, minimum: int = 1):
    """A positive-integer query value (``None`` passes); anything else raises ``ValueError``."""
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: not an integer: {value!r}") from exc
    if n < minimum:
        raise ValueError(f"{name}: must be at least {minimum}, got {n}")
    return n


def _envelope_downsample(long: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """Thin each (equip, role) series of ``long`` to at most ``max_points`` rows (0.103, #122).

    A series with more non-null samples than ``max_points`` is split into ``max_points // 2``
    equal-width time buckets, and each bucket keeps its minimum and its maximum sample (one row
    when they are the same sample), in time order. Every spike and every dip survives, so do the
    series' overall extremes, and a flatline stays flat; a plain every-k-th thinning would drop
    a one-sample spike. A series at or under the budget is returned whole; null samples are dropped
    from a thinned series only. The result is sorted by ``ts``.
    """
    if long.empty or max_points is None:
        return long
    max_points = max(2, int(max_points))
    nb = max_points // 2
    parts = []
    for _, g in long.groupby(["equip", "role"], sort=False):
        if len(g) <= max_points:
            parts.append(g)
            continue
        g = g[g["value"].notna()]
        if len(g) <= max_points:
            parts.append(g)
            continue
        g = g.sort_values("ts", kind="stable").reset_index(drop=True)
        t = pd.DatetimeIndex(g["ts"]).asi8.astype("float64")
        span = t[-1] - t[0]
        if span <= 0:
            b = np.zeros(len(g), dtype="int64")
        else:
            b = np.minimum(np.floor((t - t[0]) / span * nb), nb - 1).astype("int64")
        by = g["value"].groupby(b)
        keep = np.unique(np.concatenate([by.idxmin().to_numpy(), by.idxmax().to_numpy()]))
        parts.append(g.loc[keep])
    out = pd.concat(parts, ignore_index=True) if parts else long.iloc[0:0]
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


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
        """Catalog of stored series, optionally filtered by facility_id/equip/role.

        0.103 (#122): with a ``facility_id`` the reply also carries ``time_axis``, how the trend
        viewer labels that facility's time axis (:func:`_time_axis`): ``timezone`` (or ``None``),
        ``local_label`` and ``utc_label`` (``None`` when no zone is recorded, so no UTC toggle).
        """
        facility_id = facility_id or site  # accept the legacy ``site=`` alias
        keys = self.store.points(facility_id=facility_id)
        rows = [
            {"facility_id": k.facility_id, "equip": k.equip, "role": k.role}
            for k in keys
            if (equip is None or k.equip == equip) and (role is None or k.role == role)
        ]
        out = {"points": rows, "count": len(rows)}
        if facility_id:
            meta = self.store.facilities_meta().get(facility_id) or {}
            out["time_axis"] = _time_axis(_facility_timezone(meta))
        return out

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
        max_points=None,
    ) -> dict:
        """Point history (long form) with ISO timestamps, optionally windowed, limited or thinned.

        ``start`` / ``end`` bound the window (inclusive, the store's naive wall clock); ``limit``
        keeps the first ``limit`` rows. 0.103 (#122): ``max_points`` thins each series to at most
        that many rows with a min/max envelope per time bucket (:func:`_envelope_downsample`), so
        a whole multi-year series can be drawn without losing its spikes. Besides ``history`` and
        ``count`` (the rows returned) the reply carries ``source_count`` (the rows in the window
        before thinning), ``downsampled`` (whether any series was thinned), ``max_points``, and
        ``first`` / ``last``, the first and last timestamp in the window (``None`` when empty).
        A malformed ``start``, ``end``, ``limit`` or ``max_points`` raises ``ValueError``.
        """
        facility_id = facility_id or site  # accept the legacy ``site=`` alias
        start = _parse_ts(start, "start")
        end = _parse_ts(end, "end")
        limit = _positive_int(limit, "limit")
        max_points = _positive_int(max_points, "max_points", minimum=2)
        long = self.store.read_long(
            facility_id=facility_id,
            equips=[equip] if equip else None,
            roles=[role] if role else None,
            start=start,
            end=end,
        )
        if not long.empty and limit:
            long = long.head(limit)
        source_count = len(long)
        first = None if long.empty else pd.Timestamp(long["ts"].min()).isoformat()
        last = None if long.empty else pd.Timestamp(long["ts"].max()).isoformat()
        if max_points is not None:
            long = _envelope_downsample(long, max_points)
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
        return {
            "history": rows,
            "count": len(rows),
            "source_count": source_count,
            "downsampled": len(rows) < source_count,
            "max_points": max_points,
            "first": first,
            "last": last,
        }
