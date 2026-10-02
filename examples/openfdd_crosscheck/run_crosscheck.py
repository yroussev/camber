"""Run the G36 cross-check: CAMBER g36_afdd and open-fdd (pandas, SQL) on the same labelled frames.

Manual / networked step (the offline tests in ``tests/test_openfdd_crosscheck.py`` cover the
mapping, normalisers and scoring with synthetic data). Prerequisites:

* a CAMBER store holding the labelled LBNL AHU runs::

    camber datasets fetch lbnl-sdahu --subset full
    camber datasets ingest lbnl-sdahu --store xc_store --subset full
    camber datasets fetch lbnl-ddahu && camber datasets ingest lbnl-ddahu --store xc_store

* for the pandas engine, a separate venv with the pinned open-fdd (it needs Python 3.11+)::

    python3.12 -m venv ofvenv && ofvenv/bin/pip install "open-fdd[oracle]==4.4.9" pyarrow

* for the SQL engine, a Docker image of ``fdd_cli`` built from the pinned commit::

    docker build -f examples/openfdd_crosscheck/Dockerfile.fdd_cli \\
        -t camber-crosscheck/fdd_cli:32a6d44 examples/openfdd_crosscheck

Then::

    python examples/openfdd_crosscheck/run_crosscheck.py \\
        --store lbnl-sdahu=xc_store --store lbnl-ddahu=xc_store \\
        --openfdd-python ofvenv/bin/python --sql-image camber-crosscheck/fdd_cli:32a6d44

Engines whose prerequisite is missing are reported under "not run", never silently skipped.
``--probe`` runs the synthetic probes (``harness.PROBES``) instead of the datasets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness as hx  # noqa: E402
import pandas as pd  # noqa: E402

from camber import __version__ as CAMBER_VERSION  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.g36_rule import G36AFDD  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

DRIVER = os.path.join(hx.HERE, "openfdd_pandas_driver.py")
_ROLES = {r.value: r for r in Role}


def _camber_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [_ROLES.get(hx._slug(c), c) for c in out.columns]
    return out


def _fan_on_hours(frame: pd.DataFrame, step_h: float) -> float:
    cols = {hx._slug(c): c for c in frame.columns}
    if "supply_fan_status" in cols:
        on = pd.to_numeric(frame[cols["supply_fan_status"]], errors="coerce") > 0.5
    elif "supply_fan_speed" in cols:
        on = pd.to_numeric(frame[cols["supply_fan_speed"]], errors="coerce") > 1.0
    else:
        on = pd.Series(True, index=frame.index)
    return float(on.sum()) * step_h


def _step_seconds(frames: dict) -> float:
    f = next(iter(frames.values()))
    d = pd.Series(f.index).diff().dropna()
    return float(d.median() / pd.Timedelta(seconds=1)) if len(d) else 900.0


def _git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", _ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
        dirty = subprocess.run(
            ["git", "-C", _ROOT, "status", "--porcelain", "--", "camber"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return (r.stdout.strip() or "unknown") + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


def load_dataset(dataset: str, store_path: str, window: str):
    """``(frames, labels, meta)`` for one catalogued dataset from a CAMBER store."""
    st = ParquetStore(store_path)
    fac = f"ds-{dataset}"
    meta = st.facilities_meta().get(fac)
    if not meta:
        raise SystemExit(f"{dataset}: facility {fac} not in {store_path} (ingest it first)")
    ds = meta.get("dataset", {})
    labels = dict(ds.get("labels") or {})
    frames, out_labels = {}, {}
    for equip, truth in sorted(labels.items()):
        f = st.read_role_frame(facility_id=fac, equip=equip)
        f.columns = [hx._slug(c) for c in f.columns]
        if window == "month":
            for per, part in f.groupby(f.index.to_period("M")):
                if len(part) < 2:
                    continue
                key = f"{equip}@{per}"
                frames[key], out_labels[key] = part, truth
        else:
            frames[equip], out_labels[equip] = f, truth
    info = {
        "id": dataset,
        "licence": ds.get("licence"),
        "citation": ds.get("citation"),
        "content_hash": ds.get("content_hash"),
        "subset": ds.get("subset"),
        "n_labelled": len(labels),
        "n_fault_free": sum(1 for v in labels.values() if not v),
        "fault_types": dict(
            sorted(
                {v: sum(1 for x in labels.values() if x == v) for v in labels.values() if v}.items()
            )
        ),
    }
    return frames, out_labels, info


# --------------------------------------------------------------------------- engines
def template_params(dataset: str) -> dict:
    """``g36_afdd`` params from the dataset's catalog run template (``{}`` when it sets none).

    The same parameters ``camber datasets`` runs the rule with (e.g. the unit's documented
    minimum OA, which enables FC6); tolerances and delays stay at the G36 defaults.
    """
    import camber.datasets as cds

    path = os.path.join(os.path.dirname(cds.__file__), "configs", f"{dataset}.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for r in cfg.get("rules", []):
        if r.get("name") == "g36_afdd":
            return dict(r.get("params") or {})
    return {}


def run_camber(frames: dict, params_by_equip: dict | None = None) -> list:
    """CAMBER g36_afdd: G36 Table 5.16.14.7 tolerances and delays, plus template params."""
    params_by_equip = params_by_equip or {}
    out = []
    for equip, f in frames.items():
        rule = G36AFDD(**params_by_equip.get(equip, {}))
        out += hx.normalise_camber(rule.analyze(equip, _camber_frame(f)).as_dict(), equip)
    return out


def run_pandas(frames: dict, role_map: dict, profiles: dict, python: str, work: str):
    """open-fdd pandas engine, once per profile, in its own interpreter."""
    fdir = os.path.join(work, "pandas_frames")
    os.makedirs(fdir, exist_ok=True)
    applied = {}
    for equip, f in frames.items():
        df, applied[equip] = hx.openfdd_frame(f, role_map, "pandas")
        df.to_parquet(os.path.join(fdir, f"{hx._safe(equip)}.parquet"))
    names = {hx._safe(e): e for e in frames}
    poll = _step_seconds(frames)
    rules = [v["pandas"] for v in role_map["fault_conditions"].values()]
    verdicts, version = [], None
    for name, prof in profiles["profiles"].items():
        spec = {
            "rules": rules,
            "params": prof["pandas"],
            "poll_seconds": poll,
            "equipment_type": "AHU",
        }
        spec_path = os.path.join(work, f"pandas-spec-{name}.json")
        out_path = os.path.join(work, f"pandas-out-{name}.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        subprocess.run([python, DRIVER, fdir, spec_path, out_path], check=True)
        with open(out_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        version = doc["engine"]
        recs = [dict(r, equip=names.get(r["equip"], r["equip"])) for r in doc["records"]]
        verdicts += hx.normalise_pandas(recs, role_map, name, doc["poll_seconds"])
    return verdicts, version, applied


def docker_ready(timeout: float = 20.0) -> tuple[bool, str]:
    """``(ok, why)``: does ``docker info`` answer within ``timeout`` seconds (killed if not)."""
    if shutil.which("docker") is None:
        return False, "docker is not installed"
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"the Docker daemon did not answer `docker info` within {timeout:g} s"
    if r.returncode != 0:
        return False, "the Docker daemon is not running (`docker info` failed)"
    return True, ""


def run_sql(frames: dict, role_map: dict, profiles: dict, image: str, work: str):
    """open-fdd SQL engine (fdd_cli in Docker), once per profile, one building per equipment."""
    tree = os.path.join(work, "sql_tree")
    step = _step_seconds(frames)
    applied = hx.write_sql_tree(frames, role_map, tree, grid_minutes=round(step / 60))
    fan_h = {e: _fan_on_hours(f, step / 3600.0) for e, f in frames.items()}
    buildings = {hx._safe(e): e for e in frames}
    os.makedirs(os.path.join(work, "parquet"), exist_ok=True)
    verdicts = []
    for k, (name, prof) in enumerate(profiles["profiles"].items()):
        tuning = None
        ov = hx.sql_rule_overrides(prof, role_map)
        if ov:
            tuning = os.path.join(work, f"tuning-{name}.yaml")
            with open(tuning, "w", encoding="utf-8") as fh:
                fh.write(hx.sql_tuning_yaml(ov))
        res_dir = os.path.join(work, f"results-{name}")
        os.makedirs(res_dir, exist_ok=True)
        ingest, run = hx.docker_commands(
            image, tree, work, name, buildings=sorted(buildings), tuning_file=tuning
        )
        for argv in ([ingest] if k == 0 else []) + [run]:  # ingest once
            subprocess.run(argv, check=True)
        for b, equip in buildings.items():
            bodies = {}
            bdir = os.path.join(res_dir, b)
            for fn in os.listdir(bdir):
                if fn.endswith(".json"):
                    with open(os.path.join(bdir, fn), encoding="utf-8") as fh:
                        bodies[fn[: -len(".json")]] = json.load(fh)
            verdicts += hx.normalise_sql(bodies, role_map, name, [equip], fan_h)
    return verdicts, {"name": "open-fdd sql (fdd_cli)", "image": image, **hx.OPENFDD_PIN}, applied


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--store", action="append", default=[], metavar="DATASET=PATH")
    ap.add_argument("--openfdd-python", help="python of a venv with open-fdd installed")
    ap.add_argument("--sql-image", help="Docker image built from Dockerfile.fdd_cli")
    ap.add_argument("--sql-skip", metavar="REASON", help="record why the SQL engine was not run")
    ap.add_argument("--window", choices=("run", "month"), default="run")
    ap.add_argument("--out", default=os.path.join(hx.HERE, "results"))
    ap.add_argument("--work", help="scratch directory (default: a temporary one, removed after)")
    ap.add_argument("--probe", action="store_true", help="run the synthetic probes instead")
    ap.add_argument(
        "--omit-verdicts", action="store_true", help="leave the per-verdict list out of the JSON"
    )
    args = ap.parse_args(argv)

    role_map, profiles = hx.load_json("role_map.json"), hx.load_json("profiles.json")
    groups: dict = {}
    camber_params: dict = {}
    if args.probe:
        frames = {k: hx.probe_frame(p["frame"]) for k, p in hx.PROBES.items()}
        labels = {k: "probe" for k in frames}
        datasets = [
            {
                "id": "synthetic probes",
                "licence": "n/a",
                "n_labelled": len(frames),
                "n_fault_free": 0,
            }
        ]
    else:
        if not args.store:
            ap.error("pass --store DATASET=PATH (or --probe)")
        frames, labels, datasets = {}, {}, []
        for spec in args.store:
            ds, path = spec.split("=", 1)
            f, lab, info = load_dataset(ds, path, args.window)
            info["camber_template_params"] = tparams = template_params(ds)
            frames.update(f)
            labels.update(lab)
            camber_params.update({e: tparams for e in f})
            groups[ds] = lab
            datasets.append(info)

    work = args.work or tempfile.mkdtemp(prefix="camber-xc-")
    os.makedirs(work, exist_ok=True)
    versions = {
        "camber": f"{CAMBER_VERSION} at commit {_git_commit()} (g36_afdd at the G36 defaults, "
        "plus each dataset's run-template params)",
        "role_map": role_map["version"],
        "profiles": profiles["version"],
        "open-fdd pin": f"PyPI {hx.OPENFDD_PIN['pypi_version']}, commit {hx.OPENFDD_PIN['commit']}",
    }
    not_run: dict = {}
    applied: dict = {}
    verdicts = run_camber(frames, camber_params)
    try:
        if args.openfdd_python:
            v, ver, applied["pandas"] = run_pandas(
                frames, role_map, profiles, args.openfdd_python, work
            )
            verdicts += v
            versions["open-fdd pandas"] = f"{ver['name']} {ver['version']}"
        else:
            not_run[hx.ENGINE_PANDAS] = "no --openfdd-python given"
        if args.sql_skip:
            not_run[hx.ENGINE_SQL] = args.sql_skip
        elif args.sql_image:
            ok, why = docker_ready()
            if ok:
                v, ver, applied["sql"] = run_sql(frames, role_map, profiles, args.sql_image, work)
                verdicts += v
                versions["open-fdd sql"] = f"fdd_cli from commit {ver['commit']} ({ver['image']})"
            else:
                not_run[hx.ENGINE_SQL] = why
        else:
            not_run[hx.ENGINE_SQL] = "no --sql-image given"
    finally:
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)

    results = {
        "harness": "examples/openfdd_crosscheck",
        "window": args.window,
        "verdict_rule": (
            f"an FC fires on a run when its fault hours reach {hx.FIRE_PCT:g}% of that engine's "
            f"own evaluated hours, over at least {hx.MIN_EVAL_HOURS:g} evaluated hours (else not "
            "evaluated); the engines' own alarms are scored separately under `native`"
        ),
        "versions": versions,
        "datasets": datasets,
        "applied_mappings": {k: {e: a for e, a in v.items() if a} for k, v in applied.items()},
        "not_run": not_run,
        "scores": {
            name: {
                "pooled": hx.score(verdicts, labels, rule=rule),
                **(
                    {g: hx.score(verdicts, lab, rule=rule) for g, lab in groups.items()}
                    if len(groups) > 1
                    else {}
                ),
            }
            for name, rule in (("common", hx.common_verdict), ("native", hx.native_verdict))
        },
        "verdicts": "omitted (--omit-verdicts)"
        if args.omit_verdicts
        else [v.as_dict() for v in verdicts],
        "caveats": hx.standard_caveats(args.window, list(groups)),
    }
    if args.omit_verdicts:
        _collapse_not_evaluated(results["scores"])
    if args.probe:
        results["probes"] = probe_report(verdicts)
    os.makedirs(args.out, exist_ok=True)
    stem = "probes" if args.probe else f"results-{args.window}"
    with open(os.path.join(args.out, f"{stem}.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, sort_keys=False, default=str)
    with open(os.path.join(args.out, f"{stem}.md"), "w", encoding="utf-8") as fh:
        fh.write(hx.markdown(results) if not args.probe else probe_markdown(results))
    print(f"wrote {stem}.json / {stem}.md to {args.out}")
    return 0


def _collapse_not_evaluated(scores: dict) -> None:
    """Replace each per-equipment "not evaluated" map with counts per reason (in place)."""
    for groups in scores.values():
        for engines in groups.values():
            for sc in engines.values():
                for fc in sc["per_fc"].values():
                    counts: dict = {}
                    for why in fc["not_evaluated"].values():
                        counts[str(why)] = counts.get(str(why), 0) + 1
                    fc["not_evaluated"] = {"counts_by_reason": counts}
                sc["overall"]["not_evaluated"] = len(sc["overall"]["not_evaluated"])


def probe_report(verdicts: list) -> dict:
    """Observed vs expected native alarm per probe and engine/profile."""
    out = {}
    for name, p in hx.PROBES.items():
        rows = {}
        for v in verdicts:
            if v.equip != name or v.fc != p["fc"]:
                continue
            key = f"{v.engine} [{v.profile}]"
            st, why = hx.native_verdict(v)
            rows[key] = {"observed": st, "expected_fired": p["expect"].get(key), "reason": why}
        out[name] = {"fc": p["fc"], "what": p["what"], "engines": rows}
    return out


def probe_markdown(results: dict) -> str:
    """Markdown table of the probe outcomes."""
    lines = ["# Synthetic engine probes", "", "Each probe is a one-day steady frame (fan on).", ""]
    for name, p in results["probes"].items():
        lines += [
            f"## {name} ({p['fc']})",
            "",
            p["what"],
            "",
            "| engine [profile] | observed | expected |",
            "|---|---|---|",
        ]
        for k, r in p["engines"].items():
            exp = r["expected_fired"]
            want = "" if exp is None else ("fired" if exp else "not_fired")
            lines.append(f"| {k} | {r['observed']} | {want} |")
        lines.append("")
    if results.get("not_run"):
        lines += ["## Not run", ""] + [f"- **{k}**: {v}" for k, v in results["not_run"].items()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
