"""Rule: unmet setpoint (comfort / capacity) hours.

The most operator-facing FDD metric: how often is the space **outside its setpoint band** when it
should be comfortable? Sustained unmet hours mean a comfort complaint and often a capacity, airflow,
or control problem upstream. This counts occupied intervals where the space temperature runs above
the cooling setpoint (too hot) or below the heating setpoint (too cold), beyond a tolerance.

Occupancy comes from the OCCUPANCY role when present, else a default occupied-hours window. numpy/
pandas; a synthetic fixture proves it.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..resolve import occupied
from .base import Finding


class UnmetHours:
    """Flags excessive occupied hours with space temperature outside the setpoint band."""

    name = "unmet_setpoint_hours"
    roles_required = (Role.SPACE_TEMP,)
    roles_optional = (Role.HEAT_SP, Role.COOL_SP, Role.OCCUPANCY)

    def __init__(
        self,
        *,
        tol_F: float = 1.5,
        warn_pct: float = 5.0,
        fault_pct: float = 15.0,
        start_hour: int = 7,
        end_hour: int = 18,
    ):
        self.tol_F = tol_F
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.start_hour = start_hour
        self.end_hour = end_hour

    def _occupied_mask(self, frame):
        if Role.OCCUPANCY in frame.columns:
            return frame[Role.OCCUPANCY].fillna(0) > 0
        return occupied(frame, start_hour=self.start_hour, end_hour=self.end_hour)

    def _unmet_masks(self, frame):
        st = frame[Role.SPACE_TEMP]
        false = pd.Series(False, index=frame.index)
        too_hot = (
            (st > frame[Role.COOL_SP] + self.tol_F) if Role.COOL_SP in frame.columns else false
        )
        too_cold = (
            (st < frame[Role.HEAT_SP] - self.tol_F) if Role.HEAT_SP in frame.columns else false
        )
        return too_hot.fillna(False), too_cold.fillna(False)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on occupied unmet hours."""
        if Role.COOL_SP not in frame.columns and Role.HEAT_SP not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no heating/cooling setpoint present",
            )
        occ = self._occupied_mask(frame) & frame[Role.SPACE_TEMP].notna()
        n = int(occ.sum())
        if n == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no occupied space-temp data",
            )
        # Each direction needs its own setpoint. A missing side is NOT evaluated -- its
        # metric is None (never a confident "0%"), and a clean one-sided result is not a
        # confident "met": decline ok -> info and caveat the untested direction. The
        # union (unmet_pct) still stands on whatever side(s) we could evaluate.
        have_cool = Role.COOL_SP in frame.columns
        have_heat = Role.HEAT_SP in frame.columns
        too_hot, too_cold = self._unmet_masks(frame)
        hot_pct = 100.0 * float((too_hot & occ).sum()) / n if have_cool else None
        cold_pct = 100.0 * float((too_cold & occ).sum()) / n if have_heat else None
        unmet_pct = 100.0 * float(((too_hot | too_cold) & occ).sum()) / n
        sev = (
            "fault"
            if unmet_pct >= self.fault_pct
            else "warn"
            if unmet_pct >= self.warn_pct
            else "ok"
        )
        caveats = []
        if not have_heat:
            caveats.append("no heating setpoint: too-cold not evaluated")
        if not have_cool:
            caveats.append("no cooling setpoint: too-hot not evaluated")
        if sev == "ok" and not (have_heat and have_cool):
            sev = "info"  # one-sided clean result is not a confident "met"
        hot_txt = f"too hot {hot_pct:.0f}%" if hot_pct is not None else "too hot n/a"
        cold_txt = f"too cold {cold_pct:.0f}%" if cold_pct is not None else "too cold n/a"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "unmet_pct": round(unmet_pct, 2),
                "too_hot_pct": round(hot_pct, 2) if hot_pct is not None else None,
                "too_cold_pct": round(cold_pct, 2) if cold_pct is not None else None,
                "n_occupied": n,
                "tol_F": self.tol_F,
            },
            summary=(f"{equip}: unmet {unmet_pct:.0f}% of occupied hours ({hot_txt}, {cold_txt})"),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: space temp with its setpoint(s), unmet spans shaded."""
        from ..charts.evidence import Evidence

        roles = [Role.SPACE_TEMP] + [r for r in (Role.COOL_SP, Role.HEAT_SP) if r in frame.columns]
        if len(roles) < 2:
            return None
        too_hot, too_cold = self._unmet_masks(frame)
        # restrict to occupied hours -- the same gate the finding's count uses, so the shaded
        # evidence matches the reported unmet %
        mask = (too_hot | too_cold) & self._occupied_mask(frame)
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=mask,
            label="unmet (occupied)",
            title=f"{equip}: unmet setpoint",
        )
