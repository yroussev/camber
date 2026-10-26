"""0.111 (#299): results a model can read -- pages, the truncation flag, and the byte cap.

- ``rcx_report`` returns ``top_n`` ranked issues (``offset`` for the next ones), with only their
  facts and sections, and a page of the fact index's appendix A;
- every list a capability cuts is cut by ``_page.page``, which records a notice; the registry
  turns each notice into a banner and sets ``truncated`` (``coverage`` gaps included);
- the byte cap counts the whole result (data, text, facts, banners), and ``camber mcp`` counts
  the whole reply it sends;
- ``check_citations`` and ``rule_params`` are compact by default; the tool list is shorter.

Every test builds a tiny store in its own tmp directory.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Caps,
    Registry,
    default_registry,
)
from camber.capabilities._page import page  # noqa: E402
from camber.mcp import McpServer  # noqa: E402
from camber.model.roles import Role  # noqa: E402

FID = fx.FID
REG = default_registry()
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("cap299")
    st = fx.make_store(root, meta=None)
    # a point with a three-hour hole every six hours: 84 gaps
    idx = pd.date_range("2026-03-02", periods=24 * 21, freq="h")
    sat = pd.Series(55.0, index=idx)
    sat.iloc[[i for i in range(len(idx)) if i % 6 in (2, 3, 4)]] = np.nan
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: 60.0}, index=idx)
    st.write_role_frame(frame, facility_id="holey", equip="AHU-H", equip_class="AHU")
    st.points()
    return st


def _ctx(st, **kw):
    return CallContext(store=st.root, allowed_facilities=frozenset({FID, "holey"}), **kw)


def _cap(name, handler, output_schema=None):
    return Capability(
        name=name,
        title=name,
        description="a test capability",
        input_schema={"type": "object", "properties": {}},
        output_schema=output_schema or {"type": "object"},
        handler=handler,
    )


# --------------------------------------------------------------------------- rcx_report top_n


def test_rcx_report_honours_top_n_and_offset(store):
    ctx = _ctx(store)
    full = REG.call("rcx_report", {"facility_id": FID}, ctx)
    n = full.data["kpis"]["n_issues"]
    assert n >= 3 and len(full.data["issues"]) == min(n, 8)
    one = REG.call("rcx_report", {"facility_id": FID, "top_n": 1}, ctx)
    second = REG.call("rcx_report", {"facility_id": FID, "top_n": 1, "offset": 1}, ctx)
    assert [i["rank"] for i in one.data["issues"]] == [1]
    assert [i["rank"] for i in second.data["issues"]] == [2]
    assert one.data["kpis"]["n_issues"] == n  # the KPIs still count every issue
    assert one.truncated and any("'issues' shows row 1 of" in b for b in one.banners)
    assert len(json.dumps(one.data)) < len(json.dumps(full.data))
    # only the returned issue's facts and section
    keys = {i["key"] for i in full.data["issues"]}
    kept = one.data["issues"][0]["key"]
    contexts = {f["id"].split("@")[1].rsplit("-", 1)[0] for f in one.data["fact_index"]}
    assert kept in contexts and not (contexts & (keys - {kept}))
    assert {f["id"] for f in one.data["fact_index"]} <= {f["id"] for f in one.facts}
    sections = {s["id"] for s in one.data["sections"]}
    assert f"issue-{kept}" in sections
    assert not sections & {f"issue-{k}" for k in keys - {kept}}


def test_rcx_report_pages_appendix_a(store):
    ctx = _ctx(store)
    small = REG.call("rcx_report", {"facility_id": FID, "limit": 1}, ctx)
    big = REG.call("rcx_report", {"facility_id": FID, "limit": 1000}, ctx)

    def app_a(res):
        return [f for f in res.data["fact_index"] if "@appA-" in f["id"]]

    assert len(app_a(small)) == 1 < len(app_a(big))
    assert any("'fact_index appendix A' shows row 1 of" in b for b in small.banners)


# --------------------------------------------------------------------------- truncated


def test_coverage_cut_gaps_set_truncated(store):
    ctx = _ctx(store)
    res = REG.call("coverage", {"facility_id": "holey"}, ctx)
    assert len(res.data["gaps"]) == 50 and res.truncated
    assert any("'gaps' shows rows 1-50 of" in b and "offset=50" in b for b in res.banners)
    every = REG.call("coverage", {"facility_id": "holey", "limit": 1000}, ctx)
    assert len(every.data["gaps"]) > 50 and not every.truncated
    nxt = REG.call("coverage", {"facility_id": "holey", "offset": 50}, ctx)
    assert nxt.data["gaps"][0] == every.data["gaps"][50]


def test_a_page_sets_truncated_even_when_the_handler_does_not(store):
    def handler(ctx, args):
        rows = page(list(range(30)), args, default=10, what="rows")
        return CapabilityResult({"rows": rows}, "", [], truncated=False)

    reg = Registry()
    reg.register(
        Capability(
            name="paged",
            title="Paged",
            description="a paged test capability",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}, "offset": {"type": "integer"}},
            },
            output_schema={"type": "object"},
            handler=handler,
        )
    )
    ctx = _ctx(store)
    res = reg.call("paged", {}, ctx)
    assert res.data["rows"] == list(range(10)) and res.truncated
    assert res.banners == [
        "'rows' shows rows 1-10 of 30: call again with offset=10 for the next page, or a larger "
        "limit (up to 1000)"
    ]
    assert not reg.call("paged", {"limit": 30}, ctx).truncated
    past = reg.call("paged", {"offset": 99}, ctx)
    assert past.data["rows"] == [] and past.truncated and "past its 30" in past.banners[0]
    last = reg.call("paged", {"offset": 25}, ctx)
    assert last.data["rows"] == list(range(25, 30)) and "offset=0" in last.banners[0]


def test_every_default_page_is_flagged(store):
    """Every list capability on a store larger than its default page says so."""
    ctx = _ctx(store)
    for name, args, key in (
        ("rule_run", {"facility_id": FID, "limit": 1}, "findings"),
        ("sensor_trust", {"facility_id": FID, "limit": 1}, "points"),
        ("points", {"facility_id": FID, "limit": 1}, "points"),
        ("facilities", {"limit": 1}, "facilities"),
        ("rule_params", {"limit": 1}, "rules"),
    ):
        res = REG.call(name, args, ctx)
        assert len(res.data[key]) == 1 and res.truncated, name
        assert any(f"'{key}' shows row 1 of" in b for b in res.banners), name


def test_rule_run_pages_follow_on(store):
    ctx = _ctx(store)
    args = {"facility_id": FID, "min_severity": "ok"}
    every = REG.call("rule_run", {**args, "limit": 1000}, ctx).data
    a = REG.call("rule_run", {**args, "limit": 2}, ctx).data
    b = REG.call("rule_run", {**args, "limit": 2, "offset": 2}, ctx).data
    ids = [f["finding_id"] for f in every["findings"]]
    assert [f["finding_id"] for f in a["findings"] + b["findings"]] == ids[:4]
    assert a["count"] == b["count"] == len(ids)


# --------------------------------------------------------------------------- byte caps


def test_the_byte_cap_counts_facts_and_text_not_only_data(store):
    facts = [{"id": f"F{i}@big", "text": "x" * 1000} for i in range(1, 30)]
    reg = Registry()
    reg.register(_cap("big", lambda ctx, a: CapabilityResult({}, "Small [F1@big].", facts)))
    with pytest.raises(CapabilityError) as e:
        reg.call("big", {}, _ctx(store, caps=Caps(max_bytes=10_000)))
    assert e.value.code == "too_large" and e.value.data["max_bytes"] == 10_000
    assert e.value.data["bytes"] > 29_000 and "limit" in e.value.message
    assert reg.call("big", {}, _ctx(store, caps=Caps(max_bytes=100_000))).facts


def _mcp(reg, ctx, modern):
    server = McpServer(reg, ctx)
    if not modern:
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            }
        )
    params = {"name": "rows", "arguments": {}}
    if modern:
        params["_meta"] = MODERN_META
    out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    return out["result"]


@pytest.mark.parametrize("modern", [True, False])
def test_mcp_counts_the_whole_reply(store, modern):
    """The data alone is under the cap; the reply carries it twice and goes over."""
    reg = Registry()
    rows = ["y" * 100] * 60  # about 6.3 KB of data
    reg.register(_cap("rows", lambda ctx, a: CapabilityResult({"rows": rows}, "Rows.", [])))
    res = _mcp(reg, _ctx(store, caps=Caps(max_bytes=10_000)), modern)
    assert res["isError"] is True and res["_meta"]["camber/error"]["code"] == "too_large"
    assert res["_meta"]["camber/error"]["data"]["bytes"] > 10_000
    # under a larger cap: one compact JSON text block that matches structuredContent
    res = _mcp(reg, _ctx(store, caps=Caps(max_bytes=100_000)), modern)
    summary, block = res["content"]
    assert summary["text"] == "Rows." and res["isError"] is False
    assert block["text"] == json.dumps(res["structuredContent"], separators=(",", ":"))


# --------------------------------------------------------------------------- compact defaults


def test_check_citations_is_compact_by_default(store):
    ctx = _ctx(store)
    facts = [{"id": "F1@x-abc123", "text": "value 7"}]
    text = "A [F1@x-abc123]. " * 2000
    res = REG.call("check_citations", {"text": text, "facts": facts}, ctx)
    assert res.data["n_sentences"] == 2000 and res.data["grounded"] is True
    assert res.data["sentences"] == [] and "verified_text" not in res.data
    assert res.data["cited"] == ["F1@x-abc123"]
    assert len(json.dumps(res.data)) < 1000
    bad = "Seven is 8 [F1@x-abc123]. " * 120
    res = REG.call("check_citations", {"text": bad, "facts": facts}, ctx)
    assert len(res.data["sentences"]) == 50 and res.truncated
    assert res.data["verdicts"]["unsupported_number"] == 120
    full = REG.call(
        "check_citations", {"text": bad, "facts": facts, "detail": True, "limit": 1000}, ctx
    )
    assert len(full.data["sentences"]) == 120 and "verified_text" in full.data


def test_rule_params_default_page_and_slim_facts(store):
    ctx = _ctx(store)
    res = REG.call("rule_params", {}, ctx)
    assert len(res.data["rules"]) == 25 < res.data["count"] and res.truncated
    detail = REG.call("rule_params", {"detail": True}, ctx)
    assert len(detail.data["rules"]) == 10 and detail.truncated
    for f in detail.facts[1:]:  # a fact keeps the figures, not the prose
        assert all(set(p) <= {"name", "default", "unit", "range"} for p in f["params"])
    assert any("basis" in p for r in detail.data["rules"] for p in r["params"])


def test_the_tool_list_is_shorter():
    server = McpServer(REG, CallContext(store="s", allowed_facilities=frozenset()))
    out = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": MODERN_META}}
    )
    tools = out["result"]["tools"]
    size = len(json.dumps(tools, separators=(",", ":"), ensure_ascii=False))
    assert size < 45_000, size  # 48 KB in 0.110.0
    for t in tools:
        assert "$schema" not in t["inputSchema"] and "$schema" not in t["outputSchema"]
        assert "title" not in t["annotations"] and t["title"]
        assert '"description":"' not in json.dumps(t["outputSchema"], separators=(",", ":"))
        assert len(t["description"]) < 600
    # the registry keeps the full schemas: what tools/list leaves out are annotations
    rcx = REG.get("rcx_report")
    assert rcx.output_schema["properties"]["fact_index"]["description"]
