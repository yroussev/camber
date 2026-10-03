"""Evaluate the point-role suggester on the real BMS point names of open catalog datasets.

The BTS evaluation (``bts.py``) cannot test real naming: BTS publishes anonymised ids, so its
"named" rows use the Brick class text as the name -- effectively the label. This script asks the
suggester to recover the role of every point of open catalog datasets that publish their own BMS
point names (``AHU101_Ctrls_TE_101_2_Supply_Duct_Temp``, ``DaTemp``, ``rtu_001_sat_sp_tn``...):

* **lexical** -- the default name-only suggester (``FeatureSuggester()``, no series);
* **time series** -- no name, the data only (``FeatureSuggester(use_timeseries=True)`` with the
  hand-written role templates);
* **combined** -- the name and the data (``FeatureSuggester(use_timeseries=True)``).

**Ground truth** is each dataset's catalog mapping: the role CAMBER assigned to each published
point name when the dataset was catalogued (``camber/datasets/mappings/*.json``), hand-curated by
reading the publisher's documentation and checking the data. For ``lbnl-b59`` the rooftop-unit
points take their role from the publisher's own Brick model, with the mapping file's overrides.
Only mapped points are scored (a point CAMBER left unmapped has no label); columns CAMBER derives
at ingest (a fan-on flag computed from a power reading) are not published names and are skipped.
The same physical point in several runs (operating scenarios, fault runs of a simulation) is
scored once, from its first fault-free run.

Real buildings and the LBNL simulated FDD sets (systematic, simulation-style names such as
``SA_TEMP``) are reported separately. Every dataset is open-tier; cite each as its catalog entry
asks (``camber datasets info <id>``).

    camber datasets fetch irish-ahu nuig-ahu101 ...     # or point --search at copies on disk
    python examples/suggester_eval/real_names.py

Inputs are taken from the catalog cache, else found under ``--search`` directories by name or
size and verified against the catalog's SHA-256 pins. Profiles are cached in ``--out``, so a
re-run only re-scores.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import bts as bts_eval  # noqa: E402
import pandas as pd  # noqa: E402

from camber import datasets  # noqa: E402
from camber.datasets import _paths  # noqa: E402
from camber.datasets._fetch import sha256_file  # noqa: E402
from camber.datasets._ingest import (  # noqa: E402
    _brick_grouping,
    _extract_members,
    _plan_reads,
    _run_sources,
    read_raw_run,
    run_mapping,
    run_spec,
)
from camber.mapping_assist import FeatureSuggester  # noqa: E402
from camber.mapping_timeseries import SeriesProfile, profile_series  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "..", "_data", "suggester_eval", "real_names")
DEFAULT_SEARCH = [os.path.join(HERE, "..", "_data")]
MIN_SAMPLES = 96 * 2  # two days of 15-minute bins with data

#: real buildings (measured BMS trends) and the LBNL simulated FDD sets, reported apart
REAL = (
    "lbnl-b59",
    "irish-ahu",
    "nuig-ahu101",
    "robod",
    "b4b-windesheim",
    "sdu-ou44",
    "ornl-frp-ops",
)
SIMULATED = ("lbnl-sdahu", "lbnl-fcu", "lbnl-ddahu", "lbnl-fpu", "lbnl-chiller", "lbnl-boiler")

#: where a label or the suggester's vocabulary could leak into a dataset's figures
LEAKAGE = {
    "irish-ahu": "the 0.9x name tokenizer was written against this AHU's point list (its "
    "16 names are a unit test): the lexical figures are in-sample",
    "lbnl-b59": "the tokenizer's noise words (fbk, tn) and a range-check test come from these "
    "names: the lexical figures are partly in-sample; the RTU labels are the publisher's Brick "
    "classes with CAMBER overrides",
}


# --------------------------------------------------------------------------- inputs


def _sha(path: str) -> str:
    return sha256_file(path)


def find_inputs(entry, names, search, *, log=print) -> dict:
    """``{file name: (path, sha256)}``: the catalog cache first, else a verified copy on disk.

    A copy under a ``search`` directory is accepted by its name or its size, and only when its
    SHA-256 matches the catalog pin (an unpinned file is accepted by name alone)."""
    root = _paths.data_dir(None)
    ddir = _paths.downloads_dir(root, entry.id)
    by_name = {f["name"]: f for f in entry.files}
    out, missing = {}, []
    for name in names:
        rec = by_name[name]
        cands = [os.path.join(ddir, name)]
        base, size = os.path.basename(name), rec.get("size")
        for top in search:
            for dirpath, dirs, files in os.walk(top):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    p = os.path.join(dirpath, f)
                    if f == base or (size and f.endswith(os.path.splitext(base)[1]) and
                                     os.path.getsize(p) == size):  # fmt: skip
                        cands.append(p)
        hit = None
        for p in dict.fromkeys(cands):
            if not os.path.isfile(p) or (size and os.path.getsize(p) != size):
                continue
            sha = _sha(p) if rec.get("sha256") else ""
            if not rec.get("sha256") or sha == rec["sha256"]:
                hit = (p, sha)
                break
        if hit:
            out[name] = hit
        else:
            missing.append(name)
    if missing:
        raise FileNotFoundError(
            f"{entry.id}: {', '.join(missing)} not found; `camber datasets fetch {entry.id}`"
        )
    return out


# --------------------------------------------------------------------------- points


def _point_key(run, name) -> tuple:
    return (run.get("equip"), name)


def select_runs(entry, subset: str) -> list:
    """The subset's runs, fault-free (unlabelled) runs first, so a point shared by several runs
    (scenarios, fault variants) is read from its fault-free run."""
    runs = entry.runs(subset)
    return sorted(runs, key=lambda r: bool(r.get("label")))


def _names_of(run, mapping: MappingProvider) -> list:
    """The published names a (non-grouped) run can map."""
    aliases = getattr(mapping, "aliases", None) or {}
    return list(aliases)


def _published_name(run, col: str) -> str:
    """The point's published name: the column, or for one-point files the file's own stem."""
    many = run.get("members")
    if isinstance(many, dict) and col in many:
        return os.path.splitext(os.path.basename(many[col]))[0]
    return col


def dataset_points(entry, subset: str, search, work: str, *, log=print) -> list:
    """``[record]`` for every mapped, published point of one dataset: name, role, series profile."""
    spec = {k: v for k, v in entry.ingest.items() if k != "recode"}  # raw values, not recoded
    derived = {dv.get("column") for dv in spec.get("derive") or []}
    runs = select_runs(entry, subset)
    seen: set = set()
    chosen = []
    for run in runs:
        if run.get("group"):
            chosen.append(run)
            continue
        mapping, _ = run_mapping(spec, run)
        keys = {_point_key(run, n) for n in _names_of(run, mapping)} - seen
        if keys:
            chosen.append(run)
            seen |= keys
    names = list(dict.fromkeys(r["file"] for r in chosen))
    if spec.get("brick"):
        names.append(spec["brick"]["file"])
    inputs = find_inputs(entry, list(dict.fromkeys(names)), search, log=log)
    paths = _extract_members(entry, chosen, inputs, work)
    sources = {r["id"]: _run_sources(r, paths, inputs) for r in chosen}
    reader = _plan_reads(chosen, spec, sources)
    grouping = _brick_grouping(entry, inputs, work) if spec.get("brick") else None
    mapping_spec = json.loads(run_mapping(spec, {})[1])
    series: dict = {}
    t0 = time.time()
    for run in chosen:
        rspec = run_spec(spec, run)
        prov = {}
        if run.get("group"):
            from camber.datasets._brickgroup import BrickGrouping, mapping_overrides
            from camber.datasets._readers import clock_columns, read_table, text_layout

            g = grouping if run["group"] == "brick" and grouping is not None else BrickGrouping()
            mp, over = mapping_overrides(mapping_spec)
            header = read_table(sources[run["id"]], nrows=0, **text_layout(rspec)).columns
            clock = clock_columns(rspec)
            plan = g.plan(
                [c for c in header if c not in clock],
                default_equip=run["equip"],
                default_class=run["class"],
                mapping=mp,
                overrides=over,
            )
            mapping = MappingProvider.from_dict(
                {"aliases": {c: p.role.value for c, p in plan.items()}}
            )
            prov = {c: "publisher Brick" if p.source == "brick" else "CAMBER mapping"
                    for c, p in plan.items()}  # fmt: skip
            raw, _ = read_raw_run(sources[run["id"]], mapping, rspec, run["id"])
        else:
            mapping, _ = run_mapping(spec, run)
            raw, _ = read_raw_run(
                sources[run["id"]], mapping, rspec, run["id"], where=run.get("where"), reader=reader
            )
        for col in raw.columns:
            role = mapping.role_of(col)
            if role is None or col in derived:
                continue
            name = _published_name(run, col)
            key = _point_key(run, name)
            if key in series:
                continue
            s = pd.to_numeric(raw[col], errors="coerce").resample("15min").mean()
            if s.notna().sum() < MIN_SAMPLES:
                continue
            series[key] = (name, role.value, s, run["id"], prov.get(col, "CAMBER mapping"))
    # the same name with identical data on two pieces of equipment (a shared air handler) is one
    uniq: dict = {}
    for key, (name, role, s, rid, prov) in series.items():
        digest = hashlib.sha256(pd.util.hash_pandas_object(s.dropna()).values.tobytes()).hexdigest()
        uniq.setdefault((name, digest), (key, name, role, s, rid, prov))
    log(f"{entry.id}: {len(uniq)} mapped points with data ({time.time() - t0:.0f} s)")
    oats = sorted(
        (v for v in uniq.values() if v[2] == "oat"), key=lambda v: -int(v[3].notna().sum())
    )
    out = []
    for key, name, role, s, rid, prov in uniq.values():
        ref = next((o[3] for o in oats if o[0] != key), None)
        out.append(
            {
                "site": entry.id,
                "name": name,
                "equip": key[0],
                "run": rid,
                "role": role,
                "label_source": prov,
                "profile": profile_series(s, oat=ref).as_dict(),
            }
        )
    return out


def load_profiles(ids, cache: str, search, work: str, *, log=print) -> list:
    have = []
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as fh:
            have = json.load(fh)
    done = {r["site"] for r in have}
    for did in ids:
        if did in done:
            continue
        entry = datasets.get(did)
        if entry.access != "open":
            log(f"{did}: skipped (access {entry.access}; only open-tier entries are evaluated)")
            continue
        subset = "full"
        try:
            recs = dataset_points(entry, subset, search, work, log=log)
        except FileNotFoundError as exc:
            log(f"{did}: skipped ({exc})")
            continue
        have += recs
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(have, fh)
    return [r for r in have if r["site"] in ids]


# --------------------------------------------------------------------------- scoring

METHODS = ("lexical", "timeseries", "combined")


def rank(rec, method: str) -> list:
    """The top-3 roles one method suggests for one point."""
    prof = SeriesProfile(**rec["profile"])
    if method == "lexical":
        sugg = FeatureSuggester().suggest(rec["name"], k=3)
    elif method == "timeseries":
        sugg = FeatureSuggester(use_timeseries=True).suggest("", profile=prof, k=3)
    else:
        sugg = FeatureSuggester(use_timeseries=True).suggest(rec["name"], profile=prof, k=3)
    return [s.role for s in sugg]


def changes(records, tops: dict) -> dict:
    """Points whose top-1 correctness differs between the name alone and name + data."""
    helped, hurt = [], []
    for i, rec in enumerate(records):
        lex, comb = tops["lexical"][i], tops["combined"][i]
        a = bool(lex) and lex[0] == rec["role"]
        b = bool(comb) and comb[0] == rec["role"]
        row = [rec["site"], rec["name"], rec["role"], lex[0] if lex else None,
               comb[0] if comb else None]  # fmt: skip
        if b and not a:
            helped.append(row)
        elif a and not b:
            hurt.append(row)
    return {"helped": helped, "hurt": hurt}


def evaluate(records) -> dict:
    """Per-method summaries (per dataset in ``by_site``), pooled over real and simulated sets."""
    tops = {m: [rank(r, m) for r in records] for m in METHODS}
    groups = {
        "real": [i for i, r in enumerate(records) if r["site"] not in SIMULATED],
        # the tokenizer was written against some of these names: the out-of-sample reference
        "real, excluding in-sample names": [
            i for i, r in enumerate(records) if r["site"] not in SIMULATED + tuple(LEAKAGE)
        ],
        "simulated": [i for i, r in enumerate(records) if r["site"] in SIMULATED],
    }
    out: dict = {}
    for g, idx in groups.items():
        if not idx:
            continue
        recs = [records[i] for i in idx]
        sub = {m: [tops[m][i] for i in idx] for m in METHODS}
        methods = {m: bts_eval.summarise(list(zip(recs, sub[m]))) for m in METHODS}
        names: dict = collections.defaultdict(set)
        for r in recs:
            names[r["site"]].add(r["name"])
        for m in METHODS:
            per = methods[m]["by_site"].values()
            # every dataset counts once (lbnl-b59 alone is two thirds of the real points)
            methods[m]["dataset_macro_top1"] = round(sum(v["top1"] for v in per) / len(per), 1)
        out[g] = {
            "methods": methods,
            "distinct_names": {k: len(v) for k, v in sorted(names.items())},
            "changes": changes(recs, sub),
        }
    return out


def table(results: dict) -> str:
    """One markdown table: per dataset then pooled, top-1 / top-3 per method."""
    lines = [
        "| dataset | points (distinct names) | lexical top-1 / top-3 % | "
        "time series top-1 / top-3 % | combined top-1 / top-3 % |",
        "|---|---|---|---|---|",
    ]
    for g, res in results.items():
        m = res["methods"]
        if "excluding" not in g:
            for ds, v in m["lexical"]["by_site"].items():
                cells = [f"{m[k]['by_site'][ds]['top1']} / {m[k]['by_site'][ds]['top3']}"
                         for k in METHODS]  # fmt: skip
                n = f"{v['n']} ({res['distinct_names'][ds]})"
                lines.append(f"| {ds} | {n} | " + " | ".join(cells) + " |")
        cells = [f"**{m[k]['top1']} / {m[k]['top3']}**" for k in METHODS]
        lines.append(f"| **pooled, {g}** | **{m['lexical']['n']}** | " + " | ".join(cells) + " |")
        cells = [f"{m[k]['dataset_macro_top1']} / -" for k in METHODS]
        lines.append(f"| mean over datasets, {g} | {len(m['lexical']['by_site'])} datasets | "
                     + " | ".join(cells) + " |")  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help="results directory (profiles cached here)")
    ap.add_argument("--search", nargs="*", default=DEFAULT_SEARCH,
                    help="directories to look for already-downloaded catalog files")  # fmt: skip
    ap.add_argument("--datasets", nargs="+", default=list(REAL + SIMULATED))
    args = ap.parse_args(argv)
    work = os.path.join(args.out, "work")
    records = load_profiles(
        args.datasets, os.path.join(args.out, "profiles.json"), args.search, work
    )
    results = evaluate(records)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print(table(results))
    for g, res in results.items():
        for m in METHODS:
            print(f"\n{g} {m}: top-1 confusions (true -> suggested, count)")
            for t, p, c in res["methods"][m]["confusions"][:8]:
                print(f"  {t} -> {p}: {c}")
        ch = res["changes"]
        print(f"\n{g}: the data helped {len(ch['helped'])}, hurt {len(ch['hurt'])} (top-1)")
        for tag in ("helped", "hurt"):
            for row in ch[tag]:
                print(f"  {tag}: {row[0]} {row[1]!r} {row[2]}: {row[3]} -> {row[4]}")
    for did, why in LEAKAGE.items():
        print(f"\nleakage, {did}: {why}")
    print(f"\nresults: {os.path.join(args.out, 'results.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
