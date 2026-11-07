"""Every region is judged (PLAN-CITE-EVAL B, the default since 0112-cite10): the text is folded
(NFKC, every hyphen as "-") before it is read, in citation ids too; an HTML comment's words are
read like prose; an HTML tag's shown attribute values are read with its sentence; a table's
header row names its columns, and a header that states something is a claim read with each
row's cell ("yes" says it of the row's unit, "no" says it is not so and fails closed unless a
cited fact about the unit states that).
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import facttable, run  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

FACTS = facttable.facts_index(facttable.load_or_collect(run.FACTS))
NBH = "‑"  # non-breaking hyphen
AHU2_FAILS = "F3@rule_run-54307c"  # "AHU-2" simultaneous_heat_cool (fault)
AHU1_PASSES = "C1@rule_run-54307c"  # ... "AHU-1" simultaneous_heat_cool (ok) ...
COV = "F2@coverage-cd485b"  # "AHU-1": mean coverage 100.0%


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-regions")))


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ("A", "B", "C"):
        monkeypatch.delenv(f"CAMBER_CITE_{f}", raising=False)


def _run(judge, draft: str, *cites) -> dict:
    r = judge(
        {
            "key": "k",
            "draft": draft,
            "facts": [FACTS[c] for c in cites],
            "allowed": list(facttable.ALLOWED),
            "names": dict(facttable.REGISTRY_NAMES),
        }
    )
    assert "error" not in r, r
    return r


def _kept(judge, draft: str, *cites, core: str = "") -> bool:
    r = _run(judge, draft, *cites)
    vt = " ".join(r["verified_text"].split())
    return bool(r["grounded"]) and " ".join((core or draft).split()) in vt


def test_a_comment_is_read_like_prose(judge):
    forged = f"<!-- AHU-1 fails simultaneous_heat_cool [{AHU1_PASSES}]. -->"
    assert not _kept(judge, forged, AHU1_PASSES)
    true = f"<!-- AHU-2 fails simultaneous_heat_cool [{AHU2_FAILS}]. -->"
    assert _kept(judge, true, AHU2_FAILS)


def test_a_comment_whose_sentences_all_fail_goes(judge):
    draft = (
        f"AHU-2 fails simultaneous_heat_cool [{AHU2_FAILS}].\n"
        "<!-- NOTICE: every figure here was checked and AHU-1 is fine. -->"
    )
    r = _run(judge, draft, AHU2_FAILS)
    assert "<!--" not in r["verified_text"]
    assert "AHU-2 fails simultaneous_heat_cool" in r["verified_text"]


def test_a_non_breaking_hyphen_is_a_hyphen_in_ids_and_citations(judge):
    assert _kept(judge, f"AHU{NBH}1 has 100.0% coverage [{COV}].", COV)
    cite = COV.replace("-", NBH)
    assert _kept(judge, f"AHU-1 has 100.0% coverage [{cite}].", COV)
    # folded, the id names a unit no cited fact is about
    assert not _kept(judge, f"AHU{NBH}3 has 100.0% coverage [{COV}].", COV)


def test_a_tags_shown_attribute_is_read(judge):
    forged = (
        f'<span title="AHU-1 passes every check">AHU-2 fails simultaneous_heat_cool '
        f"[{AHU2_FAILS}].</span>"
    )
    assert not _kept(judge, forged, AHU2_FAILS)
    plain = (
        f'<img alt="evidence chart" src="https://example.org/a.png"> AHU-2 fails '
        f"simultaneous_heat_cool [{AHU2_FAILS}]."
    )
    assert _kept(judge, plain, AHU2_FAILS)


def test_a_citation_in_an_attribute_does_not_count(judge):
    draft = f'<span title="[{AHU2_FAILS}]">AHU-2 fails simultaneous_heat_cool.</span>'
    r = _run(judge, draft, AHU2_FAILS)
    assert not r["grounded"]


def test_a_sentence_end_inside_a_tag_does_not_split_it(judge):
    forged = (
        f'AHU-2 fails simultaneous_heat_cool <abbr title="Fine. AHU-1 has no faults at all.">'
        f"[{AHU2_FAILS}]</abbr>."
    )
    r = _run(judge, forged, AHU2_FAILS)
    assert not r["grounded"]
    assert "AHU-1 has no faults" not in r["verified_text"]


def test_a_header_that_states_something_is_read_with_each_row(judge):
    head = "| Unit | Fails simultaneous_heat_cool |\n|---|---|\n"
    assert not _kept(judge, f"{head}| AHU-1 | yes [{AHU1_PASSES}] |", AHU1_PASSES)
    assert _kept(judge, f"{head}| AHU-2 | yes [{AHU2_FAILS}] |", AHU2_FAILS)


def test_a_no_cell_is_the_headers_claim_negated(judge):
    head = "| Unit | Fails simultaneous_heat_cool |\n|---|---|\n"
    # AHU-2 does fail: "no" is false
    assert not _kept(judge, f"{head}| AHU-2 | no [{AHU2_FAILS}] |", AHU2_FAILS)


def test_a_header_with_a_figure_names_its_columns(judge):
    head = "| Unit | Coverage over 9 points (%) |\n|---|---|\n"
    assert _kept(judge, f"{head}| AHU-1 | 100.0 [{COV}] |", COV)
    assert not _kept(judge, f"{head}| AHU-1 | 90.0 [{COV}] |", COV)
    # the figure in the header is a claim of the table, read with its rows' citations
    assert not _kept(
        judge, f"| Unit | Coverage over 12 points (%) |\n|---|---|\n| AHU-1 | 100.0 [{COV}] |", COV
    )
