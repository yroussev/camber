"""Weather-normalized annual savings (IPMVP "normalized savings" / ASHRAE G14).

CAMBER's :func:`camber.mandv.stats.avoided_energy_savings` answers "how much did we avoid
given the *actual* reporting weather" (IPMVP avoided energy use). This module answers the
complementary "what is the saving in a *typical* year" -- IPMVP **normalized savings** --
by projecting **both** the baseline and the reporting change-point models onto a single
normal-year (e.g. TMY) temperature set and differencing their normalized annual
consumption (NAC). That removes weather from the before/after comparison entirely, so a
hot reporting year doesn't flatter or penalize the result.

    normalized savings = NAC(baseline model) - NAC(reporting model)   over the normal year

Uncertainty follows ASHRAE Guideline 14 Annex B: each projected NAC carries a fractional
uncertainty 1.26 * CV(RMSE) * sqrt((n/M) * (1 + 2/n)) (M normal-year periods, n model-fit
points), and the two are combined in quadrature at the chosen confidence. numpy only;
operates on any model with a ``predict(temps)`` method (e.g. a change-point or TOWT model).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

_TVAL = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960, 0.975: 2.241, 0.99: 2.326}


def normalized_annual_consumption(model, temps) -> float:
    """Normalized annual consumption: the model's predicted energy summed over ``temps``.

    ``temps`` is a normal-year temperature set at the model's period granularity (e.g. 12
    monthly means for a monthly model, or 8760 hourly values for an hourly one); the sum of
    the per-period predictions is the weather-normalized annual energy.
    """
    return float(np.asarray(model.predict(np.asarray(temps, dtype=float)), dtype=float).sum())


@dataclass
class NormalizedSavings:
    """Weather-normalized annual savings with a G14 uncertainty band."""

    nac_baseline: float
    nac_reporting: float
    normalized_savings: float       # nac_baseline - nac_reporting
    savings_pct: float              # of normalized baseline
    fractional_uncertainty: float   # of the savings, at ``confidence``
    abs_uncertainty: float          # +/- energy at ``confidence``
    confidence: float
    n_normal_periods: int

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def _rel_unc(cv_rmse: float, n: int, m: int) -> float:
    """G14 Annex-B fractional uncertainty of a single projected sum over ``m`` periods."""
    if cv_rmse != cv_rmse or n <= 0 or m <= 0:
        return float("nan")
    return 1.26 * cv_rmse * np.sqrt((n / m) * (1.0 + 2.0 / n))


def normalized_savings(baseline_model, reporting_model, normal_temps, *,
                       baseline_cv_rmse: float, n_baseline: int,
                       reporting_cv_rmse: float | None = None,
                       n_reporting: int | None = None,
                       confidence: float = 0.90) -> NormalizedSavings:
    """Weather-normalized annual savings between two fitted models over a normal year.

    Projects ``baseline_model`` and ``reporting_model`` onto ``normal_temps`` (the typical
    year) and differences their NAC. Provide each model's fit CV(RMSE) and number of fit
    points for the G14 uncertainty band; if the reporting fit stats are omitted they default
    to the baseline's. ``confidence`` selects the t-multiplier (0.90 -> 1.645).
    """
    temps = np.asarray(normal_temps, dtype=float)
    m = int(len(temps))
    nac_b = normalized_annual_consumption(baseline_model, temps)
    nac_r = normalized_annual_consumption(reporting_model, temps)
    savings = nac_b - nac_r
    pct = savings / nac_b if nac_b else float("nan")

    r_cv = reporting_cv_rmse if reporting_cv_rmse is not None else baseline_cv_rmse
    r_n = n_reporting if n_reporting is not None else n_baseline
    rel_b, rel_r = _rel_unc(baseline_cv_rmse, n_baseline, m), _rel_unc(r_cv, r_n, m)
    t = _TVAL.get(round(confidence, 3), 1.645)
    if rel_b == rel_b and rel_r == rel_r:
        abs_unc = t * float(np.sqrt((rel_b * nac_b) ** 2 + (rel_r * nac_r) ** 2))
    else:
        abs_unc = float("nan")
    frac = abs_unc / abs(savings) if (savings and abs_unc == abs_unc) else float("nan")

    return NormalizedSavings(
        nac_baseline=round(nac_b, 2), nac_reporting=round(nac_r, 2),
        normalized_savings=round(savings, 2),
        savings_pct=round(pct, 4) if pct == pct else float("nan"),
        fractional_uncertainty=round(frac, 4) if frac == frac else float("nan"),
        abs_uncertainty=round(abs_unc, 2) if abs_unc == abs_unc else float("nan"),
        confidence=confidence, n_normal_periods=m,
    )
