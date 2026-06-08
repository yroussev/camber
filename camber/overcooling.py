"""High-minimum-airflow / overcooling root-cause diagnostic (PNNL Ch.7).

The reheat penalty measured in ``reheat.py`` is a *symptom*. A common root cause
is a terminal box whose minimum airflow is set too high: when the zone is already
satisfied on cooling (space temp at or below the cooling setpoint), the box still
delivers cold primary air at its minimum flow because the damper cannot close
below that floor -- so the space overcools and the reheat coil fires to compensate.

This diagnostic flags the *overcooling-at-min-flow* condition directly:
  zone satisfied (SpaceTemp <= ActCoolSP)  AND
  airflow pinned near its minimum setpoint (ActFlow <= ActFlowSP * (1 + tol)) AND
  damper not driven open beyond a low position (it can't reduce flow further)
optionally co-occurring with reheat (HWValve open), which is the wasteful response.

Headline metric: fraction of occupied hours the box overcools at min flow. When
that is high AND it overlaps with reheat, the box's minimum airflow is a primary
re-tuning target (lower the min flow / fix the flow station).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .schedules import occupied_mask

# Box measures this diagnostic uses (legacy token names).
OVERCOOL_MEASURES = ["SpaceTemp", "ActCoolSP", "ActFlow", "ActFlowSP", "Damper",
                     "HWValve", "WarmUp", "CoolDown"]


@dataclass
class OvercoolResult:
    """Zone overcooling diagnostics: satisfied-at-min-flow and reheat overlap rates."""

    equip: str
    n_considered: int
    satisfied_pct: float            # % occupied hrs zone at/below cooling setpoint
    overcool_at_minflow_pct: float  # satisfied AND airflow near min
    overcool_with_reheat_pct: float # the above AND reheat valve open
    median_minflow_fraction: float  # median ActFlowSP / max ActFlow (how high is "min"?)
    has_heat_valve: bool            # whether a HEAT_VALVE (HWValve) column was present
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _pct(mask, n):
    return round(100.0 * int(mask.sum()) / n, 2) if n else 0.0


def analyze_overcooling(
    df: pd.DataFrame,
    equip: str,
    *,
    flow_tol: float = 0.15,        # airflow within +15% of its setpoint == "at min"
    valve_thr: float = 5.0,
    damper_low: float = 30.0,      # damper at/below this can't reduce flow further
    satisfied_deadband_f: float = 0.0,  # require SpaceTemp <= ActCoolSP - deadband
    occupied_only: bool = True,
) -> OvercoolResult | None:
    """Detect overcooling-at-minimum-flow for one terminal box.

    Constants are OUR engineering thresholds (judgment, not a standard):
      flow_tol=0.15      -- airflow within 15% above its setpoint counts as "at the
                            minimum floor" (normal modulation around the floor).
      damper_low=30 %    -- a damper at/below ~30% is effectively at minimum position;
                            it cannot throttle primary air any further.
      satisfied_deadband_f -- conservative deadband for the "satisfied on cooling"
                            test. The space counts as satisfied only when its
                            temperature is at least this many degF *below* the active
                            cooling setpoint (``SpaceTemp <= ActCoolSP - deadband``).
                            The default of 0.0 is a true zero deadband: satisfied
                            means at or below the cooling setpoint -- the conservative
                            choice, and the behavior the predecessor reproduces. A
                            positive value *tightens* the test (requires genuine
                            below-setpoint operation), it never loosens it; this is
                            the correct orientation for an overcooling diagnostic
                            (the old additive ``satisfied_margin_f`` could only widen
                            the band, which over-counts).
    """
    if "SpaceTemp" not in df.columns or "ActCoolSP" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(
            work.index,
            warmup=work["WarmUp"] if "WarmUp" in work.columns else None,
            cooldown=work["CoolDown"] if "CoolDown" in work.columns else None,
        )]
    work = work.dropna(subset=["SpaceTemp", "ActCoolSP"])
    n = len(work)
    if n == 0:
        return None

    satisfied = work["SpaceTemp"] <= work["ActCoolSP"] - satisfied_deadband_f

    # airflow pinned near its minimum setpoint
    if "ActFlow" in work.columns and "ActFlowSP" in work.columns:
        at_min = work["ActFlow"] <= work["ActFlowSP"] * (1.0 + flow_tol)
    else:
        at_min = pd.Series(False, index=work.index)

    # damper not open beyond a low position (can't reduce flow further)
    if "Damper" in work.columns:
        damper_pinned = work["Damper"].fillna(0) <= damper_low
        at_min = at_min & damper_pinned

    overcool = satisfied & at_min
    has_heat_valve = "HWValve" in work.columns
    if has_heat_valve:
        with_reheat = overcool & (work["HWValve"] > valve_thr)
    else:
        with_reheat = pd.Series(False, index=work.index)

    # how high is the minimum, relative to the box's own peak airflow?
    if "ActFlowSP" in work.columns and "ActFlow" in work.columns and work["ActFlow"].max() > 0:
        minflow_frac = float((work["ActFlowSP"] / work["ActFlow"].max()).median())
    else:
        minflow_frac = float("nan")

    return OvercoolResult(
        equip=equip,
        n_considered=n,
        satisfied_pct=_pct(satisfied, n),
        overcool_at_minflow_pct=_pct(overcool, n),
        overcool_with_reheat_pct=_pct(with_reheat, n),
        median_minflow_fraction=round(minflow_frac, 3) if minflow_frac == minflow_frac else float("nan"),
        has_heat_valve=has_heat_valve,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
