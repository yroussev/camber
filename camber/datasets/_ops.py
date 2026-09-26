"""Catalog operations behind the public :mod:`camber.datasets` API and the ``camber datasets`` CLI.

Fetch (with the research-only acknowledgement gate), status, remove, run-config templates and
label scoring. Downloads go through :func:`._fetch.download` (HTTPS-only, resumable, verified);
ingest is :mod:`._ingest`.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .. import __version__
from ..store import ParquetStore
from . import _paths
from ._catalog import DatasetEntry, package_text
from ._fetch import check_disk, download, human_bytes, sha256_file
from ._ingest import dataset_meta


@dataclass
class FetchResult:
    """What a fetch downloaded (or found already verified) and where."""

    dataset_id: str
    subset: str
    data_dir: str
    files: list = field(default_factory=list)
    downloaded_bytes: int = 0
    acknowledged: bool = False
    citation: str = ""
    licence: str = ""
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """A JSON-friendly dict."""
        return asdict(self)


def _dest(root: str, entry: DatasetEntry, name: str) -> str:
    return os.path.join(_paths.downloads_dir(root, entry.id), *name.split("/"))


def _pending_bytes(root: str, entry: DatasetEntry, files: list) -> int:
    total = 0
    for f in files:
        dest = _dest(root, entry, f["name"])
        size = int(f.get("size") or 0)
        have = os.path.getsize(dest) if os.path.isfile(dest) else 0
        part = dest + ".part"
        have = max(have, os.path.getsize(part) if os.path.isfile(part) else 0)
        if not (os.path.isfile(dest) and have == size):
            total += max(0, size - have)
    return total


def fetch_dataset(
    entry: DatasetEntry,
    *,
    subset: str | None = None,
    data_dir=None,
    accept_noncommercial: bool = False,
    progress: Callable[[str, int, int | None], None] | None = None,
    opener=None,
    timeout: float = 60.0,
) -> FetchResult:
    """Download and verify a subset of ``entry``'s files into the cache.

    A research-only (NC/ND) entry raises ``PermissionError`` unless ``accept_noncommercial`` is
    true; an acceptance is appended to the ``acknowledgements.json`` ledger and recorded in the
    manifest. There is deliberately no environment-variable bypass. ``progress(name, done, total)``
    reports each file's bytes.
    """
    sname = subset or "default"
    files = entry.subset_files(sname)
    if entry.research_only and not accept_noncommercial:
        raise PermissionError(
            f"{entry.id} is licensed {entry.licence}: research / non-commercial use only, and it "
            "may not be redistributed. Pass accept_noncommercial=True "
            "(CLI: --accept-noncommercial) to acknowledge the licence and download it."
        )
    root = _paths.data_dir(data_dir)
    check_disk(_paths.downloads_dir(root, entry.id), _pending_bytes(root, entry, files))
    res = FetchResult(
        dataset_id=entry.id,
        subset=sname,
        data_dir=root,
        citation=entry.citation,
        licence=entry.licence,
    )
    manifest = _paths.read_manifest(root)
    rec = manifest.get(entry.id) or {}
    recorded = dict(rec.get("files") or {})
    for f in files:
        dest = _dest(root, entry, f["name"])

        def cb(done, total, _name=f["name"]):
            if progress is not None:
                progress(_name, done, total)

        pinned = bool(f.get("sha256"))
        out = download(
            f["url"],
            dest,
            size=f.get("size") if pinned else None,
            sha256=f.get("sha256"),
            opener=opener,
            progress=cb,
            timeout=timeout,
        )
        if not pinned:
            res.warnings.append(
                f"{f['name']} is not pinned in the catalog: its sha256 ({out.sha256}) was recorded "
                "but not verified against a known value"
            )
        if not out.skipped:
            res.downloaded_bytes += out.bytes
        res.files.append(
            {
                "name": f["name"],
                "path": dest,
                "bytes": out.bytes,
                "sha256": out.sha256,
                "skipped": out.skipped,
                "resumed": out.resumed,
            }
        )
        recorded[f["name"]] = {"sha256": out.sha256, "bytes": out.bytes, "etag": out.etag}
    rec.update(
        {
            "licence": entry.licence,
            "access": entry.access,
            "fetched_at": _paths.utc_now(),
            "files": recorded,
            "subsets": sorted(set(rec.get("subsets") or []) | {sname}),
            "camber_version": __version__,
        }
    )
    if entry.research_only:
        ack = _paths.append_acknowledgement(
            root,
            {
                "dataset_id": entry.id,
                "licence": entry.licence,
                "subset": sname,
                "camber_version": __version__,
                "statement": "accepted: research / non-commercial use only; no redistribution",
            },
        )
        rec["acknowledged_at"] = ack["accepted_at"]
        res.acknowledged = True
    manifest[entry.id] = rec
    _paths.write_manifest(root, manifest)
    return res


def _dir_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, fnames in os.walk(path):
        for fn in fnames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:  # pragma: no cover - raced deletion
                pass
    return total


def dataset_status(entries, *, data_dir=None, store=None) -> list:
    """One status dict per entry: what is fetched (per subset), bytes on disk, what is ingested."""
    root = _paths.data_dir(data_dir)
    manifest = _paths.read_manifest(root)
    meta = {}
    if store is not None:
        st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
        meta = st.facilities_meta()
    out = []
    for e in entries:
        fetched = {}
        for sname in e.subsets:
            names = [f["name"] for f in e.subset_files(sname)]
            fetched[sname] = all(os.path.isfile(_dest(root, e, n)) for n in names)
        ingested = {
            fid: {
                k: dataset_meta(m).get(k)
                for k in ("subset", "rows", "equipment", "ingested_at", "content_hash")
            }
            for fid, m in sorted(meta.items())
            if dataset_meta(m).get("dataset_id") == e.id
        }
        out.append(
            {
                "id": e.id,
                "title": e.title,
                "licence": e.licence,
                "access": e.access,
                "fetched": fetched,
                "fetched_at": (manifest.get(e.id) or {}).get("fetched_at"),
                "bytes_on_disk": _dir_bytes(_paths.dataset_dir(root, e.id)),
                "ingested": ingested,
            }
        )
    return out


def remove_dataset(
    entry: DatasetEntry, *, data_dir=None, store=None, purge_store: bool = False
) -> dict:
    """Delete a dataset's downloads + extractions (and, with ``purge_store``, its facilities).

    The acknowledgement ledger is an audit trail and is never trimmed.
    """
    if purge_store and store is None:
        raise ValueError("purge_store needs the store to purge")
    root = _paths.data_dir(data_dir)
    ddir = _paths.dataset_dir(root, entry.id)
    freed = _dir_bytes(ddir)
    if os.path.isdir(ddir):
        shutil.rmtree(ddir)
    manifest = _paths.read_manifest(root)
    if manifest.pop(entry.id, None) is not None:
        _paths.write_manifest(root, manifest)
    dropped = []
    if purge_store:
        st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
        for fid, m in sorted(st.facilities_meta().items()):
            if dataset_meta(m).get("dataset_id") == entry.id:
                st.drop_facility(fid, forget=True)
                dropped.append(fid)
    return {"dataset_id": entry.id, "freed_bytes": freed, "facilities_dropped": dropped}


def _ingested(store, dataset_id: str) -> list:
    st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
    return sorted(
        fid
        for fid, m in st.facilities_meta().items()
        if dataset_meta(m).get("dataset_id") == dataset_id
    )


def build_config(
    entry: DatasetEntry, store, *, facility_id: str | None = None, out: str | None = None
) -> dict:
    """The entry's run-config template, pointed at ``store`` (and written to ``out`` if given).

    The facility defaults to the entry's; for a multi-facility dataset (BDG2: one per site) it
    defaults to the first one ingested into ``store``.
    """
    tmpl = (entry.suggested_analyses or {}).get("config_template")
    if not tmpl:
        raise KeyError(f"{entry.id} has no config template")
    cfg = json.loads(package_text("configs", tmpl))
    root = os.path.abspath(os.fspath(store.root if isinstance(store, ParquetStore) else store))
    fid = facility_id
    if fid is None:
        have = _ingested(root, entry.id) if os.path.isdir(root) else []
        default = entry.ingest.get("facility")
        fid = default if default in have or not have else have[0]
    cfg["source"] = {**cfg.get("source", {}), "kind": "store", "store": root, "facility_id": fid}
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
    return cfg


def _load_findings(findings) -> list:
    if isinstance(findings, (str, os.PathLike)):
        with open(findings, encoding="utf-8") as fh:
            return json.load(fh)
    return list(findings)


def score_dataset(
    entry: DatasetEntry, store, *, findings=None, rules=None, facility_id: str | None = None
) -> dict:
    """Score findings against the labels ingested for ``entry`` (see :mod:`._labels`).

    ``findings`` is a list of Findings / dicts or a ``findings.json`` path; when omitted the entry's
    config template is run against ``store`` first.
    """
    from ._labels import records_from_findings, score_records

    if not entry.labeled_faults:
        raise ValueError(f"{entry.id} has no fault labels to score against")
    st = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
    fid = facility_id or entry.ingest.get("facility")
    meta = dataset_meta(st.facilities_meta().get(fid) or {})
    labels = meta.get("labels")
    if not labels:
        raise ValueError(f"{entry.id}: facility {fid!r} is not ingested in {st.root}")
    if findings is None:
        from ..config import run_config

        cfg = build_config(entry, st, facility_id=fid)
        found = run_config(cfg, base_dir=os.getcwd()).findings
    else:
        found = _load_findings(findings)
    targets = dict((entry.labels or {}).get("targets") or {})
    suite = list(rules) if rules else (sorted(targets) if targets else None)
    records = records_from_findings(found, labels, suite)
    scores = score_records(records, targets)
    scores.update(
        {
            "dataset_id": entry.id,
            "facility_id": fid,
            "subset": meta.get("subset"),
            "suite": suite if suite is not None else "all rules in the findings",
            "targets": targets,
            "records": records,
        }
    )
    return scores


__all__ = [
    "FetchResult",
    "fetch_dataset",
    "dataset_status",
    "remove_dataset",
    "build_config",
    "score_dataset",
    "human_bytes",
    "sha256_file",
]
