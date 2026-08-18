"""Tests for the chiller roll-up -> CMMS ticket / notify path (camber.integrate.tickets)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerdiag import diagnose_chiller_drift  # noqa: E402
from camber.integrate.tickets import (  # noqa: E402
    Notifier,
    diagnoses_to_tickets,
    diagnosis_to_ticket,
)
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="CH_1", severity=severity, metrics=metrics)


def _machine_wide(equip="CH_1"):
    return diagnose_chiller_drift(
        [
            _f("chiller_head_pressure_drift", "fault", head_pressure_drift_psi=8.0),
            _f(
                "chiller_superheat_drift",
                "fault",
                superheat_drift_direction="up",
                superheat_drift_f=3.0,
            ),
        ],
        equip=equip,
    )


def _condenser_warn(equip="CH_2"):
    return diagnose_chiller_drift(
        [_f("chiller_head_pressure_drift", "warn", head_pressure_drift_psi=3.0)], equip=equip
    )


def _steady(equip="CH_3"):
    return diagnose_chiller_drift([], equip=equip)


# --------------------------------------------------------------------------- one ticket


def test_diagnosis_to_ticket_shape():
    t = diagnosis_to_ticket(_machine_wide(), site="SITE")
    assert t["rule"] == "chiller_drift" and t["equip"] == "CH_1"
    assert t["severity"] == "fault" and t["priority"] == "high"
    assert t["machine_wide"] is True and t["locus"] == "whole-machine"
    assert "whole-machine" in t["title"]
    assert "causes:" in t["body"] and "condenser:" in t["body"]
    assert t["status"] == "open" and t["source"] == "camber"


def test_fingerprint_is_stable_per_machine():
    a = diagnosis_to_ticket(_machine_wide(), site="SITE")
    b = diagnosis_to_ticket(_machine_wide(), site="SITE")
    assert a["fingerprint"] == b["fingerprint"]  # recurring drift -> one ticket
    other = diagnosis_to_ticket(_machine_wide(equip="CH_9"), site="SITE")
    assert other["fingerprint"] != a["fingerprint"]


# --------------------------------------------------------------------------- filters


def test_actionable_only_drops_steady_machines():
    tickets = diagnoses_to_tickets([_machine_wide(), _steady()], site="SITE")
    assert len(tickets) == 1 and tickets[0]["equip"] == "CH_1"


def test_actionable_only_false_emits_steady_too():
    tickets = diagnoses_to_tickets([_steady()], site="SITE", actionable_only=False)
    assert len(tickets) == 1 and tickets[0]["severity"] == "ok"


def test_machine_wide_only_keeps_just_the_circuit_wide_cases():
    tickets = diagnoses_to_tickets(
        [_machine_wide(), _condenser_warn()], site="SITE", machine_wide_only=True
    )
    assert len(tickets) == 1 and tickets[0]["machine_wide"] is True


# --------------------------------------------------------------------------- notify path


def test_notifier_emits_diagnoses_through_the_transport():
    n = Notifier()  # default collect transport
    sent = n.emit_diagnoses([_machine_wide(), _condenser_warn(), _steady()], site="SITE")
    assert len(sent) == 2  # steady dropped
    assert n.sent == 2
    assert n.collected is not None and len(n.collected) == 2
    assert {t["equip"] for t in n.collected} == {"CH_1", "CH_2"}


def test_notifier_machine_wide_only_routing():
    n = Notifier()
    sent = n.emit_diagnoses(
        [_machine_wide(), _condenser_warn()], site="SITE", machine_wide_only=True
    )
    assert len(sent) == 1 and sent[0]["equip"] == "CH_1"
