"""``camber interop openfdd ...`` subcommands (provisional, 0.99).

Kept outside :mod:`camber.interop` so building the CLI parser imports nothing heavy: every
handler imports :mod:`camber.interop.openfdd` only when it runs.
"""

from __future__ import annotations

import json
import os
import sys


def add_parser(sub) -> None:
    """Register ``camber interop`` (with its ``openfdd`` group) on the top-level subparsers."""
    pio = sub.add_parser("interop", help="exchange data with other tools (open-fdd)")
    iosub = pio.add_subparsers(dest="interop_cmd", required=True)
    pof = iosub.add_parser(
        "openfdd", help="read open-fdd packages / historian Parquet; findings JSON for open-fdd"
    )
    ofsub = pof.add_subparsers(dest="openfdd_cmd", required=True)

    def _read_args(p) -> None:
        p.add_argument("package", help="openfdd_package_v1 folder or .zip, or a historian root")
        p.add_argument(
            "--timezone",
            required=True,
            help="the site's IANA zone, e.g. America/Chicago (required: packages carry none)",
        )
        p.add_argument(
            "--units",
            required=True,
            choices=("ip", "si"),
            help="unit system of every column whose unit the package does not declare",
        )
        p.add_argument("--building", help="the building of a multi-building package / historian")
        p.add_argument(
            "--resample",
            default="15min",
            help="average onto this grid (default 15min); 'native' keeps the package's own",
        )
        p.add_argument(
            "--equip-types",
            dest="equip_types",
            help="historian only: JSON file {equipment_id: equipType} to classify equipment",
        )

    ing = ofsub.add_parser("ingest", help="ingest a package into a store or workspace")
    _read_args(ing)
    where = ing.add_mutually_exclusive_group(required=True)
    where.add_argument("--store", help="ParquetStore directory")
    where.add_argument("--workspace", help="portfolio workspace root (lifecycle + audit)")
    ing.add_argument("--facility-id", dest="facility_id", help="default: derived from building")
    ing.add_argument("--name", help="display name (default: 'open-fdd <building>')")
    ing.add_argument(
        "--activate",
        action="store_true",
        help="workspace: register the facility active (default: provisioning)",
    )
    ing.add_argument("--reason", help="why (recorded in the workspace audit log)")
    ing.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    ing.add_argument("--config-out", dest="config_out", help="also write a starting run config")
    ing.add_argument("--json", help="also write the ingest result (coverage, unmapped) as JSON")
    ing.set_defaults(func=_cmd_ingest)

    ins = ofsub.add_parser(
        "inspect", help="dry run: how every column would map (writes nothing to a store)"
    )
    _read_args(ins)
    ins.add_argument("--json", help="also write every column's mapping as JSON")
    ins.set_defaults(func=_cmd_inspect)

    cw = ofsub.add_parser("crosswalk", help="print the open-fdd -> CAMBER role crosswalk")
    cw.add_argument("--json", action="store_true", help="machine-readable")
    cw.set_defaults(func=_cmd_crosswalk)

    fnd = ofsub.add_parser(
        "findings",
        help="run a config and write the engine-labelled findings JSON (findings-exchange draft)",
    )
    fnd.add_argument("config", help="run config (.json, or .yaml with the [yaml] extra)")
    fnd.add_argument("--out", required=True, help="the JSON file to write ('-' for stdout)")
    fnd.set_defaults(func=_cmd_findings)


def _resample(v: str):
    return None if str(v).lower() in ("native", "none", "") else v


def _equip_types(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object {{equipment_id: equipType}}")
    return {str(k): str(v) for k, v in data.items()}


def _print_coverage(cov: dict, unmapped: dict) -> None:
    print(f"  columns: {cov['columns']}, mapped {cov['mapped']}, unmapped {cov['unmapped']}")
    for status, n in sorted(cov["by_status"].items()):
        if status != "mapped":
            print(f"    {status}: {n}")
    if unmapped:
        top = list(unmapped.items())[:12]
        print("  unmapped names (count): " + ", ".join(f"{k} ({v})" for k, v in top))
        if len(unmapped) > len(top):
            print(f"    ... and {len(unmapped) - len(top)} more (--json lists them all)")


def _cmd_ingest(args) -> int:
    from .interop.openfdd import ingest_package

    try:
        res = ingest_package(
            args.package,
            timezone=args.timezone,
            unit_system=args.units,
            store=args.store,
            workspace=args.workspace,
            facility_id=args.facility_id,
            name=args.name,
            building=args.building,
            resample=_resample(args.resample),
            activate=args.activate,
            force=args.force,
            reason=args.reason,
            equip_types=_equip_types(args.equip_types),
            config_out=args.config_out,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if res.skipped:
        print(f"{res.building_id}: up to date as {res.facility_id} in {res.store} -- skipped")
    else:
        print(
            f"{res.building_id}: ingested {res.rows:,} rows, {res.equipment} equipment into "
            f"{res.facility_id} ({res.state}) in {res.store}"
        )
    classes: dict = {}
    for cls in res.classes.values():
        classes[cls] = classes.get(cls, 0) + 1
    print("  classes: " + ", ".join(f"{k} x{v}" for k, v in sorted(classes.items())))
    _print_coverage(res.coverage, res.unmapped)
    for w in res.warnings[:20]:
        print(f"  warning: {w}")
    if len(res.warnings) > 20:
        print(f"  ... {len(res.warnings) - 20} more warnings (--json lists them all)")
    if res.state == "provisioning":
        print(
            "  note: the facility is provisioning; `camber run` skips it until "
            f"`camber facility activate {res.facility_id}` (or ingest with --activate)"
        )
    if res.config:
        print(f"wrote {res.config}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res.as_dict(), fh, indent=2, default=str)
            fh.write("\n")
        print(f"wrote {args.json}")
    return 0


def _cmd_inspect(args) -> int:
    from .interop.openfdd._historian import is_historian_root, read_historian
    from .interop.openfdd._reader import read_package

    try:
        if os.path.isdir(args.package) and is_historian_root(args.package):
            if not args.building:
                raise ValueError("a historian root needs --building")
            pkg = read_historian(
                args.package,
                building=args.building,
                timezone=args.timezone,
                unit_system=args.units,
                equip_types=_equip_types(args.equip_types),
                resample=_resample(args.resample),
            )
        else:
            pkg = read_package(
                args.package,
                timezone=args.timezone,
                unit_system=args.units,
                building=args.building,
                resample=_resample(args.resample),
            )
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{pkg.building_id} ({pkg.source_kind}): {len(pkg.equipment)} equipment")
    for eq, item in pkg.equipment.items():
        roles = ", ".join(c.camber_role for c in item.mapped) or "-"
        print(
            f"  {eq} [{item.equip_class}]: {len(item.mapped)}/{len(item.columns)} mapped: {roles}"
        )
    _print_coverage(pkg.coverage(), pkg.unmapped_names())
    for n in pkg.notes:
        print(f"  note: {n}")
    for w in pkg.warnings:
        print(f"  warning: {w}")
    if args.json:
        doc = {
            "building_id": pkg.building_id,
            "source_kind": pkg.source_kind,
            "timezone": pkg.timezone,
            "unit_system": pkg.unit_system,
            "crosswalk_version": pkg.crosswalk_version,
            "coverage": pkg.coverage(),
            "equipment": {
                eq: {
                    "class": e.equip_class,
                    "equipType": e.equip_type,
                    "columns": [c.as_dict() for c in e.columns],
                }
                for eq, e in pkg.equipment.items()
            },
            "ignored": pkg.ignored,
            "notes": pkg.notes,
            "warnings": pkg.warnings,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, default=str)
            fh.write("\n")
        print(f"wrote {args.json}")
    return 0


def _cmd_crosswalk(args) -> int:
    from .interop.openfdd._crosswalk import load_crosswalk

    cw = load_crosswalk()
    if args.json:
        doc = {
            "crosswalk_version": cw.version,
            "openfdd_docs_commit": cw.docs_commit,
            "equip_types": cw.equip_types,
            "roles": cw.table(),
        }
        print(json.dumps(doc, indent=2))
        return 0
    print(f"open-fdd -> CAMBER role crosswalk v{cw.version} (open-fdd docs @ {cw.docs_commit[:7]})")
    rows = cw.table()
    w1 = max(len(r["haystack"]) for r in rows)
    w2 = max(len(r["sql_role"]) for r in rows)
    w3 = max(len(r["camber_role"] or "-") for r in rows)
    for r in rows:
        only = f" [{', '.join(r['equip_types'])} only]" if r["equip_types"] else ""
        print(
            f"  {r['haystack']:<{w1}}  {r['sql_role']:<{w2}}  {r['camber_role'] or '-':<{w3}}  "
            f"{r['quantity']}{only}" + (f"  -- {r['note']}" if r["camber_role"] is None else "")
        )
    mapped = sum(1 for r in rows if r["camber_role"])
    print(f"{len(rows)} rows: {mapped} mapped, {len(rows) - mapped} deliberately not mapped")
    print("equipment types: " + ", ".join(f"{k}->{v}" for k, v in cw.equip_types.items()))
    return 0


def _cmd_findings(args) -> int:
    from .interop.openfdd._findings import run_findings

    doc = run_findings(args.config)
    text = json.dumps(doc, indent=2, default=str) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    c = doc["counts"]
    print(
        f"wrote {args.out}: {len(doc['findings'])} records "
        + ", ".join(f"{k} {v}" for k, v in c.items() if v)
        + f" ({doc['schema']} {doc['schema_version']}, {doc['engine']['name']} "
        f"{doc['engine']['version']})"
    )
    return 0
