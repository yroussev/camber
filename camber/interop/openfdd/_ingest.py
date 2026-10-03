"""Ingest an open-fdd package (or historian root) into a CAMBER store or portfolio workspace.

One facility per open-fdd building. In a portfolio workspace the facility is registered through
the lifecycle (``provisioning``, or ``active`` with ``activate=True``), the write takes the
workspace lock and one ``interop.openfdd.ingest`` audit record is appended. A facility in any
other state (suspended, offboarding, ...) is refused. Data is written to a staging area and
swapped in, replacing the facility's trends, so re-ingesting a newer export of the same building
is safe; an unchanged package (same files, crosswalk, time zone, units and resample) is skipped.

Provenance lands on the facility's registry entry under the key ``"openfdd"``: source, package
schema version, the sha256 of every file read, the crosswalk version and the docs commit it was
written from, the time zone and unit system given, and the column coverage. The time zone is also
written to the entry's ``"timezone"`` (0.99.1, #96), the standard place for a facility's zone.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field

import pandas as pd

from ... import __version__
from ...model.equipclass import equip_family
from ...model.roles import Role
from ...store import FacilityRegistry, ParquetStore, make_facility_id, require_facility_id
from ._crosswalk import load_crosswalk
from ._historian import is_historian_root, read_historian
from ._reader import WEATHER_EQUIP, OpenFddPackage, read_package

INGEST_VERSION = 1
META_KEY = "openfdd"
SOURCE_PACKAGE = "open-fdd package (openfdd_package_v1)"
SOURCE_HISTORIAN = "open-fdd historian Parquet"
_ACCEPTS_DATA = ("provisioning", "active")


@dataclass
class OpenFddIngestResult:
    """What :func:`ingest_package` did, or why it skipped (provisional, 0.99)."""

    facility_id: str
    building_id: str
    store: str
    workspace: str | None = None
    state: str = ""
    rows: int = 0
    equipment: int = 0
    skipped: bool = False
    content_hash: str = ""
    coverage: dict = field(default_factory=dict)
    unmapped: dict = field(default_factory=dict)
    classes: dict = field(default_factory=dict)
    config: str | None = None
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """A JSON-friendly dict."""
        return asdict(self)


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(pkg: OpenFddPackage, resample: str | None) -> str:
    """A hash of everything that decides the stored data: inputs, crosswalk, clock, units."""
    spec = {
        "files": dict(sorted(pkg.files.items())),
        "building": pkg.building_id,
        "source": pkg.source_kind,
        "crosswalk": pkg.crosswalk_version,
        "timezone": pkg.timezone,
        "unit_system": pkg.unit_system,
        "resample": resample or "native",
        "ingest_version": INGEST_VERSION,
        # the decisions themselves, so a reader or crosswalk fix re-ingests on its own
        "mapping": {
            e.equip: [[c.column, c.camber_role, c.unit_applied, e.equip_class] for c in e.mapped]
            for e in pkg.equipment.values()
        },
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def provenance(pkg: OpenFddPackage, *, chash: str, resample: str | None, rows: int) -> dict:
    """The ``"openfdd"`` registry block recorded on the facility."""
    eq = {
        e.equip: {
            "class": e.equip_class,
            "equipType": e.equip_type,
            "path": e.path,
            **({"parent": e.parent} if e.parent else {}),
            "unmapped": [c.column for c in e.unmapped],
        }
        for e in pkg.equipment.values()
    }
    return {
        "source": SOURCE_PACKAGE if pkg.source_kind == "package" else SOURCE_HISTORIAN,
        "building_id": pkg.building_id,
        "schema_version": pkg.manifest.get("schema_version"),
        "grid_minutes": pkg.manifest.get("grid_minutes"),
        "manifest_timezone": pkg.manifest.get("timezone"),
        "timezone": pkg.timezone,
        "unit_system": pkg.unit_system,
        "resample": resample or "native",
        "crosswalk_version": pkg.crosswalk_version,
        "crosswalk_docs_commit": pkg.crosswalk_docs_commit,
        "package_sha256": pkg.package_sha256,
        "files": dict(sorted(pkg.files.items())),
        "content_hash": chash,
        "coverage": {k: v for k, v in pkg.coverage().items() if k != "by_equipment"},
        "equipment": eq,
        "ignored": list(pkg.ignored),
        "rows": rows,
        "span": _span(pkg),
        "ingested_at": _utc_now(),
        "camber_version": __version__,
        "ingest_version": INGEST_VERSION,
    }


def _span(pkg: OpenFddPackage) -> list | None:
    """``[first, last]`` site wall-clock stamp over every equipment (``None`` when empty)."""
    firsts = [e.frame.index.min() for e in pkg.equipment.values() if not e.frame.empty]
    lasts = [e.frame.index.max() for e in pkg.equipment.values() if not e.frame.empty]
    if not firsts:
        return None
    return [pd.Timestamp(min(firsts)).isoformat(), pd.Timestamp(max(lasts)).isoformat()]


def openfdd_meta(meta: dict) -> dict:
    """The ``"openfdd"`` block of a facility's registry entry (``{}`` if absent)."""
    block = (meta or {}).get(META_KEY)
    return block if isinstance(block, dict) else {}


def _write_staged(pkg: OpenFddPackage, staging: str, fid: str) -> int:
    st = ParquetStore(staging)
    rows = 0
    for e in pkg.equipment.values():
        if e.frame is not None and not e.frame.empty:
            rows += st.write_role_frame(
                e.frame, facility_id=fid, equip=e.equip, equip_class=e.equip_class
            )
    return rows


def _read(path, *, timezone, unit_system, building, resample, equip_types) -> OpenFddPackage:
    if os.path.isdir(os.fspath(path)) and is_historian_root(path):
        if not building:
            raise ValueError("a historian root needs building= / --building")
        return read_historian(
            path,
            building=building,
            timezone=timezone,
            unit_system=unit_system,
            equip_types=equip_types,
            resample=resample,
        )
    return read_package(
        path, timezone=timezone, unit_system=unit_system, building=building, resample=resample
    )


def ingest_package(
    path,
    *,
    timezone: str,
    unit_system: str,
    store=None,
    workspace=None,
    facility_id: str | None = None,
    name: str | None = None,
    building: str | None = None,
    resample: str | None = "15min",
    activate: bool = False,
    force: bool = False,
    reason: str | None = None,
    equip_types: dict | None = None,
    config_out: str | None = None,
) -> OpenFddIngestResult:
    """Read an open-fdd package or historian root and write it into CAMBER (provisional, 0.99).

    Exactly one of ``store`` (a ParquetStore directory) or ``workspace`` (a portfolio workspace
    root) is required, and so are ``timezone`` and ``unit_system`` (see :func:`read_package`).
    ``facility_id`` defaults to a deterministic id derived from the building id; ``name`` to
    ``"open-fdd <building>"``. ``resample`` (default ``"15min"``) averages onto a grid; ``None``
    keeps the package's own. ``activate`` registers a new workspace facility ``active`` (or
    activates a ``provisioning`` one); otherwise it starts ``provisioning`` and ``camber run``
    skips it until activated. ``config_out`` also writes a starting run config
    (:func:`package_config`). ``equip_types`` classifies historian equipment.
    """
    from ...datasets._ingest import _staging_root, _swap_in
    from ...portfolio import Portfolio, is_workspace

    if (store is None) == (workspace is None):
        raise ValueError("give exactly one of store= (a store directory) or workspace=")
    pkg = _read(
        path,
        timezone=timezone,
        unit_system=unit_system,
        building=building,
        resample=resample,
        equip_types=equip_types,
    )
    fid = require_facility_id(facility_id or make_facility_id(f"openfdd {pkg.building_id}"))
    disp = name or f"open-fdd {pkg.building_id}"
    chash = content_hash(pkg, resample)
    port = None
    if workspace is not None:
        if not is_workspace(workspace):
            raise FileNotFoundError(
                f"{workspace} is not a portfolio workspace "
                "(create one with `camber portfolio init`)"
            )
        port = Portfolio(workspace)
        st = port.store
    else:
        st = ParquetStore(os.path.abspath(os.fspath(store)))
    res = OpenFddIngestResult(
        facility_id=fid,
        building_id=pkg.building_id,
        store=os.path.abspath(st.root),
        workspace=port.root if port else None,
        content_hash=chash,
        coverage=pkg.coverage(),
        unmapped=pkg.unmapped_names(),
        classes={e.equip: e.equip_class for e in pkg.equipment.values()},
        notes=list(pkg.notes) + [f"ignored {i}" for i in pkg.ignored],
        warnings=list(pkg.warnings),
    )
    res.equipment = len(pkg.equipment)
    why = reason or f"open-fdd ingest of building {pkg.building_id}"
    lock = port.lock() if port else contextlib.nullcontext()
    with lock:
        reg = port.registry if port else FacilityRegistry(st.root)
        entry = reg.get(fid)
        if entry:
            state = reg.state(fid)
            if state not in _ACCEPTS_DATA:
                raise ValueError(
                    f"facility {fid} is {state}: it accepts no new data "
                    "(resume or restore it first)"
                )
            prev = openfdd_meta(entry)
            if not force and prev.get("content_hash") == chash and fid in st.facilities():
                res.skipped = True
                if not entry.get("timezone"):  # 0.99.1 (#96): backfill a 0.99.0 ingest
                    reg.register(fid, timezone=pkg.timezone)
                if port is not None and activate and state == "provisioning":
                    port.transition(fid, "activate", reason=why)
                res.state = reg.state(fid)
                res.rows = int(prev.get("rows") or 0)
                res.notes.append("already ingested with identical inputs (force to re-ingest)")
                if config_out:
                    package_config(pkg, store=st.root, facility_id=fid, out=config_out)
                    res.config = os.path.abspath(config_out)
                return res
        elif port is not None:
            port.add_facility(disp, facility_id=fid, reason=why, activate=activate)
        else:
            reg._guard_write(fid)  # a tombstoned id or a case-variant: refused before any write
        staging = _staging_root(st)
        try:
            rows = _write_staged(pkg, staging, fid)
            _swap_in(st, staging, fid)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        meta = provenance(pkg, chash=chash, resample=resample, rows=rows)
        has_name = bool(reg.get(fid).get("name"))
        # 0.99.1 (#96): the site zone also goes to meta["timezone"], where the read API and the
        # trend viewer look; the store holds this zone's wall clock
        reg.register(
            fid, name=None if has_name else disp, timezone=pkg.timezone, **{META_KEY: meta}
        )
        if port is not None:
            if activate and reg.state(fid) == "provisioning":
                port.transition(fid, "activate", reason=why)
            port.audit(
                "interop.openfdd.ingest",
                reason=why,
                facility_id=fid,
                details={
                    "source": meta["source"],
                    "building_id": pkg.building_id,
                    "content_hash": chash,
                    "rows": rows,
                    "equipment": len(pkg.equipment),
                    "files": len(pkg.files),
                    "mapped": res.coverage["mapped"],
                    "unmapped": res.coverage["unmapped"],
                },
            )
        res.rows = rows
        res.state = reg.state(fid)
    if config_out:
        package_config(pkg, store=st.root, facility_id=fid, out=config_out)
        res.config = os.path.abspath(config_out)
    return res


# --------------------------------------------------------------------------- config


def _class_roles(pkg: OpenFddPackage) -> dict:
    out: dict = {}
    for e in pkg.equipment.values():
        if e.equip == WEATHER_EQUIP or e.frame is None or e.frame.empty:
            continue
        out.setdefault(e.equip_class, set()).update(
            r for r in e.frame.columns if isinstance(r, Role)
        )
    return out


def suggested_rules(class_roles: dict) -> list:
    """Built-in single-equipment and fleet rules that can run on at least one class.

    A rule qualifies for a class when the class's roles meet its required inputs and, for a rule
    written for particular equipment families, the class belongs to one. Drift, M&V and the 62.1
    ventilation rules need their own config sections and are left out.
    """
    from ...model.entities import runnable_rules
    from ...rules.applicability import rule_equip_classes
    from ...rules.builtin import builtin_registry

    reg = builtin_registry()
    names = []
    for rname in reg.names():
        rule = reg.get(rname)
        if not hasattr(rule, "analyze") and not hasattr(rule, "analyze_fleet"):
            continue
        if "ventilation" in rname and "62" in rname:
            continue
        fams = rule_equip_classes(rule)
        for cls, roles in class_roles.items():
            fam = equip_family(cls)
            if fams and fam is not None and fam not in fams:
                continue
            if not rule.roles_required and not getattr(rule, "roles_any_of", None):
                # a rule that declares no required input decides internally: suggest it only
                # when the class carries at least one of the inputs it reads
                if not set(getattr(rule, "roles_optional", ())) & set(roles):
                    continue
            if runnable_rules(roles, [rule])[0].can_run:
                names.append(rname)
                break
    return names


def package_config(
    pkg: OpenFddPackage,
    *,
    store,
    facility_id: str,
    out: str | None = None,
    rules: list | None = None,
) -> dict:
    """A starting ``camber run`` config for an ingested package (and written to ``out``).

    It points at the store facility, names the site time zone, runs every class present at an
    hourly resample, reads the package weather as the shared outdoor temperature, lists the
    built-in rules the mapped roles can run (:func:`suggested_rules`), and adds a daily M&V
    entry for each electricity meter with power when an outdoor temperature exists. A starting
    point to review, not a tuned analysis.
    """
    class_roles = _class_roles(pkg)
    cfg: dict = {
        "_comment": (
            f"Starting config for open-fdd building {pkg.building_id} (written by camber interop "
            "openfdd ingest). Review the rules and the M&V period before relying on the results."
        ),
        "source": {
            "kind": "store",
            "store": os.path.abspath(os.fspath(store)),
            "facility_id": facility_id,
            "timezone": pkg.timezone,
        },
        "resample": "1h",
        "equipment": [{"class": c} for c in sorted(class_roles)],
        "rules": rules if rules is not None else suggested_rules(class_roles),
        "report": {"level": 2},
    }
    weather = pkg.equipment.get(WEATHER_EQUIP)
    has_oat = weather is not None and Role.OAT in getattr(weather.frame, "columns", [])
    if has_oat:
        cfg["shared_oat"] = {"equip": WEATHER_EQUIP, "role": "oat"}
    spans: dict = {}  # meter class -> (first, last) power stamp over its meters
    if has_oat:
        for e in pkg.equipment.values():
            if equip_family(e.equip_class) != "meter" or Role.POWER not in e.frame.columns:
                continue
            idx = e.frame[Role.POWER].dropna().index
            if idx.empty:
                continue
            lo, hi = spans.get(e.equip_class, (idx.min(), idx.max()))
            spans[e.equip_class] = (min(lo, idx.min()), max(hi, idx.max()))
    mv = []
    for cls, (lo, hi) in sorted(spans.items()):
        start = lo.normalize()
        end = min(hi, start + _dt.timedelta(days=364))
        mv.append(
            {
                "class": cls,
                "role": "power",
                "period": [str(start.date()), str(end.date())],
                "interval": "daily",
            }
        )
    if mv:
        cfg["mv"] = mv
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
    return cfg


def crosswalk_table() -> list:
    """The shipped crosswalk as rows (``haystack``, ``sql_role``, ``camber_role``, ...)."""
    return load_crosswalk().table()
