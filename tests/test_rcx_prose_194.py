"""0.107 (#194): grounded generated prose for the RCx report, and the fact index it cites.

Every prose source here is offline: a prose dict / file, or a stub client. No network.
"""

import json
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.agent import AgentClient, Context, Fact, check, stub_client  # noqa: E402
from camber.agent.verify import extract_cites  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.report import rcx  # noqa: E402
from camber.report.rcx import (  # noqa: E402
    RcxOptions,
    build_rcx_report,
    load_prose,
    prose_prompts,
)

LOADS = {"DemoAHU2": {"heating_capacity_kbtuh": 400}}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rcxprose")
    fx.make_store(tmp)
    return run_config(fx.config(loads=LOADS), base_dir=str(tmp))


@pytest.fixture(scope="module")
def base(run):
    return build_rcx_report(run)


def _top(rep):
    return rep.issues[0].key  # DemoAHU2's held-cold SAT, costed via simultaneous_heat_cool


def _good(rep) -> dict:
    k = _top(rep)
    return {
        "exec_summary": (
            "The report ranks 3 issues [K1@summary]. The 1 costed, non-conditional issue(s) "
            "total $7,727/yr [K2@summary]."
        ),
        f"issue:{k}": (
            f"Supply air on DemoAHU2 is held cold with no reset seen [I1@{k}]. Both coils were "
            f"open 26.7% of occupied hours [F2@{k}], an estimated $7,727/yr [C1@{k}]."
        ),
    }


def _section(rep, sid):
    return next(s for s in rep.sections if s["id"] == sid)


def _prose_blocks(rep):
    return [b for s in rep.sections for b in s["blocks"] if b.get("kind") == "prose"]


# --------------------------------------------------------------------------- verify.check


def _ctx():
    return Context(
        facts=[
            Fact("C1@a1", "cost", "AHU-1", "Estimated $2,897/yr.", {"usd": 2897}),
            Fact("T1@a1", "hours", "AHU-1", "Ran 120 violation hours.", {}),
            Fact("K1@summary", "kpi", "", "The report ranks 3 issues.", {}),
        ]
    )


def test_qualified_and_plain_citations_parse():
    assert extract_cites("a [F1] b [C1@a1] c [K2@summary] [F1]") == ["F1", "C1@a1", "K2@summary"]
    assert extract_cites("[F1@] [F@x] [1@x]") == []  # malformed ids are not citations
    plain = Context(facts=[Fact("F1", "finding", "AHU-1", "Leak 12%.", {})])
    assert check("Leak 12% [F1].", plain).grounded  # existing ids keep working


def test_fully_cited_paragraph_is_kept_intact():
    text = "AHU-1 costs $2,897/yr [C1@a1]. It ran 120 hours [T1@a1]."
    g = check(text, _ctx(), require_cite_each_sentence=True)
    assert g.grounded and g.text == text and g.cited == ["C1@a1", "T1@a1"] and not g.flagged


def test_uncited_sentence_is_dropped():
    g = check("AHU-1 has a problem. It costs $2,897/yr [C1@a1].", _ctx(),
              require_cite_each_sentence=True)  # fmt: skip
    assert g.text == "It costs $2,897/yr [C1@a1]." and not g.grounded
    assert [f["reason"] for f in g.flagged] == ["uncited-sentence"]
    # the plain mode keeps an uncited sentence that states no number
    assert "AHU-1 has a problem." in check("AHU-1 has a problem. X [C1@a1].", _ctx()).text


def test_fabricated_number_is_dropped_even_when_cited():
    g = check("It costs $9,999/yr [C1@a1]. It ran 120 hours [T1@a1].", _ctx(),
              require_cite_each_sentence=True)  # fmt: skip
    assert g.text == "It ran 120 hours [T1@a1]." and not g.grounded
    assert g.flagged[0]["reason"] == "uncited-number"


def test_a_number_must_trace_to_a_fact_its_own_sentence_cites():
    text = "It costs $2,897/yr [T1@a1]. Hours are tracked [C1@a1]."
    assert "2,897" in check(text, _ctx()).text  # plain mode: any cited fact will do
    g = check(text, _ctx(), require_cite_each_sentence=True)
    assert g.text == "Hours are tracked [C1@a1]."


def test_unknown_id_is_stripped():
    g = check("It costs $2,897/yr [C1@a1] [C9@zz].", _ctx(), require_cite_each_sentence=True)
    assert g.text == "It costs $2,897/yr [C1@a1]." and g.cited == ["C1@a1"]
    assert {"reason": "unknown-citation", "text": "C9@zz"} in g.flagged and not g.grounded
    # a sentence whose only citation is unknown has none left, so it goes
    g = check("It costs $2,897/yr [C9@zz].", _ctx(), require_cite_each_sentence=True)
    assert g.text == ""


# --------------------------------------------------------------------------- fact index


def test_fact_ids_are_stable_and_context_qualified(run, base):
    again = build_rcx_report(run)
    a = [(f.id, f.kind, f.text) for f in base.facts.facts]
    assert a == [(f.id, f.kind, f.text) for f in again.facts.facts]
    ids = [f.id for f in base.facts.facts]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[A-Z]\d+@[A-Za-z0-9_\-]+", i) for i in ids)
    ctxs = {i.split("@")[1] for i in ids}
    assert {"summary", "appA", "appB"} <= ctxs and {i.key for i in base.issues} <= ctxs
    k = _top(base)
    f = base.facts.by_id(f"C1@{k}")
    assert f.kind == "cost" and "$7,727/yr" in f.text  # the figure the issue page shows
    assert base.facts.by_id(f"I1@{k}").text.startswith("Issue 1: ")
    # an issue's ids follow its key, not its rank: a report of only that issue gives the same ids
    alone = [(f.id, f.text) for f in base.facts.facts if f.id.endswith(f"@{k}")]
    assert alone and alone[0][0] == f"I1@{k}"
    d = base.to_dict()
    assert [x["id"] for x in d["facts"]] == ids and d["prose"] == {}


def test_fact_index_covers_what_the_tables_render(base):
    kinds = {f.kind for f in base.facts.facts}
    for want in ("kpi", "issue", "cost", "hours", "finding", "action", "why", "caveat",
                 "missing", "assumption"):  # fmt: skip
        assert want in kinds, want
    texts = " ".join(f.text for f in base.facts.facts)
    assert "Cost assumption cool_cop = 3.5." in texts
    assert "uncosted — needs supply_airflow_cfm or design_oa_cfm" in texts  # uncosted reason


def test_zone_chain_counts_are_facts(monkeypatch):
    from camber.report.rcx_prose import _issue_facts

    z = types.SimpleNamespace(
        boxes=["VAV-1", "VAV-2"], served=["VAV-1", "VAV-2", "VAV-3"], chain="sat_high",
        cause="supply air too warm", ahu="AHU-1", symptom="zones can't cool", absorbed=True,
        count=lambda: "2 of 3 boxes",
    )  # fmt: skip
    iss = types.SimpleNamespace(
        key="k1", root=None, members=[], member_costs=[], equip="AHU-1", rank=1,
        severity="fault", confidence="M", cost=None, cost_basis_note="uncosted", conditional=False,
        hours_union=None, why=[], conditional_on=[], dependents=[], zone_links=[z],
    )  # fmt: skip
    S = {"ctx": types.SimpleNamespace(frame=lambda e: None), "exclude_cost": lambda f: None}
    monkeypatch.setattr(rcx, "_advice", lambda S, i, r: ("T", "", ""))
    monkeypatch.setattr(rcx, "_cause_lead", lambda i, frame=None: (None, ""))
    out = []
    _issue_facts(S, iss, lambda kind, c, text, equip="", **d: out.append((kind, text, d)))
    chain = [o for o in out if o[0] == "chain"]
    assert chain and "2 of 3 boxes" in chain[0][1] and chain[0][2] == {"n_boxes": 2, "n_served": 3}


# --------------------------------------------------------------------------- slots


def test_off_by_default_is_byte_identical(run, base):
    html = base.to_html()
    assert not base.prose_enabled and not _prose_blocks(base)
    assert build_rcx_report(run, prose={}).to_html() == html
    assert build_rcx_report(run, client=AgentClient()).to_html() == html  # unwired client
    assert "class='prose'" not in html and ".prose{" not in html
    assert "Reserved for the grounded fact index" in html  # Appendix D unchanged when off


def test_fully_cited_prose_renders_with_marker_and_facts(run):
    rep = build_rcx_report(run, prose=_good(build_rcx_report(run)))
    k = _top(rep)
    assert rep.prose["exec_summary"]["status"] == "rendered"
    assert rep.prose[f"issue:{k}"]["cited"] == [f"I1@{k}", f"F2@{k}", f"C1@{k}"]
    summ = _section(rep, "summary")["blocks"]
    assert summ[0]["kind"] == "kpis" and summ[1]["kind"] == "prose"  # after the KPI strip
    html = rep.to_html()
    assert html.count("<div class='prose' role='note'>") == 2 and ".prose{" in html
    assert "Generated text from an offline prose file" in html
    assert f"<sup class='cite'>[C1@{k}]</sup>" in html
    assert f"<li><b>C1@{k}</b> Issue 1 is estimated at $7,727/yr" in html  # the footnote list
    assert "Appendix D" in html and "Reserved for the grounded fact index" not in html
    assert "<td>K1@summary</td>" in html  # the full index, to audit any citation
    # the other issue pages had no prose and keep their standard text
    other = _section(rep, f"issue-{rep.issues[1].key}")
    assert not [b for b in other["blocks"] if b.get("kind") == "prose"]


def test_stub_client_fills_every_slot_and_prompts_carry_the_facts(run, base):
    good = _good(base)
    seen = []

    def complete(prompt, **opts):
        seen.append((prompt, opts))
        for text in good.values():
            ids = extract_cites(text)
            if all(f"[{i}]" in prompt for i in ids):
                return text
        return "Nothing here is cited."

    rep = build_rcx_report(run, client=stub_client(complete, system="sys"))
    assert len(seen) == len(rep.issues) + 1 and seen[0][1]["system"] == "sys"
    assert rep.prose["exec_summary"]["source"] == "llm"
    assert [v["status"] for v in rep.prose.values()].count("rendered") == 2
    assert "Generated text from a language model" in rep.to_html()
    assert (
        seen[0][0] == prose_prompts(base)["exec_summary"]
    )  # the documented prompt is the sent one


def test_uncited_or_fabricated_slot_falls_back_and_appendix_a_lists_it(run, base):
    k = _top(base)
    prose = {
        "exec_summary": "The report ranks 3 issues [K1@summary]. The building is wasteful.",
        f"issue:{k}": f"Both coils were open 26.7% of hours, costing $9,100/yr [F2@{k}].",
    }
    rep = build_rcx_report(run, prose=prose)
    assert not _prose_blocks(rep)
    assert rep.prose["exec_summary"]["status"] == "fallback"
    assert "1 sentence(s) without a fact citation" in rep.prose["exec_summary"]["reason"]
    assert "figure no cited fact holds" in rep.prose[f"issue:{k}"]["reason"]
    # the pages keep their deterministic text
    for sid in ("summary", f"issue-{k}"):
        assert _section(rep, sid)["blocks"] == _section(base, sid)["blocks"]
    html = rep.to_html()
    a = _section(rep, "appendix-a")
    tbl = [b for b in a["blocks"] if b.get("kind") == "table" and b["header"][0] == "Slot"][0]
    assert [r[0] for r in tbl["rows"]] == ["exec_summary", f"issue:{k}"]
    assert "Generated prose not shown" in html


def test_everything_dropped_falls_back_to_the_template(run, base):
    rep = build_rcx_report(run, prose={"exec_summary": "Costs are $1/yr. All good."})
    assert rep.prose["exec_summary"]["status"] == "fallback"
    assert _section(rep, "summary")["blocks"] == _section(base, "summary")["blocks"]
    assert any("exec_summary" in str(b) for b in _section(rep, "appendix-a")["blocks"])


def test_unknown_id_is_stripped_and_the_slot_still_renders(run, base):
    k = _top(base)
    rep = build_rcx_report(
        run, prose={f"issue:{k}": f"Supply air is held cold [I1@{k}] [Z9@{k}] [K1@summary]."}
    )
    st = rep.prose[f"issue:{k}"]
    # K1@summary is a real fact, but not one an issue page may cite: stripped like an unknown id
    assert st["status"] == "rendered" and st["text"] == f"Supply air is held cold [I1@{k}]."
    assert "2 citation(s) of an id not in the fact index" in st["reason"]


def test_engineer_notes_override_generated_prose(run, base):
    k = _top(base)
    rep = build_rcx_report(run, prose=_good(base), notes={"exec_summary": "Walked the site."})
    assert rep.prose["exec_summary"]["status"] == "overridden"
    assert rep.prose[f"issue:{k}"]["status"] == "rendered"
    assert not [b for b in _section(rep, "summary")["blocks"] if b.get("kind") == "prose"]
    assert "Walked the site." in rep.to_html()


def test_client_error_falls_back(run):
    def boom(prompt, **opts):
        raise RuntimeError("offline")

    rep = build_rcx_report(run, client=stub_client(boom))
    assert {v["reason"] for v in rep.prose.values()} == {"generation failed (RuntimeError)"}
    assert not _prose_blocks(rep)


def test_prose_file_is_deterministic_and_config_driven(run, base, tmp_path):
    path = tmp_path / "prose.json"
    path.write_text(json.dumps({**_good(base), "issue:nope": "x"}))
    assert load_prose(str(path))["exec_summary"].startswith("The report ranks")
    rcx_cfg = dict(run.config["report"]["rcx"], prose="prose.json")
    rep_cfg = dict(run.config["report"], rcx=rcx_cfg)
    opts = RcxOptions.from_config(rep_cfg, base_dir=str(tmp_path))
    assert opts.prose == str(path)  # report.rcx.prose, relative to the config's folder
    one = build_rcx_report(run, options=opts).to_html()
    two = build_rcx_report(run, options=opts).to_html()
    assert one == two and one.count("class='prose'") == 2
    path.write_text("[]")
    with pytest.raises(ValueError, match="keyed by slot"):
        load_prose(str(path))


def test_pdf_path_carries_the_prose(run, base, monkeypatch):
    calls = []

    class HTML:
        def __init__(self, string=None, url_fetcher=None, media_type=None):
            calls.append(string)

        def write_pdf(self):
            return b"%PDF-1.7 fake"

    fake = types.ModuleType("weasyprint")
    fake.__version__ = "99.0"
    fake.URLFetcher = lambda **kw: None
    fake.HTML = HTML
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    rep = build_rcx_report(run, prose=_good(base))
    assert rep.to_pdf() == b"%PDF-1.7 fake"
    assert calls[-1].replace(rcx._pdf_head(rep), "") == rep.to_html()
    assert "class='prose'" in calls[-1]


def test_real_pdf_with_prose(run, base, tmp_path):
    try:
        rcx._weasyprint()
    except rcx.MissingPdfExtra as e:
        pytest.skip(f"no usable WeasyPrint: {e}")
    pypdf = pytest.importorskip("pypdf")
    rep = build_rcx_report(run, prose=_good(base))
    rep.to_pdf(tmp_path / "p.pdf")
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(str(tmp_path / "p.pdf")).pages)
    assert "Generated text from an offline prose file" in text


def test_cli_prose_flags(tmp_path, capsys):
    fx.make_store(tmp_path)
    cfg = fx.config(loads=LOADS)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    out, prompts = tmp_path / "r.html", tmp_path / "prompts.json"
    assert main(["report", str(path), "--out", str(out), "--layout", "rcx",
                 "--prose-prompts", str(prompts)]) == 0  # fmt: skip
    p = json.loads(prompts.read_text())
    assert "exec_summary" in p and "[K1@summary]" in p["exec_summary"]
    assert "prose:" not in capsys.readouterr().out  # no prose source -> nothing said
    rep = build_rcx_report(run_config(cfg, base_dir=str(tmp_path)))
    prose = tmp_path / "prose.json"
    prose.write_text(json.dumps(_good(rep)))
    assert main(["report", str(path), "--out", str(out), "--layout", "rcx",
                 "--prose", str(prose)]) == 0  # fmt: skip
    assert "prose: 2 slot(s) shown, 0 fell back" in capsys.readouterr().out
    assert "class='prose'" in out.read_text()
    assert main(["report", str(path), "--out", str(out), "--layout", "audit",
                 "--prose", str(prose)]) == 2  # fmt: skip
    assert "--prose is for --layout rcx" in capsys.readouterr().err
