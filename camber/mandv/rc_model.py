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
from typing import Any

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
    """A calibrated model with its ASHRAE G14 fit statistics.

    ``model`` is duck-typed on ``.predict`` / ``.as_dict``: a 1R1C :class:`RCModel`, a 2R2C
    :class:`RC2Model`, or a :class:`MultiZoneModel` (its ``predict`` takes a per-zone dict).
    :func:`option_d_savings` consumes any of them unchanged.
    """

    model: Any
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


# --------------------------------------------------------------------------- 2R2C (thermal mass)
#
# The 1R1C model has one time-constant, so it can only represent a single free-float/recovery decay.
# A mass-dominated building keeps *drawing* recovery energy for hours after re-entry as its thermal
# mass recharges -- a slow tail one tau cannot fit. The 2R2C model adds a second (slow) state, the
# thermal-mass node, coupled to the air node. Crucially it keeps the module's honesty invariant: the
# two time-constants (tau_air, tau_mass) plus a dimensionless air-exposure weight w are the ONLY
# nonlinear parameters and govern the state recursion *independently* of the linear conductances, so
# calibration still grids them and OLS-fits the (ua_env, uc_mass, gain) linear params. numpy-only.


def _design2(oat, schedule, tau_air: float, tau_mass: float, w: float):
    """Per-hour basis columns ``(dh_env, dh_mass, cond)`` for the 2R2C linear params given the taus.

    Two states evolve by a fixed linear recursion driven only by ``(tau_air, tau_mass, w)``: the air
    node ``Ta`` (fast) relaxes toward ``w*OAT + (1-w)*Tm`` during setback and is pinned to setpoint
    while conditioned; the mass node ``Tm`` (slow) always relaxes toward the air node. On
    conditioned hours the energy to hold setpoint is ``ua_env*dh_env + uc_mass*dh_mass - gain*cond``
    where ``dh_env`` is the envelope loss + fast air re-entry recovery and ``dh_mass`` is the heat
    the warm air sheds into the still-cold mass (nonzero after re-entry -- the mass-dominated tail).
    """
    oat, sp, cond = _as_arrays(oat, schedule)
    n = len(oat)
    da = 1.0 - np.exp(-1.0 / max(float(tau_air), 1e-6))  # fast air relaxation fraction / hour
    dm = 1.0 - np.exp(-1.0 / max(float(tau_mass), 1e-6))  # slow mass relaxation fraction / hour
    dh_env = np.zeros(n)
    dh_mass = np.zeros(n)
    ta_prev = float(sp[0])
    tm_prev = float(sp[0])
    for t in range(n):
        if cond[t] > 0:
            recovery = max(sp[t] - ta_prev, 0.0) if (t > 0 and cond[t - 1] == 0) else 0.0
            dh_env[t] = max(sp[t] - oat[t], 0.0) + recovery
            dh_mass[t] = max(sp[t] - tm_prev, 0.0)  # air sheds heat into the cold mass
            ta_cur = float(sp[t])  # air pinned to setpoint
            tm_cur = tm_prev + (sp[t] - tm_prev) * dm  # mass recharges toward setpoint
        elif t == 0:
            ta_cur = float(oat[0])
            tm_cur = float(oat[0])
        else:
            target = w * oat[t] + (1.0 - w) * tm_prev  # air relaxes toward OAT + mass
            ta_cur = ta_prev + (target - ta_prev) * da
            tm_cur = tm_prev + (ta_prev - tm_prev) * dm
        ta_prev, tm_prev = ta_cur, tm_cur
    return dh_env, dh_mass, cond


@dataclass(frozen=True)
class RC2Model:
    """A calibrated 2R2C grey box: an air node + a slow thermal-mass node (effective params)."""

    ua_env: float  # envelope conductance (air <-> OAT), metered energy per °F·h
    uc_mass: float  # coupling conductance (air <-> mass), metered energy per °F·h
    gain_eff: float  # internal + solar gain offset per conditioned hour
    tau_air: float  # fast air time-constant (hours)
    tau_mass: float  # slow thermal-mass time-constant (hours)
    w: float  # air-node OAT-exposure weight in [0, 1] (the rest couples to the mass)

    def predict(self, oat, schedule) -> np.ndarray:
        """Hourly metered HVAC energy for ``oat`` under ``schedule`` (clamped at 0 -- physical)."""
        dh_env, dh_mass, cond = _design2(oat, schedule, self.tau_air, self.tau_mass, self.w)
        return np.maximum(self.ua_env * dh_env + self.uc_mass * dh_mass - self.gain_eff * cond, 0.0)

    def as_dict(self) -> dict:
        return asdict(self)


def _nonaccepted(n: int, p: int, note: str) -> FitStats:
    """A NaN, non-accepted FitStats for the degrade-don't-raise paths."""
    nan = float("nan")
    return FitStats(
        n=n, p=p, r2=nan, rmse=nan, cv_rmse=nan, nmbe=nan, f_stat=nan, accept=False, notes=note
    )


def calibrate2(
    oat,
    schedule,
    metered_energy,
    *,
    interval: str = "hourly",
    tau_air_grid=None,
    tau_mass_grid=None,
    w_grid=None,
) -> Calibration:
    """Calibrate an :class:`RC2Model` to ``metered_energy`` (grid the taus + ``w``, OLS the rest).

    Keeps the 1R1C design: the nonlinear ``(tau_air, tau_mass, w)`` are gridded and the linear
    ``(ua_env, uc_mass, gain_eff)`` are least-squares fit at each grid point; the best CV(RMSE)
    wins, then a coarse->fine refine on the taus. Acceptance is the same ASHRAE G14 gate, with
    ``p=6`` so the extra parameters are honestly penalized. Degrades (returns a non-accepted
    :class:`Calibration`) rather than raising on thin/degenerate data -- the savings layer then
    refuses to claim a number. numpy-only, deterministic.
    """
    oat = np.asarray(oat, dtype=float)
    y = np.asarray(metered_energy, dtype=float)
    if len(y) < 6 or int(np.isfinite(y).sum()) < 6:
        note = "insufficient data to calibrate 2R2C (need >= 6 finite points)"
        return Calibration(
            model=RC2Model(*([float("nan")] * 6)), fit=_nonaccepted(len(y), 6, note), tau_grid_n=0
        )

    def _fit_at2(ta, tm, w):
        dh_env, dh_mass, cond = _design2(oat, schedule, ta, tm, w)
        beta, *_ = np.linalg.lstsq(np.column_stack([dh_env, dh_mass, -cond]), y, rcond=None)
        model = RC2Model(
            ua_env=float(beta[0]),
            uc_mass=float(beta[1]),
            gain_eff=float(beta[2]),
            tau_air=float(ta),
            tau_mass=float(tm),
            w=float(w),
        )
        return float(np.sum((y - model.predict(oat, schedule)) ** 2)), model

    ta_grid = np.asarray(tau_air_grid) if tau_air_grid is not None else np.linspace(1.0, 12.0, 12)
    tm_grid = (
        np.asarray(tau_mass_grid) if tau_mass_grid is not None else np.linspace(12.0, 200.0, 16)
    )
    ws = np.asarray(w_grid) if w_grid is not None else np.array([0.4, 0.6, 0.8])
    best_sse, model = float("inf"), None
    n_evals = 0
    for ta in ta_grid:
        for tm in tm_grid:
            if tm <= ta:  # the mass node must be the slower one
                continue
            for w in ws:
                sse, cand = _fit_at2(ta, tm, float(w))
                n_evals += 1
                if sse < best_sse:
                    best_sse, model = sse, cand
    # coarse -> fine refine on the taus around the best coarse point (w fixed at its best)
    if tau_air_grid is None and tau_mass_grid is None and model is not None:
        sa = ta_grid[1] - ta_grid[0]
        sm = tm_grid[1] - tm_grid[0]
        for ta in np.linspace(max(model.tau_air - sa, 1e-3), model.tau_air + sa, 7):
            for tm in np.linspace(max(model.tau_mass - sm, ta + 1e-3), model.tau_mass + sm, 7):
                sse, cand = _fit_at2(ta, tm, model.w)
                n_evals += 1
                if sse < best_sse:
                    best_sse, model = sse, cand

    if model is None:  # every grid point skipped (degenerate grid) -> degrade, don't raise
        return Calibration(
            model=RC2Model(*([float("nan")] * 6)),
            fit=_nonaccepted(len(y), 6, "empty tau grid"),
            tau_grid_n=n_evals,
        )
    yhat = model.predict(oat, schedule)
    try:
        fit: FitStats | None = fit_stats(y, yhat, p=6, cv_rmse_max=cv_rmse_max_for(interval))
    except ValueError:
        fit = None
    if fit is None or not np.isfinite(model.ua_env) or fit.cv_rmse != fit.cv_rmse:
        fit = _nonaccepted(len(y), 6, "2R2C calibration produced a non-finite fit")
    return Calibration(model=model, fit=fit, tau_grid_n=n_evals)


# --------------------------------------------------------------------------- multi-zone
#
# Several zones, each with its own control schedule, whose hourly predictions SUM to the metered
# whole-building energy. The grid+OLS invariant is preserved by STACKING every zone's basis columns
# into one design matrix and least-squares fitting all zones' linear params at once, given one
# shared gridded time-constant set. Identifiability honesty: whole-building energy under-determines
# the split of conductance across zones -- it is recoverable only when the zones' schedules differ
# (breaking the column collinearity) or when each zone is sub-metered. For per-zone confidence, call
# the single-zone `calibrate` / `calibrate2` per zone instead (each supports its own tau).


@dataclass(frozen=True)
class ZoneModel:
    """One zone's calibrated model (a 1R1C :class:`RCModel` or a 2R2C :class:`RC2Model`)."""

    name: str
    model: Any  # RCModel | RC2Model (duck-typed .predict / .as_dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "model": self.model.as_dict()}


@dataclass(frozen=True)
class MultiZoneModel:
    """A fleet of :class:`ZoneModel` whose hourly predictions sum to the whole-building energy."""

    zones: tuple

    def predict(self, oat, schedules) -> np.ndarray:
        """Sum each zone's hourly energy. ``schedules`` is ``{zone_name: schedule}``."""
        total = None
        for z in self.zones:
            e = z.model.predict(oat, schedules[z.name])
            total = e if total is None else total + e
        return total if total is not None else np.zeros(len(np.asarray(oat)))

    def as_dict(self) -> dict:
        return {"zones": [z.as_dict() for z in self.zones]}


def calibrate_zones(
    oat, schedules, metered_energy, *, order: int = 1, interval: str = "hourly", tau_grid=None
) -> Calibration:
    """Jointly calibrate a multi-zone model to whole-building ``metered_energy`` (stacked OLS).

    ``schedules`` is ``{zone_name: {"setpoint", "conditioned"}}``. Every zone contributes its basis
    columns (1R1C when ``order=1``, 2R2C when ``order=2``) built at one **shared** gridded
    time-constant set; the columns are stacked and a single least-squares fit recovers all zones'
    linear params. The G14 acceptance gate counts all free params (linear + shared nonlinear).
    Returns a :class:`Calibration` whose ``model`` is a :class:`MultiZoneModel`; it flows through
    :func:`option_d_savings` unchanged when the as-found / as-corrected args are per-zone schedule
    dicts. Degrades (non-accepted) rather than raising on thin/degenerate data.
    """
    if order not in (1, 2):
        raise ValueError(f"order must be 1 or 2, got {order!r}")
    oat = np.asarray(oat, dtype=float)
    y = np.asarray(metered_energy, dtype=float)
    names = list(schedules)
    z = len(names)
    per_zone_cols = 2 if order == 1 else 3
    p = z * per_zone_cols + (1 if order == 1 else 3)  # linear params + shared nonlinear
    if not names or len(y) < p or int(np.isfinite(y).sum()) < p:
        note = f"insufficient data to calibrate {z} zone(s) at order {order}"
        return Calibration(model=MultiZoneModel(zones=()), fit=_nonaccepted(len(y), p, note))

    def _stack(params):
        cols = []
        for name in names:
            if order == 1:
                ddh, cond = _design(oat, schedules[name], params[0])
                cols += [ddh, -cond]
            else:
                dh_env, dh_mass, cond = _design2(oat, schedules[name], *params)
                cols += [dh_env, dh_mass, -cond]
        return np.column_stack(cols)

    def _build(params, beta):
        zones = []
        for zi, name in enumerate(names):
            b = beta[zi * per_zone_cols : (zi + 1) * per_zone_cols]
            if order == 1:
                m: object = RCModel(ua_eff=float(b[0]), gain_eff=float(b[1]), tau=float(params[0]))
            else:
                m = RC2Model(
                    ua_env=float(b[0]),
                    uc_mass=float(b[1]),
                    gain_eff=float(b[2]),
                    tau_air=float(params[0]),
                    tau_mass=float(params[1]),
                    w=float(params[2]),
                )
            zones.append(ZoneModel(name=name, model=m))
        return MultiZoneModel(zones=tuple(zones))

    grid: list[tuple[float, ...]]
    if order == 1:
        grid = [(t,) for t in (np.asarray(tau_grid) if tau_grid is not None else _tau_grid())]
    else:
        grid = [
            (ta, tm, w)
            for ta in np.linspace(1.0, 12.0, 10)
            for tm in np.linspace(12.0, 200.0, 12)
            for w in (0.4, 0.6, 0.8)
            if tm > ta
        ]
    best_sse, model, n_evals = float("inf"), None, 0
    for params in grid:
        X = _stack(params)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        cand = _build(params, beta)
        sse = float(np.sum((y - cand.predict(oat, schedules)) ** 2))
        n_evals += 1
        if sse < best_sse:
            best_sse, model = sse, cand

    if model is None:  # empty grid -> degrade, don't raise
        return Calibration(
            model=MultiZoneModel(zones=()), fit=_nonaccepted(len(y), p, "empty tau grid")
        )
    yhat = model.predict(oat, schedules)
    try:
        fit: FitStats | None = fit_stats(y, yhat, p=p, cv_rmse_max=cv_rmse_max_for(interval))
    except ValueError:
        fit = None
    if fit is None or fit.cv_rmse != fit.cv_rmse:
        fit = _nonaccepted(len(y), p, "multi-zone calibration produced a non-finite fit")
    return Calibration(model=model, fit=fit, tau_grid_n=n_evals)
