"""Thermal comfort (PMV/PPD) per ASHRAE Standard 55 / ISO 7730 -- overcooling focus.

Implements the Fanger Predicted Mean Vote (PMV) and Predicted Percentage of
Dissatisfied (PPD) model (ASHRAE 55 Normative Appendix B / ISO 7730). The Fanger
equations are a long-published heat-balance model (not copyrightable); this is an
independent implementation validated against the standards' worked examples.

Purpose here: quantify *cold-side* discomfort. A negative PMV means occupants feel
cool/cold; for a building that overcools, flagging the fraction of occupied hours
with PMV below -0.5 (the Std-55 acceptable band edge) turns "it overcools" into a
defensible comfort metric.

Inputs: air temperature (degF), mean radiant temperature (degF, defaults to air
temp if unknown), air speed (m/s), relative humidity (%), metabolic rate (met),
clothing insulation (clo).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


def f_to_c(f):
    """Convert degF to degC (array-friendly)."""
    return (np.asarray(f, dtype=float) - 32.0) * 5.0 / 9.0


def pmv(ta_c, tr_c, vel, rh, met, clo):
    """Fanger PMV for one condition (all SI). ta_c/tr_c degC, vel m/s, rh %,
    met in met, clo in clo. Returns the predicted mean vote (-3..+3)."""
    pa = rh * 10.0 * np.exp(16.6536 - 4030.183 / (ta_c + 235.0))  # vapor pressure, Pa
    icl = 0.155 * clo                       # clothing insulation, m2K/W
    m = met * 58.15                         # metabolic rate, W/m2
    w = 0.0                                  # external work, assumed 0
    mw = m - w
    fcl = 1.05 + 0.645 * icl if icl > 0.078 else 1.0 + 1.29 * icl
    hcf = 12.1 * np.sqrt(vel)               # forced convection coeff
    taa = ta_c + 273.0
    tra = tr_c + 273.0
    # iterate clothing surface temperature
    tcla = taa + (35.5 - ta_c) / (3.96 * fcl)
    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100.0
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4
    xn = tcla / 100.0
    xf = xn
    for _ in range(150):
        xf = (xf + xn) / 2.0
        hcn = 2.38 * abs(100.0 * xf - taa) ** 0.25
        hc = max(hcf, hcn)
        xn = (p5 + p4 * hc - p2 * xf ** 4) / (100.0 + p3 * hc)
        if abs(xn - xf) < 1e-5:
            break
    tcl = 100.0 * xn - 273.0
    # heat-loss components
    hl1 = 3.05e-3 * (5733.0 - 6.99 * mw - pa)            # skin diffusion
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0     # sweat
    hl3 = 1.7e-5 * m * (5867.0 - pa)                     # latent respiration
    hl4 = 0.0014 * m * (34.0 - ta_c)                     # dry respiration
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)    # radiation
    hl6 = fcl * hc * (tcl - ta_c)                        # convection
    ts = 0.303 * np.exp(-0.036 * m) + 0.028
    return float(ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6))


def _pmv_vec(ta_c, tr_c, vel, rh, met, clo):
    """Vectorized Fanger PMV over temperature arrays (SI units).

    Numerically identical to :func:`pmv` applied element-wise: the same fixed-point
    iteration with the same 1e-5 convergence break, but each element is *frozen*
    once it converges so continuing the shared loop cannot perturb it. ``ta_c`` and
    ``tr_c`` are arrays (degC); ``vel``/``rh``/``met``/``clo`` are scalars.
    """
    ta_c = np.asarray(ta_c, dtype=float)
    tr_c = np.asarray(tr_c, dtype=float)
    pa = rh * 10.0 * np.exp(16.6536 - 4030.183 / (ta_c + 235.0))
    icl = 0.155 * clo
    m = met * 58.15
    w = 0.0
    mw = m - w
    fcl = 1.05 + 0.645 * icl if icl > 0.078 else 1.0 + 1.29 * icl
    hcf = 12.1 * np.sqrt(vel)
    taa = ta_c + 273.0
    tra = tr_c + 273.0
    tcla = taa + (35.5 - ta_c) / (3.96 * fcl)
    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100.0
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4
    xn = tcla / 100.0
    xf = xn.copy()
    hc = np.full_like(xn, hcf)
    converged = np.zeros(xn.shape, dtype=bool)
    for _ in range(150):
        upd = ~converged
        xf_new = (xf + xn) / 2.0
        hcn = 2.38 * np.abs(100.0 * xf_new - taa) ** 0.25
        hc_new = np.maximum(hcf, hcn)
        xn_new = (p5 + p4 * hc_new - p2 * xf_new ** 4) / (100.0 + p3 * hc_new)
        xf = np.where(upd, xf_new, xf)
        hc = np.where(upd, hc_new, hc)
        xn = np.where(upd, xn_new, xn)
        converged = converged | (upd & (np.abs(xn_new - xf_new) < 1e-5))
        if converged.all():
            break
    tcl = 100.0 * xn - 273.0
    hl1 = 3.05e-3 * (5733.0 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7e-5 * m * (5867.0 - pa)
    hl4 = 0.0014 * m * (34.0 - ta_c)
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)
    hl6 = fcl * hc * (tcl - ta_c)
    ts = 0.303 * np.exp(-0.036 * m) + 0.028
    return ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)


def ppd(pmv_value):
    """Predicted Percentage Dissatisfied from PMV (%, array-friendly)."""
    v = np.asarray(pmv_value, dtype=float)
    out = 100.0 - 95.0 * np.exp(-0.03353 * v ** 4 - 0.2179 * v ** 2)
    return float(out) if out.ndim == 0 else out


def pmv_f(ta_f, tr_f=None, vel=0.1, rh=50.0, met=1.1, clo=0.6):
    """PMV from degF inputs (tr defaults to air temp). Convenience wrapper.

    Defaults: still air (0.1 m/s), 50% RH, seated office metabolic rate (1.1 met),
    light clothing (0.6 clo) -- adjust per the space.
    """
    ta_c = float(f_to_c(ta_f))
    tr_c = ta_c if tr_f is None else float(f_to_c(tr_f))
    return pmv(ta_c, tr_c, vel, rh, met, clo)


@dataclass
class ComfortResult:
    """Thermal-comfort (PMV/PPD) summary over occupied hours for one zone."""

    equip: str
    n: int
    pmv_median: float
    ppd_median: float
    pct_cold: float        # % occupied hours PMV < -0.5 (cool/cold side)
    pct_hot: float         # % PMV > +0.5
    pct_comfortable: float # % within +/-0.5 (Std-55 acceptable band)
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def comfort_series(space_temp_f, *, occupied_mask=None, mrt_f=None, vel=0.1,
                   rh=50.0, met=1.1, clo=0.6, equip="zone") -> ComfortResult | None:
    """PMV/PPD comfort summary over a zone temperature series (degF).

    ``occupied_mask`` (bool Series aligned to the temps) restricts to occupied
    hours. ``mrt_f`` optional mean-radiant-temp series (else = air temp). Flags the
    cold side: % of hours PMV < -0.5 -- the overcooling metric.
    """
    s = space_temp_f.dropna()
    if occupied_mask is not None:
        s = s[occupied_mask.reindex(s.index, fill_value=False)]
    s = s[(s > 40) & (s < 95)]              # plausible indoor range
    if len(s) < 10:
        return None
    ta = s.to_numpy(dtype=float)
    if mrt_f is not None:
        mrt = mrt_f.reindex(s.index).to_numpy(dtype=float)
        tr = np.where(np.isnan(mrt), ta, mrt)   # missing MRT -> air temp (as pmv_f)
    else:
        tr = ta
    pmvs = _pmv_vec(f_to_c(ta), f_to_c(tr), vel, rh, met, clo)
    ppds = np.asarray(ppd(pmvs), dtype=float)
    return ComfortResult(
        equip=equip, n=int(len(s)),
        pmv_median=round(float(np.median(pmvs)), 2),
        ppd_median=round(float(np.median(ppds)), 1),
        pct_cold=round(100.0 * float((pmvs < -0.5).mean()), 1),
        pct_hot=round(100.0 * float((pmvs > 0.5).mean()), 1),
        pct_comfortable=round(100.0 * float((np.abs(pmvs) <= 0.5).mean()), 1),
        coverage_start=str(s.index.min()), coverage_end=str(s.index.max()),
    )
