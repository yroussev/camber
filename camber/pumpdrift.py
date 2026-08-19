"""Pump / hydronic-loop drift **co-movement diagnosis** -- localize a loop's drift to one subsystem.

Four drift detectors watch one hydronic loop, and each is deliberately narrow:

* :class:`camber.rules.pump_flow_rule.PumpFlowDrift` -- flow-at-matched-speed (a *deficit* is the
  wear/restriction signal);
* :class:`camber.rules.pump_head_rule.PumpHeadDrift` -- head-at-matched-speed (a *deficit* is the
  direct pump-condition signal);
* :class:`camber.rules.loop_deltat_rule.LoopDeltaTDrift` -- the loop's ΔT (a *collapse* is low-ΔT
  syndrome, a *widening* is starvation);
* :class:`camber.rules.loop_dp_rule.LoopDPDrift` -- the loop's differential pressure (a *rise* is
  added system resistance, a *fall* is a bypass);
* :class:`camber.rules.pump_power_rule.PumpPowerDrift` -- the pump's power-at-matched-flow (a *rise*
  is wire-to-water efficiency loss), a corroborating mechanical (pump-side) signal.

:func:`diagnose_pump_drift` reads the individual Findings, names each drifting signal's localized
cause, flags **corroboration** when two or more agree, and -- the headline -- runs the
**flow-vs-head disambiguation** that no single signal can do. A flow deficit alone is ambiguous
between *the pump getting weaker* and *the system getting more restrictive*; head resolves it:

* flow deficit **and** head deficit at matched speed -> **the pump itself** (impeller / wear-ring /
  cavitation) -- a corroborated mechanical fault;
* flow deficit with head **steady** -> **the distribution** (a throttled / stuck-closed valve
  downstream), not pump wear -- look at the loop, not the impeller;
* flow deficit with **no head point** mapped -> ambiguous, and the diagnosis says so.

It splits the loop into a **mechanical** side (the pump) and a **hydraulic** side (the
distribution), reports a ``locus`` (steady · pump · distribution · loop-wide) and a ``loop_wide``
flag (both sides
drifting -> a loop-wide cause), and stays screening-grade -- corroboration raises priority and
specificity, not the severity tier. Pure over Findings -- no data, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["PumpDriftDiagnosis", "diagnose_pump_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
_DEGRADING = ("warn", "fault")


@dataclass
class PumpDriftDiagnosis:
    """A single per-loop verdict synthesized from the four pump/hydronic drift Findings.

    ``severity`` is the worst of the contributing signals; ``locus`` says where the drift sits
    (``steady`` · ``pump`` · ``distribution`` · ``loop-wide``); ``loop_wide`` is True when both the
    mechanical (pump) and hydraulic (distribution) sides are drifting; ``causes`` are the localized
    causes (worst-first); ``signals`` maps each rule to ``{drift, severity, cause, side}``;
    ``corroborated`` is True when two or more signals drift together.
    """

    equip: str
    severity: str
    locus: str
    loop_wide: bool
    causes: list
    signals: dict
    corroborated: bool
    summary: str
    caveats: list

    def as_dict(self) -> dict:
        """Return the diagnosis as a plain dict."""
        return asdict(self)


def _get(findings, rule):
    return next((f for f in findings if getattr(f, "rule", None) == rule), None)


def _declined(f, caveats) -> bool:
    m = getattr(f, "metrics", {}) or {}
    if m.get("declined"):
        caveats.append(f"{f.rule} could not be evaluated ({m.get('reason', 'declined')})")
        return True
    return False


def diagnose_pump_drift(findings, *, equip: str | None = None) -> PumpDriftDiagnosis:
    """Synthesize the four pump/hydronic drift Findings for one loop into one localized diagnosis.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``); the
    pump/hydronic ones (``pump_flow_drift``, ``pump_head_drift``, ``loop_deltat_drift``,
    ``loop_dp_drift``, ``pump_power_drift``) are picked out by rule name and the rest ignored. A
    declined signal is a caveat, not a cause.
    """
    fs = list(findings)
    if equip is None:
        equip = next((getattr(f, "equip", "") for f in fs if getattr(f, "equip", "")), "")

    signals: dict = {}
    scored: list = []  # (severity, cause, side)
    caveats: list = []

    def _record(rule: str, severity: str, drift, cause: str, side: str) -> None:
        if severity in _DEGRADING:
            signals[rule] = {"drift": drift, "severity": severity, "cause": cause, "side": side}
            scored.append((severity, cause, side))
        else:
            signals[rule] = {"drift": drift, "severity": severity, "cause": None, "side": side}

    # --- pump head first: it disambiguates the flow deficit --------------------------------------
    hf = _get(fs, "pump_head_drift")
    head_deg = False
    head_evaluated = False  # mapped and scored (not declined / info)
    if hf is not None and not _declined(hf, caveats):
        head_evaluated = hf.severity != "info"
        head_deg = hf.severity in _DEGRADING
        if head_deg:
            _record(
                "pump_head_drift",
                hf.severity,
                hf.metrics.get("pump_head_drift_psi"),
                "pump head deficit (worn impeller / cavitation)",
                "pump",
            )

    # --- pump flow: category depends on head -----------------------------------------------------
    ff = _get(fs, "pump_flow_drift")
    if ff is not None and not _declined(ff, caveats):
        if ff.severity in _DEGRADING:
            drift = ff.metrics.get("pump_flow_drift_gpm")
            if head_deg:
                cause, side = "pump wear (impeller / wear-ring / cavitation)", "pump"
                caveats.append(
                    "flow and head are both down at matched speed -- the pump itself, corroborated"
                )
            elif head_evaluated:
                cause, side = (
                    "reduced flow from added system resistance (a throttled valve downstream)",
                    "distribution",
                )
                caveats.append(
                    "flow deficit with steady head -- added system resistance, not pump wear; "
                    "check the distribution, not the impeller"
                )
            else:
                cause, side = "flow deficit -- pump wear or added system resistance", "pump"
                caveats.append(
                    "flow deficit but no pump-head point is mapped to disambiguate pump wear "
                    "from system resistance"
                )
            _record("pump_flow_drift", ff.severity, drift, cause, side)

    # --- loop ΔT: hydraulic ----------------------------------------------------------------------
    df = _get(fs, "loop_deltat_drift")
    if df is not None and not _declined(df, caveats):
        m = df.metrics
        cause = (
            "low-ΔT syndrome (overpumping / fouled coils / stuck valves)"
            if m.get("loop_deltat_drift_direction") == "down"
            else "underflow / starvation"
        )
        _record(
            "loop_deltat_drift", df.severity, m.get("loop_deltat_drift_f"), cause, "distribution"
        )

    # --- loop DP: hydraulic ----------------------------------------------------------------------
    pf = _get(fs, "loop_dp_drift")
    if pf is not None and not _declined(pf, caveats):
        m = pf.metrics
        cause = (
            "rising system resistance / valve-authority loss"
            if m.get("loop_dp_drift_direction") == "up"
            else "bypass / short-circuit"
        )
        _record("loop_dp_drift", pf.severity, m.get("loop_dp_drift"), cause, "distribution")

    # --- pump power: mechanical (a rise is efficiency loss) --------------------------------------
    wf = _get(fs, "pump_power_drift")
    if wf is not None and not _declined(wf, caveats):
        if wf.severity in _DEGRADING:
            _record(
                "pump_power_drift",
                wf.severity,
                wf.metrics.get("pump_power_drift_kw"),
                "pump efficiency loss (wire-to-water / mechanical drag / recirculation)",
                "pump",
            )

    # --- synthesis -------------------------------------------------------------------------------
    severity = "ok"
    for sev, _c, _s in scored:
        if _RANK[sev] > _RANK[severity]:
            severity = sev
    causes = [c for _s, c, _side in sorted(scored, key=lambda t: -_RANK[t[0]])]
    corroborated = len(scored) >= 2

    pump_deg = any(side == "pump" for _s, _c, side in scored)
    dist_deg = any(side == "distribution" for _s, _c, side in scored)
    loop_wide = pump_deg and dist_deg
    if not scored:
        locus = "steady"
    elif loop_wide:
        locus = "loop-wide"
    elif pump_deg:
        locus = "pump"
    else:
        locus = "distribution"

    if corroborated:
        caveats.append(
            "multiple loop signals are drifting together -- a stronger, more localized diagnosis, "
            "but still screening-grade; confirm on a walkdown"
        )
    if loop_wide:
        caveats.append(
            "both the pump (mechanical) and the distribution (hydraulic) sides are drifting -- a "
            "loop-wide cause is more likely than one component"
        )

    if not causes:
        summary = f"{equip}: pump loop steady vs baseline"
    else:
        tag = " (loop-wide)" if loop_wide else ""
        summary = f"{equip}: pump-loop drift ({locus}) -- {'; '.join(causes)}{tag}"

    return PumpDriftDiagnosis(
        equip=equip,
        severity=severity,
        locus=locus,
        loop_wide=loop_wide,
        causes=causes,
        signals=signals,
        corroborated=corroborated,
        summary=summary,
        caveats=caveats,
    )
