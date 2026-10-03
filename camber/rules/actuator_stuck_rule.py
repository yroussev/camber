"""Rule: a terminal or fan-coil actuator stuck at one position (0.98, #85).

A damper or valve that holds one position for hours is not a fault by itself: a VAV box at its
minimum in a mild zone, or a valve held shut all summer, sits still because nothing asks it to
move. On the ORNL test building the healthy boxes hold a constant mid-stroke position (26-41 %) for
4-10.5 h on 13 of 63 box-days (15-minute data), so a plain "flat for four hours" test flags
them. The rule therefore judges every flat run by **what the zone asked for** while it lasted.

**Flat runs.** The signal is put on a 0-100 % scale and rounded to ``tol_pct``. Runs of one value
are found over the *active* samples only -- occupied (a trended ``OCCUPANCY`` point, else the
``start_hour``/``end_hour``/``occupied_days`` schedule) and, when a fan status is mapped, fan on --
and a run breaks where the active stretch ends. Runs shorter than ``min_flat_hours`` are ignored.

**Tiers.** Each run of at least ``min_flat_hours`` is one of:

* **contradicted** (can reach ``fault``) -- the zone or the controller asked for a different
  position:

  - a damper at its closed limit (<= ``limit_pct``) while occupied, with the airflow at or below
    5 % of its setpoint (``AIRFLOW_SP``, else the ``min_airflow`` parameter). With neither, the
    occupied mode itself is the demand (an occupied box owes its ventilation minimum), and the
    finding says so in a caveat;
  - a damper or cooling valve below its open limit while the zone runs more than ``warm_margin_f``
    over its cooling setpoint for at least 25 % of the run (a heating valve: below its heating
    setpoint by that margin);
  - a damper or cooling valve at its open limit (>= 100 - ``limit_pct``) while the zone sits at
    least ``satisfied_margin_f`` below its cooling setpoint for at least 50 % of the run (a heating
    valve: that far above its heating setpoint);
  - a valve position flat while its demand twin (``HEAT_VALVE`` beside ``HEAT_VALVE_POSITION``)
    moves at least 20 points.

* **unexplained_flat** (``warn`` at most) -- one value for at least ``whole_day_share`` of a day's
  active samples while something that should move it moved. The driver is the first of: the
  demand twin (>= 5 points), the airflow setpoint (dampers; >= 5 % of its peak), the heating or
  cooling setpoint, the space temperature (each >= ``min_driver_span_f``). A run at a limit the
  demand agrees with (fully open while the zone is warm, shut while it is satisfied) is saturated,
  not stuck, and stays quiet.

* **consistent** -- anything else; ignored.

**Severity.** ``fault`` when contradicted runs cover at least ``fault_pct`` % of the active
samples, ``warn`` when flagged runs (either tier) cover at least ``warn_pct`` %, ``info`` when a
run was flagged below that, else ``ok``. Each actuator is judged on its own and the worst decides.

**Limits.** A box whose zone is saturated in the direction it is stuck (stuck fully open on a hot
day, so the zone is warm and the open damper looks right) cannot be told from a box doing its job:
on the full ORNL set the room-106 box stuck at 100 % is missed that way. A flat run between
``min_flat_hours`` and a whole day, at a mid-stroke position the zone does not contradict, is
consistent by construction.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import effective_occupied_mask
from ._flat_runs import flat_runs, percent_signal
from .base import Finding

# the roles the rule can judge, by config name; a heating-valve position falls back to the demand
_ROLE_BY_NAME = {
    "damper": Role.DAMPER,
    "heat_valve_position": Role.HEAT_VALVE_POSITION,
    "heat_valve": Role.HEAT_VALVE,
    "cool_valve": Role.COOL_VALVE,
}
_FALLBACK = {Role.HEAT_VALVE_POSITION: Role.HEAT_VALVE}
_TWIN = {Role.HEAT_VALVE_POSITION: Role.HEAT_VALVE}  # position -> the controller's demand
_HEATING = frozenset({Role.HEAT_VALVE_POSITION, Role.HEAT_VALVE})  # opens on a cold zone

_WARM_SHARE = 0.25  # share of a run the zone must be out of band on the actuator's side
_SATISFIED_SHARE = 0.50  # share of a run the zone must be satisfied while the actuator is open
_TWIN_MOVE_PCT = 20.0  # demand range (points) over a flat position run that contradicts it
_CLOSED_FLOW_FRAC = 0.05  # airflow at/below this share of its setpoint: the box delivers nothing
_DRIVER_PCT_SPAN = 5.0  # a percent driver (demand twin) "moves" when its range reaches this
_DRIVER_FLOW_FRAC = 0.05  # an airflow setpoint "moves" when its range reaches this share of peak
_MAX_LISTED_RUNS = 10

_TIER_ORDER = {"consistent": 0, "unexplained_flat": 1, "contradicted": 2}


def _roles_from(roles) -> tuple:
    """Validate ``roles`` (names or :class:`Role`) into a tuple of Roles."""
    if isinstance(roles, (str, Role)):
        roles = (roles,)
    out = []
    for r in roles:
        role = r if isinstance(r, Role) else _ROLE_BY_NAME.get(str(r).strip().lower())
        if role not in _ROLE_BY_NAME.values():
            raise ValueError(
                f"actuator_stuck roles must be among {sorted(_ROLE_BY_NAME)}, not {r!r}"
            )
        if role not in out:
            out.append(role)
    if not out:
        raise ValueError("actuator_stuck needs at least one role")
    return tuple(out)


def _num(frame: pd.DataFrame, role: Role) -> pd.Series | None:
    if role not in frame.columns:
        return None
    s = pd.to_numeric(frame[role], errors="coerce")
    return s if s.notna().any() else None


def _span(s: pd.Series | None) -> float | None:
    if s is None:
        return None
    v = s.dropna()
    return float(v.max() - v.min()) if len(v) else None


class ActuatorStuck:
    """Flags a terminal or fan-coil damper or valve held at one position against the zone's
    demand (PNNL Re-tuning Ch.7)."""

    name = "actuator_stuck"
    roles_required = ()
    roles_optional = (
        Role.SPACE_TEMP,
        Role.COOL_SP,
        Role.HEAT_SP,
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.OCCUPANCY,
        Role.SUPPLY_FAN_STATUS,
    )

    def __init__(
        self,
        *,
        roles=("damper", "heat_valve_position", "cool_valve"),
        tol_pct: float = 0.5,
        min_flat_hours: float = 4.0,
        whole_day_share: float = 0.98,
        limit_pct: float = 2.0,
        min_driver_span_f: float = 1.0,
        warm_margin_f: float = 1.0,
        satisfied_margin_f: float = 2.0,
        min_airflow: float | None = None,
        warn_pct: float = 10.0,
        fault_pct: float = 50.0,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
    ):
        self.roles = _roles_from(roles)
        self.tol_pct = float(tol_pct)
        self.min_flat_hours = float(min_flat_hours)
        self.whole_day_share = float(whole_day_share)
        self.limit_pct = float(limit_pct)
        self.min_driver_span_f = float(min_driver_span_f)
        self.warm_margin_f = float(warm_margin_f)
        self.satisfied_margin_f = float(satisfied_margin_f)
        self.min_airflow = None if min_airflow is None else float(min_airflow)
        self.warn_pct = float(warn_pct)
        self.fault_pct = float(fault_pct)
        if not 0.0 <= self.warn_pct <= self.fault_pct:
            raise ValueError(
                f"actuator_stuck needs 0 <= warn_pct <= fault_pct, got {warn_pct}, {fault_pct}"
            )
        if not 0.0 < self.whole_day_share <= 1.0:
            raise ValueError(f"whole_day_share must be in (0, 1], not {whole_day_share!r}")
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)
        group = list(self.roles)
        for r in self.roles:
            fb = _FALLBACK.get(r)
            if fb is not None and fb not in group:
                group.append(fb)
        # each actuator is optional on its own, but at least one must be mapped
        self.roles_any_of = (tuple(group),)

    # ------------------------------------------------------------------ helpers
    def _active(self, frame: pd.DataFrame) -> tuple:
        """``(active mask, basis)``: occupied (and fan-on when a fan status is mapped)."""
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        trended = occ is not None and pd.Series(occ).notna().any()
        mask = effective_occupied_mask(
            frame.index,
            occ=occ,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
        )
        mask = pd.Series(mask, index=frame.index).fillna(False).astype(bool)
        fan = _num(frame, Role.SUPPLY_FAN_STATUS)
        if fan is not None:
            mask &= (fan > 0.5).fillna(False)
        return mask, ("trended occupancy" if trended else "assumed schedule")

    def _resolve_role(self, frame: pd.DataFrame, role: Role):
        if _num(frame, role) is not None:
            return role
        fb = _FALLBACK.get(role)
        if fb is not None and fb not in self.roles and _num(frame, fb) is not None:
            return fb
        return None

    def _limit(self, value: float) -> str:
        if value <= self.limit_pct:
            return "closed"
        if value >= 100.0 - self.limit_pct:
            return "open"
        return "mid"

    def _judge(self, role: Role, run, sel: pd.Series, frame, day_n: int, twin) -> dict:
        """Classify one flat run: ``{"tier", "reason", "driver", "detail"}``."""
        value = float(run.value)
        limit = self._limit(value)
        heating = role in _HEATING
        st = _num(frame, Role.SPACE_TEMP)
        csp = _num(frame, Role.COOL_SP)
        hsp = _num(frame, Role.HEAT_SP)
        n = int(sel.sum())
        out = {"tier": "consistent", "reason": None, "driver": None, "detail": None}

        # a flat position while its demand moves
        if twin is not None:
            tspan = _span(twin[sel])
            if tspan is not None and tspan >= _TWIN_MOVE_PCT:
                return {
                    **out,
                    "tier": "contradicted",
                    "reason": "demand_moved",
                    "detail": round(tspan, 1),
                }

        # a damper closed through occupied hours
        if role is Role.DAMPER and limit == "closed":
            flow = _num(frame, Role.AIRFLOW)
            ref = None
            sp = _num(frame, Role.AIRFLOW_SP)
            if sp is not None and sp[sel].notna().any():
                ref = float(sp[sel].median())
            elif self.min_airflow is not None:
                ref = self.min_airflow
            if ref is None:
                return {**out, "tier": "contradicted", "reason": "closed_occupied"}
            if ref > 0 and (flow is None or float(flow[sel].median()) <= _CLOSED_FLOW_FRAC * ref):
                frac = None if flow is None else round(float(flow[sel].median()) / ref, 3)
                return {**out, "tier": "contradicted", "reason": "closed_no_flow", "detail": frac}

        # the zone out of band on the actuator's side
        if st is not None and limit != "open":
            if heating and hsp is not None:
                gap = (hsp - st)[sel]
                side = "cold"
            elif not heating and csp is not None:
                gap = (st - csp)[sel]
                side = "warm"
            else:
                gap = None
            if gap is not None and n:
                hit = gap > self.warm_margin_f
                if float(hit.sum()) / n >= _WARM_SHARE:
                    return {
                        **out,
                        "tier": "contradicted",
                        "reason": f"zone_{side}",
                        "detail": round(float(gap[hit].median()), 1),
                    }

        # fully open while the zone is satisfied
        if st is not None and limit == "open":
            if heating and hsp is not None:
                gap = (st - hsp)[sel]
            elif not heating and csp is not None:
                gap = (csp - st)[sel]
            else:
                gap = None
            if gap is not None and n:
                hit = gap >= self.satisfied_margin_f
                if float(hit.sum()) / n >= _SATISFIED_SHARE:
                    return {
                        **out,
                        "tier": "contradicted",
                        "reason": "zone_satisfied",
                        "detail": round(float(gap[hit].median()), 1),
                    }

        # one value nearly all day while something that should move it moved
        if day_n <= 0 or n / day_n < self.whole_day_share:
            return out
        if limit != "mid" and self._saturated(limit, heating, sel, st, csp, hsp, twin):
            return out
        driver, span = self._driver(role, sel, frame, twin, st, csp, hsp)
        if driver is None:
            return out
        return {
            **out,
            "tier": "unexplained_flat",
            "reason": "flat_all_day",
            "driver": driver,
            "detail": span,
        }

    def _saturated(self, limit, heating, sel, st, csp, hsp, twin) -> bool:
        """True when the demand agrees with a run at a limit (saturated, not stuck)."""
        if twin is not None and twin[sel].notna().any():
            med = float(twin[sel].median())
            return med >= 100.0 - self.limit_pct if limit == "open" else med <= self.limit_pct
        sp = hsp if heating else csp
        if st is None or sp is None:
            return False
        need = (sp - st)[sel] if heating else (st - sp)[sel]  # > 0: the zone wants this actuator
        need = need.dropna()
        if need.empty:
            return False
        share_need = float((need >= 0).mean())
        return share_need >= 0.5 if limit == "open" else share_need < 0.5

    def _driver(self, role, sel, frame, twin, st, csp, hsp):
        """The first moving driver of a flat run as ``(name, span)``, else ``(None, None)``."""
        if twin is not None:
            s = _span(twin[sel])
            if s is not None and s >= _DRIVER_PCT_SPAN:
                return "demand", round(s, 1)
        if role is Role.DAMPER:
            sp = _num(frame, Role.AIRFLOW_SP)
            if sp is not None:
                s = _span(sp[sel])
                peak = float(sp.abs().max())
                if s is not None and peak > 0 and s >= _DRIVER_FLOW_FRAC * peak:
                    return "airflow_sp", round(s, 1)
        for name, series in (("cool_sp", csp), ("heat_sp", hsp)):
            if series is not None:
                s = _span(series[sel])
                if s is not None and s >= self.min_driver_span_f:
                    return name, round(s, 2)
        if st is not None:
            s = _span(st[sel])
            if s is not None and s >= self.min_driver_span_f:
                return "space_temp", round(s, 2)
        return None, None

    # ------------------------------------------------------------------ analysis
    def _judge_role(self, role: Role, frame: pd.DataFrame, active: pd.Series) -> dict:
        sig = percent_signal(frame[role], self.tol_pct)
        twin_role = _TWIN.get(role)
        twin = None
        if twin_role is not None and _num(frame, twin_role) is not None:
            twin = percent_signal(frame[twin_role], 0.0)
        valid = active & sig.notna()
        n_active = int(valid.sum())
        runs = flat_runs(frame[role], active, tol_pct=self.tol_pct)
        longest = float(runs["hours"].max()) if len(runs) else 0.0
        runs = runs[runs["hours"] >= self.min_flat_hours] if len(runs) else runs
        dates = pd.Series(frame.index.normalize(), index=frame.index)
        day_counts = valid.groupby(dates).sum()
        flagged = []
        for run in runs.itertuples():
            sel = valid & (frame.index >= run.start) & (frame.index <= run.end)
            sel &= sig.eq(run.value)
            day_n = int(day_counts.get(pd.Timestamp(run.start).normalize(), 0))
            verdict = self._judge(role, run, sel, frame, day_n, twin)
            if verdict["tier"] == "consistent":
                continue
            flagged.append(
                {
                    "start": str(run.start),
                    "end": str(run.end),
                    "hours": round(float(run.hours), 2),
                    "value": float(run.value),
                    "n": int(sel.sum()),
                    **verdict,
                }
            )
        c_n = sum(r["n"] for r in flagged if r["tier"] == "contradicted")
        all_n = sum(r["n"] for r in flagged)
        c_share = 100.0 * c_n / n_active if n_active else 0.0
        share = 100.0 * all_n / n_active if n_active else 0.0
        worst = max(flagged, key=lambda r: (_TIER_ORDER[r["tier"]], r["n"])) if flagged else None
        if worst is not None and worst["tier"] == "contradicted" and c_share >= self.fault_pct:
            sev = "fault"
        elif worst is not None and share >= self.warn_pct:
            sev = "warn"
        elif worst is not None:
            sev = "info"
        else:
            sev = "ok"
        flagged.sort(key=lambda r: r["start"])
        return {
            "severity": sev,
            "metrics": {
                "flat_runs": len(flagged),
                "stuck_share": round(share, 2),
                "contradicted_share": round(c_share, 2),
                "value": worst["value"] if worst else None,
                "tier": worst["tier"] if worst else None,
                "reason": worst["reason"] if worst else None,
                "driver": worst["driver"] if worst else None,
                "longest_flat_hours": round(longest, 2),
                "n_active": n_active,
                "runs": flagged[:_MAX_LISTED_RUNS],
            },
            "worst": worst,
        }

    def _phrase(self, role: Role, worst: dict) -> str:
        label = {
            Role.DAMPER: "damper",
            Role.HEAT_VALVE_POSITION: "heating valve (measured position)",
            Role.HEAT_VALVE: "heating valve",
            Role.COOL_VALVE: "cooling valve",
        }[role]
        head = f"{label} held at {worst['value']:g} % for {worst['hours']:g} h"
        d = worst["detail"]
        reason = worst["reason"]
        if reason == "zone_warm":
            return f"{head} while the zone ran {d:g} °F over its cooling setpoint"
        if reason == "zone_cold":
            return f"{head} while the zone ran {d:g} °F under its heating setpoint"
        if reason == "zone_satisfied":
            sp = "heating" if role in _HEATING else "cooling"
            side = "above" if role in _HEATING else "below"
            return f"{head} fully open while the zone sat {d:g} °F {side} its {sp} setpoint"
        if reason == "closed_no_flow":
            flow = f" (airflow {d:.0%} of its setpoint)" if d is not None else ""
            return f"{head} shut through occupied hours{flow}"
        if reason == "closed_occupied":
            return f"{head} shut through occupied hours"
        if reason == "demand_moved":
            return f"{head} while its demand moved {d:g} points"
        driver = {
            "demand": "its demand",
            "airflow_sp": "its airflow setpoint",
            "cool_sp": "the cooling setpoint",
            "heat_sp": "the heating setpoint",
            "space_temp": "the zone temperature",
        }.get(worst["driver"], "its driver")
        unit = (
            " points"
            if worst["driver"] == "demand"
            else ("" if worst["driver"] == "airflow_sp" else " °F")
        )
        return f"{head}, the whole occupied day, while {driver} moved {d:g}{unit}"

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on a terminal / fan-coil role-frame; return a Finding."""
        active, basis = self._active(frame)
        per_role: dict = {}
        caveats: list = []
        judged = []
        for want in self.roles:
            role = self._resolve_role(frame, want)
            if role is None or role.value in per_role:
                continue
            res = self._judge_role(role, frame, active)
            per_role[role.value] = res["metrics"]
            judged.append((role, res))
        if not judged:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no actuator signal with data",
            )
        if not any(r["metrics"]["n_active"] for _, r in judged):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"roles": per_role, "active_basis": basis},
                summary=f"{equip}: no active (occupied, fan-on) samples to judge",
            )
        order = {"ok": 0, "info": 1, "warn": 2, "fault": 3}
        role, res = max(
            judged, key=lambda rr: (order[rr[1]["severity"]], rr[1]["metrics"]["stuck_share"])
        )
        sev = res["severity"]
        if Role.SPACE_TEMP not in frame.columns:
            caveats.append(
                "no space temperature: the zone-demand checks (zone warm, zone satisfied) were not "
                "evaluated, so a flat run is judged only on airflow, its demand twin and the "
                "whole-day test"
            )
        for r, rr in judged:
            if rr["metrics"]["reason"] == "closed_occupied":
                caveats.append(
                    f"{r.value} shut through occupied hours, judged against the occupied mode "
                    "alone (no AIRFLOW_SP mapped and no min_airflow set): a box with a zero "
                    "minimum airflow may close legitimately -- set min_airflow to confirm"
                )
        if basis == "assumed schedule":
            caveats.append(
                "no occupancy point: active hours are the assumed schedule "
                f"({self.start_hour:g}-{self.end_hour:g} h on days {list(self.occupied_days)})"
            )
        if sev in ("ok", "info") and res["worst"] is None:
            longest = max(r["metrics"]["longest_flat_hours"] for _, r in judged)
            summary = (
                f"{equip}: no actuator held still against the zone's demand "
                f"(longest flat run {longest:g} h)"
            )
        else:
            summary = f"{equip}: {self._phrase(role, res['worst'])}"
            m = res["metrics"]
            summary += f" ({m['stuck_share']:.0f} % of active samples flagged)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                **{k: v for k, v in res["metrics"].items() if k != "runs"},
                "role": role.value,
                "roles": per_role,
                "active_basis": basis,
            },
            summary=summary,
            caveats=caveats,
        )
