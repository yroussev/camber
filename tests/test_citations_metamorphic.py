"""Metamorphic relations of check_citations on generated cases (tools/cite_eval).

A seeded sample of cases is generated from real capability facts on a synthetic site (frozen
in tests/citations_corpus/generated_facts.json): true claims written in many forms, and mutants
that change one field of a claim (tools/cite_eval/{facttable,realise,forms,mutate}.py). Five
relations must hold (tools/cite_eval/relations.py): a paraphrase keeps the verdict, an operator
flips a supported sentence, wrapping a sentence in a region keeps its verdict, Unicode folding
changes nothing, and an uncited fact never turns a fail into a pass.

The known violations are listed with gap tags in tests/citations_corpus/metamorphic_known.json,
with the escapes per cell (form x operator x capability) of the sample. Both are a ratchet: a
new violation, or more escapes in a cell, fails; and so does a known violation that no longer
happens, or a cell with fewer escapes, until the list is rewritten
(``python -m tools.cite_eval.relations --write-known``) so the improvement is kept.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cite_eval import relations  # noqa: E402
from tools.cite_eval._worker import Judge  # noqa: E402

KNOWN = relations.load_known()
PROMOTE = "then run: python -m tools.cite_eval.relations --write-known"


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    b = relations.Bench(
        KNOWN.get("n", 3000),
        KNOWN.get("seed", 0),
        judge=Judge(str(tmp_path_factory.mktemp("cite-meta"))),
    )
    return b, relations.violations(b)


def test_the_sample_covers_every_form_and_operator(bench):
    b, _ = bench
    from tools.cite_eval.forms import PAIR_FORMS, SINGLE_FORMS
    from tools.cite_eval.mutate import OPERATORS, PAIR_OPERATORS

    forms_seen = {c["form"] for c in b.sample}
    ops_seen = {c["operator"] for c in b.sample}
    assert set(SINGLE_FORMS) | set(PAIR_FORMS) <= forms_seen
    assert {"none", *OPERATORS, *PAIR_OPERATORS} <= ops_seen
    assert {c["truth"] for c in b.sample} == {"true", "false"}


@pytest.mark.parametrize("relation", relations.RELATIONS)
def test_relation_holds_apart_from_known_gaps(bench, relation):
    _, v = bench
    now = v[relation]
    was = KNOWN["relations"].get(relation, {})
    new = {k: now[k] for k in sorted(set(now) - set(was))}
    assert not new, (
        f"{len(new)} new {relation} violation(s) (regressions): {list(new.items())[:10]}"
    )
    fixed = sorted(set(was) - set(now))
    assert not fixed, f"{len(fixed)} known {relation} gap(s) now hold: promote them; {PROMOTE}"


def test_no_cell_escapes_more_than_its_known_count(bench):
    b, _ = bench
    cells = relations.cell_escapes(b)
    known = KNOWN.get("cell_escapes", {})
    worse = {c: (n, known.get(c, 0)) for c, n in sorted(cells.items()) if n > known.get(c, 0)}
    assert not worse, f"E rose in {len(worse)} cell(s) (now, known): {list(worse.items())[:10]}"
    better = {c: (cells.get(c, 0), k) for c, k in sorted(known.items()) if cells.get(c, 0) < k}
    assert not better, (
        f"fewer escapes in {len(better)} cell(s): {list(better.items())[:10]}; {PROMOTE}"
    )
