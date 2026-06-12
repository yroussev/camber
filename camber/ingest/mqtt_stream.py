"""MQTT streaming ingest adapter — subscribe, buffer, shape ([mqtt] extra).

Unlike the poll-based adapters, MQTT is *push*: the broker delivers telemetry as it's published.
This adapter subscribes to configured topics, buffers each message as a timestamped sample, and
shapes the buffer into per-point series — the natural path for streaming/IIoT telemetry
(including Sparkplug-B payloads, by pointing ``value_key`` at the metric field).

**Read-only by construction.** The adapter only subscribes and reads; it never publishes. Use
MQTT over TLS (``tls=True``) and authenticate to the broker; never disable certificate
verification (see ``docs/SECURITY.md``).

paho-mqtt (EDL-1.0 / BSD-style) is an optional dependency, imported lazily; the message path is
pure (:meth:`ingest`) so it is fully testable without a broker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd


@dataclass
class MqttPoint:
    """A subscribed topic mapped to a point name and a payload parse rule."""

    topic: str
    name: str
    value_key: str | None = None   # JSON key to read; None = parse the whole payload as a float
    unit: str = ""


def parse_payload(payload, value_key: str | None = None) -> float:
    """Parse an MQTT payload to a float: a JSON field (``value_key``) or the bare value."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if value_key is not None:
        return float(json.loads(payload)[value_key])
    return float(payload)


class MqttStreamSource:
    """A read-only MQTT :class:`~camber.ingest.base.SourceAdapter` (subscribe + buffer).

    Construct with the topic→point map, call :meth:`subscribe` to start receiving (lazy paho),
    and :meth:`to_frame` / :meth:`load_points` to snapshot the buffer. :meth:`ingest` is the
    pure message handler used by both the live callback and tests.
    """

    def __init__(self, points, *, host: str = "127.0.0.1", port: int = 1883,
                 tls: bool = False, client=None):
        self._by_topic = {p.topic: p for p in points}
        self._points = {p.name: p for p in points}
        self._host, self._port, self._tls = host, port, tls
        self._client = client
        self._buffer: dict = {p.name: [] for p in points}   # name -> [(ts, value)]

    def ingest(self, topic: str, payload, *, ts=None) -> bool:
        """Handle one message: buffer ``(ts, value)`` for the topic's point. Returns whether
        the topic was mapped (unmapped topics are ignored). Pure — no network."""
        p = self._by_topic.get(topic)
        if p is None:
            return False
        ts = pd.Timestamp(ts) if ts is not None else pd.Timestamp.now()
        try:
            value = parse_payload(payload, p.value_key)
        except (ValueError, KeyError, json.JSONDecodeError):
            return False
        self._buffer[p.name].append((ts, value))
        return True

    def point_names(self) -> list[str]:
        return list(self._points)

    def units(self) -> dict:
        return {n: p.unit for n, p in self._points.items() if p.unit}

    def to_frame(self, names=None, resample=None) -> pd.DataFrame:
        """Shape the buffered samples into a DataFrame (one column per point)."""
        sel = list(self._points) if names is None else [n for n in names if n in self._points]
        cols = {}
        for n in sel:
            buf = self._buffer.get(n, [])
            if not buf:
                continue
            s = pd.Series({ts: v for ts, v in buf}).sort_index()
            s.index = pd.DatetimeIndex(s.index)
            cols[n] = s
        df = pd.DataFrame(cols).sort_index()
        if resample and not df.empty:
            df = df.resample(resample).mean()
        return df

    def load_points(self, names, resample=None) -> pd.DataFrame:
        """Return the buffered stream so far as a DataFrame (see :meth:`to_frame`)."""
        return self.to_frame(names, resample=resample)

    def _require_client(self):
        if self._client is None:
            try:
                import paho.mqtt.client as mqtt
            except Exception as e:  # noqa: BLE001
                raise ImportError('the MQTT adapter needs the optional extra: '
                                  'pip install "camber[mqtt]"') from e
            self._client = mqtt.Client()
            if self._tls:
                self._client.tls_set()           # default secure context; verifies broker cert
        return self._client

    def subscribe(self, *, qos: int = 0, timeout: float | None = None):
        """Connect, subscribe to all mapped topics, and route messages into :meth:`ingest`.

        Starts paho's network loop; messages accumulate in the buffer until :meth:`to_frame`.
        """
        client = self._require_client()

        def _on_message(_client, _userdata, msg):
            self.ingest(msg.topic, msg.payload)

        client.on_message = _on_message
        client.connect(self._host, self._port)
        for topic in self._by_topic:
            client.subscribe(topic, qos=qos)
        client.loop_start()
        return client
