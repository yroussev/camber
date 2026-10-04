"""Declarative, config-driven analysis runs.

A single JSON config describes a whole analysis -- the data source, the tag→role
mapping, which equipment to discover, which rules to run, and what report to write
-- so a run is reproducible without a bespoke script:

    {
      "site": "ExampleHQ",
      "source": {"kind": "perpoint_csv", "folder": "trends/"},
        # or multi-folder: {"folders": ["batch1/", "batch2/"]}
        #               or {"globs": ["sites/*/trends"]}
      "mapping": {"path": "mapping.json"},
      "shared_oat": {"file": "trends/OAT.csv"},
      "equipment": [{"class": "AHU", "marker": "CHW_Valve"},
                    {"class": "VAV", "marker": "SpaceTemp"}],
      "rules": ["simultaneous_heat_cool", "reheat_penalty",
                {"name": "economizer_high_limit",     # per-rule tuning: a dict entry
                 "params": {"high_limit_f": 75, "min_damper": 0.45}}],
        # a rule is a bare name (constructor defaults) OR {"name","params"} to override its
        # kwargs for this run (e.g. a high-outside-air building's design minimum damper).
      "soo": [{"class": "AHU", "library": "g36_ahu"},
              {"class": "AHU", "spec": "ahu_sequence.json"}],
      "drift": {"store": "baselines.json",
                "baseline": ["2025-03-01", "2025-05-31"],
                "current":  ["2026-06-01", "2026-08-31"],
                "families": [{"class": "AHU", "family": "ahu", "coils": ["cooling"]},
                             {"class": "CH",  "family": "chiller"}]},
      "report": {"level": 2, "climate_zone": "CA CZ15", "out_text": "audit.txt"}
    }

The optional top-level ``site_elevation_ft`` (0.98, #92) is the site's elevation in feet. Every rule
and drift detector that derives a wet-bulb from OAT + RH takes it as its ``elevation_ft`` (unless
its own params set ``elevation_ft`` or ``pressure_psia``): ``cooling_tower_approach``,
``condenser_water_reset``, ``cooling_tower_approach_drift`` and
``cooling_tower_fan_effort_drift``. See :func:`site_elevation_ft`.

The optional ``soo`` section evaluates Sequence-of-Operations conformance per
equipment class -- either a packaged library sequence (``library``: ``g36_ahu`` /
``g36_plant``) or a JSON clause spec (``spec``) -- merging the per-clause Findings into
the run.

The optional ``mv`` section fits a daily change-point M&V baseline (ASHRAE Guideline 14 / IPMVP,
:mod:`camber.mandv`) to every equipment of a class: ``[{"class": "CHILLEDWATER_METER", "role":
"energy_rate", "period": ["2016-01-01", "2016-12-31"]}]``. The energy role is read as an hourly
rate and integrated to daily energy against the daily-mean OAT (the equipment's own ``oat`` role,
else ``shared_oat``); each meter yields one ``mv_baseline`` Finding carrying the chosen model and
its R², CV(RMSE), NMBE, residual lag-1 autocorrelation ``rho`` and the baseline's OAT support
(``oat_fit_min`` / ``oat_fit_max``, the fitted range, and ``oat_support_lo`` / ``oat_support_hi``,
the support band) -- ``ok`` when the fit meets the daily G14 acceptance, ``info`` when it does not
(a weak weather dependence is a property of the meter, not an equipment fault). An entry that also
names a ``"reporting_period": [start, end]`` gets one ``mv_savings`` Finding per meter: the
baseline projected onto the reporting days (IPMVP Option C avoided energy) with ``avoided_energy``,
``savings_pct``, ``fsu`` (fractional savings uncertainty at 90%), ``fsu_extrapolation_factor`` and
the coverage metrics (``coverage_tier``, ``share_points_outside``, ``share_energy_outside``,
``max_beyond_rel``). When the reporting period lies far outside the baseline's conditions (coverage
``severe``) the saving is **declined**: ``severity="info"``, ``metrics["declined"]`` true with
``declined_reason``, and a caveat, never a number. The optional ``"extrapolation": {...}`` keys
tune the grading (:class:`~camber.mandv.coverage.ExtrapolationPolicy`; an unknown key is an error).

``"method"`` declares the SEP adjustment-model method (:mod:`camber.mandv.methods`): ``forecast``
(the default when absent -- the Finding then says no method was declared), ``backcast`` (a model
fitted on the reporting days, projected back onto the baseline days), ``chaining`` (SEP's one
intermediate period, ``"intermediate_period": [start, end]``, of the same length as and between
the other two) or ``standard_conditions`` (both models on ``"normal_year"``, a list of daily mean
temperatures). Each gives one ``mv_savings`` Finding with ``method``, ``kernel``, ``enpi`` (the
SEnPI) and ``sep_range_valid``. ``"method": "auto"`` never reports a saving: it gives one
``mv_method_proposal`` Finding -- the method SEP's order proposes and a sensitivity table of
every valid method. ``"kernel"`` is ``g14`` or ``exact``, defaulting to ``g14`` for forecast and
backcast and ``exact`` for chaining and standard conditions.

An ``mv`` entry with a ``reporting_period`` may also carry an ``"adjustments": [...]`` ledger of
non-routine and static-factor adjustments (:mod:`camber.mandv.adjustments`): each item is the dict
form of a ``NonRoutineAdjustment`` (``"kind": "nra"``) or ``StaticFactorAdjustment`` (``"kind":
"static"``); an ``indicator`` NRA is estimated per meter on the baseline or reporting days
(``"fit_period"``, by default the period its ``start`` falls in) against the model the method
projects. The order is fixed: **the declared method gives the saving, the ledger restates its
baseline side, and the Finding reports both** -- for a chain, each entry restates the one link
whose dates hold it. ``"ecm_dates"`` and ``"settle_days"`` (default 14; one
:class:`~camber.mandv.adjustments.EcmSchedule`) drive the confounding guard and
``"materiality_threshold"`` the materiality flag. The ``mv_savings`` finding then adds
``adjusted_savings``, ``adjusted_savings_pct``, ``adjusted_abs_uncertainty``,
``adjusted_baseline``, ``adjusted_enpi``, the resolved ``adjustments`` and the ``waterfall``; a
refused ledger records ``adjustments_refused`` and a caveat and leaves the unadjusted saving
standing. With ``"method": "auto"`` the ledger never changes the proposal: each sensitivity row
shows the method's ``adjusted_*`` figures beside the unadjusted ones.

``"validity"`` (``g14`` default, ``sep`` or ``both``; issue #21 decision D1) is one key for the
whole entry: the regime the saving is reported under. ``g14`` keeps the G14 acceptance caveat;
``sep`` and ``both`` add each projecting model's SEP 2019 Ed. 2 §6.4.1 verdict (``sep_valid``,
``sep_validity``, and a caveat when a model fails, since its SEnPI is then not reportable under
SEP) and require ``evidence`` and ``approved_by`` on every adjustment (§5.3.2).

The optional ``drift`` section scores a **current** window against a frozen **baseline** one for
each configured detector family (:mod:`camber.driftrun`), merging its Findings into the run. It is
strictly read-only toward the baseline store: a config-driven run never creates or moves a frozen
reference, because a run that mints the baseline it scores against is circular. Creating one is
``camber drift freeze``; moving one is ``camber drift accept`` (see :mod:`camber.cli`).

A family may instead **declare** its reference (0.98, #86, approved decision S4):
``{"class": "CHW_PLANT", "family": "tower", "reference": {"equip": "PLANT__fault_free"}}`` scores
every other equipment of the class against a named healthy one (a labelled dataset's fault-free
run, a sister unit), and ``"reference": {"period": [start, end]}`` against a known-good window of
the same equipment. That is allowed because nothing is minted: the reference's baseline is fitted
in a scratch in-memory store on every run and **never persisted**, the reference is named in the
config for anyone to audit, each Finding records it (``baseline_source``) with a caveat, and the
reference equipment itself declines (``reason="is_reference"``). Such a family needs no store and
no windows (they default to the whole history; a period reference's current window to everything
after it), and ``camber drift freeze`` refuses it.

An ``ahu`` family entry may opt in to the **coil-valve leak drift** detector (0.100, #100;
provisional): ``"coil_leak": ["cooling"]`` (or ``["cooling", "heating"]``) appends a
:class:`~camber.rules.coil_leak_rule.CoilLeakDrift` per coil, which judges the coil's valve-shut
air rise against the baseline at matched mixed air, and ``"coil_leak_params": {...}`` overrides its
constructor defaults (``docs/THRESHOLDS.md``, "Opt-in drift detectors"). Without the key the family
is unchanged. It works with a frozen store or a declared reference alike.

**Store-backed source.** ``"source": {"kind": "store", "store": "lab_store", "facility_id":
"ds-lbnl-sdahu"}`` reads equipment from a :class:`~camber.store.ParquetStore` (e.g. one filled by
``camber datasets ingest``) instead of per-point CSV folders. The store already holds role-named
series, so ``mapping`` may be omitted (an identity mapping is used). Equipment is discovered by
the class recorded at ingest (``{"class": "AHU", "marker_role": "mixed_air_temp"}``; ``marker`` is
the folder-source file marker and is ignored here); ``shared_oat`` may name a store equipment and
role (``{"equip": "weather", "role": "oat"}``) or a CSV ``file`` as before; optional ``start`` /
``end`` bound every read. Any ``equipment`` entry (store or folder source) may add ``"equip":
[names]`` to keep only the named equipment -- e.g. one scenario of a multi-scenario dataset.
It may also name the equipment's ``"refrigerant"`` (``"R-410A"``, ``"R-134a"``, ``"R-22"``,
``"R-32"``, ``"R-744"``; provisional, 0.93): the saturation-referenced roles (subcooling,
superheat, discharge superheat, the approaches) are then derived from its refrigerant pressures and
line temperatures wherever a rule asks for them (:mod:`camber.refrigerant`).
The facility's provenance (dataset, licence, citation) recorded at ingest is attached to the
report as ``AuditReport.data_sources``. A facility whose lifecycle state is not
``active`` (suspended, provisioning, ...; see :mod:`camber.portfolio`) is skipped with a warning --
the run finds no equipment -- unless the source sets ``"include_inactive": true``. Any other
``kind`` (or none) reads folders as before; an unrecognised kind warns rather than fails, for
back-compat.

**Site time zone** (provisional, 0.90.1). ``"source": {..., "timezone": "America/Chicago"}`` names
the site's IANA zone: trend files whose stamps name an instant (ISO ``Z`` / ``+hh:mm`` offsets,
epoch numbers) are converted to that wall clock before any hour-of-day, schedule or occupancy rule
sees them (the DST fall-back repeat keeps its first reading; the spring-forward hour is a gap, as
in a naive local export). Without it such stamps keep the clock as written -- UTC for ``Z`` -- and
a :class:`~camber.tsparse.TimezoneWarning` says so; ``"strict_timezone": true`` refuses them
instead. A store source is already on the site's wall clock (``camber datasets ingest`` converts
with the catalog entry's ``local_timezone``); there ``timezone`` defaults to the facility's zone and
applies only to a ``shared_oat`` CSV file, and a ``timezone`` that disagrees with it warns. The
facility's zone is looked up as the read API does (0.100, #104): the registry entry's ``timezone``,
the dataset-catalog block and entry, then an open-fdd ingest's provenance.
Either way the zone also sets the length of a daily ``mv`` day (0.93, #68): the autumn fall-back
day sums 25 hours of energy, the spring-forward day 23 (see docs/MANDV.md).

**Facility identity and the portfolio workspace.** Every run has a ``facility_id``: a store
source's ``source.facility_id``; for a folder source the optional top-level ``"facility_id"``,
else :func:`~camber.store.make_facility_id` of ``site``. Per-facility state is keyed by it inside a
portfolio workspace (:mod:`camber.portfolio`) -- a store source whose store belongs to one, or a
folder source that names one with ``"workspace": "<root>"``. There the drift baseline store and
the optional fault history are opened *for that facility* (renaming it orphans nothing), default
to ``state/<facility_id>/baselines.json`` / ``faults.json`` when the config names no path, and
every file a run writes for the facility is listed in its ``state/<facility_id>/manifest.json``.
A folder config in a workspace without ``facility_id`` warns (its id would change with
``site``). Outside a workspace, stores stay keyed by ``site`` exactly as before.

The optional ``faults`` section folds each run's actionable findings into a persistent fault
lifecycle (:class:`~camber.faultlifecycle.FaultLifecycle`): ``{"store": "faults.json",
"run_id": "...", "auto_resolve_absent": false}`` -- ``store`` may be omitted in a workspace and
``run_id`` defaults to the current UTC time. Inside a workspace the fold holds the workspace lock.

The ``report`` section selects the report: ``"layout": "audit"`` (the default Std-211 audit) or
``"rcx"`` -- the printable retro-commissioning layout of :mod:`camber.report.rcx`, tuned by
``"rcx": {"top_n", "week", "paper", "chart_format", "sections", "price", "loads", "occupancy",
"oat_reference", "sequence", "notes"}`` (``camber report CONFIG --layout rcx``). ``report.loads``
(``{equip: {"heating_capacity_kbtuh": ...}}``) sizes equipment for the existing cost estimators.
The audit is titled as an ASHRAE Std-211 audit only when it carries the Std-211 inputs -- a
``benchmark`` and, at Level 2 and above, an ECM table (``"ecms": [{"name", "finding",
"affected_system", "est_savings", ...}]``, the fields of :class:`camber.report.audit.ECM`); else it
is a "Building analytics report". ``"title"`` sets the title explicitly (0.96, #78).

**Served-by topology** (provisional, 0.91; #61). ``"topology": {"parents": {"VAV-101": "AHU-1",
"AHU-1": ["CH-1", "CH-2"]}, "csv": ["vav_to_ahu.csv"]}`` declares which equipment serves which --
an explicit ``{child: parent}`` map and/or CSV schedules (``vav_id,parent_ahu`` style; see
:func:`camber.topology_infer.topology_from_config`). Ids match the discovered equipment ignoring
case and separators unless ``"match": "exact"``. Grouping-aware fleet rules (the rogue-zone census,
cohort starvation, system DCV) then group by it instead of guessing from equipment names, and
the RCx report ties an air handler to the plant serving it. The run records it as
``RunResult.topology`` with its provenance in ``RunResult.topology_source``.

**System ventilation (ASHRAE 62.1 VRP)** (provisional, 0.92; #17). ``"ventilation": {"zones":
"zones.csv", "systems": {"AHU-1": {"ps": 40}}}`` runs the system-level Ventilation Rate Procedure
(:class:`camber.rules.ventilation_rule.VentilationSystemVRP`, rule ``ventilation_system_62_1``):
each air handler's outdoor air against ``Vot = Vou / Ev`` over the zones it serves. ``zones`` is a
CSV path or a list of objects with ``zone``, ``area_sqft``, ``population``, ``space_type`` (or
``rp`` / ``ra``) and optionally ``system``, ``ez_cooling``, ``ez_heating``, ``vpz_min_cfm``,
``vpz_cfm``, ``area_assumed``, ``population_assumed`` (see
:func:`camber.ventilation.zones_from_records`); a zone without ``system`` joins its air handler
through the ``topology``. ``systems`` sets ``ps`` (system population) or ``d``, ``vps_cfm``,
``system_type`` and ``method`` per air handler; the other keys (``method``, ``ez_cooling``,
``ez_heating``, ``under_tol``, ``over_factor``, ``start_hour``, ``end_hour``, ``occupied_days``)
tune the rule. The section registers the rule and runs it even when ``rules`` does not list it.

Run it: ``python -m camber.config config.json``. JSON is used (not YAML/TOML) to
stay dependency-free and consistent with the mapping files. Paths are resolved
relative to the config file's directory.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from glob import glob
from typing import Any

from .driftrun import DRIFT_FAMILIES, family_names, refit_baselines, run_drift
from .model.mapping import MappingProvider
from .model.roles import Role
from .realio import load_point
from .report.audit import ECM, AuditReport, Benchmark, html_document
from .report.drift import drift_report_html
from .resolve import (
    StoreEquipRef,
    _facility_is_active,
    discover,
    discover_store,
    discover_terminals,
    resolve,
)
from .rules.base import _merge_shared, _with_class
from .rules.builtin import builtin_registry, is_fleet, make_rule, rule_factories
from .soo import soo_findings, spec_from_dicts
from .soo_library import g36_ahu_sequence, g36_plant_sequence
from .store.modelstore import BaselineStore

__all__ = [
    "RunResult",
    "run_config",
    "site_elevation_ft",
    "run_drift_config",
    "run_mv_config",
    "drift_store_path",
    "data_sources",
    "drift_refit",
    "load_config",
    "run_config_file",
]

# Named built-in SOO sequences referenceable from a config's "soo" section.
_SOO_LIBRARY = {"g36_ahu": g36_ahu_sequence, "g36_plant": g36_plant_sequence}


@dataclass
class RunResult:
    """Outcome of a config-driven run."""

    site: str
    equipment: int  # number of equipment discovered
    findings: list  # all Findings produced
    report: AuditReport | None = None
    rules_run: list = field(default_factory=list)
    drift: object | None = None  # DriftResult when the config has a "drift" section
    facility_id: str | None = None  # the identity per-facility state is keyed by
    workspace: str | None = None  # the portfolio workspace root, when the run is inside one
    faults: dict | None = None  # the fault-lifecycle fold (new/ongoing/...) with a "faults" section
    # The registry the run actually used -- built-ins with any per-rule "params" overrides
    # applied -- so evidence and reports judge with the configured rule instances.
    registry: object | None = None
    # A lazy resolver ``frame_for(equip, roles=None) -> DataFrame | None``: the equipment's role
    # frame (shared OAT merged in), loaded on first request and memoized. Never a dict of frames.
    frame_for: Callable | None = None
    refs: list = field(default_factory=list)  # the discovered equipment references
    data_sources: list = field(default_factory=list)  # ingest provenance (dataset, licence, ...)
    config: dict | None = None  # the config dict the run executed
    base_dir: str = "."  # what the config's relative paths resolve against
    # -- 0.91 (#61): the served-by topology the config declared (None when it declares none) and
    # its provenance (source file(s), edge count, ids that named no discovered equipment).
    topology: object | None = None
    topology_source: dict | None = None
    # -- 0.98 (#88): configured rules that applied but produced nothing, as
    # :class:`camber.rules.base.RuleSkip` records (missing inputs, no data, no verdict). Kept out of
    # ``findings`` (and so out of findings.json) on purpose; the RCx report lists them.
    rules_skipped: list = field(default_factory=list)


def _path(base: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(base, p)


def _source_folders(source: dict, base_dir: str) -> list:
    """Resolve a ``source`` spec to a list of data folders.

    Accepts (in precedence order):
      * ``"folder": "trends/"``           -- a single folder (back-compat),
      * ``"folders": ["a/", "b/", "c/"]`` -- explicit list, merged natively, or
      * ``"globs": ["sites/*/trends"]``   -- glob patterns expanded to folders.
    All paths resolve against ``base_dir``. A single building's export can span
    several folders; listing them here makes resolve() search across all of them
    for each equipment's points.
    """
    folders: list = []
    if source.get("folder"):
        folders.append(_path(base_dir, source["folder"]))
    folders += [_path(base_dir, f) for f in source.get("folders", [])]
    for pat in source.get("globs", []):
        folders += sorted(p for p in glob(_path(base_dir, pat)) if os.path.isdir(p))
    # de-dup, preserve order
    seen, out = set(), []
    for f in folders:
        if f not in seen:
            seen.add(f)
            out.append(f)
    if not out:
        raise ValueError("source must define 'folder', 'folders', or 'globs'")
    return out


@dataclass
class _FacilityCtx:
    """Which facility a config is about, and whether its state lives in a portfolio workspace."""

    facility_id: str | None
    workspace: str | None
    site: str
    legacy_sites: tuple = ()

    @property
    def bound(self):
        """The facility id per-facility stores are keyed by (``None`` outside a workspace)."""
        return self.facility_id if self.workspace else None


def _facility_context(config: dict, base_dir: str) -> _FacilityCtx:
    """Resolve a config's facility id, portfolio workspace and display label (no data read)."""
    from .store import ParquetStore, make_facility_id, require_facility_id
    from .store.facilities import _workspace_of_store

    source = config.get("source") or {}
    if str(source.get("kind") or "") == "store":
        if not source.get("store") or not source.get("facility_id"):
            raise ValueError("a store source needs 'store' (path) and 'facility_id'")
        store = ParquetStore(_path(base_dir, source["store"]))
        fid = source["facility_id"]
        ws = _workspace_of_store(store.root)
        site = config.get("site") or store.facility_name(fid)
    else:
        site = config.get("site", "")
        fid = config.get("facility_id")
        ws = None
        if config.get("workspace"):
            from .portfolio import is_workspace

            ws = os.path.abspath(_path(base_dir, config["workspace"]))
            if not is_workspace(ws):
                raise ValueError(f"config workspace {ws} is not a portfolio workspace")
        if fid:
            require_facility_id(fid)
        elif site:
            fid = make_facility_id(site)
            if ws:
                warnings.warn(
                    f"config has no facility_id; using {fid!r} derived from site {site!r}. Add "
                    f'"facility_id": "{fid}" to the config -- a derived id changes if the site '
                    "label does, which would split the facility's history",
                    UserWarning,
                    stacklevel=3,
                )
        elif ws:
            raise ValueError("a config inside a portfolio workspace needs facility_id (or site)")
    legacy: tuple = ()
    if ws and fid:
        from .portfolio import Portfolio

        pf = Portfolio(ws)
        try:
            pf.facility(fid)
        except KeyError as e:
            raise ValueError(
                f"{e.args[0]} in the workspace {ws}; register it first "
                f"(camber facility add NAME --id {fid} --reason ...)"
            ) from None
        names = [*pf.legacy_sites(fid), config.get("site") or ""]
        legacy = tuple(dict.fromkeys(n for n in names if n))
    return _FacilityCtx(fid, ws, site, legacy)


def _state_path(ctx: _FacilityCtx, fname: str):
    """``state/<facility_id>/<fname>`` inside the config's workspace, else ``None``."""
    if not (ctx.workspace and ctx.facility_id):
        return None
    from .portfolio._state import state_dir

    return os.path.join(state_dir(ctx.workspace, ctx.facility_id), fname)


def _record_outputs(ctx, paths: dict) -> None:
    """List files a run wrote for its facility in the workspace manifest (no-op outside one)."""
    if ctx is None or not (ctx.workspace and ctx.facility_id):
        return
    from ._statefile import save_path
    from .portfolio._state import record_outputs

    # a migrated (redirect-stub) path was written through to the facility's state file
    real = {save_path(p, facility_id=ctx.facility_id): k for p, k in paths.items()}
    record_outputs(ctx.workspace, ctx.facility_id, real)


@dataclass
class _Prepared:
    """The source/mapping/equipment context every config-driven entry point needs."""

    site: str
    resample: str
    mapping: MappingProvider
    shared: dict | None
    refs: list
    refs_by_class: dict
    min_trust: float | None
    data_sources: list = field(default_factory=list)
    ctx: _FacilityCtx | None = None
    mv_store: object = None  # the facility's MVBaselineStore, opened read-only (#21 phase 21d)
    units: object = None  # 0.92 (#69): the config's reporting UnitSystem, or None (meter units)
    timezone: str | None = None  # 0.93 (#68): the site's IANA zone, when known (DST day lengths)
    weather: object = None  # 0.94 (#73): the config's WeatherContext (privacy guardrails)


# Source kinds that mean "per-point CSV folders" (the historical default). Anything else that is
# not "store" warns and falls back to folders, so an old config with a free-form kind still runs.
_FOLDER_KINDS = frozenset({"", "perpoint_csv", "perpoint", "folder", "folders", "csv"})


def _identity_mapping() -> MappingProvider:
    """Every role slug maps to itself -- the mapping for data already stored by role."""
    return MappingProvider(aliases={r.value: r for r in Role})


def _load_mapping(config: dict, base_dir: str, *, default=None) -> MappingProvider:
    mp_spec = config.get("mapping")
    if mp_spec is None:
        if default is None:
            raise KeyError("mapping")
        return default
    if "path" in mp_spec:
        with open(_path(base_dir, mp_spec["path"])) as fh:
            mp_spec = json.load(fh)
    return MappingProvider.from_dict(mp_spec)


def _catalog_timezone(meta: dict) -> str | None:
    """The site zone a catalog dataset was ingested into (``ingest.local_timezone``), if known.

    0.99 (#22): a facility ingested from an open-fdd package records the zone it was given
    (``meta["openfdd"]["timezone"]``); that is the store's clock too. Config runs resolve a
    facility's zone with :func:`camber._provenance.facility_timezone` since 0.100 (#104); this
    narrower lookup is kept for callers that use it.
    """
    from ._provenance import _valid_zone, catalog_dataset_timezone

    ofdd = (meta or {}).get("openfdd")
    if isinstance(ofdd, dict) and ofdd.get("timezone"):
        return _valid_zone(ofdd["timezone"])
    block = (meta or {}).get("dataset") or {}
    return catalog_dataset_timezone(block.get("dataset_id") if isinstance(block, dict) else None)


def _site_timezone(source: dict, meta: dict | None = None) -> dict:
    """``{"timezone", "strict_timezone"}`` for a config source (validated; see the module doc).

    A store facility already knows its zone: it is the default, and a ``source.timezone`` that
    disagrees with it warns (the store is on that zone's clock). 0.100 (#104): the facility's zone
    is resolved as the read API resolves it (:func:`camber._provenance.facility_timezone`): the
    registry entry's ``timezone`` first, then the dataset-catalog block and entry, then the open-fdd
    provenance. Before, a run read only the catalog or the open-fdd provenance.
    """
    from .tsparse import check_timezone

    tz = check_timezone(source.get("timezone") or None)
    strict = bool(source.get("strict_timezone", False))
    if meta is not None:
        from ._provenance import facility_timezone

        known = facility_timezone(meta)
        if known and tz and known != tz:
            warnings.warn(
                f"source.timezone {tz!r} differs from the zone this dataset was ingested in "
                f"({known!r}); the store's stamps are already {known} wall clock",
                UserWarning,
                stacklevel=3,
            )
        tz = tz or known
    return {"timezone": tz, "strict_timezone": strict}


def _source_kind(source: dict) -> str:
    kind = str(source.get("kind") or "")
    if kind != "store" and kind not in _FOLDER_KINDS:
        warnings.warn(
            f"unknown source.kind {kind!r}; reading per-point CSV folders (known kinds: "
            "'store', 'perpoint_csv')",
            UserWarning,
            stacklevel=3,
        )
    return kind


def _provenance(meta: dict, facility_id: str) -> dict:
    """The report-facing provenance of a store facility (see :mod:`camber._provenance`)."""
    from ._provenance import facility_provenance

    return facility_provenance(meta, facility_id)


def _prepare_store(config: dict, base_dir: str) -> _Prepared:
    """The ``source.kind == "store"`` half of :func:`_prepare`."""
    from .store import ParquetStore

    source = config["source"]
    ctx = _facility_context(config, base_dir)
    store = ParquetStore(_path(base_dir, source["store"]))
    fid = source["facility_id"]
    if fid not in store.facilities():
        raise ValueError(f"facility {fid!r} has no data in store {store.root!r}")
    start, end = source.get("start"), source.get("end")
    site = ctx.site
    # A suspended (or otherwise non-active) facility is skipped with one warning -- the run
    # produces no equipment -- unless the config opts in with "include_inactive": true.
    include_inactive = bool(source.get("include_inactive", config.get("include_inactive", False)))
    active = include_inactive or _facility_is_active(store, fid)
    resample = config.get("resample", "1h")
    mapping = _load_mapping(config, base_dir, default=_identity_mapping())

    refs: list = []
    refs_by_class: dict = {}
    # A skipped (non-active) facility still declares its classes, so a drift section validates.
    for eq in config.get("equipment", []):
        refs_by_class.setdefault(eq["class"], [])
    for eq in config.get("equipment", []) if active else []:
        found = discover_store(
            store,
            fid,
            eq["class"],
            marker_role=eq.get("marker_role"),
            start=start,
            end=end,
            include_inactive=True,  # the lifecycle check was made once, above
        )
        found = _with_refrigerant(_only_named(found, eq), eq)
        refs += found
        refs_by_class.setdefault(eq["class"], []).extend(found)

    shared = None
    so = (config.get("shared_oat") or {}) if active else {}
    meta = store.facilities_meta().get(fid, {})
    tzkw = _site_timezone(source, meta)
    if so.get("file"):
        oat = load_point(_path(base_dir, so["file"]), "oat", **tzkw).resample(resample).mean()
        shared = {Role.OAT: oat}
    elif so.get("equip"):
        role = Role(so.get("role", Role.OAT.value))
        eqs = store.equipment(facility_id=fid).get(fid, {})
        if so["equip"] not in eqs:
            raise ValueError(f"shared_oat equip {so['equip']!r} is not stored in facility {fid!r}")
        ref = StoreEquipRef(so["equip"], eqs[so["equip"]], fid, store.root, start, end)
        frame = resolve(ref, None, (role,), resample=resample)
        if role in frame.columns:
            shared = {Role.OAT: frame[role]}

    min_trust = (config.get("trust_gate") or {}).get("min_trust")
    prov = _provenance(meta, fid)
    out = _Prepared(
        site, resample, mapping, shared, refs, refs_by_class, min_trust, [prov] if prov else [], ctx
    )
    out.timezone = tzkw["timezone"]
    return out


def _prepare_bare(config: dict, base_dir: str) -> _Prepared:
    """A config with no ``source`` and no equipment -- ``mv`` billing entries only (0.92, #64).

    Only the facility identity and a ``shared_oat.file`` are read."""
    ctx = _facility_context(config, base_dir)
    resample = config.get("resample", "1h")
    shared = None
    so = config.get("shared_oat") or {}
    if so.get("file"):
        tzkw = _site_timezone({"timezone": so.get("timezone")})
        oat = load_point(_path(base_dir, so["file"]), "oat", **tzkw).resample(resample).mean()
        shared = {Role.OAT: oat}
    return _Prepared(ctx.site, resample, _identity_mapping(), shared, [], {}, None, [], ctx)


def _prepare(config: dict, base_dir: str) -> _Prepared:
    """Resolve a config's source, mapping, shared points and discovered equipment.

    Shared by :func:`run_config` and :func:`run_drift_config` so the two entry points discover
    equipment identically -- a drift run must see exactly the equipment the ordinary run does.
    """
    from .energy_units import UnitSystem

    units = UnitSystem.from_config(config)  # 0.92 (#69): a bad units block fails up front
    prep = _prepare_sources(config, base_dir)
    prep.units = units
    from .weather_privacy import weather_context

    prep.weather = weather_context(config, base_dir, ctx=prep.ctx)  # 0.94 (#73)
    return prep


def _prepare_sources(config: dict, base_dir: str) -> _Prepared:
    """:func:`_prepare` without the ``units`` block."""
    if "source" not in config and not config.get("equipment"):  # 0.92 (#64): bills-only config
        return _prepare_bare(config, base_dir)
    if _source_kind(config["source"]) == "store":
        return _prepare_store(config, base_dir)
    ctx = _facility_context(config, base_dir)
    site = ctx.site
    resample = config.get("resample", "1h")
    folders = _source_folders(config["source"], base_dir)
    # Inside a workspace a folder config follows the facility's lifecycle state like a store one.
    active = True
    if ctx.workspace and ctx.facility_id:
        include_inactive = bool(
            config["source"].get("include_inactive", config.get("include_inactive", False))
        )
        if not include_inactive:
            from .portfolio import Portfolio

            active = _facility_is_active(Portfolio(ctx.workspace).store, ctx.facility_id)

    mapping = _load_mapping(config, base_dir)
    tzkw = _site_timezone(config["source"])

    shared = None
    so = config.get("shared_oat")
    if so and so.get("file"):
        oat = load_point(_path(base_dir, so["file"]), "oat", **tzkw).resample(resample).mean()
        shared = {Role.OAT: oat}

    refs: list = []
    refs_by_class: dict = {}  # class -> [EquipRef], for class-targeted SOO / drift specs
    # A skipped (non-active) facility still declares its classes, so a drift section validates.
    for eq in config.get("equipment", []):
        refs_by_class.setdefault(eq["class"], [])
    for eq in config.get("equipment", []) if active else []:
        marker = eq.get("marker", "SpaceTemp")
        if eq["class"] == "TERMINAL":  # union of all terminal-unit types
            found = discover_terminals(folders, marker_measure=marker)
        else:
            found = discover(folders, eq["class"], marker_measure=marker)
        found = _with_refrigerant(_only_named(found, eq), eq)
        if tzkw["timezone"] or tzkw["strict_timezone"]:
            found = [dataclasses.replace(r, **tzkw) for r in found]
        refs += found
        refs_by_class.setdefault(eq["class"], []).extend(found)

    # Optional sensor-health gate: a rule whose required inputs aren't trusted declines
    # to fire (see camber.sensorhealth). Off unless the config sets trust_gate.min_trust.
    min_trust = (config.get("trust_gate") or {}).get("min_trust")
    out = _Prepared(site, resample, mapping, shared, refs, refs_by_class, min_trust, [], ctx)
    out.timezone = tzkw["timezone"]
    return out


# --- 0.93 (#39) refrigerant-derivation block (093-refrig) ---------------------------------
def _with_refrigerant(found: list, entry: dict) -> list:
    """Tag refs with an ``equipment`` entry's ``"refrigerant"`` (validated; 0.93, #39).

    A tagged equipment's saturation-referenced roles (subcooling, superheat, discharge superheat,
    approaches) are derived at resolve time from its refrigerant pressures and line temperatures
    (:func:`camber.refrigerant.derive_refrigerant_roles`). An unknown refrigerant fails the run.
    """
    fluid = entry.get("refrigerant")
    if not fluid:
        return found
    from .refrigerant import normalize_refrigerant

    name = normalize_refrigerant(fluid)
    return [dataclasses.replace(r, refrigerant=name) for r in found]


# --- end 0.93 (#39) block ----------------------------------------------------------------------


def _only_named(found: list, entry: dict) -> list:
    """Keep only the equipment an ``equipment`` entry names in ``"equip"`` (all when absent)."""
    names = entry.get("equip")
    if not names:
        return found
    names = {names} if isinstance(names, str) else set(names)
    return [r for r in found if r.equip in names]


def _mv_declined(equip: str, why: str, *, rule: str = "mv_baseline", need: dict | None = None):
    from .rules.base import Finding

    what = "M&V savings" if rule == "mv_savings" else "M&V baseline"
    metrics: dict = {"declined": True, "declined_reason": why}
    if need is not None:  # 0.98 (#88): what data would carry the fit (camber.mandv.sufficiency)
        metrics["data_needed"] = dict(need)
    return Finding(
        rule=rule,
        equip=equip,
        severity="info",
        metrics=metrics,
        summary=f"{equip}: {what} declined -- {why}",
        caveats=[f"{what} not {'computed' if rule == 'mv_savings' else 'fitted'}: {why}"],
    )


def _mv_window(entry: dict, key: str):
    """Validate one optional ``[start, end]`` window of an ``mv`` entry."""
    win = entry.get(key)
    if win is None:
        return None
    if not (isinstance(win, (list, tuple)) and len(win) == 2):
        raise ValueError(f"mv.{key} must be a [start, end] pair, got {win!r}")
    return win


def _finite_or_none(x):
    import math

    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x


def _mv_savings_finding(
    equip: str,
    model,
    st,
    daily_r,
    policy,
    window,
    *,
    kernel: str = "g14",
    declared: bool = False,
    ctx: dict | None = None,
) -> object:
    """One ``mv_savings`` Finding: the baseline projected onto the reporting period (#20).

    With ``ctx`` (the entry's run context) the finding also carries the entry's validity verdicts
    and, when it declares ``adjustments``, the adjusted saving (#21 phases 21b / 21c)."""
    from .mandv import _mvform
    from .mandv.methods import forecast_savings
    from .rules.base import Finding

    days_r = _mvform.row_days(daily_r)  # 0.92 (#64): bills sum as days * per-day energy
    res = forecast_savings(
        model,
        _mvform.design_rows(daily_r, model),
        daily_r["energy"].values,
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=_mvform.n_params(model),
        rho=st.rho_lag1,
        extrapolation=policy,
        kernel=kernel,
        baseline_version=(ctx or {}).get("baseline_version"),
        days=days_r,
    )
    sav = res
    cov = sav.coverage or {}
    n_rep = int(len(daily_r)) if days_r is None else int(days_r.sum())
    metrics = {
        "reporting_period": [str(window[0]), str(window[1])],
        "n_report_days": n_rep,
        "avoided_energy": sav.savings,
        "baseline_projected": sav.projected,
        "reporting_actual": sav.measured,
        "savings_pct": _finite_or_none(sav.savings_pct),
        "fsu": _finite_or_none(sav.fractional_uncertainty),
        "abs_uncertainty": _finite_or_none(sav.abs_uncertainty),
        "confidence": sav.confidence,
        "fsu_extrapolation_factor": sav.fsu_extrapolation_factor,
        "rho": sav.rho,
        "coverage_tier": cov.get("tier"),
        "share_points_outside": cov.get("share_points_outside"),
        "share_energy_outside": cov.get("share_energy_outside"),
        "max_beyond_rel": cov.get("max_beyond_rel"),
        "n_outside": cov.get("n_outside"),
        "declined": bool(sav.declined),
        **_mv_method_metrics(res, declared),
    }
    if days_r is not None:
        metrics["n_report_bills"] = int(len(daily_r))
    caveats = list(sav.caveats)
    if not declared:
        caveats.append(_MV_UNDECLARED)
    if not st.accept:
        caveats.append(
            f"the baseline does not meet {_mv_interval(ctx)} G14 acceptance; this saving is for "
            "information only"
        )
    suffix = ""
    if ctx is not None:
        daily_b = ctx["daily"]
        _mv_validity_metrics(ctx, [("baseline", model, daily_b)], metrics, caveats)
        if _mv_has_ledger(ctx):
            rows = {**_mvform.ledger_rows(daily_r, model), "model": model}
            fits = {"baseline": (daily_b, model), "reporting": (daily_r, model)}
            suffix = _mv_record_adjusted(ctx, res, rows, fits, metrics, caveats)
    if sav.declined:
        metrics["declined_reason"] = sav.declined_reason
        summary = (
            f"{equip}: M&V savings declined -- the baseline does not cover the reporting period "
            f"({cov.get('share_points_outside', 0):.0%} of reporting days outside its support)"
        )
    else:
        pct = metrics["savings_pct"]
        band = metrics["abs_uncertainty"]
        summary = (
            f"{equip}: avoided energy {sav.savings:,.0f}"
            + (f" ({pct:.1%})" if pct is not None else "")
            + (f" ± {band:,.0f} at {sav.confidence:.0%}" if band is not None else "")
            + f" over {n_rep} reporting days"
            + (f" ({len(daily_r)} bills)" if days_r is not None else "")
            + f"; baseline coverage {cov.get('tier')}"
            + suffix
        )
    return Finding(
        rule="mv_savings",
        equip=equip,
        severity="info",
        metrics=metrics,
        summary=summary,
        caveats=caveats,
    )


# --------------------------------------------------------------------------- mv method / kernel
# (#21 phase 21b) -- the ``mv[].method`` and ``mv[].kernel`` keys.

_MV_METHODS = ("forecast", "backcast", "chaining", "standard_conditions", "auto")
_MV_UNDECLARED = (
    "no mv.method declared: the SEP default, forecast, was used. A reported saving should declare "
    "its method, so that it is fixed before the numbers are seen"
)


def _mv_method_spec(entry: dict, period, reporting) -> tuple:
    """Validate ``method`` / ``kernel`` of an ``mv`` entry: ``(method, kernel, declared)``.

    ``kernel`` defaults per issue #21 decision D7: ``g14`` for the single-model forecast and
    backcast, ``exact`` for the multi-model chaining and standard conditions.
    """
    declared = entry.get("method") is not None
    method = entry.get("method") or "forecast"
    if method not in _MV_METHODS:
        raise ValueError(f"mv.method must be one of {_MV_METHODS}, got {method!r}")
    kernel = entry.get("kernel")
    if kernel is None:
        kernel = "exact" if method in ("chaining", "standard_conditions") else "g14"
    if kernel not in ("g14", "exact"):
        raise ValueError(f"mv.kernel must be 'g14' or 'exact', got {kernel!r}")
    if method == "chaining" and kernel != "exact":
        raise ValueError("mv.method 'chaining' needs kernel 'exact' (one model, two projections)")
    if declared and method != "forecast":
        if reporting is None or period is None:
            raise ValueError(f"mv.method {method!r} needs both a period and a reporting_period")
    if method == "chaining" and _mv_window(entry, "intermediate_period") is None:
        raise ValueError("mv.method 'chaining' needs an intermediate_period [start, end]")
    if method == "standard_conditions":
        ny = entry.get("normal_year")
        if not isinstance(ny, (list, tuple)) or len(ny) < 2:
            raise ValueError(
                "mv.method 'standard_conditions' needs normal_year: a list of daily mean outdoor "
                "temperatures (the standard conditions)"
            )
    return method, kernel, declared


def _mv_method_metrics(res, declared: bool) -> dict:
    """The SEP fields every ``mv_savings`` Finding carries (#21 phase 21b)."""
    return {
        "method": res.method,
        "method_declared": bool(declared),
        "basis": res.basis,
        "kernel": res.kernel,
        "enpi": res.enpi,
        "enpi_uncertainty": res.enpi_uncertainty,
        "sep_range_valid": res.sep_range_valid,
        "baseline_version": res.baseline_version,
    }


def _mv_interval(ctx) -> str:
    """The G14 interval a context's baseline is judged at: ``daily``, or ``monthly`` for bills."""
    return (ctx or {}).get("interval", "daily")  # 0.92 (#64)


def _mv_frame(ctx: dict, win, entry: dict | None = None):
    """The context's rows over ``win``: daily from the resolved frame, or bills (0.92, #64)."""
    if ctx.get("slice") is not None:
        return ctx["slice"](win)
    return _mv_daily(ctx["full"], ctx["role"], win, entry, timezone=ctx.get("timezone"))


def _mv_daily(full, role, win, entry: dict | None = None, *, timezone: str | None = None):
    """Daily energy vs temperature over ``win``, with the entry's driver columns (if any)."""
    frame = full.loc[win[0] : win[1]]
    e, t = frame[role].dropna(), frame[Role.OAT].dropna()
    from .mandv import _mvform
    from .mandv.intervalfit import daily_energy_vs_temp

    if not (len(e) and len(t)):
        return None
    daily = daily_energy_vs_temp(e, t, timezone=timezone)
    return _mvform.add_drivers(daily, entry, frame) if entry is not None else daily


# --------------------------------------------------------------------------- mv validity
# (#21 decision D1) -- one ``mv[].validity`` key for the whole entry: which validity regime the
# saving is reported under. It gates the SEP model-validity verdict here and the SEP evidence
# rule of the adjustments ledger (camber.mandv.adjustments.VALIDITY).


def _mv_validity(entry: dict) -> str:
    from .mandv.adjustments import check_validity

    try:
        return check_validity(entry.get("validity", "g14"))
    except ValueError as e:
        raise ValueError(f"mv.{e}") from None


def _mv_validity_metrics(ctx: dict, models: list, metrics: dict, caveats: list) -> None:
    """Record ``validity`` and, under ``sep`` / ``both``, each projecting model's SEP verdict
    (SEP 2019 Ed. 2 §6.4.1). The G14 acceptance caveat is recorded separately, always."""
    from .mandv import _mvform
    from .mandv.stats import logical_signs, model_regression_tests, sep_validity

    validity = ctx["validity"]
    metrics["validity"] = validity
    if validity == "g14":
        return
    verdicts: dict = {}
    for role, model, frame in models:
        try:
            tests = model_regression_tests(
                model,
                _mvform.design_rows(frame, model),
                frame["energy"].values,
                time_index=frame.index,
                weights=_mvform.row_days(frame),  # 0.92 (#64): a billing fit's own weights
            )
            vd = sep_validity(tests, signs=logical_signs(model))
            verdicts[role] = {"sep_valid": bool(vd.sep_valid), "sep_failures": list(vd.failures)}
        except (TypeError, ValueError) as e:
            verdicts[role] = {"sep_valid": None, "sep_failures": [f"not evaluated: {e}"]}
    metrics["sep_valid"] = (
        None
        if any(v["sep_valid"] is None for v in verdicts.values())
        else all(v["sep_valid"] for v in verdicts.values())
    )
    metrics["sep_validity"] = verdicts
    for role, v in verdicts.items():
        if v["sep_valid"] is False:
            caveats.append(
                f"the {role} model is not SEP-valid (SEP 2019 Ed. 2 §6.4.1: "
                f"{'; '.join(v['sep_failures'])}); its SEnPI is not reportable under "
                f"validity '{validity}'"
            )


def _mv_other_method_finding(ctx: dict, method: str, kernel: str) -> object:
    """One ``mv_savings`` Finding by a declared non-forecast SEP method."""
    import numpy as np

    from .mandv import _mvform
    from .mandv import methods as mm
    from .mandv.models import N_PARAMS
    from .mandv.stats import fit_stats
    from .rules.base import Finding

    equip, entry, policy = ctx["equip"], ctx["entry"], ctx["policy"]
    daily_b, daily_r, model = ctx["daily"], ctx["daily_r"], ctx["model"]
    X = _mvform.design_rows

    def fit(d, family=None):
        m = _mvform.fit(d) if family is None else _mvform.fit(d, family=family)
        st = fit_stats(
            d["energy"].values,
            m.predict(X(d, m)),
            _mvform.n_params(m),
            time_index=d.index,
            weights=_mvform.row_days(d),  # 0.92 (#64)
        )
        return m, st

    rows_of = _mvform.ledger_rows  # 0.92 (#64): bills expand to their days for the ledger

    if method == "backcast":
        mr, st_r = fit(daily_r)
        res = mm.backcast_savings(
            mr,
            X(daily_b, mr),
            daily_b["energy"].values,
            cv_rmse=st_r.cv_rmse,
            n_reporting=st_r.n,
            p_reporting=_mvform.n_params(mr),
            rho=st_r.rho_lag1,
            extrapolation=policy,
            kernel=kernel,
            baseline_version=ctx.get("baseline_version"),
            days=_mvform.row_days(daily_b),
        )
        model_note = {"reporting_model": mr.kind, "reporting_r2": st_r.r2}
        model_note.update({f"reporting_{k}": v for k, v in _mvform.metrics(mr).items()})
        used = [("reporting", mr, daily_r)]
        rows = {**rows_of(daily_b, mr), "model": mr, "reporting_index": daily_r.index}
        fits = {"baseline": (daily_b, mr), "reporting": (daily_r, mr)}
    elif method == "chaining":
        inter = _mv_window(entry, "intermediate_period")
        daily_i = _mv_frame(ctx, inter, entry)
        need = ctx.get("min_rows") or int(entry.get("min_days", 60))  # 0.92 (#64): bills count rows
        if daily_i is None or len(daily_i) < need:
            return _mv_declined(equip, "too few intermediate-period days", rule="mv_savings")
        mi, st_i = fit(daily_i)
        res = mm.chained_savings(
            mi,
            X(daily_b, mi),
            daily_b["energy"].values,
            X(daily_r, mi),
            daily_r["energy"].values,
            periods={
                "baseline": list(ctx["period"]),
                "intermediate": list(inter),
                "reporting": list(ctx["reporting"]),
            },
            rho=st_i.rho_lag1,
            extrapolation=policy,
            kernel=kernel,
            baseline_version=ctx.get("baseline_version"),
            days_baseline=_mvform.row_days(daily_b),
            days_reporting=_mvform.row_days(daily_r),
        )
        model_note = {
            "intermediate_period": [str(inter[0]), str(inter[1])],
            "intermediate_model": mi.kind,
            "intermediate_r2": st_i.r2,
        }
        used = [("intermediate", mi, daily_i)]
        rows = {
            "links": [
                {**rows_of(daily_b, mi), "model": mi, "reporting_index": daily_i.index},
                {**rows_of(daily_r, mi), "model": mi},
            ]
        }
        fits = {"baseline": (daily_b, mi), "reporting": (daily_r, mi)}
    else:  # standard_conditions
        # 0.94 (#72): a degree-day baseline on bills is paired with a degree-day reporting model
        # at the same bases, and the normal year's daily temperatures become its degree days
        dd = getattr(model, "heating_base_f", False) is not False
        mr, st_r = fit(daily_r, "dd" if dd else ("cp" if daily_r.attrs.get("dd_kind") else None))
        st_b = ctx["st"]
        normal = np.asarray(entry["normal_year"], dtype=float)
        if dd:
            normal = model.rows_from_temps(normal)
        res = mm.standard_conditions_savings(
            model,
            mr,
            normal,
            kernel=kernel,
            rho_baseline=st_b.rho_lag1,
            rho_reporting=st_r.rho_lag1,
            extrapolation=policy,
            baseline_cv_rmse=st_b.cv_rmse,
            n_baseline=st_b.n,
            p_baseline=_mvform.n_params(model) if dd else N_PARAMS[model.kind],
            reporting_cv_rmse=st_r.cv_rmse,
            n_reporting=st_r.n,
            p_reporting=_mvform.n_params(mr) if dd else N_PARAMS[mr.kind],
            baseline_version=ctx.get("baseline_version"),
        )
        model_note = {"reporting_model": mr.kind, "reporting_r2": st_r.r2}
        used = [("baseline", model, daily_b), ("reporting", mr, daily_r)]
        rows = {"drivers": normal, "model": model, "reporting_index": daily_r.index}
        fits = {"baseline": (daily_b, model), "reporting": (daily_r, mr)}
    cov = res.coverage or {}
    days_r = _mvform.row_days(daily_r)
    metrics = {
        "reporting_period": [str(ctx["reporting"][0]), str(ctx["reporting"][1])],
        "n_report_days": int(len(daily_r)) if days_r is None else int(days_r.sum()),
        "savings": res.savings,
        "projected": res.projected,
        "measured": res.measured,
        "savings_pct": _finite_or_none(res.savings_pct),
        "fsu": _finite_or_none(res.fractional_uncertainty),
        "abs_uncertainty": _finite_or_none(res.abs_uncertainty),
        "confidence": res.confidence,
        "rho": res.rho,
        "coverage_tier": cov.get("tier"),
        "declined": bool(res.declined),
        **model_note,
        **_mv_method_metrics(res, True),
    }
    if res.links:
        metrics["links"] = [
            {k: v for k, v in vars(ln).items() if not k.startswith("_")} for ln in res.links
        ]
    if days_r is not None:
        metrics["n_report_bills"] = int(len(daily_r))
    caveats = list(res.caveats)
    if not ctx["st"].accept:
        caveats.append(
            f"the baseline does not meet {_mv_interval(ctx)} G14 acceptance; this saving is for "
            "information only"
        )
    _mv_validity_metrics(ctx, used, metrics, caveats)
    suffix = ""
    if _mv_has_ledger(ctx):
        suffix = _mv_record_adjusted(ctx, res, rows, fits, metrics, caveats)
    if res.declined:
        metrics["declined_reason"] = res.declined_reason
        summary = f"{equip}: M&V {method} savings declined -- {res.declined_reason}"
    else:
        pct, band = metrics["savings_pct"], metrics["abs_uncertainty"]
        summary = (
            f"{equip}: {method} savings {res.savings:,.0f}"
            + (f" ({pct:.1%})" if pct is not None else "")
            + (f" ± {band:,.0f} at {res.confidence:.0%}" if band is not None else "")
            + (f"; SEnPI {res.enpi:.3f}" if res.enpi is not None else "")
            + f"; coverage {cov.get('tier')}"
            + suffix
        )
    return Finding(
        rule="mv_savings",
        equip=equip,
        severity="info",
        metrics=metrics,
        summary=summary,
        caveats=caveats,
    )


def _mv_proposal_finding(ctx: dict) -> object:
    """``method: auto`` -- an ``mv_method_proposal`` Finding, never a headline saving.

    With an ``adjustments`` ledger, each sensitivity row also shows the method's saving after the
    ledger (``adjusted_*``) beside the unadjusted one -- still a proposal, not a result."""
    import pandas as pd

    from .mandv.methods import select_method
    from .rules.base import Finding

    equip, period, reporting = ctx["equip"], ctx["period"], ctx["reporting"]
    daily = _mv_frame(ctx, (period[0], reporting[1]))
    if daily is None or daily.empty:
        return Finding(
            rule="mv_method_proposal",
            equip=equip,
            severity="info",
            metrics={"declined": True, "declined_reason": "no usable days"},
            summary=f"{equip}: SEP method proposal declined -- no usable days",
        )
    ny = ctx["entry"].get("normal_year")
    base_w = [str(pd.Timestamp(period[0]).date()), str(pd.Timestamp(period[1]).date())]
    rep_w = [str(pd.Timestamp(reporting[0]).date()), str(pd.Timestamp(reporting[1]).date())]
    prop = select_method(
        daily,
        baseline=base_w,
        reporting=rep_w,
        standard_conditions=ny,
        extrapolation=ctx["policy"],
        days="days" if "days" in daily.columns else None,  # 0.92 (#64): bills
    )
    caveats = list(prop.caveats)
    if _mv_has_ledger(ctx):
        _mv_sensitivity_adjusted(ctx, prop, daily, base_w, rep_w, caveats)
    metrics = {
        "proposed": prop.proposed,
        "declined": bool(prop.declined),
        "intermediate_period": prop.intermediate_period,
        "steps": prop.steps,
        "sensitivity": prop.sensitivity,
        "models": prop.models,
        "validity": ctx["validity"],
    }
    what = prop.proposed or "no method (declined)"
    summary = (
        f"{equip}: SEP method proposal -- {what}; {len(prop.sensitivity)} valid method(s) in the "
        "sensitivity table. Declare mv.method to report a saving"
    )
    return Finding(
        rule="mv_method_proposal",
        equip=equip,
        severity="info",
        metrics=metrics,
        summary=summary,
        caveats=caveats,
    )


def _mv_sensitivity_adjusted(ctx, prop, daily, base_w, rep_w, caveats) -> None:
    """Add the adjusted figures to each sensitivity row of a proposal (``auto`` + adjustments).

    The models are refitted exactly as :func:`~camber.mandv.methods.select_method` ranked them
    (same windows, kinds and order), so the ledger is applied to the same results."""
    from .mandv import _mvform
    from .mandv.methods import _dd_rows, _dd_spec, _is_dd, _rank_models, _slice_days

    kinds = ("2P", "3PC", "3PH", "4P", "5P")
    dcol = "days" if "days" in daily.columns else None  # 0.92 (#64): bills, as select_method ranked
    ddspec = _dd_spec(daily, dcol, None)  # 0.95 (#74): bills at selected bases, as ranked there

    def ranked(win):
        T, y, idx, dd = _slice_days(daily, win, "oat", "energy", dcol)
        sub = daily.loc[idx]
        extra = None if ddspec is None else {**ddspec, "rows": _dd_rows(daily, idx, ddspec)}
        return (_rank_models(T, y, idx, kinds, dd, dd=extra) if len(y) > 5 else []), sub

    def best(win):
        c, sub = ranked(win)
        return (c[0].model if c else None), sub

    mb, db = best(base_w)
    mr, dr = best(rep_w)
    if ddspec is not None and mb is not None and mr is not None and _is_dd(mb) != _is_dd(mr):
        # standard conditions use the reporting model of the baseline model's form
        mr_sc = next((c.model for c in ranked(rep_w)[0] if _is_dd(c.model) == _is_dd(mb)), None)
    else:
        mr_sc = mr
    di = mi = None
    if prop.intermediate_period:
        mi, di = best(prop.intermediate_period)

    def cols(d, model=None):  # 0.95 (#74): a degree-day model's rows are its degree days
        return _mvform.ledger_rows(d, model if _is_dd(model) else None)

    for row in prop.sensitivity:
        res = prop.results.get(row["method"])
        name = row["method"]
        if res is None or res.declined:
            continue
        if name == "forecast":
            rows = {**cols(dr, mb), "model": mb}
            fits = {"baseline": (db, mb), "reporting": (dr, mb)}
        elif name == "backcast":
            rows = {**cols(db, mr), "model": mr, "reporting_index": dr.index}
            fits = {"baseline": (db, mr), "reporting": (dr, mr)}
        elif name == "chaining":
            assert di is not None and mi is not None  # a chaining result names its window
            rows = {
                "links": [
                    {**cols(db, mi), "model": mi, "reporting_index": di.index},
                    {**cols(dr, mi), "model": mi},
                ]
            }
            fits = {"baseline": (db, mi), "reporting": (dr, mi)}
        else:
            import numpy as np

            drivers = np.asarray(ctx["entry"]["normal_year"], dtype=float)
            if _is_dd(mb):  # 0.95 (#74): a degree-day model reads degree days at its bases
                drivers = mb.rows_from_temps(drivers)
            rows = {"drivers": drivers, "model": mb, "reporting_index": dr.index}
            fits = {"baseline": (db, mb), "reporting": (dr, mr_sc)}
        adj, err = _mv_apply_ledger(ctx, res, rows, fits)
        if err is not None:
            row["adjustments_refused"] = err
            continue
        row["adjusted_savings"] = adj.savings
        row["adjusted_savings_pct"] = adj.savings_pct
        row["adjusted_enpi"] = adj.enpi
        row["adjusted_enpi_uncertainty"] = adj.enpi_uncertainty
        row["adjusted_abs_uncertainty"] = adj.abs_uncertainty
    caveats.append(
        "the sensitivity table shows each valid method before and after the declared adjustments "
        "ledger; the ledger does not change which method SEP's order proposes"
    )


def _mv_findings(entry: dict, refs: list, prep: _Prepared) -> list:
    """Per equipment: an ``mv_baseline`` Finding (a daily change-point fit vs outdoor temp) and,
    when the entry names a ``reporting_period``, an ``mv_savings`` Finding (#20).

    The order is fixed: the declared method gives the saving, the ``adjustments`` ledger restates
    it, and the Finding reports both (``auto`` only proposes)."""
    from .mandv import _mvform
    from .mandv.coverage import ExtrapolationPolicy, support_of
    from .mandv.intervalfit import daily_energy_vs_temp
    from .mandv.stats import cv_rmse_max_for, fit_stats
    from .mvrun import baseline_window_check
    from .rules.base import Finding

    if entry.get("interval", "daily") != "daily":
        raise ValueError("mv.interval: only 'daily' change-point baselines are supported")
    role = Role(entry.get("role", Role.ENERGY_RATE.value))
    period = _mv_window(entry, "period")
    reporting = _mv_window(entry, "reporting_period")
    policy = ExtrapolationPolicy.from_dict(entry.get("extrapolation"))
    method, kernel, declared = _mv_method_spec(entry, period, reporting)
    _mvform.spec_of(entry)  # mv[].model / drivers: a bad form is a config error, up front
    extra_roles = _mvform.driver_roles(entry)
    validity = _mv_validity(entry)
    schedule = _mv_schedule(entry)
    if entry.get("rebaseline") is not None:  # a bad policy block is a config error, up front
        from .mandv.rebaseline import RebaselinePolicy, events_from_entry

        RebaselinePolicy.from_entry(entry)
        events_from_entry(entry)
    min_days = int(entry.get("min_days", 60))
    adj_specs = _mv_adjustment_specs(entry)
    cv_max = cv_rmse_max_for("daily")
    conv, conv_extra = _mv_trended_conversion(entry, getattr(prep, "units", None))  # #69, #70
    out = []

    def declined(equip, why, need=None):
        out.append(_mv_declined(equip, why, need=need))
        if reporting is not None:
            out.append(_mv_declined(equip, f"no baseline: {why}", rule="mv_savings", need=need))

    for ref in refs:
        full = resolve(ref, prep.mapping, (role, Role.OAT, *extra_roles), resample="1h")
        full = _merge_shared(full, prep.shared)
        if full is None or full.empty or role not in full.columns:
            declined(ref.equip, f"no {role.value} data")
            continue
        if Role.OAT not in full.columns:
            declined(ref.equip, "no outdoor temperature (oat or shared_oat)")
            continue
        miss = [r.value for r in extra_roles if r not in full.columns]
        if miss:
            declined(ref.equip, f"no data for the mv.drivers role(s) {miss}")
            continue
        versions = _mv_versions(prep, ref.equip, role)
        if versions:
            out += _mv_versioned_findings(
                entry,
                ref.equip,
                full,
                role,
                prep,
                versions,
                policy=policy,
                method=method,
                declared=declared,
                validity=validity,
                schedule=schedule,
                adj_specs=adj_specs,
            )
            continue
        frame = full.loc[period[0] : period[1]] if period else full
        daily = daily_energy_vs_temp(
            frame[role].dropna(), frame[Role.OAT].dropna(), timezone=prep.timezone
        )
        daily = _mvform.add_drivers(daily, entry, frame)
        if len(daily) < min_days:
            from .mandv.sufficiency import baseline_need

            need = baseline_need("daily", len(daily), min_n=min_days)
            declined(ref.equip, f"only {len(daily)} usable days (< {min_days})", need)
            continue
        if _mvform.driver_columns(daily):
            model = _mvform.fit(daily)
        else:  # the change-point form, exactly as before 0.90 (no time index at fit)
            from .mandv.models import best_model

            model = best_model(daily["oat"].values, daily["energy"].values)
        # the index lets fit_stats estimate the residuals' lag-1 autocorrelation (rho), which
        # the savings band needs; without it rho stays None and the band is unadjusted
        st = fit_stats(
            daily["energy"].values,
            model.predict(_mvform.design_rows(daily, model)),
            _mvform.n_params(model),
            cv_rmse_max=cv_max,
            time_index=daily.index,
        )
        sup = support_of(daily["oat"].values, quantile=policy.support_quantile)
        verdict = "meets" if st.accept else "does not meet"
        # the baseline-length rule fit_version (camber mv freeze) enforces, as a caveat here (#59)
        win = period or [daily.index.min(), daily.index.max()]
        _, short_why = baseline_window_check(len(daily), win, entry)
        base_caveats = [f"short or gappy baseline: {short_why}"] if short_why else []
        n_before = len(out)
        out.append(
            Finding(
                rule="mv_baseline",
                equip=ref.equip,
                severity="ok" if st.accept else "info",
                metrics={
                    "model": model.kind,
                    "n_days": st.n,
                    "r2": st.r2,
                    "cv_rmse": st.cv_rmse,
                    "nmbe": st.nmbe,
                    "accept": bool(st.accept),
                    "change_points": [round(float(t), 2) for t in model.change_points],
                    "rho": st.rho_lag1,
                    "oat_fit_min": sup["fit_min"],
                    "oat_fit_max": sup["fit_max"],
                    "oat_support_lo": sup["support_lo"],
                    "oat_support_hi": sup["support_hi"],
                    **_mvform.metrics(model),
                },
                summary=(
                    f"{ref.equip}: {model.kind}"
                    + (f" + {'/'.join(model.driver_names)}" if _mvform.metrics(model) else "")
                    + f" baseline, R2 {st.r2:.2f}, CV(RMSE) "
                    f"{st.cv_rmse:.1%} over {st.n} days -- {verdict} daily G14 acceptance"
                    + (" (short baseline)" if short_why else "")
                ),
                caveats=list(base_caveats),
            )
        )
        out[-1].metrics["short_baseline"] = bool(short_why)
        if reporting is None:
            continue
        ctx = {
            "equip": ref.equip,
            "entry": entry,
            "policy": policy,
            "period": period,
            "reporting": reporting,
            "full": full,
            "timezone": prep.timezone,
            "role": role,
            "daily": daily,
            "model": model,
            "st": st,
            "validity": validity,
            "schedule": schedule,
            "adj_specs": adj_specs,
        }
        if method == "auto":
            out.append(_mv_proposal_finding(ctx))
            _add_caveats(out[n_before + 1 :], base_caveats)
            continue
        rframe = full.loc[reporting[0] : reporting[1]]
        r_e, r_t = rframe[role].dropna(), rframe[Role.OAT].dropna()
        daily_r = (
            daily_energy_vs_temp(r_e, r_t, timezone=prep.timezone)
            if len(r_e) and len(r_t)
            else None
        )
        if daily_r is not None:
            daily_r = _mvform.add_drivers(daily_r, entry, rframe)
        if daily_r is None or daily_r.empty:
            out.append(
                _mv_declined(ref.equip, "no usable reporting-period days", rule="mv_savings")
            )
            continue
        if method == "forecast":
            out.append(
                _mv_savings_finding(
                    ref.equip,
                    model,
                    st,
                    daily_r,
                    policy,
                    reporting,
                    kernel=kernel,
                    declared=declared,
                    ctx=ctx,
                )
            )
        else:
            ctx["daily_r"] = daily_r
            out.append(_mv_other_method_finding(ctx, method, kernel))
        _add_caveats(out[n_before + 1 :], base_caveats)  # they rest on the same baseline
    if conv_extra["caveats"] or conv_extra["metrics"]:  # 0.93 (#70): a gas volume's heat content
        for f in out:
            f.metrics.update(conv_extra["metrics"])
        _add_caveats(out, conv_extra["caveats"])
    return _mv_apply_units(out, conv)


# --------------------------------------------------------------------------- 0.92 (#69) units
# A config ``units`` block reports M&V energy in kBtu (IP) or kWh (SI). The fits stay in the
# meter's own unit; only the reported quantities are converted, after the Finding is built, so a
# config without ``units`` is untouched. See camber.energy_units and docs/UNITS.md.

_MV_ENERGY_KEYS = (
    "avoided_energy",
    "baseline_projected",
    "reporting_actual",
    "abs_uncertainty",
    "savings",
    "projected",
    "measured",
    "adjusted_savings",
    "adjusted_abs_uncertainty",
    "adjusted_baseline",
)
_MV_VARIANCE_KEYS = (
    "var_savings",
    "v_param_baseline",
    "v_param_reporting",
    "covariance",
    "v_noise_baseline",
    "v_noise_reporting",
)
_MV_LINK_KEYS = ("projected", "measured", "savings", "abs_uncertainty")
_MV_ADJ_LINK_KEYS = ("baseline", "reporting", "adjusted_baseline", "adjusted_reporting", "savings")
_MV_LEDGER_KEYS = ("amount", "se", "resolved_amount", "resolved_se")
_MV_SENS_KEYS = ("savings", "abs_uncertainty", "adjusted_savings", "adjusted_abs_uncertainty")


def _mv_trended_units(entry: dict, units) -> tuple | None:
    """``(factor, reported unit, meter energy unit, system)`` for a trended ``mv`` entry, or
    ``None`` without a config ``units`` block. The entry's ``units`` names the metered rate (kW,
    Btu/h, kBtu/h, MBH, tons); its hourly integral is the meter's energy unit. A gas meter's
    volume flow (cfh, m3/h) integrates to a volume (0.93, #70), converted with the entry's
    ``heat_content`` or the config's ``units.factor_set`` (:func:`_mv_trended_conversion`)."""
    return _mv_trended_conversion(entry, units)[0]


def _mv_trended_conversion(entry: dict, units) -> tuple:
    """``(conv, extra)``: :func:`_mv_trended_units`' tuple (or ``None``) and ``extra`` =
    ``{"metrics": {...}, "caveats": [...]}`` recording a factor set's use (0.93, #70).

    ``mv[].units`` is a power (kW, Btu/h, ...) or a gas volume flow (``cfh``, ``CCF/h``,
    ``Mcf/h``, ``m3/h``). A volume flow's daily integral is a volume (ft3, m3, ...): under a unit
    system it converts with ``mv[].heat_content`` (e.g. ``"1037 Btu/ft3"``), else with the
    ``units.factor_set`` heat content of ``mv[].meter_type`` (default ``natural_gas``); neither is
    an error, never a default. Without a unit system the fits and savings stay in the meter's
    unit, as before, and ``units`` / ``heat_content`` are only validated.
    """
    from .energy_units import parse_heat_content, quantity_of_rate

    extra: dict = {"metrics": {}, "caveats": []}
    rate, hc, mtype = entry.get("units"), entry.get("heat_content"), entry.get("meter_type")
    kind = qty = None
    if rate is not None:
        try:
            kind, qty = quantity_of_rate(rate)
        except ValueError as e:
            if units is None:
                raise
            raise ValueError(f"mv.units: {e} (a trended meter's units are its rate)") from None
    if hc is not None:
        try:
            if parse_heat_content(hc)[1] != "volume":
                raise ValueError(f"{hc!r} is per mass; a gas meter's heat content is per volume")
        except ValueError as e:
            raise ValueError(f"mv.heat_content: {e}") from None
    if (hc is not None or mtype is not None) and kind != "volume":
        key = "heat_content" if hc is not None else "meter_type"
        raise ValueError(
            f"mv.{key} is for a gas meter trended as a volume flow: give mv.units as cfh, "
            "CCF/h, Mcf/h or m3/h"
        )
    if mtype is not None and getattr(units, "factor_set", None) is None:
        raise ValueError("mv.meter_type is read only with a units.factor_set")
    if units is None:
        return None, extra
    if rate is None:
        raise ValueError(
            f"units.system is {units.system!r}, but mv entry {entry.get('class')!r} does not name "
            'its meter\'s rate unit: add "units": "kW" (or Btu/h, kBtu/h, MBH, tons; cfh or m3/h '
            "for gas metered by volume)"
        )
    assert qty is not None  # a rate was given and parsed above
    if kind == "energy":
        return (units.energy_factor(qty), units.energy, qty, units.system), extra
    if hc is not None:
        k = units.energy_factor(qty, heat_content=hc)
        extra["caveats"].append(f"Gas volume ({qty}) converted with heat content {hc}.")
    elif units.factor_set is not None:
        from .energy_factors import factor_for
        from .energy_units import energy_factor

        try:
            c = factor_for(
                qty, mtype or "natural_gas", factor_set=units.factor_set, region=units.region
            )
        except ValueError as e:
            raise ValueError(f"mv.units: {e}") from None
        k = c.multiplier * energy_factor("kBtu", units.energy)
        extra["metrics"]["energy_factor"] = c.as_dict()
        extra["caveats"] += [f"Converted with {c.describe()}.", *c.caveats]
    else:
        raise ValueError(
            f"mv.units {rate!r} is a gas volume flow: give mv.heat_content (e.g. "
            "'1037 Btu/ft3') or a units.factor_set; there is no default"
        )
    if qty == "Mcf" and hc is not None:
        from .energy_factors import _m_caveat

        extra["caveats"].append(_m_caveat(rate, units.factor_set))
    return (k, units.energy, qty, units.system), extra


def _scale(d: dict, keys, k: float, *, digits: int | None = 2) -> None:
    for key in keys:
        v = d.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d[key] = v * k if digits is None else round(v * k, digits)


def _mv_units_summary(summary: str, old: dict, new: dict, unit: str) -> str:
    """Put the unit after every energy number of an ``mv_savings`` summary, converted."""
    s = summary

    def sub(prefix, key, suffix=""):
        nonlocal s
        if old.get(key) is None or new.get(key) is None:
            return
        a = f"{prefix}{old[key]:,.0f}{suffix}"
        if a in s:
            s = s.replace(a, f"{prefix}{new[key]:,.0f} {unit}{suffix}", 1)

    sub("avoided energy ", "avoided_energy")
    sub(f"{old.get('method')} savings ", "savings")
    sub("± ", "abs_uncertainty", " at ")
    sub("): ", "adjusted_savings")
    ab = old.get("adjusted_abs_uncertainty")
    if ab is not None and s.endswith(f" ± {ab:,.0f}"):
        s = s[: -len(f" ± {ab:,.0f}")] + f" ± {new['adjusted_abs_uncertainty']:,.0f} {unit}"
    return s


def _mv_apply_units(findings: list, conv) -> list:
    """Convert the reported energy of ``mv`` Findings in place (``conv`` from
    :func:`_mv_trended_units` or the billing path); ``None`` leaves them exactly as they are."""
    if conv is None:
        return findings
    k, unit, meter, system = conv
    for f in findings:
        m = f.metrics
        m["unit_system"] = system
        m["meter_unit"] = meter  # the fits, coefficients and indicator rates stay in this unit
        if m.get("declined") and f.rule != "mv_savings":
            continue
        if f.rule == "mv_baseline":
            continue
        old = dict(m)
        _scale(m, _MV_ENERGY_KEYS, k)
        # nested rows are copied before scaling: they may share dicts with the method results
        for key, keys in (
            ("links", _MV_LINK_KEYS),
            ("adjusted_links", _MV_ADJ_LINK_KEYS),
            ("adjustments", _MV_LEDGER_KEYS),
            ("waterfall", ("value",)),
            ("sensitivity", _MV_SENS_KEYS),
        ):
            if not isinstance(m.get(key), list):
                continue
            rows = [dict(r) if isinstance(r, dict) else r for r in m[key]]
            for r in rows:
                if not isinstance(r, dict):
                    continue
                _scale(r, keys, k)
                if isinstance(r.get("sep_terms"), dict):
                    r["sep_terms"] = dict(r["sep_terms"])
                    _scale(r["sep_terms"], list(r["sep_terms"]), k)
                if isinstance(r.get("uncertainty_terms"), dict):
                    r["uncertainty_terms"] = dict(r["uncertainty_terms"])
                    _scale(r["uncertainty_terms"], _MV_VARIANCE_KEYS, k * k, digits=None)
            m[key] = rows
        m["energy_unit"] = unit
        if f.rule == "mv_savings":
            f.summary = _mv_units_summary(f.summary, old, m, unit)
    return findings


def _add_caveats(findings: list, caveats: list) -> None:
    for f in findings:
        f.caveats = list(f.caveats or []) + [c for c in caveats if c not in (f.caveats or [])]


# --- versioned M&V baselines (#21 phase 21d) -------------------------------------------------
# When the facility's M&V baseline store (camber.mandv.rebaseline.MVBaselineStore) holds a frozen
# version for a meter, the run uses it -- read-only, never refitting or writing -- and records the
# version on every result. Triggers (T1-T6) become ``mv_trigger`` Findings; an unresolved one cuts
# the saving at its date (a partial result with a caveat), because CAMBER never rebaselines
# automatically: moving the baseline is ``camber mv rebaseline``.


def _mv_store_readonly(config: dict, base_dir: str, ctx):
    """The config's M&V baseline store for reading (``None`` when none can be named)."""
    from .mvrun import open_mv_store

    store, _path_, _ctx = open_mv_store(config, base_dir=base_dir, ctx=ctx, required=False)
    return store


def _mv_versions(prep, equip: str, role) -> list:
    from .mandv.rebaseline import mv_kind

    store = getattr(prep, "mv_store", None)
    if store is None:
        return []
    return store.versions(prep.site, equip, mv_kind(role))


def _mv_trigger_finding(equip: str, tr, version: str):
    from .rules.base import Finding

    live = not tr.resolved
    sev = "warn" if (live and tr.blocks) else "info"
    state = f"resolved -- {tr.resolved_by}" if tr.resolved else "unresolved"
    return Finding(
        rule="mv_trigger",
        equip=equip,
        severity=sev,
        metrics={**tr.as_dict(), "baseline_version": version},
        summary=f"{equip}: M&V trigger {tr.key} against {version} -- {tr.title}: {tr.detail} "
        f"({state}; calls for {tr.outcome.replace('_', ' ')})",
        caveats=(
            [
                "never applied automatically: `camber mv propose` shows the options, and "
                "`camber mv rebaseline` / `camber mv adjust` record the operator's decision"
            ]
            if live
            else []
        ),
    )


def _mv_versioned_findings(
    entry,
    equip,
    full,
    role,
    prep,
    versions,
    *,
    policy,
    method,
    declared,
    validity,
    schedule,
    adj_specs,
    bills=None,
) -> list:
    """The ``mv`` Findings of one meter measured against its frozen, versioned baseline.

    ``bills`` (0.94, #72) is a billing entry's ``{"frame", "slice", "min_rows", "notes"}``: the
    bills frame at the version's degree-day bases and the wholly-inside slicer. The rows are then
    bills, judged at the monthly G14 thresholds and weighted by their days."""
    import pandas as pd

    from .mandv import _mvform
    from .mandv.intervalfit import daily_energy_vs_temp
    from .mandv.models import N_PARAMS
    from .mandv.rebaseline import (
        RebaselinePolicy,
        assess_triggers,
        event_phrase,
        events_from_entry,
        first_block,
        new_baseline_window,
        version_label,
    )
    from .mandv.stats import cv_rmse_max_for, fit_stats
    from .mvrun import _fit_valid, versioned_rows
    from .rules.base import Finding

    store = prep.mv_store
    out: list = []
    if bills is None:
        daily_all = daily_energy_vs_temp(
            full[role].dropna(), full[Role.OAT].dropna(), timezone=prep.timezone
        )
        daily_all = _mvform.add_drivers(daily_all, entry, full)
    else:  # 0.94 (#72): bills
        daily_all = bills["frame"]
    reporting = _mv_window(entry, "reporting_period")
    if reporting is None:
        rec = versions[-1]
    else:
        rec = store.in_force(prep.site, equip, versions[-1].kind, reporting[0])
    live_rec = rec or versions[-1]
    label = version_label(live_rec)
    model = store.model_of(live_rec)
    if bills is None:
        daily, same = versioned_rows(daily_all, live_rec)
    else:
        from .mandv.rebaseline import fit_frame_sha256

        daily = bills["slice"]([live_rec.period_start, live_rec.period_end])
        daily = daily_all.iloc[:0] if daily is None else daily
        same = fit_frame_sha256(daily) == (live_rec.provenance or {}).get("fit_frame_sha256")
    if len(daily) <= N_PARAMS.get(getattr(model, "kind", ""), 5):
        out.append(_mv_declined(equip, f"no data under the frozen baseline {label}'s window"))
        return out
    try:
        X = _mvform.design_rows(daily, model)
    except ValueError as e:
        out.append(_mv_declined(equip, f"the frozen baseline {label}: {e}"))
        return out
    st = fit_stats(
        daily["energy"].values,
        model.predict(X),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("daily" if bills is None else "monthly"),
        time_index=daily.index,
        weights=_mvform.row_days(daily),
    )
    prov = live_rec.provenance or {}
    caveats = []
    if not same:
        caveats.append(
            f"the data under {label}'s window changed since it was frozen (fit-frame sha256 "
            "differs): the frozen model is used as recorded, but it no longer reproduces"
        )
    out.append(
        Finding(
            rule="mv_baseline",
            equip=equip,
            severity="ok" if st.accept else "info",
            metrics={
                "model": getattr(model, "kind", type(model).__name__),
                "n_days": st.n,
                "r2": st.r2,
                "cv_rmse": st.cv_rmse,
                "nmbe": st.nmbe,
                "accept": bool(st.accept),
                "rho": st.rho_lag1,
                "baseline_version": label,
                "versions": len(versions),
                "window": [live_rec.period_start, live_rec.period_end],
                "frozen_at": live_rec.frozen_at,
                "accepted_by": live_rec.accepted_by,
                "frozen_method": prov.get("method"),
                "baseline_data_changed": not same,
                **_mvform.metrics(model),
                **({} if bills is None else bills.get("metrics", {})),
            },
            summary=(
                f"{equip}: frozen M&V baseline {label} ({getattr(model, 'kind', '')}, "
                f"{live_rec.period_start}..{live_rec.period_end}), R2 {st.r2:.2f}, CV(RMSE) "
                f"{st.cv_rmse:.1%} -- read from the versioned store, not refitted"
            ),
            caveats=caveats,
        )
    )
    if reporting is None:
        return out
    if rec is None:
        out.append(
            _mv_declined(
                equip,
                "no frozen baseline version ended before the reporting period starts",
                rule="mv_savings",
            )
        )
        return out
    fmethod = prov.get("method") or "forecast"
    if declared and method not in (fmethod, "auto"):
        out.append(
            _mv_declined(
                equip,
                f"mv.method {method!r} differs from the method frozen with {label} "
                f"({fmethod!r}); changing the declared method is a rebaseline-class action "
                "(`camber mv rebaseline`)",
                rule="mv_savings",
            )
        )
        return out
    kernel = prov.get("kernel") or (
        "exact" if fmethod in ("chaining", "standard_conditions") else "g14"
    )
    pol = RebaselinePolicy.from_entry(entry)
    events, statics = events_from_entry(entry)
    r0, r1 = pd.Timestamp(reporting[0]).normalize(), pd.Timestamp(reporting[1]).normalize()
    nxt = next(
        (v for v in versions if v.provenance.get("version") == (prov.get("version") or 0) + 1), None
    )
    seen = daily_all.loc[: r1 + pd.Timedelta(hours=23)]
    trig = assess_triggers(
        seen,
        model,
        baseline=[rec.period_start, rec.period_end],
        policy=pol,
        reporting=[r0, r1],
        fit_valid=_fit_valid(prov),
        events=events,
        static_factors=statics,
        ledger=store.ledger(rec),
        next_version_start=None if nxt is None else nxt.period_start,
        intermediate_period=(entry.get("intermediate_period") if fmethod == "chaining" else None),
        extrapolation=policy,
    )
    out += [_mv_trigger_finding(equip, t, label) for t in trig]
    cut_caveats = []
    if nxt is not None:
        nd = pd.Timestamp((nxt.provenance or {}).get("trigger_date") or nxt.period_start)
        if nd <= r1:
            r1 = nd - pd.Timedelta(days=1)
            cut_caveats.append(
                f"{version_label(nxt)} supersedes {label} from {nd.date()}: this saving stops "
                "there; `camber mv report` chains the versions"
            )
    # any unresolved blocking trigger since the baseline ended counts, not only those inside the
    # requested reporting days
    blk = first_block([t for t in trig if pd.Timestamp(t.date) <= r1])
    if blk is not None:
        if blk.outcome == "rebaseline" and bills is not None:
            what = (
                f"{event_phrase(blk)}; rebaseline the bills over a new window "
                "(`camber mv rebaseline --period`)"
            )
        elif blk.outcome == "rebaseline":
            win = new_baseline_window(
                daily_all, after=blk.date, policy=pol, event=event_phrase(blk)
            )
            if win.ok and win.window:
                what = (
                    f"{event_phrase(blk)}; a rebaseline window {win.window[0]}..{win.window[1]} "
                    "is available (`camber mv rebaseline`)"
                )
            else:
                what = str(win.declined_reason)
        else:
            what = f"{event_phrase(blk)}; record an NRA (`camber mv adjust`) or rebaseline"
        if pd.Timestamp(blk.date) <= r0:
            f = _mv_declined(equip, f"{what} ({blk.key}: {blk.detail})", rule="mv_savings")
            f.metrics["baseline_version"] = label
            f.metrics["triggers"] = [t.key for t in trig if not t.resolved]
            out.append(f)
            return out
        r1 = pd.Timestamp(blk.date) - pd.Timedelta(days=1)
        cut_caveats.append(
            f"partial: savings after {r1.date()} declined -- {what} ({blk.key}: {blk.detail})"
        )
    if bills is None:
        daily_r = daily_all.loc[r0 : r1 + pd.Timedelta(hours=23)]
    else:
        daily_r = bills["slice"]([r0, r1])
        daily_r = daily_all.iloc[:0] if daily_r is None else daily_r
    if daily_r.empty:
        what = "days" if bills is None else "bills"
        out.append(_mv_declined(equip, f"no usable reporting-period {what}", rule="mv_savings"))
        return out
    win_r = [str(r0.date()), str(r1.date())]
    ctx = {
        "equip": equip,
        "entry": entry,
        "policy": policy,
        "period": [rec.period_start, rec.period_end],
        "reporting": win_r,
        "full": full,
        "timezone": prep.timezone,
        "role": role,
        "daily": daily,
        "model": model,
        "st": st,
        "validity": validity,
        "schedule": schedule,
        "adj_specs": adj_specs,
        "baseline_version": label,
        "stored_ledger": store.ledger(rec),
        "daily_r": daily_r,
    }
    if bills is not None:  # 0.94 (#72): bills, not days
        ctx.update(slice=bills["slice"], interval="monthly", min_rows=bills["min_rows"])
    fnd: Any
    if method == "auto":
        fnd = _mv_proposal_finding(ctx)
    elif fmethod == "forecast":
        fnd = _mv_savings_finding(
            equip, model, st, daily_r, policy, win_r, kernel=kernel, declared=True, ctx=ctx
        )
    else:
        fnd = _mv_other_method_finding(ctx, fmethod, kernel)
    fnd.metrics["baseline_version"] = label
    fnd.metrics["partial"] = bool(cut_caveats)
    fnd.metrics["reporting_period_requested"] = [str(reporting[0]), str(reporting[1])]
    fnd.metrics["triggers"] = [t.key for t in trig if not t.resolved]
    fnd.caveats = list(fnd.caveats or []) + caveats + cut_caveats
    if not declared:
        fnd.caveats = [c for c in fnd.caveats if c != _MV_UNDECLARED]
        fnd.metrics["method_declared"] = True  # frozen with the baseline
    out.append(fnd)
    return out


# --- mv[].adjustments: non-routine and static-factor adjustments (#21 phase 21c) -------------


def _mv_schedule(entry: dict):
    """The entry's ECM dates and settle window (``ecm_dates``, ``settle_days``), validated once.

    :class:`camber.mandv.adjustments.EcmSchedule` is the one home of both; phase 21d's
    rebaselining policy reads the same object."""
    from .mandv.adjustments import EcmSchedule

    ecm = entry.get("ecm_dates") or ()
    if isinstance(ecm, str) or not isinstance(ecm, (list, tuple)):
        raise ValueError(f"mv.ecm_dates must be a list of dates, got {ecm!r}")
    try:
        return EcmSchedule.from_dict(
            {k: entry[k] for k in ("ecm_dates", "settle_days") if entry.get(k) is not None}
        )
    except ValueError as e:
        raise ValueError(f"mv: {e}") from None


def _mv_adjustment_specs(entry: dict) -> list:
    """Validate an ``mv`` entry's ``adjustments`` ledger up front (a bad entry is a config error).

    Each item is a :func:`camber.mandv.adjustments.adjustment_from_dict` dict (``"kind": "nra"``
    or ``"static"``). An ``indicator`` NRA carries no numbers: it is estimated per meter, on the
    baseline days (``"fit_period": "baseline"``) or the reporting days (``"reporting"``), by
    default the period its ``start`` falls in.
    """
    from .mandv.adjustments import NRA_METHODS, adjustment_from_dict

    specs = entry.get("adjustments") or []
    if not isinstance(specs, list):
        raise ValueError("mv.adjustments must be a list of adjustment objects")
    if specs and entry.get("reporting_period") is None:
        raise ValueError("mv.adjustments needs a reporting_period to adjust")
    out = []
    for k, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise ValueError(f"mv.adjustments[{k}] must be an object, got {spec!r}")
        d = dict(spec)
        fp = d.pop("fit_period", None)
        if d.get("kind", "nra") == "nra" and d.get("method") == "indicator":
            if fp not in (None, "baseline", "reporting"):
                raise ValueError(f"mv.adjustments[{k}].fit_period must be baseline or reporting")
            # validate the rest with a placeholder estimate; the real one is fitted per meter
            adjustment_from_dict({**d, "rate": 0.0, "rate_se": 0.0})
        else:
            if d.get("method") not in NRA_METHODS + ("proportional", "engineering"):
                raise ValueError(f"mv.adjustments[{k}]: unknown method {d.get('method')!r}")
            adjustment_from_dict(d)
        out.append((d, fp))
    return out


def _mv_apply_ledger(ctx: dict, res, rows: dict, fits: dict) -> tuple:
    """Build the entry's ledger for one meter and apply it to ``res``: ``(adjusted, error)``.

    ``rows`` are the per-row keyword arguments of
    :func:`~camber.mandv.adjustments.apply_adjustments` for the method; ``fits`` maps
    ``"baseline"`` / ``"reporting"`` to the ``(daily frame, model)`` an indicator is estimated on.
    """
    import pandas as pd

    from .mandv import _mvform
    from .mandv.adjustments import (
        ConfoundedAdjustment,
        adjustment_from_dict,
        apply_adjustments,
        estimate_nre_indicator,
    )

    if res.declined:
        return None, f"the unadjusted saving is declined ({res.declined_reason})"
    reporting = ctx["reporting"]
    try:
        ledger = []
        for d, fp in ctx["adj_specs"]:
            if d.get("kind", "nra") == "nra" and d.get("method") == "indicator":
                start = pd.Timestamp(d["start"])
                if fp is None:
                    fp = "reporting" if start >= pd.Timestamp(reporting[0]) else "baseline"
                frame, model = fits[fp]
                kw = {k: d.get(k) for k in ("end", "reason", "evidence", "approved_by")}
                kw["reason"] = kw["reason"] or ""
                ledger.append(
                    estimate_nre_indicator(
                        _mvform.design_rows(frame, model),
                        frame["energy"].values,
                        frame.index,
                        start=start,
                        fit_period=fp,
                        model=model,
                        weights=_mvform.row_days(frame),  # 0.92 (#64): bills, per-day rate
                        **kw,
                    )
                )
            else:
                ledger.append(adjustment_from_dict(d))
        # the ledger recorded on the stored baseline version (`camber mv adjust`), dated inside
        # the rows this saving covers
        lo = pd.Timestamp(ctx["period"][0]) if ctx.get("period") else None
        hi = pd.Timestamp(reporting[1])
        for a in ctx.get("stored_ledger") or ():
            if (lo is None or pd.Timestamp(a.start) >= lo) and pd.Timestamp(a.start) <= hi:
                ledger.append(a)
        entry = ctx["entry"]
        adj = apply_adjustments(
            res,
            ledger,
            schedule=ctx["schedule"],
            validity=ctx["validity"],
            materiality_threshold=float(entry.get("materiality_threshold", 0.0)),
            **rows,
        )
    except (ConfoundedAdjustment, ValueError, TypeError) as e:
        return None, str(e)
    return adj, None


def _mv_has_ledger(ctx) -> bool:
    """Whether an ``mv`` run context carries adjustments (config ledger or stored ledger)."""
    return bool(ctx and (ctx.get("adj_specs") or ctx.get("stored_ledger")))


def _mv_record_adjusted(ctx: dict, res, rows: dict, fits: dict, metrics: dict, caveats) -> str:
    """Apply the ledger to a method's result and record it in the Finding; return the summary
    suffix. A refused ledger records ``adjustments_refused`` and a caveat and leaves the
    unadjusted saving standing."""
    if res.declined:
        metrics["adjusted"] = None
        return ""
    adj, err = _mv_apply_ledger(ctx, res, rows, fits)
    if err is not None:
        metrics["adjusted"] = None
        metrics["adjustments_refused"] = err
        caveats.append(f"adjustments not applied: {err}")
        return "; adjustments refused"
    metrics["adjusted"] = True
    metrics["adjusted_savings"] = adj.savings
    metrics["adjusted_savings_pct"] = adj.savings_pct
    metrics["adjusted_abs_uncertainty"] = adj.abs_uncertainty
    metrics["adjusted_baseline"] = adj.adjusted_baseline
    metrics["adjusted_enpi"] = adj.enpi
    metrics["adjusted_enpi_uncertainty"] = adj.enpi_uncertainty
    if adj.links:
        metrics["adjusted_links"] = adj.links
    metrics["adjustments"] = [_json_ledger(e) for e in adj.ledger]
    metrics["waterfall"] = [w.as_dict() for w in adj.waterfall]
    caveats.extend(c for c in adj.caveats if c not in caveats)
    band = adj.abs_uncertainty
    n_mat = sum(1 for e in adj.ledger if e.get("material"))
    return (
        f"; adjusted for {len(adj.ledger)} non-routine/static entr"
        + ("y" if len(adj.ledger) == 1 else "ies")
        + f" ({n_mat} material): {adj.savings:,.0f}"
        + (f" ± {band:,.0f}" if band is not None else "")
    )


def _json_ledger(e: dict) -> dict:
    """A ledger entry for Finding metrics: the fit's covariance matrix is dropped (bulky)."""
    import math

    out = {k: v for k, v in e.items() if k != "fit"}
    fit = e.get("fit")
    if fit:
        out["fit"] = {k: fit[k] for k in ("fit_period", "names", "beta", "n", "p", "df", "rho")}
    for k, v in out.items():
        if isinstance(v, float) and not math.isfinite(v):
            out[k] = None
    return out


def _drift_window(spec: dict, key: str, *, required: bool = True):
    """Validate and return one ``(start, end)`` window from a drift spec.

    Explicit windows are the whole point of a drift comparison, so a malformed one fails fast and
    names the offending key rather than surfacing three frames deep inside the period slicer.
    """
    win = spec.get(key)
    if win is None:
        if required:
            raise ValueError(f"drift.{key} is required: a drift run compares two explicit windows")
        return None
    if isinstance(win, str) or len(list(win)) != 2:
        raise ValueError(f"drift.{key} must be a [start, end] pair, got {win!r}")
    return tuple(win)


def _drift_families(spec: dict, refs_by_class: dict) -> list:
    """Validate the ``drift.families`` entries against the config's equipment classes."""
    out = []
    for entry in spec.get("families", []):
        cls = entry["class"]
        fam = entry["family"]
        if cls not in refs_by_class:
            raise ValueError(
                f"drift family class {cls!r} is not in the config's equipment list "
                f"(known: {sorted(refs_by_class)})"
            )
        if fam not in DRIFT_FAMILIES:
            raise KeyError(f"unknown drift family {fam!r} (known: {family_names()})")
        e = dict(entry)
        for key in ("baseline", "current"):
            if key in e:
                win = _drift_window(e, key)
                e[key] = win
        if e.get("reference") is not None:
            e["reference"] = _drift_reference(e["reference"], f"{cls}:{fam}", refs_by_class)
        if e.get("coil_leak") is not None or e.get("coil_leak_params") is not None:
            e["coil_leak"], e["coil_leak_params"] = _drift_coil_leak(e, f"{cls}:{fam}")
        out.append(e)
    return out


# --- 0.100 (#100) opt-in coil-valve leak drift (0100-leak-drift) ---------------------------------


def _drift_coil_leak(entry: dict, where: str) -> tuple:
    """Validate ``drift.families[].coil_leak`` (a list of ``"cooling"`` / ``"heating"``, ``ahu``
    family only) and ``coil_leak_params`` (constructor overrides of
    :class:`~camber.rules.coil_leak_rule.CoilLeakDrift`). Returns ``(coils, params)``."""
    from .driftrun import COIL_LEAK_COILS
    from .rules.coil_leak_rule import CoilLeakDrift
    from .store.modelstore import BaselineStore

    coils = entry.get("coil_leak")
    params = entry.get("coil_leak_params")
    if entry.get("family") != "ahu":
        raise ValueError(f"drift family {where}: coil_leak applies to the 'ahu' family only")
    if isinstance(coils, str) or not isinstance(coils, (list, tuple)) or not coils:
        raise ValueError(
            f"drift family {where}: coil_leak must be a non-empty list of "
            f"{list(COIL_LEAK_COILS)}, got {coils!r}"
        )
    bad = [c for c in coils if c not in COIL_LEAK_COILS]
    if bad:
        raise ValueError(
            f"drift family {where}: unknown coil_leak coil(s) {bad} "
            f"(known: {list(COIL_LEAK_COILS)})"
        )
    params = dict(params or {})
    fixed = sorted(set(params) & {"store", "site", "run_id", "coil", "freeze_if_missing"})
    if fixed:
        raise ValueError(f"drift family {where}: coil_leak_params may not set {fixed}")
    try:
        CoilLeakDrift(BaselineStore(), **params)
    except TypeError as exc:
        raise ValueError(f"drift family {where}: invalid coil_leak_params: {exc}") from exc
    return list(dict.fromkeys(coils)), params


# --- end 0.100 coil-valve leak drift --------------------------------------------------------------


# --- 0.98 (#86, S4) declared drift reference (098-plant-reference) ------------------------------
_REFERENCE_KEYS = ("equip", "period")


def _drift_reference(ref, where: str, refs_by_class: dict) -> dict:
    """Validate one ``drift.families[].reference``: ``{"equip": name}`` (a discovered equipment,
    optionally with a ``"period"``) or ``{"period": [start, end]}`` (a known-good window of the
    same equipment)."""
    if not isinstance(ref, dict) or not ref:
        raise ValueError(
            f"drift family {where}: reference must be an object naming an 'equip' and/or a "
            f"'period', got {ref!r}"
        )
    unknown = sorted(set(ref) - set(_REFERENCE_KEYS))
    if unknown:
        raise ValueError(
            f"drift family {where}: unknown reference key(s) {unknown} "
            f"(known: {list(_REFERENCE_KEYS)})"
        )
    out: dict = {}
    if ref.get("period") is not None:
        out["period"] = _drift_window(ref, "period")
    equip = ref.get("equip")
    if equip is not None:
        known = sorted(r.equip for refs in refs_by_class.values() for r in refs)
        if not isinstance(equip, str) or equip not in known:
            raise ValueError(
                f"drift family {where}: reference equip {equip!r} was not discovered "
                f"(discovered: {known[:12]}{' ...' if len(known) > 12 else ''})"
            )
        out["equip"] = equip
    if not out:
        raise ValueError(f"drift family {where}: reference names neither an 'equip' nor a 'period'")
    return out


def _all_reference(fams: list) -> bool:
    """True when every drift family declares a reference (no frozen store is then read)."""
    return bool(fams) and all(e.get("reference") for e in fams)


# --- end 0.98 declared drift reference -----------------------------------------------------------


def _rule_level_skip(rule):
    """0.98 (#88): the :class:`RuleSkip` for a configured rule that produced nothing anywhere and
    recorded no per-equipment skip (no equipment carried any of its inputs)."""
    from .rules.base import RuleSkip, _missing_labels

    missing = _missing_labels(rule, ())
    return RuleSkip(rule.name, "", "", missing, "missing_inputs" if missing else "no_verdict")


# --- 0.98 (#92, 098-followups): the site elevation for a derived wet-bulb ----------------------
#: The rules that take the config's ``site_elevation_ft``: every built-in rule whose constructor
#: has an ``elevation_ft`` parameter (it derives a wet-bulb from OAT + RH). The drift detectors
#: get it through :func:`camber.driftrun.run_drift`.
_SITE_ELEVATION_KEY = "site_elevation_ft"


def site_elevation_ft(config: dict) -> float | None:
    """The config's ``site_elevation_ft`` (ft above sea level), validated; ``None`` when absent.

    One key for the site: it feeds every rule and drift detector that derives a wet-bulb from OAT
    + RH (``cooling_tower_approach``, ``condenser_water_reset``, ``cooling_tower_approach_drift``,
    ``cooling_tower_fan_effort_drift``). A rule's own ``elevation_ft`` or ``pressure_psia`` param
    wins over it. Raises ``ValueError`` on a non-number or a value outside -1,500 to 15,000 ft.
    """
    v = config.get(_SITE_ELEVATION_KEY)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise ValueError(f"{_SITE_ELEVATION_KEY} must be a number of feet, got {v!r}")
    if not -1500.0 <= float(v) <= 15000.0:
        raise ValueError(
            f"{_SITE_ELEVATION_KEY} {v!r} is outside -1,500 to 15,000 ft (give feet, not metres)"
        )
    return float(v)


def _with_site_elevation(name: str, params: dict, elevation_ft: float | None) -> dict:
    """``params`` plus ``elevation_ft`` when the rule takes one and sets neither it nor a
    measured ``pressure_psia`` itself."""
    if elevation_ft is None or "elevation_ft" in params or "pressure_psia" in params:
        return params
    fac = rule_factories().get(name)
    if fac is None:
        return params
    import inspect

    try:
        sig = inspect.signature(fac[0])
    except (TypeError, ValueError):  # pragma: no cover - builtin classes always have one
        return params
    if "elevation_ft" not in sig.parameters:
        return params
    return {**params, "elevation_ft": elevation_ft}


def _param_basis(name: str, params: dict, basis) -> dict | None:
    """A rule entry's ``"basis"`` ({param: where its value came from}) as finding metrics.

    0.98 (#90): a template that sets a calibrated value (``"params": {"fan_heat_f": 1.0}``) can
    say where it came from (``"basis": {"fan_heat_f": "calibrated on lbnl-sdahu fault_free"}``);
    each finding of the rule then records ``metrics["param_basis"]`` =
    ``{param: {"value": ..., "basis": ...}}``. A basis for a parameter the entry does not set
    records the rule's default. Unknown parameters raise ``ValueError`` (fail fast on a typo).
    """
    if not basis:
        return None
    if not isinstance(basis, dict):
        raise ValueError(f"rule {name!r}: 'basis' must map parameter names to text")
    from .rules.param_docs import rule_params

    defaults = {rp.name: rp.default for rp in rule_params(name)}
    unknown = sorted(set(basis) - set(defaults) - set(params))
    if unknown:
        raise ValueError(f"rule {name!r}: 'basis' names unknown parameter(s) {', '.join(unknown)}")
    return {k: {"value": params.get(k, defaults.get(k)), "basis": str(v)} for k, v in basis.items()}


def run_config(config: dict, *, base_dir: str = ".") -> RunResult:
    """Execute a config dict: discover equipment, run the named rules, build a report.

    Paths in the config resolve against ``base_dir``. Unknown rule names raise
    ``KeyError`` (fail fast on a typo).
    """
    prep = _prepare(config, base_dir)
    site, resample, mapping, shared = prep.site, prep.resample, prep.mapping, prep.shared
    refs, refs_by_class, min_trust = prep.refs, prep.refs_by_class, prep.min_trust

    # -- 0.91 (#61): a declared served-by topology replaces the naming guess for fleet rules
    topology, topology_source = None, None
    if config.get("topology") is not None:
        from .topology_infer import topology_from_config

        topology, topology_source = topology_from_config(
            config["topology"], base_dir=base_dir, equip_ids=[r.equip for r in refs]
        )

    reg = builtin_registry()
    findings: list = []
    ran: list = []
    skipped: list = []  # 0.98 (#88): RuleSkip records from the rule runners
    # 0.92 (#17): the "ventilation" section configures the system-level 62.1 VRP rule
    vent_rule = _ventilation_rule(config, base_dir)
    if vent_rule is not None:
        reg.register(vent_rule)
    site_elev = site_elevation_ft(config)  # 0.98 (#92)
    for entry in config.get("rules", []):
        # A rule entry is either a bare name "economizer_high_limit" (defaults) or a dict
        # {"name": ..., "params": {...}} that overrides the rule's constructor for this run.
        basis = None
        if isinstance(entry, dict):
            name = entry["name"]
            params = entry.get("params") or {}
            basis = _param_basis(name, params, entry.get("basis"))
        else:
            name, params = entry, {}
        # 0.98 (#92): the site elevation reaches every rule that derives a wet-bulb
        params = _with_site_elevation(name, params, site_elev)
        if params:
            reg.register(make_rule(name, **params))  # override the default instance
        rule = reg.get(name)  # KeyError on unknown name
        n_skip = len(skipped)
        n_before = len(findings)
        if is_fleet(rule):
            f = reg.run_fleet(
                name,
                refs,
                mapping,
                resample=resample,
                shared=shared,
                min_trust=min_trust,
                topology=topology,
                skipped=skipped,
            )
            if f is not None:
                findings.append(f)
        else:
            got = reg.run(
                name,
                refs,
                mapping,
                resample=resample,
                shared=shared,
                min_trust=min_trust,
                skipped=skipped,
            )
            findings += got
            if not got and len(skipped) == n_skip:
                skipped.append(_rule_level_skip(rule))
        if basis:  # 0.98 (#90): a calibrated value's provenance travels with its findings
            for f in findings[n_before:]:
                f.metrics = {**(f.metrics or {}), "param_basis": basis}
        ran.append(name)
    # 0.92 (#17): a configured ventilation section runs even when "rules" omits the rule
    if vent_rule is not None and vent_rule.name not in ran:
        f = reg.run_fleet(
            vent_rule.name,
            refs,
            mapping,
            resample=resample,
            shared=shared,
            min_trust=min_trust,
            topology=topology,
            skipped=skipped,
        )
        if f is not None:
            findings.append(f)
        ran.append(vent_rule.name)

    # Optional SOO conformance: per equipment class, a packaged library sequence or a
    # JSON clause spec is evaluated over each matched equipment's role-frame, and the
    # per-clause Findings merge into the run (so they flow through the report/triage).
    for entry in config.get("soo", []):
        cls = entry["class"]
        if entry.get("library"):
            spec = _SOO_LIBRARY[entry["library"]]()  # type: ignore[operator]  # library builder
        else:
            with open(_path(base_dir, entry["spec"])) as fh:
                spec = spec_from_dicts(json.load(fh))
        if not spec:
            continue
        roles = tuple(set().union(*(c.roles() for c in spec)))
        for ref in refs_by_class.get(cls, []):
            frame = _merge_shared(resolve(ref, mapping, roles, resample=resample), shared)
            if frame is None or frame.empty:
                continue
            findings += soo_findings(frame, spec, ref.equip)
        ran.append(f"soo:{cls}:{entry.get('library') or entry.get('spec')}")

    if config.get("mv"):
        prep.mv_store = _mv_store_readonly(config, base_dir, prep.ctx)
    for entry in config.get("mv", []):
        if entry.get("bills") is not None:  # 0.92 (#64): a billing-data entry (camber.mvbilling)
            from .mvbilling import billing_findings, billing_label

            findings += billing_findings(entry, prep, base_dir=base_dir)
            ran.append(f"mv:bills:{billing_label(entry)}")
            continue
        entry = _mvform_base(entry, base_dir)  # 0.93 (#68): calendar files beside the config
        findings += _mv_findings(entry, refs_by_class.get(entry["class"], []), prep)
        ran.append(f"mv:{entry['class']}")

    # Optional drift comparison: score a current window against the frozen baseline store for
    # each configured family. Read-only toward the store by construction -- freeze_if_missing is
    # False and the store is never saved here (see the module docstring).
    drift = None
    if config.get("drift") is not None:
        drift = run_drift_config(config, base_dir=base_dir, prepared=prep)
        if drift is not None:
            findings += drift.findings
            ran += [
                f"drift:{e['class']}:{e['family']}"
                for e in _drift_families(config["drift"], refs_by_class)
            ]

    faults = None
    if config.get("faults") is not None:
        faults = _fold_faults(config, base_dir, prep, findings)

    report = None
    outputs: dict = {}
    rep = config.get("report")
    if rep is not None:
        report = AuditReport(
            building=site,
            level=rep.get("level", 2),
            climate_zone=rep.get("climate_zone", ""),
            data_sources=list(prep.data_sources),
            title=str(rep.get("title") or ""),  # 0.96 (#78)
        )
        for ecm in rep.get("ecms") or []:  # 0.96 (#78): the Std-211 ECM table, from the config
            report.add_ecm(ECM(**ecm))
        if "benchmark" in rep:
            report.benchmark = _benchmark(rep["benchmark"], prep.units)
            sf = _benchmark_scale_finding(rep["benchmark"])  # 0.92 (#71)
            if sf is not None:
                findings.append(sf)
        report.add_findings(findings, magnitude_key=rep.get("magnitude_key"))
        if rep.get("out_text"):
            with open(_path(base_dir, rep["out_text"]), "w") as fh:
                fh.write(report.to_text())
            outputs[_path(base_dir, rep["out_text"])] = "report"
        if rep.get("out_html"):
            # optional advisory action plan (findings + $ + recommendation) in the HTML report
            price = None
            if rep.get("price"):
                from .fault_economics import EnergyPrice

                price = EnergyPrice.from_dict(rep["price"])  # 0.92 (#69): also per-unit rates
            body = report.to_html(recommend=bool(rep.get("recommend")), price=price)
            if drift is not None:
                # the audit body already carries the data-source block; don't repeat it
                body += "\n" + drift_report_html(drift, standalone=False)
            with open(_path(base_dir, rep["out_html"]), "w") as fh:
                fh.write(html_document(body, title=report.display_title()))
            outputs[_path(base_dir, rep["out_html"])] = "report"
    if outputs:
        _record_outputs(prep.ctx, outputs)

    ctx = prep.ctx
    return RunResult(
        site=site,
        equipment=len(refs),
        findings=findings,
        report=report,
        rules_run=ran,
        drift=drift,
        facility_id=ctx.facility_id if ctx else None,
        workspace=ctx.workspace if ctx else None,
        faults=faults,
        registry=reg,
        frame_for=_frame_resolver(prep),
        refs=list(refs),
        data_sources=list(prep.data_sources),
        config=config,
        base_dir=base_dir,
        topology=topology,
        topology_source=topology_source,
        rules_skipped=skipped,
    )


def _benchmark(b: dict, units) -> Benchmark:
    """The report benchmark. ``unit`` (0.92, #69) states the EUIs' unit (``kBtu/ft2/yr`` by
    default); a config ``units`` block converts both to its EUI unit (kBtu/ft2/yr or kWh/m2/yr)."""
    if b.get("unit") is None and units is None:
        return Benchmark(b["site_eui"], b["peer_median_eui"])
    unit = b.get("unit") or "kBtu/ft2/yr"
    site, peer = b["site_eui"], b["peer_median_eui"]
    from .energy_units import eui_factor

    k = eui_factor(unit, unit if units is None else units.eui)  # validates the stated unit
    if units is not None:
        site, peer, unit = round(site * k, 1), round(peer * k, 1), units.eui
    return Benchmark(site, peer, unit=unit)


def _benchmark_scale_finding(b: dict):
    """0.92 (#71): a ``unit_scale`` Finding when the benchmark's stated ``site_eui`` is implausible
    as given (x1 ruled out: a hard EUI bound no building crosses), else ``None``. The optional
    ``property_type`` names the ENERGY STAR type (camber.energy_factors) for the report's own
    reference; the benchmark itself is never changed."""
    from .unit_scale import check_eui

    try:
        chk = check_eui(
            float(b["site_eui"]),
            b.get("unit") or "kBtu/ft2/yr",
            property_type=b.get("property_type"),
            label="benchmark site EUI",
        )
    except (TypeError, ValueError):
        return None
    return chk.finding("benchmark") if chk.implausible else None


_VENTILATION_KEYS = frozenset(
    {
        "zones",
        "systems",
        "method",
        "ez_cooling",
        "ez_heating",
        "under_tol",
        "over_factor",
        "start_hour",
        "end_hour",
        "occupied_days",
    }
)


def _ventilation_rule(config: dict, base_dir: str):
    """The configured :class:`~camber.rules.ventilation_rule.VentilationSystemVRP`, or None."""
    spec = config.get("ventilation")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ValueError('"ventilation" must be an object, e.g. {"zones": "zones.csv"}')
    unknown = set(spec) - _VENTILATION_KEYS
    if unknown:
        raise ValueError(f"unknown ventilation key(s): {sorted(unknown)}")
    from .rules.ventilation_rule import VentilationSystemVRP
    from .ventilation import load_vent_zones

    kw = {k: v for k, v in spec.items() if k not in ("zones", "systems")}
    if "occupied_days" in kw:
        kw["occupied_days"] = tuple(kw["occupied_days"])
    zones = load_vent_zones(spec["zones"], base_dir=base_dir) if spec.get("zones") else []
    return VentilationSystemVRP(zones=zones, systems=spec.get("systems") or {}, **kw)


def _frame_resolver(prep: _Prepared) -> Callable:
    """A lazy, memoizing ``frame_for(equip, roles=None)`` over a prepared run's equipment.

    ``roles`` defaults to every :class:`~camber.model.roles.Role` (the resolver loads only those the
    equipment actually has). Unknown equipment returns ``None``. Frames are resolved on first use,
    so a run that never renders evidence never pays for the load.
    """
    by_equip = {r.equip: r for r in prep.refs}
    cache: dict = {}

    def frame_for(equip, roles=None):
        ref = by_equip.get(equip)
        if ref is None:
            return None
        key = (equip, None if roles is None else tuple(roles))
        if key not in cache:
            want = tuple(Role) if roles is None else tuple(roles)
            frame = resolve(ref, prep.mapping, want, resample=prep.resample)
            cache[key] = _with_class(_merge_shared(frame, prep.shared), ref)  # 0.98 (#85)
        return cache[key]

    return frame_for


def _fold_faults(config: dict, base_dir: str, prep: _Prepared, findings: list) -> dict:
    """Fold a run's findings into the config's fault lifecycle store and save it."""
    import contextlib

    from .faultlifecycle import FaultLifecycle

    spec = config.get("faults") or {}
    if not isinstance(spec, dict):
        raise ValueError('"faults" must be an object, e.g. {"store": "faults.json"}')
    ctx = prep.ctx or _FacilityCtx(None, None, prep.site)
    path = _path(base_dir, spec["store"]) if spec.get("store") else _state_path(ctx, "faults.json")
    if not path:
        raise ValueError(
            "faults.store is required outside a portfolio workspace (inside one it defaults to "
            "state/<facility_id>/faults.json)"
        )
    run_id = str(spec.get("run_id") or _dt.datetime.now(_dt.timezone.utc).isoformat("seconds"))
    lock: contextlib.AbstractContextManager = contextlib.nullcontext()
    if ctx.workspace:
        from .portfolio._lock import portfolio_lock

        lock = portfolio_lock(ctx.workspace, timeout=30.0)
    with lock:
        lc = FaultLifecycle.load(
            path, facility_id=ctx.bound, legacy_sites=ctx.legacy_sites if ctx.bound else None
        )
        out = lc.update(
            findings,
            run_id=run_id,
            site=prep.site,
            auto_resolve_absent=bool(spec.get("auto_resolve_absent", False)),
        )
        lc.save()
        _record_outputs(ctx, {path: "faults"})
    from ._statefile import save_path

    where = save_path(path, facility_id=ctx.bound)
    return {**out, "store": where, "run_id": run_id, "legacy_adopted": lc.legacy_adopted}


def data_sources(config: dict, *, base_dir: str = ".") -> list:
    """Provenance dicts for a config's data (dataset, licence, citation) -- ``[]`` for folders.

    A store-backed config (``source.kind == "store"``) returns the facility's ingest provenance, the
    same list :func:`run_config` attaches to ``AuditReport.data_sources``; per-point CSV sources
    carry no recorded provenance.
    """
    source = config.get("source") or {}
    if source.get("kind") != "store" or not source.get("store"):
        return []
    from .store import ParquetStore

    fid = source.get("facility_id", "")
    meta = ParquetStore(_path(base_dir, source["store"])).facilities_meta().get(fid, {})
    prov = _provenance(meta, fid)
    return [prov] if prov else []


def drift_store_path(config: dict, *, base_dir: str = ".") -> str:
    """The baseline-store path a config's ``drift`` section names, resolved against ``base_dir``.

    Inside a portfolio workspace an omitted ``drift.store`` defaults to
    ``state/<facility_id>/baselines.json``; outside one it is required (``ValueError``).
    """
    dspec = config.get("drift") or {}
    if dspec.get("store"):
        return _path(base_dir, dspec["store"])
    default = _state_path(_facility_context(config, base_dir), "baselines.json")
    if default:
        return default
    raise ValueError(
        "drift.store is required: a drift comparison needs a frozen baseline store "
        "(create one with `camber drift freeze`; inside a portfolio workspace it defaults to "
        "state/<facility_id>/baselines.json)"
    )


def _baseline_store(config: dict, *, base_dir: str = ".", ctx=None):
    """``(store, path, ctx)``: the config's baseline store, facility-bound in a workspace."""
    ctx = ctx or _facility_context(config, base_dir)
    path = drift_store_path(config, base_dir=base_dir)
    store = BaselineStore.load(
        path, facility_id=ctx.bound, legacy_sites=ctx.legacy_sites if ctx.bound else None
    )
    return store, path, ctx


def drift_refit(config: dict, *, base_dir: str = ".", period=None, run_id: str = "") -> dict:
    """Re-fit every configured drift family's baselines over an acceptance window.

    Returns ``{(equip, kind): fitted model}`` merged across the families, ready for
    :func:`camber.driftrun.accept_new_normal_from_periods`. ``period`` defaults to the config's
    ``drift.current`` window -- accepting a new normal means "what it is doing *now* is the
    reference". Nothing is written: the fits come from a scratch store. Families that declare a
    ``reference`` (0.98) have no frozen baseline to move and are left out.
    """
    dspec = config.get("drift")
    if dspec is None:
        return {}
    prep = _prepare(config, base_dir)
    fams = _drift_families(dspec, prep.refs_by_class)
    fams = [e for e in fams if not e.get("reference")]  # 0.98: a declared reference is never kept
    if not fams:
        return {}
    win = tuple(period) if period else _drift_window(dspec, "current")
    out: dict = {}
    for entry in fams:
        out.update(
            refit_baselines(
                entry["family"],
                prep.refs_by_class.get(entry["class"], []),
                prep.mapping,
                period=win,
                site=prep.site,
                run_id=run_id,
                resample=prep.resample,
                shared=prep.shared,
                min_trust=prep.min_trust,
                coils=tuple(entry.get("coils") or ("cooling",)),
                sustained_alarm=bool(entry.get("sustained_alarm")),
                elevation_ft=site_elevation_ft(config),
                coil_leak=tuple(entry.get("coil_leak") or ()),
                coil_leak_params=entry.get("coil_leak_params"),
            )
        )
    return out


def run_drift_config(
    config: dict,
    *,
    base_dir: str = ".",
    freeze_if_missing: bool = False,
    run_id: str | None = None,
    store=None,
    evidence: bool = False,
    prepared=None,
):
    """Run only a config's ``drift`` section, returning a :class:`camber.driftrun.DriftResult`.

    ``None`` when the config has no ``drift`` section (or it names no families). The baseline store
    is **read** by default: ``freeze_if_missing`` creates a missing reference and is the caller's
    responsibility to save afterwards -- only ``camber drift freeze`` passes ``True``, so an
    ordinary run can never mint the baseline it is scoring against.

    A family that declares a ``reference`` (0.98, #86, S4) needs no store: its baselines are fitted
    on the reference in a scratch store that is never saved, and when every family declares one
    neither a store nor the ``baseline`` / ``current`` windows are required. ``freeze_if_missing``
    with such a family is a ``ValueError`` (``camber drift freeze`` refuses it).

    ``evidence`` additionally builds each rule's pattern-J chart spec (see
    :func:`camber.driftrun.run_drift`). Pass ``store`` to own the
    :class:`~camber.store.modelstore.BaselineStore` yourself -- the freeze path needs the mutated
    object back in order to save it. ``prepared`` is an internal
    optimization (reusing an already-discovered equipment set); callers pass only the config.
    """
    dspec = config.get("drift")
    if dspec is None:
        return None
    # 0.98 (#86, S4): families that all declare a reference read no frozen store at all
    declares_ref = [e for e in dspec.get("families", []) if e.get("reference") is not None]
    if freeze_if_missing and declares_ref:
        raise ValueError(
            "drift freeze refuses families that declare a reference ("
            + ", ".join(f"{e.get('class')}:{e.get('family')}" for e in declares_ref)
            + "): a declared reference is re-read on every run and never frozen"
        )
    only_ref = bool(declares_ref) and len(declares_ref) == len(dspec.get("families", []))
    if not only_ref:
        drift_store_path(config, base_dir=base_dir)  # fail fast when no store can be named
    prep = prepared if prepared is not None else _prepare(config, base_dir)
    fams = _drift_families(dspec, prep.refs_by_class)
    if not fams:
        return None
    if store is None and not _all_reference(fams):
        store, _p, _c = _baseline_store(config, base_dir=base_dir, ctx=prep.ctx)
    return run_drift(
        prep.refs_by_class,
        prep.mapping,
        store=store,
        families=fams,
        baseline=_drift_window(dspec, "baseline", required=not only_ref),
        current=_drift_window(dspec, "current", required=not only_ref),
        site=prep.site,
        run_id=run_id if run_id is not None else dspec.get("run_id", ""),
        resample=prep.resample,
        shared=prep.shared,
        min_trust=prep.min_trust,
        freeze_if_missing=freeze_if_missing,
        evidence=evidence,
        elevation_ft=site_elevation_ft(config),
    )


def run_mv_config(config: dict, *, base_dir: str = ".", prepared=None) -> list:
    """Run only a config's ``mv`` section; returns its Findings (``camber mv run``).

    Read-only toward the M&V baseline store, exactly as :func:`run_config` is: a meter with a
    frozen version is measured against it (``baseline_version`` recorded, triggers as
    ``mv_trigger`` Findings, a partial or declined saving on an unresolved trigger); one without
    is fitted afresh as before.
    """
    prep = prepared if prepared is not None else _prepare(config, base_dir)
    if not config.get("mv"):
        return []
    prep.mv_store = _mv_store_readonly(config, base_dir, prep.ctx)
    out: list = []
    for entry in config.get("mv", []):
        if entry.get("bills") is not None:  # 0.92 (#64): a billing-data entry (camber.mvbilling)
            from .mvbilling import billing_findings

            out += billing_findings(entry, prep, base_dir=base_dir)
            continue
        entry = _mvform_base(entry, base_dir)
        out += _mv_findings(entry, prep.refs_by_class.get(entry["class"], []), prep)
    return out


def _mvform_base(entry: dict, base_dir: str) -> dict:
    from .mandv._mvform import with_base_dir

    return with_base_dir(entry, base_dir)


def load_config(path: str) -> dict:
    """Load a run config file into a dict: JSON, or YAML for a ``.yaml`` / ``.yml`` path.

    0.98 (#90): YAML is an optional, equivalent format (the ``[yaml]`` extra, PyYAML) so that
    calibration notes can live as comments beside the values; a missing PyYAML raises an
    ``ImportError`` that says how to install it. JSON needs nothing.
    """
    from ._yaml import read_config_file

    return read_config_file(path)


def run_config_file(path: str) -> RunResult:
    """Load and run a config file; paths resolve relative to the file's directory."""
    return run_config(load_config(path), base_dir=os.path.dirname(os.path.abspath(path)))


if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m camber.config <config.json|config.yaml>")
    from ._yaml import MissingYamlExtra

    try:
        res = run_config_file(sys.argv[1])
    except MissingYamlExtra as e:  # a .yaml config without the [yaml] extra
        raise SystemExit(f"error: {e}") from None
    print(
        f"{res.site}: {res.equipment} equipment, {len(res.findings)} findings "
        f"from {len(res.rules_run)} rules"
    )
    if res.report is not None:
        print("\n" + res.report.to_text())
