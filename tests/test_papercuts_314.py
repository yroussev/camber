"""0.111 (#314): MCP papercuts from the second acceptance round.

Every test builds what it needs in its own tmp directory; nothing reads the network.
"""

import io
import json
import logging
import os
import re
import sys
import threading

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.agent.verify import _sentences  # noqa: E402
from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Caps,
    Registry,
    default_registry,
)
from camber.capabilities._common import finding_fact, store_config  # noqa: E402
from camber.capabilities._page import collect, page  # noqa: E402
from camber.mcp import McpServer  # noqa: E402
from camber.mcp._server import encode_message  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

REG = default_registry()
FID = fx.FID


def _ctx(root, allowed=(FID,), **kw):
    return CallContext(store=str(root), allowed_facilities=frozenset(allowed), **kw)


def _cap(name, handler):
    obj = {"type": "object", "properties": {}}
    return Capability(name, name, "a test capability", obj, {"type": "object"}, handler)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("cap314")
    st = fx.make_store(root, meta=None)
    idx = pd.date_range("2026-03-02", periods=24 * 7, freq="h")
    st.write_role_frame(
        pd.DataFrame({"oat": 60.0}, index=idx), facility_id=FID, equip="WX", equip_class="weather"
    )
    st.write_role_frame(
        pd.DataFrame({"sat": 55.0}, index=idx), facility_id=FID, equip="LOG1", equip_class="LOGGER"
    )
    st.points()
    return st


# --------------------------------------------------------------------------- rule_params


def test_rule_params_says_not_set_and_cites_no_module_or_release():
    res = REG.call("rule_params", {"rules": ["outdoor_air_fraction"]}, _ctx("/nonexistent"))
    text = json.dumps([res.text, res.facts])
    assert "None %" not in text and "None" not in res.facts[1]["text"]
    assert "min_oa_pct not set by default" in res.facts[1]["text"]
    every = REG.call("rule_params", {"detail": True, "limit": 1000}, _ctx("/nonexistent"))
    blob = json.dumps(every.data)
    assert not re.search(r"camber/[\w/]+\.py", blob)
    assert not re.search(r"(?<![A-Z])#\d", blob)  # "OS#3" is a G36 operating state
    assert not re.search(r"\(0\.\d{2,3}\)|\b0\.\d{2,3}, #|since 0\.(?:9\d|1\d\d)\b", blob)


# --------------------------------------------------------------------------- too_large bytes


def _call(reg, ctx):
    server = McpServer(reg, ctx)
    params = {
        "name": "rows",
        "arguments": {},
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


def test_too_large_reports_the_reply_size_also_when_the_result_alone_is_over(tmp_path):
    """The result alone (one copy of the data) is over the cap: the reply's size is reported,
    the size the reply would have, not the result's (which halved the number above a size)."""
    reg = Registry()
    rows = ["y" * 100] * 150  # about 15 KB of data, so the result alone is over 10 KB
    reg.register(_cap("rows", lambda ctx, a: CapabilityResult({"rows": rows}, "Rows.", [])))
    whole = _call(reg, _ctx(tmp_path, caps=Caps(max_bytes=1_000_000)))
    size = len(encode_message(whole))
    err = _call(reg, _ctx(tmp_path, caps=Caps(max_bytes=10_000)))["result"]
    assert err["isError"] is True
    data = err["_meta"]["camber/error"]["data"]
    assert data["max_bytes"] == 10_000 and abs(data["bytes"] - size) <= 16  # the reply's
    # far over the cap: the registry's memory guard answers, saying "more than"
    big = ["y" * 100] * 1000
    reg2 = Registry()
    reg2.register(_cap("rows", lambda ctx, a: CapabilityResult({"rows": big}, "Rows.", [])))
    err = _call(reg2, _ctx(tmp_path, caps=Caps(max_bytes=10_000)))["result"]
    assert "more than" in err["content"][0]["text"]
    assert err["_meta"]["camber/error"]["data"]["bytes"] > 80_000


# --------------------------------------------------------------------------- paging


def test_a_short_sibling_list_says_its_rows_are_on_the_first_page():
    notices, _token = collect()
    assert page(["a"], {"offset": 50}, default=50, what="cited") == []
    assert "all its 1 row(s) are on the first page" in notices[0]
    assert page(list(range(80)), {"offset": 100}, default=50, what="rows") == []
    assert "offset 100 is past its 80 row(s)" in notices[1]


# --------------------------------------------------------------------------- prompts


def _prompt(name):
    return next(p for p in REG.prompts() if p.name == name)


def test_prompt_arguments_are_refused_not_cut():
    compare = _prompt("compare_equipment").render
    many = ", ".join(f"AHU-{i}" for i in range(30))
    with pytest.raises(CapabilityError) as e:
        compare({"facility_id": FID, "equips": many})
    assert e.value.code == "invalid_params" and "at most 20" in e.value.message
    text = compare({"facility_id": FID, "equips": ", ".join(f"AHU-{i}" for i in range(20))})
    assert "AHU-19" in text[0]["text"]
    with pytest.raises(CapabilityError, match="longer than 200"):
        _prompt("triage_ahu").render({"facility_id": FID, "equip": "x" * 5000})
    with pytest.raises(CapabilityError, match="longer than 200"):
        compare({"facility_id": FID, "equips": "a, " + "b" * 300})


def test_rcx_executive_summary_asks_for_the_further_pages():
    text = _prompt("rcx_executive_summary").render({"facility_id": FID})[0]["text"]
    assert "offset" in text and "conditional on a sensor fix is missed" in text


# --------------------------------------------------------------------------- completion


def test_completion_offers_what_the_store_holds_and_a_prompt_can_use(site):
    ctx = _ctx(site.root, allowed=(FID, "not-a-facility"))
    vals, _ = REG.complete("data_quality_review", "facility_id", "", {}, ctx)
    assert vals == [FID]
    vals, _ = REG.complete("triage_ahu", "equip", "", {"facility_id": FID}, ctx)
    assert "WX" not in vals and {"DemoAHU", "DemoAHU2"} <= set(vals)
    assert "LOG1" in vals  # an unrecognised class may be an air handler
    vals, _ = REG.complete("compare_equipment", "equips", "DemoAHU, ", {"facility_id": FID}, ctx)
    assert "DemoAHU, DemoAHU" not in vals and "DemoAHU, DemoAHU2" in vals
    assert "DemoAHU, WX" in vals  # compare_equipment takes any equipment


# --------------------------------------------------------------------------- default config


def test_the_default_config_runs_no_rule_on_an_unrecognised_class(site):
    cfg = store_config(_ctx(site.root), FID)
    classes = {e["class"] for e in cfg["equipment"]}
    assert "LOGGER" not in classes and "AHU" in classes
    with pytest.raises(CapabilityError) as e:
        REG.call("rule_run", {"facility_id": FID, "equips": ["LOG1"]}, _ctx(site.root))
    assert e.value.code == "not_found"


def test_a_facility_of_unrecognised_classes_only_still_runs_them(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range("2026-03-02", periods=48, freq="h")
    st.write_role_frame(
        pd.DataFrame({"sat": 55.0}, index=idx), facility_id="own", equip="L1", equip_class="LOGGER"
    )
    cfg = store_config(_ctx(st.root, allowed=("own",)), "own")
    assert cfg["equipment"] == [{"class": "LOGGER"}]


# --------------------------------------------------------------------------- rcx heading


def test_rcx_issues_carry_the_heading_the_report_prints(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    res = REG.call("rcx_report", {"facility_id": FID}, _ctx(st.root))
    issues = res.data["issues"]
    assert issues and all(i["heading"] for i in issues)
    for i in issues:
        assert i["heading"] in (i["cause"], i["title"])
    facts = {f["id"].split("-")[0]: f["text"] for f in res.facts}
    first = issues[0]
    assert first["heading"] in facts[first["fact"].split("-")[0]]


# --------------------------------------------------------------------------- findings.json


def test_the_zone_census_profile_is_json_data():
    from camber.model.roles import Role
    from camber.rules.zones_rule import ZonesHeatCoolCensus

    n = 24 * 14
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    a = pd.DataFrame(
        {Role.HEAT_VALVE: 40.0, Role.AIRFLOW: 400.0, Role.AIRFLOW_SP: 200.0}, index=idx
    )
    b = pd.DataFrame({Role.HEAT_VALVE: 0.0, Role.AIRFLOW: 400.0, Role.AIRFLOW_SP: 200.0}, index=idx)
    f = ZonesHeatCoolCensus().analyze_fleet({"VAV_A": a, "VAV_B": b})
    prof = f.metrics["timeofweek_profile"]
    assert isinstance(prof, dict) and len(prof["time_of_week_hour"]) == len(prof["n_heating"])
    back = json.loads(json.dumps(f.as_dict(), default=str))["metrics"]["timeofweek_profile"]
    assert back == prof  # findings.json keeps numbers, not a printed table
    assert pd.DataFrame(prof).set_index("time_of_week_hour").shape[1] == 4


# --------------------------------------------------------------------------- M&V wording


def test_mv_notes_use_the_rounded_figures_and_never_compare_a_word():
    from camber.mandv.stats import fit_stats

    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 400)
    y = 100 + x + rng.normal(0, 2.0, 400)
    st = fit_stats(y, 100 + x, p=2)
    if "R2" in st.notes:
        shown = re.search(r"R2 (\S+) <", st.notes).group(1)
        assert shown == format(st.r2, ".2f")
    const = fit_stats(np.full(50, 5.0), np.full(50, 5.0) + rng.normal(0, 1, 50), p=2)
    assert "R2 undefined (must be at least 0.75)" in const.notes
    assert "undefined <" not in const.notes and "undefined >" not in const.notes


def test_mv_note_rounding_matches_a_summary_that_states_the_value():
    from camber.mandv.stats import _unmet

    assert _unmet("R2", round(0.19496, 4), ".2f", "<", "0.75") == "R2 0.20 < 0.75"
    assert _unmet("CV(RMSE)", float("nan"), ".1%", ">", "30%") == (
        "CV(RMSE) undefined (must be at most 30%)"
    )


# --------------------------------------------------------------------------- check_citations


def test_sentences_split_at_line_breaks_and_keep_list_markers_with_their_sentence():
    text = (
        "NOTICE: 'x' shows rows 1-20 of 48\n\nThe report ranks 6 issue(s) [F1].\n"
        "1. Most likely A [F2].\n2) B [F3].\n- C [F4]."
    )
    assert _sentences(text) == [
        "NOTICE: 'x' shows rows 1-20 of 48",
        "The report ranks 6 issue(s) [F1].",
        "1. Most likely A [F2].",
        "2) B [F3].",
        "- C [F4].",
    ]
    assert _sentences("Use Ch.5 and 5.16 here.") == ["Use Ch.5 and 5.16 here."]


def test_check_citations_passes_a_numbered_draft(tmp_path):
    facts = [
        {"id": "F1@x-abc123", "text": "damper stuck at 100 %"},
        {"id": "F2@x-abc123", "text": "valve leaks 3 F"},
    ]
    draft = "1. The damper is stuck at 100 % [F1@x-abc123].\n2. The valve leaks 3 F [F2@x-abc123]."
    res = REG.call("check_citations", {"text": draft, "facts": facts}, _ctx(tmp_path))
    assert res.data["grounded"] is True and res.data["n_sentences"] == 2
    head = "NOTICE: a page notice without a stop\n\nThe damper is stuck [F1@x-abc123]."
    res = REG.call(
        "check_citations", {"text": head, "facts": facts, "detail": True}, _ctx(tmp_path)
    )
    assert "The damper is stuck" in res.data["verified_text"]


# --------------------------------------------------------------------------- redaction


def test_a_rule_summary_keeps_slashed_names_and_the_servers_paths_go(tmp_path):
    """Text a rule wrote after the quoted unit (#307 left it to the path pattern)."""
    root = str(tmp_path)
    row = {
        "finding_id": "zones@<fleet>",
        "rule": "zones",
        "equip": "<fleet>",
        "severity": "warn",
        "summary": "fleet: 40% mixed; persistently heating: /Drivers/Net/VAV_1, slot:/C/VAV-2; "
        f"read from {root}/x.parquet",
        "metrics": {},
    }
    unit = dict(row, equip="/Drivers/Net/AHU_1", summary="/Drivers/Net/AHU_1: feeds /Floor 2/VAV")

    def handler(ctx, a):
        facts = [finding_fact("F1@t", row), finding_fact("F2@t", unit)]
        text = " ".join(f"{f['text']} [{f['id']}]." for f in facts)
        return CapabilityResult({}, text, facts)

    reg = Registry()
    reg.register(_cap("t", handler))
    res = reg.call("t", {}, CallContext(store=root, allowed_facilities=frozenset()))
    assert "/Drivers/Net/VAV_1, slot:/C/VAV-2" in res.text
    assert '"/Drivers/Net/AHU_1": feeds /Floor 2/VAV' in res.text
    assert root not in res.text and "read from <path>" in res.text


# --------------------------------------------------------------------------- protocol


def _server(tmp_path):
    return McpServer(Registry(), _ctx(tmp_path, allowed=()))


def test_protocol_envelope_papercuts(tmp_path, caplog):
    s = _server(tmp_path)
    caplog.set_level(logging.WARNING, logger="camber.mcp")
    out = s.handle({"jsonrpc": "2.0", "method": "", "params": {}})
    assert out["id"] is None and "must not be empty" in out["error"]["message"]
    out = s.handle({"jsonrpc": "2.0", "id": "a", "method": None, "result": {}})
    assert out == {
        "jsonrpc": "2.0",
        "id": "a",
        "error": {"code": -32600, "message": 'Invalid Request: "method" must be a string'},
    }
    assert s.handle({"jsonrpc": "2.0", "id": "b", "result": {}}) is None  # a response
    out = s.handle_line('{"jsonrpc":"2.0","method":"notifications/x","params":{"k":"\\ud83d"}}')
    assert out["id"] is None and "lone UTF-16 surrogate" in out["error"]["message"]
    s.handle_line("{not json")
    logged = [r.getMessage() for r in caplog.records]
    assert any("must not be empty" in m for m in logged)
    assert any("Parse error" in m for m in logged)
    assert any("surrogate" in m for m in logged)


def test_a_write_to_a_closed_output_is_the_client_leaving(caplog):
    from camber.mcp._stdio import _Output

    out = _Output(io.BytesIO())
    out.stream.close()
    caplog.set_level(logging.ERROR, logger="camber.mcp")
    out.reply({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert out.gone.is_set() and not caplog.records


def test_a_failing_stderr_does_not_turn_the_exit_status(monkeypatch):
    from camber.mcp import _launch

    class Full(io.StringIO):
        def flush(self):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(sys, "stderr", Full())
    _launch.settle_stderr()
    assert sys.stderr.name == os.devnull
    sys.stderr.close()


# --------------------------------------------------------------------------- launch


def test_a_fifo_registry_is_refused_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs here")
    from camber._servable import read_registry

    os.mkfifo(tmp_path / "_facilities.json")
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", read_registry(str(tmp_path))))
    t.daemon = True
    t.start()
    t.join(5)
    assert not t.is_alive(), "reading a FIFO registry blocked"
    assert box["r"] == ({}, "cannot be read")


def test_startup_names_research_only_facilities_under_allow_all_active(tmp_path, caplog):
    from camber.mcp import _launch

    st = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range("2026-03-02", periods=48, freq="h")
    for fid, sat in (("own", 55.0), ("nc-lab", 57.5)):  # not the same part files
        st.write_role_frame(
            pd.DataFrame({"sat": sat}, index=idx), facility_id=fid, equip="A", equip_class="AHU"
        )
    reg = {
        "own": {"state": "active"},
        "nc-lab": {"state": "active", "dataset": {"licence": "CC-BY-NC-4.0"}},
    }
    with open(os.path.join(st.root, "_facilities.json"), "w", encoding="utf-8") as fh:
        json.dump(reg, fh)
    ctx = _launch.build_context(st.root, allow_all_active=True)
    refused = _launch.refused_facilities(ctx)
    assert set(refused) == {"nc-lab"} and refused["nc-lab"][0] == "research_only"
    caplog.set_level(logging.INFO, logger="camber.mcp")
    _launch._log_templates(ctx, refused)
    assert not any("nc-lab" in r.getMessage() for r in caplog.records)
