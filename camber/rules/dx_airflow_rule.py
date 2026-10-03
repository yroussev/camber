"""Rule: DX / heat-pump **indoor airflow** from the evaporator temperature split (0.93; #40).

At a given entering-air condition the cooling coil removes a roughly fixed amount of heat, so the
air temperature drop across it -- return minus supply, the *temperature split* -- rises when less
air crosses the coil and falls when more does. A starved coil (dirty filter, slipping belt, closed
registers, a wrong fan speed tap) also runs colder and can freeze, and an over-blown one removes
too little moisture, so both directions are faults.

Two ways to say what "normal" is, as for :mod:`camber.rules.dx_charge_rule`:

* **Target split** (:meth:`DXIndoorAirflow.analyze`): the installer's target temperature split
  (``targets={"temp_split_f": 20}``, per equipment if needed) with a tolerance (default +/-3 degF).
  A target from a chart that takes the return wet bulb into account is far better than a fixed
  number; the fixed-number mode is for a unit that already has one. Without any target only a wide
  8-28 degF band applies (``mode="limits"``, each side a ``warn``).
* **A frozen fault-free baseline** (:meth:`DXIndoorAirflow.analyze_periods`): the split fitted on
  the coil's entering air -- return-air temperature and return dew point (mapped, or computed from
  return humidity; outdoor air stands in when neither is trended) -- over a known-good period, then
  the current median residual at matched conditions against a degF and a sigma floor.

**Charge confounds the low side.** Lost refrigerant also lowers the split (less capacity, same
air), so when subcooling is mapped and has itself fallen at matched conditions, a *narrowed* split
is reported as a capacity (charge) symptom and not called high airflow; a *widened* split stands
as low airflow either way. Latent load moves the split as well -- a humid spell narrows it at a
fixed dry-bulb -- which is why the baseline is matched on the return dew point when there is one;
without it that swing stays in the residual scatter the sigma floor absorbs.

Cooling operation only. Thresholds are screening-grade, characterized on the NIST laboratory heat
pumps (docs/FDD-DX.md), where airflow faults of 5-10% are too small to separate from the
fault-free scatter and 15-20% faults are caught.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..store.modelstore import BaselineStore
from . import _dxfit
from .base import Finding

__all__ = [
    "AIRFLOW_WARN_F",
    "AIRFLOW_FAULT_F",
    "AIRFLOW_WARN_SIGMA",
    "AIRFLOW_FAULT_SIGMA",
    "TARGET_TOLERANCE_F",
    "MIN_SPLIT_F",
    "MAX_SPLIT_F",
    "DXIndoorAirflow",
    "TEMP_SPLIT",
    "return_dewpoint_f",
]

TEMP_SPLIT = "temp_split"  # the prepared frame's return - supply column
_KIND = "dx_temp_split"
_SPLIT_RANGE = (0.0, 45.0)  # a cooling split, degF; outside it the reading is not a coil split
_SC_KIND = "dx_subcooling"

# SCREENING-GRADE floors characterized on the NIST heat pumps (median residual per test file):
# |split drift| >= max(1.5 degF, 2 sigma) flagged 2 of 90 fault-free files.
AIRFLOW_WARN_F = 1.5
AIRFLOW_FAULT_F = 3.0
AIRFLOW_WARN_SIGMA = 2.0
AIRFLOW_FAULT_SIGMA = 4.0
TARGET_TOLERANCE_F = 3.0
# With no target: a running DX cooling coil at rated airflow drops roughly 15-22 degF; outside
# 8-28 degF something is wrong with the air or the refrigerant (screening-grade, wide on purpose).
MIN_SPLIT_F = 8.0
MAX_SPLIT_F = 28.0
_CHARGE_LOW_F = 3.0  # subcooling this far below baseline: a narrowed split reads as charge


def return_dewpoint_f(temp_f, rh_pct):
    """Dew point (degF) from dry bulb (degF) and relative humidity (%): the Magnus form with the
    Alduchov & Eskridge (1996) constants, within ~0.4 degF over HVAC conditions."""
    t = (pd.to_numeric(temp_f, errors="coerce") - 32.0) / 1.8
    rh = pd.to_numeric(rh_pct, errors="coerce").clip(lower=1.0, upper=100.0)
    gamma = np.log(rh / 100.0) + 17.625 * t / (243.04 + t)
    return 243.04 * gamma / (17.625 - gamma) * 1.8 + 32.0


def _with_split(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out[TEMP_SPLIT] = pd.to_numeric(frame[Role.RETURN_AIR_TEMP], errors="coerce") - pd.to_numeric(
        frame[Role.SUPPLY_AIR_TEMP], errors="coerce"
    )
    if Role.RETURN_AIR_DEWPOINT_TEMP not in out.columns and Role.RETURN_AIR_HUMIDITY in out.columns:
        out[Role.RETURN_AIR_DEWPOINT_TEMP] = return_dewpoint_f(
            out[Role.RETURN_AIR_TEMP], out[Role.RETURN_AIR_HUMIDITY]
        )
    return out


class DXIndoorAirflow:
    """Indoor (evaporator) airflow from the cooling temperature split (see the module doc)."""

    name = "dx_indoor_airflow"
    roles_required = (Role.RETURN_AIR_TEMP, Role.SUPPLY_AIR_TEMP)
    roles_optional = (
        Role.RETURN_AIR_DEWPOINT_TEMP,
        Role.RETURN_AIR_HUMIDITY,
        Role.OAT,
        Role.SUBCOOLING_TEMP,
        Role.COMPRESSOR_STATUS,
        Role.REVERSING_VALVE_CMD,
        Role.SUPPLY_FAN_STATUS,
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
        warn_f: float = AIRFLOW_WARN_F,
        fault_f: float = AIRFLOW_FAULT_F,
        warn_sigma: float = AIRFLOW_WARN_SIGMA,
        fault_sigma: float = AIRFLOW_FAULT_SIGMA,
        min_samples: int = 10,
        min_baseline_samples: int = _dxfit.MIN_BASELINE_SAMPLES,
        min_split_f: float = MIN_SPLIT_F,
        max_split_f: float = MAX_SPLIT_F,
    ):
        self.store = store if store is not None else BaselineStore()
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.targets = targets
        self.tolerance_f = tolerance_f
        self.warn_f = warn_f
        self.fault_f = fault_f
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.min_samples = min_samples
        self.min_baseline_samples = min_baseline_samples
        self.min_split_f = min_split_f
        self.max_split_f = max_split_f

    def _declined(self, equip, why, reason, caveats=None) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {why}",
            caveats=list(caveats or []) + [f"could not evaluate indoor airflow: {why}"],
        )

    @staticmethod
    def _verdict(dev_f: float) -> str:
        return "airflow_low" if dev_f > 0 else "airflow_high"

    # ------------------------------------------------------------------ targets mode
    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Judge the cooling-mode median split against the target split (see the module doc)."""
        if any(r not in frame.columns for r in self.roles_required):
            return self._declined(equip, "return / supply air not mapped", "not_mapped")
        tgt = _dxfit.target_for(self.targets, equip)
        rows = _with_split(frame[_dxfit.cooling_rows(frame)])
        split = rows[TEMP_SPLIT][rows[TEMP_SPLIT].between(*_SPLIT_RANGE)]
        if len(split) < self.min_samples:
            return self._declined(
                equip,
                f"only {len(split)} cooling-mode readings (need {self.min_samples})",
                "too_few_samples",
            )
        if not tgt or tgt.get("temp_split_f") is None:
            return self._limits(equip, split)
        target = float(tgt["temp_split_f"])
        tol = float(tgt.get("tolerance_f", self.tolerance_f))
        med = _dxfit.median(split)
        dev = med - target
        mag = abs(dev)
        severity = "fault" if mag >= 2 * tol else ("warn" if mag >= tol else "ok")
        verdict = self._verdict(dev) if severity != "ok" else "airflow_ok"
        metrics = {
            "mode": "target",
            "temp_split_median_f": round(med, 2),
            "target_temp_split_f": target,
            "tolerance_f": tol,
            "deviation_f": round(dev, 2),
            "n_cooling_rows": int(len(split)),
            "airflow_verdict": verdict,
            "attribution": "indoor_airflow",
        }
        caveats = [
            "a fixed target split ignores the return-air humidity; a humid day narrows the split "
            "at the same airflow"
        ]
        if severity == "ok":
            summary = f"{equip}: temperature split {med:.1f}°F within ±{tol:g}°F of {target:g}°F"
        else:
            summary = (
                f"{equip}: temperature split {med:.1f}°F vs {target:g}°F target ({dev:+.1f}°F) -- "
                f"{'low' if verdict == 'airflow_low' else 'high'} indoor airflow? Check the "
                "filter, blower speed and registers"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    def _limits(self, equip, split) -> Finding:
        """No target: only the wide band any running DX cooling coil's split sits in."""
        med = _dxfit.median(split)
        lo, hi = self.min_split_f, self.max_split_f
        verdict = "airflow_low" if med > hi else ("split_low" if med < lo else "airflow_ok")
        severity = "ok" if verdict == "airflow_ok" else "warn"
        metrics = {
            "mode": "limits",
            "temp_split_median_f": round(med, 2),
            "limits_f": [lo, hi],
            "n_cooling_rows": int(len(split)),
            "airflow_verdict": verdict,
            "attribution": "indoor_airflow",
        }
        caveats = [
            f"no target temperature split for this unit: judged only against a wide band "
            f"({lo:g}-{hi:g}°F); set the rule's targets for an airflow verdict"
        ]
        if verdict == "airflow_low":
            summary = (
                f"{equip}: temperature split {med:.1f}°F -- far more than a DX coil drops with its "
                "rated airflow: low indoor airflow? Check the filter, blower speed and registers"
            )
        elif verdict == "split_low":
            summary = (
                f"{equip}: temperature split only {med:.1f}°F while cooling -- the coil removes "
                "little heat: high airflow, or lost capacity (charge)"
            )
        else:
            summary = f"{equip}: temperature split {med:.1f}°F is within the wide band"
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    # ------------------------------------------------------------------ baseline mode
    def drift_signature(self):
        """The frozen-model kind and the (normalizer, metric) columns the baseline is fitted on."""
        return _KIND, _dxfit.LOAD, TEMP_SPLIT

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current split against a frozen fault-free baseline at matched conditions."""
        if any(r not in current.columns for r in self.roles_required):
            return self._declined(equip, "return / supply air not mapped", "not_mapped")
        caveats: list = []
        base_s, cur_s = _with_split(baseline), _with_split(current)
        # the split follows the coil's entering air: dry bulb, then moisture (dew point) when it
        # is known, else outdoor air (the condenser side) as the second condition
        dew = Role.RETURN_AIR_DEWPOINT_TEMP
        cov = dew if dew in base_s.columns and dew in cur_s.columns else Role.OAT
        if cov is Role.OAT:
            caveats.append(
                "no return-air dew point or humidity: the split is matched on return-air and "
                "outdoor temperature only, so latent-load swings stay in the scatter"
            )
        rat = Role.RETURN_AIR_TEMP
        base_t = _dxfit.prepared(base_s, TEMP_SPLIT, water=False, load_role=rat, cov_role=cov)
        cur_t = _dxfit.prepared(cur_s, TEMP_SPLIT, water=False, load_role=rat, cov_role=cov)
        gate = _dxfit.gates(False, base_t)
        frozen = _dxfit.freeze(
            self,
            equip,
            base_t,
            caveats,
            kind=_KIND,
            metric=TEMP_SPLIT,
            metric_range=_SPLIT_RANGE,
            metric_name="temperature split",
            gate=gate,
            covariate=cov,
        )
        if frozen is None:
            return self._declined(equip, "no fault-free baseline", "no_baseline", caveats)
        drift = _dxfit.score(
            frozen,
            cur_t,
            caveats,
            kind=_KIND,
            metric=TEMP_SPLIT,
            metric_range=_SPLIT_RANGE,
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
        verdict = self._verdict(drift.drift_f) if severity != "ok" else "airflow_ok"
        metrics = {
            "mode": "baseline",
            "temp_split_drift_f": drift.drift_f,
            "temp_split_drift_sigma": drift.drift_sigma,
            "temp_split_baseline_sigma_f": frozen.sigma_f,
            "temp_split_n_current": drift.n_current,
            "covariate": frozen.covariate or None,
            "airflow_verdict": verdict,
            "attribution": "indoor_airflow",
        }
        if drift.extrapolated or drift.covariate_extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the conditions the baseline covered, "
                "so part of this drift is extrapolated"
            )
        sc = self._subcooling_drift(equip, baseline, current)
        metrics["subcooling_drift_f"] = sc
        if sc is None:
            caveats.append(
                "subcooling not available in both periods: a narrowed split was not checked "
                "against lost charge"
            )
        if verdict == "airflow_high" and sc is not None and sc <= -_CHARGE_LOW_F:
            # a narrowed split with subcooling down is lost capacity (charge), not more air
            verdict, severity = "capacity_low", "info"
            metrics["airflow_verdict"] = verdict
            metrics["attribution"] = "refrigerant_circuit"
            caveats.append(
                f"the split narrowed while subcooling fell {sc:+.1f}°F: read as lost capacity "
                "(charge; see dx_refrigerant_charge), not high airflow"
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=False))
        if severity in ("ok", "info"):
            tail = (
                "airflow looks steady"
                if severity == "ok"
                else "a capacity (charge) symptom, not airflow"
            )
            summary = (
                f"{equip}: temperature split {drift.drift_f:+.1f}°F vs its fault-free baseline "
                f"at matched conditions -- {tail}"
            )
        else:
            summary = (
                f"{equip}: temperature split {drift.drift_f:+.1f}°F "
                f"({abs(drift.drift_sigma):.1f}σ) vs its fault-free baseline at matched "
                f"conditions -- {'low' if verdict == 'airflow_low' else 'high'} indoor airflow? "
                "Check the filter, blower speed and registers"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)

    def _subcooling_drift(self, equip, baseline, current):
        role = Role.SUBCOOLING_TEMP
        if role not in baseline.columns or role not in current.columns:
            return None
        tmp: list = []
        base_t = _dxfit.prepared(baseline, role, water=False)
        if base_t.empty:
            return None
        gate = _dxfit.gates(False, base_t)
        from ..sensorhealth import PHYSICAL_BOUNDS

        rng = PHYSICAL_BOUNDS[role]
        frozen = _dxfit.freeze(
            self,
            equip,
            base_t,
            tmp,
            kind=_SC_KIND,
            metric=role,
            metric_range=rng,
            metric_name="subcooling",
            gate=gate,
        )
        if frozen is None:
            return None
        drift = _dxfit.score(
            frozen,
            _dxfit.prepared(current, role, water=False),
            tmp,
            kind=_SC_KIND,
            metric=role,
            metric_range=rng,
            min_samples=self.min_samples,
            gate=gate,
        )
        return None if drift is None else drift.drift_f
