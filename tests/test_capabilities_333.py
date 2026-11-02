"""0.112 (#333): default results a host's tool-output limit takes, M&V period dates, and the
withheld facilities named.

- every capability's default result, as the MCP ``structuredContent`` a host gives the model
  (the data and the ``camber`` facts and notices), fits :data:`HOST_CHARS` on a store whose
  pages fill up; a page that is not the whole list says the next ``offset``;
- ``rcx_report`` returns as many of the best-ranked issues as fit, best first, and its fact
  index names each fact without repeating its text;
- ``mv_summary`` gives each meter's baseline and reporting dates in its data and its facts, a
  page of meters at a time;
- ``facilities`` names the research-only facilities it withholds, and says why.

Every test builds its store in its own tmp directory.
"""

import json
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
    _page,  # noqa: E402
    default_registry,
)
from camber.capabilities._cap_drift import _shared_notes  # noqa: E402
from camber.mcp import RESULT_KEY, McpServer  # noqa: E402
from camber.model.roles import Role  # noqa: E402

#: the tool-output limit docs/MCP.md documents: characters of structuredContent
HOST_CHARS = 25_000
FID = fx.FID
FLEET = "fleet-site"
MV = "mv-site"
REG = default_registry()
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}
#: 36 units with long ids, six points each: 216 points, every default page full
UNITS = [f"AHU-North-Wing-Level-{i // 10:02d}-Unit-{i:03d}" for i in range(36)]
METERS = [f"Main-Building-Meter-{i:02d}" for i in range(12)]
#: enough facilities to fill a page of the listing
SITES = [f"campus-building-{i:02d}" for i in range(45)]


def _unit(seed: int) -> pd.DataFrame:
    """Ten days of hourly AHU trends with a hole every six hours (gaps to list)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(fx.START, periods=24 * 10, freq="h")
    sat = pd.Series(55.0 + rng.normal(0, 0.3, len(idx)), index=idx)
    sat.iloc[[i for i in range(len(idx)) if i % 6 == 3]] = np.nan
    n = len(idx)
    return pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat,
            Role.RETURN_AIR_TEMP: 72.0 + rng.normal(0, 0.4, n),
            Role.MIXED_AIR_TEMP: 66.0 + rng.normal(0, 0.4, n),
            Role.OAT: 60.0 + rng.normal(0, 2.0, n),
            Role.OA_DAMPER: 30.0 + rng.normal(0, 1.0, n),
            Role.DUCT_STATIC: 1.5 + rng.normal(0, 0.05, n),
        },
        index=idx,
    )


def _meter(seed: int, days: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


def _write(root, name: str, config: dict) -> str:
    path = os.path.join(str(root), name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return path


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("cap333")
    st = fx.make_store(root, meta=None)
    for i, unit in enumerate(UNITS):
        st.write_role_frame(_unit(i), facility_id=FLEET, equip=unit, equip_class="AHU")
    for i, meter in enumerate(METERS):
        st.write_role_frame(_meter(i), facility_id=MV, equip=meter, equip_class="METER")
    tiny = _unit(0).iloc[:24, :1]
    for fid in SITES:
        st.write_role_frame(tiny, facility_id=fid, equip="AHU-1", equip_class="AHU")
    st.write_role_frame(fx.ahu_frame(seed=5), facility_id="nc-one", equip="AHU-1")
    st.register_facility("nc-one", name="NC One", dataset=fx.NC)
    st.write_role_frame(fx.ahu_frame(seed=6), facility_id="nc-two", equip="AHU-1")
    st.register_facility("nc-two", name="NC Two", dataset=fx.NC)
    mv_cfg = _write(
        root,
        "mv.json",
        {
            "source": {"kind": "store", "store": "store", "facility_id": MV},
            "equipment": [{"class": "METER"}],
            "mv": [
                {
                    "class": "METER",
                    "role": "power",
                    "period": ["2024-01-01", "2024-03-31"],
                    "reporting_period": ["2024-04-01", "2024-08-27"],
                }
            ],
        },
    )
    st.points()
    return {"store": st, "mv_cfg": mv_cfg}


def _ctx(site, **kw):
    allowed = frozenset({FID, FLEET, MV, "nc-one", "nc-two", *SITES})
    return CallContext(store=site["store"].root, allowed_facilities=allowed, **kw)


def _structured(server: McpServer, name: str, arguments: dict) -> dict:
    """What a host gives the model of one call: its structuredContent (an error fails)."""
    out = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": MODERN_META},
        }
    )
    res = out["result"]
    assert not res["isError"], (name, res["content"][0]["text"])
    return res["structuredContent"]


def _chars(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


# --------------------------------------------------------------------------- 1. the size limit


def test_every_default_result_fits_a_hosts_tool_output_limit(site):
    """Each capability with only its required arguments, on facilities whose pages fill up."""
    assert _page.RESULT_CHARS < HOST_CHARS
    server = McpServer(REG, _ctx(site))
    mv_server = McpServer(REG, _ctx(site, config=site["mv_cfg"]))
    run = _structured(server, "rule_run", {"facility_id": FID})
    cited = list(run[RESULT_KEY]["facts"])[:2]
    long_text = " ".join(
        f"The rule run lists the finding of this fact [{cited[0]}]." for _ in range(80)
    )
    calls = [
        (server, "facilities", {}),
        (server, "points", {"facility_id": FLEET}),
        (server, "coverage", {"facility_id": FLEET}),
        (server, "sensor_trust", {"facility_id": FLEET}),
        (server, "rule_run", {"facility_id": FLEET}),
        (server, "rule_run", {"facility_id": FID}),
        (
            server,
            "finding_detail",
            {"facility_id": FID, "finding_id": run["findings"][0]["finding_id"]},
        ),
        (server, "rcx_report", {"facility_id": FID}),
        (server, "rcx_report", {"facility_id": FLEET}),
        (server, "drift", {"facility_id": FLEET, "baseline": ["2026-03-02", "2026-03-08"]}),
        (server, "dataset_search", {}),
        (server, "dataset_entry", {"dataset_id": "at-30bldg-sensors"}),
        (server, "rule_params", {}),
        (server, "rule_params", {"detail": True}),
        (server, "check_citations", {"text": long_text}),
        (server, "check_citations", {"text": long_text, "detail": True}),
        (mv_server, "mv_summary", {"facility_id": MV}),
        (mv_server, "mv_summary", {"facility_id": MV, "claim": "a 12% saving in 2024"}),
    ]
    assert {name for _, name, _ in calls} == {c.name for c in REG.capabilities()}
    sizes = {}
    for srv, name, args in calls:
        sc = _structured(srv, name, args)
        sizes[(name, json.dumps(args)[:60])] = n = _chars(sc)
        assert n <= HOST_CHARS, (name, args, n)
        # a page that is not the whole list says how to get the next one, with the argument
        for notice in sc[RESULT_KEY]["notices"]:
            if " shows row" in notice and "offset=0" not in notice:
                assert re.search(r"call again with (offset|limit)=\d+", notice), notice
    # the pages are full here: the limit is met by the page sizes, not by a small store
    full = {"facilities", "points", "coverage", "sensor_trust", "dataset_search", "rule_params"}
    full |= {"mv_summary"}
    for srv, name, args in calls:
        if name in full and not args.get("claim") and not args.get("detail"):
            assert _structured(srv, name, args)[RESULT_KEY]["notices"], name


def test_a_paging_notice_names_the_next_offset_not_a_larger_limit(site):
    res = REG.call("sensor_trust", {"facility_id": FLEET}, _ctx(site))
    assert len(res.data["points"]) == 40 and res.truncated
    notice = next(b for b in res.banners if "'points' shows" in b)
    assert notice.endswith("call again with offset=40 for the next page")
    nxt = REG.call("sensor_trust", {"facility_id": FLEET, "offset": 40}, _ctx(site))
    assert nxt.data["points"][0] not in res.data["points"]


# --------------------------------------------------------------------------- 2. rcx_report


def test_rcx_report_pages_the_best_ranked_issues_first_within_the_budget(site, monkeypatch):
    ctx = _ctx(site)
    whole = REG.call("rcx_report", {"facility_id": FID, "top_n": 200}, ctx)
    ranks = [i["rank"] for i in whole.data["issues"]]
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) >= 3
    # a budget that takes one issue: page 1 is the top issue, the notice gives the next offset
    monkeypatch.setattr(_page, "RESULT_CHARS", 1)
    one = REG.call("rcx_report", {"facility_id": FID}, ctx)
    assert [i["rank"] for i in one.data["issues"]] == [1]
    notice = next(b for b in one.banners if "'issues' shows" in b)
    assert "call again with offset=1 for the next page" in notice
    two = REG.call("rcx_report", {"facility_id": FID, "offset": 1}, ctx)
    assert [i["rank"] for i in two.data["issues"]] == [2]
    assert one.data["kpis"]["n_issues"] == len(ranks)  # the KPIs count every issue
    # a caller's top_n is the page size, whatever the budget
    assert len(REG.call("rcx_report", {"facility_id": FID, "top_n": 2}, ctx).data["issues"]) == 2
    monkeypatch.undo()
    page1 = REG.call("rcx_report", {"facility_id": FID}, ctx)
    shown = [i["rank"] for i in page1.data["issues"]]
    assert shown == list(range(1, len(shown) + 1))


def test_rcx_fact_index_names_facts_without_repeating_their_text(site):
    res = REG.call("rcx_report", {"facility_id": FID}, _ctx(site))
    index = res.data["fact_index"]
    assert index and all(set(f) == {"id", "kind"} for f in index)
    by_id = {f["id"]: f for f in res.facts}
    assert all(f["id"] in by_id and by_id[f["id"]]["kind"] == f["kind"] for f in index)


# --------------------------------------------------------------------------- 3. mv_summary


def test_mv_summary_states_each_meters_baseline_and_reporting_dates(site):
    res = REG.call("mv_summary", {"facility_id": MV}, _ctx(site, config=site["mv_cfg"]))
    rows = res.data["findings"]
    base = next(r for r in rows if r["rule"] == "mv_baseline")
    save = next(r for r in rows if r["rule"] == "mv_savings" and r["equip"] == base["equip"])
    assert (base["metrics"]["baseline_start"], base["metrics"]["baseline_end"]) == (
        "2024-01-01",
        "2024-03-31",
    )
    assert "reporting_start" not in base["metrics"]
    m = save["metrics"]
    assert (m["baseline_start"], m["baseline_end"]) == ("2024-01-01", "2024-03-31")
    assert (m["reporting_start"], m["reporting_end"]) == ("2024-04-01", "2024-08-27")
    facts = {f["id"]: f for f in res.facts}
    fact = facts[save["fact"]]
    assert fact["baseline_period"] == ["2024-01-01", "2024-03-31"]
    assert fact["reporting_period"] == ["2024-04-01", "2024-08-27"]
    assert "baseline period 2024-01-01 to 2024-03-31" in fact["text"]
    assert "reporting period 2024-04-01 to 2024-08-27" in fact["text"]
    assert "baseline period 2024-01-01 to 2024-03-31" in facts[base["fact"]]["text"]


def test_mv_summary_names_a_calendar_year():
    from camber.capabilities._cap_mv_summary import _span

    assert _span(["2016-01-01", "2016-12-31"]) == "2016-01-01 to 2016-12-31 (the year 2016)"
    assert _span(["2016-01-01", "2017-12-31"]) == "2016-01-01 to 2017-12-31"


def test_mv_summary_pages_meters_with_all_their_findings(site):
    ctx = _ctx(site, config=site["mv_cfg"])
    res = REG.call("mv_summary", {"facility_id": MV}, ctx)
    shown = {r["equip"] for r in res.data["findings"]}
    assert len(shown) == 5 and res.truncated
    assert res.data["meters"] == sorted(METERS)  # every meter is named
    assert sum(res.data["counts"].values()) == 2 * len(METERS)  # the counts cover every meter
    notice = next(b for b in res.banners if "'meters' shows" in b)
    assert "rows 1-5 of 12" in notice and "offset=5" in notice
    assert all(sum(r["equip"] == e for r in res.data["findings"]) == 2 for e in shown)
    nxt = REG.call("mv_summary", {"facility_id": MV, "offset": 5}, ctx)
    assert not shown & {r["equip"] for r in nxt.data["findings"]}


# --------------------------------------------------------------------------- 4. facilities


def test_facilities_names_the_research_only_facilities_it_withholds(site):
    res = REG.call("facilities", {}, _ctx(site))
    listed = {f["facility_id"] for f in res.data["facilities"]}
    assert res.data["count"] == 3 + len(SITES) and len(listed) == 40  # a page of 40
    assert not listed & {"nc-one", "nc-two"} and res.data["withheld_research_only"] == 2
    notice = next(b for b in res.banners if "research-only" in b)
    assert "nc-one, nc-two" in notice and "The id is right" in notice
    assert "refused_licence" in notice and os.sep + "tmp" not in notice


# --------------------------------------------------------------------------- 5. drift


def test_drift_says_a_shared_long_note_once():
    note = "a long threshold note " * 10
    rows = [{"metrics": {"note": note, "x": i}} for i in range(3)] + [{"metrics": {"x": 9}}]
    out = _shared_notes(rows)
    assert out == [f"The note of 3 finding(s) here, said once: {note}"]
    assert all("note" not in r["metrics"] for r in rows)
    lone = [{"metrics": {"note": note}}]
    assert _shared_notes(lone) == [] and lone[0]["metrics"]["note"] == note
