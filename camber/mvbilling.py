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

__all__ = [
    "billing_label",
    "load_bills",
    "billing_oat",
    "billing_units",
    "billing_conversion",
    "billing_findings",
    "billing_scale_check",
]

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
_BILL_KEYS |= {"meter_type"}  # 0.92: the fuel a units.factor_set looks up (camber.energy_factors)
_BILL_KEYS |= {"scale_check", "scale_override"}  # 0.92 (#71): unit-scale plausibility
_SCALE_KEYS = {
    "enabled",
    "fuel",
    "area",
    "area_unit",
    "property_type",
    "cost",
    "demand",
    "read_start",
    "read_end",
    "multiplier",
    "tariff",
    "price_source",
    "state",
    "eia_cache_dir",
    "eia_offline",
    "on_implausible",
    "other_site_kbtu_per_year",
}
# bills-file columns the check reads when present (the scale_check block may rename them)
_SCALE_COLUMNS = ("cost", "demand", "read_start", "read_end", "multiplier")


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
    parseable unit, and a volume or mass needs its heat content or enthalpy -- or, opt-in, a
    ``units.factor_set`` (:mod:`camber.energy_factors`) supplies it; an explicit heat content or
    enthalpy always wins. :func:`billing_conversion` also returns the factor's provenance.
    """
    return billing_conversion(spec, bills, units)[0]


def billing_conversion(spec: dict, bills, units) -> tuple:
    """``(conv, extra)``: :func:`billing_units`' tuple (or ``None``) and ``extra`` =
    ``{"metrics": {...}, "caveats": [...]}`` recording a factor set's use (provisional, 0.92).

    With ``units.factor_set`` a unit :mod:`camber.energy_units` cannot convert on its own (a
    volume or mass without ``heat_content`` / ``enthalpy``, or gallons, litres, tons, tonnes,
    ``kcf``, ``MMcf``, ``MMlb``) is looked up in the set for ``bills.meter_type`` (default:
    ``natural_gas`` for a gas volume, ``district_steam`` for a mass, as camber.energy_units
    reads them) and converted from the set's kBtu. Energy units (kWh, therm, MMBtu ...) keep the
    exact factors. A bare ``Mcf`` is a thousand cubic feet on either path, with a caveat.
    """
    from .energy_units import _norm, parse_heat_content, parse_unit

    extra: dict = {"metrics": {}, "caveats": []}
    hc, en = spec.get("heat_content"), spec.get("enthalpy")
    for key, v in (("heat_content", hc), ("enthalpy", en)):
        if v is not None:
            try:
                parse_heat_content(v)
            except ValueError as e:
                raise ValueError(f"mv.bills.{key}: {e}") from None
    mtype = spec.get("meter_type")
    fset = getattr(units, "factor_set", None)
    if mtype is not None and fset is None:
        raise ValueError("mv.bills.meter_type is read only with a units.factor_set")
    if units is None:
        return None, extra
    if not bills.units:
        raise ValueError(
            f"units.system is {units.system!r}, but the bills in {spec['file']!r} name no unit: "
            'give bills.units (e.g. "kWh", "therm", "Mcf") or a units column'
        )
    bare_m = _norm(bills.units) in ("mcf", "mscf")
    if fset is not None and hc is None and en is None:
        try:
            by_set = parse_unit(bills.units, kind=("energy", "volume", "mass")).kind != "energy"
        except ValueError:
            by_set = True
        if by_set:
            return _billing_by_factor_set(spec, bills, units, extra)
    try:
        k = units.energy_factor(bills.units, heat_content=hc, enthalpy=en)
        name = parse_unit(bills.units).name
    except ValueError as e:
        raise ValueError(f"mv.bills units: {e}") from None
    if bare_m:
        from .energy_factors import _m_caveat

        extra["caveats"].append(_m_caveat(bills.units, fset))
    return (k, units.energy, name, units.system), extra


# a gas volume or a mass with no bills.meter_type: read as camber.energy_units reads them
_DEFAULT_METER = {k: "natural_gas" for k in ("ft3", "CCF", "kcf", "MMcf", "m3")}
_DEFAULT_METER.update({k: "district_steam" for k in ("lb", "klb", "MMlb", "kg")})


def _billing_by_factor_set(spec: dict, bills, units, extra: dict) -> tuple:
    """The factor-set path of :func:`billing_conversion`."""
    from .energy_factors import factor_for, resolve_unit
    from .energy_units import energy_factor

    mtype = spec.get("meter_type")
    if mtype is None:
        try:
            key = resolve_unit(bills.units)[0]
        except ValueError as e:
            raise ValueError(f"mv.bills units: {e}") from None
        mtype = _DEFAULT_METER.get(key)
        kind = "volume" if mtype == "natural_gas" else "mass"
        if mtype is None:
            raise ValueError(
                f"mv.bills units: {bills.units!r} needs bills.meter_type to use "
                f'{units.factor_set} (e.g. "fuel_oil_2", "propane", "coal_bituminous")'
            )
        extra["caveats"].append(
            f"bills.meter_type not given: {bills.units!r} bills read as {mtype} "
            f"({'a gas volume' if kind == 'volume' else 'steam by mass'}); set bills.meter_type "
            "if they are another fuel."
        )
    try:
        c = factor_for(bills.units, mtype, factor_set=units.factor_set, region=units.region)
    except ValueError as e:
        raise ValueError(f"mv.bills units: {e}") from None
    k = c.multiplier * energy_factor("kBtu", units.energy)
    extra["metrics"]["energy_factor"] = c.as_dict()
    extra["caveats"] = [f"Converted with {c.describe()}.", *extra["caveats"], *c.caveats]
    return (k, units.energy, c.unit, units.system), extra


def billing_findings(entry: dict, prep, *, base_dir: str = ".") -> list:
    """``mv_baseline`` (and, with a ``reporting_period``, ``mv_savings`` or
    ``mv_method_proposal``) Findings for one billing entry. A bad entry is a ``ValueError``; a
    meter the data cannot serve is a declined Finding. With a config ``units`` block the reported
    energy is converted (0.92, #69); the fit stays in the bills' own unit. A factor set's use
    (``units.factor_set``) is recorded as ``energy_factor`` and a caveat on every finding."""
    from .config import _add_caveats, _mv_apply_units

    box: dict = {}
    out = _billing_findings(entry, prep, base_dir=base_dir, box=box)
    out = _mv_apply_units(out, box.get("conv"))
    extra = box.get("extra") or {}
    if extra.get("metrics") or extra.get("caveats"):
        for f in out:
            f.metrics.update(extra.get("metrics") or {})
        _add_caveats(out, list(extra.get("caveats") or []))
    if box.get("scale_finding") is not None:  # 0.92 (#71): quantities implausible at x1
        out = [box["scale_finding"], *out]
    return out


def _scale_spec(spec: dict) -> dict:
    sc = spec.get("scale_check")
    if sc is None:
        return {}
    if sc is False:
        return {"enabled": False}
    if not isinstance(sc, dict):
        raise ValueError("mv.bills.scale_check must be an object (or false)")
    extra = set(sc) - _SCALE_KEYS
    if extra:
        raise ValueError(f"mv.bills.scale_check: unknown key(s) {sorted(extra)}")
    if sc.get("on_implausible", "decline") not in ("decline", "warn"):
        raise ValueError('mv.bills.scale_check.on_implausible must be "decline" or "warn"')
    return sc


def _scale_tariff(sc: dict, base_dir: str):
    t = sc.get("tariff")
    if t is None:
        return None, None
    if not isinstance(t, dict):
        raise ValueError("mv.bills.scale_check.tariff must be an object")
    if "urdb_file" in t:
        import json

        from .config import _path

        with open(_path(base_dir, t["urdb_file"]), encoding="utf-8") as fh:
            return None, json.load(fh)
    if "urdb_label" in t:  # opt-in network fetch (OPENEI_API_KEY); only the rate label is sent
        from .interop.openei import fetch_urdb_rate

        return None, fetch_urdb_rate(str(t["urdb_label"]))
    from .tariff import Tariff

    try:
        return Tariff(**t), None
    except TypeError as e:
        raise ValueError(f"mv.bills.scale_check.tariff: {e}") from None


def billing_scale_check(entry: dict, bills, *, base_dir: str = ".", oat=None):
    """The unit-scale plausibility check of a billing entry (provisional, 0.92, #71), or ``None``
    when it cannot run (no unit, or a unit it cannot screen without ``scale_check.fuel``).

    Reads the bills file's rows as billed (estimated reads included) with the optional ``cost``,
    ``demand``, ``read_start``, ``read_end`` and ``multiplier`` columns (renamed by the
    ``bills.scale_check`` block), plus its ``area``, ``property_type``, ``tariff`` and price
    options; see :func:`camber.unit_scale.check_bills`. ``bills.scale_override`` has already been
    applied to the quantities the check sees. An explicit ``scale_check`` block turns a problem
    into a ``ValueError``; without one the check is skipped quietly.
    """
    from .config import _path
    from .unit_scale import check_bills

    spec = _spec(entry)
    sc = _scale_spec(spec)
    if sc.get("enabled", True) is False:
        return None
    explicit = bool(sc)
    unit = bills.units
    if not unit:
        if explicit:
            raise ValueError("mv.bills.scale_check needs the bills' unit (bills.units)")
        return None
    df = pd.read_csv(_path(base_dir, spec["file"]), encoding="utf-8-sig")
    cols = {"start": spec.get("start", "start"), "end": spec.get("end", "end")}
    cols["quantity"] = spec.get("energy", "energy")
    for c in _SCALE_COLUMNS:
        name = sc.get(c, c)
        if name in df.columns:
            cols[c] = name
        elif c in sc:
            raise ValueError(f"mv.bills.scale_check.{c}: the bills file has no column {name!r}")
    frame = pd.DataFrame({k: df[v] for k, v in cols.items()})
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    ov = spec.get("scale_override")
    tariff, urdb = _scale_tariff(sc, base_dir)
    try:
        return check_bills(
            frame,
            unit=str(unit),
            fuel=sc.get("fuel", spec.get("meter_type")),
            area=sc.get("area"),
            area_unit=sc.get("area_unit", "ft2"),
            property_type=sc.get("property_type"),
            oat=oat,
            tariff=tariff,
            urdb=urdb,
            price_source=sc.get("price_source"),
            state=sc.get("state"),
            eia_cache_dir=(
                None if sc.get("eia_cache_dir") is None else _path(base_dir, sc["eia_cache_dir"])
            ),
            eia_offline=bool(sc.get("eia_offline", False)),
            other_site_kbtu_per_year=sc.get("other_site_kbtu_per_year"),
            heat_content=spec.get("heat_content"),
            enthalpy=spec.get("enthalpy"),
            scale_override=ov,
            label=billing_label(entry),
            end_inclusive=bool(spec.get("end_inclusive", True)),
        )
    except ValueError as e:
        if explicit or ov is not None:
            raise ValueError(f"mv.bills.scale_check: {e}") from None
        return None


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
    box["conv"], box["extra"] = billing_conversion(  # 0.92 (#69)
        _spec(entry), bills, getattr(prep, "units", None)
    )
    if not len(bills):
        return _declined(label, "the bills file has no bills", reporting)
    ov = _spec(entry).get("scale_override")
    if ov is not None:  # 0.92 (#71): an explicit, recorded correction -- never an automatic one
        from .unit_scale import parse_scale_override

        try:
            ov = parse_scale_override(ov)
        except ValueError as e:
            raise ValueError(f"mv.bills.{e}") from None
        assert ov is not None
        bills.frame = bills.frame.assign(energy=bills.frame["energy"] * ov["factor"])
        box["extra"]["metrics"]["scale_override"] = dict(ov)
        box["extra"]["caveats"].append(
            f"bills.scale_override: every billed quantity multiplied by {ov['factor']:g} "
            f"({ov['reason']})"
        )
    f = bills.frame
    window = (f["start"].min(), f["end"].max() - pd.Timedelta(days=1))
    oat, oat_source = billing_oat(entry, prep, base_dir=base_dir, window=window)
    chk = billing_scale_check(entry, bills, base_dir=base_dir, oat=oat)  # 0.92 (#71)
    if chk is not None and chk.implausible:
        box["scale_finding"] = chk.finding(label)
        if _scale_spec(_spec(entry)).get("on_implausible", "decline") == "decline":
            return _declined(
                label,
                "the billed quantities are implausible as given (unit_scale finding); set "
                'bills.scale_override with a reason, or scale_check.on_implausible "warn"',
                reporting,
            )
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
