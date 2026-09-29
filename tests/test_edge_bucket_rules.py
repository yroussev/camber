"""Bucket lifecycle rules from a retention policy (camber.edge.bucket_rules, 0.95, #18)."""

import ast
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.edge.bucket_rules as br  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.edge.bucket_rules import (  # noqa: E402
    bucket_lifecycle_rules,
    normalize_policy,
    policy_from_portfolio,
    rule_days,
)
from camber.portfolio import DEFAULT_POLICY, Portfolio  # noqa: E402

A, B, C = "north-annex-1a2b3c", "east-wing-4d5e6f", "south-hall-778899"


def test_rule_days_is_conservative():
    assert rule_days({"keep_days": 10}) == 10
    assert rule_days({"keep_months": 25}) == 25 * 31  # never earlier than 25 calendar months
    assert rule_days({"keep_years": 7}) == 7 * 366
    for keep in ("indefinite", "forever", "equipment_life", "legal_hold"):
        assert rule_days({"keep": keep}) is None
    for bad in ({"keep_months": 0}, {"keep_days": 1.5}, {"keep_years": True}, {"keep": "?"}, 3):
        with pytest.raises(ValueError):
            rule_days(bad)


def test_normalize_policy_shapes():
    assert normalize_policy(DEFAULT_POLICY)["defaults"] == DEFAULT_POLICY
    doc = {"retention": {"defaults": {"raw_trends": {"keep_days": 5}}}, "legal_holds": {A: {}}}
    n = normalize_policy(doc)
    assert n["legal_holds"] == {A: {}} and n["overrides"] == {}
    assert normalize_policy({"defaults": {}, "overrides": {A: {}}})["overrides"] == {A: {}}
    with pytest.raises(ValueError):
        normalize_policy([1])


def test_bucket_wide_s3_rules_from_the_default_policy():
    out = bucket_lifecycle_rules(DEFAULT_POLICY, provider="s3", prefix="lake/")
    rules = out["document"]["Rules"]
    by_prefix = {r["Filter"]["Prefix"]: r["Expiration"]["Days"] for r in rules}
    assert by_prefix == {"lake/facility_id=": 775, "lake/rollups/1h/facility_id=": 2562}
    assert all(r["Status"] == "Enabled" and len(r["ID"]) <= 255 for r in rules)
    notes = " ".join(out["notes"])
    assert "daily_rollups: kept indefinite" in notes and "findings: not stored" in notes
    assert "_quarantine/" in notes and "REPLACES" in notes
    assert out["apply_with"].startswith("aws s3api")


def test_overrides_and_holds_need_per_facility_rules():
    pol = {
        "defaults": DEFAULT_POLICY,
        "overrides": {B: {"raw_trends": {"keep_months": 36}}},
        "legal_holds": {C: {"reason": "litigation"}},
    }
    with pytest.raises(ValueError, match="facility list"):
        bucket_lifecycle_rules(pol, provider="s3")
    out = bucket_lifecycle_rules(pol, provider="s3", facilities=[A, B, C])
    raw = {
        r["Filter"]["Prefix"]: r["Expiration"]["Days"]
        for r in out["document"]["Rules"]
        if "rollups" not in r["Filter"]["Prefix"]
    }
    assert raw == {f"facility_id={A}/": 775, f"facility_id={B}/": 36 * 31}
    # the held facility has no rule anywhere, and the note says to use the provider's own hold
    assert not any(C in r["Filter"]["Prefix"] for r in out["document"]["Rules"])
    assert any("legal hold" in n and C in n for n in out["notes"])


def test_gcs_groups_prefixes_by_age():
    pol = {"defaults": DEFAULT_POLICY, "overrides": {B: {"raw_trends": {"keep_days": 30}}}}
    out = bucket_lifecycle_rules(pol, provider="gcs", facilities=[A, B, C], prefix="lake")
    rules = out["document"]["rule"]
    raw = [r for r in rules if not r["condition"]["matchesPrefix"][0].startswith("lake/rollups")]
    assert {r["condition"]["age"]: r["condition"]["matchesPrefix"] for r in raw} == {
        30: [f"lake/facility_id={B}/"],
        775: [f"lake/facility_id={A}/", f"lake/facility_id={C}/"],
    }
    assert all(r["action"] == {"type": "Delete"} for r in rules)


def test_azure_needs_a_container_and_chunks_prefixes():
    with pytest.raises(ValueError, match="container"):
        bucket_lifecycle_rules(DEFAULT_POLICY, provider="azure")
    facs = [f"bldg-{i:02d}-aaaaaa" for i in range(12)]
    out = bucket_lifecycle_rules(
        DEFAULT_POLICY, provider="azure", container="lake", facilities=facs
    )
    rules = out["document"]["rules"]
    raw = [r for r in rules if "RawTrends" in r["name"]]
    assert [len(r["definition"]["filters"]["prefixMatch"]) for r in raw] == [10, 2]
    assert raw[0]["definition"]["filters"]["prefixMatch"][0] == "lake/facility_id=bldg-00-aaaaaa/"
    assert raw[0]["definition"]["actions"]["baseBlob"]["delete"] == {
        "daysAfterModificationGreaterThan": 775
    }
    assert all(r["name"].isalnum() for r in rules)
    assert len({r["name"] for r in rules}) == len(rules)


def test_rule_limits_and_edge_cases():
    facs = [f"bldg-{i:03d}-aaaaaa" for i in range(110)]
    pol = {
        "defaults": DEFAULT_POLICY,
        "overrides": {f: {"raw_trends": {"keep_days": 100 + i}} for i, f in enumerate(facs)},
    }
    with pytest.raises(ValueError, match="limit"):
        bucket_lifecycle_rules(pol, provider="gcs", facilities=facs + ["x-1"])
    with pytest.raises(ValueError, match="unknown provider"):
        bucket_lifecycle_rules(DEFAULT_POLICY, provider="ftp")
    only_daily = {"daily_rollups": {"keep": "indefinite"}}
    out = bucket_lifecycle_rules(only_daily, provider="s3", facilities=[A])
    assert out["document"] == {"Rules": []}
    assert any("raw_trends: not in the policy" in n for n in out["notes"])
    assert any("daily_rollups: no facility's data expires" in n for n in out["notes"])
    custom = bucket_lifecycle_rules(
        {"raw_trends": {"keep_days": 9}, "hourly_rollups": {"keep_days": 9}},
        provider="s3",
        class_prefixes={"hourly_rollups": "agg/hourly/"},
    )
    assert {r["Filter"]["Prefix"] for r in custom["document"]["Rules"]} == {
        "facility_id=",
        "agg/hourly/facility_id=",
    }


def test_never_imports_a_cloud_sdk_or_a_network_module():
    tree = ast.parse(open(br.__file__, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not mods & {"boto3", "botocore", "google", "azure", "urllib", "http", "requests"}
    assert mods <= {"__future__", "copy", "re"}


def test_policy_from_portfolio_uses_the_portfolio_api(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("North", facility_id=A, reason="t", activate=True)
    pf.add_facility("East", facility_id=B, reason="t", activate=True)
    p = os.path.join(pf.root, "_portfolio.json")
    doc = json.load(open(p))
    doc["retention"]["overrides"] = {B: {"raw_trends": {"keep_months": 12}}}
    doc["legal_holds"] = {A: {"reason": "litigation"}}
    json.dump(doc, open(p, "w"))
    pol, facs = policy_from_portfolio(pf)
    assert facs == [B, A] or facs == sorted([A, B])
    assert pol["overrides"] == {B: {"raw_trends": {"keep_months": 12}}}
    assert A in pol["legal_holds"] and pol["defaults"]["raw_trends"] == {"keep_months": 25}
    out = bucket_lifecycle_rules(pol, provider="s3", facilities=facs)
    assert [r["Filter"]["Prefix"] for r in out["document"]["Rules"]][0] == f"facility_id={B}/"


def test_cli_bucket_rules(tmp_path, capsys, monkeypatch):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("North", facility_id=A, reason="t", activate=True)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    assert main(["edge", "bucket-rules", "--provider", "s3"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing was sent" in out and "aws s3api" in out
    assert f"facility_id={A}/" in out
    dest = tmp_path / "rules.json"
    assert main(["edge", "bucket-rules", "--provider", "gcs", "--out", str(dest)]) == 0
    assert "wrote" in capsys.readouterr().out and json.loads(dest.read_text())["rule"]
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(DEFAULT_POLICY))
    rc = main(["edge", "bucket-rules", "--provider", "azure", "--policy", str(pol), "--json"])
    assert rc == 1 and "container" in capsys.readouterr().err
    rc = main(
        ["edge", "bucket-rules", "--provider", "azure", "--policy", str(pol), "--container",
         "lake", "--json"]
    )  # fmt: skip
    assert rc == 0 and json.loads(capsys.readouterr().out)["rules"]
