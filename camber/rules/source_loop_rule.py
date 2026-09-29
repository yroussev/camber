"""Rule: heat-pump **source-loop delta-T** -- a loop pumped without carrying heat (0.93; #40).

A water-source or ground-source heat-pump loop moves heat between the heat pumps and the ground
(or a boiler and a tower). Its temperature difference -- water back from the heat pumps against
water sent to them -- is the heat it carries per gallon. A loop that runs its pumps hour after hour
with next to no difference is moving water, not heat: the pumps are overpumping (constant speed,
no differential-pressure reset, heat-pump isolation valves that stay open when the compressors
stop), which wastes pump energy and, on a ground loop, flattens the loop's usable temperature.
Design guidance for closed-loop heat-pump systems commonly sizes for a loop difference of about
8-12 degF at design flow (roughly 3 gpm per ton; a published ASHRAE / IGSHPA design practice, not
a standard requirement), so a loop whose 90th-percentile difference never reaches half of that is
over-circulating.

Judged over the samples with the loop pumps running (``pump_status``, else a loop pump speed above
``min_pump_speed_pct``; with neither, every sample, with a caveat). The sign of the difference
follows the season (heat rejected in cooling, extracted in heating), so the rule reads its
magnitude. Not the chilled-water ``loop_deltat_drift`` detector: that one needs a frozen baseline
and a flow normalizer; this one is a level check for loops trended with temperatures only.
Thresholds are screening-grade (provisional, 0.93).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from .base import Finding

__all__ = ["SourceLoopDeltaT"]


class SourceLoopDeltaT:
    """A heat-pump source loop running its pumps with almost no temperature difference."""

    name = "source_loop_deltat"
    roles_required = (Role.SOURCE_LOOP_SUPPLY_TEMP, Role.SOURCE_LOOP_RETURN_TEMP)
    roles_optional = (
        Role.PUMP_STATUS,
        Role.SOURCE_LOOP_PUMP_SPEED,
        Role.SOURCE_LOOP_DIFF_PRESS,
        Role.OAT,
    )

    def __init__(
        self,
        *,
        design_deltat_f: float = 10.0,
        flat_deltat_f: float = 1.0,
        warn_flat_pct: float = 50.0,
        fault_flat_pct: float = 80.0,
        min_pump_speed_pct: float = 5.0,
        min_hours: float = 24.0,
    ):
        self.design_deltat_f = design_deltat_f
        self.flat_deltat_f = flat_deltat_f
        self.warn_flat_pct = warn_flat_pct
        self.fault_flat_pct = fault_flat_pct
        self.min_pump_speed_pct = min_pump_speed_pct
        self.min_hours = min_hours

    def _running(self, frame: pd.DataFrame, caveats: list) -> pd.Series:
        if Role.PUMP_STATUS in frame.columns:
            return pd.to_numeric(frame[Role.PUMP_STATUS], errors="coerce") > 0.5
        if Role.SOURCE_LOOP_PUMP_SPEED in frame.columns:
            return (
                pd.to_numeric(frame[Role.SOURCE_LOOP_PUMP_SPEED], errors="coerce")
                > self.min_pump_speed_pct
            )
        caveats.append("no loop pump status or speed: every sample is treated as pumping")
        return pd.Series(True, index=frame.index)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """The loop's |delta-T| while pumping: its spread, and the share of near-zero samples."""
        caveats: list = []
        sup = pd.to_numeric(frame[Role.SOURCE_LOOP_SUPPLY_TEMP], errors="coerce")
        ret = pd.to_numeric(frame[Role.SOURCE_LOOP_RETURN_TEMP], errors="coerce")
        run = self._running(frame, caveats).fillna(False)
        dt = (ret - sup).abs()[run & sup.notna() & ret.notna()]
        step = pd.Series(frame.index).diff().median() if len(frame) > 1 else pd.Timedelta(hours=1)
        h = float(step / pd.Timedelta(hours=1)) if pd.notna(step) else 1.0
        if len(dt) * h < self.min_hours:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "too_few_pumping_hours"},
                summary=f"{equip}: declined -- only {len(dt) * h:.0f} pumping hours with both loop "
                "temperatures",
                caveats=caveats + ["could not evaluate the loop delta-T: too few pumping hours"],
            )
        flat = float((dt < self.flat_deltat_f).mean() * 100.0)
        p90 = float(np.percentile(dt, 90))
        med = float(dt.median())
        run_share = float(run.mean() * 100.0)
        signed = (ret - sup)[dt.index]
        if flat >= self.fault_flat_pct and p90 < 0.25 * self.design_deltat_f:
            severity = "fault"
        elif flat >= self.warn_flat_pct and p90 < 0.5 * self.design_deltat_f:
            severity = "warn"
        else:
            severity = "ok"
        metrics: dict = {
            "deltat_median_f": round(med, 2),
            "deltat_p90_f": round(p90, 2),
            "flat_pct": round(flat, 1),
            "pumping_pct_of_time": round(run_share, 1),
            "pumping_hours": round(len(dt) * h, 1),
            "design_deltat_f": self.design_deltat_f,
            "return_warmer_pct": round(float((signed > self.flat_deltat_f).mean() * 100.0), 1),
            "return_colder_pct": round(float((signed < -self.flat_deltat_f).mean() * 100.0), 1),
        }
        if Role.SOURCE_LOOP_DIFF_PRESS in frame.columns:
            dp = pd.to_numeric(frame[Role.SOURCE_LOOP_DIFF_PRESS], errors="coerce")[dt.index]
            metrics["diff_press_median"] = (
                None if dp.dropna().empty else round(float(dp.median()), 2)
            )
        if severity == "ok":
            summary = (
                f"{equip}: loop delta-T {med:.1f}°F median, {p90:.1f}°F at the 90th percentile "
                "while pumping"
            )
        else:
            summary = (
                f"{equip}: the loop pumps {run_share:.0f}% of the time but carries almost no heat "
                f"-- |delta-T| under {self.flat_deltat_f:g}°F {flat:.0f}% of pumping time, "
                f"{p90:.1f}°F at the 90th percentile against ~{self.design_deltat_f:g}°F design: "
                "overpumping (pump speed / DP reset, heat-pump isolation valves)"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)
