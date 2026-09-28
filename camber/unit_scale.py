"""Unit-scale plausibility: are a meter's billed quantities right at x0.001, x1 or x1000? (#71)

A unit prefix read the wrong way -- a steam bill printing "MLb" for thousands of pounds, gas in
"Mcf" meaning a million cubic feet, a meter export in MWh labelled kWh -- moves every number
derived from the bill by a factor of 1,000 and passes every schema check. It usually surfaces only
when an EUI comes out impossible and someone works backwards. This module judges each meter
independently: for the three candidate scales (the quantities as billed x0.001, x1 and x1000) it
collects evidence, combines it by a simple conservative rule, and returns a
:class:`UnitScaleCheck` with the most likely scale, a confidence and a readable explanation. It
**never corrects anything**: an implausible reading is reported, and a correction is an explicit
``scale_override`` with a reason, recorded in provenance.

Evidence (each item gives a verdict per scale -- plausible, implausible or no opinion -- and a
weight):

=====================  ======  ==============================================================
item                   weight  what it compares
=====================  ======  ==============================================================
``tariff``             4       the invoice recomputed from the billed kWh (and kW) under a
                               :class:`~camber.tariff.Tariff` (or a URDB rate) against the
                               invoiced $, per bill (:func:`camber.tariff.validate_bill`); the
                               fixed charge does not scale
``price``              3       the implied $/MMBtu (all charges / energy) against the state's
                               EIA commercial average (opt-in, :mod:`camber.interop.eia`) or
                               the bundled ``camber_price_bands_2024`` bands
``eui``                2 or 3  the annualised site EUI of this fuel (and the building total,
                               when the other meters are given) against the property type's
                               ENERGY STAR median x CAMBER policy factors (2), or against hard
                               physical bounds that hold for any building (3)
``weather_intensity``  2       heating fuel per HDD per ft2, and the peak bill's average
                               heating power per ft2 (for steam also as lb/h)
``load_factor``        2       electricity kWh against billed kW (a load factor above 1 is
                               impossible)
``meter_reads``        2       the billed quantity against (end read - start read) x multiplier
``continuity``         --      a ~1000x step in the series (bill to bill, or year over year):
                               a unit change on the bill; the series is split there
=====================  ======  ==============================================================

**The combination rule** (deliberately simple and conservative):

1. A scale is **ruled out** when the evidence against it includes an item of weight 3 or more,
   or adds up to 4 or more, *and* the heaviest item against it outweighs the heaviest item for
   it. So a decisive tariff match (4) beats a hard EUI bound (3), a price band (3) beats any EUI
   policy verdict (2), equal weights on both sides rule nothing out, and EUI and weather
   intensity together (2 + 2) rule a scale out only when no price or tariff evidence supports it.
2. The chosen scale is the scale not ruled out with the highest net score (support minus
   against); a tie goes to x1, the reading as billed.
3. The status is ``implausible`` when x1 is ruled out (or the series has a ~1000x step),
   ``uncertain`` when another scale scores higher but x1 is not ruled out, ``plausible`` when x1
   is chosen, and ``insufficient`` when no item had an opinion.
4. The confidence is ``high`` when every other scale is ruled out and either the tariff decides it
   or two independent items (weight >= 2) support the chosen scale; ``medium`` when every other
   scale is ruled out; ``low`` otherwise.

The price bands, EUI policy factors and intensity bounds are **CAMBER screening policy**, not a
standard: wide enough that a correctly billed laboratory, hospital or data centre stays inside
them, because the error they look for is a factor of 1,000. See docs/UNITS.md, "Unit-scale
plausibility".

Provisional (0.92): names and signatures may change in a minor release.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .energy_units import KBTU_PER_KWH

__all__ = [
    "SCALES",
    "DEFAULT_PRICE_BANDS",
    "DEFAULT_EUI_REFERENCE",
    "Evidence",
    "UnitScaleCheck",
    "check_bills",
    "check_series",
    "check_eui",
    "parse_scale_override",
    "fuel_group",
    "kbtu_per_unit",
]

#: The candidate scales: the quantities as billed x0.001, x1 and x1000.
SCALES = (0.001, 1.0, 1000.0)
#: The bundled price-band set (camber.energy_factors, kind "price_band").
DEFAULT_PRICE_BANDS = "camber_price_bands_2024"
#: The bundled EUI reference (camber.energy_factors, kind "eui_reference").
DEFAULT_EUI_REFERENCE = "energy_star_us_median_eui_2024"

_ES = "energy_star_thermal_2015"
_HEATING = frozenset({"natural_gas", "district_steam", "district_hot_water", "fuel_oil", "propane"})
_OILS = frozenset({"fuel_oil_1", "fuel_oil_2", "fuel_oil_4", "fuel_oil_5_6", "diesel", "kerosene"})
_FUEL_ALIASES = {
    "chilledwater": "district_chilled_water",
    "chw": "district_chilled_water",
    "hotwater": "district_hot_water",
    "hw": "district_hot_water",
    "elec": "electricity",
    "electric": "electricity",
    "naturalgas": "natural_gas",
}
# a unit alone names its fuel (used when no fuel is given)
_UNIT_FUEL = {
    "kWh": "electricity",
    "MWh": "electricity",
    "GWh": "electricity",
    "Wh": "electricity",
    "therm": "natural_gas",
    "ft3": "natural_gas",
    "CCF": "natural_gas",
    "Mcf": "natural_gas",
    "kcf": "natural_gas",
    "MMcf": "natural_gas",
    "m3": "natural_gas",
    "lb": "district_steam",
    "klb": "district_steam",
    "MMlb": "district_steam",
    "ton-hour": "district_chilled_water",
}
# the ambiguous M-prefixed labels camber.energy_units refuses, and how this check reads them
_M_READINGS = {
    "mlb": ("klb", "a thousand pounds (the US utility M)"),
    "mlbs": ("klb", "a thousand pounds (the US utility M)"),
    "mbtu": ("kBtu", "a thousand Btu (the US utility M)"),
}
_LABELS = {0.001: "x0.001", 1.0: "x1", 1000.0: "x1000"}

# weather-intensity policy (Btu per ft2 per degF-day HDD65; Btu/h per ft2)
_HDD_PLAUSIBLE = (0.3, 150.0)
_HDD_IMPLAUSIBLE = (0.003, 3000.0)
_HDD_MIN_TOTAL = 1500.0
_PEAK_PLAUSIBLE = 150.0
_PEAK_IMPLAUSIBLE = 2000.0
# load factor
_LF_PLAUSIBLE = (0.05, 1.0)
_LF_IMPLAUSIBLE = (0.003, 1.5)
# tariff match
_TARIFF_TOL_PCT = 15.0
_TARIFF_OK_MAPE = 25.0
_TARIFF_BAD_MAPE = 60.0
# a step between two bills (log10 of the ratio)
_STEP_LO, _STEP_HI = 2.5, 3.5
_FLAT = 1.0001  # bills this close to each other are a repeated placeholder, not a reading
# EIA state average -> screening band
_EIA_PLAUSIBLE_FACTOR = 4.0
_EIA_IMPLAUSIBLE_FACTOR = 40.0


# --------------------------------------------------------------------------- results


@dataclass
class Evidence:
    """One evidence item: a verdict per candidate scale (``+1`` plausible, ``-1`` implausible,
    ``0`` no opinion), each with its weight, and what was compared."""

    name: str
    verdicts: dict  # scale -> -1 | 0 | +1
    weights: dict  # scale -> int
    detail: str
    metrics: dict = field(default_factory=dict)

    @property
    def has_opinion(self) -> bool:
        """True when the item judged at least one scale."""
        return any(v != 0 for v in self.verdicts.values())

    def as_dict(self) -> dict:
        """Return as a plain dict (scales keyed ``"x0.001"``, ``"x1"``, ``"x1000"``)."""
        word = {1: "plausible", -1: "implausible", 0: "no opinion"}
        return {
            "name": self.name,
            "verdicts": {_LABELS[s]: word[v] for s, v in self.verdicts.items()},
            "weights": {_LABELS[s]: w for s, w in self.weights.items()},
            "detail": self.detail,
            "metrics": dict(self.metrics),
        }


@dataclass
class UnitScaleCheck:
    """The verdict on one meter's quantities (see the module docstring).

    ``scale`` is the most likely factor the quantities *as given* should be multiplied by
    (``1.0`` = right as billed, ``0.001`` = they read 1000x too high), ``None`` when nothing
    decides. ``status`` is ``plausible``, ``implausible``, ``uncertain`` or ``insufficient``;
    ``confidence`` is ``high``, ``medium`` or ``low``. ``steps`` lists ~1000x steps in the series
    and ``segments`` the scale judged for each part between them. ``override`` records an explicit
    ``scale_override`` that was applied before judging.
    """

    label: str
    fuel: str | None
    unit: str
    reading: str
    candidates: tuple
    evidence: list
    scale: float | None
    confidence: str
    status: str
    explanation: str
    steps: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    override: dict | None = None
    notes: list = field(default_factory=list)

    @property
    def implausible(self) -> bool:
        """True when the quantities as given are implausible (x1 ruled out, or a unit step)."""
        return self.status == "implausible"

    def as_dict(self) -> dict:
        """Return as a plain, JSON-friendly dict."""
        return {
            "label": self.label,
            "fuel": self.fuel,
            "unit": self.unit,
            "reading": self.reading,
            "candidates": [_LABELS[s] for s in self.candidates],
            "scale": self.scale,
            "status": self.status,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "evidence": [e.as_dict() for e in self.evidence],
            "steps": [dict(s) for s in self.steps],
            "segments": [dict(s) for s in self.segments],
            "override": None if self.override is None else dict(self.override),
            "notes": list(self.notes),
        }

    def finding(self, equip: str | None = None, *, rule: str = "unit_scale"):
        """This check as a :class:`~camber.rules.base.Finding`: severity ``warn`` when implausible,
        ``info`` when uncertain, ``ok`` otherwise; the whole check in ``metrics["unit_scale"]``."""
        from .rules.base import Finding

        sev = {"implausible": "warn", "uncertain": "info"}.get(self.status, "ok")
        return Finding(
            rule=rule,
            equip=equip or self.label,
            severity=sev,
            metrics={
                "unit_scale": self.as_dict(),
                "scale": self.scale,
                "status": self.status,
                "confidence": self.confidence,
            },
            summary=f"{equip or self.label}: {self.explanation}",
            caveats=list(self.notes),
        )


# --------------------------------------------------------------------------- units and fuels


def fuel_group(fuel: str | None, unit: str | None = None) -> str | None:
    """The price/EUI group of a fuel: ``electricity``, ``natural_gas``, ``district_steam``,
    ``district_hot_water``, ``district_chilled_water``, ``fuel_oil`` or ``propane`` (``None`` when
    unknown). ``fuel`` is a :mod:`camber.energy_factors` meter type or alias (``"steam"``,
    ``"chilledwater"`` ...); without one the unit may name it (kWh -> electricity, therm/cf ->
    natural gas, lb -> steam, ton-hour -> chilled water)."""
    from .energy_factors import resolve_meter_type

    mt = None
    if fuel is not None and str(fuel).strip():
        key = str(fuel).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        f = _FUEL_ALIASES.get(key, fuel)
        try:
            mt = resolve_meter_type(f, factor_set=_ES)
        except ValueError:
            return None
    elif unit is not None:
        mt = _UNIT_FUEL.get(_unit_key(unit) or "")
    if mt is None:
        return None
    if mt in _OILS:
        return "fuel_oil"
    if mt in (
        "electricity",
        "natural_gas",
        "district_steam",
        "district_hot_water",
        "district_chilled_water",
        "propane",
    ):
        return mt
    return None


def _unit_key(unit: str) -> str | None:
    from .energy_factors import resolve_unit
    from .energy_units import _norm

    k = _norm(unit)
    if k in _M_READINGS:
        return _M_READINGS[k][0]
    try:
        return resolve_unit(unit)[0]
    except ValueError:
        return None


def _meter_type_for(group: str | None) -> str | None:
    return {"fuel_oil": "fuel_oil_2"}.get(group or "", group)


def kbtu_per_unit(unit: str, fuel: str | None = None, *, heat_content=None, enthalpy=None) -> tuple:
    """``(kBtu per billed unit, reading, notes)`` for screening.

    Energy units convert exactly (:mod:`camber.energy_units`); a volume or mass uses an explicit
    ``heat_content`` / ``enthalpy`` when given, else the ENERGY STAR U.S. factor for the fuel
    (screening only: the 1000x question does not depend on a few percent of heat content). The
    ambiguous ``Mlb`` and ``MBtu`` are read with the US-utility M (a thousand), noted -- the check
    then tests x0.001 and x1000 of that reading anyway. ``ValueError`` for an unreadable unit.
    """
    from .energy_factors import factor_for
    from .energy_units import _norm, energy_factor, parse_unit

    notes: list = []
    text = str(unit)
    k = _norm(text)
    if k in _M_READINGS:
        text, what = _M_READINGS[k]
        notes.append(f"{unit!r} read as {what}; the check also tests x0.001 and x1000 of it")
    try:
        u = parse_unit(text, kind=("energy", "volume", "mass"))
    except ValueError:
        u = None
    if u is not None and u.kind == "energy":
        return energy_factor(text, "kBtu"), f"{unit} ({u.name})", notes
    if u is not None and u.kind == "volume" and heat_content is not None:
        return energy_factor(text, "kBtu", heat_content=heat_content), f"{unit} ({u.name})", notes
    if u is not None and u.kind == "mass" and enthalpy is not None:
        return energy_factor(text, "kBtu", enthalpy=enthalpy), f"{unit} ({u.name})", notes
    mt = _meter_type_for(fuel_group(fuel, text))
    if mt is None:
        raise ValueError(
            f"{unit!r} needs a fuel (meter type) or an explicit heat content / enthalpy to be "
            "screened"
        )
    c = factor_for(text, mt, factor_set=_ES, region="US")
    notes.append(f"{unit} converted for screening with {c.describe()}")
    return c.multiplier, f"{unit} ({c.unit} of {mt})", notes


# --------------------------------------------------------------------------- input frames


def _frame(bills, *, end_inclusive: bool) -> pd.DataFrame:
    """Bills as a frame with ``start``, ``end`` (exclusive), ``days``, ``quantity`` and the
    optional ``cost``, ``demand``, ``read_start``, ``read_end``, ``multiplier``, ``hdd``."""
    from .mandv.billing import BillingSeries

    if isinstance(bills, BillingSeries):
        f = bills.frame.rename(columns={"energy": "quantity"}).reset_index(drop=True)
    else:
        f = pd.DataFrame(bills).reset_index(drop=True).copy()
        if "quantity" not in f.columns and "energy" in f.columns:
            f = f.rename(columns={"energy": "quantity"})
        missing = {"start", "end", "quantity"} - set(f.columns)
        if missing:
            raise ValueError(
                f"bills need columns start, end and quantity; missing {sorted(missing)}"
            )
        f["start"] = pd.to_datetime(f["start"]).dt.normalize()
        f["end"] = pd.to_datetime(f["end"]).dt.normalize()
        if end_inclusive:
            f["end"] = f["end"] + pd.Timedelta(days=1)
    f = f.sort_values("start", kind="stable").reset_index(drop=True)
    f["days"] = ((f["end"] - f["start"]) / pd.Timedelta(days=1)).astype(float)
    if (f["days"] <= 0).any():
        raise ValueError("every bill must end after it starts")
    for c in ("quantity", "cost", "demand", "read_start", "read_end", "multiplier", "hdd"):
        f[c] = pd.to_numeric(f[c], errors="coerce") if c in f.columns else np.nan
    return f


def _hdd_per_bill(f: pd.DataFrame, oat: pd.Series, base_f: float = 65.0) -> np.ndarray:
    """Heating degree-days (degF-day, base ``base_f``) of each bill from an OAT series in degF;
    NaN for a bill whose days are under 80 % covered."""
    s = pd.Series(oat).dropna()
    if s.empty:
        return np.full(len(f), np.nan)
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    daily = pd.Series(s.to_numpy(float), index=idx).resample("D").mean().dropna()
    hdd = (base_f - daily).clip(lower=0.0)
    out = []
    for st, en, d in zip(f["start"], f["end"], f["days"]):
        sub = hdd[(hdd.index >= st) & (hdd.index < en)]
        out.append(float(sub.sum()) * d / len(sub) if len(sub) >= 0.8 * d else np.nan)
    return np.asarray(out, float)


# --------------------------------------------------------------------------- evidence items


def _ev(name, verdicts, weights, detail, **metrics) -> Evidence:
    return Evidence(
        name=name,
        verdicts={s: int(verdicts.get(s, 0)) for s in SCALES},
        weights={s: int(weights.get(s, 0)) for s in SCALES},
        detail=detail,
        metrics=metrics,
    )


def _band_verdict(x: float, plausible, lo: float, hi: float) -> int:
    if not math.isfinite(x):
        return 0
    if plausible[0] <= x <= plausible[1]:
        return 1
    if x < lo or x > hi:
        return -1
    return 0


def _ev_tariff(f: pd.DataFrame, tariff, tol_pct: float, kwh_unit: float) -> Evidence | None:
    """The tariff item; ``kwh_unit`` is kWh per billed unit (1 for kWh, 1000 for MWh)."""
    from .tariff import BillResult, validate_bill

    use = f[np.isfinite(f["cost"]) & np.isfinite(f["quantity"]) & (f["quantity"] >= 0)]
    if use.empty:
        return None
    actual = {str(r.start.date()): float(r.cost) for r in use.itertuples()}
    verdicts, weights, per = {}, {}, {}
    for s in SCALES:
        best = None
        variants = [1.0, s] if np.isfinite(use["demand"]).any() and s != 1.0 else [1.0]
        for dscale in variants:
            rows: list[dict] = [
                {
                    "period": str(r.start.date()),
                    "total": _bill_cost(
                        tariff, r.start, r.end, r.quantity * kwh_unit * s, r.demand * dscale
                    ),
                }
                for r in use.itertuples()
            ]
            total = float(sum(float(x["total"]) for x in rows))
            bv = validate_bill(
                BillResult(rows, 0.0, 0.0, 0.0, total, len(rows)), actual, tol_pct=tol_pct
            )
            if best is None or bv.mape < best[0].mape:
                best = (bv, dscale)
        assert best is not None
        bv, dscale = best
        share = bv.n_within / bv.n_checked if bv.n_checked else 0.0
        v = (
            1
            if (share >= 0.5 and bv.mape <= _TARIFF_OK_MAPE)
            else (-1 if bv.mape >= _TARIFF_BAD_MAPE else 0)
        )
        verdicts[s], weights[s] = v, 4
        per[_LABELS[s]] = {
            "mape_pct": bv.mape,
            "bills_within": bv.n_within,
            "bills": bv.n_checked,
            "demand_scaled": dscale != 1.0,
        }
    good = [_LABELS[s] for s in SCALES if verdicts[s] == 1]
    tname = getattr(tariff, "name", "") or "the tariff"
    detail = (
        f"recomputed invoice under {tname} matches the billed "
        f"$ at {', '.join(good) if good else 'no scale'} (tolerance {tol_pct:g} % per bill)"
    )
    return _ev("tariff", verdicts, weights, detail, by_scale=per, tolerance_pct=tol_pct)


def _bill_cost(tariff, start, end, kwh: float, kw: float) -> float:
    """One bill's charges under ``tariff``: the energy through its hours (flat load, with the
    billed peak kW placed in one weekday afternoon hour when given), the demand charge of the
    calendar month holding that peak, and one fixed monthly charge."""
    from .tariff import compute_bill

    idx = pd.date_range(start, end, freq="h", inclusive="left")
    n = len(idx)
    if n == 0 or not end > start:
        return float(tariff.fixed_monthly)
    load = np.full(n, kwh / n)
    pos = n // 2
    cand = np.flatnonzero((idx.dayofweek < 5) & (idx.hour == 15))
    if len(cand):
        pos = int(cand[len(cand) // 2])
    if math.isfinite(kw) and kw > kwh / n and n > 1:
        load[:] = max(kwh - kw, 0.0) / (n - 1)
        load[pos] = kw
    res = compute_bill(tariff, pd.Series(load, index=idx))
    peak_month = f"{idx[pos].year:04d}-{idx[pos].month:02d}"
    energy = sum(m["energy"] for m in res.months)
    demand = sum(m["demand"] for m in res.months if m["period"] == peak_month)
    return float(energy + demand + tariff.fixed_monthly)


def _price_reference(group, *, price_source, state, window, bands, eia_kw) -> tuple:
    """``(band dict, label, notes)``: the EIA state band when asked for and available, else the
    bundled band for the fuel (``(None, ...)`` when the set has none)."""
    notes: list = []
    ps = price_source
    if ps is not None and not isinstance(ps, str) and hasattr(ps, "usd_per_mmbtu"):
        avg = float(ps.usd_per_mmbtu)
        return _eia_band(avg), f"EIA {ps.state} commercial average ${avg:.2f}/MMBtu", notes
    if isinstance(ps, (int, float)) and not isinstance(ps, bool):
        return _eia_band(float(ps)), f"given reference ${float(ps):.2f}/MMBtu", notes
    if ps == "eia" and group in ("electricity", "natural_gas"):
        if not state:
            notes.append("price_source 'eia' needs a state; used the bundled price bands")
        else:
            from .interop.eia import fetch_state_price

            try:
                sp = fetch_state_price(group, state, window[0], window[1], **eia_kw)
                return (
                    _eia_band(sp.usd_per_mmbtu),
                    f"EIA {sp.state} commercial average ${sp.usd_per_mmbtu:.2f}/MMBtu "
                    f"({sp.native:g} {sp.native_unit}, {len(sp.periods)} months)",
                    notes,
                )
            except Exception as e:  # noqa: BLE001 -- offline, no key, network: fall back
                notes.append(f"EIA state price unavailable ({e}); used the bundled price bands")
    elif ps not in (None, "bands", "eia"):
        raise ValueError(
            f"price_source must be 'bands', 'eia', a number or a StatePrice, got {ps!r}"
        )
    from .energy_factors import get_reference_set

    rs = get_reference_set(bands)
    b = rs.band(group) if group else None
    if b is None:
        return None, "", notes
    return b, f"{bands} {group} band ${b['plausible'][0]:g}-{b['plausible'][1]:g}/MMBtu", notes


def _eia_band(avg: float) -> dict:
    return {
        "plausible": [avg / _EIA_PLAUSIBLE_FACTOR, avg * _EIA_PLAUSIBLE_FACTOR],
        "implausible_below": avg / _EIA_IMPLAUSIBLE_FACTOR,
        "implausible_above": avg * _EIA_IMPLAUSIBLE_FACTOR,
    }


def _ev_price(f, kbtu_unit, band, label) -> Evidence | None:
    ok = np.isfinite(f["cost"]) & np.isfinite(f["quantity"]) & (f["quantity"] > 0)
    if band is None or not ok.any():
        return None
    mmbtu = float((f.loc[ok, "quantity"] * kbtu_unit).sum()) / 1000.0
    usd = float(f.loc[ok, "cost"].sum())
    if mmbtu <= 0 or usd <= 0:
        return None
    p1 = usd / mmbtu
    verdicts, prices = {}, {}
    for s in SCALES:
        p = p1 / s
        prices[_LABELS[s]] = round(p, 6)
        verdicts[s] = _band_verdict(
            p, band["plausible"], band["implausible_below"], band["implausible_above"]
        )
    detail = f"implied price ${p1:,.4g}/MMBtu as billed, against {label}"
    return _ev(
        "price",
        verdicts,
        {s: 3 for s in SCALES},
        detail,
        usd_per_mmbtu=prices,
        reference=label,
        bills_priced=int(ok.sum()),
    )


def _eui_verdict(eui: float, group: str | None, median, pol: dict, *, total: bool) -> tuple:
    """``(verdict, weight, why)`` for one annual EUI (kBtu/ft2/yr) of a fuel group or the total."""
    g = (
        "total"
        if total
        else (group if group in ("electricity", "district_chilled_water") else "thermal")
    )
    hard = (pol.get("hard") or {}).get(g) or {}
    if eui > hard.get("implausible_above", math.inf):
        return -1, 3, f"above the hard bound {hard['implausible_above']:g}"
    if eui < hard.get("implausible_below", -math.inf):
        return -1, 3, f"below the hard bound {hard['implausible_below']:g}"
    if median:
        if total:
            t = pol["total"]
            if (
                eui > median * t["implausible_high_factor"]
                or eui < median / t["implausible_low_factor"]
            ):
                return -1, 2, "outside the property type's policy range"
            if median / t["plausible_low_factor"] <= eui <= median * t["plausible_high_factor"]:
                return 1, 2, "inside the property type's range"
            return 0, 2, ""
        one = pol["single_fuel"]
        if eui > median * one["implausible_high_factor"]:
            return -1, 2, "above the property type's policy range"
        if g == "electricity" and eui < median / pol["total"]["implausible_low_factor"]:
            return -1, 2, "below the property type's policy range"
        lo = (pol.get("generic_plausible") or {}).get(g, [0, math.inf])[0]
        if lo <= eui <= median * one["plausible_high_factor"]:
            return 1, 2, "inside the property type's range"
        return 0, 2, ""
    lo, hi = (pol.get("generic_plausible") or {}).get(g, [0, math.inf])
    if lo <= eui <= hi:
        return 1, 2, "inside the generic range"
    return 0, 2, ""


def _ev_eui(f, group, kbtu_unit, area_ft2, ptype, other_kbtu_yr, ref) -> tuple:
    """``(Evidence | None, notes)`` for the annualised EUI."""
    notes: list = []
    if area_ft2 is None or not (math.isfinite(area_ft2) and area_ft2 > 0):
        return None, notes
    ok = np.isfinite(f["quantity"])
    days = float(f.loc[ok, "days"].sum())
    if days < 60:
        notes.append(f"EUI not judged: only {days:.0f} days of quantities")
        return None, notes
    from .energy_factors import get_reference_set

    rs = get_reference_set(ref)
    pt = rs.property_type(ptype)
    if ptype and pt is None:
        notes.append(f"property type {ptype!r} is not in {ref}; generic EUI bounds used")
    median = pt.get("site_eui") if pt else None
    kbtu_yr = float(f.loc[ok, "quantity"].sum()) * kbtu_unit * 365.0 / days
    if not kbtu_yr > 0:
        notes.append("EUI not judged: the quantities sum to zero (no scale changes zero)")
        return None, notes
    pol = rs.policy
    verdicts, weights, euis, why = {}, {}, {}, {}
    has_total = other_kbtu_yr is not None and math.isfinite(other_kbtu_yr)
    tot = {}
    if has_total:
        for s in SCALES:
            e_tot = kbtu_yr * s / area_ft2 + other_kbtu_yr / area_ft2
            tot[s] = (e_tot, *_eui_verdict(e_tot, group, median, pol, total=True))
        if all(t[1] == -1 for t in tot.values()):
            # implausible at every scale of this meter: the other meters, not this one
            notes.append(
                "the building's total EUI is implausible whatever this meter's scale; "
                "the total was not used to judge this meter"
            )
            has_total = False
    for s in SCALES:
        e_fuel = kbtu_yr * s / area_ft2
        v, w, r = _eui_verdict(e_fuel, group, median, pol, total=False)
        rec = {"fuel": round(e_fuel, 4)}
        if has_total:
            e_tot, vt, wt, rt = tot[s]
            rec["total"] = round(e_tot, 4)
            if vt == -1 and (v != -1 or wt > w):
                v, w, r = vt, wt, f"total {rt}"
            elif v == 0 and vt == 1:
                v, w, r = 1, wt, f"total {rt}"
        verdicts[s], weights[s], euis[_LABELS[s]], why[_LABELS[s]] = v, w, rec, r
    ref_txt = f"{pt['name']} median {median:g}" if pt and median else "generic bounds"
    detail = (
        f"site EUI of this meter {kbtu_yr / area_ft2:,.4g} kBtu/ft2/yr as billed, against "
        f"{ref_txt} ({ref})"
    )
    ev = _ev(
        "eui",
        verdicts,
        weights,
        detail,
        eui_kbtu_ft2_yr=euis,
        why=why,
        property_type=pt["key"] if pt else None,
        median_site_eui=median,
        area_ft2=area_ft2,
    )
    return ev, notes


def _ev_weather(f, group, kbtu_unit, area_ft2, steam_kbtu_per_lb) -> Evidence | None:
    if group not in _HEATING or area_ft2 is None or not (area_ft2 > 0):
        return None
    ok = np.isfinite(f["quantity"])
    if not ok.any():
        return None
    verdicts: dict = {s: 0 for s in SCALES}
    metrics: dict = {}
    subs = []
    hok = ok & np.isfinite(f["hdd"])
    hdd = float(f.loc[hok, "hdd"].sum())
    if hdd >= _HDD_MIN_TOTAL:
        btu = float(f.loc[hok, "quantity"].sum()) * kbtu_unit * 1000.0
        per = btu / area_ft2 / hdd
        metrics["btu_per_ft2_hdd"] = {_LABELS[s]: round(per * s, 6) for s in SCALES}
        metrics["hdd65_total"] = round(hdd, 1)
        subs.append({s: _band_verdict(per * s, _HDD_PLAUSIBLE, *_HDD_IMPLAUSIBLE) for s in SCALES})
    rate = f.loc[ok, "quantity"] * kbtu_unit * 1000.0 / (f.loc[ok, "days"] * 24.0) / area_ft2
    peak = float(rate.max())
    metrics["peak_bill_btu_h_ft2"] = {_LABELS[s]: round(peak * s, 6) for s in SCALES}
    if group == "district_steam" and steam_kbtu_per_lb:
        metrics["peak_bill_lb_h"] = {
            _LABELS[s]: round(peak * s * area_ft2 / (steam_kbtu_per_lb * 1000.0), 3) for s in SCALES
        }
    subs.append(
        {
            s: (1 if peak * s <= _PEAK_PLAUSIBLE else (-1 if peak * s > _PEAK_IMPLAUSIBLE else 0))
            for s in SCALES
        }
    )
    for s in SCALES:
        vs = [d[s] for d in subs]
        verdicts[s] = -1 if -1 in vs else (1 if all(v == 1 for v in vs) else 0)
    parts = []
    if "btu_per_ft2_hdd" in metrics:
        parts.append(f"{metrics['btu_per_ft2_hdd']['x1']:,.4g} Btu/ft2/HDD65")
    parts.append(f"peak-bill average {peak:,.4g} Btu/h/ft2")
    if "peak_bill_lb_h" in metrics:
        parts.append(f"{metrics['peak_bill_lb_h']['x1']:,.4g} lb/h")
    detail = "heating intensity as billed: " + ", ".join(parts)
    return _ev("weather_intensity", verdicts, {s: 2 for s in SCALES}, detail, **metrics)


def _ev_load_factor(f, group, kwh_unit: float) -> Evidence | None:
    if group != "electricity":
        return None
    ok = np.isfinite(f["quantity"]) & np.isfinite(f["demand"]) & (f["demand"] > 0)
    if not ok.any():
        return None
    kwh = f.loc[ok, "quantity"] * kwh_unit
    lf = (kwh / (f.loc[ok, "demand"] * f.loc[ok, "days"] * 24.0)).median()
    lf = float(lf)
    verdicts = {s: _band_verdict(lf * s, _LF_PLAUSIBLE, *_LF_IMPLAUSIBLE) for s in SCALES}
    return _ev(
        "load_factor",
        verdicts,
        {s: 2 for s in SCALES},
        f"median load factor {lf:.4g} as billed (kWh / (billed kW x hours))",
        load_factor={_LABELS[s]: round(lf * s, 6) for s in SCALES},
    )


def _ev_meter_reads(f) -> Evidence | None:
    mult = f["multiplier"].where(np.isfinite(f["multiplier"]), 1.0)
    delta = (f["read_end"] - f["read_start"]) * mult
    ok = np.isfinite(delta) & (delta > 0) & np.isfinite(f["quantity"]) & (f["quantity"] > 0)
    if not ok.any():
        return None
    ratio = (f.loc[ok, "quantity"] / delta[ok]).to_numpy(float)
    verdicts, err = {}, {}
    for s in SCALES:
        e = float(np.median(np.abs(ratio * s - 1.0)))
        lg = float(np.median(np.abs(np.log10(ratio * s))))
        err[_LABELS[s]] = round(e, 6)
        verdicts[s] = 1 if e <= 0.05 else (-1 if lg >= 1.0 else 0)
    return _ev(
        "meter_reads",
        verdicts,
        {s: 2 for s in SCALES},
        f"billed quantity / ((end read - start read) x multiplier) = {float(np.median(ratio)):.4g} "
        f"(median of {int(ok.sum())} bills)",
        median_relative_error=err,
    )


def _steps(f: pd.DataFrame, group: str | None) -> tuple:
    """``(steps, notes)``: ~1000x steps in the per-day quantity.

    A **bill-to-bill** candidate needs both sides steady (at least two bills within 10x of each
    other, not a repeated placeholder value, one transition bill allowed) and ~1000x apart. It
    counts when the bills after it are also ~1000x their year-earlier bills (at least three
    matched bills, 60 % of them within the step band, same direction); when there is no year
    before it, it counts only for electricity -- a heating or chilled-water meter drops 1000x
    every season. A **year-over-year** step is the start of such a run on its own.
    """
    v = (f["quantity"] / f["days"]).to_numpy(float)
    good = np.isfinite(v) & (v > 0)
    n = len(v)
    starts = f["start"].to_numpy()
    yoy = np.full(n, np.nan)
    for j in range(n):
        if not good[j]:
            continue
        target = starts[j] - np.timedelta64(365, "D")
        k = int(np.argmin(np.abs(starts - target)))
        if k < j and abs((starts[k] - target) / np.timedelta64(1, "D")) <= 20 and good[k]:
            yoy[j] = math.log10(v[j] / v[k])

    def in_band(x: float, sign: float) -> bool:
        return bool(np.isfinite(x) and np.sign(x) == sign and _STEP_LO <= abs(x) <= _STEP_HI)

    def confirmed(i: int, sign: float):
        w = yoy[i : i + 12]
        w = w[np.isfinite(w)]
        if len(w) < 3:
            return None
        return sum(in_band(x, sign) for x in w) >= max(3, 0.6 * len(w))

    cands: list = []
    notes: list = []
    for i in range(1, n):
        for gap in (0, 1):
            pre = v[max(0, i - 3) : i]
            post = v[i + gap : i + gap + 3]
            pre, post = pre[np.isfinite(pre) & (pre > 0)], post[np.isfinite(post) & (post > 0)]
            if len(pre) < 2 or len(post) < 2:
                continue
            if pre.max() / pre.min() > 10 or post.max() / post.min() > 10:
                continue
            if pre.max() / pre.min() < _FLAT or post.max() / post.min() < _FLAT:
                continue  # identical bills: a placeholder or stuck value (an outage), not a unit
            r = math.log10(float(np.median(post)) / float(np.median(pre)))
            sep = post.min() / pre.max() if r > 0 else pre.min() / post.max()
            if not (_STEP_LO <= abs(r) <= _STEP_HI and sep >= 100):
                continue
            ok = confirmed(i + gap, float(np.sign(r)))
            if ok:
                cands.append(
                    (abs(abs(r) - 3.0), i + gap, r, "bill to bill, confirmed year over year")
                )
            elif ok is None and group == "electricity":
                cands.append((abs(abs(r) - 3.0), i + gap, r, "bill to bill"))
            elif ok is None:
                day = pd.Timestamp(starts[i + gap]).date()
                notes.append(
                    f"a ~{10 ** abs(r):,.0f}x change at the bill starting {day} was not flagged: "
                    "a seasonal meter can drop that much, and there is no year before it to "
                    "compare with"
                )
            break
    for j in range(n):
        if not np.isfinite(yoy[j]):
            continue
        sign = float(np.sign(yoy[j]))
        prev = yoy[:j][np.isfinite(yoy[:j])]
        if not in_band(yoy[j], sign) or (len(prev) and in_band(prev[-1], sign)):
            continue
        run = v[j : j + 3][good[j : j + 3]]
        if len(run) >= 2 and run.max() / run.min() < _FLAT:
            continue  # a repeated placeholder value (an outage), not a reading
        if confirmed(j, sign) and not any(abs(c[1] - j) <= 1 for c in cands):
            cands.append((abs(abs(yoy[j]) - 3.0), j, yoy[j], "year over year"))
    cands.sort()
    out: list = []
    for _, i, r, how in cands:
        if all(abs(i - o["bill"]) >= 3 for o in out):
            out.append(
                {
                    "bill": int(i),
                    "start": str(pd.Timestamp(f["start"].iloc[i]).date()),
                    "ratio": round(10.0 ** abs(r), 1),
                    "direction": "up" if r > 0 else "down",
                    "found_by": how,
                }
            )
    return sorted(out, key=lambda o: o["bill"]), notes


# --------------------------------------------------------------------------- combination


def _combine(evidence: list) -> tuple:
    """``(scale, status, confidence, ruled_out)`` by the module's documented rule."""
    ops = [e for e in evidence if e.has_opinion]
    if not ops:
        return None, "insufficient", "low", set()
    ruled, net = set(), {}
    for s in SCALES:
        against = [e.weights[s] for e in ops if e.verdicts[s] == -1]
        support = [e.weights[s] for e in ops if e.verdicts[s] == 1]
        net[s] = sum(support) - sum(against)
        heavy_against = max(against, default=0)
        if (heavy_against >= 3 or sum(against) >= 4) and heavy_against > max(support, default=0):
            ruled.add(s)
    alive = [s for s in SCALES if s not in ruled]
    if not alive:
        return None, "implausible", "low", ruled
    best = max(net[s] for s in alive)
    top = [s for s in alive if net[s] == best]
    scale = 1.0 if 1.0 in top else top[0]
    others_out = all(s in ruled for s in SCALES if s != scale)
    tariff_decides = any(
        e.name == "tariff"
        and e.verdicts[scale] == 1
        and all(e.verdicts[s] == -1 for s in SCALES if s != scale)
        for e in ops
    )
    backers = sum(1 for e in ops if e.verdicts[scale] == 1 and e.weights[scale] >= 2)
    if others_out and (tariff_decides or backers >= 2):
        conf = "high"
    elif others_out:
        conf = "medium"
    else:
        conf = "low"
    if 1.0 in ruled:
        status = "implausible"
    elif scale != 1.0:
        status = "uncertain"
    else:
        status = "plausible"
    return scale, status, conf, ruled


def _explain(status, scale, conf, evidence, ruled, unit, steps, segments, override) -> str:
    lead = ""
    if override is not None:
        lead = (
            f"quantities multiplied by {override['factor']:g} (scale_override: "
            f"{override['reason']}); "
        )
    if steps:
        where = ", ".join(
            f"bill starting {s['start']} ({s['ratio']:g}x {s['direction']})" for s in steps
        )
        segs = "; ".join(
            f"{g['start']}..{g['end']}: {g['status']}"
            + (f" (most likely {_LABELS.get(g['scale'], g['scale'])})" if g.get("scale") else "")
            for g in segments
        )
        return (
            f"{lead}a ~1000x step in the {unit} quantities at {where}: the unit changed on the "
            "bill, a bill was mis-keyed, or the meter stopped reading. Segments: "
            f"{segs}. Not corrected: fix the data, or split the series and set a "
            "bills.scale_override on the part that is wrong."
        )
    against = sorted({e.name for e in evidence if e.verdicts.get(1.0) == -1})
    backing = sorted({e.name for e in evidence if scale is not None and e.verdicts.get(scale) == 1})
    if status == "insufficient":
        return (
            f"{lead}{unit} quantities: no evidence could judge the scale "
            "(give cost, area or a tariff)"
        )
    if status == "plausible":
        return (
            f"{lead}{unit} quantities are plausible as given (x1; confidence {conf}"
            + (f"; supported by {', '.join(backing)}" if backing else "")
            + ")"
        )
    area_only = against and set(against) <= {"eui", "weather_intensity"}
    tail = (
        " Every item against x1 depends on the floor area: check the area as well."
        if area_only
        else ""
    )
    if status == "uncertain":
        return (
            f"{lead}{unit} quantities: {_LABELS[scale]} scores higher than x1, but x1 is not ruled "
            f"out (confidence {conf}); review the bill's units.{tail}"
        )
    likely = f"most likely {_LABELS[scale]}" if scale is not None else "no scale fits all evidence"
    return (
        f"{lead}{unit} quantities are implausible as given: {likely} (confidence {conf}); x1 ruled "
        f"out by {', '.join(against)}"
        + (f", {_LABELS[scale]} supported by {', '.join(backing)}" if backing and scale else "")
        + ". Not corrected: check the bill's unit (a 'M' read as million where it means thousand, "
        "or the reverse) and set bills.scale_override with a reason if it is wrong." + tail
    )


# --------------------------------------------------------------------------- entry points


def parse_scale_override(spec) -> dict | None:
    """A validated ``{"factor": f, "reason": "..."}`` (``None`` passes through). The factor is a
    positive number (usually 0.001 or 1000) and the reason a non-empty string: a correction is
    never silent."""
    if spec is None:
        return None
    if not isinstance(spec, Mapping):
        raise ValueError('scale_override must be {"factor": 0.001, "reason": "..."}')
    extra = set(spec) - {"factor", "reason"}
    if extra:
        raise ValueError(f"scale_override: unknown key(s) {sorted(extra)}")
    try:
        fac = float(spec.get("factor"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("scale_override.factor must be a number (e.g. 0.001)") from None
    if not (math.isfinite(fac) and fac > 0):
        raise ValueError("scale_override.factor must be a positive number")
    reason = spec.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("scale_override.reason is required: say why the bill's scale is wrong")
    return {"factor": fac, "reason": reason.strip()}


def _evaluate(
    f,
    *,
    group,
    kbtu_unit,
    area_ft2,
    ptype,
    other_kbtu_yr,
    tariff,
    price_band,
    price_label,
    eui_ref,
    steam_kbtu_per_lb,
    tol_pct,
) -> tuple:
    notes: list = []
    ev: list = []
    if tariff is not None and group == "electricity":
        t = _ev_tariff(f, tariff, tol_pct, kbtu_unit / KBTU_PER_KWH)
        if t is not None:
            ev.append(t)
    p = _ev_price(f, kbtu_unit, price_band, price_label)
    if p is not None:
        ev.append(p)
    e, n = _ev_eui(f, group, kbtu_unit, area_ft2, ptype, other_kbtu_yr, eui_ref)
    notes += n
    if e is not None:
        ev.append(e)
    for item in (
        _ev_weather(f, group, kbtu_unit, area_ft2, steam_kbtu_per_lb),
        _ev_load_factor(f, group, kbtu_unit / KBTU_PER_KWH),
        _ev_meter_reads(f),
    ):
        if item is not None:
            ev.append(item)
    return ev, notes


def check_bills(
    bills,
    *,
    unit: str,
    fuel: str | None = None,
    area: float | None = None,
    area_unit: str = "ft2",
    property_type: str | None = None,
    oat: pd.Series | None = None,
    base_f: float = 65.0,
    tariff=None,
    urdb: Mapping | None = None,
    tariff_tol_pct: float = _TARIFF_TOL_PCT,
    price_source=None,
    state: str | None = None,
    eia_transport=None,
    eia_api_key: str | None = None,
    eia_cache_dir: str | None = None,
    eia_offline: bool = False,
    heat_content=None,
    enthalpy=None,
    other_site_kbtu_per_year: float | None = None,
    scale_override=None,
    label: str = "",
    end_inclusive: bool = False,
    price_bands: str = DEFAULT_PRICE_BANDS,
    eui_reference: str = DEFAULT_EUI_REFERENCE,
) -> UnitScaleCheck:
    """Judge one meter's billed quantities at x0.001, x1 and x1000 (see the module docstring).

    ``bills`` is a :class:`~camber.mandv.billing.BillingSeries` or a frame with ``start``,
    ``end`` (exclusive unless ``end_inclusive``) and ``quantity`` (or ``energy``), and optionally
    ``cost`` (the invoiced $), ``demand`` (billed kW), ``read_start`` / ``read_end`` /
    ``multiplier`` (meter reads) and ``hdd`` (degF-days; else computed from ``oat`` in degF).
    ``unit`` is the billed unit; ``fuel`` a :mod:`camber.energy_factors` meter type (inferred from
    the unit when omitted). ``area`` (in ``area_unit``) and ``property_type`` enable the EUI and
    weather items; ``other_site_kbtu_per_year`` (the building's other meters, kBtu/yr) adds the
    total-EUI check.

    Price evidence: ``tariff`` (a :class:`~camber.tariff.Tariff`) or ``urdb`` (a URDB rate dict,
    e.g. from :func:`camber.interop.openei.fetch_urdb_rate`) recomputes each electricity bill;
    ``price_source`` is ``None`` / ``"bands"`` (the bundled ``price_bands``), ``"eia"`` (the
    state's EIA commercial average, opt-in: needs ``state`` and an ``EIA_API_KEY`` or
    ``eia_transport``; falls back to the bands when unavailable), a
    :class:`~camber.interop.eia.StatePrice`, or a reference $/MMBtu.

    ``scale_override`` (``{"factor": 0.001, "reason": "..."}``) multiplies the quantities before
    judging and is recorded; nothing is ever corrected without it.
    """
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("unit is required: the unit the quantities are billed in")
    ov = parse_scale_override(scale_override)
    f = _frame(bills, end_inclusive=end_inclusive)
    if ov is not None:
        f["quantity"] = f["quantity"] * ov["factor"]
    if oat is not None and not np.isfinite(f["hdd"]).any():
        f["hdd"] = _hdd_per_bill(f, oat, base_f)
    group = fuel_group(fuel, unit)
    kbtu_unit, reading, notes = kbtu_per_unit(
        unit, fuel if fuel else group, heat_content=heat_content, enthalpy=enthalpy
    )
    if fuel is not None and group is None:
        notes.append(f"fuel {fuel!r} has no price band or EUI group; only generic checks apply")
    area_ft2 = None
    if area is not None:
        from .energy_units import _area

        area_ft2 = float(area) * (1.0 if _area(area_unit) == "ft2" else 1.0 / 0.3048**2)
    if tariff is None and urdb is not None:
        from .interop.openei import tariff_from_urdb

        tariff = tariff_from_urdb(dict(urdb))
    window = (f["start"].min(), f["end"].max() - pd.Timedelta(days=1))
    eia_kw = {
        "transport": eia_transport,
        "api_key": eia_api_key,
        "cache_dir": eia_cache_dir,
        "offline": eia_offline,
    }
    band, band_label, n = _price_reference(
        group,
        price_source=price_source,
        state=state,
        window=window,
        bands=price_bands,
        eia_kw=eia_kw,
    )
    notes += n
    steam_lb = None
    if group == "district_steam":
        steam_lb = kbtu_per_unit("lb", "district_steam")[0]
    kw = {
        "group": group,
        "kbtu_unit": kbtu_unit,
        "area_ft2": area_ft2,
        "ptype": property_type,
        "other_kbtu_yr": other_site_kbtu_per_year,
        "tariff": tariff,
        "price_band": band,
        "price_label": band_label,
        "eui_ref": eui_reference,
        "steam_kbtu_per_lb": steam_lb,
        "tol_pct": tariff_tol_pct,
    }
    steps, step_notes = _steps(f, group)
    notes += step_notes
    segments: list = []
    if steps:
        cuts = [0] + [s["bill"] for s in steps] + [len(f)]
        best = None
        for a, b in zip(cuts[:-1], cuts[1:]):
            seg = f.iloc[a:b]
            ev_s, n_s = _evaluate(seg, **kw)
            sc, st, cf, _ = _combine(ev_s)
            segments.append(
                {
                    "start": str(pd.Timestamp(seg["start"].iloc[0]).date()),
                    "end": str((pd.Timestamp(seg["end"].iloc[-1]) - pd.Timedelta(days=1)).date()),
                    "bills": int(b - a),
                    "scale": sc,
                    "status": st,
                    "confidence": cf,
                }
            )
            if best is None or (b - a) > best[0]:
                best = (b - a, ev_s, n_s, sc, cf)
        assert best is not None
        _, evidence, n_best, scale, conf = best
        notes += n_best
        status = "implausible"
        cont = _ev(
            "continuity",
            {},
            {},
            f"{len(steps)} step(s) of ~1000x in the series",
            steps=[dict(s) for s in steps],
        )
        evidence = [cont, *evidence]
        ruled: set = set()
    else:
        evidence, n2 = _evaluate(f, **kw)
        notes += n2
        scale, status, conf, ruled = _combine(evidence)
        evidence.append(_ev("continuity", {}, {}, "no ~1000x step in the series"))
    expl = _explain(status, scale, conf, evidence, ruled, unit, steps, segments, ov)
    return UnitScaleCheck(
        label=label,
        fuel=group,
        unit=unit,
        reading=reading,
        candidates=SCALES,
        evidence=evidence,
        scale=scale,
        confidence=conf,
        status=status,
        explanation=expl,
        steps=steps,
        segments=segments,
        override=ov,
        notes=notes,
    )


def check_series(
    series: pd.Series,
    *,
    unit: str,
    fuel: str | None = None,
    min_days: float = 7.0,
    **kwargs,
) -> UnitScaleCheck:
    """:func:`check_bills` for an interval meter series of **energy per interval** (e.g. kWh in
    each hour, as BDG2's meters are): summed into calendar-month pseudo-bills, each ``days`` long
    by the hours it has data for (months with under ``min_days`` of data are dropped). Keywords
    are :func:`check_bills`'."""
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(s.to_numpy(float), index=idx).sort_index()
    if s.empty:
        raise ValueError("the meter series has no numeric values")
    from .timegrid import interval_hours

    step_h = float(np.median(interval_hours(s.index))) if len(s) > 1 else 1.0
    g = s.groupby(s.index.to_period("M"))
    rows = []
    for per, grp in g:
        days = len(grp) * step_h / 24.0
        if days < min_days:
            continue
        start = per.start_time.normalize()
        rows.append(
            {
                "start": start,
                "end": start + pd.Timedelta(days=math.floor(days)),
                "days": float(math.floor(days)),
                "quantity": float(grp.sum()),
            }
        )
    if not rows:
        raise ValueError(f"no month has {min_days:g} days of data")
    frame = pd.DataFrame(rows)
    oat = kwargs.pop("oat", None)
    if oat is not None:
        # HDD over each month's covered span: the pseudo-bill's days start at the month start
        frame["hdd"] = _hdd_per_bill(frame, oat, kwargs.get("base_f", 65.0))
    return check_bills(frame, unit=unit, fuel=fuel, **kwargs)


def check_eui(
    value: float,
    unit: str = "kBtu/ft2/yr",
    *,
    property_type: str | None = None,
    fuel: str | None = None,
    label: str = "site EUI",
    eui_reference: str = DEFAULT_EUI_REFERENCE,
) -> UnitScaleCheck:
    """Judge a stated annual EUI (the building total, or one ``fuel``'s) at x0.001, x1 and x1000
    against the EUI reference alone (a report benchmark's ``site_eui``, say). With only one item,
    x1 is ruled out only by a hard bound (weight 3) -- a 1000x error in any real EUI."""
    from .energy_factors import get_reference_set
    from .energy_units import eui_factor

    v = float(value) * eui_factor(unit, "kBtu/ft2/yr")
    rs = get_reference_set(eui_reference)
    pt = rs.property_type(property_type)
    median = pt.get("site_eui") if pt else None
    group = fuel_group(fuel) if fuel else None
    verdicts, weights, why = {}, {}, {}
    for s in SCALES:
        vv, w, r = _eui_verdict(v * s, group, median, rs.policy, total=fuel is None)
        verdicts[s], weights[s], why[_LABELS[s]] = vv, w, r
    ev = [
        _ev(
            "eui",
            verdicts,
            weights,
            f"stated EUI {v:,.4g} kBtu/ft2/yr against "
            + (f"{pt['name']} median {median:g}" if pt and median else "generic bounds"),
            eui_kbtu_ft2_yr={_LABELS[s]: round(v * s, 4) for s in SCALES},
            why=why,
        )
    ]
    scale, status, conf, ruled = _combine(ev)
    return UnitScaleCheck(
        label=label,
        fuel=group,
        unit=unit,
        reading=unit,
        candidates=SCALES,
        evidence=ev,
        scale=scale,
        confidence=conf,
        status=status,
        explanation=_explain(status, scale, conf, ev, ruled, f"{label} ({unit})", [], [], None),
    )
