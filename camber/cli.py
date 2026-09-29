"""Command-line entry point for CAMBER.

Subcommands:

    camber run     <config.json> [--out DIR]        # run a config, print/write findings
    camber report  <config.json> --out site.html    # run + write an HTML audit report
                   [--layout audit|rcx|<plugin>]    # rcx: the printable RCx layout
    camber explain <config.json> [--no-strict]      # grounded plain-language explanation of
                                                     # findings
    camber ask "<question>" --config <config.json> [--llm-cmd CMD]   # grounded Q&A over the run
    camber fleet   '<glob>' [--ask Q] [--out f.html] [--llm-cmd CMD] # portfolio rollup + triage
    camber charts  (--csv F | --demo reheat) [--ahu N] [--out DIR]   # the legacy AHU HeC charts
    camber validate [--html d.html] [--json d.json] [--full]         # validation dossier
    camber serve   <store> [--host H] [--port P]                     # read-only API + live /ui
    camber datasets list|info|fetch|ingest|status|remove|config|score # open dataset catalog
    camber portfolio init|adopt|status|audit|migrate                  # portfolio workspace
    camber facility add|list|show|rename|activate|suspend|resume      # facility lifecycle

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
        _record(res, {path: "report"})
    if res.faults is not None:
        f = res.faults
        print(
            f"faults: {len(f['new'])} new, {len(f['ongoing'])} ongoing, "
            f"{len(f['reopened'])} reopened, {len(f['absent'])} absent -> {f['store']}"
        )
    return 0


def _record(res, paths: dict) -> None:
    """List a command's output files in the facility's workspace manifest (no-op outside one)."""
    from .config import _FacilityCtx, _record_outputs

    if getattr(res, "workspace", None):
        _record_outputs(_FacilityCtx(res.facility_id, res.workspace, res.site), paths)


def _cmd_report(args) -> int:
    from .config import load_config, run_config

    cfg = load_config(args.config)
    base = os.path.dirname(os.path.abspath(args.config))
    layout = args.layout or str((cfg.get("report") or {}).get("layout") or "audit")
    if layout == "rcx":
        return _report_rcx(args, cfg, base)
    res = run_config(cfg, base_dir=base)
    if layout == "audit":
        html = _audit_html(res, cfg, base)
    else:
        html = _plugin_report(layout, res)
        if html is None:
            return 2
    with open(args.out, "w") as fh:
        fh.write(html)
    print(f"wrote {args.out}  ({len(res.findings)} findings, layout {layout})")
    _record(res, {args.out: "report"})
    return 0


def _audit_html(res, cfg, base) -> str:
    from .config import data_sources
    from .report.audit import AuditReport

    report = res.report
    if report is None:
        report = AuditReport(
            building=res.site, level=2, data_sources=data_sources(cfg, base_dir=base)
        )
        report.add_findings(res.findings)
    return report.to_html(recommend=True)


def _plugin_report(layout: str, res):
    """Render a report layout named by a ``camber.reports`` plugin; ``None`` when unknown."""
    from .plugins import PluginRegistry

    reg = PluginRegistry().load_entrypoints(kinds=["reports"])
    try:
        obj = reg.get("reports", layout)
    except KeyError:
        known = ", ".join(["audit", "rcx", *sorted(reg.reports())])
        print(f"unknown report layout {layout!r} (known: {known})", file=sys.stderr)
        return None
    for meth in ("to_html", "render"):
        fn = getattr(obj, meth, None)
        if callable(fn):
            return str(fn(res))
    return str(obj(res))


def _report_rcx(args, cfg, base) -> int:
    from .config import run_config
    from .report.rcx import RcxOptions, build_rcx_report, load_notes, notes_template

    rep_cfg = dict(cfg.get("report") or {})
    rcx_cfg = dict(rep_cfg.get("rcx") or {})
    if args.week:
        rcx_cfg["week"] = args.week
    if args.paper:
        rcx_cfg["paper"] = args.paper
    if args.lifecycle:
        rcx_cfg["lifecycle"] = True
    rep_cfg["rcx"] = rcx_cfg
    options = RcxOptions.from_config(rep_cfg, base_dir=base)
    notes_path = args.notes or rcx_cfg.get("notes")
    if notes_path and not os.path.isabs(notes_path) and not args.notes:
        notes_path = os.path.join(base, notes_path)
    res = run_config(cfg, base_dir=base)
    rep = build_rcx_report(res, options=options, notes=load_notes(notes_path))
    with open(args.out, "w") as fh:
        fh.write(rep.to_html())
    k = rep.kpis
    dollars = f"${k['annual_cost_usd']:,.0f}/yr costed" if k["n_costed"] else "no costed issues"
    print(
        f"wrote {args.out}  (rcx: {k['n_issues']} issues, {dollars}, "
        f"{k['n_conditional']} conditional, {k['n_declined']} declined checks)"
    )
    w = rep.week
    if w is not None:
        print("week: " + (w.explanation if not w.declined else f"declined -- {w.reason}"))
    if rep.orphans:
        print(f"{len(rep.orphans)} engineer note(s) matched no slot -> Appendix E")
    written = {args.out: "report"}
    if args.notes_template:
        with open(args.notes_template, "w") as fh:
            json.dump(notes_template(rep), fh, indent=2)
        print(f"wrote {args.notes_template}  ({len(rep.slots())} note slots)")
        written[args.notes_template] = "report"
    _record(res, written)
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

    from .config import load_config, run_config_file
    from .energy_units import UnitSystem
    from .report.fleet import build_fleet_report

    paths = sorted(_glob.glob(args.glob))
    if not paths:
        print(f"no config files matched: {args.glob}", file=sys.stderr)
        return 2
    buildings, systems = [], set()
    for p in paths:
        res = run_config_file(p)
        us = UnitSystem.from_config(load_config(p))  # 0.93 (#70): the fleet's unit system
        systems.add(None if us is None else us.system)
        buildings.append(
            {"site": res.site or os.path.basename(p), "eui": None, "findings": res.findings}
        )
    units = None
    if len(systems) == 1:
        units = next(iter(systems))
    elif systems - {None}:
        print(
            f"the configs name different unit systems {sorted(map(str, systems))}; the fleet "
            "report keeps kBtu/ft2/yr",
            file=sys.stderr,
        )
    fr = build_fleet_report(buildings, units=units)
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


def _edge_retired(fwd) -> bool:
    """0.95 (#18): refuse to forward from a decommissioned device's spool (prints why)."""
    retired = fwd.spool.retirement()
    if retired is None:
        return False
    print(
        f"error: this edge device was decommissioned at {retired.get('retired_at', '?')} "
        f"(spool {fwd.spool.root}); it no longer forwards",
        file=sys.stderr,
    )
    return True


def _cmd_edge_run(args) -> int:
    from .edge.config import build_forwarder, load_config

    cfg = load_config(args.config)
    fwd = build_forwarder(cfg)
    if _edge_retired(fwd):
        return 1
    print(f"edge: forwarding facility '{cfg.facility_id}' every {cfg.interval:.0f}s (one-way)")
    fwd.run(cfg.interval)
    return 0


def _cmd_edge_send_once(args) -> int:
    from .edge.config import build_forwarder, load_config

    cfg = load_config(args.config)
    fwd = build_forwarder(cfg)
    if _edge_retired(fwd):
        return 1
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
    spool = Spool(cfg.spool_dir, max_bytes=cfg.spool_max_bytes)
    count, nbytes = spool.depth()
    print(f"edge spool '{cfg.spool_dir}': {count} batch(es) pending, {nbytes} bytes queued")
    retired = spool.retirement()  # 0.95 (#18): shown only for a decommissioned device
    if retired is not None:
        print(
            f"  RETIRED at {retired.get('retired_at', '?')} (device "
            f"{retired.get('device_id', '?')}); `edge run` / `send-once` refuse this spool"
        )
    return 0


# ---- 0.95 edge lifecycle (#18 step 5): compaction ------------------------------------------------
def _cmd_edge_compact(args) -> int:
    """Rewrite the spool journal to its pending batches (crash-safe; never drops one)."""
    from .edge.config import load_config
    from .edge.spool import Spool
    from .portfolio import PortfolioLocked

    cfg = load_config(args.config)
    spool = Spool(cfg.spool_dir, max_bytes=cfg.spool_max_bytes, lock_timeout=args.lock_timeout)
    try:
        res = spool.compact(dry_run=args.dry_run)
    except PortfolioLocked as e:
        print(f"error: spool is busy: {e}", file=sys.stderr)
        return 1
    if args.json:
        import dataclasses

        print(json.dumps(dataclasses.asdict(res), indent=2))
        return 0
    verb = "would compact" if args.dry_run else "compacted"
    print(
        f"edge spool '{cfg.spool_dir}': {verb} journal {res.records_before} -> "
        f"{res.records_after} record(s), {res.bytes_before} -> {res.bytes_after} bytes; "
        f"{res.pending} batch(es) still pending (none dropped)"
    )
    if res.torn:
        print(f"  dropped {res.torn} torn journal line(s) (a crash mid-append)")
    if res.missing_payloads:
        print(f"  {len(res.missing_payloads)} committed batch(es) already delivered (payload gone)")
    for label, files in (("orphan payload", res.orphan_payloads), ("tmp file", res.tmp_files)):
        if files:
            print(
                f"  {len(files)} {label}(s) with no journal record (an enqueue interrupted before "
                f"its commit); kept, inspect by hand: {', '.join(files[:5])}"
            )
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
        from .datasets._readers import MissingExtra

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
        except (
            FetchError,
            UnsafeArchive,
            MissingExtra,
            FileNotFoundError,
            KeyError,
            ValueError,
        ) as e:
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
    head = f"{'id':14s} {'tier':13s} {'licence':16s} {'kind':9s} {'labels':6s} {'default':>9s}"
    print(f"{head}  title")
    for e in rows:
        print(
            f"{e.id:14s} {_ds_tier(e):13s} {e.licence:16s} {e.kind:9s} "
            f"{'yes' if e.labeled_faults else 'no':6s} {_mb(e.download_bytes()):>9s}  "
            f"{e.title}{' [manual download]' if e.manual else ''}"
        )
    print(f"\n{len(rows)} dataset(s). `camber datasets info <id>` for details and citation.")
    if any(e.research_only for e in rows):
        print(
            "research-only: NC/ND licence (or a stated access reason; see `datasets info`) -- "
            "fetch needs --accept-noncommercial (recorded), and every report built from it "
            "carries a non-commercial / do-not-redistribute banner."
        )
    return 0


def _ds_tier(e) -> str:
    """The licence tier shown by `datasets list`: ``open`` or ``research-only``."""
    return "research-only" if e.research_only else "open"


@_ds_errors
def _cmd_datasets_info(args) -> int:
    from . import datasets as ds

    e = ds.get(args.id)
    if args.json:
        print(json.dumps(e.as_dict(), indent=2))
        return 0
    print(f"{e.title}  [{e.id}]\n\n{e.summary}\n")
    print(f"publisher : {e.publisher}")
    print(
        f"licence   : {e.licence} (tier: {_ds_tier(e)})"
        + ("  share-alike" if e.share_alike else "")
    )
    if e.research_only:
        print(
            "            research / non-commercial use only; no redistribution. Fetch needs "
            "--accept-noncommercial."
        )
    if e.access_reason:
        print(f"            held research-only although the licence is open: {e.access_reason}")
    print(f"source    : {e.landing_url}")
    if e.manual:
        print(f"download  : manual -- {e.manual_instructions}")
        print(f"            then: camber datasets ingest {e.id} --from-dir DIR --store STORE")
    print(f"cite      : {e.citation}")
    if e.dois:
        print(f"doi       : {', '.join(e.dois)}")
    print(f"kind      : {e.kind}; labelled faults: {'yes' if e.labeled_faults else 'no'}")
    if e.equipment:
        print(f"equipment : {e.equipment}")
    if e.teaches:
        print("teaches   : " + "; ".join(e.teaches))
    print("\nsubsets (download / estimated store size):")
    for name, sub in e.subsets.items():
        nruns = len(e.runs(name)) if e.ingest.get("runs") else 0
        runs = f", {nruns} run(s)" if nruns else ""
        store = e.store_bytes(name)
        print(
            f"  {name:8s} {_mb(e.download_bytes(name)):>9s} / {_mb(store) if store else '?':>9s}"
            f"{runs} -- {sub.get('description', '')}"
        )
    rules = (e.suggested_analyses or {}).get("rules")
    if rules:
        print("\nsuggested rules: " + ", ".join(rules))
    if e.data_issues:
        print("\ndata issues in the published data, and how CAMBER handles them:")
        for i in e.data_issues:
            doc = i.get("contradicts") or {}
            print(
                f"  [{i['handling']}] {i['title']}  ({i['id']}; columns: {', '.join(i['columns'])})"
            )
            print(f"      evidence: {i['evidence']}")
            print(f"      contradicts: {doc.get('document', '')} ({doc.get('citation', '')})")
            print(f"      handling: {i['handling_note']}")
        print("  (`camber datasets ingest --no-corrections` ingests the published data as-is)")
    if e.known_issues:
        print("\nknown issues:")
        for k in e.known_issues:
            print(f"  - {k}")
    return 0


def _store_space_warning(e, subset, store) -> str | None:
    """A warning when the store's filesystem has less free space than the subset's estimate."""
    import shutil

    est = e.store_bytes(subset)
    if not est:
        return None
    probe = os.path.abspath(store)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:  # pragma: no cover - unreadable mount
        return None
    if free < est:
        return (
            f"{e.id} ({subset or 'default'}) needs about {_mb(est)} in the store but only "
            f"{_mb(free)} is free at {probe}"
        )
    return None


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
    if args.all and args.licence == "all" and not args.accept_noncommercial:
        print(
            "error: --all --licence all includes research-only (NC/ND) datasets; add "
            "--accept-noncommercial to acknowledge their licences, or drop --licence all to "
            "fetch the open tier only",
            file=sys.stderr,
        )
        return _DS_EXIT_LICENCE
    entries = _ds_entries(args, include_research=args.licence == "all")
    # the licence gate runs for every named dataset before anything is downloaded
    blocked = [e.id for e in entries if e.research_only and not args.accept_noncommercial]
    if blocked:
        print(
            f"error: {', '.join(blocked)}: research / non-commercial use only (NC/ND licence) and "
            "may not be redistributed; pass --accept-noncommercial to acknowledge the licence "
            "(recorded in acknowledgements.json). Nothing was downloaded.",
            file=sys.stderr,
        )
        return _DS_EXIT_LICENCE
    manual = [e.id for e in entries if e.manual]
    if manual and not args.all:
        print(
            f"error: {', '.join(manual)}: manual download -- CAMBER does not fetch it (see "
            "`camber datasets info <id>`); download the files yourself, then run "
            "`camber datasets ingest <id> --from-dir DIR --store STORE`. Nothing was downloaded.",
            file=sys.stderr,
        )
        return 1
    if args.all and args.accept_noncommercial and args.licence != "all":
        print("note: --all fetches the open tier only; add --licence all for research-only data")
    for e in entries:
        if e.manual:
            print(f"skipping {e.id}: manual download (`camber datasets info {e.id}`)")
            continue
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
    if args.from_dir and args.all:
        print("error: --from-dir takes named dataset id(s), not --all", file=sys.stderr)
        return 1
    sname = args.subset or "default"
    entries = _ds_entries(args, include_research=True)
    if args.all:  # only what has been fetched for this subset
        fetched = {r["id"] for r in ds.status(data_dir=args.dir) if r["fetched"].get(sname)}
        entries = [e for e in entries if e.id in fetched and sname in e.subsets]
    corrections = not args.no_corrections
    for e in entries:
        warn = _store_space_warning(e, args.subset, args.store)
        if warn:
            print(f"warning: {warn}", file=sys.stderr)
        res = ds.ingest(
            e.id,
            args.store,
            subset=args.subset,
            data_dir=args.dir,
            force=args.force,
            progress=None if args.quiet else (lambda m: print(f"  {m}", file=sys.stderr)),
            corrections=corrections,
            accept_noncommercial=args.accept_noncommercial,
            from_dir=args.from_dir,
        )
        if res.skipped:
            print(f"{e.id}: up to date in {res.store} ({', '.join(res.facilities)}) -- skipped")
            continue
        mode = "" if corrections else " (published data as-is: fix quirks skipped)"
        print(
            f"{e.id}: ingested {res.rows:,} rows, {res.equipment} equipment into "
            f"{', '.join(res.facilities)} ({res.store}){mode}"
        )
        if e.research_only:
            why = f" (held research-only: {e.access_reason})" if e.access_reason else ""
            print(
                f"  {e.licence}{why}: research / non-commercial use only, redistribution "
                "prohibited; every report built from it carries that banner"
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


# --------------------------------------------------------------------------- portfolio + facility
#
# Mutating commands take the workspace's single-writer lock *without waiting* (a second concurrent
# admin command is refused with the holder's pid@host) and require --reason, which is audited with
# the OS user. Until CAMBER has authentication, write access to the workspace is admin.

_LATER = {"offboard", "restore", "archive", "purge"}


# --------------------------------------------------------------------------- weather audit (0.94)


def _cmd_weather_audit(args) -> int:
    """Print what CAMBER sent to weather and price services (docs/WEATHER.md#privacy)."""
    import glob

    from .portfolio import find_workspace
    from .portfolio._state import state_dir
    from .weather_privacy import AUDIT_FILE, default_weather_dir, read_weather_audit

    paths: list = []
    if args.file:
        paths.append(args.file)
    if args.cache_dir:
        paths.append(os.path.join(args.cache_dir, AUDIT_FILE))
    ws = find_workspace(args.workspace)
    if args.facility:
        if ws is None:
            print(
                "error: --facility reads state/<facility_id>/ in a portfolio workspace: pass "
                "--workspace PATH or set CAMBER_PORTFOLIO",
                file=sys.stderr,
            )
            return 1
        paths.append(os.path.join(state_dir(ws, args.facility), AUDIT_FILE))
    elif ws is not None and not paths:
        paths += sorted(glob.glob(os.path.join(ws, "state", "*", AUDIT_FILE)))
    if not paths:
        paths.append(os.path.join(default_weather_dir(), AUDIT_FILE))
    rows = read_weather_audit(paths, since=args.since, facility_id=args.facility)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        who = f" [{r['facility_id']}]" if r.get("facility_id") else ""
        sent = "SENT " if r.get("sent") else "local"
        err = f"  ! {r['error']}" if r.get("error") else ""
        print(
            f"{r.get('ts', '?')}  {r.get('service', '?'):10s} {r.get('privacy', '?'):7s} "
            f"{sent} {r.get('cache', '?'):4s}{who}  {r.get('url', '')}"
            f"  ({r.get('purpose') or '-'}){err}"
        )
    n_sent = sum(1 for r in rows if r.get("sent"))
    print(
        f"\n{len(rows)} request(s), {n_sent} sent, {len(rows) - n_sent} served locally; "
        f"from {', '.join(paths)}"
    )
    return 0


def _pf_errors(fn):
    """Print lifecycle / lock / lookup errors as ``error: ...`` with exit code 1 (2 = later)."""

    def wrapped(args) -> int:
        from .portfolio import LifecycleError, PortfolioLocked

        try:
            return fn(args)
        except NotImplementedError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        except (PortfolioLocked, LifecycleError, FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except KeyError as e:
            print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
            return 1

    wrapped.__name__ = fn.__name__
    return wrapped


def _portfolio(args):
    from .portfolio import Portfolio, find_workspace

    ws = find_workspace(getattr(args, "workspace", None))
    if ws is None:
        where = getattr(args, "workspace", None) or os.environ.get("CAMBER_PORTFOLIO")
        hint = f"{where} is not a portfolio workspace" if where else "no portfolio workspace"
        raise FileNotFoundError(
            f"{hint}: pass --workspace PATH or set CAMBER_PORTFOLIO "
            "(create one with `camber portfolio init <root>`)"
        )
    return Portfolio(ws)


@_pf_errors
def _cmd_portfolio_init(args) -> int:
    from .portfolio import Portfolio, is_workspace

    existed = is_workspace(args.root)
    pf = Portfolio.init(args.root)
    verb = "already a workspace" if existed else "created workspace"
    print(f"{verb}: {pf.root}\n  store: {pf.store_root}")
    if not existed:
        print(f"next: export CAMBER_PORTFOLIO={pf.root}; camber facility add <name> --reason ...")
    return 0


@_pf_errors
def _cmd_portfolio_adopt(args) -> int:
    from .portfolio import Portfolio

    pf = Portfolio.adopt(args.store, args.root, reason=args.reason)
    n = len(pf.facilities())
    print(f"adopted {pf.store_root} into workspace {pf.root} ({n} facilities, nothing moved)")
    return 0


@_pf_errors
def _cmd_portfolio_status(args) -> int:
    st = _portfolio(args).status()
    if args.json:
        print(json.dumps(st, indent=2, default=str))
        return 0
    print(f"workspace : {st['root']}  (schema {st['schema_version']})")
    print(f"store     : {st['store']}")
    counts = ", ".join(f"{k} {v}" for k, v in st["by_state"].items() if v) or "none"
    print(f"facilities: {st['facilities']} ({counts})")
    if st["unregistered"]:
        print(f"  unregistered store partitions (read as active): {', '.join(st['unregistered'])}")
    if st["tombstoned"]:
        print(f"tombstoned: {', '.join(st['tombstoned'])}")
    if st["tombstoned_with_data"]:
        print(
            "  data still stored under tombstoned ids (dropped from the registry only): "
            + ", ".join(st["tombstoned_with_data"])
        )
    print(f"legal holds: {', '.join(st['legal_holds']) or 'none'}")
    print(f"lock      : {'held by ' + st['locked_by'] if st['locked_by'] else 'free'}")
    print(f"audit     : {st['audit_records']} record(s)")
    print("retention defaults (enforced by a later release):")
    for cls, rule in st["retention_defaults"].items():
        print(f"  {cls:16s} {', '.join(f'{k}={v}' for k, v in rule.items())}")
    return 0


def _audit_line(r: dict) -> str:
    move = ""
    if (r.get("from_state") or r.get("to_state")) and r.get("from_state") != r.get("to_state"):
        move = f" [{r.get('from_state') or '-'} -> {r.get('to_state') or '-'}]"
    d = r.get("details") or {}
    if "from" in d and "to" in d:
        move += f" [{d['from']!r} -> {d['to']!r}]"
    fid = f" {r['facility_id']}" if r.get("facility_id") else ""
    return (
        f"{r.get('ts', '?')}  {r.get('actor', '?')}@{r.get('host', '?')}  "
        f"{r.get('action', '?')}{fid}{move}  reason: {r.get('reason') or '-'}"
    )


@_pf_errors
def _cmd_portfolio_audit(args) -> int:
    rows = _portfolio(args).audit_log(facility_id=args.facility)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    for r in rows:
        print(_audit_line(r))
    print(f"\n{len(rows)} record(s).")
    return 0


def _print_migration(r: dict) -> None:
    head = "dry run -- nothing written" if r.get("dry_run") else "apply"
    print(f"portfolio migrate ({head}): {r['workspace']}")
    if not r["sources"] and not r["configs"]:
        print("  no legacy files given: pass fault/baseline store paths or --config CFG")
    for s in r["sources"]:
        fac = f" -> {', '.join(s['facilities'])}" if s["facilities"] else ""
        print(f"  [{s['status']:12s}] {s['kind'] or '?':9s} {s['path']} ({s['records']} rec){fac}")
    for c in r["configs"]:
        who = c["facility_id"] or f"UNRESOLVED ({c['problem']})"
        print(f"  config {c['config']} -> {who}")
    if r["labels"]:
        print("site labels:")
        for label, row in sorted(r["labels"].items()):
            if row["problem"]:
                cands = f": {', '.join(row['candidates'])}" if row["candidates"] else ""
                to = f"CANNOT MAP ({row['problem']}{cands})"
            else:
                to = f"{row['facility_id']}  (via {row['via']})"
            print(f"  {label!r:36s} {row['records']:4d} record(s) -> {to}")
    for fid, c in r["facilities"].items():
        parts = [f"{c.get(k, 0)} {k}" for k in ("faults", "baselines", "reports") if c.get(k)]
        extra = f", {c['merged']} merged" if c.get("merged") else ""
        extra += f", {c['skipped']} already migrated" if c.get("skipped") else ""
        print(f"  {fid}: {', '.join(parts) or 'nothing new'}{extra}")
    for m in r["merged"]:
        print(f"  merged {m['facility_id']} {m['equip']} ({m['kind']}): kept {m['kept']}")
    if r["problems"]:
        print("\ncannot migrate -- nothing will be written until these are resolved:")
        for p in r["problems"]:
            cands = f" (candidates: {', '.join(p['candidates'])})" if p.get("candidates") else ""
            print(f"  {p['what']}: {p['problem']}{cands}")
        print('resolve a label explicitly with --map "SITE=FACILITY_ID"; ambiguous labels are')
        print("never guessed, and ids of removed (tombstoned) facilities are never reused.")


@_pf_errors
def _cmd_portfolio_migrate(args) -> int:
    pf = _portfolio(args)
    if args.apply and not (args.reason or "").strip():
        raise ValueError("--apply needs --reason (every portfolio change is audited)")
    r = pf.migrate(
        args.paths, configs=args.config, mapping=args.map, apply=args.apply, reason=args.reason
    )
    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        _print_migration(r)
        if r["blocked"]:
            print("\nexit 1: plan blocked")
        elif r.get("dry_run"):
            print("\nplan is clean; re-run with --apply --reason ... to carry it out")
        elif r.get("changed"):
            print(f"\nmigrated; {len(r['stubs'])} legacy file(s) now redirect to state/<id>/")
        else:
            print("\nnothing to do: already migrated")
    return 1 if r["blocked"] else 0


@_pf_errors
def _cmd_facility_add(args) -> int:
    pf = _portfolio(args)
    e = pf.add_facility(
        args.name,
        reason=args.reason,
        facility_id=args.id,
        owner=args.owner,
        tags=args.tag or (),
        activate=args.activate,
        **({"private": True} if getattr(args, "private", False) else {}),
    )
    fid = e["facility_id"]
    print(f"added {fid} ({e['display_name']}) -- {e['state']}")
    if e.get("private"):
        print("private: weather requests default to offline (docs/WEATHER.md#privacy)")
    if e["state"] == "provisioning":
        print(f"next: camber facility activate {fid} --reason ...")
    return 0


@_pf_errors
def _cmd_facility_list(args) -> int:
    rows = _portfolio(args).facilities(state=args.state)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    print(f"{'facility_id':28s} {'state':13s} {'owner':16s} display name")
    for fid, e in rows.items():
        flag = "" if e.get("registered", True) else "  (unregistered)"
        print(f"{fid:28s} {e['state']:13s} {(e.get('owner') or '-'):16s} {e['display_name']}{flag}")
    print(f"\n{len(rows)} facilit{'y' if len(rows) == 1 else 'ies'}.")
    return 0


@_pf_errors
def _cmd_facility_show(args) -> int:
    from .portfolio import allowed_actions

    pf = _portfolio(args)
    e = pf.facility(args.id)
    info = {
        "facility_id": args.id,
        **e,
        "has_data": args.id in pf.store.facilities(),
        "allowed_actions": allowed_actions(e["state"]),
        "legal_hold": args.id in pf.legal_holds(),
        "retention": pf.effective_retention(args.id),
        "state_dir": pf.state_dir(args.id),
        "manifest": pf.manifest(args.id),
        "audit": pf.audit_log(facility_id=args.id),
    }
    if args.json:
        print(json.dumps(info, indent=2, default=str))
        return 0
    for k in ("facility_id", "display_name", "name", "state", "created_at", "state_changed_at"):
        print(f"{k:17s}: {info.get(k) if info.get(k) is not None else '-'}")
    print(f"{'owner':17s}: {info.get('owner') or '-'}")
    print(f"{'tags':17s}: {', '.join(info.get('portfolio') or []) or '-'}")
    print(f"{'has data':17s}: {'yes' if info['has_data'] else 'no'}")
    print(f"{'legal hold':17s}: {'yes' if info['legal_hold'] else 'no'}")
    if info.get("private"):
        print(f"{'private':17s}: yes (weather requests default to offline)")
    print(f"{'allowed actions':17s}: {', '.join(info['allowed_actions']) or 'none'}")
    print("retention:")
    for cls, r in info["retention"].items():
        rule = ", ".join(f"{k}={v}" for k, v in r["rule"].items())
        print(f"  {cls:16s} {rule}  ({r['source']})")
    man = info["manifest"]
    print(f"state ({info['state_dir']}):")
    rows = [(k, v) for k, v in (man.get("files") or {}).items()]
    rows += [(k, v) for k, v in (man.get("external") or {}).items()]
    for rel, e in rows:
        size = "missing" if e.get("missing") else f"{e.get('bytes')} B"
        print(f"  {e.get('kind', '?'):10s} {rel}  ({size}, sha256 {str(e.get('sha256'))[:12]})")
    if not rows:
        print("  none")
    print("audit:")
    for r in info["audit"]:
        print(f"  {_audit_line(r)}")
    return 0


@_pf_errors
def _cmd_facility_rename(args) -> int:
    e = _portfolio(args).rename(args.id, args.display_name, reason=args.reason)
    print(f"{args.id}: display name is now {e['display_name']!r}")
    return 0


@_pf_errors
def _cmd_facility_private(args) -> int:
    e = _portfolio(args).set_private(args.id, not args.off, reason=args.reason)
    if e.get("private"):
        print(f"{args.id}: private -- weather requests default to offline")
    else:
        print(f"{args.id}: not private -- weather requests follow the config")
    return 0


@_pf_errors
def _cmd_facility_transition(args) -> int:
    if args.facility_cmd in _LATER:
        print(
            f"error: `camber facility {args.facility_cmd}` is available in a later release "
            "(it needs export bundles and the deletion cascade); see docs/PORTFOLIO.md",
            file=sys.stderr,
        )
        return 2
    r = _portfolio(args).transition(args.id, args.facility_cmd, reason=args.reason)
    print(f"{r['facility_id']}: {r['from_state']} -> {r['to_state']}")
    return 0


# ---- 0.95 edge lifecycle (#18 step 5): central reconciliation -----------------------------------
def _edge_ws(p) -> None:
    p.add_argument(
        "--workspace",
        help="portfolio workspace root (default: $CAMBER_PORTFOLIO, else the current dir)",
    )


def _print_reconcile(rep: dict, limit: int) -> None:
    src = rep["source"]
    print(
        f"edge reconcile ({'read-only' if rep.get('read_only') else 'applied'}) {src['kind']} "
        f"{src['path']}: {rep['objects_scanned']} object(s) scanned"
    )
    print("  " + "  ".join(f"{k} {v}" for k, v in rep["counts"].items()))
    rows = rep["objects"]
    for r in rows[:limit]:
        who = r["facility_id"] or "-"
        act = f"  -> {r['action']}" if r.get("action") else ""
        print(f"  {r['category']:16s} {who:28s} {r['key']}  ({r['detail']}){act}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more (use --json or --limit)")


@_pf_errors
def _cmd_edge_reconcile(args) -> int:
    from .edge.landing import reconcile

    pf = _portfolio(args)
    if args.apply:
        from .edge.quarantine import quarantine_reconciled

        if args.keys:
            raise ValueError(
                "--apply works on the workspace store or a local --landing directory; CAMBER "
                "never moves cloud objects (route uploads with a broker, see docs/EDGE-DEPLOY.md)"
            )
        rep = quarantine_reconciled(pf, landing=args.landing, reason=args.reason)
    else:
        rep = reconcile(pf, landing=args.landing, keys=args.keys, prefix=args.prefix or "")
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    _print_reconcile(rep, args.limit)
    if args.apply:
        print(f"  quarantined {rep['quarantined']} object(s) (audited)")
    elif rep["to_quarantine"]:
        hint = "" if args.keys else " (--apply --reason R to move them)"
        print(f"  {rep['to_quarantine']} object(s) should be quarantined{hint}")
    return 0


@_pf_errors
def _cmd_edge_land(args) -> int:
    from .edge.quarantine import land

    rep = land(_portfolio(args), args.inbox, apply=args.apply, reason=args.reason)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    c = rep["counts"]
    head = "applied" if rep["applied"] else "dry run; --apply --reason R to move"
    print(
        f"edge land ({head}) {rep['inbox']}: {c['store']} to the store, "
        f"{c['quarantine']} to quarantine, {c['inbox']} left in the inbox"
    )
    for r in rep["objects"][: args.limit]:
        if r["to"] != "store":
            print(f"  {r['to']:10s} {r['category']:16s} {r['key']}  ({r['detail']})")
    return 0


@_pf_errors
def _cmd_edge_quarantine(args) -> int:
    from .edge import quarantine as q

    pf = _portfolio(args)
    if args.q_cmd == "list":
        rows = q.list_quarantine(pf, facility_id=args.facility)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
            return 0
        if not rows:
            print("quarantine is empty")
            return 0
        for r in rows:
            print(
                f"{r['status']:10s} {str(r.get('facility_id') or '-'):28s} "
                f"{str(r.get('category') or '-'):16s} {r['key']}  "
                f"({r.get('quarantined_at') or '?'}: {r.get('detail') or ''})"
            )
        print(f"{len(rows)} object(s) in quarantine")
        return 0
    if args.q_cmd == "release":
        rep = q.release(
            pf, facility_id=args.facility, keys=args.key, reason=args.reason, apply=args.apply
        )
    else:
        rep = q.discard(
            pf,
            facility_id=args.facility,
            keys=args.key,
            reason=args.reason,
            apply=args.apply,
            yes=args.yes,
            confirm=args.confirm,
        )
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    verb = {"release": "released", "discard": "discarded"}[args.q_cmd]
    head = verb if rep["applied"] else f"would be {verb} (dry run; --apply)"
    print(f"{len(rep['planned'])} object(s) {head}")
    for k in rep["planned"]:
        print(f"  {k}")
    for r in rep["refused"]:
        print(f"  refused {r['key']}: {r['why']}")
    return 1 if rep["refused"] and not rep["planned"] else 0


def _cmd_edge_decommission(args) -> int:
    """Flush the spool, wait for acks, retire the device (dry run unless --apply)."""
    import dataclasses

    from .edge.config import build_forwarder, load_config
    from .edge.decommission import decommission
    from .portfolio import Portfolio, PortfolioLocked, find_workspace

    cfg = load_config(args.config)
    fwd = build_forwarder(cfg, source=_NoSource())
    ws = find_workspace(args.workspace)
    if args.workspace and ws is None:
        print(f"error: {args.workspace} is not a portfolio workspace", file=sys.stderr)
        return 1
    try:
        res = decommission(
            fwd.spool,
            fwd.sink,
            facility_id=cfg.facility_id,
            device_id=args.device or cfg.device_id,
            reason=args.reason,
            apply=args.apply,
            yes=args.yes,
            confirm=args.confirm,
            force=args.force,
            wait=args.wait,
            portfolio=Portfolio(ws) if ws else None,
        )
    except (ValueError, PortfolioLocked) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(dataclasses.asdict(res), indent=2, default=str))
        return 1 if res.refused and not res.dry_run else 0
    head = "dry run; --apply to act" if res.dry_run else "applied"
    print(
        f"edge decommission ({head}) facility '{res.facility_id}' device '{res.device_id}': "
        f"{res.pending_before} batch(es) pending, {res.forwarded} flushed, "
        f"{res.pending_after} unacknowledged"
    )
    if res.refused:
        print(f"  {'would refuse' if res.dry_run else 'REFUSED'}: {res.refused}")
        return 0 if res.dry_run else 1
    if res.dry_run:
        print("  would retire the device (spool takes no new batches) and note it in the registry")
        return 0
    word = "already retired" if res.already_retired else "retired"
    print(
        f"  {word} at {(res.receipt or {}).get('retired_at')}" + (" (FORCED)" if res.forced else "")
    )
    for u in res.unacknowledged:
        print(f"  unacknowledged, kept on disk: {u['key']} ({u['bytes']} bytes)")
    if res.registry_noted:
        print(f"  registry: {res.registry_noted}")
    else:
        print(
            "  no portfolio workspace here: copy the spool's retired.json to the portfolio host "
            "and run `camber edge record-retirement retired.json --reason R`"
        )
    return 0


class _NoSource:
    """A source placeholder: decommissioning never reads the BAS, it only flushes the spool."""

    def point_names(self):
        return []

    def load_points(self, names, resample=None):
        return None


@_pf_errors
def _cmd_edge_bucket_rules(args) -> int:
    """Print (or write) provider lifecycle JSON from a retention policy. Never calls a cloud API."""
    from .edge.bucket_rules import bucket_lifecycle_rules, policy_from_portfolio

    facilities = list(args.facility or []) or None
    if args.policy:
        with open(args.policy, encoding="utf-8") as fh:
            policy = json.load(fh)
    else:
        policy, facs = policy_from_portfolio(_portfolio(args))
        facilities = facilities or facs
    out = bucket_lifecycle_rules(
        policy,
        provider=args.provider,
        facilities=facilities,
        prefix=args.prefix or "",
        container=args.container,
    )
    doc = json.dumps(out["document"], indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(doc + "\n")
    if args.json:
        print(doc)
        return 0
    print(f"# {args.provider} lifecycle rules (dry run: nothing was sent to any cloud)")
    for row in out["plan"]:
        n = len(row["prefixes"])
        print(f"#   {row['class']}: delete after {row['days']} days ({n} prefix(es))")
    for note in out["notes"]:
        print(f"#   note: {note}")
    print(f"# review, then apply yourself: {out['apply_with']}")
    if args.out:
        print(f"# wrote {args.out}")
    else:
        print(doc)
    return 0


@_pf_errors
def _cmd_edge_record_retirement(args) -> int:
    from .edge.decommission import record_retirement

    with open(args.receipt, encoding="utf-8") as fh:
        receipt = json.load(fh)
    r = record_retirement(_portfolio(args), receipt, reason=args.reason)
    print(f"{r['facility_id']}: device {r['device_id']} retirement {r['noted']}")
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


def _drift_ctx(cfg, base):
    from .config import _facility_context

    return _facility_context(cfg, base)


def _state_lock(ctx, *, write: bool = True):
    """The workspace lock for a per-facility state write -- a drift or M&V baseline store (fail
    fast); a no-op outside a workspace or for a dry run."""
    import contextlib

    if not ctx.workspace or not write:
        return contextlib.nullcontext()
    from .portfolio._lock import portfolio_lock

    return portfolio_lock(ctx.workspace, timeout=0.0)


def _state_audit(ctx, action: str, *, reason: str, details: dict) -> None:
    """Audit a per-facility state change inside a workspace (``drift.*``, ``mv.*``), with the
    facility's lifecycle state; a no-op outside a workspace."""
    if not ctx.workspace:
        return
    from .portfolio import Portfolio
    from .portfolio._audit import append_audit, audit_record

    state = Portfolio(ctx.workspace).facility(ctx.facility_id).get("state")
    append_audit(
        ctx.workspace,
        audit_record(
            action,
            facility_id=ctx.facility_id,
            from_state=state,
            to_state=state,
            reason=reason,
            details=details,
        ),
    )


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
        from .config import _record_outputs

        _record_outputs(_drift_ctx(cfg, base), {dpath: "report", fpath: "report"})
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
    from .config import _record_outputs

    _record_outputs(_drift_ctx(cfg, base), {args.out: "report"})
    n = sum(len(f.diagnoses) for f in res.families)
    print(f"wrote {args.out}  ({n} verdict(s) across {len(res.families)} family/families)")
    return 0


def _cmd_drift_freeze(args) -> int:
    """Create the missing baselines a drift comparison measures against — the only create path."""
    from .config import _baseline_store, _record_outputs, run_drift_config
    from .portfolio import PortfolioLocked

    cfg, base = _drift_load(args)
    ctx = _drift_ctx(cfg, base)
    reason = (args.reason or "").strip()
    if ctx.workspace and not args.dry_run and not reason:
        print("error: inside a portfolio workspace `drift freeze` needs --reason (audited)",
              file=sys.stderr)  # fmt: skip
        return 1
    try:
        with _state_lock(ctx, write=not args.dry_run):
            store, path, ctx = _baseline_store(cfg, base_dir=base, ctx=ctx)
            before = {r.fingerprint for r in store.records()}
            res = run_drift_config(
                cfg, base_dir=base, freeze_if_missing=True, run_id=args.run_id or None, store=store
            )
            if res is None:
                print("config has no 'drift' section (or it names no families) — nothing to do")
                return 0
            fresh = [r for r in store.records() if r.fingerprint not in before]
            new = len(fresh)
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
                _record_outputs(ctx, {path: "baselines"})
                _state_audit(
                    ctx,
                    "drift.freeze",
                    reason=reason,
                    details={"store": path, "frozen": sorted(f"{r.equip}/{r.kind}" for r in fresh)},
                )
            else:
                print(f"{path} left unchanged (nothing new to freeze)")
    except PortfolioLocked as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(_drift_banner())
    return 0


def _cmd_drift_list(args) -> int:
    from .config import _baseline_store

    cfg, base = _drift_load(args)
    store, path, _ctx = _baseline_store(cfg, base_dir=base)
    recs = store.records()
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
    from .portfolio import PortfolioLocked

    cfg, base = _drift_load(args)
    ctx = _drift_ctx(cfg, base)
    try:
        with _state_lock(ctx, write=not args.dry_run):
            return _drift_accept(args, cfg, base, ctx)
    except PortfolioLocked as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _drift_accept(args, cfg, base, ctx) -> int:
    from datetime import datetime, timezone

    from .config import _baseline_store, _record_outputs, drift_refit, load_config
    from .driftrun import accept_new_normal_from_periods

    store, path, ctx = _baseline_store(cfg, base_dir=base, ctx=ctx)
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
        site=ctx.site,  # the label the runs key by (a store config's display name when unset)
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
        _record_outputs(ctx, {path: "baselines"})
        _state_audit(
            ctx,
            "drift.accept",
            reason=args.reason,
            details={
                "store": path,
                "accepted_by": args.by,
                "moved": [f"{r.equip}/{r.kind}" for r in recs],
                "at": at,
            },
        )
    else:
        print(f"nothing to accept; {path} left unchanged")
    return 0


# --------------------------------------------------------------------------- mv subcommands
#
# Versioned M&V baselines (#21 phase 21d). `run`, `list`, `propose` and `report` read the store;
# `freeze`, `rebaseline` and `adjust` are the only writers. Each writer needs --reason, is a dry
# run unless --apply is given, and -- inside a portfolio workspace -- takes the workspace lock
# (fail fast) and appends one `mv.<verb>` audit line. CAMBER never rebaselines automatically.


def _mv_suspended(plan_or_state, ctx) -> bool:
    state = plan_or_state if isinstance(plan_or_state, str) else plan_or_state.skipped_state
    if state:
        print(
            f"facility {ctx.facility_id} is {state}: skipped (no meters are read while it is not "
            "active; resume it with `camber facility resume`)"
        )
        return True
    return False


def _print_plan(plan, apply: bool) -> None:
    for ch in plan.changes:
        bits = [f"{ch['baseline']}: {ch['action']} {ch.get('version', '')}".rstrip()]
        if ch.get("window"):
            bits.append(f"window {ch['window'][0]}..{ch['window'][1]}")
        if ch.get("model"):
            bits.append(
                f"{ch['model']} R2 {ch['r2']:.3f} CV(RMSE) {ch['cv_rmse']:.1%} "
                f"G14 {'ok' if ch['g14_accept'] else 'fail'} SEP "
                f"{'valid' if ch['sep_valid'] else 'invalid'}"
            )
        if ch.get("supersedes"):
            bits.append(f"supersedes {ch['supersedes']} for {', '.join(ch['triggers'])}")
        for e in ch.get("entries") or ():
            bits.append(f"entry: {e}")
        print("  " + "; ".join(bits))
        for c in ch.get("caveats") or ():
            print(f"    caveat: {c}")
    for r in plan.refused:
        extra = f" (needs {r['days_needed']} more days)" if r.get("days_needed") else ""
        print(f"  {r['baseline']}: not {plan.verb}d -- {r['why']}{extra}")
    if not plan.changes and not plan.refused:
        print("  no M&V meters found")


def _mv_write(args, verb: str, planner) -> int:
    """The shared write path: lock, plan, print, and (with --apply) save + manifest + audit."""
    from .config import _record_outputs
    from .portfolio import PortfolioLocked

    cfg, base = _drift_load(args)
    ctx = _drift_ctx(cfg, base)
    reason = (args.reason or "").strip()
    if not reason:
        print(f"error: `mv {verb}` needs --reason (it is recorded and audited)", file=sys.stderr)
        return 1
    try:
        with _state_lock(ctx, write=bool(args.apply)):
            plan = planner(cfg, base, reason)
            if _mv_suspended(plan, ctx):
                return 0
            print(f"mv {verb} -> {plan.path}")
            _print_plan(plan, args.apply)
            if not args.apply:
                print(
                    f"dry run: {len(plan.changes)} change(s) planned; nothing written "
                    "(pass --apply to write)"
                )
                return 0
            if not plan.changes:
                print(f"{plan.path} left unchanged (nothing to {verb})")
                return 0 if not plan.refused else 1
            plan.store.save(plan.path)
            print(f"wrote {plan.path}")
            _record_outputs(ctx, {plan.path: "mv_baselines"})
            _state_audit(
                ctx,
                f"mv.{verb}",
                reason=reason,
                details={"store": plan.path, "changes": plan.changes, "refused": plan.refused},
            )
    except PortfolioLocked as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _who(args) -> str:
    from .portfolio._audit import _actor

    return (getattr(args, "by", None) or "").strip() or _actor()


def _cmd_mv_freeze(args) -> int:
    """Freeze version 1 of each meter's M&V baseline (never overwrites one)."""
    from .mvrun import plan_freeze

    return _mv_write(
        args,
        "freeze",
        lambda cfg, base, reason: plan_freeze(
            cfg,
            base_dir=base,
            reason=reason,
            accepted_by=_who(args),
            equips=args.equip,
            allow_short=bool(args.allow_short),
            at=args.run_id or None,
            store_path=args.store,
        ),
    )


def _cmd_mv_rebaseline(args) -> int:
    """Supersede a meter's M&V baseline -- the attributed operator decision."""
    from .mvrun import plan_rebaseline

    prop = None
    if args.from_proposal:
        doc = json.load(open(args.from_proposal))
        rows = [m for m in doc.get("meters", []) if m.get("equip") == args.equip]
        if not rows or not rows[0].get("rebaseline"):
            print(
                f"error: {args.from_proposal} has no rebaseline proposal for {args.equip}",
                file=sys.stderr,
            )
            return 1
        prop = rows[0]["rebaseline"]
    return _mv_write(
        args,
        "rebaseline",
        lambda cfg, base, reason: plan_rebaseline(
            cfg,
            base_dir=base,
            equip=args.equip,
            reason=reason,
            accepted_by=args.by,
            period=args.period,
            as_of=args.as_of,
            trigger_ids=args.trigger or (),
            from_proposal=prop,
            at=args.run_id or None,
            store_path=args.store,
        ),
    )


def _cmd_mv_adjust(args) -> int:
    """Record accepted NRA / static-factor entries on a meter's live baseline version."""
    from .mvrun import plan_adjust

    specs = json.load(open(args.spec))
    if isinstance(specs, dict):
        specs = specs.get("adjustments", specs)
    return _mv_write(
        args,
        "adjust",
        lambda cfg, base, reason: plan_adjust(
            cfg,
            base_dir=base,
            equip=args.equip,
            specs=specs,
            reason=reason,
            accepted_by=args.by,
            as_of=args.as_of,
            at=args.run_id or None,
            store_path=args.store,
        ),
    )


def _mv_cfg(args):
    cfg, base = _drift_load(args)
    if getattr(args, "store", None):
        cfg = {**cfg, "mv_store": os.path.abspath(args.store)}
    return cfg, base


def _cmd_mv_run(args) -> int:
    """Measure every mv meter against its frozen version -- read-only."""
    from .config import run_mv_config
    from .mvrun import _facility_state

    cfg, base = _mv_cfg(args)
    ctx = _drift_ctx(cfg, base)
    state = _facility_state(ctx)
    if state not in (None, "active") and _mv_suspended(state, ctx):
        return 0
    if not cfg.get("mv"):
        print("config has no 'mv' section -- nothing to do")
        return 0
    findings = run_mv_config(cfg, base_dir=base)
    _print_findings(findings)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        fpath = os.path.join(args.out, "mv_findings.json")
        json.dump([f.as_dict() for f in findings], open(fpath, "w"), indent=2, default=str)
        print(f"\nwrote {fpath}")
        from .config import _record_outputs

        _record_outputs(ctx, {fpath: "report"})
    return 0


def _cmd_mv_list(args) -> int:
    from .mvrun import _version_row, open_mv_store

    cfg, base = _mv_cfg(args)
    store, path, ctx = open_mv_store(cfg, base_dir=base)
    recs = store.records()
    if args.equip:
        recs = [r for r in recs if r.equip in set(args.equip)]
    if not recs:
        print(f"no frozen M&V baselines in {path} (run `camber mv freeze` first)")
    rows = []
    for r in recs:
        vs = store.versions(r.site, r.equip, r.kind)
        print(f"{r.equip}/{r.kind}: {len(vs)} version(s)")
        for v in vs:
            row = _version_row(v)
            rows.append({"equip": r.equip, "kind": r.kind, **row})
            trig = f" triggers={','.join(row['trigger_ids'])}" if row["trigger_ids"] else ""
            adj = f" adjustments={row['adjustments']}" if row["adjustments"] else ""
            ok = "" if row["verified"] else "  PROVENANCE MISMATCH"
            print(
                f"  {row['version']:4s} [{row['window'][0]}..{row['window'][1]}] "
                f"{row['model'] or '-':4s} method={row['method']} frozen_at={row['frozen_at']} "
                f"by={row['accepted_by'] or '-'}{trig}{adj}  {row['reason']}{ok}"
            )
    if args.json:
        json.dump(rows, open(args.json, "w"), indent=2, default=str)
        print(f"wrote {args.json}")
    return 0


def _cmd_mv_propose(args) -> int:
    from .mvrun import propose

    cfg, base = _mv_cfg(args)
    ctx = _drift_ctx(cfg, base)
    doc = propose(cfg, base_dir=base, as_of=args.as_of, equips=args.equip)
    if _mv_suspended(doc["skipped_state"] or "", ctx):
        return 0
    for m in doc["meters"]:
        head = f"{m['equip']}/{m['kind']}"
        if "version" not in m:
            print(f"{head}: nothing frozen yet (`camber mv freeze`)")
        else:
            rb = m["rebaseline"]
            print(
                f"{head}: live {m['version']} [{m['window'][0]}..{m['window'][1]}] -> "
                f"proposal: {rb['outcome']}"
            )
            if m.get("baseline_data_changed"):
                print("  caveat: the data under the frozen window changed since it was frozen")
            for t in rb["triggers"]:
                st = f"resolved ({t['resolved_by']})" if t["resolved"] else "UNRESOLVED"
                print(f"  {t['key']:14s} {t['outcome']:16s} {st}: {t['detail']}")
            w = rb.get("window") or {}
            if rb["outcome"] == "rebaseline":
                print(
                    f"  new window {w['window'][0]}..{w['window'][1]} ({w['model_kind']}, "
                    f"{w['missing_frac']:.0%} missing, coverage {w['coverage_tier']})"
                )
            elif rb["outcome"] == "declined":
                print(f"  declined: {rb['declined_reason']}")
            for spec in rb.get("nra_specs") or ():
                print(f"  NRA template: {json.dumps(spec)}")
        mp = m.get("method_proposal")
        if mp and "error" not in mp:
            print(
                f"  SEP method proposal: {mp['proposed'] or 'declined'} "
                f"({len(mp['sensitivity'])} valid method(s))"
            )
        elif mp:
            print(f"  SEP method proposal: {mp['error']}")
    print("\nnothing written: `camber mv rebaseline` / `camber mv adjust` apply a decision")
    if args.json:
        json.dump(doc, open(args.json, "w"), indent=2, default=str)
        print(f"wrote {args.json}")
    return 0


def _cmd_mv_report(args) -> int:
    from .config import _record_outputs
    from .mvrun import chained_report
    from .report.mv import mv_report_html

    cfg, base = _mv_cfg(args)
    ctx = _drift_ctx(cfg, base)
    rep = chained_report(cfg, base_dir=base, as_of=args.as_of, equips=args.equip)
    if _mv_suspended(rep["skipped_state"] or "", ctx):
        return 0
    html = mv_report_html(rep, charts=not args.no_charts)
    open(args.out, "w").write(html)
    outs = {args.out: "report"}
    if args.json:
        json.dump(
            {**rep, "meters": [m.as_dict() for m in rep["meters"]]},
            open(args.json, "w"),
            indent=2,
            default=str,
        )
        outs[args.json] = "report"
    _record_outputs(ctx, outs)
    for m in rep["meters"]:
        ch = m.chain
        what = (
            "no reported segment"
            if ch is None
            else (
                f"{ch.method} savings {ch.savings * (m.units or {}).get('factor', 1.0):,.0f}"
                + (f" {m.units['energy_unit']}" if m.units else "")
                if ch.savings is not None
                else f"{ch.method} declined"
            )
        )
        print(f"{m.equip}/{m.kind}: {len(m.versions)} version(s); {what}")
        for c in m.caveats:
            print(f"  caveat: {c}")
    print(f"wrote {args.out}")
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

    prep = sub.add_parser("report", help="run a config and write an HTML report")
    prep.add_argument("config")
    prep.add_argument("--out", required=True, help="output .html path")
    prep.add_argument(
        "--layout",
        help="audit (default), rcx (printable RCx layout), or a camber.reports plugin name; "
        "defaults to the config's report.layout",
    )
    prep.add_argument(
        "--week",
        help="rcx: representative week -- auto|evidence|oat-range|typical|YYYY-MM-DD",
    )
    prep.add_argument("--notes", help="rcx: engineer-notes JSON (slot -> note)")
    prep.add_argument(
        "--notes-template", dest="notes_template", help="rcx: write an empty notes file here"
    )
    prep.add_argument("--paper", choices=("letter", "a4"), help="rcx: printed page size")
    prep.add_argument(
        "--lifecycle",
        action="store_true",
        help="rcx: also show the fault store's notes on each issue page",
    )
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
    drf.add_argument("--reason", help="why (audited; required inside a portfolio workspace)")
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

    pmv = sub.add_parser(
        "mv", help="versioned M&V baselines: run, freeze, list, propose, rebaseline, adjust, report"
    )
    mvsub = pmv.add_subparsers(dest="mv_cmd", required=True)

    def _mvc(p, *, equip=True):
        p.add_argument("config", help="analysis config JSON with an 'mv' section")
        p.add_argument(
            "--store",
            help="M&V baseline store (default: state/<facility_id>/mv_baselines.json in the "
            "workspace, else the config's mv_store)",
        )
        if equip:
            p.add_argument("--equip", action="append", help="only this meter (repeatable)")

    def _mvw(p):
        p.add_argument("--reason", required=True, help="why (recorded and audited)")
        p.add_argument("--apply", action="store_true", help="write the change (default: a dry run)")
        p.add_argument("--run-id", default="", help="stamp as frozen_at (default: now, UTC)")

    mr = mvsub.add_parser("run", help="measure each meter against its frozen version (read-only)")
    _mvc(mr, equip=False)
    mr.add_argument("--out", help="output dir for mv_findings.json")
    mr.set_defaults(func=_cmd_mv_run)

    mf = mvsub.add_parser(
        "freeze", help="freeze version 1 of each meter's baseline (never overwrites one)"
    )
    _mvc(mf)
    _mvw(mf)
    mf.add_argument("--by", help="who accepts the baseline (default: the OS user)")
    mf.add_argument(
        "--allow-short",
        action="store_true",
        help="accept a window shorter than 12 months or >10%% missing (recorded as a caveat)",
    )
    mf.set_defaults(func=_cmd_mv_freeze)

    ml = mvsub.add_parser("list", help="every baseline version with its provenance")
    _mvc(ml)
    ml.add_argument("--json", help="also write the versions as JSON to this path")
    ml.set_defaults(func=_cmd_mv_list)

    mp = mvsub.add_parser(
        "propose", help="triggers T1-T6, the rebaseline proposal and the SEP method proposal"
    )
    _mvc(mp)
    mp.add_argument("--as-of", help="assess with the data up to this date")
    mp.add_argument("--json", help="write the proposal as JSON (usable by rebaseline)")
    mp.set_defaults(func=_cmd_mv_propose)

    mb = mvsub.add_parser(
        "rebaseline", help="supersede a meter's baseline (attributed; dry run unless --apply)"
    )
    _mvc(mb, equip=False)
    _mvw(mb)
    mb.add_argument("--equip", required=True, help="the meter to rebaseline")
    mb.add_argument("--by", required=True, help="who accepts the new baseline")
    mb.add_argument(
        "--period", nargs=2, metavar=("START", "END"), help="new window (default: the proposal's)"
    )
    mb.add_argument("--trigger", action="append", help="trigger id or key it answers (repeatable)")
    mb.add_argument("--from-proposal", help="a `mv propose --json` file: freeze its model exactly")
    mb.add_argument("--as-of", help="assess with the data up to this date")
    mb.set_defaults(func=_cmd_mv_rebaseline)

    ma = mvsub.add_parser(
        "adjust", help="record accepted NRA / static-factor entries on the live version"
    )
    _mvc(ma, equip=False)
    _mvw(ma)
    ma.add_argument("--equip", required=True, help="the meter to adjust")
    ma.add_argument(
        "--spec", required=True, help="JSON list of ledger entries (mv.adjustments form)"
    )
    ma.add_argument("--by", required=True, help="who records the adjustments")
    ma.add_argument("--as-of", help="reporting days up to this date (indicator estimates)")
    ma.set_defaults(func=_cmd_mv_adjust)

    mrep = mvsub.add_parser("report", help="savings chained across baseline versions (HTML)")
    _mvc(mrep)
    mrep.add_argument("--out", required=True, help="HTML file to write")
    mrep.add_argument("--json", help="also write the chain as JSON")
    mrep.add_argument("--as-of", help="report up to this date")
    mrep.add_argument("--no-charts", action="store_true", help="omit the chained CUSUM chart")
    mrep.set_defaults(func=_cmd_mv_report)

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
    # ---- 0.95 edge lifecycle (#18 step 5) ----
    ecp = edsub.add_parser(
        "compact", help="rewrite the spool journal to its pending batches (crash-safe)"
    )
    ecp.add_argument("config")
    ecp.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    ecp.add_argument(
        "--lock-timeout", type=float, default=30.0, help="seconds to wait for a busy spool"
    )
    ecp.add_argument("--json", action="store_true")
    ecp.set_defaults(func=_cmd_edge_compact)
    erc = edsub.add_parser(
        "reconcile",
        help="central: check landed objects against the facility registry (read-only)",
    )
    _edge_ws(erc)
    esrc = erc.add_mutually_exclusive_group()
    esrc.add_argument("--landing", help="a local landing directory (default: the workspace store)")
    esrc.add_argument(
        "--keys", help="a cloud key listing (s3api / gcloud / az JSON, or one key per line)"
    )
    erc.add_argument("--prefix", help="the sink's key prefix to strip from listed keys")
    erc.add_argument("--limit", type=int, default=50, help="rows to print (default 50)")
    erc.add_argument(
        "--apply", action="store_true", help="quarantine the flagged objects (needs --reason)"
    )
    erc.add_argument("--reason", help="why (audited; required with --apply)")
    erc.add_argument("--json", action="store_true")
    erc.set_defaults(func=_cmd_edge_reconcile)
    eld = edsub.add_parser(
        "land", help="central: route a landing inbox into the store or quarantine (dry run)"
    )
    _edge_ws(eld)
    eld.add_argument("inbox", help="the landing directory edge uploads arrive in")
    eld.add_argument("--apply", action="store_true", help="move the objects (needs --reason)")
    eld.add_argument("--reason", help="why (audited; required with --apply)")
    eld.add_argument("--limit", type=int, default=50, help="rows to print (default 50)")
    eld.add_argument("--json", action="store_true")
    eld.set_defaults(func=_cmd_edge_land)
    eqr = edsub.add_parser(
        "quarantine", help="central: list, release or discard quarantined uploads"
    )
    eqsub = eqr.add_subparsers(dest="q_cmd", required=True)
    eql = eqsub.add_parser("list", help="quarantined objects with their reason and status")
    _edge_ws(eql)
    eql.add_argument("--facility", help="only this facility_id")
    eql.add_argument("--json", action="store_true")
    eql.set_defaults(func=_cmd_edge_quarantine)
    for verb, text in (
        ("release", "move quarantined objects into the store (the facility must accept data)"),
        ("discard", "delete quarantined objects (destructive: --apply and --yes/--confirm ID)"),
    ):
        eqv = eqsub.add_parser(verb, help=text)
        _edge_ws(eqv)
        eqv.add_argument("--facility", help="every quarantined object of this facility_id")
        eqv.add_argument("--key", action="append", help="one quarantined key (repeatable)")
        eqv.add_argument("--apply", action="store_true", help="act (default: a dry run)")
        eqv.add_argument("--reason", help="why (audited; required with --apply)")
        if verb == "discard":
            eqv.add_argument("--yes", action="store_true", help="confirm the deletion")
            eqv.add_argument("--confirm", metavar="FACILITY_ID", help="confirm by typing the id")
        eqv.add_argument("--json", action="store_true")
        eqv.set_defaults(func=_cmd_edge_quarantine, yes=False, confirm=None)
    edc = edsub.add_parser(
        "decommission",
        help="flush the spool, wait for acks, retire the device (dry run unless --apply)",
    )
    edc.add_argument("config")
    edc.add_argument("--device", help="device id (default: config device_id, else the node name)")
    _edge_ws(edc)
    edc.add_argument(
        "--wait", type=float, default=60.0, help="seconds to keep flushing for acks (default 60)"
    )
    edc.add_argument("--apply", action="store_true", help="act (default: a dry run)")
    edc.add_argument("--yes", action="store_true", help="confirm the retirement")
    edc.add_argument("--confirm", metavar="FACILITY_ID", help="confirm by typing the facility id")
    edc.add_argument(
        "--force",
        action="store_true",
        help="retire even with unacknowledged batches (kept on disk; refused under a legal hold)",
    )
    edc.add_argument("--reason", help="why (audited; required with --apply or --force)")
    edc.add_argument("--json", action="store_true")
    edc.set_defaults(func=_cmd_edge_decommission)
    err = edsub.add_parser(
        "record-retirement", help="central: record a device's retirement receipt (retired.json)"
    )
    _edge_ws(err)
    err.add_argument("receipt", help="the retired.json a decommissioned device wrote")
    err.add_argument("--reason", required=True, help="why (audited)")
    err.set_defaults(func=_cmd_edge_record_retirement)
    ebr = edsub.add_parser(
        "bucket-rules",
        help="emit S3 / GCS / Azure lifecycle JSON from the retention policy (text only)",
    )
    ebr.add_argument("--provider", required=True, choices=["s3", "gcs", "azure"])
    _edge_ws(ebr)
    ebr.add_argument("--policy", help="a policy JSON file instead of the workspace's policy")
    ebr.add_argument(
        "--facility", action="append", help="emit per-facility rules for this id (repeatable)"
    )
    ebr.add_argument("--prefix", help="the landing's key prefix in the bucket")
    ebr.add_argument("--container", help="Azure container name (required for azure)")
    ebr.add_argument("--out", help="write the rules JSON to this file")
    ebr.add_argument("--json", action="store_true", help="print only the rules JSON")
    ebr.set_defaults(func=_cmd_edge_bucket_rules)

    pwx = sub.add_parser(
        "weather", help="weather privacy: what was sent to weather and price services"
    )
    wxsub = pwx.add_subparsers(dest="weather_cmd", required=True)
    wxa = wxsub.add_parser(
        "audit", help="the log of outbound weather / price requests (URL, cache hit, purpose)"
    )
    wxa.add_argument(
        "--workspace",
        help="portfolio workspace root (default: $CAMBER_PORTFOLIO, else the current dir)",
    )
    wxa.add_argument("--facility", help="only this facility_id (its state/<id>/ log)")
    wxa.add_argument("--since", help="only records at or after this date (YYYY-MM-DD)")
    wxa.add_argument("--cache-dir", help="read the log next to this weather cache")
    wxa.add_argument("--file", help="read this audit file")
    wxa.add_argument("--json", action="store_true")
    wxa.set_defaults(func=_cmd_weather_audit)

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
    dsg.add_argument(
        "--from-dir",
        dest="from_dir",
        metavar="DIR",
        help="take the files from DIR instead of a fetch (manual-download entries); pinned "
        "files are verified (size + sha256) before use",
    )
    dsg.add_argument(
        "--accept-noncommercial",
        dest="accept_noncommercial",
        action="store_true",
        help="acknowledge a research-only (NC/ND) licence when no fetch recorded one "
        "(recorded in acknowledgements.json)",
    )
    dsg.add_argument(
        "--no-corrections",
        dest="no_corrections",
        action="store_true",
        help="skip the catalog's fix quirks and ingest the data exactly as published "
        "(`datasets info <id>` lists what each fix corrects); recorded in the provenance",
    )
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

    def _ws(p):
        p.add_argument(
            "--workspace",
            help="portfolio workspace root (default: $CAMBER_PORTFOLIO, else the current dir)",
        )

    ppf = sub.add_parser(
        "portfolio",
        help="portfolio workspace: init, adopt, status, audit, migrate (docs/PORTFOLIO.md)",
    )
    pfsub = ppf.add_subparsers(dest="portfolio_cmd", required=True)
    pfi = pfsub.add_parser("init", help="create a workspace (store/, policy, audit log, lock)")
    pfi.add_argument("root")
    pfi.set_defaults(func=_cmd_portfolio_init)
    pfa = pfsub.add_parser("adopt", help="wrap an existing store directory as a workspace")
    pfa.add_argument("store", help="existing ParquetStore directory (nothing is moved)")
    pfa.add_argument("root", nargs="?", help="workspace root (default: the store's parent)")
    pfa.add_argument("--reason", required=True, help="why (audited)")
    pfa.set_defaults(func=_cmd_portfolio_adopt)
    pfs = pfsub.add_parser("status", help="facility counts by state, holds, lock, policy")
    _ws(pfs)
    pfs.add_argument("--json", action="store_true")
    pfs.set_defaults(func=_cmd_portfolio_status)
    pfl = pfsub.add_parser("audit", help="the append-only audit log (who, when, what, why)")
    _ws(pfl)
    pfl.add_argument("--facility", help="only this facility_id")
    pfl.add_argument("--json", action="store_true")
    pfl.set_defaults(func=_cmd_portfolio_audit)
    pfm = pfsub.add_parser(
        "migrate",
        help="re-key site-keyed fault/baseline files to facility_id (dry run unless --apply)",
    )
    _ws(pfm)
    pfm.add_argument("paths", nargs="*", help="legacy fault / baseline store files (JSON)")
    pfm.add_argument(
        "--config",
        action="append",
        default=[],
        help="a config whose drift.store / faults.store / reports belong to its facility "
        "(repeatable)",
    )
    pfm.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="SITE=FACILITY_ID",
        help="map a site label the registry cannot map on its own (repeatable)",
    )
    mode = pfm.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only (the default)")
    mode.add_argument("--apply", action="store_true", help="carry the plan out (needs --reason)")
    pfm.add_argument("--reason", help="why (audited; required with --apply)")
    pfm.add_argument("--json", action="store_true", help="print the plan/result as JSON")
    pfm.set_defaults(func=_cmd_portfolio_migrate)

    pfc = sub.add_parser(
        "facility", help="facility lifecycle: add, list, show, rename, suspend, resume, activate"
    )
    fcsub = pfc.add_subparsers(dest="facility_cmd", required=True)
    fca = fcsub.add_parser("add", help="register a new facility (provisioning unless --activate)")
    _ws(fca)
    fca.add_argument("name", help="the facility's name (becomes its display name)")
    fca.add_argument("--id", help="facility_id (default: derived from the name)")
    fca.add_argument("--owner", help="owner (free text)")
    fca.add_argument("--tag", action="append", help="portfolio tag (repeatable)")
    fca.add_argument("--activate", action="store_true", help="start active, not provisioning")
    fca.add_argument(
        "--private",
        action="store_true",
        help="mark it private: weather requests default to offline (docs/WEATHER.md)",
    )
    fca.add_argument("--reason", required=True, help="why (audited)")
    fca.set_defaults(func=_cmd_facility_add)
    fcl = fcsub.add_parser("list", help="facilities with their lifecycle state")
    _ws(fcl)
    fcl.add_argument("--state", help="only facilities in this state")
    fcl.add_argument("--json", action="store_true")
    fcl.set_defaults(func=_cmd_facility_list)
    fcs = fcsub.add_parser("show", help="one facility: record, retention, audit trail")
    _ws(fcs)
    fcs.add_argument("id")
    fcs.add_argument("--json", action="store_true")
    fcs.set_defaults(func=_cmd_facility_show)
    fcr = fcsub.add_parser("rename", help="change a facility's display name (the id never changes)")
    _ws(fcr)
    fcr.add_argument("id")
    fcr.add_argument("display_name")
    fcr.add_argument("--reason", required=True, help="why (audited)")
    fcr.set_defaults(func=_cmd_facility_rename)
    fcp = fcsub.add_parser(
        "private", help="mark a facility private (weather requests default to offline)"
    )
    _ws(fcp)
    fcp.add_argument("id")
    fcp.add_argument("--off", action="store_true", help="clear the flag")
    fcp.add_argument("--reason", required=True, help="why (audited)")
    fcp.set_defaults(func=_cmd_facility_private)
    for verb, text in (
        ("activate", "provisioning -> active"),
        ("suspend", "active -> suspended (analyses skip it)"),
        ("resume", "suspended -> active"),
        ("offboard", "(later release) start the reversible offboarding grace period"),
        ("restore", "(later release) offboarding/archived -> active"),
        ("archive", "(later release) delete hot data, keep the export bundle"),
        ("purge", "(later release) delete everything but the tombstone and audit"),
    ):
        fct = fcsub.add_parser(verb, help=text)
        _ws(fct)
        fct.add_argument("id")
        fct.add_argument("--reason", required=verb not in _LATER, help="why (audited)")
        fct.set_defaults(func=_cmd_facility_transition)
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
