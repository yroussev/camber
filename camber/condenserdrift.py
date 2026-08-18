"""Condenser-loop drift **co-movement diagnosis** — turn scattered alerts into a work order.

Four drift detectors watch the same condenser-water loop, and each is deliberately narrow:

* :class:`camber.rules.chiller_drift_rule.ChillerApproachDrift` (condenser leg) — the chiller's own
  **heat transfer** (tube fouling / scale widens the refrigerant-to-water approach);
* :class:`camber.rules.chiller_cw_range_rule.ChillerCwRangeDrift` — the condenser-water **flow**
  (range widens when flow falls, narrows on a bypass / short-circuit);
* :class:`camber.rules.coolingtower_drift_rule.CoolingTowerApproachDrift` — the tower's **heat
  rejection** (approach to wet-bulb widens as fill fouls / airflow drops);
* :class:`camber.rules.chiller_head_pressure_rule.ChillerHeadPressureDrift` — the **high-side
  pressure** itself (discharge / condensing pressure climbs on fouling / non-condensables), read off
  the gauge and often the earliest of the four.

They fail *independently* — a tube scaling impedes heat without restricting flow; a throttled valve
does the reverse; a fouled tower is a third thing entirely — so each alone localizes a different
subsystem. But when they move **together** the diagnosis is far stronger and more specific than any
one (e.g. condenser approach *and* head pressure *and* tower approach all widening points at
system-wide scaling / water-chemistry, not one bad heat exchanger). :func:`diagnose_condenser_drift`
reads the individual Findings, names the localized cause of each drifting signal, and flags
**corroboration** when two or more agree — the thing that turns a set of screening-grade alerts into
an actionable, prioritized walkdown. It stays screening-grade: corroboration raises priority, not
the severity tier, and never becomes a dispatch-grade verdict on its own.

Head pressure carries a confound the others don't — it also climbs with entering condenser-water
temperature, which load normalization does not remove. The diagnosis uses the *tower* signal to
disambiguate: a co-moving CW-temperature rise **backed by** a degrading tower approach corroborates
a real heat-rejection fault reaching the high side, while the same rise with a quiet tower is
flagged as likely ambient / high-load, not a fault.

Pure over Findings — no data, no I/O — it composes after a ``Registry.run_periods``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .rules.chiller_drift_rule import (
    DRIFT_FAULT_F,
    DRIFT_FAULT_SIGMA,
    DRIFT_WARN_F,
    DRIFT_WARN_SIGMA,
)
from .rules.chiller_head_pressure_rule import CW_CONFOUND_WARN_F

__all__ = ["CondenserDriftDiagnosis", "diagnose_condenser_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


@dataclass
class CondenserDriftDiagnosis:
    """A single condenser-loop verdict synthesized from the individual drift Findings.

    ``severity`` is the worst of the contributing signals; ``causes`` are the localized causes
    (worst-first); ``signals`` maps each rule to ``{drift_f, severity, cause}``; ``corroborated`` is
    True when two or more condenser-side signals drift together (stronger, higher-priority, but
    still screening-grade).
    """

    equip: str
    severity: str
    causes: list
    signals: dict
    corroborated: bool
    summary: str
    caveats: list

    def as_dict(self) -> dict:
        """Return the diagnosis as a plain dict."""
        return asdict(self)


def _cond_leg_severity(metrics: dict) -> str:
    """Re-derive the *condenser leg's* severity from its drift, isolating it from the evap leg.

    ``ChillerApproachDrift`` scores cond + evap legs in one Finding whose severity is the
    worse of the two; the condenser diagnosis must judge the condenser leg alone, on the same
    (one-sided, widening) screening-grade floors that rule uses.
    """
    drift_f = metrics.get("cond_drift_f")
    if drift_f is None:
        return "ok"
    drift_sigma = metrics.get("cond_drift_sigma")
    if drift_sigma is None or drift_sigma != drift_sigma:  # missing/NaN -> judge on degF alone
        return (
            "fault" if drift_f >= DRIFT_FAULT_F else ("warn" if drift_f >= DRIFT_WARN_F else "ok")
        )
    if drift_f >= DRIFT_FAULT_F and drift_sigma >= DRIFT_FAULT_SIGMA:
        return "fault"
    if drift_f >= DRIFT_WARN_F and drift_sigma >= DRIFT_WARN_SIGMA:
        return "warn"
    return "ok"


def diagnose_condenser_drift(findings, *, equip: str | None = None) -> CondenserDriftDiagnosis:
    """Synthesize the condenser-side drift Findings for one machine into one localized diagnosis.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``); the
    condenser-side ones (``chiller_approach_drift`` condenser leg, ``chiller_cw_range_drift``,
    ``cooling_tower_approach_drift``, ``chiller_head_pressure_drift``) are picked out by rule name
    and the rest ignored. A declined signal is recorded as a caveat, not a cause.
    """
    fs = list(findings)
    by_rule = {getattr(f, "rule", None): f for f in fs}
    if equip is None:
        equip = next((getattr(f, "equip", "") for f in fs if getattr(f, "equip", "")), "")

    signals: dict = {}
    scored: list = []  # (severity, cause) for signals that are degrading
    caveats: list = []

    def _record(rule: str, severity: str, drift_f, cause: str) -> None:
        if severity in ("warn", "fault"):
            signals[rule] = {"drift_f": drift_f, "severity": severity, "cause": cause}
            scored.append((severity, cause))
        else:
            signals[rule] = {"drift_f": drift_f, "severity": severity, "cause": None}

    def _declined(f) -> bool:
        m = getattr(f, "metrics", {}) or {}
        if m.get("declined"):
            caveats.append(f"{f.rule} could not be evaluated ({m.get('reason', 'declined')})")
            return True
        return False

    # chiller condenser-approach leg — re-derived to isolate it from the evaporator leg
    caf = by_rule.get("chiller_approach_drift")
    if caf is not None and not _declined(caf):
        m = caf.metrics
        _record(
            "chiller_approach_drift",
            _cond_leg_severity(m),
            m.get("cond_drift_f"),
            "condenser tube fouling or scale",
        )

    # condenser-water range — a single-signal rule, so its Finding severity is the signal severity
    cwf = by_rule.get("chiller_cw_range_drift")
    if cwf is not None and not _declined(cwf):
        m = cwf.metrics
        cause = (
            "reduced condenser-water flow"
            if m.get("cw_range_drift_direction") == "up"
            else "condenser-water bypass or short-circuit"
        )
        _record("chiller_cw_range_drift", cwf.severity, m.get("cw_range_drift_f"), cause)

    # cooling-tower approach — single-signal, one-sided
    ctf = by_rule.get("cooling_tower_approach_drift")
    if ctf is not None and not _declined(ctf):
        m = ctf.metrics
        _record(
            "cooling_tower_approach_drift",
            ctf.severity,
            m.get("tower_approach_drift_f"),
            "cooling-tower heat rejection degrading",
        )

    # chiller head / condensing pressure — the high-side pressure on the same loop. A single-signal,
    # one-sided rule (only a rise faults), so its Finding severity is the signal severity. Placed
    # after the tower block so its confound check can see whether the tower corroborates a CW rise.
    hpf = by_rule.get("chiller_head_pressure_drift")
    if hpf is not None and not _declined(hpf):
        m = hpf.metrics
        _record(
            "chiller_head_pressure_drift",
            hpf.severity,
            m.get("head_pressure_drift_psi"),
            "condenser high-side pressure rising (fouling / non-condensables)",
        )
        # The head-pressure confound: it also climbs with entering condenser-water temperature,
        # which load normalization does not remove. When the rule reports a co-moving CW-supply
        # rise, let the tower signal disambiguate — a degrading tower explains (and corroborates)
        # the rise; a quiet tower means the rise is likely ambient / high-load, not a fault.
        cw_shift = m.get("cw_supply_shift_f")
        rising = hpf.severity in ("warn", "fault")
        if rising and cw_shift is not None and cw_shift >= CW_CONFOUND_WARN_F:
            tower = signals.get("cooling_tower_approach_drift")
            if tower is not None and tower["cause"] is not None:
                caveats.append(
                    f"head pressure and cooling-tower approach are drifting together with a "
                    f"+{cw_shift:.1f}°F entering-CW-temperature rise — consistent with degrading "
                    "heat rejection reaching the chiller high side (corroborating, not confounded)"
                )
            else:
                caveats.append(
                    f"head pressure rose with a +{cw_shift:.1f}°F entering-CW-temperature shift "
                    "while tower approach did not degrade — some or all of the high-side climb may "
                    "be ambient / high-load heat rejection rather than a fault; confirm before "
                    "attributing it to fouling or non-condensables"
                )

    severity = "ok"
    for sev, _cause in scored:
        if _RANK[sev] > _RANK[severity]:
            severity = sev
    causes = [c for _s, c in sorted(scored, key=lambda sc: -_RANK[sc[0]])]
    corroborated = len(scored) >= 2
    if corroborated:
        caveats.append(
            "multiple condenser-side signals are drifting together — a stronger, more localized "
            "diagnosis, but still screening-grade; confirm on a walkdown"
        )

    if not causes:
        summary = f"{equip}: condenser loop steady vs baseline"
    else:
        tag = " (multiple signals corroborate)" if corroborated else ""
        summary = f"{equip}: condenser-side drift — {'; '.join(causes)}{tag}"

    return CondenserDriftDiagnosis(
        equip=equip,
        severity=severity,
        causes=causes,
        signals=signals,
        corroborated=corroborated,
        summary=summary,
        caveats=caveats,
    )
