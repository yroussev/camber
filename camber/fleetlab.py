"""Generated labeled multi-zone VAV fleets + a G36 reset validation harness (clean-room).

The multi-zone **rogue-zone census**, **cohort-starvation**, and **reset-effectiveness** detectors
(the G36 Trim-&-Respond fleet family) could never be *accuracy*-scored on real data: validating them
needs a fleet of zones with per-zone reset **requests** and a served-by topology, and no public
labeled multi-zone-fleet fault dataset is vendorable (see ``docs/VALIDATION.md``). This module
closes that gap the only license-clean way the G36 authors intend — by *generating* the fleet from
public ASHRAE Guideline 36 Trim-&-Respond logic itself (§5.1.14 / §5.14.8), never copying any
encumbered simulation.

The generated fleet is **physically coherent**: each zone's per-cycle reset requests are produced by
the same G36 request rules the detectors consume, those requests are aggregated per air handler, and
the air handler's healthy reset setpoint is literally :func:`camber.g36_reset.tr_simulate` of that
aggregate — so the reset a detector scores is the true T&R response to the fleet's own demand. Fault
archetypes then perturb exactly one layer (one rogue zone, one starved cohort, or one inert reset),
giving a labeled positive whose ground truth (which zone / which air handler / which reset failure
mode) the harness checks by **attribution**, not just a fire/no-fire bit.

:func:`generate_fleet` returns the labeled fleet; :func:`labeled_records` / :func:`targets` /
:func:`attribution` / :func:`coverage` score the six fleet detectors with the same
:func:`camber.eval.benchmark` method the LBNL / synthetic benchmarks use. Deterministic (fixed
construction + seeded noise) so a committed baseline is stable. numpy/pandas + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .g36_reset import SAT_TR, STATIC_TR, TRParams, tr_simulate
from .model.roles import Role
from .model.topology import Topology
from .rules.cohort_starvation_rule import CohortStarvation
from .rules.reset_effectiveness_rule import ResetEffectiveness
from .rules.rogue_zone_census_rule import RogueZoneCensus

__all__ = [
    "FleetLabel",
    "Fleet",
    "generate_fleet",
    "ARCHETYPES",
    "RESETS",
    "run_detectors",
    "labeled_records",
    "targets",
    "attribution",
    "coverage",
]

RESETS = ("sat", "static")
# archetype -> the detector class it targets ("" = fault-free, targets nothing)
ARCHETYPES = {
    "none": "",
    "rogue": "rogue_zone_census",
    "cohort": "cohort_starvation",
    "reset_stuck": "reset_effectiveness",
    "reset_not_responding": "reset_effectiveness",
    "reset_not_trimming": "reset_effectiveness",
    "reset_diverges": "reset_effectiveness",
}
_RESET_REASON = {  # reset archetype -> the ResetEffectivenessResult.reason it should produce
    "reset_stuck": "stuck",
    "reset_not_responding": "not_responding",
    "reset_not_trimming": "not_trimming",
    "reset_diverges": "diverges",
}
_TIER3 = 3.0  # a demanding zone emits a tier-3 reset request (G36 §5.14.8); a quiet zone emits 0


@dataclass(frozen=True)
class FleetLabel:
    """Ground truth for a generated fleet: the archetype and *where* the fault was injected."""

    fault: str  # "" fault-free, else an ARCHETYPES key
    reset: str  # "sat" | "static"
    rogue_zone: str | None = None  # zone id the rogue archetype pins
    starved_group: str | None = None  # AHU id the cohort archetype starves
    reset_equip: str | None = None  # AHU id whose reset is faulted
    reset_reason: str | None = None  # expected ResetEffectivenessResult.reason


@dataclass(frozen=True)
class Fleet:
    """A generated labeled fleet: zone role-frames + per-AHU reset frames + served-by topology."""

    zone_frames: dict  # {zone_id: role-frame} -> the census detectors' input
    ahu_reset_frames: dict  # {ahu_id: reset frame (SP + agg requests)} -> reset_effectiveness input
    topology: Topology  # zone -> AHU served-by (semantic provenance)
    label: FleetLabel


# --------------------------------------------------------------------------- generation


def _idx(days: int) -> pd.DatetimeIndex:
    return pd.date_range("2025-07-07", periods=days * 24, freq="1h")  # Monday start, deterministic


def _zone_frame(reset: str, demand: np.ndarray, idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Build one zone role-frame from a per-cycle boolean ``demand`` mask.

    A demanding cycle is engineered to a tier-3 G36 request; a quiet cycle to tier-0 — for SAT via
    zone temp 6 °F over the cooling setpoint, for static via airflow at 40% of setpoint with the
    damper wide open (mirrors the proven fixtures in tests/test_rogue_zone_census.py).
    """
    n = len(idx)
    if reset == "sat":
        cool = np.full(n, 74.0)
        temp = cool + np.where(demand, 6.0, 0.0)
        return pd.DataFrame({Role.SPACE_TEMP: temp, Role.COOL_SP: cool}, index=idx)
    sp = np.full(n, 1000.0)
    flow = np.where(demand, 400.0, 950.0)  # 40% of setpoint when demanding -> tier-3
    damper = np.where(demand, 98.0, 40.0)
    return pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: sp, Role.DAMPER: damper}, index=idx)


def _occupied(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray((idx.hour >= 10) & (idx.hour < 17))


def _day_index(idx: pd.DatetimeIndex) -> np.ndarray:
    return ((idx.normalize() - idx.normalize()[0]) / pd.Timedelta("1D")).astype(int).to_numpy()


def _rotating_half(idx: pd.DatetimeIndex, k: int, n_zones: int) -> np.ndarray:
    """Two-day demand/idle blocks with a rotating *half* of the zones leading each demand block.

    Gives the air handler a clean alternating demand/idle aggregate (so its healthy T&R reset has a
    real swing and reads unambiguously *effective*), while on any active cycle only half the zones
    request at once (< cohort threshold) and each zone leads half the blocks (no single rogue) — a
    genuine negative for BOTH censuses. This is the fault-free demand shape.
    """
    block = _day_index(idx) // 2
    demand_block = block % 2 == 0
    lead_low = (block // 2) % 2 == 0  # alternate which half of the zones leads each demand block
    in_half = k < n_zones // 2
    active = np.where(lead_low, in_half, not in_half)
    return demand_block & _occupied(idx) & active


def _rotating_single(idx: pd.DatetimeIndex, k: int, n_zones: int) -> np.ndarray:
    """One (non-rogue) zone leads each demand block — the rogue archetype's balanced *baseline*.

    Behind an always-on rogue zone, the remaining zones take turns carrying a modest baseline demand
    so the air handler's reset is still well-exercised (its healthy trajectory reads *effective*)
    while never letting a super-majority request at once (keeps the rogue fleet a cohort negative).
    """
    block = _day_index(idx) // 2
    demand_block = block % 2 == 0
    m = max(1, n_zones - 1)
    return demand_block & _occupied(idx) & (((block // 2) % m) == (k - 1))


def _zone_demands(archetype: str, ahu_faulted: bool, idx, n_zones: int) -> list[np.ndarray]:
    """Per-zone demand masks for one air handler under ``archetype`` (faulted AHU or a clean peer).

    Clean peers, the fault-free fleet, and the reset-``*`` fleets (whose fault is in the setpoint,
    not the zones) all use the strong balanced :func:`_rotating_half` demand; only the rogue and
    cohort archetypes reshape the zone demand itself.
    """
    occ = _occupied(idx)
    if not ahu_faulted or archetype == "none" or archetype in _RESET_REASON:
        return [_rotating_half(idx, k, n_zones) for k in range(n_zones)]
    if archetype == "rogue":
        # zone 0 demands every occupied hour (the rogue); the rest carry a rotating baseline
        return [occ if k == 0 else _rotating_single(idx, k, n_zones) for k in range(n_zones)]
    # cohort: every zone demands every occupied hour at once -> a starved cohort (no single rogue)
    return [occ.copy() for _ in range(n_zones)]


def _reset_frame(reset: str, agg: np.ndarray, idx, archetype: str, seed: int) -> pd.DataFrame:
    """Build an air handler's reset frame: the aggregated request count + an actual SP trajectory.

    The healthy SP is exactly :func:`tr_simulate` of the aggregate (T&R response to the fleet's own
    demand). A ``reset_*`` archetype replaces the SP with an inert trajectory engineered to the
    named G36 failure mode (stuck / not responding / not trimming / diverges).
    """
    sp_role = Role.SUPPLY_AIR_TEMP_SP if reset == "sat" else Role.DUCT_STATIC_SP
    req_role = Role.SAT_RESET_REQUESTS if reset == "sat" else Role.STATIC_PRESSURE_REQUESTS
    params: TRParams = SAT_TR if reset == "sat" else STATIC_TR
    rng = np.random.default_rng(seed)
    jitter = 0.03 if reset == "sat" else 0.005
    expected = tr_simulate(agg, params)
    mid = 0.5 * (params.sp_max + params.sp_min)
    trim_end = params.sp_max if params.sp_trim > 0 else params.sp_min  # energy-saving end
    demand_end = params.sp_min if params.sp_trim > 0 else params.sp_max  # meeting-demand end
    eff = agg - params.ignored
    demand = eff > 0

    if archetype == "reset_stuck":
        sp = np.full(len(idx), mid) + rng.normal(0, jitter, len(idx))
    elif archetype == "reset_not_responding":  # under demand, parked at the trim (saving) end
        sp = np.where(demand, trim_end, mid) + rng.normal(0, jitter, len(idx))
    elif archetype == "reset_not_trimming":  # while idle, parked at the demand end
        sp = np.where(~demand, demand_end, mid) + rng.normal(0, jitter, len(idx))
    elif archetype == "reset_diverges":  # moves the opposite way to the T&R command (mid-band)
        sp = mid - 0.5 * (expected - mid) + rng.normal(0, jitter, len(idx))
    else:  # healthy: track the request-implied T&R trajectory
        sp = expected + rng.normal(0, jitter, len(idx))
    return pd.DataFrame({sp_role: sp, req_role: agg}, index=idx)


def generate_fleet(
    archetype: str = "none",
    *,
    reset: str = "sat",
    n_ahus: int = 2,
    zones_per_ahu: int = 4,
    days: int = 21,
    seed: int = 0,
) -> Fleet:
    """Generate one labeled multi-zone VAV fleet under ``archetype`` for the ``reset`` family.

    ``archetype`` is one of :data:`ARCHETYPES`. The fault is injected into the first air handler
    (``AHU1``); the remaining ``n_ahus - 1`` stay fault-free so per-AHU attribution is exercised.
    Each air handler serves ``zones_per_ahu`` zones; the served-by topology carries **semantic**
    provenance so the census scopes per air handler without a heuristic caveat. Returns a
    :class:`Fleet` (zone role-frames + per-AHU reset frames + topology + ground-truth label).
    """
    if archetype not in ARCHETYPES:
        raise ValueError(f"archetype must be one of {sorted(ARCHETYPES)}, got {archetype!r}")
    if reset not in RESETS:
        raise ValueError(f"reset must be one of {RESETS}, got {reset!r}")
    idx = _idx(days)
    zone_frames: dict = {}
    ahu_reset_frames: dict = {}
    parent_map: dict = {}
    for a in range(n_ahus):
        ahu = f"AHU{a + 1}"
        faulted = a == 0
        demands = _zone_demands(archetype, faulted, idx, zones_per_ahu)
        agg = np.zeros(len(idx))
        for k in range(zones_per_ahu):
            zone = f"{ahu}-Z{k + 1}"
            zone_frames[zone] = _zone_frame(reset, demands[k], idx)
            parent_map[zone] = ahu
            agg = agg + np.where(demands[k], _TIER3, 0.0)
        arche = archetype if faulted else "none"
        ahu_reset_frames[ahu] = _reset_frame(reset, agg, idx, arche, seed + a)

    is_reset = archetype in _RESET_REASON
    label = FleetLabel(
        fault=archetype,
        reset=reset,
        rogue_zone="AHU1-Z1" if archetype == "rogue" else None,
        starved_group="AHU1" if archetype == "cohort" else None,
        reset_equip="AHU1" if is_reset else None,
        reset_reason=_RESET_REASON.get(archetype),
    )
    topology = Topology.from_parent_map(parent_map, provenance="semantic")
    return Fleet(zone_frames, ahu_reset_frames, topology, label)


# --------------------------------------------------------------------------- scoring harness


def _detectors(reset: str) -> dict:
    """The three fleet detectors for a reset family, keyed by their rule name."""
    return {
        f"{reset}_rogue_zone_census": RogueZoneCensus(reset),
        f"{reset}_cohort_starvation": CohortStarvation(reset),
        f"{reset}_reset_effectiveness": ResetEffectiveness(reset),
    }


def _fired(severity: str) -> bool:
    return severity in ("warn", "fault")


def run_detectors(fleet: Fleet) -> dict:
    """Run the three fleet detectors on ``fleet``; return ``{detector_name: Finding}``.

    Censuses run over all zone frames scoped by the fleet's topology; reset-effectiveness runs on
    the faulted air handler (``label.reset_equip``, else ``AHU1``).
    """
    reset = fleet.label.reset
    dets = _detectors(reset)
    ahu = fleet.label.reset_equip or "AHU1"
    return {
        f"{reset}_rogue_zone_census": dets[f"{reset}_rogue_zone_census"].analyze_fleet(
            fleet.zone_frames, topology=fleet.topology
        ),
        f"{reset}_cohort_starvation": dets[f"{reset}_cohort_starvation"].analyze_fleet(
            fleet.zone_frames, topology=fleet.topology
        ),
        f"{reset}_reset_effectiveness": dets[f"{reset}_reset_effectiveness"].analyze(
            ahu, fleet.ahu_reset_frames[ahu]
        ),
    }


def _truth(reset: str, archetype: str) -> str:
    """Namespaced truth label, or ``""`` for a fault-free fleet.

    Fault-free stays empty so :func:`camber.eval.benchmark` scores it as a true negative for every
    detector (and for the overall / correct-diagnosis rates). A faulty fleet is namespaced by reset
    family so a SAT fleet is never counted as a positive for a static detector, and the four reset
    archetypes collapse onto the single ``reset_effectiveness`` target class.
    """
    cls = ARCHETYPES[archetype]
    return f"{reset}:{cls}" if cls else ""


def _scenarios():
    for reset in RESETS:
        for archetype in ARCHETYPES:
            yield reset, archetype


def labeled_records() -> list:
    """One ``{"truth", "fired"}`` record per generated fleet, for :func:`camber.eval.benchmark`."""
    records = []
    for reset, archetype in _scenarios():
        fleet = generate_fleet(archetype, reset=reset)
        findings = run_detectors(fleet)
        fired = {name for name, f in findings.items() if _fired(f.severity)}
        records.append({"truth": _truth(reset, archetype), "fired": fired})
    return records


def targets() -> dict:
    """``{detector_name: the namespaced truth class it targets}`` for the six fleet detectors."""
    out = {}
    for reset in RESETS:
        out[f"{reset}_rogue_zone_census"] = f"{reset}:rogue_zone_census"
        out[f"{reset}_cohort_starvation"] = f"{reset}:cohort_starvation"
        out[f"{reset}_reset_effectiveness"] = f"{reset}:reset_effectiveness"
    return out


def attribution() -> dict:
    """Fraction of each detector's positive fleets where it named the *right* zone/AHU/failure mode.

    A detector that fires but mis-attributes (names the wrong rogue zone, wrong starved AHU, or
    wrong reset failure mode) fails here even though its TPR bit is 1 — the guard against a
    generator so easy that firing is meaningless.
    """
    hits: dict = {}
    tot: dict = {}
    for reset, archetype in _scenarios():
        target = ARCHETYPES[archetype]
        if not target:
            continue
        name = f"{reset}_{target}"
        fleet = generate_fleet(archetype, reset=reset)
        m = run_detectors(fleet)[name].metrics or {}
        if target == "rogue_zone_census":
            ok = m.get("worst_zone") == fleet.label.rogue_zone
        elif target == "cohort_starvation":
            ok = m.get("worst_group") == fleet.label.starved_group
        else:  # reset_effectiveness
            ok = m.get("reason") == fleet.label.reset_reason
        tot[name] = tot.get(name, 0) + 1
        hits[name] = hits.get(name, 0) + (1 if ok else 0)
    return {name: round(hits[name] / tot[name], 4) for name in tot}


def coverage() -> dict:
    """The six fleet detectors this harness accuracy-scores (3 detectors x 2 reset families)."""
    scored = sorted(targets())
    return {"fleet_scored": scored, "n_fleet_scored": len(scored)}
