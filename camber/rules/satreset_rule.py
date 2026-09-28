"""Rule: supply-air-temperature reset behavior.

Supply air held cold regardless of load (no upward reset at low cooling demand)
sustains terminal reheat and wastes energy. Adapts
:func:`camber.satreset.analyze_satreset` to the role-frame interface; needs
SUPPLY_AIR_TEMP, and uses COOL_VALVE (to isolate cooling-mode hours) and OAT
(the reset regressor) when present.

**A trended setpoint overrides the SAT-shape inference (#63).** Without a setpoint, "reset" is read
from supply air rising with OAT -- but a cooling coil that runs out of capacity on hot days produces
exactly that shape. When ``SUPPLY_AIR_TEMP_SP`` is mapped, the rule asks the setpoint instead
(:func:`camber.setpoint_reset.classify_setpoint_reset`, on fan-on samples): it must move on at least
3 days and 10 % of the days judged, and move with its driver -- the SAT reset requests when trended,
else OAT (``|Spearman rho| >= 0.3``). A flat setpoint, or one that only stepped once or a few times,
is reported as **not reset** -- "not reset (setpoint flat); SAT deviates" when supply air drifts off
it -- and never as "reset present"; a setpoint that moves but not with its driver is not confirmed.
The setpoint can only *remove* a reset the SAT shape suggested; it never upgrades a verdict. Without
a setpoint, an apparent reset from the SAT shape alone carries a capacity-shortfall caveat.
"""

from __future__ import annotations

import math

import pandas as pd

from ..model.roles import Role
from ..satreset import analyze_satreset
from ..schedules import FAN_GATE_NONE, fan_on_mask
from ..setpoint_reset import classify_setpoint_reset
from .base import Finding

# #63 setpoint-behaviour thresholds (°F). A reset that spans less than 2 °F over the window does
# nothing useful (a G36 SAT reset spans ~10 °F, 55-65 °F); 0.5 °F of movement within a day is
# above setpoint-register rounding and about two G36 SAT trim-and-respond steps (0.2-0.3 °F).
_SP_MIN_RANGE_F = 2.0
_SP_MOVE_MIN_F = 0.5
# supply air this far off a flat setpoint on average (°F) "deviates" -- beyond the ±2 °F band a
# tuned discharge-air loop holds (the supply_air_control rule's default tolerance class)
_SP_DEVIATION_F = 2.0

_ROLE_TO_SAT_COL = {
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
    Role.COOL_VALVE: "CHW_Valve",
    Role.OCCUPANCY: "Occupancy",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class SupplyAirReset:
    """Detects missing/weak supply-air-temperature reset that sustains reheat
    (PNNL Re-tuning / G36)."""

    name = "supply_air_reset"
    roles_required = (Role.SUPPLY_AIR_TEMP,)
    roles_optional = (
        Role.COOL_VALVE,
        Role.OAT,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
        # fan-on gate: status, else speed, else airflow (see camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
        # #63: the setpoint says whether a reset is programmed; requests (else OAT) drive it
        Role.SUPPLY_AIR_TEMP_SP,
        Role.SAT_RESET_REQUESTS,
    )

    def __init__(self, *, fan_gate: bool = True):
        self.fan_gate = fan_gate

    def _missing(self, frame: pd.DataFrame, fan_src: str) -> list:
        """Optional inputs truly absent -- the three fan signals are alternatives, so one is
        named only when none is present (pre-empts the runner's backstop)."""
        plain = (Role.COOL_VALVE, Role.OAT, Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)
        out = [r.value for r in plain if r not in frame.columns]
        if fan_src == FAN_GATE_NONE:
            out.append(Role.SUPPLY_FAN_STATUS.value)
        return out

    def _fan(self, frame: pd.DataFrame):
        """``(fan-on mask | None, source label)`` -- ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_SAT_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        fan, fan_src = self._fan(frame)
        res = analyze_satreset(legacy, equip, oat=oat, gate=fan)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={
                    "fan_gate": fan_src,
                    "_missing_optional": self._missing(frame, fan_src),
                },
                summary="insufficient data",
            )
        # Flag when SAT sits cold most of the time and isn't reset upward at low
        # load (flat/near-zero or load-tracking slope). A clear upward reset is ok.
        # The reset SLOPE needs OAT; without it slope is None (not evaluated) -- the
        # cold-dominant check still stands (it needs no OAT), but we caveat the missing
        # reset judgement and never format a confident slope.
        slope = res.slope_per_F
        caveats = []
        reset_evaluated = slope is not None and not math.isnan(slope)
        if not reset_evaluated:
            caveats.append("SAT reset not evaluated: no OAT")
        resetting_up = reset_evaluated and slope > 0.10
        cold_dominant = res.pct_sat_below_58 >= 50.0
        verdict = res.verdict
        sp_metrics, sp_note, sp_blocks = self._setpoint(frame, fan, slope)
        if sp_blocks == "no_reset":
            verdict = sp_note
        elif sp_note:
            verdict = f"{verdict}; {sp_note}"
        if resetting_up and sp_metrics.get("sp_behaviour") in (None, "insufficient"):
            caveats.append(
                "reset inferred from supply air alone: SAT rising with OAT can also be a cooling "
                "coil running out of capacity on hot days -- map SUPPLY_AIR_TEMP_SP to confirm"
            )
        if sp_blocks == "no_reset":
            resetting_up = False
        elif resetting_up and sp_blocks == "unconfirmed":
            caveats.append(
                "SAT rises with OAT, but the trended setpoint does not move repeatedly with its "
                "driver: the reset is not confirmed (a capacity shortfall looks the same)"
            )
        if resetting_up:
            severity = "info" if sp_blocks == "unconfirmed" else "ok"
        elif cold_dominant:
            severity = "warn"
        else:
            severity = "info"
        slope_note = f"slope {slope:+.2f} F/F" if reset_evaluated else "slope n/a (no OAT)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "sat_median": res.sat_median,
                "sat_std": res.sat_std,
                "slope_per_F": res.slope_per_F,
                "r2": res.r2,
                "pct_sat_below_58": res.pct_sat_below_58,
                "n_considered": res.n_considered,
                "fan_gate": fan_src,
                "_missing_optional": self._missing(frame, fan_src),
                **sp_metrics,
            },
            summary=(
                f"{equip}: SAT median {res.sat_median:.1f}F, {slope_note}, "
                f"<58F {res.pct_sat_below_58:.0f}% of cooling hours -- {verdict}"
            ),
            caveats=caveats,
        )

    def _setpoint(self, frame: pd.DataFrame, fan, slope):
        """#63: judge the trended SAT setpoint. Returns ``(metrics, verdict | None, block)``.

        ``block`` is ``"no_reset"`` (flat / stepped setpoint: a SAT-shape "reset" is not one),
        ``"unconfirmed"`` (the setpoint moves, but not repeatedly with its driver: a SAT-shape
        reset is only ``info``), or ``None`` (no setpoint, too little of it, or it resets with its
        driver: the SAT-shape logic stands).
        """
        if Role.SUPPLY_AIR_TEMP_SP not in frame.columns:
            return {}, None, None
        keep = (
            pd.Series(True, index=frame.index)
            if fan is None
            else pd.Series(fan, index=frame.index).fillna(False).astype(bool)
        )
        sp = pd.to_numeric(frame[Role.SUPPLY_AIR_TEMP_SP], errors="coerce")
        sp = sp.where((sp > 40) & (sp < 90))
        driver, label = None, None
        for role, name in ((Role.SAT_RESET_REQUESTS, "SAT reset requests"), (Role.OAT, "OAT")):
            if role in frame.columns and frame[role].notna().any():
                driver, label = pd.to_numeric(frame[role], errors="coerce")[keep], name
                break
        beh = classify_setpoint_reset(
            sp[keep],
            driver,
            driver_label=label,
            min_range=_SP_MIN_RANGE_F,
            move_min=_SP_MOVE_MIN_F,
            units="°F",
        )
        # supply air vs setpoint where the unit is cooling (fan on, cooling valve open)
        cooling = keep.copy()
        if Role.COOL_VALVE in frame.columns:
            cooling &= (frame[Role.COOL_VALVE] > 5.0).fillna(False)
        sat = frame[Role.SUPPLY_AIR_TEMP].where(
            (frame[Role.SUPPLY_AIR_TEMP] > 40) & (frame[Role.SUPPLY_AIR_TEMP] < 90)
        )
        dev = (sat - sp)[cooling].dropna()
        mean_dev = round(float(dev.mean()), 2) if len(dev) else None
        metrics = {
            "sp_behaviour": beh.kind,
            "sp_range_f": beh.sp_range,
            "sp_moving_days": beh.n_moving_days,
            "sp_days": beh.n_days,
            "sp_levels_f": beh.levels,
            "sp_driver": beh.driver,
            "sp_driver_rho": beh.driver_rho,
            "sat_minus_sp_mean_f": mean_dev,
        }
        if beh.kind in ("flat", "step"):
            lv = beh.levels
            if beh.kind == "flat":
                held = f"setpoint flat at ~{lv[0]:g}F" if lv else "setpoint flat"
            else:
                lvs = f" {' / '.join(f'{v:g}' for v in lv)}F" if lv else ""
                held = f"setpoint held at{lvs} -- a one-time step or manual change"
            verdict = f"NOT RESET ({held})"
            rises = slope is not None and not math.isnan(slope) and slope > 0.10
            off = mean_dev is not None and abs(mean_dev) >= _SP_DEVIATION_F
            if off or rises:
                how = [f"mean {mean_dev:+.1f}F off setpoint"] if mean_dev is not None else []
                if rises:
                    how.append("rising with OAT")
                verdict += (
                    f"; SAT deviates ({', '.join(how)}) -- a capacity or control shortfall, "
                    "not a reset"
                )
            return metrics, verdict, "no_reset"
        if beh.kind in ("varies", "unclear"):
            return metrics, f"setpoint {beh.label}", "unconfirmed"
        if beh.kind == "reset":
            return metrics, f"setpoint {beh.label}", None
        return metrics, None, None

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the SAT-vs-OAT cloud this rule's slope and spread are fitted on.

        The rule judges the *shape* of the reset (a slope, a spread), not a band, so its evidence is
        the cloud itself -- not a packaged reset line. Drawing a generic G36-style band here would
        shade "violations" against a sequence the site never declared (a site sequence, when known,
        is drawn by the RCx report's SAT reset census instead).
        """
        from ..charts.evidence import Evidence
        from ..schedules import occupied_mask

        if Role.SUPPLY_AIR_TEMP not in frame.columns or Role.OAT not in frame.columns:
            return None
        # the samples the fit uses: occupied, cooling (valve open) and a plausible SAT
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        keep = occupied_mask(
            frame.index,
            occ=occ if occ is not None and occ.notna().any() else None,
            warmup=frame[Role.WARMUP] if Role.WARMUP in frame.columns else None,
            cooldown=frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None,
        )
        if Role.COOL_VALVE in frame.columns:
            keep &= frame[Role.COOL_VALVE] > 5.0
        fan = self._fan(frame)[0]
        if fan is not None:
            keep &= fan.to_numpy(dtype=bool)  # built on this frame: same index
        sat = frame[Role.SUPPLY_AIR_TEMP]
        keep &= (sat > 40) & (sat < 90)
        derived = frame.loc[keep.to_numpy(), [Role.SUPPLY_AIR_TEMP, Role.OAT]]
        if derived.dropna().empty:
            return None
        return Evidence(
            renderer="oat_scatter",
            roles=[Role.SUPPLY_AIR_TEMP],
            title=f"{equip}: SAT vs OAT, occupied fan-on cooling hours (reset shape)",
            frame=derived,
        )
