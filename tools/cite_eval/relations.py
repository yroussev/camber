"""Metamorphic relations over the generated cases, and the known violations they ratchet.

    python -m tools.cite_eval.relations [--write-known] [--n 3000] [--seed 0]

Five relations, each a set of violations keyed by case (``tests/test_citations_metamorphic.py``
runs them on a seeded sample):

- ``paraphrase``: the paraphrases of one true claim (``realise`` variants) get one verdict;
- ``operator_flip``: a mutant of a kept true sentence is not kept (an escape is a violation);
- ``region_wrap``: a sentence and the same sentence in a bullet, a heading, a bold label, a table
  row or an HTML comment get one verdict;
- ``unicode_fold``: a non-breaking hyphen, a curly apostrophe or a no-break space changes no
  verdict;
- ``uncited_fact``: issuing one more fact that the draft does not cite never turns a removed
  sentence into a kept one.

Each violation carries a gap tag (``gap:<relation>:<form>:<operator>``). ``--write-known``
writes the current violations, and the per-cell escape counts of the sample, to
``tests/citations_corpus/metamorphic_known.json``: the ratchet the test compares with.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

from . import facttable, forms, run

KNOWN = os.path.join(facttable.REPO, "tests", "citations_corpus", "metamorphic_known.json")
WRAP_FORMS = ("bullet", "heading", "bold_label", "table_row", "html_comment")
RELATIONS = ("paraphrase", "operator_flip", "region_wrap", "unicode_fold", "uncited_fact")


class Bench:
    """The sample, its judge and a cache of results by draft (+ extra facts)."""

    def __init__(self, n: int = 3000, seed: int = 0, facts_path: str = run.FACTS, judge=None):
        self.fb = facttable.load_or_collect(facts_path)
        self.by_id = facttable.facts_index(self.fb)
        self.all = forms.generate(facttable.claims(self.fb))
        self.sample = forms.sample(self.all, n, seed)
        self.seed = seed
        self._judge = judge
        self._cache: dict = {}

    def outcome(self, case: dict, extra_facts=()) -> dict:
        p = run.payload(case, self.by_id)
        if extra_facts:
            p["facts"] = p["facts"] + list(extra_facts)
        k = (p["draft"], tuple(f["id"] for f in p["facts"]))
        if k not in self._cache:
            self._cache[k] = self._judge(p)
        return run.outcome(case, self._cache[k])

    def kept(self, case: dict, extra_facts=()) -> bool:
        return self.outcome(case, extra_facts)["kept"]

    def calls(self) -> int:
        return len(self._cache)


def _tag(rel: str, case: dict) -> str:
    return f"gap:{rel}:{case['form']}:{case['operator']}"


def paraphrase(b: Bench, rng: random.Random, limit: int = 120) -> dict:
    groups: dict = {}
    for c in b.all:
        if c["form"] == "plain" and c["truth"] == "true" and c["operator"] == "none":
            groups.setdefault(c["group"][: c["group"].index("v")], []).append(c)
    keys = sorted(k for k, v in groups.items() if len(v) > 1)
    out = {}
    for k in sorted(rng.sample(keys, min(limit, len(keys)))):
        cs = sorted(groups[k], key=lambda c: c["group"])
        verdicts = {c["key"]: b.kept(c) for c in cs}
        if len(set(verdicts.values())) > 1:
            for c in cs:
                if not verdicts[c["key"]]:
                    out[c["key"]] = _tag("paraphrase", c)
    return out


def operator_flip(b: Bench, rng: random.Random, limit: int = 700) -> dict:
    trues = {
        c["group"]: c
        for c in b.all
        if c["form"] == "plain" and c["truth"] == "true" and c["operator"] == "none"
    }
    falses = [c for c in b.all if c["form"] == "plain" and c["truth"] == "false"]
    falses = sorted(rng.sample(falses, min(limit, len(falses))), key=lambda c: c["key"])
    out = {}
    for c in falses:
        t = trues.get(c["group"])
        if t is None or not b.kept(t):
            continue  # the relation is about a supported sentence
        if b.kept(c):
            out[c["key"]] = _tag("operator_flip", c)
    return out


def region_wrap(b: Bench, rng: random.Random, limit: int = 150) -> dict:
    by: dict = {}
    for c in b.all:
        if c["operator"] in ("swap_pair", "overgeneralise"):
            continue
        by.setdefault((c["group"], c["operator"], c["claim"]), {})[c["form"]] = c
    keys = sorted(k for k, v in by.items() if "plain" in v and any(f in v for f in WRAP_FORMS))
    out = {}
    for k in sorted(rng.sample(keys, min(limit, len(keys)))):
        fs = by[k]
        base = b.kept(fs["plain"])
        for f in WRAP_FORMS:
            if f in fs and b.kept(fs[f]) != base:
                out[fs[f]["key"]] = _tag("region_wrap", fs[f])
    return out


def unicode_fold(b: Bench, rng: random.Random, limit: int = 300) -> dict:
    plain = [c for c in b.all if c["form"] in ("plain", "two_clause", "status_list")]
    out = {}
    for c in sorted(rng.sample(plain, min(limit, len(plain))), key=lambda c: c["key"]):
        vs = forms.fold_variants(c["draft"])
        if not vs:
            continue
        v = vs[rng.randrange(len(vs))]
        twin = {**c, "draft": v, "cores": [v], "key": c["key"] + "~fold"}
        if b.kept(twin) != b.kept(c):
            out[c["key"]] = _tag("unicode_fold", c)
    return out


def uncited_fact(b: Bench, rng: random.Random, limit: int = 300) -> dict:
    pool = [f for fs in b.fb.values() for f in fs]
    cand = [c for c in b.sample if not b.kept(c)]
    out = {}
    for c in sorted(rng.sample(cand, min(limit, len(cand))), key=lambda c: c["key"]):
        others = [f for f in pool if f["id"] not in c["cites"]]
        extra = others[rng.randrange(len(others))]
        if b.kept(c, [extra]):
            out[c["key"]] = _tag("uncited_fact", c)
    return out


def violations(b: Bench) -> dict:
    """``{relation: {case key: gap tag}}`` (each relation with its own seeded draw)."""
    out = {}
    for i, rel in enumerate(RELATIONS):
        rng = random.Random(b.seed * 100 + i)
        out[rel] = globals()[rel](b, rng)
    return out


def cell_escapes(b: Bench) -> dict:
    """``{"form x operator x capability": escapes}`` over the sample (the cell ratchet)."""
    out: dict = {}
    for c in b.sample:
        if c["truth"] != "false":
            continue
        cell = " x ".join((c["form"], c["operator"], c["capability"]))
        out[cell] = out.get(cell, 0) + int(b.outcome(c)["escape"])
    return out


def load_known(path: str = KNOWN) -> dict:
    if not os.path.exists(path):
        return {"relations": {r: {} for r in RELATIONS}, "cell_escapes": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--write-known", action="store_true")
    a = ap.parse_args(argv)
    import tempfile

    from ._worker import Judge

    with tempfile.TemporaryDirectory(prefix="cite-rel-") as tmp:
        b = Bench(a.n, a.seed, judge=Judge(tmp))
        v = violations(b)
        cells = cell_escapes(b)
        for rel, d in v.items():
            print(f"{rel}: {len(d)} violations")
        print(f"{b.calls()} checks; {sum(cells.values())} escapes in the sample")
        if a.write_known:
            with open(KNOWN, "w", encoding="utf-8") as fh:
                json.dump(
                    {"n": a.n, "seed": a.seed, "relations": v, "cell_escapes": cells},
                    fh,
                    indent=1,
                    sort_keys=True,
                )
                fh.write("\n")
            print(f"wrote {KNOWN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
