"""Where the dataset catalog keeps its downloads, extractions, manifest and licence ledger.

Resolution order for the cache root (first hit wins):

1. an explicit ``data_dir=`` argument (``--dir`` on the CLI),
2. ``$CAMBER_DATA_DIR``,
3. ``$XDG_CACHE_HOME/camber/datasets``,
4. ``~/.cache/camber/datasets``.

Layout under the root::

    <root>/<dataset_id>/downloads/<file>          # verified archives / files (+ .part while busy)
    <root>/<dataset_id>/extracted/<member path>   # selectively extracted archive members
    <root>/manifest.json                          # what was fetched, when, with which sha256
    <root>/acknowledgements.json                  # research-only licence acceptances (append-only)

Everything here is plain stdlib; JSON writes are atomic (tmp + ``os.replace``).
"""

from __future__ import annotations

import datetime as _dt
import json
import os

MANIFEST = "manifest.json"
ACKS = "acknowledgements.json"


def data_dir(override: str | os.PathLike | None = None) -> str:
    """The cache root for downloaded datasets (see the module docstring for the order)."""
    if override:
        return os.path.abspath(os.fspath(override))
    env = os.environ.get("CAMBER_DATA_DIR")
    if env:
        return os.path.abspath(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "camber", "datasets")


def dataset_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>``."""
    return os.path.join(root, dataset_id)


def downloads_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>/downloads`` -- verified files and in-flight ``.part`` files."""
    return os.path.join(root, dataset_id, "downloads")


def extracted_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>/extracted`` -- archive members extracted for ingest."""
    return os.path.join(root, dataset_id, "extracted")


def utc_now() -> str:
    """The current UTC time as an ISO-8601 string (seconds precision)."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: str, default):
    """Parse a JSON file, returning ``default`` when it is absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: str, data) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tmp file + ``os.replace``)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_manifest(root: str) -> dict:
    """The fetch manifest ``{dataset_id: {...}}`` (empty when absent)."""
    data = read_json(os.path.join(root, MANIFEST), {})
    return data if isinstance(data, dict) else {}


def write_manifest(root: str, data: dict) -> None:
    """Replace the fetch manifest atomically."""
    write_json_atomic(os.path.join(root, MANIFEST), data)


def read_acknowledgements(root: str) -> list:
    """The research-only licence acceptance ledger (a list of records; empty when absent)."""
    data = read_json(os.path.join(root, ACKS), [])
    return data if isinstance(data, list) else []


def append_acknowledgement(root: str, record: dict) -> dict:
    """Append one acceptance record (stamped with ``accepted_at``) to the ledger; return it."""
    rec = dict(record)
    rec.setdefault("accepted_at", utc_now())
    ledger = read_acknowledgements(root)
    ledger.append(rec)
    write_json_atomic(os.path.join(root, ACKS), ledger)
    return rec
