"""The portfolio's append-only audit log (``<root>/_audit.ndjson``): one JSON object per line.

Every lifecycle action appends exactly one line -- ``ts`` (UTC), ``actor`` (the OS user),
``host``, ``action``, ``facility_id``, ``from_state``, ``to_state``, ``reason``, ``details`` --
and ``fsync`` s it before returning, so an acknowledged action survives a power cut. CAMBER never
rewrites or truncates the file (retention treats the audit class as *never deleted*). Until CAMBER
has authentication the actor is whoever owns the process -- see docs/SECURITY.md.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import socket

AUDIT_FILE = "_audit.ndjson"


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no login name (some containers)
        return f"uid:{os.getuid()}" if hasattr(os, "getuid") else "unknown"


def audit_record(
    action: str,
    *,
    facility_id=None,
    from_state=None,
    to_state=None,
    reason=None,
    details=None,
) -> dict:
    """One audit line as a dict (``ts``/``actor``/``host`` filled in from the environment)."""
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")
    return {
        "ts": ts.replace("+00:00", "Z"),
        "actor": _actor(),
        "host": socket.gethostname(),
        "action": action,
        "facility_id": facility_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "details": dict(details or {}),
    }


def append_audit(root: str, record: dict) -> dict:
    """Append ``record`` as one line to ``<root>/_audit.ndjson`` and fsync it; returns it."""
    line = (json.dumps(record, sort_keys=True, default=str) + "\n").encode("utf-8")
    fd = os.open(os.path.join(root, AUDIT_FILE), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def read_audit(root: str, *, facility_id=None) -> list:
    """Every parseable audit line (oldest first), optionally for one facility.

    A torn final line (a crash mid-write before the fsync returned) is skipped, not fatal.
    """
    path = os.path.join(root, AUDIT_FILE)
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if facility_id is None or rec.get("facility_id") == facility_id:
                out.append(rec)
    return out
