"""0.111 (#304): the MCP and CLI papercuts of the 0.110.0 acceptance test.

Each test builds a tiny store in its own tmp directory; nothing needs the network.
"""

import json
import logging
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    CallContext,
    CapabilityError,
    default_registry,
)
from camber.capabilities._sanitize import name, plain  # noqa: E402
from camber.capabilities._schema import validate  # noqa: E402
from camber.model.roles import Role  # noqa: E402

FID = fx.FID
REG = default_registry()


def _meter(days, seed, *, weather=True):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    if weather:
        e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    else:  # no weather dependence and a lot of scatter: the baseline fails acceptance
        e = 50 + rng.normal(0, 40, len(idx)).clip(-45, None)
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("cap304")
    st = fx.make_store(root, meta=None)
    st.write_role_frame(
        fx.ahu_frame(seed=4), facility_id="nc-lab", equip="AHU-1", equip_class="AHU"
    )
    st.register_facility("nc-lab", name="NC Lab", dataset=fx.NC)
    st.write_role_frame(
        _meter(150, 0, weather=False), facility_id="mv-bad", equip="Meter", equip_class="METER"
    )
    mv_cfg = os.path.join(str(root), "mv.json")
    with open(mv_cfg, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "source": {"kind": "store", "store": "store", "facility_id": "mv-bad"},
                "equipment": [{"class": "METER"}],
                "mv": [
                    {
                        "class": "METER",
                        "role": "power",
                        "period": ["2024-01-01", "2024-03-31"],
                        "reporting_period": ["2024-04-01", None],
                    }
                ],
            },
            fh,
        )
    return {"root": str(root), "store": st, "mv_cfg": mv_cfg}


def _ctx(site, allowed=(FID, "nc-lab", "mv-bad"), **kw):
    return CallContext(store=site["store"].root, allowed_facilities=frozenset(allowed), **kw)


# --------------------------------------------------------------------------- rule_run / drift


def test_rule_run_headline_counts_rules_and_skipped_pairs_apart(site):
    args = {"facility_id": FID, "min_severity": "ok", "limit": 500}
    res = REG.call("rule_run", args, _ctx(site))
    head = res.facts[0]["text"]
    n_found = len({f["rule"] for f in res.data["findings"]})
    assert res.data["count"] == len(res.data["findings"])
    assert re.search(r"\d+ rules were run on 2 equipment of demo-fac and \d+ of them gave", head)
    assert f"and {n_found} of them gave findings" in head
    skipped = res.data["skipped"]
    n_pairs, n_rules = len(skipped), len({s["rule"] for s in skipped})
    assert f"{n_pairs} rule-equipment pair(s) ({n_rules} rule(s)) skipped" in head
    assert "rules ran" not in res.text


def test_drift_text_always_counts_the_declined_checks(site):
    res = REG.call(
        "drift", {"facility_id": FID, "baseline": ["2026-03-02", "2026-03-15"]}, _ctx(site)
    )
    assert "detector check(s) declined" in res.text
    assert "equipment had no detector run at all" in res.text
    assert "were not evaluated" not in res.text


def test_rule_params_says_what_runs_a_drift_detector(site):
    res = REG.call("rule_params", {}, _ctx(site))
    rows = res.data["rules"]
    assert all(r["run_with"] == ("rule_run" if r["kind"] == "rule" else "drift") for r in rows)
    drift = REG.call("rule_params", {"query": "drift", "limit": 200}, _ctx(site)).data["rules"]
    det = next(r for r in drift if r["kind"] == "drift")
    with pytest.raises(CapabilityError) as e:
        REG.call("rule_run", {"facility_id": FID, "rules": [det["rule"]]}, _ctx(site))
    assert e.value.code == "invalid_params"
    assert len(e.value.message) < 600, len(e.value.message)
    assert "drift capability" in e.value.message and "allowed values" in e.value.message


def test_a_long_enum_is_counted_not_listed_and_a_short_one_listed():
    schema = {"type": "string", "enum": [f"rule_{i}" for i in range(40)], "description": "d"}
    (msg,) = validate("rule_7x", schema)
    assert "not one of the 40 allowed values" in msg and "rule_7" in msg and msg.endswith("; d")
    assert "rule_39" not in msg
    (short,) = validate("c", {"enum": ["a", "b"]})
    assert short == "/: must be one of 'a', 'b'"


def test_text_from_an_unrecognised_class_says_what_the_warn_is_about():
    from camber.satreset import analyze_satreset

    idx = pd.date_range("2026-03-02", periods=48, freq="h")
    frame = pd.DataFrame({"SupplyAir": 52.0}, index=idx)
    res = analyze_satreset(frame, "LOGGER-1")
    assert res.verdict.startswith("COLD SAT MOST COOLING HOURS") and "NOT EVALUATED" in res.verdict


# --------------------------------------------------------------------------- rcx / prompts


def test_rcx_issues_name_finding_ids_that_finding_detail_reads(site):
    cfg = os.path.join(site["root"], "rcx.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump(fx.config(), fh)
    ctx = _ctx(site, config=cfg)
    res = REG.call("rcx_report", {"facility_id": FID, "top_n": 3}, ctx)
    issues = res.data["issues"]
    with_ids = [i for i in issues if i["finding_ids"]]
    assert with_ids, issues
    for i in issues:
        assert len(i["rules"]) == len(set(i["rules"]))
    first = with_ids[0]
    assert all(f.split("@")[1].split("#")[0] == first["equip"] for f in first["finding_ids"])
    detail = REG.call(
        "finding_detail", {"facility_id": FID, "finding_id": first["finding_ids"][0]}, ctx
    )
    assert detail.data["finding"]["finding_id"] == first["finding_ids"][0]
    text = REG.prompts()[[p.name for p in REG.prompts()].index("plan_site_visit")].render(
        {"facility_id": FID}
    )[0]["text"]
    assert "finding_ids" in text and "rule_run lists" not in text


# --------------------------------------------------------------------------- mv_summary


def test_mv_summary_states_the_unit_and_the_information_only_caveat(site):
    res = REG.call("mv_summary", {"facility_id": "mv-bad"}, _ctx(site, config=site["mv_cfg"]))
    base = next(f for f in res.data["findings"] if f["rule"] == "mv_baseline")
    assert base["metrics"]["accept"] is False
    assert "does not meet daily acceptance" in base["summary"] and "R2 " in base["summary"]
    assert re.search(r"acceptance \(G14 criteria; .*\): .*< 0\.75", base["summary"])
    savings = next(f for f in res.facts if f.get("rule") == "mv_savings")
    assert "the meter's own units" in savings["text"]
    assert "for information only" in savings["text"]
    assert "information only" in res.text and "meter's own units" in res.text
    assert any("does not meet G14 acceptance" in b for b in res.banners)
    assert any("units block" in b for b in res.banners)
    assert "nan" not in res.text.lower().replace("financ", "")


def test_fit_statistics_never_print_nan():
    from camber.mandv.stats import fit_stats

    st = fit_stats(np.full(20, 5.0), np.full(20, 5.0), 2)
    assert "nan" not in st.notes and "R2 undefined" in st.notes


# --------------------------------------------------------------------------- model-facing text


def test_internal_module_paths_and_issue_tags_are_left_out():
    assert plain("judgment (0.98, #85): measured (camber/iaq.py) here") == "judgment: measured here"
    assert plain("(#94) a (0.99, #95, #96) b (0.5) c") == " a b (0.5) c"
    assert plain("fixed in code (camber/rules/x_rule.py, camber/x.py)") == "fixed in code"
    quoted = name("Pump (#2)")
    assert plain(f"see {quoted} (#7)") == f"see {quoted}"


def test_rule_params_carries_no_internal_references(site):
    res = REG.call("rule_params", {"detail": True, "limit": 1000}, _ctx(site))
    blob = json.dumps(res.data) + json.dumps(res.facts)
    assert "(camber/" not in blob
    assert not re.search(r"\((?:\d+\.\d+(?:\.\d+)?, )?#\d+\)", blob)


def test_the_heat_pump_room_finding_is_on_the_fleet_like_every_fleet_rule():
    from camber.rules.heatpump_ops_rule import HPRoomImbalance

    f = HPRoomImbalance().analyze_fleet({})
    assert f.equip == "<fleet>"


def test_a_fleet_equipment_id_is_explained(site):
    with pytest.raises(CapabilityError) as e:
        REG.call("sensor_trust", {"facility_id": FID, "equips": ["<fleet>"]}, _ctx(site))
    assert e.value.code == "not_found" and "fleet finding" in e.value.message


def test_coverage_and_sensor_trust_say_what_their_coverage_is_of(site):
    cov = REG.call("coverage", {"facility_id": FID}, _ctx(site))
    assert "of the whole window" in cov.facts[0]["text"]
    from camber.capabilities._cap_coverage import OUTPUT_SCHEMA as COV
    from camber.capabilities._cap_sensor_trust import OUTPUT_SCHEMA as TRUST

    assert (
        "own span" in COV["properties"]["points"]["items"]["properties"]["coverage"]["description"]
    )
    assert (
        "whole window"
        in TRUST["properties"]["points"]["items"]["properties"]["coverage"]["description"]
    )


# --------------------------------------------------------------------------- completions


def test_completion_of_a_facility_id_offers_only_what_a_call_would_read(site):
    vals, total = REG.complete("data_quality_review", "facility_id", "", {}, _ctx(site))
    assert vals == [FID, "mv-bad"] and total == 2  # nc-lab is research-only: never offered
    vals, _ = REG.complete("data_quality_review", "facility_id", "MV", {}, _ctx(site))
    assert vals == ["mv-bad"]


def test_completion_of_equipment_reads_the_context_facility(site):
    ctx = _ctx(site)
    vals, total = REG.complete("triage_ahu", "equip", "demo", {"facility_id": FID}, ctx)
    assert vals == ["DemoAHU", "DemoAHU2"] and total == 2
    assert REG.complete("triage_ahu", "equip", "", {}, ctx) == ([], 0)
    assert REG.complete("triage_ahu", "equip", "", {"facility_id": "nc-lab"}, ctx) == ([], 0)
    assert REG.complete("triage_ahu", "equip", "", {"facility_id": "annex"}, ctx) == ([], 0)
    vals, _ = REG.complete(
        "compare_equipment", "equips", "DemoAHU ,demoahu2", {"facility_id": FID}, ctx
    )
    assert vals == ["DemoAHU, DemoAHU2"]
    assert REG.complete("explain_finding", "finding_id", "x", {"facility_id": FID}, ctx) == (
        [],
        0,
    )
    for prompt, arg in (("nope", "facility_id"), ("triage_ahu", "nope")):
        with pytest.raises(CapabilityError) as e:
            REG.complete(prompt, arg, "", {}, ctx)
        assert e.value.code == "invalid_params"


def test_completion_never_offers_a_name_a_prompt_cannot_take_whole(tmp_path):
    from camber.store import ParquetStore

    st = ParquetStore(str(tmp_path / "store"))
    frame = fx.ahu_frame(weeks=1)
    for eq in ("A" * 300, "ok,comma", "AHU-OK"):
        st.write_role_frame(frame, facility_id="f", equip=eq, equip_class="AHU")
    ctx = CallContext(store=st.root, allowed_facilities=frozenset({"f"}))
    assert REG.complete("triage_ahu", "equip", "", {"facility_id": "f"}, ctx) == (["AHU-OK"], 1)


def _modern(method, params):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def test_the_mcp_server_offers_completion(site):
    from camber.mcp import McpServer

    server = McpServer(REG, _ctx(site))
    disc = server.handle(_modern("server/discover", {}))
    assert disc["result"]["capabilities"]["completions"] == {}
    got = server.handle(
        _modern(
            "completion/complete",
            {
                "ref": {"type": "ref/prompt", "name": "triage_ahu"},
                "argument": {"name": "equip", "value": "Demo"},
                "context": {"arguments": {"facility_id": FID}},
            },
        )
    )
    res = got["result"]
    assert res["completion"] == {"values": ["DemoAHU", "DemoAHU2"], "total": 2, "hasMore": False}
    assert res["resultType"] == "complete"
    for bad in (
        {"ref": {"type": "ref/resource", "uri": "x"}, "argument": {"name": "a", "value": ""}},
        {"ref": {"type": "ref/prompt", "name": "nope"}, "argument": {"name": "a", "value": ""}},
        {"ref": {"type": "ref/prompt", "name": "triage_ahu"}, "argument": {"name": "zz"}},
        {"ref": {"type": "ref/prompt", "name": "triage_ahu"}},
    ):
        assert server.handle(_modern("completion/complete", bad))["error"]["code"] == -32602
    legacy = McpServer(REG, _ctx(site))
    init = legacy.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {}},
        }
    )
    assert init["result"]["capabilities"]["completions"] == {}
    got = legacy.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "completion/complete",
            "params": {
                "ref": {"type": "ref/prompt", "name": "triage_ahu"},
                "argument": {"name": "facility_id", "value": "de"},
            },
        }
    )
    assert got["result"] == {"completion": {"values": [FID], "total": 1, "hasMore": False}}


# --------------------------------------------------------------------------- startup


def test_an_empty_store_path_is_refused():
    from camber.mcp._launch import LaunchError, resolve_store

    for path in ("", "  "):
        with pytest.raises(LaunchError, match="empty path"):
            resolve_store(path)


def test_min_trust_minus_inf_reaches_the_range_check(capsys):
    from camber.cli import main

    with pytest.raises(SystemExit) as e:
        main(["mcp", "--store", "x", "--allow", "f", "--min-trust", "-inf"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "must be from 0 to 1" in err and "expected one argument" not in err


def test_an_unknown_config_key_is_a_warning_in_mcp_and_in_run(site, caplog, capsys):
    from camber.config import _unknown_keys_note
    from camber.mcp._launch import check_config

    assert _unknown_keys_note(fx.config()) is None
    assert _unknown_keys_note({**fx.config(), "_note": 1}) is None
    note = _unknown_keys_note({**fx.config(), "rule": ["x"]})
    assert note.startswith("unknown top-level key(s) 'rule' are ignored")
    cfg = os.path.join(site["root"], "typo.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({**fx.config(), "rule": ["x"]}, fh)
    with caplog.at_level(logging.WARNING, logger="camber.mcp"):
        assert check_config(cfg, site["store"].root, frozenset({FID})) == FID
    assert any("unknown top-level key(s) 'rule'" in r.getMessage() for r in caplog.records)


def test_camber_run_warns_on_an_unknown_key(site, capsys):
    from camber.cli import main

    cfg = os.path.join(site["root"], "typo-run.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({**fx.config(), "rules": ["outdoor_air_fraction"], "rule": []}, fh)
    assert main(["run", cfg, "--no-persist"]) in (0, None)
    assert "unknown top-level key(s) 'rule' are ignored" in capsys.readouterr().err


def test_matplotlib_config_goes_to_a_temporary_directory(monkeypatch):
    import shutil

    from camber.mcp._launch import isolate_matplotlib

    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    path = isolate_matplotlib()
    try:
        assert path and os.path.isdir(path) and os.environ["MPLCONFIGDIR"] == path
    finally:
        shutil.rmtree(path, ignore_errors=True)
    monkeypatch.setenv("MPLCONFIGDIR", "/somewhere")
    assert isolate_matplotlib() is None and os.environ["MPLCONFIGDIR"] == "/somewhere"


# --------------------------------------------------------------------------- packaging


def test_datasets_info_and_list_name_the_extra_a_dataset_needs(capsys):
    from camber.cli import main

    assert main(["datasets", "info", "ornl-frp-vav"]) == 0
    assert "needs     : the 'xlsx' extra (" in capsys.readouterr().out
    assert main(["datasets", "list"]) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("ornl-frp-vav "))
    assert row.endswith("[needs xlsx]")
    assert 'pip install "camber-toolkit[xlsx]"' in out


def test_the_all_extra_names_every_runtime_extra():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
        text = fh.read()
    section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    extras = set(re.findall(r"^([a-z][a-z0-9-]*) = ", section, re.M))
    block = section.split("\nall = [", 1)[1].split("]\n", 1)[0]
    named = set(",".join(re.findall(r"camber-toolkit\[([^\]]+)\]", block + "]")).split(","))
    assert named == extras - {"all", "dev", "docs", "mcp", "edge"}
