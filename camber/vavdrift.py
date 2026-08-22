"""VAV zone-terminal drift **co-movement diagnosis** -- localize a box's drift to one subsystem.

Two drift detectors watch one VAV box, and each is deliberately narrow:

* :class:`camber.rules.vav_airflow_rule.VavAirflowDrift` -- damper-at-matched-command (a *creep* is
  flow-authority loss: a slipping/worn actuator or linkage, or rising upstream duct-static
  starvation);
* :class:`camber.rules.vav_reheat_valve_rule.VavReheatValveDrift` -- reheat-valve-at-matched-duty (a
  *creep* is reheat-coil heat-transfer loss: fouling / HW starvation / valve-authority loss).

:func:`diagnose_vav_drift` reads the individual Findings, names each drifting signal's localized
cause, flags **corroboration** when two agree, and -- the headline -- runs the **upstream-vs-box
disambiguation** that no single signal can settle. A damper creep is ambiguous between *the box's
own* actuator/linkage failing and *the plant* starving the box of upstream duct static; the airflow
detector's ``vav_upstream_starvation_suspected`` flag (set when the creep co-moves with an upstream
``DUCT_STATIC`` fall) resolves it:

* damper creep **with** upstream starvation suspected -> the drift is a **plant** symptom (fix the
  AHU, not the box) -- locus ``upstream``;
* damper creep **without** it -> the **box**'s own flow-authority loss -- locus ``airflow``.

It splits the box into an **airflow** (damper authority) and a **reheat** (coil) subsystem, reports
a ``locus`` (steady · airflow · reheat · upstream · box-wide) + a ``box_wide`` flag (both *box*
subsystems drifting). An ``upstream`` verdict is a plant symptom and is deliberately **excluded**
from ``box_wide`` -- an AHU static problem must not read as a broadly-failing box. Screening-grade:
corroboration raises priority and specificity, not the severity tier. This is the terminal-box twin
of :func:`camber.ahudrift.diagnose_ahu_drift`'s fan-power disambiguation. Pure over Findings -- no
data, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .rules.vav_reheat_valve_rule import WATER_CONFOUND

__all__ = ["VavDriftDiagnosis", "diagnose_vav_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
_DEGRADING = ("warn", "fault")

_BOX_DAMPER_CAUSE = "box damper authority loss (actuator/linkage wear)"
_UPSTREAM_CAUSE = (
    "upstream duct-static starvation -- the AHU cannot hold static, so the box damper creeps open "
    "to keep the same commanded flow; fix the plant, not the box"
)
_REHEAT_CAUSE = "reheat coil fouling / HW starvation / valve-authority loss"


@dataclass
class VavDriftDiagnosis:
    """A single per-box verdict synthesized from the two VAV drift Findings.

    ``severity`` is the worst of the contributing signals; ``locus`` says where the drift sits
    (``steady`` · ``airflow`` · ``reheat`` · ``upstream`` · ``box-wide``); ``box_wide`` is True when
    both *box* subsystems (damper airflow-authority AND reheat coil) are drifting -- ``upstream``
    is a plant symptom and never counts toward it; ``causes`` are the causes (worst-first);
    ``signals`` maps each rule to ``{drift, severity, cause, side}``; ``corroborated`` is True when
    two or more signals drift together.
    """

    equip: str
    severity: str
    locus: str
    box_wide: bool
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


def diagnose_vav_drift(findings, *, equip: str | None = None) -> VavDriftDiagnosis:
    """Synthesize the two VAV drift Findings for one box into one localized diagnosis.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``); the
    VAV ones (``vav_airflow_drift``, ``vav_reheat_valve_drift``) are picked by name, the rest
    ignored. A declined signal is a caveat, not a cause.
    """
    fs = list(findings)
    if equip is None:
        equip = next((getattr(f, "equip", "") for f in fs if getattr(f, "equip", "")), "")

    signals: dict = {}
    scored: list = []  # (severity, cause, side)
    caveats: list = []

    def _record(key: str, severity: str, drift, cause: str, side: str) -> None:
        if severity in _DEGRADING:
            signals[key] = {"drift": drift, "severity": severity, "cause": cause, "side": side}
            scored.append((severity, cause, side))
        else:
            signals[key] = {"drift": drift, "severity": severity, "cause": None, "side": side}

    # --- damper airflow-authority: the upstream-vs-box disambiguation ----------------------
    df = _get(fs, "vav_airflow_drift")
    if df is not None and not _declined(df, caveats):
        m = df.metrics
        drift = m.get("vav_airflow_drift_pct")
        if df.severity not in _DEGRADING:  # ok -> a signal, not a cause
            _record("vav_airflow_drift", df.severity, drift, _BOX_DAMPER_CAUSE, "airflow")
        elif m.get("vav_upstream_starvation_suspected"):  # a plant symptom, not a box fault
            _record("vav_airflow_drift", df.severity, drift, _UPSTREAM_CAUSE, "upstream")
            caveats.append(
                "the damper creep co-moves with an upstream duct-static fall -- attributed to "
                "plant-side starvation (the AHU/fan cannot hold static), not a box fault; "
                "check the AHU before servicing the box"
            )
        else:  # the box's own flow-authority loss
            _record("vav_airflow_drift", df.severity, drift, _BOX_DAMPER_CAUSE, "airflow")

    # --- reheat coil: heat-transfer loss (HW-reset confound re-surfaced) -------------------
    rf = _get(fs, "vav_reheat_valve_drift")
    if rf is not None and not _declined(rf, caveats):
        m = rf.metrics
        _record(
            "vav_reheat_valve_drift",
            rf.severity,
            m.get("vav_reheat_valve_drift_pct"),
            _REHEAT_CAUSE,
            "reheat",
        )
        if rf.severity in _DEGRADING:
            shift = m.get("water_supply_shift_f")
            if shift is not None and shift <= -WATER_CONFOUND:  # a HW *fall* needs more valve
                caveats.append(
                    f"hot-water supply fell {shift:+.1f}°F over the same window; that alone needs "
                    "more valve for the same reheat, so part of this creep may be a waterside-reset"
                    " effect rather than coil fouling -- check the plant HW setpoint"
                )

    # --- synthesis -------------------------------------------------------------------------
    severity = "ok"
    for sev, _c, _s in scored:
        if _RANK[sev] > _RANK[severity]:
            severity = sev
    causes = [c for _s, c, _side in sorted(scored, key=lambda t: -_RANK[t[0]])]
    corroborated = len(scored) >= 2
    sides = {side for _s, _c, side in scored}
    box_sides = {s for s in sides if s != "upstream"}  # upstream is a plant symptom, not a box side
    box_wide = len(box_sides) >= 2

    if not scored:
        locus = "steady"
    elif box_wide:
        locus = "box-wide"
    elif box_sides:
        locus = next(iter(box_sides))  # airflow or reheat; a co-scored upstream is a caveat only
    else:
        locus = "upstream"

    if corroborated:
        if "upstream" in sides:
            caveats.append(
                "the reheat coil is drifting while the damper creep looks plant-side (upstream "
                "duct-static starvation) -- these are two different problems, not one box fault; "
                "address the plant static and the coil separately"
            )
        else:
            caveats.append(
                "both box signals are drifting together -- a stronger, more localized diagnosis, "
                "but still screening-grade; confirm on a walkdown"
            )
    if box_wide:
        caveats.append(
            "both box subsystems (damper airflow-authority and reheat coil) are drifting -- a "
            "box-wide cause (or two coincident faults) is more likely than one component"
        )

    if not causes:
        summary = f"{equip}: VAV box steady vs baseline"
    else:
        tag = " (box-wide)" if box_wide else ""
        summary = f"{equip}: VAV box drift ({locus}) -- {'; '.join(causes)}{tag}"

    return VavDriftDiagnosis(
        equip=equip,
        severity=severity,
        locus=locus,
        box_wide=box_wide,
        causes=causes,
        signals=signals,
        corroborated=corroborated,
        summary=summary,
        caveats=caveats,
    )
