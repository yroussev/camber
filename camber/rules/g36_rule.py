"""Rule: ASHRAE Guideline 36 §5.16.14 AHU fault conditions FC1-FC15, per air handler.

Adapts :func:`camber.fdd_g36.run_g36_afdd` to the role-frame interface so the G36 engine runs from a
config (``rules: [g36_afdd]``) and its results flow into ``camber run`` and the RCx report like any
other rule. One Finding per AHU carries every fault condition's trip rate, hours and denominator.

What the adapter decides (and says, as caveats):

* **Unit-running gate.** The fan is read from supply-fan status, else speed, else airflow
  (:func:`camber.schedules.fan_on_mask`); with none of them the rule declines -- G36 suspends AFDD
  while the AHU is not operating, and a fan-off hour with both valves shut reads as free cooling.
* **ModeDelay / AlarmDelay** (G36 defaults 30 / 30 min): evaluation is suspended after a fan start
  and after a zone-group mode change (from the occupancy / warm-up / cool-down points when mapped),
  and a fault condition counts only when it persisted for AlarmDelay.
* **Cooling-only AHUs.** No heating-valve point is read as "no heating coil" (HC = 0 %, FC7 and FC15
  omitted) -- explicitly, with a caveat. Pass ``heating_coil=True`` when the AHU has a heating coil
  whose valve simply is not trended: the rule then declines rather than guess its state.
* **Coil entering/leaving temperatures (FC14/FC15).** G36 lets MAT and SAT stand in for them
  "depending on the AHU configuration". Here MAT/SAT are used as the cooling-coil entering/leaving
  temperatures only on an AHU without a heating coil (nothing else sits between the two sensors),
  with the fan-heat term signed for SAT downstream of the supply fan. On an AHU with a heating
  coil and no dedicated coil sensors FC14 and FC15 are declined. When FC14 is evaluated, FC8/FC9
  hours that coincide with a confirmed FC14 are attributed to FC14 -- a passing cooling valve is
  the cause both list, and FC14 names it -- so a leaking valve is not reported under the
  free-cooling labels.
* **FC6** needs the minimum outdoor-air fraction, which no role carries; pass ``min_oa_pct`` (the
  design minimum OA as a % of supply airflow) to evaluate it against %OA from the temperatures.

Severity is screening-grade: ``fault`` when any evaluated FC was reported for at least
``fault_pct`` % of its applicable hours, ``warn`` at ``warn_pct`` %, over at least
``min_applicable_hours``. G36 itself reports every confirmed fault as a Level 3 alarm; the
percentages here only rank a period of history.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..fdd_g36 import (
    ALARM_DELAY_MIN,
    AVG_WINDOW_MIN,
    FC_DESC,
    MODE_DELAY_MIN,
    OS_FREECOOL,
    G36Thresholds,
    _median_step,
    run_g36_afdd,
)
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

# role -> engine column (the engine's own variable names, see camber.fdd_g36)
_ROLE_TO_COL = {
    Role.HEAT_VALVE: "HC",
    Role.COOL_VALVE: "CC",
    Role.SUPPLY_AIR_TEMP: "SAT",
    Role.MIXED_AIR_TEMP: "MAT",
    Role.RETURN_AIR_TEMP: "RAT",
    Role.OAT: "OAT",
    Role.SUPPLY_AIR_TEMP_SP: "SATSP",
    Role.SUPPLY_FAN_SPEED: "FS",
    Role.SUPPLY_FAN_STATUS: "FAN_STATUS",
    Role.AIRFLOW: "AIRFLOW",
    Role.DUCT_STATIC: "DSP",
    Role.DUCT_STATIC_SP: "DSPSP",
    Role.OA_DAMPER: "OA_Damper",
}
_PERCENT_COLS = ("HC", "CC", "FS", "OA_Damper")
# zone-group mode inputs, combined into one mode code for ModeDelay
_MODE_ROLES = (Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)

# Screening-grade severity floors (not from G36; see the module docstring).
WARN_PCT = 5.0
FAULT_PCT = 20.0
MIN_APPLICABLE_HOURS = 24.0

# FCs whose free-cooling hours a confirmed FC14 explains (both list a passing cooling valve)
_EXPLAINED_BY_FC14 = (8, 9)


def _num(frame: pd.DataFrame, role) -> pd.Series | None:
    if role not in frame.columns:
        return None
    s = pd.to_numeric(frame[role], errors="coerce")
    return s if s.notna().any() else None


class G36AFDD:
    """ASHRAE Guideline 36 §5.16.14 AHU fault conditions FC1-FC15 (one Finding per AHU)."""

    name = "g36_afdd"
    #: provisional: G36 §5.16.14 is written for air handlers; the runner declines other classes
    equip_classes = ("AHU", "RTU", "DOAS", "MAU")
    roles_required = (Role.SUPPLY_AIR_TEMP, Role.COOL_VALVE)
    roles_optional = (
        Role.HEAT_VALVE,
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.OAT,
        Role.SUPPLY_AIR_TEMP_SP,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
        Role.DUCT_STATIC,
        Role.DUCT_STATIC_SP,
        Role.OA_DAMPER,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
    )

    def __init__(
        self,
        *,
        heating_coil: bool | None = None,  # None: inferred from a mapped heating-valve point
        mat_sat_as_coil_temps: bool = True,  # MAT/SAT as CCET/CCLT where G36 allows (see module)
        min_oa_pct: float | None = None,  # design minimum OA, % of supply airflow (enables FC6)
        mode_delay_min: float = MODE_DELAY_MIN,  # G36 ModeDelay
        alarm_delay_min: float = ALARM_DELAY_MIN,  # G36 AlarmDelay
        avg_window_min: float = AVG_WINDOW_MIN,  # G36 rolling-average window
        econ_damper_open: float = 80.0,  # OA damper % at/above which cooling is OS#3
        valve_thr: float = 5.0,  # valve % above which a coil is active
        warn_pct: float = WARN_PCT,  # screening-grade
        fault_pct: float = FAULT_PCT,  # screening-grade
        min_applicable_hours: float = MIN_APPLICABLE_HOURS,
    ):
        self.heating_coil = heating_coil
        self.mat_sat_as_coil_temps = mat_sat_as_coil_temps
        self.min_oa_pct = min_oa_pct
        self.mode_delay_min = mode_delay_min
        self.alarm_delay_min = alarm_delay_min
        self.avg_window_min = avg_window_min
        self.econ_damper_open = econ_damper_open
        self.valve_thr = valve_thr
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.min_applicable_hours = min_applicable_hours

    # ------------------------------------------------------------------ engine input
    def _engine_frame(self, frame: pd.DataFrame):
        """``(df, thresholds, caveats, declined_fcs, declined_reason)`` for the engine."""
        caveats: list = []
        declined_fcs: dict = {}
        df = pd.DataFrame(index=frame.index)
        for role, col in _ROLE_TO_COL.items():
            s = _num(frame, role)
            if s is None:
                continue
            df[col] = normalize_percent(s) if col in _PERCENT_COLS else s
        has_hc = "HC" in df.columns
        if self.heating_coil is True and not has_hc:
            return (
                None,
                None,
                caveats,
                declined_fcs,
                (
                    "the AHU has a heating coil (heating_coil=True) but no heating-valve point is "
                    "mapped, so its operating state cannot be classified"
                ),
            )
        if self.heating_coil is False and has_hc:
            df = df.drop(columns="HC")
            has_hc = False
            caveats.append("heating_coil=False: the mapped heating-valve point is ignored")
        no_heating = not has_hc
        if no_heating and self.heating_coil is None:
            caveats.append(
                "no heating-valve point mapped: the AHU is assumed to have no heating coil "
                "(set heating_coil=True if it has one that is not trended)"
            )

        # zone-group mode for ModeDelay: occupancy / warm-up / cool-down combined into one code
        mode = None
        for bit, role in enumerate(_MODE_ROLES):
            s = _num(frame, role)
            if s is None:
                continue
            code = (s > 0.5).astype(float).where(s.notna()) * (2**bit)
            mode = code if mode is None else mode + code
        if mode is not None:
            df["MODE"] = mode
        else:
            caveats.append(
                "no occupancy / warm-up / cool-down point: ModeDelay applies after fan starts only"
            )

        # coil entering/leaving temperatures (FC14/FC15)
        thr = G36Thresholds()
        mat_sat = "MAT" in df.columns and "SAT" in df.columns
        if no_heating and self.mat_sat_as_coil_temps and mat_sat:
            df["CCET"], df["CCLT"] = df["MAT"], df["SAT"]
            # SAT is downstream of the supply fan: the air left the coil dT_sf cooler
            thr.fc14_fan_heat = -thr.dT_sf
            caveats.append(
                "FC14 uses MAT/SAT as the cooling-coil entering/leaving temperatures (no heating "
                "coil between them), with SAT corrected for supply-fan heat"
            )
        else:
            if not mat_sat:
                why = "needs cooling-coil entering/leaving temperatures (no MAT/SAT pair mapped)"
            elif not self.mat_sat_as_coil_temps:
                why = "MAT/SAT substitution disabled and no dedicated coil sensors are mapped"
            else:
                why = (
                    "MAT/SAT span both coils on an AHU with a heating coil, so they cannot "
                    "isolate the cooling coil; dedicated coil sensors are needed"
                )
            declined_fcs[14] = why
        if has_hc:
            # MAT/SAT span the cooling coil too; no role carries dedicated heating-coil sensors
            declined_fcs[15] = (
                "needs heating-coil entering/leaving temperatures; MAT/SAT span the cooling coil "
                "as well"
            )

        # FC6: %OA from the temperatures (G36 %OA definition) against a declared minimum
        if self.min_oa_pct is not None and {"MAT", "RAT", "OAT"} <= set(df.columns):
            span = df["OAT"] - df["RAT"]
            pct = 100.0 * (df["MAT"] - df["RAT"]) / span.where(span.abs() >= 1.0)
            df["pct_oa"] = pct.clip(-50.0, 150.0)
            df["pct_oa_min"] = float(self.min_oa_pct)
            caveats.append(
                f"FC6 compares %OA from the air temperatures with a fixed minimum of "
                f"{float(self.min_oa_pct):g} % (G36 uses the active minimum-OA setpoint over "
                "actual airflow)"
            )
        else:
            declined_fcs[6] = (
                "needs the minimum outdoor-air fraction (set min_oa_pct) and MAT/RAT/OAT"
            )
        return df, thr, caveats, declined_fcs, None

    def _run(self, frame: pd.DataFrame):
        df, thr, caveats, declined_fcs, reason = self._engine_frame(frame)
        if reason is not None:
            return None, caveats, declined_fcs, reason
        res = run_g36_afdd(
            df,
            "",
            thr=thr,
            econ_damper_open=self.econ_damper_open,
            valve_thr=self.valve_thr,
            mode_delay_min=self.mode_delay_min,
            alarm_delay_min=self.alarm_delay_min,
            avg_window_min=self.avg_window_min,
            keep_masks=True,
        )
        return res, caveats, declined_fcs, None

    @staticmethod
    def _attributed(res, declined_fcs, step_h: float) -> tuple[dict, dict]:
        """``({fc: reported mask}, {FC label: hours moved to FC14})`` after the FC14 attribution."""
        m = res.masks
        reported = {fc: m[f"FC{fc}"].to_numpy(dtype=bool).copy() for fc in range(1, 16)}
        moved: dict = {}
        if 14 not in declined_fcs and 14 not in res.omitted:
            fc14 = reported[14]
            for fc in _EXPLAINED_BY_FC14:
                overlap = reported[fc] & fc14
                if overlap.any():
                    moved[f"FC{fc}"] = round(float(overlap.sum()) * step_h, 2)
                    reported[fc] = reported[fc] & ~fc14
        return reported, moved

    # ------------------------------------------------------------------ the rule
    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the G36 fault set on an AHU role-frame; return one Finding."""
        res, caveats, declined_fcs, reason = self._run(frame)
        if reason is None and res is not None and res.declined:
            reason = res.declined
            caveats = caveats + [c for c in res.caveats if c not in caveats]
        if reason is not None or res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": reason},
                summary=f"{equip}: declined -- {reason}",
                caveats=caveats + [f"G36 AFDD not evaluated: {reason}"],
            )
        caveats = caveats + [c for c in res.caveats if c not in caveats]
        step_h = float(_median_step(res.masks.index) / pd.Timedelta(hours=1))
        reported, moved = self._attributed(res, declined_fcs, step_h)

        fcs: dict = {}
        worst, worst_fc = 0.0, None
        for fc in range(1, 16):
            label = f"FC{fc}"
            n_app = res.fault_n_applicable.get(fc, 0)
            app_h = round(float(res.masks[f"FC{fc}_app"].sum()) * step_h, 2)
            if fc in res.omitted:
                fcs[label] = {"status": "omitted", "reason": res.omitted[fc]}
                continue
            if fc in declined_fcs:
                fcs[label] = {"status": "declined", "reason": declined_fcs[fc]}
                caveats.append(f"{label} not evaluated: {declined_fcs[fc]}")
                continue
            miss = res.missing_inputs.get(fc)
            if miss:
                fcs[label] = {
                    "status": "declined",
                    "reason": "missing input(s): " + ", ".join(miss),
                }
                continue
            if n_app == 0:
                fcs[label] = {
                    "status": "not applicable",
                    "reason": "its operating states never occurred",
                }
                continue
            hours = round(float(reported[fc].sum()) * step_h, 2)
            pct = round(100.0 * float(reported[fc].sum()) / n_app, 2)
            fcs[label] = {
                "status": "evaluated",
                "pct": pct,
                "hours": hours,
                "applicable_hours": app_h,
                "description": FC_DESC[fc],
            }
            if app_h >= self.min_applicable_hours and pct > worst:
                worst, worst_fc = pct, label
        no_input = [k for k, v in fcs.items() if str(v.get("reason", "")).startswith("missing")]
        if no_input:
            caveats.append(f"{', '.join(no_input)} not evaluated: an input they need is not mapped")
        if moved:
            caveats.append(
                "FC8/FC9 hours that coincide with a confirmed FC14 (temperature drop across the "
                "inactive cooling coil) are attributed to FC14: "
                + ", ".join(f"{k} {v:g} h" for k, v in moved.items())
            )

        severity = (
            "fault" if worst >= self.fault_pct else ("warn" if worst >= self.warn_pct else "ok")
        )
        flagged = sorted(
            (
                (k, v)
                for k, v in fcs.items()
                if v.get("status") == "evaluated"
                and (v.get("pct") or 0) >= self.warn_pct
                and v["applicable_hours"] >= self.min_applicable_hours
            ),
            key=lambda kv: -(kv[1]["pct"] or 0),
        )
        n_eval = sum(1 for v in fcs.values() if v.get("status") == "evaluated")
        if flagged:
            body = "; ".join(
                f"{k} {v['description']} {v['pct']:.0f}% of applicable hours ({v['hours']:g} h)"
                for k, v in flagged[:4]
            )
        else:
            body = f"no fault condition reported on {self.warn_pct:g}% or more of its hours"
        fan_h = (res.n_intervals - res.n_fan_off) * step_h
        summary = (
            f"{equip}: G36 §5.16.14 AFDD ({n_eval} of 15 FCs evaluated) -- {body}. Fan gate: "
            f"{res.fan_gate}; ModeDelay {res.delays['mode_delay_min']:g} min, AlarmDelay "
            f"{res.delays['alarm_delay_min']:g} min"
        )
        os_hours = {f"OS{o}": round(c * step_h, 2) for o, c in res.os_distribution.items()}
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "worst_fc": worst_fc,
                "worst_pct": worst,
                "fc": fcs,
                "fan_gate": res.fan_gate,
                "fan_on_hours": round(float(fan_h), 2),
                "suspended_hours": round(float(res.n_suspended) * step_h, 2),
                "os_hours": os_hours,
                "free_cooling_hours": os_hours.get(f"OS{OS_FREECOOL}"),
                "attributed_to_fc14_hours": moved,
                "mode_delay_min": res.delays["mode_delay_min"],
                "alarm_delay_min": res.delays["alarm_delay_min"],
                "n_intervals": res.n_intervals,
            },
            summary=summary,
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Multi-trend of the AHU's air temperatures with the reported G36 fault hours shaded."""
        from ..charts.evidence import Evidence

        res, _c, declined_fcs, reason = self._run(frame)
        if reason is not None or res is None or res.declined or res.masks is None:
            return None
        step_h = float(_median_step(res.masks.index) / pd.Timedelta(hours=1))
        reported, _moved = self._attributed(res, declined_fcs, step_h)
        any_fc = np.zeros(len(res.masks), dtype=bool)
        for fc, m in reported.items():
            if fc not in declined_fcs and fc not in res.omitted:
                any_fc |= m
        mask = pd.Series(any_fc, index=res.masks.index).reindex(frame.index, fill_value=False)
        roles = [
            r
            for r in (
                Role.SUPPLY_AIR_TEMP,
                Role.MIXED_AIR_TEMP,
                Role.SUPPLY_AIR_TEMP_SP,
                Role.OAT,
                Role.COOL_VALVE,
            )
            if r in frame.columns
        ]
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=mask,
            label="G36 fault reported",
            title=f"{equip}: G36 §5.16.14 fault conditions",
        )
