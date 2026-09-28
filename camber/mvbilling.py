"""Config-driven M&V on utility bills: the ``mv`` entries that name a ``bills`` file (provisional).

A config ``mv`` entry normally fits a daily change-point baseline to a trended meter. An entry with
``"bills"`` instead reads a bills table -- one row per bill with its start, end, energy and,
optionally, its units and an estimated-read flag -- and runs the same M&V flow on
:class:`camber.mandv.billing.BillingSeries`:

* **weather per bill** -- each bill is paired with the day-weighted mean outdoor temperature and
  the heating / cooling degree-days of its own service period
  (:meth:`~camber.mandv.billing.BillingSeries.energy_vs_temp`), from the entry's ``oat`` file, an
  opt-in weather fetch, or the config's ``shared_oat``; bills whose period the temperature data
  covers less than ``min_coverage`` of are dropped and counted;
* **estimated reads** are merged into the next actual read
  (:meth:`~camber.mandv.billing.BillingSeries.merge_estimated`; ``"merge_estimated": false`` keeps
  them as they are, flagged);
* **fits weighted by days** -- the baseline is the best change-point model of energy per day
  against the bill's mean temperature, by least squares weighted by each bill's days, judged at the
  G14 **monthly** acceptance thresholds (:func:`~camber.mandv.stats.cv_rmse_max_for`);
* then the entry's ``method`` (forecast, backcast, chaining, standard conditions, or ``auto`` for
  the SEP proposal), ``kernel``, ``validity``, ``extrapolation`` coverage and ``adjustments``
  ledger exactly as for a trended meter, with every total summed as ``days * energy per day``.

Not supported for billing entries (0.92): the change-point + driver form (``model: cp_driver``),
versioned baselines (``camber mv freeze`` / ``rebaseline`` / ``adjust`` and a stored version: the
21d store keys meters by equipment and fits daily windows), and ``interval`` other than daily.
See docs/MANDV.md, "Billing data".
"""

from __future__ import annotations

import os

import pandas as pd

__all__ = ["billing_label", "load_bills", "billing_oat", "billing_units", "billing_findings"]

_KNOWN = {
    "bills",
    "name",
    "oat",
    "base_f",
    "min_coverage",
    "min_bills",
    "period",
    "reporting_period",
    "intermediate_period",
    "method",
    "kernel",
    "validity",
    "extrapolation",
    "adjustments",
    "normal_year",
    "ecm_dates",
    "settle_days",
    "materiality_threshold",
    "model",
    "drivers",
    "min_days",
    "interval",
}
_FULL_YEAR_DAYS = 330  # a baseline of bills covering fewer days of service is flagged short
_BILL_KEYS = {"file", "start", "end", "energy", "estimated", "units", "end_inclusive"}
_BILL_KEYS |= {"merge_estimated", "units_column"}
_BILL_KEYS |= {"heat_content", "enthalpy"}  # 0.92 (#69): gas volumes and steam mass


def _spec(entry: dict) -> dict:
    spec = entry.get("bills")
    if isinstance(spec, str):
        spec = {"file": spec}
    if not isinstance(spec, dict) or not spec.get("file"):
        raise ValueError('mv.bills must be a file path or {"file": ..., ...}')
    extra = set(spec) - _BILL_KEYS
    if extra:
        raise ValueError(f"mv.bills: unknown key(s) {sorted(extra)}")
    return spec


def billing_label(entry: dict) -> str:
    """The name a billing entry's Findings carry: ``name``, else the bills file's base name."""
    if entry.get("name"):
        return str(entry["name"])
    return os.path.splitext(os.path.basename(str(_spec(entry)["file"])))[0]


def load_bills(entry: dict, *, base_dir: str = "."):
    """The entry's bills as a :class:`~camber.mandv.billing.BillingSeries`, estimated reads merged
    into the next actual read unless ``bills.merge_estimated`` is false."""
    from .config import _path
    from .mandv.billing import BillingSeries

    spec = _spec(entry)
    kw = {k: spec[k] for k in ("start", "end", "energy", "units", "units_column") if k in spec}
    if "estimated" in spec:
        kw["estimated"] = spec["estimated"]
    if "end_inclusive" in spec:
        kw["end_inclusive"] = bool(spec["end_inclusive"])
    bs = BillingSeries.from_csv(_path(base_dir, spec["file"]), **kw)
    if spec.get("merge_estimated", True):
        bs = bs.merge_estimated()
    return bs


def billing_oat(entry: dict, prep, *, base_dir: str = ".", window=None):
    """Outdoor temperature for a billing entry: ``(series, source)``, or ``(None, why)``.

    In order: the entry's ``oat.file`` (a point CSV, ``oat.timezone`` optional); an opt-in fetch
    ``oat.fetch`` (``"auto"`` for ISD with the NASA POWER and Open-Meteo fallbacks, or one of
    ``"isd"``, ``"nasa_power"``, ``"open_meteo"``; with ``latitude``, ``longitude``, ``tz`` and
    optionally ``cache_dir`` / ``offline``) over ``window``; the config's ``shared_oat``.
    """
    from .config import _path
    from .model.roles import Role

    spec = entry.get("oat") or {}
    if spec and not isinstance(spec, dict):
        raise ValueError('mv.oat must be {"file": ...} or {"fetch": ..., ...}')
    if spec.get("file"):
        from .realio import load_point
        from .tsparse import check_timezone

        tz = check_timezone(spec.get("timezone") or None)
        s = load_point(_path(base_dir, spec["file"]), "oat", timezone=tz)
        return s, f"file {os.path.basename(str(spec['file']))}"
    if spec.get("fetch"):
        from .weather_source import oat_reference_auto

        if window is None:
            return None, "no bill dates to fetch weather for"
        cache = spec.get("cache_dir")
        s = oat_reference_auto(
            spec["latitude"],
            spec["longitude"],
            window[0],
            window[1],
            source=str(spec["fetch"]),
            tz=spec.get("tz", "UTC"),
            cache_dir=None if cache is None else _path(base_dir, cache),
            offline=bool(spec.get("offline", False)),
        )
        return s, f"fetched ({spec['fetch']})"
    shared = getattr(prep, "shared", None) or {}
    if Role.OAT in shared:
        return shared[Role.OAT], "shared_oat"
    return None, "no outdoor temperature (mv.oat or shared_oat)"


def _declined(label: str, why: str, reporting) -> list:
    from .config import _mv_declined

    out = [_mv_declined(label, why)]
    if reporting is not None:
        out.append(_mv_declined(label, f"no baseline: {why}", rule="mv_savings"))
    return out


def _slicer(frame: pd.DataFrame):
    """``win -> bills wholly inside [win[0], win[1]]`` (a bill straddling an edge is left out)."""

    def cut(win):
        lo = pd.Timestamp(win[0]).normalize()
        hi = pd.Timestamp(win[1]).normalize() + pd.Timedelta(days=1)
        sub = frame[(frame["start"] >= lo) & (frame["end"] <= hi)]
        return sub if len(sub) else None

    return cut


def billing_units(spec: dict, bills, units) -> tuple | None:
    """0.92 (#69): ``(factor, reported unit, bills unit, system)`` converting a billing entry's
    energy to the config's ``units`` system, or ``None`` without one.

    ``bills.heat_content`` (gas volumes: ``"10.37 therm/Mcf"``) and ``bills.enthalpy`` (steam:
    ``"1000 Btu/lb"``) are validated whenever given. With a unit system the bills must name a
    parseable unit, and a volume or mass needs its heat content or enthalpy; there is no default.
    """
    from .energy_units import parse_heat_content, parse_unit

    hc, en = spec.get("heat_content"), spec.get("enthalpy")
    for key, v in (("heat_content", hc), ("enthalpy", en)):
        if v is not None:
            try:
                parse_heat_content(v)
            except ValueError as e:
                raise ValueError(f"mv.bills.{key}: {e}") from None
    if units is None:
        return None
    if not bills.units:
        raise ValueError(
            f"units.system is {units.system!r}, but the bills in {spec['file']!r} name no unit: "
            'give bills.units (e.g. "kWh", "therm", "Mcf") or a units column'
        )
    try:
        k = units.energy_factor(bills.units, heat_content=hc, enthalpy=en)
        name = parse_unit(bills.units).name
    except ValueError as e:
        raise ValueError(f"mv.bills units: {e}") from None
    return k, units.energy, name, units.system


def billing_findings(entry: dict, prep, *, base_dir: str = ".") -> list:
    """``mv_baseline`` (and, with a ``reporting_period``, ``mv_savings`` or
    ``mv_method_proposal``) Findings for one billing entry. A bad entry is a ``ValueError``; a
    meter the data cannot serve is a declined Finding. With a config ``units`` block the reported
    energy is converted (0.92, #69); the fit stays in the bills' own unit."""
    from .config import _mv_apply_units

    box: dict = {}
    out = _billing_findings(entry, prep, base_dir=base_dir, box=box)
    return _mv_apply_units(out, box.get("conv"))


def _billing_findings(entry: dict, prep, *, base_dir: str, box: dict) -> list:
    import numpy as np

    from .config import (
        _add_caveats,
        _mv_adjustment_specs,
        _mv_method_spec,
        _mv_other_method_finding,
        _mv_proposal_finding,
        _mv_savings_finding,
        _mv_schedule,
        _mv_validity,
        _mv_window,
    )
    from .mandv import _mvform
    from .mandv.coverage import ExtrapolationPolicy, support_of
    from .mandv.stats import cv_rmse_max_for, fit_stats
    from .rules.base import Finding

    extra = set(entry) - _KNOWN
    if extra:
        raise ValueError(f"mv billing entry: unknown key(s) {sorted(extra)}")
    label = billing_label(entry)
    if entry.get("interval", "daily") != "daily":
        raise ValueError("mv.interval: a billing entry is fitted per bill; leave interval unset")
    if (entry.get("model") or "change_point") != "change_point" or entry.get("drivers"):
        raise ValueError(
            'mv billing entries support model "change_point" only (bills carry no daily drivers)'
        )
    period = _mv_window(entry, "period")
    reporting = _mv_window(entry, "reporting_period")
    policy = ExtrapolationPolicy.from_dict(entry.get("extrapolation"))
    method, kernel, declared = _mv_method_spec(entry, period, reporting)
    validity = _mv_validity(entry)
    schedule = _mv_schedule(entry)
    adj_specs = _mv_adjustment_specs(entry)
    min_bills = int(entry.get("min_bills", 9))
    min_cov = float(entry.get("min_coverage", 0.9))
    base_f = float(entry.get("base_f", 65.0))

    bills = load_bills(entry, base_dir=base_dir)
    box["conv"] = billing_units(_spec(entry), bills, getattr(prep, "units", None))  # 0.92 (#69)
    if not len(bills):
        return _declined(label, "the bills file has no bills", reporting)
    f = bills.frame
    window = (f["start"].min(), f["end"].max() - pd.Timedelta(days=1))
    oat, oat_source = billing_oat(entry, prep, base_dir=base_dir, window=window)
    if oat is None:
        return _declined(label, oat_source, reporting)
    oat = pd.Series(oat).dropna()
    if oat.index.tz is not None:  # bills are local calendar days
        oat.index = oat.index.tz_localize(None)
    frame = bills.energy_vs_temp(oat, base_f=base_f, min_coverage=min_cov)
    n_dropped = len(bills) - len(frame)
    notes = []
    merged = [m for m in bills.merged if m["action"] == "merged"]
    dropped_est = [m for m in bills.merged if m["action"] == "dropped"]
    if merged:
        notes.append(
            f"{sum(m['n_estimated'] for m in merged)} estimated read(s) merged into the next "
            f"actual read ({len(merged)} merged bill(s))"
        )
    if dropped_est:
        notes.append(f"{len(dropped_est)} estimated read(s) with no following actual read dropped")
    if n_dropped:
        notes.append(
            f"{n_dropped} bill(s) dropped: under {min_cov:.0%} of their days have outdoor "
            "temperature, or no usable energy"
        )
    n_est = int(frame["estimated"].sum())
    if n_est:
        notes.append(f"{n_est} bill(s) are estimated reads, used as billed (merge_estimated off)")
    cut = _slicer(frame)
    base = cut(period) if period else (frame if len(frame) else None)
    if base is None or len(base) < min_bills:
        n = 0 if base is None else len(base)
        why = f"only {n} usable bill(s) in the baseline (< {min_bills})"
        out = _declined(label, why, reporting)
        _add_caveats(out, notes)
        return out
    model = _mvform.fit(base)
    days_b = _mvform.row_days(base)
    short = int(days_b.sum()) < _FULL_YEAR_DAYS
    if short:
        notes.append(
            f"short baseline: its bills cover {int(days_b.sum())} days of service, under a year "
            "(G14 expects the baseline to span the full range of operating conditions)"
        )
    st = fit_stats(
        base["energy"].values,
        model.predict(_mvform.design_rows(base, model)),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("monthly"),
        time_index=base.index,
        weights=days_b,
    )
    sup = support_of(base["oat"].values, quantile=policy.support_quantile)
    store = getattr(prep, "mv_store", None)
    if store is not None:
        try:
            has_versions = any(r.equip == label for r in store.records())
        except Exception:  # noqa: BLE001 -- an unreadable store is the run path's concern
            has_versions = False
        if has_versions:
            notes.append(
                "a stored baseline version exists for this name, but billing entries are fitted "
                "afresh: versioned baselines are not supported for bills (0.92)"
            )
    verdict = "meets" if st.accept else "does not meet"
    metrics = {
        "model": model.kind,
        "billing": True,
        "weighted_by_days": True,
        "units": bills.units,
        "n_bills": int(st.n),
        "n_days": int(days_b.sum()),
        "r2": st.r2,
        "cv_rmse": st.cv_rmse,
        "nmbe": st.nmbe,
        "accept": bool(st.accept),
        "g14_interval": "monthly",
        "change_points": [round(float(t), 2) for t in model.change_points],
        "rho": st.rho_lag1,
        "oat_fit_min": sup["fit_min"],
        "oat_fit_max": sup["fit_max"],
        "oat_support_lo": sup["support_lo"],
        "oat_support_hi": sup["support_hi"],
        "base_f": base_f,
        "hdd_total": round(float(base["hdd"].sum()), 1),
        "cdd_total": round(float(base["cdd"].sum()), 1),
        "oat_source": oat_source,
        "bills_dropped": int(n_dropped),
        "estimated_merged": int(sum(m["n_estimated"] for m in merged)),
        "estimated_dropped": len(dropped_est),
        "mean_bill_days": round(float(np.mean(days_b)), 2),
        "short_baseline": bool(short),
    }
    out = [
        Finding(
            rule="mv_baseline",
            equip=label,
            severity="ok" if st.accept else "info",
            metrics=metrics,
            summary=(
                f"{label}: {model.kind} billing baseline (days-weighted), R2 {st.r2:.2f}, "
                f"CV(RMSE) {st.cv_rmse:.1%} over {st.n} bills ({int(days_b.sum())} days) -- "
                f"{verdict} monthly G14 acceptance"
            ),
            caveats=list(notes),
        )
    ]
    if reporting is None:
        return out
    ctx = {
        "equip": label,
        "entry": entry,
        "policy": policy,
        "period": period or [str(base["start"].min().date()), str(window[1].date())],
        "reporting": reporting,
        "full": None,
        "role": None,
        "slice": cut,
        "interval": "monthly",
        "min_rows": min_bills,
        "daily": base,
        "model": model,
        "st": st,
        "validity": validity,
        "schedule": schedule,
        "adj_specs": adj_specs,
    }
    if method == "auto":
        out.append(_mv_proposal_finding(ctx))
        _add_caveats(out[1:], notes)
        return out
    rep = cut(reporting)
    if rep is None or rep.empty:
        from .config import _mv_declined

        out.append(_mv_declined(label, "no usable reporting-period bills", rule="mv_savings"))
        return out
    if method == "forecast":
        out.append(
            _mv_savings_finding(
                label, model, st, rep, policy, reporting, kernel=kernel, declared=declared, ctx=ctx
            )
        )
    else:
        ctx["daily_r"] = rep
        out.append(_mv_other_method_finding(ctx, method, kernel))
    for fnd in out[1:]:
        fnd.metrics["billing"] = True
    _add_caveats(out[1:], notes)
    return out
