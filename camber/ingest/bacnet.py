"""BACnet ingest adapter — read-only property/Trend-Log reads ([bacnet] extra).

The analytics-friendly BACnet source is a **Trend Log** object: it already holds timestamped
historical records, matching CAMBER's per-point-series contract. This adapter also snapshots
present values. For long trends, a historian/SQL source is still preferred (see
``docs/SECURITY.md`` — historian-first posture).

**Read-only by construction.** This module only reads (ReadProperty / ReadPropertyMultiple /
Trend Log record reads). It does not import or wrap WriteProperty or any command/actuation
service, so no code path here can change a controller on an OT network.

**BACnet/SC (Secure Connect) — experimental, certificate-gated.** Legacy BACnet/IP is cleartext
and unauthenticated; BACnet/SC runs over secure WebSockets (``wss://`` + TLS 1.3) with mutual
X.509 certificate authentication through a hub. Joining an SC network requires an *operational
certificate* issued for that network plus the hub URI — there is no IP-only shortcut. CAMBER
plumbs the SC config (hub URI, cert/key/CA) through to the underlying stack, but production-grade
SC mutual-auth is still maturing in the open-source Python stack (bacpypes3); treat SC here as
**experimental**, and prefer reaching BACnet data through a historian/gateway that already
speaks SC on the OT side.

bacpypes3 (MIT) is an optional dependency, imported lazily; the client is injectable so the
record-shaping logic is testable without a network or the library.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Services this adapter may use — all read-only. Documented and asserted by tests.
READ_SERVICES = ("ReadProperty", "ReadPropertyMultiple", "ReadRange")


@dataclass
class BacnetTarget:
    """Connection target for a BACnet device or BACnet/SC hub.

    Legacy: set ``address`` (e.g. ``"10.0.0.5"`` or ``"10.0.0.5:47808"``). BACnet/SC: set
    ``secure=True`` with ``hub_uri`` (``wss://…``) and the operational ``cert``/``key`` plus the
    issuer ``ca`` certificate path. SC config is validated by :meth:`validate`.
    """

    address: str | None = None
    secure: bool = False
    hub_uri: str | None = None
    cert: str | None = None        # operational certificate (PEM) path
    key: str | None = None         # operational private key (PEM) path
    ca: str | None = None          # issuer / CA certificate (PEM) path

    def validate(self) -> "BacnetTarget":
        """Check the target is internally consistent; raise ValueError otherwise."""
        if self.secure:
            missing = [n for n in ("hub_uri", "cert", "key", "ca") if not getattr(self, n)]
            if missing:
                raise ValueError("BACnet/SC requires " + ", ".join(missing)
                                 + " (operational certificate + hub URI)")
        elif not self.address:
            raise ValueError("a legacy BACnet target needs an address (or set secure=True "
                             "with SC hub/cert config)")
        return self


@dataclass
class BacnetPoint:
    """A BACnet object mapped to a point name. ``object_id`` is ``(object_type, instance)``,
    e.g. ``("trendLog", 3)`` for history or ``("analogInput", 1)`` for present value."""

    name: str
    object_id: tuple
    unit: str = ""


def trendlog_to_series(records) -> pd.Series:
    """Shape Trend Log records into a time-indexed Series (pure; no network).

    Accepts an iterable of ``(timestamp, value)`` pairs or objects exposing ``.timestamp`` and
    ``.value``. Unparseable timestamps and non-numeric values are dropped; duplicate timestamps
    keep the last, matching :func:`camber.realio.load_point`.
    """
    pairs = []
    for r in records:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            ts, val = r[0], r[1]
        else:
            ts, val = getattr(r, "timestamp", None), getattr(r, "value", None)
        ts = pd.to_datetime(ts, errors="coerce")
        val = pd.to_numeric(val, errors="coerce")
        if pd.notna(ts) and pd.notna(val):
            pairs.append((ts, float(val)))
    if not pairs:
        return pd.Series(dtype=float)
    s = pd.Series({ts: v for ts, v in pairs}).sort_index()
    s.index = pd.DatetimeIndex(s.index)
    return s[~s.index.duplicated(keep="last")]


class BacnetSource:
    """A read-only BACnet :class:`~camber.ingest.base.SourceAdapter`.

    ``client`` is any object exposing ``read_trend_log(object_id) -> records`` and
    ``read_present_value(object_id) -> value`` — injected for tests, or built lazily from
    bacpypes3 against the (validated) :class:`BacnetTarget`.
    """

    def __init__(self, points, target: BacnetTarget, *, client=None):
        self._points = {p.name: p for p in points}
        self._target = target.validate()
        self._client = client

    def _require_client(self):
        if self._client is None:
            try:
                import bacpypes3  # noqa: F401
            except Exception as e:  # noqa: BLE001
                raise ImportError('the BACnet adapter needs the optional extra: '
                                  'pip install "camber[bacnet]"') from e
            raise NotImplementedError(
                "no BACnet client injected. The bacpypes3-backed client (incl. experimental "
                "BACnet/SC) is configured per deployment; inject a client exposing "
                "read_trend_log(object_id) and read_present_value(object_id). See docs/"
                "INGEST-PROTOCOLS.md.")
        return self._client

    def point_names(self) -> list[str]:
        return list(self._points)

    def units(self) -> dict:
        return {n: p.unit for n, p in self._points.items() if p.unit}

    def read_trend_log(self, name: str) -> pd.Series:
        """Read a Trend Log point's historical records into a Series (read-only)."""
        client = self._require_client()
        s = trendlog_to_series(client.read_trend_log(self._points[name].object_id))
        s.name = name
        return s

    def read_snapshot(self, names=None) -> dict:
        """Read present values for the named points (read-only). ``name -> value``."""
        client = self._require_client()
        sel = list(self._points) if names is None else [n for n in names if n in self._points]
        return {n: client.read_present_value(self._points[n].object_id) for n in sel}

    def load_points(self, names, resample=None) -> pd.DataFrame:
        """Load points by reading each one's Trend Log into a DataFrame (one column each)."""
        cols = {}
        for n in (names or self._points):
            if n not in self._points:
                continue
            s = self.read_trend_log(n)
            if not s.empty:
                cols[n] = s
        df = pd.DataFrame(cols).sort_index()
        if resample and not df.empty:
            df = df.resample(resample).mean()
        return df
