"""The ``dx`` drift family: DX / heat-pump refrigerant-side detectors and their roll-up (0.93).

Three frozen-baseline detectors compare a DX unit or heat pump with its own fault-free period at
matched conditions (outdoor-air and return-air temperature):

* :class:`~camber.rules.dx_charge_rule.DXRefrigerantCharge` -- liquid subcooling (charge; #40),
* :class:`~camber.rules.dx_airflow_rule.DXIndoorAirflow` -- the evaporator temperature split
  (indoor airflow; #40),
* :class:`~camber.rules.dx_discharge_superheat_rule.DischargeSuperheatDrift` -- compressor
  discharge superheat (#6).

:func:`diagnose_dx_drift` rolls one unit's findings into a verdict whose **locus** names what the
worst detection points at: ``refrigerant_circuit`` (charge, discharge superheat) or
``indoor_airflow``; ``steady`` when every detector that ran is quiet. Equipment on which every
detector declined never reaches the roll-up (:mod:`camber.driftrun` lists it as unevaluated).

Provisional (0.93).
"""

from __future__ import annotations

from .plantdrift import PlantDriftDiagnosis

__all__ = ["DX_DETECTORS", "diagnose_dx_drift"]

#: The detectors the ``dx`` family runs, by rule name (roll-up order).
DX_DETECTORS: tuple = (
    "dx_refrigerant_charge",
    "dx_indoor_airflow",
    "discharge_superheat_drift",
)

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
_LOCI = ("refrigerant_circuit", "indoor_airflow")


def diagnose_dx_drift(findings, *, equip: str | None = None) -> PlantDriftDiagnosis:
    """Roll up the ``dx`` family's findings for one unit (see the module docstring)."""
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
        return PlantDriftDiagnosis(eq, "dx", "info", "unknown", signals, "not evaluated", caveats)
    worst = max(fs, key=lambda f: _RANK.get(f.severity, 0))
    sev = worst.severity if worst.severity in _RANK else "info"
    if _RANK.get(sev, 0) > 0:
        attr = (worst.metrics or {}).get("attribution")
        locus = attr if attr in _LOCI else "unknown"
        summary = worst.summary
    else:
        sev = "ok" if sev == "ok" else sev
        locus, summary = "steady", f"{eq}: no DX refrigerant-side drift"
    return PlantDriftDiagnosis(eq, "dx", sev, locus, signals, summary, caveats)
