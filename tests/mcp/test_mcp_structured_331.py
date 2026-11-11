"""Every tool's ``structuredContent`` carries its facts and notices (0.112, #331).

Some hosts (command-line coding assistants among them) show the model only ``structuredContent``
when a result has one, never the text blocks: the facts block and the NOTICE lines must be there
too, under ``RESULT_KEY``. Each test here reads a result the way such a host does
(``structuredContent`` only) and checks that the model could see every fact id with its text and
every notice, cite them, and read the verdicts and notices of ``check_citations``. Every case runs
on the sensor-bias catalog entry (generated locally) and a small meter site in tmp directories;
nothing reaches the network.
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import pytest

from camber import datasets
from camber.capabilities import RESULT_KEY as REGISTRY_KEY
from camber.capabilities import (
    CallContext,
    Capability,
    CapabilityResult,
    Registry,
    default_registry,
)
from camber.capabilities._schema import validate
from camber.mcp import RESULT_KEY, McpServer
from camber.mcp._server import FACTS_HEADER, encode_message
from camber.model.roles import Role
from camber.store import ParquetStore

FID = "ds-synthetic-ahu-sensor-bias"
MV = "mv-site"
MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _meter(days=150):
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The sensor-bias entry (AHU__sat_bias reads 3 F high against its reference logger: its
    findings are conditional on that sensor) and a meter site with an M&V project."""
    root = tmp_path_factory.mktemp("sc331")
    store = str(root / "store")
    datasets.ingest("synthetic-ahu-sensor-bias", store, data_dir=str(root / "cache"))
    ParquetStore(store).write_role_frame(
        _meter(), facility_id=MV, equip="Main", equip_class="METER"
    )
    bias = datasets.config_template(
        "synthetic-ahu-sensor-bias", store, exercise="data-sensor-conditional"
    )
    (root / "bias.json").write_text(json.dumps(bias), encoding="utf-8")
    mv = {
        "source": {"kind": "store", "store": "store", "facility_id": MV},
        "equipment": [{"class": "METER"}],
        "mv": [
            {
                "class": "METER",
                "role": "power",
                "period": ["2024-01-01", "2024-03-31"],
                "reporting_period": ["2024-04-01", None],
            }
        ],
    }
    (root / "mv.json").write_text(json.dumps(mv), encoding="utf-8")
    return {
        "root": str(root),
        "store": store,
        "configs": (str(root / "bias.json"), str(root / "mv.json")),
    }


class Client:
    """An in-process client of one protocol revision that, like such a host, reads a tool
    result's ``structuredContent`` when there is one (the text blocks are kept for comparison)."""

    def __init__(self, site, modern: bool):
        ctx = CallContext(
            store=site["store"], allowed_facilities=frozenset({FID, MV}), config=site["configs"]
        )
        self.server = McpServer(default_registry(), ctx, server_version="TEST")
        self.modern, self.n = modern, 0
        if not modern:
            init = {"protocolVersion": "2025-11-25", "capabilities": {}}
            self.rpc("initialize", init)
            self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = self.rpc("tools/list", {})["tools"]
        self.schemas = {t["name"]: t["outputSchema"] for t in tools}

    def rpc(self, method, params):
        self.n += 1
        if self.modern:
            params = {**params, "_meta": MODERN}
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}
        return json.loads(encode_message(self.server.handle(msg)))["result"]

    def call(self, name, arguments):
        res = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert res["isError"] is False, res["content"][0]["text"]
        return res


#: one call of every tool (all 13), with arguments that succeed on the site
CALLS = [
    ("facilities", {}),
    ("points", {"facility_id": FID}),
    ("coverage", {"facility_id": FID, "group_by": "equip", "limit": 2}),
    ("sensor_trust", {"facility_id": FID}),
    ("rule_run", {"facility_id": FID, "min_severity": "ok"}),
    ("finding_detail", {"facility_id": FID, "finding_id": "leaking_valve@AHU__sat_bias"}),
    ("rcx_report", {"facility_id": FID, "top_n": 2}),
    ("mv_summary", {"facility_id": MV, "claim": "a 12% saving"}),
    ("drift", {"facility_id": FID, "baseline": ["2025-05-12", "2025-05-20"]}),
    ("dataset_search", {"query": "sensor"}),
    ("dataset_entry", {"dataset_id": "synthetic-ahu-sensor-bias"}),
    ("rule_params", {"rules": ["leaking_valve"]}),
    ("check_citations", {"text": "Nothing here cites a fact.", "detail": True}),
]


def _text_facts(res) -> dict:
    """``{id: text}`` of the facts block of ``content`` (what a content-showing host shows)."""
    blocks = [b["text"] for b in res["content"] if b["text"].startswith(FACTS_HEADER)]
    if not blocks:
        return {}
    out = {}
    for line in blocks[0].splitlines()[1:]:
        m = re.match(r"^\[([^\]]+)\] ?(.*)$", line)
        assert m, line
        out[m.group(1)] = m.group(2)
    return out


def _text_notices(res) -> list:
    """The NOTICE lines of the summary."""
    return [
        line[len("NOTICE: ") :]
        for line in res["content"][0]["text"].splitlines()
        if line.startswith("NOTICE: ")
    ]


@pytest.fixture(scope="module", params=[True, False], ids=["2026-07-28", "2025-11-25"])
def swept(site, request):
    """Every call of :data:`CALLS` through one client, in order."""
    c = Client(site, request.param)
    return c, [(name, c.call(name, args)) for name, args in CALLS]


def test_the_sweep_calls_every_tool(swept):
    c, results = swept
    assert sorted(c.schemas) == sorted({name for name, _ in results})


def test_structured_content_has_every_fact_and_notice_the_text_has(swept):
    c, results = swept
    for name, res in results:
        sc = res["structuredContent"]
        assert list(sc)[0] == RESULT_KEY, name  # first: a host that cuts a long result keeps it
        got = sc[RESULT_KEY]
        assert got["facts"] == _text_facts(res), name  # same ids, same text, same order
        assert list(got["facts"]) == [f["id"] for f in res["_meta"]["camber/facts"]], name
        assert got["notices"] == _text_notices(res) == res["_meta"]["camber/banners"], name
        # the JSON text block is the rest of the result (its facts and notices are the blocks')
        rest = {k: v for k, v in sc.items() if k != RESULT_KEY}
        assert json.loads(res["content"][1]["text"]) == rest, name
        assert not validate(sc, c.schemas[name]), name
    every = {k for _, r in results for k in r["structuredContent"][RESULT_KEY]["facts"]}
    assert len(every) > 100  # the sweep exercises real facts, not empty results


def test_the_notices_a_structured_only_host_must_show_are_there(swept):
    _c, results = swept
    notices = {name: res["structuredContent"][RESULT_KEY]["notices"] for name, res in results}
    # a finding conditional on a sensor whose reference disagrees
    assert any("sensor problem first" in n for n in notices["rule_run"])
    assert notices["finding_detail"]
    # paging: coverage gave two rows of a longer list
    assert any("shows row" in n or "rows" in n for n in notices["coverage"])
    # research-only listings are said
    assert notices["dataset_search"]
    # each notice is also a fact the model can cite, under the same id as in the text block
    for name, res in results:
        facts = res["structuredContent"][RESULT_KEY]["facts"]
        for k, notice in enumerate(notices[name], start=1):
            nid = next(i for i in facts if i.startswith(f"N{k}@"))
            assert facts[nid] == notice


def test_every_structured_fact_is_the_one_the_ledger_holds(swept):
    """The structured copy is made from the facts the server issued and recorded, never from the
    data: each id resolves in the session's ledger to a fact with that text."""
    c, results = swept
    for name, res in results:
        for fid, text in res["structuredContent"][RESULT_KEY]["facts"].items():
            state, fact = c.server.ledger.lookup(fid)
            assert state == "issued" and fact["text"] == text, (name, fid)


def test_a_draft_from_structured_content_only_is_checked(site):
    """A model that saw only structuredContent cites the facts and reads check_citations'
    verdicts, verified_text and notices from its structuredContent."""
    c = Client(site, modern=False)
    rr = c.call("rule_run", {"facility_id": FID, "min_severity": "ok"})["structuredContent"]
    facts = rr[RESULT_KEY]["facts"]
    leak = next(i for i, t in facts.items() if "AHU__sat_bias" in t and "leaking_valve" in t)
    count = next(i for i in facts if i.startswith("F1@"))
    draft = (
        f"{facts[count]} [{count}].\n"
        f'The valve of "AHU__sat_bias" is not leaking [{leak}].\n'  # a negation no fact makes
        "The weather was mild [F9@rule_run-000000]."  # an id this server never issued
    )
    out = c.call("check_citations", {"text": draft, "detail": True})["structuredContent"]
    verdicts = [s["verdict"] for s in out["sentences"]]
    assert verdicts == ["supported", "unsupported_claim", "unknown_citation"]
    assert out["n_supported"] == 1 and out["provenance"] == "issued"
    notices = out[RESULT_KEY]["notices"]
    # the stripped-caveat notice and the removed-sentence line reach this host too
    assert any("verified_text without them says less than the draft" in n for n in notices)
    assert out["verified_text"].startswith("NOTICE: check_citations removed sentences")
    assert next(iter(out[RESULT_KEY]["facts"])).startswith("F1@check_citations")


def test_structured_facts_cannot_be_passed_back_as_facts(site):
    """Nothing a caller sends is trusted: the compact structured copy (id and text) is not the
    issued fact, a caller cannot add a RESULT_KEY of its own, and an id never issued supports
    nothing."""
    c = Client(site, modern=True)
    rr = c.call("rule_run", {"facility_id": FID})["structuredContent"]
    fid, text = next(iter(rr[RESULT_KEY]["facts"].items()))
    res = c.rpc(
        "tools/call",
        {
            "name": "check_citations",
            "arguments": {"text": f"{text} [{fid}].", "facts": [{"id": fid, "text": text}]},
        },
    )
    assert res["isError"] and res["_meta"]["camber/error"]["code"] == "invalid_params"
    forged = {RESULT_KEY: {"facts": {"F1@x-abcdef": "the valve passes"}, "notices": []}}
    res = c.rpc(
        "tools/call",
        {"name": "check_citations", "arguments": {"text": "It passes [F1@x-abcdef].", **forged}},
    )
    assert res["isError"] and res["_meta"]["camber/error"]["code"] == "invalid_params"
    res = c.call("check_citations", {"text": "The valve passes [F1@x-abcdef]."})
    assert res["structuredContent"]["sentences"][0]["verdict"] == "unknown_citation"


def test_no_capability_may_declare_the_reserved_key():
    assert REGISTRY_KEY == RESULT_KEY
    reg = Registry()
    with pytest.raises(ValueError, match=RESULT_KEY):
        reg.register(
            Capability(
                name="sneaky",
                title="sneaky",
                description="a test capability",
                input_schema={"type": "object"},
                output_schema={"type": "object", "properties": {RESULT_KEY: {"type": "object"}}},
                handler=lambda ctx, a: CapabilityResult({}, "", []),
            )
        )


# --------------------------------------------------------------------------- the prompts

_CALL = re.compile(r"Call (\w+) with (\{[^\n]*?\})(?= |\.|,|$)")


def _prompt_args(name):
    args = {"facility_id": MV if name == "check_savings_claim" else FID}
    extra = {
        "triage_ahu": {"equip": "AHU__sat_bias"},
        "explain_finding": {"finding_id": "leaking_valve@AHU__sat_bias"},
        "compare_equipment": {"equips": "AHU__sat_bias, AHU__healthy"},
        "check_savings_claim": {"claim": "a 12% saving"},
    }
    return {**args, **extra.get(name, {})}


def _drive(rpc, name):
    """Follow a prompt as a structuredContent-only model would: make the calls it names, cite
    each fact it was given (its text, as the fact states it), and check the draft. Returns the
    prompt text, the facts seen and check_citations' structuredContent."""
    got = rpc("prompts/get", {"name": name, "arguments": _prompt_args(name)})
    text = got["messages"][0]["content"]["text"]
    seen: dict = {}
    for tool, args in _CALL.findall(text):
        res = rpc("tools/call", {"name": tool, "arguments": json.loads(args)})
        assert res["isError"] is False, (name, tool, res["content"][0]["text"])
        seen.update(res["structuredContent"][RESULT_KEY]["facts"])
    draft = "\n".join(f"{t.rstrip('.')} [{i}]." for i, t in seen.items() if not i.startswith("N"))
    out = rpc(
        "tools/call",
        {"name": "check_citations", "arguments": {"text": draft, "detail": True, "limit": 1000}},
    )
    return text, seen, out["structuredContent"]


@pytest.mark.parametrize(
    "name",
    [
        "triage_ahu",
        "rcx_executive_summary",
        "explain_finding",
        "check_savings_claim",
        "data_quality_review",
        "compare_equipment",
        "sensor_trust_review",
        "plan_site_visit",
    ],
)
def test_each_prompts_check_citations_step_works_from_structured_content(site, name):
    c = Client(site, modern=False)
    text, seen, out = _drive(c.rpc, name)
    # the prompt says where the ids are in either form, without assuming which one is shown
    assert '"camber"' in text and '"facts"' in text and "Facts (cite by [id]" in text
    assert "NOTICE:" in text and '"notices"' in text
    # (a fact whose text has two sentences gives two)
    assert seen and out["n_sentences"] >= len([i for i in seen if not i.startswith("N")])
    assert out["provenance"] == "issued"
    # every id the model found in structuredContent is one the server resolves
    assert not out["verdicts"]["unknown_citation"] and not out["verdicts"]["forgotten_citation"]
    assert out["n_supported"] >= 0.9 * out["n_sentences"], out["sentences"]


def test_a_prompt_end_to_end_over_the_command_line(site):
    """``camber mcp`` (worker processes, the session's ledger in the server) driven through
    triage_ahu by a client that reads only structuredContent."""
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
    cmd = [sys.executable, "-m", "camber", "mcp", "--store", site["store"], "--allow", FID]
    proc = subprocess.Popen(
        [*cmd, "--config", site["configs"][0]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=site["root"],
    )
    n = [0]

    def rpc(method, params):
        n[0] += 1
        msg = {"jsonrpc": "2.0", "id": n[0], "method": method, "params": params}
        proc.stdin.write(json.dumps(msg).encode() + b"\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, proc.stderr.read().decode()
        return json.loads(line)["result"]

    try:
        rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}})
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        _text, seen, out = _drive(rpc, "triage_ahu")
    finally:
        proc.stdin.close()
        proc.wait(60)
        proc.stdout.close()
        proc.stderr.close()
    assert len(seen) > 10 and out["provenance"] == "issued"
    assert out["n_sentences"] > 5 and out["grounded"], out["sentences"]
