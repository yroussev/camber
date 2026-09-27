"""Variable-base degree-day M&V baseline (HDD/CDD regression).

The classic weather-normalization baseline: regress period energy on **heating and cooling
degree-days** about a balance point — ``E = base + a·HDD + b·CDD``. It's the simplest defensible
weather model (ASHRAE G14 / IPMVP), a lighter cousin of the change-point models in
:mod:`camber.mandv.models`, and a good fit for monthly-bill M&V where you have average temperature
and energy per period. The balance point is fit by minimizing CV(RMSE) unless supplied.

numpy only; fit statistics reuse :func:`camber.mandv.stats.fit_stats`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .stats import fit_stats


def degree_days(tavg, balance_point: float):
    """Per-period heating and cooling degree-days about ``balance_point`` (°F): ``(hdd, cdd)``."""
    t = np.asarray(tavg, dtype=float)
    return np.clip(balance_point - t, 0.0, None), np.clip(t - balance_point, 0.0, None)


@dataclass
class DegreeDayModel:
    """A fitted variable-base degree-day baseline: ``E = base + a·HDD + b·CDD``."""

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

    def predict(self, tavg):
        """Predicted energy for period average temperature(s) ``tavg``."""
        hdd, cdd = degree_days(tavg, self.balance_point)
        return self.base + self.heating_slope * hdd + self.cooling_slope * cdd

    def coverage(self, tavg, *, projected=None, policy=None):
        """How well the fitted temperature range covers ``tavg`` (a
        :class:`~camber.mandv.coverage.Coverage`)."""
        from .coverage import _linear_coverage

        return _linear_coverage(self, tavg, projected=projected, policy=policy)

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
        d["fit"] = self.fit.as_dict() if hasattr(self.fit, "as_dict") else self.fit
        from .coverage import _FitRecord

        rec = self._fit_record
        d["type"] = "DegreeDayModel"
        d["fit_record"] = rec.as_dict() if isinstance(rec, _FitRecord) else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DegreeDayModel:
        """Rebuild a model written by :meth:`as_dict` (unknown ``fit`` keys are ignored)."""
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
        )


def _wrong_signs(heating_slope: float, cooling_slope: float) -> list:
    """The slopes of the wrong (negative) sign, described; ``[]`` when both are logical."""
    out = []
    if heating_slope < 0:
        out.append(f"heating slope {heating_slope:.4g} per HDD is negative")
    if cooling_slope < 0:
        out.append(f"cooling slope {cooling_slope:.4g} per CDD is negative")
    return out


def _fit_at(tavg, energy, bp: float, kind: str):
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
    fs = fit_stats(energy, X @ coef, p=X.shape[1])
    base = float(coef[0])
    hs = float(coef[idx["h"]]) if "h" in idx else 0.0
    cs = float(coef[idx["c"]]) if "c" in idx else 0.0
    return base, hs, cs, fs


def fit_degree_day(
    tavg,
    energy,
    *,
    balance_point: float | None = None,
    balance_range=(50.0, 70.0),
    step: float = 1.0,
    kind: str = "both",
    time_index=None,
) -> DegreeDayModel:
    """Fit ``E = base + a·HDD + b·CDD``. Returns a :class:`DegreeDayModel`.

    ``tavg``/``energy`` are per-period average temperature and energy (e.g. monthly). If
    ``balance_point`` is None, it's chosen from ``balance_range`` (stepped by ``step``) by minimum
    CV(RMSE). ``kind`` restricts to ``"heating"``/``"cooling"`` or fits ``"both"`` legs.
    ``time_index`` (aligned to ``tavg``) lets the fit record the residuals' lag-1 autocorrelation.

    **Signs** (0.90.1, issue #59). Energy cannot fall as degree-days rise, so the heating and
    cooling slopes must be >= 0 (:func:`~camber.mandv.stats.logical_signs` gives the SEP sign test
    the same rule). The balance-point search prefers a balance point whose fitted slopes are
    logical; when none is, the best fit is returned **declined** -- ``fit.accept`` is ``False``
    and ``caveats`` names the offending slope (typically a ``"both"`` fit on a building with no
    cooling load: refit with ``kind="heating"``).
    """
    if kind not in ("heating", "cooling", "both"):
        raise ValueError("kind must be 'heating', 'cooling', or 'both'")
    tavg = np.asarray(tavg, dtype=float)
    energy = np.asarray(energy, dtype=float)
    if len(tavg) != len(energy):
        raise ValueError("tavg and energy must be the same length")
    finite = np.isfinite(tavg) & np.isfinite(energy)  # drop NaN/inf pairs (a missing bill month)
    idx = None if time_index is None else np.asarray(time_index)[finite]
    tavg, energy = tavg[finite], energy[finite]
    p = 3 if kind == "both" else 2  # intercept + one or two slopes
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
        base, hs, cs, fs = _fit_at(tavg, energy, bp, kind)
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

    model = DegreeDayModel(
        kind=kind,
        balance_point=round(float(bp), 2),
        base=round(base, 3),
        heating_slope=round(hs, 4),
        cooling_slope=round(cs, 4),
        fit=fs,
        caveats=caveats,
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
