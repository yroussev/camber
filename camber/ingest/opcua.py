"""OPC-UA ingest adapter — read-only value / history reads ([opcua] extra).

OPC-UA is the dominant industrial (and increasingly building) protocol with security built into
the wire model: per-endpoint security policies, message signing/encryption, and X.509 +
user authentication. The analytics-friendly source is a **historizing node** — the server
already retains timestamped values, matching CAMBER's per-point-series contract; the adapter
also snapshots current values.

**Read-only by construction.** This module only reads (the OPC-UA Read and HistoryRead
services). It does not import or wrap WriteValues / any call/command service, so no code path
here can change a server on an OT network.

**Security.** Use an encrypted, authenticated endpoint: pass an :class:`OpcUaSecurity` with the
asyncua security string (policy, mode, client cert/key) and/or username/password. Don't connect
to a `None`-security endpoint on a production network (see ``docs/SECURITY.md``).

**Licensing.** The backend, ``asyncua`` (FreeOpcUa / opcua-asyncio), is **LGPL-3.0** — kept as
an optional, dynamically-imported extra (never vendored or statically bundled), consistent with
CAMBER's Apache-2.0 license. The client is injectable so the data-shaping logic is testable
without a server or the library.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Services this adapter may use — both read-only. Documented and asserted by tests.
READ_SERVICES = ("Read", "HistoryRead")


@dataclass
class OpcUaSecurity:
    """OPC-UA connection security. ``security_string`` is asyncua's
    ``"Policy,Mode,cert.der,key.pem[,server_cert.der]"``; ``username``/``password`` add user
    authentication. Leave all None only for a trusted/test endpoint."""

    security_string: str | None = None
    username: str | None = None
    password: str | None = None


@dataclass
class OpcUaPoint:
    """An OPC-UA node mapped to a point name. ``node_id`` is a NodeId string, e.g.
    ``"ns=2;s=AHU1.SupplyAirTemp"`` or ``"ns=2;i=1234"``."""

    name: str
    node_id: str
    unit: str = ""


def history_to_series(records) -> pd.Series:
    """Shape OPC-UA history into a time-indexed Series (pure; no network).

    Accepts ``(timestamp, value)`` pairs or asyncua ``DataValue`` objects (``.SourceTimestamp``
    + ``.Value.Value``). Unparseable timestamps / non-numeric values are dropped; duplicate
    timestamps keep the last, matching :func:`camber.realio.load_point`.
    """
    pairs = []
    for r in records:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            ts, val = r[0], r[1]
        else:
            ts = getattr(r, "SourceTimestamp", None)
            val = getattr(getattr(r, "Value", None), "Value", None)
        ts = pd.to_datetime(ts, errors="coerce")
        val = pd.to_numeric(val, errors="coerce")
        if pd.notna(ts) and pd.notna(val):
            pairs.append((ts, float(val)))
    if not pairs:
        return pd.Series(dtype=float)
    s = pd.Series({ts: v for ts, v in pairs}).sort_index()
    s.index = pd.DatetimeIndex(s.index)
    return s[~s.index.duplicated(keep="last")]


class _AsyncuaClient:  # pragma: no cover - exercised only against a live server
    """A thin, read-only wrapper over asyncua's synchronous client."""

    def __init__(self, url: str, security: OpcUaSecurity | None = None):
        from asyncua.sync import Client
        self._c = Client(url)
        if security:
            if security.security_string:
                self._c.set_security_string(security.security_string)
            if security.username:
                self._c.set_user(security.username)
            if security.password:
                self._c.set_password(security.password)
        self._connected = False

    def _ensure(self):
        if not self._connected:
            self._c.connect()
            self._connected = True

    def read_value(self, node_id):
        self._ensure()
        return self._c.get_node(node_id).read_value()

    def read_history(self, node_id, start, end):
        self._ensure()
        dvs = self._c.get_node(node_id).read_raw_history(starttime=start, endtime=end)
        return [(dv.SourceTimestamp, dv.Value.Value) for dv in dvs]

    def disconnect(self):
        if self._connected:
            self._c.disconnect()
            self._connected = False


class OpcUaSource:
    """A read-only OPC-UA :class:`~camber.ingest.base.SourceAdapter`.

    ``client`` is any object exposing ``read_value(node_id)`` and ``read_history(node_id,
    start, end) -> records`` — injected for tests, or built lazily from asyncua against ``url``
    (+ optional :class:`OpcUaSecurity`).
    """

    def __init__(self, points, *, url: str | None = None,
                 security: OpcUaSecurity | None = None, client=None):
        self._points = {p.name: p for p in points}
        self._url = url
        self._security = security
        self._client = client

    def _require_client(self):
        if self._client is None:
            try:
                import asyncua  # noqa: F401
            except Exception as e:  # noqa: BLE001
                raise ImportError('the OPC-UA adapter needs the optional extra: '
                                  'pip install "camber[opcua]"') from e
            if not self._url:
                raise ValueError("OpcUaSource needs a url (or an injected client)")
            self._client = _AsyncuaClient(self._url, self._security)
        return self._client

    def point_names(self) -> list[str]:
        return list(self._points)

    def units(self) -> dict:
        return {n: p.unit for n, p in self._points.items() if p.unit}

    def read_snapshot(self, names=None) -> dict:
        """Read the current value of each named node (read-only). ``name -> value``."""
        client = self._require_client()
        sel = list(self._points) if names is None else [n for n in names if n in self._points]
        out = {}
        for n in sel:
            v = client.read_value(self._points[n].node_id)
            out[n] = float(pd.to_numeric(v, errors="coerce"))
        return out

    def read_history(self, name: str, *, start, end) -> pd.Series:
        """Read a node's historized values over [start, end] into a Series (read-only)."""
        client = self._require_client()
        recs = client.read_history(self._points[name].node_id,
                                   pd.Timestamp(start), pd.Timestamp(end))
        s = history_to_series(recs)
        s.name = name
        return s

    def load_points(self, names, resample=None, *, start=None, end=None) -> pd.DataFrame:
        """Load points as a DataFrame. With ``start``/``end`` reads each node's history (one
        column per point); without a window, returns a one-row current-value snapshot."""
        if start is None and end is None:
            snap = self.read_snapshot(names)
            return pd.DataFrame([snap], index=pd.DatetimeIndex([pd.Timestamp.now()]))
        cols = {}
        for n in (names or self._points):
            if n not in self._points:
                continue
            s = self.read_history(n, start=start, end=end)
            if not s.empty:
                cols[n] = s
        df = pd.DataFrame(cols).sort_index()
        if resample and not df.empty:
            df = df.resample(resample).mean()
        return df
