"""Read an open-fdd ``openfdd_package_v1`` building package (or historian Parquet) into frames.

The reader follows open-fdd's documented ingest contract (pinned in ``crosswalk.json``)::

    <building>/
      manifest.json                 {"grid_minutes": 5, ...}
      column_map.json               optional: {"equipment": {<equip>: {equipType, points, ...}}}
      equipment_inventory.json      optional: [{"equip_id", "type", ...}]
      <equip>/                      (may be nested, e.g. VAV/VAV_1)
        history_wide.csv            timestamp_utc + one column per point
        columns.csv                 col|column, point_role[, unit|units]
        column_map.json | history_wide.json   sibling map: equipType + points {haystack: column}
      weather/history_wide.csv      web-outside-air-temp, ...

It reads files only and never contacts open-fdd. Every column of every ``history_wide.csv`` is
accounted for: mapped to a CAMBER role, or reported with the reason it was not.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field

import pandas as pd

from ...model.roles import Role
from ...tsparse import check_timezone
from ...units import PERCENT_ROLES, normalize_percent
from ._crosswalk import Crosswalk, convert, load_crosswalk, resolve_unit

UNIT_SYSTEMS = ("ip", "si")
WEATHER_EQUIP = "weather"
SCHEMA_VERSION = "openfdd_package_v1"
UNCLASSIFIED = "UNCLASSIFIED"

# columns that identify a row, not a point
_METADATA_COLUMNS = frozenset({"equipment_id", "site_id", "building_id", "equip", "site"})
# package members the reader knows and deliberately does not ingest (open-fdd's own outputs and UI
# state); listed in the result so nothing is skipped silently
_KNOWN_OTHER = frozenset(
    {
        "fdd_events.csv",
        "fdd_faults.csv",
        "quality.json",
        "session_config.json",
        "sensor_catalog.csv",
        "fdd_device_lookup.csv",
        "thermal_zone_model.json",
    }
)
_OFFSET = re.compile(r"(?:Z|z|[+-]\d\d:?\d\d)\s*$")

# ColumnMapping.status values
MAPPED = "mapped"
STATUSES = (
    MAPPED,
    "no_role",  # the package gives the column no point name, and its header is not one either
    "unknown_name",  # the package names it, but the name is not in the crosswalk
    "no_camber_role",  # in the crosswalk, deliberately not mapped (the row's reason says why)
    "not_applicable",  # the crosswalk row is limited to other equipment types
    "unit_unsupported",  # a declared unit CAMBER cannot convert, or that contradicts the point
    "duplicate_role",  # a second column for a CAMBER role this equipment already has
    "metadata",  # a row identifier (equipment_id, site_id, ...)
)


@dataclass
class ColumnMapping:
    """How one ``history_wide.csv`` column was (or was not) mapped."""

    column: str
    name: str | None = None  # the open-fdd point name the package gave it
    source: str = ""  # "map" (sidecar / root map), "columns.csv", "identity", "historian"
    camber_role: str | None = None
    unit: str = ""  # the declared unit, verbatim
    unit_applied: str = ""  # the unit the values were converted from
    status: str = "no_role"
    reason: str = ""

    def as_dict(self) -> dict:
        """A JSON-friendly dict."""
        return dict(self.__dict__)


@dataclass
class OpenFddEquipment:
    """One equipment of a package: its class, column mappings and CAMBER role frame."""

    equip: str
    equip_class: str
    equip_type: str | None  # the open-fdd stamp as written (None when absent)
    path: str  # relative path of its folder inside the building
    columns: list = field(default_factory=list)
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    parent: str | None = None
    rows_read: int = 0
    rows_bad_timestamp: int = 0

    @property
    def mapped(self) -> list:
        """The :class:`ColumnMapping` records that became CAMBER roles."""
        return [c for c in self.columns if c.status == MAPPED]

    @property
    def unmapped(self) -> list:
        """Every column that did not become a CAMBER role (metadata columns excluded)."""
        return [c for c in self.columns if c.status not in (MAPPED, "metadata")]


@dataclass
class OpenFddPackage:
    """What :func:`read_package` / :func:`read_historian` read (provisional, 0.99)."""

    building_id: str
    source_kind: str  # "package" or "historian"
    timezone: str
    unit_system: str
    crosswalk_version: int
    crosswalk_docs_commit: str
    manifest: dict = field(default_factory=dict)
    equipment: dict = field(default_factory=dict)  # equip -> OpenFddEquipment
    files: dict = field(default_factory=dict)  # relative path -> sha256
    package_sha256: str | None = None  # the zip archive's sha256, when read from one
    ignored: list = field(default_factory=list)  # members read past on purpose
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def coverage(self) -> dict:
        """Column counts by status, overall and per equipment, plus the totals."""
        total: dict = {s: 0 for s in STATUSES}
        per: dict = {}
        for eq, e in self.equipment.items():
            counts: dict = {}
            for c in e.columns:
                counts[c.status] = counts.get(c.status, 0) + 1
                total[c.status] += 1
            per[eq] = counts
        points = sum(v for k, v in total.items() if k != "metadata")
        return {
            "columns": points,
            "mapped": total[MAPPED],
            "unmapped": points - total[MAPPED],
            "by_status": {k: v for k, v in total.items() if v},
            "by_equipment": per,
        }

    def unmapped_names(self) -> dict:
        """``{open-fdd name or column: count}`` over every unmapped column, most common first."""
        out: dict = {}
        for e in self.equipment.values():
            for c in e.unmapped:
                k = c.name or c.column
                out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- helpers


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: str):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def check_args(timezone, unit_system) -> tuple:
    """Validate the two required, explicit inputs; ``ValueError`` with the reason otherwise."""
    if not timezone:
        raise ValueError(
            "timezone is required: an open-fdd package stores UTC stamps and carries no site time "
            "zone, so CAMBER will not guess one (pass the site's IANA zone, e.g. America/Chicago)"
        )
    tz = check_timezone(timezone)
    us = str(unit_system or "").strip().lower()
    if us not in UNIT_SYSTEMS:
        raise ValueError(
            "unit_system is required: 'ip' (degF, cfm, inH2O, psi, gpm) or 'si' (degC, L/s, Pa, "
            f"kPa, L/s); got {unit_system!r}. It applies to every column whose unit the package "
            "does not declare"
        )
    return tz, us


def _equip_blocks(root_map) -> dict:
    """The equipment blocks of a building-level ``column_map.json`` (``{}`` if none)."""
    if not isinstance(root_map, dict):
        return {}
    for key in ("equipment", "equip", "devices"):
        blk = root_map.get(key)
        if isinstance(blk, dict):
            return {k: v for k, v in blk.items() if isinstance(v, dict)}
    return {}


def _points(block) -> dict:
    """``{open-fdd name: column}`` of a map block (``points`` wins over ``column_roles``)."""
    if not isinstance(block, dict):
        return {}
    out: dict = {}
    for key in ("column_roles", "points"):  # later wins: points is the documented key
        pts = block.get(key)
        if isinstance(pts, dict):
            out.update({str(k): str(v) for k, v in pts.items() if isinstance(v, str)})
    return out


def _stamp(*blocks) -> str | None:
    for b in blocks:
        if isinstance(b, dict):
            for key in ("equipType", "equipment_type"):
                v = b.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _parent(*blocks) -> str | None:
    for b in blocks:
        if isinstance(b, dict):
            for key in ("parentAhu", "parent_ahu"):
                v = b.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _columns_csv(path: str) -> dict:
    """``{column: (point_role, unit)}`` from an equipment's ``columns.csv`` (``{}`` if absent)."""
    if not os.path.isfile(path):
        return {}
    out: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            col = row.get("col") or row.get("column")
            if not col:
                continue
            unit = row.get("unit") if "unit" in row else row.get("units")
            out[col] = ((row.get("point_role") or "").strip(), (unit or "").strip())
    return out


def _inventory(path: str) -> dict:
    """``{equip_id: type}`` from ``equipment_inventory.json`` (``{}`` if absent)."""
    data = _read_json(path)
    rows = data.get("equipment") if isinstance(data, dict) else data
    out: dict = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("equip_id"):
            out[str(r["equip_id"])] = r.get("equipType") or r.get("type")
    return out


def to_site_clock(stamps: pd.Series, timezone: str, *, utc_column: bool) -> pd.Series:
    """Parse open-fdd stamps and re-express them as naive site wall-clock time.

    ``timestamp_utc`` stamps are instants (``Z`` / ``+00:00``; a stamp without an offset is read
    as UTC, as the column name says). A ``timestamp`` column without offsets is local wall-clock
    time in ``timezone`` (the historian wide-CSV profile); the ambiguous fall-back hour and the
    skipped spring-forward hour become ``NaT``. Unparseable stamps become ``NaT`` (dropped by the
    caller and counted).
    """
    s = stamps.astype("string").str.strip()
    has_offset = s.str.contains(_OFFSET, na=False)
    if utc_column or has_offset.all():
        ts = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    else:
        naive = pd.to_datetime(
            s.where(~has_offset), errors="coerce", format="ISO8601"
        ).dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        ts = naive.dt.tz_convert("UTC")
        if has_offset.any():
            aware = pd.to_datetime(s.where(has_offset), utc=True, errors="coerce", format="ISO8601")
            ts = ts.where(~has_offset, aware)
    return ts.dt.tz_convert(timezone).dt.tz_localize(None)


def _resample(frame: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    if not rule or frame.empty:
        return frame
    return frame.resample(rule).mean().dropna(how="all")


def _role_frame(
    raw: pd.DataFrame, cols: list, quantities: dict, equip: str, warnings: list
) -> pd.DataFrame:
    """Mapped raw columns -> an IP, percent-normalised CAMBER role frame."""
    data: dict = {}
    for c in cols:
        role = Role(c.camber_role)
        s = pd.to_numeric(raw[c.column], errors="coerce")
        q = quantities[c.column]
        s = convert(s, q, c.unit_applied)
        if role in PERCENT_ROLES:
            if c.unit_applied == "fraction":
                s = s * 100.0
            elif c.unit_applied != "%":  # undeclared: CAMBER's 0-1 vs 0-100 test
                vals = set(pd.unique(s.dropna()))
                binary = 1.0 in vals and vals <= {0.0, 1.0}
                before = s
                s = normalize_percent(s)
                if binary and c.name and c.name.endswith("-cmd"):
                    warnings.append(
                        f"{equip}: {c.column} ({c.name}) is a 0/1 command, stored as 0/100 % on "
                        f"{role.value}"
                    )
                elif not s.equals(before):
                    warnings.append(
                        f"{equip}: {c.column} ({role.value}) read as a 0-1 fraction, scaled to %"
                    )
        data[role] = s
    return pd.DataFrame(data, index=raw.index)


# --------------------------------------------------------------------------- mapping


def map_columns(
    header: list,
    *,
    names: dict,
    csv_meta: dict,
    equip_type: str | None,
    unit_system: str,
    crosswalk: Crosswalk,
    source: str = "map",
) -> tuple:
    """Decide every column's fate. Returns ``(mappings, {column: quantity})`` in header order.

    ``names`` is ``{column: open-fdd name}`` from the package maps. When an equipment has a map it
    is authoritative: its columns claim their roles first, and a column it does not name can
    only take a role still unclaimed, and only when its header *is* an exact open-fdd name
    (open-fdd ingests such a header as itself; the weather folder relies on it). Its
    ``columns.csv`` label is then only reported, so a vendor label can never claim a role the map
    gave another column. An equipment with no map falls back to its ``columns.csv``
    ``point_role``, then to the header; every name must be one the crosswalk knows exactly.
    """
    quantities: dict = {}
    seen: dict = {}
    ftype = crosswalk.openfdd_type(equip_type) or equip_type
    decided: dict = {}
    # the map's own columns first, so nothing later in the header can take their roles
    order = [c for c in header if c in names] + [c for c in header if c not in names]
    for col in order:
        meta_role, meta_unit = csv_meta.get(col, ("", ""))
        m = ColumnMapping(column=col, unit=meta_unit)
        decided[col] = m
        if col.strip().lower() in _METADATA_COLUMNS:
            m.status, m.reason = "metadata", "row identifier, not a point"
            continue
        row = None
        if col in names:
            m.name, m.source = names[col], source
            row = crosswalk.lookup(m.name)
        else:
            cands = (
                [(col, "identity")] if names else [(meta_role, "columns.csv"), (col, "identity")]
            )
            for cand, src in cands:
                if cand and crosswalk.lookup(cand) is not None:
                    m.name, m.source, row = cand, src, crosswalk.lookup(cand)
                    break
        if row is None:
            if m.name:
                m.status = "unknown_name"
                m.reason = f"open-fdd name {m.name!r} is not in the crosswalk"
            else:
                m.status = "no_role"
                m.reason = (
                    "not in the package map"
                    if names
                    else "the package gives this column no point name"
                )
                if meta_role:
                    m.name = meta_role
                    m.reason = (
                        f"{m.reason} (columns.csv point_role {meta_role!r})"
                        if names
                        else f"columns.csv point_role {meta_role!r} is not in the crosswalk"
                    )
            continue
        if row.camber_role is None:
            m.status, m.reason = "no_camber_role", row.reason
            continue
        if not row.applies_to(ftype):
            m.status = "not_applicable"
            m.reason = f"crosswalk row is for {', '.join(row.equip_types)} only, not {ftype}"
            continue
        unit, err = resolve_unit(row.quantity, meta_unit, unit_system)
        if err:
            m.status, m.reason = "unit_unsupported", err
            continue
        if row.camber_role in seen:
            m.status = "duplicate_role"
            m.reason = f"{row.camber_role} already comes from column {seen[row.camber_role]!r}"
            continue
        seen[row.camber_role] = col
        m.camber_role, m.unit_applied, m.status = row.camber_role, unit, MAPPED
        quantities[col] = row.quantity
    return [decided[c] for c in header], quantities


# --------------------------------------------------------------------------- package


def _building_root(path: str, building: str | None) -> str:
    """The building folder (the one holding ``manifest.json``) under ``path``."""
    if os.path.isfile(os.path.join(path, "manifest.json")):
        return path
    found = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "__MACOSX")))
        if dirpath != path and os.path.relpath(dirpath, path).count(os.sep) >= 2:
            dirnames[:] = []
            continue
        if "manifest.json" in filenames and os.path.basename(dirpath) != WEATHER_EQUIP:
            found.append(dirpath)
    if building:
        hit = [d for d in found if os.path.basename(d) == building]
        if not hit:
            raise ValueError(
                f"no building {building!r} in the package (found: "
                f"{', '.join(os.path.basename(d) for d in found) or 'none'})"
            )
        return hit[0]
    if not found:
        raise ValueError(
            "not an open-fdd package: no building folder with a manifest.json "
            "(openfdd_package_v1: <building>/manifest.json + <equip>/history_wide.csv)"
        )
    if len(found) > 1:
        raise ValueError(
            "the package holds several buildings; choose one with building= / --building: "
            + ", ".join(os.path.basename(d) for d in found)
        )
    return found[0]


def _equip_dirs(broot: str) -> list:
    """Relative paths (``/``-separated) of every folder holding a ``history_wide.csv``."""
    out = []
    for dirpath, dirnames, filenames in os.walk(broot):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if "history_wide.csv" in filenames and dirpath != broot:
            out.append(os.path.relpath(dirpath, broot).replace(os.sep, "/"))
    return sorted(out)


def _equip_ids(rels: list) -> dict:
    """Relative folder -> equipment id: the folder name, or the joined path when names repeat."""
    base = [r.rsplit("/", 1)[-1] for r in rels]
    return {r: (b if base.count(b) == 1 else r.replace("/", "__")) for r, b in zip(rels, base)}


def _read_equipment(
    broot: str,
    rel: str,
    eid: str,
    *,
    root_blocks: dict,
    inventory: dict,
    timezone: str,
    unit_system: str,
    resample: str | None,
    crosswalk: Crosswalk,
    warnings: list,
) -> OpenFddEquipment:
    edir = os.path.join(broot, *rel.split("/"))
    sidecars = [_read_json(os.path.join(edir, n)) for n in ("column_map.json", "history_wide.json")]
    block = root_blocks.get(eid) or root_blocks.get(rel) or {}
    blocks = [b for b in (*sidecars, block) if isinstance(b, dict)]
    points: dict = {}
    for b in reversed(blocks):  # the sidecar beside the CSV wins over the root map
        points.update(_points(b))
    is_weather = rel == WEATHER_EQUIP or rel.endswith("/" + WEATHER_EQUIP)
    stamp = _stamp(*blocks) or inventory.get(eid)
    if stamp is None and is_weather:
        stamp = "weather"  # the documented weather folder is weather by contract
    cls = crosswalk.equip_class(stamp) or UNCLASSIFIED
    eq = OpenFddEquipment(
        equip=WEATHER_EQUIP if is_weather else eid,
        equip_class=cls,
        equip_type=stamp,
        path=rel,
        parent=_parent(*blocks),
    )
    if stamp is not None and cls == UNCLASSIFIED:
        warnings.append(f"{eid}: equipment stamp {stamp!r} is not one CAMBER knows -> unclassified")
    csv_path = os.path.join(edir, "history_wide.csv")
    header = list(pd.read_csv(csv_path, nrows=0).columns)
    ts_col = next((c for c in ("timestamp_utc", "timestamp") if c in header), None)
    if ts_col is None:
        raise ValueError(f"{rel}/history_wide.csv has no timestamp_utc (or timestamp) column")
    names = {}
    for name, col in points.items():
        if col in header:
            names[col] = name
        else:
            warnings.append(f"{eid}: map names column {col!r} ({name}) that the CSV lacks")
    eq.columns, quantities = map_columns(
        [c for c in header if c != ts_col],
        names=names,
        csv_meta=_columns_csv(os.path.join(edir, "columns.csv")),
        equip_type=stamp,
        unit_system=unit_system,
        crosswalk=crosswalk,
    )
    mapped = eq.mapped
    raw = pd.read_csv(
        csv_path, usecols=[ts_col] + [c.column for c in mapped], dtype={ts_col: "string"}
    )
    eq.rows_read = len(raw)
    idx = to_site_clock(raw[ts_col], timezone, utc_column=ts_col == "timestamp_utc")
    good = idx.notna().to_numpy()
    eq.rows_bad_timestamp = int((~good).sum())
    if eq.rows_bad_timestamp:
        warnings.append(f"{eid}: {eq.rows_bad_timestamp} row(s) with an unusable timestamp skipped")
    raw = raw.loc[good].drop(columns=[ts_col])
    raw.index = pd.DatetimeIndex(idx[good].to_numpy())
    if mapped:
        frame = _role_frame(raw, mapped, quantities, eq.equip, warnings)
        eq.frame = _resample(frame.sort_index(kind="stable"), resample)
    return eq


def _package_dir(path: str, tmp: str) -> tuple:
    """``(directory, zip sha256 or None)``: a zip is extracted (safely) into ``tmp``."""
    if os.path.isdir(path):
        return path, None
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path}: not a package folder or a .zip archive")
    from ...datasets._archive import safe_extract

    safe_extract(path, tmp)
    return tmp, _sha256(path)


def read_package(
    path,
    *,
    timezone: str,
    unit_system: str,
    building: str | None = None,
    resample: str | None = None,
    crosswalk: Crosswalk | None = None,
) -> OpenFddPackage:
    """Read an open-fdd ``openfdd_package_v1`` package (a folder or a ``.zip``); provisional.

    ``timezone`` (the site's IANA zone) and ``unit_system`` (``"ip"`` or ``"si"``) are required:
    the package's stamps are UTC and it records neither, so nothing is guessed. Every
    ``history_wide.csv`` becomes one :class:`OpenFddEquipment` whose frame is in CAMBER roles, IP
    units, percent positions and naive site wall-clock time. ``building`` picks one building of a
    multi-building package; ``resample`` (e.g. ``"15min"``) averages onto a grid, ``None`` keeps
    the package's own. The crosswalk defaults to the one CAMBER ships.
    """
    tz, us = check_args(timezone, unit_system)
    cw = crosswalk or load_crosswalk()
    path = os.fspath(path)
    with tempfile.TemporaryDirectory(prefix="camber-openfdd-") as tmp:
        pdir, zsha = _package_dir(path, tmp)
        broot = _building_root(pdir, building)
        manifest = _read_json(os.path.join(broot, "manifest.json")) or {}
        bid = str(manifest.get("building_id") or os.path.basename(os.path.normpath(broot)))
        pkg = OpenFddPackage(
            building_id=bid,
            source_kind="package",
            timezone=tz,
            unit_system=us,
            crosswalk_version=cw.version,
            crosswalk_docs_commit=cw.docs_commit,
            manifest=manifest,
            package_sha256=zsha,
        )
        sv = manifest.get("schema_version")
        if sv and sv != SCHEMA_VERSION:
            pkg.warnings.append(f"manifest schema_version {sv!r} is not {SCHEMA_VERSION!r}")
        mtz = manifest.get("timezone")
        if mtz:
            pkg.notes.append(
                f"manifest timezone {mtz!r} recorded but not used as the site zone "
                f"(the contract's stamps are UTC; site zone given: {tz})"
            )
        for dirpath, _dirs, filenames in os.walk(broot):
            for fn in filenames:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, broot).replace(os.sep, "/")
                pkg.files[rel] = _sha256(full)
        root_blocks = _equip_blocks(_read_json(os.path.join(broot, "column_map.json")))
        inventory = _inventory(os.path.join(broot, "equipment_inventory.json"))
        rels = _equip_dirs(broot)
        ids = _equip_ids(rels)
        for rel in rels:
            eq = _read_equipment(
                broot,
                rel,
                ids[rel],
                root_blocks=root_blocks,
                inventory=inventory,
                timezone=tz,
                unit_system=us,
                resample=resample,
                crosswalk=cw,
                warnings=pkg.warnings,
            )
            if eq.equip in pkg.equipment:
                raise ValueError(f"two package folders both read as equipment {eq.equip!r}")
            pkg.equipment[eq.equip] = eq
        used = {"manifest.json", "column_map.json", "equipment_inventory.json"}
        for rel in rels:
            for n in ("history_wide.csv", "columns.csv", "column_map.json", "history_wide.json"):
                used.add(f"{rel}/{n}")
        for rel in sorted(pkg.files):
            if rel in used:
                continue
            base = rel.rsplit("/", 1)[-1]
            why = (
                "open-fdd output / UI state" if base in _KNOWN_OTHER else "not part of the contract"
            )
            pkg.ignored.append(f"{rel} ({why})")
        missing = [e for e in root_blocks if e not in pkg.equipment and e not in rels]
        if missing:
            pkg.warnings.append(
                f"column_map.json lists {len(missing)} equipment with no history_wide.csv: "
                + ", ".join(sorted(missing)[:10])
                + (" ..." if len(missing) > 10 else "")
            )
        if not pkg.equipment:
            raise ValueError(f"{bid}: the package has no <equip>/history_wide.csv")
    return pkg
