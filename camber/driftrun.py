"""Run a whole drift-detector **family** over discovered equipment, and roll it up.

The drift detectors (:mod:`camber.ahudrift`, :mod:`camber.chillerdiag`,
:mod:`camber.condenserdrift`, :mod:`camber.evaporatordrift`, :mod:`camber.pumpdrift`,
:mod:`camber.vavdrift`) are :class:`~camber.rules.base.PeriodRule` s: each compares a frozen
baseline window against a current one. That makes them unreachable from the single-frame
:func:`camber.rules.builtin.builtin_registry` that config-driven runs and the CLI use, so until now
the whole family could only be driven from Python. This module is the missing layer --
*equipment discovery -> period slicing -> suite -> roll-up* -- and the single source of truth for
which detectors make up each family (the ``build_*_suite`` helpers in the ``*sim`` modules delegate
here, so the production path and the physics-validation path can never diverge).

Three properties are deliberate and load-bearing:

**One rule per scratch registry.** :meth:`camber.rules.base.Registry.register` keys on
``rule.name``, and :class:`~camber.rules.coil_valve_rule.CoilValveDrift` keeps the *same* name for
its cooling and heating instances (:func:`camber.ahudrift.diagnose_ahu_drift` matches on that name),
so a single Registry cannot hold both. Each rule instance therefore gets its own one-entry Registry,
which reuses all of ``run_periods``' resolve / shared-point merge / trust-gate / window-slicing
behaviour without touching it.

**Silence is never a clean bill of health.** There are two ways an equipment can go untested, and
a roll-up would call both of them *steady*. ``run_periods`` skips equipment whose resolved frame
lacks a required role, so ``diagnose_chiller_drift([])`` returns ``severity="ok"``,
``locus="steady"``; and every detector that *does* run can still **decline** (no frozen baseline
yet, an untrusted input, an empty window), rolling up to the same verdict over nothing but caveats.
Both are asserted negatives nobody tested, which the honesty convention in :mod:`camber.rules.base`
forbids. So neither is diagnosed: the equipment lands in :attr:`DriftFamilyResult.unevaluated` with
the reason, and the no-role case also gets an ``info`` Finding naming the roles the family needed
(the declines are already Findings of their own), so the absence travels into the report.

**Moving a reference is an operator's signature.** :func:`refit_baselines` re-fits a family over an
acceptance window and :func:`accept_new_normal_from_periods` hands the results to
:meth:`camber.store.modelstore.BaselineStore.accept_new_normal`, which requires who accepted it and
why. The re-fit reuses each rule's *own* fit -- run the suite against a scratch in-memory store and
harvest what it froze -- rather than a second copy of the fitting logic that could drift out of sync
with the detector it is meant to feed.

**Freezing is a verb, not a setting.** Every drift rule defaults ``freeze_if_missing=True`` and
writes the reference inline, so a scheduled run would quietly mint baselines from whatever window
the config happened to label "baseline". :func:`run_drift` defaults it to ``False`` and never saves;
only an explicit freeze command passes ``True``. Moving an existing reference stays
:meth:`camber.store.modelstore.BaselineStore.accept_new_normal`'s attributed decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ahudrift import diagnose_ahu_drift
from .chillerdiag import diagnose_chiller_drift
from .condenserdrift import diagnose_condenser_drift
from .driftthresholds import threshold_confidence
from .evaporatordrift import diagnose_evaporator_drift
from .pumpdrift import diagnose_pump_drift
from .pumpplantdiag import diagnose_pump_plant
from .rules.base import Finding, Registry
from .vavdrift import diagnose_vav_drift

__all__ = [
    "DriftFamily",
    "DriftFamilyResult",
    "DriftResult",
    "DRIFT_FAMILIES",
    "family_names",
    "build_drift_suite",
    "run_drift",
    "refit_baselines",
    "accept_new_normal_from_periods",
]


# ------------------------------------------------------------------ family membership tables
#
# One table per family, in the order the roll-up expects. These are the *only* definition of a
# family's membership: camber.ahusim.build_ahu_suite and its five siblings delegate here.


def _ahu_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.coil_valve_rule import CoilValveDrift
    from .rules.duct_static_rule import DuctStaticControlDrift
    from .rules.economizer_damper_rule import EconomizerDamperDrift
    from .rules.fan_efficiency_rule import FanEfficiencyDrift
    from .rules.filter_loading_rule import FilterLoadingDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    suite = [
        FanEfficiencyDrift(store, **kw),
        FilterLoadingDrift(store, **kw),
        DuctStaticControlDrift(store, **kw),
        EconomizerDamperDrift(store, **kw),
    ]
    suite += [CoilValveDrift(store, coil=c, **kw) for c in coils]
    return suite


def _chiller_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.chiller_cw_range_rule import ChillerCwRangeDrift
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_head_pressure_rule import ChillerHeadPressureDrift
    from .rules.chiller_subcooling_rule import ChillerSubcoolingDrift
    from .rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift
    from .rules.chiller_superheat_rule import ChillerSuperheatDrift
    from .rules.coolingtower_drift_rule import CoolingTowerApproachDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    classes = (
        ChillerApproachDrift,
        ChillerCwRangeDrift,
        CoolingTowerApproachDrift,
        ChillerHeadPressureDrift,
        ChillerSubcoolingDrift,
        ChillerSuperheatDrift,
        ChillerSuctionPressureDrift,
    )
    suite = [cls(store, **kw) for cls in classes]
    if sustained_alarm:
        # Appended, never folded into the table above: the CUSUM alarm contributes Findings only
        # (no roll-up consumes it) and its severity is a *temporal* claim -- the weaker of the two
        # threshold classes -- so it stays opt-in. Adding it to the table would also perturb the
        # physics-sim confusion matrices that score the roll-up.
        from .rules.chiller_drift_alarm_rule import ChillerApproachSustainedDrift

        suite.append(ChillerApproachSustainedDrift(store, **kw))
    return suite


def _condenser_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.chiller_cw_range_rule import ChillerCwRangeDrift
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_head_pressure_rule import ChillerHeadPressureDrift
    from .rules.coolingtower_drift_rule import CoolingTowerApproachDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    return [
        ChillerApproachDrift(store, **kw),
        ChillerCwRangeDrift(store, **kw),
        CoolingTowerApproachDrift(store, **kw),
        ChillerHeadPressureDrift(store, **kw),
    ]


def _evaporator_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift
    from .rules.chiller_superheat_rule import ChillerSuperheatDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    return [
        ChillerApproachDrift(store, **kw),
        ChillerSuperheatDrift(store, **kw),
        ChillerSuctionPressureDrift(store, **kw),
    ]


def _pump_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.loop_deltat_rule import LoopDeltaTDrift
    from .rules.loop_dp_rule import LoopDPDrift
    from .rules.pump_flow_rule import PumpFlowDrift
    from .rules.pump_head_rule import PumpHeadDrift
    from .rules.pump_power_rule import PumpPowerDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    classes = (PumpFlowDrift, PumpHeadDrift, LoopDeltaTDrift, LoopDPDrift, PumpPowerDrift)
    return [cls(store, **kw) for cls in classes]


def _vav_rules(store, *, site, run_id, freeze_if_missing, coils, sustained_alarm):
    from .rules.vav_airflow_rule import VavAirflowDrift
    from .rules.vav_reheat_valve_rule import VavReheatValveDrift

    kw = {"site": site, "run_id": run_id, "freeze_if_missing": freeze_if_missing}
    return [VavAirflowDrift(store, **kw), VavReheatValveDrift(store, **kw)]


@dataclass(frozen=True)
class DriftFamily:
    """One drift-detector family: its suite builder and the roll-up that reads its Findings.

    ``build`` returns the detector instances (in roll-up order) for a shared
    :class:`~camber.store.modelstore.BaselineStore`; ``diagnose`` turns one equipment's Findings
    into that family's localized verdict. ``label`` titles the family in reports and CLI output.
    """

    name: str
    label: str
    # (store, *, site, run_id, freeze_if_missing, coils, sustained_alarm) -> the detectors
    build: Callable[..., list]
    diagnose: Callable[..., Any]  # (findings, *, equip) -> a per-equipment diagnosis


#: Every drift family CAMBER ships, keyed by the name a config or the CLI refers to it by.
DRIFT_FAMILIES: dict = {
    "ahu": DriftFamily("ahu", "AHU air-side drift", _ahu_rules, diagnose_ahu_drift),
    "chiller": DriftFamily("chiller", "Chiller drift", _chiller_rules, diagnose_chiller_drift),
    "condenser": DriftFamily(
        "condenser", "Condenser heat-rejection drift", _condenser_rules, diagnose_condenser_drift
    ),
    "evaporator": DriftFamily(
        "evaporator",
        "Evaporator / chilled-water drift",
        _evaporator_rules,
        diagnose_evaporator_drift,
    ),
    "pump": DriftFamily("pump", "Pump / hydronic drift", _pump_rules, diagnose_pump_drift),
    "vav": DriftFamily("vav", "VAV zone-terminal drift", _vav_rules, diagnose_vav_drift),
}


def family_names() -> list:
    """Sorted names of every shipped drift family."""
    return sorted(DRIFT_FAMILIES)


def build_drift_suite(
    family: str,
    store,
    *,
    site: str = "",
    run_id: str = "",
    freeze_if_missing: bool = True,
    coils=("cooling",),
    sustained_alarm: bool = False,
) -> list:
    """The detector instances making up one drift ``family``, sharing one baseline ``store``.

    ``coils`` applies to the ``ahu`` family only (one
    :class:`~camber.rules.coil_valve_rule.CoilValveDrift` per coil); ``sustained_alarm`` applies to
    ``chiller`` only (appends the opt-in CUSUM alarm rule). Both are accepted and ignored elsewhere
    so every family shares one call shape. Raises ``KeyError`` naming the known families on an
    unknown ``family``.
    """
    try:
        fam = DRIFT_FAMILIES[family]
    except KeyError:
        raise KeyError(f"unknown drift family {family!r} (known: {family_names()})") from None
    return fam.build(
        store,
        site=site,
        run_id=run_id,
        freeze_if_missing=freeze_if_missing,
        coils=tuple(coils),
        sustained_alarm=sustained_alarm,
    )


@dataclass
class DriftFamilyResult:
    """One family's drift outcome over the equipment of one class.

    ``diagnoses`` holds a per-equipment verdict for every equipment that produced at least one
    Finding, worst-first. ``unevaluated`` names the equipment that produced **none** -- they are
    deliberately *not* diagnosed, because a roll-up over zero Findings reads as "steady" and would
    assert a negative nobody tested. ``plant`` is the cross-pump roll-up (``pump`` family with a
    ``plant`` name), otherwise ``None``.
    """

    equip_class: str
    family: str
    label: str
    baseline: tuple
    current: tuple
    diagnoses: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    unevaluated: list = field(default_factory=list)
    plant: Any = None

    def as_dict(self) -> dict:
        """A JSON-friendly view (diagnoses and findings flattened to plain dicts)."""
        return {
            "equip_class": self.equip_class,
            "family": self.family,
            "label": self.label,
            "baseline": list(self.baseline),
            "current": list(self.current),
            "diagnoses": [d.as_dict() for d in self.diagnoses],
            "findings": [f.as_dict() for f in self.findings],
            "unevaluated": list(self.unevaluated),
            "plant": None if self.plant is None else self.plant.as_dict(),
        }


@dataclass
class DriftResult:
    """The outcome of a drift run across every configured family.

    ``as_dict`` embeds the :func:`camber.driftthresholds.threshold_confidence` block, so a consumer
    reading only the JSON still sees that the magnitude floors are screening-grade and the CUSUM
    timing parameters are untuned.
    """

    site: str
    run_id: str
    store_path: str = ""
    families: list = field(default_factory=list)

    @property
    def findings(self) -> list:
        """Every Finding from every family, in family order."""
        return [f for fam in self.families for f in fam.findings]

    def as_dict(self) -> dict:
        """A JSON-friendly view of the whole run, including the threshold-confidence block."""
        return {
            "site": self.site,
            "run_id": self.run_id,
            "store_path": self.store_path,
            "threshold_confidence": threshold_confidence(magnitude=True, temporal=True),
            "families": [f.as_dict() for f in self.families],
        }


_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


def _run_one(rule, refs, mapping, **kw) -> list:
    """Run one PeriodRule instance through a private one-entry Registry.

    Registries key on ``rule.name`` and the AHU family registers two ``coil_valve_drift``
    instances, so the suite cannot share one. A scratch Registry per rule keeps all of
    ``run_periods``' behaviour and costs nothing.
    """
    reg = Registry()
    reg.register(rule)
    return reg.run_periods(rule.name, refs, mapping, **kw)


def _declined(finding) -> bool:
    """True when a Finding is a decline -- the rule reporting it could not test its claim."""
    return bool((getattr(finding, "metrics", None) or {}).get("declined"))


def _required_roles(suite) -> list:
    """Every role at least one detector in ``suite`` requires, as stable role strings."""
    seen, out = set(), []
    for rule in suite:
        for role in getattr(rule, "roles_required", ()):
            val = getattr(role, "value", str(role))
            if val not in seen:
                seen.add(val)
                out.append(val)
    return out


def run_drift(
    refs_by_class: dict,
    mapping,
    *,
    store,
    families,
    baseline,
    current,
    site: str = "",
    run_id: str = "",
    resample: str = "1h",
    shared=None,
    min_trust=None,
    freeze_if_missing: bool = False,
) -> DriftResult:
    """Run the configured drift families over discovered equipment and roll each one up.

    ``refs_by_class`` maps an equipment class to its discovered
    :class:`~camber.resolve.EquipRef` s (what a config-driven run already builds). ``families`` is
    a sequence of dicts, each naming a ``class`` and a ``family`` plus the optional ``coils`` /
    ``plant`` / ``sustained_alarm`` / per-family ``baseline`` / ``current`` overrides. ``baseline``
    and ``current`` are the default ``(start, end)`` windows.

    ``freeze_if_missing`` defaults to **False**: a run that scores drift must not also create the
    reference it scores against. Pass ``True`` only from an explicit freeze command, and save the
    store yourself afterwards.
    """
    out = DriftResult(
        site=site, run_id=run_id, store_path=getattr(store, "path", "") or "", families=[]
    )
    for entry in families:
        cls = entry["class"]
        fam_name = entry["family"]
        fam = DRIFT_FAMILIES[fam_name]
        refs = list(refs_by_class.get(cls, []))
        base_win = tuple(entry.get("baseline") or baseline)
        cur_win = tuple(entry.get("current") or current)

        suite = build_drift_suite(
            fam_name,
            store,
            site=site,
            run_id=run_id,
            freeze_if_missing=freeze_if_missing,
            coils=tuple(entry.get("coils") or ("cooling",)),
            sustained_alarm=bool(entry.get("sustained_alarm")),
        )

        findings: list = []
        for rule in suite:
            findings += _run_one(
                rule,
                refs,
                mapping,
                baseline=base_win,
                current=cur_win,
                resample=resample,
                shared=shared,
                min_trust=min_trust,
            )

        by_equip: dict = {}
        for f in findings:
            by_equip.setdefault(f.equip, []).append(f)

        # Two ways an equipment can be untested, and neither may be diagnosed: no detector ran at
        # all (its required roles never resolved), or every detector that ran *declined* (no frozen
        # baseline, an untrusted input, an empty window). A roll-up over declines-only returns
        # "steady", which would assert a negative nobody tested -- so both routes lead here.
        needed = _required_roles(suite)
        diagnoses: list = []
        unevaluated: list = []
        for eq, fs in by_equip.items():
            if all(_declined(f) for f in fs):
                unevaluated.append(
                    {
                        "equip": eq,
                        "reason": "every detector declined",
                        "declined": sorted({f.rule for f in fs}),
                        "roles_required": needed,
                    }
                )
                continue
            diagnoses.append(fam.diagnose(fs, equip=eq))
        diagnoses.sort(key=lambda d: -_RANK.get(getattr(d, "severity", "ok"), 0))

        for ref in refs:
            if ref.equip in by_equip:
                continue
            unevaluated.append(
                {
                    "equip": ref.equip,
                    "reason": "no required role resolved",
                    "declined": [],
                    "roles_required": needed,
                }
            )
            findings.append(
                Finding(
                    rule=f"drift:{fam_name}",
                    equip=ref.equip,
                    severity="info",
                    metrics={"declined": True, "family": fam_name, "roles_required": needed},
                    summary=(
                        f"{ref.equip}: declined -- no {fam.label.lower()} detector could be "
                        "evaluated (none of the required roles resolved)"
                    ),
                    caveats=[
                        f"{ref.equip} was not evaluated for {fam.label.lower()}: needs one of "
                        + ", ".join(needed)
                    ],
                )
            )
        unevaluated.sort(key=lambda r: r["equip"])

        plant = None
        if fam_name == "pump" and entry.get("plant"):
            plant = diagnose_pump_plant(diagnoses, plant=entry["plant"])

        out.families.append(
            DriftFamilyResult(
                equip_class=cls,
                family=fam_name,
                label=fam.label,
                baseline=base_win,
                current=cur_win,
                diagnoses=diagnoses,
                findings=findings,
                unevaluated=unevaluated,
                plant=plant,
            )
        )
    return out


# ------------------------------------------------------------------ moving a frozen reference


def refit_baselines(
    family: str,
    refs,
    mapping,
    *,
    period,
    site: str = "",
    run_id: str = "",
    resample: str = "1h",
    shared=None,
    min_trust=None,
    coils=("cooling",),
    sustained_alarm: bool = False,
) -> dict:
    """Re-fit one family's baselines over ``period``, without touching any real store.

    Returns ``{(equip, kind): fitted model}``. The fit is not reimplemented here: the suite is run
    against a **scratch in-memory** :class:`~camber.store.modelstore.BaselineStore` with
    ``freeze_if_missing=True`` and whatever it froze is harvested. That way each model comes from
    its own detector -- same metric and load columns, same minimum-load filter, plausibility bounds
    and running-status gate -- and cannot drift away from the rule it will be compared against.

    A detector that cannot fit over ``period`` simply produces no entry; the caller reports the
    absence rather than substituting something.
    """
    from .store.modelstore import BaselineStore

    scratch = BaselineStore()
    suite = build_drift_suite(
        family,
        scratch,
        site=site,
        run_id=run_id,
        freeze_if_missing=True,
        coils=tuple(coils),
        sustained_alarm=sustained_alarm,
    )
    for rule in suite:
        _run_one(
            rule,
            refs,
            mapping,
            baseline=period,
            current=period,
            resample=resample,
            shared=shared,
            min_trust=min_trust,
        )
    return {(rec.equip, rec.kind): rec.model() for rec in scratch.records()}


def accept_new_normal_from_periods(
    store,
    refits: dict,
    *,
    site: str,
    accepted_by: str,
    reason: str,
    at: str,
    equips=None,
    kinds=None,
    period=("", ""),
) -> list:
    """Supersede frozen baselines with the re-fits in ``refits`` -- an attributed operator decision.

    ``refits`` is :func:`refit_baselines` output. ``equips`` restricts which equipment move (there
    is deliberately no "accept everything" default: pass the equipment explicitly); ``kinds``
    optionally narrows to particular model kinds. ``accepted_by`` and ``reason`` are forwarded to
    :meth:`camber.store.modelstore.BaselineStore.accept_new_normal`, which rejects an empty either
    way -- an unattributed baseline change is indistinguishable from the automatic refit the freeze
    policy exists to prevent.

    Returns the superseding records, sorted by ``(equip, kind)``. The caller is responsible for
    saving the store.
    """
    want_equips = None if equips is None else set(equips)
    want_kinds = None if kinds is None else set(kinds)
    out = []
    for (equip, kind), model in sorted(refits.items()):
        if want_equips is not None and equip not in want_equips:
            continue
        if want_kinds is not None and kind not in want_kinds:
            continue
        out.append(
            store.accept_new_normal(
                model,
                site=site,
                equip=equip,
                kind=kind,
                accepted_by=accepted_by,
                reason=reason,
                at=at,
                period=period,
            )
        )
    return out
