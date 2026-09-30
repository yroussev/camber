"""ASHRAE 62.1 ventilation-rate verification and DCV checks.

`camber.iaq` reads CO₂ as a *proxy* for ventilation adequacy; this module does the explicit
**code-rate** check. Two analytics:

- **Ventilation Rate Procedure (VRP).** ASHRAE 62.1 sets a zone's required outdoor air as
  ``Vbz = Rp·Pz + Ra·Az`` (people term + area term), and the zone outdoor air ``Voz = Vbz / Ez``
  for a zone air-distribution effectiveness ``Ez``. :func:`assess_62_1` compares *measured*
  delivered OA against ``Voz`` and flags **under-ventilation** (a code/IAQ concern) or gross
  **over-ventilation** (a conditioning-energy penalty).
- **Demand-Controlled Ventilation (DCV).** DCV should *raise* OA when demand (CO₂ or an
  occupancy signal) rises. :func:`assess_dcv` compares OA at high vs low demand, judged only on
  occupied, non-economizing samples (:func:`economizer_active_mask`), and flags a **static** OA
  signal (DCV not functioning / fixed OA) and an **uncorrelated** one -- or declines
  (**insufficient**) when demand never gave the DCV anything to respond to. Given a CO₂ setpoint
  it also reports under-ventilation that persists while OA sits at its minimum, and given an OA
  floor, OA below it or held above it at low demand.

The 62.1 Table 6.1 rates below are public standard values included as defaults; callers may
override ``rp``/``ra``/``ez`` for any space. This verifies the *rate*; it is not a substitute
for a stamped 62.1 calculation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "OA_RATES_62_1",
    "DEFAULT_EZ",
    "oa_rates_for",
    "required_oa_cfm",
    "VrpResult",
    "assess_62_1",
    "DcvResult",
    "assess_dcv",
    "economizer_active_mask",
    "DEFAULT_DCV_ENGAGE_PPM",
    "DEFAULT_ECON_HIGH_LIMIT_F",
    # 0.92 (#17): system-level Ventilation Rate Procedure
    "DEFAULT_EZ_COOLING",
    "DEFAULT_EZ_HEATING",
    "VentZone",
    "SystemVrpRequirement",
    "SystemVrpResult",
    "simplified_ev",
    "system_outdoor_air",
    "estimate_oa_cfm",
    "assess_system_62_1",
    "zones_from_records",
    "load_vent_zones",
]

# ASHRAE 62.1 Table 6.1 minimum rates: space type -> (Rp cfm/person, Ra cfm/ft²).
# A practical subset of common space types (public standard values).
OA_RATES_62_1 = {
    "office": (5.0, 0.06),
    "open_office": (5.0, 0.06),
    "conference": (5.0, 0.06),
    "breakroom": (5.0, 0.12),
    "classroom": (10.0, 0.12),
    "lecture": (7.5, 0.06),
    "lobby": (5.0, 0.06),
    "corridor": (0.0, 0.06),
    "retail": (7.5, 0.12),
    "courtroom": (5.0, 0.06),
    "auditorium": (5.0, 0.06),
    "gym": (20.0, 0.18),
    "lab": (10.0, 0.18),
}

DEFAULT_EZ = 1.0


def oa_rates_for(space_type: str) -> tuple[float, float]:
    """``(Rp, Ra)`` for a 62.1 space type (case-insensitive). Raises if unknown."""
    key = str(space_type).strip().lower().replace(" ", "_")
    if key not in OA_RATES_62_1:
        raise KeyError(
            f"unknown space type {space_type!r}; pass rp/ra explicitly or use one "
            f"of: {', '.join(sorted(OA_RATES_62_1))}"
        )
    return OA_RATES_62_1[key]


def required_oa_cfm(
    area_sqft: float, population: float, *, rp: float, ra: float, ez: float = DEFAULT_EZ
) -> float:
    """ASHRAE 62.1 zone outdoor air ``Voz = (Rp·Pz + Ra·Az) / Ez`` (cfm)."""
    if ez <= 0:
        raise ValueError("ez (zone air-distribution effectiveness) must be > 0")
    vbz = rp * float(population) + ra * float(area_sqft)
    return vbz / ez


_AGG = {
    "median": np.nanmedian,
    "mean": np.nanmean,
    "min": np.nanmin,
    "p05": lambda a: np.nanpercentile(a, 5),
    "p95": lambda a: np.nanpercentile(a, 95),
}


@dataclass
class VrpResult:
    """Measured OA vs the 62.1 VRP requirement for one zone."""

    equip: str
    required_cfm: float
    measured_cfm: float  # aggregated delivered OA
    ratio: float  # measured / required
    status: str  # "under" | "adequate" | "over"
    deficit_cfm: float  # max(0, required - measured)
    rp: float
    ra: float
    ez: float
    n: int  # samples behind the aggregate (1 if scalar)

    def as_dict(self) -> dict:
        return asdict(self)


def assess_62_1(
    measured_oa_cfm,
    *,
    area_sqft: float,
    population: float,
    space_type: str | None = None,
    rp: float | None = None,
    ra: float | None = None,
    ez: float = DEFAULT_EZ,
    occupied_mask=None,
    aggregate: str = "median",
    under_tol: float = 0.9,
    over_factor: float = 1.5,
    equip: str = "",
) -> VrpResult:
    """Compare measured delivered OA to the 62.1 VRP requirement.

    ``measured_oa_cfm`` is a scalar or a time series; a Series is filtered by ``occupied_mask``
    (if given) and reduced by ``aggregate`` ("median"/"mean"/"min"/"p05"/"p95"). Rates come from
    ``space_type`` (62.1 table) unless ``rp``/``ra`` are given explicitly.

    Status: **under** when ``ratio < under_tol``, **over** when ``ratio > over_factor``, else
    **adequate**.
    """
    if rp is None or ra is None:
        if space_type is None:
            raise ValueError("provide space_type or explicit rp and ra")
        d_rp, d_ra = oa_rates_for(space_type)
        rp = d_rp if rp is None else rp
        ra = d_ra if ra is None else ra
    if aggregate not in _AGG:
        raise ValueError(f"aggregate must be one of {sorted(_AGG)}")

    if isinstance(measured_oa_cfm, pd.Series):
        s = measured_oa_cfm
        if occupied_mask is not None:
            s = s[occupied_mask.reindex(s.index, fill_value=False)]
        vals = s.to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        n = int(len(vals))
        reducer = _AGG[aggregate]
        measured = float(reducer(vals)) if n else float("nan")  # type: ignore[operator]  # reducer
    else:
        measured = float(measured_oa_cfm)
        n = 1

    required = required_oa_cfm(area_sqft, population, rp=rp, ra=ra, ez=ez)
    ratio = measured / required if required else float("nan")
    if not np.isfinite(ratio):
        status = "unknown"
    elif ratio < under_tol:
        status = "under"
    elif ratio > over_factor:
        status = "over"
    else:
        status = "adequate"
    return VrpResult(
        equip=equip,
        required_cfm=round(required, 1),
        measured_cfm=round(measured, 1) if np.isfinite(measured) else float("nan"),
        ratio=round(ratio, 3) if np.isfinite(ratio) else float("nan"),
        status=status,
        deficit_cfm=round(max(0.0, required - measured), 1)
        if np.isfinite(measured)
        else float("nan"),
        rp=rp,
        ra=ra,
        ez=ez,
        n=n,
    )


# --------------------------------------------------------------------------- DCV

#: CO₂ plausibility window (ppm) for a demand signal -- same guard as :mod:`camber.iaq`.
_CO2_PLAUSIBLE = (250.0, 5000.0)

#: Absolute CO₂ (ppm) by which a DCV reset should already be raising OA, when no setpoint is given.
DEFAULT_DCV_ENGAGE_PPM = 800.0

#: Economizer high-limit (°F) assumed when no economizer command is trended. The top of the
#: ASHRAE 90.1 fixed dry-bulb high-limit range, so it excludes *more* hours when unsure.
DEFAULT_ECON_HIGH_LIMIT_F = 75.0


def economizer_active_mask(
    index,
    *,
    econ_cmd=None,
    oat=None,
    heat_valve=None,
    high_limit_f: float = DEFAULT_ECON_HIGH_LIMIT_F,
    heat_active_pct: float = 5.0,
) -> tuple[pd.Series | None, str]:
    """Which samples may be economizing -- the periods a DCV judgement must exclude.

    An air handler's OA damper is driven by ``max(economizer, DCV minimum)``: while economizing,
    OA follows outdoor temperature, not CO₂, so those samples say nothing about DCV. Returns
    ``(mask, basis)`` where ``mask`` is ``True`` where the economizer is (possibly) active, from
    the best evidence available:

    1. ``econ_cmd`` -- the trended enable/command; **any** nonzero value counts as active, because
       an hourly mean of a binary command is fractional in an hour the economizer ran for only
       part of. Basis ``"econ_cmd"``.
    2. ``oat`` + ``heat_valve`` -- a sequenced SAT loop holds OA at minimum while heating, and the
       high limit locks the economizer out above ``high_limit_f``; any other sample *may* be
       economizing. Basis ``"oat_heat_valve"``.
    3. ``oat`` only -- only samples above the high limit are known non-economizing. Basis ``"oat"``.
    4. nothing -- ``(None, "none")``; the caller cannot exclude the economizer and must say so.

    A sample with a missing input counts as possibly active (excluded): the conservative direction.
    """
    idx = pd.DatetimeIndex(index)
    if econ_cmd is not None:
        cmd = econ_cmd.reindex(idx)
        return (cmd.isna() | (cmd > 0.0)).astype(bool), "econ_cmd"
    if oat is None:
        return None, "none"
    t = oat.reindex(idx)
    locked_out = t > high_limit_f
    if heat_valve is not None:
        heating = heat_valve.reindex(idx) > heat_active_pct
        return (~(locked_out | heating)).astype(bool), "oat_heat_valve"
    return (~locked_out).astype(bool), "oat"


@dataclass
class DcvResult:
    """Whether OA modulates with ventilation demand (DCV functioning).

    ``status`` is ``"functioning"`` | ``"static"`` | ``"uncorrelated"`` | ``"insufficient"``;
    ``reason`` says why an ``insufficient`` verdict was reached. Sub-checks that could not be
    evaluated are ``None``, never ``0`` (see the honesty convention in :mod:`camber.rules.base`).
    """

    equip: str
    n: int
    correlation: float  # Pearson OA vs demand -- a diagnostic only; not used for the verdict
    modulation: float  # robust (p_hi - p_lo) / p_hi of the OA signal, 0..1
    status: str  # "functioning" | "static" | "uncorrelated" | "insufficient"
    co2_breach_at_min_pct: float | None  # % samples CO₂>setpoint while OA at its min (if given)
    demand_lift: float | None = None  # demand with OA raised minus demand with OA at its floor
    reason: str | None = None  # why "insufficient"
    demand_span: float | None = None  # p90 - p10 of demand
    oa_low_demand: float | None = None  # median OA in the low-demand bin (diagnostic)
    oa_high_demand: float | None = None  # median OA in the high-demand bin (diagnostic)
    n_econ_excluded: int | None = None  # samples dropped as (possibly) economizing
    econ_excluded: bool | None = None  # None = no economizer evidence was supplied
    closed_pct: float | None = None  # % judged samples with OA closed (excluded from the verdict)
    demand_kind: str | None = None  # "co2" | "presence" | "count"
    raised_when_vacant_pct: float | None = None  # presence/count: % vacant samples with OA raised
    below_floor_pct: float | None = None  # % samples OA below the floor (needs oa_floor)
    excess_at_low_demand_pct: float | None = None  # % low-demand samples OA above the floor
    # 0.93 (#37): how the CO₂ lift was taken -- "hour_of_day" (within each hour, the verdict's
    # statistic when enough same-hour pairs exist) or "pooled" (across hours); the pooled lift is
    # kept alongside as a diagnostic. None on a verdict that never reached the lift.
    lift_basis: str | None = None
    demand_lift_pooled: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _dedup(s: pd.Series) -> pd.Series:
    """Keep the last sample per timestamp -- a duplicated index cannot be reindexed -- on a
    nanosecond index. Two regular indexes held at different resolutions (e.g. microseconds from a
    parquet file) can fail to align in pandas >= 2 and silently drop nearly every row."""
    if not s.index.is_unique:
        s = s[~s.index.duplicated(keep="last")]
    if isinstance(s.index, pd.DatetimeIndex) and s.index.unit != "ns":
        s = s.copy()
        s.index = s.index.as_unit("ns")
    return s


def _demand_kind(d: pd.Series) -> str:
    """Classify a demand signal: presence (all within 0..1, incl. an hourly mean of a binary
    point), an occupant count (non-negative, below any plausible CO₂), or CO₂ (ppm)."""
    v = d.dropna()
    if v.empty or (v.between(0.0, 1.0)).all():
        return "presence"
    if (v >= 0).all() and float(v.max()) < _CO2_PLAUSIBLE[0]:
        return "count"
    return "co2"


def _pct(mask) -> float:
    return round(100.0 * float(np.mean(mask)), 1) if len(mask) else 0.0


def _hour_of_day_lift(index, d, raised, at_floor, agg, *, min_pairs: int = 3) -> tuple:
    """Demand lift (raised minus floor) taken within each hour of day and averaged.

    Strata are (weekend?, hour): a weekday-only time clock is also a schedule. A stratum counts
    when it holds at least ``min_pairs`` raised *and* at-floor samples, weighted by the smaller
    of the two. Returns ``(lift, weight)``; ``(nan, 0.0)`` when no stratum counts.
    """
    slot = (index.dayofweek.to_numpy() >= 5) * 24 + index.hour.to_numpy()
    num = wt = 0.0
    for h in np.unique(slot):
        r, f = raised & (slot == h), at_floor & (slot == h)
        k = min(int(r.sum()), int(f.sum()))
        if k >= min_pairs:
            num += float(agg(d[r]) - agg(d[f])) * k
            wt += k
    return (num / wt if wt else float("nan")), wt


def assess_dcv(
    oa_signal: pd.Series,
    demand_signal: pd.Series,
    *,
    occupied_mask=None,
    min_corr: float | None = None,
    min_modulation: float = 0.1,
    co2_setpoint: float | None = None,
    equip: str = "",
    economizer_mask=None,
    dcv_engage_ppm: float | None = None,
    min_demand_span: float = 150.0,
    min_lift_ppm: float = 50.0,
    min_lift_occupancy: float = 0.2,
    modulation_pcts: tuple = (5, 95),
    oa_floor=None,
    floor_tol: float = 0.10,
    min_samples: int = 24,
    min_bin: int = 6,
    closed_frac: float = 0.02,
    demand_kind: str = "auto",
    min_lift_people: float = 1.0,
    stratify_hour: bool = True,
) -> DcvResult:
    """Verify DCV: is outdoor air raised when -- and only when -- ventilation demand is high?

    ``oa_signal`` is OA flow, OA fraction or OA-damper position. ``demand_signal`` is one of
    (``demand_kind="auto"`` detects which): zone **CO₂** in ppm; **presence**, any signal within
    0..1 -- a binary occupancy point, or its hourly mean; or an occupant **count**, non-negative
    and below any plausible CO₂. Only samples inside ``occupied_mask`` and outside
    ``economizer_mask`` are judged -- see :func:`economizer_active_mask` for why the economizer
    must be excluded.

    **The test.** A DCV controller maps demand to OA: a proportional reset raises OA as CO₂ climbs
    past its engage level; an integral loop raises OA only once CO₂ reaches its setpoint and holds
    it there. Either way, *the samples where OA is raised above its floor are the samples where
    demand is high*. So the verdict compares demand when OA is raised (above 25% of its robust
    range) with demand when OA sits at its floor (within 5%): ``demand_lift``, in ppm. Conditioning
    on OA rather than on CO₂ matters for integral control, which holds CO₂ just under setpoint with
    OA at its floor much of the time -- "OA when CO₂ is high" is then mostly the floor, while "CO₂
    when OA was raised" stays high. It is median-based, so a few outliers do not move it. Pearson
    correlation is kept as the ``correlation`` diagnostic only; it is a linear-fit statistic, and
    an integral loop weakens it (0.48-0.66 in :func:`camber.faultlab.dcv_sim`, against 0.92-0.99
    for a proportional reset) without breaking it.

    - **insufficient** -- too few samples, demand never varied, or never reached ``engage``
      (``co2_setpoint - 200``, else :data:`DEFAULT_DCV_ENGAGE_PPM`), or OA was raised too rarely
      to compare. ``reason`` says which. Not evidence either way.
    - **static** -- demand reached the DCV range and OA did not move
      (robust modulation < ``min_modulation``).
    - **functioning** -- ``demand_lift`` ≥ ``min_lift_ppm`` (CO₂), ``min_lift_occupancy``
      (presence fraction) or ``min_lift_people`` (count). For presence / count the lift is taken
      **within each hour of day** (weekdays and weekends apart) and averaged: occupancy follows
      the clock, and so does a time-clock valve or a thermal load, so only OA that is higher on
      busier days *at the same hour* is responding to occupancy. OA that is never both raised
      and at its floor within one hour of day reads **insufficient**
      (``reason="schedule_confounded"``). Since 0.93 (#37) the CO₂ lift is taken the same way
      (``stratify_hour``, on by default): a valve that follows a clock *and* CO₂ mixes the two
      in a pooled lift -- its clock-driven morning opening at low CO₂ drags the raised median down
      -- so only CO₂ that is higher when OA is raised *at the same hour* is DCV response. Where
      too few same-hour pairs exist the pooled lift decides, as before, and ``lift_basis`` says
      which was used (``demand_lift_pooled`` is always reported).
      ``raised_when_vacant_pct`` reports how often OA was raised with the space empty -- a DCV
      that is not holding its floor when vacant (it may also be doing thermal duty).
    - **uncorrelated** -- OA modulates, but not with demand.

    ``oa_floor`` (same units as ``oa_signal``; scalar or Series) is the lowest OA the DCV may
    reach -- for 62.1 dynamic reset, the area component ``Ra·Az`` (§6.2.7; section numbering
    varies by edition). With it, ``below_floor_pct`` and ``excess_at_low_demand_pct`` are
    reported; without it they are ``None``. ``below_floor_pct`` is measured over **all**
    occupied samples, before the economizer and closed-damper exclusions -- an economizer only
    raises OA, and a damper shut while occupied is the deepest below-floor case there is.
    ``co2_breach_at_min_pct`` is the share of **all** judged samples with CO₂ above
    ``co2_setpoint`` while OA is at its floor.

    Samples with OA effectively **closed** (at or below ``closed_frac`` of its 95th percentile)
    are excluded from the verdict and reported as ``closed_pct``: DCV never shuts OA fully while
    a space is occupied (the 62.1 area component keeps a floor above zero), so a closed damper
    inside the occupied window is warm-up, a fan transition or a fault -- and left in, an
    un-flagged morning warm-up closure makes a static DCV look like it responds.

    ``min_corr`` is deprecated and ignored.
    """
    if min_corr is not None:
        from ._deprecation import warn_deprecated

        warn_deprecated(
            "assess_dcv(min_corr=...)",
            since="0.82",
            remove_in="1.0",
            use="`min_lift_ppm` (the verdict no longer uses Pearson correlation)",
            stacklevel=3,
        )
    if demand_kind not in ("auto", "co2", "presence", "count"):
        raise ValueError(f"demand_kind must be auto/co2/presence/count, got {demand_kind!r}")
    df = pd.DataFrame({"oa": _dedup(oa_signal), "d": _dedup(demand_signal)}).dropna()
    if occupied_mask is not None:
        occ = _dedup(occupied_mask)
        df = df[occ.reindex(df.index, fill_value=False).astype(bool)]

    kind = demand_kind if demand_kind != "auto" else _demand_kind(df["d"])
    if kind == "co2":
        lo_ok, hi_ok = _CO2_PLAUSIBLE
        df = df[(df["d"] >= lo_ok) & (df["d"] <= hi_ok)]

    # The floor sub-checks run on EVERY occupied sample, before the economizer and closed-damper
    # exclusions: an economizer only ever raises OA, and a closed damper while occupied is the most
    # below-floor OA can be -- excluding either would hide exactly the under-ventilation the floor
    # exists to catch (OA held below the floor read as "not judged").
    below_floor = None
    if oa_floor is not None and len(df):
        fl_all = (
            _dedup(oa_floor).reindex(df.index).to_numpy(dtype=float)
            if isinstance(oa_floor, pd.Series)
            else np.full(len(df), float(oa_floor))
        )
        below_floor = _pct(df["oa"].to_numpy(dtype=float) < fl_all * (1.0 - floor_tol))

    n_econ = None
    econ_excluded = None
    if economizer_mask is not None:
        active = _dedup(economizer_mask).reindex(df.index).fillna(True).astype(bool)
        n_econ = int(active.sum())
        econ_excluded = True
        df = df[~active]

    closed_pct = None
    if len(df):
        ref = float(np.percentile(df["oa"].to_numpy(dtype=float), 95))
        closed = df["oa"] <= closed_frac * ref if ref > 0 else df["oa"] <= 0
        closed_pct = _pct(closed.to_numpy())
        df = df[~closed]
    n = int(len(df))

    def _result(status, **kw):
        base = dict(
            equip=equip,
            n=n,
            correlation=float("nan"),
            modulation=float("nan"),
            status=status,
            co2_breach_at_min_pct=None,
            n_econ_excluded=n_econ,
            econ_excluded=econ_excluded,
            closed_pct=closed_pct,
            below_floor_pct=below_floor,
            demand_kind=kind,
        )
        base.update(kw)
        return DcvResult(**base)

    if n < min_samples:
        return _result("insufficient", reason="too_few_samples")

    def _enough(m) -> bool:
        k = int(m.sum())
        return k >= min_bin and k >= 0.05 * n

    oa = df["oa"].to_numpy(dtype=float)
    d = df["d"].to_numpy(dtype=float)
    p_lo, p_hi = (float(np.percentile(oa, q)) for q in modulation_pcts)
    rng_oa = p_hi - p_lo
    modulation = float(np.clip(rng_oa / p_hi, 0.0, 1.0)) if p_hi > 0 else 0.0
    corr = float(np.corrcoef(oa, d)[0, 1]) if np.std(oa) > 0 and np.std(d) > 0 else float("nan")

    # demand bins (by demand) -- decide whether DCV was ever asked to respond
    if kind == "presence":
        high = d >= 0.5
        low = ~high
        span = float(high.any() and low.any())
        span_ok = span > 0
    elif kind == "count":
        hi_n = max(1.0, float(np.percentile(d, 75)))
        high = d >= hi_n
        low = (d <= float(np.percentile(d, 25))) & (d < hi_n)
        span = float(np.percentile(d, 90) - np.percentile(d, 10))
        span_ok = span >= min_lift_people
    else:
        if dcv_engage_ppm is not None:
            engage = float(dcv_engage_ppm)
        elif co2_setpoint is not None:
            engage = float(co2_setpoint) - 200.0
        else:
            engage = DEFAULT_DCV_ENGAGE_PPM
        high = d >= engage
        low = (d <= float(np.percentile(d, 25))) & (d < engage)
        span = float(np.percentile(d, 90) - np.percentile(d, 10))
        span_ok = span >= min_demand_span

    # OA bins (by OA) -- the verdict's conditioning variable
    at_floor = oa <= p_lo + 0.05 * rng_oa
    raised = oa >= p_lo + 0.25 * rng_oa

    floor = None
    if oa_floor is not None:
        if isinstance(oa_floor, pd.Series):
            floor = _dedup(oa_floor).reindex(df.index).to_numpy(dtype=float)
        else:
            floor = np.full(n, float(oa_floor))
    breach = None
    if co2_setpoint is not None and kind == "co2":
        at_min = oa <= floor * (1.0 + floor_tol) if floor is not None else at_floor
        breach = _pct((d > co2_setpoint) & at_min)
    excess = (
        _pct(oa[low] > floor[low] * (1.0 + floor_tol))
        if floor is not None and _enough(low)
        else None
    )
    # occupancy-driven DCV should hold OA at its floor when the space is empty (reported, not a
    # verdict: a supply damper can also be doing thermal duty)
    vacant = d <= 0.0 if kind in ("presence", "count") else np.zeros(n, dtype=bool)
    raised_vacant = _pct(raised[vacant]) if _enough(vacant) else None
    common: dict = dict(
        correlation=round(corr, 3) if np.isfinite(corr) else float("nan"),
        modulation=round(modulation, 3),
        co2_breach_at_min_pct=breach,
        demand_span=round(span, 1),
        excess_at_low_demand_pct=excess,
        raised_when_vacant_pct=raised_vacant,
        oa_high_demand=round(float(np.median(oa[high])), 1) if high.any() else None,
        oa_low_demand=round(float(np.median(oa[low])), 1) if low.any() else None,
    )

    if not span_ok:
        return _result("insufficient", reason="no_demand_variation", **common)
    if not _enough(high):
        return _result("insufficient", reason="demand_below_engage", **common)
    if modulation < min_modulation:
        return _result("static", **common)
    if not (_enough(raised) and _enough(at_floor)):
        return _result("insufficient", reason="oa_rarely_raised", **common)

    if kind in ("presence", "count"):
        # Occupancy follows the clock, and so do schedules and thermal loads -- a valve opened by
        # a time clock correlates with occupancy without responding to it. So the lift is taken
        # WITHIN each hour of day (weekdays and weekends apart) and averaged: only OA that is
        # higher on busier days at the same hour is responding to occupancy.
        agg = np.mean if kind == "presence" else np.median
        lift, wt = _hour_of_day_lift(df.index, d, raised, at_floor, agg)
        if wt < min_bin:
            return _result("insufficient", reason="schedule_confounded", **common)
        ok = lift >= (min_lift_occupancy if kind == "presence" else min_lift_people)
        common["lift_basis"] = "hour_of_day"
    else:
        pooled = float(np.median(d[raised]) - np.median(d[at_floor]))
        common["demand_lift_pooled"] = round(pooled, 1)
        # 0.93 (#37): within the hour of day where enough same-hour pairs exist
        hour_lift, wt = (
            _hour_of_day_lift(df.index, d, raised, at_floor, np.median)
            if stratify_hour
            else (float("nan"), 0.0)
        )
        if wt >= min_bin:
            lift, common["lift_basis"] = hour_lift, "hour_of_day"
        else:
            lift, common["lift_basis"] = pooled, "pooled"
        ok = lift >= min_lift_ppm
    common["demand_lift"] = round(lift, 3 if kind == "presence" else 1)
    return _result("functioning" if ok else "uncorrelated", **common)


# ============================================================================ 0.92 (#17)
# System-level Ventilation Rate Procedure (0.92). A multiple-zone recirculating system (one air
# handler serving several zones) must bring in Vot = Vou / Ev, not one zone's Voz.
#
# Sources (the standard's text is not quoted; the maintainer has no licensed 62.1-2022 copy):
#   * ASHRAE 62.1-2016 Addendum f, published free by ASHRAE: the uncorrected outdoor air intake
#     Vou = D * sum(Rp*Pz) + sum(Ra*Az) with occupant diversity D = Ps / sum(Pz), and the
#     simplified system ventilation efficiency Ev = 0.88*D + 0.22 for D < 0.60, else 0.75
#     (§6.2.5 of the addendum; carried into 62.1-2019, where it is numbered §6.2.4 -- the 2022
#     numbering is unverified here).
#   * The addendum's condition for using the simplified Ev on a VAV system -- each zone's minimum
#     primary airflow at least 1.5 x its Voz -- as described in public secondary sources (design
#     guides and ASHRAE Journal columns on Addendum f). Treat the exact wording as unverified.
#   * The multiple-zone calculation of the standard's normative appendix (Zpz = Voz / Vpz,
#     Xs = Vou / Vps, Evz = 1 + Xs - Zpz for a single-supply system without secondary
#     recirculation, Ev = min Evz), as given in the pre-2016 editions' Ev procedure and public
#     secondary sources. Section and table numbers are unverified for the 2019/2022 editions.
#   * Zone air-distribution effectiveness: 1.0 for ceiling supply of cool air, 0.8 for ceiling
#     supply of warm air (15 F or more above space temperature) with ceiling return -- the Ez
#     table's rows, from public secondary sources; unverified for 2022. Other configurations
#     (floor supply, displacement) have other values: set them per zone.

#: Zone air-distribution effectiveness defaults (see above): cooling-mode ceiling supply, and the
#: heating-mode default the maintainer approved (warm-air ceiling supply, the conservative row).
DEFAULT_EZ_COOLING = 1.0
DEFAULT_EZ_HEATING = 0.8

#: Occupant diversity below which the simplified Ev falls with D (62.1-2016 Addendum f).
_EV_D_BREAK = 0.60

_SYSTEM_TYPES = ("multiple_zone", "single_zone", "100pct_oa")
_METHODS = ("simplified", "appendix")


@dataclass
class VentZone:
    """One zone's 62.1 VRP inputs.

    ``population`` is the zone's design population Pz; ``area_sqft`` its floor area Az. Rates come
    from ``space_type`` (:data:`OA_RATES_62_1`) unless ``rp`` / ``ra`` are given. ``ez_cooling`` /
    ``ez_heating`` override the defaults. ``vpz_min_cfm`` is the zone's minimum primary airflow
    (for the simplified method's 1.5 x Voz check); ``vpz_cfm`` the primary airflow at the
    condition the appendix method analyses (default: ``vpz_min_cfm``, the critical condition of a
    VAV zone). ``system`` names the air handler serving it, when declared. ``area_assumed`` /
    ``population_assumed`` mark inputs that are stated assumptions rather than design data --
    every result built on one says so and is capped at ``warn``.
    """

    zone: str
    area_sqft: float | None = None
    population: float | None = None
    space_type: str | None = None
    rp: float | None = None
    ra: float | None = None
    ez_cooling: float | None = None
    ez_heating: float | None = None
    vpz_min_cfm: float | None = None
    vpz_cfm: float | None = None
    system: str | None = None
    area_assumed: bool = False
    population_assumed: bool = False
    note: str = ""

    def rates(self) -> tuple[float, float] | None:
        """``(Rp, Ra)``, or ``None`` when neither the rates nor a known space type are given."""
        if self.rp is not None and self.ra is not None:
            return float(self.rp), float(self.ra)
        if self.space_type is None:
            return None
        try:
            d_rp, d_ra = oa_rates_for(self.space_type)
        except KeyError:
            return None
        return (
            float(d_rp if self.rp is None else self.rp),
            float(d_ra if self.ra is None else self.ra),
        )

    def missing(self) -> list:
        """Which inputs the VRP needs and this zone lacks."""
        out = []
        if self.area_sqft is None or not np.isfinite(float(self.area_sqft)):
            out.append("area_sqft")
        if self.population is None or not np.isfinite(float(self.population)):
            out.append("population")
        if self.rates() is None:
            out.append("rp/ra or a known space_type")
        return out


def simplified_ev(d: float) -> float:
    """Simplified system ventilation efficiency: ``0.88*D + 0.22`` for ``D < 0.60``, else ``0.75``
    (62.1-2016 Addendum f)."""
    d = float(d)
    if not 0.0 < d <= 1.0:
        raise ValueError(f"occupant diversity D must be in (0, 1], got {d}")
    return 0.88 * d + 0.22 if d < _EV_D_BREAK else 0.75


@dataclass
class SystemVrpRequirement:
    """The 62.1 VRP outdoor-air requirement of one system, per operating mode.

    ``vot_cooling_cfm`` / ``vot_heating_cfm`` differ only where the zone air-distribution
    effectiveness enters (single-zone and 100 % OA systems, the appendix method, the Vpz-min
    check); the simplified multiple-zone Vot does not depend on Ez. ``declined`` names why no
    requirement could be computed (then the numbers are None).
    """

    system: str
    system_type: str
    method: str | None
    n_zones: int
    sum_pz: float | None = None
    ps: float | None = None
    d: float | None = None
    vou_cfm: float | None = None
    ev_cooling: float | None = None
    ev_heating: float | None = None
    vot_cooling_cfm: float | None = None
    vot_heating_cfm: float | None = None
    zones: list = field(default_factory=list)  # per-zone dicts (Vbz, Voz by mode, Zpz, Evz)
    vpz_min_short: list = field(default_factory=list)  # zones with Vpz-min < 1.5 * Voz
    assumed: list = field(default_factory=list)  # "<zone>: area" / "<zone>: population"
    caveats: list = field(default_factory=list)
    declined: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _voz(z: VentZone, ez: float) -> tuple[float, float]:
    rp, ra = z.rates()  # type: ignore[misc]  # callers check missing() first
    vbz = rp * float(z.population) + ra * float(z.area_sqft)  # type: ignore[arg-type]
    return vbz, vbz / ez


def system_outdoor_air(
    zones,
    *,
    system: str = "",
    system_type: str = "multiple_zone",
    method: str = "simplified",
    ps: float | None = None,
    d: float | None = None,
    vps_cfm: float | None = None,
    ez_cooling: float = DEFAULT_EZ_COOLING,
    ez_heating: float = DEFAULT_EZ_HEATING,
) -> SystemVrpRequirement:
    """The 62.1 VRP outdoor-air intake of one system (cfm), per mode.

    ``zones`` is a list of :class:`VentZone`. ``system_type``:

    * ``"multiple_zone"`` (default) -- recirculating, one air handler serving several zones:
      ``Vot = Vou / Ev`` with ``Vou = D*sum(Rp*Pz) + sum(Ra*Az)``. ``D = Ps / sum(Pz)`` from the
      system population ``ps`` (or ``d`` directly); **without either, D = 1** (no diversity) --
      the largest Vou and, with the simplified method, Ev = 0.75: a conservative requirement that
      can over-state it, said in a caveat. ``method="simplified"`` (default) takes
      :func:`simplified_ev`, and checks each zone's ``vpz_min_cfm`` against ``1.5 * Voz`` where
      given (a shortfall is listed in ``vpz_min_short`` with a caveat: the simplified Ev may
      over-state the system's efficiency there). ``method="appendix"`` computes
      ``Evz = 1 + Xs - Zpz`` per zone (``Zpz = Voz / Vpz``, ``Xs = Vou / Vps``; single supply, no
      secondary recirculation) and ``Ev = min Evz``; it needs every zone's ``vpz_cfm`` (or
      ``vpz_min_cfm``), and ``vps_cfm`` (default ``sum(Vpz)`` -- which over-states Vps, and so the
      requirement, when zone peaks do not coincide).
    * ``"single_zone"`` -- ``Vot = Voz`` of its one zone.
    * ``"100pct_oa"`` -- ``Vot = sum(Voz)``.

    Voz uses ``ez_cooling`` / ``ez_heating`` (a zone's own values win). A zone missing an input,
    or an unknown system type / method, declines the system (``declined``). Zones whose area or
    population is marked assumed are listed in ``assumed``.
    """
    zones = list(zones)
    if system_type not in _SYSTEM_TYPES:
        raise ValueError(f"system_type must be one of {_SYSTEM_TYPES}, got {system_type!r}")
    if method not in _METHODS:
        raise ValueError(f"method must be one of {_METHODS}, got {method!r}")
    req = SystemVrpRequirement(
        system=system,
        system_type=system_type,
        method=method if system_type == "multiple_zone" else None,
        n_zones=len(zones),
    )
    if not zones:
        req.declined = "no zones"
        return req
    lacking = {z.zone: z.missing() for z in zones if z.missing()}
    if lacking:
        req.declined = "zone inputs missing: " + "; ".join(
            f"{k} ({', '.join(v)})" for k, v in sorted(lacking.items())
        )
        return req
    for z in zones:
        if z.area_assumed:
            req.assumed.append(f"{z.zone}: area")
        if z.population_assumed:
            req.assumed.append(f"{z.zone}: population")
    if req.assumed:
        req.caveats.append(
            "the requirement rests on assumed inputs (" + ", ".join(req.assumed) + "), not design "
            "data: confirm them against the mechanical schedule"
        )
    if system_type == "single_zone" and len(zones) != 1:
        req.declined = f"a single-zone system has one zone, {len(zones)} given"
        return req

    rows = []
    for z in zones:
        ezc = float(z.ez_cooling if z.ez_cooling is not None else ez_cooling)
        ezh = float(z.ez_heating if z.ez_heating is not None else ez_heating)
        if ezc <= 0 or ezh <= 0:
            raise ValueError(f"zone {z.zone}: Ez must be > 0")
        vbz, voz_c = _voz(z, ezc)
        voz_h = vbz / ezh
        rp, ra = z.rates()  # type: ignore[misc,unused-ignore]
        rows.append(
            {
                "zone": z.zone,
                "rp": rp,
                "ra": ra,
                "pz": float(z.population or 0.0),  # missing() checked above
                "az": float(z.area_sqft or 0.0),
                "vbz_cfm": round(vbz, 1),
                "ez_cooling": ezc,
                "ez_heating": ezh,
                "voz_cooling_cfm": round(voz_c, 1),
                "voz_heating_cfm": round(voz_h, 1),
                "_voz": {"cooling": voz_c, "heating": voz_h},
                "_z": z,
            }
        )
    req.sum_pz = round(sum(r["pz"] for r in rows), 2)

    if system_type == "single_zone":
        req.vot_cooling_cfm = round(rows[0]["_voz"]["cooling"], 1)
        req.vot_heating_cfm = round(rows[0]["_voz"]["heating"], 1)
    elif system_type == "100pct_oa":
        req.vot_cooling_cfm = round(sum(r["_voz"]["cooling"] for r in rows), 1)
        req.vot_heating_cfm = round(sum(r["_voz"]["heating"] for r in rows), 1)
    else:
        spz = sum(r["pz"] for r in rows)
        if d is not None:
            dd = float(d)
            req.ps = round(dd * spz, 2)
        elif ps is not None and spz > 0:
            dd = min(float(ps) / spz, 1.0)
            req.ps = float(ps)
            if float(ps) > spz:
                req.caveats.append(
                    f"system population Ps {float(ps):g} exceeds the zones' sum {spz:g}: D = 1"
                )
        else:
            dd = 1.0
            req.caveats.append(
                "no system population (Ps) given: occupant diversity D = 1, the most "
                "conservative Vou (and, simplified, Ev = 0.75) -- set systems.<id>.ps"
            )
        if spz <= 0:
            dd = 1.0  # an unoccupied system: the area term alone; D is moot
        if not 0.0 < dd <= 1.0:
            raise ValueError(f"occupant diversity D must be in (0, 1], got {dd}")
        req.d = round(dd, 4)
        vou = dd * sum(r["rp"] * r["pz"] for r in rows) + sum(r["ra"] * r["az"] for r in rows)
        req.vou_cfm = round(vou, 1)
        if method == "simplified":
            ev = simplified_ev(dd)
            req.ev_cooling = req.ev_heating = round(ev, 4)
            req.vot_cooling_cfm = req.vot_heating_cfm = round(vou / ev, 1)
            for r in rows:
                vmin = r["_z"].vpz_min_cfm
                if vmin is None:
                    continue
                r["vpz_min_cfm"] = float(vmin)
                for mode in ("cooling", "heating"):
                    need = 1.5 * r["_voz"][mode]
                    if float(vmin) < need:
                        req.vpz_min_short.append(
                            {
                                "zone": r["zone"],
                                "mode": mode,
                                "vpz_min_cfm": float(vmin),
                                "needed_cfm": round(need, 1),
                            }
                        )
            if req.vpz_min_short:
                zs = sorted({x["zone"] for x in req.vpz_min_short})
                req.caveats.append(
                    f"{len(zs)} zone(s) have a minimum primary airflow below 1.5 x Voz ("
                    + ", ".join(zs[:5])
                    + ("..." if len(zs) > 5 else "")
                    + "): the simplified Ev may over-state the system's efficiency there -- "
                    "use method='appendix' with the zones' primary airflows"
                )
        else:
            vpz = {}
            for r in rows:
                z = r["_z"]
                v = z.vpz_cfm if z.vpz_cfm is not None else z.vpz_min_cfm
                if v is None or not float(v) > 0:
                    req.declined = (
                        f"the appendix method needs every zone's primary airflow ({r['zone']} "
                        "has none)"
                    )
                    return req
                vpz[r["zone"]] = float(v)
            if vps_cfm is not None:
                vps = float(vps_cfm)
            else:
                vps = sum(vpz.values())
                req.caveats.append(
                    "system primary airflow Vps taken as the sum of the zones' Vpz: over-states "
                    "Vps (and the requirement) when zone peaks do not coincide -- set "
                    "systems.<id>.vps_cfm"
                )
            xs = vou / vps
            for mode in ("cooling", "heating"):
                evz = {}
                for r in rows:
                    zpz = r["_voz"][mode] / vpz[r["zone"]]
                    e = 1.0 + xs - zpz
                    evz[r["zone"]] = e
                    r[f"zpz_{mode}"] = round(zpz, 3)
                    r[f"evz_{mode}"] = round(e, 3)
                ev = min(evz.values())
                if ev <= 0:
                    req.declined = (
                        f"appendix Ev <= 0 in {mode} (a zone's Voz exceeds its primary airflow "
                        "by more than the system's OA fraction): the system cannot ventilate it"
                    )
                    return req
                setattr(req, f"ev_{mode}", round(ev, 4))
                setattr(req, f"vot_{mode}_cfm", round(vou / ev, 1))
    for r in rows:
        r.pop("_voz")
        r.pop("_z")
    req.zones = rows
    return req


def estimate_oa_cfm(
    mat: pd.Series,
    rat: pd.Series,
    oat: pd.Series,
    supply_cfm: pd.Series,
    *,
    sensor_err_f: float = 2.0,
    flow_uncertainty: float = 0.10,
    min_delta_t: float = 10.0,
) -> pd.DataFrame:
    """Outdoor airflow from the mixing temperatures x supply airflow, with an uncertainty band.

    ``f = (MAT - RAT) / (OAT - RAT)`` and ``OA = f * SA`` on samples with ``|OAT - RAT| >=
    min_delta_t`` (the balance cannot resolve anything closer) and ``0 <= f <= 1``. Each
    temperature's error ``sensor_err_f`` propagates to ``sigma_f = sensor_err_f / |OAT - RAT| *
    sqrt(1 + f**2 + (1 - f)**2)`` (first order), combined in quadrature with a relative supply-flow
    uncertainty; the band is +/- one combined sigma, floored at 0. The default 2 F per sensor is
    the field accuracy of a single-point mixed-air sensor in a stratified plenum rather than a
    laboratory figure -- and even that under-covers real errors: on the open LBNL Building 59 data
    (catalog example) the estimate reads 0.4-0.7 of the RTUs' flow stations, whose own accuracy is
    unknown, while their MAT sensors fail the flow balance. Returns columns ``oa_cfm``,
    ``oa_lo_cfm``, ``oa_hi_cfm`` and ``oa_fraction`` on the usable samples. A MAT sensor biased by
    a few degrees shifts ``f`` by ``bias / |OAT - RAT|`` -- see
    :func:`camber.sensorhealth.mixing_flow_consistency`.
    """
    w = (
        pd.concat({"mat": mat, "rat": rat, "oat": oat, "sa": supply_cfm}, axis=1)
        .apply(pd.to_numeric, errors="coerce")
        .dropna()
    )
    dt = w["oat"] - w["rat"]
    w = w[dt.abs() >= min_delta_t]
    dt = w["oat"] - w["rat"]
    f = (w["mat"] - w["rat"]) / dt
    keep = f.between(0.0, 1.0) & (w["sa"] > 0)
    w, f, dt = w[keep], f[keep], dt[keep]
    sig_f = sensor_err_f / dt.abs() * np.sqrt(1.0 + f**2 + (1.0 - f) ** 2)
    oa = f * w["sa"]
    rel = np.sqrt((sig_f / f.where(f > 0)) ** 2 + flow_uncertainty**2)
    sig = (oa * rel).fillna(sig_f * w["sa"])
    return pd.DataFrame(
        {
            "oa_cfm": oa,
            "oa_lo_cfm": (oa - sig).clip(lower=0.0),
            "oa_hi_cfm": oa + sig,
            "oa_fraction": f,
        },
        index=w.index,
    )


@dataclass
class SystemVrpResult:
    """Measured (or estimated) system outdoor air against its 62.1 VRP requirement.

    ``status`` is ``under`` / ``adequate`` / ``over``, ``uncertain`` (the estimated OA's band
    straddles a threshold) or ``insufficient`` (too few judged samples). ``ratio`` is the median of
    the per-sample OA / Vot, each sample judged against its mode's Vot (heating when
    ``heating_mask`` says so). ``basis`` is ``"oa_flow"`` (a flow station) or
    ``"temperature_estimate"`` (:func:`estimate_oa_cfm`), with ``ratio_lo`` / ``ratio_hi`` its band.
    """

    system: str
    status: str
    basis: str
    n: int
    ratio: float | None = None
    ratio_lo: float | None = None
    ratio_hi: float | None = None
    ratio_cooling: float | None = None
    ratio_heating: float | None = None
    n_heating: int = 0
    measured_cfm: float | None = None
    required_cfm: float | None = None
    deficit_cfm: float | None = None
    under_hours_pct: float | None = None
    requirement: SystemVrpRequirement | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _med(x) -> float | None:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return round(float(np.median(x)), 3) if len(x) else None


def assess_system_62_1(
    oa_cfm,
    requirement: SystemVrpRequirement,
    *,
    heating_mask=None,
    judged_mask=None,
    oa_lo_cfm=None,
    oa_hi_cfm=None,
    under_tol: float = 0.9,
    over_factor: float = 1.5,
    min_samples: int = 24,
) -> SystemVrpResult:
    """Judge a system's outdoor airflow (a Series, cfm) against its VRP ``requirement``.

    ``judged_mask`` keeps the samples to judge (occupied, fan on). ``heating_mask`` marks the
    heating-mode samples, judged against ``vot_heating_cfm``; the rest are judged against
    ``vot_cooling_cfm``. Status from the median per-sample ratio OA / Vot: **under** below
    ``under_tol``, **over** above ``over_factor``, else **adequate**. With ``oa_lo_cfm`` /
    ``oa_hi_cfm`` (an estimate's band) the verdict must hold across the band: **under** only when
    the band's top is under, **over** only when its bottom is over; a band that straddles a
    threshold is **uncertain**.
    """
    req = requirement
    basis = "temperature_estimate" if oa_lo_cfm is not None else "oa_flow"
    base = {"system": req.system, "basis": basis, "requirement": req}
    if req.declined or req.vot_cooling_cfm is None:
        return SystemVrpResult(status="insufficient", n=0, **base)  # type: ignore[arg-type]
    s = pd.to_numeric(pd.Series(oa_cfm), errors="coerce")
    keep = s.notna()
    if judged_mask is not None:
        keep &= pd.Series(judged_mask).reindex(s.index).fillna(False).astype(bool)
    s = s[keep]
    heat = (
        pd.Series(heating_mask).reindex(s.index).fillna(False).astype(bool)
        if heating_mask is not None
        else pd.Series(False, index=s.index)
    )
    n = int(len(s))
    if n < min_samples:
        return SystemVrpResult(status="insufficient", n=n, **base)  # type: ignore[arg-type]
    vot_c = float(req.vot_cooling_cfm)
    vot_h = float(req.vot_heating_cfm if req.vot_heating_cfm is not None else vot_c)
    vot = np.where(heat.to_numpy(), vot_h, vot_c)
    r = s.to_numpy(dtype=float) / vot
    ratio = _med(r)
    lo = hi = None
    if oa_lo_cfm is not None and oa_hi_cfm is not None:
        lo = _med(pd.Series(oa_lo_cfm).reindex(s.index).to_numpy(dtype=float) / vot)
        hi = _med(pd.Series(oa_hi_cfm).reindex(s.index).to_numpy(dtype=float) / vot)
    rc = _med(r[~heat.to_numpy()]) if (~heat).any() else None
    rh = _med(r[heat.to_numpy()]) if heat.any() else None
    if ratio is None:
        status = "insufficient"
    elif lo is not None and hi is not None:
        if hi < under_tol:
            status = "under"
        elif lo > over_factor:
            status = "over"
        elif lo >= under_tol and hi <= over_factor:
            status = "adequate"
        else:
            status = "uncertain"
    elif ratio < under_tol:
        status = "under"
    elif ratio > over_factor:
        status = "over"
    else:
        status = "adequate"
    measured = round(float(s.median()), 1)
    required = round(float(np.median(vot)), 1)
    return SystemVrpResult(
        status=status,
        n=n,
        ratio=ratio,
        ratio_lo=lo,
        ratio_hi=hi,
        ratio_cooling=rc,
        ratio_heating=rh,
        n_heating=int(heat.sum()),
        measured_cfm=measured,
        required_cfm=required,
        deficit_cfm=round(max(0.0, required - measured), 1),
        under_hours_pct=round(100.0 * float(np.mean(r < under_tol)), 1),
        **base,  # type: ignore[arg-type]
    )


_ZONE_FIELDS = {f for f in VentZone.__dataclass_fields__}
_BOOL_TRUE = ("1", "true", "yes", "y", "assumed")


def _num_or_none(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    x = float(v)
    return x if np.isfinite(x) else None


def zones_from_records(records) -> list:
    """:class:`VentZone` objects from dicts (a config list, or CSV rows).

    Keys are the :class:`VentZone` field names; ``zone`` (or ``id`` / ``zone_id``) is required,
    ``ahu`` is accepted for ``system``, ``area`` / ``area_ft2`` for ``area_sqft`` and ``pz`` /
    ``occupants`` for ``population``. ``area_assumed`` / ``population_assumed`` accept booleans or
    yes/no text. An unknown key is an error (a typo would otherwise drop an input silently).
    """
    alias = {
        "id": "zone",
        "zone_id": "zone",
        "ahu": "system",
        "air_handler": "system",
        "area": "area_sqft",
        "area_ft2": "area_sqft",
        "pz": "population",
        "occupants": "population",
    }
    out = []
    for i, rec in enumerate(records):
        d = {}
        for k, v in dict(rec).items():
            key = alias.get(str(k).strip().lower(), str(k).strip().lower())
            if key not in _ZONE_FIELDS:
                raise ValueError(f"ventilation zone {i}: unknown field {k!r}")
            d[key] = v
        if not str(d.get("zone") or "").strip():
            raise ValueError(f"ventilation zone {i}: needs a zone id")
        kw: dict = {"zone": str(d["zone"]).strip()}
        for k in ("area_sqft", "population", "rp", "ra", "ez_cooling", "ez_heating"):
            kw[k] = _num_or_none(d.get(k))
        for k in ("vpz_min_cfm", "vpz_cfm"):
            kw[k] = _num_or_none(d.get(k))
        for k in ("space_type", "system", "note"):
            v = d.get(k)
            if v is not None and str(v).strip() != "":
                kw[k] = str(v).strip()
        for k in ("area_assumed", "population_assumed"):
            v = d.get(k)
            kw[k] = bool(v) if isinstance(v, bool) else str(v or "").strip().lower() in _BOOL_TRUE
        out.append(VentZone(**kw))
    return out


def load_vent_zones(spec, *, base_dir: str = ".") -> list:
    """Zones from a config ``ventilation.zones`` entry: a CSV path (relative to ``base_dir``), a
    list of dicts, or a list mixing both."""
    import csv
    import os

    items = spec if isinstance(spec, list) else [spec]
    out: list = []
    for it in items:
        if isinstance(it, str):
            path = it if os.path.isabs(it) else os.path.join(base_dir, it)
            with open(path, newline="") as fh:
                out += zones_from_records(csv.DictReader(fh))
        elif isinstance(it, dict):
            out += zones_from_records([it])
        else:
            raise ValueError("ventilation.zones must be a CSV path or a list of zone objects")
    seen: set = set()
    for z in out:
        if z.zone in seen:
            raise ValueError(f"ventilation zone {z.zone!r} is listed twice")
        seen.add(z.zone)
    return out
