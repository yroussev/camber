"""Rule: mechanical cooling run while free cooling was available.

When it's cool enough outside to cool for free, running the compressor/chiller is pure waste. This
detects it directly — mechanical cooling active while OAT is below the economizer high limit — the
rule companion to the `camber.freecooling` opportunity quantifier. numpy/pandas.

``active`` is a cooling-valve position in **percent** (the role pipeline scales position roles to
0-100 %; a 0-1 source is rescaled here too), so a valve parked at 1 % is not "mechanical cooling
running". Durations are reported in hours from the trend's own sampling interval, not as sample
counts.

**An integrated economizer is not "missed" (#63).** Mechanical cooling while the unit is already on
(nearly) 100 % outside air is an integrated economizer doing its job -- the outside air alone can't
meet the load -- so those samples are not counted. The test is
:func:`camber.freecooling.integrated_economizer_mask`, the same one the RCx report's economizer page
applies: the measured OA fraction (from MAT/RAT/OAT) >= 80 % where ``|OAT - RAT| >= 5 °F``, else the
OA-damper signal >= 90 %. With neither an OA damper nor mixed- and return-air temperatures mapped,
the rule counts as before and caveats that an integrated economizer can't be told apart.
"""

from __future__ import annotations

import pandas as pd

from ..freecooling import (
    ECON_DAMPER_MIN_PCT,
    ECON_MIN_DELTA_F,
    ECON_OAF_MIN_PCT,
    integrated_economizer_mask,
)
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding


def _step_hours(index) -> float | None:
    """Median positive sample spacing in hours (``None`` if it can't be determined)."""
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    steps = pd.Series(index.sort_values()).diff().dropna()
    steps = steps[steps > pd.Timedelta(0)]
    return None if steps.empty else steps.median().total_seconds() / 3600.0


def _share(n: int, total: int) -> str:
    pct = 100.0 * n / total
    return "<1%" if pct < 1.0 else f"{pct:.0f}%"


class FreeCoolingMissed:
    """Flags mechanical cooling running while outdoor air was cool enough for free cooling."""

    name = "free_cooling_missed"
    roles_required = (Role.COOL_VALVE, Role.OAT)
    # #63: the OA damper, else the mixed/return-air balance, says when the unit is already on
    # (nearly) 100 % outside air -- an integrated economizer, not missed free cooling
    roles_optional = (Role.OA_DAMPER, Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP)

    def __init__(
        self,
        *,
        high_limit_f: float = 60.0,
        active: float = 5.0,  # cooling-valve % above which mechanical cooling is running
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
    ):
        self.high_limit_f = high_limit_f
        self.active = active
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat, cool = frame[Role.OAT], normalize_percent(frame[Role.COOL_VALVE])
        available = (oat < self.high_limit_f) & oat.notna() & cool.notna()
        n_avail = int(available.sum())
        step_h = _step_hours(frame.index)
        if n_avail == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no free-cooling weather in the window",
            )
        running = available & (cool > self.active)
        econ = integrated_economizer_mask(
            oat,
            damper=frame[Role.OA_DAMPER] if Role.OA_DAMPER in frame.columns else None,
            mat=frame[Role.MIXED_AIR_TEMP] if Role.MIXED_AIR_TEMP in frame.columns else None,
            rat=frame[Role.RETURN_AIR_TEMP] if Role.RETURN_AIR_TEMP in frame.columns else None,
        )
        caveats: list = []
        if econ is None:
            econ_basis = None
            integrated = pd.Series(False, index=frame.index)
            caveats.append(
                "no OA damper and no mixed/return-air temperatures: an integrated economizer "
                "(mechanical cooling on ~100 % outside air) can't be told apart from missed free "
                "cooling, so the missed share may be overstated -- map OA_DAMPER or MAT + RAT"
            )
        else:
            integrated = running & econ
            has_temps = (
                Role.MIXED_AIR_TEMP in frame.columns and Role.RETURN_AIR_TEMP in frame.columns
            )
            econ_basis = (
                "oa_fraction+damper"
                if has_temps and Role.OA_DAMPER in frame.columns
                else "oa_fraction"
                if has_temps
                else "damper"
            )
        missed = running & ~integrated
        n_integrated = int(integrated.sum())
        pct = 100.0 * float(missed.sum()) / n_avail
        hours = None if step_h is None else round(n_avail * step_h, 1)
        span = (
            f"{hours:g} free-cooling hours"
            if hours is not None
            else f"{n_avail} free-cooling samples"
        )
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics={
                "missed_pct": round(pct, 2),
                "high_limit_f": self.high_limit_f,
                "active_pct": self.active,
                "n_free_cooling_samples": n_avail,
                "n_free_cooling_hours": hours,
                # #63: samples with mechanical cooling on ~100 % OA (integrated, not missed)
                "integrated_economizer_basis": econ_basis,
                "n_integrated_economizer_samples": n_integrated if econ is not None else None,
                "integrated_economizer_hours": (
                    None if econ is None or step_h is None else round(n_integrated * step_h, 1)
                ),
                "econ_damper_min_pct": ECON_DAMPER_MIN_PCT,
                "econ_oaf_min_pct": ECON_OAF_MIN_PCT,
                "econ_min_delta_f": ECON_MIN_DELTA_F,
            },
            summary=(
                f"{equip}: mechanical cooling (valve > {self.active:g}%) ran {pct:.0f}% of the "
                f"{span} (OAT < {self.high_limit_f:g}°F)"
                + (
                    f"; {_share(n_integrated, n_avail)} more on ~100% outside air "
                    "(integrated economizer, not counted)"
                    if n_integrated
                    else ""
                )
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: cooling-valve position vs OAT — cooling at low OAT stands out."""
        from ..charts.evidence import Evidence

        return Evidence(
            renderer="oat_scatter",
            roles=[Role.COOL_VALVE],
            title=f"{equip}: cooling vs OAT (free-cooling)",
        )
