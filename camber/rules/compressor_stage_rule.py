"""Rule: DX compressor staging instability (stage changes per day).

Multi-stage DX that ramps stages up and down excessively (rather than holding a stage) wastes
efficiency and stresses compressors — the DX analogue of chiller-staging hunting. Counts stage
*changes* per day from the compressor-stage trend. ``max_changes_per_day`` is
staging-hysteresis dependent, so it is a constructor parameter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from .base import Finding


def _changes_per_day(series: pd.Series) -> tuple:
    """(#level changes, changes/day, span_days) for a stepwise series over its datetime index."""
    s = series.dropna()
    if len(s) < 2:
        return 0, 0.0, 0.0
    changes = int((s.round().diff().fillna(0) != 0).sum())
    span_days = max((s.index[-1] - s.index[0]).total_seconds() / 86400.0, 1e-9)
    return changes, changes / span_days, span_days


class CompressorStaging:
    """Detects unstable DX compressor staging (excess stage changes per day)."""

    name = "compressor_staging"
    roles_required = (Role.COMPRESSOR_STAGE,)
    roles_optional = ()

    def __init__(self, max_changes_per_day: float = 24.0):
        self.max_changes_per_day = max_changes_per_day

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.COMPRESSOR_STAGE not in frame.columns:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need compressor stage)")
        n_changes, per_day, span = _changes_per_day(frame[Role.COMPRESSOR_STAGE])
        if span <= 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need compressor stage)")
        max_stage = float(np.nanmax(frame[Role.COMPRESSOR_STAGE].to_numpy(dtype=float)))
        if per_day >= 2 * self.max_changes_per_day:
            severity = "fault"
        elif per_day >= self.max_changes_per_day:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name, equip=equip, severity=severity,
            metrics={"stage_changes_per_day": round(per_day, 2),
                     "max_changes_per_day": self.max_changes_per_day,
                     "n_changes": n_changes, "max_stage": max_stage, "n_days": round(span, 2)},
            summary=(f"{equip}: {per_day:.1f} compressor-stage changes/day "
                     f"(threshold {self.max_changes_per_day:.0f}), top stage {max_stage:.0f}"),
        )
