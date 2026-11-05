"""Provenance in the stored data: a catalog dataset's id, licence and research-only flag in every
Parquet part file's key-value metadata (0.112, #319).

``camber datasets ingest`` writes :data:`KEY` into each part file it stages
(:func:`value_for_entry`), and every store rewrite keeps it: an append keeps the parts it does not
touch, ``store normalize`` and ``migrate-partitions`` keep the key through
:func:`camber.store.parquet_store._canonicalize`, a retention rollup records the merged value of
the parts it was built from (:func:`merge`), and a forced re-ingest's swap, an archive bundle and
its restore move or copy the files byte for byte. A user's own ingest writes no key.

The capability registry (and so the MCP server, and the A2A server after it) runs its handlers in
:func:`serving`. Inside it a store read refuses any part file marked research-only, whatever
facility id, directory or store it sits in (:func:`check`), and
:func:`camber._servable.refusal` refuses a facility holding one before the call runs
(:func:`facility_marks`, memoized per partition until a file changes). Outside it -- ``camber
run``, ``camber report``, the lab -- the data reads as before, with the licence banners.

Private module -- not public API.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import functools
import json
import os
import stat
import threading
from collections import OrderedDict

#: the part file key-value metadata key: a JSON object ``{"dataset_id", "licence",
#: "research_only"}``
KEY = b"camber.provenance"

_SERVING: contextvars.ContextVar[bool] = contextvars.ContextVar("camber_serving", default=False)


@contextlib.contextmanager
def serving():
    """A scope in which a store read refuses research-only part files (the registry's)."""
    token = _SERVING.set(True)
    try:
        yield
    finally:
        _SERVING.reset(token)


def is_serving() -> bool:
    return _SERVING.get()


def encode(dataset_id: str, licence: str, research_only: bool) -> bytes:
    """The :data:`KEY` value for a dataset (compact, sorted JSON)."""
    doc = {
        "dataset_id": str(dataset_id),
        "licence": str(licence or ""),
        "research_only": bool(research_only),
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")


def value_for_entry(entry) -> bytes:
    """The :data:`KEY` value for a catalog entry: research-only when the catalog says so or its
    licence is non-commercial / no-derivatives."""
    from ..datasets._catalog import is_research_only_licence

    licence = str(entry.licence or "")
    return encode(entry.id, licence, bool(entry.research_only) or is_research_only_licence(licence))


#: the longest :data:`KEY` value read (bytes): a real one is about a hundred; anything longer
#: is not parsed and counts as unreadable (#329: a deeply nested value would exhaust the stack)
_MAX_VALUE = 4096


#: the type each field of a :data:`KEY` value must have, as :func:`encode` writes it
_FIELDS = {"dataset_id": str, "licence": str, "research_only": bool}


def decode(value) -> dict | None:
    """The :data:`KEY` value as a dict, ``None`` when it cannot be read: not a JSON object, too
    long or too deeply nested to parse (#329), or a field of the wrong type -- a ``dataset_id``
    that is not a non-empty string, a ``licence`` that is not a string, a ``research_only`` that
    is not a boolean, or one of them missing (0.112, #337). The one rule for every reader of a
    mark: the up-front check, the read-time check and ``camber datasets provenance`` all count
    such a value as unreadable, so research-only (fail closed)."""
    try:
        raw = bytes(value)
        if len(raw) > _MAX_VALUE:
            return None
        doc = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    if any(type(doc.get(k)) is not t for k, t in _FIELDS.items()) or not doc["dataset_id"]:
        return None
    return doc


@functools.lru_cache(maxsize=256)
def research_only(value: bytes) -> bool:
    """Whether a :data:`KEY` value marks research-only data: its flag (anything but ``false``
    counts), a non-commercial / no-derivatives licence, or the catalog's current entry for its
    dataset id (terms changed since the ingest). A value that cannot be read counts (fail
    closed)."""
    doc = decode(value)
    if doc is None or doc["research_only"] is not False:
        return True
    from ..datasets._catalog import is_research_only_licence

    if is_research_only_licence(doc["licence"]):
        return True
    try:
        from ..datasets import get

        entry = get(doc["dataset_id"])
    except Exception:  # noqa: BLE001 - an unknown / retired id: the recorded value decides
        return False
    return bool(entry.research_only) or is_research_only_licence(str(entry.licence or ""))


def dataset_of(value) -> str:
    """The dataset id a :data:`KEY` value names (``""`` when none can be read)."""
    doc = decode(value)
    return doc["dataset_id"] if doc is not None else ""


def merge(values):
    """One :data:`KEY` value for a part built from parts holding ``values`` (``None``: unmarked):
    ``None`` when none is marked, else the first research-only value, else the first value."""
    marked = [v for v in values if v is not None]
    if not marked:
        return None
    return next((v for v in marked if research_only(v)), marked[0])


def of_metadata(md) -> bytes | None:
    """The :data:`KEY` value of a ``pyarrow.parquet.FileMetaData`` (``None``: unmarked)."""
    kv = md.metadata if md is not None else None
    return (kv or {}).get(KEY)


def regular(path: str) -> bool:
    """Whether ``path`` is a regular file (a link to one counts): a FIFO or a device named like
    a part would block or misbehave when opened, so nothing here opens one (#314)."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


def of_file(path: str) -> bytes | None:
    """The :data:`KEY` value of one part file, from its footer only; ``OSError`` for anything
    but a regular file, which is never opened."""
    import pyarrow.parquet as pq

    if not regular(path):
        raise OSError("not a regular file")
    return of_metadata(pq.read_metadata(path))


def of_files(paths) -> list:
    """:func:`of_file` of each of ``paths``; a file that cannot be read is left out."""
    out = []
    for p in paths:
        try:
            out.append(of_file(p))
        except Exception:  # noqa: BLE001 - missing or damaged: nothing to carry over
            continue
    return out


def _refuse(facility_id, value: bytes):
    """The refusal of a read that met a research-only part: ``refused_licence`` naming the
    facility the read was scoped to (``None``: a store-wide read, which names none) and the
    dataset, never a path. For :data:`UNCHECKED` it says that not every file could be checked
    (#334), with ``"reason": "licence_unverified"``."""
    from ..capabilities import CapabilityError

    if value == UNCHECKED:
        who = f"facility {facility_id!r}" if facility_id is not None else "the store"
        return CapabilityError(
            "refused_licence",
            f"{who} cannot be served: {UNCHECKED_REASON}; it is not served until that is repaired",
            data={"facility_id": facility_id, "reason": "licence_unverified"},
        )
    did = dataset_of(value)
    of = f" of dataset {did!r}" if did else ""
    where = f"facility {facility_id!r} holds" if facility_id is not None else "the store holds"
    return CapabilityError(
        "refused_licence",
        f"{where} part files marked as research-only (non-commercial / no-derivatives) "
        f"data{of}, which is never served here",
        data={"facility_id": facility_id} if facility_id is not None else None,
    )


def check(values, facility_id=None) -> None:
    """Inside :func:`serving`, refuse (``CapabilityError("refused_licence")``) when any of
    ``values`` is research-only; a no-op outside it."""
    if not _SERVING.get():
        return
    for v in values:
        if v is not None and research_only(v):
            raise _refuse(facility_id, v)


# -------------------------------------------------------------------- per-facility marks

#: (store root, facility id) -> (partition signature, {value or None: part files},
#: {path: (size, mtime_ns, value)}): a changed partition re-reads only its changed files
_MARKS: OrderedDict = OrderedDict()
_MARKS_MAX = 256
_MARKS_LOCK = threading.Lock()
_MAX_DEPTH = 6  # facility_id=/year=/month=/part, with room for a stray directory level
#: what :func:`facility_marks` records (with the number of such entries) for a facility whose
#: walk could not check every file a read could open: a directory nested deeper than
#: :data:`_MAX_DEPTH`, a symlink loop, or an entry it cannot read or resolve -- a dangling
#: symlink, an unreadable directory, a failed ``stat`` (#329, #334). Not a JSON object, so it
#: counts as research-only wherever a mark is weighed (fail closed); the refusal says why
UNCHECKED = b"unchecked: not every file of the facility could be checked"
#: the refusal's reason for :data:`UNCHECKED` (#334: never "marked as research-only")
UNCHECKED_REASON = (
    "not all of its files could be checked for a research-only provenance mark (a dangling "
    "symlink, an unreadable directory, a symlink loop or files nested too deep); `camber store "
    "verify` lists what could not be checked"
)


def is_part_name(name: str) -> bool:
    """Whether a store read opens a file (or enters a directory) of this name: pyarrow's dataset
    discovery opens every file whose name does not start with ``_`` or ``.``, whatever its suffix
    (``x.parq``, ``x.PARQUET``, ``x``, ``x.parquet.tmp``). The one definition of a part file that
    the provenance check and the store readers share (#329)."""
    return not name.startswith(("_", "."))


def scan(fdir: str) -> tuple:
    """``([(path, size, mtime_ns)], [(path, why)])``: every regular file a dataset read of
    ``fdir`` could open (:func:`is_part_name`, any suffix, any level, a link followed) down to
    :data:`_MAX_DEPTH`, and every entry the walk could not check (a directory deeper than that,
    or one it cannot list; a dangling link, a symlink loop or any entry it cannot ``stat``); an
    entry removed between the listing and its ``stat`` (a re-ingest) is skipped. The walk never
    stops early: it skips what it cannot use and goes on (#334). Shared by the
    provenance check and ``camber store verify``; a FIFO or device is neither read nor listed
    (#314). ``OSError`` when ``fdir`` itself cannot be listed: then every read of the facility
    fails (``unavailable``, #301), so there is nothing to check."""
    out: list = []
    bad: list = []

    def walk(path: str, depth: int, up: frozenset) -> None:
        try:
            it = os.scandir(path)
        except FileNotFoundError:  # removed since it was listed (a re-ingest): nothing to read
            return
        except OSError:
            bad.append((path, "the directory cannot be listed"))
            return
        with it:
            while True:
                try:
                    e = next(it)
                except StopIteration:
                    break
                except OSError:
                    bad.append((path, "the directory cannot be listed"))
                    break
                if not is_part_name(e.name):
                    continue
                try:
                    st = os.stat(e.path)  # follows a link: a dangling one or a loop fails
                except FileNotFoundError:
                    # gone since it was listed (a re-ingest swapped it out): no read can open it;
                    # a dangling link is still there to lstat, and is not checked
                    if not os.path.lexists(e.path):
                        continue
                    bad.append((e.path, "cannot be resolved (a dangling symlink or a loop?)"))
                    continue
                except (OSError, ValueError) as err:
                    denied = getattr(err, "errno", None) in (errno.EACCES, errno.EPERM)
                    bad.append(
                        (
                            e.path,
                            "cannot be read (permission denied)"
                            if denied
                            else "cannot be resolved (a dangling symlink or a loop?)",
                        )
                    )
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if (st.st_dev, st.st_ino) in up:  # a link back to a directory above it
                        bad.append((e.path, "a symlink loop"))
                    elif depth:
                        walk(e.path, depth - 1, up | {(st.st_dev, st.st_ino)})
                    else:
                        bad.append((e.path, "nested too deep"))
                elif stat.S_ISREG(st.st_mode):  # a FIFO would block its footer read (#314)
                    out.append((e.path, st.st_size, st.st_mtime_ns))

    top = os.stat(fdir)
    os.scandir(fdir).close()  # a facility directory that cannot be listed at all: OSError
    walk(fdir, _MAX_DEPTH, frozenset({(top.st_dev, top.st_ino)}))
    return sorted(out), sorted(bad)


def facility_marks(root: str, facility_id: str) -> dict:
    """``{value or None: part files}`` of a facility's partition in the store at ``root``, read
    from the footers (key-value metadata only) and memoized until the partition changes (its
    stat signature); then only the files that changed are read again. Every file a store read
    could open counts, whatever its name (:func:`is_part_name`, #329). A file whose footer cannot
    be read is left out: its read fails on its own. Every entry the walk could not check
    (:func:`scan`: nested too deep, unreadable, unresolvable) counts as one :data:`UNCHECKED`
    value (refused, #334)."""
    from .parquet_store import _partition_signature

    fdir = os.path.join(os.path.abspath(root), f"facility_id={facility_id}")
    if not os.path.isdir(fdir):
        return {}
    key = (os.path.abspath(root), str(facility_id))
    sig = _partition_signature(fdir)  # before the reads: a write during them voids the entry
    with _MARKS_LOCK:
        hit = _MARKS.get(key)
        if hit is not None and sig and hit[0] == sig:
            _MARKS.move_to_end(key)
            return dict(hit[1])
    known = hit[2] if hit is not None else {}
    counts: dict = {}
    files: dict = {}
    try:
        found, unchecked = scan(fdir)
    except OSError:  # the facility directory cannot be listed: its reads fail on their own
        found, unchecked = [], []
    if unchecked:
        counts[UNCHECKED] = len(unchecked)
    for path, size, mtime in found:
        prev = known.get(path)
        if prev is not None and prev[:2] == (size, mtime):
            v = prev[2]
        else:
            try:
                v = of_file(path)
            except Exception:  # noqa: BLE001 - damaged: the read reports it
                continue
        files[path] = (size, mtime, v)
        counts[v] = counts.get(v, 0) + 1
    # with no signature (a partition that changed mid-walk) the entry never matches, but the
    # next walk still reuses its per-file footers
    remember(root, facility_id, sig, counts, files)
    return dict(counts)


def remember(root: str, facility_id: str, sig, counts: dict, files=None) -> None:
    """Record a facility's marks (from :func:`facility_marks`)."""
    key = (os.path.abspath(root), str(facility_id))
    with _MARKS_LOCK:
        if files is None:  # keep what is known per file for the next change
            old = _MARKS.get(key)
            files = old[2] if old is not None else {}
        _MARKS[key] = (sig, dict(counts), files)
        _MARKS.move_to_end(key)
        while len(_MARKS) > _MARKS_MAX:
            _MARKS.popitem(last=False)


def research_only_value(counts: dict):
    """The first research-only value among ``counts`` other than :data:`UNCHECKED` (``None``
    when there is none)."""
    return next((v for v in counts if v is not None and v != UNCHECKED and research_only(v)), None)
