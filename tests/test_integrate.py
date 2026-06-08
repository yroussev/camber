"""Tests for outbound integration: findings -> tickets + notifications."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.integrate import (  # noqa: E402
    Notifier, collect_transport, finding_to_ticket, findings_to_tickets,
)
from camber.integrate.tickets import fingerprint  # noqa: E402


class _F:
    """A minimal finding-like object (duck-typed)."""
    def __init__(self, rule, equip, severity, summary="", metrics=None):
        self.rule = rule
        self.equip = equip
        self.severity = severity
        self.summary = summary
        self.metrics = metrics or {}


def test_finding_to_ticket_fields_and_priority():
    f = _F("simul_hc", "AHU_2", "fault", "heating fighting cooling",
           {"simultaneous_hc_pct": 1.2})
    t = finding_to_ticket(f, site="DemoSite")
    assert t["priority"] == "high"          # fault -> high
    assert t["equip"] == "AHU_2"
    assert t["rule"] == "simul_hc"
    assert t["status"] == "open"
    assert t["location"] == "DemoSite / AHU_2"
    assert t["metrics"]["simultaneous_hc_pct"] == 1.2


def test_severity_priority_mapping():
    assert finding_to_ticket(_F("r", "e", "warn"))["priority"] == "medium"
    assert finding_to_ticket(_F("r", "e", "info"))["priority"] == "low"
    assert finding_to_ticket(_F("r", "e", "ok"))["priority"] == "low"


def test_ticket_is_json_serializable():
    t = finding_to_ticket(_F("r", "e", "fault", "x", {"a": 1.0}), site="S")
    assert json.loads(json.dumps(t))["rule"] == "r"


def test_fingerprint_stable_and_specific():
    a = fingerprint("S", "AHU_1", "simul_hc")
    assert a == fingerprint("S", "AHU_1", "simul_hc")     # stable across calls
    assert a != fingerprint("S", "AHU_2", "simul_hc")     # equip-specific
    assert a != fingerprint("S", "AHU_1", "sat_reset")    # rule-specific


def test_finding_from_dict_also_works():
    t = finding_to_ticket({"rule": "r", "equip": "e", "severity": "warn"})
    assert t["priority"] == "medium"


def test_findings_to_tickets_filters_non_actionable():
    findings = [_F("a", "e1", "fault"), _F("b", "e2", "ok"),
                _F("c", "e3", "warn"), _F("d", "e4", "info")]
    actionable = findings_to_tickets(findings, site="S")
    assert {t["rule"] for t in actionable} == {"a", "c"}    # fault + warn only
    everything = findings_to_tickets(findings, site="S", actionable_only=False)
    assert len(everything) == 4


def test_notifier_default_collects():
    n = Notifier()
    findings = [_F("a", "e1", "fault"), _F("b", "e2", "warn")]
    sent = n.emit_findings(findings, site="S")
    assert n.sent == 2
    assert len(sent) == 2
    assert len(n.collected) == 2                # captured by the collector
    assert n.collected[0]["rule"] == "a"


def test_notifier_with_injected_transport():
    transport, sink = collect_transport()
    n = Notifier(transport=transport)
    n.emit_findings([_F("a", "e1", "fault")], site="S")
    assert len(sink) == 1
    assert n.collected is None                  # no default sink when injected
    assert sink[0]["priority"] == "high"
