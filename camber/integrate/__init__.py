"""Outbound integration (capability-map §8): get findings out where they act.

An analytic that cannot reach a ticketing system or a notification channel stays
on the shelf. This package turns :class:`~camber.rules.base.Finding` results into
CMMS-ticket-shaped records and pushes them through a pluggable transport (default
collects in-memory; a stdlib webhook transport is provided), so the deterministic
core can drive action without coupling to any one vendor's API.
"""

from .tickets import (
    Notifier, collect_transport, finding_to_ticket, findings_to_tickets,
    webhook_transport,
)

__all__ = [
    "Notifier", "collect_transport", "finding_to_ticket", "findings_to_tickets",
    "webhook_transport",
]
