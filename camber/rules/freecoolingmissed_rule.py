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

**Why free cooling was missed (0.98, #88).** With an OA damper *and* mixed/return-air temperatures
mapped, the rule separates the two causes a report must not confuse. On missed samples where the
temperature balance is well conditioned (``|OAT - RAT| >= 5 °F``), a damper commanded at or above
``cmd_open_pct`` while the measured OA fraction stays below ``oaf_open_pct`` is a damper that does
not deliver what it is told (stuck, failed actuator, broken linkage). When that holds on at least
``stuck_min_share_pct`` % of those samples and ``stuck_min_hours`` hours, ``missed_cause`` is
``damper_not_delivering``; else, with a damper signal, it is ``economizer_not_commanded`` (the
command stayed below open); with no damper signal it is ``undetermined``. These metrics are
additive: severity is unchanged. ``commanded_open_oaf_median_pct`` (the OA fraction delivered
while commanded open) below ``stuck_low_oaf_pct`` reads as "stuck low", above it as "stuck part
open".

**Economizer low-limit lockout (0.100).** Many sequences lock the economizer out below a low OAT
limit (freeze protection), holding the OA damper at minimum by design, so mechanical cooling in
that weather is not missed free cooling. With ``low_limit_f`` set, samples with OAT below it are
taken out of the free-cooling opportunity (they are neither available nor missed) and counted in
``low_limit_excluded_hours``, with a caveat; ``low_limit_cooling_hours`` says how many of them ran
mechanical cooling. The test is :mod:`camber.freecooling`'s, the one
:func:`~camber.freecooling.free_cooling_opportunity` applies with the same parameter. ``None``
(the default) keeps the pre-0.100 finding byte-identical, with none of the low-limit metrics.

**Fan-gated by default (0.102, #120).** With the supply fan stopped there is no air moving across
the cooling coil and nothing for an economizer to do, so a fan-off sample is neither free-cooling
opportunity nor missed free cooling. Only fan-on samples are judged (fan status, else fan speed,
else airflow -- see :func:`camber.schedules.fan_on_mask`, the gate the other air-side rules use);
``n_masked_fan_off`` counts the free-cooling-weather samples taken out. A unit that trends no fan
signal is judged ungated, as before, and the finding says so (the ``fan_gate`` metric, and
``supply_fan_status`` in ``_missing_optional``). ``fan_gate=False`` turns the gate off.
"""

from __future__ import annotations

import pandas as pd

from ..freecooling import (
    DEFAULT_FREE_COOLING_HIGH_LIMIT_F,
    ECON_DAMPER_MIN_PCT,
    ECON_MIN_DELTA_F,
    ECON_OAF_MIN_PCT,
    _check_low_limit,
    _free_cooling_weather,
    integrated_economizer_mask,
)
from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, fan_on_mask
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
    roles_optional = (
        Role.OA_DAMPER,
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        # 0.102 (#120) fan-on gate: status, else speed, else airflow (camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
    )

    def __init__(
        self,
        *,
        high_limit_f: float = DEFAULT_FREE_COOLING_HIGH_LIMIT_F,
        active: float = 5.0,  # cooling-valve % above which mechanical cooling is running
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
        # 0.98 (#88): why free cooling was missed -- additive metrics, severity unchanged
        cmd_open_pct: float = ECON_DAMPER_MIN_PCT,
        oaf_open_pct: float = ECON_OAF_MIN_PCT,
        stuck_min_share_pct: float = 20.0,
        stuck_min_hours: float = 24.0,
        stuck_low_oaf_pct: float = 30.0,
        # 0.100: the economizer low-limit lockout (°F); None = no lockout, as before
        low_limit_f: float | None = None,
        # 0.102 (#120): judge fan-on samples only (when the unit trends a fan signal)
        fan_gate: bool = True,
    ):
        _check_low_limit(high_limit_f, low_limit_f)
        self.fan_gate = fan_gate
        self.high_limit_f = high_limit_f
        self.low_limit_f = low_limit_f
        self.active = active
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.cmd_open_pct = cmd_open_pct
        self.oaf_open_pct = oaf_open_pct
        self.stuck_min_share_pct = stuck_min_share_pct
        self.stuck_min_hours = stuck_min_hours
        self.stuck_low_oaf_pct = stuck_low_oaf_pct

    def _fan(self, frame: pd.DataFrame):
        """``(fan-on mask | None, source label)`` -- ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    def _judged(self, frame: pd.DataFrame) -> dict:
        """The samples this rule judges, shared by :meth:`analyze` and :meth:`evidence` so the
        chart is drawn from exactly the samples behind the verdict.

        ``available`` (free-cooling weather, both inputs present, fan on when gated), ``missed``
        and ``integrated`` (subsets of it; ``integrated`` is ``None`` when the unit trends neither
        an OA damper nor MAT + RAT), ``cool`` (valve %), the fan-gate label and the
        free-cooling-weather samples the gate took out.
        """
        oat, cool = frame[Role.OAT], normalize_percent(frame[Role.COOL_VALVE])
        weather, below_low = _free_cooling_weather(oat, self.high_limit_f, self.low_limit_f)
        valid = oat.notna() & cool.notna()
        fan, fan_src = self._fan(frame)
        if fan is not None:
            on = pd.Series(fan.to_numpy(dtype=bool), index=frame.index)
            n_fan_off = int((weather & valid & ~on).sum())
            valid = valid & on
        else:
            n_fan_off = 0
        available = weather & valid
        running = available & (cool > self.active)
        econ = integrated_economizer_mask(
            oat,
            damper=frame[Role.OA_DAMPER] if Role.OA_DAMPER in frame.columns else None,
            mat=frame[Role.MIXED_AIR_TEMP] if Role.MIXED_AIR_TEMP in frame.columns else None,
            rat=frame[Role.RETURN_AIR_TEMP] if Role.RETURN_AIR_TEMP in frame.columns else None,
        )
        integrated = None if econ is None else running & econ
        missed = running if integrated is None else running & ~integrated
        return {
            "cool": cool,
            "available": available,
            "missed": missed,
            "integrated": integrated,
            "locked": below_low & valid,
            "fan_gate": fan_src,
            "n_masked_fan_off": n_fan_off,
        }

    def _missing(self, frame: pd.DataFrame, fan_src: str) -> list:
        """Optional inputs truly absent -- the three fan signals are alternatives, so one is
        named only when none is present (pre-empts the runner's backstop)."""
        plain = (Role.OA_DAMPER, Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP)
        out = [r.value for r in plain if r not in frame.columns]
        if fan_src == FAN_GATE_NONE:
            out.append(Role.SUPPLY_FAN_STATUS.value)
        return out

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        j = self._judged(frame)
        cool, available = j["cool"], j["available"]
        fan_metrics: dict = {"fan_gate": j["fan_gate"], "n_masked_fan_off": j["n_masked_fan_off"]}
        missing = self._missing(frame, j["fan_gate"])
        if missing:  # the fan signals are alternatives: pre-empt the runner's per-role backstop
            fan_metrics["_missing_optional"] = missing
        n_avail = int(available.sum())
        step_h = _step_hours(frame.index)
        low = self._low_limit_metrics(j["locked"], cool, step_h)
        if n_avail == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=(
                    f"{equip}: no free-cooling weather in the window"
                    + (" with the supply fan on" if j["n_masked_fan_off"] else "")
                ),
                metrics={**low, **fan_metrics},
            )
        econ = j["integrated"]
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
            integrated = econ
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
        missed = j["missed"]
        n_integrated = int(integrated.sum())
        pct = 100.0 * float(missed.sum()) / n_avail
        hours = None if step_h is None else round(n_avail * step_h, 1)
        span = (
            f"{hours:g} free-cooling hours"
            if hours is not None
            else f"{n_avail} free-cooling samples"
        )
        sev = "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"
        cause = self._missed_cause(frame, missed, step_h)
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
                **cause,
                **low,
                **fan_metrics,
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
                + (
                    f"; {low['low_limit_excluded_hours']:g} h below the {self.low_limit_f:g}°F "
                    "low-limit lockout not judged"
                    if low and low["low_limit_excluded_hours"]
                    else ""
                )
            ),
            caveats=caveats + self._low_limit_caveat(low),
        )

    def _low_limit_metrics(self, locked: pd.Series, cool: pd.Series, step_h) -> dict:
        """0.100: the samples below the economizer low-limit lockout, taken out of the
        opportunity. Empty (no metrics at all) without a ``low_limit_f``."""
        if self.low_limit_f is None:
            return {}
        n = int(locked.sum())
        n_cool = int((locked & (cool > self.active)).sum())
        return {
            "low_limit_f": self.low_limit_f,
            "n_low_limit_excluded_samples": n,
            "low_limit_excluded_hours": None if step_h is None else round(n * step_h, 1),
            "low_limit_cooling_hours": None if step_h is None else round(n_cool * step_h, 1),
        }

    def _low_limit_caveat(self, low: dict) -> list:
        if not low or not low["n_low_limit_excluded_samples"]:
            return []
        span = (
            f"{low['low_limit_excluded_hours']:g} hours"
            if low["low_limit_excluded_hours"] is not None
            else f"{low['n_low_limit_excluded_samples']} samples"
        )
        cooled = (
            f" (mechanical cooling ran in {low['low_limit_cooling_hours']:g} of them)"
            if low["low_limit_cooling_hours"]
            else ""
        )
        return [
            f"{span} with OAT below the {self.low_limit_f:g} °F economizer low-limit lockout are "
            f"not counted as free-cooling weather{cooled}: the sequence holds the OA damper at "
            "its minimum there by design. The lockout setting itself is not verified -- confirm "
            "it against the unit's sequence of operations"
        ]

    def _missed_cause(self, frame: pd.DataFrame, missed: pd.Series, step_h) -> dict:
        """0.98 (#88): why free cooling was missed -- the damper was told to open and did not
        deliver outside air, or the economizer never commanded it open. See the module docstring."""
        n_missed = int(missed.sum())
        out: dict = {
            "missed_cause": None,
            "commanded_open_pct": None,
            "commanded_open_hours": None,
            "commanded_open_oaf_median_pct": None,
            "missed_damper_cmd_median_pct": None,
            "cmd_open_pct": self.cmd_open_pct,
            "oaf_open_pct": self.oaf_open_pct,
            "stuck_min_share_pct": self.stuck_min_share_pct,
            "stuck_min_hours": self.stuck_min_hours,
            "stuck_low_oaf_pct": self.stuck_low_oaf_pct,
        }
        if n_missed == 0:
            return out
        if Role.OA_DAMPER not in frame.columns:
            out["missed_cause"] = "undetermined"
            return out
        cmd = normalize_percent(frame[Role.OA_DAMPER].astype(float))
        cmd_missed = cmd[missed].dropna()
        if not cmd_missed.empty:
            out["missed_damper_cmd_median_pct"] = round(float(cmd_missed.median()), 1)
        have_temps = Role.MIXED_AIR_TEMP in frame.columns and Role.RETURN_AIR_TEMP in frame.columns
        if not have_temps:
            # the integrated-economizer test used the command alone, so every missed sample has
            # the damper commanded below full open: the economizer did not command free cooling
            out["missed_cause"] = "economizer_not_commanded"
            return out
        oat = frame[Role.OAT].astype(float)
        rat = frame[Role.RETURN_AIR_TEMP].astype(float)
        mat = frame[Role.MIXED_AIR_TEMP].astype(float)
        dt = rat - oat
        stable = (dt.abs() >= ECON_MIN_DELTA_F).fillna(False) & missed & cmd.notna()
        n_stable = int(stable.sum())
        if n_stable == 0:
            out["missed_cause"] = "undetermined"
            return out
        oaf = 100.0 * (rat - mat) / dt.where(stable)
        open_cmd = stable & (cmd >= self.cmd_open_pct) & (oaf < self.oaf_open_pct)
        n_open = int(open_cmd.sum())
        share = 100.0 * n_open / n_stable
        hours = None if step_h is None else round(n_open * step_h, 1)
        out["commanded_open_pct"] = round(share, 1)
        out["commanded_open_hours"] = hours
        if n_open:
            out["commanded_open_oaf_median_pct"] = round(float(oaf[open_cmd].median()), 1)
        enough_hours = hours is not None and hours >= self.stuck_min_hours
        if share >= self.stuck_min_share_pct and enough_hours:
            out["missed_cause"] = "damper_not_delivering"
        elif share >= self.stuck_min_share_pct:
            out["missed_cause"] = "undetermined"  # the pattern is there, on too few hours
        else:
            out["missed_cause"] = "economizer_not_commanded"
        return out

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: cooling-valve position vs OAT on the samples the rule judges (0.102, #120).

        Drawn from :meth:`_judged`, the samples behind the verdict: free-cooling weather (between
        the low limit, when set, and ``high_limit_f``), both inputs present, and the supply fan on
        when the gate applies -- fan-off hours, warm weather and low-limit lockout hours are not
        plotted. The expected band is the valve at or below ``active`` %. ``mask`` is the missed
        samples ``missed_pct`` counts, and the renderer shades exactly those (#114's
        ``violating=``): integrated-economizer samples (cooling on ~100 % outside air) sit above
        the band but are not red, so the chart's out-of-band share equals ``missed_pct``.
        Axis labels come from :func:`camber.charts._labels.role_label`.
        """
        import numpy as np

        from ..charts.diagnostic import DiagnosticTemplate
        from ..charts.evidence import Evidence

        j = self._judged(frame)
        keep = j["available"]
        if not keep.any():
            return None
        derived = pd.DataFrame({Role.OAT: frame[Role.OAT][keep], Role.COOL_VALVE: j["cool"][keep]})
        active = float(self.active)

        def expected(xv):
            return np.zeros(len(xv)), np.full(len(xv), active)

        window = (
            f"{self.low_limit_f:g}-{self.high_limit_f:g}°F"
            if self.low_limit_f is not None
            else f"< {self.high_limit_f:g}°F"
        )
        gate = "fan on, " if j["fan_gate"] not in (FAN_GATE_NONE, "off") else ""
        tmpl = DiagnosticTemplate(
            f"Free cooling missed ({gate}OAT {window}, valve > {self.active:g}%)",
            Role.OAT,
            Role.COOL_VALVE,
            expected,
        )
        return Evidence(
            renderer="diagnostic",
            template=tmpl,
            frame=derived,
            mask=j["missed"][keep],
            label="missed free cooling",
            title=f"{equip}: cooling vs OAT (free-cooling)",
        )
