"""Rule: dirty air filter (high / rising filter differential pressure).

A loaded filter raises the pressure the supply fan must overcome, wasting fan energy and starving
airflow. Flags a filter whose differential pressure sits above the change-out threshold. The alarm
setpoint is filter/fan dependent, so ``change_dp_inwc`` is a constructor parameter (a common MERV-13
final-DP alarm is ~1.0 inH2O); the fault level is ``fault_factor`` times it.

**Fan-gated by default (0.106, #181).** With the fan stopped there is no flow through the filter
and its transmitter reads about zero, so only fan-on samples are judged (fan status, else fan
speed, else airflow -- see :func:`camber.schedules.fan_on_mask`). Judged over every sample, a unit
that runs under half the day could never warn however dirty its filter, and at two-thirds runtime
the median is about the lowest quarter of the running samples. A unit that trends no fan signal is
judged ungated and the finding says so (``fan_gate`` metric and a caveat).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, fan_on_mask
from .base import Finding


class FilterFouling:
    """Detects a fouled air filter (differential pressure at/above the change-out threshold)."""

    name = "filter_fouling"
    roles_required = (Role.FILTER_DIFF_PRESS,)
    roles_optional = (
        # fan-on gate: status, else speed, else airflow (see camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
    )

    def __init__(
        self, change_dp_inwc: float = 1.0, *, fault_factor: float = 1.5, fan_gate: bool = True
    ):
        if fault_factor < 1.0:
            raise ValueError("fault_factor must be at least 1 (fault at or above warn)")
        self.change_dp_inwc = change_dp_inwc
        self.fault_factor = fault_factor
        self.fan_gate = fan_gate

    def _gate(self, frame: pd.DataFrame):
        """``(fan-on mask | None, label)``; ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    @staticmethod
    def _missing(fan_src: str) -> list:
        # the three fan signals are alternatives: name one only when none is present
        return [Role.SUPPLY_FAN_STATUS.value] if fan_src == FAN_GATE_NONE else []

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.FILTER_DIFF_PRESS not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need filter differential pressure)",
            )
        gate, fan_src = self._gate(frame)
        raw = frame[Role.FILTER_DIFF_PRESS]
        n_all = int(raw.notna().sum())
        if gate is not None:
            keep = gate.reindex(raw.index).fillna(False).astype(bool)
            raw = raw[keep]
        dp = raw.dropna()
        gate_metrics = {
            "fan_gate": fan_src,
            "n_judged": int(dp.size),
            "n_masked_fan_off": n_all - int(dp.size) if gate is not None else 0,
            "_missing_optional": self._missing(fan_src),
        }
        if dp.empty:
            ran = gate is None or n_all == 0
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=gate_metrics,
                summary=(
                    "insufficient data (need filter differential pressure)"
                    if ran
                    else f"{equip}: filter not judged (the fan never ran with a filter reading)"
                ),
            )
        median = float(dp.median())
        thr = self.change_dp_inwc
        fault_dp = self.fault_factor * thr
        over_pct = float((dp > thr).mean() * 100.0)
        if median >= fault_dp:
            severity = "fault"
        elif median >= thr:
            severity = "warn"
        else:
            severity = "ok"
        caveats = []
        if fan_src == FAN_GATE_NONE:
            caveats.append(
                "not fan-gated (no fan signal trended): fan-off readings, about zero with no flow "
                "through the filter, pull the median down -- map SUPPLY_FAN_STATUS"
            )
        basis = "fan-on samples" if gate is not None else "samples"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "filter_dp_median_inwc": round(median, 3),
                "change_dp_inwc": thr,
                "fault_dp_inwc": round(fault_dp, 3),
                "pct_over_threshold": round(over_pct, 1),
                "filter_dp_p95_inwc": round(float(np.nanpercentile(dp, 95)), 3),
                **gate_metrics,
            },
            summary=(
                f"{equip}: filter ΔP median {median:.2f} inH2O "
                f"(change-out {thr:.2f}, fault {fault_dp:.2f}); "
                f"{over_pct:.0f}% of {basis} above change-out"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J (0.111, #298): the filter ΔP with the judged samples above change-out (the
        samples behind ``pct_over_threshold``) shaded."""
        from ..charts.evidence import Evidence

        if Role.FILTER_DIFF_PRESS not in frame.columns:
            return None
        dp = pd.to_numeric(frame[Role.FILTER_DIFF_PRESS], errors="coerce")
        over = (dp > self.change_dp_inwc).fillna(False)
        judged = dp.notna()  # 0.111 (#310): n_judged, pct_over_threshold's denominator
        gate, _src = self._gate(frame)
        if gate is not None:
            on = gate.reindex(frame.index).fillna(False).astype(bool)
            over &= on
            judged &= on
        return Evidence(
            renderer="multitrend",
            roles=[Role.FILTER_DIFF_PRESS],
            mask=over,
            judged=judged,
            judged_basis="fan-on samples" if gate is not None else "samples with a reading",
            label="above change-out",
            title=f"{equip}: filter ΔP",
        )
