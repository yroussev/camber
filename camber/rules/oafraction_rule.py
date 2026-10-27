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

**Cost inputs (0.107, #195).** The finding also carries the ``oa_cost_*`` metrics the excess-OA
cost estimator (:mod:`camber.fault_economics`) prices: the excess above the declared minimum on the
judged (occupied, fan-on) samples, read as a cooling or heating penalty or as economizing by
:func:`camber.oafraction.excess_oa_penalty`. The heating valve, supply air, humidities and supply
airflow it reads are cost-only inputs (``roles_cost``): they never change the verdict.
``min_oa_source`` says whether ``min_oa_pct`` was configured (``"configured"``) or is the generic
20 % (``"default"``); only a configured minimum is priced.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..oafraction import (
    _oaf_samples,
    _observed_hours,
    _sample_minima,
    analyze_oa_fraction,
    excess_oa_penalty,
)
from ..schedules import FAN_GATE_NONE, fan_on_mask
from .base import Finding

_ROLE_TO_COL = {
    Role.OAT: "OAT",
    Role.MIXED_AIR_TEMP: "MixedAir",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.OA_DAMPER: "OA_Damper",
}

# cost-only inputs (0.107, #195): role -> the column name camber.oafraction.excess_oa_penalty reads
_COST_COLS = {
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
    Role.HEAT_VALVE: "HeatValve",
    Role.OUTDOOR_RH: "OAT_RH",
    Role.RETURN_AIR_HUMIDITY: "RA_RH",
    Role.RETURN_AIR_DEWPOINT_TEMP: "RA_DP",
    Role.AIRFLOW: "SupplyCFM",
}
_DEFAULT_MIN_OA_PCT = 20.0


def _cost_conditions(frame: pd.DataFrame, legacy: pd.DataFrame) -> pd.DataFrame:
    """The judged samples' conditions for :func:`camber.oafraction.excess_oa_penalty`: the mixing
    temperatures of ``legacy`` plus the cost-only roles of ``frame`` under their helper names."""
    cols = {c: legacy[c] for c in ("OAT", "MixedAir", "ReturnAir") if c in legacy.columns}
    for role, name in _COST_COLS.items():
        if role in frame.columns:
            cols[name] = pd.to_numeric(frame[role], errors="coerce")
    out = pd.DataFrame(cols, index=frame.index)
    return out[~out.index.duplicated()]


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
    # read only to price the excess (never the verdict): see camber.oafraction.excess_oa_penalty
    roles_cost = (
        Role.SUPPLY_AIR_TEMP,
        Role.HEAT_VALVE,
        Role.OUTDOOR_RH,
        Role.RETURN_AIR_HUMIDITY,
        Role.RETURN_AIR_DEWPOINT_TEMP,
    )

    def __init__(
        self,
        min_oa_pct: float | None = None,
        cooling_cutoff_f: float = 70.0,
        *,
        denom_min_f: float = 5.0,
        fan_gate: bool = True,
        min_oa_pct_by_month: dict | None = None,
    ):
        # min OA is building-specific (sequence); cooling cutoff is climate-ish. None = the generic
        # 20 % assumption, judged as before but never priced (0.107, #195: min_oa_source)
        self.min_oa_source = "default" if min_oa_pct is None else "configured"
        self.min_oa_pct = _DEFAULT_MIN_OA_PCT if min_oa_pct is None else float(min_oa_pct)
        self.cooling_cutoff_f = cooling_cutoff_f
        # |RAT-OAT| below this makes the temperature balance divide by ~0 (PNNL Ch.5 guard)
        self.denom_min_f = denom_min_f
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

    def _missing(self, frame: pd.DataFrame, fan_src: str) -> list:
        out = [r.value for r in (Role.OAT, Role.OA_DAMPER) if r not in frame.columns]
        if fan_src == FAN_GATE_NONE:
            out.append(Role.SUPPLY_FAN_STATUS.value)
        return out

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
            denom_min_f=self.denom_min_f,
            gate=gate,
            occ=occ,
            min_oa_by_month=self.min_oa_pct_by_month,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"fan_gate": fan_src, "_missing_optional": self._missing(frame, fan_src)},
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
                "occupancy": "trended" if occ is not None and occ.notna().any() else "assumed",
                "fan_gate": fan_src,
                "denom_min_f": self.denom_min_f,
                # samples the balance could not honestly judge, by reason
                "n_masked_fan_off": res.masked.get("fan_off", 0),
                "n_masked_small_delta_t": res.masked.get("small_delta_t", 0),
                "n_masked_out_of_range": res.masked.get("out_of_range", 0),
                "_missing_optional": self._missing(frame, fan_src),
                "min_oa_source": self.min_oa_source,
                **self._cost_metrics(frame, legacy, gate, occ),
                # only an excess-OA verdict is priced (an ok or under-ventilated unit costs nothing)
                "oa_cost_flagged": sev_excess != "ok",
            },
            summary=(
                f"{equip}: OAF median {res.oaf_median_pct:.0f}% "
                f"({res.median_oaf_cooling:.0f}% in cooling weather); {tail}"
            ),
        )

    def _cost_metrics(self, frame, legacy, gate, occ) -> dict:
        """The ``oa_cost_*`` metrics on the samples the verdict judged (0.107, #195)."""
        w = _oaf_samples(legacy, denom_min_f=self.denom_min_f, gate=gate, occ=occ)
        if w is None or w.empty:
            return {}
        mins = _sample_minima(w.index, self.min_oa_pct, self.min_oa_pct_by_month)
        out = excess_oa_penalty(
            _cost_conditions(frame, legacy),
            w["oaf"],
            mins,
            margin_pct=self.excess_margin_pct,
            observed_h=_observed_hours(legacy, ("OAT", "MixedAir", "ReturnAir")),
        )
        if self.min_oa_source == "default":
            # 0.111 (#298): the excess energy above the generic 20 % is not a figure the cost
            # estimator prices (it declines without min_oa_pct), so the finding does not carry it
            for k in (
                "oa_cost_excess_cool_btu_per_cfm",
                "oa_cost_excess_heat_btu_per_cfm",
                "oa_cost_excess_cool_kbtu",
                "oa_cost_excess_heat_kbtu",
            ):
                if k in out:
                    out[k] = None
        return out

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
        gate = self._gate(frame)[0]
        w = _oaf_samples(
            frame.rename(columns=cols), denom_min_f=self.denom_min_f, gate=gate, occ=occ
        )
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
        # 0.111 (#310): the chart's red points are of every judged (occupied, fan-on, stable)
        # sample; the finding's two shares have their own denominators -- excess OA of the
        # cooling-weather samples, under-ventilation of every judged one
        cooling = w["OAT"] > cut
        every = pd.Series(True, index=w.index)
        basis = "occupied fan-on samples" if gate is not None else "occupied samples"
        return Evidence(
            renderer="diagnostic",
            template=tmpl,
            frame=derived,
            title=f"{equip}: OA fraction",
            judged_basis=basis,
            masks={"excess OA": cooling & (y > hi_ok), "under-ventilation": y < lo_ok},
            masks_judged={
                "excess OA": (cooling, basis.replace(" samples", " cooling-weather samples")),
                "under-ventilation": (every, basis),
            },
        )
