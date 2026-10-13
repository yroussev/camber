"""Degree-day M&V baseline on period-mean temperature (HDD/CDD regression).

Regress period energy on **heating and cooling degree-days** about one balance point --
``E = base + a·HDD + b·CDD`` -- where each period's degree-days are computed from that period's
**mean** temperature (``HDD = max(0, balance_point - tavg)``). It is a lighter cousin of the
change-point models in :mod:`camber.mandv.models`, for monthly-bill M&V when only a period-average
temperature and energy are at hand. The balance point is fit by minimizing CV(RMSE) unless
supplied, and both legs share it.

**Not the variable-base degree-day (VBDD / PRISM) model** (0.105, #163). PRISM sums *daily*
degree-days over each meter-reading period at a fitted reference temperature; degree-days from a
period's mean undercount the swing days of a shoulder month (a bill whose mean sits above the base
still has cold days below it), and heating and cooling share one balance point here. For the
variable-base form on bills -- daily degree-days summed over each bill, separate heating and
cooling bases -- use :func:`camber.mandv.basetemp.fit_bill_degree_day` /
:func:`camber.mandv.basetemp.select_bases`. Hence the 0.105 names :func:`fit_mean_temp_degree_day`
and :class:`MeanTempDegreeDayModel`; the old names ``fit_degree_day`` / ``DegreeDayModel`` remain as
deprecated aliases.

**Parameter count.** ``p`` counts the intercept and the slopes (``kind="both"``: 3; one leg: 2),
plus 1 for the balance point when the fit searches for it (a fixed ``balance_point`` is not
counted), as :mod:`camber.mandv.basetemp` counts its fitted bases (0.105, #163).

numpy only; fit statistics reuse :func:`camber.mandv.stats.fit_stats`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .._deprecation import deprecated
from .stats import fit_stats


def degree_days(tavg, balance_point: float):
    """Per-period heating and cooling degree-days about ``balance_point`` (°F): ``(hdd, cdd)``."""
    t = np.asarray(tavg, dtype=float)
    return np.clip(balance_point - t, 0.0, None), np.clip(t - balance_point, 0.0, None)


@dataclass
class MeanTempDegreeDayModel:
    """A fitted degree-day baseline on period-mean temperature: ``E = base + a·HDD + b·CDD``.

    One balance point for both legs, degree-days from each period's mean temperature (see the
    module docstring: not the variable-base / PRISM form). Serialised with ``"type":
    "DegreeDayModel"``, the pre-0.105 name, so stored baselines read back unchanged.
    """

    kind: str  # "heating" | "cooling" | "both"
    balance_point: float
    base: float  # weather-independent energy per period
    heating_slope: float  # energy per HDD (0 if kind excludes heating)
    cooling_slope: float  # energy per CDD (0 if kind excludes cooling)
    fit: object  # camber.mandv.stats.FitStats
    # the one private fit-time record (support, pinv(X'X), ...; camber.mandv.coverage)
    _fit_record: object = field(default=None, repr=False, compare=False)
    # 0.90.1 (#59): why the model should not be trusted as fitted (a slope of the wrong sign)
    caveats: list = field(default_factory=list)
    # 0.105 (#159): temperatures outside the plausible range set to missing before the fit
    n_temp_screened: int = 0
    # 0.105 (#168): the fit's temp_bounds (None: the default range), applied again at predict
    temp_bounds: tuple | None = None

    def predict(self, tavg, *, clip_negative: bool = False, return_counts: bool = False):
        """Predicted energy for period average temperature(s) ``tavg``.

        A temperature outside the bounds the model was fitted with predicts NaN (0.105, #168).
        ``clip_negative`` / ``return_counts`` (0.105, #155; opt-in) as
        :meth:`camber.mandv.models.ChangePointModel.predict`.
        """
        from ._temperature import screen_temperature

        t, n_screened = screen_temperature(np.asarray(tavg, dtype=float), self.temp_bounds)
        hdd, cdd = degree_days(t, self.balance_point)
        out = self.base + self.heating_slope * hdd + self.cooling_slope * cdd
        if not (clip_negative or return_counts):
            return out
        from .models import _clip_negative

        res = _clip_negative(out, clip_negative, return_counts)
        if return_counts:
            res[1]["n_temp_screened"] = n_screened
        return res

    def coverage(self, tavg, *, projected=None, policy=None):
        """How well the fitted temperature range covers ``tavg`` (a
        :class:`~camber.mandv.coverage.Coverage`); a temperature the predict-time screen sets to
        missing is a row without a driver (0.105, #168)."""
        from ._temperature import screen_temperature
        from .coverage import _linear_coverage

        t, _ = screen_temperature(np.asarray(tavg, dtype=float), self.temp_bounds)
        return _linear_coverage(self, t, projected=projected, policy=policy)

    def as_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "balance_point": self.balance_point,
            "base": self.base,
            "heating_slope": self.heating_slope,
            "cooling_slope": self.cooling_slope,
        }
        if self.caveats:
            d["caveats"] = list(self.caveats)
        if self.n_temp_screened:  # 0.105 (#159): only when the screen removed something
            d["n_temp_screened"] = int(self.n_temp_screened)
        if self.temp_bounds is not None:  # 0.105 (#168): only when not the default range
            from ._temperature import bounds_to_json

            d["temp_bounds"] = bounds_to_json(self.temp_bounds)
        d["fit"] = self.fit.as_dict() if hasattr(self.fit, "as_dict") else self.fit
        from .coverage import _FitRecord

        rec = self._fit_record
        d["type"] = "DegreeDayModel"
        d["fit_record"] = rec.as_dict() if isinstance(rec, _FitRecord) else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> MeanTempDegreeDayModel:
        """Rebuild a model written by :meth:`as_dict` (unknown ``fit`` keys are ignored)."""
        from ._temperature import bounds_from_json
        from .coverage import _FitRecord
        from .stats import FitStats

        fit = d.get("fit")
        if isinstance(fit, dict):
            known = set(FitStats.__dataclass_fields__)
            fit = FitStats(**{k: v for k, v in fit.items() if k in known})
        return cls(
            kind=d["kind"],
            balance_point=float(d["balance_point"]),
            base=float(d["base"]),
            heating_slope=float(d["heating_slope"]),
            cooling_slope=float(d["cooling_slope"]),
            fit=fit,
            _fit_record=_FitRecord.from_dict(d.get("fit_record")),
            caveats=list(d.get("caveats") or []),
            n_temp_screened=int(d.get("n_temp_screened", 0)),
            temp_bounds=bounds_from_json(d.get("temp_bounds")),
        )


def _wrong_signs(heating_slope: float, cooling_slope: float) -> list:
    """The slopes of the wrong (negative) sign, described; ``[]`` when both are logical."""
    out = []
    if heating_slope < 0:
        out.append(f"heating slope {heating_slope:.4g} per HDD is negative")
    if cooling_slope < 0:
        out.append(f"cooling slope {cooling_slope:.4g} per CDD is negative")
    return out


def _fit_at(tavg, energy, bp: float, kind: str, p_extra: int = 0):
    hdd, cdd = degree_days(tavg, bp)
    cols = [np.ones(len(tavg))]
    idx = {}
    if kind in ("heating", "both"):
        idx["h"] = len(cols)
        cols.append(hdd)
    if kind in ("cooling", "both"):
        idx["c"] = len(cols)
        cols.append(cdd)
    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, energy, rcond=None)
    fs = fit_stats(energy, X @ coef, p=X.shape[1] + p_extra)
    base = float(coef[0])
    hs = float(coef[idx["h"]]) if "h" in idx else 0.0
    cs = float(coef[idx["c"]]) if "c" in idx else 0.0
    return base, hs, cs, fs


def fit_mean_temp_degree_day(
    tavg,
    energy,
    *,
    balance_point: float | None = None,
    balance_range=(50.0, 70.0),
    step: float = 1.0,
    kind: str = "both",
    time_index=None,
    temp_bounds: tuple | None = None,
) -> MeanTempDegreeDayModel:
    """Fit ``E = base + a·HDD + b·CDD`` on period-mean temperature. Returns a
    :class:`MeanTempDegreeDayModel`.

    ``tavg``/``energy`` are per-period average temperature and energy (e.g. monthly); each
    period's degree-days come from its mean temperature, about one balance point shared by both
    legs (not the variable-base / PRISM form; see the module docstring). If ``balance_point`` is
    None, it's chosen from ``balance_range`` (stepped by ``step``) by minimum CV(RMSE) (and not
    counted in ``p``; see the module docstring). ``kind`` restricts to ``"heating"`` /
    ``"cooling"`` or fits ``"both"`` legs.
    ``time_index`` (aligned to ``tavg``) lets the fit record the residuals' lag-1 autocorrelation.

    **Signs** (0.90.1, issue #59). Energy cannot fall as degree-days rise, so the heating and
    cooling slopes must be >= 0 (:func:`~camber.mandv.stats.logical_signs` gives the SEP sign test
    the same rule). The balance-point search prefers a balance point whose fitted slopes are
    logical; when none is, the best fit is returned **declined** -- ``fit.accept`` is ``False``
    and ``caveats`` names the offending slope (typically a ``"both"`` fit on a building with no
    cooling load: refit with ``kind="heating"``).

    **Temperature screen** (0.105, #159). Temperatures outside ``temp_bounds`` (default the
    sensor-health outdoor-air range, -40 to 140 degF; see
    :func:`camber.mandv._temperature.screen_temperature`) are dropped like a missing month; the
    model's ``n_temp_screened`` counts them. (They are not ``caveats``: those decline the model.)
    """
    if kind not in ("heating", "cooling", "both"):
        raise ValueError("kind must be 'heating', 'cooling', or 'both'")
    from ._temperature import fitted_bounds, screen_temperature

    tavg, n_screened = screen_temperature(np.asarray(tavg, dtype=float), temp_bounds)
    energy = np.asarray(energy, dtype=float)
    if len(tavg) != len(energy):
        raise ValueError("tavg and energy must be the same length")
    finite = np.isfinite(tavg) & np.isfinite(energy)  # drop NaN/inf pairs (a missing bill month)
    idx = None if time_index is None else np.asarray(time_index)[finite]
    tavg, energy = tavg[finite], energy[finite]
    # intercept + one or two slopes, plus the balance point when the fit chooses it
    p_extra = 1 if balance_point is None else 0
    p = (3 if kind == "both" else 2) + p_extra
    if len(tavg) <= p:  # need > p points, else the fit is degenerate
        raise ValueError(
            f"need more than {p} finite (tavg, energy) points for kind={kind!r}, have {len(tavg)}"
        )

    candidates = (
        [float(balance_point)]
        if balance_point is not None
        else list(np.arange(balance_range[0], balance_range[1] + 1e-9, step))
    )
    best = None
    for bp in candidates:
        base, hs, cs, fs = _fit_at(tavg, energy, bp, kind, p_extra)
        cv = fs.cv_rmse if fs.cv_rmse == fs.cv_rmse else float("inf")
        key = (bool(_wrong_signs(hs, cs)), cv)  # a logical fit beats any illogical one
        if best is None or key < best[0]:
            best = (key, bp, base, hs, cs, fs)
    assert best is not None  # candidates is non-empty, so the loop always sets best
    _, bp, base, hs, cs, fs = best
    wrong = _wrong_signs(round(hs, 4), round(cs, 4))
    caveats = []
    if wrong:
        from dataclasses import replace

        caveats.append(
            "declined: "
            + "; ".join(wrong)
            + " -- energy cannot fall as degree-days rise, so the model is not physically logical"
            + (" (refit with kind='heating' or kind='cooling')" if kind == "both" else "")
        )
        fs = replace(fs, accept=False, notes=(fs.notes + "; " if fs.notes else "") + caveats[0])
    from .coverage import _linear_fit_record, _safe
    from .models import _rho_of

    model = MeanTempDegreeDayModel(
        kind=kind,
        balance_point=round(float(bp), 2),
        base=round(base, 3),
        heating_slope=round(hs, 4),
        cooling_slope=round(cs, 4),
        fit=fs,
        caveats=caveats,
        n_temp_screened=n_screened,
        temp_bounds=fitted_bounds(temp_bounds),
    )
    # the record's s2 is the reported (rounded) model's, so an exact band matches its predictions
    resid = energy - model.predict(tavg)
    n = len(energy)
    model._fit_record = _safe(
        _linear_fit_record,
        tavg,
        ("tavg",),
        ("dd", kind, round(float(bp), 2)),
        s2=float(resid @ resid) / (n - p),
        n=n,
        p=p,
        rho=_safe(_rho_of, resid, idx),
    )
    return model


# --- deprecated names (0.105, #163) ---------------------------------------------------------------


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.degreeday.MeanTempDegreeDayModel")
class DegreeDayModel(MeanTempDegreeDayModel):
    """Deprecated alias of :class:`MeanTempDegreeDayModel` (the old name said "variable-base",
    which the model is not). ``from_dict`` on it still reads stored models."""


@deprecated(since="0.105", remove_in="1.0", use="camber.mandv.degreeday.fit_mean_temp_degree_day")
def fit_degree_day(tavg, energy, **kwargs) -> MeanTempDegreeDayModel:
    """Deprecated alias of :func:`fit_mean_temp_degree_day`."""
    return fit_mean_temp_degree_day(tavg, energy, **kwargs)
