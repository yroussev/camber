"""The portfolio workspace: one root holding a portfolio's store, policy, audit log and lock.

::

    <root>/
      _portfolio.json   schema version, retention policy defaults, per-facility overrides, holds
      _audit.ndjson     append-only audit log (see ._audit)
      _lock             single-writer advisory lock (see ._lock)
      store/            the ParquetStore root (+ registry v2, tombstones, _workspace.json marker)
      rollups/          reserved: downsampled stores (created by a later release)
      state/<fid>/      faults, drift and M&V baselines, migrated originals, sha256 manifest
      archive/<fid>/    reserved: export bundles

The reserved directories are created lazily by the releases that use them.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os

from ..store import FacilityRegistry, ParquetStore, make_facility_id, require_facility_id
from ..store.facilities import _WORKSPACE_MARKER, _workspace_of_store
from ._audit import AUDIT_FILE, append_audit, audit_record, read_audit
from ._lock import LOCK_FILE, describe_holder, portfolio_lock, probe, read_holder
from ._state import SiteResolver, read_manifest, state_dir
from ._states import IMPLEMENTED, STATES, LifecycleError, transition

_CASCADE = ("offboard", "restore", "archive", "purge")

PORTFOLIO_FILE = "_portfolio.json"
SCHEMA_VERSION = 1
RESERVED_DIRS = ("rollups", "state", "archive")

# The agreed retention defaults, by data class. Stored now; *enforced* by a later release
# (`camber retention apply`). Precedence: legal hold > facility override > portfolio default.
DEFAULT_POLICY: dict = {
    "raw_trends": {"keep_months": 25},
    "hourly_rollups": {"keep_years": 7},
    "daily_rollups": {"keep": "indefinite"},
    "findings": {"keep_years": 7},
    "drift_baselines": {"keep": "equipment_life", "keep_versions": 10},
    # past reported savings depend on superseded M&V baselines, so every version is kept
    "mv_baselines": {"keep": "indefinite", "keep_versions": "all"},
    "reports": {"keep_last": 12},
    "audit": {"keep": "forever"},
}
OFFBOARDING_GRACE_DAYS = 30
_NEVER_OVERRIDDEN = ("audit",)  # the audit log is never deleted, whatever an override says


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_doc(store_rel: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "store": store_rel,
        "retention": {"defaults": copy.deepcopy(DEFAULT_POLICY), "overrides": {}},
        "legal_holds": {},
        "offboarding_grace_days": OFFBOARDING_GRACE_DAYS,
    }


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def is_workspace(path) -> bool:
    """True if ``path`` is a portfolio workspace root (it has a ``_portfolio.json``)."""
    return os.path.isfile(os.path.join(os.fspath(path), PORTFOLIO_FILE))


class Portfolio:
    """A portfolio workspace (provisional API; see docs/PORTFOLIO.md).

    Open an existing workspace with ``Portfolio(root)``; create one with :meth:`init`, or wrap an
    existing store directory with :meth:`adopt`. Every mutating method takes the single-writer
    lock (``lock_timeout`` seconds to wait; ``0`` refuses at once with
    :class:`~camber.portfolio.PortfolioLocked`) and appends one audit line with the OS user and
    the ``reason`` given.
    """

    def __init__(self, root, *, lock_timeout: float = 0.0):
        root = os.path.abspath(os.fspath(root))
        if not is_workspace(root):
            raise FileNotFoundError(
                f"{root} is not a portfolio workspace (no {PORTFOLIO_FILE}); create one with "
                "`camber portfolio init <root>` or wrap a store with `camber portfolio adopt`"
            )
        self.root = root
        self.lock_timeout = lock_timeout

    # ------------------------------------------------------------------ creation

    @classmethod
    def init(cls, root, *, lock_timeout: float = 0.0) -> Portfolio:
        """Create a workspace at ``root`` with an empty ``store/`` (idempotent).

        Re-running on an existing workspace changes nothing and writes no audit line.
        """
        root = os.path.abspath(os.fspath(root))
        if is_workspace(root):
            return cls(root, lock_timeout=lock_timeout)
        os.makedirs(root, exist_ok=True)
        with portfolio_lock(root, timeout=lock_timeout):
            if not is_workspace(root):  # re-check under the lock
                store = os.path.join(root, "store")
                os.makedirs(store, exist_ok=True)
                _mark_store(store, root)
                _write_json(os.path.join(root, PORTFOLIO_FILE), _default_doc("store"))
                append_audit(
                    root,
                    audit_record(
                        "portfolio.init", reason="workspace created", details={"store": "store"}
                    ),
                )
        return cls(root, lock_timeout=lock_timeout)

    @classmethod
    def adopt(cls, store, root=None, *, reason: str, lock_timeout: float = 0.0) -> Portfolio:
        """Wrap an existing store directory as a workspace, moving nothing.

        ``root`` defaults to the store's parent directory. The workspace records where the store is
        (relative to ``root`` when inside it, else absolute), and the store gets a
        ``_workspace.json`` marker pointing back so the registry takes the lock and audits from then
        on. Existing facilities keep their data and read as ``active`` (v1 entries); unregistered
        partitions are listed but not registered. Idempotent: adopting an already-adopted store
        into the same workspace is a no-op.
        """
        store = os.path.abspath(os.fspath(store))
        if not os.path.isdir(store):
            raise FileNotFoundError(f"store directory {store} does not exist")
        root = os.path.abspath(os.fspath(root)) if root is not None else os.path.dirname(store)
        current = _workspace_of_store(store)
        if current is not None:
            if os.path.normpath(current) == os.path.normpath(root):
                return cls(root, lock_timeout=lock_timeout)
            raise ValueError(f"store {store} already belongs to the workspace {current}")
        if is_workspace(root):
            existing = cls(root).store_root
            if os.path.normpath(existing) != os.path.normpath(store):
                raise ValueError(f"{root} is already a workspace for the store {existing}")
        os.makedirs(root, exist_ok=True)
        rel = os.path.relpath(store, root)
        store_ref = rel if not rel.startswith("..") else store
        with portfolio_lock(root, timeout=lock_timeout):
            if not is_workspace(root):
                _write_json(os.path.join(root, PORTFOLIO_FILE), _default_doc(store_ref))
            _mark_store(store, root)
            st = ParquetStore(store)
            registered = sorted(FacilityRegistry(store).all())
            unregistered = sorted(set(st.facilities()) - set(registered))
            append_audit(
                root,
                audit_record(
                    "portfolio.adopt",
                    reason=reason,
                    details={
                        "store": store_ref,
                        "registered": registered,
                        "unregistered": unregistered,
                    },
                ),
            )
        return cls(root, lock_timeout=lock_timeout)

    # ------------------------------------------------------------------ plumbing

    def _doc(self) -> dict:
        with open(os.path.join(self.root, PORTFOLIO_FILE), encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError(f"{PORTFOLIO_FILE} is not a JSON object")
        ver = doc.get("schema_version")
        if isinstance(ver, int) and ver > SCHEMA_VERSION:
            raise ValueError(
                f"{PORTFOLIO_FILE} has schema_version {ver}; this CAMBER reads up to "
                f"{SCHEMA_VERSION} -- upgrade CAMBER"
            )
        return doc

    @property
    def store_root(self) -> str:
        """Absolute path of the workspace's ParquetStore root."""
        ref = self._doc().get("store") or "store"
        return os.path.normpath(os.path.join(self.root, ref))

    @property
    def store(self) -> ParquetStore:
        """The workspace's :class:`~camber.store.ParquetStore`."""
        return ParquetStore(self.store_root)

    @property
    def registry(self) -> FacilityRegistry:
        """The workspace store's :class:`~camber.store.FacilityRegistry`."""
        return FacilityRegistry(self.store_root, lock_timeout=self.lock_timeout)

    def lock(self, *, timeout=None):
        """Context manager holding the single-writer lock (re-entrant within this process)."""
        return portfolio_lock(self.root, timeout=self.lock_timeout if timeout is None else timeout)

    def _audit(self, action: str, **kw) -> dict:
        return append_audit(self.root, audit_record(action, **kw))

    def audit_log(self, *, facility_id=None) -> list:
        """Audit records, oldest first (optionally for one facility)."""
        return read_audit(self.root, facility_id=facility_id)

    # ------------------------------------------------------------------ policy

    def policy(self) -> dict:
        """The portfolio's retention defaults by data class (a copy)."""
        doc = self._doc()
        base = copy.deepcopy(DEFAULT_POLICY)
        base.update(copy.deepcopy((doc.get("retention") or {}).get("defaults") or {}))
        return base

    def legal_holds(self) -> dict:
        """``{facility_id: {...}}`` of facilities under a legal hold."""
        return dict(self._doc().get("legal_holds") or {})

    def effective_retention(self, facility_id: str) -> dict:
        """Per data class: ``{"rule": {...}, "source": "legal_hold"|"facility"|"default"}``.

        Precedence: a legal hold (nothing is deleted) beats a facility override, which beats the
        portfolio default. The audit class is never deleted and cannot be overridden.
        """
        doc = self._doc()
        overrides = ((doc.get("retention") or {}).get("overrides") or {}).get(facility_id) or {}
        held = facility_id in (doc.get("legal_holds") or {})
        out = {}
        for cls, rule in self.policy().items():
            if held:
                out[cls] = {"rule": {"keep": "legal_hold"}, "source": "legal_hold"}
            elif cls in overrides and cls not in _NEVER_OVERRIDDEN:
                out[cls] = {"rule": dict(overrides[cls]), "source": "facility"}
            else:
                out[cls] = {"rule": dict(rule), "source": "default"}
        return out

    # ------------------------------------------------------------------ facilities

    def facilities(self, *, state=None) -> dict:
        """``{facility_id: entry}`` for every registered facility *and* every unregistered store
        partition (shown as ``active`` with ``"registered": False``), optionally by ``state``.
        Tombstoned ids are not facilities (see :meth:`status` for any data they left behind)."""
        entries = {
            fid: {"facility_id": fid, **e, "registered": True}
            for fid, e in self.registry.all().items()
        }
        tomb = self.registry.tombstones()
        for fid in self.store.facilities():
            if fid not in entries and fid not in tomb:  # a tombstoned id is not a facility
                entries[fid] = {
                    "facility_id": fid,
                    "state": "active",
                    "display_name": fid,
                    "created_at": None,
                    "state_changed_at": None,
                    "owner": None,
                    "portfolio": [],
                    "notes": None,
                    "registered": False,
                }
        if state is not None:
            if state not in STATES:
                raise ValueError(f"unknown state {state!r} (known: {', '.join(STATES)})")
            entries = {k: v for k, v in entries.items() if v.get("state") == state}
        return dict(sorted(entries.items()))

    def facility(self, facility_id: str) -> dict:
        """One facility's entry (registered or a bare store partition); ``KeyError`` if unknown."""
        entries = self.facilities()
        if facility_id not in entries:
            tomb = self.registry.tombstones().get(facility_id)
            if tomb:
                raise KeyError(f"facility {facility_id!r} was removed (tombstoned {tomb})")
            raise KeyError(f"unknown facility {facility_id!r}")
        return entries[facility_id]

    def add_facility(
        self,
        name: str,
        *,
        reason: str,
        facility_id=None,
        owner=None,
        tags=(),
        notes=None,
        activate: bool = False,
        private: bool = False,
    ) -> dict:
        """Register a new facility (``provisioning``, or ``active`` with ``activate=True``).

        ``facility_id`` defaults to :func:`~camber.store.make_facility_id` of ``name``. Refused if
        the id is already registered, tombstoned, or a case-variant of a known id. ``private``
        (0.94, provisional) marks it private: its weather requests default to ``offline`` (see
        :mod:`camber.weather_privacy`).
        """
        _need_reason(reason)
        fid = require_facility_id(facility_id or make_facility_id(name))
        with self.lock():
            reg = self.registry
            if fid in reg.all():
                raise ValueError(f"facility {fid!r} is already registered")
            meta = {"owner": owner, "portfolio": sorted(set(tags or ())), "notes": notes}
            if private:
                meta["private"] = True
            reg._create(
                fid,
                name,
                state="active" if activate else "provisioning",
                reason=reason,
                meta={k: v for k, v in meta.items() if v not in (None, [])},
                action="facility.add",
            )
        return self.facility(fid)

    def _ensure_registered(self, facility_id: str) -> None:
        """Register a bare store partition (data but no entry) as ``active``; audited."""
        reg = self.registry
        if facility_id in reg.all():
            return
        if facility_id not in self.store.facilities():
            self.facility(facility_id)  # raises the right KeyError
        reg._create(
            facility_id,
            None,
            state="active",
            reason="registered on first lifecycle action (had data, no registry entry)",
        )

    def transition(self, facility_id: str, action: str, *, reason: str) -> dict:
        """Apply a lifecycle ``action`` to a facility and return
        ``{"facility_id", "from_state", "to_state"}``.

        ``activate``/``suspend``/``resume`` change the state only. ``offboard``, ``restore`` and
        ``archive`` run the full cascade (:meth:`offboard`, :meth:`restore`, :meth:`archive` with
        ``apply=True``). ``purge`` is refused here: it needs the typed facility id, so call
        :meth:`purge` with ``confirm=``. Raises :class:`~camber.portfolio.LifecycleError` for a
        transition the state machine refuses (including a deleting one under a legal hold).
        """
        _need_reason(reason)
        if action in _CASCADE:
            cur = self.facility(facility_id).get("state", "active")
            transition(cur, action, legal_hold=facility_id in self.legal_holds())
            if action == "purge":
                raise LifecycleError(
                    "purge is irreversible and needs the typed facility id: call "
                    f"Portfolio.purge({facility_id!r}, confirm={facility_id!r}, apply=True, ...)"
                )
            r = getattr(self, action)(facility_id, reason=reason, apply=True)
            return {
                "facility_id": facility_id,
                "from_state": r["from_state"],
                "to_state": r["to_state"],
            }
        with self.lock():
            cur = self.facility(facility_id).get("state", "active")  # KeyError if unknown
            held = facility_id in self.legal_holds()
            new = transition(cur, action, legal_hold=held)  # validates before anything changes
            if action not in IMPLEMENTED:  # pragma: no cover - every action is implemented
                raise NotImplementedError(f"`{action}` is not implemented")
            self._ensure_registered(facility_id)
            reg = self.registry
            reg._update(facility_id, {"state": new, "state_changed_at": _utc_now()})
            self._audit(
                f"facility.{action}",
                facility_id=facility_id,
                from_state=cur,
                to_state=new,
                reason=reason,
            )
        return {"facility_id": facility_id, "from_state": cur, "to_state": new}

    def rename(self, facility_id: str, display_name: str, *, reason: str) -> dict:
        """Change a facility's ``display_name`` (its id and original ``name`` are unchanged)."""
        _need_reason(reason)
        display_name = str(display_name).strip()
        if not display_name:
            raise ValueError("the new display name is empty")
        with self.lock():
            self._ensure_registered(facility_id)
            reg = self.registry
            old = reg.get(facility_id)
            if old.get("state") == "purged":
                raise LifecycleError(f"facility {facility_id!r} is purged; it cannot be renamed")
            reg._update(facility_id, {"display_name": display_name})
            self._audit(
                "facility.rename",
                facility_id=facility_id,
                from_state=old.get("state"),
                to_state=old.get("state"),
                reason=reason,
                details={"from": old.get("display_name"), "to": display_name},
            )
        return self.facility(facility_id)

    def set_private(self, facility_id: str, private: bool = True, *, reason: str) -> dict:
        """Mark a facility private (or not); audited (provisional, 0.94, #73).

        A private facility's weather requests default to ``offline`` and can never be
        ``public``; a config may opt it in to ``coarse`` (see :mod:`camber.weather_privacy`).
        The flag lives in the registry so it holds for every config of the facility.
        """
        _need_reason(reason)
        with self.lock():
            self._ensure_registered(facility_id)
            reg = self.registry
            old = reg.get(facility_id)
            reg._update(facility_id, {"private": bool(private)})
            self._audit(
                "facility.private",
                facility_id=facility_id,
                from_state=old.get("state"),
                to_state=old.get("state"),
                reason=reason,
                details={"from": bool(old.get("private", False)), "to": bool(private)},
            )
        return self.facility(facility_id)

    # ------------------------------------------------------------------ offboard .. purge (0.95)

    def offboard(self, facility_id: str, *, reason: str, apply: bool = False, now=None) -> dict:
        """Start the reversible offboarding grace period (active/suspended -> offboarding).

        Exports a verified bundle to ``archive/<fid>/`` first, then records the state with its
        grace deadline (``offboarding_grace_days``, default 30). ``apply=False`` (the default)
        returns the plan and changes nothing. Provisional (0.95).
        """
        from ._cascade import offboard

        _need_reason(reason)
        return offboard(self, facility_id, reason=reason, apply=apply, now=now)

    def archive(
        self,
        facility_id: str,
        *,
        reason: str,
        apply: bool = False,
        now=None,
        skip_grace: bool = False,
    ) -> dict:
        """Delete the facility's hot data, keeping its verified bundle (offboarding -> archived).

        Refused under a legal hold and, unless ``skip_grace``, before the grace period ends. The
        latest bundle is reused only if it still matches the hot data and verifies; otherwise a
        new one is exported first. Provisional (0.95).
        """
        from ._cascade import archive

        _need_reason(reason)
        return archive(
            self, facility_id, reason=reason, apply=apply, now=now, skip_grace=skip_grace
        )

    def restore(
        self, facility_id: str, *, reason: str, apply: bool = False, bundle=None, now=None
    ) -> dict:
        """Return an offboarding or archived facility to ``active``.

        From ``archived`` the bundle (the one archive recorded, or ``bundle=`` id) is verified,
        its content swapped back in and re-hashed. Provisional (0.95).
        """
        from ._cascade import restore

        _need_reason(reason)
        return restore(self, facility_id, reason=reason, apply=apply, bundle=bundle, now=now)

    def purge(
        self, facility_id: str, *, reason: str, apply: bool = False, confirm=None, now=None
    ) -> dict:
        """Delete everything of an archived facility but its tombstone and audit record.

        ``apply=True`` needs ``confirm`` equal to the facility id (the typed confirmation). The id
        stays tombstoned: it is never reused. Refused under a legal hold. Provisional (0.95).
        """
        from ._cascade import purge

        _need_reason(reason)
        return purge(self, facility_id, reason=reason, apply=apply, confirm=confirm, now=now)

    def export(self, facility_id: str, *, reason: str, now=None) -> dict:
        """Write a verified export bundle of the facility now, changing nothing else (audited)."""
        from ._bundle import export_bundle

        _need_reason(reason)
        with self.lock():
            self.facility(facility_id)
            man = export_bundle(self, facility_id, kind="manual", reason=reason, now=now)
            st = self.facility(facility_id).get("state")
            self._audit(
                "facility.export",
                facility_id=facility_id,
                from_state=st,
                to_state=st,
                reason=reason,
                details={"bundle": man["bundle_id"], "counts": man["counts"]},
            )
        return man

    def bundles(self, facility_id: str, *, verify: bool = False) -> list:
        """The facility's export bundles, oldest first (``verify=True`` re-hashes each one)."""
        from ._bundle import list_bundles, verify_bundle

        out = list_bundles(self.root, facility_id)
        if verify:
            for b in out:
                b["verify"] = verify_bundle(b["path"])
        return out

    def recover(self) -> list:
        """Finish or roll back work a crash interrupted (lifecycle commands run this first)."""
        from ._cascade import recover

        with self.lock():
            return recover(self)

    def _drop_policy_entries(self, facility_id: str) -> None:
        """Remove a facility's retention override and hold from ``_portfolio.json`` (purge)."""
        doc = self._doc()
        changed = False
        ov = (doc.get("retention") or {}).get("overrides") or {}
        if facility_id in ov:
            del ov[facility_id]
            changed = True
        if facility_id in (doc.get("legal_holds") or {}):
            del doc["legal_holds"][facility_id]
            changed = True
        if changed:
            _write_json(os.path.join(self.root, PORTFOLIO_FILE), doc)

    # ------------------------------------------------------------------ per-facility state

    def state_dir(self, facility_id: str) -> str:
        """``<root>/state/<facility_id>``: where the facility's faults and baselines live."""
        return state_dir(self.root, facility_id)

    def manifest(self, facility_id: str) -> dict:
        """The facility's state manifest (files with sha256, external artifacts, migrations)."""
        return read_manifest(self.root, facility_id)

    def migrate(self, paths=(), *, configs=(), mapping=None, apply=False, reason=None) -> dict:
        """Re-key site-keyed fault / baseline files to ``facility_id`` (see docs/PORTFOLIO.md).

        ``paths`` are legacy state files; ``configs`` are config files whose ``drift.store``,
        ``faults.store`` and report outputs belong to the config's facility; ``mapping`` is
        ``{site label: facility_id}`` (or ``["SITE=ID", ...]``) for labels the registry cannot
        map on its own. Returns a JSON-ready report.

        A dry run (the default) only plans. ``apply=True`` needs a ``reason``, takes the lock and
        carries the plan out -- unless it is ``blocked`` (a label that is ambiguous, unmapped or
        belongs to a tombstoned facility, a missing file): then nothing is written and the
        report says ``"applied": False``. Idempotent: re-applying changes nothing.
        """
        from ._migrate import apply as _apply
        from ._migrate import plan as _plan

        if not apply:
            report, _internal = _plan(self, paths, configs=configs, mapping=mapping)
            return {**report, "applied": False, "dry_run": True}
        _need_reason(reason)
        with self.lock():
            report, internal = _plan(self, paths, configs=configs, mapping=mapping)
            if report["blocked"]:
                return {**report, "applied": False, "dry_run": False}
            return {**_apply(self, internal, report, reason=str(reason)), "dry_run": False}

    def legacy_sites(self, facility_id: str) -> list:
        """The site labels that map to ``facility_id`` unambiguously (see :meth:`migrate`)."""
        return SiteResolver(self).legacy_sites(facility_id)

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        """A JSON-ready summary: paths, schema, facility counts by state, holds, lock holder."""
        doc = self._doc()
        facs = self.facilities()
        counts = {s: 0 for s in STATES}
        for e in facs.values():
            counts[e.get("state", "active")] = counts.get(e.get("state", "active"), 0) + 1
        holder = read_holder(self.root)
        locked = probe(self.root)
        tomb = self.registry.tombstones()
        counts["purged"] += sum(1 for t in tomb.values() if (t or {}).get("state") == "purged")
        return {
            "root": self.root,
            "store": self.store_root,
            "schema_version": doc.get("schema_version"),
            "created_at": doc.get("created_at"),
            "facilities": len(facs),
            "by_state": counts,
            "unregistered": sorted(k for k, v in facs.items() if not v.get("registered")),
            "tombstoned": sorted(tomb),
            "tombstoned_with_data": sorted(set(tomb) & set(self.store.facilities())),
            "legal_holds": sorted(self.legal_holds()),
            "retention_defaults": self.policy(),
            "offboarding_grace_days": doc.get("offboarding_grace_days", OFFBOARDING_GRACE_DAYS),
            "locked_by": None if locked is None else describe_holder(locked),
            "last_lock_holder": describe_holder(holder) if holder else None,
            "audit_records": len(self.audit_log()),
            "files": {
                "portfolio": PORTFOLIO_FILE,
                "audit": AUDIT_FILE,
                "lock": LOCK_FILE,
            },
        }


def _need_reason(reason) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason is required for every portfolio change (it is audited)")


def _mark_store(store: str, root: str) -> None:
    """Write the store's ``_workspace.json`` back-pointer (relative path to the workspace)."""
    rel = os.path.relpath(root, store)
    _write_json(os.path.join(store, _WORKSPACE_MARKER), {"portfolio": rel})


def find_workspace(explicit=None):
    """The workspace root to use: ``explicit``, else ``$CAMBER_PORTFOLIO``, else the current
    directory if it is a workspace; ``None`` when there is none."""
    if explicit:
        return os.path.abspath(explicit) if is_workspace(explicit) else None
    for cand in (os.environ.get("CAMBER_PORTFOLIO"), os.getcwd()):
        if cand and is_workspace(cand):
            return os.path.abspath(cand)
    return None
