"""Rules: chilled-water plant CHWST reset + low-deltaT (PNNL Ch.8), and CHWST setpoint tracking.

:class:`CHWPlantReset` flags a chilled-water plant holding supply temp low at part load (no reset)
and/or running at low loop delta-T. :class:`CHWSupplyTracking` (0.91) flags a plant whose supply
temperature does not reach its trended setpoint while it runs -- a capacity or control fault that
starves every air handler downstream. Both adapt :mod:`camber.chwplant` to the role-frame
interface, and both decide "running" from the chiller's run status or command
(``compressor_status``) when it is mapped; only without one do they fall back to the supply
temperature, and then they say so. OAT (the reset regressor) comes via the runner's ``shared``
channel since it is building-level.
"""

from __future__ import annotations

import pandas as pd

from ..chwplant import analyze_chw_plant, analyze_chw_tracking, chiller_running, chw_tracking_mask
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_SUPPLY_TEMP_SP: "CHWS_SP",
    Role.OAT: "OAT",
}

#: The chiller run status / command role (a chiller's compressor run point).
RUN_STATUS_ROLE = Role.COMPRESSOR_STATUS

TEMPERATURE_GATE_CAVEAT = (
    "no chiller run status or command mapped: running hours inferred from the supply "
    "temperature sitting in a chilled range (38-58F), which also counts a stopped chiller on a "
    "cold or shared loop -- map the chiller's status or command (compressor_status)"
)


POWER_GATE_CAVEAT = (
    "no chiller run status or command mapped: running hours read from the chiller's power above "
    "a tenth of its own 95th-percentile draw -- map the status or command (compressor_status) "
    "for a firmer gate"
)


def _run_mask(frame: pd.DataFrame):
    """``(running mask | None, source)``: the chiller's status/command, else its power, else None.

    0.92 (#66): a chiller with no run status but a power point is gated on the power
    (:func:`camber.schedules.plant_run_mask`), source ``"power"``; only without either do the
    rules fall back to the supply temperature.
    """
    if RUN_STATUS_ROLE in frame.columns:
        run = chiller_running(frame[RUN_STATUS_ROLE], index=frame.index)
        if run is not None:
            return run, "status"
    if Role.POWER in frame.columns:
        from ..schedules import plant_run_mask

        run, src = plant_run_mask(frame, "chw")
        if run is not None and src == "chiller power proxy":
            return run, "power"
    return None, "temperature"


def _gate_caveats(source: str) -> list:
    if source == "temperature":
        return [TEMPERATURE_GATE_CAVEAT]
    return [POWER_GATE_CAVEAT] if source == "power" else []


class CHWPlantReset:
    """Detects no CHWST reset and/or low loop delta-T at the chilled-water plant
    (PNNL Re-tuning Ch.8)."""

    name = "chw_plant_reset"
    roles_required = (Role.CHW_SUPPLY_TEMP,)
    roles_optional = (
        Role.CHW_RETURN_TEMP,
        Role.CHW_SUPPLY_TEMP_SP,
        Role.OAT,
        RUN_STATUS_ROLE,
        Role.POWER,  # 0.92 (#66): the run gate's fallback when no status is mapped
    )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        run, source = _run_mask(frame)
        if run is not None and not run.any():
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"n_running": 0, "run_source": source},
                summary=f"{equip}: did not run in the window (run {source} never on); not judged",
            )
        res = analyze_chw_plant(legacy, equip, running=run)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"n_running": 0, "run_source": source},
                summary="insufficient data",
                caveats=_gate_caveats(source),
            )
        caveats = _gate_caveats(source)
        # Low-deltaT sub-check: a NaN pct means no usable CHW return temp -> not evaluated
        # (must not count toward the fault). Reset sub-check: None means OAT was absent/thin
        # -> not evaluated (must not read as a confident "no reset").
        dt_evaluated = res.low_deltaT_pct == res.low_deltaT_pct  # False when NaN
        low_dt = res.low_deltaT_pct if dt_evaluated else 0.0
        if not dt_evaluated:
            caveats.append("loop deltaT not evaluated: no CHW return temp")
        reset = res.chwst_reset_present  # True / False / None
        if reset is None:
            caveats.append("CHWST reset not evaluated: no/insufficient OAT")
        # Severity: only a genuinely-evaluated flat reset (is False) downgrades; None does not.
        if low_dt >= 50.0:
            severity = "fault"
        elif low_dt >= 20.0 or reset is False:
            severity = "warn"
        else:
            severity = "ok"
        reset_note = (
            "CHWST reset present"
            if reset is True
            else "flat CHWST (no reset)"
            if reset is False
            else "CHWST reset not evaluated (no OAT)"
        )
        dt_note = (
            f"loop deltaT median {res.deltaT_median_f:.1f}F "
            f"({res.low_deltaT_pct:.0f}% of running hours < {res.design_deltaT_min_f:.0f}F)"
            if dt_evaluated
            else "loop deltaT not evaluated (no return temp)"
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "chwst_median_f": res.chwst_median_f,
                "chwst_slope_per_F": res.chwst_slope_per_F,
                "chwst_reset_present": res.chwst_reset_present,
                "pct_chwst_low": res.pct_chwst_low,
                "deltaT_median_f": res.deltaT_median_f,
                "low_deltaT_pct": res.low_deltaT_pct,
                "n_running": res.n_running,
                "run_source": source if run is not None else res.run_source,
            },
            summary=f"{equip}: CHWST median {res.chwst_median_f:.1f}F, {dt_note}; {reset_note}",
            caveats=caveats,
        )


class CHWSupplyTracking:
    """Flags chilled-water supply temperature held above its setpoint while the plant runs.

    Judged on running samples only: the chiller's status / command (``compressor_status``) when
    mapped, the first interval after each start left out as pull-down. Reports the share of
    running time with CHWST more than ``above_f`` above the trended setpoint, and the loop deltaT
    (overall and during those hours: a wide deltaT while short of setpoint points at load beyond
    capacity, a narrow one at flow or control). ``warn`` at ``warn_pct``, ``fault`` at
    ``fault_pct`` of running time. Without a run status the plant is taken as running when CHWST
    sits in 38-58F; the rule then caveats that and never goes beyond ``warn``.

    ``above_f`` defaults to 3F: outside a healthy loop's ~1F control band plus ~0.5F sensor
    accuracy and hourly staging transients (see :func:`camber.chwplant.analyze_chw_tracking`).
    """

    name = "chw_supply_tracking"
    roles_required = (Role.CHW_SUPPLY_TEMP, Role.CHW_SUPPLY_TEMP_SP)
    roles_optional = (RUN_STATUS_ROLE, Role.CHW_RETURN_TEMP, Role.POWER)  # POWER: 0.92 (#66)

    def __init__(
        self,
        *,
        above_f: float = 3.0,
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
        settle_intervals: int = 1,
        min_running: int = 24,
    ):
        self.above_f = above_f
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.settle_intervals = settle_intervals
        self.min_running = min_running

    def _legacy(self, frame):
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        return frame.rename(columns=cols)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over a chiller / plant role-frame; return a Finding on CHWST-vs-setpoint tracking."""
        run, source = _run_mask(frame)
        base = {"run_source": source, "above_f": self.above_f}
        if run is not None and not run.any():
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={**base, "n_running": 0, "above_pct": None},
                summary=f"{equip}: did not run in the window (run {source} never on); not judged",
            )
        res = analyze_chw_tracking(
            self._legacy(frame),
            equip,
            running=run,
            above_f=self.above_f,
            settle_intervals=self.settle_intervals,
        )
        caveats = _gate_caveats(source)
        if res is None or res.n_running < self.min_running:
            n = 0 if res is None else res.n_running
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={**base, "n_running": n, "above_pct": None},
                summary=(
                    f"{equip}: CHWST-vs-setpoint not judged ({n} running samples with a supply "
                    f"temperature and a setpoint; needs {self.min_running})"
                ),
                caveats=caveats,
            )
        pct = float(res.above_pct or 0.0)
        severity = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        if source == "temperature" and severity == "fault":
            severity = "warn"  # an inferred runtime cannot carry a fault on its own
            caveats.append("severity capped at warn: runtime inferred from temperature")
        if res.deltaT_median_f is None:
            caveats.append("loop deltaT not reported: no CHW return temperature")
        dt = (
            f"; loop deltaT median {res.deltaT_median_f:.1f}F"
            + (
                f" ({res.deltaT_median_above_f:.1f}F while above setpoint)"
                if res.deltaT_median_above_f is not None
                else ""
            )
            if res.deltaT_median_f is not None
            else ""
        )
        excess = (
            f", median {res.median_excess_above_f:.1f}F over"
            if res.median_excess_above_f is not None
            else ""
        )
        summary = (
            f"{equip}: CHWST more than {self.above_f:g}F above setpoint {pct:.0f}% of "
            f"{res.running_hours:,.0f} running h ({source} gate{excess}); CHWST median "
            f"{res.chwst_median_f}F vs setpoint median {res.setpoint_median_f}F{dt}"
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                **base,
                "n_running": res.n_running,
                "running_hours": res.running_hours,
                "above_pct": res.above_pct,
                "above_hours": res.above_hours,
                "mean_excess_f": res.mean_excess_f,
                "median_excess_above_f": res.median_excess_above_f,
                "chwst_median_f": res.chwst_median_f,
                "setpoint_median_f": res.setpoint_median_f,
                "deltaT_median_f": res.deltaT_median_f,
                "deltaT_median_above_f": res.deltaT_median_above_f,
                "n_status_on": res.n_status_on,
            },
            summary=summary,
            caveats=caveats,
        )

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """Running samples with CHWST above setpoint + ``above_f`` (bool, on ``frame``'s index)."""
        legacy = self._legacy(frame)
        if "CHWS_Temp" not in legacy.columns or "CHWS_SP" not in legacy.columns:
            return pd.Series(False, index=frame.index)
        run, _src = _run_mask(frame)
        return chw_tracking_mask(
            legacy, running=run, above_f=self.above_f, settle_intervals=self.settle_intervals
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: CHWST vs its setpoint, the running hours short of setpoint shaded."""
        from ..charts.evidence import Evidence

        roles = [r for r in (Role.CHW_SUPPLY_TEMP, Role.CHW_SUPPLY_TEMP_SP) if r in frame.columns]
        if Role.CHW_RETURN_TEMP in frame.columns:
            roles.append(Role.CHW_RETURN_TEMP)
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=self.violation_mask(frame),
            label=f"CHWST > setpoint + {self.above_f:g}F",
            title=f"{equip}: CHW supply vs setpoint",
        )
