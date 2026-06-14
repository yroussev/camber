"""Persistent fault lifecycle at portfolio scale.

`rules.triage.FaultRegister` is the lightweight, in-memory new/ongoing/resolved classifier for a
single session. This is its durable, operational sibling: a **persisted fault store** keyed by
the stable (site, equip, rule) fingerprint that survives across runs and processes, with an
**assignment / status workflow** (open → acknowledged → in-progress → resolved, plus suppressed)
and **SLA / aging** tracking so a portfolio's open faults can be triaged, owned, and held to a
response time.

Dependency-light: state is a JSON document (atomic write); time math uses pandas (already a core
dependency). Findings are duck-typed (`severity`/`equip`/`rule`), so any finding-like object works.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

import pandas as pd

from .integrate.tickets import _attr, fingerprint

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
    first_seen: str = ""          # run_id stamped when first observed (ISO timestamp recommended)
    last_seen: str = ""           # run_id of the most recent observation
    occurrences: int = 0
    assignee: str = ""
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FaultRecord":
        return cls(**d)


class FaultLifecycle:
    """A persistent fault store with assignment, status workflow, and SLA/aging."""

    def __init__(self, path: str | None = None):
        self.path = path
        self._recs: dict[str, FaultRecord] = {}

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: str) -> "FaultLifecycle":
        """Load a fault store from JSON (empty if the file doesn't exist yet)."""
        lc = cls(path)
        if path and os.path.isfile(path):
            data = json.load(open(path, encoding="utf-8"))
            for d in data.get("faults", []):
                lc._recs[d["fingerprint"]] = FaultRecord.from_dict(d)
        return lc

    def save(self, path: str | None = None) -> int:
        """Atomically write the store to JSON; returns the record count."""
        p = path or self.path
        if not p:
            raise ValueError("no path to save to (pass path= or construct with one)")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"faults": [r.as_dict() for r in self._recs.values()]}, fh, indent=2)
        os.replace(tmp, p)
        return len(self._recs)

    # ----------------------------------------------------------------- folding a run
    def update(self, findings, *, run_id, site: str = "", actionable=ACTIONABLE,
               reopen_on_recurrence: bool = True, auto_resolve_absent: bool = False) -> dict:
        """Fold one analysis run's findings into the store. Returns fingerprint lists
        ``{new, ongoing, reopened, absent, resolved}``.

        New actionable findings create ``open`` records; recurring ones bump ``last_seen`` and
        ``occurrences`` (and, if ``reopen_on_recurrence``, reopen a previously-resolved fault).
        Open faults *absent* from this run are returned under ``absent`` (candidates to close);
        ``auto_resolve_absent`` resolves them at ``run_id`` instead.
        """
        rid = str(run_id)
        seen, new, ongoing, reopened = set(), [], [], []
        for f in findings:
            if _attr(f, "severity", "info") not in actionable:
                continue
            equip, rule = _attr(f, "equip", ""), _attr(f, "rule", "")
            fp = fingerprint(site, equip, rule)
            seen.add(fp)
            r = self._recs.get(fp)
            if r is None:
                self._recs[fp] = FaultRecord(
                    fingerprint=fp, site=site, equip=equip, rule=rule,
                    severity=_attr(f, "severity", "info"), status="open",
                    first_seen=rid, last_seen=rid, occurrences=1)
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
        absent = [fp for fp, r in self._recs.items()
                  if fp not in seen and r.status in OPEN_STATUSES]
        resolved = []
        if auto_resolve_absent:
            for fp in absent:
                self._recs[fp].status = "resolved"
                self._recs[fp].resolved_at = rid
                self._recs[fp].notes.append(f"{rid}: auto-resolved (absent)")
                resolved.append(fp)
            absent = []
        return {"new": sorted(new), "ongoing": sorted(ongoing), "reopened": sorted(reopened),
                "absent": sorted(absent), "resolved": sorted(resolved)}

    # ----------------------------------------------------------------- workflow ops
    def _get(self, fp: str) -> FaultRecord:
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
        self._get(fp).notes.append(note)
        return self._recs[fp]

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

    def overdue(self, now, *, ack_sla_hours: dict | None = None,
                resolve_sla_hours: dict | None = None) -> list:
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
        return {"total": len(self._recs), "open": len(self.open_faults()),
                "by_status": by_status, "open_by_severity": by_sev}
