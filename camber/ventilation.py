"""ASHRAE 62.1 ventilation-rate verification and DCV checks.

`camber.iaq` reads CO₂ as a *proxy* for ventilation adequacy; this module does the explicit
**code-rate** check. Two analytics:

- **Ventilation Rate Procedure (VRP).** ASHRAE 62.1 sets a zone's required outdoor air as
  ``Vbz = Rp·Pz + Ra·Az`` (people term + area term), and the zone outdoor air ``Voz = Vbz / Ez``
  for a zone air-distribution effectiveness ``Ez``. :func:`assess_62_1` compares *measured*
  delivered OA against ``Voz`` and flags **under-ventilation** (a code/IAQ concern) or gross
  **over-ventilation** (a conditioning-energy penalty).
- **Demand-Controlled Ventilation (DCV).** DCV should *modulate* OA with occupancy (CO₂ or an
  occupancy signal). :func:`assess_dcv` checks that OA actually tracks demand: it flags a
  **static** OA signal (DCV not functioning / fixed OA) and an **uncorrelated** one, and — given
  a CO₂ setpoint — under-ventilation that persists while OA sits at its minimum.

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
        measured = float(_AGG[aggregate](vals)) if n else float("nan")
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


@dataclass
class DcvResult:
    """Whether OA modulates with ventilation demand (DCV functioning)."""

    equip: str
    n: int
    correlation: float  # OA vs demand (CO₂/occupancy); DCV -> positive
    modulation: float  # (max-min)/max of the OA signal, 0..1
    status: str  # "functioning" | "static" | "uncorrelated" | "insufficient"
    co2_breach_at_min_pct: float | None  # % samples CO₂>setpoint while OA at its min (if given)

    def as_dict(self) -> dict:
        return asdict(self)


def assess_dcv(
    oa_signal: pd.Series,
    demand_signal: pd.Series,
    *,
    occupied_mask=None,
    min_corr: float = 0.3,
    min_modulation: float = 0.1,
    co2_setpoint: float | None = None,
    equip: str = "",
) -> DcvResult:
    """Verify DCV: does the OA signal track ventilation demand?

    ``oa_signal`` is OA flow / OA fraction / OA-damper position; ``demand_signal`` is CO₂ or an
    occupancy signal. DCV working ⇒ OA *modulates* (range ≥ ``min_modulation``) and *correlates*
    positively with demand (≥ ``min_corr``). A flat OA signal ⇒ **static** (fixed OA / DCV off);
    modulating but uncorrelated ⇒ **uncorrelated**. If ``co2_setpoint`` is given (demand is CO₂),
    also reports the share of samples breaching it while OA is pinned at its minimum.
    """
    df = pd.DataFrame({"oa": oa_signal, "d": demand_signal}).dropna()
    if occupied_mask is not None:
        df = df[occupied_mask.reindex(df.index, fill_value=False)]
    n = int(len(df))
    if n < 10:
        return DcvResult(
            equip=equip,
            n=n,
            correlation=float("nan"),
            modulation=float("nan"),
            status="insufficient",
            co2_breach_at_min_pct=None,
        )
    oa = df["oa"].to_numpy(dtype=float)
    d = df["d"].to_numpy(dtype=float)
    oa_max = float(np.max(oa))
    modulation = float((oa_max - float(np.min(oa))) / oa_max) if oa_max else 0.0
    corr = float(np.corrcoef(oa, d)[0, 1]) if np.std(oa) > 0 and np.std(d) > 0 else float("nan")

    if modulation < min_modulation:
        status = "static"
    elif np.isfinite(corr) and corr >= min_corr:
        status = "functioning"
    else:
        status = "uncorrelated"

    breach = None
    if co2_setpoint is not None:
        oa_min_level = float(np.min(oa)) + 0.05 * (oa_max - float(np.min(oa)))  # near-minimum band
        at_min = oa <= oa_min_level
        breach = round(100.0 * float(np.mean((d > co2_setpoint) & at_min)), 1) if n else 0.0

    return DcvResult(
        equip=equip,
        n=n,
        correlation=round(corr, 3) if np.isfinite(corr) else float("nan"),
        modulation=round(modulation, 3),
        status=status,
        co2_breach_at_min_pct=breach,
    )
