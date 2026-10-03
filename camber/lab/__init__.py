"""``camber lab``: a loopback-only local UI for the open dataset catalog (provisional API).

A learner picks datasets from the catalog, CAMBER fetches them from their publishers and ingests
them (as background jobs with progress and cancel), and the trend viewer and audit reports open
on the ingested data. Everything goes through :mod:`camber.datasets`; with a portfolio workspace
the dataset facilities (``ds-<id>``) follow the facility lifecycle and every action is audited.

::

    from camber.lab import LabApp, make_lab_server

    app = LabApp(store="lab_store")            # or LabApp(workspace="portfolio")
    httpd = make_lab_server(app, port=8765)    # 127.0.0.1 only; any other host is refused
    httpd.serve_forever()

``camber serve`` stays GET-only; the lab is a separate server with its own request checks (Host /
Origin allowlist, CSRF token, JSON only, 16 KiB bodies, strict CSP) -- see docs/SECURITY.md.
This API is **provisional** (docs/API-STABILITY.md).
"""

from __future__ import annotations

from ._app import DEFAULT_PORT, LAB_HOST, LabApp, LabError
from ._jobs import Job, JobCancelled, JobQueue
from ._server import BODY_LIMIT, TOKEN_HEADER, dispatch_lab, make_lab_server, serve_lab

__all__ = [
    "LabApp",
    "LabError",
    "Job",
    "JobQueue",
    "JobCancelled",
    "dispatch_lab",
    "make_lab_server",
    "serve_lab",
    "LAB_HOST",
    "DEFAULT_PORT",
    "BODY_LIMIT",
    "TOKEN_HEADER",
]
