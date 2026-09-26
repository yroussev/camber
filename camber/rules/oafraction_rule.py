"""Rule: outdoor-air-fraction / excess OA (PNNL Ch.5).

Flags an AHU pulling more outdoor air than its minimum while in cooling weather --
a direct cooling penalty in a hot climate. Adapts
:func:`camber.oafraction.analyze_oa_fraction` to the role-frame interface. OAT is
building-level and comes via the runner's ``shared`` channel.

**Fan-gated by default.** With the fan stopped the mixing-box temperatures describe still air,
not a mix, so only fan-on samples are judged (fan status, else fan speed, else airflow -- see
:func:`camber.schedules.fan_on_mask`); a unit that trends no fan signal is judged ungated and the
finding says so (``fan_gate`` metric). **Occupancy** comes from the unit's trended ``occupancy``
point when it has one, else an assumed weekday schedule. **The design minimum is the unit's
own:** ``min_oa_pct`` (and, for a sequence with a seasonal minimum, ``min_oa_pct_by_month``) must
be set from the sequence or measured at the unit's minimum damper position -- a 10 % minimum
*damper position* can be a 1.6 % OA *fraction*.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..oafraction import analyze_oa_fraction
from ..schedules import FAN_GATE_NONE, fan_on_mask
from .base import Finding

_ROLE_TO_COL = {
    Role.OAT: "OAT",
    Role.MIXED_AIR_TEMP: "MixedAir",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.OA_DAMPER: "OA_Damper",
}


class OutdoorAirFraction:
    """Detects excess (or insufficient) outdoor-air fraction at an AHU (PNNL Re-tuning Ch.5)."""

    name = "outdoor_air_fraction"
    roles_required = (Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP)
    roles_optional = (
        Role.OAT,
        Role.OA_DAMPER,
        # fan-on gate: status, else speed, else airflow (see camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
        Role.OCCUPANCY,
    )

    def __init__(
        self,
        min_oa_pct: float = 20.0,
        cooling_cutoff_f: float = 70.0,
        *,
        fan_gate: bool = True,
        min_oa_pct_by_month: dict | None = None,
    ):
        # min OA is building-specific (sequence); cooling cutoff is climate-ish
        self.min_oa_pct = min_oa_pct
        self.cooling_cutoff_f = cooling_cutoff_f
        # judge fan-on samples only (when the unit trends a fan signal); fan-off samples read
        # still air, and on the LBNL single-duct AHU they alone made a unit at 1.6 % OA read "ok"
        # against a 20 % assumption (#23)
        self.fan_gate = fan_gate
        # a seasonal design minimum: {month (1-12): pct} overriding min_oa_pct in those months
        by_month = {int(k): float(v) for k, v in (min_oa_pct_by_month or {}).items()}
        if any(not 1 <= k <= 12 for k in by_month):
            raise ValueError("min_oa_pct_by_month keys must be months 1-12")
        self.min_oa_pct_by_month = by_month or None
        # the analyzer's margins (camber.oafraction defaults); the evidence envelope uses them too
        self.excess_margin_pct = 5.0
        self.under_margin_pct = 5.0

    def _gate(self, frame: pd.DataFrame):
        """``(fan-on mask | None, label)``; ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        gate, fan_src = self._gate(frame)
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        res = analyze_oa_fraction(
            legacy,
            equip,
            min_oa_pct=self.min_oa_pct,
            cooling_cutoff_f=self.cooling_cutoff_f,
            excess_margin_pct=self.excess_margin_pct,
            under_margin_pct=self.under_margin_pct,
            gate=gate,
            occ=occ,
            min_oa_by_month=self.min_oa_pct_by_month,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"fan_gate": fan_src},
                summary="insufficient data (need OAT/MAT/RAT)",
            )
        ex, mn = res.excess_oa_pct, res.min_oa_pct
        order = {"ok": 0, "warn": 1, "fault": 2}
        # excess-OA severity (energy penalty): share of cooling hours above the min
        sev_excess = "fault" if ex >= 50.0 else ("warn" if ex >= 20.0 else "ok")
        # under-ventilation severity (IAQ / code risk): from the MEDIAN OAF shortfall
        # below the minimum -- robust to noise near the floor, unlike a %-below count
        m = res.oaf_median_pct
        if self.min_oa_pct_by_month:  # seasonal minimum: judge each sample against its own
            below_half = res.median_ratio_to_min < 0.5
            below_margin = res.median_vs_min_pct < -5.0
        else:
            below_half, below_margin = m < mn * 0.5, m < mn - 5.0
        if below_half:
            sev_under = "fault"
        elif below_margin:
            sev_under = "warn"
        else:
            sev_under = "ok"
        severity = max(sev_excess, sev_under, key=lambda s: order[s])
        mins = f"{mn:g}% min"
        if res.min_oa_by_month:
            groups: dict = {}
            for month, pct in sorted(res.min_oa_by_month.items()):
                groups.setdefault(pct, []).append(str(month))
            seasonal = "; ".join(f"{p:g}% in months {', '.join(ms)}" for p, ms in groups.items())
            mins = f"{mn:g}% min ({seasonal})"
        if order[sev_under] > order[sev_excess]:
            tail = (
                f"under-ventilation: median OAF {m:.0f}% below the "
                f"{mins} ({res.under_vent_pct:.0f}% of occupied hours low)"
            )
        else:
            tail = f"excess OA {ex:.0f}% of cooling hours above {mins}"
        if fan_src == FAN_GATE_NONE:
            tail += "; not fan-gated (no fan signal trended)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "oaf_median_pct": res.oaf_median_pct,
                "median_oaf_cooling": res.median_oaf_cooling,
                "excess_oa_pct": res.excess_oa_pct,
                "under_vent_pct": res.under_vent_pct,
                "min_oa_pct": res.min_oa_pct,
                "n_cooling": res.n_cooling,
                "n_valid": res.n_valid,
                # which way it failed, for the recommendation: the worse of the two reads
                "failure_mode": (
                    "under_ventilation"
                    if order[sev_under] > order[sev_excess]
                    else ("excess_oa" if sev_excess != "ok" else None)
                ),
                "min_oa_pct_by_month": res.min_oa_by_month,
                "fan_gate": fan_src,
                "occupancy": "trended" if occ is not None and occ.notna().any() else "assumed",
            },
            summary=(
                f"{equip}: OAF median {res.oaf_median_pct:.0f}% "
                f"({res.median_oaf_cooling:.0f}% in cooling weather); {tail}"
            ),
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: OA fraction vs OAT against *this rule's* envelope.

        Drawn from the samples the verdict is computed on (occupied, fan-on when gated, plausible,
        stable temperature balance) with the configured ``min_oa_pct`` and ``cooling_cutoff_f``:
        every sample must sit at or above ``min - 5`` % (under-ventilation), and in cooling weather
        (OAT above the cutoff) at or below ``min + 5`` % (excess OA). The red points are exactly
        the samples the two percentages in the finding count.

        With a seasonal minimum (``min_oa_pct_by_month``) one flat line would misjudge every month
        on the other minimum, so the chart plots each sample's OA fraction *relative to its own
        month's minimum* (percentage points) against the same margins.
        """
        import numpy as np

        from ..charts.diagnostic import DiagnosticTemplate
        from ..charts.evidence import Evidence
        from ..oafraction import _oaf_samples, _sample_minima

        if Role.OAT not in frame.columns:
            return None
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        w = _oaf_samples(frame.rename(columns=cols), gate=self._gate(frame)[0], occ=occ)
        if w is None or w.empty:
            return None
        cut = float(self.cooling_cutoff_f)
        if self.min_oa_pct_by_month:
            mins = _sample_minima(w.index, self.min_oa_pct, self.min_oa_pct_by_month)
            y = w["oaf"] - mins
            ycol, floor = "oa_fraction_vs_min_pts", 0.0
            groups: dict = {}
            for month, pct in sorted(self.min_oa_pct_by_month.items()):
                groups.setdefault(pct, []).append(str(month))
            seasonal = "; ".join(f"{p:g}% in months {', '.join(ms)}" for p, ms in groups.items())
            name = (
                f"OA fraction vs the month's minimum ({self.min_oa_pct:g}%; {seasonal}), "
                f"cooling above {cut:g}°F"
            )
            ylabel = "OA fraction − month's minimum (%-points)"
        else:
            y = w["oaf"]
            ycol, floor = "oa_fraction_pct", float(self.min_oa_pct)
            name = f"OA fraction (min {self.min_oa_pct:g}%, cooling above {cut:g}°F)"
            ylabel = "OA fraction (%)"
        derived = pd.DataFrame({Role.OAT: w["OAT"], ycol: y})
        lo_ok = floor - self.under_margin_pct
        hi_ok = floor + self.excess_margin_pct
        top = max(float(y.max()), hi_ok) + 5.0

        def expected(xv):
            xv = np.asarray(xv, dtype=float)
            return np.full(len(xv), lo_ok), np.where(xv > cut, hi_ok, top)

        tmpl = DiagnosticTemplate(
            name, Role.OAT, ycol, expected, "OAT (°F)", ylabel, "the rule's own parameters"
        )
        return Evidence(
            renderer="diagnostic", template=tmpl, frame=derived, title=f"{equip}: OA fraction"
        )
