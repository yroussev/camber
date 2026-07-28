"""Rule: economizer not locked out above the high limit.

Above the outdoor-air high limit, bringing in more outdoor air only adds cooling load. The
economizer should hold minimum OA; a damper still modulated open in hot weather is a stuck/mis-tuned
economizer wasting cooling energy. Counts hours where OAT exceeds the high limit yet the OA
damper is above minimum. numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding


class EconomizerHighLimit:
    """Flags an OA damper held open above the economizer high limit (no lockout)."""

    name = "economizer_high_limit"
    roles_required = (Role.OA_DAMPER, Role.OAT)
    roles_optional = ()

    def __init__(
        self,
        *,
        high_limit_f: float = 65.0,
        min_damper: float = 0.25,
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
    ):
        self.high_limit_f = high_limit_f
        self.min_damper = min_damper
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat, damper = frame[Role.OAT], frame[Role.OA_DAMPER]
        hot = (oat > self.high_limit_f) & oat.notna() & damper.notna()
        n_hot = int(hot.sum())
        if n_hot == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no hours above the {self.high_limit_f:g}°F high limit",
            )
        open_when_hot = hot & (damper > self.min_damper + 0.05)
        pct = 100.0 * float(open_when_hot.sum()) / n_hot
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "not_locked_out_pct": round(pct, 2),
                "high_limit_f": self.high_limit_f,
                "min_damper": self.min_damper,
                "n_above_limit": n_hot,
            },
            summary=(
                f"{equip}: OA damper open above the {self.high_limit_f:g}°F high limit "
                f"{pct:.0f}% of those hours (economizer not locked out)"
            ),
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: OA damper vs OAT against the economizer expectation."""
        from ..charts.diagnostic import TEMPLATES
        from ..charts.evidence import Evidence

        return Evidence(
            renderer="diagnostic",
            template=TEMPLATES["economizer"],
            title=f"{equip}: economizer high-limit",
        )
