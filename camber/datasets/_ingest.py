"""Ingest a fetched catalog dataset into a :class:`~camber.store.ParquetStore`.

The model: one **facility** per dataset (``ds-<id>``; BDG2: one per site, ``ds-bdg2-<site>``), and
each labelled run of a simulated dataset becomes one **equipment** ``<equip>__<scenario>`` of the
same class -- so a single ``camber run`` scores every scenario, and ``camber datasets score``
compares the findings with the labels recorded on the facility. A ``splice`` derived run stitches a
fault-free run and a faulted run at an onset date (``<equip>__onset_<scenario>``) for fault-onset /
drift exercises.

Pipeline per run: column-pruned ``read_csv`` (only mapped + quirk columns, as the LBNL benchmark
does) -> ``fix`` quirks -> point -> role mapping -> resample (default 15 min: mean, which is the
duty of a regularly-sampled status point; the warm-up/cool-down exclusion flags take the max) ->
source-unit -> IP conversion -> percent normalization -> plausibility warnings -> staging store.

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
from . import _paths
from ._archive import archive_kind, safe_extract
from ._catalog import DatasetEntry, package_text
from ._fetch import check_disk, sha256_file
from ._quirks import apply_quirks, quirk_columns
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


def content_hash(entry: DatasetEntry, subset: str, shas: dict, mapping_text: str = "") -> str:
    """The idempotency key of an ingest (see the module docstring)."""
    payload = {
        "dataset": entry.id,
        "subset": subset,
        "files": dict(sorted(shas.items())),
        "ingest": entry.ingest,
        "subset_spec": entry.subset(subset),
        "mapping": hashlib.sha256(mapping_text.encode("utf-8")).hexdigest(),
        "ingest_version": INGEST_VERSION,
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


def read_wide_run(path: str, mapping: MappingProvider, spec: dict, run_id: str | None = None):
    """One wide CSV run -> (role frame at the spec's resample, quirk notes, warnings)."""
    ts = spec.get("timestamp", "Datetime")
    quirks = spec.get("quirks") or []
    extra = quirk_columns(quirks)
    raw = pd.read_csv(
        path,
        usecols=lambda c: c == ts or c in extra or mapping.role_of(c) is not None,
        parse_dates=[ts],
    ).set_index(ts)
    raw = raw[~raw.index.isna()]
    raw = raw[~raw.index.duplicated(keep="first")].sort_index()
    raw, notes = apply_quirks(raw, quirks, run=run_id)
    cols: dict = {}
    for c in raw.columns:
        role = mapping.role_of(c)
        if role is not None and role not in cols:
            cols[role] = pd.to_numeric(raw[c], errors="coerce")
    if not cols:
        return pd.DataFrame(), notes, [f"{run_id}: no mapped columns"]
    frame = _resample(pd.DataFrame(cols), spec.get("resample", "15min"))
    frame = convert_frame(frame, spec.get("units") or {})
    frame = normalize_percent_frame(frame)
    return frame, notes, plausibility_warnings(frame, label=str(run_id))


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
    return meta


# --------------------------------------------------------------------------- adapters


def _ingest_wide(entry, subset, inputs, root, staging, progress) -> tuple:
    spec = entry.ingest
    mapping_text = package_text("mappings", spec["mapping"])
    mapping = MappingProvider.from_dict(json.loads(mapping_text))
    runs = entry.runs(subset)
    duplicates = _duplicate_runs(runs, inputs)
    runs = [r for r in runs if r["id"] not in duplicates]
    paths = _extract_members(entry, runs, inputs, root)
    fid = spec["facility"]
    st = ParquetStore(staging)
    keep = {d[k] for d in spec.get("derived") or [] for k in ("base", "fault")}
    frames: dict = {}
    labels, onsets, notes, warns = {}, {}, [], []
    for dup, canon in duplicates.items():
        notes.append(f"{dup} skipped: byte-identical to {canon} in the archive (one run, not two)")
    rows = 0
    for i, run in enumerate(runs, 1):
        if progress:
            progress(f"{entry.id}: run {i}/{len(runs)} {run['id']}")
        path = paths[(run["file"], run["member"])]
        frame, qn, w = read_wide_run(path, mapping, spec, run_id=run["id"])
        notes += [n for n in qn if n not in notes]
        warns += w
        eq = _equip_id(run)
        rows += st.write_role_frame(frame, facility_id=fid, equip=eq, equip_class=run["class"])
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
        "quirks": notes,
        "runs": len(runs),
        "duplicates": duplicates,
    }
    return {fid: (entry.title, rows, len(labels) + len(onsets), extra)}, mapping_text, warns


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


def _ingest_bdg2(entry, subset, inputs, root, staging, progress) -> tuple:
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
) -> IngestResult:
    """Ingest a fetched dataset into ``store`` (path or :class:`ParquetStore`); see the module."""
    st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
    sname = subset or "default"
    entry.subset(sname)  # KeyError on an unknown subset, before any work
    root = _paths.data_dir(data_dir)
    inputs = verified_inputs(entry, sname, root)
    shas = {k: v[1] for k, v in inputs.items()}
    mapping_text = (
        package_text("mappings", entry.ingest["mapping"]) if entry.ingest.get("mapping") else ""
    )
    chash = content_hash(entry, sname, shas, mapping_text)
    existing = _existing_hashes(st, entry.id)
    result = IngestResult(dataset_id=entry.id, store=os.path.abspath(st.root), subset=sname)
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
            entry, sname, inputs, root, staging, progress
        )
        base = _base_meta(entry, root, sname, shas, chash)
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
