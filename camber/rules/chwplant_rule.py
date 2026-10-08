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
    (PNNL Re-tuning Ch.8).

    0.98 (#86) parameters:

    - ``design_deltaT_min_f`` (8 F): the loop delta-T below which an hour counts as low;
    - ``expected_reset_sign`` (``"negative"``): the direction a healthy reset moves CHWST as the
      outdoor temperature rises. An outdoor-air reset lowers CHWST in hot weather, so its slope on
      OAT is negative. A clear slope the other way is not a reset: ``chwst_reset_present`` is
      False, ``chwst_reset_direction`` is ``"reverse"``, and the finding warns. It is usually a
      plant that cannot hold its supply temperature in hot weather, or a reversed schedule.
      ``"positive"`` expects the opposite; ``"any"`` accepts either direction (the pre-0.98
      behaviour);
    - ``flow_mode`` (``"auto"``) and ``constant_flow_cv`` (0.05): a constant-primary-flow plant
      moves the same water at every load, so its loop delta-T is low at part load by design.
      ``"auto"`` reads the mapped CHW flow (``chw_flow``) over the judged hours: when at least
      24 hours have a reading and the flow's coefficient of variation is at most
      ``constant_flow_cv``, the plant is taken as constant flow. For a constant-flow plant the
      delta-T is reported but left out of severity, with a caveat. ``"constant"`` and
      ``"variable"`` declare the plant's flow mode instead.
    """

    name = "chw_plant_reset"
    roles_required = (Role.CHW_SUPPLY_TEMP,)
    roles_optional = (
        Role.CHW_RETURN_TEMP,
        Role.CHW_SUPPLY_TEMP_SP,
        Role.OAT,
        RUN_STATUS_ROLE,
        Role.POWER,  # 0.92 (#66): the run gate's fallback when no status is mapped
        Role.CHW_FLOW,  # 0.98 (#86): recognises a constant-primary-flow plant
    )

    #: judged hours with a flow reading that ``flow_mode="auto"`` needs before deciding
    MIN_FLOW_SAMPLES = 24

    def __init__(
        self,
        *,
        design_deltaT_min_f: float = 8.0,
        expected_reset_sign: str = "negative",
        flow_mode: str = "auto",
        constant_flow_cv: float = 0.05,
    ):
        if expected_reset_sign not in ("negative", "positive", "any"):
            raise ValueError(
                f"expected_reset_sign must be 'negative', 'positive' or 'any', "
                f"not {expected_reset_sign!r}"
            )
        if flow_mode not in ("auto", "constant", "variable"):
            raise ValueError(
                f"flow_mode must be 'auto', 'constant' or 'variable', not {flow_mode!r}"
            )
        self.design_deltaT_min_f = float(design_deltaT_min_f)
        self.expected_reset_sign = expected_reset_sign
        self.flow_mode = flow_mode
        self.constant_flow_cv = float(constant_flow_cv)

    def _direction(self, slope, present):
        """``(reset present, direction)`` after the sign check.

        Direction is ``"expected"``, ``"reverse"``, ``"flat"``, or ``None`` when the reset was
        not evaluated.
        """
        if present is None or slope is None:
            return None, None
        if not present:
            return False, "flat"
        sign = self.expected_reset_sign
        if sign == "any" or (sign == "negative") == (slope < 0):
            return True, "expected"
        return False, "reverse"

    def _flow_mode(self, res):
        """The plant's flow mode: ``"constant"``, ``"variable"`` or ``"unknown"``."""
        if self.flow_mode != "auto":
            return self.flow_mode
        if res.flow_cv is None or res.n_flow < self.MIN_FLOW_SAMPLES:
            return "unknown"
        return "constant" if res.flow_cv <= self.constant_flow_cv else "variable"

    def violation_masks(self, frame: pd.DataFrame) -> dict:
        """The running samples behind the finding (0.102, #119), boolean Series on ``frame``'s
        index: ``"low deltaT"`` -- loop deltaT below ``design_deltaT_min_f``, only when the rule
        judges it (a return temperature, not constant flow) -- and ``"CHWST held low"`` -- CHWST
        at or below 46 F, only when the rule finds no reset (flat or reversed)."""
        none = pd.Series(False, index=frame.index)
        out: dict = {}
        f = self._analyze(frame, masks_out=out)
        m = f.metrics
        pct = m.get("low_deltaT_pct")  # NaN when no return temperature was usable
        dt_judged = pct is not None and pct == pct and m.get("flow_mode") != "constant"
        return {
            "low deltaT": out.get("low_deltaT", none) if dt_judged else none,
            "CHWST held low": (
                out.get("chwst_low", none) if m.get("chwst_reset_present") is False else none
            ),
        }

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The union of :meth:`violation_masks`."""
        m = self.violation_masks(frame)
        return m["low deltaT"] | m["CHWST held low"]

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: CHW supply/return and OAT, the hours behind the finding shaded (#119)."""
        from ..charts.evidence import Evidence

        roles = [
            r for r in (Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP, Role.OAT) if r in frame.columns
        ]
        if Role.CHW_SUPPLY_TEMP not in roles:
            return None
        masks = self.violation_masks(frame)
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=masks["low deltaT"] | masks["CHWST held low"],
            masks=masks,
            label=f"low deltaT (< {self.design_deltaT_min_f:g}F) or CHWST held low, no reset",
            title=f"{equip}: CHW plant reset / deltaT",
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        return self._analyze(frame, equip=equip)

    def _analyze(self, frame: pd.DataFrame, *, equip: str = "", masks_out=None) -> Finding:
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        if Role.CHW_FLOW in frame.columns:
            cols[Role.CHW_FLOW] = "CHW_Flow"
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
        res = analyze_chw_plant(
            legacy,
            equip,
            running=run,
            design_deltaT_min_f=self.design_deltaT_min_f,
            masks_out=masks_out,
        )
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
        if not dt_evaluated:
            caveats.append("loop deltaT not evaluated: no CHW return temp")
        flow_mode = self._flow_mode(res)
        constant = flow_mode == "constant"
        dt_judged = dt_evaluated and not constant
        low_dt = res.low_deltaT_pct if dt_judged else 0.0
        if constant and dt_evaluated:
            how = (
                f"CHW flow varies by {100.0 * res.flow_cv:.2f}% over the running hours"
                if self.flow_mode == "auto" and res.flow_cv is not None
                else "declared constant flow (flow_mode)"
            )
            caveats.append(
                f"constant primary flow ({how}): a low loop deltaT at part load is expected by "
                "design, so it is reported but not judged"
            )
        reset, direction = self._direction(res.chwst_slope_per_F, res.chwst_reset_present)
        if reset is None:
            caveats.append("CHWST reset not evaluated: no/insufficient OAT")
        if direction == "reverse":
            caveats.append(
                f"CHWST moves the wrong way with OAT (slope {res.chwst_slope_per_F:+.2f} F/F; "
                f"expected {self.expected_reset_sign}): not counted as a reset -- a plant that "
                "cannot hold its supply temperature in hot weather, or a reversed reset schedule"
            )
        # Severity: only a genuinely-evaluated flat or reversed reset (is False) downgrades;
        # None does not.
        if low_dt >= 50.0:
            severity = "fault"
        elif low_dt >= 20.0 or reset is False:
            severity = "warn"
        else:
            severity = "ok"
        reset_note = (
            "CHWST reset present"
            if reset is True
            else "CHWST rises with OAT (reverse of a reset)"
            if direction == "reverse"
            else "flat CHWST (no reset)"
            if reset is False
            else "CHWST reset not evaluated (no OAT)"
        )
        dt_note = (
            f"loop deltaT median {res.deltaT_median_f:.1f}F "
            f"({res.low_deltaT_pct:.0f}% of running hours < {res.design_deltaT_min_f:g}F"
            + (", constant flow: not judged)" if constant else ")")
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
                "chwst_reset_present": reset,
                "chwst_reset_direction": direction,
                "pct_chwst_low": res.pct_chwst_low,
                "deltaT_median_f": res.deltaT_median_f,
                "low_deltaT_pct": res.low_deltaT_pct,
                "design_deltaT_min_f": res.design_deltaT_min_f,
                "flow_mode": flow_mode,
                "flow_cv": res.flow_cv,
                "n_running": res.n_running,
                "run_source": source if run is not None else res.run_source,
            },
            summary=f"{equip}: CHWST median {res.chwst_median_f:.1f}F, {dt_note}; {reset_note}",
            caveats=caveats,
        )


class CHWSupplyTracking:
    """Flags chilled-water supply temperature held above its setpoint while the plant runs.

    Judged on running samples only, the first ``settle_intervals`` after each start left out as
    pull-down. Running comes from the chiller's status / command (``compressor_status``) when
    mapped; else (0.92, #66) from its ``power`` above a tenth of its own 95th-percentile draw,
    with a caveat; else the plant is taken as running when CHWST sits in 38-58F, which is
    caveated and never goes beyond ``warn``. A chiller that never ran in the window, or with fewer
    than ``min_running`` judged samples, is ``info`` and not judged. Reports the share of running
    time with CHWST more than ``above_f`` above the trended setpoint, and the loop deltaT (overall
    and during those hours: a wide deltaT while short of setpoint points at load beyond capacity,
    a narrow one at flow or control). ``warn`` at ``warn_pct``, ``fault`` at ``fault_pct`` of
    running time.

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
