"""0.108 (#231): the worked example of RCx generated prose (docs/RCX-REPORT.md, "A worked example").

Renders the synthetic RCx demo (the golden report's store and config) with
``examples/rcx_prose/prose.json``, through the same code the docs screenshot uses, and checks the
boxed prose, the cited facts, the slot that falls back and its Appendix A row against the docs.
Offline: the prose comes from the file.
"""

import importlib.util
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.report.rcx import build_rcx_report  # noqa: E402

PROSE = os.path.join(ROOT, "examples", "rcx_prose", "prose.json")
DOC = os.path.join(ROOT, "docs", "RCX-REPORT.md")
SHOT = os.path.join(ROOT, "scripts", "docs_figures.d", "rcx_prose.py")
SUMMARY_IDS = ["K1@summary", "K2@summary", "C1@a8612f9b8b3b", "K3@summary"]
FALLBACK = "issue:5e97d850de5d"
REASON = "1 sentence(s) without a fact citation"


def _shot_module():
    spec = importlib.util.spec_from_file_location("docs_figures_d_rcx_prose", SHOT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def shot():
    return _shot_module()


@pytest.fixture(scope="module")
def example(shot, tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("prose231"))
    rep, html = shot.prose_example_report(ROOT, tmp)
    return rep, html


def _section(rep, sid):
    return next(s for s in rep.sections if s["id"] == sid)


def _doc_section() -> str:
    with open(DOC, encoding="utf-8") as fh:
        doc = fh.read()
    start = doc.index("### A worked example")
    return doc[start : doc.index("\n## ", start)]


def test_the_example_file_is_the_one_the_docs_show():
    with open(PROSE, encoding="utf-8") as fh:
        prose = json.load(fh)
    assert set(prose) == {"exec_summary", FALLBACK}
    block = re.search(r"```json\n(.*?)```", _doc_section(), re.S).group(1)
    assert json.loads(block) == prose


def test_the_summary_prose_is_boxed_with_its_cited_facts(example):
    rep, html = example
    st = rep.prose["exec_summary"]
    assert st["status"] == "rendered" and st["source"] == "file" and st["reason"] == ""
    assert st["cited"] == SUMMARY_IDS
    summ = _section(rep, "summary")["blocks"]
    assert summ[0]["kind"] == "kpis" and summ[1]["kind"] == "prose"  # under the KPI strip
    assert html.count("<div class='prose' role='note'>") == 1  # the issue slot fell back
    assert "Generated text from an offline prose file" in html
    box = re.search(r"<div class='prose' role='note'>.*?</ol>", html, re.S).group(0)
    for fid in SUMMARY_IDS:
        assert f"<sup class='cite'>[{fid}]</sup>" in box, fid
        # each cited fact is listed under the paragraph with the text the fact index holds
        text = rep.facts.by_id(fid).text
        assert f"<li><b>{fid}</b> {text.replace('&', '&amp;')}" in box, fid
    assert "Issue 1 is estimated at $7,727/yr" in box


def test_the_uncited_sentence_makes_the_issue_slot_fall_back(example, tmp_path):
    rep, html = example
    st = rep.prose[FALLBACK]
    assert st["status"] == "fallback" and st["source"] == "file" and st["reason"] == REASON
    assert st["text"] == "" and st["cited"] == []
    key = FALLBACK.split(":", 1)[1]
    assert rep.issues[1].key == key  # issue 2, the economizer above its high limit
    # the page keeps its standard text: the same blocks as the report with no prose
    import _rcx_fixture as fx

    from camber.config import run_config

    fx.make_store(tmp_path)
    base = build_rcx_report(
        run_config(fx.config(loads={"DemoAHU2": {"heating_capacity_kbtuh": 400}}),
                   base_dir=str(tmp_path))
    )  # fmt: skip
    assert _section(rep, f"issue-{key}")["blocks"] == _section(base, f"issue-{key}")["blocks"]
    # the first two sentences alone are grounded: the third, uncited one is what drops the slot
    with open(PROSE, encoding="utf-8") as fh:
        text = json.load(fh)[FALLBACK]
    grounded = text[: text.rindex(" Fixing")]
    two = build_rcx_report(
        run_config(fx.config(loads={"DemoAHU2": {"heating_capacity_kbtuh": 400}}),
                   base_dir=str(tmp_path)),
        prose={FALLBACK: grounded},
    )  # fmt: skip
    assert two.prose[FALLBACK]["status"] == "rendered"


def test_appendix_a_records_the_fallback_row(example):
    rep, html = example
    a = _section(rep, "appendix-a")
    tbl = [b for b in a["blocks"] if b.get("kind") == "table" and b["header"][0] == "Slot"]
    assert tbl and tbl[0]["header"] == ["Slot", "Source", "Why"]
    assert tbl[0]["rows"] == [[FALLBACK, "file", REASON]]
    assert f"<tr><td>{FALLBACK}</td><td>file</td><td>{REASON}</td></tr>" in html
    # the docs show the same row
    assert f"| `{FALLBACK}` | file | {REASON} |" in _doc_section()


def test_the_docs_screenshot_is_cut_from_the_same_render(shot, example):
    _rep, html = example
    page = shot.excerpt(html)
    assert page.count("<div class='prose' role='note'>") == 1
    assert f"<td>{FALLBACK}</td><td>file</td><td>{REASON}</td>" in page
    assert "<section id='issue-" not in page and "Appendix B" not in page
    doc = _doc_section()
    assert "](img/shots/rcx-prose.png)" in doc
    assert os.path.isfile(os.path.join(ROOT, "docs", "img", "shots", "rcx-prose.png"))
