"""The regression corpus of check_citations (tests/citations_corpus/*.jsonl).

Each entry is one draft, the facts it cites exactly as a server issued them, the facility names
the checker reads, and the expected verdict: the draft's (``supported``: every sentence is
supported; ``fail``: at least one is not) and, where the label is per sentence, each sentence's.
The entries come from the acceptance-test repros (``tools/cite_eval/import_uat.py``): every
round-9 finding is among them. The facts are issued to a session ledger and the draft checked as
an MCP client does.

An entry the checker does not meet yet is ``xfail`` with a ``gap:<name>`` tag, and the xfail is
strict: when the checker starts to meet it, the test fails as XPASS, and the entry must be
promoted (its ``xfail`` and ``gap`` removed) -- the ratchet. A passing entry that starts to fail
is a regression.
"""

import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval._worker import Judge  # noqa: E402
from tools.cite_eval.import_uat import FINDINGS  # noqa: E402

CORPUS = os.path.join(ROOT, "tests", "citations_corpus")
TAGS = ("form", "operator", "region", "capability", "source")


def _entries() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(CORPUS, "*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if line.strip():
                    e = json.loads(line)
                    e["_file"] = f"{os.path.basename(path)}:{n}"
                    out.append(e)
    return out


ENTRIES = _entries()


def _params():
    for e in ENTRIES:
        marks = []
        if e.get("xfail"):
            marks.append(pytest.mark.xfail(strict=True, reason=e.get("gap", "gap:unnamed")))
        yield pytest.param(e, id=e["id"], marks=marks)


@pytest.fixture(scope="module")
def judge(tmp_path_factory):
    return Judge(str(tmp_path_factory.mktemp("cite-corpus")))


def _judged(judge, e: dict) -> dict:
    r = judge(
        {
            "key": e["id"],
            "draft": e["draft"],
            "facts": e["facts"],
            "allowed": e.get("allowed", []),
            "names": e.get("names", {}),
        }
    )
    assert "error" not in r, r.get("error")
    return r


@pytest.mark.parametrize("entry", list(_params()))
def test_corpus_entry(judge, entry):
    r = _judged(judge, entry)
    verdicts = [s[1] for s in r["sentences"]]
    exp = entry["expected"]
    where = f"{entry['_file']} {entry['source']}"
    if exp["draft"] == "supported":
        assert r["grounded"] and all(v == "supported" for v in verdicts), (where, r["sentences"])
    else:
        assert not r["grounded"], (where, r["sentences"])
    by_text = {}
    for text, verdict, _why in r["sentences"]:
        by_text.setdefault(" ".join(text.split()), verdict)
    for s in exp.get("sentences", []):
        got = by_text.get(" ".join(s["text"].split()))
        assert got is not None, (where, "sentence not found", s["text"], r["sentences"])
        assert (got == "supported") == (s["verdict"] == "supported"), (where, s["text"], got)


def test_corpus_entries_are_tagged():
    assert ENTRIES, "the corpus is empty"
    ids = [e["id"] for e in ENTRIES]
    assert len(ids) == len(set(ids)), "entry ids repeat"
    for e in ENTRIES:
        assert all(e["tags"].get(t) for t in TAGS), e["_file"]
        assert e["expected"]["draft"] in ("supported", "fail"), e["_file"]
        if e.get("xfail"):
            assert str(e.get("gap", "")).startswith("gap:"), e["_file"]
        else:
            assert "gap" not in e, e["_file"]


def test_every_round9_finding_is_in_the_corpus():
    found = {e["tags"].get("finding") for e in ENTRIES}
    missing = sorted(f for f in FINDINGS if f not in found)
    assert not missing, missing


def test_corpus_has_no_local_paths():
    for e in ENTRIES:
        blob = json.dumps(e, ensure_ascii=False)
        assert "/Users/" not in blob and "/home/" not in blob, e["_file"]
