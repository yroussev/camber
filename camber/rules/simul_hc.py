"""Rule #1: simultaneous heating and cooling at an AHU.

Both the heating and cooling coil open at once wastes energy directly. This rule
adapts the existing :func:`camber.ahu.analyze_ahu` math to the role-frame
interface: it needs the HEAT_VALVE and COOL_VALVE roles (occupancy/prep roles are
used opportunistically if present).

0.93 (#41): dehumidification with reheat looks the same at the valves -- the cooling coil runs
cold to wring moisture out, and the heating (post-heat) coil warms the dried air back to the
supply setpoint. So the rule classifies each both-open interval before counting it:

* **dehumidification with reheat** (not counted): the fan runs, the air leaving the cooling coil
  (``COOL_COIL_LEAVING_TEMP``) is at least ``reheat_lift_f`` below the supply-air temperature
  (the heat is added *after* the cooling coil), and the coil leaves at or below the entering
  air's dew point plus ``dewpoint_margin_f`` -- it is condensing moisture. The dew point comes
  from ``OAT`` + ``OUTDOOR_RH`` and ``RETURN_AIR_TEMP`` + ``RETURN_AIR_HUMIDITY`` (the lower of
  the two when both exist, so the test never flatters the coil). Where no dew point can be
  formed, a return-air humidity at or above ``humid_rh_pct`` stands in.
* **possibly dehumidification** (a caveat, never a fault): only part of that evidence exists --
  reheat after the coil with no humidity at all, or high humidity with no coil-leaving
  temperature to show the heat is reheat.
* **simultaneous heating and cooling** (counted): everything else, including a coil that leaves
  *above* the entering dew point (a dry coil removes no moisture, so reheating after it is
  coil-against-coil fighting) and any interval with the fan stopped.

``dehumidification`` declares the unit's sequence: ``True`` (the unit has a dehumidification
sequence) accepts reheat after the coil as dehumidification unless a dew point shows the coil
dry, and accepts high humidity alone when no coil-leaving temperature is trended; ``False`` (it
has none) counts every both-open interval, as before 0.93; ``None`` (unknown, the default)
applies the physical tests above. A unit with none of these signals is judged exactly as before.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..ahu import analyze_ahu
from ..model.roles import Role
from .base import Finding

# Map the roles this rule consumes to the column names analyze_ahu expects.
_ROLE_TO_AHU_COL = {
    Role.COOL_VALVE: "CHW_Valve",
    Role.HEAT_VALVE: "HHW_Valve",
    Role.OAT: "OSA",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.OA_DAMPER: "OA_Damper",
    Role.OCCUPANCY: "Occupancy",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}

_SEV_RANK = {"ok": 0, "info": 1, "warn": 2, "fault": 3}


# ============================================================================ 0.93 (#41)
def _dew_point_f(temp_f: pd.Series, rh_pct: pd.Series) -> pd.Series:
    """Dew point (°F) from dry-bulb (°F) and relative humidity (%) -- the Magnus approximation
    (coefficients 17.62 / 243.12 °C, good to ~0.2 °C over -45..60 °C). A 0-1 humidity is read as
    a fraction; readings outside 1-100 % are dropped."""
    from ..units import normalize_percent

    rh = normalize_percent(pd.Series(rh_pct, dtype=float))
    rh = rh.where((rh >= 1.0) & (rh <= 100.0))
    tc = (pd.Series(temp_f, dtype=float) - 32.0) * 5.0 / 9.0
    a, b = 17.62, 243.12
    g = np.log(rh / 100.0) + a * tc / (b + tc)
    return (b * g / (a - g)) * 9.0 / 5.0 + 32.0


def _fan_on(frame: pd.DataFrame) -> pd.Series | None:
    """Fan running, from the status (hourly mean > 0.5) or the speed (> 5 %); None if neither."""
    if Role.SUPPLY_FAN_STATUS in frame.columns and frame[Role.SUPPLY_FAN_STATUS].notna().any():
        return frame[Role.SUPPLY_FAN_STATUS].fillna(0.0) > 0.5
    if Role.SUPPLY_FAN_SPEED in frame.columns and frame[Role.SUPPLY_FAN_SPEED].notna().any():
        from ..units import normalize_percent

        return normalize_percent(frame[Role.SUPPLY_FAN_SPEED]).fillna(0.0) > 5.0
    return None


class SimultaneousHeatCool:
    """Detects an AHU with heating and cooling coils open at once (PNNL Re-tuning Ch.5).

    Both-open intervals that read as dehumidification with reheat are separated out (0.93, #41;
    see the module docstring): the fault is judged on the rest.
    """

    name = "simultaneous_heat_cool"
    roles_required = (Role.HEAT_VALVE, Role.COOL_VALVE)
    roles_optional = (
        Role.OAT,
        Role.RETURN_AIR_TEMP,
        Role.OA_DAMPER,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
        # 0.93 (#41): dehumidification-with-reheat evidence
        Role.COOL_COIL_LEAVING_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.OUTDOOR_RH,
        Role.RETURN_AIR_HUMIDITY,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(
        self,
        *,
        dehumidification: bool | None = None,
        reheat_lift_f: float = 2.0,
        dewpoint_margin_f: float = 2.0,
        humid_rh_pct: float = 55.0,
        fault_pct: float = 5.0,
        warn_pct: float = 1.0,
    ):
        if dehumidification not in (None, True, False):
            raise ValueError(
                f"dehumidification must be true, false or null, not {dehumidification!r}"
            )
        self.dehumidification = dehumidification
        self.reheat_lift_f = reheat_lift_f
        self.dewpoint_margin_f = dewpoint_margin_f
        self.humid_rh_pct = humid_rh_pct
        self.fault_pct = fault_pct
        self.warn_pct = warn_pct

    def _severity(self, pct: float) -> str:
        return "fault" if pct >= self.fault_pct else ("warn" if pct >= self.warn_pct else "ok")

    def _classes(self, frame: pd.DataFrame) -> tuple[dict | None, dict]:
        """``({"dehum": mask, "possible": mask}, evidence)`` -- the dehumidification classes of
        every interval (only both-open ones are counted by :func:`analyze_ahu`), or ``(None, ev)``
        when no evidence signal is trended or the sequence is declared absent."""
        idx = frame.index
        col = frame.get
        cclt = col(Role.COOL_COIL_LEAVING_TEMP)
        sat = col(Role.SUPPLY_AIR_TEMP)
        dps = []
        if col(Role.OAT) is not None and col(Role.OUTDOOR_RH) is not None:
            dps.append(_dew_point_f(frame[Role.OAT], frame[Role.OUTDOOR_RH]))
        if col(Role.RETURN_AIR_TEMP) is not None and col(Role.RETURN_AIR_HUMIDITY) is not None:
            dps.append(_dew_point_f(frame[Role.RETURN_AIR_TEMP], frame[Role.RETURN_AIR_HUMIDITY]))
        rh = None
        if col(Role.RETURN_AIR_HUMIDITY) is not None:
            from ..units import normalize_percent

            rh = normalize_percent(frame[Role.RETURN_AIR_HUMIDITY].astype(float))
        ev = {
            "coil_leaving_temp": cclt is not None and sat is not None,
            "dew_point": bool(dps),
            "humidity": rh is not None,
        }
        if self.dehumidification is False or not any(ev.values()):
            return None, ev

        fan = _fan_on(frame)
        fan = pd.Series(True, index=idx) if fan is None else fan.reindex(idx).fillna(False)
        nan = pd.Series(np.nan, index=idx)
        # reheat after the cooling coil: True / False / NaN (unknown)
        if ev["coil_leaving_temp"]:
            lift = (sat - cclt).astype(float)
            reheat = (lift >= self.reheat_lift_f).where(lift.notna())
        else:
            reheat = nan
        # the coil is condensing (latent work): True / False / NaN
        if ev["dew_point"]:
            dp = pd.concat(dps, axis=1).min(axis=1, skipna=True).reindex(idx)
        else:
            dp = nan
        if ev["coil_leaving_temp"]:
            wet = (cclt <= dp + self.dewpoint_margin_f).where(dp.notna() & cclt.notna())
        else:
            wet = nan
        humid = (rh >= self.humid_rh_pct).where(rh.notna()) if rh is not None else nan

        def t(s):
            return s.eq(True) & s.notna()

        def unknown(s):
            return s.isna()

        # proof of latent work: a wet coil; failing a dew point, high humidity
        latent = t(wet) | (unknown(wet) & t(humid))
        latent_unknown = unknown(wet) & unknown(humid)
        dry = wet.eq(False) & wet.notna()
        if self.dehumidification:  # declared: reheat after the coil is enough unless it is dry
            dehum = t(reheat) & ~dry
            possible = unknown(reheat) & t(humid) & ~dry
        else:
            dehum = t(reheat) & latent
            possible = (t(reheat) & latent_unknown) | (unknown(reheat) & t(humid) & ~dry)
        dehum = dehum & fan
        possible = possible & fan & ~dehum
        return {"dehum": dehum, "possible": possible}, ev

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
        classes, ev = self._classes(frame)
        # translate role columns -> the legacy measure-named frame analyze_ahu wants
        cols = {role: col for role, col in _ROLE_TO_AHU_COL.items() if role in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_ahu(legacy, equip, simul_classes=classes)
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        pct = res.simultaneous_hc_pct
        metrics = {
            "simultaneous_hc_pct": pct,
            "chw_open_pct": res.chw_open_pct,
            "hhw_open_pct": res.hhw_open_pct,
            "mean_overlap_when_simul": res.mean_overlap_when_simul,
            "n_considered": res.n_considered,
        }
        summary = (
            f"{equip}: both coils open {pct:.1f}% of occupied hours "
            f"(CHW {res.chw_open_pct:.0f}%, HHW {res.hhw_open_pct:.0f}%)"
        )
        caveats: list = []
        if classes is None or res.simul_class_pct is None:
            if self.dehumidification is False:
                metrics["dehumidification"] = False
            elif self._severity(pct) != "ok":
                caveats.append(
                    "dehumidification with reheat could not be ruled out: no cooling-coil leaving "
                    "temperature, humidity or dew point is trended -- declare the unit's sequence "
                    "with the rule parameter `dehumidification` (true / false)"
                )
            return Finding(
                rule=self.name,
                equip=equip,
                severity=self._severity(pct),
                metrics=metrics,
                caveats=caveats,
                summary=summary,
            )

        dehum = res.simul_class_pct["dehum"]
        possible = res.simul_class_pct["possible"]
        unexplained = round(max(0.0, pct - dehum - possible), 2)
        metrics.update(
            {
                "unexplained_hc_pct": unexplained,
                "dehum_reheat_pct": dehum,
                "dehum_possible_pct": possible,
                "dehumidification": self.dehumidification,
                "dehum_evidence": sorted(k for k, v in ev.items() if v),
            }
        )
        severity = self._severity(unexplained)
        if (
            possible > 0.0
            and _SEV_RANK[self._severity(unexplained + possible)] > _SEV_RANK[severity]
        ):
            # "possibly dehumidification" never makes a fault on its own (#41): it caps at warn
            severity = max(severity, "warn", key=_SEV_RANK.__getitem__)
        if possible > 0.0:
            caveats.append(
                f"{possible:.1f}% of occupied hours with both coils open may be dehumidification "
                "with reheat, but the evidence is incomplete ("
                + (
                    "no humidity or dew point to show the coil removing moisture"
                    if not (ev["dew_point"] or ev["humidity"])
                    else "no cooling-coil leaving temperature to show the heat is reheat"
                )
                + "); they are not counted as a fault -- confirm the sequence with the rule "
                "parameter `dehumidification`"
            )
        if dehum > 0.0:
            if self.dehumidification:
                why = "the declared dehumidification sequence, with the heat added after the coil"
            else:
                why = (
                    "the cooling coil leaving at or below the entering dew point and the heat "
                    "added after it"
                )
            caveats.append(
                f"{dehum:.1f}% of occupied hours with both coils open read as dehumidification "
                f"with reheat ({why}) and are not counted; reheat for dehumidification still "
                "costs energy -- check the humidity setpoint it holds"
            )
            if self.dehumidification is None and severity == "ok":
                severity = "info"  # a physical reading of an undeclared sequence: say so
        if dehum > 0.0 or possible > 0.0:
            parts = [f"{dehum:.1f}% dehumidification with reheat"]
            if possible > 0.0:
                parts.append(f"{possible:.1f}% possibly so")
            summary += f"; {', '.join(parts)}; {unexplained:.1f}% unexplained"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            caveats=caveats,
            summary=summary,
        )

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The samples this rule counts as a fault (0.102, #114): occupied, both valves above the
        5 % open threshold, and -- where the dehumidification classes apply -- neither
        dehumidification with reheat nor possibly so (those are reported, not counted). A boolean
        Series on ``frame``'s (de-duplicated) index."""
        from ..ahu import _simultaneous_hc_mask

        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
        classes, _ev = self._classes(frame)
        cols = {role: col for role, col in _ROLE_TO_AHU_COL.items() if role in frame.columns}
        exclude = None
        if classes is not None:
            exclude = classes["dehum"] | classes["possible"]
        return _simultaneous_hc_mask(frame.rename(columns=cols), exclude=exclude)

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the heat-vs-cool valve diagnostic, the samples the rule counts shaded.

        0.102 (#114): the template is on the percent scale with the rule's own 5 % threshold, and
        the shaded points are :meth:`violation_mask` -- the band alone cannot see the occupancy
        gate or the dehumidification classes."""
        from ..charts.diagnostic import no_simultaneous_template
        from ..charts.evidence import Evidence

        if Role.HEAT_VALVE in frame.columns and Role.COOL_VALVE in frame.columns:
            return Evidence(
                renderer="diagnostic",
                template=no_simultaneous_template(active=5.0),
                mask=self.violation_mask(frame),
                label="both valves open (counted)",
                title=f"{equip}: simultaneous heat/cool",
            )
        return None
