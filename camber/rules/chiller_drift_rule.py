"""Rule: chiller approach **drift** -- a current period scored against a frozen baseline.

:class:`~camber.rules.chiller_approach_rule.ChillerApproachFouling` answers "is the approach high?"
by comparing a whole-window median to a static design constant. This rule answers the different and
usually earlier question, "has the approach been climbing?" -- the signal that shows up weeks before
a heat exchanger's condition becomes a work order. Both ship: a plant can fail either way, and a
chiller can drift substantially while still sitting inside its design band.

The comparison is made **at matched load**. Approach widens with tons on its own, so a busier
current period looks like degradation to any level-vs-level test; scoring the current period's
residuals against a fitted ``approach ~ f(tons)`` line removes that confound
(:mod:`camber.chillerbaseline`).

The reference is **frozen**, not rolling (:mod:`camber.store.modelstore`). A baseline refit from the
window being judged would define away the very drift it is meant to catch. The baseline is fit once
over the supplied baseline period, written with provenance, and thereafter only read; moving it
takes an explicit, attributed ``accept_new_normal`` by an operator.

Both heat-exchanger legs are scored when present -- the condenser leg is required, the evaporator
leg enriches -- and the worst drives severity, mirroring the fouling rule's shape.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import drift_stats, fit_approach_baseline, tons_from_flow
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}

_LEGS = (
    (Role.COND_APPROACH_TEMP, "cond", "condenser"),
    (Role.EVAP_APPROACH_TEMP, "evap", "evaporator"),
)

_RANK = {"ok": 0, "info": 1, "warn": 2, "fault": 3}

# ---------------------------------------------------------------------------------------------
# PROVISIONAL THRESHOLDS -- NOT YET VALIDATED AGAINST FIELD DATA.
#
# These are engineering-judgement starting points, not measurements. They have never been checked
# against real chiller trend data with confirmed fouling events, so they should be treated as a
# placeholder to be tuned before anyone acts on this rule's severities in production. They are
# deliberately module-level and overridable per instance (constructor arguments) so tuning is a
# config change, not a code change.
#
# The pairing is intentional: a finding must clear BOTH a degF floor and a sigma floor. The degF
# floor stops a very tight baseline (small residual sigma) from firing on a thermally meaningless
# fraction of a degree; the sigma floor stops a noisy baseline from burying a real widening.
# ---------------------------------------------------------------------------------------------
DRIFT_WARN_F = 1.0  # PROVISIONAL
DRIFT_FAULT_F = 2.0  # PROVISIONAL
DRIFT_WARN_SIGMA = 2.0  # PROVISIONAL
DRIFT_FAULT_SIGMA = 3.0  # PROVISIONAL


class ChillerApproachDrift:
    """Detects a chiller's approach drifting above its frozen, load-normalized baseline.

    Takes a :class:`~camber.store.modelstore.BaselineStore` so the reference survives between runs.
    Because it needs that store (and the site identity that keys it), it is **not** auto-registered
    in :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it, as
    with the other rules that require injected context.

    Run it via :meth:`camber.rules.base.Registry.run_periods`, which supplies the two explicitly
    bounded windows.
    """

    name = "chiller_approach_drift"
    roles_required = (
        Role.COND_APPROACH_TEMP,
        Role.CHW_FLOW,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
    )
    roles_optional = (Role.EVAP_APPROACH_TEMP,)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        drift_warn_f: float = DRIFT_WARN_F,  # PROVISIONAL -- see the module note
        drift_fault_f: float = DRIFT_FAULT_F,  # PROVISIONAL
        drift_warn_sigma: float = DRIFT_WARN_SIGMA,  # PROVISIONAL
        drift_fault_sigma: float = DRIFT_FAULT_SIGMA,  # PROVISIONAL
        min_tons: float = 5.0,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        # When no baseline is frozen yet, fit one from the baseline period and freeze it. That is
        # establishing the reference, not refitting it -- an existing frozen baseline is never
        # replaced here. Set False to require an operator to pre-freeze it out of band.
        self.freeze_if_missing = freeze_if_missing
        self.drift_warn_f = drift_warn_f
        self.drift_fault_f = drift_fault_f
        self.drift_warn_sigma = drift_warn_sigma
        self.drift_fault_sigma = drift_fault_sigma
        self.min_tons = min_tons

    # ------------------------------------------------------------------ frame prep
    def _with_tons(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``tons`` + approach-role frame; tons derived as in :mod:`camber.chiller`."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        for role, _slug, _label in _LEGS:
            if role in frame.columns:
                out[role] = frame[role]
        return out

    def _baseline_for(self, equip, role, kind, base_frame, caveats):
        """The frozen baseline for one leg, freezing an initial one from ``base_frame`` if none."""
        frozen = self.store.model_for(self.site, equip, kind)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {kind}: no frozen baseline and freezing is disabled"
            )
            return None
        fit = fit_approach_baseline(
            base_frame, approach_col=role, tons_col="tons", min_tons=self.min_tons
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {kind}: the baseline period would not support a fit "
                "(too few loaded samples, or too narrow a load range)"
            )
            return None
        idx = base_frame.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=kind,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, slug, caveats) -> str:
        """Severity for one leg: both the degF floor and the sigma floor must be cleared."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: the baseline fit had no residual scatter
            caveats.append(
                f"{slug}: baseline had no residual scatter, so drift is judged on degF alone"
            )
            if drift.drift_f >= self.drift_fault_f:
                return "fault"
            return "warn" if drift.drift_f >= self.drift_warn_f else "ok"
        if drift.drift_f >= self.drift_fault_f and drift.drift_sigma >= self.drift_fault_sigma:
            return "fault"
        if drift.drift_f >= self.drift_warn_f and drift.drift_sigma >= self.drift_warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score ``current`` against the frozen baseline for ``equip``; return a Finding."""
        base_t, cur_t = self._with_tons(baseline), self._with_tons(current)
        caveats: list = []
        metrics: dict = {}
        legs, severity = [], "ok"

        for role, slug, label in _LEGS:
            if role not in cur_t.columns:
                continue
            kind = f"chiller_approach_{slug}"
            frozen = self._baseline_for(equip, role, kind, base_t, caveats)
            if frozen is None:
                continue
            drift = drift_stats(
                frozen, cur_t, approach_col=role, tons_col="tons", min_tons=self.min_tons
            )
            if drift is None:
                caveats.append(
                    f"could not evaluate {kind}: no loaded samples in the current period"
                )
                continue
            rec = self.store.get(self.site, equip, kind)
            leg_sev = self._severity(drift, slug, caveats)
            severity = max(severity, leg_sev, key=lambda s: _RANK[s])
            metrics.update(
                {
                    f"{slug}_drift_f": drift.drift_f,
                    f"{slug}_drift_sigma": drift.drift_sigma,
                    f"{slug}_slope_f_per_month": drift.slope_f_per_month,
                    f"{slug}_pct_outside_2sigma": drift.pct_outside_2sigma,
                    f"{slug}_n_current": drift.n_current,
                    f"{slug}_baseline_sigma_f": frozen.sigma_f,
                    f"{slug}_baseline_frozen_at": rec.frozen_at if rec else "",
                }
            )
            if drift.extrapolated:
                caveats.append(
                    f"{slug}: over 10% of the current period ran outside the baseline's fitted "
                    "load envelope, so part of this drift is extrapolated"
                )
            legs.append(f"{label} {drift.drift_f:+.1f}°F ({drift.drift_sigma:.1f}σ)")

        if not legs:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no leg could be scored against a frozen baseline",
                caveats=caveats,
            )
        metrics["thresholds_provisional"] = True  # see the module-level threshold note
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: approach drift vs frozen baseline at matched load — " + "; ".join(legs)
            ),
            caveats=caveats,
        )
