"""Run the generated cases through ``check_citations`` and report E and S per cell.

    python -m tools.cite_eval.run [--sample N] [--seed S] [--python PY | --venv DIR]
                                  [--label NAME] [--out RESULT.json] [--history HISTORY.csv]
                                  [--corpus-out CORPUS.json]

E = P(a false case is kept with ``grounded: true``) and S = P(a true case is removed), overall
and per cell (form x operator x capability), per form, per operator and per capability. The
cases come from :mod:`.forms` over the fact table of :mod:`.facttable` (read from ``--facts``,
by default the committed ``tests/citations_corpus/generated_facts.json``, so every run judges
the same cases). With ``--python`` or ``--venv`` the checker of that interpreter judges them
(a worker process imports only that interpreter's ``camber``): an installed build is
baselined without touching it. ``--corpus-out`` also judges the regression corpus
(``tests/citations_corpus/*.jsonl``) with that checker: entries met, listed xfail, listed
passing but not met (regressions) and xfail now met. ``--history`` appends one CSV row per run.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import os
import subprocess
import sys
import tempfile

from . import facttable, forms

HERE = os.path.dirname(os.path.abspath(__file__))
FACTS = os.path.join(facttable.REPO, "tests", "citations_corpus", "generated_facts.json")
HISTORY_FIELDS = [
    "when",
    "label",
    "checker",
    "seed",
    "n_cases",
    "n_false",
    "n_true",
    "E",
    "E_upper95",
    "S",
    "cells",
    "cells_E_over_2pct",
    "worst_cell",
    "worst_cell_E",
    "corpus_entries",
    "corpus_met",
    "corpus_xfail_listed",
    "corpus_regressions",
    "corpus_promotable",
]
CORPUS = os.path.join(facttable.REPO, "tests", "citations_corpus")


def corpus_results(python: str | None = None, jobs: int = 1, corpus: str = CORPUS) -> dict:
    """Judge every regression-corpus entry: ``{"entries", "met", "xfail_listed",
    "regressions" (listed as passing, not met), "promotable" (listed xfail, met), "rows"}``."""
    import glob

    entries = []
    for path in sorted(glob.glob(os.path.join(corpus, "*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            entries += [json.loads(line) for line in fh if line.strip()]
    payloads = [
        {
            "key": e["id"],
            "draft": e["draft"],
            "facts": e["facts"],
            "allowed": e.get("allowed", []),
            "names": e.get("names", {}),
        }
        for e in entries
    ]
    res = judge(payloads, python, jobs)
    rows = []
    for e in entries:
        r = res.get(e["id"], {})
        verdicts = [s[1] for s in r.get("sentences", [])]
        if r.get("error"):
            met = False
        elif e["expected"]["draft"] == "supported":
            met = bool(r.get("grounded")) and all(v == "supported" for v in verdicts)
        else:
            met = not r.get("grounded")
        rows.append(
            {
                "id": e["id"],
                "source": e["source"],
                "xfail": bool(e.get("xfail")),
                "gap": e.get("gap"),
                "finding": e["tags"].get("finding"),
                "expected": e["expected"]["draft"],
                "met": met,
                "grounded": r.get("grounded"),
                "draft": e["draft"],
            }
        )
    return {
        "entries": len(rows),
        "met": sum(r["met"] for r in rows),
        "xfail_listed": sum(r["xfail"] for r in rows),
        "regressions": sum(1 for r in rows if not r["xfail"] and not r["met"]),
        "promotable": sum(1 for r in rows if r["xfail"] and r["met"]),
        "rows": rows,
    }


def _ws(s: str) -> str:
    return " ".join(str(s or "").split())


def kept(case: dict, res: dict) -> bool:
    """Every core of the case is still in the verified text."""
    vt = _ws(res.get("verified_text", ""))
    return all(_ws(c) in vt for c in case["cores"])


def outcome(case: dict, res: dict) -> dict:
    """``{"kept", "grounded", "escape", "strip"}`` for one judged case."""
    if res.get("error"):
        k, g = False, False
    else:
        k, g = kept(case, res), bool(res.get("grounded"))
    return {
        "kept": k,
        "grounded": g,
        "escape": case["truth"] == "false" and k and g,
        "strip": case["truth"] == "true" and not k,
    }


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + m) / d


def payload(case: dict, facts_by_id: dict) -> dict:
    """What the worker needs for ``case``: its draft and the facts it cites."""
    return {
        "key": case["key"],
        "draft": case["draft"],
        "facts": [facts_by_id[i] for i in case["cites"] if i in facts_by_id],
        "allowed": list(facttable.ALLOWED),
        "names": dict(facttable.REGISTRY_NAMES),
        **({"per_sentence": case["per_sentence"]} if "per_sentence" in case else {}),
    }


def judge(payloads: list, python: str | None = None, jobs: int = 1) -> dict:
    """``{key: result}`` for each payload: in this interpreter, or in ``python``'s."""
    if python is None:
        from ._worker import Judge

        with tempfile.TemporaryDirectory(prefix="cite-judge-") as tmp:
            j = Judge(tmp)
            return {p["key"]: j(p) for p in payloads}
    jobs = max(1, int(jobs))
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="cite-eval-") as tmp:
        procs = []
        for k in range(jobs):
            chunk = payloads[k::jobs]
            if not chunk:
                continue
            src, dst = os.path.join(tmp, f"in{k}.jsonl"), os.path.join(tmp, f"out{k}.jsonl")
            with open(src, "w", encoding="utf-8") as fh:
                for p in chunk:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            env = {k2: v for k2, v in os.environ.items() if k2 not in ("PYTHONPATH", "PYTHONHOME")}
            # -I: the worker must import that interpreter's camber, never this checkout's
            cmd = [python, "-I", os.path.join(HERE, "_worker.py"), src, dst]
            procs.append((subprocess.Popen(cmd, env=env, cwd=tmp), dst))
        for p, dst in procs:
            if p.wait() != 0:
                raise RuntimeError(f"worker failed: {p.args}")
            with open(dst, encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    out[r["key"]] = r
    return out


def metrics(cases: list, results: dict) -> dict:
    """E and S overall and per cell, form, operator, capability and region."""
    rows = []
    for c in cases:
        o = outcome(c, results.get(c["key"], {"error": "no result"}))
        rows.append((c, o))

    def agg(keyf):
        acc: dict = {}
        for c, o in rows:
            k = keyf(c)
            a = acc.setdefault(k, {"n_false": 0, "escapes": 0, "n_true": 0, "strips": 0})
            if c["truth"] == "false":
                a["n_false"] += 1
                a["escapes"] += o["escape"]
            else:
                a["n_true"] += 1
                a["strips"] += o["strip"]
        for a in acc.values():
            a["E"] = a["escapes"] / a["n_false"] if a["n_false"] else None
            a["S"] = a["strips"] / a["n_true"] if a["n_true"] else None
        return acc

    allc = agg(lambda c: "all")["all"]
    allc["E_upper95"] = wilson_upper(allc["escapes"], allc["n_false"])
    cells = agg(lambda c: (c["form"], c["operator"], c["capability"]))
    false_cells = {k: v for k, v in cells.items() if v["n_false"]}
    # the worst cells: E first, among cells with at least 5 false cases (a 1-of-1 cell says little)
    big = {k: v for k, v in false_cells.items() if v["n_false"] >= 5} or false_cells
    worst = sorted(big.items(), key=lambda kv: (-kv[1]["E"], -kv[1]["n_false"], kv[0]))
    return {
        "overall": allc,
        "by_form": agg(lambda c: c["form"]),
        "by_operator": agg(lambda c: c["operator"]),
        "by_capability": agg(lambda c: c["capability"]),
        "by_region": agg(lambda c: c["region"]),
        "by_form_operator": {
            f"{k[0]} x {k[1]}": v for k, v in agg(lambda c: (c["form"], c["operator"])).items()
        },
        "cells": {" x ".join(k): v for k, v in cells.items()},
        "worst_cells": [{"cell": " x ".join(k), **v} for k, v in worst[:25]],
        "n_cells_false": len(false_cells),
        "n_cells_E_over_2pct": sum(1 for v in false_cells.values() if v["E"] > 0.02),
        "errors": sum(1 for c in cases if results.get(c["key"], {}).get("error")),
    }


def build_cases(facts_path: str | None = FACTS, sample: int | None = None, seed: int = 0):
    """``(facts_by_cap, cases)``: the generated cases (a seeded sample when ``sample``)."""
    fb = facttable.load_or_collect(facts_path)
    cases = forms.generate(facttable.claims(fb))
    if sample:
        cases = forms.sample(cases, sample, seed)
    return fb, cases


def _checker_label(python: str | None) -> str:
    py = python or sys.executable
    try:
        code = "import camber,sys;sys.stdout.write(camber.__version__)"
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        if python is None:
            import camber

            ver = camber.__version__
        else:
            ver = subprocess.run(
                [py, "-I", "-c", code], capture_output=True, text=True, env=env, timeout=60
            ).stdout.strip()
    except Exception:  # noqa: BLE001
        ver = "?"
    if python is None:
        try:
            sha = subprocess.run(
                ["git", "-C", facttable.REPO, "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            sha = "?"
        return f"checkout@{sha} camber {ver}"
    return f"{os.path.basename(os.path.dirname(os.path.dirname(py)))} camber {ver}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--facts", default=FACTS, help="fact table JSON (collected when missing)")
    ap.add_argument("--sample", type=int, default=0, help="judge a seeded sample of N cases")
    ap.add_argument("--seed", type=int, default=0)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--python", help="judge with this interpreter's camber")
    g.add_argument("--venv", help="judge with this venv's camber (DIR/bin/python)")
    ap.add_argument("--jobs", type=int, default=4, help="worker processes with --python/--venv")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", help="write the metrics (and per-case results) JSON here")
    ap.add_argument("--history", help="append one CSV row here")
    ap.add_argument("--corpus-out", help="also judge the regression corpus; write it here")
    a = ap.parse_args(argv)
    python = a.python or (os.path.join(a.venv, "bin", "python") if a.venv else None)
    fb, cases = build_cases(a.facts, a.sample or None, a.seed)
    by_id = facttable.facts_index(fb)
    results = judge([payload(c, by_id) for c in cases], python, a.jobs)
    m = metrics(cases, results)
    checker = _checker_label(python)
    o = m["overall"]
    print(
        f"{checker}: {len(cases)} cases ({o['n_false']} false, {o['n_true']} true); "
        f"E = {o['E']:.4f} (95% upper {o['E_upper95']:.4f}), S = {o['S']:.4f}; "
        f"{m['n_cells_E_over_2pct']} of {m['n_cells_false']} cells have E > 2%"
    )
    for w in m["worst_cells"][:10]:
        print(f"  E {w['E']:.2f} ({w['escapes']}/{w['n_false']})  {w['cell']}")
    cr = None
    if a.corpus_out:
        cr = corpus_results(python, a.jobs)
        print(
            f"corpus: {cr['entries']} entries, {cr['met']} met; {cr['xfail_listed']} listed xfail, "
            f"{cr['regressions']} listed passing but not met, {cr['promotable']} xfail now met"
        )
        os.makedirs(os.path.dirname(os.path.abspath(a.corpus_out)), exist_ok=True)
        with open(a.corpus_out, "w", encoding="utf-8") as fh:
            json.dump({"checker": checker, **cr}, fh, ensure_ascii=False, indent=1)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        per_case = []
        for c in cases:
            r = results.get(c["key"], {})
            per_case.append({**{k: c[k] for k in c if k != "cores"}, **outcome(c, r), "result": r})
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "checker": checker,
                    "label": a.label,
                    "seed": a.seed,
                    "metrics": m,
                    "cases": per_case,
                },
                fh,
                ensure_ascii=False,
                indent=1,
            )
    if a.history:
        new = not os.path.exists(a.history)
        os.makedirs(os.path.dirname(os.path.abspath(a.history)), exist_ok=True)
        worst = m["worst_cells"][0] if m["worst_cells"] else {"cell": "", "E": 0}
        with open(a.history, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
            if new:
                w.writeheader()
            w.writerow(
                {
                    "when": _dt.datetime.now().isoformat(timespec="seconds"),
                    "label": a.label,
                    "checker": checker,
                    "seed": a.seed,
                    "n_cases": len(cases),
                    "n_false": o["n_false"],
                    "n_true": o["n_true"],
                    "E": f"{o['E']:.5f}",
                    "E_upper95": f"{o['E_upper95']:.5f}",
                    "S": f"{o['S']:.5f}",
                    "cells": m["n_cells_false"],
                    "cells_E_over_2pct": m["n_cells_E_over_2pct"],
                    "worst_cell": worst["cell"],
                    "worst_cell_E": f"{worst['E']:.4f}",
                    **(
                        {
                            "corpus_entries": cr["entries"],
                            "corpus_met": cr["met"],
                            "corpus_xfail_listed": cr["xfail_listed"],
                            "corpus_regressions": cr["regressions"],
                            "corpus_promotable": cr["promotable"],
                        }
                        if cr
                        else {}
                    ),
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
