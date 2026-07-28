"""Outbound notification channels, severity filtering, and cross-run dedupe.

Builds on :mod:`camber.integrate.tickets` (ticket render + webhook/collect transports). Adds:

- **channel formatters** that shape a neutral ticket into a provider payload — Slack and
  Microsoft Teams incoming webhooks, plus a raw passthrough for a generic JSON webhook;
- an **email** transport (stdlib :mod:`smtplib`, injectable for tests); and
- a **severity filter** + **cross-run dedupe** (by the stable finding fingerprint) so only new,
  actionable findings go out.

All of this pushes findings to people / ticketing / chat — it is read-only toward the BAS and
never writes to a controller. The high-level entry point is :func:`dispatch_findings`.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .tickets import findings_to_tickets

# Severity ordering for the min-severity filter.
_RANK = {"ok": 0, "info": 1, "warn": 2, "fault": 3}
_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🔵"}


def _rank(sev: str) -> int:
    return _RANK.get(sev, 1)


def slack_payload(ticket: dict) -> dict:
    """Shape a ticket into a Slack incoming-webhook payload (`{"text": ...}`)."""
    emoji = _PRIORITY_EMOJI.get(ticket.get("priority", "low"), "•")
    where = ticket.get("location") or ticket.get("equip") or "building"
    return {
        "text": f"{emoji} *{ticket.get('title', 'finding')}*\n{ticket.get('body', '')}\n"
        f"_location:_ {where}"
    }


def teams_payload(ticket: dict) -> dict:
    """Shape a ticket into a Microsoft Teams MessageCard payload."""
    colors = {"high": "D7263D", "medium": "F46036", "low": "2E86AB"}
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": colors.get(ticket.get("priority", "low"), "888888"),
        "summary": ticket.get("title", "CAMBER finding"),
        "sections": [
            {
                "activityTitle": ticket.get("title", "CAMBER finding"),
                "text": ticket.get("body", ""),
                "facts": [
                    {"name": "location", "value": ticket.get("location", "")},
                    {"name": "priority", "value": ticket.get("priority", "")},
                    {"name": "rule", "value": ticket.get("rule", "")},
                ],
            }
        ],
    }


_CHANNELS = {"webhook": lambda t: t, "slack": slack_payload, "teams": teams_payload}


def format_for(channel: str, ticket: dict) -> dict:
    """Format a ticket for ``channel`` ("webhook" | "slack" | "teams")."""
    if channel not in _CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; use one of {sorted(_CHANNELS)}")
    return _CHANNELS[channel](ticket)


def email_transport(
    host: str,
    *,
    port: int = 587,
    sender: str,
    recipients,
    use_tls: bool = True,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 30.0,
    _smtp_factory=None,
):
    """A transport that emails each ticket (subject = title, body = body).

    Pass it as the ``transport`` to :func:`dispatch_findings` with ``channel="webhook"`` (the raw
    ticket carries ``title``/``body``). ``_smtp_factory`` (default :class:`smtplib.SMTP`) is
    injectable so the send path is testable without a server.
    """
    recips = [recipients] if isinstance(recipients, str) else list(recipients)
    factory = _smtp_factory or (lambda: smtplib.SMTP(host, port, timeout=timeout))

    def transport(ticket: dict) -> dict:
        msg = EmailMessage()
        msg["Subject"] = ticket.get("title", "CAMBER finding")
        msg["From"] = sender
        msg["To"] = ", ".join(recips)
        msg.set_content(ticket.get("body", ""))
        smtp = factory()
        try:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(msg)
        finally:
            smtp.quit()
        return {"ok": True, "recipients": recips}

    return transport


def dispatch_findings(
    findings,
    transport,
    *,
    channel: str = "webhook",
    min_severity: str = "warn",
    site: str = "",
    seen: set | None = None,
    dedupe: bool = True,
    dry_run: bool = False,
    source: str = "camber",
) -> list:
    """Render findings to tickets, filter by severity, dedupe, format, and send.

    ``transport`` is a callable taking the formatted payload (e.g. ``webhook_transport(url)``,
    ``email_transport(...)``, or ``collect_transport()[0]``). Option flags:

    - ``channel`` — payload shape ("webhook" raw / "slack" / "teams").
    - ``min_severity`` — drop findings below this ("ok"/"info"/"warn"/"fault").
    - ``seen`` — a set of fingerprints already notified; when given and ``dedupe``, only new
      findings are sent and ``seen`` is updated in place (persist it across runs).
    - ``dry_run`` — format but don't call ``transport`` (returns what *would* be sent).

    Returns the list of payloads sent (or that would be sent under ``dry_run``).
    """
    tickets = findings_to_tickets(findings, site=site, actionable_only=False, source=source)
    tickets = [t for t in tickets if _rank(t.get("severity", "info")) >= _rank(min_severity)]
    if dedupe and seen is not None:
        fresh = []
        for t in tickets:
            fp = t.get("fingerprint")
            if fp in seen:
                continue
            seen.add(fp)
            fresh.append(t)
        tickets = fresh
    sent = []
    for t in tickets:
        payload = format_for(channel, t)
        if not dry_run:
            transport(payload)
        sent.append(payload)
    return sent
