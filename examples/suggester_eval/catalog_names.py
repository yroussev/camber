"""Name-only check of the point-role suggester on catalog mappings held out of ``real_names.py``.

``real_names.py`` scores seven real buildings by their published point names. From 0.100 on its
lexical figures are in-sample: the 0.100 vocabulary additions (#102) were chosen from the misses
of the 0.96 run on those same names. This script is the out-of-sample reference for the name
side. It scores every published point name of the catalog mappings **not** used by
``real_names.py`` (``camber/datasets/mappings/*.json``) against the role CAMBER assigned to it
when the dataset was catalogued. It needs no download: the names and labels ship with CAMBER.

* **Name only.** No data is read, so only the default ``FeatureSuggester()`` is scored.
* **Templated names are skipped** (``zone_{z}_temp``): a placeholder is not a published name.
* Each mapping file is reported with its own count; one large file can dominate the pool.

    python examples/suggester_eval/catalog_names.py
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from camber.mapping_assist import FeatureSuggester  # noqa: E402

MAPPINGS = os.path.join(ROOT, "camber", "datasets", "mappings")
#: mapping-file prefixes of the datasets real_names.py evaluates (in-sample from 0.100)
IN_SAMPLE = ("b4b", "b59", "irish", "lbnl", "nuig", "ornl_frp_ops", "robod", "sdu")


def held_out_points(mappings_dir: str = MAPPINGS) -> list:
    """``[(file, name, role)]`` for every non-templated alias of the held-out mapping files."""
    out = []
    for path in sorted(glob.glob(os.path.join(mappings_dir, "*.json"))):
        base = os.path.basename(path)
        if base.startswith(IN_SAMPLE):
            continue
        with open(path, encoding="utf-8") as fh:
            aliases = json.load(fh).get("aliases", {})
        out += [(base, name, role) for name, role in aliases.items() if "{" not in name]
    return out


def evaluate(points) -> dict:
    """Top-1 / top-3 per mapping file and pooled, with each miss."""
    fs = FeatureSuggester()
    per: dict = {}
    misses = []
    for base, name, role in points:
        top = [s.role for s in fs.suggest(name, k=3)]
        rec = per.setdefault(base, {"n": 0, "top1": 0, "top3": 0})
        rec["n"] += 1
        rec["top1"] += bool(top) and top[0] == role
        rec["top3"] += role in top
        if not top or top[0] != role:
            misses.append([base, name, role, top[0] if top else None])
    n = sum(v["n"] for v in per.values())
    pooled = {
        "n": n,
        "top1": round(100 * sum(v["top1"] for v in per.values()) / n, 1) if n else None,
        "top3": round(100 * sum(v["top3"] for v in per.values()) / n, 1) if n else None,
    }
    return {"by_file": per, "pooled": pooled, "misses": misses}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", help="write the results here")
    args = ap.parse_args(argv)
    res = evaluate(held_out_points())
    print("| mapping file | points | name only top-1 / top-3 |")
    print("|---|---|---|")
    for base, v in res["by_file"].items():
        print(f"| {base} | {v['n']} | {v['top1']} / {v['top3']} |")
    p = res["pooled"]
    print(f"| **pooled** | **{p['n']}** | **{p['top1']} / {p['top3']} %** |")
    print("\nmisses (file, name, label, top-1):")
    for row in res["misses"]:
        print("  " + " | ".join(str(x) for x in row))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
