"""Pump-**plant** drift roll-up -- one verdict across the pumps of a plant.

:func:`camber.pumpdrift.diagnose_pump_drift` localizes *one* loop's drift to its pump or its
distribution. A real plant runs several pumps -- lead/lag, primary/secondary, per-zone -- and the
question that dispatches work is *which* pump, or whether the problem is shared.
:func:`diagnose_pump_plant` rolls the per-loop diagnoses into one plant verdict and adds the
**cross-pump reasoning** no single loop can do:

* exactly one pump drifting -> **single-pump**: stage the spare and schedule that pump/impeller;
* two or more loops drifting on the **distribution** side (low-ΔT, rising DP, a bypass) -> a shared
  central hydraulic cause is more likely than several independent pumps (a plant-wide low-ΔT, a
  decoupler bypass, a loop-control problem) -- look there **before** touching individual pumps;
* two or more pumps drifting otherwise -> **plant-wide**: look for a common-mode cause (suction
  conditions, water chemistry, a shared drive/control) before treating them one by one.

It reports a plant ``locus`` (steady · single-pump · distribution · plant-wide), the worst severity,
the per-pump diagnoses (nested), and a plain-language ``recommendation``. Screening-grade and pure
over the per-loop diagnoses -- no data, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["PumpPlantDiagnosis", "diagnose_pump_plant"]

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
_DEGRADING = ("warn", "fault")
_DIST_LOCI = ("distribution", "loop-wide")


@dataclass
class PumpPlantDiagnosis:
    """A whole-plant pump verdict rolled up from the per-loop diagnoses.

    ``severity`` is the worst across the pumps; ``locus`` says where the plant problem sits
    (``steady`` · ``single-pump`` · ``distribution`` · ``plant-wide``); ``n_degrading`` of
    ``n_pumps`` are drifting; ``pumps`` are the full per-loop diagnoses; ``recommendation`` is the
    plain-language next step.
    """

    plant: str
    severity: str
    locus: str
    n_pumps: int
    n_degrading: int
    pumps: list
    recommendation: str
    summary: str
    caveats: list

    def as_dict(self) -> dict:
        """Return the plant diagnosis (including the nested per-loop diagnoses) as plain dicts."""
        return asdict(self)


def diagnose_pump_plant(diagnoses, *, plant: str = "") -> PumpPlantDiagnosis:
    """Roll the per-loop pump diagnoses into one plant verdict with a cross-pump recommendation.

    ``diagnoses`` is an iterable of :class:`camber.pumpdrift.PumpDriftDiagnosis`, one per pump/loop.
    """
    ds = list(diagnoses)
    degrading = [d for d in ds if getattr(d, "severity", "ok") in _DEGRADING]

    severity = "ok"
    for d in degrading:
        if _RANK[d.severity] > _RANK[severity]:
            severity = d.severity

    caveats: list = []
    n_pumps, n_deg = len(ds), len(degrading)

    if not degrading:
        locus, recommendation = "steady", ""
    elif n_deg == 1:
        d0 = degrading[0]
        locus = "single-pump"
        recommendation = (
            f"service or stage around {d0.equip or 'the drifting pump'} (locus: {d0.locus})"
        )
    else:
        dist = [d for d in degrading if d.locus in _DIST_LOCI]
        if len(dist) >= 2:
            locus = "distribution"
            recommendation = (
                "investigate the central distribution (a plant-wide low-ΔT, a decoupler bypass, or "
                "a loop-control problem) before individual pumps"
            )
            caveats.append(
                "two or more loops are degrading on the distribution side -- a shared or central "
                "hydraulic cause is more likely than several independent pumps"
            )
        else:
            locus = "plant-wide"
            recommendation = (
                "multiple pumps are degrading -- look for a common-mode cause (suction conditions, "
                "water chemistry, a shared drive / control) before treating them one by one"
            )

    if not degrading:
        summary = f"{plant}: pump plant steady vs baseline".strip()
    else:
        summary = f"{plant}: {n_deg}/{n_pumps} pumps drifting ({locus}) -- {recommendation}".strip()

    return PumpPlantDiagnosis(
        plant=plant,
        severity=severity,
        locus=locus,
        n_pumps=n_pumps,
        n_degrading=n_deg,
        pumps=ds,
        recommendation=recommendation,
        summary=summary,
        caveats=caveats,
    )
