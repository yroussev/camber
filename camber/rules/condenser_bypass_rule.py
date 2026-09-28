"""Rule: condenser-water **tower-bypass valve leak** (0.92, #15).

Many plants have a bypass valve (often three-way) that sends condenser water around the cooling
towers to keep the water entering the chillers from getting too cold. With the valve commanded
**shut**, all the water goes through the towers, so the water entering the condensers
(:attr:`Role.COND_ENTERING_WATER_TEMP`) equals the water leaving the towers
(:attr:`Role.CW_SUPPLY_TEMP`) to within sensor accuracy. A valve that leaks, or sticks part-open,
mixes warm condenser return back in: the chillers see warmer water than the towers make, lift and
kW/ton rise, and the tower controls cannot see it -- they hold their own leaving water at setpoint.
The same shape as :class:`~camber.rules.leakvalve_rule.LeakingValve` (a temperature change across
a valve commanded shut), on the condenser loop.

The rule judges only samples with the bypass commanded shut (at or below ``closed_pct``) while a
chiller runs (its status, else its power; :func:`camber.schedules.plant_run_mask`). With the
condenser return temperature mapped it also estimates the **bypassed fraction**:
``(entering - tower leaving) / (return - tower leaving)`` -- the share of the condenser flow that
went around the towers. It also separates a leak from a **sensor offset**, which the temperatures
alone cannot: mixed-in return water raises the entering water in proportion to the condenser range
(``difference = fraction x range``), while a miscalibrated sensor adds the same offset whatever the
range. A difference that does not grow with the range (slope under 0.05, intercept carrying at
least half of it) is reported as an offset (``info``, ``attribution="sensor_offset"``), as is
entering water reading *colder* than the tower's leaving water, which mixing cannot produce.
Without the return temperature the rule cannot tell the two apart and says so.

Thresholds (screening-grade): the median difference over the shut samples must reach
``warn_f`` (2 F -- twice the combined +/-0.5 F accuracy of two plant sensors) for a warn and
``fault_f`` (5 F, roughly a 5-8 % rise in chiller kW/ton) for a fault.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

CLOSED_PCT = 2.0  # a bypass command at or below this is "shut"
BYPASS_WARN_F = 2.0  # screening-grade
BYPASS_FAULT_F = 5.0  # screening-grade
_MIN_SAMPLES = 24
_MIN_RANGE_SPAN_F = 2.0  # the condenser range must move this much to regress on it
_OFFSET_MAX_SLOPE = 0.05  # a difference growing slower than this with the range is an offset


class CondenserBypassLeak:
    """Detects condenser water bypassing the towers while the bypass valve is commanded shut."""

    name = "condenser_bypass_leak"
    roles_required = (Role.CW_BYPASS_VALVE, Role.CW_SUPPLY_TEMP, Role.COND_ENTERING_WATER_TEMP)
    roles_optional = (Role.CW_RETURN_TEMP, Role.COMPRESSOR_STATUS, Role.POWER, Role.CHW_SUPPLY_TEMP)

    def __init__(
        self,
        *,
        closed_pct: float = CLOSED_PCT,
        warn_f: float = BYPASS_WARN_F,  # screening-grade
        fault_f: float = BYPASS_FAULT_F,  # screening-grade
        min_samples: int = _MIN_SAMPLES,
    ):
        self.closed_pct = closed_pct
        self.warn_f = warn_f
        self.fault_f = fault_f
        self.min_samples = min_samples

    def _shut_running(self, frame: pd.DataFrame):
        """``(mask, run_source)``: bypass commanded shut while a chiller runs (both temps valid)."""
        from ..schedules import PLANT_GATE_NONE, plant_run_mask

        valve = normalize_percent(pd.to_numeric(frame[Role.CW_BYPASS_VALVE], errors="coerce"))
        mask = valve <= self.closed_pct
        run, src = plant_run_mask(frame, "chw")
        if run is not None:
            mask &= run.reindex(frame.index).fillna(False).astype(bool)
        else:
            src = PLANT_GATE_NONE
        for r in (Role.CW_SUPPLY_TEMP, Role.COND_ENTERING_WATER_TEMP):
            mask &= pd.to_numeric(frame[r], errors="coerce").notna()
        return mask.fillna(False).astype(bool), src

    def _diff(self, frame: pd.DataFrame) -> pd.Series:
        num = lambda r: pd.to_numeric(frame[r], errors="coerce")  # noqa: E731
        return num(Role.COND_ENTERING_WATER_TEMP) - num(Role.CW_SUPPLY_TEMP)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over a chiller-plant / condenser-loop role-frame; return a Finding."""
        mask, src = self._shut_running(frame)
        n = int(mask.sum())
        base = {"n_shut": n, "run_source": src, "closed_pct": self.closed_pct}
        caveats: list = []
        if src.startswith("ungated"):
            caveats.append(
                "no chiller run status or power mapped: shut-bypass hours include hours with no "
                "condenser flow, when the two temperatures need not agree"
            )
        if n < self.min_samples:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={**base, "median_diff_f": None},
                summary=(
                    f"{equip}: bypass valve not judged ({n} samples commanded shut with a chiller "
                    f"running; needs {self.min_samples})"
                ),
                caveats=caveats,
            )
        d = self._diff(frame)[mask]
        med = float(d.median())
        warm_share = float((d >= self.warn_f).mean())
        frac = slope = icpt = None
        if Role.CW_RETURN_TEMP in frame.columns:
            rng = (
                pd.to_numeric(frame[Role.CW_RETURN_TEMP], errors="coerce")
                - pd.to_numeric(frame[Role.CW_SUPPLY_TEMP], errors="coerce")
            )[mask]
            ok = (rng > 1.0) & rng.notna()  # the range must be real to divide by it
            if int(ok.sum()) >= self.min_samples:
                frac = round(float((d[ok] / rng[ok]).clip(lower=0.0).median()), 3)
                x, y = rng[ok].to_numpy(dtype=float), d[ok].to_numpy(dtype=float)
                if float(np.percentile(x, 95) - np.percentile(x, 5)) >= _MIN_RANGE_SPAN_F:
                    slope, icpt = (float(v) for v in np.polyfit(x, y, 1))
        metrics = {
            **base,
            "median_diff_f": round(med, 2),
            "p90_diff_f": round(float(d.quantile(0.9)), 2),
            "warm_share": round(warm_share, 4),
            "bypass_fraction_est": frac,
            "diff_per_range_slope": None if slope is None else round(slope, 3),
            "diff_intercept_f": None if icpt is None else round(icpt, 2),
            "attribution": "valve",
        }
        offset_like = (
            slope is not None
            and icpt is not None
            and med >= self.warn_f
            and slope < _OFFSET_MAX_SLOPE
            and icpt >= 0.5 * med
        )
        if offset_like:
            metrics["attribution"] = "sensor_offset"
            caveats.append(
                f"the difference does not grow with the condenser range (slope {slope:.2f} per F, "
                f"intercept {icpt:+.1f}F): mixed-in return water would scale with the range, a "
                "sensor offset does not -- check the tower leaving-water and condenser entering "
                "sensors before the valve"
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=metrics,
                summary=(
                    f"{equip}: condenser entering water {med:+.1f}F vs tower leaving with the "
                    "bypass shut, constant across loads -- a sensor offset, not a bypass leak"
                ),
                caveats=caveats,
            )
        if slope is None and med >= self.warn_f:
            caveats.append(
                "no condenser return temperature (or too little range): a leak cannot be told "
                "from an offset between the two supply sensors"
            )
        if med <= -self.warn_f:
            metrics["attribution"] = "sensor_offset"
            caveats.append(
                "condenser water entering the chillers reads colder than the towers' leaving water "
                "with the bypass shut -- mixing can only warm it, so one of the two sensors is off"
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=metrics,
                summary=(
                    f"{equip}: condenser entering water {med:+.1f}F vs tower leaving with the "
                    "bypass shut -- a sensor offset, not a leak"
                ),
                caveats=caveats,
            )
        severity = "fault" if med >= self.fault_f else "warn" if med >= self.warn_f else "ok"
        frac_note = f"; about {frac:.0%} of the condenser flow bypassing" if frac else ""
        summary = (
            f"{equip}: with the tower bypass commanded shut, condenser water enters the chillers "
            f"{med:+.1f}F warmer than it leaves the towers (median of {n} samples; "
            f"{warm_share:.0%} over {self.warn_f:g}F){frac_note}"
        )
        if severity != "ok":
            summary += " -- the bypass valve is leaking or not seating"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=summary,
            caveats=caveats,
        )

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """Shut-bypass running samples where entering water is ``warn_f`` above tower leaving."""
        mask, _src = self._shut_running(frame)
        return (mask & (self._diff(frame) >= self.warn_f)).fillna(False).astype(bool)

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: tower leaving vs condenser entering water, the leaking hours shaded."""
        from ..charts.evidence import Evidence

        roles = [
            r
            for r in (Role.CW_SUPPLY_TEMP, Role.COND_ENTERING_WATER_TEMP, Role.CW_RETURN_TEMP)
            if r in frame.columns
        ]
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=self.violation_mask(frame),
            label=f"bypass shut, entering > tower leaving + {self.warn_f:g}F",
            title=f"{equip}: condenser water, tower leaving vs chiller entering",
        )
