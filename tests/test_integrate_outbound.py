"""Tests for outbound integrations (camber.integrate.notify + export).

No network/SMTP: transports are injected fakes, the email path uses a fake SMTP factory, and
exports go to tmp files.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.integrate.export import export_findings, findings_to_frame  # noqa: E402
from camber.integrate.notify import (  # noqa: E402
    dispatch_findings,
    email_transport,
    format_for,
    slack_payload,
    teams_payload,
)
from camber.integrate.tickets import finding_to_ticket  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, equip, sev, **m):
    return Finding(rule=rule, equip=equip, severity=sev, metrics=m, summary=f"{rule} on {equip}")


_FINDINGS = [
    _f("simultaneous_heat_cool", "AHU-1", "fault", simultaneous_hc_pct=22.0),
    _f("chiller_efficiency", "CH-1", "warn", kw_per_ton_median=0.9),
    _f("co2_ventilation", "VAV-3", "info", co2_median_ppm=600),
]


# --------------------------------------------------------------------------- channels


def test_slack_and_teams_payload_shape():
    t = finding_to_ticket(_FINDINGS[0], site="S1")
    slack = slack_payload(t)
    assert "text" in slack and "simultaneous_heat_cool" in slack["text"]
    teams = teams_payload(t)
    assert teams["@type"] == "MessageCard" and teams["sections"][0]["text"]


def test_format_for_dispatch_and_unknown_channel():
    t = finding_to_ticket(_FINDINGS[1], site="S1")
    assert format_for("webhook", t) is t  # raw passthrough
    assert "text" in format_for("slack", t)
    try:
        format_for("carrier-pigeon", t)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# --------------------------------------------------------------------------- dispatch


def test_dispatch_severity_filter():
    sent = []
    dispatch_findings(_FINDINGS, sent.append, min_severity="warn", dedupe=False)
    # the "info" co2 finding is dropped; fault + warn go out
    rules = {p["rule"] for p in sent}
    assert rules == {"simultaneous_heat_cool", "chiller_efficiency"}


def test_dispatch_dedupe_across_runs():
    seen = set()
    sent = []
    n1 = dispatch_findings(_FINDINGS, sent.append, seen=seen, min_severity="warn")
    assert len(n1) == 2 and len(seen) == 2
    # same findings next run -> nothing new sent
    n2 = dispatch_findings(_FINDINGS, sent.append, seen=seen, min_severity="warn")
    assert n2 == [] and len(sent) == 2


def test_dispatch_dry_run_does_not_call_transport():
    calls = []
    out = dispatch_findings(_FINDINGS, calls.append, dry_run=True, dedupe=False)
    assert len(out) == 2 and calls == []  # formatted but not sent


def test_dispatch_slack_channel_formats_payloads():
    sent = []
    dispatch_findings(_FINDINGS, sent.append, channel="slack", min_severity="fault", dedupe=False)
    assert len(sent) == 1 and "text" in sent[0]  # only the fault, slack-shaped


# --------------------------------------------------------------------------- email


class _FakeSMTP:
    log = []

    def starttls(self):
        self.log.append("starttls")

    def login(self, u, p):
        self.log.append(("login", u))

    def send_message(self, msg):
        self.log.append(("send", msg["Subject"], msg["To"]))

    def quit(self):
        self.log.append("quit")


def test_email_transport_sends_via_fake_smtp():
    _FakeSMTP.log = []
    fake = _FakeSMTP()
    transport = email_transport(
        "smtp.example.com",
        sender="camber@x.com",
        recipients=["ops@x.com", "fm@x.com"],
        username="u",
        password="p",
        _smtp_factory=lambda: fake,
    )
    ticket = finding_to_ticket(_FINDINGS[0], site="S1")
    res = transport(ticket)
    assert res["ok"] and res["recipients"] == ["ops@x.com", "fm@x.com"]
    kinds = [e[0] if isinstance(e, tuple) else e for e in _FakeSMTP.log]
    assert "starttls" in kinds and "login" in kinds and "send" in kinds and "quit" in kinds


# --------------------------------------------------------------------------- export


def test_findings_to_frame_flattens_metrics():
    df = findings_to_frame(_FINDINGS, site="S1")
    assert list(df["rule"]) == ["simultaneous_heat_cool", "chiller_efficiency", "co2_ventilation"]
    assert "metric_simultaneous_hc_pct" in df.columns
    assert df.loc[0, "metric_simultaneous_hc_pct"] == 22.0
    assert df.loc[0, "fingerprint"]  # stable id present


def test_export_csv_json_parquet(tmp_path):
    for ext in ("csv", "json", "parquet"):
        p = tmp_path / f"findings.{ext}"
        n = export_findings(_FINDINGS, str(p), site="S1")
        assert n == 3 and p.exists()
    # JSON is record-oriented and round-trips
    recs = json.load(open(tmp_path / "findings.json"))
    assert len(recs) == 3 and recs[0]["rule"] == "simultaneous_heat_cool"


def test_export_columns_subset_and_bad_format(tmp_path):
    df = findings_to_frame(_FINDINGS, columns=["rule", "severity"])
    assert list(df.columns) == ["rule", "severity"]
    try:
        export_findings(_FINDINGS, str(tmp_path / "x.xml"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
