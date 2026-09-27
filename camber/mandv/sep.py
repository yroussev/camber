"""DOE SEP 50001 M&V Protocol arithmetic: primary energy, SEnPI, savings, RF (provisional).

Everything here follows the *SEP 50001 Program Measurement & Verification Protocol: 2019
Edition 2* (DOE / LBNL, October 31 2023) -- the **Protocol**, which is normative. The SEP 2019
*Guidance* document is not used as a source: its range-check worked example contains arithmetic
errors (issue #21).

* **Primary energy** (§5.1.1, Eq 1): ``ECP(*) = m(*) x ECD(*)``, with the Primary Energy
  Multipliers of **Annex B** (Tables 4A / 4B) as defaults and a user table on top
  (:data:`ANNEX_B_MULTIPLIERS`, :func:`primary_energy`). Only rows transcribed from the Protocol's
  own Annex B are included; anything else must come from the user.
* **SEnPI** (§7.1): Eq 5 ``SEnPI = ECP(Σ)r / ECP(Σ)b`` with one or both periods adjusted
  (:func:`senpi`), and Eq 6 -- the chained SEnPI, a **product** of the backcast EnPI from the
  baseline to the intermediate period and the forecast EnPI from the intermediate to the
  reporting period (:func:`chained_senpi`; Table 2 gives both factors).
* **Improvement** (§7.2, Eq 7): ``(1 - SEnPI) x 100`` (:func:`improvement_pct`).
* **Top-down savings** (§8.3 step 5, Eq 8-11): forecast, backcast, standard conditions and the
  chaining sum, which is **additive** (:func:`top_down_savings`).
* **Bottom-up reconciliation** (§8.3 step 5, Eq 12): ``RF = ESP_BU / ESP_TD``; below 0.80 the
  verified improvement is the top-down one times RF (:func:`bottom_up_reconciliation`).
* **The range rule** (§6.4.2.1): the *mean* of each relevant variable over the application period
  must fall within the observed fit range or within three standard deviations of the fit mean
  (:func:`sep_range_check`). CAMBER's primary extrapolation guard is the per-point coverage of
  :mod:`camber.mandv.coverage`; this is the secondary ``sep_range_valid`` verdict (decision D2 on
  #21), because a mild mean can hide hot extremes on a change-point slope.
* **Aggregation across energy types** (§6.2, §6.3.2): one model per energy type, the same method
  for every type, adjusted values converted to primary energy and summed
  (:func:`aggregate_energy_types`).

SEP says nothing about uncertainty; the bands here are CAMBER's (see :mod:`camber.mandv.methods`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import numpy as np

__all__ = [
    "ANNEX_B_MULTIPLIERS",
    "SEP_METHODS",
    "PrimaryEnergy",
    "primary_energy",
    "senpi",
    "chained_senpi",
    "improvement_pct",
    "top_down_savings",
    "RF_THRESHOLD",
    "Reconciliation",
    "bottom_up_reconciliation",
    "SEPRange",
    "sep_range_check",
    "FacilitySEP",
    "aggregate_energy_types",
]

#: The four SEP adjustment-model methods (SEP 2019 Ed. 2 §6.2.1-6.2.4).
SEP_METHODS = ("forecast", "backcast", "standard_conditions", "chaining")

#: Primary Energy Multipliers ``m(*)`` transcribed from SEP 2019 Ed. 2 **Annex B** (Tables 4A and
#: 4B, pp. 34-40; the two tables give the same multipliers). They convert *delivered* energy, in
#: energy units, to primary energy; converting physical units (kWh, therms, ton-hours, pounds of
#: steam) to energy is the user's job (Annex B gives the unit factors). Notes from the Protocol:
#: fired-boiler rows assume 75% combustion efficiency and chilled-water rows ``1/COP``; 3.0 is the
#: default for grid electricity and any other factor needs Verification Body approval; the
#: compressed-air row assumes a motor-driven compressor at 100 psi (7 bar) only; energy generated
#: or extracted on site is 1.0 (§5.1.1 NOTE). The fuel rows (all 1.0) are listed individually in
#: Annex B; ``biomass`` stands for its agricultural, herbaceous, woody, forest, urban and other
#: biogas / biomass / biofuel rows, all 1.0.
ANNEX_B_MULTIPLIERS: dict = {
    "grid_electricity": 3.0,
    "solar_electricity": 1.0,
    "wind_electricity": 1.0,
    "geothermal_electricity": 1.0,
    "steam_fired_boiler": 1.33,
    "steam_electric_boiler": 3.0,
    "hot_water_fired_boiler": 1.33,
    "hot_water_electric_boiler": 3.0,
    "solar_or_geothermal_hot_water_or_steam": 1.0,
    "chilled_water_fired_absorption_chiller": 1.25,
    "chilled_water_engine_driven_compressor": 0.83,
    "chilled_water_electric": 0.72,
    "compressed_air": 3.0,
    "natural_gas": 1.0,
    "propane": 1.0,
    "coal": 1.0,
    "petroleum_coke": 1.0,
    "crude_oil": 1.0,
    "conventional_gasoline": 1.0,
    "low_sulfur_gasoline": 1.0,
    "conventional_diesel": 1.0,
    "low_sulfur_diesel": 1.0,
    "still_gas": 1.0,
    "digester_gas": 1.0,
    "hydrogen_gas": 1.0,
    "liquid_hydrogen": 1.0,
    "biomass": 1.0,
    "onsite_generation": 1.0,
}


def _multipliers(types, user: Mapping | None) -> tuple:
    """``({type: m}, caveats)`` for ``types``: the user's table first, then Annex B."""
    user = dict(user or {})
    out, caveats = {}, []
    for t in types:
        if t in user:
            m = float(user[t])
            if not (np.isfinite(m) and m > 0):
                raise ValueError(f"multiplier for {t!r} must be a positive number, got {user[t]!r}")
            default = ANNEX_B_MULTIPLIERS.get(t)
            if default is None:
                caveats.append(
                    f"{t}: multiplier {m:g} is user-supplied (no Annex B row); SEP requires "
                    "Verification Body approval of site-specific multipliers (§5.1.1)"
                )
            elif m != default:
                caveats.append(
                    f"{t}: multiplier {m:g} replaces the Annex B default {default:g}; SEP requires "
                    "Verification Body approval of site-specific multipliers (§5.1.1)"
                )
            out[t] = m
        elif t in ANNEX_B_MULTIPLIERS:
            out[t] = ANNEX_B_MULTIPLIERS[t]
        else:
            raise ValueError(
                f"no primary energy multiplier for energy type {t!r}: it is not an Annex B row "
                f"({sorted(ANNEX_B_MULTIPLIERS)}); pass multipliers={{{t!r}: m}}"
            )
    return out, caveats


@dataclass
class PrimaryEnergy:
    """Delivered energy converted to primary energy (SEP 2019 Ed. 2 §5.1.1, Eq 1)."""

    total: float
    by_type: dict
    multipliers: dict
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


def primary_energy(delivered: Mapping, *, multipliers: Mapping | None = None) -> PrimaryEnergy:
    """Primary energy of delivered energy by type: ``ECP(*) = m(*) x ECD(*)`` (Eq 1), summed.

    ``delivered`` maps an energy type to its delivered (net) consumption in energy units.
    ``multipliers`` overrides or extends :data:`ANNEX_B_MULTIPLIERS`; a type in neither raises
    ``ValueError``. SEP §5.1.2: a net consumption calculated to be negative "shall be accounted for
    as zero" -- such a type is counted as zero, with a caveat. The multiplier chosen for a type must
    be the same in the baseline and reporting periods (§5.1.1): convert both with one table.
    """
    m, caveats = _multipliers(list(delivered), multipliers)
    by_type = {}
    for t, e in delivered.items():
        e = float(e)
        if not np.isfinite(e):
            raise ValueError(f"delivered energy for {t!r} is not finite: {e!r}")
        if e < 0:
            caveats.append(
                f"{t}: net consumption {e:g} is negative and is counted as zero (§5.1.2)"
            )
            e = 0.0
        by_type[t] = m[t] * e
    return PrimaryEnergy(
        total=float(sum(by_type.values())), by_type=by_type, multipliers=m, caveats=caveats
    )


def senpi(reporting: float, baseline: float) -> float:
    """SEnPI = ``ECP(Σ)r / ECP(Σ)b`` (SEP 2019 Ed. 2 §7.1, Eq 5), one or both periods adjusted.

    Forecast: ``E^o_r / E^a_b|r``; backcast: ``E^a_r|b / E^o_b``; standard conditions:
    ``E^a_r|s / E^a_b|s`` (Table 2). Below 1.0 energy performance improved.
    """
    reporting, baseline = float(reporting), float(baseline)
    if baseline == 0:
        raise ValueError("SEnPI is undefined for a zero baseline")
    return reporting / baseline


def chained_senpi(
    observed_baseline: float,
    intermediate_at_baseline: float,
    intermediate_at_reporting: float,
    observed_reporting: float,
) -> float:
    """Chained SEnPI (SEP 2019 Ed. 2 §7.1, Eq 6 and Table 2) -- a **product** of two EnPIs::

        SEnPI = (E^a_i|b / E^o_b) x (E^o_r / E^a_i|r)

    the backcast EnPI from the baseline to the intermediate period (the intermediate model at
    baseline conditions over observed baseline energy) times the forecast EnPI from the
    intermediate to the reporting period.
    """
    return senpi(intermediate_at_baseline, observed_baseline) * senpi(
        observed_reporting, intermediate_at_reporting
    )


def improvement_pct(senpi_value: float) -> float:
    """Energy performance improvement, percent: ``(1 - SEnPI) x 100`` (SEP 2019 Ed. 2 Eq 7)."""
    return (1.0 - float(senpi_value)) * 100.0


_TERMS = {
    "forecast": ("adjusted_baseline", "observed_reporting"),
    "backcast": ("observed_baseline", "adjusted_reporting"),
    "standard_conditions": ("adjusted_baseline", "adjusted_reporting"),
    "chaining": (
        "observed_baseline",
        "intermediate_at_baseline",
        "intermediate_at_reporting",
        "observed_reporting",
    ),
}


def _check_terms(method: str, terms: dict) -> dict:
    if method not in _TERMS:
        raise ValueError(f"unknown SEP method {method!r}; use one of {SEP_METHODS}")
    need = _TERMS[method]
    extra = sorted(k for k, v in terms.items() if v is not None and k not in need)
    missing = [k for k in need if terms.get(k) is None]
    if missing or extra:
        raise ValueError(
            f"SEP {method} takes exactly {list(need)}"
            + (f"; missing {missing}" if missing else "")
            + (f"; unexpected {extra}" if extra else "")
        )
    return {k: float(terms[k]) for k in need}


def top_down_savings(
    method: str,
    *,
    observed_baseline: float | None = None,
    observed_reporting: float | None = None,
    adjusted_baseline: float | None = None,
    adjusted_reporting: float | None = None,
    intermediate_at_baseline: float | None = None,
    intermediate_at_reporting: float | None = None,
) -> float:
    """Top-down energy savings ``ESP_TD`` (SEP 2019 Ed. 2 §8.3 step 5, Eq 8-11).

    * forecast (Eq 8): ``adjusted_baseline - observed_reporting`` (``E^a_b|r - E^o_r``)
    * backcast (Eq 9): ``observed_baseline - adjusted_reporting`` (``E^o_b - E^a_r|b``)
    * standard_conditions (Eq 10): ``adjusted_baseline - adjusted_reporting``
      (``E^a_b|s - E^a_r|s``)
    * chaining (Eq 11), **additive**: ``(observed_baseline - intermediate_at_baseline) +
      (intermediate_at_reporting - observed_reporting)``

    Each method takes exactly its own terms; a missing or extra one raises ``ValueError``.
    """
    t = _check_terms(
        method,
        {
            "observed_baseline": observed_baseline,
            "observed_reporting": observed_reporting,
            "adjusted_baseline": adjusted_baseline,
            "adjusted_reporting": adjusted_reporting,
            "intermediate_at_baseline": intermediate_at_baseline,
            "intermediate_at_reporting": intermediate_at_reporting,
        },
    )
    if method == "forecast":
        return t["adjusted_baseline"] - t["observed_reporting"]
    if method == "backcast":
        return t["observed_baseline"] - t["adjusted_reporting"]
    if method == "standard_conditions":
        return t["adjusted_baseline"] - t["adjusted_reporting"]
    return (t["observed_baseline"] - t["intermediate_at_baseline"]) + (
        t["intermediate_at_reporting"] - t["observed_reporting"]
    )


def _senpi_of(method: str, t: dict) -> float:
    if method == "forecast":
        return senpi(t["observed_reporting"], t["adjusted_baseline"])
    if method == "backcast":
        return senpi(t["adjusted_reporting"], t["observed_baseline"])
    if method == "standard_conditions":
        return senpi(t["adjusted_reporting"], t["adjusted_baseline"])
    return chained_senpi(
        t["observed_baseline"],
        t["intermediate_at_baseline"],
        t["intermediate_at_reporting"],
        t["observed_reporting"],
    )


@dataclass
class Reconciliation:
    """The SEP bottom-up to top-down reconciliation (SEP 2019 Ed. 2 §8.3 step 5, Eq 12)."""

    rf: float | None
    esp_bu: float
    esp_td: float
    top_down_improvement_pct: float
    verified_improvement_pct: float
    scaled: bool
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


#: The reconciliation-factor threshold of SEP 2019 Ed. 2 §8.3 step 5.
RF_THRESHOLD = 0.80


def bottom_up_reconciliation(
    esp_bu: float, esp_td: float, top_down_improvement_pct: float
) -> Reconciliation:
    """``RF = ESP_BU / ESP_TD`` (Eq 12) and the verified improvement percentage.

    ``esp_bu`` is the bottom-up estimate -- the Register's actual annual primary energy savings
    summed over energy types (§8.3 steps 2-4); ``esp_td`` the top-down savings (Eq 8-11,
    :func:`top_down_savings`); ``top_down_improvement_pct`` the Eq 7 percentage. With
    ``RF >= 0.80`` the verified improvement is the top-down one; below 0.80 it is the top-down one
    multiplied by RF. An RF above 1 never scales the improvement up. Without a positive top-down
    saving there is nothing to reconcile against: ``rf`` is ``None`` and the top-down figure
    stands, with a caveat.
    """
    esp_bu, esp_td = float(esp_bu), float(esp_td)
    td = float(top_down_improvement_pct)
    if not esp_td > 0:
        return Reconciliation(
            rf=None,
            esp_bu=esp_bu,
            esp_td=esp_td,
            top_down_improvement_pct=td,
            verified_improvement_pct=td,
            scaled=False,
            caveats=[
                f"the top-down saving {esp_td:g} is not positive, so Eq 12's reconciliation "
                "factor is undefined; the top-down improvement is reported unreconciled"
            ],
        )
    rf = esp_bu / esp_td
    scaled = rf < RF_THRESHOLD
    return Reconciliation(
        rf=rf,
        esp_bu=esp_bu,
        esp_td=esp_td,
        top_down_improvement_pct=td,
        verified_improvement_pct=td * rf if scaled else td,
        scaled=scaled,
    )


# --------------------------------------------------------------------------- the range rule


@dataclass
class SEPRange:
    """The SEP mean-in-range verdict of one model applied to one set of conditions (§6.4.2.1).

    ``valid`` is ``None`` when it cannot be evaluated (a model with no recorded driver support,
    such as TOWT or a duck-typed model). Per variable: the application mean, the observed fit
    range, the fit mean and standard deviation, and which arm of the rule it passed.
    """

    valid: bool | None
    variables: list = field(default_factory=list)
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain, JSON-safe dict."""
        return _json_safe(asdict(self))


def _json_safe(x):
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    return x


def sep_range_check(model, drivers) -> SEPRange:
    """SEP's quantitative range rule (SEP 2019 Ed. 2 §6.4.2.1; SEP 2012 §3.4.6).

    For each of the model's relevant variables, the **mean** over the application ``drivers`` must
    fall within the range of the data that went into the model **or** not exceed three standard
    deviations from that data's mean. The fit data are the values the model recorded at fit time,
    so anything excluded from the fit (outliers, non-finite rows) is excluded from the range too,
    as the Protocol requires. The standard deviation is the sample one (``ddof=1``), a CAMBER
    choice the Protocol does not fix. A variable that did not vary keeps its single-value range
    (SEP 2012 §3.4.7).

    This is CAMBER's **secondary** verdict (D2 on #21): the per-point tiers of
    :func:`camber.mandv.coverage.assess_coverage` remain the primary guard.
    """
    from ._design import fit_record

    rec = fit_record(model)
    if rec is None or not rec.linear:
        return SEPRange(
            valid=None,
            caveats=[
                f"SEP range rule not evaluated: {type(model).__name__} records no driver support"
            ],
        )
    D = np.asarray(drivers, dtype=float)
    D = D[:, None] if D.ndim == 1 else D
    if D.shape[1] != len(rec.values):
        raise ValueError(f"drivers have {D.shape[1]} columns; the model has {len(rec.values)}")
    D = D[np.all(np.isfinite(D), axis=1)]
    if not len(D):
        return SEPRange(valid=None, caveats=["SEP range rule not evaluated: no finite drivers"])
    rows, ok_all = [], True
    for j, (name, v) in enumerate(zip(rec.names, rec.values)):
        v = np.asarray(v, dtype=float)
        v = v[np.isfinite(v)]
        mean_app = float(D[:, j].mean())
        lo, hi = float(v.min()), float(v.max())
        mu = float(v.mean())
        sd = float(v.std(ddof=1)) if len(v) > 1 else 0.0
        in_range = lo <= mean_app <= hi
        within = abs(mean_app - mu) <= 3.0 * sd
        ok = bool(in_range or within)
        ok_all &= ok
        rows.append(
            {
                "name": name,
                "mean_applied": mean_app,
                "fit_min": lo,
                "fit_max": hi,
                "fit_mean": mu,
                "fit_sd": sd,
                "in_observed_range": bool(in_range),
                "within_3sd": bool(within),
                "valid": ok,
            }
        )
    caveats = []
    if not ok_all:
        bad = ", ".join(r["name"] for r in rows if not r["valid"])
        caveats.append(
            f"SEP range rule (§6.4.2.1) fails for {bad}: the application mean lies outside the "
            "fitted range and more than 3 standard deviations from the fit mean"
        )
    return SEPRange(valid=bool(ok_all), variables=rows, caveats=caveats)


# --------------------------------------------------------------------------- aggregation


@dataclass
class FacilitySEP:
    """Facility SEnPI across energy types on a primary-energy basis (SEP 2019 Ed. 2 §6.3.2, §7).

    ``terms`` are the SEP quantities summed over energy types after each was multiplied by its
    primary energy multiplier; ``by_type`` holds each type's primary terms. ``esp_td_uncertainty``
    combines the per-type savings bands in quadrature (IPMVP 2012 B-19; the types' models are
    fitted separately, so independent) at Student's t on the smallest degrees of freedom.
    ``senpi_uncertainty`` is the delta-method band ``SEnPI x band / denominator`` for the single
    ratio methods, and ``None`` for chaining (a product of two ratios per type does not reduce to
    one denominator).
    """

    method: str
    senpi: float | None
    improvement_pct: float | None
    esp_td: float | None
    esp_td_uncertainty: float | None
    senpi_uncertainty: float | None
    confidence: float | None
    terms: dict
    by_type: dict
    multipliers: dict
    declined: bool = False
    declined_reason: str | None = None
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


def aggregate_energy_types(results: Mapping, *, multipliers: Mapping | None = None) -> FacilitySEP:
    """Combine per-energy-type method results into one facility SEnPI on primary energy.

    ``results`` maps an energy type (a key of :data:`ANNEX_B_MULTIPLIERS` or of ``multipliers``)
    to that type's :class:`~camber.mandv.methods.MethodResult`, each in **delivered** energy units.
    SEP requires one model per energy type and "the same adjustment model method ... for each
    energy type" (§6.2), so mixed methods raise ``ValueError``; so does a result that carries no
    SEP terms (``sequential_chain`` is a CAMBER extension, not an SEP method). A declined type
    declines the facility figure. Each type's terms are multiplied by its multiplier (Eq 1) and
    summed (§6.3.2) before Eq 5-11 are applied. Energy types totalling 5.0% or less of consumption
    may be left out in both periods (§5.1.3); that choice is the caller's.
    """
    if not results:
        raise ValueError("no energy-type results to aggregate")
    methods = {getattr(r, "method", None) for r in results.values()}
    if len(methods) != 1:
        raise ValueError(
            f"SEP requires the same adjustment model method for every energy type (§6.2); got "
            f"{sorted(map(str, methods))}"
        )
    (method,) = methods
    if method not in _TERMS:
        raise ValueError(
            f"method {method!r} is not an SEP method ({SEP_METHODS}); it cannot be aggregated "
            "into an SEnPI"
        )
    m, caveats = _multipliers(list(results), multipliers)
    declined = [t for t, r in results.items() if r.declined]
    need = _TERMS[method]
    by_type: dict[str, dict] = {}
    for t, r in results.items():
        terms = r.sep_terms or {}
        if not declined and any(terms.get(k) is None for k in need):
            raise ValueError(f"{t}: the result carries no SEP terms for {method}")
        by_type[t] = {k: (None if terms.get(k) is None else m[t] * terms[k]) for k in need}
    conf = {r.confidence for r in results.values()}
    confidence = conf.pop() if len(conf) == 1 else None
    if declined:
        return FacilitySEP(
            method=method,
            senpi=None,
            improvement_pct=None,
            esp_td=None,
            esp_td_uncertainty=None,
            senpi_uncertainty=None,
            confidence=confidence,
            terms={},
            by_type=by_type,
            multipliers=m,
            declined=True,
            declined_reason=f"declined for energy type(s): {', '.join(declined)}",
            caveats=caveats,
        )
    total = {k: math.fsum(by_type[t][k] for t in by_type) for k in need}
    s = _senpi_of(method, total)
    esp = top_down_savings(method, **total)
    band = senpi_band = None
    if confidence is None:
        caveats.append("the energy types' bands are at different confidence levels; not combined")
    else:
        from .stats import _t_value

        var, dfs, ok = 0.0, [], True
        for t, r in results.items():
            if r.abs_uncertainty is None or not np.isfinite(r.abs_uncertainty):
                ok = False
                break
            var += (m[t] * r.abs_uncertainty / _t_value(r.confidence, r.df)) ** 2
            if r.df is not None:
                dfs.append(r.df)
        if ok:
            band = _t_value(confidence, min(dfs) if dfs else None) * math.sqrt(var)
            denom = {
                "forecast": "adjusted_baseline",
                "backcast": "observed_baseline",
                "standard_conditions": "adjusted_baseline",
            }.get(method)
            if denom is not None and total[denom]:
                senpi_band = s * band / abs(total[denom])
        else:
            caveats.append("an energy type has no uncertainty band; the facility band is omitted")
    if method == "chaining":
        caveats.append("no SEnPI band for an aggregated chain: see each type's own enpi band")
    return FacilitySEP(
        method=method,
        senpi=s,
        improvement_pct=improvement_pct(s),
        esp_td=esp,
        esp_td_uncertainty=band,
        senpi_uncertainty=senpi_band,
        confidence=confidence,
        terms=total,
        by_type=by_type,
        multipliers=m,
        caveats=caveats,
    )
