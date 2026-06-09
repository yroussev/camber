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
      "rules": ["simultaneous_heat_cool", "outdoor_air_fraction", "reheat_penalty"],
      "soo": [{"class": "AHU", "library": "g36_ahu"},
              {"class": "AHU", "spec": "ahu_sequence.json"}],
      "report": {"level": 2, "climate_zone": "CA CZ15", "out_text": "audit.txt"}
    }

The optional ``soo`` section evaluates Sequence-of-Operations conformance per
equipment class -- either a packaged library sequence (``library``: ``g36_ahu`` /
``g36_plant``) or a JSON clause spec (``spec``) -- merging the per-clause Findings into
the run.

Run it: ``python -m camber.config config.json``. JSON is used (not YAML/TOML) to
stay dependency-free and consistent with the mapping files. Paths are resolved
relative to the config file's directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from glob import glob

from .model.mapping import MappingProvider
from .model.roles import Role
from .realio import load_point
from .report.audit import AuditReport, Benchmark
from .resolve import discover, discover_terminals, resolve
from .rules.base import _merge_shared
from .rules.builtin import builtin_registry, is_fleet
from .soo import soo_findings, spec_from_dicts
from .soo_library import g36_ahu_sequence, g36_plant_sequence

# Named built-in SOO sequences referenceable from a config's "soo" section.
_SOO_LIBRARY = {"g36_ahu": g36_ahu_sequence, "g36_plant": g36_plant_sequence}


@dataclass
class RunResult:
    """Outcome of a config-driven run."""

    site: str
    equipment: int               # number of equipment discovered
    findings: list               # all Findings produced
    report: AuditReport | None = None
    rules_run: list = field(default_factory=list)


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


def run_config(config: dict, *, base_dir: str = ".") -> RunResult:
    """Execute a config dict: discover equipment, run the named rules, build a report.

    Paths in the config resolve against ``base_dir``. Unknown rule names raise
    ``KeyError`` (fail fast on a typo).
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

    refs = []
    refs_by_class: dict = {}          # class -> [EquipRef], for class-targeted SOO specs
    for eq in config.get("equipment", []):
        marker = eq.get("marker", "SpaceTemp")
        if eq["class"] == "TERMINAL":   # union of all terminal-unit types
            found = discover_terminals(folders, marker_measure=marker)
        else:
            found = discover(folders, eq["class"], marker_measure=marker)
        refs += found
        refs_by_class.setdefault(eq["class"], []).extend(found)

    # Optional sensor-health gate: a rule whose required inputs aren't trusted declines
    # to fire (see camber.sensorhealth). Off unless the config sets trust_gate.min_trust.
    min_trust = (config.get("trust_gate") or {}).get("min_trust")

    reg = builtin_registry()
    findings, ran = [], []
    for name in config.get("rules", []):
        rule = reg.get(name)                 # KeyError on unknown name
        if is_fleet(rule):
            f = reg.run_fleet(name, refs, mapping, resample=resample, shared=shared,
                              min_trust=min_trust)
            if f is not None:
                findings.append(f)
        else:
            findings += reg.run(name, refs, mapping, resample=resample, shared=shared,
                                min_trust=min_trust)
        ran.append(name)

    # Optional SOO conformance: per equipment class, a packaged library sequence or a
    # JSON clause spec is evaluated over each matched equipment's role-frame, and the
    # per-clause Findings merge into the run (so they flow through the report/triage).
    for entry in config.get("soo", []):
        cls = entry["class"]
        if entry.get("library"):
            spec = _SOO_LIBRARY[entry["library"]]()
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

    report = None
    rep = config.get("report")
    if rep is not None:
        report = AuditReport(building=site, level=rep.get("level", 2),
                             climate_zone=rep.get("climate_zone", ""))
        if "benchmark" in rep:
            b = rep["benchmark"]
            report.benchmark = Benchmark(b["site_eui"], b["peer_median_eui"])
        report.add_findings(findings, magnitude_key=rep.get("magnitude_key"))
        if rep.get("out_text"):
            with open(_path(base_dir, rep["out_text"]), "w") as fh:
                fh.write(report.to_text())
        if rep.get("out_html"):
            with open(_path(base_dir, rep["out_html"]), "w") as fh:
                fh.write("<html><body>\n" + report.to_html() + "\n</body></html>\n")

    return RunResult(site=site, equipment=len(refs), findings=findings,
                     report=report, rules_run=ran)


def load_config(path: str) -> dict:
    """Load a JSON config file into a dict."""
    with open(path) as fh:
        return json.load(fh)


def run_config_file(path: str) -> RunResult:
    """Load and run a config file; paths resolve relative to the file's directory."""
    return run_config(load_config(path),
                      base_dir=os.path.dirname(os.path.abspath(path)))


if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m camber.config <config.json>")
    res = run_config_file(sys.argv[1])
    print(f"{res.site}: {res.equipment} equipment, {len(res.findings)} findings "
          f"from {len(res.rules_run)} rules")
    if res.report is not None:
        print("\n" + res.report.to_text())
