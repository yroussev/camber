"""Rule: supply-air-temperature reset **compliance** with the G36 OAT target.

Complementary to :class:`camber.rules.satreset_rule.SupplyAirReset`. That rule asks the *shape*
question -- does supply air get reset **upward at all** as cooling load drops (a positive slope)?
This rule asks the *target* question -- is supply air held **colder than the specific ASHRAE
Guideline-36 §5.16.2.2.b OAT→SAT map** would command? The two are complementary: a plant can have a
healthy reset slope yet still sit below the G36 target (and vice-versa). Supply air held colder than
the reset target sustains terminal reheat and wastes energy, so a persistent below-target gap is a
**reheat/energy opportunity** (a warn, not a hard fault).

It compares supply air to the OAT-**computed** target (via
:func:`camber.g36_reset.oat_sat_setpoint`), **not** to a mapped ``SUPPLY_AIR_TEMP_SP`` -- so it
works on typical trend exports that carry only SAT and OAT. It wraps the existing
:func:`camber.g36_reset.sat_reset_compliance` analyzer unchanged; OAT is building-level and arrives
via the runner's ``shared`` channel. The G36 map parameters
(``min_clg_sat`` / ``t_max`` / ``oat_min`` / ``oat_max``) are constructor arguments so a site can
match its own reset schedule.
"""

from __future__ import annotations

import pandas as pd

from ..g36_reset import sat_reset_compliance
from ..model.roles import Role
from .base import Finding


class SupplyAirResetCompliance:
    """Flags supply air held colder than the G36 OAT-based reset target (a reheat/energy chance).

    The G36 §5.16.2.2.b OAT→SAT map holds SAT at ``min_clg_sat`` when it is hot out (OAT ≥
    ``oat_max``) and resets it up toward ``t_max`` when it is cool (OAT ≤ ``oat_min``);
    ``min_clg_sat=55`` / ``t_max=65`` °F over an OAT band of 60→70 °F are the G36 defaults. It warns
    when supply air runs
    below that target ``warn_pct`` % of the hours **and** the mean gap clears ``warn_gap_f`` (so a
    trivially small persistent gap does not warn). Instantaneous; auto-registered; needs no store.
    """

    name = "supply_air_reset_compliance"
    roles_required = (Role.SUPPLY_AIR_TEMP,)
    roles_optional = (Role.OAT,)

    def __init__(
        self,
        *,
        min_clg_sat: float = 55.0,  # G36 Min_ClgSAT (°F)
        t_max: float = 65.0,  # G36 upper reset bound (°F)
        oat_min: float = 60.0,  # reset band low end (°F)
        oat_max: float = 70.0,  # reset band high end (°F)
        tol_f: float = 1.0,  # per-sample "below target" tolerance (°F)
        warn_pct: float = 40.0,  # warn when below target this % of hours ...
        warn_gap_f: float = 2.0,  # ... AND the mean gap clears this floor (°F)
    ):
        self.tol_f = tol_f
        self.warn_pct = warn_pct
        self.warn_gap_f = warn_gap_f
        self._reset_kwargs = {
            "min_clg_sat": min_clg_sat,
            "t_max": t_max,
            "oat_min": oat_min,
            "oat_max": oat_max,
        }

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Score supply air against the G36 OAT-reset target; return a Finding."""
        if Role.OAT not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "no OAT -- cannot compute the G36 target"},
                summary=f"{equip}: declined -- G36 SAT-reset target needs OAT (unmapped)",
                caveats=[
                    "SAT-reset compliance not evaluated: outdoor-air temperature (building-level, "
                    "via the shared channel) is required to compute the G36 reset target"
                ],
            )

        res = sat_reset_compliance(
            frame,
            equip,
            sat_col=Role.SUPPLY_AIR_TEMP,
            oat_col=Role.OAT,
            tol_f=self.tol_f,
            **self._reset_kwargs,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "insufficient in-range SAT+OAT rows (<10)"},
                summary=f"{equip}: declined -- too few valid SAT/OAT samples for G36 compliance",
                caveats=["SAT-reset compliance not evaluated: fewer than 10 usable rows"],
            )

        # One-sided opportunity: only supply air *colder* than the target is avoidable reheat;
        # running warmer than Min_ClgSAT is the energy-saving direction and stays ok.
        opportunity = (
            res.pct_below_g36_target >= self.warn_pct and res.mean_gap_f >= self.warn_gap_f
        )
        severity = "warn" if opportunity else "ok"
        metrics = {
            "pct_below_g36_target": res.pct_below_g36_target,
            "mean_gap_f": res.mean_gap_f,
            "actual_sat_median": res.actual_sat_median,
            "g36_target_median": res.g36_target_median,
            "n": res.n,
            "warn_pct_threshold": self.warn_pct,
            "warn_gap_floor_f": self.warn_gap_f,
        }
        tail = "reheat/energy opportunity" if severity == "warn" else "tracks the G36 reset target"
        summary = (
            f"{equip}: SAT median {res.actual_sat_median:.1f}°F vs G36 target "
            f"{res.g36_target_median:.1f}°F; below target {res.pct_below_g36_target:.0f}% of hours "
            f"(mean gap {res.mean_gap_f:+.1f}°F) -- {tail}"
        )
        return Finding(
            rule=self.name, equip=equip, severity=severity, metrics=metrics, summary=summary
        )
