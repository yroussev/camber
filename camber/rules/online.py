"""Online FDD — sliding-window rule evaluation over a streaming role-frame.

The batch runner (`rules.base.Registry.run`) scores a finished frame. For a live BAS feed you want
the same rules re-evaluated as data arrives, over a bounded trailing window, emitting a finding
only when a rule's verdict *changes* (so a sustained fault doesn't re-alert every sample). This is
the streaming companion: push samples in, and get transition events out.

Any object exposing ``name`` / ``roles_required`` / ``analyze(equip, frame)`` works as a rule
(the same duck-typed protocol as the batch registry). Dependency-light: a bounded deque + pandas.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import pandas as pd

_ACTIONABLE = frozenset({"warn", "fault"})


@dataclass
class Transition:
    """A change in a rule's verdict for one equipment on a window evaluation."""

    equip: str
    rule: str
    from_severity: str | None    # None = first verdict for this (equip, rule)
    to_severity: str
    finding: object              # the Finding at the new state
    at: object                   # the window's last timestamp


@dataclass
class OnlineFDD:
    """Maintains a trailing per-equipment role-frame window and re-runs rules on advance.

    Feed samples with :meth:`push` (one role-named row) or :meth:`extend` (a frame). Every
    ``eval_every`` new samples — or on an explicit :meth:`evaluate` — each rule whose required
    roles are present is run over the current window; a :class:`Transition` is returned only when a
    rule's severity differs from its last emitted value for that equipment.
    """

    rules: list
    window: int = 240                       # trailing samples retained per equipment
    eval_every: int = 1                     # evaluate after this many pushes
    min_samples: int = 12                   # don't evaluate a window smaller than this
    emit_ok: bool = False                   # also emit transitions back to "ok"/"info"
    _buffers: dict = field(default_factory=dict)   # equip -> deque[(ts, {role: value})]
    _last: dict = field(default_factory=dict)      # (equip, rule) -> last emitted severity
    _since_eval: dict = field(default_factory=dict)

    def _buf(self, equip: str) -> deque:
        if equip not in self._buffers:
            self._buffers[equip] = deque(maxlen=self.window)
            self._since_eval[equip] = 0
        return self._buffers[equip]

    def push(self, equip: str, row: dict, *, ts=None) -> list:
        """Add one sample (``{Role|str: value}``) for ``equip``; evaluate if due. Returns
        transitions (possibly empty)."""
        self._buf(equip).append((ts if ts is not None else pd.Timestamp.now(), dict(row)))
        self._since_eval[equip] += 1
        if self._since_eval[equip] >= self.eval_every:
            self._since_eval[equip] = 0
            return self.evaluate(equip)
        return []

    def extend(self, equip: str, frame: pd.DataFrame) -> list:
        """Feed a whole role-frame for ``equip`` (rows applied in order). Returns all transitions
        emitted across the implied evaluations."""
        out = []
        for ts, row in zip(frame.index, frame.to_dict("records")):
            out.extend(self.push(equip, row, ts=ts))
        return out

    def window_frame(self, equip: str) -> pd.DataFrame:
        """The current trailing window for ``equip`` as a role-named frame."""
        buf = self._buffers.get(equip)
        if not buf:
            return pd.DataFrame()
        idx = [ts for ts, _ in buf]
        return pd.DataFrame([r for _, r in buf], index=pd.DatetimeIndex(idx)).sort_index()

    def evaluate(self, equip: str) -> list:
        """Run all applicable rules over ``equip``'s window; return verdict-change transitions."""
        frame = self.window_frame(equip)
        if len(frame) < self.min_samples:
            return []
        out = []
        for rule in self.rules:
            required = tuple(getattr(rule, "roles_required", ()))
            if any(r not in frame.columns for r in required):
                continue
            finding = rule.analyze(equip, frame)
            if finding is None:
                continue
            sev = getattr(finding, "severity", "info")
            key = (equip, getattr(rule, "name", repr(rule)))
            prev = self._last.get(key)
            if sev == prev:
                continue                                  # no change -> no re-alert
            worsened = sev in _ACTIONABLE
            recovered = prev in _ACTIONABLE and sev not in _ACTIONABLE
            if worsened or (recovered and self.emit_ok) or (prev is None and self.emit_ok):
                out.append(Transition(equip=equip, rule=key[1], from_severity=prev,
                                      to_severity=sev, finding=finding, at=frame.index[-1]))
            self._last[key] = sev
        return out

    def state(self) -> dict:
        """Current emitted severity per (equip, rule)."""
        return dict(self._last)
