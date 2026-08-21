"""Outbound integration (capability-map §8): get findings out where they act.

An analytic that cannot reach a ticketing system or a notification channel stays
on the shelf. This package turns :class:`~camber.rules.base.Finding` results into
CMMS-ticket-shaped records and pushes them through a pluggable transport (default
collects in-memory; a stdlib webhook transport is provided), so the deterministic
core can drive action without coupling to any one vendor's API.
"""

from .export import (
    ahu_diagnoses_to_frame,
    condenser_diagnoses_to_frame,
    diagnoses_to_frame,
    export_ahu_diagnoses,
    export_condenser_diagnoses,
    export_diagnoses,
    export_findings,
    export_pump_diagnoses,
    findings_to_frame,
    pump_diagnoses_to_frame,
)
from .notify import (
    dispatch_findings,
    email_transport,
    format_for,
    slack_payload,
    teams_payload,
)
from .tickets import (
    Notifier,
    collect_transport,
    diagnoses_to_tickets,
    diagnosis_to_ticket,
    finding_to_ticket,
    findings_to_tickets,
    webhook_transport,
)

__all__ = [
    "Notifier",
    "collect_transport",
    "finding_to_ticket",
    "findings_to_tickets",
    "diagnosis_to_ticket",
    "diagnoses_to_tickets",
    "webhook_transport",
    "dispatch_findings",
    "format_for",
    "slack_payload",
    "teams_payload",
    "email_transport",
    "findings_to_frame",
    "export_findings",
    "diagnoses_to_frame",
    "export_diagnoses",
    "pump_diagnoses_to_frame",
    "export_pump_diagnoses",
    "ahu_diagnoses_to_frame",
    "export_ahu_diagnoses",
    "condenser_diagnoses_to_frame",
    "export_condenser_diagnoses",
]
