"""CalTRACK / IPMVP-aligned whole-building savings (NMEC).

CAMBER's M&V pieces already implement the IPMVP Option-C / CalTRACK *normalized
metered energy consumption* workflow; this module assembles them into one call
using CalTRACK vocabulary and documents the correspondence so results can be
cross-checked against OpenEEmeter (eemeter). See ``docs/MANDV.md`` for the full
terminology bridge and the eemeter cross-check recipe.

Correspondence:

| CalTRACK / IPMVP term        | CAMBER                                          |
|------------------------------|-------------------------------------------------|
| baseline-period model        | `models.best_model` change-point (daily)        |
| fit metrics CV(RMSE) / NMBE  | `stats.fit_stats`                               |
| avoided energy use           | `stats.avoided_energy_savings` (G14 Annex-B FSU)|

This is the daily method (CalTRACK Daily). The hourly method maps onto
:mod:`camber.mandv.towt`; assembling that here is future work.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .intervalfit import daily_energy_vs_temp
from .models import N_PARAMS, best_model
from .nonroutine import residual_outliers
from .stats import SavingsResult, avoided_energy_savings, fit_stats


@dataclass
class NMECResult:
    """Whole-building NMEC savings: the baseline fit plus avoided energy use."""

    model_kind: str                 # change-point family chosen for the baseline
    baseline_cv_rmse: float         # baseline fit CV(RMSE), fraction
    baseline_nmbe: float            # baseline fit NMBE (net determination bias)
    baseline_r2: float
    baseline_n: int                 # baseline days used (after any NRE exclusion)
    savings: SavingsResult          # avoided energy + fractional savings uncertainty
    n_non_routine_excluded: int = 0  # baseline days dropped as non-routine events

    def as_dict(self) -> dict:
        """Return the result (with the nested savings) as a plain dict."""
        d = asdict(self)
        d["savings"] = self.savings.as_dict()
        return d


def caltrack_savings(baseline_energy: pd.Series, baseline_temp: pd.Series,
                     reporting_energy: pd.Series, reporting_temp: pd.Series, *,
                     confidence: float = 0.90, min_days: int = 60,
                     rate_is_energy_rate: bool = False,
                     exclude_non_routine: bool = False, nre_z: float = 3.5) -> NMECResult:
    """CalTRACK Daily / IPMVP Option-C avoided energy use from baseline + reporting.

    Fits a change-point baseline on daily energy vs daily-mean temperature, projects
    it onto the reporting period's weather, and reports avoided energy use with
    ASHRAE G14 Annex-B fractional savings uncertainty. ``*_energy`` are interval
    meter series and ``*_temp`` the matching outdoor temperatures;
    ``rate_is_energy_rate`` marks energy that is a rate (BTU/hr) rather than per-
    interval energy.

    With ``exclude_non_routine`` the baseline is screened for non-routine events
    (days whose residual is a robust outlier at modified-z > ``nre_z``); those days
    are dropped and the baseline refit, so a shutdown or anomaly doesn't skew it.
    """
    base = daily_energy_vs_temp(baseline_energy, baseline_temp,
                                rate_is_energy_rate=rate_is_energy_rate)
    if len(base) < min_days:
        raise ValueError(f"need >= {min_days} baseline days, got {len(base)}")

    excluded = 0
    if exclude_non_routine:
        m0 = best_model(base["oat"].to_numpy(), base["energy"].to_numpy())
        nre = residual_outliers(base["energy"], base["oat"], m0, z=nre_z)
        excluded = int(nre.sum())
        if excluded:
            base = base[~nre.to_numpy()]

    model = best_model(base["oat"].to_numpy(), base["energy"].to_numpy())
    p = N_PARAMS[model.kind]
    st = fit_stats(base["energy"].to_numpy(),
                   model.predict(base["oat"].to_numpy()), p)

    rep = daily_energy_vs_temp(reporting_energy, reporting_temp,
                               rate_is_energy_rate=rate_is_energy_rate)
    if rep.empty:
        raise ValueError("no usable reporting-period data")
    savings = avoided_energy_savings(
        model, rep["oat"].to_numpy(), rep["energy"].to_numpy(),
        cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=p, confidence=confidence)

    return NMECResult(model_kind=model.kind, baseline_cv_rmse=st.cv_rmse,
                      baseline_nmbe=st.nmbe, baseline_r2=st.r2, baseline_n=st.n,
                      savings=savings, n_non_routine_excluded=excluded)
