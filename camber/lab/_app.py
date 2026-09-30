"""The lab's state and actions: which store, the catalog view, fetch/ingest jobs and reports.

:class:`LabApp` is what :func:`camber.lab.dispatch_lab` routes to. It holds the store (a plain
:class:`~camber.store.ParquetStore`, or a portfolio workspace's store), the dataset cache
directory, the job queue, the per-run CSRF token and the host/origin allowlist once the server is
bound. Everything here goes through the public :mod:`camber.datasets` pipeline -- the lab adds no
second way to download or ingest data -- and, in a workspace, through the 0.95 facility lifecycle.

**Workspace mode.** A dataset's facility (``ds-<id>``) is registered ``provisioning`` before its
first ingest, the ingest runs under the workspace's single-writer lock, and the facility is
activated afterwards; every fetch, acknowledgement and ingest appends a ``lab.*`` audit line. A
facility that has left the ``provisioning`` / ``active`` states (suspended, offboarding, archived)
is not re-ingested: its lifecycle owns it (``camber facility resume|restore``).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading

from ..api.read import ReadAPI
from ..store import ParquetStore, valid_facility_id
from ._jobs import JobQueue

LAB_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_PENDING_JOBS = 20
INGESTABLE_STATES = ("provisioning", "active")
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")


class LabError(Exception):
    """A request the lab refuses, with the HTTP status to answer it with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _free_bytes(path: str) -> int | None:
    """Free bytes on the filesystem that holds ``path`` (its nearest existing ancestor)."""
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    try:
        return int(shutil.disk_usage(p).free)
    except OSError:  # pragma: no cover - unreadable mount
        return None


class LabApp:
    """The state behind one ``camber lab`` server (provisional API).

    Pass exactly one of ``store`` (a ParquetStore or its directory) or ``workspace`` (a portfolio
    workspace root). ``data_dir`` is the dataset cache (default: :func:`camber.datasets` rules).
    ``entries`` replaces the packaged catalog and ``opener`` the HTTPS opener -- both for tests.
    """

    def __init__(
        self,
        *,
        store=None,
        workspace=None,
        data_dir=None,
        entries=None,
        opener=None,
        token: str | None = None,
    ):
        if (store is None) == (workspace is None):
            raise ValueError("pass exactly one of store= or workspace=")
        from ..datasets import _paths

        self.portfolio = None
        if workspace is not None:
            from ..portfolio import Portfolio

            self.portfolio = Portfolio(workspace)
            store = self.portfolio.store_root
        self.store = store if isinstance(store, ParquetStore) else ParquetStore(os.fspath(store))
        self.read_api = ReadAPI(self.store)
        self.data_dir = _paths.data_dir(data_dir)
        self._entries = tuple(entries) if entries is not None else None
        self.opener = opener
        self.token = token or secrets.token_urlsafe(32)
        self.jobs = JobQueue()
        self.port: int | None = None
        self.allowed_hosts: frozenset = frozenset()
        self.allowed_origins: frozenset = frozenset()
        self._report_lock = threading.Lock()
        self._reports: dict = {}

    # ------------------------------------------------------------------ binding

    def bind(self, port: int) -> None:
        """Set the Host / Origin allowlist for the bound port (loopback names only)."""
        self.port = int(port)
        hosts = {f"{LAB_HOST}:{self.port}", f"localhost:{self.port}"}
        self.allowed_hosts = frozenset(hosts)
        self.allowed_origins = frozenset(f"http://{h}" for h in hosts)

    def close(self) -> None:
        """Stop the job worker (the running job finishes; queued ones are cancelled)."""
        self.jobs.close()

    @property
    def mode(self) -> str:
        """``"workspace"`` or ``"store"``."""
        return "workspace" if self.portfolio is not None else "store"

    # ------------------------------------------------------------------ catalog

    def entries(self) -> list:
        """The catalog entries this lab offers (the packaged catalog unless overridden)."""
        if self._entries is not None:
            return list(self._entries)
        from .. import datasets

        return datasets.catalog()

    def entry(self, dataset_id: str):
        """The entry ``dataset_id``; :class:`LabError` 400 for an id not in the catalog."""
        if isinstance(dataset_id, str):
            for e in self.entries():
                if e.id == dataset_id:
                    return e
        raise LabError(400, f"unknown dataset id {dataset_id!r}: the lab takes catalog ids only")

    def _dataset_facilities(self) -> dict:
        """``{dataset_id: [{facility_id, state, subset, rows, ...}]}`` for ingested datasets."""
        from ..datasets._ingest import dataset_meta

        out: dict = {}
        present = set(self.store.facilities())
        for fid, meta in sorted(self.store.facilities_meta().items()):
            d = dataset_meta(meta)
            if not d.get("dataset_id") or fid not in present:
                continue
            out.setdefault(d["dataset_id"], []).append(
                {
                    "facility_id": fid,
                    "state": meta.get("state") or "active",
                    "subset": d.get("subset"),
                    "rows": d.get("rows"),
                    "equipment": d.get("equipment"),
                    "ingested_at": d.get("ingested_at"),
                    "redistribution": d.get("redistribution"),
                }
            )
        return out

    def catalog_view(self) -> dict:
        """The JSON behind the catalog table: entries, what is fetched / ingested, free disk."""
        from ..datasets import _licence, _ops

        entries = self.entries()
        status = {r["id"]: r for r in _ops.dataset_status(entries, data_dir=self.data_dir)}
        ingested = self._dataset_facilities()
        rows = []
        for e in entries:
            st = status.get(e.id) or {}
            sugg = e.suggested_analyses or {}
            rows.append(
                {
                    "id": e.id,
                    "title": e.title,
                    "summary": e.summary,
                    "teaches": list(e.teaches),
                    "publisher": e.publisher,
                    "citation": e.citation,
                    "landing_url": e.landing_url,
                    "kind": e.kind,
                    "licence": e.licence,
                    "access": e.access,
                    "research_only": e.research_only,
                    "access_reason": e.access_reason,
                    "commercial_ok": e.commercial_ok,
                    "labeled_faults": e.labeled_faults,
                    "manual": e.manual,
                    "equipment": e.equipment,
                    "requires_extras": list(e.requires_extras),
                    "subsets": {
                        name: {
                            "description": (spec or {}).get("description", ""),
                            "download_bytes": e.download_bytes(name),
                            "store_bytes": e.store_bytes(name),
                        }
                        for name, spec in e.subsets.items()
                    },
                    "fetched": st.get("fetched") or {},
                    "bytes_on_disk": st.get("bytes_on_disk") or 0,
                    "acknowledged": bool(
                        e.research_only and _licence.acknowledged(self.data_dir, e)
                    ),
                    "facilities": ingested.get(e.id, []),
                    "report": bool(sugg.get("config_template")),
                    "exercise": sugg.get("exercise"),
                    "data_issues": len(e.data_issues),
                }
            )
        return {
            "mode": self.mode,
            "store": self.store.root,
            "workspace": self.portfolio.root if self.portfolio is not None else None,
            "data_dir": self.data_dir,
            "free_bytes": {
                "data_dir": _free_bytes(self.data_dir),
                "store": _free_bytes(self.store.root),
            },
            "busy": self.jobs.busy(),
            "datasets": rows,
        }

    # ------------------------------------------------------------------ jobs

    def _check_room(self) -> None:
        pending = sum(1 for j in self.jobs.jobs() if j.state in ("queued", "running"))
        if pending >= MAX_PENDING_JOBS:
            raise LabError(429, f"{pending} jobs are already queued; wait or cancel some")

    def submit_fetch(self, ids, *, subset=None, ingest=False, acknowledge=None):
        """Queue one fetch job (``ingest=True``: fetch, then ingest) for ``ids``; returns it.

        A research-only id needs ``acknowledge[id] == id`` (the id typed in the modal) on every
        fetch, as the CLI needs ``--accept-noncommercial`` on every fetch: else 403.
        """
        entries = self._targets(ids, subset)
        ack = acknowledge or {}
        for e in entries:
            if e.manual:
                raise LabError(
                    400,
                    f"{e.id} is a manual download: CAMBER does not fetch it. Download the files "
                    f"yourself, then `camber datasets ingest {e.id} --from-dir DIR --store STORE`",
                )
            if e.research_only and ack.get(e.id) != e.id:
                raise LabError(403, _refusal(e, "download"))
        self._check_room()
        accepted = {e.id for e in entries if e.research_only}
        params = {"ids": [e.id for e in entries], "subset": subset or "default", "ingest": ingest}

        def work(job):
            out = []
            for e in entries:
                rec = {"id": e.id, "fetch": self._fetch_one(job, e, subset, e.id in accepted)}
                if ingest:
                    rec["ingest"] = self._ingest_one(job, e, subset, accept=False)
                out.append(rec)
            return out

        return self.jobs.submit("fetch+ingest" if ingest else "fetch", params, work)

    def submit_ingest(self, ids, *, subset=None, force=False, acknowledge=None):
        """Queue one ingest job for already-fetched ``ids``; returns it.

        A research-only id needs a recorded acknowledgement of its current licence (from a fetch)
        or ``acknowledge[id] == id``: else 403.
        """
        from ..datasets import _licence

        entries = self._targets(ids, subset)
        ack = acknowledge or {}
        accept = set()
        for e in entries:
            if not e.research_only:
                continue
            if ack.get(e.id) == e.id:
                accept.add(e.id)
            elif not _licence.acknowledged(self.data_dir, e):
                raise LabError(403, _refusal(e, "ingest"))
        self._check_room()
        params = {"ids": [e.id for e in entries], "subset": subset or "default", "force": force}

        def work(job):
            return [
                {
                    "id": e.id,
                    "ingest": self._ingest_one(job, e, subset, accept=e.id in accept, force=force),
                }
                for e in entries
            ]

        return self.jobs.submit("ingest", params, work)

    def cancel(self, job_id: str) -> dict:
        """Cancel a job; :class:`LabError` 404 when it is unknown."""
        job = self.jobs.cancel(job_id) if _JOB_ID.match(job_id or "") else None
        if job is None:
            raise LabError(404, f"no job {job_id!r}")
        return job.view()

    def job(self, job_id: str) -> dict:
        """One job's view; :class:`LabError` 404 when it is unknown."""
        job = self.jobs.get(job_id) if _JOB_ID.match(job_id or "") else None
        if job is None:
            raise LabError(404, f"no job {job_id!r}")
        return job.view()

    def _targets(self, ids, subset) -> list:
        if not isinstance(ids, list) or not ids:
            raise LabError(400, "ids must be a non-empty list of catalog ids")
        if len(ids) != len(set(map(str, ids))):
            raise LabError(400, "ids must not repeat")
        entries = [self.entry(i) for i in ids]
        if subset is not None:
            if not isinstance(subset, str):
                raise LabError(400, "subset must be a string")
            for e in entries:
                if subset not in e.subsets:
                    raise LabError(
                        400, f"{e.id}: unknown subset {subset!r} (known: {sorted(e.subsets)})"
                    )
        return entries

    def _audit(self, action: str, *, reason: str, facility_id=None, details=None) -> None:
        if self.portfolio is not None:
            self.portfolio.audit(
                action, reason=reason, facility_id=facility_id, details=details or {}
            )

    def _fetch_one(self, job, entry, subset, accepted: bool) -> dict:
        from ..datasets._ops import fetch_dataset

        job.report(f"{entry.id}: fetching")

        def progress(name, done, total, _id=entry.id):
            job.report(f"{_id}: downloading {name}", done, total)

        res = fetch_dataset(
            entry,
            subset=subset,
            data_dir=self.data_dir,
            accept_noncommercial=accepted,
            progress=progress,
            opener=self.opener,
            via="lab fetch",
        )
        if accepted:
            self._audit(
                "lab.acknowledge",
                reason=f"research-only licence {entry.licence} acknowledged in camber lab",
                details={"dataset_id": entry.id, "licence": entry.licence, "subset": res.subset},
            )
        self._audit(
            "lab.fetch",
            reason=f"camber lab fetched dataset {entry.id}",
            details={
                "dataset_id": entry.id,
                "subset": res.subset,
                "downloaded_bytes": res.downloaded_bytes,
                "files": [f["name"] for f in res.files],
            },
        )
        job.report(f"{entry.id}: fetched")
        return {
            "subset": res.subset,
            "downloaded_bytes": res.downloaded_bytes,
            "files": [
                {k: f.get(k) for k in ("name", "bytes", "sha256", "skipped")} for f in res.files
            ],
            "citation": res.citation,
            "licence": res.licence,
            "warnings": list(res.warnings),
        }

    def _ingest_one(self, job, entry, subset, *, accept: bool, force: bool = False) -> dict:
        from ..datasets._ingest import ingest_dataset

        def progress(msg):
            job.report(str(msg))

        kw = dict(
            subset=subset,
            data_dir=self.data_dir,
            force=force,
            progress=progress,
            accept_noncommercial=accept,
            via="lab ingest",
        )
        job.report(f"{entry.id}: ingesting")
        if self.portfolio is None:
            res = ingest_dataset(entry, self.store, **kw)
        else:
            res = self._ingest_in_workspace(job, entry, kw)
        job.report(f"{entry.id}: {'up to date' if res.skipped else 'ingested'}")
        out = res.as_dict()
        out.pop("store", None)
        return out

    def _ingest_in_workspace(self, job, entry, kw):
        """Ingest under the workspace lock: register ``provisioning``, ingest, activate, audit."""
        from ..datasets._ingest import dataset_meta, ingest_dataset

        pf = self.portfolio
        reason = f"camber lab ingest of dataset {entry.id}"
        with pf.lock(timeout=0.0):  # a concurrent admin command is refused, not waited on
            reg = pf.registry
            known = reg.all()
            base = entry.ingest.get("facility")
            mine = {
                fid
                for fid, m in known.items()
                if dataset_meta(m).get("dataset_id") == entry.id or fid == base
            }
            for fid in sorted(mine):
                state = known[fid].get("state") or "active"
                if state not in INGESTABLE_STATES:
                    raise PermissionError(
                        f"facility {fid} is {state}: the lab ingests only into provisioning or "
                        "active facilities (use `camber facility resume|restore` first)"
                    )
            single = entry.ingest.get("adapter") != "bdg2"  # BDG2: one facility per site
            if (
                single
                and base
                and valid_facility_id(base)
                and base not in known
                and base not in reg.tombstones()
            ):
                pf.add_facility(entry.title, facility_id=base, reason=reason)
            res = ingest_dataset(entry, self.store, **kw)
            for fid in res.facilities:
                if pf.registry.state(fid) == "provisioning":
                    pf.transition(fid, "activate", reason=reason)
                pf.audit(
                    "lab.ingest",
                    reason=reason,
                    facility_id=fid,
                    details={
                        "dataset_id": entry.id,
                        "subset": res.subset,
                        "rows": res.rows,
                        "skipped": res.skipped,
                        "content_hash": res.content_hash,
                        "corrections": res.corrections,
                    },
                )
        return res

    # ------------------------------------------------------------------ reports

    def report_html(self, facility_id: str) -> str:
        """The audit report for an ingested dataset facility, built on demand from the dataset's
        config template (cached per content hash). :class:`LabError` 404 when ``facility_id`` is
        not an ingested dataset facility, 409 when its dataset has no config template."""
        from ..datasets._ingest import dataset_meta

        if not valid_facility_id(facility_id or "") or facility_id not in self.store.facilities():
            raise LabError(404, f"no ingested facility {facility_id!r}")
        meta = dataset_meta(self.store.facilities_meta().get(facility_id) or {})
        if not meta.get("dataset_id"):
            raise LabError(404, f"{facility_id} is not a catalog dataset facility")
        entry = self.entry(meta["dataset_id"])
        if not (entry.suggested_analyses or {}).get("config_template"):
            raise LabError(409, f"{entry.id} has no config template to build a report from")
        key = (facility_id, meta.get("content_hash"))
        with self._report_lock:  # one build at a time: a report runs the whole rule set
            html = self._reports.get(key)
            if html is None:
                try:
                    html = self._build_report(entry, facility_id)
                except Exception as exc:  # shown to the local user, who can fix the store/config
                    raise LabError(500, f"report for {facility_id} failed: {exc}") from exc
                self._reports = {k: v for k, v in self._reports.items() if k[0] != facility_id}
                self._reports[key] = html
        return html

    def _build_report(self, entry, facility_id: str) -> str:
        from ..config import data_sources, run_config
        from ..datasets._ops import build_config
        from ..report.audit import AuditReport

        cfg = build_config(entry, self.store, facility_id=facility_id)
        base = self.store.root
        res = run_config(cfg, base_dir=base)
        report = res.report
        if report is None:  # pragma: no cover - run_config always builds one for a store config
            report = AuditReport(
                building=res.site, level=2, data_sources=data_sources(cfg, base_dir=base)
            )
            report.add_findings(res.findings)
        return report.to_html(recommend=True)


def _refusal(entry, action: str) -> str:
    why = (
        f"is held research-only although its licence is {entry.licence} ({entry.access_reason})"
        if entry.access_reason
        else f"is licensed {entry.licence}"
    )
    return (
        f"{entry.id} {why}: research / non-commercial use only, no redistribution. Acknowledge "
        f"the licence (type the dataset id in the dialog) to {action} it."
    )
