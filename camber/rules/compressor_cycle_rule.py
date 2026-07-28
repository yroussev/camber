"""Rule: DX compressor short-cycling (starts per day).

A packaged-unit / heat-pump compressor firing in short bursts wastes start-up inrush, stresses the
compressor, and lowers seasonal efficiency — usually an oversized stage or too-tight staging
hysteresis. Reuses the generic on/off start counter
(:func:`camber.boilercycle.analyze_boiler_cycling`)
over the compressor status trend. ``max_starts_per_day`` is manufacturer / min-off-time
dependent, so
it is a constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..boilercycle import analyze_boiler_cycling
from ..model.roles import Role
from .base import Finding


class CompressorShortCycle:
    """Detects a DX compressor short-cycling (excess starts per day)."""

    name = "compressor_short_cycle"
    roles_required = (Role.COMPRESSOR_STATUS,)
    roles_optional = ()

    def __init__(self, max_starts_per_day: float = 12.0):
        # DX min-off-time is typically ~5 min -> ~12 starts/day is a generous ceiling.
        self.max_starts_per_day = max_starts_per_day

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.COMPRESSOR_STATUS not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need compressor status)",
            )
        legacy = frame.rename(columns={Role.COMPRESSOR_STATUS: "BoilerStatus"})
        res = analyze_boiler_cycling(legacy, equip)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need compressor status)",
            )
        cyc = res.starts_per_day
        if cyc >= 2 * self.max_starts_per_day:
            severity = "fault"
        elif cyc >= self.max_starts_per_day:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "starts_per_day": res.starts_per_day,
                "max_starts_per_day": self.max_starts_per_day,
                "runtime_pct": res.runtime_pct,
                "n_starts": res.n_starts,
                "n_days": res.n_days,
            },
            summary=(
                f"{equip}: {res.starts_per_day:.1f} compressor starts/day "
                f"(threshold {self.max_starts_per_day:.0f}), running "
                f"{res.runtime_pct:.0f}% of the time"
            ),
        )
