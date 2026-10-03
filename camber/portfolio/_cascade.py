"""The deleting half of the lifecycle: offboard, archive, restore, purge -- and crash recovery.

Every function here takes a :class:`~camber.portfolio.Portfolio`, plans first (a dry run returns
the plan and changes nothing) and, when applied, runs under the workspace lock, audits with the OS
user and the reason, and orders its steps so a crash at any point leaves a state that
:func:`recover` (run first by every lifecycle and retention command) finishes:

* **offboard** -- export a verified bundle, *then* record ``offboarding`` with the grace deadline.
  A crash in between leaves an extra bundle and an unchanged facility.
* **archive** -- make sure a verified bundle matches the hot data (re-exporting if anything
  changed during the grace period), record ``archived`` with ``hot_deleted: false``, audit, then
  delete the hot data (each tree by one atomic rename, then removal) and set ``hot_deleted``.
  A crash after the state change is finished by :func:`recover`, which re-verifies the bundle
  before deleting anything.
* **restore** -- verify the bundle, swap each tree back in (crash-safe), re-hash, and only then
  return the facility to ``active``. Re-running an interrupted restore finishes it.
* **purge** -- tombstone the id with ``purge_pending``, audit, delete every remaining tree
  (bundles included), then clear ``purge_pending``. :func:`recover` finishes a pending purge.
"""

from __future__ import annotations

import datetime as _dt
import os

from ..store import _swap
from ._bundle import (
    ROLLUPS_DIR,
    archive_dir,
    export_bundle,
    hot_fingerprint,
    list_bundles,
    restore_bundle,
    rollup_roots,
    summarize,
    verify_bundle,
)
from ._state import read_manifest, sha256_file, state_dir
from ._states import transition as _check

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _utc(now=None) -> _dt.datetime:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, str):
        now = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
    return now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)


def _stamp(now) -> str:
    return _utc(now).strftime(_FMT)


def _parse(ts) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        return _utc(str(ts))
    except ValueError:
        return None


def _state_of(pf, facility_id: str) -> str:
    return pf.facility(facility_id).get("state", "active")  # KeyError if unknown


def _held(pf, facility_id: str) -> bool:
    return facility_id in pf.legal_holds()


def _preflight(pf, facility_id: str, action: str) -> tuple:
    cur = _state_of(pf, facility_id)
    new = _check(cur, action, legal_hold=_held(pf, facility_id))
    return cur, new


# The edge landing's quarantine (camber.edge.quarantine.QUARANTINE_DIR; a test pins the two):
# uploads that arrived after the facility stopped accepting data. Archive keeps them (they are
# not in the bundle; restore then release them); purge deletes them with everything else.
QUARANTINE_DIR = "quarantine"


def _quarantine_tree(pf, facility_id: str) -> str:
    return os.path.join(pf.root, QUARANTINE_DIR, f"facility_id={facility_id}")


def _hot_trees(pf, facility_id: str) -> list:
    """The directories archive/purge delete: store and rollup partitions, ``state/<fid>/``."""
    part = f"facility_id={facility_id}"
    trees = [os.path.join(pf.store_root, part)]
    trees += [os.path.join(r, part) for r in rollup_roots(pf.root).values()]
    trees.append(state_dir(pf.root, facility_id))
    return trees


def _deletable_external(pf, facility_id: str, bundle: dict | None) -> tuple:
    """External report artifacts safe to delete (unchanged since recorded and in the bundle).

    Only ``report`` artifacts are deleted: they are per-facility and regenerable. Other external
    kinds (a fault or baseline store at a config-chosen path may be shared) are kept in place and
    listed as ``kept``.
    """
    man = read_manifest(pf.root, facility_id)
    in_bundle = {e.get("path"): e.get("sha256") for e in (bundle or {}).get("external") or []}
    delete, kept = [], []
    for path, e in sorted((man.get("external") or {}).items()):
        if not os.path.isfile(path):
            continue
        kind = (e or {}).get("kind")
        sha = sha256_file(path)
        if kind == "report" and sha == (e or {}).get("sha256") and in_bundle.get(path) == sha:
            delete.append(path)
        else:
            kept.append({"path": path, "kind": kind})
    return delete, kept


def _latest_bundle(pf, facility_id: str, bundle_id=None) -> dict | None:
    bundles = [b for b in list_bundles(pf.root, facility_id) if not b.get("unreadable")]
    if bundle_id:
        hits = [b for b in bundles if b["bundle_id"] == bundle_id]
        if not hits:
            raise KeyError(f"no bundle {bundle_id!r} for {facility_id!r}")
        return hits[0]
    return bundles[-1] if bundles else None


def _read_bundle(b: dict) -> dict:
    from ._bundle import read_bundle_manifest

    return read_bundle_manifest(b["path"])


# --------------------------------------------------------------------------- offboard


def offboard(pf, facility_id: str, *, reason: str, apply: bool = False, now=None) -> dict:
    """Start the reversible offboarding grace period, exporting a bundle first."""
    doc = pf._doc()
    grace = int(doc.get("offboarding_grace_days", 30))
    now = _utc(now)
    until = now + _dt.timedelta(days=grace)
    with pf.lock() if apply else _null():
        if apply:
            recover(pf)
        cur, new = _preflight(pf, facility_id, "offboard")
        plan = {
            "action": "offboard",
            "facility_id": facility_id,
            "from_state": cur,
            "to_state": new,
            "grace_days": grace,
            "grace_until": until.strftime(_FMT),
            "export": summarize(pf, facility_id),
            "deletes": [],
            "dry_run": not apply,
        }
        if not apply:
            return plan
        pf._ensure_registered(facility_id)
        man = export_bundle(pf, facility_id, kind="offboard", reason=reason, now=now)
        pf.registry._update(
            facility_id,
            {
                "state": new,
                "state_changed_at": _stamp(now),
                "offboarding": {
                    "started_at": _stamp(now),
                    "grace_until": plan["grace_until"],
                    "bundle": man["bundle_id"],
                },
            },
        )
        pf._audit(
            "facility.offboard",
            facility_id=facility_id,
            from_state=cur,
            to_state=new,
            reason=reason,
            details={
                "bundle": man["bundle_id"],
                "fingerprint": man["fingerprint"],
                "counts": man["counts"],
                "grace_until": plan["grace_until"],
            },
        )
        return {**plan, "bundle": man["bundle_id"], "counts": man["counts"]}


# --------------------------------------------------------------------------- archive


def grace_until(pf, facility_id: str):
    """The facility's offboarding grace deadline (a ``datetime``) or ``None``."""
    e = pf.registry.get(facility_id)
    return _parse((e.get("offboarding") or {}).get("grace_until"))


def archive(
    pf, facility_id: str, *, reason: str, apply: bool = False, now=None, skip_grace: bool = False
) -> dict:
    """Delete the facility's hot data, keeping a verified bundle (offboarding -> archived)."""
    now = _utc(now)
    with pf.lock() if apply else _null():
        if apply:
            recover(pf)
        cur, new = _preflight(pf, facility_id, "archive")
        until = grace_until(pf, facility_id)
        if until is not None and now < until and not skip_grace:
            from ._states import LifecycleError

            raise LifecycleError(
                f"{facility_id} is in its offboarding grace period until "
                f"{until.strftime(_FMT)}; archive after that, restore it, or pass "
                "--skip-grace (audited) to archive early"
            )
        latest = _latest_bundle(pf, facility_id)
        current = hot_fingerprint(pf, facility_id)
        reuse = latest is not None and latest.get("fingerprint") == current
        delete_ext, kept_ext = _deletable_external(
            pf, facility_id, _read_bundle(latest) if reuse and latest else None
        )
        plan = {
            "action": "archive",
            "facility_id": facility_id,
            "from_state": cur,
            "to_state": new,
            "grace_until": until.strftime(_FMT) if until else None,
            "skip_grace": bool(skip_grace and until is not None and now < until),
            "bundle": latest["bundle_id"] if reuse and latest else None,
            "new_bundle": not reuse,
            "export": summarize(pf, facility_id),
            "deletes": [t for t in _hot_trees(pf, facility_id) if os.path.exists(t)],
            "deletes_external": delete_ext if reuse else "(decided after the new bundle)",
            "kept_external": kept_ext,
            "dry_run": not apply,
        }
        if not apply:
            return plan
        pf._ensure_registered(facility_id)
        if reuse and latest is not None:
            man = _read_bundle(latest)
            check = verify_bundle(latest["path"])
            if not check["ok"]:
                reuse = False
        if not reuse:
            man = export_bundle(pf, facility_id, kind="archive", reason=reason, now=now)
        delete_ext, kept_ext = _deletable_external(pf, facility_id, man)
        pf.registry._update(
            facility_id,
            {
                "state": new,
                "state_changed_at": _stamp(now),
                "archive": {
                    "bundle": man["bundle_id"],
                    "archived_at": _stamp(now),
                    "hot_deleted": False,
                    "external_to_delete": delete_ext,
                },
            },
        )
        pf._audit(
            "facility.archive",
            facility_id=facility_id,
            from_state=cur,
            to_state=new,
            reason=reason,
            details={
                "bundle": man["bundle_id"],
                "fingerprint": man["fingerprint"],
                "counts": man["counts"],
                "skip_grace": plan["skip_grace"],
                "deleted_external": delete_ext,
                "kept_external": kept_ext,
            },
        )
        _delete_hot(pf, facility_id)
        return {
            **plan,
            "bundle": man["bundle_id"],
            "new_bundle": not reuse,
            "deletes_external": delete_ext,
            "kept_external": kept_ext,
        }


def _delete_hot(pf, facility_id: str) -> None:
    """Delete the hot trees + external reports of an archived facility, then mark it done."""
    e = pf.registry.get(facility_id)
    arch = dict(e.get("archive") or {})
    for path in arch.get("external_to_delete") or []:
        if os.path.isfile(path):
            os.remove(path)
    for tree in _hot_trees(pf, facility_id):
        _swap.discard(tree)
    pf.store._invalidate_catalog()
    from ..resolve import clear_store_cache

    clear_store_cache(pf.store_root, facility_id)
    arch["hot_deleted"] = True
    pf.registry._update(facility_id, {"archive": arch})


# --------------------------------------------------------------------------- restore


def restore(pf, facility_id: str, *, reason: str, apply: bool = False, bundle=None, now=None):
    """offboarding -> active (nothing to copy), or archived -> active from a verified bundle."""
    now = _utc(now)
    with pf.lock() if apply else _null():
        if apply:
            recover(pf)
        cur, new = _preflight(pf, facility_id, "restore")
        plan = {
            "action": "restore",
            "facility_id": facility_id,
            "from_state": cur,
            "to_state": new,
            "bundle": None,
            "deletes": [],
            "dry_run": not apply,
        }
        b = None
        if cur == "archived":
            arch = pf.registry.get(facility_id).get("archive") or {}
            b = _latest_bundle(pf, facility_id, bundle or arch.get("bundle"))
            if b is None:
                raise FileNotFoundError(f"{facility_id} is archived but has no bundle to restore")
            plan["bundle"] = b["bundle_id"]
            plan["restores"] = b.get("counts")
        if not apply:
            return plan
        counts = None
        if b is not None:
            counts = restore_bundle(pf, facility_id, b["path"])
        pf.registry._update(
            facility_id,
            {"state": new, "state_changed_at": _stamp(now)},
            drop=("offboarding", "archive"),
        )
        pf._audit(
            "facility.restore",
            facility_id=facility_id,
            from_state=cur,
            to_state=new,
            reason=reason,
            details={"bundle": plan["bundle"], "restored": counts},
        )
        return {**plan, "restored": counts}


# --------------------------------------------------------------------------- purge


def purge(
    pf, facility_id: str, *, reason: str, apply: bool = False, confirm=None, now=None
) -> dict:
    """archived -> purged: delete everything but the tombstone and the audit record."""
    now = _utc(now)
    with pf.lock() if apply else _null():
        if apply:
            recover(pf)
        cur, new = _preflight(pf, facility_id, "purge")
        bundles = list_bundles(pf.root, facility_id)
        plan = {
            "action": "purge",
            "facility_id": facility_id,
            "from_state": cur,
            "to_state": new,
            "deletes": [
                t
                for t in _hot_trees(pf, facility_id) + [_quarantine_tree(pf, facility_id)]
                if os.path.exists(t)
            ]
            + [archive_dir(pf.root, facility_id)],
            "bundles": [b["bundle_id"] for b in bundles],
            "dry_run": not apply,
        }
        if not apply:
            return plan
        if confirm != facility_id:
            from ._states import LifecycleError

            raise LifecycleError(
                f"purge is irreversible: confirm by typing the facility id ({facility_id})"
            )
        reg = pf.registry
        entry = reg._raw().get(facility_id) or {}
        reg._tombstone(
            facility_id,
            {**entry, "state": new},
            reason=reason,
            extra={"purged_at": _stamp(now), "purge_pending": True},
        )
        data = reg._raw()
        if facility_id in data:
            del data[facility_id]
            reg._write(data)
        pf._drop_policy_entries(facility_id)
        pf._audit(
            "facility.purge",
            facility_id=facility_id,
            from_state=cur,
            to_state=new,
            reason=reason,
            details={"bundles_deleted": plan["bundles"], "trees": len(plan["deletes"])},
        )
        _finish_purge(pf, facility_id)
        return plan


def _finish_purge(pf, facility_id: str) -> None:
    for tree in _hot_trees(pf, facility_id) + [
        archive_dir(pf.root, facility_id),
        _quarantine_tree(pf, facility_id),
    ]:
        _swap.discard(tree)
    pf.store._invalidate_catalog()
    from ..resolve import clear_store_cache

    clear_store_cache(pf.store_root, facility_id)
    reg = pf.registry
    tomb = reg.tombstones()
    rec = dict(tomb.get(facility_id) or {})
    rec.pop("purge_pending", None)
    reg._tombstone(facility_id, rec, reason=rec.get("reason") or "purged", replace=True)


# --------------------------------------------------------------------------- recovery


def recover(pf) -> list:
    """Finish or roll back whatever a crash interrupted (the caller holds the lock).

    Rolls interrupted swaps forward or back and removes ``_trash-*`` leftovers under the store,
    the rollups, ``state/`` and ``archive/``; finishes the hot-data deletion of a facility recorded
    ``archived`` (after re-verifying its bundle) and any purge recorded ``purge_pending``. Audited
    as one ``portfolio.recover`` record when it did anything. Returns what it did.
    """
    done: list = []
    for base in (
        pf.store_root,
        os.path.join(pf.root, ROLLUPS_DIR),
        os.path.join(pf.root, "state"),
        os.path.join(pf.root, "archive"),
        os.path.join(pf.root, QUARANTINE_DIR),
    ):
        done += _swap.recover_tree(base, max_depth=5 if base != pf.store_root else 3)
    reg = pf.registry
    for fid, e in reg.all().items():
        arch = e.get("archive") or {}
        if e.get("state") == "archived" and arch and not arch.get("hot_deleted", True):
            b = _latest_bundle(pf, fid, arch.get("bundle"))
            if b is None or not verify_bundle(b["path"])["ok"]:
                done.append({"facility_id": fid, "action": "archive_blocked_bad_bundle"})
                continue
            _delete_hot(pf, fid)
            done.append({"facility_id": fid, "action": "archive_finished"})
    for fid, t in reg.tombstones().items():
        if (t or {}).get("purge_pending"):
            _finish_purge(pf, fid)
            done.append({"facility_id": fid, "action": "purge_finished"})
    if done:
        pf._audit(
            "portfolio.recover",
            reason="finished or rolled back work interrupted by a crash",
            details={"actions": [{k: str(v) for k, v in d.items()} for d in done]},
        )
    return done


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
