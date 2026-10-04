"""Out-of-sample check of the point-role suggester on the catalog mappings ``real_names.py`` skips.

``real_names.py`` scores seven real buildings by their published point names. From 0.100 on its
lexical figures are in-sample: the 0.100 vocabulary additions (#102) were chosen from the misses
of the 0.96 run on those same names. This script is the out-of-sample reference for the name
side. It scores every published point name of the catalog mappings **not** used by
``real_names.py`` (``camber/datasets/mappings/*.json``) against the role CAMBER assigned to it
when the dataset was catalogued. It needs no download: the names and labels ship with CAMBER.

* **Name only** by default. No data is read, so only the default ``FeatureSuggester()`` is
  scored.
* **Templated names are skipped** (``zone_{z}_temp``): a placeholder is not a published name.
* Each mapping file is reported with its own count; one large file can dominate the pool.

**Names plus data (``--data``, 0.101, #106).** Each held-out name is also scored with its series
from the catalog data. Three suggesters are compared: the name only (``FeatureSuggester()``),
the same default suggester with the series passed (it adds the physical-range gate), and the
name plus data (``FeatureSuggester(use_timeseries=True)``). Each is reported per mapping file and
pooled, with every point whose top-1 the data helped or hurt against the name alone.

* The series are read as ``real_names.py`` reads them: the catalog cache, else a verified copy
  under ``--search``; 15-minute means from the first fault-free run of a point; no unit passed.
* A series needs half a day of data (``--min-samples``, 48 bins). The held-out lab and test
  datasets publish runs of 18 to 24 hours; ``real_names.py`` asks two days of building trends.
* Only open-tier entries are read. A research-only entry keeps its names in the name-only table
  but has no data row.
* A name with no usable series (too short a run, or a column CAMBER derives at ingest) gets the
  same answer from all three suggesters, so the all-names pool counts it once for each.

    python examples/suggester_eval/catalog_names.py
    camber datasets fetch finnish-dcv nist-ibal ... --subset full
    python examples/suggester_eval/catalog_names.py --data      # --search DIR finds copies on disk
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
sys.path.insert(0, HERE)

from camber.mapping_assist import FeatureSuggester  # noqa: E402

MAPPINGS = os.path.join(ROOT, "camber", "datasets", "mappings")
DEFAULT_WORK = os.path.join(HERE, "..", "_data", "suggester_eval", "catalog_names", "work")
DEFAULT_SEARCH = [os.path.join(HERE, "..", "_data")]
#: half a day of 15-minute bins with data: the held-out lab and test datasets publish runs of 18
#: to 24 hours (``real_names.py`` asks for two days of building trends)
MIN_SAMPLES = 48
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


# --------------------------------------------------------------------------- names plus data


def mapping_datasets() -> dict:
    """``{mapping file: catalog entry}`` for every catalog entry that ingests through a mapping
    file (the entry-wide ``mapping`` or a run's own)."""
    from camber import datasets

    out: dict = {}
    for entry in datasets.catalog():
        ingest = entry.ingest or {}
        files = {ingest.get("mapping")} | {r.get("mapping") for r in ingest.get("runs") or []}
        for f in files - {None}:
            out.setdefault(f, entry)
    return out


def _run_mapping_file(entry, run_id: str):
    ingest = entry.ingest or {}
    run = next((r for r in ingest.get("runs") or [] if r.get("id") == run_id), {})
    return run.get("mapping") or ingest.get("mapping")


def held_out_series(points, search, work: str, *, min_samples: int = MIN_SAMPLES, log=print):
    """``{(file, name): (profile, series)}`` for the held-out points with a usable series.

    Reads every open-tier dataset behind a held-out mapping file with ``real_names.py``'s reader
    and joins its records to the held-out names by mapping file and published name; a name seen
    on several pieces of equipment takes the first record (the first fault-free run).
    ``min_samples`` is the fewest 15-minute bins with data a series needs. Nothing is cached:
    the series themselves are scored (the physical-range check reads them)."""
    import real_names

    from camber.mapping_timeseries import SeriesProfile

    real_names.MIN_SAMPLES = min_samples
    by_file = mapping_datasets()
    found: dict = {}
    for did in dict.fromkeys(by_file[f].id for f, _, _ in points if f in by_file):
        entry = next(e for e in by_file.values() if e.id == did)
        if entry.access != "open":
            log(f"{did}: no data row (access {entry.access}; only open-tier entries are read)")
            continue
        try:
            recs = real_names.dataset_points(entry, "full", search, work, log=log, keep_series=True)
        except FileNotFoundError as exc:
            log(f"{did}: no data row ({exc})")
            continue
        for r in recs:
            key = (_run_mapping_file(entry, r["run"]), r["name"])
            found.setdefault(key, (SeriesProfile(**r["profile"]), r["series"]))
    return {(f, n): found[(f, n)] for f, n, _ in points if (f, n) in found}


#: the three suggesters compared on the held-out points with data
DATA_METHODS = ("name only", "name only, series passed", "name + data")


def _data_tops(name: str, data) -> dict:
    """The top-3 roles of each :data:`DATA_METHODS` suggester for one point."""
    lex = [s.role for s in FeatureSuggester().suggest(name, k=3)]
    if data is None:  # no series: the three agree
        return dict.fromkeys(DATA_METHODS, lex)
    prof, series = data
    return {
        "name only": lex,
        # the default suggester when a caller passes the series: the name and the range check
        "name only, series passed": [
            s.role for s in FeatureSuggester().suggest(name, series=series, k=3)
        ],
        "name + data": [
            s.role
            for s in FeatureSuggester(use_timeseries=True).suggest(
                name, series=series, profile=prof, k=3
            )
        ],
    }


def evaluate_data(points, data: dict) -> dict:
    """The :data:`DATA_METHODS` on the held-out points: per mapping file (points with a series),
    pooled over the points with a series and over all points, and each top-1 the data changed
    against the name alone."""
    per: dict = {}
    pools: dict = {"all": [], "with data": []}
    helped, hurt = [], []
    for base, name, role in points:
        d = data.get((base, name))
        tops = _data_tops(name, d)
        hits = {m: (bool(t) and t[0] == role, role in t) for m, t in tops.items()}
        pools["all"].append(hits)
        rec = per.setdefault(base, {"n": 0, "hits": []})
        rec["n"] += 1
        if d is not None:
            pools["with data"].append(hits)
            rec["hits"].append(hits)
        a, b = tops["name only"], tops["name + data"]
        row = [base, name, role, a[0] if a else None, b[0] if b else None]
        if hits["name + data"][0] and not hits["name only"][0]:
            helped.append(row)
        elif hits["name only"][0] and not hits["name + data"][0]:
            hurt.append(row)

    def score(hits: list) -> dict:
        n = len(hits)
        out: dict = {"n": n}
        for m in DATA_METHODS:
            out[m] = [round(100.0 * sum(h[m][i] for h in hits) / n, 1) if n else None
                      for i in (0, 1)]  # fmt: skip
        return out

    return {
        "by_file": {k: dict(score(v["hits"]), points=v["n"]) for k, v in per.items()},
        "pooled": {k: score(v) for k, v in pools.items()},
        "helped": helped,
        "hurt": hurt,
    }


def data_table(res: dict) -> str:
    """One markdown table: per mapping file then pooled, top-1 / top-3 per method."""
    head = " | ".join(f"{m} top-1 / top-3 %" for m in DATA_METHODS)
    lines = [f"| mapping file | points (with data) | {head} |", "|---|---|---|---|---|"]
    for base, v in res["by_file"].items():
        cells = [f"{v[m][0]} / {v[m][1]}" if v["n"] else "-" for m in DATA_METHODS]
        lines.append(f"| {base} | {v['points']} ({v['n']}) | " + " | ".join(cells) + " |")
    for key, v in res["pooled"].items():
        cells = [f"**{v[m][0]} / {v[m][1]}**" for m in DATA_METHODS]
        lines.append(f"| **pooled, {key}** | **{v['n']}** | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--data", action="store_true",
                    help="also score name plus data (series from the catalog data)")  # fmt: skip
    ap.add_argument("--work", default=DEFAULT_WORK,
                    help="where archive members are extracted (with --data)")  # fmt: skip
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES,
                    help="fewest 15-minute bins a series needs (with --data)")  # fmt: skip
    ap.add_argument("--search", nargs="*", default=DEFAULT_SEARCH,
                    help="directories to look for already-downloaded catalog files")  # fmt: skip
    args = ap.parse_args(argv)
    points = held_out_points()
    res = evaluate(points)
    print("| mapping file | points | name only top-1 / top-3 |")
    print("|---|---|---|")
    for base, v in res["by_file"].items():
        print(f"| {base} | {v['n']} | {v['top1']} / {v['top3']} |")
    p = res["pooled"]
    print(f"| **pooled** | **{p['n']}** | **{p['top1']} / {p['top3']} %** |")
    print("\nmisses (file, name, label, top-1):")
    for row in res["misses"]:
        print("  " + " | ".join(str(x) for x in row))
    if args.data:
        data = held_out_series(points, args.search, args.work, min_samples=args.min_samples)
        res["data"] = evaluate_data(points, data)
        print("\nnames plus data (series from the catalog data, no unit passed):\n")
        print(data_table(res["data"]))
        d = res["data"]
        print(f"\nagainst the name only, the data helped {len(d['helped'])}, "
              f"hurt {len(d['hurt'])} (top-1)")  # fmt: skip
        for tag in ("helped", "hurt"):
            for row in d[tag]:
                print(f"  {tag}: {row[0]} {row[1]!r} {row[2]}: {row[3]} -> {row[4]}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
