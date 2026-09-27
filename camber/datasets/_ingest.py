"""Ingest a fetched catalog dataset into a :class:`~camber.store.ParquetStore`.

The model: one **facility** per dataset (``ds-<id>``; BDG2: one per site, ``ds-bdg2-<site>``), and
each labelled run of a simulated dataset becomes one **equipment** ``<equip>__<scenario>`` of the
same class -- so a single ``camber run`` scores every scenario, and ``camber datasets score``
compares the findings with the labels recorded on the facility. A ``splice`` derived run stitches a
fault-free run and a faulted run at an onset date (``<equip>__onset_<scenario>``) for fault-onset /
drift exercises.

Pipeline per run (:func:`read_raw_run` is shared with the LBNL benchmark): column-pruned
``read_csv`` (only mapped, quirk and transform columns; timestamps parsed with the entry's pinned
``timestamp_format``) -> ``fix`` quirks (skipped with ``corrections=False``) -> CAMBER's column
transforms (``recode`` / ``derive``) -> point -> role mapping -> resample (default 15 min: mean,
which is the duty of a regularly-sampled status point; the warm-up/cool-down exclusion flags take
the max) -> source-unit -> IP conversion -> percent normalization -> plausibility warnings ->
staging store.

**Corrections.** ``fix`` quirks correct problems in the published data (each is a described data
issue on the catalog entry). ``corrections=False`` (``camber datasets ingest --no-corrections``)
ingests the data exactly as published so a learner can compare the two; the mode is part of the
content hash and is recorded in the provenance. Runs excluded by a data issue (e.g. a "fault" run
that contains no fault) are ingested for inspection but carry no scoring label.

**Idempotent and crash-safe.** A content hash covers the verified file sha256s, the canonical
ingest spec, the subset, the mapping bytes and :data:`INGEST_VERSION`. When every target facility
already carries that hash the ingest is skipped (``force=True`` re-ingests). Otherwise the data is
written to ``<store>/_staging/`` (invisible to reads: pyarrow ignores ``_``-prefixed paths) and
swapped in per facility: the old partition is renamed aside, the new one renamed into place, the
old one deleted. A crash before the swap leaves the previous data untouched.

Provenance (licence, citation, sha256s, content hash, labels, ...) is recorded on each facility's
registry entry under the single namespaced key ``"dataset"`` (:data:`META_KEY`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import pandas as pd

from .. import __version__
from ..model.mapping import MappingProvider
from ..model.roles import Role
from ..store import FacilityRegistry, ParquetStore
from ..units import normalize_percent_frame
from . import _licence, _paths
from ._archive import archive_kind, safe_extract
from ._catalog import DatasetEntry, package_text
from ._fetch import check_disk, sha256_file
from ._quirks import apply_quirks, quirk_columns
from ._readers import read_table, require_extras
from ._units import convert_frame, convert_series, plausibility_warnings

INGEST_VERSION = 1
_STAGING = "_staging"
_ANY_ON = frozenset({Role.WARMUP, Role.COOLDOWN})


@dataclass
class IngestResult:
    """What an ingest did (or why it was skipped)."""

    dataset_id: str
    store: str
    subset: str
    facilities: list = field(default_factory=list)
    rows: int = 0
    equipment: int = 0
    skipped: bool = False
    content_hash: str = ""
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    corrections: bool = True

    def as_dict(self) -> dict:
        """A JSON-friendly dict."""
        return asdict(self)


# --------------------------------------------------------------------------- inputs


def _manifest_files(root: str, dataset_id: str) -> dict:
    return (_paths.read_manifest(root).get(dataset_id) or {}).get("files", {})


def verified_inputs(entry: DatasetEntry, subset: str | None, root: str) -> dict:
    """``{file name: (path, sha256)}`` for a subset's files; raises if any is not fetched.

    The sha256 is the one recorded when the file was fetched and verified (the manifest), falling
    back to hashing the file when the manifest lacks it.
    """
    ddir = _paths.downloads_dir(root, entry.id)
    recorded = _manifest_files(root, entry.id)
    out = {}
    missing = []
    for f in entry.subset_files(subset):
        path = os.path.join(ddir, f["name"])
        if not os.path.isfile(path):
            missing.append(f["name"])
            continue
        sha = (recorded.get(f["name"]) or {}).get("sha256") or sha256_file(path)
        if f.get("sha256") and sha != f["sha256"]:
            raise ValueError(
                f"{entry.id}: {f['name']} does not match the catalog's sha256 -- re-fetch it"
            )
        out[f["name"]] = (path, sha)
    if missing:
        sub = f" --subset {subset}" if subset else ""
        raise FileNotFoundError(
            f"{entry.id}: not fetched yet ({', '.join(missing)}); run "
            f"`camber datasets fetch {entry.id}{sub}` first"
        )
    return out


def _pending_bytes(path: str, members, dest: str) -> int:
    """Uncompressed bytes still to extract (members already present at full size are free)."""
    import zipfile

    if archive_kind(path) != "zip":  # pragma: no cover - every 0.86 archive is a zip
        return 0
    total = 0
    with zipfile.ZipFile(path) as z:
        for m in members:
            info = z.getinfo(m)
            target = os.path.join(dest, m)
            if not (os.path.isfile(target) and os.path.getsize(target) == info.file_size):
                total += info.file_size
    return total


def _duplicate_runs(runs, inputs: dict) -> dict:
    """``{run id: canonical run id}`` for runs whose archive member is byte-identical to an earlier
    run's (same CRC-32 and size in the same zip).

    Publishers do ship one simulation under several labels -- LBNL's single-duct AHU "leakage
    severities" 010/025/040/050 are one file four times -- and ingesting each copy as its own
    scenario would score one case several times.
    """
    import zipfile

    seen: dict = {}
    dupes: dict = {}
    by_file: dict = {}
    for r in runs:
        if r.get("member"):
            by_file.setdefault(r["file"], []).append(r)
    for fname, rs in by_file.items():
        path = inputs[fname][0]
        if archive_kind(path) != "zip":  # pragma: no cover - every 0.86 archive is a zip
            continue
        with zipfile.ZipFile(path) as z:
            for r in rs:
                info = z.getinfo(r["member"])
                key = (fname, info.CRC, info.file_size)
                if key in seen:
                    dupes[r["id"]] = seen[key]
                else:
                    seen[key] = r["id"]
    return dupes


def _extract_members(entry, runs, inputs: dict, root: str) -> dict:
    """Extract the archive members the runs need; returns ``{(file, member): path}``."""
    by_file: dict = {}
    for r in runs:
        if r.get("member"):
            by_file.setdefault(r["file"], []).append(r["member"])
    out = {}
    for fname, members in by_file.items():
        path = inputs[fname][0]
        dest = os.path.join(
            _paths.extracted_dir(root, entry.id), os.path.splitext(os.path.basename(fname))[0]
        )
        check_disk(dest, _pending_bytes(path, members, dest))
        safe_extract(path, dest, members=members)
        for m in members:
            out[(fname, m)] = os.path.join(dest, m)
    return out


def content_hash(
    entry: DatasetEntry,
    subset: str,
    shas: dict,
    mapping_text: str = "",
    *,
    corrections: bool = True,
) -> str:
    """The idempotency key of an ingest (see the module docstring).

    A raw (``corrections=False``) and a corrected ingest of the same inputs hash differently, so
    switching mode always re-ingests.
    """
    payload = {
        "dataset": entry.id,
        "subset": subset,
        "files": dict(sorted(shas.items())),
        "ingest": entry.ingest,
        "subset_spec": entry.subset(subset),
        "mapping": hashlib.sha256(mapping_text.encode("utf-8")).hexdigest(),
        "ingest_version": INGEST_VERSION,
        "corrections": bool(corrections),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------- frames


def _resample(frame: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    if not rule or frame.empty:
        return frame
    cols = {}
    for col in frame.columns:
        r = frame[col].resample(rule)
        cols[col] = r.max() if col in _ANY_ON else r.mean()
    return pd.concat(cols, axis=1)


def transform_columns(spec: dict) -> set:
    """Raw columns CAMBER's ``recode`` / ``derive`` transforms read (a pruned read keeps them)."""
    out = set((spec.get("recode") or {}).keys())
    for dv in spec.get("derive") or []:
        out.update(dv.get("sum") or [])
        if dv.get("above"):
            out.add(dv["above"][0])
    return out


def apply_transforms(raw: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Apply the spec's ``recode`` then ``derive`` to a raw frame (a copy; see the catalog docs)."""
    recode = spec.get("recode") or {}
    derive = spec.get("derive") or []
    if not recode and not derive:
        return raw
    out = raw.copy()
    for col, table in recode.items():
        if col in out.columns:
            vals = pd.to_numeric(out[col], errors="coerce")
            out[col] = vals.replace({float(k): float(v) for k, v in table.items()})
    for dv in derive:
        if "above" in dv:  # a 0/1 flag: the source is above a threshold (NaN stays NaN)
            col, threshold = dv["above"]
            if col in out.columns:
                vals = pd.to_numeric(out[col], errors="coerce")
                out[dv["column"]] = (vals > float(threshold)).astype(float).where(vals.notna())
            continue
        src = [c for c in dv["sum"] if c in out.columns]
        if len(src) == len(dv["sum"]):
            out[dv["column"]] = (
                out[src].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=len(src))
            )
    return out


def read_raw_run(
    path: str,
    mapping: MappingProvider,
    spec: dict,
    run_id: str | None = None,
    *,
    corrections: bool = True,
    sheet=None,
):
    """One wide table run -> ``(raw frame indexed by timestamp, quirk notes)``, before role mapping.

    The table is read by extension through :func:`._readers.read_table`: CSV in the core, an
    ``.xlsx`` workbook through the ``xlsx`` extra (``sheet``, else the spec's ``sheet``, names
    the worksheet; default the first).

    Reads only the timestamp, the mapped columns and the columns quirks and transforms need;
    parses timestamps with the spec's ``timestamp_format`` when it pins one; drops unparseable and
    duplicated stamps; applies the ``fix`` quirks (unless ``corrections`` is false) and CAMBER's
    ``recode`` / ``derive`` transforms. Shared by the ingester and ``examples/lbnl_fdd``.
    """
    ts = spec.get("timestamp", "Datetime")
    quirks = spec.get("quirks") or []
    extra = quirk_columns(quirks) | transform_columns(spec)
    derived = {dv.get("column") for dv in spec.get("derive") or []}
    fmt = spec.get("timestamp_format")
    raw = read_table(
        path,
        usecols=lambda c: c == ts or c in extra or (c not in derived and mapping.role_of(c)),
        sheet=sheet if sheet is not None else spec.get("sheet"),
    )
    raw[ts] = (
        pd.to_datetime(raw[ts], format=fmt, errors="coerce")
        if fmt
        else pd.to_datetime(raw[ts], errors="coerce")
    )
    raw = raw.set_index(ts)
    raw = raw[~raw.index.isna()]
    raw = raw[~raw.index.duplicated(keep="first")].sort_index()
    raw, notes = apply_quirks(raw, quirks, run=run_id, corrections=corrections)
    return apply_transforms(raw, spec), notes


def read_wide_run(
    path: str,
    mapping: MappingProvider,
    spec: dict,
    run_id: str | None = None,
    *,
    corrections: bool = True,
    sheet=None,
):
    """One wide table run -> (role frame at the spec's resample, quirk notes, warnings)."""
    raw, notes = read_raw_run(path, mapping, spec, run_id, corrections=corrections, sheet=sheet)
    cols: dict = {}
    for c in raw.columns:
        role = mapping.role_of(c)
        if role is not None and role not in cols:
            cols[role] = pd.to_numeric(raw[c], errors="coerce")
    if not cols:
        return pd.DataFrame(), notes, [f"{run_id}: no mapped columns"]
    frame = _role_frame(cols, spec)
    return frame, notes, plausibility_warnings(frame, label=str(run_id))


def _role_frame(cols: dict, spec: dict) -> pd.DataFrame:
    """``{role: raw series}`` -> resampled, IP-converted, percent-normalized role frame."""
    frame = _resample(pd.DataFrame(cols), spec.get("resample", "15min"))
    frame = convert_frame(frame, spec.get("units") or {})
    return normalize_percent_frame(frame)


def _brick_grouping(entry, inputs, root):
    """The entry's Brick grouping plan source (``None`` when it declares no ``ingest.brick``)."""
    from ._brickgroup import grouping_from_brick

    bspec = entry.ingest.get("brick")
    if not bspec:
        return None
    fname, member = bspec["file"], bspec.get("member")
    if member:
        path = _extract_members(entry, [{"file": fname, "member": member}], inputs, root)[
            (fname, member)
        ]
    else:
        path = inputs[fname][0]
    with open(path, encoding="utf-8") as fh:
        ttl = fh.read()
    return grouping_from_brick(ttl, dict(bspec.get("equip_classes") or {}))


def _grouped_run(path, run, spec, grouping, mapping_spec, corrections, used: set):
    """One ``group: "brick"`` run -> ``({equip: (class, role frame)}, notes, warnings)``."""
    from ._brickgroup import mapping_overrides

    mp, over = mapping_overrides(mapping_spec)
    header = read_table(path, sheet=run.get("sheet", spec.get("sheet")), nrows=0).columns
    ts = spec.get("timestamp", "Datetime")
    plan = grouping.plan(
        [c for c in header if c != ts],
        default_equip=run["equip"],
        default_class=run["class"],
        mapping=mp,
        overrides=over,
    )
    notes: list = []
    if not plan:
        return {}, notes, [f"{run['id']}: no column is a mapped Brick point"]
    roles = MappingProvider.from_dict({"aliases": {c: g.role.value for c, g in plan.items()}})
    raw, qn = read_raw_run(
        path, roles, spec, run["id"], corrections=corrections, sheet=run.get("sheet")
    )
    notes += qn
    suffix = f"__{_scenario(run)}" if run.get("label") else ""
    by_equip: dict = {}
    for col, g in plan.items():
        if col not in raw.columns:
            continue
        eq = g.equip + suffix
        if (eq, g.role) in used:
            notes.append(f"{run['id']}: {col} skipped: {eq} already has {g.role.value}")
            continue
        used.add((eq, g.role))
        by_equip.setdefault(eq, (g.equip_class, {}))[1][g.role] = pd.to_numeric(
            raw[col], errors="coerce"
        )
        if g.source == "mapping":
            notes.append(
                f"{run['id']}: {col} -> {eq}/{g.role.value} (mapping file overrides Brick)"
            )
    out, warns = {}, []
    for eq, (cls, cols) in by_equip.items():
        frame = _role_frame(cols, spec)
        out[eq] = (cls, frame)
        warns += plausibility_warnings(frame, label=f"{run['id']}/{eq}")
    return out, notes, warns


def splice(base: pd.DataFrame, fault: pd.DataFrame, onset) -> pd.DataFrame:
    """Fault-free ``base`` before ``onset``, then ``fault`` from ``onset`` on (same roles)."""
    t = pd.Timestamp(onset)
    cols = [c for c in base.columns if c in fault.columns]
    return pd.concat([base.loc[base.index < t, cols], fault.loc[fault.index >= t, cols]])


def _scenario(run: dict) -> str:
    return str(run["id"]).split("__", 1)[-1]


def _equip_id(run: dict) -> str:
    return f"{run['equip']}__{_scenario(run)}"


# --------------------------------------------------------------------------- store swap


def _staging_root(store: ParquetStore) -> str:
    return os.path.join(store.root, _STAGING, f"ingest-{os.getpid()}-{id(store):x}")


def _swap_in(store: ParquetStore, staging: str, fid: str) -> None:
    """Replace ``fid``'s partition with the staged one (rename aside -> rename in -> delete)."""
    from ..resolve import clear_store_cache

    final = os.path.join(store.root, f"facility_id={fid}")
    staged = os.path.join(staging, f"facility_id={fid}")
    aside = os.path.join(store.root, _STAGING, f"{fid}.old-{os.getpid()}")
    os.makedirs(store.root, exist_ok=True)
    if os.path.isdir(final):
        if os.path.isdir(aside):  # pragma: no cover - leftover of a crashed run
            shutil.rmtree(aside)
        os.replace(final, aside)
    try:
        if os.path.isdir(staged):
            os.replace(staged, final)
    except OSError:  # pragma: no cover - restore the old data if the rename-in fails
        if os.path.isdir(aside):
            os.replace(aside, final)
        raise
    if os.path.isdir(aside):
        shutil.rmtree(aside)
    store._invalidate_catalog()
    clear_store_cache(store.root, fid)


META_KEY = "dataset"  # the one registry-meta key the catalog owns (provenance + ingest record)


def _register(store: ParquetStore, fid: str, name: str, meta: dict) -> None:
    """Record provenance under ``meta["dataset"]``, replacing only that key.

    Any other metadata on the facility (a display-name change, its lifecycle state) is left
    untouched; the name is set only when the facility has none yet. A facility dropped by an
    earlier ``remove --purge-store`` is tombstoned; re-ingesting the *same* dataset reclaims it.
    """
    reg = FacilityRegistry(store.root)
    tomb = reg.tombstones().get(fid)
    if tomb is not None and tomb.get("dataset_id") == meta.get("dataset_id"):
        # The id is derived from the dataset itself, so re-ingesting after a purge re-creates the
        # very same facility -- the one case a tombstoned id may be reclaimed.
        reg.reclaim(fid, reason=f"re-ingest of dataset {meta.get('dataset_id')}")
    has_name = bool(reg.get(fid).get("name"))
    reg.register(fid, name=None if has_name else name, **{META_KEY: meta})


def dataset_meta(meta: dict) -> dict:
    """The catalog's namespaced block of a facility's registry entry (``{}`` if absent)."""
    block = (meta or {}).get(META_KEY)
    return block if isinstance(block, dict) else {}


def _existing_hashes(store: ParquetStore, dataset_id: str) -> dict:
    return {
        fid: dataset_meta(m).get("content_hash")
        for fid, m in store.facilities_meta().items()
        if dataset_meta(m).get("dataset_id") == dataset_id
    }


def _base_meta(entry: DatasetEntry, root: str, subset: str, shas: dict, chash: str) -> dict:
    man = _paths.read_manifest(root).get(entry.id) or {}
    meta = entry.provenance()
    meta.update(
        {
            "subset": subset,
            "sha256": dict(sorted(shas.items())),
            "content_hash": chash,
            "fetched_at": man.get("fetched_at", ""),
            "ingested_at": _paths.utc_now(),
            "camber_version": __version__,
            "ingest_version": INGEST_VERSION,
        }
    )
    if man.get("acknowledged_at"):
        meta["acknowledgement"] = man["acknowledged_at"]
        meta["acknowledged_licence"] = man.get("acknowledged_licence", entry.licence)
    return meta


# --------------------------------------------------------------------------- adapters


def _ingest_wide(entry, subset, inputs, root, staging, progress, corrections=True) -> tuple:
    spec = entry.ingest
    mapping_text = package_text("mappings", spec["mapping"]) if spec.get("mapping") else "{}"
    mapping_spec = json.loads(mapping_text)
    mapping = MappingProvider.from_dict(mapping_spec)
    grouping = _brick_grouping(entry, inputs, root)
    used: set = set()
    runs = entry.runs(subset)
    duplicates = _duplicate_runs(runs, inputs)
    runs = [r for r in runs if r["id"] not in duplicates]
    paths = _extract_members(entry, runs, inputs, root)
    fid = spec["facility"]
    st = ParquetStore(staging)
    keep = {d[k] for d in spec.get("derived") or [] for k in ("base", "fault")}
    frames: dict = {}
    labels, onsets, excluded, notes, warns = {}, {}, {}, [], []
    for dup, canon in duplicates.items():
        notes.append(f"{dup} skipped: byte-identical to {canon} in the archive (one run, not two)")
    rows = 0
    for i, run in enumerate(runs, 1):
        if progress:
            progress(f"{entry.id}: run {i}/{len(runs)} {run['id']}")
        path = paths[(run["file"], run["member"])] if run.get("member") else inputs[run["file"]][0]
        if run.get("group") == "brick":
            groups, qn, w = _grouped_run(path, run, spec, grouping, mapping_spec, corrections, used)
            notes += [n for n in qn if n not in notes]
            warns += w
            for eq, (cls, frame) in groups.items():
                rows += st.write_role_frame(frame, facility_id=fid, equip=eq, equip_class=cls)
                if run.get("exclude"):
                    excluded[eq] = {"label": run["label"], "issue": run["exclude"]}
                else:
                    labels[eq] = run["label"]
            continue
        frame, qn, w = read_wide_run(
            path, mapping, spec, run_id=run["id"], corrections=corrections, sheet=run.get("sheet")
        )
        notes += [n for n in qn if n not in notes]
        warns += w
        eq = _equip_id(run)
        rows += st.write_role_frame(frame, facility_id=fid, equip=eq, equip_class=run["class"])
        if run.get("exclude"):
            # kept for inspection, never scored: the data issue says why
            excluded[eq] = {"label": run["label"], "issue": run["exclude"]}
        else:
            labels[eq] = run["label"]
        if run["id"] in keep:
            frames[run["id"]] = frame
    by_id = {r["id"]: r for r in runs}
    for dv in spec.get("derived") or []:
        if dv["base"] not in frames or dv["fault"] not in frames:
            notes.append(f"splice {dv['fault']} skipped: its runs are not in subset {subset!r}")
            continue
        run = by_id[dv["fault"]]
        eq = f"{dv.get('equip', run['equip'])}__onset_{_scenario(run)}"
        cls = dv.get("class", run["class"])
        joined = splice(frames[dv["base"]], frames[dv["fault"]], dv["onset"])
        rows += st.write_role_frame(joined, facility_id=fid, equip=eq, equip_class=cls)
        onsets[eq] = {"label": run["label"], "onset": str(dv["onset"]), "base": dv["base"]}
    extra = {
        "labels": labels,
        "onsets": onsets,
        "excluded": excluded,
        "quirks": notes,
        "runs": len(runs),
        "duplicates": duplicates,
    }
    if grouping is not None:
        extra["grouping"] = "brick"
    n_eq = len(labels) + len(excluded) + len(onsets)
    return {fid: (entry.title, rows, n_eq, extra)}, mapping_text, warns


def _bdg2_buildings(meta: pd.DataFrame, sub: dict, headers: dict) -> dict:
    """``{site: [building, ...]}`` for a BDG2 subset (include list first, then sorted, capped)."""
    sites = sub.get("sites", "all")
    site_list = sorted(meta["site_id"].dropna().unique()) if sites == "all" else list(sites)
    cap = sub.get("max_buildings_per_site")
    include = list(sub.get("include_buildings") or [])
    have = set().union(*headers.values()) if headers else set()
    out = {}
    for site in site_list:
        inside = sorted(b for b in meta.loc[meta["site_id"] == site, "building_id"] if b in have)
        chosen = [b for b in include if b in inside]
        for b in inside:
            if cap is not None and len(chosen) >= cap:
                break
            if b not in chosen:
                chosen.append(b)
        if chosen:
            out[site] = chosen
    return out


def _ingest_bdg2(entry, subset, inputs, root, staging, progress, corrections=True) -> tuple:
    spec = entry.ingest
    sub = entry.subset(subset)
    src = spec.get("meter_source", "cleaned")
    meters = list(sub.get("meters") or ["electricity"])
    meta = pd.read_csv(inputs["metadata.csv"][0])
    files = {}
    for m in meters:
        name = f"cleaned/{m}_cleaned.csv" if src == "cleaned" else f"raw/{m}.csv"
        if name in inputs:
            files[m] = inputs[name][0]
    headers = {m: set(pd.read_csv(p, nrows=0).columns) - {"timestamp"} for m, p in files.items()}
    chosen = _bdg2_buildings(meta, sub, headers)
    wanted = {b for bs in chosen.values() for b in bs}
    weather = pd.read_csv(
        inputs["weather.csv"][0],
        usecols=["timestamp", "site_id", "airTemperature"],
        parse_dates=["timestamp"],
    )
    wunits = spec.get("weather_units") or {"oat": "degC"}
    roles = spec.get("meter_roles") or {}
    default_role = Role(spec.get("default_meter_role", "energy_rate"))
    rule = spec.get("resample", "1h")
    st = ParquetStore(staging)
    out: dict = {}
    series: dict = {}
    for m, path in files.items():
        if progress:
            progress(f"{entry.id}: reading {m}")
        cols = ["timestamp"] + sorted(wanted & headers[m])
        df = pd.read_csv(path, usecols=cols, parse_dates=["timestamp"]).set_index("timestamp")
        series[m] = df
    tz = meta.set_index("building_id")["timezone"].to_dict()
    for site, buildings in chosen.items():
        fid = f"{spec['facility']}-{site.lower()}"
        rows = 0
        n_eq = 0
        w = weather[weather["site_id"] == site].set_index("timestamp")["airTemperature"]
        if not w.empty:
            oat = convert_series(w.sort_index(), wunits.get("oat", "degC"))
            wf = _resample(pd.DataFrame({Role.OAT: oat}), rule)
            rows += st.write_role_frame(wf, facility_id=fid, equip="weather", equip_class="WEATHER")
            n_eq += 1
        for b in buildings:
            for m, df in series.items():
                if b not in df.columns:
                    continue
                s = pd.to_numeric(df[b], errors="coerce").dropna()
                if s.empty:
                    continue
                role = Role(roles[m]) if m in roles else default_role
                frame = _resample(pd.DataFrame({role: s}), rule)
                rows += st.write_role_frame(
                    frame, facility_id=fid, equip=f"{b}__{m}", equip_class=f"{m.upper()}_METER"
                )
                n_eq += 1
        extra = {
            "site_id": site,
            "timezone": tz.get(buildings[0], ""),
            "buildings": buildings,
            "meters": meters,
        }
        out[fid] = (f"{entry.title} -- site {site}", rows, n_eq, extra)
    return out, "", []


_ADAPTERS: dict = {"wide_csv": _ingest_wide, "bdg2": _ingest_bdg2}


# --------------------------------------------------------------------------- entry point


def ingest_dataset(
    entry: DatasetEntry,
    store,
    *,
    subset: str | None = None,
    data_dir=None,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    corrections: bool = True,
    accept_noncommercial: bool = False,
    via: str = "ingest",
) -> IngestResult:
    """Ingest a fetched dataset into ``store`` (path or :class:`ParquetStore`); see the module.

    ``corrections=False`` skips the entry's ``fix`` quirks and ingests the data as published. A
    research-only entry needs an acknowledgement of its current licence in the cache manifest (a
    ``fetch`` with ``accept_noncommercial``) or ``accept_noncommercial=True`` here, which records
    one; otherwise ``PermissionError``.
    """
    st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
    sname = subset or "default"
    entry.subset(sname)  # KeyError on an unknown subset, before any work
    root = _paths.data_dir(data_dir)
    require_extras(entry.requires_extras, what=f"dataset {entry.id}")
    _licence.require(
        root,
        entry,
        accept_noncommercial=accept_noncommercial,
        subset=sname,
        via=via,
        action="ingest",
    )
    inputs = verified_inputs(entry, sname, root)
    shas = {k: v[1] for k, v in inputs.items()}
    mapping_text = (
        package_text("mappings", entry.ingest["mapping"]) if entry.ingest.get("mapping") else ""
    )
    chash = content_hash(entry, sname, shas, mapping_text, corrections=corrections)
    existing = _existing_hashes(st, entry.id)
    result = IngestResult(
        dataset_id=entry.id, store=os.path.abspath(st.root), subset=sname, corrections=corrections
    )
    result.content_hash = chash
    if (
        not force
        and existing
        and all(h == chash for h in existing.values())
        and all(fid in st.facilities() for fid in existing)
    ):
        result.skipped = True
        result.facilities = sorted(existing)
        result.notes.append("already ingested with identical inputs (use force to re-ingest)")
        return result
    staging = _staging_root(st)
    try:
        facs, _, warns = _ADAPTERS[entry.ingest["adapter"]](
            entry, sname, inputs, root, staging, progress, corrections
        )
        base = _base_meta(entry, root, sname, shas, chash)
        base["corrections"] = "applied" if corrections else "skipped (published data as-is)"
        for fid, (name, rows, n_eq, extra) in facs.items():
            _swap_in(st, staging, fid)
            _register(st, fid, name, {**base, **extra, "rows": rows, "equipment": n_eq})
            result.rows += rows
            result.equipment += n_eq
            result.facilities.append(fid)
            result.notes += [n for n in extra.get("quirks", []) if n not in result.notes]
        for fid in existing:  # a facility this subset no longer produces (e.g. a BDG2 site)
            if fid not in facs:
                st.drop_facility(fid, forget=True)
        result.warnings = list(warns)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result.facilities.sort()
    return result
