"""``camber portfolio migrate``: re-key site-keyed fault and baseline state to ``facility_id``.

Before 0.87 a fault's and a drift baseline's fingerprint was ``sha1(site, equip, rule/kind)``,
with ``site`` a free-text label -- so renaming a facility orphaned its history. This module moves
such **legacy** state files into the workspace:

1. **Plan** (always; a dry run stops here). Each record's ``site`` is mapped to a facility id
   through the registry (:class:`~._state.SiteResolver`: id, ``name``, ``display_name`` and past
   display names), through an explicit ``--map "SITE=ID"``, or -- for files a config names --
   through the config's own facility. A label that matches more than one facility is
   **ambiguous** and is never guessed; a label of a removed (tombstoned) facility is refused.
   Any such problem blocks the whole migration.
2. **Apply** (under the workspace lock). Per facility: the original records are kept under
   ``state/<fid>/migrated/``; the re-keyed records (old fingerprint kept in ``aliases``) are
   merged into ``state/<fid>/faults.json`` / ``baselines.json``; the legacy file is replaced by a
   redirect stub (:mod:`camber._statefile`) so configs naming it keep working; each facility's
   manifest is rewritten with sha256 per file; and the audit log gets one ``portfolio.migrate``
   record plus one ``facility.migrate`` record per facility.

Re-running is a no-op: a stub is recognised as migrated, and a record whose old fingerprint a
target already carries is skipped. Reports a config writes are regenerable and stay where they
are; they are listed in the facility's manifest as external artifacts.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
from typing import Any

from .._statefile import read_json, redirect_of, write_json
from ..faultlifecycle import FaultRecord, _merge_faults
from ..integrate.tickets import fingerprint
from ..store.modelstore import BaselineRecord, _merge_baselines
from ._state import (
    BASELINES_FILE,
    FAULTS_FILE,
    MIGRATED_DIR,
    STATE_DIR,
    SiteResolver,
    refresh_manifest,
    sha256_file,
    state_dir,
)

_KINDS: dict[str, tuple[str, Any, str]] = {
    "faults": (FAULTS_FILE, FaultRecord, "rule"),
    "baselines": (BASELINES_FILE, BaselineRecord, "kind"),
}


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_mapping(items) -> dict:
    """``["Old Site=fid", ...]`` -> ``{"Old Site": "fid"}`` (split on the last ``=``)."""
    out = {}
    for item in items or ():
        if isinstance(item, (tuple, list)):
            site, fid = item
        else:
            if "=" not in str(item):
                raise ValueError(f"--map expects SITE=FACILITY_ID, got {item!r}")
            site, fid = str(item).rsplit("=", 1)
        out[str(site)] = str(fid).strip()
    return out


class _Source:
    def __init__(self, path: str, origin: str):
        self.path = os.path.abspath(path)
        self.origin = origin  # "path" or the config file it came from
        self.kind: str | None = None  # faults | baselines | report
        self.status = "legacy"  # legacy | migrated | missing | unrecognized | empty | report
        self.records: list = []
        self.sha256: str = ""
        self.hints: dict = {}  # site label -> set(fid) from configs naming this file
        self.assign: list = []  # [(record dict, fid or None, label)]


def _classify(src: _Source, want=None) -> None:
    if not os.path.isfile(src.path):
        src.status = "missing"
        return
    if want == "report":
        src.kind, src.status = "report", "report"
        return
    try:
        data = read_json(src.path)
    except (ValueError, OSError):
        src.status = "unrecognized"
        return
    red = redirect_of(data)
    if red is not None:
        src.kind, src.status = red.get("kind"), "migrated"
        return
    for kind in ("faults", "baselines"):
        if isinstance(data.get(kind), list) and (want in (None, kind)):
            src.kind = kind
            src.records = [r for r in data[kind] if isinstance(r, dict)]
            src.sha256 = sha256_file(src.path)
            src.status = "legacy" if src.records else "empty"
            return
    src.status = "unrecognized"


def _config_sources(pf, cfg_path: str, resolver) -> tuple:
    """(sources, problem-or-None, fid) for the state files and reports a config names."""
    cfg_path = os.path.abspath(cfg_path)
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    base = os.path.dirname(cfg_path)
    source = cfg.get("source") or {}
    site = str(cfg.get("site") or "")
    if source.get("kind") == "store" and source.get("facility_id"):
        res = resolver.check(source["facility_id"], via="config")
    elif cfg.get("facility_id"):
        res = resolver.check(cfg["facility_id"], via="config")
    else:
        res = resolver.resolve(site)
        res["via"] = f"config site ({res['via']})" if res["via"] else "config site"
    fid = res["facility_id"]

    def p(x):
        return x if os.path.isabs(x) else os.path.join(base, x)

    out = []
    drift = cfg.get("drift") or {}
    if drift.get("store"):
        out.append((_Source(p(drift["store"]), cfg_path), "baselines"))
    faults = cfg.get("faults") or {}
    if isinstance(faults, dict) and faults.get("store"):
        out.append((_Source(p(faults["store"]), cfg_path), "faults"))
    rep = cfg.get("report") or {}
    for key in ("out_text", "out_html"):
        if rep.get(key):
            out.append((_Source(p(rep[key]), cfg_path), "report"))
    for src, _want in out:
        if fid:
            for label in {site, ""}:
                src.hints.setdefault(label, set()).add(fid)
    return out, res, fid


def plan(pf, paths=(), *, configs=(), mapping=None) -> tuple:
    """Build the migration plan. Returns ``(report, internal)``; nothing is written."""
    mapping = parse_mapping(mapping) if not isinstance(mapping, dict) else dict(mapping or {})
    resolver = SiteResolver(pf)
    problems: list = []
    for site, fid in mapping.items():
        chk = resolver.check(fid, via="map")
        if chk["problem"]:
            problems.append({"what": f"--map {site}={fid}", **chk})

    by_path: dict = {}
    reports: dict = {}  # fid -> {path: "report"}
    cfg_rows = []
    for c in configs or ():
        srcs, res, fid = _config_sources(pf, c, resolver)
        cfg_rows.append({"config": os.path.abspath(c), **res})
        if res["problem"]:
            problems.append({"what": f"config {c}", **res})
        for src, want in srcs:
            if want == "report":
                if fid and os.path.isfile(src.path):
                    reports.setdefault(fid, {})[src.path] = "report"
                continue
            cur = by_path.get(src.path)
            if cur is None:
                _classify(src, want)
                by_path[src.path] = src
            else:
                for label, fids in src.hints.items():
                    cur.hints.setdefault(label, set()).update(fids)
    for pth in paths or ():
        src = by_path.get(os.path.abspath(pth)) or _Source(pth, "path")
        if src.path not in by_path:
            _classify(src)
            by_path[src.path] = src
        if src.status in ("missing", "unrecognized"):
            problems.append(
                {
                    "what": f"file {src.path}",
                    "facility_id": None,
                    "via": None,
                    "candidates": [],
                    "problem": src.status,
                }  # fmt: skip
            )

    labels: dict = {}
    for src in by_path.values():
        if src.status != "legacy":
            continue
        for rec in src.records:
            label = str(rec.get("site") or "")
            if rec.get("facility_id"):
                res = resolver.check(rec["facility_id"], via="record")
                key = f"facility_id={rec['facility_id']}"
            elif label in src.hints and label not in mapping:
                fids = sorted(src.hints[label])
                res = (
                    resolver.check(fids[0], via="config")
                    if len(fids) == 1
                    else {
                        "facility_id": None,
                        "via": "config",
                        "candidates": fids,
                        "problem": "ambiguous",
                    }  # fmt: skip
                )
                key = label
            else:
                res = resolver.resolve(label, mapping)
                key = label
            row = labels.setdefault(key, {**res, "records": 0, "files": []})
            row["records"] += 1
            if src.path not in row["files"]:
                row["files"].append(src.path)
            src.assign.append((rec, res["facility_id"], key))
    for key, row in labels.items():
        if row["problem"]:
            problems.append({"what": f"site {key!r}", **row})

    # target state per facility, merged in memory; conflicts become deterministic merges
    targets: dict = {}
    merged: list = []
    counts: dict = {}
    skipped = 0
    for src in by_path.values():
        if src.status != "legacy":
            continue
        fname, cls, sub = _KINDS[src.kind]
        for rec, fid, _key in src.assign:
            if fid is None:
                continue
            tgt = targets.get((fid, src.kind))
            if tgt is None:
                tpath = os.path.join(state_dir(pf.root, fid), fname)
                tgt = {
                    r["fingerprint"]: cls.from_dict(r) for r in read_json(tpath).get(src.kind, [])
                }
                targets[(fid, src.kind)] = tgt
            new = cls.from_dict(copy.deepcopy(rec))
            old = new.fingerprint
            new.facility_id = fid
            new.fingerprint = fingerprint(fid, new.equip, getattr(new, sub))
            if old != new.fingerprint and old not in new.aliases:
                new.aliases.append(old)
            c = counts.setdefault(fid, {"faults": 0, "baselines": 0, "merged": 0, "skipped": 0})
            cur = tgt.get(new.fingerprint)
            if cur is not None and (cur.fingerprint == old or old in cur.aliases):
                c["skipped"] += 1
                skipped += 1
                continue
            c[src.kind] += 1
            if cur is None:
                tgt[new.fingerprint] = new
                continue
            c["merged"] += 1
            if src.kind == "faults":
                tgt[new.fingerprint] = _merge_faults(cur, new)
                kept = "combined"
            else:
                live, other = _prefer(cur, new, resolver.display.get(fid))
                tgt[new.fingerprint] = _merge_baselines(live, other)
                kept = f"frozen_at {live.frozen_at} (the other is filed under history)"
            merged.append(
                {
                    "facility_id": fid,
                    "kind": src.kind,
                    "equip": new.equip,
                    sub: getattr(new, sub),
                    "kept": kept,
                }  # fmt: skip
            )

    for fid in reports:
        counts.setdefault(fid, {"faults": 0, "baselines": 0, "merged": 0, "skipped": 0})
        counts[fid]["reports"] = len(reports[fid])

    report = {
        "workspace": pf.root,
        "sources": [
            {
                "path": s.path,
                "origin": s.origin,
                "kind": s.kind,
                "status": s.status,
                "records": len(s.records),
                "facilities": sorted({f for _r, f, _k in s.assign if f}),
            }
            for s in by_path.values()
        ],
        "configs": cfg_rows,
        "labels": labels,
        "facilities": dict(sorted(counts.items())),
        "merged": merged,
        "skipped_already_migrated": skipped,
        "problems": problems,
        "blocked": bool(problems),
    }
    return report, {"sources": by_path, "targets": targets, "reports": reports}


def _prefer(a, b, display):
    """Which of two baselines for the same equipment stays the live reference.

    The one keyed under the facility's current display name (what runs were reading), else the
    later ``frozen_at``. The other is filed under history -- never discarded.
    """
    if display:
        if a.site == display and b.site != display:
            return a, b
        if b.site == display and a.site != display:
            return b, a
    return (a, b) if str(a.frozen_at) >= str(b.frozen_at) else (b, a)


def apply(pf, internal: dict, report: dict, *, reason: str) -> dict:
    """Carry out a plan (the caller holds the lock and has checked it is not blocked)."""
    from ._audit import append_audit, audit_record

    now = _utc_now()
    written: dict = {}
    migrated_from: dict = {}
    stubs = []
    targets = internal["targets"]
    for src in internal["sources"].values():
        if src.status != "legacy":
            continue
        fname = _KINDS[src.kind][0]
        per_fid: dict = {}
        for rec, fid, _k in src.assign:
            per_fid.setdefault(fid, []).append(rec)
        for fid, recs in per_fid.items():
            bdir = os.path.join(state_dir(pf.root, fid), MIGRATED_DIR)
            bpath = os.path.join(bdir, f"{src.kind}-{src.sha256[:12]}.json")
            if not os.path.isfile(bpath):
                write_json(
                    bpath,
                    {
                        "source": src.path,
                        "sha256": src.sha256,
                        "migrated_at": now,
                        "reason": reason,
                        src.kind: recs,
                    },  # fmt: skip
                )
            migrated_from.setdefault(fid, []).append(
                {
                    "path": src.path,
                    "kind": src.kind,
                    "sha256": src.sha256,
                    "records": len(recs),
                    "at": now,
                }  # fmt: skip
            )
        for fid in per_fid:
            tpath = os.path.join(state_dir(pf.root, fid), fname)
            tgt = targets[(fid, src.kind)]
            write_json(tpath, {src.kind: [r.as_dict() for r in tgt.values()]})
            written.setdefault(fid, set()).add(tpath)
        if src.path in {os.path.abspath(p) for s in written.values() for p in s}:
            continue  # the legacy file *is* a target: rewritten in place, no stub
        sroot = os.path.join(pf.root, STATE_DIR)
        rel = os.path.relpath(sroot, os.path.dirname(src.path))
        stub = {
            "camber_redirect": {
                "kind": src.kind,
                "file": fname,
                "state_root": rel,
                "state_root_abs": sroot,
                "facilities": sorted(per_fid),
                "sha256": src.sha256,
                "records": len(src.records),
                "migrated_at": now,
                "reason": reason,
                "note": "migrated by `camber portfolio migrate`; the records now live under "
                "state/<facility_id>/ in the portfolio workspace",
            }
        }
        write_json(src.path, stub)
        stubs.append(src.path)

    changed = set()
    for fid in sorted(set(report["facilities"]) | set(migrated_from)):
        before = json.dumps(_manifest_body(pf, fid), sort_keys=True)
        refresh_manifest(
            pf.root,
            fid,
            external=internal["reports"].get(fid),
            migrated_from=migrated_from.get(fid),
        )
        if migrated_from.get(fid) or json.dumps(_manifest_body(pf, fid), sort_keys=True) != before:
            changed.add(fid)

    if changed or stubs:
        append_audit(
            pf.root,
            audit_record(
                "portfolio.migrate",
                reason=reason,
                details={
                    "sources": [s["path"] for s in report["sources"] if s["status"] == "legacy"],
                    "stubs": stubs,
                    "facilities": {f: report["facilities"].get(f, {}) for f in sorted(changed)},
                    "merged": len(report["merged"]),
                },
            ),
        )
        reg = pf.registry
        for fid in sorted(changed):
            st = reg.get(fid).get("state") or "active"
            append_audit(
                pf.root,
                audit_record(
                    "facility.migrate",
                    facility_id=fid,
                    from_state=st,
                    to_state=st,
                    reason=reason,
                    details={
                        **report["facilities"].get(fid, {}),
                        "sources": [m["path"] for m in migrated_from.get(fid, [])],
                    },
                ),
            )
    out = dict(report)
    out.update(
        {
            "applied": True,
            "changed": bool(changed or stubs),
            "stubs": stubs,
            "written": {f: sorted(p) for f, p in sorted(written.items())},
        }
    )
    return out


def _manifest_body(pf, fid) -> dict:
    from ._state import read_manifest

    m = read_manifest(pf.root, fid)
    return {k: v for k, v in m.items() if k not in ("updated_at",)}
