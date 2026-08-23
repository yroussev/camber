"""Rule: is a G36 setpoint **reset** actually trimming-and-responding, or inert?

Where :class:`camber.rules.satreset_compliance_rule.SupplyAirResetCompliance` asks the *target*
question (is supply air colder than the G36 OAT-SAT map would command, needing only SAT+OAT), this
rule asks the *mechanism* question: given the plant's own **reset-request** count, does the actual
reset setpoint follow the Trim-&-Respond trajectory those requests imply (ASHRAE Guideline 36
§5.1.14)? It reconstructs the expected setpoint with :func:`camber.g36_reset.tr_simulate` and
compares it to the trended setpoint, flagging four failure modes:

* **stuck** — the setpoint barely moves while the request pattern would have moved it (a frozen or
  overridden reset);
* **not responding** — under sustained demand the setpoint sits at the energy-saving (trim) end
  instead of responding toward demand (zones starve);
* **not trimming** — while zones are idle the setpoint stays parked at the demand end (energy
  wasted);
* **diverges** — the setpoint moves the *opposite* way to the T&R command on most cycles (an
  inverted or mis-wired reset).

The rule is **reset-agnostic**: instantiate it with ``reset="sat"`` (supply-air-temp setpoint in °F,
`SAT_TR` preset) or ``reset="static"`` (duct-static setpoint in in. w.c., `STATIC_TR` preset). It
needs both the reset **setpoint** and the aggregated per-cycle **request** count mapped, so unlike
the compliance rule it declines on the common trend export that lacks a request point. Because it
takes a constructor argument it is registered as explicit instances (see ``rules/builtin.py``), not
auto-constructed. Thresholds are screening / opportunity-grade (provisional-untuned).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..g36_reset import SAT_TR, STATIC_TR, TRParams, reset_effectiveness
from ..model.roles import Role
from .base import Finding

# reset key -> (setpoint role, request-count role, T&R params, unit label, human label)
_RESETS: dict[str, tuple[Role, Role, TRParams, str, str]] = {
    "sat": (Role.SUPPLY_AIR_TEMP_SP, Role.SAT_RESET_REQUESTS, SAT_TR, "degF", "supply-air-temp"),
    "static": (
        Role.DUCT_STATIC_SP,
        Role.STATIC_PRESSURE_REQUESTS,
        STATIC_TR,
        "in. w.c.",
        "duct-static",
    ),
}

# human-facing tail per failure mode
_REASONS = {
    "stuck": "reset is stuck (setpoint flat while requests demand movement)",
    "not_responding": "reset not responding (parked at the energy-saving end under demand)",
    "not_trimming": "reset not trimming (parked at the demand end while idle -- energy wasted)",
    "diverges": "reset diverges (setpoint moves the wrong way vs the T&R command)",
}


class ResetEffectiveness:
    """Flags a G36 setpoint reset that is not actually trimming-and-responding to its requests.

    Reconstructs the expected Trim-&-Respond setpoint trajectory from the plant's own reset-request
    count (`tr_simulate`) and compares it to the trended reset setpoint, warning when the reset is
    **stuck**, **not responding**, **not trimming**, or **diverging** (see the module docstring).
    ``reset`` selects the family: ``"sat"`` (supply-air-temp, °F) or ``"static"`` (duct-static, in.
    w.c.). Needs the reset setpoint **and** the aggregated per-cycle request count mapped, so it
    declines loudly when either is absent. Two-sided (both starving and wasting are faults);
    warn-level (an operational opportunity, not a hard equipment fault). Screening-grade thresholds.
    """

    def __init__(
        self,
        reset: str = "sat",
        *,
        min_cycles: int = 12,
        flat_frac: float = 0.10,  # "flat" actual range, as a fraction of the reset band
        expected_move_frac: float = 0.25,  # expected range must clear this to call "stuck"
        pinned_frac: float = 0.15,  # "parked at an end" tolerance, fraction of band
        mode_frac: float = 0.60,  # fraction of cycles at an end to call not-responding/trimming
        min_mode_cycles: int = 10,  # min high-demand / idle cycles before judging that mode
        wrong_dir_frac: float = 0.50,  # fraction of wrong-way moved cycles to call diverges
    ):
        if reset not in _RESETS:
            raise ValueError(f"reset must be one of {sorted(_RESETS)}, got {reset!r}")
        self.reset = reset
        sp_role, req_role, params, unit, label = _RESETS[reset]
        self._sp_role = sp_role
        self._req_role = req_role
        self._params = params
        self._unit = unit
        self._label = label
        self.name = f"{reset}_reset_effectiveness"
        self.roles_required: tuple[Role, ...] = (sp_role, req_role)
        self.roles_optional: tuple[Role, ...] = ()
        self._kwargs: dict[str, Any] = {
            "min_cycles": min_cycles,
            "flat_frac": flat_frac,
            "expected_move_frac": expected_move_frac,
            "pinned_frac": pinned_frac,
            "mode_frac": mode_frac,
            "min_mode_cycles": min_mode_cycles,
            "wrong_dir_frac": wrong_dir_frac,
        }

    def _decline(self, equip: str, reason: str, caveat: str) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {reason}",
            caveats=[caveat],
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Score the reset setpoint against its request-implied T&R trajectory; return a Finding."""
        missing = [r for r in self.roles_required if r not in frame.columns]
        if missing:
            names = ", ".join(str(r) for r in missing)
            return self._decline(
                equip,
                f"missing {names} -- reset-effectiveness needs both setpoint and request count",
                f"{self._label} reset effectiveness not evaluated: {names} unmapped "
                "(the aggregated per-cycle reset-request point is required)",
            )

        res = reset_effectiveness(
            frame,
            equip,
            sp_col=self._sp_role,
            requests_col=self._req_role,
            params=self._params,
            unit=self._unit,
            **self._kwargs,
        )
        if res is None:
            return self._decline(
                equip,
                "insufficient usable setpoint+request rows",
                f"{self._label} reset effectiveness not evaluated: too few in-range rows",
            )

        severity = "ok" if res.effective else "warn"
        metrics = {
            "reset": self.reset,
            "effective": res.effective,
            "reason": res.reason,
            "stuck": res.stuck,
            "not_responding": res.not_responding,
            "not_trimming": res.not_trimming,
            "diverges": res.diverges,
            "actual_sp_range": res.actual_sp_range,
            "expected_sp_range": res.expected_sp_range,
            "mean_abs_error_sp": res.mean_abs_error_sp,
            "pct_cycles_wrong_direction": res.pct_cycles_wrong_direction,
            "pct_high_demand_unresponsive": res.pct_high_demand_unresponsive,
            "pct_idle_untrimmed": res.pct_idle_untrimmed,
            "n": res.n,
        }
        if res.effective:
            summary = (
                f"{equip}: {self._label} reset tracks its request-implied T&R trajectory "
                f"(actual swing {res.actual_sp_range:g} vs expected {res.expected_sp_range:g} "
                f"{self._unit}) -- effective"
            )
        else:
            summary = (
                f"{equip}: {self._label} reset -- {_REASONS.get(res.reason, res.reason)}; "
                f"actual swing {res.actual_sp_range:g} vs expected {res.expected_sp_range:g} "
                f"{self._unit} over {res.n} cycles"
            )
        return Finding(
            rule=self.name, equip=equip, severity=severity, metrics=metrics, summary=summary
        )
