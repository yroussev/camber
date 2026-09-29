"""Portfolio lifecycle (provisional): a workspace, facility states, an audit log and a lock.

A **portfolio workspace** is one directory holding a portfolio's Parquet store, its retention
policy, an append-only audit log and a single-writer lock, so every facility's footprint is
enumerable and every change is attributable. Facilities move through a lifecycle::

    provisioning -> active <-> suspended -> offboarding -> archived -> purged (tombstone)

The workspace, registry v2 (states, editable display names, tombstones), the audit log, the lock
and every transition are implemented; since 0.95 ``offboard`` / ``archive`` / ``restore`` /
``purge`` run the export-bundle cascade (``archive/<fid>/`` bundles with a sha256 manifest,
crash-safe swaps and recovery). See docs/PORTFOLIO.md. **Provisional**: names may change in a
minor release.
"""

from __future__ import annotations

from ._lock import PortfolioLocked
from ._states import DELETING, STATES, TRANSITIONS, LifecycleError, allowed_actions, transition
from ._workspace import DEFAULT_POLICY, Portfolio, find_workspace, is_workspace

__all__ = [
    "Portfolio",
    "PortfolioLocked",
    "LifecycleError",
    "STATES",
    "TRANSITIONS",
    "DELETING",
    "DEFAULT_POLICY",
    "allowed_actions",
    "transition",
    "find_workspace",
    "is_workspace",
]
