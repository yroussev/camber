"""Rule: DX / heat-pump **refrigerant charge** from liquid subcooling (provisional, 0.93; #40).

On a unit with a thermostatic expansion valve (TXV / EEV) -- nearly every modern split system and
heat pump -- the valve holds suction superheat, so the charge shows up in the **liquid subcooling**:
refrigerant missing from the circuit is liquid missing from the condenser (subcooling falls; at
the extreme the liquid line carries flash gas), and surplus refrigerant backs liquid up into the
condenser (subcooling rises). The NIST residential heat-pump FDD data show it plainly: 8.2 degF
fault-free, 2.7 degF at 80 % of the nominal charge and 18.5 degF at 110.6 % (14 SEER unit, long
line set; doi:10.18434/M32132). Once a starved TXV runs out of stroke the superheat climbs too,
so superheat is reported as corroboration, never required.

Two ways to say what "normal" is:

* **Manufacturer targets** (:meth:`DXRefrigerantCharge.analyze`, the ordinary single-frame run):
  the nameplate / installation-manual target subcooling (``targets={"subcooling_f": 10}``, or per
  equipment ``{"RTU-*": {...}}``) with the charging tolerance (``tolerance_f``, default 3 degF,
  the common +/-3 degF of manufacturer charging instructions). Without a target only universal
  limits apply (``mode="limits"``: a liquid line subcooled less than 2 degF feeds the valve flash
  gas; more than 25 degF is liquid backed up in the condenser), each a ``warn``.
* **A frozen fault-free baseline** (:meth:`DXRefrigerantCharge.analyze_periods`, run by
  :meth:`camber.rules.base.Registry.run_periods` or the ``dx`` drift family): subcooling fitted on
  outdoor-air and return-air temperature over a known-good period, then the current period's
  median residual at matched conditions, judged against both a degF floor and a sigma floor.

**Scope and limits.** Cooling operation only (compressor running, reversing valve in cooling,
supply air below return -- whichever of those points are mapped). Subcooling that is a
controller's reported value or is derived from the liquid pressure and liquid-line temperature
(:mod:`camber.refrigerant`) are equally good. A fixed-orifice (piston) unit is charged by superheat,
not subcooling; for one, set ``metric="superheat"``. Condenser-side faults move subcooling too:
on the NIST data a blocked outdoor coil and a liquid-line restriction raise it on some units and
lower it on others, so a charge verdict is a prompt to check charge **and** the condenser, not a
diagnosis on its own.

Thresholds are screening-grade, characterized on the NIST laboratory heat pumps (see
docs/FDD-DX.md for the scored results), not on field equipment.
"""

from __future__ import annotations

import pandas as pd

from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..sensorhealth import PHYSICAL_BOUNDS
from ..store.modelstore import BaselineStore
from . import _dxfit
from .base import Finding

__all__ = [
    "CHARGE_WARN_F",
    "CHARGE_FAULT_F",
    "CHARGE_WARN_SIGMA",
    "CHARGE_FAULT_SIGMA",
    "TARGET_TOLERANCE_F",
    "MIN_SUBCOOLING_F",
    "MAX_SUBCOOLING_F",
    "DXRefrigerantCharge",
]

_KIND = {"subcooling": "dx_subcooling", "superheat": "dx_superheat"}
_ROLE = {"subcooling": Role.SUBCOOLING_TEMP, "superheat": Role.SUPERHEAT_TEMP}

# SCREENING-GRADE floors (see camber.driftthresholds), characterized on the NIST heat pumps: over
# 90 fault-free and 95 charge-fault test files, |median residual| >= max(3 degF, 1.5 sigma) caught
# 91% of the charge faults at 8% of the fault-free files; both are constructor arguments.
CHARGE_WARN_F = 3.0
CHARGE_FAULT_F = 5.0
CHARGE_WARN_SIGMA = 1.5
CHARGE_FAULT_SIGMA = 3.0
TARGET_TOLERANCE_F = 3.0  # manufacturer charging tolerance on the target subcooling
# With no target: a TXV circuit needs a subcooled liquid line (below ~2 degF the valve sees flash
# gas) and no charging target sits near 25 degF (screening-grade universal limits).
MIN_SUBCOOLING_F = 2.0
MAX_SUBCOOLING_F = 25.0
_CORROBORATE_SH_F = 2.0  # superheat rise that corroborates a low-charge reading
_WORDS = {"undercharge": "low charge", "overcharge": "overcharge"}


def _plausible(role):
    return PHYSICAL_BOUNDS[role]


class DXRefrigerantCharge:
    """Refrigerant charge from liquid subcooling on a DX unit or heat pump (see the module doc).

    ``metric="subcooling"`` (default, TXV / EEV units) or ``"superheat"`` (fixed-orifice units,
    where charge is set by superheat: a high superheat reads low charge). ``store`` holds the
    frozen baselines for :meth:`analyze_periods` (an in-memory store when omitted).
    """

    name = "dx_refrigerant_charge"
    roles_required = (Role.SUBCOOLING_TEMP,)
    roles_optional = (
        Role.SUPERHEAT_TEMP,
        Role.OAT,
        Role.RETURN_AIR_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.COMPRESSOR_STATUS,
        Role.REVERSING_VALVE_CMD,
    )

    def __init__(
        self,
        store=None,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        targets: dict | None = None,
        tolerance_f: float = TARGET_TOLERANCE_F,
        metric: str = "subcooling",
        warn_f: float = CHARGE_WARN_F,
        fault_f: float = CHARGE_FAULT_F,
        warn_sigma: float = CHARGE_WARN_SIGMA,
        fault_sigma: float = CHARGE_FAULT_SIGMA,
        min_samples: int = 10,
        min_baseline_samples: int = _dxfit.MIN_BASELINE_SAMPLES,
        min_subcooling_f: float = MIN_SUBCOOLING_F,
        max_subcooling_f: float = MAX_SUBCOOLING_F,
    ):
        if metric not in _ROLE:
            raise ValueError(f"metric must be 'subcooling' or 'superheat', not {metric!r}")
        self.store = store if store is not None else BaselineStore()
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.targets = targets
        self.tolerance_f = tolerance_f
        self.metric = metric
        self.warn_f = warn_f
        self.fault_f = fault_f
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.min_samples = min_samples
        self.min_baseline_samples = min_baseline_samples
        self.min_subcooling_f = min_subcooling_f
        self.max_subcooling_f = max_subcooling_f
        if metric == "superheat":
            self.roles_required = (Role.SUPERHEAT_TEMP,)

    # ------------------------------------------------------------------ helpers
    @property
    def _role(self):
        return _ROLE[self.metric]

    def _verdict(self, dev_f: float) -> str:
        """``undercharge`` / ``overcharge`` from the sign of the metric's deviation."""
        low = dev_f < 0
        if self.metric == "superheat":  # a fixed orifice: superheat *rises* on low charge
            low = not low
        return "undercharge" if low else "overcharge"

    def _declined(self, equip, why, reason, caveats=None) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {why}",
            caveats=list(caveats or []) + [f"could not evaluate refrigerant charge: {why}"],
        )

    # ------------------------------------------------------------------ targets mode
    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Judge the frame's cooling-mode median against the manufacturer target (see module)."""
        role = self._role
        if role not in frame.columns:
            return self._declined(equip, f"no {self.metric} point", f"{self.metric}_not_mapped")
        tgt = _dxfit.target_for(self.targets, equip)
        key = f"{self.metric}_f"
        if (not tgt or tgt.get(key) is None) and self.metric != "subcooling":
            return self._declined(
                equip,
                f"no target {self.metric} for this unit (set the rule's targets from the "
                "nameplate / installation manual, or score it against a fault-free baseline)",
                "no_target",
            )
        rows = frame[_dxfit.cooling_rows(frame)]
        vals = pd.to_numeric(rows[role], errors="coerce")
        lo, hi = _plausible(role)
        vals = vals[vals.between(lo, hi)]
        if len(vals) < self.min_samples:
            return self._declined(
                equip,
                f"only {len(vals)} cooling-mode {self.metric} readings (need {self.min_samples})",
                "too_few_samples",
            )
        if not tgt or tgt.get(key) is None:
            return self._limits(equip, rows, vals)
        target = float(tgt[key])
        tol = float(tgt.get("tolerance_f", self.tolerance_f))
        med = _dxfit.median(vals)
        dev = med - target
        mag = abs(dev)
        # outside the charging tolerance is a warn; twice outside it a fault
        severity = "fault" if mag >= 2 * tol else ("warn" if mag >= tol else "ok")
        caveats: list = []
        metrics = {
            "mode": "target",
            "metric": self.metric,
            f"{self.metric}_median_f": round(med, 2),
            f"target_{self.metric}_f": target,
            "tolerance_f": tol,
            "deviation_f": round(dev, 2),
            "n_cooling_rows": int(len(vals)),
        }
        verdict = self._verdict(dev) if severity != "ok" else "charge_ok"
        metrics["charge_verdict"] = verdict
        metrics["attribution"] = "refrigerant_circuit"
        self._superheat_note(rows, tgt, metrics, caveats, verdict)
        if severity == "ok":
            summary = (
                f"{equip}: {self.metric} {med:.1f}°F within ±{tol:g}°F of its "
                f"{target:g}°F target -- charge looks right"
            )
        else:
            summary = (
                f"{equip}: {self.metric} {med:.1f}°F vs {target:g}°F target "
                f"({dev:+.1f}°F) -- {_WORDS[verdict]}? "
                "Check the charge and the condenser coil"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    def _limits(self, equip, rows, vals) -> Finding:
        """No target: only the universal limits a TXV circuit's subcooling must respect."""
        med = _dxfit.median(vals)
        lo, hi = self.min_subcooling_f, self.max_subcooling_f
        verdict = "undercharge" if med < lo else ("overcharge" if med > hi else "charge_ok")
        severity = "ok" if verdict == "charge_ok" else "warn"
        metrics = {
            "mode": "limits",
            "metric": "subcooling",
            "subcooling_median_f": round(med, 2),
            "limits_f": [lo, hi],
            "n_cooling_rows": int(len(vals)),
            "charge_verdict": verdict,
            "attribution": "refrigerant_circuit",
        }
        caveats = [
            f"no target subcooling for this unit: judged only against the universal limits "
            f"({lo:g}-{hi:g}°F); set the rule's targets from the nameplate for a charge verdict"
        ]
        self._superheat_note(rows, None, metrics, caveats, verdict)
        if verdict == "undercharge":
            summary = (
                f"{equip}: liquid subcooling only {med:.1f}°F -- the liquid line is barely "
                "subcooled (flash gas at the expansion valve): low charge or a liquid-line "
                "restriction"
            )
        elif verdict == "overcharge":
            summary = (
                f"{equip}: liquid subcooling {med:.1f}°F -- liquid is backing up in the "
                "condenser: overcharge or a condenser-side problem"
            )
        else:
            summary = f"{equip}: liquid subcooling {med:.1f}°F is within the universal limits"
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    def _superheat_note(self, rows, tgt, metrics, caveats, verdict) -> None:
        if self.metric != "subcooling":
            return
        if Role.SUPERHEAT_TEMP not in rows.columns:
            metrics["superheat_median_f"] = None
            caveats.append("superheat not mapped: a low-charge reading was not cross-checked")
            return
        sh = _dxfit.median(rows[Role.SUPERHEAT_TEMP])
        metrics["superheat_median_f"] = None if sh != sh else round(sh, 2)
        t_sh = tgt.get("superheat_f") if tgt else None
        if t_sh is not None and sh == sh:
            metrics["target_superheat_f"] = float(t_sh)
            metrics["superheat_corroborates"] = (
                bool(sh - float(t_sh) >= _CORROBORATE_SH_F) if verdict == "undercharge" else None
            )

    # ------------------------------------------------------------------ baseline mode
    def drift_signature(self):
        """The frozen-model kind and the (normalizer, metric) columns the baseline is fitted on."""
        return _KIND[self.metric], _dxfit.LOAD, self._role

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period against a frozen fault-free baseline at matched conditions."""
        role, kind = self._role, _KIND[self.metric]
        if role not in current.columns:
            return self._declined(equip, f"no {self.metric} point", f"{self.metric}_not_mapped")
        caveats: list = []
        water = _dxfit.is_water_cooled(current)
        base_t = _dxfit.prepared(baseline, role, water=water)
        cur_t = _dxfit.prepared(current, role, water=water)
        if base_t.empty or cur_t.empty:
            need = "chilled-water load" if water else "outdoor-air temperature"
            return self._declined(equip, f"no {need} to match conditions on", "no_normalizer")
        gate = _dxfit.gates(water, base_t)
        rng = _plausible(role)
        frozen = _dxfit.freeze(
            self,
            equip,
            base_t,
            caveats,
            kind=kind,
            metric=role,
            metric_range=rng,
            metric_name=self.metric,
            gate=gate,
        )
        if frozen is None:
            return self._declined(equip, "no fault-free baseline", "no_baseline", caveats)
        drift = _dxfit.score(
            frozen,
            cur_t,
            caveats,
            kind=kind,
            metric=role,
            metric_range=rng,
            min_samples=self.min_samples,
            gate=gate,
        )
        if drift is None:
            return self._declined(
                equip, "nothing scoreable in the current period", "empty", caveats
            )
        severity = _dxfit.severity(
            drift.drift_f,
            frozen.sigma_f,
            warn_f=self.warn_f,
            fault_f=self.fault_f,
            warn_sigma=self.warn_sigma,
            fault_sigma=self.fault_sigma,
        )
        verdict = self._verdict(drift.drift_f) if severity != "ok" else "charge_ok"
        metrics = {
            "mode": "baseline",
            "metric": self.metric,
            f"{self.metric}_drift_f": drift.drift_f,
            f"{self.metric}_drift_sigma": drift.drift_sigma,
            f"{self.metric}_baseline_sigma_f": frozen.sigma_f,
            f"{self.metric}_n_current": drift.n_current,
            "normalized_on": "tons" if water else "oat",
            "covariate": frozen.covariate or None,
            "charge_verdict": verdict,
            "attribution": "refrigerant_circuit",
        }
        if drift.extrapolated or drift.covariate_extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the conditions the baseline covered, "
                "so part of this drift is extrapolated"
            )
        self._superheat_drift(equip, baseline, current, water, metrics, caveats, verdict)
        metrics.update(threshold_confidence(magnitude=True, temporal=False))
        if severity == "ok":
            summary = (
                f"{equip}: {self.metric} {drift.drift_f:+.1f}°F vs its fault-free baseline at "
                "matched conditions -- charge looks steady"
            )
        else:
            summary = (
                f"{equip}: {self.metric} {drift.drift_f:+.1f}°F "
                f"({abs(drift.drift_sigma):.1f}σ) vs its fault-free baseline at matched "
                f"conditions -- {_WORDS[verdict]}? Check the charge and the "
                "condenser coil"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    def _superheat_drift(self, equip, baseline, current, water, metrics, caveats, verdict) -> None:
        """Superheat's own drift at matched conditions, as corroboration of a low-charge verdict."""
        if self.metric != "subcooling":
            return
        role = Role.SUPERHEAT_TEMP
        if role not in current.columns or role not in baseline.columns:
            metrics["superheat_drift_f"] = None
            caveats.append("superheat not mapped: a low-charge reading was not cross-checked")
            return
        tmp: list = []
        base_t = _dxfit.prepared(baseline, role, water=water)
        gate = _dxfit.gates(water, base_t)
        frozen = _dxfit.freeze(
            self,
            equip,
            base_t,
            tmp,
            kind=_KIND["superheat"],
            metric=role,
            metric_range=_plausible(role),
            metric_name="superheat",
            gate=gate,
        )
        drift = (
            None
            if frozen is None
            else _dxfit.score(
                frozen,
                _dxfit.prepared(current, role, water=water),
                tmp,
                kind=_KIND["superheat"],
                metric=role,
                metric_range=_plausible(role),
                min_samples=self.min_samples,
                gate=gate,
            )
        )
        metrics["superheat_drift_f"] = None if drift is None else drift.drift_f
        if verdict == "undercharge":
            metrics["superheat_corroborates"] = (
                None if drift is None else bool(drift.drift_f >= _CORROBORATE_SH_F)
            )
