"""Modbus TCP ingest adapter — read-only register polling ([modbus] extra).

Modbus has no concept of history: a read returns the *current* register value. So this adapter
takes point snapshots (and can poll repeatedly into a short series); for analytics over long
trends, prefer a historian/SQL source (see ``docs/SECURITY.md`` — historian-first posture).

**Read-only by construction.** This module only ever calls Modbus *read* functions
(read_holding_registers / read_input_registers). It does not import or wrap any write coil/
register service, so no code path here can actuate equipment on an OT network.

Modbus itself has no authentication or encryption; treat any Modbus link as untrusted and keep
the analytics host outside the control VLAN (see ``docs/SECURITY.md``). pymodbus (BSD-3) is an
optional dependency, imported lazily; the client is injectable for testing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd


@dataclass
class ModbusPoint:
    """One readable Modbus register mapped to a point name."""

    name: str
    address: int
    kind: str = "holding"        # "holding" (read_holding_registers) | "input" (read_input_registers)
    slave: int = 1               # unit/slave id
    count: int = 1               # registers to read (1 = 16-bit, 2 = 32-bit)
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""               # engineering unit string


def decode_registers(registers, *, count: int = 1, scale: float = 1.0,
                     offset: float = 0.0) -> float:
    """Decode raw 16-bit register words to an engineering value (scale·raw + offset).

    ``count == 1`` reads a single 16-bit word; ``count == 2`` combines two words as a 32-bit
    big-endian (high word first) unsigned integer. Other counts use the first word.
    """
    regs = list(registers)
    if not regs:
        return float("nan")
    if count >= 2 and len(regs) >= 2:
        raw = (int(regs[0]) << 16) | int(regs[1])
    else:
        raw = int(regs[0])
    return raw * scale + offset


class ModbusSource:
    """A read-only Modbus TCP :class:`~camber.ingest.base.SourceAdapter` (snapshot/poll).

    ``client`` is any object exposing pymodbus-style ``read_holding_registers(address,
    count=, slave=)`` and ``read_input_registers(...)`` returning a response with
    ``.registers`` and ``.isError()`` — injected for tests, or built lazily from pymodbus.
    """

    def __init__(self, points, *, host: str = "127.0.0.1", port: int = 502, client=None):
        self._points = {p.name: p for p in points}
        self._host, self._port = host, port
        self._client = client

    def _require_client(self):
        if self._client is None:
            try:
                from pymodbus.client import ModbusTcpClient
            except Exception as e:  # noqa: BLE001
                raise ImportError('the Modbus adapter needs the optional extra: '
                                  'pip install "camber-toolkit[modbus]"') from e
            self._client = ModbusTcpClient(self._host, port=self._port)
            self._client.connect()
        return self._client

    def point_names(self) -> list[str]:
        return list(self._points)

    def units(self) -> dict:
        return {n: p.unit for n, p in self._points.items() if p.unit}

    def _read_one(self, client, p: ModbusPoint) -> float:
        reader = (client.read_input_registers if p.kind == "input"
                  else client.read_holding_registers)
        rr = reader(p.address, count=p.count, slave=p.slave)
        if rr is None or (hasattr(rr, "isError") and rr.isError()):
            return float("nan")
        return decode_registers(rr.registers, count=p.count, scale=p.scale, offset=p.offset)

    def read_snapshot(self, names=None) -> dict:
        """Read the current value of each named point (read-only). ``name -> value``."""
        client = self._require_client()
        sel = list(self._points) if names is None else [n for n in names if n in self._points]
        return {n: self._read_one(client, self._points[n]) for n in sel}

    def poll(self, names=None, *, samples: int = 1, interval_s: float = 60.0,
             _sleep=time.sleep, _clock=None) -> pd.DataFrame:
        """Repeatedly snapshot to build a short time series (``samples`` rows, ``interval_s``
        apart). ``_clock``/``_sleep`` are injectable for testing."""
        clock = _clock or (lambda: pd.Timestamp.now())
        rows, idx = [], []
        for i in range(max(1, samples)):
            idx.append(clock())
            rows.append(self.read_snapshot(names))
            if i < samples - 1:
                _sleep(interval_s)
        return pd.DataFrame(rows, index=pd.DatetimeIndex(idx)).sort_index()

    def load_points(self, names, resample=None) -> pd.DataFrame:
        """One-row snapshot at the current time (Modbus has no history — see module docs)."""
        ts = pd.Timestamp.now()
        return pd.DataFrame([self.read_snapshot(names)], index=pd.DatetimeIndex([ts]))
