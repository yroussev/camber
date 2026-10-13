"""Industry-standard names for CAMBER's M&V models and methods: one registry (0.105, #163).

Every M&V model kind, degree-day kind, savings method and M&V function CAMBER exposes has one row
here: the CAMBER code (the ``kind`` string stored in data, or a function name), the label reports
and charts show, the **standard name** an evaluator or testing portal expects, the citation (ids
in :mod:`camber.references`), the CAMBER callable that implements it, and the known differences
from the cited standard. Reports, the ``mv_baseline`` summary, the energy-signature and OAT-scatter
legends and the "Method correspondence" table of ``docs/MANDV.md`` (written by
``scripts/mv_names_doc.py``) read their names from here, so every output says the same thing.

The ``kind`` codes themselves (``"3PC"``, ``"DD-H"``, ``"forecast"``) are unchanged: they are what
stored baselines, findings and JSON outputs carry. Only the human-facing label comes from the
registry.

**Standard name, or none.** A row whose ``standard_name`` is empty is a CAMBER construct with no
standard counterpart; its ``differences`` say what it is. A non-empty ``differences`` on a row
with a standard name lists where CAMBER's implementation departs from the cited source -- report
the result under the standard name *with* those differences, or under the CAMBER label.
``differences`` are descriptions only, safe to paste into a report verbatim (0.105, #174); how to
cite lives in ``docs/MANDV.md``, "Naming and citations". :func:`ensemble_description` builds the
"Ensemble approach combining ..." sentence for a prediction that combines several models.

Provisional (0.105): the row set and wording may change before 1.0; the ``kind`` codes will not.
Dependency-free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = [
    "MVName",
    "MV_NAMES",
    "GROUPS",
    "SHORT_CITATIONS",
    "mv_name",
    "label",
    "model_label",
    "citations",
    "component_description",
    "ensemble_description",
]

_G14 = "ashrae-g14-2014"
_IMT = "ashrae-rp1050-imt"
_PRISM = "fels-1986-prism"
_TOWT = "mathieu-2011-towt"
_RMV2 = "lbnl-rmv2"
_CALTRACK = "caltrack-2"
_IPMVP = "evo-ipmvp-core-2022"
_SEP = "doe-sep-mv-2019"
_LBNL_TEST = ("granderson-2015-baselines", "lbnl-187225", "lbnl-6963e")

#: the row groups, in table order
GROUPS = (
    "change-point model",
    "degree-day model",
    "other baseline model",
    "savings method",
    "IPMVP option",
    "savings function",
    "tracking and forecasting",
)


@dataclass(frozen=True)
class MVName:
    """One M&V name: the CAMBER code, its label, the standard name and where they differ.

    ``citation_id`` is a tuple of :mod:`camber.references` ids, primary first (empty when no
    standard defines the thing). ``callable`` is the dotted path of the CAMBER function or class
    that implements it. ``group`` is one of :data:`GROUPS`.
    """

    code: str
    label: str
    standard_name: str
    citation_id: tuple
    callable: str
    differences: str
    group: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["citation_id"] = list(self.citation_id)
        return d


_CP_SEARCH = (
    "Change points grid-searched over 40 points between the 5th and 95th temperature "
    "percentiles, slopes by least squares at each (the RP-1050 approach)."
)
_BIC = (
    " `best_model` chooses among the kinds by BIC, a CAMBER choice (G14 and the IMT do not "
    "prescribe a selection criterion)."
)
_ZERO = (
    "CAMBER extension, not a G14 / IMT model: the intercept is fixed at zero (no base load), so "
    "p counts the slope(s) and change point(s) only. Opt-in (not in `best_model`'s default set)."
)

_ROWS = (
    # --- change-point models (camber.mandv.models; G14 / ASHRAE RP-1050 IMT) -------------------
    MVName(
        "2P",
        "2P",
        "Two-parameter (2P) linear regression model",
        (_G14, _IMT),
        "camber.mandv.models.fit_model",
        "",
        "change-point model",
    ),
    MVName(
        "3PC",
        "3P cooling",
        "Three-parameter cooling change-point model (3PC)",
        (_G14, _IMT),
        "camber.mandv.models.fit_model",
        _CP_SEARCH + _BIC,
        "change-point model",
    ),
    MVName(
        "3PH",
        "3P heating",
        "Three-parameter heating change-point model (3PH)",
        (_G14, _IMT),
        "camber.mandv.models.fit_model",
        _CP_SEARCH + _BIC,
        "change-point model",
    ),
    MVName(
        "3PCZ",
        "3P cooling, zero base (CAMBER)",
        "",
        (),
        "camber.mandv.models.fit_model",
        _ZERO,
        "change-point model",
    ),
    MVName(
        "3PHZ",
        "3P heating, zero base (CAMBER)",
        "",
        (),
        "camber.mandv.models.fit_model",
        _ZERO,
        "change-point model",
    ),
    MVName(
        "4P",
        "4P",
        "Four-parameter change-point model (4P)",
        (_G14, _IMT),
        "camber.mandv.models.fit_model",
        _CP_SEARCH + _BIC,
        "change-point model",
    ),
    MVName(
        "5P",
        "5P",
        "Five-parameter change-point model (5P)",
        (_G14, _IMT),
        "camber.mandv.models.fit_model",
        "Both change points grid-searched on the same 40-point grid, the dead-band at least one "
        "grid step wide. When no dead-band fits, the fit falls back to a 2P line and keeps "
        'kind "5P" with no change points; it is then labelled "2P (5P fallback)".' + _BIC,
        "change-point model",
    ),
    MVName(
        "5PZ",
        "5P heating and cooling, zero base (CAMBER)",
        "",
        (),
        "camber.mandv.models.fit_model",
        _ZERO + ' Falls back to a 2P line (with an intercept) like 5P: "2P (5P zero-base '
        'fallback)".',
        "change-point model",
    ),
    MVName(
        "best_model",
        "daily change-point (2P–5P, BIC)",
        "Change-point regression model (ASHRAE Guideline 14 / RP-1050 IMT), selected by BIC",
        (_G14, _IMT),
        "camber.mandv.models.best_model",
        "Fits 2P, 3PC, 3PH, 4P and 5P and keeps the lowest BIC, n ln(SSE/n) + p ln(n), with every "
        "change point counted in p; the model it returns is one of those kinds.",
        "change-point model",
    ),
    # --- degree-day models ----------------------------------------------------------------------
    MVName(
        "DD-H",
        "Degree-day heating",
        "Variable-base degree-day (VBDD) heating model, PRISM heating-only form",
        (_PRISM, _IMT),
        "camber.mandv.basetemp.fit_bill_degree_day",
        "Daily degree-days summed over each bill, the base chosen from a grid by days-weighted "
        "least squares with a profile-likelihood range (`select_bases`); with a numeric `base_f` "
        "the base is fixed (a fixed-base degree-day model, base not counted in p).",
        "degree-day model",
    ),
    MVName(
        "DD-C",
        "Degree-day cooling",
        "Variable-base degree-day (VBDD) cooling model",
        (_PRISM, _IMT),
        "camber.mandv.basetemp.fit_bill_degree_day",
        "The cooling analogue of DD-H (PRISM's original model is heating-only); same base search.",
        "degree-day model",
    ),
    MVName(
        "DD-HC",
        "Degree-day heating and cooling",
        "Variable-base degree-day (VBDD) heating-and-cooling model",
        (_PRISM, _IMT),
        "camber.mandv.basetemp.fit_bill_degree_day",
        "Separate heating and cooling bases (heating base no higher than the cooling base), each "
        "searched on its own grid; p counts both fitted bases.",
        "degree-day model",
    ),
    MVName(
        "degreeday",
        "Degree-day on period-mean temperature (CAMBER)",
        "",
        (),
        "camber.mandv.degreeday.fit_mean_temp_degree_day",
        "Not a variable-base degree-day (PRISM) model: degree-days come from each period's mean "
        "temperature, about one balance point shared by both legs, so a shoulder month's swing "
        "days are undercounted. The searched balance point is counted in p (since 0.105). "
        "Renamed in 0.105 from `fit_degree_day` / `DegreeDayModel` (deprecated aliases).",
        "degree-day model",
    ),
    # --- other baseline models ------------------------------------------------------------------
    MVName(
        "towt",
        "TOWT (CAMBER variant)",
        "Time-of-Week-and-Temperature (TOWT) model (Mathieu et al. 2011; LBNL RMV2.0)",
        (_TOWT, _RMV2) + _LBNL_TEST,
        "camber.mandv.towt.fit_towt",
        "One pooled model over the baseline; hour-of-week bins (168); by default "
        "`n_temp_segments` quantile-spaced temperature knots held flat beyond the fitted range "
        "rather than the reference's fixed knots; occupancy from each bin's mean load against "
        "the median of bin means; no time-weighting of observations. Opt-in (0.105): "
        "`towt_lbnl_config()` matches RMV2.0's knots (40, 55, 65, 80, 90 degF, open outer "
        "segments, outer knots pruned under 20 observations), its zero clip and its recency "
        "weight function (`fit_towt(weights=lbnl_time_weights(...))`); it still fits one "
        "weighted model with load-median occupancy, in hour-of-week bins where RMV2.0 defaults "
        "to 15-minute intervals (the same on hourly data), and refuses (or, opt-in, fills) an "
        "unseen time of week where RMV2.0 predicts NaN. RMV2.0's 65% positive-residual "
        "occupancy rule and its ensemble of local fits are in `towt_rmv2` "
        "(`fit_towt_rmv2`).",
        "other baseline model",
    ),
    MVName(
        "towt_rmv2",
        "TOWT, LBNL RMV2.0 variant",
        "Time-of-Week-and-Temperature (TOWT) model, LBNL RMV2.0 implementation (towt_baseline)",
        (_RMV2, _TOWT),
        "camber.mandv.towt_rmv2.fit_towt_rmv2",
        "Opt-in (0.105). Reproduces RMV2.0 1.1.0's makeBaseline (R/towt_baseline.R, commit "
        "979a963): its occupancy rule (a regression of load on temperature hinged at 50 and "
        "65 degF; an interval is occupied when over 65% of its residuals are positive, and the "
        "split needs more than two), its knots and pruning, and its ensemble of local fits "
        "centred every ~14 days by row count, each prediction clipped at zero and averaged with "
        "the weights s^2/(s^2+dt^2) about the prediction time. An unseen time of week is NaN. "
        "Differences: hour-of-week intervals by default (RMV2.0: 15 minutes, the same on hourly "
        "data); RMV2.0's data cleaning (clean_eload, clean_Temp) is not applied, CAMBER's "
        "temperature screen is; the final approx() fill of NaN is opt-in "
        '(`unseen="previous"`). Differs from Mathieu et al. (2011): data-driven occupancy and '
        "time weighting.",
        "other baseline model",
    ),
    MVName(
        "multivariable",
        "Change-point + drivers (multivariable)",
        "Multivariable change-point regression model (SEP 50001 M&V Protocol §6.3.2)",
        (_SEP, _G14),
        "camber.mandv.multivariable.fit_cp_driver_model",
        "Change points grid-searched with the drivers in the design; drivers enter linearly.",
        "other baseline model",
    ),
    MVName(
        "categorical",
        "Categorical change-point (one model per category)",
        "",
        (_G14,),
        "camber.mandv.categorical.fit_categorical",
        "A CAMBER construct: a separate change-point model per category combination (day type, "
        "occupancy mode), routed by category, with pooled fit statistics; not indicator terms in "
        "one regression.",
        "other baseline model",
    ),
    # --- savings methods (SEP 50001 M&V Protocol §6.2; the config's mv.method) ------------------
    MVName(
        "forecast",
        "Forecast (avoided energy)",
        "Forecast method (SEP 50001 M&V Protocol §6.2.1); IPMVP avoided energy use",
        (_SEP, _IPMVP),
        "camber.mandv.methods.forecast_savings",
        "",
        "savings method",
    ),
    MVName(
        "backcast",
        "Backcast",
        "Backcast method (SEP 50001 M&V Protocol §6.2.2)",
        (_SEP,),
        "camber.mandv.methods.backcast_savings",
        "",
        "savings method",
    ),
    MVName(
        "standard_conditions",
        "Standard conditions (normalized savings)",
        "Standard conditions method (SEP 50001 M&V Protocol §6.2.3); IPMVP normalized savings",
        (_SEP, _IPMVP),
        "camber.mandv.methods.standard_conditions_savings",
        "",
        "savings method",
    ),
    MVName(
        "chaining",
        "Chaining",
        "Chaining method (SEP 50001 M&V Protocol §6.2.4)",
        (_SEP,),
        "camber.mandv.methods.chained_savings",
        "",
        "savings method",
    ),
    MVName(
        "sequential_chain",
        "Sequential chain (CAMBER extension)",
        "",
        (),
        "camber.mandv.methods.sequential_chain",
        "A multi-link chain; the SEP Protocol defines exactly one intermediate period.",
        "savings method",
    ),
    MVName(
        "auto",
        "Method proposal (SEP order)",
        "",
        (_SEP,),
        "camber.mandv.methods.select_method",
        "Proposes a method in the SEP order and ranks models by SEP validity then adjusted R² "
        "(the DOE EnPI tool's order). It proposes a method; it computes no saving itself.",
        "savings method",
    ),
    # --- IPMVP options --------------------------------------------------------------------------
    MVName(
        "option_a",
        "IPMVP Option A (key-parameter measurement)",
        "IPMVP Option A: retrofit isolation, key parameter measurement",
        (_IPMVP,),
        "camber.mandv.option_a.option_a_savings",
        "The uncertainty of the stipulated portion is not quantified.",
        "IPMVP option",
    ),
    MVName(
        "retrofit_isolation",
        "IPMVP Option B (retrofit isolation)",
        "IPMVP Option B: retrofit isolation, all parameter measurement",
        (_IPMVP, _G14),
        "camber.mandv.retrofit_isolation.isolation_savings",
        "",
        "IPMVP option",
    ),
    MVName(
        "normalized",
        "Normalized savings",
        "IPMVP normalized savings (standard conditions)",
        (_IPMVP, _SEP),
        "camber.mandv.normalized.normalized_savings",
        "",
        "IPMVP option",
    ),
    MVName(
        "option_d",
        "Option D-style modeled savings (calibrated 1R1C grey box; pre-implementation estimate)",
        "IPMVP Option D: calibrated simulation",
        (_IPMVP, _G14),
        "camber.mandv.rc_model.option_d_savings",
        "A calibrated grey-box RC model (1R1C, or 2R2C / multi-zone), not a whole-building "
        "simulation program, used before implementation to estimate a control change. The "
        "calibration gate is `fit_stats`: CV(RMSE) by interval (30 % hourly), |NMBE| <= 0.5 % and "
        "R² >= 0.75, all required.",
        "IPMVP option",
    ),
    # --- whole-building savings functions -------------------------------------------------------
    MVName(
        "caltrack_daily_savings",
        "CalTRACK 2.0 Daily",
        "CalTRACK 2.0 Daily method",
        (_CALTRACK,),
        "camber.mandv.caltrack_daily.caltrack_daily_savings",
        "Model `CalTRACKDailyModel` (`fit_caltrack_daily`). Defaults follow the CalTRACK 2.0 "
        "documents (3 °F balance-point grid, at most 37 missing days, day usage kept at >= 50 % "
        "coverage, <= 6 h temperature gaps interpolated); `eemeter_compat=True` reproduces "
        "eemeter 2.10 where the two differ (1 °F grid and the rest). The degree days of a period "
        "are the mean over its days with a valid temperature (eemeter's reading). The savings "
        "band is CAMBER's G14 band; CalTRACK's own FSU is reported beside it.",
        "savings function",
    ),
    MVName(
        "caltrack_billing_savings",
        "CalTRACK 2.0 Billing",
        "CalTRACK 2.0 Billing method",
        (_CALTRACK,),
        "camber.mandv.caltrack_daily.caltrack_billing_savings",
        "Model `CalTRACKBillingModel` (`fit_caltrack_billing`): the daily model fitted to bills' "
        "usage per day by WLS weighted by the bill's days with a valid temperature (eemeter's "
        "weight; the documents' n_p is equal when temperature is complete). Estimated reads are "
        "combined with the next bill up to 70 days (eemeter does not cap). `eemeter_compat=True` "
        "reproduces eemeter 2.10.",
        "savings function",
    ),
    MVName(
        "caltrack_hourly_savings",
        "CalTRACK 2.0 Hourly",
        "CalTRACK 2.0 Hourly method (CalTRACK TOWT)",
        (_CALTRACK, _TOWT),
        "camber.mandv.caltrack_hourly.caltrack_hourly_savings",
        "Model `CalTRACKHourlyModel` (`fit_caltrack_hourly`). An hour of the week that a month's "
        "segment never saw predicts NaN in both modes (eemeter 3.1's fix; eemeter 2.10 predicts "
        "the temperature terms alone there). The > 90 % monthly coverage rule is applied (a stub "
        "in eemeter 2.10, skipped under `eemeter_compat=True`). CalTRACK publishes no hourly FSU, "
        "so only the G14 band is reported.",
        "savings function",
    ),
    MVName(
        "daily_changepoint_savings",
        "Daily change-point savings (not CalTRACK)",
        "",
        (_G14, _IPMVP),
        "camber.mandv.caltrack.daily_changepoint_savings",
        "Not CalTRACK 2.0 Daily: a `best_model` daily change-point baseline (2P–5P, BIC) with "
        "IPMVP avoided energy and a G14 band, without CalTRACK's candidate models, balance-point "
        "grid, qualifying tests, adjusted-R² selection or data-sufficiency rules. Called "
        "`caltrack_savings` before 0.105 (#156).",
        "savings function",
    ),
    MVName(
        "towt_savings_hourly",
        "Pooled TOWT hourly savings (not CalTRACK Hourly)",
        "",
        (_TOWT,),
        "camber.mandv.caltrack.towt_savings_hourly",
        "Not CalTRACK 2.0 Hourly: one pooled TOWT (CAMBER variant) model, not per-month "
        "segmented models with fixed temperature bins and a residual-based occupancy lookup. "
        "Called `caltrack_savings_hourly` before 0.105 (#156).",
        "savings function",
    ),
    MVName(
        "caltrack_savings",
        "Daily change-point savings (not CalTRACK; deprecated name)",
        "",
        (_G14, _IPMVP),
        "camber.mandv.caltrack.caltrack_savings",
        "Deprecated alias (0.105, removed in 1.0) of `daily_changepoint_savings`, same numbers; "
        "not CalTRACK 2.0 Daily, which is `caltrack_daily_savings` (#156).",
        "savings function",
    ),
    MVName(
        "caltrack_savings_hourly",
        "Pooled TOWT hourly savings (not CalTRACK Hourly; deprecated name)",
        "",
        (_TOWT,),
        "camber.mandv.caltrack.caltrack_savings_hourly",
        "Deprecated alias (0.105, removed in 1.0) of `towt_savings_hourly`, same numbers; not "
        "CalTRACK 2.0 Hourly, which is `caltrack_hourly_savings` (#156).",
        "savings function",
    ),
    # --- tracking and forecasting ---------------------------------------------------------------
    MVName(
        "cusum",
        "CUSUM of savings",
        "Cumulative sum (CUSUM) of savings",
        (),
        "camber.mandv.cusum.cusum_savings",
        "Common M&V tracking practice; no single standard defines it, so none is cited.",
        "tracking and forecasting",
    ),
    MVName(
        "seasonal_forecast",
        "Mean-Week + drift (LBNL M4 analogue), not an M&V method",
        "",
        _LBNL_TEST,
        "camber.forecast.seasonal_forecast",
        "A load forecast for anomaly detection, not an M&V baseline: the mean of history at each "
        "time-of-week slot (the LBNL test reports' Mean-Week model) plus an additive drift "
        "correction from the recent residuals. No weather term.",
        "tracking and forecasting",
    ),
)

#: every name, by code (table order)
MV_NAMES: dict = {r.code: r for r in _ROWS}

#: short citation texts by :mod:`camber.references` id (the docs table's Citation column, and
#: :func:`component_description`)
SHORT_CITATIONS: dict = {
    _G14: "ASHRAE G14-2014",
    _IMT: "Kissock et al. 2003 (RP-1050 IMT)",
    _PRISM: "Fels 1986 (PRISM)",
    _TOWT: "Mathieu et al. 2011",
    _RMV2: "LBNL RMV2.0",
    "granderson-2015-baselines": "Granderson et al. 2015",
    "lbnl-187225": "LBNL-187225",
    "lbnl-6963e": "LBNL-6963E",
    _CALTRACK: "CalTRACK 2.0",
    _IPMVP: "IPMVP Core Concepts 2022",
    _SEP: "SEP 50001 M&V Protocol 2019",
}

# labels for a 5P / 5PZ fit that fell back to a straight line (kind kept, no change points)
_FALLBACK = {"5P": "2P (5P fallback)", "5PZ": "2P (5P zero-base fallback)"}
# serialised model types that have no ``kind`` of their own, or whose kind is not the registry's
_TYPE_CODES = {
    "TOWTModel": "towt",
    "DegreeDayModel": "degreeday",
    "MeanTempDegreeDayModel": "degreeday",
    "CategoricalModel": "categorical",
    "CalTRACKDailyModel": "caltrack_daily_savings",
    "CalTRACKBillingModel": "caltrack_billing_savings",
    "CalTRACKHourlyModel": "caltrack_hourly_savings",
}


def mv_name(code: str) -> MVName:
    """The registry row for ``code`` (``KeyError`` when unknown)."""
    return MV_NAMES[code]


def label(code, change_points=None) -> str:
    """The user-facing label for a CAMBER code; an unknown code is returned as given.

    ``change_points`` (optional) marks a 5P / 5PZ fit that fell back to a line: pass the model's
    change points and an empty tuple gives ``"2P (5P fallback)"``.
    """
    if code is None:
        return ""
    code = str(code)
    if change_points is not None and code in _FALLBACK and len(tuple(change_points)) == 0:
        return _FALLBACK[code]
    row = MV_NAMES.get(code)
    return row.label if row is not None else code


def model_label(model) -> str:
    """The label for a fitted model, its ``as_dict`` payload, or a bare kind string.

    Reads the model's ``kind`` (and change points, for the 5P fallback); a model type without a
    registry kind (TOWT, the period-mean degree-day model) is labelled by its type. A
    change-point + drivers model is labelled by its change-point kind.
    """
    if model is None:
        return ""
    if isinstance(model, str):
        return label(model)
    if isinstance(model, dict):
        typ = str(model.get("type") or "")
        kind, cps = model.get("kind"), model.get("change_points")
    else:
        typ = type(model).__name__
        kind, cps = getattr(model, "kind", None), getattr(model, "change_points", None)
    if typ in _TYPE_CODES:
        return label(_TYPE_CODES[typ])
    if kind is None:
        return typ
    return label(kind, cps)


def citations(code: str) -> list:
    """The :class:`camber.references.Reference` objects a code's row cites, primary first."""
    from ..references import REFERENCES

    return [REFERENCES[i] for i in mv_name(code).citation_id]


# --------------------------------------------------------------------------- ensembles (#174)
def _code_and_note(component) -> tuple:
    """``(code, note)`` for a code, an :class:`MVName`, a ``(code, note)`` pair, a fitted model
    or its ``as_dict`` payload. A 5P / 5PZ that fell back to a line is described as the 2P it is."""
    if isinstance(component, MVName):
        return component.code, None
    if isinstance(component, tuple) and len(component) == 2 and isinstance(component[0], str):
        return component[0], component[1]
    if isinstance(component, str):
        return component, None
    if isinstance(component, dict):
        typ = str(component.get("type") or "")
        kind, cps = component.get("kind"), component.get("change_points")
    else:
        typ = type(component).__name__
        kind = getattr(component, "kind", None)
        cps = getattr(component, "change_points", None)
    if typ in _TYPE_CODES:
        return _TYPE_CODES[typ], None
    if kind is None:
        raise ValueError(f"cannot name an ensemble component of type {typ!r}")
    if kind in _FALLBACK and cps is not None and len(tuple(cps)) == 0:
        return "2P", _FALLBACK[kind].split("(", 1)[1].rstrip(")")
    return str(kind), None


def _lower_first(name: str) -> str:
    """``name`` with its first letter lowercased for use mid-sentence, unless the first word is
    an acronym or a proper name (any capital after its first letter: "TOWT", "CalTRACK")."""
    first = name.split(" ", 1)[0]
    if not first or any(c.isupper() for c in first[1:]):
        return name
    return name[0].lower() + name[1:]


def component_description(component, note: str | None = None, *, cite: str = "primary") -> str:
    """One model as an ensemble names it: its standard name (its CAMBER label when it has none),
    with an indefinite article, a note, and its citation in square brackets (0.105, #174).

    ``component`` is a registry code, an :class:`MVName`, a fitted model or its ``as_dict``
    payload. ``note`` is added after the name; a row that has a standard name and a CAMBER label
    (TOWT) gets ``"CAMBER variant"``. ``cite`` is ``"primary"`` (the row's first citation),
    ``"all"`` or ``"none"``. E.g. ``component_description("3PC")`` is ``"a three-parameter
    cooling change-point model (3PC) [ASHRAE G14-2014]"``.
    """
    if cite not in ("primary", "all", "none"):
        raise ValueError(f"cite must be 'primary', 'all' or 'none', got {cite!r}")
    code, auto_note = _code_and_note(component)
    row = mv_name(code)
    name = _lower_first(row.standard_name or row.label)
    notes = [n for n in (note, auto_note) if n]
    if row.standard_name and "CAMBER" in row.label and not notes:
        notes.append("CAMBER variant")
    text = ("an " if name[:1].lower() in "aeiou" else "a ") + name
    if notes:
        text += ", " + ", ".join(notes)
    ids = () if cite == "none" else row.citation_id[:1] if cite == "primary" else row.citation_id
    if ids:
        text += " [" + "; ".join(SHORT_CITATIONS.get(i, i) for i in ids) + "]"
    return text


def ensemble_description(components, rule: str, *, cite: str = "primary") -> str:
    """The sentence that names a prediction combining several models (0.105, #174).

    ``"Ensemble approach combining <component>, <component> and <component>; <rule>."`` -- each
    component by :func:`component_description` (its standard name and citation, never a CAMBER
    nickname), then the combination ``rule`` exactly as applied: the weights, or how one model
    is selected. This is the rule of ``docs/MANDV.md``, "Naming and citations". ``components``
    are registry codes, :class:`MVName` rows, ``(code, note)`` pairs, fitted models or their
    ``as_dict`` payloads (at least two); ``rule`` must not be empty. For example::

        ensemble_description(["3PC", "towt"], "the prediction is the unweighted mean of the two")

    gives "Ensemble approach combining a three-parameter cooling change-point model (3PC) [ASHRAE
    G14-2014] and a Time-of-Week-and-Temperature (TOWT) model (Mathieu et al. 2011; LBNL RMV2.0),
    CAMBER variant [Mathieu et al. 2011]; the prediction is the unweighted mean of the two."
    """
    parts = [component_description(c, cite=cite) for c in components]
    if len(parts) < 2:
        raise ValueError("an ensemble combines at least two models")
    rule = (rule or "").strip().rstrip(".")
    if not rule:
        raise ValueError("give the combination rule: the weights, or how a model is selected")
    joined = ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Ensemble approach combining {joined}; {rule}."
