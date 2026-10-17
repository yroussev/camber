"""Rule: chiller heat-exchanger fouling via approach-temperature degradation.

The condenser/evaporator *approach* — the gap between the refrigerant saturation temperature and the
water leaving that heat exchanger — widens as tubes foul or (on the evaporator side) as charge/flow
degrade. It is the classic refrigerant-side degradation indicator that needs no refrigerant-pressure
instrumentation beyond the approach temps a chiller controller already reports. Flags a sustained
approach above a design threshold. Thresholds are chiller-specific, so they are constructor
parameters.

**Running samples only (0.106, #184).** An approach means nothing while the compressor is off (the
two temperatures drift together toward the room, or the controller holds its last reading), so the
medians are read over the samples the chiller ran: its run status / command
(``compressor_status``, a duty above 0.5) when mapped, else its ``power`` above a tenth of its own
95th-percentile draw. With neither, every trended sample is read, with a caveat.
"""

from __future__ import annotations

import pandas as pd

from ..chwplant import chiller_running
from ..model.roles import Role
from .base import Finding

NO_GATE_CAVEAT = (
    "no chiller run status or power mapped: the medians read every trended approach sample, "
    "including any the controller reports while the compressor is off -- map compressor_status"
)
POWER_GATE_CAVEAT = (
    "no chiller run status mapped: running samples read from the chiller's power above a tenth "
    "of its own 95th-percentile draw -- map the status or command (compressor_status)"
)


def _run_mask(frame: pd.DataFrame):
    """``(running mask | None, source)``: status, else power, else ``(None, "none")``."""
    if Role.COMPRESSOR_STATUS in frame.columns:
        run = chiller_running(frame[Role.COMPRESSOR_STATUS], index=frame.index)
        if run is not None:
            return run, "status"
    if Role.POWER in frame.columns:
        from ..schedules import CHILLER_POWER_RUN_FRAC

        p = pd.to_numeric(frame[Role.POWER], errors="coerce")
        if p.notna().any():
            p95 = float(p.quantile(0.95))
            if p95 > 0:
                return (p > CHILLER_POWER_RUN_FRAC * p95).fillna(False), "power"
    return None, "none"


class ChillerApproachFouling:
    """Detects condenser/evaporator fouling from an elevated approach temperature."""

    name = "chiller_approach_fouling"
    roles_required = (Role.COND_APPROACH_TEMP,)
    roles_optional = (
        Role.EVAP_APPROACH_TEMP,
        Role.COMPRESSOR_STATUS,  # 0.106 (#184): the run gate
        Role.POWER,  # 0.106 (#184): the run gate's fallback
    )

    def __init__(self, cond_design_f: float = 5.0, evap_design_f: float = 4.0):
        self.cond_design_f = cond_design_f
        self.evap_design_f = evap_design_f

    def _leg(self, frame, role, design):
        if role not in frame.columns:
            return None
        s = frame[role].dropna()
        if s.empty:
            return None
        median = float(s.median())
        return {
            "median_f": round(median, 2),
            "design_f": design,
            "ratio": round(median / design, 2) if design else float("nan"),
        }

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        run, source = _run_mask(frame)
        caveats = (
            [NO_GATE_CAVEAT]
            if source == "none"
            else [POWER_GATE_CAVEAT]
            if source == "power"
            else []
        )
        if run is not None:
            if not bool(run.any()):
                return Finding(
                    rule=self.name,
                    equip=equip,
                    severity="info",
                    metrics={"run_source": source, "n_judged": 0},
                    summary=f"{equip}: did not run in the window (run {source} never on); "
                    "not judged",
                    caveats=caveats,
                )
            frame = frame[run.reindex(frame.index, fill_value=False)]
        cond = self._leg(frame, Role.COND_APPROACH_TEMP, self.cond_design_f)
        evap = self._leg(frame, Role.EVAP_APPROACH_TEMP, self.evap_design_f)
        if cond is None and evap is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"run_source": source, "n_judged": int(len(frame))},
                summary="insufficient data (need condenser/evaporator approach temp"
                + (" while running)" if run is not None else ")"),
                caveats=caveats,
            )
        worst = max((leg["ratio"] for leg in (cond, evap) if leg), default=0.0)
        if worst >= 2.0:
            severity = "fault"
        elif worst >= 1.5:
            severity = "warn"
        else:
            severity = "ok"
        metrics = {"worst_approach_ratio": worst}
        if cond:
            metrics.update(cond_approach_f=cond["median_f"], cond_design_f=cond["design_f"])
        if evap:
            metrics.update(evap_approach_f=evap["median_f"], evap_design_f=evap["design_f"])
        metrics.update(run_source=source, n_judged=int(len(frame)))
        legs = []
        if cond:
            legs.append(f"condenser {cond['median_f']:.1f}°F (design {cond['design_f']:.0f})")
        if evap:
            legs.append(f"evaporator {evap['median_f']:.1f}°F (design {evap['design_f']:.0f})")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=f"{equip}: approach temperature — {'; '.join(legs)} "
            "(fouling widens the approach)",
            caveats=caveats,
        )
