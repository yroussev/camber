"""Whole-machine chiller drift **roll-up** — one per-chiller verdict from both side diagnoses.

:mod:`camber.condenserdrift` and :mod:`camber.evaporatordrift` each localize *one* side of the
refrigerant circuit. :func:`diagnose_chiller_drift` rolls both into a single per-chiller diagnosis
and adds the **cross-side reasoning** neither side can do alone:

* only the **condenser** side degrading → the problem is localized to the condenser loop (tube
  fouling / scale, condenser-water flow, tower heat rejection, high-side pressure);
* only the **evaporator** side degrading → localized to the evaporator (fouling, feed / metering);
* **both** sides degrading together → a *circuit-wide* cause — refrigerant charge, non-condensables,
  or a compressor / metering fault — showing on both heat exchangers rather than one bad exchanger.

Liquid-line **subcooling** is folded in as the dedicated charge signal: it barely moves an approach,
so it has its own detector, and a subcooling drift *alongside* both sides moving corroborates a
charge / inventory problem specifically. The roll-up reports a ``locus`` (steady · condenser ·
evaporator · charge · whole-machine) and a ``machine_wide`` flag so a screening pass can separate
"one heat exchanger needs a walkdown" from "put a gauge set on the whole machine".

It stays screening-grade: it re-uses the side diagnoses unchanged and only adds the cross-side
synthesis; it never raises a severity tier on its own. Pure over Findings — no data, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .condenserdrift import CondenserDriftDiagnosis, diagnose_condenser_drift
from .evaporatordrift import EvaporatorDriftDiagnosis, diagnose_evaporator_drift

__all__ = ["ChillerDriftDiagnosis", "diagnose_chiller_drift"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


@dataclass
class ChillerDriftDiagnosis:
    """A whole-chiller verdict rolled up from the condenser and evaporator side diagnoses.

    ``severity`` is the worst of the two sides and the charge signal; ``locus`` says where the drift
    sits (``steady`` · ``condenser`` · ``evaporator`` · ``charge`` · ``whole-machine``);
    ``machine_wide`` is True when both sides are drifting together (a circuit-wide cause is more
    likely than one bad heat exchanger); ``condenser`` and ``evaporator`` are the full side
    diagnoses; ``charge`` is the subcooling read (or None); ``causes`` are the side-tagged causes,
    worst-first.
    """

    equip: str
    severity: str
    locus: str
    machine_wide: bool
    condenser: CondenserDriftDiagnosis
    evaporator: EvaporatorDriftDiagnosis
    charge: dict | None
    causes: list
    summary: str
    caveats: list

    def as_dict(self) -> dict:
        """Return the diagnosis (including the nested side diagnoses) as plain dicts."""
        return asdict(self)


def _worst(*severities: str) -> str:
    out = "ok"
    for s in severities:
        if _RANK.get(s, 0) > _RANK[out]:
            out = s
    return out


def diagnose_chiller_drift(findings, *, equip: str | None = None) -> ChillerDriftDiagnosis:
    """Roll the condenser + evaporator drift diagnoses into one per-chiller verdict.

    ``findings`` is an iterable of :class:`camber.rules.base.Finding` (from ``run_periods``) for one
    chiller. The condenser- and evaporator-side Findings are dispatched to the two side diagnoses,
    liquid-line subcooling is read as the charge signal, and the cross-side synthesis is layered on
    top. Findings for other rules are ignored by the side diagnoses as before.
    """
    fs = list(findings)
    cond = diagnose_condenser_drift(fs, equip=equip)
    evap = diagnose_evaporator_drift(fs, equip=equip)
    if equip is None:
        equip = cond.equip or evap.equip

    caveats: list = []

    # subcooling — the dedicated charge signal (folded in here, not in either side diagnosis)
    charge: dict | None = None
    scf = next((f for f in fs if getattr(f, "rule", None) == "chiller_subcooling_drift"), None)
    if scf is not None:
        m = getattr(scf, "metrics", {}) or {}
        if m.get("declined"):
            caveats.append(
                f"chiller_subcooling_drift could not be evaluated ({m.get('reason', 'declined')})"
            )
        elif scf.severity in ("warn", "fault"):
            down = m.get("subcooling_drift_direction") == "down"
            cause = (
                "refrigerant undercharge or leak"
                if down
                else "refrigerant overcharge or non-condensables"
            )
            charge = {
                "severity": scf.severity,
                "direction": m.get("subcooling_drift_direction"),
                "cause": cause,
                "drift_f": m.get("subcooling_drift_f"),
            }

    cond_deg = cond.severity in ("warn", "fault")
    evap_deg = evap.severity in ("warn", "fault")
    charge_deg = charge is not None

    severity = _worst(cond.severity, evap.severity, charge["severity"] if charge else "ok")
    machine_wide = cond_deg and evap_deg

    if not (cond_deg or evap_deg or charge_deg):
        locus = "steady"
    elif machine_wide:
        locus = "whole-machine"
    elif cond_deg:
        locus = "condenser"
    elif evap_deg:
        locus = "evaporator"
    else:
        locus = "charge"

    # combined causes, side-tagged, ordered by each side's severity (worst side first)
    sides: list = []
    if cond_deg:
        sides.append((cond.severity, "condenser", cond.causes))
    if evap_deg:
        sides.append((evap.severity, "evaporator", evap.causes))
    if charge is not None:
        sides.append((charge["severity"], "charge", [charge["cause"]]))
    sides.sort(key=lambda s: -_RANK[s[0]])
    causes = [f"{side}: {c}" for _sev, side, cs in sides for c in cs]

    # cross-side synthesis
    if machine_wide:
        note = (
            "both the condenser and evaporator sides are drifting together — this points at a "
            "circuit-wide cause (refrigerant charge, non-condensables, or a compressor / metering "
            "fault) rather than a single fouled heat exchanger"
        )
        if charge is not None:
            note += (
                f"; subcooling has also drifted ({charge['cause']}), corroborating a "
                "charge / inventory problem"
            )
        note += " — gauge the whole machine before dispatching on one exchanger"
        caveats.append(note)
    elif charge is not None and (cond_deg or evap_deg):
        caveats.append(
            f"subcooling has drifted ({charge['cause']}) alongside a one-sided "
            f"{'condenser' if cond_deg else 'evaporator'} signal — read the charge signal together "
            "with that side before acting"
        )
    elif charge is not None:
        caveats.append(
            f"subcooling has drifted ({charge['cause']}) while both heat-exchanger sides look "
            "steady — a charge / inventory signal that barely moves an approach; confirm on a gauge"
        )

    if not causes:
        summary = f"{equip}: chiller drift — all loops steady vs baseline"
    else:
        tag = " — circuit-wide, gauge the whole machine" if machine_wide else ""
        summary = f"{equip}: chiller drift ({locus}) — {'; '.join(causes)}{tag}"

    return ChillerDriftDiagnosis(
        equip=equip,
        severity=severity,
        locus=locus,
        machine_wide=machine_wide,
        condenser=cond,
        evaporator=evap,
        charge=charge,
        causes=causes,
        summary=summary,
        caveats=caveats,
    )
