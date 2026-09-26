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

from dataclasses import asdict, dataclass

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
      (``reason="schedule_confounded"``).
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
    # exists to catch (a wildfire damper closure read as "not judged").
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
    common = dict(
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
        # stratum = (weekend?, hour): a weekday-only time clock is also a schedule
        slot = (df.index.dayofweek.to_numpy() >= 5) * 24 + df.index.hour.to_numpy()
        num = wt = 0.0
        for h in np.unique(slot):
            r, f = raised & (slot == h), at_floor & (slot == h)
            k = min(int(r.sum()), int(f.sum()))
            if k >= 3:
                num += float(agg(d[r]) - agg(d[f])) * k
                wt += k
        if wt < min_bin:
            return _result("insufficient", reason="schedule_confounded", **common)
        lift = num / wt
        ok = lift >= (min_lift_occupancy if kind == "presence" else min_lift_people)
    else:
        lift = float(np.median(d[raised]) - np.median(d[at_floor]))
        ok = lift >= min_lift_ppm
    common["demand_lift"] = round(lift, 3 if kind == "presence" else 1)
    return _result("functioning" if ok else "uncorrelated", **common)
