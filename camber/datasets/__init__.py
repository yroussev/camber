"""Open building datasets: fetch, verify, ingest and analyse them with CAMBER (provisional API).

A reviewed **catalog** (``catalog.json``, package data) lists open-licensed building datasets --
simulated fault-labelled HVAC runs, real whole-building meters -- with their publisher, citation,
SPDX licence, pinned download size and sha256, and an ingest spec. CAMBER redistributes none of
the data: :func:`fetch` downloads each file from its publisher over HTTPS into a local cache
(``$CAMBER_DATA_DIR``, else ``$XDG_CACHE_HOME/camber/datasets``, else
``~/.cache/camber/datasets``), verifies it, and :func:`ingest` normalizes it into a
:class:`~camber.store.ParquetStore` so ``camber run`` / ``report`` / ``drift`` work on it through
a ``source.kind: "store"`` config (:func:`config_template` writes one).

::

    from camber import datasets

    datasets.fetch("lbnl-sdahu")
    datasets.ingest("lbnl-sdahu", "lab_store")
    datasets.config_template("lbnl-sdahu", "lab_store", out="sdahu.json")
    print(datasets.score("lbnl-sdahu", "lab_store")["overall"])

Synthetic entries (``kind == "synthetic"``, 0.103) are the exception to "downloaded from its
publisher": :func:`fetch` runs one of CAMBER's own generators instead, deterministically, and the
data carries CAMBER's licence (see :mod:`camber.datasets._synthetic`).

Licences: entries whose licence forbids commercial use or derivatives (NC / ND) are
``access == "research_only"``; :func:`fetch` refuses them unless ``accept_noncommercial=True`` and
records the acceptance, and every report built from them carries a do-not-redistribute banner.

This API is **provisional** (see docs/API-STABILITY.md): names may still change in a minor
release while the catalog grows.
"""

from __future__ import annotations

from functools import lru_cache

from . import _licence, _paths
from ._catalog import DatasetEntry, load_entries
from ._ingest import IngestResult, ingest_dataset
from ._ops import (
    FetchResult,
    adopt_local_files,
    build_config,
    dataset_status,
    fetch_dataset,
    remove_dataset,
    score_dataset,
)

__all__ = [
    "DatasetEntry",
    "FetchResult",
    "IngestResult",
    "catalog",
    "get",
    "fetch",
    "ingest",
    "status",
    "remove",
    "config_template",
    "score",
]


@lru_cache(maxsize=1)
def _entries() -> tuple:
    return tuple(load_entries())


def catalog(*, licence: str = "all", kind: str | None = None, labeled: bool | None = None) -> list:
    """Catalog entries, optionally filtered.

    ``licence="commercial"`` keeps only the open tier (entries CAMBER lets you use commercially:
    the licence allows it and no ``access_reason`` holds the entry research-only); ``"all"``
    (default) includes research-only entries. ``kind`` is ``"simulated"``,
    ``"real"``, ``"lab"`` or ``"synthetic"`` (0.103, #133: generated locally by CAMBER, never
    downloaded); ``labeled=True`` keeps entries with ground-truth fault labels.
    """
    if licence not in ("all", "commercial"):
        raise ValueError("licence must be 'all' or 'commercial'")
    out = []
    for e in _entries():
        if licence == "commercial" and (e.research_only or not e.commercial_ok):
            continue
        if kind is not None and e.kind != kind:
            continue
        if labeled is not None and e.labeled_faults != labeled:
            continue
        out.append(e)
    return out


def get(dataset_id: str) -> DatasetEntry:
    """The catalog entry ``dataset_id`` (``KeyError`` listing the known ids)."""
    for e in _entries():
        if e.id == dataset_id:
            return e
    known = ", ".join(e.id for e in _entries())
    raise KeyError(f"unknown dataset {dataset_id!r} (known: {known})")


def fetch(
    dataset_id: str,
    *,
    subset: str | None = None,
    data_dir=None,
    accept_noncommercial: bool = False,
    progress=None,
    opener=None,
) -> FetchResult:
    """Download and verify a dataset subset (default ``"default"``) into the local cache.

    Resumes partial downloads, verifies size + sha256 (a mismatch is kept as ``<file>.bad`` and
    raised, never accepted) and refuses a research-only dataset without ``accept_noncommercial``
    (``PermissionError``). ``progress(name, done_bytes, total_bytes)``; ``opener`` replaces the
    HTTPS opener (tests). See :mod:`camber.datasets._fetch`.
    """
    return fetch_dataset(
        get(dataset_id),
        subset=subset,
        data_dir=data_dir,
        accept_noncommercial=accept_noncommercial,
        progress=progress,
        opener=opener,
    )


def ingest(
    dataset_id: str,
    store,
    *,
    subset: str | None = None,
    data_dir=None,
    force: bool = False,
    progress=None,
    corrections: bool = True,
    accept_noncommercial: bool = False,
    from_dir=None,
) -> IngestResult:
    """Normalize a fetched dataset subset into ``store`` (path or ParquetStore).

    Idempotent: identical inputs are skipped unless ``force``; a changed subset/spec replaces the
    dataset's facilities atomically. ``corrections=False`` skips the entry's ``fix`` quirks and
    ingests the data exactly as published (recorded in the provenance; the entry's data issues say
    what each fix corrects). A research-only dataset needs its licence acknowledged -- by the
    ``fetch`` that downloaded it, or ``accept_noncommercial=True`` here -- else ``PermissionError``;
    its facilities record ``redistribution: "prohibited"``.

    ``from_dir`` takes the files from a local directory instead of a prior :func:`fetch` -- the way
    in for ``manual`` entries (downloaded by hand from a portal with terms): pinned files are
    verified (size + sha256) and placed in the cache first. See :mod:`camber.datasets._ingest`.
    """
    entry = get(dataset_id)
    if from_dir is not None:
        root = _paths.data_dir(data_dir)
        if (
            entry.research_only
            and not accept_noncommercial
            and not _licence.acknowledged(root, entry)
        ):
            raise _licence.refusal(entry, "ingest")  # before any file is placed in the cache
        adopt_local_files(entry, from_dir, subset=subset, data_dir=data_dir)
    return ingest_dataset(
        entry,
        store,
        subset=subset,
        data_dir=data_dir,
        force=force,
        progress=progress,
        corrections=corrections,
        accept_noncommercial=accept_noncommercial,
        via="ingest --from-dir" if from_dir is not None else "ingest",
    )


def status(*, data_dir=None, store=None) -> list:
    """Per-entry status: fetched subsets, bytes on disk, and (with ``store``) what is ingested."""
    return dataset_status(_entries(), data_dir=data_dir, store=store)


def remove(dataset_id: str, *, data_dir=None, store=None, purge_store: bool = False) -> dict:
    """Delete a dataset's local files (and, with ``purge_store``, its facilities in ``store``)."""
    return remove_dataset(get(dataset_id), data_dir=data_dir, store=store, purge_store=purge_store)


def config_template(
    dataset_id: str,
    store,
    *,
    facility_id: str | None = None,
    out: str | None = None,
    exercise: str | None = None,
    format: str = "json",
) -> dict:
    """A ready-to-run ``source.kind: "store"`` config for the dataset (written to ``out``).

    ``exercise`` (0.97) takes a workbook exercise's tuned template instead of the dataset's own
    (``camber datasets config <id> --exercise <exercise-id>``; see docs/workbook/index.md).
    ``format`` (0.98, #90) is ``"json"`` or ``"yaml"`` for the file written to ``out``; YAML
    turns the template's ``_comment`` notes into comments and needs no extra to write.
    """
    return build_config(
        get(dataset_id), store, facility_id=facility_id, out=out, exercise=exercise, format=format
    )


def score(
    dataset_id: str, store, *, findings=None, rules=None, facility_id: str | None = None
) -> dict:
    """Score findings against the dataset's ingested fault labels (TPR / FPR / accuracy + CIs).

    ``findings`` (Findings, dicts, or a ``findings.json`` path) default to running the dataset's
    config template against ``store``. Uses :func:`camber.validation.metrics_with_ci`.
    """
    return score_dataset(
        get(dataset_id), store, findings=findings, rules=rules, facility_id=facility_id
    )
