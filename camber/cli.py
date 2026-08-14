"""Command-line entry point for CAMBER.

Subcommands:

    camber run     <config.json> [--out DIR]        # run a config, print/write findings
    camber report  <config.json> --out site.html    # run + write an HTML audit report
    camber explain <config.json> [--no-strict]      # grounded plain-language explanation of
                                                     # findings
    camber ask "<question>" --config <config.json> [--llm-cmd CMD]   # grounded Q&A over the run
    camber fleet   '<glob>' [--ask Q] [--out f.html] [--llm-cmd CMD] # portfolio rollup + triage
    camber charts  (--csv F | --demo reheat) [--ahu N] [--out DIR]   # the legacy AHU HeC charts

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
    from .config import run_config_file
    from .report.audit import AuditReport

    res = run_config_file(args.config)
    report = res.report
    if report is None:
        report = AuditReport(building=res.site, level=2)
        report.add_findings(res.findings)
    html = report.to_html(recommend=True)
    open(args.out, "w").write(html)
    print(f"wrote {args.out}  ({len(res.findings)} findings)")
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
    return ap


def main(argv=None):
    """CLI entry point: parse args and dispatch to the requested subcommand."""
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
