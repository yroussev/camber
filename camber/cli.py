"""Command-line entry point for CAMBER.

Subcommands:

    camber run     <config.json> [--out DIR]        # run a config, print/write findings
    camber report  <config.json> --out site.html    # run + write an HTML audit report
    camber explain <config.json> [--no-strict]      # grounded plain-language explanation of
                                                     # findings
    camber ask "<question>" --config <config.json> [--llm-cmd CMD]   # grounded Q&A over the run
    camber fleet   '<glob>' [--ask Q] [--out f.html] [--llm-cmd CMD] # portfolio rollup + triage
    camber charts  (--csv F | --demo reheat) [--ahu N] [--out DIR]   # the legacy AHU HeC charts
    camber validate [--html d.html] [--json d.json] [--full]         # validation dossier
    camber serve   <store> [--host H] [--port P]                     # read-only API + live /ui
    camber datasets list|info|fetch|ingest|status|remove|config|score # open dataset catalog

The agent subcommands (`explain`, `ask`) are grounded and useful with **no LLM** (deterministic
templates). To wire a model, pass ``--llm-cmd`` a shell command that reads the prompt on stdin and
writes the completion to stdout — a vendor-neutral seam (no provider is named or imported). The
subprocess wrapper lives here, not in ``camber.agent``, so that package stays pure.

    # legacy invocation still works via the charts subcommand:
    python -m camber.cli charts --demo reheat --ahu 1 --out out/
"""

from __future__ import annotations

import argparse
import json
import os
import sys

__all__ = [
    "main",
]

# --------------------------------------------------------------------------- LLM seam
# (vendor-neutral)


def _subprocess_client(cmd: str):
    """Wrap a shell command as an agent ``complete`` callable: prompt on stdin, completion on
    stdout.

    Kept in the CLI (not camber.agent) so the agent package imports no subprocess / does no I/O.
    """
    import subprocess

    from .agent import client_from_callable

    def _complete(prompt: str, **_opts) -> str:
        proc = subprocess.run(cmd, shell=True, input=prompt, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"--llm-cmd failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout

    return client_from_callable(_complete)


def _client_from_args(args):
    cmd = getattr(args, "llm_cmd", None)
    return _subprocess_client(cmd) if cmd else None


# --------------------------------------------------------------------------- findings helpers


def _print_findings(findings) -> None:
    order = {"fault": 0, "warn": 1, "info": 2, "ok": 3}
    for f in sorted(findings, key=lambda x: order.get(x.severity, 9)):
        if f.severity in ("fault", "warn"):
            print(f"  [{f.severity:5s}] {f.equip:12s} {f.rule:26s} {f.summary}")
    n = {s: sum(1 for f in findings if f.severity == s) for s in ("fault", "warn", "info", "ok")}
    print(
        f"\n{len(findings)} findings — {n['fault']} fault, {n['warn']} warn, "
        f"{n['info']} info, {n['ok']} ok"
    )


# --------------------------------------------------------------------------- subcommands


def _cmd_run(args) -> int:
    from .config import run_config_file

    res = run_config_file(args.config)
    print(f"Site '{res.site}': {res.equipment} equipment, {len(res.rules_run)} rules run")
    _print_findings(res.findings)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "findings.json")
        json.dump([f.as_dict() for f in res.findings], open(path, "w"), indent=2, default=str)
        print(f"wrote {path}")
    return 0


def _cmd_report(args) -> int:
    from .config import data_sources, load_config, run_config_file
    from .report.audit import AuditReport

    res = run_config_file(args.config)
    report = res.report
    if report is None:
        base = os.path.dirname(os.path.abspath(args.config))
        report = AuditReport(
            building=res.site,
            level=2,
            data_sources=data_sources(load_config(args.config), base_dir=base),
        )
        report.add_findings(res.findings)
    html = report.to_html(recommend=True)
    open(args.out, "w").write(html)
    print(f"wrote {args.out}  ({len(res.findings)} findings)")
    return 0


def _cmd_validate(args) -> int:
    from .dossier import build_dossier

    dossier = build_dossier(full=args.full)
    print(dossier.to_text())
    if args.json:
        json.dump(dossier.as_dict(), open(args.json, "w"), indent=2)
        print(f"wrote {args.json}")
    if args.html:
        open(args.html, "w").write(dossier.to_html())
        print(f"wrote {args.html}")
    return 0


def _cmd_serve(args) -> int:  # pragma: no cover - blocking server loop
    from .api.server import serve
    from .store import ParquetStore

    print(f"CAMBER read-only API + live dashboard on http://{args.host}:{args.port}/ui")
    print("(read-only, GET-only; bind stays on localhost unless you change --host)")
    serve(ParquetStore(args.store), host=args.host, port=args.port)
    return 0


def _cmd_explain(args) -> int:
    from .agent import explain
    from .config import run_config_file

    res = run_config_file(args.config)
    g = explain(res.findings, client=_client_from_args(args), strict=not args.no_strict)
    print(g.text)
    print(f"\n[{g.source}; grounded={g.grounded}; {len(g.cited)} citations]")
    return 0


def _cmd_ask(args) -> int:
    from .agent import ask, build_context
    from .config import run_config_file

    res = run_config_file(args.config)
    ctx = build_context(run=res)
    g = ask(args.question, ctx, client=_client_from_args(args), strict=not args.no_strict)
    print(g.text)
    print(f"\n[{g.source}; grounded={g.grounded}; {len(g.cited)} citations]")
    return 0


def _cmd_fleet(args) -> int:
    import glob as _glob

    from .config import run_config_file
    from .report.fleet import build_fleet_report

    paths = sorted(_glob.glob(args.glob))
    if not paths:
        print(f"no config files matched: {args.glob}", file=sys.stderr)
        return 2
    buildings = []
    for p in paths:
        res = run_config_file(p)
        buildings.append(
            {"site": res.site or os.path.basename(p), "eui": None, "findings": res.findings}
        )
    fr = build_fleet_report(buildings)
    print(fr.to_text())
    if args.out:
        open(args.out, "w").write(fr.to_html())
        print(f"\nwrote {args.out}")
    if args.ask:
        from .agent import ask, build_context

        g = ask(
            args.ask,
            build_context(fleet=fr),
            client=_client_from_args(args),
            strict=not args.no_strict,
        )
        print(f"\nQ: {args.ask}\n{g.text}\n[{g.source}; grounded={g.grounded}]")
    return 0


def _cmd_charts(args) -> int:
    """The legacy AHU heating-vs-cooling scatter/timeseries charts."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .charts.scatter import ahu_hec_scatter
    from .charts.timeseries import ahu_hec_timeseries

    if args.demo:
        from .synth import make_ahu_trends

        df = make_ahu_trends(fault=args.demo, ahu_id=args.ahu or 1)
    else:
        from .io import load_csv

        df = load_csv(args.csv, timestamp_col=args.timestamp_col, resample=args.resample)

    if args.ahu:
        ids = [args.ahu]
    else:
        from .points import count_equipment

        ids = list(range(1, count_equipment(df.columns, "AHU") + 1))
    if not ids:
        print("No AHU equipment found (need columns like AHU1_HeC, AHU1_CC).", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    summary = []
    for i in ids:
        ax, m = ahu_hec_scatter(df, i, threshold=args.threshold, occupied_only=args.occupied_only)
        ax.figure.tight_layout()
        ax.figure.savefig(os.path.join(args.out, f"AHU{i}_HeC_scatter.png"), dpi=120)
        plt.close(ax.figure)
        ax2 = ahu_hec_timeseries(df, i, threshold=args.threshold)
        ax2.figure.tight_layout()
        ax2.figure.savefig(os.path.join(args.out, f"AHU{i}_HeC_timeseries.png"), dpi=120)
        plt.close(ax2.figure)
        summary.append(m.as_dict())
        print(
            f"AHU{i}: simultaneous H/C = {m.simultaneous_pct:.1f}% "
            f"({m.simultaneous_pct_oat_gt_65:.1f}% at OAT>65°F), n={m.n_considered}"
        )

    with open(os.path.join(args.out, "hec_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {len(ids) * 2} charts + hec_summary.json to {args.out}/")
    return 0


def _cmd_bacnet_discover(args) -> int:  # pragma: no cover - drives a live BACnet network
    """Discover a BACnet network (read-only) and bootstrap a role mapping."""
    import json as _json

    from .ingest.bacnet_client import BacnetClientConfig, discover_default
    from .ingest.bacnet_discovery import discovery_to_inventory, to_rows
    from .interop.bacnet import roles_from_bacnet

    cfg = BacnetClientConfig.from_file(args.config) if args.config else BacnetClientConfig()
    if args.local_address:
        cfg.local_address = args.local_address
    if args.instance is not None:
        cfg.local_device_id = args.instance
    if args.timeout is not None:
        cfg.timeout = args.timeout
    if args.range_low is not None and args.range_high is not None:
        cfg.device_range = (args.range_low, args.range_high)
    if args.device:
        cfg.known_addresses = tuple(args.device)  # unicast discovery, skips broadcast Who-Is

    devices = discover_default(cfg)
    objs = [o for d in devices for o in d.objects]
    rows = to_rows(discovery_to_inventory(devices))
    roles = roles_from_bacnet(objs)
    print(
        f"discovered {len(devices)} device(s), {len(objs)} object(s); "
        f"bootstrapped {len(roles)} role mapping(s)"
    )
    if args.out:
        payload = {"inventory": rows, "roles": {n: r.value for n, r in roles.items()}}
        with open(args.out, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, default=str)
        print(f"wrote {args.out}")
    return 0


def _cmd_edge_run(args) -> int:
    from .edge.config import build_forwarder, load_config

    cfg = load_config(args.config)
    fwd = build_forwarder(cfg)
    print(f"edge: forwarding facility '{cfg.facility_id}' every {cfg.interval:.0f}s (one-way)")
    fwd.run(cfg.interval)
    return 0


def _cmd_edge_send_once(args) -> int:
    from .edge.config import build_forwarder, load_config

    cfg = load_config(args.config)
    fwd = build_forwarder(cfg)
    res = fwd.poll_once()
    print(
        f"edge: facility '{res.facility_id}' rows={res.rows} parts={res.spooled} "
        f"forwarded={res.forwarded} window={res.window}"
    )
    return 0


def _cmd_edge_status(args) -> int:
    from .edge.config import load_config
    from .edge.spool import Spool

    cfg = load_config(args.config)
    count, nbytes = Spool(cfg.spool_dir, max_bytes=cfg.spool_max_bytes).depth()
    print(f"edge spool '{cfg.spool_dir}': {count} batch(es) pending, {nbytes} bytes queued")
    return 0


def _cmd_edge_selftest(args) -> int:
    """Dry-run through an in-memory sink: prove read-only + build the batches without any egress."""
    from .edge.config import build_forwarder, load_config
    from .edge.sink import collect_sink

    cfg = load_config(args.config)
    sink, log = collect_sink()
    fwd = build_forwarder(cfg, sink=sink)
    res = fwd.poll_once()
    print(f"edge selftest: would send {len(log)} object(s); rows={res.rows}")
    for obj in log:
        print(f"  {obj['key']} ({len(obj['data'])} bytes)")
    print("no real sink touched; no BAS write path exists (see docs/EDGE-DEPLOY.md).")
    return 0


# --------------------------------------------------------------------------- datasets subcommands
#
# The open dataset catalog (camber.datasets). Exit codes: 2 = checksum mismatch (a changed upstream
# file is never accepted), 3 = licence gate (research-only data needs --accept-noncommercial),
# 4 = not enough disk. Imports stay lazy so `camber --help` never pays for them.

_DS_EXIT_CHECKSUM, _DS_EXIT_LICENCE, _DS_EXIT_DISK = 2, 3, 4


def _ds_entries(args, *, include_research: bool):
    from . import datasets as ds

    if getattr(args, "all", False):
        return ds.catalog(licence="all" if include_research else "commercial")
    return [ds.get(i) for i in args.ids]


def _ds_errors(fn):
    """Map catalog errors onto the documented exit codes, printing the reason."""

    def wrapped(args) -> int:
        from .datasets._archive import UnsafeArchive
        from .datasets._fetch import ChecksumMismatch, FetchError, InsufficientSpace

        try:
            return fn(args)
        except ChecksumMismatch as e:
            print(f"error: {e}", file=sys.stderr)
            return _DS_EXIT_CHECKSUM
        except PermissionError as e:
            print(f"error: {e}", file=sys.stderr)
            return _DS_EXIT_LICENCE
        except InsufficientSpace as e:
            print(f"error: {e}", file=sys.stderr)
            return _DS_EXIT_DISK
        except (FetchError, UnsafeArchive, FileNotFoundError, KeyError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    wrapped.__name__ = fn.__name__
    return wrapped


def _mb(n) -> str:
    from .datasets._fetch import human_bytes

    return human_bytes(n or 0)


@_ds_errors
def _cmd_datasets_list(args) -> int:
    from . import datasets as ds

    rows = ds.catalog(licence=args.licence, kind=args.kind, labeled=True if args.labeled else None)
    if args.json:
        print(json.dumps([e.as_dict() for e in rows], indent=2))
        return 0
    head = f"{'id':14s} {'licence':16s} {'access':13s} {'kind':9s} {'labels':6s} {'default':>9s}"
    print(f"{head}  title")
    for e in rows:
        print(
            f"{e.id:14s} {e.licence:16s} {e.access:13s} {e.kind:9s} "
            f"{'yes' if e.labeled_faults else 'no':6s} {_mb(e.download_bytes()):>9s}  {e.title}"
        )
    print(f"\n{len(rows)} dataset(s). `camber datasets info <id>` for details and citation.")
    return 0


@_ds_errors
def _cmd_datasets_info(args) -> int:
    from . import datasets as ds

    e = ds.get(args.id)
    if args.json:
        print(json.dumps(e.as_dict(), indent=2))
        return 0
    print(f"{e.title}  [{e.id}]\n\n{e.summary}\n")
    print(f"publisher : {e.publisher}")
    print(f"licence   : {e.licence} ({e.access})" + ("  share-alike" if e.share_alike else ""))
    print(f"source    : {e.landing_url}")
    print(f"cite      : {e.citation}")
    if e.dois:
        print(f"doi       : {', '.join(e.dois)}")
    print(f"kind      : {e.kind}; labelled faults: {'yes' if e.labeled_faults else 'no'}")
    if e.equipment:
        print(f"equipment : {e.equipment}")
    if e.teaches:
        print("teaches   : " + "; ".join(e.teaches))
    print("\nsubsets:")
    for name, sub in e.subsets.items():
        nruns = len(e.runs(name)) if e.ingest.get("runs") else 0
        runs = f", {nruns} run(s)" if nruns else ""
        print(
            f"  {name:8s} {_mb(e.download_bytes(name)):>9s}{runs} -- {sub.get('description', '')}"
        )
    rules = (e.suggested_analyses or {}).get("rules")
    if rules:
        print("\nsuggested rules: " + ", ".join(rules))
    if e.known_issues:
        print("\nknown issues:")
        for k in e.known_issues:
            print(f"  - {k}")
    return 0


def _progress_printer():
    import time

    state = {"t": 0.0}

    def cb(name, done, total):
        now = time.monotonic()
        if done != total and now - state["t"] < 0.5:
            return
        state["t"] = now
        tot = f"/{_mb(total)}" if total else ""
        end = "\n" if total and done >= total else ""
        print(f"\r  {name}: {_mb(done)}{tot}", end=end, file=sys.stderr, flush=True)

    return cb


@_ds_errors
def _cmd_datasets_fetch(args) -> int:
    from . import datasets as ds

    if not args.all and not args.ids:
        print("error: name dataset id(s) or pass --all", file=sys.stderr)
        return 1
    entries = _ds_entries(args, include_research=args.licence == "all")
    for e in entries:
        print(f"fetching {e.id} ({args.subset or 'default'}, {_mb(e.download_bytes(args.subset))})")
        res = ds.fetch(
            e.id,
            subset=args.subset,
            data_dir=args.dir,
            accept_noncommercial=args.accept_noncommercial,
            progress=None if args.quiet else _progress_printer(),
        )
        for f in res.files:
            state = "verified (already present)" if f["skipped"] else "downloaded + verified"
            print(f"  {f['name']}: {state}, sha256 {f['sha256'][:12]}…")
        for w in res.warnings:
            print(f"  warning: {w}")
        if res.acknowledged:
            print(f"  licence acknowledged: {e.licence} (research / non-commercial use only)")
        print(f"  licence: {e.licence}. Please cite: {res.citation}\n")
    return 0


@_ds_errors
def _cmd_datasets_ingest(args) -> int:
    from . import datasets as ds

    if not args.all and not args.ids:
        print("error: name dataset id(s) or pass --all", file=sys.stderr)
        return 1
    sname = args.subset or "default"
    entries = _ds_entries(args, include_research=True)
    if args.all:  # only what has been fetched for this subset
        fetched = {r["id"] for r in ds.status(data_dir=args.dir) if r["fetched"].get(sname)}
        entries = [e for e in entries if e.id in fetched and sname in e.subsets]
    for e in entries:
        res = ds.ingest(
            e.id,
            args.store,
            subset=args.subset,
            data_dir=args.dir,
            force=args.force,
            progress=None if args.quiet else (lambda m: print(f"  {m}", file=sys.stderr)),
        )
        if res.skipped:
            print(f"{e.id}: up to date in {res.store} ({', '.join(res.facilities)}) -- skipped")
            continue
        print(
            f"{e.id}: ingested {res.rows:,} rows, {res.equipment} equipment into "
            f"{', '.join(res.facilities)} ({res.store})"
        )
        for n in res.notes:
            print(f"  quirk {n}")
        for w in res.warnings:
            print(f"  warning: {w}")
    return 0


@_ds_errors
def _cmd_datasets_status(args) -> int:
    from . import datasets as ds

    rows = ds.status(data_dir=args.dir, store=args.store)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    for r in rows:
        got = [s for s, ok in r["fetched"].items() if ok]
        fetched = ",".join(got) if got else "-"
        ing = ", ".join(r["ingested"]) if r["ingested"] else "-"
        print(
            f"{r['id']:14s} fetched: {fetched:14s} on disk: {_mb(r['bytes_on_disk']):>9s}  "
            f"ingested: {ing}"
        )
    return 0


@_ds_errors
def _cmd_datasets_remove(args) -> int:
    from . import datasets as ds

    res = ds.remove(args.id, data_dir=args.dir, store=args.store, purge_store=args.purge_store)
    print(f"{args.id}: freed {_mb(res['freed_bytes'])}")
    if res["facilities_dropped"]:
        print(f"  dropped from the store: {', '.join(res['facilities_dropped'])}")
    return 0


@_ds_errors
def _cmd_datasets_config(args) -> int:
    from . import datasets as ds

    cfg = ds.config_template(args.id, args.store, facility_id=args.facility, out=args.out)
    if args.out:
        print(f"wrote {args.out} (facility {cfg['source']['facility_id']})")
        print(f"next: camber report {args.out} --out report.html")
    else:
        print(json.dumps(cfg, indent=2))
    return 0


@_ds_errors
def _cmd_datasets_score(args) -> int:
    from . import datasets as ds

    res = ds.score(args.id, args.store, findings=args.findings, facility_id=args.facility)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    o = res["overall"]
    print(
        f"{res['dataset_id']} ({res['facility_id']}, subset {res['subset']}): {res['n']} scenarios"
    )
    print(
        f"overall detection: TPR {o['tpr']:.0%} [{o['tpr_ci'][0]:.0%}-{o['tpr_ci'][1]:.0%}]  "
        f"FPR {o['fpr']:.0%} [{o['fpr_ci'][0]:.0%}-{o['fpr_ci'][1]:.0%}]  "
        f"accuracy {o['accuracy']:.0%}"
    )
    cd = res["correct_diagnosis"]
    if cd == cd:
        print(f"correct diagnosis (right detector for the fault): {cd:.0%}")
    for name, c in res["per_detector"].items():
        print(
            f"  {name:22s} TPR {c['tpr']:.0%} [{c['tpr_ci'][0]:.0%}-{c['tpr_ci'][1]:.0%}]  "
            f"FPR {c['fpr']:.0%} [{c['fpr_ci'][0]:.0%}-{c['fpr_ci'][1]:.0%}]  (n={c['n']})"
        )
    for r in res["records"]:
        print(f"  {r['equip']:36s} truth={r['truth'] or 'fault-free':14s} fired={r['fired']}")
    return 0


# --------------------------------------------------------------------------- drift subcommands
#
# The baseline store is written by exactly two verbs -- `freeze` (create a missing reference) and
# `accept` (move an existing one, attributed). `run`/`report` open it read-only: a run that mints
# the baseline it scores against would define away the very drift it is meant to catch.


def _drift_banner() -> str:
    from .driftthresholds import MAGNITUDE_NOTE, TEMPORAL_NOTE

    return f"\nHow to read these severities:\n  - {MAGNITUDE_NOTE}.\n  - {TEMPORAL_NOTE}."


def _drift_load(args):
    """Load a config file and the base dir its relative paths resolve against."""
    from .config import load_config

    return load_config(args.config), os.path.dirname(os.path.abspath(args.config))


def _print_drift(result) -> None:
    rank = {"fault": 0, "warn": 1, "info": 2, "ok": 3}
    for fam in result.families:
        print(
            f"\n{fam.label} — {fam.equip_class}  (baseline {tuple(fam.baseline)} "
            f"vs current {tuple(fam.current)})"
        )
        if not fam.diagnoses:
            print("  no equipment produced a verdict")
        for d in sorted(fam.diagnoses, key=lambda x: rank.get(x.severity, 9)):
            causes = "; ".join(getattr(d, "causes", []) or []) or "steady"
            print(f"  [{d.severity:5s}] {d.equip:12s} locus={d.locus:14s} {causes}")
        if fam.plant is not None:
            print(f"  plant: {fam.plant.summary}")
        for row in fam.unevaluated:
            why = row.get("reason", "not evaluated")
            detail = (
                ", ".join(row.get("declined") or [])
                if row.get("declined")
                else "needs " + ", ".join(row.get("roles_required") or [])
            )
            print(f"  [ n/a ] {row['equip']:12s} not evaluated — {why} ({detail})")
    print(_drift_banner())


def _cmd_drift_run(args) -> int:
    from .config import run_drift_config

    cfg, base = _drift_load(args)
    res = run_drift_config(cfg, base_dir=base, run_id=args.run_id or None)
    if res is None:
        print("config has no 'drift' section (or it names no families) — nothing to do")
        return 0
    _print_drift(res)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        dpath = os.path.join(args.out, "drift.json")
        json.dump(res.as_dict(), open(dpath, "w"), indent=2, default=str)
        fpath = os.path.join(args.out, "findings.json")
        json.dump([f.as_dict() for f in res.findings], open(fpath, "w"), indent=2, default=str)
        print(f"\nwrote {dpath}\nwrote {fpath}")
    return 0


def _cmd_drift_report(args) -> int:
    from .config import data_sources, run_drift_config
    from .report.drift import drift_report_html

    cfg, base = _drift_load(args)
    res = run_drift_config(
        cfg, base_dir=base, run_id=args.run_id or None, evidence=bool(args.charts)
    )
    if res is None:
        print("config has no 'drift' section (or it names no families) — nothing to do")
        return 0
    html = drift_report_html(
        res, charts=bool(args.charts), data_sources=data_sources(cfg, base_dir=base)
    )
    open(args.out, "w").write(html)
    n = sum(len(f.diagnoses) for f in res.families)
    print(f"wrote {args.out}  ({n} verdict(s) across {len(res.families)} family/families)")
    return 0


def _cmd_drift_freeze(args) -> int:
    """Create the missing baselines a drift comparison measures against — the only create path."""
    from .config import drift_store_path, run_drift_config
    from .store.modelstore import BaselineStore

    cfg, base = _drift_load(args)
    path = drift_store_path(cfg, base_dir=base)
    store = BaselineStore.load(path)
    before = {r.fingerprint for r in store.records()}
    res = run_drift_config(
        cfg, base_dir=base, freeze_if_missing=True, run_id=args.run_id or None, store=store
    )
    if res is None:
        print("config has no 'drift' section (or it names no families) — nothing to do")
        return 0
    after = {r.fingerprint for r in store.records()}
    new = len(after - before)
    if args.dry_run:
        print(
            f"dry run: would freeze {new} new baseline(s); {len(before)} already frozen "
            f"(left untouched). {path} not written."
        )
        return 0
    print(f"froze {new} new baseline(s); {len(before)} already frozen (left untouched)")
    if new:
        store.save(path)
        print(f"wrote {path}")
    else:
        print(f"{path} left unchanged (nothing new to freeze)")
    print(_drift_banner())
    return 0


def _cmd_drift_list(args) -> int:
    from .config import drift_store_path
    from .store.modelstore import BaselineStore

    cfg, base = _drift_load(args)
    path = drift_store_path(cfg, base_dir=base)
    recs = BaselineStore.load(path).records()
    if args.equip:
        recs = [r for r in recs if r.equip in set(args.equip)]
    if args.kind:
        recs = [r for r in recs if r.kind in set(args.kind)]
    if not recs:
        print(f"no frozen baselines in {path} (run `camber drift freeze` first)")
    for r in recs:
        window = f"{r.period_start}..{r.period_end}".strip(".")
        who = f" accepted_by={r.accepted_by}" if r.accepted_by else ""
        sup = f" supersedes={r.supersedes}" if r.supersedes else ""
        hist = f" history={len(r.history)}" if r.history else ""
        print(
            f"{r.equip:14s} {r.kind:26s} frozen_at={r.frozen_at or '-'} [{window}]"
            f"{who}{sup}{hist}  {r.reason}"
        )
    if args.json:
        json.dump([r.as_dict() for r in recs], open(args.json, "w"), indent=2, default=str)
        print(f"wrote {args.json}")
    return 0


def _cmd_drift_accept(args) -> int:
    """Move frozen references to a newly fitted normal — an attributed operator decision."""
    from datetime import datetime, timezone

    from .config import drift_refit, drift_store_path, load_config
    from .driftrun import accept_new_normal_from_periods
    from .store.modelstore import BaselineStore

    cfg, base = _drift_load(args)
    path = drift_store_path(cfg, base_dir=base)
    store = BaselineStore.load(path)
    at = args.run_id or datetime.now(timezone.utc).isoformat(timespec="seconds")
    period = tuple(args.period) if args.period else None

    want_equips = set(args.equip)
    want_kinds = set(args.kind) if args.kind else None
    before = {(r.equip, r.kind): r.frozen_at for r in store.records()}
    unknown = want_equips - {e for e, _ in before}
    for equip in sorted(unknown):
        print(f"note: {equip} has no frozen baseline yet — accepting will establish one")

    refits = drift_refit(cfg, base_dir=base, period=period, run_id=at)
    window = period or tuple(load_config(args.config)["drift"]["current"])

    # Say what could not be re-fit rather than silently moving fewer references than asked for.
    for equip, kind in sorted(before):
        if equip not in want_equips or (want_kinds and kind not in want_kinds):
            continue
        if (equip, kind) not in refits:
            print(f"could not refit {kind!r} for {equip} over {window} — leaving it frozen")

    recs = accept_new_normal_from_periods(
        store,
        refits,
        site=cfg.get("site", ""),
        accepted_by=args.by,
        reason=args.reason,
        at=at,
        equips=want_equips,
        kinds=args.kind or None,
        period=window,
    )
    for r in recs:
        prev = before.get((r.equip, r.kind), "-")
        print(
            f"{r.equip}/{r.kind}: frozen_at {prev} -> {r.frozen_at} "
            f"(supersedes {r.supersedes or '-'}, history now {len(r.history)})"
        )
    if args.dry_run:
        print(f"dry run: would move {len(recs)} baseline(s). {path} not written.")
        return 0
    if recs:
        store.save(path)
        print(f"moved {len(recs)} baseline(s) — accepted by {args.by}: {args.reason}")
        print(f"wrote {path}")
    else:
        print(f"nothing to accept; {path} left unchanged")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="camber", description="CAMBER — BAS trend analysis (FDD / M&V / RCx)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run a config and print/write findings")
    pr.add_argument("config")
    pr.add_argument("--out", help="output dir for findings.json")
    pr.set_defaults(func=_cmd_run)

    prep = sub.add_parser("report", help="run a config and write an HTML audit report")
    prep.add_argument("config")
    prep.add_argument("--out", required=True, help="output .html path")
    prep.set_defaults(func=_cmd_report)

    pe = sub.add_parser("explain", help="grounded plain-language explanation of the findings")
    pe.add_argument("config")
    pe.add_argument(
        "--llm-cmd", dest="llm_cmd", help="shell command: prompt on stdin -> completion on stdout"
    )
    pe.add_argument("--no-strict", action="store_true", help="do not repair ungrounded LLM text")
    pe.set_defaults(func=_cmd_explain)

    pa = sub.add_parser("ask", help="grounded natural-language Q&A over the run")
    pa.add_argument("question")
    pa.add_argument("--config", required=True)
    pa.add_argument(
        "--llm-cmd", dest="llm_cmd", help="shell command: prompt on stdin -> completion on stdout"
    )
    pa.add_argument("--no-strict", action="store_true")
    pa.set_defaults(func=_cmd_ask)

    pf = sub.add_parser("fleet", help="portfolio rollup across configs + optional triage")
    pf.add_argument("glob", help="glob of config .json files, e.g. 'sites/*/config.json'")
    pf.add_argument("--ask", help="a portfolio question to answer (grounded)")
    pf.add_argument("--out", help="output fleet .html path")
    pf.add_argument("--llm-cmd", dest="llm_cmd")
    pf.add_argument("--no-strict", action="store_true")
    pf.set_defaults(func=_cmd_fleet)

    pc = sub.add_parser("charts", help="legacy AHU heating-vs-cooling charts")
    src = pc.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="trend-log CSV path")
    src.add_argument("--demo", choices=["none", "reheat"], help="use built-in synthetic data")
    pc.add_argument("--ahu", type=int, default=None, help="AHU id (default: all found)")
    pc.add_argument("--timestamp-col", dest="timestamp_col", default=None)
    pc.add_argument("--resample", default=None, help='e.g. "15min", "1h"')
    pc.add_argument("--occupied-only", dest="occupied_only", action="store_true")
    pc.add_argument(
        "--threshold",
        type=float,
        default=5.0,
        help="deadband %% above which a valve counts as open",
    )
    pc.add_argument("--out", default="out", help="output directory for PNGs/JSON")
    pc.set_defaults(func=_cmd_charts)

    pv = sub.add_parser(
        "validate", help="unified validation & credibility dossier (text, HTML, JSON)"
    )
    pv.add_argument("--html", help="write the self-contained HTML dossier to this path")
    pv.add_argument("--json", help="write the machine-readable dossier JSON to this path")
    pv.add_argument(
        "--full", action="store_true", help="include per-detector / per-family breakdown metrics"
    )
    pv.set_defaults(func=_cmd_validate)

    psv = sub.add_parser("serve", help="serve the read-only API + live web dashboard (/ui)")
    psv.add_argument("store", help="path to the ParquetStore directory")
    psv.add_argument(
        "--host", default="127.0.0.1", help="bind host (default 127.0.0.1 / localhost)"
    )
    psv.add_argument("--port", type=int, default=8080, help="bind port (default 8080)")
    psv.set_defaults(func=_cmd_serve)

    pb = sub.add_parser(
        "bacnet-discover",
        help="discover a BACnet network (read-only) and bootstrap a role mapping",
    )
    pb.add_argument(
        "--config", help="YAML/JSON BacnetClientConfig file (see docs/INGEST-PROTOCOLS.md)"
    )
    pb.add_argument(
        "--local-address",
        dest="local_address",
        help="interface/IP[:port] to bind on a multi-homed host",
    )
    pb.add_argument("--instance", type=int, help="this host's BACnet device instance")
    pb.add_argument("--timeout", type=float, help="per-request timeout (s)")
    pb.add_argument("--range-low", dest="range_low", type=int, help="Who-Is low device instance")
    pb.add_argument("--range-high", dest="range_high", type=int, help="Who-Is high device instance")
    pb.add_argument(
        "--device",
        dest="device",
        action="append",
        help="known device address (repeatable) — unicast discovery, skips broadcast Who-Is",
    )
    pb.add_argument("--out", help="write the discovered inventory + roles as JSON to this path")
    pb.set_defaults(func=_cmd_bacnet_discover)

    pdr = sub.add_parser(
        "drift", help="baseline-vs-current drift across a detector family (see docs/*-DRIFT.md)"
    )
    drsub = pdr.add_subparsers(dest="drift_cmd", required=True)

    dr = drsub.add_parser("run", help="score the current window against the frozen baselines")
    dr.add_argument("config", help="analysis config JSON with a 'drift' section")
    dr.add_argument("--out", help="output dir for drift.json + findings.json")
    dr.add_argument("--run-id", default="", help="stamp this run id on the results")
    dr.set_defaults(func=_cmd_drift_run)

    drp = drsub.add_parser("report", help="write the drift verdicts as a standalone HTML page")
    drp.add_argument("config")
    drp.add_argument("--out", required=True, help="HTML file to write")
    drp.add_argument("--run-id", default="")
    drp.add_argument(
        "--charts",
        action="store_true",
        help="embed each finding's evidence chart (the period on its frozen baseline's band)",
    )
    drp.set_defaults(func=_cmd_drift_report)

    drf = drsub.add_parser(
        "freeze",
        help="create the missing baselines (the ONLY create path; never overwrites one)",
    )
    drf.add_argument("config")
    drf.add_argument("--run-id", default="", help="stamp this run id as the baselines' frozen_at")
    drf.add_argument(
        "--dry-run", action="store_true", help="report what would be frozen without writing"
    )
    drf.set_defaults(func=_cmd_drift_freeze)

    drl = drsub.add_parser("list", help="show the frozen baselines and their provenance")
    drl.add_argument("config")
    drl.add_argument("--equip", action="append", help="filter to this equipment (repeatable)")
    drl.add_argument("--kind", action="append", help="filter to this model kind (repeatable)")
    drl.add_argument("--json", help="also write the records as JSON to this path")
    drl.set_defaults(func=_cmd_drift_list)

    dra = drsub.add_parser(
        "accept",
        help="move a frozen baseline to a new normal (attributed; --by and --reason required)",
    )
    dra.add_argument("config")
    dra.add_argument(
        "--equip", action="append", required=True, help="equipment to move (repeatable; required)"
    )
    dra.add_argument("--kind", action="append", help="narrow to this model kind (repeatable)")
    dra.add_argument("--by", required=True, help="who is accepting the new normal")
    dra.add_argument(
        "--reason", required=True, help="why the baseline moved (e.g. 'belt replaced')"
    )
    dra.add_argument(
        "--period",
        nargs=2,
        metavar=("START", "END"),
        help="window to re-fit over (default: the config's drift.current — what it is doing now)",
    )
    dra.add_argument("--run-id", default="", help="stamp as frozen_at (default: now, UTC)")
    dra.add_argument("--dry-run", action="store_true", help="show what would move without writing")
    dra.set_defaults(func=_cmd_drift_accept)

    ped = sub.add_parser(
        "edge", help="one-way edge→cloud BAS forwarder (read-only in, outbound-only out)"
    )
    edsub = ped.add_subparsers(dest="edge_cmd", required=True)
    er = edsub.add_parser("run", help="run the forwarder daemon (poll → spool → push, on a loop)")
    er.add_argument("config", help="edge config JSON (no secrets; see docs/EDGE-DEPLOY.md)")
    er.set_defaults(func=_cmd_edge_run)
    es = edsub.add_parser(
        "send-once", help="one poll+push (for cron / Windows Task Scheduler — the IT-friendly path)"
    )
    es.add_argument("config")
    es.set_defaults(func=_cmd_edge_send_once)
    est = edsub.add_parser("status", help="show the local spool depth (pending batches / bytes)")
    est.add_argument("config")
    est.set_defaults(func=_cmd_edge_status)
    esf = edsub.add_parser(
        "selftest", help="dry-run through an in-memory sink (no egress; proves the read-only path)"
    )
    esf.add_argument("config")
    esf.set_defaults(func=_cmd_edge_selftest)

    pds = sub.add_parser(
        "datasets", help="open dataset catalog: fetch, verify, ingest and score (docs/DATASETS.md)"
    )
    dssub = pds.add_subparsers(dest="datasets_cmd", required=True)

    dsl = dssub.add_parser("list", help="list catalog datasets")
    dsl.add_argument("--licence", choices=["commercial", "all"], default="all")
    dsl.add_argument("--kind", choices=["simulated", "real", "lab"])
    dsl.add_argument("--labeled", action="store_true", help="only datasets with fault labels")
    dsl.add_argument("--json", action="store_true")
    dsl.set_defaults(func=_cmd_datasets_list)

    dsi = dssub.add_parser("info", help="details, licence and citation of one dataset")
    dsi.add_argument("id")
    dsi.add_argument("--json", action="store_true")
    dsi.set_defaults(func=_cmd_datasets_info)

    def _targets(p, *, fetching: bool):
        p.add_argument("ids", nargs="*", help="dataset id(s)")
        p.add_argument(
            "--all",
            action="store_true",
            help="every open-tier dataset (add --licence all for research-only ones too)"
            if fetching
            else "every dataset that has been fetched",
        )
        p.add_argument("--subset", help="subset name (default: 'default'; 'full' for everything)")
        p.add_argument("--dir", help="cache directory (default: $CAMBER_DATA_DIR or ~/.cache)")
        p.add_argument("--quiet", action="store_true", help="no progress output")

    dsf = dssub.add_parser("fetch", help="download + verify (size, sha256) from the publisher")
    _targets(dsf, fetching=True)
    dsf.add_argument("--licence", choices=["commercial", "all"], default="commercial")
    dsf.add_argument(
        "--accept-noncommercial",
        dest="accept_noncommercial",
        action="store_true",
        help="acknowledge a research-only (NC/ND) licence; recorded in acknowledgements.json",
    )
    dsf.set_defaults(func=_cmd_datasets_fetch)

    dsg = dssub.add_parser("ingest", help="normalize fetched data into a Parquet store")
    _targets(dsg, fetching=False)
    dsg.add_argument("--store", required=True, help="ParquetStore directory")
    dsg.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    dsg.set_defaults(func=_cmd_datasets_ingest)

    dss = dssub.add_parser("status", help="what is fetched, its size on disk, what is ingested")
    dss.add_argument("--dir")
    dss.add_argument("--store")
    dss.add_argument("--json", action="store_true")
    dss.set_defaults(func=_cmd_datasets_status)

    dsr = dssub.add_parser("remove", help="delete a dataset's downloads (and store facilities)")
    dsr.add_argument("id")
    dsr.add_argument("--dir")
    dsr.add_argument("--store")
    dsr.add_argument(
        "--purge-store", dest="purge_store", action="store_true", help="also drop its facilities"
    )
    dsr.set_defaults(func=_cmd_datasets_remove)

    dsc = dssub.add_parser("config", help="write a ready-to-run config for an ingested dataset")
    dsc.add_argument("id")
    dsc.add_argument("--store", required=True)
    dsc.add_argument("--out", help="config JSON to write (default: print it)")
    dsc.add_argument("--facility", help="facility id (multi-facility datasets such as bdg2)")
    dsc.set_defaults(func=_cmd_datasets_config)

    dsk = dssub.add_parser("score", help="score findings against the ingested fault labels")
    dsk.add_argument("id")
    dsk.add_argument("--store", required=True)
    dsk.add_argument(
        "--findings", help="findings.json from `camber run --out` (default: run the template)"
    )
    dsk.add_argument("--facility")
    dsk.add_argument("--json", action="store_true")
    dsk.set_defaults(func=_cmd_datasets_score)
    return ap


def _ensure_utf8_streams():
    """Force stdout/stderr to UTF-8 so CLI output never crashes on a legacy console.

    Help text and finding summaries contain non-ASCII (``—``, ``°``, ``→``); on a Windows
    ``cp1252`` console argparse's ``--help`` write raises ``UnicodeEncodeError``. Reconfiguring
    to UTF-8 (with ``backslashreplace`` as a belt-and-braces fallback) makes every code path
    encodable on any platform. No-op where the stream can't be reconfigured (e.g. replaced by a
    plain buffer in an embedding host).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):  # pragma: no cover - defensive; stream not reconfigurable
                pass


def main(argv=None):
    """CLI entry point: parse args and dispatch to the requested subcommand."""
    _ensure_utf8_streams()
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
