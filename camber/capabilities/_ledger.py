"""The facts a server issued to one session, so ``check_citations`` can tell where a cited fact
came from (0.112, #294).

A :class:`FactLedger` records every fact of every result a session was given (``Registry.call``
records into ``ctx.ledger``; ``camber mcp`` keeps one per process, which is one session). A
cited id then resolves to the fact the server itself issued, never to what a caller says the fact
was: an id this ledger never recorded (from another server, another config or store, an earlier
session, or made up) is unknown, and a caller-supplied fact must equal the recorded one.

Memory is bounded: the ledger keeps the most recently issued or cited facts up to ``max_bytes``
of their compact JSON (8 MB by default, some tens of thousands of facts) and forgets the oldest
beyond that. A forgotten id is still known to have been issued -- it resolves to ``FORGOTTEN``,
not to a fact -- for the ``max_forgotten`` most recently forgotten ids (100,000 by default);
older ones read as never issued. Thread-safe; it pickles (without its lock), so a small
:meth:`~FactLedger.view` of it can travel to a worker process with a call.

It also keeps the names its facts gave (0112-cite10, :meth:`~FactLedger.names`): every slug and
identifier of every fact it recorded, forgotten ones included, so ``check_citations`` can fail a
sentence that names a unit, dataset or rule no fact of the session named. A view carries them.

And it keeps which facts are of one set (0112-cite17, :meth:`~FactLedger.peers`): the findings of
one rule on one facility, the equipment or point coverage rows of one facility. A sentence that
ranks a cited fact among its set ("the widest band", "the only meter with ...") is read against
the set's other facts the session was given; a view carries the cited facts' peers.
"""

from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

__all__ = ["FactLedger", "ISSUED", "FORGOTTEN", "UNKNOWN"]

#: :meth:`FactLedger.lookup` outcomes
ISSUED, FORGOTTEN, UNKNOWN = "issued", "forgotten", "unknown"


def _safe(value: Any, depth: int = 0) -> Any:
    """``value`` as a JSON client sees it: NaN and infinities as ``None``, tuples as lists, keys
    as strings, anything else JSON cannot hold as its ``str``."""
    if depth > 30:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth + 1) for v in value]
    return str(value)


def canonical(fact: dict) -> dict:
    """``fact`` as it is recorded and compared: the JSON value a client receives."""
    return _safe(fact)


#: (0112-cite17) the fact kinds whose facts form a set a sentence may rank one of them in
_PEER_KINDS = frozenset({"finding", "equip_coverage", "point_coverage"})
#: the most peers a view or a check carries
_PEERS_MAX = 200


def peer_key(fact: dict) -> tuple | None:
    """``(kind, rule, facility)`` of the set ``fact`` is one of (the findings of one rule on one
    facility; one facility's equipment or point coverage rows), or None."""
    kind = fact.get("kind")
    if kind not in _PEER_KINDS:
        return None
    rule = str(fact.get("rule") or "")
    subj = fact.get("subject")
    fac = str(
        fact.get("facility_id") or (subj.get("facility_id") if isinstance(subj, dict) else "")
    )
    if not fac or (kind == "finding" and not rule):
        return None
    return (str(kind), rule, fac)


class FactLedger:
    """The facts issued to one session, by id, oldest forgotten first (see the module doc)."""

    def __init__(self, *, max_bytes: int = 8_000_000, max_forgotten: int = 100_000):
        if int(max_bytes) < 1 or int(max_forgotten) < 0:
            raise ValueError("max_bytes must be positive and max_forgotten not negative")
        self.max_bytes = int(max_bytes)
        self.max_forgotten = int(max_forgotten)
        self._facts: OrderedDict = OrderedDict()  # id -> compact JSON of the fact
        self._forgotten: OrderedDict = OrderedDict()  # id -> None
        self._bytes = 0
        self._names: set = set()  # 0112-cite10: the names its facts gave
        self._keys: dict = {}  # 0112-cite17: id -> the set it is one of (peer_key)
        self._lock = threading.Lock()

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_lock", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self.__dict__.setdefault("_names", set())
        self.__dict__.setdefault("_keys", {})
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._facts)

    def record(self, facts: Iterable) -> None:
        """Record each fact (a dict with a non-empty string ``id``; anything else is skipped).
        A fact recorded again with the same id replaces the earlier one and counts as new."""
        from ..agent.verify import _issued_names

        with self._lock:
            for f in facts or ():
                if isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"]:
                    fact = canonical(f)
                    text = json.dumps(
                        fact, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    )
                    self._put(f["id"], text)
                    self._names |= _issued_names([fact])
                    key = peer_key(fact)
                    if key is not None:
                        self._keys[f["id"]] = key

    def names(self) -> frozenset:
        """The names (lower case) the facts recorded here gave, forgotten ones included."""
        with self._lock:
            return frozenset(self._names)

    def peers(self, ids: Iterable[str]) -> list:
        """(0112-cite17) The ids of the held facts of the same set (:func:`peer_key`) as a held
        fact of ``ids``, not those themselves, oldest first (at most :data:`_PEERS_MAX`)."""
        ids = {i for i in ids if isinstance(i, str)}
        with self._lock:
            keys = {self._keys[i] for i in ids if i in self._keys and i in self._facts}
            if not keys:
                return []
            out = [
                i for i, k in self._keys.items() if k in keys and i not in ids and i in self._facts
            ]
        return out[:_PEERS_MAX]

    def ids(self) -> list:
        """The ids of the facts held, oldest first (0112-cite13: a bare short id's why names
        the full id the session issued last with that prefix)."""
        with self._lock:
            return list(self._facts)

    def _put(self, fid: str, text: str) -> None:
        old = self._facts.pop(fid, None)
        if old is not None:
            self._bytes -= len(old)
            self._keys.pop(fid, None)
        self._forgotten.pop(fid, None)
        self._facts[fid] = text
        self._bytes += len(text)
        while self._bytes > self.max_bytes and len(self._facts) > 1:
            gone, was = self._facts.popitem(last=False)
            self._bytes -= len(was)
            self._keys.pop(gone, None)
            self._forget(gone)

    def _forget(self, fid: str) -> None:
        if self.max_forgotten:
            self._forgotten[fid] = None
            while len(self._forgotten) > self.max_forgotten:
                self._forgotten.popitem(last=False)

    def lookup(self, fid: str) -> tuple[str, dict | None]:
        """``(ISSUED, fact)``, ``(FORGOTTEN, None)`` or ``(UNKNOWN, None)`` for one id."""
        with self._lock:
            text = self._facts.get(fid)
            if text is not None:
                return ISSUED, json.loads(text)
            return (FORGOTTEN if fid in self._forgotten else UNKNOWN), None

    def view(self, ids: Iterable[str]) -> FactLedger:
        """A new ledger with only ``ids`` (each issued fact, or the knowledge that it was
        forgotten): what one call needs, small enough to send to a worker process. Each issued
        id it holds counts as recently used here, so a fact that is cited stays longest."""
        out = FactLedger(max_bytes=self.max_bytes, max_forgotten=self.max_forgotten)
        with self._lock:
            out._names = set(self._names)
            for fid in dict.fromkeys(i for i in ids if isinstance(i, str)):
                text = self._facts.get(fid)
                if text is not None:
                    self._facts.move_to_end(fid)
                    out._facts[fid] = text
                    out._bytes += len(text)
                    if fid in self._keys:
                        out._keys[fid] = self._keys[fid]
                elif fid in self._forgotten:
                    out._forgotten[fid] = None
        return out
