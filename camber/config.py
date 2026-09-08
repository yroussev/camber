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

The optional ``soo`` section evaluates Sequence-of-Operations conformance per
equipment class -- either a packaged library sequence (``library``: ``g36_ahu`` /
``g36_plant``) or a JSON clause spec (``spec``) -- merging the per-clause Findings into
the run.

The optional ``drift`` section scores a **current** window against a frozen **baseline** one for
each configured detector family (:mod:`camber.driftrun`), merging its Findings into the run. It is
strictly read-only toward the baseline store: a config-driven run never creates or moves a frozen
reference, because a run that mints the baseline it scores against is circular. Creating one is
``camber drift freeze``; moving one is ``camber drift accept`` (see :mod:`camber.cli`).

Run it: ``python -m camber.config config.json``. JSON is used (not YAML/TOML) to
stay dependency-free and consistent with the mapping files. Paths are resolved
relative to the config file's directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from glob import glob

from .driftrun import DRIFT_FAMILIES, family_names, refit_baselines, run_drift
from .model.mapping import MappingProvider
from .model.roles import Role
from .realio import load_point
from .report.audit import AuditReport, Benchmark
from .report.drift import drift_report_html
from .resolve import discover, discover_terminals, resolve
from .rules.base import _merge_shared
from .rules.builtin import builtin_registry, is_fleet, make_rule
from .soo import soo_findings, spec_from_dicts
from .soo_library import g36_ahu_sequence, g36_plant_sequence
from .store.modelstore import BaselineStore

__all__ = [
    "RunResult",
    "run_config",
    "run_drift_config",
    "drift_store_path",
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
class _Prepared:
    """The source/mapping/equipment context every config-driven entry point needs."""

    site: str
    resample: str
    mapping: MappingProvider
    shared: dict | None
    refs: list
    refs_by_class: dict
    min_trust: float | None


def _prepare(config: dict, base_dir: str) -> _Prepared:
    """Resolve a config's source, mapping, shared points and discovered equipment.

    Shared by :func:`run_config` and :func:`run_drift_config` so the two entry points discover
    equipment identically -- a drift run must see exactly the equipment the ordinary run does.
    """
    site = config.get("site", "")
    resample = config.get("resample", "1h")
    folders = _source_folders(config["source"], base_dir)

    mp_spec = config["mapping"]
    if "path" in mp_spec:
        with open(_path(base_dir, mp_spec["path"])) as fh:
            mp_spec = json.load(fh)
    mapping = MappingProvider.from_dict(mp_spec)

    shared = None
    so = config.get("shared_oat")
    if so and so.get("file"):
        oat = load_point(_path(base_dir, so["file"]), "oat").resample(resample).mean()
        shared = {Role.OAT: oat}

    refs: list = []
    refs_by_class: dict = {}  # class -> [EquipRef], for class-targeted SOO / drift specs
    for eq in config.get("equipment", []):
        marker = eq.get("marker", "SpaceTemp")
        if eq["class"] == "TERMINAL":  # union of all terminal-unit types
            found = discover_terminals(folders, marker_measure=marker)
        else:
            found = discover(folders, eq["class"], marker_measure=marker)
        refs += found
        refs_by_class.setdefault(eq["class"], []).extend(found)

    # Optional sensor-health gate: a rule whose required inputs aren't trusted declines
    # to fire (see camber.sensorhealth). Off unless the config sets trust_gate.min_trust.
    min_trust = (config.get("trust_gate") or {}).get("min_trust")
    return _Prepared(site, resample, mapping, shared, refs, refs_by_class, min_trust)


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
        out.append(e)
    return out


def run_config(config: dict, *, base_dir: str = ".") -> RunResult:
    """Execute a config dict: discover equipment, run the named rules, build a report.

    Paths in the config resolve against ``base_dir``. Unknown rule names raise
    ``KeyError`` (fail fast on a typo).
    """
    prep = _prepare(config, base_dir)
    site, resample, mapping, shared = prep.site, prep.resample, prep.mapping, prep.shared
    refs, refs_by_class, min_trust = prep.refs, prep.refs_by_class, prep.min_trust

    reg = builtin_registry()
    findings, ran = [], []
    for entry in config.get("rules", []):
        # A rule entry is either a bare name "economizer_high_limit" (defaults) or a dict
        # {"name": ..., "params": {...}} that overrides the rule's constructor for this run.
        if isinstance(entry, dict):
            name = entry["name"]
            params = entry.get("params") or {}
            if params:
                reg.register(make_rule(name, **params))  # override the default instance
        else:
            name = entry
        rule = reg.get(name)  # KeyError on unknown name
        if is_fleet(rule):
            f = reg.run_fleet(
                name, refs, mapping, resample=resample, shared=shared, min_trust=min_trust
            )
            if f is not None:
                findings.append(f)
        else:
            findings += reg.run(
                name, refs, mapping, resample=resample, shared=shared, min_trust=min_trust
            )
        ran.append(name)

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

    report = None
    rep = config.get("report")
    if rep is not None:
        report = AuditReport(
            building=site, level=rep.get("level", 2), climate_zone=rep.get("climate_zone", "")
        )
        if "benchmark" in rep:
            b = rep["benchmark"]
            report.benchmark = Benchmark(b["site_eui"], b["peer_median_eui"])
        report.add_findings(findings, magnitude_key=rep.get("magnitude_key"))
        if rep.get("out_text"):
            with open(_path(base_dir, rep["out_text"]), "w") as fh:
                fh.write(report.to_text())
        if rep.get("out_html"):
            # optional advisory action plan (findings + $ + recommendation) in the HTML report
            price = None
            if rep.get("price"):
                from .fault_economics import EnergyPrice

                known = {"electricity_per_kwh", "gas_per_therm"}
                price = EnergyPrice(**{k: v for k, v in rep["price"].items() if k in known})
            body = report.to_html(recommend=bool(rep.get("recommend")), price=price)
            if drift is not None:
                body += "\n" + drift_report_html(drift, standalone=False)
            with open(_path(base_dir, rep["out_html"]), "w") as fh:
                fh.write("<html><body>\n" + body + "\n</body></html>\n")

    return RunResult(
        site=site,
        equipment=len(refs),
        findings=findings,
        report=report,
        rules_run=ran,
        drift=drift,
    )


def drift_store_path(config: dict, *, base_dir: str = ".") -> str:
    """The baseline-store path a config's ``drift`` section names, resolved against ``base_dir``.

    Raises ``ValueError`` when the config has no ``drift.store`` -- every drift command needs one.
    """
    dspec = config.get("drift") or {}
    if not dspec.get("store"):
        raise ValueError(
            "drift.store is required: a drift comparison needs a frozen baseline store "
            "(create one with `camber drift freeze`)"
        )
    return _path(base_dir, dspec["store"])


def drift_refit(config: dict, *, base_dir: str = ".", period=None, run_id: str = "") -> dict:
    """Re-fit every configured drift family's baselines over an acceptance window.

    Returns ``{(equip, kind): fitted model}`` merged across the families, ready for
    :func:`camber.driftrun.accept_new_normal_from_periods`. ``period`` defaults to the config's
    ``drift.current`` window -- accepting a new normal means "what it is doing *now* is the
    reference". Nothing is written: the fits come from a scratch store.
    """
    dspec = config.get("drift")
    if dspec is None:
        return {}
    prep = _prepare(config, base_dir)
    fams = _drift_families(dspec, prep.refs_by_class)
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
    prepared=None,
):
    """Run only a config's ``drift`` section, returning a :class:`camber.driftrun.DriftResult`.

    ``None`` when the config has no ``drift`` section (or it names no families). The baseline store
    is **read** by default: ``freeze_if_missing`` creates a missing reference and is the caller's
    responsibility to save afterwards -- only ``camber drift freeze`` passes ``True``, so an
    ordinary run can never mint the baseline it is scoring against.

    Pass ``store`` to own the :class:`~camber.store.modelstore.BaselineStore` yourself -- the
    freeze path needs the mutated object back in order to save it. ``prepared`` is an internal
    optimization (reusing an already-discovered equipment set); callers pass only the config.
    """
    dspec = config.get("drift")
    if dspec is None:
        return None
    if not dspec.get("store"):
        raise ValueError(
            "drift.store is required: a drift comparison needs a frozen baseline store "
            "(create one with `camber drift freeze`)"
        )
    prep = prepared if prepared is not None else _prepare(config, base_dir)
    fams = _drift_families(dspec, prep.refs_by_class)
    if not fams:
        return None
    if store is None:
        store = BaselineStore.load(drift_store_path(config, base_dir=base_dir))
    return run_drift(
        prep.refs_by_class,
        prep.mapping,
        store=store,
        families=fams,
        baseline=_drift_window(dspec, "baseline"),
        current=_drift_window(dspec, "current"),
        site=prep.site,
        run_id=run_id if run_id is not None else dspec.get("run_id", ""),
        resample=prep.resample,
        shared=prep.shared,
        min_trust=prep.min_trust,
        freeze_if_missing=freeze_if_missing,
    )


def load_config(path: str) -> dict:
    """Load a JSON config file into a dict."""
    with open(path) as fh:
        return json.load(fh)


def run_config_file(path: str) -> RunResult:
    """Load and run a config file; paths resolve relative to the file's directory."""
    return run_config(load_config(path), base_dir=os.path.dirname(os.path.abspath(path)))


if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m camber.config <config.json>")
    res = run_config_file(sys.argv[1])
    print(
        f"{res.site}: {res.equipment} equipment, {len(res.findings)} findings "
        f"from {len(res.rules_run)} rules"
    )
    if res.report is not None:
        print("\n" + res.report.to_text())
