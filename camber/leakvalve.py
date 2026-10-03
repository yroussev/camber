"""Leaking coil-valve detection (PNNL Re-tuning Ch.5/Ch.7).

A valve commanded closed that still passes water wastes energy that the
simultaneous-heat/cool and reheat checks miss (those see *commanded* coil action;
a leak is uncommanded). The tell is a temperature shift across a coil whose valve
is shut.

At an AHU, air flows mixed-air -> coils -> supply-air. When BOTH coil valves are
commanded closed, supply-air should about equal mixed-air plus the supply fan's heat.
If instead:
  SAT > MAT + fan heat + thr  -> the heating coil is adding heat with its valve shut  (HW leak)
  SAT < MAT - thr             -> the cooling coil is removing heat with its valve shut (CHW leak)

Reported as the fraction of both-valves-closed hours showing each leak signature.

**Fan heat (0.93, #42).** ``fan_heat_f`` is the supply fan's temperature rise, the
allowance G36 calls ΔT_SF (``fdd_g36.G36Thresholds.dT_sf``). It defaults to the same 2 F.
It is an allowance, not an offset: up to ``fan_heat_f`` of a rise may be the fan's, so a
heating leak must exceed it, while a cooling leak is judged with none of it credited.
(Until 0.93 a fixed 1 F was subtracted on both sides.)
A fan adds heat only while it runs, so the allowance applies only to fan-on samples. With a
fan status or speed mapped, fan-off samples are left out altogether: with no air moving,
the two sensors say nothing about the coils. On a real AHU the supply sensor also sits
downstream of the fan, so a heat rise between mixed and supply air can be fan heat, not
a leak. Where the unit trends its **coil leaving-air temperatures** (``HeatCoilLeaving`` /
``CoolCoilLeaving``, G36's HCLT / CCLT), each coil is judged on its own leaving air against
the mixed air, and the supply air and fan heat are not used. Those sensors sit upstream of
a draw-through supply fan; on a blow-through unit, pass ``coil_sensor_fan_heat=True`` so
the allowance applies to them too. Judging each coil against the mixed air holds whichever
coil comes first: with both valves shut, the air between the coils is still the mixed air.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "LeakValveResult",
    "analyze_leak_valves",
]


@dataclass
class LeakValveResult:
    """Coil-valve leak diagnostics: SAT-vs-MAT drift when both valves are closed."""

    equip: str
    n_both_closed: int  # hours both coil valves commanded closed
    hw_leak_pct: float  # % of those hours SAT > MAT + thr (heating leak)
    chw_leak_pct: float  # % of those hours SAT < MAT - thr (cooling leak)
    median_delta_f: float  # median (SAT - MAT - fan_heat_f) when both closed
    coverage_start: str
    coverage_end: str
    # --- 0.93 (#42), provisional ---
    fan_heat_f: float | None = None  # the fan-heat allowance applied to the supply-air path
    fan_gated: bool = False  # True when a fan signal kept only fan-on samples
    # which temperature judged each coil: "SupplyAir" or the coil's own leaving-air sensor
    hw_basis: str | None = None
    chw_basis: str | None = None
    hw_median_delta_f: float | None = None  # median heating-coil rise, beyond the fan allowance
    chw_median_delta_f: float | None = None  # median cooling-coil rise (a drop is negative)

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _fan_on(work: pd.DataFrame, speed_thr: float):
    """Fan-running mask from ``SupplyFanStatus`` (duty > 0.5) or ``SupplyFanSpeed``, else None."""
    if "SupplyFanStatus" in work.columns and work["SupplyFanStatus"].notna().any():
        return work["SupplyFanStatus"].astype(float) > 0.5
    if "SupplyFanSpeed" in work.columns and work["SupplyFanSpeed"].notna().any():
        return work["SupplyFanSpeed"].astype(float) > speed_thr
    return None


def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(s.median()), 1) if len(s) else None


def analyze_leak_valves(
    df: pd.DataFrame,
    equip: str,
    *,
    valve_closed_thr: float = 5.0,  # valve at/below this == commanded closed
    delta_thr_f: float = 3.0,  # coil-side shift beyond the fan allowance to call a leak
    fan_heat_f: float = 2.0,  # supply-fan temperature rise allowed for (G36 dT_SF)
    occupied_only: bool = False,  # leaks show whenever the AHU runs
    coil_sensor_fan_heat: bool = False,  # True on a blow-through unit (fan ahead of the coils)
    fan_speed_thr: float = 5.0,  # fan speed above this counts as running
) -> LeakValveResult | None:
    """Detect leaking coil valves at an AHU. ``df`` has CHW_Valve, HHW_Valve,
    MixedAir, SupplyAir (measure-named), and optionally HeatCoilLeaving, CoolCoilLeaving,
    SupplyFanStatus / SupplyFanSpeed.

    Thresholds are OUR engineering judgment / PNNL Ch.5: valve_closed_thr=5%
    (deadband), delta_thr_f=3F (a coil-side shift beyond noise). ``fan_heat_f`` is the
    supply fan's temperature rise, allowed for before a heating leak is called: G36's ΔT_SF,
    2 F by default (``fdd_g36.G36Thresholds.dT_sf``; until 0.93 a fixed 1 F, subtracted on
    both sides). A coil with its own leaving-air sensor is judged on it instead (module
    docstring).
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
    fan = _fan_on(work, fan_speed_thr)
    if fan is not None:
        work = work[fan]  # no air moving -> the sensors say nothing about the coils
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
    # The fan's heat is an ALLOWANCE, not an offset: somewhere between none and ``fan_heat_f`` of
    # the SAT - MAT rise is the fan's. A heating leak must rise beyond all of it; a cooling leak
    # must drop below the mixed air with none of it credited (crediting it would call a supply
    # sensor that reads a little below the mixed air a leak).
    rise = bc.SupplyAir - bc.MixedAir
    delta = rise - fan_heat_f  # the rise beyond the fan-heat allowance (reported)
    coil_heat = fan_heat_f if coil_sensor_fan_heat else 0.0

    def coil_rise(col):
        """A coil's own leaving air minus the mixed air (NaN where the sensor is missing)."""
        if col not in work.columns or work[col].notna().sum() == 0:
            return None
        leave = work[col].reindex(bc.index).astype(float)
        leave = leave.where(leave.between(30, 140))
        d = leave - bc.MixedAir
        return d if d.notna().sum() >= 10 else None

    hw_own = coil_rise("HeatCoilLeaving") if has_hw else None
    chw_own = coil_rise("CoolCoilLeaving")
    # heating: the rise beyond the fan heat the sensor may carry; cooling: the drop, no credit
    hw_src = (hw_own - coil_heat) if hw_own is not None else delta
    chw_src = chw_own if chw_own is not None else rise
    # a heating-leak signature is only meaningful where a heating coil exists; a coil sensor's
    # missing samples are not evaluated (the share is over the samples it read)
    if has_hw:
        hw_ok = hw_src.dropna()
        hw_pct = 100.0 * float((hw_ok > delta_thr_f).mean()) if len(hw_ok) else 0.0
    else:
        hw_pct = 0.0
    chw_ok = chw_src.dropna()
    chw_pct = 100.0 * float((chw_ok < -delta_thr_f).mean()) if len(chw_ok) else 0.0
    return LeakValveResult(
        equip=equip,
        n_both_closed=n,
        hw_leak_pct=round(hw_pct, 1),
        chw_leak_pct=round(chw_pct, 1),
        median_delta_f=round(float(delta.median()), 1),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        fan_heat_f=float(fan_heat_f),
        fan_gated=fan is not None,
        hw_basis=("HeatCoilLeaving" if hw_own is not None else "SupplyAir") if has_hw else None,
        chw_basis="CoolCoilLeaving" if chw_own is not None else "SupplyAir",
        hw_median_delta_f=_median(hw_src) if has_hw else None,
        chw_median_delta_f=_median(chw_src),
    )
