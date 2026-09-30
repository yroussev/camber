"""Evaluate the point-role suggester on BTS (the ``bts`` catalog entry): names versus data.

BTS (Prabowo et al., NeurIPS 2024 Datasets & Benchmarks; CC BY 4.0) publishes three real
Australian buildings whose ~20,000 points carry a Brick class and anonymised ids. CAMBER maps a
Brick class to a role where a clean mapping exists (``camber.interop.brick``): 1,492 points, 903
of them with data (A 416, B 32, C 455). This script asks the suggester to recover those roles:

* **lexical** -- the current name-only suggester (``FeatureSuggester()``, no series);
* **time series (templates)** -- no name, the data only, scored against CAMBER's hand-written role
  templates (no training at all);
* **time series (fitted)** -- no name, a ``ProfileModel`` fitted on the *other two* buildings
  (leave one building out);
* **combined** -- the name and the data (``FeatureSuggester(use_timeseries=True)``), with the
  templates or with the fitted model.

Each is run with two kinds of name. The **anonymised** run uses the published stream id, as a
suggester would see an anonymised export: these are the honest BTS figures. BTS has no BMS point
names (its ids are UUIDs), so the **named** run uses the Brick class text as the name
(``Zone_Air_Temperature_Sensor``). That text is effectively the label: those rows are
*Brick-class labels used as names* -- an upper bound, not real-world naming (for real point names
see ``real_names.py``); they show only whether the data spoils a good name.
Results are top-1 / top-3 accuracy per role and overall, per held-out building and pooled, plus
the most frequent top-1 confusions.

    camber datasets fetch bts --subset full          # ~19 GB from the publisher, verified
    python examples/suggester_eval/bts.py        # profiles cached; results in --out

``--files DIR`` reads the published files from a directory instead of the catalog cache.
Profiles are cached (``--out``/profiles.json), so a re-run only re-scores.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

import pandas as pd  # noqa: E402

from camber import datasets  # noqa: E402
from camber.datasets import _paths  # noqa: E402
from camber.datasets._brickstreams import iter_site_series, site_points, stream_series  # noqa: E402
from camber.mapping_assist import FeatureSuggester  # noqa: E402
from camber.mapping_timeseries import ProfileModel, SeriesProfile, profile_series  # noqa: E402
from camber.model.roles import Role  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "..", "_data", "suggester_eval", "bts")
MIN_SAMPLES = 96 * 7  # a week of 15-minute bins with data


# --------------------------------------------------------------------------- profiles


def _paths_for(entry, files_dir):
    """``{file name: path}`` from a directory of published files or the verified catalog cache."""
    if files_dir:
        return {f["name"]: os.path.join(files_dir, f["name"]) for f in entry.files}
    from camber.datasets._ingest import verified_inputs

    root = _paths.data_dir(None)
    return {k: v[0] for k, v in verified_inputs(entry, "full", root).items()}


def site_profiles(entry, site_key: str, paths: dict, *, log=print) -> list:
    """``[record]`` for every labelled stream of one site: its role, class, ids and profile."""
    spec = entry.ingest
    site = spec["sites"][site_key]
    with open(paths[site["model"]], encoding="utf-8") as fh:
        ttl = fh.read()
    index = pd.read_csv(paths[site["index"]], dtype=str)
    points = site_points(ttl, index, spec)
    labelled = {sid: p for sid, p in points.items() if p.role is not None}
    tz = site.get("local_timezone")
    series = {}
    t0 = time.time()
    for sid, t, v in iter_site_series(paths[site["series"]], set(labelled)):
        if t is None:
            continue
        s, _ = stream_series(
            t,
            v,
            spec,
            tz,
            site.get("source_timezone", "UTC"),
            role=labelled[sid].role,
            site=site_key,
        )
        if s.notna().sum() >= MIN_SAMPLES:
            series[sid] = s
    log(f"site {site_key}: {len(series)} labelled streams with data ({time.time() - t0:.0f} s)")
    oats = sorted(
        (sid for sid in series if labelled[sid].role == Role.OAT),
        key=lambda sid: -series[sid].notna().sum(),
    )
    out = []
    for sid, s in series.items():
        # the site's outdoor air -- never the point itself (an outdoor sensor would match trivially)
        ref = next((o for o in oats if o != sid), None)
        prof = profile_series(s, oat=series[ref] if ref else None)
        p = labelled[sid]
        out.append(
            {
                "site": site_key,
                "stream_id": sid,
                "brick_class": p.brick_class,
                "role": p.role.value,
                "equip_class": p.equip_class,
                "profile": prof.as_dict(),
            }
        )
    return out


def load_profiles(entry, paths: dict, cache: str, sites, *, log=print) -> list:
    have = []
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as fh:
            have = json.load(fh)
    done = {r["site"] for r in have}
    for key in sites:
        if key not in done:
            have += site_profiles(entry, key, paths, log=log)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(have, fh)
    return [r for r in have if r["site"] in sites]


# --------------------------------------------------------------------------- scoring


def _profile(rec) -> SeriesProfile:
    return SeriesProfile(**rec["profile"])


def _name(rec, naming: str) -> str:
    return rec["brick_class"] if naming == "named" else rec["stream_id"]


def run_method(records, method: str, naming: str, models: dict) -> list:
    """``[(record, [role, ...top 3])]`` for one method and naming."""
    out = []
    for rec in records:
        name = _name(rec, naming)
        prof = _profile(rec)
        if method == "lexical":
            sugg = FeatureSuggester().suggest(name, k=3)
        elif method.startswith("timeseries"):
            fs = FeatureSuggester(use_timeseries=True, model=models.get((method, rec["site"])))
            sugg = fs.suggest("", profile=prof, k=3)
        else:
            base = method.replace("combined", "timeseries")
            fs = FeatureSuggester(use_timeseries=True, model=models.get((base, rec["site"])))
            sugg = fs.suggest(name, profile=prof, k=3)
        out.append((rec, [s.role for s in sugg]))
    return out


def summarise(results) -> dict:
    """Overall and per-role top-1 / top-3, per building, and the top-1 confusions."""
    by_role: dict = collections.defaultdict(lambda: [0, 0, 0])
    by_site: dict = collections.defaultdict(lambda: [0, 0, 0])
    conf: collections.Counter = collections.Counter()
    for rec, top in results:
        hit1 = bool(top) and top[0] == rec["role"]
        hit3 = rec["role"] in top
        for key, d in ((rec["role"], by_role), (rec["site"], by_site)):
            d[key][0] += 1
            d[key][1] += hit1
            d[key][2] += hit3
        if not hit1:
            conf[(rec["role"], top[0] if top else "(none)")] += 1
    n = len(results)

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    return {
        "n": n,
        "top1": pct(sum(v[1] for v in by_role.values()), n),
        "top3": pct(sum(v[2] for v in by_role.values()), n),
        # every role counts once (294 of the 903 points are zone temperatures)
        "macro_top1": round(sum(100.0 * v[1] / v[0] for v in by_role.values()) / len(by_role), 1)
        if by_role
        else None,
        "by_site": {
            k: {"n": v[0], "top1": pct(v[1], v[0]), "top3": pct(v[2], v[0])}
            for k, v in sorted(by_site.items())
        },  # fmt: skip
        "by_role": {
            k: {"n": v[0], "top1": pct(v[1], v[0]), "top3": pct(v[2], v[0])}
            for k, v in sorted(by_role.items(), key=lambda kv: -kv[1][0])
        },  # fmt: skip
        "confusions": [[t, p, c] for (t, p), c in conf.most_common(15)],
    }


def fit_lobo(records, sites) -> dict:
    """``{("timeseries_fitted", held-out site): ProfileModel}`` trained on the other sites."""
    models = {}
    for key in sites:
        train = [(_profile(r), r["role"]) for r in records if r["site"] != key]
        models[("timeseries_fitted", key)] = ProfileModel().fit(train)
    return models


METHODS = (
    ("lexical", "named"),
    ("lexical", "anonymised"),
    ("timeseries_templates", "none"),
    ("timeseries_fitted", "none"),
    ("combined_templates", "named"),
    ("combined_templates", "anonymised"),
    ("combined_fitted", "named"),
    ("combined_fitted", "anonymised"),
)


def evaluate(records, sites) -> dict:
    models = fit_lobo(records, sites)
    return {
        f"{m}/{naming}": summarise(run_method(records, m, naming, models)) for m, naming in METHODS
    }


#: how the printed tables label each kind of name
NAMING_LABEL = {
    "named": "Brick-class labels used as names (upper bound, not real-world naming)",
    "anonymised": "anonymised (published stream id)",
    "none": "none (data only)",
}


def table(results: dict) -> str:
    lines = [
        "BTS: 'named' rows use Brick-class labels as names -- an upper bound, "
        "not real-world naming",
        "",
        "| method | names | top-1 % | top-3 % | macro top-1 % | "
        + " | ".join(f"{s} top-1" for s in next(iter(results.values()))["by_site"])
        + " |",
    ]
    lines.append("|" + "---|" * (5 + len(next(iter(results.values()))["by_site"])))
    for key, r in results.items():
        method, naming = key.split("/")
        sites = " | ".join(str(v["top1"]) for v in r["by_site"].values())
        lines.append(
            f"| {method} | {NAMING_LABEL.get(naming, naming)} | {r['top1']} | {r['top3']} | "
            f"{r['macro_top1']} | {sites} |"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--files", help="directory holding the published BTS files")
    ap.add_argument("--out", default=DEFAULT_OUT, help="results directory (profiles cached here)")
    ap.add_argument("--sites", nargs="+", default=["A", "B", "C"])
    args = ap.parse_args(argv)
    entry = datasets.get("bts")
    paths = _paths_for(entry, args.files)
    records = load_profiles(entry, paths, os.path.join(args.out, "profiles.json"), args.sites)
    counts = collections.Counter(r["role"] for r in records)
    print(f"{len(records)} labelled streams; roles: {dict(counts.most_common())}")
    results = evaluate(records, args.sites)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print(table(results))
    for key in (
        "lexical/anonymised",
        "combined_templates/anonymised",
        "combined_fitted/anonymised",
    ):
        print(f"\n{key}: top-1 confusions (true -> suggested, count)")
        for t, p, c in results[key]["confusions"][:10]:
            print(f"  {t} -> {p}: {c}")
    print(f"\nresults: {os.path.join(args.out, 'results.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
