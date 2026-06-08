"""Findings -> CMMS-ticket records and a pluggable notification path.

A :class:`~camber.rules.base.Finding` is the analytic's verdict; a *ticket* is the
actionable form an operator's work-order/CMMS system understands. This module maps
one to the other (severity -> priority, a stable fingerprint for dedup, a
human-readable title/body) and routes tickets through an injectable transport so
the same code path serves a real webhook, an email gateway, or an in-memory
collector under test.

There is no universal CMMS schema, so the ticket dict is a neutral, JSON-
serializable shape that a thin per-vendor adapter can remap. Finding objects are
duck-typed (``rule``/``equip``/``severity``/``metrics``/``summary``) so any
finding-like result works without importing the rules layer.
"""

from __future__ import annotations

import hashlib
import json
from urllib import request as _request

# Map a Finding severity to a CMMS priority. "ok" produces no ticket.
SEVERITY_TO_PRIORITY = {"fault": "high", "warn": "medium", "info": "low"}
_ACTIONABLE = frozenset({"fault", "warn"})


def _attr(finding, name, default=""):
    """Read an attribute or dict key from a finding-like object."""
    if isinstance(finding, dict):
        return finding.get(name, default)
    return getattr(finding, name, default)


def fingerprint(site: str, equip: str, rule: str) -> str:
    """Stable short id for dedup: same (site, equip, rule) -> same fingerprint.

    Deliberately excludes metrics/severity so a recurring issue updates one
    ticket rather than spawning a new one each run.
    """
    raw = f"{site}\x1f{equip}\x1f{rule}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def finding_to_ticket(finding, *, site: str = "", source: str = "camber") -> dict:
    """Render one finding as a neutral, JSON-serializable CMMS ticket dict."""
    rule = _attr(finding, "rule")
    equip = _attr(finding, "equip")
    severity = _attr(finding, "severity", "info")
    summary = _attr(finding, "summary", "")
    metrics = _attr(finding, "metrics", {}) or {}
    priority = SEVERITY_TO_PRIORITY.get(severity, "low")
    where = f"{site} / {equip}".strip(" /")
    title = f"[{priority.upper()}] {rule} on {equip or 'building'}".strip()
    body = summary or f"{rule} flagged {severity} on {equip}."
    return {
        "fingerprint": fingerprint(site, equip, rule),
        "title": title,
        "body": body,
        "priority": priority,
        "status": "open",
        "site": site,
        "equip": equip,
        "rule": rule,
        "severity": severity,
        "metrics": metrics,
        "source": source,
        "location": where,
    }


def findings_to_tickets(findings, *, site: str = "", actionable_only: bool = True,
                        source: str = "camber") -> list:
    """Map many findings to tickets, by default dropping non-actionable ones.

    ``actionable_only`` keeps just ``fault``/``warn`` severities (the ones an
    operator should triage); set False to emit every finding.
    """
    out = []
    for f in findings:
        sev = _attr(f, "severity", "info")
        if actionable_only and sev not in _ACTIONABLE:
            continue
        out.append(finding_to_ticket(f, site=site, source=source))
    return out


def collect_transport():
    """A no-network transport that records payloads; returns (transport, sink).

    The default for tests and dry runs: ``sink`` is the list of every payload the
    transport was asked to send.
    """
    sink: list = []

    def transport(payload: dict) -> dict:
        """Record the payload in the sink instead of sending it anywhere."""
        sink.append(payload)
        return {"ok": True, "collected": len(sink)}

    return transport, sink


def webhook_transport(url: str, *, timeout: float = 10.0):
    """A stdlib JSON-POST transport to ``url`` (no third-party HTTP dependency).

    Returns a callable suitable for :class:`Notifier`. Network errors propagate to
    the caller; swap in :func:`collect_transport` to exercise the path offline.
    """
    def transport(payload: dict) -> dict:
        """POST the payload as JSON to the configured URL."""
        data = json.dumps(payload).encode("utf-8")
        req = _request.Request(url, data=data,
                               headers={"Content-Type": "application/json"})
        with _request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return {"status": resp.status}
    return transport


class Notifier:
    """Route ticket payloads through a transport, with a count of what was sent."""

    def __init__(self, transport=None):
        if transport is None:
            transport, self._sink = collect_transport()
        else:
            self._sink = None
        self.transport = transport
        self.sent = 0

    def send(self, payload: dict) -> dict:
        """Send one ticket payload via the transport; bump the sent counter."""
        result = self.transport(payload)
        self.sent += 1
        return result

    def emit_findings(self, findings, *, site: str = "",
                      actionable_only: bool = True) -> list:
        """Convert findings to tickets and send each; returns the tickets sent."""
        tickets = findings_to_tickets(findings, site=site,
                                      actionable_only=actionable_only)
        for t in tickets:
            self.send(t)
        return tickets

    @property
    def collected(self):
        """Payloads captured by the default collector (None if a transport was given)."""
        return self._sink
