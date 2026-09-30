"""Persistent fault lifecycle at portfolio scale.

`rules.triage.FaultRegister` is the lightweight, in-memory new/ongoing/resolved classifier for a
single session. This is its durable, operational sibling: a **persisted fault store** keyed by
the stable (site, equip, rule) fingerprint that survives across runs and processes, with an
**assignment / status workflow** (open → acknowledged → in-progress → resolved, plus suppressed)
and **SLA / aging** tracking so a portfolio's open faults can be triaged, owned, and held to a
response time.

Dependency-light: state is a JSON document (atomic write); time math uses pandas (already a core
dependency). Findings are duck-typed (`severity`/`equip`/`rule`), so any finding-like object works.

**Identity.** A fault's fingerprint is ``sha1(key, equip, rule)``. Pass ``facility_id=`` (to
:meth:`FaultLifecycle.load` or :meth:`FaultLifecycle.update`) and the key is the facility's
stable, never-reused ``facility_id``: renaming the facility no longer orphans its history. Without
one the key is the free-text ``site`` string, exactly as before (outside a portfolio workspace
nothing changes). A store opened for a facility still reads records written under the old
site-keyed scheme through a **deprecated compatibility path**: records whose ``site`` is one of
the facility's known names are re-keyed to the facility id on the fly (their old fingerprint is
kept in ``aliases``, so a ticket or a script holding it still resolves). ``camber portfolio
migrate`` does the same once, on disk. See docs/PORTFOLIO.md.

**Scope of a run (0.96, #76).** :meth:`FaultLifecycle.update` only ever reports -- and, with
``auto_resolve_absent``, resolves -- faults that belong to the run's own key. Bound to a
facility, that is the records whose ``facility_id`` is the facility's. Unbound (the legacy,
site-keyed path) it is the records whose *fingerprint* is keyed by the run's ``site`` (so a file
shared by several sites, or by site- and facility-keyed records, never has one site's run close
another's faults). A site-keyed record whose key cannot be told -- its fingerprint matches
neither the run's ``site`` nor its own stored ``site`` label (a hand-edited or pre-``site``
record) -- is never auto-resolved; the run lists it under ``unscoped`` instead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import pandas as pd

from ._deprecation import warn_deprecated
from ._statefile import dedup, load_state, save_path, write_json
from .integrate.tickets import _attr, fingerprint

__all__ = [
    "ACTIONABLE",
    "OPEN_STATUSES",
    "STATUSES",
    "FaultRecord",
    "FaultLifecycle",
]

ACTIONABLE = frozenset({"fault", "warn"})
OPEN_STATUSES = frozenset({"open", "acknowledged", "in_progress"})
STATUSES = ("open", "acknowledged", "in_progress", "resolved", "suppressed")


@dataclass
class FaultRecord:
    """One tracked fault and its lifecycle state."""

    fingerprint: str
    site: str
    equip: str
    rule: str
    severity: str
    status: str = "open"
    first_seen: str = ""  # run_id stamped when first observed (ISO timestamp recommended)
    last_seen: str = ""  # run_id of the most recent observation
    occurrences: int = 0
    assignee: str = ""
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    notes: list = field(default_factory=list)
    facility_id: str = ""  # the identity key; "" for a legacy, site-keyed record
    aliases: list = field(default_factory=list)  # earlier fingerprints (site-keyed, merged)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FaultRecord:
        """Rebuild a record; unknown keys (from a newer CAMBER) are ignored."""
        known = {f.name for f in fields(cls)}
        # a record without a stored ``site`` label loads with "" (its scope is then told from the
        # fingerprint alone -- see _legacy_scope)
        return cls(**{"site": "", **{k: v for k, v in d.items() if k in known}})


def _warn_legacy(n: int, what: str) -> None:
    warn_deprecated(
        f"reading {n} site-keyed {what} record(s) through the compatibility path",
        since="0.86",
        remove_in="2.0",
        use="`camber portfolio migrate` to re-key them to facility_id once, on disk",
        stacklevel=4,
    )


def _legacy_scope(r: FaultRecord, site) -> str:
    """Whether a record belongs to an unbound (site-keyed) run for ``site`` (0.96, #76).

    ``"in"`` when its fingerprint is keyed by ``site`` (the identity :meth:`FaultLifecycle.update`
    matches findings by; the stored ``site`` is only a label), ``"other"`` when it is
    facility-keyed or keyed by its own, different ``site`` label, and ``"unknown"`` when the
    fingerprint matches neither -- such a record's site cannot be told, so it is never
    auto-resolved. ``aliases`` are earlier identities and never widen the scope.
    """
    if r.facility_id:
        return "other"
    if r.fingerprint == fingerprint(site, r.equip, r.rule):
        return "in"
    if isinstance(r.site, str) and r.fingerprint == fingerprint(r.site, r.equip, r.rule):
        return "other"
    return "unknown"


def _merge_faults(keep: FaultRecord, other: FaultRecord) -> FaultRecord:
    """Fold ``other`` (the same fault under another key) into ``keep``, deterministically.

    The later-seen record's workflow state (status, severity, assignee, acknowledgement,
    resolution) wins; ``first_seen`` is the earlier one, ``last_seen`` the later one, occurrences
    add up, and notes and aliases are concatenated (older record first).
    """
    if str(keep.last_seen) >= str(other.last_seen):
        newer, older = keep, other
    else:
        newer, older = other, keep
    out = FaultRecord.from_dict(newer.as_dict())
    out.fingerprint = keep.fingerprint
    out.facility_id = keep.facility_id or other.facility_id
    firsts = [x for x in (keep.first_seen, other.first_seen) if x]
    out.first_seen = min(firsts, key=str) if firsts else ""
    out.last_seen = max(str(keep.last_seen), str(other.last_seen))
    out.occurrences = int(keep.occurrences) + int(other.occurrences)
    out.notes = list(older.notes) + list(newer.notes)
    out.aliases = dedup([*keep.aliases, other.fingerprint, *other.aliases], drop=out.fingerprint)
    return out


class FaultLifecycle:
    """A persistent fault store with assignment, status workflow, and SLA/aging.

    ``facility_id`` binds the store to one facility: fingerprints are keyed by it (not by the
    ``site`` string), and records written under the old site-keyed scheme are re-keyed to it
    through the deprecated compatibility path when their ``site`` is one of ``legacy_sites`` --
    or, when ``legacy_sites`` is not given, the ``site`` a later :meth:`update` passes. Pass the
    labels explicitly (even ``()``) when a label could belong to another facility too.
    Unbound (the default) the store behaves exactly as before.
    """

    def __init__(self, path: str | None = None, *, facility_id=None, legacy_sites=None):
        self.path = path
        self.facility_id = facility_id or None
        self._explicit_legacy = legacy_sites is not None
        self.legacy_sites = tuple(s for s in (legacy_sites or ()) if s)
        self.legacy_adopted = 0  # site-keyed records re-keyed through the compatibility path
        self._recs: dict[str, FaultRecord] = {}
        self._aliases: dict[str, str] = {}

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: str, *, facility_id=None, legacy_sites=None) -> FaultLifecycle:
        """Load a fault store from JSON (empty if the file doesn't exist yet).

        A file that ``camber portfolio migrate`` replaced by a redirect stub is followed: with
        ``facility_id`` to that facility's ``state/<facility_id>/faults.json``, without one to a
        read-only merged view of every migrated facility.
        """
        lc = cls(path, facility_id=facility_id, legacy_sites=legacy_sites)
        data, _where, _red = load_state(path, facility_id=facility_id, list_key="faults")
        for d in data.get("faults", []):
            rec = FaultRecord.from_dict(d)
            lc._recs[rec.fingerprint] = rec
        lc._index_aliases()
        if lc.facility_id and lc.legacy_sites:
            lc._adopt_legacy(lc.facility_id, lc.legacy_sites)
        return lc

    def save(self, path: str | None = None) -> int:
        """Atomically write the store to JSON; returns the record count.

        Saving to a migrated (redirect-stub) path writes the bound facility's state file; an
        unbound store cannot save there (see :meth:`load`).
        """
        p = path or self.path
        if not p:
            raise ValueError("no path to save to (pass path= or construct with one)")
        write_json(
            save_path(p, facility_id=self.facility_id),
            {"faults": [r.as_dict() for r in self._recs.values()]},
        )
        return len(self._recs)

    # ----------------------------------------------------------------- identity
    def _index_aliases(self) -> None:
        self._aliases = {a: fp for fp, r in self._recs.items() for a in r.aliases}

    def _adopt_legacy(self, facility_id: str, sites) -> int:
        """Re-key site-keyed records whose ``site`` is in ``sites`` to ``facility_id``."""
        want = {s for s in sites if s}
        legacy = [r for r in self._recs.values() if not r.facility_id and r.site in want]
        for r in legacy:
            del self._recs[r.fingerprint]
            old = r.fingerprint
            r.facility_id = facility_id
            r.fingerprint = fingerprint(facility_id, r.equip, r.rule)
            if old != r.fingerprint and old not in r.aliases:
                r.aliases.append(old)
            cur = self._recs.get(r.fingerprint)
            self._recs[r.fingerprint] = _merge_faults(cur, r) if cur is not None else r
        if legacy:
            self.legacy_adopted += len(legacy)
            self._index_aliases()
            _warn_legacy(len(legacy), "fault")
        return len(legacy)

    # ----------------------------------------------------------------- folding a run
    def update(
        self,
        findings,
        *,
        run_id,
        site: str = "",
        facility_id=None,
        actionable=ACTIONABLE,
        reopen_on_recurrence: bool = True,
        auto_resolve_absent: bool = False,
    ) -> dict:
        """Fold one analysis run's findings into the store. Returns fingerprint lists
        ``{new, ongoing, reopened, absent, resolved}``.

        New actionable findings create ``open`` records; recurring ones bump ``last_seen`` and
        ``occurrences`` (and, if ``reopen_on_recurrence``, reopen a previously-resolved fault).
        Open faults *absent* from this run are returned under ``absent`` (candidates to close);
        ``auto_resolve_absent`` resolves them at ``run_id`` instead.

        With a ``facility_id`` (here or bound at :meth:`load`) fingerprints are keyed by it and
        ``site`` is only the display label stored on new records; ``absent`` then covers that
        facility's faults only. Without one, the key is ``site`` (the pre-0.86 behaviour) and
        ``absent`` covers only the open records whose fingerprint is keyed by ``site`` (0.96,
        #76: before, it also swept other sites' and facility-keyed records in the same file).
        Open site-keyed records whose site cannot be told are never auto-resolved; they are
        listed under an extra ``unscoped`` key, present only when there is one.
        """
        rid = str(run_id)
        fid = facility_id or self.facility_id
        if fid:
            self._adopt_legacy(fid, self.legacy_sites if self._explicit_legacy else (site,))
        key = fid or site
        seen, new, ongoing, reopened = set(), [], [], []
        for f in findings:
            if _attr(f, "severity", "info") not in actionable:
                continue
            equip, rule = _attr(f, "equip", ""), _attr(f, "rule", "")
            fp = fingerprint(key, equip, rule)
            seen.add(fp)
            r = self._recs.get(fp)
            if r is None:
                self._recs[fp] = FaultRecord(
                    fingerprint=fp,
                    site=site,
                    equip=equip,
                    rule=rule,
                    severity=_attr(f, "severity", "info"),
                    status="open",
                    first_seen=rid,
                    last_seen=rid,
                    occurrences=1,
                    facility_id=fid or "",
                )
                new.append(fp)
            else:
                r.last_seen = rid
                r.occurrences += 1
                r.severity = _attr(f, "severity", r.severity)
                if r.status == "resolved" and reopen_on_recurrence:
                    r.status, r.resolved_at = "open", None
                    r.notes.append(f"{rid}: reopened (recurred)")
                    reopened.append(fp)
                else:
                    ongoing.append(fp)
        absent, unscoped = [], []
        for fp, r in self._recs.items():
            if fp in seen or r.status not in OPEN_STATUSES:
                continue
            if fid:
                if r.facility_id == fid:
                    absent.append(fp)
                continue
            scope = _legacy_scope(r, site)
            if scope == "in":
                absent.append(fp)
            elif scope == "unknown":
                unscoped.append(fp)
        resolved = []
        if auto_resolve_absent:
            for fp in absent:
                self._recs[fp].status = "resolved"
                self._recs[fp].resolved_at = rid
                self._recs[fp].notes.append(f"{rid}: auto-resolved (absent)")
                resolved.append(fp)
            absent = []
        out = {
            "new": sorted(new),
            "ongoing": sorted(ongoing),
            "reopened": sorted(reopened),
            "absent": sorted(absent),
            "resolved": sorted(resolved),
        }
        if unscoped:
            out["unscoped"] = sorted(unscoped)
        return out

    # ----------------------------------------------------------------- workflow ops
    def _get(self, fp: str) -> FaultRecord:
        """A record by fingerprint -- or by an earlier (site-keyed / merged) one in ``aliases``."""
        fp = fp if fp in self._recs else self._aliases.get(fp, fp)
        if fp not in self._recs:
            raise KeyError(f"no fault with fingerprint {fp!r}")
        return self._recs[fp]

    def get(self, fp: str) -> FaultRecord:
        """Return one record by fingerprint."""
        return self._get(fp)

    def assign(self, fp: str, who: str) -> FaultRecord:
        """Assign a fault to an owner."""
        r = self._get(fp)
        r.assignee = who
        return r

    def acknowledge(self, fp: str, at) -> FaultRecord:
        """Mark a fault acknowledged at time ``at``."""
        r = self._get(fp)
        r.status, r.acknowledged_at = "acknowledged", str(at)
        return r

    def start(self, fp: str) -> FaultRecord:
        """Mark a fault in progress."""
        r = self._get(fp)
        r.status = "in_progress"
        return r

    def resolve(self, fp: str, at, *, note: str | None = None) -> FaultRecord:
        """Resolve a fault at time ``at``."""
        r = self._get(fp)
        r.status, r.resolved_at = "resolved", str(at)
        if note:
            r.notes.append(f"{at}: {note}")
        return r

    def suppress(self, fp: str, *, note: str | None = None) -> FaultRecord:
        """Suppress a fault (known/accepted; excluded from open work)."""
        r = self._get(fp)
        r.status = "suppressed"
        if note:
            r.notes.append(note)
        return r

    def reopen(self, fp: str) -> FaultRecord:
        """Reopen a resolved/suppressed fault."""
        r = self._get(fp)
        r.status, r.resolved_at = "open", None
        return r

    def add_note(self, fp: str, note: str) -> FaultRecord:
        """Append a free-text note to a fault."""
        r = self._get(fp)
        r.notes.append(note)
        return r

    # ----------------------------------------------------------------- queries
    def records(self) -> list:
        """All records."""
        return list(self._recs.values())

    def open_faults(self) -> list:
        """Records in an open status (open / acknowledged / in_progress)."""
        return [r for r in self._recs.values() if r.status in OPEN_STATUSES]

    def by_status(self, status: str) -> list:
        return [r for r in self._recs.values() if r.status == status]

    def by_assignee(self, who: str) -> list:
        return [r for r in self._recs.values() if r.assignee == who]

    def aging(self, now) -> dict:
        """``{fingerprint: hours_open}`` for every open fault (now − first_seen)."""
        t = pd.Timestamp(now)
        out = {}
        for fp, r in self._recs.items():
            if r.status in OPEN_STATUSES and r.first_seen:
                out[fp] = round((t - pd.Timestamp(r.first_seen)) / pd.Timedelta(hours=1), 2)
        return out

    def overdue(
        self, now, *, ack_sla_hours: dict | None = None, resolve_sla_hours: dict | None = None
    ) -> list:
        """Open faults past an SLA. Returns ``[(record, kind, age_hours, sla_hours)]``.

        ``ack_sla_hours``/``resolve_sla_hours`` map severity → hours. A still-unacknowledged
        ``open`` fault older than its ack SLA is ``"ack"``-overdue; any open fault older than its
        resolve SLA is ``"resolve"``-overdue.
        """
        t = pd.Timestamp(now)
        ack_sla, res_sla = ack_sla_hours or {}, resolve_sla_hours or {}
        out = []
        for r in self._recs.values():
            if r.status not in OPEN_STATUSES or not r.first_seen:
                continue
            age = (t - pd.Timestamp(r.first_seen)) / pd.Timedelta(hours=1)
            if r.status == "open" and r.severity in ack_sla and age > ack_sla[r.severity]:
                out.append((r, "ack", round(age, 2), ack_sla[r.severity]))
            if r.severity in res_sla and age > res_sla[r.severity]:
                out.append((r, "resolve", round(age, 2), res_sla[r.severity]))
        return out

    def summary(self) -> dict:
        """Counts by status and by severity (open faults only), plus totals."""
        by_status = {s: 0 for s in STATUSES}
        by_sev: dict = {}
        for r in self._recs.values():
            by_status[r.status] = by_status.get(r.status, 0) + 1
            if r.status in OPEN_STATUSES:
                by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        return {
            "total": len(self._recs),
            "open": len(self.open_faults()),
            "by_status": by_status,
            "open_by_severity": by_sev,
        }
