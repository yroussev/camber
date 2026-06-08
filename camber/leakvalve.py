"""Leaking coil-valve detection (PNNL Re-tuning Ch.5/Ch.7).

A valve commanded closed that still passes water wastes energy that the
simultaneous-heat/cool and reheat checks miss (those see *commanded* coil action;
a leak is uncommanded). The tell is a temperature shift across a coil whose valve
is shut.

At an AHU, air flows mixed-air -> coils -> supply-air. When BOTH coil valves are
commanded closed, supply-air should about equal mixed-air (plus ~1-2 degF of fan
heat). If instead:
  SAT > MAT + thr  -> the heating coil is adding heat with its valve shut  (HW leak)
  SAT < MAT - thr  -> the cooling coil is removing heat with its valve shut (CHW leak)

Reported as the fraction of both-valves-closed hours showing each leak signature.
``fan_heat_f`` offsets the expected small SAT rise from fan work so it isn't
mistaken for a heating leak.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask


@dataclass
class LeakValveResult:
    """Coil-valve leak diagnostics: SAT-vs-MAT drift when both valves are closed."""

    equip: str
    n_both_closed: int            # hours both coil valves commanded closed
    hw_leak_pct: float            # % of those hours SAT > MAT + thr (heating leak)
    chw_leak_pct: float           # % of those hours SAT < MAT - thr (cooling leak)
    median_delta_f: float         # median (SAT-MAT) when both closed
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_leak_valves(
    df: pd.DataFrame,
    equip: str,
    *,
    valve_closed_thr: float = 5.0,   # valve at/below this == commanded closed
    delta_thr_f: float = 3.0,        # |SAT-MAT| beyond fan heat to call a leak
    fan_heat_f: float = 1.0,         # expected SAT rise from fan work (subtracted)
    occupied_only: bool = False,     # leaks show whenever the AHU runs
) -> LeakValveResult | None:
    """Detect leaking coil valves at an AHU. ``df`` has CHW_Valve, HHW_Valve,
    MixedAir, SupplyAir (measure-named).

    Thresholds are OUR engineering judgment / PNNL Ch.5: valve_closed_thr=5%
    (deadband), delta_thr_f=3F (a coil-side shift beyond noise), fan_heat_f=1F
    (typical supply-fan temperature rise, removed before judging a heating leak).
    """
    # The cooling coil + air temps are required; the heating coil is optional, so a
    # cooling-only AHU (no heating valve) is still screened for a cooling-coil leak.
    base_need = ("CHW_Valve", "MixedAir", "SupplyAir")
    if any(c not in df.columns for c in base_need):
        return None
    has_hw = "HHW_Valve" in df.columns
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    cols = list(base_need) + (["HHW_Valve"] if has_hw else [])
    w = work[cols].dropna()
    w = w[(w.MixedAir.between(30, 120)) & (w.SupplyAir.between(30, 120))]
    closed = w.CHW_Valve <= valve_closed_thr
    if has_hw:
        closed = closed & (w.HHW_Valve <= valve_closed_thr)
    bc = w[closed]
    n = len(bc)
    if n < 10:
        return None
    # remove expected fan heat before judging a heating leak
    delta = (bc.SupplyAir - bc.MixedAir) - fan_heat_f
    # a heating-leak signature is only meaningful where a heating coil exists
    hw_leak = (delta > delta_thr_f) if has_hw else pd.Series(False, index=bc.index)
    chw_leak = delta < -delta_thr_f
    return LeakValveResult(
        equip=equip,
        n_both_closed=n,
        hw_leak_pct=round(100.0 * float(hw_leak.mean()), 1),
        chw_leak_pct=round(100.0 * float(chw_leak.mean()), 1),
        median_delta_f=round(float(delta.median()), 1),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
