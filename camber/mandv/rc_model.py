"""IPMVP Option D — a dependency-light grey-box RC building model + calibration + modeled savings.

Options A/B/C in this package are *inverse* models: they regress measured energy on temperature and
cannot answer a **counterfactual** ("what would this building use if we fixed the control?"). Option
D needs a *forward*, schedule-driven model, calibrated to metered data, then run under an
as-corrected control to difference the two annual profiles. This module is that model — a minimal,
citable **1R1C** (single-zone, one thermal time-constant) grey box in the ISO 13790 simple-hourly /
ASHRAE inverse-modeling lineage.

Design that keeps calibration honest and cheap: during conditioned hours the zone is pinned to its
setpoint, so hourly HVAC energy is **linear** in the effective conductance and gain; during setback
the zone free-floats toward outdoor air with time-constant ``tau`` (independent of the linear
parameters), and the re-entry recovery adds heating degree-hours ``(setpoint − drifted temp)`` —
where ``tau`` sets how far the zone drifted, so the whole thing stays **linear in the conductance
given ``tau``**. So calibration is exactly the change-point fitter's move (`camber.mandv.models`):
**grid the one nonlinear parameter ``tau``, OLS the linear ones, keep the best CV(RMSE)** — no
scipy. Acceptance uses the existing ASHRAE Guideline 14 gate
(`stats.fit_stats` + `cv_rmse_max_for`).

Identifiability note: energy alone cannot separate conductance from HVAC efficiency, so the
calibrated parameters are the **effective** combinations (metered energy per °F·h, per
conditioned h). That is enough for the counterfactual, which only re-runs the schedule. numpy-only,
deterministic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .normalized import _rel_unc
from .stats import FitStats, cv_rmse_max_for, fit_stats

# --------------------------------------------------------------------------- schedule helper


def daily_schedule(
    index, *, occ_setpoint=70.0, setback_setpoint=60.0, occ_start=7, occ_end=18, weekdays_only=True
) -> dict:
    """Build an hourly control schedule aligned to ``index``.

    Returns ``{"setpoint": ndarray, "conditioned": ndarray(0/1)}`` — occupied hours hold
    ``occ_setpoint`` (conditioned); off hours relax toward a ``setback_setpoint`` (unconditioned).
    """
    idx = pd.DatetimeIndex(index)
    occ = (idx.hour >= occ_start) & (idx.hour < occ_end)
    if weekdays_only:
        occ = occ & (idx.dayofweek < 5)
    occ = np.asarray(occ)
    setpoint = np.where(occ, occ_setpoint, setback_setpoint).astype(float)
    return {"setpoint": setpoint, "conditioned": occ.astype(float)}


def _as_arrays(oat, schedule):
    oat = np.asarray(oat, dtype=float)
    sp = np.asarray(schedule["setpoint"], dtype=float)
    cond = np.asarray(schedule["conditioned"], dtype=float)
    if not (len(oat) == len(sp) == len(cond)):
        raise ValueError("oat, setpoint, and conditioned must be the same length")
    return oat, sp, cond


def _design(oat, schedule, tau: float):
    """Per-hour basis functions for the linear parameters, given ``tau`` (independent of them).

    Returns ``(ddh, cond)``: ``ddh`` = the effective heating degree-hours the conductance multiplies
    (steady hold + re-entry recovery), ``cond`` = conditioned indicator (what the gain offsets).
    """
    oat, sp, cond = _as_arrays(oat, schedule)
    n = len(oat)
    tau = max(float(tau), 1e-6)
    decay = np.exp(-1.0 / tau)  # hourly free-float relaxation toward OAT
    tz = sp.copy()  # zone temp; starts at setpoint
    ddh = np.zeros(n)
    for t in range(n):
        if cond[t] > 0:
            # re-entry recovery: the extra heating degree-hours to pull the drifted zone back to
            # setpoint (tau enters through how far the zone drifted during setback, below)
            recovery = max(sp[t] - tz[t - 1], 0.0) if (t > 0 and cond[t - 1] == 0) else 0.0
            ddh[t] = max(sp[t] - oat[t], 0.0) + recovery
            tz[t] = sp[t]
        else:
            # free-float toward OAT with time-constant tau (independent of the linear params)
            tz[t] = oat[t] + (tz[t - 1] - oat[t]) * decay if t > 0 else oat[t]
            ddh[t] = 0.0
    return ddh, cond


@dataclass(frozen=True)
class RCModel:
    """A calibrated 1R1C grey-box model (effective, energy-identifiable parameters)."""

    ua_eff: float  # metered energy per °F of setpoint-OAT gap per hour
    gain_eff: float  # metered energy offset per conditioned hour (internal + solar)
    tau: float  # free-float / recovery time-constant (hours)

    def predict(self, oat, schedule) -> np.ndarray:
        """Hourly metered HVAC energy for ``oat`` under ``schedule`` (clamped at 0 — physical)."""
        ddh, cond = _design(oat, schedule, self.tau)
        return np.maximum(self.ua_eff * ddh - self.gain_eff * cond, 0.0)

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- calibration


@dataclass
class Calibration:
    """A calibrated :class:`RCModel` with its ASHRAE G14 fit statistics."""

    model: RCModel
    fit: FitStats
    tau_grid_n: int = 0

    @property
    def accept(self) -> bool:
        return bool(getattr(self.fit, "accept", False))

    def as_dict(self) -> dict:
        return {
            "model": self.model.as_dict(),
            "fit": self.fit.as_dict(),
            "accept": self.accept,
            "tau_grid_n": self.tau_grid_n,
        }


def _tau_grid(n: int = 40, lo: float = 1.0, hi: float = 120.0):
    return np.linspace(lo, hi, n)


def calibrate(
    oat, schedule, metered_energy, *, interval: str = "hourly", tau_grid=None
) -> Calibration:
    """Calibrate an :class:`RCModel` to ``metered_energy`` (grid ``tau``, OLS the linear params).

    Mirrors `mandv.models` (grid the nonlinear parameter, least-squares the linear ones, keep the
    best CV(RMSE)). Acceptance is the ASHRAE G14 gate for ``interval`` (hourly → CV(RMSE) ≤ 30%). A
    model that fails acceptance is still returned (with ``fit.accept == False``); the savings layer
    refuses to claim a number from it.
    """
    oat = np.asarray(oat, dtype=float)
    y = np.asarray(metered_energy, dtype=float)

    # Degrade, don't raise, on data too thin/degenerate to calibrate: return a non-accepted
    # Calibration so the savings layer refuses to claim a number (rather than a ValueError).
    n_finite = int(np.isfinite(y).sum())
    if len(y) < 4 or n_finite < 4:
        nan = float("nan")
        deg_fit = FitStats(
            n=len(y),
            p=3,
            r2=nan,
            rmse=nan,
            cv_rmse=nan,
            nmbe=nan,
            f_stat=nan,
            accept=False,
            notes="insufficient data to calibrate (need >= 4 finite points)",
        )
        return Calibration(
            model=RCModel(ua_eff=nan, gain_eff=nan, tau=nan), fit=deg_fit, tau_grid_n=0
        )

    def _fit_at(tau):
        ddh, cond = _design(oat, schedule, float(tau))
        # energy = ua_eff*ddh - gain_eff*cond  ->  OLS on columns [ddh, -cond]
        beta, *_ = np.linalg.lstsq(np.column_stack([ddh, -cond]), y, rcond=None)
        model = RCModel(ua_eff=float(beta[0]), gain_eff=float(beta[1]), tau=float(tau))
        return float(np.sum((y - model.predict(oat, schedule)) ** 2)), model

    grid = np.asarray(tau_grid) if tau_grid is not None else _tau_grid()
    scored = [(_fit_at(tau)) for tau in grid]
    best_sse, model = min(scored, key=lambda s: s[0])
    n_evals = len(grid)
    # coarse -> fine: refine tau on a tighter grid bracketing the best coarse point (unless caller
    # supplied their own grid). Recovers tau to grid-free precision (mirrors a change-point refine).
    if tau_grid is None:
        step = grid[1] - grid[0]
        fine = np.linspace(max(model.tau - step, 1e-3), model.tau + step, 21)
        for tau in fine:
            sse, cand = _fit_at(tau)
            n_evals += 1
            if sse < best_sse:
                best_sse, model = sse, cand
    yhat = model.predict(oat, schedule)
    try:
        fit: FitStats | None = fit_stats(y, yhat, p=3, cv_rmse_max=cv_rmse_max_for(interval))
    except ValueError:
        fit = None
    # NaN-gapped energy yields non-finite coefficients / stats -> degrade to a non-accepted fit
    if fit is None or not np.isfinite(model.ua_eff) or fit.cv_rmse != fit.cv_rmse:
        nan = float("nan")
        fit = FitStats(
            n=len(y),
            p=3,
            r2=nan,
            rmse=nan,
            cv_rmse=nan,
            nmbe=nan,
            f_stat=nan,
            accept=False,
            notes="calibration produced a non-finite fit (gapped/degenerate energy)",
        )
    return Calibration(model=model, fit=fit, tau_grid_n=n_evals)


# --------------------------------------------------------------------------- Option-D savings


@dataclass
class OptionDSavings:
    """Modeled avoided energy from an as-found → as-corrected control change (IPMVP Option D)."""

    avoided_energy: float | None  # None when the calibration failed G14 acceptance
    energy_as_found: float
    energy_as_corrected: float
    fractional_savings: float
    frac_savings_uncertainty: float  # G14 Annex-B fractional savings uncertainty (NaN if invalid)
    valid: bool  # calibration met the G14 acceptance gate
    basis: str
    fit: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def option_d_savings(calibration, oat, as_found_schedule, as_corrected_schedule) -> OptionDSavings:
    """Difference the calibrated model's as-found vs as-corrected annual profiles → modeled savings.

    ``calibration`` is a :class:`Calibration` (carries the model, its G14 :class:`FitStats`, and the
    acceptance verdict). **If the calibration failed acceptance, no saving is claimed**
    (``valid=False``, ``avoided_energy=None``) — the same refuse-to-fabricate posture as
    ``fault_economics`` (``costed``) and ``ecm_savings`` (upper-bound). The uncertainty band is the
    ASHRAE G14 Annex-B fractional savings uncertainty from the calibration CV(RMSE).
    """
    model, fit = calibration.model, calibration.fit
    valid = calibration.accept
    oat = np.asarray(oat, dtype=float)
    e_found = float(model.predict(oat, as_found_schedule).sum())
    e_corr = float(model.predict(oat, as_corrected_schedule).sum())
    avoided = e_found - e_corr
    frac = avoided / e_found if e_found > 0 else float("nan")
    if valid and frac == frac and frac not in (0.0,):
        fsu = _rel_unc(getattr(fit, "cv_rmse", float("nan")), len(oat), len(oat)) / abs(frac)
        basis = "IPMVP Option D (calibrated simulation)"
    else:
        fsu = float("nan")
        basis = (
            (
                "uncalibrated: the model did not meet the ASHRAE G14 acceptance gate — "
                "no saving claimed"
            )
            if not valid
            else "no net change between the two schedules"
        )
    return OptionDSavings(
        avoided_energy=(avoided if valid else None),
        energy_as_found=e_found,
        energy_as_corrected=e_corr,
        fractional_savings=frac,
        frac_savings_uncertainty=fsu,
        valid=valid,
        basis=basis,
        fit=fit.as_dict() if fit is not None else None,
    )
