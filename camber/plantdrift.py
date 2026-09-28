"""Roll-ups for the plant drift families added in 0.92: ``boiler`` (#13) and ``tower`` (#14).

Each family has one or two detectors, so the roll-up is simple: the worst finding sets the
severity, and the **locus** says what the evidence points at -- the equipment itself, or a sensor
that fakes the symptom. Both detectors carry that attribution in their own metrics
(``attribution``): a boiler whose gas-in / heat-out ratio rose while the gas burned at matched
weather did not is a heat-metering problem (``heat_metering``), and a tower whose fan works harder
while its leaving-water sensor has walked away from the chiller's condenser-entering sensor is a
sensor problem (``sensor_offset``). A roll-up over only declined findings is never "steady": the
runner (:mod:`camber.driftrun`) lists such equipment as unevaluated before it gets here.

Provisional (0.92).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

__all__ = ["PlantDriftDiagnosis", "diagnose_boiler_drift", "diagnose_tower_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


@dataclass
class PlantDriftDiagnosis:
    """One equipment's verdict from a plant drift family.

    ``severity`` is the worst contributing severity; ``locus`` is ``steady``, the equipment
    (``boiler`` / ``tower``), ``sensor`` (a sensor fakes the symptom -- ``info`` severity on the
    finding, named in ``summary``) or ``unknown`` (a detection the detector could not attribute).
    ``signals`` maps each rule to ``{severity, attribution, summary}``.
    """

    equip: str
    family: str
    severity: str
    locus: str
    signals: dict = field(default_factory=dict)
    summary: str = ""
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return the diagnosis as a plain dict."""
        return asdict(self)


_SENSOR_ATTRIBUTIONS = ("heat_metering", "sensor_offset")


def _diagnose(findings, *, equip, family: str, locus_name: str) -> PlantDriftDiagnosis:
    fs = [f for f in findings if not (getattr(f, "metrics", None) or {}).get("declined")]
    caveats = [
        f"{f.rule} could not be evaluated ({(f.metrics or {}).get('reason', 'declined')})"
        for f in findings
        if (getattr(f, "metrics", None) or {}).get("declined")
    ]
    signals = {
        f.rule: {
            "severity": f.severity,
            "attribution": (f.metrics or {}).get("attribution"),
            "summary": f.summary,
        }
        for f in fs
    }
    eq = equip or (fs[0].equip if fs else "")
    if not fs:
        return PlantDriftDiagnosis(eq, family, "info", "unknown", signals, "not evaluated", caveats)
    worst = max(fs, key=lambda f: _RANK.get(f.severity, 0))
    sev = worst.severity if worst.severity in _RANK else "info"
    sensor = [f for f in fs if (f.metrics or {}).get("attribution") in _SENSOR_ATTRIBUTIONS]
    if _RANK.get(sev, 0) > 0:
        attr = (worst.metrics or {}).get("attribution")
        locus = locus_name if attr in (None, locus_name) else "unknown"
        summary = worst.summary
    elif sensor:
        locus, summary = "sensor", sensor[0].summary
    else:
        locus, summary = "steady", f"{eq}: no {family} drift"
    return PlantDriftDiagnosis(eq, family, sev, locus, signals, summary, caveats)


def diagnose_boiler_drift(findings, *, equip: str | None = None) -> PlantDriftDiagnosis:
    """Roll up the ``boiler`` family (:class:`~camber.rules.boiler_efficiency_rule.
    BoilerEfficiencyDrift`) for one boiler or hot-water plant."""
    return _diagnose(findings, equip=equip, family="boiler", locus_name="boiler")


def diagnose_tower_drift(findings, *, equip: str | None = None) -> PlantDriftDiagnosis:
    """Roll up the ``tower`` family (tower approach and fan-effort drift) for one tower."""
    return _diagnose(findings, equip=equip, family="tower", locus_name="tower")
