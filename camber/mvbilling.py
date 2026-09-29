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

**0.94 (#72).**

* ``base_f: "auto"`` chooses the heating and cooling degree-day bases from the baseline bills
  (:mod:`camber.mandv.basetemp`; degree days built from each day's temperatures and summed over
  each bill), and the degree-day model at those bases competes with the change-point models by
  BIC. The finding reports the selection profile, each base's range, R² and adjusted R², and a
  comparison of every candidate with the fixed-base (65 °F) model.
* Versioned billing baselines: ``camber mv freeze | rebaseline | adjust | propose | report`` and
  the run path treat a billing entry as a meter keyed ``(facility, name, "mv_bills")``. The frozen
  record keeps the model, the bills, the weather basis and the bases, so the reporting period uses
  the same bases; a rebaseline that moves them records ``bases_changed``.
* ``calendarize`` adds the Portfolio Manager calendar-month view; ``bills.cost`` carries the billed
  cost, and ``avoided_cost`` prices the saving at each bill's own rate or a stated one.

**0.95 (#74).** A rebaseline window of whole bills is searched
(:mod:`camber.mandv.billwindow`), ``rebaseline.bill_steps`` opts in to a step test on bills
(:mod:`camber.mandv.billsteps`), and with selected bases the SEP method proposal offers the
degree-day model too (the finding states its criterion).

Not supported for billing entries: the change-point + driver form (``model: cp_driver``) and
``interval`` other than daily. See docs/MANDV.md, "Billing data".
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

__all__ = [
    "BILLS_KIND",
    "base_spec",
    "bases_changed",
    "billing_fit_version",
    "billing_meter",
    "frame_for",
    "slice_bills",
    "billing_frame",
    "fit_billing_baseline",
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
_KNOWN |= {"base_search", "calendarize", "avoided_cost", "rebaseline"}  # 0.94 (#72)
#: the store kind of a billing entry's versioned baseline (0.94, #72); its equip is the label
BILLS_KIND = "mv_bills"
_FULL_YEAR_DAYS = 330  # a baseline of bills covering fewer days of service is flagged short
_BILL_KEYS = {"file", "start", "end", "energy", "estimated", "units", "end_inclusive"}
_BILL_KEYS |= {"merge_estimated", "units_column"}
_BILL_KEYS |= {"heat_content", "enthalpy"}  # 0.92 (#69): gas volumes and steam mass
_BILL_KEYS |= {"meter_type"}  # 0.92: the fuel a units.factor_set looks up (camber.energy_factors)
_BILL_KEYS |= {"scale_check", "scale_override"}  # 0.92 (#71): unit-scale plausibility
_BILL_KEYS |= {"cost"}  # 0.94 (#72): the column of each bill's billed cost
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
    if spec.get("cost"):  # 0.94 (#72)
        kw["cost"] = str(spec["cost"])
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

    0.94 (#73): the fetch follows the config's weather privacy (``oat.weather`` overrides the
    config's ``weather``; a private facility defaults to ``offline``), may name a ``place``
    (an airport code or a city) instead of coordinates, and is audited. An offline cache miss
    returns ``(None, why)`` with how to supply a file.
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
        cache = None if cache is None else _path(base_dir, cache)
        guard = _weather_guard(prep, spec.get("weather"), cache, "M&V billing weather")
        try:
            s = oat_reference_auto(
                spec.get("latitude"),
                spec.get("longitude"),
                window[0],
                window[1],
                source=str(spec["fetch"]),
                tz=spec.get("tz", "UTC"),
                cache_dir=cache,
                offline=bool(spec.get("offline", False)),
                **({"place": spec["place"]} if spec.get("place") else {}),
                **guard,
            )
        except LookupError as e:  # WeatherCacheMiss: offline, nothing cached
            if guard.get("privacy") is not None and guard["privacy"].offline:
                return None, f"no outdoor temperature: {e}"
            raise
        pol = guard.get("privacy")
        mode = f", privacy {pol.privacy}" if pol is not None else ""
        return s, f"fetched ({spec['fetch']}{mode})"
    shared = getattr(prep, "shared", None) or {}
    if Role.OAT in shared:
        return shared[Role.OAT], "shared_oat"
    return None, "no outdoor temperature (mv.oat or shared_oat)"


def _weather_guard(prep, spec_weather, cache_dir, purpose: str) -> dict:
    """The ``privacy`` / ``audit`` / ``purpose`` / ``place`` keywords of one fetch (0.94, #73);
    empty for a public fetch with nowhere to audit -- the pre-0.94 call exactly."""
    from .weather_privacy import WeatherContext

    wctx = getattr(prep, "weather", None) or WeatherContext()
    pol = wctx.policy(spec_weather)
    audit = wctx.audit(pol, cache_dir)
    out: dict = {}
    if pol.privacy != "public":
        out["privacy"] = pol
    if audit is not None:
        out.update(audit=audit, purpose=purpose)
    return out


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


def _scale_tariff(sc: dict, base_dir: str, guard: dict | None = None):
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

        return None, fetch_urdb_rate(str(t["urdb_label"]), **(guard or {}))
    from .tariff import Tariff

    try:
        return Tariff(**t), None
    except TypeError as e:
        raise ValueError(f"mv.bills.scale_check.tariff: {e}") from None


def billing_scale_check(entry: dict, bills, *, base_dir: str = ".", oat=None, prep=None):
    """The unit-scale plausibility check of a billing entry (provisional, 0.92, #71), or ``None``
    when it cannot run (no unit, or a unit it cannot screen without ``scale_check.fuel``).

    Reads the bills file's rows as billed (estimated reads included) with the optional ``cost``,
    ``demand``, ``read_start``, ``read_end`` and ``multiplier`` columns (renamed by the
    ``bills.scale_check`` block), plus its ``area``, ``property_type``, ``tariff`` and price
    options; see :func:`camber.unit_scale.check_bills`. ``bills.scale_override`` has already been
    applied to the quantities the check sees. An explicit ``scale_check`` block turns a problem
    into a ``ValueError``; without one the check is skipped quietly. ``prep`` (0.94) carries the
    config's weather privacy, which the EIA and URDB requests follow and are audited under.
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
    guard: dict = {}
    t_spec = sc.get("tariff")
    urdb_fetch = isinstance(t_spec, dict) and "urdb_label" in t_spec
    if prep is not None and (sc.get("price_source") == "eia" or urdb_fetch):
        cache = sc.get("eia_cache_dir")
        guard = _weather_guard(
            prep, None, None if cache is None else _path(base_dir, cache), "unit-scale check"
        )
    from .weather_privacy import PrivacyViolation

    try:
        tariff, urdb = _scale_tariff(sc, base_dir, guard)
    except PrivacyViolation as e:
        raise ValueError(f"mv.bills.scale_check.tariff: {e}") from None
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
            **{k: v for k, v in guard.items() if k in ("privacy", "audit")},
        )
    except ValueError as e:
        if explicit or ov is not None:
            raise ValueError(f"mv.bills.scale_check: {e}") from None
        return None


# --------------------------------------------------------------------------- 0.94 (#72)
# Degree-day bases chosen from the bills (``base_f: "auto"``), and the one place a billing
# baseline is fitted -- shared by the run path and ``camber mv freeze`` / ``rebaseline``.


def base_spec(entry: dict) -> tuple:
    """``(auto, base_f, search)`` of a billing entry (provisional, 0.94).

    ``base_f`` is a number (the fixed base, default 65 °F, exactly as before) or ``"auto"``: the
    heating and cooling bases are then chosen from the baseline bills
    (:func:`camber.mandv.basetemp.select_bases`) with the entry's ``base_search`` block, and the
    fixed base the result is compared with is ``base_search.fixed_f`` (65).
    """
    from .mandv.basetemp import BaseSearch

    raw = entry.get("base_f", 65.0)
    auto = isinstance(raw, str) and raw.strip().lower() == "auto"
    if not auto and entry.get("base_search") is not None:
        raise ValueError('mv.base_search needs base_f "auto"')
    if auto:
        try:
            search = BaseSearch.from_dict(entry.get("base_search"))
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"mv.{e}" if str(e).startswith("base_search") else f"mv.base_search: {e}"
            ) from None
        return True, float(search.fixed_f), search
    try:
        return False, float(raw), None
    except (TypeError, ValueError):
        raise ValueError(f'mv.base_f must be a number or "auto", got {raw!r}') from None


def billing_frame(
    bills,
    oat,
    *,
    heating_base_f: float,
    cooling_base_f: float,
    min_coverage: float = 0.9,
    dd_kind: str | None = None,
    bases_fitted: bool = True,
):
    """The bills frame at given bases; with ``dd_kind`` it also offers the degree-day model at
    those bases to the fit (:func:`camber.mandv._mvform.best_billing_model`)."""
    frame = bills.energy_vs_temp(
        oat,
        heating_base_f=heating_base_f,
        cooling_base_f=cooling_base_f,
        min_coverage=min_coverage,
    )
    if dd_kind:
        frame.attrs["dd_kind"] = dd_kind
        frame.attrs["dd_bases_fitted"] = bool(bases_fitted)
    return frame


def _stored_bases(prov: dict) -> dict | None:
    b = (prov or {}).get("billing") or {}
    if b.get("heating_base_f") is None and b.get("cooling_base_f") is None:
        return None
    return b


def fit_billing_baseline(bills, oat, entry: dict, period, *, stored: dict | None = None) -> dict:
    """Fit a billing baseline the one way both the run path and ``camber mv`` do (0.94).

    ``period`` is the baseline window (``None``: every bill). With a numeric ``base_f`` the frame
    is at that base and the best change-point model is fitted, exactly as before. With
    ``base_f: "auto"`` the bases are selected on the baseline bills, the frame is rebuilt at them,
    and the degree-day model at those bases competes with the change-point models by BIC.
    ``stored`` (a frozen version's ``provenance["billing"]``) reuses its bases instead of
    selecting: pre and post periods use the same bases, and changing them is a rebaseline.

    Returns ``frame`` (every bill), ``base`` (the baseline bills), ``model`` (``None`` when fewer
    than ``min_bills``), ``selection`` (a :class:`~camber.mandv.basetemp.BaseSelection` or
    ``None``), ``bases`` (the provenance record of the bases) and ``notes``.
    """
    from .mandv import _mvform

    auto, base_f, search = base_spec(entry)
    min_cov = float(entry.get("min_coverage", 0.9))
    min_bills = int(entry.get("min_bills", 9))
    frame = bills.energy_vs_temp(oat, base_f=base_f, min_coverage=min_cov)
    cut = _slicer(frame)
    base = cut(period) if period else (frame if len(frame) else None)
    out: dict = {
        "frame": frame,
        "base": base,
        "model": None,
        "selection": None,
        "bases": {"base_f": "auto" if auto else base_f},
        "notes": [],
        "auto": auto,
        "search": search,
    }
    if base is None or len(base) < min_bills:
        return out
    if stored is not None:
        hb = stored.get("heating_base_f")
        cb = stored.get("cooling_base_f")
        kind = stored.get("dd_kind")
        frame = billing_frame(
            bills,
            oat,
            heating_base_f=base_f if hb is None else float(hb),
            cooling_base_f=base_f if cb is None else float(cb),
            min_coverage=min_cov,
            dd_kind=kind,
            bases_fitted=bool(stored.get("bases_fitted", True)),
        )
        out["bases"] = dict(stored)
    elif auto:
        from .mandv.basetemp import select_bases

        sel = select_bases(base, oat, search=search)
        out["selection"] = sel
        if sel.model is None:
            out["notes"].append(
                f"degree-day bases not selected ({sel.declined_reason}); the change-point models "
                f"were fitted with degree days at {base_f:g} F"
            )
            out["bases"] = {
                "base_f": "auto",
                "heating_base_f": base_f,
                "cooling_base_f": base_f,
                "dd_kind": None,
                "selected": False,
            }
        else:
            hb = sel.heating_base_f if sel.heating_base_f is not None else base_f
            cb = sel.cooling_base_f if sel.cooling_base_f is not None else base_f
            frame = billing_frame(
                bills, oat, heating_base_f=hb, cooling_base_f=cb, min_coverage=min_cov,
                dd_kind=sel.kind,
            )  # fmt: skip
            out["bases"] = {
                "base_f": "auto",
                "heating_base_f": float(hb),
                "cooling_base_f": float(cb),
                "dd_kind": sel.kind,
                "bases_fitted": True,
                "selected": True,
                "heating_range": None if sel.heating_range is None else list(sel.heating_range),
                "cooling_range": None if sel.cooling_range is None else list(sel.cooling_range),
                "flat": list(sel.flat),
                "at_edge": list(sel.at_edge),
            }
            out["notes"] += list(sel.caveats)
    if frame is not out["frame"]:
        out["frame"] = frame
        cut = _slicer(frame)
        base = cut(period) if period else frame
        out["base"] = base
    out["model"] = _mvform.fit(base)
    return out


def _model_comparison(fitted: dict, bills, oat, entry: dict) -> tuple:
    """``(rows, fixed)``: every change-point kind, the degree-day model at the selected bases and
    the same kind at the fixed base, compared on the baseline bills (R², adjusted R², CV(RMSE),
    NMBE, BIC); and the fixed-base summary."""
    from .mandv.basetemp import compare_models, fit_bill_degree_day, rows_at_bases
    from .mandv.models import fit_model

    base = fitted["base"]
    sel = fitted["selection"]
    search = fitted["search"]
    days = base["days"].to_numpy(float)
    T, y = base["oat"].to_numpy(float), base["energy"].to_numpy(float)
    models: list = []
    for k in ("2P", "3PC", "3PH", "4P", "5P"):
        try:
            models.append((k, fit_model(T, y, k, time_index=base.index, weights=days), base))
        except (ValueError, np.linalg.LinAlgError):
            continue
    fixed: dict | None = None
    if sel is not None and sel.model is not None:
        kind = sel.kind
        dd_sel = fit_bill_degree_day(
            rows_at_bases(base, kind, sel.heating_base_f, sel.cooling_base_f),
            y,
            kind=kind,
            heating_base_f=sel.heating_base_f,
            cooling_base_f=sel.cooling_base_f,
            days=days,
            time_index=base.index,
        )
        models.append((f"{kind} fitted bases", dd_sel, base))
        fb = float(search.fixed_f)
        ff = billing_frame(
            bills, oat, heating_base_f=fb, cooling_base_f=fb,
            min_coverage=float(entry.get("min_coverage", 0.9)),
        )  # fmt: skip
        fbase = ff.loc[base.index.intersection(ff.index)]
        hb = fb if sel.heating_base_f is not None else None
        cb = fb if sel.cooling_base_f is not None else None
        dd_fix = fit_bill_degree_day(
            rows_at_bases(fbase, kind, hb, cb),
            fbase["energy"].to_numpy(float),
            kind=kind,
            heating_base_f=hb,
            cooling_base_f=cb,
            days=fbase["days"].to_numpy(float),
            bases_fitted=False,
            time_index=fbase.index,
        )
        models.append((f"{kind} fixed {fb:g} F", dd_fix, fbase))
        fixed = {
            "base_f": fb,
            "hdd_total": round(float(fbase["hdd"].sum()), 1),
            "cdd_total": round(float(fbase["cdd"].sum()), 1),
        }
        if dd_fix.caveats:
            fixed["refused"] = "; ".join(dd_fix.caveats)
    rows = compare_models(base, models, r2_min=float(search.r2_min))
    chosen = fitted["model"]
    for r, (_lbl, m, _f) in zip(rows, models):
        same = getattr(m, "kind", None) == getattr(chosen, "kind", None) and (
            getattr(m, "bases_fitted", True) is not False
        )
        r["selected"] = bool(same)
        if getattr(m, "caveats", None):
            r["refused"] = "; ".join(m.caveats)
    if fixed is not None:
        fixed["model"] = rows[-1]
        rows_fit = [r for r in rows if r["label"].endswith("fitted bases")]
        if rows_fit:
            fixed["fitted"] = rows_fit[0]
    return rows, fixed


def _versions_of(prep, label: str) -> list:
    store = getattr(prep, "mv_store", None)
    if store is None:
        return []
    try:
        return store.versions(prep.site, label, BILLS_KIND)
    except Exception:  # noqa: BLE001 -- an unreadable store is the run path's concern
        return []


def _billing_cost(entry: dict, bills, cut, model, fnd) -> None:
    """``avoided_cost`` on a savings Finding (0.94, #72): the saving priced at each reporting
    bill's own rate (``"bills"``: its billed cost over its energy), or a stated ``rate``. The
    reporting bills are those of the finding's own (possibly cut) reporting period."""
    spec = entry.get("avoided_cost")
    if spec is None or fnd.metrics.get("declined") or fnd.rule != "mv_savings":
        return
    from .mandv import _mvform

    win = fnd.metrics.get("reporting_period")
    rep = cut(win) if win else None
    if rep is None:
        return

    sav = fnd.metrics.get("avoided_energy", fnd.metrics.get("savings"))
    band = fnd.metrics.get("abs_uncertainty")
    if sav is None:
        return
    if spec == "bills" or (isinstance(spec, dict) and spec.get("rate") in (None, "bills")):
        if "cost" not in rep.columns:
            raise ValueError('mv.avoided_cost "bills" needs the bills\' cost column (bills.cost)')
        d = rep["days"].to_numpy(float)
        e = rep["energy"].to_numpy(float) * d
        c = rep["cost"].to_numpy(float)
        ok = np.isfinite(c) & (e > 0)
        rate = np.where(ok, c / np.where(e > 0, e, 1.0), np.nan)
        blended = float(c[ok].sum() / e[ok].sum()) if ok.any() else float("nan")
        if fnd.metrics.get("method") == "forecast":
            pred = np.asarray(model.predict(_mvform.design_rows(rep, model)), float) * d
            per = pred - e
            r = np.where(np.isfinite(rate), rate, blended)
            cost = float(np.sum(per * r))
        else:
            cost = float(sav * blended)
        basis = "each reporting bill's own rate (billed cost / billed energy)"
    else:
        if not isinstance(spec, dict) or not isinstance(spec.get("rate"), (int, float)):
            raise ValueError('mv.avoided_cost must be "bills" or {"rate": <cost per unit>}')
        blended = float(spec["rate"])
        cost = float(sav * blended)
        basis = f"a stated rate of {blended:g} per {bills.units or 'unit'}"
    fnd.metrics["avoided_cost"] = round(cost, 2)
    fnd.metrics["avoided_cost_rate"] = None if not np.isfinite(blended) else round(blended, 6)
    fnd.metrics["avoided_cost_basis"] = basis
    if band is not None and np.isfinite(blended):
        fnd.metrics["avoided_cost_uncertainty"] = round(float(band) * blended, 2)


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
    auto, base_f, _search = base_spec(entry)  # 0.94 (#72): a number, or "auto"
    if entry.get("rebaseline") is not None:  # a bad policy block is a config error, up front
        from .mandv.rebaseline import RebaselinePolicy, events_from_entry

        RebaselinePolicy.from_entry(entry)
        events_from_entry(entry)
    _check_cost_spec(entry)

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
    chk = billing_scale_check(entry, bills, base_dir=base_dir, oat=oat, prep=prep)  # 0.92 (#71)
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
    versions = _versions_of(prep, label)  # 0.94 (#72): a frozen billing baseline
    stored = rec = None
    if versions:
        rec = versions[-1]
        if reporting is not None:
            rec = prep.mv_store.in_force(prep.site, label, BILLS_KIND, reporting[0]) or rec
        stored = _stored_bases(rec.provenance)
        period = [rec.period_start, rec.period_end]
    fitted = fit_billing_baseline(bills, oat, entry, period, stored=stored)
    frame = fitted["frame"]
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
    base = fitted["base"]
    if base is None or len(base) < min_bills:
        n = 0 if base is None else len(base)
        why = f"only {n} usable bill(s) in the baseline (< {min_bills})"
        out = _declined(label, why, reporting)
        _add_caveats(out, notes)
        return out
    if versions:
        return _billing_versioned(
            entry, label, prep, versions, fitted, cut, notes, bills, oat, oat_source, rec,
            reporting=reporting, policy=policy, method=method, declared=declared,
            validity=validity, schedule=schedule, adj_specs=adj_specs, min_bills=min_bills,
        )  # fmt: skip
    model = fitted["model"]
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
    base_notes: list = []
    if auto:  # 0.94 (#72): the selected bases, the profile and the model comparison
        base_notes = list(fitted["notes"]) + _auto_metrics(metrics, fitted, st, bills, oat, entry)
    metrics.update(_extra_views(entry, bills, oat, fitted, base))
    out = [
        Finding(
            rule="mv_baseline",
            equip=label,
            severity="ok" if st.accept else "info",
            metrics=metrics,
            summary=(
                f"{label}: {model.kind} billing baseline (days-weighted), R2 {st.r2:.2f}, "
                f"CV(RMSE) {st.cv_rmse:.1%} over {st.n} bills ({int(days_b.sum())} days) -- "
                f"{verdict} monthly G14 acceptance" + (_auto_suffix(fitted, model) if auto else "")
            ),
            caveats=list(notes) + base_notes,
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
        _proposal_criterion(out[-1], frame)  # 0.95 (#74): the degree-day model is a candidate
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
        _billing_cost(entry, bills, cut, model, fnd)
    _add_caveats(out[1:], notes)
    return out


def _proposal_criterion(fnd, frame) -> None:
    """State the SEP proposal's model criterion on its finding (0.95, #74): with bases selected
    from the bills, the degree-day model at them is a candidate beside the change-point kinds."""
    kind = frame.attrs.get("dd_kind")
    if not kind or fnd.metrics.get("declined_reason") == "no usable days":
        return
    fnd.metrics["model_criterion"] = (
        "SEP validity (§6.4.1), then adjusted R² with every fitted parameter counted"
    )
    fnd.metrics["degree_day_candidate"] = {
        "kind": kind,
        "heating_base_f": frame.attrs.get("heating_base_f") if kind != "DD-C" else None,
        "cooling_base_f": frame.attrs.get("cooling_base_f") if kind != "DD-H" else None,
    }


def _check_cost_spec(entry: dict) -> None:
    spec = entry.get("avoided_cost")
    if spec is None:
        return
    if spec == "bills":
        ok = True
    elif isinstance(spec, dict):
        ok = set(spec) <= {"rate"} and (
            spec.get("rate") == "bills" or isinstance(spec.get("rate"), (int, float))
        )
    else:
        ok = False
    if not ok:
        raise ValueError('mv.avoided_cost must be "bills" or {"rate": <cost per billed unit>}')
    if (spec == "bills" or spec.get("rate") == "bills") and not _spec(entry).get("cost"):
        raise ValueError('mv.avoided_cost "bills" needs bills.cost (the column of billed cost)')


def _auto_suffix(fitted: dict, model) -> str:
    b = fitted["bases"]
    if not b.get("selected"):
        return "; degree-day bases not selected"
    bits = []
    if b.get("dd_kind") in ("DD-H", "DD-HC"):
        bits.append(f"heating base {b['heating_base_f']:g} F")
    if b.get("dd_kind") in ("DD-C", "DD-HC"):
        bits.append(f"cooling base {b['cooling_base_f']:g} F")
    return "; " + ", ".join(bits) + " (selected from the bills)"


def _auto_metrics(metrics: dict, fitted: dict, st, bills, oat, entry: dict) -> list:
    """Add the ``base_f: "auto"`` metrics; return their caveats."""
    sel = fitted["selection"]
    search = fitted["search"]
    b = fitted["bases"]
    metrics["base_f"] = "auto"
    metrics["heating_base_f"] = b.get("heating_base_f")
    metrics["cooling_base_f"] = b.get("cooling_base_f")
    metrics["adj_r2"] = st.adj_r2
    metrics["n_params"] = int(st.p)
    metrics["base_selection"] = sel.as_dict() if sel is not None else None
    rows, fixed = _model_comparison(fitted, bills, oat, entry)
    metrics["model_comparison"] = rows
    metrics["fixed_base"] = fixed
    caveats = []
    if np.isfinite(st.r2) and st.r2 < float(search.r2_min):
        caveats.append(
            f"R2 {st.r2:.2f} is below {float(search.r2_min):.2f} (the SEP 50001 M&V Protocol "
            "2019 Ed. 2 §6.4.1 validity threshold): weather explains little of the variation in "
            "this meter's use -- a caveat under validity 'g14', not a refusal"
        )
    for r in rows:
        if r.get("selected") and r.get("mismatch"):
            caveats.append(f"{r['model']}: {r['mismatch']}")
    return caveats


def _extra_views(entry: dict, bills, oat, fitted: dict, base) -> dict:
    """The opt-in views of the bills: ``calendarized`` months and the billed cost."""
    out: dict = {}
    cal = entry.get("calendarize")
    if cal:
        if cal is not True and not (isinstance(cal, dict) and set(cal) <= {"max_gap_days"}):
            raise ValueError('mv.calendarize must be true or {"max_gap_days": n}')
        mg = int(cal.get("max_gap_days", 0)) if isinstance(cal, dict) else 0
        f = fitted["frame"]
        c = bills.calendarize(
            oat,
            heating_base_f=f.attrs.get("heating_base_f", 65.0),
            cooling_base_f=f.attrs.get("cooling_base_f", 65.0),
            max_gap_days=mg,
        )
        out["calendarized"] = c.as_dict()
    if "cost" in base.columns:
        c = base["cost"].to_numpy(float)
        e = base["energy"].to_numpy(float) * base["days"].to_numpy(float)
        ok = np.isfinite(c) & (e > 0)
        if ok.any():
            u = c[ok] / e[ok]
            out["billed_cost"] = round(float(c[ok].sum()), 2)
            out["unit_cost"] = {
                "blended": round(float(c[ok].sum() / e[ok].sum()), 6),
                "min": round(float(u.min()), 6),
                "median": round(float(np.median(u)), 6),
                "max": round(float(u.max()), 6),
                "per": bills.units,
            }
    return out


def _billing_versioned(
    entry, label, prep, versions, fitted, cut, notes, bills, oat, oat_source, rec, *, reporting,
    policy, method, declared, validity, schedule, adj_specs, min_bills,
) -> list:  # fmt: skip
    """A billing entry measured against its frozen version (0.94, #72): the trended path's
    versioned findings (:func:`camber.config._mv_versioned_findings`) on the bills frame at the
    version's bases."""
    from .config import _add_caveats, _mv_versioned_findings

    b = fitted["bases"]
    extra = {
        "billing": True,
        "weighted_by_days": True,
        "units": bills.units,
        "g14_interval": "monthly",
        "oat_source": oat_source,
    }
    if b.get("heating_base_f") is not None or b.get("cooling_base_f") is not None:
        extra["heating_base_f"] = b.get("heating_base_f")
        extra["cooling_base_f"] = b.get("cooling_base_f")
    out = _mv_versioned_findings(
        entry,
        label,
        None,
        None,
        prep,
        versions,
        policy=policy,
        method=method,
        declared=declared,
        validity=validity,
        schedule=schedule,
        adj_specs=adj_specs,
        bills={"frame": fitted["frame"], "slice": cut, "min_rows": min_bills, "metrics": extra},
    )
    model = prep.mv_store.model_of(rec)
    for f in out:
        if f.rule in ("mv_savings", "mv_method_proposal"):
            f.metrics["billing"] = True
            if f.rule == "mv_savings":
                _billing_cost(entry, bills, cut, model, f)
            elif not f.metrics.get("declined"):
                _proposal_criterion(f, fitted["frame"])  # 0.95 (#74)
    if out and out[0].rule == "mv_baseline" and not out[0].metrics.get("declined"):
        sub = cut([rec.period_start, rec.period_end])
        if sub is not None:
            out[0].metrics["n_bills"] = int(len(sub))
            out[0].metrics["n_days"] = int(sub["days"].sum())
        out[0].metrics.update(_extra_views(entry, bills, oat, fitted, fitted["base"]))
    _add_caveats(out, notes)
    return out


# --------------------------------------------------------------------------- versioned bills
# 0.94 (#72): what `camber mv freeze | rebaseline | adjust | propose | report` need of a billing
# entry. The store keys a billing baseline as (facility, the entry's label, "mv_bills").


def billing_meter(k: int, entry: dict, prep, *, base_dir: str = "."):
    """``(bills, oat, oat_source, frame)`` of a billing entry for the versioned verbs, or ``None``
    when its bills or temperature are missing. ``bills.scale_override`` is applied (the fit is on
    the corrected quantities, as on the run path); ``frame`` is at the entry's own base."""
    bills = load_bills(entry, base_dir=base_dir)
    if not len(bills):
        return None
    ov = _spec(entry).get("scale_override")
    if ov is not None:
        from .unit_scale import parse_scale_override

        ov = parse_scale_override(ov)
        assert ov is not None
        bills.frame = bills.frame.assign(energy=bills.frame["energy"] * ov["factor"])
    f = bills.frame
    window = (f["start"].min(), f["end"].max() - pd.Timedelta(days=1))
    oat, source = billing_oat(entry, prep, base_dir=base_dir, window=window)
    if oat is None:
        return None
    oat = pd.Series(oat).dropna()
    if oat.index.tz is not None:
        oat.index = oat.index.tz_localize(None)
    _auto, base_f, _s = base_spec(entry)
    frame = bills.energy_vs_temp(
        oat, base_f=base_f, min_coverage=float(entry.get("min_coverage", 0.9))
    )
    return bills, oat, source, frame


def frame_for(ms, prov: dict | None):
    """The bills frame of a meter at a stored version's bases (its own base without them)."""
    b = _stored_bases(prov or {}) if prov is not None else None
    if b is None:
        return ms.daily
    _auto, base_f, _s = base_spec(ms.entry)
    hb, cb = b.get("heating_base_f"), b.get("cooling_base_f")
    return billing_frame(
        ms.bills,
        ms.oat,
        heating_base_f=base_f if hb is None else float(hb),
        cooling_base_f=base_f if cb is None else float(cb),
        min_coverage=float(ms.entry.get("min_coverage", 0.9)),
        dd_kind=b.get("dd_kind"),
        bases_fitted=bool(b.get("bases_fitted", True)),
    )


def slice_bills(frame: pd.DataFrame, win) -> pd.DataFrame:
    """The bills wholly inside ``win`` (an empty frame when none)."""
    sub = _slicer(frame)(win)
    return frame.iloc[:0] if sub is None else sub


def billing_fit_version(ms, period, *, allow_short: bool = False, stored: dict | None = None):
    """:func:`camber.mvrun.fit_version` for a billing meter: the fit, its statistics at the
    monthly G14 thresholds (days-weighted), regression tests, SEP verdict, and the
    ``provenance["billing"]`` record (the bills, the weather basis and the bases)."""
    from .mandv import _mvform
    from .mandv.stats import (
        cv_rmse_max_for,
        fit_stats,
        logical_signs,
        model_regression_tests,
        sep_validity,
    )
    from .mvrun import _ds, baseline_window_check

    entry = ms.entry
    fitted = fit_billing_baseline(ms.bills, ms.oat, entry, period, stored=stored)
    sub = slice_bills(fitted["frame"], period)
    # the bills' own span is the window (a bill straddling an edge of the period is not a gap)
    span = [sub["start"].min(), sub["end"].max() - pd.Timedelta(days=1)] if len(sub) else period
    missing, why = baseline_window_check(int(sub["days"].sum()) if len(sub) else 0, span, entry)
    caveats = list(fitted["notes"])
    if why:
        if not allow_short:
            raise ValueError(why + " -- pass --allow-short to freeze it anyway, with a caveat")
        caveats.append("short or gappy baseline accepted by override: " + why)
    min_bills = int(entry.get("min_bills", 9))
    model = fitted["model"]
    if model is None or len(sub) < min_bills:
        raise ValueError(f"only {len(sub)} usable bill(s) in {period} (< {min_bills})")
    X = _mvform.design_rows(sub, model)
    y = sub["energy"].to_numpy(float)
    d = _mvform.row_days(sub)
    st = fit_stats(
        y,
        model.predict(X),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("monthly"),
        time_index=sub.index,
        weights=d,
    )
    tests = model_regression_tests(model, X, y, time_index=sub.index, weights=d)
    vd = sep_validity(tests, signs=logical_signs(model))
    f = ms.bills.frame
    rows = []
    for r in f.itertuples(index=False):
        row = {
            "start": _ds(r.start),
            "end": _ds(r.end - pd.Timedelta(days=1)),
            "days": int(r.days),
            "energy": float(r.energy),
            "estimated": bool(r.estimated),
        }
        if "cost" in f.columns:
            c = float(r.cost)
            row["cost"] = c if np.isfinite(c) else None
        rows.append(row)
    sel = fitted["selection"]
    billing = {
        **fitted["bases"],
        "units": ms.bills.units,
        "weather_basis": {
            "oat_source": ms.oat_source,
            "min_coverage": float(entry.get("min_coverage", 0.9)),
            "degree_days": "from each day's temperatures, summed over each bill's service days",
        },
        "bills": rows,
        "estimated_reads": list(ms.bills.merged),
        "base_selection": None if sel is None else sel.as_dict(),
    }
    return {
        "model": model,
        "st": st,
        "tests": tests,
        "sep": vd,
        "sub": sub,
        "frame": fitted["frame"],
        "missing_frac": round(max(missing, 0.0), 4),
        "caveats": caveats,
        "billing": billing,
    }


def bases_changed(old: dict | None, new: dict) -> dict | None:
    """``{"from", "to"}`` when a rebaseline moves the degree-day bases (else ``None``)."""
    keys = ("heating_base_f", "cooling_base_f", "dd_kind")
    a = {k: (old or {}).get(k) for k in keys}
    b = {k: new.get(k) for k in keys}
    return None if a == b else {"from": a, "to": b}
