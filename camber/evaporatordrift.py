"""Evaporator-loop drift **co-movement diagnosis** — the low-side mirror of ``condenserdrift``.

Three drift detectors watch the same evaporator / low side, and each is deliberately narrow:

* :class:`camber.rules.chiller_drift_rule.ChillerApproachDrift` (evaporator leg) — the evaporator's
  **heat transfer** (fouling / scale widens the chilled-water-to-refrigerant approach);
* :class:`camber.rules.chiller_superheat_rule.ChillerSuperheatDrift` — the evaporator **feed**
  (superheat falls on overfeed / floodback, rises on starvation / undercharge);
* :class:`camber.rules.chiller_suction_pressure_rule.ChillerSuctionPressureDrift` — the **low-side
  pressure** itself (suction pressure falls on heat-transfer loss / low charge, rises on
  overfeed / flooding).

They fail *independently* — a fouled evaporator impedes heat without mis-feeding; a stuck expansion
valve mis-feeds without fouling — so each alone localizes a different thing. But when they move
**together** the diagnosis is far stronger and more specific. :func:`diagnose_evaporator_drift`
reads the individual Findings, names the localized cause of each drifting signal, and flags
**corroboration** when two or more agree — turning a set of screening-grade alerts into an
actionable, prioritized walkdown. It stays screening-grade: corroboration raises priority, not tier.

Superheat and suction pressure are two reads on the same **feed / charge axis**, so the diagnosis
cross-checks them: an *overfeed* reads as falling superheat **and** rising suction, a *starvation*
reads as rising superheat **and** falling suction. When both degrade and agree on a direction the
verdict is a strong, specific feed diagnosis; when they disagree the picture is called ambiguous
rather than asserted. This is the evaporator-side twin of the tower-disambiguates-head-pressure
check in :mod:`camber.condenserdrift`.

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

__all__ = ["EvaporatorDriftDiagnosis", "diagnose_evaporator_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


@dataclass
class EvaporatorDriftDiagnosis:
    """A single evaporator-loop verdict synthesized from the individual drift Findings.

    ``severity`` is the worst of the contributing signals; ``causes`` are the localized causes
    (worst-first); ``signals`` maps each rule to ``{drift_f, severity, cause}``; ``corroborated`` is
    True when two or more evaporator-side signals drift together (stronger, higher-priority, but
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


def _evap_leg_severity(metrics: dict) -> str:
    """Re-derive the *evaporator leg's* severity from its drift, isolating it from the cond leg.

    ``ChillerApproachDrift`` scores cond + evap legs in one Finding whose severity is the worse of
    the two; the evaporator diagnosis must judge the evaporator leg alone, on the same (one-sided,
    widening) screening-grade floors that rule uses.
    """
    drift_f = metrics.get("evap_drift_f")
    if drift_f is None:
        return "ok"
    drift_sigma = metrics.get("evap_drift_sigma")
    if drift_sigma is None or drift_sigma != drift_sigma:  # missing/NaN -> judge on degF alone
        return (
            "fault" if drift_f >= DRIFT_FAULT_F else ("warn" if drift_f >= DRIFT_WARN_F else "ok")
        )
    if drift_f >= DRIFT_FAULT_F and drift_sigma >= DRIFT_FAULT_SIGMA:
        return "fault"
    if drift_f >= DRIFT_WARN_F and drift_sigma >= DRIFT_WARN_SIGMA:
        return "warn"
    return "ok"


def diagnose_evaporator_drift(findings, *, equip: str | None = None) -> EvaporatorDriftDiagnosis:
    """Synthesize the evaporator-side drift Findings for one machine into one localized diagnosis.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``); the
    evaporator-side ones (``chiller_approach_drift`` evaporator leg, ``chiller_superheat_drift``,
    ``chiller_suction_pressure_drift``) are picked out by rule name and the rest ignored. A declined
    signal is recorded as a caveat, not a cause.
    """
    fs = list(findings)
    by_rule = {getattr(f, "rule", None): f for f in fs}
    if equip is None:
        equip = next((getattr(f, "equip", "") for f in fs if getattr(f, "equip", "")), "")

    signals: dict = {}
    scored: list = []  # (severity, cause) for signals that are degrading
    caveats: list = []
    # each read's inferred position on the feed/charge axis ("overfed" | "starved"), when degrading
    superheat_feed: str | None = None
    suction_feed: str | None = None

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

    # evaporator-approach leg — re-derived to isolate it from the condenser leg
    caf = by_rule.get("chiller_approach_drift")
    if caf is not None and not _declined(caf):
        m = caf.metrics
        _record(
            "chiller_approach_drift",
            _evap_leg_severity(m),
            m.get("evap_drift_f"),
            "evaporator tube fouling or scale",
        )

    # superheat — the feed read. A fall is overfeed (floodback), a rise is starvation.
    shf = by_rule.get("chiller_superheat_drift")
    if shf is not None and not _declined(shf):
        m = shf.metrics
        down = m.get("superheat_drift_direction") == "down"
        cause = (
            "evaporator overfed — liquid floodback risk"
            if down
            else "evaporator starved / underfed (undercharge or restricted metering)"
        )
        _record("chiller_superheat_drift", shf.severity, m.get("superheat_drift_f"), cause)
        if shf.severity in ("warn", "fault"):
            superheat_feed = "overfed" if down else "starved"

    # suction pressure — the low-side pressure read. A fall is heat-transfer loss / low charge, a
    # rise is overfeed / flooding.
    spf = by_rule.get("chiller_suction_pressure_drift")
    if spf is not None and not _declined(spf):
        m = spf.metrics
        up = m.get("suction_pressure_drift_direction") == "up"
        cause = (
            "evaporator overfeed / flooding"
            if up
            else "evaporator heat-transfer loss or low charge"
        )
        _record(
            "chiller_suction_pressure_drift",
            spf.severity,
            m.get("suction_pressure_drift_psi"),
            cause,
        )
        if spf.severity in ("warn", "fault"):
            suction_feed = "overfed" if up else "starved"

    # cross-check the two feed reads when both are degrading
    if superheat_feed is not None and suction_feed is not None:
        if superheat_feed == suction_feed:
            caveats.append(
                f"superheat and suction pressure agree the evaporator is {superheat_feed} — a "
                "strong, specific feed diagnosis (still screening-grade; confirm on a walkdown)"
            )
        else:
            caveats.append(
                "superheat and suction pressure disagree on the feed direction (one reads overfed, "
                "the other starved) — the evaporator-feed picture is ambiguous; confirm first"
            )

    severity = "ok"
    for sev, _cause in scored:
        if _RANK[sev] > _RANK[severity]:
            severity = sev
    causes = [c for _s, c in sorted(scored, key=lambda sc: -_RANK[sc[0]])]
    corroborated = len(scored) >= 2
    if corroborated:
        caveats.append(
            "multiple evaporator-side signals are drifting together — a stronger, more localized "
            "diagnosis, but still screening-grade; confirm on a walkdown"
        )

    if not causes:
        summary = f"{equip}: evaporator loop steady vs baseline"
    else:
        tag = " (multiple signals corroborate)" if corroborated else ""
        summary = f"{equip}: evaporator-side drift — {'; '.join(causes)}{tag}"

    return EvaporatorDriftDiagnosis(
        equip=equip,
        severity=severity,
        causes=causes,
        signals=signals,
        corroborated=corroborated,
        summary=summary,
        caveats=caveats,
    )
