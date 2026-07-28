"""Rule: control hunting / oscillation on a modulating output.

A well-tuned loop settles; a badly-tuned one **hunts** — the valve or damper reverses direction
again and again, never holding. Hunting wastes actuator life, upsets the controlled variable, and
often drives simultaneous heating/cooling downstream. This detects it directly from a modulating
output by counting *direction reversals per hour* beyond a deadband (so slow, legitimate modulation
doesn't trip it). Works on any present modulating role. numpy/pandas; a synthetic fixture proves it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from .base import Finding

_MODULATING = (Role.HEAT_VALVE, Role.COOL_VALVE, Role.DAMPER, Role.OA_DAMPER)


def reversals_per_hour(signal, deadband: float):
    """Direction reversals per hour of a modulating signal, ignoring moves within ``deadband``.

    Returns ``(rate_per_hour, n_reversals)``. A reversal is a sign change between consecutive
    significant (> deadband) increments — the signature of a loop that can't hold a position.
    """
    s = pd.Series(signal).dropna()
    if len(s) < 3:
        return 0.0, 0
    d = s.diff().dropna()
    sig = d[d.abs() > deadband]
    if len(sig) < 2:
        return 0.0, 0
    signs = np.sign(sig.to_numpy())
    reversals = int(np.sum(signs[1:] != signs[:-1]))
    idx = pd.DatetimeIndex(s.index)
    # span from min/max, not endpoints -> robust to unsorted or duplicate (DST fall-back) timestamps
    hours = (idx.max() - idx.min()).total_seconds() / 3600.0
    return (reversals / hours if hours > 0 else 0.0), reversals


class ControlHunting:
    """Flags a modulating output that reverses direction excessively (unstable/hunting loop)."""

    name = "control_hunting"
    roles_required = ()
    roles_optional = _MODULATING

    def __init__(
        self, *, warn_per_hr: float = 6.0, fault_per_hr: float = 12.0, deadband: float = 0.05
    ):
        self.warn_per_hr = warn_per_hr
        self.fault_per_hr = fault_per_hr
        self.deadband = deadband

    def _signals(self, frame):
        return [r for r in _MODULATING if r in frame.columns]

    def _worst(self, frame):
        worst_role, worst_rate, worst_rev, rates = None, 0.0, 0, {}
        for role in self._signals(frame):
            rate, rev = reversals_per_hour(frame[role], self.deadband)
            rates[getattr(role, "value", str(role))] = round(rate, 2)
            if rate > worst_rate:
                worst_role, worst_rate, worst_rev = role, rate, rev
        return worst_role, worst_rate, worst_rev, rates

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on the worst-hunting output."""
        if not self._signals(frame):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no modulating output present",
            )
        role, rate, rev, rates = self._worst(frame)
        sev = "fault" if rate >= self.fault_per_hr else "warn" if rate >= self.warn_per_hr else "ok"
        rn = getattr(role, "value", str(role)) if role is not None else ""
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "worst_signal": rn,
                "reversals_per_hr": round(rate, 2),
                "reversals": rev,
                "per_signal_rate": rates,
                "deadband": self.deadband,
            },
            summary=(
                f"{equip}: {rn} reverses {rate:.1f}x/hr (hunting)"
                if sev != "ok"
                else f"{equip}: modulating outputs stable ({rate:.1f}x/hr)"
            ),
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the worst-hunting signal's trend (the oscillation is the evidence)."""
        from ..charts.evidence import Evidence

        role = self._worst(frame)[0]
        if role is None:
            return None
        return Evidence(
            renderer="multitrend",
            roles=[role],
            title=f"{equip}: {getattr(role, 'value', role)} hunting",
        )
